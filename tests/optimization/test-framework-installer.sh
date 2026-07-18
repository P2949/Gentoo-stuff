#!/usr/bin/env bash
# shellcheck disable=SC1090,SC1091,SC2031,SC2329
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export LC_ALL=C

SOURCE_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
WORK=$(mktemp -d /tmp/gentoo-optimization-framework-installer.XXXXXXXX)
REPOSITORY=${WORK}/repository
TARGET=${WORK}/target
LOG=${WORK}/installer.log
OUTER_CASE_TIMEOUT_SECONDS=${TEST_CASE_TIMEOUT_SECONDS:-1800}
[[ ${OUTER_CASE_TIMEOUT_SECONDS} =~ ^[1-9][0-9]*$ ]] || {
    printf 'FAIL: TEST_CASE_TIMEOUT_SECONDS must be a positive integer\n' >&2
    exit 2
}
if [[ -n ${INSTALLER_MARKER_TIMEOUT_SECONDS:-} ]]; then
    [[ ${INSTALLER_MARKER_TIMEOUT_SECONDS} =~ ^[1-9][0-9]*$ ]] || {
        printf 'FAIL: INSTALLER_MARKER_TIMEOUT_SECONDS must be a positive integer\n' >&2
        exit 2
    }
else
    INSTALLER_MARKER_TIMEOUT_SECONDS=$((OUTER_CASE_TIMEOUT_SECONDS / 20))
fi
((INSTALLER_MARKER_TIMEOUT_SECONDS >= 10)) || INSTALLER_MARKER_TIMEOUT_SECONDS=10
((INSTALLER_MARKER_TIMEOUT_SECONDS <= 120)) || INSTALLER_MARKER_TIMEOUT_SECONDS=120
LOCK_BASELINE_IDENTITY=
LOCK_BASELINE_SHA256=
declare -a BACKGROUND_PIDS=()

background_group_exists() {
    kill -0 -- "-$1" 2>/dev/null
}

track_background_pid() {
    local pid=$1 pgid='' fixture_pgid
    fixture_pgid=$(ps -o pgid= -p "${BASHPID}")
    fixture_pgid=${fixture_pgid//[[:space:]]/}
    for _ in $(seq 1 100); do
        pgid=$(ps -o pgid= -p "${pid}" 2>/dev/null || true)
        pgid=${pgid//[[:space:]]/}
        [[ ${pgid} == "${pid}" ]] && break
        sleep 0.01
    done
    [[ ${pgid} == "${pid}" && ${pgid} != "${fixture_pgid}" ]] || \
        fail "background process lacks a private process group: pid=${pid} pgid=${pgid:-absent}"
    BACKGROUND_PIDS+=("${pid}")
}

untrack_background_pid() {
    local completed=$1 pid
    local -a remaining=()
    for pid in "${BACKGROUND_PIDS[@]}"; do
        [[ ${pid} == "${completed}" ]] || remaining+=("${pid}")
    done
    BACKGROUND_PIDS=("${remaining[@]}")
}

terminate_background_group() {
    local pid=$1
    kill -TERM -- "-${pid}" 2>/dev/null || true
    for _ in $(seq 1 100); do
        background_group_exists "${pid}" || return 0
        sleep 0.01
    done
    kill -KILL -- "-${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
    for _ in $(seq 1 100); do
        background_group_exists "${pid}" || return 0
        sleep 0.01
    done
    fail "background process group survived SIGKILL: ${pid}"
}

assert_background_group_gone() {
    background_group_exists "$1" && \
        fail "completed background process left a live process group: $1"
    return 0
}

cleanup_fixture() {
    local pid
    trap - EXIT INT TERM HUP
    for pid in "${BACKGROUND_PIDS[@]}"; do
        kill -TERM -- "-${pid}" 2>/dev/null || true
    done
    sleep 0.1
    for pid in "${BACKGROUND_PIDS[@]}"; do
        kill -KILL -- "-${pid}" 2>/dev/null || true
        wait "${pid}" 2>/dev/null || true
    done
    rm -rf -- "${WORK}"
}
trap cleanup_fixture EXIT INT TERM HUP

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

record_required_subtest() {
    local status=$1 name=$2 detail=$3
    [[ -n ${GENTOO_OPT_SUBTEST_RESULTS:-} ]] || return 0
    printf '%s\trequired\t%s\t%s\n' "${status}" "${name}" "${detail}" \
        >>"${GENTOO_OPT_SUBTEST_RESULTS}"
}

probe_fixture_atomic_exchange() {
    local probe=${WORK}/atomic-exchange-probe reason
    mkdir -- "${probe}" "${probe}/left" "${probe}/right"
    printf 'left\n' >"${probe}/left/identity"
    printf 'right\n' >"${probe}/right/identity"
    if ! /usr/bin/mv --exchange --no-copy -T -- \
        "${probe}/left" "${probe}/right" 2>"${probe}/error"; then
        reason=$(tr '\n' ' ' <"${probe}/error" 2>/dev/null || true)
        rm -rf -- "${probe}"
        printf 'SKIP: fixture filesystem lacks required atomic exchange%s\n' \
            "${reason:+ (${reason})}"
        exit 77
    fi
    [[ $(<"${probe}/left/identity") == right && \
        $(<"${probe}/right/identity") == left ]] || \
        fail 'fixture filesystem returned invalid atomic-exchange semantics'
    rm -rf -- "${probe}"
}

probe_fixture_atomic_exchange

run_installer() {
    GENTOO_OPT_INSTALLER_TEST_MODE=1 \
        bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
        --test-root "${TARGET}" "$@"
}

expect_failure() {
    local expected=$1
    shift
    if "$@" >"${LOG}" 2>&1; then
        fail "command unexpectedly succeeded: ${expected}"
    fi
    grep -Fq -- "${expected}" "${LOG}" || {
        sed -n '1,240p' "${LOG}" >&2
        fail "failure omitted expected diagnostic: ${expected}"
    }
}

wait_for_log() {
    local needle=$1 installer_pid=$2 deadline child_status
    [[ ${installer_pid} =~ ^[1-9][0-9]*$ ]] || fail 'invalid installer PID'
    deadline=$((SECONDS + INSTALLER_MARKER_TIMEOUT_SECONDS))
    while ((SECONDS < deadline)); do
        grep -Fq -- "${needle}" "${LOG}" 2>/dev/null && return 0
        if ! jobs -pr | grep -Fxq -- "${installer_pid}"; then
            set +e
            wait "${installer_pid}"
            child_status=$?
            set -e
            sed -n '1,240p' "${LOG}" >&2 || true
            fail "installer exited with status ${child_status} before marker: ${needle}"
        fi
        sleep 0.1
    done
    sed -n '1,240p' "${LOG}" >&2 || true
    fail "timed out after ${INSTALLER_MARKER_TIMEOUT_SECONDS}s waiting for installer marker: ${needle}"
}

wait_for_installer_lock_release() {
    local lock=${TARGET}/run/gentoo-optimization/framework-install.lock
    local directory=${lock%/*} identity payload opened_identity
    [[ -d ${directory} && ! -L ${directory} && \
        $(stat -c '%u:%g:%a' -- "${directory}") == \
        "$(id -u):$(id -g):700" ]] || \
        fail 'installer publication lock directory is not trusted'
    [[ -f ${lock} && ! -L ${lock} ]] || \
        fail 'installer publication lock is absent or not a trusted regular file'
    identity=$(stat -c '%d:%i:%u:%g:%a:%h:%s' -- "${lock}")
    [[ ${identity} == *":$(id -u):$(id -g):600:1:0" ]] || \
        fail "installer publication lock metadata is unsafe: ${identity}"
    payload=$(sha256sum -- "${lock}"); payload=${payload%% *}
    if [[ -z ${LOCK_BASELINE_IDENTITY} ]]; then
        LOCK_BASELINE_IDENTITY=${identity}
        LOCK_BASELINE_SHA256=${payload}
    elif [[ ${identity} != "${LOCK_BASELINE_IDENTITY}" || \
        ${payload} != "${LOCK_BASELINE_SHA256}" ]]; then
        fail 'installer publication lock inode, metadata, or payload changed'
    fi
    for _ in $(seq 1 100); do
        if (
            exec 9<"${lock}" || exit 2
            opened_identity=$(stat -Lc '%d:%i:%u:%g:%a:%h:%s' -- \
                "/proc/${BASHPID}/fd/9") || exit 2
            [[ ${opened_identity} == "${LOCK_BASELINE_IDENTITY}" ]] || exit 2
            flock -n 9
        ); then
            return 0
        fi
        sleep 0.05
    done
    fail 'SIGKILLed installer did not release the publication lock'
}

# A child that exits before a pause marker must report its real status/log
# immediately instead of consuming the complete marker deadline.
EARLY_EXIT_LOG=${WORK}/early-installer-exit.log
EARLY_EXIT_DIAGNOSTIC=${WORK}/early-installer-diagnostic.log
SAVED_INSTALLER_LOG=${LOG}
LOG=${EARLY_EXIT_LOG}
if (
    bash -c 'printf "fixture child failed before marker\\n" >&2; exit 42' \
        >"${LOG}" 2>&1 &
    early_pid=$!
    wait_for_log 'marker-that-will-never-appear' "${early_pid}"
) >"${EARLY_EXIT_DIAGNOSTIC}" 2>&1; then
    fail 'early installer child failure unexpectedly reached its marker'
fi
LOG=${SAVED_INSTALLER_LOG}
grep -Fq 'installer exited with status 42 before marker:' \
    "${EARLY_EXIT_DIAGNOSTIC}" || {
    sed -n '1,160p' "${EARLY_EXIT_DIAGNOSTIC}" >&2 || true
    fail 'early installer child failure lost its exact status'
}
grep -Fq 'fixture child failed before marker' "${EARLY_EXIT_DIAGNOSTIC}" || {
    sed -n '1,160p' "${EARLY_EXIT_DIAGNOSTIC}" >&2 || true
    fail 'early installer child failure lost its underlying log'
}

# The fixture's own cleanup primitive must cover descendants, including a
# TERM-resistant child, rather than merely reaping a leader PID.
GROUP_CHILD_PID_FILE=${WORK}/process-group-child.pid
# shellcheck disable=SC2016  # Positional parameters expand in the child shell.
/usr/bin/setsid --wait bash -Eeuo pipefail -c '
    trap "" TERM
    sleep 300 &
    printf "%s\n" "$!" >"$1"
    wait
' bash "${GROUP_CHILD_PID_FILE}" &
GROUP_TEST_PID=$!
track_background_pid "${GROUP_TEST_PID}"
for _ in $(seq 1 100); do
    [[ -s ${GROUP_CHILD_PID_FILE} ]] && break
    sleep 0.01
done
[[ -s ${GROUP_CHILD_PID_FILE} ]] || fail 'process-group cleanup child did not start'
GROUP_CHILD_PID=$(<"${GROUP_CHILD_PID_FILE}")
[[ ${GROUP_CHILD_PID} =~ ^[1-9][0-9]*$ && -e /proc/${GROUP_CHILD_PID} ]] || \
    fail 'process-group cleanup child identity is invalid'
terminate_background_group "${GROUP_TEST_PID}"
assert_background_group_gone "${GROUP_TEST_PID}"
[[ ! -e /proc/${GROUP_CHILD_PID} ]] || \
    fail 'process-group cleanup left its TERM-resistant descendant alive'
untrack_background_pid "${GROUP_TEST_PID}"

find_transaction_debris() {
    local parent pattern found
    local -a checks=(
        "${TARGET}/usr/local/libexec|gentoo-optimization.partial.*"
        "${TARGET}/usr/local/share|gentoo-optimization.partial.*"
        "${TARGET}/etc|portage.partial.*"
        "${TARGET}/usr/local/lib/install-qa-check.d|zz-gentoo-optimization-bolt.partial.*"
        "${BASE}/state/project|phase-2-framework-install.manifest.partial.*"
        "${BASE}|framework-current.partial.*"
        "${BASE}|framework-*.partial.*"
        "${BASE}|.framework-expected-manifest.*"
        "${BASE}|.framework-source-snapshot.*"
        "${BASE}|.source-git-contract.*"
        "${BASE}|.extended-metadata-audit.*"
        "${BASE}|.framework-rollback.*"
        "${BASE}|.helper-bootstrap-check.*"
        "${BASE}|.qa-bootstrap-check.*"
        "${BASE}|.qa-bootstrap-compatibility.*"
        "${BASE}|.portage-migration-guard-check.*"
        "${BASE}|.framework-activation-expected.*"
        "${BASE}|framework-activation.pending.partial.*"
        "${BASE}|.candidate-inventory-check.*"
        "${BASE}|.framework-check-manifest.*"
    )
    TRANSACTION_DEBRIS=
    for found in "${checks[@]}"; do
        parent=${found%%|*}
        pattern=${found#*|}
        [[ -d ${parent} && ! -L ${parent} ]] || continue
        if IFS= read -r -d '' TRANSACTION_DEBRIS < <(
            find "${parent}" -mindepth 1 -maxdepth 1 -name "${pattern}" -print0 -quit
        ); then
            return 0
        fi
    done
    return 1
}

assert_no_transaction_debris() {
    if find_transaction_debris; then
        fail "installer left transaction debris: ${TRANSACTION_DEBRIS}"
    fi
}

assert_transaction_debris_present() {
    find_transaction_debris || fail 'fixture did not create fixed-parent transaction debris'
}

capture_helper_tree_manifest() {
    local root=$1 output=$2 entry relative object_type mode uid gid digest target root_row
    [[ -d ${root} && ! -L ${root} ]] || \
        fail "helper-tree manifest root is not a real directory: ${root}"
    printf 'object_type\trelative_path\tmode\tuid\tgid\tsha256\tsymlink_target\n' \
        >"${output}"
    while IFS= read -r -d '' entry; do
        if [[ ${entry} == "${root}" ]]; then
            relative=.
        else
            relative=${entry#"${root}"/}
            [[ ${relative} != "${entry}" && -n ${relative} ]] || \
                fail "helper-tree manifest entry escaped its root: ${entry}"
        fi
        mode=$(stat -c %a -- "${entry}")
        uid=$(stat -c %u -- "${entry}")
        gid=$(stat -c %g -- "${entry}")
        digest=-
        target=-
        if [[ -L ${entry} ]]; then
            object_type=symlink
            target=$(readlink -- "${entry}")
        elif [[ -f ${entry} ]]; then
            object_type=regular
            digest=$(sha256sum -- "${entry}")
            digest=${digest%% *}
        elif [[ -d ${entry} ]]; then
            object_type=directory
        else
            fail "helper-tree manifest found an unsupported object: ${entry}"
        fi
        [[ ${relative} != *$'\t'* && ${relative} != *$'\n'* && \
            ${target} != *$'\t'* && ${target} != *$'\n'* ]] || \
            fail 'helper-tree manifest cannot encode a control character'
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "${object_type}" "${relative}" "${mode}" "${uid}" "${gid}" \
            "${digest}" "${target}" >>"${output}"
    done < <(find "${root}" -mindepth 0 -print0 | sort -z)
    root_row=$(printf 'directory\t.\t755\t%s\t%s\t-\t-' "$(id -u)" "$(id -g)")
    [[ $(grep -Fxc -- "${root_row}" "${output}") == 1 ]] || \
        fail 'helper-tree manifest omits the exact 0755 owned root row'
}

mkdir -p -- "${REPOSITORY}/scripts/optimization" "${TARGET}"
mkdir -p -- "${REPOSITORY}/tests/optimization"
cp -a -- "${SOURCE_ROOT}/portage" "${REPOSITORY}/portage"
cp -a -- "${SOURCE_ROOT}/local-overlay" "${REPOSITORY}/local-overlay"
cp -a -- "${SOURCE_ROOT}/scripts/optimization/bolt" \
    "${REPOSITORY}/scripts/optimization/bolt"
cp -a -- "${SOURCE_ROOT}/scripts/optimization/pgo" \
    "${REPOSITORY}/scripts/optimization/pgo"
cp -a -- "${SOURCE_ROOT}/scripts/optimization/lib" \
    "${REPOSITORY}/scripts/optimization/lib"
cp -a -- "${SOURCE_ROOT}/scripts/optimization/verify" \
    "${REPOSITORY}/scripts/optimization/verify"
cp -a -- "${SOURCE_ROOT}/scripts/optimization/recovery" \
    "${REPOSITORY}/scripts/optimization/recovery"
mkdir -p -- "${REPOSITORY}/optimization"
cp -a -- "${SOURCE_ROOT}/optimization/schema" "${REPOSITORY}/optimization/schema"
install -m 0755 -T -- "${SOURCE_ROOT}/scripts/optimization/install-framework.sh" \
    "${REPOSITORY}/scripts/optimization/install-framework.sh"
install -m 0755 -T -- \
    "${SOURCE_ROOT}/scripts/optimization/pgo/production-profile-lock-transaction.py" \
    "${REPOSITORY}/scripts/optimization/pgo/production-profile-lock-transaction.py"
install -m 0755 -T -- \
    "${SOURCE_ROOT}/scripts/optimization/pgo/authorization-token-scan.py" \
    "${REPOSITORY}/scripts/optimization/pgo/authorization-token-scan.py"

# Git cannot represent empty directories.  The production installer rejects
# them in behavior-affecting input trees, so normalize any harmless checkout
# residue before constructing the fixture's exact Git snapshot.
find "${REPOSITORY}/portage" "${REPOSITORY}/local-overlay" -depth -type d \
    -empty -delete
find "${REPOSITORY}/portage" "${REPOSITORY}/local-overlay" -type d \
    -exec chmod 0755 -- {} +

# Exercise NUL-delimited discovery with names that are awkward but safe in a
# line-oriented canonical manifest.  Control characters are deliberately
# rejected by the installer.
printf 'unusual-name-fixture\n' >"${REPOSITORY}/local-overlay/metadata/a file with spaces"
printf 'leading-dash-fixture\n' >"${REPOSITORY}/local-overlay/metadata/--leading-dash"
printf 'legitimate similarly named input\n' \
    >"${REPOSITORY}/local-overlay/metadata/.source-git-contract.fixture-input"
printf 'legitimate similarly named input\n' \
    >"${REPOSITORY}/local-overlay/metadata/.extended-metadata-audit.fixture-input"

git -C "${REPOSITORY}" init -q
git -C "${REPOSITORY}" config user.name 'Framework Installer Fixture'
git -C "${REPOSITORY}" config user.email framework-installer@example.invalid
git -C "${REPOSITORY}" add --all
git -C "${REPOSITORY}" commit -qm 'fixture baseline'

PROFILE=${TARGET}/var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm
mkdir -p -- "${PROFILE}"
printf '%s\n' '..' '../../../../../features/llvm' >"${PROFILE}/parent"
mkdir -p -- "${TARGET}/usr/bin"
cp -- "$(command -v jq)" "${TARGET}/usr/bin/jq"
chmod 0755 -- "${TARGET}/usr/bin/jq"
mkdir -p -- "${TARGET}/var/tmp"
chmod 1777 -- "${TARGET}/var/tmp"

# Atomic exchange is a declared publication prerequisite and must fail before
# the installer creates locks, framework state, or any durable destination.
expect_failure \
    "destination filesystem does not support atomic exchange: ${TARGET}/etc" \
    env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_FORCE_EXCHANGE_UNSUPPORTED=1 \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}"
[[ ! -e ${TARGET}/run/gentoo-optimization && \
    ! -e ${TARGET}/var/lib/gentoo-optimization ]] || \
    fail 'atomic-exchange rejection occurred after durable installer mutation'
[[ -z $(find "${TARGET}" -name '.gentoo-optimization-rename-exchange.*' \
    -print -quit) ]] || fail 'atomic-exchange rejection left probe debris'

# The canonical root-owned 01777 /var/tmp boundary is required and accepted;
# removing its sticky bit must make the same ancestor fail closed.
chmod 0777 -- "${TARGET}/var/tmp"
expect_failure "trusted ancestor is group/world-writable: ${TARGET}/var/tmp" \
    run_installer
[[ ! -e ${TARGET}/run/gentoo-optimization && \
    ! -e ${TARGET}/var/lib/gentoo-optimization ]] || \
    fail 'unsafe /var/tmp rejection occurred after durable installer mutation'
chmod 1777 -- "${TARGET}/var/tmp"

# Unsafe destination ancestors must fail before the lock or framework base is
# created.  The fixture root itself is the hermetic trust boundary.
UNSAFE_ROOT=${WORK}/unsafe-root-itself
mkdir -p -- "${UNSAFE_ROOT}"
chmod 0777 -- "${UNSAFE_ROOT}"
expect_failure 'test trust root is group/world-writable' \
    env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${UNSAFE_ROOT}"
[[ ! -e ${UNSAFE_ROOT}/run && ! -e ${UNSAFE_ROOT}/var ]] || \
    fail 'unsafe filesystem-root rejection occurred after mutation'
chmod 0700 -- "${UNSAFE_ROOT}"

UNSAFE=${WORK}/unsafe-target
mkdir -p -- "${UNSAFE}/usr"
chmod 0777 -- "${UNSAFE}/usr"
mkdir -p -- "${UNSAFE}/var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm"
printf '%s\n' '..' '../../../../../features/llvm' \
    >"${UNSAFE}/var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm/parent"
mkdir -p -- "${UNSAFE}/usr/bin"
cp -- "$(command -v jq)" "${UNSAFE}/usr/bin/jq"
chmod 0755 -- "${UNSAFE}/usr/bin/jq"
expect_failure 'trusted ancestor is group/world-writable' \
    env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${UNSAFE}"
[[ ! -e ${UNSAFE}/run/gentoo-optimization ]] || \
    fail 'unsafe-ancestor rejection occurred after lock mutation'
[[ ! -e ${UNSAFE}/var/lib/gentoo-optimization ]] || \
    fail 'unsafe-ancestor rejection occurred after framework mutation'

# A migration source outside the explicit checkout/managed allowlist is fatal.
mkdir -p -- "${TARGET}/etc"
ln -s -- /unmanaged/portage "${TARGET}/etc/portage"
expect_failure '/etc/portage points to an unmanaged migration source' run_installer
rm -f -- "${TARGET}/etc/portage"

# Existing lock paths are validated before opening or metadata repair.  A
# symlink must not truncate its referent, and a hardlinked project lock must
# not be chowned, chmodded, or accepted as a stable transaction inode.
LOCK_DIRECTORY=${TARGET}/run/gentoo-optimization
FRAMEWORK_LOCK=${LOCK_DIRECTORY}/framework-install.lock
PROJECT_LOCK=${LOCK_DIRECTORY}/project.lock
mkdir -p -- "${LOCK_DIRECTORY}"
chmod 0700 -- "${LOCK_DIRECTORY}"
for preflight_lock in framework-install project generation; do
    preflight_path=${LOCK_DIRECTORY}/${preflight_lock}.lock
    if [[ -e ${preflight_path} || -L ${preflight_path} ]]; then
        [[ -f ${preflight_path} && ! -L ${preflight_path} && \
            ! -s ${preflight_path} && \
            $(stat -c '%u:%g:%a:%h' -- "${preflight_path}") == \
            "$(id -u):$(id -g):600:1" ]] || \
            fail "pre-baseline installer left an unsafe lock: ${preflight_path}"
        rm -f -- "${preflight_path}"
    fi
done
LOCK_SYMLINK_VICTIM=${WORK}/framework-lock-symlink-victim
printf 'framework-lock-sentinel\n' >"${LOCK_SYMLINK_VICTIM}"
chmod 0644 -- "${LOCK_SYMLINK_VICTIM}"
ln -s -- "${LOCK_SYMLINK_VICTIM}" "${FRAMEWORK_LOCK}"
expect_failure 'expected a regular non-symlink lock file:' run_installer
[[ $(<"${LOCK_SYMLINK_VICTIM}") == framework-lock-sentinel && \
    $(stat -c '%a:%h' -- "${LOCK_SYMLINK_VICTIM}") == 644:1 ]] || \
    fail 'installer mutated a framework-lock symlink referent before validation'
rm -f -- "${FRAMEWORK_LOCK}"

: >"${FRAMEWORK_LOCK}"
chmod 0600 -- "${FRAMEWORK_LOCK}"
LOCK_HARDLINK_VICTIM=${WORK}/project-lock-hardlink-victim
printf 'project-lock-sentinel\n' >"${LOCK_HARDLINK_VICTIM}"
chmod 0600 -- "${LOCK_HARDLINK_VICTIM}"
ln -- "${LOCK_HARDLINK_VICTIM}" "${PROJECT_LOCK}"
expect_failure 'lock ownership/mode/link-count differs from' run_installer
[[ $(<"${LOCK_HARDLINK_VICTIM}") == project-lock-sentinel && \
    $(stat -c '%a:%h' -- "${LOCK_HARDLINK_VICTIM}") == 600:2 ]] || \
    fail 'installer mutated an unsafe hardlinked project lock before validation'
rm -f -- "${PROJECT_LOCK}" "${LOCK_HARDLINK_VICTIM}"

BASE=${TARGET}/var/lib/gentoo-optimization
CURRENT=${BASE}/framework-current
MANIFEST=${BASE}/state/project/phase-2-framework-install.manifest
ACTIVATION_JOURNAL=${BASE}/framework-activation.pending

# Repository-local Git configuration is controlled by the checkout owner.  The
# installer must override executable fsmonitor hooks, and production executes
# all Git inspection as that non-root owner rather than as root.
GIT_HELPER_MARKER=${WORK}/git-helper-executed
GIT_HELPER=${WORK}/malicious-fsmonitor
printf '#!/bin/sh\n: >%s\nexit 0\n' "${GIT_HELPER_MARKER}" >"${GIT_HELPER}"
chmod 0755 -- "${GIT_HELPER}"
git -C "${REPOSITORY}" config core.fsmonitor "${GIT_HELPER}"
# Model the real first migration from the reviewed but user-owned checkout.
ln -s -- "${REPOSITORY}/portage" "${TARGET}/etc/portage"
PAUSE_FILE=${WORK}/kill-after-first-guard
: >"${PAUSE_FILE}"
: >"${LOG}"
/usr/bin/setsid --wait env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_PAUSE_AT=after-first-migration-guard \
    GENTOO_OPT_INSTALLER_PAUSE_FILE="${PAUSE_FILE}" \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" >"${LOG}" 2>&1 &
INSTALL_PID=$!
track_background_pid "${INSTALL_PID}"
wait_for_log 'PAUSE: after-first-migration-guard' "${INSTALL_PID}"
kill -KILL "${INSTALL_PID}"
if wait "${INSTALL_PID}" 2>/dev/null; then fail 'first-guard SIGKILL unexpectedly succeeded'; fi
terminate_background_group "${INSTALL_PID}"
untrack_background_pid "${INSTALL_PID}"
wait_for_installer_lock_release
rm -f -- "${PAUSE_FILE}"
[[ ! -e ${CURRENT} && ! -L ${CURRENT} ]] || \
    fail 'first-guard SIGKILL exposed a framework generation prematurely'
[[ ! -e ${ACTIVATION_JOURNAL} && ! -L ${ACTIVATION_JOURNAL} ]] || \
    fail 'first-guard SIGKILL published the journal out of order'
[[ -d ${TARGET}/etc/portage && ! -L ${TARGET}/etc/portage ]] || \
    fail 'first-guard SIGKILL did not preserve the root-owned Portage guard'
# Dynamic guard and mock Portage die.
# shellcheck disable=SC1090,SC1091,SC2329
if (die() { return 96; }; source "${TARGET}/etc/portage/bashrc") >"${LOG}" 2>&1; then
    fail 'root-owned first-migration guard allowed a build shell to continue'
fi
grep -Fq 'framework activation is incomplete' "${LOG}" || \
    fail 'root-owned first-migration guard omitted its diagnostic'

PAUSE_FILE=${WORK}/kill-first-activation
: >"${PAUSE_FILE}"
: >"${LOG}"
/usr/bin/setsid --wait env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_PAUSE_AT=after-bootstrap-activation \
    GENTOO_OPT_INSTALLER_PAUSE_FILE="${PAUSE_FILE}" \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" >"${LOG}" 2>&1 &
INSTALL_PID=$!
track_background_pid "${INSTALL_PID}"
wait_for_log 'PAUSE: after-bootstrap-activation' "${INSTALL_PID}"
kill -KILL "${INSTALL_PID}"
if wait "${INSTALL_PID}" 2>/dev/null; then fail 'first-activation SIGKILL unexpectedly succeeded'; fi
terminate_background_group "${INSTALL_PID}"
untrack_background_pid "${INSTALL_PID}"
wait_for_installer_lock_release
rm -f -- "${PAUSE_FILE}"
[[ -f ${ACTIVATION_JOURNAL} && ! -L ${ACTIVATION_JOURNAL} && \
    $(stat -c %a -- "${ACTIVATION_JOURNAL}") == 600 ]] || \
    fail 'first-activation SIGKILL did not preserve the durable trusted journal'
[[ -L ${CURRENT} && -d $(readlink -- "${CURRENT}") ]] || \
    fail 'first-activation SIGKILL did not reach the current-link crash point'
[[ -d ${TARGET}/etc/portage && ! -L ${TARGET}/etc/portage ]] || \
    fail 'first-activation SIGKILL did not leave the root-owned Portage guard active'
# Dynamic guard and mock Portage die.
# shellcheck disable=SC1090,SC1091,SC2329
if (die() { return 97; }; source "${TARGET}/etc/portage/bashrc") >"${LOG}" 2>&1; then
    fail 'first-activation Portage guard allowed a build shell to continue'
fi
grep -Fq 'framework activation is incomplete' "${LOG}" || \
    fail 'first-activation Portage guard omitted its fail-closed diagnostic'
run_installer >"${LOG}" 2>&1 || { sed -n '1,260p' "${LOG}" >&2; fail 'initial crash recovery failed'; }
[[ ! -e ${ACTIVATION_JOURNAL} && ! -L ${ACTIVATION_JOURNAL} ]] || \
    fail 'initial crash recovery left the activation journal pending'
grep -Fq 'PASS: root-owned Phase 2 framework check verified' "${LOG}" || \
    fail 'initial crash recovery lacks strict-check PASS evidence'
run_installer --check >"${LOG}" 2>&1 || { sed -n '1,260p' "${LOG}" >&2; fail 'strict check failed'; }
[[ ! -e ${GIT_HELPER_MARKER} ]] || fail 'installer executed repository-local Git helper'
git -C "${REPOSITORY}" config --unset core.fsmonitor

# A pending production profile-lock transaction is accepted only when the real
# coordinator has armed the exact installer locks, published its durable child
# sidecar and seven-row authorization, and supplied the one-time token to the
# supervised child.  No hand-forged reduced journal is sufficient evidence.
PROFILE_TRANSACTION_JOURNAL=${BASE}/state/profile-transactions/phase-2-production-profile-locks.pending
PROFILE_TRANSACTION_PARTIAL=${PROFILE_TRANSACTION_JOURNAL}.partial
PROFILE_TRANSACTION_CHILD=${PROFILE_TRANSACTION_JOURNAL}.child.json
PROFILE_TRANSACTION_GENERATION=fixture-production-profile-gate
PROFILE_TRANSACTION_INVENTORY=fixture-production-profile-inventory
PROFILE_TRANSACTION_INVENTORY_SHA=$(printf fixture-profile-inventory | sha256sum)
PROFILE_TRANSACTION_INVENTORY_SHA=${PROFILE_TRANSACTION_INVENTORY_SHA%% *}
PROFILE_TRANSACTION_RUN=fixture-run
PROFILE_TRANSACTION_FIXTURE=${TARGET}/profile-transaction-fixture
PROFILE_TRANSACTION_ARTIFACTS=${TARGET}/artifacts
PROFILE_TRANSACTION_PROFILES=${TARGET}/profile-artifacts
PROFILE_TRANSACTION_EVIDENCE=${TARGET}/evidence-output
PROFILE_TRANSACTION_SCANNER=${PROFILE_TRANSACTION_FIXTURE}/authorization-token-scan.py
PROFILE_TRANSACTION_CHILD_COMMAND=${PROFILE_TRANSACTION_FIXTURE}/installer-check-child.sh
PROFILE_TRANSACTION_AUTHORIZATION=${TARGET}/generation-state/${PROFILE_TRANSACTION_GENERATION}/phase2-sample-gate-${PROFILE_TRANSACTION_RUN}/transaction.authorization
PROFILE_TRANSACTION_SCAN=${PROFILE_TRANSACTION_AUTHORIZATION%/*}/coordinator-token-scan.tsv
PROFILE_TRANSACTION_RECEIPT=${PROFILE_TRANSACTION_JOURNAL%/*}/phase-2-production-profile-locks-${PROFILE_TRANSACTION_GENERATION}.receipt.json
mkdir -m 0700 -- "${PROFILE_TRANSACTION_FIXTURE}" \
    "${PROFILE_TRANSACTION_ARTIFACTS}" "${PROFILE_TRANSACTION_PROFILES}" \
    "${PROFILE_TRANSACTION_EVIDENCE}" "${TARGET}/generation-state"
install -m 0700 -T -- \
    "${REPOSITORY}/scripts/optimization/pgo/authorization-token-scan.py" \
    "${PROFILE_TRANSACTION_SCANNER}"
cat >"${PROFILE_TRANSACTION_CHILD_COMMAND}" <<EOF
#!/bin/bash
set -euo pipefail
token=\${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN}
authorization=\${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION}
unset GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN \
    GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION
run_check() {
    local supplied_token=\$1 supplied_authorization=\$2
    GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN=\${supplied_token} \
    GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION=\${supplied_authorization} \
    bash -- ${REPOSITORY@Q}/scripts/optimization/install-framework.sh \
        --test-root ${TARGET@Q} --check
}
if run_check "\$(printf 'b%.0s' {1..64})" "\${authorization}" >/dev/null 2>&1; then
    exit 41
fi
if run_check "\${token}" ${WORK@Q}/outside.authorization >/dev/null 2>&1; then
    exit 42
fi
run_check "\${token}" "\${authorization}" >${PROFILE_TRANSACTION_ARTIFACTS@Q}/installer-check.log 2>&1
printf 'authorized\n' >${PROFILE_TRANSACTION_ARTIFACTS@Q}/installer-check.marker
EOF
chmod 0700 -- "${PROFILE_TRANSACTION_CHILD_COMMAND}"

/usr/bin/env -i HOME="${HOME}" USER="${USER:-fixture}" LOGNAME="${LOGNAME:-fixture}" \
    SHELL=/bin/bash PATH=/usr/bin:/bin LANG=C LC_ALL=C TZ=UTC \
    PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I -B \
    "${REPOSITORY}/scripts/optimization/pgo/production-profile-lock-transaction.py" run \
    --test-mode --test-root "${TARGET}" \
    --test-framework-lock "${TARGET}/run/gentoo-optimization/framework-install.lock" \
    --test-project-lock "${TARGET}/run/gentoo-optimization/project.lock" \
    --test-generation-lock "${TARGET}/run/gentoo-optimization/generation.lock" \
    --test-journal "${PROFILE_TRANSACTION_JOURNAL}" --lock-timeout-seconds 5 \
    --generation-id "${PROFILE_TRANSACTION_GENERATION}" \
    --inventory-id "${PROFILE_TRANSACTION_INVENTORY}" \
    --inventory-sha256 "${PROFILE_TRANSACTION_INVENTORY_SHA}" \
    --gate-run-id "${PROFILE_TRANSACTION_RUN}" --child-timeout-seconds 120 \
    --kill-after-seconds 2 --token-scanner "${PROFILE_TRANSACTION_SCANNER}" \
    --token-scan-root "${PROFILE_TRANSACTION_ARTIFACTS}" \
    --token-scan-root "${PROFILE_TRANSACTION_PROFILES}" \
    --token-scan-root "${PROFILE_TRANSACTION_AUTHORIZATION%/*}" \
    --token-scan-root "${PROFILE_TRANSACTION_EVIDENCE}" \
    --token-scan-output "${PROFILE_TRANSACTION_SCAN}" \
    --evidence-output-root "${PROFILE_TRANSACTION_EVIDENCE}" -- \
    "${PROFILE_TRANSACTION_CHILD_COMMAND}" >"${LOG}" 2>&1 || {
        sed -n '1,260p' "${LOG}" >&2
        fail 'coordinator-supervised installer check failed'
    }
[[ $(<"${PROFILE_TRANSACTION_ARTIFACTS}/installer-check.marker") == authorized && \
    -f ${PROFILE_TRANSACTION_RECEIPT} && ! -e ${PROFILE_TRANSACTION_JOURNAL} && \
    ! -e ${PROFILE_TRANSACTION_CHILD} ]] || \
    fail 'coordinator did not publish a complete installer transaction result'
jq -e '.status == "passed" and .child_exit_status == 0 and .token_scan.scanner_status == 0' \
    "${PROFILE_TRANSACTION_RECEIPT}" >/dev/null || \
    fail 'installer transaction receipt is not a strict pass'

PROFILE_TRANSACTION_TOKEN=$(printf 'a%.0s' {1..64})
GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN=${PROFILE_TRANSACTION_TOKEN} \
GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION=${PROFILE_TRANSACTION_AUTHORIZATION} \
    expect_failure 'stale production profile transaction authorization is present without a journal' \
    run_installer --check
: >"${PROFILE_TRANSACTION_PARTIAL}"
chmod 0600 -- "${PROFILE_TRANSACTION_PARTIAL}"
expect_failure 'production profile-lock transaction publication is incomplete' \
    run_installer --check
rm -f -- "${PROFILE_TRANSACTION_PARTIAL}"
: >"${PROFILE_TRANSACTION_CHILD}"
chmod 0600 -- "${PROFILE_TRANSACTION_CHILD}"
expect_failure 'orphan production profile-lock child identity is pending' \
    run_installer --check
rm -f -- "${PROFILE_TRANSACTION_CHILD}"
run_installer --check >/dev/null || fail 'profile-transaction guard damaged the framework'

# A second independently materialized checkout of the same raw commit must
# reproduce the exact source and framework identity.
INDEPENDENT_REPOSITORY=${WORK}/independent-repository
git clone -q --no-local --no-hardlinks "${REPOSITORY}" "${INDEPENDENT_REPOSITORY}"
GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    bash -- "${INDEPENDENT_REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" --check >/dev/null ||
    fail 'independent clean clone did not reproduce the active framework identity'

# Clean status alone is not a reproducible source identity: Git ignores files,
# cannot track empty directories, and index/config flags can suppress ordinary
# status changes.  Every class must fail while the active framework remains
# unchanged.
SOURCE_CONTRACT_BASELINE=$(readlink -- "${CURRENT}")
assert_source_rejection_clean() {
    [[ $(readlink -- "${CURRENT}") == "${SOURCE_CONTRACT_BASELINE}" ]] || \
        fail 'source Git-contract rejection changed the active framework'
    assert_no_transaction_debris
}

# The source identity must be one captured, peeled commit object.  Neither an
# invalid HEAD nor a tree object masquerading as HEAD may reach raw-tree
# enumeration or publication.
HEAD_FILE=${REPOSITORY}/.git/HEAD
HEAD_FILE_SAVED=${WORK}/HEAD.saved
cp -- "${HEAD_FILE}" "${HEAD_FILE_SAVED}"
printf 'not-a-valid-object-name\n' >"${HEAD_FILE}"
expect_failure 'cannot determine the raw Git object format' run_installer --check
assert_source_rejection_clean
cp -- "${HEAD_FILE_SAVED}" "${HEAD_FILE}"

NONCOMMIT_HEAD=$(git -C "${REPOSITORY}" rev-parse 'HEAD^{tree}')
printf '%s\n' "${NONCOMMIT_HEAD}" >"${HEAD_FILE}"
expect_failure 'raw HEAD does not resolve to a commit object' run_installer --check
assert_source_rejection_clean
cp -- "${HEAD_FILE_SAVED}" "${HEAD_FILE}"
run_installer --check >/dev/null || fail 'raw-commit HEAD rejection damaged the framework'

# A selected Git tree may contain only ordinary trees, regular blobs, and
# symlink blobs.  A gitlink is never materializable as an exact framework
# source entry and must fail before source status or copying is considered.
GITLINK_BASE=$(git -C "${REPOSITORY}" rev-parse 'HEAD^{commit}')
git -C "${REPOSITORY}" update-index --add --cacheinfo \
    160000 "${GITLINK_BASE}" portage/raw-head-gitlink-fixture
git -C "${REPOSITORY}" -c core.hooksPath=/dev/null -c commit.gpgSign=false commit -qm \
    'fixture raw HEAD gitlink rejection'
expect_failure \
    'unsupported raw HEAD mode/type 160000:commit: portage/raw-head-gitlink-fixture' \
    run_installer --check
assert_source_rejection_clean
git -C "${REPOSITORY}" reset --hard -q "${GITLINK_BASE}"
run_installer --check >/dev/null || fail 'raw gitlink rejection damaged the framework'

mkdir -- "${REPOSITORY}/portage/untracked-empty-directory"
expect_failure 'source filesystem inventory differs from the raw HEAD commit tree' run_installer
assert_source_rejection_clean
expect_failure 'source filesystem inventory differs from the raw HEAD commit tree' \
    run_installer --check
assert_source_rejection_clean
rmdir -- "${REPOSITORY}/portage/untracked-empty-directory"
run_installer --check >/dev/null || fail 'empty-directory rejection damaged the framework'

printf '*.ignored-source-fixture\n' >>"${REPOSITORY}/.git/info/exclude"
printf 'ignored input\n' \
    >"${REPOSITORY}/local-overlay/metadata/policy.ignored-source-fixture"
git -C "${REPOSITORY}" check-ignore -q -- \
    local-overlay/metadata/policy.ignored-source-fixture ||
    fail 'ignored-source fixture is not actually ignored'
expect_failure 'source filesystem inventory differs from the raw HEAD commit tree' run_installer
assert_source_rejection_clean
expect_failure 'source filesystem inventory differs from the raw HEAD commit tree' \
    run_installer --check
assert_source_rejection_clean
rm -f -- "${REPOSITORY}/local-overlay/metadata/policy.ignored-source-fixture"
run_installer --check >/dev/null || fail 'ignored-source rejection damaged the framework'

ASSUME_UNCHANGED=${REPOSITORY}/portage/make.conf
cp -- "${ASSUME_UNCHANGED}" "${WORK}/make.conf.saved"
git -C "${REPOSITORY}" update-index --assume-unchanged -- portage/make.conf
printf '\n# hidden assume-unchanged mutation\n' >>"${ASSUME_UNCHANGED}"
[[ -z $(git -C "${REPOSITORY}" status --porcelain=v1 -- portage/make.conf) ]] ||
    fail 'assume-unchanged fixture did not hide its worktree mutation'
expect_failure 'source filesystem inventory differs from the raw HEAD commit tree' run_installer
assert_source_rejection_clean
cp -- "${WORK}/make.conf.saved" "${ASSUME_UNCHANGED}"
git -C "${REPOSITORY}" update-index --no-assume-unchanged -- portage/make.conf
run_installer --check >/dev/null || fail 'assume-unchanged rejection damaged the framework'

git -C "${REPOSITORY}" update-index --skip-worktree -- portage/make.conf
printf '\n# hidden skip-worktree mutation\n' >>"${ASSUME_UNCHANGED}"
[[ -z $(git -C "${REPOSITORY}" status --porcelain=v1 -- portage/make.conf) ]] ||
    fail 'skip-worktree fixture did not hide its worktree mutation'
expect_failure 'source filesystem inventory differs from the raw HEAD commit tree' run_installer
assert_source_rejection_clean
cp -- "${WORK}/make.conf.saved" "${ASSUME_UNCHANGED}"
git -C "${REPOSITORY}" update-index --no-skip-worktree -- portage/make.conf
run_installer --check >/dev/null || fail 'skip-worktree rejection damaged the framework'

# git archive would honor this untracked attributes file and omit the hidden
# deletion.  Raw ls-tree/cat-file inventory must still see the committed blob.
ATTRIBUTE_PATH=local-overlay/metadata/.source-git-contract.fixture-input
printf '%s export-ignore\n' "${ATTRIBUTE_PATH}" >"${REPOSITORY}/.git/info/attributes"
git -C "${REPOSITORY}" update-index --assume-unchanged -- "${ATTRIBUTE_PATH}"
rm -f -- "${REPOSITORY}/${ATTRIBUTE_PATH}"
[[ -z $(git -C "${REPOSITORY}" status --porcelain=v1 -- "${ATTRIBUTE_PATH}") ]] ||
    fail 'export-ignore fixture did not hide its worktree deletion'
expect_failure 'source filesystem inventory differs from the raw HEAD commit tree' run_installer
assert_source_rejection_clean
git -C "${REPOSITORY}" show "HEAD:${ATTRIBUTE_PATH}" >"${REPOSITORY}/${ATTRIBUTE_PATH}"
chmod 0644 -- "${REPOSITORY}/${ATTRIBUTE_PATH}"
git -C "${REPOSITORY}" update-index --no-assume-unchanged -- "${ATTRIBUTE_PATH}"
rm -f -- "${REPOSITORY}/.git/info/attributes"
run_installer --check >/dev/null || fail 'raw-object attribute rejection damaged the framework'

git -C "${REPOSITORY}" config core.fileMode false
chmod 0755 -- "${REPOSITORY}/portage/make.conf"
[[ -z $(git -C "${REPOSITORY}" status --porcelain=v1 -- portage/make.conf) ]] ||
    fail 'core.fileMode=false fixture did not hide its executable-mode mutation'
expect_failure 'source filesystem inventory differs from the raw HEAD commit tree' run_installer
assert_source_rejection_clean
chmod 0644 -- "${REPOSITORY}/portage/make.conf"
git -C "${REPOSITORY}" config --unset core.fileMode
run_installer --check >/dev/null || fail 'fileMode rejection damaged the framework'

chmod 0666 -- "${REPOSITORY}/portage/make.conf"
expect_failure 'framework source input mode is unsafe:' run_installer
assert_source_rejection_clean
chmod 0644 -- "${REPOSITORY}/portage/make.conf"
run_installer --check >/dev/null || fail 'unsafe-file-mode rejection damaged the framework'

chmod 0777 -- "${REPOSITORY}/local-overlay/metadata"
expect_failure 'framework source input mode is unsafe:' run_installer
assert_source_rejection_clean
chmod 0755 -- "${REPOSITORY}/local-overlay/metadata"
run_installer --check >/dev/null || fail 'unsafe-directory-mode rejection damaged the framework'

chmod 4644 -- "${REPOSITORY}/portage/make.conf"
expect_failure 'framework source input mode is unsafe:' run_installer
assert_source_rejection_clean
chmod 0644 -- "${REPOSITORY}/portage/make.conf"
run_installer --check >/dev/null || fail 'special-file-mode rejection damaged the framework'

HARDLINK_PROBE=${WORK}/profile-locks-hardlink
ln -- "${REPOSITORY}/scripts/optimization/pgo/profile_locks.py" "${HARDLINK_PROBE}"
expect_failure 'framework source input is not a single-link regular file:' run_installer
assert_source_rejection_clean
rm -f -- "${HARDLINK_PROBE}"
run_installer --check >/dev/null || fail 'hardlink rejection damaged the framework'

setfattr -n user.gentoo_optimization_fixture -v present -- \
    "${REPOSITORY}/portage/make.conf"
expect_failure 'framework source input carries unbound extended metadata:' run_installer
assert_source_rejection_clean
setfattr -x user.gentoo_optimization_fixture -- "${REPOSITORY}/portage/make.conf"
run_installer --check >/dev/null || fail 'recursive-file xattr rejection damaged the framework'

EXPLICIT_XATTR=${REPOSITORY}/scripts/optimization/pgo/profile_locks.py
setfattr -n user.gentoo_optimization_fixture -v present -- "${EXPLICIT_XATTR}"
expect_failure 'framework source input carries unbound extended metadata:' run_installer
assert_source_rejection_clean
setfattr -x user.gentoo_optimization_fixture -- "${EXPLICIT_XATTR}"
run_installer --check >/dev/null || fail 'explicit-input xattr rejection damaged the framework'

DIRECTORY_XATTR=${REPOSITORY}/local-overlay/metadata
setfattr -n user.gentoo_optimization_fixture -v present -- "${DIRECTORY_XATTR}"
expect_failure 'framework source input carries unbound extended metadata:' run_installer
assert_source_rejection_clean
setfattr -x user.gentoo_optimization_fixture -- "${DIRECTORY_XATTR}"
run_installer --check >/dev/null || fail 'source Git-contract rejection damaged the active framework'

# Replacement refs may affect ordinary porcelain and object reads, but every
# source_git invocation pins the literal raw object graph.
REPLACEMENT_BASE=$(git -C "${REPOSITORY}" rev-parse HEAD)
ORIGINAL_BLOB=$(GIT_NO_REPLACE_OBJECTS=1 git -C "${REPOSITORY}" \
    rev-parse HEAD:portage/make.conf)
REPLACEMENT_CONTENT=${WORK}/replacement-content
GIT_NO_REPLACE_OBJECTS=1 git -C "${REPOSITORY}" cat-file blob "${ORIGINAL_BLOB}" \
    >"${REPLACEMENT_CONTENT}"
printf '\n# replacement-only mutation\n' >>"${REPLACEMENT_CONTENT}"
ALTERNATE_BLOB=$(git -C "${REPOSITORY}" hash-object -w -- "${REPLACEMENT_CONTENT}")
PRIVATE_INDEX=${WORK}/replacement.index
GIT_INDEX_FILE=${PRIVATE_INDEX} GIT_NO_REPLACE_OBJECTS=1 \
    git -C "${REPOSITORY}" read-tree "${REPLACEMENT_BASE}^{tree}"
GIT_INDEX_FILE=${PRIVATE_INDEX} GIT_NO_REPLACE_OBJECTS=1 \
    git -C "${REPOSITORY}" update-index --add --cacheinfo \
    100644 "${ALTERNATE_BLOB}" portage/make.conf
ALTERNATE_TREE=$(GIT_INDEX_FILE=${PRIVATE_INDEX} GIT_NO_REPLACE_OBJECTS=1 \
    git -C "${REPOSITORY}" write-tree)
ALTERNATE_COMMIT=$(printf 'replacement regression\n' | GIT_NO_REPLACE_OBJECTS=1 \
    git -C "${REPOSITORY}" commit-tree "${ALTERNATE_TREE}" -p "${REPLACEMENT_BASE}")
git -C "${REPOSITORY}" replace "${REPLACEMENT_BASE}" "${ALTERNATE_COMMIT}"
[[ $(git -C "${REPOSITORY}" rev-parse 'HEAD^{tree}') != \
    $(GIT_NO_REPLACE_OBJECTS=1 git -C "${REPOSITORY}" rev-parse 'HEAD^{tree}') ]] ||
    fail 'commit replacement fixture did not affect ordinary Git object traversal'
run_installer --check >/dev/null || fail 'commit replacement influenced raw HEAD identity'
git -C "${REPOSITORY}" replace -d "${REPLACEMENT_BASE}" >/dev/null

git -C "${REPOSITORY}" replace "${ORIGINAL_BLOB}" "${ALTERNATE_BLOB}"
NORMAL_BLOB_HASH=$(git -C "${REPOSITORY}" cat-file blob "${ORIGINAL_BLOB}" | sha256sum)
RAW_BLOB_HASH=$(GIT_NO_REPLACE_OBJECTS=1 git -C "${REPOSITORY}" \
    cat-file blob "${ORIGINAL_BLOB}" | sha256sum)
[[ ${NORMAL_BLOB_HASH%% *} != "${RAW_BLOB_HASH%% *}" ]] ||
    fail 'blob replacement fixture did not affect ordinary Git object reads'
run_installer --check >/dev/null || fail 'blob replacement influenced raw HEAD identity'
git -C "${REPOSITORY}" replace -d "${ORIGINAL_BLOB}" >/dev/null
assert_no_transaction_debris

assert_coherent_indirections() {
    local active
    [[ -L ${CURRENT} ]] || fail 'framework-current is not a symlink'
    active=$(readlink -- "${CURRENT}")
    [[ -d ${active} && ! -L ${active} ]] || fail 'framework-current target is unavailable'
    [[ -L ${TARGET}/etc/portage && $(readlink -- "${TARGET}/etc/portage") == \
        "${CURRENT}/portage" ]] || fail '/etc/portage bypasses framework-current'
    [[ -L ${TARGET}/usr/local/share/gentoo-optimization && \
        $(readlink -- "${TARGET}/usr/local/share/gentoo-optimization") == \
            "${CURRENT}/share" ]] || fail 'schema namespace bypasses framework-current'
    [[ -L ${MANIFEST} && $(readlink -- "${MANIFEST}") == \
        "${CURRENT}/install.manifest" ]] || fail 'manifest bypasses framework-current'
    cmp -s -- "${active}/install.manifest" "${MANIFEST}" || \
        fail 'manifest indirection exposes a mixed generation'
    cmp -s -- "${active}/share/schema/package-state.schema.json" \
        "${TARGET}/usr/local/share/gentoo-optimization/schema/package-state.schema.json" || \
        fail 'schema indirection exposes a mixed generation'
    grep -Fq -- "${CURRENT}" \
        "${TARGET}/usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py" || \
        fail 'helper bootstrap does not dispatch through framework-current'
    grep -Fq -- "gentoo_opt_framework_current=${CURRENT}" \
        "${TARGET}/usr/local/lib/install-qa-check.d/zz-gentoo-optimization-bolt" || \
        fail 'QA bootstrap does not dispatch through framework-current'
    "${TARGET}/usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py" --help \
        >/dev/null || fail 'Python-named helper bootstrap is not directly executable'
    /usr/bin/python3 -I \
        "${TARGET}/usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py" --help \
        >/dev/null || fail 'Python-named helper bootstrap cannot be invoked by the pinned interpreter'
}

ACTIVE=$(readlink -- "${CURRENT}")
[[ ${ACTIVE} == "${BASE}"/framework-[0-9a-f]* ]] || fail 'current target is not content-addressed'
[[ $(readlink -- "${TARGET}/etc/portage") == "${CURRENT}/portage" ]] || \
    fail '/etc/portage is not bound through the single current link'
grep -Fxq "current_generation=${ACTIVE}" "${MANIFEST}" || fail 'manifest omits exact current target'
grep -Fxq 'previous_generation=none' "${MANIFEST}" || fail 'initial manifest previous target differs'
grep -Eq '^installer_sha256=[0-9a-f]{64}$' "${MANIFEST}" || fail 'manifest installer hash is invalid'
grep -Eq '^source_aggregate_sha256=[0-9a-f]{64}$' "${MANIFEST}" || \
    fail 'manifest source aggregate is invalid'
grep -Eq '^git_commit=[0-9a-f]{40}$' "${MANIFEST}" || fail 'manifest Git commit is invalid'
grep -Fxq 'git_worktree=clean' "${MANIFEST}" || fail 'manifest clean/dirty state differs'
grep -Eq '^jq_sha256=[0-9a-f]{64}$' "${MANIFEST}" || fail 'manifest jq hash is invalid'
[[ $(stat -c %a -- "${ACTIVE}/portage") == 755 ]] || fail 'Portage candidate root is not 0755'
[[ $(stat -c %a -- "${ACTIVE}/portage/make.conf") == 644 ]] || fail 'Portage config is not 0644'
[[ $(stat -c %a -- "${TARGET}/usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py") == 755 ]] || \
    fail 'installed executable helper is not 0755'
[[ -x ${TARGET}/usr/local/libexec/gentoo-optimization/scripts/optimization/verify/reconcile-state.py ]] || \
    fail 'installed state reconciliation entry point is absent'
[[ -f ${TARGET}/usr/local/share/gentoo-optimization/schema/package-state.schema.json ]] || \
    fail 'installed package state schema is absent'
[[ -L ${TARGET}/usr/local/share/gentoo-optimization ]] || \
    fail 'schema namespace is not a stable current-generation indirection'
[[ -L ${MANIFEST} ]] || fail 'external manifest is not a stable current-generation indirection'
! cmp -s -- "${ACTIVE}/libexec/bolt/artifact_tool.py" \
    "${TARGET}/usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py" || \
    fail 'fixed helper path still contains a mutable generation implementation'
assert_coherent_indirections

# The exchanged helper directory itself is part of the stable contract, not
# merely a container for verified descendants. A trusted-but-wrong root mode
# must fail strict verification before source snapshotting or repair.
chmod 0700 -- "${TARGET}/usr/local/libexec/gentoo-optimization"
expect_failure 'directory ownership/mode differs' run_installer --check
chmod 0755 -- "${TARGET}/usr/local/libexec/gentoo-optimization"
run_installer --check >/dev/null || \
    fail 'restoring the exact external helper-root metadata did not restore strict verification'

# Every Python implementation in the immutable candidate must be reached only
# through the stable bootstrap, which pins isolated mode and disables bytecode
# publication.  Exercise every discovered Python helper without relying on a
# caller-supplied PYTHONDONTWRITEBYTECODE setting, then prove that neither the
# candidate namespace nor its canonical inventory changed and that the strict
# installer check remains clean.
CANDIDATE_HELPER_TREE_BEFORE=${WORK}/candidate-helper-tree.before
CANDIDATE_HELPER_TREE_AFTER=${WORK}/candidate-helper-tree.after
CANDIDATE_HELPER_CHECK_LOG=${WORK}/candidate-helper-check.log
find "${ACTIVE}" -mindepth 1 \
    -printf '%y\t%P\t%m\t%U\t%G\t%s\t%l\n' | sort \
    >"${CANDIDATE_HELPER_TREE_BEFORE}"
CANDIDATE_INVENTORY_SHA256_BEFORE=$(sha256sum -- \
    "${ACTIVE}/.candidate-inventory")
CANDIDATE_INVENTORY_SHA256_BEFORE=${CANDIDATE_INVENTORY_SHA256_BEFORE%% *}
[[ -z $(find "${ACTIVE}" \( -type d -name __pycache__ -o \
    -type f \( -name '*.pyc' -o -name '*.pyo' \) \) -print -quit) ]] || \
    fail 'immutable candidate already contains Python bytecode residue'
mapfile -d '' -t CANDIDATE_PYTHON_HELPERS < <(
    find "${ACTIVE}/libexec" -type f -name '*.py' -print0 | sort -z
)
(( ${#CANDIDATE_PYTHON_HELPERS[@]} > 0 )) || \
    fail 'immutable candidate contains no Python helper implementations'
for candidate_helper in "${CANDIDATE_PYTHON_HELPERS[@]}"; do
    candidate_helper_relative=${candidate_helper#"${ACTIVE}/libexec/"}
    [[ ${candidate_helper_relative} != "${candidate_helper}" ]] || \
        fail "candidate Python helper escaped libexec: ${candidate_helper}"
    candidate_helper_bootstrap=${TARGET}/usr/local/libexec/gentoo-optimization/${candidate_helper_relative}
    [[ -x ${candidate_helper_bootstrap} ]] || \
        fail "candidate Python helper lacks an executable stable bootstrap: ${candidate_helper_relative}"
    grep -Fxq \
        'os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", "-B", framework_tool, *sys.argv[1:]])' \
        "${candidate_helper_bootstrap}" || \
        fail "candidate Python bootstrap does not pin /usr/bin/python3 -I -B: ${candidate_helper_relative}"
    env -u PYTHONDONTWRITEBYTECODE -u PYTHONPYCACHEPREFIX \
        "${candidate_helper_bootstrap}" --help >/dev/null || \
        fail "candidate Python helper bootstrap did not execute cleanly: ${candidate_helper_relative}"
done
[[ -z $(find "${ACTIVE}" \( -type d -name __pycache__ -o \
    -type f \( -name '*.pyc' -o -name '*.pyo' \) \) -print -quit) ]] || \
    fail 'stable Python helper dispatch wrote bytecode into the immutable candidate'
find "${ACTIVE}" -mindepth 1 \
    -printf '%y\t%P\t%m\t%U\t%G\t%s\t%l\n' | sort \
    >"${CANDIDATE_HELPER_TREE_AFTER}"
cmp -- "${CANDIDATE_HELPER_TREE_BEFORE}" "${CANDIDATE_HELPER_TREE_AFTER}" || \
    fail 'stable Python helper execution changed the immutable candidate namespace'
[[ ${CANDIDATE_INVENTORY_SHA256_BEFORE} == "$(sha256sum -- \
    "${ACTIVE}/.candidate-inventory" | awk '{print $1}')" ]] || \
    fail 'stable Python helper execution changed the candidate inventory'
run_installer --check >"${CANDIDATE_HELPER_CHECK_LOG}" 2>&1 || {
    sed -n '1,260p' "${CANDIDATE_HELPER_CHECK_LOG}" >&2
    fail 'strict check failed after executing every candidate Python helper'
}
grep -Fq 'PASS: root-owned Phase 2 framework check verified' \
    "${CANDIDATE_HELPER_CHECK_LOG}" || \
    fail 'post-helper strict check omitted its PASS evidence'
find "${ACTIVE}" -mindepth 1 \
    -printf '%y\t%P\t%m\t%U\t%G\t%s\t%l\n' | sort \
    >"${CANDIDATE_HELPER_TREE_AFTER}"
cmp -- "${CANDIDATE_HELPER_TREE_BEFORE}" "${CANDIDATE_HELPER_TREE_AFTER}" || \
    fail 'post-helper strict check changed the immutable candidate namespace'
HELPER_BOOTSTRAP=${TARGET}/usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py

# The currently deployed pre-Candidate-A schema is the ten-helper tree from
# commit 8a1200915d2693fd7486a421a9b232f638e9840c.  Materialize it through a
# fixture-owned historical renderer, independent of the current installer,
# and first prove that its production rendering matches every SHA-256 observed
# in the deployed fixed namespace.  The later twelve-helper hybrid Git
# predecessor was never installed on this host and is intentionally not an
# accepted migration source.
LEGACY_CURRENT=$(readlink -- "${CURRENT}")
LEGACY_LIBEXEC=${TARGET}/usr/local/libexec/gentoo-optimization
GOLDEN_FIXTURE=${SOURCE_ROOT}/tests/optimization/fixtures/framework-bootstrap/deployed-v1
GOLDEN_PRODUCTION_TREE=${WORK}/deployed-v1-production
GOLDEN_TEST_TREE=${WORK}/deployed-v1-test-root
CURRENT_V2_MANIFEST=${WORK}/current-v2-helper-tree.manifest
LEGACY_BASELINE_MANIFEST=${WORK}/deployed-v1-helper-tree.manifest
PRE_CRASH_MANIFEST=${WORK}/deployed-v1-after-pre-crash.manifest
POST_CRASH_MANIFEST=${WORK}/current-v2-after-post-crash.manifest
POST_CRASH_CHECK_LOG=${WORK}/post-exchange-strict-check.log
[[ -x ${GOLDEN_FIXTURE}/render.sh && \
    -f ${GOLDEN_FIXTURE}/production.sha256 ]] || \
    fail 'deployed-v1 golden bootstrap fixture is incomplete'
"${GOLDEN_FIXTURE}/render.sh" "${GOLDEN_PRODUCTION_TREE}" \
    /var/lib/gentoo-optimization \
    /var/lib/gentoo-optimization/framework-current / 0
(
    cd -- "${GOLDEN_PRODUCTION_TREE}"
    sha256sum -c -- "${GOLDEN_FIXTURE}/production.sha256"
) >/dev/null || fail 'deployed-v1 golden renderer differs from the live hash manifest'
capture_helper_tree_manifest "${LEGACY_LIBEXEC}" "${CURRENT_V2_MANIFEST}"
"${GOLDEN_FIXTURE}/render.sh" "${GOLDEN_TEST_TREE}" \
    "${BASE}" "${CURRENT}" "${TARGET}" "$(id -u)"
rm -rf -- "${LEGACY_LIBEXEC}"
cp -a -- "${GOLDEN_TEST_TREE}" "${LEGACY_LIBEXEC}"
[[ $(find "${LEGACY_LIBEXEC}" -type f | wc -l) -eq 10 ]] || \
    fail 'legacy stable-bootstrap fixture does not contain exactly ten helpers'
while IFS= read -r -d '' legacy_python_bootstrap; do
    grep -Fxq '#!/usr/bin/python3 -I' "${legacy_python_bootstrap}" || \
        fail "golden legacy Python bootstrap shebang differs: ${legacy_python_bootstrap}"
    grep -Fxq \
        'os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", framework_tool, *sys.argv[1:]])' \
        "${legacy_python_bootstrap}" || \
        fail "golden legacy Python bootstrap exec differs: ${legacy_python_bootstrap}"
done < <(find "${LEGACY_LIBEXEC}" -type f -name '*.py' -print0 | sort -z)
capture_helper_tree_manifest "${LEGACY_LIBEXEC}" "${LEGACY_BASELINE_MANIFEST}"
LEGACY_PYTHON_BOOTSTRAP=${LEGACY_LIBEXEC}/pgo/profile-identity.py
LEGACY_PYTHON_BOOTSTRAP_COPY=${WORK}/legacy-python-bootstrap-v1
cp -- "${LEGACY_PYTHON_BOOTSTRAP}" "${LEGACY_PYTHON_BOOTSTRAP_COPY}"

# An unrecognized fixed bootstrap must fail before publication rather than being
# treated as a compatible old version.
printf '# unrecognized-fixture-byte\n' >>"${LEGACY_PYTHON_BOOTSTRAP}"
expect_failure 'stable-bootstrap migration required: installed helper bootstraps differ' \
    run_installer
[[ $(readlink -- "${CURRENT}") == "${LEGACY_CURRENT}" ]] || \
    fail 'unrecognized bootstrap rejection changed framework-current'
cmp -- "${LEGACY_PYTHON_BOOTSTRAP_COPY}" "${LEGACY_PYTHON_BOOTSTRAP}" 2>/dev/null && \
    fail 'unrecognized bootstrap fixture did not mutate its exact byte stream'
cp -- "${LEGACY_PYTHON_BOOTSTRAP_COPY}" "${LEGACY_PYTHON_BOOTSTRAP}"

# SIGKILL before the directory exchange preserves every v1 bootstrap exactly.
PAUSE_FILE=${WORK}/legacy-bootstrap-before-exchange
: >"${PAUSE_FILE}"
: >"${LOG}"
/usr/bin/setsid --wait env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_PAUSE_AT=before-helper-bootstrap-exchange \
    GENTOO_OPT_INSTALLER_PAUSE_FILE="${PAUSE_FILE}" \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" >"${LOG}" 2>&1 &
INSTALL_PID=$!
track_background_pid "${INSTALL_PID}"
wait_for_log 'PAUSE: before-helper-bootstrap-exchange' "${INSTALL_PID}"
kill -KILL "${INSTALL_PID}"
if wait "${INSTALL_PID}" 2>/dev/null; then
    fail 'pre-exchange legacy-bootstrap SIGKILL unexpectedly succeeded'
fi
terminate_background_group "${INSTALL_PID}"
untrack_background_pid "${INSTALL_PID}"
wait_for_installer_lock_release
rm -f -- "${PAUSE_FILE}"
[[ $(readlink -- "${CURRENT}") == "${LEGACY_CURRENT}" ]] || \
    fail 'pre-exchange legacy-bootstrap SIGKILL changed framework-current'
capture_helper_tree_manifest "${LEGACY_LIBEXEC}" "${PRE_CRASH_MANIFEST}"
cmp -- "${LEGACY_BASELINE_MANIFEST}" "${PRE_CRASH_MANIFEST}" || \
    fail 'pre-exchange legacy-bootstrap SIGKILL changed the complete v1 helper tree'

# SIGKILL immediately after the exchange cannot expose a partial bootstrap
# tree.  The old candidate remains selected, but the new no-bytecode
# dispatcher is compatible with it and the following strict check must pass.
PAUSE_FILE=${WORK}/legacy-bootstrap-after-exchange
: >"${PAUSE_FILE}"
: >"${LOG}"
/usr/bin/setsid --wait env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_PAUSE_AT=after-helper-bootstrap-exchange \
    GENTOO_OPT_INSTALLER_PAUSE_FILE="${PAUSE_FILE}" \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" >"${LOG}" 2>&1 &
INSTALL_PID=$!
track_background_pid "${INSTALL_PID}"
wait_for_log 'PAUSE: after-helper-bootstrap-exchange' "${INSTALL_PID}"
kill -KILL "${INSTALL_PID}"
if wait "${INSTALL_PID}" 2>/dev/null; then
    fail 'post-exchange legacy-bootstrap SIGKILL unexpectedly succeeded'
fi
terminate_background_group "${INSTALL_PID}"
untrack_background_pid "${INSTALL_PID}"
wait_for_installer_lock_release
rm -f -- "${PAUSE_FILE}"
[[ $(readlink -- "${CURRENT}") == "${LEGACY_CURRENT}" ]] || \
    fail 'post-exchange legacy-bootstrap SIGKILL changed framework-current'
# Observe the crash state before any repairing install.  The read-only strict
# check must accept it immediately, representative pre-existing and newly
# added dispatchers must both execute, and the complete metadata/content
# manifest must equal the previously captured v2 tree.
run_installer --check >"${POST_CRASH_CHECK_LOG}" 2>&1 || {
    sed -n '1,260p' "${POST_CRASH_CHECK_LOG}" >&2
    fail 'strict check rejected the immediate post-exchange SIGKILL state'
}
grep -Fq 'PASS: root-owned Phase 2 framework check verified' \
    "${POST_CRASH_CHECK_LOG}" || \
    fail 'immediate post-exchange strict check omitted PASS evidence'
"${LEGACY_LIBEXEC}/bolt/artifact_tool.py" --help >/dev/null || \
    fail 'post-exchange pre-existing helper bootstrap did not execute'
"${LEGACY_LIBEXEC}/pgo/authorization-token-scan.py" --help >/dev/null || \
    fail 'post-exchange newly added helper bootstrap did not execute'
capture_helper_tree_manifest "${LEGACY_LIBEXEC}" "${POST_CRASH_MANIFEST}"
cmp -- "${CURRENT_V2_MANIFEST}" "${POST_CRASH_MANIFEST}" || \
    fail 'post-exchange SIGKILL did not publish the complete exact v2 helper tree'

# Only after direct crash-state verification may a normal installer retry run
# its recovery/idempotence path.
run_installer >"${LOG}" 2>&1 || {
    sed -n '1,260p' "${LOG}" >&2
    fail 'reviewed v1-to-v2 stable-bootstrap migration failed'
}
run_installer --check >/dev/null || \
    fail 'post-exchange legacy-bootstrap SIGKILL did not leave a coherent framework'
capture_helper_tree_manifest "${LEGACY_LIBEXEC}" "${POST_CRASH_MANIFEST}"
cmp -- "${CURRENT_V2_MANIFEST}" "${POST_CRASH_MANIFEST}" || \
    fail 'v1-to-v2 recovery/idempotence changed the verified v2 helper tree'
[[ $(readlink -- "${CURRENT}") == "${LEGACY_CURRENT}" ]] || \
    fail 'v1-to-v2 stable-bootstrap repair changed the active candidate'
for added_helper in production-profile-lock-transaction.py authorization-token-scan.py; do
    [[ -x ${LEGACY_LIBEXEC}/pgo/${added_helper} ]] || \
        fail "v1-to-v2 stable-bootstrap repair omitted ${added_helper}"
done
run_installer --check >/dev/null || \
    fail 'v1-to-v2 stable-bootstrap repair failed strict verification'
assert_coherent_indirections

# An installed policy binds the process to its literal candidate. Re-sourcing
# the same candidate is harmless, but an upgrade must not make an old-bound
# process dispatch a new helper or QA implementation.
(
    source "${ACTIVE}/portage/bashrc"
    [[ ${GENTOO_OPT_FRAMEWORK_TARGET} == "${ACTIVE}" ]]
    source "${ACTIVE}/portage/bashrc"
    [[ ${GENTOO_OPT_FRAMEWORK_TARGET} == "${ACTIVE}" ]]
) || fail 'same-generation Portage policy re-source did not preserve its exact binding'

# Fixed bootstrap bytes are an invariant upgrade ABI. A changed renderer must
# stop before it can publish new fixed code under the old current generation.
BOOTSTRAP_BASELINE_CURRENT=$(readlink -- "${CURRENT}")
BOOTSTRAP_BASELINE_HASH=$(sha256sum -- "${HELPER_BOOTSTRAP}" | awk '{print $1}')
sed -i 's/Stable active-framework dispatcher/Changed active-framework dispatcher/' \
    "${REPOSITORY}/scripts/optimization/install-framework.sh"
git -C "${REPOSITORY}" add -- scripts/optimization/install-framework.sh
git -C "${REPOSITORY}" commit -qm 'fixture changed stable bootstrap'
expect_failure 'stable-bootstrap migration required: installed helper bootstraps differ' \
    run_installer
[[ $(readlink -- "${CURRENT}") == "${BOOTSTRAP_BASELINE_CURRENT}" ]] || \
    fail 'stable-bootstrap incompatibility changed framework-current'
[[ $(sha256sum -- "${HELPER_BOOTSTRAP}" | awk '{print $1}') == \
    "${BOOTSTRAP_BASELINE_HASH}" ]] || \
    fail 'stable-bootstrap incompatibility changed fixed helper bytes'
git -C "${REPOSITORY}" revert --no-edit HEAD >/dev/null
assert_no_transaction_debris

OLD_BOUND=${ACTIVE}
RACE_READY=${WORK}/generation-race.ready
RACE_GO=${WORK}/generation-race.go
RACE_LOG=${WORK}/generation-race.log
RACE_HELPER_LOG=${WORK}/generation-race-helper.log
RACE_SHELL_HELPER_LOG=${WORK}/generation-race-shell-helper.log
RACE_RESOURCE_LOG=${WORK}/generation-race-resource.log
RACE_CHILD=${WORK}/generation-race-child.sh
cat >"${RACE_CHILD}" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
old_bound=$1
helper_bootstrap=$2
capture_bootstrap=$3
qa_bootstrap=$4
portage_bashrc=$5
ready=$6
go=$7
helper_log=$8
shell_helper_log=$9
resource_log=${10}
source "${old_bound}/portage/bashrc"
source "${old_bound}/portage/bashrc"
[[ ${GENTOO_OPT_FRAMEWORK_TARGET} == "${old_bound}" ]]
: >"${ready}"
while [[ ! -e ${go} ]]; do sleep 0.05; done
GENTOO_OPT_TEST_GENERATION_PROBE=1 \
    "${helper_bootstrap}" --help >"${helper_log}" 2>&1
if grep -Fq 'NEW-GENERATION-HELPER' "${helper_log}"; then exit 87; fi
grep -Fq 'usage:' "${helper_log}"
GENTOO_OPT_TEST_GENERATION_PROBE=1 \
    "${capture_bootstrap}" --help >"${shell_helper_log}" 2>&1
if grep -Fq 'NEW-GENERATION-HELPER' "${shell_helper_log}"; then exit 86; fi
grep -Fq 'usage:' "${shell_helper_log}"
GENTOO_OPT_TEST_QA_GENERATION=old-bound
source "${qa_bootstrap}"
[[ ${GENTOO_OPT_TEST_QA_GENERATION} == old-bound ]]
die() { return 98; }
if source "${portage_bashrc}" >"${resource_log}" 2>&1; then exit 88; fi
grep -Fq 'attempted to cross framework generations' "${resource_log}"
EOF
chmod 0700 -- "${RACE_CHILD}"
/usr/bin/setsid --wait bash -- "${RACE_CHILD}" \
    "${OLD_BOUND}" "${HELPER_BOOTSTRAP}" \
    "${TARGET}/usr/local/libexec/gentoo-optimization/bolt/capture-input.sh" \
    "${TARGET}/usr/local/lib/install-qa-check.d/zz-gentoo-optimization-bolt" \
    "${TARGET}/etc/portage/bashrc" "${RACE_READY}" "${RACE_GO}" \
    "${RACE_HELPER_LOG}" "${RACE_SHELL_HELPER_LOG}" "${RACE_RESOURCE_LOG}" \
    >"${RACE_LOG}" 2>&1 &
RACE_PID=$!
track_background_pid "${RACE_PID}"
for _ in $(seq 1 100); do
    [[ -e ${RACE_READY} ]] && break
    sleep 0.05
done
[[ -e ${RACE_READY} ]] || fail 'old-generation bound process did not start'
sed -i '/^import os$/a if os.environ.get("GENTOO_OPT_TEST_GENERATION_PROBE") == "1": print("NEW-GENERATION-HELPER"); raise SystemExit(0)' \
    "${REPOSITORY}/scripts/optimization/bolt/artifact_tool.py"
sed -i '5i GENTOO_OPT_TEST_QA_GENERATION=new-generation' \
    "${REPOSITORY}/portage/install-qa-check.d/zz-gentoo-optimization-bolt"
git -C "${REPOSITORY}" add -- \
    scripts/optimization/bolt/artifact_tool.py \
    portage/install-qa-check.d/zz-gentoo-optimization-bolt
git -C "${REPOSITORY}" commit -qm 'fixture new framework generation'
run_installer >/dev/null
RACE_NEW_ACTIVE=$(readlink -- "${CURRENT}")
[[ ${RACE_NEW_ACTIVE} != "${OLD_BOUND}" ]] || fail 'generation-race fixture did not activate new content'
: >"${RACE_GO}"
if ! wait "${RACE_PID}"; then
    sed -n '1,240p' "${RACE_LOG}" >&2 || true
    fail 'old-bound process crossed or lost its pinned generation during upgrade'
fi
assert_background_group_gone "${RACE_PID}"
untrack_background_pid "${RACE_PID}"
git -C "${REPOSITORY}" revert --no-edit HEAD >/dev/null
run_installer >/dev/null
ACTIVE=$(readlink -- "${CURRENT}")
assert_coherent_indirections

chmod 0777 -- "${BASE}"
if "${HELPER_BOOTSTRAP}" --help >"${LOG}" 2>&1; then
    fail 'helper bootstrap trusted a writable framework base'
fi
chmod 0755 -- "${BASE}"
chmod 0777 -- "${ACTIVE}/libexec/bolt"
if "${HELPER_BOOTSTRAP}" --help >"${LOG}" 2>&1; then
    fail 'helper bootstrap trusted a writable candidate ancestor'
fi
chmod 0755 -- "${ACTIVE}/libexec/bolt"
assert_coherent_indirections
[[ $(stat -c '%g:%a' -- "${TARGET}/var/cache/gentoo-optimization/pgo") == "$(id -g):750" ]] || \
    fail 'validated PGO cache trust root mode differs'
[[ $(stat -c '%g:%a' -- "${TARGET}/var/tmp/gentoo-optimization/pgo-raw") == "$(id -g):755" ]] || \
    fail 'raw PGO spool trust root mode differs'
[[ $(stat -c %a -- "${TARGET}/var/lib/gentoo-optimization/generations") == 755 ]] || \
    fail 'generation trust root mode differs'
for lock in framework-install project generation; do
    [[ $(stat -c '%u:%g:%a' -- "${TARGET}/run/gentoo-optimization/${lock}.lock") == \
        "$(id -u):$(id -g):600" ]] || fail "${lock} lock ownership/mode differs"
done
for lock in project generation; do
    ready=${WORK}/${lock}-lock-ready
    # shellcheck disable=SC2016  # Positional parameters expand in the child shell.
    /usr/bin/setsid --wait bash -Eeuo pipefail -c '
        exec 9<>"$1"
        flock -x 9
        : >"$2"
        sleep 20
    ' bash "${TARGET}/run/gentoo-optimization/${lock}.lock" "${ready}" &
    holder=$!
    track_background_pid "${holder}"
    for _ in $(seq 1 100); do
        [[ -e ${ready} ]] && break
        sleep 0.05
    done
    [[ -e ${ready} ]] || fail "${lock} lock holder did not start"
    expect_failure "cannot acquire shared project lock: ${TARGET}/run/gentoo-optimization/${lock}.lock" \
        run_installer --check
    kill "${holder}" 2>/dev/null || true
    wait "${holder}" 2>/dev/null || true
    terminate_background_group "${holder}"
    untrack_background_pid "${holder}"
done
mapfile -t GENERATED_ENTRIES < <(find "${ACTIVE}/generated-policy" -mindepth 1 \
    -maxdepth 1 -printf '%f\n' | sort)
[[ ${GENERATED_ENTRIES[*]} == $'.identity\nenv\npackage.env' && \
    ! -s ${ACTIVE}/generated-policy/package.env && \
    -z $(find "${ACTIVE}/generated-policy/env" -mindepth 1 -print -quit) ]] || \
    fail 'generated policy is not rigorously empty'
[[ $(readlink -- "${ACTIVE}/portage/package.env/99-generated-optimization") == \
    ../../generated-policy/package.env ]] || fail 'generated package policy is not Portage-bound'
[[ $(readlink -- "${ACTIVE}/portage/env/optimization/generated") == \
    ../../../generated-policy/env ]] || fail 'generated environments are not Portage-bound'

# Global ordering is checked over sysadmin, system, repositories and built-in
# QA directories, not just the source Portage tree.
mkdir -p -- "${TARGET}/usr/lib/install-qa-check.d"
printf '# late QA fixture\n' >"${TARGET}/usr/lib/install-qa-check.d/zzz-after-optimization"
expect_failure 'QA check sorts after zz-gentoo-optimization-bolt' run_installer --check
rm -f -- "${TARGET}/usr/lib/install-qa-check.d/zzz-after-optimization"

# Strict candidate inventory catches both an extra path and ordinary content
# tampering, while the current generation remains intact for the next cases.
printf 'unexpected\n' >"${ACTIVE}/unexpected-entry"
expect_failure 'immutable candidate entry set or content differs' run_installer --check
rm -f -- "${ACTIVE}/unexpected-entry"
printf 'tamper\n' >>"${ACTIVE}/portage/make.conf"
expect_failure 'immutable candidate entry set or content differs' run_installer --check
git -C "${REPOSITORY}" show HEAD:portage/make.conf >"${ACTIVE}/portage/make.conf"
chmod 0644 -- "${ACTIVE}/portage/make.conf"
run_installer --check >/dev/null

# A canonical-manifest mismatch occurs after the check-only temporary is
# created.  Its failure path must remove that temporary so the restored
# generation can pass another check immediately.
ACTIVE_MANIFEST_SAVED=${WORK}/active-install.manifest.saved
cp -- "${ACTIVE}/install.manifest" "${ACTIVE_MANIFEST_SAVED}"
sed -i 's/^qa_hook_basename=.*/qa_hook_basename=fixture-mismatch/' \
    "${ACTIVE}/install.manifest"
expect_failure 'active generation manifest is not the strict canonical manifest' \
    run_installer --check
assert_no_transaction_debris
install -m 0600 -T -- "${ACTIVE_MANIFEST_SAVED}" "${ACTIVE}/install.manifest"
run_installer --check >/dev/null || \
    fail 'canonical-manifest failure cleanup poisoned the following strict check'

# Candidate ownership and inode topology are exact invariants, not merely
# content checks.  An external hardlink changes the candidate inode group and
# must fail even though the bytes and mode remain unchanged.
CANDIDATE_HARDLINK=${WORK}/candidate-make-conf-hardlink
ln -- "${ACTIVE}/portage/make.conf" "${CANDIDATE_HARDLINK}"
expect_failure 'candidate regular file is not single-link:' run_installer --check
rm -f -- "${CANDIDATE_HARDLINK}"

mapfile -t FIXTURE_MEMBER_GIDS < <(id -G | tr ' ' '\n')
if ((EUID == 0)) && [[ $(id -g) != 65534 ]]; then
    # The authoritative host gate runs as real root, which may intentionally
    # have no supplementary groups. Root can still construct the required
    # wrong-group candidate deterministically without weakening production
    # ownership checks. A restricted user namespace will fail this probe and
    # retain the explicit required skip below.
    FIXTURE_MEMBER_GIDS+=(65534)
fi
ALTERNATE_FIXTURE_GID=
ALTERNATE_GID_PROBE=${WORK}/alternate-gid-capability-probe
: >"${ALTERNATE_GID_PROBE}"
chmod 0600 -- "${ALTERNATE_GID_PROBE}"
for candidate_gid in "${FIXTURE_MEMBER_GIDS[@]}"; do
    if [[ ${candidate_gid} != "$(id -g)" ]] &&
       chgrp "${candidate_gid}" -- "${ALTERNATE_GID_PROBE}" 2>/dev/null &&
       [[ $(stat -c %g -- "${ALTERNATE_GID_PROBE}") == "${candidate_gid}" ]] &&
       chgrp "$(id -g)" -- "${ALTERNATE_GID_PROBE}" 2>/dev/null &&
       [[ $(stat -c %g -- "${ALTERNATE_GID_PROBE}") == "$(id -g)" ]]; then
        ALTERNATE_FIXTURE_GID=${candidate_gid}
        break
    fi
    chgrp "$(id -g)" -- "${ALTERNATE_GID_PROBE}" 2>/dev/null || \
        fail 'alternate-GID capability probe could not restore its private inode'
done
rm -f -- "${ALTERNATE_GID_PROBE}"
if [[ -n ${ALTERNATE_FIXTURE_GID} ]]; then
    chgrp "${ALTERNATE_FIXTURE_GID}" -- "${ACTIVE}/portage/make.conf"
    expect_failure 'candidate entry has the wrong owner/group:' run_installer --check
    chgrp "$(id -g)" -- "${ACTIVE}/portage/make.conf"
    record_required_subtest PASS ownership.alternate-gid \
        'strict candidate verification rejected an alternate group identity'
else
    record_required_subtest SKIP ownership.alternate-gid \
        'no mapped alternate GID can exercise candidate ownership mismatch'
    printf 'INFO: alternate GID unavailable; recorded required subtest SKIP\n'
fi

# Command substitution strips trailing newlines from readlink output.  The
# verifier must consume a NUL-terminated target so this control-byte mutation
# cannot serialize as the original generated-policy link.
CANDIDATE_GENERATED_LINK=${ACTIVE}/portage/env/optimization/generated
rm -f -- "${CANDIDATE_GENERATED_LINK}"
ln -s -- $'../../../generated-policy/env\n' "${CANDIDATE_GENERATED_LINK}"
expect_failure 'symlink target contains a control byte' run_installer --check
rm -f -- "${CANDIDATE_GENERATED_LINK}"
ln -s -- ../../../generated-policy/env "${CANDIDATE_GENERATED_LINK}"
run_installer --check >/dev/null || \
    fail 'candidate topology rejection damaged the framework'

chmod 0666 -- "${ACTIVE}/portage/make.conf"
expect_failure 'candidate entry is group/world-writable:' run_installer --check
chmod 0644 -- "${ACTIVE}/portage/make.conf"
chmod 0777 -- "${ACTIVE}/local-overlay/metadata"
expect_failure 'candidate entry is group/world-writable:' run_installer --check
chmod 0755 -- "${ACTIVE}/local-overlay/metadata"
chmod 4644 -- "${ACTIVE}/portage/make.conf"
expect_failure 'candidate entry has unsafe special mode bits:' run_installer --check
chmod 0644 -- "${ACTIVE}/portage/make.conf"
setfattr -n user.gentoo_optimization_fixture -v present -- \
    "${ACTIVE}/share/schema/package-state.schema.json"
expect_failure 'immutable framework candidate carries unbound extended metadata:' \
    run_installer --check
setfattr -x user.gentoo_optimization_fixture -- \
    "${ACTIVE}/share/schema/package-state.schema.json"
run_installer --check >/dev/null || fail 'candidate metadata rejection damaged the framework'

# Every existing BOLT lock is held across publication.  A busy transaction
# fails before any reviewed input snapshot is made.
LOCK=${TARGET}/var/cache/gentoo-optimization/bolt/locks/busy.lock
: >"${LOCK}"
# shellcheck disable=SC2016  # Positional parameters expand in the child shell.
/usr/bin/setsid --wait bash -Eeuo pipefail -c '
    exec 9<>"$1"
    flock 9
    printf ready >"$2"
    sleep 20
' bash "${LOCK}" "${WORK}/bolt-lock-ready" &
LOCK_PID=$!
track_background_pid "${LOCK_PID}"
for _ in $(seq 1 100); do
    [[ -e ${WORK}/bolt-lock-ready ]] && break
    sleep 0.05
done
[[ -e ${WORK}/bolt-lock-ready ]] || fail 'BOLT lock holder did not start'
expect_failure 'an active BOLT transaction holds' run_installer --check
kill "${LOCK_PID}" 2>/dev/null || true
wait "${LOCK_PID}" 2>/dev/null || true
terminate_background_group "${LOCK_PID}"
untrack_background_pid "${LOCK_PID}"
rm -f -- "${LOCK}"

BASELINE_CURRENT=$(readlink -- "${CURRENT}")
BASELINE_MANIFEST_HASH=$(sha256sum -- "${MANIFEST}" | awk '{print $1}')
BASELINE_HELPER_HASH=$(sha256sum -- \
    "${TARGET}/usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py" | awk '{print $1}')

# A worktree identity change produces a distinct candidate.  Failures before
# and after the current-link rename must both restore every prior entry point.
printf 'dirty identity\n' >"${REPOSITORY}/worktree-identity-marker"
expect_failure 'injected installer failure at after-helpers' \
    env GENTOO_OPT_INSTALLER_TEST_MODE=1 GENTOO_OPT_INSTALLER_FAIL_AT=after-helpers \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" --test-root "${TARGET}"
[[ $(readlink -- "${CURRENT}") == "${BASELINE_CURRENT}" ]] || fail 'pre-activation failure changed current'
[[ $(sha256sum -- "${MANIFEST}" | awk '{print $1}') == "${BASELINE_MANIFEST_HASH}" ]] || \
    fail 'pre-activation failure changed manifest'
[[ $(sha256sum -- "${TARGET}/usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py" | awk '{print $1}') == \
    "${BASELINE_HELPER_HASH}" ]] || fail 'pre-activation failure changed helper'
assert_no_transaction_debris

expect_failure 'injected installer failure at after-activation' \
    env GENTOO_OPT_INSTALLER_TEST_MODE=1 GENTOO_OPT_INSTALLER_FAIL_AT=after-activation \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" --test-root "${TARGET}"
[[ $(readlink -- "${CURRENT}") == "${BASELINE_CURRENT}" ]] || fail 'post-activation failure did not roll back current'
[[ $(sha256sum -- "${MANIFEST}" | awk '{print $1}') == "${BASELINE_MANIFEST_HASH}" ]] || \
    fail 'post-activation failure did not roll back manifest'
assert_no_transaction_debris

# Signal interruption at the quiescent boundary follows the same rollback path.
PAUSE_FILE=${WORK}/pause
: >"${PAUSE_FILE}"
: >"${LOG}"
/usr/bin/setsid --wait env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_PAUSE_AT=before-activation \
    GENTOO_OPT_INSTALLER_PAUSE_FILE="${PAUSE_FILE}" \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" >"${LOG}" 2>&1 &
INSTALL_PID=$!
track_background_pid "${INSTALL_PID}"
wait_for_log 'PAUSE: before-activation' "${INSTALL_PID}"
kill -TERM "${INSTALL_PID}"
if wait "${INSTALL_PID}"; then fail 'signal-interrupted installer unexpectedly succeeded'; fi
terminate_background_group "${INSTALL_PID}"
untrack_background_pid "${INSTALL_PID}"
rm -f -- "${PAUSE_FILE}"
[[ $(readlink -- "${CURRENT}") == "${BASELINE_CURRENT}" ]] || fail 'signal interruption changed current'
assert_coherent_indirections
assert_no_transaction_debris

# SIGKILL cannot run shell cleanup.  Kill immediately before activation and
# prove that every fixed entry point still exposes only the old generation;
# the next installer pass must remove abandoned transaction files safely.
PAUSE_FILE=${WORK}/kill-before-activation
: >"${PAUSE_FILE}"
: >"${LOG}"
/usr/bin/setsid --wait env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_PAUSE_AT=before-activation \
    GENTOO_OPT_INSTALLER_PAUSE_FILE="${PAUSE_FILE}" \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" >"${LOG}" 2>&1 &
INSTALL_PID=$!
track_background_pid "${INSTALL_PID}"
wait_for_log 'PAUSE: before-activation' "${INSTALL_PID}"
kill -KILL "${INSTALL_PID}"
if wait "${INSTALL_PID}" 2>/dev/null; then fail 'SIGKILLed installer unexpectedly succeeded'; fi
terminate_background_group "${INSTALL_PID}"
untrack_background_pid "${INSTALL_PID}"
wait_for_installer_lock_release
rm -f -- "${PAUSE_FILE}"
[[ $(readlink -- "${CURRENT}") == "${BASELINE_CURRENT}" ]] || \
    fail 'pre-activation SIGKILL changed current'
assert_coherent_indirections
assert_transaction_debris_present
mkdir -p -- "${BASE}/.source-git-contract.fixture-stale"
printf 'stale audit\n' >"${BASE}/.extended-metadata-audit.fixture-stale"
mkdir -p -- "${TARGET}/unmanaged-debris-decoys"
printf 'must survive fixed-parent cleanup\n' \
    >"${TARGET}/unmanaged-debris-decoys/.source-git-contract.fixture-decoy"
printf 'must survive fixed-parent cleanup\n' \
    >"${TARGET}/unmanaged-debris-decoys/.extended-metadata-audit.fixture-decoy"
expect_failure 'stale framework publication debris remains:' run_installer --check
assert_transaction_debris_present
expect_failure 'injected installer failure at after-candidate' \
    env GENTOO_OPT_INSTALLER_TEST_MODE=1 GENTOO_OPT_INSTALLER_FAIL_AT=after-candidate \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" --test-root "${TARGET}"
[[ $(readlink -- "${CURRENT}") == "${BASELINE_CURRENT}" ]] || \
    fail 'debris recovery changed current before activation'
assert_coherent_indirections
assert_no_transaction_debris
[[ -f ${TARGET}/unmanaged-debris-decoys/.source-git-contract.fixture-decoy && \
    -f ${TARGET}/unmanaged-debris-decoys/.extended-metadata-audit.fixture-decoy ]] || \
    fail 'fixed-parent stale cleanup removed an unmanaged decoy'

# A source edit during the one-time snapshot is detected; no mixed candidate is
# allowed to escape.  This runs only in the copied fixture repository.
PAUSE_FILE=${WORK}/source-pause
: >"${PAUSE_FILE}"
: >"${LOG}"
/usr/bin/setsid --wait env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_PAUSE_AT=before-source-copy \
    GENTOO_OPT_INSTALLER_PAUSE_FILE="${PAUSE_FILE}" \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" >"${LOG}" 2>&1 &
INSTALL_PID=$!
track_background_pid "${INSTALL_PID}"
wait_for_log 'PAUSE: before-source-copy' "${INSTALL_PID}"
printf '\n# concurrent fixture mutation\n' >>"${REPOSITORY}/portage/make.conf"
rm -f -- "${PAUSE_FILE}"
if wait "${INSTALL_PID}"; then fail 'mixed-source installer unexpectedly succeeded'; fi
assert_background_group_gone "${INSTALL_PID}"
untrack_background_pid "${INSTALL_PID}"
grep -Fq 'source filesystem inventory differs from the raw HEAD commit tree' "${LOG}" || {
    sed -n '1,240p' "${LOG}" >&2
    fail 'mixed-source failure lacks exact diagnostic'
}
git -C "${REPOSITORY}" checkout -q -- portage/make.conf
assert_no_transaction_debris

# Finally kill the installer immediately after its single activation rename.
# All consumers must already expose the new generation coherently, and a retry
# must recover abandoned transaction files without reverting the committed link.
PAUSE_FILE=${WORK}/kill-after-activation
: >"${PAUSE_FILE}"
: >"${LOG}"
/usr/bin/setsid --wait env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_PAUSE_AT=after-activation \
    GENTOO_OPT_INSTALLER_PAUSE_FILE="${PAUSE_FILE}" \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" >"${LOG}" 2>&1 &
INSTALL_PID=$!
track_background_pid "${INSTALL_PID}"
wait_for_log 'PAUSE: after-activation' "${INSTALL_PID}"
kill -KILL "${INSTALL_PID}"
if wait "${INSTALL_PID}" 2>/dev/null; then fail 'post-activation SIGKILL unexpectedly succeeded'; fi
terminate_background_group "${INSTALL_PID}"
untrack_background_pid "${INSTALL_PID}"
wait_for_installer_lock_release
rm -f -- "${PAUSE_FILE}"
NEW_ACTIVE=$(readlink -- "${CURRENT}")
[[ ${NEW_ACTIVE} != "${BASELINE_CURRENT}" ]] || fail 'changed worktree identity reused old generation'
assert_coherent_indirections
grep -Fxq "previous_generation=${BASELINE_CURRENT}" "${MANIFEST}" || \
    fail 'new manifest omits exact rollback generation'
run_installer >/dev/null
[[ $(readlink -- "${CURRENT}") == "${NEW_ACTIVE}" ]] || \
    fail 'post-SIGKILL recovery changed the committed generation'
run_installer --check >/dev/null
assert_no_transaction_debris

# The future generated-policy interface is content-addressed and consumed from
# inside the same framework generation.  A check without the exact policy input
# then fails closed rather than silently treating it as the empty generation.
POLICY_STAGE=${WORK}/policy-stage
mkdir -p -- "${POLICY_STAGE}/env"
printf '=app-misc/example-1 optimization/generated/example.conf\n' >"${POLICY_STAGE}/package.env"
printf 'GENTOO_OPT_MODE=off\n' >"${POLICY_STAGE}/env/example.conf"
POLICY_HASH=$(
    while IFS= read -r -d '' entry; do
        relative=${entry#"${POLICY_STAGE}/"}
        if [[ -d ${entry} ]]; then
            printf 'd\t0755\t-\tgenerated-policy/%s\t-\n' "${relative}"
        else
            digest=$(sha256sum -- "${entry}"); digest=${digest%% *}
            printf 'f\t0644\t%s\tgenerated-policy/%s\t-\n' "${digest}" "${relative}"
        fi
    done < <(find "${POLICY_STAGE}" -mindepth 1 -print0 | sort -z)
)
POLICY_HASH=$( # Restore the record-terminating newline stripped by command substitution.
    printf '%s\n' "${POLICY_HASH}" | sha256sum | awk '{print $1}'
)
POLICY_PARENT=${TARGET}/var/lib/gentoo-optimization/generated-policy-sources
mkdir -p -- "${POLICY_PARENT}"
POLICY=${POLICY_PARENT}/generated-policy-${POLICY_HASH}
mv -- "${POLICY_STAGE}" "${POLICY}"
FROZEN_INVENTORY=${TARGET}/var/lib/gentoo-optimization/generations/fixture/frozen-inventory.json
mkdir -p -- "${FROZEN_INVENTORY%/*}"
printf '%s\n' \
    '{"schema_version":2,"record_type":"frozen-inventory","generation_id":"fixture","inventory_id":"fixture-inventory","packages":[{"cpv":"app-misc/example-1"}]}' \
    >"${FROZEN_INVENTORY}"
expect_failure 'strict frozen-inventory semantic validation failed' \
    run_installer --generated-policy-generation "${POLICY}" \
    --frozen-inventory "${FROZEN_INVENTORY}"
printf '%s\n' \
    '{"schema_version":2,"record_type":"frozen-inventory","generation_id":"fixture","inventory_id":"fixture-inventory","packages":[{"cpv":"app-misc/example-1","entry_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],"owned_paths":[],"owned_directories":[]}' \
    >"${FROZEN_INVENTORY}"
cp -- "${POLICY}/env/example.conf" "${WORK}/example.conf.saved"
printf 'source /tmp/forbidden\n' >"${POLICY}/env/example.conf"
expect_failure 'generated environment is not assignment-only' \
    run_installer --generated-policy-generation "${POLICY}" \
    --frozen-inventory "${FROZEN_INVENTORY}"
cp -- "${WORK}/example.conf.saved" "${POLICY}/env/example.conf"
printf 'GENTOO_OPT_MODE=off\n' >"${POLICY}/env/unreferenced.conf"
expect_failure 'generated environment is unreferenced' \
    run_installer --generated-policy-generation "${POLICY}" \
    --frozen-inventory "${FROZEN_INVENTORY}"
rm -f -- "${POLICY}/env/unreferenced.conf"
printf 'not-an-atom optimization/generated/example.conf\n' >"${POLICY}/package.env"
expect_failure 'generated package.env atom contains unsafe syntax' \
    run_installer --generated-policy-generation "${POLICY}" \
    --frozen-inventory "${FROZEN_INVENTORY}"
printf '=app-misc/example-1 optimization/generated/example.conf\n' >"${POLICY}/package.env"
expect_failure 'a nonempty generated policy requires --frozen-inventory' \
    run_installer --generated-policy-generation "${POLICY}"
printf '%s\n' \
    '{"schema_version":2,"record_type":"frozen-inventory","generation_id":"fixture","inventory_id":"fixture-inventory","packages":[{"cpv":"app-misc/different-1","entry_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}],"owned_paths":[],"owned_directories":[]}' \
    >"${FROZEN_INVENTORY}"
expect_failure 'generated package.env atom is absent from the frozen inventory' \
    run_installer --generated-policy-generation "${POLICY}" \
    --frozen-inventory "${FROZEN_INVENTORY}"
printf '%s\n' \
    '{"schema_version":2,"record_type":"frozen-inventory","generation_id":"fixture","inventory_id":"fixture-inventory","packages":[{"cpv":"app-misc/example-1","entry_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],"owned_paths":[],"owned_directories":[]}' \
    >"${FROZEN_INVENTORY}"
run_installer --generated-policy-generation "${POLICY}" \
    --frozen-inventory "${FROZEN_INVENTORY}" >/dev/null
POLICY_ACTIVE=$(readlink -- "${CURRENT}")
[[ $(<"${POLICY_ACTIVE}/generated-policy/.identity") == "${POLICY_HASH}" ]] || \
    fail 'content-addressed generated policy identity differs'
cmp -s -- "${POLICY}/package.env" "${POLICY_ACTIVE}/generated-policy/package.env" || \
    fail 'generated package policy was not published exactly'
run_installer --check --generated-policy-generation "${POLICY}" \
    --frozen-inventory "${FROZEN_INVENTORY}" >/dev/null
expect_failure 'generated-policy identity differs' \
    run_installer --check
assert_no_transaction_debris

printf 'PASS: framework installer snapshot/publication/rollback fixture\n'

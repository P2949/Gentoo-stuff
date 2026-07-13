#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export LC_ALL=C

SOURCE_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
WORK=$(mktemp -d /tmp/gentoo-optimization-framework-installer.XXXXXXXX)
REPOSITORY=${WORK}/repository
TARGET=${WORK}/target
LOG=${WORK}/installer.log
trap 'rm -rf -- "${WORK}"' EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

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
    local needle=$1 index
    for index in $(seq 1 100); do
        grep -Fq -- "${needle}" "${LOG}" 2>/dev/null && return 0
        sleep 0.1
    done
    sed -n '1,240p' "${LOG}" >&2 || true
    fail "timed out waiting for installer marker: ${needle}"
}

assert_no_transaction_debris() {
    ! find "${TARGET}" -name '*.partial.*' -o -name '.framework-rollback.*' \
        -o -name '.framework-source-snapshot.*' | grep -q . || \
        fail 'installer left partial, rollback, or source-snapshot debris'
}

mkdir -p -- "${REPOSITORY}/scripts/optimization" "${TARGET}"
cp -a -- "${SOURCE_ROOT}/portage" "${REPOSITORY}/portage"
cp -a -- "${SOURCE_ROOT}/local-overlay" "${REPOSITORY}/local-overlay"
cp -a -- "${SOURCE_ROOT}/scripts/optimization/bolt" \
    "${REPOSITORY}/scripts/optimization/bolt"
cp -a -- "${SOURCE_ROOT}/scripts/optimization/pgo" \
    "${REPOSITORY}/scripts/optimization/pgo"
install -m 0755 -T -- "${SOURCE_ROOT}/scripts/optimization/install-framework.sh" \
    "${REPOSITORY}/scripts/optimization/install-framework.sh"

# Exercise NUL-delimited discovery with names that are awkward but safe in a
# line-oriented canonical manifest.  Control characters are deliberately
# rejected by the installer.
printf 'unusual-name-fixture\n' >"${REPOSITORY}/local-overlay/metadata/a file with spaces"
printf 'leading-dash-fixture\n' >"${REPOSITORY}/local-overlay/metadata/--leading-dash"

git -C "${REPOSITORY}" init -q
git -C "${REPOSITORY}" config user.name 'Framework Installer Fixture'
git -C "${REPOSITORY}" config user.email framework-installer@example.invalid
git -C "${REPOSITORY}" add --all
git -C "${REPOSITORY}" commit -qm 'fixture baseline'

PROFILE=${TARGET}/var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm
mkdir -p -- "${PROFILE}"
printf 'ARCH="amd64"\n' >"${PROFILE}/make.defaults"

# Unsafe destination ancestors must fail before the lock or framework base is
# created.  The fixture root itself is the hermetic trust boundary.
UNSAFE=${WORK}/unsafe-target
mkdir -p -- "${UNSAFE}/usr"
chmod 0777 -- "${UNSAFE}/usr"
mkdir -p -- "${UNSAFE}/var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm"
printf 'ARCH="amd64"\n' >"${UNSAFE}/var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm/make.defaults"
expect_failure 'trusted ancestor is group/world-writable' \
    env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${UNSAFE}"
[[ ! -e ${UNSAFE}/run/lock/gentoo-optimization-framework-install.lock ]] || \
    fail 'unsafe-ancestor rejection occurred after lock mutation'
[[ ! -e ${UNSAFE}/var/lib/gentoo-optimization ]] || \
    fail 'unsafe-ancestor rejection occurred after framework mutation'

# A migration source outside the explicit checkout/managed allowlist is fatal.
mkdir -p -- "${TARGET}/etc"
ln -s -- /unmanaged/portage "${TARGET}/etc/portage"
expect_failure '/etc/portage points to an unmanaged migration source' run_installer
rm -f -- "${TARGET}/etc/portage"

run_installer >"${LOG}" 2>&1 || { sed -n '1,260p' "${LOG}" >&2; fail 'initial install failed'; }
grep -Fq 'PASS: root-owned Phase 2 framework install verified' "${LOG}" || \
    fail 'initial install lacks PASS evidence'
run_installer --check >"${LOG}" 2>&1 || { sed -n '1,260p' "${LOG}" >&2; fail 'strict check failed'; }

BASE=${TARGET}/var/lib/gentoo-optimization
CURRENT=${BASE}/framework-current
MANIFEST=${BASE}/state/project/phase-2-framework-install.manifest
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
[[ $(stat -c %a -- "${ACTIVE}/portage") == 755 ]] || fail 'Portage candidate root is not 0755'
[[ $(stat -c %a -- "${ACTIVE}/portage/make.conf") == 644 ]] || fail 'Portage config is not 0644'
[[ $(stat -c %a -- "${TARGET}/usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py") == 755 ]] || \
    fail 'installed executable helper is not 0755'
[[ $(find "${ACTIVE}/generated-policy" -mindepth 1 -maxdepth 1 -printf '%f\n') == .empty-v1 ]] || \
    fail 'generated policy is not rigorously empty'

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

# Every existing BOLT lock is held across publication.  A busy transaction
# fails before any reviewed input snapshot is made.
LOCK=${TARGET}/var/cache/gentoo-optimization/bolt/locks/busy.lock
: >"${LOCK}"
(
    exec 9<>"${LOCK}"
    flock 9
    printf ready >"${WORK}/bolt-lock-ready"
    sleep 20
) &
LOCK_PID=$!
wait_for_log_not_used=1
for unused in $(seq 1 100); do
    [[ -e ${WORK}/bolt-lock-ready ]] && break
    sleep 0.05
done
[[ -e ${WORK}/bolt-lock-ready ]] || fail 'BOLT lock holder did not start'
expect_failure 'an active BOLT transaction holds' run_installer --check
kill "${LOCK_PID}" 2>/dev/null || true
wait "${LOCK_PID}" 2>/dev/null || true
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
env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_PAUSE_AT=before-activation \
    GENTOO_OPT_INSTALLER_PAUSE_FILE="${PAUSE_FILE}" \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" >"${LOG}" 2>&1 &
INSTALL_PID=$!
wait_for_log 'PAUSE: before-activation'
kill -TERM "${INSTALL_PID}"
if wait "${INSTALL_PID}"; then fail 'signal-interrupted installer unexpectedly succeeded'; fi
rm -f -- "${PAUSE_FILE}"
grep -Fq 'ROLLBACK: restoring the pre-install framework' "${LOG}" || \
    fail 'signal interruption lacks explicit rollback evidence'
[[ $(readlink -- "${CURRENT}") == "${BASELINE_CURRENT}" ]] || fail 'signal interruption changed current'
assert_no_transaction_debris

# A source edit during the one-time snapshot is detected; no mixed candidate is
# allowed to escape.  This runs only in the copied fixture repository.
PAUSE_FILE=${WORK}/source-pause
: >"${PAUSE_FILE}"
: >"${LOG}"
env GENTOO_OPT_INSTALLER_TEST_MODE=1 \
    GENTOO_OPT_INSTALLER_PAUSE_AT=before-source-copy \
    GENTOO_OPT_INSTALLER_PAUSE_FILE="${PAUSE_FILE}" \
    bash -- "${REPOSITORY}/scripts/optimization/install-framework.sh" \
    --test-root "${TARGET}" >"${LOG}" 2>&1 &
INSTALL_PID=$!
wait_for_log 'PAUSE: before-source-copy'
printf '\n# concurrent fixture mutation\n' >>"${REPOSITORY}/portage/make.conf"
rm -f -- "${PAUSE_FILE}"
if wait "${INSTALL_PID}"; then fail 'mixed-source installer unexpectedly succeeded'; fi
grep -Fq 'reviewed inputs changed while the immutable source snapshot was created' "${LOG}" || {
    sed -n '1,240p' "${LOG}" >&2
    fail 'mixed-source failure lacks exact diagnostic'
}
git -C "${REPOSITORY}" checkout -q -- portage/make.conf
assert_no_transaction_debris

# Finally publish the dirty worktree identity successfully and prove its
# rollback link, then run the canonical byte-for-byte --check.
run_installer >/dev/null
NEW_ACTIVE=$(readlink -- "${CURRENT}")
[[ ${NEW_ACTIVE} != "${BASELINE_CURRENT}" ]] || fail 'changed worktree identity reused old generation'
grep -Fxq "previous_generation=${BASELINE_CURRENT}" "${MANIFEST}" || \
    fail 'new manifest omits exact rollback generation'
run_installer --check >/dev/null
assert_no_transaction_debris

printf 'PASS: framework installer snapshot/publication/rollback fixture\n'

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
    local needle=$1
    for _ in $(seq 1 100); do
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
mkdir -p -- "${TARGET}/usr/bin"
cp -- "$(command -v jq)" "${TARGET}/usr/bin/jq"
chmod 0755 -- "${TARGET}/usr/bin/jq"

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
printf 'ARCH="amd64"\n' >"${UNSAFE}/var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm/make.defaults"
mkdir -p -- "${UNSAFE}/usr/bin"
cp -- "$(command -v jq)" "${UNSAFE}/usr/bin/jq"
chmod 0755 -- "${UNSAFE}/usr/bin/jq"
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

# Repository-local Git configuration is controlled by the checkout owner.  The
# installer must override executable fsmonitor hooks, and production executes
# all Git inspection as that non-root owner rather than as root.
GIT_HELPER_MARKER=${WORK}/git-helper-executed
GIT_HELPER=${WORK}/malicious-fsmonitor
printf '#!/bin/sh\n: >%s\nexit 0\n' "${GIT_HELPER_MARKER}" >"${GIT_HELPER}"
chmod 0755 -- "${GIT_HELPER}"
git -C "${REPOSITORY}" config core.fsmonitor "${GIT_HELPER}"
run_installer >"${LOG}" 2>&1 || { sed -n '1,260p' "${LOG}" >&2; fail 'initial install failed'; }
grep -Fq 'PASS: root-owned Phase 2 framework install verified' "${LOG}" || \
    fail 'initial install lacks PASS evidence'
run_installer --check >"${LOG}" 2>&1 || { sed -n '1,260p' "${LOG}" >&2; fail 'strict check failed'; }
[[ ! -e ${GIT_HELPER_MARKER} ]] || fail 'installer executed repository-local Git helper'
git -C "${REPOSITORY}" config --unset core.fsmonitor

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
grep -Eq '^jq_sha256=[0-9a-f]{64}$' "${MANIFEST}" || fail 'manifest jq hash is invalid'
[[ $(stat -c %a -- "${ACTIVE}/portage") == 755 ]] || fail 'Portage candidate root is not 0755'
[[ $(stat -c %a -- "${ACTIVE}/portage/make.conf") == 644 ]] || fail 'Portage config is not 0644'
[[ $(stat -c %a -- "${TARGET}/usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py") == 755 ]] || \
    fail 'installed executable helper is not 0755'
[[ -x ${TARGET}/usr/local/libexec/gentoo-optimization/scripts/optimization/verify/reconcile-state.py ]] || \
    fail 'installed state reconciliation entry point is absent'
[[ -f ${TARGET}/usr/local/share/gentoo-optimization/schema/package-state.schema.json ]] || \
    fail 'installed package state schema is absent'
[[ $(stat -c '%g:%a' -- "${TARGET}/var/cache/gentoo-optimization/pgo") == "$(id -g):750" ]] || \
    fail 'validated PGO cache trust root mode differs'
[[ $(stat -c '%g:%a' -- "${TARGET}/var/tmp/gentoo-optimization/pgo-raw") == "$(id -g):755" ]] || \
    fail 'raw PGO spool trust root mode differs'
[[ $(stat -c %a -- "${TARGET}/var/lib/gentoo-optimization/generations") == 755 ]] || \
    fail 'generation trust root mode differs'
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
for _ in $(seq 1 100); do
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

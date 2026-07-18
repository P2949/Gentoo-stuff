#!/bin/bash
set -Eeuo pipefail
shopt -s inherit_errexit
shopt -u varredir_close
IFS=$'\n\t'
umask 077
export LC_ALL=C
export LANG=C LANGUAGE=C HOME=/root PATH=/usr/bin:/bin TZ=UTC
unset BASH_ENV CDPATH ENV GLOBIGNORE

# A supervised Phase 2 check may carry one bearer token.  Capture it before
# the first external command and immediately remove it from the exported
# environment.  The shell-only copies are cleared after the candidate-bound
# coordinator verifies the active transaction.
PROFILE_TRANSACTION_TOKEN=${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN-}
PROFILE_TRANSACTION_AUTHORIZATION=${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION-}
export -n PROFILE_TRANSACTION_TOKEN PROFILE_TRANSACTION_AUTHORIZATION
unset GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN \
    GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION

# This installer is the only reviewed bridge from the mutable checkout to the
# live, root-owned Phase 2 framework.  It snapshots every input once, builds and
# verifies one immutable candidate containing every mutable policy/helper/schema,
# and activates that whole candidate with one framework-current rename.  Fixed
# external entry points are generation-independent bootstraps or symlinks which
# always resolve through framework-current; they are never per-generation copies.

SELF_PATH=$(realpath -e -- "${BASH_SOURCE[0]}")
DEFAULT_SOURCE_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
MODE=install
TEST_ROOT=
GENERATED_POLICY_INPUT=
FROZEN_INVENTORY_INPUT=
SOURCE_ROOT_ARG=

usage() {
    cat <<EOF
Usage: ${0##*/} --source-root ABSOLUTE_PATH [--check]
       [--generated-policy-generation ABSOLUTE_PATH]
       [--frozen-inventory ABSOLUTE_PATH]

The following interface exists only for the hermetic repository fixture:
  GENTOO_OPT_INSTALLER_TEST_MODE=1 ${0##*/} [--check] --test-root ABSOLUTE_PATH
EOF
}

while (($#)); do
    case $1 in
        --check)
            MODE=check
            ;;
        --test-root)
            shift
            (($#)) || { usage >&2; exit 2; }
            TEST_ROOT=$1
            ;;
        --generated-policy-generation)
            shift
            (($#)) || { usage >&2; exit 2; }
            GENERATED_POLICY_INPUT=$1
            ;;
        --frozen-inventory)
            shift
            (($#)) || { usage >&2; exit 2; }
            FROZEN_INVENTORY_INPUT=$1
            ;;
        --source-root)
            shift
            (($#)) || { usage >&2; exit 2; }
            SOURCE_ROOT_ARG=$1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [[ -n ${TEST_ROOT} ]]; then
    [[ ${GENTOO_OPT_INSTALLER_TEST_MODE:-0} == 1 ]] || {
        printf 'ERROR: --test-root is restricted to the hermetic installer fixture\n' >&2
        exit 2
    }
    [[ ${TEST_ROOT} == /* && ${TEST_ROOT} != / && -d ${TEST_ROOT} && ! -L ${TEST_ROOT} ]] || {
        printf 'ERROR: test root must be an existing absolute non-symlink directory\n' >&2
        exit 2
    }
    TEST_ROOT=$(realpath -e -- "${TEST_ROOT}")
else
    ((EUID == 0)) || {
        printf 'ERROR: framework installation/check requires root\n' >&2
        exit 1
    }
    [[ -z ${GENTOO_OPT_INSTALLER_FAIL_AT:-}${GENTOO_OPT_INSTALLER_PAUSE_AT:-}${GENTOO_OPT_INSTALLER_FORCE_EXCHANGE_UNSUPPORTED:-} ]] || {
        printf 'ERROR: failure injection is forbidden outside --test-root\n' >&2
        exit 2
    }
fi

if [[ -n ${GENERATED_POLICY_INPUT} && ${GENERATED_POLICY_INPUT} != /* ]]; then
    printf 'ERROR: generated policy generation path must be absolute\n' >&2
    exit 2
fi
if [[ -n ${FROZEN_INVENTORY_INPUT} && ${FROZEN_INVENTORY_INPUT} != /* ]]; then
    printf 'ERROR: frozen inventory path must be absolute\n' >&2
    exit 2
fi

if [[ -n ${SOURCE_ROOT_ARG} ]]; then
    [[ ${SOURCE_ROOT_ARG} == /* && -d ${SOURCE_ROOT_ARG} ]] || {
        printf 'ERROR: --source-root must name an existing absolute directory\n' >&2
        exit 2
    }
    ROOT=$(realpath -e -- "${SOURCE_ROOT_ARG}")
else
    [[ -n ${TEST_ROOT} ]] || {
        printf 'ERROR: production invocation requires --source-root\n' >&2
        exit 2
    }
    ROOT=${DEFAULT_SOURCE_ROOT}
fi
readonly ROOT SELF_PATH

SOURCE_UID=$(stat -c %u -- "${ROOT}")
SOURCE_USER=$(getent passwd "${SOURCE_UID}" | awk -F: 'NR == 1 { print $1 }')
[[ -n ${SOURCE_USER} ]] || {
    printf 'ERROR: cannot resolve the source checkout owner (%s)\n' "${SOURCE_UID}" >&2
    exit 1
}
readonly SOURCE_UID SOURCE_USER

EXPECTED_UID=$EUID
EXPECTED_GID=$(id -g)
if [[ -n ${TEST_ROOT} ]]; then
    PORTAGE_GID=${EXPECTED_GID}
    LOCK_GID=${EXPECTED_GID}
    LOCK_DIRECTORY_MODE=0700
    LOCK_FILE_MODE=0600
else
    PORTAGE_GID=$(getent group portage | awk -F: 'NR == 1 { print $3 }')
    [[ ${PORTAGE_GID} =~ ^[1-9][0-9]*$ ]] || {
        printf 'ERROR: cannot resolve a nonzero Portage group identity\n' >&2
        exit 1
    }
    LOCK_GID=${PORTAGE_GID}
    LOCK_DIRECTORY_MODE=0750
    LOCK_FILE_MODE=0640
fi
readonly LOCK_GID LOCK_DIRECTORY_MODE LOCK_FILE_MODE

physical() {
    local logical=$1
    [[ ${logical} == /* ]] || return 1
    if [[ -n ${TEST_ROOT} ]]; then
        printf '%s%s\n' "${TEST_ROOT}" "${logical}"
    else
        printf '%s\n' "${logical}"
    fi
}

BASE=$(physical /var/lib/gentoo-optimization)
FRAMEWORK_CURRENT=${BASE}/framework-current
ACTIVATION_JOURNAL=${BASE}/framework-activation.pending
STATE_ROOT=${BASE}/state/project
MANIFEST=${STATE_ROOT}/phase-2-framework-install.manifest
PROFILE_TRANSACTION_ROOT=${BASE}/state/profile-transactions
PROFILE_TRANSACTION_JOURNAL=${PROFILE_TRANSACTION_ROOT}/phase-2-production-profile-locks.pending
PROFILE_TRANSACTION_JOURNAL_PARTIAL=${PROFILE_TRANSACTION_JOURNAL}.partial
CACHE_ROOT=$(physical /var/cache/gentoo-optimization/bolt)
INSTALL_QA_ROOT=$(physical /usr/local/lib/install-qa-check.d)
LIBEXEC_ROOT=$(physical /usr/local/libexec/gentoo-optimization)
SHARE_ROOT=$(physical /usr/local/share/gentoo-optimization)
ETC_PORTAGE=$(physical /etc/portage)
LOCK_PATH=$(physical /run/gentoo-optimization/framework-install.lock)
PROJECT_LOCK_PATH=$(physical /run/gentoo-optimization/project.lock)
GENERATION_LOCK_PATH=$(physical /run/gentoo-optimization/generation.lock)
JQ_PATH=$(physical /usr/bin/jq)
GENERATIONS_ROOT=${BASE}/generations
PGO_CACHE=$(physical /var/cache/gentoo-optimization/pgo)
PGO_RAW=$(physical /var/tmp/gentoo-optimization/pgo-raw)
VAR_TMP_BOUNDARY=$(physical /var/tmp)
readonly BASE FRAMEWORK_CURRENT ACTIVATION_JOURNAL STATE_ROOT MANIFEST CACHE_ROOT INSTALL_QA_ROOT \
    LIBEXEC_ROOT SHARE_ROOT ETC_PORTAGE LOCK_PATH PROJECT_LOCK_PATH \
    GENERATION_LOCK_PATH JQ_PATH GENERATIONS_ROOT \
    PGO_CACHE PGO_RAW VAR_TMP_BOUNDARY PORTAGE_GID \
    PROFILE_TRANSACTION_ROOT PROFILE_TRANSACTION_JOURNAL \
    PROFILE_TRANSACTION_JOURNAL_PARTIAL

HOOK_BASENAME=zz-gentoo-optimization-bolt
readonly HOOK_BASENAME

declare -a INPUT_FILES=(
    scripts/optimization/install-framework.sh
    scripts/optimization/bolt/artifact_tool.py
    scripts/optimization/bolt/capture-input.sh
    scripts/optimization/bolt/deploy-output.sh
    scripts/optimization/bolt/register-output.sh
    scripts/optimization/pgo/profile-identity.py
    scripts/optimization/pgo/profile_locks.py
    scripts/optimization/pgo/validate-profile.py
    scripts/optimization/pgo/production-profile-lock-transaction.py
    scripts/optimization/pgo/authorization-token-scan.py
    scripts/optimization/lib/state.py
    scripts/optimization/verify/reconcile-state.py
    scripts/optimization/recovery/verify-binpkg-snapshot.py
    optimization/schema/package-state.schema.json
    optimization/schema/artifact-state.schema.json
    optimization/schema/final-system-state.schema.json
)
declare -a HELPER_RELATIVE=(
    bolt/artifact_tool.py
    bolt/capture-input.sh
    bolt/deploy-output.sh
    bolt/register-output.sh
    pgo/profile-identity.py
    pgo/profile_locks.py
    pgo/validate-profile.py
    pgo/production-profile-lock-transaction.py
    pgo/authorization-token-scan.py
    scripts/optimization/lib/state.py
    scripts/optimization/verify/reconcile-state.py
    recovery/verify-binpkg-snapshot.py
)
declare -a HELPER_SOURCE_RELATIVE=(
    scripts/optimization/bolt/artifact_tool.py
    scripts/optimization/bolt/capture-input.sh
    scripts/optimization/bolt/deploy-output.sh
    scripts/optimization/bolt/register-output.sh
    scripts/optimization/pgo/profile-identity.py
    scripts/optimization/pgo/profile_locks.py
    scripts/optimization/pgo/validate-profile.py
    scripts/optimization/pgo/production-profile-lock-transaction.py
    scripts/optimization/pgo/authorization-token-scan.py
    scripts/optimization/lib/state.py
    scripts/optimization/verify/reconcile-state.py
    scripts/optimization/recovery/verify-binpkg-snapshot.py
)
# Exact stable-bootstrap layout installed by the currently deployed
# pre-Candidate-A framework (Git commit
# 8a1200915d2693fd7486a421a9b232f638e9840c).  Its live manifest and fixed
# namespace contain these ten helpers.  It is accepted only as the source of
# this reviewed additive migration; the whole fixed tree is exchanged for the
# current layout while Portage is quiescent and all framework/project/
# generation locks are held.  The later, never-deployed twelve-helper hybrid
# from 19a46b78 is deliberately not an accepted migration source.
declare -ar LEGACY_BOOTSTRAP_HELPER_RELATIVE=(
    bolt/artifact_tool.py
    bolt/capture-input.sh
    bolt/deploy-output.sh
    bolt/register-output.sh
    pgo/profile-identity.py
    pgo/profile_locks.py
    pgo/validate-profile.py
    recovery/verify-binpkg-snapshot.py
    scripts/optimization/lib/state.py
    scripts/optimization/verify/reconcile-state.py
)

SNAPSHOT=
SOURCE_CONTRACT_TEMP=
METADATA_AUDIT_TEMP=
CANDIDATE_INVENTORY_TEMP=
RAW_HEAD_OID_LENGTH=
EXPECTED_CHECK_MANIFEST=
CANDIDATE_STAGE=
CANDIDATE_FINAL=
CREATED_CANDIDATE=0
COMMITTED=0
ROLLBACK_REQUIRED=0
PREVIOUS_TARGET=none
INSTALLER_LOCK_FD=
declare -a HELD_BOLT_LOCK_FDS=()
declare -a HELD_PROJECT_LOCK_FDS=()
declare -a EXCHANGE_PROBE_ROOTS=()
declare -A FROZEN_CPVS=()
FROZEN_INVENTORY_SHA256=none

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    return 1
}

# Git may honor executable helpers from repository-local configuration.  Never
# run it as root against a non-root-owned checkout: use the checkout owner and
# a minimal environment, and override the executable configuration surfaces
# needed by these read-only status/identity calls.
source_git() {
    local -a command=(
        /usr/bin/git
        -c core.fsmonitor=false
        -c core.hooksPath=/dev/null
        -c diff.external=
        -c pager.status=false
        -C "${ROOT}"
    )
    local -a environment=(
        /usr/bin/env -i
        HOME=/nonexistent
        LANG=C
        LC_ALL=C
        PATH=/usr/bin:/bin
        GIT_NO_REPLACE_OBJECTS=1
        GIT_CONFIG_NOSYSTEM=1
        GIT_CONFIG_GLOBAL=/dev/null
        GIT_OPTIONAL_LOCKS=0
    )
    if [[ -n ${TEST_ROOT} || ${SOURCE_UID} == "${EXPECTED_UID}" ]]; then
        "${environment[@]}" "${command[@]}" "$@"
    else
        /usr/bin/runuser -u "${SOURCE_USER}" -- \
            "${environment[@]}" "${command[@]}" "$@"
    fi
}

initialize_raw_git_identity() {
    local object_format
    object_format=$(source_git --no-replace-objects \
        rev-parse --show-object-format=storage) || \
        fail 'cannot determine the raw Git object format'
    case ${object_format} in
        sha1) RAW_HEAD_OID_LENGTH=40 ;;
        sha256) RAW_HEAD_OID_LENGTH=64 ;;
        *) fail "unsupported Git object format: ${object_format}" ;;
    esac
}

resolve_raw_head_commit() {
    local commit object_type
    if ! commit=$(source_git --no-replace-objects \
        rev-parse --verify 'HEAD^{commit}'); then
        fail 'raw HEAD does not resolve to a commit object'
        return 1
    fi
    [[ ${commit} =~ ^[0-9a-f]+$ && \
        ${#commit} == "${RAW_HEAD_OID_LENGTH}" ]] || {
        fail 'raw HEAD resolved to an invalid commit identity'
        return 1
    }
    if ! object_type=$(source_git --no-replace-objects cat-file -t "${commit}"); then
        fail 'cannot inspect the raw HEAD commit object'
        return 1
    fi
    [[ ${object_type} == commit ]] || {
        fail 'raw HEAD did not peel to a commit object'
        return 1
    }
    printf '%s\n' "${commit}"
}

mode_is_trusted() {
    local mode=$1
    # No behavior-affecting framework object may carry setuid, setgid, sticky,
    # or group/world-write bits.  The single reviewed exception is the exact
    # root-owned 01777 /var/tmp boundary handled by the ancestor walker.
    [[ ${mode} =~ ^[0-7]{3,4}$ ]] && (( (8#${mode} & 8#7022) == 0 ))
}

# Walk lexical components without realpath so an unsafe symlink is rejected,
# not followed.  Missing trailing components are permitted for preflight.
verify_existing_ancestor_chain() {
    local path=$1 current=/ remainder component uid mode
    [[ ${path} == /* ]] || fail "trusted path is not absolute: ${path}"
    if [[ -n ${TEST_ROOT} && (${path} == "${TEST_ROOT}" || ${path} == "${TEST_ROOT}"/*) ]]; then
        current=${TEST_ROOT}
        remainder=${path#"${TEST_ROOT}"/}
        [[ ${path} != "${TEST_ROOT}" ]] || remainder=
        uid=$(stat -c %u -- "${current}")
        mode=$(stat -c %a -- "${current}")
        [[ ${uid} == "${EXPECTED_UID}" ]] || fail "test trust root has the wrong owner: ${current}"
        mode_is_trusted "${mode}" || fail "test trust root is group/world-writable: ${current}"
    else
        [[ -d / && ! -L / ]] || fail 'filesystem root is not a non-symlink directory'
        uid=$(stat -c %u -- /)
        mode=$(stat -c %a -- /)
        [[ ${uid} == "${EXPECTED_UID}" ]] || fail 'filesystem root has the wrong owner'
        mode_is_trusted "${mode}" || fail 'filesystem root is group/world-writable'
        remainder=${path#/}
    fi
    while IFS= read -r component; do
        [[ -n ${component} ]] || continue
        if [[ ${current} == / ]]; then current=/${component}; else current=${current}/${component}; fi
        [[ -e ${current} || -L ${current} ]] || break
        [[ -d ${current} && ! -L ${current} ]] || \
            fail "trusted ancestor is not a non-symlink directory: ${current}"
        uid=$(stat -c %u -- "${current}")
        mode=$(stat -c %a -- "${current}")
        [[ ${uid} == "${EXPECTED_UID}" ]] || fail "trusted ancestor has the wrong owner: ${current}"
        if [[ ${current} == "${VAR_TMP_BOUNDARY}" && ${mode} == 1777 ]]; then
            continue
        fi
        mode_is_trusted "${mode}" || fail "trusted ancestor is group/world-writable: ${current}"
    done < <(/usr/bin/tr '/' '\n' <<<"${remainder}")
}

verify_regular_trusted() {
    local path=$1 expected_mode=${2:-} uid gid mode
    [[ -f ${path} && ! -L ${path} ]] || fail "expected a regular non-symlink file: ${path}"
    uid=$(stat -c %u -- "${path}")
    gid=$(stat -c %g -- "${path}")
    mode=$(stat -c %a -- "${path}")
    [[ ${uid}:${gid} == "${EXPECTED_UID}:${EXPECTED_GID}" ]] || \
        fail "file ownership is not trusted: ${path}"
    mode_is_trusted "${mode}" || fail "file is group/world-writable: ${path}"
    [[ -z ${expected_mode} || 0${mode} == "${expected_mode}" ]] || \
        fail "file mode is ${mode}, expected ${expected_mode}: ${path}"
}

verify_candidate_portage_readable() {
    local path=$1 expected_gid=${EXPECTED_GID} expected_mode=0600 uid gid mode nlink
    if [[ -z ${TEST_ROOT} ]]; then
        expected_gid=${PORTAGE_GID}
        expected_mode=0640
    fi
    [[ -f ${path} && ! -L ${path} ]] || \
        fail "expected a regular candidate evidence file: ${path}"
    uid=$(stat -c %u -- "${path}")
    gid=$(stat -c %g -- "${path}")
    mode=$(stat -c %a -- "${path}")
    nlink=$(stat -c %h -- "${path}")
    [[ ${uid}:${gid}:${mode}:${nlink} == \
        "${EXPECTED_UID}:${expected_gid}:${expected_mode#0}:1" ]] || \
        fail "candidate evidence is not trusted Portage-readable data: ${path}"
}

verify_directory() {
    local path=$1 uid=$2 gid=$3 expected_mode=$4
    [[ -d ${path} && ! -L ${path} ]] || fail "expected a non-symlink directory: ${path}"
    [[ $(stat -c '%u:%g:%a' -- "${path}") == "${uid}:${gid}:${expected_mode#0}" ]] || \
        fail "directory ownership/mode differs from ${uid}:${gid}:${expected_mode}: ${path}"
}

verify_lock_file() {
    local path=$1
    [[ -f ${path} && ! -L ${path} ]] || fail "expected a regular non-symlink lock file: ${path}"
    [[ $(stat -c '%u:%g:%a:%h' -- "${path}") == \
        "${EXPECTED_UID}:${LOCK_GID}:${LOCK_FILE_MODE#0}:1" ]] || \
        fail "lock ownership/mode/link-count differs from ${EXPECTED_UID}:${LOCK_GID}:${LOCK_FILE_MODE}:1: ${path}"
}

create_lock_if_absent() {
    local path=$1
    if [[ ! -e ${path} && ! -L ${path} ]]; then
        if ! (umask 077; set -o noclobber; : >"${path}") 2>/dev/null; then
            [[ -e ${path} || -L ${path} ]] || \
                fail "cannot atomically create lock file: ${path}"
        else
            chown "${EXPECTED_UID}:${LOCK_GID}" -- "${path}"
            chmod "${LOCK_FILE_MODE}" -- "${path}"
            sync_path "${path}"
            sync_path "${path%/*}"
        fi
    fi
    verify_lock_file "${path}"
}

open_verified_lock_descriptor() {
    local path=$1 destination=$2 opened_fd before opened after
    verify_lock_file "${path}"
    before=$(stat -c '%d:%i:%u:%g:%a:%h' -- "${path}")
    exec {opened_fd}<>"${path}"
    opened=$(stat -Lc '%d:%i:%u:%g:%a:%h' -- "/proc/${BASHPID}/fd/${opened_fd}")
    after=$(stat -c '%d:%i:%u:%g:%a:%h' -- "${path}")
    if ! [[ ${before} == "${opened}" && ${before} == "${after}" ]]; then
        exec {opened_fd}>&-
        fail "lock identity changed while it was opened: ${path}"
    fi
    printf -v "${destination}" '%s' "${opened_fd}"
}

verify_runtime_namespaces() {
    local directory
    verify_directory "${LOCK_PATH%/*}" "${EXPECTED_UID}" "${LOCK_GID}" "${LOCK_DIRECTORY_MODE}"
    verify_lock_file "${LOCK_PATH}"
    verify_lock_file "${PROJECT_LOCK_PATH}"
    verify_lock_file "${GENERATION_LOCK_PATH}"
    verify_directory "${GENERATIONS_ROOT}" "${EXPECTED_UID}" "${EXPECTED_GID}" 0755
    verify_directory "${PROFILE_TRANSACTION_ROOT}" "${EXPECTED_UID}" \
        "${LOCK_GID}" "${LOCK_DIRECTORY_MODE}"
    verify_directory "${CACHE_ROOT}" "${EXPECTED_UID}" "${EXPECTED_GID}" 0700
    for directory in inputs outputs perf fdata diagnostics locks; do
        verify_directory "${CACHE_ROOT}/${directory}" "${EXPECTED_UID}" "${EXPECTED_GID}" 0700
    done
    verify_directory "${PGO_CACHE}" "${EXPECTED_UID}" "${PORTAGE_GID}" 0750
    for directory in clang-ir clang-sample ebuild-native gcc go rust; do
        verify_directory "${PGO_CACHE}/${directory}" "${EXPECTED_UID}" "${PORTAGE_GID}" 0750
    done
    verify_directory "${PGO_RAW}" "${EXPECTED_UID}" "${EXPECTED_GID}" 0755
}

verify_jq() {
    verify_existing_ancestor_chain "${JQ_PATH%/*}"
    verify_regular_trusted "${JQ_PATH}" 0755
    JQ_SHA256=$(sha256sum -- "${JQ_PATH}"); JQ_SHA256=${JQ_SHA256%% *}
    JQ_VERSION=$("${JQ_PATH}" --version)
    [[ ${JQ_VERSION} =~ ^jq-[0-9][A-Za-z0-9.+_-]*$ ]] || \
        fail "jq reported an invalid version identity: ${JQ_VERSION}"
}

verify_profile_transaction_authorization() {
    local token=${PROFILE_TRANSACTION_TOKEN-}
    local authorization=${PROFILE_TRANSACTION_AUTHORIZATION-}
    local helper=${LIBEXEC_ROOT}/pgo/production-profile-lock-transaction.py
    local child_identity=${PROFILE_TRANSACTION_JOURNAL}.child.json
    local -a arguments=(verify-active)
    PROFILE_TRANSACTION_TOKEN=
    PROFILE_TRANSACTION_AUTHORIZATION=
    if [[ -e ${PROFILE_TRANSACTION_JOURNAL_PARTIAL} || \
          -L ${PROFILE_TRANSACTION_JOURNAL_PARTIAL} || \
          -e ${child_identity}.partial || -L ${child_identity}.partial ]]; then
        fail 'production profile-lock transaction publication is incomplete'
    fi
    if [[ ! -e ${PROFILE_TRANSACTION_JOURNAL} && \
          ! -L ${PROFILE_TRANSACTION_JOURNAL} ]]; then
        [[ ! -e ${child_identity} && ! -L ${child_identity} ]] || \
            fail 'orphan production profile-lock child identity is pending'
        [[ -z ${token}${authorization} ]] || \
            fail 'stale production profile transaction authorization is present without a journal'
        PROFILE_TRANSACTION_TOKEN=
        PROFILE_TRANSACTION_AUTHORIZATION=
        return 0
    fi
    [[ ${MODE} == check ]] || \
        fail "production profile-lock transaction journal is pending: ${PROFILE_TRANSACTION_JOURNAL}"
    [[ ${token} =~ ^[0-9a-f]{64}$ && -n ${authorization} ]] || \
        fail 'pending production profile transaction lacks the exact coordinator token'
    if [[ -n ${TEST_ROOT} ]]; then
        arguments+=(
            --test-mode
            --test-root "${TEST_ROOT}"
            --test-framework-lock "${LOCK_PATH}"
            --test-project-lock "${PROJECT_LOCK_PATH}"
            --test-generation-lock "${GENERATION_LOCK_PATH}"
            --test-journal "${PROFILE_TRANSACTION_JOURNAL}"
        )
    fi
    verify_regular_trusted "${helper}" 0755
    arguments+=(--token-fd 0 --authorization "${authorization}")
    printf '%s\n' "${token}" | "${helper}" "${arguments[@]}" >/dev/null || \
        fail 'candidate-bound coordinator rejected the active profile transaction'
    PROFILE_TRANSACTION_TOKEN=
    PROFILE_TRANSACTION_AUTHORIZATION=
}

preflight_destination_ancestors() {
    local path
    for path in \
        "${BASE}" "${STATE_ROOT}" "${PROFILE_TRANSACTION_ROOT}" "${CACHE_ROOT}" \
        "${INSTALL_QA_ROOT}" "${LIBEXEC_ROOT}" "${LOCK_PATH%/*}" \
        "${SHARE_ROOT%/*}" "${ETC_PORTAGE%/*}" "${GENERATIONS_ROOT}" \
        "${PGO_CACHE}" "${PGO_RAW}" "${JQ_PATH%/*}" "${BASE}/bootstrap"; do
        verify_existing_ancestor_chain "${path}"
    done
}

nearest_existing_destination_ancestor() {
    local path=$1
    while [[ ! -e ${path} && ! -L ${path} ]]; do
        [[ ${path} != / ]] || break
        path=${path%/*}
        [[ -n ${path} ]] || path=/
    done
    [[ -d ${path} && ! -L ${path} ]] || \
        fail "atomic-exchange destination ancestor is not a real directory: ${path}"
    printf '%s\n' "${path}"
}

preflight_atomic_exchange_destinations() {
    local destination_parent existing probe error detail exchange_failed
    local -a destination_parents=(
        "${ETC_PORTAGE%/*}"
        "${LIBEXEC_ROOT%/*}"
        "${SHARE_ROOT%/*}"
        "${INSTALL_QA_ROOT}"
        "${MANIFEST%/*}"
    )
    [[ -x /usr/bin/mv ]] || fail 'required atomic-exchange tool is absent: /usr/bin/mv'
    for destination_parent in "${destination_parents[@]}"; do
        existing=$(nearest_existing_destination_ancestor "${destination_parent}")
        probe=$(mktemp -d \
            "${existing}/.gentoo-optimization-rename-exchange.XXXXXXXX") || \
            fail "cannot create atomic-exchange probe for destination parent: ${destination_parent}"
        EXCHANGE_PROBE_ROOTS+=("${probe}")
        mkdir -- "${probe}/left" "${probe}/right"
        printf 'left\n' >"${probe}/left/identity"
        printf 'right\n' >"${probe}/right/identity"
        error=${probe}/exchange.stderr
        exchange_failed=0
        if [[ -n ${TEST_ROOT} && \
            ${GENTOO_OPT_INSTALLER_FORCE_EXCHANGE_UNSUPPORTED:-0} == 1 ]]; then
            printf 'forced unsupported exchange for fixture validation\n' >"${error}"
            exchange_failed=1
        elif ! /usr/bin/mv --exchange --no-copy -T -- \
            "${probe}/left" "${probe}/right" 2>"${error}"; then
            exchange_failed=1
        fi
        if ((exchange_failed)); then
            detail=$(/usr/bin/tr '\n' ' ' <"${error}" 2>/dev/null || true)
            rm -rf -- "${probe}"
            fail "destination filesystem does not support atomic exchange: ${destination_parent}${detail:+ (${detail})}"
        fi
        [[ $(<"${probe}/left/identity") == right && \
            $(<"${probe}/right/identity") == left ]] || {
            rm -rf -- "${probe}"
            fail "destination filesystem returned invalid atomic-exchange semantics: ${destination_parent}"
        }
        rm -rf -- "${probe}"
        printf 'PREFLIGHT: atomic exchange destination_parent=%s probe_ancestor=%s device=%s\n' \
            "${destination_parent}" "${existing}" "$(stat -c %d -- "${existing}")"
    done
    EXCHANGE_PROBE_ROOTS=()
}

open_project_lock() {
    local path=$1 mode=$2
    if [[ ${MODE} == install ]]; then
        create_lock_if_absent "${path}"
    else
        verify_lock_file "${path}"
    fi
    local descriptor
    open_verified_lock_descriptor "${path}" descriptor
    if [[ ${mode} == exclusive ]]; then
        flock -n -x "${descriptor}" || fail "cannot acquire exclusive project lock: ${path}"
    else
        flock -n -s "${descriptor}" || fail "cannot acquire shared project lock: ${path}"
    fi
    HELD_PROJECT_LOCK_FDS+=("${descriptor}")
}

verify_bootstrap_identity() {
    local expected=${BASE}/bootstrap/install-framework.sh
    [[ -n ${TEST_ROOT} ]] && return 0
    [[ ${SELF_PATH} == "${expected}" ]] || \
        fail "production installer must execute the root-owned bootstrap copy: ${expected}"
    verify_existing_ancestor_chain "${SELF_PATH%/*}"
    verify_regular_trusted "${SELF_PATH}" 0755
}

safe_mkdir() {
    local mode=$1 path=$2
    verify_existing_ancestor_chain "${path}"
    install -d -o "${EXPECTED_UID}" -g "${EXPECTED_GID}" -m "${mode}" -- "${path}"
}

safe_mkdir_owner() {
    local mode=$1 uid=$2 gid=$3 path=$4
    verify_existing_ancestor_chain "${path}"
    install -d -o "${uid}" -g "${gid}" -m "${mode}" -- "${path}"
}

sync_path() {
    sync -f -- "$1"
}

sync_tree() {
    local root=$1 entry
    while IFS= read -r -d '' entry; do
        [[ -L ${entry} ]] || sync_path "${entry}"
    done < <(find "${root}" -depth -print0)
    sync_path "${root%/*}"
}

reject_control_name() {
    local value=$1 label=$2
    [[ ! ${value} =~ [[:cntrl:]] ]] || fail "${label} contains a control byte"
}

read_exact_symlink_target() {
    local path=$1 destination=$2 value=''
    [[ -L ${path} ]] || fail "expected a symlink: ${path}"
    IFS= read -r -d '' value < <(readlink -z -- "${path}") || \
        fail "cannot read exact NUL-terminated symlink target: ${path}"
    reject_control_name "${value}" 'symlink target'
    printf -v "${destination}" '%s' "${value}"
}

emit_tree_inventory() {
    local tree=$1 prefix=$2 exclude_one=${3:-} exclude_two=${4:-}
    local entry relative mode digest target
    while IFS= read -r -d '' entry; do
        relative=${entry#"${tree}/"}
        [[ ${relative} != "${entry}" ]] || continue
        [[ ${relative} != "${exclude_one}" && ${relative} != "${exclude_two}" ]] || continue
        reject_control_name "${relative}" 'relative path'
        if [[ -L ${entry} ]]; then
            target=
            read_exact_symlink_target "${entry}" target
            printf 'l\t-\t-\t%s/%s\t%s\n' "${prefix}" "${relative}" "${target}"
        elif [[ -d ${entry} ]]; then
            printf 'd\t0755\t-\t%s/%s\t-\n' "${prefix}" "${relative}"
        elif [[ -f ${entry} ]]; then
            if [[ -x ${entry} ]]; then mode=0755; else mode=0644; fi
            digest=$(sha256sum -- "${entry}"); digest=${digest%% *}
            printf 'f\t%s\t%s\t%s/%s\t-\n' "${mode}" "${digest}" "${prefix}" "${relative}"
        else
            fail "unsupported filesystem object in source tree: ${entry}"
        fi
    done < <(find "${tree}" -mindepth 1 -print0 | sort -z)
}

emit_source_inventory() {
    local source_root=$1 relative file mode digest
    emit_tree_inventory "${source_root}/portage" portage
    emit_tree_inventory "${source_root}/local-overlay" local-overlay
    for relative in "${INPUT_FILES[@]}"; do
        file=${source_root}/${relative}
        [[ -f ${file} && ! -L ${file} ]] || fail "source input is not a regular file: ${relative}"
        reject_control_name "${relative}" 'relative path'
        if [[ -x ${file} ]]; then mode=0755; else mode=0644; fi
        digest=$(sha256sum -- "${file}"); digest=${digest%% *}
        printf 'f\t%s\t%s\t%s\t-\n' "${mode}" "${digest}" "${relative}"
    done
}

source_identity() {
    local source_root=$1
    emit_source_inventory "${source_root}" | sha256sum | awk '{print $1}'
}

verify_source_entry_trust() {
    local entry=$1 label=$2 uid mode nlink
    [[ -e ${entry} || -L ${entry} ]] || fail "${label} is absent: ${entry}"
    uid=$(stat -c %u -- "${entry}")
    [[ ${uid} == "${SOURCE_UID}" || ${uid} == "${EXPECTED_UID}" ]] || \
        fail "${label} has an untrusted owner: ${entry}"
    if [[ -L ${entry} ]]; then
        return 0
    fi
    mode=$(stat -c %a -- "${entry}")
    mode_is_trusted "${mode}" || fail "${label} mode is unsafe: ${entry} (${mode})"
    if [[ -f ${entry} ]]; then
        nlink=$(stat -c %h -- "${entry}")
        [[ ${nlink} == 1 ]] || fail "${label} is not a single-link regular file: ${entry}"
    elif [[ ! -d ${entry} ]]; then
        fail "${label} is not a regular file, directory, or symlink: ${entry}"
    fi
}

verify_source_parent_chain() {
    local relative=$1 current=${ROOT} component
    local -a components=()
    IFS=/ read -r -a components <<<"${relative%/*}"
    verify_source_entry_trust "${current}" 'framework source root'
    for component in "${components[@]}"; do
        [[ -n ${component} && ${component} != . && ${component} != .. ]] || \
            fail "framework source input has an unsafe parent component: ${relative}"
        current=${current}/${component}
        [[ -d ${current} && ! -L ${current} ]] || \
            fail "framework source input parent is not a real directory: ${current}"
        verify_source_entry_trust "${current}" 'framework source input parent'
    done
}

verify_source_filesystem_trust() {
    local entry relative path_list=${SOURCE_CONTRACT_TEMP}/source-paths
    verify_source_entry_trust "${ROOT}" 'framework source root'
    find "${ROOT}/portage" "${ROOT}/local-overlay" -print0 >"${path_list}" || \
        fail 'cannot enumerate framework source trust metadata'
    while IFS= read -r -d '' entry; do
        verify_source_entry_trust "${entry}" 'framework source input'
    done <"${path_list}"
    for relative in "${INPUT_FILES[@]}"; do
        verify_source_parent_chain "${relative}"
        entry=${ROOT}/${relative}
        [[ -f ${entry} && ! -L ${entry} ]] || \
            fail "framework explicit source input is not a regular file: ${relative}"
        verify_source_entry_trust "${entry}" 'framework source input'
    done
}

raw_head_path_is_input_ancestor() {
    local candidate=$1 relative parent
    for relative in "${INPUT_FILES[@]}"; do
        parent=${relative%/*}
        while [[ ${parent} == */* || -n ${parent} ]]; do
            [[ ${candidate} != "${parent}" ]] || return 0
            [[ ${parent} == */* ]] || break
            parent=${parent%/*}
        done
    done
    return 1
}

render_raw_head_entry() {
    local path=$1 mode=$2 type=$3 oid=$4 digest target
    local blob=${SOURCE_CONTRACT_TEMP}/raw-head.blob
    local scrubbed=${SOURCE_CONTRACT_TEMP}/raw-head.blob.scrubbed
    case ${mode}:${type} in
        040000:tree)
            printf 'd\t0755\t-\t%s\t-\n' "${path}"
            ;;
        100644:blob|100755:blob)
            source_git --no-replace-objects cat-file blob "${oid}" >"${blob}" || \
                fail "cannot read raw HEAD blob: ${path}"
            digest=$(sha256sum -- "${blob}"); digest=${digest%% *}
            if [[ ${mode} == 100755 ]]; then mode=0755; else mode=0644; fi
            printf 'f\t%s\t%s\t%s\t-\n' "${mode}" "${digest}" "${path}"
            ;;
        120000:blob)
            source_git --no-replace-objects cat-file blob "${oid}" >"${blob}" || \
                fail "cannot read raw HEAD symlink blob: ${path}"
            [[ -s ${blob} ]] || fail "raw HEAD symlink target is empty: ${path}"
            LC_ALL=C /usr/bin/tr -d '\000-\037\177' <"${blob}" >"${scrubbed}"
            cmp -s -- "${blob}" "${scrubbed}" || \
                fail "raw HEAD symlink target contains a control byte: ${path}"
            target=$(<"${blob}")
            [[ ${#target} == $(stat -c %s -- "${blob}") ]] || \
                fail "raw HEAD symlink target cannot be represented exactly: ${path}"
            printf 'l\t-\t-\t%s\t%s\n' "${path}" "${target}"
            ;;
        *)
            fail "unsupported raw HEAD mode/type ${mode}:${type}: ${path}"
            ;;
    esac
}

emit_raw_head_inventory() {
    local raw_commit=$1
    local stream=${SOURCE_CONTRACT_TEMP}/raw-head.ls-tree
    local portage_records=${SOURCE_CONTRACT_TEMP}/raw-head.portage
    local overlay_records=${SOURCE_CONTRACT_TEMP}/raw-head.local-overlay
    local record header path mode type oid extra input_index index
    local previous='' root_portage=0 root_overlay=0
    local -a input_modes=() input_types=() input_oids=()
    : >"${portage_records}"
    : >"${overlay_records}"
    source_git --no-replace-objects --literal-pathspecs \
        ls-tree -r -z -t --full-tree "${raw_commit}" -- \
        portage local-overlay "${INPUT_FILES[@]}" >"${stream}" || \
        fail 'cannot enumerate the raw HEAD commit tree'
    [[ -s ${stream} ]] || fail 'raw HEAD commit tree selection is empty'
    while IFS= read -r -d '' record; do
        [[ ${record} == *$'\t'* ]] || fail 'raw HEAD contains a malformed ls-tree record'
        header=${record%%$'\t'*}
        path=${record#*$'\t'}
        mode=''; type=''; oid=''; extra=''
        IFS=' ' read -r mode type oid extra <<<"${header}"
        [[ -z ${extra} && ${header} == "${mode} ${type} ${oid}" && \
            ${oid} =~ ^[0-9a-f]+$ && ${#oid} == "${RAW_HEAD_OID_LENGTH}" ]] || \
            fail "raw HEAD contains a malformed object identity: ${path:-unknown}"
        reject_control_name "${path}" 'raw HEAD path'
        [[ -n ${path} && ${path} != /* && ${path} != *//* ]] || \
            fail "raw HEAD contains an unsafe path: ${path}"
        case /${path}/ in
            */./*|*/../*) fail "raw HEAD contains an unsafe path component: ${path}" ;;
        esac
        case ${mode}:${type} in
            040000:tree|100644:blob|100755:blob|120000:blob) ;;
            *) fail "unsupported raw HEAD mode/type ${mode}:${type}: ${path}" ;;
        esac
        case ${path} in
            portage)
                [[ ${mode}:${type} == 040000:tree ]] || \
                    fail 'raw HEAD portage root is not a tree'
                root_portage=1
                ;;
            local-overlay)
                [[ ${mode}:${type} == 040000:tree ]] || \
                    fail 'raw HEAD local-overlay root is not a tree'
                root_overlay=1
                ;;
            portage/*)
                printf '%s\t%s\t%s\t%s\n' "${path}" "${mode}" "${type}" "${oid}" \
                    >>"${portage_records}"
                ;;
            local-overlay/*)
                printf '%s\t%s\t%s\t%s\n' "${path}" "${mode}" "${type}" "${oid}" \
                    >>"${overlay_records}"
                ;;
            *)
                input_index=-1
                for index in "${!INPUT_FILES[@]}"; do
                    if [[ ${path} == "${INPUT_FILES[index]}" ]]; then
                        input_index=${index}
                        break
                    fi
                done
                if ((input_index >= 0)); then
                    [[ ${mode}:${type} == 100644:blob || \
                        ${mode}:${type} == 100755:blob ]] || \
                        fail "raw HEAD explicit input is not a regular blob: ${path}"
                    [[ -z ${input_oids[input_index]+x} ]] || \
                        fail "raw HEAD repeats explicit input: ${path}"
                    input_modes[input_index]=${mode}
                    input_types[input_index]=${type}
                    input_oids[input_index]=${oid}
                elif raw_head_path_is_input_ancestor "${path}"; then
                    [[ ${mode}:${type} == 040000:tree ]] || \
                        fail "raw HEAD explicit-input ancestor is not a tree: ${path}"
                else
                    fail "raw HEAD selection contains an unexpected path: ${path}"
                fi
                ;;
        esac
    done <"${stream}"
    ((root_portage && root_overlay)) || fail 'raw HEAD lacks a required framework source root'

    for record in "${portage_records}" "${overlay_records}"; do
        sort -o "${record}" -- "${record}"
        previous=
        while IFS=$'\t' read -r path mode type oid extra; do
            [[ -z ${extra} && -n ${path} ]] || fail 'raw HEAD record serialization is malformed'
            [[ ${path} != "${previous}" ]] || fail "raw HEAD repeats path: ${path}"
            previous=${path}
            render_raw_head_entry "${path}" "${mode}" "${type}" "${oid}"
        done <"${record}"
    done
    for index in "${!INPUT_FILES[@]}"; do
        [[ -n ${input_oids[index]+x} ]] || \
            fail "raw HEAD lacks explicit input: ${INPUT_FILES[index]}"
        render_raw_head_entry "${INPUT_FILES[index]}" "${input_modes[index]}" \
            "${input_types[index]}" "${input_oids[index]}"
    done
}

verify_source_git_contract() {
    local raw_commit=$1
    # A clean Git status does not describe ignored files or empty directories;
    # index flags and repository-local core.fileMode can also hide byte/mode
    # changes. Read raw tree/blob objects with replacement objects disabled and
    # compare the exact normalized framework inventory, never archive
    # attributes, checkout filters, the index, or a configurable worktree diff.
    SOURCE_CONTRACT_TEMP=$(mktemp -d "${BASE}/.source-git-contract.XXXXXXXX")
    verify_source_filesystem_trust
    emit_source_inventory "${ROOT}" >"${SOURCE_CONTRACT_TEMP}/filesystem.inventory"
    emit_raw_head_inventory "${raw_commit}" >"${SOURCE_CONTRACT_TEMP}/head.inventory"
    if ! cmp -s -- "${SOURCE_CONTRACT_TEMP}/head.inventory" \
        "${SOURCE_CONTRACT_TEMP}/filesystem.inventory"; then
        fail 'source filesystem inventory differs from the raw HEAD commit tree'
        return 1
    fi

    # cp -a would otherwise carry checkout ACLs, capabilities, or user xattrs
    # into a candidate even though Git and the source identity do not bind
    # them.  No extended metadata is valid on a framework source input.
    verify_no_extended_metadata 'framework source input' \
        "${ROOT}/portage" "${ROOT}/local-overlay" \
        "${INPUT_FILES[@]/#/${ROOT}/}"
    rm -rf -- "${SOURCE_CONTRACT_TEMP}"
    SOURCE_CONTRACT_TEMP=
}

snapshot_frozen_inventory() {
    local before after cpv validation_json
    [[ -n ${FROZEN_INVENTORY_INPUT} ]] || \
        fail 'a nonempty generated policy requires --frozen-inventory'
    if [[ -z ${TEST_ROOT} ]]; then
        [[ ${FROZEN_INVENTORY_INPUT} == "${GENERATIONS_ROOT}"/*/frozen-inventory.json ]] || \
            fail 'frozen inventory must be the authoritative file inside a versioned generation'
    fi
    verify_existing_ancestor_chain "${FROZEN_INVENTORY_INPUT%/*}"
    verify_regular_trusted "${FROZEN_INVENTORY_INPUT}"
    before=$(sha256sum -- "${FROZEN_INVENTORY_INPUT}"); before=${before%% *}
    # Validate this data inside the already trusted bootstrap process.  Do not
    # execute a Python helper copied from the mutable source checkout before
    # publication.  This filter mirrors the strict state inventory contract:
    # exact keys, ordered unique packages/paths, exact CPVs/hashes, canonical
    # absolute paths, valid owners, and disjoint file/directory namespaces.
    # shellcheck disable=SC2016 # jq variables are intentionally single-quoted.
    validation_json=$("${JQ_PATH}" -ce --arg inventory_sha256 "${before}" '
        def safe_id:
            type == "string" and test("^[A-Za-z0-9+_.:@-]+$");
        def exact_cpv:
            type == "string" and
            test("^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+$");
        def sha256:
            type == "string" and test("^[0-9a-f]{64}$");
        def canonical_absolute:
            type == "string" and startswith("/") and . != "/" and
            (test("[\\u0000-\\u001f]") | not) and
            (contains("//") | not) and
            (test("/(?:[.]{1,2})(?:/|$)") | not) and
            (endswith("/") | not);
        if (
        keys == ["generation_id", "inventory_id", "owned_directories",
                 "owned_paths", "packages", "record_type", "schema_version"] and
        .schema_version == 2 and .record_type == "frozen-inventory" and
        (.generation_id | safe_id) and (.inventory_id | safe_id) and
        (.packages | type == "array" and length > 0) and
        all(.packages[];
            keys == ["cpv", "entry_sha256"] and
            (.cpv | exact_cpv) and (.entry_sha256 | sha256)) and
        ([.packages[].cpv] as $cpvs |
            $cpvs == ($cpvs | sort) and
            ($cpvs | length) == ($cpvs | unique | length) and
            all(.owned_paths[], .owned_directories[];
                . as $entry |
                keys == ["owner_cpv", "path"] and
                (.owner_cpv | exact_cpv) and
                (.path | canonical_absolute) and
                ($cpvs | index($entry.owner_cpv)) != null)) and
        (.owned_paths | type == "array") and
        (.owned_directories | type == "array") and
        ([.owned_paths[] | [.owner_cpv, .path]] as $paths |
         [.owned_directories[] | [.owner_cpv, .path]] as $directories |
            $paths == ($paths | sort) and
            ($paths | length) == ($paths | unique | length) and
            $directories == ($directories | sort) and
            ($directories | length) == ($directories | unique | length) and
            all($directories[]; . as $directory | ($paths | index($directory)) == null))
        ) then
        {
            cpvs: [.packages[].cpv],
            generation_id: .generation_id,
            inventory_id: .inventory_id,
            inventory_sha256: $inventory_sha256,
            owned_directory_count: (.owned_directories | length),
            owned_path_count: (.owned_paths | length),
            package_count: (.packages | length)
        }
        else error("frozen inventory violates the strict semantic contract") end
    ' "${FROZEN_INVENTORY_INPUT}") || \
        fail 'strict frozen-inventory semantic validation failed'
    "${JQ_PATH}" -e '
        keys == ["cpvs", "generation_id", "inventory_id", "inventory_sha256",
                 "owned_directory_count", "owned_path_count", "package_count"] and
        (.inventory_sha256 | test("^[0-9a-f]{64}$")) and
        (.generation_id | type == "string" and length > 0) and
        (.inventory_id | type == "string" and length > 0) and
        (.package_count | type == "number") and
        (.owned_path_count | type == "number") and
        (.owned_directory_count | type == "number") and
        (.cpvs | type == "array") and (.package_count == (.cpvs | length))
    ' <<<"${validation_json}" >/dev/null || \
        fail 'frozen-inventory validator returned an invalid summary'
    [[ ${before} == "$("${JQ_PATH}" -r '.inventory_sha256' <<<"${validation_json}")" ]] || \
        fail 'frozen inventory changed after strict semantic validation'
    FROZEN_CPVS=()
    while IFS= read -r cpv; do
        [[ -z ${FROZEN_CPVS["${cpv}"]+x} ]] || \
            fail "frozen inventory repeats CPV: ${cpv}"
        FROZEN_CPVS["${cpv}"]=1
    done < <("${JQ_PATH}" -r '.cpvs[]' <<<"${validation_json}")
    ((${#FROZEN_CPVS[@]} > 0)) || fail 'frozen inventory has no package entries'
    cp -- "${FROZEN_INVENTORY_INPUT}" "${SNAPSHOT}/frozen-inventory.json"
    after=$(sha256sum -- "${FROZEN_INVENTORY_INPUT}"); after=${after%% *}
    [[ ${before} == "${after}" && \
        ${before} == "$(sha256sum -- "${SNAPSHOT}/frozen-inventory.json" | awk '{print $1}')" ]] || \
        fail 'frozen inventory changed while it was snapshotted'
    FROZEN_INVENTORY_SHA256=${before}
}

validate_generated_policy_grammar() {
    local source=$1 line atom environment extra basename file variable value cpv
    local atom_re='^[A-Za-z0-9+_.~=@<>,*:-]+/[A-Za-z0-9+_.~=@<>,*:-]+$'
    local -A pairs=() referenced=() files=() variables=()
    local -a top_entries=()
    mapfile -t top_entries < <(
        find "${source}" -mindepth 1 -maxdepth 1 -printf '%y\t%f\n' | sort
    )
    [[ ${top_entries[*]} == $'d\tenv\nf\tpackage.env' ]] || \
        fail 'generated policy top-level entry set must be exactly package.env and env/'
    while IFS= read -r line || [[ -n ${line} ]]; do
        [[ ${line} =~ ^[[:space:]]*$ || ${line} =~ ^[[:space:]]*# ]] && continue
        atom='' environment='' extra=''
        IFS=$' \t' read -r atom environment extra <<<"${line}"
        [[ -n ${atom} && -n ${environment} && -z ${extra} ]] || \
            fail "generated package.env line is not exactly ATOM ENVIRONMENT: ${line}"
        [[ ${atom} =~ ${atom_re} ]] || \
            fail "generated package.env atom contains unsafe syntax: ${atom}"
        [[ ${atom} == =* && ${atom} != *'*'* ]] || \
            fail "generated package.env atom is not an exact CPV: ${atom}"
        /usr/bin/python3 -I - "${atom}" <<'PY' >/dev/null 2>&1 || \
            fail "generated package.env atom is invalid: ${atom}"
import sys
from portage.dep import Atom
Atom(sys.argv[1])
PY
        cpv=${atom#=}
        cpv=${cpv%%:*}
        [[ -n ${FROZEN_CPVS["${cpv}"]+x} ]] || \
            fail "generated package.env atom is absent from the frozen inventory: ${atom}"
        if [[ -z ${TEST_ROOT} ]]; then
            [[ -n $(portageq match / "${atom}" 2>/dev/null) ]] || \
                fail "generated package.env atom does not match the live installed universe: ${atom}"
        fi
        [[ ${environment} =~ ^optimization/generated/([A-Za-z0-9][A-Za-z0-9_.-]*\.conf)$ ]] || \
            fail "generated environment path escapes optimization/generated: ${environment}"
        basename=${BASH_REMATCH[1]}
        [[ -z ${pairs["${atom}\t${environment}"]+x} ]] || \
            fail "duplicate generated package/environment pair: ${atom} ${environment}"
        pairs["${atom}\t${environment}"]=1
        referenced["${basename}"]=1
    done <"${source}/package.env"

    while IFS= read -r -d '' file; do
        basename=${file##*/}
        [[ ${basename} =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*\.conf$ ]] || \
            fail "generated environment has an unsafe filename: ${basename}"
        files["${basename}"]=1
        variables=()
        while IFS= read -r line || [[ -n ${line} ]]; do
            [[ ${line} =~ ^[[:space:]]*$ || ${line} =~ ^[[:space:]]*# ]] && continue
            [[ ${line} =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]] || \
                fail "generated environment is not assignment-only: ${basename}: ${line}"
            variable=${BASH_REMATCH[1]}
            value=${BASH_REMATCH[2]}
            case ${variable} in
                GENTOO_OPT_MODE|GENTOO_OPT_BOLT_STAGE|GENTOO_OPT_BOLT_GCC_READY|\
                GENTOO_OPT_PROFILE_MAP_READY|GENTOO_OPT_ABI|GENTOO_OPT_COMPILER_FAMILY|\
                GENTOO_OPT_FINGERPRINT_FILE|\
                GENTOO_OPT_IDENTITY_INPUT|GENTOO_OPT_PROFILE_MANIFEST|\
                GENTOO_OPT_PROFILE_PATH|GENTOO_OPT_PROFILE_METADATA|\
                GENTOO_OPT_RUST_TARGET|GENTOO_OPT_GO_MAIN_COUNT|GENTOO_OPT_GO_BINARY|\
                GENTOO_OPT_BOLT_CACHE_ROOT|GENTOO_OPT_BOLT_EXPECTED_ELIGIBLE_COUNT|\
                GENTOO_OPT_BOLT_ELIGIBILITY_PROOF) ;;
                *) fail "generated environment assigns a forbidden variable: ${variable}" ;;
            esac
            [[ -z ${variables["${variable}"]+x} ]] || \
                fail "generated environment repeats ${variable}: ${basename}"
            variables["${variable}"]=1
            [[ ${value} =~ ^[A-Za-z0-9_./:@,+%=-]*$ || \
                ${value} =~ ^\"[A-Za-z0-9_./:@,+%=-]*\"$ ]] || \
                fail "generated environment value contains shell syntax: ${basename}: ${line}"
        done <"${file}"
    done < <(find "${source}/env" -mindepth 1 -maxdepth 1 -type f -print0 | sort -z)
    [[ $(find "${source}/env" -mindepth 1 ! -type f -print -quit) == '' ]] || \
        fail 'generated env/ must contain regular files only at one level'
    for basename in "${!referenced[@]}"; do
        [[ -n ${files["${basename}"]+x} ]] || \
            fail "generated package.env references a missing environment: ${basename}"
    done
    for basename in "${!files[@]}"; do
        [[ -n ${referenced["${basename}"]+x} ]] || \
            fail "generated environment is unreferenced: ${basename}"
    done
}

generated_policy_identity() {
    local source=$1 basename expected entry uid mode calculated
    basename=${source##*/}
    [[ ${basename} =~ ^generated-policy-([0-9a-f]{64})$ ]] || \
        fail 'generated policy basename must be generated-policy-<sha256>'
    expected=${BASH_REMATCH[1]}
    [[ -d ${source} && ! -L ${source} && -f ${source}/package.env && \
        ! -L ${source}/package.env && -d ${source}/env && ! -L ${source}/env ]] || \
        fail 'generated policy requires regular package.env and non-symlink env/'
    validate_generated_policy_grammar "${source}"
    while IFS= read -r -d '' entry; do
        [[ -L ${entry} ]] && fail "generated policy contains a symlink: ${entry}"
        [[ -d ${entry} || -f ${entry} ]] || \
            fail "generated policy contains a special file: ${entry}"
        uid=$(stat -c %u -- "${entry}")
        mode=$(stat -c %a -- "${entry}")
        [[ ${uid} == "${EXPECTED_UID}" ]] || \
            fail "generated policy entry has the wrong owner: ${entry}"
        mode_is_trusted "${mode}" || \
            fail "generated policy entry is group/world-writable: ${entry}"
    done < <(find "${source}" -mindepth 1 -print0)
    calculated=$(emit_tree_inventory "${source}" generated-policy | sha256sum | awk '{print $1}')
    [[ ${calculated} == "${expected}" ]] || \
        fail "generated policy content hash ${calculated} differs from its versioned basename"
    printf '%s\n' "${calculated}"
}

snapshot_inputs() {
    local before after snapshot_identity relative source_status_after commit_after \
        generated_before generated_after snapshot_generated git_status_before \
        git_status_after
    initialize_raw_git_identity
    GIT_COMMIT=$(resolve_raw_head_commit)
    verify_source_git_contract "${GIT_COMMIT}"
    git_status_before=$(source_git status --porcelain=v1 --untracked-files=all \
        --ignore-submodules=none)
    SOURCE_STATUS=$(printf '%s' "${git_status_before}" | sha256sum | awk '{print $1}')
    GIT_DIRTY=clean
    [[ -z ${git_status_before} ]] || GIT_DIRTY=dirty
    [[ -n ${TEST_ROOT} || ${GIT_DIRTY} == clean ]] || \
        fail 'production framework publication requires a clean Git worktree'
    before=$(source_identity "${ROOT}")
    failure_point before-source-copy
    SNAPSHOT=$(mktemp -d "${BASE}/.framework-source-snapshot.XXXXXXXX")
    mkdir -p -- "${SNAPSHOT}/scripts/optimization/bolt" \
        "${SNAPSHOT}/scripts/optimization/pgo" \
        "${SNAPSHOT}/scripts/optimization/lib" \
        "${SNAPSHOT}/scripts/optimization/verify" \
        "${SNAPSHOT}/scripts/optimization/recovery" \
        "${SNAPSHOT}/optimization/schema"
    cp -a -- "${ROOT}/portage" "${SNAPSHOT}/portage"
    cp -a -- "${ROOT}/local-overlay" "${SNAPSHOT}/local-overlay"
    for relative in "${INPUT_FILES[@]}"; do
        mkdir -p -- "${SNAPSHOT}/${relative%/*}"
        install -m "$(if [[ -x ${ROOT}/${relative} ]]; then printf 0755; else printf 0644; fi)" \
            -T -- "${ROOT}/${relative}" "${SNAPSHOT}/${relative}"
    done
    verify_source_git_contract "${GIT_COMMIT}"
    after=$(source_identity "${ROOT}")
    snapshot_identity=$(source_identity "${SNAPSHOT}")
    git_status_after=$(source_git status --porcelain=v1 --untracked-files=all \
        --ignore-submodules=none)
    source_status_after=$(printf '%s' "${git_status_after}" | sha256sum | awk '{print $1}')
    commit_after=$(resolve_raw_head_commit)
    [[ ${before} == "${after}" && ${before} == "${snapshot_identity}" ]] || \
        fail 'reviewed inputs changed while the immutable source snapshot was created'
    [[ ${SOURCE_STATUS} == "${source_status_after}" && ${GIT_COMMIT} == "${commit_after}" ]] || \
        fail 'Git commit/worktree identity changed while inputs were snapshotted'
    if [[ -n ${GENERATED_POLICY_INPUT} ]]; then
        snapshot_frozen_inventory
        verify_existing_ancestor_chain "${GENERATED_POLICY_INPUT}"
        generated_before=$(generated_policy_identity "${GENERATED_POLICY_INPUT}")
        cp -a -- "${GENERATED_POLICY_INPUT}" "${SNAPSHOT}/generated-policy"
        generated_after=$(generated_policy_identity "${GENERATED_POLICY_INPUT}")
        snapshot_generated=$(emit_tree_inventory "${SNAPSHOT}/generated-policy" \
            generated-policy | sha256sum | awk '{print $1}')
        [[ ${generated_before} == "${generated_after}" && \
            ${generated_before} == "${snapshot_generated}" ]] || \
            fail 'generated policy changed while it was snapshotted'
        GENERATED_POLICY_ID=${generated_before}
    else
        [[ -z ${FROZEN_INVENTORY_INPUT} ]] || \
            fail '--frozen-inventory is invalid without a generated policy generation'
        mkdir -p -- "${SNAPSHOT}/generated-policy/env"
        : >"${SNAPSHOT}/generated-policy/package.env"
        GENERATED_POLICY_ID=empty-v1
    fi
    SOURCE_AGGREGATE=$(printf '%s\n' "repository=${snapshot_identity}" \
        "generated_policy=${GENERATED_POLICY_ID}" | sha256sum | awk '{print $1}')
    INSTALLER_SHA256=$(sha256sum -- "${SNAPSHOT}/scripts/optimization/install-framework.sh")
    INSTALLER_SHA256=${INSTALLER_SHA256%% *}
    [[ ${INSTALLER_SHA256} == "$(sha256sum -- "${SELF_PATH}" | awk '{print $1}')" ]] || \
        fail 'root-owned bootstrap installer differs from the snapshotted source installer'
    emit_source_inventory "${SNAPSHOT}" >"${SNAPSHOT}/source.inventory"
    emit_tree_inventory "${SNAPSHOT}/generated-policy" generated-policy \
        >>"${SNAPSHOT}/source.inventory"
    chmod 0600 -- "${SNAPSHOT}/source.inventory"
}

discard_source_snapshot() {
    # Once an already-active candidate has been proven to match every reviewed
    # source identity, repair of its generation-independent bootstrap tree no
    # longer consumes the mutable snapshot.  Remove and fsync it before the
    # atomic helper exchange.  A SIGKILL after that exchange can therefore be
    # subjected immediately to the strict read-only check without a surviving
    # installer temporary masquerading as framework corruption.
    if [[ -n ${SNAPSHOT} ]]; then
        rm -rf -- "${SNAPSHOT}"
        SNAPSHOT=
        sync_path "${BASE}"
    fi
}

verify_source_symlinks() {
    local entry relative target
    while IFS= read -r -d '' entry; do
        relative=${entry#"${SNAPSHOT}/"}
        target=
        read_exact_symlink_target "${entry}" target
        case ${relative} in
            portage/make.profile)
                [[ ${target} == ../../../../../var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm ]] || \
                    fail "unreviewed make.profile target: ${target}"
                ;;
            portage/postsync.d/50-eix-postsync)
                [[ ${target} == /usr/bin/eix-postsync ]] || fail "unreviewed postsync target: ${target}"
                ;;
            *)
                fail "unreviewed source symlink: ${relative} -> ${target}"
                ;;
        esac
    done < <(find "${SNAPSHOT}/portage" "${SNAPSHOT}/local-overlay" -type l -print0 | sort -z)
}

normalize_tree() {
    local tree=$1 entry
    find "${tree}" -type d -exec chmod 0755 -- {} +
    while IFS= read -r -d '' entry; do
        if [[ -x ${entry} ]]; then chmod 0755 -- "${entry}"; else chmod 0644 -- "${entry}"; fi
    done < <(find "${tree}" -type f -print0)
    chown -R "${EXPECTED_UID}:${EXPECTED_GID}" -- "${tree}"
}

verify_no_extended_metadata() {
    local label=$1 entry quoted
    shift
    METADATA_AUDIT_TEMP=$(mktemp "${BASE}/.extended-metadata-audit.XXXXXXXX")
    if ! /usr/bin/getfattr -R -P -h -d -m - --absolute-names -- "$@" \
        >"${METADATA_AUDIT_TEMP}" 2>"${METADATA_AUDIT_TEMP}.stderr"; then
        fail "cannot audit extended metadata on ${label}"
        return 1
    fi
    if [[ -s ${METADATA_AUDIT_TEMP} ]]; then
        entry=$(sed -n 's/^# file: //p' "${METADATA_AUDIT_TEMP}" | head -n 1)
        printf -v quoted '%q' "${entry:-unknown}"
        fail "${label} carries unbound extended metadata: ${quoted}"
        return 1
    fi
    rm -f -- "${METADATA_AUDIT_TEMP}" "${METADATA_AUDIT_TEMP}.stderr"
    METADATA_AUDIT_TEMP=
}

render_bound_portage_bashrc() {
    local target=$1 line found=0
    while IFS= read -r line || [[ -n ${line} ]]; do
        if [[ ${line} == '# GENTOO_OPT_FRAMEWORK_BINDING_PLACEHOLDER' ]]; then
            found=$((found + 1))
            printf 'gentoo_opt_embedded_framework_target=%q\n' "${target}"
            printf 'gentoo_opt_embedded_framework_base=%q\n' "${BASE}"
            printf 'gentoo_opt_embedded_framework_trust_anchor=%q\n' "${TEST_ROOT:-/}"
            printf 'gentoo_opt_embedded_framework_expected_uid=%q\n' "${EXPECTED_UID}"
        else
            printf '%s\n' "${line}"
        fi
    done <"${SNAPSHOT}/portage/bashrc"
    ((found == 1)) || fail 'Portage bashrc does not contain exactly one framework binding marker'
}

render_shell_helper_bootstrap() {
    local relative=$1
    # shellcheck disable=SC2016 # These are literal lines for the installed bootstrap.
    printf '%s\n' \
        '#!/bin/bash' \
        'set -Eeuo pipefail' \
        "FRAMEWORK_CURRENT=$(printf '%q' "${FRAMEWORK_CURRENT}")" \
        "FRAMEWORK_BASE=$(printf '%q' "${BASE}")" \
        "FRAMEWORK_TRUST_ANCHOR=$(printf '%q' "${TEST_ROOT:-/}")" \
        "FRAMEWORK_RELATIVE=$(printf '%q' "${relative}")" \
        "EXPECTED_UID=$(printf '%q' "${EXPECTED_UID}")" \
        'FRAMEWORK_COMPONENT=${FRAMEWORK_BASE}' \
        'while :; do' \
        '    [[ -d ${FRAMEWORK_COMPONENT} && ! -L ${FRAMEWORK_COMPONENT} ]] || exit 125' \
        '    FRAMEWORK_STAT=$(/usr/bin/stat -c '\''%u:%a'\'' -- "${FRAMEWORK_COMPONENT}") || exit 125' \
        '    FRAMEWORK_OWNER=${FRAMEWORK_STAT%%:*}' \
        '    FRAMEWORK_MODE=${FRAMEWORK_STAT#*:}' \
        '    [[ ${FRAMEWORK_OWNER} == "${EXPECTED_UID}" && ${FRAMEWORK_MODE} =~ ^[0-7]{3,4}$ ]] || exit 125' \
        '    (( (8#${FRAMEWORK_MODE} & 8#022) == 0 )) || { printf '\''gentoo-optimization: framework trust path is writable by an untrusted identity\n'\'' >&2; exit 125; }' \
        '    [[ ${FRAMEWORK_COMPONENT} == "${FRAMEWORK_TRUST_ANCHOR}" ]] && break' \
        '    if [[ ${FRAMEWORK_TRUST_ANCHOR} == / ]]; then [[ ${FRAMEWORK_COMPONENT} == /* ]] || exit 125; else [[ ${FRAMEWORK_COMPONENT} == "${FRAMEWORK_TRUST_ANCHOR}"/* ]] || exit 125; fi' \
        '    FRAMEWORK_COMPONENT=${FRAMEWORK_COMPONENT%/*}' \
        '    [[ -n ${FRAMEWORK_COMPONENT} ]] || FRAMEWORK_COMPONENT=/' \
        'done' \
        'if [[ -n ${GENTOO_OPT_FRAMEWORK_TARGET-} ]]; then' \
        '    FRAMEWORK_TARGET=${GENTOO_OPT_FRAMEWORK_TARGET}' \
        'else' \
        '    [[ -L ${FRAMEWORK_CURRENT} ]] || { printf '\''gentoo-optimization: active framework link is unavailable\n'\'' >&2; exit 125; }' \
        '    FRAMEWORK_LINK_STAT=$(/usr/bin/stat -c '\''%F:%u'\'' -- "${FRAMEWORK_CURRENT}") || exit 125' \
        '    [[ ${FRAMEWORK_LINK_STAT} == "symbolic link:${EXPECTED_UID}" ]] || { printf '\''gentoo-optimization: active framework link has an untrusted identity\n'\'' >&2; exit 125; }' \
        '    FRAMEWORK_TARGET=$(/usr/bin/readlink -- "${FRAMEWORK_CURRENT}") || exit 125' \
        'fi' \
        'FRAMEWORK_ID=${FRAMEWORK_TARGET#"${FRAMEWORK_BASE}"/framework-}' \
        '[[ ${FRAMEWORK_TARGET} == "${FRAMEWORK_BASE}/framework-${FRAMEWORK_ID}" && ${FRAMEWORK_ID} =~ ^[0-9a-f]{64}$ ]] || { printf '\''gentoo-optimization: selected framework target is unmanaged\n'\'' >&2; exit 125; }' \
        '[[ -d ${FRAMEWORK_TARGET} && ! -L ${FRAMEWORK_TARGET} ]] || { printf '\''gentoo-optimization: active framework target is unavailable\n'\'' >&2; exit 125; }' \
        'FRAMEWORK_TOOL=${FRAMEWORK_TARGET}/libexec/${FRAMEWORK_RELATIVE}' \
        '[[ -f ${FRAMEWORK_TOOL} && ! -L ${FRAMEWORK_TOOL} && -x ${FRAMEWORK_TOOL} ]] || { printf '\''gentoo-optimization: active framework helper is unavailable\n'\'' >&2; exit 125; }' \
        'FRAMEWORK_COMPONENT=${FRAMEWORK_TOOL}' \
        'while :; do' \
        '    FRAMEWORK_STAT=$(/usr/bin/stat -c '\''%u:%a'\'' -- "${FRAMEWORK_COMPONENT}") || exit 125' \
        '    FRAMEWORK_OWNER=${FRAMEWORK_STAT%%:*}' \
        '    FRAMEWORK_MODE=${FRAMEWORK_STAT#*:}' \
        '    [[ ${FRAMEWORK_OWNER} == "${EXPECTED_UID}" && ${FRAMEWORK_MODE} =~ ^[0-7]{3,4}$ ]] || exit 125' \
        '    (( (8#${FRAMEWORK_MODE} & 8#022) == 0 )) || { printf '\''gentoo-optimization: active framework helper path is writable by an untrusted identity\n'\'' >&2; exit 125; }' \
        '    [[ ${FRAMEWORK_COMPONENT} == "${FRAMEWORK_TARGET}" ]] && break' \
        '    FRAMEWORK_COMPONENT=${FRAMEWORK_COMPONENT%/*}' \
        'done' \
        'exec /bin/bash -- "${FRAMEWORK_TOOL}" "$@"'
}

python_literal() {
    /usr/bin/python3 -I -B -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}

render_python_helper_bootstrap_version() {
    local relative=$1 version=$2 shebang exec_line
    case ${version} in
        current-v2)
            # `-IB` is one shebang argument and disables bytecode before this
            # dispatcher imports any standard-library module.  The executable
            # candidate helper receives the same no-bytecode guarantee below.
            shebang='#!/usr/bin/python3 -IB'
            exec_line='os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", "-B", framework_tool, *sys.argv[1:]])'
            ;;
        legacy-v1)
            # Exact bytes accepted solely to migrate the reviewed, currently
            # deployed pre-Candidate-A ten-helper bootstrap schema.  Do not
            # broaden this compatibility path: an unrecognized fixed bootstrap
            # remains a hard stop.
            shebang='#!/usr/bin/python3 -I'
            exec_line='os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", framework_tool, *sys.argv[1:]])'
            ;;
        *)
            fail "unknown Python bootstrap renderer version: ${version}"
            ;;
    esac
    printf '%s\n' \
        "${shebang}" \
        '"""Stable active-framework dispatcher; contains no mutable implementation."""' \
        'import os' \
        'import re' \
        'import stat' \
        'import sys' \
        "FRAMEWORK_CURRENT = $(python_literal "${FRAMEWORK_CURRENT}")" \
        "FRAMEWORK_BASE = $(python_literal "${BASE}")" \
        "FRAMEWORK_TRUST_ANCHOR = $(python_literal "${TEST_ROOT:-/}")" \
        "FRAMEWORK_RELATIVE = $(python_literal "${relative}")" \
        "EXPECTED_UID = ${EXPECTED_UID}" \
        'def abort(message):' \
        '    print(f"gentoo-optimization: {message}", file=sys.stderr)' \
        '    raise SystemExit(125)' \
        'def trusted(path):' \
        '    try:' \
        '        metadata = os.lstat(path)' \
        '    except OSError as error:' \
        '        abort(f"cannot stat active framework path: {error}")' \
        '    if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != EXPECTED_UID or stat.S_IMODE(metadata.st_mode) & 0o022:' \
        '        abort("active framework path has an untrusted identity")' \
        '    return metadata' \
        'component = FRAMEWORK_BASE' \
        'while True:' \
        '    if not stat.S_ISDIR(trusted(component).st_mode):' \
        '        abort("framework trust path is not a directory")' \
        '    if component == FRAMEWORK_TRUST_ANCHOR:' \
        '        break' \
        '    if os.path.commonpath((component, FRAMEWORK_TRUST_ANCHOR)) != FRAMEWORK_TRUST_ANCHOR:' \
        '        abort("framework trust path escapes its anchor")' \
        '    component = os.path.dirname(component) or "/"' \
        'framework_target = os.environ.get("GENTOO_OPT_FRAMEWORK_TARGET", "")' \
        'if not framework_target:' \
        '    try:' \
        '        link_metadata = os.lstat(FRAMEWORK_CURRENT)' \
        '    except OSError as error:' \
        '        abort(f"active framework link is unavailable: {error}")' \
        '    if not stat.S_ISLNK(link_metadata.st_mode) or link_metadata.st_uid != EXPECTED_UID:' \
        '        abort("active framework link has an untrusted identity")' \
        '    try:' \
        '        framework_target = os.readlink(FRAMEWORK_CURRENT)' \
        '    except OSError as error:' \
        '        abort(f"cannot resolve active framework link: {error}")' \
        'prefix = FRAMEWORK_BASE + "/framework-"' \
        'identity = framework_target[len(prefix):] if framework_target.startswith(prefix) else ""' \
        'if re.fullmatch(r"[0-9a-f]{64}", identity) is None or framework_target != prefix + identity:' \
        '    abort("selected framework target is unmanaged")' \
        'framework_tool = framework_target + "/libexec/" + FRAMEWORK_RELATIVE' \
        'component = framework_tool' \
        'while True:' \
        '    metadata = trusted(component)' \
        '    if component == framework_tool and (not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & stat.S_IXUSR):' \
        '        abort("active framework helper is not executable")' \
        '    if component == framework_target:' \
        '        break' \
        '    component = os.path.dirname(component)' \
        "${exec_line}"
}

render_python_helper_bootstrap() {
    render_python_helper_bootstrap_version "$1" current-v2
}

render_legacy_python_helper_bootstrap() {
    render_python_helper_bootstrap_version "$1" legacy-v1
}

render_helper_bootstrap() {
    case $1 in
        *.py) render_python_helper_bootstrap "$1" ;;
        *) render_shell_helper_bootstrap "$1" ;;
    esac
}

render_qa_bootstrap() {
    # shellcheck disable=SC2016 # These are literal lines for the installed bootstrap.
    printf '%s\n' \
        '# shellcheck shell=bash' \
        '# Stable bootstrap: all mutable QA logic comes from framework-current.' \
        "gentoo_opt_framework_current=$(printf '%q' "${FRAMEWORK_CURRENT}")" \
        "gentoo_opt_framework_base=$(printf '%q' "${BASE}")" \
        "gentoo_opt_framework_trust_anchor=$(printf '%q' "${TEST_ROOT:-/}")" \
        "gentoo_opt_framework_expected_uid=$(printf '%q' "${EXPECTED_UID}")" \
        'gentoo_opt_framework_component=${gentoo_opt_framework_base}' \
        'while :; do' \
        '    [[ -d ${gentoo_opt_framework_component} && ! -L ${gentoo_opt_framework_component} ]] || die '\''gentoo-optimization: framework trust path is unavailable'\''' \
        '    gentoo_opt_framework_component_stat=$(/usr/bin/stat -c '\''%u:%a'\'' -- "${gentoo_opt_framework_component}") || die '\''gentoo-optimization: cannot stat framework trust path'\''' \
        '    gentoo_opt_framework_component_owner=${gentoo_opt_framework_component_stat%%:*}' \
        '    gentoo_opt_framework_component_mode=${gentoo_opt_framework_component_stat#*:}' \
        '    [[ ${gentoo_opt_framework_component_owner} == "${gentoo_opt_framework_expected_uid}" && ${gentoo_opt_framework_component_mode} =~ ^[0-7]{3,4}$ ]] || die '\''gentoo-optimization: framework trust path has an untrusted identity'\''' \
        '    (( (8#${gentoo_opt_framework_component_mode} & 8#022) == 0 )) || die '\''gentoo-optimization: framework trust path is writable by an untrusted identity'\''' \
        '    [[ ${gentoo_opt_framework_component} == "${gentoo_opt_framework_trust_anchor}" ]] && break' \
        '    if [[ ${gentoo_opt_framework_trust_anchor} == / ]]; then [[ ${gentoo_opt_framework_component} == /* ]] || die '\''gentoo-optimization: framework trust path escapes its anchor'\''; else [[ ${gentoo_opt_framework_component} == "${gentoo_opt_framework_trust_anchor}"/* ]] || die '\''gentoo-optimization: framework trust path escapes its anchor'\''; fi' \
        '    gentoo_opt_framework_component=${gentoo_opt_framework_component%/*}' \
        '    [[ -n ${gentoo_opt_framework_component} ]] || gentoo_opt_framework_component=/' \
        'done' \
        'if [[ -n ${GENTOO_OPT_FRAMEWORK_TARGET-} ]]; then' \
        '    gentoo_opt_framework_target=${GENTOO_OPT_FRAMEWORK_TARGET}' \
        'else' \
        '    [[ -L ${gentoo_opt_framework_current} ]] || die '\''gentoo-optimization: active framework link is unavailable at the QA boundary'\''' \
        '    gentoo_opt_framework_link_stat=$(/usr/bin/stat -c '\''%F:%u'\'' -- "${gentoo_opt_framework_current}") || die '\''gentoo-optimization: cannot stat active framework link'\''' \
        '    [[ ${gentoo_opt_framework_link_stat} == "symbolic link:${gentoo_opt_framework_expected_uid}" ]] || die '\''gentoo-optimization: active framework link has an untrusted identity'\''' \
        '    gentoo_opt_framework_target=$(/usr/bin/readlink -- "${gentoo_opt_framework_current}") || die '\''gentoo-optimization: cannot resolve the active framework at the QA boundary'\''' \
        'fi' \
        'gentoo_opt_framework_id=${gentoo_opt_framework_target#"${gentoo_opt_framework_base}"/framework-}' \
        '[[ ${gentoo_opt_framework_target} == "${gentoo_opt_framework_base}/framework-${gentoo_opt_framework_id}" && ${gentoo_opt_framework_id} =~ ^[0-9a-f]{64}$ ]] || die '\''gentoo-optimization: unmanaged selected framework at the QA boundary'\''' \
        'gentoo_opt_framework_qa=${gentoo_opt_framework_target}/qa/zz-gentoo-optimization-bolt' \
        '[[ -d ${gentoo_opt_framework_target} && ! -L ${gentoo_opt_framework_target} && -f ${gentoo_opt_framework_qa} && ! -L ${gentoo_opt_framework_qa} ]] || die '\''gentoo-optimization: active QA implementation is unavailable'\''' \
        'gentoo_opt_framework_component=${gentoo_opt_framework_qa}' \
        'while :; do' \
        '    [[ ! -L ${gentoo_opt_framework_component} ]] || die '\''gentoo-optimization: active QA implementation traverses a symlink'\''' \
        '    gentoo_opt_framework_component_stat=$(/usr/bin/stat -c '\''%u:%a'\'' -- "${gentoo_opt_framework_component}") || die '\''gentoo-optimization: cannot stat active QA implementation path'\''' \
        '    gentoo_opt_framework_component_owner=${gentoo_opt_framework_component_stat%%:*}' \
        '    gentoo_opt_framework_component_mode=${gentoo_opt_framework_component_stat#*:}' \
        '    [[ ${gentoo_opt_framework_component_owner} == "${gentoo_opt_framework_expected_uid}" && ${gentoo_opt_framework_component_mode} =~ ^[0-7]{3,4}$ ]] || die '\''gentoo-optimization: active QA implementation has an untrusted identity'\''' \
        '    (( (8#${gentoo_opt_framework_component_mode} & 8#022) == 0 )) || die '\''gentoo-optimization: active QA implementation is writable by an untrusted identity'\''' \
        '    [[ ${gentoo_opt_framework_component} == "${gentoo_opt_framework_target}" ]] && break' \
        '    gentoo_opt_framework_component=${gentoo_opt_framework_component%/*}' \
        'done' \
        '# shellcheck disable=SC1090 -- the exact root-owned generation was validated above.' \
        'source "${gentoo_opt_framework_qa}"' \
        'unset gentoo_opt_framework_current gentoo_opt_framework_base gentoo_opt_framework_trust_anchor gentoo_opt_framework_expected_uid gentoo_opt_framework_component gentoo_opt_framework_component_stat gentoo_opt_framework_component_owner gentoo_opt_framework_component_mode gentoo_opt_framework_link_stat gentoo_opt_framework_target gentoo_opt_framework_id gentoo_opt_framework_qa'
}

render_portage_migration_guard() {
    # shellcheck disable=SC2016 # These are literal lines for the installed guard.
    printf '%s\n' \
        '# shellcheck shell=bash' \
        '# Root-owned first-activation guard. No package build may cross it.' \
        "gentoo_opt_framework_activation_journal=$(printf '%q' "${ACTIVATION_JOURNAL}")" \
        'printf '\''gentoo-optimization: ERROR: framework activation is incomplete (%s)\n'\'' "${gentoo_opt_framework_activation_journal}" >&2' \
        'if declare -F die >/dev/null; then' \
        '    die "gentoo-optimization: framework activation is incomplete"' \
        'fi' \
        'return 1'
}

portage_migration_guard_matches() {
    local root=$1 temporary
    local -a actual=()
    [[ -d ${root} && ! -L ${root} ]] || return 1
    mapfile -t actual < <(find "${root}" -mindepth 1 -printf '%y\t%P\n' | sort)
    [[ ${actual[*]} == $'f\tbashrc' ]] || return 1
    temporary=$(mktemp "${BASE}/.portage-migration-guard-check.XXXXXXXX")
    render_portage_migration_guard >"${temporary}"
    if ! cmp -s -- "${temporary}" "${root}/bashrc"; then
        rm -f -- "${temporary}"
        return 1
    fi
    rm -f -- "${temporary}"
}

render_activation_journal() {
    local candidate=$1
    printf 'schema=gentoo-optimization-framework-activation-v1\n'
    printf 'state=pending\n'
    printf 'candidate=%s\n' "${candidate}"
    printf 'previous=none\n'
    printf 'source_aggregate_sha256=%s\n' "${SOURCE_AGGREGATE}"
    printf 'installer_sha256=%s\n' "${INSTALLER_SHA256}"
}

begin_first_activation_journal() {
    local candidate=$1 expected temporary
    expected=$(mktemp "${BASE}/.framework-activation-expected.XXXXXXXX")
    render_activation_journal "${candidate}" >"${expected}"
    if [[ -e ${ACTIVATION_JOURNAL} || -L ${ACTIVATION_JOURNAL} ]]; then
        verify_regular_trusted "${ACTIVATION_JOURNAL}" 0600
        cmp -s -- "${expected}" "${ACTIVATION_JOURNAL}" || {
            rm -f -- "${expected}"
            fail 'pending framework activation journal differs from the reviewed retry'
        }
        rm -f -- "${expected}"
        return 0
    fi
    temporary=${ACTIVATION_JOURNAL}.partial.$$
    install -o "${EXPECTED_UID}" -g "${EXPECTED_GID}" -m 0600 -T -- \
        "${expected}" "${temporary}"
    rm -f -- "${expected}"
    sync_path "${temporary}"
    mv -T -- "${temporary}" "${ACTIVATION_JOURNAL}"
    sync_path "${BASE}"
}

install_portage_migration_guard() {
    local stage=${ETC_PORTAGE}.partial.$$
    rm -rf -- "${stage}"
    mkdir -p -- "${stage}"
    render_portage_migration_guard >"${stage}/bashrc"
    chown -R "${EXPECTED_UID}:${EXPECTED_GID}" -- "${stage}"
    chmod 0755 -- "${stage}"
    chmod 0644 -- "${stage}/bashrc"
    sync_tree "${stage}"
    atomic_publish_entry "${stage}" "${ETC_PORTAGE}"
    portage_migration_guard_matches "${ETC_PORTAGE}" || \
        fail 'first-activation Portage guard publication differs'
}

finish_first_activation_journal() {
    local candidate=$1
    if [[ ! -e ${ACTIVATION_JOURNAL} && ! -L ${ACTIVATION_JOURNAL} ]]; then
        return 0
    fi
    verify_regular_trusted "${ACTIVATION_JOURNAL}" 0600
    cmp -s -- <(render_activation_journal "${candidate}") "${ACTIVATION_JOURNAL}" || \
        fail 'cannot finish a mismatched framework activation journal'
    [[ -L ${FRAMEWORK_CURRENT} && $(readlink -- "${FRAMEWORK_CURRENT}") == "${candidate}" ]] || \
        fail 'cannot finish framework activation before current selects the journal candidate'
    verify_external_indirections
    rm -f -- "${ACTIVATION_JOURNAL}"
    sync_path "${BASE}"
}

helper_bootstrap_sha256() {
    render_helper_bootstrap "$1" | sha256sum | awk '{print $1}'
}

qa_bootstrap_sha256() {
    render_qa_bootstrap | sha256sum | awk '{print $1}'
}

render_manifest() {
    local candidate_inventory_sha=$1 current_target=$2 previous_target=$3 index source hash
    printf 'schema=gentoo-optimization-framework-install-v4\n'
    printf 'installer_sha256=%s\n' "${INSTALLER_SHA256}"
    printf 'source_aggregate_sha256=%s\n' "${SOURCE_AGGREGATE}"
    printf 'framework_aggregate_sha256=%s\n' "${FRAMEWORK_AGGREGATE}"
    printf 'candidate_inventory_sha256=%s\n' "${candidate_inventory_sha}"
    printf 'current_generation=%s\n' "${current_target}"
    printf 'previous_generation=%s\n' "${previous_target}"
    printf 'git_commit=%s\n' "${GIT_COMMIT}"
    printf 'git_worktree=%s\n' "${GIT_DIRTY}"
    printf 'git_status_sha256=%s\n' "${SOURCE_STATUS}"
    printf 'generated_policy=%s\n' "${GENERATED_POLICY_ID}"
    printf 'frozen_inventory_sha256=%s\n' "${FROZEN_INVENTORY_SHA256}"
    printf 'qa_hook_basename=%s\n' "${HOOK_BASENAME}"
    printf 'jq_sha256=%s\n' "${JQ_SHA256}"
    printf 'jq_version=%s\n' "${JQ_VERSION}"
    printf 'lock_directory=%s\t%s:%s\t%s\n' "${LOCK_PATH%/*}" \
        "${EXPECTED_UID}" "${LOCK_GID}" "${LOCK_DIRECTORY_MODE}"
    printf 'lock_files=%s,%s,%s\t%s:%s\t%s\n' "${LOCK_PATH}" \
        "${PROJECT_LOCK_PATH}" "${GENERATION_LOCK_PATH}" \
        "${EXPECTED_UID}" "${LOCK_GID}" "${LOCK_FILE_MODE}"
    printf 'path\tsha256\tmode\towner\n'
    printf '%s\t%s\t0644\t%s:%s\n' \
        "${ETC_PORTAGE}/bashrc" \
        "$(render_bound_portage_bashrc "${current_target}" | sha256sum | awk '{print $1}')" \
        "${EXPECTED_UID}" "${EXPECTED_GID}"
    printf '%s\t%s\t0644\t%s:%s\n' \
        "${INSTALL_QA_ROOT}/${HOOK_BASENAME}" \
        "$(qa_bootstrap_sha256)" \
        "${EXPECTED_UID}" "${EXPECTED_GID}"
    for index in "${!HELPER_RELATIVE[@]}"; do
        hash=$(helper_bootstrap_sha256 "${HELPER_RELATIVE[index]}")
        printf '%s/%s\t%s\t0755\t%s:%s\n' \
            "${LIBEXEC_ROOT}" "${HELPER_RELATIVE[index]}" "${hash}" \
            "${EXPECTED_UID}" "${EXPECTED_GID}"
    done
    for source in package-state.schema.json artifact-state.schema.json \
        final-system-state.schema.json; do
        hash=$(sha256sum -- "${SNAPSHOT}/optimization/schema/${source}"); hash=${hash%% *}
        printf '%s/schema/%s\t%s\t0644\t%s:%s\n' \
            "${SHARE_ROOT}" "${source}" "${hash}" "${EXPECTED_UID}" "${EXPECTED_GID}"
    done
    printf '%s\t%s\t0755\t%s:%s\n' "${JQ_PATH}" "${JQ_SHA256}" \
        "${EXPECTED_UID}" "${EXPECTED_GID}"
}

candidate_inventory() {
    local candidate=$1 entry relative mode digest target
    while IFS= read -r -d '' entry; do
        relative=${entry#"${candidate}/"}
        [[ ${relative} != "${entry}" ]] || continue
        [[ ${relative} != install.manifest && ${relative} != .candidate-inventory ]] || continue
        reject_control_name "${relative}" 'relative path'
        if [[ -L ${entry} ]]; then
            target=
            read_exact_symlink_target "${entry}" target
            printf 'l\t-\t-\tframework/%s\t%s\n' "${relative}" "${target}"
        elif [[ -d ${entry} ]]; then
            mode=$(stat -c %a -- "${entry}")
            printf 'd\t0%s\t-\tframework/%s\t-\n' "${mode}" "${relative}"
        elif [[ -f ${entry} ]]; then
            mode=$(stat -c %a -- "${entry}")
            digest=$(sha256sum -- "${entry}"); digest=${digest%% *}
            printf 'f\t0%s\t%s\tframework/%s\t-\n' "${mode}" "${digest}" "${relative}"
        else
            fail "unsupported filesystem object in candidate: ${entry}"
        fi
    done < <(find "${candidate}" -mindepth 1 -print0 | sort -z)
}

verify_inventory_exact() {
    local candidate=$1 expected
    expected=${candidate}/.candidate-inventory
    verify_regular_trusted "${expected}" 0600
    CANDIDATE_INVENTORY_TEMP=$(mktemp "${BASE}/.candidate-inventory-check.XXXXXXXX")
    candidate_inventory "${candidate}" >"${CANDIDATE_INVENTORY_TEMP}"
    cmp -s -- "${CANDIDATE_INVENTORY_TEMP}" "${expected}" || {
        rm -f -- "${CANDIDATE_INVENTORY_TEMP}"
        CANDIDATE_INVENTORY_TEMP=
        fail "immutable candidate entry set or content differs: ${candidate}"
    }
    rm -f -- "${CANDIDATE_INVENTORY_TEMP}"
    CANDIDATE_INVENTORY_TEMP=
}

verify_make_profile() {
    local candidate=$1 profile expected actual probe literal_target
    profile=${candidate}/portage/make.profile
    [[ -L ${profile} ]] || fail 'candidate make.profile is not a symlink'
    literal_target=
    read_exact_symlink_target "${profile}" literal_target
    [[ ${literal_target} == ../../../../../var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm ]] || \
        fail 'candidate make.profile has the wrong literal target'
    expected=$(physical /var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm)
    actual=$(realpath -e -- "${profile}") || fail 'candidate make.profile target cannot be resolved'
    [[ ${actual} == "${expected}" ]] || fail "candidate make.profile resolves to ${actual}, expected ${expected}"
    # Gentoo leaf profiles commonly inherit all make.defaults values from
    # parents and therefore have no leaf make.defaults of their own.  Require
    # the exact reviewed leaf's parent descriptor here; the live Portage query
    # below proves that the complete inheritance chain resolves ARCH/CHOST.
    probe=${actual}/parent
    [[ -f ${probe} && ! -L ${probe} && -r ${probe} ]] || \
        fail "profile parent descriptor is not readable: ${probe}"
    if [[ -z ${TEST_ROOT} ]] && id -u portage >/dev/null 2>&1; then
        runuser -u portage -- test -x "${actual}" || \
            fail 'Portage user cannot traverse the selected profile'
        runuser -u portage -- test -r "${probe}" || \
            fail 'Portage user cannot read the selected profile'
        runuser -u portage -- test -r "${candidate}/portage/make.conf" || \
            fail 'Portage user cannot read the candidate configuration'
    else
        test -r "${probe}" && test -r "${candidate}/portage/make.conf" || \
            fail 'installer test user cannot read candidate configuration/profile'
    fi
}

verify_generated_policy() {
    local candidate=$1 directory identity calculated package_env_target generated_env_target
    directory=${candidate}/generated-policy
    [[ -d ${directory} && ! -L ${directory} ]] || fail 'generated-policy directory is absent'
    verify_candidate_portage_readable "${directory}/.identity"
    identity=$(<"${directory}/.identity")
    [[ ${identity} == "${GENERATED_POLICY_ID}" ]] || fail 'generated-policy identity differs'
    [[ -f ${directory}/package.env && ! -L ${directory}/package.env && \
        -d ${directory}/env && ! -L ${directory}/env ]] || \
        fail 'candidate generated policy shape differs'
    package_env_target=
    generated_env_target=
    read_exact_symlink_target \
        "${candidate}/portage/package.env/99-generated-optimization" package_env_target
    read_exact_symlink_target \
        "${candidate}/portage/env/optimization/generated" generated_env_target
    [[ ${package_env_target} == ../../generated-policy/package.env ]] || \
        fail 'Portage package.env is not bound to the candidate generated policy'
    [[ ${generated_env_target} == ../../../generated-policy/env ]] || \
        fail 'Portage env is not bound to the candidate generated policy'
    if [[ ${identity} == empty-v1 ]]; then
        [[ ! -s ${directory}/package.env && \
            -z $(find "${directory}/env" -mindepth 1 -print -quit) ]] || \
            fail 'empty generated-policy generation contains an assignment or environment'
    else
        verify_regular_trusted "${directory}/.frozen-inventory.json" 0600
        [[ $(sha256sum -- "${directory}/.frozen-inventory.json" | awk '{print $1}') == \
            "${FROZEN_INVENTORY_SHA256}" ]] || \
            fail 'candidate generated policy frozen-inventory binding differs'
        calculated=$(emit_tree_inventory "${directory}" generated-policy .identity \
            .frozen-inventory.json | \
            sha256sum | awk '{print $1}')
        [[ ${calculated} == "${identity}" ]] || \
            fail 'candidate generated-policy content differs from its identity'
    fi
}

verify_candidate() {
    local candidate=$1 expected_manifest=${2:-} entry uid gid mode nlink expected_gid
    [[ -d ${candidate} && ! -L ${candidate} ]] || fail "candidate is not a directory: ${candidate}"
    [[ $(stat -c '%u:%g:%a' -- "${candidate}") == "${EXPECTED_UID}:${EXPECTED_GID}:755" ]] || \
        fail "candidate root ownership/mode differs: ${candidate}"
    verify_existing_ancestor_chain "${candidate}"
    while IFS= read -r -d '' entry; do
        uid=$(stat -c %u -- "${entry}")
        gid=$(stat -c %g -- "${entry}")
        expected_gid=${EXPECTED_GID}
        if [[ -z ${TEST_ROOT} && \
            (${entry} == "${candidate}/install.manifest" || \
             ${entry} == "${candidate}/generated-policy/.identity") ]]; then
            expected_gid=${PORTAGE_GID}
        fi
        [[ ${uid}:${gid} == "${EXPECTED_UID}:${expected_gid}" ]] || \
            fail "candidate entry has the wrong owner/group: ${entry}"
        if [[ -L ${entry} ]]; then
            nlink=$(stat -c %h -- "${entry}")
            [[ ${nlink} == 1 ]] || \
                fail "candidate symlink is not single-link: ${entry}"
            continue
        fi
        mode=$(stat -c %a -- "${entry}")
        [[ ${mode} =~ ^[0-7]{3,4}$ ]] || \
            fail "candidate entry has an invalid mode: ${entry}"
        (( (8#${mode} & 8#022) == 0 )) || \
            fail "candidate entry is group/world-writable: ${entry}"
        (( (8#${mode} & 8#7000) == 0 )) || \
            fail "candidate entry has unsafe special mode bits: ${entry}"
        if [[ -f ${entry} ]]; then
            nlink=$(stat -c %h -- "${entry}")
            [[ ${nlink} == 1 ]] || \
                fail "candidate regular file is not single-link: ${entry}"
        elif [[ ! -d ${entry} ]]; then
            fail "candidate entry has an unsupported filesystem type: ${entry}"
        fi
    done < <(find "${candidate}" -mindepth 1 -print0)
    verify_no_extended_metadata 'immutable framework candidate' "${candidate}"
    verify_inventory_exact "${candidate}"
    verify_candidate_portage_readable "${candidate}/install.manifest"
    [[ -z ${expected_manifest} ]] || cmp -s -- "${expected_manifest}" "${candidate}/install.manifest" || \
        fail 'candidate manifest is not the canonical expected manifest'
    verify_generated_policy "${candidate}"
    verify_make_profile "${candidate}"
    grep -Fxq 'location = /var/lib/gentoo-optimization/framework-current/local-overlay' \
        "${candidate}/portage/repos.conf/codex-local.conf" || \
        fail 'codex-local repos.conf is not bound to framework-current/local-overlay'
}

verify_live_overlay_resolution() {
    local resolved actual_profile expected_profile
    local -a effective_identity=()
    [[ $(<"${FRAMEWORK_CURRENT}/local-overlay/profiles/repo_name") == codex-local ]] || \
        fail 'active local overlay repo_name differs'
    [[ -n ${TEST_ROOT} ]] && return 0
    resolved=$(portageq get_repo_path / codex-local 2>/dev/null || true)
    [[ ${resolved} == "${FRAMEWORK_CURRENT}/local-overlay" || \
        ${resolved} == "$(readlink -e -- "${FRAMEWORK_CURRENT}")/local-overlay" ]] || \
        fail "Portage resolves codex-local to ${resolved}, not the active framework overlay"
    expected_profile=$(physical /var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm)
    actual_profile=$(realpath -e -- "${ETC_PORTAGE}/make.profile") || \
        fail 'active Portage profile cannot be resolved'
    [[ ${actual_profile} == "${expected_profile}" ]] || \
        fail "active Portage profile resolves to ${actual_profile}, expected ${expected_profile}"
    mapfile -t effective_identity < <(
        /usr/bin/runuser -u portage -- /usr/bin/portageq envvar ARCH CHOST
    )
    [[ ${#effective_identity[@]} == 2 && \
        ${effective_identity[0]} == amd64 && \
        ${effective_identity[1]} == x86_64-pc-linux-gnu ]] || \
        fail "Portage profile inheritance resolved unexpected ARCH/CHOST: ${effective_identity[*]}"
}

get_previous_target() {
    local target identity
    if [[ -L ${FRAMEWORK_CURRENT} ]]; then
        [[ $(stat -c '%u:%g' -- "${FRAMEWORK_CURRENT}") == \
            "${EXPECTED_UID}:${EXPECTED_GID}" ]] || \
            fail 'framework-current symlink has the wrong owner'
        target=$(readlink -- "${FRAMEWORK_CURRENT}")
        identity=${target#"${BASE}"/framework-}
        [[ ${target} == "${BASE}/framework-${identity}" && ${identity} =~ ^[0-9a-f]{64}$ ]] || \
            fail "framework-current has an unmanaged target: ${target}"
        [[ -d ${target} && ! -L ${target} ]] || fail 'framework-current target is absent or symlinked'
        PREVIOUS_TARGET=${target}
    elif [[ -e ${FRAMEWORK_CURRENT} ]]; then
        fail 'framework-current exists but is not a managed symlink'
    fi
}

validate_legacy_migration() {
    local target
    if [[ -L ${ETC_PORTAGE} ]]; then
        target=$(readlink -- "${ETC_PORTAGE}")
        case ${target} in
            "${ROOT}/portage"|"${FRAMEWORK_CURRENT}/portage"|"${BASE}/portage-current") ;;
            "${BASE}"/portage-[0-9a-f]*/portage) ;;
            *) fail "/etc/portage points to an unmanaged migration source: ${target}" ;;
        esac
    elif [[ -e ${ETC_PORTAGE} ]]; then
        portage_migration_guard_matches "${ETC_PORTAGE}" || \
            fail '/etc/portage is neither a reviewed symlink nor the exact first-activation guard'
    fi
    if [[ -e ${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt || \
          -L ${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt ]]; then
        [[ -L ${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt ]] || \
            fail 'legacy early BOLT QA hook is not the reviewed symlink'
        target=$(readlink -- "${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt")
        [[ ${target} == "${ROOT}/portage/install-qa-check.d/50-gentoo-optimization-bolt" ]] || \
            fail "legacy early BOLT QA hook has an unmanaged target: ${target}"
    fi
}

assert_global_qa_order() {
    local -a paths=()
    local repo portage_bin candidate_path name
    paths+=("${INSTALL_QA_ROOT}" "$(physical /usr/lib/install-qa-check.d)")
    if [[ -n ${TEST_ROOT} ]]; then
        paths+=("$(physical /var/db/repos/gentoo/metadata/install-qa-check.d)" \
            "$(physical /usr/lib/portage/install-qa-check.d)")
    else
        while IFS= read -r repo; do
            [[ -n ${repo} ]] || continue
            candidate_path=$(portageq get_repo_path / "${repo}" 2>/dev/null || true)
            [[ -n ${candidate_path} ]] && paths+=("${candidate_path}/metadata/install-qa-check.d")
        done < <(portageq get_repos / 2>/dev/null || true)
        paths+=(/usr/lib/portage/install-qa-check.d)
        portage_bin=$(portageq envvar PORTAGE_BIN_PATH 2>/dev/null || true)
        [[ -n ${portage_bin} ]] && paths+=("${portage_bin}/install-qa-check.d")
    fi
    paths+=("${CANDIDATE_STAGE:-${PREVIOUS_TARGET}}/local-overlay/metadata/install-qa-check.d")
    for candidate_path in "${paths[@]}"; do
        [[ -d ${candidate_path} ]] || continue
        while IFS= read -r -d '' name; do
            name=${name##*/}
            [[ ${name} == "${HOOK_BASENAME}" || ${name} < "${HOOK_BASENAME}" ]] || \
                fail "QA check sorts after ${HOOK_BASENAME}: ${candidate_path}/${name}"
        done < <(find "${candidate_path}" -maxdepth 1 -type f -print0 | sort -z)
    done
}

portage_quiescent() {
    local proc comm cmdline
    [[ -n ${TEST_ROOT} ]] && return 0
    for proc in /proc/[0-9]*; do
        [[ -r ${proc}/comm ]] || continue
        IFS= read -r comm <"${proc}/comm" || continue
        case ${comm} in
            emerge|ebuild|ebuild.sh|emaint|quickpkg)
                fail "Portage is active (${comm}, pid ${proc##*/})"
                ;;
        esac
        [[ -r ${proc}/cmdline ]] || continue
        cmdline=$(/usr/bin/tr '\0' ' ' <"${proc}/cmdline" 2>/dev/null || true)
        case ${cmdline} in
            *'/usr/bin/emerge '*|*'/usr/bin/ebuild '*|*'/usr/bin/emaint '*|*'/usr/bin/quickpkg '*)
                fail "Portage command is active (pid ${proc##*/})"
                ;;
        esac
    done
}

hold_bolt_locks() {
    local entry fd
    [[ -d ${CACHE_ROOT}/locks ]] || return 0
    while IFS= read -r -d '' entry; do
        [[ -f ${entry} && ! -L ${entry} ]] || fail "BOLT lock namespace contains an unsafe entry: ${entry}"
        exec {fd}<>"${entry}"
        flock -n "${fd}" || fail "an active BOLT transaction holds ${entry}"
        HELD_BOLT_LOCK_FDS+=("${fd}")
    done < <(find "${CACHE_ROOT}/locks" -mindepth 1 -maxdepth 1 -print0 | sort -z)
}

failure_point() {
    local point=$1
    [[ -n ${TEST_ROOT} ]] || return 0
    if [[ ${GENTOO_OPT_INSTALLER_PAUSE_AT:-} == "${point}" ]]; then
        printf 'PAUSE: %s\n' "${point}" >&2
        if [[ -n ${GENTOO_OPT_INSTALLER_PAUSE_FILE:-} ]]; then
            while [[ -e ${GENTOO_OPT_INSTALLER_PAUSE_FILE} ]]; do
                read -r -t 0.1 _ </dev/null || :
            done
        else
            while :; do read -r -t 1 _ </dev/null || :; done
        fi
    fi
    [[ ${GENTOO_OPT_INSTALLER_FAIL_AT:-} != "${point}" ]] || \
        fail "injected installer failure at ${point}"
}

bootstrap_tree_matches() {
    local root=$1 index temporary
    local -a actual=()
    [[ -d ${root} && ! -L ${root} ]] || return 1
    mapfile -t actual < <(find "${root}" -mindepth 1 -printf '%y\t%P\n' | sort)
    [[ ${actual[*]} == $'d\tbolt\nd\tpgo\nd\trecovery\nd\tscripts\nd\tscripts/optimization\nd\tscripts/optimization/lib\nd\tscripts/optimization/verify\nf\tbolt/artifact_tool.py\nf\tbolt/capture-input.sh\nf\tbolt/deploy-output.sh\nf\tbolt/register-output.sh\nf\tpgo/authorization-token-scan.py\nf\tpgo/production-profile-lock-transaction.py\nf\tpgo/profile-identity.py\nf\tpgo/profile_locks.py\nf\tpgo/validate-profile.py\nf\trecovery/verify-binpkg-snapshot.py\nf\tscripts/optimization/lib/state.py\nf\tscripts/optimization/verify/reconcile-state.py' ]] || return 1
    for index in "${!HELPER_RELATIVE[@]}"; do
        temporary=$(mktemp "${BASE}/.helper-bootstrap-check.XXXXXXXX")
        render_helper_bootstrap "${HELPER_RELATIVE[index]}" >"${temporary}"
        if ! cmp -s -- "${temporary}" "${root}/${HELPER_RELATIVE[index]}"; then
            rm -f -- "${temporary}"
            return 1
        fi
        rm -f -- "${temporary}"
    done
}

legacy_bootstrap_tree_matches() {
    local root=$1 relative temporary
    local -a actual=()
    [[ -d ${root} && ! -L ${root} ]] || return 1
    mapfile -t actual < <(find "${root}" -mindepth 1 -printf '%y\t%P\n' | sort)
    [[ ${actual[*]} == $'d\tbolt\nd\tpgo\nd\trecovery\nd\tscripts\nd\tscripts/optimization\nd\tscripts/optimization/lib\nd\tscripts/optimization/verify\nf\tbolt/artifact_tool.py\nf\tbolt/capture-input.sh\nf\tbolt/deploy-output.sh\nf\tbolt/register-output.sh\nf\tpgo/profile-identity.py\nf\tpgo/profile_locks.py\nf\tpgo/validate-profile.py\nf\trecovery/verify-binpkg-snapshot.py\nf\tscripts/optimization/lib/state.py\nf\tscripts/optimization/verify/reconcile-state.py' ]] || return 1
    for relative in "${LEGACY_BOOTSTRAP_HELPER_RELATIVE[@]}"; do
        temporary=$(mktemp "${BASE}/.helper-bootstrap-check.XXXXXXXX")
        render_helper_bootstrap "${relative}" >"${temporary}"
        if ! cmp -s -- "${temporary}" "${root}/${relative}"; then
            rm -f -- "${temporary}"
            return 1
        fi
        rm -f -- "${temporary}"
    done
}

legacy_python_bootstrap_tree_matches() {
    local root=$1 relative temporary
    local -a actual=()
    [[ -d ${root} && ! -L ${root} ]] || return 1
    mapfile -t actual < <(find "${root}" -mindepth 1 -printf '%y\t%P\n' | sort)
    [[ ${actual[*]} == $'d\tbolt\nd\tpgo\nd\trecovery\nd\tscripts\nd\tscripts/optimization\nd\tscripts/optimization/lib\nd\tscripts/optimization/verify\nf\tbolt/artifact_tool.py\nf\tbolt/capture-input.sh\nf\tbolt/deploy-output.sh\nf\tbolt/register-output.sh\nf\tpgo/profile-identity.py\nf\tpgo/profile_locks.py\nf\tpgo/validate-profile.py\nf\trecovery/verify-binpkg-snapshot.py\nf\tscripts/optimization/lib/state.py\nf\tscripts/optimization/verify/reconcile-state.py' ]] || return 1
    for relative in "${LEGACY_BOOTSTRAP_HELPER_RELATIVE[@]}"; do
        temporary=$(mktemp "${BASE}/.helper-bootstrap-check.XXXXXXXX")
        case ${relative} in
            *.py) render_legacy_python_helper_bootstrap "${relative}" >"${temporary}" ;;
            *) render_shell_helper_bootstrap "${relative}" >"${temporary}" ;;
        esac
        if ! cmp -s -- "${temporary}" "${root}/${relative}"; then
            rm -f -- "${temporary}"
            return 1
        fi
        rm -f -- "${temporary}"
    done
}

require_stable_bootstrap_compatibility() {
    local qa=${INSTALL_QA_ROOT}/${HOOK_BASENAME} temporary
    [[ ${PREVIOUS_TARGET} != none ]] || return 0
    # A surviving first-activation journal is already guarded by /etc/portage
    # and may legitimately have only a prefix of the stable indirections.  Its
    # exact candidate binding is checked before the journal can be completed.
    if [[ -e ${ACTIVATION_JOURNAL} || -L ${ACTIVATION_JOURNAL} ]]; then
        verify_regular_trusted "${ACTIVATION_JOURNAL}" 0600
        cmp -s -- <(render_activation_journal "${PREVIOUS_TARGET}") \
            "${ACTIVATION_JOURNAL}" || \
            fail 'pending first-activation journal does not select the active framework'
        return 0
    fi
    bootstrap_tree_matches "${LIBEXEC_ROOT}" || \
        legacy_bootstrap_tree_matches "${LIBEXEC_ROOT}" || \
        legacy_python_bootstrap_tree_matches "${LIBEXEC_ROOT}" || \
        fail 'stable-bootstrap migration required: installed helper bootstraps differ from the reviewed invariant bytes'
    temporary=$(mktemp "${BASE}/.qa-bootstrap-compatibility.XXXXXXXX")
    render_qa_bootstrap >"${temporary}"
    if ! cmp -s -- "${temporary}" "${qa}"; then
        rm -f -- "${temporary}"
        fail 'stable-bootstrap migration required: installed QA bootstrap differs from the reviewed invariant bytes'
    fi
    rm -f -- "${temporary}"
}

legacy_helper_tree_matches() {
    local root=$1 candidate=$2 relative
    local -a actual=()
    [[ -d ${root} && ! -L ${root} ]] || return 1
    mapfile -t actual < <(find "${root}" -mindepth 1 -printf '%y\t%P\n' | sort)
    [[ ${actual[*]} == $'d\tbolt\nd\tpgo\nd\trecovery\nd\tscripts\nd\tscripts/optimization\nd\tscripts/optimization/lib\nd\tscripts/optimization/verify\nf\tbolt/artifact_tool.py\nf\tbolt/capture-input.sh\nf\tbolt/deploy-output.sh\nf\tbolt/register-output.sh\nf\tpgo/profile-identity.py\nf\tpgo/profile_locks.py\nf\tpgo/validate-profile.py\nf\trecovery/verify-binpkg-snapshot.py\nf\tscripts/optimization/lib/state.py\nf\tscripts/optimization/verify/reconcile-state.py' ]] || return 1
    for relative in "${LEGACY_BOOTSTRAP_HELPER_RELATIVE[@]}"; do
        cmp -s -- "${root}/${relative}" \
            "${candidate}/libexec/${relative}" || return 1
    done
}

legacy_schema_tree_matches() {
    local root=$1 candidate=$2 schema
    local -a actual=()
    [[ -d ${root} && ! -L ${root} ]] || return 1
    mapfile -t actual < <(find "${root}" -mindepth 1 -printf '%y\t%P\n' | sort)
    [[ ${actual[*]} == $'d\tschema\nf\tschema/artifact-state.schema.json\nf\tschema/final-system-state.schema.json\nf\tschema/package-state.schema.json' ]] || return 1
    for schema in artifact-state.schema.json final-system-state.schema.json \
        package-state.schema.json; do
        cmp -s -- "${root}/schema/${schema}" "${candidate}/share/schema/${schema}" || return 1
    done
}

manifest_bootstrap_tree_matches() {
    local root=$1 candidate=$2 relative path directory expected_hash expected_mode expected_owner
    local actual_hash actual_mode actual_owner
    local -a actual_files=() manifest_files=()
    [[ -d ${root} && ! -L ${root} && -f ${candidate}/install.manifest ]] || return 1
    [[ -z $(find "${root}" -mindepth 1 ! -type d ! -type f -print -quit) ]] || return 1
    mapfile -t actual_files < <(find "${root}" -type f -printf '%P\n' | sort)
    mapfile -t manifest_files < <(
        awk -F '\t' -v prefix="${root}/" \
            'index($1, prefix) == 1 { print substr($1, length(prefix) + 1) }' \
            "${candidate}/install.manifest" | sort
    )
    [[ ${#actual_files[@]} -gt 0 && ${actual_files[*]} == "${manifest_files[*]}" ]] || return 1
    while IFS= read -r -d '' directory; do
        [[ $(stat -c '%u:%g' -- "${directory}") == "${EXPECTED_UID}:${EXPECTED_GID}" ]] || return 1
        mode_is_trusted "$(stat -c %a -- "${directory}")" || return 1
    done < <(find "${root}" -type d -print0)
    for relative in "${actual_files[@]}"; do
        path=${root}/${relative}
        IFS=$'\t' read -r expected_hash expected_mode expected_owner < <(
            awk -F '\t' -v exact="${path}" \
                '$1 == exact { count++; hash=$2; mode=$3; owner=$4 } END { if (count == 1) print hash "\t" mode "\t" owner }' \
                "${candidate}/install.manifest"
        )
        [[ ${expected_hash} =~ ^[0-9a-f]{64}$ && ${expected_mode} =~ ^0[0-7]{3}$ && \
            ${expected_owner} == "${EXPECTED_UID}:${EXPECTED_GID}" ]] || return 1
        actual_hash=$(sha256sum -- "${path}"); actual_hash=${actual_hash%% *}
        actual_mode=0$(stat -c %a -- "${path}")
        actual_owner=$(stat -c '%u:%g' -- "${path}")
        [[ ${actual_hash} == "${expected_hash}" && ${actual_mode} == "${expected_mode}" && \
            ${actual_owner} == "${expected_owner}" ]] || return 1
    done
}

manifest_external_file_matches() {
    local path=$1 candidate=$2 expected_hash expected_mode expected_owner
    local actual_hash actual_mode actual_owner
    [[ -f ${path} && ! -L ${path} && -f ${candidate}/install.manifest ]] || return 1
    IFS=$'\t' read -r expected_hash expected_mode expected_owner < <(
        awk -F '\t' -v exact="${path}" \
            '$1 == exact { count++; hash=$2; mode=$3; owner=$4 } END { if (count == 1) print hash "\t" mode "\t" owner }' \
            "${candidate}/install.manifest"
    )
    [[ ${expected_hash} =~ ^[0-9a-f]{64}$ && ${expected_mode} =~ ^0[0-7]{3}$ && \
        ${expected_owner} == "${EXPECTED_UID}:${EXPECTED_GID}" ]] || return 1
    actual_hash=$(sha256sum -- "${path}"); actual_hash=${actual_hash%% *}
    actual_mode=0$(stat -c %a -- "${path}")
    actual_owner=$(stat -c '%u:%g' -- "${path}")
    [[ ${actual_hash} == "${expected_hash}" && ${actual_mode} == "${expected_mode}" && \
        ${actual_owner} == "${expected_owner}" ]]
}

verify_external_migration_source() {
    local candidate=$1 qa=${INSTALL_QA_ROOT}/${HOOK_BASENAME}
    if [[ -e ${LIBEXEC_ROOT} || -L ${LIBEXEC_ROOT} ]]; then
        verify_directory "${LIBEXEC_ROOT}" "${EXPECTED_UID}" "${EXPECTED_GID}" 0755
        bootstrap_tree_matches "${LIBEXEC_ROOT}" || \
            legacy_bootstrap_tree_matches "${LIBEXEC_ROOT}" || \
            legacy_python_bootstrap_tree_matches "${LIBEXEC_ROOT}" || \
            manifest_bootstrap_tree_matches "${LIBEXEC_ROOT}" "${candidate}" || \
            legacy_helper_tree_matches "${LIBEXEC_ROOT}" "${candidate}" || \
            fail 'fixed libexec tree is neither the reviewed bootstrap nor the active generation implementation'
    fi
    if [[ -e ${SHARE_ROOT} || -L ${SHARE_ROOT} ]]; then
        [[ -L ${SHARE_ROOT} && $(readlink -- "${SHARE_ROOT}") == "${FRAMEWORK_CURRENT}/share" ]] || \
            legacy_schema_tree_matches "${SHARE_ROOT}" "${candidate}" || \
            fail 'fixed share tree is neither the reviewed indirection nor the active generation schema tree'
    fi
    if [[ -e ${qa} || -L ${qa} ]]; then
        if [[ -f ${qa} && ! -L ${qa} ]]; then
            cmp -s -- <(render_qa_bootstrap) "${qa}" || \
                manifest_external_file_matches "${qa}" "${candidate}" || \
                cmp -s -- "${candidate}/qa/${HOOK_BASENAME}" "${qa}" || \
                fail 'fixed QA hook is neither the reviewed bootstrap nor the active generation implementation'
        else
            fail 'fixed QA hook has an unmanaged filesystem type'
        fi
    fi
    if [[ -e ${MANIFEST} || -L ${MANIFEST} ]]; then
        [[ -L ${MANIFEST} && $(readlink -- "${MANIFEST}") == \
            "${FRAMEWORK_CURRENT}/install.manifest" ]] || \
            { [[ -f ${MANIFEST} && ! -L ${MANIFEST} ]] && \
                cmp -s -- "${candidate}/install.manifest" "${MANIFEST}"; } || \
            fail 'external manifest is neither the reviewed indirection nor the active generation manifest'
    fi
}

atomic_publish_entry() {
    local prepared=$1 destination=$2 parent
    parent=${destination%/*}
    [[ ${prepared%/*} == "${parent}" ]] || fail 'atomic publication staging entry is not in the destination directory'
    if [[ -e ${destination} || -L ${destination} ]]; then
        /usr/bin/mv --exchange --no-copy -T -- "${prepared}" "${destination}"
    else
        /usr/bin/mv --no-copy -T -- "${prepared}" "${destination}"
    fi
    sync_path "${parent}"
    rm -rf -- "${prepared}"
    sync_path "${parent}"
}

install_external_indirections() {
    local index path stage
    # The next operation exchanges the complete fixed helper directory.  A
    # recognized v1 Python bootstrap is therefore upgraded atomically, never
    # one helper at a time; Portage quiescence and project/generation locks are
    # held by the caller throughout this boundary.
    failure_point before-helper-bootstrap-exchange
    safe_mkdir 0755 "${LIBEXEC_ROOT%/*}"
    stage=${LIBEXEC_ROOT}.partial.$$
    rm -rf -- "${stage}"
    mkdir -p -- "${stage}/bolt" "${stage}/pgo" "${stage}/recovery" \
        "${stage}/scripts/optimization/lib" "${stage}/scripts/optimization/verify"
    for index in "${!HELPER_RELATIVE[@]}"; do
        path=${stage}/${HELPER_RELATIVE[index]}
        render_helper_bootstrap "${HELPER_RELATIVE[index]}" >"${path}"
        chmod 0755 -- "${path}"
    done
    normalize_tree "${stage}"
    sync_tree "${stage}"
    atomic_publish_entry "${stage}" "${LIBEXEC_ROOT}"
    failure_point after-helper-bootstrap-exchange

    safe_mkdir 0755 "${SHARE_ROOT%/*}"
    stage=${SHARE_ROOT}.partial.$$
    rm -rf -- "${stage}"
    ln -s -- "${FRAMEWORK_CURRENT}/share" "${stage}"
    atomic_publish_entry "${stage}" "${SHARE_ROOT}"

    safe_mkdir 0755 "${INSTALL_QA_ROOT}"
    stage=${INSTALL_QA_ROOT}/${HOOK_BASENAME}.partial.$$
    rm -rf -- "${stage}"
    render_qa_bootstrap >"${stage}"
    chown "${EXPECTED_UID}:${EXPECTED_GID}" -- "${stage}"
    chmod 0644 -- "${stage}"
    sync_path "${stage}"
    atomic_publish_entry "${stage}" "${INSTALL_QA_ROOT}/${HOOK_BASENAME}"

    safe_mkdir 0700 "${STATE_ROOT}"
    stage=${MANIFEST}.partial.$$
    rm -rf -- "${stage}"
    ln -s -- "${FRAMEWORK_CURRENT}/install.manifest" "${stage}"
    atomic_publish_entry "${stage}" "${MANIFEST}"

    safe_mkdir 0755 "${ETC_PORTAGE%/*}"
    stage=${ETC_PORTAGE}.partial.$$
    rm -rf -- "${stage}"
    ln -s -- "${FRAMEWORK_CURRENT}/portage" "${stage}"
    atomic_publish_entry "${stage}" "${ETC_PORTAGE}"

    rm -f -- "${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt"
    sync_path "${INSTALL_QA_ROOT}"
}

verify_external_indirections() {
    local index schema qa=${INSTALL_QA_ROOT}/${HOOK_BASENAME} temporary
    [[ -L ${ETC_PORTAGE} && $(readlink -- "${ETC_PORTAGE}") == "${FRAMEWORK_CURRENT}/portage" && \
        $(stat -c '%u:%g' -- "${ETC_PORTAGE}") == "${EXPECTED_UID}:${EXPECTED_GID}" ]] || \
        fail '/etc/portage is not atomically bound to framework-current/portage'
    [[ -L ${SHARE_ROOT} && $(readlink -- "${SHARE_ROOT}") == "${FRAMEWORK_CURRENT}/share" && \
        $(stat -c '%u:%g' -- "${SHARE_ROOT}") == "${EXPECTED_UID}:${EXPECTED_GID}" ]] || \
        fail 'fixed schema namespace is not bound to framework-current/share'
    [[ -L ${MANIFEST} && $(readlink -- "${MANIFEST}") == \
        "${FRAMEWORK_CURRENT}/install.manifest" && \
        $(stat -c '%u:%g' -- "${MANIFEST}") == "${EXPECTED_UID}:${EXPECTED_GID}" ]] || \
        fail 'external manifest is not bound to framework-current/install.manifest'
    verify_directory "${LIBEXEC_ROOT}" "${EXPECTED_UID}" "${EXPECTED_GID}" 0755
    bootstrap_tree_matches "${LIBEXEC_ROOT}" || fail 'fixed helper bootstrap tree differs'
    verify_regular_trusted "${qa}" 0644
    temporary=$(mktemp "${BASE}/.qa-bootstrap-check.XXXXXXXX")
    render_qa_bootstrap >"${temporary}"
    cmp -s -- "${temporary}" "${qa}" || {
        rm -f -- "${temporary}"
        fail 'fixed QA bootstrap differs'
    }
    rm -f -- "${temporary}"
    [[ ! -e ${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt && \
        ! -L ${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt ]] || \
        fail 'obsolete early BOLT QA hook remains installed'
    for index in "${!HELPER_RELATIVE[@]}"; do
        verify_regular_trusted "${LIBEXEC_ROOT}/${HELPER_RELATIVE[index]}" 0755
    done
    for schema in package-state.schema.json artifact-state.schema.json \
        final-system-state.schema.json; do
        [[ -f ${SHARE_ROOT}/schema/${schema} ]] || fail "active schema is absent: ${schema}"
    done
}

activate_framework_target() {
    local target=$1 temporary=${FRAMEWORK_CURRENT}.partial.$$
    rm -f -- "${temporary}"
    ln -s -- "${target}" "${temporary}"
    mv -fT -- "${temporary}" "${FRAMEWORK_CURRENT}"
    sync_path "${BASE}"
}

cleanup_stale_publication_debris() {
    local pattern entry
    local -a patterns=(
        "${LIBEXEC_ROOT}.partial.*"
        "${SHARE_ROOT}.partial.*"
        "${ETC_PORTAGE}.partial.*"
        "${MANIFEST}.partial.*"
        "${INSTALL_QA_ROOT}/${HOOK_BASENAME}.partial.*"
        "${FRAMEWORK_CURRENT}.partial.*"
        "${BASE}/framework-*.partial.*"
        "${BASE}/.framework-expected-manifest.*"
        "${BASE}/.framework-source-snapshot.*"
        "${BASE}/.source-git-contract.*"
        "${BASE}/.extended-metadata-audit.*"
        "${BASE}/.candidate-inventory-check.*"
        "${BASE}/.framework-check-manifest.*"
        "${BASE}/.framework-rollback.*"
        "${BASE}/.helper-bootstrap-check.*"
        "${BASE}/.qa-bootstrap-check.*"
        "${BASE}/.qa-bootstrap-compatibility.*"
        "${BASE}/.portage-migration-guard-check.*"
        "${BASE}/.framework-activation-expected.*"
        "${ACTIVATION_JOURNAL}.partial.*"
    )
    for pattern in "${patterns[@]}"; do
        while IFS= read -r entry; do
            rm -rf -- "${entry}"
        done < <(compgen -G "${pattern}" || true)
    done
}

verify_no_stale_publication_debris() {
    local pattern
    local -a matches=()
    local -a patterns=(
        "${LIBEXEC_ROOT}.partial.*"
        "${SHARE_ROOT}.partial.*"
        "${ETC_PORTAGE}.partial.*"
        "${MANIFEST}.partial.*"
        "${INSTALL_QA_ROOT}/${HOOK_BASENAME}.partial.*"
        "${FRAMEWORK_CURRENT}.partial.*"
        "${BASE}/framework-*.partial.*"
        "${BASE}/.framework-expected-manifest.*"
        "${BASE}/.framework-source-snapshot.*"
        "${BASE}/.source-git-contract.*"
        "${BASE}/.extended-metadata-audit.*"
        "${BASE}/.candidate-inventory-check.*"
        "${BASE}/.framework-check-manifest.*"
        "${BASE}/.framework-rollback.*"
        "${BASE}/.helper-bootstrap-check.*"
        "${BASE}/.qa-bootstrap-check.*"
        "${BASE}/.qa-bootstrap-compatibility.*"
        "${BASE}/.portage-migration-guard-check.*"
        "${BASE}/.framework-activation-expected.*"
        "${ACTIVATION_JOURNAL}.partial.*"
    )
    for pattern in "${patterns[@]}"; do
        matches=()
        mapfile -t matches < <(compgen -G "${pattern}" || true)
        ((${#matches[@]} == 0)) || \
            fail "stale framework publication debris remains: ${matches[0]}"
    done
}

release_locks_for_check_reexec() {
    local fd_to_close
    for fd_to_close in "${HELD_BOLT_LOCK_FDS[@]}" "${HELD_PROJECT_LOCK_FDS[@]}"; do
        [[ ${fd_to_close} =~ ^[0-9]+$ ]] || fail 'internal lock descriptor identity is invalid'
        exec {fd_to_close}>&-
    done
    if [[ -n ${INSTALLER_LOCK_FD} ]]; then
        fd_to_close=${INSTALLER_LOCK_FD}
        [[ ${fd_to_close} =~ ^[0-9]+$ ]] || fail 'internal installer lock descriptor identity is invalid'
        exec {fd_to_close}>&-
    fi
    HELD_BOLT_LOCK_FDS=()
    HELD_PROJECT_LOCK_FDS=()
    INSTALLER_LOCK_FD=
}

rollback_install() {
    ((ROLLBACK_REQUIRED)) || return 0
    set +e
    printf 'ROLLBACK: restoring the pre-install framework after an error or signal\n' >&2
    if [[ ${PREVIOUS_TARGET} != none ]]; then
        activate_framework_target "${PREVIOUS_TARGET}"
    fi
    ROLLBACK_REQUIRED=0
}

cleanup() {
    local status=$?
    local exchange_probe
    if ((status != 0 || ! COMMITTED)); then rollback_install; fi
    if ((status != 0 || ! COMMITTED)) && ((CREATED_CANDIDATE)) && \
        [[ -n ${CANDIDATE_FINAL} && (! -L ${FRAMEWORK_CURRENT} || \
            $(readlink -- "${FRAMEWORK_CURRENT}") != "${CANDIDATE_FINAL}") ]]; then
        rm -rf -- "${CANDIDATE_FINAL}"
    fi
    [[ -n ${SNAPSHOT} ]] && rm -rf -- "${SNAPSHOT}"
    [[ -n ${SOURCE_CONTRACT_TEMP} ]] && rm -rf -- "${SOURCE_CONTRACT_TEMP}"
    [[ -n ${METADATA_AUDIT_TEMP} ]] && \
        rm -f -- "${METADATA_AUDIT_TEMP}" "${METADATA_AUDIT_TEMP}.stderr"
    [[ -n ${CANDIDATE_INVENTORY_TEMP} ]] && \
        rm -f -- "${CANDIDATE_INVENTORY_TEMP}"
    [[ -n ${CANDIDATE_STAGE} ]] && rm -rf -- "${CANDIDATE_STAGE}"
    [[ -n ${EXPECTED_MANIFEST:-} ]] && rm -f -- "${EXPECTED_MANIFEST}"
    [[ -n ${EXPECTED_CHECK_MANIFEST} ]] && rm -f -- "${EXPECTED_CHECK_MANIFEST}"
    for exchange_probe in "${EXCHANGE_PROBE_ROOTS[@]}"; do
        [[ -n ${exchange_probe} ]] && rm -rf -- "${exchange_probe}"
    done
    exit "${status}"
}

signal_exit() {
    printf 'ERROR: framework installer interrupted by signal %s\n' "$1" >&2
    exit 128
}

trap cleanup EXIT
trap 'signal_exit INT' INT
trap 'signal_exit TERM' TERM
trap 'signal_exit HUP' HUP

preflight_destination_ancestors
preflight_atomic_exchange_destinations
verify_bootstrap_identity
verify_jq
if [[ ${MODE} == install ]]; then
    safe_mkdir_owner "${LOCK_DIRECTORY_MODE}" "${EXPECTED_UID}" "${LOCK_GID}" "${LOCK_PATH%/*}"
    create_lock_if_absent "${LOCK_PATH}"
    open_verified_lock_descriptor "${LOCK_PATH}" INSTALLER_LOCK_FD
    flock -n -x "${INSTALLER_LOCK_FD}" || \
        fail 'another framework installer holds the publication lock'
    [[ ! -s /proc/${BASHPID}/fd/${INSTALLER_LOCK_FD} ]] || \
        fail 'framework publication lock payload is unexpectedly nonempty'
    verify_profile_transaction_authorization
    open_project_lock "${PROJECT_LOCK_PATH}" exclusive
    open_project_lock "${GENERATION_LOCK_PATH}" exclusive
else
    verify_directory "${LOCK_PATH%/*}" "${EXPECTED_UID}" "${LOCK_GID}" "${LOCK_DIRECTORY_MODE}"
    verify_lock_file "${LOCK_PATH}"
    open_verified_lock_descriptor "${LOCK_PATH}" INSTALLER_LOCK_FD
    flock -n -s "${INSTALLER_LOCK_FD}" || \
        fail 'another framework installer holds the publication lock'
    [[ ! -s /proc/${BASHPID}/fd/${INSTALLER_LOCK_FD} ]] || \
        fail 'framework publication lock payload is unexpectedly nonempty'
    verify_profile_transaction_authorization
    open_project_lock "${PROJECT_LOCK_PATH}" shared
    open_project_lock "${GENERATION_LOCK_PATH}" shared
fi
portage_quiescent
hold_bolt_locks
[[ ${MODE} == check ]] || cleanup_stale_publication_debris
validate_legacy_migration
get_previous_target

if [[ ${MODE} == check ]]; then
    verify_no_stale_publication_debris
    [[ ! -e ${ACTIVATION_JOURNAL} && ! -L ${ACTIVATION_JOURNAL} ]] || \
        fail 'framework activation journal remains pending'
    [[ -L ${FRAMEWORK_CURRENT} ]] || fail 'framework-current is absent'
    ACTIVE_TARGET=$(readlink -- "${FRAMEWORK_CURRENT}")
    [[ ${ACTIVE_TARGET} == "${BASE}"/framework-[0-9a-f]* ]] || fail 'framework-current target is unmanaged'
    verify_external_indirections
    snapshot_inputs
    verify_source_symlinks
    verify_candidate "${ACTIVE_TARGET}"
    ACTIVE_PREVIOUS=$(awk -F= '$1 == "previous_generation" { print substr($0, index($0, "=") + 1) }' \
        "${ACTIVE_TARGET}/install.manifest")
    [[ -n ${ACTIVE_PREVIOUS} && $(grep -c '^previous_generation=' \
        "${ACTIVE_TARGET}/install.manifest") == 1 ]] || fail 'active manifest previous generation is invalid'
    FRAMEWORK_AGGREGATE=$(printf '%s\n' \
        'gentoo-optimization-framework-v4' \
        "installer=${INSTALLER_SHA256}" \
        "source=${SOURCE_AGGREGATE}" \
        "git_commit=${GIT_COMMIT}" \
        "git_worktree=${GIT_DIRTY}" \
        "git_status=${SOURCE_STATUS}" \
        "jq=${JQ_SHA256}:${JQ_VERSION}" \
        "generated_policy=${GENERATED_POLICY_ID}" \
        "frozen_inventory=${FROZEN_INVENTORY_SHA256}" \
        "previous=${ACTIVE_PREVIOUS}" | sha256sum | awk '{print $1}')
    [[ ${ACTIVE_TARGET} == "${BASE}/framework-${FRAMEWORK_AGGREGATE}" ]] || \
        fail 'active generation identity does not match the reviewed input snapshot'
    CANDIDATE_INVENTORY_SHA=$(sha256sum -- "${ACTIVE_TARGET}/.candidate-inventory" | awk '{print $1}')
    EXPECTED_CHECK_MANIFEST=$(mktemp "${BASE}/.framework-check-manifest.XXXXXXXX")
    render_manifest "${CANDIDATE_INVENTORY_SHA}" "${ACTIVE_TARGET}" "${ACTIVE_PREVIOUS}" \
        >"${EXPECTED_CHECK_MANIFEST}"
    cmp -s -- "${EXPECTED_CHECK_MANIFEST}" "${ACTIVE_TARGET}/install.manifest" || \
        fail 'active generation manifest is not the strict canonical manifest for this checkout'
    cmp -s -- "${ACTIVE_TARGET}/install.manifest" "${MANIFEST}" || \
        fail 'active manifest indirection does not expose the active generation manifest'
    rm -f -- "${EXPECTED_CHECK_MANIFEST}"
    EXPECTED_CHECK_MANIFEST=
    assert_global_qa_order
    for schema in package-state.schema.json artifact-state.schema.json \
        final-system-state.schema.json; do
        cmp -s -- "${ACTIVE_TARGET}/share/schema/${schema}" \
            "${SHARE_ROOT}/schema/${schema}" || fail "active schema differs: ${schema}"
    done
    verify_runtime_namespaces
    verify_live_overlay_resolution
    COMMITTED=1
    printf 'PASS: root-owned Phase 2 framework check verified (%s)\n' "${MANIFEST}"
    exit 0
fi

safe_mkdir 0755 "${BASE}"
safe_mkdir 0700 "${STATE_ROOT}"
safe_mkdir_owner "${LOCK_DIRECTORY_MODE}" "${EXPECTED_UID}" "${LOCK_GID}" \
    "${PROFILE_TRANSACTION_ROOT}"
safe_mkdir 0755 "${GENERATIONS_ROOT}"
safe_mkdir 0700 "${CACHE_ROOT}"
for cache_dir in inputs outputs perf fdata diagnostics locks; do
    safe_mkdir 0700 "${CACHE_ROOT}/${cache_dir}"
done
safe_mkdir_owner 0750 "${EXPECTED_UID}" "${PORTAGE_GID}" "${PGO_CACHE}"
for pgo_backend in clang-ir clang-sample ebuild-native gcc go rust; do
    safe_mkdir_owner 0750 "${EXPECTED_UID}" "${PORTAGE_GID}" "${PGO_CACHE}/${pgo_backend}"
done
safe_mkdir 0755 "${PGO_RAW%/*}"
safe_mkdir 0755 "${PGO_RAW}"
snapshot_inputs
verify_source_symlinks

FRAMEWORK_AGGREGATE=$(printf '%s\n' \
    'gentoo-optimization-framework-v4' \
    "installer=${INSTALLER_SHA256}" \
    "source=${SOURCE_AGGREGATE}" \
    "git_commit=${GIT_COMMIT}" \
    "git_worktree=${GIT_DIRTY}" \
    "git_status=${SOURCE_STATUS}" \
    "jq=${JQ_SHA256}:${JQ_VERSION}" \
    "generated_policy=${GENERATED_POLICY_ID}" \
    "frozen_inventory=${FROZEN_INVENTORY_SHA256}" \
    "previous=${PREVIOUS_TARGET}" | sha256sum | awk '{print $1}')
CANDIDATE_FINAL=${BASE}/framework-${FRAMEWORK_AGGREGATE}
CANDIDATE_STAGE=${CANDIDATE_FINAL}.partial.$$
EXPECTED_MANIFEST=${BASE}/.framework-expected-manifest.$$

if [[ ${PREVIOUS_TARGET} != none && -f ${PREVIOUS_TARGET}/install.manifest ]] && \
    grep -Fxq "installer_sha256=${INSTALLER_SHA256}" "${PREVIOUS_TARGET}/install.manifest" && \
    grep -Fxq "source_aggregate_sha256=${SOURCE_AGGREGATE}" "${PREVIOUS_TARGET}/install.manifest" && \
    grep -Fxq "git_commit=${GIT_COMMIT}" "${PREVIOUS_TARGET}/install.manifest" && \
    grep -Fxq "git_status_sha256=${SOURCE_STATUS}" "${PREVIOUS_TARGET}/install.manifest" && \
    grep -Fxq "jq_sha256=${JQ_SHA256}" "${PREVIOUS_TARGET}/install.manifest" && \
    grep -Fxq "jq_version=${JQ_VERSION}" "${PREVIOUS_TARGET}/install.manifest" && \
    grep -Fxq "generated_policy=${GENERATED_POLICY_ID}" "${PREVIOUS_TARGET}/install.manifest" && \
    grep -Fxq "frozen_inventory_sha256=${FROZEN_INVENTORY_SHA256}" \
        "${PREVIOUS_TARGET}/install.manifest"; then
    printf 'INFO: reviewed inputs already match the active generation; repairing stable indirections and running strict check\n'
    require_stable_bootstrap_compatibility
    verify_external_migration_source "${PREVIOUS_TARGET}"
    discard_source_snapshot
    install_external_indirections
    verify_external_indirections
    finish_first_activation_journal "${PREVIOUS_TARGET}"
    COMMITTED=1
    trap - EXIT INT TERM HUP
    discard_source_snapshot
    release_locks_for_check_reexec
    REEXEC_ARGS=("${SELF_PATH}" --check --source-root "${ROOT}")
    [[ -z ${GENERATED_POLICY_INPUT} ]] || REEXEC_ARGS+=(
        --generated-policy-generation "${GENERATED_POLICY_INPUT}"
    )
    [[ -z ${FROZEN_INVENTORY_INPUT} ]] || REEXEC_ARGS+=(
        --frozen-inventory "${FROZEN_INVENTORY_INPUT}"
    )
    if [[ -n ${TEST_ROOT} ]]; then
        REEXEC_ARGS+=(--test-root "${TEST_ROOT}")
    fi
    exec env -u GENTOO_OPT_INSTALLER_FAIL_AT -u GENTOO_OPT_INSTALLER_PAUSE_AT \
        "${REEXEC_ARGS[@]}"
fi

rm -rf -- "${CANDIDATE_STAGE}"
mkdir -p -- "${CANDIDATE_STAGE}/portage" "${CANDIDATE_STAGE}/local-overlay" \
    "${CANDIDATE_STAGE}/generated-policy" "${CANDIDATE_STAGE}/libexec/bolt" \
    "${CANDIDATE_STAGE}/libexec/pgo" \
    "${CANDIDATE_STAGE}/libexec/scripts/optimization/lib" \
    "${CANDIDATE_STAGE}/libexec/scripts/optimization/verify" \
    "${CANDIDATE_STAGE}/libexec/recovery" \
    "${CANDIDATE_STAGE}/share/schema" "${CANDIDATE_STAGE}/qa"
cp -a -- "${SNAPSHOT}/portage/." "${CANDIDATE_STAGE}/portage/"
cp -a -- "${SNAPSHOT}/local-overlay/." "${CANDIDATE_STAGE}/local-overlay/"
render_bound_portage_bashrc "${CANDIDATE_FINAL}" >"${CANDIDATE_STAGE}/portage/bashrc"
rm -rf -- "${CANDIDATE_STAGE}/generated-policy"
cp -a -- "${SNAPSHOT}/generated-policy" "${CANDIDATE_STAGE}/generated-policy"
if [[ ${GENERATED_POLICY_ID} != empty-v1 ]]; then
    install -m 0600 -T -- "${SNAPSHOT}/frozen-inventory.json" \
        "${CANDIDATE_STAGE}/generated-policy/.frozen-inventory.json"
fi
ln -s -- ../../generated-policy/package.env \
    "${CANDIDATE_STAGE}/portage/package.env/99-generated-optimization"
ln -s -- ../../../generated-policy/env \
    "${CANDIDATE_STAGE}/portage/env/optimization/generated"
for index in "${!HELPER_RELATIVE[@]}"; do
    install -m 0755 -T -- "${SNAPSHOT}/${HELPER_SOURCE_RELATIVE[index]}" \
        "${CANDIDATE_STAGE}/libexec/${HELPER_RELATIVE[index]}"
done
install -m 0644 -T -- "${SNAPSHOT}/optimization/schema/package-state.schema.json" \
    "${CANDIDATE_STAGE}/share/schema/package-state.schema.json"
install -m 0644 -T -- "${SNAPSHOT}/optimization/schema/artifact-state.schema.json" \
    "${CANDIDATE_STAGE}/share/schema/artifact-state.schema.json"
install -m 0644 -T -- "${SNAPSHOT}/optimization/schema/final-system-state.schema.json" \
    "${CANDIDATE_STAGE}/share/schema/final-system-state.schema.json"
install -m 0644 -T -- "${SNAPSHOT}/portage/install-qa-check.d/${HOOK_BASENAME}" \
    "${CANDIDATE_STAGE}/qa/${HOOK_BASENAME}"
printf '%s\n' "${SOURCE_AGGREGATE}" >"${CANDIDATE_STAGE}/portage/.gentoo-optimization-source-hash"
printf '%s\n' "${SOURCE_AGGREGATE}" >"${CANDIDATE_STAGE}/local-overlay/.gentoo-optimization-source-hash"
printf '%s\n' "${GENERATED_POLICY_ID}" >"${CANDIDATE_STAGE}/generated-policy/.identity"
normalize_tree "${CANDIDATE_STAGE}"
chmod 0600 -- "${CANDIDATE_STAGE}/portage/.gentoo-optimization-source-hash" \
    "${CANDIDATE_STAGE}/local-overlay/.gentoo-optimization-source-hash"
if [[ -n ${TEST_ROOT} ]]; then
    chmod 0600 -- "${CANDIDATE_STAGE}/generated-policy/.identity"
else
    chmod 0640 -- "${CANDIDATE_STAGE}/generated-policy/.identity"
fi
if [[ ${GENERATED_POLICY_ID} != empty-v1 ]]; then
    chmod 0600 -- "${CANDIDATE_STAGE}/generated-policy/.frozen-inventory.json"
fi
candidate_inventory "${CANDIDATE_STAGE}" >"${CANDIDATE_STAGE}/.candidate-inventory"
chmod 0600 -- "${CANDIDATE_STAGE}/.candidate-inventory"
CANDIDATE_INVENTORY_SHA=$(sha256sum -- "${CANDIDATE_STAGE}/.candidate-inventory" | awk '{print $1}')
render_manifest "${CANDIDATE_INVENTORY_SHA}" "${CANDIDATE_FINAL}" "${PREVIOUS_TARGET}" \
    >"${CANDIDATE_STAGE}/install.manifest"
if [[ -n ${TEST_ROOT} ]]; then
    chmod 0600 -- "${CANDIDATE_STAGE}/install.manifest"
else
    chmod 0640 -- "${CANDIDATE_STAGE}/install.manifest"
fi
chown -R "${EXPECTED_UID}:${EXPECTED_GID}" -- "${CANDIDATE_STAGE}"
if [[ -z ${TEST_ROOT} ]]; then
    chown "${EXPECTED_UID}:${PORTAGE_GID}" -- \
        "${CANDIDATE_STAGE}/install.manifest" \
        "${CANDIDATE_STAGE}/generated-policy/.identity"
fi
render_manifest "${CANDIDATE_INVENTORY_SHA}" "${CANDIDATE_FINAL}" "${PREVIOUS_TARGET}" \
    >"${EXPECTED_MANIFEST}"
verify_candidate "${CANDIDATE_STAGE}" "${EXPECTED_MANIFEST}"
assert_global_qa_order
sync_tree "${CANDIDATE_STAGE}"
if [[ -e ${CANDIDATE_FINAL} || -L ${CANDIDATE_FINAL} ]]; then
    verify_candidate "${CANDIDATE_FINAL}" "${EXPECTED_MANIFEST}"
    rm -rf -- "${CANDIDATE_STAGE}"
    CANDIDATE_STAGE=
else
    mv -T -- "${CANDIDATE_STAGE}" "${CANDIDATE_FINAL}"
    CANDIDATE_STAGE=
    CREATED_CANDIDATE=1
fi
sync_path "${BASE}"
verify_candidate "${CANDIDATE_FINAL}" "${EXPECTED_MANIFEST}"
failure_point after-candidate

if [[ ${PREVIOUS_TARGET} == none ]]; then
    # The first migration has no old framework-current to dispatch through.
    # Atomically replace /etc/portage with a root-owned fail-closed guard first.
    # Only then publish and fsync the durable journal, before making current
    # visible. A power loss at any following instruction therefore cannot start
    # a build through the mutable legacy checkout or a partial framework.
    verify_external_migration_source "${CANDIDATE_FINAL}"
    install_portage_migration_guard
    failure_point after-first-migration-guard
    begin_first_activation_journal "${CANDIDATE_FINAL}"
    failure_point after-activation-journal
    activate_framework_target "${CANDIDATE_FINAL}"
    failure_point after-bootstrap-activation
else
    # On upgrades, publish only generation-independent indirections while they
    # still dispatch to the old current generation.  The final rename below is
    # therefore the sole behavior-changing operation.
    require_stable_bootstrap_compatibility
    verify_external_migration_source "${PREVIOUS_TARGET}"
fi
install_external_indirections
verify_external_indirections
failure_point after-helpers

# Recheck quiescence immediately before the only activation point.  Existing
# transaction locks are still held from the first check.
portage_quiescent
assert_global_qa_order
failure_point before-activation
if [[ ${PREVIOUS_TARGET} != none ]]; then
    ROLLBACK_REQUIRED=1
    activate_framework_target "${CANDIDATE_FINAL}"
fi
failure_point after-activation

[[ $(readlink -- "${FRAMEWORK_CURRENT}") == "${CANDIDATE_FINAL}" ]] || \
    fail 'framework-current activation target differs'
verify_external_indirections
verify_candidate "${CANDIDATE_FINAL}" "${MANIFEST}"
verify_live_overlay_resolution
finish_first_activation_journal "${CANDIDATE_FINAL}"
ROLLBACK_REQUIRED=0
COMMITTED=1
rm -f -- "${EXPECTED_MANIFEST}"
printf 'PASS: root-owned Phase 2 framework install verified (%s)\n' "${MANIFEST}"

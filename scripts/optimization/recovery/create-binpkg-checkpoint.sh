#!/usr/bin/env bash
# shellcheck disable=SC2016 # jq programs are intentionally single-quoted.
# Create an exact, package-managed Gentoo binary-package recovery checkpoint.
#
# The source snapshot is selected through the same root-owned symlink that is
# replaced at the end of a successful transaction.  The caller must bind the
# source target and Packages digest explicitly.  Only exact installed CPVs that
# form the complete source-to-live delta may be passed to quickpkg.

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly PROGRAM=${0##*/}
readonly COORDINATOR_PID=${BASHPID}

FIXTURE_MODE=0
ACTION=create
FIXTURE_ROOT=
FIXTURE_OWNER=
TOOL_ROOT=
VDB_OVERRIDE=
CACHE_PARENT_OVERRIDE=
DURABLE_PARENT_OVERRIDE=
REPORT_PARENT_OVERRIDE=
STATE_PARENT_OVERRIDE=
LOCK_OVERRIDE=
SELECTOR_OVERRIDE=
VERIFIER_OVERRIDE=
MAKE_CONF_OVERRIDE=
EXPECTED_SOURCE_TARGET=
EXPECTED_SOURCE_PACKAGES_SHA256=
EXPECTED_VERIFIER_SHA256=
CHECKPOINT_ID=
LOCK_FD=
FRAMEWORK_LOCK_FD=
PROJECT_LOCK_FD=
GENERATION_LOCK_FD=
FRAMEWORK_LOCK_GID=
REPORT_READY=0
ACTIVATION_STARTED=0
ACTIVATION_COMPLETE=0
PORTAGE_LOCK_PID=
PORTAGE_LOCK_STARTTIME=
MAKE_CONF_OVERLAY_ACTIVE=0
IN_FAILURE_TRAP=0
ACTIVE_CHILD_PID=
ACTIVE_CHILD_STARTTIME=
TRACKED_STATUS=0
VERIFIER_STATUS=0
VERIFY_COUNTS=
JOURNAL_SEQUENCE=0
CURRENT_PHASE=bootstrap
EXPECTED_SELECTOR_IDENTITY=
EXPECTED_SOURCE_IDENTITY=
EXPECTED_SOURCE_PACKAGES_IDENTITY=
EXPECTED_VERIFIER_IDENTITY=
EXPECTED_MAKE_CONF_IDENTITY=
EXPECTED_MAKE_CONF_SHA256=
ACTIVATION_INTENT_SHA256=
REPORT=
STATE=
CACHE=
DURABLE=
CACHE_PARTIAL=
DURABLE_PARTIAL=
SELECTOR_PARTIAL=
SELECTOR_WITNESS=
REPORT_PARTIAL=
ACTIVATION_INTENT=
ACTIVATION_RECEIPT=
PREPARED_SELECTOR_RECORD=
STATE_PREPARED=
STATE_ACTIVATED=
STATE_RESTORED=
SELF=
RESTORE_CPV=
RETRY_INTERRUPTED_RESTORE=0
VALIDATED_RETRY_COUNT=0

declare -a ATOMS=()
declare -a ATOM_CPVS=()
declare -a TOOL_NAMES=(
    bash chmod chown cmp cp date emaint env find findmnt flock getent install jq ln mount mv
    emerge portageq python3 qcheck quickpkg readlink rm setsid sha256sum sleep sort stat sync timeout umount
    unshare zstd
)
declare -A TOOL=()
declare -a TOOL_IDENTITY_LINES=()

usage() {
    cat <<EOF
Usage:
  ${PROGRAM} [options] CHECKPOINT_ID =category/package-version [...]

Required source binding:
  --expected-source-target PATH
  --expected-source-packages-sha256 SHA256
  --expected-verifier-sha256 SHA256

Production defaults:
  VDB                 /var/db/pkg
  source/selector     /var/cache/gentoo-optimization/binpkgs/critical-current
  cache generations   /var/cache/gentoo-optimization/binpkgs
  durable generations /var/lib/gentoo-optimization/recovery/binpkgs
  evidence            /var/lib/gentoo-optimization/reports
  state               /var/lib/gentoo-optimization/state/project
  transaction lock    /var/lib/gentoo-optimization/state/project/binpkg-checkpoint.lock

Options:
  --reconcile                        Reconcile an interrupted activation.
  --finalize-offline-restore         Publish the terminal offline-restore proof.
  --restore-cpv CATEGORY/PKG-VERSION Exact CPV restored by the supervised finalizer.
  --retry-interrupted-offline-restore
                                      Explicitly authorize another exact emerge
                                      after an ambiguous interrupted attempt.
  --expected-source-target PATH       Exact absolute selector target.
  --expected-source-packages-sha256 H Exact source Packages SHA-256.
  --expected-verifier-sha256 H       Exact immutable direct-verifier SHA-256.
  --fixture-mode                      Permit a non-root fake-root test.
  --fixture-root PATH                 Private fake root (fixture mode only).
  --fixture-owner UID:GID             Expected fake-root owner.
  --tool-root PATH                    Prefix for logical /usr tools.
  --vdb PATH                          Fixture VDB override.
  --cache-parent PATH                 Fixture cache-parent override.
  --durable-parent PATH               Fixture durable-parent override.
  --report-parent PATH                Fixture evidence-parent override.
  --state-parent PATH                 Fixture state-parent override.
  --lock PATH                         Fixture lock override.
  --selector PATH                     Fixture selector override.
  --verifier PATH                     Fixture verifier override.
  --make-conf PATH                    Fixture make.conf override.
  -h, --help                          Show this help.

The fixture-only path switches are rejected in production mode.  Creation,
reconciliation, and finalization use the same checkpoint ID and exact source,
verifier, and delta bindings.  Reconciliation is idempotent and is the only
supported way to complete a transaction interrupted after activation intent.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

need_value() {
    (($# >= 2)) || die "option $1 requires a value"
}

while (($#)); do
    case $1 in
        --expected-source-target)
            need_value "$@"
            EXPECTED_SOURCE_TARGET=$2
            shift 2
            ;;
        --expected-source-packages-sha256)
            need_value "$@"
            EXPECTED_SOURCE_PACKAGES_SHA256=$2
            shift 2
            ;;
        --expected-verifier-sha256)
            need_value "$@"
            EXPECTED_VERIFIER_SHA256=$2
            shift 2
            ;;
        --reconcile)
            [[ ${ACTION} == create ]] || die 'checkpoint action may be selected only once'
            ACTION=reconcile
            shift
            ;;
        --finalize-offline-restore)
            [[ ${ACTION} == create ]] || die 'checkpoint action may be selected only once'
            ACTION=finalize
            shift
            ;;
        --restore-cpv)
            need_value "$@"
            RESTORE_CPV=$2
            shift 2
            ;;
        --retry-interrupted-offline-restore)
            RETRY_INTERRUPTED_RESTORE=1
            shift
            ;;
        --fixture-mode)
            FIXTURE_MODE=1
            shift
            ;;
        --fixture-root)
            need_value "$@"
            FIXTURE_ROOT=$2
            shift 2
            ;;
        --fixture-owner)
            need_value "$@"
            FIXTURE_OWNER=$2
            shift 2
            ;;
        --tool-root)
            need_value "$@"
            TOOL_ROOT=$2
            shift 2
            ;;
        --vdb)
            need_value "$@"
            VDB_OVERRIDE=$2
            shift 2
            ;;
        --cache-parent)
            need_value "$@"
            CACHE_PARENT_OVERRIDE=$2
            shift 2
            ;;
        --durable-parent)
            need_value "$@"
            DURABLE_PARENT_OVERRIDE=$2
            shift 2
            ;;
        --report-parent)
            need_value "$@"
            REPORT_PARENT_OVERRIDE=$2
            shift 2
            ;;
        --state-parent)
            need_value "$@"
            STATE_PARENT_OVERRIDE=$2
            shift 2
            ;;
        --lock)
            need_value "$@"
            LOCK_OVERRIDE=$2
            shift 2
            ;;
        --selector)
            need_value "$@"
            SELECTOR_OVERRIDE=$2
            shift 2
            ;;
        --verifier)
            need_value "$@"
            VERIFIER_OVERRIDE=$2
            shift 2
            ;;
        --make-conf)
            need_value "$@"
            MAKE_CONF_OVERRIDE=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            die "unknown option: $1"
            ;;
        *)
            if [[ -z ${CHECKPOINT_ID} ]]; then
                CHECKPOINT_ID=$1
            else
                ATOMS+=("$1")
            fi
            shift
            ;;
    esac
done

[[ -n ${CHECKPOINT_ID} ]] || die 'a checkpoint ID is required'
((${#ATOMS[@]} > 0)) || die 'at least one exact source-to-live delta atom is required'
[[ ${CHECKPOINT_ID} =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$ ]] || \
    die 'unsafe checkpoint ID'
[[ ${EXPECTED_SOURCE_PACKAGES_SHA256} =~ ^[0-9a-f]{64}$ ]] || \
    die 'an exact lowercase source Packages SHA-256 is required'
[[ ${EXPECTED_VERIFIER_SHA256} =~ ^[0-9a-f]{64}$ ]] || \
    die 'an exact lowercase immutable verifier SHA-256 is required'
if [[ ${ACTION} == finalize ]]; then
    [[ ${RESTORE_CPV} =~ ^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+$ ]] || \
        die 'offline finalization requires --restore-cpv with an exact CPV'
fi
[[ ${ACTION} == finalize || -z ${RESTORE_CPV} ]] || die '--restore-cpv requires --finalize-offline-restore'
[[ ${ACTION} == finalize || ${RETRY_INTERRUPTED_RESTORE} -eq 0 ]] || \
    die '--retry-interrupted-offline-restore requires --finalize-offline-restore'

# Do not permit inherited shell or compiler configuration to influence Portage
# frontends.  Every external command below is invoked through an absolute path.
unset BASH_ENV ENV CDPATH GLOBIGNORE GREP_OPTIONS POSIXLY_CORRECT
unset CC CXX CPP FC F77 F90 CFLAGS CXXFLAGS CPPFLAGS FCFLAGS FFLAGS LDFLAGS
unset MAKEFLAGS NINJAFLAGS FEATURES PORTAGE_BASHRC PORTAGE_BINHOST
export LC_ALL=C LANG=C TZ=UTC

if ((FIXTURE_MODE)); then
    [[ ${EUID} -ne 0 || -n ${FIXTURE_OWNER} ]] || :
    [[ -n ${FIXTURE_ROOT} && -n ${FIXTURE_OWNER} && -n ${TOOL_ROOT} ]] || \
        die 'fixture mode requires --fixture-root, --fixture-owner, and --tool-root'
    [[ ${FIXTURE_OWNER} =~ ^[0-9]+:[0-9]+$ ]] || die 'invalid fixture owner UID:GID'
    TRUST_UID=${FIXTURE_OWNER%%:*}
    TRUST_GID=${FIXTURE_OWNER##*:}
    VDB=${VDB_OVERRIDE:-${FIXTURE_ROOT}/var/db/pkg}
    CACHE_PARENT=${CACHE_PARENT_OVERRIDE:-${FIXTURE_ROOT}/var/cache/gentoo-optimization/binpkgs}
    DURABLE_PARENT=${DURABLE_PARENT_OVERRIDE:-${FIXTURE_ROOT}/var/lib/gentoo-optimization/recovery/binpkgs}
    REPORT_PARENT=${REPORT_PARENT_OVERRIDE:-${FIXTURE_ROOT}/var/lib/gentoo-optimization/reports}
    STATE_PARENT=${STATE_PARENT_OVERRIDE:-${FIXTURE_ROOT}/var/lib/gentoo-optimization/state/project}
    FRAMEWORK_LOCK_PATH=${FIXTURE_ROOT}/run/gentoo-optimization/framework-install.lock
    PROJECT_LOCK_PATH=${FIXTURE_ROOT}/run/gentoo-optimization/project.lock
    GENERATION_LOCK_PATH=${FIXTURE_ROOT}/run/gentoo-optimization/generation.lock
    LOCK_PATH=${LOCK_OVERRIDE:-${STATE_PARENT}/binpkg-checkpoint.lock}
    SELECTOR=${SELECTOR_OVERRIDE:-${CACHE_PARENT}/critical-current}
    VERIFIER=${VERIFIER_OVERRIDE}
    MAKE_CONF=${MAKE_CONF_OVERRIDE:-${FIXTURE_ROOT}/etc/portage/make.conf}
    HOME_DIR=${FIXTURE_ROOT}/root
    PATH_VALUE=${TOOL_ROOT}/usr/sbin:${TOOL_ROOT}/usr/bin:${TOOL_ROOT}/sbin:${TOOL_ROOT}/bin
else
    [[ ${EUID} -eq 0 ]] || die 'checkpoint creation requires root'
    [[ -z ${FIXTURE_ROOT}${FIXTURE_OWNER}${TOOL_ROOT}${VDB_OVERRIDE}${CACHE_PARENT_OVERRIDE} ]] || \
        die 'fixture-only path overrides are forbidden in production mode'
    [[ -z ${DURABLE_PARENT_OVERRIDE}${REPORT_PARENT_OVERRIDE}${STATE_PARENT_OVERRIDE} ]] || \
        die 'fixture-only path overrides are forbidden in production mode'
    [[ -z ${LOCK_OVERRIDE}${SELECTOR_OVERRIDE}${VERIFIER_OVERRIDE}${MAKE_CONF_OVERRIDE} ]] || \
        die 'fixture-only path overrides are forbidden in production mode'
    TRUST_UID=0
    TRUST_GID=0
    TOOL_ROOT=
    FIXTURE_ROOT=/
    VDB=/var/db/pkg
    CACHE_PARENT=/var/cache/gentoo-optimization/binpkgs
    DURABLE_PARENT=/var/lib/gentoo-optimization/recovery/binpkgs
    REPORT_PARENT=/var/lib/gentoo-optimization/reports
    STATE_PARENT=/var/lib/gentoo-optimization/state/project
    FRAMEWORK_LOCK_PATH=/run/gentoo-optimization/framework-install.lock
    PROJECT_LOCK_PATH=/run/gentoo-optimization/project.lock
    GENERATION_LOCK_PATH=/run/gentoo-optimization/generation.lock
    LOCK_PATH=${STATE_PARENT}/binpkg-checkpoint.lock
    SELECTOR=${CACHE_PARENT}/critical-current
    VERIFIER=
    MAKE_CONF=/etc/portage/make.conf
    HOME_DIR=/root
    PATH_VALUE=/usr/sbin:/usr/bin:/sbin:/bin
fi

tool_path() {
    printf '%s/usr/bin/%s\n' "${TOOL_ROOT}" "$1"
}

for tool_name in "${TOOL_NAMES[@]}"; do
    TOOL[${tool_name}]=$(tool_path "${tool_name}")
done

readonly CHMOD=${TOOL[chmod]}
readonly BASH_TOOL=${TOOL[bash]}
readonly CHOWN=${TOOL[chown]}
readonly CMP=${TOOL[cmp]}
readonly CP=${TOOL[cp]}
readonly DATE=${TOOL[date]}
readonly EMERGE=${TOOL[emerge]}
readonly EMAINT=${TOOL[emaint]}
readonly ENV_TOOL=${TOOL[env]}
readonly FIND=${TOOL[find]}
readonly FINDMNT=${TOOL[findmnt]}
readonly FLOCK=${TOOL[flock]}
readonly GETENT=${TOOL[getent]}
readonly INSTALL=${TOOL[install]}
readonly JQ=${TOOL[jq]}
readonly LN=${TOOL[ln]}
readonly MOUNT=${TOOL[mount]}
readonly MV=${TOOL[mv]}
readonly PORTAGEQ=${TOOL[portageq]}
readonly PYTHON=${TOOL[python3]}
readonly QCHECK=${TOOL[qcheck]}
readonly QUICKPKG=${TOOL[quickpkg]}
readonly READLINK=${TOOL[readlink]}
readonly RM=${TOOL[rm]}
readonly SETSID=${TOOL[setsid]}
readonly SHA256SUM=${TOOL[sha256sum]}
readonly SLEEP=${TOOL[sleep]}
readonly SORT=${TOOL[sort]}
readonly STAT=${TOOL[stat]}
readonly SYNC=${TOOL[sync]}
readonly TIMEOUT=${TOOL[timeout]}
readonly UMOUNT=${TOOL[umount]}
readonly UNSHARE=${TOOL[unshare]}
readonly ZSTD=${TOOL[zstd]}

CACHE=${CACHE_PARENT}/snapshot-${CHECKPOINT_ID}
DURABLE=${DURABLE_PARENT}/critical-${CHECKPOINT_ID}
REPORT=${REPORT_PARENT}/checkpoint-${CHECKPOINT_ID}
STATE=${STATE_PARENT}/binpkg-checkpoint-${CHECKPOINT_ID}.json
CACHE_PARTIAL=${CACHE_PARENT}/.snapshot-${CHECKPOINT_ID}.partial.${COORDINATOR_PID}
DURABLE_PARTIAL=${DURABLE_PARENT}/.critical-${CHECKPOINT_ID}.partial.${COORDINATOR_PID}
REPORT_PARTIAL=${REPORT_PARENT}/.checkpoint-${CHECKPOINT_ID}.partial.${COORDINATOR_PID}
STATE_PREPARED=${STATE_PARENT}/binpkg-checkpoint-${CHECKPOINT_ID}.prepared.json
STATE_ACTIVATED=${STATE_PARENT}/binpkg-checkpoint-${CHECKPOINT_ID}.selector-activated-offline-restore-pending.json
STATE_RESTORED=${STATE_PARENT}/binpkg-checkpoint-${CHECKPOINT_ID}.offline-restore-proven.json
SELECTOR_PARTIAL=${CACHE_PARENT}/critical-current.prepared-${CHECKPOINT_ID}
SELECTOR_WITNESS=${CACHE_PARENT}/critical-current.previous-${CHECKPOINT_ID}
ACTIVATION_INTENT=${REPORT}/activation-intent.json
ACTIVATION_RECEIPT=${REPORT}/activation-receipt.json
PREPARED_SELECTOR_RECORD=${REPORT}/prepared-selector.json

has_unsafe_text() {
    local value=$1
    [[ ${value} == *$'\n'* || ${value} == *$'\r'* || ${value} == *$'\t'* ]]
}

require_absolute_canonical() {
    local path=$1 label=$2 normalized parent leaf
    [[ ${path} == /* ]] || die "${label} must be absolute: ${path}"
    ! has_unsafe_text "${path}" || die "${label} contains control whitespace"
    if [[ ${path} == / ]]; then
        normalized=/
    else
        parent=${path%/*}
        [[ -n ${parent} ]] || parent=/
        leaf=${path##*/}
        [[ -n ${leaf} && ${leaf} != . && ${leaf} != .. ]] || \
            die "${label} has an unsafe final component: ${path}"
        parent=$(${READLINK} -m -- "${parent}") || die "cannot normalize ${label}: ${path}"
        normalized=${parent%/}/${leaf}
    fi
    [[ ${normalized} == "${path}" ]] || die "${label} is not lexically canonical: ${path}"
    if ((FIXTURE_MODE)); then
        [[ ${path} == "${FIXTURE_ROOT}" || ${path} == "${FIXTURE_ROOT}/"* ]] || \
            die "fixture ${label} escapes the fake root: ${path}"
    fi
}

mode_is_trusted() {
    local mode=$1
    (( (8#${mode} & 0022) == 0 ))
}

stat_fields() {
    ${STAT} -c '%d:%i:%u:%g:%a:%h:%F' -- "$1"
}

stat_follow_fields() {
    ${STAT} -L -c '%d:%i:%u:%g:%a:%h:%F' -- "$1"
}

device_inode_from_fields() {
    local fields=$1 device inode
    IFS=: read -r device inode _ <<<"${fields}"
    printf '%s:%s\n' "${device}" "${inode}"
}

validate_trusted_directory() {
    local path=$1 fields uid gid mode type
    [[ -d ${path} && ! -L ${path} ]] || die "trusted directory is absent or not real: ${path}"
    fields=$(stat_fields "${path}") || die "cannot stat trusted directory: ${path}"
    IFS=: read -r _ _ uid gid mode _ type <<<"${fields}"
    [[ ${type} == directory ]] || die "trusted path is not a directory: ${path}"
    [[ ${uid} == "${TRUST_UID}" && ${gid} == "${TRUST_GID}" ]] || \
        die "untrusted directory owner: ${path} (${uid}:${gid})"
    mode_is_trusted "${mode}" || die "group/world-writable trusted directory: ${path} (${mode})"
}

validate_ancestor_chain() {
    local path=$1 current stop
    require_absolute_canonical "${path}" 'trusted path'
    current=$path
    [[ -d ${current} ]] || current=${current%/*}
    [[ -n ${current} ]] || current=/
    stop=/
    ((FIXTURE_MODE)) && stop=${FIXTURE_ROOT}
    while :; do
        validate_trusted_directory "${current}"
        [[ ${current} == "${stop}" ]] && break
        [[ ${current} != / ]] || die "trusted path escaped validation root: ${path}"
        current=${current%/*}
        [[ -n ${current} ]] || current=/
    done
}

validate_regular_trusted_file() {
    local path=$1 executable=${2:-0} fields uid gid mode links type
    require_absolute_canonical "${path}" 'trusted file'
    validate_ancestor_chain "${path%/*}"
    [[ -f ${path} && ! -L ${path} ]] || die "trusted file is absent or not regular: ${path}"
    fields=$(stat_fields "${path}") || die "cannot stat trusted file: ${path}"
    IFS=: read -r _ _ uid gid mode links type <<<"${fields}"
    [[ ${type} == 'regular file' || ${type} == 'regular empty file' ]] || \
        die "trusted object is not a regular file: ${path}"
    [[ ${uid} == "${TRUST_UID}" && ${gid} == "${TRUST_GID}" ]] || \
        die "untrusted file owner: ${path} (${uid}:${gid})"
    mode_is_trusted "${mode}" || die "group/world-writable trusted file: ${path} (${mode})"
    ((links >= 1)) || die "trusted file has invalid link count: ${path}"
    if ((executable)); then
        (( (8#${mode} & 0111) != 0 )) || die "trusted tool is not executable: ${path}"
    fi
}

open_shared_framework_lock() {
    local path=$1 destination=$2 expected_gid=$3 fields uid gid mode links type
    local opened_fd before opened after
    [[ -f ${path} && ! -L ${path} ]] || die "stable framework lock is absent or not regular: ${path}"
    fields=$(stat_fields "${path}") || die "cannot stat stable framework lock: ${path}"
    IFS=: read -r _ _ uid gid mode links type <<<"${fields}"
    [[ ${type} == 'regular file' || ${type} == 'regular empty file' ]] || \
        die "stable framework lock is not a regular file: ${path}"
    [[ ${uid} == "${TRUST_UID}" && ${gid} == "${expected_gid}" && \
        ${mode} == 640 && ${links} == 1 ]] || \
        die "stable framework lock identity is untrusted: ${path}"
    before=${fields}
    exec {opened_fd}<>"${path}"
    opened=$(stat_follow_fields "/proc/${COORDINATOR_PID}/fd/${opened_fd}") || \
        die "cannot stat opened stable framework lock: ${path}"
    after=$(stat_fields "${path}") || die "cannot re-stat stable framework lock: ${path}"
    [[ ${before} == "${opened}" && ${before} == "${after}" ]] || \
        die "stable framework lock path/fd identity changed: ${path}"
    ${FLOCK} -n -s "${opened_fd}" || die "an exclusive framework transaction holds: ${path}"
    [[ ! -s /proc/${COORDINATOR_PID}/fd/${opened_fd} ]] || \
        die "stable framework lock payload is unexpectedly nonempty: ${path}"
    printf -v "${destination}" '%s' "${opened_fd}"
}

acquire_framework_freeze_locks() {
    local directory=${FRAMEWORK_LOCK_PATH%/*} fields uid gid mode type
    require_absolute_canonical "${directory}" 'stable framework lock directory'
    validate_ancestor_chain "${directory%/*}"
    [[ -d ${directory} && ! -L ${directory} ]] || \
        die 'stable framework lock directory is absent or symlinked'
    fields=$(stat_fields "${directory}") || die 'cannot stat stable framework lock directory'
    IFS=: read -r _ _ uid gid mode _ type <<<"${fields}"
    [[ ${type} == directory && ${uid} == "${TRUST_UID}" && ${gid} == "${FRAMEWORK_LOCK_GID}" && ${mode} == 750 ]] || \
        die 'stable framework lock directory has an untrusted identity'
    open_shared_framework_lock "${FRAMEWORK_LOCK_PATH}" FRAMEWORK_LOCK_FD "${FRAMEWORK_LOCK_GID}"
    open_shared_framework_lock "${PROJECT_LOCK_PATH}" PROJECT_LOCK_FD "${FRAMEWORK_LOCK_GID}"
    open_shared_framework_lock "${GENERATION_LOCK_PATH}" GENERATION_LOCK_FD "${FRAMEWORK_LOCK_GID}"
}

initialize_framework_freeze_locks() {
    local directory=${FRAMEWORK_LOCK_PATH%/*} staging fields gid entry name lock staging_lock uid actual_gid mode links type
    require_absolute_canonical "${directory}" 'stable framework lock directory'
    validate_ancestor_chain "${directory%/*}"
    if ((FIXTURE_MODE)); then
        gid=${TRUST_GID}
    else
        entry=$(${GETENT} group portage) || die 'required portage group is absent'
        IFS=: read -r name _ gid _ <<<"${entry}"
        [[ ${name} == portage && ${gid} =~ ^[0-9]+$ ]] || die 'portage group identity is malformed'
    fi
    FRAMEWORK_LOCK_GID=${gid}
    staging=${directory%/*}/.${directory##*/}.prepared
    if ! path_absent "${staging}"; then
        [[ -d ${staging} && ! -L ${staging} ]] || die 'foreign runtime lock-directory prepared object'
        fields=$(stat_fields "${staging}") || die 'cannot stat runtime lock-directory prepared object'
        IFS=: read -r _ _ uid actual_gid mode _ type <<<"${fields}"
        [[ ${uid} == "${TRUST_UID}" && ${actual_gid} == "${gid}" && ${mode} == 750 && ${type} == directory ]] || \
            die 'foreign runtime lock-directory prepared object'
        [[ -z $(${FIND} "${staging}" -mindepth 1 -maxdepth 1 -print -quit) ]] || \
            die 'runtime lock-directory prepared object is not empty'
    fi
    if path_absent "${directory}"; then
        if path_absent "${staging}"; then
            ${INSTALL} -d -o "${TRUST_UID}" -g "${gid}" -m 0750 "${staging}"
        fi
        sync_paths "${staging}" "${staging%/*}"
        crash_point runtime-lock-directory-staged
        ${MV} --no-clobber --no-copy -T -- "${staging}" "${directory}" || \
            die 'framework lock-directory publication command failed'
        if ! path_absent "${staging}"; then
            fields=$(stat_fields "${directory}") || die 'concurrent framework lock-directory winner is unreadable'
            IFS=: read -r _ _ uid actual_gid mode _ type <<<"${fields}"
            [[ ${uid} == "${TRUST_UID}" && ${actual_gid} == "${gid}" && ${mode} == 750 && ${type} == directory ]] || \
                die 'concurrent framework lock-directory winner is foreign'
            ${RM} -d -- "${staging}"
        fi
        sync_paths "${directory}" "${directory%/*}"
    fi
    [[ -d ${directory} && ! -L ${directory} ]] || die 'stable framework lock directory is foreign'
    fields=$(stat_fields "${directory}") || die 'cannot stat stable framework lock directory'
    IFS=: read -r _ _ uid actual_gid mode _ type <<<"${fields}"
    [[ ${uid} == "${TRUST_UID}" && ${actual_gid} == "${gid}" && ${mode} == 750 && ${type} == directory ]] || \
        die 'stable framework lock directory has wrong owner or mode'
    path_absent "${staging}" || ${RM} -d -- "${staging}"
    for lock in "${FRAMEWORK_LOCK_PATH}" "${PROJECT_LOCK_PATH}" "${GENERATION_LOCK_PATH}"; do
        staging_lock=${lock}.prepared
        if ! path_absent "${staging_lock}"; then
            fields=$(stat_fields "${staging_lock}") || die 'cannot stat stable-lock prepared object'
            IFS=: read -r _ _ uid actual_gid mode links type <<<"${fields}"
            [[ ${uid} == "${TRUST_UID}" && ${actual_gid} == "${gid}" && ${mode} == 640 && \
               ${links} == 1 && ${type} == 'regular empty file' ]] || die 'foreign stable-lock prepared object'
        fi
        if path_absent "${lock}"; then
            if path_absent "${staging_lock}"; then
                ${INSTALL} -o "${TRUST_UID}" -g "${gid}" -m 0640 /dev/null "${staging_lock}"
            fi
            sync_paths "${staging_lock}" "${directory}"
            crash_point "stable-lock-staged-${lock##*/}"
            ${MV} --no-clobber --no-copy -T -- "${staging_lock}" "${lock}" || \
                die 'stable framework lock publication command failed'
            if ! path_absent "${staging_lock}"; then
                fields=$(stat_fields "${lock}") || die 'concurrent stable-lock winner is unreadable'
                ${RM} -f -- "${staging_lock}"
                IFS=: read -r _ _ uid actual_gid mode links type <<<"${fields}"
                [[ ${uid} == "${TRUST_UID}" && ${actual_gid} == "${gid}" && ${mode} == 640 && ${links} == 1 && \
                   ${type} == 'regular empty file' ]] || \
                    die 'concurrent stable-lock winner is foreign'
            fi
            sync_paths "${lock}" "${directory}"
        fi
        path_absent "${staging_lock}" || ${RM} -f -- "${staging_lock}"
    done
}

tool_identity_line() {
    local logical=$1 current target resolved fields sha step=0 uid gid type
    local chain=
    require_absolute_canonical "${logical}" 'logical tool path'
    validate_ancestor_chain "${logical%/*}"
    current=${logical}
    while [[ -L ${current} ]]; do
        ((step += 1))
        ((step <= 40)) || die "tool symlink chain is too deep: ${logical}"
        fields=$(stat_fields "${current}") || die "cannot lstat tool frontend: ${current}"
        IFS=: read -r _ _ uid gid _ _ type <<<"${fields}"
        [[ ${type} == 'symbolic link' ]] || die "tool frontend changed type: ${current}"
        [[ ${uid} == "${TRUST_UID}" && ${gid} == "${TRUST_GID}" ]] || \
            die "untrusted tool symlink owner: ${current}"
        target=$(${READLINK} -- "${current}") || die "cannot read tool symlink: ${current}"
        ! has_unsafe_text "${target}" || die "unsafe tool symlink target: ${current}"
        chain+="${current}->${target}|"
        if [[ ${target} == /* ]]; then
            current=$(${READLINK} -m -- "${target}")
        else
            current=$(${READLINK} -m -- "${current%/*}/${target}")
        fi
        require_absolute_canonical "${current}" 'resolved tool path'
        validate_ancestor_chain "${current%/*}"
    done
    resolved=${current}
    validate_regular_trusted_file "${resolved}" 1
    fields=$(stat_fields "${logical}") || die "cannot stat logical tool: ${logical}"
    sha=$(${SHA256SUM} -- "${resolved}") || die "cannot hash trusted tool: ${resolved}"
    sha=${sha%% *}
    printf '%s\t%s\t%s\t%s\t%s\n' \
        "${logical}" "${resolved}" "${fields}" "${sha}" "${chain:--}"
}

path_absent() {
    [[ -n $1 && ! -e $1 && ! -L $1 ]]
}

require_direct_child() {
    local path=$1 parent=$2 label=$3
    require_absolute_canonical "${path}" "${label}"
    [[ ${path%/*} == "${parent}" && ${path##*/} != "${path}" ]] || \
        die "${label} must be a direct child of ${parent}: ${path}"
}

validate_transaction_paths() {
    local path
    for path in "${CACHE}" "${CACHE_PARTIAL}" "${SELECTOR}" \
        "${SELECTOR_PARTIAL}" "${SELECTOR_WITNESS}"; do
        require_direct_child "${path}" "${CACHE_PARENT}" 'cache mutation path'
    done
    for path in "${DURABLE}" "${DURABLE_PARTIAL}"; do
        require_direct_child "${path}" "${DURABLE_PARENT}" 'durable mutation path'
    done
    for path in "${REPORT}" "${REPORT_PARTIAL}"; do
        require_direct_child "${path}" "${REPORT_PARENT}" 'report mutation path'
    done
    for path in "${STATE}" "${STATE_PREPARED}" "${STATE_ACTIVATED}" \
        "${STATE_RESTORED}"; do
        require_direct_child "${path}" "${STATE_PARENT}" 'state mutation path'
    done
    for path in "${ACTIVATION_INTENT}" "${ACTIVATION_RECEIPT}" \
        "${PREPARED_SELECTOR_RECORD}"; do
        require_direct_child "${path}" "${REPORT}" 'activation record path'
    done
}

crash_point() {
    local name=$1 marker
    ((FIXTURE_MODE)) || return 0
    marker=${FIXTURE_ROOT}/control/crash-${name}
    [[ -e ${marker} ]] || return 0
    ${SYNC} -f -- "${REPORT}" >/dev/null 2>&1 || :
    kill -KILL -- "${COORDINATOR_PID}"
    ${SLEEP} 60
}

safe_publish_noreplace() {
    local source=$1 destination=$2 source_fields dest_fields source_inode destination_inode
    path_absent "${destination}" || die "publication destination already exists: ${destination}"
    source_fields=$(stat_fields "${source}") || die "cannot stat publication source: ${source}"
    source_inode=$(device_inode_from_fields "${source_fields}")
    # GNU mv --no-clobber deliberately exits zero when it did not move.  The
    # postconditions, not its status, authorize publication.
    ${MV} --no-clobber --no-copy -T -- "${source}" "${destination}" || \
        die "no-replace publication command failed: ${destination}"
    path_absent "${source}" || die "no-replace publication left staging source in place: ${source}"
    [[ -e ${destination} || -L ${destination} ]] || die "publication destination is absent: ${destination}"
    dest_fields=$(stat_fields "${destination}") || die "cannot stat publication destination: ${destination}"
    destination_inode=$(device_inode_from_fields "${dest_fields}")
    [[ ${source_inode} == "${destination_inode}" ]] || \
        die "published object does not have the staged device/inode: ${destination}"
}

sync_paths() {
    local path
    for path in "$@"; do
        ${SYNC} -f -- "${path}" || die "fsync failed: ${path}"
    done
}

preflight_selector_exchange() {
    local left=${CACHE_PARENT}/.critical-current.exchange-preflight-${CHECKPOINT_ID}-a
    local right=${CACHE_PARENT}/.critical-current.exchange-preflight-${CHECKPOINT_ID}-b
    local left_before right_before left_after right_after left_target right_target
    require_direct_child "${left}" "${CACHE_PARENT}" 'exchange preflight path'
    require_direct_child "${right}" "${CACHE_PARENT}" 'exchange preflight path'
    if ! path_absent "${left}" || ! path_absent "${right}"; then
        left_target=absent
        right_target=absent
        path_absent "${left}" || {
            [[ -L ${left} ]] || die 'foreign first exchange-preflight residue'
            left_target=$(${READLINK} -- "${left}") || die 'cannot read first exchange-preflight residue'
            [[ ${left_target} == exchange-preflight-a || ${left_target} == exchange-preflight-b ]] || \
                die 'foreign first exchange-preflight residue'
        }
        path_absent "${right}" || {
            [[ -L ${right} ]] || die 'foreign second exchange-preflight residue'
            right_target=$(${READLINK} -- "${right}") || die 'cannot read second exchange-preflight residue'
            [[ ${right_target} == exchange-preflight-a || ${right_target} == exchange-preflight-b ]] || \
                die 'foreign second exchange-preflight residue'
        }
        [[ ${left_target} == absent || ${right_target} == absent || ${left_target} != "${right_target}" ]] || \
            die 'ambiguous duplicate exchange-preflight residue'
        ${RM} -f -- "${left}" "${right}"
        sync_paths "${CACHE_PARENT}"
    fi
    ${LN} -s -- exchange-preflight-a "${left}"
    ${CHOWN} -h "${TRUST_UID}:${TRUST_GID}" -- "${left}"
    sync_paths "${CACHE_PARENT}"
    crash_point exchange-preflight-first-created
    ${LN} -s -- exchange-preflight-b "${right}"
    ${CHOWN} -h "${TRUST_UID}:${TRUST_GID}" -- "${left}" "${right}"
    sync_paths "${CACHE_PARENT}"
    crash_point exchange-preflight-created
    left_before=$(stat_fields "${left}") || die 'cannot stat first exchange preflight symlink'
    right_before=$(stat_fields "${right}") || die 'cannot stat second exchange preflight symlink'
    if ! ${MV} --exchange --no-copy -T -- "${left}" "${right}"; then
        ${RM} -f -- "${left}" "${right}" >/dev/null 2>&1 || :
        sync_paths "${CACHE_PARENT}" >/dev/null 2>&1 || :
        die "selector filesystem does not support atomic mv --exchange: ${CACHE_PARENT}"
    fi
    crash_point exchange-preflight-swapped
    left_after=$(stat_fields "${left}") || die 'exchange preflight lost first symlink'
    right_after=$(stat_fields "${right}") || die 'exchange preflight lost second symlink'
    [[ $(device_inode_from_fields "${left_after}") == $(device_inode_from_fields "${right_before}") && \
       $(device_inode_from_fields "${right_after}") == $(device_inode_from_fields "${left_before}") && \
       $(${READLINK} -- "${left}") == exchange-preflight-b && \
       $(${READLINK} -- "${right}") == exchange-preflight-a ]] || \
        die 'atomic exchange preflight did not swap exact symlink identities'
    ${MV} --exchange --no-copy -T -- "${left}" "${right}" || \
        die 'atomic exchange preflight could not restore its original identities'
    sync_paths "${CACHE_PARENT}"
    crash_point exchange-preflight-restored
    left_after=$(stat_fields "${left}") || die 'cannot restat restored first preflight symlink'
    right_after=$(stat_fields "${right}") || die 'cannot restat restored second preflight symlink'
    [[ $(device_inode_from_fields "${left_after}") == $(device_inode_from_fields "${left_before}") && \
       $(device_inode_from_fields "${right_after}") == $(device_inode_from_fields "${right_before}") ]] || \
        die 'atomic exchange preflight did not restore exact identities'
    ${RM} -f -- "${left}" "${right}"
    if ! path_absent "${left}" || ! path_absent "${right}"; then
        die 'atomic exchange preflight left unexplained objects'
    fi
    sync_paths "${CACHE_PARENT}"
}

read_proc_identity() {
    local pid=$1 state_destination=$2 start_destination=$3 line remainder
    local -a fields=()
    [[ -r /proc/${pid}/stat ]] || return 1
    IFS= read -r line <"/proc/${pid}/stat" || return 1
    [[ ${line} == *') '* ]] || return 1
    remainder=${line##*) }
    IFS=' ' read -r -a fields <<<"${remainder}"
    ((${#fields[@]} >= 20)) || return 1
    printf -v "${state_destination}" '%s' "${fields[0]}"
    printf -v "${start_destination}" '%s' "${fields[19]}"
}

pidfd_signal() {
    local pid=$1 expected_start=$2 signal_name=$3 code status=0
    if ((FIXTURE_MODE)); then
        local state current_start
        if ! read_proc_identity "${pid}" state current_start; then
            return 3
        fi
        [[ ${current_start} == "${expected_start}" ]] || return 4
        kill -s "${signal_name}" -- "${pid}" 2>/dev/null || return 3
        return 0
    fi
    read -r -d '' code <<'PY' || :
import os
import signal
import sys
from pathlib import Path

pid = int(sys.argv[1])
expected_start = sys.argv[2]
signum = getattr(signal, "SIG" + sys.argv[3])

def identity() -> str | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    remainder = text[text.rfind(") ") + 2 :].split()
    if len(remainder) < 20:
        raise SystemExit(6)
    return remainder[19]

if identity() is None:
    raise SystemExit(3)
if identity() != expected_start:
    raise SystemExit(4)
try:
    descriptor = os.pidfd_open(pid, 0)
except ProcessLookupError:
    raise SystemExit(3)
except (AttributeError, OSError):
    raise SystemExit(5)
try:
    current = identity()
    if current is None:
        raise SystemExit(3)
    if current != expected_start:
        raise SystemExit(4)
    signal.pidfd_send_signal(descriptor, signum, None, 0)
finally:
    os.close(descriptor)
PY
    "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C PATH="${PATH_VALUE}" TZ=UTC \
        "${PYTHON}" -I -B -c "${code}" "${pid}" "${expected_start}" "${signal_name}" || status=$?
    return "${status}"
}

terminate_active_child() {
    local status=0 index state current_start signal_status=0
    [[ -n ${ACTIVE_CHILD_PID} && -n ${ACTIVE_CHILD_STARTTIME} ]] || return 0
    pidfd_signal "${ACTIVE_CHILD_PID}" "${ACTIVE_CHILD_STARTTIME}" TERM || signal_status=$?
    case ${signal_status} in
        0|3) ;;
        *) status=${signal_status} ;;
    esac
    for ((index = 0; index < 600; index++)); do
        if ! read_proc_identity "${ACTIVE_CHILD_PID}" state current_start; then
            break
        fi
        [[ ${current_start} == "${ACTIVE_CHILD_STARTTIME}" ]] || {
            status=4
            break
        }
        [[ ${state} == Z ]] && break
        ${SLEEP} 0.05
    done
    if read_proc_identity "${ACTIVE_CHILD_PID}" state current_start && \
        [[ ${current_start} == "${ACTIVE_CHILD_STARTTIME}" && ${state} != Z ]]; then
        pidfd_signal "${ACTIVE_CHILD_PID}" "${ACTIVE_CHILD_STARTTIME}" KILL || status=$?
        for ((index = 0; index < 100; index++)); do
            if ! read_proc_identity "${ACTIVE_CHILD_PID}" state current_start; then
                break
            fi
            [[ ${current_start} == "${ACTIVE_CHILD_STARTTIME}" ]] || {
                status=4
                break
            }
            [[ ${state} == Z ]] && break
            ${SLEEP} 0.05
        done
    fi
    if read_proc_identity "${ACTIVE_CHILD_PID}" state current_start && \
        [[ ${current_start} == "${ACTIVE_CHILD_STARTTIME}" && ${state} != Z ]]; then
        status=5
    fi
    wait "${ACTIVE_CHILD_PID}" 2>/dev/null || :
    ACTIVE_CHILD_PID=
    ACTIVE_CHILD_STARTTIME=
    return "${status}"
}

release_portage_vdb_lock() {
    local index state current_start signal_status=0
    [[ -n ${PORTAGE_LOCK_PID} && -n ${PORTAGE_LOCK_STARTTIME} ]] || return 0
    pidfd_signal "${PORTAGE_LOCK_PID}" "${PORTAGE_LOCK_STARTTIME}" TERM || signal_status=$?
    [[ ${signal_status} == 0 || ${signal_status} == 3 ]] || return "${signal_status}"
    for ((index = 0; index < 100; index++)); do
        read_proc_identity "${PORTAGE_LOCK_PID}" state current_start || break
        [[ ${current_start} == "${PORTAGE_LOCK_STARTTIME}" ]] || return 4
        [[ ${state} == Z ]] && break
        ${SLEEP} 0.05
    done
    if read_proc_identity "${PORTAGE_LOCK_PID}" state current_start && \
       [[ ${current_start} == "${PORTAGE_LOCK_STARTTIME}" && ${state} != Z ]]; then
        pidfd_signal "${PORTAGE_LOCK_PID}" "${PORTAGE_LOCK_STARTTIME}" KILL || signal_status=$?
        [[ ${signal_status} == 0 || ${signal_status} == 3 ]] || return "${signal_status}"
    fi
    wait "${PORTAGE_LOCK_PID}" 2>/dev/null || :
    PORTAGE_LOCK_PID=
    PORTAGE_LOCK_STARTTIME=
}

deactivate_make_conf_overlay() {
    ((MAKE_CONF_OVERLAY_ACTIVE)) || return 0
    if ${UMOUNT} -- "${MAKE_CONF}"; then
        MAKE_CONF_OVERLAY_ACTIVE=0
        return 0
    fi
    return 1
}

scan_portage_processes() {
    local output=$1 proc pid comm argument matched
    : >"${output}"
    for proc in /proc/[0-9]*; do
        [[ -d ${proc} ]] || continue
        pid=${proc##*/}
        [[ ${pid} != "${COORDINATOR_PID}" ]] || continue
        comm=
        IFS= read -r comm <"${proc}/comm" 2>/dev/null || continue
        matched=0
        case ${comm} in
            emerge|ebuild|ebuild.sh|emaint|quickpkg) matched=1 ;;
        esac
        if [[ -r ${proc}/cmdline ]]; then
            while IFS= read -r -d '' argument; do
                case ${argument##*/} in
                    emerge|ebuild|ebuild.sh|emaint|quickpkg) matched=1 ;;
                esac
            done <"${proc}/cmdline"
        fi
        ((matched == 0)) || printf '%s\t%s\n' "${pid}" "${comm}" >>"${output}"
    done
    [[ ! -s ${output} ]] || die 'an existing Portage package mutation process prevents VDB freeze'
}

scan_vdb_handles() {
    local output=$1 proc pid object target map_line maps_fd
    : >"${output}"
    for proc in /proc/[0-9]*; do
        [[ -d ${proc} ]] || continue
        pid=${proc##*/}
        [[ ${pid} != "${COORDINATOR_PID}" && ${pid} != "${PORTAGE_LOCK_PID}" ]] || continue
        for object in "${proc}"/fd/[0-9]* "${proc}"/cwd "${proc}"/root; do
            [[ -L ${object} ]] || continue
            target=$(${READLINK} -- "${object}" 2>/dev/null) || continue
            target=${target% ' (deleted)'}
            if [[ ${target} == "${VDB}" || ${target} == "${VDB}/"* ]]; then
                printf '%s\t%s\t%s\n' "${pid}" "${object#"${proc}/"}" "${target}" >>"${output}"
            fi
        done
        if [[ -r ${proc}/maps ]] && { exec {maps_fd}<"${proc}/maps"; } 2>/dev/null; then
            while IFS= read -r map_line <&"${maps_fd}"; do
                [[ ${map_line} == *" ${VDB}"* ]] || continue
                printf '%s\tmaps\t%s\n' "${pid}" "${map_line}" >>"${output}"
            done
            exec {maps_fd}<&-
        fi
    done
    [[ ! -s ${output} ]] || die 'a pre-freeze process retains a VDB path or mapping'
}

activate_make_conf_overlay() {
    local suffix=${1:-} before=${REPORT}/make-conf-mount.before${1:-}.json
    local after=${REPORT}/make-conf-mount.overlay${1:-}.json
    local mounted_fields overlay_fields mounted_sha overlay_sha features
    ${FINDMNT} --json --target "${MAKE_CONF}" -o TARGET,SOURCE,FSTYPE,OPTIONS >"${before}" || \
        die 'cannot record make.conf mount state before freeze'
    run_tracked "${REPORT}/make-conf-bind-mount${suffix}.log" "${REPORT}/make-conf-bind-mount${suffix}.stderr" 2m \
        "${MOUNT}" --bind -- "${REPORT}/make.conf.freeze" "${MAKE_CONF}"
    [[ ${TRACKED_STATUS} -eq 0 ]] || die "make.conf bind mount failed with status ${TRACKED_STATUS}"
    MAKE_CONF_OVERLAY_ACTIVE=1
    ${FINDMNT} --json --target "${MAKE_CONF}" -o TARGET,SOURCE,FSTYPE,OPTIONS >"${after}" || \
        die 'cannot record active make.conf freeze mount'
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg target "${MAKE_CONF}" '
        (.filesystems | length) == 1 and
        .filesystems[0].target == $target' "${after}" >/dev/null || \
        die 'make.conf freeze is not the exact active mount target'
    mounted_fields=$(stat_fields "${MAKE_CONF}") || die 'cannot stat mounted make.conf freeze'
    overlay_fields=$(stat_fields "${REPORT}/make.conf.freeze") || die 'cannot stat make.conf freeze source'
    [[ $(device_inode_from_fields "${mounted_fields}") == \
        "$(device_inode_from_fields "${overlay_fields}")" ]] || \
        die 'make.conf bind mount is not the prepared freeze inode'
    mounted_sha=$(${SHA256SUM} -- "${MAKE_CONF}") || die 'cannot hash mounted make.conf freeze'
    mounted_sha=${mounted_sha%% *}
    overlay_sha=$(${SHA256SUM} -- "${REPORT}/make.conf.freeze") || die 'cannot hash freeze source'
    overlay_sha=${overlay_sha%% *}
    [[ ${mounted_sha} == "${overlay_sha}" ]] || die 'mounted make.conf freeze bytes differ'
    printf '%s\t%s\t%s\n' "${MAKE_CONF}" "${mounted_fields}" "${mounted_sha}" \
        >"${REPORT}/make-conf-mounted${suffix}.identity"
    run_tracked "${REPORT}/portage-features.freeze${suffix}.txt" "${REPORT}/portage-features.freeze${suffix}.stderr" 2m \
        "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C PATH="${PATH_VALUE}" TZ=UTC \
        "${PORTAGEQ}" envvar FEATURES
    [[ ${TRACKED_STATUS} -eq 0 ]] || die "portageq FEATURES probe failed with status ${TRACKED_STATUS}"
    features=$(<"${REPORT}/portage-features.freeze${suffix}.txt")
    features=" ${features//$'\n'/ } "
    [[ ${features} != *' parallel-install '* ]] || \
        die 'make.conf freeze failed to disable FEATURES=parallel-install'
}

verify_make_conf_restored() {
    local fields sha output=${1:-${REPORT}/make-conf-restored.identity}
    fields=$(stat_fields "${MAKE_CONF}") || die 'cannot stat restored active make.conf'
    sha=$(${SHA256SUM} -- "${MAKE_CONF}") || die 'cannot hash restored active make.conf'
    sha=${sha%% *}
    [[ ${fields} == "${EXPECTED_MAKE_CONF_IDENTITY}" && \
        ${sha} == "${EXPECTED_MAKE_CONF_SHA256}" ]] || \
        die 'active make.conf was not restored to its exact source identity'
    printf '%s\t%s\t%s\n' "${MAKE_CONF}" "${fields}" "${sha}" >"${output}"
}

start_portage_vdb_lock() {
    local suffix ready index code ready_pid _state current_start
    suffix=${1:-}
    ready=${REPORT}/portage-vdb-lock${suffix}.ready.json
    local implementation_path implementation_sha actual_implementation_sha
    read -r -d '' code <<'PY' || :
import ctypes
import fcntl
import hashlib
import json
import os
import signal
import sys
from pathlib import Path

vdb = Path(sys.argv[1])
ready = Path(sys.argv[2])
fixture = sys.argv[3] == "fixture"
parent = os.getppid()
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGTERM) != 0:
    raise OSError(ctypes.get_errno(), "PR_SET_PDEATHSIG failed")
if os.getppid() != parent:
    raise SystemExit("coordinator disappeared before parent-death binding")
stop = False
def terminate(_signum, _frame):
    global stop
    stop = True
signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)
lock = None
lock_file = None
try:
    if fixture:
        lock_path = vdb.parent / ("." + vdb.name + ".portage_lockfile")
        lock_file = lock_path.open("a+")
        fcntl.lockf(lock_file.fileno(), fcntl.LOCK_EX)
        implementation = "fixture-fcntl-lockf"
    else:
        import portage.locks
        lock = portage.locks.lockdir(str(vdb))
        implementation = str(Path(portage.locks.__file__).resolve())
    if fixture:
        implementation_sha256 = "fixture-fcntl-lockf"
    else:
        digest = hashlib.sha256()
        with Path(implementation).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        implementation_sha256 = digest.hexdigest()
    payload = {
        "schema_version": 1,
        "pid": os.getpid(),
        "parent_pid": parent,
        "vdb": str(vdb),
        "implementation": implementation,
        "implementation_sha256": implementation_sha256,
    }
    temporary = ready.with_name(ready.name + ".partial." + str(os.getpid()))
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, ready)
    directory_fd = os.open(ready.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    while not stop:
        signal.pause()
finally:
    if lock is not None:
        portage.locks.unlockdir(lock)
    if lock_file is not None:
        fcntl.lockf(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
PY
    ${SETSID} "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C PATH="${PATH_VALUE}" TZ=UTC \
        "${PYTHON}" -I -B -c "${code}" "${VDB}" "${ready}" \
        "$([[ ${FIXTURE_MODE} -eq 1 ]] && printf fixture || printf production)" \
        >"${REPORT}/portage-vdb-lock.stdout" 2>"${REPORT}/portage-vdb-lock.stderr" &
    PORTAGE_LOCK_PID=$!
    read_proc_identity "${PORTAGE_LOCK_PID}" _state PORTAGE_LOCK_STARTTIME || \
        die 'cannot bind Portage VDB lock-holder process identity'
    for ((index = 0; index < 3000; index++)); do
        [[ -f ${ready} ]] && break
        kill -0 "${PORTAGE_LOCK_PID}" 2>/dev/null || \
            die 'Portage VDB lock holder exited before readiness publication'
        ${SLEEP} 0.1
    done
    [[ -f ${ready} ]] || die 'timed out acquiring the real Portage VDB lock'
    ready_pid=$(${JQ} -r '.pid' "${ready}")
    [[ ${ready_pid} == "${PORTAGE_LOCK_PID}" ]] || die 'Portage VDB lock readiness PID mismatch'
    read_proc_identity "${PORTAGE_LOCK_PID}" _state current_start || \
        die 'Portage VDB lock holder disappeared after readiness publication'
    [[ ${current_start} == "${PORTAGE_LOCK_STARTTIME}" ]] || \
        die 'Portage VDB lock holder identity changed after readiness publication'
    if ((!FIXTURE_MODE)); then
        implementation_path=$(${JQ} -r '.implementation' "${ready}")
        implementation_sha=$(${JQ} -r '.implementation_sha256' "${ready}")
        require_absolute_canonical "${implementation_path}" 'Portage lock implementation'
        validate_regular_trusted_file "${implementation_path}" 0
        actual_implementation_sha=$(${SHA256SUM} -- "${implementation_path}") || \
            die 'cannot hash Portage lock implementation'
        actual_implementation_sha=${actual_implementation_sha%% *}
        [[ ${actual_implementation_sha} == "${implementation_sha}" ]] || \
            die 'Portage lock implementation changed after acquisition'
    fi
}

# Run an expensive child in its own session under a hard deadline.  The outer
# signal trap always terminates that exact process group, so no verifier,
# quickpkg, emaint, or large clone can outlive the coordinator shell.
run_tracked() {
    local output=$1 error_output=$2 deadline=$3 status=0 launcher_code state start
    shift 3
    read -r -d '' launcher_code <<'PY' || :
import ctypes
import os
import signal
import sys

expected_parent = int(sys.argv[1])
command = sys.argv[2:]
if not command:
    raise SystemExit("contained launcher received no command")
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGTERM) != 0:
    raise OSError(ctypes.get_errno(), "PR_SET_PDEATHSIG failed")
if os.getppid() != expected_parent:
    raise SystemExit("coordinator disappeared before parent-death binding")
os.execv(command[0], command)
PY
    ${SETSID} "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C PATH="${PATH_VALUE}" TZ=UTC \
        "${PYTHON}" -I -B -c "${launcher_code}" "${COORDINATOR_PID}" \
        "${UNSHARE}" --pid --fork --kill-child=KILL -- \
        "${TIMEOUT}" --signal=TERM --kill-after=30s "${deadline}" \
        "$@" >"${output}" 2>"${error_output}" &
    ACTIVE_CHILD_PID=$!
    for _ in {1..100}; do
        read_proc_identity "${ACTIVE_CHILD_PID}" state start && break
        ${SLEEP} 0.01
    done
    [[ -n ${start:-} ]] || die 'contained child disappeared before identity capture'
    ACTIVE_CHILD_STARTTIME=${start}
    wait "${ACTIVE_CHILD_PID}" || status=$?
    ACTIVE_CHILD_PID=
    ACTIVE_CHILD_STARTTIME=
    [[ ${status} -ne 124 && ${status} -ne 137 ]] || \
        die "bounded child timed out or required SIGKILL (status=${status}): $1"
    TRACKED_STATUS=${status}
}

preflight_containment_primitives() {
    local code
    if ((FIXTURE_MODE)); then
        printf '%s\n' \
            '{"emulated":true,"kill_child":"KILL","pid_namespace":true,"pidfd_open":true,"pidfd_send_signal":true}' \
            >"${REPORT}/containment-preflight.json"
        : >"${REPORT}/containment-preflight.stderr"
        return 0
    fi
    read -r -d '' code <<'PY' || :
import json
import os
import signal
import subprocess
import sys

child = subprocess.Popen([sys.argv[1], "300"])
try:
    descriptor = os.pidfd_open(child.pid, 0)
    try:
        signal.pidfd_send_signal(descriptor, signal.SIGTERM, None, 0)
    finally:
        os.close(descriptor)
    status = child.wait(timeout=10)
    if status != -signal.SIGTERM:
        raise SystemExit(f"pidfd probe child returned {status}")
finally:
    if child.poll() is None:
        child.kill()
        child.wait()
print(json.dumps({"pid_namespace": True, "pidfd_open": True,
                  "pidfd_send_signal": True, "kill_child": "KILL"}, sort_keys=True))
PY
    run_tracked "${REPORT}/containment-preflight.json" \
        "${REPORT}/containment-preflight.stderr" 2m \
        "${PYTHON}" -I -B -c "${code}" "${SLEEP}"
    [[ ${TRACKED_STATUS} -eq 0 ]] || \
        die "PID-namespace/pidfd containment preflight failed with status ${TRACKED_STATUS}"
    ${JQ} -e '.pid_namespace == true and .pidfd_open == true and
        .pidfd_send_signal == true and .kill_child == "KILL"' \
        "${REPORT}/containment-preflight.json" >/dev/null || \
        die 'containment preflight returned an invalid result'
}

revalidate_all_tool_identities() {
    local output=${REPORT}/.tool-identities.revalidate.${COORDINATOR_PID}.tsv tool_name
    path_absent "${output}" || die 'stale tool-identity revalidation temporary exists'
    printf 'logical_path\tresolved_path\tlogical_stat\tsha256\tsymlink_chain\n' >"${output}"
    for tool_name in "${TOOL_NAMES[@]}"; do
        tool_identity_line "${TOOL[${tool_name}]}" >>"${output}"
    done
    tool_identity_line "${VERIFIER}" >>"${output}"
    tool_identity_line "${SELF}" >>"${output}"
    if ! ${CMP} -- "${REPORT}/tool-identities.tsv" "${output}"; then
        ${RM} -f -- "${output}"
        die 'a trusted tool identity changed during checkpoint creation'
    fi
    ${RM} -f -- "${output}"
}

materialize_sorted_find() {
    local output=$1 deadline=$2 raw
    shift 2
    raw=${output}.unsorted.paths0
    if ! path_absent "${raw}"; then
        validate_regular_trusted_file "${raw}" 0
        ${RM} -f -- "${raw}"
    fi
    run_tracked "${raw}" "${raw}.stderr" "${deadline}" "${FIND}" "$@" -print0
    [[ ${TRACKED_STATUS} -eq 0 ]] || die "find traversal failed with status ${TRACKED_STATUS}: $1"
    run_tracked "${output}" "${output}.sort.stderr" "${deadline}" "${SORT}" -z -- "${raw}"
    [[ ${TRACKED_STATUS} -eq 0 ]] || die "sorted traversal materialization failed with status ${TRACKED_STATUS}: $1"
}

timestamp() {
    ${DATE} -u '+%Y-%m-%dT%H:%M:%SZ'
}

journal_event() {
    local phase=$1 detail=$2 sequence file partial existing name candidate
    ((REPORT_READY)) || return 0
    if ((JOURNAL_SEQUENCE == 0)); then
        for existing in "${REPORT}"/journal/[0-9][0-9][0-9]-*.json; do
            [[ -f ${existing} && ! -L ${existing} ]] || continue
            name=${existing##*/}
            candidate=${name%%-*}
            [[ ${candidate} =~ ^[0-9]{3}$ ]] || die 'journal contains a malformed sequence name'
            ((10#${candidate} > JOURNAL_SEQUENCE)) && JOURNAL_SEQUENCE=$((10#${candidate}))
        done
    fi
    ((JOURNAL_SEQUENCE += 1))
    printf -v sequence '%03d' "${JOURNAL_SEQUENCE}"
    file=${REPORT}/journal/${sequence}-${phase}.json
    partial=${file}.partial.${COORDINATOR_PID}
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -n --arg at "$(timestamp)" --arg phase "${phase}" \
        --arg detail "${detail}" --arg id "${CHECKPOINT_ID}" \
        --argjson sequence "${JOURNAL_SEQUENCE}" \
        '{schema_version:1,sequence:$sequence,checkpoint_id:$id,
          recorded_at:$at,phase:$phase,detail:$detail}' >"${partial}"
    ${CHMOD} 0600 -- "${partial}"
    ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
    sync_paths "${partial}" "${file%/*}"
    safe_publish_noreplace "${partial}" "${file}"
    sync_paths "${file}" "${file%/*}"
    CURRENT_PHASE=${phase}
}

selector_identity() {
    local selector=$1 target resolved selector_fields target_fields packages_sha uid gid type
    [[ -L ${selector} ]] || return 1
    selector_fields=$(stat_fields "${selector}") || return 1
    IFS=: read -r _ _ uid gid _ _ type <<<"${selector_fields}"
    [[ ${type} == 'symbolic link' && ${uid} == "${TRUST_UID}" && ${gid} == "${TRUST_GID}" ]] || return 1
    target=$(${READLINK} -- "${selector}") || return 1
    [[ ${target} == /* ]] || return 1
    resolved=$(${READLINK} -e -- "${selector}") || return 1
    [[ ${resolved} == "${target}" ]] || return 1
    [[ -d ${resolved} && ! -L ${resolved} && -f ${resolved}/Packages && ! -L ${resolved}/Packages ]] || return 1
    target_fields=$(stat_fields "${resolved}") || return 1
    packages_sha=$(${SHA256SUM} -- "${resolved}/Packages") || return 1
    packages_sha=${packages_sha%% *}
    printf '%s|%s|%s|%s|%s\n' \
        "${selector_fields}" "${target}" "${resolved}" "${target_fields}" "${packages_sha}"
}

require_selector_identity() {
    local label=$1 actual
    actual=$(selector_identity "${SELECTOR}") || die "${label}: selector identity is unreadable"
    [[ ${actual} == "${EXPECTED_SELECTOR_IDENTITY}" ]] || \
        die "${label}: source selector identity changed (lost update)"
}

capture_vdb_manifest() {
    local output=$1 path relative fields sha target paths
    paths=${output}.paths0
    materialize_sorted_find "${paths}" 30m "${VDB}" -xdev
    : >"${output}"
    while IFS= read -r -d '' path; do
        relative=${path#"${VDB}/"}
        [[ ${path} == "${VDB}" ]] && relative=.
        ! has_unsafe_text "${relative}" || die "VDB path contains control whitespace"
        fields=$(stat_fields "${path}") || die "cannot stat VDB object: ${path}"
        if [[ -f ${path} && ! -L ${path} ]]; then
            sha=$(${SHA256SUM} -- "${path}") || die "cannot hash VDB file: ${path}"
            sha=${sha%% *}
            printf 'file\t%s\t%s\t%s\n' "${relative}" "${fields}" "${sha}" >>"${output}"
        elif [[ -L ${path} ]]; then
            target=$(${READLINK} -- "${path}") || die "cannot read VDB symlink: ${path}"
            ! has_unsafe_text "${target}" || die "VDB symlink has unsafe target text: ${path}"
            printf 'symlink\t%s\t%s\t%s\n' "${relative}" "${fields}" "${target}" >>"${output}"
        elif [[ -d ${path} ]]; then
            printf 'directory\t%s\t%s\t-\n' "${relative}" "${fields}" >>"${output}"
        else
            die "unsupported object in VDB: ${path}"
        fi
    done <"${paths}"
}

validate_snapshot_tree_trust() {
    local snapshot=$1 path fields uid gid mode type paths snapshot_key
    snapshot_key=$(${SHA256SUM} <<<"${snapshot}") || die 'cannot derive snapshot traversal identity'
    snapshot_key=${snapshot_key%% *}
    snapshot_key=${snapshot_key:0:16}
    paths=${REPORT}/snapshot-traversal.${snapshot_key}.paths0
    materialize_sorted_find "${paths}" 2h "${snapshot}" -xdev
    while IFS= read -r -d '' path; do
        fields=$(stat_fields "${path}") || die "cannot stat snapshot object: ${path}"
        IFS=: read -r _ _ uid gid mode _ type <<<"${fields}"
        [[ ${uid} == "${TRUST_UID}" && ${gid} == "${TRUST_GID}" ]] || \
            die "snapshot object has untrusted owner: ${path} (${uid}:${gid})"
        mode_is_trusted "${mode}" || die "snapshot object is group/world writable: ${path} (${mode})"
        [[ ${type} == directory || ${type} == 'regular file' || ${type} == 'regular empty file' ]] || \
            die "snapshot contains an unsupported object: ${path} (${type})"
    done <"${paths}"
}

revalidate_vdb() {
    local label=$1 manifest
    manifest=${REPORT}/vdb.${label}.tsv
    capture_vdb_manifest "${manifest}"
    ${CMP} -- "${REPORT}/vdb.before.tsv" "${manifest}" || \
        die "live VDB content or metadata changed at ${label}"
}

revalidate_direct_verifier() {
    local fields sha identity
    fields=$(stat_fields "${VERIFIER}") || die 'cannot re-stat immutable direct verifier'
    sha=$(${SHA256SUM} -- "${VERIFIER}") || die 'cannot re-hash immutable direct verifier'
    sha=${sha%% *}
    identity=${fields}'|'${sha}
    [[ ${identity} == "${EXPECTED_VERIFIER_IDENTITY}" ]] || \
        die 'immutable direct verifier identity changed during checkpoint transaction'
}

run_verifier() {
    local snapshot=$1 output=$2 validate_payload=$3 status=0
    local -a arguments=(
        "${VERIFIER}" --snapshot "${snapshot}" --vdb "${VDB}"
        --zstd "${ZSTD}" --format json
    )
    revalidate_direct_verifier
    ((validate_payload)) && arguments+=(--validate-gpkg)
    run_tracked "${output}" "${output}.stderr" 8h \
        "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C PATH="${PATH_VALUE}" TZ=UTC \
        "${PYTHON}" -I -B "${arguments[@]}"
    status=${TRACKED_STATUS}
    [[ ${status} -eq 0 || ${status} -eq 1 ]] || die "snapshot verifier execution failed with ${status}: ${snapshot}"
    [[ ! -s ${output}.stderr ]] || die "snapshot verifier emitted stderr: ${snapshot}"
    VERIFIER_STATUS=${status}
}

verify_exact_final() {
    local snapshot=$1 output=$2 status live indexed validated streams
    run_verifier "${snapshot}" "${output}" 1
    status=${VERIFIER_STATUS}
    [[ ${status} -eq 0 ]] || die "exact GPKG payload verification failed: ${snapshot}"
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg snapshot "${snapshot}" --arg vdb "${VDB}" --arg zstd "${ZSTD}" '
        .schema_version == 1 and
        .status == "pass" and
        .inputs.snapshot == $snapshot and .inputs.vdb == $vdb and
        .inputs.validate_gpkg == true and .inputs.zstd == $zstd and
        .counts.errors == 0 and
        .counts.live_cpvs == .counts.indexed_unique_cpvs and
        .counts.live_cpvs == .counts.indexed_records and
        .counts.live_cpvs == .counts.gpkg_archives_validated and
        .counts.live_cpvs == .counts.image_tar_zst_streams_tested and
        .counts.missing_live_cpvs == 0 and
        .counts.extra_indexed_archives == 0 and
        .counts.unindexed_gpkg_archives == 0 and
        (.coverage.missing_live_cpvs | length) == 0 and
        (.coverage.extra_indexed_archives | length) == 0 and
        (.coverage.unindexed_gpkg_archives | length) == 0' \
        "${output}" >/dev/null || die "exact verifier invariants failed: ${snapshot}"
    live=$(${JQ} -r '.counts.live_cpvs' "${output}")
    indexed=$(${JQ} -r '.counts.indexed_unique_cpvs' "${output}")
    validated=$(${JQ} -r '.counts.gpkg_archives_validated' "${output}")
    streams=$(${JQ} -r '.counts.image_tar_zst_streams_tested' "${output}")
    VERIFY_COUNTS=${live}:${indexed}:${validated}:${streams}
}

write_final_snapshot_manifest() {
    local snapshot=$1 verification=$2 prefix=$3 packages_sha archive_count expected_count
    local records cpv relative expected_size archive actual_size sha count=0
    declare -A seen_paths=()
    packages_sha=$(${SHA256SUM} -- "${snapshot}/Packages") || \
        die "cannot hash final Packages index: ${snapshot}"
    packages_sha=${packages_sha%% *}
    printf '%s  %s\n' "${packages_sha}" "${snapshot}/Packages" \
        >"${REPORT}/${prefix}-packages.sha256"
    records=${REPORT}/${prefix}-archives.records0
    # shellcheck disable=SC2016 # jq program is intentionally literal.
    run_tracked "${records}" "${records}.stderr" 10m \
        "${JQ}" -j '.archives[] | .cpv,"\u0000",.path,"\u0000",(.size.actual|tostring),"\u0000"' \
        "${verification}"
    [[ ${TRACKED_STATUS} -eq 0 ]] || die "cannot materialize final archive records: ${snapshot}"
    printf 'cpv\trelative_path\tsize\tsha256\n' >"${REPORT}/${prefix}-archives.tsv"
    while IFS= read -r -d '' cpv && IFS= read -r -d '' relative && \
        IFS= read -r -d '' expected_size; do
        [[ -n ${cpv} && -n ${relative} && ${relative} != /* && \
            ${relative} != . && ${relative} != .. && \
            ${relative} != */../* && ${relative} != ../* && ${relative} != */.. ]] || \
            die "unsafe final archive record: ${snapshot}"
        ! has_unsafe_text "${cpv}${relative}" || die "final archive identity contains control whitespace"
        [[ -z ${seen_paths[${relative}]+x} ]] || die "duplicate final archive path: ${relative}"
        seen_paths[${relative}]=1
        archive=${snapshot}/${relative}
        [[ -f ${archive} && ! -L ${archive} ]] || die "final archive is absent or not regular: ${archive}"
        stat_fields "${archive}" >/dev/null || die "cannot stat final archive: ${archive}"
        actual_size=$(${STAT} -c %s -- "${archive}") || die "cannot read final archive size: ${archive}"
        [[ ${actual_size} == "${expected_size}" ]] || die "final archive size differs from verifier: ${archive}"
        sha=$(${SHA256SUM} -- "${archive}") || die "cannot hash final archive: ${archive}"
        sha=${sha%% *}
        [[ ${sha} =~ ^[0-9a-f]{64}$ ]] || die "invalid final archive SHA-256: ${archive}"
        printf '%s\t%s\t%s\t%s\n' "${cpv}" "${relative}" "${actual_size}" "${sha}" \
            >>"${REPORT}/${prefix}-archives.tsv"
        ((count += 1))
    done <"${records}"
    archive_count=$(${JQ} -r '.archives | length' "${verification}")
    expected_count=$(${JQ} -r '.counts.live_cpvs' "${verification}")
    [[ ${archive_count} == "${expected_count}" && ${count} == "${expected_count}" ]] || \
        die "final archive manifest count mismatch: ${snapshot}"
}

verify_source_delta() {
    local output=${1:-${REPORT}/source-verification.json} status missing_file expected_file suffix
    suffix=${output##*/}
    suffix=${suffix%.json}
    run_verifier "${EXPECTED_SOURCE_TARGET}" "${output}" 1
    status=${VERIFIER_STATUS}
    [[ ${status} -eq 1 ]] || die 'source snapshot unexpectedly equals the live VDB; nonempty delta was supplied'
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg snapshot "${EXPECTED_SOURCE_TARGET}" --arg vdb "${VDB}" \
        --arg zstd "${ZSTD}" '
        .schema_version == 1 and .status == "fail" and
        .inputs.snapshot == $snapshot and .inputs.vdb == $vdb and
        .inputs.validate_gpkg == true and .inputs.zstd == $zstd and
        .counts.missing_live_cpvs > 0 and
        .counts.errors == .counts.missing_live_cpvs and
        .counts.extra_indexed_archives == 0 and
        .counts.unindexed_gpkg_archives == 0 and
        .counts.indexed_unique_cpvs + .counts.missing_live_cpvs == .counts.live_cpvs and
        .counts.gpkg_archives_validated == .counts.indexed_unique_cpvs and
        .counts.image_tar_zst_streams_tested == .counts.indexed_unique_cpvs and
        ([.issues[].code] | all(. == "live_cpv_missing_archive")) and
        (.coverage.duplicate_live_cpvs | length) == 0 and
        (.coverage.extra_indexed_archives | length) == 0 and
        (.coverage.unindexed_gpkg_archives | length) == 0' \
        "${output}" >/dev/null || die 'source snapshot has failures beyond the exact live delta'
    missing_file=${REPORT}/${suffix}.missing-cpvs.txt
    expected_file=${REPORT}/${suffix}.requested-delta-cpvs.txt
    ${JQ} -r '.coverage.missing_live_cpvs[]' "${output}" | ${SORT} >"${missing_file}"
    printf '%s\n' "${ATOM_CPVS[@]}" | ${SORT} >"${expected_file}"
    ${CMP} -- "${missing_file}" "${expected_file}" || \
        die 'quickpkg atoms are not the complete exact source-to-live CPV delta'
}

publish_canonical_state() {
    local phase_file=$1 staged=${STATE}.partial phase_fields state_fields predecessor_fields allowed=0
    phase_fields=$(stat_fields "${phase_file}") || die 'cannot stat immutable phase state'
    if path_absent "${STATE}"; then
        [[ ${phase_file} == "${STATE_PREPARED}" ]] || \
            die 'canonical state may be created only from the prepared phase'
        allowed=1
    elif [[ -f ${STATE} && ! -L ${STATE} ]]; then
        state_fields=$(stat_fields "${STATE}") || die 'cannot stat canonical checkpoint state'
        if [[ $(device_inode_from_fields "${phase_fields}") == \
              $(device_inode_from_fields "${state_fields}") ]]; then
            return 0
        fi
        if [[ ${phase_file} == "${STATE_ACTIVATED}" && -f ${STATE_PREPARED} && ! -L ${STATE_PREPARED} ]]; then
            predecessor_fields=$(stat_fields "${STATE_PREPARED}") || die 'cannot stat prepared predecessor state'
            if [[ $(device_inode_from_fields "${state_fields}") == \
                  $(device_inode_from_fields "${predecessor_fields}") ]] && \
               ${JQ} -e '.schema_version == 2 and .status == "prepared-selector-activation-pending" and
                 .pending_total == 1 and .unknown_total == 0 and .failed_total == 0' \
                 "${STATE_PREPARED}" >/dev/null; then
                allowed=1
            fi
        elif [[ ${phase_file} == "${STATE_RESTORED}" && -f ${STATE_ACTIVATED} && ! -L ${STATE_ACTIVATED} ]]; then
            predecessor_fields=$(stat_fields "${STATE_ACTIVATED}") || die 'cannot stat activated predecessor state'
            if [[ $(device_inode_from_fields "${state_fields}") == \
                  $(device_inode_from_fields "${predecessor_fields}") ]] && \
               ${JQ} -e '.schema_version == 2 and .status == "selector-activated-offline-restore-pending" and
                 .pending_total == 1 and .unknown_total == 0 and .failed_total == 0' \
                 "${STATE_ACTIVATED}" >/dev/null; then
                allowed=1
            fi
        fi
    else
        die 'canonical checkpoint state is a foreign object type'
    fi
    ((allowed)) || die 'canonical checkpoint state is not the exact allowed predecessor inode/state'
    if ! path_absent "${staged}"; then
        validate_regular_trusted_file "${staged}" 0
        ${RM} -f -- "${staged}"
    fi
    ${LN} -- "${phase_file}" "${staged}"
    sync_paths "${staged}" "${STATE_PARENT}"
    ${MV} --force --no-copy -T -- "${staged}" "${STATE}" || \
        die 'cannot atomically publish canonical checkpoint state'
    sync_paths "${STATE}" "${STATE_PARENT}"
    state_fields=$(stat_fields "${STATE}") || die 'cannot stat canonical checkpoint state'
    [[ $(device_inode_from_fields "${phase_fields}") == \
       $(device_inode_from_fields "${state_fields}") ]] || \
        die 'canonical checkpoint state is not the immutable phase-state inode'
}

publish_phase_state() {
    local status=$1 destination=$2 receipt_sha=${3:--} offline=$4 restore_receipt_sha=${5:--}
    local partial=${destination}.partial intent_sha restore_evidence=null
    intent_sha=$(${SHA256SUM} -- "${ACTIVATION_INTENT}") || die 'cannot hash activation intent'
    intent_sha=${intent_sha%% *}
    if [[ ${restore_receipt_sha} != - ]]; then
        restore_evidence=$(${JQ} -c '.evidence' "${REPORT}/offline-restore-receipt.json") || \
            die 'cannot load immutable offline restore evidence binding'
    fi
    if [[ -f ${destination} && ! -L ${destination} ]]; then
        # shellcheck disable=SC2016 # jq variables, not shell expansions.
        ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg status "${status}" \
            --arg cache "${CACHE}" --arg durable "${DURABLE}" --arg selector "${SELECTOR}" \
            --arg intent "${ACTIVATION_INTENT}" --arg receipt "${ACTIVATION_RECEIPT}" \
            --arg restore_receipt "${REPORT}/offline-restore-receipt.json" \
            --argjson live_cpvs "${live_cpvs:-0}" \
            --arg intent_sha "${intent_sha}" --arg receipt_sha "${receipt_sha}" \
            --arg restore_receipt_sha "${restore_receipt_sha}" \
            --argjson restore_evidence "${restore_evidence}" \
            --argjson offline "${offline}" '
            .schema_version == 2 and .control == "exact-live-binpkg-checkpoint" and
            .checkpoint_id == $id and .status == $status and .live_cpvs == $live_cpvs and
            .cache_checkpoint == {path:$cache} and .durable_checkpoint == {path:$durable} and
            .activation.selector == $selector and .activation.intent == $intent and
            .activation.receipt == (if $receipt_sha == "-" then null else $receipt end) and
            .activation.intent_sha256 == $intent_sha and
            .activation.receipt_sha256 == (if $receipt_sha == "-" then null else $receipt_sha end) and
            .offline_restore.receipt_sha256 ==
              (if $restore_receipt_sha == "-" then null else $restore_receipt_sha end) and
            .offline_restore.evidence == $restore_evidence and
            .offline_restore.receipt ==
              (if $restore_receipt_sha == "-" then null else $restore_receipt end) and
            .offline_restoration_tested == $offline and
            .pending_total == (if $offline then 0 else 1 end) and
            .unknown_total == 0 and .failed_total == 0' "${destination}" >/dev/null || \
            die "existing immutable phase state is incoherent: ${destination}"
        publish_canonical_state "${destination}"
        return 0
    fi
    if ! path_absent "${partial}"; then
        validate_regular_trusted_file "${partial}" 0
        ${RM} -f -- "${partial}"
    fi
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -n --arg id "${CHECKPOINT_ID}" --arg status "${status}" \
        --arg at "$(timestamp)" --arg cache "${CACHE}" --arg durable "${DURABLE}" \
        --arg selector "${SELECTOR}" --arg intent "${ACTIVATION_INTENT}" \
        --arg intent_sha "${intent_sha}" --arg receipt "${ACTIVATION_RECEIPT}" \
        --arg receipt_sha "${receipt_sha}" --argjson offline "${offline}" \
        --arg restore_receipt "${REPORT}/offline-restore-receipt.json" \
        --arg restore_receipt_sha "${restore_receipt_sha}" \
        --argjson restore_evidence "${restore_evidence}" \
        --argjson live_cpvs "${live_cpvs:-0}" \
        '{schema_version:2,control:"exact-live-binpkg-checkpoint",
          checkpoint_id:$id,status:$status,recorded_at:$at,live_cpvs:$live_cpvs,
          cache_checkpoint:{path:$cache},durable_checkpoint:{path:$durable},
          activation:{selector:$selector,intent:$intent,intent_sha256:$intent_sha,
            receipt:(if $receipt_sha == "-" then null else $receipt end),
            receipt_sha256:(if $receipt_sha == "-" then null else $receipt_sha end)},
          offline_restore:{receipt:(if $restore_receipt_sha == "-" then null else $restore_receipt end),
            receipt_sha256:(if $restore_receipt_sha == "-" then null else $restore_receipt_sha end),
            evidence:$restore_evidence},
          offline_restoration_tested:$offline,
          pending_total:(if $offline then 0 else 1 end),unknown_total:0,failed_total:0}' \
        >"${partial}"
    ${CHMOD} 0600 -- "${partial}"
    ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
    sync_paths "${partial}" "${STATE_PARENT}"
    safe_publish_noreplace "${partial}" "${destination}"
    crash_point "after-${status}-phase-publication"
    publish_canonical_state "${destination}"
}

recover_owned_make_conf_overlay() {
    local mount_json=${REPORT}/make-conf-mount.recovery.json expected_path expected_fields expected_sha actual_sha intent_make_conf
    local mounted_fields freeze_fields mounted_sha freeze_sha mount_target
    [[ -f ${REPORT}/freeze-intent.json ]] || return 0
    intent_make_conf=$(${JQ} -r '.make_conf // ""' "${REPORT}/freeze-intent.json") || \
        die 'cannot read checkpoint freeze-intent make.conf path'
    require_absolute_canonical "${intent_make_conf}" 'freeze-intent make.conf path'
    validate_ancestor_chain "${intent_make_conf%/*}"
    MAKE_CONF=${intent_make_conf}
    ${FINDMNT} --json --target "${MAKE_CONF}" -o TARGET,SOURCE,FSTYPE,OPTIONS >"${mount_json}" || \
        die 'cannot inspect make.conf while reconciling checkpoint'
    validate_regular_trusted_file "${REPORT}/make.conf.freeze" 0
    mount_target=$(${JQ} -r '.filesystems[0].target // ""' "${mount_json}")
    mounted_fields=$(stat_fields "${MAKE_CONF}") || die 'cannot stat active make.conf during recovery'
    freeze_fields=$(stat_fields "${REPORT}/make.conf.freeze") || die 'cannot stat checkpoint freeze file during recovery'
    if [[ $(device_inode_from_fields "${mounted_fields}") != \
          $(device_inode_from_fields "${freeze_fields}") ]]; then
        [[ ${mount_target} != "${MAKE_CONF}" ]] || \
            die 'foreign mount is active at checkpoint make.conf target'
        return 0
    fi
    [[ ${mount_target} == "${MAKE_CONF}" ]] || die 'checkpoint freeze inode is active at an unexpected mount target'
    mounted_sha=$(${SHA256SUM} -- "${MAKE_CONF}") || die 'cannot hash mounted make.conf during recovery'
    mounted_sha=${mounted_sha%% *}
    freeze_sha=$(${SHA256SUM} -- "${REPORT}/make.conf.freeze") || die 'cannot hash freeze source during recovery'
    freeze_sha=${freeze_sha%% *}
    [[ ${mounted_sha} == "${freeze_sha}" ]] || die 'mounted checkpoint freeze inode has unexpected bytes'
    ${UMOUNT} -- "${MAKE_CONF}" || die 'cannot remove checkpoint-owned make.conf overlay'
    IFS=$'\t' read -r expected_path expected_fields expected_sha \
        <"${REPORT}/make-conf-source.identity" || die 'cannot read original make.conf identity'
    [[ ${expected_path} == "${MAKE_CONF}" ]] || die 'make.conf recovery identity names another path'
    [[ $(stat_fields "${MAKE_CONF}") == "${expected_fields}" ]] || \
        die 'make.conf metadata was not restored after reconciliation unmount'
    actual_sha=$(${SHA256SUM} -- "${MAKE_CONF}") || die 'cannot hash restored make.conf'
    actual_sha=${actual_sha%% *}
    [[ ${actual_sha} == "${expected_sha}" ]] || die 'make.conf bytes were not restored after reconciliation unmount'
    sync_paths "${MAKE_CONF}" "${MAKE_CONF%/*}"
}

load_activation_intent() {
    local staged=${ACTIVATION_INTENT}.partial incomplete=${REPORT}/activation-intent.incomplete
    local delta_path delta_sha delta_count actual_sha cli_delta line actual_count=0
    local preparation_path preparation_sha preparation_live
    if path_absent "${ACTIVATION_INTENT}" && ! path_absent "${staged}"; then
        validate_regular_trusted_file "${staged}" 0
        # shellcheck disable=SC2016 # jq variables, not shell expansions.
        if ! ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg selector "${SELECTOR}" \
            --arg target "${DURABLE}" '
            .schema_version == 1 and .checkpoint_id == $id and .status == "prepared" and
            .selector == $selector and .target == $target' "${staged}" >/dev/null 2>&1; then
            path_absent "${incomplete}" || die 'both incomplete activation-intent records are visible'
            safe_publish_noreplace "${staged}" "${incomplete}"
            sync_paths "${incomplete}" "${REPORT}" "${REPORT_PARENT}"
            die 'incomplete activation intent was durably classified; selector activation did not start'
        fi
        sync_paths "${staged}" "${REPORT}"
        safe_publish_noreplace "${staged}" "${ACTIVATION_INTENT}"
        sync_paths "${ACTIVATION_INTENT}" "${REPORT}" "${REPORT_PARENT}"
    fi
    validate_regular_trusted_file "${ACTIVATION_INTENT}" 0
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg selector "${SELECTOR}" \
        --arg target "${DURABLE}" --arg source "${EXPECTED_SOURCE_TARGET}" \
        --arg source_sha "${EXPECTED_SOURCE_PACKAGES_SHA256}" --arg verifier "${VERIFIER}" \
        --arg verifier_sha "${EXPECTED_VERIFIER_SHA256}" '
        .schema_version == 1 and .checkpoint_id == $id and .status == "prepared" and
        .selector == $selector and .target == $target and
        .input_bindings.source.path == $source and
        .input_bindings.source.packages_sha256 == $source_sha and
        .input_bindings.verifier.path == $verifier and
        .input_bindings.verifier.sha256 == $verifier_sha and
        (.input_bindings.delta.sorted_cpvs_path | type == "string" and length > 0) and
        (.input_bindings.delta.sorted_cpvs_sha256 | test("^[0-9a-f]{64}$")) and
        (.input_bindings.delta.count | type == "number" and . > 0) and
        (.input_bindings.artifact_preparation.path | type == "string" and length > 0) and
        (.input_bindings.artifact_preparation.sha256 | test("^[0-9a-f]{64}$")) and
        (.input_bindings.artifact_preparation.live_cpvs | type == "number" and . > 0) and
        (.expected_old_selector_identity | type == "string" and length > 0) and
        (.activation_evidence.path | type == "string" and length > 0) and
        (.activation_evidence.sha256 | test("^[0-9a-f]{64}$"))' \
        "${ACTIVATION_INTENT}" >/dev/null || die 'activation intent is invalid or names another transaction'
    EXPECTED_SELECTOR_IDENTITY=$(${JQ} -r '.expected_old_selector_identity' "${ACTIVATION_INTENT}")
    ACTIVATION_INTENT_SHA256=$(${SHA256SUM} -- "${ACTIVATION_INTENT}") || die 'cannot hash activation intent'
    ACTIVATION_INTENT_SHA256=${ACTIVATION_INTENT_SHA256%% *}
    validate_ancestor_chain "${EXPECTED_SOURCE_TARGET}"
    validate_regular_trusted_file "${EXPECTED_SOURCE_TARGET}/Packages" 0
    actual_sha=$(${SHA256SUM} -- "${EXPECTED_SOURCE_TARGET}/Packages") || \
        die 'cannot hash bound source Packages during reconciliation'
    actual_sha=${actual_sha%% *}
    [[ ${actual_sha} == "${EXPECTED_SOURCE_PACKAGES_SHA256}" ]] || \
        die 'bound source Packages changed after activation intent'
    delta_path=$(${JQ} -r '.input_bindings.delta.sorted_cpvs_path' "${ACTIVATION_INTENT}")
    delta_sha=$(${JQ} -r '.input_bindings.delta.sorted_cpvs_sha256' "${ACTIVATION_INTENT}")
    delta_count=$(${JQ} -r '.input_bindings.delta.count' "${ACTIVATION_INTENT}")
    require_direct_child "${delta_path}" "${REPORT}" 'bound sorted delta path'
    validate_regular_trusted_file "${delta_path}" 0
    actual_sha=$(${SHA256SUM} -- "${delta_path}") || die 'cannot hash bound sorted delta'
    actual_sha=${actual_sha%% *}
    [[ ${actual_sha} == "${delta_sha}" ]] || die 'bound sorted delta changed after activation intent'
    while IFS= read -r line; do
        [[ -n ${line} ]] || die 'bound sorted delta contains an empty CPV'
        ((actual_count += 1))
    done <"${delta_path}"
    [[ ${actual_count} == "${delta_count}" ]] || die 'bound sorted delta count changed'
    preparation_path=$(${JQ} -r '.input_bindings.artifact_preparation.path' "${ACTIVATION_INTENT}")
    preparation_sha=$(${JQ} -r '.input_bindings.artifact_preparation.sha256' "${ACTIVATION_INTENT}")
    preparation_live=$(${JQ} -r '.input_bindings.artifact_preparation.live_cpvs' "${ACTIVATION_INTENT}")
    [[ ${preparation_path} == "${REPORT}/artifact-preparation-state.json" ]] || \
        die 'activation intent names a foreign artifact preparation state'
    validate_regular_trusted_file "${preparation_path}" 0
    actual_sha=$(${SHA256SUM} -- "${preparation_path}"); actual_sha=${actual_sha%% *}
    [[ ${actual_sha} == "${preparation_sha}" ]] || die 'bound artifact preparation state changed'
    [[ $(${JQ} -r '.live_cpvs' "${preparation_path}") == "${preparation_live}" ]] || \
        die 'bound artifact preparation live CPV count changed'
    cli_delta=${REPORT}/.reconcile-cli-delta.txt
    if ! path_absent "${cli_delta}"; then
        validate_regular_trusted_file "${cli_delta}" 0
        ${RM} -f -- "${cli_delta}"
    fi
    printf '%s\n' "${ATOM_CPVS[@]}" | ${SORT} >"${cli_delta}"
    if ! ${CMP} -- "${cli_delta}" "${delta_path}"; then
        ${RM} -f -- "${cli_delta}"
        die 'reconcile/finalize exact delta atoms differ from activation intent'
    fi
    ${RM} -f -- "${cli_delta}"
}

validate_prepared_selector_record() {
    local expected_identity=$1 prepared_identity
    validate_regular_trusted_file "${PREPARED_SELECTOR_RECORD}" 0
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg path "${SELECTOR_PARTIAL}" \
        --arg target "${DURABLE}" --arg intent_sha "${ACTIVATION_INTENT_SHA256}" '
        .schema_version == 1 and .checkpoint_id == $id and
        .path == $path and .target == $target and
        .activation_intent_sha256 == $intent_sha and
        (.selector_identity | type == "string" and length > 0)' \
        "${PREPARED_SELECTOR_RECORD}" >/dev/null || die 'prepared-selector record is incoherent'
    prepared_identity=$(${JQ} -r '.selector_identity // ""' "${PREPARED_SELECTOR_RECORD}") || \
        die 'cannot read prepared-selector identity'
    [[ ${prepared_identity} == "${expected_identity}" ]] || \
        die 'prepared-selector identity differs from its immutable record'
}

require_activated_selector_is_prepared_object() {
    local active_identity
    active_identity=$(selector_identity "${SELECTOR}") || die 'cannot read activated selector identity'
    validate_prepared_selector_record "${active_identity}"
}

validate_activation_receipt_contract() {
    local active_identity witness_identity evidence_path evidence_sha evidence_actual
    local prepared_sha receipt_prepared_sha
    validate_regular_trusted_file "${ACTIVATION_RECEIPT}" 0
    require_activated_selector_is_prepared_object
    active_identity=$(selector_identity "${SELECTOR}") || die 'cannot read activated selector identity'
    witness_identity=$(selector_identity "${SELECTOR_WITNESS}") || die 'cannot read displaced selector identity'
    evidence_path=$(${JQ} -r '.activation_evidence.path' "${ACTIVATION_INTENT}")
    evidence_sha=$(${JQ} -r '.activation_evidence.sha256' "${ACTIVATION_INTENT}")
    validate_regular_trusted_file "${evidence_path}" 0
    evidence_actual=$(${SHA256SUM} -- "${evidence_path}"); evidence_actual=${evidence_actual%% *}
    [[ ${evidence_actual} == "${evidence_sha}" ]] || die 'activation evidence manifest changed'
    prepared_sha=$(${SHA256SUM} -- "${PREPARED_SELECTOR_RECORD}"); prepared_sha=${prepared_sha%% *}
    receipt_prepared_sha=$(${JQ} -r '.prepared_selector_record.sha256 // ""' "${ACTIVATION_RECEIPT}")
    [[ ${receipt_prepared_sha} == "${prepared_sha}" ]] || die 'activation receipt prepared-record binding differs'
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg selector "${SELECTOR}" \
        --arg target "${DURABLE}" --arg active "${active_identity}" \
        --arg witness "${SELECTOR_WITNESS}" --arg displaced "${witness_identity}" \
        --arg intent "${ACTIVATION_INTENT}" --arg intent_sha "${ACTIVATION_INTENT_SHA256}" \
        --arg prepared "${PREPARED_SELECTOR_RECORD}" --arg prepared_sha "${prepared_sha}" \
        --arg evidence "${evidence_path}" --arg evidence_sha "${evidence_sha}" '
        .schema_version == 1 and .checkpoint_id == $id and .status == "selector-activated" and
        .selector == $selector and .target == $target and
        .activated_selector_identity == $active and
        .displaced_selector_witness == $witness and .displaced_selector_identity == $displaced and
        .activation_intent == {path:$intent,sha256:$intent_sha} and
        .prepared_selector_record == {path:$prepared,sha256:$prepared_sha} and
        .activation_evidence == {path:$evidence,sha256:$evidence_sha}' \
        "${ACTIVATION_RECEIPT}" >/dev/null || die 'activation receipt is structurally incoherent'
}

ensure_prepared_selector() {
    local identity partial
    if path_absent "${SELECTOR_PARTIAL}"; then
        ${LN} -s -- "${DURABLE}" "${SELECTOR_PARTIAL}"
        ${CHOWN} -h "${TRUST_UID}:${TRUST_GID}" -- "${SELECTOR_PARTIAL}"
        sync_paths "${SELECTOR_PARTIAL}" "${CACHE_PARENT}"
    fi
    identity=$(selector_identity "${SELECTOR_PARTIAL}") || die 'prepared selector is unreadable'
    [[ $(${READLINK} -- "${SELECTOR_PARTIAL}") == "${DURABLE}" ]] || \
        die 'prepared selector names a foreign target'
    if path_absent "${PREPARED_SELECTOR_RECORD}"; then
        partial=${PREPARED_SELECTOR_RECORD}.partial
        if ! path_absent "${partial}"; then
            validate_regular_trusted_file "${partial}" 0
            ${RM} -f -- "${partial}"
        fi
        # shellcheck disable=SC2016 # jq variables, not shell expansions.
        ${JQ} -n --arg id "${CHECKPOINT_ID}" --arg at "$(timestamp)" \
            --arg path "${SELECTOR_PARTIAL}" --arg target "${DURABLE}" \
            --arg identity "${identity}" --arg intent_sha "${ACTIVATION_INTENT_SHA256}" \
            '{schema_version:1,checkpoint_id:$id,prepared_at:$at,path:$path,target:$target,
              selector_identity:$identity,activation_intent_sha256:$intent_sha}' >"${partial}"
        ${CHMOD} 0600 -- "${partial}"
        ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
        sync_paths "${partial}" "${REPORT}"
        safe_publish_noreplace "${partial}" "${PREPARED_SELECTOR_RECORD}"
        sync_paths "${PREPARED_SELECTOR_RECORD}" "${REPORT}"
    else
        validate_prepared_selector_record "${identity}"
    fi
}

publish_activation_receipt() {
    local active_identity witness_identity partial receipt_sha evidence_path evidence_sha actual_sha prepared_sha
    active_identity=$(selector_identity "${SELECTOR}") || die 'activated selector is unreadable'
    [[ $(${READLINK} -- "${SELECTOR}") == "${DURABLE}" ]] || die 'activated selector names a foreign target'
    require_activated_selector_is_prepared_object
    witness_identity=$(selector_identity "${SELECTOR_WITNESS}") || die 'displaced-selector witness is unreadable'
    [[ ${witness_identity} == "${EXPECTED_SELECTOR_IDENTITY}" ]] || \
        die 'displaced-selector witness does not have the expected old identity'
    evidence_path=$(${JQ} -r '.activation_evidence.path' "${ACTIVATION_INTENT}")
    evidence_sha=$(${JQ} -r '.activation_evidence.sha256' "${ACTIVATION_INTENT}")
    actual_sha=$(${SHA256SUM} -- "${evidence_path}") || die 'cannot hash activation evidence manifest'
    actual_sha=${actual_sha%% *}
    [[ ${actual_sha} == "${evidence_sha}" ]] || die 'activation evidence manifest changed after intent publication'
    prepared_sha=$(${SHA256SUM} -- "${PREPARED_SELECTOR_RECORD}") || die 'cannot hash prepared selector record'
    prepared_sha=${prepared_sha%% *}
    if path_absent "${ACTIVATION_RECEIPT}"; then
        partial=${ACTIVATION_RECEIPT}.partial
        if ! path_absent "${partial}"; then
            validate_regular_trusted_file "${partial}" 0
            ${RM} -f -- "${partial}"
        fi
        # shellcheck disable=SC2016 # jq variables, not shell expansions.
        ${JQ} -n --arg id "${CHECKPOINT_ID}" --arg at "$(timestamp)" \
            --arg selector "${SELECTOR}" --arg target "${DURABLE}" \
            --arg active_identity "${active_identity}" --arg witness "${SELECTOR_WITNESS}" \
            --arg witness_identity "${witness_identity}" --arg intent "${ACTIVATION_INTENT}" \
            --arg intent_sha "${ACTIVATION_INTENT_SHA256}" \
            --arg prepared_record "${PREPARED_SELECTOR_RECORD}" \
            --arg prepared_sha "${prepared_sha}" \
            --arg evidence "${evidence_path}" --arg evidence_sha "${evidence_sha}" \
            '{schema_version:1,checkpoint_id:$id,status:"selector-activated",
              activated_at:$at,selector:$selector,target:$target,
              activated_selector_identity:$active_identity,
              displaced_selector_witness:$witness,
              displaced_selector_identity:$witness_identity,
              activation_intent:{path:$intent,sha256:$intent_sha},
              prepared_selector_record:{path:$prepared_record,sha256:$prepared_sha},
              activation_evidence:{path:$evidence,sha256:$evidence_sha}}' >"${partial}"
        ${CHMOD} 0600 -- "${partial}"
        ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
        sync_paths "${partial}" "${REPORT}"
        safe_publish_noreplace "${partial}" "${ACTIVATION_RECEIPT}"
        sync_paths "${ACTIVATION_RECEIPT}" "${REPORT}" "${REPORT_PARENT}"
    else
        validate_activation_receipt_contract
    fi
    validate_activation_receipt_contract
    receipt_sha=$(${SHA256SUM} -- "${ACTIVATION_RECEIPT}") || die 'cannot hash activation receipt'
    printf '%s\n' "${receipt_sha%% *}"
}

reconcile_activation() {
    local freeze_already_held=${1:-0} current_identity displaced_identity receipt_sha
    load_activation_intent
    current_identity=$(selector_identity "${SELECTOR}") || die 'cannot classify current selector during reconciliation'
    if [[ ${current_identity} == "${EXPECTED_SELECTOR_IDENTITY}" ]]; then
        path_absent "${SELECTOR_WITNESS}" || die 'old selector and displaced-selector witness are simultaneously visible'
        path_absent "${ACTIVATION_RECEIPT}" || die 'old selector is incompatible with an activation receipt'
        ensure_prepared_selector
        crash_point after-prepared-selector
        publish_phase_state prepared-selector-activation-pending "${STATE_PREPARED}" - false
        crash_point after-prepared-state
        if ((freeze_already_held == 0)); then
            scan_portage_processes "${REPORT}/portage-processes.reconcile-before-overlay.tsv"
            activate_make_conf_overlay ".reconcile-${COORDINATOR_PID}"
            scan_portage_processes "${REPORT}/portage-processes.reconcile-after-overlay.tsv"
            start_portage_vdb_lock ".reconcile-${COORDINATOR_PID}"
            scan_vdb_handles "${REPORT}/vdb-handles.reconcile-after-lock.tsv"
            revalidate_vdb reconcile
            require_selector_identity 'reconciliation final VDB/source freeze'
        fi
        ACTIVATION_STARTED=1
        ${MV} --exchange --no-copy -T -- "${SELECTOR_PARTIAL}" "${SELECTOR}"
        sync_paths "${CACHE_PARENT}"
        crash_point after-exchange
        current_identity=$(selector_identity "${SELECTOR}") || die 'exchange lost activated selector'
        [[ $(${READLINK} -- "${SELECTOR}") == "${DURABLE}" ]] || die 'exchange did not activate the exact durable checkpoint'
    elif [[ $(${READLINK} -- "${SELECTOR}" 2>/dev/null) == "${DURABLE}" ]]; then
        ACTIVATION_STARTED=1
        require_activated_selector_is_prepared_object
    else
        die 'selector is neither the exact old identity nor the exact activated target (foreign selector)'
    fi

    if [[ -L ${SELECTOR_PARTIAL} ]]; then
        displaced_identity=$(selector_identity "${SELECTOR_PARTIAL}") || die 'displaced selector is unreadable'
        if [[ ${displaced_identity} != "${EXPECTED_SELECTOR_IDENTITY}" ]]; then
            ${MV} --exchange --no-copy -T -- "${SELECTOR_PARTIAL}" "${SELECTOR}" || \
                die 'selector CAS captured a foreign update but could not roll it back'
            sync_paths "${CACHE_PARENT}"
            [[ $(selector_identity "${SELECTOR}") == "${displaced_identity}" ]] || \
                die 'selector CAS rollback did not restore the foreign selector identity'
            [[ $(${READLINK} -- "${SELECTOR_PARTIAL}") == "${DURABLE}" ]] || \
                die 'selector CAS rollback lost the prepared checkpoint selector'
            ${RM} -f -- "${SELECTOR_PARTIAL}"
            sync_paths "${CACHE_PARENT}"
            ACTIVATION_STARTED=0
            die 'selector CAS captured a near-rename lost update and rolled it back'
        fi
        crash_point after-displaced-verified
        path_absent "${SELECTOR_WITNESS}" || die 'both displaced selector and witness are visible'
        safe_publish_noreplace "${SELECTOR_PARTIAL}" "${SELECTOR_WITNESS}"
        sync_paths "${SELECTOR_WITNESS}" "${CACHE_PARENT}"
        crash_point after-witness
    fi
    [[ -L ${SELECTOR_WITNESS} ]] || die 'activated selector has no displaced-selector witness'
    displaced_identity=$(selector_identity "${SELECTOR_WITNESS}") || die 'cannot read displaced-selector witness'
    [[ ${displaced_identity} == "${EXPECTED_SELECTOR_IDENTITY}" ]] || \
        die 'displaced-selector witness is foreign'
    path_absent "${SELECTOR_PARTIAL}" || die 'unexplained prepared selector remains after witness publication'
    receipt_sha=$(publish_activation_receipt)
    crash_point after-receipt
    publish_phase_state selector-activated-offline-restore-pending "${STATE_ACTIVATED}" "${receipt_sha}" false
    crash_point after-activated-state
    ACTIVATION_COMPLETE=1
    release_portage_vdb_lock
    deactivate_make_conf_overlay || die 'cannot remove checkpoint make.conf overlay after activation'
    verify_make_conf_restored "${REPORT}/make-conf-restored.identity"
    path_absent "${SELECTOR_PARTIAL}" || die 'successful activation left an unexplained prepared selector'
}

validate_hash_manifest() {
    local manifest=$1 label=$2 line expected path actual
    validate_regular_trusted_file "${manifest}" 0
    while IFS= read -r line; do
        [[ ${line} =~ ^([0-9a-f]{64})[[:space:]][[:space:]](/.*)$ ]] || \
            die "${label} contains a malformed entry"
        expected=${BASH_REMATCH[1]}
        path=${BASH_REMATCH[2]}
        validate_regular_trusted_file "${path}" 0
        actual=$(${SHA256SUM} -- "${path}") || die "cannot hash ${label} member: ${path}"
        actual=${actual%% *}
        [[ ${actual} == "${expected}" ]] || die "${label} member changed: ${path}"
    done <"${manifest}"
}

validate_durable_archive_manifest() {
    local cpv relative expected_size expected_sha archive actual_size actual_sha count=0
    validate_regular_trusted_file "${REPORT}/durable-final-archives.tsv" 0
    while IFS=$'\t' read -r cpv relative expected_size expected_sha; do
        [[ ${cpv} != cpv ]] || continue
        [[ -n ${cpv} && ${relative} != /* && ${relative} != *$'\n'* && "/${relative}/" != *'/../'* ]] || \
            die 'durable archive manifest contains an unsafe record'
        archive=${DURABLE}/${relative}
        validate_regular_trusted_file "${archive}" 0
        actual_size=$(${STAT} -c %s -- "${archive}") || die 'cannot stat durable archive'
        actual_sha=$(${SHA256SUM} -- "${archive}"); actual_sha=${actual_sha%% *}
        [[ ${actual_size} == "${expected_size}" && ${actual_sha} == "${expected_sha}" ]] || \
            die "durable archive changed after creation: ${cpv}"
        ((count += 1))
    done <"${REPORT}/durable-final-archives.tsv"
    [[ ${count} == "${live_cpvs}" ]] || die 'durable archive manifest count differs from prepared live CPVs'
}

validate_vdb_transition_confined() {
    local before=$1 after=$2 cpv=$3 code
    read -r -d '' code <<'PY' || :
import pathlib
import sys

before = pathlib.Path(sys.argv[1])
after = pathlib.Path(sys.argv[2])
cpv = sys.argv[3]

def load(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t", 3)
        if len(fields) != 4:
            raise SystemExit("malformed VDB manifest")
        result[fields[1]] = line
    return result

def owned(path):
    return path == cpv or path.startswith(cpv + "/")

a = load(before)
b = load(after)
if {key: value for key, value in a.items() if not owned(key)} != {
    key: value for key, value in b.items() if not owned(key)
}:
    raise SystemExit("VDB transition escaped restored CPV")
if {key: value for key, value in a.items() if owned(key)} == {
    key: value for key, value in b.items() if owned(key)
}:
    raise SystemExit("restored CPV VDB subtree did not change")
PY
    "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C PATH="${PATH_VALUE}" TZ=UTC \
        "${PYTHON}" -I -B -c "${code}" "${before}" "${after}" "${cpv}" || \
        die 'supervised offline restore VDB transition is not confined to the exact CPV'
}

validate_offline_restore_directory_inventory() {
    local directory=${REPORT}/offline-restore path name
    validate_trusted_directory "${directory}"
    for path in "${directory}"/* "${directory}"/.[!.]* "${directory}"/..?*; do
        path_absent "${path}" && continue
        [[ -f ${path} && ! -L ${path} ]] || die "foreign object in offline restore evidence: ${path}"
        name=${path##*/}
        case ${name} in
            binpkg.json|command.json|post-verifier.json|post-verifier-report.json|post-verifier-report.json.stderr|command-intent.json|attempt-ledger.sha256) ;;
            retry-intent-[0-9][0-9][0-9].json|pre-command-verifier.[0-9][0-9][0-9].json|pre-command-verifier.[0-9][0-9][0-9].json.stderr) ;;
            vdb.before.[0-9][0-9][0-9].tsv|vdb.after.[0-9][0-9][0-9].tsv) ;;
            vdb.before.[0-9][0-9][0-9].tsv.paths0*|vdb.after.[0-9][0-9][0-9].tsv.paths0*) ;;
            emerge.stdout.[0-9][0-9][0-9]|emerge.stderr.[0-9][0-9][0-9]) ;;
            qcheck.stdout.[0-9][0-9][0-9]|qcheck.stderr.[0-9][0-9][0-9]) ;;
            *) die "unexplained file in offline restore evidence: ${path}" ;;
        esac
        validate_regular_trusted_file "${path}" 0
    done
}

validate_restore_attempt_prefix() {
    local restore_dir=${REPORT}/offline-restore command_intent=${REPORT}/offline-restore/command-intent.json
    local binpkg=${REPORT}/offline-restore/binpkg.json binpkg_sha tool_line local_path expected_hash current_hash
    local retry_path expected_retry retry_index=0 command_intent_sha actual
    validate_regular_trusted_file "${binpkg}" 0
    validate_regular_trusted_file "${command_intent}" 0
    binpkg_sha=$(${SHA256SUM} -- "${binpkg}"); binpkg_sha=${binpkg_sha%% *}
    tool_line=$(tool_identity_line "${EMERGE}")
    ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg tool "${tool_line}" --arg home "${HOME_DIR}" \
        --arg snapshot "${DURABLE}" --arg cpv "${RESTORE_CPV}" --arg emerge "${EMERGE}" \
        --arg binpkg_sha "${binpkg_sha}" '.schema_version == 1 and .checkpoint_id == $id and
        .status == "supervised-command-pending" and .emerge_tool_identity == $tool and
        .environment == {HOME:$home,LANG:"C",LC_ALL:"C",TZ:"UTC",PKGDIR:$snapshot} and
        .argv == [$emerge,"--ignore-default-opts","--offline","--usepkgonly","--getbinpkg=n",
          "--nodeps","--oneshot",("="+$cpv)] and .binpkg_evidence_sha256 == $binpkg_sha and
        (.vdb_before.path | type == "string") and (.vdb_before.sha256 | test("^[0-9a-f]{64}$")) and
        (.pre_command_verifier.path | type == "string") and
        (.pre_command_verifier.sha256 | test("^[0-9a-f]{64}$"))' "${command_intent}" >/dev/null || \
        die 'offline restore command intent is incoherent before retry'
    for actual in vdb_before pre_command_verifier; do
        local_path=$(${JQ} -r ".${actual}.path" "${command_intent}")
        expected_hash=$(${JQ} -r ".${actual}.sha256" "${command_intent}")
        require_direct_child "${local_path}" "${restore_dir}" 'command-intent evidence path'
        validate_regular_trusted_file "${local_path}" 0
        current_hash=$(${SHA256SUM} -- "${local_path}"); current_hash=${current_hash%% *}
        [[ ${current_hash} == "${expected_hash}" ]] || die "command-intent ${actual} evidence changed before retry"
    done
    command_intent_sha=$(${SHA256SUM} -- "${command_intent}"); command_intent_sha=${command_intent_sha%% *}
    for retry_path in "${restore_dir}"/retry-intent-[0-9][0-9][0-9].json; do
        [[ -f ${retry_path} && ! -L ${retry_path} ]] || continue
        ((retry_index += 1)); printf -v expected_retry 'retry-intent-%03d.json' "${retry_index}"
        [[ ${retry_path##*/} == "${expected_retry}" ]] || die 'retry intents are not contiguous before retry'
        ${JQ} -e --arg id "${CHECKPOINT_ID}" --argjson attempt "${retry_index}" \
            --arg intent_sha "${command_intent_sha}" '.schema_version == 1 and
            .checkpoint_id == $id and .status == "operator-authorized-retry" and
            .attempt == $attempt and .command_intent_sha256 == $intent_sha and
            (.authorized_at_unix_ns | test("^[0-9]+$")) and
            (.vdb_before.path | type == "string") and (.vdb_before.sha256 | test("^[0-9a-f]{64}$")) and
            (.pre_command_verifier.path | type == "string") and
            (.pre_command_verifier.sha256 | test("^[0-9a-f]{64}$"))' "${retry_path}" >/dev/null || \
            die 'offline restore retry intent is incoherent before retry'
        for actual in vdb_before pre_command_verifier; do
            local_path=$(${JQ} -r ".${actual}.path" "${retry_path}")
            expected_hash=$(${JQ} -r ".${actual}.sha256" "${retry_path}")
            require_direct_child "${local_path}" "${restore_dir}" 'retry-intent evidence path'
            validate_regular_trusted_file "${local_path}" 0
            current_hash=$(${SHA256SUM} -- "${local_path}"); current_hash=${current_hash%% *}
            [[ ${current_hash} == "${expected_hash}" ]] || die "retry-intent ${actual} evidence changed before retry"
        done
    done
    VALIDATED_RETRY_COUNT=${retry_index}
}

validate_activated_state() {
    local activation_sha intent_sha canonical_fields activated_fields restored_fields canonical_inode
    validate_regular_trusted_file "${STATE_ACTIVATED}" 0
    validate_activation_receipt_contract
    activation_sha=$(${SHA256SUM} -- "${ACTIVATION_RECEIPT}") || die 'cannot hash activation receipt'
    activation_sha=${activation_sha%% *}
    intent_sha=$(${SHA256SUM} -- "${ACTIVATION_INTENT}") || die 'cannot hash activation intent'
    intent_sha=${intent_sha%% *}
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg activation_sha "${activation_sha}" \
        --arg intent_sha "${intent_sha}" --arg cache "${CACHE}" --arg durable "${DURABLE}" \
        --arg selector "${SELECTOR}" --arg intent "${ACTIVATION_INTENT}" \
        --arg receipt "${ACTIVATION_RECEIPT}" --argjson live_cpvs "${live_cpvs}" '
        .schema_version == 2 and .control == "exact-live-binpkg-checkpoint" and .checkpoint_id == $id and
        .status == "selector-activated-offline-restore-pending" and
        .live_cpvs == $live_cpvs and .cache_checkpoint == {path:$cache} and
        .durable_checkpoint == {path:$durable} and .activation.selector == $selector and
        .activation.intent == $intent and .activation.receipt == $receipt and
        .activation.intent_sha256 == $intent_sha and
        .activation.receipt_sha256 == $activation_sha and
        .offline_restoration_tested == false and .pending_total == 1 and
        .unknown_total == 0 and .failed_total == 0 and
        (.live_cpvs | type == "number" and . > 0)' "${STATE_ACTIVATED}" >/dev/null || \
        die 'selector-activated immutable state is incoherent'
    validate_regular_trusted_file "${STATE}" 0
    canonical_fields=$(stat_fields "${STATE}") || die 'cannot stat canonical checkpoint state'
    activated_fields=$(stat_fields "${STATE_ACTIVATED}") || die 'cannot stat activated phase state'
    canonical_inode=$(device_inode_from_fields "${canonical_fields}")
    if [[ ${canonical_inode} == $(device_inode_from_fields "${activated_fields}") ]]; then
        return 0
    fi
    if [[ -f ${STATE_RESTORED} && ! -L ${STATE_RESTORED} ]]; then
        restored_fields=$(stat_fields "${STATE_RESTORED}") || die 'cannot stat restored phase state'
        [[ ${canonical_inode} == $(device_inode_from_fields "${restored_fields}") ]] && return 0
    fi
    die 'canonical checkpoint state is not an exact activated/restored phase-state inode'
}

validate_offline_restore_evidence() {
    local restore_dir=${REPORT}/offline-restore binpkg command post receipt attempt_ledger
    local binpkg_sha command_sha post_sha receipt_sha actual before after qcheck_stdout qcheck_stderr ledger_sha
    local archive_relative archive_path archive_sha tool_line qcheck_tool_line command_intent command_intent_sha
    local local_path expected_hash current_hash activation_actual
    local selected_ns start_ns end_ns post_ns attempt retry_path retry_sha
    local retry_index=0 retry_at retry_before retry_pre expected_retry
    restore_dir=${REPORT}/offline-restore
    binpkg=${restore_dir}/binpkg.json
    command=${restore_dir}/command.json
    post=${restore_dir}/post-verifier.json
    receipt=${REPORT}/offline-restore-receipt.json
    command_intent=${restore_dir}/command-intent.json
    attempt_ledger=${restore_dir}/attempt-ledger.sha256
    for actual in "${binpkg}" "${command}" "${post}" "${receipt}" "${command_intent}" "${attempt_ledger}"; do
        validate_regular_trusted_file "${actual}" 0
    done
    binpkg_sha=$(${SHA256SUM} -- "${binpkg}"); binpkg_sha=${binpkg_sha%% *}
    command_sha=$(${SHA256SUM} -- "${command}"); command_sha=${command_sha%% *}
    post_sha=$(${SHA256SUM} -- "${post}"); post_sha=${post_sha%% *}
    receipt_sha=$(${SHA256SUM} -- "${receipt}"); receipt_sha=${receipt_sha%% *}
    command_intent_sha=$(${SHA256SUM} -- "${command_intent}"); command_intent_sha=${command_intent_sha%% *}
    ledger_sha=$(${SHA256SUM} -- "${attempt_ledger}"); ledger_sha=${ledger_sha%% *}
    validate_hash_manifest "${attempt_ledger}" 'offline restore attempt ledger'
    archive_relative=$(${JQ} -r '.archive_relative_path' "${binpkg}")
    archive_path=${DURABLE}/${archive_relative}
    validate_regular_trusted_file "${archive_path}" 0
    archive_sha=$(${SHA256SUM} -- "${archive_path}"); archive_sha=${archive_sha%% *}
    selected_ns=$(${JQ} -r '.selected_at_unix_ns' "${binpkg}")
    start_ns=$(${JQ} -r '.started_at_unix_ns' "${command}")
    end_ns=$(${JQ} -r '.completed_at_unix_ns' "${command}")
    post_ns=$(${JQ} -r '.completed_at_unix_ns' "${post}")
    [[ ${selected_ns}${start_ns}${end_ns}${post_ns} =~ ^[0-9]+$ ]] || die 'offline restore timestamps are malformed'
    ((selected_ns <= start_ns && start_ns <= end_ns && end_ns <= post_ns)) || \
        die 'offline restore evidence timestamps are out of order'
    attempt=$(${JQ} -r '.attempt' "${command}")
    [[ ${attempt} =~ ^[0-9]+$ ]] || die 'offline restore attempt number is malformed'
    if ((attempt == 0)); then
        ${JQ} -e '.retry_authorization == null' "${command}" >/dev/null || \
            die 'initial restore attempt has a retry authorization'
    else
        retry_path=$(${JQ} -r '.retry_authorization.path' "${command}")
        retry_sha=$(${JQ} -r '.retry_authorization.sha256' "${command}")
        validate_regular_trusted_file "${retry_path}" 0
        current_hash=$(${SHA256SUM} -- "${retry_path}"); current_hash=${current_hash%% *}
        [[ ${current_hash} == "${retry_sha}" ]] || die 'successful retry authorization changed'
        ${JQ} -e --arg id "${CHECKPOINT_ID}" --argjson attempt "${attempt}" \
            --arg intent_sha "${command_intent_sha}" '.schema_version == 1 and
            .checkpoint_id == $id and .status == "operator-authorized-retry" and
            .attempt == $attempt and .command_intent_sha256 == $intent_sha' \
            "${retry_path}" >/dev/null || die 'successful retry authorization is incoherent'
    fi
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg snapshot "${DURABLE}" \
        --arg cpv "${RESTORE_CPV}" --arg relative "${archive_relative}" --arg archive_sha "${archive_sha}" '
        .schema_version == 1 and .sequence == 1 and .checkpoint_id == $id and
        .selected_snapshot == $snapshot and .cpv == $cpv and
        .archive_relative_path == $relative and .archive_sha256 == $archive_sha' "${binpkg}" >/dev/null || \
        die 'offline restore binpkg evidence is incoherent'
    before=$(${JQ} -r '.vdb_transition.before.path' "${command}")
    after=$(${JQ} -r '.vdb_transition.after.path' "${command}")
    qcheck_stdout=$(${JQ} -r '.package_check.stdout.path' "${command}")
    qcheck_stderr=$(${JQ} -r '.package_check.stderr.path' "${command}")
    for actual in "${before}" "${after}" "${qcheck_stdout}" "${qcheck_stderr}" \
        "$(${JQ} -r '.logs.stdout.path' "${command}")" \
        "$(${JQ} -r '.logs.stderr.path' "${command}")"; do
        require_direct_child "${actual}" "${restore_dir}" 'offline restore evidence path'
        validate_regular_trusted_file "${actual}" 0
    done
    validate_vdb_transition_confined "${before}" "${after}" "${RESTORE_CPV}"
    tool_line=$(tool_identity_line "${EMERGE}")
    qcheck_tool_line=$(tool_identity_line "${QCHECK}")
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg tool "${tool_line}" --arg home "${HOME_DIR}" \
        --arg snapshot "${DURABLE}" --arg cpv "${RESTORE_CPV}" --arg emerge "${EMERGE}" \
        --arg binpkg_sha "${binpkg_sha}" '
        .schema_version == 1 and .checkpoint_id == $id and .status == "supervised-command-pending" and
        .emerge_tool_identity == $tool and
        .environment == {HOME:$home,LANG:"C",LC_ALL:"C",TZ:"UTC",PKGDIR:$snapshot} and
        .argv == [$emerge,"--ignore-default-opts","--offline","--usepkgonly","--getbinpkg=n",
          "--nodeps","--oneshot",("="+$cpv)] and .binpkg_evidence_sha256 == $binpkg_sha and
        (.vdb_before.path | type == "string") and (.vdb_before.sha256 | test("^[0-9a-f]{64}$")) and
        (.pre_command_verifier.path | type == "string") and
        (.pre_command_verifier.sha256 | test("^[0-9a-f]{64}$"))' "${command_intent}" >/dev/null || \
        die 'offline restore command intent is incoherent'
    for actual in vdb_before pre_command_verifier; do
        local_path=$(${JQ} -r ".${actual}.path" "${command_intent}")
        expected_hash=$(${JQ} -r ".${actual}.sha256" "${command_intent}")
        require_direct_child "${local_path}" "${restore_dir}" 'command-intent evidence path'
        validate_regular_trusted_file "${local_path}" 0
        current_hash=$(${SHA256SUM} -- "${local_path}"); current_hash=${current_hash%% *}
        [[ ${current_hash} == "${expected_hash}" ]] || die "command-intent ${actual} evidence changed"
    done
    for retry_path in "${restore_dir}"/retry-intent-[0-9][0-9][0-9].json; do
        [[ -f ${retry_path} && ! -L ${retry_path} ]] || continue
        ((retry_index += 1))
        printf -v expected_retry 'retry-intent-%03d.json' "${retry_index}"
        [[ ${retry_path##*/} == "${expected_retry}" ]] || die 'retry intents are not contiguous'
        validate_regular_trusted_file "${retry_path}" 0
        ${JQ} -e --arg id "${CHECKPOINT_ID}" --argjson attempt "${retry_index}" \
            --arg intent_sha "${command_intent_sha}" '.schema_version == 1 and
            .checkpoint_id == $id and .status == "operator-authorized-retry" and
            .attempt == $attempt and .command_intent_sha256 == $intent_sha and
            (.authorized_at_unix_ns | test("^[0-9]+$")) and
            (.vdb_before.path | type == "string") and (.vdb_before.sha256 | test("^[0-9a-f]{64}$")) and
            (.pre_command_verifier.path | type == "string") and
            (.pre_command_verifier.sha256 | test("^[0-9a-f]{64}$"))' "${retry_path}" >/dev/null || \
            die 'offline restore retry intent is incoherent'
        retry_at=$(${JQ} -r '.authorized_at_unix_ns' "${retry_path}")
        retry_before=$(${JQ} -r '.vdb_before.path' "${retry_path}")
        retry_pre=$(${JQ} -r '.pre_command_verifier.path' "${retry_path}")
        for actual in vdb_before pre_command_verifier; do
            local_path=$(${JQ} -r ".${actual}.path" "${retry_path}")
            expected_hash=$(${JQ} -r ".${actual}.sha256" "${retry_path}")
            require_direct_child "${local_path}" "${restore_dir}" 'retry-intent evidence path'
            validate_regular_trusted_file "${local_path}" 0
            current_hash=$(${SHA256SUM} -- "${local_path}"); current_hash=${current_hash%% *}
            [[ ${current_hash} == "${expected_hash}" ]] || die "retry-intent ${actual} evidence changed"
        done
        [[ -n ${retry_before}${retry_pre} && ${retry_at} =~ ^[0-9]+$ ]] || die 'retry intent fields are malformed'
    done
    if ((attempt == 0)); then
        ((retry_index == 0)) || die 'initial successful restore has unexplained retry intents'
    else
        ((attempt == retry_index)) || die 'successful restore does not bind the latest contiguous retry'
        ((retry_at <= start_ns)) || die 'retry authorization occurs after successful attempt start'
    fi
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg cpv "${RESTORE_CPV}" \
        --arg snapshot "${DURABLE}" --arg vdb "${VDB}" --arg emerge "${EMERGE}" \
        --arg tool "${tool_line}" --arg qcheck_tool "${qcheck_tool_line}" \
        --arg home "${HOME_DIR}" --arg binpkg_sha "${binpkg_sha}" \
        --arg intent_sha "${command_intent_sha}" '
        .schema_version == 2 and .sequence == 2 and .checkpoint_id == $id and
        .exit_status == 0 and .offline == true and .usepkgonly == true and
        .getbinpkg == false and .nodeps == true and .selected_snapshot == $snapshot and
        .pkgdir == $snapshot and .vdb == $vdb and .restored_cpv == $cpv and
        .binpkg_evidence_sha256 == $binpkg_sha and .command_intent_sha256 == $intent_sha and
        .emerge_tool_identity == $tool and
        .environment == {HOME:$home,LANG:"C",LC_ALL:"C",TZ:"UTC",PKGDIR:$snapshot} and
        .command == [$emerge,"--ignore-default-opts","--offline","--usepkgonly",
          "--getbinpkg=n","--nodeps","--oneshot",("="+$cpv)] and
        .vdb_transition.changed == true and .package_check.exit_status == 0 and
        .package_check.tool_identity == $qcheck_tool' "${command}" >/dev/null || \
        die 'offline restore command evidence is not an exact successful supervised restore'
    for actual in before after; do
        local_path=$(${JQ} -r ".vdb_transition.${actual}.path" "${command}")
        expected_hash=$(${JQ} -r ".vdb_transition.${actual}.sha256" "${command}")
        current_hash=$(${SHA256SUM} -- "${local_path}"); current_hash=${current_hash%% *}
        [[ ${current_hash} == "${expected_hash}" ]] || die "offline restore ${actual} VDB manifest changed"
    done
    for actual in stdout stderr; do
        local_path=$(${JQ} -r ".package_check.${actual}.path" "${command}")
        expected_hash=$(${JQ} -r ".package_check.${actual}.sha256" "${command}")
        current_hash=$(${SHA256SUM} -- "${local_path}"); current_hash=${current_hash%% *}
        [[ ${current_hash} == "${expected_hash}" ]] || die "offline restore package-check ${actual} changed"
    done
    for actual in stdout stderr; do
        local_path=$(${JQ} -r ".logs.${actual}.path" "${command}")
        expected_hash=$(${JQ} -r ".logs.${actual}.sha256" "${command}")
        current_hash=$(${SHA256SUM} -- "${local_path}"); current_hash=${current_hash%% *}
        [[ ${current_hash} == "${expected_hash}" ]] || die "offline restore emerge ${actual} changed"
    done
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg command_sha "${command_sha}" \
        --arg binpkg_sha "${binpkg_sha}" --arg verifier "${VERIFIER}" \
        --arg verifier_sha "${EXPECTED_VERIFIER_SHA256}" --arg snapshot "${DURABLE}" \
        --arg vdb "${VDB}" '
        .schema_version == 1 and .sequence == 3 and .checkpoint_id == $id and
        .command_evidence_sha256 == $command_sha and .binpkg_evidence_sha256 == $binpkg_sha and
        .verifier == {path:$verifier,sha256:$verifier_sha} and
        .report.schema_version == 1 and .report.status == "pass" and
        .report.inputs.snapshot == $snapshot and .report.inputs.vdb == $vdb and
        .report.inputs.validate_gpkg == true and .report.counts.errors == 0 and
        .report.counts.missing_live_cpvs == 0 and .report.counts.extra_indexed_archives == 0 and
        .report.counts.unindexed_gpkg_archives == 0 and .report.issues == []' "${post}" >/dev/null || \
        die 'offline restore post-verifier evidence is not a strict exact pass'
    # shellcheck disable=SC2016 # jq variables, not shell expansions.
    activation_actual=$(${SHA256SUM} -- "${ACTIVATION_RECEIPT}"); activation_actual=${activation_actual%% *}
    ${JQ} -e --arg id "${CHECKPOINT_ID}" --arg activation_sha "${activation_actual}" \
        --arg command_sha "${command_sha}" --arg binpkg_sha "${binpkg_sha}" --arg post_sha "${post_sha}" \
        --arg ledger_sha "${ledger_sha}" '
        .schema_version == 1 and .checkpoint_id == $id and .status == "offline-restore-proven" and
        .activation_receipt_sha256 == $activation_sha and
        .evidence.command == {path:"offline-restore/command.json",sha256:$command_sha} and
        .evidence.binpkg == {path:"offline-restore/binpkg.json",sha256:$binpkg_sha} and
        .evidence.post_verifier == {path:"offline-restore/post-verifier.json",sha256:$post_sha} and
        .evidence.attempt_ledger == {path:"offline-restore/attempt-ledger.sha256",sha256:$ledger_sha}' \
        "${receipt}" >/dev/null || die 'offline restore receipt is incoherent'
    validate_offline_restore_directory_inventory
    printf '%s\n' "${receipt_sha}"
}

finalize_offline_restore_supervised() {
    local restore_dir=${REPORT}/offline-restore binpkg command post receipt command_intent attempt_ledger
    local partial archive_relative archive_path archive_sha match_count selected_at selected_ns
    local binpkg_sha command_sha post_sha receipt_sha activation_sha tool_line qcheck_tool_line attempt=0 retry_path=- retry_sha=- ledger_sha
    local before after before_sha after_sha started_at started_ns completed_at completed_ns
    local pre_sha
    local stdout stderr stdout_sha stderr_sha qstdout qstderr qstdout_sha qstderr_sha current_report
    local existing retry_count=0 grep_match=0 command_intent_sha manifest_cpv manifest_relative
    local manifest_size manifest_sha manifest_matches=0 archive_size
    load_activation_intent
    live_cpvs=$(${JQ} -r '.input_bindings.artifact_preparation.live_cpvs' "${ACTIVATION_INTENT}") || \
        die 'cannot load prepared live CPV count for finalization'
    [[ ${live_cpvs} =~ ^[0-9]+$ && ${live_cpvs} -gt 0 ]] || die 'prepared live CPV count is invalid'
    validate_durable_archive_manifest
    [[ $(${READLINK} -- "${SELECTOR}") == "${DURABLE}" ]] || die 'offline restore requires the exact activated selector'
    activation_sha=$(publish_activation_receipt)
    validate_activated_state
    [[ -f ${STATE_RESTORED} ]] && {
        ((RETRY_INTERRUPTED_RESTORE == 0)) || die 'retry authorization is invalid after terminal restoration'
        for existing in "${REPORT}"/offline-restore/.terminal-verifier.*; do
            path_absent "${existing}" && continue
            validate_regular_trusted_file "${existing}" 0
            ${RM} -f -- "${existing}"
        done
        receipt_sha=$(validate_offline_restore_evidence)
        current_report=${REPORT}/offline-restore/.terminal-verifier.${COORDINATOR_PID}.json
        verify_exact_final "${DURABLE}" "${current_report}"
        ${RM} -f -- "${current_report}" "${current_report}.stderr"
        sync_paths "${REPORT}/offline-restore"
        publish_phase_state offline-restore-proven "${STATE_RESTORED}" "${activation_sha}" true "${receipt_sha}"
        return 0
    }
    printf '%s\n' "${ATOM_CPVS[@]}" | ${SORT} | ${CMP} - "${REPORT}/source-final-preactivation-verification.requested-delta-cpvs.txt" || \
        die 'finalizer delta differs from the activation intent'
    grep_match=0
    for existing in "${ATOM_CPVS[@]}"; do [[ ${existing} == "${RESTORE_CPV}" ]] && grep_match=1; done
    ((grep_match)) || die '--restore-cpv is not in the exact checkpoint delta'
    validate_hash_manifest "${REPORT}/evidence-manifest.sha256" 'creation evidence manifest'
    validate_hash_manifest "${REPORT}/activation-evidence-manifest.sha256" 'activation evidence manifest'
    [[ -d ${restore_dir} && ! -L ${restore_dir} ]] || ${INSTALL} -d -o "${TRUST_UID}" -g "${TRUST_GID}" -m 0700 "${restore_dir}"
    validate_trusted_directory "${restore_dir}"
    binpkg=${restore_dir}/binpkg.json; command=${restore_dir}/command.json
    post=${restore_dir}/post-verifier.json; receipt=${REPORT}/offline-restore-receipt.json
    command_intent=${restore_dir}/command-intent.json
    attempt_ledger=${restore_dir}/attempt-ledger.sha256
    match_count=$(${JQ} --arg cpv "${RESTORE_CPV}" '[.archives[]|select(.cpv==$cpv)]|length' "${REPORT}/durable-final-verification.json")
    [[ ${match_count} == 1 ]] || die 'restore CPV does not have exactly one verified durable archive'
    archive_relative=$(${JQ} -r --arg cpv "${RESTORE_CPV}" '.archives[]|select(.cpv==$cpv)|.path' "${REPORT}/durable-final-verification.json")
    archive_path=${DURABLE}/${archive_relative}; validate_regular_trusted_file "${archive_path}" 0
    archive_sha=$(${SHA256SUM} -- "${archive_path}"); archive_sha=${archive_sha%% *}
    archive_size=$(${STAT} -c %s -- "${archive_path}") || die 'cannot stat selected restore archive size'
    while IFS=$'\t' read -r manifest_cpv manifest_relative manifest_size manifest_sha; do
        [[ ${manifest_cpv} != cpv ]] || continue
        if [[ ${manifest_cpv} == "${RESTORE_CPV}" ]]; then
            ((manifest_matches += 1))
            [[ ${manifest_relative} == "${archive_relative}" && ${manifest_size} == "${archive_size}" && \
               ${manifest_sha} == "${archive_sha}" ]] || die 'restore archive differs from creation-time durable manifest'
        fi
    done <"${REPORT}/durable-final-archives.tsv"
    [[ ${manifest_matches} == 1 ]] || die 'creation-time durable manifest does not uniquely bind restore archive'
    if path_absent "${binpkg}"; then
        selected_at=$(timestamp); selected_ns=$(${DATE} -u '+%s%N'); partial=${binpkg}.partial
        ${JQ} -n --arg id "${CHECKPOINT_ID}" --arg at "${selected_at}" --arg ns "${selected_ns}" \
            --arg snapshot "${DURABLE}" --arg cpv "${RESTORE_CPV}" --arg relative "${archive_relative}" \
            --arg archive_sha "${archive_sha}" '{schema_version:1,sequence:1,checkpoint_id:$id,
            selected_at:$at,selected_at_unix_ns:$ns,selected_snapshot:$snapshot,cpv:$cpv,
            archive_relative_path:$relative,archive_sha256:$archive_sha}' >"${partial}"
        ${CHMOD} 0600 -- "${partial}"; ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
        sync_paths "${partial}" "${restore_dir}"; safe_publish_noreplace "${partial}" "${binpkg}"
    fi
    binpkg_sha=$(${SHA256SUM} -- "${binpkg}"); binpkg_sha=${binpkg_sha%% *}
    if path_absent "${command}"; then
        if path_absent "${command_intent}"; then
            ((RETRY_INTERRUPTED_RESTORE == 0)) || die 'retry authorization requires an existing ambiguous command intent'
            attempt=0
        else
            ((RETRY_INTERRUPTED_RESTORE)) || die 'offline restore attempt is ambiguous; inspect evidence, then rerun with --retry-interrupted-offline-restore'
            validate_restore_attempt_prefix
            retry_count=${VALIDATED_RETRY_COUNT}
            attempt=${retry_count}
            ((attempt += 1))
        fi
        printf -v before '%s/vdb.before.%03d.tsv' "${restore_dir}" "${attempt}"
        printf -v after '%s/vdb.after.%03d.tsv' "${restore_dir}" "${attempt}"
        capture_vdb_manifest "${before}"; before_sha=$(${SHA256SUM} -- "${before}"); before_sha=${before_sha%% *}
        printf -v current_report '%s/pre-command-verifier.%03d.json' "${restore_dir}" "${attempt}"
        verify_exact_final "${DURABLE}" "${current_report}"
        pre_sha=$(${SHA256SUM} -- "${current_report}"); pre_sha=${pre_sha%% *}
        tool_line=$(tool_identity_line "${EMERGE}"); started_at=$(timestamp); started_ns=$(${DATE} -u '+%s%N')
        if ((attempt == 0)); then
            partial=${command_intent}.partial
            ${JQ} -n --arg id "${CHECKPOINT_ID}" --arg at "${started_at}" --arg ns "${started_ns}" \
                --arg tool "${tool_line}" --arg pkgdir "${DURABLE}" --arg cpv "${RESTORE_CPV}" \
                --arg binpkg_sha "${binpkg_sha}" --arg before "${before}" --arg before_sha "${before_sha}" \
                --arg pre "${current_report}" --arg pre_sha "${pre_sha}" \
                --arg emerge "${EMERGE}" --arg home "${HOME_DIR}" '{schema_version:1,checkpoint_id:$id,
                status:"supervised-command-pending",started_at:$at,started_at_unix_ns:$ns,
                emerge_tool_identity:$tool,environment:{HOME:$home,LANG:"C",LC_ALL:"C",TZ:"UTC",PKGDIR:$pkgdir},
                argv:[$emerge,"--ignore-default-opts","--offline","--usepkgonly","--getbinpkg=n","--nodeps","--oneshot",("="+$cpv)],
                binpkg_evidence_sha256:$binpkg_sha,vdb_before:{path:$before,sha256:$before_sha},
                pre_command_verifier:{path:$pre,sha256:$pre_sha}}' >"${partial}"
            ${CHMOD} 0600 -- "${partial}"; ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
            sync_paths "${partial}" "${restore_dir}"; safe_publish_noreplace "${partial}" "${command_intent}"
        else
            printf -v retry_path '%s/retry-intent-%03d.json' "${restore_dir}" "${attempt}"
            partial=${retry_path}.partial
            command_intent_sha=$(${SHA256SUM} -- "${command_intent}"); command_intent_sha=${command_intent_sha%% *}
            ${JQ} -n --arg id "${CHECKPOINT_ID}" --arg at "${started_at}" --arg ns "${started_ns}" \
                --argjson attempt "${attempt}" --arg intent_sha "${command_intent_sha}" \
                --arg before "${before}" --arg before_sha "${before_sha}" \
                --arg pre "${current_report}" --arg pre_sha "${pre_sha}" '{schema_version:1,
                checkpoint_id:$id,status:"operator-authorized-retry",authorized_at:$at,
                authorized_at_unix_ns:$ns,attempt:$attempt,command_intent_sha256:$intent_sha,
                vdb_before:{path:$before,sha256:$before_sha},
                pre_command_verifier:{path:$pre,sha256:$pre_sha}}' >"${partial}"
            ${CHMOD} 0600 -- "${partial}"; ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
            sync_paths "${partial}" "${restore_dir}"; safe_publish_noreplace "${partial}" "${retry_path}"
            retry_sha=$(${SHA256SUM} -- "${retry_path}"); retry_sha=${retry_sha%% *}
        fi
        crash_point before-offline-command
        printf -v stdout '%s/emerge.stdout.%03d' "${restore_dir}" "${attempt}"
        printf -v stderr '%s/emerge.stderr.%03d' "${restore_dir}" "${attempt}"
        run_tracked "${stdout}" "${stderr}" 4h "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C \
            PATH="${PATH_VALUE}" PKGDIR="${DURABLE}" TZ=UTC "${EMERGE}" --ignore-default-opts --offline \
            --usepkgonly --getbinpkg=n --nodeps --oneshot "=${RESTORE_CPV}"
        [[ ${TRACKED_STATUS} -eq 0 ]] || die "supervised offline emerge failed with status ${TRACKED_STATUS}"
        crash_point after-offline-command
        completed_at=$(timestamp); completed_ns=$(${DATE} -u '+%s%N')
        capture_vdb_manifest "${after}"; after_sha=$(${SHA256SUM} -- "${after}"); after_sha=${after_sha%% *}
        validate_vdb_transition_confined "${before}" "${after}" "${RESTORE_CPV}"
        printf -v qstdout '%s/qcheck.stdout.%03d' "${restore_dir}" "${attempt}"
        printf -v qstderr '%s/qcheck.stderr.%03d' "${restore_dir}" "${attempt}"
        run_tracked "${qstdout}" "${qstderr}" 30m "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C \
            PATH="${PATH_VALUE}" TZ=UTC "${QCHECK}" "=${RESTORE_CPV}"
        [[ ${TRACKED_STATUS} -eq 0 ]] || die "package-managed installed-file check failed with status ${TRACKED_STATUS}"
        stdout_sha=$(${SHA256SUM} -- "${stdout}"); stdout_sha=${stdout_sha%% *}; stderr_sha=$(${SHA256SUM} -- "${stderr}"); stderr_sha=${stderr_sha%% *}
        qstdout_sha=$(${SHA256SUM} -- "${qstdout}"); qstdout_sha=${qstdout_sha%% *}; qstderr_sha=$(${SHA256SUM} -- "${qstderr}"); qstderr_sha=${qstderr_sha%% *}
        qcheck_tool_line=$(tool_identity_line "${QCHECK}")
        command_intent_sha=$(${SHA256SUM} -- "${command_intent}"); command_intent_sha=${command_intent_sha%% *}; partial=${command}.partial
        ${JQ} -n --arg id "${CHECKPOINT_ID}" --argjson attempt "${attempt}" --arg start "${started_at}" \
            --arg start_ns "${started_ns}" --arg end "${completed_at}" --arg end_ns "${completed_ns}" \
            --arg snapshot "${DURABLE}" --arg vdb "${VDB}" --arg cpv "${RESTORE_CPV}" --arg binpkg_sha "${binpkg_sha}" \
            --arg tool "${tool_line}" --arg emerge "${EMERGE}" --arg home "${HOME_DIR}" --arg intent_sha "${command_intent_sha}" \
            --arg retry "${retry_path}" --arg retry_sha "${retry_sha}" --arg before "${before}" --arg before_sha "${before_sha}" \
            --arg pre "${current_report}" --arg pre_sha "${pre_sha}" \
            --arg after "${after}" --arg after_sha "${after_sha}" --arg stdout "${stdout}" --arg stdout_sha "${stdout_sha}" \
            --arg stderr "${stderr}" --arg stderr_sha "${stderr_sha}" --arg qstdout "${qstdout}" --arg qstdout_sha "${qstdout_sha}" \
            --arg qstderr "${qstderr}" --arg qstderr_sha "${qstderr_sha}" --arg qcheck_tool "${qcheck_tool_line}" \
            '{schema_version:2,sequence:2,
            checkpoint_id:$id,attempt:$attempt,started_at:$start,started_at_unix_ns:$start_ns,
            completed_at:$end,completed_at_unix_ns:$end_ns,exit_status:0,offline:true,usepkgonly:true,
            getbinpkg:false,nodeps:true,selected_snapshot:$snapshot,pkgdir:$snapshot,vdb:$vdb,restored_cpv:$cpv,
            binpkg_evidence_sha256:$binpkg_sha,command_intent_sha256:$intent_sha,
            retry_authorization:(if $retry == "-" then null else {path:$retry,sha256:$retry_sha} end),
            emerge_tool_identity:$tool,environment:{HOME:$home,LANG:"C",LC_ALL:"C",TZ:"UTC",PKGDIR:$snapshot},
            command:[$emerge,"--ignore-default-opts","--offline","--usepkgonly","--getbinpkg=n","--nodeps","--oneshot",("="+$cpv)],
            vdb_transition:{before:{path:$before,sha256:$before_sha},after:{path:$after,sha256:$after_sha},changed:true},
            pre_command_verifier:{path:$pre,sha256:$pre_sha},
            logs:{stdout:{path:$stdout,sha256:$stdout_sha},stderr:{path:$stderr,sha256:$stderr_sha}},
            package_check:{tool:"qcheck",tool_identity:$qcheck_tool,argv:["qcheck",("="+$cpv)],exit_status:0,
              stdout:{path:$qstdout,sha256:$qstdout_sha},stderr:{path:$qstderr,sha256:$qstderr_sha}}}' >"${partial}"
        ${CHMOD} 0600 -- "${partial}"; ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
        sync_paths "${partial}" "${restore_dir}"; safe_publish_noreplace "${partial}" "${command}"
    fi
    ((RETRY_INTERRUPTED_RESTORE == 0 || attempt > 0)) || die 'retry authorization was not consumed'
    command_sha=$(${SHA256SUM} -- "${command}"); command_sha=${command_sha%% *}
    if path_absent "${post}"; then
        current_report=${restore_dir}/post-verifier-report.json
        verify_exact_final "${DURABLE}" "${current_report}"
        partial=${post}.partial
        ${JQ} -n --arg id "${CHECKPOINT_ID}" --arg at "$(timestamp)" --arg ns "$(${DATE} -u '+%s%N')" \
            --arg command_sha "${command_sha}" --arg binpkg_sha "${binpkg_sha}" --arg verifier "${VERIFIER}" \
            --arg verifier_sha "${EXPECTED_VERIFIER_SHA256}" --slurpfile report "${current_report}" \
            '{schema_version:1,sequence:3,checkpoint_id:$id,completed_at:$at,completed_at_unix_ns:$ns,
            command_evidence_sha256:$command_sha,binpkg_evidence_sha256:$binpkg_sha,
            verifier:{path:$verifier,sha256:$verifier_sha},report:$report[0]}' >"${partial}"
        ${CHMOD} 0600 -- "${partial}"; ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
        sync_paths "${partial}" "${restore_dir}"; safe_publish_noreplace "${partial}" "${post}"
    fi
    post_sha=$(${SHA256SUM} -- "${post}"); post_sha=${post_sha%% *}; crash_point after-offline-evidence
    if path_absent "${attempt_ledger}"; then
        partial=${attempt_ledger}.partial
        : >"${partial}"
        for existing in "${restore_dir}"/command-intent.json "${restore_dir}"/retry-intent-*.json \
            "${restore_dir}"/pre-command-verifier.*.json* "${restore_dir}"/vdb.before.*.tsv* \
            "${restore_dir}"/vdb.after.*.tsv* "${restore_dir}"/post-verifier-report.json \
            "${restore_dir}"/post-verifier-report.json.stderr "${restore_dir}"/emerge.stdout.* \
            "${restore_dir}"/emerge.stderr.* "${restore_dir}"/qcheck.stdout.* \
            "${restore_dir}"/qcheck.stderr.*; do
            [[ -f ${existing} && ! -L ${existing} ]] || continue
            ${SHA256SUM} -- "${existing}" >>"${partial}"
        done
        ${CHMOD} 0600 -- "${partial}"; ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
        sync_paths "${partial}" "${restore_dir}"; safe_publish_noreplace "${partial}" "${attempt_ledger}"
    fi
    ledger_sha=$(${SHA256SUM} -- "${attempt_ledger}"); ledger_sha=${ledger_sha%% *}
    validate_hash_manifest "${attempt_ledger}" 'offline restore attempt ledger'
    if path_absent "${receipt}"; then
        partial=${receipt}.partial
        ${JQ} -n --arg id "${CHECKPOINT_ID}" --arg at "$(timestamp)" --arg activation_sha "${activation_sha}" \
            --arg command_sha "${command_sha}" --arg binpkg_sha "${binpkg_sha}" --arg post_sha "${post_sha}" \
            --arg ledger_sha "${ledger_sha}" \
            '{schema_version:1,checkpoint_id:$id,status:"offline-restore-proven",recorded_at:$at,
            activation_receipt_sha256:$activation_sha,evidence:{command:{path:"offline-restore/command.json",sha256:$command_sha},
            binpkg:{path:"offline-restore/binpkg.json",sha256:$binpkg_sha},
            post_verifier:{path:"offline-restore/post-verifier.json",sha256:$post_sha},
            attempt_ledger:{path:"offline-restore/attempt-ledger.sha256",sha256:$ledger_sha}}}' >"${partial}"
        ${CHMOD} 0600 -- "${partial}"; ${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${partial}"
        sync_paths "${partial}" "${REPORT}"; safe_publish_noreplace "${partial}" "${receipt}"
    fi
    crash_point after-offline-receipt
    receipt_sha=$(validate_offline_restore_evidence)
    publish_phase_state offline-restore-proven "${STATE_RESTORED}" "${activation_sha}" true "${receipt_sha}"
    crash_point after-offline-restored-state
}

failure_trap() {
    local status=$? actual unchanged=unknown failure_record
    ((status != 0)) || return 0
    if ((IN_FAILURE_TRAP)); then
        trap - EXIT HUP INT TERM
        exit "${status}"
    fi
    IN_FAILURE_TRAP=1
    trap - EXIT HUP INT TERM
    set +e
    terminate_active_child >/dev/null 2>&1 || :
    release_portage_vdb_lock >/dev/null 2>&1 || :
    deactivate_make_conf_overlay >/dev/null 2>&1 || :
    if [[ -n ${EXPECTED_SELECTOR_IDENTITY} ]]; then
        actual=$(selector_identity "${SELECTOR}" 2>/dev/null)
        if [[ ${actual} == "${EXPECTED_SELECTOR_IDENTITY}" ]]; then
            unchanged=true
        else
            unchanged=false
        fi
    fi
    if ((REPORT_READY && !ACTIVATION_STARTED)); then
        journal_event failed "status=${status};phase=${CURRENT_PHASE};selector_unchanged=${unchanged};activation_started=${ACTIVATION_STARTED};activation_complete=${ACTIVATION_COMPLETE}" >/dev/null 2>&1
        failure_record=${REPORT}/failure-attempt-${COORDINATOR_PID}.txt
        printf 'status=%s\nphase=%s\nselector_unchanged=%s\nactivation_started=%s\nactivation_complete=%s\n' \
            "${status}" "${CURRENT_PHASE}" "${unchanged}" "${ACTIVATION_STARTED}" \
            "${ACTIVATION_COMPLETE}" >"${failure_record}.partial"
        ${CHMOD} 0600 -- "${failure_record}.partial" >/dev/null 2>&1
        ${MV} --no-clobber --no-copy -T -- "${failure_record}.partial" "${failure_record}" >/dev/null 2>&1
        ${RM} -f -- "${failure_record}.partial" >/dev/null 2>&1
        if path_absent "${REPORT}/failure.txt"; then
            ${CP} -- "${failure_record}" "${REPORT}/failure.txt.partial.${COORDINATOR_PID}" >/dev/null 2>&1
            ${MV} --no-clobber --no-copy -T -- "${REPORT}/failure.txt.partial.${COORDINATOR_PID}" \
                "${REPORT}/failure.txt" >/dev/null 2>&1
            ${RM} -f -- "${REPORT}/failure.txt.partial.${COORDINATOR_PID}" >/dev/null 2>&1
        fi
        ${SYNC} -f -- "${REPORT}" >/dev/null 2>&1
    elif ((ACTIVATION_STARTED)); then
        printf 'EMERGENCY: selector activation began; durable failure evidence is intentionally not mutated; inspect selector and prepared activation intent\n' >&2
    fi
    exit "${status}"
}

signal_exit() {
    local status=$1
    terminate_active_child >/dev/null 2>&1 || :
    exit "${status}"
}

trap failure_trap EXIT
trap 'signal_exit 129' HUP
trap 'signal_exit 130' INT
trap 'signal_exit 143' TERM

# Bootstrap readlink/stat/sha256sum through their fixed logical paths, then
# validate and record the complete logical-to-resolved chain for every tool.
SELF=$(${READLINK} -e -- "$0") || die 'cannot resolve checkpoint script'
[[ -n ${VERIFIER} ]] || VERIFIER=${SELF%/*}/verify-binpkg-snapshot.py
for path in "${VDB}" "${CACHE_PARENT}" "${DURABLE_PARENT}" "${REPORT_PARENT}" \
    "${STATE_PARENT}" "${LOCK_PATH}" "${SELECTOR}" "${VERIFIER}" \
    "${MAKE_CONF}" "${EXPECTED_SOURCE_TARGET}" "${FIXTURE_ROOT}" \
    "${FRAMEWORK_LOCK_PATH}" "${PROJECT_LOCK_PATH}" "${GENERATION_LOCK_PATH}"; do
    require_absolute_canonical "${path}" 'configured path'
done

validate_ancestor_chain "${VDB}"
validate_ancestor_chain "${CACHE_PARENT}"
validate_ancestor_chain "${DURABLE_PARENT}"
validate_ancestor_chain "${REPORT_PARENT}"
validate_ancestor_chain "${STATE_PARENT}"
validate_ancestor_chain "${LOCK_PATH%/*}"
validate_ancestor_chain "${SELECTOR%/*}"
validate_regular_trusted_file "${VERIFIER}" 0
validate_regular_trusted_file "${SELF}" 1
if ((!FIXTURE_MODE)); then
    self_fields=$(stat_fields "${SELF}") || die 'cannot stat production checkpoint script'
    [[ ${self_fields} == *':0:0:755:1:regular file' ]] || \
        die 'production checkpoint script must be root:root mode-0755 with one hardlink'
fi
verifier_fields=$(stat_fields "${VERIFIER}") || die 'cannot stat immutable direct verifier'
if ((!FIXTURE_MODE)); then
    [[ ${verifier_fields} == *':0:0:755:1:regular file' ]] || \
        die 'production direct verifier must be root:root mode-0755 with one hardlink'
fi
verifier_sha=$(${SHA256SUM} -- "${VERIFIER}") || die 'cannot hash immutable direct verifier'
verifier_sha=${verifier_sha%% *}
[[ ${verifier_sha} == "${EXPECTED_VERIFIER_SHA256}" ]] || \
    die 'immutable direct verifier digest does not match --expected-verifier-sha256'
EXPECTED_VERIFIER_IDENTITY=${verifier_fields}'|'${verifier_sha}

# Validate every logical frontend and its resolved chain before any Portage or
# snapshot mutation.  The recorded lines are published after the evidence root
# exists.  In particular quickpkg/emaint remain invoked through their logical
# Gentoo python-exec symlinks so argv[0] dispatch is preserved.
for tool_name in "${TOOL_NAMES[@]}"; do
    TOOL_IDENTITY_LINES+=("$(tool_identity_line "${TOOL[${tool_name}]}")")
done
TOOL_IDENTITY_LINES+=("$(tool_identity_line "${VERIFIER}")")
TOOL_IDENTITY_LINES+=("$(tool_identity_line "${SELF}")")
expected_bash=$(${READLINK} -e -- "${BASH_TOOL}") || die 'cannot resolve trusted Bash interpreter'
[[ /proc/${COORDINATOR_PID}/exe -ef ${expected_bash} ]] || \
    die "checkpoint is not running under the trusted Bash interpreter: ${expected_bash}"

# Hold the same three stable framework locks as the installed publisher, in
# the publisher's global order.  This pins the framework-current resolution,
# active make.conf, and generated project/generation policy for the complete
# checkpoint transaction.
initialize_framework_freeze_locks
acquire_framework_freeze_locks
if [[ ${ACTION} != create ]]; then
    validate_trusted_directory "${REPORT}"
    recover_owned_make_conf_overlay
fi
MAKE_CONF=$(${READLINK} -e -- "${MAKE_CONF}") || die 'cannot resolve active make.conf under framework lock'
require_absolute_canonical "${MAKE_CONF}" 'resolved active make.conf'
validate_regular_trusted_file "${MAKE_CONF}" 0
EXPECTED_MAKE_CONF_IDENTITY=$(stat_fields "${MAKE_CONF}") || die 'cannot stat active make.conf'
EXPECTED_MAKE_CONF_SHA256=$(${SHA256SUM} -- "${MAKE_CONF}") || die 'cannot hash active make.conf'
EXPECTED_MAKE_CONF_SHA256=${EXPECTED_MAKE_CONF_SHA256%% *}

declare -A seen_atom=()
declare -A seen_cpv=()
for atom in "${ATOMS[@]}"; do
    [[ ${atom} =~ ^=([A-Za-z0-9+_.-]+)/([A-Za-z0-9+_.-]+)$ ]] || \
        die "non-exact or unsafe quickpkg atom: ${atom}"
    [[ -z ${seen_atom[${atom}]+x} ]] || die "duplicate quickpkg atom: ${atom}"
    cpv=${atom#=}
    [[ -z ${seen_cpv[${cpv}]+x} ]] || die "duplicate quickpkg CPV: ${cpv}"
    [[ -d ${VDB}/${cpv} && ! -L ${VDB}/${cpv} ]] || die "exact quickpkg CPV is not installed: ${cpv}"
    seen_atom[${atom}]=1
    seen_cpv[${cpv}]=1
    ATOM_CPVS+=("${cpv}")
done

# The lock parent is trusted, so creating a missing lock with install and then
# verifying its path/fd identity cannot be redirected through an untrusted link.
lock_partial=${LOCK_PATH}.prepared
if ! path_absent "${lock_partial}"; then
    validate_regular_trusted_file "${lock_partial}" 0
    lock_fields=$(stat_fields "${lock_partial}") || die 'cannot stat transaction-lock prepared object'
    [[ ${lock_fields} == *":${TRUST_UID}:${TRUST_GID}:600:1:regular empty file" ]] || \
        die 'foreign transaction-lock prepared object'
fi
if path_absent "${LOCK_PATH}"; then
    if path_absent "${lock_partial}"; then
        ${INSTALL} -o "${TRUST_UID}" -g "${TRUST_GID}" -m 0600 /dev/null "${lock_partial}"
    fi
    crash_point transaction-lock-staged
    ${MV} --no-clobber --no-copy -T -- "${lock_partial}" "${LOCK_PATH}" || \
        die 'transaction-lock publication command failed'
    if ! path_absent "${lock_partial}"; then
        lock_fields=$(stat_fields "${LOCK_PATH}") || die 'concurrent transaction-lock winner is unreadable'
        ${RM} -f -- "${lock_partial}"
        [[ ${lock_fields} == *":${TRUST_UID}:${TRUST_GID}:600:1:regular empty file" ]] || \
            die 'concurrent transaction-lock winner is foreign'
    fi
    sync_paths "${LOCK_PATH}" "${LOCK_PATH%/*}"
fi
path_absent "${lock_partial}" || ${RM} -f -- "${lock_partial}"
validate_regular_trusted_file "${LOCK_PATH}" 0
lock_fields=$(stat_fields "${LOCK_PATH}") || die 'cannot stat transaction lock'
[[ ${lock_fields} == *":${TRUST_UID}:${TRUST_GID}:600:"* ]] || \
    die 'transaction lock must have exact owner and mode 0600'
exec {LOCK_FD}<>"${LOCK_PATH}"
${FLOCK} -n -x "${LOCK_FD}" || die 'another binpkg checkpoint transaction holds the lock'
lock_path_identity=$(stat_fields "${LOCK_PATH}")
lock_fd_identity=$(stat_follow_fields "/proc/${COORDINATOR_PID}/fd/${LOCK_FD}")
[[ $(device_inode_from_fields "${lock_path_identity}") == \
    "$(device_inode_from_fields "${lock_fd_identity}")" ]] || \
    die 'transaction lock path/fd identity mismatch'

validate_transaction_paths
preflight_selector_exchange

if [[ ${ACTION} == reconcile ]]; then
    REPORT_READY=1
    revalidate_all_tool_identities
    live_cpvs=$(${JQ} -r '.live_cpvs' "${REPORT}/artifact-preparation-state.json") || \
        die 'cannot load prepared live CPV count for reconciliation'
    [[ ${live_cpvs} =~ ^[0-9]+$ && ${live_cpvs} -gt 0 ]] || \
        die 'prepared live CPV count is invalid'
    reconcile_activation 0
    trap - EXIT HUP INT TERM
    printf 'PASS: checkpoint=%s action=reconcile state=%s evidence=%s\n' \
        "${CHECKPOINT_ID}" "${STATE}" "${REPORT}"
    exit 0
fi

if [[ ${ACTION} == finalize ]]; then
    REPORT_READY=1
    revalidate_all_tool_identities
    finalize_offline_restore_supervised
    trap - EXIT HUP INT TERM
    printf 'PASS: checkpoint=%s action=finalize-offline-restore state=%s evidence=%s\n' \
        "${CHECKPOINT_ID}" "${STATE}" "${REPORT}"
    exit 0
fi

for path in "${CACHE}" "${DURABLE}" "${REPORT}" "${STATE}" \
    "${CACHE_PARTIAL}" "${DURABLE_PARTIAL}" "${REPORT_PARTIAL}" \
    "${STATE_PREPARED}" "${STATE_ACTIVATED}" "${STATE_RESTORED}" \
    "${SELECTOR_PARTIAL}" "${SELECTOR_WITNESS}"; do
    path_absent "${path}" || die "refusing existing transaction path: ${path}"
done

# Create and publish the evidence root first.  Any later failure is therefore
# durable and attributable to this unique transaction ID.
${INSTALL} -d -o "${TRUST_UID}" -g "${TRUST_GID}" -m 0700 "${REPORT_PARTIAL}"
${INSTALL} -d -o "${TRUST_UID}" -g "${TRUST_GID}" -m 0700 "${REPORT_PARTIAL}/journal"
sync_paths "${REPORT_PARTIAL}/journal" "${REPORT_PARTIAL}" "${REPORT_PARENT}"
safe_publish_noreplace "${REPORT_PARTIAL}" "${REPORT}"
REPORT_READY=1
sync_paths "${REPORT}" "${REPORT_PARENT}"
journal_event initialized 'exclusive lock acquired; evidence root published'

printf 'logical_path\tresolved_path\tlogical_stat\tsha256\tsymlink_chain\n' \
    >"${REPORT}/tool-identities.tsv"
printf '%s\n' "${TOOL_IDENTITY_LINES[@]}" >>"${REPORT}/tool-identities.tsv"
printf 'path\tpath_stat\tfd_stat\n' >"${REPORT}/framework-lock-identities.tsv"
for lock_pair in \
    "${FRAMEWORK_LOCK_PATH}:${FRAMEWORK_LOCK_FD}" \
    "${PROJECT_LOCK_PATH}:${PROJECT_LOCK_FD}" \
    "${GENERATION_LOCK_PATH}:${GENERATION_LOCK_FD}"; do
    lock_name=${lock_pair%:*}
    lock_descriptor=${lock_pair##*:}
    printf '%s\t%s\t%s\n' "${lock_name}" "$(stat_fields "${lock_name}")" \
        "$(stat_follow_fields "/proc/${COORDINATOR_PID}/fd/${lock_descriptor}")" \
        >>"${REPORT}/framework-lock-identities.tsv"
done
sync_paths "${REPORT}/tool-identities.tsv" "${REPORT}"
preflight_containment_primitives
journal_event tools-validated 'tools, stable framework locks, PID namespace, kill-child, and pidfd primitives recorded'

validate_ancestor_chain "${EXPECTED_SOURCE_TARGET}"
[[ -d ${EXPECTED_SOURCE_TARGET} && ! -L ${EXPECTED_SOURCE_TARGET} ]] || \
    die 'expected source target is not a real directory'
source_fields=$(stat_fields "${EXPECTED_SOURCE_TARGET}") || die 'cannot stat expected source snapshot'
[[ ${source_fields} == *":${TRUST_UID}:${TRUST_GID}:700:"* ]] || \
    die 'expected source snapshot must have exact owner and mode 0700'
validate_regular_trusted_file "${EXPECTED_SOURCE_TARGET}/Packages" 0
validate_snapshot_tree_trust "${EXPECTED_SOURCE_TARGET}"
actual_packages_sha=$(${SHA256SUM} -- "${EXPECTED_SOURCE_TARGET}/Packages")
actual_packages_sha=${actual_packages_sha%% *}
[[ ${actual_packages_sha} == "${EXPECTED_SOURCE_PACKAGES_SHA256}" ]] || \
    die 'source Packages digest does not match the explicit expected identity'

EXPECTED_SELECTOR_IDENTITY=$(selector_identity "${SELECTOR}") || \
    die 'source selector is not a trusted absolute symlink to a snapshot'
selector_target=$(${READLINK} -- "${SELECTOR}")
[[ ${selector_target} == "${EXPECTED_SOURCE_TARGET}" ]] || \
    die 'source selector target does not match --expected-source-target'
EXPECTED_SOURCE_IDENTITY=$(stat_fields "${EXPECTED_SOURCE_TARGET}") || die 'cannot record source directory identity'
EXPECTED_SOURCE_PACKAGES_IDENTITY=$(stat_fields "${EXPECTED_SOURCE_TARGET}/Packages") || \
    die 'cannot record source Packages identity'
printf '%s\n' "${EXPECTED_SELECTOR_IDENTITY}" >"${REPORT}/source-selector.identity"
printf '%s\n' "${EXPECTED_SOURCE_IDENTITY}" >"${REPORT}/source-directory.identity"
printf '%s  %s\n' "${EXPECTED_SOURCE_PACKAGES_SHA256}" \
    "${EXPECTED_SOURCE_TARGET}/Packages" >"${REPORT}/source-packages.sha256"

capture_vdb_manifest "${REPORT}/vdb.before.tsv"
printf '%s\n' "${ATOMS[@]}" >"${REPORT}/quickpkg-atoms.txt"
verify_source_delta
require_selector_identity 'after source delta validation'
sync_paths "${REPORT}" "${REPORT}/source-verification.json" "${REPORT}/vdb.before.tsv"
journal_event source-validated 'source payloads, exact live delta, selector identity, and content-hashed VDB validated'

run_tracked "${REPORT}/cache-clone.log" "${REPORT}/cache-clone.stderr" 4h \
    "${CP}" -a --reflink="$([[ ${FIXTURE_MODE} -eq 1 ]] && printf auto || printf always)" \
    -- "${EXPECTED_SOURCE_TARGET}" "${CACHE_PARTIAL}"
[[ ${TRACKED_STATUS} -eq 0 ]] || die "cache staging clone failed with status ${TRACKED_STATUS}"
[[ -d ${CACHE_PARTIAL} && ! -L ${CACHE_PARTIAL} ]] || die 'cache staging clone failed'
${CHMOD} 0700 -- "${CACHE_PARTIAL}"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${CACHE_PARTIAL}"
validate_snapshot_tree_trust "${CACHE_PARTIAL}"

run_tracked "${REPORT}/quickpkg.log" "${REPORT}/quickpkg.stderr" 8h \
    "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C PATH="${PATH_VALUE}" \
    PKGDIR="${CACHE_PARTIAL}" TZ=UTC \
    "${QUICKPKG}" --ignore-default-opts --include-config=n "${ATOMS[@]}"
[[ ${TRACKED_STATUS} -eq 0 ]] || die "quickpkg failed with status ${TRACKED_STATUS}"
run_tracked "${REPORT}/emaint-fix.log" "${REPORT}/emaint-fix.stderr" 2h \
    "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C PATH="${PATH_VALUE}" \
    PKGDIR="${CACHE_PARTIAL}" TZ=UTC \
    "${EMAINT}" -f binhost
[[ ${TRACKED_STATUS} -eq 0 ]] || die "emaint -f binhost failed with status ${TRACKED_STATUS}"
run_tracked "${REPORT}/emaint-check.log" "${REPORT}/emaint-check.stderr" 2h \
    "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C PATH="${PATH_VALUE}" \
    PKGDIR="${CACHE_PARTIAL}" TZ=UTC \
    "${EMAINT}" -c binhost
[[ ${TRACKED_STATUS} -eq 0 ]] || die "emaint -c binhost failed with status ${TRACKED_STATUS}"

validate_snapshot_tree_trust "${CACHE_PARTIAL}"
verify_exact_final "${CACHE_PARTIAL}" "${REPORT}/cache-partial-verification.json"
revalidate_vdb before-cache-publication
require_selector_identity 'before cache publication'
sync_paths "${CACHE_PARTIAL}" "${CACHE_PARTIAL}/Packages" "${CACHE_PARENT}"
safe_publish_noreplace "${CACHE_PARTIAL}" "${CACHE}"
sync_paths "${CACHE}" "${CACHE}/Packages" "${CACHE_PARENT}"
validate_snapshot_tree_trust "${CACHE}"
verify_exact_final "${CACHE}" "${REPORT}/cache-final-verification.json"
cache_counts=${VERIFY_COUNTS}
write_final_snapshot_manifest "${CACHE}" "${REPORT}/cache-final-verification.json" cache-final
journal_event cache-published "inode-preserving no-replace publication;counts=${cache_counts}"

run_tracked "${REPORT}/durable-clone.log" "${REPORT}/durable-clone.stderr" 4h \
    "${CP}" -a --reflink="$([[ ${FIXTURE_MODE} -eq 1 ]] && printf auto || printf always)" \
    -- "${CACHE}" "${DURABLE_PARTIAL}"
[[ ${TRACKED_STATUS} -eq 0 ]] || die "durable staging clone failed with status ${TRACKED_STATUS}"
${CHMOD} 0700 -- "${DURABLE_PARTIAL}"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${DURABLE_PARTIAL}"
validate_snapshot_tree_trust "${DURABLE_PARTIAL}"
verify_exact_final "${DURABLE_PARTIAL}" "${REPORT}/durable-partial-verification.json"
revalidate_vdb before-durable-publication
require_selector_identity 'before durable publication'
sync_paths "${DURABLE_PARTIAL}" "${DURABLE_PARTIAL}/Packages" "${DURABLE_PARENT}"
safe_publish_noreplace "${DURABLE_PARTIAL}" "${DURABLE}"
sync_paths "${DURABLE}" "${DURABLE}/Packages" "${DURABLE_PARENT}"
validate_snapshot_tree_trust "${DURABLE}"
verify_exact_final "${DURABLE}" "${REPORT}/durable-final-verification.json"
durable_counts=${VERIFY_COUNTS}
write_final_snapshot_manifest "${DURABLE}" "${REPORT}/durable-final-verification.json" durable-final
journal_event durable-published "inode-preserving no-replace publication;counts=${durable_counts}"

# No report produced for a staging path is used as proof for a final path.  The
# two final-path reports above are the records bound into this evidence manifest.
revalidate_vdb before-evidence-publication
require_selector_identity 'before evidence publication'
source_identity_now=$(stat_fields "${EXPECTED_SOURCE_TARGET}") || die 'cannot re-read source directory identity'
source_packages_identity_now=$(stat_fields "${EXPECTED_SOURCE_TARGET}/Packages") || \
    die 'cannot re-read source Packages identity'
source_packages_sha_now=$(${SHA256SUM} -- "${EXPECTED_SOURCE_TARGET}/Packages")
source_packages_sha_now=${source_packages_sha_now%% *}
[[ ${source_identity_now} == "${EXPECTED_SOURCE_IDENTITY}" ]] || die 'source directory identity changed'
[[ ${source_packages_identity_now} == "${EXPECTED_SOURCE_PACKAGES_IDENTITY}" ]] || die 'source Packages metadata identity changed'
[[ ${source_packages_sha_now} == "${EXPECTED_SOURCE_PACKAGES_SHA256}" ]] || die 'source Packages content changed'

# Prepare (but do not mount) an exact active make.conf copy with only the
# reviewed parallel-install demotion appended.  The source is an immutable,
# root-trusted framework file and its exact identity is retained.
make_conf_fields=$(stat_fields "${MAKE_CONF}") || die 'cannot stat active make.conf'
make_conf_sha=$(${SHA256SUM} -- "${MAKE_CONF}") || die 'cannot hash active make.conf'
make_conf_sha=${make_conf_sha%% *}
[[ ${make_conf_fields} == "${EXPECTED_MAKE_CONF_IDENTITY}" && \
    ${make_conf_sha} == "${EXPECTED_MAKE_CONF_SHA256}" ]] || \
    die 'active make.conf changed while stable framework locks were held'
${CP} -a -- "${MAKE_CONF}" "${REPORT}/make.conf.freeze"
# shellcheck disable=SC2016 # Portage must expand FEATURES when sourcing the overlay.
printf '\n# Temporary exact-checkpoint mutation freeze.\nFEATURES="${FEATURES} -parallel-install"\n' \
    >>"${REPORT}/make.conf.freeze"
${CHMOD} 0600 -- "${REPORT}/make.conf.freeze"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${REPORT}/make.conf.freeze"
freeze_make_conf_fields=$(stat_fields "${REPORT}/make.conf.freeze") || \
    die 'cannot stat prepared make.conf freeze overlay'
freeze_make_conf_sha=$(${SHA256SUM} -- "${REPORT}/make.conf.freeze") || \
    die 'cannot hash prepared make.conf freeze overlay'
freeze_make_conf_sha=${freeze_make_conf_sha%% *}
printf '%s\t%s\t%s\n' "${MAKE_CONF}" "${make_conf_fields}" "${make_conf_sha}" \
    >"${REPORT}/make-conf-source.identity"
printf '%s\t%s\t%s\n' "${REPORT}/make.conf.freeze" "${freeze_make_conf_fields}" \
    "${freeze_make_conf_sha}" >"${REPORT}/make-conf-freeze.identity"

journal_pre_manifest=${REPORT}/journal-preactivation-manifest.sha256
: >"${journal_pre_manifest}"
journal_pre_paths=${REPORT}/journal-preactivation.paths0
materialize_sorted_find "${journal_pre_paths}" 10m "${REPORT}/journal" \
    -maxdepth 1 -type f -name '*.json'
while IFS= read -r -d '' journal_file; do
    ${SHA256SUM} -- "${journal_file}" >>"${journal_pre_manifest}"
done <"${journal_pre_paths}"
${CHMOD} 0600 -- "${journal_pre_manifest}"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${journal_pre_manifest}"

evidence_list=${REPORT}/evidence-files.list
evidence_manifest=${REPORT}/evidence-manifest.sha256
materialize_sorted_find "${evidence_list}" 10m "${REPORT}" -maxdepth 1 -type f \
    ! -name 'evidence-manifest.sha256' ! -name 'evidence-files.list'
: >"${evidence_manifest}"
while IFS= read -r -d '' evidence_file; do
    ${SHA256SUM} -- "${evidence_file}" >>"${evidence_manifest}"
done <"${evidence_list}"
${CHMOD} 0600 -- "${evidence_list}" "${evidence_manifest}"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${evidence_list}" "${evidence_manifest}"
evidence_manifest_sha=$(${SHA256SUM} -- "${evidence_manifest}")
evidence_manifest_sha=${evidence_manifest_sha%% *}
live_cpvs=${cache_counts%%:*}
completed_at=$(timestamp)

# This preparation record is deliberately non-authorizing.  It cannot claim
# exact activation or restoration while the final freeze/CAS gate is pending.
# shellcheck disable=SC2016 # jq variables, not shell expansions.
${JQ} -n --arg id "${CHECKPOINT_ID}" --arg prepared_at "${completed_at}" \
    --arg cache "${CACHE}" --arg durable "${DURABLE}" --arg selector "${SELECTOR}" \
    --arg source "${EXPECTED_SOURCE_TARGET}" --arg source_sha "${EXPECTED_SOURCE_PACKAGES_SHA256}" \
    --arg report "${REPORT}" --arg evidence_sha "${evidence_manifest_sha}" \
    --arg expected_selector_identity "${EXPECTED_SELECTOR_IDENTITY}" \
    --argjson live_cpvs "${live_cpvs}" \
    '{schema_version:1,control:"exact-live-binpkg-checkpoint",
      checkpoint_id:$id,status:"artifact-generations-verified-final-freeze-pending",
      prepared_at:$prepared_at,live_cpvs:$live_cpvs,
      source:{path:$source,packages_sha256:$source_sha,
        exact_delta_only:true,full_gpkg_payloads_validated:true},
      cache_checkpoint:{path:$cache,indexed_cpvs:$live_cpvs,
        gpkg_archives_validated:$live_cpvs,image_streams_tested:$live_cpvs,
        missing_total:0,extra_total:0,archive_failure_total:0,payload_failure_total:0},
      durable_checkpoint:{path:$durable,indexed_cpvs:$live_cpvs,
        gpkg_archives_validated:$live_cpvs,image_streams_tested:$live_cpvs,
        missing_total:0,extra_total:0,archive_failure_total:0,payload_failure_total:0},
      activation_intent:{selector:$selector,target:$durable,
        expected_old_identity:$expected_selector_identity,
        guard:"exclusive-lock plus exact pre-rename identity comparison"},
      evidence:{directory:$report,manifest_sha256:$evidence_sha},
      offline_restoration_tested:false,
      pending_total:1,unknown_total:0,failed_total:0}' \
    >"${REPORT}/artifact-preparation-state.json"
${CHMOD} 0600 -- "${REPORT}/artifact-preparation-state.json"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${REPORT}/artifact-preparation-state.json"
sync_paths "${REPORT}/artifact-preparation-state.json" "${evidence_manifest}" "${REPORT}"
journal_event prepared-for-final-freeze 'cache and durable artifacts are verified; final source/VDB freeze and selector CAS remain pending'

# Re-read every source payload and content-hash every VDB object after the
# durable state and prepared journal exist.  These late reports are bound by a
# separate immutable activation intent.  Recovery is deterministic after any
# crash: old selector means prepared/not activated, exact new selector means
# activated, and every other identity is a lost update that must fail closed.
verify_source_delta "${REPORT}/source-final-preactivation-verification.json"
scan_portage_processes "${REPORT}/portage-processes.before-overlay.tsv"

# shellcheck disable=SC2016 # jq variables, not shell expansions.
${JQ} -n --arg id "${CHECKPOINT_ID}" --arg at "$(timestamp)" \
    --arg make_conf "${MAKE_CONF}" --arg overlay "${REPORT}/make.conf.freeze" \
    --arg vdb "${VDB}" --arg selector "${SELECTOR}" --arg target "${DURABLE}" \
    '{schema_version:1,checkpoint_id:$id,status:"freeze-intent-durable",
      recorded_at:$at,make_conf:$make_conf,overlay:$overlay,vdb:$vdb,
      selector:$selector,target:$target,
      crash_recovery:"unmount make_conf if still overlaid; selector identity plus activation intent determines activation"}' \
    >"${REPORT}/freeze-intent.json"
${CHMOD} 0600 -- "${REPORT}/freeze-intent.json"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${REPORT}/freeze-intent.json"
sync_paths "${REPORT}/freeze-intent.json" "${REPORT}" "${REPORT_PARENT}"

activate_make_conf_overlay
scan_portage_processes "${REPORT}/portage-processes.after-overlay.tsv"
start_portage_vdb_lock
scan_vdb_handles "${REPORT}/vdb-handles.after-lock.tsv"
revalidate_vdb activation
require_selector_identity 'after final source and VDB validation'

journal_activation_manifest=${REPORT}/journal-activation-manifest.sha256
: >"${journal_activation_manifest}"
journal_activation_paths=${REPORT}/journal-activation.paths0
materialize_sorted_find "${journal_activation_paths}" 10m "${REPORT}/journal" \
    -maxdepth 1 -type f -name '*.json'
while IFS= read -r -d '' journal_file; do
    ${SHA256SUM} -- "${journal_file}" >>"${journal_activation_manifest}"
done <"${journal_activation_paths}"
${CHMOD} 0600 -- "${journal_activation_manifest}"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${journal_activation_manifest}"

activation_evidence_manifest=${REPORT}/activation-evidence-manifest.sha256
: >"${activation_evidence_manifest}"
for activation_evidence in \
    "${REPORT}/source-final-preactivation-verification.json" \
    "${REPORT}/source-final-preactivation-verification.missing-cpvs.txt" \
    "${REPORT}/source-final-preactivation-verification.requested-delta-cpvs.txt" \
    "${REPORT}/vdb.activation.tsv" "${REPORT}/freeze-intent.json" \
    "${REPORT}/make-conf-mount.before.json" "${REPORT}/make-conf-mount.overlay.json" \
    "${REPORT}/portage-features.freeze.txt" "${REPORT}/portage-vdb-lock.ready.json" \
    "${REPORT}/portage-processes.before-overlay.tsv" \
    "${REPORT}/portage-processes.after-overlay.tsv" \
    "${REPORT}/vdb-handles.after-lock.tsv" "${journal_activation_manifest}" \
    "${evidence_manifest}"; do
    ${SHA256SUM} -- "${activation_evidence}" >>"${activation_evidence_manifest}"
done
${CHMOD} 0600 -- "${activation_evidence_manifest}"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${activation_evidence_manifest}"
activation_evidence_sha=$(${SHA256SUM} -- "${activation_evidence_manifest}")
activation_evidence_sha=${activation_evidence_sha%% *}
delta_binding_path=${REPORT}/source-final-preactivation-verification.requested-delta-cpvs.txt
delta_binding_sha=$(${SHA256SUM} -- "${delta_binding_path}") || die 'cannot hash final sorted delta binding'
delta_binding_sha=${delta_binding_sha%% *}
delta_binding_count=${#ATOM_CPVS[@]}
artifact_preparation_sha=$(${SHA256SUM} -- "${REPORT}/artifact-preparation-state.json") || \
    die 'cannot hash artifact preparation state'
artifact_preparation_sha=${artifact_preparation_sha%% *}
revalidate_all_tool_identities

# shellcheck disable=SC2016 # jq variables, not shell expansions.
${JQ} -n --arg id "${CHECKPOINT_ID}" --arg prepared_at "$(timestamp)" \
    --arg selector "${SELECTOR}" --arg expected_old "${EXPECTED_SELECTOR_IDENTITY}" \
    --arg target "${DURABLE}" --arg state "${STATE}" \
    --arg activation_evidence "${activation_evidence_manifest}" \
    --arg activation_evidence_sha "${activation_evidence_sha}" \
    --arg source "${EXPECTED_SOURCE_TARGET}" \
    --arg source_sha "${EXPECTED_SOURCE_PACKAGES_SHA256}" \
    --arg verifier "${VERIFIER}" --arg verifier_sha "${EXPECTED_VERIFIER_SHA256}" \
    --arg delta_path "${delta_binding_path}" --arg delta_sha "${delta_binding_sha}" \
    --arg preparation "${REPORT}/artifact-preparation-state.json" \
    --arg preparation_sha "${artifact_preparation_sha}" --argjson live_cpvs "${live_cpvs}" \
    --argjson delta_count "${delta_binding_count}" \
    '{schema_version:1,checkpoint_id:$id,status:"prepared",
      prepared_at:$prepared_at,selector:$selector,
      expected_old_selector_identity:$expected_old,target:$target,state:$state,
      input_bindings:{source:{path:$source,packages_sha256:$source_sha},
        verifier:{path:$verifier,sha256:$verifier_sha},
        delta:{sorted_cpvs_path:$delta_path,sorted_cpvs_sha256:$delta_sha,count:$delta_count},
        artifact_preparation:{path:$preparation,sha256:$preparation_sha,live_cpvs:$live_cpvs}},
      activation_evidence:{path:$activation_evidence,sha256:$activation_evidence_sha},
      recovery_rule:"old=not-activated; exact-target=activated; anything-else=lost-update"}' \
    >"${ACTIVATION_INTENT}.partial"
${CHMOD} 0600 -- "${ACTIVATION_INTENT}.partial"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${ACTIVATION_INTENT}.partial"
sync_paths "${ACTIVATION_INTENT}.partial" "${REPORT}"
crash_point before-intent-publication
safe_publish_noreplace "${ACTIVATION_INTENT}.partial" "${ACTIVATION_INTENT}"
activation_intent_sha=$(${SHA256SUM} -- "${ACTIVATION_INTENT}")
activation_intent_sha=${activation_intent_sha%% *}

# The intent is the durable linearization prerequisite.  From this point, the
# idempotent reconciler owns every transition: durable prepared selector,
# exact exchange, named displaced-selector witness, immutable receipt, and the
# activated/offline-restore-pending state.
crash_point after-intent
revalidate_all_tool_identities
reconcile_activation 1

trap - EXIT HUP INT TERM
printf 'PASS: checkpoint=%s live_cpvs=%s cache=%s durable=%s state=%s evidence=%s\n' \
    "${CHECKPOINT_ID}" "${live_cpvs}" "${CACHE}" "${DURABLE}" "${STATE}" "${REPORT}"

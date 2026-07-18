#!/usr/bin/env bash
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
REPORT_READY=0
ACTIVATION_STARTED=0
ACTIVATION_COMPLETE=0
PORTAGE_LOCK_PID=
PORTAGE_LOCK_PGID=
MAKE_CONF_OVERLAY_ACTIVE=0
IN_FAILURE_TRAP=0
ACTIVE_CHILD_PID=
ACTIVE_CHILD_PGID=
ACTIVE_CHILD_STARTTIME=
TRACKED_STATUS=0
VERIFIER_STATUS=0
VERIFY_COUNTS=
TRAVERSAL_SEQUENCE=0
JOURNAL_SEQUENCE=0
CURRENT_PHASE=bootstrap
EXPECTED_SELECTOR_IDENTITY=
EXPECTED_SOURCE_IDENTITY=
EXPECTED_SOURCE_PACKAGES_IDENTITY=
EXPECTED_VERIFIER_IDENTITY=
EXPECTED_MAKE_CONF_IDENTITY=
EXPECTED_MAKE_CONF_SHA256=
PREPARED_WITNESS_IDENTITY=
LIVE_CPVS=
ACTIVATION_INTENT_SHA256=
REPORT=
STATE=
CACHE=
DURABLE=
CACHE_PARTIAL=
DURABLE_PARTIAL=
STATE_PARTIAL=
SELECTOR_PARTIAL=
SELECTOR_WITNESS=
REPORT_PARTIAL=
ACTIVATION_INTENT=
ACTIVATION_RECEIPT=
STATE_PRECAS=
SELF=

declare -a ATOMS=()
declare -a ATOM_CPVS=()
declare -a TOOL_NAMES=(
    bash chmod chown cmp cp date emaint env find findmnt flock install jq ln mount mv
    portageq python3 quickpkg readlink setsid sha256sum sleep sort stat sync timeout umount
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

The fixture-only path switches are rejected in production mode.  Checkpoint
publication is intentionally not resumable under the same ID: an interrupted
transaction leaves an immutable journal and must be inspected before a new ID
is used.
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
readonly EMAINT=${TOOL[emaint]}
readonly ENV_TOOL=${TOOL[env]}
readonly FIND=${TOOL[find]}
readonly FINDMNT=${TOOL[findmnt]}
readonly FLOCK=${TOOL[flock]}
readonly INSTALL=${TOOL[install]}
readonly JQ=${TOOL[jq]}
readonly LN=${TOOL[ln]}
readonly MOUNT=${TOOL[mount]}
readonly MV=${TOOL[mv]}
readonly PORTAGEQ=${TOOL[portageq]}
readonly PYTHON=${TOOL[python3]}
readonly QUICKPKG=${TOOL[quickpkg]}
readonly READLINK=${TOOL[readlink]}
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
STATE_PARTIAL=${STATE_PARENT}/.binpkg-checkpoint-${CHECKPOINT_ID}.partial.${COORDINATOR_PID}.json
STATE_PRECAS=${STATE_PARENT}/binpkg-checkpoint-${CHECKPOINT_ID}.pre-cas-pending.json
SELECTOR_WITNESS=${CACHE_PARENT}/critical-current.previous-${CHECKPOINT_ID}
ACTIVATION_INTENT=${REPORT}/activation-intent.json
ACTIVATION_RECEIPT=${REPORT}/activation-receipt.json

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
    [[ ${type} == directory && ${uid} == "${TRUST_UID}" && ${mode} == 750 ]] || \
        die 'stable framework lock directory has an untrusted identity'
    open_shared_framework_lock "${FRAMEWORK_LOCK_PATH}" FRAMEWORK_LOCK_FD "${gid}"
    open_shared_framework_lock "${PROJECT_LOCK_PATH}" PROJECT_LOCK_FD "${gid}"
    open_shared_framework_lock "${GENERATION_LOCK_PATH}" GENERATION_LOCK_FD "${gid}"
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
    [[ ! -e $1 && ! -L $1 ]]
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
    ACTIVE_CHILD_PGID=
    ACTIVE_CHILD_STARTTIME=
    return "${status}"
}

release_portage_vdb_lock() {
    local index state
    [[ -n ${PORTAGE_LOCK_PID} ]] || return 0
    kill -TERM -- "${PORTAGE_LOCK_PID}" 2>/dev/null || :
    for ((index = 0; index < 100; index++)); do
        [[ -r /proc/${PORTAGE_LOCK_PID}/stat ]] || break
        read -r _ _ state _ <"/proc/${PORTAGE_LOCK_PID}/stat" || break
        [[ ${state} == Z ]] && break
        ${SLEEP} 0.05
    done
    if [[ -r /proc/${PORTAGE_LOCK_PID}/stat ]]; then
        kill -KILL -- "-${PORTAGE_LOCK_PGID}" 2>/dev/null || :
    fi
    wait "${PORTAGE_LOCK_PID}" 2>/dev/null || :
    PORTAGE_LOCK_PID=
    PORTAGE_LOCK_PGID=
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
    local output=$1 proc pid object target map_line
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
        if [[ -r ${proc}/maps ]]; then
            while IFS= read -r map_line; do
                [[ ${map_line} == *" ${VDB}"* ]] || continue
                printf '%s\tmaps\t%s\n' "${pid}" "${map_line}" >>"${output}"
            done <"${proc}/maps"
        fi
    done
    [[ ! -s ${output} ]] || die 'a pre-freeze process retains a VDB path or mapping'
}

activate_make_conf_overlay() {
    local before=${REPORT}/make-conf-mount.before.json after=${REPORT}/make-conf-mount.overlay.json
    local mounted_fields overlay_fields mounted_sha overlay_sha
    ${FINDMNT} --json --target "${MAKE_CONF}" -o TARGET,SOURCE,FSTYPE,OPTIONS >"${before}" || \
        die 'cannot record make.conf mount state before freeze'
    run_tracked "${REPORT}/make-conf-bind-mount.log" "${REPORT}/make-conf-bind-mount.stderr" 2m \
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
        >"${REPORT}/make-conf-mounted.identity"
    run_tracked "${REPORT}/portage-features.freeze.txt" "${REPORT}/portage-features.freeze.stderr" 2m \
        "${ENV_TOOL}" -i HOME="${HOME_DIR}" LANG=C LC_ALL=C PATH="${PATH_VALUE}" TZ=UTC \
        "${PORTAGEQ}" envvar FEATURES
    [[ ${TRACKED_STATUS} -eq 0 ]] || die "portageq FEATURES probe failed with status ${TRACKED_STATUS}"
    features=$(<"${REPORT}/portage-features.freeze.txt")
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
    local ready=${REPORT}/portage-vdb-lock.ready.json index code ready_pid
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
    PORTAGE_LOCK_PGID=${PORTAGE_LOCK_PID}
    for ((index = 0; index < 3000; index++)); do
        [[ -f ${ready} ]] && break
        kill -0 "${PORTAGE_LOCK_PID}" 2>/dev/null || \
            die 'Portage VDB lock holder exited before readiness publication'
        ${SLEEP} 0.1
    done
    [[ -f ${ready} ]] || die 'timed out acquiring the real Portage VDB lock'
    ready_pid=$(${JQ} -r '.pid' "${ready}")
    [[ ${ready_pid} == "${PORTAGE_LOCK_PID}" ]] || die 'Portage VDB lock readiness PID mismatch'
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
    ACTIVE_CHILD_PGID=${ACTIVE_CHILD_PID}
    for _ in {1..100}; do
        read_proc_identity "${ACTIVE_CHILD_PID}" state start && break
        ${SLEEP} 0.01
    done
    [[ -n ${start:-} ]] || die 'contained child disappeared before identity capture'
    ACTIVE_CHILD_STARTTIME=${start}
    wait "${ACTIVE_CHILD_PID}" || status=$?
    ACTIVE_CHILD_PID=
    ACTIVE_CHILD_PGID=
    ACTIVE_CHILD_STARTTIME=
    [[ ${status} -ne 124 && ${status} -ne 137 ]] || \
        die "bounded child timed out or required SIGKILL (status=${status}): $1"
    TRACKED_STATUS=${status}
}

preflight_containment_primitives() {
    local code
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
    local output=${REPORT}/tool-identities.final.tsv tool_name
    printf 'logical_path\tresolved_path\tlogical_stat\tsha256\tsymlink_chain\n' >"${output}"
    for tool_name in "${TOOL_NAMES[@]}"; do
        tool_identity_line "${TOOL[${tool_name}]}" >>"${output}"
    done
    tool_identity_line "${VERIFIER}" >>"${output}"
    tool_identity_line "${SELF}" >>"${output}"
    ${CMP} -- "${REPORT}/tool-identities.tsv" "${output}" || \
        die 'a trusted tool identity changed during checkpoint creation'
}

materialize_sorted_find() {
    local output=$1 deadline=$2 raw
    shift 2
    ((TRAVERSAL_SEQUENCE += 1))
    raw=${REPORT}/traversal.$(printf '%03d' "${TRAVERSAL_SEQUENCE}").unsorted.paths0
    run_tracked "${raw}" "${raw}.stderr" "${deadline}" "${FIND}" "$@" -print0
    [[ ${TRACKED_STATUS} -eq 0 ]] || die "find traversal failed with status ${TRACKED_STATUS}: $1"
    run_tracked "${output}" "${output}.sort.stderr" "${deadline}" "${SORT}" -z -- "${raw}"
    [[ ${TRACKED_STATUS} -eq 0 ]] || die "sorted traversal materialization failed with status ${TRACKED_STATUS}: $1"
}

timestamp() {
    ${DATE} -u '+%Y-%m-%dT%H:%M:%SZ'
}

journal_event() {
    local phase=$1 detail=$2 sequence file partial
    ((REPORT_READY)) || return 0
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
    local snapshot=$1 path fields uid gid mode type paths
    paths=${REPORT}/snapshot-traversal.$(printf '%03d' "$((TRAVERSAL_SEQUENCE + 1))").paths0
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

failure_trap() {
    local status=$? actual unchanged=unknown
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
        printf 'status=%s\nphase=%s\nselector_unchanged=%s\nactivation_started=%s\nactivation_complete=%s\n' \
            "${status}" "${CURRENT_PHASE}" "${unchanged}" "${ACTIVATION_STARTED}" \
            "${ACTIVATION_COMPLETE}" >"${REPORT}/failure.txt.partial.${COORDINATOR_PID}"
        ${CHMOD} 0600 -- "${REPORT}/failure.txt.partial.${COORDINATOR_PID}" >/dev/null 2>&1
        ${MV} --no-clobber --no-copy -T -- "${REPORT}/failure.txt.partial.${COORDINATOR_PID}" \
            "${REPORT}/failure.txt" >/dev/null 2>&1
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
actual_bash=$(${READLINK} -e -- "/proc/${COORDINATOR_PID}/exe") || die 'cannot resolve active Bash interpreter'
[[ ${actual_bash} == "${expected_bash}" ]] || \
    die "checkpoint is running under an untrusted Bash interpreter: ${actual_bash}"

# Hold the same three stable framework locks as the installed publisher, in
# the publisher's global order.  This pins the framework-current resolution,
# active make.conf, and generated project/generation policy for the complete
# checkpoint transaction.
acquire_framework_freeze_locks
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
if path_absent "${LOCK_PATH}"; then
    lock_partial=${LOCK_PATH}.partial.${COORDINATOR_PID}
    path_absent "${lock_partial}" || die "stale lock staging path: ${lock_partial}"
    ${INSTALL} -o "${TRUST_UID}" -g "${TRUST_GID}" -m 0600 /dev/null "${lock_partial}"
    safe_publish_noreplace "${lock_partial}" "${LOCK_PATH}"
    sync_paths "${LOCK_PATH}" "${LOCK_PATH%/*}"
fi
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

for path in "${CACHE}" "${DURABLE}" "${REPORT}" "${STATE}" \
    "${CACHE_PARTIAL}" "${DURABLE_PARTIAL}" "${REPORT_PARTIAL}" \
    "${STATE_PARTIAL}" "${SELECTOR_PARTIAL}"; do
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

# shellcheck disable=SC2016 # jq variables, not shell expansions.
${JQ} -n --arg id "${CHECKPOINT_ID}" --arg prepared_at "$(timestamp)" \
    --arg selector "${SELECTOR}" --arg expected_old "${EXPECTED_SELECTOR_IDENTITY}" \
    --arg target "${DURABLE}" --arg state "${STATE}" \
    --arg activation_evidence "${activation_evidence_manifest}" \
    --arg activation_evidence_sha "${activation_evidence_sha}" \
    '{schema_version:1,checkpoint_id:$id,status:"prepared",
      prepared_at:$prepared_at,selector:$selector,
      expected_old_selector_identity:$expected_old,target:$target,state:$state,
      activation_evidence:{path:$activation_evidence,sha256:$activation_evidence_sha},
      recovery_rule:"old=not-activated; exact-target=activated; anything-else=lost-update"}' \
    >"${ACTIVATION_INTENT}.partial.${COORDINATOR_PID}"
${CHMOD} 0600 -- "${ACTIVATION_INTENT}.partial.${COORDINATOR_PID}"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${ACTIVATION_INTENT}.partial.${COORDINATOR_PID}"
sync_paths "${ACTIVATION_INTENT}.partial.${COORDINATOR_PID}" "${REPORT}"
safe_publish_noreplace "${ACTIVATION_INTENT}.partial.${COORDINATOR_PID}" "${ACTIVATION_INTENT}"
activation_intent_sha=$(${SHA256SUM} -- "${ACTIVATION_INTENT}")
activation_intent_sha=${activation_intent_sha%% *}

# Publish a canonical but explicitly nonterminal state only after every late
# check passes while the real Portage VDB lock remains held.  Selector+intent,
# not this pending state alone, determines crash recovery.
# shellcheck disable=SC2016 # jq variables, not shell expansions.
${JQ} -n --arg id "${CHECKPOINT_ID}" --arg prepared_at "$(timestamp)" \
    --arg cache "${CACHE}" --arg durable "${DURABLE}" --arg selector "${SELECTOR}" \
    --arg intent "${ACTIVATION_INTENT}" --arg intent_sha "${activation_intent_sha}" \
    --argjson live_cpvs "${live_cpvs}" \
    '{schema_version:1,control:"exact-live-binpkg-checkpoint",
      checkpoint_id:$id,status:"verified-final-freeze-held-selector-activation-pending",
      prepared_at:$prepared_at,live_cpvs:$live_cpvs,
      cache_checkpoint:{path:$cache,indexed_cpvs:$live_cpvs},
      durable_checkpoint:{path:$durable,indexed_cpvs:$live_cpvs},
      activation:{selector:$selector,intent:$intent,intent_sha256:$intent_sha,
        crash_recovery:"selector plus intent is authoritative"},
      offline_restoration_tested:false,
      pending_total:1,unknown_total:0,failed_total:0}' >"${STATE_PARTIAL}"
${CHMOD} 0600 -- "${STATE_PARTIAL}"
${CHOWN} "${TRUST_UID}:${TRUST_GID}" -- "${STATE_PARTIAL}"
sync_paths "${STATE_PARTIAL}" "${STATE_PARENT}"
safe_publish_noreplace "${STATE_PARTIAL}" "${STATE}"
sync_paths "${ACTIVATION_INTENT}" "${activation_evidence_manifest}" "${REPORT}" \
    "${REPORT_PARENT}" "${STATE}" "${STATE_PARENT}"

# This guarded compare-and-swap atomically exchanges the prepared and live
# symlinks.  The displaced live object is then verified against the exact old
# identity.  A near-rename noncooperating update is exchanged back before the
# transaction fails, so it is never silently overwritten.
require_selector_identity 'immediately before selector compare-and-swap'
sync_paths "${REPORT}" "${STATE}" "${CACHE}" "${DURABLE}" \
    "${REPORT_PARENT}" "${STATE_PARENT}" "${CACHE_PARENT}" "${DURABLE_PARENT}"

${LN} -s -- "${DURABLE}" "${SELECTOR_PARTIAL}"
${CHOWN} -h "${TRUST_UID}:${TRUST_GID}" -- "${SELECTOR_PARTIAL}"
selector_partial_fields=$(stat_fields "${SELECTOR_PARTIAL}") || die 'cannot stat prepared selector'
ACTIVATION_STARTED=1
${MV} --exchange --no-copy -T -- "${SELECTOR_PARTIAL}" "${SELECTOR}"
sync_paths "${SELECTOR%/*}"
[[ -L ${SELECTOR} ]] || die 'activated selector is not a symlink'
[[ -L ${SELECTOR_PARTIAL} ]] || die 'atomic exchange did not retain the displaced selector'
displaced_selector_identity=$(selector_identity "${SELECTOR_PARTIAL}") || \
    die 'atomic exchange displaced an unreadable selector identity'
if [[ ${displaced_selector_identity} != "${EXPECTED_SELECTOR_IDENTITY}" ]]; then
    unexpected_selector_identity=${displaced_selector_identity}
    ${MV} --exchange --no-copy -T -- "${SELECTOR_PARTIAL}" "${SELECTOR}"
    sync_paths "${SELECTOR%/*}"
    restored_selector_identity=$(selector_identity "${SELECTOR}") || \
        die 'selector CAS rollback did not restore a readable selector'
    [[ ${restored_selector_identity} == "${unexpected_selector_identity}" ]] || \
        die 'selector CAS rollback did not restore the noncooperating update'
    ACTIVATION_STARTED=0
    die 'selector CAS captured a near-rename lost update and rolled it back'
fi
activated_selector_fields=$(stat_fields "${SELECTOR}") || die 'cannot stat activated selector'
[[ $(device_inode_from_fields "${selector_partial_fields}") == \
    "$(device_inode_from_fields "${activated_selector_fields}")" ]] || \
    die 'activated selector does not have the prepared symlink device/inode'
[[ $(${READLINK} -- "${SELECTOR}") == "${DURABLE}" ]] || \
    die 'activated selector target is not the exact durable checkpoint'
ACTIVATION_COMPLETE=1
release_portage_vdb_lock
deactivate_make_conf_overlay || \
    die 'selector activated but temporary make.conf overlay could not be unmounted'

trap - EXIT HUP INT TERM
printf 'PASS: checkpoint=%s live_cpvs=%s cache=%s durable=%s state=%s evidence=%s\n' \
    "${CHECKPOINT_ID}" "${live_cpvs}" "${CACHE}" "${DURABLE}" "${STATE}" "${REPORT}"

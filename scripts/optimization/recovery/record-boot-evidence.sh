#!/usr/bin/env bash
# Capture one pending post-reboot validation request without making startup fail.
#
# This is suitable for installation as an OpenRC local.d startup hook.  The
# live default ESP is /efi.  Tests must use --fixture-mode with isolated roots
# and fixture tools; fixture mode never falls back to live probe commands.

set -uo pipefail
IFS=$'\n\t'
umask 077

readonly PROGRAM=${0##*/}
readonly SCHEMA_VERSION=1

FIXTURE_MODE=${BOOT_EVIDENCE_FIXTURE_MODE:-0}
SYSTEM_ROOT=${BOOT_EVIDENCE_ROOT:-/}
STATE_ROOT=${BOOT_EVIDENCE_STATE_ROOT:-${RECOVERY_STATE_ROOT:-}}
ESP_ROOT=${BOOT_EVIDENCE_ESP_ROOT:-${RECOVERY_ESP_ROOT:-}}
TOOL_ROOT=${BOOT_EVIDENCE_TOOL_ROOT:-${RECOVERY_TOOL_ROOT:-}}
MARKER_RELATIVE=${BOOT_EVIDENCE_MARKER:-recovery/boot-validation.pending}
EVIDENCE_RELATIVE=${BOOT_EVIDENCE_DIR:-reports/recovery/boot-evidence}
PROBE_TIMEOUT=${BOOT_EVIDENCE_TIMEOUT:-30}

MARKER=
EVIDENCE_DIR=
EVIDENCE_TEMP=
EVIDENCE_FINAL=
WORK_DIR=
LOCK_FD=
MARKER_UID=
MARKER_MODE=
RECORDED_AT=
BOOT_ID=
UNAME_RELEASE=
BOOT_CURRENT=
ROOT_SOURCE=
RESULT_STATUS=failed
PROBE_FAILURES=0
VALIDATION_FAILURES=0
declare -a FAILURES=()
declare -A MARKER_FIELDS=()

usage() {
    cat <<EOF
Usage: ${PROGRAM} [OPTIONS]

Capture a pending reboot marker and atomically publish its evidence.

Options:
  --root DIR          System root used for /proc, /etc, and fixture paths.
  --state-root DIR    Persistent state root (default /var/lib/gentoo-optimization).
  --esp-root DIR      EFI System Partition mount (default ROOT/efi).
  --tool-root DIR     Directory containing fixture probe tools.
  --marker RELPATH    Marker below STATE_ROOT.
  --evidence-dir RELPATH
                      Evidence directory below STATE_ROOT.
  --fixture-mode      Permit a non-/ isolated root owned by the invoking user.
  -h, --help          Show this help.

Recognized marker keys (lowercase is canonical):
  attempt_id, expected_bootnum, expected_kernel_release,
  expected_root_source (optional), expected_kernel_path,
  expected_kernel_sha256, expected_initramfs_path, and
  expected_initramfs_sha256.

The hook always returns success after argument parsing so evidence or probe
failures cannot stop OpenRC.  A completed marker is retained and is a no-op on
later invocations.
EOF
}

timestamp() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

notice() {
    printf '%s [%s] %s\n' "$(timestamp)" "${PROGRAM}" "$*" >&2
}

option_value() {
    (($# >= 2)) || return 1
    [[ -n $2 ]]
}

parse_options() {
    while (($#)); do
        case $1 in
            --fixture-mode)
                FIXTURE_MODE=1
                shift
                ;;
            --root)
                option_value "$@" || return 2
                SYSTEM_ROOT=$2
                shift 2
                ;;
            --state-root)
                option_value "$@" || return 2
                STATE_ROOT=$2
                shift 2
                ;;
            --esp-root)
                option_value "$@" || return 2
                ESP_ROOT=$2
                shift 2
                ;;
            --tool-root)
                option_value "$@" || return 2
                TOOL_ROOT=$2
                shift 2
                ;;
            --marker)
                option_value "$@" || return 2
                MARKER_RELATIVE=$2
                shift 2
                ;;
            --evidence-dir)
                option_value "$@" || return 2
                EVIDENCE_RELATIVE=$2
                shift 2
                ;;
            -h|--help)
                usage
                return 10
                ;;
            --*)
                notice "unknown option: $1"
                return 2
                ;;
            *)
                notice "unexpected positional argument: $1"
                return 2
                ;;
        esac
    done
}

clean_absolute_path() {
    local value=$1 label=$2
    [[ ${value} == /* ]] || {
        notice "${label} must be absolute: ${value}"
        return 1
    }
    while [[ ${value} != / && ${value} == */ ]]; do
        value=${value%/}
    done
    if [[ ${value} != / ]]; then
        case ${value}/ in
            *'/../'*|*'/./'*|*'//'*)
                notice "${label} contains a non-canonical path component: ${value}"
                return 1
                ;;
        esac
    fi
    printf '%s\n' "${value}"
}

clean_relative_path() {
    local value=$1 label=$2
    [[ -n ${value} && ${value} != /* ]] || {
        notice "${label} must be a nonempty relative path"
        return 1
    }
    while [[ ${value} == */ ]]; do
        value=${value%/}
    done
    case /${value}/ in
        *'/../'*|*'/./'*|*'//'*)
            notice "${label} contains an unsafe path component: ${value}"
            return 1
            ;;
    esac
    [[ -n ${value} ]] || return 1
    printf '%s\n' "${value}"
}

root_path() {
    local logical=$1
    if [[ ${SYSTEM_ROOT} == / ]]; then
        printf '%s\n' "${logical}"
    else
        printf '%s%s\n' "${SYSTEM_ROOT}" "${logical}"
    fi
}

path_is_below() {
    local child=$1 parent=$2
    [[ ${child} == "${parent}" || ${child} == "${parent}"/* ]]
}

configure_paths() {
    SYSTEM_ROOT=$(clean_absolute_path "${SYSTEM_ROOT}" 'system root') || return 1
    if [[ -z ${STATE_ROOT} ]]; then
        STATE_ROOT=$(root_path /var/lib/gentoo-optimization)
    fi
    if [[ -z ${ESP_ROOT} ]]; then
        ESP_ROOT=$(root_path /efi)
    fi
    STATE_ROOT=$(clean_absolute_path "${STATE_ROOT}" 'state root') || return 1
    ESP_ROOT=$(clean_absolute_path "${ESP_ROOT}" 'ESP root') || return 1
    MARKER_RELATIVE=$(clean_relative_path "${MARKER_RELATIVE}" 'marker') || return 1
    EVIDENCE_RELATIVE=$(clean_relative_path "${EVIDENCE_RELATIVE}" 'evidence directory') || return 1
    MARKER=${STATE_ROOT}/${MARKER_RELATIVE}
    EVIDENCE_DIR=${STATE_ROOT}/${EVIDENCE_RELATIVE}

    if [[ ! ${PROBE_TIMEOUT} =~ ^[1-9][0-9]*$ ]] || ((PROBE_TIMEOUT > 300)); then
        notice 'BOOT_EVIDENCE_TIMEOUT must be an integer from 1 through 300 seconds'
        return 1
    fi
    command -v timeout >/dev/null 2>&1 || {
        notice 'the coreutils timeout command is required'
        return 1
    }

    case ${FIXTURE_MODE} in
        0|1) ;;
        *)
            notice 'BOOT_EVIDENCE_FIXTURE_MODE must be 0 or 1'
            return 1
            ;;
    esac

    if ((FIXTURE_MODE)); then
        [[ ${SYSTEM_ROOT} != / ]] || {
            notice 'fixture mode requires a non-/ system root'
            return 1
        }
        [[ -n ${TOOL_ROOT} ]] || {
            notice 'fixture mode requires --tool-root'
            return 1
        }
        TOOL_ROOT=$(clean_absolute_path "${TOOL_ROOT}" 'tool root') || return 1
        path_is_below "${STATE_ROOT}" "${SYSTEM_ROOT}" || {
            notice 'fixture state root must be below the fixture system root'
            return 1
        }
        path_is_below "${ESP_ROOT}" "${SYSTEM_ROOT}" || {
            notice 'fixture ESP root must be below the fixture system root'
            return 1
        }
        path_is_below "${TOOL_ROOT}" "${SYSTEM_ROOT}" || {
            notice 'fixture tool root must be below the fixture system root'
            return 1
        }
    else
        [[ ${SYSTEM_ROOT} == / ]] || {
            notice 'a non-/ system root requires --fixture-mode'
            return 1
        }
        [[ ${EUID} -eq 0 ]] || {
            notice 'live boot evidence capture must run as root'
            return 1
        }
    fi
}

marker_field_name() {
    local key=${1,,}
    case ${key} in
        attempt_id|generation_id)
            printf 'attempt_id\n'
            ;;
        expected_bootnum|bootnum|boot_number)
            printf 'expected_bootnum\n'
            ;;
        expected_kernel_release|kernel_release|kernel_version)
            printf 'expected_kernel_release\n'
            ;;
        expected_root_source|root_source)
            printf 'expected_root_source\n'
            ;;
        expected_kernel_path|kernel_path|kernel|kernel_target|vmlinuz_target)
            printf 'expected_kernel_path\n'
            ;;
        expected_kernel_sha256|kernel_sha256|vmlinuz_sha256)
            printf 'expected_kernel_sha256\n'
            ;;
        expected_initramfs_path|initramfs_path|initramfs|initramfs_target)
            printf 'expected_initramfs_path\n'
            ;;
        expected_initramfs_sha256|initramfs_sha256)
            printf 'expected_initramfs_sha256\n'
            ;;
        status|completion_status)
            printf 'status\n'
            ;;
        *)
            return 1
            ;;
    esac
}

parse_marker() {
    local line key value canonical old
    while IFS= read -r line || [[ -n ${line} ]]; do
        line=${line%$'\r'}
        [[ -n ${line} && ${line} != \#* && ${line} == *=* ]] || continue
        key=${line%%=*}
        value=${line#*=}
        canonical=$(marker_field_name "${key}") || continue
        if [[ -v MARKER_FIELDS[${canonical}] ]]; then
            old=${MARKER_FIELDS[${canonical}]}
            if [[ ${old} != "${value}" ]]; then
                add_failure validation "duplicate_${canonical}" 'conflicting marker values'
            fi
            continue
        fi
        MARKER_FIELDS[${canonical}]=${value}
    done <"${MARKER}"
}

validate_marker_file() {
    local expected_uid mode_decimal
    [[ -f ${MARKER} && ! -L ${MARKER} ]] || {
        notice "pending marker is not a regular non-symlink file: ${MARKER}"
        return 1
    }
    MARKER_UID=$(stat -c '%u' -- "${MARKER}") || return 1
    MARKER_MODE=$(stat -c '%a' -- "${MARKER}") || return 1
    if ((FIXTURE_MODE)); then
        expected_uid=${EUID}
    else
        expected_uid=0
    fi
    [[ ${MARKER_UID} == "${expected_uid}" ]] || {
        notice "refusing marker owned by uid ${MARKER_UID}; expected ${expected_uid}: ${MARKER}"
        return 1
    }
    [[ ${MARKER_MODE} =~ ^[0-7]{3,4}$ ]] || return 1
    mode_decimal=$((8#${MARKER_MODE}))
    (( (mode_decimal & 8#022) == 0 )) || {
        notice "refusing group/world-writable marker mode ${MARKER_MODE}: ${MARKER}"
        return 1
    }
}

prepare_output() {
    local expected_uid owner
    [[ -d ${STATE_ROOT} && ! -L ${STATE_ROOT} ]] || {
        notice "state root is not a directory or is a symlink: ${STATE_ROOT}"
        return 1
    }
    mkdir -p -- "${EVIDENCE_DIR}" || return 1
    [[ -d ${EVIDENCE_DIR} && ! -L ${EVIDENCE_DIR} ]] || {
        notice "evidence directory is not a directory or is a symlink: ${EVIDENCE_DIR}"
        return 1
    }
    owner=$(stat -c '%u' -- "${EVIDENCE_DIR}") || return 1
    if ((FIXTURE_MODE)); then expected_uid=${EUID}; else expected_uid=0; fi
    [[ ${owner} == "${expected_uid}" ]] || {
        notice "evidence directory has unexpected uid ${owner}: ${EVIDENCE_DIR}"
        return 1
    }
    chmod 0700 -- "${EVIDENCE_DIR}" || return 1
    EVIDENCE_TEMP=$(mktemp "${EVIDENCE_DIR}/.boot-evidence.XXXXXX") || return 1
    chmod 0600 -- "${EVIDENCE_TEMP}" || return 1
    WORK_DIR=$(mktemp -d "${EVIDENCE_DIR}/.boot-evidence-work.XXXXXX") || return 1
    chmod 0700 -- "${WORK_DIR}" || return 1
}

# Invoked indirectly by the EXIT trap below.
# shellcheck disable=SC2329
cleanup() {
    local status=$?
    if [[ -n ${EVIDENCE_TEMP} && -e ${EVIDENCE_TEMP} ]]; then
        rm -f -- "${EVIDENCE_TEMP}"
    fi
    if [[ -n ${WORK_DIR} && -d ${WORK_DIR} ]]; then
        rm -rf -- "${WORK_DIR}"
    fi
    return "${status}"
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM

append_key() {
    local key=$1 value=${2-}
    value=${value//$'\n'/\\n}
    value=${value//$'\r'/\\r}
    printf '%s=%s\n' "${key}" "${value}" >>"${EVIDENCE_TEMP}"
}

add_failure() {
    local kind=$1 code=$2 detail=$3
    detail=${detail//$'\n'/ }
    FAILURES+=("${kind}:${code}:${detail}")
    case ${kind} in
        probe) ((PROBE_FAILURES += 1)) ;;
        validation) ((VALIDATION_FAILURES += 1)) ;;
    esac
}

tool_path() {
    local name=$1 candidate
    if ((FIXTURE_MODE)); then
        candidate=${TOOL_ROOT}/${name}
        [[ -f ${candidate} && -x ${candidate} && ! -L ${candidate} ]] || return 1
        printf '%s\n' "${candidate}"
        return 0
    fi
    if [[ -n ${TOOL_ROOT} ]]; then
        candidate=${TOOL_ROOT}/${name}
        [[ -x ${candidate} ]] || return 1
        printf '%s\n' "${candidate}"
        return 0
    fi
    command -v "${name}" 2>/dev/null
}

record_file() {
    local section=$1 path=$2 required=$3
    {
        printf '\n===== %s =====\n' "${section}"
        printf 'path=%s\n' "${path}"
        if [[ -r ${path} ]]; then
            printf 'read_status=0\n--- raw output ---\n'
            command cat -- "${path}"
            [[ ! -s ${path} ]] || [[ $(tail -c 1 -- "${path}" 2>/dev/null) == '' ]] || printf '\n'
            printf '%s\n' '--- end raw output ---'
        else
            printf 'read_status=1\nerror=not readable\n'
        fi
    } >>"${EVIDENCE_TEMP}"
    if [[ ! -r ${path} && ${required} == required ]]; then
        add_failure probe "${section}_unreadable" "${path}"
        return 1
    fi
}

record_command() {
    local section=$1 required=$2
    shift 2
    local output=${WORK_DIR}/${section//[^A-Za-z0-9_.-]/_}.out
    local rc=0 arg
    timeout --signal=TERM --kill-after=5 "${PROBE_TIMEOUT}" "$@" >"${output}" 2>&1 || rc=$?
    {
        printf '\n===== %s =====\n' "${section}"
        printf 'timeout_seconds=%s\ncommand=' "${PROBE_TIMEOUT}"
        for arg in "$@"; do printf '%q ' "${arg}"; done
        printf '\nexit_status=%s\n--- raw output ---\n' "${rc}"
        command cat -- "${output}"
        [[ ! -s ${output} ]] || [[ $(tail -c 1 -- "${output}" 2>/dev/null) == '' ]] || printf '\n'
        printf '%s\n' '--- end raw output ---'
    } >>"${EVIDENCE_TEMP}"
    if ((rc != 0)) && [[ ${required} == required ]]; then
        add_failure probe "${section}_failed" "exit status ${rc}"
    fi
    return "${rc}"
}

missing_tool() {
    local section=$1 tool=$2 required=$3
    {
        printf '\n===== %s =====\n' "${section}"
        printf 'tool=%s\nexit_status=127\nerror=tool unavailable\n' "${tool}"
    } >>"${EVIDENCE_TEMP}"
    if [[ ${required} == required ]]; then
        add_failure probe "${section}_tool_missing" "${tool}"
    fi
}

marker_value() {
    printf '%s' "${MARKER_FIELDS[$1]-}"
}

require_expected_field() {
    local name=$1 value
    value=$(marker_value "${name}")
    if [[ -z ${value} ]]; then
        add_failure validation "missing_${name}" 'required marker field is empty or absent'
        return 1
    fi
    return 0
}

resolve_asset_path() {
    local value=$1
    if [[ ${SYSTEM_ROOT} != / && ${value} == /* && ! ${value} == "${SYSTEM_ROOT}"/* ]]; then
        printf '%s%s\n' "${SYSTEM_ROOT}" "${value}"
    else
        printf '%s\n' "${value}"
    fi
}

validate_hash() {
    local kind=$1 expected_path=$2 expected_hash=$3 resolved actual
    if [[ -z ${expected_path} || -z ${expected_hash} ]]; then
        return 1
    fi
    resolved=$(resolve_asset_path "${expected_path}")
    append_key "${kind}_expected_path" "${expected_path}"
    append_key "${kind}_resolved_path" "${resolved}"
    append_key "${kind}_expected_sha256" "${expected_hash,,}"
    if [[ ! ${expected_hash} =~ ^[[:xdigit:]]{64}$ ]]; then
        append_key "${kind}_hash_status" invalid-expected-digest
        add_failure validation "${kind}_expected_sha256_invalid" "${expected_hash}"
        return 1
    fi
    if [[ ! -f ${resolved} || -L ${resolved} ]]; then
        append_key "${kind}_hash_status" missing-or-nonregular
        add_failure validation "${kind}_asset_invalid" "${resolved}"
        return 1
    fi
    actual=$(sha256sum -- "${resolved}" 2>/dev/null) || {
        append_key "${kind}_hash_status" read-failed
        add_failure validation "${kind}_sha256_failed" "${resolved}"
        return 1
    }
    actual=${actual%% *}
    append_key "${kind}_actual_sha256" "${actual}"
    if [[ ${actual,,} == "${expected_hash,,}" ]]; then
        append_key "${kind}_hash_status" match
        return 0
    fi
    append_key "${kind}_hash_status" mismatch
    add_failure validation "${kind}_sha256_mismatch" "expected ${expected_hash,,}, got ${actual,,}"
    return 1
}

normalize_bootnum() {
    local value=${1#0x}
    value=${value#0X}
    [[ ${value} =~ ^[[:xdigit:]]{1,4}$ ]] || return 1
    printf '%04X\n' "$((16#${value}))"
}

capture_header_and_marker() {
    local key
    RECORDED_AT=$(timestamp)
    append_key schema_version "${SCHEMA_VERSION}"
    append_key recorded_at_utc "${RECORDED_AT}"
    append_key program "${PROGRAM}"
    append_key fixture_mode "${FIXTURE_MODE}"
    append_key system_root "${SYSTEM_ROOT}"
    append_key state_root "${STATE_ROOT}"
    append_key esp_root "${ESP_ROOT}"
    append_key marker_path "${MARKER}"
    append_key marker_uid "${MARKER_UID}"
    append_key marker_mode "${MARKER_MODE}"
    for key in attempt_id expected_bootnum expected_kernel_release expected_root_source \
        expected_kernel_path expected_kernel_sha256 expected_initramfs_path \
        expected_initramfs_sha256; do
        append_key "marker_${key}" "$(marker_value "${key}")"
    done
    record_file marker_raw "${MARKER}" required
}

capture_boot_identity() {
    local boot_id_file uname_tool release_output expected
    boot_id_file=$(root_path /proc/sys/kernel/random/boot_id)
    record_file boot_id "${boot_id_file}" required
    if [[ -r ${boot_id_file} ]]; then
        IFS= read -r BOOT_ID <"${boot_id_file}" || true
        BOOT_ID=${BOOT_ID%$'\r'}
    fi
    [[ ${BOOT_ID} =~ ^[[:xdigit:]-]{16,64}$ ]] || add_failure validation boot_id_invalid "${BOOT_ID:-empty}"

    if uname_tool=$(tool_path uname); then
        record_command uname required "${uname_tool}" -a || true
        release_output=${WORK_DIR}/uname-release.out
        timeout --signal=TERM --kill-after=5 "${PROBE_TIMEOUT}" \
            "${uname_tool}" -r >"${release_output}" 2>&1 || {
            add_failure probe uname_release_failed 'uname -r failed'
            : >"${release_output}"
        }
        IFS= read -r UNAME_RELEASE <"${release_output}" || true
        UNAME_RELEASE=${UNAME_RELEASE%$'\r'}
    else
        missing_tool uname uname required
    fi
    append_key uname_release "${UNAME_RELEASE}"
    expected=$(marker_value expected_kernel_release)
    if [[ -n ${expected} && ${UNAME_RELEASE} == "${expected}" ]]; then
        append_key kernel_release_status match
    elif [[ -n ${expected} ]]; then
        append_key kernel_release_status mismatch
        add_failure validation kernel_release_mismatch "expected ${expected}, got ${UNAME_RELEASE:-empty}"
    fi
    record_file proc_cmdline "$(root_path /proc/cmdline)" required
}

capture_efi() {
    local efi_tool output rc=0 line expected expected_norm current_norm
    output=${WORK_DIR}/efibootmgr.raw
    if efi_tool=$(tool_path efibootmgr); then
        timeout --signal=TERM --kill-after=5 "${PROBE_TIMEOUT}" \
            "${efi_tool}" >"${output}" 2>&1 || rc=$?
        {
            printf '\n===== efibootmgr =====\n'
            printf 'command=%q\nexit_status=%s\n--- raw output ---\n' "${efi_tool}" "${rc}"
            command cat -- "${output}"
            [[ ! -s ${output} ]] || [[ $(tail -c 1 -- "${output}" 2>/dev/null) == '' ]] || printf '\n'
            printf '%s\n' '--- end raw output ---'
        } >>"${EVIDENCE_TEMP}"
        ((rc == 0)) || add_failure probe efibootmgr_failed "exit status ${rc}"
        while IFS= read -r line; do
            if [[ ${line} == 'BootCurrent:'* ]]; then
                BOOT_CURRENT=${line#BootCurrent:}
                BOOT_CURRENT=${BOOT_CURRENT//[[:space:]]/}
                break
            fi
        done <"${output}"
    else
        missing_tool efibootmgr efibootmgr required
    fi
    append_key boot_current "${BOOT_CURRENT}"
    expected=$(marker_value expected_bootnum)
    if ! current_norm=$(normalize_bootnum "${BOOT_CURRENT}" 2>/dev/null); then
        append_key boot_current_status invalid-or-missing
        add_failure validation boot_current_invalid "${BOOT_CURRENT:-empty}"
        return
    fi
    append_key boot_current_normalized "${current_norm}"
    if ! expected_norm=$(normalize_bootnum "${expected}" 2>/dev/null); then
        append_key expected_bootnum_status invalid-or-missing
        add_failure validation expected_bootnum_invalid "${expected:-empty}"
        return
    fi
    append_key expected_bootnum_normalized "${expected_norm}"
    if [[ ${current_norm} == "${expected_norm}" ]]; then
        append_key boot_current_status match
    else
        append_key boot_current_status mismatch
        add_failure validation boot_current_mismatch "expected ${expected_norm}, got ${current_norm}"
    fi
}

capture_mounts() {
    local findmnt_tool output expected
    record_file proc_self_mountinfo "$(root_path /proc/self/mountinfo)" required
    record_file proc_mounts "$(root_path /proc/mounts)" required
    if ! findmnt_tool=$(tool_path findmnt); then
        missing_tool findmnt_root findmnt required
        missing_tool findmnt_efi findmnt required
        add_failure validation root_source_unavailable 'findmnt is unavailable'
        return
    fi
    record_command findmnt_root required "${findmnt_tool}" --output SOURCE,TARGET,FSTYPE,OPTIONS --target "${SYSTEM_ROOT}" || true
    record_command findmnt_efi required "${findmnt_tool}" --output SOURCE,TARGET,FSTYPE,OPTIONS --target "${ESP_ROOT}" || true
    output=${WORK_DIR}/root-source.out
    if timeout --signal=TERM --kill-after=5 "${PROBE_TIMEOUT}" \
        "${findmnt_tool}" --noheadings --output SOURCE --target "${SYSTEM_ROOT}" >"${output}" 2>&1; then
        IFS= read -r ROOT_SOURCE <"${output}" || true
        ROOT_SOURCE=${ROOT_SOURCE%$'\r'}
        ROOT_SOURCE=${ROOT_SOURCE#"${ROOT_SOURCE%%[![:space:]]*}"}
        ROOT_SOURCE=${ROOT_SOURCE%"${ROOT_SOURCE##*[![:space:]]}"}
    else
        add_failure probe findmnt_root_source_failed 'could not determine root source'
    fi
    append_key root_source "${ROOT_SOURCE}"
    expected=$(marker_value expected_root_source)
    if [[ -z ${expected} ]]; then
        append_key root_source_status not-requested
    elif [[ ${ROOT_SOURCE} == "${expected}" ]]; then
        append_key root_source_status match
    else
        append_key root_source_status mismatch
        add_failure validation root_source_mismatch "expected ${expected}, got ${ROOT_SOURCE:-empty}"
    fi
}

capture_openrc() {
    local rc_status crashed_output
    if ! rc_status=$(tool_path rc-status); then
        missing_tool rc_status_all rc-status required
        missing_tool rc_status_crashed rc-status required
        return
    fi
    record_command rc_status_all required "${rc_status}" --all || true
    record_command rc_status_crashed required "${rc_status}" --crashed || true
    crashed_output=${WORK_DIR}/rc_status_crashed.out
    # OpenRC's --crashed mode emits one plain service name per line and emits
    # no output when the set is empty.  Do not look for the decorated
    # "[ crashed ]" form used by other rc-status modes; that would turn real
    # live crashes into a false pass.
    if [[ -s ${crashed_output} ]] && grep -Eq '[^[:space:]]' "${crashed_output}"; then
        add_failure validation openrc_crashed_services 'rc-status --crashed reported at least one crashed service'
    fi
}

capture_smokes() {
    local tool source output
    if tool=$(tool_path emerge); then
        record_command portage_emerge_info required env ROOT="${SYSTEM_ROOT}" PORTAGE_CONFIGROOT="${SYSTEM_ROOT}" "${tool}" --info || true
    else
        missing_tool portage_emerge_info emerge required
    fi
    if tool=$(tool_path portageq); then
        record_command portageq_root required "${tool}" envvar ROOT || true
    else
        missing_tool portageq_root portageq required
    fi
    if tool=$(tool_path python3); then
        record_command python_portage_smoke required "${tool}" -c \
            'import portage, sys; print(sys.version.split()[0]); print(portage.__file__)' || true
    else
        missing_tool python_portage_smoke python3 required
    fi
    if tool=$(tool_path sh); then
        # This literal is deliberately expanded by the shell under test.
        # shellcheck disable=SC2016
        record_command shell_smoke required "${tool}" -c \
            'set -eu; value=boot-evidence; test "$value" = boot-evidence; printf "shell-smoke-ok\\n"' || true
    else
        missing_tool shell_smoke sh required
    fi

    source=${WORK_DIR}/smoke.c
    output=${WORK_DIR}/smoke-c.o
    printf '%s\n' 'int main(void) { return 0; }' >"${source}"
    if tool=$(tool_path cc); then
        record_command c_compiler_version required "${tool}" --version || true
        record_command c_compiler_smoke required "${tool}" -x c -c "${source}" -o "${output}" || true
        [[ -s ${output} ]] || add_failure validation c_compiler_output_missing "${output}"
    else
        missing_tool c_compiler_version cc required
        missing_tool c_compiler_smoke cc required
    fi

    source=${WORK_DIR}/smoke.cc
    output=${WORK_DIR}/smoke-cxx.o
    printf '%s\n' 'int main() { return 0; }' >"${source}"
    if tool=$(tool_path c++); then
        record_command cxx_compiler_version required "${tool}" --version || true
        record_command cxx_compiler_smoke required "${tool}" -x c++ -c "${source}" -o "${output}" || true
        [[ -s ${output} ]] || add_failure validation cxx_compiler_output_missing "${output}"
    else
        missing_tool cxx_compiler_version c++ required
        missing_tool cxx_compiler_smoke c++ required
    fi
}

capture_network() {
    local ip_tool
    if ip_tool=$(tool_path ip); then
        record_command network_link required "${ip_tool}" -details link show || true
        record_command network_address required "${ip_tool}" address show || true
        record_command network_route_v4 required "${ip_tool}" -4 route show table all || true
        record_command network_route_v6 required "${ip_tool}" -6 route show table all || true
    else
        missing_tool network_link ip required
        missing_tool network_address ip required
        missing_tool network_route_v4 ip required
        missing_tool network_route_v6 ip required
    fi
    record_file resolv_conf "$(root_path /etc/resolv.conf)" optional
}

validate_expectations() {
    local name
    for name in expected_bootnum expected_kernel_release expected_kernel_path \
        expected_kernel_sha256 expected_initramfs_path expected_initramfs_sha256; do
        require_expected_field "${name}" || true
    done
    validate_hash kernel "$(marker_value expected_kernel_path)" "$(marker_value expected_kernel_sha256)" || true
    validate_hash initramfs "$(marker_value expected_initramfs_path)" "$(marker_value expected_initramfs_sha256)" || true
}

append_summary() {
    local index
    if ((PROBE_FAILURES == 0 && VALIDATION_FAILURES == 0)); then
        RESULT_STATUS=pass
    else
        RESULT_STATUS=failed
    fi
    {
        printf '\n===== summary =====\n'
        printf 'result_status=%s\n' "${RESULT_STATUS}"
        printf 'probe_failure_count=%s\n' "${PROBE_FAILURES}"
        printf 'validation_failure_count=%s\n' "${VALIDATION_FAILURES}"
        printf 'failure_count=%s\n' "$((PROBE_FAILURES + VALIDATION_FAILURES))"
        printf 'failure_records=%s\n' "${#FAILURES[@]}"
        for index in "${!FAILURES[@]}"; do
            printf 'failure_%04d=%s\n' "$((index + 1))" "${FAILURES[index]}"
        done
    } >>"${EVIDENCE_TEMP}"
}

safe_component() {
    local value=$1
    value=${value//[^A-Za-z0-9._-]/_}
    value=${value#.}
    [[ -n ${value} ]] || value=unnamed
    printf '%.96s\n' "${value}"
}

publish_evidence() {
    local attempt compact safe_attempt safe_boot sequence=0 candidate
    attempt=$(marker_value attempt_id)
    [[ -n ${attempt} ]] || attempt='boot-validation'
    compact=${RECORDED_AT//[-:TZ]/}
    safe_attempt=$(safe_component "${attempt}")
    safe_boot=$(safe_component "${BOOT_ID:-no-boot-id}")
    candidate=${EVIDENCE_DIR}/${compact}-${safe_attempt}-${safe_boot}.log
    while [[ -e ${candidate} ]]; do
        ((sequence += 1))
        candidate=${EVIDENCE_DIR}/${compact}-${safe_attempt}-${safe_boot}.${sequence}.log
    done
    mv -T -- "${EVIDENCE_TEMP}" "${candidate}" || return 1
    EVIDENCE_TEMP=
    EVIDENCE_FINAL=${candidate}
}

mark_completed() {
    local marker_dir temp line key canonical
    marker_dir=${MARKER%/*}
    temp=$(mktemp "${marker_dir}/.${MARKER##*/}.completed.XXXXXX") || return 1
    while IFS= read -r line || [[ -n ${line} ]]; do
        if [[ ${line} == *=* ]]; then
            key=${line%%=*}
            canonical=$(marker_field_name "${key}" 2>/dev/null) || canonical=
            case ${canonical} in
                status) continue ;;
            esac
            case ${key,,} in
                completed_at|completed_boot_id|evidence_path|result_status|failure_count)
                    continue
                    ;;
            esac
        fi
        printf '%s\n' "${line}"
    done <"${MARKER}" >"${temp}"
    {
        printf 'status=completed\n'
        printf 'completed_at=%s\n' "$(timestamp)"
        printf 'completed_boot_id=%s\n' "${BOOT_ID}"
        printf 'evidence_path=%s\n' "${EVIDENCE_FINAL}"
        printf 'result_status=%s\n' "${RESULT_STATUS}"
        printf 'failure_count=%s\n' "$((PROBE_FAILURES + VALIDATION_FAILURES))"
    } >>"${temp}"
    chmod "${MARKER_MODE}" -- "${temp}" || { rm -f -- "${temp}"; return 1; }
    chown --reference="${MARKER}" -- "${temp}" || { rm -f -- "${temp}"; return 1; }
    mv -T -- "${temp}" "${MARKER}" || { rm -f -- "${temp}"; return 1; }
}

write_completion_failure() {
    local temp final
    temp=$(mktemp "${EVIDENCE_DIR}/.marker-completion-failure.XXXXXX") || return 0
    {
        printf 'recorded_at_utc=%s\n' "$(timestamp)"
        printf 'marker_path=%s\n' "${MARKER}"
        printf 'evidence_path=%s\n' "${EVIDENCE_FINAL}"
        printf 'error=atomic marker completion failed\n'
    } >"${temp}"
    chmod 0600 -- "${temp}" || true
    final=${EVIDENCE_FINAL%.log}.marker-completion-failure.log
    mv -T -- "${temp}" "${final}" || rm -f -- "${temp}"
}

run_capture() {
    capture_header_and_marker
    validate_expectations
    capture_boot_identity
    capture_efi
    capture_mounts
    capture_openrc
    capture_smokes
    capture_network
    append_summary
    if ! publish_evidence; then
        notice 'could not atomically publish boot evidence'
        return 1
    fi
    if ! mark_completed; then
        notice "evidence was published but marker completion failed: ${EVIDENCE_FINAL}"
        write_completion_failure
        return 1
    fi
    notice "boot evidence ${RESULT_STATUS}: ${EVIDENCE_FINAL}"
}

main() {
    local parse_status=0 status
    parse_options "$@" || parse_status=$?
    if ((parse_status == 10)); then
        return 0
    elif ((parse_status != 0)); then
        usage >&2
        return 0
    fi
    configure_paths || return 0
    [[ -e ${MARKER} ]] || return 0
    validate_marker_file || return 0

    # Lock the marker inode.  Concurrent invocations see either this pending
    # inode or the atomically replaced completed marker and cannot duplicate it.
    exec {LOCK_FD}<"${MARKER}" || {
        notice "cannot open marker lock: ${MARKER}"
        return 0
    }
    if ! flock -n "${LOCK_FD}"; then
        notice "another boot evidence capture owns the marker: ${MARKER}"
        return 0
    fi
    parse_marker
    status=$(marker_value status)
    if [[ ${status,,} == completed ]]; then
        return 0
    fi
    prepare_output || {
        notice 'could not prepare the atomic evidence staging area'
        return 0
    }
    run_capture || true
    return 0
}

main "$@"
exit 0

#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
TEMPLATE=${ROOT}/optimization/fixtures/portage/phase2-sample-pgo-fixture-1.ebuild.in
AUTHORIZATION_TOKEN_SCANNER=${ROOT}/scripts/optimization/pgo/authorization-token-scan.py
INSTALLER=/var/lib/gentoo-optimization/bootstrap/install-framework.sh
PROFILE_IDENTITY=/usr/local/libexec/gentoo-optimization/pgo/profile-identity.py
VALIDATOR=/usr/local/libexec/gentoo-optimization/pgo/validate-profile.py
LLVM_ROOT=/usr/lib/llvm/22/bin
CLANG_LINK=${LLVM_ROOT}/clang
CLANGXX_LINK=${LLVM_ROOT}/clang++
PROFGEN_LINK=${LLVM_ROOT}/llvm-profgen
PROFDATA_LINK=${LLVM_ROOT}/llvm-profdata
READELF_LINK=${LLVM_ROOT}/llvm-readelf
OBJCOPY_LINK=${LLVM_ROOT}/llvm-objcopy
PERF=/usr/bin/perf
PORTAGE_SAMPLE_ITERATIONS_WAS_SET=${PORTAGE_SAMPLE_PGO_ITERATIONS+x}
KEEP_TEMP_WAS_SET=${KEEP_TEMP+x}
ITERATIONS=${PORTAGE_SAMPLE_PGO_ITERATIONS:-100000000}
KEEP_TEMP=${KEEP_TEMP:-0}
OUTPUT_DIR=
CANONICAL_OUTPUT_DIR=
EXPLICIT_OUTPUT_DIR=0
TRUSTED_OUTPUT_BASE=/var/tmp/gentoo-optimization
PRODUCTION_OUTPUT_BASE=/var/lib/gentoo-optimization/reports
PRODUCTION_LOCKS=0
PORTAGE_POLICY_MODE=
LIVE_POLICY_PREFLIGHT_ONLY=0
LIVE_PORTAGE_FEATURES=
LIVE_MAKE_CONF=
LIVE_MAKE_CONF_SHA256=
LIVE_POLICY_IDENTITY_SHA256=
LIVE_PORTAGE_TMPDIR_EFFECTIVE=
LIVE_PORTAGE_TMPDIR_PORTAGE=
LIVE_PORTAGE_TMPDIR_PORTAGE_EFFECTIVE=
LIVE_PORTAGE_DEPCACHEDIR=
LIVE_PORTAGE_LOGDIR=
LIVE_DISTDIR=
LIVE_PKGDIR=
LIVE_CCACHE_DIR=
LIVE_CCACHE_TEMPDIR=
LIVE_CCACHE_TEMPDIR_SOURCE=
LIVE_SCCACHE_DIR=
LIVE_SCCACHE_DIR_SOURCE=
WRITABLE_ROOT_RESOLVER_TEST=
WRITABLE_ROOT_RECEIPT_TEST=
WRITABLE_ROOT_RECEIPT_EXPECTED=
declare -a PORTAGE_WRITABLE_ROOT_NAMES=(
    PORTAGE_TMPDIR PORTAGE_LOGDIR PORTAGE_DEPCACHEDIR DISTDIR PKGDIR
    CCACHE_DIR CCACHE_TEMPDIR SCCACHE_DIR
)
declare -A PORTAGE_WRITABLE_RAW=()
declare -A PORTAGE_WRITABLE_EFFECTIVE=()
declare -A PORTAGE_WRITABLE_CANONICAL=()
declare -A PORTAGE_WRITABLE_SOURCE=()
declare -A PORTAGE_WRITABLE_QUERY_STATUS=()
PRODUCTION_ROOTS_CREATED=0
PRODUCTION_GATE_COMPLETE=0
PRODUCTION_STATUS_FINALIZED=0
PRODUCTION_CAPTURED=0
PRODUCTION_TRANSACTION_TOKEN=${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN-}
PRODUCTION_TRANSACTION_AUTHORIZATION=${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION-}
PRODUCTION_GATE_RUN_ID=${GENTOO_OPT_PRODUCTION_GATE_RUN_ID-}
PRODUCTION_GATE_GENERATION_ID=${GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID-}
PRODUCTION_GATE_INVENTORY_ID=${GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID-}
PRODUCTION_GATE_INVENTORY_SHA256=${GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256-}
PRODUCTION_GATE_WORK_ROOT=${GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT-}
export -n PRODUCTION_TRANSACTION_TOKEN PRODUCTION_TRANSACTION_AUTHORIZATION \
    PRODUCTION_GATE_RUN_ID PRODUCTION_GATE_GENERATION_ID \
    PRODUCTION_GATE_INVENTORY_ID PRODUCTION_GATE_INVENTORY_SHA256 \
    PRODUCTION_GATE_WORK_ROOT
# Nothing launched while parsing or validating the gate may inherit the raw
# bearer or its coordinator-supplied identities.  Retain shell-only copies and
# rebuild the exact environment only for the two reviewed authorization entry
# points below.
unset GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN \
    GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION \
    GENTOO_OPT_PRODUCTION_GATE_RUN_ID \
    GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID \
    GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID \
    GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256 \
    GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT

usage() {
    cat <<'EOF'
Usage: test-portage-sample-pgo-integration.sh [--output-dir ABSOLUTE_PATH]
       [--keep-temp] --portage-policy isolated-diagnostic|live
       [--production-locks]
       test-portage-sample-pgo-integration.sh --live-policy-preflight
       test-portage-sample-pgo-integration.sh --writable-root-resolver-test PATH
       test-portage-sample-pgo-integration.sh --writable-root-receipt-test RECEIPT EXPECTED

The optional output directory must be a new path below the trusted
/var/tmp/gentoo-optimization tree. Explicit output directories are always preserved.
--production-locks is a root-only Phase 2 gate driven by the reviewed production
lock coordinator. It uses canonical live lock paths and root-owned namespaces;
it never passes a test-mode or substituted-lock argument to an installed helper.
The isolated-diagnostic policy retains the historical sandbox-disabled lane for
fault localization only. The live policy copies the host's resolved FEATURES
as its baseline, permits only the dispatcher's recorded compiler-wrapper
exception in optimization stages, and proves sandbox, usersandbox/userpriv,
PID, network, IPC, and mount isolation. Production mode requires live policy.
--live-policy-preflight is a read-only, non-root TSV probe used to prove that
an inherited FEATURES variable cannot override the authoritative live policy.
--writable-root-resolver-test runs only the hermetic resolver contract against
the supplied fixture-owned portageq executable and performs no Portage build.
--writable-root-receipt-test verifies a synthetic phase receipt against an
expected writable-root TSV and performs no Portage build.
EOF
}

while (($#)); do
    case $1 in
        --output-dir)
            (($# >= 2)) || { usage >&2; exit 2; }
            OUTPUT_DIR=$2
            shift 2
            ;;
        --keep-temp)
            KEEP_TEMP=1
            shift
            ;;
        --production-locks)
            PRODUCTION_LOCKS=1
            shift
            ;;
        --portage-policy)
            (($# >= 2)) || { usage >&2; exit 2; }
            PORTAGE_POLICY_MODE=$2
            shift 2
            ;;
        --live-policy-preflight)
            LIVE_POLICY_PREFLIGHT_ONLY=1
            shift
            ;;
        --writable-root-resolver-test)
            (($# >= 2)) || { usage >&2; exit 2; }
            WRITABLE_ROOT_RESOLVER_TEST=$2
            shift 2
            ;;
        --writable-root-receipt-test)
            (($# >= 3)) || { usage >&2; exit 2; }
            WRITABLE_ROOT_RECEIPT_TEST=$2
            WRITABLE_ROOT_RECEIPT_EXPECTED=$3
            shift 3
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
done

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

checked_portageq_envvar() {
    local portageq_tool=$1 variable=$2 destination=$3 status_destination=$4
    local output status=0
    output=$(/usr/bin/env -i \
        HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
        PORTAGE_CONFIGROOT=/ "${portageq_tool}" envvar "${variable}") || \
        status=$?
    if ((status > 1)); then
        printf 'FAIL: portageq envvar %s failed with status %s\n' \
            "${variable}" "${status}" >&2
        return 1
    fi
    [[ ${output} != *$'\n'* ]] || {
        printf 'FAIL: portageq envvar %s returned multiple lines\n' \
            "${variable}" >&2
        return 1
    }
    if ((status == 0)) && [[ -z ${output} ]]; then
        printf 'FAIL: portageq envvar %s returned empty with success status\n' \
            "${variable}" >&2
        return 1
    fi
    if ((status == 1)) && [[ -n ${output} ]]; then
        printf 'FAIL: portageq envvar %s returned data with unset status\n' \
            "${variable}" >&2
        return 1
    fi
    printf -v "${destination}" '%s' "${output}"
    printf -v "${status_destination}" '%s' "${status}"
}

resolve_portage_writable_roots() {
    local portageq_tool=$1 forbidden_prefix=${2-} name value canonical
    local protected
    local xdg_cache_home='' cache_home='' query_status=''
    local -A canonical_owners=()
    local -a forbidden_live_aliases=(
        /etc/portage /var/db/pkg /var/lib/gentoo-optimization "${ROOT}"
    )

    PORTAGE_WRITABLE_RAW=()
    PORTAGE_WRITABLE_EFFECTIVE=()
    PORTAGE_WRITABLE_CANONICAL=()
    PORTAGE_WRITABLE_SOURCE=()
    PORTAGE_WRITABLE_QUERY_STATUS=()
    for name in "${PORTAGE_WRITABLE_ROOT_NAMES[@]}"; do
        value=
        query_status=
        checked_portageq_envvar "${portageq_tool}" "${name}" value \
            query_status || return 1
        PORTAGE_WRITABLE_RAW[${name}]=${value}
        PORTAGE_WRITABLE_QUERY_STATUS[${name}]=${query_status}
    done
    checked_portageq_envvar "${portageq_tool}" XDG_CACHE_HOME \
        xdg_cache_home query_status || return 1
    checked_portageq_envvar "${portageq_tool}" HOME cache_home \
        query_status || return 1

    if [[ -n ${PORTAGE_WRITABLE_RAW[PORTAGE_TMPDIR]} ]]; then
        PORTAGE_WRITABLE_EFFECTIVE[PORTAGE_TMPDIR]=${PORTAGE_WRITABLE_RAW[PORTAGE_TMPDIR]}
        PORTAGE_WRITABLE_SOURCE[PORTAGE_TMPDIR]=configured
    else
        PORTAGE_WRITABLE_EFFECTIVE[PORTAGE_TMPDIR]=/var/tmp
        PORTAGE_WRITABLE_SOURCE[PORTAGE_TMPDIR]=portage-default
    fi
    if [[ -n ${PORTAGE_WRITABLE_RAW[PORTAGE_LOGDIR]} ]]; then
        PORTAGE_WRITABLE_EFFECTIVE[PORTAGE_LOGDIR]=${PORTAGE_WRITABLE_RAW[PORTAGE_LOGDIR]}
        PORTAGE_WRITABLE_SOURCE[PORTAGE_LOGDIR]=configured
    else
        # PORTAGE_LOGDIR is optional.  With PORTAGE_ELOG_SYSTEM=save, Portage's
        # live fallback is /var/log/portage; build-temporary logs are covered
        # separately by the derived PORTAGE_TMPDIR/portage root below.
        PORTAGE_WRITABLE_EFFECTIVE[PORTAGE_LOGDIR]=/var/log/portage
        PORTAGE_WRITABLE_SOURCE[PORTAGE_LOGDIR]=portage-elog-default
    fi
    if [[ -n ${PORTAGE_WRITABLE_RAW[PORTAGE_DEPCACHEDIR]} ]]; then
        PORTAGE_WRITABLE_EFFECTIVE[PORTAGE_DEPCACHEDIR]=${PORTAGE_WRITABLE_RAW[PORTAGE_DEPCACHEDIR]}
        PORTAGE_WRITABLE_SOURCE[PORTAGE_DEPCACHEDIR]=configured
    else
        PORTAGE_WRITABLE_EFFECTIVE[PORTAGE_DEPCACHEDIR]=/var/cache/edb/dep
        PORTAGE_WRITABLE_SOURCE[PORTAGE_DEPCACHEDIR]=portage-default
    fi
    if [[ -n ${PORTAGE_WRITABLE_RAW[DISTDIR]} ]]; then
        PORTAGE_WRITABLE_EFFECTIVE[DISTDIR]=${PORTAGE_WRITABLE_RAW[DISTDIR]}
        PORTAGE_WRITABLE_SOURCE[DISTDIR]=configured
    else
        PORTAGE_WRITABLE_EFFECTIVE[DISTDIR]=/var/cache/distfiles
        PORTAGE_WRITABLE_SOURCE[DISTDIR]=portage-default
    fi
    if [[ -n ${PORTAGE_WRITABLE_RAW[PKGDIR]} ]]; then
        PORTAGE_WRITABLE_EFFECTIVE[PKGDIR]=${PORTAGE_WRITABLE_RAW[PKGDIR]}
        PORTAGE_WRITABLE_SOURCE[PKGDIR]=configured
    else
        PORTAGE_WRITABLE_EFFECTIVE[PKGDIR]=/var/cache/binpkgs
        PORTAGE_WRITABLE_SOURCE[PKGDIR]=portage-default
    fi
    if [[ -n ${PORTAGE_WRITABLE_RAW[CCACHE_DIR]} ]]; then
        PORTAGE_WRITABLE_EFFECTIVE[CCACHE_DIR]=${PORTAGE_WRITABLE_RAW[CCACHE_DIR]}
        PORTAGE_WRITABLE_SOURCE[CCACHE_DIR]=configured
    else
        PORTAGE_WRITABLE_EFFECTIVE[CCACHE_DIR]=${PORTAGE_WRITABLE_EFFECTIVE[PORTAGE_TMPDIR]%/}/ccache
        PORTAGE_WRITABLE_SOURCE[CCACHE_DIR]=portage-default-below-tmpdir
    fi
    if [[ -n ${PORTAGE_WRITABLE_RAW[CCACHE_TEMPDIR]} ]]; then
        PORTAGE_WRITABLE_EFFECTIVE[CCACHE_TEMPDIR]=${PORTAGE_WRITABLE_RAW[CCACHE_TEMPDIR]}
        PORTAGE_WRITABLE_SOURCE[CCACHE_TEMPDIR]=configured
    else
        PORTAGE_WRITABLE_EFFECTIVE[CCACHE_TEMPDIR]=${PORTAGE_WRITABLE_EFFECTIVE[CCACHE_DIR]%/}/tmp
        PORTAGE_WRITABLE_SOURCE[CCACHE_TEMPDIR]=ccache-default-below-cache-dir
    fi
    if [[ -n ${PORTAGE_WRITABLE_RAW[SCCACHE_DIR]} ]]; then
        PORTAGE_WRITABLE_EFFECTIVE[SCCACHE_DIR]=${PORTAGE_WRITABLE_RAW[SCCACHE_DIR]}
        PORTAGE_WRITABLE_SOURCE[SCCACHE_DIR]=configured
    elif [[ -n ${xdg_cache_home} ]]; then
        PORTAGE_WRITABLE_EFFECTIVE[SCCACHE_DIR]=${xdg_cache_home%/}/sccache
        PORTAGE_WRITABLE_SOURCE[SCCACHE_DIR]=xdg-cache-home-default
    elif [[ -n ${cache_home} ]]; then
        PORTAGE_WRITABLE_EFFECTIVE[SCCACHE_DIR]=${cache_home%/}/.cache/sccache
        PORTAGE_WRITABLE_SOURCE[SCCACHE_DIR]=home-cache-default
    else
        PORTAGE_WRITABLE_EFFECTIVE[SCCACHE_DIR]=/root/.cache/sccache
        PORTAGE_WRITABLE_SOURCE[SCCACHE_DIR]='root-home-default'
    fi

    for name in "${PORTAGE_WRITABLE_ROOT_NAMES[@]}"; do
        value=${PORTAGE_WRITABLE_EFFECTIVE[${name}]}
        [[ ${value} == /* && ${value} != / && ${value} != *$'\n'* ]] || {
            printf 'FAIL: effective %s is not one absolute non-root path: %s\n' \
                "${name}" "${value}" >&2
            return 1
        }
        if ! canonical=$(/usr/bin/realpath -m -- "${value}"); then
            printf 'FAIL: cannot canonicalize effective %s: %s\n' \
                "${name}" "${value}" >&2
            return 1
        fi
        if [[ -L ${value} ]] && ! /usr/bin/realpath -e -- "${value}" >/dev/null; then
            printf 'FAIL: effective %s is a dangling symlink: %s\n' \
                "${name}" "${value}" >&2
            return 1
        fi
        [[ ${canonical} == /* && ${canonical} != / ]] || {
            printf 'FAIL: canonical %s is unsafe: %s\n' \
                "${name}" "${canonical}" >&2
            return 1
        }
        for protected in "${forbidden_live_aliases[@]}"; do
            if [[ ${canonical} == "${protected}" || \
                ${canonical} == "${protected}"/* || \
                ${protected} == "${canonical}"/* ]]; then
                printf 'FAIL: writable root %s unsafely overlaps %s: %s\n' \
                    "${name}" "${protected}" "${canonical}" >&2
                return 1
            fi
        done
        if [[ -n ${forbidden_prefix} && \
            ( ${canonical} == "${forbidden_prefix}" || \
              ${canonical} == "${forbidden_prefix}"/* ) ]]; then
            printf 'FAIL: live %s aliases the disposable work root: %s\n' \
                "${name}" "${canonical}" >&2
            return 1
        fi
        if [[ -n ${canonical_owners[${canonical}]-} ]]; then
            printf 'FAIL: writable roots %s and %s alias canonical path %s\n' \
                "${canonical_owners[${canonical}]}" "${name}" \
                "${canonical}" >&2
            return 1
        fi
        canonical_owners[${canonical}]=${name}
        PORTAGE_WRITABLE_CANONICAL[${name}]=${canonical}
    done
}

emit_portage_writable_roots() {
    local name
    printf 'schema\tgentoo-optimization-portage-writable-roots-v1\n'
    for name in "${PORTAGE_WRITABLE_ROOT_NAMES[@]}"; do
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "${name}" \
            "${PORTAGE_WRITABLE_RAW[${name}]}" \
            "${PORTAGE_WRITABLE_EFFECTIVE[${name}]}" \
            "${PORTAGE_WRITABLE_CANONICAL[${name}]}" \
            "${PORTAGE_WRITABLE_SOURCE[${name}]}" \
            "${PORTAGE_WRITABLE_QUERY_STATUS[${name}]}"
    done
}

verify_writable_root_receipt() {
    local receipt=$1 expected=$2 label=$3
    /usr/bin/python3 -I - "${receipt}" "${expected}" "${label}" <<'PY'
import pathlib
import sys

receipt_path, expected_path, label = sys.argv[1:]
names = (
    "PORTAGE_TMPDIR",
    "PORTAGE_LOGDIR",
    "PORTAGE_DEPCACHEDIR",
    "DISTDIR",
    "PKGDIR",
    "CCACHE_DIR",
    "CCACHE_TEMPDIR",
    "SCCACHE_DIR",
)

def unique_rows(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("\t")
        if not separator or key in values:
            raise SystemExit(f"{label}: invalid or duplicate receipt row: {line!r}")
        values[key] = value
    return values

observed = unique_rows(receipt_path)
expected_lines = pathlib.Path(expected_path).read_text(encoding="utf-8").splitlines()
if not expected_lines or expected_lines[0] != (
    "schema\tgentoo-optimization-portage-writable-roots-v1"
):
    raise SystemExit(f"{label}: invalid expected writable-root schema")
expected: dict[str, tuple[str, str]] = {}
for line in expected_lines[1:]:
    fields = line.split("\t")
    if len(fields) != 6 or fields[0] in expected:
        raise SystemExit(f"{label}: invalid expected writable-root row: {line!r}")
    name, _query_raw, effective, canonical, _source, _status = fields
    expected[name] = (effective, canonical)
if set(expected) != set(names):
    raise SystemExit(f"{label}: expected writable-root names are incomplete")

failures: list[str] = []
for name in names:
    expected_raw, expected_canonical = expected[name]
    raw_key = f"{name}_raw"
    canonical_key = f"{name}_canonical"
    if observed.get(raw_key) != expected_raw:
        failures.append(
            f"{raw_key}: expected={expected_raw!r} observed={observed.get(raw_key)!r}"
        )
    if observed.get(canonical_key) != expected_canonical:
        failures.append(
            f"{canonical_key}: expected={expected_canonical!r} "
            f"observed={observed.get(canonical_key)!r}"
        )
if failures:
    raise SystemExit(f"{label}: writable-root receipt mismatch: {'; '.join(failures)}")
PY
}

if [[ -n ${WRITABLE_ROOT_RESOLVER_TEST} ]]; then
    [[ -z ${OUTPUT_DIR}${PORTAGE_POLICY_MODE}${WRITABLE_ROOT_RECEIPT_TEST} && \
        ${LIVE_POLICY_PREFLIGHT_ONLY} == 0 && ${PRODUCTION_LOCKS} == 0 ]] || \
        fail '--writable-root-resolver-test cannot be combined with other modes'
    [[ ${WRITABLE_ROOT_RESOLVER_TEST} == /* && \
        -f ${WRITABLE_ROOT_RESOLVER_TEST} && \
        -x ${WRITABLE_ROOT_RESOLVER_TEST} ]] || \
        fail '--writable-root-resolver-test requires an absolute executable'
    resolve_portage_writable_roots "${WRITABLE_ROOT_RESOLVER_TEST}" || \
        fail 'hermetic writable-root resolution failed'
    emit_portage_writable_roots
    exit 0
fi

if [[ -n ${WRITABLE_ROOT_RECEIPT_TEST} ]]; then
    [[ -z ${OUTPUT_DIR}${PORTAGE_POLICY_MODE}${WRITABLE_ROOT_RESOLVER_TEST} && \
        ${LIVE_POLICY_PREFLIGHT_ONLY} == 0 && ${PRODUCTION_LOCKS} == 0 ]] || \
        fail '--writable-root-receipt-test cannot be combined with other modes'
    verify_writable_root_receipt "${WRITABLE_ROOT_RECEIPT_TEST}" \
        "${WRITABLE_ROOT_RECEIPT_EXPECTED}" hermetic-receipt
    printf 'PASS: hermetic writable-root receipt matches\n'
    exit 0
fi

require_root_trusted_canonical_path() {
    local logical=$1 kind=$2 resolved current metadata mode
    [[ ${logical} == /* && ( -e ${logical} || -L ${logical} ) ]] || return 1
    [[ $(/usr/bin/stat -c %u -- "${logical}") == 0 ]] || return 1
    resolved=$(/usr/bin/realpath -e -- "${logical}") || return 1
    [[ ${resolved} == /* && ! -L ${resolved} ]] || return 1
    case ${kind} in
        executable) [[ -f ${resolved} && -x ${resolved} ]] || return 1 ;;
        regular) [[ -f ${resolved} ]] || return 1 ;;
        directory) [[ -d ${resolved} ]] || return 1 ;;
        *) return 1 ;;
    esac
    metadata=$(/usr/bin/stat -c '%u:%a' -- "${resolved}") || return 1
    mode=${metadata#*:}
    [[ ${metadata%%:*} == 0 ]] || return 1
    (( (8#${mode} & 8#022) == 0 )) || return 1
    current=${resolved}
    [[ -d ${current} ]] || current=${current%/*}
    [[ -n ${current} ]] || current=/
    while :; do
        [[ -d ${current} && ! -L ${current} && \
            $(/usr/bin/realpath -e -- "${current}") == "${current}" ]] || return 1
        metadata=$(/usr/bin/stat -c '%u:%a' -- "${current}") || return 1
        mode=${metadata#*:}
        [[ ${metadata%%:*} == 0 ]] || return 1
        (( (8#${mode} & 8#022) == 0 )) || return 1
        [[ ${current} == / ]] && break
        current=${current%/*}
        [[ -n ${current} ]] || current=/
    done
}

LIVE_POLICY_ERROR_SCHEMA=gentoo-optimization-live-policy-observation-error-v1
LIVE_POLICY_ROOT_IDENTITY_STATUS=74

emit_live_policy_observation_error() {
    local value
    for value in "$@"; do
        [[ -n ${value} && ${value} != *$'\t'* && ${value} != *$'\n'* ]] || \
            fail 'live-policy observation error contains an unsafe field'
    done
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${LIVE_POLICY_ERROR_SCHEMA}" "$1" "$2" "$3" "$4" "$5" "$6" \
        "$7" "$8" "$9" "${10}" >&2
}

observe_remapped_root_tool_identity() {
    local logical=$1 resolved root_uid current metadata mode
    resolved=$(/usr/bin/realpath -e -- "${logical}") || return 1
    [[ ${resolved} == /* && -f ${resolved} && -x ${resolved} && ! -L ${resolved} ]] || \
        return 1
    root_uid=$(/usr/bin/stat -c %u -- /) || return 1
    [[ ${root_uid} =~ ^[0-9]+$ ]] || return 1
    ((root_uid != 0)) || return 1
    metadata=$(/usr/bin/stat -c '%u:%a' -- "${resolved}") || return 1
    [[ ${metadata%%:*} == "${root_uid}" ]] || return 1
    mode=${metadata#*:}
    [[ ${mode} =~ ^[0-7]{3,4}$ ]] || return 1
    (( (8#${mode} & 8#022) == 0 )) || return 1
    current=${resolved%/*}
    [[ -n ${current} ]] || current=/
    while :; do
        [[ -d ${current} && ! -L ${current} && \
            $(/usr/bin/realpath -e -- "${current}") == "${current}" ]] || return 1
        metadata=$(/usr/bin/stat -c '%u:%a' -- "${current}") || return 1
        [[ ${metadata%%:*} == "${root_uid}" ]] || return 1
        (( (8#${metadata#*:} & 8#022) == 0 )) || return 1
        [[ ${current} == / ]] && break
        current=${current%/*}
        [[ -n ${current} ]] || current=/
    done
    printf -v mode '%04o' "$((8#${mode}))"
    emit_live_policy_observation_error \
        root-identity-unobservable not-applicable live-policy.tool-root-trust \
        "${resolved}" executable-regular 0 root-owned-no-group-world-write \
        executable-regular "${root_uid}" "${mode}"
}

resolve_live_portage_policy() {
    local output=$1 tool feature writable_roots_tsv
    local -a live_feature_tokens=()
    local -A live_feature_state=()
    for tool in /usr/bin/env /usr/bin/portageq /usr/bin/python3 \
        /usr/bin/realpath /usr/bin/sha256sum /usr/bin/stat /usr/bin/mktemp \
        /usr/bin/rm; do
        if ! require_root_trusted_canonical_path "${tool}" executable; then
            if [[ ${tool} == /usr/bin/env ]] && \
                observe_remapped_root_tool_identity "${tool}"; then
                return "${LIVE_POLICY_ROOT_IDENTITY_STATUS}"
            fi
            fail "live-policy tool or canonical ancestry is not root-trusted: ${tool}"
        fi
    done
    LIVE_MAKE_CONF=$(/usr/bin/realpath -e -- /etc/portage/make.conf)
    require_root_trusted_canonical_path "${LIVE_MAKE_CONF}" regular || \
        fail 'live make.conf or canonical ancestry is not root-trusted'
    [[ $(/usr/bin/stat -c %h -- "${LIVE_MAKE_CONF}") == 1 ]] || \
        fail 'live make.conf is not single-link'
    LIVE_MAKE_CONF_SHA256=$(/usr/bin/sha256sum -- "${LIVE_MAKE_CONF}")
    LIVE_MAKE_CONF_SHA256=${LIVE_MAKE_CONF_SHA256%% *}
    LIVE_PORTAGE_FEATURES=$(/usr/bin/env -i \
        HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
        PORTAGE_CONFIGROOT=/ /usr/bin/portageq envvar FEATURES)
    resolve_portage_writable_roots /usr/bin/portageq "${WORK-}" || \
        fail 'cannot resolve the complete live Portage writable-root set'
    LIVE_PORTAGE_TMPDIR_EFFECTIVE=${PORTAGE_WRITABLE_EFFECTIVE[PORTAGE_TMPDIR]}
    LIVE_PORTAGE_TMPDIR_PORTAGE_EFFECTIVE=${LIVE_PORTAGE_TMPDIR_EFFECTIVE%/}/portage
    LIVE_PORTAGE_TMPDIR_PORTAGE=$(/usr/bin/realpath -m -- \
        "${LIVE_PORTAGE_TMPDIR_PORTAGE_EFFECTIVE}") || \
        fail 'cannot canonicalize the live PORTAGE_TMPDIR/portage tree'
    LIVE_PORTAGE_LOGDIR=${PORTAGE_WRITABLE_CANONICAL[PORTAGE_LOGDIR]}
    LIVE_PORTAGE_DEPCACHEDIR=${PORTAGE_WRITABLE_CANONICAL[PORTAGE_DEPCACHEDIR]}
    LIVE_DISTDIR=${PORTAGE_WRITABLE_CANONICAL[DISTDIR]}
    LIVE_PKGDIR=${PORTAGE_WRITABLE_CANONICAL[PKGDIR]}
    LIVE_CCACHE_DIR=${PORTAGE_WRITABLE_CANONICAL[CCACHE_DIR]}
    LIVE_CCACHE_TEMPDIR=${PORTAGE_WRITABLE_CANONICAL[CCACHE_TEMPDIR]}
    LIVE_CCACHE_TEMPDIR_SOURCE=${PORTAGE_WRITABLE_SOURCE[CCACHE_TEMPDIR]}
    LIVE_SCCACHE_DIR=${PORTAGE_WRITABLE_CANONICAL[SCCACHE_DIR]}
    LIVE_SCCACHE_DIR_SOURCE=${PORTAGE_WRITABLE_SOURCE[SCCACHE_DIR]}
    [[ -n ${LIVE_PORTAGE_FEATURES} && ${LIVE_PORTAGE_FEATURES} != *$'\n'* ]] || \
        fail 'live Portage FEATURES did not resolve to one nonempty line'
    IFS=' ' read -r -a live_feature_tokens <<< "${LIVE_PORTAGE_FEATURES}"
    ((${#live_feature_tokens[@]} > 0)) || fail 'live Portage FEATURES is empty'
    for feature in "${live_feature_tokens[@]}"; do
        [[ ${feature} =~ ^-?[A-Za-z0-9][A-Za-z0-9+_.-]*$ ]] || \
            fail "live Portage FEATURES contains an unsafe token: ${feature}"
        if [[ ${feature} == -* ]]; then
            live_feature_state[${feature#-}]=0
        else
            live_feature_state[${feature}]=1
        fi
    done
    for feature in ccache sandbox usersandbox userpriv mount-sandbox pid-sandbox \
        ipc-sandbox network-sandbox; do
        [[ ${live_feature_state[${feature}]:-0} == 1 ]] || \
            fail "live Portage policy does not effectively enable ${feature}"
    done
    writable_roots_tsv=$(emit_portage_writable_roots)
    /usr/bin/env -i \
        HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
        PORTAGE_CONFIGROOT=/ LIVE_FEATURES="${LIVE_PORTAGE_FEATURES}" \
        LIVE_WRITABLE_ROOTS_TSV="${writable_roots_tsv}" \
        LIVE_PORTAGE_TMPDIR_PORTAGE_EFFECTIVE="${LIVE_PORTAGE_TMPDIR_PORTAGE_EFFECTIVE}" \
        LIVE_PORTAGE_TMPDIR_PORTAGE="${LIVE_PORTAGE_TMPDIR_PORTAGE}" \
        /usr/bin/python3 -I - "${output}" <<'PY'
import hashlib
import json
import os
import pathlib
import pwd
import stat
import sys

import portage

destination = pathlib.Path(sys.argv[1])

def digest(path: pathlib.Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()

def metadata(path: pathlib.Path, *, follow: bool = False) -> dict[str, object]:
    info = path.stat() if follow else path.lstat()
    return {
        "device": info.st_dev,
        "gid": info.st_gid,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "mtime_ns": info.st_mtime_ns,
        "nlink": info.st_nlink,
        "size": info.st_size,
        "uid": info.st_uid,
    }

def require_trusted_canonical(
    path: pathlib.Path, kind: str, allowed_uids: frozenset[int] = frozenset({0})
) -> pathlib.Path:
    resolved = path.resolve(strict=True)
    info = resolved.lstat()
    if info.st_uid not in allowed_uids or stat.S_IMODE(info.st_mode) & 0o022:
        raise SystemExit(f"untrusted {kind}: {resolved}")
    if kind == "regular" and not stat.S_ISREG(info.st_mode):
        raise SystemExit(f"not a regular file: {resolved}")
    if kind == "directory" and not stat.S_ISDIR(info.st_mode):
        raise SystemExit(f"not a directory: {resolved}")
    current = resolved if resolved.is_dir() else resolved.parent
    while True:
        current_info = current.lstat()
        if (
            not stat.S_ISDIR(current_info.st_mode)
            or current_info.st_uid not in allowed_uids
            or stat.S_IMODE(current_info.st_mode) & 0o022
            or current.is_symlink()
        ):
            raise SystemExit(f"untrusted canonical ancestor: {current}")
        if current == current.parent:
            break
        current = current.parent
    return resolved

def tree_identity(
    path: pathlib.Path,
    allowed_uids: frozenset[int] = frozenset({0}),
    symlink_target_uids: frozenset[int] | None = None,
) -> list[dict[str, object]]:
    if symlink_target_uids is None:
        symlink_target_uids = allowed_uids
    root = require_trusted_canonical(path, "directory", allowed_uids)
    entries: list[dict[str, object]] = []
    for node in [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]:
        info = node.lstat()
        if info.st_uid not in allowed_uids or (
            not stat.S_ISLNK(info.st_mode) and stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise SystemExit(f"untrusted policy node: {node}")
        if stat.S_ISREG(info.st_mode):
            kind, content, target = "regular", digest(node), None
        elif stat.S_ISDIR(info.st_mode):
            kind, content, target = "directory", None, None
        elif stat.S_ISLNK(info.st_mode):
            target_path = node.resolve(strict=True)
            target_info = target_path.lstat()
            if stat.S_ISREG(target_info.st_mode):
                require_trusted_canonical(node, "regular", symlink_target_uids)
                content = digest(target_path)
            elif stat.S_ISDIR(target_info.st_mode):
                require_trusted_canonical(node, "directory", symlink_target_uids)
                content = None
            else:
                raise SystemExit(f"unsupported policy symlink target: {node}")
            kind, target = "symlink", os.readlink(node)
        else:
            raise SystemExit(f"unsupported policy node: {node}")
        entries.append({
            "content_sha256": content,
            "kind": kind,
            "metadata": metadata(node),
            "path": node.as_posix(),
            "symlink_target": target,
        })
    return entries

make_conf = require_trusted_canonical(pathlib.Path("/etc/portage/make.conf"), "regular")
make_globals = require_trusted_canonical(
    pathlib.Path("/usr/share/portage/config/make.globals"), "regular"
)
portage_uid = pwd.getpwnam("portage").pw_uid
policy_uids = frozenset({0, portage_uid})
profile_selector = pathlib.Path("/etc/portage/make.profile")
selector_info = profile_selector.lstat()
if not stat.S_ISLNK(selector_info.st_mode) or selector_info.st_uid != 0:
    raise SystemExit("live profile selector is not a root-owned symlink")

profiles = []
seen_profiles: set[str] = set()
for raw_profile in portage.settings.profiles:
    profile = require_trusted_canonical(
        pathlib.Path(raw_profile), "directory", policy_uids
    )
    if profile.as_posix() in seen_profiles:
        raise SystemExit(f"duplicate resolved profile in chain: {profile}")
    seen_profiles.add(profile.as_posix())
    profiles.append({
        "path": profile.as_posix(),
        "tree": tree_identity(profile, policy_uids),
    })

repositories = []
for repo in portage.settings.repositories:
    location = require_trusted_canonical(
        pathlib.Path(repo.location), "directory", policy_uids
    )
    markers = []
    for relative in ("metadata/layout.conf", "profiles/repo_name"):
        marker = location / relative
        if marker.exists():
            trusted = require_trusted_canonical(marker, "regular", policy_uids)
            markers.append({
                "path": trusted.as_posix(),
                "sha256": digest(trusted),
                "metadata": metadata(trusted),
            })
    repositories.append({
        "location": location.as_posix(),
        "markers": markers,
        "masters": [getattr(master, "name", str(master)) for master in repo.masters],
        "name": repo.name,
    })

tools = []
for raw_tool in (
    "/usr/bin/env",
    "/usr/bin/portageq",
    "/usr/bin/python3",
    "/usr/bin/realpath",
    "/usr/bin/sha256sum",
    "/usr/bin/stat",
):
    logical = pathlib.Path(raw_tool)
    resolved_tool = require_trusted_canonical(logical, "regular")
    tools.append({
        "logical_path": logical.as_posix(),
        "metadata": metadata(resolved_tool),
        "path": resolved_tool.as_posix(),
        "sha256": digest(resolved_tool),
    })

repos_conf = pathlib.Path("/etc/portage/repos.conf")
portage_config_selector = pathlib.Path("/etc/portage")
portage_config_info = portage_config_selector.lstat()
if portage_config_info.st_uid != 0:
    raise SystemExit("live Portage config selector is not root-owned")
portage_config = require_trusted_canonical(portage_config_selector, "directory")
private_source_marker = portage_config / ".gentoo-optimization-source-hash"
if private_source_marker.exists() and not private_source_marker.is_symlink():
    private_source_marker_info = private_source_marker.lstat()
    if (
        stat.S_ISREG(private_source_marker_info.st_mode)
        and private_source_marker_info.st_uid == 0
        and stat.S_IMODE(private_source_marker_info.st_mode) == 0o600
    ):
        try:
            digest(private_source_marker)
        except PermissionError as error:
            canonical_marker = private_source_marker.resolve(strict=True).as_posix()
            fields = (
                "gentoo-optimization-live-policy-observation-error-v1",
                "permission-denied",
                str(error.errno),
                "portage_config.tree.regular-content-sha256",
                canonical_marker,
                "regular",
                "0",
                "0600",
                "regular",
                str(private_source_marker_info.st_uid),
                f"{stat.S_IMODE(private_source_marker_info.st_mode):04o}",
            )
            if any(not field or "\t" in field or "\n" in field for field in fields):
                raise SystemExit(
                    "live-policy permission error contains an unsafe field"
                ) from error
            sys.stderr.write("\t".join(fields) + "\n")
            raise SystemExit(73) from None
writable_root_lines = os.environ["LIVE_WRITABLE_ROOTS_TSV"].splitlines()
if not writable_root_lines or writable_root_lines[0] != (
    "schema\tgentoo-optimization-portage-writable-roots-v1"
):
    raise SystemExit("live writable-root identity has the wrong schema")
writable_roots = {}
for line in writable_root_lines[1:]:
    fields = line.split("\t")
    if len(fields) != 6 or fields[0] in writable_roots:
        raise SystemExit(f"invalid live writable-root row: {line!r}")
    name, raw, effective, canonical, source, query_status = fields
    writable_roots[name.lower()] = {
        "canonical": canonical,
        "effective": effective,
        "query_raw": raw,
        "query_status": int(query_status),
        "source": source,
    }
document = {
    "features_effective": {
        token.removeprefix("-"): not token.startswith("-")
        for token in os.environ["LIVE_FEATURES"].split()
    },
    "features_raw": os.environ["LIVE_FEATURES"],
    "make_conf": {
        "metadata": metadata(make_conf),
        "path": make_conf.as_posix(),
        "sha256": digest(make_conf),
    },
    "make_globals": {
        "metadata": metadata(make_globals),
        "path": make_globals.as_posix(),
        "sha256": digest(make_globals),
    },
    "profile_selector": {
        "metadata": metadata(profile_selector),
        "path": profile_selector.as_posix(),
        "resolved": profile_selector.resolve(strict=True).as_posix(),
        "target": os.readlink(profile_selector),
    },
    "profiles": profiles,
    "portage_config": tree_identity(
        portage_config, symlink_target_uids=policy_uids
    ),
    "portage_config_selector": {
        "kind": "symlink" if stat.S_ISLNK(portage_config_info.st_mode) else "directory",
        "metadata": metadata(portage_config_selector),
        "path": portage_config_selector.as_posix(),
        "resolved": portage_config.as_posix(),
        "target": os.readlink(portage_config_selector)
        if stat.S_ISLNK(portage_config_info.st_mode)
        else None,
    },
    "repositories": sorted(repositories, key=lambda item: item["name"]),
    "repositories_config": tree_identity(repos_conf),
    "schema": "gentoo-optimization-live-portage-policy-identity-v2",
    "tools": tools,
    "writable_roots": writable_roots,
    "writable_roots_derived": {
        "portage_tmpdir_portage": {
            "canonical": os.environ["LIVE_PORTAGE_TMPDIR_PORTAGE"],
            "effective": os.environ["LIVE_PORTAGE_TMPDIR_PORTAGE_EFFECTIVE"],
            "source": "below-portage-tmpdir",
        },
    },
}
destination.write_text(
    json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
    LIVE_POLICY_IDENTITY_SHA256=$(/usr/bin/sha256sum -- "${output}")
    LIVE_POLICY_IDENTITY_SHA256=${LIVE_POLICY_IDENTITY_SHA256%% *}
}

if ((LIVE_POLICY_PREFLIGHT_ONLY)); then
    [[ -z ${OUTPUT_DIR}${PORTAGE_POLICY_MODE} && ${PRODUCTION_LOCKS} == 0 ]] || \
        fail '--live-policy-preflight cannot be combined with build or production options'
    PREFLIGHT_IDENTITY=$(/usr/bin/mktemp \
        /tmp/gentoo-optimization-live-policy.XXXXXXXX)
    trap '/usr/bin/rm -f -- "${PREFLIGHT_IDENTITY}"' EXIT
    resolve_live_portage_policy "${PREFLIGHT_IDENTITY}"
    printf 'schema\tgentoo-optimization-sample-live-policy-preflight-v2\n'
    printf 'live_make_conf\t%s\n' "${LIVE_MAKE_CONF}"
    printf 'live_make_conf_sha256\t%s\n' "${LIVE_MAKE_CONF_SHA256}"
    printf 'live_resolved_features\t%s\n' "${LIVE_PORTAGE_FEATURES}"
    printf 'live_policy_identity_sha256\t%s\n' "${LIVE_POLICY_IDENTITY_SHA256}"
    printf 'live_portage_depcachedir\t%s\n' "${LIVE_PORTAGE_DEPCACHEDIR}"
    printf 'live_portage_logdir\t%s\n' "${LIVE_PORTAGE_LOGDIR}"
    printf 'live_distdir\t%s\n' "${LIVE_DISTDIR}"
    printf 'live_pkgdir\t%s\n' "${LIVE_PKGDIR}"
    printf 'live_ccache_dir\t%s\n' "${LIVE_CCACHE_DIR}"
    printf 'live_ccache_tempdir\t%s\n' "${LIVE_CCACHE_TEMPDIR}"
    printf 'live_ccache_tempdir_source\t%s\n' "${LIVE_CCACHE_TEMPDIR_SOURCE}"
    printf 'live_sccache_dir\t%s\n' "${LIVE_SCCACHE_DIR}"
    printf 'live_sccache_dir_source\t%s\n' "${LIVE_SCCACHE_DIR_SOURCE}"
    /usr/bin/rm -f -- "${PREFLIGHT_IDENTITY}"
    trap - EXIT
    exit 0
fi

production_authorized_command() {
    ((PRODUCTION_LOCKS)) || fail 'internal production command used outside production mode'
    [[ ${PRODUCTION_TRANSACTION_TOKEN} =~ ^[0-9a-f]{64}$ &&
        -n ${TRANSACTION_AUTHORIZATION-} ]] ||
        fail 'internal production command lacks its exact coordinator authorization'
    /usr/bin/env -i \
        HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        LANG=C LC_ALL=C TZ=UTC \
        "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN=${PRODUCTION_TRANSACTION_TOKEN}" \
        "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION=${TRANSACTION_AUTHORIZATION}" \
        "$@"
}

if [[ -n ${OUTPUT_DIR} ]]; then
    [[ ${OUTPUT_DIR} == /* && ${OUTPUT_DIR} != / ]] || \
        fail '--output-dir must be an absolute non-root path'
    command -v realpath >/dev/null 2>&1 || fail 'missing command: realpath'
    CANONICAL_OUTPUT_DIR=$(realpath -m -- "${OUTPUT_DIR}")
    if ((PRODUCTION_LOCKS)); then
        [[ ${CANONICAL_OUTPUT_DIR} == "${PRODUCTION_OUTPUT_BASE}"/* ]] || \
            fail '--production-locks output must remain below the authoritative report root'
    else
        [[ ${CANONICAL_OUTPUT_DIR} == "${TRUSTED_OUTPUT_BASE}"/* ]] || \
            fail '--output-dir must remain below /var/tmp/gentoo-optimization'
    fi
    [[ ${CANONICAL_OUTPUT_DIR} =~ ^/[A-Za-z0-9_./-]+$ ]] || \
        fail '--output-dir contains unsafe characters'
    if [[ -e ${CANONICAL_OUTPUT_DIR} || -L ${CANONICAL_OUTPUT_DIR} ]]; then
        if ((PRODUCTION_LOCKS)) && [[ -d ${CANONICAL_OUTPUT_DIR} && ! -L ${CANONICAL_OUTPUT_DIR} ]]; then
            IFS=' ' read -r output_mode output_uid output_gid < <(
                stat -c '%a %u %g' -- "${CANONICAL_OUTPUT_DIR}"
            )
            [[ ${output_mode} == 750 && ${output_uid} == 0 && ${output_gid} == 0 ]] || \
                fail 'production --output-dir must be an empty root-owned 0750 directory'
            [[ -z $(find "${CANONICAL_OUTPUT_DIR}" -mindepth 1 -maxdepth 1 -print -quit) ]] || \
                fail 'production --output-dir must be empty'
        else
            fail "--output-dir already exists: ${CANONICAL_OUTPUT_DIR}"
        fi
    fi
fi
[[ ${ITERATIONS} =~ ^[1-9][0-9]*$ ]] || fail 'iteration count must be positive'
[[ ${KEEP_TEMP} == 0 || ${KEEP_TEMP} == 1 ]] || fail 'KEEP_TEMP must be 0 or 1'
[[ ${PRODUCTION_LOCKS} == 0 || ${PRODUCTION_LOCKS} == 1 ]] || \
    fail 'PRODUCTION_LOCKS must be 0 or 1'
case ${PORTAGE_POLICY_MODE} in
    isolated-diagnostic|live) ;;
    '') fail '--portage-policy is required' ;;
    *) fail '--portage-policy must be isolated-diagnostic or live' ;;
esac
if ((PRODUCTION_LOCKS)); then
    [[ ${PORTAGE_POLICY_MODE} == live ]] || \
        fail '--production-locks requires --portage-policy live'
    AUTHORIZATION_TOKEN_SCANNER=/usr/local/libexec/gentoo-optimization/pgo/authorization-token-scan.py
    [[ -n ${OUTPUT_DIR} ]] || fail '--production-locks requires an explicit output directory'
    [[ -z ${PORTAGE_SAMPLE_ITERATIONS_WAS_SET}${KEEP_TEMP_WAS_SET} ]] || \
        fail '--production-locks forbids inherited workload or retention overrides'
    while IFS= read -r variable; do
        case ${variable} in
            GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN|\
            GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION|\
            GENTOO_OPT_PRODUCTION_GATE_RUN_ID|\
            GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID|\
            GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID|\
            GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256|\
            GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT) ;;
            GENTOO_OPT_*)
                fail "--production-locks inherited a forbidden optimization override: ${variable}"
                ;;
        esac
    done < <(compgen -e | LC_ALL=C sort)
    for variable in PRODUCTION_GATE_RUN_ID PRODUCTION_GATE_GENERATION_ID \
        PRODUCTION_GATE_INVENTORY_ID PRODUCTION_GATE_INVENTORY_SHA256 \
        PRODUCTION_GATE_WORK_ROOT; do
        [[ -n ${!variable} ]] || fail "--production-locks requires ${variable}"
    done
    for variable in PRODUCTION_GATE_RUN_ID PRODUCTION_GATE_GENERATION_ID \
        PRODUCTION_GATE_INVENTORY_ID; do
        value=${!variable}
        [[ ${value} =~ ^[A-Za-z0-9+_.@-]+$ && ${value} != . && \
            ${value} != .. && ${#value} -le 128 ]] || \
            fail "${variable} is not a safe common identity component"
    done
    [[ ${PRODUCTION_GATE_INVENTORY_SHA256} =~ ^[0-9a-f]{64}$ ]] || \
        fail 'production gate inventory SHA-256 is malformed'
    [[ ${PRODUCTION_TRANSACTION_TOKEN} =~ ^[0-9a-f]{64}$ ]] || \
        fail '--production-locks must be supervised by the production lock coordinator'
    [[ ${PRODUCTION_TRANSACTION_AUTHORIZATION} == /* && \
        ${PRODUCTION_TRANSACTION_AUTHORIZATION} != / ]] || \
        fail '--production-locks requires the coordinator authorization path'
fi
((EUID == 0)) || {
    printf 'SKIP: real Portage sample-PGO integration requires root\n'
    exit 77
}
for command in awk b2sum bash chmod chown cp cmp cut date ebuild env find getent grep \
    hostname id install jq ln mkdir mktemp mv perf portageq python3 readelf readlink rm runuser sed \
    realpath sha256sum sha512sum sort stat sync tail timeout touch xargs; do
    command -v "${command}" >/dev/null 2>&1 || fail "missing command: ${command}"
done
if ((PRODUCTION_LOCKS)); then
    for command in mount unshare; do
        command -v "${command}" >/dev/null 2>&1 || \
            fail "missing production tamper-isolation command: ${command}"
    done
    unshare --mount --propagation private -- true >/dev/null 2>&1 || \
        fail 'production tamper isolation cannot create a private mount namespace'
fi
[[ -f ${TEMPLATE} && -x ${INSTALLER} ]] || fail 'fixture template or installer is absent'
[[ -f ${AUTHORIZATION_TOKEN_SCANNER} && ! -L ${AUTHORIZATION_TOKEN_SCANNER} ]] || \
    fail 'authorization-token persistence scanner is absent or symlinked'
for tool in "${PROFILE_IDENTITY}" "${VALIDATOR}" "${CLANG_LINK}" \
    "${CLANGXX_LINK}" "${PROFGEN_LINK}" "${PROFDATA_LINK}" \
    "${READELF_LINK}" "${OBJCOPY_LINK}" "${PERF}"; do
    [[ -x ${tool} ]] || fail "required exact tool is absent: ${tool}"
done
if ((PRODUCTION_LOCKS == 0)); then
    "${INSTALLER}" --source-root "${ROOT}" --check >/dev/null || \
        fail 'installed framework differs from the reviewed repository source'
fi
FRAMEWORK_CURRENT=/var/lib/gentoo-optimization/framework-current
[[ -L ${FRAMEWORK_CURRENT} && $(stat -c '%u:%g' -- "${FRAMEWORK_CURRENT}") == 0:0 ]] || \
    fail 'active framework selector is not the trusted root-owned symlink'
FRAMEWORK_TARGET=$(readlink -- "${FRAMEWORK_CURRENT}")
FRAMEWORK_ID=${FRAMEWORK_TARGET#/var/lib/gentoo-optimization/framework-}
[[ ${FRAMEWORK_TARGET} == "/var/lib/gentoo-optimization/framework-${FRAMEWORK_ID}" && \
    ${FRAMEWORK_ID} =~ ^[0-9a-f]{64}$ && -d ${FRAMEWORK_TARGET} && \
    ! -L ${FRAMEWORK_TARGET} ]] || fail 'active framework target is unmanaged or unavailable'
export GENTOO_OPT_FRAMEWORK_TARGET=${FRAMEWORK_TARGET}

if [[ -n ${OUTPUT_DIR} ]]; then
    OUTPUT_PARENT=${CANONICAL_OUTPUT_DIR%/*}
    if ((PRODUCTION_LOCKS)); then
        CURRENT_OUTPUT_ANCESTOR=${PRODUCTION_OUTPUT_BASE}
        OUTPUT_RELATIVE_PARENT=${OUTPUT_PARENT#"${PRODUCTION_OUTPUT_BASE}"}
    else
        [[ -d /var/tmp && ! -L /var/tmp && $(realpath -e -- /var/tmp) == /var/tmp && \
            $(stat -c %u -- /var/tmp) == 0 ]] || \
            fail '/var/tmp is not the canonical root-owned output boundary'
        VAR_TMP_MODE=$(stat -c %a -- /var/tmp)
        (( (8#${VAR_TMP_MODE} & 8#1000) != 0 )) || \
            fail '/var/tmp output boundary lacks the sticky bit'
        CURRENT_OUTPUT_ANCESTOR=${TRUSTED_OUTPUT_BASE}
        OUTPUT_RELATIVE_PARENT=${OUTPUT_PARENT#"${TRUSTED_OUTPUT_BASE}"}
    fi
    OUTPUT_RELATIVE_PARENT=${OUTPUT_RELATIVE_PARENT#/}
    IFS=/ read -r -a OUTPUT_ANCESTOR_COMPONENTS <<< "${OUTPUT_RELATIVE_PARENT}"
    for OUTPUT_ANCESTOR_COMPONENT in '' "${OUTPUT_ANCESTOR_COMPONENTS[@]}"; do
        if [[ -n ${OUTPUT_ANCESTOR_COMPONENT} ]]; then
            CURRENT_OUTPUT_ANCESTOR+=/${OUTPUT_ANCESTOR_COMPONENT}
        fi
        [[ -d ${CURRENT_OUTPUT_ANCESTOR} && ! -L ${CURRENT_OUTPUT_ANCESTOR} && \
            $(realpath -e -- "${CURRENT_OUTPUT_ANCESTOR}") == \
            "${CURRENT_OUTPUT_ANCESTOR}" && \
            $(stat -c %u -- "${CURRENT_OUTPUT_ANCESTOR}") == "${EUID}" ]] || \
            fail "untrusted output ancestor: ${CURRENT_OUTPUT_ANCESTOR}"
        OUTPUT_ANCESTOR_MODE=$(stat -c %a -- "${CURRENT_OUTPUT_ANCESTOR}")
        (( (8#${OUTPUT_ANCESTOR_MODE} & 8#022) == 0 )) || \
            fail "group/world-writable output ancestor: ${CURRENT_OUTPUT_ANCESTOR}"
    done
    EXPLICIT_OUTPUT_DIR=1
fi
if ((PRODUCTION_LOCKS)); then
    EXPECTED_PRODUCTION_WORK_ROOT=${TRUSTED_OUTPUT_BASE}/phase2-sample-work-${PRODUCTION_GATE_RUN_ID}
    [[ ${PRODUCTION_GATE_WORK_ROOT} == "${EXPECTED_PRODUCTION_WORK_ROOT}" ]] || \
        fail 'production work root differs from its exact coordinator contract'
    if [[ -e ${PRODUCTION_GATE_WORK_ROOT} || -L ${PRODUCTION_GATE_WORK_ROOT} ]]; then
        [[ -d ${PRODUCTION_GATE_WORK_ROOT} && ! -L ${PRODUCTION_GATE_WORK_ROOT} ]] || \
            fail 'production work root is not a directory'
        IFS=' ' read -r work_mode work_uid work_gid < <(
            stat -c '%a %u %g' -- "${PRODUCTION_GATE_WORK_ROOT}"
        )
        [[ ${work_mode} == 755 && ${work_uid} == 0 && ${work_gid} == 0 ]] || \
            fail 'production work root must be root-owned 0755'
    fi
    install -d -o 0 -g 0 -m 0700 -- "${PRODUCTION_GATE_WORK_ROOT}"
    sync -f -- "${PRODUCTION_GATE_WORK_ROOT}" "${PRODUCTION_GATE_WORK_ROOT%/*}"
    WORK=${PRODUCTION_GATE_WORK_ROOT}
else
    WORK=$(mktemp -d /var/tmp/gentoo-phase2-pgo-portage.sample.XXXXXX)
fi
printf 'gentoo-optimization-portage-sample-fixture-v1\n' > \
    "${WORK}/.optimization-fixture-root"
if ((PRODUCTION_LOCKS)); then
    python3 - "${WORK}/portage-process-preflight.tsv" <<'PY'
import hashlib
import os
import pathlib
import sys

output = pathlib.Path(sys.argv[1])
blocked = {
    "ebuild", "eclean", "emaint", "emerge", "portageq", "quickpkg",
}
rows = []
for process in pathlib.Path("/proc").iterdir():
    if not process.name.isdigit() or int(process.name) == os.getpid():
        continue
    try:
        arguments = [
            item.decode("utf-8", "replace")
            for item in (process / "cmdline").read_bytes().split(b"\0")
            if item
        ]
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    matches = sorted(
        {
            pathlib.PurePath(argument).name
            for argument in arguments
            if pathlib.PurePath(argument).name in blocked
        }
    )
    if matches:
        rows.append((int(process.name), ",".join(matches), " ".join(arguments)))
with output.open("w", encoding="utf-8") as stream:
    stream.write("pid\tmatched_tools\tcommand\n")
    for pid, matches, command in sorted(rows):
        stream.write(f"{pid}\t{matches}\t{command}\n")
if rows:
    raise SystemExit("an existing Portage process makes the production gate unsafe")
PY
    PROBE_SOURCE=${WORK}/mount-probe.source
    PROBE_TARGET=${WORK}/mount-probe.target
    printf 'private-source\n' > "${PROBE_SOURCE}"
    printf 'host-target\n' > "${PROBE_TARGET}"
    # shellcheck disable=SC2016  # Positional parameters expand in the child shell.
    unshare --mount --propagation private -- bash -Eeuo pipefail -c '
        mount --bind "$1" "$2"
        cmp -- "$1" "$2"
    ' bash "${PROBE_SOURCE}" "${PROBE_TARGET}" || \
        fail 'production tamper isolation cannot bind a private regular-file substitute'
    grep -Fxq host-target "${PROBE_TARGET}" || \
        fail 'private bind-mount preflight escaped into the host mount namespace'
    rm -f -- "${PROBE_SOURCE}" "${PROBE_TARGET}"
fi

emit_publication_tree() {
    local root=$1
    python3 - "${root}" <<'PY'
import base64
import json
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
excluded = {"publication-tree.jsonl", "publication-root.sha256"}
raw: list[tuple[pathlib.Path, os.stat_result]] = []
for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
    relative = path.relative_to(root)
    # Portage's live IPC helper creates transient sockets under .ipc while
    # the fixture runs.  They are runtime plumbing, not evidence objects;
    # never admit them to the immutable publication tree.
    if relative.as_posix() in excluded or ".ipc" in relative.parts:
        continue
    raw.append((relative, path.lstat()))

inode_paths: dict[tuple[int, int], list[str]] = {}
for relative, metadata in raw:
    if stat.S_ISREG(metadata.st_mode):
        inode_paths.setdefault((metadata.st_dev, metadata.st_ino), []).append(
            relative.as_posix()
        )

for relative, metadata in raw:
    path = root / relative
    mode = metadata.st_mode
    if stat.S_ISREG(mode):
        kind = "regular"
    elif stat.S_ISDIR(mode):
        kind = "directory"
    elif stat.S_ISLNK(mode):
        kind = "symlink"
    else:
        raise SystemExit(f"unsupported evidence node: {relative.as_posix()}")
    names = sorted(os.listxattr(path, follow_symlinks=False))
    xattrs = [
        {
            "name": name,
            "value_base64": base64.b64encode(
                os.getxattr(path, name, follow_symlinks=False)
            ).decode("ascii"),
        }
        for name in names
    ]
    hardlinks: list[str] = []
    if kind == "regular":
        hardlinks = sorted(inode_paths[(metadata.st_dev, metadata.st_ino)])
        if metadata.st_nlink != len(hardlinks):
            raise SystemExit(
                f"external hardlink in evidence tree: {relative.as_posix()}"
            )
    record = {
        "gid": metadata.st_gid,
        "hardlink_paths": hardlinks,
        "kind": kind,
        "link_count": metadata.st_nlink,
        "mode": stat.S_IMODE(mode),
        "mtime_ns": metadata.st_mtime_ns,
        "path": relative.as_posix(),
        "size": metadata.st_size,
        "symlink_target": os.readlink(path) if kind == "symlink" else None,
        "uid": metadata.st_uid,
        "xattrs": xattrs,
    }
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
PY
}

prepare_publication_manifests() {
    local evidence_gid=${PORTAGE_GID:-0}
    (
        cd -- "${WORK}"
        find . -type f \
            ! -path './publication-files.sha256' \
            ! -path './publication-tree.jsonl' \
            ! -path './publication-root.sha256' \
            -print0 | LC_ALL=C sort -z | xargs -0r sha256sum -- \
            > publication-files.sha256
    ) || return 1
    chown "0:${evidence_gid}" -- "${WORK}/publication-files.sha256" || return 1
    chmod 0440 -- "${WORK}/publication-files.sha256" || return 1
    emit_publication_tree "${WORK}" > "${WORK}/publication-tree.jsonl" || return 1
    chown "0:${evidence_gid}" -- "${WORK}/publication-tree.jsonl" || return 1
    chmod 0440 -- "${WORK}/publication-tree.jsonl" || return 1
    (
        cd -- "${WORK}"
        sha256sum -- publication-files.sha256 publication-tree.jsonl \
            > publication-root.sha256
    ) || return 1
    chown "0:${evidence_gid}" -- "${WORK}/publication-root.sha256" || return 1
    chmod 0440 -- "${WORK}/publication-root.sha256"
}

seal_authoritative_work() {
    local evidence_gid=${PORTAGE_GID:-0} unexpected
    # Reject special nodes and links escaping this evidence tree before any
    # recursive metadata operation could affect an external inode.
    emit_publication_tree "${WORK}" >/dev/null || return 1
    find "${WORK}" -xdev -type l -exec chown -h "0:${evidence_gid}" -- {} + || return 1
    find "${WORK}" -xdev \( -type f -o -type d \) \
        -exec chown "0:${evidence_gid}" -- {} + || return 1
    find "${WORK}" -xdev \( -type f -o -type d \) \
        -exec chmod go-w -- {} + || return 1
    if [[ -n ${PORTAGE_GID:-} ]]; then
        chmod 0750 -- "${WORK}" || return 1
    else
        chmod 0700 -- "${WORK}" || return 1
    fi
    unexpected=$(find "${WORK}" -xdev \
        \( \( -type f -o -type d -o -type l \) \
        \( ! -uid 0 -o ! -gid "${evidence_gid}" \) \) -print -quit) || return 1
    [[ -z ${unexpected} ]] || {
        printf 'FAIL: unsealed evidence owner: %s\n' "${unexpected}" >&2
        return 1
    }
    unexpected=$(find "${WORK}" -xdev \( -type f -o -type d \) \
        -perm /022 -print -quit) || return 1
    [[ -z ${unexpected} ]] || {
        printf 'FAIL: writable authoritative evidence node: %s\n' "${unexpected}" >&2
        return 1
    }
}

sync_authoritative_work() {
    find "${WORK}" -type f -exec sync -f -- {} + &&
        find "${WORK}" -depth -type d -exec sync -f -- {} + &&
        sync -f -- /var/tmp
}

require_trusted_production_directory_chain() {
    local current=$1 owner mode
    [[ ${current} == /* && -d ${current} && ! -L ${current} && \
        $(realpath -e -- "${current}") == "${current}" ]] || return 1
    while :; do
        [[ -d ${current} && ! -L ${current} ]] || return 1
        owner=$(stat -c %u -- "${current}") || return 1
        mode=$(stat -c %a -- "${current}") || return 1
        [[ ${owner} == 0 && ${mode} =~ ^[0-7]{3,4}$ ]] || return 1
        (( (8#${mode} & 8#022) == 0 )) || return 1
        [[ ${current} == / ]] && break
        current=${current%/*}
        [[ -n ${current} ]] || current=/
    done
}

write_production_gate_status() {
    local status=$1 exit_status=$2 root destination partial
    ((PRODUCTION_LOCKS && PRODUCTION_ROOTS_CREATED)) || return 0
    [[ ${status} == in-progress || ${status} == failed || ${status} == passed ]] || \
        return 1
    for root in "${PRODUCTION_STATE_ROOT}" "${PROFILE_ROOT}"; do
        [[ -d ${root} && ! -L ${root} ]] || return 1
        destination=${root}/gate-status.tsv
        partial=${destination}.partial.$$
        rm -f -- "${partial}"
        {
            printf 'schema\tgentoo-optimization-phase2-sample-gate-v1\n'
            printf 'status\t%s\n' "${status}"
            printf 'exit_status\t%s\n' "${exit_status}"
            printf 'run_id\t%s\n' "${PRODUCTION_GATE_RUN_ID}"
            printf 'generation_id\t%s\n' "${GENERATION_ID}"
            printf 'inventory_id\t%s\n' "${INVENTORY_ID}"
            printf 'inventory_sha256\t%s\n' "${INVENTORY_SHA256}"
            printf 'framework_target\t%s\n' "${FRAMEWORK_TARGET}"
            printf 'work_root\t%s\n' "${PRODUCTION_GATE_WORK_ROOT}"
            printf 'profile_root\t%s\n' "${PROFILE_ROOT}"
            printf 'generation_state_root\t%s\n' "${PRODUCTION_STATE_ROOT}"
            printf 'recorded_at_utc\t%s\n' "$(TZ=UTC date -u +%FT%TZ)"
        } > "${partial}" || return 1
        chown "0:${PORTAGE_GID}" -- "${partial}" || return 1
        chmod 0440 -- "${partial}" || return 1
        sync -f -- "${partial}" || return 1
        mv -fT -- "${partial}" "${destination}" || return 1
        sync -f -- "${root}" || return 1
    done
}

emit_production_live_root_index() {
    local status=$1 root entry kind digest metadata
    printf 'gate_status\t%s\n' "${status}"
    printf 'profile_root\t%s\n' "${PROFILE_ROOT}"
    printf 'generation_state_root\t%s\n' "${PRODUCTION_STATE_ROOT}"
    for root in "${PROFILE_ROOT}" "${PRODUCTION_STATE_ROOT}"; do
        if [[ ! -e ${root} && ! -L ${root} ]]; then
            printf 'missing\t%s\n' "${root}"
            continue
        fi
        [[ -d ${root} && ! -L ${root} ]] || return 1
        while IFS= read -r -d '' entry; do
            if [[ -d ${entry} && ! -L ${entry} ]]; then
                kind=directory
                digest=-
            elif [[ -f ${entry} && ! -L ${entry} ]]; then
                kind=regular
                digest=$(sha256sum -- "${entry}") || return 1
                digest=${digest%% *}
            else
                printf 'unsupported\t%s\n' "${entry}"
                return 1
            fi
            metadata=$(stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${entry}") || \
                return 1
            printf 'artifact\t%s\t%s\t%s\t%s\n' \
                "${kind}" "${entry}" "${digest}" "${metadata}"
        done < <(find "${root}" -xdev -print0 | LC_ALL=C sort -z)
    done
}

capture_production_live_roots() {
    local status=$1 index_partial=${WORK}/production-live-roots.tsv.partial
    local root
    local -a roots=()
    ((PRODUCTION_LOCKS && PRODUCTION_ROOTS_CREATED)) || return 0
    for root in "${PROFILE_ROOT}" "${PRODUCTION_STATE_ROOT}"; do
        if [[ -d ${root} && ! -L ${root} ]]; then
            roots+=("${root}")
        elif [[ -e ${root} || -L ${root} ]]; then
            return 1
        fi
    done
    if ((${#roots[@]})); then
        find "${roots[@]}" -type f -exec sync -f -- {} + || return 1
        find "${roots[@]}" -depth -type d -exec sync -f -- {} + || return 1
    fi
    for root in "${PROFILE_ROOT%/*}" "${PRODUCTION_STATE_ROOT%/*}"; do
        if [[ -e ${root} || -L ${root} ]]; then
            require_trusted_production_directory_chain "${root}" || return 1
            sync -f -- "${root}" || return 1
        fi
    done
    rm -rf -- "${WORK}/profile" "${WORK}/production-state"
    rm -f -- "${WORK}/perf.data" "${WORK}/production-live-roots.tsv" \
        "${index_partial}"
    emit_production_live_root_index "${status}" > "${index_partial}" || {
        rm -f -- "${index_partial}"
        return 1
    }
    mv -T -- "${index_partial}" "${WORK}/production-live-roots.tsv" || return 1
    if [[ -d ${PROFILE_ROOT} && ! -L ${PROFILE_ROOT} ]]; then
        cp -a -- "${PROFILE_ROOT}" "${WORK}/profile" || return 1
    fi
    if [[ -d ${PRODUCTION_STATE_ROOT} && ! -L ${PRODUCTION_STATE_ROOT} ]]; then
        cp -a -- "${PRODUCTION_STATE_ROOT}" "${WORK}/production-state" || return 1
    fi
    if [[ -f ${PERF_DATA} && ! -L ${PERF_DATA} ]]; then
        cp -a -- "${PERF_DATA}" "${WORK}/perf.data" || return 1
    fi
}

finalize_authoritative_work() {
    local status=$1 evidence_gid=${PORTAGE_GID:-0}
    rm -f -- "${WORK}/publication-files.sha256" \
        "${WORK}/publication-tree.jsonl" "${WORK}/publication-root.sha256"
    seal_authoritative_work || return 1
    printf 'exit_status\t%s\n' "${status}" > "${WORK}/fixture-status.tsv" || return 1
    chown "0:${evidence_gid}" -- "${WORK}/fixture-status.tsv" || return 1
    chmod 0440 -- "${WORK}/fixture-status.tsv" || return 1
    prepare_publication_manifests || return 1
    seal_authoritative_work || return 1
    sync_authoritative_work
}

quarantine_published_evidence() {
    local quarantine=${CANONICAL_OUTPUT_DIR}.failed.$$
    [[ -e ${CANONICAL_OUTPUT_DIR} || -L ${CANONICAL_OUTPUT_DIR} ]] || return 0
    [[ ! -e ${quarantine} && ! -L ${quarantine} ]] || return 1
    mv -T -- "${CANONICAL_OUTPUT_DIR}" "${quarantine}" || return 1
    sync -f -- "${OUTPUT_PARENT}" || return 1
    printf 'FAILED_EVIDENCE: %s\n' "${quarantine}" >&2
}

publish_evidence() {
    local status=$1 partial=${CANONICAL_OUTPUT_DIR}.partial.$$
    [[ ${EXPLICIT_OUTPUT_DIR} == 1 ]] || return 0
    if [[ -e ${CANONICAL_OUTPUT_DIR} || -L ${CANONICAL_OUTPUT_DIR} ]]; then
        if ! ((PRODUCTION_LOCKS)) || [[ ! -d ${CANONICAL_OUTPUT_DIR} || -L ${CANONICAL_OUTPUT_DIR} ]] || \
            [[ -n $(find "${CANONICAL_OUTPUT_DIR}" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
            printf 'FAIL: sample-PGO evidence publication destination is no longer empty\n' >&2
            return 1
        fi
    fi
    if [[ -e ${partial} || -L ${partial} ]]; then
        printf 'FAIL: sample-PGO evidence partial destination already exists\n' >&2
        return 1
    fi
    if ! cp -a -- "${WORK}" "${partial}" || ! chmod 0700 -- "${partial}"; then
        rm -rf -- "${partial}"
        printf 'FAIL: could not create the private partial evidence tree\n' >&2
        return 1
    fi
    if [[ ! -d ${partial} || -L ${partial} || \
        $(stat -c '%u:%a' -- "${partial}") != "${EUID}:700" ]]; then
        rm -rf -- "${partial}"
        printf 'FAIL: partial evidence publication has the wrong identity\n' >&2
        return 1
    fi
    if ! find "${partial}" -type f -exec sync -f -- {} + || \
        ! find "${partial}" -depth -type d -exec sync -f -- {} +; then
        rm -rf -- "${partial}"
        printf 'FAIL: evidence partial could not be made durable\n' >&2
        return 1
    fi
    if ! (cd -- "${partial}" && \
        sha256sum -c -- publication-files.sha256 publication-root.sha256) || \
        ! cmp -s -- "${partial}/publication-tree.jsonl" \
            <(emit_publication_tree "${partial}"); then
        rm -rf -- "${partial}"
        printf 'FAIL: evidence partial does not match its exhaustive integrity root\n' >&2
        return 1
    fi
    if ((status == 0)) && ! (cd -- "${partial}" && \
        sha256sum -c -- evidence.sha256 generated-policy.sha256); then
        rm -rf -- "${partial}"
        printf 'FAIL: successful evidence partial fails its inner manifests\n' >&2
        return 1
    fi
    if ! mv -T -- "${partial}" "${CANONICAL_OUTPUT_DIR}"; then
        rm -rf -- "${partial}"
        printf 'FAIL: atomic evidence publication rename failed\n' >&2
        return 1
    fi
    if ! sync -f -- "${OUTPUT_PARENT}"; then
        quarantine_published_evidence || \
            printf 'FAIL: could not quarantine the visible failed publication\n' >&2
        printf 'FAIL: evidence publication parent could not be made durable\n' >&2
        return 1
    fi
}

cleanup() {
    local status=$? finalized=0
    trap '' HUP INT TERM
    if ((PRODUCTION_LOCKS && PRODUCTION_ROOTS_CREATED)); then
        if ((status == 0 && PRODUCTION_GATE_COMPLETE && \
            PRODUCTION_STATUS_FINALIZED && PRODUCTION_CAPTURED)); then
            :
        else
            ((status != 0)) || status=1
            if ! write_production_gate_status failed "${status}"; then
                printf 'FAIL: could not durably mark live production sample-gate roots failed\n' >&2
            fi
            if ! capture_production_live_roots failed; then
                printf 'FAIL: could not index the failed live production sample-gate roots\n' >&2
            fi
        fi
    fi
    if ((status != 0)); then
        printf 'Sample-PGO fixture evidence remains readable in %s until cleanup.\n' "${WORK}" >&2
        for log in "${WORK}"/*.log; do
            [[ -f ${log} ]] || continue
            printf '\n--- %s ---\n' "${log}" >&2
            tail -n 160 -- "${log}" >&2 || :
        done
    fi
    if finalize_authoritative_work "${status}"; then
        finalized=1
    else
        printf 'FAIL: could not durably finalize the authoritative sample-PGO evidence tree\n' >&2
        status=1
        if finalize_authoritative_work "${status}"; then
            finalized=1
        else
            printf 'FAIL: failed evidence remains preserved but lacks a complete integrity root\n' >&2
        fi
    fi
    if ((finalized == 1)); then
        if ! publish_evidence "${status}"; then
            printf 'FAIL: could not publish complete sample-PGO fixture evidence\n' >&2
            status=1
            # The publication copy is historical.  The preserved Work tree is
            # the authoritative object referenced by the validator sidecars,
            # so make its final failure state durable even if copy publication
            # failed.
            if ! finalize_authoritative_work "${status}"; then
                printf 'FAIL: could not durably record the publication failure\n' >&2
            fi
        elif [[ ${EXPLICIT_OUTPUT_DIR} == 1 ]]; then
            printf 'EVIDENCE: %s\n' "${CANONICAL_OUTPUT_DIR}"
        fi
    fi
    if [[ ${KEEP_TEMP} == 0 && ${EXPLICIT_OUTPUT_DIR} == 0 && ${status} == 0 ]]; then
        rm -rf -- "${WORK}"
    else
        printf 'AUTHORITATIVE_WORK: %s\n' "${WORK}"
    fi
    trap - EXIT HUP INT TERM
    exit "${status}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

PACKAGE_ROOT=${WORK}/app-test/phase2-pgo-use-fixture
EBUILD=${PACKAGE_ROOT}/phase2-pgo-use-fixture-1.ebuild
CONFIG_ROOT=${WORK}/config-root
PORTAGE_ROOT=${CONFIG_ROOT}/etc/portage
PORTAGE_TMP=${WORK}/portage-tmp
PORTAGE_LOG_DIR=${WORK}/portage-logs
PORTAGE_DEPCACHE_DIR=${WORK}/portage-depcache
CCACHE_CACHE_DIR=${WORK}/ccache
CCACHE_TEMP_DIR=${WORK}/ccache-tmp
SCCACHE_CACHE_DIR=${WORK}/sccache
DRIVER_HOME=${WORK}/driver-home
DRIVER_TMP=${WORK}/driver-tmp
XDG_CACHE_DIR=${WORK}/xdg-cache
XDG_CONFIG_DIR=${WORK}/xdg-config
XDG_STATE_DIR=${WORK}/xdg-state
BUILD_ROOT=${PORTAGE_TMP}/portage/app-test/phase2-pgo-use-fixture-1
FLAGS_FILE=${BUILD_ROOT}/temp/effective-flags.tsv
PROFILE_ROOT=${WORK}/profile
PERF_DATA=${WORK}/perf.data
PROFILE=${PROFILE_ROOT}/sample.prof
SAMPLE_METADATA=${PROFILE_ROOT}/sample-metadata.json
CONVERSION_LOG=${PROFILE_ROOT}/llvm-profgen-conversion-log.json
MANIFEST=${PROFILE_ROOT}/profile.manifest
SIDECAR=${MANIFEST}.metadata.json
GENERATED_ASSIGNMENT=${PORTAGE_ROOT}/package.env/zz-generated-sample-pgo
MAP_ENV=${PORTAGE_ROOT}/env/generated-sample-map.conf
USE_ENV=${PORTAGE_ROOT}/env/generated-sample-use.conf
VALIDATOR_PROXY=${WORK}/validator-proxy
VALIDATOR_IDENTITY=${WORK}/validator-identity.tsv
FRAMEWORK_LOCK=${WORK}/framework.lock
PROJECT_LOCK=${WORK}/project.lock
GENERATION_LOCK=${WORK}/generation.lock
GENERATION_ID=phase2-sample-portage-fixture-v1
INVENTORY_ID=phase2-sample-portage-inventory-v1
INVENTORY_SHA256=$(printf '%s' "${WORK}:inventory" | sha256sum | awk '{print $1}')
PRODUCTION_STATE_ROOT=
SEED_FINGERPRINT_FILE=
MAP_FINGERPRINT_FILE=
USE_FINGERPRINT_FILE=
TRANSACTION_AUTHORIZATION=
TRANSACTION_JOURNAL=
TRANSACTION_CHILD_IDENTITY=
TRANSACTION_JOURNAL_SHA256=
TRANSACTION_CHILD_IDENTITY_SHA256=
TRANSACTION_EXPECTED_PAYLOAD_SHA256=
TRANSACTION_FRAMEWORK_AGGREGATE_SHA256=
PROFILE_LOCK_ARGS=()
VALIDATOR_COMMAND=${VALIDATOR_PROXY}
HOST_PID_NAMESPACE=$(readlink -- /proc/self/ns/pid)
HOST_NETWORK_NAMESPACE=$(readlink -- /proc/self/ns/net)
HOST_IPC_NAMESPACE=$(readlink -- /proc/self/ns/ipc)
HOST_MOUNT_NAMESPACE=$(readlink -- /proc/self/ns/mnt)
POLICY_PROBE_ENV=${PORTAGE_ROOT}/env/sample-live-policy-probe.conf
 # Keep the probe under the runtime lock root, which is writable by the
 # Portage test group but intentionally absent from Portage's SANDBOX_WRITE
 # allow-list.  A probe under /var/tmp would be incorrectly permitted.
SANDBOX_DENY_DIRECTORY=/run/gentoo-optimization/sample-pgo-sandbox-policy-deny
SANDBOX_DENY_PATH=${SANDBOX_DENY_DIRECTORY}/forbidden-write
if ((PRODUCTION_LOCKS)); then
    GENERATION_ID=${PRODUCTION_GATE_GENERATION_ID}
    INVENTORY_ID=${PRODUCTION_GATE_INVENTORY_ID}
    INVENTORY_SHA256=${PRODUCTION_GATE_INVENTORY_SHA256}
    PRODUCTION_STATE_ROOT=/var/lib/gentoo-optimization/generations/${GENERATION_ID}/phase2-sample-gate-${PRODUCTION_GATE_RUN_ID}
    PROFILE_ROOT=/var/cache/gentoo-optimization/pgo/clang-sample/phase2-sample-gate-${PRODUCTION_GATE_RUN_ID}
    PERF_DATA=${PROFILE_ROOT}/perf.data
    PROFILE=${PROFILE_ROOT}/sample.prof
    SAMPLE_METADATA=${PROFILE_ROOT}/sample-metadata.json
    CONVERSION_LOG=${PROFILE_ROOT}/llvm-profgen-conversion-log.json
    MANIFEST=${PROFILE_ROOT}/profile.manifest
    SIDECAR=${MANIFEST}.metadata.json
    SEED_FINGERPRINT_FILE=${PRODUCTION_STATE_ROOT}/seed.fingerprint
    MAP_FINGERPRINT_FILE=${PRODUCTION_STATE_ROOT}/mapping.fingerprint
    USE_FINGERPRINT_FILE=${PRODUCTION_STATE_ROOT}/consumer.fingerprint
    TRANSACTION_AUTHORIZATION=${PRODUCTION_STATE_ROOT}/transaction.authorization
    TRANSACTION_JOURNAL=/var/lib/gentoo-optimization/state/profile-transactions/phase-2-production-profile-locks.pending
    TRANSACTION_CHILD_IDENTITY=${TRANSACTION_JOURNAL}.child.json
    VALIDATOR_COMMAND=${VALIDATOR}
fi
GENTOO_REPO=$(/usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
    PORTAGE_CONFIGROOT=/ /usr/bin/portageq get_repo_path / gentoo)
PROFILE_LINK=$(readlink -f -- /etc/portage/make.profile)
PORTAGE_GID=$(getent group portage | awk -F: '$1 == "portage" {print $3; exit}')
PORTAGE_UID=$(getent passwd portage | awk -F: '$1 == "portage" {print $3; exit}')

for value in "${GENTOO_REPO}" "${PROFILE_LINK}"; do
    [[ ${value} == /* && -d ${value} ]] || fail "invalid live Portage path: ${value}"
done
[[ ${PORTAGE_GID} =~ ^[1-9][0-9]*$ && ${PORTAGE_UID} =~ ^[1-9][0-9]*$ ]] || \
    fail 'cannot resolve the nonzero Portage user/group'
if [[ ${PORTAGE_POLICY_MODE} == live ]]; then
    resolve_live_portage_policy "${WORK}/live-policy-before.json"
fi
mkdir -p -- "${PACKAGE_ROOT}" "${WORK}/metadata" "${WORK}/profiles" \
    "${PORTAGE_ROOT}/env" "${PORTAGE_ROOT}/package.env" \
    "${PORTAGE_ROOT}/repos.conf" "${PORTAGE_TMP}" "${WORK}/distfiles" \
    "${WORK}/binpkgs" "${PORTAGE_LOG_DIR}" "${PORTAGE_DEPCACHE_DIR}" \
    "${CCACHE_CACHE_DIR}" "${CCACHE_TEMP_DIR}" "${SCCACHE_CACHE_DIR}" \
    "${DRIVER_HOME}" "${DRIVER_TMP}" "${XDG_CACHE_DIR}" \
    "${XDG_CONFIG_DIR}" "${XDG_STATE_DIR}"
chmod 0755 -- "${WORK}" "${WORK}/app-test" "${PACKAGE_ROOT}" \
    "${WORK}/metadata" "${WORK}/profiles" "${CONFIG_ROOT}" \
    "${CONFIG_ROOT}/etc" "${PORTAGE_ROOT}" "${PORTAGE_ROOT}/env" \
    "${PORTAGE_ROOT}/package.env" "${PORTAGE_ROOT}/repos.conf" \
    "${PORTAGE_TMP}" "${WORK}/distfiles" "${WORK}/binpkgs" \
    "${PORTAGE_LOG_DIR}" "${PORTAGE_DEPCACHE_DIR}" "${CCACHE_CACHE_DIR}" \
    "${CCACHE_TEMP_DIR}" "${SCCACHE_CACHE_DIR}" "${DRIVER_HOME}" \
    "${DRIVER_TMP}" "${XDG_CACHE_DIR}" "${XDG_CONFIG_DIR}" "${XDG_STATE_DIR}"
chown "0:${PORTAGE_GID}" -- "${PORTAGE_LOG_DIR}" "${PORTAGE_DEPCACHE_DIR}" \
    "${CCACHE_CACHE_DIR}" "${CCACHE_TEMP_DIR}" "${SCCACHE_CACHE_DIR}" \
    "${DRIVER_HOME}" "${DRIVER_TMP}" "${XDG_CACHE_DIR}" \
    "${XDG_CONFIG_DIR}" "${XDG_STATE_DIR}"
chmod 0770 -- "${PORTAGE_LOG_DIR}" "${PORTAGE_DEPCACHE_DIR}" \
    "${CCACHE_CACHE_DIR}" "${CCACHE_TEMP_DIR}" "${SCCACHE_CACHE_DIR}" \
    "${DRIVER_HOME}" "${DRIVER_TMP}" "${XDG_CACHE_DIR}" \
    "${XDG_CONFIG_DIR}" "${XDG_STATE_DIR}"
if ((PRODUCTION_LOCKS)); then
    [[ ${PRODUCTION_TRANSACTION_AUTHORIZATION} == "${TRANSACTION_AUTHORIZATION}" ]] || \
        fail 'coordinator authorization path differs from the exact gate namespace'
    [[ -d ${PRODUCTION_STATE_ROOT} && ! -L ${PRODUCTION_STATE_ROOT} && \
        -f ${TRANSACTION_AUTHORIZATION} && ! -L ${TRANSACTION_AUTHORIZATION} && \
        ! -e ${TRANSACTION_AUTHORIZATION}.partial && \
        ! -L ${TRANSACTION_AUTHORIZATION}.partial && \
        ! -e ${PROFILE_ROOT} && ! -L ${PROFILE_ROOT} ]] || \
        fail 'coordinator did not publish one exact production gate state root'
    [[ $(find "${PRODUCTION_STATE_ROOT}" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort) == $'coordinator-token-scan.tsv\ntransaction.authorization' ]] || \
        fail 'coordinator production gate state root contains unexpected entries'
    require_trusted_production_directory_chain \
        /var/lib/gentoo-optimization/generations || \
        fail 'production generation-state parent is not a trusted root-owned chain'
    if [[ -e ${PRODUCTION_STATE_ROOT%/*} || -L ${PRODUCTION_STATE_ROOT%/*} ]]; then
        require_trusted_production_directory_chain "${PRODUCTION_STATE_ROOT%/*}" || \
            fail 'existing production generation parent is not a trusted root-owned chain'
    fi
    require_trusted_production_directory_chain "${PROFILE_ROOT%/*}" || \
        fail 'production sample-profile parent is not a trusted root-owned chain'
    [[ $(realpath -e -- "${PRODUCTION_STATE_ROOT}") == "${PRODUCTION_STATE_ROOT}" && \
        $(stat -c '%u:%g:%a' -- "${PRODUCTION_STATE_ROOT}") == 0:0:755 ]] || \
        fail 'coordinator production gate state root has an unsafe identity'
    PRODUCTION_ROOTS_CREATED=1
    install -d -o 0 -g "${PORTAGE_GID}" -m 0750 -- "${PROFILE_ROOT}"
    [[ $(realpath -e -- "${PRODUCTION_STATE_ROOT}") == "${PRODUCTION_STATE_ROOT}" && \
        $(stat -c '%u:%g:%a' -- "${PRODUCTION_STATE_ROOT}") == 0:0:755 && \
        $(realpath -e -- "${PROFILE_ROOT}") == "${PROFILE_ROOT}" && \
        $(stat -c '%u:%g:%a' -- "${PROFILE_ROOT}") == \
        "0:${PORTAGE_GID}:750" ]] || \
        fail 'production sample gate roots have an unsafe canonical identity'
    if ! require_trusted_production_directory_chain "${PRODUCTION_STATE_ROOT}" || \
        ! require_trusted_production_directory_chain "${PROFILE_ROOT}"; then
        fail 'production sample gate root ancestry is not immutable root-owned state'
    fi
    sync -f -- "${PRODUCTION_STATE_ROOT}" "${PRODUCTION_STATE_ROOT%/*}" \
        /var/lib/gentoo-optimization/generations
    sync -f -- "${PROFILE_ROOT}" "${PROFILE_ROOT%/*}"
    write_production_gate_status in-progress - || \
        fail 'cannot publish the durable production sample-gate in-progress marker'
    TRANSACTION_TOKEN_SHA=$(printf '%s' \
        "${PRODUCTION_TRANSACTION_TOKEN}" | sha256sum)
    TRANSACTION_TOKEN_SHA=${TRANSACTION_TOKEN_SHA%% *}
    [[ -f ${TRANSACTION_JOURNAL} && ! -L ${TRANSACTION_JOURNAL} && \
        $(stat -c '%u:%g:%a:%h' -- "${TRANSACTION_JOURNAL}") == \
        "0:${PORTAGE_GID}:640:1" ]] || \
        fail 'production transaction journal has unsafe metadata'
    [[ -f ${TRANSACTION_CHILD_IDENTITY} && ! -L ${TRANSACTION_CHILD_IDENTITY} && \
        $(stat -c '%u:%g:%a:%h' -- "${TRANSACTION_CHILD_IDENTITY}") == \
        "0:${PORTAGE_GID}:640:1" ]] || \
        fail 'production transaction child identity has unsafe metadata'
    TRANSACTION_JOURNAL_IDENTITY_BEFORE=$(stat -c \
        '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${TRANSACTION_JOURNAL}")
    TRANSACTION_CHILD_IDENTITY_BEFORE=$(stat -c \
        '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${TRANSACTION_CHILD_IDENTITY}")
    TRANSACTION_JOURNAL_SHA256=$(sha256sum -- "${TRANSACTION_JOURNAL}")
    TRANSACTION_JOURNAL_SHA256=${TRANSACTION_JOURNAL_SHA256%% *}
    TRANSACTION_CHILD_IDENTITY_SHA256=$(sha256sum -- "${TRANSACTION_CHILD_IDENTITY}")
    TRANSACTION_CHILD_IDENTITY_SHA256=${TRANSACTION_CHILD_IDENTITY_SHA256%% *}
    TRANSACTION_EXPECTED_PAYLOAD_SHA256=$(
        printf '{\n  "generation_id": "%s",\n  "inventory_id": "%s",\n  "inventory_sha256": "%s"\n}\n' \
            "${GENERATION_ID}" "${INVENTORY_ID}" "${INVENTORY_SHA256}" | \
            sha256sum | awk '{print $1}'
    )
    TRANSACTION_FRAMEWORK_AGGREGATE_SHA256=$(awk -F= \
        '$1 == "framework_aggregate_sha256" { print substr($0, index($0, "=") + 1) }' \
        "${FRAMEWORK_TARGET}/install.manifest")
    [[ ${TRANSACTION_FRAMEWORK_AGGREGATE_SHA256} =~ ^[0-9a-f]{64}$ && \
        $(grep -c '^framework_aggregate_sha256=' \
            "${FRAMEWORK_TARGET}/install.manifest") == 1 ]] || \
        fail 'active framework manifest lacks one exact aggregate identity'
    jq -e \
        --arg run "${PRODUCTION_GATE_RUN_ID}" \
        --arg generation "${GENERATION_ID}" \
        --arg inventory "${INVENTORY_ID}" \
        --arg inventory_sha "${INVENTORY_SHA256}" \
        --arg token_sha "${TRANSACTION_TOKEN_SHA}" \
        --arg payload_sha "${TRANSACTION_EXPECTED_PAYLOAD_SHA256}" \
        --arg framework_sha "${TRANSACTION_FRAMEWORK_AGGREGATE_SHA256}" '
        .schema == "gentoo-optimization-production-profile-lock-transaction-v1" and
        .test_mode == false and .gate_run_id == $run and
        .generation == {
            generation_id: $generation,
            inventory_id: $inventory,
            inventory_sha256: $inventory_sha
        } and
        .authorization_token_sha256 == $token_sha and
        .expected_payload_sha256 == $payload_sha and
        .framework_context.framework_aggregate_sha256 == $framework_sha
    ' "${TRANSACTION_JOURNAL}" >/dev/null || \
        fail 'production transaction journal differs from the requested gate identity'
    jq -e \
        --arg journal_sha "${TRANSACTION_JOURNAL_SHA256}" \
        --arg run "${PRODUCTION_GATE_RUN_ID}" \
        --arg token_sha "${TRANSACTION_TOKEN_SHA}" '
        .schema == "gentoo-optimization-production-profile-lock-child-identity-v1" and
        .test_mode == false and .gate_run_id == $run and
        .journal_sha256 == $journal_sha and
        .authorization_token_sha256 == $token_sha and
        .child.pid == .child.process_group
    ' "${TRANSACTION_CHILD_IDENTITY}" >/dev/null || \
        fail 'production transaction child identity differs from its journal or gate'
    [[ ${TRANSACTION_JOURNAL_IDENTITY_BEFORE} == "$(stat -c \
            '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${TRANSACTION_JOURNAL}")" && \
        ${TRANSACTION_CHILD_IDENTITY_BEFORE} == "$(stat -c \
            '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${TRANSACTION_CHILD_IDENTITY}")" && \
        ${TRANSACTION_JOURNAL_SHA256} == "$(sha256sum -- \
            "${TRANSACTION_JOURNAL}" | awk '{print $1}')" && \
        ${TRANSACTION_CHILD_IDENTITY_SHA256} == "$(sha256sum -- \
            "${TRANSACTION_CHILD_IDENTITY}" | awk '{print $1}')" ]] || \
        fail 'production transaction journal or child identity changed during authorization'
    [[ $(stat -c '%u:%g:%a:%h' -- "${TRANSACTION_AUTHORIZATION}") == \
        "0:${PORTAGE_GID}:640:1" ]] || \
        fail 'production transaction authorization has unsafe metadata'
    cmp -s -- "${TRANSACTION_AUTHORIZATION}" <(
        printf 'schema\tgentoo-optimization-production-profile-authorization-v1\n'
        printf 'generation_id\t%s\n' "${GENERATION_ID}"
        printf 'expected_payload_sha256\t%s\n' \
            "${TRANSACTION_EXPECTED_PAYLOAD_SHA256}"
        printf 'journal_sha256\t%s\n' "${TRANSACTION_JOURNAL_SHA256}"
        printf 'framework_aggregate_sha256\t%s\n' \
            "${TRANSACTION_FRAMEWORK_AGGREGATE_SHA256}"
        printf 'authorization_token_sha256\t%s\n' "${TRANSACTION_TOKEN_SHA}"
        printf 'child_identity_sha256\t%s\n' \
            "${TRANSACTION_CHILD_IDENTITY_SHA256}"
    ) || fail 'coordinator transaction authorization payload is not exact'
    production_authorized_command \
        "${INSTALLER}" --source-root "${ROOT}" --check >/dev/null || \
        fail 'installed framework differs from the reviewed repository source'
else
    mkdir -- "${PROFILE_ROOT}"
    chown "0:${PORTAGE_GID}" -- "${PROFILE_ROOT}"
    chmod 0750 -- "${PROFILE_ROOT}"
fi
cp -- "${TEMPLATE}" "${EBUILD}"
cp --dereference -- /etc/portage/bashrc "${PORTAGE_ROOT}/bashrc"
grep -Fxq "gentoo_opt_embedded_framework_target=${FRAMEWORK_TARGET}" \
    "${PORTAGE_ROOT}/bashrc" || \
    fail 'copied Portage dispatcher is not bound to the selected exact framework target'
ln -s -- "${PROFILE_LINK}" "${PORTAGE_ROOT}/make.profile"
printf '%s\n' 'masters = gentoo' > "${WORK}/metadata/layout.conf"
printf '%s\n' phase2-sample-pgo-fixture > "${WORK}/profiles/repo_name"
printf '%s\n' app-test > "${WORK}/profiles/categories"
PORTAGE_FEATURES_ASSIGNMENT='userpriv -ccache -distcc -icecream -sandbox -usersandbox -network-sandbox -pid-sandbox -ipc-sandbox nostrip'
PORTAGE_FRESHNESS_ASSIGNMENTS=()
if [[ ${PORTAGE_POLICY_MODE} == live ]]; then
    PORTAGE_FEATURES_ASSIGNMENT=${LIVE_PORTAGE_FEATURES}
    PORTAGE_FRESHNESS_ASSIGNMENTS+=('CCACHE_RECACHE="1"')
fi
printf '%s\n' \
    '[gentoo]' \
    "location = ${GENTOO_REPO}" \
    '' \
    '[phase2-sample-pgo-fixture]' \
    "location = ${WORK}" \
    'masters = gentoo' \
    > "${PORTAGE_ROOT}/repos.conf/repos.conf"

CLANG=$(readlink -f -- "${CLANG_LINK}")
CLANGXX=$(readlink -f -- "${CLANGXX_LINK}")
PROFGEN=$(readlink -f -- "${PROFGEN_LINK}")
PROFDATA=$(readlink -f -- "${PROFDATA_LINK}")
LLVM_READELF=$(readlink -f -- "${READELF_LINK}")
LLVM_OBJCOPY=$(readlink -f -- "${OBJCOPY_LINK}")
for tool in "${CLANG}" "${CLANGXX}" "${PROFGEN}" "${PROFDATA}" \
    "${LLVM_READELF}" "${LLVM_OBJCOPY}"; do
    [[ -f ${tool} && -x ${tool} && ! -L ${tool} ]] || \
        fail "LLVM tool did not resolve to a regular executable: ${tool}"
done

printf '%s\n' \
    "CC=\"${CLANG}\"" \
    "CXX=\"${CLANGXX}\"" \
    'CHOST="x86_64-pc-linux-gnu"' \
    'ABI="amd64"' \
    'CFLAGS="-O2 -pipe -fno-omit-frame-pointer"' \
    'CXXFLAGS="-O2 -pipe -fno-omit-frame-pointer"' \
    'FCFLAGS="-O2 -pipe"' \
    'FFLAGS="-O2 -pipe"' \
    'LDFLAGS="-fuse-ld=lld"' \
    'RUSTFLAGS=""' \
    "FEATURES=\"${PORTAGE_FEATURES_ASSIGNMENT}\"" \
    "${PORTAGE_FRESHNESS_ASSIGNMENTS[@]}" \
    'ACCEPT_KEYWORDS="**"' \
    'MAKEOPTS="-j2"' \
    "PORTAGE_TMPDIR=\"${PORTAGE_TMP}\"" \
    "PORTAGE_LOGDIR=\"${PORTAGE_LOG_DIR}\"" \
    "PORTAGE_DEPCACHEDIR=\"${PORTAGE_DEPCACHE_DIR}\"" \
    "DISTDIR=\"${WORK}/distfiles\"" \
    "PKGDIR=\"${WORK}/binpkgs\"" \
    "CCACHE_DIR=\"${CCACHE_CACHE_DIR}\"" \
    "CCACHE_TEMPDIR=\"${CCACHE_TEMP_DIR}\"" \
    "SCCACHE_DIR=\"${SCCACHE_CACHE_DIR}\"" \
    "XDG_CACHE_HOME=\"${XDG_CACHE_DIR}\"" \
    "XDG_CONFIG_HOME=\"${XDG_CONFIG_DIR}\"" \
    "XDG_STATE_HOME=\"${XDG_STATE_DIR}\"" \
    'PORTAGE_ELOG_CLASSES="log warn error qa"' \
    'PORTAGE_ELOG_SYSTEM="save"' \
    'PORTAGE_ELOG_MAILURI=""' \
    > "${PORTAGE_ROOT}/make.conf"

{
    printf 'schema\tgentoo-optimization-sample-portage-policy-v1\n'
    printf 'selected_policy\t%s\n' "${PORTAGE_POLICY_MODE}"
    printf 'configured_features\t%s\n' "${PORTAGE_FEATURES_ASSIGNMENT}"
    printf 'host_pid_namespace\t%s\n' "${HOST_PID_NAMESPACE}"
    printf 'host_network_namespace\t%s\n' "${HOST_NETWORK_NAMESPACE}"
    printf 'host_ipc_namespace\t%s\n' "${HOST_IPC_NAMESPACE}"
    printf 'host_mount_namespace\t%s\n' "${HOST_MOUNT_NAMESPACE}"
    if [[ ${PORTAGE_POLICY_MODE} == live ]]; then
        printf 'live_make_conf\t%s\n' "${LIVE_MAKE_CONF}"
        printf 'live_make_conf_sha256\t%s\n' "${LIVE_MAKE_CONF_SHA256}"
        printf 'live_resolved_features\t%s\n' "${LIVE_PORTAGE_FEATURES}"
        printf 'live_policy_identity_sha256\t%s\n' \
            "${LIVE_POLICY_IDENTITY_SHA256}"
        printf 'disposable_portage_logdir\t%s\n' "${PORTAGE_LOG_DIR}"
        printf 'disposable_portage_depcachedir\t%s\n' "${PORTAGE_DEPCACHE_DIR}"
        printf 'disposable_ccache_dir\t%s\n' "${CCACHE_CACHE_DIR}"
        printf 'disposable_sccache_dir\t%s\n' "${SCCACHE_CACHE_DIR}"
        printf 'disposable_xdg_cache_home\t%s\n' "${XDG_CACHE_DIR}"
        printf 'disposable_xdg_config_home\t%s\n' "${XDG_CONFIG_DIR}"
        printf 'disposable_xdg_state_home\t%s\n' "${XDG_STATE_DIR}"
        printf 'protected_live_ccache_dir\t%s\n' "${LIVE_CCACHE_DIR}"
        printf 'protected_live_ccache_tempdir\t%s\n' "${LIVE_CCACHE_TEMPDIR}"
        printf 'protected_live_ccache_tempdir_source\t%s\n' \
            "${LIVE_CCACHE_TEMPDIR_SOURCE}"
        printf 'protected_live_sccache_dir\t%s\n' "${LIVE_SCCACHE_DIR}"
        printf 'protected_live_sccache_dir_source\t%s\n' \
            "${LIVE_SCCACHE_DIR_SOURCE}"
        printf 'sandbox_exceptions\tnone\n'
        printf 'stage_feature_exception\t-ccache,-distcc,-icecream-only-during-profile-map-and-sample-use-by-reviewed-dispatcher\n'
        printf 'fresh_compile_policy\tabsolute-clang-plus-CCACHE_RECACHE=1\n'
        printf 'diagnostic_lane\tseparate;not-authoritative-for-live-policy\n'
    else
        printf 'live_make_conf\tnot-applicable\n'
        printf 'live_make_conf_sha256\tnot-applicable\n'
        printf 'live_resolved_features\tnot-applicable\n'
        printf 'sandbox_exceptions\tdiagnostic-lane-disables-sandbox-usersandbox-network-pid-ipc\n'
        printf 'stage_feature_exception\tnot-applicable\n'
        printf 'fresh_compile_policy\tabsolute-clang\n'
        printf 'diagnostic_lane\tfault-localization-only\n'
    fi
} > "${WORK}/portage-policy.tsv"
chmod 0644 -- "${EBUILD}" "${PORTAGE_ROOT}/bashrc" \
    "${PORTAGE_ROOT}/make.conf" "${PORTAGE_ROOT}/repos.conf/repos.conf" \
    "${WORK}/metadata/layout.conf" "${WORK}/profiles/repo_name" \
    "${WORK}/profiles/categories"
printf 'EBUILD %s %s BLAKE2B %s SHA512 %s\n' \
    "$(basename -- "${EBUILD}")" "$(stat -c %s -- "${EBUILD}")" \
    "$(b2sum -- "${EBUILD}" | awk '{print $1}')" \
    "$(sha512sum -- "${EBUILD}" | awk '{print $1}')" \
    > "${PACKAGE_ROOT}/Manifest"
chmod 0644 -- "${PACKAGE_ROOT}/Manifest"

if ((PRODUCTION_LOCKS == 0)); then
    python3 - "${PROJECT_LOCK}" "${GENERATION_LOCK}" "${GENERATION_ID}" \
        "${INVENTORY_ID}" "${INVENTORY_SHA256}" <<'PY'
import json
import pathlib
import sys

payload = json.dumps(
    {
        "generation_id": sys.argv[3],
        "inventory_id": sys.argv[4],
        "inventory_sha256": sys.argv[5],
    },
    indent=2,
    sort_keys=True,
) + "\n"
for path in map(pathlib.Path, sys.argv[1:3]):
    path.write_text(payload, encoding="utf-8")
PY
    : > "${FRAMEWORK_LOCK}"
    chown "0:${PORTAGE_GID}" -- \
        "${FRAMEWORK_LOCK}" "${PROJECT_LOCK}" "${GENERATION_LOCK}"
    chmod 0640 -- "${FRAMEWORK_LOCK}" "${PROJECT_LOCK}" "${GENERATION_LOCK}"
    : > "${VALIDATOR_IDENTITY}"
    chown "0:${PORTAGE_GID}" -- "${VALIDATOR_IDENTITY}"
    chmod 0660 -- "${VALIDATOR_IDENTITY}"
    printf '%s\n' \
        '#!/bin/sh' \
        "printf '%s\\t%s\\t%s\\t%s\\n' \"\${1-}\" \"\$(/usr/bin/id -u)\" \"\$(/usr/bin/id -g)\" \"\${EBUILD_PHASE-}\" >> '${VALIDATOR_IDENTITY}' || exit 1" \
        "exec '${VALIDATOR}' \"\$@\" --test-mode --test-framework-lock '${FRAMEWORK_LOCK}' --test-project-lock '${PROJECT_LOCK}' --test-generation-lock '${GENERATION_LOCK}'" \
        > "${VALIDATOR_PROXY}"
    chmod 0755 -- "${VALIDATOR_PROXY}"
    PROFILE_LOCK_ARGS=(
        --test-mode
        --test-framework-lock "${FRAMEWORK_LOCK}"
        --test-project-lock "${PROJECT_LOCK}"
        --test-generation-lock "${GENERATION_LOCK}"
    )
    for lock in "${FRAMEWORK_LOCK}" "${PROJECT_LOCK}" "${GENERATION_LOCK}"; do
        [[ $(stat -c '%u:%g:%a:%h' -- "${lock}") == "0:${PORTAGE_GID}:640:1" ]] || \
            fail "fixture lock has an unsafe identity: ${lock}"
        runuser -u portage -- test -r "${lock}" || \
            fail "Portage cannot read fixture lock: ${lock}"
    done
else
    : > "${VALIDATOR_IDENTITY}"
    chown "0:${PORTAGE_GID}" -- "${VALIDATOR_IDENTITY}"
    chmod 0440 -- "${VALIDATOR_IDENTITY}"
    printf '%s\n' \
        $'mode\tproduction-locks' \
        $'profile_helper_lock_arguments\t0' \
        $'profile_validator\t/usr/local/libexec/gentoo-optimization/pgo/validate-profile.py' \
        > "${VALIDATOR_IDENTITY}.partial"
    chown "0:${PORTAGE_GID}" -- "${VALIDATOR_IDENTITY}.partial"
    chmod 0440 -- "${VALIDATOR_IDENTITY}.partial"
    mv -- "${VALIDATOR_IDENTITY}.partial" "${VALIDATOR_IDENTITY}"
    ((${#PROFILE_LOCK_ARGS[@]} == 0)) || \
        fail 'production mode unexpectedly constructed substituted lock arguments'
fi

publish_production_fingerprint() {
    local fingerprint=$1 destination=$2
    local partial=${destination}.partial
    ((PRODUCTION_LOCKS)) || return 0
    [[ ${fingerprint} =~ ^[0-9a-f]{64}$ && ${destination} == "${PRODUCTION_STATE_ROOT}"/* ]] || \
        fail 'invalid production fingerprint publication request'
    [[ ! -e ${destination} && ! -L ${destination} && \
        ! -e ${partial} && ! -L ${partial} ]] || \
        fail "production fingerprint destination is not new: ${destination}"
    printf 'fingerprint=%s\n' "${fingerprint}" > "${partial}"
    chown "0:${PORTAGE_GID}" -- "${partial}"
    chmod 0640 -- "${partial}"
    sync -f -- "${partial}"
    mv -- "${partial}" "${destination}"
    sync -f -- "${PRODUCTION_STATE_ROOT}"
}

write_map_environment() {
    local fingerprint=$1 fingerprint_file=${2:-} output=${MAP_ENV}.partial
    local -a identity_lines=() freshness_lines=()
    [[ ${PORTAGE_POLICY_MODE} == live ]] && \
        freshness_lines+=('CCACHE_RECACHE="1"')
    if ((PRODUCTION_LOCKS)); then
        [[ ${fingerprint_file} == "${PRODUCTION_STATE_ROOT}"/* ]] || \
            fail 'production map environment lacks its root-owned fingerprint file'
        identity_lines+=("GENTOO_OPT_FINGERPRINT_FILE=\"${fingerprint_file}\"")
    else
        identity_lines+=(
            'GENTOO_OPT_PORTAGE_FIXTURE_MODE="1"'
            "GENTOO_OPT_FINGERPRINT=\"${fingerprint}\""
        )
    fi
    printf '%s\n' \
        "${identity_lines[@]}" \
        'GENTOO_OPT_MODE="off"' \
        'GENTOO_OPT_PROFILE_MAP_READY="1"' \
        'GENTOO_OPT_COMPILER_FAMILY="clang"' \
        'GENTOO_OPT_ABI="amd64"' \
        "${freshness_lines[@]}" \
        > "${output}"
    chmod 0644 -- "${output}"
    mv -- "${output}" "${MAP_ENV}"
    printf '%s\n' '=app-test/phase2-pgo-use-fixture-1 generated-sample-map.conf' \
        > "${GENERATED_ASSIGNMENT}.partial"
    chmod 0644 -- "${GENERATED_ASSIGNMENT}.partial"
    mv -- "${GENERATED_ASSIGNMENT}.partial" "${GENERATED_ASSIGNMENT}"
}

write_use_environment() {
    local fingerprint=$1 fingerprint_file=${2:-} output=${USE_ENV}.partial
    local -a identity_lines=() validator_lines=() freshness_lines=()
    [[ ${PORTAGE_POLICY_MODE} == live ]] && \
        freshness_lines+=('CCACHE_RECACHE="1"')
    if ((PRODUCTION_LOCKS)); then
        [[ ${fingerprint_file} == "${PRODUCTION_STATE_ROOT}"/* ]] || \
            fail 'production use environment lacks its root-owned fingerprint file'
        identity_lines+=("GENTOO_OPT_FINGERPRINT_FILE=\"${fingerprint_file}\"")
    else
        identity_lines+=(
            'GENTOO_OPT_PORTAGE_FIXTURE_MODE="1"'
            "GENTOO_OPT_FINGERPRINT=\"${fingerprint}\""
        )
        validator_lines+=("GENTOO_OPT_PROFILE_VALIDATOR=\"${VALIDATOR_PROXY}\"")
    fi
    printf '%s\n' \
        "${identity_lines[@]}" \
        'GENTOO_OPT_MODE="clang-sample-use"' \
        'GENTOO_OPT_PROFILE_MAP_READY="0"' \
        'GENTOO_OPT_COMPILER_FAMILY="clang"' \
        'GENTOO_OPT_ABI="amd64"' \
        "GENTOO_OPT_PROFILE_PATH=\"${PROFILE}\"" \
        "GENTOO_OPT_PROFILE_MANIFEST=\"${MANIFEST}\"" \
        "GENTOO_OPT_PROFILE_METADATA=\"${SIDECAR}\"" \
        "${validator_lines[@]}" \
        "${freshness_lines[@]}" \
        > "${output}"
    chmod 0644 -- "${output}"
    mv -- "${output}" "${USE_ENV}"
    printf '%s\n' '=app-test/phase2-pgo-use-fixture-1 generated-sample-use.conf' \
        > "${GENERATED_ASSIGNMENT}.partial"
    chmod 0644 -- "${GENERATED_ASSIGNMENT}.partial"
    mv -- "${GENERATED_ASSIGNMENT}.partial" "${GENERATED_ASSIGNMENT}"
}

write_use_probe_environment() {
    local output=${USE_ENV}.partial
    local -a fixture_lines=() freshness_lines=()
    ((PRODUCTION_LOCKS)) || fixture_lines+=('GENTOO_OPT_PORTAGE_FIXTURE_MODE="1"')
    [[ ${PORTAGE_POLICY_MODE} == live ]] && \
        freshness_lines+=('CCACHE_RECACHE="1"')
    printf '%s\n' \
        "${fixture_lines[@]}" \
        'GENTOO_OPT_MODE="off"' \
        'GENTOO_OPT_PROFILE_MAP_READY="0"' \
        'GENTOO_OPT_COMPILER_FAMILY="clang"' \
        'GENTOO_OPT_ABI="amd64"' \
        "${freshness_lines[@]}" \
        > "${output}"
    chmod 0644 -- "${output}"
    mv -- "${output}" "${USE_ENV}"
    printf '%s\n' '=app-test/phase2-pgo-use-fixture-1 generated-sample-use.conf' \
        > "${GENERATED_ASSIGNMENT}.partial"
    chmod 0644 -- "${GENERATED_ASSIGNMENT}.partial"
    mv -- "${GENERATED_ASSIGNMENT}.partial" "${GENERATED_ASSIGNMENT}"
}

run_ebuild() {
    local log=$1
    shift
    if ((PRODUCTION_LOCKS)); then
        production_authorized_command \
            "HOME=${DRIVER_HOME}" "TMPDIR=${DRIVER_TMP}" \
            "XDG_CACHE_HOME=${XDG_CACHE_DIR}" \
            "XDG_CONFIG_HOME=${XDG_CONFIG_DIR}" \
            "XDG_STATE_HOME=${XDG_STATE_DIR}" \
            "PORTAGE_CONFIGROOT=${CONFIG_ROOT}" NOCOLOR=true \
            /usr/bin/ebuild --color n "${EBUILD}" "$@" > "${log}" 2>&1
    else
        /usr/bin/env -i "HOME=${DRIVER_HOME}" USER=root LOGNAME=root SHELL=/bin/bash \
            PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
            LANG=C LC_ALL=C TZ=UTC "TMPDIR=${DRIVER_TMP}" \
            "XDG_CACHE_HOME=${XDG_CACHE_DIR}" "XDG_CONFIG_HOME=${XDG_CONFIG_DIR}" \
            "XDG_STATE_HOME=${XDG_STATE_DIR}" PORTAGE_CONFIGROOT="${CONFIG_ROOT}" \
            "FEATURES=${PORTAGE_FEATURES_ASSIGNMENT}" NOCOLOR=true \
            /usr/bin/ebuild --color n "${EBUILD}" "$@" \
            > "${log}" 2>&1
    fi
}

run_ebuild_with_private_sidecar_bind() {
    local log=$1 substitute=$2
    local unshare_tool mount_tool bash_tool ebuild_tool
    ((PRODUCTION_LOCKS)) || return 1
    unshare_tool=$(command -v unshare) || return 1
    mount_tool=$(command -v mount) || return 1
    bash_tool=$(command -v bash) || return 1
    ebuild_tool=$(command -v ebuild) || return 1
    # shellcheck disable=SC2016  # Positional parameters expand in the child shell.
    production_authorized_command \
        "HOME=${DRIVER_HOME}" "TMPDIR=${DRIVER_TMP}" \
        "XDG_CACHE_HOME=${XDG_CACHE_DIR}" \
        "XDG_CONFIG_HOME=${XDG_CONFIG_DIR}" \
        "XDG_STATE_HOME=${XDG_STATE_DIR}" \
        "${unshare_tool}" --mount --propagation private -- \
        "${bash_tool}" -Eeuo pipefail -c '
            substitute=$1
            canonical=$2
            config_root=$3
            ebuild_path=$4
            mount_tool=$5
            ebuild_tool=$6
            "${mount_tool}" --bind "${substitute}" "${canonical}"
            export PORTAGE_CONFIGROOT="${config_root}" NOCOLOR=true
            exec "${ebuild_tool}" --color n "${ebuild_path}" compile
        ' bash "${substitute}" "${SIDECAR}" "${CONFIG_ROOT}" "${EBUILD}" \
        "${mount_tool}" "${ebuild_tool}" > "${log}" 2>&1
}

assert_no_persisted_authorization_token() {
    local status
    local output=${WORK}/authorization-token-persistence-scan.tsv
    ((PRODUCTION_LOCKS)) || return 0
    set +e
    printf '%s\n' "${PRODUCTION_TRANSACTION_TOKEN}" | \
        PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I \
        "${AUTHORIZATION_TOKEN_SCANNER}" --token-fd 0 --output "${output}" \
        "${WORK}" "${PROFILE_ROOT}" "${PRODUCTION_STATE_ROOT}"
    status=$?
    set -e
    ((status == 0)) || return "${status}"
    grep -Fxq $'passed\t-' "${output}" || return 1
}

publish_mapping_input() {
    local source=$1 destination=$2 partial=${2}.partial.$$
    [[ -f ${source} && ! -L ${source} && ! -e ${destination} && \
        ! -L ${destination} && ! -e ${partial} && ! -L ${partial} ]] || \
        return 1
    if ! cp --reflink=auto -- "${source}" "${partial}" || \
        ! chown "0:${PORTAGE_GID}" -- "${partial}" || \
        ! chmod 0550 -- "${partial}" || ! sync -f -- "${partial}" || \
        ! cmp -s -- "${source}" "${partial}" || \
        ! mv -T -- "${partial}" "${destination}" || \
        ! sync -f -- "${destination%/*}"; then
        rm -f -- "${partial}"
        return 1
    fi
}

field() {
    local key=$1 path=$2
    awk -F '\t' -v key="${key}" '$1 == key {sub($1 FS, ""); print; exit}' "${path}"
}

capture_protected_live_state() {
    local output=$1
    [[ ${PORTAGE_POLICY_MODE} == live ]] || return 0
    /usr/bin/python3 -I - "${output}" /var/db/pkg /var/cache/edb \
        /var/log/portage "${LIVE_PORTAGE_DEPCACHEDIR}" \
        "${LIVE_PORTAGE_LOGDIR}" "${LIVE_DISTDIR}" "${LIVE_PKGDIR}" \
        "${LIVE_CCACHE_DIR}" "${LIVE_CCACHE_TEMPDIR}" \
        "${LIVE_SCCACHE_DIR}" <<'PY'
import hashlib
import json
import os
import pathlib
import stat
import sys

output = pathlib.Path(sys.argv[1])
requested = [pathlib.Path(raw) for raw in sys.argv[2:] if raw]
roots: list[pathlib.Path] = []
for requested_root in requested:
    normalized = pathlib.Path(os.path.normpath(requested_root.as_posix()))
    if not normalized.is_absolute():
        raise SystemExit(f"protected Portage root is not absolute: {normalized}")
    if any(root == normalized or root in normalized.parents for root in roots):
        continue
    roots = [root for root in roots if normalized not in root.parents]
    roots.append(normalized)

def metadata(info: os.stat_result) -> dict[str, int]:
    return {
        "ctime_ns": info.st_ctime_ns,
        "device": info.st_dev,
        "gid": info.st_gid,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "mtime_ns": info.st_mtime_ns,
        "nlink": info.st_nlink,
        "size": info.st_size,
        "uid": info.st_uid,
    }

records = []
for root in sorted(roots, key=lambda item: item.as_posix()):
    if not root.exists() and not root.is_symlink():
        records.append({
            "aggregate_sha256": None,
            "kind": "absent",
            "node_count": 0,
            "path": root.as_posix(),
        })
        continue
    root_info = root.lstat()
    root_device = root_info.st_dev
    aggregate = hashlib.sha256()
    node_count = 0
    pending = [root]
    while pending:
        node = pending.pop()
        info = node.lstat()
        if stat.S_ISREG(info.st_mode):
            kind, target = "regular", None
        elif stat.S_ISDIR(info.st_mode):
            kind, target = "directory", None
        elif stat.S_ISLNK(info.st_mode):
            kind, target = "symlink", os.readlink(node)
        else:
            kind, target = "special", None
        relative = "." if node == root else node.relative_to(root).as_posix()
        identity = {
            "kind": kind,
            "metadata": metadata(info),
            "path": relative,
            "symlink_target": target,
        }
        aggregate.update(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        aggregate.update(b"\n")
        node_count += 1
        if kind == "directory" and info.st_dev == root_device:
            children = sorted(
                (pathlib.Path(entry.path) for entry in os.scandir(node)),
                key=lambda item: item.name,
                reverse=True,
            )
            pending.extend(children)
    records.append({
        "aggregate_sha256": aggregate.hexdigest(),
        "kind": "tree",
        "node_count": node_count,
        "path": root.as_posix(),
    })
output.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

assert_production_write_allowlist() {
    local actual expected
    ((PRODUCTION_LOCKS)) || return 0
    expected=$'gate-status.tsv\nllvm-profgen-conversion-log.json\nmapping-input\nperf.data\nprofile.manifest\nprofile.manifest.metadata.json\nsample-metadata.json\nsample.prof'
    actual=$(find "${PROFILE_ROOT}" -mindepth 1 -maxdepth 1 -type f \
        -printf '%f\n' | LC_ALL=C sort)
    [[ ${actual} == "${expected}" ]] || \
        fail 'production profile root differs from its exact write allowlist'
    [[ -z $(find "${PROFILE_ROOT}" -mindepth 1 -maxdepth 1 ! -type f -print -quit) ]] || \
        fail 'production profile root contains a non-file outside its allowlist'
    expected=$'consumer.fingerprint\ngate-status.tsv\nmapping.fingerprint\nseed.fingerprint\ntransaction.authorization'
    actual=$(find "${PRODUCTION_STATE_ROOT}" -mindepth 1 -maxdepth 1 -type f \
        -printf '%f\n' | LC_ALL=C sort)
    [[ ${actual} == "${expected}" ]] || \
        fail 'production generation-state root differs from its exact write allowlist'
    [[ -z $(find "${PRODUCTION_STATE_ROOT}" -mindepth 1 -maxdepth 1 \
        ! -type f -print -quit) ]] || \
        fail 'production generation-state root contains a non-file outside its allowlist'
}

assert_live_policy_receipt() {
    local receipt=$1 label=$2 expected_policy=${3:-baseline}
    python3 - "${receipt}" "${label}" "${PORTAGE_UID}" "${PORTAGE_GID}" \
        "${HOST_PID_NAMESPACE}" "${HOST_NETWORK_NAMESPACE}" \
        "${HOST_IPC_NAMESPACE}" "${HOST_MOUNT_NAMESPACE}" \
        "${CLANG}" "${CLANGXX}" "${LIVE_PORTAGE_FEATURES}" \
        "${expected_policy}" <<'PY'
import pathlib
import sys

(
    receipt_path,
    label,
    expected_uid,
    expected_gid,
    host_pid,
    host_network,
    host_ipc,
    host_mount,
    expected_compiler,
    expected_cxx,
    baseline_features,
    expected_policy,
) = sys.argv[1:]
values: dict[str, str] = {}
for line in pathlib.Path(receipt_path).read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("\t")
    if not separator or key in values:
        raise SystemExit(f"{label}: invalid or duplicate receipt row: {line!r}")
    values[key] = value

required_features = {
    "sandbox",
    "usersandbox",
    "userpriv",
    "mount-sandbox",
    "pid-sandbox",
    "network-sandbox",
}
def effective_features(raw: str) -> dict[str, bool]:
    state: dict[str, bool] = {}
    for token in raw.split():
        if token.startswith("-"):
            state[token[1:]] = False
        else:
            state[token] = True
    return state

feature_state = effective_features(values.get("FEATURES", ""))
expected_state = effective_features(baseline_features)
# Portage may enable the news feature implicitly from the active profile even
# when `portageq envvar FEATURES` omits it.  Treat that one profile-derived
# capability as part of the live baseline while retaining exact drift checks
# for every configured feature.
if "news" not in expected_state and "news" in feature_state:
    expected_state["news"] = True
# Portage exposes ipc-sandbox in the global FEATURES value on this host but
# removes it when constructing the ebuild phase environment.  The phase
# receipt is the authority for the sandbox actually applied to the build;
# retain exact drift checking for every other configured feature.
if expected_state.get("ipc-sandbox") is True and "ipc-sandbox" not in feature_state:
    expected_state.pop("ipc-sandbox")
# `merge-wait` is likewise a global Portage setting that is not exported to
# the ebuild phase environment.  It remains covered by the global policy
# capture; phase receipts compare only the effective phase feature set.
if expected_state.get("merge-wait") is True and "merge-wait" not in feature_state:
    expected_state.pop("merge-wait")
if expected_policy == "profile-stage":
    expected_state.update({"ccache": False, "distcc": False, "icecream": False})
elif expected_policy != "baseline":
    raise SystemExit(f"{label}: unknown expected policy {expected_policy!r}")
if feature_state != expected_state:
    raise SystemExit(
        f"{label}: effective FEATURES drifted: expected={expected_state!r} "
        f"observed={feature_state!r}"
    )
missing = sorted(name for name in required_features if feature_state.get(name) is not True)
if missing:
    raise SystemExit(f"{label}: normal Portage policy is inactive: {missing}")
if values.get("phase_euid") != expected_uid or values.get("phase_egid") != expected_gid:
    raise SystemExit(f"{label}: userpriv did not select the live portage identity")
if values.get("sandbox_on") != "1":
    raise SystemExit(f"{label}: SANDBOX_ON is not active")
if values.get("CC") != expected_compiler or values.get("cc_realpath") != expected_compiler:
    raise SystemExit(f"{label}: CC is not the exact absolute reviewed Clang executable")
if values.get("CXX") != expected_cxx or values.get("cxx_realpath") != expected_cxx:
    raise SystemExit(f"{label}: CXX is not the exact absolute reviewed Clang++ executable")
if values.get("ccache_recache") != "1":
    raise SystemExit(f"{label}: CCACHE_RECACHE=1 fresh-compile defense is absent")
if expected_policy == "profile-stage":
    if values.get("ccache_disable") != "1" or values.get("sccache_disable") != "1":
        raise SystemExit(f"{label}: profile stage lacks both cache-disable controls")
else:
    if values.get("ccache_disable") not in {"0", "unset"}:
        raise SystemExit(f"{label}: ordinary phase unexpectedly disables ccache")
    if values.get("sccache_disable") not in {"0", "unset"}:
        raise SystemExit(f"{label}: ordinary phase unexpectedly disables sccache")
for key, host_identity in (
    ("pid_namespace", host_pid),
    ("network_namespace", host_network),
    ("ipc_namespace", host_ipc),
    ("mount_namespace", host_mount),
):
    phase_identity = values.get(key)
    if not phase_identity or phase_identity == host_identity:
        raise SystemExit(
            f"{label}: {key} is absent or identical to the driver namespace"
        )
PY
}

assert_isolated_policy_receipt() {
    local receipt=$1 label=$2
    python3 - "${receipt}" "${label}" <<'PY'
import pathlib
import sys

path, label = sys.argv[1:]
values: dict[str, str] = {}
for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("\t")
    if not separator or key in values:
        raise SystemExit(f"{label}: invalid or duplicate receipt row: {line!r}")
    values[key] = value
state: dict[str, bool] = {}
for token in values.get("FEATURES", "").split():
    if token.startswith("-"):
        state[token[1:]] = False
    else:
        state[token] = True
unexpected = sorted(
    name
    for name in (
        "sandbox",
        "usersandbox",
        "mount-sandbox",
        "network-sandbox",
        "pid-sandbox",
        "ipc-sandbox",
    )
    if state.get(name) is True
)
if unexpected:
    raise SystemExit(f"{label}: diagnostic lane unexpectedly enabled {unexpected}")
if values.get("sandbox_on") != "0":
    raise SystemExit(f"{label}: diagnostic lane did not retain SANDBOX_ON=0")
PY
}

assert_selected_policy_receipt() {
    local receipt=$1 label=$2 expected_policy=${3:-baseline}
    if [[ ${PORTAGE_POLICY_MODE} == live ]]; then
        assert_live_policy_receipt "${receipt}" "${label}" "${expected_policy}"
    else
        assert_isolated_policy_receipt "${receipt}" "${label}"
    fi
}

assert_fresh_compiler_execution() {
    local log=$1 label=$2 marker
    for marker in workload.c main.c link; do
        [[ $(grep -Fc \
            "fixture-compiler-execution=${marker}:completed" "${log}") == 1 ]] || \
            fail "${label} lacks one exact completed ${marker} compiler marker"
    done
    grep -Fq "fixture-CC=${CLANG}" "${log}" || \
        fail "${label} did not invoke the exact absolute reviewed Clang"
    if [[ ${PORTAGE_POLICY_MODE} == live ]]; then
        grep -Fq 'fixture-ccache-recache=1' "${log}" || \
            fail "${label} lacks CCACHE_RECACHE=1 evidence"
    fi
}

write_live_policy_probe_environment() {
    local output=${POLICY_PROBE_ENV}.partial
    [[ ${PORTAGE_POLICY_MODE} == live ]] || return 1
    printf '%s\n' \
        'GENTOO_OPT_MODE="off"' \
        'GENTOO_OPT_PROFILE_MAP_READY="0"' \
        'GENTOO_OPT_COMPILER_FAMILY="clang"' \
        'GENTOO_OPT_ABI="amd64"' \
        'GENTOO_OPT_SAMPLE_POLICY_PROBE="1"' \
        "GENTOO_OPT_SAMPLE_SANDBOX_DENY_PATH=\"${SANDBOX_DENY_PATH}\"" \
        "SANDBOX_DENY=\"${SANDBOX_DENY_PATH}\"" \
        'CCACHE_RECACHE="1"' \
        > "${output}"
    chmod 0644 -- "${output}"
    mv -- "${output}" "${POLICY_PROBE_ENV}"
    printf '%s\n' '=app-test/phase2-pgo-use-fixture-1 sample-live-policy-probe.conf' \
        > "${GENERATED_ASSIGNMENT}.partial"
    chmod 0644 -- "${GENERATED_ASSIGNMENT}.partial"
    mv -- "${GENERATED_ASSIGNMENT}.partial" "${GENERATED_ASSIGNMENT}"
}

assert_framework_target_receipt() {
    local receipt=$1 label=$2
    [[ $(field framework_target "${receipt}") == "${FRAMEWORK_TARGET}" ]] || \
        fail "${label} did not execute through the exact selected framework target"
}

build_fingerprint_input() {
    local receipt=$1 environment_name=$2 transform=$3 output=$4 expected=$5
    python3 - "${receipt}" "${environment_name}" "${transform}" \
        "${output}" "${expected}" "${EBUILD_SHA}" "${CLANG}" "${PROFILE}" <<'PY'
import json
import pathlib
import sys

receipt_path, environment_name, transform, output, expected, ebuild_sha, compiler, profile = sys.argv[1:]
values = {}
for line in pathlib.Path(receipt_path).read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("\t")
    if not separator or key in values:
        raise SystemExit(f"invalid or duplicate receipt field: {line!r}")
    values[key] = value

axes = (
    "CFLAGS", "CXXFLAGS", "LDFLAGS", "RUSTFLAGS", "GOFLAGS",
    "FEATURES", "USE", "EXTRA_ECONF", "EXTRA_EMESON", "EXTRA_ECMAKE",
)
missing = [key for key in axes if key not in values]
if missing:
    raise SystemExit(f"receipt lacks exact axes: {missing}")

def append_once(value: str, flag: str) -> str:
    if flag in value.split():
        return value
    return f"{value} {flag}" if value else flag

resolved = {key: values[key] for key in axes}
if transform == "sample-use":
    profile_flag = f"-fprofile-sample-use={profile}"
    for key in ("CFLAGS", "CXXFLAGS"):
        resolved[key] = append_once(resolved[key], profile_flag)
        resolved[key] = append_once(resolved[key], "-fsample-profile-use-profi")
    for flag in ("-ccache", "-distcc", "-icecream"):
        resolved["FEATURES"] = append_once(resolved["FEATURES"], flag)
elif transform != "observed":
    raise SystemExit(f"unknown fingerprint transform: {transform}")

def exact_tokens(value: str) -> list[str]:
    return sorted(set(value.split()))

document = {
    "schema_version": 3,
    "category": "app-test",
    "pf": "phase2-pgo-use-fixture-1",
    "slot": "0",
    "subslot": "0",
    "repository": "phase2-sample-pgo-fixture",
    "ebuild_sha256": ebuild_sha,
    "eapi": "8",
    "chost": "x86_64-pc-linux-gnu",
    "abi": "amd64",
    "compiler": {
        "path": compiler,
        "family": "clang",
        "major": 22,
        "profile_format": "llvm-sample-v22",
    },
    "use_flags": exact_tokens(resolved["USE"]),
    "cflags": resolved["CFLAGS"],
    "cxxflags": resolved["CXXFLAGS"],
    "ldflags": resolved["LDFLAGS"],
    "rustflags": resolved["RUSTFLAGS"],
    "goflags": resolved["GOFLAGS"],
    "features": resolved["FEATURES"].split(),
    "package_env_files": [environment_name],
    "extra_econf": resolved["EXTRA_ECONF"],
    "extra_emeson": resolved["EXTRA_EMESON"],
    "extra_ecmake": resolved["EXTRA_ECMAKE"],
    "kernel_module": False,
    "kernel_release": None,
    "rust_target_triple": None,
    "rustc_llvm_version": None,
}
pathlib.Path(output).write_text(
    json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
pathlib.Path(expected).write_text(
    json.dumps(resolved, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
}

assert_receipt_axes() {
    local receipt=$1 expected=$2 label=$3
    python3 - "${receipt}" "${expected}" "${label}" <<'PY'
import json
import pathlib
import sys

receipt, expected_path, label = sys.argv[1:]
values = {}
for line in pathlib.Path(receipt).read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("\t")
    if not separator or key in values:
        raise SystemExit(f"{label}: invalid or duplicate receipt field {line!r}")
    values[key] = value
expected = json.loads(pathlib.Path(expected_path).read_text(encoding="utf-8"))
failures = {
    key: {"expected": expected_value, "observed": values.get(key)}
    for key, expected_value in expected.items()
    if values.get(key) != expected_value
}
if failures:
    raise SystemExit(f"{label}: exact build axes differ: {json.dumps(failures, sort_keys=True)}")
PY
}

if [[ ${PORTAGE_POLICY_MODE} == live ]]; then
    capture_protected_live_state "${WORK}/protected-live-state.before.json"
fi
if ((PRODUCTION_LOCKS)); then
    {
        printf 'schema\tgentoo-optimization-production-sample-write-allowlist-v1\n'
        for name in gate-status.tsv llvm-profgen-conversion-log.json mapping-input \
            perf.data profile.manifest profile.manifest.metadata.json \
            sample-metadata.json sample.prof; do
            printf 'allowed_file\t%s/%s\n' "${PROFILE_ROOT}" "${name}"
        done
        for name in consumer.fingerprint gate-status.tsv mapping.fingerprint \
            seed.fingerprint transaction.authorization; do
            printf 'allowed_file\t%s/%s\n' "${PRODUCTION_STATE_ROOT}" "${name}"
        done
        printf 'must_remain_exact\t%s\n' "${TRANSACTION_JOURNAL}"
        printf 'must_remain_exact\t%s\n' "${TRANSACTION_CHILD_IDENTITY}"
    } > "${WORK}/production-write-allowlist.tsv"
fi

# The live-policy lane first proves actual enforcement, independently of the
# feature-string and namespace evidence collected by every successful phase.
# The repository-owned probe directory is intentionally DAC-writable by the
# portage user yet outside Portage's permitted build roots.  A write that
# succeeds outside sandbox would be a hard failure; a normal diagnostic build
# never executes this expected-failure phase.
if [[ ${PORTAGE_POLICY_MODE} == live ]]; then
    mkdir -- "${SANDBOX_DENY_DIRECTORY}"
    chown "0:${PORTAGE_GID}" -- "${SANDBOX_DENY_DIRECTORY}"
    chmod 0770 -- "${SANDBOX_DENY_DIRECTORY}"
    runuser -u portage -- /usr/bin/touch "${SANDBOX_DENY_PATH}" || \
        fail 'sandbox probe path is not DAC-writable by the live portage user'
    [[ -f ${SANDBOX_DENY_PATH} && ! -L ${SANDBOX_DENY_PATH} && \
        $(stat -c %u -- "${SANDBOX_DENY_PATH}") == "${PORTAGE_UID}" ]] || \
        fail 'sandbox DAC preflight did not create the expected portage-owned file'
    rm -f -- "${SANDBOX_DENY_PATH}"
    printf '%s\n' \
        $'schema\tgentoo-optimization-sample-sandbox-enforcement-v1' \
        $'dac_write_as_portage\tpassed' \
        "deny_path\t${SANDBOX_DENY_PATH}" \
        > "${WORK}/sandbox-enforcement.tsv"
    write_live_policy_probe_environment
    run_ebuild "${WORK}/sandbox-probe-clean.log" clean || :
    sandbox_probe_status=0
    run_ebuild "${WORK}/sandbox-probe-build.log" compile || sandbox_probe_status=$?
    # The expected denied write is surfaced by Portage as a nonzero ebuild
    # status even though the fixture handles it deliberately; validate the
    # denial receipt below instead of treating that diagnostic status as a
    # policy failure.
    ((sandbox_probe_status == 0 || sandbox_probe_status == 1)) || \
        fail 'live sandbox probe ebuild failed unexpectedly'
    [[ -s ${FLAGS_FILE} ]] || \
        fail 'live sandbox policy probe emitted no phase receipt'
    cp -- "${FLAGS_FILE}" "${WORK}/sandbox-probe-effective-flags.tsv"
    assert_live_policy_receipt "${WORK}/sandbox-probe-effective-flags.tsv" \
        'live sandbox enforcement probe' baseline
    python3 - "${WORK}/sandbox-probe-effective-flags.tsv" \
        "${SANDBOX_DENY_PATH}" <<'PY'
import pathlib
import sys

receipt, deny_path = sys.argv[1:]
values = {}
for line in pathlib.Path(receipt).read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("\t")
    if not separator or key in values:
        raise SystemExit(f"invalid or duplicate sandbox probe receipt row: {line!r}")
    values[key] = value
allowlist = values.get("sandbox_write", "")
if not allowlist or allowlist == "unset":
    raise SystemExit("sandbox probe receipt lacks SANDBOX_WRITE")
deny = pathlib.PurePosixPath(deny_path)
for raw_entry in allowlist.split(":"):
    entry = raw_entry.rstrip("/") or "/"
    if not entry.startswith("/"):
        continue
    allowed = pathlib.PurePosixPath(entry)
    if deny == allowed or allowed in deny.parents:
        raise SystemExit(
            f"sandbox deny path is covered by SANDBOX_WRITE entry {raw_entry!r}"
        )
PY
    grep -Fq 'sample sandbox policy probe observed the expected write denial' \
        "${WORK}/sandbox-probe-build.log" || \
        fail 'live sandbox denial lacked the fixture expected-denial diagnostic'
    [[ ! -e ${SANDBOX_DENY_PATH} && ! -L ${SANDBOX_DENY_PATH} ]] || \
        fail 'live sandbox policy probe created the forbidden external file'
    printf 'sandbox_write_denied\tpassed\n' >> "${WORK}/sandbox-enforcement.tsv"
    chmod 0750 -- "${SANDBOX_DENY_DIRECTORY}"
    # The probe intentionally leaves a sandbox-denial diagnostic in the
    # ebuild environment; Portage may report that cleanup as unsuccessful
    # even though the policy probe itself passed.  Cleanup is best-effort and
    # must not convert the validated denial into a fixture failure.
    run_ebuild "${WORK}/sandbox-probe-final-clean.log" clean || :
fi

# Resolve the exact dispatcher-expanded mapping axes once, then bind an
# authoritative rebuild to their canonical package fingerprint.
SEED_FINGERPRINT=$(printf 'a%.0s' {1..64})
if ((PRODUCTION_LOCKS)); then
    publish_production_fingerprint "${SEED_FINGERPRINT}" "${SEED_FINGERPRINT_FILE}"
fi
write_map_environment "${SEED_FINGERPRINT}" "${SEED_FINGERPRINT_FILE}"
run_ebuild "${WORK}/preliminary-clean.log" clean
run_ebuild "${WORK}/preliminary-map-build.log" compile
[[ -s ${FLAGS_FILE} ]] || fail 'preliminary mapping build emitted no exact flag receipt'
cp -- "${FLAGS_FILE}" "${WORK}/preliminary-effective-flags.tsv"
assert_framework_target_receipt "${WORK}/preliminary-effective-flags.tsv" \
    'preliminary mapping build'
assert_selected_policy_receipt "${WORK}/preliminary-effective-flags.tsv" \
    'preliminary mapping build' profile-stage
assert_fresh_compiler_execution "${WORK}/preliminary-map-build.log" \
    'preliminary mapping build'
MAPPING_CFLAGS=$(field CFLAGS "${FLAGS_FILE}")
MAPPING_LDFLAGS=$(field LDFLAGS "${FLAGS_FILE}")
ACTIVE_COMPILER=$(field active_compiler "${FLAGS_FILE}")
[[ ${ACTIVE_COMPILER} == "${CLANG}" ]] || fail 'dispatcher selected an unexpected compiler'
for flag in -gline-tables-only -fdebug-info-for-profiling \
    -funique-internal-linkage-names -fpseudo-probe-for-profiling; do
    [[ " ${MAPPING_CFLAGS} " == *" ${flag} "* ]] || \
        fail "mapping-ready build lacks ${flag}"
done
[[ ${MAPPING_CFLAGS} != *'-fprofile-sample-use='* && \
    ${MAPPING_CFLAGS} != *'-fprofile-use='* ]] || \
    fail 'mapping build unexpectedly consumed a profile'
[[ " ${MAPPING_LDFLAGS} " == *' -Wl,--build-id=sha1 '* && \
    ${MAPPING_LDFLAGS} != *'--emit-relocs'* ]] || \
    fail 'mapping build lacks its exact stage-owned build ID or carries BOLT relocations'
[[ $(awk -v value="${MAPPING_LDFLAGS}" 'BEGIN {count=gsub(/-Wl,--build-id=sha1/, "", value); print count}') == 1 ]] || \
    fail 'mapping build carries a duplicate build-ID policy'

EBUILD_SHA=$(sha256sum -- "${EBUILD}"); EBUILD_SHA=${EBUILD_SHA%% *}
build_fingerprint_input "${WORK}/preliminary-effective-flags.tsv" \
    generated-sample-map.conf observed "${WORK}/map-fingerprint-input.json" \
    "${WORK}/map-expected-axes.json"
MAP_FINGERPRINT=$("${PROFILE_IDENTITY}" fingerprint \
    --input "${WORK}/map-fingerprint-input.json" \
    --metadata-out "${WORK}/map-fingerprint-metadata.json")
[[ ${MAP_FINGERPRINT} =~ ^[0-9a-f]{64}$ ]] || \
    fail 'invalid canonical mapping fingerprint'

if ((PRODUCTION_LOCKS)); then
    publish_production_fingerprint "${MAP_FINGERPRINT}" "${MAP_FINGERPRINT_FILE}"
fi
write_map_environment "${MAP_FINGERPRINT}" "${MAP_FINGERPRINT_FILE}"
cp -- "${GENERATED_ASSIGNMENT}" "${WORK}/generated-package-env-map"
run_ebuild "${WORK}/map-clean.log" clean
run_ebuild "${WORK}/map-build.log" compile
[[ -s ${FLAGS_FILE} ]] || fail 'authoritative mapping build emitted no flag receipt'
cp -- "${FLAGS_FILE}" "${WORK}/map-effective-flags.tsv"
assert_framework_target_receipt "${WORK}/map-effective-flags.tsv" \
    'authoritative mapping build'
assert_selected_policy_receipt "${WORK}/map-effective-flags.tsv" \
    'authoritative mapping build' profile-stage
assert_fresh_compiler_execution "${WORK}/map-build.log" \
    'authoritative mapping build'
[[ $(field active_fingerprint "${FLAGS_FILE}") == "${MAP_FINGERPRINT}" ]] || \
    fail 'mapping build did not load the generated fingerprint assignment'
[[ $(field phase_euid "${FLAGS_FILE}") == "${PORTAGE_UID}" && \
    $(field phase_egid "${FLAGS_FILE}") == "${PORTAGE_GID}" ]] || \
    fail 'mapping compilation did not run through the live userpriv identity'
assert_receipt_axes "${WORK}/map-effective-flags.tsv" \
    "${WORK}/map-expected-axes.json" 'fingerprint-bound mapping rebuild'
TRAIN_BINARY=${BUILD_ROOT}/work/phase2-pgo-use-fixture-1/phase2-pgo-use-fixture
[[ -x ${TRAIN_BINARY} ]] || fail 'mapping-ready Portage build produced no executable'
MAPPED_BINARY=${PROFILE_ROOT}/mapping-input
publish_mapping_input "${TRAIN_BINARY}" "${MAPPED_BINARY}" || \
    fail 'could not durably publish the exact mapping input in its target filesystem'
[[ $(stat -c '%u:%g:%a:%h' -- "${MAPPED_BINARY}") == \
    "0:${PORTAGE_GID}:550:1" ]] || fail 'mapping binary is not immutable and Portage-readable'
runuser -u portage -- test -x "${MAPPED_BINARY}" || \
    fail 'Portage cannot execute the preserved mapping input'
readelf -nW -- "${MAPPED_BINARY}" > "${WORK}/map-binary.notes"
grep -Eq 'Build ID: [0-9a-fA-F]+' "${WORK}/map-binary.notes" || \
    fail 'mapping-ready executable has no GNU build ID'
readelf -SW -- "${MAPPED_BINARY}" > "${WORK}/map-binary.sections"

# Resolve the ordinary build axes under the exact future package.env filename.
# The consumer-only flag transformation is deterministic; the real use build
# below must reproduce every predicted axis before its fingerprint is accepted.
write_use_probe_environment
run_ebuild "${WORK}/use-probe-clean.log" clean
run_ebuild "${WORK}/use-probe-build.log" compile
[[ -s ${FLAGS_FILE} ]] || fail 'ordinary consumer probe emitted no exact flag receipt'
cp -- "${FLAGS_FILE}" "${WORK}/use-probe-effective-flags.tsv"
assert_framework_target_receipt "${WORK}/use-probe-effective-flags.tsv" \
    'ordinary consumer probe'
assert_selected_policy_receipt "${WORK}/use-probe-effective-flags.tsv" \
    'ordinary consumer probe' baseline
assert_fresh_compiler_execution "${WORK}/use-probe-build.log" \
    'ordinary consumer probe'
[[ $(field mode "${FLAGS_FILE}") == off ]] || fail 'consumer probe was not ordinary/off'
for forbidden in -fprofile-sample-use= -fsample-profile-use-profi \
    -gline-tables-only -fdebug-info-for-profiling \
    -funique-internal-linkage-names -fpseudo-probe-for-profiling \
    -ffunction-sections -fdata-sections; do
    [[ $(field CFLAGS "${FLAGS_FILE}") != *"${forbidden}"* ]] || \
        fail "ordinary consumer probe contains stage-only flag ${forbidden}"
    [[ $(field CXXFLAGS "${FLAGS_FILE}") != *"${forbidden}"* ]] || \
        fail "ordinary C++ consumer probe contains stage-only flag ${forbidden}"
done
[[ $(field LDFLAGS "${FLAGS_FILE}") != *'--emit-relocs'* && \
    $(field LDFLAGS "${FLAGS_FILE}") != *'--build-id'* ]] || \
    fail 'ordinary consumer probe contains stage-only linker metadata'
[[ $(field RUSTFLAGS "${FLAGS_FILE}") != *'debuginfo'* && \
    $(field RUSTFLAGS "${FLAGS_FILE}") != *'--emit-relocs'* && \
    $(field RUSTFLAGS "${FLAGS_FILE}") != *'--build-id'* ]] || \
    fail 'ordinary consumer probe contains stage-only Rust metadata'
build_fingerprint_input "${WORK}/use-probe-effective-flags.tsv" \
    generated-sample-use.conf sample-use "${WORK}/use-fingerprint-input.json" \
    "${WORK}/use-expected-axes.json"
USE_FINGERPRINT=$("${PROFILE_IDENTITY}" fingerprint \
    --input "${WORK}/use-fingerprint-input.json" \
    --metadata-out "${WORK}/use-fingerprint-metadata.json")
[[ ${USE_FINGERPRINT} =~ ^[0-9a-f]{64}$ ]] || \
    fail 'invalid canonical consumer fingerprint'
[[ ${USE_FINGERPRINT} != "${MAP_FINGERPRINT}" ]] || \
    fail 'mapping and sample-use fingerprints unexpectedly collide'
run_ebuild "${WORK}/use-probe-final-clean.log" clean

rm -f -- "${PERF_DATA}" "${PERF_DATA}.partial"
if ! timeout --signal=TERM --kill-after=10 300 \
    "${PERF}" record -q --no-buildid-cache -e cycles:u -j any,u \
    -o "${PERF_DATA}.partial" -- "${MAPPED_BINARY}" "${ITERATIONS}" \
    > "${WORK}/training.stdout" 2> "${WORK}/perf-record.stderr"; then
    rm -f -- "${PERF_DATA}.partial"
    fail 'representative perf collection failed or timed out'
fi
[[ -s ${PERF_DATA}.partial ]] || fail 'perf produced no nonempty transaction output'
mv -- "${PERF_DATA}.partial" "${PERF_DATA}"
chown "0:${PORTAGE_GID}" -- "${PERF_DATA}"
chmod 0440 -- "${PERF_DATA}"
[[ $(stat -c '%u:%g:%a:%h' -- "${PERF_DATA}") == \
    "0:${PORTAGE_GID}:440:1" ]] || fail 'perf input is not immutable and Portage-readable'
runuser -u portage -- test -r "${PERF_DATA}" || fail 'Portage cannot read perf input'
"${PERF}" evlist -v -i "${PERF_DATA}" > "${WORK}/perf-evlist.log"
grep -Eq 'sample_type:.*BRANCH_STACK' "${WORK}/perf-evlist.log" || \
    fail 'perf profile lacks branch-stack samples'
grep -Eq 'branch_sample_type:.*USER.*ANY' "${WORK}/perf-evlist.log" || \
    fail 'perf profile lacks the requested user/any branch filter'

PRODUCTION_HOST=$(hostname)
PRODUCTION_DATE=$(TZ=UTC date -u +%F)
[[ ${PRODUCTION_HOST} =~ ^[A-Za-z0-9+_.@-]+$ ]] || fail 'unsafe hostname identity'
"${PROFILE_IDENTITY}" sample-convert \
    --llvm-profgen "${PROFGEN}" \
    --llvm-profdata "${PROFDATA}" \
    --readelf "${LLVM_READELF}" \
    --objcopy "${LLVM_OBJCOPY}" \
    --binary "${MAPPED_BINARY}" \
    --perf-data "${PERF_DATA}" \
    --profile-out "${PROFILE}" \
    --metadata-out "${SAMPLE_METADATA}" \
    --conversion-log-out "${CONVERSION_LOG}" \
    --cpv app-test/phase2-pgo-use-fixture-1 \
    --fingerprint "${MAP_FINGERPRINT}" \
    --abi amd64 \
    --clang-major 22 \
    --optimization-generation-id "${GENERATION_ID}" \
    --inventory-id "${INVENTORY_ID}" \
    --inventory-sha256 "${INVENTORY_SHA256}" \
    --workload-revision phase2-sample-portage-workload-v1 \
    --source-identity-sha256 "${EBUILD_SHA}" \
    --production-host "${PRODUCTION_HOST}" \
    --production-date "${PRODUCTION_DATE}" \
    "${PROFILE_LOCK_ARGS[@]}" \
    > "${WORK}/sample-convert.stdout" 2> "${WORK}/sample-convert.stderr"
[[ -s ${PROFILE} && -s ${SAMPLE_METADATA} && -s ${CONVERSION_LOG} ]] || \
    fail 'sample conversion did not publish its exact three-file transaction'
for artifact in "${PROFILE}" "${SAMPLE_METADATA}"; do
    [[ $(stat -c '%u:%g:%a:%h' -- "${artifact}") == \
        "0:${PORTAGE_GID}:640:1" ]] || \
        fail "sample artifact is not immutable, single-link, root:portage 0640: ${artifact}"
done
[[ $(stat -c '%u:%g:%a:%h' -- "${CONVERSION_LOG}") == \
    "0:${PORTAGE_GID}:440:1" ]] || \
    fail 'conversion log is not immutable, single-link, root:portage 0440 evidence'
"${PROFDATA}" show --sample --all-functions --counts "${PROFILE}" \
    > "${WORK}/sample-profile.show"
grep -Fq 'Function:' "${WORK}/sample-profile.show" || \
    fail 'sample profile contains no recorded function'

CLANG_SHA=$(sha256sum -- "${CLANG}"); CLANG_SHA=${CLANG_SHA%% *}
PROFDATA_SHA=$(sha256sum -- "${PROFDATA}"); PROFDATA_SHA=${PROFDATA_SHA%% *}
"${VALIDATOR}" produce \
    --backend clang-sample \
    --profile "${PROFILE}" \
    --fingerprint "${USE_FINGERPRINT}" \
    --sample-input-fingerprint "${MAP_FINGERPRINT}" \
    --abi amd64 \
    --compiler-family clang \
    --compiler "${CLANG}" \
    --compiler-sha256 "${CLANG_SHA}" \
    --compiler-major 22 \
    --profile-tool "${PROFDATA}" \
    --profile-tool-sha256 "${PROFDATA_SHA}" \
    --profile-tool-major 22 \
    --sample-metadata "${SAMPLE_METADATA}" \
    --manifest-out "${MANIFEST}" \
    --metadata-out "${SIDECAR}" \
    --generation-id "${GENERATION_ID}" \
    --inventory-id "${INVENTORY_ID}" \
    --inventory-sha256 "${INVENTORY_SHA256}" \
    "${PROFILE_LOCK_ARGS[@]}" \
    > "${WORK}/profile-produce.stdout" 2> "${WORK}/profile-produce.stderr"
"${VALIDATOR_COMMAND}" verify --manifest "${MANIFEST}" --metadata "${SIDECAR}" \
    > "${WORK}/profile-verify.stdout" 2> "${WORK}/profile-verify.stderr"

for artifact in "${MANIFEST}" "${SIDECAR}"; do
    [[ $(stat -c '%u:%g:%a:%h' -- "${artifact}") == \
        "0:${PORTAGE_GID}:640:1" ]] || \
        fail "validated profile artifact is not root:portage 0640: ${artifact}"
done
for artifact in "${MAPPED_BINARY}" "${PERF_DATA}" "${PROFILE}" \
    "${SAMPLE_METADATA}" "${CONVERSION_LOG}" "${MANIFEST}" "${SIDECAR}"; do
    runuser -u portage -- test -r "${artifact}" || \
        fail "Portage cannot read an exact sample-use input: ${artifact}"
done
python3 - "${SIDECAR}" "${USE_FINGERPRINT}" "${MAP_FINGERPRINT}" \
    "${GENERATION_ID}" "${INVENTORY_ID}" "${INVENTORY_SHA256}" \
    "${EBUILD_SHA}" "${PRODUCTION_HOST}" "${PRODUCTION_DATE}" <<'PY'
import json
import pathlib
import sys

(
    path,
    consumer,
    mapping,
    generation,
    inventory,
    inventory_sha,
    source_identity,
    production_host,
    production_date,
) = sys.argv[1:]
data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
if data["profile"]["fingerprint"] != consumer:
    raise SystemExit("validator sidecar lost the consumer-build fingerprint")
if data["backend_proof"]["sample_input_fingerprint"] != mapping:
    raise SystemExit("validator sidecar lost the mapping-input fingerprint")
expected_generation = {
    "generation_id": generation,
    "inventory_id": inventory,
    "inventory_sha256": inventory_sha,
}
if data["generation"] != expected_generation:
    raise SystemExit("validator sidecar lost the complete generation identity")
if data["backend_proof"]["reproducibility"] != {
    "optimization_generation_id": generation,
    "inventory_id": inventory,
    "inventory_sha256": inventory_sha,
    "workload_revision": "phase2-sample-portage-workload-v1",
    "source_identity_sha256": source_identity,
    "production_host": production_host,
    "production_date": production_date,
}:
    raise SystemExit("validator sidecar reproducibility identity is inconsistent")
PY

if ((PRODUCTION_LOCKS)); then
    publish_production_fingerprint "${USE_FINGERPRINT}" "${USE_FINGERPRINT_FILE}"
fi
write_use_environment "${USE_FINGERPRINT}" "${USE_FINGERPRINT_FILE}"
cp -- "${GENERATED_ASSIGNMENT}" "${WORK}/generated-package-env-use"
(
    cd -- "${WORK}"
    sha256sum -- generated-package-env-map generated-package-env-use \
        config-root/etc/portage/env/generated-sample-map.conf \
        config-root/etc/portage/env/generated-sample-use.conf
) > "${WORK}/generated-policy.sha256"
run_ebuild "${WORK}/use-clean.log" clean
run_ebuild "${WORK}/sample-use-build.log" install
[[ -s ${FLAGS_FILE} ]] || fail 'sample-use build emitted no flag receipt'
cp -- "${FLAGS_FILE}" "${WORK}/sample-use-effective-flags.tsv"
assert_framework_target_receipt "${WORK}/sample-use-effective-flags.tsv" \
    'authoritative sample-use build'
assert_selected_policy_receipt "${WORK}/sample-use-effective-flags.tsv" \
    'authoritative sample-use build' profile-stage
assert_fresh_compiler_execution "${WORK}/sample-use-build.log" \
    'authoritative sample-use build'
USE_CFLAGS=$(field CFLAGS "${FLAGS_FILE}")
[[ $(field mode "${FLAGS_FILE}") == clang-sample-use ]] || \
    fail 'generated package.env assignment did not activate sample use'
[[ $(field active_fingerprint "${FLAGS_FILE}") == "${USE_FINGERPRINT}" ]] || \
    fail 'sample-use build consumed a different package fingerprint'
[[ $(field active_compiler "${FLAGS_FILE}") == "${CLANG}" ]] || \
    fail 'sample-use build selected a different compiler from its fingerprint'
[[ $(field phase_euid "${FLAGS_FILE}") == "${PORTAGE_UID}" && \
    $(field phase_egid "${FLAGS_FILE}") == "${PORTAGE_GID}" ]] || \
    fail 'sample-use compilation did not run through the live userpriv identity'
assert_receipt_axes "${WORK}/sample-use-effective-flags.tsv" \
    "${WORK}/use-expected-axes.json" 'authoritative sample-use build'
[[ " ${USE_CFLAGS} " == *" -fprofile-sample-use=${PROFILE} "* ]] || \
    fail 'sample-use compiler flags lack the exact sample profile'
[[ " ${USE_CFLAGS} " == *' -fsample-profile-use-profi '* ]] || \
    fail 'sample-use compiler flags lack profi consumption'
[[ ${USE_CFLAGS} != *'-fprofile-use='* ]] || \
    fail 'sample profile leaked through the IR instrumentation consumer flag'
grep -Fq 'mode=clang-sample-use' "${WORK}/sample-use-build.log" || \
    fail 'Portage build log lacks the selected sample-use mode'
grep -Fq "profile=${PROFILE}" "${WORK}/sample-use-build.log" || \
    fail 'Portage build log lacks the exact validated profile path'
if ((PRODUCTION_LOCKS == 0)); then
    awk -F '\t' -v uid="${PORTAGE_UID}" -v gid="${PORTAGE_GID}" \
        '$1 == "verify" && $2 == uid && $3 == gid {found = 1} END {exit !found}' \
        "${VALIDATOR_IDENTITY}" || \
        fail 'authoritative profile validation did not execute under userpriv'
else
    grep -Fxq $'mode\tproduction-locks' "${VALIDATOR_IDENTITY}" || \
        fail 'production validation identity record is absent'
    grep -Fxq $'profile_helper_lock_arguments\t0' "${VALIDATOR_IDENTITY}" || \
        fail 'production validation unexpectedly used substituted lock arguments'
fi

STAGED=${BUILD_ROOT}/image/usr/bin/phase2-pgo-use-fixture
[[ -x ${STAGED} ]] || fail 'sample-use Portage install staged no executable'
"${STAGED}" "${ITERATIONS}" > "${WORK}/sample-use.stdout"
cmp -- "${WORK}/training.stdout" "${WORK}/sample-use.stdout" || \
    fail 'sample-use executable changed functional output'

TAMPERED_SIDECAR=${WORK}/sidecar.tampered
{
    sha256sum -- "${SIDECAR}"
    stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${SIDECAR}"
} > "${WORK}/canonical-sidecar.before"
cp --preserve=mode,ownership,xattr -- "${SIDECAR}" "${TAMPERED_SIDECAR}"
printf '\n' >> "${TAMPERED_SIDECAR}"
[[ $(stat -c '%u:%g:%a:%h' -- "${TAMPERED_SIDECAR}") == \
    "0:${PORTAGE_GID}:640:1" ]] || \
    fail 'tampered sidecar substitute lost its trusted regular-file identity'
run_ebuild "${WORK}/tamper-clean.log" clean
if ((PRODUCTION_LOCKS)); then
    if run_ebuild_with_private_sidecar_bind \
        "${WORK}/tampered-sidecar.log" "${TAMPERED_SIDECAR}"; then
        fail 'real Portage sample-use build accepted a privately mounted tampered validator sidecar'
    fi
else
    cp --preserve=mode,ownership,xattr -- "${SIDECAR}" "${WORK}/sidecar.saved"
    cp --preserve=mode,ownership,xattr -- "${TAMPERED_SIDECAR}" "${SIDECAR}"
    if run_ebuild "${WORK}/tampered-sidecar.log" compile; then
        fail 'real Portage sample-use build accepted a tampered validator sidecar'
    fi
fi
grep -Fq 'authoritative profile manifest/sidecar verification failed' \
    "${WORK}/tampered-sidecar.log" || \
    fail 'tampered sidecar rejection lacked the fail-closed diagnostic'
if ((PRODUCTION_LOCKS)); then
    {
        sha256sum -- "${SIDECAR}"
        stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${SIDECAR}"
    } > "${WORK}/canonical-sidecar.after"
    cmp -- "${WORK}/canonical-sidecar.before" \
        "${WORK}/canonical-sidecar.after" || \
        fail 'private tamper test changed the canonical production sidecar'
else
    mv -- "${WORK}/sidecar.saved" "${SIDECAR}"
fi
[[ $(stat -c '%u:%g:%a:%h' -- "${SIDECAR}") == \
    "0:${PORTAGE_GID}:640:1" ]] || fail 'restored sidecar lost trusted metadata'
"${VALIDATOR_COMMAND}" verify --manifest "${MANIFEST}" --metadata "${SIDECAR}" \
    > "${WORK}/profile-verify-restored.stdout" \
    2> "${WORK}/profile-verify-restored.stderr"
run_ebuild "${WORK}/final-clean.log" clean

if [[ ${PORTAGE_POLICY_MODE} == live ]]; then
    resolve_live_portage_policy "${WORK}/live-policy-after.json"
    cmp -- "${WORK}/live-policy-before.json" "${WORK}/live-policy-after.json" || \
        fail 'authoritative live Portage policy changed during the sample-PGO pipeline'
    capture_protected_live_state "${WORK}/protected-live-state.after.json"
    cmp -- "${WORK}/protected-live-state.before.json" \
        "${WORK}/protected-live-state.after.json" || \
        fail 'sample-PGO pipeline changed a protected live Portage state root'
fi
assert_production_write_allowlist

if ((PRODUCTION_LOCKS)); then
    [[ ! -e ${VALIDATOR_PROXY} && ! -e ${FRAMEWORK_LOCK} && \
        ! -e ${PROJECT_LOCK} && ! -e ${GENERATION_LOCK} ]] || \
        fail 'production sample gate created a fixture helper or substituted lock'
    if grep -E 'GENTOO_OPT_PORTAGE_FIXTURE_MODE|GENTOO_OPT_FINGERPRINT=|GENTOO_OPT_PROFILE_VALIDATOR' \
        "${MAP_ENV}" "${USE_ENV}"; then
        fail 'production generated policy contains a fixture marker, inline fingerprint, or validator override'
    fi
    cp -- "${TRANSACTION_JOURNAL}" "${WORK}/transaction-journal.json"
    cp -- "${TRANSACTION_CHILD_IDENTITY}" \
        "${WORK}/transaction-child-identity.json"
    chmod 0600 -- "${WORK}/transaction-journal.json" \
        "${WORK}/transaction-child-identity.json"
    [[ ${TRANSACTION_JOURNAL_SHA256} == "$(sha256sum -- \
            "${WORK}/transaction-journal.json" | awk '{print $1}')" && \
        ${TRANSACTION_CHILD_IDENTITY_SHA256} == "$(sha256sum -- \
            "${WORK}/transaction-child-identity.json" | awk '{print $1}')" ]] || \
        fail 'preserved transaction journal/child evidence differs from its live identity'
    assert_no_persisted_authorization_token || \
        fail 'raw coordinator authorization persisted in production gate artifacts'
    PRODUCTION_TRANSACTION_TOKEN=
    [[ $(readlink -- "${FRAMEWORK_CURRENT}") == "${FRAMEWORK_TARGET}" ]] || \
        fail 'active framework target changed during the production sample gate'
    PRODUCTION_GATE_COMPLETE=1
    write_production_gate_status passed 0 || \
        fail 'cannot publish the durable production sample-gate pass marker'
    PRODUCTION_STATUS_FINALIZED=1
    capture_production_live_roots passed || \
        fail 'cannot preserve and index the passed live production sample-gate roots'
    PRODUCTION_CAPTURED=1
    {
        printf 'mode\tproduction-locks\n'
        printf 'work_root\t%s\n' "${WORK}"
        printf 'profile_root\t%s\n' "${PROFILE_ROOT}"
        printf 'generation_state_root\t%s\n' "${PRODUCTION_STATE_ROOT}"
        printf 'generation_id\t%s\n' "${GENERATION_ID}"
        printf 'inventory_id\t%s\n' "${INVENTORY_ID}"
        printf 'inventory_sha256\t%s\n' "${INVENTORY_SHA256}"
        for artifact in "${MAPPED_BINARY}" "${PERF_DATA}" "${PROFILE}" \
            "${SAMPLE_METADATA}" "${CONVERSION_LOG}" "${MANIFEST}" "${SIDECAR}" \
            "${SEED_FINGERPRINT_FILE}" "${MAP_FINGERPRINT_FILE}" \
            "${USE_FINGERPRINT_FILE}" "${PROFILE_ROOT}/gate-status.tsv" \
            "${PRODUCTION_STATE_ROOT}/gate-status.tsv" \
            "${TRANSACTION_AUTHORIZATION}" "${TRANSACTION_JOURNAL}" \
            "${TRANSACTION_CHILD_IDENTITY}"; do
            printf 'artifact\t%s\t%s\t%s\n' "${artifact}" \
                "$(sha256sum -- "${artifact}" | awk '{print $1}')" \
                "$(stat -c '%u:%g:%a:%h:%s' -- "${artifact}")"
        done
    } > "${WORK}/production-artifacts.tsv"
fi

{
    printf '%s\t%s\n' \
        'authoritative_work_root' "${WORK}" \
        'published_copy' "${CANONICAL_OUTPUT_DIR:-none}" \
        'portage_policy_mode' "${PORTAGE_POLICY_MODE}" \
        'published_copy_semantics' \
        'historical-byte-evidence; validator sidecars remain bound to authoritative paths' \
        'authoritative_work_final_identity' 'root:portage:0750'
    if ((PRODUCTION_LOCKS)); then
        printf 'production_work_root\t%s\n' "${PRODUCTION_GATE_WORK_ROOT}"
        printf 'profile_artifact_root\t%s\n' "${PROFILE_ROOT}"
        printf 'generation_state_root\t%s\n' "${PRODUCTION_STATE_ROOT}"
    fi
} > "${WORK}/publication-context.tsv"
(
    cd -- "${WORK}"
    sha256sum -- \
        app-test/phase2-pgo-use-fixture/phase2-pgo-use-fixture-1.ebuild \
        perf.data profile/sample.prof profile/sample-metadata.json \
        profile/llvm-profgen-conversion-log.json profile/profile.manifest \
        profile/profile.manifest.metadata.json profile/mapping-input \
        map-fingerprint-input.json map-fingerprint-metadata.json \
        use-fingerprint-input.json use-fingerprint-metadata.json \
        validator-identity.tsv preliminary-effective-flags.tsv \
        map-effective-flags.tsv use-probe-effective-flags.tsv \
        sample-use-effective-flags.tsv preliminary-map-build.log \
        map-build.log use-probe-build.log sample-use-build.log \
        publication-context.tsv portage-policy.tsv
    if [[ ${PORTAGE_POLICY_MODE} == live ]]; then
        sha256sum -- sandbox-enforcement.tsv \
            sandbox-probe-effective-flags.tsv sandbox-probe-build.log \
            live-policy-before.json live-policy-after.json \
            protected-live-state.before.json protected-live-state.after.json
    fi
    sha256sum -- "${AUTHORIZATION_TOKEN_SCANNER}"
    if ((PRODUCTION_LOCKS)); then
        sha256sum -- production-artifacts.tsv production-live-roots.tsv \
            production-write-allowlist.tsv \
            authorization-token-persistence-scan.tsv \
            transaction-journal.json transaction-child-identity.json \
            canonical-sidecar.before canonical-sidecar.after \
            profile/gate-status.tsv production-state/gate-status.tsv \
            production-state/seed.fingerprint \
            production-state/mapping.fingerprint \
            production-state/consumer.fingerprint
    fi
) > "${WORK}/evidence.sha256"
if ((PRODUCTION_LOCKS)); then
    printf 'PASS: production-lock Portage live sandbox policy, exact mapping fingerprint, perf training, sample conversion, distinct consumer fingerprint, immutable validation, generated package.env use, runtime proof, and tamper rejection\n'
elif [[ ${PORTAGE_POLICY_MODE} == live ]]; then
    printf 'PASS: real Portage live sandbox/userpriv/namespace policy, exact mapping fingerprint, perf training, sample conversion, distinct consumer fingerprint, immutable validation, generated package.env use, runtime proof, and tamper rejection\n'
else
    printf 'PASS: isolated-diagnostic Portage exact mapping fingerprint, perf training, sample conversion, distinct consumer fingerprint, immutable validation, generated package.env use, runtime proof, and tamper rejection\n'
fi

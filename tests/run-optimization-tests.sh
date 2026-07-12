#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly SCRIPT_DIR
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd -P)
readonly REPOSITORY_ROOT

MODE=${OPTIMIZATION_TEST_MODE:-quick}
OUTPUT_DIR=
KEEP_TEMP=0
LIST_ONLY=0
EXPLICIT_CAPABILITIES=0
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
RUN_ROOT=
LOG_ROOT=
RESULTS_FILE=
PREFLIGHT_REASON=
RESOLVED_TOOL=
TEMP_ROOT_CREATED=0

declare -A SELECTED_CAPABILITIES=()
readonly -a ALL_CAPABILITIES=(clang-ir clang-sample gcc rust go bolt)

usage() {
    cat <<'EOF'
Usage: tests/run-optimization-tests.sh [OPTIONS]

Run the repository's non-mutating optimization validation suites.

Modes:
  --mode quick          Static, Python, and fake-root recovery tests only
                        (default). Every capability fixture is an explicit SKIP.
  --mode capabilities   Also run every supported PGO and BOLT capability fixture.

Options:
  --capability NAME     Run only this capability fixture in addition to the quick
                        suites. Repeat as needed; NAME is clang-ir, clang-sample,
                        gcc, rust, go, bolt, or all. An explicit filter narrows
                        --mode capabilities as well.
  --output-dir DIR      Keep logs/evidence in a new absolute directory below
                        /tmp or /var/tmp/gentoo-optimization. Its canonical
                        path may contain only letters, digits, /, ., _, and -.
  --keep-temp           Keep the automatically allocated temporary directory.
  --list                List suites and capability names without running them.
  -h, --help            Show this help.

Environment:
  OPTIMIZATION_TEST_MODE=quick|capabilities
  OPTIMIZATION_TEST_CAPABILITIES=comma,separated,names
  SHELLCHECK=/path/to/shellcheck

Fixture-specific tool and iteration overrides (for example CLANGXX,
LLVM_PROFDATA, CLANG_SAMPLE_ITERATIONS, RUST_PGO_ITERATIONS,
GO_PGO_ITERATIONS, and BOLT_FIXTURE_TRAIN_ITERATIONS) are passed through.

The default quick mode never invokes perf or a PGO/BOLT training workload.
All writes stay in a private test directory. Recovery tests use fake roots and
mocked package/EFI tools; capability fixtures only build/profile local fixtures.
EOF
}

list_suites() {
    cat <<'EOF'
quick suites:
  bash-syntax (every .sh below bench/, optimization/, scripts/, and tests/)
  shellcheck (same shell source set; skipped when unavailable)
  python-source-compilation (temporary pycache only)
  python-unit-tests
  package-env-duplicate-policy
  driver-cli-self-test
  recovery-boot-evidence-fixture (fake root)
  recovery-rollback-fixture (fake root, including Clang/libc++ and GCC/libstdc++)

opt-in capability fixtures:
  clang-ir       Clang IR instrumentation PGO executable/DSO
  clang-sample   perf/llvm-profgen sample PGO
  gcc            GCC gcov PGO executable/DSO
  rust           Rust LLVM instrumentation PGO
  go             Go CPU-pprof PGO
  bolt           BOLT ET_EXEC, PIE, and DSO classes
EOF
}

fail_usage() {
    printf 'ERROR: %s\n\n' "$*" >&2
    usage >&2
    exit 2
}

is_capability() {
    local requested=$1
    local capability
    for capability in "${ALL_CAPABILITIES[@]}"; do
        [[ ${requested} == "${capability}" ]] && return 0
    done
    return 1
}

select_capability() {
    local requested=$1
    local capability
    if [[ ${requested} == all ]]; then
        for capability in "${ALL_CAPABILITIES[@]}"; do
            SELECTED_CAPABILITIES["${capability}"]=1
        done
    elif is_capability "${requested}"; then
        SELECTED_CAPABILITIES["${requested}"]=1
    else
        fail_usage "unknown capability: ${requested}"
    fi
    EXPLICIT_CAPABILITIES=1
}

parse_capability_list() {
    local value=$1
    local -a requested_capabilities=()
    local capability
    [[ -n ${value} ]] || return 0
    IFS=',' read -r -a requested_capabilities <<<"${value}"
    for capability in "${requested_capabilities[@]}"; do
        [[ -n ${capability} ]] || fail_usage 'empty capability in capability list'
        select_capability "${capability}"
    done
}

parse_capability_list "${OPTIMIZATION_TEST_CAPABILITIES:-}"
while (($#)); do
    case $1 in
        --mode)
            (($# >= 2)) || fail_usage '--mode requires an argument'
            MODE=$2
            shift 2
            ;;
        --capability)
            (($# >= 2)) || fail_usage '--capability requires an argument'
            select_capability "$2"
            shift 2
            ;;
        --output-dir)
            (($# >= 2)) || fail_usage '--output-dir requires an argument'
            OUTPUT_DIR=$2
            shift 2
            ;;
        --keep-temp)
            KEEP_TEMP=1
            shift
            ;;
        --list)
            LIST_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            (($# == 0)) || fail_usage "unexpected argument: $1"
            ;;
        *)
            fail_usage "unknown option: $1"
            ;;
    esac
done

case ${MODE} in
    quick|capabilities) ;;
    *) fail_usage "unknown mode: ${MODE}" ;;
esac

if ((LIST_ONLY)); then
    list_suites
    exit 0
fi

if [[ ${MODE} == capabilities && ${EXPLICIT_CAPABILITIES} -eq 0 ]]; then
    for capability in "${ALL_CAPABILITIES[@]}"; do
        SELECTED_CAPABILITIES["${capability}"]=1
    done
fi

resolve_executable() {
    local requested_tool=$1
    local resolved
    if [[ ${requested_tool} == */* ]]; then
        [[ -x ${requested_tool} && ! -d ${requested_tool} ]] || return 1
        resolved=${requested_tool}
    else
        resolved=$(command -v -- "${requested_tool}" 2>/dev/null) || return 1
        [[ -x ${resolved} && ! -d ${resolved} ]] || return 1
    fi
    RESOLVED_TOOL=${resolved}
}

require_commands() {
    local -a missing=()
    local command_name
    for command_name in "$@"; do
        if ! resolve_executable "${command_name}"; then
            missing+=("${command_name}")
        fi
    done
    if ((${#missing[@]})); then
        local joined
        joined=$(IFS=', '; printf '%s' "${missing[*]}")
        PREFLIGHT_REASON="missing required command(s): ${joined}"
        return 1
    fi
    return 0
}

create_run_root() {
    local canonical
    if [[ -n ${OUTPUT_DIR} ]]; then
        [[ ${OUTPUT_DIR} == /* && ${OUTPUT_DIR} != / ]] || \
            fail_usage '--output-dir must be an absolute non-root path'
        resolve_executable realpath || fail_usage 'realpath is required for --output-dir'
        canonical=$(${RESOLVED_TOOL} -m -- "${OUTPUT_DIR}")
        case ${canonical} in
            /tmp/*|/var/tmp/gentoo-optimization/*) ;;
            *) fail_usage '--output-dir must remain below /tmp or /var/tmp/gentoo-optimization' ;;
        esac
        [[ ${canonical} =~ ^/[A-Za-z0-9_./-]+$ ]] || \
            fail_usage '--output-dir canonical path contains characters unsafe for capability workloads'
        [[ ! -e ${canonical} ]] || fail_usage "--output-dir already exists: ${canonical}"
        mkdir -p -- "${canonical}"
        RUN_ROOT=${canonical}
    else
        RUN_ROOT=$(mktemp -d /tmp/gentoo-optimization-tests.XXXXXXXX)
        TEMP_ROOT_CREATED=1
    fi
    printf 'gentoo-optimization-test-root-v1\n' >"${RUN_ROOT}/.optimization-test-root"
    LOG_ROOT=${RUN_ROOT}/logs
    RESULTS_FILE=${RUN_ROOT}/results.tsv
    mkdir -p -- "${LOG_ROOT}" "${RUN_ROOT}/capabilities" \
        "${RUN_ROOT}/python-cache" "${RUN_ROOT}/preflight"
    printf 'status\ttest\tdetail\n' >"${RESULTS_FILE}"
}

# The EXIT/signal traps invoke this function indirectly.
# shellcheck disable=SC2329
cleanup() {
    local status=$?
    if ((TEMP_ROOT_CREATED)) && ((KEEP_TEMP == 0)) && ((FAIL_COUNT == 0)); then
        case ${RUN_ROOT} in
            /tmp/gentoo-optimization-tests.*)
                if [[ -f ${RUN_ROOT}/.optimization-test-root ]] && \
                    grep -Fxq 'gentoo-optimization-test-root-v1' \
                        "${RUN_ROOT}/.optimization-test-root"; then
                    rm -rf -- "${RUN_ROOT}"
                else
                    printf 'WARNING: refusing to clean unmarked test root: %s\n' \
                        "${RUN_ROOT}" >&2
                fi
                ;;
            *)
                printf 'WARNING: refusing to clean unexpected test root: %s\n' \
                    "${RUN_ROOT}" >&2
                ;;
        esac
    elif [[ -n ${RUN_ROOT} ]]; then
        printf 'EVIDENCE: %s\n' "${RUN_ROOT}"
    fi
    return "${status}"
}

create_run_root
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

safe_detail() {
    local detail=$1
    detail=${detail//$'\t'/ }
    detail=${detail//$'\n'/ }
    printf '%s' "${detail}"
}

record_result() {
    local status=$1 name=$2 detail=$3
    printf '%s\t%s\t%s\n' "${status}" "${name}" "$(safe_detail "${detail}")" \
        >>"${RESULTS_FILE}"
}

skip_case() {
    local name=$1 reason=$2
    ((SKIP_COUNT += 1))
    record_result SKIP "${name}" "${reason}"
    printf 'SKIP: %s — %s\n' "${name}" "${reason}"
}

run_case() {
    local name=$1
    shift
    local slug=${name//[^[:alnum:]_.-]/_}
    local log=${LOG_ROOT}/${slug}.log
    local status

    printf 'RUN:  %s\n' "${name}"
    {
        printf 'COMMAND'
        printf ' %q' "$@"
        printf '\n'
    } >"${log}"
    set +e
    "$@" >>"${log}" 2>&1
    status=$?
    set -e
    if ((status == 0)); then
        ((PASS_COUNT += 1))
        record_result PASS "${name}" "exit_status=0 log=${log}"
        printf 'PASS: %s\n' "${name}"
    else
        ((FAIL_COUNT += 1))
        KEEP_TEMP=1
        record_result FAIL "${name}" "exit_status=${status} log=${log}"
        printf 'FAIL: %s (exit %d; log: %s)\n' "${name}" "${status}" "${log}" >&2
        printf '%s\n' '----- failure log tail -----' >&2
        tail -n 240 -- "${log}" >&2 || true
        printf '%s\n' '----- end failure log -----' >&2
    fi
}

# run_case receives this function name as its command.
# shellcheck disable=SC2329
run_in_repository() {
    (
        cd -- "${REPOSITORY_ROOT}"
        "$@"
    )
}

declare -a SHELL_SOURCES=()
declare -a PYTHON_SOURCES=()
declare -a PYTHON_TEST_DIRECTORIES=()
while IFS= read -r -d '' source_file; do
    SHELL_SOURCES+=("${source_file}")
done < <(
    find "${REPOSITORY_ROOT}/bench" "${REPOSITORY_ROOT}/optimization" \
        "${REPOSITORY_ROOT}/scripts" "${REPOSITORY_ROOT}/tests" \
        -type f -name '*.sh' -print0 | LC_ALL=C sort -z
)
while IFS= read -r -d '' source_file; do
    PYTHON_SOURCES+=("${source_file}")
done < <(
    find "${REPOSITORY_ROOT}/optimization" "${REPOSITORY_ROOT}/scripts" \
        "${REPOSITORY_ROOT}/tests" -type f -name '*.py' -print0 | \
        LC_ALL=C sort -z
)
while IFS= read -r -d '' test_directory; do
    PYTHON_TEST_DIRECTORIES+=("${test_directory}")
done < <(
    find "${REPOSITORY_ROOT}/tests" -type f -name 'test_*.py' \
        -printf '%h\0' | LC_ALL=C sort -zu
)

if ((${#SHELL_SOURCES[@]} == 0)); then
    skip_case bash-syntax 'no shell sources were discovered in the repository test scope'
else
    for source_file in "${SHELL_SOURCES[@]}"; do
        relative_file=${source_file#"${REPOSITORY_ROOT}/"}
        run_case "bash-syntax:${relative_file}" bash -n -- "${source_file}"
    done
fi

if resolve_executable "${SHELLCHECK:-shellcheck}"; then
    SHELLCHECK_BIN=${RESOLVED_TOOL}
    run_case shellcheck "${SHELLCHECK_BIN}" -- "${SHELL_SOURCES[@]}"
else
    skip_case shellcheck \
        "${SHELLCHECK:-shellcheck} is not an executable in PATH; set SHELLCHECK=/absolute/path"
fi

PYTHON_BIN=
if ! resolve_executable python3; then
    skip_case python-source-compilation 'python3 is unavailable'
    skip_case python-unit-tests 'python3 is unavailable'
else
    PYTHON_BIN=${RESOLVED_TOOL}
    if ((${#PYTHON_SOURCES[@]} == 0)); then
        skip_case python-source-compilation \
            'no Python sources were discovered in the repository test scope'
    else
        run_case python-source-compilation env \
            PYTHONDONTWRITEBYTECODE=1 \
            PYTHONPYCACHEPREFIX="${RUN_ROOT}/python-cache/compile" \
            "${PYTHON_BIN}" -m py_compile "${PYTHON_SOURCES[@]}"
    fi
    if ((${#PYTHON_TEST_DIRECTORIES[@]} == 0)); then
        skip_case python-unit-tests 'no test_*.py source was discovered below tests/'
    else
        if ! resolve_executable zstd; then
            skip_case python-gpkg-zstd-subtests \
                'zstd is unavailable; GPKG stream unittest cases declare their own skip'
        fi
        for test_directory in "${PYTHON_TEST_DIRECTORIES[@]}"; do
            relative_directory=${test_directory#"${REPOSITORY_ROOT}/"}
            run_case "python-unit-tests:${relative_directory}" \
                run_in_repository env -u PYTHONPYCACHEPREFIX \
                PYTHONDONTWRITEBYTECODE=1 \
                "${PYTHON_BIN}" -m unittest discover \
                -s "${test_directory}" -p 'test_*.py' -v
        done
    fi
fi

PACKAGE_ENV_DUPLICATE_CHECKER=${REPOSITORY_ROOT}/scripts/optimization/check-package-env-duplicates.py
if [[ -z ${PYTHON_BIN} ]]; then
    skip_case package-env-duplicate-policy 'python3 is unavailable'
elif [[ ! -f ${PACKAGE_ENV_DUPLICATE_CHECKER} ]]; then
    skip_case package-env-duplicate-policy \
        "checker is absent: ${PACKAGE_ENV_DUPLICATE_CHECKER}"
else
    run_case package-env-duplicate-policy \
        run_in_repository "${PYTHON_BIN}" "${PACKAGE_ENV_DUPLICATE_CHECKER}"
fi

DRIVER_SELF_TEST=${REPOSITORY_ROOT}/tests/optimization/test-run-optimization-tests.sh
if [[ -f ${DRIVER_SELF_TEST} ]]; then
    run_case driver-cli-self-test bash -- "${DRIVER_SELF_TEST}"
else
    skip_case driver-cli-self-test "fixture is absent: ${DRIVER_SELF_TEST}"
fi

BOOT_EVIDENCE_FIXTURE=${REPOSITORY_ROOT}/optimization/fixtures/recovery/test-record-boot-evidence.sh
if [[ ! -f ${BOOT_EVIDENCE_FIXTURE} ]]; then
    skip_case recovery-boot-evidence-fixture \
        "fixture is absent: ${BOOT_EVIDENCE_FIXTURE}"
elif ! require_commands bash grep find sha256sum mktemp; then
    skip_case recovery-boot-evidence-fixture "${PREFLIGHT_REASON}"
else
    run_case recovery-boot-evidence-fixture bash -- "${BOOT_EVIDENCE_FIXTURE}"
fi

ROLLBACK_FIXTURE=${REPOSITORY_ROOT}/optimization/fixtures/recovery/test-rollback.sh
preflight_recovery_abi_lanes() {
    local clangxx_path gxx_path probe_source clang_output gcc_output
    local clang_compile_log clang_run_log gcc_compile_log gcc_run_log
    local status

    resolve_executable clang++ || {
        PREFLIGHT_REASON='Clang/libc++ ABI probe compiler is unavailable: clang++'
        return 1
    }
    clangxx_path=${RESOLVED_TOOL}
    resolve_executable g++ || {
        PREFLIGHT_REASON='GCC/libstdc++ ABI probe compiler is unavailable: g++'
        return 1
    }
    gxx_path=${RESOLVED_TOOL}

    probe_source=${RUN_ROOT}/preflight/recovery-cxx-abi-probe.cpp
    clang_output=${RUN_ROOT}/preflight/recovery-clang-libcxx-probe
    gcc_output=${RUN_ROOT}/preflight/recovery-gcc-libstdcxx-probe
    clang_compile_log=${RUN_ROOT}/preflight/recovery-clang-libcxx-compile.log
    clang_run_log=${RUN_ROOT}/preflight/recovery-clang-libcxx-run.log
    gcc_compile_log=${RUN_ROOT}/preflight/recovery-gcc-libstdcxx-compile.log
    gcc_run_log=${RUN_ROOT}/preflight/recovery-gcc-libstdcxx-run.log
    printf '%s\n' \
        '#include <iostream>' \
        'int main() { std::cout << "gentoo-recovery-cxx-abi-probe\n"; return 0; }' \
        >"${probe_source}"
    rm -f -- "${clang_output}" "${gcc_output}"

    if "${clangxx_path}" -O2 -pipe -stdlib=libc++ \
        "${probe_source}" -o "${clang_output}" >"${clang_compile_log}" 2>&1; then
        :
    else
        status=$?
        rm -f -- "${clang_output}"
        PREFLIGHT_REASON="Clang/libc++ ABI probe compilation failed (exit ${status}; log=${clang_compile_log})"
        return 1
    fi
    if [[ ! -x ${clang_output} ]]; then
        PREFLIGHT_REASON="Clang/libc++ ABI probe produced no executable (log=${clang_compile_log})"
        return 1
    fi
    if "${clang_output}" >"${clang_run_log}" 2>&1; then
        :
    else
        status=$?
        PREFLIGHT_REASON="Clang/libc++ ABI probe execution failed (exit ${status}; log=${clang_run_log})"
        return 1
    fi
    if [[ $(<"${clang_run_log}") != gentoo-recovery-cxx-abi-probe ]]; then
        PREFLIGHT_REASON="Clang/libc++ ABI probe returned unexpected output (log=${clang_run_log})"
        return 1
    fi

    if "${gxx_path}" -O2 -pipe \
        "${probe_source}" -o "${gcc_output}" >"${gcc_compile_log}" 2>&1; then
        :
    else
        status=$?
        rm -f -- "${gcc_output}"
        PREFLIGHT_REASON="GCC/libstdc++ ABI probe compilation failed (exit ${status}; log=${gcc_compile_log})"
        return 1
    fi
    if [[ ! -x ${gcc_output} ]]; then
        PREFLIGHT_REASON="GCC/libstdc++ ABI probe produced no executable (log=${gcc_compile_log})"
        return 1
    fi
    if "${gcc_output}" >"${gcc_run_log}" 2>&1; then
        :
    else
        status=$?
        PREFLIGHT_REASON="GCC/libstdc++ ABI probe execution failed (exit ${status}; log=${gcc_run_log})"
        return 1
    fi
    if [[ $(<"${gcc_run_log}") != gentoo-recovery-cxx-abi-probe ]]; then
        PREFLIGHT_REASON="GCC/libstdc++ ABI probe returned unexpected output (log=${gcc_run_log})"
        return 1
    fi

    {
        printf 'lane\tcompiler\tcompile_status\trun_status\toutput\n'
        printf 'clang-libcxx\t%s\t0\t0\t%s\n' "${clangxx_path}" "${clang_output}"
        printf 'gcc-libstdcxx\t%s\t0\t0\t%s\n' "${gxx_path}" "${gcc_output}"
    } >"${RUN_ROOT}/preflight/recovery-cxx-abi-probes.tsv"
    return 0
}

if [[ ! -f ${ROLLBACK_FIXTURE} ]]; then
    skip_case recovery-rollback-fixture "fixture is absent: ${ROLLBACK_FIXTURE}"
elif ! require_commands bash clang++ g++ readelf iconv od md5sum sha256sum \
    stat realpath flock rm; then
    skip_case recovery-rollback-fixture \
        "${PREFLIGHT_REASON}; the C++ ABI lane fixture was not run"
elif ! preflight_recovery_abi_lanes; then
    skip_case recovery-rollback-fixture \
        "${PREFLIGHT_REASON}; the C++ ABI lane fixture was not run"
else
    run_case recovery-rollback-fixture bash -- "${ROLLBACK_FIXTURE}"
fi

preflight_perf_branch_stack() {
    local perf_binary=$1
    local label=$2
    local probe_data=${RUN_ROOT}/preflight/${label}.perf.data
    local probe_log=${RUN_ROOT}/preflight/${label}.perf.log
    if ! "${perf_binary}" record -q -e cycles:u -j any,u \
        -o "${probe_data}" -- /bin/true >"${probe_log}" 2>&1; then
        local diagnostic
        diagnostic=$(tail -n 4 -- "${probe_log}" | tr '\n' ' ')
        PREFLIGHT_REASON="perf branch-stack probe is unavailable: ${diagnostic:-unknown error}"
        return 1
    fi
    return 0
}

preflight_clang_ir() {
    local clangxx=${CLANGXX:-clang++}
    local clang_path bindir profdata lld
    require_commands portageq rg readelf sha256sum readlink awk sed find sort tee || return 1
    if ! resolve_executable "${clangxx}"; then
        PREFLIGHT_REASON="Clang C++ driver is unavailable: ${clangxx}"
        return 1
    fi
    clang_path=${RESOLVED_TOOL}
    bindir=$(dirname -- "${clang_path}")
    profdata=${LLVM_PROFDATA:-${bindir}/llvm-profdata}
    lld=${LLD:-${bindir}/ld.lld}
    if [[ ! -x ${profdata} ]]; then
        PREFLIGHT_REASON="paired llvm-profdata is unavailable: ${profdata}"
        return 1
    fi
    if [[ ! -x ${lld} ]]; then
        PREFLIGHT_REASON="paired ld.lld is unavailable: ${lld}"
        return 1
    fi
    return 0
}

preflight_clang_sample() {
    local llvm_root=${LLVM_ROOT:-/usr/lib/llvm/22/bin}
    local clangxx=${CLANGXX:-${llvm_root}/clang++}
    local profgen=${PROFGEN:-${llvm_root}/llvm-profgen}
    local profdata=${PROFDATA:-${llvm_root}/llvm-profdata}
    local readelf_tool=${READELF:-${llvm_root}/llvm-readelf}
    local perf_tool=${PERF:-/usr/bin/perf}
    local rg_tool=${RG:-/usr/bin/rg}
    local candidate
    for candidate in "${clangxx}" "${profgen}" "${profdata}" \
        "${readelf_tool}" "${perf_tool}" "${rg_tool}"; do
        if [[ ! -x ${candidate} ]]; then
            PREFLIGHT_REASON="required Clang sample-PGO tool is unavailable: ${candidate}"
            return 1
        fi
    done
    preflight_perf_branch_stack "${perf_tool}" clang-sample || return 1
    return 0
}

preflight_gcc() {
    require_commands gcc-config gcc python3 jq readelf nm rg sha256sum realpath || return 1
    [[ -d /var/db/pkg/sys-devel ]] || {
        PREFLIGHT_REASON='Gentoo installed-package metadata is absent: /var/db/pkg/sys-devel'
        return 1
    }
    if ! gcc-config -c >/dev/null 2>&1; then
        PREFLIGHT_REASON='gcc-config cannot report an active compiler selection'
        return 1
    fi
    return 0
}

preflight_rust() {
    local rustc=${RUSTC:-rustc}
    local rustc_path version_info llvm_major profdata profdata_version
    require_commands cargo rg sha256sum readelf awk sed head || return 1
    if ! resolve_executable "${rustc}"; then
        PREFLIGHT_REASON="rustc is unavailable: ${rustc}"
        return 1
    fi
    rustc_path=${RESOLVED_TOOL}
    if ! version_info=$("${rustc_path}" -vV 2>/dev/null); then
        PREFLIGHT_REASON="rustc -vV failed: ${rustc_path}"
        return 1
    fi
    llvm_major=$(sed -n 's/^LLVM version: \([0-9][0-9]*\).*/\1/p' <<<"${version_info}")
    [[ -n ${llvm_major} ]] || {
        PREFLIGHT_REASON='rustc does not report a bundled LLVM major'
        return 1
    }
    if [[ -n ${LLVM_PROFDATA:-} ]]; then
        profdata=${LLVM_PROFDATA}
    elif [[ -x /usr/lib/llvm/${llvm_major}/bin/llvm-profdata ]]; then
        profdata=/usr/lib/llvm/${llvm_major}/bin/llvm-profdata
    elif resolve_executable llvm-profdata; then
        profdata=${RESOLVED_TOOL}
    else
        PREFLIGHT_REASON="llvm-profdata for rustc LLVM ${llvm_major} is unavailable"
        return 1
    fi
    if ! profdata_version=$("${profdata}" --version 2>/dev/null) || \
        [[ ${profdata_version} != *"LLVM version ${llvm_major}."* ]]; then
        PREFLIGHT_REASON="llvm-profdata does not match rustc LLVM ${llvm_major}: ${profdata}"
        return 1
    fi
    return 0
}

preflight_go() {
    local go_tool=${GO:-go}
    local go_path goos goarch help_text
    require_commands rg sha256sum readelf || return 1
    if ! resolve_executable "${go_tool}"; then
        PREFLIGHT_REASON="Go tool is unavailable: ${go_tool}"
        return 1
    fi
    go_path=${RESOLVED_TOOL}
    goos=$("${go_path}" env GOOS 2>/dev/null) || {
        PREFLIGHT_REASON="go env GOOS failed: ${go_path}"
        return 1
    }
    goarch=$("${go_path}" env GOARCH 2>/dev/null) || {
        PREFLIGHT_REASON="go env GOARCH failed: ${go_path}"
        return 1
    }
    [[ ${goos}/${goarch} == linux/amd64 ]] || {
        PREFLIGHT_REASON="Go fixture requires linux/amd64, found ${goos}/${goarch}"
        return 1
    }
    help_text=$("${go_path}" help build 2>/dev/null) || {
        PREFLIGHT_REASON='go help build failed'
        return 1
    }
    [[ ${help_text} == *'-pgo'* ]] || {
        PREFLIGHT_REASON='active Go toolchain does not advertise -pgo support'
        return 1
    }
    return 0
}

preflight_bolt() {
    local runner=${REPOSITORY_ROOT}/optimization/fixtures/bolt/run.sh
    local perf_path
    [[ -f ${runner} ]] || {
        PREFLIGHT_REASON="combined BOLT fixture runner is absent: ${runner}"
        return 1
    }
    require_commands awk bash chmod clang cmp cp dirname file find getcap \
        getfattr grep head lddtree llvm-bolt merge-fdata mkdir nm objcopy perf \
        perf2bolt readelf readlink rm sed setfattr sha256sum sort stat strip tail \
        timeout tr mv xargs || return 1
    resolve_executable perf
    perf_path=${RESOLVED_TOOL}
    preflight_perf_branch_stack "${perf_path}" bolt || return 1
    return 0
}

run_capability() {
    local capability=$1
    local runner output description
    if [[ -z ${SELECTED_CAPABILITIES[${capability}]:-} ]]; then
        if [[ ${MODE} == quick && ${EXPLICIT_CAPABILITIES} -eq 0 ]]; then
            skip_case "capability:${capability}" \
                'quick mode excludes profiling/training; use --mode capabilities or --capability'
        else
            skip_case "capability:${capability}" 'not selected by the explicit capability filter'
        fi
        return 0
    fi

    output=${RUN_ROOT}/capabilities/${capability}
    case ${capability} in
        clang-ir)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/pgo/clang-ir/run.sh
            description='Clang IR instrumentation PGO executable/DSO'
            if ! preflight_clang_ir; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        clang-sample)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/pgo/clang-sample/run.sh
            description='Clang sample PGO with perf branch stacks'
            if ! preflight_clang_sample; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        gcc)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/pgo/gcc/run.sh
            description='GCC gcov PGO executable/DSO'
            if ! preflight_gcc; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        rust)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/pgo/rust/run.sh
            description='Rust LLVM instrumentation PGO'
            if ! preflight_rust; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        go)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/pgo/go/run.sh
            description='Go CPU-pprof PGO'
            if ! preflight_go; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        bolt)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/bolt/run.sh
            description='BOLT ET_EXEC, PIE, and DSO classes'
            if ! preflight_bolt; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        *)
            printf 'internal error: unhandled capability %s\n' "${capability}" >&2
            exit 2
            ;;
    esac
    if [[ ! -f ${runner} ]]; then
        skip_case "capability:${capability}" "fixture runner is absent: ${runner}"
        return 0
    fi
    run_case "capability:${capability}:${description}" bash -- "${runner}" "${output}"
}

for capability in "${ALL_CAPABILITIES[@]}"; do
    run_capability "${capability}"
done

TOTAL_COUNT=$((PASS_COUNT + FAIL_COUNT + SKIP_COUNT))
EXIT_STATUS=0
((FAIL_COUNT == 0)) || EXIT_STATUS=1
{
    printf 'mode=%s\n' "${MODE}"
    printf 'pass=%d\nfail=%d\nskip=%d\ntotal=%d\n' \
        "${PASS_COUNT}" "${FAIL_COUNT}" "${SKIP_COUNT}" "${TOTAL_COUNT}"
    printf 'exit_status=%d\n' "${EXIT_STATUS}"
    printf 'results=%s\n' "${RESULTS_FILE}"
} | tee "${RUN_ROOT}/summary.txt"

printf 'SUMMARY: PASS=%d FAIL=%d SKIP=%d TOTAL=%d EXIT=%d\n' \
    "${PASS_COUNT}" "${FAIL_COUNT}" "${SKIP_COUNT}" "${TOTAL_COUNT}" \
    "${EXIT_STATUS}"
exit "${EXIT_STATUS}"

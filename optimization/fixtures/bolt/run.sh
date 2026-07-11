#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

require_tool() {
    local name=$1
    local resolved
    resolved=$(command -v -- "${name}" 2>/dev/null) || fail "missing required tool: ${name}"
    # perf2bolt is intentionally an argv[0]-dispatched symlink to llvm-bolt.
    # Preserve the command path here; canonicalizing it changes program mode.
    printf '%s\n' "${resolved}"
}

run() {
    {
        printf 'RUN'
        printf ' %q' "$@"
        printf '\n'
    } >>"${COMMAND_LOG}"
    "$@"
}

run_capture() {
    local output=$1
    shift
    {
        printf 'RUN'
        printf ' %q' "$@"
        printf '\n'
    } >>"${COMMAND_LOG}"
    "$@" >"${output}" 2>&1
}

run_split_capture() {
    local stdout_file=$1
    local stderr_file=$2
    shift 2
    {
        printf 'RUN'
        printf ' %q' "$@"
        printf '\n'
    } >>"${COMMAND_LOG}"
    "$@" >"${stdout_file}" 2>"${stderr_file}"
}

section_text_sha256() {
    local input=$1
    local destination=$2
    # Supplying /dev/null as the explicit objcopy output keeps the input
    # byte-for-byte immutable while extracting the raw section.
    run "${OBJCOPY}" --dump-section ".text=${destination}" "${input}" /dev/null
    [[ -s ${destination} ]] || fail "empty .text section extracted from ${input}"
    "${SHA256SUM}" "${destination}" | awk '{print $1}'
}

gnu_build_id() {
    local input=$1
    local build_id
    build_id=$("${READELF}" -n -- "${input}" | sed -n 's/.*Build ID: //p' | head -n 1)
    [[ ${build_id} =~ ^[0-9A-Fa-f]+$ ]] || fail "missing hexadecimal GNU build ID: ${input}"
    printf '%s\n' "${build_id,,}"
}

record_elf() {
    local input=$1
    local prefix=$2
    run_capture "${prefix}-file.txt" "${FILE_TOOL}" -L -- "${input}"
    run_capture "${prefix}-header.txt" "${READELF}" -hW -- "${input}"
    run_capture "${prefix}-sections.txt" "${READELF}" -SW -- "${input}"
    run_capture "${prefix}-notes.txt" "${READELF}" -nW -- "${input}"
    run_capture "${prefix}-dynamic.txt" "${READELF}" -dW -- "${input}"
    run_capture "${prefix}-symbols.txt" "${NM}" -an -- "${input}"
    run_capture "${prefix}-dynamic-symbols.txt" "${NM}" -D --defined-only -- "${input}"
    run_capture "${prefix}-lddtree.txt" "${LDDTREE}" -- "${input}"
    run_capture "${prefix}-stat.txt" stat -Lc '%u:%g %a %f %s %n' -- "${input}"
    run_capture "${prefix}-xattrs.txt" "${GETFATTR}" --absolute-names --encoding=hex -d -- "${input}"
    run_capture "${prefix}-capabilities.txt" "${GETCAP}" -n -- "${input}"
}

normalized_needed() {
    local input=$1
    "${READELF}" -dW -- "${input}" |
        sed -n 's/.*Shared library: \[\(.*\)\]/\1/p' |
        LC_ALL=C sort
}

normalized_dynamic_symbols() {
    local input=$1
    "${NM}" -D --defined-only -- "${input}" |
        awk '{ if (NF >= 2) print $(NF-1), $NF }' |
        LC_ALL=C sort
}

normalized_xattrs() {
    local input=$1
    "${GETFATTR}" --absolute-names --encoding=hex -d -- "${input}" 2>/dev/null |
        sed '/^# file: /d;/^$/d' |
        LC_ALL=C sort
}

assert_input_shape() {
    local class=$1
    local input=$2
    local header=$3
    local sections=$4
    grep -Fq 'Class:                             ELF64' "${header}" || fail "${class}: input is not ELF64"
    grep -Fq 'Machine:                           Advanced Micro Devices X86-64' "${header}" || fail "${class}: input is not x86-64"
    case ${class} in
        executable)
            grep -Eq 'Type:[[:space:]]+EXEC ' "${header}" || fail 'fixed executable is not ET_EXEC'
            ;;
        pie|dso)
            grep -Eq 'Type:[[:space:]]+DYN ' "${header}" || fail "${class}: input is not ET_DYN"
            ;;
        *)
            fail "unknown fixture class: ${class}"
            ;;
    esac
    grep -Eq '[.]rel(a)?[.]text' "${sections}" || fail "${class}: emitted text relocations are absent"
    grep -Eq '[.]symtab' "${sections}" || fail "${class}: full symbol table is absent"
    gnu_build_id "${input}" >/dev/null
}

apply_reference_metadata() {
    local reference=$1
    local output=$2
    run cp --attributes-only --preserve=mode,ownership,timestamps,xattr -- "${reference}" "${output}"
}

validate_output() {
    local class=$1
    local input=$2
    local output=$3
    local record_root=$4
    local runtime_command=$5
    local expected_output=$6

    [[ -s ${output} ]] || fail "${class}: BOLT output is absent or empty"
    record_elf "${output}" "${record_root}/output"
    grep -Fq '.note.bolt_info' "${record_root}/output-sections.txt" || fail "${class}: .note.bolt_info is absent"
    "${READELF}" -hW -- "${input}" | sed -n '/Class:/p;/Type:/p;/Machine:/p' >"${record_root}/input-identity.txt"
    "${READELF}" -hW -- "${output}" | sed -n '/Class:/p;/Type:/p;/Machine:/p' >"${record_root}/output-identity.txt"
    cmp -s -- "${record_root}/input-identity.txt" "${record_root}/output-identity.txt" || fail "${class}: ELF class/type/machine changed"

    normalized_needed "${input}" >"${record_root}/input-needed.txt"
    normalized_needed "${output}" >"${record_root}/output-needed.txt"
    cmp -s -- "${record_root}/input-needed.txt" "${record_root}/output-needed.txt" || fail "${class}: DT_NEEDED changed"

    if [[ ${class} == dso ]]; then
        normalized_dynamic_symbols "${input}" >"${record_root}/input-exported-abi.txt"
        normalized_dynamic_symbols "${output}" >"${record_root}/output-exported-abi.txt"
        cmp -s -- "${record_root}/input-exported-abi.txt" "${record_root}/output-exported-abi.txt" || fail 'dso: exported ABI changed'
        grep -Fq 'bolt_fixture_run' "${record_root}/output-exported-abi.txt" || fail 'dso: fixture export is absent'
    fi

    [[ $(stat -Lc '%u:%g %a' -- "${input}") == "$(stat -Lc '%u:%g %a' -- "${output}")" ]] || fail "${class}: ownership intent or mode changed"
    normalized_xattrs "${input}" >"${record_root}/input-xattrs-normalized.txt"
    normalized_xattrs "${output}" >"${record_root}/output-xattrs-normalized.txt"
    cmp -s -- "${record_root}/input-xattrs-normalized.txt" "${record_root}/output-xattrs-normalized.txt" || fail "${class}: xattrs changed"
    [[ -z $("${GETCAP}" -n -- "${input}") && -z $("${GETCAP}" -n -- "${output}") ]] || fail "${class}: unexpected file capability"

    run_capture "${record_root}/output-functional.txt" bash -c "${runtime_command}"
    cmp -s -- "${expected_output}" "${record_root}/output-functional.txt" || fail "${class}: BOLT output changed workload result"
}

profile_and_bolt() {
    local class=$1
    local input=$2
    local mode1_command=$3
    local mode2_command=$4
    local runtime_command=$5
    local expected_output=$6
    local class_root=${OUTPUT_ROOT}/${class}
    local input_build_id input_sha input_sha_after_profile input_text_sha output_text_sha
    mkdir -p -- "${class_root}"

    run "${SETFATTR}" -n user.gentoo_optimization_fixture -v "bolt-${class}" -- "${input}"
    record_elf "${input}" "${class_root}/input"
    assert_input_shape "${class}" "${input}" "${class_root}/input-header.txt" "${class_root}/input-sections.txt"
    input_build_id=$(gnu_build_id "${input}")
    input_sha=$("${SHA256SUM}" "${input}" | awk '{print $1}')
    input_text_sha=$(section_text_sha256 "${input}" "${class_root}/input.text")

    run_capture "${class_root}/perf-mode1.log" "${PERF}" record -q -e cycles:u -j any,u -o "${class_root}/mode1.perf.data" -- bash -c "${mode1_command}"
    run_capture "${class_root}/perf-mode2.log" "${PERF}" record -q -e cycles:u -j any,u -o "${class_root}/mode2.perf.data" -- bash -c "${mode2_command}"
    [[ -s ${class_root}/mode1.perf.data && -s ${class_root}/mode2.perf.data ]] || fail "${class}: perf data is empty"
    run_capture "${class_root}/perf-report.txt" "${PERF}" report --stdio --no-children --sort dso,symbol -i "${class_root}/mode1.perf.data"
    run_capture "${class_root}/perf-buildids-mode1.txt" "${PERF}" buildid-list -i "${class_root}/mode1.perf.data"
    run_capture "${class_root}/perf-buildids-mode2.txt" "${PERF}" buildid-list -i "${class_root}/mode2.perf.data"
    grep -Fiq -- "${input_build_id}" "${class_root}/perf-buildids-mode1.txt" || fail "${class}: mode1 perf data lacks the exact input build ID"
    grep -Fiq -- "${input_build_id}" "${class_root}/perf-buildids-mode2.txt" || fail "${class}: mode2 perf data lacks the exact input build ID"

    run_capture "${class_root}/perf2bolt-mode1.log" "${PERF2BOLT}" -p "${class_root}/mode1.perf.data" -o "${class_root}/mode1.fdata" "${input}"
    run_capture "${class_root}/perf2bolt-mode2.log" "${PERF2BOLT}" -p "${class_root}/mode2.perf.data" -o "${class_root}/mode2.fdata" "${input}"
    [[ -s ${class_root}/mode1.fdata && -s ${class_root}/mode2.fdata ]] || fail "${class}: perf2bolt produced empty fdata"
    run_split_capture "${class_root}/merged.fdata" "${class_root}/merge-fdata.log" \
        "${MERGE_FDATA}" "${class_root}/mode1.fdata" "${class_root}/mode2.fdata"
    [[ -s ${class_root}/merged.fdata ]] || fail "${class}: merged fdata is empty"
    grep -Eq 'bolt_fixture_run|hot_even|hot_odd' "${class_root}/merged.fdata" || fail "${class}: merged fdata lacks fixture functions"
    input_sha_after_profile=$("${SHA256SUM}" "${input}" | awk '{print $1}')
    [[ ${input_sha} == "${input_sha_after_profile}" ]] || fail "${class}: exact input changed during profiling/conversion"

    run_capture "${class_root}/llvm-bolt.log" \
        "${LLVM_BOLT}" "${input}" \
        -o "${class_root}/output.bolt" \
        -data="${class_root}/merged.fdata" \
        -reorder-blocks=ext-tsp \
        -reorder-functions=hfsort+ \
        -split-functions \
        -split-all-cold \
        -split-eh \
        -update-debug-sections \
        -dyno-stats
    apply_reference_metadata "${input}" "${class_root}/output.bolt"
    output_text_sha=$(section_text_sha256 "${class_root}/output.bolt" "${class_root}/output.text")
    [[ ${input_text_sha} != "${output_text_sha}" ]] || fail "${class}: BOLT did not change the .text image"
    validate_output "${class}" "${input}" "${class_root}/output.bolt" "${class_root}" "${runtime_command}" "${expected_output}"

    {
        printf 'class=%s\n' "${class}"
        printf 'result=PASS\n'
        printf 'input=%s\n' "${input}"
        printf 'input_sha256=%s\n' "${input_sha}"
        printf 'input_build_id=%s\n' "${input_build_id}"
        printf 'input_text_sha256=%s\n' "${input_text_sha}"
        printf 'output=%s\n' "${class_root}/output.bolt"
        printf 'output_build_id=%s\n' "$(gnu_build_id "${class_root}/output.bolt")"
        printf 'output_text_sha256=%s\n' "${output_text_sha}"
        printf 'bolt_note=true\nfunctionality=true\ndynamic_dependencies_preserved=true\n'
        printf 'ownership_mode_xattrs_preserved=true\n'
        printf 'exported_abi_preserved=%s\n' "$([[ ${class} == dso ]] && printf true || printf not-applicable)"
    } >"${class_root}/summary.txt"
}

[[ $# -eq 1 ]] || fail "usage: ${0##*/} /tmp/unique-output-directory"
OUTPUT_ROOT=$1
[[ ${OUTPUT_ROOT} == /* ]] || fail 'output directory must be absolute'
OUTPUT_ROOT=$(readlink -m -- "${OUTPUT_ROOT}")
[[ ${OUTPUT_ROOT} =~ ^/[A-Za-z0-9_./-]+$ ]] || \
    fail 'output directory contains characters unsafe for recorded workload commands'
case ${OUTPUT_ROOT} in
    /tmp/*|/var/tmp/gentoo-optimization/*)
        ;;
    *)
        fail 'output directory must be below /tmp or /var/tmp/gentoo-optimization'
        ;;
esac
[[ ! -e ${OUTPUT_ROOT} ]] || fail "refusing existing output directory: ${OUTPUT_ROOT}"
mkdir -p -- "${OUTPUT_ROOT}/build" "${OUTPUT_ROOT}/dso-runtime"
COMMAND_LOG=${OUTPUT_ROOT}/commands.log
: >"${COMMAND_LOG}"

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CLANG=$(require_tool clang)
PERF=$(require_tool perf)
PERF2BOLT=$(require_tool perf2bolt)
LLVM_BOLT=$(require_tool llvm-bolt)
MERGE_FDATA=$(require_tool merge-fdata)
READELF=$(require_tool readelf)
OBJCOPY=$(require_tool objcopy)
NM=$(require_tool nm)
LDDTREE=$(require_tool lddtree)
FILE_TOOL=$(require_tool file)
GETFATTR=$(require_tool getfattr)
SETFATTR=$(require_tool setfattr)
GETCAP=$(require_tool getcap)
SHA256SUM=$(require_tool sha256sum)

"${SHA256SUM}" \
    "${SCRIPT_DIR}/run.sh" \
    "${SCRIPT_DIR}/fixture.h" \
    "${SCRIPT_DIR}/fixture.c" \
    "${SCRIPT_DIR}/main.c" >"${OUTPUT_ROOT}/source-files.sha256"

TRAIN_ITERATIONS=${BOLT_FIXTURE_TRAIN_ITERATIONS:-120000000}
[[ ${TRAIN_ITERATIONS} =~ ^[1-9][0-9]*$ ]] || fail 'BOLT_FIXTURE_TRAIN_ITERATIONS must be a positive integer'
VERIFY_ITERATIONS=2000000

run_capture "${OUTPUT_ROOT}/clang-version.txt" "${CLANG}" --version
run_capture "${OUTPUT_ROOT}/perf-version.txt" "${PERF}" --version
run_capture "${OUTPUT_ROOT}/llvm-bolt-version.txt" "${LLVM_BOLT}" --version
run_capture "${OUTPUT_ROOT}/perf2bolt-help.txt" "${PERF2BOLT}" --help
run_capture "${OUTPUT_ROOT}/merge-fdata-help.txt" "${MERGE_FDATA}" --help

common_flags=(
    -O3
    -g
    -fno-omit-frame-pointer
    -fno-optimize-sibling-calls
    -ffunction-sections
    -fdata-sections
    -Wall
    -Wextra
    -Werror
    '-Wl,--build-id=sha1'
    '-Wl,--emit-relocs'
    '-Wl,-z,now'
    '-Wl,-z,relro'
)

run "${CLANG}" "${common_flags[@]}" -fno-pie -no-pie \
    "${SCRIPT_DIR}/main.c" "${SCRIPT_DIR}/fixture.c" \
    -o "${OUTPUT_ROOT}/build/fixture-exec"
run chmod 0751 "${OUTPUT_ROOT}/build/fixture-exec"

run "${CLANG}" "${common_flags[@]}" -fPIE -pie \
    "${SCRIPT_DIR}/main.c" "${SCRIPT_DIR}/fixture.c" \
    -o "${OUTPUT_ROOT}/build/fixture-pie"
run chmod 0755 "${OUTPUT_ROOT}/build/fixture-pie"

run "${CLANG}" "${common_flags[@]}" -fPIC -shared \
    -Wl,-soname,libboltfixture.so \
    "${SCRIPT_DIR}/fixture.c" \
    -o "${OUTPUT_ROOT}/build/libboltfixture.so"
run chmod 0750 "${OUTPUT_ROOT}/build/libboltfixture.so"
# Keep $ORIGIN literal for the fixture driver's runtime lookup.
# shellcheck disable=SC2016
run "${CLANG}" "${common_flags[@]}" -fPIE -pie \
    "${SCRIPT_DIR}/main.c" \
    -L"${OUTPUT_ROOT}/build" -lboltfixture '-Wl,-rpath,$ORIGIN' \
    -o "${OUTPUT_ROOT}/build/fixture-dso-driver"

run_capture "${OUTPUT_ROOT}/executable-expected.txt" \
    "${OUTPUT_ROOT}/build/fixture-exec" "${VERIFY_ITERATIONS}" 1
run_capture "${OUTPUT_ROOT}/pie-expected.txt" \
    "${OUTPUT_ROOT}/build/fixture-pie" "${VERIFY_ITERATIONS}" 1
run_capture "${OUTPUT_ROOT}/dso-expected.txt" \
    "${OUTPUT_ROOT}/build/fixture-dso-driver" "${VERIFY_ITERATIONS}" 1

profile_and_bolt executable \
    "${OUTPUT_ROOT}/build/fixture-exec" \
    "${OUTPUT_ROOT}/build/fixture-exec ${TRAIN_ITERATIONS} 1" \
    "${OUTPUT_ROOT}/build/fixture-exec ${TRAIN_ITERATIONS} 2" \
    "${OUTPUT_ROOT}/executable/output.bolt ${VERIFY_ITERATIONS} 1" \
    "${OUTPUT_ROOT}/executable-expected.txt"

profile_and_bolt pie \
    "${OUTPUT_ROOT}/build/fixture-pie" \
    "${OUTPUT_ROOT}/build/fixture-pie ${TRAIN_ITERATIONS} 1" \
    "${OUTPUT_ROOT}/build/fixture-pie ${TRAIN_ITERATIONS} 2" \
    "${OUTPUT_ROOT}/pie/output.bolt ${VERIFY_ITERATIONS} 1" \
    "${OUTPUT_ROOT}/pie-expected.txt"

profile_and_bolt dso \
    "${OUTPUT_ROOT}/build/libboltfixture.so" \
    "${OUTPUT_ROOT}/build/fixture-dso-driver ${TRAIN_ITERATIONS} 1" \
    "${OUTPUT_ROOT}/build/fixture-dso-driver ${TRAIN_ITERATIONS} 2" \
    "cp -- ${OUTPUT_ROOT}/build/fixture-dso-driver ${OUTPUT_ROOT}/dso-runtime/fixture-dso-driver && cp -- ${OUTPUT_ROOT}/dso/output.bolt ${OUTPUT_ROOT}/dso-runtime/libboltfixture.so && ${OUTPUT_ROOT}/dso-runtime/fixture-dso-driver ${VERIFY_ITERATIONS} 1" \
    "${OUTPUT_ROOT}/dso-expected.txt"

{
    printf 'result=PASS\n'
    printf 'fixture_classes=executable,pie,dso\n'
    printf 'training_iterations_per_mode=%s\n' "${TRAIN_ITERATIONS}"
    printf 'profiles_per_class=2\n'
    printf 'tool_llvm_bolt=%s\n' "${LLVM_BOLT}"
    printf 'tool_llvm_bolt_realpath=%s\n' "$(readlink -f -- "${LLVM_BOLT}")"
    printf 'tool_perf2bolt=%s\n' "${PERF2BOLT}"
    printf 'tool_perf2bolt_realpath=%s\n' "$(readlink -f -- "${PERF2BOLT}")"
    printf 'tool_merge_fdata=%s\n' "${MERGE_FDATA}"
    printf 'all_bolt_notes=true\nall_functionality=true\nall_metadata=true\nall_dynamic_dependencies=true\n'
} >"${OUTPUT_ROOT}/validation-summary.txt"

find "${OUTPUT_ROOT}" -type f ! -name evidence.sha256 -print0 |
    LC_ALL=C sort -z |
    xargs -0 "${SHA256SUM}" >"${OUTPUT_ROOT}/evidence.sha256"
"${SHA256SUM}" -c "${OUTPUT_ROOT}/evidence.sha256" >"${OUTPUT_ROOT}/evidence-verification.txt"
printf 'PASS: BOLT executable, PIE, and DSO fixture suite (%s)\n' "${OUTPUT_ROOT}"

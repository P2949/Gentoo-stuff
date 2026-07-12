#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=optimization/fixtures/bolt/transaction.sh
source "${SCRIPT_DIR}/transaction.sh"

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

run_expect_status() {
    local expected_status=$1
    local output=$2
    shift 2
    local status
    if run_capture "${output}" "$@"; then
        status=0
    else
        status=$?
    fi
    [[ ${status} -eq ${expected_status} ]] || \
        fail "expected exit ${expected_status}, received ${status}: $*"
}

section_sha256() {
    local input=$1
    local section=$2
    local destination=$3
    # Supplying /dev/null as the explicit objcopy output keeps the input
    # byte-for-byte immutable while extracting the raw section.
    run "${OBJCOPY}" --dump-section "${section}=${destination}" "${input}" /dev/null
    [[ -s ${destination} ]] || fail "empty ${section} section extracted from ${input}"
    "${SHA256SUM}" "${destination}" | awk '{print $1}'
}

section_text_sha256() {
    section_sha256 "$1" .text "$2"
}

gnu_build_id() {
    local input=$1
    local build_id
    build_id=$("${READELF}" -n -- "${input}" |
        sed -n 's/.*Build ID: //p' |
        sed -n '1p')
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
    run_capture "${prefix}-program-headers.txt" "${READELF}" -lW -- "${input}"
    run_capture "${prefix}-dynamic.txt" "${READELF}" -dW -- "${input}"
    run_capture "${prefix}-symbol-versions.txt" "${READELF}" --version-info -W -- "${input}"
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

normalized_dynamic_policy() {
    local input=$1
    "${READELF}" -dW -- "${input}" |
        grep -E '[(](SONAME|RPATH|RUNPATH|FLAGS|FLAGS_1)[)]' |
        sed 's/^[[:space:]]*//' |
        LC_ALL=C sort || true
}

normalized_interpreter() {
    local input=$1
    "${READELF}" -lW -- "${input}" |
        sed -n 's/.*Requesting program interpreter: \(.*\)]/\1/p'
}

normalized_gnu_stack() {
    local input=$1
    "${READELF}" -lW -- "${input}" |
        awk '$1 == "GNU_STACK" { print $(NF - 1) }'
}

normalized_gnu_relro() {
    local input=$1
    "${READELF}" -lW -- "${input}" |
        awk '$1 == "GNU_RELRO" { print $1, $(NF - 1) }'
}

normalized_lddtree() {
    local input=$1
    "${LDDTREE}" -- "${input}" 2>/dev/null |
        sed '1d;s/^[[:space:]]*//' |
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
    local label=${5:-${class}}
    local interpreter soname gnu_stack gnu_relro
    grep -Fq 'Class:                             ELF64' "${header}" || fail "${label}: input is not ELF64"
    grep -Fq 'Machine:                           Advanced Micro Devices X86-64' "${header}" || fail "${label}: input is not x86-64"
    interpreter=$(normalized_interpreter "${input}")
    soname=$("${READELF}" -dW -- "${input}" |
        sed -n 's/.*(SONAME).*\[\(.*\)\]/\1/p')
    case ${class} in
        executable)
            grep -Eq 'Type:[[:space:]]+EXEC ' "${header}" || fail "${label}: fixed executable is not ET_EXEC"
            [[ -n ${interpreter} ]] || fail "${label}: ET_EXEC fixture lacks PT_INTERP"
            ! "${READELF}" -dW -- "${input}" | grep -E '[(]FLAGS_1[)].*Flags:.*PIE' >/dev/null || \
                fail "${label}: fixed executable unexpectedly carries DF_1_PIE"
            ;;
        pie)
            grep -Eq 'Type:[[:space:]]+DYN ' "${header}" || fail "${label}: PIE is not ET_DYN"
            [[ -n ${interpreter} ]] || fail "${label}: PIE lacks PT_INTERP"
            "${READELF}" -dW -- "${input}" | grep -E '[(]FLAGS_1[)].*Flags:.*PIE' >/dev/null || \
                fail "${label}: ET_DYN executable lacks DF_1_PIE"
            ;;
        dso)
            grep -Eq 'Type:[[:space:]]+DYN ' "${header}" || fail "${label}: DSO is not ET_DYN"
            [[ -z ${interpreter} ]] || fail "${label}: DSO unexpectedly carries PT_INTERP"
            [[ ${soname} == libboltfixture.so ]] || \
                fail "${label}: DSO SONAME is not libboltfixture.so: ${soname:-absent}"
            ! "${READELF}" -dW -- "${input}" | grep -E '[(]FLAGS_1[)].*Flags:.*PIE' >/dev/null || \
                fail "${label}: DSO unexpectedly carries DF_1_PIE"
            ;;
        *)
            fail "unknown fixture class: ${class}"
            ;;
    esac
    grep -Eq '[.]rel(a)?[.]text' "${sections}" || fail "${label}: emitted text relocations are absent"
    grep -Eq '[.]symtab' "${sections}" || fail "${label}: full symbol table is absent"
    gnu_stack=$(normalized_gnu_stack "${input}")
    [[ ${gnu_stack} == RW ]] || fail "${label}: GNU_STACK policy is not exactly RW: ${gnu_stack:-absent}"
    gnu_relro=$(normalized_gnu_relro "${input}")
    [[ ${gnu_relro} == 'GNU_RELRO R' ]] || \
        fail "${label}: GNU_RELRO policy is not exactly read-only: ${gnu_relro:-absent}"
    "${READELF}" -nW -- "${input}" | grep -E 'x86 feature:.*IBT' >/dev/null || \
        fail "${label}: GNU property does not declare IBT"
    "${READELF}" -nW -- "${input}" | grep -E 'x86 feature:.*SHSTK' >/dev/null || \
        fail "${label}: GNU property does not declare SHSTK"
    gnu_build_id "${input}" >/dev/null
}

apply_reference_metadata() {
    local reference=$1
    local output=$2
    run cp --attributes-only --preserve=mode,ownership,timestamps,xattr -- "${reference}" "${output}"
}

percentage_at_most() {
    local value=$1
    local limit=$2
    awk -v value="${value}" -v limit="${limit}" 'BEGIN { exit !(value + 0 <= limit + 0) }'
}

validate_profile_quality() {
    local class=$1
    local log=$2
    local mode=$3
    local sample_line samples brstack ignored_line mismatch_line mismatch_count mismatch_percent
    local out_of_range_line out_of_range_count out_of_range_percent profiled_functions
    local diagnostics_file unexpected_file skylake_warning skylake_workaround_count

    sample_line=$(grep -E 'PERF2BOLT: read [0-9]+ samples and [0-9]+ brstack entries' "${log}" | tail -n 1) || \
        fail "${class}/${mode}: perf2bolt did not report branch-stack sample counts"
    IFS=' ' read -r samples brstack < <(
        sed -E 's/.*read ([0-9]+) samples and ([0-9]+) brstack entries.*/\1 \2/' <<<"${sample_line}"
    )
    ((samples >= 100)) || fail "${class}/${mode}: only ${samples} perf samples were converted"
    ((brstack >= 1000)) || fail "${class}/${mode}: only ${brstack} branch-stack entries were converted"

    ignored_line=$(grep -E 'PERF2BOLT: ignored samples:' "${log}" | tail -n 1) || \
        fail "${class}/${mode}: perf2bolt did not report ignored samples"
    grep -Eq 'ignored samples: 0 \(0([.]0+)?%\)' <<<"${ignored_line}" || \
        fail "${class}/${mode}: perf2bolt ignored samples: ${ignored_line}"

    mismatch_line=$(grep -E 'PERF2BOLT: traces mismatching disassembled function contents: [0-9]+ \([0-9.]+%\)' "${log}" | tail -n 1) || \
        fail "${class}/${mode}: perf2bolt did not report the mismatching-trace metric"
    IFS=' ' read -r mismatch_count mismatch_percent < <(
        sed -E 's/.*contents: ([0-9]+) \(([0-9.]+)%\).*/\1 \2/' <<<"${mismatch_line}"
    )
    [[ ${mismatch_count} =~ ^[0-9]+$ && ${mismatch_percent} =~ ^[0-9]+([.][0-9]+)?$ ]] || \
        fail "${class}/${mode}: malformed mismatching-trace metric: ${mismatch_line}"
    percentage_at_most "${mismatch_percent}" 5.0 || \
        fail "${class}/${mode}: mismatching trace ratio ${mismatch_percent}% exceeds 5%"
    out_of_range_line=$(grep -E 'PERF2BOLT: out of range traces involving unknown regions: [0-9]+ \([0-9.]+%\)' "${log}" | tail -n 1) || \
        fail "${class}/${mode}: perf2bolt did not report the out-of-range-trace metric"
    IFS=' ' read -r out_of_range_count out_of_range_percent < <(
        sed -E 's/.*regions: ([0-9]+) \(([0-9.]+)%\).*/\1 \2/' <<<"${out_of_range_line}"
    )
    [[ ${out_of_range_count} =~ ^[0-9]+$ && ${out_of_range_percent} =~ ^[0-9]+([.][0-9]+)?$ ]] || \
        fail "${class}/${mode}: malformed out-of-range-trace metric: ${out_of_range_line}"
    percentage_at_most "${out_of_range_percent}" 10.0 || \
        fail "${class}/${mode}: out-of-range trace ratio ${out_of_range_percent}% exceeds 10%"

    profiled_functions=$(sed -nE 's/.*BOLT-INFO: ([0-9]+) out of [0-9]+ functions.*non-empty execution profile.*/\1/p' "${log}" | tail -n 1)
    [[ ${profiled_functions} =~ ^[1-9][0-9]*$ ]] || fail "${class}/${mode}: no positively profiled functions were reported"
    diagnostics_file=${log%.log}-critical-diagnostics.txt
    unexpected_file=${log%.log}-unexpected-diagnostics.txt
    skylake_warning='PERF2BOLT-WARNING: using Intel Skylake bug workaround'
    grep -E 'BOLT-ERROR|LLVM ERROR|PLEASE submit a bug report|BOLT-WARNING' "${log}" \
        >"${diagnostics_file}" || true
    grep -Fvx -- "${skylake_warning}" "${diagnostics_file}" >"${unexpected_file}" || true
    [[ ! -s ${unexpected_file} ]] || \
        fail "${class}/${mode}: unexplained critical/error/warning diagnostic in ${log}"
    skylake_workaround_count=$(grep -Fxc -- "${skylake_warning}" "${diagnostics_file}" || true)
    ((skylake_workaround_count <= 1)) || \
        fail "${class}/${mode}: duplicate Skylake workaround diagnostics"
    if ((skylake_workaround_count == 1)); then
        printf '%s\n' "${skylake_warning}" >"${log%.log}-allowed-warning.txt"
    else
        : >"${log%.log}-allowed-warning.txt"
    fi

    {
        printf 'samples=%s\nbrstack_entries=%s\nprofiled_functions=%s\n' \
            "${samples}" "${brstack}" "${profiled_functions}"
        printf 'ignored_samples=0\nmismatching_traces=%s\nmismatching_trace_percent=%s\n' \
            "${mismatch_count}" "${mismatch_percent}"
        printf 'out_of_range_traces=%s\nout_of_range_trace_percent=%s\n' \
            "${out_of_range_count}" "${out_of_range_percent}"
        printf 'skylake_lbr_workaround=%s\n' \
            "$([[ ${skylake_workaround_count} -eq 1 ]] && printf true || printf false)"
    } >"${log%.log}-quality.txt"
}

validate_output() {
    local class=$1
    local input=$2
    local output=$3
    local record_root=$4
    local runtime_command=$5
    local expected_output=$6
    local input_gnu_property_sha output_gnu_property_sha

    [[ -s ${output} ]] || fail "${class}: BOLT output is absent or empty"
    record_elf "${output}" "${record_root}/output"
    grep -Fq '.note.bolt_info' "${record_root}/output-sections.txt" || fail "${class}: .note.bolt_info is absent"
    "${READELF}" -hW -- "${input}" | sed -n '/Class:/p;/Type:/p;/Machine:/p' >"${record_root}/input-identity.txt"
    "${READELF}" -hW -- "${output}" | sed -n '/Class:/p;/Type:/p;/Machine:/p' >"${record_root}/output-identity.txt"
    cmp -s -- "${record_root}/input-identity.txt" "${record_root}/output-identity.txt" || fail "${class}: ELF class/type/machine changed"

    normalized_needed "${input}" >"${record_root}/input-needed.txt"
    normalized_needed "${output}" >"${record_root}/output-needed.txt"
    cmp -s -- "${record_root}/input-needed.txt" "${record_root}/output-needed.txt" || fail "${class}: DT_NEEDED changed"
    normalized_dynamic_policy "${input}" >"${record_root}/input-dynamic-policy.txt"
    normalized_dynamic_policy "${output}" >"${record_root}/output-dynamic-policy.txt"
    cmp -s -- "${record_root}/input-dynamic-policy.txt" "${record_root}/output-dynamic-policy.txt" || fail "${class}: SONAME/RPATH/RUNPATH/FLAGS policy changed"
    normalized_interpreter "${input}" >"${record_root}/input-interpreter.txt"
    normalized_interpreter "${output}" >"${record_root}/output-interpreter.txt"
    cmp -s -- "${record_root}/input-interpreter.txt" "${record_root}/output-interpreter.txt" || fail "${class}: PT_INTERP changed"
    normalized_gnu_stack "${input}" >"${record_root}/input-gnu-stack.txt"
    normalized_gnu_stack "${output}" >"${record_root}/output-gnu-stack.txt"
    cmp -s -- "${record_root}/input-gnu-stack.txt" "${record_root}/output-gnu-stack.txt" || fail "${class}: GNU_STACK policy changed"
    normalized_gnu_relro "${input}" >"${record_root}/input-gnu-relro.txt"
    normalized_gnu_relro "${output}" >"${record_root}/output-gnu-relro.txt"
    cmp -s -- "${record_root}/input-gnu-relro.txt" "${record_root}/output-gnu-relro.txt" || fail "${class}: GNU_RELRO policy changed"
    input_gnu_property_sha=$(section_sha256 "${input}" .note.gnu.property "${record_root}/input.note.gnu.property")
    output_gnu_property_sha=$(section_sha256 "${output}" .note.gnu.property "${record_root}/output.note.gnu.property")
    [[ ${input_gnu_property_sha} == "${output_gnu_property_sha}" ]] || \
        fail "${class}: GNU property/CET note changed"
    normalized_lddtree "${input}" >"${record_root}/input-lddtree-normalized.txt"
    normalized_lddtree "${output}" >"${record_root}/output-lddtree-normalized.txt"
    cmp -s -- "${record_root}/input-lddtree-normalized.txt" "${record_root}/output-lddtree-normalized.txt" || fail "${class}: resolved dependency tree changed"
    "${READELF}" --version-info -W -- "${input}" >"${record_root}/input-symbol-versions.txt"
    "${READELF}" --version-info -W -- "${output}" >"${record_root}/output-symbol-versions.txt"
    cmp -s -- "${record_root}/input-symbol-versions.txt" "${record_root}/output-symbol-versions.txt" || fail "${class}: symbol-version metadata changed"

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
    local input_build_id input_sha input_sha_after_profile input_sha_after_bolt input_text_sha
    local output_build_id output_text_sha stripped_text_sha stripped_runtime_command required_symbol
    mkdir -p -- "${class_root}"

    run "${SETFATTR}" -n user.gentoo_optimization_fixture -v "bolt-${class}" -- "${input}"
    record_elf "${input}" "${class_root}/input"
    assert_input_shape "${class}" "${input}" "${class_root}/input-header.txt" "${class_root}/input-sections.txt"
    input_build_id=$(gnu_build_id "${input}")
    input_sha=$("${SHA256SUM}" "${input}" | awk '{print $1}')
    input_text_sha=$(section_text_sha256 "${input}" "${class_root}/input.text")

    run_timed_generated_artifact "${class}:perf-record-mode1" \
        "${PERF_RECORD_TIMEOUT_SECONDS}" "${class_root}/perf-mode1.log" \
        "${class_root}/mode1.perf.data" "${class_root}/mode1.perf.data.partial" \
        "${PERF}" record -q -e cycles:u -j any,u \
        -o "${class_root}/mode1.perf.data.partial" -- bash -c "${mode1_command}"
    run_timed_generated_artifact "${class}:perf-record-mode2" \
        "${PERF_RECORD_TIMEOUT_SECONDS}" "${class_root}/perf-mode2.log" \
        "${class_root}/mode2.perf.data" "${class_root}/mode2.perf.data.partial" \
        "${PERF}" record -q -e cycles:u -j any,u \
        -o "${class_root}/mode2.perf.data.partial" -- bash -c "${mode2_command}"
    [[ -s ${class_root}/mode1.perf.data && -s ${class_root}/mode2.perf.data ]] || fail "${class}: perf data is empty"
    run_timed_stdout_artifact "${class}:perf-report" \
        "${PERF_ANALYSIS_TIMEOUT_SECONDS}" "${class_root}/perf-report.txt" \
        "${class_root}/perf-report.stderr.log" \
        "${PERF}" report --stdio --no-children --sort dso,symbol \
        -i "${class_root}/mode1.perf.data"
    run_timed_stdout_artifact "${class}:perf-buildid-list-mode1" \
        "${PERF_ANALYSIS_TIMEOUT_SECONDS}" "${class_root}/perf-buildids-mode1.txt" \
        "${class_root}/perf-buildids-mode1.stderr.log" \
        "${PERF}" buildid-list -i "${class_root}/mode1.perf.data"
    run_timed_stdout_artifact "${class}:perf-buildid-list-mode2" \
        "${PERF_ANALYSIS_TIMEOUT_SECONDS}" "${class_root}/perf-buildids-mode2.txt" \
        "${class_root}/perf-buildids-mode2.stderr.log" \
        "${PERF}" buildid-list -i "${class_root}/mode2.perf.data"
    grep -Fiq -- "${input_build_id}" "${class_root}/perf-buildids-mode1.txt" || fail "${class}: mode1 perf data lacks the exact input build ID"
    grep -Fiq -- "${input_build_id}" "${class_root}/perf-buildids-mode2.txt" || fail "${class}: mode2 perf data lacks the exact input build ID"

    run_timed_generated_artifact "${class}:perf2bolt-mode1" \
        "${PERF2BOLT_TIMEOUT_SECONDS}" "${class_root}/perf2bolt-mode1.log" \
        "${class_root}/mode1.fdata" "${class_root}/mode1.fdata.partial" \
        "${PERF2BOLT}" -p "${class_root}/mode1.perf.data" \
        -o "${class_root}/mode1.fdata.partial" "${input}"
    run_timed_generated_artifact "${class}:perf2bolt-mode2" \
        "${PERF2BOLT_TIMEOUT_SECONDS}" "${class_root}/perf2bolt-mode2.log" \
        "${class_root}/mode2.fdata" "${class_root}/mode2.fdata.partial" \
        "${PERF2BOLT}" -p "${class_root}/mode2.perf.data" \
        -o "${class_root}/mode2.fdata.partial" "${input}"
    validate_profile_quality "${class}" "${class_root}/perf2bolt-mode1.log" mode1
    validate_profile_quality "${class}" "${class_root}/perf2bolt-mode2.log" mode2
    [[ -s ${class_root}/mode1.fdata && -s ${class_root}/mode2.fdata ]] || fail "${class}: perf2bolt produced empty fdata"
    run_timed_stdout_artifact "${class}:merge-fdata" \
        "${MERGE_FDATA_TIMEOUT_SECONDS}" "${class_root}/merged.fdata" \
        "${class_root}/merge-fdata.log" \
        "${MERGE_FDATA}" "${class_root}/mode1.fdata" "${class_root}/mode2.fdata"
    [[ -s ${class_root}/merged.fdata ]] || fail "${class}: merged fdata is empty"
    for required_symbol in bolt_fixture_run hot_even hot_odd; do
        grep -Fq -- "${required_symbol}" "${class_root}/merged.fdata" || \
            fail "${class}: merged fdata lacks required function ${required_symbol}"
    done
    input_sha_after_profile=$("${SHA256SUM}" "${input}" | awk '{print $1}')
    [[ ${input_sha} == "${input_sha_after_profile}" ]] || fail "${class}: exact input changed during profiling/conversion"

    run_timed_generated_artifact "${class}:llvm-bolt" \
        "${LLVM_BOLT_TIMEOUT_SECONDS}" "${class_root}/llvm-bolt.log" \
        "${class_root}/output.bolt" "${class_root}/output.bolt.partial" \
        "${LLVM_BOLT}" "${input}" \
        -o "${class_root}/output.bolt.partial" \
        -data="${class_root}/merged.fdata" \
        -reorder-blocks=ext-tsp \
        -reorder-functions=cdsort \
        -split-functions \
        -split-all-cold \
        -split-eh \
        -icf=1 \
        -use-gnu-stack \
        -update-debug-sections \
        -dyno-stats
    if grep -Eq 'BOLT-ERROR|LLVM ERROR|PLEASE submit a bug report|BOLT-WARNING' "${class_root}/llvm-bolt.log"; then
        fail "${class}: unexplained critical/error/warning diagnostic in llvm-bolt output"
    fi
    apply_reference_metadata "${input}" "${class_root}/output.bolt"
    output_text_sha=$(section_text_sha256 "${class_root}/output.bolt" "${class_root}/output.text")
    [[ ${input_text_sha} != "${output_text_sha}" ]] || fail "${class}: BOLT did not change the .text image"
    output_build_id=$(gnu_build_id "${class_root}/output.bolt")
    [[ ${input_build_id} != "${output_build_id}" ]] || fail "${class}: BOLT output retained the input GNU build ID"
    input_sha_after_bolt=$("${SHA256SUM}" "${input}" | awk '{print $1}')
    [[ ${input_sha} == "${input_sha_after_bolt}" ]] || fail "${class}: exact input changed during BOLT optimization"
    validate_output "${class}" "${input}" "${class_root}/output.bolt" "${class_root}" "${runtime_command}" "${expected_output}"

    run cp --preserve=all -- "${class_root}/output.bolt" "${class_root}/output.stripped"
    run "${STRIP}" --strip-unneeded -- "${class_root}/output.stripped"
    stripped_text_sha=$(section_text_sha256 "${class_root}/output.stripped" "${class_root}/stripped.text")
    [[ ${stripped_text_sha} == "${output_text_sha}" ]] || fail "${class}: normal strip changed the BOLT .text image"
    stripped_runtime_command=${runtime_command//output.bolt/output.stripped}
    mkdir -p -- "${class_root}/stripped"
    validate_output "${class}" "${input}" "${class_root}/output.stripped" "${class_root}/stripped" "${stripped_runtime_command}" "${expected_output}"
    [[ $(gnu_build_id "${class_root}/output.stripped") == "${output_build_id}" ]] || fail "${class}: normal strip changed the BOLT output build ID"

    {
        printf 'class=%s\n' "${class}"
        printf 'result=PASS\n'
        printf 'input=%s\n' "${input}"
        printf 'input_sha256=%s\n' "${input_sha}"
        printf 'input_build_id=%s\n' "${input_build_id}"
        printf 'input_text_sha256=%s\n' "${input_text_sha}"
        printf 'output=%s\n' "${class_root}/output.bolt"
        printf 'output_build_id=%s\n' "${output_build_id}"
        printf 'output_text_sha256=%s\n' "${output_text_sha}"
        printf 'post_strip_text_sha256=%s\n' "${stripped_text_sha}"
        printf 'bolt_note=true\npost_strip_bolt_note=true\nfunctionality=true\npost_strip_functionality=true\n'
        printf 'runtime_identity_and_dynamic_dependencies_preserved=true\n'
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
TIMEOUT_STATUS_FILE=${OUTPUT_ROOT}/timeout-status.tsv
printf 'stage\ttimeout_seconds\tkill_after_seconds\texit_status\tresult\ttimed_out\tartifact\tpublished\tpartial_removed\n' \
    >"${TIMEOUT_STATUS_FILE}"

CLANG=$(require_tool clang)
PERF=$(require_tool perf)
PERF2BOLT=$(require_tool perf2bolt)
LLVM_BOLT=$(require_tool llvm-bolt)
MERGE_FDATA=$(require_tool merge-fdata)
READELF=$(require_tool readelf)
OBJCOPY=$(require_tool objcopy)
STRIP=$(require_tool strip)
NM=$(require_tool nm)
LDDTREE=$(require_tool lddtree)
FILE_TOOL=$(require_tool file)
GETFATTR=$(require_tool getfattr)
SETFATTR=$(require_tool setfattr)
GETCAP=$(require_tool getcap)
SHA256SUM=$(require_tool sha256sum)
TIMEOUT=$(require_tool timeout)

PERF_RECORD_TIMEOUT_SECONDS=${BOLT_FIXTURE_PERF_RECORD_TIMEOUT_SECONDS:-900}
PERF_ANALYSIS_TIMEOUT_SECONDS=${BOLT_FIXTURE_PERF_ANALYSIS_TIMEOUT_SECONDS:-300}
PERF2BOLT_TIMEOUT_SECONDS=${BOLT_FIXTURE_PERF2BOLT_TIMEOUT_SECONDS:-900}
MERGE_FDATA_TIMEOUT_SECONDS=${BOLT_FIXTURE_MERGE_FDATA_TIMEOUT_SECONDS:-300}
LLVM_BOLT_TIMEOUT_SECONDS=${BOLT_FIXTURE_LLVM_BOLT_TIMEOUT_SECONDS:-900}
TIMEOUT_KILL_AFTER_SECONDS=${BOLT_FIXTURE_TIMEOUT_KILL_AFTER_SECONDS:-30}
readonly PERF_RECORD_TIMEOUT_SECONDS PERF_ANALYSIS_TIMEOUT_SECONDS
readonly PERF2BOLT_TIMEOUT_SECONDS MERGE_FDATA_TIMEOUT_SECONDS
readonly LLVM_BOLT_TIMEOUT_SECONDS TIMEOUT_KILL_AFTER_SECONDS
for timeout_name in PERF_RECORD_TIMEOUT_SECONDS PERF_ANALYSIS_TIMEOUT_SECONDS \
    PERF2BOLT_TIMEOUT_SECONDS MERGE_FDATA_TIMEOUT_SECONDS \
    LLVM_BOLT_TIMEOUT_SECONDS TIMEOUT_KILL_AFTER_SECONDS; do
    timeout_value=${!timeout_name}
    [[ ${timeout_value} =~ ^[1-9][0-9]*$ && ${#timeout_value} -le 5 ]] || \
        fail "${timeout_name} must be an integer from 1 through 86400 seconds"
    ((timeout_value <= 86400)) || \
        fail "${timeout_name} must be an integer from 1 through 86400 seconds"
done
bolt_transaction_install_traps

EXPECTED_TIMED_STAGES_FILE=${OUTPUT_ROOT}/expected-timed-stages.txt
bolt_transaction_write_stage_registry "${EXPECTED_TIMED_STAGES_FILE}"
{
    printf 'timeout_max_seconds=86400\n'
    printf 'termination_signal=TERM\n'
    printf 'perf_record_timeout_seconds=%s\n' "${PERF_RECORD_TIMEOUT_SECONDS}"
    printf 'perf_analysis_timeout_seconds=%s\n' "${PERF_ANALYSIS_TIMEOUT_SECONDS}"
    printf 'perf2bolt_timeout_seconds=%s\n' "${PERF2BOLT_TIMEOUT_SECONDS}"
    printf 'merge_fdata_timeout_seconds=%s\n' "${MERGE_FDATA_TIMEOUT_SECONDS}"
    printf 'llvm_bolt_timeout_seconds=%s\n' "${LLVM_BOLT_TIMEOUT_SECONDS}"
    printf 'kill_after_seconds=%s\n' "${TIMEOUT_KILL_AFTER_SECONDS}"
    printf 'publication=atomic-rename-after-success\n'
    printf 'failed_partial_policy=remove-and-never-publish\n'
} >"${OUTPUT_ROOT}/timeout-policy.txt"

"${SHA256SUM}" \
    "${SCRIPT_DIR}/run.sh" \
    "${SCRIPT_DIR}/transaction.sh" \
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
    -fcf-protection=full
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

record_elf "${OUTPUT_ROOT}/build/fixture-dso-driver" "${OUTPUT_ROOT}/dso-driver-input"
assert_input_shape pie \
    "${OUTPUT_ROOT}/build/fixture-dso-driver" \
    "${OUTPUT_ROOT}/dso-driver-input-header.txt" \
    "${OUTPUT_ROOT}/dso-driver-input-sections.txt" \
    dso-driver
normalized_needed "${OUTPUT_ROOT}/build/fixture-dso-driver" |
    grep -Fx -- libboltfixture.so >/dev/null || fail 'DSO driver does not depend on libboltfixture.so'
# Keep $ORIGIN literal while checking the decoded dynamic tag.
# shellcheck disable=SC2016
"${READELF}" -dW -- "${OUTPUT_ROOT}/build/fixture-dso-driver" |
    grep -F 'Library runpath: [$ORIGIN]' >/dev/null || fail 'DSO driver does not carry the exact $ORIGIN RUNPATH'

run_expect_status 2 "${OUTPUT_ROOT}/negative-signed-iterations.log" \
    "${TIMEOUT}" 2 "${OUTPUT_ROOT}/build/fixture-exec" -1 1
grep -Fq 'decimal digits only' "${OUTPUT_ROOT}/negative-signed-iterations.log" || \
    fail 'signed-negative iteration rejection lacked its exact diagnostic'
run_expect_status 2 "${OUTPUT_ROOT}/negative-mode-overflow.log" \
    "${TIMEOUT}" 2 "${OUTPUT_ROOT}/build/fixture-exec" 1 4294967296
grep -Fq 'mode is outside the unsigned range' "${OUTPUT_ROOT}/negative-mode-overflow.log" || \
    fail 'unsigned-mode overflow rejection lacked its exact diagnostic'
run_expect_status 2 "${OUTPUT_ROOT}/negative-extra-argument.log" \
    "${TIMEOUT}" 2 "${OUTPUT_ROOT}/build/fixture-exec" 1 1 unexpected
grep -Fq 'usage:' "${OUTPUT_ROOT}/negative-extra-argument.log" || \
    fail 'extra-argument rejection lacked its usage diagnostic'

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

ACTUAL_TIMED_STAGES_FILE=${OUTPUT_ROOT}/actual-timed-stages.txt
bolt_transaction_validate_stage_evidence "${TIMEOUT_STATUS_FILE}" \
    "${EXPECTED_TIMED_STAGES_FILE}" "${ACTUAL_TIMED_STAGES_FILE}"
TIMED_STAGE_COUNT=${BOLT_TRANSACTION_TIMED_STAGE_COUNT}
awk -F '\t' '
    NR == 1 { next }
    $4 != "0" || $5 != "completed" || $6 != "false" ||
        $8 != "true" || $9 != "true" { exit 1 }
' "${TIMEOUT_STATUS_FILE}" || \
    fail 'at least one timed stage lacks a clean successful publication record'
if find "${OUTPUT_ROOT}" -type f -name '*.partial' -print -quit | grep -q .; then
    fail 'an unpublished partial artifact remains after successful validation'
fi

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
    printf 'timeout_status=%s\n' "${TIMEOUT_STATUS_FILE}"
    printf 'perf_record_timeout_seconds=%s\n' "${PERF_RECORD_TIMEOUT_SECONDS}"
    printf 'perf_analysis_timeout_seconds=%s\n' "${PERF_ANALYSIS_TIMEOUT_SECONDS}"
    printf 'perf2bolt_timeout_seconds=%s\n' "${PERF2BOLT_TIMEOUT_SECONDS}"
    printf 'merge_fdata_timeout_seconds=%s\n' "${MERGE_FDATA_TIMEOUT_SECONDS}"
    printf 'llvm_bolt_timeout_seconds=%s\n' "${LLVM_BOLT_TIMEOUT_SECONDS}"
    printf 'timeout_kill_after_seconds=%s\n' "${TIMEOUT_KILL_AFTER_SECONDS}"
    printf 'timed_stage_count=%s\n' "${TIMED_STAGE_COUNT}"
    printf 'partial_artifact_publication=atomic-after-success\n'
    printf 'all_bolt_notes=true\nall_functionality=true\nall_metadata=true\nall_dynamic_dependencies=true\n'
} >"${OUTPUT_ROOT}/validation-summary.txt"

find "${OUTPUT_ROOT}" -type f ! -name evidence.sha256 -print0 |
    LC_ALL=C sort -z |
    xargs -0 "${SHA256SUM}" >"${OUTPUT_ROOT}/evidence.sha256"
"${SHA256SUM}" -c "${OUTPUT_ROOT}/evidence.sha256" >"${OUTPUT_ROOT}/evidence-verification.txt"
printf 'PASS: BOLT executable, PIE, and DSO fixture suite (%s)\n' "${OUTPUT_ROOT}"

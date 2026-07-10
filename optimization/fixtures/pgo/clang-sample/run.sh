#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
OUTPUT_ROOT=${1:-}
ITERATIONS=${CLANG_SAMPLE_ITERATIONS:-50000000}
LLVM_ROOT=${LLVM_ROOT:-/usr/lib/llvm/22/bin}
CLANGXX=${CLANGXX:-${LLVM_ROOT}/clang++}
PROFGEN=${PROFGEN:-${LLVM_ROOT}/llvm-profgen}
PROFDATA=${PROFDATA:-${LLVM_ROOT}/llvm-profdata}
READELF=${READELF:-${LLVM_ROOT}/llvm-readelf}
PERF=${PERF:-/usr/bin/perf}
RG=${RG:-/usr/bin/rg}

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

[[ -n ${OUTPUT_ROOT} && ${OUTPUT_ROOT} == /* && ${OUTPUT_ROOT} != / ]] || \
    fail 'provide one new absolute output directory'
case ${OUTPUT_ROOT} in
    /tmp/*|/var/tmp/gentoo-optimization/*) ;;
    *) fail 'output directory must be below /tmp or /var/tmp/gentoo-optimization' ;;
esac
[[ ! -e ${OUTPUT_ROOT} ]] || fail "output already exists: ${OUTPUT_ROOT}"
[[ ${ITERATIONS} =~ ^[1-9][0-9]*$ ]] || fail 'CLANG_SAMPLE_ITERATIONS must be positive'

for tool in "${CLANGXX}" "${PROFGEN}" "${PROFDATA}" "${READELF}" "${PERF}" "${RG}"; do
    [[ -x ${tool} ]] || fail "required tool is not executable: ${tool}"
done

mkdir -p -- "${OUTPUT_ROOT}"
OUTPUT_ROOT=$(cd -- "${OUTPUT_ROOT}" && pwd -P)
case ${OUTPUT_ROOT} in
    /tmp/*|/var/tmp/gentoo-optimization/*) ;;
    *) fail "canonical output directory escaped the allowed roots: ${OUTPUT_ROOT}" ;;
esac
exec > >(tee "${OUTPUT_ROOT}/run.log") 2>&1
set -x

COMMON_FLAGS=(
    -O3
    -flto=thin
    -gline-tables-only
    -fdebug-info-for-profiling
    -funique-internal-linkage-names
    -fpseudo-probe-for-profiling
    -fno-omit-frame-pointer
    -ffunction-sections
    -fdata-sections
)
LINK_FLAGS=(
    -fuse-ld=lld
    "-Wl,--build-id=sha1"
    "-Wl,--emit-relocs"
)

"${CLANGXX}" "${COMMON_FLAGS[@]}" -c "${SCRIPT_DIR}/sample_math.cpp" \
    -o "${OUTPUT_ROOT}/sample_math.o"
"${CLANGXX}" "${COMMON_FLAGS[@]}" -c "${SCRIPT_DIR}/sample_main.cpp" \
    -o "${OUTPUT_ROOT}/sample_main.o"
"${CLANGXX}" "${COMMON_FLAGS[@]}" "${LINK_FLAGS[@]}" -pie \
    "${OUTPUT_ROOT}/sample_main.o" "${OUTPUT_ROOT}/sample_math.o" \
    -o "${OUTPUT_ROOT}/sample-train"

"${READELF}" --notes --sections --relocs "${OUTPUT_ROOT}/sample-train" \
    >"${OUTPUT_ROOT}/sample-train.readelf"
"${PERF}" record -q -e cycles:u -j any,u \
    -o "${OUTPUT_ROOT}/perf.data" -- \
    "${OUTPUT_ROOT}/sample-train" "${ITERATIONS}" \
    >"${OUTPUT_ROOT}/training.stdout"
"${PERF}" evlist -v -i "${OUTPUT_ROOT}/perf.data" \
    >"${OUTPUT_ROOT}/perf-evlist.log"
"${RG}" -q 'sample_type:.*BRANCH_STACK' "${OUTPUT_ROOT}/perf-evlist.log" || \
    fail 'perf.data does not declare branch-stack samples'
"${RG}" -q 'branch_sample_type:.*USER.*ANY' "${OUTPUT_ROOT}/perf-evlist.log" || \
    fail 'perf.data does not declare the requested user/any branch filter'

"${PROFGEN}" \
    --binary="${OUTPUT_ROOT}/sample-train" \
    --perfdata="${OUTPUT_ROOT}/perf.data" \
    --format=extbinary \
    --show-detailed-warning \
    --output="${OUTPUT_ROOT}/sample.prof" \
    >"${OUTPUT_ROOT}/llvm-profgen.log" 2>&1
"${PROFDATA}" show --sample --all-functions --counts \
    "${OUTPUT_ROOT}/sample.prof" >"${OUTPUT_ROOT}/sample-profile.show"

"${CLANGXX}" "${COMMON_FLAGS[@]}" \
    -fprofile-sample-use="${OUTPUT_ROOT}/sample.prof" \
    -fsample-profile-use-profi \
    -c "${SCRIPT_DIR}/sample_math.cpp" -o "${OUTPUT_ROOT}/sample_math.use.o" \
    2>"${OUTPUT_ROOT}/sample-use-compile.log"
"${CLANGXX}" "${COMMON_FLAGS[@]}" \
    -fprofile-sample-use="${OUTPUT_ROOT}/sample.prof" \
    -fsample-profile-use-profi \
    -c "${SCRIPT_DIR}/sample_main.cpp" -o "${OUTPUT_ROOT}/sample_main.use.o" \
    2>>"${OUTPUT_ROOT}/sample-use-compile.log"
"${CLANGXX}" "${COMMON_FLAGS[@]}" "${LINK_FLAGS[@]}" -pie \
    "${OUTPUT_ROOT}/sample_main.use.o" "${OUTPUT_ROOT}/sample_math.use.o" \
    -o "${OUTPUT_ROOT}/sample-use"

"${OUTPUT_ROOT}/sample-use" "${ITERATIONS}" >"${OUTPUT_ROOT}/sample-use.stdout"
cmp -- "${OUTPUT_ROOT}/training.stdout" "${OUTPUT_ROOT}/sample-use.stdout"

if "${CLANGXX}" "${COMMON_FLAGS[@]}" \
    -fprofile-use="${OUTPUT_ROOT}/sample.prof" \
    -c "${SCRIPT_DIR}/sample_math.cpp" \
    -o "${OUTPUT_ROOT}/must-not-build.o" \
    >"${OUTPUT_ROOT}/ir-consumer-negative.stdout" \
    2>"${OUTPUT_ROOT}/ir-consumer-negative.stderr"; then
    fail 'Clang incorrectly accepted the sample profile through -fprofile-use'
fi
[[ -s ${OUTPUT_ROOT}/ir-consumer-negative.stderr ]] || \
    fail 'wrong-consumer negative test produced no diagnostic'

"${CLANGXX}" "${COMMON_FLAGS[@]}" \
    -fprofile-sample-use="${OUTPUT_ROOT}/sample.prof" \
    -fsample-profile-use-profi \
    -### -c "${SCRIPT_DIR}/sample_math.cpp" \
    -o "${OUTPUT_ROOT}/command-proof.o" \
    2>"${OUTPUT_ROOT}/sample-use-command.log"

profile_functions=$("${RG}" -c '^Function: ' "${OUTPUT_ROOT}/sample-profile.show")
profgen_warning_lines=$("${RG}" -c '^warning:' "${OUTPUT_ROOT}/llvm-profgen.log" || true)
[[ -n ${profile_functions} && ${profile_functions} -gt 0 ]] || \
    fail 'sample profile contains no functions'
"${RG}" -q '^[1-9][0-9]*, [0-9]+, [1-9][0-9]* sampled lines$' \
    "${OUTPUT_ROOT}/sample-profile.show" || \
    fail 'sample profile has no nonzero top-level samples'
"${RG}" -q -- '-fprofile-sample-use=' "${OUTPUT_ROOT}/sample-use-command.log" || \
    fail 'Clang command proof omits the sample-profile consumer'
"${RG}" -qi 'invalid|malformed|instrumentation profile|bad magic|unrecognized' \
    "${OUTPUT_ROOT}/ir-consumer-negative.stderr" || \
    fail 'wrong-consumer diagnostic does not prove profile-format rejection'

{
    printf 'result=PASS\n'
    printf 'iterations=%s\n' "${ITERATIONS}"
    printf 'sample_profile=%s\n' "${OUTPUT_ROOT}/sample.prof"
    printf 'sample_profile_functions=%s\n' "${profile_functions}"
    printf 'perf_sample_type=BRANCH_STACK\n'
    printf 'perf_branch_sample_type=USER_ANY\n'
    printf 'llvm_profgen_warning_lines=%s\n' "${profgen_warning_lines:-0}"
    printf 'training_output=%s\n' "$(<"${OUTPUT_ROOT}/training.stdout")"
    printf 'sample_use_output=%s\n' "$(<"${OUTPUT_ROOT}/sample-use.stdout")"
    printf 'wrong_consumer_exit=nonzero\n'
    printf 'wrong_consumer_diagnostic=present\n'
    printf 'fixture_runner_sha256=%s\n' "$(sha256sum "${SCRIPT_DIR}/run.sh" | awk '{print $1}')"
} >"${OUTPUT_ROOT}/validation-summary.log"

sha256sum -- \
    "${OUTPUT_ROOT}/sample-train" \
    "${OUTPUT_ROOT}/perf.data" \
    "${OUTPUT_ROOT}/perf-evlist.log" \
    "${OUTPUT_ROOT}/llvm-profgen.log" \
    "${OUTPUT_ROOT}/sample.prof" \
    "${OUTPUT_ROOT}/sample-use" \
    "${OUTPUT_ROOT}/validation-summary.log" \
    >"${OUTPUT_ROOT}/evidence-sha256.log"

set +x
printf 'PASS: Clang sample-PGO fixture (%s functions)\n' "${profile_functions}"

#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export LC_ALL=C
export TZ=UTC
unset LLVM_PROFILE_FILE

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

require_executable() {
    [[ -x $1 ]] || fail "required executable is unavailable: $1"
}

run_logged() {
    local log=$1
    shift
    if "$@" >"${log}" 2>&1; then
        return 0
    else
        local command_status=$?
        sed -n '1,240p' "${log}" >&2
        fail "command failed with status ${command_status}; see ${log}"
    fi
}

expect_failure() {
    local status_name=$1
    local log=$2
    shift 2
    local command_status

    set +e
    "$@" >"${log}" 2>&1
    command_status=$?
    set -e
    [[ ${command_status} -ne 0 ]] || fail "command unexpectedly succeeded: $*"
    printf -v "${status_name}" '%d' "${command_status}"
}

run_workload_matrix() {
    local binary=$1
    local output=$2

    {
        printf 'run=1 '
        "${binary}" hot 25000 0x123456789abcdef0
        printf 'run=2 '
        "${binary}" mixed 32000 0x0f1e2d3c4b5a6978
        printf 'run=3 '
        "${binary}" cold 3500 0x1020304050607080
        printf 'run=4 '
        "${binary}" hot 41000 0x8877665544332211
        printf 'run=5 '
        "${binary}" mixed 19000 0xdeadbeef12345678
        printf 'run=6 '
        "${binary}" hot 17000 0x3141592653589793
    } >"${output}"
}

copy_pair() {
    local destination=$1
    mkdir -p -- "${destination}"
    install -m 0755 "${BUILD_DIR}/gcc-pgo-fixture" \
        "${destination}/gcc-pgo-fixture"
    install -m 0755 "${BUILD_DIR}/libgcc_pgo_fixture.so" \
        "${destination}/libgcc_pgo_fixture.so"
}

build_generation() {
    local log_dir=$1
    mkdir -p -- "${log_dir}"

    run_logged "${log_dir}/plugin_core.log" \
        "${GCC}" "${COMMON_COMPILE_FLAGS[@]}" \
        "${GENERATION_FLAGS[@]}" -c "${SCRIPT_DIR}/plugin_core.c" \
        -o "${BUILD_DIR}/plugin_core.o"
    run_logged "${log_dir}/plugin_table.log" \
        "${GCC}" "${COMMON_COMPILE_FLAGS[@]}" \
        "${GENERATION_FLAGS[@]}" -c "${SCRIPT_DIR}/plugin_table.c" \
        -o "${BUILD_DIR}/plugin_table.o"
    run_logged "${log_dir}/shared-link.log" \
        "${GCC}" -O2 -shared "${GENERATION_FLAGS[@]}" \
        -Wl,-z,defs -Wl,--build-id=sha1 \
        "${BUILD_DIR}/plugin_core.o" "${BUILD_DIR}/plugin_table.o" \
        -o "${BUILD_DIR}/libgcc_pgo_fixture.so"

    run_logged "${log_dir}/workload.log" \
        "${GCC}" "${COMMON_COMPILE_FLAGS[@]}" \
        "${GENERATION_FLAGS[@]}" -c "${SCRIPT_DIR}/workload.c" \
        -o "${BUILD_DIR}/workload.o"
    run_logged "${log_dir}/main.log" \
        "${GCC}" "${COMMON_COMPILE_FLAGS[@]}" \
        "${GENERATION_FLAGS[@]}" -c "${SCRIPT_DIR}/main.c" \
        -o "${BUILD_DIR}/main.o"
    # Keep the dynamic lookup path relative to the copied fixture executable.
    # shellcheck disable=SC2016
    run_logged "${log_dir}/executable-link.log" \
        "${GCC}" -O2 -pie "${GENERATION_FLAGS[@]}" \
        -Wl,--build-id=sha1 '-Wl,-rpath,$ORIGIN' \
        "${BUILD_DIR}/main.o" "${BUILD_DIR}/workload.o" \
        -L"${BUILD_DIR}" -lgcc_pgo_fixture \
        -o "${BUILD_DIR}/gcc-pgo-fixture"
}

build_profile_use() {
    local profile_dir=$1
    local log_dir=$2
    local dump_profiles=$3
    local profile_flags=(
        "-fprofile-use=${profile_dir}"
        -fprofile-correction
        -Wcoverage-mismatch
        -Wmissing-profile
        -Werror=coverage-mismatch
        -Werror=missing-profile
    )
    local source object name
    local dump_flag=()

    mkdir -p -- "${log_dir}"
    for name in plugin_core plugin_table workload main; do
        source="${SCRIPT_DIR}/${name}.c"
        object="${BUILD_DIR}/${name}.o"
        dump_flag=()
        if [[ ${dump_profiles} == yes ]]; then
            dump_flag=("-fdump-ipa-profile-details=${log_dir}/${name}.profile-details")
        fi
        run_logged "${log_dir}/${name}.log" \
            "${GCC}" "${COMMON_COMPILE_FLAGS[@]}" \
            "${profile_flags[@]}" "${dump_flag[@]}" \
            -c "${source}" -o "${object}"
    done

    run_logged "${log_dir}/shared-link.log" \
        "${GCC}" -O2 -shared "${profile_flags[@]}" \
        -Wl,-z,defs -Wl,--build-id=sha1 \
        "${BUILD_DIR}/plugin_core.o" "${BUILD_DIR}/plugin_table.o" \
        -o "${BUILD_DIR}/libgcc_pgo_fixture.so"
    # Keep the dynamic lookup path relative to the copied fixture executable.
    # shellcheck disable=SC2016
    run_logged "${log_dir}/executable-link.log" \
        "${GCC}" -O2 -pie "${profile_flags[@]}" \
        -Wl,--build-id=sha1 '-Wl,-rpath,$ORIGIN' \
        "${BUILD_DIR}/main.o" "${BUILD_DIR}/workload.o" \
        -L"${BUILD_DIR}" -lgcc_pgo_fixture \
        -o "${BUILD_DIR}/gcc-pgo-fixture"
}

[[ $# -eq 1 ]] || fail 'usage: run.sh NEW-ABSOLUTE-OUTPUT-DIRECTORY'
[[ $1 == /* ]] || fail 'the output directory must be absolute'
[[ $1 != / ]] || fail 'the output directory cannot be /'
[[ $1 != *$'\n'* ]] || fail 'the output directory cannot contain a newline'
case $1 in
    /tmp/*|/var/tmp/gentoo-optimization/*) ;;
    *) fail 'output directory must be below /tmp or /var/tmp/gentoo-optimization' ;;
esac

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
OUTPUT_ROOT=$(realpath -m -- "$1")
case ${OUTPUT_ROOT} in
    /tmp/*|/var/tmp/gentoo-optimization/*) ;;
    *) fail "canonical output directory escaped the allowed roots: ${OUTPUT_ROOT}" ;;
esac
[[ ! -e ${OUTPUT_ROOT} ]] || fail "output already exists: ${OUTPUT_ROOT}"

GCC_CONFIG_BIN=$(type -P gcc-config) || fail 'gcc-config is unavailable'
ACTIVE_GCC_LINK=$(type -P gcc) || fail 'gcc is unavailable'
GCC_CONFIG_SELECTION=$("${GCC_CONFIG_BIN}" -c)
GCC_SLOT=${GCC_CONFIG_SELECTION##*-}
GCC_TRIPLE=${GCC_CONFIG_SELECTION%-*}
CONFIGURED_GCC_LINK=$(type -P "${GCC_TRIPLE}-gcc-${GCC_SLOT}") || \
    fail "configured compiler binary is unavailable for ${GCC_CONFIG_SELECTION}"
ACTIVE_GCC_REAL=$(readlink -f -- "${ACTIVE_GCC_LINK}")
CONFIGURED_GCC_REAL=$(readlink -f -- "${CONFIGURED_GCC_LINK}")
[[ ${ACTIVE_GCC_REAL} == "${CONFIGURED_GCC_REAL}" ]] || \
    fail "gcc does not resolve to the active gcc-config selection"
GCC=${ACTIVE_GCC_REAL}
GCOV_DUMP="$(dirname -- "${GCC}")/gcov-dump"

PYTHON=$(type -P python3) || fail 'python3 is unavailable'
JQ=$(type -P jq) || fail 'jq is unavailable'
READELF=$(type -P readelf) || fail 'readelf is unavailable'
NM=$(type -P nm) || fail 'nm is unavailable'
RG=$(type -P rg) || fail 'ripgrep is unavailable'
SHA256SUM=$(type -P sha256sum) || fail 'sha256sum is unavailable'
require_executable "${GCC}"
require_executable "${GCOV_DUMP}"
require_executable "${SCRIPT_DIR}/corrupt-gcda.py"

GCC_VERSION=$("${GCC}" -dumpfullversion -dumpversion)
GCC_MACHINE=$("${GCC}" -dumpmachine)
[[ ${GCC_MACHINE} == "${GCC_TRIPLE}" ]] || \
    fail "active GCC target ${GCC_MACHINE} differs from gcc-config target ${GCC_TRIPLE}"
[[ ${GCC_VERSION%%.*} == "${GCC_SLOT}" ]] || \
    fail "active GCC version ${GCC_VERSION} differs from gcc-config slot ${GCC_SLOT}"

shopt -s nullglob
gcc_package_matches=()
for slot_file in /var/db/pkg/sys-devel/gcc-[0-9]*/SLOT; do
    package_slot=$(sed -n '1p' "${slot_file}")
    if [[ ${package_slot%%/*} == "${GCC_SLOT}" ]]; then
        gcc_package_matches+=("${slot_file%/SLOT}")
    fi
done
[[ ${#gcc_package_matches[@]} -eq 1 ]] || \
    fail "expected one installed sys-devel/gcc package for slot ${GCC_SLOT}, found ${#gcc_package_matches[@]}"
ACTIVE_GCC_CPV="sys-devel/$(basename -- "${gcc_package_matches[0]}")"

package_component=${ACTIVE_GCC_CPV//\//_}
PROFILE_DIR="${OUTPUT_ROOT}/profiles/${package_component}/${GCC_CONFIG_SELECTION}/gcc-pgo-executable-dso-fixture"
CORRECTION_PROFILE_DIR="${OUTPUT_ROOT}/correction-profiles/${package_component}/${GCC_CONFIG_SELECTION}/gcc-pgo-executable-dso-fixture"
EMPTY_PROFILE_DIR="${OUTPUT_ROOT}/empty-profiles/${package_component}/${GCC_CONFIG_SELECTION}/gcc-pgo-executable-dso-fixture"
BUILD_DIR="${OUTPUT_ROOT}/work/build"
GENERATION_ARTIFACT_DIR="${OUTPUT_ROOT}/artifacts/generation"
USE_ARTIFACT_DIR="${OUTPUT_ROOT}/artifacts/profile-use"
CORRECTION_ARTIFACT_DIR="${OUTPUT_ROOT}/artifacts/profile-correction"

mkdir -p -- "${OUTPUT_ROOT}" "${PROFILE_DIR}" "${BUILD_DIR}"
exec > >(tee "${OUTPUT_ROOT}/run.log") 2>&1
PS4='+ '
set -x

COMMON_COMPILE_FLAGS=(
    -std=c11
    -O2
    -g3
    -fno-omit-frame-pointer
    -fPIC
    -Wall
    -Wextra
    -Wpedantic
    -Werror
    -I"${SCRIPT_DIR}"
)
GENERATION_FLAGS=(
    "-fprofile-generate=${PROFILE_DIR}"
    -fprofile-update=atomic
)

{
    printf 'gcc_config_selection=%s\n' "${GCC_CONFIG_SELECTION}"
    printf 'active_gcc_link=%s\n' "${ACTIVE_GCC_LINK}"
    printf 'active_gcc_real=%s\n' "${ACTIVE_GCC_REAL}"
    printf 'configured_gcc_link=%s\n' "${CONFIGURED_GCC_LINK}"
    printf 'active_gcc_cpv=%s\n' "${ACTIVE_GCC_CPV}"
    printf 'gcc_version=%s\n' "${GCC_VERSION}"
    printf 'gcc_machine=%s\n' "${GCC_MACHINE}"
    printf 'gcov_dump=%s\n' "${GCOV_DUMP}"
    printf 'profile_dir=%s\n' "${PROFILE_DIR}"
    printf 'llvm_profile_file_environment=unset\n'
    "${GCC}" --version
    "${GCC}" -v
    "${GCC}" -Q --help=common | \
        "${RG}" 'fprofile-(generate|use|correction|dir|update)|Wcoverage-mismatch|Wmissing-profile'
} >"${OUTPUT_ROOT}/toolchain.txt" 2>&1

"${SHA256SUM}" "${SCRIPT_DIR}/fixture.h" "${SCRIPT_DIR}/main.c" \
    "${SCRIPT_DIR}/workload.c" "${SCRIPT_DIR}/plugin_core.c" \
    "${SCRIPT_DIR}/plugin_table.c" "${SCRIPT_DIR}/run.sh" \
    "${SCRIPT_DIR}/corrupt-gcda.py" >"${OUTPUT_ROOT}/source.sha256"

build_generation "${OUTPUT_ROOT}/logs/generation"
copy_pair "${GENERATION_ARTIFACT_DIR}"
"${READELF}" -dW "${GENERATION_ARTIFACT_DIR}/gcc-pgo-fixture" \
    >"${OUTPUT_ROOT}/generation-executable.dynamic"
"${READELF}" -dW "${GENERATION_ARTIFACT_DIR}/libgcc_pgo_fixture.so" \
    >"${OUTPUT_ROOT}/generation-shared.dynamic"
"${RG}" -q 'NEEDED.*libgcc_pgo_fixture\.so' \
    "${OUTPUT_ROOT}/generation-executable.dynamic" || \
    fail 'generation executable does not declare the fixture DSO dependency'

run_workload_matrix "${GENERATION_ARTIFACT_DIR}/gcc-pgo-fixture" \
    "${OUTPUT_ROOT}/generation-workloads.txt"

mapfile -d '' GCDA_FILES < <(
    find "${PROFILE_DIR}" -type f -name '*.gcda' -print0 | sort -z
)
[[ ${#GCDA_FILES[@]} -eq 4 ]] || \
    fail "expected four GCC gcda files, found ${#GCDA_FILES[@]}"
declare -A gcda_basenames=()
for gcda_file in "${GCDA_FILES[@]}"; do
    [[ ${gcda_file} == "${PROFILE_DIR}/"* ]] || \
        fail "profile escaped the absolute fixture profile directory: ${gcda_file}"
    gcda_basenames["$(basename -- "${gcda_file}")"]=${gcda_file}
done
for expected_profile in main.gcda workload.gcda plugin_core.gcda plugin_table.gcda; do
    [[ -n ${gcda_basenames[${expected_profile}]:-} ]] || \
        fail "missing expected profile ${expected_profile}"
done

: >"${OUTPUT_ROOT}/gcov-dump.txt"
mkdir -p -- "${OUTPUT_ROOT}/logs/gcov-dump"
for gcda_file in "${GCDA_FILES[@]}"; do
    dump_file="${OUTPUT_ROOT}/logs/gcov-dump/$(basename -- "${gcda_file}").txt"
    run_logged "${dump_file}" "${GCOV_DUMP}" -l "${gcda_file}"
    "${RG}" -q 'OBJECT_SUMMARY runs=6([ ,]|$)' "${dump_file}" || \
        fail "profile does not prove all six training processes: ${gcda_file}"
    sed -n '1,240p' "${dump_file}" >>"${OUTPUT_ROOT}/gcov-dump.txt"
done
printf '%s\0' "${GCDA_FILES[@]}" | sort -z | \
    xargs -0 "${SHA256SUM}" >"${OUTPUT_ROOT}/profile-before-use.sha256"

"${NM}" -an "${GENERATION_ARTIFACT_DIR}/gcc-pgo-fixture" \
    >"${OUTPUT_ROOT}/generation-executable.nm"
"${NM}" -an "${GENERATION_ARTIFACT_DIR}/libgcc_pgo_fixture.so" \
    >"${OUTPUT_ROOT}/generation-shared.nm"
"${RG}" -q '__gcov_(init|exit|merge)' \
    "${OUTPUT_ROOT}/generation-executable.nm" || \
    fail 'generation executable has no GCC instrumentation runtime symbols'
"${RG}" -q '__gcov_(init|exit|merge)' \
    "${OUTPUT_ROOT}/generation-shared.nm" || \
    fail 'generation DSO has no GCC instrumentation runtime symbols'

build_profile_use "${PROFILE_DIR}" "${OUTPUT_ROOT}/logs/profile-use" yes
copy_pair "${USE_ARTIFACT_DIR}"
run_workload_matrix "${USE_ARTIFACT_DIR}/gcc-pgo-fixture" \
    "${OUTPUT_ROOT}/profile-use-workloads.txt"
cmp -s "${OUTPUT_ROOT}/generation-workloads.txt" \
    "${OUTPUT_ROOT}/profile-use-workloads.txt" || \
    fail 'profile-use executable output differs from the generation executable'

: >"${OUTPUT_ROOT}/profile-consumption.txt"
for profile_dump in "${OUTPUT_ROOT}"/logs/profile-use/*.profile-details; do
    "${RG}" -q 'Profile feedback for function is available' "${profile_dump}" || \
        fail "GCC did not report profile feedback in ${profile_dump}"
    "${RG}" -q '[0-9]+ edge counts read' "${profile_dump}" || \
        fail "GCC did not report reading edge counts in ${profile_dump}"
    "${RG}" -n '([0-9]+ edge counts read|Profile feedback for function is available)' \
        "${profile_dump}" >>"${OUTPUT_ROOT}/profile-consumption.txt"
done
[[ $(find "${OUTPUT_ROOT}/logs/profile-use" -type f -name '*.profile-details' | wc -l) -eq 4 ]] || \
    fail 'expected one GCC profile-consumption dump per translation unit'

"${NM}" -an "${USE_ARTIFACT_DIR}/gcc-pgo-fixture" \
    >"${OUTPUT_ROOT}/profile-use-executable.nm"
"${NM}" -an "${USE_ARTIFACT_DIR}/libgcc_pgo_fixture.so" \
    >"${OUTPUT_ROOT}/profile-use-shared.nm"
if "${RG}" -q '__gcov_(init|exit|merge)' \
    "${OUTPUT_ROOT}/profile-use-executable.nm" \
    "${OUTPUT_ROOT}/profile-use-shared.nm"; then
    fail 'profile-use artifacts still contain GCC generation runtime symbols'
fi

mkdir -p -- "${CORRECTION_PROFILE_DIR}"
cp -a -- "${PROFILE_DIR}/." "${CORRECTION_PROFILE_DIR}/"
mapfile -d '' correction_targets < <(
    find "${CORRECTION_PROFILE_DIR}" -type f -name 'plugin_core.gcda' -print0
)
[[ ${#correction_targets[@]} -eq 1 ]] || \
    fail "expected one copied plugin_core.gcda, found ${#correction_targets[@]}"
run_logged "${OUTPUT_ROOT}/correction-corruption.txt" \
    "${PYTHON}" "${SCRIPT_DIR}/corrupt-gcda.py" \
    --counter-index 0 --value 1152921504606846976 \
    "${correction_targets[0]}"
run_logged "${OUTPUT_ROOT}/correction-corrupt-gcov-dump.txt" \
    "${GCOV_DUMP}" -l "${correction_targets[0]}"
"${RG}" -q '1152921504606846976' \
    "${OUTPUT_ROOT}/correction-corrupt-gcov-dump.txt" || \
    fail 'the correction profile counter was not changed as requested'

NO_CORRECTION_FLAGS=(
    "-fprofile-use=${CORRECTION_PROFILE_DIR}"
    -Wcoverage-mismatch
    -Wmissing-profile
    -Werror=coverage-mismatch
    -Werror=missing-profile
)
expect_failure NO_CORRECTION_STATUS \
    "${OUTPUT_ROOT}/correction-without-flag.log" \
    "${GCC}" "${COMMON_COMPILE_FLAGS[@]}" \
    "${NO_CORRECTION_FLAGS[@]}" \
    -c "${SCRIPT_DIR}/plugin_core.c" -o "${BUILD_DIR}/plugin_core.o"
"${RG}" -q 'corrupted profile info: profile data is not flow-consistent' \
    "${OUTPUT_ROOT}/correction-without-flag.log" || \
    fail 'uncorrected corrupt profile did not produce the expected GCC diagnostic'

build_profile_use "${CORRECTION_PROFILE_DIR}" \
    "${OUTPUT_ROOT}/logs/profile-correction" yes
"${RG}" -q 'note: correcting inconsistent profile data' \
    "${OUTPUT_ROOT}/logs/profile-correction/plugin_core.profile-details" || \
    fail 'GCC did not record its correction of the inconsistent profile'
copy_pair "${CORRECTION_ARTIFACT_DIR}"
run_workload_matrix "${CORRECTION_ARTIFACT_DIR}/gcc-pgo-fixture" \
    "${OUTPUT_ROOT}/profile-correction-workloads.txt"
cmp -s "${OUTPUT_ROOT}/generation-workloads.txt" \
    "${OUTPUT_ROOT}/profile-correction-workloads.txt" || \
    fail 'corrected-profile executable output differs from the generation executable'

mkdir -p -- "${EMPTY_PROFILE_DIR}"
MISSING_PROFILE_FLAGS=(
    "-fprofile-use=${EMPTY_PROFILE_DIR}"
    -fprofile-correction
    -Wmissing-profile
    -Werror=missing-profile
)
expect_failure MISSING_PROFILE_STATUS \
    "${OUTPUT_ROOT}/missing-profile.log" \
    "${GCC}" "${COMMON_COMPILE_FLAGS[@]}" \
    "${MISSING_PROFILE_FLAGS[@]}" \
    -c "${SCRIPT_DIR}/plugin_core.c" -o "${BUILD_DIR}/plugin_core.o"
"${RG}" -q '(profile count data file not found|missing-profile)' \
    "${OUTPUT_ROOT}/missing-profile.log" || \
    fail 'empty absolute profile directory did not produce a missing-profile diagnostic'

MISMATCH_FLAGS=(
    "-fprofile-use=${PROFILE_DIR}"
    -fprofile-correction
    -Wcoverage-mismatch
    -Werror=coverage-mismatch
)
expect_failure MISMATCH_STATUS "${OUTPUT_ROOT}/coverage-mismatch.log" \
    "${GCC}" "${COMMON_COMPILE_FLAGS[@]}" \
    "${MISMATCH_FLAGS[@]}" -DGCC_PGO_FORCE_MISMATCH=1 \
    -c "${SCRIPT_DIR}/plugin_core.c" -o "${BUILD_DIR}/plugin_core.o"
"${RG}" -q 'does not match its profile data.*coverage-mismatch' \
    "${OUTPUT_ROOT}/coverage-mismatch.log" || \
    fail 'changed control flow did not produce the expected coverage-mismatch diagnostic'

printf '%s\0' "${GCDA_FILES[@]}" | sort -z | \
    xargs -0 "${SHA256SUM}" >"${OUTPUT_ROOT}/profile-after-use.sha256"
cmp -s "${OUTPUT_ROOT}/profile-before-use.sha256" \
    "${OUTPUT_ROOT}/profile-after-use.sha256" || \
    fail 'the original GCC profile pool changed during profile-use validation'

mapfile -d '' LLVM_PROFILE_FILES < <(
    find "${OUTPUT_ROOT}" -type f \
        \( -name '*.profraw' -o -name '*.profdata' -o -name '*.prof' \) \
        -print0
)
printf '%s\n' "${LLVM_PROFILE_FILES[@]}" >"${OUTPUT_ROOT}/llvm-profile-files.txt"
[[ ${#LLVM_PROFILE_FILES[@]} -eq 0 ]] || \
    fail "LLVM profile files unexpectedly participated: ${LLVM_PROFILE_FILES[*]}"

generation_executable_sha=$(
    "${SHA256SUM}" "${GENERATION_ARTIFACT_DIR}/gcc-pgo-fixture"
)
generation_executable_sha=${generation_executable_sha%% *}
generation_shared_sha=$(
    "${SHA256SUM}" "${GENERATION_ARTIFACT_DIR}/libgcc_pgo_fixture.so"
)
generation_shared_sha=${generation_shared_sha%% *}
use_executable_sha=$(
    "${SHA256SUM}" "${USE_ARTIFACT_DIR}/gcc-pgo-fixture"
)
use_executable_sha=${use_executable_sha%% *}
use_shared_sha=$(
    "${SHA256SUM}" "${USE_ARTIFACT_DIR}/libgcc_pgo_fixture.so"
)
use_shared_sha=${use_shared_sha%% *}

# The jq program intentionally refers to jq variables, not shell variables.
# shellcheck disable=SC2016
"${JQ}" -n \
    --arg status passed \
    --arg gcc_config_selection "${GCC_CONFIG_SELECTION}" \
    --arg active_gcc "${GCC}" \
    --arg active_gcc_cpv "${ACTIVE_GCC_CPV}" \
    --arg gcc_version "${GCC_VERSION}" \
    --arg gcc_machine "${GCC_MACHINE}" \
    --arg profile_dir "${PROFILE_DIR}" \
    --arg generation_executable_sha256 "${generation_executable_sha}" \
    --arg generation_shared_sha256 "${generation_shared_sha}" \
    --arg use_executable_sha256 "${use_executable_sha}" \
    --arg use_shared_sha256 "${use_shared_sha}" \
    --argjson gcda_files "${#GCDA_FILES[@]}" \
    --argjson training_runs 6 \
    --argjson no_correction_exit "${NO_CORRECTION_STATUS}" \
    --argjson missing_profile_exit "${MISSING_PROFILE_STATUS}" \
    --argjson mismatch_exit "${MISMATCH_STATUS}" \
    '{
        status: $status,
        fixture: "gcc-pgo-executable-dso",
        gcc_config_selection: $gcc_config_selection,
        active_gcc: $active_gcc,
        active_gcc_cpv: $active_gcc_cpv,
        gcc_version: $gcc_version,
        gcc_machine: $gcc_machine,
        profile_dir: $profile_dir,
        profile_dir_is_absolute: true,
        gcda_files: $gcda_files,
        training_runs: $training_runs,
        profile_feedback_dumps: 4,
        generation_and_use_outputs_match: true,
        corrected_profile_output_matches: true,
        profile_pool_unchanged_by_use: true,
        correction_without_flag_rejected: true,
        correction_without_flag_exit: $no_correction_exit,
        correction_with_flag_verified: true,
        changed_control_flow_rejected: true,
        changed_control_flow_exit: $mismatch_exit,
        empty_profile_dir_rejected: true,
        empty_profile_dir_exit: $missing_profile_exit,
        llvm_profile_file_environment: "unset",
        llvm_profile_files: 0,
        generation_executable_sha256: $generation_executable_sha256,
        generation_shared_sha256: $generation_shared_sha256,
        use_executable_sha256: $use_executable_sha256,
        use_shared_sha256: $use_shared_sha256
    }' >"${OUTPUT_ROOT}/result.json"
"${JQ}" -e '
    .status == "passed" and
    .gcda_files == 4 and
    .training_runs == 6 and
    .profile_feedback_dumps == 4 and
    .correction_without_flag_rejected and
    .correction_with_flag_verified and
    .changed_control_flow_rejected and
    .empty_profile_dir_rejected and
    .llvm_profile_files == 0
' "${OUTPUT_ROOT}/result.json" >/dev/null

{
    printf 'result=passed\n'
    printf 'active_gcc=%s\n' "${GCC}"
    printf 'active_gcc_cpv=%s\n' "${ACTIVE_GCC_CPV}"
    printf 'profile_dir=%s\n' "${PROFILE_DIR}"
    printf 'gcda_files=%d\n' "${#GCDA_FILES[@]}"
    printf 'training_runs=6\n'
    printf 'valid_profile_use=passed\n'
    printf 'functional_comparison=passed\n'
    printf 'corrupt_profile_without_correction_exit=%d\n' "${NO_CORRECTION_STATUS}"
    printf 'corrupt_profile_with_correction=passed\n'
    printf 'coverage_mismatch_exit=%d\n' "${MISMATCH_STATUS}"
    printf 'missing_profile_exit=%d\n' "${MISSING_PROFILE_STATUS}"
    printf 'llvm_profile_files=0\n'
} >"${OUTPUT_ROOT}/verification-summary.txt"

set +x
printf 'PASS: GCC PGO executable/DSO fixture; evidence: %s\n' \
    "${OUTPUT_ROOT}/result.json"

#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly SCRIPT_DIR
OUTPUT_ROOT=${1:-}
readonly ITERATIONS=${RUST_PGO_ITERATIONS:-2000000}

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
[[ ! -e ${OUTPUT_ROOT} ]] || fail "output path already exists: ${OUTPUT_ROOT}"
[[ ${ITERATIONS} =~ ^[1-9][0-9]*$ ]] || fail 'RUST_PGO_ITERATIONS must be positive'

mkdir -p -- "${OUTPUT_ROOT}"
OUTPUT_ROOT=$(cd -- "${OUTPUT_ROOT}" && pwd -P)
readonly OUTPUT_ROOT
case ${OUTPUT_ROOT} in
    /tmp/*|/var/tmp/gentoo-optimization/*) ;;
    *) fail "canonical output directory escaped the allowed roots: ${OUTPUT_ROOT}" ;;
esac

readonly COMMAND_LOG="${OUTPUT_ROOT}/commands.log"
readonly RUN_LOG="${OUTPUT_ROOT}/run.log"
readonly PROFILE_DIR="${OUTPUT_ROOT}/raw"
readonly RAW_PATTERN="${PROFILE_DIR}/%m-%p.profraw"
readonly MERGED_PROFILE="${OUTPUT_ROOT}/merged.profdata"
readonly TARGET_GENERATE="${OUTPUT_ROOT}/target-generate"
readonly TARGET_USE="${OUTPUT_ROOT}/target-use"
readonly TARGET_MISMATCH="${OUTPUT_ROOT}/target-mismatch"
readonly TARGET_INVALID="${OUTPUT_ROOT}/target-invalid"
readonly TARGET_TEST="${OUTPUT_ROOT}/target-test"
mkdir -p -- "${PROFILE_DIR}"
exec > >(tee "${RUN_LOG}") 2>&1

record_command() {
    {
        printf 'COMMAND'
        printf ' %q' "$@"
        printf '\n'
    } >>"${COMMAND_LOG}"
}

run() {
    record_command "$@"
    "$@"
}

run_capture() {
    local log_file=$1
    shift
    record_command "$@"
    "$@" >"${log_file}" 2>&1
    cat -- "${log_file}"
}

expect_failure() {
    local status_name=$1
    local log_file=$2
    shift 2
    record_command "$@"
    set +e
    "$@" >"${log_file}" 2>&1
    local command_status=$?
    set -e
    cat -- "${log_file}"
    (( command_status != 0 )) || fail "negative command unexpectedly succeeded: $*"
    printf -v "${status_name}" '%s' "${command_status}"
}

resolve_tool() {
    local requested=$1
    local resolved
    resolved=$(command -v -- "${requested}") || fail "required tool not found: ${requested}"
    [[ ${resolved} == /* ]] || resolved="$(pwd -P)/${resolved}"
    printf '%s/%s\n' "$(cd -- "$(dirname -- "${resolved}")" && pwd -P)" \
        "$(basename -- "${resolved}")"
}

join_encoded_flags() {
    local IFS=$'\x1f'
    printf '%s' "$*"
}

RUSTC=$(resolve_tool "${RUSTC:-rustc}")
CARGO=$(resolve_tool "${CARGO:-cargo}")
RG=$(resolve_tool rg)
SHA256SUM=$(resolve_tool sha256sum)
READELF=$(resolve_tool readelf)
readonly RUSTC CARGO RG SHA256SUM READELF

run_capture "${OUTPUT_ROOT}/rustc-vV.txt" "${RUSTC}" -vV
run_capture "${OUTPUT_ROOT}/cargo-version.txt" "${CARGO}" -V
TARGET=$(${RUSTC} --print host-tuple)
readonly TARGET
[[ ${TARGET} =~ ^[A-Za-z0-9_.-]+$ ]] || fail "unsafe active target triple: ${TARGET}"
LLVM_VERSION=$(${RG} '^LLVM version:' "${OUTPUT_ROOT}/rustc-vV.txt" | \
    sed -E 's/^LLVM version:[[:space:]]*//')
LLVM_MAJOR=${LLVM_VERSION%%.*}
[[ ${LLVM_MAJOR} =~ ^[0-9]+$ ]] || fail 'could not determine rustc bundled LLVM major'
readonly LLVM_VERSION LLVM_MAJOR

if [[ -n ${LLVM_PROFDATA:-} ]]; then
    LLVM_PROFDATA=$(resolve_tool "${LLVM_PROFDATA}")
elif [[ -x /usr/lib/llvm/${LLVM_MAJOR}/bin/llvm-profdata ]]; then
    LLVM_PROFDATA=/usr/lib/llvm/${LLVM_MAJOR}/bin/llvm-profdata
else
    LLVM_PROFDATA=$(resolve_tool llvm-profdata)
fi
readonly LLVM_PROFDATA
run_capture "${OUTPUT_ROOT}/llvm-profdata-version.txt" "${LLVM_PROFDATA}" --version
PROFDATA_MAJOR=$(${RG} -o 'LLVM version [0-9]+' \
    "${OUTPUT_ROOT}/llvm-profdata-version.txt" | awk '{print $3}' | head -n 1)
[[ ${PROFDATA_MAJOR} == "${LLVM_MAJOR}" ]] || \
    fail "rustc LLVM ${LLVM_MAJOR} requires matching llvm-profdata, found ${PROFDATA_MAJOR:-unknown}"

COMMON_FLAGS=(
    -Ccodegen-units=1
    -Clto=thin
    -Cdebuginfo=1
    -Cstrip=none
    -Csymbol-mangling-version=v0
    "-Clink-arg=-Wl,--build-id=sha1"
)
GENERATE_FLAGS=("-Cprofile-generate=${PROFILE_DIR}" "${COMMON_FLAGS[@]}")
USE_FLAGS=(
    "-Cprofile-use=${MERGED_PROFILE}"
    -Cllvm-args=-pgo-warn-missing-function
    "${COMMON_FLAGS[@]}"
)
GENERATE_ENCODED=$(join_encoded_flags "${GENERATE_FLAGS[@]}")
USE_ENCODED=$(join_encoded_flags "${USE_FLAGS[@]}")
readonly GENERATE_ENCODED USE_ENCODED

run_capture "${OUTPUT_ROOT}/cargo-test.log" \
    env CARGO_INCREMENTAL=0 CARGO_TARGET_DIR="${TARGET_TEST}" RUSTC="${RUSTC}" \
    "${CARGO}" test --manifest-path "${SCRIPT_DIR}/Cargo.toml" \
    --release --locked --offline --target "${TARGET}"

run_capture "${OUTPUT_ROOT}/generation-build.log" \
    env CARGO_ENCODED_RUSTFLAGS="${GENERATE_ENCODED}" CARGO_INCREMENTAL=0 \
    CARGO_TARGET_DIR="${TARGET_GENERATE}" RUSTC="${RUSTC}" \
    "${CARGO}" build --manifest-path "${SCRIPT_DIR}/Cargo.toml" \
    --release --locked --offline --target "${TARGET}" -vv

"${RG}" -- '--crate-name build_script_build' \
    "${OUTPUT_ROOT}/generation-build.log" >"${OUTPUT_ROOT}/host-build-script-command.txt"
[[ -s ${OUTPUT_ROOT}/host-build-script-command.txt ]] || \
    fail 'Cargo log contains no host build-script compilation command'
if "${RG}" -Fq -- "profile-generate=${PROFILE_DIR}" \
    "${OUTPUT_ROOT}/host-build-script-command.txt"; then
    fail 'Cargo host build script was accidentally instrumented'
fi
"${RG}" -- '--crate-name rust_pgo_fixture' \
    "${OUTPUT_ROOT}/generation-build.log" >"${OUTPUT_ROOT}/target-rustc-commands.txt"
[[ -s ${OUTPUT_ROOT}/target-rustc-commands.txt ]] || \
    fail 'Cargo log contains no target crate compilation command'
"${RG}" -Fq -- "profile-generate=${PROFILE_DIR}" \
    "${OUTPUT_ROOT}/target-rustc-commands.txt" || \
    fail 'target crate commands lack the absolute profile-generation path'

if find "${PROFILE_DIR}" -type f -name '*.profraw' -print -quit | "${RG}" -q .; then
    fail 'profile data appeared during the Cargo build; a host tool was likely instrumented'
fi

readonly GENERATE_BINARY="${TARGET_GENERATE}/${TARGET}/release/rust-pgo-fixture"
[[ -x ${GENERATE_BINARY} ]] || fail "generation binary missing: ${GENERATE_BINARY}"
training_pids=()
for mode in 1 2 3 4 5 6; do
    record_command env LLVM_PROFILE_FILE="${RAW_PATTERN}" \
        "${GENERATE_BINARY}" "${mode}" "${ITERATIONS}"
    LLVM_PROFILE_FILE="${RAW_PATTERN}" \
        "${GENERATE_BINARY}" "${mode}" "${ITERATIONS}" \
        >"${OUTPUT_ROOT}/training-${mode}.txt" 2>&1 &
    training_pids+=("$!")
done
for pid in "${training_pids[@]}"; do
    wait "${pid}"
done

mapfile -d '' RAW_PROFILES < <(
    find "${PROFILE_DIR}" -type f -name '*.profraw' -size +0c -print0 | sort -z
)
(( ${#RAW_PROFILES[@]} >= 6 )) || \
    fail "expected at least six nonempty Rust raw profiles, found ${#RAW_PROFILES[@]}"
RAW_PIDS=()
for raw_profile in "${RAW_PROFILES[@]}"; do
    raw_name=${raw_profile##*/}
    raw_pid=${raw_name##*-}
    raw_pid=${raw_pid%.profraw}
    [[ ${raw_pid} =~ ^[0-9]+$ ]] || \
        fail "raw Rust profile lacks the requested process-id suffix: ${raw_name}"
    RAW_PIDS+=("${raw_pid}")
done
mapfile -t UNIQUE_RAW_PIDS < <(printf '%s\n' "${RAW_PIDS[@]}" | sort -u)
(( ${#UNIQUE_RAW_PIDS[@]} >= 6 )) || \
    fail "expected profiles from six Rust processes, found ${#UNIQUE_RAW_PIDS[@]}"
printf '%s\n' "${RAW_PROFILES[@]}" >"${OUTPUT_ROOT}/raw-profiles.list"
run "${LLVM_PROFDATA}" merge --failure-mode=all \
    --input-files="${OUTPUT_ROOT}/raw-profiles.list" -o "${MERGED_PROFILE}"
run_capture "${OUTPUT_ROOT}/merged-profile.txt" "${LLVM_PROFDATA}" show \
    --all-functions --counts "${MERGED_PROFILE}"
[[ -s ${MERGED_PROFILE} ]] || fail 'merged Rust profile is empty'
"${RG}" -q '^Instrumentation level: IR' "${OUTPUT_ROOT}/merged-profile.txt" || \
    fail 'merged Rust data is not an LLVM IR instrumentation profile'
"${RG}" -q 'rust_pgo_fixture' "${OUTPUT_ROOT}/merged-profile.txt" || \
    fail 'merged profile lacks the Rust fixture crate'

run_capture "${OUTPUT_ROOT}/profile-use-build.log" \
    env CARGO_ENCODED_RUSTFLAGS="${USE_ENCODED}" CARGO_INCREMENTAL=0 \
    CARGO_TARGET_DIR="${TARGET_USE}" RUSTC="${RUSTC}" \
    "${CARGO}" build --manifest-path "${SCRIPT_DIR}/Cargo.toml" \
    --release --locked --offline --target "${TARGET}" -vv
"${RG}" -Fq -- "profile-use=${MERGED_PROFILE}" \
    "${OUTPUT_ROOT}/profile-use-build.log" || \
    fail 'Cargo use log lacks the absolute Rust profile-use path'
"${RG}" -Fq -- '-pgo-warn-missing-function' \
    "${OUTPUT_ROOT}/profile-use-build.log" || \
    fail 'Rust use build did not enable missing-function warnings'
if "${RG}" -qi 'no profile data available for function|invalid instrumentation profile|bad magic' \
    "${OUTPUT_ROOT}/profile-use-build.log"; then
    fail 'positive Rust profile-use build emitted a missing or invalid profile diagnostic'
fi
"${RG}" -- '--crate-name build_script_build' \
    "${OUTPUT_ROOT}/profile-use-build.log" \
    >"${OUTPUT_ROOT}/host-profile-use-build-script-command.txt"
[[ -s ${OUTPUT_ROOT}/host-profile-use-build-script-command.txt ]] || \
    fail 'Rust use log contains no host build-script compilation command'
if "${RG}" -Fq -- "profile-use=${MERGED_PROFILE}" \
    "${OUTPUT_ROOT}/host-profile-use-build-script-command.txt"; then
    fail 'Cargo host build script accidentally received the Rust profile-use flag'
fi

readonly USE_BINARY="${TARGET_USE}/${TARGET}/release/rust-pgo-fixture"
[[ -x ${USE_BINARY} ]] || fail "profile-use binary missing: ${USE_BINARY}"
for mode in 1 2 3 4 5 6; do
    run "${USE_BINARY}" "${mode}" "${ITERATIONS}" \
        >"${OUTPUT_ROOT}/profile-use-${mode}.txt"
    cmp -- "${OUTPUT_ROOT}/training-${mode}.txt" \
        "${OUTPUT_ROOT}/profile-use-${mode}.txt"
done

run_capture "${OUTPUT_ROOT}/profile-use-readelf.txt" \
    "${READELF}" -h -n "${USE_BINARY}"
"${RG}" -q 'Build ID:' "${OUTPUT_ROOT}/profile-use-readelf.txt" || \
    fail 'profile-use Rust ELF lacks a GNU build ID'

set +e
record_command env CARGO_ENCODED_RUSTFLAGS="${USE_ENCODED}" CARGO_INCREMENTAL=0 \
    CARGO_TARGET_DIR="${TARGET_MISMATCH}" RUSTC="${RUSTC}" \
    "${CARGO}" build --manifest-path "${SCRIPT_DIR}/Cargo.toml" \
    --release --locked --offline --target "${TARGET}" \
    --features profile-mismatch -vv
env CARGO_ENCODED_RUSTFLAGS="${USE_ENCODED}" CARGO_INCREMENTAL=0 \
    CARGO_TARGET_DIR="${TARGET_MISMATCH}" RUSTC="${RUSTC}" \
    "${CARGO}" build --manifest-path "${SCRIPT_DIR}/Cargo.toml" \
    --release --locked --offline --target "${TARGET}" \
    --features profile-mismatch -vv >"${OUTPUT_ROOT}/profile-mismatch.log" 2>&1
MISMATCH_STATUS=$?
set -e
cat -- "${OUTPUT_ROOT}/profile-mismatch.log"
"${RG}" -qi 'no profile data available for function|missing function.*profile|function.*not in profile' \
    "${OUTPUT_ROOT}/profile-mismatch.log" || \
    fail 'changed Rust feature did not emit the enabled missing-function diagnostic'

readonly INVALID_PROFILE="${OUTPUT_ROOT}/invalid.profdata"
printf 'not an LLVM indexed instrumentation profile\n' >"${INVALID_PROFILE}"
INVALID_FLAGS=("-Cprofile-use=${INVALID_PROFILE}" -Dwarnings "${COMMON_FLAGS[@]}")
INVALID_ENCODED=$(join_encoded_flags "${INVALID_FLAGS[@]}")
record_command env CARGO_ENCODED_RUSTFLAGS="${INVALID_ENCODED}" CARGO_INCREMENTAL=0 \
    CARGO_TARGET_DIR="${TARGET_INVALID}" RUSTC="${RUSTC}" \
    "${CARGO}" build --manifest-path "${SCRIPT_DIR}/Cargo.toml" \
    --release --locked --offline --target "${TARGET}" -vv
set +e
env CARGO_ENCODED_RUSTFLAGS="${INVALID_ENCODED}" CARGO_INCREMENTAL=0 \
    CARGO_TARGET_DIR="${TARGET_INVALID}" RUSTC="${RUSTC}" \
    "${CARGO}" build --manifest-path "${SCRIPT_DIR}/Cargo.toml" \
    --release --locked --offline --target "${TARGET}" -vv \
    >"${OUTPUT_ROOT}/invalid-profile.log" 2>&1
INVALID_COMPILER_STATUS=$?
set -e
cat -- "${OUTPUT_ROOT}/invalid-profile.log"
"${RG}" -qi 'invalid instrumentation profile data|invalid profile|malformed|bad magic|failed to load profile' \
    "${OUTPUT_ROOT}/invalid-profile.log" || \
    fail 'malformed Rust profile consumption lacks a visible format diagnostic'
expect_failure INVALID_VALIDATOR_STATUS \
    "${OUTPUT_ROOT}/invalid-profile-validator.log" \
    "${LLVM_PROFDATA}" show "${INVALID_PROFILE}"
"${RG}" -qi 'truncated profile data|invalid instrumentation profile data|invalid profile|bad magic' \
    "${OUTPUT_ROOT}/invalid-profile-validator.log" || \
    fail 'llvm-profdata rejected malformed Rust data without a format diagnostic'

PROFILE_FUNCTION_COUNT=$(
    sed -n 's/^Total functions: //p' "${OUTPUT_ROOT}/merged-profile.txt" | tail -n 1
)
[[ ${PROFILE_FUNCTION_COUNT} =~ ^[1-9][0-9]*$ ]] || \
    fail 'could not determine a nonzero merged Rust function count'
RUSTC_REAL=$(readlink -f -- "${RUSTC}")
CARGO_REAL=$(readlink -f -- "${CARGO}")
SOURCE_SHA=$(
    "${SHA256SUM}" "${SCRIPT_DIR}"/Cargo.toml "${SCRIPT_DIR}"/Cargo.lock \
        "${SCRIPT_DIR}"/build.rs "${SCRIPT_DIR}"/src/lib.rs \
        "${SCRIPT_DIR}"/src/main.rs "${SCRIPT_DIR}"/README.md \
        "${SCRIPT_DIR}"/run.sh | "${SHA256SUM}" | awk '{print $1}'
)
RUSTC_SHA=$("${SHA256SUM}" "${RUSTC_REAL}" | awk '{print $1}')
CARGO_SHA=$("${SHA256SUM}" "${CARGO_REAL}" | awk '{print $1}')
PROFDATA_SHA=$("${SHA256SUM}" "${LLVM_PROFDATA}" | awk '{print $1}')
MERGED_SHA=$("${SHA256SUM}" "${MERGED_PROFILE}" | awk '{print $1}')
GENERATE_SHA=$("${SHA256SUM}" "${GENERATE_BINARY}" | awk '{print $1}')
USE_SHA=$("${SHA256SUM}" "${USE_BINARY}" | awk '{print $1}')

{
    printf 'result=PASS\n'
    printf 'fixture=rust-llvm-ir-pgo-cargo\n'
    printf 'rustc=%s\nrustc_sha256=%s\n' "${RUSTC}" "${RUSTC_SHA}"
    printf 'cargo=%s\ncargo_sha256=%s\n' "${CARGO}" "${CARGO_SHA}"
    printf 'rustc_release=%s\nrustc_commit=%s\n' \
        "$(sed -n 's/^release: //p' "${OUTPUT_ROOT}/rustc-vV.txt")" \
        "$(sed -n 's/^commit-hash: //p' "${OUTPUT_ROOT}/rustc-vV.txt")"
    printf 'target=%s\nrustc_llvm_version=%s\n' "${TARGET}" "${LLVM_VERSION}"
    printf 'llvm_profdata=%s\nllvm_profdata_sha256=%s\n' \
        "${LLVM_PROFDATA}" "${PROFDATA_SHA}"
    printf 'profile_generate_path=%s\nprofile_use_path=%s\n' \
        "${PROFILE_DIR}" "${MERGED_PROFILE}"
    printf 'runtime_raw_pattern=%s\n' "${RAW_PATTERN}"
    printf 'raw_profile_count=%s\nraw_process_count=%s\ntraining_process_count=6\n' \
        "${#RAW_PROFILES[@]}" "${#UNIQUE_RAW_PIDS[@]}"
    printf 'merged_profile_sha256=%s\nmerged_profile_function_count=%s\n' \
        "${MERGED_SHA}" "${PROFILE_FUNCTION_COUNT}"
    printf 'cargo_target_was_explicit=true\nhost_build_script_instrumented=false\n'
    printf 'missing_function_warning_enabled=true\nprofile_mismatch_exit_status=%s\n' \
        "${MISMATCH_STATUS}"
    printf 'profile_mismatch_diagnostic=missing-function-visible\n'
    printf 'invalid_profile_compiler_exit_status=%s\n' "${INVALID_COMPILER_STATUS}"
    printf 'invalid_profile_validator_exit_status=%s\ninvalid_profile_rejected=true\n' \
        "${INVALID_VALIDATOR_STATUS}"
    printf 'fixture_source_sha256=%s\n' "${SOURCE_SHA}"
    printf 'generation_binary_sha256=%s\nprofile_use_binary_sha256=%s\n' \
        "${GENERATE_SHA}" "${USE_SHA}"
    printf 'functional_outputs_match=true\n'
} | tee "${OUTPUT_ROOT}/validation-summary.txt"

"${SHA256SUM}" "${COMMAND_LOG}" "${OUTPUT_ROOT}/rustc-vV.txt" \
    "${OUTPUT_ROOT}/cargo-version.txt" \
    "${OUTPUT_ROOT}/llvm-profdata-version.txt" \
    "${OUTPUT_ROOT}/generation-build.log" \
    "${OUTPUT_ROOT}/host-build-script-command.txt" \
    "${OUTPUT_ROOT}/target-rustc-commands.txt" \
    "${OUTPUT_ROOT}/merged-profile.txt" "${MERGED_PROFILE}" \
    "${OUTPUT_ROOT}/profile-use-build.log" \
    "${OUTPUT_ROOT}/host-profile-use-build-script-command.txt" \
    "${OUTPUT_ROOT}/profile-mismatch.log" \
    "${OUTPUT_ROOT}/invalid-profile.log" \
    "${OUTPUT_ROOT}/invalid-profile-validator.log" \
    "${OUTPUT_ROOT}/validation-summary.txt" "${GENERATE_BINARY}" "${USE_BINARY}" \
    "${RAW_PROFILES[@]}" >"${OUTPUT_ROOT}/evidence.sha256"

printf 'PASS: Rust Cargo instrumentation PGO, host/target isolation, profile use, and negative checks\n'

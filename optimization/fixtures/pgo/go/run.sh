#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly SCRIPT_DIR
OUTPUT_ROOT=${1:-}
readonly ITERATIONS=${GO_PGO_ITERATIONS:-300000000}

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
[[ ${ITERATIONS} =~ ^[1-9][0-9]*$ ]] || fail 'GO_PGO_ITERATIONS must be positive'

mkdir -p -- "${OUTPUT_ROOT}"
OUTPUT_ROOT=$(cd -- "${OUTPUT_ROOT}" && pwd -P)
readonly OUTPUT_ROOT
case ${OUTPUT_ROOT} in
    /tmp/*|/var/tmp/gentoo-optimization/*) ;;
    *) fail "canonical output directory escaped the allowed roots: ${OUTPUT_ROOT}" ;;
esac

readonly COMMAND_LOG="${OUTPUT_ROOT}/commands.log"
readonly RUN_LOG="${OUTPUT_ROOT}/run.log"
readonly BASELINE_BINARY="${OUTPUT_ROOT}/go-pgo-baseline"
readonly PGO_BINARY="${OUTPUT_ROOT}/go-pgo-use"
readonly PROFILE="${OUTPUT_ROOT}/cpu.pprof"
readonly UNRELATED_BINARY="${OUTPUT_ROOT}/unrelated"
readonly UNRELATED_PROFILE="${OUTPUT_ROOT}/unrelated.pprof"
readonly INVALID_PROFILE="${OUTPUT_ROOT}/invalid.pprof"
readonly TEST_CACHE="${OUTPUT_ROOT}/gocache-test"
readonly BASELINE_CACHE="${OUTPUT_ROOT}/gocache-baseline"
readonly PGO_CACHE="${OUTPUT_ROOT}/gocache-pgo"
readonly NEGATIVE_CACHE="${OUTPUT_ROOT}/gocache-negative"
readonly MISMATCH_CACHE="${OUTPUT_ROOT}/gocache-mismatch"
mkdir -p -- "${TEST_CACHE}" "${BASELINE_CACHE}" "${PGO_CACHE}" \
    "${NEGATIVE_CACHE}" "${MISMATCH_CACHE}"
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

GO=$(resolve_tool "${GO:-go}")
RG=$(resolve_tool rg)
SHA256SUM=$(resolve_tool sha256sum)
READELF=$(resolve_tool readelf)
readonly GO RG SHA256SUM READELF

GO_ENV=(env GOENV=off GOFLAGS= GOTOOLCHAIN=local CGO_ENABLED=0)
run_capture "${OUTPUT_ROOT}/go-version.txt" "${GO}" version
run_capture "${OUTPUT_ROOT}/go-env.txt" "${GO}" env \
    GOOS GOARCH GOVERSION GOAMD64 GOTOOLCHAIN GOEXPERIMENT CGO_ENABLED
GO_VERSION=$(${GO} env GOVERSION)
GO_OS=$(${GO} env GOOS)
GO_ARCH=$(${GO} env GOARCH)
readonly GO_VERSION GO_OS GO_ARCH
[[ ${GO_OS}/${GO_ARCH} == linux/amd64 ]] || \
    fail "fixture requires the active linux/amd64 Go lane, found ${GO_OS}/${GO_ARCH}"

run_capture "${OUTPUT_ROOT}/go-test.log" \
    "${GO_ENV[@]}" GOCACHE="${TEST_CACHE}" "${GO}" -C "${SCRIPT_DIR}" test \
    -count=1 -mod=readonly -pgo=off ./...

run_capture "${OUTPUT_ROOT}/baseline-build.log" \
    "${GO_ENV[@]}" GOCACHE="${BASELINE_CACHE}" "${GO}" -C "${SCRIPT_DIR}" build -x \
    -mod=readonly -buildvcs=false -pgo=off -o "${BASELINE_BINARY}" \
    ./cmd/go-pgo-fixture
[[ -x ${BASELINE_BINARY} ]] || fail 'baseline Go command was not produced'
run_capture "${OUTPUT_ROOT}/baseline-readelf.txt" \
    "${READELF}" -n "${BASELINE_BINARY}"
BASELINE_BUILD_ID=$(
    sed -n 's/.*Build ID: //p' "${OUTPUT_ROOT}/baseline-readelf.txt" | head -n 1
)
[[ ${BASELINE_BUILD_ID} =~ ^[0-9a-f]{40}$ ]] || \
    fail 'baseline Go command lacks a SHA-1 GNU build ID'

run "${BASELINE_BINARY}" -mode=1 -iterations="${ITERATIONS}" \
    >"${OUTPUT_ROOT}/baseline-output.txt"
run "${BASELINE_BINARY}" -mode=1 -iterations="${ITERATIONS}" \
    -cpuprofile="${PROFILE}" >"${OUTPUT_ROOT}/training-output.txt"
cmp -- "${OUTPUT_ROOT}/baseline-output.txt" "${OUTPUT_ROOT}/training-output.txt"
[[ -s ${PROFILE} ]] || fail 'Go CPU workload profile is empty'

run_capture "${OUTPUT_ROOT}/pprof-raw.txt" \
    "${GO}" tool pprof -raw "${BASELINE_BINARY}" "${PROFILE}"
run_capture "${OUTPUT_ROOT}/pprof-top.txt" \
    "${GO}" tool pprof -top -nodecount=30 "${BASELINE_BINARY}" "${PROFILE}"
"${RG}" -q 'fixturework\.Hot(Even|Odd)' "${OUTPUT_ROOT}/pprof-raw.txt" || \
    fail 'CPU pprof profile lacks fixture workload functions'
"${RG}" -Fq -- "${BASELINE_BINARY} ${BASELINE_BUILD_ID}" \
    "${OUTPUT_ROOT}/pprof-raw.txt" || \
    fail 'CPU pprof mapping does not match the exact baseline GNU build ID'
"${RG}" -q 'workload\.go:[1-9][0-9]*:[0-9]+ s=[1-9][0-9]*' \
    "${OUTPUT_ROOT}/pprof-raw.txt" || \
    fail 'CPU pprof profile lacks function start-line metadata'
"${RG}" -q 'fixturework\.inlineRotate \(inline\)' "${OUTPUT_ROOT}/pprof-top.txt" || \
    fail 'CPU pprof profile lacks reconstructed inline-frame information'
PROFILE_SAMPLE_COUNT=$(
    awk '
        /^Samples:/ { in_samples = 1; next }
        /^Locations/ { in_samples = 0 }
        in_samples && $1 ~ /^[0-9]+$/ { total += $1 }
        END { print total + 0 }
    ' "${OUTPUT_ROOT}/pprof-raw.txt"
)
[[ ${PROFILE_SAMPLE_COUNT} =~ ^[0-9]+$ && ${PROFILE_SAMPLE_COUNT} -ge 20 ]] || \
    fail "CPU pprof profile has too few samples: ${PROFILE_SAMPLE_COUNT:-unknown}"

run_capture "${OUTPUT_ROOT}/pgo-build.log" \
    "${GO_ENV[@]}" GOCACHE="${PGO_CACHE}" "${GO}" -C "${SCRIPT_DIR}" build -x -a \
    -mod=readonly -buildvcs=false "-pgo=${PROFILE}" \
    '-gcflags=example.com/gentoo-optimization/go-pgo-fixture/internal/fixturework=-d=pgodebug=3' \
    -o "${PGO_BINARY}" ./cmd/go-pgo-fixture
[[ -x ${PGO_BINARY} ]] || fail 'PGO-use Go command was not produced'
"${RG}" -q -- '-pgoprofile=' "${OUTPUT_ROOT}/pgo-build.log" || \
    fail 'Go compiler trace contains no processed -pgoprofile argument'
"${RG}" -Fq -- '-p example.com/gentoo-optimization/go-pgo-fixture/internal/fixturework' \
    "${OUTPUT_ROOT}/pgo-build.log" || \
    fail 'Go compiler trace lacks the fixture workload package'
"${RG}" -q -- 'compile .* -p example\.com/gentoo-optimization/go-pgo-fixture/internal/fixturework .* -pgoprofile=' \
    "${OUTPUT_ROOT}/pgo-build.log" || \
    fail 'fixture workload compiler invocation does not contain -pgoprofile'
"${RG}" -q 'hot-node enabled increased budget=.*fixturework\.inlineRotate' \
    "${OUTPUT_ROOT}/pgo-build.log" || \
    fail 'Go PGO debug trace contains no profile-driven hot-node decision'
if "${RG}" -qi 'preprofile: error|error parsing profile' "${OUTPUT_ROOT}/pgo-build.log"; then
    fail 'positive Go PGO build emitted a profile preprocessing error'
fi

run_capture "${OUTPUT_ROOT}/pgo-build-metadata.txt" \
    "${GO}" version -m "${PGO_BINARY}"
"${RG}" -Fq -- "-pgo=${PROFILE}" "${OUTPUT_ROOT}/pgo-build-metadata.txt" || \
    fail 'Go binary build metadata does not retain the exact absolute -pgo path'
run "${PGO_BINARY}" -mode=1 -iterations="${ITERATIONS}" \
    >"${OUTPUT_ROOT}/pgo-output.txt"
cmp -- "${OUTPUT_ROOT}/baseline-output.txt" "${OUTPUT_ROOT}/pgo-output.txt"
run_capture "${OUTPUT_ROOT}/pgo-readelf.txt" "${READELF}" -h -n "${PGO_BINARY}"

printf 'not a Go CPU pprof protobuf\n' >"${INVALID_PROFILE}"
expect_failure INVALID_STATUS "${OUTPUT_ROOT}/invalid-profile.log" \
    "${GO_ENV[@]}" GOCACHE="${NEGATIVE_CACHE}" "${GO}" -C "${SCRIPT_DIR}" build -x -a \
    -mod=readonly -buildvcs=false "-pgo=${INVALID_PROFILE}" \
    -o "${OUTPUT_ROOT}/must-not-build" ./cmd/go-pgo-fixture
"${RG}" -qi 'error parsing profile|preprocess.*profile|malformed|invalid.*profile|unrecognized profile' \
    "${OUTPUT_ROOT}/invalid-profile.log" || \
    fail 'malformed Go profile failure lacks a format diagnostic'

run_capture "${OUTPUT_ROOT}/unrelated-build.log" \
    "${GO_ENV[@]}" GOCACHE="${BASELINE_CACHE}" "${GO}" -C "${SCRIPT_DIR}" build \
    -mod=readonly -buildvcs=false -pgo=off -o "${UNRELATED_BINARY}" \
    ./cmd/unrelated
run "${UNRELATED_BINARY}" -iterations="${ITERATIONS}" \
    -cpuprofile="${UNRELATED_PROFILE}" >"${OUTPUT_ROOT}/unrelated-output.txt"
[[ -s ${UNRELATED_PROFILE} ]] || fail 'unrelated Go profile is empty'
run_capture "${OUTPUT_ROOT}/unrelated-pprof-raw.txt" \
    "${GO}" tool pprof -raw "${UNRELATED_BINARY}" "${UNRELATED_PROFILE}"
"${RG}" -q 'unrelatedMix' "${OUTPUT_ROOT}/unrelated-pprof-raw.txt" || \
    fail 'unrelated profile lacks its own hot function'
if "${RG}" -q 'fixturework\.Hot(Even|Odd)' "${OUTPUT_ROOT}/unrelated-pprof-raw.txt"; then
    fail 'unrelated profile unexpectedly contains target fixture functions'
fi

record_command "${GO_ENV[@]}" GOCACHE="${MISMATCH_CACHE}" "${GO}" \
    -C "${SCRIPT_DIR}" build -x -a \
    -mod=readonly -buildvcs=false "-pgo=${UNRELATED_PROFILE}" \
    '-gcflags=example.com/gentoo-optimization/go-pgo-fixture/internal/fixturework=-d=pgodebug=3' \
    -o "${OUTPUT_ROOT}/mismatched-profile-build" ./cmd/go-pgo-fixture
set +e
"${GO_ENV[@]}" GOCACHE="${MISMATCH_CACHE}" "${GO}" \
    -C "${SCRIPT_DIR}" build -x -a \
    -mod=readonly -buildvcs=false "-pgo=${UNRELATED_PROFILE}" \
    '-gcflags=example.com/gentoo-optimization/go-pgo-fixture/internal/fixturework=-d=pgodebug=3' \
    -o "${OUTPUT_ROOT}/mismatched-profile-build" ./cmd/go-pgo-fixture \
    >"${OUTPUT_ROOT}/mismatched-profile-build.log" 2>&1
MISMATCH_STATUS=$?
set -e
cat -- "${OUTPUT_ROOT}/mismatched-profile-build.log"
# Go intentionally permits source-stable profiles, so compiler success is not
# sufficient. The symbol check above is the fail-closed mismatch validator.
printf 'validator_result=REJECTED_NO_TARGET_SYMBOLS\ncompiler_exit_status=%s\n' \
    "${MISMATCH_STATUS}" >"${OUTPUT_ROOT}/mismatch-validation.txt"

SOURCE_SHA=$(
    "${SHA256SUM}" "${SCRIPT_DIR}/go.mod" \
        "${SCRIPT_DIR}/internal/fixturework/workload.go" \
        "${SCRIPT_DIR}/internal/fixturework/workload_test.go" \
        "${SCRIPT_DIR}/cmd/go-pgo-fixture/main.go" \
        "${SCRIPT_DIR}/cmd/unrelated/main.go" "${SCRIPT_DIR}/README.md" \
        "${SCRIPT_DIR}/run.sh" | "${SHA256SUM}" | awk '{print $1}'
)
GO_REAL=$(readlink -f -- "${GO}")
GO_SHA=$("${SHA256SUM}" "${GO_REAL}" | awk '{print $1}')
PROFILE_SHA=$("${SHA256SUM}" "${PROFILE}" | awk '{print $1}')
BASELINE_SHA=$("${SHA256SUM}" "${BASELINE_BINARY}" | awk '{print $1}')
PGO_SHA=$("${SHA256SUM}" "${PGO_BINARY}" | awk '{print $1}')
{
    printf 'result=PASS\nfixture=go-cpu-pprof-pgo\n'
    printf 'go=%s\ngo_sha256=%s\ngo_version=%s\n' "${GO}" "${GO_SHA}" "${GO_VERSION}"
    printf 'goos=%s\ngoarch=%s\n' "${GO_OS}" "${GO_ARCH}"
    printf 'pgo_profile=%s\npgo_profile_sha256=%s\n' "${PROFILE}" "${PROFILE_SHA}"
    printf 'pgo_profile_is_absolute=true\npgo_profile_sample_count=%s\n' \
        "${PROFILE_SAMPLE_COUNT}"
    printf 'profiled_baseline_gnu_build_id=%s\nprofile_mapping_identity_matches=true\n' \
        "${BASELINE_BUILD_ID}"
    printf 'pprof_target_symbols_present=true\npprof_start_lines_present=true\n'
    printf 'pprof_inline_frames_present=true\n'
    printf 'compiler_pgoprofile_argument_present=true\nbinary_pgo_build_setting_present=true\n'
    printf 'profile_driven_hot_node_diagnostic_present=true\n'
    printf 'invalid_profile_exit_status=%s\ninvalid_profile_rejected=true\n' \
        "${INVALID_STATUS}"
    printf 'mismatch_profile_compiler_exit_status=%s\n' "${MISMATCH_STATUS}"
    printf 'mismatch_profile_validator_result=REJECTED_NO_TARGET_SYMBOLS\n'
    printf 'fixture_source_sha256=%s\n' "${SOURCE_SHA}"
    printf 'baseline_binary_sha256=%s\npgo_binary_sha256=%s\n' \
        "${BASELINE_SHA}" "${PGO_SHA}"
    printf 'functional_outputs_match=true\n'
} | tee "${OUTPUT_ROOT}/validation-summary.txt"

"${SHA256SUM}" "${COMMAND_LOG}" "${OUTPUT_ROOT}/go-version.txt" \
    "${OUTPUT_ROOT}/go-env.txt" "${OUTPUT_ROOT}/go-test.log" \
    "${OUTPUT_ROOT}/baseline-build.log" "${OUTPUT_ROOT}/baseline-readelf.txt" \
    "${PROFILE}" \
    "${OUTPUT_ROOT}/pprof-raw.txt" "${OUTPUT_ROOT}/pprof-top.txt" \
    "${OUTPUT_ROOT}/pgo-build.log" "${OUTPUT_ROOT}/pgo-build-metadata.txt" \
    "${OUTPUT_ROOT}/invalid-profile.log" "${UNRELATED_PROFILE}" \
    "${OUTPUT_ROOT}/unrelated-pprof-raw.txt" \
    "${OUTPUT_ROOT}/mismatched-profile-build.log" \
    "${OUTPUT_ROOT}/mismatch-validation.txt" \
    "${OUTPUT_ROOT}/validation-summary.txt" "${BASELINE_BINARY}" "${PGO_BINARY}" \
    >"${OUTPUT_ROOT}/evidence.sha256"

printf 'PASS: Go CPU pprof PGO, explicit consumer proof, and negative format/identity checks\n'

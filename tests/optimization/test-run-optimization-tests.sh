#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)
DRIVER=${REPOSITORY_ROOT}/tests/run-optimization-tests.sh
if ((EUID == 0)); then
    FIXTURE=$(mktemp -d \
        /var/tmp/gentoo-optimization/optimization-driver-self-test.XXXXXXXX)
else
    FIXTURE=$(mktemp -d /tmp/gentoo-optimization-driver-self-test.XXXXXXXX)
fi
trap 'rm -rf -- "${FIXTURE}"' EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

[[ -f ${DRIVER} ]] || fail "driver is absent: ${DRIVER}"

bash -- "${DRIVER}" --help >"${FIXTURE}/help.txt"
grep -Fq -- '--mode smoke' "${FIXTURE}/help.txt" || fail 'help omits smoke mode'
grep -Fq -- '--mode portable-complete' "${FIXTURE}/help.txt" || \
    fail 'help omits portable-complete mode'
grep -Fq -- '--mode stress' "${FIXTURE}/help.txt" || fail 'help omits stress mode'
grep -Fq -- '--mode capabilities' "${FIXTURE}/help.txt" || fail 'help omits capability mode'
grep -Fq -- '--mode authoritative' "${FIXTURE}/help.txt" || \
    fail 'help omits authoritative mode'
grep -Fq -- '--mode quick' "${FIXTURE}/help.txt" || \
    fail 'help omits the deprecated quick alias'
grep -Fq -- '--capability NAME' "${FIXTURE}/help.txt" || fail 'help omits capability filter'
grep -Fq 'TEST_CASE_TIMEOUT_SECONDS=1800' "${FIXTURE}/help.txt" || \
    fail 'help omits the global per-case timeout'
grep -Fq 'TEST_CASE_KILL_AFTER_SECONDS=10' "${FIXTURE}/help.txt" || \
    fail 'help omits the per-case forced-kill grace period'
grep -Fq 'TEST_CASE_TIMEOUT_SECONDS_CLANG_IR' "${FIXTURE}/help.txt" || \
    fail 'help omits normalized per-capability deadline overrides'
grep -Fq 'GENTOO_OPT_AUTHORITATIVE=0|1' "${FIXTURE}/help.txt" || \
    fail 'help omits authoritative subtest accounting'

bash -- "${DRIVER}" --list >"${FIXTURE}/list.txt"
grep -Fq 'recovery-rollback-fixture' "${FIXTURE}/list.txt" || fail 'suite list omits rollback fixture'
grep -Fq 'Clang/libc++ and GCC/libstdc++' "${FIXTURE}/list.txt" || fail 'suite list omits ABI lanes'
grep -Fq 'package-env-duplicate-policy' "${FIXTURE}/list.txt" || \
    fail 'suite list omits package.env duplicate-policy validation'
grep -Fq 'package-env-portage-semantic' "${FIXTURE}/list.txt" || \
    fail 'suite list omits explicit live Portage semantic status'
grep -Fq 'portage-config-cleanup' "${FIXTURE}/list.txt" || \
    fail 'suite list omits reviewed Portage configuration cleanup'
grep -Fq 'framework-installer' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the hermetic framework installer transaction gate'
grep -Fq 'no-legacy-pgo' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the retired legacy PGO gate'
grep -Fq 'pgo-dispatcher' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the strict PGO dispatcher fixture'
grep -Fq 'portage-qa-hook-state' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the Portage QA hook state fixture'
grep -Fq 'portage-pre-strip-integration' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the real Portage pre-strip integration fixture'
grep -Fq 'portage-pgo-use-integration' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the real Portage PGO-use integration fixture'
grep -Fq 'portage-sample-pgo-integration' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the real Portage sample-PGO integration fixture'
grep -Fq 'portage-sample-pgo-live-policy-integration' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the live-policy Portage sample-PGO integration fixture'
grep -Fq 'resolved live sandbox/userpriv/PID/network/IPC' "${FIXTURE}/list.txt" || \
    fail 'suite list does not declare the normal live Portage policy lane'
grep -Fq 'root-only and opt-in with clang-sample' "${FIXTURE}/list.txt" || \
    fail 'suite list does not declare the sample-PGO perf workload opt-in'
grep -Fq 'bolt-command-policy' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the exact BOLT command-policy gate'
grep -Fq 'bolt-transaction-fixture' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the hermetic BOLT transaction fixture'
grep -Fq 'bolt-pre-strip-hooks' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the pre-strip BOLT hook fixture'
for capability in clang-ir clang-sample gcc rust go bolt; do
    grep -Eq "^[[:space:]]+${capability}([[:space:]]|$)" \
        "${FIXTURE}/list.txt" || fail "suite list omits ${capability}"
done
bash -- "${DRIVER}" --mode quick --list \
    >"${FIXTURE}/quick-list.txt" 2>"${FIXTURE}/quick-list.stderr"
grep -Fq -- 'WARNING: --mode quick is deprecated; using portable-complete' \
    "${FIXTURE}/quick-list.stderr" || \
    fail 'deprecated quick alias did not visibly normalize to portable-complete'

if GENTOO_OPT_AUTHORITATIVE=invalid bash -- "${DRIVER}" --mode smoke \
    >"${FIXTURE}/bad-authoritative.log" 2>&1; then
    fail 'invalid authoritative selector unexpectedly succeeded'
fi
grep -Fq 'GENTOO_OPT_AUTHORITATIVE must be exactly 0 or 1' \
    "${FIXTURE}/bad-authoritative.log" || \
    fail 'invalid authoritative selector lacks a visible diagnostic'

if bash -- "${DRIVER}" --mode unsupported >"${FIXTURE}/bad-mode.log" 2>&1; then
    fail 'unsupported mode unexpectedly succeeded'
fi
grep -Fq 'unknown mode: unsupported' "${FIXTURE}/bad-mode.log" || \
    fail 'unsupported mode lacks a visible diagnostic'

if bash -- "${DRIVER}" --capability unknown >"${FIXTURE}/bad-capability.log" 2>&1; then
    fail 'unknown capability unexpectedly succeeded'
fi
grep -Fq 'unknown capability: unknown' "${FIXTURE}/bad-capability.log" || \
    fail 'unknown capability lacks a visible diagnostic'

if TEST_CASE_TIMEOUT_SECONDS=0 bash -- "${DRIVER}" --mode quick \
    >"${FIXTURE}/bad-timeout.log" 2>&1; then
    fail 'zero per-case timeout unexpectedly succeeded'
fi
grep -Fq 'TEST_CASE_TIMEOUT_SECONDS must be a positive integer number of seconds' \
    "${FIXTURE}/bad-timeout.log" || fail 'invalid global timeout lacks a visible diagnostic'

if TEST_CASE_KILL_AFTER_SECONDS_BOLT=invalid bash -- "${DRIVER}" --mode quick \
    >"${FIXTURE}/bad-capability-kill-after.log" 2>&1; then
    fail 'invalid capability kill-after override unexpectedly succeeded'
fi
grep -Fq 'TEST_CASE_KILL_AFTER_SECONDS_BOLT must be a positive integer number of seconds' \
    "${FIXTURE}/bad-capability-kill-after.log" || \
    fail 'invalid capability kill-after override lacks a visible diagnostic'

if bash -- "${DRIVER}" --output-dir relative/path \
    >"${FIXTURE}/bad-output.log" 2>&1; then
    fail 'relative output directory unexpectedly succeeded'
fi
grep -Fq -- '--output-dir must be an absolute non-root path' \
    "${FIXTURE}/bad-output.log" || fail 'unsafe output path lacks a visible diagnostic'

UNSAFE_OUTPUT=${FIXTURE}/unsafe\ output
if bash -- "${DRIVER}" --output-dir "${UNSAFE_OUTPUT}" \
    >"${FIXTURE}/bad-output-characters.log" 2>&1; then
    fail 'output directory with unsafe characters unexpectedly succeeded'
fi
grep -Fq -- '--output-dir canonical path contains characters unsafe for capability workloads' \
    "${FIXTURE}/bad-output-characters.log" || \
    fail 'unsafe output characters lack a visible diagnostic'
[[ ! -e ${UNSAFE_OUTPUT} ]] || fail 'unsafe output path was created before rejection'

# Exercise capability preflight without recursively invoking this self-test or
# risking a real profiling workload.  The copied driver sees a deliberately
# tiny repository and PATH; its BOLT runner is a stub that must remain unused.
HERMETIC_ROOT=${FIXTURE}/hermetic-repository
HERMETIC_BIN=${FIXTURE}/hermetic-bin
HERMETIC_DRIVER=${HERMETIC_ROOT}/tests/run-optimization-tests.sh
HERMETIC_BOLT_RUNNER=${HERMETIC_ROOT}/optimization/fixtures/bolt/run.sh
HERMETIC_SAMPLE_RUNNER=${HERMETIC_ROOT}/tests/optimization/test-portage-sample-pgo-integration.sh
HERMETIC_OUTPUT=${FIXTURE}/hermetic-preflight-output
HERMETIC_RUNNER_MARKER=${FIXTURE}/bolt-runner-was-invoked
HERMETIC_SAMPLE_MARKER=${FIXTURE}/sample-runner-was-invoked
mkdir -p -- "${HERMETIC_ROOT}/bench" \
    "${HERMETIC_ROOT}/optimization/fixtures/bolt" \
    "${HERMETIC_ROOT}/scripts" "${HERMETIC_ROOT}/tests/optimization" \
    "${HERMETIC_BIN}"
cp -- "${DRIVER}" "${HERMETIC_DRIVER}"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    "printf '%s\\n' invoked >'${HERMETIC_RUNNER_MARKER}'" \
    'exit 97' >"${HERMETIC_BOLT_RUNNER}"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    "printf '%s\\n' invoked >'${HERMETIC_SAMPLE_MARKER}'" \
    'exit 96' >"${HERMETIC_SAMPLE_RUNNER}"
chmod 0755 -- "${HERMETIC_DRIVER}" "${HERMETIC_BOLT_RUNNER}" \
    "${HERMETIC_SAMPLE_RUNNER}"
for required_driver_tool in bash dirname env find mkdir realpath setsid sleep sort \
    stat tee timeout; do
    required_driver_path=$(command -v -- "${required_driver_tool}") || \
        fail "self-test prerequisite is unavailable: ${required_driver_tool}"
    ln -s -- "${required_driver_path}" \
        "${HERMETIC_BIN}/${required_driver_tool}"
done

PATH=${HERMETIC_BIN} bash -- "${HERMETIC_DRIVER}" \
    --mode capabilities --capability bolt --output-dir "${HERMETIC_OUTPUT}" \
    >"${FIXTURE}/hermetic-preflight.log" 2>&1 || {
    sed -n '1,240p' "${FIXTURE}/hermetic-preflight.log" >&2
    fail 'hermetic capability-preflight driver invocation failed'
}
[[ ! -e ${HERMETIC_RUNNER_MARKER} ]] || \
    fail 'BOLT runner executed despite a failed dependency preflight'
[[ ! -e ${HERMETIC_SAMPLE_MARKER} ]] || \
    fail 'sample Portage runner executed without selecting clang-sample'

BOLT_SKIP_ROWS=0
BOLT_SKIP_DETAIL=
while IFS=$'\t' read -r result_status result_name result_detail; do
    if [[ ${result_status} == SKIP && ${result_name} == capability:bolt ]]; then
        ((BOLT_SKIP_ROWS += 1))
        BOLT_SKIP_DETAIL=${result_detail}
    fi
done <"${HERMETIC_OUTPUT}/results.tsv"
[[ ${BOLT_SKIP_ROWS} -eq 1 ]] || \
    fail "expected one explicit BOLT SKIP row, found ${BOLT_SKIP_ROWS}"
[[ ${BOLT_SKIP_DETAIL} == 'missing required command(s): '* ]] || \
    fail "BOLT SKIP lacks a dependency-preflight reason: ${BOLT_SKIP_DETAIL}"
SAMPLE_DIAGNOSTIC_SKIP_ROWS=0
SAMPLE_LIVE_SKIP_ROWS=0
SAMPLE_DIAGNOSTIC_SKIP_DETAIL=
SAMPLE_LIVE_SKIP_DETAIL=
while IFS=$'\t' read -r result_status result_name result_detail; do
    [[ ${result_status} == SKIP ]] || continue
    case ${result_name} in
        portage-sample-pgo-integration)
            ((SAMPLE_DIAGNOSTIC_SKIP_ROWS += 1))
            SAMPLE_DIAGNOSTIC_SKIP_DETAIL=${result_detail}
            ;;
        portage-sample-pgo-live-policy-integration)
            ((SAMPLE_LIVE_SKIP_ROWS += 1))
            SAMPLE_LIVE_SKIP_DETAIL=${result_detail}
            ;;
    esac
done <"${HERMETIC_OUTPUT}/results.tsv"
[[ ${SAMPLE_DIAGNOSTIC_SKIP_ROWS} -eq 1 ]] || \
    fail "expected one diagnostic sample Portage opt-in SKIP row, found ${SAMPLE_DIAGNOSTIC_SKIP_ROWS}"
[[ ${SAMPLE_LIVE_SKIP_ROWS} -eq 1 ]] || \
    fail "expected one live-policy sample Portage opt-in SKIP row, found ${SAMPLE_LIVE_SKIP_ROWS}"
[[ ${SAMPLE_DIAGNOSTIC_SKIP_DETAIL} == \
    'requires the explicitly selected clang-sample capability because it runs perf and a training workload' ]] || \
    fail "diagnostic sample Portage SKIP lacks the exact opt-in reason: ${SAMPLE_DIAGNOSTIC_SKIP_DETAIL}"
[[ ${SAMPLE_LIVE_SKIP_DETAIL} == \
    'requires the explicitly selected clang-sample capability because it runs perf and a training workload' ]] || \
    fail "live-policy sample Portage SKIP lacks the exact opt-in reason: ${SAMPLE_LIVE_SKIP_DETAIL}"
MISSING_COMMANDS=${BOLT_SKIP_DETAIL#missing required command(s): }
for expected_missing_command in awk chmod clang cmp cp file getcap getfattr \
    grep head lddtree llvm-bolt merge-fdata nm objcopy perf perf2bolt readelf \
    readlink rm sed setfattr sha256sum strip tail tr mv xargs; do
    case ,${MISSING_COMMANDS}, in
        *,${expected_missing_command},*) ;;
        *) fail "BOLT dependency preflight did not report missing ${expected_missing_command}" ;;
    esac
done
grep -Fxq 'fail=0' "${HERMETIC_OUTPUT}/summary.txt" || \
    fail 'hermetic preflight SKIP was incorrectly counted as a failure'
grep -Fxq 'exit_status=0' "${HERMETIC_OUTPUT}/summary.txt" || \
    fail 'hermetic preflight SKIP produced a nonzero driver status'
grep -Fxq 'mode=capabilities' "${HERMETIC_OUTPUT}/summary.txt" || \
    fail 'hermetic capability-preflight run lost its exact mode'

AUTHORITATIVE_OUTPUT=${FIXTURE}/hermetic-authoritative-output
set +e
PATH=${HERMETIC_BIN} bash -- "${HERMETIC_DRIVER}" \
    --mode authoritative --output-dir "${AUTHORITATIVE_OUTPUT}" \
    >"${FIXTURE}/hermetic-authoritative.log" 2>&1
AUTHORITATIVE_STATUS=$?
set -e
[[ ${AUTHORITATIVE_STATUS} -eq 1 ]] || \
    fail "incomplete authoritative gate returned ${AUTHORITATIVE_STATUS}, expected 1"
grep -Fxq 'mode=authoritative' "${AUTHORITATIVE_OUTPUT}/summary.txt" || \
    fail 'authoritative summary lost its exact mode'
grep -Fxq 'authoritative=1' "${AUTHORITATIVE_OUTPUT}/summary.txt" || \
    fail 'authoritative mode did not enable fail-closed subtest accounting'
for capability in clang-ir clang-sample gcc rust go bolt; do
    grep -Eq $'^SKIP\tcapability:'"${capability}"$'(:|\t)' \
        "${AUTHORITATIVE_OUTPUT}/results.tsv" || \
        fail "authoritative mode did not select/preflight ${capability}"
    if grep -Fq $'\tcapability:'"${capability}"$'\tnot selected' \
        "${AUTHORITATIVE_OUTPUT}/results.tsv"; then
        fail "authoritative mode left ${capability} unselected"
    fi
done

# Exercise the real per-case deadline around a capability whose runner and
# preflight are entirely fake.  Both the runner and its child ignore TERM, so
# the configured forced-kill boundary must remove the complete process group.
TIMEOUT_ROOT=${FIXTURE}/timeout-repository
TIMEOUT_BIN_ROOT=${FIXTURE}/timeout-bin
TIMEOUT_DRIVER=${TIMEOUT_ROOT}/tests/run-optimization-tests.sh
TIMEOUT_RUNNER=${TIMEOUT_ROOT}/optimization/fixtures/pgo/go/run.sh
TIMEOUT_FAKE_GO=${TIMEOUT_BIN_ROOT}/go
TIMEOUT_OUTPUT=${FIXTURE}/timeout-output
TIMEOUT_PARENT_PID_FILE=${FIXTURE}/timeout-parent.pid
TIMEOUT_CHILD_PID_FILE=${FIXTURE}/timeout-child.pid
mkdir -p -- "${TIMEOUT_ROOT}/bench" \
    "${TIMEOUT_ROOT}/optimization/fixtures/pgo/go" \
    "${TIMEOUT_ROOT}/scripts" "${TIMEOUT_ROOT}/tests" "${TIMEOUT_BIN_ROOT}"
cp -- "${DRIVER}" "${TIMEOUT_DRIVER}"
# These single-quoted expressions are intentionally emitted into the fake
# runner and expand only when that generated helper runs.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -u' \
    '(trap "" TERM; while :; do sleep 1; done) &' \
    'child=$!' \
    "printf '%s\\n' \"\${child}\" >'${TIMEOUT_CHILD_PID_FILE}'" \
    "printf '%s\\n' \"\${BASHPID}\" >'${TIMEOUT_PARENT_PID_FILE}'" \
    'trap "" TERM' \
    'while :; do sleep 1; done' \
    >"${TIMEOUT_RUNNER}"
# The positional parameters intentionally expand in the generated fake Go tool.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'case ${1:-}:${2:-} in' \
    '    env:GOOS) printf "linux\\n" ;;' \
    '    env:GOARCH) printf "amd64\\n" ;;' \
    '    help:build) printf "usage: go build [-pgo file]\\n" ;;' \
    '    *) exit 91 ;;' \
    'esac' \
    >"${TIMEOUT_FAKE_GO}"
chmod 0755 -- "${TIMEOUT_DRIVER}" "${TIMEOUT_RUNNER}" "${TIMEOUT_FAKE_GO}"
for required_timeout_tool in bash dirname env find mkdir readelf realpath rg sed \
    mv setsid sha256sum sleep sort stat tail tee timeout; do
    [[ ${required_timeout_tool} == go ]] && continue
    required_timeout_path=$(command -v -- "${required_timeout_tool}") || \
        fail "self-test prerequisite is unavailable: ${required_timeout_tool}"
    ln -s -- "${required_timeout_path}" \
        "${TIMEOUT_BIN_ROOT}/${required_timeout_tool}"
done

set +e
PATH=${TIMEOUT_BIN_ROOT} \
TEST_CASE_TIMEOUT_SECONDS=30 \
TEST_CASE_KILL_AFTER_SECONDS=30 \
TEST_CASE_TIMEOUT_SECONDS_GO=1 \
TEST_CASE_KILL_AFTER_SECONDS_GO=1 \
    bash -- "${TIMEOUT_DRIVER}" --mode smoke --capability go \
    --output-dir "${TIMEOUT_OUTPUT}" \
    >"${FIXTURE}/timeout-driver.log" 2>&1
TIMEOUT_DRIVER_STATUS=$?
set -e
[[ ${TIMEOUT_DRIVER_STATUS} -eq 1 ]] || \
    fail "timed-out capability produced driver status ${TIMEOUT_DRIVER_STATUS}, expected 1"

[[ $(<"${TIMEOUT_OUTPUT}/results.tsv") == $'status\ttest\tdetail'* ]] || \
    fail 'results.tsv no longer begins with the compatible three-column header'
TIMEOUT_RESULT_ROWS=0
TIMEOUT_RESULT_DETAIL=
while IFS=$'\t' read -r result_status result_name result_detail extra_field; do
    if [[ ${result_name} == capability:go ]]; then
        ((TIMEOUT_RESULT_ROWS += 1))
        [[ ${result_status} == FAIL ]] || \
            fail "timed-out capability status changed from compatible FAIL: ${result_status}"
        [[ -z ${extra_field} ]] || fail 'timed-out result added an incompatible fourth TSV field'
        TIMEOUT_RESULT_DETAIL=${result_detail}
    fi
done <"${TIMEOUT_OUTPUT}/results.tsv"
[[ ${TIMEOUT_RESULT_ROWS} -eq 1 ]] || \
    fail "expected one timed-out capability result, found ${TIMEOUT_RESULT_ROWS}"
case ${TIMEOUT_RESULT_DETAIL} in
    *'exit_status=124 '*|*'exit_status=137 '*) ;;
    *) fail "timed-out capability lacks exit 124/137: ${TIMEOUT_RESULT_DETAIL}" ;;
esac
[[ ${TIMEOUT_RESULT_DETAIL} == *'timeout_seconds=1 '* ]] || \
    fail "Go timeout override was not recorded: ${TIMEOUT_RESULT_DETAIL}"
[[ ${TIMEOUT_RESULT_DETAIL} == *'kill_after_seconds=1 '* ]] || \
    fail "Go forced-kill override was not recorded: ${TIMEOUT_RESULT_DETAIL}"
[[ ${TIMEOUT_RESULT_DETAIL} == *'deadline=exceeded '* ]] || \
    fail "timed-out capability is not marked deadline=exceeded: ${TIMEOUT_RESULT_DETAIL}"
grep -Fxq 'fail=1' "${TIMEOUT_OUTPUT}/summary.txt" || \
    fail 'timed-out capability was not counted as one failure'
grep -Fxq 'exit_status=1' "${TIMEOUT_OUTPUT}/summary.txt" || \
    fail 'timed-out capability did not produce a failing driver summary'

assert_process_gone() {
    local label=$1 pid=$2 attempt
    for ((attempt = 0; attempt < 50; attempt += 1)); do
        kill -0 "${pid}" 2>/dev/null || return 0
        sleep 0.1
    done
    kill -KILL "${pid}" 2>/dev/null || true
    fail "${label} survived the per-case process-group deadline: pid ${pid}"
}

[[ -s ${TIMEOUT_PARENT_PID_FILE} ]] || fail 'timed-out runner did not record its PID'
[[ -s ${TIMEOUT_CHILD_PID_FILE} ]] || fail 'timed-out runner did not record its child PID'
assert_process_gone 'capability runner' "$(<"${TIMEOUT_PARENT_PID_FILE}")"
assert_process_gone 'capability child' "$(<"${TIMEOUT_CHILD_PID_FILE}")"

# Exit 77 is the conventional explicit fixture skip.  It must remain a SKIP
# with its reason and must not make the aggregate driver fail.
SKIP_OUTPUT=${FIXTURE}/explicit-fixture-skip-output
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "SKIP: deliberate hermetic capability limitation\\n"' \
    'exit 77' >"${TIMEOUT_RUNNER}"
chmod 0755 -- "${TIMEOUT_RUNNER}"
PATH=${TIMEOUT_BIN_ROOT} \
    bash -- "${TIMEOUT_DRIVER}" --mode smoke --capability go \
    --output-dir "${SKIP_OUTPUT}" \
    >"${FIXTURE}/explicit-fixture-skip-driver.log" 2>&1 || \
    fail 'reason-bearing fixture exit 77 made the driver fail'
SKIP_RESULT_ROWS=0
SKIP_RESULT_DETAIL=
while IFS=$'\t' read -r result_status result_name result_detail; do
    if [[ ${result_name} == capability:go ]]; then
        ((SKIP_RESULT_ROWS += 1))
        [[ ${result_status} == SKIP ]] || \
            fail "fixture exit 77 was not recorded as SKIP: ${result_status}"
        SKIP_RESULT_DETAIL=${result_detail}
    fi
done <"${SKIP_OUTPUT}/results.tsv"
[[ ${SKIP_RESULT_ROWS} -eq 1 ]] || \
    fail "expected one explicit fixture SKIP row, found ${SKIP_RESULT_ROWS}"
[[ ${SKIP_RESULT_DETAIL} == 'deliberate hermetic capability limitation '* && \
    ${SKIP_RESULT_DETAIL} == *'exit_status=77 '* ]] || \
    fail "fixture exit 77 lost its reason/status: ${SKIP_RESULT_DETAIL}"
grep -Fxq 'fail=0' "${SKIP_OUTPUT}/summary.txt" || \
    fail 'fixture exit 77 was counted as a failure'

run_fragment_contract_failure() {
    local label=$1 body=$2 expected_detail=$3
    local output=${FIXTURE}/fragment-${label}-output
    printf '%s\n' '#!/usr/bin/env bash' "${body}" 'exit 0' >"${TIMEOUT_RUNNER}"
    chmod 0755 -- "${TIMEOUT_RUNNER}"
    set +e
    PATH=${TIMEOUT_BIN_ROOT} \
        bash -- "${TIMEOUT_DRIVER}" --mode smoke --capability go \
        --output-dir "${output}" \
        >"${FIXTURE}/fragment-${label}-driver.log" 2>&1
    local driver_status=$?
    set -e
    [[ ${driver_status} -eq 1 ]] || \
        fail "${label} subtest-fragment violation returned ${driver_status}, expected 1"
    grep -Fq $'FAIL\tcapability:go\t' "${output}/results.tsv" || \
        fail "${label} subtest-fragment violation remained a top-level PASS"
    grep -Fq "${expected_detail}" "${output}/subtests.tsv" || \
        fail "${label} subtest-fragment violation lacks its structured diagnostic"
}

# These fragments are emitted into generated runners and expand only when
# those runners execute under the driver.
# shellcheck disable=SC2016
run_fragment_contract_failure truncation \
    ': >"${GENTOO_OPT_SUBTEST_RESULTS}"' \
    'fixture truncated its structured subtest fragment to zero bytes'
# shellcheck disable=SC2016
run_fragment_contract_failure replacement \
    'replacement=${GENTOO_OPT_SUBTEST_RESULTS}.replacement; printf "gentoo-optimization-subtest-fragment-v1\\n" >"${replacement}"; mv -f -- "${replacement}" "${GENTOO_OPT_SUBTEST_RESULTS}"' \
    'fixture replaced or removed its private structured subtest fragment'
# shellcheck disable=SC2016
run_fragment_contract_failure duplicate \
    'printf "PASS\\trequired\\tduplicate-name\\tfirst\\nPASS\\trequired\\tduplicate-name\\tsecond\\n" >>"${GENTOO_OPT_SUBTEST_RESULTS}"' \
    'fixture emitted a duplicate structured subtest name'
# shellcheck disable=SC2016
run_fragment_contract_failure forged-completion \
    'printf "PASS\\trequired\\tdriver.case-completion\\tforged\\n" >>"${GENTOO_OPT_SUBTEST_RESULTS}"' \
    'fixture emitted a malformed structured subtest row'

DIAGNOSTIC_SKIP_OUTPUT=${FIXTURE}/diagnostic-subtest-output
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "DIAGNOSTIC-SKIP-SUBTEST: optional observation unavailable\\n"' \
    'exit 0' >"${TIMEOUT_RUNNER}"
chmod 0755 -- "${TIMEOUT_RUNNER}"
PATH=${TIMEOUT_BIN_ROOT} \
    bash -- "${TIMEOUT_DRIVER}" --mode smoke --capability go \
    --output-dir "${DIAGNOSTIC_SKIP_OUTPUT}" \
    >"${FIXTURE}/diagnostic-subtest-driver.log" 2>&1 || \
    fail 'explicit diagnostic subtest skip made the non-authoritative driver fail'
grep -Fq $'PASS\tcapability:go\t' "${DIAGNOSTIC_SKIP_OUTPUT}/results.tsv" || \
    fail 'explicit diagnostic subtest skip changed the enclosing case from PASS'
grep -Fq $'SKIP\tdiagnostic\tcapability:go\t' \
    "${DIAGNOSTIC_SKIP_OUTPUT}/subtests.tsv" || \
    fail 'explicit diagnostic subtest skip was not classified as diagnostic'
grep -Fxq 'mandatory_internal_skip=0' \
    "${DIAGNOSTIC_SKIP_OUTPUT}/summary.txt" || \
    fail 'diagnostic subtest skip polluted the mandatory internal skip count'

HOST_SKIP_OUTPUT=${FIXTURE}/unstructured-host-skip-output
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "HOST-SKIP: unstructured host branch unavailable\n"' \
    'exit 0' >"${TIMEOUT_RUNNER}"
chmod 0755 -- "${TIMEOUT_RUNNER}"
set +e
PATH=${TIMEOUT_BIN_ROOT} GENTOO_OPT_AUTHORITATIVE=1 \
    bash -- "${TIMEOUT_DRIVER}" --mode smoke --capability go \
    --output-dir "${HOST_SKIP_OUTPUT}" \
    >"${FIXTURE}/unstructured-host-skip-driver.log" 2>&1
HOST_SKIP_STATUS=$?
set -e
[[ ${HOST_SKIP_STATUS} -eq 1 ]] || \
    fail "unstructured HOST-SKIP returned ${HOST_SKIP_STATUS}, expected 1"
grep -Fq $'SKIP\trequired\tcapability:go\t' \
    "${HOST_SKIP_OUTPUT}/subtests.tsv" || \
    fail 'unstructured HOST-SKIP was not surfaced as a required skip'
grep -Fxq 'mandatory_internal_skip=1' "${HOST_SKIP_OUTPUT}/summary.txt" || \
    fail 'unstructured HOST-SKIP did not increment mandatory skip accounting'

# Prove that the one-shot py_compile suite gets an isolated cache while
# subprocess-heavy unittest discovery clears even an inherited cache prefix.
PYTHON_ROOT=${FIXTURE}/python-repository
PYTHON_BIN_ROOT=${FIXTURE}/python-bin
PYTHON_DRIVER=${PYTHON_ROOT}/tests/run-optimization-tests.sh
PYTHON_UNITTEST_RUNNER=${PYTHON_ROOT}/scripts/optimization/verify/run-unittest-suite.py
PYTHON_TEST_DIR=${PYTHON_ROOT}/tests/unit
PYTHON_OUTPUT=${FIXTURE}/python-output
PYTHON_ENV_MARKER=${FIXTURE}/python-unittest-environment.txt
mkdir -p -- "${PYTHON_ROOT}/bench" "${PYTHON_ROOT}/optimization" \
    "${PYTHON_ROOT}/scripts/optimization/verify" "${PYTHON_TEST_DIR}" \
    "${PYTHON_BIN_ROOT}"
cp -- "${DRIVER}" "${PYTHON_DRIVER}"
cp -- "${REPOSITORY_ROOT}/scripts/optimization/verify/run-unittest-suite.py" \
    "${PYTHON_UNITTEST_RUNNER}"
printf '%s\n' \
    'import os' \
    'import subprocess' \
    'import sys' \
    'from pathlib import Path' \
    'import unittest' \
    '' \
    'class DriverPythonEnvironmentTest(unittest.TestCase):' \
    '    def test_unittest_environment(self):' \
    '        self.assertEqual(os.environ.get("PYTHONDONTWRITEBYTECODE"), "1")' \
    '        self.assertNotIn("PYTHONPYCACHEPREFIX", os.environ)' \
    '        for name in ("GENTOO_OPT_AUTHORITATIVE", "GENTOO_OPT_SUBTEST_RESULTS", "GENTOO_OPT_TEST_CASE"):' \
    '            self.assertNotIn(name, os.environ)' \
    '        subprocess.run([sys.executable, "-c", "import os; assert not any(name in os.environ for name in (\"GENTOO_OPT_AUTHORITATIVE\", \"GENTOO_OPT_SUBTEST_RESULTS\", \"GENTOO_OPT_TEST_CASE\"))"], check=True)' \
    "        Path('${PYTHON_ENV_MARKER}').write_text('dontwrite=1\\npycacheprefix=unset\\n', encoding='utf-8')" \
    '' \
    '    @unittest.skip("deliberate required unittest skip")' \
    '    def test_required_internal_skip(self):' \
    '        self.fail("skip decorator did not skip")' \
    >"${PYTHON_TEST_DIR}/test_environment.py"
for required_python_tool in bash dirname env find mkdir realpath setsid sleep \
    sort stat tail tee timeout; do
    required_python_path=$(command -v -- "${required_python_tool}") || \
        fail "self-test prerequisite is unavailable: ${required_python_tool}"
    ln -s -- "${required_python_path}" \
        "${PYTHON_BIN_ROOT}/${required_python_tool}"
done
python3_path=$(command -v -- python3) || \
    fail 'self-test prerequisite is unavailable: python3'
ln -s -- "${python3_path}" "${PYTHON_BIN_ROOT}/python3"
PYTHON_DOTTED_FRAGMENT=${FIXTURE}/python-dotted-fragment.tsv
printf 'gentoo-optimization-subtest-fragment-v1\n' >"${PYTHON_DOTTED_FRAGMENT}"
GENTOO_OPT_AUTHORITATIVE=0 \
GENTOO_OPT_SUBTEST_RESULTS=${PYTHON_DOTTED_FRAGMENT} \
GENTOO_OPT_TEST_CASE=wrapper-dotted-import \
PYTHONDONTWRITEBYTECODE=1 \
    "${python3_path}" "${PYTHON_UNITTEST_RUNNER}" -v \
    tests.unit.test_environment.DriverPythonEnvironmentTest.test_unittest_environment \
    >"${FIXTURE}/python-dotted-wrapper.log" 2>&1 || {
    sed -n '1,160p' "${FIXTURE}/python-dotted-wrapper.log" >&2
    fail 'structured unittest runner could not import a real dotted test identity'
}
grep -Fq $'PASS\trequired\tpython.tests.unit.test_environment.DriverPythonEnvironmentTest.test_unittest_environment\t' \
    "${PYTHON_DOTTED_FRAGMENT}" || \
    fail 'dotted unittest invocation did not emit its exact structured identity'
PYTHON_EMPTY_TEST_DIR=${PYTHON_ROOT}/tests/empty
PYTHON_EMPTY_FRAGMENT=${FIXTURE}/python-empty-fragment.tsv
mkdir -p -- "${PYTHON_EMPTY_TEST_DIR}"
printf 'gentoo-optimization-subtest-fragment-v1\n' >"${PYTHON_EMPTY_FRAGMENT}"
set +e
GENTOO_OPT_AUTHORITATIVE=0 \
GENTOO_OPT_SUBTEST_RESULTS=${PYTHON_EMPTY_FRAGMENT} \
GENTOO_OPT_TEST_CASE=wrapper-zero-discovery \
    "${python3_path}" "${PYTHON_UNITTEST_RUNNER}" discover \
    -s "${PYTHON_EMPTY_TEST_DIR}" -p 'test_*.py' -v \
    >"${FIXTURE}/python-empty-wrapper.log" 2>&1
PYTHON_EMPTY_STATUS=$?
set -e
[[ ${PYTHON_EMPTY_STATUS} -eq 2 ]] || \
    fail "zero-test discovery returned ${PYTHON_EMPTY_STATUS}, expected 2"
grep -Fq 'ERROR: unittest discovery executed zero tests' \
    "${FIXTURE}/python-empty-wrapper.log" || \
    fail 'zero-test discovery failure omitted its exact diagnostic'
PATH=${PYTHON_BIN_ROOT} \
PYTHONPYCACHEPREFIX=/inherited-prefix-that-must-not-reach-unittest \
    bash -- "${PYTHON_DRIVER}" --mode stress --output-dir "${PYTHON_OUTPUT}" \
    >"${FIXTURE}/python-driver.log" 2>&1 || {
    sed -n '1,240p' "${FIXTURE}/python-driver.log" >&2
    fail 'hermetic Python-environment driver invocation failed'
}
grep -Fxq 'dontwrite=1' "${PYTHON_ENV_MARKER}" || \
    fail 'unittest did not receive PYTHONDONTWRITEBYTECODE=1'
grep -Fxq 'pycacheprefix=unset' "${PYTHON_ENV_MARKER}" || \
    fail 'unittest inherited PYTHONPYCACHEPREFIX'
find "${PYTHON_OUTPUT}/python-cache/compile" -type f -name '*.pyc' -print -quit |
    grep -q . || fail 'py_compile did not write to its isolated cache prefix'
[[ ! -e ${PYTHON_OUTPUT}/python-cache/unittest ]] || \
    fail 'the driver recreated the removed unittest cache prefix'
grep -Fxq 'fail=0' "${PYTHON_OUTPUT}/summary.txt" || \
    fail 'Python-environment self-test produced a driver failure'
grep -Fxq 'mandatory_internal_skip=1' "${PYTHON_OUTPUT}/summary.txt" || \
    fail 'Python unittest skip was not surfaced as one mandatory internal skip'
PYTHON_SKIP_ROWS=0
while IFS=$'\t' read -r subtest_status subtest_requirement subtest_test \
    subtest_name subtest_detail; do
    if [[ ${subtest_status}:${subtest_requirement} == SKIP:required &&
          ${subtest_test} == python-unit-tests:tests/unit &&
          ${subtest_name} == python.*test_required_internal_skip ]]; then
        ((PYTHON_SKIP_ROWS += 1))
        [[ ${subtest_detail} == 'deliberate required unittest skip' ]] || \
            fail "structured unittest skip lost its reason: ${subtest_detail}"
    fi
done <"${PYTHON_OUTPUT}/subtests.tsv"
[[ ${PYTHON_SKIP_ROWS} -eq 1 ]] || \
    fail "expected one explicit structured unittest skip row, found ${PYTHON_SKIP_ROWS}"

PYTHON_AUTHORITATIVE_OUTPUT=${FIXTURE}/python-authoritative-output
set +e
PATH=${PYTHON_BIN_ROOT} \
GENTOO_OPT_AUTHORITATIVE=1 \
    bash -- "${PYTHON_DRIVER}" --mode stress \
    --output-dir "${PYTHON_AUTHORITATIVE_OUTPUT}" \
    >"${FIXTURE}/python-authoritative-driver.log" 2>&1
PYTHON_AUTHORITATIVE_STATUS=$?
set -e
[[ ${PYTHON_AUTHORITATIVE_STATUS} -eq 1 ]] || \
    fail "authoritative unittest skip produced status ${PYTHON_AUTHORITATIVE_STATUS}, expected 1"
grep -Fq $'FAIL\tpython-unit-tests:tests/unit\t' \
    "${PYTHON_AUTHORITATIVE_OUTPUT}/results.tsv" || \
    fail 'authoritative unittest skip remained hidden behind top-level PASS'
PROFILE_STRESS_SKIP_ROWS=0
PROFILE_STRESS_SKIP_DETAIL=
while IFS=$'\t' read -r result_status result_name result_detail; do
    if [[ ${result_status} == SKIP &&
          ${result_name} == production-profile-lock-crash-stress ]]; then
        ((PROFILE_STRESS_SKIP_ROWS += 1))
        PROFILE_STRESS_SKIP_DETAIL=${result_detail}
    fi
done <"${PYTHON_OUTPUT}/results.tsv"
[[ ${PROFILE_STRESS_SKIP_ROWS} -eq 1 ]] || \
    fail "expected one profile-lock stress SKIP row, found ${PROFILE_STRESS_SKIP_ROWS}"
[[ ${PROFILE_STRESS_SKIP_DETAIL} == \
    'production profile-lock transaction test module is unavailable' ]] || \
    fail "profile-lock stress SKIP lacks its exact module preflight reason: ${PROFILE_STRESS_SKIP_DETAIL}"

# Exercise both reason-bearing recovery ABI preflight failures while keeping
# the rollback fixture itself hermetic and provably unexecuted.
RECOVERY_ROOT=${FIXTURE}/recovery-repository
RECOVERY_BIN=${FIXTURE}/recovery-bin
RECOVERY_DRIVER=${RECOVERY_ROOT}/tests/run-optimization-tests.sh
RECOVERY_FIXTURE=${RECOVERY_ROOT}/optimization/fixtures/recovery/test-rollback.sh
RECOVERY_MARKER=${FIXTURE}/rollback-fixture-was-invoked
FAKE_COMPILER=${FIXTURE}/fake-cxx-compiler
mkdir -p -- "${RECOVERY_ROOT}/bench" \
    "${RECOVERY_ROOT}/optimization/fixtures/recovery" \
    "${RECOVERY_ROOT}/scripts" "${RECOVERY_ROOT}/tests" "${RECOVERY_BIN}"
cp -- "${DRIVER}" "${RECOVERY_DRIVER}"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    "printf '%s\\n' invoked >'${RECOVERY_MARKER}'" \
    'exit 97' >"${RECOVERY_FIXTURE}"
# These single-quoted expressions are intentionally emitted into the fake
# compiler and must expand only when that generated helper runs.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -u' \
    'compiler=${0##*/}' \
    'case ${compiler} in' \
    '    clang++) result=${FAKE_CLANGXX_RESULT:-success}; log=${FAKE_CLANGXX_LOG:?} ;;' \
    '    g++) result=${FAKE_GXX_RESULT:-success}; log=${FAKE_GXX_LOG:?} ;;' \
    '    *) exit 99 ;;' \
    'esac' \
    'printf "compiler=%s" "${compiler}" >"${log}"' \
    'output=' \
    'while (($#)); do' \
    '    printf " <%s>" "$1" >>"${log}"' \
    '    if [[ $1 == -o && $# -ge 2 ]]; then output=$2; shift 2; else shift; fi' \
    'done' \
    'printf "\\n" >>"${log}"' \
    '[[ ${result} == success ]] || exit 72' \
    '[[ -n ${output} ]] || exit 98' \
    "printf '%s\\n' '#!/usr/bin/env bash' 'printf \"gentoo-recovery-cxx-abi-probe\\\\n\"' >\"\${output}\"" \
    'chmod 0755 -- "${output}"' \
    >"${FAKE_COMPILER}"
chmod 0755 -- "${RECOVERY_DRIVER}" "${RECOVERY_FIXTURE}" "${FAKE_COMPILER}"
ln -s -- "${FAKE_COMPILER}" "${RECOVERY_BIN}/clang++"
ln -s -- "${FAKE_COMPILER}" "${RECOVERY_BIN}/g++"
for required_recovery_tool in bash chmod dirname env find flock iconv md5sum \
    mkdir od readelf realpath rm setsid sha256sum sleep sort stat tail tee timeout; do
    required_recovery_path=$(command -v -- "${required_recovery_tool}") || \
        fail "self-test prerequisite is unavailable: ${required_recovery_tool}"
    ln -s -- "${required_recovery_path}" \
        "${RECOVERY_BIN}/${required_recovery_tool}"
done

assert_recovery_preflight_skip() {
    local output=$1 expected_reason=$2
    local rows=0 detail=
    while IFS=$'\t' read -r result_status result_name result_detail; do
        if [[ ${result_status} == SKIP && ${result_name} == recovery-rollback-fixture ]]; then
            ((rows += 1))
            detail=${result_detail}
        fi
    done <"${output}/results.tsv"
    [[ ${rows} -eq 1 ]] || \
        fail "expected one recovery preflight SKIP row, found ${rows}"
    [[ ${detail} == "${expected_reason}"* ]] || \
        fail "recovery SKIP lacks the expected reason '${expected_reason}': ${detail}"
    [[ ${detail} == *'; the C++ ABI lane fixture was not run' ]] || \
        fail "recovery SKIP does not say that the ABI fixture was withheld: ${detail}"
    grep -Fxq 'fail=0' "${output}/summary.txt" || \
        fail 'recovery preflight SKIP was incorrectly counted as a failure'
}

CLANG_FAIL_OUTPUT=${FIXTURE}/recovery-clang-fail-output
PATH=${RECOVERY_BIN} \
FAKE_CLANGXX_RESULT=fail FAKE_GXX_RESULT=success \
FAKE_CLANGXX_LOG=${FIXTURE}/fake-clang-fail.log \
FAKE_GXX_LOG=${FIXTURE}/fake-gxx-unused.log \
    bash -- "${RECOVERY_DRIVER}" --mode stress --output-dir "${CLANG_FAIL_OUTPUT}" \
    >"${FIXTURE}/recovery-clang-fail-driver.log" 2>&1 || \
    fail 'Clang/libc++ preflight-failure driver invocation failed'
assert_recovery_preflight_skip "${CLANG_FAIL_OUTPUT}" \
    'Clang/libc++ ABI probe compilation failed'
grep -Fq '<-stdlib=libc++>' "${FIXTURE}/fake-clang-fail.log" || \
    fail 'Clang/libc++ preflight did not request libc++'
[[ ! -e ${FIXTURE}/fake-gxx-unused.log ]] || \
    fail 'GCC probe ran after the Clang/libc++ probe had failed'

GCC_FAIL_OUTPUT=${FIXTURE}/recovery-gcc-fail-output
PATH=${RECOVERY_BIN} \
FAKE_CLANGXX_RESULT=success FAKE_GXX_RESULT=fail \
FAKE_CLANGXX_LOG=${FIXTURE}/fake-clang-success.log \
FAKE_GXX_LOG=${FIXTURE}/fake-gxx-fail.log \
    bash -- "${RECOVERY_DRIVER}" --mode stress --output-dir "${GCC_FAIL_OUTPUT}" \
    >"${FIXTURE}/recovery-gcc-fail-driver.log" 2>&1 || \
    fail 'GCC/libstdc++ preflight-failure driver invocation failed'
assert_recovery_preflight_skip "${GCC_FAIL_OUTPUT}" \
    'GCC/libstdc++ ABI probe compilation failed'
grep -Fq '<-O2>' "${FIXTURE}/fake-gxx-fail.log" || \
    fail 'GCC/libstdc++ preflight did not invoke g++ after the Clang probe passed'
[[ ! -e ${RECOVERY_MARKER} ]] || \
    fail 'rollback fixture executed despite a failed ABI capability probe'

printf 'PASS: optimization test-driver CLI self-test\n'

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
grep -Fq -- '--mode quick' "${FIXTURE}/help.txt" || fail 'help omits quick mode'
grep -Fq -- '--mode capabilities' "${FIXTURE}/help.txt" || fail 'help omits capability mode'
grep -Fq -- '--capability NAME' "${FIXTURE}/help.txt" || fail 'help omits capability filter'
grep -Fq 'TEST_CASE_TIMEOUT_SECONDS=1800' "${FIXTURE}/help.txt" || \
    fail 'help omits the global per-case timeout'
grep -Fq 'TEST_CASE_KILL_AFTER_SECONDS=10' "${FIXTURE}/help.txt" || \
    fail 'help omits the per-case forced-kill grace period'
grep -Fq 'TEST_CASE_TIMEOUT_SECONDS_CLANG_IR' "${FIXTURE}/help.txt" || \
    fail 'help omits normalized per-capability deadline overrides'

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
    --mode quick --capability bolt --output-dir "${HERMETIC_OUTPUT}" \
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
SAMPLE_SKIP_ROWS=0
SAMPLE_SKIP_DETAIL=
while IFS=$'\t' read -r result_status result_name result_detail; do
    if [[ ${result_status} == SKIP && \
        ${result_name} == portage-sample-pgo-integration ]]; then
        ((SAMPLE_SKIP_ROWS += 1))
        SAMPLE_SKIP_DETAIL=${result_detail}
    fi
done <"${HERMETIC_OUTPUT}/results.tsv"
[[ ${SAMPLE_SKIP_ROWS} -eq 1 ]] || \
    fail "expected one sample Portage opt-in SKIP row, found ${SAMPLE_SKIP_ROWS}"
[[ ${SAMPLE_SKIP_DETAIL} == \
    'requires the explicitly selected clang-sample capability because it runs perf and a training workload' ]] || \
    fail "sample Portage SKIP lacks the exact opt-in reason: ${SAMPLE_SKIP_DETAIL}"
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
for required_timeout_tool in bash dirname env find mkdir readelf realpath rg setsid \
    sha256sum sleep sort stat tail tee timeout; do
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
    bash -- "${TIMEOUT_DRIVER}" --mode quick --capability go \
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
    if [[ ${result_name} == capability:go:* ]]; then
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

# Prove that the one-shot py_compile suite gets an isolated cache while
# subprocess-heavy unittest discovery clears even an inherited cache prefix.
PYTHON_ROOT=${FIXTURE}/python-repository
PYTHON_BIN_ROOT=${FIXTURE}/python-bin
PYTHON_DRIVER=${PYTHON_ROOT}/tests/run-optimization-tests.sh
PYTHON_TEST_DIR=${PYTHON_ROOT}/tests/unit
PYTHON_OUTPUT=${FIXTURE}/python-output
PYTHON_ENV_MARKER=${FIXTURE}/python-unittest-environment.txt
mkdir -p -- "${PYTHON_ROOT}/bench" "${PYTHON_ROOT}/optimization" \
    "${PYTHON_ROOT}/scripts" "${PYTHON_TEST_DIR}" "${PYTHON_BIN_ROOT}"
cp -- "${DRIVER}" "${PYTHON_DRIVER}"
printf '%s\n' \
    'import os' \
    'from pathlib import Path' \
    'import unittest' \
    '' \
    'class DriverPythonEnvironmentTest(unittest.TestCase):' \
    '    def test_unittest_environment(self):' \
    '        self.assertEqual(os.environ.get("PYTHONDONTWRITEBYTECODE"), "1")' \
    '        self.assertNotIn("PYTHONPYCACHEPREFIX", os.environ)' \
    "        Path('${PYTHON_ENV_MARKER}').write_text('dontwrite=1\\npycacheprefix=unset\\n', encoding='utf-8')" \
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
PATH=${PYTHON_BIN_ROOT} \
PYTHONPYCACHEPREFIX=/inherited-prefix-that-must-not-reach-unittest \
    bash -- "${PYTHON_DRIVER}" --mode quick --output-dir "${PYTHON_OUTPUT}" \
    >"${FIXTURE}/python-driver.log" 2>&1 || \
    fail 'hermetic Python-environment driver invocation failed'
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
    bash -- "${RECOVERY_DRIVER}" --mode quick --output-dir "${CLANG_FAIL_OUTPUT}" \
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
    bash -- "${RECOVERY_DRIVER}" --mode quick --output-dir "${GCC_FAIL_OUTPUT}" \
    >"${FIXTURE}/recovery-gcc-fail-driver.log" 2>&1 || \
    fail 'GCC/libstdc++ preflight-failure driver invocation failed'
assert_recovery_preflight_skip "${GCC_FAIL_OUTPUT}" \
    'GCC/libstdc++ ABI probe compilation failed'
grep -Fq '<-O2>' "${FIXTURE}/fake-gxx-fail.log" || \
    fail 'GCC/libstdc++ preflight did not invoke g++ after the Clang probe passed'
[[ ! -e ${RECOVERY_MARKER} ]] || \
    fail 'rollback fixture executed despite a failed ABI capability probe'

printf 'PASS: optimization test-driver CLI self-test\n'

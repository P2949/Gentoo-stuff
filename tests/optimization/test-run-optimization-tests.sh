#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)
DRIVER=${REPOSITORY_ROOT}/tests/run-optimization-tests.sh
FIXTURE=$(mktemp -d /tmp/gentoo-optimization-driver-self-test.XXXXXXXX)
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

bash -- "${DRIVER}" --list >"${FIXTURE}/list.txt"
grep -Fq 'recovery-rollback-fixture' "${FIXTURE}/list.txt" || fail 'suite list omits rollback fixture'
grep -Fq 'Clang/libc++ and GCC/libstdc++' "${FIXTURE}/list.txt" || fail 'suite list omits ABI lanes'
grep -Fq 'package-env-duplicate-policy' "${FIXTURE}/list.txt" || \
    fail 'suite list omits package.env duplicate-policy validation'
grep -Fq 'package-env-portage-semantic' "${FIXTURE}/list.txt" || \
    fail 'suite list omits explicit live Portage semantic status'
grep -Fq 'bolt-transaction-fixture' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the hermetic BOLT transaction fixture'
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
HERMETIC_OUTPUT=${FIXTURE}/hermetic-preflight-output
HERMETIC_RUNNER_MARKER=${FIXTURE}/bolt-runner-was-invoked
mkdir -p -- "${HERMETIC_ROOT}/bench" \
    "${HERMETIC_ROOT}/optimization/fixtures/bolt" \
    "${HERMETIC_ROOT}/scripts" "${HERMETIC_ROOT}/tests" "${HERMETIC_BIN}"
cp -- "${DRIVER}" "${HERMETIC_DRIVER}"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    "printf '%s\\n' invoked >'${HERMETIC_RUNNER_MARKER}'" \
    'exit 97' >"${HERMETIC_BOLT_RUNNER}"
chmod 0755 -- "${HERMETIC_DRIVER}" "${HERMETIC_BOLT_RUNNER}"
for required_driver_tool in bash dirname find mkdir realpath sort tee; do
    required_driver_path=$(command -v -- "${required_driver_tool}") || \
        fail "self-test prerequisite is unavailable: ${required_driver_tool}"
    ln -s -- "${required_driver_path}" \
        "${HERMETIC_BIN}/${required_driver_tool}"
done

PATH=${HERMETIC_BIN} bash -- "${HERMETIC_DRIVER}" \
    --mode quick --capability bolt --output-dir "${HERMETIC_OUTPUT}" \
    >"${FIXTURE}/hermetic-preflight.log" 2>&1 || \
    fail 'hermetic capability-preflight driver invocation failed'
[[ ! -e ${HERMETIC_RUNNER_MARKER} ]] || \
    fail 'BOLT runner executed despite a failed dependency preflight'

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
MISSING_COMMANDS=${BOLT_SKIP_DETAIL#missing required command(s): }
for expected_missing_command in awk chmod clang cmp cp file getcap getfattr \
    grep head lddtree llvm-bolt merge-fdata nm objcopy perf perf2bolt readelf \
    readlink rm sed setfattr sha256sum stat strip tail timeout tr mv xargs; do
    case ,${MISSING_COMMANDS}, in
        *,${expected_missing_command},*) ;;
        *) fail "BOLT dependency preflight did not report missing ${expected_missing_command}" ;;
    esac
done
grep -Fxq 'fail=0' "${HERMETIC_OUTPUT}/summary.txt" || \
    fail 'hermetic preflight SKIP was incorrectly counted as a failure'
grep -Fxq 'exit_status=0' "${HERMETIC_OUTPUT}/summary.txt" || \
    fail 'hermetic preflight SKIP produced a nonzero driver status'

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
for required_python_tool in bash dirname env find mkdir realpath sort tee; do
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
    mkdir od readelf realpath rm sha256sum sort stat tee; do
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

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
    readlink sed setfattr sha256sum stat strip tail timeout tr xargs; do
    case ,${MISSING_COMMANDS}, in
        *,${expected_missing_command},*) ;;
        *) fail "BOLT dependency preflight did not report missing ${expected_missing_command}" ;;
    esac
done
grep -Fxq 'fail=0' "${HERMETIC_OUTPUT}/summary.txt" || \
    fail 'hermetic preflight SKIP was incorrectly counted as a failure'
grep -Fxq 'exit_status=0' "${HERMETIC_OUTPUT}/summary.txt" || \
    fail 'hermetic preflight SKIP produced a nonzero driver status'

printf 'PASS: optimization test-driver CLI self-test\n'

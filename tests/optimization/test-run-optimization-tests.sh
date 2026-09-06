#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)
DRIVER=${REPOSITORY_ROOT}/tests/run-optimization-tests.sh
TRUSTED_ROOT_CREATED=0
ZOMBIE_PARENT=
if ((EUID == 0)); then
    TRUSTED_ROOT=/var/tmp/gentoo-optimization
    VAR_TMP_MODE=$(stat -c %a -- /var/tmp 2>/dev/null || true)
    if [[ ! -d /var/tmp || -L /var/tmp ||
          $(realpath -e -- /var/tmp) != /var/tmp ||
          $(stat -c %u -- /var/tmp) != 0 ||
          ! ${VAR_TMP_MODE} =~ ^[0-7]+$ ]] ||
        { (( (8#${VAR_TMP_MODE} & 8#022) != 0 )) &&
          (( (8#${VAR_TMP_MODE} & 8#1000) == 0 )); }; then
        printf '%s\n' \
            'FAIL: root self-test prerequisite /var/tmp must be root-owned and sticky or non-writable by group/other' >&2
        exit 1
    fi
    if [[ ! -e ${TRUSTED_ROOT} && ! -L ${TRUSTED_ROOT} ]]; then
        mkdir -m 0700 -- "${TRUSTED_ROOT}"
        TRUSTED_ROOT_CREATED=1
    fi
    TRUSTED_ROOT_MODE=$(stat -c %a -- "${TRUSTED_ROOT}" 2>/dev/null || true)
    if [[ ! -d ${TRUSTED_ROOT} || -L ${TRUSTED_ROOT} ||
          $(realpath -e -- "${TRUSTED_ROOT}") != "${TRUSTED_ROOT}" ||
          $(stat -c %u -- "${TRUSTED_ROOT}") != 0 ||
          ! ${TRUSTED_ROOT_MODE} =~ ^[0-7]+$ ]] ||
        (( (8#${TRUSTED_ROOT_MODE} & 8#022) != 0 )); then
        printf 'FAIL: root self-test prerequisite is untrusted: %s\n' \
            "${TRUSTED_ROOT}" >&2
        exit 1
    fi
    FIXTURE=$(mktemp -d \
        "${TRUSTED_ROOT}/optimization-driver-self-test.XXXXXXXX")
else
    FIXTURE=$(mktemp -d /tmp/gentoo-optimization-driver-self-test.XXXXXXXX)
fi
cleanup() {
    local status=$?
    if [[ -n ${ZOMBIE_PARENT} ]]; then
        kill -TERM "${ZOMBIE_PARENT}" 2>/dev/null || true
        wait "${ZOMBIE_PARENT}" 2>/dev/null || true
    fi
    rm -rf -- "${FIXTURE}"
    if ((TRUSTED_ROOT_CREATED)); then
        rmdir -- "${TRUSTED_ROOT}" 2>/dev/null || true
    fi
    return "${status}"
}
trap cleanup EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

install_hermetic_contract_support() {
    local repository=$1 binary_directory=$2
    local contract=${repository}/optimization/phase2-authoritative-test-contract.json
    local generated=${contract}.generated
    local python3_path git_path readelf_path
    python3_path=$(command -v -- python3) || \
        fail 'self-test prerequisite is unavailable: python3'
    mkdir -p -- "${repository}/optimization" \
        "${repository}/scripts/optimization/verify" \
        "${repository}/tests/contract-unit" \
        "${repository}/tests/optimization"
    cp -- "${REPOSITORY_ROOT}/scripts/optimization/verify/phase2-test-contract.py" \
        "${repository}/scripts/optimization/verify/phase2-test-contract.py"
    cp -- "${REPOSITORY_ROOT}/scripts/optimization/verify/phase2-evidence.py" \
        "${repository}/scripts/optimization/verify/phase2-evidence.py"
    cp -- "${REPOSITORY_ROOT}/scripts/optimization/verify/run-unittest-suite.py" \
        "${repository}/scripts/optimization/verify/run-unittest-suite.py"
    printf '%s\n' \
        'import unittest' \
        'class ContractIdentityTest(unittest.TestCase):' \
        '    def test_identity(self):' \
        '        pass' \
        >"${repository}/tests/contract-unit/test_contract_identity.py"
    printf '%s\n' \
        'import unittest' \
        'class EvidenceContractIdentityTest(unittest.TestCase):' \
        '    def test_identity(self):' \
        '        pass' \
        >"${repository}/tests/optimization/test_phase2_evidence.py"
    printf '%s\n' \
        'import unittest' \
        'class StressContractIdentityTest(unittest.TestCase):' \
        '    def test_child_barrier_crash_recovery_stress(self):' \
        '        pass' \
        >"${repository}/tests/optimization/test_production_profile_lock_transaction.py"
    printf '%s\n' \
        '{' \
        '  "expected_diagnostic_subtests": [],' \
        '  "portable_allowed_required_skips": [],' \
        '  "portable_allowed_top_level_skips": [],' \
        '  "required_named_subtests": [],' \
        '  "schema": "gentoo-optimization-phase2-authoritative-test-contract-v1",' \
        '  "top_level": {"exact_names": [], "prefix_groups": []},' \
        '  "unittest_suites": []' \
        '}' >"${contract}"
    if [[ ! -e ${binary_directory}/python3 && ! -L ${binary_directory}/python3 ]]; then
        ln -s -- "${python3_path}" "${binary_directory}/python3"
    fi
    git_path=$(command -v -- git) || \
        fail 'self-test prerequisite is unavailable: git'
    if [[ ! -e ${binary_directory}/git && ! -L ${binary_directory}/git ]]; then
        ln -s -- "${git_path}" "${binary_directory}/git"
    fi
    readelf_path=$(command -v -- readelf) ||
        fail 'self-test prerequisite is unavailable: readelf'
    if [[ ! -e ${binary_directory}/readelf &&
          ! -L ${binary_directory}/readelf ]]; then
        ln -s -- "${readelf_path}" "${binary_directory}/readelf"
    fi
    if [[ ! -e ${binary_directory}/shellcheck &&
          ! -L ${binary_directory}/shellcheck ]]; then
        # The positional expression expands only in the generated fixture.
        # shellcheck disable=SC2016
        printf '%s\n' \
            '#!/usr/bin/bash' \
            'if [[ ${1:-} == --version ]]; then' \
            '    printf "version: fixture-shellcheck\\n"' \
            'fi' \
            'exit 0' >"${binary_directory}/shellcheck"
        chmod 0755 -- "${binary_directory}/shellcheck"
    fi
    printf '%s\n' \
        '{' \
        '  "authoritative_test_path": [' \
        "    \"${binary_directory}\"" \
        '  ],' \
        '  "test_execution_tools": [' \
        '    "bash",' \
        '    "env",' \
        '    "git",' \
        '    "python3",' \
        '    "setsid",' \
        '    "shellcheck",' \
        '    "sleep",' \
        '    "timeout"' \
        '  ]' \
        '}' >"${repository}/optimization/phase2-evidence-policy.json"
    printf '%s\n' \
        '{' \
        '  "schema": "gentoo-optimization-phase2-tool-manifest-v1",' \
        '  "tools": [' \
        "    {\"name\": \"bash\", \"path\": \"${binary_directory}/bash\", \"version_args\": [\"--version\"]}," \
        "    {\"name\": \"env\", \"path\": \"${binary_directory}/env\", \"version_args\": [\"--version\"]}," \
        "    {\"name\": \"git\", \"path\": \"${binary_directory}/git\", \"version_args\": [\"--version\"]}," \
        "    {\"name\": \"python3\", \"path\": \"${binary_directory}/python3\", \"version_args\": [\"--version\"]}," \
        "    {\"name\": \"readelf\", \"path\": \"${binary_directory}/readelf\", \"version_args\": [\"--version\"]}," \
        "    {\"name\": \"setsid\", \"path\": \"${binary_directory}/setsid\", \"version_args\": [\"--version\"]}," \
        "    {\"name\": \"shellcheck\", \"path\": \"${binary_directory}/shellcheck\", \"version_args\": [\"--version\"]}," \
        "    {\"name\": \"sleep\", \"path\": \"${binary_directory}/sleep\", \"version_args\": [\"--version\"]}," \
        "    {\"name\": \"timeout\", \"path\": \"${binary_directory}/timeout\", \"version_args\": [\"--version\"]}" \
        '  ]' \
        '}' >"${repository}/optimization/phase2-tool-manifest.json"
    "${python3_path}" -I -B \
        "${REPOSITORY_ROOT}/scripts/optimization/verify/phase2-test-contract.py" \
        generate --repository-root "${repository}" --contract "${contract}" \
        --output "${generated}" >/dev/null
    mv -f -- "${generated}" "${contract}"
}

[[ -f ${DRIVER} ]] || fail "driver is absent: ${DRIVER}"

bash -- "${DRIVER}" --help >"${FIXTURE}/help.txt"
grep -Fq -- '--mode smoke' "${FIXTURE}/help.txt" || fail 'help omits smoke mode'
grep -Fq -- '--mode checkpoint-smoke' "${FIXTURE}/help.txt" || \
    fail 'help omits checkpoint-smoke mode'
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
grep -Fq 'CHECKPOINT_SMOKE_TIMEOUT_SECONDS=600' "${FIXTURE}/help.txt" || \
    fail 'help omits the checkpoint-smoke deadline'
grep -Fq 'CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS=90' "${FIXTURE}/help.txt" || \
    fail 'help omits the checkpoint-smoke per-identity deadline'
grep -Fq 'RECOVERY_SUITE_TIMEOUT_SECONDS=2700' "${FIXTURE}/help.txt" || \
    fail 'help omits the complete recovery-suite deadline'
grep -Fq 'TEST_CASE_TIMEOUT_SECONDS_CLANG_IR' "${FIXTURE}/help.txt" || \
    fail 'help omits normalized per-capability deadline overrides'
grep -Fq 'GENTOO_OPT_AUTHORITATIVE=0|1' "${FIXTURE}/help.txt" || \
    fail 'help omits authoritative subtest accounting'
grep -Fq -- '--contract-topology' "${FIXTURE}/help.txt" || \
    fail 'help omits deterministic contract topology discovery'

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
grep -Fq 'no-legacy-bolt' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the retired legacy BOLT gate'
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
grep -Fq 'checkpoint-smoke' "${FIXTURE}/list.txt" || \
    fail 'suite list omits the focused checkpoint state-machine gate'
for capability in clang-ir clang-sample gcc rust go bolt; do
    grep -Eq "^[[:space:]]+${capability}([[:space:]]|$)" \
        "${FIXTURE}/list.txt" || fail "suite list omits ${capability}"
done
bash -- "${DRIVER}" --contract-topology >"${FIXTURE}/contract-topology.tsv"
grep -Fxq $'top-level\tno-legacy-bolt' \
    "${FIXTURE}/contract-topology.tsv" || \
    fail 'contract topology omits the retired legacy BOLT gate'
grep -Fxq $'top-level\tphase2-evidence-contract' \
    "${FIXTURE}/contract-topology.tsv" || \
    fail 'contract topology omits the distinct Phase 2 evidence identity'
[[ $(grep -Fc $'unittest\tphase2-evidence-contract\t' \
    "${FIXTURE}/contract-topology.tsv") -eq 1 ]] || \
    fail 'contract topology does not contain exactly one Phase 2 evidence suite'
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

if CHECKPOINT_SMOKE_TIMEOUT_SECONDS=0 bash -- "${DRIVER}" \
    --mode checkpoint-smoke >"${FIXTURE}/bad-checkpoint-timeout.log" 2>&1; then
    fail 'zero checkpoint-smoke timeout unexpectedly succeeded'
fi
grep -Fq \
    'CHECKPOINT_SMOKE_TIMEOUT_SECONDS must be a positive integer number of seconds' \
    "${FIXTURE}/bad-checkpoint-timeout.log" || \
    fail 'invalid checkpoint-smoke timeout lacks a visible diagnostic'

if CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS=0 bash -- "${DRIVER}" \
    --mode checkpoint-smoke >"${FIXTURE}/bad-checkpoint-method-timeout.log" 2>&1; then
    fail 'zero checkpoint-smoke per-identity timeout unexpectedly succeeded'
fi
grep -Fq \
    'CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS must be a positive integer number of seconds' \
    "${FIXTURE}/bad-checkpoint-method-timeout.log" || \
    fail 'invalid checkpoint-smoke per-identity timeout lacks a visible diagnostic'

if RECOVERY_SUITE_TIMEOUT_SECONDS=0 bash -- "${DRIVER}" --mode smoke \
    >"${FIXTURE}/bad-recovery-timeout.log" 2>&1; then
    fail 'zero recovery-suite timeout unexpectedly succeeded'
fi
grep -Fq \
    'RECOVERY_SUITE_TIMEOUT_SECONDS must be a positive integer number of seconds' \
    "${FIXTURE}/bad-recovery-timeout.log" || \
    fail 'invalid recovery-suite timeout lacks a visible diagnostic'

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

# A zombie retains a numeric PID and process-group identity, so kill -0 alone
# produces a false positive. Keep a deliberately unreaped zombie in its own
# process group and prove the driver's bounded procfs inspection calls it
# quiescent while its live parent remains outside that group.
ZOMBIE_MARKER=${FIXTURE}/zombie-group.txt
python3 - "${ZOMBIE_MARKER}" <<'PY' &
import os
from pathlib import Path
import signal
import sys
import time

marker = Path(sys.argv[1])
child = os.fork()
if child == 0:
    os.setpgid(0, 0)
    os._exit(0)

def reap_and_exit(_signal: int, _frame: object) -> None:
    os.waitpid(child, 0)
    raise SystemExit(0)

signal.signal(signal.SIGTERM, reap_and_exit)
for _ in range(200):
    try:
        fields = Path(f"/proc/{child}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
    except (FileNotFoundError, IndexError):
        break
    if fields[0] == "Z" and int(fields[2]) == child:
        marker.write_text(f"{child}\n", encoding="utf-8")
        while True:
            signal.pause()
    time.sleep(0.01)
os.waitpid(child, 0)
raise SystemExit(91)
PY
ZOMBIE_PARENT=$!
for ((attempt = 0; attempt < 200; attempt += 1)); do
    [[ ! -s ${ZOMBIE_MARKER} ]] || break
    kill -0 "${ZOMBIE_PARENT}" 2>/dev/null || break
    sleep 0.01
done
if [[ ! -s ${ZOMBIE_MARKER} ]]; then
    kill -TERM "${ZOMBIE_PARENT}" 2>/dev/null || true
    wait "${ZOMBIE_PARENT}" 2>/dev/null || true
    fail 'could not construct the zombie-only process-group fixture'
fi
ZOMBIE_PGID=$(<"${ZOMBIE_MARKER}")
kill -0 -- "-${ZOMBIE_PGID}" 2>/dev/null || \
    fail 'zombie-only fixture is not visible to kill -0'
set +e
bash -- "${DRIVER}" --internal-process-group-probe "${ZOMBIE_PGID}" \
    >"${FIXTURE}/zombie-group-probe.log" 2>&1
ZOMBIE_PROBE_STATUS=$?
set -e
kill -TERM "${ZOMBIE_PARENT}" 2>/dev/null || true
wait "${ZOMBIE_PARENT}" 2>/dev/null || true
ZOMBIE_PARENT=
[[ ${ZOMBIE_PROBE_STATUS} -eq 1 ]] || \
    fail "zombie-only process group was reported active (status ${ZOMBIE_PROBE_STATUS})"
grep -Fxq quiescent "${FIXTURE}/zombie-group-probe.log" || \
    fail 'zombie-only process-group probe omitted the quiescent result'

FAKE_PROC=${FIXTURE}/fake-proc
mkdir -p -- "${FAKE_PROC}/self" "${FAKE_PROC}/100" "${FAKE_PROC}/101"
printf '1 (self) S 0 1 1 0 0\n' >"${FAKE_PROC}/self/stat"
assert_fake_process_group_state() {
    local expected_status=$1 expected_output=$2 label=$3
    shift 3
    local status
    "$@" >"${FIXTURE}/fake-proc-${label}.log" 2>&1 && status=0 || status=$?
    [[ ${status} -eq ${expected_status} ]] || \
        fail "${label} fake process group returned ${status}, expected ${expected_status}"
    grep -Fxq "${expected_output}" "${FIXTURE}/fake-proc-${label}.log" || \
        fail "${label} fake process group omitted ${expected_output}"
}
for dead_state in Z X x; do
    printf '100 (dead member) %s 1 777 0 0 0\n' "${dead_state}" \
        >"${FAKE_PROC}/100/stat"
    rm -f -- "${FAKE_PROC}/101/stat"
    assert_fake_process_group_state 1 quiescent "dead-${dead_state}" \
        bash -- "${DRIVER}" --internal-process-group-probe 777 \
        --internal-process-group-proc-root "${FAKE_PROC}" \
        --internal-process-group-assume-visible
done
printf '100 (dead member) Z 1 777 0 0 0\n' >"${FAKE_PROC}/100/stat"
printf '101 (live member) S 1 777 0 0 0\n' >"${FAKE_PROC}/101/stat"
assert_fake_process_group_state 0 active mixed-live \
    bash -- "${DRIVER}" --internal-process-group-probe 777 \
    --internal-process-group-proc-root "${FAKE_PROC}" \
    --internal-process-group-assume-visible
printf 'malformed\n' >"${FAKE_PROC}/101/stat"
assert_fake_process_group_state 0 active malformed-fail-closed \
    bash -- "${DRIVER}" --internal-process-group-probe 777 \
    --internal-process-group-proc-root "${FAKE_PROC}" \
    --internal-process-group-assume-visible

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
install_hermetic_contract_support "${HERMETIC_ROOT}" "${HERMETIC_BIN}"

# A PATH-selected wrapper must never stand in for any PATH-resolved execution-
# core tool during an authoritative run. Exercise this contract against the
# hermetic reviewed manifest so the portable CI host need not contain Gentoo's
# complete production tool topology. The driver itself starts through reviewed
# Bash, proving preflight rejection rather than accidentally executing a shadow.
TOOL_SHADOW_BIN=${FIXTURE}/tool-shadow-bin
TOOL_SHADOW_MARKER=${FIXTURE}/tool-shadow-was-executed
mkdir -p -- "${TOOL_SHADOW_BIN}"
for shadowed_tool in bash env git python3 setsid sleep timeout; do
    rm -f -- "${TOOL_SHADOW_BIN}"/* "${TOOL_SHADOW_MARKER}"
    printf '%s\n' \
        '#!/usr/bin/bash' \
        "printf '%s\\n' '${shadowed_tool}' >'${TOOL_SHADOW_MARKER}'" \
        'exit 99' >"${TOOL_SHADOW_BIN}/${shadowed_tool}"
    chmod 0755 -- "${TOOL_SHADOW_BIN}/${shadowed_tool}"
    set +e
    PATH=${TOOL_SHADOW_BIN}:${HERMETIC_BIN} \
    GENTOO_OPT_AUTHORITATIVE=1 \
    SHELLCHECK=${HERMETIC_BIN}/shellcheck \
        "${HERMETIC_BIN}/bash" -- "${HERMETIC_DRIVER}" \
        --internal-process-group-probe 2147483647 \
        >"${FIXTURE}/shadow-${shadowed_tool}.log" 2>&1
    shadow_status=$?
    set -e
    [[ ${shadow_status} -eq 2 ]] || \
        fail "authoritative ${shadowed_tool} shadow returned ${shadow_status}, expected 2"
    grep -Fq \
        "authoritative PATH shadows reviewed ${shadowed_tool}:" \
        "${FIXTURE}/shadow-${shadowed_tool}.log" || \
        fail "authoritative ${shadowed_tool} shadow lacks an exact diagnostic"
    [[ ! -e ${TOOL_SHADOW_MARKER} ]] || \
        fail "authoritative driver executed the PATH shadow for ${shadowed_tool}"
done
rm -f -- "${TOOL_SHADOW_BIN}"/*

set +e
PATH=${HERMETIC_BIN} GENTOO_OPT_AUTHORITATIVE=1 \
SHELLCHECK=${HERMETIC_BIN}/shellcheck \
    /usr/bin/bash -- "${HERMETIC_DRIVER}" \
    --internal-process-group-probe 2147483647 \
    >"${FIXTURE}/authoritative-reviewed-entrypoints.log" 2>&1
reviewed_entrypoint_status=$?
set -e
[[ ${reviewed_entrypoint_status} -eq 1 ]] || \
    fail "authoritative reviewed-entrypoint probe returned ${reviewed_entrypoint_status}, expected 1"
grep -Fxq quiescent "${FIXTURE}/authoritative-reviewed-entrypoints.log" || \
    fail 'authoritative reviewed-entrypoint probe did not reach process inspection'

set +e
PATH=${HERMETIC_BIN} GENTOO_OPT_AUTHORITATIVE=1 \
SHELLCHECK=${HERMETIC_BIN}/shellcheck \
    /bin/bash -- "${HERMETIC_DRIVER}" \
    --internal-process-group-probe 2147483647 \
    >"${FIXTURE}/authoritative-bin-bash.log" 2>&1
bin_bash_status=$?
set -e
[[ ${bin_bash_status} -eq 2 ]] || \
    fail "authoritative /bin/bash probe returned ${bin_bash_status}, expected 2"
grep -Fq 'authoritative driver Bash argv-zero differs from the reviewed entry point:' \
    "${FIXTURE}/authoritative-bin-bash.log" || \
    fail 'authoritative driver accepted /bin/bash for another reviewed Bash entry point'

set +e
PATH=${HERMETIC_BIN} GENTOO_OPT_AUTHORITATIVE=1 \
SHELLCHECK=/usr/bin/true \
    /usr/bin/bash -- "${HERMETIC_DRIVER}" \
    --internal-process-group-probe 2147483647 \
    >"${FIXTURE}/authoritative-fake-shellcheck.log" 2>&1
fake_shellcheck_status=$?
set -e
[[ ${fake_shellcheck_status} -eq 2 ]] || \
    fail "authoritative fake ShellCheck probe returned ${fake_shellcheck_status}, expected 2"
grep -Fq 'authoritative ShellCheck entry point differs from the reviewed manifest:' \
    "${FIXTURE}/authoritative-fake-shellcheck.log" || \
    fail 'authoritative driver accepted an unreviewed ShellCheck entry point'

PREBIND_SHADOW_BIN=${FIXTURE}/prebind-shadow-bin
PREBIND_DIRNAME_MARKER=${FIXTURE}/prebind-dirname-executed
mkdir -p -- "${PREBIND_SHADOW_BIN}"
printf '%s\n' \
    '#!/usr/bin/bash' \
    "printf '%s\\n' executed >'${PREBIND_DIRNAME_MARKER}'" \
    'exec /usr/bin/dirname "$@"' \
    >"${PREBIND_SHADOW_BIN}/dirname"
chmod 0755 -- "${PREBIND_SHADOW_BIN}/dirname"
set +e
PATH=${PREBIND_SHADOW_BIN}:${HERMETIC_BIN} GENTOO_OPT_AUTHORITATIVE=1 \
SHELLCHECK=${HERMETIC_BIN}/shellcheck \
    "${HERMETIC_BIN}/bash" -- "${HERMETIC_DRIVER}" \
    --internal-process-group-probe 2147483647 \
    >"${FIXTURE}/authoritative-prebind-shadow.log" 2>&1
prebind_shadow_status=$?
set -e
[[ ${prebind_shadow_status} -eq 2 ]] || \
    fail "authoritative pre-binding shadow probe returned ${prebind_shadow_status}, expected 2"
grep -Fq 'authoritative PATH differs from the reviewed execution path:' \
    "${FIXTURE}/authoritative-prebind-shadow.log" || \
    fail 'authoritative pre-binding shadow lacks the exact PATH diagnostic'
[[ ! -e ${PREBIND_DIRNAME_MARKER} ]] || \
    fail 'authoritative driver executed PATH-selected dirname before tool binding'

TOOL_ALIAS_BIN=${FIXTURE}/tool-alias-bin
mkdir -p -- "${TOOL_ALIAS_BIN}"
ln -s -- "${HERMETIC_BIN}/git" "${TOOL_ALIAS_BIN}/git"
ln -s -- "${HERMETIC_BIN}/python3" "${TOOL_ALIAS_BIN}/python3"
set +e
PATH=${TOOL_ALIAS_BIN}:${HERMETIC_BIN} GENTOO_OPT_AUTHORITATIVE=1 \
SHELLCHECK=${HERMETIC_BIN}/shellcheck \
    "${HERMETIC_BIN}/bash" -- "${HERMETIC_DRIVER}" \
    --internal-process-group-probe 2147483647 \
    >"${FIXTURE}/authoritative-symlink-entrypoints.log" 2>&1
symlink_entrypoint_status=$?
set -e
[[ ${symlink_entrypoint_status} -eq 2 ]] || \
    fail "authoritative alias-PATH probe returned ${symlink_entrypoint_status}, expected 2"
grep -Fq 'authoritative PATH shadows reviewed git:' \
    "${FIXTURE}/authoritative-symlink-entrypoints.log" || \
    fail 'authoritative driver accepted an unreviewed identity-equivalent PATH'

MANIFEST_SHADOW_BIN=${FIXTURE}/manifest-shadow-bin
MANIFEST_SHADOW_MARKER=${FIXTURE}/manifest-shadow-was-executed
mkdir -p -- "${MANIFEST_SHADOW_BIN}"
printf '%s\n' \
    '#!/usr/bin/bash' \
    "printf '%s\\n' executed >'${MANIFEST_SHADOW_MARKER}'" \
    'exit 99' >"${MANIFEST_SHADOW_BIN}/readelf"
chmod 0755 -- "${MANIFEST_SHADOW_BIN}/readelf"
set +e
PATH=${MANIFEST_SHADOW_BIN}:${HERMETIC_BIN} GENTOO_OPT_AUTHORITATIVE=1 \
SHELLCHECK=${HERMETIC_BIN}/shellcheck \
    "${HERMETIC_BIN}/bash" -- "${HERMETIC_DRIVER}" \
    --internal-process-group-probe 2147483647 \
    >"${FIXTURE}/authoritative-manifest-shadow.log" 2>&1
manifest_shadow_status=$?
set -e
[[ ${manifest_shadow_status} -eq 2 ]] || \
    fail "authoritative non-core manifest shadow returned ${manifest_shadow_status}, expected 2"
grep -Fq \
    "authoritative PATH shadows reviewed readelf: expected=${HERMETIC_BIN}/readelf selected=${MANIFEST_SHADOW_BIN}/readelf" \
    "${FIXTURE}/authoritative-manifest-shadow.log" || \
    fail 'authoritative driver accepted a non-core reviewed-tool PATH shadow'
[[ ! -e ${MANIFEST_SHADOW_MARKER} ]] || \
    fail 'authoritative driver executed the non-core manifest PATH shadow'

set +e
PATH=${HERMETIC_BIN} GENTOO_OPT_AUTHORITATIVE=1 \
SHELLCHECK=${HERMETIC_BIN}/shellcheck \
    "${HERMETIC_BIN}/bash" -- "${HERMETIC_DRIVER}" \
    --mode capabilities --output-dir "${AUTHORITATIVE_OUTPUT}" \
    >"${FIXTURE}/hermetic-authoritative.log" 2>&1
AUTHORITATIVE_STATUS=$?
set -e
if [[ ${AUTHORITATIVE_STATUS} -ne 1 ]]; then
    sed -n '1,240p' "${FIXTURE}/hermetic-authoritative.log" >&2
    fail "incomplete authoritative gate returned ${AUTHORITATIVE_STATUS}, expected 1"
fi
grep -Fxq 'mode=capabilities' "${AUTHORITATIVE_OUTPUT}/summary.txt" || \
    fail 'hermetic authoritative-accounting summary lost its exact mode'
grep -Fxq 'authoritative=1' "${AUTHORITATIVE_OUTPUT}/summary.txt" || \
    fail 'authoritative mode did not enable fail-closed subtest accounting'
grep -Fq $'PASS\tphase2-test-contract-static\t' \
    "${AUTHORITATIVE_OUTPUT}/results.tsv" || \
    fail 'authoritative capabilities mode omitted its static exact-contract gate'
[[ -s ${AUTHORITATIVE_OUTPUT}/test-contract.log ]] || \
    fail 'authoritative capabilities mode omitted its final exact-contract gate'
awk -F '\t' '
    $2 == "phase2-run-provenance-start" { provenance = NR }
    $2 == "phase2-test-contract-static" { contract = NR }
    END { exit !(provenance > 0 && contract > provenance) }
' "${AUTHORITATIVE_OUTPUT}/results.tsv" || \
    fail 'static contract discovery was not enclosed after provenance start'
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
install_hermetic_contract_support "${TIMEOUT_ROOT}" "${TIMEOUT_BIN_ROOT}"
set +e
PATH=${TIMEOUT_BIN_ROOT} GENTOO_OPT_AUTHORITATIVE=1 \
SHELLCHECK=${TIMEOUT_BIN_ROOT}/shellcheck \
    "${TIMEOUT_BIN_ROOT}/bash" -- "${TIMEOUT_DRIVER}" --mode smoke --capability go \
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
PYTHON_OPTIMIZATION_TEST_DIR=${PYTHON_ROOT}/tests/optimization
PYTHON_RECOVERY_TEST_DIR=${PYTHON_ROOT}/tests/optimization/recovery
PYTHON_OUTPUT=${FIXTURE}/python-output
PYTHON_ENV_MARKER=${FIXTURE}/python-unittest-environment.txt
mkdir -p -- "${PYTHON_ROOT}/bench" "${PYTHON_ROOT}/optimization" \
    "${PYTHON_ROOT}/scripts/optimization/verify" "${PYTHON_TEST_DIR}" \
    "${PYTHON_RECOVERY_TEST_DIR}" \
    "${PYTHON_BIN_ROOT}"
cp -- "${DRIVER}" "${PYTHON_DRIVER}"
cp -- "${REPOSITORY_ROOT}/optimization/phase2-evidence-policy.json" \
    "${PYTHON_ROOT}/optimization/phase2-evidence-policy.json"
cp -- "${REPOSITORY_ROOT}/optimization/phase2-tool-manifest.json" \
    "${PYTHON_ROOT}/optimization/phase2-tool-manifest.json"
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
printf '%s\n' \
    'import os' \
    'import unittest' \
    '' \
    '@unittest.skipUnless(' \
    '    os.environ.get("GENTOO_OPT_RUN_CHECKPOINT_HOST_CAPABILITIES") == "1",' \
    '    "checkpoint host primitives require the authoritative opt-in",' \
    ')' \
    'class CheckpointHostOptInTest(unittest.TestCase):' \
    '    def test_authoritative_recovery_suite_receives_opt_in(self):' \
    '        self.assertEqual(' \
    '            os.environ.get("GENTOO_OPT_RUN_CHECKPOINT_HOST_CAPABILITIES"), "1"' \
    '        )' \
    >"${PYTHON_RECOVERY_TEST_DIR}/test_checkpoint_host_opt_in.py"
printf '%s\n' \
    'import os' \
    'import unittest' \
    '' \
    '@unittest.skipUnless(' \
    '    os.environ.get("GENTOO_OPT_RUN_JSONSCHEMA_HOST_CAPABILITIES") == "1",' \
    '    "jsonschema prerequisite host primitives require the authoritative opt-in",' \
    ')' \
    'class JsonschemaHostOptInTest(unittest.TestCase):' \
    '    def test_authoritative_optimization_suite_receives_opt_in(self):' \
    '        self.assertEqual(' \
    '            os.environ.get("GENTOO_OPT_RUN_JSONSCHEMA_HOST_CAPABILITIES"), "1"' \
    '        )' \
    >"${PYTHON_OPTIMIZATION_TEST_DIR}/test_jsonschema_host_opt_in.py"
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
GENTOO_OPT_RUN_CHECKPOINT_HOST_CAPABILITIES=1 \
GENTOO_OPT_RUN_JSONSCHEMA_HOST_CAPABILITIES=1 \
RECOVERY_SUITE_TIMEOUT_SECONDS=2711 \
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
grep -Fxq 'mandatory_internal_skip=3' "${PYTHON_OUTPUT}/summary.txt" || \
    fail 'Python unittest, jsonschema, and recovery host opt-in skips were not all surfaced'
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
grep -Fq $'SKIP\trequired\tpython-unit-tests:tests/optimization/recovery\tpython.' \
    "${PYTHON_OUTPUT}/subtests.tsv" || \
    fail 'portable recovery host-capability opt-in was not an explicit required skip'
grep -Fq $'SKIP\trequired\tpython-unit-tests:tests/optimization\tpython.' \
    "${PYTHON_OUTPUT}/subtests.tsv" || \
    fail 'portable jsonschema host-capability opt-in was not an explicit required skip'
PYTHON_RECOVERY_RESULT_ROWS=0
while IFS=$'\t' read -r result_status result_name result_detail; do
    if [[ ${result_name} == python-unit-tests:tests/optimization/recovery ]]; then
        ((PYTHON_RECOVERY_RESULT_ROWS += 1))
        [[ ${result_status} == PASS ]] || \
            fail "hermetic recovery suite did not pass: ${result_status}"
        [[ ${result_detail} == *'timeout_seconds=2711 '* ]] || \
            fail "recovery suite did not use its dedicated deadline: ${result_detail}"
    fi
done <"${PYTHON_OUTPUT}/results.tsv"
[[ ${PYTHON_RECOVERY_RESULT_ROWS} -eq 1 ]] || \
    fail "expected one hermetic recovery-suite result, found ${PYTHON_RECOVERY_RESULT_ROWS}"

PYTHON_AUTHORITATIVE_OUTPUT=${FIXTURE}/python-authoritative-output
install_hermetic_contract_support "${PYTHON_ROOT}" "${PYTHON_BIN_ROOT}"
set +e
PATH=${PYTHON_BIN_ROOT} \
GENTOO_OPT_AUTHORITATIVE=1 \
SHELLCHECK=${PYTHON_BIN_ROOT}/shellcheck \
    "${PYTHON_BIN_ROOT}/bash" -- "${PYTHON_DRIVER}" --mode stress \
    --output-dir "${PYTHON_AUTHORITATIVE_OUTPUT}" \
    >"${FIXTURE}/python-authoritative-driver.log" 2>&1
PYTHON_AUTHORITATIVE_STATUS=$?
set -e
[[ ${PYTHON_AUTHORITATIVE_STATUS} -eq 1 ]] || \
    fail "authoritative unittest skip produced status ${PYTHON_AUTHORITATIVE_STATUS}, expected 1"
grep -Fq $'PASS\tphase2-test-contract-static\t' \
    "${PYTHON_AUTHORITATIVE_OUTPUT}/results.tsv" || \
    fail 'authoritative stress mode omitted its static exact-contract gate'
[[ -s ${PYTHON_AUTHORITATIVE_OUTPUT}/test-contract.log ]] || \
    fail 'authoritative stress mode omitted its final exact-contract gate'
grep -Fq $'FAIL\tpython-unit-tests:tests/unit\t' \
    "${PYTHON_AUTHORITATIVE_OUTPUT}/results.tsv" || \
    fail 'authoritative unittest skip remained hidden behind top-level PASS'
grep -Fq $'PASS\tpython-unit-tests:tests/optimization/recovery\t' \
    "${PYTHON_AUTHORITATIVE_OUTPUT}/results.tsv" || \
    fail 'authoritative recovery suite did not execute its host-capability opt-in'
grep -Fq $'PASS\tpython-unit-tests:tests/optimization\t' \
    "${PYTHON_AUTHORITATIVE_OUTPUT}/results.tsv" || \
    fail 'authoritative optimization suite did not execute its jsonschema host-capability opt-in'
grep -Fq $'PASS\trequired\tpython-unit-tests:tests/optimization/recovery\tpython.' \
    "${PYTHON_AUTHORITATIVE_OUTPUT}/subtests.tsv" || \
    fail 'authoritative recovery host-capability subtest did not pass'
grep -Fq $'PASS\trequired\tpython-unit-tests:tests/optimization\tpython.' \
    "${PYTHON_AUTHORITATIVE_OUTPUT}/subtests.tsv" || \
    fail 'authoritative jsonschema host-capability subtest did not pass'
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

# checkpoint-smoke must not execute its 18 stateful methods inside one Python
# interpreter.  A leaked signal mask, handler, child, descriptor, or module
# global from one method must be unable to affect the next, and one stuck
# method must terminate at its own reviewed deadline rather than consuming the
# whole checkpoint-smoke case deadline.
CHECKPOINT_ISOLATION_TEST=${PYTHON_RECOVERY_TEST_DIR}/test_create_binpkg_checkpoint.py
CHECKPOINT_ISOLATION_MARKER=${FIXTURE}/checkpoint-isolation.tsv
CHECKPOINT_ISOLATION_OUTPUT=${FIXTURE}/checkpoint-isolation-output
CHECKPOINT_TIMEOUT_MARKER=${FIXTURE}/checkpoint-timeout.tsv
CHECKPOINT_TIMEOUT_OUTPUT=${FIXTURE}/checkpoint-timeout-output
mapfile -t CHECKPOINT_ISOLATION_IDENTITIES < <(
    awk '
        /^readonly -a CHECKPOINT_SMOKE_IDENTITIES=\(/ { inside = 1; next }
        inside && /^\)/ { exit }
        inside {
            sub(/^[[:space:]]+/, "")
            if (length($0)) print
        }
    ' "${DRIVER}"
)
[[ ${#CHECKPOINT_ISOLATION_IDENTITIES[@]} -eq 18 ]] || \
    fail "self-test discovered ${#CHECKPOINT_ISOLATION_IDENTITIES[@]} checkpoint-smoke identities, expected 18"
{
    printf '%s\n' \
        'import os' \
        'import time' \
        'from pathlib import Path' \
        'import unittest' \
        '' \
        '_executed = False' \
        '' \
        'def exercise(label):' \
        '    global _executed' \
        '    if _executed:' \
        '        raise AssertionError("checkpoint methods shared one interpreter")' \
        '    _executed = True' \
        '    fields = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="ascii").rsplit(") ", 1)[1].split()' \
        '    marker = Path(os.environ["CHECKPOINT_ISOLATION_MARKER"])' \
        '    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)' \
        '    try:' \
        '        os.write(descriptor, f"{label}\t{os.getpid()}\t{fields[19]}\n".encode("ascii"))' \
        '        os.fsync(descriptor)' \
        '    finally:' \
        '        os.close(descriptor)' \
        '    if os.environ.get("CHECKPOINT_SMOKE_FORCE_HANG") == label:' \
        '        time.sleep(300)' \
        ''
    checkpoint_class=
    for checkpoint_identity in "${CHECKPOINT_ISOLATION_IDENTITIES[@]}"; do
        checkpoint_qualified=${checkpoint_identity#test_create_binpkg_checkpoint.}
        checkpoint_next_class=${checkpoint_qualified%%.*}
        checkpoint_method=${checkpoint_qualified##*.}
        if [[ ${checkpoint_next_class} != "${checkpoint_class}" ]]; then
            checkpoint_class=${checkpoint_next_class}
            printf 'class %s(unittest.TestCase):\n' "${checkpoint_class}"
        fi
        printf '    def %s(self):\n' "${checkpoint_method}"
        printf "        exercise('%s')\n\n" \
            "${checkpoint_class}.${checkpoint_method}"
    done
} >"${CHECKPOINT_ISOLATION_TEST}"
printf '%s\n' \
    'import unittest' \
    'class Phase2EvidenceTests(unittest.TestCase):' \
    '    def test_exact_topology_rejects_deleted_and_unexpected_cases(self):' \
    '        pass' \
    >"${PYTHON_ROOT}/tests/optimization/test_phase2_evidence.py"

PATH=${PYTHON_BIN_ROOT} \
SHELLCHECK=${PYTHON_BIN_ROOT}/shellcheck \
CHECKPOINT_ISOLATION_MARKER=${CHECKPOINT_ISOLATION_MARKER} \
CHECKPOINT_SMOKE_TIMEOUT_SECONDS=120 \
CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS=10 \
    "${PYTHON_BIN_ROOT}/bash" -- "${PYTHON_DRIVER}" --mode checkpoint-smoke \
    --output-dir "${CHECKPOINT_ISOLATION_OUTPUT}" \
    >"${FIXTURE}/checkpoint-isolation-driver.log" 2>&1 || {
    sed -n '1,240p' "${FIXTURE}/checkpoint-isolation-driver.log" >&2
    fail 'checkpoint-smoke per-identity isolation fixture failed'
}
grep -Fxq 'fail=0' "${CHECKPOINT_ISOLATION_OUTPUT}/summary.txt" || \
    fail 'checkpoint-smoke isolation fixture produced a driver failure'
mapfile -t CHECKPOINT_OBSERVED_IDENTITIES < <(
    cut -f2,3 -- "${CHECKPOINT_ISOLATION_MARKER}" | sort -u
)
[[ ${#CHECKPOINT_OBSERVED_IDENTITIES[@]} -eq 18 ]] || \
    fail "checkpoint-smoke reused an interpreter identity: ${#CHECKPOINT_OBSERVED_IDENTITIES[@]} unique, expected 18"

CHECKPOINT_HANG_IDENTITY=${CHECKPOINT_ISOLATION_IDENTITIES[0]#test_create_binpkg_checkpoint.}
set +e
PATH=${PYTHON_BIN_ROOT} \
SHELLCHECK=${PYTHON_BIN_ROOT}/shellcheck \
CHECKPOINT_ISOLATION_MARKER=${CHECKPOINT_TIMEOUT_MARKER} \
CHECKPOINT_SMOKE_FORCE_HANG=${CHECKPOINT_HANG_IDENTITY} \
CHECKPOINT_SMOKE_TIMEOUT_SECONDS=30 \
CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS=1 \
TEST_CASE_KILL_AFTER_SECONDS=1 \
    "${PYTHON_BIN_ROOT}/bash" -- "${PYTHON_DRIVER}" --mode checkpoint-smoke \
    --output-dir "${CHECKPOINT_TIMEOUT_OUTPUT}" \
    >"${FIXTURE}/checkpoint-timeout-driver.log" 2>&1
CHECKPOINT_TIMEOUT_STATUS=$?
set -e
[[ ${CHECKPOINT_TIMEOUT_STATUS} -eq 1 ]] || \
    fail "checkpoint-smoke hung-method fixture returned ${CHECKPOINT_TIMEOUT_STATUS}, expected 1"
grep -Fq $'FAIL\tcheckpoint-smoke\texit_status=124 ' \
    "${CHECKPOINT_TIMEOUT_OUTPUT}/results.tsv" || \
    fail 'checkpoint-smoke hung method did not surface its inner timeout status'
[[ $(wc -l <"${CHECKPOINT_TIMEOUT_MARKER}") -eq 1 ]] || \
    fail 'checkpoint-smoke continued to later identities after a method timeout'

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

# Portable mode may use a PATH-selected Python entry point, but its finalized
# provenance must identify the wrapper that actually launched every Python
# case.  Use a clean synthetic repository and replace this recursive self-test
# with a harmless same-path stub so the nested smoke gate stays bounded.
PORTABLE_TOOL_ROOT=${FIXTURE}/portable-tool-repository
PORTABLE_TOOL_BIN=${FIXTURE}/portable-tool-bin
PORTABLE_TOOL_OUTPUT=${FIXTURE}/portable-tool-output
PORTABLE_PYTHON_MARKER=${FIXTURE}/portable-python-invocations.log
PORTABLE_GIT_PRECHECK_MARKER=${FIXTURE}/portable-ambient-git-precheck-executed
mkdir -p -- "${PORTABLE_TOOL_ROOT}" "${PORTABLE_TOOL_BIN}"
(
    cd -- "${REPOSITORY_ROOT}"
    git ls-files -z | tar --null --files-from=- --create --file=-
) | (
    cd -- "${PORTABLE_TOOL_ROOT}"
    tar --extract --file=-
)
# The retirement regression is a newly added source in the candidate working
# tree and therefore is not visible to git ls-files until the correction is
# committed.  Copy it explicitly so this pre-commit driver boundary exercises
# the same required-source policy as the eventual clean commit.
cp -- "${REPOSITORY_ROOT}/tests/optimization/test-no-legacy-bolt.sh" \
    "${PORTABLE_TOOL_ROOT}/tests/optimization/test-no-legacy-bolt.sh"
chmod 0755 -- \
    "${PORTABLE_TOOL_ROOT}/tests/optimization/test-no-legacy-bolt.sh"
printf '%s\n' '#!/usr/bin/bash' 'exit 0' \
    >"${PORTABLE_TOOL_ROOT}/tests/optimization/test-run-optimization-tests.sh"
chmod 0755 -- \
    "${PORTABLE_TOOL_ROOT}/tests/optimization/test-run-optimization-tests.sh"
printf '%s\n' \
    '#!/usr/bin/bash' \
    "printf '%s\\n' invoked >>'${PORTABLE_PYTHON_MARKER}'" \
    'exec /usr/bin/python3 "$@"' \
    >"${PORTABLE_TOOL_BIN}/python3"
# The positional expression expands only in the generated fixture.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/bash' \
    'if [[ ${1:-} == --version ]]; then' \
    '    printf "version: portable-fixture-shellcheck\\n"' \
    'fi' \
    'exit 0' >"${PORTABLE_TOOL_BIN}/shellcheck"
# The positional expressions expand only in the generated Git wrapper.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/bash' \
    'if [[ ${1:-} == -C && ${3:-} == status ]]; then' \
    "    printf '%s\\n' executed >'${PORTABLE_GIT_PRECHECK_MARKER}'" \
    '    exit 99' \
    'fi' \
    'exec /usr/bin/git "$@"' >"${PORTABLE_TOOL_BIN}/git"
chmod 0755 -- "${PORTABLE_TOOL_BIN}/python3" \
    "${PORTABLE_TOOL_BIN}/shellcheck" "${PORTABLE_TOOL_BIN}/git"
git -C "${PORTABLE_TOOL_ROOT}" init -q
git -C "${PORTABLE_TOOL_ROOT}" config user.email \
    gentoo-optimization-fixture.invalid
git -C "${PORTABLE_TOOL_ROOT}" config user.name \
    gentoo-optimization-fixture
git -C "${PORTABLE_TOOL_ROOT}" add -A
git -C "${PORTABLE_TOOL_ROOT}" commit -qm portable-tool-provenance-fixture
PATH=${PORTABLE_TOOL_BIN}:/usr/bin:/bin \
SHELLCHECK=${PORTABLE_TOOL_BIN}/shellcheck \
    /usr/bin/bash -- \
    "${PORTABLE_TOOL_ROOT}/tests/run-optimization-tests.sh" --mode smoke \
    --output-dir "${PORTABLE_TOOL_OUTPUT}" \
    >"${FIXTURE}/portable-tool-driver.log" 2>&1 || {
    sed -n '1,240p' "${FIXTURE}/portable-tool-driver.log" >&2
    fail 'portable PATH-selected Python provenance run failed'
}
[[ -s ${PORTABLE_PYTHON_MARKER} ]] || \
    fail 'portable PATH-selected Python wrapper was not executed'
[[ ! -e ${PORTABLE_GIT_PRECHECK_MARKER} ]] || \
    fail 'portable test-run provenance used the old ambient Git status precheck'
[[ -f ${PORTABLE_TOOL_OUTPUT}/test-run-provenance.json ]] || \
    fail 'portable PATH-selected Python run omitted finalized provenance'
/usr/bin/python3 -I -B - \
    "${PORTABLE_TOOL_OUTPUT}/test-run-provenance.json" \
    "${PORTABLE_TOOL_BIN}/python3" \
    "${PORTABLE_TOOL_BIN}/shellcheck" <<'PY'
import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
tools = document.get("executed_tools")
if not isinstance(tools, list):
    raise SystemExit("finalized provenance omits executed_tools")
python_records = [item for item in tools if item.get("name") == "python3"]
if len(python_records) != 1:
    raise SystemExit("finalized provenance lacks one Python execution record")
entrypoint = python_records[0].get("entrypoint")
if not isinstance(entrypoint, dict) or entrypoint.get("requested_path") != sys.argv[2]:
    raise SystemExit("finalized provenance did not retain the PATH-selected Python wrapper")
shellcheck_records = [item for item in tools if item.get("name") == "shellcheck"]
if len(shellcheck_records) != 1:
    raise SystemExit("finalized provenance lacks one ShellCheck execution record")
shellcheck_entrypoint = shellcheck_records[0].get("entrypoint")
if (
    not isinstance(shellcheck_entrypoint, dict)
    or shellcheck_entrypoint.get("requested_path") != sys.argv[3]
):
    raise SystemExit(
        "finalized provenance did not retain the selected ShellCheck entry point"
    )
PY

printf 'PASS: optimization test-driver CLI self-test\n'

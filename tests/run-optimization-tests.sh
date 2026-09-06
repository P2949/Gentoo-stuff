#!/usr/bin/bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_SOURCE=${BASH_SOURCE[0]}
if [[ ${SCRIPT_SOURCE} == */* ]]; then
    SCRIPT_PARENT=${SCRIPT_SOURCE%/*}
else
    SCRIPT_PARENT=.
fi
SCRIPT_DIR=$(cd -- "${SCRIPT_PARENT}" && pwd -P)
readonly SCRIPT_DIR
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd -P)
readonly REPOSITORY_ROOT

MODE=${OPTIMIZATION_TEST_MODE:-smoke}
OUTPUT_DIR=
KEEP_TEMP=0
LIST_ONLY=0
CONTRACT_TOPOLOGY_ONLY=0
INTERNAL_PROCESS_GROUP_PROBE=
INTERNAL_PROCESS_GROUP_PROC_ROOT=/proc
INTERNAL_PROCESS_GROUP_ASSUME_VISIBLE=0
EXPLICIT_CAPABILITIES=0
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
RUN_ROOT=
LOG_ROOT=
RESULTS_FILE=
PREFLIGHT_REASON=
RESOLVED_TOOL=
TEMP_ROOT_CREATED=0
PRESERVE_RUN_ROOT_FOR_EXTERNAL_AUTHORITY=0
TIMEOUT_BIN=
SETSID_BIN=
SLEEP_BIN=
ENV_BIN=
BASH_BIN=
PYTHON_BIN=
GIT_BIN=
SHELLCHECK_BIN=
TEST_EXECUTION_POLICY=${REPOSITORY_ROOT}/optimization/phase2-evidence-policy.json
TEST_EXECUTION_TOOL_MANIFEST=${REPOSITORY_ROOT}/optimization/phase2-tool-manifest.json
REVIEWED_AUTHORITATIVE_PATH=
declare -a TEST_EXECUTION_TOOL_NAMES=()
declare -A TEST_EXECUTION_TOOL_PATHS=()
declare -a AUTHORITATIVE_PATH_TOOL_NAMES=()
declare -A AUTHORITATIVE_PATH_TOOL_PATHS=()
ACTIVE_CASE_PGID=
ACTIVE_CASE_NAME=
ACTIVE_CASE_KILL_AFTER_SECONDS=
RESOLVED_CASE_TIMEOUT_SECONDS=
RESOLVED_CASE_KILL_AFTER_SECONDS=
PROCESS_GROUP_WAS_ACTIVE=0
PROCESS_GROUP_SURVIVED=0
SUBTESTS_FILE=
SUBTEST_FRAGMENT_ROOT=
REQUIRED_SUBTEST_PASS_COUNT=0
REQUIRED_SUBTEST_FAIL_COUNT=0
REQUIRED_SUBTEST_SKIP_COUNT=0
MANDATORY_INTERNAL_SKIP_COUNT=0
DIAGNOSTIC_SUBTEST_PASS_COUNT=0
DIAGNOSTIC_SUBTEST_FAIL_COUNT=0
DIAGNOSTIC_SUBTEST_SKIP_COUNT=0

AUTHORITATIVE=${GENTOO_OPT_AUTHORITATIVE:-0}

TEST_CASE_TIMEOUT_SECONDS=${TEST_CASE_TIMEOUT_SECONDS:-1800}
TEST_CASE_KILL_AFTER_SECONDS=${TEST_CASE_KILL_AFTER_SECONDS:-10}
CHECKPOINT_SMOKE_TIMEOUT_SECONDS=${CHECKPOINT_SMOKE_TIMEOUT_SECONDS:-600}
CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS=${CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS:-90}
RECOVERY_SUITE_TIMEOUT_SECONDS=${RECOVERY_SUITE_TIMEOUT_SECONDS:-2700}

declare -A SELECTED_CAPABILITIES=()
readonly -a ALL_CAPABILITIES=(clang-ir clang-sample gcc rust go bolt)
readonly -a CHECKPOINT_SMOKE_IDENTITIES=(
    test_create_binpkg_checkpoint.CheckpointHarnessTest.test_forced_supervisor_deadline_freezes_and_drains_private_escape
    test_create_binpkg_checkpoint.CheckpointHarnessTest.test_interruption_before_fork_or_release_commitment_never_executes_target
    test_create_binpkg_checkpoint.CheckpointHarnessTest.test_subreaper_catches_fast_setsid_escape_without_touching_baseline_child
    test_create_binpkg_checkpoint.CheckpointHarnessTest.test_timeout_kills_detached_descendant_without_waiting_for_pipe_eof
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_fake_unshare_terminal_commit_keeps_late_signal_and_receipt_consistent
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_fake_unshare_unblocks_inherited_handled_signals
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_fake_unshare_watchdog_drains_normal_same_session_background_group
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_fake_unshare_watchdog_failure_dominates_sigterm_status
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_fake_unshare_watchdog_pre_drain_failure_is_bounded_and_non_sigterm
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_fake_unshare_watchdog_rescans_late_same_session_group_after_sigterm
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_fake_unshare_watchdog_stall_after_child_then_late_signal_is_bounded
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_fake_unshare_watchdog_stall_after_short_child_uses_original_deadline
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_fake_unshare_watchdog_stall_is_bounded_and_non_sigterm
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_near_exchange_lost_update_is_atomically_captured_and_restored
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_offline_restore_finalizer_binds_all_evidence_and_is_idempotent
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_sigkill_inside_exchange_is_reconciled_without_guessing
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_signal_terminates_active_process_group_and_preserves_selector_inode
    test_create_binpkg_checkpoint.CreateBinpkgCheckpointTest.test_success_is_exact_journaled_and_activates_last
)
readonly -a EXPLICIT_SHELL_SOURCES=(
    "${REPOSITORY_ROOT}/optimization/fixtures/portage/capture-proxy.sh.in"
    "${REPOSITORY_ROOT}/optimization/fixtures/portage/phase2-phase-identity-1.ebuild"
    "${REPOSITORY_ROOT}/optimization/fixtures/portage/phase2-phase-identity-install-qa"
    "${REPOSITORY_ROOT}/portage/bashrc"
    "${REPOSITORY_ROOT}/portage/install-qa-check.d/zz-gentoo-optimization-bolt"
    "${REPOSITORY_ROOT}/portage/repo.postsync.d/fix-sft-broken"
)

print_block() {
    local line
    while IFS= read -r line || [[ -n ${line} ]]; do
        printf '%s\n' "${line}"
    done
}

usage() {
    print_block <<'EOF'
Usage: tests/run-optimization-tests.sh [OPTIONS]

Run the repository's non-mutating optimization validation suites.

Modes:
  --mode smoke              Short static/core gate (default).
  --mode checkpoint-smoke   Static gate plus 18 exact checkpoint methods: four
                            supervisor containment/release paths, nine portable
                            fake-unshare terminal/watchdog paths, and five
                            state-machine/process-group paths.
  --mode portable-complete  All portable non-capability, non-stress fixtures.
  --mode stress             Portable-complete plus the 300-cycle crash stress.
  --mode capabilities       Portable-complete plus every capability fixture.
  --mode authoritative      Complete host gate: portable, stress, all
                            capabilities, and fail-closed subtest accounting.
  --mode quick              Deprecated alias for portable-complete.

Options:
  --capability NAME     Run only this capability fixture in addition to the mode's
                        suites. Repeat as needed; NAME is clang-ir, clang-sample,
                        gcc, rust, go, bolt, or all. An explicit filter narrows
                        capabilities and authoritative modes as well.
  --output-dir DIR      Keep logs/evidence in a new absolute directory below
                        /tmp or /var/tmp/gentoo-optimization. Root runs require
                        the latter trusted tree. Its canonical path may contain
                        only letters, digits, /, ., _, and -.
  --keep-temp           Keep the automatically allocated temporary directory.
  --list                List suites and capability names without running them.
  --contract-topology   Emit the machine-readable portable/authoritative test
                        topology without executing tests, then exit.
  -h, --help            Show this help.

Environment:
  OPTIMIZATION_TEST_MODE=smoke|checkpoint-smoke|portable-complete|stress|capabilities|authoritative
  OPTIMIZATION_TEST_CAPABILITIES=comma,separated,names
  SHELLCHECK=/path/to/shellcheck (authoritative mode requires the reviewed path)
  TEST_CASE_TIMEOUT_SECONDS=1800
  TEST_CASE_KILL_AFTER_SECONDS=10
  CHECKPOINT_SMOKE_TIMEOUT_SECONDS=600
  CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS=90
  RECOVERY_SUITE_TIMEOUT_SECONDS=2700
  GENTOO_OPT_AUTHORITATIVE=0|1

Each case receives a private GENTOO_OPT_SUBTEST_RESULTS fragment path.  A
fixture may append four tab-separated fields: status (PASS|FAIL|SKIP),
requirement (required|diagnostic), subtest name, and a one-line detail.  The
driver converts legacy shell SKIP-SUBTEST markers into required rows, while the
structured unittest runner publishes every Python test outcome directly.
Authoritative mode fails on every required internal skip and every internal
failure.

Per-capability deadlines override the global values by appending the normalized
capability name, for example TEST_CASE_TIMEOUT_SECONDS_CLANG_IR or
TEST_CASE_KILL_AFTER_SECONDS_BOLT. Values are positive integer seconds.
The complete recovery unittest case has the separately reviewed
RECOVERY_SUITE_TIMEOUT_SECONDS deadline; checkpoint-smoke retains its own
shorter CHECKPOINT_SMOKE_TIMEOUT_SECONDS deadline and executes every selected
identity in a fresh Python process bounded by
CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS.

Fixture-specific tool and iteration overrides (for example CLANGXX,
LLVM_PROFDATA, CLANG_SAMPLE_ITERATIONS, RUST_PGO_ITERATIONS,
GO_PGO_ITERATIONS, and BOLT_FIXTURE_TRAIN_ITERATIONS) are passed through.

The default smoke mode never invokes perf or a PGO/BOLT training workload.
All writes stay in a private test directory. Recovery tests use fake roots and
mocked package tools; capability fixtures only build/profile local fixtures.
An individual fixture exit status of 77 is recorded as an explicit reason-bearing
SKIP rather than as a pass or failure.
EOF
}

list_suites() {
    print_block <<'EOF'
portable suites (smoke runs only the initial static/core subset):
  bash-syntax (every .sh below bench/, optimization/, scripts/, and tests/)
  shellcheck (same shell source set; skipped when unavailable)
  python-source-compilation (temporary pycache only)
  python-unit-tests
  checkpoint-smoke (18 exact methods: four supervisor containment/release,
                    nine portable fake-unshare terminal/watchdog, and five
                    checkpoint state-machine/process-group paths; its own mode only)
  phase2-test-contract-static (deterministic no-execution pre-gate topology check)
  phase2-evidence-smoke (one parser/topology regression; smoke only)
  phase2-evidence-contract (clean-tree, plan-marker, tool, state, and detached-index binding)
  production-profile-lock-crash-stress (stress/authoritative only; 300 cycles)
  package-env-duplicate-policy (portable checks plus required live Portage semantics on Gentoo)
  package-env-portage-semantic (explicit SKIP when the live Portage universe is unavailable)
  portage-config-cleanup (reviewed package.mask and shared O3 baseline)
  framework-installer (hermetic snapshot, trust, transaction, and rollback gate)
  no-legacy-bolt (retired identity-free BOLT helpers remain unusable)
  no-legacy-pgo (retired weak paths/helpers remain unusable)
  pgo-dispatcher (strict backend/ABI/fingerprint and stage-hook fixture)
  portage-qa-hook-state (lost/mismatched active state and marker invalidation)
  portage-pre-strip-integration (real disposable ebuild; root-only)
  portage-phase-identity (live userpriv/install/QA privilege boundary; root-only)
  portage-pgo-use-integration (real Clang generation/use/sidecar gate; root-only)
  portage-sample-pgo-integration (isolated diagnostic mapping/perf/sample lane;
                                  root-only and opt-in with clang-sample)
  portage-sample-pgo-live-policy-integration (the same complete pipeline with
                                  resolved live sandbox/userpriv/PID/network/IPC
                                  policy; root-only and opt-in with clang-sample)
  bolt-command-policy (exact production/capability BOLT layout policy)
  bolt-transaction-fixture (hermetic timeout/publication/interruption paths)
  bolt-pre-strip-hooks (hermetic capture/register/deploy and rollback fixture)
  driver-cli-self-test
  no-boot-entry-automation (permanent firmware/kernel authority prohibition)
  recovery-rollback-fixture (fake root, including Clang/libc++ and GCC/libstdc++)

opt-in capability fixtures:
  clang-ir       Clang IR instrumentation PGO executable/DSO
  clang-sample   perf/llvm-profgen sample PGO
  gcc            GCC gcov PGO executable/DSO
  rust           Rust LLVM instrumentation PGO
  go             Go CPU-pprof PGO
  bolt           BOLT ET_EXEC, dynamic PIE, static PIE, and DSO classes
EOF
}

validate_explicit_shell_sources() {
    local source
    for source in "${EXPLICIT_SHELL_SOURCES[@]}"; do
        if [[ ! -e ${source} && ! -L ${source} ]]; then
            # Hermetic driver fixtures intentionally contain only their
            # minimal synthetic source tree.  The real repository policy test
            # requires every reviewed explicit source to exist.
            continue
        fi
        [[ -f ${source} && ! -L ${source} ]] || \
            fail_usage "reviewed shell source is absent or a symlink: ${source}"
    done
}

discover_shell_sources() {
    {
        local source
        for source in "${EXPLICIT_SHELL_SOURCES[@]}"; do
            [[ -f ${source} && ! -L ${source} ]] || continue
            printf '%s\0' "${source}"
        done
        find "${REPOSITORY_ROOT}/bench" "${REPOSITORY_ROOT}/optimization" \
            "${REPOSITORY_ROOT}/scripts" "${REPOSITORY_ROOT}/tests" \
            -type f -name '*.sh' -print0
    } | LC_ALL=C sort -zu
}

emit_contract_topology() {
    local name source_file test_file test_directory relative_directory exclusion i j swap
    local LC_ALL=C
    validate_explicit_shell_sources
    local -a exact_names=(
        bolt-command-policy
        bolt-pre-strip-hooks
        bolt-transaction-fixture
        capability:bolt
        capability:clang-ir
        capability:clang-sample
        capability:gcc
        capability:go
        capability:rust
        driver-cli-self-test
        framework-installer
        no-legacy-bolt
        no-legacy-pgo
        package-env-duplicate-policy
        package-env-portage-semantic
        pgo-dispatcher
        phase2-evidence-contract
        phase2-run-provenance-start
        phase2-test-contract-static
        portage-config-cleanup
        portage-pgo-use-integration
        portage-phase-identity
        portage-pre-strip-integration
        portage-qa-hook-state
        portage-sample-pgo-integration
        portage-sample-pgo-live-policy-integration
        portage-sample-production-env
        production-profile-lock-crash-stress
        python-source-compilation
        no-boot-entry-automation
        recovery-rollback-fixture
        shellcheck
    )
    local -a test_directories=()
    local -A seen_test_directories=()
    for name in "${exact_names[@]}"; do
        printf 'top-level\t%s\n' "${name}"
    done
    while IFS= read -r -d '' source_file; do
        relative_directory=${source_file#"${REPOSITORY_ROOT}/"}
        printf 'shell\tbash-syntax:%s\n' "${relative_directory}"
    done < <(discover_shell_sources)
    shopt -s globstar nullglob
    for test_file in "${REPOSITORY_ROOT}"/tests/**/test_*.py; do
        [[ -f ${test_file} && ! -L ${test_file} ]] || continue
        test_directory=${test_file%/*}
        if [[ -z ${seen_test_directories["${test_directory}"]:-} ]]; then
            seen_test_directories["${test_directory}"]=1
            test_directories+=("${test_directory}")
        fi
    done
    ((${#test_directories[@]} > 0)) || \
        fail_usage 'contract topology discovered no Python unittest directories'
    for ((i = 0; i < ${#test_directories[@]}; i += 1)); do
        for ((j = i + 1; j < ${#test_directories[@]}; j += 1)); do
            if [[ ${test_directories[j]} < ${test_directories[i]} ]]; then
                swap=${test_directories[i]}
                test_directories[i]=${test_directories[j]}
                test_directories[j]=${swap}
            fi
        done
    done
    for test_directory in "${test_directories[@]}"; do
        relative_directory=${test_directory#"${REPOSITORY_ROOT}/"}
        name=python-unit-tests:${relative_directory}
        printf 'top-level\t%s\n' "${name}"
        exclusion=
        if [[ ${relative_directory} == tests/optimization ]]; then
            exclusion=test_phase2_evidence.
        fi
        printf 'unittest\t%s\t%s\t%s\t%s\t%s\n' \
            "${name}" "${relative_directory}" 'test_*.py' "${exclusion}" ''
    done
    printf 'unittest\t%s\t%s\t%s\t%s\t%s\n' \
        phase2-evidence-contract tests/optimization test_phase2_evidence.py '' ''
    printf 'unittest\t%s\t%s\t%s\t%s\t%s\n' \
        production-profile-lock-crash-stress tests/optimization \
        test_production_profile_lock_transaction.py '' \
        test_child_barrier_crash_recovery_stress
}

fail_usage() {
    printf 'ERROR: %s\n\n' "$*" >&2
    usage >&2
    exit 2
}

is_capability() {
    local requested=$1
    local capability
    for capability in "${ALL_CAPABILITIES[@]}"; do
        [[ ${requested} == "${capability}" ]] && return 0
    done
    return 1
}

select_capability() {
    local requested=$1
    local capability
    if [[ ${requested} == all ]]; then
        for capability in "${ALL_CAPABILITIES[@]}"; do
            SELECTED_CAPABILITIES["${capability}"]=1
        done
    elif is_capability "${requested}"; then
        SELECTED_CAPABILITIES["${requested}"]=1
    else
        fail_usage "unknown capability: ${requested}"
    fi
    EXPLICIT_CAPABILITIES=1
}

parse_capability_list() {
    local value=$1
    local -a requested_capabilities=()
    local capability
    [[ -n ${value} ]] || return 0
    IFS=',' read -r -a requested_capabilities <<<"${value}"
    for capability in "${requested_capabilities[@]}"; do
        [[ -n ${capability} ]] || fail_usage 'empty capability in capability list'
        select_capability "${capability}"
    done
}

parse_capability_list "${OPTIMIZATION_TEST_CAPABILITIES:-}"
[[ ${AUTHORITATIVE} == 0 || ${AUTHORITATIVE} == 1 ]] || \
    fail_usage 'GENTOO_OPT_AUTHORITATIVE must be exactly 0 or 1'
while (($#)); do
    case $1 in
        --mode)
            (($# >= 2)) || fail_usage '--mode requires an argument'
            MODE=$2
            shift 2
            ;;
        --capability)
            (($# >= 2)) || fail_usage '--capability requires an argument'
            select_capability "$2"
            shift 2
            ;;
        --output-dir)
            (($# >= 2)) || fail_usage '--output-dir requires an argument'
            OUTPUT_DIR=$2
            shift 2
            ;;
        --keep-temp)
            KEEP_TEMP=1
            shift
            ;;
        --list)
            LIST_ONLY=1
            shift
            ;;
        --contract-topology)
            CONTRACT_TOPOLOGY_ONLY=1
            shift
            ;;
        --internal-process-group-probe)
            (($# >= 2)) || fail_usage '--internal-process-group-probe requires a PGID'
            INTERNAL_PROCESS_GROUP_PROBE=$2
            shift 2
            ;;
        --internal-process-group-proc-root)
            (($# >= 2)) || fail_usage '--internal-process-group-proc-root requires a directory'
            INTERNAL_PROCESS_GROUP_PROC_ROOT=$2
            shift 2
            ;;
        --internal-process-group-assume-visible)
            INTERNAL_PROCESS_GROUP_ASSUME_VISIBLE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            (($# == 0)) || fail_usage "unexpected argument: $1"
            ;;
        *)
            fail_usage "unknown option: $1"
            ;;
    esac
done

case ${MODE} in
    quick)
        printf '%s\n' \
            'WARNING: --mode quick is deprecated; using portable-complete' >&2
        MODE=portable-complete
        ;;
    smoke|checkpoint-smoke|portable-complete|stress|capabilities|authoritative) ;;
    *) fail_usage "unknown mode: ${MODE}" ;;
esac

if [[ ${MODE} == authoritative ]]; then
    AUTHORITATIVE=1
fi

validate_explicit_shell_sources

validate_positive_seconds() {
    local variable_name=$1 value=$2
    [[ ${value} =~ ^[1-9][0-9]*$ ]] || \
        fail_usage "${variable_name} must be a positive integer number of seconds"
}

validate_positive_seconds TEST_CASE_TIMEOUT_SECONDS "${TEST_CASE_TIMEOUT_SECONDS}"
validate_positive_seconds TEST_CASE_KILL_AFTER_SECONDS "${TEST_CASE_KILL_AFTER_SECONDS}"
validate_positive_seconds CHECKPOINT_SMOKE_TIMEOUT_SECONDS \
    "${CHECKPOINT_SMOKE_TIMEOUT_SECONDS}"
validate_positive_seconds CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS \
    "${CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS}"
validate_positive_seconds RECOVERY_SUITE_TIMEOUT_SECONDS \
    "${RECOVERY_SUITE_TIMEOUT_SECONDS}"
for capability in "${ALL_CAPABILITIES[@]}"; do
    capability_suffix=${capability^^}
    capability_suffix=${capability_suffix//-/_}
    timeout_override_name=TEST_CASE_TIMEOUT_SECONDS_${capability_suffix}
    kill_after_override_name=TEST_CASE_KILL_AFTER_SECONDS_${capability_suffix}
    if [[ -v ${timeout_override_name} ]]; then
        validate_positive_seconds "${timeout_override_name}" \
            "${!timeout_override_name}"
    fi
    if [[ -v ${kill_after_override_name} ]]; then
        validate_positive_seconds "${kill_after_override_name}" \
            "${!kill_after_override_name}"
    fi
done

if [[ ( ${MODE} == capabilities || ${MODE} == authoritative ) &&
      ${EXPLICIT_CAPABILITIES} -eq 0 ]]; then
    for capability in "${ALL_CAPABILITIES[@]}"; do
        SELECTED_CAPABILITIES["${capability}"]=1
    done
fi

resolve_executable() {
    local requested_tool=$1
    local resolved
    if [[ ${requested_tool} == */* ]]; then
        [[ -x ${requested_tool} && ! -d ${requested_tool} ]] || return 1
        resolved=${requested_tool}
    else
        resolved=$(command -v -- "${requested_tool}" 2>/dev/null) || return 1
        [[ -x ${resolved} && ! -d ${resolved} ]] || return 1
    fi
    RESOLVED_TOOL=${resolved}
}

load_test_execution_tool_contract() {
    local python_bin=$1 rows name path index
    local -a expected=(bash env git python3 setsid shellcheck sleep timeout)
    [[ -f ${TEST_EXECUTION_POLICY} && ! -L ${TEST_EXECUTION_POLICY} ]] ||
        fail_usage "test-execution policy is absent or symlinked: ${TEST_EXECUTION_POLICY}"
    [[ -f ${TEST_EXECUTION_TOOL_MANIFEST} &&
       ! -L ${TEST_EXECUTION_TOOL_MANIFEST} ]] ||
        fail_usage "test-execution tool manifest is absent or symlinked: ${TEST_EXECUTION_TOOL_MANIFEST}"
    rows=$("${python_bin}" -I -B -c '
import json
import pathlib
import sys

policy_path = pathlib.Path(sys.argv[1])
manifest_path = pathlib.Path(sys.argv[2])
expected = ["bash", "env", "git", "python3", "setsid", "shellcheck", "sleep", "timeout"]
try:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise SystemExit(f"cannot read test-execution policy or tool manifest: {error}")
names = policy.get("test_execution_tools")
if names != expected:
    raise SystemExit(
        f"test_execution_tools must exactly equal the reviewed execution core: {expected!r}"
    )
authoritative_path = policy.get("authoritative_test_path")
if (
    not isinstance(authoritative_path, list)
    or not authoritative_path
    or len(authoritative_path) != len(set(authoritative_path))
    or any(
        not isinstance(path, str)
        or not path.startswith("/")
        or ":" in path
        or "\t" in path
        or "\n" in path
        for path in authoritative_path
    )
):
    raise SystemExit("authoritative_test_path must be unique safe absolute paths")
tools = manifest.get("tools")
if not isinstance(tools, list):
    raise SystemExit("tool manifest tools must be a list")
by_name = {}
for item in tools:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise SystemExit("tool manifest contains an invalid tool entry")
    name = item["name"]
    if name in by_name:
        raise SystemExit(f"tool manifest repeats tool name: {name}")
    by_name[name] = item
for name in names:
    item = by_name.get(name)
    if item is None:
        raise SystemExit(f"tool manifest omits test-execution tool: {name}")
    path = item.get("path")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or "\t" in path
        or "\n" in path
    ):
        raise SystemExit(f"tool manifest has an invalid path for {name}")
print("@authoritative-path\t" + ":".join(authoritative_path))
for name in names:
    print(name + "\t" + str(by_name[name].get("path")))
for name, item in sorted(by_name.items()):
    path = str(item.get("path"))
    if pathlib.PurePosixPath(path).name == name:
        print("@path-tool:" + name + "\t" + path)
' "${TEST_EXECUTION_POLICY}" "${TEST_EXECUTION_TOOL_MANIFEST}") ||
        fail_usage 'cannot load the reviewed test-execution tool contract'

    TEST_EXECUTION_TOOL_NAMES=()
    TEST_EXECUTION_TOOL_PATHS=()
    AUTHORITATIVE_PATH_TOOL_NAMES=()
    AUTHORITATIVE_PATH_TOOL_PATHS=()
    REVIEWED_AUTHORITATIVE_PATH=
    while IFS=$'\t' read -r name path; do
        [[ -n ${name} && -n ${path} ]] ||
            fail_usage 'test-execution tool contract emitted an empty row'
        if [[ ${name} == @authoritative-path ]]; then
            [[ -z ${REVIEWED_AUTHORITATIVE_PATH} ]] ||
                fail_usage 'test-execution tool contract repeated its authoritative PATH'
            REVIEWED_AUTHORITATIVE_PATH=${path}
            continue
        fi
        if [[ ${name} == @path-tool:* ]]; then
            name=${name#@path-tool:}
            [[ ! -v AUTHORITATIVE_PATH_TOOL_PATHS["${name}"] ]] ||
                fail_usage "test-execution tool contract repeated PATH tool ${name}"
            AUTHORITATIVE_PATH_TOOL_NAMES+=("${name}")
            AUTHORITATIVE_PATH_TOOL_PATHS["${name}"]=${path}
            continue
        fi
        [[ ! -v TEST_EXECUTION_TOOL_PATHS["${name}"] ]] ||
            fail_usage "test-execution tool contract repeated ${name}"
        TEST_EXECUTION_TOOL_NAMES+=("${name}")
        TEST_EXECUTION_TOOL_PATHS["${name}"]=${path}
    done <<<"${rows}"
    [[ -n ${REVIEWED_AUTHORITATIVE_PATH} ]] ||
        fail_usage 'test-execution tool contract omitted its authoritative PATH'
    ((${#TEST_EXECUTION_TOOL_NAMES[@]} == ${#expected[@]})) ||
        fail_usage 'test-execution tool contract emitted the wrong name count'
    for ((index = 0; index < ${#expected[@]}; index += 1)); do
        [[ ${TEST_EXECUTION_TOOL_NAMES[index]} == "${expected[index]}" ]] ||
            fail_usage 'test-execution tool contract emitted the wrong ordered names'
    done
}

validate_authoritative_manifest_path_tools() {
    local name expected selected
    ((AUTHORITATIVE == 1)) || return 0
    for name in "${AUTHORITATIVE_PATH_TOOL_NAMES[@]}"; do
        expected=${AUTHORITATIVE_PATH_TOOL_PATHS["${name}"]}
        selected=$(type -P -- "${name}" 2>/dev/null) ||
            fail_usage "reviewed authoritative PATH tool is unavailable: ${name}=${expected}"
        [[ ${selected} == "${expected}" ]] ||
            fail_usage "authoritative PATH shadows reviewed ${name}: expected=${expected} selected=${selected}"
    done
    [[ ${PATH} == "${REVIEWED_AUTHORITATIVE_PATH}" ]] ||
        fail_usage "authoritative PATH differs from the reviewed execution path: expected=${REVIEWED_AUTHORITATIVE_PATH} actual=${PATH}"
}

bind_test_execution_tool() {
    local name=$1 selection=${2:-$1} requested selected selected_directory selected_name
    requested=${TEST_EXECUTION_TOOL_PATHS["${name}"]}
    if [[ ${selection} == */* ]]; then
        [[ -x ${selection} && ! -d ${selection} ]] || return 1
        selected=${selection}
    else
        selected=$(command -v -- "${selection}" 2>/dev/null) || return 1
    fi
    [[ -x ${selected} && ! -d ${selected} ]] || return 1
    if [[ ${selected} != /* ]]; then
        selected_name=${selected##*/}
        if [[ ${selected} == */* ]]; then
            selected_directory=${selected%/*}
        else
            selected_directory=.
        fi
        selected_directory=$(cd -- "${selected_directory}" && pwd -P) ||
            return 1
        selected=${selected_directory}/${selected_name}
    fi
    if ((AUTHORITATIVE == 1)); then
        [[ ${requested} == /* && -x ${requested} && ! -d ${requested} ]] ||
            fail_usage "reviewed test-execution tool is unavailable: ${name}=${requested}"
        if [[ ${name} == shellcheck && ${selected} != "${requested}" ]]; then
            fail_usage "authoritative ShellCheck entry point differs from the reviewed manifest: expected=${requested} selected=${selected}"
        fi
        [[ ${selected} -ef ${requested} ]] ||
            fail_usage "authoritative PATH shadows reviewed ${name}: expected=${requested} selected=${selected}"
        # Preserve the reviewed entry point instead of its canonical target.
        # Git and Python are intentionally argv-zero-sensitive symlinks on the
        # Gentoo host, and the evidence contract distinguishes requested from
        # resolved identity.
        selected=${requested}
    fi
    TEST_EXECUTION_TOOL_PATHS["${name}"]=${selected}
    RESOLVED_TOOL=${selected}
}

# `/usr/bin/python3` is the reviewed bootstrap used to read the tracked tool
# contract in authoritative mode.  Portable runs may use another PATH-selected
# Python, but record that actual entry point in test-run provenance.
if ((AUTHORITATIVE == 1)); then
    [[ -x /usr/bin/python3 && ! -d /usr/bin/python3 ]] ||
        fail_usage 'reviewed Python bootstrap is unavailable: /usr/bin/python3'
    TOOL_CONTRACT_PYTHON=/usr/bin/python3
else
    TOOL_CONTRACT_PYTHON=$(command -v -- python3 2>/dev/null || true)
fi
if [[ -n ${TOOL_CONTRACT_PYTHON} ]]; then
    load_test_execution_tool_contract "${TOOL_CONTRACT_PYTHON}"
else
    TEST_EXECUTION_TOOL_NAMES=(bash env git python3 setsid shellcheck sleep timeout)
    for execution_tool_name in "${TEST_EXECUTION_TOOL_NAMES[@]}"; do
        TEST_EXECUTION_TOOL_PATHS["${execution_tool_name}"]=
    done
fi

validate_authoritative_manifest_path_tools

bind_test_execution_tool bash || fail_usage 'bash is required for shell validation'
BASH_BIN=${RESOLVED_TOOL}
bind_test_execution_tool timeout || fail_usage 'GNU timeout is required for per-case deadlines'
TIMEOUT_BIN=${RESOLVED_TOOL}
timeout_help=$("${TIMEOUT_BIN}" --help 2>&1) || \
    fail_usage 'timeout --help failed while checking per-case deadline support'
[[ ${timeout_help} == *'--kill-after'* && ${timeout_help} == *'--foreground'* ]] || \
    fail_usage 'timeout lacks the required --kill-after/--foreground support'
bind_test_execution_tool setsid || fail_usage 'setsid is required for per-case process isolation'
SETSID_BIN=${RESOLVED_TOOL}
setsid_help=$("${SETSID_BIN}" --help 2>&1) || \
    fail_usage 'setsid --help failed while checking process-isolation support'
[[ ${setsid_help} == *'--wait'* ]] || \
    fail_usage 'setsid lacks the required --wait support'
bind_test_execution_tool sleep || fail_usage 'sleep is required for process-group cleanup'
SLEEP_BIN=${RESOLVED_TOOL}
bind_test_execution_tool env || fail_usage 'GNU env is required for child signal normalization'
ENV_BIN=${RESOLVED_TOOL}
env_help=$("${ENV_BIN}" --help 2>&1) || \
    fail_usage 'env --help failed while checking child signal-normalization support'
[[ ${env_help} == *'--default-signal'* ]] || \
    fail_usage 'env lacks the required --default-signal support'
if bind_test_execution_tool git; then
    GIT_BIN=${RESOLVED_TOOL}
elif ((AUTHORITATIVE == 1)); then
    fail_usage 'reviewed Git is required for authoritative test-run provenance'
fi
if bind_test_execution_tool python3; then
    PYTHON_BIN=${RESOLVED_TOOL}
elif ((AUTHORITATIVE == 1)); then
    fail_usage 'reviewed Python is required for authoritative test execution'
fi
if bind_test_execution_tool shellcheck "${SHELLCHECK:-shellcheck}"; then
    SHELLCHECK_BIN=${RESOLVED_TOOL}
elif ((AUTHORITATIVE == 1)); then
    fail_usage 'reviewed ShellCheck is required for authoritative shell validation'
else
    TEST_EXECUTION_TOOL_PATHS["shellcheck"]=
fi

if ((AUTHORITATIVE == 1)); then
    [[ ${PATH} == "${REVIEWED_AUTHORITATIVE_PATH}" ]] ||
        fail_usage "authoritative PATH differs from the reviewed execution path: expected=${REVIEWED_AUTHORITATIVE_PATH} actual=${PATH}"
    [[ /proc/${BASHPID}/exe -ef ${BASH_BIN} ]] ||
        fail_usage "authoritative driver is not running under reviewed Bash: ${BASH_BIN}"
    # Bash normalizes argv[0] to `bash` when invoked as an absolute path.
    # The reviewed launcher supplies BASH_ARGV0 explicitly so the identity
    # remains observable without relying on that normalization detail.
    ACTIVE_BASH_ARGV0=${GENTOO_OPT_REVIEWED_BASH_ARGV0:-${BASH_ARGV0:-}}
    [[ -n ${ACTIVE_BASH_ARGV0} ]] ||
        fail_usage 'cannot read the authoritative driver Bash argv-zero identity'
    [[ ${ACTIVE_BASH_ARGV0} == "${BASH_BIN}" ]] ||
        fail_usage "authoritative driver Bash argv-zero differs from the reviewed entry point: expected=${BASH_BIN} actual=${ACTIVE_BASH_ARGV0}"
fi

if ((LIST_ONLY)); then
    list_suites
    exit 0
fi
if ((CONTRACT_TOPOLOGY_ONLY)); then
    emit_contract_topology
    exit 0
fi

require_commands() {
    local -a missing=()
    local command_name
    for command_name in "$@"; do
        if ! resolve_executable "${command_name}"; then
            missing+=("${command_name}")
        fi
    done
    if ((${#missing[@]})); then
        local joined
        joined=$(IFS=', '; printf '%s' "${missing[*]}")
        PREFLIGHT_REASON="missing required command(s): ${joined}"
        return 1
    fi
    return 0
}

create_run_root() {
    local canonical parent relative component current mode
    local trusted_root=/var/tmp/gentoo-optimization
    if [[ -n ${OUTPUT_DIR} ]]; then
        [[ ${OUTPUT_DIR} == /* && ${OUTPUT_DIR} != / ]] || \
            fail_usage '--output-dir must be an absolute non-root path'
        resolve_executable realpath || fail_usage 'realpath is required for --output-dir'
        canonical=$(${RESOLVED_TOOL} -m -- "${OUTPUT_DIR}")
        if ((EUID == 0)); then
            [[ ${canonical} == "${trusted_root}"/* ]] || \
                fail_usage 'root --output-dir must remain below /var/tmp/gentoo-optimization'
            [[ -d /var/tmp && ! -L /var/tmp && \
                $(realpath -e -- /var/tmp) == /var/tmp && \
                $(stat -c %u -- /var/tmp) == 0 ]] || \
                fail_usage '/var/tmp is not the canonical root-owned output boundary'
            mode=$(stat -c %a -- /var/tmp)
            (( (8#${mode} & 8#1000) != 0 )) || \
                fail_usage '/var/tmp output boundary lacks the sticky bit'
            parent=${canonical%/*}
            current=${trusted_root}
            relative=${parent#"${trusted_root}"}
            relative=${relative#/}
            IFS=/ read -r -a output_ancestor_components <<< "${relative}"
            for component in '' "${output_ancestor_components[@]}"; do
                [[ -z ${component} ]] || current+=/${component}
                [[ -d ${current} && ! -L ${current} && \
                    $(realpath -e -- "${current}") == "${current}" && \
                    $(stat -c %u -- "${current}") == "${EUID}" ]] || \
                    fail_usage "untrusted root output ancestor: ${current}"
                mode=$(stat -c %a -- "${current}")
                (( (8#${mode} & 8#022) == 0 )) || \
                    fail_usage "group/world-writable root output ancestor: ${current}"
            done
        else
            case ${canonical} in
                /tmp/*|/var/tmp/gentoo-optimization/*) ;;
                *) fail_usage '--output-dir must remain below /tmp or /var/tmp/gentoo-optimization' ;;
            esac
            parent=${canonical%/*}
            [[ -d ${parent} && ! -L ${parent} ]] || \
                fail_usage '--output-dir parent must already exist as a real directory'
        fi
        [[ ${canonical} =~ ^/[A-Za-z0-9_./-]+$ ]] || \
            fail_usage '--output-dir canonical path contains characters unsafe for capability workloads'
        [[ ! -e ${canonical} ]] || fail_usage "--output-dir already exists: ${canonical}"
        mkdir -m 0700 -- "${canonical}"
        RUN_ROOT=${canonical}
    else
        if ((EUID == 0)); then
            [[ -d /var/tmp && ! -L /var/tmp && \
                $(realpath -e -- /var/tmp) == /var/tmp && \
                $(stat -c %u -- /var/tmp) == 0 ]] || \
                fail_usage '/var/tmp is not the canonical root-owned output boundary'
            mode=$(stat -c %a -- /var/tmp)
            (( (8#${mode} & 8#1000) != 0 )) || \
                fail_usage '/var/tmp output boundary lacks the sticky bit'
            [[ -d ${trusted_root} && ! -L ${trusted_root} && \
                $(realpath -e -- "${trusted_root}") == "${trusted_root}" && \
                $(stat -c %u -- "${trusted_root}") == "${EUID}" ]] || \
                fail_usage "trusted root output tree is unavailable: ${trusted_root}"
            mode=$(stat -c %a -- "${trusted_root}")
            (( (8#${mode} & 8#022) == 0 )) || \
                fail_usage "trusted root output tree is group/world-writable: ${trusted_root}"
            RUN_ROOT=$(mktemp -d \
                "${trusted_root}/optimization-tests.XXXXXXXX")
        else
            RUN_ROOT=$(mktemp -d /tmp/gentoo-optimization-tests.XXXXXXXX)
        fi
        TEMP_ROOT_CREATED=1
    fi
    [[ -d ${RUN_ROOT} && ! -L ${RUN_ROOT} && \
        $(realpath -e -- "${RUN_ROOT}") == "${RUN_ROOT}" && \
        $(stat -c '%u:%a' -- "${RUN_ROOT}") == "${EUID}:700" ]] || \
        fail_usage "created test root has the wrong identity: ${RUN_ROOT}"
    printf 'gentoo-optimization-test-root-v1\n' >"${RUN_ROOT}/.optimization-test-root"
    LOG_ROOT=${RUN_ROOT}/logs
    RESULTS_FILE=${RUN_ROOT}/results.tsv
    SUBTESTS_FILE=${RUN_ROOT}/subtests.tsv
    SUBTEST_FRAGMENT_ROOT=${RUN_ROOT}/subtest-fragments
    mkdir -p -- "${LOG_ROOT}" "${RUN_ROOT}/capabilities" \
        "${RUN_ROOT}/python-cache" "${RUN_ROOT}/preflight" \
        "${SUBTEST_FRAGMENT_ROOT}"
    printf 'status\ttest\tdetail\n' >"${RESULTS_FILE}"
    printf 'status\trequirement\ttest\tsubtest\tdetail\n' >"${SUBTESTS_FILE}"
}

# The EXIT/signal traps invoke this function indirectly.
# ShellCheck <=0.10 reports SC2317; >=0.11 reports SC2329 for trap callbacks.
# shellcheck disable=SC2317,SC2329
cleanup() {
    local status=$?
    local active_case_pid=
    if [[ -n ${ACTIVE_CASE_PGID} ]]; then
        active_case_pid=${ACTIVE_CASE_PGID}
        printf 'WARNING: terminating active test case on driver exit: %s (process group %s)\n' \
            "${ACTIVE_CASE_NAME}" "${ACTIVE_CASE_PGID}" >&2
        cleanup_process_group "${ACTIVE_CASE_PGID}" \
            "${ACTIVE_CASE_KILL_AFTER_SECONDS}"
        wait "${active_case_pid}" 2>/dev/null || true
        ACTIVE_CASE_PGID=
        ACTIVE_CASE_NAME=
        ACTIVE_CASE_KILL_AFTER_SECONDS=
    fi
    if ((TEMP_ROOT_CREATED)) && ((KEEP_TEMP == 0)) && ((FAIL_COUNT == 0)) && \
        ((PRESERVE_RUN_ROOT_FOR_EXTERNAL_AUTHORITY == 0)); then
        case ${RUN_ROOT} in
            /tmp/gentoo-optimization-tests.*|/var/tmp/gentoo-optimization/optimization-tests.*)
                if [[ -f ${RUN_ROOT}/.optimization-test-root ]] && \
                    grep -Fxq 'gentoo-optimization-test-root-v1' \
                        "${RUN_ROOT}/.optimization-test-root"; then
                    rm -rf -- "${RUN_ROOT}"
                else
                    printf 'WARNING: refusing to clean unmarked test root: %s\n' \
                        "${RUN_ROOT}" >&2
                fi
                ;;
            *)
                printf 'WARNING: refusing to clean unexpected test root: %s\n' \
                    "${RUN_ROOT}" >&2
                ;;
        esac
    elif [[ -n ${RUN_ROOT} ]]; then
        printf 'EVIDENCE: %s\n' "${RUN_ROOT}"
    fi
    return "${status}"
}

safe_detail() {
    local detail=$1
    detail=${detail//$'\t'/ }
    detail=${detail//$'\n'/ }
    printf '%s' "${detail}"
}

record_result() {
    local status=$1 name=$2 detail=$3
    printf '%s\t%s\t%s\n' "${status}" "${name}" "$(safe_detail "${detail}")" \
        >>"${RESULTS_FILE}"
}

record_subtest() {
    local status=$1 requirement=$2 test_name=$3 subtest_name=$4 detail=$5
    case ${status}:${requirement} in
        PASS:required) ((REQUIRED_SUBTEST_PASS_COUNT += 1)) ;;
        FAIL:required) ((REQUIRED_SUBTEST_FAIL_COUNT += 1)) ;;
        SKIP:required)
            ((REQUIRED_SUBTEST_SKIP_COUNT += 1))
            if [[ ${subtest_name} != driver.case-completion ]]; then
                ((MANDATORY_INTERNAL_SKIP_COUNT += 1))
            fi
            ;;
        PASS:diagnostic) ((DIAGNOSTIC_SUBTEST_PASS_COUNT += 1)) ;;
        FAIL:diagnostic) ((DIAGNOSTIC_SUBTEST_FAIL_COUNT += 1)) ;;
        SKIP:diagnostic) ((DIAGNOSTIC_SUBTEST_SKIP_COUNT += 1)) ;;
        *)
            printf 'ERROR: invalid structured subtest status/requirement: %s/%s\n' \
                "${status}" "${requirement}" >&2
            return 1
            ;;
    esac
    printf '%s\t%s\t%s\t%s\t%s\n' \
        "${status}" "${requirement}" "$(safe_detail "${test_name}")" \
        "$(safe_detail "${subtest_name}")" "$(safe_detail "${detail}")" \
        >>"${SUBTESTS_FILE}"
}

skip_case() {
    local name=$1 reason=$2
    ((SKIP_COUNT += 1))
    record_result SKIP "${name}" "${reason}"
    record_subtest SKIP required "${name}" driver.case-completion "${reason}"
    printf 'SKIP: %s — %s\n' "${name}" "${reason}"
}

collect_case_subtests() {
    local name=$1 log=$2 fragment=$3 expected_fragment_identity=$4 child_status=$5
    local row_status row_requirement row_name row_detail extra line marker
    local legacy_skip_index=0
    local line_number=0
    local observed_fragment_identity=
    local -A observed_subtest_names=()
    CASE_REQUIRED_SUBTEST_BLOCKER=0
    CASE_ANY_SUBTEST_FAILURE=0

    if [[ -f ${fragment} && ! -L ${fragment} ]]; then
        observed_fragment_identity=$(stat -c '%d:%i:%u:%g:%a:%h' -- "${fragment}")
    fi
    if [[ ! -f ${fragment} || -L ${fragment} ||
          ${observed_fragment_identity} != "${expected_fragment_identity}" ]]; then
        record_subtest FAIL required "${name}" driver.fragment-contract \
            'fixture replaced or removed its private structured subtest fragment'
        CASE_REQUIRED_SUBTEST_BLOCKER=1
        CASE_ANY_SUBTEST_FAILURE=1
    else
        while IFS=$'\t' read -r row_status row_requirement row_name row_detail extra || \
            [[ -n ${row_status}${row_requirement}${row_name}${row_detail}${extra} ]]; do
            ((line_number += 1))
            if ((line_number == 1)); then
                marker=${row_status}
                if [[ ${marker} != gentoo-optimization-subtest-fragment-v1 ||
                      -n ${row_requirement}${row_name}${row_detail}${extra} ]]; then
                    record_subtest FAIL required "${name}" driver.fragment-contract \
                        'fixture truncated or rewrote its structured subtest fragment marker'
                    CASE_REQUIRED_SUBTEST_BLOCKER=1
                    CASE_ANY_SUBTEST_FAILURE=1
                    break
                fi
                continue
            fi
            if [[ -n ${extra} || -z ${row_status} || -z ${row_requirement} || \
                  -z ${row_name} || -z ${row_detail} || ${row_name} == driver.* ||
                  ! ${row_name} =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$ ]]; then
                record_subtest FAIL required "${name}" driver.fragment-contract \
                    'fixture emitted a malformed structured subtest row'
                CASE_REQUIRED_SUBTEST_BLOCKER=1
                CASE_ANY_SUBTEST_FAILURE=1
                break
            fi
            if [[ -n ${observed_subtest_names["${row_name}"]:-} ]]; then
                record_subtest FAIL required "${name}" driver.fragment-contract \
                    'fixture emitted a duplicate structured subtest name'
                CASE_REQUIRED_SUBTEST_BLOCKER=1
                CASE_ANY_SUBTEST_FAILURE=1
                break
            fi
            if ! record_subtest "${row_status}" "${row_requirement}" "${name}" \
                "${row_name}" "${row_detail}"; then
                record_subtest FAIL required "${name}" driver.fragment-contract \
                    'fixture emitted a malformed structured subtest row'
                CASE_REQUIRED_SUBTEST_BLOCKER=1
                CASE_ANY_SUBTEST_FAILURE=1
                break
            fi
            observed_subtest_names["${row_name}"]=1
            if [[ ${row_status} == FAIL ]]; then
                CASE_ANY_SUBTEST_FAILURE=1
            elif [[ ${row_status}:${row_requirement} == SKIP:required ]]; then
                CASE_REQUIRED_SUBTEST_BLOCKER=1
            fi
        done <"${fragment}"
        if ((line_number == 0)); then
            record_subtest FAIL required "${name}" driver.fragment-contract \
                'fixture truncated its structured subtest fragment to zero bytes'
            CASE_REQUIRED_SUBTEST_BLOCKER=1
            CASE_ANY_SUBTEST_FAILURE=1
        fi
    fi

    # Legacy shell fixtures cannot silently hide optional branches behind a
    # successful child exit.  Python unittest uses the structured runner above.
    # Explicit diagnostic skips use their own marker; every other discovered
    # shell skip is mandatory until a fixture deliberately classifies it.
    while IFS= read -r line || [[ -n ${line} ]]; do
        row_requirement=
        row_detail=
        case ${line} in
            DIAGNOSTIC-SKIP-SUBTEST:\ *)
                row_requirement=diagnostic
                row_detail=${line#DIAGNOSTIC-SKIP-SUBTEST: }
                ;;
            SKIP-SUBTEST:\ *)
                row_requirement=required
                row_detail=${line#SKIP-SUBTEST: }
                ;;
            HOST-SKIP:\ *)
                row_requirement=required
                row_detail=${line#HOST-SKIP: }
                ;;
            SKIP:\ *)
                if ((child_status == 0)); then
                    row_requirement=required
                    row_detail=${line#SKIP: }
                fi
                ;;
        esac
        [[ -n ${row_requirement} ]] || continue
        ((legacy_skip_index += 1))
        record_subtest SKIP "${row_requirement}" "${name}" \
            "driver.discovered-skip-${legacy_skip_index}" "${row_detail}"
        if [[ ${row_requirement} == required ]]; then
            CASE_REQUIRED_SUBTEST_BLOCKER=1
        fi
    done <"${log}"
}

process_group_exists() {
    local pgid=$1
    local stat_file stat_line stat_tail state member_pgid scanned=0
    local -a stat_files=()
    [[ ${pgid} =~ ^[1-9][0-9]*$ ]] || return 0
    if ((INTERNAL_PROCESS_GROUP_ASSUME_VISIBLE == 0)); then
        kill -0 -- "-${pgid}" 2>/dev/null || return 1
    fi
    # kill(2) reports a process group containing only zombies as existent even
    # though it has no executable work and no signal can quiesce it further.
    # Inspect one coherent stat record per member and keep the conservative
    # "active" result if procfs itself is unavailable.
    [[ -r ${INTERNAL_PROCESS_GROUP_PROC_ROOT}/self/stat ]] || return 0
    stat_files=("${INTERNAL_PROCESS_GROUP_PROC_ROOT}"/[0-9]*/stat)
    for stat_file in "${stat_files[@]}"; do
        ((scanned += 1))
        ((scanned <= 131072)) || return 0
        if [[ ! -r ${stat_file} ]]; then
            [[ ! -e ${stat_file} ]] || return 0
            continue
        fi
        if ! IFS= read -r stat_line <"${stat_file}"; then
            [[ ! -e ${stat_file} ]] || return 0
            continue
        fi
        if [[ ${stat_line} != *') '* ]]; then
            [[ ! -e ${stat_file} ]] || return 0
            continue
        fi
        stat_tail=${stat_line##*) }
        IFS=' ' read -r state _ member_pgid _ <<<"${stat_tail}"
        if [[ ! ${state} =~ ^[A-Za-z]$ || ! ${member_pgid} =~ ^[0-9]+$ ]]; then
            [[ ! -e ${stat_file} ]] || return 0
            continue
        fi
        if ((member_pgid == pgid)) && \
            [[ ${state} != Z && ${state} != X && ${state} != x ]]; then
            return 0
        fi
    done
    return 1
}

cleanup_process_group() {
    local pgid=$1 grace_seconds=$2
    local attempt max_attempts
    PROCESS_GROUP_WAS_ACTIVE=0
    PROCESS_GROUP_SURVIVED=0
    if ! process_group_exists "${pgid}"; then
        return 0
    fi

    PROCESS_GROUP_WAS_ACTIVE=1
    kill -TERM -- "-${pgid}" 2>/dev/null || true
    max_attempts=$((grace_seconds * 10))
    for ((attempt = 0; attempt < max_attempts; attempt += 1)); do
        process_group_exists "${pgid}" || return 0
        "${SLEEP_BIN}" 0.1
    done

    kill -KILL -- "-${pgid}" 2>/dev/null || true
    for ((attempt = 0; attempt < 20; attempt += 1)); do
        process_group_exists "${pgid}" || return 0
        "${SLEEP_BIN}" 0.1
    done
    PROCESS_GROUP_SURVIVED=1
    return 0
}

run_case_with_deadline() {
    local name=$1 timeout_seconds=$2 kill_after_seconds=$3
    shift 3
    local slug=${name//[^[:alnum:]_.-]/_}
    local log=${LOG_ROOT}/${slug}.log
    local status case_pgid started_at elapsed_seconds skip_detail
    local fragment=${SUBTEST_FRAGMENT_ROOT}/${slug}.tsv
    local fragment_identity=
    local deadline_state=within-limit process_group_cleanup=clean

    printf 'RUN:  %s\n' "${name}"
    {
        printf 'COMMAND'
        printf ' %q' "$@"
        printf '\n'
        printf 'DEADLINE timeout_seconds=%s kill_after_seconds=%s\n' \
            "${timeout_seconds}" "${kill_after_seconds}"
    } >"${log}"
    printf 'gentoo-optimization-subtest-fragment-v1\n' >"${fragment}"
    fragment_identity=$(stat -c '%d:%i:%u:%g:%a:%h' -- "${fragment}")
    started_at=${SECONDS}
    set +e
    "${SETSID_BIN}" --wait \
        "${TIMEOUT_BIN}" --foreground --signal=TERM \
        --kill-after="${kill_after_seconds}s" "${timeout_seconds}s" \
        "${ENV_BIN}" --default-signal=HUP --default-signal=INT \
        --default-signal=QUIT --default-signal=TERM -- \
        GENTOO_OPT_AUTHORITATIVE="${AUTHORITATIVE}" \
        GENTOO_OPT_SUBTEST_RESULTS="${fragment}" \
        GENTOO_OPT_TEST_CASE="${name}" \
        "$@" >>"${log}" 2>&1 &
    case_pgid=$!
    ACTIVE_CASE_PGID=${case_pgid}
    ACTIVE_CASE_NAME=${name}
    ACTIVE_CASE_KILL_AFTER_SECONDS=${kill_after_seconds}
    wait "${case_pgid}"
    status=$?
    cleanup_process_group "${case_pgid}" "${kill_after_seconds}"
    if ((PROCESS_GROUP_WAS_ACTIVE)); then
        process_group_cleanup=terminated-residual
        if ((status == 0)); then
            status=125
        fi
    fi
    if ((PROCESS_GROUP_SURVIVED)); then
        process_group_cleanup=failed
        status=125
    fi
    ACTIVE_CASE_PGID=
    ACTIVE_CASE_NAME=
    ACTIVE_CASE_KILL_AFTER_SECONDS=
    set -e
    elapsed_seconds=$((SECONDS - started_at))
    if ((status == 124 || status == 137)); then
        deadline_state=exceeded
    fi
    collect_case_subtests "${name}" "${log}" "${fragment}" \
        "${fragment_identity}" "${status}"
    if ((CASE_ANY_SUBTEST_FAILURE)) || \
        ((AUTHORITATIVE == 1 && CASE_REQUIRED_SUBTEST_BLOCKER)); then
        if ((status == 0)); then
            status=86
            printf '%s\n' \
                'AUTHORITATIVE-SUBTEST-FAIL: required internal subtest did not pass' \
                >>"${log}"
        fi
    fi
    if ((status == 0)); then
        record_subtest PASS required "${name}" driver.case-completion \
            'top-level case completed successfully'
        ((PASS_COUNT += 1))
        record_result PASS "${name}" \
            "exit_status=0 log=${log} timeout_seconds=${timeout_seconds} kill_after_seconds=${kill_after_seconds} elapsed_seconds=${elapsed_seconds} deadline=${deadline_state} process_group_cleanup=${process_group_cleanup}"
        printf 'PASS: %s\n' "${name}"
    elif ((status == 77)); then
        record_subtest SKIP required "${name}" driver.case-completion \
            'top-level fixture exited with skip status 77'
        ((SKIP_COUNT += 1))
        skip_detail=$(sed -n 's/^SKIP: //p' "${log}" | tail -n 1)
        [[ -n ${skip_detail} ]] || skip_detail='fixture exited with the conventional skip status 77'
        record_result SKIP "${name}" \
            "$(safe_detail "${skip_detail}") exit_status=77 log=${log} timeout_seconds=${timeout_seconds} kill_after_seconds=${kill_after_seconds} elapsed_seconds=${elapsed_seconds} deadline=${deadline_state} process_group_cleanup=${process_group_cleanup}"
        printf 'SKIP: %s — %s\n' "${name}" "${skip_detail}"
    else
        record_subtest FAIL required "${name}" driver.case-completion \
            "top-level case failed with exit status ${status}"
        ((FAIL_COUNT += 1))
        KEEP_TEMP=1
        record_result FAIL "${name}" \
            "exit_status=${status} log=${log} timeout_seconds=${timeout_seconds} kill_after_seconds=${kill_after_seconds} elapsed_seconds=${elapsed_seconds} deadline=${deadline_state} process_group_cleanup=${process_group_cleanup}"
        printf 'FAIL: %s (exit %d; log: %s)\n' "${name}" "${status}" "${log}" >&2
        printf '%s\n' '----- failure log tail -----' >&2
        tail -n 240 -- "${log}" >&2 || true
        printf '%s\n' '----- end failure log -----' >&2
    fi
}

run_case() {
    local name=$1
    shift
    run_case_with_deadline "${name}" "${TEST_CASE_TIMEOUT_SECONDS}" \
        "${TEST_CASE_KILL_AFTER_SECONDS}" "$@"
}

run_case_in_repository_with_deadline() {
    local name=$1 timeout_seconds=$2 kill_after_seconds=$3
    shift 3
    # The positional parameters intentionally expand in the child Bash.
    # shellcheck disable=SC2016
    run_case_with_deadline "${name}" "${timeout_seconds}" \
        "${kill_after_seconds}" "${BASH_BIN}" -c \
        'cd -- "$1"; shift; exec "$@"' run-in-repository \
        "${REPOSITORY_ROOT}" "$@"
}

run_case_in_repository() {
    local name=$1
    shift
    run_case_in_repository_with_deadline "${name}" \
        "${TEST_CASE_TIMEOUT_SECONDS}" "${TEST_CASE_KILL_AFTER_SECONDS}" "$@"
}

if [[ -n ${INTERNAL_PROCESS_GROUP_PROBE} ]]; then
    if process_group_exists "${INTERNAL_PROCESS_GROUP_PROBE}"; then
        printf 'active\n'
        exit 0
    fi
    printf 'quiescent\n'
    exit 1
fi

create_run_root
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

PHASE2_EVIDENCE_TOOL=${REPOSITORY_ROOT}/scripts/optimization/verify/phase2-evidence.py
TEST_RUN_PROVENANCE_PENDING=${RUN_ROOT}/test-run-provenance.pending.json
TEST_RUN_PROVENANCE=${RUN_ROOT}/test-run-provenance.json
PROVENANCE_PYTHON=${PYTHON_BIN}
declare -a PROVENANCE_EXECUTED_TOOL_ARGUMENTS=()
for execution_tool_name in "${TEST_EXECUTION_TOOL_NAMES[@]}"; do
    PROVENANCE_EXECUTED_TOOL_ARGUMENTS+=(
        --executed-tool
        "${execution_tool_name}=${TEST_EXECUTION_TOOL_PATHS["${execution_tool_name}"]}"
    )
done
declare -a PROVENANCE_AUTHORITY_ARGUMENTS=()
if ((AUTHORITATIVE == 1)); then
    PROVENANCE_AUTHORITY_ARGUMENTS+=(--authoritative-tools)
fi
PROVENANCE_ACTIVE=0
PROVENANCE_GIT_STATUS=
read_isolated_repository_status() {
    "${SETSID_BIN}" --wait \
        "${TIMEOUT_BIN}" --signal=TERM \
        --kill-after=10s 60s \
        "${ENV_BIN}" -i \
        GIT_CONFIG_GLOBAL=/dev/null \
        GIT_CONFIG_NOSYSTEM=1 \
        GIT_OPTIONAL_LOCKS=0 \
        HOME=/nonexistent LANG=C LC_ALL=C PATH=/usr/bin:/bin \
        "${GIT_BIN}" --no-pager --no-replace-objects \
        -c core.hooksPath=/dev/null \
        -c core.fsmonitor=false \
        -c diff.external= \
        -c "safe.directory=${REPOSITORY_ROOT}" \
        -C "${REPOSITORY_ROOT}" \
        status --porcelain=v1 --untracked-files=all
}
if [[ ! -f ${PHASE2_EVIDENCE_TOOL} || -L ${PHASE2_EVIDENCE_TOOL} ]]; then
    skip_case phase2-run-provenance-start \
        "evidence tool is absent or symlinked: ${PHASE2_EVIDENCE_TOOL}"
elif [[ -z ${PROVENANCE_PYTHON} || -z ${GIT_BIN} || -z ${SHELLCHECK_BIN} ]]; then
    skip_case phase2-run-provenance-start \
        'python3, git, or shellcheck is unavailable for exact test-run provenance'
elif ! PROVENANCE_GIT_STATUS=$(read_isolated_repository_status 2>/dev/null); then
    skip_case phase2-run-provenance-start \
        'bounded isolated repository status inspection failed'
elif [[ -n ${PROVENANCE_GIT_STATUS} ]]; then
    skip_case phase2-run-provenance-start \
        'repository is dirty or has untracked files; authoritative provenance requires a clean commit'
else
    run_case_in_repository phase2-run-provenance-start \
        "${ENV_BIN}" -u PYTHONPYCACHEPREFIX PYTHONDONTWRITEBYTECODE=1 \
        "${PROVENANCE_PYTHON}" -I -B "${PHASE2_EVIDENCE_TOOL}" \
        run-provenance-start --repository-root "${REPOSITORY_ROOT}" \
        --policy optimization/phase2-evidence-policy.json \
        --driver "${REPOSITORY_ROOT}/tests/run-optimization-tests.sh" \
        --driver-bash-pid "${BASHPID}" --git "${GIT_BIN}" \
        "${PROVENANCE_AUTHORITY_ARGUMENTS[@]}" \
        "${PROVENANCE_EXECUTED_TOOL_ARGUMENTS[@]}" \
        --output "${TEST_RUN_PROVENANCE_PENDING}"
    if [[ -f ${TEST_RUN_PROVENANCE_PENDING} && \
        ! -L ${TEST_RUN_PROVENANCE_PENDING} ]]; then
        PROVENANCE_ACTIVE=1
    fi
fi

TEST_CONTRACT_DISCOVERY_TOOL=${REPOSITORY_ROOT}/scripts/optimization/verify/phase2-test-contract.py
AUTHORITATIVE_TEST_CONTRACT=${REPOSITORY_ROOT}/optimization/phase2-authoritative-test-contract.json
if [[ ${MODE} == portable-complete || ${MODE} == authoritative || \
      ${AUTHORITATIVE} == 1 ]]; then
    contract_python=${PYTHON_BIN}
    if [[ -z ${contract_python} || ! -f ${TEST_CONTRACT_DISCOVERY_TOOL} ||
          -L ${TEST_CONTRACT_DISCOVERY_TOOL} ||
          ! -f ${AUTHORITATIVE_TEST_CONTRACT} ||
          -L ${AUTHORITATIVE_TEST_CONTRACT} ]]; then
        printf '%s\n' \
            'ERROR: deterministic test-contract discovery prerequisites are unavailable' \
            >"${RUN_ROOT}/test-contract-static-check.log"
        cat -- "${RUN_ROOT}/test-contract-static-check.log" >&2
        KEEP_TEMP=1
        exit 2
    fi
    set +e
    "${ENV_BIN}" -u PYTHONPYCACHEPREFIX PYTHONDONTWRITEBYTECODE=1 \
        "${contract_python}" -I -B "${TEST_CONTRACT_DISCOVERY_TOOL}" check \
        --repository-root "${REPOSITORY_ROOT}" \
        --contract "${AUTHORITATIVE_TEST_CONTRACT}" \
        >"${RUN_ROOT}/test-contract-static-check.log" 2>&1
    contract_discovery_status=$?
    set -e
    if ((contract_discovery_status != 0)); then
        printf 'FAIL: deterministic test-contract discovery (exit %d; log: %s)\n' \
            "${contract_discovery_status}" \
            "${RUN_ROOT}/test-contract-static-check.log" >&2
        tail -n 240 -- "${RUN_ROOT}/test-contract-static-check.log" >&2 || true
        KEEP_TEMP=1
        exit 2
    fi
    printf 'TEST-CONTRACT-DISCOVERY: %s\n' \
        "${RUN_ROOT}/test-contract-static-check.log"
    record_subtest PASS required phase2-test-contract-static \
        driver.case-completion \
        'deterministic no-execution topology matches the reviewed contract'
    ((PASS_COUNT += 1))
    record_result PASS phase2-test-contract-static \
        "exit_status=0 log=${RUN_ROOT}/test-contract-static-check.log discovery=pre-gate"
fi

declare -a SHELL_SOURCES=()
declare -a PYTHON_SOURCES=()
declare -a PYTHON_TEST_DIRECTORIES=()
while IFS= read -r -d '' source_file; do
    SHELL_SOURCES+=("${source_file}")
done < <(discover_shell_sources)
while IFS= read -r -d '' source_file; do
    PYTHON_SOURCES+=("${source_file}")
done < <(
    find "${REPOSITORY_ROOT}/optimization" "${REPOSITORY_ROOT}/scripts" \
        "${REPOSITORY_ROOT}/tests" -type f -name '*.py' -print0 | \
        LC_ALL=C sort -z
)
while IFS= read -r -d '' test_directory; do
    PYTHON_TEST_DIRECTORIES+=("${test_directory}")
done < <(
    find "${REPOSITORY_ROOT}/tests" -type f -name 'test_*.py' \
        -printf '%h\0' | LC_ALL=C sort -zu
)

if ((${#SHELL_SOURCES[@]} == 0)); then
    skip_case bash-syntax 'no shell sources were discovered in the repository test scope'
else
    for source_file in "${SHELL_SOURCES[@]}"; do
        relative_file=${source_file#"${REPOSITORY_ROOT}/"}
        run_case "bash-syntax:${relative_file}" \
            "${BASH_BIN}" -n -- "${source_file}"
    done
fi

if [[ -n ${SHELLCHECK_BIN} ]]; then
    run_case_in_repository shellcheck \
        "${SHELLCHECK_BIN}" -- "${SHELL_SOURCES[@]}"
else
    skip_case shellcheck \
        "${SHELLCHECK:-shellcheck} is not an executable in PATH; set SHELLCHECK=/absolute/path"
fi

PRODUCTION_PROFILE_LOCK_TEST=${REPOSITORY_ROOT}/tests/optimization/test_production_profile_lock_transaction.py
STRUCTURED_UNITTEST_RUNNER=${REPOSITORY_ROOT}/scripts/optimization/verify/run-unittest-suite.py
if [[ -z ${PYTHON_BIN} ]]; then
    skip_case python-source-compilation 'python3 is unavailable'
    skip_case python-unit-tests 'python3 is unavailable'
    skip_case production-profile-lock-crash-stress 'python3 is unavailable'
else
    if ((${#PYTHON_SOURCES[@]} == 0)); then
        skip_case python-source-compilation \
            'no Python sources were discovered in the repository test scope'
    else
        run_case python-source-compilation "${ENV_BIN}" \
            PYTHONDONTWRITEBYTECODE=1 \
            PYTHONPYCACHEPREFIX="${RUN_ROOT}/python-cache/compile" \
            "${PYTHON_BIN}" -m py_compile "${PYTHON_SOURCES[@]}"
    fi
    if [[ ${MODE} == smoke ]]; then
        skip_case python-unit-tests \
            'smoke mode excludes full Python unittest discovery; use portable-complete'
        skip_case production-profile-lock-crash-stress \
            'smoke mode excludes the 300-cycle crash stress; use stress or authoritative'
    elif [[ ${MODE} == checkpoint-smoke ]]; then
        if [[ ! -f ${STRUCTURED_UNITTEST_RUNNER} || \
              -L ${STRUCTURED_UNITTEST_RUNNER} ]]; then
            skip_case checkpoint-smoke \
                'structured unittest runner is absent or symlinked'
        else
            # The child Bash must expand its own exact-identity arrays.
            # shellcheck disable=SC2016
            run_case_in_repository_with_deadline checkpoint-smoke \
                "${CHECKPOINT_SMOKE_TIMEOUT_SECONDS}" \
                "${TEST_CASE_KILL_AFTER_SECONDS}" \
                "${ENV_BIN}" -u PYTHONPYCACHEPREFIX \
                -u GENTOO_OPT_RUN_CHECKPOINT_HOST_CAPABILITIES \
                -u GENTOO_OPT_RUN_JSONSCHEMA_HOST_CAPABILITIES \
                PYTHONDONTWRITEBYTECODE=1 \
                "${BASH_BIN}" -c '
                    set -Eeuo pipefail
                    python_bin=$1
                    runner=$2
                    timeout_bin=$3
                    method_timeout=$4
                    kill_after=$5
                    shift 5
                    expected=("$@")
                    filters=()
                    for identity in "${expected[@]}"; do
                        filters+=(-k "${identity#test_create_binpkg_checkpoint.}")
                    done
                    mapfile -t actual < <(
                        "${python_bin}" "${runner}" --list-identities discover \
                            -s tests/optimization/recovery \
                            -p test_create_binpkg_checkpoint.py \
                            "${filters[@]}"
                    )
                    if ((${#actual[@]} != ${#expected[@]})); then
                        printf "ERROR: checkpoint-smoke selected %s identities, expected %s\n" \
                            "${#actual[@]}" "${#expected[@]}" >&2
                        printf "actual=%s\n" "${actual[@]}" >&2
                        exit 2
                    fi
                    for ((index = 0; index < ${#expected[@]}; index += 1)); do
                        if [[ ${actual[index]} != "${expected[index]}" ]]; then
                            printf "ERROR: checkpoint-smoke identity mismatch at %s: expected=%s actual=%s\n" \
                                "${index}" "${expected[index]}" "${actual[index]}" >&2
                            exit 2
                        fi
                    done
                    for identity in "${expected[@]}"; do
                        method=${identity#test_create_binpkg_checkpoint.}
                        "${timeout_bin}" --foreground --signal=TERM \
                            --kill-after="${kill_after}s" \
                            "${method_timeout}s" \
                            "${python_bin}" "${runner}" discover \
                            -s tests/optimization/recovery \
                            -p test_create_binpkg_checkpoint.py -v \
                            -k "${method}"
                    done
                ' checkpoint-smoke-exact-identities \
                "${PYTHON_BIN}" "${STRUCTURED_UNITTEST_RUNNER}" \
                "${TIMEOUT_BIN}" "${CHECKPOINT_SMOKE_METHOD_TIMEOUT_SECONDS}" \
                "${TEST_CASE_KILL_AFTER_SECONDS}" \
                "${CHECKPOINT_SMOKE_IDENTITIES[@]}"
        fi
        skip_case production-profile-lock-crash-stress \
            'checkpoint-smoke excludes the 300-cycle profile-lock stress'
    elif [[ ! -f ${STRUCTURED_UNITTEST_RUNNER} || -L ${STRUCTURED_UNITTEST_RUNNER} ]]; then
        skip_case python-unit-tests \
            'structured unittest runner is absent or symlinked'
        skip_case production-profile-lock-crash-stress \
            'structured unittest runner is absent or symlinked'
    elif ((${#PYTHON_TEST_DIRECTORIES[@]} == 0)); then
        skip_case python-unit-tests 'no test_*.py source was discovered below tests/'
        skip_case production-profile-lock-crash-stress \
            'production profile-lock transaction test module is unavailable'
    else
        for test_directory in "${PYTHON_TEST_DIRECTORIES[@]}"; do
            relative_directory=${test_directory#"${REPOSITORY_ROOT}/"}
            unittest_environment=("${ENV_BIN}" -u PYTHONPYCACHEPREFIX \
                -u GENTOO_OPT_RUN_CHECKPOINT_HOST_CAPABILITIES \
                -u GENTOO_OPT_RUN_JSONSCHEMA_HOST_CAPABILITIES \
                PYTHONDONTWRITEBYTECODE=1)
            if ((AUTHORITATIVE == 1)) && \
                [[ ${relative_directory} == tests/optimization/recovery ]]; then
                # These three methods exercise the real host pidfd and
                # unshare --kill-child primitives.  Portable runs retain their
                # explicit required skips; an authoritative host run must
                # execute them, so enable the reviewed opt-in only for this
                # isolated recovery suite.
                unittest_environment+=(
                    GENTOO_OPT_RUN_CHECKPOINT_HOST_CAPABILITIES=1
                )
            fi
            if ((AUTHORITATIVE == 1)) && \
                [[ ${relative_directory} == tests/optimization ]]; then
                # The jsonschema prerequisite fixture keeps its real Gemato,
                # mount/PID/network-namespace and Portage-vardb probes behind
                # a separate explicit opt-in. Portable execution reports the
                # reviewed host-only methods as required skips; authoritative
                # execution must run them and therefore cannot hide a missing
                # primitive behind the ordinary unittest top-level result.
                unittest_environment+=(
                    GENTOO_OPT_RUN_JSONSCHEMA_HOST_CAPABILITIES=1
                )
            fi
            unittest_arguments=(discover -s "${test_directory}" -p 'test_*.py' -v)
            if [[ ${relative_directory} == tests/optimization ]]; then
                # This expensive matrix has its own required top-level case.
                # Excluding it here prevents duplicate execution while its
                # identity remains independently bound in results/subtests.
                unittest_arguments=(--exclude-id-prefix test_phase2_evidence. \
                    "${unittest_arguments[@]}")
            fi
            if [[ ${relative_directory} == tests/optimization/recovery ]]; then
                run_case_in_repository_with_deadline \
                    "python-unit-tests:${relative_directory}" \
                    "${RECOVERY_SUITE_TIMEOUT_SECONDS}" \
                    "${TEST_CASE_KILL_AFTER_SECONDS}" \
                    "${unittest_environment[@]}" \
                    "${PYTHON_BIN}" "${STRUCTURED_UNITTEST_RUNNER}" \
                    "${unittest_arguments[@]}"
            else
                run_case_in_repository "python-unit-tests:${relative_directory}" \
                    "${unittest_environment[@]}" \
                    "${PYTHON_BIN}" "${STRUCTURED_UNITTEST_RUNNER}" \
                    "${unittest_arguments[@]}"
            fi
        done
        if [[ ${MODE} != stress && ${MODE} != authoritative ]]; then
            skip_case production-profile-lock-crash-stress \
                'this 300-cycle workload runs only in stress or authoritative mode'
        elif [[ -f ${PRODUCTION_PROFILE_LOCK_TEST} &&
                ! -L ${PRODUCTION_PROFILE_LOCK_TEST} ]]; then
            run_case_in_repository production-profile-lock-crash-stress \
                "${ENV_BIN}" -u PYTHONPYCACHEPREFIX \
                GENTOO_OPT_COORDINATOR_CRASH_STRESS=1 \
                PYTHONDONTWRITEBYTECODE=1 \
                "${PYTHON_BIN}" "${STRUCTURED_UNITTEST_RUNNER}" discover \
                -s tests/optimization \
                -p test_production_profile_lock_transaction.py \
                -k test_child_barrier_crash_recovery_stress -v
        else
            skip_case production-profile-lock-crash-stress \
                'production profile-lock transaction test module is unavailable'
        fi
    fi
fi

PHASE2_EVIDENCE_TEST=${REPOSITORY_ROOT}/tests/optimization/test_phase2_evidence.py
if [[ -z ${PYTHON_BIN} ]]; then
    skip_case phase2-evidence-contract 'python3 is unavailable'
elif [[ ! -f ${STRUCTURED_UNITTEST_RUNNER} || -L ${STRUCTURED_UNITTEST_RUNNER} ]]; then
    skip_case phase2-evidence-contract 'structured unittest runner is absent or symlinked'
elif [[ ! -f ${PHASE2_EVIDENCE_TEST} ]]; then
    skip_case phase2-evidence-contract \
        "fixture is absent: ${PHASE2_EVIDENCE_TEST}"
elif [[ ${MODE} == smoke || ${MODE} == checkpoint-smoke ]]; then
    run_case_in_repository phase2-evidence-smoke \
        "${ENV_BIN}" -u PYTHONPYCACHEPREFIX PYTHONDONTWRITEBYTECODE=1 \
        "${PYTHON_BIN}" "${STRUCTURED_UNITTEST_RUNNER}" -v \
        tests.optimization.test_phase2_evidence.Phase2EvidenceTests.test_exact_topology_rejects_deleted_and_unexpected_cases
else
    run_case_in_repository phase2-evidence-contract \
        "${ENV_BIN}" -u PYTHONPYCACHEPREFIX PYTHONDONTWRITEBYTECODE=1 \
        "${PYTHON_BIN}" "${STRUCTURED_UNITTEST_RUNNER}" discover \
        -s tests/optimization -p test_phase2_evidence.py -v
fi

if [[ ${MODE} != smoke && ${MODE} != checkpoint-smoke ]]; then

PACKAGE_ENV_DUPLICATE_CHECKER=${REPOSITORY_ROOT}/scripts/optimization/check-package-env-duplicates.py
if [[ -z ${PYTHON_BIN} ]]; then
    skip_case package-env-duplicate-policy 'python3 is unavailable'
elif [[ ! -f ${PACKAGE_ENV_DUPLICATE_CHECKER} ]]; then
    skip_case package-env-duplicate-policy \
        "checker is absent: ${PACKAGE_ENV_DUPLICATE_CHECKER}"
else
    run_case_in_repository package-env-duplicate-policy \
        "${PYTHON_BIN}" \
        "${PACKAGE_ENV_DUPLICATE_CHECKER}" --skip-portage-universe
    if [[ -d /var/db/pkg && -d /var/db/repos ]] && \
        PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -c 'import portage' \
            >/dev/null 2>&1; then
        run_case_in_repository package-env-portage-semantic \
            "${PYTHON_BIN}" \
            "${PACKAGE_ENV_DUPLICATE_CHECKER}" --require-portage-universe
    else
        skip_case package-env-portage-semantic \
            'Portage Python API and live /var/db/pkg plus /var/db/repos are unavailable; portable policy checks ran, live atom/overlap semantics did not'
    fi
fi

PORTAGE_CONFIG_CLEANUP_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-portage-config-cleanup.sh
if [[ ! -f ${PORTAGE_CONFIG_CLEANUP_FIXTURE} ]]; then
    skip_case portage-config-cleanup "fixture is absent: ${PORTAGE_CONFIG_CLEANUP_FIXTURE}"
elif ! require_commands bash grep rg stat wc; then
    skip_case portage-config-cleanup "${PREFLIGHT_REASON}"
else
    run_case portage-config-cleanup \
        "${BASH_BIN}" -- "${PORTAGE_CONFIG_CLEANUP_FIXTURE}"
fi

FRAMEWORK_INSTALLER_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-framework-installer.sh
if [[ ! -f ${FRAMEWORK_INSTALLER_FIXTURE} ]]; then
    skip_case framework-installer "fixture is absent: ${FRAMEWORK_INSTALLER_FIXTURE}"
elif ! require_commands awk bash chmod cmp cp find flock getfattr git grep install jq ln \
    mkdir mktemp mv ps readlink realpath rm runuser sed seq setsid sha256sum \
    sleep sort stat sync tar tr rmdir setfattr; then
    skip_case framework-installer "${PREFLIGHT_REASON}"
else
    run_case framework-installer "${ENV_BIN}" \
        TEST_CASE_TIMEOUT_SECONDS="${TEST_CASE_TIMEOUT_SECONDS}" \
        "${BASH_BIN}" -- "${FRAMEWORK_INSTALLER_FIXTURE}"
fi

NO_LEGACY_BOLT_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-no-legacy-bolt.sh
if [[ ! -f ${NO_LEGACY_BOLT_FIXTURE} ]]; then
    skip_case no-legacy-bolt "fixture is absent: ${NO_LEGACY_BOLT_FIXTURE}"
elif ! require_commands bash cmp grep mktemp rm; then
    skip_case no-legacy-bolt "${PREFLIGHT_REASON}"
else
    run_case no-legacy-bolt "${BASH_BIN}" -- "${NO_LEGACY_BOLT_FIXTURE}"
fi

NO_LEGACY_PGO_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-no-legacy-pgo.sh
if [[ ! -f ${NO_LEGACY_PGO_FIXTURE} ]]; then
    skip_case no-legacy-pgo "fixture is absent: ${NO_LEGACY_PGO_FIXTURE}"
elif ! require_commands awk bash grep mktemp rg rm; then
    skip_case no-legacy-pgo "${PREFLIGHT_REASON}"
else
    run_case no-legacy-pgo "${BASH_BIN}" -- "${NO_LEGACY_PGO_FIXTURE}"
fi

PGO_DISPATCHER_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-pgo-dispatcher.sh
if [[ ! -f ${PGO_DISPATCHER_FIXTURE} ]]; then
    skip_case pgo-dispatcher "fixture is absent: ${PGO_DISPATCHER_FIXTURE}"
elif ! require_commands awk bash chmod dirname find grep head mkdir mktemp realpath \
    rm sed sha256sum sort stat touch tr wc; then
    skip_case pgo-dispatcher "${PREFLIGHT_REASON}"
else
    run_case pgo-dispatcher "${BASH_BIN}" -- "${PGO_DISPATCHER_FIXTURE}"
fi

PORTAGE_QA_HOOK_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-portage-qa-hook.sh
if [[ ! -f ${PORTAGE_QA_HOOK_FIXTURE} ]]; then
    skip_case portage-qa-hook-state "fixture is absent: ${PORTAGE_QA_HOOK_FIXTURE}"
elif ! require_commands bash mkdir mktemp rm wc; then
    skip_case portage-qa-hook-state "${PREFLIGHT_REASON}"
else
    run_case portage-qa-hook-state \
        "${BASH_BIN}" -- "${PORTAGE_QA_HOOK_FIXTURE}"
fi

PORTAGE_PHASE_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-portage-phase-integration.sh
if [[ ! -f ${PORTAGE_PHASE_FIXTURE} ]]; then
    skip_case portage-pre-strip-integration \
        "fixture is absent: ${PORTAGE_PHASE_FIXTURE}"
elif ((EUID != 0)); then
    skip_case portage-pre-strip-integration \
        'real disposable Portage phase integration requires a root driver invocation'
elif ! require_commands awk b2sum bash ebuild portageq python3 readelf sed \
    sha256sum sha512sum stat; then
    skip_case portage-pre-strip-integration "${PREFLIGHT_REASON}"
else
    run_case portage-pre-strip-integration \
        "${BASH_BIN}" -- "${PORTAGE_PHASE_FIXTURE}"
fi

PORTAGE_PHASE_IDENTITY_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-portage-phase-identity.sh
if [[ ! -f ${PORTAGE_PHASE_IDENTITY_FIXTURE} ]]; then
    skip_case portage-phase-identity \
        "fixture is absent: ${PORTAGE_PHASE_IDENTITY_FIXTURE}"
elif ((EUID != 0)); then
    skip_case portage-phase-identity \
        'live Portage userpriv/install identity proof requires a root driver invocation'
elif ! require_commands awk b2sum bash cat chmod cp cut ebuild find getent grep \
    head mkdir mktemp mv portageq python3 readlink realpath rm sha256sum \
    sha512sum sort stat sync tail xargs; then
    skip_case portage-phase-identity "${PREFLIGHT_REASON}"
else
    run_case portage-phase-identity \
        "${BASH_BIN}" -- "${PORTAGE_PHASE_IDENTITY_FIXTURE}" \
        --output-dir "${RUN_ROOT}/portage-phase-identity"
fi

PORTAGE_PGO_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-portage-pgo-use-integration.sh
if [[ ! -f ${PORTAGE_PGO_FIXTURE} ]]; then
    skip_case portage-pgo-use-integration "fixture is absent: ${PORTAGE_PGO_FIXTURE}"
elif ((EUID != 0)); then
    skip_case portage-pgo-use-integration \
        'real disposable Portage PGO-use integration requires a root driver invocation'
elif ! require_commands awk b2sum bash cp ebuild find grep mv python3 rm sed \
    sha256sum sha512sum stat tail wc; then
    skip_case portage-pgo-use-integration "${PREFLIGHT_REASON}"
else
    run_case portage-pgo-use-integration \
        "${BASH_BIN}" -- "${PORTAGE_PGO_FIXTURE}"
fi

PORTAGE_SAMPLE_PGO_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-portage-sample-pgo-integration.sh
PORTAGE_SAMPLE_ENV_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-portage-sample-production-env.sh
if [[ ! -f ${PORTAGE_SAMPLE_ENV_FIXTURE} ]]; then
    skip_case portage-sample-production-env \
        "fixture is absent: ${PORTAGE_SAMPLE_ENV_FIXTURE}"
elif ! require_commands bash env grep mktemp realpath sha256sum; then
    skip_case portage-sample-production-env "${PREFLIGHT_REASON}"
else
    run_case portage-sample-production-env \
        "${BASH_BIN}" -- "${PORTAGE_SAMPLE_ENV_FIXTURE}"
fi

PORTAGE_SAMPLE_CASES=(
    portage-sample-pgo-integration
    portage-sample-pgo-live-policy-integration
)
if [[ ! -f ${PORTAGE_SAMPLE_PGO_FIXTURE} ]]; then
    for sample_case in "${PORTAGE_SAMPLE_CASES[@]}"; do
        skip_case "${sample_case}" \
            "fixture is absent: ${PORTAGE_SAMPLE_PGO_FIXTURE}"
    done
elif [[ ! -v 'SELECTED_CAPABILITIES[clang-sample]' ]]; then
    for sample_case in "${PORTAGE_SAMPLE_CASES[@]}"; do
        skip_case "${sample_case}" \
            'requires the explicitly selected clang-sample capability because it runs perf and a training workload'
    done
elif ((EUID != 0)); then
    for sample_case in "${PORTAGE_SAMPLE_CASES[@]}"; do
        skip_case "${sample_case}" \
            'real disposable Portage sample-PGO integration requires a root driver invocation'
    done
elif ! require_commands awk b2sum bash chmod chown cp cmp cut date ebuild find \
    getent grep hostname id ln mkdir mktemp mv perf portageq python3 readelf \
    readlink realpath rm runuser sed sha256sum sha512sum sort stat sync tail \
    timeout touch xargs; then
    for sample_case in "${PORTAGE_SAMPLE_CASES[@]}"; do
        skip_case "${sample_case}" "${PREFLIGHT_REASON}"
    done
else
    # The sample fixture's validator sidecars deliberately remain bound to its
    # separately fsynced authoritative Work tree.  Preserve this run root so
    # its log and publication-context.tsv remain a durable index to that tree.
    PRESERVE_RUN_ROOT_FOR_EXTERNAL_AUTHORITY=1
    run_case portage-sample-pgo-integration \
        "${BASH_BIN}" -- "${PORTAGE_SAMPLE_PGO_FIXTURE}" \
        --portage-policy isolated-diagnostic \
        --output-dir "${RUN_ROOT}/portage-sample-pgo"
    run_case portage-sample-pgo-live-policy-integration "${BASH_BIN}" -- \
        "${PORTAGE_SAMPLE_PGO_FIXTURE}" --portage-policy live \
        --output-dir "${RUN_ROOT}/portage-sample-pgo-live-policy"
fi

BOLT_COMMAND_POLICY_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-bolt-command-policy.sh
if [[ ! -f ${BOLT_COMMAND_POLICY_FIXTURE} ]]; then
    skip_case bolt-command-policy \
        "fixture is absent: ${BOLT_COMMAND_POLICY_FIXTURE}"
elif ! require_commands bash grep; then
    skip_case bolt-command-policy "${PREFLIGHT_REASON}"
else
    run_case bolt-command-policy \
        "${BASH_BIN}" -- "${BOLT_COMMAND_POLICY_FIXTURE}"
fi

BOLT_TRANSACTION_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-bolt-transaction.sh
if [[ ! -f ${BOLT_TRANSACTION_FIXTURE} ]]; then
    skip_case bolt-transaction-fixture \
        "fixture is absent: ${BOLT_TRANSACTION_FIXTURE}"
elif ! require_commands awk bash chmod cmp cp grep mkdir mktemp mv ps rm sleep \
    timeout tr; then
    skip_case bolt-transaction-fixture "${PREFLIGHT_REASON}"
else
    run_case bolt-transaction-fixture \
        "${BASH_BIN}" -- "${BOLT_TRANSACTION_FIXTURE}"
fi

BOLT_HOOK_FIXTURE=${REPOSITORY_ROOT}/tests/optimization/test-bolt-hooks.sh
if [[ ! -f ${BOLT_HOOK_FIXTURE} ]]; then
    skip_case bolt-pre-strip-hooks \
        "fixture is absent: ${BOLT_HOOK_FIXTURE}"
elif ! require_commands awk bash cc chmod cmp cp find grep ln mkdir mktemp \
    objcopy python3 readelf readlink rm sed sha256sum sort stat; then
    skip_case bolt-pre-strip-hooks "${PREFLIGHT_REASON}"
else
    run_case bolt-pre-strip-hooks "${BASH_BIN}" -- "${BOLT_HOOK_FIXTURE}"
fi

DRIVER_SELF_TEST=${REPOSITORY_ROOT}/tests/optimization/test-run-optimization-tests.sh
if [[ -f ${DRIVER_SELF_TEST} ]]; then
    run_case driver-cli-self-test "${BASH_BIN}" -- "${DRIVER_SELF_TEST}"
else
    skip_case driver-cli-self-test "fixture is absent: ${DRIVER_SELF_TEST}"
fi

NO_BOOT_AUTOMATION_FIXTURE=${REPOSITORY_ROOT}/optimization/fixtures/recovery/test-no-boot-entry-automation.sh
if [[ ! -f ${NO_BOOT_AUTOMATION_FIXTURE} ]]; then
    skip_case no-boot-entry-automation \
        "fixture is absent: ${NO_BOOT_AUTOMATION_FIXTURE}"
elif ! require_commands bash grep sed; then
    skip_case no-boot-entry-automation "${PREFLIGHT_REASON}"
else
    run_case no-boot-entry-automation \
        "${BASH_BIN}" -- "${NO_BOOT_AUTOMATION_FIXTURE}"
fi

ROLLBACK_FIXTURE=${REPOSITORY_ROOT}/optimization/fixtures/recovery/test-rollback.sh
preflight_recovery_abi_lanes() {
    local clangxx_path gxx_path probe_source clang_output gcc_output
    local clang_compile_log clang_run_log gcc_compile_log gcc_run_log
    local status

    resolve_executable clang++ || {
        PREFLIGHT_REASON='Clang/libc++ ABI probe compiler is unavailable: clang++'
        return 1
    }
    clangxx_path=${RESOLVED_TOOL}
    resolve_executable g++ || {
        PREFLIGHT_REASON='GCC/libstdc++ ABI probe compiler is unavailable: g++'
        return 1
    }
    gxx_path=${RESOLVED_TOOL}

    probe_source=${RUN_ROOT}/preflight/recovery-cxx-abi-probe.cpp
    clang_output=${RUN_ROOT}/preflight/recovery-clang-libcxx-probe
    gcc_output=${RUN_ROOT}/preflight/recovery-gcc-libstdcxx-probe
    clang_compile_log=${RUN_ROOT}/preflight/recovery-clang-libcxx-compile.log
    clang_run_log=${RUN_ROOT}/preflight/recovery-clang-libcxx-run.log
    gcc_compile_log=${RUN_ROOT}/preflight/recovery-gcc-libstdcxx-compile.log
    gcc_run_log=${RUN_ROOT}/preflight/recovery-gcc-libstdcxx-run.log
    printf '%s\n' \
        '#include <iostream>' \
        'int main() { std::cout << "gentoo-recovery-cxx-abi-probe\n"; return 0; }' \
        >"${probe_source}"
    rm -f -- "${clang_output}" "${gcc_output}"

    if "${clangxx_path}" -O2 -pipe -stdlib=libc++ \
        "${probe_source}" -o "${clang_output}" >"${clang_compile_log}" 2>&1; then
        :
    else
        status=$?
        rm -f -- "${clang_output}"
        PREFLIGHT_REASON="Clang/libc++ ABI probe compilation failed (exit ${status}; log=${clang_compile_log})"
        return 1
    fi
    if [[ ! -x ${clang_output} ]]; then
        PREFLIGHT_REASON="Clang/libc++ ABI probe produced no executable (log=${clang_compile_log})"
        return 1
    fi
    if "${clang_output}" >"${clang_run_log}" 2>&1; then
        :
    else
        status=$?
        PREFLIGHT_REASON="Clang/libc++ ABI probe execution failed (exit ${status}; log=${clang_run_log})"
        return 1
    fi
    if [[ $(<"${clang_run_log}") != gentoo-recovery-cxx-abi-probe ]]; then
        PREFLIGHT_REASON="Clang/libc++ ABI probe returned unexpected output (log=${clang_run_log})"
        return 1
    fi

    if "${gxx_path}" -O2 -pipe \
        "${probe_source}" -o "${gcc_output}" >"${gcc_compile_log}" 2>&1; then
        :
    else
        status=$?
        rm -f -- "${gcc_output}"
        PREFLIGHT_REASON="GCC/libstdc++ ABI probe compilation failed (exit ${status}; log=${gcc_compile_log})"
        return 1
    fi
    if [[ ! -x ${gcc_output} ]]; then
        PREFLIGHT_REASON="GCC/libstdc++ ABI probe produced no executable (log=${gcc_compile_log})"
        return 1
    fi
    if "${gcc_output}" >"${gcc_run_log}" 2>&1; then
        :
    else
        status=$?
        PREFLIGHT_REASON="GCC/libstdc++ ABI probe execution failed (exit ${status}; log=${gcc_run_log})"
        return 1
    fi
    if [[ $(<"${gcc_run_log}") != gentoo-recovery-cxx-abi-probe ]]; then
        PREFLIGHT_REASON="GCC/libstdc++ ABI probe returned unexpected output (log=${gcc_run_log})"
        return 1
    fi

    {
        printf 'lane\tcompiler\tcompile_status\trun_status\toutput\n'
        printf 'clang-libcxx\t%s\t0\t0\t%s\n' "${clangxx_path}" "${clang_output}"
        printf 'gcc-libstdcxx\t%s\t0\t0\t%s\n' "${gxx_path}" "${gcc_output}"
    } >"${RUN_ROOT}/preflight/recovery-cxx-abi-probes.tsv"
    return 0
}

if [[ ! -f ${ROLLBACK_FIXTURE} ]]; then
    skip_case recovery-rollback-fixture "fixture is absent: ${ROLLBACK_FIXTURE}"
elif ! require_commands bash clang++ g++ readelf md5sum sha256sum \
    stat realpath flock rm; then
    skip_case recovery-rollback-fixture \
        "${PREFLIGHT_REASON}; the C++ ABI lane fixture was not run"
elif ! preflight_recovery_abi_lanes; then
    skip_case recovery-rollback-fixture \
        "${PREFLIGHT_REASON}; the C++ ABI lane fixture was not run"
else
    run_case recovery-rollback-fixture \
        "${BASH_BIN}" -- "${ROLLBACK_FIXTURE}"
fi

fi

preflight_perf_branch_stack() {
    local perf_binary=$1
    local label=$2
    local probe_data=${RUN_ROOT}/preflight/${label}.perf.data
    local probe_log=${RUN_ROOT}/preflight/${label}.perf.log
    if ! "${perf_binary}" record -q -e cycles:u -j any,u \
        -o "${probe_data}" -- /bin/true >"${probe_log}" 2>&1; then
        local diagnostic
        diagnostic=$(tail -n 4 -- "${probe_log}" | tr '\n' ' ')
        PREFLIGHT_REASON="perf branch-stack probe is unavailable: ${diagnostic:-unknown error}"
        return 1
    fi
    return 0
}

preflight_clang_ir() {
    local clangxx=${CLANGXX:-clang++}
    local clang_path bindir profdata lld
    require_commands portageq rg readelf sha256sum readlink awk sed find sort tee || return 1
    if ! resolve_executable "${clangxx}"; then
        PREFLIGHT_REASON="Clang C++ driver is unavailable: ${clangxx}"
        return 1
    fi
    clang_path=${RESOLVED_TOOL}
    bindir=$(dirname -- "${clang_path}")
    profdata=${LLVM_PROFDATA:-${bindir}/llvm-profdata}
    lld=${LLD:-${bindir}/ld.lld}
    if [[ ! -x ${profdata} ]]; then
        PREFLIGHT_REASON="paired llvm-profdata is unavailable: ${profdata}"
        return 1
    fi
    if [[ ! -x ${lld} ]]; then
        PREFLIGHT_REASON="paired ld.lld is unavailable: ${lld}"
        return 1
    fi
    return 0
}

preflight_clang_sample() {
    local llvm_root=${LLVM_ROOT:-/usr/lib/llvm/22/bin}
    local clangxx=${CLANGXX:-${llvm_root}/clang++}
    local profgen=${PROFGEN:-${llvm_root}/llvm-profgen}
    local profdata=${PROFDATA:-${llvm_root}/llvm-profdata}
    local readelf_tool=${READELF:-${llvm_root}/llvm-readelf}
    local perf_tool=${PERF:-/usr/bin/perf}
    local rg_tool=${RG:-/usr/bin/rg}
    local candidate
    for candidate in "${clangxx}" "${profgen}" "${profdata}" \
        "${readelf_tool}" "${perf_tool}" "${rg_tool}"; do
        if [[ ! -x ${candidate} ]]; then
            PREFLIGHT_REASON="required Clang sample-PGO tool is unavailable: ${candidate}"
            return 1
        fi
    done
    preflight_perf_branch_stack "${perf_tool}" clang-sample || return 1
    return 0
}

preflight_gcc() {
    require_commands gcc-config gcc python3 jq readelf nm rg sha256sum realpath || return 1
    [[ -d /var/db/pkg/sys-devel ]] || {
        PREFLIGHT_REASON='Gentoo installed-package metadata is absent: /var/db/pkg/sys-devel'
        return 1
    }
    if ! gcc-config -c >/dev/null 2>&1; then
        PREFLIGHT_REASON='gcc-config cannot report an active compiler selection'
        return 1
    fi
    return 0
}

preflight_rust() {
    local rustc=${RUSTC:-rustc}
    local rustc_path version_info llvm_major profdata profdata_version
    require_commands cargo rg sha256sum readelf awk sed head || return 1
    if ! resolve_executable "${rustc}"; then
        PREFLIGHT_REASON="rustc is unavailable: ${rustc}"
        return 1
    fi
    rustc_path=${RESOLVED_TOOL}
    if ! version_info=$("${rustc_path}" -vV 2>/dev/null); then
        PREFLIGHT_REASON="rustc -vV failed: ${rustc_path}"
        return 1
    fi
    llvm_major=$(sed -n 's/^LLVM version: \([0-9][0-9]*\).*/\1/p' <<<"${version_info}")
    [[ -n ${llvm_major} ]] || {
        PREFLIGHT_REASON='rustc does not report a bundled LLVM major'
        return 1
    }
    if [[ -n ${LLVM_PROFDATA:-} ]]; then
        profdata=${LLVM_PROFDATA}
    elif [[ -x /usr/lib/llvm/${llvm_major}/bin/llvm-profdata ]]; then
        profdata=/usr/lib/llvm/${llvm_major}/bin/llvm-profdata
    elif resolve_executable llvm-profdata; then
        profdata=${RESOLVED_TOOL}
    else
        PREFLIGHT_REASON="llvm-profdata for rustc LLVM ${llvm_major} is unavailable"
        return 1
    fi
    if ! profdata_version=$("${profdata}" --version 2>/dev/null) || \
        [[ ${profdata_version} != *"LLVM version ${llvm_major}."* ]]; then
        PREFLIGHT_REASON="llvm-profdata does not match rustc LLVM ${llvm_major}: ${profdata}"
        return 1
    fi
    return 0
}

preflight_go() {
    local go_tool=${GO:-go}
    local go_path goos goarch help_text
    require_commands rg sha256sum readelf || return 1
    if ! resolve_executable "${go_tool}"; then
        PREFLIGHT_REASON="Go tool is unavailable: ${go_tool}"
        return 1
    fi
    go_path=${RESOLVED_TOOL}
    goos=$("${go_path}" env GOOS 2>/dev/null) || {
        PREFLIGHT_REASON="go env GOOS failed: ${go_path}"
        return 1
    }
    goarch=$("${go_path}" env GOARCH 2>/dev/null) || {
        PREFLIGHT_REASON="go env GOARCH failed: ${go_path}"
        return 1
    }
    [[ ${goos}/${goarch} == linux/amd64 ]] || {
        PREFLIGHT_REASON="Go fixture requires linux/amd64, found ${goos}/${goarch}"
        return 1
    }
    help_text=$("${go_path}" help build 2>/dev/null) || {
        PREFLIGHT_REASON='go help build failed'
        return 1
    }
    [[ ${help_text} == *'-pgo'* ]] || {
        PREFLIGHT_REASON='active Go toolchain does not advertise -pgo support'
        return 1
    }
    return 0
}

preflight_bolt() {
    local runner=${REPOSITORY_ROOT}/optimization/fixtures/bolt/run.sh
    local perf_path
    [[ -f ${runner} ]] || {
        PREFLIGHT_REASON="combined BOLT fixture runner is absent: ${runner}"
        return 1
    }
    require_commands awk bash chmod clang cmp cp dirname file find getcap \
        getfattr grep head lddtree llvm-bolt merge-fdata mkdir nm objcopy perf \
        perf2bolt readelf readlink rm sed setfattr sha256sum sort stat strip tail \
        timeout tr mv xargs || return 1
    resolve_executable perf
    perf_path=${RESOLVED_TOOL}
    preflight_perf_branch_stack "${perf_path}" bolt || return 1
    return 0
}

resolve_capability_deadlines() {
    local capability=$1 capability_suffix timeout_override_name
    local kill_after_override_name
    capability_suffix=${capability^^}
    capability_suffix=${capability_suffix//-/_}
    timeout_override_name=TEST_CASE_TIMEOUT_SECONDS_${capability_suffix}
    kill_after_override_name=TEST_CASE_KILL_AFTER_SECONDS_${capability_suffix}

    RESOLVED_CASE_TIMEOUT_SECONDS=${TEST_CASE_TIMEOUT_SECONDS}
    RESOLVED_CASE_KILL_AFTER_SECONDS=${TEST_CASE_KILL_AFTER_SECONDS}
    if [[ -v ${timeout_override_name} ]]; then
        RESOLVED_CASE_TIMEOUT_SECONDS=${!timeout_override_name}
    fi
    if [[ -v ${kill_after_override_name} ]]; then
        RESOLVED_CASE_KILL_AFTER_SECONDS=${!kill_after_override_name}
    fi
}

run_capability() {
    local capability=$1
    local runner output
    if [[ -z ${SELECTED_CAPABILITIES[${capability}]:-} ]]; then
        if [[ ${MODE} != capabilities && ${MODE} != authoritative &&
              ${EXPLICIT_CAPABILITIES} -eq 0 ]]; then
            skip_case "capability:${capability}" \
                'selected mode excludes profiling/training; use --mode capabilities, --mode authoritative, or --capability'
        else
            skip_case "capability:${capability}" 'not selected by the explicit capability filter'
        fi
        return 0
    fi

    output=${RUN_ROOT}/capabilities/${capability}
    case ${capability} in
        clang-ir)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/pgo/clang-ir/run.sh
            if ! preflight_clang_ir; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        clang-sample)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/pgo/clang-sample/run.sh
            if ! preflight_clang_sample; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        gcc)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/pgo/gcc/run.sh
            if ! preflight_gcc; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        rust)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/pgo/rust/run.sh
            if ! preflight_rust; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        go)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/pgo/go/run.sh
            if ! preflight_go; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        bolt)
            runner=${REPOSITORY_ROOT}/optimization/fixtures/bolt/run.sh
            if ! preflight_bolt; then
                skip_case "capability:${capability}" "${PREFLIGHT_REASON}"
                return 0
            fi
            ;;
        *)
            printf 'internal error: unhandled capability %s\n' "${capability}" >&2
            exit 2
            ;;
    esac
    if [[ ! -f ${runner} ]]; then
        skip_case "capability:${capability}" "fixture runner is absent: ${runner}"
        return 0
    fi
    resolve_capability_deadlines "${capability}"
    run_case_with_deadline "capability:${capability}" \
        "${RESOLVED_CASE_TIMEOUT_SECONDS}" \
        "${RESOLVED_CASE_KILL_AFTER_SECONDS}" \
        "${BASH_BIN}" -- "${runner}" "${output}"
}

for capability in "${ALL_CAPABILITIES[@]}"; do
    run_capability "${capability}"
done

TOTAL_COUNT=$((PASS_COUNT + FAIL_COUNT + SKIP_COUNT))
SUBTEST_TOTAL_COUNT=$((
    REQUIRED_SUBTEST_PASS_COUNT + REQUIRED_SUBTEST_FAIL_COUNT +
    REQUIRED_SUBTEST_SKIP_COUNT + DIAGNOSTIC_SUBTEST_PASS_COUNT +
    DIAGNOSTIC_SUBTEST_FAIL_COUNT + DIAGNOSTIC_SUBTEST_SKIP_COUNT
))
EXIT_STATUS=0
((FAIL_COUNT == 0)) || EXIT_STATUS=1
if ((AUTHORITATIVE == 1)) && ((SKIP_COUNT != 0 || REQUIRED_SUBTEST_SKIP_COUNT != 0)); then
    EXIT_STATUS=1
    KEEP_TEMP=1
fi
if [[ ${MODE} == portable-complete || ${MODE} == authoritative || \
      ${AUTHORITATIVE} == 1 ]]; then
    contract_validation_mode=portable-complete
    if ((AUTHORITATIVE == 1)); then
        contract_validation_mode=authoritative
    fi
    set +e
    if [[ -n ${PYTHON_BIN} && -f ${PHASE2_EVIDENCE_TOOL} && \
          ! -L ${PHASE2_EVIDENCE_TOOL} && \
          -f ${AUTHORITATIVE_TEST_CONTRACT} && \
          ! -L ${AUTHORITATIVE_TEST_CONTRACT} ]]; then
        "${ENV_BIN}" -u PYTHONPYCACHEPREFIX PYTHONDONTWRITEBYTECODE=1 \
            "${PYTHON_BIN}" -I -B "${PHASE2_EVIDENCE_TOOL}" test-contract \
            --contract "${AUTHORITATIVE_TEST_CONTRACT}" \
            --results "${RESULTS_FILE}" --subtests "${SUBTESTS_FILE}" \
            --mode "${contract_validation_mode}" \
            >"${RUN_ROOT}/test-contract.log" 2>&1
        contract_status=$?
    else
        printf '%s\n' \
            'ERROR: exact test-contract validator, Python, or contract is unavailable' \
            >"${RUN_ROOT}/test-contract.log"
        contract_status=1
    fi
    set -e
    if ((contract_status != 0)); then
        EXIT_STATUS=1
        KEEP_TEMP=1
        printf 'FAIL: exact %s test topology/identity contract (log: %s)\n' \
            "${contract_validation_mode}" "${RUN_ROOT}/test-contract.log" >&2
        tail -n 120 -- "${RUN_ROOT}/test-contract.log" >&2 || true
    else
        printf 'TEST-CONTRACT: %s\n' "${RUN_ROOT}/test-contract.log"
    fi
fi
{
    printf 'mode=%s\n' "${MODE}"
    printf 'authoritative=%d\n' "${AUTHORITATIVE}"
    printf 'pass=%d\nfail=%d\nskip=%d\ntotal=%d\n' \
        "${PASS_COUNT}" "${FAIL_COUNT}" "${SKIP_COUNT}" "${TOTAL_COUNT}"
    printf 'required_subtest_pass=%d\nrequired_subtest_fail=%d\n' \
        "${REQUIRED_SUBTEST_PASS_COUNT}" "${REQUIRED_SUBTEST_FAIL_COUNT}"
    printf 'required_subtest_skip=%d\n' "${REQUIRED_SUBTEST_SKIP_COUNT}"
    printf 'mandatory_internal_skip=%d\n' "${MANDATORY_INTERNAL_SKIP_COUNT}"
    printf 'diagnostic_subtest_pass=%d\ndiagnostic_subtest_fail=%d\n' \
        "${DIAGNOSTIC_SUBTEST_PASS_COUNT}" "${DIAGNOSTIC_SUBTEST_FAIL_COUNT}"
    printf 'diagnostic_internal_skip=%d\nsubtest_total=%d\n' \
        "${DIAGNOSTIC_SUBTEST_SKIP_COUNT}" "${SUBTEST_TOTAL_COUNT}"
    printf 'exit_status=%d\n' "${EXIT_STATUS}"
    printf 'external_authority_index_preserved=%d\n' \
        "${PRESERVE_RUN_ROOT_FOR_EXTERNAL_AUTHORITY}"
    printf 'results=%s\n' "${RESULTS_FILE}"
    printf 'subtests=%s\n' "${SUBTESTS_FILE}"
} | tee "${RUN_ROOT}/summary.txt"

if ((PROVENANCE_ACTIVE)); then
    set +e
    "${ENV_BIN}" -u PYTHONPYCACHEPREFIX PYTHONDONTWRITEBYTECODE=1 \
        "${PROVENANCE_PYTHON}" -I -B "${PHASE2_EVIDENCE_TOOL}" \
        run-provenance-finish \
        --pending "${TEST_RUN_PROVENANCE_PENDING}" \
        --driver-bash-pid "${BASHPID}" \
        --results "${RESULTS_FILE}" --subtests "${SUBTESTS_FILE}" \
        --summary "${RUN_ROOT}/summary.txt" \
        --output "${TEST_RUN_PROVENANCE}" \
        >"${RUN_ROOT}/test-run-provenance-finalize.log" 2>&1
    provenance_status=$?
    set -e
    if ((provenance_status != 0)); then
        EXIT_STATUS=1
        KEEP_TEMP=1
        sed -i 's/^exit_status=.*/exit_status=1/' "${RUN_ROOT}/summary.txt"
        printf 'FAIL: test-run provenance finalization (exit %d; log: %s)\n' \
            "${provenance_status}" \
            "${RUN_ROOT}/test-run-provenance-finalize.log" >&2
        tail -n 80 -- "${RUN_ROOT}/test-run-provenance-finalize.log" >&2 || true
    else
        printf 'PROVENANCE: %s\n' "${TEST_RUN_PROVENANCE}"
    fi
fi

printf 'SUMMARY: PASS=%d FAIL=%d SKIP=%d TOTAL=%d EXIT=%d\n' \
    "${PASS_COUNT}" "${FAIL_COUNT}" "${SKIP_COUNT}" "${TOTAL_COUNT}" \
    "${EXIT_STATUS}"
exit "${EXIT_STATUS}"

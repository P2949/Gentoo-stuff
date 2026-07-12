#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)
TRANSACTION_HELPER=${REPOSITORY_ROOT}/optimization/fixtures/bolt/transaction.sh

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

initialize_case() {
    local root=$1
    TIMEOUT=${FAKE_TIMEOUT:?}
    TIMEOUT_KILL_AFTER_SECONDS=2
    TIMEOUT_STATUS_FILE=${root}/timeout-status.tsv
    COMMAND_LOG=${root}/commands.log
    : >"${COMMAND_LOG}"
    printf 'stage\ttimeout_seconds\tkill_after_seconds\texit_status\tresult\ttimed_out\tartifact\tpublished\tpartial_removed\n' \
        >"${TIMEOUT_STATUS_FILE}"
    # shellcheck source=optimization/fixtures/bolt/transaction.sh
    source "${TRANSACTION_HELPER}"
    bolt_transaction_install_traps
}

run_child_case() {
    local case_name=$1 root=$2
    local artifact=${root}/artifact partial=${root}/artifact.partial
    mkdir -p -- "${root}"
    initialize_case "${root}"
    case ${case_name} in
        generated-success)
            printf 'stale-final\n' >"${artifact}"
            printf 'stale-partial\n' >"${partial}"
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_generated_artifact generated-success 17 \
                    "${root}/stage.log" "${artifact}" "${partial}" \
                    "${PRODUCER}" generated "${partial}" 0 new-generated
            ;;
        generated-failure)
            printf 'stale-final\n' >"${artifact}"
            printf 'stale-partial\n' >"${partial}"
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_generated_artifact generated-failure 17 \
                    "${root}/stage.log" "${artifact}" "${partial}" \
                    "${PRODUCER}" generated "${partial}" 42 failed-generated
            ;;
        timeout-124)
            printf 'stale-final\n' >"${artifact}"
            printf 'stale-partial\n' >"${partial}"
            FAKE_TIMEOUT_RESULT=status124 \
                run_timed_generated_artifact timeout-124 17 \
                    "${root}/stage.log" "${artifact}" "${partial}" \
                    "${PRODUCER}" generated "${partial}" 0 unreachable
            ;;
        forced-kill-137)
            FAKE_TIMEOUT_RESULT=status137 \
                run_timed_generated_artifact forced-kill-137 17 \
                    "${root}/stage.log" "${artifact}" "${partial}" \
                    "${PRODUCER}" generated "${partial}" 0 unreachable
            ;;
        missing-generated)
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_generated_artifact missing-generated 17 \
                    "${root}/stage.log" "${artifact}" "${partial}" \
                    "${PRODUCER}" missing
            ;;
        stdout-success)
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_stdout_artifact stdout-success 17 "${artifact}" \
                    "${root}/stage.stderr.log" \
                    "${PRODUCER}" stdout 0 new-stdout
            ;;
        stdout-failure)
            printf 'stale-final\n' >"${artifact}"
            printf 'stale-partial\n' >"${partial}"
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_stdout_artifact stdout-failure 17 "${artifact}" \
                    "${root}/stage.stderr.log" \
                    "${PRODUCER}" stdout 23 failed-stdout
            ;;
        status-boundary-success)
            bolt_transaction_after_status_hook() {
                kill -TERM "${BASHPID}"
            }
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_generated_artifact "${case_name}" 17 \
                    "${root}/stage.log" "${artifact}" "${partial}" \
                    "${PRODUCER}" generated "${partial}" 0 status-published
            fail "${case_name}: status-boundary signal did not terminate the shell"
            ;;
        status-boundary-failure)
            bolt_transaction_after_status_hook() {
                kill -TERM "${BASHPID}"
            }
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_generated_artifact "${case_name}" 17 \
                    "${root}/stage.log" "${artifact}" "${partial}" \
                    "${PRODUCER}" generated "${partial}" 42 status-failed
            fail "${case_name}: status-boundary signal did not terminate the shell"
            ;;
        status-boundary-missing)
            bolt_transaction_after_status_hook() {
                kill -TERM "${BASHPID}"
            }
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_generated_artifact "${case_name}" 17 \
                    "${root}/stage.log" "${artifact}" "${partial}" \
                    "${PRODUCER}" missing
            fail "${case_name}: status-boundary signal did not terminate the shell"
            ;;
        publish-boundary-*)
            local boundary=${case_name#publish-boundary-}
            local boundary_signal boundary_status
            case ${boundary} in
                EXIT)
                    boundary_signal=EXIT
                    boundary_status=88
                    ;;
                HUP)
                    boundary_signal=HUP
                    boundary_status=129
                    ;;
                INT)
                    boundary_signal=INT
                    boundary_status=130
                    ;;
                TERM)
                    boundary_signal=TERM
                    boundary_status=143
                    ;;
                *) fail "unknown publish-boundary case: ${case_name}" ;;
            esac
            bolt_transaction_after_rename_hook() {
                if [[ ${boundary_signal} == EXIT ]]; then
                    exit "${boundary_status}"
                fi
                kill -s "${boundary_signal}" "${BASHPID}"
            }
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_generated_artifact "${case_name}" 17 \
                    "${root}/stage.log" "${artifact}" "${partial}" \
                    "${PRODUCER}" generated "${partial}" 0 boundary-published
            fail "${case_name}: boundary signal did not terminate the transaction shell"
            ;;
        interrupt-*)
            local signal=${case_name#interrupt-}
            local expected_status
            TIMEOUT=$(command -v timeout)
            printf 'keep-unrelated\n' >"${root}/unrelated.partial"
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_stdout_artifact published 17 "${root}/published" \
                    "${root}/published.stderr.log" \
                    "${PRODUCER}" stdout 0 published-final
            case ${signal} in
                EXIT)
                    expected_status=88
                    trap 'exit 88' USR1
                    signal=USR1
                    ;;
                HUP) expected_status=129 ;;
                INT) expected_status=130 ;;
                TERM) expected_status=143 ;;
                *) fail "unknown interrupt case: ${case_name}" ;;
            esac
            printf '%s\n' "${expected_status}" >"${root}/expected-status"
            local target_pid=${BASHPID}
            (
                while [[ ! -s ${root}/workload-process-group ]]; do
                    sleep 0.01
                done
                kill -s "${signal}" "${target_pid}"
            ) &
            FAKE_TIMEOUT_RESULT=passthrough \
                run_timed_generated_artifact "interrupt-${case_name#interrupt-}" 17 \
                    "${root}/interrupt.log" "${root}/active" \
                    "${root}/active.partial" \
                    "${STUBBORN_PRODUCER}" "${root}/active.partial" \
                    "${root}/workload-process-group"
            fail "${case_name}: interrupt did not terminate the transaction shell"
            ;;
        *)
            fail "unknown child case: ${case_name}"
            ;;
    esac
}

if [[ ${1:-} == --case ]]; then
    [[ $# -eq 3 ]] || fail 'internal --case usage requires a name and root'
    run_child_case "$2" "$3"
    exit 0
fi

[[ $# -eq 0 ]] || fail 'this test does not accept arguments'
[[ -r ${TRANSACTION_HELPER} ]] || fail "transaction helper is absent: ${TRANSACTION_HELPER}"
for required_tool in awk bash cmp cp grep kill mkdir mktemp mv ps rm sleep timeout tr; do
    command -v -- "${required_tool}" >/dev/null || fail "missing test prerequisite: ${required_tool}"
done

FIXTURE=$(mktemp -d /tmp/gentoo-optimization-bolt-transaction.XXXXXXXX)
trap 'rm -rf -- "${FIXTURE}"' EXIT
FAKE_TIMEOUT=${FIXTURE}/fake-timeout
PRODUCER=${FIXTURE}/producer
STUBBORN_PRODUCER=${FIXTURE}/stubborn-producer
export FAKE_TIMEOUT PRODUCER STUBBORN_PRODUCER

# Parse the GNU timeout options used by the helper, then either execute the
# child or return a deterministic synthetic deadline/kill status.
# The single-quoted expressions are emitted literally into the fake tool.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -u' \
    'while (($#)); do' \
    '    case $1 in' \
    '        --verbose|--signal=*|--kill-after=*) shift ;;' \
    '        *) break ;;' \
    '    esac' \
    'done' \
    '[[ $# -ge 2 ]] || exit 99' \
    'shift' \
    'case ${FAKE_TIMEOUT_RESULT:-passthrough} in' \
    '    passthrough) exec "$@" ;;' \
    '    status124) exit 124 ;;' \
    '    status137) exit 137 ;;' \
    '    *) exit 98 ;;' \
    'esac' >"${FAKE_TIMEOUT}"

# The single-quoted expressions are emitted literally into the producer.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -u' \
    'mode=$1; shift' \
    'case ${mode} in' \
    '    generated)' \
    '        output=$1 status=$2 content=$3' \
    '        printf "%s\\n" "${content}" >"${output}"' \
    '        exit "${status}"' \
    '        ;;' \
    '    stdout)' \
    '        status=$1 content=$2' \
    '        printf "%s\\n" "${content}"' \
    '        exit "${status}"' \
    '        ;;' \
    '    missing) exit 0 ;;' \
    '    *) exit 97 ;;' \
    'esac' >"${PRODUCER}"

# The single-quoted expressions are emitted literally into the producer.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -u' \
    'partial=$1 marker=$2' \
    'trap "" HUP INT TERM' \
    '(' \
    '    trap "" HUP INT TERM' \
    '    while :; do sleep 1; done' \
    ') &' \
    'descendant=$!' \
    'pgid=$(ps -o pgid= -p "${BASHPID}" | tr -d "[:space:]")' \
    'printf "%s\\t%s\\t%s\\n" "${BASHPID}" "${descendant}" "${pgid}" >"${marker}"' \
    'printf "unpublished\\n" >"${partial}"' \
    'while :; do sleep 1; done' >"${STUBBORN_PRODUCER}"
chmod 0700 -- "${FAKE_TIMEOUT}" "${PRODUCER}" "${STUBBORN_PRODUCER}"

run_expected_status() {
    local case_name=$1 expected_status=$2 root
    local status
    root=${FIXTURE}/cases/${case_name}
    set +e
    bash -- "$0" --case "${case_name}" "${root}" \
        >"${FIXTURE}/${case_name}.stdout" 2>"${FIXTURE}/${case_name}.stderr"
    status=$?
    set -e
    [[ ${status} -eq ${expected_status} ]] || \
        fail "${case_name}: expected exit ${expected_status}, received ${status}"
}

assert_exact_single_row() {
    local case_name=$1 status=$2 result=$3 timed_out=$4 published=$5 removed=$6
    local root=${FIXTURE}/cases/${case_name}
    printf '%s\t17\t2\t%s\t%s\t%s\t%s/artifact\t%s\t%s\n' \
        "${case_name}" "${status}" "${result}" "${timed_out}" "${root}" \
        "${published}" "${removed}" >"${root}/expected-row"
    awk 'NR > 1' "${root}/timeout-status.tsv" >"${root}/actual-row"
    cmp -s -- "${root}/expected-row" "${root}/actual-row" || \
        fail "${case_name}: timeout-status.tsv row differs from the exact contract"
}

run_expected_status generated-success 0
assert_exact_single_row generated-success 0 completed false true true
grep -Fxq new-generated "${FIXTURE}/cases/generated-success/artifact" || \
    fail 'generated success did not atomically replace stale output'
[[ ! -e ${FIXTURE}/cases/generated-success/artifact.partial ]] || \
    fail 'generated success retained its partial output'

run_expected_status generated-failure 1
assert_exact_single_row generated-failure 42 command-failed false false true
[[ ! -e ${FIXTURE}/cases/generated-failure/artifact && \
   ! -e ${FIXTURE}/cases/generated-failure/artifact.partial ]] || \
    fail 'ordinary failure retained stale final or partial output'

run_expected_status timeout-124 1
assert_exact_single_row timeout-124 124 deadline-exceeded true false true
run_expected_status forced-kill-137 1
assert_exact_single_row forced-kill-137 137 forced-kill-or-sigkill true false true
run_expected_status missing-generated 1
assert_exact_single_row missing-generated 0 missing-artifact false false true

run_expected_status stdout-success 0
assert_exact_single_row stdout-success 0 completed false true true
grep -Fxq new-stdout "${FIXTURE}/cases/stdout-success/artifact" || \
    fail 'stdout success did not atomically publish its exact output'
run_expected_status stdout-failure 1
assert_exact_single_row stdout-failure 23 command-failed false false true
[[ ! -e ${FIXTURE}/cases/stdout-failure/artifact && \
   ! -e ${FIXTURE}/cases/stdout-failure/artifact.partial ]] || \
    fail 'stdout failure retained stale final or partial output'

# Signal in the deterministic hook immediately after mv has removed the
# partial and published the final, but before active state or evidence is
# normally cleared. Every trap must recognize publication and emit one
# completed row rather than a false unpublished/interrupted row.
for boundary_name in EXIT HUP INT TERM; do
    case ${boundary_name} in
        EXIT) boundary_status=88 ;;
        HUP) boundary_status=129 ;;
        INT) boundary_status=130 ;;
        TERM) boundary_status=143 ;;
    esac
    case_name=publish-boundary-${boundary_name}
    root=${FIXTURE}/cases/${case_name}
    run_expected_status "${case_name}" "${boundary_status}"
    grep -Fxq boundary-published "${root}/artifact" || \
        fail "${case_name}: published final was lost at the signal boundary"
    [[ ! -e ${root}/artifact.partial ]] || \
        fail "${case_name}: partial survived completed publication"
    assert_exact_single_row "${case_name}" 0 completed false true true
done

# Once a row has been appended it is the bookkeeping commit marker. A signal
# before active state is cleared must observe that one row and never append a
# duplicate, across success, ordinary failure, and missing-artifact paths.
run_expected_status status-boundary-success 143
assert_exact_single_row status-boundary-success 0 completed false true true
grep -Fxq status-published "${FIXTURE}/cases/status-boundary-success/artifact" || \
    fail 'status-boundary success lost its published final'

run_expected_status status-boundary-failure 143
assert_exact_single_row status-boundary-failure 42 command-failed false false true
[[ ! -e ${FIXTURE}/cases/status-boundary-failure/artifact && \
   ! -e ${FIXTURE}/cases/status-boundary-failure/artifact.partial ]] || \
    fail 'status-boundary failure retained an output'

run_expected_status status-boundary-missing 143
assert_exact_single_row status-boundary-missing 0 missing-artifact false false true
[[ ! -e ${FIXTURE}/cases/status-boundary-missing/artifact && \
   ! -e ${FIXTURE}/cases/status-boundary-missing/artifact.partial ]] || \
    fail 'status-boundary missing-artifact path retained an output'

assert_process_group_gone() {
    local marker=$1
    local producer_pid descendant_pid process_group
    IFS=$'\t' read -r producer_pid descendant_pid process_group <"${marker}"
    [[ ${producer_pid} =~ ^[0-9]+$ && ${descendant_pid} =~ ^[0-9]+$ && \
       ${process_group} =~ ^[0-9]+$ ]] || fail 'interruption marker is malformed'
    for _ in {1..100}; do
        if ! ps -eo pgid=,stat= | awk -v pgid="${process_group}" \
            '$1 == pgid && $2 !~ /^Z/ { found = 1 } END { exit found ? 0 : 1 }'; then
            return 0
        fi
        sleep 0.02
    done
    fail "interrupted workload process group ${process_group} still has a live member"
}

for interrupt_name in EXIT HUP INT TERM; do
    case ${interrupt_name} in
        EXIT) interrupt_status=88 ;;
        HUP) interrupt_status=129 ;;
        INT) interrupt_status=130 ;;
        TERM) interrupt_status=143 ;;
    esac
    case_name=interrupt-${interrupt_name}
    root=${FIXTURE}/cases/${case_name}
    run_expected_status "${case_name}" "${interrupt_status}"
    grep -Fxq published-final "${root}/published" || \
        fail "${case_name}: an already published final was changed"
    grep -Fxq keep-unrelated "${root}/unrelated.partial" || \
        fail "${case_name}: cleanup removed an unrelated partial"
    [[ ! -e ${root}/active && ! -e ${root}/active.partial ]] || \
        fail "${case_name}: interrupted active transaction retained output"
    assert_process_group_gone "${root}/workload-process-group"
    printf 'published\t17\t2\t0\tcompleted\tfalse\t%s/published\ttrue\ttrue\n' \
        "${root}" >"${root}/expected-interrupt-rows"
    printf 'interrupt-%s\t17\t2\t%s\tinterrupted\tfalse\t%s/active\tfalse\ttrue\n' \
        "${interrupt_name}" "${interrupt_status}" "${root}" \
        >>"${root}/expected-interrupt-rows"
    awk 'NR > 1' "${root}/timeout-status.tsv" >"${root}/actual-interrupt-rows"
    cmp -s -- "${root}/expected-interrupt-rows" "${root}/actual-interrupt-rows" || \
        fail "${case_name}: interruption evidence differs from the exact contract"
done

# Prove that the declared class/stage registry, rather than a literal count,
# is the authority for final status evidence membership and uniqueness.
REGISTRY_ROOT=${FIXTURE}/stage-registry
mkdir -p -- "${REGISTRY_ROOT}"
# shellcheck source=optimization/fixtures/bolt/transaction.sh
source "${TRANSACTION_HELPER}"
EXPECTED_REGISTRY=${REGISTRY_ROOT}/expected.txt
VALID_STATUS=${REGISTRY_ROOT}/valid.tsv
bolt_transaction_write_stage_registry "${EXPECTED_REGISTRY}"
printf 'stage\ttimeout_seconds\tkill_after_seconds\texit_status\tresult\ttimed_out\tartifact\tpublished\tpartial_removed\n' \
    >"${VALID_STATUS}"
while IFS= read -r declared_stage; do
    printf '%s\t17\t2\t0\tcompleted\tfalse\tartifact\ttrue\ttrue\n' \
        "${declared_stage}"
done <"${EXPECTED_REGISTRY}" >>"${VALID_STATUS}"
bolt_transaction_validate_stage_evidence "${VALID_STATUS}" \
    "${EXPECTED_REGISTRY}" "${REGISTRY_ROOT}/valid-actual.txt"
[[ ${BOLT_TRANSACTION_TIMED_STAGE_COUNT} -eq \
   ${BOLT_TRANSACTION_EXPECTED_STAGE_COUNT} ]] || \
    fail 'valid registry evidence did not retain its derived stage count'

assert_registry_rejected() {
    local label=$1 status_file=$2 diagnostic=$3
    if (bolt_transaction_validate_stage_evidence "${status_file}" \
        "${EXPECTED_REGISTRY}" "${REGISTRY_ROOT}/${label}-actual.txt") \
        >"${REGISTRY_ROOT}/${label}.log" 2>&1; then
        fail "stage registry unexpectedly accepted ${label} evidence"
    fi
    grep -Fq -- "${diagnostic}" "${REGISTRY_ROOT}/${label}.log" || \
        fail "${label} registry rejection lacks its exact diagnostic"
}

awk -F '\t' 'BEGIN { OFS = FS } NR == 2 { $1 = "unknown:stage" } { print }' \
    "${VALID_STATUS}" >"${REGISTRY_ROOT}/unknown.tsv"
assert_registry_rejected unknown "${REGISTRY_ROOT}/unknown.tsv" \
    'unknown, missing, duplicated, or out-of-order timed stage'

awk 'NR != 2' "${VALID_STATUS}" >"${REGISTRY_ROOT}/missing.tsv"
assert_registry_rejected missing "${REGISTRY_ROOT}/missing.tsv" \
    'stages instead of declared'

cp -- "${VALID_STATUS}" "${REGISTRY_ROOT}/duplicate.tsv"
awk 'NR == 2' "${VALID_STATUS}" >>"${REGISTRY_ROOT}/duplicate.tsv"
assert_registry_rejected duplicate "${REGISTRY_ROOT}/duplicate.tsv" \
    'duplicate timed-stage row'

awk 'NR == 2 { first = $0; next } NR == 3 { print; print first; next } { print }' \
    "${VALID_STATUS}" >"${REGISTRY_ROOT}/out-of-order.tsv"
assert_registry_rejected out-of-order "${REGISTRY_ROOT}/out-of-order.tsv" \
    'unknown, missing, duplicated, or out-of-order timed stage'

printf 'PASS: BOLT transaction publication, failure, timeout, and interruption fixture\n'

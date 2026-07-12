#!/usr/bin/env bash

# Sourceable, fail-closed publication helpers for bounded BOLT fixture stages.
# The caller provides fail(), TIMEOUT, TIMEOUT_KILL_AFTER_SECONDS,
# TIMEOUT_STATUS_FILE, and COMMAND_LOG before invoking these functions.

BOLT_TRANSACTION_ACTIVE_STAGE=
BOLT_TRANSACTION_ACTIVE_LIMIT=
BOLT_TRANSACTION_ACTIVE_ARTIFACT=
BOLT_TRANSACTION_ACTIVE_PARTIAL=
BOLT_TRANSACTION_ACTIVE_PID=
BOLT_TRANSACTION_LAST_STATUS=
readonly -a BOLT_FIXTURE_CLASSES=(executable pie dso)
readonly -a BOLT_TIMED_STAGE_SUFFIXES=(
    perf-record-mode1
    perf-record-mode2
    perf-report
    perf-buildid-list-mode1
    perf-buildid-list-mode2
    perf2bolt-mode1
    perf2bolt-mode2
    merge-fdata
    llvm-bolt
)
BOLT_TRANSACTION_EXPECTED_STAGE_COUNT=
BOLT_TRANSACTION_TIMED_STAGE_COUNT=

bolt_transaction_write_stage_registry() {
    local destination=$1 fixture_class stage_suffix
    : >"${destination}"
    for fixture_class in "${BOLT_FIXTURE_CLASSES[@]}"; do
        for stage_suffix in "${BOLT_TIMED_STAGE_SUFFIXES[@]}"; do
            printf '%s:%s\n' "${fixture_class}" "${stage_suffix}"
        done
    done >"${destination}"
    BOLT_TRANSACTION_EXPECTED_STAGE_COUNT=$((${#BOLT_FIXTURE_CLASSES[@]} * ${#BOLT_TIMED_STAGE_SUFFIXES[@]}))
    [[ $(awk 'END { print NR + 0 }' "${destination}") -eq \
        ${BOLT_TRANSACTION_EXPECTED_STAGE_COUNT} ]] || \
        fail 'declared timed-stage registry count is inconsistent'
    awk 'seen[$0]++ { exit 1 }' "${destination}" || \
        fail 'declared timed-stage registry contains a duplicate stage'
}

bolt_transaction_validate_stage_evidence() {
    local status_file=$1 expected_stages=$2 actual_stages=$3
    awk -F '\t' 'NR > 1 { print $1 }' "${status_file}" >"${actual_stages}"
    awk 'seen[$0]++ { exit 1 }' "${actual_stages}" || \
        fail 'timeout evidence contains a duplicate timed-stage row'
    BOLT_TRANSACTION_TIMED_STAGE_COUNT=$(awk -F '\t' \
        'NR > 1 { count += 1 } END { print count + 0 }' "${status_file}")
    [[ ${BOLT_TRANSACTION_TIMED_STAGE_COUNT} -eq \
        ${BOLT_TRANSACTION_EXPECTED_STAGE_COUNT} ]] || \
        fail "timeout evidence covers ${BOLT_TRANSACTION_TIMED_STAGE_COUNT} stages instead of declared ${BOLT_TRANSACTION_EXPECTED_STAGE_COUNT}"
    cmp -s -- "${expected_stages}" "${actual_stages}" || \
        fail 'timeout evidence has an unknown, missing, duplicated, or out-of-order timed stage'
}

record_timeout_status() {
    local stage=$1 limit=$2 status=$3 result=$4 timed_out=$5 artifact=$6
    local published=$7 partial_removed=$8
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${stage}" "${limit}" "${TIMEOUT_KILL_AFTER_SECONDS}" "${status}" \
        "${result}" "${timed_out}" "${artifact}" "${published}" \
        "${partial_removed}" >>"${TIMEOUT_STATUS_FILE}"
}

log_timed_command() {
    local stage=$1 limit=$2
    shift 2
    {
        printf 'TIMED_RUN stage=%q timeout_seconds=%q kill_after_seconds=%q' \
            "${stage}" "${limit}" "${TIMEOUT_KILL_AFTER_SECONDS}"
        printf ' %q' "${TIMEOUT}" --verbose --signal=TERM \
            --kill-after="${TIMEOUT_KILL_AFTER_SECONDS}s" "${limit}s" "$@"
        printf '\n'
    } >>"${COMMAND_LOG}"
}

bolt_transaction_clear_active() {
    BOLT_TRANSACTION_ACTIVE_STAGE=
    BOLT_TRANSACTION_ACTIVE_LIMIT=
    BOLT_TRANSACTION_ACTIVE_ARTIFACT=
    BOLT_TRANSACTION_ACTIVE_PARTIAL=
    BOLT_TRANSACTION_ACTIVE_PID=
}

bolt_transaction_remove_active_partial() {
    local removed=false
    if [[ -n ${BOLT_TRANSACTION_ACTIVE_PARTIAL} ]]; then
        rm -f -- "${BOLT_TRANSACTION_ACTIVE_PARTIAL}"
        [[ ! -e ${BOLT_TRANSACTION_ACTIVE_PARTIAL} ]] && removed=true
    fi
    printf '%s\n' "${removed}"
}

bolt_transaction_publication_is_complete() {
    [[ -n ${BOLT_TRANSACTION_ACTIVE_ARTIFACT} && \
       -s ${BOLT_TRANSACTION_ACTIVE_ARTIFACT} && \
       -n ${BOLT_TRANSACTION_ACTIVE_PARTIAL} && \
       ! -e ${BOLT_TRANSACTION_ACTIVE_PARTIAL} ]]
}

# Tests override this no-op hook to inject a signal in the exact boundary after
# rename(2) publication but before normal state clearing/status recording.
# shellcheck disable=SC2329
bolt_transaction_after_rename_hook() {
    :
}

# Tests override this no-op hook to inject a signal after the durable status
# append but before active in-memory state is cleared.
# shellcheck disable=SC2329
bolt_transaction_after_status_hook() {
    :
}

bolt_transaction_active_status_count() {
    awk -F '\t' -v stage="${BOLT_TRANSACTION_ACTIVE_STAGE}" \
        'NR > 1 && $1 == stage { count += 1 } END { print count + 0 }' \
        "${TIMEOUT_STATUS_FILE}"
}

bolt_transaction_terminate_active_group() {
    local pid=${BOLT_TRANSACTION_ACTIVE_PID}
    [[ -n ${pid} ]] || return 0

    # GNU timeout creates a process group whose ID is its own PID unless
    # --foreground is requested. Signal that group so perf and all workload
    # descendants cannot survive an interrupted fixture shell.
    kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM -- "${pid}" 2>/dev/null || true
    kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL -- "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
}

bolt_transaction_interrupt_active() {
    local status=$1
    local stage=${BOLT_TRANSACTION_ACTIVE_STAGE}
    local limit=${BOLT_TRANSACTION_ACTIVE_LIMIT}
    local artifact=${BOLT_TRANSACTION_ACTIVE_ARTIFACT}
    local partial_removed status_count
    [[ -n ${stage} ]] || return 0

    status_count=$(bolt_transaction_active_status_count)
    if [[ ${status_count} -eq 1 ]]; then
        bolt_transaction_clear_active
        return 0
    fi
    if [[ ${status_count} -gt 1 ]]; then
        printf 'FAIL: active stage already has %s status rows: %s\n' \
            "${status_count}" "${stage}" >&2
        bolt_transaction_clear_active
        return 0
    fi

    if bolt_transaction_publication_is_complete; then
        record_timeout_status "${stage}" "${limit}" 0 completed false \
            "${artifact}" true true
        bolt_transaction_clear_active
        return 0
    fi

    bolt_transaction_terminate_active_group
    partial_removed=$(bolt_transaction_remove_active_partial)
    record_timeout_status "${stage}" "${limit}" "${status}" interrupted false \
        "${artifact}" false "${partial_removed}"
    bolt_transaction_clear_active
}

bolt_transaction_exit_trap() {
    local status=$1
    trap - EXIT
    trap '' HUP INT TERM
    bolt_transaction_interrupt_active "${status}"
    exit "${status}"
}

bolt_transaction_signal_trap() {
    local status=$1
    trap - EXIT
    trap '' HUP INT TERM
    bolt_transaction_interrupt_active "${status}"
    exit "${status}"
}

bolt_transaction_install_traps() {
    trap 'bolt_transaction_exit_trap "$?"' EXIT
    trap 'bolt_transaction_signal_trap 129' HUP
    trap 'bolt_transaction_signal_trap 130' INT
    trap 'bolt_transaction_signal_trap 143' TERM
}

bolt_transaction_start() {
    local stage=$1 limit=$2 artifact=$3 partial=$4
    [[ -z ${BOLT_TRANSACTION_ACTIVE_STAGE} ]] || \
        fail "${stage}: internal error: another transaction is active"
    BOLT_TRANSACTION_ACTIVE_STAGE=${stage}
    BOLT_TRANSACTION_ACTIVE_LIMIT=${limit}
    BOLT_TRANSACTION_ACTIVE_ARTIFACT=${artifact}
    BOLT_TRANSACTION_ACTIVE_PARTIAL=${partial}
    BOLT_TRANSACTION_ACTIVE_PID=
}

bolt_transaction_publish() {
    local stage=$1 limit=$2 artifact=$3 partial=$4
    mv -- "${partial}" "${artifact}"
    bolt_transaction_after_rename_hook "${stage}" "${artifact}" "${partial}"
    record_timeout_status "${stage}" "${limit}" 0 completed false \
        "${artifact}" true true
    bolt_transaction_after_status_hook "${stage}" completed
    bolt_transaction_clear_active
}

bolt_transaction_wait() {
    set +e
    "${TIMEOUT}" --verbose --signal=TERM \
        --kill-after="${TIMEOUT_KILL_AFTER_SECONDS}s" "$@" &
    BOLT_TRANSACTION_ACTIVE_PID=$!
    wait "${BOLT_TRANSACTION_ACTIVE_PID}"
    BOLT_TRANSACTION_LAST_STATUS=$?
    BOLT_TRANSACTION_ACTIVE_PID=
    set -e
}

timed_failure() {
    local stage=$1 limit=$2 status=$3 artifact=$4 partial=$5
    local result timed_out=false partial_removed=false
    case ${status} in
        124)
            result=deadline-exceeded
            timed_out=true
            ;;
        137)
            result=forced-kill-or-sigkill
            timed_out=true
            ;;
        *)
            result=command-failed
            ;;
    esac
    rm -f -- "${artifact}" "${partial}"
    if [[ ! -e ${artifact} && ! -e ${partial} ]]; then
        partial_removed=true
    fi
    record_timeout_status "${stage}" "${limit}" "${status}" "${result}" \
        "${timed_out}" "${artifact}" false "${partial_removed}"
    bolt_transaction_after_status_hook "${stage}" "${result}"
    bolt_transaction_clear_active
    if [[ ${timed_out} == true ]]; then
        fail "${stage}: timed operation exceeded ${limit}s (exit ${status}); unpublished partial output was removed"
    fi
    fail "${stage}: timed operation failed with exit ${status}; unpublished partial output was removed"
}

# Run a command that writes its artifact to an explicit, caller-supplied
# partial path. The final path is never visible until an atomic rename.
run_timed_generated_artifact() {
    local stage=$1 limit=$2 log=$3 artifact=$4 partial=$5
    shift 5
    local status
    [[ ${partial} == "${artifact}.partial" ]] || \
        fail "${stage}: internal error: partial path is not tied to its final artifact"
    rm -f -- "${artifact}" "${partial}"
    log_timed_command "${stage}" "${limit}" "$@"
    bolt_transaction_start "${stage}" "${limit}" "${artifact}" "${partial}"
    bolt_transaction_wait "${limit}s" "$@" >"${log}" 2>&1
    status=${BOLT_TRANSACTION_LAST_STATUS}
    ((status == 0)) || \
        timed_failure "${stage}" "${limit}" "${status}" "${artifact}" "${partial}"
    if [[ ! -s ${partial} ]]; then
        rm -f -- "${artifact}" "${partial}"
        record_timeout_status "${stage}" "${limit}" 0 missing-artifact false \
            "${artifact}" false true
        bolt_transaction_after_status_hook "${stage}" missing-artifact
        bolt_transaction_clear_active
        fail "${stage}: command exited successfully without a nonempty partial artifact"
    fi
    bolt_transaction_publish "${stage}" "${limit}" "${artifact}" "${partial}"
}

# Variant for tools such as perf report and merge-fdata whose artifact is
# emitted on stdout. Stderr remains a separate diagnostic log.
run_timed_stdout_artifact() {
    local stage=$1 limit=$2 artifact=$3 stderr_log=$4
    shift 4
    local partial=${artifact}.partial
    local status
    rm -f -- "${artifact}" "${partial}"
    log_timed_command "${stage}" "${limit}" "$@"
    bolt_transaction_start "${stage}" "${limit}" "${artifact}" "${partial}"
    bolt_transaction_wait "${limit}s" "$@" >"${partial}" 2>"${stderr_log}"
    status=${BOLT_TRANSACTION_LAST_STATUS}
    ((status == 0)) || \
        timed_failure "${stage}" "${limit}" "${status}" "${artifact}" "${partial}"
    if [[ ! -s ${partial} ]]; then
        rm -f -- "${artifact}" "${partial}"
        record_timeout_status "${stage}" "${limit}" 0 missing-artifact false \
            "${artifact}" false true
        bolt_transaction_after_status_hook "${stage}" missing-artifact
        bolt_transaction_clear_active
        fail "${stage}: command exited successfully without a nonempty stdout artifact"
    fi
    bolt_transaction_publish "${stage}" "${limit}" "${artifact}" "${partial}"
}

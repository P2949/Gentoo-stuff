#!/usr/bin/env bash
# The fixture deliberately supplies variables/functions that are consumed only
# by the dynamically selected QA hook below.
# shellcheck disable=SC1090,SC2034,SC2329
set -Eeuo pipefail
IFS=$'\n\t'

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
HOOK=${ROOT}/portage/install-qa-check.d/zz-gentoo-optimization-bolt
TMP=$(mktemp -d "${TMPDIR:-/tmp}/gentoo-opt-qa-hook.XXXXXX")
trap 'rm -rf -- "${TMP}"' EXIT HUP INT TERM
PASS=0
FAIL=0

run_case() {
    local name=$1
    shift
    if ("$@"); then
        printf 'PASS: %s\n' "${name}"
        PASS=$((PASS + 1))
    else
        printf 'FAIL: %s\n' "${name}" >&2
        FAIL=$((FAIL + 1))
    fi
}

new_marker() {
    local name=$1
    PORTAGE_TMPDIR=${TMP}/${name}
    PORTAGE_BUILDDIR=${PORTAGE_TMPDIR}/portage/app-test/fixture-1
    mkdir -p -- "${PORTAGE_BUILDDIR}"
    : > "${PORTAGE_BUILDDIR}/.installed"
    export PORTAGE_TMPDIR PORTAGE_BUILDDIR
}

case_off_is_noop() (
    new_marker off
    GENTOO_OPT_MODE=off
    source "${HOOK}"
    [[ -f ${PORTAGE_BUILDDIR}/.installed ]]
)

case_lost_active_state_is_fatal() (
    new_marker lost
    GENTOO_OPT_MODE=bolt-capture
    unset GENTOO_OPT_ACTIVE_BOLT_STAGE
    die() { exit 91; }
    set +e
    ( source "${HOOK}" ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 91 && ! -e ${PORTAGE_BUILDDIR}/.installed ]]
)

case_mismatched_active_state_is_fatal() (
    new_marker mismatch
    GENTOO_OPT_MODE=clang-ir-use
    GENTOO_OPT_BOLT_STAGE=capture
    GENTOO_OPT_ACTIVE_BOLT_STAGE=deploy
    die() { exit 92; }
    set +e
    ( source "${HOOK}" ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 92 && ! -e ${PORTAGE_BUILDDIR}/.installed ]]
)

case_missing_transaction_function_is_fatal() (
    new_marker missing-function
    GENTOO_OPT_MODE=bolt-deploy
    GENTOO_OPT_ACTIVE_BOLT_STAGE=deploy
    unset -f gentoo_opt_post_src_install gentoo_opt_post_install_abort 2>/dev/null || :
    die() { exit 93; }
    set +e
    ( source "${HOOK}" ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 93 && ! -e ${PORTAGE_BUILDDIR}/.installed ]]
)

case_active_transaction_runs_exactly_once() (
    new_marker active
    GENTOO_OPT_MODE=rust-generate
    GENTOO_OPT_BOLT_STAGE=capture
    GENTOO_OPT_ACTIVE_BOLT_STAGE=capture
    TRANSACTION_LOG=${TMP}/transaction.log
    gentoo_opt_post_src_install() { printf 'ran\n' >> "${TRANSACTION_LOG}"; }
    gentoo_opt_post_install_abort() { return 97; }
    source "${HOOK}"
    [[ $(wc -l < "${TRANSACTION_LOG}") -eq 1 ]]
    [[ -f ${PORTAGE_BUILDDIR}/.installed ]]
)

[[ -f ${HOOK} ]] || {
    printf 'FAIL: hook is absent: %s\n' "${HOOK}" >&2
    exit 1
}
run_case 'off state is a strict no-op' case_off_is_noop
run_case 'lost active state invalidates the install' case_lost_active_state_is_fatal
run_case 'requested/active mismatch invalidates the install' case_mismatched_active_state_is_fatal
run_case 'missing transaction function invalidates the install' case_missing_transaction_function_is_fatal
run_case 'active transaction runs exactly once' case_active_transaction_runs_exactly_once
printf 'SUMMARY: pass=%d fail=%d total=%d\n' "${PASS}" "${FAIL}" "$((PASS + FAIL))"
((FAIL == 0))

#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export LC_ALL=C

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
FIXTURE=${ROOT}/tests/optimization/test-portage-sample-pgo-integration.sh
WORK=$(mktemp -d /tmp/gentoo-optimization-sample-env.XXXXXXXX)
trap 'rm -rf -- "${WORK}"' EXIT INT TERM HUP
TOKEN=$(printf '%064d' 0 | tr 0 a)
INVENTORY_SHA=$(printf fixture-inventory | sha256sum | awk '{print $1}')
OUTPUT=/var/tmp/gentoo-optimization/forbidden-env-fixture-${BASHPID}

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

if ((EUID != 0)); then
    record_required_subtest() {
        local status=$1 name=$2 detail=$3
        [[ -n ${GENTOO_OPT_SUBTEST_RESULTS:-} ]] || return 0
        printf '%s\trequired\t%s\t%s\n' "${status}" "${name}" "${detail}" \
            >>"${GENTOO_OPT_SUBTEST_RESULTS}"
    }
    record_required_subtest SKIP production-sample.environment \
        'root-owned production report-root checks require the root driver'
    printf 'SKIP: production sample environment contract requires root driver\n'
    exit 0
fi

record_required_subtest() {
    local status=$1 name=$2 detail=$3
    [[ -n ${GENTOO_OPT_SUBTEST_RESULTS:-} ]] || return 0
    printf '%s\trequired\t%s\t%s\n' "${status}" "${name}" "${detail}" \
        >>"${GENTOO_OPT_SUBTEST_RESULTS}"
}

LIVE_POLICY_ERROR_SCHEMA=gentoo-optimization-live-policy-observation-error-v1
LIVE_POLICY_PERMISSION_STATUS=73
LIVE_POLICY_ROOT_IDENTITY_STATUS=74

read_single_live_policy_error_record() {
    local source=$1
    local -a rows=()
    [[ -f ${source} && ! -L ${source} ]] || return 1
    mapfile -t rows <"${source}"
    ((${#rows[@]} == 1)) || return 1
    printf '%s' "${rows[0]}"
}

expected_live_policy_error_record() {
    local reason=$1 errno_value=$2 observation=$3 canonical_path=$4
    local expected_kind=$5 expected_uid=$6 expected_mode=$7
    local observed_kind=$8 observed_uid=$9 observed_mode=${10}
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' \
        "${LIVE_POLICY_ERROR_SCHEMA}" "${reason}" "${errno_value}" \
        "${observation}" "${canonical_path}" "${expected_kind}" \
        "${expected_uid}" "${expected_mode}" "${observed_kind}" \
        "${observed_uid}" "${observed_mode}"
}

live_policy_error_record_matches() {
    local source=$1 reason=$2 errno_value=$3 observation=$4 canonical_path=$5
    local expected_kind=$6 expected_uid=$7 expected_mode=$8
    local observed_kind=$9 observed_uid=${10} observed_mode=${11}
    local actual expected
    actual=$(read_single_live_policy_error_record "${source}") || return 1
    expected=$(expected_live_policy_error_record \
        "${reason}" "${errno_value}" "${observation}" "${canonical_path}" \
        "${expected_kind}" "${expected_uid}" "${expected_mode}" \
        "${observed_kind}" "${observed_uid}" "${observed_mode}")
    [[ ${actual} == "${expected}" ]]
}

normalized_mode() {
    local raw
    raw=$(/usr/bin/stat -c %a -- "$1") || return 1
    [[ ${raw} =~ ^[0-7]{3,4}$ ]] || return 1
    printf '%04o\n' "$((8#${raw}))"
}

root_trusted_regular_file() {
    local path=$1 expected_mode=$2 resolved current metadata mode
    resolved=$(/usr/bin/realpath -e -- "${path}") || return 1
    [[ ${resolved} == "${path}" && -f ${resolved} && ! -L ${resolved} ]] || return 1
    metadata=$(/usr/bin/stat -c '%u:%a' -- "${resolved}") || return 1
    mode=$(normalized_mode "${resolved}") || return 1
    [[ ${metadata%%:*} == 0 && ${mode} == "${expected_mode}" ]] || return 1
    current=${resolved%/*}
    [[ -n ${current} ]] || current=/
    while :; do
        [[ -d ${current} && ! -L ${current} && \
            $(/usr/bin/realpath -e -- "${current}") == "${current}" ]] || return 1
        metadata=$(/usr/bin/stat -c '%u:%a' -- "${current}") || return 1
        [[ ${metadata%%:*} == 0 ]] || return 1
        (( (8#${metadata#*:} & 8#022) == 0 )) || return 1
        [[ ${current} == / ]] && break
        current=${current%/*}
        [[ -n ${current} ]] || current=/
    done
}

expected_private_marker_permission_error() {
    local source=$1 portage_root=$2 marker uid mode
    portage_root=$(/usr/bin/realpath -e -- "${portage_root}") || return 1
    marker=${portage_root}/.gentoo-optimization-source-hash
    root_trusted_regular_file "${marker}" 0600 || return 1
    uid=$(/usr/bin/stat -c %u -- "${marker}") || return 1
    mode=$(normalized_mode "${marker}") || return 1
    ((EUID != 0)) || return 1
    [[ ! -r ${marker} ]] || return 1
    live_policy_error_record_matches "${source}" \
        permission-denied 13 portage_config.tree.regular-content-sha256 \
        "${marker}" regular 0 0600 regular "${uid}" "${mode}"
}

expected_remapped_root_identity_error() {
    local source=$1 canonical_env root_uid env_uid env_mode current metadata
    canonical_env=$(/usr/bin/realpath -e -- /usr/bin/env) || return 1
    [[ -f ${canonical_env} && -x ${canonical_env} && ! -L ${canonical_env} ]] || return 1
    root_uid=$(/usr/bin/stat -c %u -- /) || return 1
    env_uid=$(/usr/bin/stat -c %u -- "${canonical_env}") || return 1
    env_mode=$(normalized_mode "${canonical_env}") || return 1
    ((EUID != 0 && root_uid != 0 && env_uid == root_uid)) || return 1
    (( (8#${env_mode} & 8#022) == 0 )) || return 1
    current=${canonical_env%/*}
    [[ -n ${current} ]] || current=/
    while :; do
        [[ -d ${current} && ! -L ${current} && \
            $(/usr/bin/realpath -e -- "${current}") == "${current}" ]] || return 1
        metadata=$(/usr/bin/stat -c '%u:%a' -- "${current}") || return 1
        [[ ${metadata%%:*} == "${root_uid}" ]] || return 1
        (( (8#${metadata#*:} & 8#022) == 0 )) || return 1
        [[ ${current} == / ]] && break
        current=${current%/*}
        [[ -n ${current} ]] || current=/
    done
    live_policy_error_record_matches "${source}" \
        root-identity-unobservable not-applicable live-policy.tool-root-trust \
        "${canonical_env}" executable-regular 0 root-owned-no-group-world-write \
        executable-regular "${env_uid}" "${env_mode}"
}

test_live_policy_error_record_contract() {
    local fixture=${WORK}/live-policy-error-contract marker exact
    /usr/bin/mkdir -p -- "${fixture}"
    marker=${fixture}/.gentoo-optimization-source-hash
    : >"${marker}"
    /usr/bin/chmod 0600 -- "${marker}"
    marker=$(/usr/bin/realpath -e -- "${marker}")
    exact=$(expected_live_policy_error_record permission-denied 13 \
        portage_config.tree.regular-content-sha256 "${marker}" regular 0 0600 \
        regular 0 0600)
    printf '%s\n' "${exact}" >"${fixture}/record"
    live_policy_error_record_matches "${fixture}/record" permission-denied 13 \
        portage_config.tree.regular-content-sha256 "${marker}" regular 0 0600 \
        regular 0 0600 || fail 'exact live-policy error record was rejected'
    for mutation in \
        'PermissionError: [Errno 13] Permission denied: unrelated' \
        "${exact/permission-denied/unrelated-permission}" \
        "${exact/portage_config.tree.regular-content-sha256/other-observation}" \
        "${exact/${marker}/${marker}.other}" \
        "${exact/$'\t0\t0600\tregular\t0\t0600'/$'\t0\t0644\tregular\t0\t0644'}"; do
        printf '%s\n' "${mutation}" >"${fixture}/record"
        if live_policy_error_record_matches "${fixture}/record" permission-denied 13 \
            portage_config.tree.regular-content-sha256 "${marker}" regular 0 0600 \
            regular 0 0600; then
            fail 'live-policy error classifier accepted a mutated record'
        fi
    done
    printf '%s\n%s\n' "${exact}" traceback >"${fixture}/record"
    if live_policy_error_record_matches "${fixture}/record" permission-denied 13 \
        portage_config.tree.regular-content-sha256 "${marker}" regular 0 0600 \
        regular 0 0600; then
        fail 'live-policy error classifier accepted additional stderr'
    fi
}

test_live_policy_error_record_contract

LIVE_POLICY_BASELINE_LOG=${WORK}/live-policy-preflight-baseline.tsv
LIVE_POLICY_PREFLIGHT_LOG=${WORK}/live-policy-preflight-poisoned.tsv
if [[ -x /usr/bin/portageq && -e /etc/portage/make.conf ]]; then
    LIVE_POLICY_PREFLIGHT_AVAILABLE=1
    EXPECTED_LIVE_FEATURES=$(/usr/bin/env -i HOME=/root USER=root LOGNAME=root \
        SHELL=/bin/bash PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
        PORTAGE_CONFIGROOT=/ /usr/bin/portageq envvar FEATURES)
    LIVE_MAKE_CONF=$(/usr/bin/realpath -e -- /etc/portage/make.conf)
    EXPECTED_LIVE_MAKE_CONF_SHA256=$(/usr/bin/sha256sum -- "${LIVE_MAKE_CONF}")
    EXPECTED_LIVE_MAKE_CONF_SHA256=${EXPECTED_LIVE_MAKE_CONF_SHA256%% *}
    if /bin/bash -- "${FIXTURE}" --live-policy-preflight \
        > "${LIVE_POLICY_BASELINE_LOG}" 2> "${WORK}/live-policy-preflight.stderr"; then
        :
    else
        LIVE_POLICY_PREFLIGHT_STATUS=$?
        LIVE_PORTAGE_ROOT=$(/usr/bin/realpath -e -- /etc/portage)
        LIVE_POLICY_SKIP_DETAIL=
        if ((LIVE_POLICY_PREFLIGHT_STATUS == LIVE_POLICY_PERMISSION_STATUS)) && \
            expected_private_marker_permission_error \
                "${WORK}/live-policy-preflight.stderr" "${LIVE_PORTAGE_ROOT}"; then
            LIVE_POLICY_SKIP_DETAIL="reason=permission-denied observation=portage_config.tree.regular-content-sha256 path=${LIVE_PORTAGE_ROOT}/.gentoo-optimization-source-hash expected_kind=regular expected_uid=0 expected_mode=0600"
        elif ((LIVE_POLICY_PREFLIGHT_STATUS == LIVE_POLICY_ROOT_IDENTITY_STATUS)) && \
            expected_remapped_root_identity_error \
                "${WORK}/live-policy-preflight.stderr"; then
            LIVE_POLICY_SKIP_DETAIL="reason=root-identity-unobservable observation=live-policy.tool-root-trust path=$(/usr/bin/realpath -e -- /usr/bin/env) expected_kind=executable-regular expected_uid=0 expected_mode=root-owned-no-group-world-write"
        fi
        if [[ -n ${LIVE_POLICY_SKIP_DETAIL} && \
            ${GENTOO_OPT_AUTHORITATIVE:-0} == 0 ]]; then
            record_required_subtest SKIP live-portage.policy \
                "${LIVE_POLICY_SKIP_DETAIL}"
            printf 'INFO: live Portage policy observation unavailable under the exact reviewed boundary; recorded required subtest SKIP\n'
            LIVE_POLICY_PREFLIGHT_AVAILABLE=0
        else
            sed -n '1,120p' "${WORK}/live-policy-preflight.stderr" >&2
            if [[ -n ${LIVE_POLICY_SKIP_DETAIL} ]]; then
                fail 'authoritative live-policy preflight cannot skip an unavailable required observation'
            fi
            fail 'live-policy baseline preflight failed unexpectedly'
        fi
    fi
    if ((LIVE_POLICY_PREFLIGHT_AVAILABLE)); then
        /usr/bin/env \
            HOME=/tmp/poison-home TMPDIR=/tmp/poison-tmpdir PATH=/tmp \
            FEATURES='caller-injected-feature -sandbox -usersandbox' \
            PORTAGE_CONFIGROOT=/tmp/poison-config ROOT=/tmp/poison-root \
            SYSROOT=/tmp/poison-sysroot EPREFIX=/tmp/poison-prefix \
            PORTDIR=/tmp/poison-portdir PORTDIR_OVERLAY=/tmp/poison-overlay \
            PORTAGE_TMPDIR=/tmp/poison-tmp PORTAGE_LOGDIR=/tmp/poison-log \
            PORTAGE_DEPCACHEDIR=/tmp/poison-depcache \
            DISTDIR=/tmp/poison-dist PKGDIR=/tmp/poison-binpkgs \
            PORTAGE_BINHOST=https://invalid.example/ \
            PORTAGE_ELOG_SYSTEM=mail PORTAGE_ELOG_CLASSES=info \
            PORTAGE_ELOG_MAILURI=poison@example.invalid \
            PORTAGE_ELOG_COMMAND=/tmp/poison-elog \
            GENTOO_MIRRORS=https://invalid.example/ \
            FETCHCOMMAND=/tmp/poison-fetch RESUMECOMMAND=/tmp/poison-resume \
            MAKEOPTS=-j999 EMERGE_DEFAULT_OPTS=--usepkgonly \
            CONFIG_PROTECT=/tmp/poison-protect ACCEPT_KEYWORDS=-amd64 \
            USE=-pgo \
            CCACHE_DIR=/tmp/poison-ccache CCACHE_TEMPDIR=/tmp/poison-ccache-tmp \
            CCACHE_DISABLE=0 SCCACHE_DIR=/tmp/poison-sccache SCCACHE_DISABLE=0 \
            CC=/tmp/poison-cc CXX=/tmp/poison-cxx \
            /bin/bash -- "${FIXTURE}" --live-policy-preflight \
            > "${LIVE_POLICY_PREFLIGHT_LOG}"
        /usr/bin/cmp -- "${LIVE_POLICY_BASELINE_LOG}" \
            "${LIVE_POLICY_PREFLIGHT_LOG}" || \
            fail 'poisoned FEATURES changed the complete live-policy preflight output'
        grep -Fxq $'schema\tgentoo-optimization-sample-live-policy-preflight-v2' \
            "${LIVE_POLICY_PREFLIGHT_LOG}" || \
            fail 'live-policy preflight lacks its exact schema'
        grep -Fxq $'live_resolved_features\t'"${EXPECTED_LIVE_FEATURES}" \
            "${LIVE_POLICY_PREFLIGHT_LOG}" || \
            fail 'inherited FEATURES changed the captured authoritative live policy'
        grep -Fxq $'live_make_conf_sha256\t'"${EXPECTED_LIVE_MAKE_CONF_SHA256}" \
            "${LIVE_POLICY_PREFLIGHT_LOG}" || \
            fail 'live-policy preflight recorded the wrong make.conf identity'
        if grep -Fq caller-injected-feature "${LIVE_POLICY_PREFLIGHT_LOG}"; then
            fail 'live-policy preflight persisted an inherited FEATURES token'
        fi
        grep -Eq $'^live_policy_identity_sha256\t[0-9a-f]{64}$' \
            "${LIVE_POLICY_PREFLIGHT_LOG}" || \
            fail 'live-policy preflight lacks its complete trusted identity digest'
        record_required_subtest PASS live-portage.policy \
            'poisoned caller environment reproduced the exact trusted live Portage policy'
    fi
else
    record_required_subtest SKIP live-portage.policy \
        'live Portage policy tools or configuration are unavailable'
    printf 'INFO: live Portage policy unavailable; recorded required subtest SKIP\n'
fi

for forbidden in \
    GENTOO_OPT_PORTAGE_FIXTURE_MODE=1 \
    GENTOO_OPT_PROFILE_VALIDATOR=/tmp/untrusted-validator \
    GENTOO_OPT_FINGERPRINT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    GENTOO_OPT_DISPATCHER_TEST_MODE=1; do
    log=${WORK}/${forbidden%%=*}.log
    if /usr/bin/env -i \
        HOME="${HOME:-/tmp}" PATH=/usr/bin:/bin LANG=C LC_ALL=C \
        GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN="${TOKEN}" \
        GENTOO_OPT_PRODUCTION_GATE_RUN_ID=fixture-run \
        GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID=fixture-generation \
        GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID=fixture-inventory \
        GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256="${INVENTORY_SHA}" \
        GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT=/var/tmp/gentoo-optimization/phase2-sample-work-fixture-run \
        "${forbidden}" \
        /bin/bash -- "${FIXTURE}" --production-locks --portage-policy live \
        --output-dir "${OUTPUT}" \
        >"${log}" 2>&1; then
        fail "production sample gate accepted inherited override: ${forbidden%%=*}"
    fi
    grep -Fq -- \
        "--production-locks inherited a forbidden optimization override: ${forbidden%%=*}" \
        "${log}" || {
        sed -n '1,120p' "${log}" >&2
        fail "forbidden override rejection lacked its exact diagnostic: ${forbidden%%=*}"
    }
    [[ ! -e ${OUTPUT} && ! -L ${OUTPUT} ]] || \
        fail 'forbidden override rejection created its output destination'
done

for forbidden in PORTAGE_SAMPLE_PGO_ITERATIONS=1 KEEP_TEMP=1; do
    log=${WORK}/${forbidden%%=*}.log
    if /usr/bin/env -i \
        HOME="${HOME:-/tmp}" PATH=/usr/bin:/bin LANG=C LC_ALL=C \
        GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN="${TOKEN}" \
        GENTOO_OPT_PRODUCTION_GATE_RUN_ID=fixture-run \
        GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID=fixture-generation \
        GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID=fixture-inventory \
        GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256="${INVENTORY_SHA}" \
        GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT=/var/tmp/gentoo-optimization/phase2-sample-work-fixture-run \
        "${forbidden}" \
        /bin/bash -- "${FIXTURE}" --production-locks --portage-policy live \
        --output-dir "${OUTPUT}" \
        >"${log}" 2>&1; then
        fail "production sample gate accepted inherited override: ${forbidden%%=*}"
    fi
    grep -Fq -- '--production-locks forbids inherited workload or retention overrides' \
        "${log}" || {
        sed -n '1,120p' "${log}" >&2
        fail "workload override rejection lacked its exact diagnostic: ${forbidden%%=*}"
    }
    [[ ! -e ${OUTPUT} && ! -L ${OUTPUT} ]] || \
        fail 'workload override rejection created its output destination'
done

MISSING_AUTHORIZATION_LOG=${WORK}/missing-authorization.log
if /usr/bin/env -i \
    HOME="${HOME:-/tmp}" PATH=/usr/bin:/bin LANG=C LC_ALL=C \
    GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN="${TOKEN}" \
    GENTOO_OPT_PRODUCTION_GATE_RUN_ID=fixture-run \
    GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID=fixture-generation \
    GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID=fixture-inventory \
    GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256="${INVENTORY_SHA}" \
    GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT=/var/tmp/gentoo-optimization/phase2-sample-work-fixture-run \
    /bin/bash -- "${FIXTURE}" --production-locks --portage-policy live \
    --output-dir "${OUTPUT}" \
    >"${MISSING_AUTHORIZATION_LOG}" 2>&1; then
    fail 'production sample gate accepted a missing coordinator authorization path'
fi
grep -Fq -- '--production-locks requires the coordinator authorization path' \
    "${MISSING_AUTHORIZATION_LOG}" || {
    sed -n '1,120p' "${MISSING_AUTHORIZATION_LOG}" >&2
    fail 'missing coordinator authorization rejection lacked its exact diagnostic'
}
[[ ! -e ${OUTPUT} && ! -L ${OUTPUT} ]] || \
    fail 'missing coordinator authorization rejection created its output destination'

DIAGNOSTIC_POLICY_LOG=${WORK}/production-diagnostic-policy.log
if /usr/bin/env -i \
    HOME="${HOME:-/tmp}" PATH=/usr/bin:/bin LANG=C LC_ALL=C \
    /bin/bash -- "${FIXTURE}" --production-locks \
    --portage-policy isolated-diagnostic --output-dir "${OUTPUT}" \
    >"${DIAGNOSTIC_POLICY_LOG}" 2>&1; then
    fail 'production sample gate accepted the isolated diagnostic Portage policy'
fi
grep -Fq -- '--production-locks requires --portage-policy live' \
    "${DIAGNOSTIC_POLICY_LOG}" || {
    sed -n '1,120p' "${DIAGNOSTIC_POLICY_LOG}" >&2
    fail 'production diagnostic-policy rejection lacked its exact diagnostic'
}
[[ ! -e ${OUTPUT} && ! -L ${OUTPUT} ]] || \
    fail 'production diagnostic-policy rejection created its output destination'

printf 'PASS: production sample-PGO environment rejects inherited overrides and non-live Portage policy\n'

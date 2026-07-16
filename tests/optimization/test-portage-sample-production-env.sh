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

LIVE_POLICY_BASELINE_LOG=${WORK}/live-policy-preflight-baseline.tsv
LIVE_POLICY_PREFLIGHT_LOG=${WORK}/live-policy-preflight-poisoned.tsv
if [[ -x /usr/bin/portageq && -e /etc/portage/make.conf ]]; then
    EXPECTED_LIVE_FEATURES=$(/usr/bin/env -i HOME=/root USER=root LOGNAME=root \
        SHELL=/bin/bash PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
        PORTAGE_CONFIGROOT=/ /usr/bin/portageq envvar FEATURES)
    LIVE_MAKE_CONF=$(/usr/bin/realpath -e -- /etc/portage/make.conf)
    EXPECTED_LIVE_MAKE_CONF_SHA256=$(/usr/bin/sha256sum -- "${LIVE_MAKE_CONF}")
    EXPECTED_LIVE_MAKE_CONF_SHA256=${EXPECTED_LIVE_MAKE_CONF_SHA256%% *}
    if ! /bin/bash -- "${FIXTURE}" --live-policy-preflight \
        > "${LIVE_POLICY_BASELINE_LOG}" 2> "${WORK}/live-policy-preflight.stderr"; then
        if grep -Fq 'canonical ancestry is not root-trusted' \
            "${WORK}/live-policy-preflight.stderr"; then
            printf 'SKIP-SUBTEST: managed user namespace does not expose literal uid-0 live-policy trust metadata\n'
        else
            sed -n '1,120p' "${WORK}/live-policy-preflight.stderr" >&2
            fail 'live-policy baseline preflight failed unexpectedly'
        fi
    else
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
    fi
else
    printf 'SKIP-SUBTEST: live Portage FEATURES override test requires /usr/bin/portageq and /etc/portage/make.conf\n'
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

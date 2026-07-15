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
        /bin/bash -- "${FIXTURE}" --production-locks --output-dir "${OUTPUT}" \
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
        /bin/bash -- "${FIXTURE}" --production-locks --output-dir "${OUTPUT}" \
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
    /bin/bash -- "${FIXTURE}" --production-locks --output-dir "${OUTPUT}" \
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

printf 'PASS: production sample-PGO environment rejects inherited fixture and identity overrides\n'

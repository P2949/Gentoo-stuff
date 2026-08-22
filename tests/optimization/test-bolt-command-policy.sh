#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)
FIXTURE_RUNNER=${REPOSITORY_ROOT}/optimization/fixtures/bolt/run.sh
PRODUCTION_POLICY=${REPOSITORY_ROOT}/scripts/optimization/bolt/artifact_tool.py

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

validate_command_policy() {
    local label=$1 path=$2 forbidden blocks_line functions_line
    local -a blocks_matches=() functions_matches=()

    [[ -f ${path} ]] || fail "${label} is absent: ${path}"
    mapfile -t blocks_matches < <(
        grep -Fn -- '-reorder-blocks=ext-tsp' "${path}" || true
    )
    mapfile -t functions_matches < <(
        grep -Fn -- '-reorder-functions=cdsort' "${path}" || true
    )
    [[ ${#blocks_matches[@]} -eq 1 ]] || \
        fail "${label} must contain exactly one -reorder-blocks=ext-tsp literal"
    [[ ${#functions_matches[@]} -eq 1 ]] || \
        fail "${label} must contain exactly one -reorder-functions=cdsort literal"
    blocks_line=${blocks_matches[0]%%:*}
    functions_line=${functions_matches[0]%%:*}
    ((blocks_line < functions_line)) || \
        fail "${label} reverses the reviewed BOLT layout-policy order"

    for forbidden in 'hfsort+' cdfsort -use-gnu-stack; do
        if grep -Fq -- "${forbidden}" "${path}"; then
            fail "${label} contains forbidden BOLT policy ${forbidden}"
        fi
    done
}

validate_command_policy 'production BOLT policy authority' "${PRODUCTION_POLICY}"
validate_command_policy 'BOLT capability fixture' "${FIXTURE_RUNNER}"

printf 'PASS: production BOLT policy and capability command use the exact reviewed layout\n'

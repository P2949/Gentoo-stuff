#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)
FIXTURE_RUNNER=${REPOSITORY_ROOT}/optimization/fixtures/bolt/run.sh
LEGACY_EXPLICIT_OUTPUT_HELPER=${REPOSITORY_ROOT}/scripts/bolt/optimize-binary.sh

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

validate_command_policy() {
    local label=$1 path=$2 token forbidden
    local -a tokens=()

    [[ -f ${path} ]] || fail "${label} is absent: ${path}"
    while IFS= read -r token; do
        tokens+=("${token}")
    done < <(grep -Eo -- '-reorder-(blocks|functions)=[^[:space:]\\]+' "${path}" || true)

    [[ ${#tokens[@]} -eq 2 ]] || \
        fail "${label} must contain exactly two BOLT layout-policy tokens, found ${#tokens[@]}"
    [[ ${tokens[0]} == -reorder-blocks=ext-tsp ]] || \
        fail "${label} does not use exactly -reorder-blocks=ext-tsp: ${tokens[0]}"
    [[ ${tokens[1]} == -reorder-functions=cdsort ]] || \
        fail "${label} does not use exactly -reorder-functions=cdsort: ${tokens[1]}"

    for forbidden in 'hfsort+' cdfsort -use-gnu-stack; do
        if grep -Fq -- "${forbidden}" "${path}"; then
            fail "${label} contains forbidden BOLT policy ${forbidden}"
        fi
    done
}

validate_command_policy 'BOLT capability fixture' "${FIXTURE_RUNNER}"
validate_command_policy 'legacy explicit-output BOLT helper' \
    "${LEGACY_EXPLICIT_OUTPUT_HELPER}"

printf 'PASS: exact BOLT command policy is consistent in both command producers\n'

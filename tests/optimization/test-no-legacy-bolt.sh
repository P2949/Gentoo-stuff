#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
readonly ROOT
WORK=$(mktemp -d "${TMPDIR:-/tmp}/legacy-bolt-test.XXXXXX")
trap 'rm -rf -- "${WORK}"' EXIT HUP INT TERM

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

EXPECTED_STUB=${WORK}/expected-stub.sh
EXPECTED_STDERR=${WORK}/expected-stderr
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    '' \
    "printf '%s\\n' \\" \
    "    'ERROR: this legacy BOLT prototype entry point is permanently disabled.' \\" \
    "    'Use the exact Phase 2 pre-strip capture, registered-output, and \${ED} deployment lane.' \\" \
    '    >&2' \
    'exit 1' \
    >"${EXPECTED_STUB}"
printf '%s\n' \
    'ERROR: this legacy BOLT prototype entry point is permanently disabled.' \
    'Use the exact Phase 2 pre-strip capture, registered-output, and ${ED} deployment lane.' \
    >"${EXPECTED_STDERR}"

readonly -a LEGACY_HELPERS=(
    scripts/bolt/bolt-package-binaries.sh
    scripts/bolt/collect-profile.sh
    scripts/bolt/list-package-binaries.sh
    scripts/bolt/optimize-binary.sh
)

for relative in "${LEGACY_HELPERS[@]}"; do
    helper=${ROOT}/${relative}
    [[ -f ${helper} && ! -L ${helper} && -x ${helper} ]] || \
        fail "legacy helper is absent, linked, or not executable: ${relative}"
    cmp -s -- "${EXPECTED_STUB}" "${helper}" || \
        fail "legacy helper differs from the reviewed fail-closed stub: ${relative}"

    status=0
    PATH=/usr/bin:/bin "${helper}" arbitrary arguments \
        >"${WORK}/stdout" 2>"${WORK}/stderr" || status=$?
    [[ ${status} -eq 1 ]] || \
        fail "legacy helper returned ${status}, expected permanent-disable status 1: ${relative}"
    [[ ! -s ${WORK}/stdout ]] || \
        fail "legacy helper emitted usable stdout: ${relative}"
    cmp -s -- "${EXPECTED_STDERR}" "${WORK}/stderr" || \
        fail "legacy helper lacks the exact permanent-disable diagnostic: ${relative}"
done

DOCUMENTATION=${ROOT}/docs/bolt-global.md
grep -Fq '# Retired global BOLT prototype' "${DOCUMENTATION}" || \
    fail 'global BOLT documentation is not explicitly retired'
grep -Fq 'The prototype is permanently disabled.' "${DOCUMENTATION}" || \
    fail 'global BOLT documentation does not declare permanent disablement'
if grep -Eq '^```(bash|sh|shell)?$' "${DOCUMENTATION}"; then
    fail 'retired global BOLT documentation still contains a runnable shell block'
fi
if grep -Eq '/opt/bolt-test|/var/tmp/bolt-profiles' "${DOCUMENTATION}"; then
    fail 'retired global BOLT documentation still advertises prototype output/profile paths'
fi

printf 'PASS: legacy identity-free BOLT helpers and operator workflow are disabled\n'

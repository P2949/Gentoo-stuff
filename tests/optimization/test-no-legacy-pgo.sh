#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
readonly ROOT
WORK=$(mktemp -d "${TMPDIR:-/tmp}/legacy-pgo-test.XXXXXX")
trap 'rm -rf -- "${WORK}"' EXIT HUP INT TERM

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

readonly -a LEGACY_HELPERS=(
    scripts/pgo/package-profile-path.sh
    scripts/pgo/pgo-path.sh
    scripts/pgo/list-profiled-packages.sh
    scripts/pgo/merge-instr-profile.sh
    scripts/pgo/collect-sample-profile.sh
    scripts/pgo/make-sample-prof.sh
)

for relative in "${LEGACY_HELPERS[@]}"; do
    helper=${ROOT}/${relative}
    [[ -x ${helper} ]] || fail "legacy helper is absent or not executable: ${relative}"
    if "${helper}" arbitrary arguments >"${WORK}/stdout" 2>"${WORK}/stderr"; then
        fail "legacy helper unexpectedly succeeded: ${relative}"
    fi
    grep -Fq 'permanently disabled' "${WORK}/stderr" || \
        fail "legacy helper lacks permanent-disable diagnostic: ${relative}"
    [[ ! -s ${WORK}/stdout ]] || \
        fail "legacy helper emitted usable stdout: ${relative}"
done

weak_root='/var/tmp/'
weak_root+='pgo-profiles'
if rg -n -F -- "${weak_root}" "${ROOT}/scripts/pgo" "${ROOT}/portage"; then
    fail 'legacy unkeyed profile root remains in executable/configuration scope'
fi

for deleted_name in pgo-instrument.conf pgo-use-if-available.conf no-pgo-use.conf; do
    [[ ! -e ${ROOT}/portage/env/${deleted_name} ]] || \
        fail "deleted legacy environment was restored: ${deleted_name}"
    if rg -n -F -- "${deleted_name}" "${ROOT}/docs"; then
        fail "operator documentation still recommends ${deleted_name}"
    fi
done

if awk 'NF && $1 !~ /^#/' "${ROOT}/portage/package.env/50-global-pgo" | grep -q .; then
    fail 'legacy global package.env policy contains an active assignment'
fi

grep -Fq '# Retired global PGO prototype' "${ROOT}/docs/pgo-global.md" || \
    fail 'global PGO documentation is not explicitly retired'

printf 'PASS: legacy compiler-agnostic PGO helpers and operator paths are disabled\n'

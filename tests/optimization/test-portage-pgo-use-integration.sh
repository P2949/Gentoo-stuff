#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
TEMPLATE=${ROOT}/optimization/fixtures/portage/phase2-pgo-use-fixture-1.ebuild.in
INSTALLER=${ROOT}/scripts/optimization/install-framework.sh
VALIDATOR=/usr/local/libexec/gentoo-optimization/pgo/validate-profile.py
CLANG=/usr/lib/llvm/22/bin/clang-22
PROFDATA=/usr/lib/llvm/22/bin/llvm-profdata

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
((EUID == 0)) || { printf 'SKIP: real Portage PGO integration requires root\n'; exit 77; }
for command in awk b2sum ebuild grep python3 sed sha256sum sha512sum stat; do
    command -v "${command}" >/dev/null 2>&1 || fail "missing command: ${command}"
done
[[ -f ${TEMPLATE} && -x ${INSTALLER} && -x ${VALIDATOR} &&
    -x ${CLANG} && -x ${PROFDATA} ]] || fail 'fixture or installed exact tool is absent'
"${INSTALLER}" --check >/dev/null || fail 'installed framework differs from reviewed source'

WORK=$(mktemp -d /var/tmp/gentoo-phase2-pgo-portage.XXXXXX)
cleanup() {
    local result=$?
    if ((result != 0)); then
        for log in "${WORK}"/*.log; do
            [[ -f ${log} ]] || continue
            printf '\n--- %s ---\n' "${log}" >&2
            tail -n 160 "${log}" >&2 || :
        done
    fi
    rm -rf -- "${WORK}"
    return "${result}"
}
trap cleanup EXIT HUP INT TERM

PACKAGE_ROOT=${WORK}/app-test/phase2-pgo-use-fixture
EBUILD=${PACKAGE_ROOT}/phase2-pgo-use-fixture-1.ebuild
RAW_ROOT=${WORK}/profiles/raw
PROFILE=${WORK}/profiles/merged.profdata
MANIFEST=${WORK}/profiles/profile.manifest
METADATA=${MANIFEST}.metadata.json
GENERATE_SWITCH=${WORK}/generate
FINGERPRINT=$(printf '%s' "${WORK}:pgo" | sha256sum | awk '{print $1}')
mkdir -p -- "${PACKAGE_ROOT}" "${RAW_ROOT}" "${WORK}/metadata" "${WORK}/profiles"
chmod 0755 -- "${WORK}" "${WORK}/app-test" "${PACKAGE_ROOT}" \
    "${WORK}/metadata" "${WORK}/profiles" "${RAW_ROOT}"
printf '%s\n' 'masters = gentoo' > "${WORK}/metadata/layout.conf"
printf '%s\n' phase2-pgo-use-fixture > "${WORK}/profiles/repo_name"
printf '%s\n' app-test > "${WORK}/profiles/categories"

escape_sed() { printf '%s' "$1" | sed 's/[&|]/\\&/g'; }
sed \
    -e "s|@FINGERPRINT@|${FINGERPRINT}|g" \
    -e "s|@GENERATE_SWITCH@|$(escape_sed "${GENERATE_SWITCH}")|g" \
    -e "s|@RAW_ROOT@|$(escape_sed "${RAW_ROOT}")|g" \
    -e "s|@PROFILE@|$(escape_sed "${PROFILE}")|g" \
    -e "s|@MANIFEST@|$(escape_sed "${MANIFEST}")|g" \
    -e "s|@METADATA@|$(escape_sed "${METADATA}")|g" \
    "${TEMPLATE}" > "${EBUILD}"
chmod 0644 -- "${EBUILD}" "${WORK}/metadata/layout.conf" \
    "${WORK}/profiles/repo_name" "${WORK}/profiles/categories"
printf 'EBUILD %s %s BLAKE2B %s SHA512 %s\n' \
    "$(basename -- "${EBUILD}")" "$(stat -c %s "${EBUILD}")" \
    "$(b2sum "${EBUILD}" | awk '{print $1}')" \
    "$(sha512sum "${EBUILD}" | awk '{print $1}')" > "${PACKAGE_ROOT}/Manifest"
chmod 0644 -- "${PACKAGE_ROOT}/Manifest"

: > "${GENERATE_SWITCH}"
ebuild "${EBUILD}" clean > "${WORK}/clean-before-generate.log" 2>&1
ebuild "${EBUILD}" install > "${WORK}/generate.log" 2>&1
raw_count=$(find "${RAW_ROOT}" -type f -name '*.profraw' -size +0c | wc -l)
((raw_count > 0)) || fail 'real Portage generation emitted no nonempty raw profile'
"${PROFDATA}" merge -o "${PROFILE}" "${RAW_ROOT}"/*.profraw
[[ -s ${PROFILE} ]] || fail 'llvm-profdata produced no indexed profile'

clang_hash=$(sha256sum -- "${CLANG}"); clang_hash=${clang_hash%% *}
profdata_hash=$(sha256sum -- "${PROFDATA}"); profdata_hash=${profdata_hash%% *}
"${VALIDATOR}" produce --backend clang-ir --profile "${PROFILE}" \
    --fingerprint "${FINGERPRINT}" --abi amd64 --compiler-family clang \
    --compiler "${CLANG}" --compiler-sha256 "${clang_hash}" --compiler-major 22 \
    --profile-tool "${PROFDATA}" --profile-tool-sha256 "${profdata_hash}" \
    --profile-tool-major 22 --manifest-out "${MANIFEST}" --metadata-out "${METADATA}"
[[ $(stat -c '%a' -- "${METADATA}") == 600 ]] || fail 'validator sidecar is not mode 0600'
"${VALIDATOR}" verify --manifest "${MANIFEST}" --metadata "${METADATA}"

rm -f -- "${GENERATE_SWITCH}"
ebuild "${EBUILD}" clean > "${WORK}/clean-before-use.log" 2>&1
ebuild "${EBUILD}" install > "${WORK}/use.log" 2>&1
grep -Fq 'mode=clang-ir-use' "${WORK}/use.log" || fail 'real use log lacks dispatcher mode'
grep -Fq "profile=${PROFILE}" "${WORK}/use.log" || fail 'real use log lacks exact profile path'
grep -Fq 'Completed installing app-test/phase2-pgo-use-fixture-1' \
    "${WORK}/use.log" || fail 'real PGO-use install did not complete'

cp -- "${METADATA}" "${WORK}/metadata.saved"
printf '\n' >> "${METADATA}"
ebuild "${EBUILD}" clean > "${WORK}/clean-before-tamper.log" 2>&1
if ebuild "${EBUILD}" install > "${WORK}/tampered-sidecar.log" 2>&1; then
    fail 'real Portage use accepted a tampered validation sidecar'
fi
grep -Fq 'authoritative profile manifest/sidecar verification failed' \
    "${WORK}/tampered-sidecar.log" || fail 'tampered sidecar lacked fail-closed diagnostic'
mv -- "${WORK}/metadata.saved" "${METADATA}"
ebuild "${EBUILD}" clean > "${WORK}/final-clean.log" 2>&1
printf 'PASS: real Portage Clang IR generation, strict sidecar use, and tamper rejection\n'

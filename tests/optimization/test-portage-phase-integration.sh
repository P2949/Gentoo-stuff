#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
readonly ROOT
TEMPLATE=${ROOT}/optimization/fixtures/portage/phase2-portage-fixture-1.ebuild.in
PROXY_TEMPLATE=${ROOT}/optimization/fixtures/portage/capture-proxy.sh.in
CAPTURE_TOOL=${ROOT}/scripts/optimization/bolt/capture-input.sh

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

if ((EUID != 0)); then
    printf 'SKIP: real Portage phase integration requires root\n'
    exit 77
fi

for command in b2sum ebuild portageq python3 readelf sed sha256sum sha512sum stat; do
    command -v "${command}" >/dev/null 2>&1 || fail "missing required command: ${command}"
done
[[ -f ${TEMPLATE} && -f ${PROXY_TEMPLATE} && -x ${CAPTURE_TOOL} ]] || \
    fail 'fixture template, proxy template, or capture tool is absent'
[[ $(readlink -f /etc/portage/bashrc) == "${ROOT}/portage/bashrc" ]] || \
    fail 'live /etc/portage/bashrc does not resolve to this repository'

WORK=$(mktemp -d /var/tmp/gentoo-phase2-portage-fixture.XXXXXX)
SUCCESS_FINGERPRINT=''
CACHE_ROOT=/var/cache/gentoo-optimization/bolt
cleanup() {
    local status=$?
    if ((status != 0)); then
        printf 'Portage fixture logs from %s:\n' "${WORK}" >&2
        for log in "${WORK}"/*.log; do
            [[ -f ${log} ]] || continue
            printf '\n--- %s ---\n' "${log}" >&2
            tail -n 120 "${log}" >&2 || :
        done
    fi
    for fingerprint in "${SUCCESS_FINGERPRINT}"; do
        [[ ${fingerprint} =~ ^[0-9a-f]{64}$ ]] || continue
        rm -rf -- "${CACHE_ROOT}/inputs/${fingerprint}" \
            "${CACHE_ROOT}/perf/${fingerprint}" \
            "${CACHE_ROOT}/fdata/${fingerprint}" \
            "${CACHE_ROOT}/outputs/${fingerprint}"
        rm -f -- "${CACHE_ROOT}/locks/${fingerprint}.lock"
    done
    rm -rf -- "${WORK}"
    return "${status}"
}
trap cleanup EXIT HUP INT TERM
PACKAGE_ROOT=${WORK}/app-test/phase2-portage-fixture
EBUILD=${PACKAGE_ROOT}/phase2-portage-fixture-1.ebuild
FAIL_SWITCH=${WORK}/force-capture-failure
CAPTURE_PROXY=${WORK}/capture-proxy.sh
SUCCESS_FINGERPRINT=$(printf '%s' "${WORK}:success" | sha256sum | awk '{print $1}')
mkdir -p -- "${PACKAGE_ROOT}" "${WORK}/metadata" "${WORK}/profiles"
chmod 0755 -- "${WORK}" "${WORK}/app-test" "${PACKAGE_ROOT}" \
    "${WORK}/metadata" "${WORK}/profiles"
printf '%s\n' 'masters = gentoo' > "${WORK}/metadata/layout.conf"
printf '%s\n' phase2-portage-fixture > "${WORK}/profiles/repo_name"
printf '%s\n' app-test > "${WORK}/profiles/categories"

escape_sed() {
    printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

sed \
    -e "s|@CACHE_ROOT@|$(escape_sed "${CACHE_ROOT}")|g" \
    -e "s|@CAPTURE_PROXY@|$(escape_sed "${CAPTURE_PROXY}")|g" \
    -e "s|@SUCCESS_FINGERPRINT@|${SUCCESS_FINGERPRINT}|g" \
    "${TEMPLATE}" > "${EBUILD}"
sed \
    -e "s|@CAPTURE_TOOL@|$(escape_sed "${CAPTURE_TOOL}")|g" \
    -e "s|@FAIL_SWITCH@|$(escape_sed "${FAIL_SWITCH}")|g" \
    "${PROXY_TEMPLATE}" > "${CAPTURE_PROXY}"
chmod 0755 -- "${CAPTURE_PROXY}"
chmod 0644 -- "${EBUILD}" "${WORK}/metadata/layout.conf" "${WORK}/profiles/repo_name"
printf 'EBUILD %s %s BLAKE2B %s SHA512 %s\n' \
    "$(basename -- "${EBUILD}")" "$(stat -c %s "${EBUILD}")" \
    "$(b2sum "${EBUILD}" | awk '{print $1}')" \
    "$(sha512sum "${EBUILD}" | awk '{print $1}')" \
    > "${PACKAGE_ROOT}/Manifest"
chmod 0644 -- "${WORK}/profiles/categories" "${PACKAGE_ROOT}/Manifest"

PORTAGE_TMPDIR=$(portageq envvar PORTAGE_TMPDIR)
[[ ${PORTAGE_TMPDIR} == /* ]] || fail 'Portage returned an unsafe PORTAGE_TMPDIR'
BUILD_ROOT=${PORTAGE_TMPDIR%/}/portage/app-test/phase2-portage-fixture-1

# The installed ebuild CLI cannot invoke its internal `depend` action directly
# (it requires Portage's returnproc plumbing), so dependency sourcing remains
# covered by the exact phase emulator in test-pgo-dispatcher.sh. Exercise the
# real cleanup and build/install processes here.
ebuild "${EBUILD}" clean >"${WORK}/initial-clean.log" 2>&1

# Successful install stops at Portage staging; it does not merge into /usr/VDB.
ebuild "${EBUILD}" install >"${WORK}/install-success.log" 2>&1
MANIFEST=${CACHE_ROOT}/inputs/${SUCCESS_FINGERPRINT}/manifest.json
[[ -s ${MANIFEST} ]] || fail 'real Portage install did not publish the capture manifest'
python3 - "${MANIFEST}" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["eligible_total"] == 1
artifact = manifest["artifacts"][0]
assert artifact["eligible"] is True
assert artifact["has_symtab"] is True
assert artifact["text_relocation_sections"]
assert artifact["build_id"] and artifact["text_sha256"]
PY
cp -- "${MANIFEST}" "${WORK}/first-capture-manifest.json"
[[ ! -e /var/db/pkg/app-test/phase2-portage-fixture-1 ]] || \
    fail 'disposable ebuild unexpectedly entered the installed VDB'
[[ -f ${BUILD_ROOT}/.installed ]] || fail 'successful Portage install lacks its .installed marker'
[[ $(<"${BUILD_ROOT}/temp/previous-hook.log") == previous-hook-passed ]] || \
    fail 'pre-existing post_src_install hook did not run before capture'

# Force the wrapper to fail after Portage creates .installed. The fatal hook
# must remove that marker so a retry cannot bypass src_install/post_src_install.
ebuild "${EBUILD}" clean >"${WORK}/pre-failure-clean.log" 2>&1
rm -rf -- "${CACHE_ROOT}/inputs/${SUCCESS_FINGERPRINT}"
: > "${FAIL_SWITCH}"
if ebuild "${EBUILD}" install >"${WORK}/install-failure.log" 2>&1; then
    fail 'real Portage install accepted a failing BOLT capture wrapper'
fi
[[ ! -e ${BUILD_ROOT}/.installed ]] || \
    fail 'failed post_src_install left Portage .installed behind'

rm -f -- "${FAIL_SWITCH}"
ebuild "${EBUILD}" install >"${WORK}/install-retry.log" 2>&1
[[ -s ${MANIFEST} ]] || fail 'successful retry did not recapture its exact input'
python3 - "${WORK}/first-capture-manifest.json" "${MANIFEST}" <<'PY'
import json
import sys

before = json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"][0]
after = json.load(open(sys.argv[2], encoding="utf-8"))["artifacts"][0]
for key in ("file_sha256", "build_id", "text_sha256"):
    assert before[key] == after[key], key
PY
[[ -f ${BUILD_ROOT}/.installed ]] || fail 'successful retry did not complete install phase'
grep -Fq 'Completed installing app-test/phase2-portage-fixture-1' \
    "${WORK}/install-retry.log" || fail 'retry appears to have skipped src_install'

ebuild "${EBUILD}" clean >"${WORK}/final-clean.log" 2>&1
printf 'PASS: real Portage phase, sandbox, fatal marker, and retry integration fixture\n'

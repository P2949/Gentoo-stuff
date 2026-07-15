#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
readonly ROOT
EBUILD_SOURCE=${ROOT}/optimization/fixtures/portage/phase2-phase-identity-1.ebuild
QA_SOURCE=${ROOT}/optimization/fixtures/portage/phase2-phase-identity-install-qa
OUTPUT_DIR=''
OUTPUT_PARENT=''
OUTPUT_PARTIAL=''
TRUSTED_OUTPUT_BASE=/var/tmp/gentoo-optimization

fail() {
	printf 'FAIL: %s\n' "$*" >&2
	exit 1
}

usage() {
	printf 'Usage: %s [--output-dir NEW-PATH-BELOW-/var/tmp/gentoo-optimization]\n' "$0"
}

while (($#)); do
	case $1 in
		--output-dir)
			(($# >= 2)) || fail '--output-dir requires a value'
			OUTPUT_DIR=$2
			shift 2
			;;
		-h|--help)
			usage
			exit 0
			;;
		*) fail "unknown argument: $1" ;;
	esac
done

if [[ -n ${OUTPUT_DIR} ]]; then
	[[ ${OUTPUT_DIR} == /* && ${OUTPUT_DIR} != / ]] || \
		fail '--output-dir must be an absolute non-root path'
	command -v realpath >/dev/null 2>&1 || fail 'missing command: realpath'
	OUTPUT_DIR=$(realpath -m -- "${OUTPUT_DIR}")
	[[ ${OUTPUT_DIR} == "${TRUSTED_OUTPUT_BASE}"/* ]] || \
		fail '--output-dir must remain below /var/tmp/gentoo-optimization'
	[[ ${OUTPUT_DIR} =~ ^/[A-Za-z0-9_./-]+$ ]] || \
		fail '--output-dir contains unsafe characters'
	[[ ! -e ${OUTPUT_DIR} && ! -L ${OUTPUT_DIR} ]] || \
		fail "--output-dir already exists: ${OUTPUT_DIR}"
fi
((EUID == 0)) || {
	printf 'SKIP: live Portage phase identity requires root\n'
	exit 77
}
for command in awk b2sum cat chmod cp cut ebuild find getent grep head mkdir mktemp \
	mv portageq python3 readlink realpath rm sha256sum sha512sum sort stat sync tail \
	xargs; do
	command -v "${command}" >/dev/null 2>&1 || fail "missing command: ${command}"
done
[[ -f ${EBUILD_SOURCE} && -f ${QA_SOURCE} ]] || fail 'phase identity fixture assets are absent'
FRAMEWORK_CURRENT=/var/lib/gentoo-optimization/framework-current
if [[ ! -L ${FRAMEWORK_CURRENT} ]]; then
	printf 'SKIP: live framework-current is absent; activate the reviewed framework first\n'
	exit 77
fi
ACTIVE_FRAMEWORK=$(readlink -- "${FRAMEWORK_CURRENT}")
[[ ${ACTIVE_FRAMEWORK} =~ ^/var/lib/gentoo-optimization/framework-[0-9a-f]{64}$ && \
	-d ${ACTIVE_FRAMEWORK} && ! -L ${ACTIVE_FRAMEWORK} ]] || \
	fail 'live framework-current selects an unmanaged target'
if [[ -n ${OUTPUT_DIR} ]]; then
	OUTPUT_PARENT=${OUTPUT_DIR%/*}
	[[ -d /var/tmp && ! -L /var/tmp && $(realpath -e -- /var/tmp) == /var/tmp && \
		$(stat -c %u -- /var/tmp) == 0 ]] || \
		fail '/var/tmp is not the canonical root-owned output boundary'
	VAR_TMP_MODE=$(stat -c %a -- /var/tmp)
	(( (8#${VAR_TMP_MODE} & 8#1000) != 0 )) || \
		fail '/var/tmp output boundary lacks the sticky bit'
	CURRENT_OUTPUT_ANCESTOR=${TRUSTED_OUTPUT_BASE}
	OUTPUT_RELATIVE_PARENT=${OUTPUT_PARENT#"${TRUSTED_OUTPUT_BASE}"}
	OUTPUT_RELATIVE_PARENT=${OUTPUT_RELATIVE_PARENT#/}
	IFS=/ read -r -a OUTPUT_ANCESTOR_COMPONENTS <<< "${OUTPUT_RELATIVE_PARENT}"
	for OUTPUT_ANCESTOR_COMPONENT in '' "${OUTPUT_ANCESTOR_COMPONENTS[@]}"; do
		if [[ -n ${OUTPUT_ANCESTOR_COMPONENT} ]]; then
			CURRENT_OUTPUT_ANCESTOR+=/${OUTPUT_ANCESTOR_COMPONENT}
		fi
		[[ -d ${CURRENT_OUTPUT_ANCESTOR} && ! -L ${CURRENT_OUTPUT_ANCESTOR} && \
			$(realpath -e -- "${CURRENT_OUTPUT_ANCESTOR}") == \
			"${CURRENT_OUTPUT_ANCESTOR}" && \
			$(stat -c %u -- "${CURRENT_OUTPUT_ANCESTOR}") == "${EUID}" ]] || \
			fail "untrusted output ancestor: ${CURRENT_OUTPUT_ANCESTOR}"
		OUTPUT_ANCESTOR_MODE=$(stat -c %a -- "${CURRENT_OUTPUT_ANCESTOR}")
		(( (8#${OUTPUT_ANCESTOR_MODE} & 8#022) == 0 )) || \
			fail "group/world-writable output ancestor: ${CURRENT_OUTPUT_ANCESTOR}"
	done
fi

PORTAGE_UID=$(getent passwd portage | cut -d: -f3)
PORTAGE_GID=$(getent group portage | cut -d: -f3)
[[ ${PORTAGE_UID} =~ ^[1-9][0-9]*$ && ${PORTAGE_GID} =~ ^[1-9][0-9]*$ ]] || \
	fail 'could not resolve the nonzero portage account identities'
FEATURES_EFFECTIVE=$(portageq envvar FEATURES)
case " ${FEATURES_EFFECTIVE} " in
	*' userpriv '*) ;;
	*) fail 'live effective FEATURES does not contain userpriv' ;;
esac

WORK=$(mktemp -d /var/tmp/gentoo-phase-identity.XXXXXX)
PACKAGE_ROOT=${WORK}/app-test/phase2-phase-identity
EBUILD=${PACKAGE_ROOT}/phase2-phase-identity-1.ebuild
PORTAGE_TMPDIR=$(portageq envvar PORTAGE_TMPDIR)
[[ ${PORTAGE_TMPDIR} == /* && ${PORTAGE_TMPDIR} != / ]] || fail 'unsafe PORTAGE_TMPDIR'
BUILD_ROOT=${PORTAGE_TMPDIR%/}/portage/app-test/phase2-phase-identity-1

cleanup() {
	local status=$?
	trap '' HUP INT TERM
	if ((status != 0)); then
		printf 'Portage phase identity fixture logs from %s:\n' "${WORK}" >&2
		for log in "${WORK}"/*.log; do
			[[ -f ${log} ]] || continue
			printf '\n--- %s ---\n' "${log}" >&2
			tail -n 160 "${log}" >&2 || :
		done
	fi
	# An interrupted or rejected install can leave the only phase receipt below
	# PORTAGE_TMPDIR.  Capture it before ebuild clean destroys that tree.
	if ((status != 0)) && [[ -s ${BUILD_ROOT}/temp/phase-identity.tsv && \
		! -s ${WORK}/phase-identity.tsv ]]; then
		if ! cp -- "${BUILD_ROOT}/temp/phase-identity.tsv" \
			"${WORK}/phase-identity.partial.tsv"; then
			printf 'FAIL: could not preserve the partial phase identity receipt\n' >&2
			status=1
		fi
	fi
	if ((status != 0)); then
		printf 'exit_status\t%s\n' "${status}" > "${WORK}/fixture-status.tsv" || status=1
		if ! (
			cd -- "${WORK}"
			find . -type f ! -path './failure-evidence.sha256' -print0 |
				LC_ALL=C sort -z | xargs -0r sha256sum -- \
				> failure-evidence.sha256
		); then
			printf 'FAIL: could not create the phase identity failure manifest\n' >&2
			status=1
		fi
		chmod 0700 -- "${WORK}" || status=1
		find "${WORK}" -type f -exec sync -f -- {} + || status=1
		find "${WORK}" -depth -type d -exec sync -f -- {} + || status=1
		sync -f -- /var/tmp || status=1
	fi
	ebuild "${EBUILD}" clean >/dev/null 2>&1 || :
	if [[ -n ${OUTPUT_PARTIAL} && ( -e ${OUTPUT_PARTIAL} || -L ${OUTPUT_PARTIAL} ) ]]; then
		rm -rf -- "${OUTPUT_PARTIAL}"
	fi
	if ((status != 0)); then
		printf 'AUTHORITATIVE_WORK: %s\n' "${WORK}" >&2
	else
		rm -rf -- "${WORK}"
	fi
	trap - EXIT HUP INT TERM
	exit "${status}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p -- "${PACKAGE_ROOT}" "${WORK}/metadata/install-qa-check.d" "${WORK}/profiles"
chmod 0755 -- "${WORK}" "${WORK}/app-test" "${PACKAGE_ROOT}" \
	"${WORK}/metadata" "${WORK}/metadata/install-qa-check.d" "${WORK}/profiles"
cp -- "${EBUILD_SOURCE}" "${EBUILD}"
cp -- "${QA_SOURCE}" "${WORK}/metadata/install-qa-check.d/zz-phase2-phase-identity"
printf '%s\n' 'masters = gentoo' > "${WORK}/metadata/layout.conf"
printf '%s\n' phase2-phase-identity > "${WORK}/profiles/repo_name"
printf '%s\n' app-test > "${WORK}/profiles/categories"
chmod 0644 -- "${EBUILD}" "${WORK}/metadata/layout.conf" \
	"${WORK}/metadata/install-qa-check.d/zz-phase2-phase-identity" \
	"${WORK}/profiles/repo_name" "${WORK}/profiles/categories"
printf 'EBUILD %s %s BLAKE2B %s SHA512 %s\n' \
	"$(basename -- "${EBUILD}")" "$(stat -c %s "${EBUILD}")" \
	"$(b2sum "${EBUILD}" | awk '{print $1}')" \
	"$(sha512sum "${EBUILD}" | awk '{print $1}')" \
	> "${PACKAGE_ROOT}/Manifest"
chmod 0644 -- "${PACKAGE_ROOT}/Manifest"

ebuild "${EBUILD}" clean > "${WORK}/clean.log" 2>&1
ebuild "${EBUILD}" install > "${WORK}/install.log" 2>&1
RECEIPT=${BUILD_ROOT}/temp/phase-identity.tsv
[[ -s ${RECEIPT} ]] || fail 'Portage phase identity receipt is absent'
[[ ! -e /var/db/pkg/app-test/phase2-phase-identity-1 ]] || \
	fail 'disposable phase identity fixture unexpectedly entered the installed VDB'
RECEIPT_EVIDENCE=${WORK}/phase-identity.tsv
cp -- "${RECEIPT}" "${RECEIPT_EVIDENCE}"

PHASE_VALIDATOR=${WORK}/verify-phase-identity.py
cat > "${PHASE_VALIDATOR}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
portage_uid, portage_gid, active_framework = sys.argv[2:5]
rows = {}
for line in path.read_text(encoding="utf-8").splitlines():
    fields = line.split("\t", 8)
    if len(fields) != 9:
        raise SystemExit(f"malformed phase identity row: {line!r}")
    if fields[0] in rows:
        raise SystemExit(f"duplicate phase identity row: {fields[0]}")
    rows[fields[0]] = fields[1:]

required = {
    "src_unpack": (portage_uid, portage_gid),
    "src_compile": (portage_uid, portage_gid),
    "pre_src_install": ("0", "0"),
    "src_install": ("0", "0"),
    "post_src_install": ("0", "0"),
    "install_qa_check": ("0", "0"),
}
if set(rows) != set(required):
    raise SystemExit(f"unexpected phase set: {sorted(rows)}")
for phase, expected_ids in required.items():
    (
        uid,
        gid,
        groups,
        ebuild_phase,
        build_user,
        build_group,
        features,
        framework_target,
    ) = rows[phase]
    if (uid, gid) != expected_ids:
        raise SystemExit(f"{phase}: expected IDs {expected_ids}, got {(uid, gid)}")
    if "userpriv" not in features.split():
        raise SystemExit(f"{phase}: effective FEATURES omitted userpriv")
    if framework_target != active_framework:
        raise SystemExit(
            f"{phase}: framework pin {framework_target!r} differs from "
            f"{active_framework!r}"
        )
    expected_phase = "install" if phase in {
        "pre_src_install", "src_install", "post_src_install"
    } else phase.removeprefix("src_")
    if phase == "install_qa_check":
        # MiscFunctionsProcess deliberately clears EBUILD_PHASE.
        expected_phase = ""
    if ebuild_phase != expected_phase:
        raise SystemExit(
            f"{phase}: expected EBUILD_PHASE={expected_phase!r}, got {ebuild_phase!r}"
        )
    if build_user != "portage" or build_group != "portage":
        raise SystemExit(
            f"{phase}: unexpected build identity labels {build_user}:{build_group}"
        )
print(path.read_text(encoding="utf-8"), end="")
PY
chmod 0500 -- "${PHASE_VALIDATOR}"
python3 "${PHASE_VALIDATOR}" "${RECEIPT_EVIDENCE}" "${PORTAGE_UID}" \
	"${PORTAGE_GID}" "${ACTIVE_FRAMEWORK}"
DUPLICATE_RECEIPT=${WORK}/duplicate-phase-identity.tsv
cp -- "${RECEIPT_EVIDENCE}" "${DUPLICATE_RECEIPT}"
head -n 1 -- "${RECEIPT_EVIDENCE}" >> "${DUPLICATE_RECEIPT}"
if python3 "${PHASE_VALIDATOR}" "${DUPLICATE_RECEIPT}" "${PORTAGE_UID}" \
	"${PORTAGE_GID}" "${ACTIVE_FRAMEWORK}" \
	> "${WORK}/duplicate-receipt.stdout" \
	2> "${WORK}/duplicate-receipt-rejection.log"; then
	fail 'phase identity validator accepted a duplicate required phase row'
fi
grep -Fq 'duplicate phase identity row:' \
	"${WORK}/duplicate-receipt-rejection.log" || \
	fail 'duplicate phase rejection lacked its exact diagnostic'

if [[ -n ${OUTPUT_DIR} ]]; then
	OUTPUT_PARTIAL=${OUTPUT_DIR}.partial.$$
	[[ ! -e ${OUTPUT_DIR} && ! -L ${OUTPUT_DIR} && \
		! -e ${OUTPUT_PARTIAL} && ! -L ${OUTPUT_PARTIAL} ]] || \
		fail 'phase-identity evidence destination is no longer empty'
	mkdir -m 0700 -- "${OUTPUT_PARTIAL}"
	cp -- "${RECEIPT_EVIDENCE}" "${OUTPUT_PARTIAL}/phase-identity.tsv"
	cp -- "${WORK}/install.log" "${OUTPUT_PARTIAL}/install.log"
	cp -- "${WORK}/duplicate-receipt-rejection.log" \
		"${OUTPUT_PARTIAL}/duplicate-receipt-rejection.log"
	cp -- "${PHASE_VALIDATOR}" "${OUTPUT_PARTIAL}/verify-phase-identity.py"
	{
		printf 'fixture_ebuild_sha256\t%s\n' "$(sha256sum "${EBUILD_SOURCE}" | awk '{print $1}')"
		printf 'fixture_qa_sha256\t%s\n' "$(sha256sum "${QA_SOURCE}" | awk '{print $1}')"
		printf 'receipt_validator_sha256\t%s\n' \
			"$(sha256sum "${PHASE_VALIDATOR}" | awk '{print $1}')"
		printf 'portage_version\t%s\n' "$(portageq --version 2>&1 | head -n1)"
		printf 'effective_features\t%s\n' "${FEATURES_EFFECTIVE}"
		printf 'active_framework\t%s\n' "${ACTIVE_FRAMEWORK}"
	} > "${OUTPUT_PARTIAL}/environment.tsv"
	(
		cd -- "${OUTPUT_PARTIAL}"
		sha256sum -- phase-identity.tsv install.log \
			duplicate-receipt-rejection.log verify-phase-identity.py environment.tsv
	) > "${OUTPUT_PARTIAL}/evidence.sha256"
	chmod 0444 -- "${OUTPUT_PARTIAL}/phase-identity.tsv" \
		"${OUTPUT_PARTIAL}/install.log" \
		"${OUTPUT_PARTIAL}/duplicate-receipt-rejection.log" \
		"${OUTPUT_PARTIAL}/verify-phase-identity.py" \
		"${OUTPUT_PARTIAL}/environment.tsv" \
		"${OUTPUT_PARTIAL}/evidence.sha256"
	[[ $(stat -c '%u:%a' -- "${OUTPUT_PARTIAL}") == "${EUID}:700" ]] || \
		fail 'phase-identity partial evidence directory has the wrong identity'
	sync -f -- "${OUTPUT_PARTIAL}/phase-identity.tsv" \
		"${OUTPUT_PARTIAL}/install.log" \
		"${OUTPUT_PARTIAL}/duplicate-receipt-rejection.log" \
		"${OUTPUT_PARTIAL}/verify-phase-identity.py" \
		"${OUTPUT_PARTIAL}/environment.tsv" \
		"${OUTPUT_PARTIAL}/evidence.sha256" "${OUTPUT_PARTIAL}"
	mv -T -- "${OUTPUT_PARTIAL}" "${OUTPUT_DIR}"
	OUTPUT_PARTIAL=''
	sync -f -- "${OUTPUT_PARENT}"
	[[ -d ${OUTPUT_DIR} && ! -L ${OUTPUT_DIR} && \
		$(stat -c '%u:%a' -- "${OUTPUT_DIR}") == "${EUID}:700" ]] || \
		fail 'published phase-identity evidence has the wrong identity'
	(cd -- "${OUTPUT_DIR}" && sha256sum -c -- evidence.sha256) || \
		fail 'published phase-identity evidence does not match its manifest'
fi

printf 'PASS: live Portage userpriv/install identity boundary is verified\n'

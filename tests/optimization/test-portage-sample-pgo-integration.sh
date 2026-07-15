#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
TEMPLATE=${ROOT}/optimization/fixtures/portage/phase2-sample-pgo-fixture-1.ebuild.in
INSTALLER=/var/lib/gentoo-optimization/bootstrap/install-framework.sh
PROFILE_IDENTITY=/usr/local/libexec/gentoo-optimization/pgo/profile-identity.py
VALIDATOR=/usr/local/libexec/gentoo-optimization/pgo/validate-profile.py
LLVM_ROOT=/usr/lib/llvm/22/bin
CLANG_LINK=${LLVM_ROOT}/clang
CLANGXX_LINK=${LLVM_ROOT}/clang++
PROFGEN_LINK=${LLVM_ROOT}/llvm-profgen
PROFDATA_LINK=${LLVM_ROOT}/llvm-profdata
READELF_LINK=${LLVM_ROOT}/llvm-readelf
OBJCOPY_LINK=${LLVM_ROOT}/llvm-objcopy
PERF=/usr/bin/perf
ITERATIONS=${PORTAGE_SAMPLE_PGO_ITERATIONS:-100000000}
KEEP_TEMP=${KEEP_TEMP:-0}
OUTPUT_DIR=
CANONICAL_OUTPUT_DIR=
EXPLICIT_OUTPUT_DIR=0
TRUSTED_OUTPUT_BASE=/var/tmp/gentoo-optimization

usage() {
    cat <<'EOF'
Usage: test-portage-sample-pgo-integration.sh [--output-dir ABSOLUTE_PATH] [--keep-temp]

The optional output directory must be a new path below the trusted
/var/tmp/gentoo-optimization tree. Explicit output directories are always preserved.
EOF
}

while (($#)); do
    case $1 in
        --output-dir)
            (($# >= 2)) || { usage >&2; exit 2; }
            OUTPUT_DIR=$2
            shift 2
            ;;
        --keep-temp)
            KEEP_TEMP=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

if [[ -n ${OUTPUT_DIR} ]]; then
    [[ ${OUTPUT_DIR} == /* && ${OUTPUT_DIR} != / ]] || \
        fail '--output-dir must be an absolute non-root path'
    command -v realpath >/dev/null 2>&1 || fail 'missing command: realpath'
    CANONICAL_OUTPUT_DIR=$(realpath -m -- "${OUTPUT_DIR}")
    [[ ${CANONICAL_OUTPUT_DIR} == "${TRUSTED_OUTPUT_BASE}"/* ]] || \
        fail '--output-dir must remain below /var/tmp/gentoo-optimization'
    [[ ${CANONICAL_OUTPUT_DIR} =~ ^/[A-Za-z0-9_./-]+$ ]] || \
        fail '--output-dir contains unsafe characters'
    [[ ! -e ${CANONICAL_OUTPUT_DIR} && ! -L ${CANONICAL_OUTPUT_DIR} ]] || \
        fail "--output-dir already exists: ${CANONICAL_OUTPUT_DIR}"
fi
((EUID == 0)) || {
    printf 'SKIP: real Portage sample-PGO integration requires root\n'
    exit 77
}
[[ ${ITERATIONS} =~ ^[1-9][0-9]*$ ]] || fail 'iteration count must be positive'
[[ ${KEEP_TEMP} == 0 || ${KEEP_TEMP} == 1 ]] || fail 'KEEP_TEMP must be 0 or 1'
for command in awk b2sum chmod chown cp cmp cut date ebuild find getent grep \
    hostname id ln mkdir mktemp mv perf portageq python3 readelf readlink rm runuser sed \
    realpath sha256sum sha512sum sort stat sync tail timeout xargs; do
    command -v "${command}" >/dev/null 2>&1 || fail "missing command: ${command}"
done
[[ -f ${TEMPLATE} && -x ${INSTALLER} ]] || fail 'fixture template or installer is absent'
for tool in "${PROFILE_IDENTITY}" "${VALIDATOR}" "${CLANG_LINK}" \
    "${CLANGXX_LINK}" "${PROFGEN_LINK}" "${PROFDATA_LINK}" \
    "${READELF_LINK}" "${OBJCOPY_LINK}" "${PERF}"; do
    [[ -x ${tool} ]] || fail "required exact tool is absent: ${tool}"
done
"${INSTALLER}" --source-root "${ROOT}" --check >/dev/null || \
    fail 'installed framework differs from the reviewed repository source'

if [[ -n ${OUTPUT_DIR} ]]; then
    OUTPUT_PARENT=${CANONICAL_OUTPUT_DIR%/*}
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
    EXPLICIT_OUTPUT_DIR=1
fi
WORK=$(mktemp -d /var/tmp/gentoo-phase2-pgo-portage.sample.XXXXXX)
printf 'gentoo-optimization-portage-sample-fixture-v1\n' > \
    "${WORK}/.optimization-fixture-root"

emit_publication_tree() {
    local root=$1
    python3 - "${root}" <<'PY'
import base64
import json
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
excluded = {"publication-tree.jsonl", "publication-root.sha256"}
raw: list[tuple[pathlib.Path, os.stat_result]] = []
for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
    relative = path.relative_to(root)
    if relative.as_posix() in excluded:
        continue
    raw.append((relative, path.lstat()))

inode_paths: dict[tuple[int, int], list[str]] = {}
for relative, metadata in raw:
    if stat.S_ISREG(metadata.st_mode):
        inode_paths.setdefault((metadata.st_dev, metadata.st_ino), []).append(
            relative.as_posix()
        )

for relative, metadata in raw:
    path = root / relative
    mode = metadata.st_mode
    if stat.S_ISREG(mode):
        kind = "regular"
    elif stat.S_ISDIR(mode):
        kind = "directory"
    elif stat.S_ISLNK(mode):
        kind = "symlink"
    else:
        raise SystemExit(f"unsupported evidence node: {relative.as_posix()}")
    names = sorted(os.listxattr(path, follow_symlinks=False))
    xattrs = [
        {
            "name": name,
            "value_base64": base64.b64encode(
                os.getxattr(path, name, follow_symlinks=False)
            ).decode("ascii"),
        }
        for name in names
    ]
    hardlinks: list[str] = []
    if kind == "regular":
        hardlinks = sorted(inode_paths[(metadata.st_dev, metadata.st_ino)])
        if metadata.st_nlink != len(hardlinks):
            raise SystemExit(
                f"external hardlink in evidence tree: {relative.as_posix()}"
            )
    record = {
        "gid": metadata.st_gid,
        "hardlink_paths": hardlinks,
        "kind": kind,
        "link_count": metadata.st_nlink,
        "mode": stat.S_IMODE(mode),
        "mtime_ns": metadata.st_mtime_ns,
        "path": relative.as_posix(),
        "size": metadata.st_size,
        "symlink_target": os.readlink(path) if kind == "symlink" else None,
        "uid": metadata.st_uid,
        "xattrs": xattrs,
    }
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
PY
}

prepare_publication_manifests() {
    local evidence_gid=${PORTAGE_GID:-0}
    (
        cd -- "${WORK}"
        find . -type f \
            ! -path './publication-files.sha256' \
            ! -path './publication-tree.jsonl' \
            ! -path './publication-root.sha256' \
            -print0 | LC_ALL=C sort -z | xargs -0r sha256sum -- \
            > publication-files.sha256
    ) || return 1
    chown "0:${evidence_gid}" -- "${WORK}/publication-files.sha256" || return 1
    chmod 0440 -- "${WORK}/publication-files.sha256" || return 1
    emit_publication_tree "${WORK}" > "${WORK}/publication-tree.jsonl" || return 1
    chown "0:${evidence_gid}" -- "${WORK}/publication-tree.jsonl" || return 1
    chmod 0440 -- "${WORK}/publication-tree.jsonl" || return 1
    (
        cd -- "${WORK}"
        sha256sum -- publication-files.sha256 publication-tree.jsonl \
            > publication-root.sha256
    ) || return 1
    chown "0:${evidence_gid}" -- "${WORK}/publication-root.sha256" || return 1
    chmod 0440 -- "${WORK}/publication-root.sha256"
}

seal_authoritative_work() {
    local evidence_gid=${PORTAGE_GID:-0} unexpected
    # Reject special nodes and links escaping this evidence tree before any
    # recursive metadata operation could affect an external inode.
    emit_publication_tree "${WORK}" >/dev/null || return 1
    find "${WORK}" -xdev -type l -exec chown -h "0:${evidence_gid}" -- {} + || return 1
    find "${WORK}" -xdev \( -type f -o -type d \) \
        -exec chown "0:${evidence_gid}" -- {} + || return 1
    find "${WORK}" -xdev \( -type f -o -type d \) \
        -exec chmod go-w -- {} + || return 1
    if [[ -n ${PORTAGE_GID:-} ]]; then
        chmod 0750 -- "${WORK}" || return 1
    else
        chmod 0700 -- "${WORK}" || return 1
    fi
    unexpected=$(find "${WORK}" -xdev \
        \( \( -type f -o -type d -o -type l \) \
        \( ! -uid 0 -o ! -gid "${evidence_gid}" \) \) -print -quit) || return 1
    [[ -z ${unexpected} ]] || {
        printf 'FAIL: unsealed evidence owner: %s\n' "${unexpected}" >&2
        return 1
    }
    unexpected=$(find "${WORK}" -xdev \( -type f -o -type d \) \
        -perm /022 -print -quit) || return 1
    [[ -z ${unexpected} ]] || {
        printf 'FAIL: writable authoritative evidence node: %s\n' "${unexpected}" >&2
        return 1
    }
}

sync_authoritative_work() {
    find "${WORK}" -type f -exec sync -f -- {} + &&
        find "${WORK}" -depth -type d -exec sync -f -- {} + &&
        sync -f -- /var/tmp
}

finalize_authoritative_work() {
    local status=$1 evidence_gid=${PORTAGE_GID:-0}
    rm -f -- "${WORK}/publication-files.sha256" \
        "${WORK}/publication-tree.jsonl" "${WORK}/publication-root.sha256"
    seal_authoritative_work || return 1
    printf 'exit_status\t%s\n' "${status}" > "${WORK}/fixture-status.tsv" || return 1
    chown "0:${evidence_gid}" -- "${WORK}/fixture-status.tsv" || return 1
    chmod 0440 -- "${WORK}/fixture-status.tsv" || return 1
    prepare_publication_manifests || return 1
    seal_authoritative_work || return 1
    sync_authoritative_work
}

quarantine_published_evidence() {
    local quarantine=${CANONICAL_OUTPUT_DIR}.failed.$$
    [[ -e ${CANONICAL_OUTPUT_DIR} || -L ${CANONICAL_OUTPUT_DIR} ]] || return 0
    [[ ! -e ${quarantine} && ! -L ${quarantine} ]] || return 1
    mv -T -- "${CANONICAL_OUTPUT_DIR}" "${quarantine}" || return 1
    sync -f -- "${OUTPUT_PARENT}" || return 1
    printf 'FAILED_EVIDENCE: %s\n' "${quarantine}" >&2
}

publish_evidence() {
    local status=$1 partial=${CANONICAL_OUTPUT_DIR}.partial.$$
    [[ ${EXPLICIT_OUTPUT_DIR} == 1 ]] || return 0
    if [[ -e ${CANONICAL_OUTPUT_DIR} || -L ${CANONICAL_OUTPUT_DIR} || \
        -e ${partial} || -L ${partial} ]]; then
        printf 'FAIL: sample-PGO evidence publication destination is no longer empty\n' >&2
        return 1
    fi
    if ! cp -a -- "${WORK}" "${partial}" || ! chmod 0700 -- "${partial}"; then
        rm -rf -- "${partial}"
        printf 'FAIL: could not create the private partial evidence tree\n' >&2
        return 1
    fi
    if [[ ! -d ${partial} || -L ${partial} || \
        $(stat -c '%u:%a' -- "${partial}") != "${EUID}:700" ]]; then
        rm -rf -- "${partial}"
        printf 'FAIL: partial evidence publication has the wrong identity\n' >&2
        return 1
    fi
    if ! find "${partial}" -type f -exec sync -f -- {} + || \
        ! find "${partial}" -depth -type d -exec sync -f -- {} +; then
        rm -rf -- "${partial}"
        printf 'FAIL: evidence partial could not be made durable\n' >&2
        return 1
    fi
    if ! (cd -- "${partial}" && \
        sha256sum -c -- publication-files.sha256 publication-root.sha256) || \
        ! cmp -s -- "${partial}/publication-tree.jsonl" \
            <(emit_publication_tree "${partial}"); then
        rm -rf -- "${partial}"
        printf 'FAIL: evidence partial does not match its exhaustive integrity root\n' >&2
        return 1
    fi
    if ((status == 0)) && ! (cd -- "${partial}" && \
        sha256sum -c -- evidence.sha256 generated-policy.sha256); then
        rm -rf -- "${partial}"
        printf 'FAIL: successful evidence partial fails its inner manifests\n' >&2
        return 1
    fi
    if ! mv -T -- "${partial}" "${CANONICAL_OUTPUT_DIR}"; then
        rm -rf -- "${partial}"
        printf 'FAIL: atomic evidence publication rename failed\n' >&2
        return 1
    fi
    if ! sync -f -- "${OUTPUT_PARENT}"; then
        quarantine_published_evidence || \
            printf 'FAIL: could not quarantine the visible failed publication\n' >&2
        printf 'FAIL: evidence publication parent could not be made durable\n' >&2
        return 1
    fi
}

cleanup() {
    local status=$? finalized=0
    trap '' HUP INT TERM
    if ((status != 0)); then
        printf 'Sample-PGO fixture evidence remains readable in %s until cleanup.\n' "${WORK}" >&2
        for log in "${WORK}"/*.log; do
            [[ -f ${log} ]] || continue
            printf '\n--- %s ---\n' "${log}" >&2
            tail -n 160 -- "${log}" >&2 || :
        done
    fi
    if finalize_authoritative_work "${status}"; then
        finalized=1
    else
        printf 'FAIL: could not durably finalize the authoritative sample-PGO evidence tree\n' >&2
        status=1
        if finalize_authoritative_work "${status}"; then
            finalized=1
        else
            printf 'FAIL: failed evidence remains preserved but lacks a complete integrity root\n' >&2
        fi
    fi
    if ((finalized == 1)); then
        if ! publish_evidence "${status}"; then
            printf 'FAIL: could not publish complete sample-PGO fixture evidence\n' >&2
            status=1
            # The publication copy is historical.  The preserved Work tree is
            # the authoritative object referenced by the validator sidecars,
            # so make its final failure state durable even if copy publication
            # failed.
            if ! finalize_authoritative_work "${status}"; then
                printf 'FAIL: could not durably record the publication failure\n' >&2
            fi
        elif [[ ${EXPLICIT_OUTPUT_DIR} == 1 ]]; then
            printf 'EVIDENCE: %s\n' "${CANONICAL_OUTPUT_DIR}"
        fi
    fi
    if [[ ${KEEP_TEMP} == 0 && ${EXPLICIT_OUTPUT_DIR} == 0 && ${status} == 0 ]]; then
        rm -rf -- "${WORK}"
    else
        printf 'AUTHORITATIVE_WORK: %s\n' "${WORK}"
    fi
    trap - EXIT HUP INT TERM
    exit "${status}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

PACKAGE_ROOT=${WORK}/app-test/phase2-pgo-use-fixture
EBUILD=${PACKAGE_ROOT}/phase2-pgo-use-fixture-1.ebuild
CONFIG_ROOT=${WORK}/config-root
PORTAGE_ROOT=${CONFIG_ROOT}/etc/portage
PORTAGE_TMP=${WORK}/portage-tmp
BUILD_ROOT=${PORTAGE_TMP}/portage/app-test/phase2-pgo-use-fixture-1
FLAGS_FILE=${BUILD_ROOT}/temp/effective-flags.tsv
PROFILE_ROOT=${WORK}/profile
PERF_DATA=${WORK}/perf.data
PROFILE=${PROFILE_ROOT}/sample.prof
SAMPLE_METADATA=${PROFILE_ROOT}/sample-metadata.json
CONVERSION_LOG=${PROFILE_ROOT}/llvm-profgen-conversion-log.json
MANIFEST=${PROFILE_ROOT}/profile.manifest
SIDECAR=${MANIFEST}.metadata.json
GENERATED_ASSIGNMENT=${PORTAGE_ROOT}/package.env/zz-generated-sample-pgo
MAP_ENV=${PORTAGE_ROOT}/env/generated-sample-map.conf
USE_ENV=${PORTAGE_ROOT}/env/generated-sample-use.conf
VALIDATOR_PROXY=${WORK}/validator-proxy
VALIDATOR_IDENTITY=${WORK}/validator-identity.tsv
FRAMEWORK_LOCK=${WORK}/framework.lock
PROJECT_LOCK=${WORK}/project.lock
GENERATION_LOCK=${WORK}/generation.lock
GENERATION_ID=phase2-sample-portage-fixture-v1
INVENTORY_ID=phase2-sample-portage-inventory-v1
INVENTORY_SHA256=$(printf '%s' "${WORK}:inventory" | sha256sum | awk '{print $1}')
GENTOO_REPO=$(portageq get_repo_path / gentoo)
PROFILE_LINK=$(readlink -f -- /etc/portage/make.profile)
PORTAGE_GID=$(getent group portage | awk -F: '$1 == "portage" {print $3; exit}')
PORTAGE_UID=$(getent passwd portage | awk -F: '$1 == "portage" {print $3; exit}')

for value in "${GENTOO_REPO}" "${PROFILE_LINK}"; do
    [[ ${value} == /* && -d ${value} ]] || fail "invalid live Portage path: ${value}"
done
[[ ${PORTAGE_GID} =~ ^[1-9][0-9]*$ && ${PORTAGE_UID} =~ ^[1-9][0-9]*$ ]] || \
    fail 'cannot resolve the nonzero Portage user/group'
mkdir -p -- "${PACKAGE_ROOT}" "${WORK}/metadata" "${WORK}/profiles" \
    "${PORTAGE_ROOT}/env" "${PORTAGE_ROOT}/package.env" \
    "${PORTAGE_ROOT}/repos.conf" "${PORTAGE_TMP}" "${WORK}/distfiles" \
    "${WORK}/binpkgs" "${PROFILE_ROOT}"
chmod 0755 -- "${WORK}" "${WORK}/app-test" "${PACKAGE_ROOT}" \
    "${WORK}/metadata" "${WORK}/profiles" "${CONFIG_ROOT}" \
    "${CONFIG_ROOT}/etc" "${PORTAGE_ROOT}" "${PORTAGE_ROOT}/env" \
    "${PORTAGE_ROOT}/package.env" "${PORTAGE_ROOT}/repos.conf" \
    "${PORTAGE_TMP}" "${WORK}/distfiles" "${WORK}/binpkgs"
chown "0:${PORTAGE_GID}" -- "${PROFILE_ROOT}"
chmod 0750 -- "${PROFILE_ROOT}"
cp -- "${TEMPLATE}" "${EBUILD}"
cp --dereference -- /etc/portage/bashrc "${PORTAGE_ROOT}/bashrc"
ln -s -- "${PROFILE_LINK}" "${PORTAGE_ROOT}/make.profile"
printf '%s\n' 'masters = gentoo' > "${WORK}/metadata/layout.conf"
printf '%s\n' phase2-sample-pgo-fixture > "${WORK}/profiles/repo_name"
printf '%s\n' app-test > "${WORK}/profiles/categories"
printf '%s\n' \
    '[gentoo]' \
    "location = ${GENTOO_REPO}" \
    '' \
    '[phase2-sample-pgo-fixture]' \
    "location = ${WORK}" \
    'masters = gentoo' \
    > "${PORTAGE_ROOT}/repos.conf/repos.conf"

CLANG=$(readlink -f -- "${CLANG_LINK}")
CLANGXX=$(readlink -f -- "${CLANGXX_LINK}")
PROFGEN=$(readlink -f -- "${PROFGEN_LINK}")
PROFDATA=$(readlink -f -- "${PROFDATA_LINK}")
LLVM_READELF=$(readlink -f -- "${READELF_LINK}")
LLVM_OBJCOPY=$(readlink -f -- "${OBJCOPY_LINK}")
for tool in "${CLANG}" "${CLANGXX}" "${PROFGEN}" "${PROFDATA}" \
    "${LLVM_READELF}" "${LLVM_OBJCOPY}"; do
    [[ -f ${tool} && -x ${tool} && ! -L ${tool} ]] || \
        fail "LLVM tool did not resolve to a regular executable: ${tool}"
done

printf '%s\n' \
    "CC=\"${CLANG}\"" \
    "CXX=\"${CLANGXX}\"" \
    'CHOST="x86_64-pc-linux-gnu"' \
    'ABI="amd64"' \
    'CFLAGS="-O2 -pipe -fno-omit-frame-pointer"' \
    'CXXFLAGS="-O2 -pipe -fno-omit-frame-pointer"' \
    'FCFLAGS="-O2 -pipe"' \
    'FFLAGS="-O2 -pipe"' \
    'LDFLAGS="-fuse-ld=lld"' \
    'RUSTFLAGS=""' \
    'FEATURES="userpriv -ccache -distcc -icecream -sandbox -usersandbox -network-sandbox -pid-sandbox -ipc-sandbox nostrip"' \
    'ACCEPT_KEYWORDS="**"' \
    'MAKEOPTS="-j2"' \
    "PORTAGE_TMPDIR=\"${PORTAGE_TMP}\"" \
    "DISTDIR=\"${WORK}/distfiles\"" \
    "PKGDIR=\"${WORK}/binpkgs\"" \
    > "${PORTAGE_ROOT}/make.conf"
chmod 0644 -- "${EBUILD}" "${PORTAGE_ROOT}/bashrc" \
    "${PORTAGE_ROOT}/make.conf" "${PORTAGE_ROOT}/repos.conf/repos.conf" \
    "${WORK}/metadata/layout.conf" "${WORK}/profiles/repo_name" \
    "${WORK}/profiles/categories"
printf 'EBUILD %s %s BLAKE2B %s SHA512 %s\n' \
    "$(basename -- "${EBUILD}")" "$(stat -c %s -- "${EBUILD}")" \
    "$(b2sum -- "${EBUILD}" | awk '{print $1}')" \
    "$(sha512sum -- "${EBUILD}" | awk '{print $1}')" \
    > "${PACKAGE_ROOT}/Manifest"
chmod 0644 -- "${PACKAGE_ROOT}/Manifest"

python3 - "${PROJECT_LOCK}" "${GENERATION_LOCK}" "${GENERATION_ID}" \
    "${INVENTORY_ID}" "${INVENTORY_SHA256}" <<'PY'
import json
import pathlib
import sys

payload = json.dumps(
    {
        "generation_id": sys.argv[3],
        "inventory_id": sys.argv[4],
        "inventory_sha256": sys.argv[5],
    },
    indent=2,
    sort_keys=True,
) + "\n"
for path in map(pathlib.Path, sys.argv[1:3]):
    path.write_text(payload, encoding="utf-8")
PY
: > "${FRAMEWORK_LOCK}"
chown "0:${PORTAGE_GID}" -- \
    "${FRAMEWORK_LOCK}" "${PROJECT_LOCK}" "${GENERATION_LOCK}"
chmod 0640 -- "${FRAMEWORK_LOCK}" "${PROJECT_LOCK}" "${GENERATION_LOCK}"
: > "${VALIDATOR_IDENTITY}"
chown "0:${PORTAGE_GID}" -- "${VALIDATOR_IDENTITY}"
chmod 0660 -- "${VALIDATOR_IDENTITY}"
printf '%s\n' \
    '#!/bin/sh' \
    "printf '%s\\t%s\\t%s\\t%s\\n' \"\${1-}\" \"\$(/usr/bin/id -u)\" \"\$(/usr/bin/id -g)\" \"\${EBUILD_PHASE-}\" >> '${VALIDATOR_IDENTITY}' || exit 1" \
    "exec '${VALIDATOR}' \"\$@\" --test-mode --test-framework-lock '${FRAMEWORK_LOCK}' --test-project-lock '${PROJECT_LOCK}' --test-generation-lock '${GENERATION_LOCK}'" \
    > "${VALIDATOR_PROXY}"
chmod 0755 -- "${VALIDATOR_PROXY}"
for lock in "${FRAMEWORK_LOCK}" "${PROJECT_LOCK}" "${GENERATION_LOCK}"; do
    [[ $(stat -c '%u:%g:%a:%h' -- "${lock}") == "0:${PORTAGE_GID}:640:1" ]] || \
        fail "fixture lock has an unsafe identity: ${lock}"
    runuser -u portage -- test -r "${lock}" || \
        fail "Portage cannot read fixture lock: ${lock}"
done

write_map_environment() {
    local fingerprint=$1 output=${MAP_ENV}.partial
    printf '%s\n' \
        'GENTOO_OPT_PORTAGE_FIXTURE_MODE="1"' \
        'GENTOO_OPT_MODE="off"' \
        'GENTOO_OPT_PROFILE_MAP_READY="1"' \
        'GENTOO_OPT_COMPILER_FAMILY="clang"' \
        'GENTOO_OPT_ABI="amd64"' \
        "GENTOO_OPT_FINGERPRINT=\"${fingerprint}\"" \
        > "${output}"
    chmod 0644 -- "${output}"
    mv -- "${output}" "${MAP_ENV}"
    printf '%s\n' '=app-test/phase2-pgo-use-fixture-1 generated-sample-map.conf' \
        > "${GENERATED_ASSIGNMENT}.partial"
    chmod 0644 -- "${GENERATED_ASSIGNMENT}.partial"
    mv -- "${GENERATED_ASSIGNMENT}.partial" "${GENERATED_ASSIGNMENT}"
}

write_use_environment() {
    local fingerprint=$1 output=${USE_ENV}.partial
    printf '%s\n' \
        'GENTOO_OPT_PORTAGE_FIXTURE_MODE="1"' \
        'GENTOO_OPT_MODE="clang-sample-use"' \
        'GENTOO_OPT_PROFILE_MAP_READY="0"' \
        'GENTOO_OPT_COMPILER_FAMILY="clang"' \
        'GENTOO_OPT_ABI="amd64"' \
        "GENTOO_OPT_FINGERPRINT=\"${fingerprint}\"" \
        "GENTOO_OPT_PROFILE_PATH=\"${PROFILE}\"" \
        "GENTOO_OPT_PROFILE_MANIFEST=\"${MANIFEST}\"" \
        "GENTOO_OPT_PROFILE_METADATA=\"${SIDECAR}\"" \
        "GENTOO_OPT_PROFILE_VALIDATOR=\"${VALIDATOR_PROXY}\"" \
        > "${output}"
    chmod 0644 -- "${output}"
    mv -- "${output}" "${USE_ENV}"
    printf '%s\n' '=app-test/phase2-pgo-use-fixture-1 generated-sample-use.conf' \
        > "${GENERATED_ASSIGNMENT}.partial"
    chmod 0644 -- "${GENERATED_ASSIGNMENT}.partial"
    mv -- "${GENERATED_ASSIGNMENT}.partial" "${GENERATED_ASSIGNMENT}"
}

write_use_probe_environment() {
    local output=${USE_ENV}.partial
    printf '%s\n' \
        'GENTOO_OPT_PORTAGE_FIXTURE_MODE="1"' \
        'GENTOO_OPT_MODE="off"' \
        'GENTOO_OPT_PROFILE_MAP_READY="0"' \
        'GENTOO_OPT_COMPILER_FAMILY="clang"' \
        'GENTOO_OPT_ABI="amd64"' \
        > "${output}"
    chmod 0644 -- "${output}"
    mv -- "${output}" "${USE_ENV}"
    printf '%s\n' '=app-test/phase2-pgo-use-fixture-1 generated-sample-use.conf' \
        > "${GENERATED_ASSIGNMENT}.partial"
    chmod 0644 -- "${GENERATED_ASSIGNMENT}.partial"
    mv -- "${GENERATED_ASSIGNMENT}.partial" "${GENERATED_ASSIGNMENT}"
}

run_ebuild() {
    local log=$1
    shift
    PORTAGE_CONFIGROOT=${CONFIG_ROOT} NOCOLOR=true \
        ebuild --color n "${EBUILD}" "$@" > "${log}" 2>&1
}

field() {
    local key=$1 path=$2
    awk -F '\t' -v key="${key}" '$1 == key {sub($1 FS, ""); print; exit}' "${path}"
}

build_fingerprint_input() {
    local receipt=$1 environment_name=$2 transform=$3 output=$4 expected=$5
    python3 - "${receipt}" "${environment_name}" "${transform}" \
        "${output}" "${expected}" "${EBUILD_SHA}" "${CLANG}" "${PROFILE}" <<'PY'
import json
import pathlib
import sys

receipt_path, environment_name, transform, output, expected, ebuild_sha, compiler, profile = sys.argv[1:]
values = {}
for line in pathlib.Path(receipt_path).read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("\t")
    if not separator or key in values:
        raise SystemExit(f"invalid or duplicate receipt field: {line!r}")
    values[key] = value

axes = (
    "CFLAGS", "CXXFLAGS", "LDFLAGS", "RUSTFLAGS", "GOFLAGS",
    "FEATURES", "USE", "EXTRA_ECONF", "EXTRA_EMESON", "EXTRA_ECMAKE",
)
missing = [key for key in axes if key not in values]
if missing:
    raise SystemExit(f"receipt lacks exact axes: {missing}")

def append_once(value: str, flag: str) -> str:
    if flag in value.split():
        return value
    return f"{value} {flag}" if value else flag

resolved = {key: values[key] for key in axes}
if transform == "sample-use":
    profile_flag = f"-fprofile-sample-use={profile}"
    for key in ("CFLAGS", "CXXFLAGS"):
        resolved[key] = append_once(resolved[key], profile_flag)
        resolved[key] = append_once(resolved[key], "-fsample-profile-use-profi")
    for flag in ("-ccache", "-distcc", "-icecream"):
        resolved["FEATURES"] = append_once(resolved["FEATURES"], flag)
elif transform != "observed":
    raise SystemExit(f"unknown fingerprint transform: {transform}")

def exact_tokens(value: str) -> list[str]:
    return sorted(set(value.split()))

document = {
    "schema_version": 2,
    "category": "app-test",
    "pf": "phase2-pgo-use-fixture-1",
    "slot": "0",
    "subslot": "0",
    "repository": "phase2-sample-pgo-fixture",
    "ebuild_sha256": ebuild_sha,
    "eapi": "8",
    "chost": "x86_64-pc-linux-gnu",
    "abi": "amd64",
    "compiler": {
        "path": compiler,
        "family": "clang",
        "major": 22,
        "profile_format": "llvm-sample-v22",
    },
    "use_flags": exact_tokens(resolved["USE"]),
    "cflags": resolved["CFLAGS"],
    "cxxflags": resolved["CXXFLAGS"],
    "ldflags": resolved["LDFLAGS"],
    "rustflags": resolved["RUSTFLAGS"],
    "goflags": resolved["GOFLAGS"],
    "features": exact_tokens(resolved["FEATURES"]),
    "package_env_files": [environment_name],
    "extra_econf": resolved["EXTRA_ECONF"],
    "extra_emeson": resolved["EXTRA_EMESON"],
    "extra_ecmake": resolved["EXTRA_ECMAKE"],
    "kernel_module": False,
    "kernel_release": None,
    "rust_target_triple": None,
    "rustc_llvm_version": None,
}
pathlib.Path(output).write_text(
    json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
pathlib.Path(expected).write_text(
    json.dumps(resolved, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
}

assert_receipt_axes() {
    local receipt=$1 expected=$2 label=$3
    python3 - "${receipt}" "${expected}" "${label}" <<'PY'
import json
import pathlib
import sys

receipt, expected_path, label = sys.argv[1:]
values = {}
for line in pathlib.Path(receipt).read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("\t")
    if not separator or key in values:
        raise SystemExit(f"{label}: invalid or duplicate receipt field {line!r}")
    values[key] = value
expected = json.loads(pathlib.Path(expected_path).read_text(encoding="utf-8"))
failures = {
    key: {"expected": expected_value, "observed": values.get(key)}
    for key, expected_value in expected.items()
    if values.get(key) != expected_value
}
if failures:
    raise SystemExit(f"{label}: exact build axes differ: {json.dumps(failures, sort_keys=True)}")
PY
}

# Resolve the exact dispatcher-expanded mapping axes once, then bind an
# authoritative rebuild to their canonical package fingerprint.
SEED_FINGERPRINT=$(printf 'a%.0s' {1..64})
write_map_environment "${SEED_FINGERPRINT}"
run_ebuild "${WORK}/preliminary-clean.log" clean
run_ebuild "${WORK}/preliminary-map-build.log" compile
[[ -s ${FLAGS_FILE} ]] || fail 'preliminary mapping build emitted no exact flag receipt'
cp -- "${FLAGS_FILE}" "${WORK}/preliminary-effective-flags.tsv"
MAPPING_CFLAGS=$(field CFLAGS "${FLAGS_FILE}")
MAPPING_LDFLAGS=$(field LDFLAGS "${FLAGS_FILE}")
ACTIVE_COMPILER=$(field active_compiler "${FLAGS_FILE}")
[[ ${ACTIVE_COMPILER} == "${CLANG}" ]] || fail 'dispatcher selected an unexpected compiler'
for flag in -gline-tables-only -fdebug-info-for-profiling \
    -funique-internal-linkage-names -fpseudo-probe-for-profiling; do
    [[ " ${MAPPING_CFLAGS} " == *" ${flag} "* ]] || \
        fail "mapping-ready build lacks ${flag}"
done
[[ ${MAPPING_CFLAGS} != *'-fprofile-sample-use='* && \
    ${MAPPING_CFLAGS} != *'-fprofile-use='* ]] || \
    fail 'mapping build unexpectedly consumed a profile'
[[ " ${MAPPING_LDFLAGS} " == *' -Wl,--build-id=sha1 '* && \
    ${MAPPING_LDFLAGS} != *'--emit-relocs'* ]] || \
    fail 'mapping build lacks its exact stage-owned build ID or carries BOLT relocations'
[[ $(awk -v value="${MAPPING_LDFLAGS}" 'BEGIN {count=gsub(/-Wl,--build-id=sha1/, "", value); print count}') == 1 ]] || \
    fail 'mapping build carries a duplicate build-ID policy'

EBUILD_SHA=$(sha256sum -- "${EBUILD}"); EBUILD_SHA=${EBUILD_SHA%% *}
build_fingerprint_input "${WORK}/preliminary-effective-flags.tsv" \
    generated-sample-map.conf observed "${WORK}/map-fingerprint-input.json" \
    "${WORK}/map-expected-axes.json"
MAP_FINGERPRINT=$("${PROFILE_IDENTITY}" fingerprint \
    --input "${WORK}/map-fingerprint-input.json" \
    --metadata-out "${WORK}/map-fingerprint-metadata.json")
[[ ${MAP_FINGERPRINT} =~ ^[0-9a-f]{64}$ ]] || \
    fail 'invalid canonical mapping fingerprint'

write_map_environment "${MAP_FINGERPRINT}"
cp -- "${GENERATED_ASSIGNMENT}" "${WORK}/generated-package-env-map"
run_ebuild "${WORK}/map-clean.log" clean
run_ebuild "${WORK}/map-build.log" compile
[[ -s ${FLAGS_FILE} ]] || fail 'authoritative mapping build emitted no flag receipt'
cp -- "${FLAGS_FILE}" "${WORK}/map-effective-flags.tsv"
[[ $(field active_fingerprint "${FLAGS_FILE}") == "${MAP_FINGERPRINT}" ]] || \
    fail 'mapping build did not load the generated fingerprint assignment'
[[ $(field phase_euid "${FLAGS_FILE}") == "${PORTAGE_UID}" && \
    $(field phase_egid "${FLAGS_FILE}") == "${PORTAGE_GID}" ]] || \
    fail 'mapping compilation did not run through the live userpriv identity'
assert_receipt_axes "${WORK}/map-effective-flags.tsv" \
    "${WORK}/map-expected-axes.json" 'fingerprint-bound mapping rebuild'
TRAIN_BINARY=${BUILD_ROOT}/work/phase2-pgo-use-fixture-1/phase2-pgo-use-fixture
[[ -x ${TRAIN_BINARY} ]] || fail 'mapping-ready Portage build produced no executable'
MAPPED_BINARY=${PROFILE_ROOT}/mapping-input
mv -- "${TRAIN_BINARY}" "${MAPPED_BINARY}"
chown "0:${PORTAGE_GID}" -- "${MAPPED_BINARY}"
chmod 0550 -- "${MAPPED_BINARY}"
[[ $(stat -c '%u:%g:%a:%h' -- "${MAPPED_BINARY}") == \
    "0:${PORTAGE_GID}:550:1" ]] || fail 'mapping binary is not immutable and Portage-readable'
runuser -u portage -- test -x "${MAPPED_BINARY}" || \
    fail 'Portage cannot execute the preserved mapping input'
readelf -nW -- "${MAPPED_BINARY}" > "${WORK}/map-binary.notes"
grep -Eq 'Build ID: [0-9a-fA-F]+' "${WORK}/map-binary.notes" || \
    fail 'mapping-ready executable has no GNU build ID'
readelf -SW -- "${MAPPED_BINARY}" > "${WORK}/map-binary.sections"

# Resolve the ordinary build axes under the exact future package.env filename.
# The consumer-only flag transformation is deterministic; the real use build
# below must reproduce every predicted axis before its fingerprint is accepted.
write_use_probe_environment
run_ebuild "${WORK}/use-probe-clean.log" clean
run_ebuild "${WORK}/use-probe-build.log" compile
[[ -s ${FLAGS_FILE} ]] || fail 'ordinary consumer probe emitted no exact flag receipt'
cp -- "${FLAGS_FILE}" "${WORK}/use-probe-effective-flags.tsv"
[[ $(field mode "${FLAGS_FILE}") == off ]] || fail 'consumer probe was not ordinary/off'
for forbidden in -fprofile-sample-use= -fsample-profile-use-profi \
    -gline-tables-only -fdebug-info-for-profiling \
    -funique-internal-linkage-names -fpseudo-probe-for-profiling \
    -ffunction-sections -fdata-sections; do
    [[ $(field CFLAGS "${FLAGS_FILE}") != *"${forbidden}"* ]] || \
        fail "ordinary consumer probe contains stage-only flag ${forbidden}"
    [[ $(field CXXFLAGS "${FLAGS_FILE}") != *"${forbidden}"* ]] || \
        fail "ordinary C++ consumer probe contains stage-only flag ${forbidden}"
done
[[ $(field LDFLAGS "${FLAGS_FILE}") != *'--emit-relocs'* && \
    $(field LDFLAGS "${FLAGS_FILE}") != *'--build-id'* ]] || \
    fail 'ordinary consumer probe contains stage-only linker metadata'
[[ $(field RUSTFLAGS "${FLAGS_FILE}") != *'debuginfo'* && \
    $(field RUSTFLAGS "${FLAGS_FILE}") != *'--emit-relocs'* && \
    $(field RUSTFLAGS "${FLAGS_FILE}") != *'--build-id'* ]] || \
    fail 'ordinary consumer probe contains stage-only Rust metadata'
build_fingerprint_input "${WORK}/use-probe-effective-flags.tsv" \
    generated-sample-use.conf sample-use "${WORK}/use-fingerprint-input.json" \
    "${WORK}/use-expected-axes.json"
USE_FINGERPRINT=$("${PROFILE_IDENTITY}" fingerprint \
    --input "${WORK}/use-fingerprint-input.json" \
    --metadata-out "${WORK}/use-fingerprint-metadata.json")
[[ ${USE_FINGERPRINT} =~ ^[0-9a-f]{64}$ ]] || \
    fail 'invalid canonical consumer fingerprint'
[[ ${USE_FINGERPRINT} != "${MAP_FINGERPRINT}" ]] || \
    fail 'mapping and sample-use fingerprints unexpectedly collide'
run_ebuild "${WORK}/use-probe-final-clean.log" clean

rm -f -- "${PERF_DATA}" "${PERF_DATA}.partial"
if ! timeout --signal=TERM --kill-after=10 300 \
    "${PERF}" record -q --no-buildid-cache -e cycles:u -j any,u \
    -o "${PERF_DATA}.partial" -- "${MAPPED_BINARY}" "${ITERATIONS}" \
    > "${WORK}/training.stdout" 2> "${WORK}/perf-record.stderr"; then
    rm -f -- "${PERF_DATA}.partial"
    fail 'representative perf collection failed or timed out'
fi
[[ -s ${PERF_DATA}.partial ]] || fail 'perf produced no nonempty transaction output'
mv -- "${PERF_DATA}.partial" "${PERF_DATA}"
chown "0:${PORTAGE_GID}" -- "${PERF_DATA}"
chmod 0440 -- "${PERF_DATA}"
[[ $(stat -c '%u:%g:%a:%h' -- "${PERF_DATA}") == \
    "0:${PORTAGE_GID}:440:1" ]] || fail 'perf input is not immutable and Portage-readable'
runuser -u portage -- test -r "${PERF_DATA}" || fail 'Portage cannot read perf input'
"${PERF}" evlist -v -i "${PERF_DATA}" > "${WORK}/perf-evlist.log"
grep -Eq 'sample_type:.*BRANCH_STACK' "${WORK}/perf-evlist.log" || \
    fail 'perf profile lacks branch-stack samples'
grep -Eq 'branch_sample_type:.*USER.*ANY' "${WORK}/perf-evlist.log" || \
    fail 'perf profile lacks the requested user/any branch filter'

PRODUCTION_HOST=$(hostname)
PRODUCTION_DATE=$(TZ=UTC date -u +%F)
[[ ${PRODUCTION_HOST} =~ ^[A-Za-z0-9+_.@-]+$ ]] || fail 'unsafe hostname identity'
"${PROFILE_IDENTITY}" sample-convert \
    --llvm-profgen "${PROFGEN}" \
    --llvm-profdata "${PROFDATA}" \
    --readelf "${LLVM_READELF}" \
    --objcopy "${LLVM_OBJCOPY}" \
    --binary "${MAPPED_BINARY}" \
    --perf-data "${PERF_DATA}" \
    --profile-out "${PROFILE}" \
    --metadata-out "${SAMPLE_METADATA}" \
    --conversion-log-out "${CONVERSION_LOG}" \
    --cpv app-test/phase2-pgo-use-fixture-1 \
    --fingerprint "${MAP_FINGERPRINT}" \
    --abi amd64 \
    --clang-major 22 \
    --optimization-generation-id "${GENERATION_ID}" \
    --inventory-id "${INVENTORY_ID}" \
    --inventory-sha256 "${INVENTORY_SHA256}" \
    --workload-revision phase2-sample-portage-workload-v1 \
    --source-identity-sha256 "${EBUILD_SHA}" \
    --production-host "${PRODUCTION_HOST}" \
    --production-date "${PRODUCTION_DATE}" \
    --test-mode \
    --test-framework-lock "${FRAMEWORK_LOCK}" \
    --test-project-lock "${PROJECT_LOCK}" \
    --test-generation-lock "${GENERATION_LOCK}" \
    > "${WORK}/sample-convert.stdout" 2> "${WORK}/sample-convert.stderr"
[[ -s ${PROFILE} && -s ${SAMPLE_METADATA} && -s ${CONVERSION_LOG} ]] || \
    fail 'sample conversion did not publish its exact three-file transaction'
for artifact in "${PROFILE}" "${SAMPLE_METADATA}"; do
    [[ $(stat -c '%u:%g:%a:%h' -- "${artifact}") == \
        "0:${PORTAGE_GID}:640:1" ]] || \
        fail "sample artifact is not immutable, single-link, root:portage 0640: ${artifact}"
done
[[ $(stat -c '%u:%g:%a:%h' -- "${CONVERSION_LOG}") == \
    "0:${PORTAGE_GID}:440:1" ]] || \
    fail 'conversion log is not immutable, single-link, root:portage 0440 evidence'
"${PROFDATA}" show --sample --all-functions --counts "${PROFILE}" \
    > "${WORK}/sample-profile.show"
grep -Fq 'Function:' "${WORK}/sample-profile.show" || \
    fail 'sample profile contains no recorded function'

CLANG_SHA=$(sha256sum -- "${CLANG}"); CLANG_SHA=${CLANG_SHA%% *}
PROFDATA_SHA=$(sha256sum -- "${PROFDATA}"); PROFDATA_SHA=${PROFDATA_SHA%% *}
"${VALIDATOR}" produce \
    --backend clang-sample \
    --profile "${PROFILE}" \
    --fingerprint "${USE_FINGERPRINT}" \
    --sample-input-fingerprint "${MAP_FINGERPRINT}" \
    --abi amd64 \
    --compiler-family clang \
    --compiler "${CLANG}" \
    --compiler-sha256 "${CLANG_SHA}" \
    --compiler-major 22 \
    --profile-tool "${PROFDATA}" \
    --profile-tool-sha256 "${PROFDATA_SHA}" \
    --profile-tool-major 22 \
    --sample-metadata "${SAMPLE_METADATA}" \
    --manifest-out "${MANIFEST}" \
    --metadata-out "${SIDECAR}" \
    --generation-id "${GENERATION_ID}" \
    --inventory-id "${INVENTORY_ID}" \
    --inventory-sha256 "${INVENTORY_SHA256}" \
    --test-mode \
    --test-framework-lock "${FRAMEWORK_LOCK}" \
    --test-project-lock "${PROJECT_LOCK}" \
    --test-generation-lock "${GENERATION_LOCK}" \
    > "${WORK}/profile-produce.stdout" 2> "${WORK}/profile-produce.stderr"
"${VALIDATOR_PROXY}" verify --manifest "${MANIFEST}" --metadata "${SIDECAR}" \
    > "${WORK}/profile-verify.stdout" 2> "${WORK}/profile-verify.stderr"

for artifact in "${MANIFEST}" "${SIDECAR}"; do
    [[ $(stat -c '%u:%g:%a:%h' -- "${artifact}") == \
        "0:${PORTAGE_GID}:640:1" ]] || \
        fail "validated profile artifact is not root:portage 0640: ${artifact}"
done
for artifact in "${MAPPED_BINARY}" "${PERF_DATA}" "${PROFILE}" \
    "${SAMPLE_METADATA}" "${CONVERSION_LOG}" "${MANIFEST}" "${SIDECAR}"; do
    runuser -u portage -- test -r "${artifact}" || \
        fail "Portage cannot read an exact sample-use input: ${artifact}"
done
python3 - "${SIDECAR}" "${USE_FINGERPRINT}" "${MAP_FINGERPRINT}" \
    "${GENERATION_ID}" "${INVENTORY_ID}" "${INVENTORY_SHA256}" \
    "${EBUILD_SHA}" "${PRODUCTION_HOST}" "${PRODUCTION_DATE}" <<'PY'
import json
import pathlib
import sys

(
    path,
    consumer,
    mapping,
    generation,
    inventory,
    inventory_sha,
    source_identity,
    production_host,
    production_date,
) = sys.argv[1:]
data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
if data["profile"]["fingerprint"] != consumer:
    raise SystemExit("validator sidecar lost the consumer-build fingerprint")
if data["backend_proof"]["sample_input_fingerprint"] != mapping:
    raise SystemExit("validator sidecar lost the mapping-input fingerprint")
expected_generation = {
    "generation_id": generation,
    "inventory_id": inventory,
    "inventory_sha256": inventory_sha,
}
if data["generation"] != expected_generation:
    raise SystemExit("validator sidecar lost the complete generation identity")
if data["backend_proof"]["reproducibility"] != {
    "optimization_generation_id": generation,
    "inventory_id": inventory,
    "inventory_sha256": inventory_sha,
    "workload_revision": "phase2-sample-portage-workload-v1",
    "source_identity_sha256": source_identity,
    "production_host": production_host,
    "production_date": production_date,
}:
    raise SystemExit("validator sidecar reproducibility identity is inconsistent")
PY

write_use_environment "${USE_FINGERPRINT}"
cp -- "${GENERATED_ASSIGNMENT}" "${WORK}/generated-package-env-use"
(
    cd -- "${WORK}"
    sha256sum -- generated-package-env-map generated-package-env-use \
        config-root/etc/portage/env/generated-sample-map.conf \
        config-root/etc/portage/env/generated-sample-use.conf
) > "${WORK}/generated-policy.sha256"
run_ebuild "${WORK}/use-clean.log" clean
run_ebuild "${WORK}/sample-use-build.log" install
[[ -s ${FLAGS_FILE} ]] || fail 'sample-use build emitted no flag receipt'
cp -- "${FLAGS_FILE}" "${WORK}/sample-use-effective-flags.tsv"
USE_CFLAGS=$(field CFLAGS "${FLAGS_FILE}")
[[ $(field mode "${FLAGS_FILE}") == clang-sample-use ]] || \
    fail 'generated package.env assignment did not activate sample use'
[[ $(field active_fingerprint "${FLAGS_FILE}") == "${USE_FINGERPRINT}" ]] || \
    fail 'sample-use build consumed a different package fingerprint'
[[ $(field active_compiler "${FLAGS_FILE}") == "${CLANG}" ]] || \
    fail 'sample-use build selected a different compiler from its fingerprint'
[[ $(field phase_euid "${FLAGS_FILE}") == "${PORTAGE_UID}" && \
    $(field phase_egid "${FLAGS_FILE}") == "${PORTAGE_GID}" ]] || \
    fail 'sample-use compilation did not run through the live userpriv identity'
assert_receipt_axes "${WORK}/sample-use-effective-flags.tsv" \
    "${WORK}/use-expected-axes.json" 'authoritative sample-use build'
[[ " ${USE_CFLAGS} " == *" -fprofile-sample-use=${PROFILE} "* ]] || \
    fail 'sample-use compiler flags lack the exact sample profile'
[[ " ${USE_CFLAGS} " == *' -fsample-profile-use-profi '* ]] || \
    fail 'sample-use compiler flags lack profi consumption'
[[ ${USE_CFLAGS} != *'-fprofile-use='* ]] || \
    fail 'sample profile leaked through the IR instrumentation consumer flag'
grep -Fq 'mode=clang-sample-use' "${WORK}/sample-use-build.log" || \
    fail 'Portage build log lacks the selected sample-use mode'
grep -Fq "profile=${PROFILE}" "${WORK}/sample-use-build.log" || \
    fail 'Portage build log lacks the exact validated profile path'
awk -F '\t' -v uid="${PORTAGE_UID}" -v gid="${PORTAGE_GID}" \
    '$1 == "verify" && $2 == uid && $3 == gid {found = 1} END {exit !found}' \
    "${VALIDATOR_IDENTITY}" || \
    fail 'authoritative profile validation did not execute under userpriv'

STAGED=${BUILD_ROOT}/image/usr/bin/phase2-pgo-use-fixture
[[ -x ${STAGED} ]] || fail 'sample-use Portage install staged no executable'
"${STAGED}" "${ITERATIONS}" > "${WORK}/sample-use.stdout"
cmp -- "${WORK}/training.stdout" "${WORK}/sample-use.stdout" || \
    fail 'sample-use executable changed functional output'

cp --preserve=mode,ownership,xattr -- "${SIDECAR}" "${WORK}/sidecar.saved"
printf '\n' >> "${SIDECAR}"
run_ebuild "${WORK}/tamper-clean.log" clean
if run_ebuild "${WORK}/tampered-sidecar.log" compile; then
    fail 'real Portage sample-use build accepted a tampered validator sidecar'
fi
grep -Fq 'authoritative profile manifest/sidecar verification failed' \
    "${WORK}/tampered-sidecar.log" || \
    fail 'tampered sidecar rejection lacked the fail-closed diagnostic'
mv -- "${WORK}/sidecar.saved" "${SIDECAR}"
[[ $(stat -c '%u:%g:%a:%h' -- "${SIDECAR}") == \
    "0:${PORTAGE_GID}:640:1" ]] || fail 'restored sidecar lost trusted metadata'
"${VALIDATOR_PROXY}" verify --manifest "${MANIFEST}" --metadata "${SIDECAR}" \
    > "${WORK}/profile-verify-restored.stdout" \
    2> "${WORK}/profile-verify-restored.stderr"
run_ebuild "${WORK}/final-clean.log" clean

printf '%s\t%s\n' \
    'authoritative_work_root' "${WORK}" \
    'published_copy' "${CANONICAL_OUTPUT_DIR:-none}" \
    'published_copy_semantics' \
    'historical-byte-evidence; validator sidecars remain bound to authoritative_work_root' \
    'authoritative_work_final_identity' 'root:portage:0750' \
    > "${WORK}/publication-context.tsv"
(
    cd -- "${WORK}"
    sha256sum -- \
        app-test/phase2-pgo-use-fixture/phase2-pgo-use-fixture-1.ebuild \
        perf.data profile/sample.prof profile/sample-metadata.json \
        profile/llvm-profgen-conversion-log.json profile/profile.manifest \
        profile/profile.manifest.metadata.json profile/mapping-input \
        map-fingerprint-input.json map-fingerprint-metadata.json \
        use-fingerprint-input.json use-fingerprint-metadata.json \
        validator-identity.tsv map-effective-flags.tsv \
        sample-use-effective-flags.tsv publication-context.tsv
) > "${WORK}/evidence.sha256"
printf 'PASS: real Portage exact mapping fingerprint, perf training, sample conversion, distinct consumer fingerprint, immutable validation, generated package.env use, runtime proof, and tamper rejection\n'

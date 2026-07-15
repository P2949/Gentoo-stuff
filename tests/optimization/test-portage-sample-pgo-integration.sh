#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
TEMPLATE=${ROOT}/optimization/fixtures/portage/phase2-sample-pgo-fixture-1.ebuild.in
AUTHORIZATION_TOKEN_SCANNER=${ROOT}/tests/optimization/authorization_token_scan.py
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
PORTAGE_SAMPLE_ITERATIONS_WAS_SET=${PORTAGE_SAMPLE_PGO_ITERATIONS+x}
KEEP_TEMP_WAS_SET=${KEEP_TEMP+x}
ITERATIONS=${PORTAGE_SAMPLE_PGO_ITERATIONS:-100000000}
KEEP_TEMP=${KEEP_TEMP:-0}
OUTPUT_DIR=
CANONICAL_OUTPUT_DIR=
EXPLICIT_OUTPUT_DIR=0
TRUSTED_OUTPUT_BASE=/var/tmp/gentoo-optimization
PRODUCTION_LOCKS=0
PRODUCTION_ROOTS_CREATED=0
PRODUCTION_GATE_COMPLETE=0
PRODUCTION_STATUS_FINALIZED=0
PRODUCTION_CAPTURED=0
PRODUCTION_TRANSACTION_TOKEN=${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN-}
PRODUCTION_TRANSACTION_AUTHORIZATION=${GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION-}
PRODUCTION_GATE_RUN_ID=${GENTOO_OPT_PRODUCTION_GATE_RUN_ID-}
PRODUCTION_GATE_GENERATION_ID=${GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID-}
PRODUCTION_GATE_INVENTORY_ID=${GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID-}
PRODUCTION_GATE_INVENTORY_SHA256=${GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256-}
PRODUCTION_GATE_WORK_ROOT=${GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT-}
export -n PRODUCTION_TRANSACTION_TOKEN PRODUCTION_TRANSACTION_AUTHORIZATION \
    PRODUCTION_GATE_RUN_ID PRODUCTION_GATE_GENERATION_ID \
    PRODUCTION_GATE_INVENTORY_ID PRODUCTION_GATE_INVENTORY_SHA256 \
    PRODUCTION_GATE_WORK_ROOT
# Nothing launched while parsing or validating the gate may inherit the raw
# bearer or its coordinator-supplied identities.  Retain shell-only copies and
# rebuild the exact environment only for the two reviewed authorization entry
# points below.
unset GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN \
    GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION \
    GENTOO_OPT_PRODUCTION_GATE_RUN_ID \
    GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID \
    GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID \
    GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256 \
    GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT

usage() {
    cat <<'EOF'
Usage: test-portage-sample-pgo-integration.sh [--output-dir ABSOLUTE_PATH]
       [--keep-temp] [--production-locks]

The optional output directory must be a new path below the trusted
/var/tmp/gentoo-optimization tree. Explicit output directories are always preserved.
--production-locks is a root-only Phase 2 gate driven by the reviewed production
lock coordinator. It uses canonical live lock paths and root-owned namespaces;
it never passes a test-mode or substituted-lock argument to an installed helper.
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
        --production-locks)
            PRODUCTION_LOCKS=1
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

production_authorized_command() {
    ((PRODUCTION_LOCKS)) || fail 'internal production command used outside production mode'
    [[ ${PRODUCTION_TRANSACTION_TOKEN} =~ ^[0-9a-f]{64}$ &&
        -n ${TRANSACTION_AUTHORIZATION-} ]] ||
        fail 'internal production command lacks its exact coordinator authorization'
    /usr/bin/env -i \
        HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        LANG=C LC_ALL=C TZ=UTC \
        "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN=${PRODUCTION_TRANSACTION_TOKEN}" \
        "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION=${TRANSACTION_AUTHORIZATION}" \
        "$@"
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
[[ ${ITERATIONS} =~ ^[1-9][0-9]*$ ]] || fail 'iteration count must be positive'
[[ ${KEEP_TEMP} == 0 || ${KEEP_TEMP} == 1 ]] || fail 'KEEP_TEMP must be 0 or 1'
[[ ${PRODUCTION_LOCKS} == 0 || ${PRODUCTION_LOCKS} == 1 ]] || \
    fail 'PRODUCTION_LOCKS must be 0 or 1'
if ((PRODUCTION_LOCKS)); then
    AUTHORIZATION_TOKEN_SCANNER=/usr/local/libexec/gentoo-optimization/pgo/authorization-token-scan.py
    [[ -n ${OUTPUT_DIR} ]] || fail '--production-locks requires an explicit output directory'
    [[ -z ${PORTAGE_SAMPLE_ITERATIONS_WAS_SET}${KEEP_TEMP_WAS_SET} ]] || \
        fail '--production-locks forbids inherited workload or retention overrides'
    while IFS= read -r variable; do
        case ${variable} in
            GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN|\
            GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION|\
            GENTOO_OPT_PRODUCTION_GATE_RUN_ID|\
            GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID|\
            GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID|\
            GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256|\
            GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT) ;;
            GENTOO_OPT_*)
                fail "--production-locks inherited a forbidden optimization override: ${variable}"
                ;;
        esac
    done < <(compgen -e | LC_ALL=C sort)
    for variable in PRODUCTION_GATE_RUN_ID PRODUCTION_GATE_GENERATION_ID \
        PRODUCTION_GATE_INVENTORY_ID PRODUCTION_GATE_INVENTORY_SHA256 \
        PRODUCTION_GATE_WORK_ROOT; do
        [[ -n ${!variable} ]] || fail "--production-locks requires ${variable}"
    done
    for variable in PRODUCTION_GATE_RUN_ID PRODUCTION_GATE_GENERATION_ID \
        PRODUCTION_GATE_INVENTORY_ID; do
        value=${!variable}
        [[ ${value} =~ ^[A-Za-z0-9+_.@-]+$ && ${value} != . && \
            ${value} != .. && ${#value} -le 128 ]] || \
            fail "${variable} is not a safe common identity component"
    done
    [[ ${PRODUCTION_GATE_INVENTORY_SHA256} =~ ^[0-9a-f]{64}$ ]] || \
        fail 'production gate inventory SHA-256 is malformed'
    [[ ${PRODUCTION_TRANSACTION_TOKEN} =~ ^[0-9a-f]{64}$ ]] || \
        fail '--production-locks must be supervised by the production lock coordinator'
    [[ ${PRODUCTION_TRANSACTION_AUTHORIZATION} == /* && \
        ${PRODUCTION_TRANSACTION_AUTHORIZATION} != / ]] || \
        fail '--production-locks requires the coordinator authorization path'
fi
((EUID == 0)) || {
    printf 'SKIP: real Portage sample-PGO integration requires root\n'
    exit 77
}
for command in awk b2sum bash chmod chown cp cmp cut date ebuild env find getent grep \
    hostname id install jq ln mkdir mktemp mv perf portageq python3 readelf readlink rm runuser sed \
    realpath sha256sum sha512sum sort stat sync tail timeout xargs; do
    command -v "${command}" >/dev/null 2>&1 || fail "missing command: ${command}"
done
if ((PRODUCTION_LOCKS)); then
    for command in mount unshare; do
        command -v "${command}" >/dev/null 2>&1 || \
            fail "missing production tamper-isolation command: ${command}"
    done
    unshare --mount --propagation private -- true >/dev/null 2>&1 || \
        fail 'production tamper isolation cannot create a private mount namespace'
fi
[[ -f ${TEMPLATE} && -x ${INSTALLER} ]] || fail 'fixture template or installer is absent'
[[ -f ${AUTHORIZATION_TOKEN_SCANNER} && ! -L ${AUTHORIZATION_TOKEN_SCANNER} ]] || \
    fail 'authorization-token persistence scanner is absent or symlinked'
for tool in "${PROFILE_IDENTITY}" "${VALIDATOR}" "${CLANG_LINK}" \
    "${CLANGXX_LINK}" "${PROFGEN_LINK}" "${PROFDATA_LINK}" \
    "${READELF_LINK}" "${OBJCOPY_LINK}" "${PERF}"; do
    [[ -x ${tool} ]] || fail "required exact tool is absent: ${tool}"
done
if ((PRODUCTION_LOCKS == 0)); then
    "${INSTALLER}" --source-root "${ROOT}" --check >/dev/null || \
        fail 'installed framework differs from the reviewed repository source'
fi
FRAMEWORK_CURRENT=/var/lib/gentoo-optimization/framework-current
[[ -L ${FRAMEWORK_CURRENT} && $(stat -c '%u:%g' -- "${FRAMEWORK_CURRENT}") == 0:0 ]] || \
    fail 'active framework selector is not the trusted root-owned symlink'
FRAMEWORK_TARGET=$(readlink -- "${FRAMEWORK_CURRENT}")
FRAMEWORK_ID=${FRAMEWORK_TARGET#/var/lib/gentoo-optimization/framework-}
[[ ${FRAMEWORK_TARGET} == "/var/lib/gentoo-optimization/framework-${FRAMEWORK_ID}" && \
    ${FRAMEWORK_ID} =~ ^[0-9a-f]{64}$ && -d ${FRAMEWORK_TARGET} && \
    ! -L ${FRAMEWORK_TARGET} ]] || fail 'active framework target is unmanaged or unavailable'
export GENTOO_OPT_FRAMEWORK_TARGET=${FRAMEWORK_TARGET}

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
if ((PRODUCTION_LOCKS)); then
    EXPECTED_PRODUCTION_WORK_ROOT=${TRUSTED_OUTPUT_BASE}/phase2-sample-work-${PRODUCTION_GATE_RUN_ID}
    [[ ${PRODUCTION_GATE_WORK_ROOT} == "${EXPECTED_PRODUCTION_WORK_ROOT}" && \
        ! -e ${PRODUCTION_GATE_WORK_ROOT} && ! -L ${PRODUCTION_GATE_WORK_ROOT} ]] || \
        fail 'production work root differs from its exact absent coordinator contract'
    install -d -o 0 -g 0 -m 0700 -- "${PRODUCTION_GATE_WORK_ROOT}"
    sync -f -- "${PRODUCTION_GATE_WORK_ROOT}" "${PRODUCTION_GATE_WORK_ROOT%/*}"
    WORK=${PRODUCTION_GATE_WORK_ROOT}
else
    WORK=$(mktemp -d /var/tmp/gentoo-phase2-pgo-portage.sample.XXXXXX)
fi
printf 'gentoo-optimization-portage-sample-fixture-v1\n' > \
    "${WORK}/.optimization-fixture-root"
if ((PRODUCTION_LOCKS)); then
    python3 - "${WORK}/portage-process-preflight.tsv" <<'PY'
import hashlib
import os
import pathlib
import sys

output = pathlib.Path(sys.argv[1])
blocked = {
    "ebuild", "eclean", "emaint", "emerge", "portageq", "quickpkg",
}
rows = []
for process in pathlib.Path("/proc").iterdir():
    if not process.name.isdigit() or int(process.name) == os.getpid():
        continue
    try:
        arguments = [
            item.decode("utf-8", "replace")
            for item in (process / "cmdline").read_bytes().split(b"\0")
            if item
        ]
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    matches = sorted(
        {
            pathlib.PurePath(argument).name
            for argument in arguments
            if pathlib.PurePath(argument).name in blocked
        }
    )
    if matches:
        rows.append((int(process.name), ",".join(matches), " ".join(arguments)))
with output.open("w", encoding="utf-8") as stream:
    stream.write("pid\tmatched_tools\tcommand\n")
    for pid, matches, command in sorted(rows):
        stream.write(f"{pid}\t{matches}\t{command}\n")
if rows:
    raise SystemExit("an existing Portage process makes the production gate unsafe")
PY
    PROBE_SOURCE=${WORK}/mount-probe.source
    PROBE_TARGET=${WORK}/mount-probe.target
    printf 'private-source\n' > "${PROBE_SOURCE}"
    printf 'host-target\n' > "${PROBE_TARGET}"
    # shellcheck disable=SC2016  # Positional parameters expand in the child shell.
    unshare --mount --propagation private -- bash -Eeuo pipefail -c '
        mount --bind "$1" "$2"
        cmp -- "$1" "$2"
    ' bash "${PROBE_SOURCE}" "${PROBE_TARGET}" || \
        fail 'production tamper isolation cannot bind a private regular-file substitute'
    grep -Fxq host-target "${PROBE_TARGET}" || \
        fail 'private bind-mount preflight escaped into the host mount namespace'
    rm -f -- "${PROBE_SOURCE}" "${PROBE_TARGET}"
fi

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

require_trusted_production_directory_chain() {
    local current=$1 owner mode
    [[ ${current} == /* && -d ${current} && ! -L ${current} && \
        $(realpath -e -- "${current}") == "${current}" ]] || return 1
    while :; do
        [[ -d ${current} && ! -L ${current} ]] || return 1
        owner=$(stat -c %u -- "${current}") || return 1
        mode=$(stat -c %a -- "${current}") || return 1
        [[ ${owner} == 0 && ${mode} =~ ^[0-7]{3,4}$ ]] || return 1
        (( (8#${mode} & 8#022) == 0 )) || return 1
        [[ ${current} == / ]] && break
        current=${current%/*}
        [[ -n ${current} ]] || current=/
    done
}

write_production_gate_status() {
    local status=$1 exit_status=$2 root destination partial
    ((PRODUCTION_LOCKS && PRODUCTION_ROOTS_CREATED)) || return 0
    [[ ${status} == in-progress || ${status} == failed || ${status} == passed ]] || \
        return 1
    for root in "${PRODUCTION_STATE_ROOT}" "${PROFILE_ROOT}"; do
        [[ -d ${root} && ! -L ${root} ]] || return 1
        destination=${root}/gate-status.tsv
        partial=${destination}.partial.$$
        rm -f -- "${partial}"
        {
            printf 'schema\tgentoo-optimization-phase2-sample-gate-v1\n'
            printf 'status\t%s\n' "${status}"
            printf 'exit_status\t%s\n' "${exit_status}"
            printf 'run_id\t%s\n' "${PRODUCTION_GATE_RUN_ID}"
            printf 'generation_id\t%s\n' "${GENERATION_ID}"
            printf 'inventory_id\t%s\n' "${INVENTORY_ID}"
            printf 'inventory_sha256\t%s\n' "${INVENTORY_SHA256}"
            printf 'framework_target\t%s\n' "${FRAMEWORK_TARGET}"
            printf 'work_root\t%s\n' "${PRODUCTION_GATE_WORK_ROOT}"
            printf 'profile_root\t%s\n' "${PROFILE_ROOT}"
            printf 'generation_state_root\t%s\n' "${PRODUCTION_STATE_ROOT}"
            printf 'recorded_at_utc\t%s\n' "$(TZ=UTC date -u +%FT%TZ)"
        } > "${partial}" || return 1
        chown "0:${PORTAGE_GID}" -- "${partial}" || return 1
        chmod 0440 -- "${partial}" || return 1
        sync -f -- "${partial}" || return 1
        mv -fT -- "${partial}" "${destination}" || return 1
        sync -f -- "${root}" || return 1
    done
}

emit_production_live_root_index() {
    local status=$1 root entry kind digest metadata
    printf 'gate_status\t%s\n' "${status}"
    printf 'profile_root\t%s\n' "${PROFILE_ROOT}"
    printf 'generation_state_root\t%s\n' "${PRODUCTION_STATE_ROOT}"
    for root in "${PROFILE_ROOT}" "${PRODUCTION_STATE_ROOT}"; do
        if [[ ! -e ${root} && ! -L ${root} ]]; then
            printf 'missing\t%s\n' "${root}"
            continue
        fi
        [[ -d ${root} && ! -L ${root} ]] || return 1
        while IFS= read -r -d '' entry; do
            if [[ -d ${entry} && ! -L ${entry} ]]; then
                kind=directory
                digest=-
            elif [[ -f ${entry} && ! -L ${entry} ]]; then
                kind=regular
                digest=$(sha256sum -- "${entry}") || return 1
                digest=${digest%% *}
            else
                printf 'unsupported\t%s\n' "${entry}"
                return 1
            fi
            metadata=$(stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${entry}") || \
                return 1
            printf 'artifact\t%s\t%s\t%s\t%s\n' \
                "${kind}" "${entry}" "${digest}" "${metadata}"
        done < <(find "${root}" -xdev -print0 | LC_ALL=C sort -z)
    done
}

capture_production_live_roots() {
    local status=$1 index_partial=${WORK}/production-live-roots.tsv.partial
    local root
    local -a roots=()
    ((PRODUCTION_LOCKS && PRODUCTION_ROOTS_CREATED)) || return 0
    for root in "${PROFILE_ROOT}" "${PRODUCTION_STATE_ROOT}"; do
        if [[ -d ${root} && ! -L ${root} ]]; then
            roots+=("${root}")
        elif [[ -e ${root} || -L ${root} ]]; then
            return 1
        fi
    done
    if ((${#roots[@]})); then
        find "${roots[@]}" -type f -exec sync -f -- {} + || return 1
        find "${roots[@]}" -depth -type d -exec sync -f -- {} + || return 1
    fi
    for root in "${PROFILE_ROOT%/*}" "${PRODUCTION_STATE_ROOT%/*}"; do
        if [[ -e ${root} || -L ${root} ]]; then
            require_trusted_production_directory_chain "${root}" || return 1
            sync -f -- "${root}" || return 1
        fi
    done
    rm -rf -- "${WORK}/profile" "${WORK}/production-state"
    rm -f -- "${WORK}/perf.data" "${WORK}/production-live-roots.tsv" \
        "${index_partial}"
    emit_production_live_root_index "${status}" > "${index_partial}" || {
        rm -f -- "${index_partial}"
        return 1
    }
    mv -T -- "${index_partial}" "${WORK}/production-live-roots.tsv" || return 1
    if [[ -d ${PROFILE_ROOT} && ! -L ${PROFILE_ROOT} ]]; then
        cp -a -- "${PROFILE_ROOT}" "${WORK}/profile" || return 1
    fi
    if [[ -d ${PRODUCTION_STATE_ROOT} && ! -L ${PRODUCTION_STATE_ROOT} ]]; then
        cp -a -- "${PRODUCTION_STATE_ROOT}" "${WORK}/production-state" || return 1
    fi
    if [[ -f ${PERF_DATA} && ! -L ${PERF_DATA} ]]; then
        cp -a -- "${PERF_DATA}" "${WORK}/perf.data" || return 1
    fi
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
    if ((PRODUCTION_LOCKS && PRODUCTION_ROOTS_CREATED)); then
        if ((status == 0 && PRODUCTION_GATE_COMPLETE && \
            PRODUCTION_STATUS_FINALIZED && PRODUCTION_CAPTURED)); then
            :
        else
            ((status != 0)) || status=1
            if ! write_production_gate_status failed "${status}"; then
                printf 'FAIL: could not durably mark live production sample-gate roots failed\n' >&2
            fi
            if ! capture_production_live_roots failed; then
                printf 'FAIL: could not index the failed live production sample-gate roots\n' >&2
            fi
        fi
    fi
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
PRODUCTION_STATE_ROOT=
SEED_FINGERPRINT_FILE=
MAP_FINGERPRINT_FILE=
USE_FINGERPRINT_FILE=
TRANSACTION_AUTHORIZATION=
TRANSACTION_JOURNAL=
TRANSACTION_CHILD_IDENTITY=
TRANSACTION_JOURNAL_SHA256=
TRANSACTION_CHILD_IDENTITY_SHA256=
TRANSACTION_EXPECTED_PAYLOAD_SHA256=
TRANSACTION_FRAMEWORK_AGGREGATE_SHA256=
PROFILE_LOCK_ARGS=()
VALIDATOR_COMMAND=${VALIDATOR_PROXY}
if ((PRODUCTION_LOCKS)); then
    GENERATION_ID=${PRODUCTION_GATE_GENERATION_ID}
    INVENTORY_ID=${PRODUCTION_GATE_INVENTORY_ID}
    INVENTORY_SHA256=${PRODUCTION_GATE_INVENTORY_SHA256}
    PRODUCTION_STATE_ROOT=/var/lib/gentoo-optimization/generations/${GENERATION_ID}/phase2-sample-gate-${PRODUCTION_GATE_RUN_ID}
    PROFILE_ROOT=/var/cache/gentoo-optimization/pgo/clang-sample/phase2-sample-gate-${PRODUCTION_GATE_RUN_ID}
    PERF_DATA=${PROFILE_ROOT}/perf.data
    PROFILE=${PROFILE_ROOT}/sample.prof
    SAMPLE_METADATA=${PROFILE_ROOT}/sample-metadata.json
    CONVERSION_LOG=${PROFILE_ROOT}/llvm-profgen-conversion-log.json
    MANIFEST=${PROFILE_ROOT}/profile.manifest
    SIDECAR=${MANIFEST}.metadata.json
    SEED_FINGERPRINT_FILE=${PRODUCTION_STATE_ROOT}/seed.fingerprint
    MAP_FINGERPRINT_FILE=${PRODUCTION_STATE_ROOT}/mapping.fingerprint
    USE_FINGERPRINT_FILE=${PRODUCTION_STATE_ROOT}/consumer.fingerprint
    TRANSACTION_AUTHORIZATION=${PRODUCTION_STATE_ROOT}/transaction.authorization
    TRANSACTION_JOURNAL=/var/lib/gentoo-optimization/state/profile-transactions/phase-2-production-profile-locks.pending
    TRANSACTION_CHILD_IDENTITY=${TRANSACTION_JOURNAL}.child.json
    VALIDATOR_COMMAND=${VALIDATOR}
fi
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
    "${WORK}/binpkgs"
chmod 0755 -- "${WORK}" "${WORK}/app-test" "${PACKAGE_ROOT}" \
    "${WORK}/metadata" "${WORK}/profiles" "${CONFIG_ROOT}" \
    "${CONFIG_ROOT}/etc" "${PORTAGE_ROOT}" "${PORTAGE_ROOT}/env" \
    "${PORTAGE_ROOT}/package.env" "${PORTAGE_ROOT}/repos.conf" \
    "${PORTAGE_TMP}" "${WORK}/distfiles" "${WORK}/binpkgs"
if ((PRODUCTION_LOCKS)); then
    [[ ${PRODUCTION_TRANSACTION_AUTHORIZATION} == "${TRANSACTION_AUTHORIZATION}" ]] || \
        fail 'coordinator authorization path differs from the exact gate namespace'
    [[ -d ${PRODUCTION_STATE_ROOT} && ! -L ${PRODUCTION_STATE_ROOT} && \
        -f ${TRANSACTION_AUTHORIZATION} && ! -L ${TRANSACTION_AUTHORIZATION} && \
        ! -e ${TRANSACTION_AUTHORIZATION}.partial && \
        ! -L ${TRANSACTION_AUTHORIZATION}.partial && \
        ! -e ${PROFILE_ROOT} && ! -L ${PROFILE_ROOT} ]] || \
        fail 'coordinator did not publish one exact production gate state root'
    [[ $(find "${PRODUCTION_STATE_ROOT}" -mindepth 1 -maxdepth 1 -printf '%f\n') == \
        transaction.authorization ]] || \
        fail 'coordinator production gate state root contains unexpected entries'
    require_trusted_production_directory_chain \
        /var/lib/gentoo-optimization/generations || \
        fail 'production generation-state parent is not a trusted root-owned chain'
    if [[ -e ${PRODUCTION_STATE_ROOT%/*} || -L ${PRODUCTION_STATE_ROOT%/*} ]]; then
        require_trusted_production_directory_chain "${PRODUCTION_STATE_ROOT%/*}" || \
            fail 'existing production generation parent is not a trusted root-owned chain'
    fi
    require_trusted_production_directory_chain "${PROFILE_ROOT%/*}" || \
        fail 'production sample-profile parent is not a trusted root-owned chain'
    [[ $(realpath -e -- "${PRODUCTION_STATE_ROOT}") == "${PRODUCTION_STATE_ROOT}" && \
        $(stat -c '%u:%g:%a' -- "${PRODUCTION_STATE_ROOT}") == 0:0:755 ]] || \
        fail 'coordinator production gate state root has an unsafe identity'
    PRODUCTION_ROOTS_CREATED=1
    install -d -o 0 -g "${PORTAGE_GID}" -m 0750 -- "${PROFILE_ROOT}"
    [[ $(realpath -e -- "${PRODUCTION_STATE_ROOT}") == "${PRODUCTION_STATE_ROOT}" && \
        $(stat -c '%u:%g:%a' -- "${PRODUCTION_STATE_ROOT}") == 0:0:755 && \
        $(realpath -e -- "${PROFILE_ROOT}") == "${PROFILE_ROOT}" && \
        $(stat -c '%u:%g:%a' -- "${PROFILE_ROOT}") == \
        "0:${PORTAGE_GID}:750" ]] || \
        fail 'production sample gate roots have an unsafe canonical identity'
    if ! require_trusted_production_directory_chain "${PRODUCTION_STATE_ROOT}" || \
        ! require_trusted_production_directory_chain "${PROFILE_ROOT}"; then
        fail 'production sample gate root ancestry is not immutable root-owned state'
    fi
    sync -f -- "${PRODUCTION_STATE_ROOT}" "${PRODUCTION_STATE_ROOT%/*}" \
        /var/lib/gentoo-optimization/generations
    sync -f -- "${PROFILE_ROOT}" "${PROFILE_ROOT%/*}"
    write_production_gate_status in-progress - || \
        fail 'cannot publish the durable production sample-gate in-progress marker'
    TRANSACTION_TOKEN_SHA=$(printf '%s' \
        "${PRODUCTION_TRANSACTION_TOKEN}" | sha256sum)
    TRANSACTION_TOKEN_SHA=${TRANSACTION_TOKEN_SHA%% *}
    [[ -f ${TRANSACTION_JOURNAL} && ! -L ${TRANSACTION_JOURNAL} && \
        $(stat -c '%u:%g:%a:%h' -- "${TRANSACTION_JOURNAL}") == \
        "0:${PORTAGE_GID}:640:1" ]] || \
        fail 'production transaction journal has unsafe metadata'
    [[ -f ${TRANSACTION_CHILD_IDENTITY} && ! -L ${TRANSACTION_CHILD_IDENTITY} && \
        $(stat -c '%u:%g:%a:%h' -- "${TRANSACTION_CHILD_IDENTITY}") == \
        "0:${PORTAGE_GID}:640:1" ]] || \
        fail 'production transaction child identity has unsafe metadata'
    TRANSACTION_JOURNAL_IDENTITY_BEFORE=$(stat -c \
        '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${TRANSACTION_JOURNAL}")
    TRANSACTION_CHILD_IDENTITY_BEFORE=$(stat -c \
        '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${TRANSACTION_CHILD_IDENTITY}")
    TRANSACTION_JOURNAL_SHA256=$(sha256sum -- "${TRANSACTION_JOURNAL}")
    TRANSACTION_JOURNAL_SHA256=${TRANSACTION_JOURNAL_SHA256%% *}
    TRANSACTION_CHILD_IDENTITY_SHA256=$(sha256sum -- "${TRANSACTION_CHILD_IDENTITY}")
    TRANSACTION_CHILD_IDENTITY_SHA256=${TRANSACTION_CHILD_IDENTITY_SHA256%% *}
    TRANSACTION_EXPECTED_PAYLOAD_SHA256=$(
        printf '{\n  "generation_id": "%s",\n  "inventory_id": "%s",\n  "inventory_sha256": "%s"\n}\n' \
            "${GENERATION_ID}" "${INVENTORY_ID}" "${INVENTORY_SHA256}" | \
            sha256sum | awk '{print $1}'
    )
    TRANSACTION_FRAMEWORK_AGGREGATE_SHA256=$(awk -F= \
        '$1 == "framework_aggregate_sha256" { print substr($0, index($0, "=") + 1) }' \
        "${FRAMEWORK_TARGET}/install.manifest")
    [[ ${TRANSACTION_FRAMEWORK_AGGREGATE_SHA256} =~ ^[0-9a-f]{64}$ && \
        $(grep -c '^framework_aggregate_sha256=' \
            "${FRAMEWORK_TARGET}/install.manifest") == 1 ]] || \
        fail 'active framework manifest lacks one exact aggregate identity'
    jq -e \
        --arg run "${PRODUCTION_GATE_RUN_ID}" \
        --arg generation "${GENERATION_ID}" \
        --arg inventory "${INVENTORY_ID}" \
        --arg inventory_sha "${INVENTORY_SHA256}" \
        --arg token_sha "${TRANSACTION_TOKEN_SHA}" \
        --arg payload_sha "${TRANSACTION_EXPECTED_PAYLOAD_SHA256}" \
        --arg framework_sha "${TRANSACTION_FRAMEWORK_AGGREGATE_SHA256}" '
        .schema == "gentoo-optimization-production-profile-lock-transaction-v1" and
        .test_mode == false and .gate_run_id == $run and
        .generation == {
            generation_id: $generation,
            inventory_id: $inventory,
            inventory_sha256: $inventory_sha
        } and
        .authorization_token_sha256 == $token_sha and
        .expected_payload_sha256 == $payload_sha and
        .framework_context.framework_aggregate_sha256 == $framework_sha
    ' "${TRANSACTION_JOURNAL}" >/dev/null || \
        fail 'production transaction journal differs from the requested gate identity'
    jq -e \
        --arg journal_sha "${TRANSACTION_JOURNAL_SHA256}" \
        --arg run "${PRODUCTION_GATE_RUN_ID}" \
        --arg token_sha "${TRANSACTION_TOKEN_SHA}" '
        .schema == "gentoo-optimization-production-profile-lock-child-identity-v1" and
        .test_mode == false and .gate_run_id == $run and
        .journal_sha256 == $journal_sha and
        .authorization_token_sha256 == $token_sha and
        .child.pid == .child.process_group
    ' "${TRANSACTION_CHILD_IDENTITY}" >/dev/null || \
        fail 'production transaction child identity differs from its journal or gate'
    [[ ${TRANSACTION_JOURNAL_IDENTITY_BEFORE} == "$(stat -c \
            '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${TRANSACTION_JOURNAL}")" && \
        ${TRANSACTION_CHILD_IDENTITY_BEFORE} == "$(stat -c \
            '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${TRANSACTION_CHILD_IDENTITY}")" && \
        ${TRANSACTION_JOURNAL_SHA256} == "$(sha256sum -- \
            "${TRANSACTION_JOURNAL}" | awk '{print $1}')" && \
        ${TRANSACTION_CHILD_IDENTITY_SHA256} == "$(sha256sum -- \
            "${TRANSACTION_CHILD_IDENTITY}" | awk '{print $1}')" ]] || \
        fail 'production transaction journal or child identity changed during authorization'
    [[ $(stat -c '%u:%g:%a:%h' -- "${TRANSACTION_AUTHORIZATION}") == \
        "0:${PORTAGE_GID}:640:1" ]] || \
        fail 'production transaction authorization has unsafe metadata'
    cmp -s -- "${TRANSACTION_AUTHORIZATION}" <(
        printf 'schema\tgentoo-optimization-production-profile-authorization-v1\n'
        printf 'generation_id\t%s\n' "${GENERATION_ID}"
        printf 'expected_payload_sha256\t%s\n' \
            "${TRANSACTION_EXPECTED_PAYLOAD_SHA256}"
        printf 'journal_sha256\t%s\n' "${TRANSACTION_JOURNAL_SHA256}"
        printf 'framework_aggregate_sha256\t%s\n' \
            "${TRANSACTION_FRAMEWORK_AGGREGATE_SHA256}"
        printf 'authorization_token_sha256\t%s\n' "${TRANSACTION_TOKEN_SHA}"
        printf 'child_identity_sha256\t%s\n' \
            "${TRANSACTION_CHILD_IDENTITY_SHA256}"
    ) || fail 'coordinator transaction authorization payload is not exact'
    production_authorized_command \
        "${INSTALLER}" --source-root "${ROOT}" --check >/dev/null || \
        fail 'installed framework differs from the reviewed repository source'
else
    mkdir -- "${PROFILE_ROOT}"
    chown "0:${PORTAGE_GID}" -- "${PROFILE_ROOT}"
    chmod 0750 -- "${PROFILE_ROOT}"
fi
cp -- "${TEMPLATE}" "${EBUILD}"
cp --dereference -- /etc/portage/bashrc "${PORTAGE_ROOT}/bashrc"
grep -Fxq "gentoo_opt_embedded_framework_target=${FRAMEWORK_TARGET}" \
    "${PORTAGE_ROOT}/bashrc" || \
    fail 'copied Portage dispatcher is not bound to the selected exact framework target'
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

if ((PRODUCTION_LOCKS == 0)); then
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
    PROFILE_LOCK_ARGS=(
        --test-mode
        --test-framework-lock "${FRAMEWORK_LOCK}"
        --test-project-lock "${PROJECT_LOCK}"
        --test-generation-lock "${GENERATION_LOCK}"
    )
    for lock in "${FRAMEWORK_LOCK}" "${PROJECT_LOCK}" "${GENERATION_LOCK}"; do
        [[ $(stat -c '%u:%g:%a:%h' -- "${lock}") == "0:${PORTAGE_GID}:640:1" ]] || \
            fail "fixture lock has an unsafe identity: ${lock}"
        runuser -u portage -- test -r "${lock}" || \
            fail "Portage cannot read fixture lock: ${lock}"
    done
else
    : > "${VALIDATOR_IDENTITY}"
    chown "0:${PORTAGE_GID}" -- "${VALIDATOR_IDENTITY}"
    chmod 0440 -- "${VALIDATOR_IDENTITY}"
    printf '%s\n' \
        $'mode\tproduction-locks' \
        $'profile_helper_lock_arguments\t0' \
        $'profile_validator\t/usr/local/libexec/gentoo-optimization/pgo/validate-profile.py' \
        > "${VALIDATOR_IDENTITY}.partial"
    chown "0:${PORTAGE_GID}" -- "${VALIDATOR_IDENTITY}.partial"
    chmod 0440 -- "${VALIDATOR_IDENTITY}.partial"
    mv -- "${VALIDATOR_IDENTITY}.partial" "${VALIDATOR_IDENTITY}"
    ((${#PROFILE_LOCK_ARGS[@]} == 0)) || \
        fail 'production mode unexpectedly constructed substituted lock arguments'
fi

publish_production_fingerprint() {
    local fingerprint=$1 destination=$2
    local partial=${destination}.partial
    ((PRODUCTION_LOCKS)) || return 0
    [[ ${fingerprint} =~ ^[0-9a-f]{64}$ && ${destination} == "${PRODUCTION_STATE_ROOT}"/* ]] || \
        fail 'invalid production fingerprint publication request'
    [[ ! -e ${destination} && ! -L ${destination} && \
        ! -e ${partial} && ! -L ${partial} ]] || \
        fail "production fingerprint destination is not new: ${destination}"
    printf 'fingerprint=%s\n' "${fingerprint}" > "${partial}"
    chown "0:${PORTAGE_GID}" -- "${partial}"
    chmod 0640 -- "${partial}"
    sync -f -- "${partial}"
    mv -- "${partial}" "${destination}"
    sync -f -- "${PRODUCTION_STATE_ROOT}"
}

write_map_environment() {
    local fingerprint=$1 fingerprint_file=${2:-} output=${MAP_ENV}.partial
    local -a identity_lines=()
    if ((PRODUCTION_LOCKS)); then
        [[ ${fingerprint_file} == "${PRODUCTION_STATE_ROOT}"/* ]] || \
            fail 'production map environment lacks its root-owned fingerprint file'
        identity_lines+=("GENTOO_OPT_FINGERPRINT_FILE=\"${fingerprint_file}\"")
    else
        identity_lines+=(
            'GENTOO_OPT_PORTAGE_FIXTURE_MODE="1"'
            "GENTOO_OPT_FINGERPRINT=\"${fingerprint}\""
        )
    fi
    printf '%s\n' \
        "${identity_lines[@]}" \
        'GENTOO_OPT_MODE="off"' \
        'GENTOO_OPT_PROFILE_MAP_READY="1"' \
        'GENTOO_OPT_COMPILER_FAMILY="clang"' \
        'GENTOO_OPT_ABI="amd64"' \
        > "${output}"
    chmod 0644 -- "${output}"
    mv -- "${output}" "${MAP_ENV}"
    printf '%s\n' '=app-test/phase2-pgo-use-fixture-1 generated-sample-map.conf' \
        > "${GENERATED_ASSIGNMENT}.partial"
    chmod 0644 -- "${GENERATED_ASSIGNMENT}.partial"
    mv -- "${GENERATED_ASSIGNMENT}.partial" "${GENERATED_ASSIGNMENT}"
}

write_use_environment() {
    local fingerprint=$1 fingerprint_file=${2:-} output=${USE_ENV}.partial
    local -a identity_lines=() validator_lines=()
    if ((PRODUCTION_LOCKS)); then
        [[ ${fingerprint_file} == "${PRODUCTION_STATE_ROOT}"/* ]] || \
            fail 'production use environment lacks its root-owned fingerprint file'
        identity_lines+=("GENTOO_OPT_FINGERPRINT_FILE=\"${fingerprint_file}\"")
    else
        identity_lines+=(
            'GENTOO_OPT_PORTAGE_FIXTURE_MODE="1"'
            "GENTOO_OPT_FINGERPRINT=\"${fingerprint}\""
        )
        validator_lines+=("GENTOO_OPT_PROFILE_VALIDATOR=\"${VALIDATOR_PROXY}\"")
    fi
    printf '%s\n' \
        "${identity_lines[@]}" \
        'GENTOO_OPT_MODE="clang-sample-use"' \
        'GENTOO_OPT_PROFILE_MAP_READY="0"' \
        'GENTOO_OPT_COMPILER_FAMILY="clang"' \
        'GENTOO_OPT_ABI="amd64"' \
        "GENTOO_OPT_PROFILE_PATH=\"${PROFILE}\"" \
        "GENTOO_OPT_PROFILE_MANIFEST=\"${MANIFEST}\"" \
        "GENTOO_OPT_PROFILE_METADATA=\"${SIDECAR}\"" \
        "${validator_lines[@]}" \
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
    local -a fixture_lines=()
    ((PRODUCTION_LOCKS)) || fixture_lines+=('GENTOO_OPT_PORTAGE_FIXTURE_MODE="1"')
    printf '%s\n' \
        "${fixture_lines[@]}" \
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
    if ((PRODUCTION_LOCKS)); then
        production_authorized_command \
            "PORTAGE_CONFIGROOT=${CONFIG_ROOT}" NOCOLOR=true \
            /usr/bin/ebuild --color n "${EBUILD}" "$@" > "${log}" 2>&1
    else
        PORTAGE_CONFIGROOT=${CONFIG_ROOT} NOCOLOR=true \
            ebuild --color n "${EBUILD}" "$@" > "${log}" 2>&1
    fi
}

run_ebuild_with_private_sidecar_bind() {
    local log=$1 substitute=$2
    local unshare_tool mount_tool bash_tool ebuild_tool
    ((PRODUCTION_LOCKS)) || return 1
    unshare_tool=$(command -v unshare) || return 1
    mount_tool=$(command -v mount) || return 1
    bash_tool=$(command -v bash) || return 1
    ebuild_tool=$(command -v ebuild) || return 1
    # shellcheck disable=SC2016  # Positional parameters expand in the child shell.
    production_authorized_command \
        "${unshare_tool}" --mount --propagation private -- \
        "${bash_tool}" -Eeuo pipefail -c '
            substitute=$1
            canonical=$2
            config_root=$3
            ebuild_path=$4
            mount_tool=$5
            ebuild_tool=$6
            "${mount_tool}" --bind "${substitute}" "${canonical}"
            export PORTAGE_CONFIGROOT="${config_root}" NOCOLOR=true
            exec "${ebuild_tool}" --color n "${ebuild_path}" compile
        ' bash "${substitute}" "${SIDECAR}" "${CONFIG_ROOT}" "${EBUILD}" \
        "${mount_tool}" "${ebuild_tool}" > "${log}" 2>&1
}

assert_no_persisted_authorization_token() {
    local status
    local output=${WORK}/authorization-token-persistence-scan.tsv
    ((PRODUCTION_LOCKS)) || return 0
    set +e
    printf '%s\n' "${PRODUCTION_TRANSACTION_TOKEN}" | \
        PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I \
        "${AUTHORIZATION_TOKEN_SCANNER}" --token-fd 0 --output "${output}" \
        "${WORK}" "${PROFILE_ROOT}" "${PRODUCTION_STATE_ROOT}"
    status=$?
    set -e
    ((status == 0)) || return "${status}"
    grep -Fxq $'passed\t-' "${output}" || return 1
}

publish_mapping_input() {
    local source=$1 destination=$2 partial=${2}.partial.$$
    [[ -f ${source} && ! -L ${source} && ! -e ${destination} && \
        ! -L ${destination} && ! -e ${partial} && ! -L ${partial} ]] || \
        return 1
    if ! cp --reflink=auto -- "${source}" "${partial}" || \
        ! chown "0:${PORTAGE_GID}" -- "${partial}" || \
        ! chmod 0550 -- "${partial}" || ! sync -f -- "${partial}" || \
        ! cmp -s -- "${source}" "${partial}" || \
        ! mv -T -- "${partial}" "${destination}" || \
        ! sync -f -- "${destination%/*}"; then
        rm -f -- "${partial}"
        return 1
    fi
}

field() {
    local key=$1 path=$2
    awk -F '\t' -v key="${key}" '$1 == key {sub($1 FS, ""); print; exit}' "${path}"
}

assert_framework_target_receipt() {
    local receipt=$1 label=$2
    [[ $(field framework_target "${receipt}") == "${FRAMEWORK_TARGET}" ]] || \
        fail "${label} did not execute through the exact selected framework target"
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
if ((PRODUCTION_LOCKS)); then
    publish_production_fingerprint "${SEED_FINGERPRINT}" "${SEED_FINGERPRINT_FILE}"
fi
write_map_environment "${SEED_FINGERPRINT}" "${SEED_FINGERPRINT_FILE}"
run_ebuild "${WORK}/preliminary-clean.log" clean
run_ebuild "${WORK}/preliminary-map-build.log" compile
[[ -s ${FLAGS_FILE} ]] || fail 'preliminary mapping build emitted no exact flag receipt'
cp -- "${FLAGS_FILE}" "${WORK}/preliminary-effective-flags.tsv"
assert_framework_target_receipt "${WORK}/preliminary-effective-flags.tsv" \
    'preliminary mapping build'
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

if ((PRODUCTION_LOCKS)); then
    publish_production_fingerprint "${MAP_FINGERPRINT}" "${MAP_FINGERPRINT_FILE}"
fi
write_map_environment "${MAP_FINGERPRINT}" "${MAP_FINGERPRINT_FILE}"
cp -- "${GENERATED_ASSIGNMENT}" "${WORK}/generated-package-env-map"
run_ebuild "${WORK}/map-clean.log" clean
run_ebuild "${WORK}/map-build.log" compile
[[ -s ${FLAGS_FILE} ]] || fail 'authoritative mapping build emitted no flag receipt'
cp -- "${FLAGS_FILE}" "${WORK}/map-effective-flags.tsv"
assert_framework_target_receipt "${WORK}/map-effective-flags.tsv" \
    'authoritative mapping build'
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
publish_mapping_input "${TRAIN_BINARY}" "${MAPPED_BINARY}" || \
    fail 'could not durably publish the exact mapping input in its target filesystem'
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
assert_framework_target_receipt "${WORK}/use-probe-effective-flags.tsv" \
    'ordinary consumer probe'
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
    "${PROFILE_LOCK_ARGS[@]}" \
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
    "${PROFILE_LOCK_ARGS[@]}" \
    > "${WORK}/profile-produce.stdout" 2> "${WORK}/profile-produce.stderr"
"${VALIDATOR_COMMAND}" verify --manifest "${MANIFEST}" --metadata "${SIDECAR}" \
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

if ((PRODUCTION_LOCKS)); then
    publish_production_fingerprint "${USE_FINGERPRINT}" "${USE_FINGERPRINT_FILE}"
fi
write_use_environment "${USE_FINGERPRINT}" "${USE_FINGERPRINT_FILE}"
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
assert_framework_target_receipt "${WORK}/sample-use-effective-flags.tsv" \
    'authoritative sample-use build'
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
if ((PRODUCTION_LOCKS == 0)); then
    awk -F '\t' -v uid="${PORTAGE_UID}" -v gid="${PORTAGE_GID}" \
        '$1 == "verify" && $2 == uid && $3 == gid {found = 1} END {exit !found}' \
        "${VALIDATOR_IDENTITY}" || \
        fail 'authoritative profile validation did not execute under userpriv'
else
    grep -Fxq $'mode\tproduction-locks' "${VALIDATOR_IDENTITY}" || \
        fail 'production validation identity record is absent'
    grep -Fxq $'profile_helper_lock_arguments\t0' "${VALIDATOR_IDENTITY}" || \
        fail 'production validation unexpectedly used substituted lock arguments'
fi

STAGED=${BUILD_ROOT}/image/usr/bin/phase2-pgo-use-fixture
[[ -x ${STAGED} ]] || fail 'sample-use Portage install staged no executable'
"${STAGED}" "${ITERATIONS}" > "${WORK}/sample-use.stdout"
cmp -- "${WORK}/training.stdout" "${WORK}/sample-use.stdout" || \
    fail 'sample-use executable changed functional output'

TAMPERED_SIDECAR=${WORK}/sidecar.tampered
{
    sha256sum -- "${SIDECAR}"
    stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${SIDECAR}"
} > "${WORK}/canonical-sidecar.before"
cp --preserve=mode,ownership,xattr -- "${SIDECAR}" "${TAMPERED_SIDECAR}"
printf '\n' >> "${TAMPERED_SIDECAR}"
[[ $(stat -c '%u:%g:%a:%h' -- "${TAMPERED_SIDECAR}") == \
    "0:${PORTAGE_GID}:640:1" ]] || \
    fail 'tampered sidecar substitute lost its trusted regular-file identity'
run_ebuild "${WORK}/tamper-clean.log" clean
if ((PRODUCTION_LOCKS)); then
    if run_ebuild_with_private_sidecar_bind \
        "${WORK}/tampered-sidecar.log" "${TAMPERED_SIDECAR}"; then
        fail 'real Portage sample-use build accepted a privately mounted tampered validator sidecar'
    fi
else
    cp --preserve=mode,ownership,xattr -- "${SIDECAR}" "${WORK}/sidecar.saved"
    cp --preserve=mode,ownership,xattr -- "${TAMPERED_SIDECAR}" "${SIDECAR}"
    if run_ebuild "${WORK}/tampered-sidecar.log" compile; then
        fail 'real Portage sample-use build accepted a tampered validator sidecar'
    fi
fi
grep -Fq 'authoritative profile manifest/sidecar verification failed' \
    "${WORK}/tampered-sidecar.log" || \
    fail 'tampered sidecar rejection lacked the fail-closed diagnostic'
if ((PRODUCTION_LOCKS)); then
    {
        sha256sum -- "${SIDECAR}"
        stat -c '%d:%i:%u:%g:%a:%h:%s:%y:%z' -- "${SIDECAR}"
    } > "${WORK}/canonical-sidecar.after"
    cmp -- "${WORK}/canonical-sidecar.before" \
        "${WORK}/canonical-sidecar.after" || \
        fail 'private tamper test changed the canonical production sidecar'
else
    mv -- "${WORK}/sidecar.saved" "${SIDECAR}"
fi
[[ $(stat -c '%u:%g:%a:%h' -- "${SIDECAR}") == \
    "0:${PORTAGE_GID}:640:1" ]] || fail 'restored sidecar lost trusted metadata'
"${VALIDATOR_COMMAND}" verify --manifest "${MANIFEST}" --metadata "${SIDECAR}" \
    > "${WORK}/profile-verify-restored.stdout" \
    2> "${WORK}/profile-verify-restored.stderr"
run_ebuild "${WORK}/final-clean.log" clean

if ((PRODUCTION_LOCKS)); then
    [[ ! -e ${VALIDATOR_PROXY} && ! -e ${FRAMEWORK_LOCK} && \
        ! -e ${PROJECT_LOCK} && ! -e ${GENERATION_LOCK} ]] || \
        fail 'production sample gate created a fixture helper or substituted lock'
    if grep -E 'GENTOO_OPT_PORTAGE_FIXTURE_MODE|GENTOO_OPT_FINGERPRINT=|GENTOO_OPT_PROFILE_VALIDATOR' \
        "${MAP_ENV}" "${USE_ENV}"; then
        fail 'production generated policy contains a fixture marker, inline fingerprint, or validator override'
    fi
    cp -- "${TRANSACTION_JOURNAL}" "${WORK}/transaction-journal.json"
    cp -- "${TRANSACTION_CHILD_IDENTITY}" \
        "${WORK}/transaction-child-identity.json"
    chmod 0600 -- "${WORK}/transaction-journal.json" \
        "${WORK}/transaction-child-identity.json"
    [[ ${TRANSACTION_JOURNAL_SHA256} == "$(sha256sum -- \
            "${WORK}/transaction-journal.json" | awk '{print $1}')" && \
        ${TRANSACTION_CHILD_IDENTITY_SHA256} == "$(sha256sum -- \
            "${WORK}/transaction-child-identity.json" | awk '{print $1}')" ]] || \
        fail 'preserved transaction journal/child evidence differs from its live identity'
    assert_no_persisted_authorization_token || \
        fail 'raw coordinator authorization persisted in production gate artifacts'
    PRODUCTION_TRANSACTION_TOKEN=
    [[ $(readlink -- "${FRAMEWORK_CURRENT}") == "${FRAMEWORK_TARGET}" ]] || \
        fail 'active framework target changed during the production sample gate'
    PRODUCTION_GATE_COMPLETE=1
    write_production_gate_status passed 0 || \
        fail 'cannot publish the durable production sample-gate pass marker'
    PRODUCTION_STATUS_FINALIZED=1
    capture_production_live_roots passed || \
        fail 'cannot preserve and index the passed live production sample-gate roots'
    PRODUCTION_CAPTURED=1
    {
        printf 'mode\tproduction-locks\n'
        printf 'work_root\t%s\n' "${WORK}"
        printf 'profile_root\t%s\n' "${PROFILE_ROOT}"
        printf 'generation_state_root\t%s\n' "${PRODUCTION_STATE_ROOT}"
        printf 'generation_id\t%s\n' "${GENERATION_ID}"
        printf 'inventory_id\t%s\n' "${INVENTORY_ID}"
        printf 'inventory_sha256\t%s\n' "${INVENTORY_SHA256}"
        for artifact in "${MAPPED_BINARY}" "${PERF_DATA}" "${PROFILE}" \
            "${SAMPLE_METADATA}" "${CONVERSION_LOG}" "${MANIFEST}" "${SIDECAR}" \
            "${SEED_FINGERPRINT_FILE}" "${MAP_FINGERPRINT_FILE}" \
            "${USE_FINGERPRINT_FILE}" "${PROFILE_ROOT}/gate-status.tsv" \
            "${PRODUCTION_STATE_ROOT}/gate-status.tsv" \
            "${TRANSACTION_AUTHORIZATION}" "${TRANSACTION_JOURNAL}" \
            "${TRANSACTION_CHILD_IDENTITY}"; do
            printf 'artifact\t%s\t%s\t%s\n' "${artifact}" \
                "$(sha256sum -- "${artifact}" | awk '{print $1}')" \
                "$(stat -c '%u:%g:%a:%h:%s' -- "${artifact}")"
        done
    } > "${WORK}/production-artifacts.tsv"
fi

{
    printf '%s\t%s\n' \
        'authoritative_work_root' "${WORK}" \
        'published_copy' "${CANONICAL_OUTPUT_DIR:-none}" \
        'published_copy_semantics' \
        'historical-byte-evidence; validator sidecars remain bound to authoritative paths' \
        'authoritative_work_final_identity' 'root:portage:0750'
    if ((PRODUCTION_LOCKS)); then
        printf 'production_work_root\t%s\n' "${PRODUCTION_GATE_WORK_ROOT}"
        printf 'profile_artifact_root\t%s\n' "${PROFILE_ROOT}"
        printf 'generation_state_root\t%s\n' "${PRODUCTION_STATE_ROOT}"
    fi
} > "${WORK}/publication-context.tsv"
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
    sha256sum -- "${AUTHORIZATION_TOKEN_SCANNER}"
    if ((PRODUCTION_LOCKS)); then
        sha256sum -- production-artifacts.tsv production-live-roots.tsv \
            authorization-token-persistence-scan.tsv \
            transaction-journal.json transaction-child-identity.json \
            canonical-sidecar.before canonical-sidecar.after \
            profile/gate-status.tsv production-state/gate-status.tsv \
            production-state/seed.fingerprint \
            production-state/mapping.fingerprint \
            production-state/consumer.fingerprint
    fi
) > "${WORK}/evidence.sha256"
if ((PRODUCTION_LOCKS)); then
    printf 'PASS: production-lock Portage exact mapping fingerprint, perf training, sample conversion, distinct consumer fingerprint, immutable validation, generated package.env use, runtime proof, and tamper rejection\n'
else
    printf 'PASS: real Portage exact mapping fingerprint, perf training, sample conversion, distinct consumer fingerprint, immutable validation, generated package.env use, runtime proof, and tamper rejection\n'
fi

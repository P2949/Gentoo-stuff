#!/usr/bin/env bash
# Golden renderer copied from the stable-bootstrap implementation installed by
# commit 8a1200915d2693fd7486a421a9b232f638e9840c.  Keep this fixture independent
# of scripts/optimization/install-framework.sh: it is the byte oracle for the
# currently deployed pre-Candidate-A ten-helper schema.
set -Eeuo pipefail
IFS=$'\n\t'
umask 022
export LC_ALL=C

[[ $# -eq 5 ]] || {
    printf 'usage: %s OUTPUT FRAMEWORK_BASE FRAMEWORK_CURRENT TRUST_ANCHOR EXPECTED_UID\n' \
        "${0##*/}" >&2
    exit 2
}
OUTPUT=$1
FRAMEWORK_BASE=$2
FRAMEWORK_CURRENT=$3
FRAMEWORK_TRUST_ANCHOR=$4
EXPECTED_UID=$5

[[ ${OUTPUT} == /* && ${FRAMEWORK_BASE} == /* && ${FRAMEWORK_CURRENT} == /* && \
    ${FRAMEWORK_TRUST_ANCHOR} == /* && ${EXPECTED_UID} =~ ^[0-9]+$ ]] || {
    printf 'golden renderer received an invalid path or UID\n' >&2
    exit 2
}
[[ ! -e ${OUTPUT} && ! -L ${OUTPUT} ]] || {
    printf 'golden renderer output already exists: %s\n' "${OUTPUT}" >&2
    exit 2
}

declare -ar HELPERS=(
    bolt/artifact_tool.py
    bolt/capture-input.sh
    bolt/deploy-output.sh
    bolt/register-output.sh
    pgo/profile-identity.py
    pgo/profile_locks.py
    pgo/validate-profile.py
    recovery/verify-binpkg-snapshot.py
    scripts/optimization/lib/state.py
    scripts/optimization/verify/reconcile-state.py
)

python_literal() {
    /usr/bin/python3 -I -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}

render_shell() {
    local relative=$1
    # shellcheck disable=SC2016 # Literal historical dispatcher source.
    printf '%s\n' \
        '#!/bin/bash' \
        'set -Eeuo pipefail' \
        "FRAMEWORK_CURRENT=$(printf '%q' "${FRAMEWORK_CURRENT}")" \
        "FRAMEWORK_BASE=$(printf '%q' "${FRAMEWORK_BASE}")" \
        "FRAMEWORK_TRUST_ANCHOR=$(printf '%q' "${FRAMEWORK_TRUST_ANCHOR}")" \
        "FRAMEWORK_RELATIVE=$(printf '%q' "${relative}")" \
        "EXPECTED_UID=$(printf '%q' "${EXPECTED_UID}")" \
        'FRAMEWORK_COMPONENT=${FRAMEWORK_BASE}' \
        'while :; do' \
        '    [[ -d ${FRAMEWORK_COMPONENT} && ! -L ${FRAMEWORK_COMPONENT} ]] || exit 125' \
        '    FRAMEWORK_STAT=$(/usr/bin/stat -c '\''%u:%a'\'' -- "${FRAMEWORK_COMPONENT}") || exit 125' \
        '    FRAMEWORK_OWNER=${FRAMEWORK_STAT%%:*}' \
        '    FRAMEWORK_MODE=${FRAMEWORK_STAT#*:}' \
        '    [[ ${FRAMEWORK_OWNER} == "${EXPECTED_UID}" && ${FRAMEWORK_MODE} =~ ^[0-7]{3,4}$ ]] || exit 125' \
        '    (( (8#${FRAMEWORK_MODE} & 8#022) == 0 )) || { printf '\''gentoo-optimization: framework trust path is writable by an untrusted identity\n'\'' >&2; exit 125; }' \
        '    [[ ${FRAMEWORK_COMPONENT} == "${FRAMEWORK_TRUST_ANCHOR}" ]] && break' \
        '    if [[ ${FRAMEWORK_TRUST_ANCHOR} == / ]]; then [[ ${FRAMEWORK_COMPONENT} == /* ]] || exit 125; else [[ ${FRAMEWORK_COMPONENT} == "${FRAMEWORK_TRUST_ANCHOR}"/* ]] || exit 125; fi' \
        '    FRAMEWORK_COMPONENT=${FRAMEWORK_COMPONENT%/*}' \
        '    [[ -n ${FRAMEWORK_COMPONENT} ]] || FRAMEWORK_COMPONENT=/' \
        'done' \
        'if [[ -n ${GENTOO_OPT_FRAMEWORK_TARGET-} ]]; then' \
        '    FRAMEWORK_TARGET=${GENTOO_OPT_FRAMEWORK_TARGET}' \
        'else' \
        '    [[ -L ${FRAMEWORK_CURRENT} ]] || { printf '\''gentoo-optimization: active framework link is unavailable\n'\'' >&2; exit 125; }' \
        '    FRAMEWORK_LINK_STAT=$(/usr/bin/stat -c '\''%F:%u'\'' -- "${FRAMEWORK_CURRENT}") || exit 125' \
        '    [[ ${FRAMEWORK_LINK_STAT} == "symbolic link:${EXPECTED_UID}" ]] || { printf '\''gentoo-optimization: active framework link has an untrusted identity\n'\'' >&2; exit 125; }' \
        '    FRAMEWORK_TARGET=$(/usr/bin/readlink -- "${FRAMEWORK_CURRENT}") || exit 125' \
        'fi' \
        'FRAMEWORK_ID=${FRAMEWORK_TARGET#"${FRAMEWORK_BASE}"/framework-}' \
        '[[ ${FRAMEWORK_TARGET} == "${FRAMEWORK_BASE}/framework-${FRAMEWORK_ID}" && ${FRAMEWORK_ID} =~ ^[0-9a-f]{64}$ ]] || { printf '\''gentoo-optimization: selected framework target is unmanaged\n'\'' >&2; exit 125; }' \
        '[[ -d ${FRAMEWORK_TARGET} && ! -L ${FRAMEWORK_TARGET} ]] || { printf '\''gentoo-optimization: active framework target is unavailable\n'\'' >&2; exit 125; }' \
        'FRAMEWORK_TOOL=${FRAMEWORK_TARGET}/libexec/${FRAMEWORK_RELATIVE}' \
        '[[ -f ${FRAMEWORK_TOOL} && ! -L ${FRAMEWORK_TOOL} && -x ${FRAMEWORK_TOOL} ]] || { printf '\''gentoo-optimization: active framework helper is unavailable\n'\'' >&2; exit 125; }' \
        'FRAMEWORK_COMPONENT=${FRAMEWORK_TOOL}' \
        'while :; do' \
        '    FRAMEWORK_STAT=$(/usr/bin/stat -c '\''%u:%a'\'' -- "${FRAMEWORK_COMPONENT}") || exit 125' \
        '    FRAMEWORK_OWNER=${FRAMEWORK_STAT%%:*}' \
        '    FRAMEWORK_MODE=${FRAMEWORK_STAT#*:}' \
        '    [[ ${FRAMEWORK_OWNER} == "${EXPECTED_UID}" && ${FRAMEWORK_MODE} =~ ^[0-7]{3,4}$ ]] || exit 125' \
        '    (( (8#${FRAMEWORK_MODE} & 8#022) == 0 )) || { printf '\''gentoo-optimization: active framework helper path is writable by an untrusted identity\n'\'' >&2; exit 125; }' \
        '    [[ ${FRAMEWORK_COMPONENT} == "${FRAMEWORK_TARGET}" ]] && break' \
        '    FRAMEWORK_COMPONENT=${FRAMEWORK_COMPONENT%/*}' \
        'done' \
        'exec /bin/bash -- "${FRAMEWORK_TOOL}" "$@"'
}

render_python() {
    local relative=$1
    printf '%s\n' \
        '#!/usr/bin/python3 -I' \
        '"""Stable active-framework dispatcher; contains no mutable implementation."""' \
        'import os' \
        'import re' \
        'import stat' \
        'import sys' \
        "FRAMEWORK_CURRENT = $(python_literal "${FRAMEWORK_CURRENT}")" \
        "FRAMEWORK_BASE = $(python_literal "${FRAMEWORK_BASE}")" \
        "FRAMEWORK_TRUST_ANCHOR = $(python_literal "${FRAMEWORK_TRUST_ANCHOR}")" \
        "FRAMEWORK_RELATIVE = $(python_literal "${relative}")" \
        "EXPECTED_UID = ${EXPECTED_UID}" \
        'def abort(message):' \
        '    print(f"gentoo-optimization: {message}", file=sys.stderr)' \
        '    raise SystemExit(125)' \
        'def trusted(path):' \
        '    try:' \
        '        metadata = os.lstat(path)' \
        '    except OSError as error:' \
        '        abort(f"cannot stat active framework path: {error}")' \
        '    if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != EXPECTED_UID or stat.S_IMODE(metadata.st_mode) & 0o022:' \
        '        abort("active framework path has an untrusted identity")' \
        '    return metadata' \
        'component = FRAMEWORK_BASE' \
        'while True:' \
        '    if not stat.S_ISDIR(trusted(component).st_mode):' \
        '        abort("framework trust path is not a directory")' \
        '    if component == FRAMEWORK_TRUST_ANCHOR:' \
        '        break' \
        '    if os.path.commonpath((component, FRAMEWORK_TRUST_ANCHOR)) != FRAMEWORK_TRUST_ANCHOR:' \
        '        abort("framework trust path escapes its anchor")' \
        '    component = os.path.dirname(component) or "/"' \
        'framework_target = os.environ.get("GENTOO_OPT_FRAMEWORK_TARGET", "")' \
        'if not framework_target:' \
        '    try:' \
        '        link_metadata = os.lstat(FRAMEWORK_CURRENT)' \
        '    except OSError as error:' \
        '        abort(f"active framework link is unavailable: {error}")' \
        '    if not stat.S_ISLNK(link_metadata.st_mode) or link_metadata.st_uid != EXPECTED_UID:' \
        '        abort("active framework link has an untrusted identity")' \
        '    try:' \
        '        framework_target = os.readlink(FRAMEWORK_CURRENT)' \
        '    except OSError as error:' \
        '        abort(f"cannot resolve active framework link: {error}")' \
        'prefix = FRAMEWORK_BASE + "/framework-"' \
        'identity = framework_target[len(prefix):] if framework_target.startswith(prefix) else ""' \
        'if re.fullmatch(r"[0-9a-f]{64}", identity) is None or framework_target != prefix + identity:' \
        '    abort("selected framework target is unmanaged")' \
        'framework_tool = framework_target + "/libexec/" + FRAMEWORK_RELATIVE' \
        'component = framework_tool' \
        'while True:' \
        '    metadata = trusted(component)' \
        '    if component == framework_tool and (not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & stat.S_IXUSR):' \
        '        abort("active framework helper is not executable")' \
        '    if component == framework_target:' \
        '        break' \
        '    component = os.path.dirname(component)' \
        'os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", framework_tool, *sys.argv[1:]])'
}

mkdir -p -- "${OUTPUT}/bolt" "${OUTPUT}/pgo" "${OUTPUT}/recovery" \
    "${OUTPUT}/scripts/optimization/lib" "${OUTPUT}/scripts/optimization/verify"
for relative in "${HELPERS[@]}"; do
    case ${relative} in
        *.py) render_python "${relative}" >"${OUTPUT}/${relative}" ;;
        *) render_shell "${relative}" >"${OUTPUT}/${relative}" ;;
    esac
    chmod 0755 -- "${OUTPUT}/${relative}"
done

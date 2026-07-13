#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
readonly ROOT
STATE_ROOT=/var/lib/gentoo-optimization/state/project
MANIFEST=${STATE_ROOT}/phase-2-framework-install.manifest
MODE=install

if (($#)); then
    [[ $# == 1 && $1 == --check ]] || {
        printf 'Usage: %s [--check]\n' "$0" >&2
        exit 2
    }
    MODE=check
fi
((EUID == 0)) || {
    printf 'ERROR: framework installation/check requires root\n' >&2
    exit 1
}

declare -a SOURCES=(
    "${ROOT}/portage/bashrc"
    "${ROOT}/portage/install-qa-check.d/50-gentoo-optimization-bolt"
    "${ROOT}/scripts/optimization/bolt/artifact_tool.py"
    "${ROOT}/scripts/optimization/bolt/capture-input.sh"
    "${ROOT}/scripts/optimization/bolt/deploy-output.sh"
    "${ROOT}/scripts/optimization/bolt/register-output.sh"
    "${ROOT}/scripts/optimization/pgo/profile-identity.py"
    "${ROOT}/scripts/optimization/pgo/validate-profile.py"
)
declare -a DESTINATIONS=(
    /etc/portage/bashrc
    /usr/local/lib/install-qa-check.d/50-gentoo-optimization-bolt
    /usr/local/libexec/gentoo-optimization/bolt/artifact_tool.py
    /usr/local/libexec/gentoo-optimization/bolt/capture-input.sh
    /usr/local/libexec/gentoo-optimization/bolt/deploy-output.sh
    /usr/local/libexec/gentoo-optimization/bolt/register-output.sh
    /usr/local/libexec/gentoo-optimization/pgo/profile-identity.py
    /usr/local/libexec/gentoo-optimization/pgo/validate-profile.py
)
declare -a MODES=(0644 0644 0755 0755 0755 0755 0755 0755)

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

verify_source() {
    local source=$1
    [[ -f ${source} && ! -L ${source} ]] || \
        fail "source is not a regular non-symlink file: ${source}"
}

verify_destination() {
    local source=$1 destination=$2 expected_mode=$3
    local source_hash destination_hash owner group actual_mode
    [[ -f ${destination} && ! -L ${destination} ]] || \
        fail "installed path is not a regular non-symlink file: ${destination}"
    owner=$(stat -c %U -- "${destination}")
    group=$(stat -c %G -- "${destination}")
    actual_mode=$(stat -c %a -- "${destination}")
    [[ ${owner}:${group} == root:root ]] || \
        fail "installed path is not root:root: ${destination}"
    [[ 0${actual_mode} == "${expected_mode}" ]] || \
        fail "installed path mode is ${actual_mode}, expected ${expected_mode}: ${destination}"
    source_hash=$(sha256sum -- "${source}"); source_hash=${source_hash%% *}
    destination_hash=$(sha256sum -- "${destination}"); destination_hash=${destination_hash%% *}
    [[ ${source_hash} == "${destination_hash}" ]] || \
        fail "installed path hash differs from reviewed source: ${destination}"
    printf '%s\t%s\t%s\troot:root\n' \
        "${destination}" "${source_hash}" "${expected_mode}"
}

publish_file() {
    local source=$1 destination=$2 mode=$3 directory temporary
    directory=${destination%/*}
    mkdir -p -- "${directory}"
    chown root:root -- "${directory}"
    chmod go-w -- "${directory}"
    temporary=${destination}.partial.$$
    rm -f -- "${temporary}"
    install -o root -g root -m "${mode}" -T -- "${source}" "${temporary}"
    sync -f -- "${temporary}"
    mv -fT -- "${temporary}" "${destination}"
    sync -f -- "${directory}"
}

for source in "${SOURCES[@]}"; do
    verify_source "${source}"
done

if [[ ${MODE} == install ]]; then
    for index in "${!SOURCES[@]}"; do
        publish_file "${SOURCES[index]}" "${DESTINATIONS[index]}" "${MODES[index]}"
    done
fi

manifest_temporary=${MANIFEST}.partial.$$
if [[ ${MODE} == install ]]; then
    mkdir -p -- "${STATE_ROOT}"
    chown root:root -- "${STATE_ROOT}"
    chmod 0700 -- "${STATE_ROOT}"
    {
        printf 'schema=gentoo-optimization-framework-install-v1\n'
        printf 'repository=%s\n' "${ROOT}"
        printf 'path\tsha256\tmode\towner\n'
        for index in "${!SOURCES[@]}"; do
            verify_destination "${SOURCES[index]}" "${DESTINATIONS[index]}" \
                "${MODES[index]}"
        done
    } > "${manifest_temporary}"
    chown root:root -- "${manifest_temporary}"
    chmod 0600 -- "${manifest_temporary}"
    sync -f -- "${manifest_temporary}"
    mv -fT -- "${manifest_temporary}" "${MANIFEST}"
    sync -f -- "${STATE_ROOT}"
else
    [[ -f ${MANIFEST} && ! -L ${MANIFEST} ]] || \
        fail "framework install manifest is absent: ${MANIFEST}"
    [[ $(stat -c '%U:%G:%a' -- "${MANIFEST}") == root:root:600 ]] || \
        fail "framework install manifest ownership/mode is not root:root:0600"
    for index in "${!SOURCES[@]}"; do
        verify_destination "${SOURCES[index]}" "${DESTINATIONS[index]}" \
            "${MODES[index]}" >/dev/null
    done
fi

printf 'PASS: root-owned Phase 2 framework %s verified (%s)\n' \
    "${MODE}" "${MANIFEST}"

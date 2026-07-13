#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
readonly ROOT
STATE_ROOT=/var/lib/gentoo-optimization/state/project
MANIFEST=${STATE_ROOT}/phase-2-framework-install.manifest
PORTAGE_CURRENT=/var/lib/gentoo-optimization/portage-current
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
    "${ROOT}/portage/install-qa-check.d/zz-gentoo-optimization-bolt"
    "${ROOT}/scripts/optimization/bolt/artifact_tool.py"
    "${ROOT}/scripts/optimization/bolt/capture-input.sh"
    "${ROOT}/scripts/optimization/bolt/deploy-output.sh"
    "${ROOT}/scripts/optimization/bolt/register-output.sh"
    "${ROOT}/scripts/optimization/pgo/profile-identity.py"
    "${ROOT}/scripts/optimization/pgo/validate-profile.py"
)
declare -a DESTINATIONS=(
    /etc/portage/bashrc
    /usr/local/lib/install-qa-check.d/zz-gentoo-optimization-bolt
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

portage_source_hash() {
    local entry relative mode digest target
    (
        cd -- "${ROOT}/portage"
        while IFS= read -r -d '' entry; do
            relative=${entry#./}
            if [[ -L ${entry} ]]; then
                target=$(readlink -- "${entry}")
                printf 'l\t%s\t%s\n' "${relative}" "${target}"
            elif [[ -d ${entry} ]]; then
                printf 'd\t0755\t%s\n' "${relative}"
            elif [[ -f ${entry} ]]; then
                if [[ -x ${entry} ]]; then mode=0755; else mode=0644; fi
                digest=$(sha256sum -- "${entry}"); digest=${digest%% *}
                printf 'f\t%s\t%s\t%s\n' "${mode}" "${relative}" "${digest}"
            else
                fail "unsupported Portage source entry type: ${entry}"
            fi
        done < <(find . -mindepth 1 -print0 | LC_ALL=C sort -z)
    ) | sha256sum | awk '{print $1}'
}

verify_portage_tree() {
    local live=$1 entry relative expected actual mode
    [[ -d ${live} && ! -L ${live} ]] || fail "Portage live generation is invalid: ${live}"
    [[ $(<"${live}/.gentoo-optimization-source-hash") == "${PORTAGE_SOURCE_HASH}" ]] || \
        fail "Portage live generation has the wrong source hash: ${live}"
    while IFS= read -r -d '' entry; do
        relative=${entry#"${ROOT}/portage/"}
        [[ ${relative} != "${entry}" ]] || continue
        if [[ -L ${entry} ]]; then
            [[ -L ${live}/${relative} ]] || fail "live Portage symlink is absent: ${relative}"
            expected=$(readlink -- "${entry}")
            actual=$(readlink -- "${live}/${relative}")
            [[ ${actual} == "${expected}" ]] || fail "live Portage symlink differs: ${relative}"
        elif [[ -d ${entry} ]]; then
            [[ -d ${live}/${relative} && ! -L ${live}/${relative} ]] || \
                fail "live Portage directory is absent: ${relative}"
            [[ $(stat -c '%U:%G:%a' -- "${live}/${relative}") == root:root:755 ]] || \
                fail "live Portage directory ownership/mode differs: ${relative}"
        elif [[ -f ${entry} ]]; then
            [[ -f ${live}/${relative} && ! -L ${live}/${relative} ]] || \
                fail "live Portage file is absent: ${relative}"
            if [[ -x ${entry} ]]; then mode=755; else mode=644; fi
            [[ $(stat -c '%U:%G:%a' -- "${live}/${relative}") == root:root:${mode} ]] || \
                fail "live Portage file ownership/mode differs: ${relative}"
            expected=$(sha256sum -- "${entry}"); expected=${expected%% *}
            actual=$(sha256sum -- "${live}/${relative}"); actual=${actual%% *}
            [[ ${actual} == "${expected}" ]] || fail "live Portage file hash differs: ${relative}"
        fi
    done < <(find "${ROOT}/portage" -mindepth 1 -print0 | LC_ALL=C sort -z)
}

deploy_portage_tree() {
    local generation_root live stage second_hash link_tmp etc_tmp
    generation_root=/var/lib/gentoo-optimization/portage-${PORTAGE_SOURCE_HASH}
    live=${generation_root}/portage
    if [[ ! -e ${generation_root} ]]; then
        stage=${generation_root}.partial.$$
        rm -rf -- "${stage}"
        mkdir -p -- "${stage}/portage"
        cp -a -- "${ROOT}/portage/." "${stage}/portage/"
        find "${stage}/portage" -type d -exec chmod 0755 -- {} +
        while IFS= read -r -d '' entry; do
            if [[ -x ${entry} ]]; then chmod 0755 -- "${entry}"; else chmod 0644 -- "${entry}"; fi
        done < <(find "${stage}/portage" -type f -print0)
        printf '%s\n' "${PORTAGE_SOURCE_HASH}" > \
            "${stage}/portage/.gentoo-optimization-source-hash"
        chmod 0600 -- "${stage}/portage/.gentoo-optimization-source-hash"
        chown -R root:root -- "${stage}"
        second_hash=$(portage_source_hash)
        [[ ${second_hash} == "${PORTAGE_SOURCE_HASH}" ]] || \
            fail 'Portage source changed during root-owned publication'
        mv -T -- "${stage}" "${generation_root}"
    fi
    verify_portage_tree "${live}"
    link_tmp=${PORTAGE_CURRENT}.partial.$$
    ln -s -- "${live}" "${link_tmp}"
    mv -fT -- "${link_tmp}" "${PORTAGE_CURRENT}"
    etc_tmp=/etc/portage.partial.$$
    ln -s -- "${PORTAGE_CURRENT}" "${etc_tmp}"
    mv -fT -- "${etc_tmp}" /etc/portage
    [[ $(readlink -- /etc/portage) == "${PORTAGE_CURRENT}" ]] || \
        fail '/etc/portage did not publish the root-owned current generation'
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
PORTAGE_SOURCE_HASH=$(portage_source_hash)
[[ ${PORTAGE_SOURCE_HASH} =~ ^[0-9a-f]{64}$ ]] || fail 'cannot hash Portage source tree'

if [[ ${MODE} == install ]]; then
    deploy_portage_tree
    install -d -o root -g root -m 0700 \
        /var/cache/gentoo-optimization/bolt \
        /var/cache/gentoo-optimization/bolt/inputs \
        /var/cache/gentoo-optimization/bolt/outputs \
        /var/cache/gentoo-optimization/bolt/perf \
        /var/cache/gentoo-optimization/bolt/fdata \
        /var/cache/gentoo-optimization/bolt/diagnostics \
        /var/cache/gentoo-optimization/bolt/locks
    for index in "${!SOURCES[@]}"; do
        publish_file "${SOURCES[index]}" "${DESTINATIONS[index]}" "${MODES[index]}"
    done
    rm -f -- /usr/local/lib/install-qa-check.d/50-gentoo-optimization-bolt
fi

manifest_temporary=${MANIFEST}.partial.$$
if [[ ${MODE} == install ]]; then
    mkdir -p -- "${STATE_ROOT}"
    chown root:root -- "${STATE_ROOT}"
    chmod 0700 -- "${STATE_ROOT}"
    {
        printf 'schema=gentoo-optimization-framework-install-v1\n'
        printf 'repository=%s\n' "${ROOT}"
        printf 'portage_source_hash=%s\n' "${PORTAGE_SOURCE_HASH}"
        printf 'portage_current=%s\n' "${PORTAGE_CURRENT}"
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
    [[ -L /etc/portage && $(readlink -- /etc/portage) == "${PORTAGE_CURRENT}" ]] || \
        fail '/etc/portage is not the root-owned current-generation link'
    [[ -L ${PORTAGE_CURRENT} ]] || fail 'root-owned Portage current link is absent'
    verify_portage_tree "$(readlink -e -- "${PORTAGE_CURRENT}")"
    for index in "${!SOURCES[@]}"; do
        verify_destination "${SOURCES[index]}" "${DESTINATIONS[index]}" \
            "${MODES[index]}" >/dev/null
    done
fi

printf 'PASS: root-owned Phase 2 framework %s verified (%s)\n' \
    "${MODE}" "${MANIFEST}"

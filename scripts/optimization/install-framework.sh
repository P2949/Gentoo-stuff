#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export LC_ALL=C

# This installer is the only reviewed bridge from the mutable checkout to the
# live, root-owned Phase 2 framework.  It snapshots every input once, builds and
# verifies one immutable candidate, publishes the regular helper entry points
# while Portage is quiescent, and changes the single framework-current link last.

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
readonly ROOT
MODE=install
TEST_ROOT=

usage() {
    cat <<EOF
Usage: ${0##*/} [--check]

The following interface exists only for the hermetic repository fixture:
  GENTOO_OPT_INSTALLER_TEST_MODE=1 ${0##*/} [--check] --test-root ABSOLUTE_PATH
EOF
}

while (($#)); do
    case $1 in
        --check)
            MODE=check
            ;;
        --test-root)
            shift
            (($#)) || { usage >&2; exit 2; }
            TEST_ROOT=$1
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
    shift
done

if [[ -n ${TEST_ROOT} ]]; then
    [[ ${GENTOO_OPT_INSTALLER_TEST_MODE:-0} == 1 ]] || {
        printf 'ERROR: --test-root is restricted to the hermetic installer fixture\n' >&2
        exit 2
    }
    [[ ${TEST_ROOT} == /* && ${TEST_ROOT} != / && -d ${TEST_ROOT} && ! -L ${TEST_ROOT} ]] || {
        printf 'ERROR: test root must be an existing absolute non-symlink directory\n' >&2
        exit 2
    }
    TEST_ROOT=$(realpath -e -- "${TEST_ROOT}")
else
    ((EUID == 0)) || {
        printf 'ERROR: framework installation/check requires root\n' >&2
        exit 1
    }
    [[ -z ${GENTOO_OPT_INSTALLER_FAIL_AT:-}${GENTOO_OPT_INSTALLER_PAUSE_AT:-} ]] || {
        printf 'ERROR: failure injection is forbidden outside --test-root\n' >&2
        exit 2
    }
fi

EXPECTED_UID=$EUID
EXPECTED_GID=$(id -g)

physical() {
    local logical=$1
    [[ ${logical} == /* ]] || return 1
    if [[ -n ${TEST_ROOT} ]]; then
        printf '%s%s\n' "${TEST_ROOT}" "${logical}"
    else
        printf '%s\n' "${logical}"
    fi
}

BASE=$(physical /var/lib/gentoo-optimization)
FRAMEWORK_CURRENT=${BASE}/framework-current
STATE_ROOT=${BASE}/state/project
MANIFEST=${STATE_ROOT}/phase-2-framework-install.manifest
CACHE_ROOT=$(physical /var/cache/gentoo-optimization/bolt)
INSTALL_QA_ROOT=$(physical /usr/local/lib/install-qa-check.d)
LIBEXEC_ROOT=$(physical /usr/local/libexec/gentoo-optimization)
ETC_PORTAGE=$(physical /etc/portage)
LOCK_PATH=$(physical /run/lock/gentoo-optimization-framework-install.lock)
readonly BASE FRAMEWORK_CURRENT STATE_ROOT MANIFEST CACHE_ROOT INSTALL_QA_ROOT \
    LIBEXEC_ROOT ETC_PORTAGE LOCK_PATH

HOOK_BASENAME=zz-gentoo-optimization-bolt
readonly HOOK_BASENAME

declare -a INPUT_FILES=(
    scripts/optimization/install-framework.sh
    scripts/optimization/bolt/artifact_tool.py
    scripts/optimization/bolt/capture-input.sh
    scripts/optimization/bolt/deploy-output.sh
    scripts/optimization/bolt/register-output.sh
    scripts/optimization/pgo/profile-identity.py
    scripts/optimization/pgo/validate-profile.py
)
declare -a HELPER_RELATIVE=(
    bolt/artifact_tool.py
    bolt/capture-input.sh
    bolt/deploy-output.sh
    bolt/register-output.sh
    pgo/profile-identity.py
    pgo/validate-profile.py
)
declare -a HELPER_SOURCE_RELATIVE=(
    scripts/optimization/bolt/artifact_tool.py
    scripts/optimization/bolt/capture-input.sh
    scripts/optimization/bolt/deploy-output.sh
    scripts/optimization/bolt/register-output.sh
    scripts/optimization/pgo/profile-identity.py
    scripts/optimization/pgo/validate-profile.py
)

SNAPSHOT=
CANDIDATE_STAGE=
CANDIDATE_FINAL=
CREATED_CANDIDATE=0
COMMITTED=0
ROLLBACK_REQUIRED=0
ROLLBACK_ROOT=
PREVIOUS_TARGET=none
INSTALLER_LOCK_FD=
declare -a HELD_BOLT_LOCK_FDS=()

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    return 1
}

mode_is_trusted() {
    local mode=$1
    [[ ${mode} =~ ^[0-7]{3,4}$ ]] && (( (8#${mode} & 8#022) == 0 ))
}

# Walk lexical components without realpath so an unsafe symlink is rejected,
# not followed.  Missing trailing components are permitted for preflight.
verify_existing_ancestor_chain() {
    local path=$1 current=/ remainder component uid mode
    [[ ${path} == /* ]] || fail "trusted path is not absolute: ${path}"
    if [[ -n ${TEST_ROOT} && (${path} == "${TEST_ROOT}" || ${path} == "${TEST_ROOT}"/*) ]]; then
        current=${TEST_ROOT}
        remainder=${path#"${TEST_ROOT}"/}
        [[ ${path} != "${TEST_ROOT}" ]] || remainder=
        uid=$(stat -c %u -- "${current}")
        mode=$(stat -c %a -- "${current}")
        [[ ${uid} == "${EXPECTED_UID}" ]] || fail "test trust root has the wrong owner: ${current}"
        mode_is_trusted "${mode}" || fail "test trust root is group/world-writable: ${current}"
    else
        remainder=${path#/}
    fi
    while IFS= read -r component; do
        [[ -n ${component} ]] || continue
        if [[ ${current} == / ]]; then current=/${component}; else current=${current}/${component}; fi
        [[ -e ${current} || -L ${current} ]] || break
        [[ -d ${current} && ! -L ${current} ]] || \
            fail "trusted ancestor is not a non-symlink directory: ${current}"
        uid=$(stat -c %u -- "${current}")
        mode=$(stat -c %a -- "${current}")
        [[ ${uid} == "${EXPECTED_UID}" ]] || fail "trusted ancestor has the wrong owner: ${current}"
        mode_is_trusted "${mode}" || fail "trusted ancestor is group/world-writable: ${current}"
    done < <(tr '/' '\n' <<<"${remainder}")
}

verify_regular_trusted() {
    local path=$1 expected_mode=${2:-} uid gid mode
    [[ -f ${path} && ! -L ${path} ]] || fail "expected a regular non-symlink file: ${path}"
    uid=$(stat -c %u -- "${path}")
    gid=$(stat -c %g -- "${path}")
    mode=$(stat -c %a -- "${path}")
    [[ ${uid}:${gid} == "${EXPECTED_UID}:${EXPECTED_GID}" ]] || \
        fail "file ownership is not trusted: ${path}"
    mode_is_trusted "${mode}" || fail "file is group/world-writable: ${path}"
    [[ -z ${expected_mode} || 0${mode} == "${expected_mode}" ]] || \
        fail "file mode is ${mode}, expected ${expected_mode}: ${path}"
}

preflight_destination_ancestors() {
    local path
    for path in \
        "${BASE}" "${STATE_ROOT}" "${CACHE_ROOT}" \
        "${INSTALL_QA_ROOT}" "${LIBEXEC_ROOT}" "${LOCK_PATH%/*}" \
        "${ETC_PORTAGE%/*}"; do
        verify_existing_ancestor_chain "${path}"
    done
}

safe_mkdir() {
    local mode=$1 path=$2
    verify_existing_ancestor_chain "${path}"
    install -d -o "${EXPECTED_UID}" -g "${EXPECTED_GID}" -m "${mode}" -- "${path}"
}

sync_path() {
    sync -f -- "$1"
}

sync_tree() {
    local root=$1 entry
    while IFS= read -r -d '' entry; do
        [[ -L ${entry} ]] || sync_path "${entry}"
    done < <(find "${root}" -depth -print0)
    sync_path "${root%/*}"
}

reject_control_name() {
    local value=$1 label=$2
    [[ ${value} != *$'\n'* && ${value} != *$'\r'* && ${value} != *$'\t'* ]] || \
        fail "${label} contains a newline, carriage return, or tab"
}

emit_tree_inventory() {
    local tree=$1 prefix=$2 exclude_one=${3:-} exclude_two=${4:-}
    local entry relative type mode digest target
    while IFS= read -r -d '' entry; do
        relative=${entry#"${tree}/"}
        [[ ${relative} != "${entry}" ]] || continue
        [[ ${relative} != "${exclude_one}" && ${relative} != "${exclude_two}" ]] || continue
        reject_control_name "${relative}" 'relative path'
        if [[ -L ${entry} ]]; then
            target=$(readlink -- "${entry}")
            reject_control_name "${target}" 'symlink target'
            printf 'l\t-\t-\t%s/%s\t%s\n' "${prefix}" "${relative}" "${target}"
        elif [[ -d ${entry} ]]; then
            printf 'd\t0755\t-\t%s/%s\t-\n' "${prefix}" "${relative}"
        elif [[ -f ${entry} ]]; then
            if [[ -x ${entry} ]]; then mode=0755; else mode=0644; fi
            digest=$(sha256sum -- "${entry}"); digest=${digest%% *}
            printf 'f\t%s\t%s\t%s/%s\t-\n' "${mode}" "${digest}" "${prefix}" "${relative}"
        else
            fail "unsupported filesystem object in source tree: ${entry}"
        fi
    done < <(find "${tree}" -mindepth 1 -print0 | sort -z)
}

emit_source_inventory() {
    local source_root=$1 relative file mode digest
    emit_tree_inventory "${source_root}/portage" portage
    emit_tree_inventory "${source_root}/local-overlay" local-overlay
    for relative in "${INPUT_FILES[@]}"; do
        file=${source_root}/${relative}
        [[ -f ${file} && ! -L ${file} ]] || fail "source input is not a regular file: ${relative}"
        reject_control_name "${relative}" 'relative path'
        if [[ -x ${file} ]]; then mode=0755; else mode=0644; fi
        digest=$(sha256sum -- "${file}"); digest=${digest%% *}
        printf 'f\t%s\t%s\t%s\t-\n' "${mode}" "${digest}" "${relative}"
    done
}

source_identity() {
    local source_root=$1
    emit_source_inventory "${source_root}" | sha256sum | awk '{print $1}'
}

snapshot_inputs() {
    local before after snapshot_identity relative source_status_after commit_after
    SOURCE_STATUS=$(git -C "${ROOT}" status --porcelain=v1 --untracked-files=all | sha256sum | awk '{print $1}')
    GIT_DIRTY=clean
    git -C "${ROOT}" diff --quiet --ignore-submodules -- && \
        git -C "${ROOT}" diff --cached --quiet --ignore-submodules -- && \
        [[ -z $(git -C "${ROOT}" ls-files --others --exclude-standard) ]] || GIT_DIRTY=dirty
    GIT_COMMIT=$(git -C "${ROOT}" rev-parse --verify HEAD)
    before=$(source_identity "${ROOT}")
    SNAPSHOT=$(mktemp -d "${BASE}/.framework-source-snapshot.XXXXXXXX")
    mkdir -p -- "${SNAPSHOT}/scripts/optimization/bolt" \
        "${SNAPSHOT}/scripts/optimization/pgo"
    cp -a -- "${ROOT}/portage" "${SNAPSHOT}/portage"
    cp -a -- "${ROOT}/local-overlay" "${SNAPSHOT}/local-overlay"
    for relative in "${INPUT_FILES[@]}"; do
        install -m "$(if [[ -x ${ROOT}/${relative} ]]; then printf 0755; else printf 0644; fi)" \
            -T -- "${ROOT}/${relative}" "${SNAPSHOT}/${relative}"
    done
    after=$(source_identity "${ROOT}")
    snapshot_identity=$(source_identity "${SNAPSHOT}")
    source_status_after=$(git -C "${ROOT}" status --porcelain=v1 --untracked-files=all | sha256sum | awk '{print $1}')
    commit_after=$(git -C "${ROOT}" rev-parse --verify HEAD)
    [[ ${before} == "${after}" && ${before} == "${snapshot_identity}" ]] || \
        fail 'reviewed inputs changed while the immutable source snapshot was created'
    [[ ${SOURCE_STATUS} == "${source_status_after}" && ${GIT_COMMIT} == "${commit_after}" ]] || \
        fail 'Git commit/worktree identity changed while inputs were snapshotted'
    SOURCE_AGGREGATE=${snapshot_identity}
    INSTALLER_SHA256=$(sha256sum -- "${SNAPSHOT}/scripts/optimization/install-framework.sh")
    INSTALLER_SHA256=${INSTALLER_SHA256%% *}
    emit_source_inventory "${SNAPSHOT}" >"${SNAPSHOT}/source.inventory"
    chmod 0600 -- "${SNAPSHOT}/source.inventory"
}

verify_source_symlinks() {
    local entry relative target
    while IFS= read -r -d '' entry; do
        relative=${entry#"${SNAPSHOT}/"}
        target=$(readlink -- "${entry}")
        case ${relative} in
            portage/make.profile)
                [[ ${target} == ../../../../../var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm ]] || \
                    fail "unreviewed make.profile target: ${target}"
                ;;
            portage/postsync.d/50-eix-postsync)
                [[ ${target} == /usr/bin/eix-postsync ]] || fail "unreviewed postsync target: ${target}"
                ;;
            *)
                fail "unreviewed source symlink: ${relative} -> ${target}"
                ;;
        esac
    done < <(find "${SNAPSHOT}/portage" "${SNAPSHOT}/local-overlay" -type l -print0 | sort -z)
}

normalize_tree() {
    local tree=$1 entry
    find "${tree}" -type d -exec chmod 0755 -- {} +
    while IFS= read -r -d '' entry; do
        if [[ -x ${entry} ]]; then chmod 0755 -- "${entry}"; else chmod 0644 -- "${entry}"; fi
    done < <(find "${tree}" -type f -print0)
    chown -R "${EXPECTED_UID}:${EXPECTED_GID}" -- "${tree}"
}

render_manifest() {
    local candidate_inventory_sha=$1 current_target=$2 previous_target=$3 index source hash
    printf 'schema=gentoo-optimization-framework-install-v3\n'
    printf 'installer_sha256=%s\n' "${INSTALLER_SHA256}"
    printf 'source_aggregate_sha256=%s\n' "${SOURCE_AGGREGATE}"
    printf 'framework_aggregate_sha256=%s\n' "${FRAMEWORK_AGGREGATE}"
    printf 'candidate_inventory_sha256=%s\n' "${candidate_inventory_sha}"
    printf 'current_generation=%s\n' "${current_target}"
    printf 'previous_generation=%s\n' "${previous_target}"
    printf 'git_commit=%s\n' "${GIT_COMMIT}"
    printf 'git_worktree=%s\n' "${GIT_DIRTY}"
    printf 'git_status_sha256=%s\n' "${SOURCE_STATUS}"
    printf 'generated_policy=empty-v1\n'
    printf 'qa_hook_basename=%s\n' "${HOOK_BASENAME}"
    printf 'path\tsha256\tmode\towner\n'
    printf '%s\t%s\t0644\t%s:%s\n' \
        "${ETC_PORTAGE}/bashrc" \
        "$(sha256sum -- "${SNAPSHOT}/portage/bashrc" | awk '{print $1}')" \
        "${EXPECTED_UID}" "${EXPECTED_GID}"
    printf '%s\t%s\t0644\t%s:%s\n' \
        "${INSTALL_QA_ROOT}/${HOOK_BASENAME}" \
        "$(sha256sum -- "${SNAPSHOT}/portage/install-qa-check.d/${HOOK_BASENAME}" | awk '{print $1}')" \
        "${EXPECTED_UID}" "${EXPECTED_GID}"
    for index in "${!HELPER_RELATIVE[@]}"; do
        source=${SNAPSHOT}/${HELPER_SOURCE_RELATIVE[index]}
        hash=$(sha256sum -- "${source}"); hash=${hash%% *}
        printf '%s/%s\t%s\t0755\t%s:%s\n' \
            "${LIBEXEC_ROOT}" "${HELPER_RELATIVE[index]}" "${hash}" \
            "${EXPECTED_UID}" "${EXPECTED_GID}"
    done
}

candidate_inventory() {
    local candidate=$1 entry relative type mode digest target
    while IFS= read -r -d '' entry; do
        relative=${entry#"${candidate}/"}
        [[ ${relative} != "${entry}" ]] || continue
        [[ ${relative} != install.manifest && ${relative} != .candidate-inventory ]] || continue
        reject_control_name "${relative}" 'relative path'
        if [[ -L ${entry} ]]; then
            target=$(readlink -- "${entry}")
            reject_control_name "${target}" 'symlink target'
            printf 'l\t-\t-\tframework/%s\t%s\n' "${relative}" "${target}"
        elif [[ -d ${entry} ]]; then
            mode=$(stat -c %a -- "${entry}")
            printf 'd\t0%s\t-\tframework/%s\t-\n' "${mode}" "${relative}"
        elif [[ -f ${entry} ]]; then
            mode=$(stat -c %a -- "${entry}")
            digest=$(sha256sum -- "${entry}"); digest=${digest%% *}
            printf 'f\t0%s\t%s\tframework/%s\t-\n' "${mode}" "${digest}" "${relative}"
        else
            fail "unsupported filesystem object in candidate: ${entry}"
        fi
    done < <(find "${candidate}" -mindepth 1 -print0 | sort -z)
}

verify_inventory_exact() {
    local candidate=$1 expected=${candidate}/.candidate-inventory actual
    verify_regular_trusted "${expected}" 0600
    actual=$(mktemp "${BASE}/.candidate-inventory-check.XXXXXXXX")
    candidate_inventory "${candidate}" >"${actual}"
    cmp -s -- "${actual}" "${expected}" || {
        rm -f -- "${actual}"
        fail "immutable candidate entry set or content differs: ${candidate}"
    }
    rm -f -- "${actual}"
}

verify_make_profile() {
    local candidate=$1 profile=${candidate}/portage/make.profile expected actual probe
    [[ -L ${profile} ]] || fail 'candidate make.profile is not a symlink'
    [[ $(readlink -- "${profile}") == ../../../../../var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm ]] || \
        fail 'candidate make.profile has the wrong literal target'
    expected=$(physical /var/db/repos/gentoo/profiles/default/linux/amd64/23.0/llvm)
    actual=$(realpath -e -- "${profile}") || fail 'candidate make.profile target cannot be resolved'
    [[ ${actual} == "${expected}" ]] || fail "candidate make.profile resolves to ${actual}, expected ${expected}"
    probe=${actual}/make.defaults
    [[ -r ${probe} ]] || fail "profile make.defaults is not readable: ${probe}"
    if [[ -z ${TEST_ROOT} ]] && id -u portage >/dev/null 2>&1; then
        runuser -u portage -- test -r "${probe}" || \
            fail 'Portage user cannot read the selected profile'
        runuser -u portage -- test -r "${candidate}/portage/make.conf" || \
            fail 'Portage user cannot read the candidate configuration'
    else
        test -r "${probe}" && test -r "${candidate}/portage/make.conf" || \
            fail 'installer test user cannot read candidate configuration/profile'
    fi
}

verify_generated_policy() {
    local candidate=$1 directory=${candidate}/generated-policy
    [[ -d ${directory} && ! -L ${directory} ]] || fail 'generated-policy directory is absent'
    [[ $(find "${directory}" -mindepth 1 -maxdepth 1 -printf '%f\n') == .empty-v1 ]] || \
        fail 'initial generated-policy generation is not rigorously empty'
    verify_regular_trusted "${directory}/.empty-v1" 0600
    [[ $(<"${directory}/.empty-v1") == "no generated optimization policy is active" ]] || \
        fail 'generated-policy empty marker differs'
}

verify_candidate() {
    local candidate=$1 expected_manifest=${2:-} entry uid mode
    [[ -d ${candidate} && ! -L ${candidate} ]] || fail "candidate is not a directory: ${candidate}"
    [[ $(stat -c '%u:%g:%a' -- "${candidate}") == "${EXPECTED_UID}:${EXPECTED_GID}:755" ]] || \
        fail "candidate root ownership/mode differs: ${candidate}"
    verify_existing_ancestor_chain "${candidate}"
    while IFS= read -r -d '' entry; do
        [[ -L ${entry} ]] && continue
        uid=$(stat -c %u -- "${entry}")
        mode=$(stat -c %a -- "${entry}")
        [[ ${uid} == "${EXPECTED_UID}" ]] || fail "candidate entry has the wrong owner: ${entry}"
        mode_is_trusted "${mode}" || fail "candidate entry is group/world-writable: ${entry}"
    done < <(find "${candidate}" -mindepth 1 -print0)
    verify_inventory_exact "${candidate}"
    verify_regular_trusted "${candidate}/install.manifest" 0600
    [[ -z ${expected_manifest} ]] || cmp -s -- "${expected_manifest}" "${candidate}/install.manifest" || \
        fail 'candidate manifest is not the canonical expected manifest'
    verify_generated_policy "${candidate}"
    verify_make_profile "${candidate}"
}

get_previous_target() {
    local target
    if [[ -L ${FRAMEWORK_CURRENT} ]]; then
        target=$(readlink -- "${FRAMEWORK_CURRENT}")
        [[ ${target} == "${BASE}"/framework-[0-9a-f]* ]] || \
            fail "framework-current has an unmanaged target: ${target}"
        [[ -d ${target} && ! -L ${target} ]] || fail 'framework-current target is absent or symlinked'
        PREVIOUS_TARGET=${target}
    elif [[ -e ${FRAMEWORK_CURRENT} ]]; then
        fail 'framework-current exists but is not a managed symlink'
    fi
}

validate_legacy_migration() {
    local target
    if [[ -L ${ETC_PORTAGE} ]]; then
        target=$(readlink -- "${ETC_PORTAGE}")
        case ${target} in
            "${ROOT}/portage"|"${FRAMEWORK_CURRENT}/portage"|"${BASE}/portage-current") ;;
            "${BASE}"/portage-[0-9a-f]*/portage) ;;
            *) fail "/etc/portage points to an unmanaged migration source: ${target}" ;;
        esac
    elif [[ -e ${ETC_PORTAGE} ]]; then
        fail '/etc/portage is not a symlink from the reviewed migration allowlist'
    fi
    if [[ -e ${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt || \
          -L ${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt ]]; then
        [[ -L ${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt ]] || \
            fail 'legacy early BOLT QA hook is not the reviewed symlink'
        target=$(readlink -- "${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt")
        [[ ${target} == "${ROOT}/portage/install-qa-check.d/50-gentoo-optimization-bolt" ]] || \
            fail "legacy early BOLT QA hook has an unmanaged target: ${target}"
    fi
}

assert_global_qa_order() {
    local -a paths=()
    local repo portage_bin candidate_path name
    paths+=("${INSTALL_QA_ROOT}" "$(physical /usr/lib/install-qa-check.d)")
    if [[ -n ${TEST_ROOT} ]]; then
        paths+=("$(physical /var/db/repos/gentoo/metadata/install-qa-check.d)" \
            "$(physical /usr/lib/portage/install-qa-check.d)")
    else
        while IFS= read -r repo; do
            [[ -n ${repo} ]] || continue
            candidate_path=$(portageq get_repo_path / "${repo}" 2>/dev/null || true)
            [[ -n ${candidate_path} ]] && paths+=("${candidate_path}/metadata/install-qa-check.d")
        done < <(portageq get_repos / 2>/dev/null || true)
        paths+=(/usr/lib/portage/install-qa-check.d)
        portage_bin=$(portageq envvar PORTAGE_BIN_PATH 2>/dev/null || true)
        [[ -n ${portage_bin} ]] && paths+=("${portage_bin}/install-qa-check.d")
    fi
    paths+=("${CANDIDATE_STAGE:-${PREVIOUS_TARGET}}/local-overlay/metadata/install-qa-check.d")
    for candidate_path in "${paths[@]}"; do
        [[ -d ${candidate_path} ]] || continue
        while IFS= read -r -d '' name; do
            name=${name##*/}
            [[ ${name} == "${HOOK_BASENAME}" || ${name} < "${HOOK_BASENAME}" ]] || \
                fail "QA check sorts after ${HOOK_BASENAME}: ${candidate_path}/${name}"
        done < <(find "${candidate_path}" -maxdepth 1 -type f -print0 | sort -z)
    done
}

portage_quiescent() {
    local proc comm cmdline
    [[ -n ${TEST_ROOT} ]] && return 0
    for proc in /proc/[0-9]*; do
        [[ -r ${proc}/comm ]] || continue
        IFS= read -r comm <"${proc}/comm" || continue
        case ${comm} in
            emerge|ebuild|ebuild.sh|emaint|quickpkg)
                fail "Portage is active (${comm}, pid ${proc##*/})"
                ;;
        esac
        [[ -r ${proc}/cmdline ]] || continue
        cmdline=$(tr '\0' ' ' <"${proc}/cmdline" 2>/dev/null || true)
        case ${cmdline} in
            *'/usr/bin/emerge '*|*'/usr/bin/ebuild '*|*'/usr/bin/emaint '*|*'/usr/bin/quickpkg '*)
                fail "Portage command is active (pid ${proc##*/})"
                ;;
        esac
    done
}

hold_bolt_locks() {
    local entry fd
    [[ -d ${CACHE_ROOT}/locks ]] || return 0
    while IFS= read -r -d '' entry; do
        [[ -f ${entry} && ! -L ${entry} ]] || fail "BOLT lock namespace contains an unsafe entry: ${entry}"
        exec {fd}<>"${entry}"
        flock -n "${fd}" || fail "an active BOLT transaction holds ${entry}"
        HELD_BOLT_LOCK_FDS+=("${fd}")
    done < <(find "${CACHE_ROOT}/locks" -mindepth 1 -maxdepth 1 -print0 | sort -z)
}

failure_point() {
    local point=$1
    [[ -n ${TEST_ROOT} ]] || return 0
    if [[ ${GENTOO_OPT_INSTALLER_PAUSE_AT:-} == "${point}" ]]; then
        printf 'PAUSE: %s\n' "${point}" >&2
        while :; do sleep 1; done
    fi
    [[ ${GENTOO_OPT_INSTALLER_FAIL_AT:-} != "${point}" ]] || \
        fail "injected installer failure at ${point}"
}

publish_regular() {
    local source=$1 destination=$2 mode=$3 directory temporary
    directory=${destination%/*}
    safe_mkdir 0755 "${directory}"
    temporary=${destination}.partial.$$
    rm -f -- "${temporary}"
    install -o "${EXPECTED_UID}" -g "${EXPECTED_GID}" -m "${mode}" -T -- \
        "${source}" "${temporary}"
    sync_path "${temporary}"
    mv -fT -- "${temporary}" "${destination}"
    sync_path "${directory}"
}

backup_path() {
    local path=$1 label=$2
    [[ -e ${path} || -L ${path} ]] || return 0
    mkdir -p -- "${ROLLBACK_ROOT}"
    mv -T -- "${path}" "${ROLLBACK_ROOT}/${label}"
}

restore_path() {
    local path=$1 label=$2
    rm -rf -- "${path}"
    if [[ -e ${ROLLBACK_ROOT}/${label} || -L ${ROLLBACK_ROOT}/${label} ]]; then
        mkdir -p -- "${path%/*}"
        mv -T -- "${ROLLBACK_ROOT}/${label}" "${path}"
    fi
}

rollback_install() {
    ((ROLLBACK_REQUIRED)) || return 0
    set +e
    printf 'ROLLBACK: restoring the pre-install framework after an error or signal\n' >&2
    restore_path "${LIBEXEC_ROOT}/bolt" libexec-bolt
    restore_path "${LIBEXEC_ROOT}/pgo" libexec-pgo
    restore_path "${INSTALL_QA_ROOT}/${HOOK_BASENAME}" qa-hook
    restore_path "${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt" qa-hook-legacy
    restore_path "${MANIFEST}" manifest
    restore_path "${ETC_PORTAGE}" etc-portage
    if [[ -L ${FRAMEWORK_CURRENT} || -e ${FRAMEWORK_CURRENT} ]]; then rm -rf -- "${FRAMEWORK_CURRENT}"; fi
    if [[ ${PREVIOUS_TARGET} != none ]]; then
        ln -s -- "${PREVIOUS_TARGET}" "${FRAMEWORK_CURRENT}"
    fi
    if ((CREATED_CANDIDATE)) && [[ -n ${CANDIDATE_FINAL} ]]; then rm -rf -- "${CANDIDATE_FINAL}"; fi
    [[ -n ${CANDIDATE_STAGE} ]] && rm -rf -- "${CANDIDATE_STAGE}"
    ROLLBACK_REQUIRED=0
}

cleanup() {
    local status=$?
    if ((status != 0 || ! COMMITTED)); then rollback_install; fi
    if ((status != 0 || ! COMMITTED)) && ((CREATED_CANDIDATE)) && \
        [[ -n ${CANDIDATE_FINAL} ]]; then
        rm -rf -- "${CANDIDATE_FINAL}"
    fi
    [[ -n ${SNAPSHOT} ]] && rm -rf -- "${SNAPSHOT}"
    [[ -n ${CANDIDATE_STAGE} ]] && rm -rf -- "${CANDIDATE_STAGE}"
    [[ -n ${EXPECTED_MANIFEST:-} ]] && rm -f -- "${EXPECTED_MANIFEST}"
    [[ -n ${ROLLBACK_ROOT} ]] && rm -rf -- "${ROLLBACK_ROOT}"
    exit "${status}"
}

signal_exit() {
    printf 'ERROR: framework installer interrupted by signal %s\n' "$1" >&2
    exit 128
}

trap cleanup EXIT
trap 'signal_exit INT' INT
trap 'signal_exit TERM' TERM
trap 'signal_exit HUP' HUP

preflight_destination_ancestors
safe_mkdir 0755 "${LOCK_PATH%/*}"
exec {INSTALLER_LOCK_FD}>"${LOCK_PATH}"
flock -n "${INSTALLER_LOCK_FD}" || fail 'another framework installer holds the publication lock'
portage_quiescent
hold_bolt_locks
validate_legacy_migration
get_previous_target

if [[ ${MODE} == check ]]; then
    [[ -L ${FRAMEWORK_CURRENT} ]] || fail 'framework-current is absent'
    ACTIVE_TARGET=$(readlink -- "${FRAMEWORK_CURRENT}")
    [[ ${ACTIVE_TARGET} == "${BASE}"/framework-[0-9a-f]* ]] || fail 'framework-current target is unmanaged'
    [[ -L ${ETC_PORTAGE} && $(readlink -- "${ETC_PORTAGE}") == "${FRAMEWORK_CURRENT}/portage" ]] || \
        fail '/etc/portage is not atomically bound to framework-current/portage'
    snapshot_inputs
    verify_source_symlinks
    verify_candidate "${ACTIVE_TARGET}"
    ACTIVE_PREVIOUS=$(awk -F= '$1 == "previous_generation" { print substr($0, index($0, "=") + 1) }' \
        "${ACTIVE_TARGET}/install.manifest")
    [[ -n ${ACTIVE_PREVIOUS} && $(grep -c '^previous_generation=' \
        "${ACTIVE_TARGET}/install.manifest") == 1 ]] || fail 'active manifest previous generation is invalid'
    FRAMEWORK_AGGREGATE=$(printf '%s\n' \
        'gentoo-optimization-framework-v3' \
        "installer=${INSTALLER_SHA256}" \
        "source=${SOURCE_AGGREGATE}" \
        "git_commit=${GIT_COMMIT}" \
        "git_worktree=${GIT_DIRTY}" \
        "git_status=${SOURCE_STATUS}" \
        "previous=${ACTIVE_PREVIOUS}" | sha256sum | awk '{print $1}')
    [[ ${ACTIVE_TARGET} == "${BASE}/framework-${FRAMEWORK_AGGREGATE}" ]] || \
        fail 'active generation identity does not match the reviewed input snapshot'
    CANDIDATE_INVENTORY_SHA=$(sha256sum -- "${ACTIVE_TARGET}/.candidate-inventory" | awk '{print $1}')
    EXPECTED_CHECK_MANIFEST=$(mktemp "${BASE}/.framework-check-manifest.XXXXXXXX")
    render_manifest "${CANDIDATE_INVENTORY_SHA}" "${ACTIVE_TARGET}" "${ACTIVE_PREVIOUS}" \
        >"${EXPECTED_CHECK_MANIFEST}"
    cmp -s -- "${EXPECTED_CHECK_MANIFEST}" "${ACTIVE_TARGET}/install.manifest" || \
        fail 'active generation manifest is not the strict canonical manifest for this checkout'
    cmp -s -- "${ACTIVE_TARGET}/install.manifest" "${MANIFEST}" || \
        fail 'canonical external manifest differs from the active generation manifest'
    rm -f -- "${EXPECTED_CHECK_MANIFEST}"
    assert_global_qa_order
    verify_regular_trusted "${INSTALL_QA_ROOT}/${HOOK_BASENAME}" 0644
    cmp -s -- "${ACTIVE_TARGET}/qa/${HOOK_BASENAME}" "${INSTALL_QA_ROOT}/${HOOK_BASENAME}" || \
        fail 'installed QA hook differs from the active generation'
    for index in "${!HELPER_RELATIVE[@]}"; do
        verify_regular_trusted "${LIBEXEC_ROOT}/${HELPER_RELATIVE[index]}" 0755
        cmp -s -- "${ACTIVE_TARGET}/libexec/${HELPER_RELATIVE[index]}" \
            "${LIBEXEC_ROOT}/${HELPER_RELATIVE[index]}" || \
            fail "installed helper differs: ${HELPER_RELATIVE[index]}"
    done
    mapfile -t ACTUAL_BOLT_HELPERS < <(find "${LIBEXEC_ROOT}/bolt" -mindepth 1 -maxdepth 1 \
        -printf '%f\n' | sort)
    mapfile -t ACTUAL_PGO_HELPERS < <(find "${LIBEXEC_ROOT}/pgo" -mindepth 1 -maxdepth 1 \
        -printf '%f\n' | sort)
    [[ ${ACTUAL_BOLT_HELPERS[*]} == $'artifact_tool.py\ncapture-input.sh\ndeploy-output.sh\nregister-output.sh' ]] || \
        fail 'installed BOLT helper entry set differs'
    [[ ${ACTUAL_PGO_HELPERS[*]} == $'profile-identity.py\nvalidate-profile.py' ]] || \
        fail 'installed PGO helper entry set differs'
    [[ ! -e ${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt && \
        ! -L ${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt ]] || \
        fail 'obsolete early BOLT QA hook remains installed'
    COMMITTED=1
    printf 'PASS: root-owned Phase 2 framework check verified (%s)\n' "${MANIFEST}"
    exit 0
fi

safe_mkdir 0755 "${BASE}"
safe_mkdir 0700 "${STATE_ROOT}"
safe_mkdir 0700 "${CACHE_ROOT}"
for cache_dir in inputs outputs perf fdata diagnostics locks; do
    safe_mkdir 0700 "${CACHE_ROOT}/${cache_dir}"
done
snapshot_inputs
verify_source_symlinks

FRAMEWORK_AGGREGATE=$(printf '%s\n' \
    'gentoo-optimization-framework-v3' \
    "installer=${INSTALLER_SHA256}" \
    "source=${SOURCE_AGGREGATE}" \
    "git_commit=${GIT_COMMIT}" \
    "git_worktree=${GIT_DIRTY}" \
    "git_status=${SOURCE_STATUS}" \
    "previous=${PREVIOUS_TARGET}" | sha256sum | awk '{print $1}')
CANDIDATE_FINAL=${BASE}/framework-${FRAMEWORK_AGGREGATE}
CANDIDATE_STAGE=${CANDIDATE_FINAL}.partial.$$
EXPECTED_MANIFEST=${BASE}/.framework-expected-manifest.$$

if [[ ${PREVIOUS_TARGET} != none && -f ${PREVIOUS_TARGET}/install.manifest ]] && \
    grep -Fxq "installer_sha256=${INSTALLER_SHA256}" "${PREVIOUS_TARGET}/install.manifest" && \
    grep -Fxq "source_aggregate_sha256=${SOURCE_AGGREGATE}" "${PREVIOUS_TARGET}/install.manifest" && \
    grep -Fxq "git_commit=${GIT_COMMIT}" "${PREVIOUS_TARGET}/install.manifest" && \
    grep -Fxq "git_status_sha256=${SOURCE_STATUS}" "${PREVIOUS_TARGET}/install.manifest"; then
    printf 'INFO: reviewed inputs already match the active generation; running strict check\n'
    COMMITTED=1
    trap - EXIT INT TERM HUP
    rm -rf -- "${SNAPSHOT}"
    if [[ -n ${TEST_ROOT} ]]; then
        exec env -u GENTOO_OPT_INSTALLER_FAIL_AT -u GENTOO_OPT_INSTALLER_PAUSE_AT \
            "${ROOT}/scripts/optimization/install-framework.sh" --check --test-root "${TEST_ROOT}"
    else
        exec env -u GENTOO_OPT_INSTALLER_FAIL_AT -u GENTOO_OPT_INSTALLER_PAUSE_AT \
            "${ROOT}/scripts/optimization/install-framework.sh" --check
    fi
fi

rm -rf -- "${CANDIDATE_STAGE}"
mkdir -p -- "${CANDIDATE_STAGE}/portage" "${CANDIDATE_STAGE}/local-overlay" \
    "${CANDIDATE_STAGE}/generated-policy" "${CANDIDATE_STAGE}/libexec/bolt" \
    "${CANDIDATE_STAGE}/libexec/pgo" "${CANDIDATE_STAGE}/qa"
cp -a -- "${SNAPSHOT}/portage/." "${CANDIDATE_STAGE}/portage/"
cp -a -- "${SNAPSHOT}/local-overlay/." "${CANDIDATE_STAGE}/local-overlay/"
for index in "${!HELPER_RELATIVE[@]}"; do
    install -m 0755 -T -- "${SNAPSHOT}/${HELPER_SOURCE_RELATIVE[index]}" \
        "${CANDIDATE_STAGE}/libexec/${HELPER_RELATIVE[index]}"
done
install -m 0644 -T -- "${SNAPSHOT}/portage/install-qa-check.d/${HOOK_BASENAME}" \
    "${CANDIDATE_STAGE}/qa/${HOOK_BASENAME}"
printf '%s\n' "${SOURCE_AGGREGATE}" >"${CANDIDATE_STAGE}/portage/.gentoo-optimization-source-hash"
printf '%s\n' "${SOURCE_AGGREGATE}" >"${CANDIDATE_STAGE}/local-overlay/.gentoo-optimization-source-hash"
printf '%s\n' 'no generated optimization policy is active' >"${CANDIDATE_STAGE}/generated-policy/.empty-v1"
normalize_tree "${CANDIDATE_STAGE}"
chmod 0600 -- "${CANDIDATE_STAGE}/portage/.gentoo-optimization-source-hash" \
    "${CANDIDATE_STAGE}/local-overlay/.gentoo-optimization-source-hash" \
    "${CANDIDATE_STAGE}/generated-policy/.empty-v1"
candidate_inventory "${CANDIDATE_STAGE}" >"${CANDIDATE_STAGE}/.candidate-inventory"
chmod 0600 -- "${CANDIDATE_STAGE}/.candidate-inventory"
CANDIDATE_INVENTORY_SHA=$(sha256sum -- "${CANDIDATE_STAGE}/.candidate-inventory" | awk '{print $1}')
render_manifest "${CANDIDATE_INVENTORY_SHA}" "${CANDIDATE_FINAL}" "${PREVIOUS_TARGET}" \
    >"${CANDIDATE_STAGE}/install.manifest"
chmod 0600 -- "${CANDIDATE_STAGE}/install.manifest"
chown -R "${EXPECTED_UID}:${EXPECTED_GID}" -- "${CANDIDATE_STAGE}"
render_manifest "${CANDIDATE_INVENTORY_SHA}" "${CANDIDATE_FINAL}" "${PREVIOUS_TARGET}" \
    >"${EXPECTED_MANIFEST}"
verify_candidate "${CANDIDATE_STAGE}" "${EXPECTED_MANIFEST}"
assert_global_qa_order
sync_tree "${CANDIDATE_STAGE}"
if [[ -e ${CANDIDATE_FINAL} || -L ${CANDIDATE_FINAL} ]]; then
    verify_candidate "${CANDIDATE_FINAL}" "${EXPECTED_MANIFEST}"
    rm -rf -- "${CANDIDATE_STAGE}"
    CANDIDATE_STAGE=
else
    mv -T -- "${CANDIDATE_STAGE}" "${CANDIDATE_FINAL}"
    CANDIDATE_STAGE=
    CREATED_CANDIDATE=1
fi
sync_path "${BASE}"
verify_candidate "${CANDIDATE_FINAL}" "${EXPECTED_MANIFEST}"
failure_point after-candidate

ROLLBACK_ROOT=$(mktemp -d "${BASE}/.framework-rollback.XXXXXXXX")
ROLLBACK_REQUIRED=1
backup_path "${LIBEXEC_ROOT}/bolt" libexec-bolt
backup_path "${LIBEXEC_ROOT}/pgo" libexec-pgo
backup_path "${INSTALL_QA_ROOT}/${HOOK_BASENAME}" qa-hook
backup_path "${INSTALL_QA_ROOT}/50-gentoo-optimization-bolt" qa-hook-legacy
backup_path "${MANIFEST}" manifest
backup_path "${ETC_PORTAGE}" etc-portage
safe_mkdir 0755 "${ETC_PORTAGE%/*}"
safe_mkdir 0755 "${LIBEXEC_ROOT}"
cp -a -- "${CANDIDATE_FINAL}/libexec/bolt" "${LIBEXEC_ROOT}/bolt"
cp -a -- "${CANDIDATE_FINAL}/libexec/pgo" "${LIBEXEC_ROOT}/pgo"
chown -R "${EXPECTED_UID}:${EXPECTED_GID}" -- "${LIBEXEC_ROOT}/bolt" "${LIBEXEC_ROOT}/pgo"
find "${LIBEXEC_ROOT}/bolt" "${LIBEXEC_ROOT}/pgo" -type d -exec chmod 0755 -- {} +
find "${LIBEXEC_ROOT}/bolt" "${LIBEXEC_ROOT}/pgo" -type f -exec chmod 0755 -- {} +
publish_regular "${CANDIDATE_FINAL}/qa/${HOOK_BASENAME}" \
    "${INSTALL_QA_ROOT}/${HOOK_BASENAME}" 0644
publish_regular "${CANDIDATE_FINAL}/install.manifest" "${MANIFEST}" 0600
ln -s -- "${FRAMEWORK_CURRENT}/portage" "${ETC_PORTAGE}"
sync_tree "${LIBEXEC_ROOT}/bolt"
sync_tree "${LIBEXEC_ROOT}/pgo"
sync_path "${INSTALL_QA_ROOT}"
sync_path "${STATE_ROOT}"
sync_path "${ETC_PORTAGE%/*}"
failure_point after-helpers

# Recheck quiescence immediately before the only activation point.  Existing
# transaction locks are still held from the first check.
portage_quiescent
assert_global_qa_order
failure_point before-activation
CURRENT_TMP=${FRAMEWORK_CURRENT}.partial.$$
ln -s -- "${CANDIDATE_FINAL}" "${CURRENT_TMP}"
mv -fT -- "${CURRENT_TMP}" "${FRAMEWORK_CURRENT}"
sync_path "${BASE}"
failure_point after-activation

[[ $(readlink -- "${FRAMEWORK_CURRENT}") == "${CANDIDATE_FINAL}" ]] || \
    fail 'framework-current activation target differs'
verify_candidate "${CANDIDATE_FINAL}" "${MANIFEST}"
ROLLBACK_REQUIRED=0
COMMITTED=1
rm -rf -- "${ROLLBACK_ROOT}"
ROLLBACK_ROOT=
rm -f -- "${EXPECTED_MANIFEST}"
printf 'PASS: root-owned Phase 2 framework install verified (%s)\n' "${MANIFEST}"

#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
PORTAGE=${ROOT}/portage
MASK=${PORTAGE}/package.mask/99-block-new-live-from-double-star

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
[[ -f ${MASK} && ! -L ${MASK} ]] || fail 'reviewed package.mask file is absent or symlinked'
[[ $(stat -c %a -- "${MASK}") == 644 ]] || fail 'reviewed package.mask is not mode 0644'

o3_definitions=$(rg -n '^O3_BASE_FLAGS=' "${PORTAGE}" || true)
[[ $(wc -l <<<"${o3_definitions}") -eq 1 &&
    ${o3_definitions} == "${PORTAGE}/make.conf:"* ]] || \
    fail 'O3_BASE_FLAGS is not defined exactly once in make.conf'
for environment in O3.conf O3-no-vtables.conf O3-no-vtables-no-forced-libs.conf \
    O3-thin-lto-no-libs.conf; do
    FILE=${PORTAGE}/env/${environment} bash -c '
        source "${0}/make.conf"
        source "${FILE}"
        [[ ${COMMON_FLAGS} == *-fno-signed-zeros* &&
           ${COMMON_FLAGS} == *-Wno-deprecated* ]]
    ' "${PORTAGE}" || fail "${environment} did not inherit the shared strict-FP O3 baseline"
done

for forbidden in \
    '=dev-util/vulkan-tools-9999' '=media-libs/vulkan-layers-9999' \
    '=dev-util/spirv-tools-9999' '=dev-util/glslang-9999' \
    '=dev-vcs/git-9999-r3' '=net-libs/libssh2-9999' \
    '=dev-lang/python-0.3.14.9999' '=dev-lang/python-0.3.15.9999'; do
    ! grep -Fxq -- "${forbidden}" "${MASK}" || fail "redundant/suspicious mask remains: ${forbidden}"
done
if rg -q '^=.*-23[.]0[.]0[.]9999$' "${MASK}"; then
    fail 'redundant exact LLVM-23 live masks remain beside the reviewed broad masks'
fi
for atom in '=dev-util/pahole-9999*' '=media-libs/babl-9999*' \
    '=media-libs/gegl-9999*' '=dev-vcs/git-9999*' '=net-libs/libssh2-9999*'; do
    [[ $(grep -Fxc -- "${atom}" "${MASK}") -eq 1 ]] || \
        fail "reviewed wildcard mask is absent or duplicated: ${atom}"
done

# Keep accidental shell redirections and local bytecode out of the reviewed
# repository surface.  The explicit development-directory exceptions are
# directories only; an unexpected top-level file (such as the former `trap`
# scratch output) is always fatal.
declare -A allowed_root_entries=(
    [.agents]=1 [.codex]=1 [.git]=1 [.github]=1 [.gitignore]=1 [.mypy_cache]=1 [.vscode]=1
    [LICENSE]=1 [README.md]=1 [bench]=1 [docs]=1 [local-overlay]=1
    [optimization]=1 [plan.md]=1 [plans]=1 [portage]=1 [scripts]=1 [tests]=1
)
while IFS= read -r -d '' entry; do
    name=${entry#"${ROOT}"/}
    [[ -n ${allowed_root_entries[${name}]+x} ]] || \
        fail "unexpected top-level repository residue: ${name}"
    case ${name} in
        .gitignore|LICENSE|README.md|plan.md)
            [[ ! -d ${entry} ]] || fail "expected top-level file is a directory: ${name}"
            ;;
        *)
            [[ -d ${entry} && ! -L ${entry} ]] || \
                fail "expected top-level directory is not a real directory: ${name}"
            ;;
    esac
done < <(find "${ROOT}" -mindepth 1 -maxdepth 1 -print0)
if find "${ROOT}" -path "${ROOT}/.git" -prune -o \
    \( -type d -name __pycache__ -o -type f -name '*.pyc' \) -print -quit | \
    grep -q .; then
    fail 'repository contains Python bytecode residue'
fi

for helper in production-profile-lock-transaction.py authorization-token-scan.py; do
    runtime=${ROOT}/scripts/optimization/pgo/${helper}
    [[ -f ${runtime} && ! -L ${runtime} && -x ${runtime} ]] || \
        fail "production PGO helper is absent, symlinked, or non-executable: ${helper}"
done
for residue in production_profile_lock_transaction.py authorization_token_scan.py; do
    [[ ! -e ${ROOT}/tests/optimization/${residue} &&
       ! -L ${ROOT}/tests/optimization/${residue} ]] || \
        fail "production runtime remains misplaced below tests/: ${residue}"
done

printf 'PASS: package masks and shared O3 baseline match reviewed cleanup policy\n'

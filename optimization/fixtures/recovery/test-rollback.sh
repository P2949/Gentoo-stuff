#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../../.." && pwd -P)
ROLLBACK=${REPOSITORY_ROOT}/scripts/optimization/recovery/rollback.sh
CHECKPOINT=${REPOSITORY_ROOT}/scripts/optimization/recovery/create-binpkg-checkpoint.sh
EXACT_CPV_CONTRACT=${REPOSITORY_ROOT}/optimization/exact-cpv-contract.json
FIXTURE=$(mktemp -d)
trap 'rm -rf -- "${FIXTURE}"' EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_file() {
    [[ -f $1 ]] || fail "expected file: $1"
}

assert_absent() {
    [[ ! -e $1 ]] || fail "expected absent path: $1"
}

assert_contains() {
    local needle=$1
    local file=$2
    grep -Fq -- "${needle}" "${file}" || fail "expected '${needle}' in ${file}"
}

assert_checkpoint_exact_cpv_contract() {
    local cpv output
    local -a valid_cpvs=() invalid_cpvs=()
    mapfile -d '' -t valid_cpvs < <(
        jq -j '.valid_cpvs[] | ., "\u0000"' "${EXACT_CPV_CONTRACT}"
    )
    mapfile -d '' -t invalid_cpvs < <(
        jq -j '.invalid_cpvs[] | ., "\u0000"' "${EXACT_CPV_CONTRACT}"
    )
    ((${#valid_cpvs[@]} > 0 && ${#invalid_cpvs[@]} > 0)) || \
        fail 'exact CPV contract corpus is empty'
    for cpv in "${valid_cpvs[@]}"; do
        if output=$("${CHECKPOINT}" \
            --expected-source-target /fixture/source \
            --expected-source-packages-sha256 \
            0000000000000000000000000000000000000000000000000000000000000000 \
            --expected-verifier-sha256 \
            0000000000000000000000000000000000000000000000000000000000000000 \
            --fixture-mode cpv-contract "=${cpv}" 2>&1); then
            fail "checkpoint unexpectedly ran past its intentionally incomplete fixture for valid CPV: ${cpv}"
        fi
        [[ ${output} == *'fixture mode requires --fixture-root, --fixture-owner, and --tool-root'* ]] || \
            fail "checkpoint rejected contract-valid CPV: ${cpv}: ${output}"
    done
    for cpv in "${invalid_cpvs[@]}"; do
        if output=$("${CHECKPOINT}" \
            --expected-source-target /fixture/source \
            --expected-source-packages-sha256 \
            0000000000000000000000000000000000000000000000000000000000000000 \
            --expected-verifier-sha256 \
            0000000000000000000000000000000000000000000000000000000000000000 \
            --fixture-mode cpv-contract "=${cpv}" 2>&1); then
            fail "checkpoint accepted contract-invalid CPV: ${cpv}"
        fi
        [[ ${output} == *'non-exact or unsafe quickpkg atom:'* ]] || \
            fail "checkpoint rejected contract-invalid CPV for an unrelated reason: ${cpv}: ${output}"
    done
    printf 'PASS: checkpoint exact CPV contract corpus\n'
}

assert_checkpoint_exact_cpv_contract

compile_recovery_cpp_lane() {
    local lane=$1
    local output=$2
    local required_library=$3
    local forbidden_library=$4
    local off=${ROOT}/etc/portage/env/recovery/optimization-off.conf
    local lane_file=${ROOT}/etc/portage/env/recovery/${lane}.conf
    local compiler compiler_version
    (
        local -a compile_flags=()
        local -a link_flags=()
        # shellcheck disable=SC1090
        source "${off}"
        # shellcheck disable=SC1090
        source "${lane_file}"
        IFS=' ' read -r -a compile_flags <<<"${CXXFLAGS}"
        IFS=' ' read -r -a link_flags <<<"${LDFLAGS}"
        "${CXX}" "${compile_flags[@]}" "${RECOVERY_CPP_SOURCE}" "${link_flags[@]}" -o "${output}"
    )
    [[ $("${output}") == 'recovery-cxx-ok' ]] || fail "${lane} C++ recovery binary did not execute correctly"
    readelf -d -- "${output}" | grep -Fq -- "${required_library}" || fail "${lane} binary did not link ${required_library}"
    if readelf -d -- "${output}" | grep -Fq -- "${forbidden_library}"; then
        fail "${lane} binary unexpectedly linked ${forbidden_library}"
    fi
    compiler=$(
        # shellcheck disable=SC1090
        source "${off}"
        # shellcheck disable=SC1090
        source "${lane_file}"
        printf '%s' "${CXX}"
    )
    compiler_version=$("${compiler}" --version)
    compiler_version=${compiler_version%%$'\n'*}
    printf 'PASS: recovery C++ lane=%s compiler=%s required=%s forbidden=%s output=%s\n' \
        "${lane}" "${compiler_version}" "${required_library}" "${forbidden_library}" "${output}"
    readelf -d -- "${output}" | grep -F -- "${required_library}"
}

ROOT=${FIXTURE}/root
STATE=${FIXTURE}/state
CACHE=${FIXTURE}/cache
TOOLS=${ROOT}/tools
CRITICAL=${CACHE}/binpkgs/critical-20260710
PACKAGE_ENV=${ROOT}/etc/portage/package.env
ARCHIVE_REL=app-misc/demo-1.0.gpkg.tar
ARCHIVE=${CRITICAL}/${ARCHIVE_REL}

mkdir -p -- \
    "${PACKAGE_ENV}" \
    "${ROOT}/etc/portage/env" \
    "${TOOLS}" \
    "${CRITICAL}/app-misc" \
    "${CACHE}/binpkgs" \
    "${STATE}"

cat >"${PACKAGE_ENV}/50-global-pgo" <<'EOF'
*/* pgo-use-if-available.conf
EOF
cat >"${PACKAGE_ENV}/30-unrelated" <<'EOF'
dev-libs/example no-lto.conf
EOF
printf 'fixture protected binary package\n' >"${ARCHIVE}"
archive_size=$(stat -c '%s' -- "${ARCHIVE}")
archive_md5=$(md5sum -- "${ARCHIVE}")
archive_md5=${archive_md5%% *}
cat >"${CRITICAL}/Packages" <<EOF
VERSION: 0
PACKAGES: 1
TIMESTAMP: 1783700000

CPV: app-misc/demo-1.0
PATH: ${ARCHIVE_REL}
SIZE: ${archive_size}
MD5: ${archive_md5}
EOF
chmod 0755 "${CRITICAL}"
chmod 0644 "${CRITICAL}/Packages" "${ARCHIVE}"
ln -s critical-20260710 "${CACHE}/binpkgs/critical-current"

export RECOVERY_FIXTURE_DIR=${FIXTURE}

cat >"${TOOLS}/emerge" <<'EOF'
#!/usr/bin/env bash
set -eu
{
    printf 'CALL\n'
    printf 'ROOT=%s\n' "${ROOT-}"
    printf 'PORTAGE_CONFIGROOT=%s\n' "${PORTAGE_CONFIGROOT-}"
    printf 'PKGDIR=%s\n' "${PKGDIR-}"
    printf 'PORTAGE_BINHOST=%s\n' "${PORTAGE_BINHOST-}"
    printf 'FETCHCOMMAND=%s\n' "${FETCHCOMMAND-}"
    printf 'PGO_DISABLE_USE=%s\n' "${PGO_DISABLE_USE-}"
    printf 'BOLT_DEPLOY=%s\n' "${BOLT_DEPLOY-}"
    printf 'CFLAGS=%s\n' "${CFLAGS-}"
    printf 'ARGS'
    printf ' <%s>' "$@"
    printf '\n'
} >>"${RECOVERY_FIXTURE_DIR}/emerge.log"
EOF

chmod 0755 "${TOOLS}/emerge"

common=(
    --fixture-mode
    --root "${ROOT}"
    --state-root "${STATE}"
    --cache-root "${CACHE}"
    --tool-root "${TOOLS}"
)

assert_rollback_exact_cpv_contract() {
    local cpv output
    local -a valid_cpvs=() invalid_cpvs=()
    mapfile -d '' -t valid_cpvs < <(
        jq -j '.valid_cpvs[] | ., "\u0000"' "${EXACT_CPV_CONTRACT}"
    )
    mapfile -d '' -t invalid_cpvs < <(
        jq -j '.invalid_cpvs[] | ., "\u0000"' "${EXACT_CPV_CONTRACT}"
    )
    for cpv in "${valid_cpvs[@]}"; do
        if output=$("${ROLLBACK}" "${common[@]}" --dry-run restore "=${cpv}" 2>&1); then
            fail "rollback unexpectedly found a protected archive for contract-valid CPV: ${cpv}"
        fi
        [[ ${output} == *"expected exactly one protected binpkg record for ${cpv}; found 0"* ]] || \
            fail "rollback rejected contract-valid CPV before archive lookup: ${cpv}: ${output}"
    done
    for cpv in "${invalid_cpvs[@]}"; do
        if output=$("${ROLLBACK}" "${common[@]}" --dry-run restore "=${cpv}" 2>&1); then
            fail "rollback accepted contract-invalid CPV: ${cpv}"
        fi
        if [[ ${cpv} == *[[:space:]]* ]]; then
            [[ ${output} == *'restore atom contains whitespace:'* ]] || \
                fail "rollback rejected whitespace-bearing contract-invalid CPV for an unrelated reason: ${cpv}: ${output}"
        else
            [[ ${output} == *'invalid exact Gentoo CPV in restore atom:'* ]] || \
                fail "rollback rejected contract-invalid CPV for an unrelated reason: ${cpv}: ${output}"
        fi
    done
    printf 'PASS: rollback exact CPV contract corpus\n'
}

assert_rollback_exact_cpv_contract

"${ROLLBACK}" "${common[@]}" check

"${ROLLBACK}" "${common[@]}" --dry-run disable
assert_file "${PACKAGE_ENV}/50-global-pgo"
assert_absent "${STATE}/recovery/optimization.disabled"

"${ROLLBACK}" "${common[@]}" disable
assert_absent "${PACKAGE_ENV}/50-global-pgo"
assert_file "${PACKAGE_ENV}/30-unrelated"
assert_file "${PACKAGE_ENV}/99-recovery-optimization-off"
assert_file "${ROOT}/etc/portage/env/recovery/optimization-off.conf"
assert_file "${ROOT}/etc/portage/env/recovery/clang-libcxx.conf"
assert_file "${ROOT}/etc/portage/env/recovery/gcc.conf"
assert_file "${STATE}/recovery/optimization.disabled"
find "${ROOT}/etc/portage/package.env.recovery-disabled" -type f -name 50-global-pgo -print -quit | grep -q . || fail 'legacy assignment was not quarantined'
assert_contains '*/* recovery/optimization-off.conf recovery/clang-libcxx.conf' "${PACKAGE_ENV}/99-recovery-optimization-off"
assert_contains 'sys-devel/gcc recovery/gcc.conf' "${PACKAGE_ENV}/99-recovery-optimization-off"
# The generated file must retain the literal Portage-time expansion.
# shellcheck disable=SC2016
assert_contains 'CXXFLAGS="${COMMON_FLAGS} -stdlib=libc++"' "${ROOT}/etc/portage/env/recovery/clang-libcxx.conf"
assert_contains 'LDFLAGS="-fuse-ld=lld -rtlib=compiler-rt -unwindlib=libunwind -stdlib=libc++"' "${ROOT}/etc/portage/env/recovery/clang-libcxx.conf"
assert_contains 'CXX="g++"' "${ROOT}/etc/portage/env/recovery/gcc.conf"

RECOVERY_CPP_SOURCE=${FIXTURE}/recovery-lanes.cpp
cat >"${RECOVERY_CPP_SOURCE}" <<'EOF'
#include <iostream>
#include <string>

int main() {
    const std::string result = "recovery-cxx-ok";
    std::cout << result << '\n';
    return result.size() == 15 ? 0 : 1;
}
EOF
compile_recovery_cpp_lane clang-libcxx "${FIXTURE}/recovery-clang-libcxx" 'libc++.so' 'libstdc++.so'
compile_recovery_cpp_lane gcc "${FIXTURE}/recovery-gcc-libstdcxx" 'libstdc++.so' 'libc++.so'

"${ROLLBACK}" "${common[@]}" restore '=app-misc/demo-1.0'
assert_contains "PKGDIR=${CRITICAL}" "${FIXTURE}/emerge.log"
assert_contains 'PORTAGE_BINHOST=' "${FIXTURE}/emerge.log"
assert_contains 'FETCHCOMMAND=/bin/false' "${FIXTURE}/emerge.log"
assert_contains 'ARGS <--ignore-default-opts> <--ask=n> <--autounmask=n> <--buildpkg=n> <--getbinpkg=n> <--usepkgonly>' "${FIXTURE}/emerge.log"
assert_contains '<=app-misc/demo-1.0>' "${FIXTURE}/emerge.log"

calls_before=$(grep -c '^CALL$' "${FIXTURE}/emerge.log")
if "${ROLLBACK}" "${common[@]}" restore '=app-misc/missing-1.0'; then
    fail 'missing exact protected atom unexpectedly restored'
fi
calls_after=$(grep -c '^CALL$' "${FIXTURE}/emerge.log")
[[ ${calls_before} == "${calls_after}" ]] || fail 'emerge ran before missing archive validation failed'

"${ROLLBACK}" "${common[@]}" restore-critical
"${ROLLBACK}" "${common[@]}" preserved-rebuild
assert_contains 'ARGS <--ignore-default-opts> <--ask=n> <--autounmask=n> <--buildpkg=n> <--getbinpkg=n> <--usepkgonly> <--binpkg-changed-deps=n> <--binpkg-respect-use=n> <--oneshot> <--verbose> <@preserved-rebuild>' "${FIXTURE}/emerge.log"
assert_contains '<@preserved-rebuild>' "${FIXTURE}/emerge.log"

# The aggregate remains a package/config rollback only.
"${ROLLBACK}" "${common[@]}" --dry-run all >"${FIXTURE}/package-only-all.log"
assert_contains 'command=all dry_run=1' "${FIXTURE}/package-only-all.log"

"${ROLLBACK}" "${common[@]}" check

find "${STATE}/reports/recovery" -type f -name 'rollback-*.log' -print -quit | grep -q . || fail 'timestamped recovery log was not created'

printf 'PASS: rollback fixture suite\n'

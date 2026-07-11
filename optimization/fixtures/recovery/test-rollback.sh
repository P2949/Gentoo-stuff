#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../../.." && pwd -P)
ROLLBACK=${REPOSITORY_ROOT}/scripts/optimization/recovery/rollback.sh
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
ESP=${ROOT}/efi
CRITICAL=${CACHE}/binpkgs/critical-20260710
PACKAGE_ENV=${ROOT}/etc/portage/package.env
AUTHORITATIVE_DIR=${ESP}/EFI/Gentoo/recovery/pgo-known-good-fixture
KNOWN_KERNEL=${AUTHORITATIVE_DIR}/vmlinuz-7.1.2-cachyos2.efi
KNOWN_INITRAMFS=${AUTHORITATIVE_DIR}/initramfs-7.1.2-cachyos2.img
LEGACY_KERNEL=${ESP}/EFI/Gentoo/vmlinuz-7.1.2-cachyos2-old.efi
LEGACY_INITRAMFS=${ESP}/EFI/Gentoo/initramfs-7.1.2-cachyos2.img.old
AUTHORITATIVE_MANIFEST=${STATE}/recovery/authoritative-known-good.manifest
CURRENT_KERNEL=${ESP}/EFI/Gentoo/vmlinuz-7.1.2-cachyos2.efi
CURRENT_INITRAMFS=${ESP}/EFI/Gentoo/initramfs-7.1.2-cachyos2.img
CURRENT_CONFIG=${ESP}/EFI/Gentoo/config-7.1.2-cachyos2
CURRENT_MAP=${ESP}/EFI/Gentoo/System.map-7.1.2-cachyos2
ARCHIVE_REL=app-misc/demo-1.0.gpkg.tar
ARCHIVE=${CRITICAL}/${ARCHIVE_REL}

mkdir -p -- \
    "${PACKAGE_ENV}" \
    "${ROOT}/etc/portage/env" \
    "${TOOLS}" \
    "${AUTHORITATIVE_DIR}" \
    "${ROOT}/proc" \
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

printf 'fixture known-good EFI kernel\n' >"${KNOWN_KERNEL}"
printf 'fixture known-good initramfs\n' >"${KNOWN_INITRAMFS}"
printf 'fixture legacy EFI kernel\n' >"${LEGACY_KERNEL}"
printf 'fixture legacy initramfs\n' >"${LEGACY_INITRAMFS}"
printf 'fixture current EFI kernel\n' >"${CURRENT_KERNEL}"
printf 'fixture current initramfs\n' >"${CURRENT_INITRAMFS}"
printf 'fixture current config\n' >"${CURRENT_CONFIG}"
printf 'fixture current System.map\n' >"${CURRENT_MAP}"
printf 'fixture AMD microcode\n' >"${ESP}/EFI/Gentoo/amd-uc.img"
printf 'fixture Intel microcode\n' >"${ESP}/EFI/Gentoo/intel-uc.img"
printf 'root=/dev/fixture quiet rd.break=old initrd=\\EFI\\Gentoo\\stale-one.img initrd=\\EFI\\Gentoo\\stale-two.img\n' >"${ROOT}/proc/cmdline"
kernel_sha=$(sha256sum -- "${KNOWN_KERNEL}")
kernel_sha=${kernel_sha%% *}
initramfs_sha=$(sha256sum -- "${KNOWN_INITRAMFS}")
initramfs_sha=${initramfs_sha%% *}
legacy_kernel_sha=$(sha256sum -- "${LEGACY_KERNEL}")
legacy_kernel_sha=${legacy_kernel_sha%% *}
legacy_initramfs_sha=$(sha256sum -- "${LEGACY_INITRAMFS}")
legacy_initramfs_sha=${legacy_initramfs_sha%% *}

mkdir -p -- "${STATE}/recovery"
cat >"${AUTHORITATIVE_MANIFEST}" <<EOF
version=1
generation_type=independent
bootnum=0004
kernel_version=7.1.2-cachyos2
kernel_image=${KNOWN_KERNEL}
kernel_sha256=${kernel_sha}
initramfs=${KNOWN_INITRAMFS}
initramfs_sha256=${initramfs_sha}
EOF
chmod 0600 "${AUTHORITATIVE_MANIFEST}"
manifest_sha=$(sha256sum -- "${AUTHORITATIVE_MANIFEST}")
manifest_sha=${manifest_sha%% *}

authoritative_initramfs_efi='\EFI\Gentoo\recovery\pgo-known-good-fixture\initramfs-7.1.2-cachyos2.img'
AUTHORITATIVE_INITRAMFS_HEX=$(printf '%s' "${authoritative_initramfs_efi}" | iconv -f UTF-8 -t UTF-16LE | od -An -tx1 | tr -d ' \n')
legacy_initramfs_efi='\EFI\Gentoo\initramfs-7.1.2-cachyos2.img.old'
LEGACY_INITRAMFS_HEX=$(printf '%s' "${legacy_initramfs_efi}" | iconv -f UTF-8 -t UTF-16LE | od -An -tx1 | tr -d ' \n')
export AUTHORITATIVE_INITRAMFS_HEX LEGACY_INITRAMFS_HEX
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

cat >"${TOOLS}/efibootmgr" <<'EOF'
#!/usr/bin/env bash
set -eu
state=${RECOVERY_FIXTURE_DIR}/bootnext
created=${RECOVERY_FIXTURE_DIR}/created-entry
rescue=${RECOVERY_FIXTURE_DIR}/rescue-entry
if [[ ${1-} == -n ]]; then
    printf '%s\n' "$2" >"${state}"
    exit 0
fi
if [[ ${1-} == --create || ${1-} == --create-only ]]; then
    mode=$1
    shift
    label= loader= cmdline= index=
    while (($#)); do
        case $1 in
            --index)
                index=$2
                shift 2
                ;;
            --disk|--part)
                shift 2
                ;;
            --label)
                label=$2
                shift 2
                ;;
            --loader)
                loader=$2
                shift 2
                ;;
            --unicode)
                cmdline=$2
                shift 2
                ;;
            *)
                exit 2
                ;;
        esac
    done
    if [[ ${mode} == --create ]]; then
        [[ ${index} == 1 ]]
        output=${created}
    else
        [[ -z ${index} ]]
        output=${rescue}
    fi
    [[ -n ${label} && -n ${loader} && -n ${cmdline} ]]
    {
        printf '%s\n' "${label}"
        printf '%s\n' "${loader}"
        printf '%s\n' "${cmdline}"
    } >"${output}"
    exit 0
fi
printf 'BootCurrent: 01FF\n'
if [[ -s ${state} ]]; then
    printf 'BootNext: %s\n' "$(<"${state}")"
fi
if [[ -s ${created} ]]; then
    printf 'BootOrder: 01FF,0300,0004,0200\n'
else
    printf 'BootOrder: 01FF,0004,0200\n'
fi
printf 'Boot01FF* Fixture current HD()/\\EFI\\Gentoo\\vmlinuz-7.1.2-cachyos2.efi\n'
printf 'Boot0004* Fixture authoritative independent HD()/\\EFI\\Gentoo\\recovery\\pgo-known-good-fixture\\vmlinuz-7.1.2-cachyos2.efi%s\n' "${AUTHORITATIVE_INITRAMFS_HEX}"
printf 'Boot0200* Fixture legacy managed HD()/\\EFI\\Gentoo\\vmlinuz-7.1.2-cachyos2-old.efi%s\n' "${LEGACY_INITRAMFS_HEX}"
if [[ -s ${created} ]]; then
    label=$(sed -n '1p' "${created}")
    loader=$(sed -n '2p' "${created}")
    cmdline=$(sed -n '3p' "${created}")
    encoded=$(printf '%s' "${cmdline}" | iconv -f UTF-8 -t UTF-16LE | od -An -tx1 | tr -d ' \n')
    printf 'Boot0300* %s HD()/%s%s\n' "${label}" "${loader}" "${encoded}"
fi
if [[ -s ${rescue} ]]; then
    label=$(sed -n '1p' "${rescue}")
    loader=$(sed -n '2p' "${rescue}")
    cmdline=$(sed -n '3p' "${rescue}")
    encoded=$(printf '%s' "${cmdline}" | iconv -f UTF-8 -t UTF-16LE | od -An -tx1 | tr -d ' \n')
    printf 'Boot0400* %s HD()/%s%s\n' "${label}" "${loader}" "${encoded}"
fi
EOF

cat >"${TOOLS}/lsinitrd" <<'EOF'
#!/usr/bin/env bash
set -eu
[[ -s $1 ]]
EOF

cat >"${TOOLS}/dracut" <<'EOF'
#!/usr/bin/env bash
set -eu
output=
while (($#)); do
    case $1 in
        --kver|--sysroot)
            shift 2
            ;;
        --force)
            shift
            ;;
        *)
            output=$1
            shift
            ;;
    esac
done
[[ -n ${output} ]]
printf 'fixture regenerated initramfs\n' >"${output}"
EOF
chmod 0755 "${TOOLS}/emerge" "${TOOLS}/efibootmgr" "${TOOLS}/lsinitrd" "${TOOLS}/dracut"

common=(
    --fixture-mode
    --root "${ROOT}"
    --state-root "${STATE}"
    --cache-root "${CACHE}"
    --esp-root "${ESP}"
    --tool-root "${TOOLS}"
)

"${ROLLBACK}" "${common[@]}" check

WEAK_MANIFEST=${STATE}/recovery/group-writable.manifest
cp -- "${AUTHORITATIVE_MANIFEST}" "${WEAK_MANIFEST}"
chmod 0620 "${WEAK_MANIFEST}"
if "${ROLLBACK}" "${common[@]}" --recovery-manifest "${WEAK_MANIFEST}" check; then
    fail 'group-writable authoritative recovery manifest was accepted'
fi
printf 'PASS: group-writable authoritative recovery manifest rejected\n'

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

known_before=$(sha256sum -- "${KNOWN_INITRAMFS}")
known_before=${known_before%% *}
RECOVERY_OUTPUT=${ESP}/EFI/Gentoo/initramfs-fixture-recovery.img
"${ROLLBACK}" "${common[@]}" \
    --regenerate-initramfs \
    --initramfs-output "${RECOVERY_OUTPUT}" \
    initramfs
assert_file "${RECOVERY_OUTPUT}"
known_after=$(sha256sum -- "${KNOWN_INITRAMFS}")
known_after=${known_after%% *}
[[ ${known_before} == "${known_after}" ]] || fail 'known-good initramfs changed during regeneration'

if "${ROLLBACK}" "${common[@]}" \
    --regenerate-initramfs \
    --initramfs-output "${KNOWN_INITRAMFS}" \
    initramfs; then
    fail 'known-good initramfs overwrite was not rejected'
fi

preserve_options=(
    "${common[@]}"
    --preserve-kernel 7.1.2-cachyos2
    --recovery-tag fixture-recovery
    --efi-label 'Gentoo Fixture Recovery'
    --efi-disk /dev/fixture
    --efi-part 1
)
PRESERVED_DIR=${ESP}/EFI/Gentoo/recovery/fixture-recovery
"${ROLLBACK}" "${preserve_options[@]}" preserve-boot
assert_absent "${PRESERVED_DIR}"
assert_absent "${FIXTURE}/created-entry"

"${ROLLBACK}" "${preserve_options[@]}" --execute preserve-boot
assert_file "${PRESERVED_DIR}/vmlinuz-7.1.2-cachyos2.efi"
assert_file "${PRESERVED_DIR}/initramfs-7.1.2-cachyos2.img"
assert_file "${PRESERVED_DIR}/config-7.1.2-cachyos2"
assert_file "${PRESERVED_DIR}/System.map-7.1.2-cachyos2"
cmp -s -- "${CURRENT_KERNEL}" "${PRESERVED_DIR}/vmlinuz-7.1.2-cachyos2.efi" || fail 'preserved kernel differs'
cmp -s -- "${CURRENT_INITRAMFS}" "${PRESERVED_DIR}/initramfs-7.1.2-cachyos2.img" || fail 'preserved initramfs differs'
assert_file "${STATE}/recovery/boot-fixture-recovery.manifest"
AUTHORITATIVE_CANDIDATE=${STATE}/recovery/authoritative-candidates/fixture-recovery.manifest
assert_file "${AUTHORITATIVE_CANDIDATE}"
[[ $(wc -l <"${AUTHORITATIVE_CANDIDATE}") == 8 ]] || fail 'authoritative candidate does not use the exact eight-key schema'
assert_contains 'version=1' "${AUTHORITATIVE_CANDIDATE}"
assert_contains 'generation_type=independent' "${AUTHORITATIVE_CANDIDATE}"
assert_contains 'bootnum=0300' "${AUTHORITATIVE_CANDIDATE}"
assert_contains "kernel_image=${PRESERVED_DIR}/vmlinuz-7.1.2-cachyos2.efi" "${AUTHORITATIVE_CANDIDATE}"
assert_contains "initramfs=${PRESERVED_DIR}/initramfs-7.1.2-cachyos2.img" "${AUTHORITATIVE_CANDIDATE}"
"${ROLLBACK}" "${common[@]}" --recovery-manifest "${AUTHORITATIVE_CANDIDATE}" --dry-run bootnext
printf 'PASS: newly preserved authoritative candidate is accepted by the default loader\n'
assert_contains 'Gentoo Fixture Recovery' "${FIXTURE}/created-entry"
assert_contains '\EFI\Gentoo\recovery\fixture-recovery\vmlinuz-7.1.2-cachyos2.efi' "${FIXTURE}/created-entry"
assert_contains 'initrd=\EFI\Gentoo\amd-uc.img' "${FIXTURE}/created-entry"
assert_contains 'initrd=\EFI\Gentoo\intel-uc.img' "${FIXTURE}/created-entry"
assert_contains 'initrd=\EFI\Gentoo\recovery\fixture-recovery\initramfs-7.1.2-cachyos2.img' "${FIXTURE}/created-entry"
if grep -Fq 'stale-' "${FIXTURE}/created-entry"; then
    fail 'stale initrd token survived recovery cmdline sanitization'
fi

rescue_options=(
    "${common[@]}"
    --recovery-tag fixture-rescue
    --efi-label 'Gentoo Fixture Rescue'
    --efi-disk /dev/fixture
    --efi-part 1
)
esp_file_count_before=$(find "${ESP}" -type f | wc -l)
known_kernel_before=$(sha256sum -- "${KNOWN_KERNEL}")
known_kernel_before=${known_kernel_before%% *}
known_initramfs_before=$(sha256sum -- "${KNOWN_INITRAMFS}")
known_initramfs_before=${known_initramfs_before%% *}
"${ROLLBACK}" "${rescue_options[@]}" rescue-entry
assert_absent "${FIXTURE}/rescue-entry"

"${ROLLBACK}" "${rescue_options[@]}" --execute rescue-entry
assert_file "${FIXTURE}/rescue-entry"
assert_file "${STATE}/recovery/rescue-entry-fixture-rescue.manifest"
assert_contains 'Gentoo Fixture Rescue' "${FIXTURE}/rescue-entry"
assert_contains '\EFI\Gentoo\recovery\pgo-known-good-fixture\vmlinuz-7.1.2-cachyos2.efi' "${FIXTURE}/rescue-entry"
assert_contains 'initrd=\EFI\Gentoo\amd-uc.img' "${FIXTURE}/rescue-entry"
assert_contains 'initrd=\EFI\Gentoo\intel-uc.img' "${FIXTURE}/rescue-entry"
assert_contains 'initrd=\EFI\Gentoo\recovery\pgo-known-good-fixture\initramfs-7.1.2-cachyos2.img' "${FIXTURE}/rescue-entry"
assert_contains 'rd.break=pre-mount' "${FIXTURE}/rescue-entry"
if grep -Fq 'rd.break=old' "${FIXTURE}/rescue-entry" || grep -Fq 'stale-' "${FIXTURE}/rescue-entry"; then
    fail 'stale initrd/break token survived rescue cmdline sanitization'
fi
esp_file_count_after=$(find "${ESP}" -type f | wc -l)
[[ ${esp_file_count_before} == "${esp_file_count_after}" ]] || fail 'rescue-entry copied a boot asset'
known_kernel_after=$(sha256sum -- "${KNOWN_KERNEL}")
known_kernel_after=${known_kernel_after%% *}
known_initramfs_after=$(sha256sum -- "${KNOWN_INITRAMFS}")
known_initramfs_after=${known_initramfs_after%% *}
[[ ${known_kernel_before} == "${known_kernel_after}" ]] || fail 'rescue-entry overwrote known-good kernel'
[[ ${known_initramfs_before} == "${known_initramfs_after}" ]] || fail 'rescue-entry overwrote known-good initramfs'
efi_state=$("${TOOLS}/efibootmgr" -v)
grep -Fxq 'BootOrder: 01FF,0300,0004,0200' <<<"${efi_state}" || fail 'rescue --create-only changed BootOrder'

"${ROLLBACK}" "${common[@]}" bootnext
[[ $(<"${FIXTURE}/bootnext") == 0004 ]] || fail 'zero-override BootNext was not armed to authoritative Boot0004'

"${ROLLBACK}" "${common[@]}" --dry-run all >"${FIXTURE}/zero-override-all.log"
assert_contains "recovery_identity_source=manifest:${AUTHORITATIVE_MANIFEST}" "${FIXTURE}/zero-override-all.log"
assert_contains "manifest_sha256=${manifest_sha} bootnum=0004" "${FIXTURE}/zero-override-all.log"
assert_contains "kernel=${KNOWN_KERNEL}" "${FIXTURE}/zero-override-all.log"
assert_contains "initramfs=${KNOWN_INITRAMFS}" "${FIXTURE}/zero-override-all.log"
assert_contains "${TOOLS}/efibootmgr -n 0004" "${FIXTURE}/zero-override-all.log"

"${ROLLBACK}" "${common[@]}" \
    --legacy-managed-default \
    --kernel-sha256 "${legacy_kernel_sha}" \
    --initramfs-sha256 "${legacy_initramfs_sha}" \
    bootnext
[[ $(<"${FIXTURE}/bootnext") == 0200 ]] || fail 'explicit legacy fallback did not arm Boot0200'

"${ROLLBACK}" "${common[@]}" check

find "${STATE}/reports/recovery" -type f -name 'rollback-*.log' -print -quit | grep -q . || fail 'timestamped recovery log was not created'

printf 'PASS: rollback fixture suite\n'

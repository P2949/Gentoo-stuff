#!/usr/bin/env bash
# Recover a Gentoo installation from the system-wide optimization project.
#
# Live mutation is intentionally fail-closed.  Use --dry-run to inspect an
# action, or --fixture-mode with a non-/ root and tools below --tool-root for
# non-root fixture tests.

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly PROGRAM=${0##*/}
readonly VERSION=2
readonly LEGACY_BOOTNUM=0200
readonly LEGACY_KERNEL_VERSION=7.1.2-cachyos2
readonly LEGACY_KERNEL_SHA256=415428b4ffac67a801b62d316d80864ba58d5c539888e2adc2b9e66004159e3e
readonly LEGACY_INITRAMFS_SHA256=3c24514e71ff80bb7a7c5abb4da9f5ff1de0e02a75adbe4892e3ec97e6a7afcf

DRY_RUN=0
FIXTURE_MODE=0
EXECUTE_PRESERVE_BOOT=0
REGENERATE_INITRAMFS=0
OVERWRITE_INITRAMFS=0
OVERWRITE_KNOWN_GOOD=0
LEGACY_MANAGED_DEFAULT=0
PORTAGE_ROOT=${RECOVERY_PORTAGE_ROOT:-/}
STATE_ROOT=${RECOVERY_STATE_ROOT:-}
CACHE_ROOT=${RECOVERY_CACHE_ROOT:-}
PKGDIR=${RECOVERY_PKGDIR:-}
ESP_ROOT=${RECOVERY_ESP_ROOT:-}
TOOL_ROOT=${RECOVERY_TOOL_ROOT:-}
LOG_DIR=${RECOVERY_LOG_DIR:-}
RECOVERY_MANIFEST=${RECOVERY_AUTHORITATIVE_MANIFEST:-}
KNOWN_GOOD_BOOTNUM=${RECOVERY_KNOWN_GOOD_BOOTNUM:-}
KNOWN_GOOD_KERNEL_VERSION=${RECOVERY_KNOWN_GOOD_KERNEL_VERSION:-}
KNOWN_GOOD_KERNEL_IMAGE=${RECOVERY_KNOWN_GOOD_KERNEL_IMAGE:-}
KNOWN_GOOD_INITRAMFS=${RECOVERY_KNOWN_GOOD_INITRAMFS:-}
KNOWN_GOOD_KERNEL_SHA256=${RECOVERY_KNOWN_GOOD_KERNEL_SHA256:-}
KNOWN_GOOD_INITRAMFS_SHA256=${RECOVERY_KNOWN_GOOD_INITRAMFS_SHA256:-}
INITRAMFS_OUTPUT=${RECOVERY_INITRAMFS_OUTPUT:-}
CRITICAL_FILE=${RECOVERY_CRITICAL_FILE:-}
PRESERVE_KERNEL_VERSION=${RECOVERY_PRESERVE_KERNEL_VERSION:-}
RECOVERY_TAG=${RECOVERY_BOOT_TAG:-}
EFI_LABEL=${RECOVERY_EFI_LABEL:-}
EFI_DISK=${RECOVERY_EFI_DISK:-}
EFI_PART=${RECOVERY_EFI_PART:-}
CMDLINE_FILE=${RECOVERY_CMDLINE_FILE:-}
COMMAND=
LOG_FILE=
LOG_READY=0
LOCK_HELD=0
LOCK_FD=
SIMULATED_KILL_SWITCH=0
RESOLVED_PKGDIR=
PACKAGES_INDEX=
ATOM_CPV=
VALIDATED_ARCHIVE=
CHECK_FAILURES=0
RECOVERY_IDENTITY_SOURCE=
RECOVERY_MANIFEST_SHA256=
declare -a EXTRA_ASSIGNMENTS=()
declare -a MICROCODE_IMAGES=()
declare -a RECOVERY_GCC_ATOMS=(sys-devel/gcc dev-lang/fpc dev-build/make sys-fs/zfs)

usage() {
    cat <<EOF
Usage: ${PROGRAM} [global options] COMMAND [=category/package-version ...]

Commands:
  check                 Read-only preflight of rollback prerequisites.
  disable               Disable active PGO/BOLT package.env assignments and
                        install the optimization-off kill switch.
  restore ATOM...       Restore one or more exact atoms from protected PKGDIR.
  restore-critical      Restore all exact CPVs in protected PKGDIR (or those
                        listed by --critical-file).
  preserved-rebuild     Rebuild @preserved-rebuild with optimization disabled.
  initramfs             Check the known-good initramfs; regenerate a distinct
                        recovery image only with --regenerate-initramfs.
  bootnext              Verify the known-good EFI entry and arm it with BootNext.
  preserve-boot         Preserve the current kernel assets under a unique,
                        non-managed recovery tag and create a custom EFI entry
                        at BootOrder index 1. A loader-compatible authoritative
                        manifest candidate is emitted for post-boot promotion.
                        Preview-only unless --execute.
  rescue-entry          Create a BootOrder-neutral EFI rescue entry referencing
                        the existing known-good kernel/initramfs and adding a
                        pre-mount dracut break. Preview-only unless --execute.
  all                   Run disable, restore-critical, preserved-rebuild,
                        initramfs check/regeneration, then bootnext.

Global options (must precede COMMAND):
  --dry-run                      Log mutations without performing them.
  --execute                      Permit preserve-boot/rescue-entry mutations
                                 (both otherwise default to dry-run as root).
  --check                        Alias for the check command.
  --root DIR                     Portage ROOT/PORTAGE_CONFIGROOT (default /).
  --state-root DIR               Persistent state root.
  --cache-root DIR               Cache/recovery root.
  --pkgdir DIR                   Protected binary package directory; default
                                 CACHE_ROOT/binpkgs/critical-current.
  --esp-root DIR                 EFI System Partition mount (default ROOT/efi).
  --tool-root DIR                Resolve emerge/dracut/lsinitrd/efibootmgr from
                                 this directory (required for non-root fixtures).
  --fixture-mode                 Permit non-root mutation only below a non-/
                                 Portage root, using tools below --tool-root.
  --log-dir DIR                  Recovery log directory.
  --assignment RELPATH           Additional package.env file to disable.
  --critical-file FILE           Exact atoms to restore instead of every CPV in
                                 the protected Packages index.
  --recovery-manifest FILE       Root-owned authoritative recovery manifest;
                                 default STATE_ROOT/recovery/authoritative-known-good.manifest.
  --legacy-managed-default       Explicitly select the superseded managed
                                 Boot${LEGACY_BOOTNUM} *-old assets instead of the manifest.
  --known-good-bootnum HEX       Explicitly override the selected EFI boot number.
  --known-good-kernel VERSION    Preserved kernel version.
  --known-good-kernel-image FILE Preserved EFI kernel path.
  --known-good-initramfs FILE    Preserved initramfs path.
  --kernel-sha256 SHA256|none    Expected preserved kernel identity.
  --initramfs-sha256 SHA256|none Expected preserved initramfs identity.
  --regenerate-initramfs         Build a separate recovery initramfs.
  --initramfs-output FILE        Regeneration destination.
  --preserve-kernel VERSION      Kernel release whose current assets to preserve.
  --recovery-tag TAG             Unique recovery directory tag.
  --efi-label LABEL              Custom EFI label (must not contain "UMC").
  --efi-disk DEVICE              ESP parent disk; auto-detected by default.
  --efi-part NUMBER              ESP partition number; auto-detected by default.
  --cmdline-file FILE            Kernel command line source (default ROOT/proc/cmdline).
  --microcode FILE               Microcode image to append; may be repeated.
  --overwrite-initramfs          Permit replacing a non-known-good output.
  --overwrite-known-good         Also permit replacing the known-good image;
                                 deliberately separate and never the default.
  -h, --help                     Show this help.

Boot identity defaults are loaded from the root-owned authoritative manifest.
The managed Boot${LEGACY_BOOTNUM} paths are available only through the explicit
--legacy-managed-default fallback. All EFI assets are below /efi, not /boot.
Restore operations use emerge --usepkgonly with remote binhosts, source fetch,
and source fallback disabled.  All actions emit a timestamp-named log.
EOF
}

timestamp() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

log() {
    local level=$1
    shift
    printf '%s [%s] %s\n' "$(timestamp)" "${level}" "$*"
}

die() {
    log ERROR "$*" >&2
    exit 1
}

warn() {
    log WARN "$*" >&2
}

need_option_value() {
    (($# >= 2)) || die "option $1 requires a value"
}

while (($#)); do
    case $1 in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --execute)
            EXECUTE_PRESERVE_BOOT=1
            shift
            ;;
        --check)
            [[ -z ${COMMAND} ]] || die "--check cannot be combined with a command"
            COMMAND=check
            shift
            ;;
        --fixture-mode)
            FIXTURE_MODE=1
            shift
            ;;
        --regenerate-initramfs)
            REGENERATE_INITRAMFS=1
            shift
            ;;
        --overwrite-initramfs)
            OVERWRITE_INITRAMFS=1
            shift
            ;;
        --overwrite-known-good)
            OVERWRITE_KNOWN_GOOD=1
            shift
            ;;
        --legacy-managed-default)
            LEGACY_MANAGED_DEFAULT=1
            shift
            ;;
        --root)
            need_option_value "$@"
            PORTAGE_ROOT=$2
            shift 2
            ;;
        --state-root)
            need_option_value "$@"
            STATE_ROOT=$2
            shift 2
            ;;
        --cache-root)
            need_option_value "$@"
            CACHE_ROOT=$2
            shift 2
            ;;
        --pkgdir)
            need_option_value "$@"
            PKGDIR=$2
            shift 2
            ;;
        --esp-root)
            need_option_value "$@"
            ESP_ROOT=$2
            shift 2
            ;;
        --tool-root)
            need_option_value "$@"
            TOOL_ROOT=$2
            shift 2
            ;;
        --log-dir)
            need_option_value "$@"
            LOG_DIR=$2
            shift 2
            ;;
        --assignment)
            need_option_value "$@"
            EXTRA_ASSIGNMENTS+=("$2")
            shift 2
            ;;
        --critical-file)
            need_option_value "$@"
            CRITICAL_FILE=$2
            shift 2
            ;;
        --recovery-manifest)
            need_option_value "$@"
            RECOVERY_MANIFEST=$2
            shift 2
            ;;
        --known-good-bootnum)
            need_option_value "$@"
            KNOWN_GOOD_BOOTNUM=$2
            shift 2
            ;;
        --known-good-kernel)
            need_option_value "$@"
            KNOWN_GOOD_KERNEL_VERSION=$2
            shift 2
            ;;
        --known-good-kernel-image)
            need_option_value "$@"
            KNOWN_GOOD_KERNEL_IMAGE=$2
            shift 2
            ;;
        --known-good-initramfs)
            need_option_value "$@"
            KNOWN_GOOD_INITRAMFS=$2
            shift 2
            ;;
        --kernel-sha256)
            need_option_value "$@"
            KNOWN_GOOD_KERNEL_SHA256=$2
            shift 2
            ;;
        --initramfs-sha256)
            need_option_value "$@"
            KNOWN_GOOD_INITRAMFS_SHA256=$2
            shift 2
            ;;
        --initramfs-output)
            need_option_value "$@"
            INITRAMFS_OUTPUT=$2
            shift 2
            ;;
        --preserve-kernel)
            need_option_value "$@"
            PRESERVE_KERNEL_VERSION=$2
            shift 2
            ;;
        --recovery-tag)
            need_option_value "$@"
            RECOVERY_TAG=$2
            shift 2
            ;;
        --efi-label)
            need_option_value "$@"
            EFI_LABEL=$2
            shift 2
            ;;
        --efi-disk)
            need_option_value "$@"
            EFI_DISK=$2
            shift 2
            ;;
        --efi-part)
            need_option_value "$@"
            EFI_PART=$2
            shift 2
            ;;
        --cmdline-file)
            need_option_value "$@"
            CMDLINE_FILE=$2
            shift 2
            ;;
        --microcode)
            need_option_value "$@"
            MICROCODE_IMAGES+=("$2")
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        check|disable|restore|restore-critical|preserved-rebuild|initramfs|bootnext|preserve-boot|rescue-entry|all)
            [[ -z ${COMMAND} ]] || die "multiple commands supplied"
            COMMAND=$1
            shift
            break
            ;;
        --)
            shift
            break
            ;;
        *)
            die "unknown option or command: $1"
            ;;
    esac
done

declare -a COMMAND_ARGS=("$@")
[[ -n ${COMMAND} ]] || die "a command is required (try --help)"
if [[ ${COMMAND} != restore && ${#COMMAND_ARGS[@]} -ne 0 ]]; then
    die "${COMMAND} does not accept positional arguments"
fi
if [[ ${COMMAND} != preserve-boot && ${COMMAND} != rescue-entry && ${EXECUTE_PRESERVE_BOOT} -eq 1 ]]; then
    die "--execute is valid only with preserve-boot or rescue-entry"
fi
if [[ ( ${COMMAND} == preserve-boot || ${COMMAND} == rescue-entry ) && ${EXECUTE_PRESERVE_BOOT} -ne 1 ]]; then
    DRY_RUN=1
fi
if [[ ${COMMAND} == restore && ${#COMMAND_ARGS[@]} -eq 0 ]]; then
    die "restore requires at least one exact atom"
fi

normalize_absolute() {
    local value=$1
    local label=$2
    [[ ${value} == /* ]] || die "${label} must be an absolute path: ${value}"
    if [[ ${value} != / ]]; then
        value=${value%/}
    fi
    printf '%s' "${value}"
}

root_path() {
    local relative=${1#/}
    if [[ ${PORTAGE_ROOT} == / ]]; then
        printf '/%s' "${relative}"
    else
        printf '%s/%s' "${PORTAGE_ROOT}" "${relative}"
    fi
}

path_is_within() {
    local child parent
    child=$(readlink -m -- "$1") || return 1
    parent=$(readlink -m -- "$2") || return 1
    [[ ${child} == "${parent}" || ${child} == "${parent}/"* ]]
}

command_needs_known_good_identity() {
    case ${COMMAND} in
        check|initramfs|bootnext|rescue-entry|all)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

assert_authoritative_manifest_security() {
    local manifest=$1
    local recovery_dir mode owner numeric path
    recovery_dir=${STATE_ROOT}/recovery
    [[ -d ${recovery_dir} ]] || die "authoritative recovery state directory is absent: ${recovery_dir}"
    [[ ! -L ${recovery_dir} ]] || die "authoritative recovery state directory may not be a symlink: ${recovery_dir}"
    [[ -f ${manifest} && -r ${manifest} ]] || die "authoritative recovery manifest is absent or unreadable: ${manifest}"
    [[ ! -L ${manifest} ]] || die "authoritative recovery manifest may not be a symlink: ${manifest}"
    path_is_within "${manifest}" "${recovery_dir}" || die "authoritative recovery manifest must remain below ${recovery_dir}"
    for path in "${recovery_dir}" "${manifest}"; do
        mode=$(stat -Lc '%a' -- "${path}") || die "cannot stat protected recovery identity path: ${path}"
        owner=$(stat -Lc '%u' -- "${path}") || die "cannot read owner of protected recovery identity path: ${path}"
        [[ ${mode} =~ ^[0-7]{3,4}$ ]] || die "invalid protected recovery identity mode ${mode}: ${path}"
        numeric=$((8#${mode}))
        (( (numeric & 8#022) == 0 )) || die "recovery identity path is group/world writable: ${path} (mode ${mode})"
        if [[ ${PORTAGE_ROOT} == / ]] && ((owner != 0)); then
            die "live recovery identity path is not root-owned: ${path}"
        fi
    done
}

load_authoritative_manifest() {
    local manifest=$1
    local line key value line_number=0 required
    local -A values=()
    local -A seen=()
    assert_authoritative_manifest_security "${manifest}"
    RECOVERY_MANIFEST_SHA256=$(sha256sum -- "${manifest}") || die "cannot hash authoritative recovery manifest"
    RECOVERY_MANIFEST_SHA256=${RECOVERY_MANIFEST_SHA256%% *}
    while IFS= read -r line || [[ -n ${line} ]]; do
        line_number=$((line_number + 1))
        [[ ${line} != *$'\r'* ]] || die "carriage return in authoritative manifest line ${line_number}"
        [[ -n ${line} && ${line} != \#* ]] || continue
        [[ ${line} == *=* ]] || die "malformed authoritative manifest line ${line_number}"
        key=${line%%=*}
        value=${line#*=}
        case ${key} in
            version|generation_type|bootnum|kernel_version|kernel_image|kernel_sha256|initramfs|initramfs_sha256)
                ;;
            *)
                die "unknown authoritative manifest key '${key}' on line ${line_number}"
                ;;
        esac
        [[ -z ${seen[${key}]:-} ]] || die "duplicate authoritative manifest key: ${key}"
        [[ -n ${value} ]] || die "empty authoritative manifest value for ${key}"
        seen["${key}"]=1
        values["${key}"]=${value}
    done <"${manifest}"
    for required in version generation_type bootnum kernel_version kernel_image kernel_sha256 initramfs initramfs_sha256; do
        [[ -n ${values[${required}]:-} ]] || die "authoritative recovery manifest lacks ${required}"
    done
    [[ ${values[version]} == 1 ]] || die "unsupported authoritative recovery manifest version: ${values[version]}"
    [[ ${values[generation_type]} == independent ]] || die "authoritative recovery manifest is not an independent generation"
    [[ ${values[bootnum]} =~ ^[0-9A-Fa-f]{4}$ ]] || die "invalid bootnum in authoritative recovery manifest"
    [[ ${values[kernel_version]} =~ ^[A-Za-z0-9+_.-]+$ ]] || die "invalid kernel_version in authoritative recovery manifest"
    [[ ${values[kernel_image]} == /* && ${values[initramfs]} == /* ]] || die "authoritative EFI asset paths must be absolute"
    path_is_within "${values[kernel_image]}" "${ESP_ROOT}/EFI/Gentoo/recovery" || die "authoritative kernel is not below the independent /efi recovery tree"
    path_is_within "${values[initramfs]}" "${ESP_ROOT}/EFI/Gentoo/recovery" || die "authoritative initramfs is not below the independent /efi recovery tree"
    [[ ${values[kernel_sha256]} =~ ^[0-9a-fA-F]{64}$ ]] || die "invalid kernel SHA-256 in authoritative recovery manifest"
    [[ ${values[initramfs_sha256]} =~ ^[0-9a-fA-F]{64}$ ]] || die "invalid initramfs SHA-256 in authoritative recovery manifest"

    KNOWN_GOOD_BOOTNUM=${values[bootnum]}
    KNOWN_GOOD_KERNEL_VERSION=${values[kernel_version]}
    KNOWN_GOOD_KERNEL_IMAGE=${values[kernel_image]}
    KNOWN_GOOD_KERNEL_SHA256=${values[kernel_sha256]}
    KNOWN_GOOD_INITRAMFS=${values[initramfs]}
    KNOWN_GOOD_INITRAMFS_SHA256=${values[initramfs_sha256]}
}

configure_known_good_identity() {
    local override_bootnum=${KNOWN_GOOD_BOOTNUM}
    local override_kernel_version=${KNOWN_GOOD_KERNEL_VERSION}
    local override_kernel_image=${KNOWN_GOOD_KERNEL_IMAGE}
    local override_initramfs=${KNOWN_GOOD_INITRAMFS}
    local override_kernel_sha256=${KNOWN_GOOD_KERNEL_SHA256}
    local override_initramfs_sha256=${KNOWN_GOOD_INITRAMFS_SHA256}
    if ((LEGACY_MANAGED_DEFAULT)); then
        KNOWN_GOOD_BOOTNUM=${LEGACY_BOOTNUM}
        KNOWN_GOOD_KERNEL_VERSION=${LEGACY_KERNEL_VERSION}
        KNOWN_GOOD_KERNEL_IMAGE=${ESP_ROOT}/EFI/Gentoo/vmlinuz-${LEGACY_KERNEL_VERSION}-old.efi
        KNOWN_GOOD_INITRAMFS=${ESP_ROOT}/EFI/Gentoo/initramfs-${LEGACY_KERNEL_VERSION}.img.old
        KNOWN_GOOD_KERNEL_SHA256=${LEGACY_KERNEL_SHA256}
        KNOWN_GOOD_INITRAMFS_SHA256=${LEGACY_INITRAMFS_SHA256}
        RECOVERY_IDENTITY_SOURCE=explicit-legacy-managed-Boot${LEGACY_BOOTNUM}
    else
        if [[ -z ${RECOVERY_MANIFEST} ]]; then
            RECOVERY_MANIFEST=${STATE_ROOT}/recovery/authoritative-known-good.manifest
        fi
        RECOVERY_MANIFEST=$(normalize_absolute "${RECOVERY_MANIFEST}" "authoritative recovery manifest")
        load_authoritative_manifest "${RECOVERY_MANIFEST}"
        RECOVERY_IDENTITY_SOURCE=manifest:${RECOVERY_MANIFEST}
    fi
    [[ -z ${override_bootnum} ]] || KNOWN_GOOD_BOOTNUM=${override_bootnum}
    [[ -z ${override_kernel_version} ]] || KNOWN_GOOD_KERNEL_VERSION=${override_kernel_version}
    [[ -z ${override_kernel_image} ]] || KNOWN_GOOD_KERNEL_IMAGE=${override_kernel_image}
    [[ -z ${override_initramfs} ]] || KNOWN_GOOD_INITRAMFS=${override_initramfs}
    [[ -z ${override_kernel_sha256} ]] || KNOWN_GOOD_KERNEL_SHA256=${override_kernel_sha256}
    [[ -z ${override_initramfs_sha256} ]] || KNOWN_GOOD_INITRAMFS_SHA256=${override_initramfs_sha256}
}

PORTAGE_ROOT=$(normalize_absolute "${PORTAGE_ROOT}" "Portage root")
if [[ -z ${STATE_ROOT} ]]; then
    STATE_ROOT=$(root_path /var/lib/gentoo-optimization)
fi
if [[ -z ${CACHE_ROOT} ]]; then
    CACHE_ROOT=$(root_path /var/cache/gentoo-optimization)
fi
if [[ -z ${ESP_ROOT} ]]; then
    ESP_ROOT=$(root_path /efi)
fi
STATE_ROOT=$(normalize_absolute "${STATE_ROOT}" "state root")
CACHE_ROOT=$(normalize_absolute "${CACHE_ROOT}" "cache root")
ESP_ROOT=$(normalize_absolute "${ESP_ROOT}" "ESP root")
if [[ -n ${TOOL_ROOT} ]]; then
    TOOL_ROOT=$(normalize_absolute "${TOOL_ROOT}" "tool root")
fi
if [[ -z ${PKGDIR} ]]; then
    PKGDIR=${CACHE_ROOT}/binpkgs/critical-current
fi
PKGDIR=$(normalize_absolute "${PKGDIR}" "PKGDIR")
if [[ -z ${LOG_DIR} ]]; then
    LOG_DIR=${STATE_ROOT}/reports/recovery
fi
LOG_DIR=$(normalize_absolute "${LOG_DIR}" "log directory")
if command_needs_known_good_identity; then
    configure_known_good_identity
    KNOWN_GOOD_KERNEL_IMAGE=$(normalize_absolute "${KNOWN_GOOD_KERNEL_IMAGE}" "known-good kernel image")
    KNOWN_GOOD_INITRAMFS=$(normalize_absolute "${KNOWN_GOOD_INITRAMFS}" "known-good initramfs")
elif ((LEGACY_MANAGED_DEFAULT)); then
    die "recovery identity options are valid only for check, initramfs, bootnext, rescue-entry, or all"
fi
if [[ -n ${INITRAMFS_OUTPUT} ]]; then
    INITRAMFS_OUTPUT=$(normalize_absolute "${INITRAMFS_OUTPUT}" "initramfs output")
fi
if [[ -n ${CRITICAL_FILE} ]]; then
    CRITICAL_FILE=$(normalize_absolute "${CRITICAL_FILE}" "critical atom file")
fi
if [[ -z ${PRESERVE_KERNEL_VERSION} ]]; then
    PRESERVE_KERNEL_VERSION=$(uname -r)
fi
[[ ${PRESERVE_KERNEL_VERSION} =~ ^[A-Za-z0-9+_.-]+$ ]] || die "invalid preserve kernel version: ${PRESERVE_KERNEL_VERSION}"
if [[ -z ${RECOVERY_TAG} ]]; then
    RECOVERY_TAG=recovery-$(date -u '+%Y%m%dT%H%M%SZ')-${$}
fi
[[ ${RECOVERY_TAG} =~ ^[A-Za-z0-9._-]+$ ]] || die "recovery tag contains unsafe characters: ${RECOVERY_TAG}"
if [[ -z ${EFI_LABEL} ]]; then
    if [[ ${COMMAND} == rescue-entry ]]; then
        EFI_LABEL="Gentoo Rescue ${KNOWN_GOOD_KERNEL_VERSION} ${RECOVERY_TAG}"
    else
        EFI_LABEL="Gentoo Recovery ${PRESERVE_KERNEL_VERSION} ${RECOVERY_TAG}"
    fi
fi
[[ ${EFI_LABEL^^} != *UMC* ]] || die "custom recovery EFI label must not contain UMC"
[[ ${EFI_LABEL} != *$'\n'* && ${EFI_LABEL} != *$'\r'* ]] || die "EFI label may not contain newlines"
if [[ -n ${EFI_DISK} ]]; then
    EFI_DISK=$(normalize_absolute "${EFI_DISK}" "EFI disk")
fi
if [[ -n ${EFI_PART} ]]; then
    [[ ${EFI_PART} =~ ^[1-9][0-9]*$ ]] || die "EFI partition must be a positive integer"
fi
if [[ -z ${CMDLINE_FILE} ]]; then
    CMDLINE_FILE=$(root_path /proc/cmdline)
fi
CMDLINE_FILE=$(normalize_absolute "${CMDLINE_FILE}" "cmdline file")
if ((${#MICROCODE_IMAGES[@]})); then
    for index in "${!MICROCODE_IMAGES[@]}"; do
        MICROCODE_IMAGES[index]=$(normalize_absolute "${MICROCODE_IMAGES[index]}" "microcode image")
    done
fi
if command_needs_known_good_identity; then
    KNOWN_GOOD_BOOTNUM=${KNOWN_GOOD_BOOTNUM#Boot}
    KNOWN_GOOD_BOOTNUM=${KNOWN_GOOD_BOOTNUM^^}
    [[ ${KNOWN_GOOD_BOOTNUM} =~ ^[0-9A-F]{4}$ ]] || die "known-good boot number must be four hexadecimal digits"
    [[ ${KNOWN_GOOD_KERNEL_VERSION} =~ ^[A-Za-z0-9+_.-]+$ ]] || die "invalid known-good kernel version"
    for digest in "${KNOWN_GOOD_KERNEL_SHA256}" "${KNOWN_GOOD_INITRAMFS_SHA256}"; do
        [[ ${digest} == none || ${digest} =~ ^[0-9a-fA-F]{64}$ ]] || die "expected SHA-256 must be 64 hexadecimal digits or 'none'"
    done
    KNOWN_GOOD_KERNEL_SHA256=${KNOWN_GOOD_KERNEL_SHA256,,}
    KNOWN_GOOD_INITRAMFS_SHA256=${KNOWN_GOOD_INITRAMFS_SHA256,,}
fi

is_mutating_command() {
    case ${COMMAND} in
        disable|restore|restore-critical|preserved-rebuild|bootnext|preserve-boot|rescue-entry|all)
            return 0
            ;;
        initramfs)
            ((REGENERATE_INITRAMFS))
            return
            ;;
        *)
            return 1
            ;;
    esac
}

require_mutation_authority() {
    ((DRY_RUN)) && return 0
    is_mutating_command || return 0
    if ((EUID == 0)); then
        return 0
    fi
    ((FIXTURE_MODE)) || die "live mutations require root; use --dry-run for inspection"
    [[ ${PORTAGE_ROOT} != / ]] || die "fixture mutation refuses Portage root /"
    [[ -n ${TOOL_ROOT} ]] || die "non-root fixture mutation requires --tool-root"
    path_is_within "${TOOL_ROOT}" "${PORTAGE_ROOT}" || die "fixture tool root must be below the fixture Portage root"
    path_is_within "${ESP_ROOT}" "${PORTAGE_ROOT}" || die "fixture ESP root must be below the fixture Portage root"
}

init_log() {
    local requested=${LOG_DIR}
    local stamp
    stamp=$(date -u '+%Y%m%dT%H%M%SZ')
    if ! mkdir -p -- "${requested}" 2>/dev/null || [[ ! -w ${requested} ]]; then
        if is_mutating_command && ((DRY_RUN == 0)); then
            die "cannot create persistent recovery log directory: ${requested}"
        fi
        requested=${TMPDIR:-/tmp}/gentoo-optimization-recovery-${EUID}
        mkdir -p -- "${requested}" || die "cannot create fallback log directory: ${requested}"
        warn "persistent log directory is not writable; using ${requested}"
    fi
    LOG_FILE=${requested}/rollback-${stamp}-${$}.log
    : >"${LOG_FILE}" || die "cannot create recovery log: ${LOG_FILE}"
    chmod 0600 "${LOG_FILE}" || die "cannot protect recovery log: ${LOG_FILE}"
    exec > >(tee -a -- "${LOG_FILE}") 2>&1
    LOG_READY=1
    log INFO "${PROGRAM} v${VERSION}: command=${COMMAND} dry_run=${DRY_RUN}"
    log INFO "log=${LOG_FILE}"
    log INFO "portage_root=${PORTAGE_ROOT} state_root=${STATE_ROOT} cache_root=${CACHE_ROOT} pkgdir=${PKGDIR} esp=${ESP_ROOT}"
    if command_needs_known_good_identity; then
        log INFO "recovery_identity_source=${RECOVERY_IDENTITY_SOURCE} manifest_sha256=${RECOVERY_MANIFEST_SHA256:-none} bootnum=${KNOWN_GOOD_BOOTNUM} kernel=${KNOWN_GOOD_KERNEL_IMAGE} initramfs=${KNOWN_GOOD_INITRAMFS}"
    fi
}

release_lock() {
    if ((LOCK_HELD)); then
        flock -u "${LOCK_FD}" || true
        LOCK_HELD=0
    fi
}

on_exit() {
    local rc=$?
    trap - EXIT
    release_lock
    if ((LOG_READY)); then
        if ((rc == 0)); then
            log INFO "completed successfully"
        else
            log ERROR "failed with exit status ${rc}"
        fi
    fi
    exit "${rc}"
}
trap on_exit EXIT

require_mutation_authority
init_log

acquire_lock() {
    ((DRY_RUN)) && return 0
    is_mutating_command || return 0
    local lock_dir=${STATE_ROOT}/locks
    mkdir -p -- "${lock_dir}" || die "cannot create recovery lock directory"
    exec {LOCK_FD}>"${lock_dir}/rollback.lock"
    flock -n "${LOCK_FD}" || die "another recovery action holds ${lock_dir}/rollback.lock"
    LOCK_HELD=1
}
acquire_lock

resolve_tool() {
    local name=$1
    local result
    if [[ -n ${TOOL_ROOT} ]]; then
        result=${TOOL_ROOT}/${name}
        [[ -x ${result} ]] || die "required fixture tool is not executable: ${result}"
    else
        result=$(command -v -- "${name}") || die "required tool not found: ${name}"
    fi
    printf '%s' "${result}"
}

format_command() {
    local arg
    printf 'RUN'
    for arg in "$@"; do
        printf ' %q' "${arg}"
    done
    printf '\n'
}

run_mutation() {
    format_command "$@"
    if ((DRY_RUN)); then
        return 0
    fi
    "$@"
}

assert_secure_path() {
    local path=$1
    local kind=$2
    local mode owner numeric
    if [[ ${kind} == directory ]]; then
        [[ -d ${path} ]] || die "required protected directory is absent: ${path}"
    else
        [[ -f ${path} ]] || die "required protected file is absent: ${path}"
    fi
    mode=$(stat -Lc '%a' -- "${path}") || die "cannot stat protected ${kind}: ${path}"
    owner=$(stat -Lc '%u' -- "${path}") || die "cannot read owner of protected ${kind}: ${path}"
    numeric=$((8#${mode}))
    (( (numeric & 8#022) == 0 )) || die "protected ${kind} is group/world writable: ${path} (mode ${mode})"
    if [[ ${PORTAGE_ROOT} == / ]] && ((owner != 0)); then
        die "live protected ${kind} is not root-owned: ${path}"
    fi
}

resolve_pkgdir() {
    if [[ -L ${PKGDIR} && ${PORTAGE_ROOT} == / ]]; then
        local link_owner
        link_owner=$(stat -c '%u' -- "${PKGDIR}") || die "cannot stat PKGDIR symlink: ${PKGDIR}"
        ((link_owner == 0)) || die "live PKGDIR symlink is not root-owned: ${PKGDIR}"
    fi
    RESOLVED_PKGDIR=$(readlink -f -- "${PKGDIR}") || die "protected PKGDIR does not resolve: ${PKGDIR}"
    assert_secure_path "${RESOLVED_PKGDIR}" directory
    PACKAGES_INDEX=${RESOLVED_PKGDIR}/Packages
    assert_secure_path "${PACKAGES_INDEX}" file
    [[ -r ${PACKAGES_INDEX} ]] || die "protected Packages index is not readable: ${PACKAGES_INDEX}"
    log INFO "protected PKGDIR resolved to ${RESOLVED_PKGDIR}"
}

atom_to_cpv() {
    local atom=$1
    local body
    [[ ${atom} == =* ]] || die "restore atom must be exact and begin with '=': ${atom}"
    [[ ${atom} != *'['* && ${atom} != *']'* && ${atom} != *'*'* && ${atom} != *'?'* ]] || die "restore atom may not contain USE/wildcard syntax: ${atom}"
    [[ ${atom} != *[[:space:]]* ]] || die "restore atom contains whitespace: ${atom}"
    body=${atom#=}
    body=${body%%::*}
    body=${body%%:*}
    [[ ${body} == */* ]] || die "invalid exact atom: ${atom}"
    [[ ${body#*/} =~ -[0-9] ]] || die "exact atom lacks a version beginning with a digit: ${atom}"
    [[ ${body} =~ ^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+$ ]] || die "invalid characters in exact atom: ${atom}"
    ATOM_CPV=${body}
}

validate_archive_for_cpv() {
    local cpv=$1
    local record rel_path expected_size expected_md5 archive actual_size actual_md5
    local -a records=()
    mapfile -t records < <(
        awk -v wanted="${cpv}" '
            BEGIN { RS=""; FS="\n" }
            {
                cpv=""; path=""; size=""; md5=""
                for (i=1; i<=NF; i++) {
                    if ($i ~ /^CPV: /) cpv=substr($i, 6)
                    else if ($i ~ /^PATH: /) path=substr($i, 7)
                    else if ($i ~ /^SIZE: /) size=substr($i, 7)
                    else if ($i ~ /^MD5: /) md5=substr($i, 6)
                }
                if (cpv == wanted) print path "|" size "|" md5
            }
        ' "${PACKAGES_INDEX}"
    )
    ((${#records[@]} == 1)) || die "expected exactly one protected binpkg record for ${cpv}; found ${#records[@]}"
    record=${records[0]}
    IFS='|' read -r rel_path expected_size expected_md5 <<<"${record}"
    [[ -n ${rel_path} ]] || die "protected Packages record has no PATH for ${cpv}"
    [[ ${rel_path} != /* && /${rel_path}/ != *'/../'* ]] || die "unsafe archive PATH for ${cpv}: ${rel_path}"
    archive=$(readlink -f -- "${RESOLVED_PKGDIR}/${rel_path}") || die "protected archive is absent for ${cpv}: ${rel_path}"
    path_is_within "${archive}" "${RESOLVED_PKGDIR}" || die "protected archive escapes PKGDIR for ${cpv}: ${archive}"
    assert_secure_path "${archive}" file
    [[ -r ${archive} ]] || die "protected archive is not readable for ${cpv}: ${archive}"
    if [[ -n ${expected_size} ]]; then
        [[ ${expected_size} =~ ^[0-9]+$ ]] || die "invalid SIZE in protected Packages record for ${cpv}"
        actual_size=$(stat -Lc '%s' -- "${archive}") || die "cannot size archive for ${cpv}"
        [[ ${actual_size} == "${expected_size}" ]] || die "archive SIZE mismatch for ${cpv}: expected ${expected_size}, got ${actual_size}"
    else
        warn "protected Packages record has no SIZE for ${cpv}"
    fi
    if [[ -n ${expected_md5} ]]; then
        [[ ${expected_md5} =~ ^[0-9a-fA-F]{32}$ ]] || die "invalid MD5 in protected Packages record for ${cpv}"
        actual_md5=$(md5sum -- "${archive}") || die "cannot hash archive for ${cpv}"
        actual_md5=${actual_md5%% *}
        [[ ${actual_md5,,} == "${expected_md5,,}" ]] || die "archive MD5 mismatch for ${cpv}"
    else
        warn "protected Packages record has no MD5 for ${cpv}"
    fi
    VALIDATED_ARCHIVE=${archive}
}

collect_protected_atoms() {
    local line
    local -n destination=$1
    destination=()
    if [[ -n ${CRITICAL_FILE} ]]; then
        assert_secure_path "${CRITICAL_FILE}" file
        while IFS= read -r line || [[ -n ${line} ]]; do
            line=${line%%#*}
            line=${line#"${line%%[![:space:]]*}"}
            line=${line%"${line##*[![:space:]]}"}
            [[ -n ${line} ]] || continue
            atom_to_cpv "${line}"
            destination+=("${line}")
        done <"${CRITICAL_FILE}"
    else
        mapfile -t destination < <(awk '/^CPV: / { print "=" substr($0, 6) }' "${PACKAGES_INDEX}" | LC_ALL=C sort -u)
    fi
    ((${#destination[@]} > 0)) || die "protected critical package set is empty"
}

optimization_env_content() {
    cat <<'EOF'
# Managed by scripts/optimization/recovery/rollback.sh.  Do not edit.
RECOVERY_OPTIMIZATION_DISABLED="1"
RECOVERY_COMPILER_LANE=""
PGO_INSTRUMENT="0"
PGO_DISABLE_USE="1"
PGO_USE_IF_AVAILABLE="0"
PGO_GENERATE="0"
PGO_USE="0"
BOLT_CAPTURE="0"
BOLT_DEPLOY="0"
BOLT_ENABLE="0"
AUTOFDO_ENABLE="0"
PROPELLER_ENABLE="0"
POLLY_FLAGS=""
AGGRO_OPT_FLAGS=""
OPT_FLAGS=""
CLEAN_FLAGS=""
BOLT_READY_FLAGS=""
BOLT_READY_LD_FLAGS=""
PROFILE_MAPPING_FLAGS=""
SECTION_FLAGS=""
SECTION_LD_FLAGS=""
VISIBILITY_FLAGS=""
CXX_VISIBILITY_FLAGS=""
OPT_REMARK_FLAGS=""
VTABLE_OPT_FLAGS=""
GLOBAL_PERF_FLAGS=""
LTO_FLAGS=""
LTO_UNI_FLAGS=""
UARG_FLAGS=""
RUNTIME_LINK_FLAGS=""
LD_OPT_FLAGS=""
LD_CLEAN_FLAGS=""
FORCED_LIBS=""
COMMON_FLAGS=""
CFLAGS=""
CXXFLAGS=""
FCFLAGS=""
FFLAGS=""
LDFLAGS=""
RUSTFLAGS=""
RUSTFLAGS_BOOTSTRAP=""
GOFLAGS=""
EOF
}

recovery_clang_libcxx_env_content() {
    cat <<'EOF'
# Managed by scripts/optimization/recovery/rollback.sh.  Do not edit.
# Conservative Clang recovery baseline. Keep the installed libc++ ABI and
# compiler-rt/libunwind/lld runtime policy while dropping all project PGO,
# BOLT, LTO, Polly, OpenMP, visibility, section-splitting, and remark flags.
RECOVERY_COMPILER_LANE="clang-libcxx"
COMMON_FLAGS="-O2 -pipe"
CFLAGS="${COMMON_FLAGS}"
CXXFLAGS="${COMMON_FLAGS} -stdlib=libc++"
FCFLAGS="${COMMON_FLAGS}"
FFLAGS="${COMMON_FLAGS}"
LDFLAGS="-fuse-ld=lld -rtlib=compiler-rt -unwindlib=libunwind -stdlib=libc++"
CC="clang"
CXX="clang++"
CPP="clang-cpp"
LD="ld.lld"
AR="llvm-ar"
NM="llvm-nm"
RANLIB="llvm-ranlib"
LIB_FLAGS="-stdlib=libc++"
RUNTIME_LINK_FLAGS="-fuse-ld=lld -rtlib=compiler-rt -unwindlib=libunwind -stdlib=libc++"
GCCLIB_FLAGS=""
EOF
}

recovery_gcc_env_content() {
    cat <<'EOF'
# Managed by scripts/optimization/recovery/rollback.sh.  Do not edit.
# Conservative GCC recovery baseline. Use GCC/libstdc++-compatible tools and
# flags only; no Clang runtime, PGO, BOLT, LTO, or project optimization axes.
RECOVERY_COMPILER_LANE="gcc-libstdcxx"
COMMON_FLAGS="-O2 -pipe"
CFLAGS="${COMMON_FLAGS}"
CXXFLAGS="${COMMON_FLAGS}"
FCFLAGS="${COMMON_FLAGS}"
FFLAGS="${COMMON_FLAGS}"
LDFLAGS=""
CC="gcc"
CXX="g++"
CPP="gcc -E"
LD="ld.bfd"
AR="gcc-ar"
NM="gcc-nm"
RANLIB="gcc-ranlib"
LIB_FLAGS=""
RUNTIME_LINK_FLAGS=""
GCCLIB_FLAGS=""
EOF
}

optimization_assignment_content() {
    local atom
    cat <<'EOF'
# Managed by scripts/optimization/recovery/rollback.sh.  Do not edit.
*/* recovery/optimization-off.conf recovery/clang-libcxx.conf
# Every package assigned to gcc.conf by the live policy is repeated below so
# it cannot inherit the global Clang recovery lane after the final 99-* file.
EOF
    for atom in "${RECOVERY_GCC_ATOMS[@]}"; do
        printf '%s recovery/gcc.conf\n' "${atom}"
    done
}

collect_recovery_gcc_atoms() {
    local package_env_dir=$1
    local path atom
    local -A seen=()
    local -a discovered=()
    for atom in "${RECOVERY_GCC_ATOMS[@]}"; do
        seen["${atom}"]=1
    done
    while IFS= read -r -d '' path; do
        [[ ${path##*/} != 99-recovery-optimization-off ]] || continue
        while IFS= read -r atom; do
            [[ -n ${atom} ]] || continue
            [[ ${atom} != *[[:space:]]* && ${atom} == */* ]] || die "unsafe GCC package.env selector discovered: ${atom}"
            seen["${atom}"]=1
        done < <(
            awk '
                /^[[:space:]]*#/ || NF < 2 { next }
                {
                    for (i=2; i<=NF; i++) {
                        if ($i == "gcc.conf" || $i ~ /\/gcc[.]conf$/) {
                            print $1
                            break
                        }
                    }
                }
            ' "${path}"
        )
    done < <(find "${package_env_dir}" -type f -print0)
    mapfile -t discovered < <(printf '%s\n' "${!seen[@]}" | LC_ALL=C sort)
    RECOVERY_GCC_ATOMS=("${discovered[@]}")
    ((${#RECOVERY_GCC_ATOMS[@]} > 0)) || die "recovery GCC assignment set is empty"
}

atomic_install_content() {
    local path=$1
    local mode=$2
    local producer=$3
    local permit_managed_upgrade=${4:-0}
    local directory temp
    directory=${path%/*}
    if ((DRY_RUN)); then
        log INFO "would atomically install ${path} (mode ${mode})"
        return 0
    fi
    mkdir -p -- "${directory}" || die "cannot create ${directory}"
    temp=$(mktemp "${directory}/.${PROGRAM}.XXXXXX") || die "cannot create temporary file in ${directory}"
    "${producer}" >"${temp}" || {
        rm -f -- "${temp}"
        die "cannot generate ${path}"
    }
    chmod "${mode}" "${temp}" || {
        rm -f -- "${temp}"
        die "cannot set mode on temporary ${path}"
    }
    if [[ -e ${path} ]]; then
        if cmp -s -- "${temp}" "${path}"; then
            rm -f -- "${temp}"
            return 0
        fi
        if ((permit_managed_upgrade)) && head -n 1 -- "${path}" | grep -Fxq '# Managed by scripts/optimization/recovery/rollback.sh.  Do not edit.'; then
            mv -T -- "${temp}" "${path}" || {
                rm -f -- "${temp}"
                die "cannot atomically upgrade managed file: ${path}"
            }
            log INFO "atomically upgraded prior managed recovery file: ${path}"
            return 0
        fi
        rm -f -- "${temp}"
        die "refusing to overwrite non-matching unmanaged file: ${path}"
    fi
    mv -T -- "${temp}" "${path}" || {
        rm -f -- "${temp}"
        die "cannot atomically install ${path}"
    }
}

is_optimization_assignment() {
    local path=$1
    local base=${path##*/}
    [[ ${base} == 99-recovery-optimization-off ]] && return 1
    case ${base} in
        50-global-pgo|50-pgo-generated|51-bolt-capture-generated|52-bolt-deploy-generated)
            return 0
            ;;
    esac
    if [[ ${base,,} =~ (pgo|bolt|autofdo|propeller).*(generated|active|deploy|capture)|(generated).*(pgo|bolt|autofdo|propeller) ]]; then
        return 0
    fi
    awk '
        /^[[:space:]]*#/ { next }
        {
            line=tolower($0)
            if (line ~ /(^|[[:space:]_\/.:-])(pgo|bolt|autofdo|propeller)([[:space:]_\/.:-]|$)/ ||
                line ~ /optimization\/[[:alnum:]_.\/-]+\.conf/) found=1
        }
        END { exit(found ? 0 : 1) }
    ' "${path}"
}

collect_optimization_assignments() {
    local package_env_dir=$1
    # Bash nameref intentionally receives the caller's array variable name.
    # shellcheck disable=SC2178
    local -n destination=$2
    local path relative
    local -A seen=()
    destination=()
    [[ -d ${package_env_dir} ]] || die "Portage package.env directory is absent: ${package_env_dir}"
    while IFS= read -r -d '' path; do
        if is_optimization_assignment "${path}"; then
            destination+=("${path}")
            seen["${path}"]=1
        fi
    done < <(find "${package_env_dir}" -type f -print0)
    for relative in "${EXTRA_ASSIGNMENTS[@]}"; do
        [[ ${relative} != /* && /${relative}/ != *'/../'* ]] || die "--assignment must be a safe path relative to package.env: ${relative}"
        path=${package_env_dir}/${relative}
        [[ -f ${path} ]] || die "requested package.env assignment is absent: ${path}"
        if [[ -z ${seen[${path}]:-} ]]; then
            destination+=("${path}")
            seen["${path}"]=1
        fi
    done
}

kill_switch_is_active() {
    local config_root
    config_root=$(root_path /etc/portage)
    [[ -f ${STATE_ROOT}/recovery/optimization.disabled ]] || return 1
    [[ -f ${config_root}/env/recovery/optimization-off.conf ]] || return 1
    [[ -f ${config_root}/env/recovery/clang-libcxx.conf ]] || return 1
    [[ -f ${config_root}/env/recovery/gcc.conf ]] || return 1
    [[ -f ${config_root}/package.env/99-recovery-optimization-off ]] || return 1
    grep -Fxq '*/* recovery/optimization-off.conf recovery/clang-libcxx.conf' "${config_root}/package.env/99-recovery-optimization-off" &&
        grep -Fxq 'sys-devel/gcc recovery/gcc.conf' "${config_root}/package.env/99-recovery-optimization-off"
}

disable_optimization() {
    local config_root package_env_dir quarantine txn path relative target manifest marker marker_tmp
    local -a assignments=()
    config_root=$(root_path /etc/portage)
    package_env_dir=${config_root}/package.env
    collect_optimization_assignments "${package_env_dir}" assignments
    txn=$(date -u '+%Y%m%dT%H%M%SZ')-${$}
    quarantine=${config_root}/package.env.recovery-disabled/${txn}
    manifest=${STATE_ROOT}/recovery/disabled-${txn}.manifest
    marker=${STATE_ROOT}/recovery/optimization.disabled

    collect_recovery_gcc_atoms "${package_env_dir}"
    atomic_install_content "${config_root}/env/recovery/optimization-off.conf" 0644 optimization_env_content 1
    atomic_install_content "${config_root}/env/recovery/clang-libcxx.conf" 0644 recovery_clang_libcxx_env_content 1
    atomic_install_content "${config_root}/env/recovery/gcc.conf" 0644 recovery_gcc_env_content 1
    atomic_install_content "${package_env_dir}/99-recovery-optimization-off" 0644 optimization_assignment_content 1

    if ((DRY_RUN)); then
        log INFO "would atomically create kill-switch marker ${marker}"
        for path in "${assignments[@]}"; do
            log INFO "would quarantine optimization assignment ${path}"
        done
        SIMULATED_KILL_SWITCH=1
        return 0
    fi

    mkdir -p -- "${STATE_ROOT}/recovery" "${quarantine}" || die "cannot create recovery transaction directories"
    marker_tmp=$(mktemp "${STATE_ROOT}/recovery/.optimization.disabled.XXXXXX") || die "cannot stage kill-switch marker"
    {
        printf 'version=%s\n' "${VERSION}"
        printf 'disabled_at=%s\n' "$(timestamp)"
        printf 'transaction=%s\n' "${txn}"
        printf 'portage_root=%s\n' "${PORTAGE_ROOT}"
    } >"${marker_tmp}"
    chmod 0644 "${marker_tmp}"
    if [[ -e ${marker} ]]; then
        rm -f -- "${marker_tmp}"
    else
        mv -T -- "${marker_tmp}" "${marker}" || die "cannot atomically activate kill-switch marker"
    fi

    : >"${manifest}.tmp"
    chmod 0600 "${manifest}.tmp"
    for path in "${assignments[@]}"; do
        relative=${path#"${package_env_dir}/"}
        target=${quarantine}/${relative}
        mkdir -p -- "${target%/*}" || die "cannot create quarantine path for ${relative}"
        printf '%s\t%s\t%s\n' "$(sha256sum -- "${path}" | awk '{print $1}')" "${path}" "${target}" >>"${manifest}.tmp"
        if ! mv -T -- "${path}" "${target}"; then
            die "failed to atomically quarantine ${path}; kill switch remains active"
        fi
        log INFO "quarantined optimization assignment ${path} -> ${target}"
    done
    mv -T -- "${manifest}.tmp" "${manifest}" || die "cannot publish disable transaction manifest"
    kill_switch_is_active || die "optimization kill switch failed post-install verification"
    collect_optimization_assignments "${package_env_dir}" assignments
    ((${#assignments[@]} == 0)) || die "active optimization assignments remain after disable"
    log INFO "optimization is disabled; transaction manifest=${manifest}"
}

require_kill_switch() {
    if ((DRY_RUN && SIMULATED_KILL_SWITCH)); then
        return 0
    fi
    kill_switch_is_active || die "optimization kill switch is not active; run '${PROGRAM} disable' first"
}

restore_atoms() {
    local emerge atom
    local -a atoms=("$@")
    resolve_pkgdir
    for atom in "${atoms[@]}"; do
        atom_to_cpv "${atom}"
        validate_archive_for_cpv "${ATOM_CPV}"
        log INFO "validated protected archive for ${atom}: ${VALIDATED_ARCHIVE}"
    done
    emerge=$(resolve_tool emerge)
    run_mutation env \
        "ROOT=${PORTAGE_ROOT}" \
        "PORTAGE_CONFIGROOT=${PORTAGE_ROOT}" \
        "PKGDIR=${RESOLVED_PKGDIR}" \
        'PORTAGE_BINHOST=' \
        'GENTOO_MIRRORS=' \
        'FETCHCOMMAND=/bin/false' \
        'RESUMECOMMAND=/bin/false' \
        'PGO_INSTRUMENT=0' \
        'PGO_DISABLE_USE=1' \
        'PGO_USE_IF_AVAILABLE=0' \
        'BOLT_CAPTURE=0' \
        'BOLT_DEPLOY=0' \
        "${emerge}" \
        --ignore-default-opts \
        --ask=n \
        --autounmask=n \
        --buildpkg=n \
        --getbinpkg=n \
        --usepkgonly \
        --binpkg-changed-deps=n \
        --binpkg-respect-use=n \
        --oneshot \
        --verbose \
        "${atoms[@]}"
}

restore_critical() {
    local -a atoms=()
    resolve_pkgdir
    collect_protected_atoms atoms
    log INFO "critical restore set contains ${#atoms[@]} exact CPVs"
    restore_atoms "${atoms[@]}"
}

run_preserved_rebuild() {
    local emerge
    require_kill_switch
    resolve_pkgdir
    emerge=$(resolve_tool emerge)
    run_mutation env \
        "ROOT=${PORTAGE_ROOT}" \
        "PORTAGE_CONFIGROOT=${PORTAGE_ROOT}" \
        "PKGDIR=${RESOLVED_PKGDIR}" \
        'PORTAGE_BINHOST=' \
        'GENTOO_MIRRORS=' \
        'FETCHCOMMAND=/bin/false' \
        'RESUMECOMMAND=/bin/false' \
        'RECOVERY_OPTIMIZATION_DISABLED=1' \
        'PGO_INSTRUMENT=0' \
        'PGO_DISABLE_USE=1' \
        'PGO_USE_IF_AVAILABLE=0' \
        'BOLT_CAPTURE=0' \
        'BOLT_DEPLOY=0' \
        'RUSTFLAGS=' \
        'GOFLAGS=' \
        "${emerge}" \
        --ignore-default-opts \
        --ask=n \
        --autounmask=n \
        --buildpkg=n \
        --getbinpkg=n \
        --usepkgonly \
        --binpkg-changed-deps=n \
        --binpkg-respect-use=n \
        --oneshot \
        --verbose \
        @preserved-rebuild
}

verify_sha256() {
    local path=$1
    local expected=$2
    local label=$3
    local actual
    [[ -f ${path} && -s ${path} ]] || die "${label} is absent or empty: ${path}"
    if [[ ${expected} == none ]]; then
        warn "no expected SHA-256 configured for ${label}: ${path}"
        return 0
    fi
    actual=$(sha256sum -- "${path}") || die "cannot hash ${label}: ${path}"
    actual=${actual%% *}
    [[ ${actual,,} == "${expected}" ]] || die "${label} SHA-256 mismatch: expected ${expected}, got ${actual}"
    log INFO "verified ${label} SHA-256 ${actual}: ${path}"
}

check_initramfs_file() {
    local path=$1
    local lsinitrd
    [[ -f ${path} && -s ${path} ]] || die "initramfs is absent or empty: ${path}"
    lsinitrd=$(resolve_tool lsinitrd)
    log INFO "checking initramfs structure with ${lsinitrd}: ${path}"
    "${lsinitrd}" "${path}" >/dev/null || die "lsinitrd rejected ${path}"
}

handle_initramfs() {
    local dracut output canonical_output canonical_known
    local -a dracut_args=()
    verify_sha256 "${KNOWN_GOOD_INITRAMFS}" "${KNOWN_GOOD_INITRAMFS_SHA256}" "known-good initramfs"
    check_initramfs_file "${KNOWN_GOOD_INITRAMFS}"
    ((REGENERATE_INITRAMFS)) || {
        log INFO "known-good initramfs is valid; regeneration was not requested"
        return 0
    }
    if [[ -n ${INITRAMFS_OUTPUT} ]]; then
        output=${INITRAMFS_OUTPUT}
    else
        output=${ESP_ROOT}/EFI/Gentoo/initramfs-${KNOWN_GOOD_KERNEL_VERSION}.recovery-$(date -u '+%Y%m%dT%H%M%SZ').img
    fi
    canonical_output=$(readlink -m -- "${output}")
    canonical_known=$(readlink -m -- "${KNOWN_GOOD_INITRAMFS}")
    if [[ ${canonical_output} == "${canonical_known}" && ${OVERWRITE_KNOWN_GOOD} -ne 1 ]]; then
        die "refusing to overwrite the known-good initramfs without --overwrite-known-good"
    fi
    if [[ -e ${output} && ${OVERWRITE_INITRAMFS} -ne 1 ]]; then
        die "refusing to overwrite existing initramfs output without --overwrite-initramfs: ${output}"
    fi
    if [[ ${canonical_output} == "${canonical_known}" && ${OVERWRITE_INITRAMFS} -ne 1 ]]; then
        die "--overwrite-known-good also requires --overwrite-initramfs"
    fi
    path_is_within "${output}" "${ESP_ROOT}" || die "initramfs output must remain below the configured ESP: ${output}"
    dracut=$(resolve_tool dracut)
    dracut_args=("${dracut}" --kver "${KNOWN_GOOD_KERNEL_VERSION}")
    if [[ ${PORTAGE_ROOT} != / ]]; then
        dracut_args+=(--sysroot "${PORTAGE_ROOT}")
    fi
    if [[ -e ${output} ]]; then
        dracut_args+=(--force)
    fi
    dracut_args+=("${output}")
    run_mutation mkdir -p -- "${output%/*}"
    run_mutation "${dracut_args[@]}"
    if ((DRY_RUN)); then
        log INFO "would validate regenerated initramfs ${output}"
    else
        check_initramfs_file "${output}"
        log INFO "regenerated recovery initramfs without changing known-good image: ${output}"
    fi
}

efi_entry_output() {
    local efibootmgr=$1
    "${efibootmgr}" -v
}

ascii_utf16le_hex() {
    local text=$1
    local char code encoded=
    local index
    LC_ALL=C
    for ((index = 0; index < ${#text}; index++)); do
        char=${text:index:1}
        printf -v code '%d' "'${char}"
        printf -v encoded '%s%02x00' "${encoded}" "${code}"
    done
    printf '%s' "${encoded}"
}

verify_efi_entry() {
    local efibootmgr output entry line expected_relative expected_efi
    local initramfs_relative initramfs_efi initramfs_hex
    efibootmgr=$(resolve_tool efibootmgr)
    output=$(efi_entry_output "${efibootmgr}") || die "efibootmgr could not read EFI variables"
    entry=
    while IFS= read -r line; do
        if [[ ${line} == "Boot${KNOWN_GOOD_BOOTNUM}"* ]]; then
            entry=${line}
            break
        fi
    done <<<"${output}"
    [[ -n ${entry} ]] || die "known-good EFI entry Boot${KNOWN_GOOD_BOOTNUM} is absent"
    path_is_within "${KNOWN_GOOD_KERNEL_IMAGE}" "${ESP_ROOT}" || die "known-good kernel path is outside configured ESP"
    expected_relative=${KNOWN_GOOD_KERNEL_IMAGE#"${ESP_ROOT}"}
    expected_efi=${expected_relative//\//\\}
    [[ ${entry,,} == *"${expected_efi,,}"* ]] || die "Boot${KNOWN_GOOD_BOOTNUM} does not reference ${expected_efi}"
    path_is_within "${KNOWN_GOOD_INITRAMFS}" "${ESP_ROOT}" || die "known-good initramfs path is outside configured ESP"
    initramfs_relative=${KNOWN_GOOD_INITRAMFS#"${ESP_ROOT}"}
    initramfs_efi=${initramfs_relative//\//\\}
    initramfs_hex=$(ascii_utf16le_hex "${initramfs_efi}")
    [[ ${entry,,} == *"${initramfs_hex}"* ]] || die "Boot${KNOWN_GOOD_BOOTNUM} load options do not reference ${initramfs_efi}"
    log INFO "verified EFI entry: ${entry}"
}

arm_bootnext() {
    local efibootmgr output next line
    verify_sha256 "${KNOWN_GOOD_KERNEL_IMAGE}" "${KNOWN_GOOD_KERNEL_SHA256}" "known-good EFI kernel"
    verify_sha256 "${KNOWN_GOOD_INITRAMFS}" "${KNOWN_GOOD_INITRAMFS_SHA256}" "known-good initramfs"
    check_initramfs_file "${KNOWN_GOOD_INITRAMFS}"
    verify_efi_entry
    efibootmgr=$(resolve_tool efibootmgr)
    run_mutation "${efibootmgr}" -n "${KNOWN_GOOD_BOOTNUM}"
    if ((DRY_RUN)); then
        log INFO "would re-read EFI variables and require BootNext=${KNOWN_GOOD_BOOTNUM}"
        return 0
    fi
    output=$(efi_entry_output "${efibootmgr}") || die "efibootmgr could not verify BootNext"
    next=
    while IFS= read -r line; do
        if [[ ${line} == 'BootNext: '* ]]; then
            next=${line#BootNext: }
            next=${next^^}
            break
        fi
    done <<<"${output}"
    [[ ${next} == "${KNOWN_GOOD_BOOTNUM}" ]] || die "BootNext verification failed: expected ${KNOWN_GOOD_BOOTNUM}, got ${next:-unset}"
    log INFO "verified BootNext=${next}; reboot was not performed"
}

filesystem_path_to_efi() {
    local path=$1
    local relative
    path_is_within "${path}" "${ESP_ROOT}" || die "EFI asset is outside configured ESP: ${path}"
    relative=${path#"${ESP_ROOT}"}
    printf '%s' "${relative//\//\\}"
}

derive_efi_disk_and_part() {
    local findmnt lsblk source
    local -a values=()
    if [[ -n ${EFI_DISK} || -n ${EFI_PART} ]]; then
        [[ -n ${EFI_DISK} && -n ${EFI_PART} ]] || die "--efi-disk and --efi-part must be supplied together"
    else
        findmnt=$(resolve_tool findmnt)
        lsblk=$(resolve_tool lsblk)
        mapfile -t values < <("${findmnt}" --noheadings --raw --output SOURCE --target "${ESP_ROOT}")
        ((${#values[@]} == 1)) || die "could not identify one block source for ESP ${ESP_ROOT}"
        source=${values[0]//[[:space:]]/}
        [[ ${source} == /dev/* ]] || die "ESP source is not a block device path: ${source}"
        mapfile -t values < <("${lsblk}" --noheadings --raw --output PKNAME "${source}")
        ((${#values[@]} == 1 && ${#values[0]} > 0)) || die "could not identify parent disk for ${source}"
        EFI_DISK=/dev/${values[0]//[[:space:]]/}
        mapfile -t values < <("${lsblk}" --noheadings --raw --output PARTN "${source}")
        ((${#values[@]} == 1)) || die "could not identify partition number for ${source}"
        EFI_PART=${values[0]//[[:space:]]/}
    fi
    [[ ${EFI_DISK} == /dev/* ]] || die "EFI disk must be a /dev path: ${EFI_DISK}"
    [[ ${EFI_PART} =~ ^[1-9][0-9]*$ ]] || die "EFI partition must be a positive integer"
    if [[ ${PORTAGE_ROOT} == / ]]; then
        [[ -b ${EFI_DISK} ]] || die "EFI parent disk is not a block device: ${EFI_DISK}"
    fi
    log INFO "EFI entry target disk=${EFI_DISK} partition=${EFI_PART}"
}

prepare_microcode_images() {
    local candidate
    if ((${#MICROCODE_IMAGES[@]} == 0)); then
        for candidate in \
            "${ESP_ROOT}/EFI/Gentoo/amd-uc.img" \
            "${ESP_ROOT}/EFI/Gentoo/intel-uc.img"; do
            [[ -f ${candidate} && -s ${candidate} ]] && MICROCODE_IMAGES+=("${candidate}")
        done
    fi
    ((${#MICROCODE_IMAGES[@]} > 0)) || die "no microcode image was found; use --microcode with an ESP path"
    for candidate in "${MICROCODE_IMAGES[@]}"; do
        path_is_within "${candidate}" "${ESP_ROOT}" || die "microcode image is outside configured ESP: ${candidate}"
        [[ -f ${candidate} && -s ${candidate} ]] || die "microcode image is absent or empty: ${candidate}"
    done
}

build_recovery_cmdline() {
    local recovery_initramfs=$1
    local mode=${2:-normal}
    local raw token efi_path
    local -a original=()
    local -a sanitized=()
    [[ -r ${CMDLINE_FILE} ]] || die "kernel command line is not readable: ${CMDLINE_FILE}"
    raw=$(<"${CMDLINE_FILE}")
    [[ ${raw} != *$'\n'* && ${raw} != *$'\r'* ]] || die "kernel command line must contain exactly one line"
    IFS=$' \t' read -r -a original <<<"${raw}"
    for token in "${original[@]}"; do
        [[ -n ${token} ]] || continue
        [[ ${token} == initrd=* ]] && continue
        if [[ ${mode} == rescue && ( ${token} == rd.break || ${token} == rd.break=* ) ]]; then
            continue
        fi
        sanitized+=("${token}")
    done
    for token in "${MICROCODE_IMAGES[@]}"; do
        efi_path=$(filesystem_path_to_efi "${token}")
        sanitized+=("initrd=${efi_path}")
    done
    efi_path=$(filesystem_path_to_efi "${recovery_initramfs}")
    sanitized+=("initrd=${efi_path}")
    if [[ ${mode} == rescue ]]; then
        sanitized+=(rd.break=pre-mount)
    fi
    PRESERVED_CMDLINE=$(printf '%s ' "${sanitized[@]}")
    PRESERVED_CMDLINE=${PRESERVED_CMDLINE% }
    [[ -n ${PRESERVED_CMDLINE} ]] || die "sanitized recovery command line is empty"
    ((${#PRESERVED_CMDLINE} <= 4096)) || die "recovery command line exceeds 4096 bytes"
    log INFO "sanitized EFI load options: ${PRESERVED_CMDLINE}"
}

collect_boot_numbers() {
    local output=$1
    # Bash nameref intentionally receives the caller's array variable name.
    # shellcheck disable=SC2178
    local -n destination=$2
    local line number
    destination=()
    while IFS= read -r line; do
        [[ ${line} == Boot????* ]] || continue
        number=${line:4:4}
        [[ ${number} =~ ^[0-9A-Fa-f]{4}$ ]] || continue
        destination+=("${number^^}")
    done <<<"${output}"
}

collect_matching_efi_entries() {
    local output=$1
    local loader=$2
    local label=$3
    # Bash nameref intentionally receives the caller's array variable name.
    # shellcheck disable=SC2178
    local -n destination=$4
    local line
    destination=()
    while IFS= read -r line; do
        [[ ${line} == Boot????* ]] || continue
        if [[ ${line,,} == *"${loader,,}"* && ${line} == *"${label}"* ]]; then
            destination+=("${line}")
        fi
    done <<<"${output}"
}

verify_new_recovery_entry() {
    local before=$1
    local after=$2
    local loader=$3
    local recovery_initramfs=$4
    local line bootnum order_line expected_hex asset efi_path
    local -a before_numbers=()
    local -a matches=()
    local -a after_numbers=()
    local -A after_number_set=()
    local -a order=()
    collect_boot_numbers "${before}" before_numbers
    collect_matching_efi_entries "${after}" "${loader}" "${EFI_LABEL}" matches
    ((${#matches[@]} == 1)) || die "expected exactly one matching custom recovery EFI entry; found ${#matches[@]}"
    line=${matches[0]}
    bootnum=${line:4:4}
    bootnum=${bootnum^^}
    while IFS= read -r order_line; do
        if [[ ${order_line} == 'BootOrder: '* ]]; then
            order_line=${order_line#BootOrder: }
            break
        fi
        order_line=
    done <<<"${after}"
    [[ -n ${order_line} ]] || die "EFI BootOrder is absent after recovery entry creation"
    IFS=',' read -r -a order <<<"${order_line}"
    ((${#order[@]} >= 2)) || die "EFI BootOrder has no index 1 after entry creation"
    [[ ${order[1]^^} == "${bootnum}" ]] || die "new recovery entry Boot${bootnum} is not at BootOrder index 1"
    for asset in "${MICROCODE_IMAGES[@]}" "${recovery_initramfs}"; do
        efi_path=$(filesystem_path_to_efi "${asset}")
        expected_hex=$(ascii_utf16le_hex "${efi_path}")
        [[ ${line,,} == *"${expected_hex}"* ]] || die "new recovery EFI load options omit ${efi_path}"
    done
    collect_boot_numbers "${after}" after_numbers
    for bootnum in "${after_numbers[@]}"; do
        after_number_set["${bootnum}"]=1
    done
    for bootnum in "${before_numbers[@]}"; do
        [[ -n ${after_number_set[${bootnum}]:-} ]] || die "pre-existing EFI entry Boot${bootnum} disappeared"
    done
    log INFO "verified one custom recovery entry at BootOrder index 1: ${line}"
}

write_boot_preservation_manifest() {
    local recovery_dir=$1
    local boot_entry=$2
    local manifest temp source target digest bootnum kernel_digest initramfs_digest
    local -a names=(vmlinuz initramfs config System.map)
    local -a sources=("$3" "$4" "$5" "$6")
    local -a targets=("$7" "$8" "$9" "${10}")
    bootnum=${boot_entry:4:4}
    [[ ${bootnum} =~ ^[0-9A-Fa-f]{4}$ ]] || die "cannot derive boot number for preservation manifest"
    bootnum=${bootnum^^}
    kernel_digest=$(sha256sum -- "${targets[0]}")
    kernel_digest=${kernel_digest%% *}
    initramfs_digest=$(sha256sum -- "${targets[1]}")
    initramfs_digest=${initramfs_digest%% *}
    manifest=${STATE_ROOT}/recovery/boot-${RECOVERY_TAG}.manifest
    mkdir -p -- "${manifest%/*}" || die "cannot create boot recovery state directory"
    temp=$(mktemp "${manifest%/*}/.boot-${RECOVERY_TAG}.XXXXXX") || die "cannot stage boot preservation manifest"
    {
        printf 'version=%s\n' "${VERSION}"
        printf 'created_at=%s\n' "$(timestamp)"
        printf 'generation_type=independent\n'
        printf 'bootnum=%s\n' "${bootnum}"
        printf 'kernel_version=%s\n' "${PRESERVE_KERNEL_VERSION}"
        printf 'recovery_tag=%s\n' "${RECOVERY_TAG}"
        printf 'destination=%s\n' "${recovery_dir}"
        printf 'efi_entry=%s\n' "${boot_entry}"
        printf 'cmdline=%s\n' "${PRESERVED_CMDLINE}"
        for index in "${!names[@]}"; do
            source=${sources[index]}
            target=${targets[index]}
            digest=$(sha256sum -- "${target}")
            digest=${digest%% *}
            printf '%s_source=%s\n' "${names[index]}" "${source}"
            printf '%s_target=%s\n' "${names[index]}" "${target}"
            printf '%s_sha256=%s\n' "${names[index]}" "${digest}"
        done
        printf 'kernel_image=%s\n' "${targets[0]}"
        printf 'kernel_sha256=%s\n' "${kernel_digest}"
        printf 'initramfs=%s\n' "${targets[1]}"
        printf 'initramfs_sha256=%s\n' "${initramfs_digest}"
    } >"${temp}"
    chmod 0600 "${temp}"
    [[ ! -e ${manifest} ]] || die "boot preservation manifest already exists: ${manifest}"
    mv -T -- "${temp}" "${manifest}" || die "cannot publish boot preservation manifest"
    log INFO "boot preservation manifest=${manifest}"
    write_authoritative_candidate_manifest \
        "${bootnum}" "${PRESERVE_KERNEL_VERSION}" \
        "${targets[0]}" "${kernel_digest}" \
        "${targets[1]}" "${initramfs_digest}"
}

write_authoritative_candidate_manifest() {
    local bootnum=$1
    local kernel_version=$2
    local kernel_image=$3
    local kernel_sha256=$4
    local initramfs=$5
    local initramfs_sha256=$6
    local candidate_dir candidate temp authoritative
    candidate_dir=${STATE_ROOT}/recovery/authoritative-candidates
    candidate=${candidate_dir}/${RECOVERY_TAG}.manifest
    authoritative=${STATE_ROOT}/recovery/authoritative-known-good.manifest
    mkdir -p -- "${candidate_dir}" || die "cannot create authoritative manifest candidate directory"
    chmod 0700 "${candidate_dir}" || die "cannot protect authoritative manifest candidate directory"
    temp=$(mktemp "${candidate_dir}/.${RECOVERY_TAG}.XXXXXX") || die "cannot stage authoritative manifest candidate"
    {
        printf 'version=1\n'
        printf 'generation_type=independent\n'
        printf 'bootnum=%s\n' "${bootnum}"
        printf 'kernel_version=%s\n' "${kernel_version}"
        printf 'kernel_image=%s\n' "${kernel_image}"
        printf 'kernel_sha256=%s\n' "${kernel_sha256}"
        printf 'initramfs=%s\n' "${initramfs}"
        printf 'initramfs_sha256=%s\n' "${initramfs_sha256}"
    } >"${temp}"
    chmod 0600 "${temp}"
    [[ ! -e ${candidate} ]] || die "authoritative manifest candidate already exists: ${candidate}"
    mv -T -- "${temp}" "${candidate}" || die "cannot publish authoritative manifest candidate"
    log INFO "loader-compatible authoritative manifest candidate=${candidate}"
    log INFO "after a successful boot through Boot${bootnum}, promote with: install -o root -g root -m 0600 ${candidate} ${authoritative}"
}

preserve_boot_assets() {
    local source_dir destination source_kernel source_initramfs source_config source_map
    local target_kernel target_initramfs target_config target_map loader
    local efibootmgr before after entry
    local source target source_hash target_hash
    local -a matches=()
    local -a sources=()
    local -a targets=()

    source_dir=${ESP_ROOT}/EFI/Gentoo
    destination=${source_dir}/recovery/${RECOVERY_TAG}
    source_kernel=${source_dir}/vmlinuz-${PRESERVE_KERNEL_VERSION}.efi
    source_initramfs=${source_dir}/initramfs-${PRESERVE_KERNEL_VERSION}.img
    source_config=${source_dir}/config-${PRESERVE_KERNEL_VERSION}
    source_map=${source_dir}/System.map-${PRESERVE_KERNEL_VERSION}
    target_kernel=${destination}/vmlinuz-${PRESERVE_KERNEL_VERSION}.efi
    target_initramfs=${destination}/initramfs-${PRESERVE_KERNEL_VERSION}.img
    target_config=${destination}/config-${PRESERVE_KERNEL_VERSION}
    target_map=${destination}/System.map-${PRESERVE_KERNEL_VERSION}
    sources=("${source_kernel}" "${source_initramfs}" "${source_config}" "${source_map}")
    targets=("${target_kernel}" "${target_initramfs}" "${target_config}" "${target_map}")

    [[ ! -e ${destination} ]] || die "recovery tag is not unique; destination exists: ${destination}"
    for source in "${sources[@]}"; do
        [[ -f ${source} && -s ${source} ]] || die "current boot asset is absent or empty: ${source}"
        log INFO "current boot asset SHA-256 $(sha256sum -- "${source}" | awk '{print $1}'): ${source}"
    done
    check_initramfs_file "${source_initramfs}"
    prepare_microcode_images
    build_recovery_cmdline "${target_initramfs}"
    derive_efi_disk_and_part
    loader=$(filesystem_path_to_efi "${target_kernel}")
    efibootmgr=$(resolve_tool efibootmgr)
    before=$(efi_entry_output "${efibootmgr}") || die "efibootmgr could not capture pre-create state"
    collect_matching_efi_entries "${before}" "${loader}" "${EFI_LABEL}" matches
    ((${#matches[@]} == 0)) || die "a matching recovery EFI entry already exists"

    log INFO "preserve-boot destination=${destination} label=${EFI_LABEL} loader=${loader}"
    run_mutation mkdir --mode=0700 --parents -- "${destination}"
    for index in "${!sources[@]}"; do
        run_mutation cp --preserve=timestamps -- "${sources[index]}" "${targets[index]}"
    done
    run_mutation sync -f "${destination}"
    if ((DRY_RUN == 0)); then
        for index in "${!sources[@]}"; do
            source_hash=$(sha256sum -- "${sources[index]}")
            source_hash=${source_hash%% *}
            target_hash=$(sha256sum -- "${targets[index]}")
            target_hash=${target_hash%% *}
            [[ ${source_hash} == "${target_hash}" ]] || die "preserved boot asset hash mismatch: ${targets[index]}"
        done
        check_initramfs_file "${target_initramfs}"
    fi

    run_mutation "${efibootmgr}" \
        --create \
        --index 1 \
        --disk "${EFI_DISK}" \
        --part "${EFI_PART}" \
        --label "${EFI_LABEL}" \
        --loader "${loader}" \
        --unicode "${PRESERVED_CMDLINE}"
    if ((DRY_RUN)); then
        log INFO "preview complete: no files or EFI variables were changed; rerun with --execute to apply"
        return 0
    fi
    after=$(efi_entry_output "${efibootmgr}") || die "efibootmgr could not capture post-create state"
    verify_new_recovery_entry "${before}" "${after}" "${loader}" "${target_initramfs}"
    collect_matching_efi_entries "${after}" "${loader}" "${EFI_LABEL}" matches
    entry=${matches[0]}
    write_boot_preservation_manifest \
        "${destination}" "${entry}" \
        "${source_kernel}" "${source_initramfs}" "${source_config}" "${source_map}" \
        "${target_kernel}" "${target_initramfs}" "${target_config}" "${target_map}"
}

verify_new_rescue_entry() {
    local before=$1
    local after=$2
    local loader=$3
    local line before_order='' after_order='' asset efi_path expected_hex bootnum
    local -a matches=()
    local -a before_numbers=()
    local -a after_numbers=()
    local -A after_number_set=()
    collect_matching_efi_entries "${after}" "${loader}" "${EFI_LABEL}" matches
    ((${#matches[@]} == 1)) || die "expected exactly one matching custom rescue EFI entry; found ${#matches[@]}"
    line=${matches[0]}
    while IFS= read -r bootnum; do
        if [[ ${bootnum} == 'BootOrder: '* ]]; then
            before_order=${bootnum#BootOrder: }
            break
        fi
    done <<<"${before}"
    while IFS= read -r bootnum; do
        if [[ ${bootnum} == 'BootOrder: '* ]]; then
            after_order=${bootnum#BootOrder: }
            break
        fi
    done <<<"${after}"
    [[ -n ${before_order} && ${after_order} == "${before_order}" ]] || die "rescue --create-only unexpectedly changed BootOrder"
    for asset in "${MICROCODE_IMAGES[@]}" "${KNOWN_GOOD_INITRAMFS}"; do
        efi_path=$(filesystem_path_to_efi "${asset}")
        expected_hex=$(ascii_utf16le_hex "${efi_path}")
        [[ ${line,,} == *"${expected_hex}"* ]] || die "new rescue EFI load options omit ${efi_path}"
    done
    expected_hex=$(ascii_utf16le_hex 'rd.break=pre-mount')
    [[ ${line,,} == *"${expected_hex}"* ]] || die "new rescue EFI load options omit rd.break=pre-mount"
    collect_boot_numbers "${before}" before_numbers
    collect_boot_numbers "${after}" after_numbers
    for bootnum in "${after_numbers[@]}"; do
        after_number_set["${bootnum}"]=1
    done
    for bootnum in "${before_numbers[@]}"; do
        [[ -n ${after_number_set[${bootnum}]:-} ]] || die "pre-existing EFI entry Boot${bootnum} disappeared"
    done
    log INFO "verified one BootOrder-neutral custom rescue entry: ${line}"
}

write_rescue_entry_manifest() {
    local entry=$1
    local manifest temp
    manifest=${STATE_ROOT}/recovery/rescue-entry-${RECOVERY_TAG}.manifest
    mkdir -p -- "${manifest%/*}" || die "cannot create rescue-entry state directory"
    temp=$(mktemp "${manifest%/*}/.rescue-entry-${RECOVERY_TAG}.XXXXXX") || die "cannot stage rescue-entry manifest"
    {
        printf 'version=%s\n' "${VERSION}"
        printf 'created_at=%s\n' "$(timestamp)"
        printf 'recovery_tag=%s\n' "${RECOVERY_TAG}"
        printf 'kernel=%s\n' "${KNOWN_GOOD_KERNEL_IMAGE}"
        printf 'kernel_sha256=%s\n' "${KNOWN_GOOD_KERNEL_SHA256}"
        printf 'initramfs=%s\n' "${KNOWN_GOOD_INITRAMFS}"
        printf 'initramfs_sha256=%s\n' "${KNOWN_GOOD_INITRAMFS_SHA256}"
        printf 'efi_entry=%s\n' "${entry}"
        printf 'cmdline=%s\n' "${PRESERVED_CMDLINE}"
    } >"${temp}"
    chmod 0600 "${temp}"
    [[ ! -e ${manifest} ]] || die "rescue-entry manifest already exists: ${manifest}"
    mv -T -- "${temp}" "${manifest}" || die "cannot publish rescue-entry manifest"
    log INFO "rescue-entry manifest=${manifest}"
}

create_rescue_entry() {
    local efibootmgr loader before after entry
    local -a matches=()
    verify_sha256 "${KNOWN_GOOD_KERNEL_IMAGE}" "${KNOWN_GOOD_KERNEL_SHA256}" "known-good EFI kernel"
    verify_sha256 "${KNOWN_GOOD_INITRAMFS}" "${KNOWN_GOOD_INITRAMFS_SHA256}" "known-good initramfs"
    check_initramfs_file "${KNOWN_GOOD_INITRAMFS}"
    prepare_microcode_images
    build_recovery_cmdline "${KNOWN_GOOD_INITRAMFS}" rescue
    derive_efi_disk_and_part
    loader=$(filesystem_path_to_efi "${KNOWN_GOOD_KERNEL_IMAGE}")
    efibootmgr=$(resolve_tool efibootmgr)
    before=$(efi_entry_output "${efibootmgr}") || die "efibootmgr could not capture pre-create rescue state"
    collect_matching_efi_entries "${before}" "${loader}" "${EFI_LABEL}" matches
    ((${#matches[@]} == 0)) || die "a matching rescue EFI entry already exists"
    log INFO "rescue-entry label=${EFI_LABEL} loader=${loader}; no boot assets will be copied or overwritten"
    run_mutation "${efibootmgr}" \
        --create-only \
        --disk "${EFI_DISK}" \
        --part "${EFI_PART}" \
        --label "${EFI_LABEL}" \
        --loader "${loader}" \
        --unicode "${PRESERVED_CMDLINE}"
    if ((DRY_RUN)); then
        log INFO "preview complete: no files, BootOrder, or EFI entries were changed; rerun with --execute to apply"
        return 0
    fi
    after=$(efi_entry_output "${efibootmgr}") || die "efibootmgr could not capture post-create rescue state"
    verify_new_rescue_entry "${before}" "${after}" "${loader}"
    collect_matching_efi_entries "${after}" "${loader}" "${EFI_LABEL}" matches
    entry=${matches[0]}
    write_rescue_entry_manifest "${entry}"
}

check_assignment_state() {
    local config_root package_env_dir
    local -a assignments=()
    config_root=$(root_path /etc/portage)
    package_env_dir=${config_root}/package.env
    collect_optimization_assignments "${package_env_dir}" assignments
    if ((${#assignments[@]})); then
        log INFO "${#assignments[@]} optimization assignment file(s) would be disabled:"
        printf '  %s\n' "${assignments[@]}"
    else
        log INFO "no active legacy/generated optimization assignments detected"
    fi
    if kill_switch_is_active; then
        log INFO "optimization kill switch is active"
    else
        log INFO "optimization kill switch is not active (expected before rollback)"
    fi
}

check_protected_set() {
    local atom
    local -a atoms=()
    resolve_pkgdir
    collect_protected_atoms atoms
    for atom in "${atoms[@]}"; do
        atom_to_cpv "${atom}"
        validate_archive_for_cpv "${ATOM_CPV}"
    done
    log INFO "validated ${#atoms[@]} exact protected binpkg record(s)"
    resolve_tool emerge >/dev/null
}

check_efi_recovery() {
    resolve_tool efibootmgr >/dev/null
    resolve_tool dracut >/dev/null
    verify_sha256 "${KNOWN_GOOD_KERNEL_IMAGE}" "${KNOWN_GOOD_KERNEL_SHA256}" "known-good EFI kernel"
    verify_sha256 "${KNOWN_GOOD_INITRAMFS}" "${KNOWN_GOOD_INITRAMFS_SHA256}" "known-good initramfs"
    check_initramfs_file "${KNOWN_GOOD_INITRAMFS}"
    verify_efi_entry
}

check_item() {
    local label=$1
    shift
    log INFO "CHECK ${label}"
    if ("$@"); then
        log INFO "PASS ${label}"
    else
        warn "FAIL ${label}"
        CHECK_FAILURES=$((CHECK_FAILURES + 1))
    fi
}

run_checks() {
    check_item package-env check_assignment_state
    check_item protected-binpkgs check_protected_set
    check_item efi-recovery check_efi_recovery
    if ((CHECK_FAILURES)); then
        die "rollback preflight failed ${CHECK_FAILURES} check group(s)"
    fi
    log INFO "rollback preflight passed"
}

case ${COMMAND} in
    check)
        run_checks
        ;;
    disable)
        disable_optimization
        ;;
    restore)
        restore_atoms "${COMMAND_ARGS[@]}"
        ;;
    restore-critical)
        restore_critical
        ;;
    preserved-rebuild)
        run_preserved_rebuild
        ;;
    initramfs)
        handle_initramfs
        ;;
    bootnext)
        arm_bootnext
        ;;
    preserve-boot)
        preserve_boot_assets
        ;;
    rescue-entry)
        create_rescue_entry
        ;;
    all)
        disable_optimization
        restore_critical
        run_preserved_rebuild
        handle_initramfs
        arm_bootnext
        ;;
    *)
        die "internal error: unhandled command ${COMMAND}"
        ;;
esac

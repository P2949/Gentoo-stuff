#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../../.." && pwd -P)
ROLLBACK=${REPOSITORY_ROOT}/scripts/optimization/recovery/rollback.sh
RETIRED_BOOT_EVIDENCE=${REPOSITORY_ROOT}/scripts/optimization/recovery/record-boot-evidence.sh

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_rejected() {
    local output
    if output=$("${ROLLBACK}" "$@" 2>&1); then
        fail "prohibited boot/kernel request succeeded: $*"
    fi
    [[ ${output} == *'permanently outside project authority'* ]] ||
        fail "prohibited request did not return the permanent authority error: $*: ${output}"
}

# The retired hook must refuse every invocation before consulting a marker,
# tool, ESP, firmware variable, or kernel asset.
if output=$("${RETIRED_BOOT_EVIDENCE}" --fixture-mode 2>&1); then
    fail 'retired boot-evidence entrypoint unexpectedly succeeded'
fi
[[ ${output} == *'permanently retired'* ]] ||
    fail "retired boot-evidence entrypoint returned an unrelated error: ${output}"

# Every historical mutation command and option is rejected by the parser.
for command in initramfs bootnext preserve-boot rescue-entry; do
    assert_rejected "${command}"
done
for option in \
    --execute \
    --regenerate-initramfs \
    --overwrite-initramfs \
    --overwrite-known-good \
    --legacy-managed-default \
    --esp-root \
    --recovery-manifest \
    --known-good-bootnum \
    --known-good-kernel \
    --known-good-kernel-image \
    --known-good-initramfs \
    --kernel-sha256 \
    --initramfs-sha256 \
    --initramfs-output \
    --preserve-kernel \
    --recovery-tag \
    --efi-label \
    --efi-disk \
    --efi-part \
    --cmdline-file \
    --microcode; do
    assert_rejected "${option}"
done

# No executable path remains for firmware or kernel tooling, EFI/NVRAM state,
# boot selection, or kernel/initramfs copying/building.  The command words above
# remain only as explicit parser rejections.
if grep -En \
    'resolve_tool[[:space:]]+(efibootmgr|dracut|lsinitrd)|run_mutation[^\n]*(efibootmgr|dracut|lsinitrd)|(^|[^A-Za-z_])(ESP_ROOT|KNOWN_GOOD_BOOTNUM|KNOWN_GOOD_KERNEL|KNOWN_GOOD_INITRAMFS|EFI_DISK|EFI_PART)([^A-Za-z_]|$)|/efi|BootNext|BootOrder' \
    "${ROLLBACK}"; then
    fail 'package rollback still contains an executable firmware/kernel path'
fi

# The package-only aggregate must contain exactly its three package/config
# recovery actions and no historical boot/kernel action.
all_body=$(sed -n '/^[[:space:]]*all)$/,/^[[:space:]]*;;/p' "${ROLLBACK}")
for required in disable_optimization restore_critical run_preserved_rebuild; do
    [[ ${all_body} == *"${required}"* ]] || fail "package-only all command omits ${required}"
done
if [[ ${all_body} == *handle_initramfs* || ${all_body} == *arm_bootnext* ||
      ${all_body} == *preserve_boot_assets* || ${all_body} == *create_rescue_entry* ]]; then
    fail 'package-only all command still dispatches a prohibited boot/kernel action'
fi

printf 'PASS: boot-entry and kernel automation is permanently retired\n'

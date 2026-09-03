#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../../.." && pwd -P)
ROLLBACK=${REPOSITORY_ROOT}/scripts/optimization/recovery/rollback.sh
RETIRED_BOOT_EVIDENCE=${REPOSITORY_ROOT}/scripts/optimization/recovery/record-boot-evidence.sh
STATE_AUTHORITY=${REPOSITORY_ROOT}/scripts/optimization/lib/state.py
PACKAGE_SCHEMA=${REPOSITORY_ROOT}/optimization/schema/package-state.schema.json
ARTIFACT_SCHEMA=${REPOSITORY_ROOT}/optimization/schema/artifact-state.schema.json
FINAL_SYSTEM_SCHEMA=${REPOSITORY_ROOT}/optimization/schema/final-system-state.schema.json

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

# The active semantic and wire-format authorities must encode the same
# userspace-only boundary.  Inspect their structure rather than banning words
# globally: explicit rejection/prohibition text is useful and remains allowed.
/usr/bin/python3 -I -B - \
    "${STATE_AUTHORITY}" \
    "${PACKAGE_SCHEMA}" \
    "${ARTIFACT_SCHEMA}" \
    "${FINAL_SYSTEM_SCHEMA}" <<'PY'
import ast
import json
import sys
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def literal_assignment(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return ast.literal_eval(node.value)
    fail(f"state semantic authority does not define {name}")


def schema_field_names(value) -> set[str]:
    fields: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            fields.update(properties)
        required = value.get("required")
        if isinstance(required, list):
            fields.update(item for item in required if isinstance(item, str))
        for nested in value.values():
            fields.update(schema_field_names(nested))
    elif isinstance(value, list):
        for nested in value:
            fields.update(schema_field_names(nested))
    return fields


def schema_enum_values(value) -> set[str]:
    values: set[str] = set()
    if isinstance(value, dict):
        enum = value.get("enum")
        if isinstance(enum, list):
            values.update(item for item in enum if isinstance(item, str))
        for nested in value.values():
            values.update(schema_enum_values(nested))
    elif isinstance(value, list):
        for nested in value:
            values.update(schema_enum_values(nested))
    return values


state_path, package_path, artifact_path, final_path = map(Path, sys.argv[1:])
state_tree = ast.parse(state_path.read_text(encoding="utf-8"), filename=str(state_path))

if literal_assignment(state_tree, "SCHEMA_VERSION") != 5:
    fail("state semantic authority is not package/artifact schema v5")
if literal_assignment(state_tree, "FINAL_SYSTEM_SCHEMA_VERSION") != 2:
    fail("state semantic authority is not final-system schema v2")
if "kernel-autofdo" in literal_assignment(state_tree, "BACKENDS"):
    fail("state semantic authority still admits the retired kernel PGO backend")
final_keys = literal_assignment(state_tree, "FINAL_SYSTEM_KEYS")
if "runtime_after_reboot" not in final_keys or "boot" in final_keys:
    fail("state semantic authority has not adopted runtime_after_reboot")

function_names = {
    node.name
    for node in state_tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
if "_verify_boot" in function_names:
    fail("state semantic authority still defines the retired boot-entry verifier")

for node in ast.walk(state_tree):
    if isinstance(node, ast.Name) and node.id == "PRODUCTION_EFIBOOTMGR":
        fail("state semantic authority still references a boot-entry executable")
    if isinstance(node, ast.Subscript):
        try:
            key = ast.literal_eval(node.slice)
        except (ValueError, TypeError):
            continue
        if key in {
            "boot",
            "boot_entry_id",
            "boot_evidence",
            "boot_current",
            "efi_root",
            "efi_loader",
            "efibootmgr",
            "efibootmgr_output_sha256",
        }:
            fail(f"state semantic authority still actively reads retired field {key!r}")
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Path"
        and node.args
    ):
        try:
            argument = ast.literal_eval(node.args[0])
        except (ValueError, TypeError):
            continue
        if argument in {"/efi", "/boot"}:
            fail(f"state semantic authority still declares boot asset root {argument!r}")

schemas = {
    "package": json.loads(package_path.read_text(encoding="utf-8")),
    "artifact": json.loads(artifact_path.read_text(encoding="utf-8")),
    "final-system": json.loads(final_path.read_text(encoding="utf-8")),
}
expected_versions = {"package": 5, "artifact": 5, "final-system": 2}
for name, schema in schemas.items():
    actual_version = schema.get("properties", {}).get("schema_version", {}).get("const")
    if actual_version != expected_versions[name]:
        fail(f"{name} wire schema version is {actual_version!r}, expected {expected_versions[name]}")

retired_fields = {
    "boot",
    "boot_entry_id",
    "boot_evidence",
    "boot_current",
    "efi_root",
    "efi_loader",
    "efibootmgr",
    "efibootmgr_output_sha256",
    "kernel_image",
    "initramfs",
}
for name, schema in schemas.items():
    present = sorted(schema_field_names(schema) & retired_fields)
    if present:
        fail(f"{name} wire schema still declares retired boot fields: {present}")
    if "kernel-autofdo" in schema_enum_values(schema):
        fail(f"{name} wire schema still admits the retired kernel PGO backend")

package_kernel = schemas["package"].get("$defs", {}).get("kernel_build", {})
expected_package_kernel = {
    "release",
    "config",
    "image",
    "modules_manifest",
    "operator_managed",
    "project_mutation_prohibited",
}
if set(package_kernel.get("required", [])) != expected_package_kernel:
    fail("package wire schema does not bind the exact human-managed kernel component shape")
if set(package_kernel.get("properties", {})) != expected_package_kernel:
    fail("package kernel properties differ from the human-managed component shape")

artifact_kernel = schemas["artifact"].get("$defs", {}).get("kernel", {})
expected_artifact_kernel = {
    "release",
    "artifact_type",
    "module_name",
    "vermagic",
    "config_sha256",
    "signed",
    "signature_key_id",
    "operator_managed",
    "project_mutation_prohibited",
}
if set(artifact_kernel.get("required", [])) != expected_artifact_kernel:
    fail("artifact wire schema does not bind the exact human-managed kernel artifact shape")
if set(artifact_kernel.get("properties", {})) != expected_artifact_kernel:
    fail("artifact kernel properties differ from the human-managed artifact shape")

final_schema = schemas["final-system"]
top_required = set(final_schema.get("required", []))
if "runtime_after_reboot" not in top_required or "boot" in top_required:
    fail("final-system wire schema has not adopted runtime_after_reboot")
runtime = final_schema.get("properties", {}).get("runtime_after_reboot", {})
expected_runtime = {
    "boot_id",
    "kernel_release",
    "modules_manifest",
    "openrc_output_sha256",
    "reboot_evidence",
}
if set(runtime.get("required", [])) != expected_runtime:
    fail("final-system wire schema does not bind the exact userspace runtime receipt shape")
if set(runtime.get("properties", {})) != expected_runtime:
    fail("final-system runtime properties differ from the userspace-only receipt shape")
PY

printf 'PASS: boot-entry and kernel automation is permanently retired\n'

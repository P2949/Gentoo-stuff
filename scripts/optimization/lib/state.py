#!/usr/bin/env python3
"""Strict v3 optimization state contracts and collection reconciliation.

JSON Schema documents the wire format.  This module is the semantic authority:
it rejects contradictory state machines, incomplete evidence, mismatched ABI and
toolchain identities, ambiguous filesystem topology, and collection coverage
claims which are not derivable from the package and artifact records.
"""

from __future__ import annotations

import hashlib
import errno
import fcntl
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, NoReturn, Sequence


SCHEMA_VERSION = 4
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
BUILD_ID_RE = re.compile(r"^[0-9a-f]+$")
CP_RE = re.compile(r"^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+$")
CPV_RE = re.compile(r"^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+-[0-9][A-Za-z0-9+_.-]*(?:-r[0-9]+)?$")
VERSION_RE = re.compile(r"^[0-9][A-Za-z0-9+_.]*(?:-r[0-9]+)?$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9+_.:@-]+$")
TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

AUTHORITATIVE_VDB_ROOT = Path("/var/db/pkg")
AUTHORITATIVE_INSTALLED_ROOT = Path("/")
AUTHORITATIVE_RUNTIME_ROOT = Path("/usr/local/libexec/gentoo-optimization")
AUTHORITATIVE_STATE_RUNTIME = AUTHORITATIVE_RUNTIME_ROOT / "scripts/optimization/lib/state.py"
AUTHORITATIVE_RECONCILER_RUNTIME = AUTHORITATIVE_RUNTIME_ROOT / "scripts/optimization/verify/reconcile-state.py"
AUTHORITATIVE_PROFILE_VALIDATOR = AUTHORITATIVE_RUNTIME_ROOT / "pgo/validate-profile.py"
AUTHORITATIVE_BINPKG_VALIDATOR = AUTHORITATIVE_RUNTIME_ROOT / "recovery/verify-binpkg-snapshot.py"
TRUSTED_STATE_ROOT = Path("/var/lib/gentoo-optimization")
TRUSTED_CACHE_ROOT = Path("/var/cache/gentoo-optimization")
AUTHORITATIVE_LOCKS = {
    "framework": Path("/run/gentoo-optimization/framework-install.lock"),
    "project": Path("/run/gentoo-optimization/project.lock"),
    "generation": Path("/run/gentoo-optimization/generation.lock"),
}
PRODUCTION_GETCAP = Path("/usr/bin/getcap")
PRODUCTION_READELF = Path("/usr/bin/readelf")
PRODUCTION_UNAME = Path("/usr/bin/uname")
PRODUCTION_EFIBOOTMGR = Path("/usr/bin/efibootmgr")
PRODUCTION_RC_STATUS = Path("/bin/rc-status")
BOLT_POLICY_REVISION = "gentoo-system-wide-bolt-v1-cdsort-20260712"
BOLT_APPROVED_ARGV = [
    "-reorder-blocks=ext-tsp",
    "-reorder-functions=cdsort",
    "-split-functions",
    "-split-all-cold",
    "-split-eh",
    "-icf=safe",
    "-update-debug-sections",
    "-dyno-stats",
]

EVIDENCE_KINDS = {
    "binary", "binpkg", "command-output", "config", "log", "manifest",
    "profile", "report", "sidecar", "source", "transaction", "other",
}
TERMINAL_REASON_REGISTRY_VERSION = "1"
TERMINAL_REASON_CODES = {
    "bolt-no-relocations", "bolt-no-symbols", "bolt-not-elf",
    "bolt-unsupported-abi", "bolt-unsupported-role", "classification-unknown",
    "deterministic-no-benefit", "firmware-not-rebuildable", "generated-artifact",
    "kernel-policy-exclusion", "not-machine-code", "profile-format-unsupported",
    "runtime-unreachable", "toolchain-unsupported", "upstream-incompatible",
    "unsafe-to-profile", "verification-failed", "workload-unavailable",
}

ABIS = {"amd64", "x86", "other", "none"}
LANGUAGES = {"c", "c++", "fortran", "rust", "go", "jvm", "python", "shell", "data", "other"}
COMPONENT_KINDS = {"native", "rust", "go", "kernel", "jvm", "script-data", "gpu", "other"}
BACKENDS = {
    "clang-ir", "clang-sample", "gcc-gcov", "rust-llvm-ir", "go-pprof",
    "ebuild-native", "kernel-autofdo", "not-applicable",
}
TOOL_FAMILIES = {
    "clang", "gcc", "rust", "go", "lld", "gnu-binutils", "llvm-binutils",
    "perf", "native",
}
TOOL_ROLES = {"cc", "cxx", "fc", "rustc", "go", "linker", "archiver", "profiler", "llvm-bolt", "perf2bolt", "merge-fdata"}
PGO_STATUSES = {"pending", "training", "profiled", "optimized", "not-applicable", "terminal-exclusion", "unknown", "failed"}
BOLT_STATUSES = {"pending", "captured", "profiled", "optimized", "not-applicable", "terminal-exclusion", "unknown", "failed"}
ELIGIBILITIES = {"eligible", "not-applicable", "terminal-exclusion", "unknown"}
SOURCE_STATUSES = {"pending", "running", "succeeded", "unknown", "failed"}
FINAL_STATUSES = {"pending", "optimized", "optimized-with-exclusions", "not-applicable", "terminal-exclusion", "unknown", "failed"}

ARTIFACT_KINDS = {
    "elf", "static-archive", "relocatable-object", "kernel-image",
    "kernel-module", "ebpf", "gpu-object", "firmware", "bytecode", "script",
    "data", "symlink",
}
ELF_ROLES = {
    "executable", "pie-executable", "shared-library", "plugin", "relocatable",
    "kernel-image", "kernel-module", "ebpf", "other",
}
ELF_REQUIRED_KINDS = {"elf", "relocatable-object", "kernel-image", "kernel-module", "ebpf"}
ELF_FORBIDDEN_KINDS = {"static-archive", "gpu-object", "firmware", "bytecode", "script", "data"}
ELF_FORBIDDEN_KINDS.add("symlink")

PACKAGE_KEYS = {
    "schema_version", "record_type", "generation", "identity",
    "frozen_inventory_entry", "live_instance", "source", "abis", "languages",
    "use_flags", "components", "source_rebuild", "graphs", "aggregate",
    "final_status", "resolution", "notes",
}
ARTIFACT_KEYS = {
    "schema_version", "record_type", "generation", "artifact_id", "owner",
    "kind", "format", "role", "installed_path", "canonical_path",
    "content_sha256", "size", "abi", "target", "metadata", "topology", "elf",
    "kernel", "graphs", "bolt", "final_status", "resolution",
}
FINAL_SYSTEM_KEYS = {
    "schema_version", "record_type", "generation", "trusted_roots", "locks",
    "validators", "registries", "final_transaction", "boot",
}


class StateValidationError(ValueError):
    """A record or collection violates the exact state contract."""


def _error(path: str, message: str) -> NoReturn:
    raise StateValidationError(f"{path}: {message}")


def _object(value: Any, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(path, "must be an object")
    actual = set(value)
    if actual != keys:
        _error(path, f"keys differ (missing={sorted(keys-actual)}, extra={sorted(actual-keys)})")
    return value


def _string(value: Any, path: str, *, absolute: bool = False) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        _error(path, "must be a nonempty NUL-free string")
    if absolute and (not value.startswith("/") or value == "/" or value.startswith("//") or posixpath.normpath(value) != value):
        _error(path, "must be a canonical absolute non-root path")
    return value


def _nullable_string(value: Any, path: str, *, absolute: bool = False) -> str | None:
    return None if value is None else _string(value, path, absolute=absolute)


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _error(path, "must be a boolean")
    return value


def _int(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _error(path, f"must be an integer >= {minimum}")
    return value


def _number(value: Any, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error(path, "must be numeric")
    result = float(value)
    if not minimum <= result <= maximum:
        _error(path, f"must be between {minimum} and {maximum}")
    return result


def _enum(value: Any, path: str, values: set[str]) -> str:
    if not isinstance(value, str) or value not in values:
        _error(path, f"must be one of {sorted(values)}")
    return value


def _sha(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    result = _string(value, path)
    if not SHA256_RE.fullmatch(result):
        _error(path, "must be a lowercase SHA-256")
    return result


def _fingerprint(value: Any, path: str) -> str:
    result = _string(value, path)
    if not FINGERPRINT_RE.fullmatch(result):
        _error(path, "must use sha256:<64 lowercase hex> form")
    return result


def _timestamp(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    result = _string(value, path)
    if not TIMESTAMP_RE.fullmatch(result):
        _error(path, "must be an RFC3339 UTC second timestamp")
    return result


def _sorted_strings(value: Any, path: str, *, absolute: bool = False, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        _error(path, "must be an array")
    result = [_string(item, f"{path}[{index}]", absolute=absolute) for index, item in enumerate(value)]
    if not allow_empty and not result:
        _error(path, "must not be empty")
    if result != sorted(set(result)):
        _error(path, "must be sorted and contain no duplicates")
    return result


def _cp_cpv(cp: Any, cpv: Any, path: str) -> tuple[str, str]:
    cp_s = _string(cp, f"{path}.cp")
    cpv_s = _string(cpv, f"{path}.cpv")
    if not CP_RE.fullmatch(cp_s):
        _error(f"{path}.cp", "must have category/package form")
    prefix = f"{cp_s}-"
    if not cpv_s.startswith(prefix) or not VERSION_RE.fullmatch(cpv_s[len(prefix):]):
        _error(f"{path}.cpv", "must be an exact versioned CPV tied to cp")
    return cp_s, cpv_s


def _evidence(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path, {"path", "sha256", "kind"})
    _string(item["path"], f"{path}.path", absolute=True)
    _sha(item["sha256"], f"{path}.sha256")
    _enum(item["kind"], f"{path}.kind", EVIDENCE_KINDS)
    return item


def _evidence_list(value: Any, path: str, *, required: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _error(path, "must be an array")
    result = [_evidence(item, f"{path}[{index}]") for index, item in enumerate(value)]
    identities = [(item["path"], item["sha256"], item["kind"]) for item in result]
    if identities != sorted(set(identities)):
        _error(path, "must be sorted by path/hash/kind and unique")
    if required and not result:
        _error(path, "must contain evidence")
    return result


def _resolution(value: Any, path: str, required: bool) -> dict[str, Any] | None:
    if value is None:
        if required:
            _error(path, "is required for terminal, unknown, or failed state")
        return None
    if not required:
        _error(path, "must be null for this state")
    item = _object(value, path, {"registry_version", "reason_code", "reviewed_by", "reviewed_at", "evidence"})
    if item["registry_version"] != TERMINAL_REASON_REGISTRY_VERSION:
        _error(f"{path}.registry_version", f"must be {TERMINAL_REASON_REGISTRY_VERSION}")
    _enum(item["reason_code"], f"{path}.reason_code", TERMINAL_REASON_CODES)
    _string(item["reviewed_by"], f"{path}.reviewed_by")
    _timestamp(item["reviewed_at"], f"{path}.reviewed_at")
    _evidence_list(item["evidence"], f"{path}.evidence", required=True)
    return item


def _generation(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path, {"generation_id", "inventory_id", "inventory_sha256"})
    for key in ("generation_id", "inventory_id"):
        if not SAFE_ID_RE.fullmatch(_string(item[key], f"{path}.{key}")):
            _error(f"{path}.{key}", "must be a safe stable identifier")
    _sha(item["inventory_sha256"], f"{path}.inventory_sha256")
    return item


def parse_slot(raw: str) -> tuple[str, str]:
    """Parse a VDB SLOT value without normalizing away its raw spelling."""
    if not raw or raw.strip() != raw or raw.count("/") > 1:
        raise StateValidationError("invalid raw SLOT value")
    if "/" in raw:
        slot, subslot = raw.split("/", 1)
        if not slot or not subslot:
            raise StateValidationError("invalid raw SLOT value")
        return slot, subslot
    return raw, raw


def vdb_identity_sha256(live_instance: Mapping[str, Any]) -> str:
    """Hash the exact live VDB identity fields, excluding the self hash."""
    identity = {key: live_instance[key] for key in (
        "vdb_path", "contents_sha256", "metadata_tree_sha256", "repository", "slot_raw", "slot", "subslot",
        "build_time", "counter", "environment_bz2_sha256",
    )}
    return hashlib.sha256(canonical_bytes(identity)).hexdigest()


def _tool(value: Any, path: str, expected_role: str | None = None) -> dict[str, Any]:
    item = _object(value, path, {"role", "family", "path", "realpath", "sha256", "version", "target_triple"})
    role = _enum(item["role"], f"{path}.role", TOOL_ROLES)
    if expected_role is not None and role != expected_role:
        _error(f"{path}.role", f"must be {expected_role}")
    _enum(item["family"], f"{path}.family", TOOL_FAMILIES)
    _string(item["path"], f"{path}.path", absolute=True)
    _string(item["realpath"], f"{path}.realpath", absolute=True)
    _sha(item["sha256"], f"{path}.sha256")
    _string(item["version"], f"{path}.version")
    _nullable_string(item["target_triple"], f"{path}.target_triple")
    return item


def _nullable_tool(value: Any, path: str, role: str) -> dict[str, Any] | None:
    return None if value is None else _tool(value, path, role)


def _target(value: Any, path: str, abi: str) -> dict[str, Any] | None:
    if value is None:
        if abi != "none":
            _error(path, "is required for a machine-code ABI")
        return None
    item = _object(value, path, {"triple", "architecture", "machine", "abi", "elf_class", "endianness", "libc", "cxx_abi", "kernel_release"})
    _string(item["triple"], f"{path}.triple")
    architecture = _string(item["architecture"], f"{path}.architecture")
    machine = _string(item["machine"], f"{path}.machine")
    if item["abi"] != abi:
        _error(f"{path}.abi", "must equal its component/artifact ABI")
    elf_class = item["elf_class"]
    if elf_class not in {32, 64, None}:
        _error(f"{path}.elf_class", "must be 32, 64, or null")
    _enum(item["endianness"], f"{path}.endianness", {"little", "big", "not-applicable"})
    _nullable_string(item["libc"], f"{path}.libc")
    _nullable_string(item["cxx_abi"], f"{path}.cxx_abi")
    _nullable_string(item["kernel_release"], f"{path}.kernel_release")
    if abi == "amd64" and (architecture != "x86_64" or elf_class != 64 or "X86-64" not in machine):
        _error(path, "amd64 must map to x86_64 ELF64 X86-64")
    if abi == "x86" and (architecture not in {"i386", "i486", "i586", "i686"} or elf_class != 32 or "80386" not in machine):
        _error(path, "x86 must map to i?86 ELF32 Intel 80386")
    return item


def _toolchain(value: Any, path: str, languages: set[str], backend: str, abi: str) -> dict[str, Any] | None:
    if value is None:
        if backend != "not-applicable":
            _error(path, "is required for a build backend")
        return None
    keys = {"cc", "cxx", "fc", "rustc", "go", "linker", "archiver", "profiler", "runtimes", "environment_fingerprint"}
    item = _object(value, path, keys)
    tools = {role: _nullable_tool(item[role], f"{path}.{role}", role) for role in ("cc", "cxx", "fc", "rustc", "go", "linker", "archiver", "profiler")}
    requirements = {"c": "cc", "c++": "cxx", "fortran": "fc", "rust": "rustc", "go": "go"}
    for language, role in requirements.items():
        if language in languages and tools[role] is None:
            _error(f"{path}.{role}", f"is required for {language}")
    if backend != "not-applicable" and tools["linker"] is None and languages & {"c", "c++", "fortran", "rust", "go"}:
        _error(f"{path}.linker", "is required for machine-code components")
    if backend in {"clang-ir", "clang-sample"}:
        compiler_roles = [role for role in ("cc", "cxx") if tools[role] is not None]
        compiler_tools = [tools[role] for role in compiler_roles]
        if not compiler_tools or any(compiler_tool is None or compiler_tool["family"] != "clang" for compiler_tool in compiler_tools):
            _error(path, f"{backend} requires a pure Clang CC/CXX tuple")
    if backend == "gcc-gcov":
        compiler_roles = [role for role in ("cc", "cxx", "fc") if tools[role] is not None]
        compiler_tools = [tools[role] for role in compiler_roles]
        if not compiler_tools or any(compiler_tool is None or compiler_tool["family"] != "gcc" for compiler_tool in compiler_tools):
            _error(path, "gcc-gcov requires a pure GCC CC/CXX/FC tuple")
    family_requirements = {
        "rust-llvm-ir": ("rustc", "rust"),
        "go-pprof": ("go", "go"), "kernel-autofdo": ("cc", "clang"),
    }
    requirement = family_requirements.get(backend)
    if requirement:
        role, family = requirement
        required_tool = tools[role]
        if required_tool is None or required_tool["family"] != family:
            _error(f"{path}.{role}", f"{backend} requires {family}")
    runtimes = item["runtimes"]
    if not isinstance(runtimes, list):
        _error(f"{path}.runtimes", "must be an array")
    runtime_keys: list[tuple[str, str, str]] = []
    for index, raw in enumerate(runtimes):
        rpath = f"{path}.runtimes[{index}]"
        runtime = _object(raw, rpath, {"name", "abi", "path", "sha256", "version"})
        name = _string(runtime["name"], f"{rpath}.name")
        runtime_abi = _enum(runtime["abi"], f"{rpath}.abi", ABIS - {"none"})
        runtime_path = _string(runtime["path"], f"{rpath}.path", absolute=True)
        _sha(runtime["sha256"], f"{rpath}.sha256")
        _string(runtime["version"], f"{rpath}.version")
        runtime_keys.append((name, runtime_abi, runtime_path))
    if runtime_keys != sorted(set(runtime_keys)):
        _error(f"{path}.runtimes", "must be sorted and unique")
    if languages & {"c", "c++", "fortran", "rust", "go"} and abi not in {runtime_abi for _name, runtime_abi, _path in runtime_keys}:
        _error(f"{path}.runtimes", f"must identify at least one runtime for component ABI {abi}")
    _fingerprint(item["environment_fingerprint"], f"{path}.environment_fingerprint")
    return item


def _workload_ref(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path, {"workload_id", "revision", "evidence"})
    for key in ("workload_id", "revision"):
        if not SAFE_ID_RE.fullmatch(_string(item[key], f"{path}.{key}")):
            _error(f"{path}.{key}", "must be a safe stable identifier")
    _evidence_list(item["evidence"], f"{path}.evidence", required=True)
    return item


def _workload_refs(value: Any, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _error(path, "must be an array")
    result = [_workload_ref(item, f"{path}[{i}]") for i, item in enumerate(value)]
    keys = [(item["workload_id"], item["revision"]) for item in result]
    if keys != sorted(set(keys)):
        _error(path, "must be sorted and unique")
    return result


def _pgo(value: Any, path: str, backend: str, generation_id: str, toolchain: dict[str, Any] | None) -> dict[str, Any]:
    keys = {"eligibility", "mode", "status", "generation_id", "manifest", "sidecar", "profile", "toolchain_fingerprint", "workload_refs", "training_evidence", "validation_evidence", "build_use", "resolution"}
    item = _object(value, path, keys)
    eligibility = _enum(item["eligibility"], f"{path}.eligibility", ELIGIBILITIES)
    mode = _enum(item["mode"], f"{path}.mode", BACKENDS)
    status = _enum(item["status"], f"{path}.status", PGO_STATUSES)
    gen = _nullable_string(item["generation_id"], f"{path}.generation_id")
    manifest = None if item["manifest"] is None else _evidence(item["manifest"], f"{path}.manifest")
    sidecar = None if item["sidecar"] is None else _evidence(item["sidecar"], f"{path}.sidecar")
    profile = None if item["profile"] is None else _evidence(item["profile"], f"{path}.profile")
    toolchain_fp = None if item["toolchain_fingerprint"] is None else _fingerprint(item["toolchain_fingerprint"], f"{path}.toolchain_fingerprint")
    workloads = _workload_refs(item["workload_refs"], f"{path}.workload_refs")
    training = _evidence_list(item["training_evidence"], f"{path}.training_evidence")
    validation = _evidence_list(item["validation_evidence"], f"{path}.validation_evidence")
    build_use = item["build_use"]
    if build_use is not None:
        proof = _object(build_use, f"{path}.build_use", {"build_log", "flags", "diagnostics", "installed_evidence", "validator_receipt"})
        _evidence(proof["build_log"], f"{path}.build_use.build_log")
        _sorted_strings(proof["flags"], f"{path}.build_use.flags", allow_empty=False)
        _evidence_list(proof["diagnostics"], f"{path}.build_use.diagnostics", required=True)
        _evidence_list(proof["installed_evidence"], f"{path}.build_use.installed_evidence", required=True)
        _evidence(proof["validator_receipt"], f"{path}.build_use.validator_receipt")
    reason_required = status in {"not-applicable", "terminal-exclusion", "unknown", "failed"}
    _resolution(item["resolution"], f"{path}.resolution", reason_required)

    if eligibility == "eligible":
        if status in {"not-applicable", "terminal-exclusion", "unknown"}:
            _error(path, "eligible PGO cannot claim a noneligible state")
        if mode != backend or mode == "not-applicable":
            _error(f"{path}.mode", "eligible mode must equal its component backend")
    else:
        expected = eligibility
        if status != expected:
            _error(f"{path}.status", f"must be {expected}")
        if mode != "not-applicable":
            _error(f"{path}.mode", "noneligible PGO must use not-applicable")
    identity_values = (manifest, sidecar, profile, toolchain_fp)
    if status in {"not-applicable", "terminal-exclusion", "unknown"} and (any(identity_values) or gen or workloads or training or validation or build_use):
        _error(path, "noneligible PGO cannot claim profile or build-use proof")
    if status in {"training", "profiled", "optimized"} and gen != generation_id:
        _error(f"{path}.generation_id", "must equal package generation")
    if status in {"profiled", "optimized"} and not all(identity_values):
        _error(path, f"{status} requires manifest, sidecar, profile, and toolchain identity")
    if toolchain is not None and toolchain_fp is not None and toolchain_fp != toolchain["environment_fingerprint"]:
        _error(f"{path}.toolchain_fingerprint", "must equal component toolchain fingerprint")
    if status == "optimized" and (not workloads or not training or not validation or build_use is None):
        _error(path, "optimized PGO requires workload, training, validation, and build-use proof")
    if status != "optimized" and build_use is not None:
        _error(f"{path}.build_use", "is valid only for optimized state")
    return item


def _kernel_build(value: Any, path: str, required: bool) -> dict[str, Any] | None:
    if value is None:
        if required:
            _error(path, "is required for a kernel component")
        return None
    if not required:
        _error(path, "must be null outside a kernel component")
    item = _object(value, path, {"release", "config", "image", "modules_manifest", "boot_entry_id", "boot_evidence"})
    _string(item["release"], f"{path}.release")
    for key in ("config", "image", "modules_manifest"):
        _evidence(item[key], f"{path}.{key}")
    _string(item["boot_entry_id"], f"{path}.boot_entry_id")
    _evidence_list(item["boot_evidence"], f"{path}.boot_evidence", required=True)
    return item


def _component(value: Any, path: str, generation_id: str) -> dict[str, Any]:
    keys = {"component_id", "component_kind", "languages", "abi", "target", "build_backend", "toolchain", "fingerprint", "pgo", "kernel"}
    item = _object(value, path, keys)
    component_id = _string(item["component_id"], f"{path}.component_id")
    if not SAFE_ID_RE.fullmatch(component_id):
        _error(f"{path}.component_id", "must be a safe stable identifier")
    kind = _enum(item["component_kind"], f"{path}.component_kind", COMPONENT_KINDS)
    language_list = _sorted_strings(item["languages"], f"{path}.languages", allow_empty=False)
    if not set(language_list) <= LANGUAGES:
        _error(f"{path}.languages", "contains an unsupported language")
    abi = _enum(item["abi"], f"{path}.abi", ABIS)
    target = _target(item["target"], f"{path}.target", abi)
    backend = _enum(item["build_backend"], f"{path}.build_backend", BACKENDS)
    toolchain = _toolchain(item["toolchain"], f"{path}.toolchain", set(language_list), backend, abi)
    _fingerprint(item["fingerprint"], f"{path}.fingerprint")
    if backend == "not-applicable" and (abi != "none" or toolchain is not None):
        _error(path, "not-applicable backend requires ABI none and no toolchain")
    if backend != "not-applicable" and abi == "none":
        _error(f"{path}.abi", "machine-code backend cannot use ABI none")
    if kind == "rust" and "rust" not in language_list:
        _error(f"{path}.languages", "Rust component must declare rust")
    if kind == "go" and "go" not in language_list:
        _error(f"{path}.languages", "Go component must declare go")
    _pgo(item["pgo"], f"{path}.pgo", backend, generation_id, toolchain)
    _kernel_build(item["kernel"], f"{path}.kernel", kind == "kernel")
    if kind == "kernel" and (target is None or target["kernel_release"] is None):
        _error(f"{path}.target.kernel_release", "is required for a kernel component")
    return item


def _check_result(value: Any, path: str, *, many: bool = False) -> dict[str, Any]:
    keys = {"status", "evidence"}
    item = _object(value, path, keys)
    _enum(item["status"], f"{path}.status", {"passed", "not-applicable"})
    evidence = _evidence_list(item["evidence"], f"{path}.evidence", required=True)
    if many and not evidence:
        _error(f"{path}.evidence", "must contain checks")
    return item


def _source_rebuild(value: Any, path: str, generation_id: str) -> dict[str, Any]:
    keys = {"required", "status", "generation_id", "transaction_id", "source_only", "attempts", "proof", "resolution"}
    item = _object(value, path, keys)
    if _bool(item["required"], f"{path}.required") is not True:
        _error(f"{path}.required", "must be true for every frozen installed package")
    status_value = _enum(item["status"], f"{path}.status", SOURCE_STATUSES)
    if item["generation_id"] != generation_id:
        _error(f"{path}.generation_id", "must equal package generation")
    transaction_id = _nullable_string(item["transaction_id"], f"{path}.transaction_id")
    source_only = _bool(item["source_only"], f"{path}.source_only")
    attempts = item["attempts"]
    if not isinstance(attempts, list):
        _error(f"{path}.attempts", "must be an array")
    attempt_ids: list[str] = []
    successful = 0
    for index, raw in enumerate(attempts):
        apath = f"{path}.attempts[{index}]"
        attempt = _object(raw, apath, {"attempt_id", "started_at", "completed_at", "result", "environment_fingerprint", "build_log", "failure_evidence"})
        attempt_id = _string(attempt["attempt_id"], f"{apath}.attempt_id")
        attempt_ids.append(attempt_id)
        _timestamp(attempt["started_at"], f"{apath}.started_at")
        _timestamp(attempt["completed_at"], f"{apath}.completed_at", nullable=True)
        result = _enum(attempt["result"], f"{apath}.result", {"running", "succeeded", "failed", "interrupted"})
        _fingerprint(attempt["environment_fingerprint"], f"{apath}.environment_fingerprint")
        _evidence(attempt["build_log"], f"{apath}.build_log")
        failures = _evidence_list(attempt["failure_evidence"], f"{apath}.failure_evidence")
        if result == "running" and attempt["completed_at"] is not None:
            _error(apath, "running attempt cannot have completed_at")
        if result != "running" and attempt["completed_at"] is None:
            _error(apath, "completed attempt requires completed_at")
        if attempt["completed_at"] is not None and attempt["completed_at"] < attempt["started_at"]:
            _error(apath, "completed_at cannot precede started_at")
        if result in {"failed", "interrupted"} and not failures:
            _error(f"{apath}.failure_evidence", "is required for failed/interrupted attempt")
        if result == "succeeded" and failures:
            _error(f"{apath}.failure_evidence", "must be empty for success")
        successful += result == "succeeded"
    if attempt_ids != sorted(set(attempt_ids)):
        _error(f"{path}.attempts", "must be sorted by unique attempt_id")
    proof = item["proof"]
    if proof is not None:
        pobj = _object(proof, f"{path}.proof", {"transaction_log", "install_log", "binpkg", "equery_check", "smoke_tests", "reverse_dependencies", "installed_vdb_identity_sha256", "active_modes", "portage_transaction_receipt", "binpkg_validation_receipt"})
        _evidence(pobj["transaction_log"], f"{path}.proof.transaction_log")
        _evidence(pobj["install_log"], f"{path}.proof.install_log")
        binpkg = _object(pobj["binpkg"], f"{path}.proof.binpkg", {"path", "sha256", "format", "production_marker"})
        _string(binpkg["path"], f"{path}.proof.binpkg.path", absolute=True)
        _sha(binpkg["sha256"], f"{path}.proof.binpkg.sha256")
        _enum(binpkg["format"], f"{path}.proof.binpkg.format", {"gpkg", "xpak"})
        _evidence(binpkg["production_marker"], f"{path}.proof.binpkg.production_marker")
        _check_result(pobj["equery_check"], f"{path}.proof.equery_check")
        smoke = pobj["smoke_tests"]
        if not isinstance(smoke, list) or not smoke:
            _error(f"{path}.proof.smoke_tests", "must contain at least one smoke test")
        smoke_names: list[str] = []
        for index, raw in enumerate(smoke):
            spath = f"{path}.proof.smoke_tests[{index}]"
            check = _object(raw, spath, {"name", "status", "evidence"})
            smoke_names.append(_string(check["name"], f"{spath}.name"))
            if check["status"] != "passed":
                _error(f"{spath}.status", "must be passed")
            _evidence_list(check["evidence"], f"{spath}.evidence", required=True)
        if smoke_names != sorted(set(smoke_names)):
            _error(f"{path}.proof.smoke_tests", "must be sorted by unique name")
        _check_result(pobj["reverse_dependencies"], f"{path}.proof.reverse_dependencies")
        _sha(pobj["installed_vdb_identity_sha256"], f"{path}.proof.installed_vdb_identity_sha256")
        active_modes = _sorted_strings(pobj["active_modes"], f"{path}.proof.active_modes")
        if not set(active_modes) <= {"pgo-use", "bolt-deploy"}:
            _error(f"{path}.proof.active_modes", "contains an unsupported final build mode")
        _evidence(pobj["portage_transaction_receipt"], f"{path}.proof.portage_transaction_receipt")
        _evidence(pobj["binpkg_validation_receipt"], f"{path}.proof.binpkg_validation_receipt")
    _resolution(item["resolution"], f"{path}.resolution", status_value in {"unknown", "failed"})
    if status_value == "succeeded":
        if not source_only or not transaction_id or proof is None or successful != 1:
            _error(path, "succeeded rebuild requires source-only transaction, one successful attempt, and complete proof")
    elif proof is not None:
        _error(f"{path}.proof", "is valid only for succeeded status")
    if status_value in {"running", "succeeded"} and not transaction_id:
        _error(f"{path}.transaction_id", f"is required for {status_value}")
    running_count = sum(attempt["result"] == "running" for attempt in attempts)
    if status_value == "running" and running_count != 1:
        _error(f"{path}.attempts", "running rebuild requires exactly one active attempt")
    if status_value != "running" and running_count:
        _error(f"{path}.attempts", "active attempt requires running rebuild status")
    if status_value == "failed" and not attempts:
        _error(f"{path}.attempts", "failed rebuild requires attempt evidence")
    if status_value == "pending" and attempts:
        _error(f"{path}.attempts", "must be empty while pending")
    return item


def _graph_ref(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path, {"cpv", "component_id", "evidence"})
    cpv = _string(item["cpv"], f"{path}.cpv")
    if "/" not in cpv:
        _error(f"{path}.cpv", "must be an exact CPV")
    component = _nullable_string(item["component_id"], f"{path}.component_id")
    if component is not None and not SAFE_ID_RE.fullmatch(component):
        _error(f"{path}.component_id", "must be a safe identifier")
    _evidence_list(item["evidence"], f"{path}.evidence", required=True)
    return item


def _graphs(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path, {"consumer_refs", "workload_refs", "reverse_dependency_refs"})
    for field in ("consumer_refs", "reverse_dependency_refs"):
        raw = item[field]
        if not isinstance(raw, list):
            _error(f"{path}.{field}", "must be an array")
        refs = [_graph_ref(ref, f"{path}.{field}[{i}]") for i, ref in enumerate(raw)]
        keys = [(ref["cpv"], ref["component_id"] or "") for ref in refs]
        if keys != sorted(set(keys)):
            _error(f"{path}.{field}", "must be sorted and unique")
    _workload_refs(item["workload_refs"], f"{path}.workload_refs")
    return item


def _lane_aggregate(value: Any, path: str, *, bolt: bool = False) -> dict[str, Any]:
    prefix = "candidate_count" if bolt else "eligible_count"
    keys = {prefix, "optimized_count", "excluded_count", "not_applicable_count", "pending_count", "unknown_count", "failed_count", "status"}
    item = _object(value, path, keys)
    counts = {key: _int(item[key], f"{path}.{key}") for key in keys - {"status"}}
    _enum(item["status"], f"{path}.status", FINAL_STATUSES)
    return {**counts, "status": item["status"]}


def _expected_final(optimized: int, excluded: int, not_applicable: int, pending: int, unknown: int, failed: int) -> str:
    if failed:
        return "failed"
    if unknown:
        return "unknown"
    if pending:
        return "pending"
    if optimized and excluded:
        return "optimized-with-exclusions"
    if optimized:
        return "optimized"
    if excluded:
        return "terminal-exclusion"
    if not_applicable:
        return "not-applicable"
    return "not-applicable"


def _pgo_counts(components: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    states = [component["pgo"] for component in components]
    return {
        "eligible_count": sum(state["eligibility"] == "eligible" for state in states),
        "optimized_count": sum(state["status"] == "optimized" for state in states),
        "excluded_count": sum(state["status"] == "terminal-exclusion" for state in states),
        "not_applicable_count": sum(state["status"] == "not-applicable" for state in states),
        "pending_count": sum(state["status"] in {"pending", "training", "profiled"} for state in states),
        "unknown_count": sum(state["status"] == "unknown" for state in states),
        "failed_count": sum(state["status"] == "failed" for state in states),
    }


def _assert_lane(item: Mapping[str, Any], expected: Mapping[str, int], path: str) -> None:
    actual = {key: item[key] for key in expected}
    if actual != expected:
        _error(path, f"counts do not match child records (expected={dict(expected)})")
    if "candidate_count" in expected:
        covered = expected["optimized_count"] + expected["pending_count"] + expected["unknown_count"] + expected["failed_count"] + expected["excluded_count"]
        if expected["candidate_count"] != covered:
            _error(path, "candidate accounting is incomplete")
    elif expected["eligible_count"] != expected["optimized_count"] + expected["pending_count"] + expected["failed_count"]:
        _error(path, "eligible accounting is incomplete")
    status = _expected_final(expected["optimized_count"], expected["excluded_count"], expected["not_applicable_count"], expected["pending_count"], expected["unknown_count"], expected["failed_count"])
    if item["status"] != status:
        _error(f"{path}.status", f"must be {status}")


def validate_package(record: Any) -> dict[str, Any]:
    package = _object(record, "$", PACKAGE_KEYS)
    if package["schema_version"] != SCHEMA_VERSION or package["record_type"] != "package":
        _error("$", f"requires schema_version={SCHEMA_VERSION} and record_type=package")
    generation = _generation(package["generation"], "$.generation")
    identity = _object(package["identity"], "$.identity", {"cpv", "cp", "repository", "slot", "subslot"})
    _cp_cpv(identity["cp"], identity["cpv"], "$.identity")
    for key in ("repository", "slot", "subslot"):
        _string(identity[key], f"$.identity.{key}")
    frozen = _object(package["frozen_inventory_entry"], "$.frozen_inventory_entry", {"entry_sha256", "installed_at_freeze", "payload"})
    _sha(frozen["entry_sha256"], "$.frozen_inventory_entry.entry_sha256")
    if _bool(frozen["installed_at_freeze"], "$.frozen_inventory_entry.installed_at_freeze") is not True:
        _error("$.frozen_inventory_entry.installed_at_freeze", "must be true")
    frozen_payload = _object(frozen["payload"], "$.frozen_inventory_entry.payload", {"cpv", "repository", "slot_raw", "contents_sha256", "metadata_tree_sha256"})
    if frozen_payload["cpv"] != identity["cpv"] or frozen_payload["repository"] != identity["repository"]:
        _error("$.frozen_inventory_entry.payload", "must identify the exact package")
    _string(frozen_payload["slot_raw"], "$.frozen_inventory_entry.payload.slot_raw")
    parse_slot(frozen_payload["slot_raw"])
    _sha(frozen_payload["contents_sha256"], "$.frozen_inventory_entry.payload.contents_sha256")
    _sha(frozen_payload["metadata_tree_sha256"], "$.frozen_inventory_entry.payload.metadata_tree_sha256")
    if frozen["entry_sha256"] != hashlib.sha256(canonical_bytes(frozen_payload)).hexdigest():
        _error("$.frozen_inventory_entry.entry_sha256", "does not hash the exact frozen entry payload")
    live = _object(package["live_instance"], "$.live_instance", {"vdb_path", "contents_sha256", "metadata_tree_sha256", "repository", "slot_raw", "slot", "subslot", "build_time", "counter", "environment_bz2_sha256", "identity_sha256"})
    _string(live["vdb_path"], "$.live_instance.vdb_path", absolute=True)
    for key in ("contents_sha256", "metadata_tree_sha256", "identity_sha256"):
        _sha(live[key], f"$.live_instance.{key}")
    _sha(live["environment_bz2_sha256"], "$.live_instance.environment_bz2_sha256", nullable=True)
    for key in ("repository", "slot_raw", "slot", "subslot", "build_time", "counter"):
        _string(live[key], f"$.live_instance.{key}")
    parsed_slot, parsed_subslot = parse_slot(live["slot_raw"])
    if (live["slot"], live["subslot"]) != (parsed_slot, parsed_subslot):
        _error("$.live_instance.slot_raw", "does not exactly map to slot/subslot")
    if any(live[key] != identity[key] for key in ("repository", "slot", "subslot")):
        _error("$.live_instance", "repository/slot/subslot must equal package identity")
    expected_vdb_suffix = "/" + identity["cpv"]
    if not live["vdb_path"].endswith(expected_vdb_suffix):
        _error("$.live_instance.vdb_path", "must end in the exact installed CPV")
    if live["identity_sha256"] != vdb_identity_sha256(live):
        _error("$.live_instance.identity_sha256", "does not hash the exact VDB identity fields")
    source = _object(package["source"], "$.source", {"ebuild", "manifest", "distfiles", "source_fingerprint"})
    _evidence(source["ebuild"], "$.source.ebuild")
    if source["manifest"] is not None:
        _evidence(source["manifest"], "$.source.manifest")
    _evidence_list(source["distfiles"], "$.source.distfiles")
    _fingerprint(source["source_fingerprint"], "$.source.source_fingerprint")
    abis = _sorted_strings(package["abis"], "$.abis")
    if not set(abis) <= ABIS - {"none"}:
        _error("$.abis", "contains an unsupported ABI")
    languages = _sorted_strings(package["languages"], "$.languages", allow_empty=False)
    if not set(languages) <= LANGUAGES:
        _error("$.languages", "contains an unsupported language")
    _sorted_strings(package["use_flags"], "$.use_flags")
    components_raw = package["components"]
    if not isinstance(components_raw, list) or not components_raw:
        _error("$.components", "must be a nonempty array")
    components = [_component(item, f"$.components[{i}]", generation["generation_id"]) for i, item in enumerate(components_raw)]
    component_ids = [component["component_id"] for component in components]
    fingerprints = [component["fingerprint"] for component in components]
    if component_ids != sorted(set(component_ids)):
        _error("$.components", "must be sorted by unique component_id")
    if len(fingerprints) != len(set(fingerprints)):
        _error("$.components", "component fingerprints must be unique")
    actual_abis = sorted({component["abi"] for component in components if component["abi"] != "none"})
    if abis != actual_abis:
        _error("$.abis", f"must exactly equal component ABI coverage {actual_abis}")
    actual_languages = sorted({language for component in components for language in component["languages"]})
    if languages != actual_languages:
        _error("$.languages", f"must exactly equal component language coverage {actual_languages}")
    source_rebuild = _source_rebuild(package["source_rebuild"], "$.source_rebuild", generation["generation_id"])
    if source_rebuild["proof"] is not None and source_rebuild["proof"]["installed_vdb_identity_sha256"] != live["identity_sha256"]:
        _error("$.source_rebuild.proof.installed_vdb_identity_sha256", "must equal live_instance.identity_sha256")
    package_graphs = _graphs(package["graphs"], "$.graphs")
    registered_workloads = {(ref["workload_id"], ref["revision"]) for ref in package_graphs["workload_refs"]}
    used_workloads = {(ref["workload_id"], ref["revision"]) for component in components for ref in component["pgo"]["workload_refs"]}
    if not used_workloads <= registered_workloads:
        _error("$.graphs.workload_refs", f"does not register component PGO workloads {sorted(used_workloads-registered_workloads)}")
    aggregate = _object(package["aggregate"], "$.aggregate", {"component_count", "artifact_count", "pgo", "bolt"})
    if _int(aggregate["component_count"], "$.aggregate.component_count", 1) != len(components):
        _error("$.aggregate.component_count", "must equal components length")
    _int(aggregate["artifact_count"], "$.aggregate.artifact_count")
    pgo_agg = _lane_aggregate(aggregate["pgo"], "$.aggregate.pgo")
    expected_pgo = _pgo_counts(components)
    _assert_lane(pgo_agg, expected_pgo, "$.aggregate.pgo")
    bolt_agg = _lane_aggregate(aggregate["bolt"], "$.aggregate.bolt", bolt=True)
    if bolt_agg["candidate_count"] + bolt_agg["not_applicable_count"] != aggregate["artifact_count"]:
        _error("$.aggregate.bolt", "candidate and not-applicable counts must cover artifact_count")
    if bolt_agg["candidate_count"] != bolt_agg["optimized_count"] + bolt_agg["excluded_count"] + bolt_agg["pending_count"] + bolt_agg["unknown_count"] + bolt_agg["failed_count"]:
        _error("$.aggregate.bolt", "candidate accounting is incomplete")
    bolt_status = _expected_final(bolt_agg["optimized_count"], bolt_agg["excluded_count"], bolt_agg["not_applicable_count"], bolt_agg["pending_count"], bolt_agg["unknown_count"], bolt_agg["failed_count"])
    if bolt_agg["status"] != bolt_status:
        _error("$.aggregate.bolt.status", f"must be {bolt_status}")
    pgo = expected_pgo
    source_pending = source_rebuild["status"] in {"pending", "running"}
    source_unknown = source_rebuild["status"] == "unknown"
    source_failed = source_rebuild["status"] == "failed"
    expected_final = _expected_final(
        pgo["optimized_count"] + bolt_agg["optimized_count"],
        pgo["excluded_count"] + bolt_agg["excluded_count"],
        pgo["not_applicable_count"] + bolt_agg["not_applicable_count"],
        pgo["pending_count"] + bolt_agg["pending_count"] + int(source_pending),
        pgo["unknown_count"] + bolt_agg["unknown_count"] + int(source_unknown),
        pgo["failed_count"] + bolt_agg["failed_count"] + int(source_failed),
    )
    final = _enum(package["final_status"], "$.final_status", FINAL_STATUSES)
    if final != expected_final:
        _error("$.final_status", f"must be {expected_final}")
    _resolution(package["resolution"], "$.resolution", final in {"not-applicable", "terminal-exclusion", "unknown", "failed"})
    _sorted_strings(package["notes"], "$.notes")
    return package


def _xattrs(value: Any, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _error(path, "must be an array")
    result: list[dict[str, Any]] = []
    names: list[str] = []
    for index, raw in enumerate(value):
        xpath = f"{path}[{index}]"
        item = _object(raw, xpath, {"name", "value_sha256"})
        names.append(_string(item["name"], f"{xpath}.name"))
        _sha(item["value_sha256"], f"{xpath}.value_sha256")
        result.append(item)
    if names != sorted(set(names)):
        _error(path, "must be sorted by unique xattr name")
    return result


def _elf(value: Any, path: str, abi: str, role: str) -> dict[str, Any]:
    keys = {"class", "data_encoding", "type", "machine", "build_id", "text_sha256", "has_symbols", "has_relocations", "has_executable_sections", "soname", "rpath", "runpath", "exports", "symbol_versions", "debug", "runtime_instrumentation", "dynamic_linkage", "security"}
    item = _object(value, path, keys)
    elf_class = item["class"]
    if elf_class not in {32, 64}:
        _error(f"{path}.class", "must be 32 or 64")
    _enum(item["data_encoding"], f"{path}.data_encoding", {"little", "big"})
    elf_type = _enum(item["type"], f"{path}.type", {"EXEC", "DYN", "REL", "CORE", "OTHER"})
    machine = _string(item["machine"], f"{path}.machine")
    build_id = item["build_id"]
    if build_id is not None and not BUILD_ID_RE.fullmatch(_string(build_id, f"{path}.build_id")):
        _error(f"{path}.build_id", "must be lowercase hexadecimal")
    _sha(item["text_sha256"], f"{path}.text_sha256", nullable=True)
    for key in ("has_symbols", "has_relocations", "has_executable_sections"):
        _bool(item[key], f"{path}.{key}")
    _nullable_string(item["soname"], f"{path}.soname")
    _sorted_strings(item["rpath"], f"{path}.rpath", absolute=True)
    _sorted_strings(item["runpath"], f"{path}.runpath", absolute=True)
    exports = item["exports"]
    if not isinstance(exports, list):
        _error(f"{path}.exports", "must be an array")
    export_keys: list[tuple[str, str]] = []
    for index, raw in enumerate(exports):
        epath = f"{path}.exports[{index}]"
        export = _object(raw, epath, {"name", "version", "binding", "visibility", "type"})
        name = _string(export["name"], f"{epath}.name")
        version = _nullable_string(export["version"], f"{epath}.version")
        _enum(export["binding"], f"{epath}.binding", {"LOCAL", "GLOBAL", "WEAK", "GNU_UNIQUE", "OTHER"})
        _enum(export["visibility"], f"{epath}.visibility", {"DEFAULT", "INTERNAL", "HIDDEN", "PROTECTED", "OTHER"})
        _string(export["type"], f"{epath}.type")
        export_keys.append((name, version or ""))
    if export_keys != sorted(set(export_keys)):
        _error(f"{path}.exports", "must be sorted and unique")
    versions = item["symbol_versions"]
    if not isinstance(versions, list):
        _error(f"{path}.symbol_versions", "must be an array")
    version_keys: list[tuple[str, str, bool]] = []
    for index, raw in enumerate(versions):
        vpath = f"{path}.symbol_versions[{index}]"
        symbol_version = _object(raw, vpath, {"name", "provider", "default"})
        version_keys.append((_string(symbol_version["name"], f"{vpath}.name"), _string(symbol_version["provider"], f"{vpath}.provider"), _bool(symbol_version["default"], f"{vpath}.default")))
    if version_keys != sorted(set(version_keys)):
        _error(f"{path}.symbol_versions", "must be sorted and unique")
    debug = _object(item["debug"], f"{path}.debug", {"has_debug_info", "has_full_symtab", "separate_debug_path", "separate_debug_sha256", "gnu_debuglink"})
    _bool(debug["has_debug_info"], f"{path}.debug.has_debug_info")
    _bool(debug["has_full_symtab"], f"{path}.debug.has_full_symtab")
    debug_path = _nullable_string(debug["separate_debug_path"], f"{path}.debug.separate_debug_path", absolute=True)
    debug_sha = _sha(debug["separate_debug_sha256"], f"{path}.debug.separate_debug_sha256", nullable=True)
    if (debug_path is None) != (debug_sha is None):
        _error(f"{path}.debug", "separate debug path and hash must be set together")
    _nullable_string(debug["gnu_debuglink"], f"{path}.debug.gnu_debuglink")
    runtime = _object(item["runtime_instrumentation"], f"{path}.runtime_instrumentation", {"pgo_markers", "bolt_note", "build_id_note", "cet_properties"})
    _sorted_strings(runtime["pgo_markers"], f"{path}.runtime_instrumentation.pgo_markers")
    for key in ("bolt_note", "build_id_note"):
        _bool(runtime[key], f"{path}.runtime_instrumentation.{key}")
    _sorted_strings(runtime["cet_properties"], f"{path}.runtime_instrumentation.cet_properties")
    dynamic = _object(item["dynamic_linkage"], f"{path}.dynamic_linkage", {"is_dynamic", "pie", "interpreter", "needed"})
    is_dynamic = _bool(dynamic["is_dynamic"], f"{path}.dynamic_linkage.is_dynamic")
    pie = _bool(dynamic["pie"], f"{path}.dynamic_linkage.pie")
    _nullable_string(dynamic["interpreter"], f"{path}.dynamic_linkage.interpreter", absolute=True)
    _sorted_strings(dynamic["needed"], f"{path}.dynamic_linkage.needed")
    if not is_dynamic and (pie or dynamic["interpreter"] or dynamic["needed"]):
        _error(f"{path}.dynamic_linkage", "static object cannot claim dynamic linkage")
    security = _object(item["security"], f"{path}.security", {"gnu_stack", "relro", "bind_now", "writable_executable_load"})
    _enum(security["gnu_stack"], f"{path}.security.gnu_stack", {"absent", "executable", "non-executable"})
    for key in ("relro", "bind_now", "writable_executable_load"):
        _bool(security[key], f"{path}.security.{key}")
    if role == "pie-executable" and (elf_type != "DYN" or not pie):
        _error(path, "PIE role requires DYN with pie=true")
    if role == "executable" and elf_type != "EXEC":
        _error(path, "executable role requires EXEC")
    if role in {"shared-library", "plugin"} and elf_type != "DYN":
        _error(path, f"{role} requires DYN")
    if role in {"relocatable", "kernel-module", "ebpf"} and elf_type != "REL":
        _error(path, f"{role} requires REL")
    if abi == "amd64" and (elf_class != 64 or "X86-64" not in machine):
        _error(path, "amd64 ELF must be ELF64 X86-64")
    if abi == "x86" and (elf_class != 32 or "80386" not in machine):
        _error(path, "x86 ELF must be ELF32 Intel 80386")
    return item


def _kernel_artifact(value: Any, path: str, required: bool, role: str) -> dict[str, Any] | None:
    if value is None:
        if required:
            _error(path, "is required for a kernel artifact")
        return None
    if not required:
        _error(path, "must be null outside a kernel artifact")
    item = _object(value, path, {"release", "artifact_type", "module_name", "vermagic", "config_sha256", "signed", "signature_key_id", "boot_entry_id", "boot_evidence"})
    _string(item["release"], f"{path}.release")
    artifact_type = _enum(item["artifact_type"], f"{path}.artifact_type", {"image", "module"})
    if (role == "kernel-module") != (artifact_type == "module"):
        _error(f"{path}.artifact_type", "must match ELF role")
    module = _nullable_string(item["module_name"], f"{path}.module_name")
    vermagic = _nullable_string(item["vermagic"], f"{path}.vermagic")
    if artifact_type == "module" and (not module or not vermagic):
        _error(path, "kernel module requires module_name and vermagic")
    _sha(item["config_sha256"], f"{path}.config_sha256")
    signed = _bool(item["signed"], f"{path}.signed")
    key = _nullable_string(item["signature_key_id"], f"{path}.signature_key_id")
    if signed != (key is not None):
        _error(path, "signed and signature_key_id must agree")
    boot_entry = _nullable_string(item["boot_entry_id"], f"{path}.boot_entry_id")
    evidence = _evidence_list(item["boot_evidence"], f"{path}.boot_evidence")
    if artifact_type == "image" and (not boot_entry or not evidence):
        _error(path, "kernel image requires boot entry and boot evidence")
    return item


def _bolt(value: Any, path: str, kind: str, role: str, abi: str, elf: dict[str, Any] | None, generation_id: str) -> dict[str, Any]:
    keys = {"eligibility", "status", "generation_id", "resolution", "capture", "perf_profiles", "fdata", "tools", "option_policy_revision", "options", "command", "output", "deployment"}
    item = _object(value, path, keys)
    eligibility = _enum(item["eligibility"], f"{path}.eligibility", ELIGIBILITIES)
    status_value = _enum(item["status"], f"{path}.status", BOLT_STATUSES)
    gen = _nullable_string(item["generation_id"], f"{path}.generation_id")
    _resolution(item["resolution"], f"{path}.resolution", status_value in {"not-applicable", "terminal-exclusion", "unknown", "failed"})
    capture = item["capture"]
    if capture is not None:
        cap = _object(capture, f"{path}.capture", {"input_path", "input_sha256", "input_text_sha256", "input_build_id", "manifest", "metadata_snapshot"})
        _string(cap["input_path"], f"{path}.capture.input_path", absolute=True)
        _sha(cap["input_sha256"], f"{path}.capture.input_sha256")
        _sha(cap["input_text_sha256"], f"{path}.capture.input_text_sha256")
        build_id = _string(cap["input_build_id"], f"{path}.capture.input_build_id")
        if not BUILD_ID_RE.fullmatch(build_id):
            _error(f"{path}.capture.input_build_id", "must be lowercase hexadecimal")
        _evidence(cap["manifest"], f"{path}.capture.manifest")
        _evidence(cap["metadata_snapshot"], f"{path}.capture.metadata_snapshot")
    profiles = item["perf_profiles"]
    if not isinstance(profiles, list):
        _error(f"{path}.perf_profiles", "must be an array")
    profile_ids: list[str] = []
    for index, raw in enumerate(profiles):
        ppath = f"{path}.perf_profiles[{index}]"
        profile = _object(raw, ppath, {"workload_id", "perf_data", "perf_tool", "samples", "branch_entries", "lost_samples"})
        profile_ids.append(_string(profile["workload_id"], f"{ppath}.workload_id"))
        _evidence(profile["perf_data"], f"{ppath}.perf_data")
        _tool(profile["perf_tool"], f"{ppath}.perf_tool", "profiler")
        for key in ("samples", "branch_entries", "lost_samples"):
            _int(profile[key], f"{ppath}.{key}")
        if profile["samples"] <= 0 or profile["branch_entries"] <= 0:
            _error(ppath, "profile requires positive samples and branch entries")
    if profile_ids != sorted(set(profile_ids)):
        _error(f"{path}.perf_profiles", "must be sorted by unique workload_id")
    fdata = item["fdata"]
    if fdata is not None:
        fobj = _object(fdata, f"{path}.fdata", {"path", "sha256", "merge_log", "input_sample_count", "fdata_record_count", "count_evidence", "stale_percent"})
        _string(fobj["path"], f"{path}.fdata.path", absolute=True)
        _sha(fobj["sha256"], f"{path}.fdata.sha256")
        _evidence(fobj["merge_log"], f"{path}.fdata.merge_log")
        _int(fobj["input_sample_count"], f"{path}.fdata.input_sample_count", 1)
        _int(fobj["fdata_record_count"], f"{path}.fdata.fdata_record_count", 1)
        _evidence(fobj["count_evidence"], f"{path}.fdata.count_evidence")
        _number(fobj["stale_percent"], f"{path}.fdata.stale_percent", 0, 100)
    tools = item["tools"]
    if tools is not None:
        tobj = _object(tools, f"{path}.tools", {"llvm_bolt", "perf2bolt", "merge_fdata"})
        for key, role_name in (("llvm_bolt", "llvm-bolt"), ("perf2bolt", "perf2bolt"), ("merge_fdata", "merge-fdata")):
            _tool(tobj[key], f"{path}.tools.{key}", role_name)
    policy_revision = _nullable_string(item["option_policy_revision"], f"{path}.option_policy_revision")
    options_raw = item["options"]
    if not isinstance(options_raw, list):
        _error(f"{path}.options", "must be an ordered array")
    options = [_string(option, f"{path}.options[{index}]") for index, option in enumerate(options_raw)]
    if len(options) != len(set(options)):
        _error(f"{path}.options", "must not contain duplicate arguments")
    command = item["command"]
    if command is not None:
        cobj = _object(command, f"{path}.command", {"argv", "output_partial_path", "record"})
        if not isinstance(cobj["argv"], list):
            _error(f"{path}.command.argv", "must be an ordered array")
        argv = [_string(argument, f"{path}.command.argv[{index}]") for index, argument in enumerate(cobj["argv"])]
        _string(cobj["output_partial_path"], f"{path}.command.output_partial_path", absolute=True)
        _evidence(cobj["record"], f"{path}.command.record")
    output = item["output"]
    if output is not None:
        out = _object(output, f"{path}.output", {"path", "sha256", "text_sha256", "build_id", "bolt_note", "note_binding", "verification"})
        _string(out["path"], f"{path}.output.path", absolute=True)
        _sha(out["sha256"], f"{path}.output.sha256")
        _sha(out["text_sha256"], f"{path}.output.text_sha256")
        out_build = _string(out["build_id"], f"{path}.output.build_id")
        if not BUILD_ID_RE.fullmatch(out_build):
            _error(f"{path}.output.build_id", "must be lowercase hexadecimal")
        if _bool(out["bolt_note"], f"{path}.output.bolt_note") is not True:
            _error(f"{path}.output.bolt_note", "must be true")
        binding = _object(out["note_binding"], f"{path}.output.note_binding", {"input_sha256", "fdata_sha256", "option_policy_revision", "command_record_sha256"})
        for key in ("input_sha256", "fdata_sha256", "command_record_sha256"):
            _sha(binding[key], f"{path}.output.note_binding.{key}")
        _string(binding["option_policy_revision"], f"{path}.output.note_binding.option_policy_revision")
        _evidence_list(out["verification"], f"{path}.output.verification", required=True)
    deployment = item["deployment"]
    if deployment is not None:
        dep = _object(deployment, f"{path}.deployment", {"transaction_id", "prestrip_path", "prestrip_deployed_sha256", "deploy_log", "rollback_artifact", "installed_sha256", "post_strip_verification", "metadata_verified", "runtime_verified"})
        _string(dep["transaction_id"], f"{path}.deployment.transaction_id")
        _string(dep["prestrip_path"], f"{path}.deployment.prestrip_path", absolute=True)
        _sha(dep["prestrip_deployed_sha256"], f"{path}.deployment.prestrip_deployed_sha256")
        _evidence(dep["deploy_log"], f"{path}.deployment.deploy_log")
        _evidence(dep["rollback_artifact"], f"{path}.deployment.rollback_artifact")
        _sha(dep["installed_sha256"], f"{path}.deployment.installed_sha256")
        _evidence_list(dep["post_strip_verification"], f"{path}.deployment.post_strip_verification", required=True)
        for key in ("metadata_verified", "runtime_verified"):
            if _bool(dep[key], f"{path}.deployment.{key}") is not True:
                _error(f"{path}.deployment.{key}", "must be true")
    eligible_shape = kind == "elf" and role in {"executable", "pie-executable", "shared-library", "plugin"} and abi == "amd64" and elf is not None
    if eligibility == "eligible":
        if status_value in {"not-applicable", "terminal-exclusion", "unknown"}:
            _error(path, "eligible BOLT cannot claim a noneligible state")
        if not eligible_shape:
            _error(path, "BOLT eligibility requires an amd64 executable/PIE/DSO/plugin ELF")
        assert elf is not None
        if not (elf["build_id"] and elf["text_sha256"] and elf["has_symbols"] and elf["has_relocations"] and elf["has_executable_sections"]):
            _error(path, "BOLT eligibility requires build ID, text hash, symbols, relocations, and executable sections")
    else:
        if status_value != eligibility:
            _error(f"{path}.status", f"must be {eligibility}")
    proof_values = (capture, profiles, fdata, tools, policy_revision, options, command, output, deployment)
    if status_value in {"not-applicable", "terminal-exclusion", "unknown"} and (gen or any(proof_values)):
        _error(path, "noneligible BOLT cannot claim capture/profile/output proof")
    if status_value in {"captured", "profiled", "optimized"} and gen != generation_id:
        _error(f"{path}.generation_id", "must equal artifact generation")
    if status_value in {"captured", "profiled", "optimized"} and capture is None:
        _error(f"{path}.capture", f"is required for {status_value}")
    if status_value in {"profiled", "optimized"} and (not profiles or fdata is None or tools is None):
        _error(path, f"{status_value} requires perf profiles, fdata, and exact tools")
    if status_value == "optimized" and (not options or command is None or output is None or deployment is None):
        _error(path, "optimized BOLT requires exact ordered command, output, and deployment proof")
    if status_value != "optimized" and (output is not None or deployment is not None):
        _error(path, "output/deployment is valid only for optimized BOLT")
    if fdata is not None and fdata["input_sample_count"] != sum(profile["samples"] for profile in profiles):
        _error(f"{path}.fdata.input_sample_count", "must equal the exact contributing perf input sample total")
    if status_value == "optimized":
        assert capture is not None and fdata is not None and tools is not None and command is not None and output is not None
        if policy_revision != BOLT_POLICY_REVISION or options != BOLT_APPROVED_ARGV:
            _error(path, "optimized BOLT must use the exact reviewed ordered option policy")
        expected_argv = [
            tools["llvm_bolt"]["realpath"], capture["input_path"], "-o",
            command["output_partial_path"], f"-data={fdata['path']}", *BOLT_APPROVED_ARGV,
        ]
        if command["argv"] != expected_argv:
            _error(f"{path}.command.argv", f"must equal exact reviewed argv {expected_argv}")
        binding = output["note_binding"]
        if binding != {
            "input_sha256": capture["input_sha256"],
            "fdata_sha256": fdata["sha256"],
            "option_policy_revision": BOLT_POLICY_REVISION,
            "command_record_sha256": command["record"]["sha256"],
        }:
            _error(f"{path}.output.note_binding", "does not bind exact input, fdata, policy, and command record")
    return item


def _bolt_counts(artifacts: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    states = [artifact["bolt"] for artifact in artifacts]
    return {
        "candidate_count": sum(state["eligibility"] in {"eligible", "terminal-exclusion", "unknown"} for state in states),
        "optimized_count": sum(state["status"] == "optimized" for state in states),
        "excluded_count": sum(state["status"] == "terminal-exclusion" for state in states),
        "not_applicable_count": sum(state["status"] == "not-applicable" for state in states),
        "pending_count": sum(state["status"] in {"pending", "captured", "profiled"} for state in states),
        "unknown_count": sum(state["status"] == "unknown" for state in states),
        "failed_count": sum(state["status"] == "failed" for state in states),
    }


def validate_artifact(record: Any) -> dict[str, Any]:
    artifact = _object(record, "$", ARTIFACT_KEYS)
    if artifact["schema_version"] != SCHEMA_VERSION or artifact["record_type"] != "artifact":
        _error("$", f"requires schema_version={SCHEMA_VERSION} and record_type=artifact")
    generation = _generation(artifact["generation"], "$.generation")
    _fingerprint(artifact["artifact_id"], "$.artifact_id")
    owner = _object(artifact["owner"], "$.owner", {"cpv", "cp", "component_id", "component_fingerprint"})
    _cp_cpv(owner["cp"], owner["cpv"], "$.owner")
    if not SAFE_ID_RE.fullmatch(_string(owner["component_id"], "$.owner.component_id")):
        _error("$.owner.component_id", "must be a safe stable identifier")
    _fingerprint(owner["component_fingerprint"], "$.owner.component_fingerprint")
    kind = _enum(artifact["kind"], "$.kind", ARTIFACT_KINDS)
    _string(artifact["format"], "$.format")
    role = _enum(artifact["role"], "$.role", ELF_ROLES | {"archive", "firmware", "bytecode", "script", "data", "gpu-object", "symlink"})
    installed = _string(artifact["installed_path"], "$.installed_path", absolute=True)
    canonical = _string(artifact["canonical_path"], "$.canonical_path", absolute=True)
    _sha(artifact["content_sha256"], "$.content_sha256")
    _int(artifact["size"], "$.size")
    abi = _enum(artifact["abi"], "$.abi", ABIS)
    target = _target(artifact["target"], "$.target", abi)
    metadata = _object(artifact["metadata"], "$.metadata", {"file_type", "device_major", "device_minor", "mode", "uid", "gid", "mtime_ns", "xattrs", "file_capabilities", "selinux_context"})
    file_type = _enum(metadata["file_type"], "$.metadata.file_type", {"regular", "fifo", "char-device", "block-device", "symlink"})
    device_major = metadata["device_major"]
    device_minor = metadata["device_minor"]
    if file_type in {"char-device", "block-device"}:
        _int(device_major, "$.metadata.device_major")
        _int(device_minor, "$.metadata.device_minor")
    elif device_major is not None or device_minor is not None:
        _error("$.metadata", "device major/minor are valid only for device nodes")
    mode = _int(metadata["mode"], "$.metadata.mode")
    if mode > 0o7777:
        _error("$.metadata.mode", "must contain only Unix permission/special bits")
    for key in ("uid", "gid", "mtime_ns"):
        _int(metadata[key], f"$.metadata.{key}")
    _xattrs(metadata["xattrs"], "$.metadata.xattrs")
    _sorted_strings(metadata["file_capabilities"], "$.metadata.file_capabilities")
    _nullable_string(metadata["selinux_context"], "$.metadata.selinux_context")
    topology = _object(artifact["topology"], "$.topology", {"device", "inode", "link_count", "hardlink_paths", "symlinks"})
    for key in ("device", "inode"):
        _int(topology[key], f"$.topology.{key}")
    link_count = _int(topology["link_count"], "$.topology.link_count", 1)
    hardlinks = _sorted_strings(topology["hardlink_paths"], "$.topology.hardlink_paths", absolute=True, allow_empty=kind == "symlink")
    if kind != "symlink" and link_count != len(hardlinks):
        _error("$.topology.link_count", "must equal recorded hardlink path count")
    symlinks = topology["symlinks"]
    if not isinstance(symlinks, list):
        _error("$.topology.symlinks", "must be an array")
    symlink_paths: list[str] = []
    for index, raw in enumerate(symlinks):
        spath = f"$.topology.symlinks[{index}]"
        link = _object(raw, spath, {"path", "target"})
        symlink_paths.append(_string(link["path"], f"{spath}.path", absolute=True))
        _string(link["target"], f"{spath}.target")
    if symlink_paths != sorted(set(symlink_paths)):
        _error("$.topology.symlinks", "must be sorted by unique path")
    if set(hardlinks) & set(symlink_paths):
        _error("$.topology", "hardlink and symlink paths must not overlap")
    if kind != "symlink" and canonical not in hardlinks:
        _error("$.canonical_path", "must be a recorded hardlink")
    if installed not in hardlinks and installed not in symlink_paths:
        _error("$.installed_path", "must be present in topology")
    symlink_map = {link["path"]: link["target"] for link in symlinks}
    if kind == "symlink":
        if role != "symlink" or file_type != "symlink" or hardlinks or link_count != 1 or len(symlinks) != 1 or installed != canonical or symlink_paths != [installed]:
            _error("$.topology", "standalone symlink requires one independently owned link and no hardlinks")
    def resolve_link(link_path: str, visiting: set[str]) -> str:
        if link_path in visiting:
            _error("$.topology.symlinks", f"cycle detected at {link_path}")
        target_value = symlink_map[link_path]
        resolved = posixpath.normpath(target_value if target_value.startswith("/") else posixpath.join(posixpath.dirname(link_path), target_value))
        if not resolved.startswith("/"):
            _error("$.topology.symlinks", f"target escapes installed hierarchy: {link_path}")
        if resolved in hardlinks:
            return resolved
        if resolved in symlink_map:
            return resolve_link(resolved, visiting | {link_path})
        if kind == "symlink" and link_path == installed:
            return resolved
        _error("$.topology.symlinks", f"target is outside this artifact topology: {link_path} -> {target_value}")
    for symlink_path in symlink_paths:
        resolve_link(symlink_path, set())
    elf_value = artifact["elf"]
    elf = None if elf_value is None else _elf(elf_value, "$.elf", abi, role)
    if kind in ELF_REQUIRED_KINDS and elf is None:
        _error("$.elf", f"is required for {kind}")
    if kind in ELF_FORBIDDEN_KINDS and elf is not None:
        _error("$.elf", f"must be null for {kind}")
    role_kind = {"relocatable-object": "relocatable", "kernel-image": "kernel-image", "kernel-module": "kernel-module", "ebpf": "ebpf"}
    role_kind["symlink"] = "symlink"
    if kind in role_kind and role != role_kind[kind]:
        _error("$.role", f"must be {role_kind[kind]} for {kind}")
    if elf is not None and target is not None and (elf["class"] != target["elf_class"] or elf["machine"] != target["machine"]):
        _error("$.elf", "class/machine must equal target mapping")
    kernel_required = kind in {"kernel-image", "kernel-module"}
    kernel = _kernel_artifact(artifact["kernel"], "$.kernel", kernel_required, role)
    if kernel is not None and target is not None and kernel["release"] != target["kernel_release"]:
        _error("$.kernel.release", "must equal target kernel_release")
    artifact_graphs = _graphs(artifact["graphs"], "$.graphs")
    bolt = _bolt(artifact["bolt"], "$.bolt", kind, role, abi, elf, generation["generation_id"])
    registered_bolt_workloads = {ref["workload_id"] for ref in artifact_graphs["workload_refs"]}
    used_bolt_workloads = {profile["workload_id"] for profile in bolt["perf_profiles"]}
    if not used_bolt_workloads <= registered_bolt_workloads:
        _error("$.graphs.workload_refs", f"does not register BOLT perf workloads {sorted(used_bolt_workloads-registered_bolt_workloads)}")
    if bolt["status"] == "optimized":
        assert elf is not None and bolt["output"] is not None and bolt["deployment"] is not None
        output = bolt["output"]
        deployment = bolt["deployment"]
        if output["sha256"] != deployment["prestrip_deployed_sha256"]:
            _error("$.bolt.deployment.prestrip_deployed_sha256", "must equal the exact BOLT output hash")
        if deployment["installed_sha256"] != artifact["content_sha256"]:
            _error("$.bolt.deployment.installed_sha256", "must equal the final installed artifact hash")
        if output["build_id"] != elf["build_id"] or output["text_sha256"] != elf["text_sha256"]:
            _error("$.bolt.output", "build ID and text hash must equal installed ELF metadata")
        if not elf["runtime_instrumentation"]["bolt_note"]:
            _error("$.elf.runtime_instrumentation.bolt_note", "optimized BOLT artifact must carry the installed note")
    expected = {
        "pending": "pending", "captured": "pending", "profiled": "pending",
        "optimized": "optimized", "not-applicable": "not-applicable",
        "terminal-exclusion": "terminal-exclusion", "unknown": "unknown", "failed": "failed",
    }[bolt["status"]]
    final = _enum(artifact["final_status"], "$.final_status", FINAL_STATUSES - {"optimized-with-exclusions"})
    if final != expected:
        _error("$.final_status", f"must be {expected}")
    _resolution(artifact["resolution"], "$.resolution", final in {"not-applicable", "terminal-exclusion", "unknown", "failed"})
    return artifact


VALIDATORS = {"package": validate_package, "artifact": validate_artifact}


def load_and_validate(path: Path, kind: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise StateValidationError(f"{path}: {error}") from error
    return VALIDATORS[kind](value)


def canonical_bytes(record: Mapping[str, Any]) -> bytes:
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise StateValidationError(f"output path traverses symlink: {current}")


def atomic_publish(record: Mapping[str, Any], output: Path) -> str:
    if not output.is_absolute() or output == Path("/"):
        raise StateValidationError("output must be an absolute non-root path")
    _reject_symlink_components(output.parent)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink_components(output.parent)
    if output.is_symlink():
        raise StateValidationError(f"output must not be a symlink: {output}")
    if output.exists() and not output.is_file():
        raise StateValidationError(f"output exists and is not a regular file: {output}")
    payload = canonical_bytes(record)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".partial", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        directory_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()
    return hashlib.sha256(payload).hexdigest()


def _load_records(paths: Iterable[Path], kind: str) -> list[dict[str, Any]]:
    return [load_and_validate(path, kind) for path in sorted(paths)]


def _inventory(raw: Any, path: str = "inventory") -> dict[str, Any]:
    item = _object(raw, path, {"schema_version", "record_type", "generation_id", "inventory_id", "packages", "owned_paths", "owned_directories"})
    if item["schema_version"] != 2 or item["record_type"] != "frozen-inventory":
        _error(path, "requires schema_version=2 and record_type=frozen-inventory")
    for key in ("generation_id", "inventory_id"):
        _string(item[key], f"{path}.{key}")
    packages = item["packages"]
    if not isinstance(packages, list):
        _error(f"{path}.packages", "must be an array")
    package_keys: list[str] = []
    for index, raw_package in enumerate(packages):
        ppath = f"{path}.packages[{index}]"
        package = _object(raw_package, ppath, {"cpv", "entry_sha256"})
        cpv = _string(package["cpv"], f"{ppath}.cpv")
        if not CPV_RE.fullmatch(cpv):
            _error(f"{ppath}.cpv", "must be an exact versioned CPV")
        package_keys.append(cpv)
        _sha(package["entry_sha256"], f"{ppath}.entry_sha256")
    if package_keys != sorted(set(package_keys)):
        _error(f"{path}.packages", "must be sorted by unique CPV")
    owned = item["owned_paths"]
    if not isinstance(owned, list):
        _error(f"{path}.owned_paths", "must be an array")
    keys: list[tuple[str, str]] = []
    for index, raw_entry in enumerate(owned):
        epath = f"{path}.owned_paths[{index}]"
        entry = _object(raw_entry, epath, {"owner_cpv", "path"})
        keys.append((_string(entry["owner_cpv"], f"{epath}.owner_cpv"), _string(entry["path"], f"{epath}.path", absolute=True)))
    if keys != sorted(set(keys)):
        _error(f"{path}.owned_paths", "must be sorted and unique")
    directories = item["owned_directories"]
    if not isinstance(directories, list):
        _error(f"{path}.owned_directories", "must be an array")
    directory_keys: list[tuple[str, str]] = []
    for index, raw_entry in enumerate(directories):
        epath = f"{path}.owned_directories[{index}]"
        entry = _object(raw_entry, epath, {"owner_cpv", "path", "mode", "uid", "gid", "classification", "resolution"})
        directory_keys.append((_string(entry["owner_cpv"], f"{epath}.owner_cpv"), _string(entry["path"], f"{epath}.path", absolute=True)))
        mode = _int(entry["mode"], f"{epath}.mode")
        if mode > 0o7777:
            _error(f"{epath}.mode", "must contain only Unix permission/special bits")
        _int(entry["uid"], f"{epath}.uid")
        _int(entry["gid"], f"{epath}.gid")
        if entry["classification"] != "not-applicable":
            _error(f"{epath}.classification", "directories must be terminal not-applicable")
        directory_resolution = _resolution(entry["resolution"], f"{epath}.resolution", True)
        if directory_resolution is None or directory_resolution["reason_code"] != "not-machine-code":
            _error(f"{epath}.resolution", "directory reason must be not-machine-code")
    if directory_keys != sorted(set(directory_keys)):
        _error(f"{path}.owned_directories", "must be sorted and unique")
    if set(keys) & set(directory_keys):
        _error(path, "file/symlink and directory inventories must not overlap")
    return item


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_beneath(path: Path, roots: Sequence[Path]) -> bool:
    absolute = Path(os.path.abspath(path))
    for root in roots:
        normalized_root = Path(os.path.abspath(root))
        try:
            if os.path.commonpath((str(absolute), str(normalized_root))) == str(normalized_root):
                return True
        except ValueError:
            continue
    return False


def _assert_trusted_path(path: Path, *, regular: bool | None, fixture_mode: bool) -> os.stat_result:
    """Reject symlink traversal and, in production, every writable/non-root ancestor."""
    if not path.is_absolute():
        raise StateValidationError(f"trusted path must be absolute: {path}")
    current = Path(path.anchor)
    anchor_metadata = current.lstat()
    if not fixture_mode and (anchor_metadata.st_uid != 0 or stat.S_IMODE(anchor_metadata.st_mode) & 0o022):
        raise StateValidationError(f"trusted path anchor is not root-owned/non-writable: {current}")
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise StateValidationError(f"trusted path is unavailable: {current}: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise StateValidationError(f"trusted path traverses symlink: {current}")
        if not fixture_mode and (metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022):
            raise StateValidationError(f"trusted path is not root-owned/non-writable: {current}")
    metadata = path.lstat()
    if regular is True and not stat.S_ISREG(metadata.st_mode):
        raise StateValidationError(f"trusted path is not a regular file: {path}")
    if regular is False and not stat.S_ISDIR(metadata.st_mode):
        raise StateValidationError(f"trusted path is not a directory: {path}")
    return metadata


def secure_read(
    path: Path,
    expected_sha256: str | None = None,
    *,
    fixture_mode: bool = False,
    allowed_roots: Sequence[Path] = (),
) -> bytes:
    """O_NOFOLLOW reopen with stable inode/size/ctime and optional exact hash."""
    if allowed_roots and not _path_beneath(path, allowed_roots):
        raise StateValidationError(f"proof path is outside the trusted generation roots: {path}")
    before = _assert_trusted_path(path, regular=True, fixture_mode=fixture_mode)
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened_before = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        opened_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    def identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
        return (
            value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns,
        )
    after = path.lstat()
    if identity(before) != identity(opened_before) or identity(opened_before) != identity(opened_after) or identity(opened_after) != identity(after):
        raise StateValidationError(f"proof changed while it was being verified: {path}")
    payload = b"".join(chunks)
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise StateValidationError(f"proof hash mismatch: {path}")
    return payload


def secure_json(
    path: Path,
    expected_sha256: str | None = None,
    *,
    fixture_mode: bool = False,
    allowed_roots: Sequence[Path] = (),
) -> Any:
    try:
        return json.loads(secure_read(path, expected_sha256, fixture_mode=fixture_mode, allowed_roots=allowed_roots))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StateValidationError(f"invalid trusted JSON {path}: {error}") from error


def _validator(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path, {"path", "sha256"})
    _string(item["path"], f"{path}.path", absolute=True)
    _sha(item["sha256"], f"{path}.sha256")
    return item


def validate_final_system_state(value: Any) -> dict[str, Any]:
    """Validate the wire shape of the independent generation/boot authority."""
    item = _object(value, "$", FINAL_SYSTEM_KEYS)
    if item["schema_version"] != 1 or item["record_type"] != "final-system-state":
        _error("$", "requires schema_version=1 and record_type=final-system-state")
    _generation(item["generation"], "$.generation")
    roots = _object(item["trusted_roots"], "$.trusted_roots", {
        "generation_root", "evidence_root", "profiles_root", "bolt_root",
        "binpkg_snapshot", "packages_dir", "artifacts_dir", "inventory",
    })
    for key, raw in roots.items():
        _string(raw, f"$.trusted_roots.{key}", absolute=True)
    locks = _object(item["locks"], "$.locks", {"framework", "project", "generation"})
    for key in locks:
        _validator(locks[key], f"$.locks.{key}")
    validators = _object(item["validators"], "$.validators", {"state_runtime", "reconciler_runtime", "profile", "binpkg_snapshot", "readelf", "getcap", "uname", "efibootmgr", "rc_status"})
    for key in validators:
        _validator(validators[key], f"$.validators.{key}")
    registries = _object(item["registries"], "$.registries", {"workloads", "dependency_edges"})
    _workload_refs(registries["workloads"], "$.registries.workloads")
    edges = registries["dependency_edges"]
    if not isinstance(edges, list):
        _error("$.registries.dependency_edges", "must be an array")
    edge_keys: list[tuple[str, str, str, str]] = []
    for index, raw in enumerate(edges):
        path = f"$.registries.dependency_edges[{index}]"
        edge = _object(raw, path, {"consumer_cpv", "consumer_component_id", "provider_cpv", "provider_component_id", "evidence"})
        edge_key = (
            _string(edge["consumer_cpv"], f"{path}.consumer_cpv"),
            _string(edge["consumer_component_id"], f"{path}.consumer_component_id"),
            _string(edge["provider_cpv"], f"{path}.provider_cpv"),
            _string(edge["provider_component_id"], f"{path}.provider_component_id"),
        )
        edge_keys.append(edge_key)
        _evidence_list(edge["evidence"], f"{path}.evidence", required=True)
    if edge_keys != sorted(set(edge_keys)):
        _error("$.registries.dependency_edges", "must be sorted and unique")
    transaction = _object(item["final_transaction"], "$.final_transaction", {"transaction_id", "completed_at", "active_modes", "portage_receipt", "vdb_receipt", "binpkg_snapshot_receipt"})
    _string(transaction["transaction_id"], "$.final_transaction.transaction_id")
    _timestamp(transaction["completed_at"], "$.final_transaction.completed_at")
    modes = _sorted_strings(transaction["active_modes"], "$.final_transaction.active_modes")
    if not set(modes) <= {"pgo-use", "bolt-deploy"}:
        _error("$.final_transaction.active_modes", "contains unsupported final mode")
    for key in ("portage_receipt", "vdb_receipt", "binpkg_snapshot_receipt"):
        _evidence(transaction[key], f"$.final_transaction.{key}")
    boot = _object(item["boot"], "$.boot", {
        "boot_id", "kernel_release", "boot_current", "efi_root", "kernel_image",
        "initramfs", "efi_loader", "modules_manifest", "efibootmgr_output_sha256",
        "openrc_output_sha256", "reboot_evidence",
    })
    _string(boot["boot_id"], "$.boot.boot_id")
    _string(boot["kernel_release"], "$.boot.kernel_release")
    if not re.fullmatch(r"[0-9A-Fa-f]{4}", _string(boot["boot_current"], "$.boot.boot_current")):
        _error("$.boot.boot_current", "must be a four-digit EFI boot number")
    if boot["efi_root"] != "/efi":
        _error("$.boot.efi_root", "must be /efi")
    for key in ("kernel_image", "initramfs", "efi_loader", "modules_manifest"):
        _evidence(boot[key], f"$.boot.{key}")
    for key in ("efibootmgr_output_sha256", "openrc_output_sha256"):
        _sha(boot[key], f"$.boot.{key}")
    _evidence_list(boot["reboot_evidence"], "$.boot.reboot_evidence", required=True)
    return item


def vdb_metadata_tree_sha256(instance: Path) -> str:
    """Hash every VDB metadata file/symlink by relative path, type, mode, and payload."""
    entries: list[dict[str, Any]] = []
    for path in sorted(instance.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(instance).as_posix()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            entries.append({"path": relative, "type": "directory", "mode": mode, "sha256": None, "target": None})
        elif stat.S_ISREG(metadata.st_mode):
            entries.append({"path": relative, "type": "regular", "mode": mode, "sha256": _file_sha(path), "target": None})
        elif stat.S_ISLNK(metadata.st_mode):
            entries.append({"path": relative, "type": "symlink", "mode": mode, "sha256": None, "target": os.readlink(path)})
        else:
            raise StateValidationError(f"unsupported VDB metadata file type: {path}")
    return hashlib.sha256(canonical_bytes({"entries": entries})).hexdigest()


def parse_vdb_contents(path: Path) -> dict[str, tuple[str, str | None]]:
    """Return every installed CONTENTS path mapped to (type, symlink target)."""
    result: dict[str, tuple[str, str | None]] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as error:
        raise StateValidationError(f"{path}: {error}") from error
    for line_number, line in enumerate(lines, 1):
        try:
            fields = shlex.split(line)
        except ValueError as error:
            raise StateValidationError(f"{path}:{line_number}: {error}") from error
        if not fields:
            continue
        if fields[0] not in {"obj", "sym", "fif", "dev", "dir"} or len(fields) < 2:
            raise StateValidationError(f"{path}:{line_number}: unsupported CONTENTS row")
        installed = fields[1]
        if not installed.startswith("/") or posixpath.normpath(installed) != installed:
            raise StateValidationError(f"{path}:{line_number}: noncanonical installed path")
        if installed in result:
            raise StateValidationError(f"{path}:{line_number}: duplicate installed path {installed}")
        target: str | None = None
        if fields[0] == "sym":
            if len(fields) < 5 or fields[2] != "->":
                raise StateValidationError(f"{path}:{line_number}: malformed symlink row")
            target = fields[3]
        elif fields[0] == "dir" and len(fields) != 2:
            raise StateValidationError(f"{path}:{line_number}: malformed directory row")
        result[installed] = (fields[0], target)
    return result


def _read_vdb_scalar(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise StateValidationError(f"{path}: {error}") from error


def _rooted_path(root: Path, installed_path: str) -> Path:
    root_real = root.resolve(strict=True)
    candidate = root / installed_path.lstrip("/")
    try:
        parent_real = candidate.parent.resolve(strict=True)
    except OSError as error:
        raise StateValidationError(f"installed parent is unavailable for {installed_path}: {error}") from error
    try:
        if os.path.commonpath((str(root_real), str(parent_real))) != str(root_real):
            raise StateValidationError(f"installed path escapes root: {installed_path}")
    except ValueError as error:
        raise StateValidationError(f"installed path is on an unrelated root: {installed_path}") from error
    # Keep the owning VDB path lexical in records, but operate on the confined
    # canonical parent so legitimate merged-/usr aliases (/bin, /lib*) do not
    # look like hostile proof-path traversal.  The leaf itself is never followed
    # when lstat/O_NOFOLLOW semantics are required.
    return parent_real / candidate.name


def _installed_file_type(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISCHR(mode):
        return "char-device"
    if stat.S_ISBLK(mode):
        return "block-device"
    raise StateValidationError("installed canonical object is not a supported file type")


def _special_content_sha256(file_type: str, device_major: int | None, device_minor: int | None) -> str:
    return hashlib.sha256(canonical_bytes({"file_type": file_type, "device_major": device_major, "device_minor": device_minor})).hexdigest()


def _get_file_capabilities(path: Path) -> list[str]:
    command = PRODUCTION_GETCAP
    if not command.is_file():
        raise StateValidationError(f"exact getcap is required: {command}")
    try:
        result = subprocess.run(
            [str(command), "-n", str(path)], check=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=10,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise StateValidationError(f"getcap failed for {path}: {error}") from error
    if result.returncode != 0:
        raise StateValidationError(f"getcap failed for {path}: {result.stderr.strip()}")
    line = result.stdout.strip()
    if not line:
        return []
    prefix = f"{path} "
    if not line.startswith(prefix) or "\n" in line:
        raise StateValidationError(f"unexpected getcap output for {path}")
    return [line[len(prefix):]]


def verify_installed_artifacts(artifacts: Sequence[Mapping[str, Any]], installed_root: Path) -> None:
    """Verify every recorded path and exact post-strip installed metadata."""
    if not installed_root.is_absolute() or not installed_root.is_dir():
        _error("collection.installed_root", "must be an existing absolute directory")
    for artifact in artifacts:
        artifact_id = artifact["artifact_id"]
        metadata = artifact["metadata"]
        if artifact["kind"] == "symlink":
            installed = artifact["installed_path"]
            path = _rooted_path(installed_root, installed)
            link_stat = path.lstat()
            if not stat.S_ISLNK(link_stat.st_mode):
                _error(f"collection.installed[{artifact_id}]", f"standalone link is not a symlink: {installed}")
            target = os.readlink(path)
            if artifact["topology"]["symlinks"] != [{"path": installed, "target": target}]:
                _error(f"collection.installed[{artifact_id}].symlinks", "exact standalone symlink target mismatch")
            if (link_stat.st_dev, link_stat.st_ino, link_stat.st_nlink) != (artifact["topology"]["device"], artifact["topology"]["inode"], artifact["topology"]["link_count"]):
                _error(f"collection.installed[{artifact_id}].topology", "standalone symlink inode/link-count mismatch")
            scalar_actual = {"mode": stat.S_IMODE(link_stat.st_mode), "uid": link_stat.st_uid, "gid": link_stat.st_gid, "mtime_ns": link_stat.st_mtime_ns}
            for field, actual in scalar_actual.items():
                if metadata[field] != actual:
                    _error(f"collection.installed[{artifact_id}].{field}", f"must be {actual}")
            target_bytes = os.fsencode(target)
            if artifact["size"] != link_stat.st_size or artifact["content_sha256"] != hashlib.sha256(target_bytes).hexdigest():
                _error(f"collection.installed[{artifact_id}]", "standalone symlink size/target hash mismatch")
            try:
                actual_xattrs = [{"name": name, "value_sha256": hashlib.sha256(os.getxattr(path, name, follow_symlinks=False)).hexdigest()} for name in sorted(os.listxattr(path, follow_symlinks=False))]
            except OSError as error:
                raise StateValidationError(f"collection.installed[{artifact_id}].xattrs: {error}") from error
            if metadata["xattrs"] != actual_xattrs or metadata["file_capabilities"]:
                _error(f"collection.installed[{artifact_id}]", "standalone symlink xattr/capability mismatch")
            selinux_raw = next((os.getxattr(path, name, follow_symlinks=False) for name in os.listxattr(path, follow_symlinks=False) if name == "security.selinux"), None)
            selinux_context = None if selinux_raw is None else selinux_raw.rstrip(b"\x00").decode("utf-8")
            if metadata["selinux_context"] != selinux_context:
                _error(f"collection.installed[{artifact_id}].selinux_context", "standalone symlink SELinux context mismatch")
            continue
        hardlink_stats: list[os.stat_result] = []
        for installed in artifact["topology"]["hardlink_paths"]:
            path = _rooted_path(installed_root, installed)
            try:
                path_metadata = path.lstat()
            except OSError as error:
                raise StateValidationError(f"collection.installed[{artifact_id}]: {installed}: {error}") from error
            if stat.S_ISLNK(path_metadata.st_mode):
                _error(f"collection.installed[{artifact_id}]", f"hardlink path is a symlink: {installed}")
            hardlink_stats.append(path_metadata)
        canonical = _rooted_path(installed_root, artifact["canonical_path"])
        canonical_stat = canonical.lstat()
        actual_type = _installed_file_type(canonical_stat.st_mode)
        if actual_type != metadata["file_type"]:
            _error(f"collection.installed[{artifact_id}].file_type", f"must be {actual_type}")
        if len({(item.st_dev, item.st_ino) for item in hardlink_stats}) != 1:
            _error(f"collection.installed[{artifact_id}].topology", "recorded hardlink paths do not share one inode")
        if (canonical_stat.st_dev, canonical_stat.st_ino) != (artifact["topology"]["device"], artifact["topology"]["inode"]):
            _error(f"collection.installed[{artifact_id}].topology", "device/inode mismatch")
        if canonical_stat.st_nlink != artifact["topology"]["link_count"]:
            _error(f"collection.installed[{artifact_id}].topology", "hardlink count mismatch")
        scalar_actual = {"mode": stat.S_IMODE(canonical_stat.st_mode), "uid": canonical_stat.st_uid, "gid": canonical_stat.st_gid, "mtime_ns": canonical_stat.st_mtime_ns}
        for field, actual in scalar_actual.items():
            if metadata[field] != actual:
                _error(f"collection.installed[{artifact_id}].{field}", f"must be {actual}")
        if artifact["size"] != canonical_stat.st_size:
            _error(f"collection.installed[{artifact_id}].size", f"must be {canonical_stat.st_size}")
        if actual_type == "regular":
            actual_sha = _file_sha(canonical)
        else:
            major = os.major(canonical_stat.st_rdev) if actual_type in {"char-device", "block-device"} else None
            minor = os.minor(canonical_stat.st_rdev) if actual_type in {"char-device", "block-device"} else None
            if (metadata["device_major"], metadata["device_minor"]) != (major, minor):
                _error(f"collection.installed[{artifact_id}].device", "major/minor mismatch")
            actual_sha = _special_content_sha256(actual_type, major, minor)
        if artifact["content_sha256"] != actual_sha:
            _error(f"collection.installed[{artifact_id}].content_sha256", "live content hash mismatch")
        try:
            xattr_names = sorted(os.listxattr(canonical, follow_symlinks=False))
            actual_xattrs = [{"name": name, "value_sha256": hashlib.sha256(os.getxattr(canonical, name, follow_symlinks=False)).hexdigest()} for name in xattr_names]
        except OSError as error:
            raise StateValidationError(f"collection.installed[{artifact_id}].xattrs: {error}") from error
        if metadata["xattrs"] != actual_xattrs:
            _error(f"collection.installed[{artifact_id}].xattrs", "exact xattr set/hash mismatch")
        capabilities = _get_file_capabilities(canonical)
        if metadata["file_capabilities"] != capabilities:
            _error(f"collection.installed[{artifact_id}].file_capabilities", "capability mismatch")
        selinux_raw = None
        try:
            selinux_raw = os.getxattr(canonical, "security.selinux", follow_symlinks=False)
        except OSError as error:
            if error.errno != errno.ENODATA:
                raise StateValidationError(f"collection.installed[{artifact_id}].selinux_context: {error}") from error
        selinux_context = None if selinux_raw is None else selinux_raw.rstrip(b"\x00").decode("utf-8")
        if metadata["selinux_context"] != selinux_context:
            _error(f"collection.installed[{artifact_id}].selinux_context", "SELinux context mismatch")
        for link in artifact["topology"]["symlinks"]:
            link_path = _rooted_path(installed_root, link["path"])
            try:
                link_stat = link_path.lstat()
            except OSError as error:
                raise StateValidationError(f"collection.installed[{artifact_id}]: {link['path']}: {error}") from error
            if not stat.S_ISLNK(link_stat.st_mode) or os.readlink(link_path) != link["target"]:
                _error(f"collection.installed[{artifact_id}].symlinks", f"target/type mismatch for {link['path']}")


def _run_exact(tool: Path, arguments: Sequence[str], *, timeout: int = 30) -> bytes:
    try:
        result = subprocess.run(
            [str(tool), *arguments], check=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "HOME": "/"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise StateValidationError(f"exact tool failed: {tool}: {error}") from error
    if result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise StateValidationError(f"exact tool failed ({result.returncode}): {tool}: {diagnostic}")
    return result.stdout


def _readelf(tool: Path, path: Path, *arguments: str) -> str:
    return _run_exact(tool, [*arguments, str(path)]).decode("utf-8", errors="strict")


def parse_program_security(program: str) -> tuple[str, bool, bool]:
    stack = "absent"
    relro = False
    writable_executable = False
    for line in program.splitlines():
        if re.match(r"^\s*GNU_STACK\s", line):
            fields = line.split()
            flags = fields[6:-1] if len(fields) >= 8 else []
            stack = "executable" if "E" in flags else "non-executable"
        if re.match(r"^\s*GNU_RELRO\s", line):
            relro = True
        if re.match(r"^\s*LOAD\s", line):
            fields = line.split()
            load_flags = fields[6:-1] if len(fields) >= 8 else []
            writable_executable = writable_executable or ("W" in load_flags and "E" in load_flags)
    return stack, relro, writable_executable


def observe_elf(path: Path, readelf: Path) -> dict[str, Any] | None:
    """Independently observe the ELF facts represented by artifact state."""
    payload = secure_read(path, fixture_mode=True)
    if not payload.startswith(b"\x7fELF"):
        return None
    if len(payload) < 16 or payload[4] not in {1, 2} or payload[5] not in {1, 2}:
        raise StateValidationError(f"malformed ELF identification: {path}")
    header = _readelf(readelf, path, "-W", "-h")
    sections = _readelf(readelf, path, "-W", "-S")
    program = _readelf(readelf, path, "-W", "-l")
    notes = _readelf(readelf, path, "-W", "-n")
    dynamic_result = subprocess.run(
        [str(readelf), "-W", "-d", str(path)], check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "HOME": "/"}, timeout=30,
    )
    if dynamic_result.returncode not in {0, 1}:
        raise StateValidationError(f"readelf dynamic inspection failed: {path}")
    dynamic = dynamic_result.stdout.decode("utf-8", errors="strict")
    symbols_result = subprocess.run(
        [str(readelf), "-W", "--dyn-syms", str(path)], check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "HOME": "/"}, timeout=30,
    )
    symbols = symbols_result.stdout.decode("utf-8", errors="strict") if symbols_result.returncode in {0, 1} else ""
    versions_result = subprocess.run(
        [str(readelf), "-W", "--version-info", str(path)], check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "HOME": "/"}, timeout=30,
    )
    versions_text = versions_result.stdout.decode("utf-8", errors="strict") if versions_result.returncode in {0, 1} else ""
    header_values: dict[str, str] = {}
    for line in header.splitlines():
        match = re.match(r"^\s*(Class|Data|Type|Machine):\s*(.*?)\s*$", line)
        if match:
            header_values[match.group(1)] = match.group(2)
    elf_type_raw = header_values.get("Type", "")
    elf_type = elf_type_raw.split()[0] if elf_type_raw else "OTHER"
    if elf_type not in {"EXEC", "DYN", "REL", "CORE"}:
        elf_type = "OTHER"
    section_rows: list[tuple[str, str, int, int, str]] = []
    for line in sections.splitlines():
        match = re.match(
            r"^\s*\[\s*\d+\]\s+(\S+)\s+(\S+)\s+[0-9A-Fa-f]+\s+"
            r"([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s+\S+\s+(\S*)\s+",
            line,
        )
        if match:
            section_rows.append((match.group(1), match.group(2), int(match.group(3), 16), int(match.group(4), 16), match.group(5)))
    text_rows = [row for row in section_rows if row[0] == ".text"]
    text_sha = None
    if text_rows:
        _name, _kind, offset, size, _flags = text_rows[0]
        if offset + size > len(payload):
            raise StateValidationError(f"ELF .text exceeds file: {path}")
        text_sha = hashlib.sha256(payload[offset:offset + size]).hexdigest()
    debuglink = None
    debuglink_rows = [row for row in section_rows if row[0] == ".gnu_debuglink"]
    if debuglink_rows:
        _name, _kind, offset, size, _flags = debuglink_rows[0]
        if offset + size > len(payload):
            raise StateValidationError(f"ELF .gnu_debuglink exceeds file: {path}")
        raw_debuglink = payload[offset:offset + size].split(b"\x00", 1)[0]
        try:
            debuglink = raw_debuglink.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise StateValidationError(f"invalid ELF .gnu_debuglink name: {path}") from error
    build_match = re.search(r"Build ID:\s*([0-9A-Fa-f]+)", notes)
    build_id = build_match.group(1).lower() if build_match else None
    def dynamic_values(tag: str) -> list[str]:
        return re.findall(rf"\({re.escape(tag)}\).*?\[(.*?)\]", dynamic)
    needed = sorted(set(dynamic_values("NEEDED")))
    sonames = dynamic_values("SONAME")
    rpath = sorted(set(part for value in dynamic_values("RPATH") for part in value.split(":")))
    runpath = sorted(set(part for value in dynamic_values("RUNPATH") for part in value.split(":")))
    interpreter_match = re.search(r"Requesting program interpreter:\s*([^\]]+)\]", program)
    interpreter = interpreter_match.group(1) if interpreter_match else None
    stack, relro, writable_executable = parse_program_security(program)
    bind_now = bool(re.search(r"\(BIND_NOW\)|FLAGS.*\bNOW\b|FLAGS_1.*\bNOW\b", dynamic))
    exports: list[dict[str, Any]] = []
    defined_versions: list[tuple[str, bool]] = []
    for line in symbols.splitlines():
        match = re.match(r"^\s*\d+:\s+[0-9A-Fa-f]+\s+\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+?)\s*$", line)
        if not match:
            continue
        symbol_type, binding, visibility, ndx, raw_name = match.groups()
        raw_name = re.sub(r"\s+\(\d+\)$", "", raw_name)
        if ndx == "UND" or binding not in {"GLOBAL", "WEAK", "GNU_UNIQUE"} or not raw_name:
            continue
        version: str | None = None
        name = raw_name
        if "@@" in raw_name:
            name, version = raw_name.split("@@", 1)
            defined_versions.append((version, True))
        elif "@" in raw_name:
            name, version = raw_name.split("@", 1)
            defined_versions.append((version, False))
        exports.append({"name": name, "version": version, "binding": binding, "visibility": visibility, "type": symbol_type})
    exports.sort(key=lambda entry: (entry["name"], entry["version"] or ""))
    symbol_versions: set[tuple[str, str, bool]] = set()
    provider = sonames[0] if len(sonames) == 1 else path.name
    for version, is_default in defined_versions:
        symbol_versions.add((version, provider, is_default))
    needed_provider: str | None = None
    for line in versions_text.splitlines():
        file_match = re.search(r"\bFile:\s*(\S+)", line)
        if file_match:
            needed_provider = file_match.group(1)
        name_match = re.search(r"\bName:\s*(\S+)", line)
        if name_match and needed_provider is not None:
            symbol_versions.add((name_match.group(1), needed_provider, False))
    cet = sorted(feature for feature in ("IBT", "SHSTK") if re.search(rf"\b{feature}\b", notes))
    pgo_markers: list[str] = []
    section_names = {row[0] for row in section_rows}
    if any(name.startswith("__llvm_prf") for name in section_names):
        pgo_markers.append("clang-ir-instrumented")
    if any(name.startswith(".gcov") or name in {".gcda", ".gcno"} for name in section_names):
        pgo_markers.append("gcc-gcov-instrumented")
    return {
        "class": 64 if payload[4] == 2 else 32,
        "data_encoding": "little" if payload[5] == 1 else "big",
        "type": elf_type,
        "machine": header_values.get("Machine", ""),
        "build_id": build_id,
        "text_sha256": text_sha,
        "has_symbols": any(row[0] == ".symtab" for row in section_rows),
        "has_relocations": any(row[1] in {"REL", "RELA"} for row in section_rows),
        "has_executable_sections": any("X" in row[4] for row in section_rows),
        "soname": sonames[0] if len(sonames) == 1 else None,
        "rpath": rpath,
        "runpath": runpath,
        "exports": exports,
        "symbol_versions": [
            {"name": name, "provider": version_provider, "default": default}
            for name, version_provider, default in sorted(symbol_versions)
        ],
        "debug": {
            "has_debug_info": any(row[0].startswith(".debug_") for row in section_rows),
            "has_full_symtab": any(row[0] == ".symtab" for row in section_rows),
            "gnu_debuglink": debuglink,
        },
        "runtime_instrumentation": {
            "pgo_markers": pgo_markers,
            "bolt_note": any(row[0] == ".note.bolt_info" for row in section_rows),
            "build_id_note": build_id is not None,
            "cet_properties": cet,
        },
        "dynamic_linkage": {
            "is_dynamic": bool(dynamic.strip() and "There is no dynamic section" not in dynamic),
            "pie": elf_type == "DYN" and interpreter is not None,
            "interpreter": interpreter,
            "needed": needed,
        },
        "security": {
            "gnu_stack": stack, "relro": relro, "bind_now": bind_now,
            "writable_executable_load": writable_executable,
        },
    }


def observe_file_magic(path: Path) -> str:
    payload = secure_read(path, fixture_mode=True)
    if payload.startswith(b"\x7fELF"):
        return "elf"
    if payload.startswith(b"!<arch>\n") or payload.startswith(b"!<thin>\n"):
        return "static-archive"
    if payload.startswith(b"#!"):
        return "script"
    return "other"


def _verify_live_elf(artifact: Mapping[str, Any], path: Path, readelf: Path) -> None:
    observed = observe_elf(path, readelf)
    recorded = artifact["elf"]
    if artifact["kind"] in ELF_REQUIRED_KINDS and observed is None:
        _error(f"collection.elf[{artifact['artifact_id']}]", "recorded ELF has non-ELF magic")
    if artifact["kind"] not in ELF_REQUIRED_KINDS and observed is not None:
        _error(f"collection.elf[{artifact['artifact_id']}]", "unrecorded live ELF magic")
    if observed is None or recorded is None:
        return
    for key in (
        "class", "data_encoding", "type", "machine", "build_id", "text_sha256",
        "has_symbols", "has_relocations", "has_executable_sections", "soname",
        "rpath", "runpath", "exports", "symbol_versions", "dynamic_linkage", "security",
    ):
        if recorded[key] != observed[key]:
            _error(f"collection.elf[{artifact['artifact_id']}].{key}", f"live ELF observation differs: {observed[key]!r}")
    for key in ("has_debug_info", "has_full_symtab", "gnu_debuglink"):
        expected = recorded["debug"][key]
        actual = observed["debug"][key]
        if expected != actual:
            _error(f"collection.elf[{artifact['artifact_id']}].debug.{key}", f"live ELF observation differs: {actual!r}")
    for key in ("pgo_markers", "bolt_note", "build_id_note", "cet_properties"):
        if recorded["runtime_instrumentation"][key] != observed["runtime_instrumentation"][key]:
            _error(f"collection.elf[{artifact['artifact_id']}].runtime_instrumentation.{key}", "live ELF observation differs")


def _all_evidence(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if set(value) == {"path", "sha256", "kind"}:
            result.append(value)
        else:
            for child in value.values():
                result.extend(_all_evidence(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_all_evidence(child))
    return result


def _all_tools(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if set(value) == {"role", "family", "path", "realpath", "sha256", "version", "target_triple"}:
            result.append(value)
        else:
            for child in value.values():
                result.extend(_all_tools(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_all_tools(child))
    return result


def _verify_terminal_reason(record: Mapping[str, Any], *, observed_elf: bool | None = None) -> None:
    resolutions = [record.get("resolution")]
    if record.get("record_type") == "artifact":
        resolutions.append(record["bolt"].get("resolution"))
    for resolution in resolutions:
        if resolution is None:
            continue
        code = resolution["reason_code"]
        if record.get("record_type") == "artifact":
            artifact = record
            elf = artifact["elf"]
            facts = {
                "bolt-not-elf": observed_elf is False,
                "not-machine-code": artifact["kind"] in {"firmware", "bytecode", "script", "data"},
                "firmware-not-rebuildable": artifact["kind"] == "firmware",
                "generated-artifact": artifact["kind"] in {"bytecode", "data"},
                "kernel-policy-exclusion": artifact["kind"] in {"kernel-image", "kernel-module"},
                "bolt-no-relocations": elf is not None and not elf["has_relocations"],
                "bolt-no-symbols": elf is not None and not elf["has_symbols"],
                "bolt-unsupported-abi": artifact["abi"] != "amd64",
                "bolt-unsupported-role": artifact["role"] not in {"executable", "pie-executable", "shared-library", "plugin"},
            }
            if code in facts and not facts[code]:
                _error(f"collection.terminal[{artifact['artifact_id']}]", f"reason {code} contradicts independently observed facts")
        else:
            package = record
            if code == "not-machine-code" and any(component["abi"] != "none" for component in package["components"]):
                _error(f"collection.terminal[{package['identity']['cpv']}]", "not-machine-code contradicts component ABIs")
            if code == "kernel-policy-exclusion" and not any(component["component_kind"] == "kernel" for component in package["components"]):
                _error(f"collection.terminal[{package['identity']['cpv']}]", "kernel exclusion lacks a kernel component")


def _verify_bolt_provenance(
    artifact: Mapping[str, Any], readelf: Path, *, fixture_mode: bool,
    bolt_root: Path,
) -> None:
    bolt = artifact["bolt"]
    if bolt["status"] != "optimized":
        return
    capture = bolt["capture"]; fdata = bolt["fdata"]; tools = bolt["tools"]
    command = bolt["command"]; output = bolt["output"]
    assert capture is not None and fdata is not None and tools is not None and command is not None and output is not None
    captured_path = Path(capture["input_path"])
    captured = observe_elf(captured_path, readelf)
    if captured is None or captured["build_id"] != capture["input_build_id"] or captured["text_sha256"] != capture["input_text_sha256"]:
        _error(f"collection.bolt[{artifact['artifact_id']}].capture", "captured input ELF identity differs")
    output_path = Path(output["path"])
    output_elf = observe_elf(output_path, readelf)
    if output_elf is None or output_elf["build_id"] != output["build_id"] or output_elf["text_sha256"] != output["text_sha256"] or output_elf["runtime_instrumentation"]["bolt_note"] is not True:
        _error(f"collection.bolt[{artifact['artifact_id']}].output", "BOLT output ELF/note identity differs")
    command_record = secure_json(
        Path(command["record"]["path"]), command["record"]["sha256"],
        fixture_mode=fixture_mode, allowed_roots=[bolt_root],
    )
    if not isinstance(command_record, dict):
        _error(f"collection.bolt[{artifact['artifact_id']}].command", "command record must be an object")
    required = {
        "schema", "argv", "exit_status", "tool", "input", "output",
        "option_policy_revision", "options", "fdata",
    }
    if not required <= set(command_record):
        _error(f"collection.bolt[{artifact['artifact_id']}].command", "command record lacks authoritative provenance fields")
    if command_record["argv"] != command["argv"] or command_record["exit_status"] != 0:
        _error(f"collection.bolt[{artifact['artifact_id']}].command", "command argv/status differs")
    if command_record["option_policy_revision"] != BOLT_POLICY_REVISION or command_record["options"] != BOLT_APPROVED_ARGV:
        _error(f"collection.bolt[{artifact['artifact_id']}].command", "command policy differs")
    tool_record = command_record["tool"]
    if not isinstance(tool_record, dict) or tool_record.get("path") != tools["llvm_bolt"]["realpath"] or tool_record.get("sha256") != tools["llvm_bolt"]["sha256"]:
        _error(f"collection.bolt[{artifact['artifact_id']}].command", "command tool identity differs")
    input_record = command_record["input"]
    if not isinstance(input_record, dict) or input_record.get("path") != capture["input_path"] or input_record.get("sha256") != capture["input_sha256"]:
        _error(f"collection.bolt[{artifact['artifact_id']}].command", "command input identity differs")
    output_record = command_record["output"]
    if not isinstance(output_record, dict) or output_record.get("sha256") != output["sha256"]:
        _error(f"collection.bolt[{artifact['artifact_id']}].command", "command output identity differs")
    fdata_records = command_record["fdata"]
    if not isinstance(fdata_records, list) or not any(isinstance(entry, dict) and entry.get("path") == fdata["path"] and entry.get("sha256") == fdata["sha256"] for entry in fdata_records):
        _error(f"collection.bolt[{artifact['artifact_id']}].command", "command fdata identity differs")


def _verify_source_transactions(
    packages: Sequence[Mapping[str, Any]], artifacts: Sequence[Mapping[str, Any]],
    final: Mapping[str, Any], *, fixture_mode: bool, evidence_root: Path,
) -> None:
    final_receipt_evidence = final["final_transaction"]["portage_receipt"]
    final_receipt = secure_json(
        Path(final_receipt_evidence["path"]), final_receipt_evidence["sha256"],
        fixture_mode=fixture_mode, allowed_roots=[evidence_root],
    )
    if not isinstance(final_receipt, dict) or set(final_receipt) != {"schema", "generation", "transaction_id", "completed_at", "packages"}:
        _error("collection.final_transaction.portage_receipt", "invalid final transaction receipt schema")
    if final_receipt["schema"] != "gentoo-optimization-final-portage-transaction-v1" or final_receipt["generation"] != final["generation"] or final_receipt["transaction_id"] != final["final_transaction"]["transaction_id"]:
        _error("collection.final_transaction.portage_receipt", "final transaction identity differs")
    if final_receipt["completed_at"] != final["final_transaction"]["completed_at"]:
        _error("collection.final_transaction.portage_receipt", "final transaction completion timestamp differs")
    package_receipts = final_receipt["packages"]
    if not isinstance(package_receipts, list):
        _error("collection.final_transaction.portage_receipt", "packages must be an array")
    receipt_index: dict[str, tuple[str, str]] = {}
    for entry in package_receipts:
        if not isinstance(entry, dict) or set(entry) != {"cpv", "path", "sha256"}:
            _error("collection.final_transaction.portage_receipt", "invalid package receipt entry")
        cpv = _string(entry["cpv"], "collection.final_transaction.portage_receipt.cpv")
        path = _string(entry["path"], "collection.final_transaction.portage_receipt.path", absolute=True)
        digest = _sha(entry["sha256"], "collection.final_transaction.portage_receipt.sha256")
        assert digest is not None
        if cpv in receipt_index:
            _error("collection.final_transaction.portage_receipt", f"duplicate CPV {cpv}")
        receipt_index[cpv] = (path, digest)
    if set(receipt_index) != {package["identity"]["cpv"] for package in packages}:
        _error("collection.final_transaction.portage_receipt", "package receipt coverage is not exact")
    artifacts_by_owner: dict[str, list[Mapping[str, Any]]] = {}
    for artifact in artifacts:
        artifacts_by_owner.setdefault(artifact["owner"]["cpv"], []).append(artifact)
    for package in packages:
        cpv = package["identity"]["cpv"]
        proof = package["source_rebuild"]["proof"]
        if proof is None:
            _error(f"collection.source[{cpv}]", "successful source proof is absent")
        evidence = proof["portage_transaction_receipt"]
        if receipt_index[cpv] != (evidence["path"], evidence["sha256"]):
            _error(f"collection.source[{cpv}]", "package receipt is not registered by the final transaction")
        receipt = secure_json(Path(evidence["path"]), evidence["sha256"], fixture_mode=fixture_mode, allowed_roots=[evidence_root])
        required = {"schema", "generation", "cpv", "transaction_id", "source_only", "emerge_argv", "active_modes", "started_at", "completed_at", "pre_vdb", "post_vdb", "build_log", "profiles", "bolt_artifacts", "binpkg"}
        if not isinstance(receipt, dict) or set(receipt) != required:
            _error(f"collection.source[{cpv}]", "invalid source transaction receipt schema")
        if receipt["schema"] != "gentoo-optimization-source-rebuild-v1" or receipt["generation"] != final["generation"] or receipt["cpv"] != cpv or receipt["transaction_id"] != package["source_rebuild"]["transaction_id"] or receipt["source_only"] is not True:
            _error(f"collection.source[{cpv}]", "source transaction identity/source-only proof differs")
        argv = receipt["emerge_argv"]
        if not isinstance(argv, list) or not all(isinstance(argument, str) for argument in argv):
            _error(f"collection.source[{cpv}].emerge_argv", "must be an exact argument vector")
        if not fixture_mode and (not argv or argv[0] != "/usr/bin/emerge"):
            _error(f"collection.source[{cpv}].emerge_argv", "must invoke exact /usr/bin/emerge")
        if "--usepkg=n" not in argv or "--buildpkg=y" not in argv or f"={cpv}" not in argv or any(argument in {"--usepkgonly", "-K"} for argument in argv):
            _error(f"collection.source[{cpv}].emerge_argv", "does not prove a source-only saved-binpkg build")
        if receipt["active_modes"] != proof["active_modes"]:
            _error(f"collection.source[{cpv}].active_modes", "receipt differs from resolved final modes")
        _timestamp(receipt["started_at"], f"collection.source[{cpv}].started_at")
        _timestamp(receipt["completed_at"], f"collection.source[{cpv}].completed_at")
        if receipt["completed_at"] < receipt["started_at"]:
            _error(f"collection.source[{cpv}]", "receipt completion precedes start")
        for phase in ("pre_vdb", "post_vdb"):
            vdb = receipt[phase]
            if not isinstance(vdb, dict) or set(vdb) != {"build_time", "counter", "identity_sha256"}:
                _error(f"collection.source[{cpv}].{phase}", "invalid VDB boundary")
            for scalar in ("build_time", "counter"):
                scalar_value = _string(vdb[scalar], f"collection.source[{cpv}].{phase}.{scalar}")
                if not scalar_value.isdigit():
                    _error(f"collection.source[{cpv}].{phase}.{scalar}", "must be a nonnegative decimal integer")
            _sha(vdb["identity_sha256"], f"collection.source[{cpv}].{phase}.identity_sha256")
        live = package["live_instance"]
        post = receipt["post_vdb"]
        if post != {"build_time": live["build_time"], "counter": live["counter"], "identity_sha256": live["identity_sha256"]}:
            _error(f"collection.source[{cpv}].post_vdb", "does not equal exact final VDB identity")
        if int(receipt["pre_vdb"]["build_time"]) >= int(post["build_time"]) or int(receipt["pre_vdb"]["counter"]) >= int(post["counter"]):
            _error(f"collection.source[{cpv}]", "VDB BUILD_TIME/COUNTER did not advance across source rebuild")
        successful_attempt = next(attempt for attempt in package["source_rebuild"]["attempts"] if attempt["result"] == "succeeded")
        if receipt["build_log"] != successful_attempt["build_log"]:
            _error(f"collection.source[{cpv}].build_log", "receipt does not bind the successful attempt log")
        build_log = secure_read(Path(receipt["build_log"]["path"]), receipt["build_log"]["sha256"], fixture_mode=fixture_mode, allowed_roots=[evidence_root]).decode("utf-8", errors="strict")
        mode_text = ",".join(proof["active_modes"]) if proof["active_modes"] else "none"
        marker = f"gentoo-optimization-transaction-v1\tgeneration={final['generation']['generation_id']}\tcpv={cpv}\ttransaction={receipt['transaction_id']}\tsource_only=true\tactive_modes={mode_text}"
        if marker not in build_log.splitlines():
            _error(f"collection.source[{cpv}].build_log", "exact dispatcher transaction marker is absent")
        expected_profiles = [
            {"component_id": component["component_id"], "manifest_sha256": component["pgo"]["manifest"]["sha256"], "sidecar_sha256": component["pgo"]["sidecar"]["sha256"], "profile_sha256": component["pgo"]["profile"]["sha256"], "validator_receipt_sha256": component["pgo"]["build_use"]["validator_receipt"]["sha256"]}
            for component in package["components"] if component["pgo"]["status"] == "optimized"
        ]
        if receipt["profiles"] != expected_profiles:
            _error(f"collection.source[{cpv}].profiles", "profile-use receipt is not exact")
        for profile in expected_profiles:
            profile_marker = f"gentoo-optimization-profile-use-v1\tcomponent={profile['component_id']}\tmanifest_sha256={profile['manifest_sha256']}"
            if profile_marker not in build_log.splitlines():
                _error(f"collection.source[{cpv}].build_log", "profile-use marker is absent")
        expected_bolt = [
            {"artifact_id": artifact["artifact_id"], "installed_sha256": artifact["content_sha256"], "deploy_transaction_id": artifact["bolt"]["deployment"]["transaction_id"], "command_record_sha256": artifact["bolt"]["command"]["record"]["sha256"]}
            for artifact in sorted(artifacts_by_owner.get(cpv, []), key=lambda item: item["artifact_id"])
            if artifact["bolt"]["status"] == "optimized"
        ]
        if receipt["bolt_artifacts"] != expected_bolt:
            _error(f"collection.source[{cpv}].bolt_artifacts", "BOLT-deploy receipt is not exact")
        for bolt in expected_bolt:
            bolt_marker = f"gentoo-optimization-bolt-deploy-v1\tartifact_id={bolt['artifact_id']}\tinstalled_sha256={bolt['installed_sha256']}"
            if bolt_marker not in build_log.splitlines():
                _error(f"collection.source[{cpv}].build_log", "BOLT-deploy marker is absent")
        if receipt["binpkg"] != {"path": proof["binpkg"]["path"], "sha256": proof["binpkg"]["sha256"], "format": proof["binpkg"]["format"]}:
            _error(f"collection.source[{cpv}].binpkg", "saved binpkg identity differs")
        marker_evidence = proof["binpkg"]["production_marker"]
        production_marker = secure_json(Path(marker_evidence["path"]), marker_evidence["sha256"], fixture_mode=fixture_mode, allowed_roots=[evidence_root])
        expected_marker = {"schema": "gentoo-optimization-binpkg-production-v1", "generation": final["generation"], "cpv": cpv, "transaction_id": receipt["transaction_id"], "active_modes": proof["active_modes"], "binpkg_sha256": proof["binpkg"]["sha256"], "vdb_identity_sha256": live["identity_sha256"]}
        if production_marker != expected_marker:
            _error(f"collection.source[{cpv}].binpkg", "saved binpkg production marker differs")


def _graph_key(ref: Mapping[str, Any]) -> tuple[str, str]:
    return ref["cpv"], ref["component_id"] or ""


def _graph_evidence_sha(ref: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes({"evidence": ref["evidence"]})).hexdigest()


def _verify_registries(
    packages: Sequence[Mapping[str, Any]], artifacts: Sequence[Mapping[str, Any]],
    final_state: Mapping[str, Any],
) -> None:
    registry_workloads = {
        (entry["workload_id"], entry["revision"], hashlib.sha256(canonical_bytes({"evidence": entry["evidence"]})).hexdigest())
        for entry in final_state["registries"]["workloads"]
    }
    referenced_workloads = {
        (entry["workload_id"], entry["revision"], hashlib.sha256(canonical_bytes({"evidence": entry["evidence"]})).hexdigest())
        for record in [*packages, *artifacts] for entry in record["graphs"]["workload_refs"]
    }
    if registry_workloads != referenced_workloads:
        _error("collection.registries.workloads", "frozen workload registry is not exact")
    package_by_cpv = {package["identity"]["cpv"]: package for package in packages}
    component_index = {
        (package["identity"]["cpv"], component["component_id"])
        for package in packages for component in package["components"]
    }
    edges: set[tuple[str, str, str, str]] = set()
    for edge in final_state["registries"]["dependency_edges"]:
        key = (edge["consumer_cpv"], edge["consumer_component_id"], edge["provider_cpv"], edge["provider_component_id"])
        if (key[0], key[1]) not in component_index or (key[2], key[3]) not in component_index:
            _error("collection.registries.dependency_edges", f"edge endpoint is absent: {key}")
        edges.add(key)
    expected_consumers: dict[tuple[str, str], set[tuple[str, str]]] = {endpoint: set() for endpoint in component_index}
    expected_reverse: dict[tuple[str, str], set[tuple[str, str]]] = {endpoint: set() for endpoint in component_index}
    consumer_evidence: dict[tuple[tuple[str, str], tuple[str, str]], str] = {}
    reverse_evidence: dict[tuple[tuple[str, str], tuple[str, str]], str] = {}
    edge_documents = {
        (entry["consumer_cpv"], entry["consumer_component_id"], entry["provider_cpv"], entry["provider_component_id"]): entry
        for entry in final_state["registries"]["dependency_edges"]
    }
    for consumer_cpv, consumer_component, provider_cpv, provider_component in edges:
        consumer_endpoint = (consumer_cpv, consumer_component)
        provider_endpoint = (provider_cpv, provider_component)
        expected_consumers[(consumer_cpv, consumer_component)].add((provider_cpv, provider_component))
        expected_reverse[(provider_cpv, provider_component)].add((consumer_cpv, consumer_component))
        evidence_sha = hashlib.sha256(canonical_bytes({"evidence": edge_documents[(consumer_cpv, consumer_component, provider_cpv, provider_component)]["evidence"]})).hexdigest()
        consumer_evidence[(consumer_endpoint, provider_endpoint)] = evidence_sha
        reverse_evidence[(provider_endpoint, consumer_endpoint)] = evidence_sha
    for package in packages:
        cpv = package["identity"]["cpv"]
        actual_consumers = {_graph_key(ref) for ref in package["graphs"]["consumer_refs"]}
        actual_reverse = {_graph_key(ref) for ref in package["graphs"]["reverse_dependency_refs"]}
        expected_package_consumers = set().union(*(expected_consumers[(cpv, component["component_id"])] for component in package["components"]))
        expected_package_reverse = set().union(*(expected_reverse[(cpv, component["component_id"])] for component in package["components"]))
        if actual_consumers != expected_package_consumers or actual_reverse != expected_package_reverse:
            _error(f"collection.registries.graphs[{cpv}]", "consumer/reverse-dependency graph is incomplete or asymmetric")
        for ref in package["graphs"]["consumer_refs"]:
            matches = {consumer_evidence[(endpoint, _graph_key(ref))] for endpoint in component_index if endpoint[0] == cpv and (endpoint, _graph_key(ref)) in consumer_evidence}
            if matches != {_graph_evidence_sha(ref)}:
                _error(f"collection.registries.graphs[{cpv}]", "consumer evidence differs from frozen edge registry")
        for ref in package["graphs"]["reverse_dependency_refs"]:
            matches = {reverse_evidence[(endpoint, _graph_key(ref))] for endpoint in component_index if endpoint[0] == cpv and (endpoint, _graph_key(ref)) in reverse_evidence}
            if matches != {_graph_evidence_sha(ref)}:
                _error(f"collection.registries.graphs[{cpv}]", "reverse-dependency evidence differs from frozen edge registry")
    for artifact in artifacts:
        endpoint = (artifact["owner"]["cpv"], artifact["owner"]["component_id"])
        if {_graph_key(ref) for ref in artifact["graphs"]["consumer_refs"]} != expected_consumers[endpoint]:
            _error(f"collection.registries.graphs[{artifact['artifact_id']}]", "artifact consumer graph is not exact")
        if {_graph_key(ref) for ref in artifact["graphs"]["reverse_dependency_refs"]} != expected_reverse[endpoint]:
            _error(f"collection.registries.graphs[{artifact['artifact_id']}]", "artifact reverse-dependency graph is not exact")
        for ref in artifact["graphs"]["consumer_refs"]:
            if consumer_evidence[(endpoint, _graph_key(ref))] != _graph_evidence_sha(ref):
                _error(f"collection.registries.graphs[{artifact['artifact_id']}]", "artifact consumer evidence differs")
        for ref in artifact["graphs"]["reverse_dependency_refs"]:
            if reverse_evidence[(endpoint, _graph_key(ref))] != _graph_evidence_sha(ref):
                _error(f"collection.registries.graphs[{artifact['artifact_id']}]", "artifact reverse-dependency evidence differs")


def _verify_validator_path(
    record: Mapping[str, Any], expected: Path, *, fixture_mode: bool, executable: bool = True,
) -> Path:
    path = Path(record["path"])
    expected_real = expected.resolve(strict=True) if not fixture_mode else path
    if not fixture_mode and path != expected_real:
        raise StateValidationError(f"validator path must be exact trusted executable {expected_real}: {path}")
    metadata = _assert_trusted_path(path, regular=True, fixture_mode=fixture_mode)
    if executable and not metadata.st_mode & stat.S_IXUSR:
        raise StateValidationError(f"validator is not executable: {path}")
    secure_read(path, record["sha256"], fixture_mode=fixture_mode)
    return path


def _tree_identity(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISDIR(metadata.st_mode):
            entries.append({"path": relative, "type": "directory", "sha256": None, "target": None})
        elif stat.S_ISREG(metadata.st_mode):
            entries.append({"path": relative, "type": "regular", "sha256": _file_sha(path), "target": None})
        elif stat.S_ISLNK(metadata.st_mode):
            entries.append({"path": relative, "type": "symlink", "sha256": None, "target": os.readlink(path)})
        else:
            raise StateValidationError(f"unsupported module-tree entry: {path}")
    return entries


def _verify_boot(final_state: Mapping[str, Any], validators: Mapping[str, Path], artifacts: Sequence[Mapping[str, Any]], *, fixture_mode: bool) -> str:
    boot = final_state["boot"]
    if fixture_mode:
        # Hermetic fixtures prove wire/reopen behavior but can never authorize completion.
        return _string(boot["boot_id"], "collection.boot.boot_id")
    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    boot_id_before = boot_id_path.read_text(encoding="ascii").strip()
    if boot_id_before != boot["boot_id"]:
        _error("collection.boot.boot_id", "live boot ID differs from frozen final boot")
    kernel_release = _run_exact(validators["uname"], ["-r"]).decode("utf-8", errors="strict").strip()
    if kernel_release != boot["kernel_release"]:
        _error("collection.boot.kernel_release", "live uname release differs")
    kernel_artifacts = [
        artifact for artifact in artifacts
        if artifact["kind"] == "kernel-image"
        and artifact["canonical_path"] == boot["kernel_image"]["path"]
        and artifact["content_sha256"] == boot["kernel_image"]["sha256"]
        and artifact["kernel"] is not None
        and artifact["kernel"]["release"] == kernel_release
    ]
    if len(kernel_artifacts) != 1:
        _error("collection.boot.kernel_image", "must bind exactly one installed kernel artifact for the running release")
    efibootmgr = _run_exact(validators["efibootmgr"], ["-v"])
    if hashlib.sha256(efibootmgr).hexdigest() != boot["efibootmgr_output_sha256"]:
        _error("collection.boot.efibootmgr_output_sha256", "live EFI state differs")
    current_match = re.search(rb"(?m)^BootCurrent:\s*([0-9A-Fa-f]{4})\s*$", efibootmgr)
    if current_match is None or current_match.group(1).decode().upper() != boot["boot_current"].upper():
        _error("collection.boot.boot_current", "live BootCurrent differs")
    current_line = next((line for line in efibootmgr.decode("utf-8", errors="replace").splitlines() if line.upper().startswith(f"BOOT{boot['boot_current'].upper()}")), "")
    loader_path = Path(boot["efi_loader"]["path"])
    if not loader_path.is_relative_to(Path("/efi")):
        _error("collection.boot.efi_loader", "EFI loader must be on /efi")
    firmware_loader = "\\" + "\\".join(loader_path.relative_to("/efi").parts)
    if firmware_loader.casefold() not in current_line.casefold():
        _error("collection.boot.efi_loader", "BootCurrent does not reference the recorded /efi loader")
    kernel_path = Path(boot["kernel_image"]["path"])
    if not kernel_path.is_relative_to(Path("/efi")):
        _error("collection.boot.kernel_image", "kernel image must be on /efi")
    firmware_kernel = "\\" + "\\".join(kernel_path.relative_to("/efi").parts)
    if loader_path != kernel_path and firmware_kernel.casefold() not in current_line.casefold():
        _error("collection.boot.kernel_image", "BootCurrent options do not select the recorded kernel image")
    initramfs_path = Path(boot["initramfs"]["path"])
    if not initramfs_path.is_relative_to(Path("/efi")):
        _error("collection.boot.initramfs", "initramfs must be on /efi")
    firmware_initramfs = "\\" + "\\".join(initramfs_path.relative_to("/efi").parts)
    if firmware_initramfs.casefold() not in current_line.casefold():
        _error("collection.boot.initramfs", "BootCurrent options do not select the recorded initramfs")
    openrc = _run_exact(validators["rc_status"], ["--all"])
    if hashlib.sha256(openrc).hexdigest() != boot["openrc_output_sha256"]:
        _error("collection.boot.openrc_output_sha256", "live OpenRC state differs")
    modules_root = Path("/lib/modules") / kernel_release
    if not modules_root.is_dir():
        _error("collection.boot.modules_manifest", "running kernel module tree is absent")
    try:
        modules_document = json.loads(secure_read(Path(boot["modules_manifest"]["path"]), boot["modules_manifest"]["sha256"]))
    except (OSError, json.JSONDecodeError) as error:
        raise StateValidationError(f"invalid modules manifest: {error}") from error
    if modules_document != {"kernel_release": kernel_release, "entries": _tree_identity(modules_root)}:
        _error("collection.boot.modules_manifest", "running kernel module tree differs from the exact manifest")
    if len(boot["reboot_evidence"]) != 1:
        _error("collection.boot.reboot_evidence", "requires exactly one post-final-build reboot receipt")
    reboot_evidence = boot["reboot_evidence"][0]
    reboot = secure_json(Path(reboot_evidence["path"]), reboot_evidence["sha256"])
    expected_reboot_keys = {"schema", "generation", "final_transaction_id", "final_transaction_completed_at", "pre_boot_id", "post_boot_id", "observed_at", "kernel_release", "boot_current", "efi_loader_sha256", "portage_receipt_sha256", "vdb_receipt_sha256"}
    if not isinstance(reboot, dict) or set(reboot) != expected_reboot_keys:
        _error("collection.boot.reboot_evidence", "invalid reboot receipt schema")
    transaction = final_state["final_transaction"]
    expected_reboot = {
        "schema": "gentoo-optimization-post-final-reboot-v1", "generation": final_state["generation"],
        "final_transaction_id": transaction["transaction_id"], "final_transaction_completed_at": transaction["completed_at"],
        "post_boot_id": boot_id_before, "kernel_release": kernel_release,
        "boot_current": boot["boot_current"], "efi_loader_sha256": boot["efi_loader"]["sha256"],
        "portage_receipt_sha256": transaction["portage_receipt"]["sha256"],
        "vdb_receipt_sha256": transaction["vdb_receipt"]["sha256"],
    }
    for key, expected in expected_reboot.items():
        if reboot.get(key) != expected:
            _error("collection.boot.reboot_evidence", f"reboot receipt differs for {key}")
    pre_boot_id = _string(reboot.get("pre_boot_id"), "collection.boot.reboot_evidence.pre_boot_id")
    observed_at = _timestamp(reboot.get("observed_at"), "collection.boot.reboot_evidence.observed_at")
    if pre_boot_id == boot_id_before or observed_at is None or observed_at <= transaction["completed_at"]:
        _error("collection.boot.reboot_evidence", "does not prove a distinct boot after final transaction completion")
    return boot_id_before


def verify_authoritative_state(
    packages: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    inventory: Mapping[str, Any],
    final_state: Mapping[str, Any],
    *,
    packages_dir: Path,
    artifacts_dir: Path,
    inventory_path: Path,
    vdb_root: Path,
    installed_root: Path,
    fixture_mode: bool,
) -> dict[str, Any]:
    """Reopen and independently revalidate every proof used for completion."""
    final = validate_final_system_state(final_state)
    record_generation = packages[0]["generation"] if packages else None
    if record_generation is None or final["generation"] != record_generation:
        _error("collection.final_system_state.generation", "does not equal record generation")
    roots = {key: Path(value) for key, value in final["trusted_roots"].items()}
    if (roots["packages_dir"], roots["artifacts_dir"], roots["inventory"]) != (packages_dir, artifacts_dir, inventory_path):
        _error("collection.trusted_roots", "record/inventory paths differ from final generation authority")
    if not fixture_mode:
        if vdb_root != AUTHORITATIVE_VDB_ROOT or installed_root != AUTHORITATIVE_INSTALLED_ROOT:
            _error("collection.roots", "authoritative mode requires exact /var/db/pkg and /")
        if not _path_beneath(roots["generation_root"], [TRUSTED_STATE_ROOT]):
            _error("collection.trusted_roots.generation_root", "must be below /var/lib/gentoo-optimization")
        if not _path_beneath(roots["evidence_root"], [roots["generation_root"]]):
            _error("collection.trusted_roots.evidence_root", "must be below generation_root")
        for key in ("profiles_root", "bolt_root", "binpkg_snapshot"):
            if not _path_beneath(roots[key], [TRUSTED_CACHE_ROOT]):
                _error(f"collection.trusted_roots.{key}", "must be below /var/cache/gentoo-optimization")
        if not _path_beneath(packages_dir, [roots["generation_root"]]) or not _path_beneath(artifacts_dir, [roots["generation_root"]]) or not _path_beneath(inventory_path, [roots["generation_root"]]):
            _error("collection.trusted_roots", "records and inventory must be below generation_root")
    for key in ("generation_root", "evidence_root", "profiles_root", "bolt_root", "binpkg_snapshot", "packages_dir", "artifacts_dir"):
        _assert_trusted_path(roots[key], regular=False, fixture_mode=fixture_mode)
    _assert_trusted_path(inventory_path, regular=True, fixture_mode=fixture_mode)
    allowed_proof_roots = [roots["generation_root"], roots["profiles_root"], roots["bolt_root"], roots["binpkg_snapshot"], Path("/efi")]
    lock_payloads: dict[str, bytes] = {}
    lock_descriptors: list[int] = []
    for key in ("framework", "project", "generation"):
        evidence = final["locks"][key]
        if not fixture_mode and Path(evidence["path"]) != AUTHORITATIVE_LOCKS[key]:
            _error(f"collection.locks.{key}", f"must use shared writer lock {AUTHORITATIVE_LOCKS[key]}")
        payload = secure_read(Path(evidence["path"]), evidence["sha256"], fixture_mode=fixture_mode)
        lock_payloads[key] = payload
        if key == "framework":
            if payload != b"":
                _error("collection.locks.framework", "stable framework-install lock inode must remain empty")
        else:
            try:
                lock_generation = json.loads(payload)
            except json.JSONDecodeError as error:
                raise StateValidationError(f"invalid {key} lock JSON") from error
            if lock_generation != final["generation"]:
                _error(f"collection.locks.{key}", "must contain the exact generation identity")
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        lock_descriptor = os.open(evidence["path"], flags)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as error:
            os.close(lock_descriptor)
            raise StateValidationError(f"cannot acquire stable {key} lock: {error}") from error
        lock_descriptors.append(lock_descriptor)
    validator_expected = {
        "state_runtime": AUTHORITATIVE_STATE_RUNTIME,
        "reconciler_runtime": AUTHORITATIVE_RECONCILER_RUNTIME,
        "profile": AUTHORITATIVE_PROFILE_VALIDATOR,
        "binpkg_snapshot": AUTHORITATIVE_BINPKG_VALIDATOR,
        "readelf": PRODUCTION_READELF,
        "getcap": PRODUCTION_GETCAP,
        "uname": PRODUCTION_UNAME,
        "efibootmgr": PRODUCTION_EFIBOOTMGR,
        "rc_status": PRODUCTION_RC_STATUS,
    }
    validators = {
        key: _verify_validator_path(
            final["validators"][key], expected, fixture_mode=fixture_mode,
            executable=key not in {"state_runtime", "reconciler_runtime"},
        )
        for key, expected in validator_expected.items()
    }
    # Every evidence object is a real stable file inside a declared trusted root.
    evidence_by_path: dict[str, str] = {}
    for record in [*packages, *artifacts, inventory, final]:
        for evidence in _all_evidence(record):
            previous = evidence_by_path.setdefault(evidence["path"], evidence["sha256"])
            if previous != evidence["sha256"]:
                _error("collection.evidence", f"conflicting hashes for {evidence['path']}")
    for path_text, digest in sorted(evidence_by_path.items()):
        secure_read(Path(path_text), digest, fixture_mode=fixture_mode, allowed_roots=allowed_proof_roots)
    for directory in inventory["owned_directories"]:
        installed_directory = _rooted_path(installed_root, directory["path"])
        directory_metadata = installed_directory.lstat()
        if not stat.S_ISDIR(directory_metadata.st_mode) or stat.S_ISLNK(directory_metadata.st_mode):
            _error("collection.installed_directories", f"not a real directory: {directory['path']}")
        observed_directory = {
            "mode": stat.S_IMODE(directory_metadata.st_mode),
            "uid": directory_metadata.st_uid,
            "gid": directory_metadata.st_gid,
        }
        if any(directory[key] != value for key, value in observed_directory.items()):
            _error("collection.installed_directories", f"metadata differs for {directory['path']}")
    # Reopen every tool/runtime/binpkg/profile/BOLT payload, not merely its evidence receipt.
    for tool in _all_tools([*packages, *artifacts]):
        alias = Path(tool["path"])
        real = Path(tool["realpath"])
        _assert_trusted_path(alias.parent, regular=False, fixture_mode=fixture_mode)
        alias_before = alias.lstat()
        if not fixture_mode and (alias_before.st_uid != 0 or stat.S_IMODE(alias_before.st_mode) & 0o022):
            _error("collection.tools", f"tool alias is not root-owned/non-writable: {alias}")
        if alias.resolve(strict=True) != real:
            _error("collection.tools", f"tool alias no longer resolves exactly: {alias}")
        secure_read(real, tool["sha256"], fixture_mode=fixture_mode, allowed_roots=[])
        alias_after = alias.lstat()
        if (alias_before.st_dev, alias_before.st_ino, alias_before.st_mtime_ns, alias_before.st_ctime_ns) != (alias_after.st_dev, alias_after.st_ino, alias_after.st_mtime_ns, alias_after.st_ctime_ns):
            _error("collection.tools", f"tool alias changed during verification: {alias}")
        version = _run_exact(real, ["--version"]).decode("utf-8", errors="strict").strip()
        if version != tool["version"]:
            _error("collection.tools", f"tool version output differs: {real}")
    for package in packages:
        proof = package["source_rebuild"]["proof"]
        if proof is not None:
            secure_read(Path(proof["binpkg"]["path"]), proof["binpkg"]["sha256"], fixture_mode=fixture_mode, allowed_roots=[roots["binpkg_snapshot"]])
            if proof["binpkg_validation_receipt"] != final["final_transaction"]["binpkg_snapshot_receipt"]:
                _error(f"collection.source[{package['identity']['cpv']}]", "package is not bound to the final binpkg validation receipt")
        for component in package["components"]:
            toolchain = component["toolchain"]
            if toolchain is not None:
                for runtime in toolchain["runtimes"]:
                    secure_read(Path(runtime["path"]), runtime["sha256"], fixture_mode=fixture_mode)
            pgo = component["pgo"]
            if pgo["status"] == "optimized":
                result = _run_exact(validators["profile"], ["verify", "--manifest", pgo["manifest"]["path"], "--metadata", pgo["sidecar"]["path"]], timeout=120)
                receipt = pgo["build_use"]["validator_receipt"]
                if hashlib.sha256(result).hexdigest() != receipt["sha256"]:
                    _error("collection.pgo", "authoritative validator output differs from receipt")
        _verify_terminal_reason(package)
    _verify_source_transactions(packages, artifacts, final, fixture_mode=fixture_mode, evidence_root=roots["evidence_root"])
    for artifact in artifacts:
        canonical = _rooted_path(installed_root, artifact["canonical_path"])
        magic_kind = observe_file_magic(canonical)
        if artifact["kind"] in ELF_REQUIRED_KINDS and magic_kind != "elf":
            _error(f"collection.magic[{artifact['artifact_id']}]", "machine-code artifact does not have ELF magic")
        if artifact["kind"] == "static-archive" and magic_kind != "static-archive":
            _error(f"collection.magic[{artifact['artifact_id']}]", "static archive magic differs")
        if artifact["kind"] == "script" and magic_kind != "script":
            _error(f"collection.magic[{artifact['artifact_id']}]", "script magic differs")
        if artifact["kind"] not in ELF_REQUIRED_KINDS and magic_kind == "elf":
            _error(f"collection.magic[{artifact['artifact_id']}]", "live ELF was classified as a non-ELF artifact")
        observed = observe_elf(canonical, validators["readelf"])
        _verify_live_elf(artifact, canonical, validators["readelf"])
        if artifact["elf"] is not None and artifact["elf"]["debug"]["separate_debug_path"] is not None:
            secure_read(
                Path(artifact["elf"]["debug"]["separate_debug_path"]),
                artifact["elf"]["debug"]["separate_debug_sha256"],
                fixture_mode=fixture_mode,
            )
        _verify_terminal_reason(artifact, observed_elf=observed is not None)
        bolt = artifact["bolt"]
        if bolt["capture"] is not None:
            capture = bolt["capture"]
            secure_read(Path(capture["input_path"]), capture["input_sha256"], fixture_mode=fixture_mode, allowed_roots=[roots["bolt_root"]])
        if bolt["fdata"] is not None:
            secure_read(Path(bolt["fdata"]["path"]), bolt["fdata"]["sha256"], fixture_mode=fixture_mode, allowed_roots=[roots["bolt_root"]])
        if bolt["output"] is not None:
            secure_read(Path(bolt["output"]["path"]), bolt["output"]["sha256"], fixture_mode=fixture_mode, allowed_roots=[roots["bolt_root"]])
        _verify_bolt_provenance(artifact, validators["readelf"], fixture_mode=fixture_mode, bolt_root=roots["bolt_root"])
    _verify_registries(packages, artifacts, final)
    required_modes: set[str] = set()
    if any(component["pgo"]["status"] == "optimized" for package in packages for component in package["components"]):
        required_modes.add("pgo-use")
    if any(artifact["bolt"]["status"] == "optimized" for artifact in artifacts):
        required_modes.add("bolt-deploy")
    if set(final["final_transaction"]["active_modes"]) != required_modes:
        _error("collection.final_transaction.active_modes", f"must exactly equal {sorted(required_modes)}")
    # The binpkg authority revalidates archive structure and exact live VDB coverage.
    binpkg_output = _run_exact(validators["binpkg_snapshot"], [
        "--snapshot", str(roots["binpkg_snapshot"]), "--vdb", str(vdb_root),
        "--validate-gpkg", "--format", "json",
    ], timeout=3600)
    if hashlib.sha256(binpkg_output).hexdigest() != final["final_transaction"]["binpkg_snapshot_receipt"]["sha256"]:
        _error("collection.binpkg_snapshot", "live semantic validator output differs from final receipt")
    try:
        binpkg_report = json.loads(binpkg_output)
    except json.JSONDecodeError as error:
        raise StateValidationError("binpkg validator did not return JSON") from error
    if binpkg_report.get("status") not in {"pass", "PASS", True}:
        _error("collection.binpkg_snapshot", "authoritative binpkg semantic validation failed")
    boot_id = _verify_boot(final, validators, artifacts, fixture_mode=fixture_mode)
    # Close the long-running semantic-validation TOCTOU window over VDB and live files.
    for package in packages:
        live = package["live_instance"]
        instance = Path(live["vdb_path"])
        _assert_trusted_path(instance, regular=False, fixture_mode=fixture_mode)
        secure_read(instance / "CONTENTS", live["contents_sha256"], fixture_mode=fixture_mode, allowed_roots=[vdb_root])
        if vdb_metadata_tree_sha256(instance) != live["metadata_tree_sha256"]:
            _error(f"collection.vdb[{package['identity']['cpv']}]", "VDB changed during reconciliation")
        if _read_vdb_scalar(instance / "SLOT") != live["slot_raw"]:
            _error(f"collection.vdb[{package['identity']['cpv']}].SLOT", "changed during reconciliation")
        environment = instance / "environment.bz2"
        environment_sha = hashlib.sha256(secure_read(environment, fixture_mode=fixture_mode, allowed_roots=[vdb_root])).hexdigest() if environment.exists() else None
        if environment_sha != live["environment_bz2_sha256"]:
            _error(f"collection.vdb[{package['identity']['cpv']}].environment.bz2", "changed during reconciliation")
    verify_installed_artifacts(artifacts, installed_root)
    for key in ("framework", "project", "generation"):
        evidence = final["locks"][key]
        if secure_read(Path(evidence["path"]), evidence["sha256"], fixture_mode=fixture_mode) != lock_payloads[key]:
            _error(f"collection.locks.{key}", "changed during reconciliation")
    if not fixture_mode and Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip() != boot_id:
        _error("collection.boot.boot_id", "boot changed during reconciliation")
    for descriptor in reversed(lock_descriptors):
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    return {"authoritative": not fixture_mode, "strict_verified": True, "boot_id": boot_id}


def reconcile_collection(
    packages: Sequence[dict[str, Any]],
    artifacts: Sequence[dict[str, Any]],
    *,
    inventory: dict[str, Any] | None = None,
    inventory_sha256: str | None = None,
    vdb_root: Path | None = None,
    installed_root: Path | None = None,
    final_system_state: dict[str, Any] | None = None,
    packages_dir: Path | None = None,
    artifacts_dir: Path | None = None,
    inventory_path: Path | None = None,
    strict: bool = False,
    fixture_mode: bool = False,
) -> dict[str, Any]:
    """Validate links and derive authoritative completion totals for a generation."""
    validated_packages = [validate_package(package) for package in packages]
    validated_artifacts = [validate_artifact(artifact) for artifact in artifacts]
    package_by_cpv: dict[str, dict[str, Any]] = {}
    generations: set[tuple[str, str, str]] = set()
    for package in validated_packages:
        cpv = package["identity"]["cpv"]
        if cpv in package_by_cpv:
            _error("collection.packages", f"duplicate CPV {cpv}")
        package_by_cpv[cpv] = package
        generation = package["generation"]
        generations.add((generation["generation_id"], generation["inventory_id"], generation["inventory_sha256"]))
    if len(generations) != 1:
        _error("collection.generation", "all package records must share one exact generation/inventory identity")
    generation_tuple = next(iter(generations)) if generations else ("", "", "")
    component_index: dict[tuple[str, str], dict[str, Any]] = {}
    for cpv, package in package_by_cpv.items():
        for component in package["components"]:
            component_index[(cpv, component["component_id"])] = component
    artifact_ids: set[str] = set()
    inode_owners: dict[tuple[int, int], str] = {}
    topology_owners: dict[str, tuple[str, str]] = {}
    artifacts_by_owner: dict[str, list[dict[str, Any]]] = {cpv: [] for cpv in package_by_cpv}
    for artifact in validated_artifacts:
        if artifact["artifact_id"] in artifact_ids:
            _error("collection.artifacts", f"duplicate artifact_id {artifact['artifact_id']}")
        artifact_ids.add(artifact["artifact_id"])
        inode_key = (artifact["topology"]["device"], artifact["topology"]["inode"])
        previous_inode = inode_owners.get(inode_key)
        if previous_inode is not None:
            _error("collection.topology", f"split inode {inode_key} across {previous_inode} and {artifact['artifact_id']}")
        inode_owners[inode_key] = artifact["artifact_id"]
        generation = artifact["generation"]
        if (generation["generation_id"], generation["inventory_id"], generation["inventory_sha256"]) != generation_tuple:
            _error("collection.generation", f"artifact {artifact['artifact_id']} has a different generation")
        owner = artifact["owner"]
        component = component_index.get((owner["cpv"], owner["component_id"]))
        if component is None:
            _error("collection.owner", f"unresolved owner component {owner['cpv']}:{owner['component_id']}")
        if owner["component_fingerprint"] != component["fingerprint"]:
            _error("collection.owner", f"fingerprint mismatch for {owner['cpv']}:{owner['component_id']}")
        if owner["cp"] != package_by_cpv[owner["cpv"]]["identity"]["cp"]:
            _error("collection.owner", f"CP mismatch for {owner['cpv']}")
        if artifact["abi"] != component["abi"] and artifact["abi"] != "none":
            _error("collection.owner", f"ABI mismatch for artifact {artifact['artifact_id']}")
        artifacts_by_owner[owner["cpv"]].append(artifact)
        paths = list(artifact["topology"]["hardlink_paths"]) + [link["path"] for link in artifact["topology"]["symlinks"]]
        for installed in paths:
            previous = topology_owners.get(installed)
            current = (owner["cpv"], artifact["artifact_id"])
            if previous is not None:
                _error("collection.topology", f"ambiguous path {installed}: {previous} and {current}")
            topology_owners[installed] = current
    # Graph references must resolve after the complete CPV/component index exists.
    for record_name, records in (("package", validated_packages), ("artifact", validated_artifacts)):
        for record in records:
            for field in ("consumer_refs", "reverse_dependency_refs"):
                for ref in record["graphs"][field]:
                    if ref["cpv"] not in package_by_cpv:
                        _error("collection.graph", f"{record_name} references absent CPV {ref['cpv']}")
                    if ref["component_id"] is not None and (ref["cpv"], ref["component_id"]) not in component_index:
                        _error("collection.graph", f"unresolved component ref {ref['cpv']}:{ref['component_id']}")
    # Package artifact and BOLT aggregates are claims: independently rederive them.
    for cpv, package in package_by_cpv.items():
        owned = artifacts_by_owner[cpv]
        if package["aggregate"]["artifact_count"] != len(owned):
            _error(f"collection.package[{cpv}].aggregate.artifact_count", f"must be {len(owned)}")
        expected_bolt = _bolt_counts(owned)
        _assert_lane(package["aggregate"]["bolt"], expected_bolt, f"collection.package[{cpv}].aggregate.bolt")
        proof = package["source_rebuild"]["proof"]
        if proof is not None:
            required_modes: set[str] = set()
            if any(component["pgo"]["status"] == "optimized" for component in package["components"]):
                required_modes.add("pgo-use")
            if any(artifact["bolt"]["status"] == "optimized" for artifact in owned):
                required_modes.add("bolt-deploy")
            if set(proof["active_modes"]) != required_modes:
                _error(
                    f"collection.package[{cpv}].source_rebuild.proof.active_modes",
                    f"must exactly equal final optimized lanes {sorted(required_modes)}",
                )
    expected_cpvs = set(package_by_cpv)
    if inventory is not None:
        inv = _inventory(inventory)
        if inventory_sha256 is None or not SHA256_RE.fullmatch(inventory_sha256):
            _error("collection.inventory_sha256", "is required with a frozen inventory")
        if (inv["generation_id"], inv["inventory_id"], inventory_sha256) != generation_tuple:
            _error("collection.inventory", "identity/hash does not equal record generation")
        inventory_cpvs = {entry["cpv"] for entry in inv["packages"]}
        if inventory_cpvs != expected_cpvs:
            _error("collection.inventory.cpvs", f"exact CPV mismatch missing={sorted(inventory_cpvs-expected_cpvs)} extra={sorted(expected_cpvs-inventory_cpvs)}")
        for entry in inv["packages"]:
            if package_by_cpv[entry["cpv"]]["frozen_inventory_entry"]["entry_sha256"] != entry["entry_sha256"]:
                _error("collection.inventory.packages", f"entry hash mismatch for {entry['cpv']}")
        inv_paths = {(entry["owner_cpv"], entry["path"]) for entry in inv["owned_paths"]}
        record_paths = {(owner[0], path) for path, owner in topology_owners.items()}
        if inv_paths != record_paths:
            _error("collection.inventory.owned_paths", f"exact owned-path mismatch missing={sorted(inv_paths-record_paths)} extra={sorted(record_paths-inv_paths)}")
        for entry in inv["owned_paths"]:
            if entry["owner_cpv"] not in inventory_cpvs:
                _error("collection.inventory.owned_paths", f"path owner is absent: {entry['owner_cpv']}")
        for entry in inv["owned_directories"]:
            if entry["owner_cpv"] not in inventory_cpvs:
                _error("collection.inventory.owned_directories", f"directory owner is absent: {entry['owner_cpv']}")
    if vdb_root is not None:
        if not vdb_root.is_absolute():
            _error("collection.vdb_root", "must be absolute")
        live_cpvs: set[str] = set()
        live_paths: dict[str, tuple[str, str, str | None]] = {}
        live_directories: set[tuple[str, str]] = set()
        if vdb_root.exists():
            for category in sorted(vdb_root.iterdir()):
                category_metadata = category.lstat()
                if not stat.S_ISDIR(category_metadata.st_mode) or stat.S_ISLNK(category_metadata.st_mode):
                    _error("collection.vdb", f"malformed non-directory category entry: {category}")
                for instance in sorted(category.iterdir()):
                    instance_metadata = instance.lstat()
                    if not stat.S_ISDIR(instance_metadata.st_mode) or stat.S_ISLNK(instance_metadata.st_mode):
                        _error("collection.vdb", f"malformed non-directory package entry: {instance}")
                    if not (instance / "CONTENTS").is_file():
                        _error("collection.vdb", f"malformed package directory lacks CONTENTS: {instance}")
                    cpv = f"{category.name}/{instance.name}"
                    live_cpvs.add(cpv)
                    live_package = package_by_cpv.get(cpv)
                    if live_package is None:
                        continue
                    live = live_package["live_instance"]
                    if Path(live["vdb_path"]) != instance:
                        _error(f"collection.vdb[{cpv}]", "vdb_path does not equal live instance")
                    if _file_sha(instance / "CONTENTS") != live["contents_sha256"]:
                        _error(f"collection.vdb[{cpv}].CONTENTS", "hash mismatch")
                    if vdb_metadata_tree_sha256(instance) != live["metadata_tree_sha256"]:
                        _error(f"collection.vdb[{cpv}]", "metadata tree hash mismatch")
                    scalars = {"repository": "repository", "build_time": "BUILD_TIME", "counter": "COUNTER"}
                    for field, filename in scalars.items():
                        if _read_vdb_scalar(instance / filename) != live[field]:
                            _error(f"collection.vdb[{cpv}].{filename}", "value mismatch")
                    slot_raw = _read_vdb_scalar(instance / "SLOT")
                    if slot_raw != live["slot_raw"]:
                        _error(f"collection.vdb[{cpv}].SLOT", "raw SLOT mismatch")
                    if parse_slot(slot_raw) != (live["slot"], live["subslot"]):
                        _error(f"collection.vdb[{cpv}].SLOT", "slot/subslot mismatch")
                    env_path = instance / "environment.bz2"
                    env_sha = _file_sha(env_path) if env_path.exists() else None
                    if env_sha != live["environment_bz2_sha256"]:
                        _error(f"collection.vdb[{cpv}].environment.bz2", "hash mismatch")
                    for installed, (entry_type, link_target) in parse_vdb_contents(instance / "CONTENTS").items():
                        if entry_type == "dir":
                            live_directories.add((cpv, installed))
                        else:
                            if installed in live_paths:
                                _error("collection.vdb", f"duplicate ownership for {installed}")
                            live_paths[installed] = (cpv, entry_type, link_target)
        if live_cpvs != expected_cpvs:
            _error("collection.vdb.cpvs", f"exact CPV mismatch missing={sorted(live_cpvs-expected_cpvs)} extra={sorted(expected_cpvs-live_cpvs)}")
        expected_live_paths = {(owner[0], path) for path, owner in topology_owners.items()}
        actual_live_paths = {(owner[0], path) for path, owner in live_paths.items()}
        if expected_live_paths != actual_live_paths:
            _error("collection.vdb.contents", f"exact owned-path mismatch missing={sorted(actual_live_paths-expected_live_paths)} extra={sorted(expected_live_paths-actual_live_paths)}")
        if inventory is not None:
            inventory_directories = {(entry["owner_cpv"], entry["path"]) for entry in inv["owned_directories"]}
            actual_directories = live_directories
            if inventory_directories != actual_directories:
                _error("collection.vdb.directories", f"exact directory mismatch missing={sorted(actual_directories-inventory_directories)} extra={sorted(inventory_directories-actual_directories)}")
        vdb_type_to_file_type = {"obj": "regular", "fif": "fifo"}
        for installed, (cpv, entry_type, link_target) in live_paths.items():
            artifact_id = topology_owners[installed][1]
            artifact = next(item for item in validated_artifacts if item["artifact_id"] == artifact_id)
            symlink_map = {link["path"]: link["target"] for link in artifact["topology"]["symlinks"]}
            symlink_set = set(symlink_map)
            if (entry_type == "sym") != (installed in symlink_set):
                _error("collection.vdb.contents", f"topology type mismatch for {installed}")
            if entry_type == "sym" and symlink_map[installed] != link_target:
                _error("collection.vdb.contents", f"symlink target mismatch for {installed}")
            if entry_type in vdb_type_to_file_type and artifact["metadata"]["file_type"] != vdb_type_to_file_type[entry_type]:
                _error("collection.vdb.contents", f"file type mismatch for {installed}")
            if entry_type == "dev" and artifact["metadata"]["file_type"] not in {"char-device", "block-device"}:
                _error("collection.vdb.contents", f"device type mismatch for {installed}")
    if installed_root is not None:
        verify_installed_artifacts(validated_artifacts, installed_root)

    authoritative_result = {"authoritative": False, "strict_verified": False, "boot_id": None}
    if strict:
        if inventory is None or final_system_state is None or vdb_root is None or installed_root is None:
            _error("collection.strict", "strict verification requires inventory, final-system-state, VDB, and installed roots")
        if packages_dir is None or artifacts_dir is None or inventory_path is None:
            _error("collection.strict", "strict verification requires exact record and inventory paths")
        authoritative_result = verify_authoritative_state(
            validated_packages, validated_artifacts, inventory, final_system_state,
            packages_dir=packages_dir, artifacts_dir=artifacts_dir,
            inventory_path=inventory_path, vdb_root=vdb_root,
            installed_root=installed_root, fixture_mode=fixture_mode,
        )

    pgo_counts = _pgo_counts([component for package in validated_packages for component in package["components"]])
    bolt_counts = _bolt_counts(validated_artifacts)
    source_succeeded = sum(package["source_rebuild"]["status"] == "succeeded" for package in validated_packages)
    source_pending = sum(package["source_rebuild"]["status"] in {"pending", "running"} for package in validated_packages)
    source_unknown = sum(package["source_rebuild"]["status"] == "unknown" for package in validated_packages)
    source_failed = sum(package["source_rebuild"]["status"] == "failed" for package in validated_packages)
    pending_total = source_pending + pgo_counts["pending_count"] + bolt_counts["pending_count"]
    unknown_total = source_unknown + pgo_counts["unknown_count"] + bolt_counts["unknown_count"]
    failed_total = source_failed + pgo_counts["failed_count"] + bolt_counts["failed_count"]
    counts = {
        "installed_total": len(package_by_cpv), "package_record_total": len(validated_packages),
        "component_total": len(component_index), "artifact_record_total": len(validated_artifacts),
        "owned_path_total": len(topology_owners),
        "owned_directory_total": len(inventory["owned_directories"]) if inventory is not None else 0,
        "owner_component_resolved_total": len(validated_artifacts),
        "source_rebuild_required_total": len(validated_packages), "source_rebuild_succeeded_total": source_succeeded,
        **{f"pgo_{key}": value for key, value in pgo_counts.items()},
        **{f"bolt_{key}": value for key, value in bolt_counts.items()},
        "pending_total": pending_total, "unknown_total": unknown_total, "failed_total": failed_total,
    }
    inventory_verified = inventory is not None
    vdb_verified = vdb_root is not None
    installed_artifacts_verified = installed_root is not None
    authoritative_verified = bool(authoritative_result["authoritative"] and authoritative_result["strict_verified"])
    return {
        "schema_version": 1, "record_type": "state-reconciliation",
        "generation_id": generation_tuple[0], "inventory_id": generation_tuple[1],
        "inventory_sha256": generation_tuple[2], "inventory_verified": inventory_verified,
        "vdb_verified": vdb_verified, "installed_artifacts_verified": installed_artifacts_verified,
        "strict_verified": authoritative_result["strict_verified"],
        "authoritative_verified": authoritative_verified,
        "fixture_mode": fixture_mode,
        "boot_id": authoritative_result["boot_id"],
        "counts": counts,
        "coverage_complete": bool(validated_packages) and authoritative_verified and inventory_verified and vdb_verified and installed_artifacts_verified and source_succeeded == len(validated_packages) and pending_total == unknown_total == failed_total == 0,
    }

#!/usr/bin/env python3
"""Strict v3 optimization state contracts and collection reconciliation.

JSON Schema documents the wire format.  This module is the semantic authority:
it rejects contradictory state machines, incomplete evidence, mismatched ABI and
toolchain identities, ambiguous filesystem topology, and collection coverage
claims which are not derivable from the package and artifact records.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shlex
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, NoReturn, Sequence


SCHEMA_VERSION = 3
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
BUILD_ID_RE = re.compile(r"^[0-9a-f]+$")
CP_RE = re.compile(r"^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+$")
VERSION_RE = re.compile(r"^[0-9][A-Za-z0-9+_.]*(?:-r[0-9]+)?$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9+_.:@-]+$")
TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

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
    "data",
}
ELF_ROLES = {
    "executable", "pie-executable", "shared-library", "plugin", "relocatable",
    "kernel-image", "kernel-module", "ebpf", "other",
}
ELF_REQUIRED_KINDS = {"elf", "relocatable-object", "kernel-image", "kernel-module", "ebpf"}
ELF_FORBIDDEN_KINDS = {"static-archive", "gpu-object", "firmware", "bytecode", "script", "data"}

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


def vdb_identity_sha256(live_instance: Mapping[str, Any]) -> str:
    """Hash the exact live VDB identity fields, excluding the self hash."""
    identity = {key: live_instance[key] for key in (
        "vdb_path", "contents_sha256", "repository", "slot", "subslot",
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


def _toolchain(value: Any, path: str, languages: set[str], backend: str) -> dict[str, Any] | None:
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
    family_requirements = {
        "gcc-gcov": ("cc", "gcc"), "rust-llvm-ir": ("rustc", "rust"),
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
        proof = _object(build_use, f"{path}.build_use", {"build_log", "flags", "diagnostics", "installed_evidence"})
        _evidence(proof["build_log"], f"{path}.build_use.build_log")
        _sorted_strings(proof["flags"], f"{path}.build_use.flags", allow_empty=False)
        _evidence_list(proof["diagnostics"], f"{path}.build_use.diagnostics", required=True)
        _evidence_list(proof["installed_evidence"], f"{path}.build_use.installed_evidence", required=True)
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
    toolchain = _toolchain(item["toolchain"], f"{path}.toolchain", set(language_list), backend)
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
        if result in {"failed", "interrupted"} and not failures:
            _error(f"{apath}.failure_evidence", "is required for failed/interrupted attempt")
        if result == "succeeded" and failures:
            _error(f"{apath}.failure_evidence", "must be empty for success")
        successful += result == "succeeded"
    if attempt_ids != sorted(set(attempt_ids)):
        _error(f"{path}.attempts", "must be sorted by unique attempt_id")
    proof = item["proof"]
    if proof is not None:
        pobj = _object(proof, f"{path}.proof", {"transaction_log", "install_log", "binpkg", "equery_check", "smoke_tests", "reverse_dependencies", "installed_vdb_identity_sha256"})
        _evidence(pobj["transaction_log"], f"{path}.proof.transaction_log")
        _evidence(pobj["install_log"], f"{path}.proof.install_log")
        binpkg = _object(pobj["binpkg"], f"{path}.proof.binpkg", {"path", "sha256", "format"})
        _string(binpkg["path"], f"{path}.proof.binpkg.path", absolute=True)
        _sha(binpkg["sha256"], f"{path}.proof.binpkg.sha256")
        _enum(binpkg["format"], f"{path}.proof.binpkg.format", {"gpkg", "xpak"})
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
    _resolution(item["resolution"], f"{path}.resolution", status_value in {"unknown", "failed"})
    if status_value == "succeeded":
        if not source_only or not transaction_id or proof is None or successful != 1:
            _error(path, "succeeded rebuild requires source-only transaction, one successful attempt, and complete proof")
    elif proof is not None:
        _error(f"{path}.proof", "is valid only for succeeded status")
    if status_value in {"running", "succeeded"} and not transaction_id:
        _error(f"{path}.transaction_id", f"is required for {status_value}")
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
    frozen = _object(package["frozen_inventory_entry"], "$.frozen_inventory_entry", {"entry_sha256", "installed_at_freeze"})
    _sha(frozen["entry_sha256"], "$.frozen_inventory_entry.entry_sha256")
    if _bool(frozen["installed_at_freeze"], "$.frozen_inventory_entry.installed_at_freeze") is not True:
        _error("$.frozen_inventory_entry.installed_at_freeze", "must be true")
    live = _object(package["live_instance"], "$.live_instance", {"vdb_path", "contents_sha256", "repository", "slot", "subslot", "build_time", "counter", "environment_bz2_sha256", "identity_sha256"})
    _string(live["vdb_path"], "$.live_instance.vdb_path", absolute=True)
    for key in ("contents_sha256", "identity_sha256"):
        _sha(live[key], f"$.live_instance.{key}")
    _sha(live["environment_bz2_sha256"], "$.live_instance.environment_bz2_sha256", nullable=True)
    for key in ("repository", "slot", "subslot", "build_time", "counter"):
        _string(live[key], f"$.live_instance.{key}")
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
    _graphs(package["graphs"], "$.graphs")
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
    keys = {"class", "type", "machine", "build_id", "text_sha256", "has_symbols", "has_relocations", "has_executable_sections", "soname", "rpath", "runpath", "exports", "symbol_versions", "debug", "runtime_instrumentation", "dynamic_linkage"}
    item = _object(value, path, keys)
    elf_class = item["class"]
    if elf_class not in {32, 64}:
        _error(f"{path}.class", "must be 32 or 64")
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
    keys = {"eligibility", "status", "generation_id", "resolution", "capture", "perf_profiles", "fdata", "tools", "options", "output", "deployment"}
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
        fobj = _object(fdata, f"{path}.fdata", {"path", "sha256", "merge_log", "sample_count", "stale_percent"})
        _string(fobj["path"], f"{path}.fdata.path", absolute=True)
        _sha(fobj["sha256"], f"{path}.fdata.sha256")
        _evidence(fobj["merge_log"], f"{path}.fdata.merge_log")
        _int(fobj["sample_count"], f"{path}.fdata.sample_count", 1)
        _number(fobj["stale_percent"], f"{path}.fdata.stale_percent", 0, 100)
    tools = item["tools"]
    if tools is not None:
        tobj = _object(tools, f"{path}.tools", {"llvm_bolt", "perf2bolt", "merge_fdata"})
        for key, role_name in (("llvm_bolt", "llvm-bolt"), ("perf2bolt", "perf2bolt"), ("merge_fdata", "merge-fdata")):
            _tool(tobj[key], f"{path}.tools.{key}", role_name)
    options = _sorted_strings(item["options"], f"{path}.options")
    output = item["output"]
    if output is not None:
        out = _object(output, f"{path}.output", {"path", "sha256", "text_sha256", "build_id", "bolt_note", "verification"})
        _string(out["path"], f"{path}.output.path", absolute=True)
        _sha(out["sha256"], f"{path}.output.sha256")
        _sha(out["text_sha256"], f"{path}.output.text_sha256")
        out_build = _string(out["build_id"], f"{path}.output.build_id")
        if not BUILD_ID_RE.fullmatch(out_build):
            _error(f"{path}.output.build_id", "must be lowercase hexadecimal")
        if _bool(out["bolt_note"], f"{path}.output.bolt_note") is not True:
            _error(f"{path}.output.bolt_note", "must be true")
        _evidence_list(out["verification"], f"{path}.output.verification", required=True)
    deployment = item["deployment"]
    if deployment is not None:
        dep = _object(deployment, f"{path}.deployment", {"transaction_id", "prestrip_path", "deploy_log", "rollback_artifact", "installed_sha256", "metadata_verified", "runtime_verified"})
        _string(dep["transaction_id"], f"{path}.deployment.transaction_id")
        _string(dep["prestrip_path"], f"{path}.deployment.prestrip_path", absolute=True)
        _evidence(dep["deploy_log"], f"{path}.deployment.deploy_log")
        _evidence(dep["rollback_artifact"], f"{path}.deployment.rollback_artifact")
        _sha(dep["installed_sha256"], f"{path}.deployment.installed_sha256")
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
    proof_values = (capture, profiles, fdata, tools, options, output, deployment)
    if status_value in {"not-applicable", "terminal-exclusion", "unknown"} and (gen or any(proof_values)):
        _error(path, "noneligible BOLT cannot claim capture/profile/output proof")
    if status_value in {"captured", "profiled", "optimized"} and gen != generation_id:
        _error(f"{path}.generation_id", "must equal artifact generation")
    if status_value in {"captured", "profiled", "optimized"} and capture is None:
        _error(f"{path}.capture", f"is required for {status_value}")
    if status_value in {"profiled", "optimized"} and (not profiles or fdata is None or tools is None):
        _error(path, f"{status_value} requires perf profiles, fdata, and exact tools")
    if status_value == "optimized" and (not options or output is None or deployment is None):
        _error(path, "optimized BOLT requires exact options, output, and deployment proof")
    if status_value != "optimized" and (output is not None or deployment is not None):
        _error(path, "output/deployment is valid only for optimized BOLT")
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
    role = _enum(artifact["role"], "$.role", ELF_ROLES | {"archive", "firmware", "bytecode", "script", "data", "gpu-object"})
    installed = _string(artifact["installed_path"], "$.installed_path", absolute=True)
    canonical = _string(artifact["canonical_path"], "$.canonical_path", absolute=True)
    _sha(artifact["content_sha256"], "$.content_sha256")
    _int(artifact["size"], "$.size")
    abi = _enum(artifact["abi"], "$.abi", ABIS)
    target = _target(artifact["target"], "$.target", abi)
    metadata = _object(artifact["metadata"], "$.metadata", {"mode", "uid", "gid", "mtime_ns", "xattrs", "file_capabilities", "selinux_context"})
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
    hardlinks = _sorted_strings(topology["hardlink_paths"], "$.topology.hardlink_paths", absolute=True, allow_empty=False)
    if link_count != len(hardlinks):
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
    if canonical not in hardlinks:
        _error("$.canonical_path", "must be a recorded hardlink")
    if installed not in hardlinks and installed not in symlink_paths:
        _error("$.installed_path", "must be present in topology")
    elf_value = artifact["elf"]
    elf = None if elf_value is None else _elf(elf_value, "$.elf", abi, role)
    if kind in ELF_REQUIRED_KINDS and elf is None:
        _error("$.elf", f"is required for {kind}")
    if kind in ELF_FORBIDDEN_KINDS and elf is not None:
        _error("$.elf", f"must be null for {kind}")
    role_kind = {"relocatable-object": "relocatable", "kernel-image": "kernel-image", "kernel-module": "kernel-module", "ebpf": "ebpf"}
    if kind in role_kind and role != role_kind[kind]:
        _error("$.role", f"must be {role_kind[kind]} for {kind}")
    if elf is not None and target is not None and (elf["class"] != target["elf_class"] or elf["machine"] != target["machine"]):
        _error("$.elf", "class/machine must equal target mapping")
    kernel_required = kind in {"kernel-image", "kernel-module"}
    kernel = _kernel_artifact(artifact["kernel"], "$.kernel", kernel_required, role)
    if kernel is not None and target is not None and kernel["release"] != target["kernel_release"]:
        _error("$.kernel.release", "must equal target kernel_release")
    _graphs(artifact["graphs"], "$.graphs")
    bolt = _bolt(artifact["bolt"], "$.bolt", kind, role, abi, elf, generation["generation_id"])
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
    item = _object(raw, path, {"schema_version", "record_type", "generation_id", "inventory_id", "cpvs", "owned_paths"})
    if item["schema_version"] != 1 or item["record_type"] != "frozen-inventory":
        _error(path, "requires schema_version=1 and record_type=frozen-inventory")
    for key in ("generation_id", "inventory_id"):
        _string(item[key], f"{path}.{key}")
    _sorted_strings(item["cpvs"], f"{path}.cpvs")
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
    return item


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_vdb_contents(path: Path) -> dict[str, str]:
    """Return non-directory installed paths mapped to obj/sym/fif/dev type."""
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as error:
        raise StateValidationError(f"{path}: {error}") from error
    for line_number, line in enumerate(lines, 1):
        try:
            fields = shlex.split(line)
        except ValueError as error:
            raise StateValidationError(f"{path}:{line_number}: {error}") from error
        if not fields or fields[0] == "dir":
            continue
        if fields[0] not in {"obj", "sym", "fif", "dev"} or len(fields) < 2:
            raise StateValidationError(f"{path}:{line_number}: unsupported CONTENTS row")
        installed = fields[1]
        if not installed.startswith("/") or posixpath.normpath(installed) != installed:
            raise StateValidationError(f"{path}:{line_number}: noncanonical installed path")
        if installed in result:
            raise StateValidationError(f"{path}:{line_number}: duplicate installed path {installed}")
        result[installed] = fields[0]
    return result


def _read_vdb_scalar(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise StateValidationError(f"{path}: {error}") from error


def reconcile_collection(
    packages: Sequence[dict[str, Any]],
    artifacts: Sequence[dict[str, Any]],
    *,
    inventory: dict[str, Any] | None = None,
    inventory_sha256: str | None = None,
    vdb_root: Path | None = None,
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
    topology_owners: dict[str, tuple[str, str]] = {}
    artifacts_by_owner: dict[str, list[dict[str, Any]]] = {cpv: [] for cpv in package_by_cpv}
    for artifact in validated_artifacts:
        if artifact["artifact_id"] in artifact_ids:
            _error("collection.artifacts", f"duplicate artifact_id {artifact['artifact_id']}")
        artifact_ids.add(artifact["artifact_id"])
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
    expected_cpvs = set(package_by_cpv)
    if inventory is not None:
        inv = _inventory(inventory)
        if inventory_sha256 is None or not SHA256_RE.fullmatch(inventory_sha256):
            _error("collection.inventory_sha256", "is required with a frozen inventory")
        if (inv["generation_id"], inv["inventory_id"], inventory_sha256) != generation_tuple:
            _error("collection.inventory", "identity/hash does not equal record generation")
        inventory_cpvs = set(inv["cpvs"])
        if inventory_cpvs != expected_cpvs:
            _error("collection.inventory.cpvs", f"exact CPV mismatch missing={sorted(inventory_cpvs-expected_cpvs)} extra={sorted(expected_cpvs-inventory_cpvs)}")
        inv_paths = {(entry["owner_cpv"], entry["path"]) for entry in inv["owned_paths"]}
        record_paths = {(owner[0], path) for path, owner in topology_owners.items()}
        if inv_paths != record_paths:
            _error("collection.inventory.owned_paths", f"exact owned-path mismatch missing={sorted(inv_paths-record_paths)} extra={sorted(record_paths-inv_paths)}")
        for entry in inv["owned_paths"]:
            if entry["owner_cpv"] not in inventory_cpvs:
                _error("collection.inventory.owned_paths", f"path owner is absent: {entry['owner_cpv']}")
    if vdb_root is not None:
        if not vdb_root.is_absolute():
            _error("collection.vdb_root", "must be absolute")
        live_cpvs: set[str] = set()
        live_paths: dict[str, tuple[str, str]] = {}
        if vdb_root.exists():
            for category in sorted(vdb_root.iterdir()):
                if not category.is_dir():
                    continue
                for instance in sorted(category.iterdir()):
                    if not instance.is_dir() or not (instance / "CONTENTS").is_file():
                        continue
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
                    scalars = {"repository": "repository", "slot": "SLOT", "build_time": "BUILD_TIME", "counter": "COUNTER"}
                    for field, filename in scalars.items():
                        if _read_vdb_scalar(instance / filename) != live[field]:
                            _error(f"collection.vdb[{cpv}].{filename}", "value mismatch")
                    slot = live["slot"] + (f"/{live['subslot']}" if live["subslot"] != live["slot"] else "")
                    if _read_vdb_scalar(instance / "SLOT") != slot:
                        _error(f"collection.vdb[{cpv}].SLOT", "slot/subslot mismatch")
                    env_path = instance / "environment.bz2"
                    env_sha = _file_sha(env_path) if env_path.exists() else None
                    if env_sha != live["environment_bz2_sha256"]:
                        _error(f"collection.vdb[{cpv}].environment.bz2", "hash mismatch")
                    for installed, entry_type in parse_vdb_contents(instance / "CONTENTS").items():
                        if installed in live_paths:
                            _error("collection.vdb", f"duplicate ownership for {installed}")
                        live_paths[installed] = (cpv, entry_type)
        if live_cpvs != expected_cpvs:
            _error("collection.vdb.cpvs", f"exact CPV mismatch missing={sorted(live_cpvs-expected_cpvs)} extra={sorted(expected_cpvs-live_cpvs)}")
        expected_live_paths = {(owner[0], path) for path, owner in topology_owners.items()}
        actual_live_paths = {(owner[0], path) for path, owner in live_paths.items()}
        if expected_live_paths != actual_live_paths:
            _error("collection.vdb.contents", f"exact owned-path mismatch missing={sorted(actual_live_paths-expected_live_paths)} extra={sorted(expected_live_paths-actual_live_paths)}")
        for installed, (cpv, entry_type) in live_paths.items():
            artifact_id = topology_owners[installed][1]
            artifact = next(item for item in validated_artifacts if item["artifact_id"] == artifact_id)
            symlink_set = {link["path"] for link in artifact["topology"]["symlinks"]}
            if (entry_type == "sym") != (installed in symlink_set):
                _error("collection.vdb.contents", f"topology type mismatch for {installed}")

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
        "owned_path_total": len(topology_owners), "owner_component_resolved_total": len(validated_artifacts),
        "source_rebuild_required_total": len(validated_packages), "source_rebuild_succeeded_total": source_succeeded,
        **{f"pgo_{key}": value for key, value in pgo_counts.items()},
        **{f"bolt_{key}": value for key, value in bolt_counts.items()},
        "pending_total": pending_total, "unknown_total": unknown_total, "failed_total": failed_total,
    }
    return {
        "schema_version": 1, "record_type": "state-reconciliation",
        "generation_id": generation_tuple[0], "inventory_id": generation_tuple[1],
        "inventory_sha256": generation_tuple[2], "counts": counts,
        "coverage_complete": bool(validated_packages) and source_succeeded == len(validated_packages) and pending_total == unknown_total == failed_total == 0,
    }

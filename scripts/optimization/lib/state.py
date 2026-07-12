#!/usr/bin/env python3
"""Validate and atomically publish exact optimization state records.

The checked-in JSON schemas document the version-2 wire format.  This module
uses only the Python standard library and additionally enforces cross-field
state machines, coverage accounting, exact CP/CPV relationships, and
kind-specific artifact constraints that JSON Schema cannot express cleanly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn


PACKAGE_KEYS = {
    "schema_version",
    "record_type",
    "cpv",
    "cp",
    "repository",
    "slot",
    "subslot",
    "abis",
    "ebuild_sha256",
    "use_flags",
    "build_identities",
    "aggregate",
    "final_status",
    "terminal_reason",
    "notes",
}
BUILD_IDENTITY_KEYS = {
    "component_id",
    "component_kind",
    "abi",
    "target_triple",
    "build_backend",
    "compiler",
    "fingerprint",
    "pgo",
}
PGO_KEYS = {
    "eligibility",
    "mode",
    "generation_id",
    "profile_path",
    "profile_sha256",
    "profile_valid",
    "build_verified",
    "terminal_reason",
    "status",
}
COMPILER_KEYS = {"family", "path", "realpath", "sha256", "version", "profile_format"}
TERMINAL_KEYS = {"reason_code", "evidence", "reviewed"}
AGGREGATE_KEYS = {"component_count", "artifact_count", "pgo", "bolt"}
PGO_AGGREGATE_KEYS = {
    "eligible_count",
    "optimized_count",
    "excluded_count",
    "not_applicable_count",
    "pending_count",
    "failed_count",
    "status",
}
BOLT_AGGREGATE_KEYS = {
    "candidate_count",
    "optimized_count",
    "excluded_count",
    "not_applicable_count",
    "pending_count",
    "failed_count",
    "status",
}
ARTIFACT_KEYS = {
    "schema_version",
    "record_type",
    "owner_cpv",
    "owner_cp",
    "owner_component_id",
    "owner_component_fingerprint",
    "kind",
    "format",
    "installed_path",
    "canonical_path",
    "content_sha256",
    "size",
    "abi",
    "elf",
    "setuid",
    "setgid",
    "file_capabilities",
    "hardlink_paths",
    "symlink_paths",
    "bolt",
    "final_status",
}
ELF_KEYS = {
    "class",
    "type",
    "machine",
    "build_id",
    "text_sha256",
    "has_symbols",
    "has_text_relocations",
    "has_executable_sections",
    "interpreter",
    "needed",
}
ARTIFACT_BOLT_KEYS = {
    "eligibility",
    "terminal_reason",
    "profile_samples",
    "profile_stale_percent",
    "output_path",
    "installed_has_bolt_note",
    "status",
}

BACKENDS = {
    "clang-ir",
    "gcc-gcov",
    "rust-llvm-ir",
    "go-pprof",
    "ebuild-native",
    "clang-sample",
    "kernel-autofdo",
    "not-applicable",
}
BACKEND_FAMILIES = {
    "clang-ir": {"clang"},
    "clang-sample": {"clang"},
    "gcc-gcov": {"gcc"},
    "rust-llvm-ir": {"rust"},
    "go-pprof": {"go"},
    "kernel-autofdo": {"clang"},
    "ebuild-native": {"clang", "gcc", "rust", "go", "native"},
}
COMPONENT_KINDS = {"native", "rust", "go", "kernel", "jvm", "script-data", "other"}
PGO_STATUSES = {
    "pending",
    "training",
    "profiled",
    "optimized",
    "not-applicable",
    "terminal-exclusion",
    "failed",
}
ARTIFACT_BOLT_STATUSES = {
    "pending",
    "captured",
    "profiled",
    "optimized",
    "not-applicable",
    "terminal-exclusion",
    "failed",
}
AGGREGATE_STATUSES = {
    "pending",
    "optimized",
    "optimized-with-exclusions",
    "not-applicable",
    "terminal-exclusion",
    "failed",
}
PACKAGE_FINAL_STATUSES = AGGREGATE_STATUSES
ARTIFACT_FINAL_STATUSES = {
    "pending",
    "optimized",
    "not-applicable",
    "terminal-exclusion",
    "failed",
}
ELIGIBILITIES = {"eligible", "not-applicable", "terminal-exclusion"}
ARTIFACT_KINDS = {
    "elf",
    "static-archive",
    "relocatable-object",
    "kernel-module",
    "ebpf",
    "gpu-object",
    "firmware",
    "bytecode",
    "script",
    "data",
}
ELF_REQUIRED_KINDS = {"elf", "relocatable-object", "kernel-module", "ebpf"}
ELF_FORBIDDEN_KINDS = {"static-archive", "firmware", "bytecode", "script", "data"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
BUILD_ID_RE = re.compile(r"^[0-9a-f]+$")
CP_RE = re.compile(r"^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+$")
VERSION_RE = re.compile(r"^[0-9][A-Za-z0-9+_.]*(?:-r[0-9]+)?$")
COMPONENT_ID_RE = re.compile(r"^[A-Za-z0-9+_.@-]+$")


class StateValidationError(ValueError):
    """A record violates its structural or semantic contract."""


def _error(path: str, message: str) -> NoReturn:
    raise StateValidationError(f"{path}: {message}")


def _object(value: Any, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(path, "must be an object")
    actual = set(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        _error(path, f"keys differ (missing={missing}, extra={extra})")
    return value


def _string(value: Any, path: str, *, absolute: bool = False) -> str:
    if not isinstance(value, str) or not value:
        _error(path, "must be a nonempty string")
    if absolute and (
        not value.startswith("/")
        or value == "/"
        or value.startswith("//")
        or posixpath.normpath(value) != value
    ):
        _error(path, "must be a canonical absolute non-root path")
    if "\x00" in value:
        _error(path, "must not contain NUL")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _error(path, "must be a boolean")
    return value


def _integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _error(path, f"must be an integer >= {minimum}")
    return value


def _number(value: Any, path: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error(path, "must be numeric")
    numeric = float(value)
    if not minimum <= numeric <= maximum:
        _error(path, f"must be between {minimum} and {maximum}")
    return numeric


def _enum(value: Any, path: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        _error(path, f"must be one of {sorted(allowed)}")
    return value


def _string_list(value: Any, path: str, *, absolute: bool = False) -> list[str]:
    if not isinstance(value, list):
        _error(path, "must be an array")
    result = [
        _string(item, f"{path}[{index}]", absolute=absolute)
        for index, item in enumerate(value)
    ]
    if result != sorted(set(result)):
        _error(path, "must be sorted and contain no duplicates")
    return result


def _nullable_absolute(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path, absolute=True)


def _nullable_sha256(value: Any, path: str) -> str | None:
    if value is None:
        return None
    digest = _string(value, path)
    if not SHA256_RE.fullmatch(digest):
        _error(path, "must be a lowercase SHA-256")
    return digest


def _fingerprint(value: Any, path: str) -> str:
    fingerprint = _string(value, path)
    if not FINGERPRINT_RE.fullmatch(fingerprint):
        _error(path, "must use sha256:<64 lowercase hex> form")
    return fingerprint


def _cp_and_cpv(cp_value: Any, cpv_value: Any, path: str) -> tuple[str, str]:
    cp = _string(cp_value, f"{path}.cp")
    cpv = _string(cpv_value, f"{path}.cpv")
    if not CP_RE.fullmatch(cp):
        _error(f"{path}.cp", "must have exact category/package form")
    prefix = f"{cp}-"
    if not cpv.startswith(prefix) or not VERSION_RE.fullmatch(cpv[len(prefix) :]):
        _error(f"{path}.cpv", "must be an exact versioned CPV tied to cp")
    return cp, cpv


def _terminal_reason(value: Any, path: str, required: bool) -> None:
    if value is None:
        if required:
            _error(path, "is required for a terminal or failed state")
        return
    if not required:
        _error(path, "must be null for this nonterminal state")
    reason = _object(value, path, TERMINAL_KEYS)
    _string(reason["reason_code"], f"{path}.reason_code")
    evidence = _string_list(reason["evidence"], f"{path}.evidence")
    if not evidence:
        _error(f"{path}.evidence", "must contain at least one evidence reference")
    if reason["reviewed"] is not True:
        _error(f"{path}.reviewed", "must be true")


def _validate_compiler(value: Any, path: str, backend: str) -> str | None:
    if backend == "not-applicable":
        if value is not None:
            _error(path, "must be null for a not-applicable backend")
        return None
    compiler = _object(value, path, COMPILER_KEYS)
    family = _enum(
        compiler["family"], f"{path}.family", {"clang", "gcc", "rust", "go", "native"}
    )
    if family not in BACKEND_FAMILIES[backend]:
        _error(f"{path}.family", f"is incompatible with {backend}")
    _string(compiler["path"], f"{path}.path", absolute=True)
    _string(compiler["realpath"], f"{path}.realpath", absolute=True)
    compiler_sha = _string(compiler["sha256"], f"{path}.sha256")
    if not SHA256_RE.fullmatch(compiler_sha):
        _error(f"{path}.sha256", "must be a lowercase SHA-256")
    _string(compiler["version"], f"{path}.version")
    _string(compiler["profile_format"], f"{path}.profile_format")
    return family


def _validate_pgo(value: Any, path: str, backend: str) -> dict[str, Any]:
    pgo = _object(value, path, PGO_KEYS)
    eligibility = _enum(pgo["eligibility"], f"{path}.eligibility", ELIGIBILITIES)
    mode = _enum(pgo["mode"], f"{path}.mode", BACKENDS)
    status = _enum(pgo["status"], f"{path}.status", PGO_STATUSES)
    generation = pgo["generation_id"]
    if generation is not None:
        _string(generation, f"{path}.generation_id")
    profile_path = _nullable_absolute(pgo["profile_path"], f"{path}.profile_path")
    profile_sha = _nullable_sha256(pgo["profile_sha256"], f"{path}.profile_sha256")
    if (profile_path is None) != (profile_sha is None):
        _error(path, "profile_path and profile_sha256 must be set or null together")
    profile_valid = _boolean(pgo["profile_valid"], f"{path}.profile_valid")
    build_verified = _boolean(pgo["build_verified"], f"{path}.build_verified")

    if eligibility == "eligible":
        if status in {"not-applicable", "terminal-exclusion"}:
            _error(f"{path}.status", "eligible PGO cannot be terminal or not-applicable")
        if mode == "not-applicable" or mode != backend:
            _error(f"{path}.mode", "eligible PGO mode must equal the component backend")
        _terminal_reason(
            pgo["terminal_reason"], f"{path}.terminal_reason", status == "failed"
        )
    else:
        if status != eligibility:
            _error(f"{path}.status", f"must be {eligibility} for this eligibility")
        if mode != "not-applicable":
            _error(f"{path}.mode", "noneligible PGO must use not-applicable mode")
        if any((generation, profile_path, profile_sha, profile_valid, build_verified)):
            _error(path, "noneligible PGO cannot carry profile or build-use state")
        _terminal_reason(pgo["terminal_reason"], f"{path}.terminal_reason", True)

    if profile_valid and profile_path is None:
        _error(f"{path}.profile_valid", "requires an exact profile identity")
    if status in {"training", "profiled", "optimized"} and not generation:
        _error(f"{path}.generation_id", f"is required for {status}")
    if status in {"profiled", "optimized"} and not (
        profile_path and profile_sha and profile_valid
    ):
        _error(path, f"{status} requires a valid exact profile")
    if status == "optimized" and not build_verified:
        _error(f"{path}.build_verified", "optimized PGO requires verified profile use")
    if status != "optimized" and build_verified:
        _error(f"{path}.build_verified", "may be true only for optimized PGO")
    return pgo


def _expected_aggregate_status(
    optimized: int, excluded: int, pending: int, failed: int
) -> str:
    if failed:
        return "failed"
    if pending:
        return "pending"
    if optimized and excluded:
        return "optimized-with-exclusions"
    if optimized:
        return "optimized"
    if excluded:
        return "terminal-exclusion"
    return "not-applicable"


def _validate_pgo_aggregate(
    value: Any, path: str, identities: list[dict[str, Any]]
) -> dict[str, int | str]:
    aggregate = _object(value, path, PGO_AGGREGATE_KEYS)
    actual = {key: _integer(aggregate[key], f"{path}.{key}") for key in PGO_AGGREGATE_KEYS - {"status"}}
    pgo_states = [identity["pgo"] for identity in identities]
    expected = {
        "eligible_count": sum(state["eligibility"] == "eligible" for state in pgo_states),
        "optimized_count": sum(state["status"] == "optimized" for state in pgo_states),
        "excluded_count": sum(
            state["eligibility"] == "terminal-exclusion" for state in pgo_states
        ),
        "not_applicable_count": sum(
            state["eligibility"] == "not-applicable" for state in pgo_states
        ),
        "pending_count": sum(
            state["eligibility"] == "eligible"
            and state["status"] not in {"optimized", "failed"}
            for state in pgo_states
        ),
        "failed_count": sum(state["status"] == "failed" for state in pgo_states),
    }
    if actual != expected:
        _error(path, f"counts do not match build identities (expected={expected})")
    if actual["eligible_count"] != (
        actual["optimized_count"] + actual["pending_count"] + actual["failed_count"]
    ):
        _error(path, "eligible PGO accounting is incomplete")
    status = _enum(aggregate["status"], f"{path}.status", AGGREGATE_STATUSES)
    expected_status = _expected_aggregate_status(
        actual["optimized_count"],
        actual["excluded_count"],
        actual["pending_count"],
        actual["failed_count"],
    )
    if status != expected_status:
        _error(f"{path}.status", f"must be {expected_status} for these counts")
    return {**actual, "status": status}


def _validate_bolt_aggregate(
    value: Any, path: str, artifact_count: int
) -> dict[str, int | str]:
    aggregate = _object(value, path, BOLT_AGGREGATE_KEYS)
    counts = {key: _integer(aggregate[key], f"{path}.{key}") for key in BOLT_AGGREGATE_KEYS - {"status"}}
    if counts["candidate_count"] != (
        counts["optimized_count"]
        + counts["excluded_count"]
        + counts["pending_count"]
        + counts["failed_count"]
    ):
        _error(path, "optimized+excluded+pending+failed must cover every candidate")
    if artifact_count != counts["candidate_count"] + counts["not_applicable_count"]:
        _error(path, "candidate and not-applicable counts must cover every artifact")
    status = _enum(aggregate["status"], f"{path}.status", AGGREGATE_STATUSES)
    expected_status = _expected_aggregate_status(
        counts["optimized_count"],
        counts["excluded_count"],
        counts["pending_count"],
        counts["failed_count"],
    )
    if status != expected_status:
        _error(f"{path}.status", f"must be {expected_status} for these counts")
    return {**counts, "status": status}


def validate_package(record: Any) -> dict[str, Any]:
    package = _object(record, "$", PACKAGE_KEYS)
    if package["schema_version"] != 2 or package["record_type"] != "package":
        _error("$", "requires schema_version=2 and record_type=package")
    _cp_and_cpv(package["cp"], package["cpv"], "$")
    for field in ("repository", "slot", "subslot"):
        _string(package[field], f"$.{field}")
    abis = _string_list(package["abis"], "$.abis")
    if not set(abis) <= {"amd64", "x86"}:
        _error("$.abis", "contains an unsupported ABI")
    ebuild_sha = _string(package["ebuild_sha256"], "$.ebuild_sha256")
    if not SHA256_RE.fullmatch(ebuild_sha):
        _error("$.ebuild_sha256", "must be a lowercase SHA-256")
    _string_list(package["use_flags"], "$.use_flags")
    _string_list(package["notes"], "$.notes")

    raw_identities = package["build_identities"]
    if not isinstance(raw_identities, list) or not raw_identities:
        _error("$.build_identities", "must be a nonempty array")
    identities: list[dict[str, Any]] = []
    component_ids: list[str] = []
    fingerprints: list[str] = []
    for index, raw_identity in enumerate(raw_identities):
        path = f"$.build_identities[{index}]"
        identity = _object(raw_identity, path, BUILD_IDENTITY_KEYS)
        component_id = _string(identity["component_id"], f"{path}.component_id")
        if not COMPONENT_ID_RE.fullmatch(component_id):
            _error(f"{path}.component_id", "must be a safe stable component identifier")
        component_ids.append(component_id)
        component_kind = _enum(
            identity["component_kind"], f"{path}.component_kind", COMPONENT_KINDS
        )
        abi = _enum(identity["abi"], f"{path}.abi", {"amd64", "x86", "none"})
        backend = _enum(identity["build_backend"], f"{path}.build_backend", BACKENDS)
        family = _validate_compiler(identity["compiler"], f"{path}.compiler", backend)
        fingerprint = _fingerprint(identity["fingerprint"], f"{path}.fingerprint")
        fingerprints.append(fingerprint)
        target_triple = identity["target_triple"]
        rust_component = component_kind == "rust" or backend == "rust-llvm-ir" or family == "rust"
        if rust_component:
            _string(target_triple, f"{path}.target_triple")
        elif target_triple is not None:
            _error(f"{path}.target_triple", "must be null outside a Rust component")
        if backend == "not-applicable":
            if abi != "none" or component_kind not in {"jvm", "script-data", "other"}:
                _error(path, "not-applicable backend requires a non-machine component")
        elif abi == "none":
            _error(f"{path}.abi", "machine-code backend requires amd64 or x86")
        _validate_pgo(identity["pgo"], f"{path}.pgo", backend)
        identities.append(identity)

    if component_ids != sorted(set(component_ids)):
        _error("$.build_identities", "must be sorted by unique component_id")
    if len(set(fingerprints)) != len(fingerprints):
        _error("$.build_identities", "component fingerprints must be unique")
    identity_abis = sorted({identity["abi"] for identity in identities if identity["abi"] != "none"})
    if abis != identity_abis:
        _error("$.abis", f"must exactly match component ABI coverage {identity_abis}")

    aggregate = _object(package["aggregate"], "$.aggregate", AGGREGATE_KEYS)
    component_count = _integer(aggregate["component_count"], "$.aggregate.component_count", minimum=1)
    if component_count != len(identities):
        _error("$.aggregate.component_count", "must equal build_identities length")
    artifact_count = _integer(aggregate["artifact_count"], "$.aggregate.artifact_count")
    pgo_aggregate = _validate_pgo_aggregate(aggregate["pgo"], "$.aggregate.pgo", identities)
    bolt_aggregate = _validate_bolt_aggregate(
        aggregate["bolt"], "$.aggregate.bolt", artifact_count
    )

    optimized = int(pgo_aggregate["optimized_count"]) + int(bolt_aggregate["optimized_count"])
    excluded = int(pgo_aggregate["excluded_count"]) + int(bolt_aggregate["excluded_count"])
    pending = int(pgo_aggregate["pending_count"]) + int(bolt_aggregate["pending_count"])
    failed = int(pgo_aggregate["failed_count"]) + int(bolt_aggregate["failed_count"])
    expected_final = _expected_aggregate_status(optimized, excluded, pending, failed)
    final_status = _enum(
        package["final_status"], "$.final_status", PACKAGE_FINAL_STATUSES
    )
    if final_status != expected_final:
        _error("$.final_status", f"must be {expected_final} for aggregate coverage")
    _terminal_reason(
        package["terminal_reason"],
        "$.terminal_reason",
        final_status in {"terminal-exclusion", "failed"},
    )
    return package


def _validate_elf(value: Any, path: str, abi: str | None) -> dict[str, Any]:
    elf = _object(value, path, ELF_KEYS)
    elf_class = elf["class"]
    if elf_class not in {32, 64}:
        _error(f"{path}.class", "must be 32 or 64")
    _enum(elf["type"], f"{path}.type", {"EXEC", "DYN", "REL", "CORE", "OTHER"})
    _string(elf["machine"], f"{path}.machine")
    build_id = elf["build_id"]
    if build_id is not None and not BUILD_ID_RE.fullmatch(
        _string(build_id, f"{path}.build_id")
    ):
        _error(f"{path}.build_id", "must be lowercase hexadecimal")
    _nullable_sha256(elf["text_sha256"], f"{path}.text_sha256")
    for field in ("has_symbols", "has_text_relocations", "has_executable_sections"):
        _boolean(elf[field], f"{path}.{field}")
    _nullable_absolute(elf["interpreter"], f"{path}.interpreter")
    _string_list(elf["needed"], f"{path}.needed")
    if elf_class == 64 and abi == "x86":
        _error("$.abi", "x86 ABI cannot describe an ELF64 artifact")
    if elf_class == 32 and abi == "amd64":
        _error("$.abi", "amd64 ABI cannot describe an ELF32 artifact")
    return elf


def _validate_artifact_bolt(
    value: Any, path: str, kind: str, abi: str | None, elf: dict[str, Any] | None
) -> str:
    bolt = _object(value, path, ARTIFACT_BOLT_KEYS)
    eligibility = _enum(bolt["eligibility"], f"{path}.eligibility", ELIGIBILITIES)
    status = _enum(bolt["status"], f"{path}.status", ARTIFACT_BOLT_STATUSES)
    samples = _integer(bolt["profile_samples"], f"{path}.profile_samples")
    stale_value = bolt["profile_stale_percent"]
    stale = (
        None
        if stale_value is None
        else _number(stale_value, f"{path}.profile_stale_percent", minimum=0, maximum=100)
    )
    output = _nullable_absolute(bolt["output_path"], f"{path}.output_path")
    note = _boolean(bolt["installed_has_bolt_note"], f"{path}.installed_has_bolt_note")

    if eligibility == "eligible":
        if status in {"not-applicable", "terminal-exclusion"}:
            _error(f"{path}.status", "eligible BOLT artifact cannot be terminal or not-applicable")
        _terminal_reason(
            bolt["terminal_reason"], f"{path}.terminal_reason", status == "failed"
        )
        if kind != "elf" or abi != "amd64" or elf is None:
            _error(path, "BOLT eligibility requires an amd64 executable/shared ELF")
        if elf["class"] != 64 or elf["type"] not in {"EXEC", "DYN"}:
            _error(path, "BOLT eligibility requires ELF64 EXEC or DYN")
        if not all(
            (
                elf["build_id"],
                elf["text_sha256"],
                elf["has_symbols"],
                elf["has_text_relocations"],
                elf["has_executable_sections"],
            )
        ):
            _error(path, "BOLT eligibility requires exact identities, symbols, and relocations")
    else:
        if status != eligibility:
            _error(f"{path}.status", f"must be {eligibility} for this eligibility")
        _terminal_reason(bolt["terminal_reason"], f"{path}.terminal_reason", True)
        if samples or stale is not None or output is not None or note:
            _error(path, "noneligible BOLT state cannot carry profile or output state")

    if status in {"pending", "captured", "failed"}:
        if samples or stale is not None or output is not None or note:
            _error(path, f"{status} BOLT state cannot claim profile/output deployment")
    elif status == "profiled":
        if samples <= 0 or stale is None or output is not None or note:
            _error(path, "profiled BOLT state requires samples but no output deployment")
    elif status == "optimized":
        if samples <= 0 or stale is None or output is None or not note:
            _error(path, "optimized BOLT state requires samples, output, and installed note")
    return status


def validate_artifact(record: Any) -> dict[str, Any]:
    artifact = _object(record, "$", ARTIFACT_KEYS)
    if artifact["schema_version"] != 2 or artifact["record_type"] != "artifact":
        _error("$", "requires schema_version=2 and record_type=artifact")
    _cp_and_cpv(artifact["owner_cp"], artifact["owner_cpv"], "$.owner")
    component_id = _string(artifact["owner_component_id"], "$.owner_component_id")
    if not COMPONENT_ID_RE.fullmatch(component_id):
        _error("$.owner_component_id", "must be a safe stable component identifier")
    _fingerprint(artifact["owner_component_fingerprint"], "$.owner_component_fingerprint")
    kind = _enum(artifact["kind"], "$.kind", ARTIFACT_KINDS)
    _string(artifact["format"], "$.format")
    installed_path = _string(artifact["installed_path"], "$.installed_path", absolute=True)
    canonical_path = _string(artifact["canonical_path"], "$.canonical_path", absolute=True)
    content_sha = _string(artifact["content_sha256"], "$.content_sha256")
    if not SHA256_RE.fullmatch(content_sha):
        _error("$.content_sha256", "must be a lowercase SHA-256")
    _integer(artifact["size"], "$.size")
    abi_value = artifact["abi"]
    abi = None if abi_value is None else _enum(abi_value, "$.abi", {"amd64", "x86", "other"})
    elf_value = artifact["elf"]
    elf = None if elf_value is None else _validate_elf(elf_value, "$.elf", abi)
    if kind in ELF_REQUIRED_KINDS and elf is None:
        _error("$.elf", f"is required for {kind}")
    if kind in ELF_FORBIDDEN_KINDS and elf is not None:
        _error("$.elf", f"must be null for {kind}")
    if kind == "elf" and elf is not None and elf["type"] == "REL":
        _error("$.kind", "REL input must use a relocatable-object kind")
    if kind in {"relocatable-object", "kernel-module", "ebpf"} and (
        elf is None or elf["type"] != "REL"
    ):
        _error("$.elf.type", f"must be REL for {kind}")
    if elf is None and abi in {"amd64", "x86"} and kind not in {"static-archive", "gpu-object"}:
        _error("$.abi", "machine ABI without ELF is valid only for archives/GPU objects")

    for field in ("setuid", "setgid"):
        _boolean(artifact[field], f"$.{field}")
    _string_list(artifact["file_capabilities"], "$.file_capabilities")
    hardlinks = _string_list(artifact["hardlink_paths"], "$.hardlink_paths", absolute=True)
    symlinks = _string_list(artifact["symlink_paths"], "$.symlink_paths", absolute=True)
    if not hardlinks or canonical_path not in hardlinks:
        _error("$.hardlink_paths", "must include canonical_path")
    if installed_path not in hardlinks and installed_path not in symlinks:
        _error("$.installed_path", "must appear in hardlink_paths or symlink_paths")
    if set(hardlinks) & set(symlinks):
        _error("$", "hardlink_paths and symlink_paths must not overlap")

    bolt_status = _validate_artifact_bolt(artifact["bolt"], "$.bolt", kind, abi, elf)
    expected_final = {
        "pending": "pending",
        "captured": "pending",
        "profiled": "pending",
        "optimized": "optimized",
        "not-applicable": "not-applicable",
        "terminal-exclusion": "terminal-exclusion",
        "failed": "failed",
    }[bolt_status]
    final_status = _enum(
        artifact["final_status"], "$.final_status", ARTIFACT_FINAL_STATUSES
    )
    if final_status != expected_final:
        _error("$.final_status", f"must be {expected_final} for BOLT status {bolt_status}")
    return artifact


VALIDATORS = {"package": validate_package, "artifact": validate_artifact}


def load_and_validate(path: Path, kind: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise StateValidationError(f"{path}: {error}") from error
    return VALIDATORS[kind](value)


def canonical_bytes(record: dict[str, Any]) -> bytes:
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


def atomic_publish(record: dict[str, Any], output: Path) -> str:
    if not output.is_absolute() or output == Path("/"):
        raise StateValidationError("output must be an absolute non-root path")
    _reject_symlink_components(output.parent)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink_components(output.parent)
    if output.exists() and not output.is_file():
        raise StateValidationError(f"output exists and is not a regular file: {output}")
    if output.is_symlink():
        raise StateValidationError(f"output must not be a symlink: {output}")

    payload = canonical_bytes(record)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".partial", dir=output.parent
    )
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
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(payload).hexdigest()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="validate one state record")
    validate_parser.add_argument("--kind", choices=sorted(VALIDATORS), required=True)
    validate_parser.add_argument("record", type=Path)
    write_parser = subparsers.add_parser("write", help="validate and atomically publish a record")
    write_parser.add_argument("--kind", choices=sorted(VALIDATORS), required=True)
    write_parser.add_argument("--input", type=Path, required=True)
    write_parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if arguments.command == "validate":
            record = load_and_validate(arguments.record, arguments.kind)
            digest = hashlib.sha256(canonical_bytes(record)).hexdigest()
        else:
            record = load_and_validate(arguments.input, arguments.kind)
            digest = atomic_publish(record, arguments.output)
    except StateValidationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

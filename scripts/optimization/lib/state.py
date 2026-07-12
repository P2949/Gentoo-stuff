#!/usr/bin/env python3
"""Validate and atomically publish strict optimization state records.

This module deliberately uses only the Python standard library so Portage and
recovery environments do not need a third-party JSON Schema implementation.
The checked-in schemas document the wire format; the validators below enforce
the cross-field invariants that JSON Schema cannot express conveniently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    "build_backend",
    "compiler",
    "fingerprint",
    "pgo",
    "bolt",
    "final_status",
    "notes",
}
PGO_KEYS = {
    "eligibility",
    "mode",
    "generation_id",
    "profile_path",
    "profile_valid",
    "build_verified",
    "terminal_reason",
    "status",
}
BOLT_KEYS = {
    "candidate_count",
    "optimized_count",
    "excluded_count",
    "terminal_reason",
    "status",
}
COMPILER_KEYS = {"family", "path", "realpath", "version", "profile_format"}
TERMINAL_KEYS = {"reason_code", "evidence", "reviewed"}
ARTIFACT_KEYS = {
    "schema_version",
    "record_type",
    "owner_cpv",
    "package_fingerprint",
    "installed_path",
    "canonical_path",
    "elf_class",
    "elf_type",
    "machine",
    "abi",
    "build_id",
    "text_sha256",
    "has_symbols",
    "has_text_relocations",
    "setuid",
    "setgid",
    "file_capabilities",
    "hardlink_paths",
    "symlink_paths",
    "bolt_eligibility",
    "terminal_reason",
    "bolt_profile_samples",
    "bolt_profile_stale_percent",
    "bolt_output_path",
    "installed_has_bolt_note",
    "status",
}

PACKAGE_BACKENDS = {
    "clang-ir",
    "gcc-gcov",
    "rust-llvm-ir",
    "go-pprof",
    "ebuild-native",
    "clang-sample",
    "kernel-autofdo",
    "not-applicable",
}
PGO_MODES = PACKAGE_BACKENDS
PGO_STATUSES = {
    "pending",
    "training",
    "profiled",
    "optimized",
    "not-applicable",
    "terminal-exclusion",
    "failed",
}
BOLT_STATUSES = {
    "pending",
    "captured",
    "profiled",
    "optimized",
    "not-applicable",
    "terminal-exclusion",
    "failed",
}
FINAL_STATUSES = {
    "pending",
    "optimized",
    "not-applicable",
    "terminal-exclusion",
    "failed",
}
ELIGIBILITIES = {"eligible", "not-applicable", "terminal-exclusion"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
BUILD_ID_RE = re.compile(r"^[0-9A-Fa-f]+$")
CP_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


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
    if absolute and not value.startswith("/"):
        _error(path, "must be an absolute path")
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
    result = [_string(item, f"{path}[{index}]", absolute=absolute) for index, item in enumerate(value)]
    if result != sorted(set(result)):
        _error(path, "must be sorted and contain no duplicates")
    return result


def _nullable_absolute(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path, absolute=True)


def _terminal_reason(value: Any, path: str, required: bool) -> None:
    if value is None:
        if required:
            _error(path, "is required for a terminal state")
        return
    if not required:
        _error(path, "must be null for a nonterminal eligibility state")
    reason = _object(value, path, TERMINAL_KEYS)
    _string(reason["reason_code"], f"{path}.reason_code")
    evidence = _string_list(reason["evidence"], f"{path}.evidence")
    if not evidence:
        _error(f"{path}.evidence", "must contain at least one evidence reference")
    if reason["reviewed"] is not True:
        _error(f"{path}.reviewed", "must be true")


def validate_package(record: Any) -> dict[str, Any]:
    package = _object(record, "$", PACKAGE_KEYS)
    if package["schema_version"] != 1 or package["record_type"] != "package":
        _error("$", "requires schema_version=1 and record_type=package")
    for field in ("cpv", "cp"):
        value = _string(package[field], f"$.{field}")
        if not CP_RE.fullmatch(value):
            _error(f"$.{field}", "must have category/name form")
    for field in ("repository", "slot", "subslot"):
        _string(package[field], f"$.{field}")
    abis = _string_list(package["abis"], "$.abis")
    if not abis or not set(abis) <= {"amd64", "x86"}:
        _error("$.abis", "must contain one or both supported ABIs")
    ebuild_sha = _string(package["ebuild_sha256"], "$.ebuild_sha256")
    if not SHA256_RE.fullmatch(ebuild_sha):
        _error("$.ebuild_sha256", "must be a lowercase SHA-256")
    _string_list(package["use_flags"], "$.use_flags")
    backend = _enum(package["build_backend"], "$.build_backend", PACKAGE_BACKENDS)
    fingerprint = _string(package["fingerprint"], "$.fingerprint")
    if not FINGERPRINT_RE.fullmatch(fingerprint):
        _error("$.fingerprint", "must use sha256:<64 lowercase hex> form")
    _string_list(package["notes"], "$.notes")
    final_status = _enum(package["final_status"], "$.final_status", FINAL_STATUSES)

    compiler = package["compiler"]
    if compiler is None:
        if backend != "not-applicable":
            _error("$.compiler", "may be null only for a not-applicable backend")
    else:
        compiler_obj = _object(compiler, "$.compiler", COMPILER_KEYS)
        _enum(compiler_obj["family"], "$.compiler.family", {"clang", "gcc", "rust", "go", "native"})
        _string(compiler_obj["path"], "$.compiler.path", absolute=True)
        _string(compiler_obj["realpath"], "$.compiler.realpath", absolute=True)
        _string(compiler_obj["version"], "$.compiler.version")
        _string(compiler_obj["profile_format"], "$.compiler.profile_format")

    pgo = _object(package["pgo"], "$.pgo", PGO_KEYS)
    pgo_eligibility = _enum(pgo["eligibility"], "$.pgo.eligibility", ELIGIBILITIES)
    pgo_mode = _enum(pgo["mode"], "$.pgo.mode", PGO_MODES)
    pgo_status = _enum(pgo["status"], "$.pgo.status", PGO_STATUSES)
    generation_id = pgo["generation_id"]
    if generation_id is not None:
        _string(generation_id, "$.pgo.generation_id")
    profile_path = _nullable_absolute(pgo["profile_path"], "$.pgo.profile_path")
    profile_valid = _boolean(pgo["profile_valid"], "$.pgo.profile_valid")
    build_verified = _boolean(pgo["build_verified"], "$.pgo.build_verified")
    _terminal_reason(
        pgo["terminal_reason"],
        "$.pgo.terminal_reason",
        pgo_eligibility != "eligible",
    )
    if pgo_eligibility == "eligible" and pgo_mode == "not-applicable":
        _error("$.pgo.mode", "eligible PGO state requires a real backend")
    if pgo_eligibility != "eligible" and pgo_mode != "not-applicable":
        _error("$.pgo.mode", "noneligible PGO state must use not-applicable mode")
    expected_pgo_terminal = pgo_eligibility
    if pgo_eligibility != "eligible" and pgo_status != expected_pgo_terminal:
        _error("$.pgo.status", f"must be {expected_pgo_terminal} for this eligibility")
    if pgo_status == "optimized":
        if not all((generation_id, profile_path, profile_valid, build_verified)):
            _error("$.pgo", "optimized state requires generation, profile, validation, and build proof")

    bolt = _object(package["bolt"], "$.bolt", BOLT_KEYS)
    candidate_count = _integer(bolt["candidate_count"], "$.bolt.candidate_count")
    optimized_count = _integer(bolt["optimized_count"], "$.bolt.optimized_count")
    excluded_count = _integer(bolt["excluded_count"], "$.bolt.excluded_count")
    bolt_status = _enum(bolt["status"], "$.bolt.status", BOLT_STATUSES)
    bolt_terminal = bolt_status in {"not-applicable", "terminal-exclusion"}
    _terminal_reason(bolt["terminal_reason"], "$.bolt.terminal_reason", bolt_terminal)
    if optimized_count > candidate_count:
        _error("$.bolt.optimized_count", "cannot exceed candidate_count")
    if bolt_status == "optimized" and optimized_count != candidate_count:
        _error("$.bolt", "optimized status requires every candidate optimized")
    if bolt_status == "not-applicable" and (candidate_count or optimized_count):
        _error("$.bolt", "not-applicable status cannot contain candidates")
    if bolt_status == "terminal-exclusion" and excluded_count == 0:
        _error("$.bolt.excluded_count", "terminal exclusion requires an excluded artifact")

    if final_status == "optimized":
        terminal_ok = {"optimized", "not-applicable", "terminal-exclusion"}
        if pgo_status not in terminal_ok or bolt_status not in terminal_ok:
            _error("$.final_status", "optimized final state requires terminal PGO and BOLT states")
    if final_status == "not-applicable" and not (
        pgo_status == "not-applicable" and bolt_status == "not-applicable"
    ):
        _error("$.final_status", "not-applicable final state requires both lanes not applicable")
    return package


def validate_artifact(record: Any) -> dict[str, Any]:
    artifact = _object(record, "$", ARTIFACT_KEYS)
    if artifact["schema_version"] != 1 or artifact["record_type"] != "artifact":
        _error("$", "requires schema_version=1 and record_type=artifact")
    owner = _string(artifact["owner_cpv"], "$.owner_cpv")
    if not CP_RE.fullmatch(owner):
        _error("$.owner_cpv", "must have category/name form")
    fingerprint = _string(artifact["package_fingerprint"], "$.package_fingerprint")
    if not FINGERPRINT_RE.fullmatch(fingerprint):
        _error("$.package_fingerprint", "must use sha256:<64 lowercase hex> form")
    installed_path = _string(artifact["installed_path"], "$.installed_path", absolute=True)
    canonical_path = _string(artifact["canonical_path"], "$.canonical_path", absolute=True)
    if artifact["elf_class"] not in (32, 64):
        _error("$.elf_class", "must be 32 or 64")
    _enum(artifact["elf_type"], "$.elf_type", {"EXEC", "DYN", "REL", "CORE", "OTHER"})
    _string(artifact["machine"], "$.machine")
    abi = _enum(artifact["abi"], "$.abi", {"amd64", "x86", "other"})
    if artifact["elf_class"] == 64 and abi == "x86":
        _error("$.abi", "x86 ABI cannot describe an ELF64 artifact")
    if artifact["elf_class"] == 32 and abi == "amd64":
        _error("$.abi", "amd64 ABI cannot describe an ELF32 artifact")
    build_id = artifact["build_id"]
    if build_id is not None and not BUILD_ID_RE.fullmatch(_string(build_id, "$.build_id")):
        _error("$.build_id", "must be hexadecimal")
    text_sha = artifact["text_sha256"]
    if text_sha is not None and not SHA256_RE.fullmatch(_string(text_sha, "$.text_sha256")):
        _error("$.text_sha256", "must be a lowercase SHA-256")
    for field in ("has_symbols", "has_text_relocations", "setuid", "setgid", "installed_has_bolt_note"):
        _boolean(artifact[field], f"$.{field}")
    _string_list(artifact["file_capabilities"], "$.file_capabilities")
    hardlinks = _string_list(artifact["hardlink_paths"], "$.hardlink_paths", absolute=True)
    symlinks = _string_list(artifact["symlink_paths"], "$.symlink_paths", absolute=True)
    if canonical_path not in hardlinks:
        _error("$.hardlink_paths", "must include canonical_path")
    if installed_path not in hardlinks and installed_path not in symlinks:
        _error("$.installed_path", "must appear in hardlink_paths or symlink_paths")
    eligibility = _enum(artifact["bolt_eligibility"], "$.bolt_eligibility", ELIGIBILITIES)
    _terminal_reason(
        artifact["terminal_reason"],
        "$.terminal_reason",
        eligibility != "eligible",
    )
    samples = _integer(artifact["bolt_profile_samples"], "$.bolt_profile_samples")
    _number(
        artifact["bolt_profile_stale_percent"],
        "$.bolt_profile_stale_percent",
        minimum=0,
        maximum=100,
    )
    output_path = _nullable_absolute(artifact["bolt_output_path"], "$.bolt_output_path")
    status = _enum(artifact["status"], "$.status", BOLT_STATUSES)
    if eligibility != "eligible" and status != eligibility:
        _error("$.status", f"must be {eligibility} for this eligibility")
    if status == "optimized":
        if not build_id or not text_sha or samples <= 0 or not output_path:
            _error("$", "optimized artifact requires identities, samples, and output")
        if artifact["installed_has_bolt_note"] is not True:
            _error("$.installed_has_bolt_note", "optimized artifact requires a BOLT note")
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

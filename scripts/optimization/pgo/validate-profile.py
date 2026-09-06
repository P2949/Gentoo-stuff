#!/usr/bin/env python3
"""Validate one exact backend profile and publish its dispatcher manifest.

This is the only generic producer for the eight-line
``gentoo-optimization-profile-v1`` manifest consumed by ``portage/bashrc``.
It observes tool and payload identities directly, proves backend-native
profile structure, and never searches for a profile by package name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

_PGO_MODULE_ROOT = Path(__file__).resolve().parent
if os.fspath(_PGO_MODULE_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_PGO_MODULE_ROOT))

from profile_locks import (  # noqa: E402
    ProfileLockError,
    generation_from_fields,
    profile_lock_hierarchy,
    validate_generation,
)


BUFFER_SIZE = 1024 * 1024
MAX_JSON_SIZE = 4 * 1024 * 1024
MAX_TOOL_OUTPUT = 4 * 1024 * 1024
TOOL_TIMEOUT_SECONDS = 30
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_RE = re.compile(r"^[0-9a-f]{8,128}$")
CATEGORY_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9+_.-]*"
PACKAGE_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9+_-]*"
VERSION_PATTERN = (
    r"[0-9]+(?:\.[0-9]+)*[a-z]?"
    r"(?:_(?:alpha|beta|pre|rc|p)[0-9]*)*"
)
VERSION_REVISION_PATTERN = rf"{VERSION_PATTERN}(?:-r[0-9]+)?"
CPV_RE = re.compile(
    rf"{CATEGORY_PATTERN}/"
    rf"(?!{PACKAGE_PATTERN}-{VERSION_REVISION_PATTERN}-"
    rf"{VERSION_REVISION_PATTERN}\Z)"
    rf"{PACKAGE_PATTERN}-{VERSION_REVISION_PATTERN}\Z"
)
SAFE_GO_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_./+@~-]+$")
SAFE_GO_SYMBOL_RE = re.compile(r"^[^\s\x00=]+$")
SAFE_REPRODUCIBILITY_COMPONENT_RE = re.compile(r"^[A-Za-z0-9+_.@-]+$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

BACKEND_FAMILY = {
    "clang-ir": "clang",
    "clang-sample": "clang",
    "gcc": "gcc",
    "rust": "rust",
    "go": "go",
}
VALIDATION_METADATA_FIELDS = {
    "schema_version",
    "generation",
    "manifest",
    "profile",
    "compiler",
    "profile_tool",
    "backend_proof",
}
MANIFEST_IDENTITY_FIELDS = {"path", "sha256"}
PROFILE_IDENTITY_FIELDS = {
    "backend",
    "path",
    "sha256",
    "fingerprint",
    "abi",
    "compiler_family",
}
COMPILER_IDENTITY_FIELDS = {
    "path",
    "sha256",
    "family",
    "major",
    "rust_llvm_major",
    "version_arguments",
    "version_stdout_sha256",
    "version_stderr_sha256",
    "verbose_version_stdout_sha256",
    "verbose_version_stderr_sha256",
}
PROFILE_TOOL_IDENTITY_FIELDS = {
    "path",
    "sha256",
    "major",
    "version_arguments",
    "version_stdout_sha256",
    "version_stderr_sha256",
    "version_stdout",
    "version_stderr",
}
BACKEND_PROOF_FIELDS = {
    "clang-ir": {
        "kind",
        "function_count",
        "maximum_function_count",
        "validation_arguments",
        "validation_output_sha256",
    },
    "rust": {
        "kind",
        "function_count",
        "maximum_function_count",
        "validation_arguments",
        "validation_output_sha256",
    },
    "clang-sample": {
        "kind",
        "function_count",
        "maximum_sample_total",
        "sample_metadata_path",
        "sample_metadata_sha256",
        "source_kind",
        "input_build_id",
        "input_text_sha256",
        "sample_input_fingerprint",
        "reproducibility",
        "recorded_validation_output_sha256",
        "detailed_validation_arguments",
        "detailed_validation_output_sha256",
    },
    "gcc": {
        "kind",
        "gcda_file_count",
        "hot_file_count",
        "gcda_files",
        "validation_arguments",
        "validation_output_sha256",
    },
    "go": {
        "kind",
        "profiled_binary_path",
        "profiled_binary_sha256",
        "go_build_id",
        "gnu_build_id",
        "readelf_path",
        "readelf_sha256",
        "target_package",
        "target_symbols",
        "raw_validation_arguments",
        "raw_validation_output_sha256",
        "nonzero_sample_count",
        "mapping_identity",
        "target_function_metadata",
    },
}

SAMPLE_METADATA_FIELDS = {
    "schema_version",
    "profile_family",
    "profile_format",
    "profile_path",
    "profile_sha256",
    "profile_size",
    "package",
    "compiler",
    "input_identity",
    "validator",
    "validation",
    "source",
    "reproducibility",
}
SAMPLE_PACKAGE_FIELDS = {"abi", "cpv", "fingerprint"}
SAMPLE_COMPILER_FIELDS = {"family", "major"}
SAMPLE_INPUT_FIELDS = {"build_id", "text_sha256"}
SAMPLE_VALIDATION_FIELDS = {"command_arguments", "output_sha256"}
LLVM_TOOL_IDENTITY_FIELDS = {
    "realpath",
    "sha256",
    "version_stderr",
    "version_stdout",
}
SAMPLE_PROFGEN_SOURCE_FIELDS = {
    "kind",
    "binary_path",
    "binary_sha256",
    "debug_binary_path",
    "debug_binary_sha256",
    "perf_data_path",
    "perf_data_sha256",
    "producer",
    "readelf",
    "objcopy",
    "command_arguments",
    "command_output_sha256",
    "conversion_log_path",
    "conversion_log_sha256",
    "conversion_log_observation",
    "binary_observation",
    "debug_binary_observation",
    "perf_data_observation",
}
CONVERSION_LOG_FIELDS = {
    "schema_version",
    "producer_realpath",
    "producer_sha256",
    "command_arguments",
    "exit_status",
    "stdout",
    "stdout_sha256",
    "stderr",
    "stderr_sha256",
}
FILE_OBSERVATION_FIELDS = {
    "device",
    "inode",
    "mode",
    "uid",
    "gid",
    "link_count",
    "size",
    "mtime_ns",
    "ctime_ns",
    "sha256",
}
REPRODUCIBILITY_FIELDS = {
    "optimization_generation_id",
    "inventory_id",
    "inventory_sha256",
    "workload_revision",
    "source_identity_sha256",
    "production_host",
    "production_date",
}


class ProfileValidationError(Exception):
    """The requested profile could not be proven safe for consumption."""


def fail(message: str) -> NoReturn:
    raise ProfileValidationError(message)


def require_exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing or unknown:
        parts: list[str] = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if unknown:
            parts.append(f"unknown {', '.join(unknown)}")
        fail(f"{label} has invalid fields: {'; '.join(parts)}")


def require_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be one JSON object")
    return value


def require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        fail(f"{label} must be a nonempty string without NUL bytes")
    return value


def require_hex64(value: object, label: str) -> str:
    text = require_string(value, label)
    if not HEX64_RE.fullmatch(text):
        fail(f"{label} must be exactly 64 lowercase hexadecimal characters")
    return text


def require_safe_reproducibility_component(value: object, label: str) -> str:
    text = require_string(value, label)
    if (
        not SAFE_REPRODUCIBILITY_COMPONENT_RE.fullmatch(text)
        or text in {".", ".."}
    ):
        fail(f"{label} is not a safe exact reproducibility component")
    return text


def require_positive_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        fail(f"{label} must be a positive integer")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(BUFFER_SIZE):
                digest.update(chunk)
    except OSError as error:
        fail(f"cannot hash {path}: {error}")
    return digest.hexdigest()


def observe_regular_file(path: Path, label: str) -> dict[str, object]:
    try:
        path_stat = path.stat()
    except OSError as error:
        fail(f"cannot stat {label} {path}: {error}")
    if not stat.S_ISREG(path_stat.st_mode):
        fail(f"{label} is not a regular file: {path}")
    return {
        "ctime_ns": path_stat.st_ctime_ns,
        "device": path_stat.st_dev,
        "gid": path_stat.st_gid,
        "inode": path_stat.st_ino,
        "link_count": path_stat.st_nlink,
        "mode": path_stat.st_mode,
        "mtime_ns": path_stat.st_mtime_ns,
        "sha256": sha256_file(path),
        "size": path_stat.st_size,
        "uid": path_stat.st_uid,
    }


def validate_file_observation(
    value: object, path: Path, label: str
) -> dict[str, object]:
    observation = require_object(value, f"{label} observation")
    require_exact_fields(
        observation, FILE_OBSERVATION_FIELDS, f"{label} observation"
    )
    for field in FILE_OBSERVATION_FIELDS - {"sha256"}:
        item = observation[field]
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            fail(f"{label} observation {field} must be a nonnegative integer")
    require_hex64(observation["sha256"], f"{label} observation sha256")
    observed = observe_regular_file(path, label)
    if observation != observed:
        fail(f"{label} no longer matches its exact recorded observation")
    return observed


def validate_reproducibility(value: object) -> dict[str, object]:
    reproducibility = require_object(value, "sample reproducibility")
    require_exact_fields(
        reproducibility, REPRODUCIBILITY_FIELDS, "sample reproducibility"
    )
    production_date = require_string(
        reproducibility["production_date"], "sample production date"
    )
    if not DATE_RE.fullmatch(production_date):
        fail("sample production date must use exact YYYY-MM-DD format")
    try:
        date.fromisoformat(production_date)
    except ValueError:
        fail("sample production date is not a valid calendar date")
    return {
        "optimization_generation_id": require_safe_reproducibility_component(
            reproducibility["optimization_generation_id"],
            "optimization generation ID",
        ),
        "inventory_id": require_safe_reproducibility_component(
            reproducibility["inventory_id"], "sample inventory ID"
        ),
        "inventory_sha256": require_hex64(
            reproducibility["inventory_sha256"], "sample inventory SHA-256"
        ),
        "production_date": production_date,
        "production_host": require_safe_reproducibility_component(
            reproducibility["production_host"], "production host"
        ),
        "source_identity_sha256": require_hex64(
            reproducibility["source_identity_sha256"], "source identity SHA-256"
        ),
        "workload_revision": require_safe_reproducibility_component(
            reproducibility["workload_revision"], "workload revision"
        ),
    }


def safe_absolute_path(path: Path, label: str, *, must_exist: bool) -> Path:
    text = os.fspath(path)
    if (
        not path.is_absolute()
        or text == "/"
        or re.search(r"[\s=]", text)
        or "//" in text
        or any(part in {".", ".."} for part in path.parts)
    ):
        fail(f"{label} must be a safe non-root absolute path without whitespace or '='")
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as error:
        fail(f"cannot resolve {label} {path}: {error}")
    if resolved != path:
        fail(f"{label} is not canonical or traverses a symlink: {path}")
    return path


def regular_input(path: Path, label: str, *, executable: bool = False) -> Path:
    path = safe_absolute_path(path, label, must_exist=True)
    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"cannot stat {label} {path}: {error}")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size == 0:
        fail(f"{label} is missing, empty, symlinked, or not a regular file: {path}")
    if executable and not os.access(path, os.X_OK):
        fail(f"{label} is not executable: {path}")
    return path


def directory_input(path: Path, label: str) -> Path:
    path = safe_absolute_path(path, label, must_exist=True)
    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"cannot stat {label} {path}: {error}")
    if not stat.S_ISDIR(metadata.st_mode):
        fail(f"{label} is not a real directory: {path}")
    return path


def load_json(path: Path, label: str) -> dict[str, Any]:
    path = regular_input(path, label)
    if path.stat().st_size > MAX_JSON_SIZE:
        fail(f"{label} exceeds {MAX_JSON_SIZE} bytes")
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read {label} {path}: {error}")
    return require_object(value, label)


def run_tool(
    path: Path,
    arguments: list[str],
    label: str,
    *,
    allowed_exit_statuses: frozenset[int] = frozenset({0}),
) -> tuple[str, str]:
    try:
        # Profile validation is authoritative and may run as root from Portage.
        # Never inherit loader, Python, compiler-wrapper, or user startup state
        # from the caller into an observed compiler/profile tool.
        environment = {
            "HOME": "/nonexistent",
            "LANG": "C",
            "LANGUAGE": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        }
        completed = subprocess.run(
            [os.fspath(path), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            timeout=TOOL_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"{label} failed to execute: {error}")
    if len(completed.stdout) > MAX_TOOL_OUTPUT or len(completed.stderr) > MAX_TOOL_OUTPUT:
        fail(f"{label} exceeded the {MAX_TOOL_OUTPUT}-byte output limit")
    try:
        stdout = completed.stdout.decode("utf-8", errors="strict")
        stderr = completed.stderr.decode("utf-8", errors="strict")
    except UnicodeError as error:
        fail(f"{label} returned non-UTF-8 output: {error}")
    if completed.returncode not in allowed_exit_statuses:
        detail = stderr.strip() or stdout.strip() or "no diagnostic"
        fail(f"{label} exited {completed.returncode}: {detail}")
    return stdout, stderr


def observe_compiler(
    path: Path,
    expected_sha256: str,
    family: str,
    expected_major: int,
    rust_llvm_major: int | None,
) -> dict[str, object]:
    path = regular_input(path, "compiler", executable=True)
    if sha256_file(path) != expected_sha256:
        fail("compiler binary SHA-256 does not match --compiler-sha256")
    arguments = ["version"] if family == "go" else ["--version"]
    stdout, stderr = run_tool(path, arguments, "compiler version command")
    combined = stdout + "\n" + stderr
    if family == "clang":
        match = re.search(r"(?im)clang version\s+([0-9]+)(?:\.|\s)", combined)
    elif family == "gcc":
        if "clang" in combined.lower():
            match = None
        else:
            match = re.search(r"(?im)\bgcc(?:\s|[^\n]*?\))[^0-9]*([0-9]+)(?:\.|\s)", combined)
    elif family == "rust":
        match = re.search(r"(?im)^rustc\s+([0-9]+)(?:\.|\s)", combined)
    else:
        match = re.search(r"(?im)^go version go([0-9]+)(?:\.|\s)", combined)
    if match is None:
        fail(f"compiler output does not prove the requested {family} family")
    observed_major = int(match.group(1))
    if observed_major != expected_major:
        fail(f"compiler major {observed_major} does not match {expected_major}")
    observed_rust_llvm: int | None = None
    verbose_stdout = ""
    verbose_stderr = ""
    if family == "rust":
        verbose_stdout, verbose_stderr = run_tool(path, ["-vV"], "rustc verbose version command")
        llvm_match = re.search(
            r"(?im)^LLVM version:\s*([0-9]+)(?:\.|\s)",
            verbose_stdout + "\n" + verbose_stderr,
        )
        if llvm_match is None:
            fail("rustc -vV does not report its bundled LLVM major")
        observed_rust_llvm = int(llvm_match.group(1))
        if rust_llvm_major != observed_rust_llvm:
            fail(
                f"rustc LLVM major {observed_rust_llvm} does not match "
                f"--rust-llvm-major={rust_llvm_major}"
            )
    return {
        "path": os.fspath(path),
        "sha256": expected_sha256,
        "family": family,
        "major": observed_major,
        "rust_llvm_major": observed_rust_llvm,
        "version_arguments": arguments,
        "version_stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "version_stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "verbose_version_stdout_sha256": hashlib.sha256(
            verbose_stdout.encode("utf-8")
        ).hexdigest(),
        "verbose_version_stderr_sha256": hashlib.sha256(
            verbose_stderr.encode("utf-8")
        ).hexdigest(),
    }


def observe_profile_tool(
    path: Path,
    expected_sha256: str,
    backend: str,
    expected_major: int,
) -> dict[str, object]:
    path = regular_input(path, "profile tool", executable=True)
    if sha256_file(path) != expected_sha256:
        fail("profile-tool binary SHA-256 does not match --profile-tool-sha256")
    arguments = ["version"] if backend == "go" else ["--version"]
    stdout, stderr = run_tool(path, arguments, "profile-tool version command")
    combined = stdout + "\n" + stderr
    if backend in {"clang-ir", "clang-sample", "rust"}:
        match = re.search(r"(?im)LLVM version\s+([0-9]+)(?:\.|\s)", combined)
    elif backend == "gcc":
        match = re.search(
            r"(?im)(?:gcov-tool|GCC)[^0-9\n]*([0-9]+)(?:\.|\s)", combined
        )
    else:
        match = re.search(r"(?im)^go version go([0-9]+)(?:\.|\s)", combined)
    if match is None:
        fail(f"profile-tool output does not prove the {backend} profile family")
    observed_major = int(match.group(1))
    if observed_major != expected_major:
        fail(f"profile-tool major {observed_major} does not match {expected_major}")
    return {
        "path": os.fspath(path),
        "sha256": expected_sha256,
        "major": observed_major,
        "version_arguments": arguments,
        "version_stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "version_stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        # Retained only for comparison with profile-identity.py sample metadata.
        "version_stdout": stdout,
        "version_stderr": stderr,
    }


def validate_indexed_profile(profile: Path, tool: Path, backend: str) -> dict[str, object]:
    if profile.name == "sample.prof" or profile.suffix != ".profdata":
        fail(f"{backend} indexed profile must use an unambiguous .profdata name")
    stdout, _stderr = run_tool(
        tool,
        ["show", "--all-functions", "--counts", os.fspath(profile)],
        f"{backend} indexed-profile validation",
    )
    functions = re.search(r"(?m)^Total functions:\s*([0-9]+)\s*$", stdout)
    maximum = re.search(r"(?m)^Maximum function count:\s*([0-9]+)\s*$", stdout)
    if functions is None or int(functions.group(1)) < 1:
        fail(f"{backend} profile contains no readable functions")
    if maximum is None or int(maximum.group(1)) < 1:
        fail(f"{backend} profile contains no nonzero function count")
    return {
        "kind": "llvm-indexed",
        "function_count": int(functions.group(1)),
        "maximum_function_count": int(maximum.group(1)),
        "validation_arguments": [
            "show",
            "--all-functions",
            "--counts",
            os.fspath(profile),
        ],
        "validation_output_sha256": hashlib.sha256(
            (stdout + "\0" + _stderr).encode("utf-8")
        ).hexdigest(),
    }


def validate_recorded_file(
    path_value: object, sha_value: object, label: str
) -> tuple[Path, str]:
    path = regular_input(Path(require_string(path_value, f"{label}_path")), label)
    expected = require_hex64(sha_value, f"{label}_sha256")
    if sha256_file(path) != expected:
        fail(f"recorded {label} SHA-256 no longer matches")
    return path, expected


def validate_recorded_tool_identity(value: object, label: str) -> Path:
    identity = require_object(value, f"{label} identity")
    require_exact_fields(identity, LLVM_TOOL_IDENTITY_FIELDS, f"{label} identity")
    path = regular_input(Path(require_string(identity["realpath"], f"{label}.realpath")), label, executable=True)
    if sha256_file(path) != require_hex64(identity["sha256"], f"{label}.sha256"):
        fail(f"recorded {label} binary SHA-256 no longer matches")
    version_stdout = require_string(
        identity["version_stdout"], f"{label}.version_stdout"
    )
    if not isinstance(identity["version_stderr"], str):
        fail(f"{label}.version_stderr must be a string")
    observed_stdout, observed_stderr = run_tool(
        path, ["--version"], f"recorded {label} version command"
    )
    if (
        version_stdout != observed_stdout
        or identity["version_stderr"] != observed_stderr
    ):
        fail(f"recorded {label} complete version output no longer matches")
    return path


def extract_sample_binary_identity(
    binary: Path, readelf: Path, objcopy: Path
) -> tuple[str, str]:
    notes_stdout, _notes_stderr = run_tool(
        readelf, ["-n", os.fspath(binary)], "sample binary build-ID inspection"
    )
    build_ids = {
        match.group(1).lower()
        for match in re.finditer(
            r"(?im)^\s*Build ID:\s*([0-9a-f]+)\s*$", notes_stdout
        )
    }
    if len(build_ids) != 1:
        fail("sample binary must contain exactly one unambiguous GNU build ID")
    build_id = build_ids.pop()
    if not BUILD_ID_RE.fullmatch(build_id) or len(build_id) % 2:
        fail("sample binary contains an invalid GNU build ID")
    with tempfile.TemporaryDirectory(prefix="gentoo-sample-validation-") as directory:
        text_path = Path(directory) / "text.section"
        rewritten_path = Path(directory) / "rewritten-elf"
        run_tool(
            objcopy,
            [
                "--dump-section",
                f".text={text_path}",
                os.fspath(binary),
                os.fspath(rewritten_path),
            ],
            "sample binary .text extraction",
        )
        text_path = regular_input(text_path, "sample binary .text payload")
        regular_input(rewritten_path, "sample binary scratch ELF")
        text_sha256 = sha256_file(text_path)
    return build_id, text_sha256


def validate_sample_source(value: object, profile: Path) -> dict[str, object]:
    source = require_object(value, "sample source")
    kind = require_string(source.get("kind"), "sample source kind")
    if kind != "llvm-profgen":
        fail(
            f"unsupported sample source kind: {kind}; "
            "authoritative sample profiles require llvm-profgen"
        )
    require_exact_fields(source, SAMPLE_PROFGEN_SOURCE_FIELDS, "sample source")
    binary, binary_sha256 = validate_recorded_file(
        source["binary_path"], source["binary_sha256"], "binary"
    )
    binary_observation = validate_file_observation(
        source["binary_observation"], binary, "binary"
    )
    perf_data, perf_data_sha256 = validate_recorded_file(
        source["perf_data_path"], source["perf_data_sha256"], "perf_data"
    )
    perf_data_observation = validate_file_observation(
        source["perf_data_observation"], perf_data, "perf data"
    )
    debug_binary: Path | None
    debug_binary_sha256: str | None
    debug_binary_observation: dict[str, object] | None
    if source["debug_binary_path"] is None and source["debug_binary_sha256"] is None:
        debug_binary = None
        debug_binary_sha256 = None
        if source["debug_binary_observation"] is not None:
            fail(
                "sample debug binary observation must be null when no debug binary is set"
            )
        debug_binary_observation = None
    elif source["debug_binary_path"] is not None and source["debug_binary_sha256"] is not None:
        debug_binary, debug_binary_sha256 = validate_recorded_file(
            source["debug_binary_path"], source["debug_binary_sha256"], "debug_binary"
        )
        debug_binary_observation = validate_file_observation(
            source["debug_binary_observation"], debug_binary, "debug binary"
        )
    else:
        fail("sample debug binary path and SHA-256 must both be null or both be set")
    tools = {
        name: validate_recorded_tool_identity(source[name], name)
        for name in ("producer", "readelf", "objcopy")
    }
    arguments = source["command_arguments"]
    if not isinstance(arguments, list) or not arguments or not all(
        isinstance(item, str) and item for item in arguments
    ):
        fail("sample source command_arguments must be a nonempty string array")
    expected_arguments = [f"--binary={binary}"]
    if debug_binary is not None:
        expected_arguments.append(f"--debug-binary={debug_binary}")
    expected_arguments.extend(
        [
            f"--perfdata={perf_data}",
            "--format=extbinary",
            "--show-detailed-warning",
            f"--output={profile}.partial",
        ]
    )
    if arguments != expected_arguments:
        fail("sample source command does not match its exact binary/perf/profile inputs")
    command_output_sha256 = require_hex64(
        source["command_output_sha256"], "command_output_sha256"
    )
    conversion_log, conversion_log_sha256 = validate_recorded_file(
        source["conversion_log_path"],
        source["conversion_log_sha256"],
        "conversion log",
    )
    if conversion_log != profile.parent / "llvm-profgen-conversion-log.json":
        fail("conversion log is not the exact required sibling of sample.prof")
    conversion_log_observation = validate_file_observation(
        source["conversion_log_observation"], conversion_log, "conversion log"
    )
    validate_conversion_log(
        conversion_log,
        conversion_log_sha256,
        conversion_log_observation,
        source["producer"],
        arguments,
        command_output_sha256,
    )
    return {
        "binary_path": os.fspath(binary),
        "binary_sha256": binary_sha256,
        "binary_observation": binary_observation,
        "command_arguments": arguments,
        "command_output_sha256": command_output_sha256,
        "conversion_log_path": os.fspath(conversion_log),
        "conversion_log_sha256": conversion_log_sha256,
        "conversion_log_observation": conversion_log_observation,
        "debug_binary_path": (
            os.fspath(debug_binary) if debug_binary is not None else None
        ),
        "debug_binary_sha256": debug_binary_sha256,
        "debug_binary_observation": debug_binary_observation,
        "kind": "llvm-profgen",
        "objcopy": source["objcopy"],
        "objcopy_path": os.fspath(tools["objcopy"]),
        "perf_data_path": os.fspath(perf_data),
        "perf_data_sha256": perf_data_sha256,
        "perf_data_observation": perf_data_observation,
        "producer": source["producer"],
        "readelf": source["readelf"],
        "readelf_path": os.fspath(tools["readelf"]),
    }


def validate_conversion_log(
    path: Path,
    expected_sha256: str,
    expected_observation: dict[str, object],
    producer: object,
    command_arguments: list[str],
    command_output_sha256: str,
) -> dict[str, object]:
    path_stat = path.stat()
    if stat.S_IMODE(path_stat.st_mode) != 0o440 or path_stat.st_nlink != 1:
        fail("conversion log must be a link-count-one, mode-0440 regular file")
    if sha256_file(path) != expected_sha256:
        fail("conversion log no longer matches its recorded SHA-256")
    log = load_json(path, "conversion log")
    require_exact_fields(log, CONVERSION_LOG_FIELDS, "conversion log")
    if log["schema_version"] != 1:
        fail("conversion log schema_version must be 1")
    producer_identity = require_object(producer, "sample source producer")
    if log["producer_realpath"] != producer_identity["realpath"]:
        fail("conversion log producer realpath mismatch")
    if log["producer_sha256"] != producer_identity["sha256"]:
        fail("conversion log producer SHA-256 mismatch")
    if log["command_arguments"] != command_arguments:
        fail("conversion log command arguments mismatch")
    if log["exit_status"] != 0:
        fail("conversion log does not record a successful converter exit")
    stdout = log["stdout"]
    stderr = log["stderr"]
    if not isinstance(stdout, str) or "\x00" in stdout:
        fail("conversion log stdout must be a string without NUL bytes")
    if not isinstance(stderr, str) or "\x00" in stderr:
        fail("conversion log stderr must be a string without NUL bytes")
    if require_hex64(log["stdout_sha256"], "conversion log stdout SHA-256") != hashlib.sha256(
        stdout.encode("utf-8")
    ).hexdigest():
        fail("conversion log stdout hash mismatch")
    if require_hex64(log["stderr_sha256"], "conversion log stderr SHA-256") != hashlib.sha256(
        stderr.encode("utf-8")
    ).hexdigest():
        fail("conversion log stderr hash mismatch")
    observed_output_sha256 = hashlib.sha256(
        (stdout + "\0" + stderr).encode("utf-8")
    ).hexdigest()
    if observed_output_sha256 != command_output_sha256:
        fail("conversion log streams do not match command_output_sha256")
    if observe_regular_file(path, "conversion log") != expected_observation:
        fail("conversion log changed while it was being validated")
    return log


def validate_sample_metadata(
    metadata_path: Path,
    profile: Path,
    profile_sha256: str,
    sample_input_fingerprint: str,
    abi: str,
    compiler_major: int,
    tool_identity: dict[str, object],
    sample_stdout: str,
    sample_stderr: str,
) -> dict[str, Any]:
    if metadata_path != profile.parent / "sample-metadata.json":
        fail("sample metadata is not the exact required sibling of sample.prof")
    metadata = load_json(metadata_path, "sample metadata")
    require_exact_fields(metadata, SAMPLE_METADATA_FIELDS, "sample metadata")
    if metadata["schema_version"] != 4:
        fail("sample metadata schema_version must be 4")
    if metadata["profile_family"] != "clang-sample" or metadata["profile_format"] != "llvm-sample":
        fail("sample metadata has the wrong family or format")
    if metadata["profile_path"] != os.fspath(profile):
        fail("sample metadata profile_path mismatch")
    if metadata["profile_sha256"] != profile_sha256:
        fail("sample metadata profile SHA-256 mismatch")
    if metadata["profile_size"] != profile.stat().st_size:
        fail("sample metadata profile size mismatch")

    package = require_object(metadata["package"], "sample package")
    require_exact_fields(package, SAMPLE_PACKAGE_FIELDS, "sample package")
    if package["fingerprint"] != sample_input_fingerprint or package["abi"] != abi:
        fail("sample metadata input fingerprint or ABI mismatch")
    cpv = require_string(package["cpv"], "sample package CPV")
    if CPV_RE.fullmatch(cpv) is None:
        fail("sample package CPV is not an exact Gentoo CPV")

    compiler = require_object(metadata["compiler"], "sample compiler")
    require_exact_fields(compiler, SAMPLE_COMPILER_FIELDS, "sample compiler")
    if compiler != {"family": "clang", "major": compiler_major}:
        fail("sample metadata compiler identity mismatch")

    input_identity = require_object(metadata["input_identity"], "sample input identity")
    require_exact_fields(input_identity, SAMPLE_INPUT_FIELDS, "sample input identity")
    build_id = require_string(input_identity["build_id"], "sample build ID")
    if not BUILD_ID_RE.fullmatch(build_id) or len(build_id) % 2:
        fail("sample build ID is not an even-length lowercase hexadecimal value")
    require_hex64(input_identity["text_sha256"], "sample text SHA-256")

    validator = require_object(metadata["validator"], "sample validator")
    require_exact_fields(validator, LLVM_TOOL_IDENTITY_FIELDS, "sample validator")
    expected_validator = {
        "realpath": tool_identity["path"],
        "sha256": tool_identity["sha256"],
        "version_stderr": tool_identity["version_stderr"],
        "version_stdout": tool_identity["version_stdout"],
    }
    if validator != expected_validator:
        fail("sample metadata validator no longer matches the exact llvm-profdata tool")

    validation = require_object(metadata["validation"], "sample validation")
    require_exact_fields(validation, SAMPLE_VALIDATION_FIELDS, "sample validation")
    expected_arguments = ["show", "--sample", os.fspath(profile)]
    if validation["command_arguments"] != expected_arguments:
        fail("sample metadata validation command mismatch")
    output_hash = hashlib.sha256(
        (sample_stdout + "\0" + sample_stderr).encode("utf-8")
    ).hexdigest()
    if validation["output_sha256"] != output_hash:
        fail("sample metadata validation output mismatch")
    source = validate_sample_source(metadata["source"], profile)
    observed_build_id, observed_text_sha256 = extract_sample_binary_identity(
        Path(require_string(source["binary_path"], "sample source binary path")),
        Path(require_string(source["readelf_path"], "sample source readelf path")),
        Path(require_string(source["objcopy_path"], "sample source objcopy path")),
    )
    if input_identity["build_id"] != observed_build_id:
        fail("sample metadata build ID does not match the exact profiled binary")
    if input_identity["text_sha256"] != observed_text_sha256:
        fail("sample metadata .text SHA-256 does not match the exact profiled binary")
    # Extraction is required to be read-only.  Re-run the complete source
    # observation after the native identity proof before publication.
    validate_sample_source(metadata["source"], profile)
    metadata["reproducibility"] = validate_reproducibility(
        metadata["reproducibility"]
    )
    return metadata


def validate_sample_profile(
    profile: Path,
    tool: Path,
    metadata_path: Path,
    profile_sha256: str,
    sample_input_fingerprint: str,
    abi: str,
    compiler_major: int,
    tool_identity: dict[str, object],
) -> dict[str, object]:
    if profile.name != "sample.prof":
        fail("Clang sample profile must be named exactly sample.prof")
    recorded_stdout, recorded_stderr = run_tool(
        tool,
        ["show", "--sample", os.fspath(profile)],
        "recorded sample-aware validation",
    )
    stdout, _stderr = run_tool(
        tool,
        ["show", "--sample", "--all-functions", "--counts", os.fspath(profile)],
        "detailed sample-aware validation",
    )
    functions = len(re.findall(r"(?m)^Function:\s+\S+", stdout))
    positive_totals = [
        int(match.group(1))
        for match in re.finditer(r"(?m)^([0-9]+),\s*[0-9]+,\s*[0-9]+ sampled lines\s*$", stdout)
    ]
    if functions < 1 or not any(value > 0 for value in positive_totals):
        fail("Clang sample profile has no function with a nonzero sample total")
    metadata = validate_sample_metadata(
        metadata_path,
        profile,
        profile_sha256,
        sample_input_fingerprint,
        abi,
        compiler_major,
        tool_identity,
        recorded_stdout,
        recorded_stderr,
    )
    source = require_object(metadata["source"], "sample source")
    if source.get("kind") != "llvm-profgen":
        fail("authoritative sample manifests require source.kind=llvm-profgen")
    input_identity = require_object(metadata["input_identity"], "sample input identity")
    return {
        "kind": "llvm-sample",
        "function_count": functions,
        "maximum_sample_total": max(positive_totals),
        "sample_metadata_path": os.fspath(metadata_path),
        "sample_metadata_sha256": sha256_file(metadata_path),
        "source_kind": "llvm-profgen",
        "input_build_id": input_identity["build_id"],
        "input_text_sha256": input_identity["text_sha256"],
        "sample_input_fingerprint": sample_input_fingerprint,
        "reproducibility": metadata["reproducibility"],
        "recorded_validation_output_sha256": hashlib.sha256(
            (recorded_stdout + "\0" + recorded_stderr).encode("utf-8")
        ).hexdigest(),
        "detailed_validation_arguments": [
            "show",
            "--sample",
            "--all-functions",
            "--counts",
            os.fspath(profile),
        ],
        "detailed_validation_output_sha256": hashlib.sha256(
            (stdout + "\0" + _stderr).encode("utf-8")
        ).hexdigest(),
    }


def gcc_directory_hash(profile: Path) -> tuple[str, list[Path]]:
    files: list[Path] = []
    for root, directories, names in os.walk(profile, followlinks=False):
        root_path = Path(root)
        for directory in directories:
            candidate = root_path / directory
            if candidate.is_symlink():
                fail(f"GCC profile directory contains a symlink: {candidate}")
        for name in names:
            candidate = root_path / name
            if candidate.is_symlink():
                fail(f"GCC profile directory contains a symlink: {candidate}")
            if candidate.suffix == ".gcda":
                regular_input(candidate, "GCC .gcda profile")
                files.append(candidate)
    if not files:
        fail("GCC profile directory contains no nonempty .gcda files")
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(profile).as_posix()):
        relative = "./" + path.relative_to(profile).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(sha256_file(path).encode("ascii") + b"\n")
    return digest.hexdigest(), files


def validate_gcc_profile(profile: Path, tool: Path) -> tuple[str, dict[str, object]]:
    profile_hash, files = gcc_directory_hash(profile)
    stdout, _stderr = run_tool(
        tool,
        ["overlap", "-f", os.fspath(profile), os.fspath(profile)],
        "GCC gcov profile validation",
        # gcov-tool reports a processable self-overlap with status 1 on the
        # active GCC 17 snapshot.  The complete/hot-file statistics below are
        # still mandatory, so an ordinary error cannot pass through silently.
        allowed_exit_statuses=frozenset({0, 1}),
    )
    gcda_match = re.search(r"(?m)^\s*gcda files:\s*([0-9]+)\s+", stdout)
    hot_match = re.search(r"(?m)^\s*hot files:\s*([0-9]+)\s+", stdout)
    if gcda_match is None or int(gcda_match.group(1)) != len(files):
        fail("gcov-tool did not read the complete .gcda set")
    if hot_match is None or int(hot_match.group(1)) < 1:
        fail("GCC profile contains no nonzero hot-file data")
    file_records = [
        {
            "path": path.relative_to(profile).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(files, key=lambda item: item.relative_to(profile).as_posix())
    ]
    return profile_hash, {
        "kind": "gcc-gcov",
        "gcda_file_count": len(files),
        "hot_file_count": int(hot_match.group(1)),
        "gcda_files": file_records,
        "validation_arguments": [
            "overlap",
            "-f",
            os.fspath(profile),
            os.fspath(profile),
        ],
        "validation_output_sha256": hashlib.sha256(
            (stdout + "\0" + _stderr).encode("utf-8")
        ).hexdigest(),
    }


def read_gnu_build_id(readelf: Path, binary: Path) -> str | None:
    stdout, _stderr = run_tool(readelf, ["-n", os.fspath(binary)], "GNU build-ID inspection")
    matches: list[str] = re.findall(
        r"(?im)^\s*Build ID:\s*([0-9a-f]+)\s*$", stdout
    )
    if len(matches) > 1:
        fail("profiled Go binary contains multiple GNU build IDs")
    if not matches:
        return None
    build_id = matches[0]
    if not BUILD_ID_RE.fullmatch(build_id) or len(build_id) % 2:
        fail("profiled Go binary has an invalid GNU build ID")
    return build_id


def parse_go_raw_profile(
    raw: str,
    binary: Path,
    gnu_build_id: str | None,
    go_build_id: str,
    target_package: str,
    target_symbols: list[str],
) -> dict[str, object]:
    if not all(marker in raw for marker in ("PeriodType:", "Samples:\n", "Locations\n", "Mappings\n")):
        fail("Go pprof output is structurally incomplete")
    sampled_locations: set[int] = set()
    nonzero_samples = 0
    in_samples = False
    for line in raw.splitlines():
        if line == "Samples:":
            in_samples = True
            continue
        if line == "Locations":
            in_samples = False
        if in_samples:
            match = re.match(r"^\s*([0-9]+)\s+([0-9]+):\s+([0-9 ]+)\s*$", line)
            if match and int(match.group(1)) > 0 and int(match.group(2)) > 0:
                nonzero_samples += int(match.group(1))
                sampled_locations.update(int(item) for item in match.group(3).split())
    if nonzero_samples < 1 or not sampled_locations:
        fail("Go profile contains no nonzero CPU samples")

    location_pattern = re.compile(
        r"^\s*([0-9]+):\s+0x[0-9a-f]+\s+M=([0-9]+)\s+(\S+)\s+\S+:[0-9]+:[0-9]+\s+s=([0-9]+)\s*$",
        re.MULTILINE,
    )
    locations: dict[str, list[tuple[int, int, int]]] = {}
    for match in location_pattern.finditer(raw):
        location_id = int(match.group(1))
        mapping_id = int(match.group(2))
        symbol = match.group(3)
        start_line = int(match.group(4))
        locations.setdefault(symbol, []).append((location_id, mapping_id, start_line))
    if not locations:
        fail("Go profile contains no readable function metadata")
    prefix = target_package + "."
    if not any(symbol.startswith(prefix) for symbol in locations):
        fail("Go profile contains no function from the declared target package")
    selected_locations: dict[str, tuple[int, int, int]] = {}
    for symbol in target_symbols:
        records = locations.get(symbol)
        if records is None:
            fail(f"Go profile does not contain target symbol: {symbol}")
        matching = [
            record
            for record in records
            if record[2] > 0 and record[0] in sampled_locations
        ]
        if not matching:
            fail(f"Go target symbol lacks positive function metadata or samples: {symbol}")
        selected_locations[symbol] = matching[0]

    mapping_pattern = re.compile(
        r"^([0-9]+):\s+0x[0-9a-f]+/0x[0-9a-f]+/0x[0-9a-f]+\s+(\S+)\s+(\S*)\s+\[FN\]\s*$",
        re.MULTILINE,
    )
    mappings = {
        int(match.group(1)): (match.group(2), match.group(3))
        for match in mapping_pattern.finditer(raw)
    }
    target_mapping_ids = {
        selected_locations[symbol][1] for symbol in target_symbols
    }
    if len(target_mapping_ids) != 1:
        fail("Go target symbols do not share one exact executable mapping")
    mapping = mappings.get(next(iter(target_mapping_ids)))
    if mapping is None:
        fail("Go target mapping is missing or lacks function metadata")
    mapping_path, mapping_build_id = mapping
    if gnu_build_id is not None:
        if mapping_build_id != gnu_build_id:
            fail("Go profile mapping GNU build ID does not match the profiled binary")
    elif mapping_build_id:
        if mapping_build_id != go_build_id:
            fail("Go profile mapping build ID does not match the native Go build ID")
    elif mapping_path != os.fspath(binary):
        fail("Go profile without a GNU build ID does not map the exact profiled binary path")
    if gnu_build_id is not None:
        mapping_identity = {"type": "gnu-build-id", "value": gnu_build_id}
    elif mapping_build_id:
        mapping_identity = {"type": "go-build-id", "value": go_build_id}
    else:
        mapping_identity = {"type": "exact-path", "value": os.fspath(binary)}
    return {
        "nonzero_sample_count": nonzero_samples,
        "mapping_identity": mapping_identity,
        "target_function_metadata": [
            {
                "symbol": symbol,
                "location_id": selected_locations[symbol][0],
                "mapping_id": selected_locations[symbol][1],
                "start_line": selected_locations[symbol][2],
            }
            for symbol in target_symbols
        ],
    }


def validate_go_profile(
    profile: Path,
    go_tool: Path,
    binary: Path,
    binary_sha256: str,
    expected_go_build_id: str,
    readelf: Path,
    readelf_sha256: str,
    target_package: str,
    target_symbols: list[str],
) -> dict[str, object]:
    if profile.suffix not in {".pprof", ".pgo"}:
        fail("Go profile must use an unambiguous .pprof or .pgo suffix")
    binary = regular_input(binary, "profiled Go binary", executable=True)
    if sha256_file(binary) != binary_sha256:
        fail("profiled Go binary SHA-256 mismatch")
    readelf = regular_input(readelf, "readelf", executable=True)
    if sha256_file(readelf) != readelf_sha256:
        fail("readelf binary SHA-256 mismatch")
    go_build_id_stdout, _stderr = run_tool(
        go_tool, ["tool", "buildid", os.fspath(binary)], "Go build-ID inspection"
    )
    observed_go_build_id = go_build_id_stdout.strip()
    if not observed_go_build_id or re.search(r"[\s\x00]", observed_go_build_id):
        fail("profiled binary has no safe nonempty Go build ID")
    if observed_go_build_id != expected_go_build_id:
        fail("profiled binary Go build ID mismatch")
    gnu_build_id = read_gnu_build_id(readelf, binary)
    raw, _stderr = run_tool(
        go_tool,
        ["tool", "pprof", "-raw", os.fspath(binary), os.fspath(profile)],
        "Go pprof structural validation",
    )
    structural_proof = parse_go_raw_profile(
        raw,
        binary,
        gnu_build_id,
        observed_go_build_id,
        target_package,
        target_symbols,
    )
    return {
        "kind": "go-pprof",
        "profiled_binary_path": os.fspath(binary),
        "profiled_binary_sha256": binary_sha256,
        "go_build_id": observed_go_build_id,
        "gnu_build_id": gnu_build_id,
        "readelf_path": os.fspath(readelf),
        "readelf_sha256": readelf_sha256,
        "target_package": target_package,
        "target_symbols": target_symbols,
        "raw_validation_arguments": [
            "tool",
            "pprof",
            "-raw",
            os.fspath(binary),
            os.fspath(profile),
        ],
        "raw_validation_output_sha256": hashlib.sha256(
            (raw + "\0" + _stderr).encode("utf-8")
        ).hexdigest(),
        **structural_proof,
    }


def manifest_bytes(
    backend: str,
    fingerprint: str,
    abi: str,
    compiler_family: str,
    profile: Path,
    profile_sha256: str,
) -> bytes:
    fields = (
        ("schema", "gentoo-optimization-profile-v1"),
        ("backend", backend),
        ("fingerprint", fingerprint),
        ("abi", abi),
        ("compiler_family", compiler_family),
        ("profile_path", os.fspath(profile)),
        ("profile_sha256", profile_sha256),
        ("validation_status", "passed"),
    )
    return "".join(f"{key}={value}\n" for key, value in fields).encode("ascii")


def require_new_output(path: Path, label: str) -> Path:
    path = safe_absolute_path(path, label, must_exist=False)
    directory_input(path.parent, f"{label} parent")
    try:
        path.lstat()
    except FileNotFoundError:
        return path
    except OSError as error:
        fail(f"cannot inspect {label} {path}: {error}")
    fail(f"{label} already exists: {path}")


def stage_output(path: Path, payload: bytes, mode: int) -> str:
    descriptor: int | None = None
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.partial.", dir=path.parent
        )
        parent_gid = path.parent.stat().st_gid
        if os.geteuid() == 0:
            os.fchown(descriptor, 0, parent_gid)
        elif os.fstat(descriptor).st_gid != parent_gid:
            fail("cannot publish validation output with the trusted parent group")
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except BaseException as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        if isinstance(error, OSError):
            fail(f"cannot stage output for {path}: {error}")
        raise
    if temporary_name is None:
        fail(f"internal error: no staged output for {path}")
    return temporary_name


def atomic_publish_pair(
    manifest_path: Path,
    manifest_payload: bytes,
    metadata_path: Path,
    metadata_payload: bytes,
) -> None:
    manifest_path = require_new_output(manifest_path, "manifest output")
    metadata_path = require_new_output(metadata_path, "validation metadata output")
    if manifest_path == metadata_path:
        fail("manifest and validation metadata outputs must be distinct")
    if manifest_path.parent != metadata_path.parent:
        fail("manifest and validation metadata sidecar must share one directory")
    expected_metadata_path = Path(os.fspath(manifest_path) + ".metadata.json")
    if metadata_path != expected_metadata_path:
        fail(
            "validation metadata sidecar must be named "
            f"{expected_metadata_path.name}"
        )
    manifest_temporary: str | None = None
    metadata_temporary: str | None = None
    metadata_published = False
    transaction_complete = False
    try:
        manifest_temporary = stage_output(manifest_path, manifest_payload, 0o640)
        metadata_temporary = stage_output(metadata_path, metadata_payload, 0o640)
        # Metadata is renamed first.  The manifest is the authoritative commit
        # marker, so interruption can never expose an unbound passed manifest.
        os.replace(metadata_temporary, metadata_path)
        metadata_temporary = None
        metadata_published = True
        os.replace(manifest_temporary, manifest_path)
        manifest_temporary = None
        for parent in {manifest_path.parent, metadata_path.parent}:
            directory_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        transaction_complete = True
    except OSError as error:
        fail(f"cannot publish manifest transaction: {error}")
    finally:
        for temporary_name in (manifest_temporary, metadata_temporary):
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
        if metadata_published and not transaction_complete:
            # A signal can arrive after os.replace(manifest) and before the
            # in-memory commit flag changes.  Remove both in that boundary so
            # an unbound passed manifest can never survive.
            try:
                manifest_path.unlink()
            except FileNotFoundError:
                pass
            try:
                metadata_path.unlink()
            except FileNotFoundError:
                pass


def validate_arguments(arguments: argparse.Namespace) -> None:
    generation_from_fields(
        arguments.generation_id,
        arguments.inventory_id,
        arguments.inventory_sha256,
    )
    expected_family = BACKEND_FAMILY[arguments.backend]
    if arguments.compiler_family != expected_family:
        fail(
            f"backend {arguments.backend} requires compiler-family={expected_family}, "
            f"not {arguments.compiler_family}"
        )
    if arguments.abi not in {"amd64", "x86"}:
        fail("ABI must be exactly amd64 or x86")
    require_hex64(arguments.fingerprint, "fingerprint")
    require_hex64(arguments.compiler_sha256, "compiler SHA-256")
    require_hex64(arguments.profile_tool_sha256, "profile-tool SHA-256")
    require_positive_integer(arguments.compiler_major, "compiler major")
    require_positive_integer(arguments.profile_tool_major, "profile-tool major")

    if arguments.backend == "rust":
        require_positive_integer(arguments.rust_llvm_major, "rust LLVM major")
    elif arguments.rust_llvm_major is not None:
        fail("--rust-llvm-major is valid only for the Rust backend")
    if arguments.backend == "clang-sample":
        if arguments.sample_metadata is None:
            fail("Clang sample validation requires --sample-metadata")
        require_hex64(
            arguments.sample_input_fingerprint, "sample input fingerprint"
        )
    elif (
        arguments.sample_metadata is not None
        or arguments.sample_input_fingerprint is not None
    ):
        fail(
            "--sample-metadata and --sample-input-fingerprint are valid only "
            "for the Clang sample backend"
        )

    go_values = (
        arguments.go_binary,
        arguments.go_binary_sha256,
        arguments.go_build_id,
        arguments.go_target_package,
        arguments.readelf,
        arguments.readelf_sha256,
    )
    if arguments.backend == "go":
        if any(value is None for value in go_values) or not arguments.go_target_symbol:
            fail(
                "Go validation requires binary/hash/build-ID, readelf/hash, target package, "
                "and at least one target symbol"
            )
        require_hex64(arguments.go_binary_sha256, "Go binary SHA-256")
        require_hex64(arguments.readelf_sha256, "readelf SHA-256")
        target_package = require_string(arguments.go_target_package, "Go target package")
        if not SAFE_GO_COMPONENT_RE.fullmatch(target_package) or target_package.endswith("."):
            fail("Go target package is not a safe exact package name")
        if len(set(arguments.go_target_symbol)) != len(arguments.go_target_symbol):
            fail("Go target symbols contain duplicates")
        prefix = target_package + "."
        for symbol in arguments.go_target_symbol:
            if (
                not SAFE_GO_SYMBOL_RE.fullmatch(symbol)
                or not symbol.startswith(prefix)
            ):
                fail(f"Go target symbol is outside the target package: {symbol}")
        require_string(arguments.go_build_id, "Go build ID")
    elif any(value is not None for value in go_values) or arguments.go_target_symbol:
        fail("Go-specific arguments are valid only for the Go backend")


def perform_validation(
    arguments: argparse.Namespace, manifest_path: Path
) -> tuple[bytes, dict[str, object]]:
    validate_arguments(arguments)
    profile: Path
    if arguments.backend == "gcc":
        profile = directory_input(arguments.profile, "profile")
    else:
        profile = regular_input(arguments.profile, "profile")
    compiler = observe_compiler(
        arguments.compiler,
        arguments.compiler_sha256,
        arguments.compiler_family,
        arguments.compiler_major,
        arguments.rust_llvm_major,
    )
    profile_tool = observe_profile_tool(
        arguments.profile_tool,
        arguments.profile_tool_sha256,
        arguments.backend,
        arguments.profile_tool_major,
    )
    if arguments.backend in {"clang-ir", "clang-sample"}:
        if compiler["major"] != profile_tool["major"]:
            fail("Clang and llvm-profdata majors differ")
    elif arguments.backend == "gcc":
        if compiler["major"] != profile_tool["major"]:
            fail("GCC and gcov-tool majors differ")
    elif arguments.backend == "rust":
        if compiler["rust_llvm_major"] != profile_tool["major"]:
            fail("rustc bundled LLVM and llvm-profdata majors differ")
    else:
        if compiler["path"] != profile_tool["path"] or compiler["sha256"] != profile_tool["sha256"]:
            fail("Go compiler and pprof tool must be the same exact Go executable")

    if arguments.backend == "gcc":
        profile_sha256, backend_proof = validate_gcc_profile(
            profile, Path(str(profile_tool["path"]))
        )
    else:
        profile_sha256 = sha256_file(profile)
        if arguments.backend in {"clang-ir", "rust"}:
            backend_proof = validate_indexed_profile(
                profile, Path(str(profile_tool["path"])), arguments.backend
            )
        elif arguments.backend == "clang-sample":
            backend_proof = validate_sample_profile(
                profile,
                Path(str(profile_tool["path"])),
                arguments.sample_metadata,
                profile_sha256,
                arguments.sample_input_fingerprint,
                arguments.abi,
                arguments.compiler_major,
                profile_tool,
            )
            sample_reproducibility = require_object(
                backend_proof["reproducibility"], "sample reproducibility"
            )
            sample_generation = generation_from_fields(
                sample_reproducibility["optimization_generation_id"],
                sample_reproducibility["inventory_id"],
                sample_reproducibility["inventory_sha256"],
            )
            requested_generation = generation_from_fields(
                arguments.generation_id,
                arguments.inventory_id,
                arguments.inventory_sha256,
            )
            if sample_generation != requested_generation:
                fail(
                    "sample mapping input generation differs from the consumer "
                    "manifest generation"
                )
        else:
            backend_proof = validate_go_profile(
                profile,
                Path(str(profile_tool["path"])),
                arguments.go_binary,
                arguments.go_binary_sha256,
                arguments.go_build_id,
                arguments.readelf,
                arguments.readelf_sha256,
                arguments.go_target_package,
                arguments.go_target_symbol,
            )
    # Re-observe every identity after native validation.  Nothing may change
    # between proof and publication.
    if sha256_file(Path(str(compiler["path"]))) != arguments.compiler_sha256:
        fail("compiler changed during profile validation")
    if sha256_file(Path(str(profile_tool["path"]))) != arguments.profile_tool_sha256:
        fail("profile tool changed during profile validation")
    if arguments.backend == "gcc":
        final_profile_hash, _files = gcc_directory_hash(profile)
    else:
        final_profile_hash = sha256_file(profile)
    if final_profile_hash != profile_sha256:
        fail("profile payload changed during validation")
    if arguments.backend == "clang-sample":
        if sha256_file(arguments.sample_metadata) != backend_proof["sample_metadata_sha256"]:
            fail("sample metadata changed during validation")
        current_sample_metadata = load_json(arguments.sample_metadata, "sample metadata")
        validate_sample_source(current_sample_metadata["source"], profile)
    elif arguments.backend == "go":
        if sha256_file(arguments.go_binary) != arguments.go_binary_sha256:
            fail("profiled Go binary changed during validation")
        if sha256_file(arguments.readelf) != arguments.readelf_sha256:
            fail("readelf changed during validation")

    payload = manifest_bytes(
        arguments.backend,
        arguments.fingerprint,
        arguments.abi,
        arguments.compiler_family,
        profile,
        profile_sha256,
    )
    metadata: dict[str, object] = {
        "schema_version": 1,
        "generation": generation_from_fields(
            arguments.generation_id,
            arguments.inventory_id,
            arguments.inventory_sha256,
        ),
        "manifest": {
            "path": os.fspath(manifest_path),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "profile": {
            "backend": arguments.backend,
            "path": os.fspath(profile),
            "sha256": profile_sha256,
            "fingerprint": arguments.fingerprint,
            "abi": arguments.abi,
            "compiler_family": arguments.compiler_family,
        },
        "compiler": compiler,
        "profile_tool": profile_tool,
        "backend_proof": backend_proof,
    }
    return payload, metadata


def metadata_bytes(metadata: dict[str, object]) -> bytes:
    return (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")


def fixture_lock_paths(
    arguments: argparse.Namespace,
) -> tuple[Path, Path, Path] | None:
    values = (
        arguments.test_framework_lock,
        arguments.test_project_lock,
        arguments.test_generation_lock,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        fail("test mode requires framework, project, and generation lock paths")
    return (values[0], values[1], values[2])


def command_produce(arguments: argparse.Namespace) -> int:
    generation = generation_from_fields(
        arguments.generation_id,
        arguments.inventory_id,
        arguments.inventory_sha256,
    )
    with profile_lock_hierarchy(
        exclusive=True,
        expected_generation=generation,
        expected_generation_id=None,
        timeout_seconds=arguments.lock_timeout_seconds,
        test_mode=arguments.test_mode,
        test_paths=fixture_lock_paths(arguments),
    ):
        return _command_produce_locked(arguments)


def _command_produce_locked(arguments: argparse.Namespace) -> int:
    manifest_path = safe_absolute_path(
        arguments.manifest_out, "manifest output", must_exist=False
    )
    manifest_payload, metadata = perform_validation(arguments, manifest_path)
    atomic_publish_pair(
        manifest_path,
        manifest_payload,
        arguments.metadata_out,
        metadata_bytes(metadata),
    )
    return 0


def require_metadata_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        fail(f"{label} must be a nonempty string array")
    if len(set(value)) != len(value):
        fail(f"{label} contains duplicate values")
    return value


def arguments_from_metadata(
    metadata: dict[str, Any], manifest_path: Path
) -> argparse.Namespace:
    require_exact_fields(metadata, VALIDATION_METADATA_FIELDS, "validation metadata")
    if metadata["schema_version"] != 1:
        fail("validation metadata schema_version must be 1")
    generation = validate_generation(metadata["generation"], "validation generation")
    manifest = require_object(metadata["manifest"], "manifest identity")
    require_exact_fields(manifest, MANIFEST_IDENTITY_FIELDS, "manifest identity")
    if manifest["path"] != os.fspath(manifest_path):
        fail("validation metadata references a different manifest path")
    require_hex64(manifest["sha256"], "manifest SHA-256")

    profile = require_object(metadata["profile"], "profile identity")
    require_exact_fields(profile, PROFILE_IDENTITY_FIELDS, "profile identity")
    backend = require_string(profile["backend"], "profile backend")
    if backend not in BACKEND_FAMILY:
        fail(f"validation metadata has an unknown backend: {backend}")
    fingerprint = require_hex64(profile["fingerprint"], "profile fingerprint")
    profile_sha256 = require_hex64(profile["sha256"], "profile SHA-256")
    abi = require_string(profile["abi"], "profile ABI")
    compiler_family = require_string(profile["compiler_family"], "compiler family")
    profile_path = Path(require_string(profile["path"], "profile path"))

    compiler = require_object(metadata["compiler"], "compiler identity")
    require_exact_fields(compiler, COMPILER_IDENTITY_FIELDS, "compiler identity")
    compiler_path = Path(require_string(compiler["path"], "compiler path"))
    compiler_sha256 = require_hex64(compiler["sha256"], "compiler SHA-256")
    compiler_major = require_positive_integer(compiler["major"], "compiler major")
    if compiler["family"] != compiler_family:
        fail("compiler identity family differs from profile identity")
    rust_llvm_major_value = compiler["rust_llvm_major"]
    rust_llvm_major: int | None
    if rust_llvm_major_value is None:
        rust_llvm_major = None
    else:
        rust_llvm_major = require_positive_integer(
            rust_llvm_major_value, "rust LLVM major"
        )
    for key in (
        "version_stdout_sha256",
        "version_stderr_sha256",
        "verbose_version_stdout_sha256",
        "verbose_version_stderr_sha256",
    ):
        require_hex64(compiler[key], f"compiler {key}")
    require_metadata_list(compiler["version_arguments"], "compiler version arguments")

    profile_tool = require_object(metadata["profile_tool"], "profile-tool identity")
    require_exact_fields(
        profile_tool, PROFILE_TOOL_IDENTITY_FIELDS, "profile-tool identity"
    )
    profile_tool_path = Path(
        require_string(profile_tool["path"], "profile-tool path")
    )
    profile_tool_sha256 = require_hex64(
        profile_tool["sha256"], "profile-tool SHA-256"
    )
    profile_tool_major = require_positive_integer(
        profile_tool["major"], "profile-tool major"
    )
    for key in ("version_stdout_sha256", "version_stderr_sha256"):
        require_hex64(profile_tool[key], f"profile-tool {key}")
    require_metadata_list(
        profile_tool["version_arguments"], "profile-tool version arguments"
    )
    if not isinstance(profile_tool["version_stdout"], str) or not isinstance(
        profile_tool["version_stderr"], str
    ):
        fail("profile-tool version outputs must be strings")

    proof = require_object(metadata["backend_proof"], "backend proof")
    require_exact_fields(proof, BACKEND_PROOF_FIELDS[backend], "backend proof")
    namespace = argparse.Namespace(
        backend=backend,
        profile=profile_path,
        fingerprint=fingerprint,
        abi=abi,
        compiler_family=compiler_family,
        compiler=compiler_path,
        compiler_sha256=compiler_sha256,
        compiler_major=compiler_major,
        profile_tool=profile_tool_path,
        profile_tool_sha256=profile_tool_sha256,
        profile_tool_major=profile_tool_major,
        rust_llvm_major=rust_llvm_major,
        sample_metadata=None,
        sample_input_fingerprint=None,
        go_binary=None,
        go_binary_sha256=None,
        go_build_id=None,
        go_target_package=None,
        go_target_symbol=[],
        readelf=None,
        readelf_sha256=None,
        generation_id=generation["generation_id"],
        inventory_id=generation["inventory_id"],
        inventory_sha256=generation["inventory_sha256"],
    )
    if backend == "clang-sample":
        namespace.sample_metadata = Path(
            require_string(proof["sample_metadata_path"], "sample metadata path")
        )
        require_hex64(proof["sample_metadata_sha256"], "sample metadata SHA-256")
        namespace.sample_input_fingerprint = require_hex64(
            proof["sample_input_fingerprint"], "sample input fingerprint"
        )
        validate_reproducibility(proof["reproducibility"])
    elif backend == "go":
        namespace.go_binary = Path(
            require_string(proof["profiled_binary_path"], "profiled Go binary path")
        )
        namespace.go_binary_sha256 = require_hex64(
            proof["profiled_binary_sha256"], "profiled Go binary SHA-256"
        )
        namespace.go_build_id = require_string(proof["go_build_id"], "Go build ID")
        namespace.go_target_package = require_string(
            proof["target_package"], "Go target package"
        )
        namespace.go_target_symbol = require_metadata_list(
            proof["target_symbols"], "Go target symbols"
        )
        namespace.readelf = Path(
            require_string(proof["readelf_path"], "readelf path")
        )
        namespace.readelf_sha256 = require_hex64(
            proof["readelf_sha256"], "readelf SHA-256"
        )
    # The profile hash is checked both against the literal manifest and the
    # newly observed metadata below.
    if profile_sha256 != profile["sha256"]:
        fail("internal profile SHA-256 mismatch")
    return namespace


def _command_verify_locked(
    arguments: argparse.Namespace, locked_generation: dict[str, str]
) -> int:
    manifest_path = regular_input(arguments.manifest, "dispatcher manifest")
    expected_metadata_path = Path(os.fspath(manifest_path) + ".metadata.json")
    if arguments.metadata != expected_metadata_path:
        fail(
            "validation metadata path does not match the deterministic "
            "<manifest>.metadata.json sidecar"
        )
    metadata = load_json(arguments.metadata, "validation metadata")
    namespace = arguments_from_metadata(metadata, manifest_path)
    if generation_from_fields(
        namespace.generation_id,
        namespace.inventory_id,
        namespace.inventory_sha256,
    ) != locked_generation:
        fail("validation metadata generation differs from the stable lock identity")
    manifest_payload = manifest_path.read_bytes()
    manifest_identity = require_object(metadata["manifest"], "manifest identity")
    if hashlib.sha256(manifest_payload).hexdigest() != manifest_identity["sha256"]:
        fail("dispatcher manifest SHA-256 does not match validation metadata")
    expected_payload, observed_metadata = perform_validation(namespace, manifest_path)
    if manifest_payload != expected_payload:
        fail("dispatcher manifest content is not the exact canonical eight-line form")
    if metadata != observed_metadata:
        fail("validation metadata no longer matches current complete identities and proof")
    # The sidecar is an authenticated immutable artifact, not merely a JSON
    # value.  Require the exact canonical bytes emitted by ``produce`` so
    # whitespace-only tampering cannot be accepted by Portage's dispatcher.
    try:
        metadata_payload = arguments.metadata.read_bytes()
    except OSError as error:
        fail(f"cannot read validation metadata bytes: {error}")
    if metadata_payload != metadata_bytes(metadata):
        fail("validation metadata is not the exact canonical JSON encoding")
    return 0


def command_verify(arguments: argparse.Namespace) -> int:
    with profile_lock_hierarchy(
        exclusive=False,
        expected_generation=None,
        expected_generation_id=None,
        timeout_seconds=arguments.lock_timeout_seconds,
        test_mode=arguments.test_mode,
        test_paths=fixture_lock_paths(arguments),
    ) as locked_generation:
        return _command_verify_locked(arguments, locked_generation)


def add_produce_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=tuple(BACKEND_FAMILY), required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--abi", required=True)
    parser.add_argument("--compiler-family", required=True)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--compiler-sha256", required=True)
    parser.add_argument("--compiler-major", type=int, required=True)
    parser.add_argument("--profile-tool", type=Path, required=True)
    parser.add_argument("--profile-tool-sha256", required=True)
    parser.add_argument("--profile-tool-major", type=int, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--metadata-out", type=Path, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--inventory-id", required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--rust-llvm-major", type=int)
    parser.add_argument("--sample-metadata", type=Path)
    parser.add_argument("--sample-input-fingerprint")
    parser.add_argument("--go-binary", type=Path)
    parser.add_argument("--go-binary-sha256")
    parser.add_argument("--go-build-id")
    parser.add_argument("--go-target-package")
    parser.add_argument("--go-target-symbol", action="append", default=[])
    parser.add_argument("--readelf", type=Path)
    parser.add_argument("--readelf-sha256")


def add_profile_lock_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lock-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--test-framework-lock", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--test-project-lock", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--test-generation-lock", type=Path, help=argparse.SUPPRESS)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    produce = subparsers.add_parser(
        "produce", help="prove a profile and atomically publish manifest plus metadata"
    )
    add_produce_arguments(produce)
    add_profile_lock_arguments(produce)
    produce.set_defaults(function=command_produce)
    verify = subparsers.add_parser(
        "verify", help="revalidate a manifest, sidecar, payload, and complete tool tuple"
    )
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--metadata", type=Path, required=True)
    add_profile_lock_arguments(verify)
    verify.set_defaults(function=command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    arguments = parser.parse_args(argv)
    previous_handlers: dict[signal.Signals, Any] = {}

    def interrupted(signum: int, _frame: object) -> NoReturn:
        raise ProfileValidationError(f"interrupted by signal {signum}")

    for item in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        previous_handlers[item] = signal.getsignal(item)
        signal.signal(item, interrupted)
    try:
        return int(arguments.function(arguments))
    except (ProfileValidationError, ProfileLockError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        for item, handler in previous_handlers.items():
            signal.signal(item, handler)


if __name__ == "__main__":
    raise SystemExit(main())

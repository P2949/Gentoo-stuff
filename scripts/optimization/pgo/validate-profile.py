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
from pathlib import Path
from typing import Any, NoReturn


BUFFER_SIZE = 1024 * 1024
MAX_JSON_SIZE = 4 * 1024 * 1024
MAX_TOOL_OUTPUT = 4 * 1024 * 1024
TOOL_TIMEOUT_SECONDS = 30
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_RE = re.compile(r"^[0-9a-f]{8,128}$")
SAFE_GO_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_./+@~-]+$")

BACKEND_FAMILY = {
    "clang-ir": "clang",
    "clang-sample": "clang",
    "gcc": "gcc",
    "rust": "rust",
    "go": "go",
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
SAMPLE_EXTERNAL_SOURCE_FIELDS = {"kind"}
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


def run_tool(path: Path, arguments: list[str], label: str) -> tuple[str, str]:
    try:
        completed = subprocess.run(
            [os.fspath(path), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            timeout=TOOL_TIMEOUT_SECONDS,
            check=False,
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
    if completed.returncode != 0:
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
        "major": observed_major,
        "rust_llvm_major": observed_rust_llvm,
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
        "version_stdout": stdout,
        "version_stderr": stderr,
    }


def validate_indexed_profile(profile: Path, tool: Path, backend: str) -> None:
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


def validate_recorded_file(path_value: object, sha_value: object, label: str) -> None:
    path = regular_input(Path(require_string(path_value, f"{label}_path")), label)
    expected = require_hex64(sha_value, f"{label}_sha256")
    if sha256_file(path) != expected:
        fail(f"recorded {label} SHA-256 no longer matches")


def validate_recorded_tool_identity(value: object, label: str) -> None:
    identity = require_object(value, f"{label} identity")
    require_exact_fields(identity, LLVM_TOOL_IDENTITY_FIELDS, f"{label} identity")
    path = regular_input(Path(require_string(identity["realpath"], f"{label}.realpath")), label, executable=True)
    if sha256_file(path) != require_hex64(identity["sha256"], f"{label}.sha256"):
        fail(f"recorded {label} binary SHA-256 no longer matches")
    require_string(identity["version_stdout"], f"{label}.version_stdout")
    if not isinstance(identity["version_stderr"], str):
        fail(f"{label}.version_stderr must be a string")


def validate_sample_source(value: object) -> None:
    source = require_object(value, "sample source")
    kind = require_string(source.get("kind"), "sample source kind")
    if kind == "external":
        require_exact_fields(source, SAMPLE_EXTERNAL_SOURCE_FIELDS, "sample source")
        return
    if kind != "llvm-profgen":
        fail(f"unsupported sample source kind: {kind}")
    require_exact_fields(source, SAMPLE_PROFGEN_SOURCE_FIELDS, "sample source")
    validate_recorded_file(source["binary_path"], source["binary_sha256"], "binary")
    validate_recorded_file(source["perf_data_path"], source["perf_data_sha256"], "perf_data")
    if source["debug_binary_path"] is None and source["debug_binary_sha256"] is None:
        pass
    elif source["debug_binary_path"] is not None and source["debug_binary_sha256"] is not None:
        validate_recorded_file(
            source["debug_binary_path"], source["debug_binary_sha256"], "debug_binary"
        )
    else:
        fail("sample debug binary path and SHA-256 must both be null or both be set")
    for name in ("producer", "readelf", "objcopy"):
        validate_recorded_tool_identity(source[name], name)
    arguments = source["command_arguments"]
    if not isinstance(arguments, list) or not arguments or not all(
        isinstance(item, str) and item for item in arguments
    ):
        fail("sample source command_arguments must be a nonempty string array")
    require_hex64(source["command_output_sha256"], "command_output_sha256")


def validate_sample_metadata(
    metadata_path: Path,
    profile: Path,
    profile_sha256: str,
    fingerprint: str,
    abi: str,
    compiler_major: int,
    tool_identity: dict[str, object],
    sample_stdout: str,
    sample_stderr: str,
) -> None:
    metadata = load_json(metadata_path, "sample metadata")
    require_exact_fields(metadata, SAMPLE_METADATA_FIELDS, "sample metadata")
    if metadata["schema_version"] != 1:
        fail("sample metadata schema_version must be 1")
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
    if package["fingerprint"] != fingerprint or package["abi"] != abi:
        fail("sample metadata package fingerprint or ABI mismatch")
    require_string(package["cpv"], "sample package CPV")

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
    validate_sample_source(metadata["source"])


def validate_sample_profile(
    profile: Path,
    tool: Path,
    metadata_path: Path,
    profile_sha256: str,
    fingerprint: str,
    abi: str,
    compiler_major: int,
    tool_identity: dict[str, object],
) -> None:
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
    validate_sample_metadata(
        metadata_path,
        profile,
        profile_sha256,
        fingerprint,
        abi,
        compiler_major,
        tool_identity,
        recorded_stdout,
        recorded_stderr,
    )


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


def validate_gcc_profile(profile: Path, tool: Path) -> str:
    profile_hash, files = gcc_directory_hash(profile)
    stdout, _stderr = run_tool(
        tool,
        ["overlap", "-f", os.fspath(profile), os.fspath(profile)],
        "GCC gcov profile validation",
    )
    gcda_match = re.search(r"(?m)^\s*gcda files:\s*([0-9]+)\s+", stdout)
    hot_match = re.search(r"(?m)^\s*hot files:\s*([0-9]+)\s+", stdout)
    if gcda_match is None or int(gcda_match.group(1)) != len(files):
        fail("gcov-tool did not read the complete .gcda set")
    if hot_match is None or int(hot_match.group(1)) < 1:
        fail("GCC profile contains no nonzero hot-file data")
    return profile_hash


def read_gnu_build_id(readelf: Path, binary: Path) -> str | None:
    stdout, _stderr = run_tool(readelf, ["-n", os.fspath(binary)], "GNU build-ID inspection")
    matches = re.findall(r"(?im)^\s*Build ID:\s*([0-9a-f]+)\s*$", stdout)
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
    target_package: str,
    target_symbols: list[str],
) -> None:
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
    locations: dict[str, tuple[int, int, int]] = {}
    for match in location_pattern.finditer(raw):
        location_id = int(match.group(1))
        mapping_id = int(match.group(2))
        symbol = match.group(3)
        start_line = int(match.group(4))
        locations.setdefault(symbol, (location_id, mapping_id, start_line))
    if not locations:
        fail("Go profile contains no readable function metadata")
    prefix = target_package + "."
    if not any(symbol.startswith(prefix) for symbol in locations):
        fail("Go profile contains no function from the declared target package")
    for symbol in target_symbols:
        record = locations.get(symbol)
        if record is None:
            fail(f"Go profile does not contain target symbol: {symbol}")
        location_id, _mapping_id, start_line = record
        if start_line < 1 or location_id not in sampled_locations:
            fail(f"Go target symbol lacks positive function metadata or samples: {symbol}")

    mapping_pattern = re.compile(
        r"^([0-9]+):\s+0x[0-9a-f]+/0x[0-9a-f]+/0x[0-9a-f]+\s+(\S+)\s+(\S*)\s+\[FN\]\s*$",
        re.MULTILINE,
    )
    mappings = {
        int(match.group(1)): (match.group(2), match.group(3))
        for match in mapping_pattern.finditer(raw)
    }
    target_mapping_ids = {locations[symbol][1] for symbol in target_symbols}
    if len(target_mapping_ids) != 1:
        fail("Go target symbols do not share one exact executable mapping")
    mapping = mappings.get(next(iter(target_mapping_ids)))
    if mapping is None:
        fail("Go target mapping is missing or lacks function metadata")
    mapping_path, mapping_build_id = mapping
    if gnu_build_id is not None:
        if mapping_build_id != gnu_build_id:
            fail("Go profile mapping GNU build ID does not match the profiled binary")
    elif mapping_path != os.fspath(binary):
        fail("Go profile without a GNU build ID does not map the exact profiled binary path")


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
) -> None:
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
    parse_go_raw_profile(raw, binary, gnu_build_id, target_package, target_symbols)


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


def atomic_publish(path: Path, payload: bytes) -> None:
    path = safe_absolute_path(path, "manifest output", must_exist=False)
    parent = directory_input(path.parent, "manifest output parent")
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        fail(f"cannot inspect manifest output {path}: {error}")
    else:
        fail(f"manifest output already exists: {path}")
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.partial.", dir=parent
        )
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        directory_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        fail(f"cannot publish manifest {path}: {error}")
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def validate_arguments(arguments: argparse.Namespace) -> None:
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
    elif arguments.sample_metadata is not None:
        fail("--sample-metadata is valid only for the Clang sample backend")

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
                not SAFE_GO_COMPONENT_RE.fullmatch(symbol)
                or not symbol.startswith(prefix)
            ):
                fail(f"Go target symbol is outside the target package: {symbol}")
        require_string(arguments.go_build_id, "Go build ID")
    elif any(value is not None for value in go_values) or arguments.go_target_symbol:
        fail("Go-specific arguments are valid only for the Go backend")


def command_validate(arguments: argparse.Namespace) -> int:
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
        profile_sha256 = validate_gcc_profile(profile, Path(str(profile_tool["path"])))
    else:
        profile_sha256 = sha256_file(profile)
        if arguments.backend in {"clang-ir", "rust"}:
            validate_indexed_profile(profile, Path(str(profile_tool["path"])), arguments.backend)
        elif arguments.backend == "clang-sample":
            validate_sample_profile(
                profile,
                Path(str(profile_tool["path"])),
                arguments.sample_metadata,
                profile_sha256,
                arguments.fingerprint,
                arguments.abi,
                arguments.compiler_major,
                profile_tool,
            )
        else:
            validate_go_profile(
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
    atomic_publish(
        arguments.manifest_out,
        manifest_bytes(
            arguments.backend,
            arguments.fingerprint,
            arguments.abi,
            arguments.compiler_family,
            profile,
            profile_sha256,
        ),
    )
    return 0


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument("--rust-llvm-major", type=int)
    parser.add_argument("--sample-metadata", type=Path)
    parser.add_argument("--go-binary", type=Path)
    parser.add_argument("--go-binary-sha256")
    parser.add_argument("--go-build-id")
    parser.add_argument("--go-target-package")
    parser.add_argument("--go-target-symbol", action="append", default=[])
    parser.add_argument("--readelf", type=Path)
    parser.add_argument("--readelf-sha256")
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
        return command_validate(arguments)
    except ProfileValidationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        for item, handler in previous_handlers.items():
            signal.signal(item, handler)


if __name__ == "__main__":
    raise SystemExit(main())

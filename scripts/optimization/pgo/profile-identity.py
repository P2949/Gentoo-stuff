#!/usr/bin/env python3
"""Create exact PGO package identities and validate Clang sample profiles.

Fingerprint output deliberately contains no timestamps.  A fingerprint is the SHA-256
of a canonical JSON document containing the complete, validated build identity.
Compiler identity is observed by this program rather than trusted from the
input manifest.
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


SCHEMA_VERSION = 2
BUFFER_SIZE = 1024 * 1024
MAX_INPUT_SIZE = 4 * 1024 * 1024
MAX_TOOL_OUTPUT = 1024 * 1024
TOOL_TIMEOUT_SECONDS = 15

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_RE = re.compile(r"^[0-9a-f]{8,128}$")
COMPONENT_RE = re.compile(r"^[A-Za-z0-9+_.@-]+$")
CPV_RE = re.compile(
    r"^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+-[0-9][A-Za-z0-9+_.-]*(?:-r[0-9]+)?$"
)
PROFILE_FORMAT_PREFIX = {
    "clang": "llvm-",
    "gcc": "gcc-",
    "rustc": "rust-llvm-",
    "go": "go-pprof-",
}

FINGERPRINT_FIELDS = {
    "schema_version",
    "category",
    "pf",
    "slot",
    "subslot",
    "repository",
    "ebuild_sha256",
    "eapi",
    "chost",
    "abi",
    "compiler",
    "use_flags",
    "cflags",
    "cxxflags",
    "ldflags",
    "rustflags",
    "goflags",
    "features",
    "package_env_files",
    "extra_econf",
    "extra_emeson",
    "extra_ecmake",
    "kernel_module",
    "kernel_release",
    "rust_target_triple",
    "rustc_llvm_version",
}
COMPILER_FIELDS = {"path", "family", "major", "profile_format"}
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
SAMPLE_SOURCE_PROFGEN_FIELDS = {
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
    "binary_observation",
    "debug_binary_observation",
    "perf_data_observation",
}
LLVM_TOOL_IDENTITY_FIELDS = {
    "realpath",
    "sha256",
    "version_stderr",
    "version_stdout",
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
    "workload_revision",
    "source_identity_sha256",
    "production_host",
    "production_date",
}
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class IdentityError(Exception):
    """An identity input or profile failed closed validation."""


def fail(message: str) -> NoReturn:
    raise IdentityError(message)


def require_exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        fail(f"{label} has invalid fields: {'; '.join(details)}")


def require_string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        fail(f"{label} must be a string")
    if "\x00" in value:
        fail(f"{label} contains a NUL byte")
    if not allow_empty and not value:
        fail(f"{label} must not be empty")
    return value


def require_component(value: object, label: str) -> str:
    text = require_string(value, label)
    if not COMPONENT_RE.fullmatch(text) or text in {".", ".."}:
        fail(f"{label} is not a safe path component: {text!r}")
    return text


def require_abi(value: object, label: str = "abi") -> str:
    abi = require_string(value, label)
    if abi not in {"amd64", "x86"}:
        fail(f"{label} must be exactly 'amd64' or 'x86'")
    return abi


def require_positive_major(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        fail(f"{label} must be a positive integer")
    return value


def require_hex64(value: object, label: str) -> str:
    text = require_string(value, label).lower()
    if not HEX64_RE.fullmatch(text):
        fail(f"{label} must be exactly 64 hexadecimal characters")
    return text


def require_build_id(value: object, label: str = "build_id") -> str:
    text = require_string(value, label).lower()
    if not BUILD_ID_RE.fullmatch(text) or len(text) % 2:
        fail(f"{label} must be an even-length 8..128 character hexadecimal value")
    return text


def require_cpv(value: object, label: str = "cpv") -> str:
    text = require_string(value, label)
    if not CPV_RE.fullmatch(text):
        fail(f"{label} is not an exact Gentoo CPV: {text!r}")
    return text


def require_string_list(
    value: object, label: str, *, sort_values: bool
) -> list[str]:
    if not isinstance(value, list):
        fail(f"{label} must be a JSON array")
    result = [require_string(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        fail(f"{label} contains duplicate values")
    if sort_values:
        result.sort()
    return result


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        path_stat = path.stat()
    except OSError as exc:
        fail(f"cannot stat {label} {path}: {exc}")
    if not stat.S_ISREG(path_stat.st_mode):
        fail(f"{label} is not a regular file: {path}")
    if path_stat.st_size > MAX_INPUT_SIZE:
        fail(f"{label} exceeds {MAX_INPUT_SIZE} bytes: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"cannot read {label} {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"{label} must contain one JSON object")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(BUFFER_SIZE):
                digest.update(chunk)
    except OSError as exc:
        fail(f"cannot hash {path}: {exc}")
    return digest.hexdigest()


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def reject_symlink_traversal(path: Path, label: str, *, include_leaf: bool) -> None:
    if not path.is_absolute() or path == Path("/") or ".." in path.parts:
        fail(f"{label} must be a non-root absolute path without '..' components")
    limit = len(path.parts) if include_leaf else len(path.parts) - 1
    current = Path(path.anchor)
    for part in path.parts[1:limit]:
        current /= part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            fail(f"cannot inspect {label} component {current}: {exc}")
        if stat.S_ISLNK(current_stat.st_mode):
            fail(f"{label} traverses a symlink component: {current}")
        if current != path and not stat.S_ISDIR(current_stat.st_mode):
            fail(f"{label} traverses a non-directory component: {current}")


def validate_output_destination(path: Path) -> None:
    reject_symlink_traversal(path, "output path", include_leaf=True)
    try:
        output_stat = path.lstat()
    except FileNotFoundError:
        output_stat = None
    except OSError as exc:
        fail(f"cannot inspect output path {path}: {exc}")
    if output_stat is not None and not stat.S_ISREG(output_stat.st_mode):
        fail(f"output path exists and is not a regular file: {path}")


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    validate_output_destination(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".partial", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
            directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary_path.unlink(missing_ok=True)
            raise
    except IdentityError:
        raise
    except OSError as exc:
        fail(f"cannot atomically write {path}: {exc}")


def run_tool(path: Path, arguments: list[str], label: str) -> tuple[str, str]:
    try:
        completed = subprocess.run(
            [os.fspath(path), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=TOOL_TIMEOUT_SECONDS,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f"cannot execute {label} {path}: {exc}")
    if len(completed.stdout) > MAX_TOOL_OUTPUT or len(completed.stderr) > MAX_TOOL_OUTPUT:
        fail(f"{label} output exceeds {MAX_TOOL_OUTPUT} bytes")
    try:
        stdout = completed.stdout.decode("utf-8", errors="strict")
        stderr = completed.stderr.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        fail(f"{label} output is not UTF-8: {exc}")
    if completed.returncode != 0:
        diagnostic = (stderr or stdout).strip().replace("\n", " ")[:400]
        fail(f"{label} exited {completed.returncode}: {diagnostic}")
    return stdout, stderr


def terminate_process_group(process: subprocess.Popen[bytes], kill_after: float) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=kill_after)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=kill_after)
    except subprocess.TimeoutExpired:
        fail("converter process group survived SIGKILL")


def run_bounded_tool(
    path: Path,
    arguments: list[str],
    label: str,
    timeout_seconds: float,
    kill_after_seconds: float,
) -> tuple[str, str]:
    try:
        process = subprocess.Popen(
            [os.fspath(path), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
        )
    except OSError as exc:
        fail(f"cannot execute {label} {path}: {exc}")
    try:
        try:
            stdout_bytes, stderr_bytes = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            terminate_process_group(process, kill_after_seconds)
            fail(f"{label} timed out after {timeout_seconds:g} seconds")
    except BaseException:
        terminate_process_group(process, kill_after_seconds)
        raise
    if len(stdout_bytes) > MAX_TOOL_OUTPUT or len(stderr_bytes) > MAX_TOOL_OUTPUT:
        fail(f"{label} output exceeds {MAX_TOOL_OUTPUT} bytes")
    try:
        stdout = stdout_bytes.decode("utf-8", errors="strict")
        stderr = stderr_bytes.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        fail(f"{label} output is not UTF-8: {exc}")
    if process.returncode != 0:
        diagnostic = (stderr or stdout).strip().replace("\n", " ")[:400]
        fail(f"{label} exited {process.returncode}: {diagnostic}")
    return stdout, stderr


def canonical_regular_input(path: Path, label: str, *, nonempty: bool = True) -> Path:
    if not path.is_absolute():
        fail(f"{label} must be an absolute path")
    try:
        canonical = path.resolve(strict=True)
        path_stat = canonical.stat()
    except OSError as exc:
        fail(f"cannot resolve {label} {path}: {exc}")
    if not stat.S_ISREG(path_stat.st_mode):
        fail(f"{label} does not resolve to a regular file: {path}")
    if nonempty and path_stat.st_size == 0:
        fail(f"{label} is empty: {path}")
    return canonical


def observe_regular_file(path: Path, label: str) -> dict[str, object]:
    """Capture content and inode metadata used for exact before/after proof."""
    try:
        path_stat = path.stat()
    except OSError as exc:
        fail(f"cannot stat {label} {path}: {exc}")
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


def require_unchanged_file(
    path: Path, before: dict[str, object], label: str
) -> dict[str, object]:
    after = observe_regular_file(path, label)
    if after != before:
        fail(f"{label} changed during sample conversion")
    return after


def validate_file_observation(
    value: object, path: Path, label: str
) -> dict[str, object]:
    if not isinstance(value, dict):
        fail(f"{label} observation must be a JSON object")
    require_exact_fields(value, FILE_OBSERVATION_FIELDS, f"{label} observation")
    observed = observe_regular_file(path, label)
    if value != observed:
        fail(f"{label} no longer matches its exact recorded observation")
    return observed


def inspect_executable(path_value: object, label: str) -> tuple[Path, Path, str]:
    requested_text = require_string(path_value, f"{label}.path")
    requested = Path(requested_text)
    if not requested.is_absolute():
        fail(f"{label}.path must be absolute")
    try:
        realpath = requested.resolve(strict=True)
        path_stat = realpath.stat()
    except OSError as exc:
        fail(f"cannot resolve {label}.path {requested}: {exc}")
    if not stat.S_ISREG(path_stat.st_mode) or not os.access(realpath, os.X_OK):
        fail(f"{label}.path does not resolve to an executable regular file: {requested}")
    return requested, realpath, sha256_file(realpath)


def compiler_version_arguments(family: str) -> list[str]:
    if family == "rustc":
        return ["--version", "--verbose"]
    if family == "go":
        return ["version"]
    return ["--version"]


def detect_compiler_major(family: str, output: str) -> int:
    patterns = {
        "clang": r"(?im)^.*?clang version\s+([0-9]+)(?:\.|\s)",
        "gcc": r"(?im)^.*?(?:gcc|g\+\+)\b[^\n]*?\s([0-9]+)(?:\.|\s)",
        "rustc": r"(?im)^rustc\s+([0-9]+)(?:\.|\s)",
        "go": r"(?im)^go version go([0-9]+)(?:\.|\s)",
    }
    match = re.search(patterns[family], output)
    if match is None:
        fail(f"compiler output does not identify the requested {family} family and major")
    return int(match.group(1))


def inspect_compiler(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        fail("compiler must be a JSON object")
    require_exact_fields(value, COMPILER_FIELDS, "compiler")
    family = require_string(value["family"], "compiler.family")
    if family not in PROFILE_FORMAT_PREFIX:
        fail(f"compiler.family is unsupported: {family!r}")
    major = require_positive_major(value["major"], "compiler.major")
    profile_format = require_component(value["profile_format"], "compiler.profile_format")
    if not profile_format.startswith(PROFILE_FORMAT_PREFIX[family]):
        fail(
            f"compiler.profile_format {profile_format!r} is incompatible with {family}"
        )
    _requested, realpath, binary_sha256 = inspect_executable(value["path"], "compiler")
    version_arguments = compiler_version_arguments(family)
    stdout, stderr = run_tool(realpath, version_arguments, "compiler version command")
    observed_major = detect_compiler_major(family, stdout + "\n" + stderr)
    if observed_major != major:
        fail(
            f"compiler.major is {major}, but the active compiler reports {observed_major}"
        )
    identity: dict[str, object] = {
        "family": family,
        "major": major,
        "profile_format": profile_format,
        "realpath": os.fspath(realpath),
        "sha256": binary_sha256,
        "version_arguments": version_arguments,
        "version_stderr": stderr,
        "version_stdout": stdout,
    }
    if family == "rustc":
        host_match = re.search(r"(?im)^host:\s*(\S+)\s*$", stdout + "\n" + stderr)
        llvm_match = re.search(
            r"(?im)^LLVM version:\s*([0-9]+(?:\.[0-9]+)+)\s*$",
            stdout + "\n" + stderr,
        )
        if host_match is None or llvm_match is None:
            fail("rustc verbose version output lacks exact host or bundled LLVM version")
        identity["rustc_host_triple"] = require_component(
            host_match.group(1), "compiler.rustc_host_triple"
        )
        identity["rustc_llvm_version"] = require_component(
            llvm_match.group(1), "compiler.rustc_llvm_version"
        )
    return identity


def validate_rust_identity_axes(
    input_data: dict[str, Any], compiler: dict[str, object]
) -> tuple[str | None, str | None]:
    target_value = input_data["rust_target_triple"]
    llvm_value = input_data["rustc_llvm_version"]
    if compiler["family"] != "rustc":
        if target_value is not None or llvm_value is not None:
            fail("rust_target_triple and rustc_llvm_version must be null outside rustc")
        return None, None

    target = require_component(target_value, "rust_target_triple")
    llvm_version = require_component(llvm_value, "rustc_llvm_version")
    if compiler.get("rustc_llvm_version") != llvm_version:
        fail("rustc_llvm_version does not match the observed bundled LLVM version")
    target_stdout, _target_stderr = run_tool(
        Path(require_string(compiler["realpath"], "compiler.realpath")),
        ["--print", "target-list"],
        "rustc target-list command",
    )
    if target not in target_stdout.splitlines():
        fail(f"rust_target_triple is not supported by the exact rustc: {target}")
    return target, llvm_version


def validate_package_env_files(value: object) -> list[str]:
    result = require_string_list(value, "package_env_files", sort_values=False)
    for index, item in enumerate(result):
        path = Path(item)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            fail(f"package_env_files[{index}] is not a safe relative path: {item!r}")
    return result


def build_fingerprint_identity(input_data: dict[str, Any]) -> dict[str, object]:
    require_exact_fields(input_data, FINGERPRINT_FIELDS, "fingerprint input")
    if input_data["schema_version"] != SCHEMA_VERSION:
        fail(f"unsupported fingerprint schema_version: {input_data['schema_version']!r}")

    category = require_component(input_data["category"], "category")
    pf = require_component(input_data["pf"], "pf")
    if not re.search(r"-[0-9]", pf):
        fail("pf must include an exact package version")
    slot = require_component(input_data["slot"], "slot")
    subslot = require_component(input_data["subslot"], "subslot")
    repository = require_component(input_data["repository"], "repository")
    ebuild_sha256 = require_hex64(input_data["ebuild_sha256"], "ebuild_sha256")
    eapi = require_component(input_data["eapi"], "eapi")
    chost = require_component(input_data["chost"], "chost")
    abi = require_abi(input_data["abi"])

    kernel_module = input_data["kernel_module"]
    if not isinstance(kernel_module, bool):
        fail("kernel_module must be a boolean")
    kernel_release_value = input_data["kernel_release"]
    if kernel_module:
        kernel_release: str | None = require_component(
            kernel_release_value, "kernel_release"
        )
    else:
        if kernel_release_value is not None:
            fail("kernel_release must be null for a non-kernel-module package")
        kernel_release = None

    compiler = inspect_compiler(input_data["compiler"])
    rust_target_triple, rustc_llvm_version = validate_rust_identity_axes(
        input_data, compiler
    )

    return {
        "abi": abi,
        "category": category,
        "chost": chost,
        "compiler": compiler,
        "eapi": eapi,
        "ebuild_sha256": ebuild_sha256,
        "extra_ecmake": require_string(
            input_data["extra_ecmake"], "extra_ecmake", allow_empty=True
        ),
        "extra_econf": require_string(
            input_data["extra_econf"], "extra_econf", allow_empty=True
        ),
        "extra_emeson": require_string(
            input_data["extra_emeson"], "extra_emeson", allow_empty=True
        ),
        "features": require_string_list(
            input_data["features"], "features", sort_values=True
        ),
        "flags": {
            "cflags": require_string(input_data["cflags"], "cflags", allow_empty=True),
            "cxxflags": require_string(
                input_data["cxxflags"], "cxxflags", allow_empty=True
            ),
            "goflags": require_string(
                input_data["goflags"], "goflags", allow_empty=True
            ),
            "ldflags": require_string(
                input_data["ldflags"], "ldflags", allow_empty=True
            ),
            "rustflags": require_string(
                input_data["rustflags"], "rustflags", allow_empty=True
            ),
        },
        "kernel_module": kernel_module,
        "kernel_release": kernel_release,
        "package_env_files": validate_package_env_files(input_data["package_env_files"]),
        "pf": pf,
        "repository": repository,
        "rust_target_triple": rust_target_triple,
        "rustc_llvm_version": rustc_llvm_version,
        "schema_version": SCHEMA_VERSION,
        "slot": slot,
        "subslot": subslot,
        "use_flags": require_string_list(
            input_data["use_flags"], "use_flags", sort_values=True
        ),
    }


def fingerprint_command(arguments: argparse.Namespace) -> int:
    input_data = load_json_object(arguments.input, "fingerprint input")
    identity = build_fingerprint_identity(input_data)
    fingerprint = hashlib.sha256(canonical_bytes(identity)).hexdigest()
    metadata = {
        "canonical_identity": identity,
        "fingerprint": fingerprint,
        "fingerprint_algorithm": "sha256",
        "fingerprint_id": f"sha256:{fingerprint}",
        "schema_version": SCHEMA_VERSION,
    }
    destinations = [
        path
        for path in (arguments.metadata_out, arguments.key_out)
        if path is not None
    ]
    if len({os.path.normpath(path) for path in destinations}) != len(destinations):
        fail("--metadata-out and --key-out must be distinct paths")
    for destination in destinations:
        validate_output_destination(destination)
    if arguments.metadata_out is not None:
        atomic_write(
            arguments.metadata_out,
            (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
    if arguments.key_out is not None:
        atomic_write(arguments.key_out, f"fingerprint={fingerprint}\n".encode("ascii"))
    print(fingerprint)
    return 0


def required(arguments: argparse.Namespace, name: str, family: str) -> str:
    value = getattr(arguments, name)
    if value is None:
        fail(f"--{name.replace('_', '-')} is required for {family}")
    return require_string(value, name)


def reject_unused(arguments: argparse.Namespace, allowed: set[str], family: str) -> None:
    common = {"command", "family", "root", "func"}
    for name, value in vars(arguments).items():
        if name in common | allowed:
            continue
        if value is not None:
            fail(f"--{name.replace('_', '-')} is not valid for {family}")


def profile_path_command(arguments: argparse.Namespace) -> int:
    root = arguments.root
    reject_symlink_traversal(root, "--root", include_leaf=True)
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        root_stat = None
    except OSError as exc:
        fail(f"cannot inspect --root {root}: {exc}")
    if root_stat is not None and not stat.S_ISDIR(root_stat.st_mode):
        fail(f"--root exists and is not a directory: {root}")
    family = arguments.family
    path: Path
    if family == "clang-ir":
        allowed = {"compiler_major", "generation", "abi"}
        reject_unused(arguments, allowed, family)
        major = require_positive_major(arguments.compiler_major, "compiler_major")
        path = root / family / str(major) / require_component(
            required(arguments, "generation", family), "generation"
        ) / require_abi(required(arguments, "abi", family)) / "merged.profdata"
    elif family == "rust":
        allowed = {
            "language_version",
            "compiler_major",
            "generation",
            "abi",
            "target_triple",
            "rustc_llvm_version",
        }
        reject_unused(arguments, allowed, family)
        major = require_positive_major(arguments.compiler_major, "compiler_major")
        path = (
            root
            / family
            / require_component(required(arguments, "language_version", family), "language_version")
            / str(major)
            / require_component(
                required(arguments, "rustc_llvm_version", family),
                "rustc_llvm_version",
            )
            / require_component(
                required(arguments, "target_triple", family), "target_triple"
            )
            / require_component(required(arguments, "generation", family), "generation")
            / require_abi(required(arguments, "abi", family))
            / "merged.profdata"
        )
    elif family == "gcc":
        allowed = {"compiler_major", "cpv", "fingerprint", "abi"}
        reject_unused(arguments, allowed, family)
        major = require_positive_major(arguments.compiler_major, "compiler_major")
        path = (
            root
            / family
            / str(major)
            / require_cpv(required(arguments, "cpv", family))
            / require_hex64(required(arguments, "fingerprint", family), "fingerprint")
            / require_abi(required(arguments, "abi", family))
        )
    elif family == "go":
        allowed = {"language_version", "cpv", "fingerprint", "binary"}
        reject_unused(arguments, allowed, family)
        path = (
            root
            / family
            / require_component(required(arguments, "language_version", family), "language_version")
            / require_cpv(required(arguments, "cpv", family))
            / require_hex64(required(arguments, "fingerprint", family), "fingerprint")
            / require_component(required(arguments, "binary", family), "binary")
            / "default.pgo"
        )
    elif family == "clang-sample":
        allowed = {"compiler_major", "cpv", "fingerprint", "build_id"}
        reject_unused(arguments, allowed, family)
        major = require_positive_major(arguments.compiler_major, "compiler_major")
        path = (
            root
            / family
            / str(major)
            / require_cpv(required(arguments, "cpv", family))
            / require_hex64(required(arguments, "fingerprint", family), "fingerprint")
            / require_build_id(required(arguments, "build_id", family))
            / "sample.prof"
        )
    elif family == "kernel":
        allowed = {"kernel_release", "config_hash"}
        reject_unused(arguments, allowed, family)
        path = (
            root
            / family
            / require_component(required(arguments, "kernel_release", family), "kernel_release")
            / require_hex64(required(arguments, "config_hash", family), "config_hash")
        )
    else:  # pragma: no cover - argparse enforces choices
        fail(f"unsupported profile family: {family}")
    print(path)
    return 0


def inspect_llvm_tool(
    path_value: Path, clang_major: int, label: str
) -> dict[str, object]:
    _requested, realpath, binary_sha256 = inspect_executable(
        os.fspath(path_value), label
    )
    stdout, stderr = run_tool(realpath, ["--version"], f"{label} version command")
    match = re.search(r"(?im)LLVM version\s+([0-9]+)(?:\.|\s)", stdout + "\n" + stderr)
    if match is None:
        fail(f"{label} output does not contain an LLVM major")
    observed_major = int(match.group(1))
    if observed_major != clang_major:
        fail(
            f"{label} major {observed_major} does not match Clang major {clang_major}"
        )
    return {
        "realpath": os.fspath(realpath),
        "sha256": binary_sha256,
        "version_stderr": stderr,
        "version_stdout": stdout,
    }


def inspect_llvm_profdata(path_value: Path, clang_major: int) -> dict[str, object]:
    return inspect_llvm_tool(path_value, clang_major, "llvm-profdata")


def validate_sample_file(
    profile: Path, llvm_profdata: Path, *, allow_transaction_partial: bool = False
) -> dict[str, object]:
    if not profile.is_absolute():
        fail("sample profile path must be absolute")
    allowed_names = {"sample.prof"}
    if allow_transaction_partial:
        allowed_names.add("sample.prof.partial")
    if profile.name not in allowed_names:
        fail("Clang sample profile must be named sample.prof")
    try:
        profile_stat = profile.lstat()
    except OSError as exc:
        fail(f"cannot stat sample profile {profile}: {exc}")
    if not stat.S_ISREG(profile_stat.st_mode) or profile_stat.st_size == 0:
        fail(f"sample profile is missing, empty, or not a regular file: {profile}")
    stdout, stderr = run_tool(
        llvm_profdata, ["show", "--sample", os.fspath(profile)], "sample-profile validation"
    )
    if not stdout.strip():
        fail("sample-aware llvm-profdata validation returned no profile description")
    return {
        "command_arguments": ["show", "--sample", os.fspath(profile)],
        "output_sha256": hashlib.sha256((stdout + "\0" + stderr).encode("utf-8")).hexdigest(),
    }


def build_reproducibility(arguments: argparse.Namespace) -> dict[str, object]:
    production_date = require_string(arguments.production_date, "production_date")
    if not DATE_RE.fullmatch(production_date):
        fail("production_date must use exact YYYY-MM-DD format")
    try:
        date.fromisoformat(production_date)
    except ValueError:
        fail("production_date is not a valid calendar date")
    return {
        "optimization_generation_id": require_component(
            arguments.optimization_generation_id, "optimization_generation_id"
        ),
        "production_date": production_date,
        "production_host": require_component(
            arguments.production_host, "production_host"
        ),
        "source_identity_sha256": require_hex64(
            arguments.source_identity_sha256, "source_identity_sha256"
        ),
        "workload_revision": require_component(
            arguments.workload_revision, "workload_revision"
        ),
    }


def validate_recorded_reproducibility(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        fail("reproducibility metadata must be a JSON object")
    require_exact_fields(value, REPRODUCIBILITY_FIELDS, "reproducibility metadata")
    return value


def sample_identity(
    arguments: argparse.Namespace, source: dict[str, object]
) -> dict[str, object]:
    cpv = require_cpv(arguments.cpv)
    fingerprint = require_hex64(arguments.fingerprint, "fingerprint")
    abi = require_abi(arguments.abi)
    clang_major = require_positive_major(arguments.clang_major, "clang_major")
    build_id = require_build_id(arguments.build_id)
    text_sha256 = require_hex64(arguments.text_sha256, "text_sha256")
    profile = arguments.profile
    validator = inspect_llvm_profdata(arguments.llvm_profdata, clang_major)
    validation = validate_sample_file(profile, Path(str(validator["realpath"])))
    return {
        "compiler": {"family": "clang", "major": clang_major},
        "input_identity": {"build_id": build_id, "text_sha256": text_sha256},
        "package": {"abi": abi, "cpv": cpv, "fingerprint": fingerprint},
        "profile_family": "clang-sample",
        "profile_format": "llvm-sample",
        "profile_path": os.fspath(profile.resolve(strict=True)),
        "profile_sha256": sha256_file(profile),
        "profile_size": profile.stat().st_size,
        "reproducibility": build_reproducibility(arguments),
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "validation": validation,
        "validator": validator,
    }


def validate_tool_identity(
    value: object, clang_major: int, label: str
) -> dict[str, object]:
    if not isinstance(value, dict):
        fail(f"{label} identity must be a JSON object")
    require_exact_fields(value, LLVM_TOOL_IDENTITY_FIELDS, f"{label} identity")
    realpath_text = require_string(value["realpath"], f"{label}.realpath")
    realpath = Path(realpath_text)
    if not realpath.is_absolute() or os.fspath(realpath.resolve(strict=True)) != realpath_text:
        fail(f"{label}.realpath is not an exact canonical absolute path")
    observed = inspect_llvm_tool(realpath, clang_major, label)
    if value != observed:
        fail(f"{label} identity no longer matches its recorded tool")
    return observed


def validate_recorded_source_file(
    path_value: object, sha_value: object, label: str
) -> tuple[str, str]:
    path_text = require_string(path_value, f"{label}_path")
    path = canonical_regular_input(Path(path_text), label)
    if os.fspath(path) != path_text:
        fail(f"{label}_path is not canonical")
    expected_sha = require_hex64(sha_value, f"{label}_sha256")
    if sha256_file(path) != expected_sha:
        fail(f"{label} content no longer matches its recorded SHA-256")
    return path_text, expected_sha


def validate_sample_source(
    value: object, clang_major: int, profile: Path | None = None
) -> dict[str, object]:
    if not isinstance(value, dict):
        fail("sample profile source must be a JSON object")
    kind = require_string(value.get("kind"), "sample source kind")
    if kind != "llvm-profgen":
        fail(f"unsupported sample profile source kind: {kind!r}")
    require_exact_fields(value, SAMPLE_SOURCE_PROFGEN_FIELDS, "sample source")
    binary_path, binary_sha256 = validate_recorded_source_file(
        value["binary_path"], value["binary_sha256"], "binary"
    )
    binary_observation = validate_file_observation(
        value["binary_observation"], Path(binary_path), "binary"
    )
    perf_path, perf_sha256 = validate_recorded_source_file(
        value["perf_data_path"], value["perf_data_sha256"], "perf_data"
    )
    perf_data_observation = validate_file_observation(
        value["perf_data_observation"], Path(perf_path), "perf data"
    )
    debug_path_value = value["debug_binary_path"]
    debug_sha_value = value["debug_binary_sha256"]
    debug_path: str | None
    debug_sha256: str | None
    if debug_path_value is None and debug_sha_value is None:
        debug_path = None
        debug_sha256 = None
        if value["debug_binary_observation"] is not None:
            fail("debug binary observation must be null when no debug binary is set")
        debug_binary_observation = None
    elif debug_path_value is not None and debug_sha_value is not None:
        debug_path, debug_sha256 = validate_recorded_source_file(
            debug_path_value, debug_sha_value, "debug_binary"
        )
        debug_binary_observation = validate_file_observation(
            value["debug_binary_observation"], Path(debug_path), "debug binary"
        )
    else:
        fail("debug binary path and SHA-256 must either both be null or both be set")

    producer = validate_tool_identity(value["producer"], clang_major, "llvm-profgen")
    readelf = validate_tool_identity(value["readelf"], clang_major, "llvm-readelf")
    objcopy = validate_tool_identity(value["objcopy"], clang_major, "llvm-objcopy")
    command_arguments = require_string_list(
        value["command_arguments"], "sample source command_arguments", sort_values=False
    )
    expected_arguments = [f"--binary={binary_path}"]
    if debug_path is not None:
        expected_arguments.append(f"--debug-binary={debug_path}")
    expected_arguments.extend(
        [
            f"--perfdata={perf_path}",
            "--format=extbinary",
            "--show-detailed-warning",
        ]
    )
    if profile is not None:
        expected_arguments.append(f"--output={profile}.partial")
        if command_arguments != expected_arguments:
            fail("recorded llvm-profgen command does not match the exact profile inputs")
    command_output_sha256 = require_hex64(
        value["command_output_sha256"], "command_output_sha256"
    )
    return {
        "binary_path": binary_path,
        "binary_sha256": binary_sha256,
        "binary_observation": binary_observation,
        "command_arguments": command_arguments,
        "command_output_sha256": command_output_sha256,
        "debug_binary_path": debug_path,
        "debug_binary_sha256": debug_sha256,
        "debug_binary_observation": debug_binary_observation,
        "kind": "llvm-profgen",
        "objcopy": objcopy,
        "perf_data_path": perf_path,
        "perf_data_sha256": perf_sha256,
        "perf_data_observation": perf_data_observation,
        "producer": producer,
        "readelf": readelf,
    }


def extract_binary_identity(
    binary: Path, readelf: Path, objcopy: Path
) -> tuple[str, str]:
    try:
        original_stat = binary.stat()
    except OSError as exc:
        fail(f"cannot stat profiled binary before identity extraction: {exc}")
    original_identity = (
        original_stat.st_dev,
        original_stat.st_ino,
        original_stat.st_mode,
        original_stat.st_uid,
        original_stat.st_gid,
        original_stat.st_nlink,
        original_stat.st_size,
        original_stat.st_mtime_ns,
        original_stat.st_ctime_ns,
    )
    original_sha256 = sha256_file(binary)
    try:
        original_xattrs = {
            name: os.getxattr(binary, name, follow_symlinks=False)
            for name in os.listxattr(binary, follow_symlinks=False)
        }
    except OSError as exc:
        fail(f"cannot snapshot profiled binary xattrs before extraction: {exc}")
    notes_stdout, _notes_stderr = run_tool(
        readelf, ["-n", os.fspath(binary)], "binary build-ID inspection"
    )
    build_ids = {
        match.group(1).lower()
        for match in re.finditer(r"(?im)^\s*Build ID:\s*([0-9a-f]+)\s*$", notes_stdout)
    }
    if len(build_ids) != 1:
        fail("profiled binary must contain exactly one unambiguous GNU build ID")
    build_id = require_build_id(build_ids.pop())
    with tempfile.TemporaryDirectory(prefix="gentoo-sample-text-") as directory:
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
            "binary .text extraction",
        )
        try:
            text_stat = text_path.lstat()
        except OSError as exc:
            fail(f"objcopy did not create the requested .text payload: {exc}")
        if not stat.S_ISREG(text_stat.st_mode) or text_stat.st_size == 0:
            fail("profiled binary has no nonempty regular .text payload")
        try:
            rewritten_stat = rewritten_path.lstat()
        except OSError as exc:
            fail(f"objcopy did not create its separate scratch ELF output: {exc}")
        if not stat.S_ISREG(rewritten_stat.st_mode) or rewritten_stat.st_size == 0:
            fail("objcopy scratch ELF output is not a nonempty regular file")
        text_sha256 = sha256_file(text_path)
    try:
        final_stat = binary.stat()
    except OSError as exc:
        fail(f"cannot stat profiled binary after identity extraction: {exc}")
    final_identity = (
        final_stat.st_dev,
        final_stat.st_ino,
        final_stat.st_mode,
        final_stat.st_uid,
        final_stat.st_gid,
        final_stat.st_nlink,
        final_stat.st_size,
        final_stat.st_mtime_ns,
        final_stat.st_ctime_ns,
    )
    try:
        final_xattrs = {
            name: os.getxattr(binary, name, follow_symlinks=False)
            for name in os.listxattr(binary, follow_symlinks=False)
        }
    except OSError as exc:
        fail(f"cannot snapshot profiled binary xattrs after extraction: {exc}")
    if (
        final_identity != original_identity
        or final_xattrs != original_xattrs
        or sha256_file(binary) != original_sha256
    ):
        fail("profiled binary changed during read-only identity extraction")
    return build_id, text_sha256


def ensure_new_regular_destination(path: Path, label: str) -> None:
    validate_output_destination(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        fail(f"cannot inspect {label} {path}: {exc}")
    fail(f"{label} already exists: {path}")


def sample_convert_command(arguments: argparse.Namespace) -> int:
    profile = arguments.profile_out
    metadata_out = arguments.metadata_out
    if profile.name != "sample.prof":
        fail("--profile-out must end in the exact filename sample.prof")
    ensure_new_regular_destination(profile, "sample profile output")
    ensure_new_regular_destination(metadata_out, "sample metadata output")
    destinations = {os.path.normpath(path) for path in (profile, metadata_out)}
    if len(destinations) != 2:
        fail("--profile-out and --metadata-out must be distinct")
    partial = profile.with_name("sample.prof.partial")
    validate_output_destination(partial)
    try:
        partial_stat = partial.lstat()
    except FileNotFoundError:
        partial_stat = None
    if partial_stat is not None:
        if not stat.S_ISREG(partial_stat.st_mode):
            fail(f"stale transaction path is not a regular file: {partial}")
        partial.unlink()

    timeout_seconds = arguments.timeout_seconds
    kill_after_seconds = arguments.kill_after_seconds
    if not 0 < timeout_seconds <= 86400:
        fail("--timeout-seconds must be greater than zero and at most 86400")
    if not 0 < kill_after_seconds <= 60:
        fail("--kill-after-seconds must be greater than zero and at most 60")
    # Validate caller-supplied provenance before launching an expensive converter.
    build_reproducibility(arguments)

    binary = canonical_regular_input(arguments.binary, "profiled binary")
    perf_data = canonical_regular_input(arguments.perf_data, "perf data")
    debug_binary = (
        canonical_regular_input(arguments.debug_binary, "debug binary")
        if arguments.debug_binary is not None
        else None
    )
    binary_observation = observe_regular_file(binary, "profiled binary")
    perf_data_observation = observe_regular_file(perf_data, "perf data")
    debug_binary_observation = (
        observe_regular_file(debug_binary, "debug binary")
        if debug_binary is not None
        else None
    )
    clang_major = require_positive_major(arguments.clang_major, "clang_major")
    producer = inspect_llvm_tool(arguments.llvm_profgen, clang_major, "llvm-profgen")
    validator = inspect_llvm_profdata(arguments.llvm_profdata, clang_major)
    readelf_identity = inspect_llvm_tool(arguments.readelf, clang_major, "llvm-readelf")
    objcopy_identity = inspect_llvm_tool(arguments.objcopy, clang_major, "llvm-objcopy")
    build_id, text_sha256 = extract_binary_identity(
        binary,
        Path(str(readelf_identity["realpath"])),
        Path(str(objcopy_identity["realpath"])),
    )

    profile.parent.mkdir(parents=True, exist_ok=True)
    validate_output_destination(partial)
    command_arguments = [f"--binary={binary}"]
    if debug_binary is not None:
        command_arguments.append(f"--debug-binary={debug_binary}")
    command_arguments.extend(
        [
            f"--perfdata={perf_data}",
            "--format=extbinary",
            "--show-detailed-warning",
            f"--output={partial}",
        ]
    )

    completed = False
    old_handlers: dict[signal.Signals, Any] = {}

    def interrupted(signum: int, _frame: object) -> NoReturn:
        fail(f"sample conversion interrupted by signal {signum}")

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old_handlers[signum] = signal.signal(signum, interrupted)
        converter_stdout, converter_stderr = run_bounded_tool(
            Path(str(producer["realpath"])),
            command_arguments,
            "llvm-profgen conversion",
            timeout_seconds,
            kill_after_seconds,
        )
        validate_sample_file(
            partial,
            Path(str(validator["realpath"])),
            allow_transaction_partial=True,
        )
        require_unchanged_file(binary, binary_observation, "profiled binary")
        require_unchanged_file(perf_data, perf_data_observation, "perf data")
        if debug_binary is not None and debug_binary_observation is not None:
            require_unchanged_file(
                debug_binary, debug_binary_observation, "debug binary"
            )
        source: dict[str, object] = {
            "binary_path": os.fspath(binary),
            "binary_sha256": binary_observation["sha256"],
            "binary_observation": binary_observation,
            "command_arguments": command_arguments,
            "command_output_sha256": hashlib.sha256(
                (converter_stdout + "\0" + converter_stderr).encode("utf-8")
            ).hexdigest(),
            "debug_binary_path": (
                os.fspath(debug_binary) if debug_binary is not None else None
            ),
            "debug_binary_sha256": (
                debug_binary_observation["sha256"]
                if debug_binary_observation is not None
                else None
            ),
            "debug_binary_observation": debug_binary_observation,
            "kind": "llvm-profgen",
            "objcopy": objcopy_identity,
            "perf_data_path": os.fspath(perf_data),
            "perf_data_sha256": perf_data_observation["sha256"],
            "perf_data_observation": perf_data_observation,
            "producer": producer,
            "readelf": readelf_identity,
        }
        os.replace(partial, profile)
        directory_descriptor = os.open(profile.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        arguments.profile = profile
        arguments.build_id = build_id
        arguments.text_sha256 = text_sha256
        metadata = sample_identity(arguments, source)
        require_unchanged_file(binary, binary_observation, "profiled binary")
        require_unchanged_file(perf_data, perf_data_observation, "perf data")
        if debug_binary is not None and debug_binary_observation is not None:
            require_unchanged_file(
                debug_binary, debug_binary_observation, "debug binary"
            )
        atomic_write(
            metadata_out,
            (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        completed = True
        print(metadata["profile_sha256"])
        return 0
    finally:
        partial.unlink(missing_ok=True)
        if not completed:
            metadata_out.unlink(missing_ok=True)
            profile.unlink(missing_ok=True)
        for signum, old_handler in old_handlers.items():
            signal.signal(signum, old_handler)


def sample_validate_command(arguments: argparse.Namespace) -> int:
    recorded = load_json_object(arguments.metadata, "sample profile metadata")
    require_exact_fields(recorded, SAMPLE_METADATA_FIELDS, "sample profile metadata")
    source = validate_sample_source(
        recorded["source"], arguments.clang_major, arguments.profile
    )
    validate_recorded_reproducibility(recorded["reproducibility"])
    expected = sample_identity(arguments, source)
    if recorded != expected:
        differing = sorted(
            key for key in SAMPLE_METADATA_FIELDS if recorded.get(key) != expected.get(key)
        )
        fail(f"sample profile metadata mismatch in: {', '.join(differing)}")
    print(expected["profile_sha256"])
    return 0


def sample_record_disabled(_arguments: argparse.Namespace) -> int:
    fail(
        "sample-record is permanently disabled: external caller-asserted sample "
        "identity is not authoritative; use sample-convert, then validate-profile.py "
        "for the sole dispatcher manifest/sidecar transaction"
    )


def add_sample_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--llvm-profdata", type=Path, required=True)
    parser.add_argument("--cpv", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--abi", required=True)
    parser.add_argument("--clang-major", type=int, required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--text-sha256", required=True)


def add_sample_package_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cpv", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--abi", required=True)
    parser.add_argument("--clang-major", type=int, required=True)


def add_reproducibility_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--optimization-generation-id", required=True)
    parser.add_argument("--workload-revision", required=True)
    parser.add_argument("--source-identity-sha256", required=True)
    parser.add_argument("--production-host", required=True)
    parser.add_argument("--production-date", required=True)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    fingerprint_parser = subparsers.add_parser(
        "fingerprint", help="hash an exact package build identity"
    )
    fingerprint_parser.add_argument("--input", type=Path, required=True)
    fingerprint_parser.add_argument("--metadata-out", type=Path)
    fingerprint_parser.add_argument("--key-out", type=Path)
    fingerprint_parser.set_defaults(func=fingerprint_command)

    path_parser = subparsers.add_parser(
        "profile-path", help="construct a family-separated profile path"
    )
    path_parser.add_argument("--root", type=Path, required=True)
    path_parser.add_argument(
        "--family",
        choices=("clang-ir", "rust", "gcc", "go", "clang-sample", "kernel"),
        required=True,
    )
    path_parser.add_argument("--compiler-major", type=int)
    path_parser.add_argument("--language-version")
    path_parser.add_argument("--target-triple")
    path_parser.add_argument("--rustc-llvm-version")
    path_parser.add_argument("--generation")
    path_parser.add_argument("--abi")
    path_parser.add_argument("--cpv")
    path_parser.add_argument("--fingerprint")
    path_parser.add_argument("--binary")
    path_parser.add_argument("--build-id")
    path_parser.add_argument("--kernel-release")
    path_parser.add_argument("--config-hash")
    path_parser.set_defaults(func=profile_path_command)

    sample_record_parser = subparsers.add_parser(
        "sample-record",
        help="disabled legacy external sample identity recorder",
    )
    sample_record_parser.set_defaults(func=sample_record_disabled)

    sample_validate_parser = subparsers.add_parser(
        "sample-validate", help="fail closed unless sample.prof matches exact metadata"
    )
    add_sample_identity_arguments(sample_validate_parser)
    add_reproducibility_arguments(sample_validate_parser)
    sample_validate_parser.add_argument("--metadata", type=Path, required=True)
    sample_validate_parser.set_defaults(func=sample_validate_command)

    sample_convert_parser = subparsers.add_parser(
        "sample-convert",
        help="transactionally convert exact perf/binary input into sample.prof",
    )
    add_sample_package_arguments(sample_convert_parser)
    add_reproducibility_arguments(sample_convert_parser)
    sample_convert_parser.add_argument("--llvm-profgen", type=Path, required=True)
    sample_convert_parser.add_argument("--llvm-profdata", type=Path, required=True)
    sample_convert_parser.add_argument("--readelf", type=Path, required=True)
    sample_convert_parser.add_argument("--objcopy", type=Path, required=True)
    sample_convert_parser.add_argument("--binary", type=Path, required=True)
    sample_convert_parser.add_argument("--debug-binary", type=Path)
    sample_convert_parser.add_argument("--perf-data", type=Path, required=True)
    sample_convert_parser.add_argument("--profile-out", type=Path, required=True)
    sample_convert_parser.add_argument("--metadata-out", type=Path, required=True)
    sample_convert_parser.add_argument(
        "--timeout-seconds", type=float, default=600.0
    )
    sample_convert_parser.add_argument(
        "--kill-after-seconds", type=float, default=5.0
    )
    sample_convert_parser.set_defaults(func=sample_convert_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    arguments = parser.parse_args(argv)
    try:
        return int(arguments.func(arguments))
    except IdentityError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

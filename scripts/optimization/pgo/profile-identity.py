#!/usr/bin/env python3
"""Create exact PGO package identities and validate Clang sample profiles.

The output deliberately contains no timestamps.  A fingerprint is the SHA-256
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
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn


SCHEMA_VERSION = 1
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
}


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


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not path.is_file():
            fail(f"output path exists and is not a regular file: {path}")
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
    major = value["major"]
    if not isinstance(major, int) or isinstance(major, bool) or major < 1:
        fail("compiler.major must be a positive integer")
    profile_format = require_component(value["profile_format"], "compiler.profile_format")
    if not profile_format.startswith(PROFILE_FORMAT_PREFIX[family]):
        fail(
            f"compiler.profile_format {profile_format!r} is incompatible with {family}"
        )
    requested, realpath, binary_sha256 = inspect_executable(value["path"], "compiler")
    version_arguments = compiler_version_arguments(family)
    stdout, stderr = run_tool(realpath, version_arguments, "compiler version command")
    observed_major = detect_compiler_major(family, stdout + "\n" + stderr)
    if observed_major != major:
        fail(
            f"compiler.major is {major}, but the active compiler reports {observed_major}"
        )
    return {
        "family": family,
        "major": major,
        "path": os.fspath(requested),
        "profile_format": profile_format,
        "realpath": os.fspath(realpath),
        "sha256": binary_sha256,
        "version_arguments": version_arguments,
        "version_stderr": stderr,
        "version_stdout": stdout,
    }


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
    abi = require_component(input_data["abi"], "abi")

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

    return {
        "abi": abi,
        "category": category,
        "chost": chost,
        "compiler": inspect_compiler(input_data["compiler"]),
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
    if not root.is_absolute():
        fail("--root must be absolute")
    family = arguments.family
    path: Path
    if family == "clang-ir":
        allowed = {"compiler_major", "generation", "abi"}
        reject_unused(arguments, allowed, family)
        path = root / family / str(arguments.compiler_major) / require_component(
            required(arguments, "generation", family), "generation"
        ) / require_component(required(arguments, "abi", family), "abi") / "merged.profdata"
    elif family == "rust":
        allowed = {"language_version", "compiler_major", "generation", "abi"}
        reject_unused(arguments, allowed, family)
        path = (
            root
            / family
            / require_component(required(arguments, "language_version", family), "language_version")
            / str(arguments.compiler_major)
            / require_component(required(arguments, "generation", family), "generation")
            / require_component(required(arguments, "abi", family), "abi")
            / "merged.profdata"
        )
    elif family == "gcc":
        allowed = {"compiler_major", "cpv", "fingerprint", "abi"}
        reject_unused(arguments, allowed, family)
        path = (
            root
            / family
            / str(arguments.compiler_major)
            / require_cpv(required(arguments, "cpv", family))
            / require_hex64(required(arguments, "fingerprint", family), "fingerprint")
            / require_component(required(arguments, "abi", family), "abi")
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
        path = (
            root
            / family
            / str(arguments.compiler_major)
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


def inspect_llvm_profdata(path_value: object, clang_major: int) -> dict[str, object]:
    requested, realpath, binary_sha256 = inspect_executable(path_value, "llvm_profdata")
    stdout, stderr = run_tool(realpath, ["--version"], "llvm-profdata version command")
    match = re.search(r"(?im)LLVM version\s+([0-9]+)(?:\.|\s)", stdout + "\n" + stderr)
    if match is None:
        fail("llvm-profdata output does not contain an LLVM major")
    observed_major = int(match.group(1))
    if observed_major != clang_major:
        fail(
            f"llvm-profdata major {observed_major} does not match Clang major {clang_major}"
        )
    return {
        "path": os.fspath(requested),
        "realpath": os.fspath(realpath),
        "sha256": binary_sha256,
        "version_stderr": stderr,
        "version_stdout": stdout,
    }


def validate_sample_file(profile: Path, llvm_profdata: Path) -> dict[str, object]:
    if not profile.is_absolute():
        fail("sample profile path must be absolute")
    if profile.name != "sample.prof":
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


def sample_identity(arguments: argparse.Namespace) -> dict[str, object]:
    cpv = require_cpv(arguments.cpv)
    fingerprint = require_hex64(arguments.fingerprint, "fingerprint")
    abi = require_component(arguments.abi, "abi")
    clang_major = arguments.clang_major
    if not isinstance(clang_major, int) or clang_major < 1:
        fail("clang_major must be a positive integer")
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
        "schema_version": SCHEMA_VERSION,
        "validation": validation,
        "validator": validator,
    }


def sample_record_command(arguments: argparse.Namespace) -> int:
    metadata = sample_identity(arguments)
    atomic_write(
        arguments.metadata_out,
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(metadata["profile_sha256"])
    return 0


def sample_validate_command(arguments: argparse.Namespace) -> int:
    recorded = load_json_object(arguments.metadata, "sample profile metadata")
    require_exact_fields(recorded, SAMPLE_METADATA_FIELDS, "sample profile metadata")
    expected = sample_identity(arguments)
    if recorded != expected:
        differing = sorted(
            key for key in SAMPLE_METADATA_FIELDS if recorded.get(key) != expected.get(key)
        )
        fail(f"sample profile metadata mismatch in: {', '.join(differing)}")
    print(expected["profile_sha256"])
    return 0


def add_sample_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--llvm-profdata", type=Path, required=True)
    parser.add_argument("--cpv", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--abi", required=True)
    parser.add_argument("--clang-major", type=int, required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--text-sha256", required=True)


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
        "sample-record", help="validate sample.prof and atomically record its identity"
    )
    add_sample_identity_arguments(sample_record_parser)
    sample_record_parser.add_argument("--metadata-out", type=Path, required=True)
    sample_record_parser.set_defaults(func=sample_record_command)

    sample_validate_parser = subparsers.add_parser(
        "sample-validate", help="fail closed unless sample.prof matches exact metadata"
    )
    add_sample_identity_arguments(sample_validate_parser)
    sample_validate_parser.add_argument("--metadata", type=Path, required=True)
    sample_validate_parser.set_defaults(func=sample_validate_command)
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

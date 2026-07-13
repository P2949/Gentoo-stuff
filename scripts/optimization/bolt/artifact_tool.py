#!/usr/bin/env python3
"""Fail-closed pre-strip BOLT capture and ${ED} deployment transactions."""

from __future__ import annotations

import argparse
import base64
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn


SCHEMA_CAPTURE = "gentoo-optimization-bolt-capture-v2"
SCHEMA_OUTPUT = "gentoo-optimization-bolt-output-v2"
SCHEMA_COMMAND = "gentoo-optimization-bolt-command-v1"
SCHEMA_ZERO_ELIGIBILITY = "gentoo-optimization-bolt-zero-eligibility-v1"
FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
BUILD_ID_RE = re.compile(r"Build ID:\s*([0-9A-Fa-f]+)")
HEADER_RE = re.compile(r"^\s*(Class|Data|Type|Machine):\s*(.*?)\s*$")
SECTION_RE = re.compile(
    r"^\s*\[\s*(\d+)\]\s+(\S+)\s+(\S+)\s+"
    r"[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+([0-9A-Fa-f]+)\s+"
    r"\S+\s+(\S*)\s+(\d+)\s+(\d+)\s+\d+\s*$"
)
INTERPRETER_RE = re.compile(r"Requesting program interpreter:\s*([^\]]+)\]")
POLICY_REVISION = "gentoo-system-wide-bolt-v1-cdsort-20260712"
APPROVED_BOLT_OPTIONS = [
    "-reorder-blocks=ext-tsp",
    "-reorder-functions=cdsort",
    "-split-functions",
    "-split-all-cold",
    "-split-eh",
    "-icf=safe",
    "-update-debug-sections",
    "-dyno-stats",
]
ABI_IDENTITY_KEYS = (
    "elf_class",
    "elf_data",
    "elf_type",
    "machine",
    "elf_role",
    "interpreter",
    "needed",
    "soname",
    "rpath",
    "runpath",
    "exported_dynamic_symbols",
    "symbol_version_names",
    "symbol_version_files",
    "cet_properties",
    "gnu_stack_policy",
    "has_gnu_relro",
    "bind_now",
)
ELF_MAGIC = b"\x7fELF"
SYSTEM_ROOT = Path("/usr")
PRODUCTION_CACHE_ROOT = Path("/var/cache/gentoo-optimization/bolt")
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
DEFAULT_TOOL_KILL_AFTER_SECONDS = 5.0
TOOL_TIMEOUT_SECONDS = DEFAULT_TOOL_TIMEOUT_SECONDS
TOOL_KILL_AFTER_SECONDS = DEFAULT_TOOL_KILL_AFTER_SECONDS


class BoltArtifactError(RuntimeError):
    """A fail-closed capture or deployment error."""


def fail(message: str) -> NoReturn:
    raise BoltArtifactError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def positive_seconds(value: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive number of seconds") from error
    if result <= 0 or result > 300:
        raise argparse.ArgumentTypeError("must be greater than zero and at most 300 seconds")
    return result


def nonnegative_seconds(value: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a nonnegative number of seconds") from error
    if result < 0 or result > 30:
        raise argparse.ArgumentTypeError("must be between zero and 30 seconds")
    return result


def nonnegative_integer(value: str) -> int:
    try:
        result = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a nonnegative integer") from error
    if result < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return result


def terminate_process_group(process: subprocess.Popen[str], kill_after: float) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=kill_after)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=kill_after)
    except subprocess.TimeoutExpired:
        fail(f"tool process group did not die after SIGKILL: pid={process.pid}")


def run_bounded(argv: list[str]) -> tuple[int, str, str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            start_new_session=True,
        )
    except OSError as error:
        fail(f"cannot execute {argv[0]}: {error}")
    try:
        stdout, stderr = process.communicate(timeout=TOOL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        terminate_process_group(process, TOOL_KILL_AFTER_SECONDS)
        fail(
            f"tool timed out after {TOOL_TIMEOUT_SECONDS:g}s "
            f"(TERM then KILL after {TOOL_KILL_AFTER_SECONDS:g}s): {' '.join(argv)}"
        )
    except BaseException:
        terminate_process_group(process, TOOL_KILL_AFTER_SECONDS)
        raise
    return process.returncode, stdout, stderr


def run_checked(argv: list[str]) -> str:
    status, stdout, stderr = run_bounded(argv)
    if status != 0:
        detail = stderr.strip() or stdout.strip()
        fail(f"command failed ({status}): {' '.join(argv)}: {detail}")
    return stdout


def reject_symlink_components(path_text: str, label: str) -> Path:
    path = Path(os.path.abspath(path_text))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            fail(f"{label} contains a symlink component: {current}")
    return path


def validate_private_directory(path: Path, expected_uid: int, label: str) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        fail(f"{label} is not a directory: {path}")
    if info.st_uid != expected_uid:
        fail(f"{label} has wrong owner uid {info.st_uid}; expected {expected_uid}: {path}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        fail(f"{label} is group/world-writable: {path}")


def validate_root(path_text: str, label: str) -> Path:
    unresolved = reject_symlink_components(path_text, label)
    path = unresolved.resolve(strict=True)
    if not path.is_dir():
        fail(f"{label} is not a directory: {path}")
    if path == Path("/") or path == SYSTEM_ROOT or SYSTEM_ROOT in path.parents:
        fail(f"refusing unsafe {label}: {path}")
    return path


def validate_portage_ed(path_text: str, test_mode: bool) -> Path:
    ed = validate_root(path_text, "ED")
    if test_mode:
        return ed
    if os.geteuid() != 0:
        fail("production BOLT hooks must run as root")
    environment_ed = os.environ.get("ED")
    environment_d = os.environ.get("D")
    if not environment_ed:
        fail("production BOLT hook requires active Portage ED")
    active_ed = validate_root(environment_ed, "active Portage ED")
    if active_ed != ed:
        fail(f"--ed does not match active Portage ED: argument={ed}, active={active_ed}")
    if environment_d:
        active_d = validate_root(environment_d, "active Portage D")
        if active_d != ed:
            fail(f"active Portage D does not match ED: D={active_d}, ED={ed}")
    builddir_text = os.environ.get("PORTAGE_BUILDDIR")
    if not builddir_text:
        fail("production BOLT hook requires PORTAGE_BUILDDIR")
    builddir = validate_root(builddir_text, "PORTAGE_BUILDDIR")
    if builddir not in ed.parents:
        fail(f"active Portage ED is not below PORTAGE_BUILDDIR: ED={ed}, builddir={builddir}")
    return ed


def validate_cache_root(path_text: str, ed: Path | None, test_mode: bool) -> Path:
    raw = reject_symlink_components(path_text, "cache root")
    if not test_mode:
        if os.geteuid() != 0:
            fail("production BOLT cache operations must run as root")
        if raw != PRODUCTION_CACHE_ROOT:
            fail(
                "production cache root must be exactly "
                f"{PRODUCTION_CACHE_ROOT}; use --test-mode only for hermetic fixtures"
            )
    raw.mkdir(mode=0o700, parents=True, exist_ok=True)
    reject_symlink_components(str(raw), "cache root")
    path = raw.resolve(strict=True)
    if path == Path("/") or path == SYSTEM_ROOT or SYSTEM_ROOT in path.parents:
        fail(f"refusing unsafe cache root: {path}")
    if ed is not None and (path == ed or ed in path.parents or path in ed.parents):
        fail("cache root and ED must be disjoint")
    validate_private_directory(path, os.geteuid() if test_mode else 0, "cache root")
    return path


@contextlib.contextmanager
def fingerprint_lock(
    cache: Path,
    fingerprint: str,
    timeout_seconds: float,
    test_mode: bool,
    test_hold_seconds: float,
) -> Iterator[None]:
    locks = cache / "locks"
    locks.mkdir(mode=0o700, exist_ok=True)
    reject_symlink_components(str(locks), "BOLT lock root")
    expected_uid = os.geteuid() if test_mode else 0
    validate_private_directory(locks, expected_uid, "BOLT lock root")
    lock_path = locks / f"{fingerprint}.lock"
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        fail(f"cannot open non-symlink fingerprint lock {lock_path}: {error}")
    acquired = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            fail(f"fingerprint lock is not regular: {lock_path}")
        if info.st_uid != expected_uid:
            fail(
                f"fingerprint lock has wrong owner uid {info.st_uid}; "
                f"expected {expected_uid}: {lock_path}"
            )
        if stat.S_IMODE(info.st_mode) & 0o077:
            fail(f"fingerprint lock is not mode 0600/private: {lock_path}")
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    fail(
                        f"timed out after {timeout_seconds:g}s waiting for "
                        f"fingerprint lock: {lock_path}"
                    )
                time.sleep(min(0.05, remaining))
        if test_hold_seconds:
            if not test_mode:
                fail("--test-lock-hold-seconds requires --test-mode")
            time.sleep(test_hold_seconds)
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def validate_fingerprint(value: str) -> str:
    if FINGERPRINT_RE.fullmatch(value) is None:
        fail("package fingerprint must be exactly 64 lowercase hexadecimal characters")
    return value


def safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        fail(f"path escaped root {root}: {path}")
    text = relative.as_posix()
    if text in ("", ".") or text.startswith("/") or ".." in relative.parts:
        fail(f"unsafe relative path: {text!r}")
    return text


def path_from_relative(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        fail(f"unsafe manifest path: {relative!r}")
    result = root.joinpath(*candidate.parts)
    try:
        result.relative_to(root)
    except ValueError:
        fail(f"manifest path escaped root: {relative!r}")
    return result


def read_xattrs(path: Path, *, follow_symlinks: bool = False) -> dict[str, str]:
    try:
        names = os.listxattr(path, follow_symlinks=follow_symlinks)
    except OSError as error:
        if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            return {}
        fail(f"cannot list xattrs for {path}: {error}")
    result: dict[str, str] = {}
    for name in sorted(names):
        try:
            value = os.getxattr(path, name, follow_symlinks=follow_symlinks)
        except OSError as error:
            fail(f"cannot read xattr {name!r} for {path}: {error}")
        result[name] = base64.b64encode(value).decode("ascii")
    return result


def metadata(path: Path, status: os.stat_result | None = None) -> dict[str, Any]:
    info = status if status is not None else path.lstat()
    xattrs = read_xattrs(path)
    return {
        "mode": format(stat.S_IMODE(info.st_mode), "04o"),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "xattrs_base64": xattrs,
        "capability_base64": xattrs.get("security.capability"),
    }


def open_noatime(path: Path) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    noatime = getattr(os, "O_NOATIME", 0)
    if not noatime:
        fail("O_NOATIME is unavailable; refusing a capture that could mutate ED atime")
    try:
        return os.open(path, flags | noatime)
    except OSError as error:
        fail(f"cannot read {path} without modifying atime: {error}")


def copy_noatime(source: Path, destination: Path, expected: os.stat_result) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = open_noatime(source)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            fail(f"file changed type during capture: {source}")
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            expected.st_dev,
            expected.st_ino,
            expected.st_size,
        ):
            fail(f"file changed identity during capture: {source}")
        with os.fdopen(fd, "rb", closefd=False) as input_stream:
            with destination.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
    finally:
        os.close(fd)
    if destination.stat().st_size != expected.st_size:
        fail(f"short copy while capturing {source}")


def is_elf(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(4) == ELF_MAGIC


def dump_section(
    objcopy: str, path: Path, directory: Path, section: str
) -> tuple[Path | None, str | None, int]:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe_name = hashlib.sha256(section.encode("utf-8")).hexdigest()
    output = directory / f"{safe_name}.section"
    rewritten = directory / "objcopy.output"
    output.unlink(missing_ok=True)
    rewritten.unlink(missing_ok=True)
    try:
        status, _, _ = run_bounded(
            [objcopy, "--dump-section", f"{section}={output}", str(path), str(rewritten)]
        )
        if status != 0 or not output.is_file():
            output.unlink(missing_ok=True)
            return None, None, 0
        return output, sha256_file(output), output.stat().st_size
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        rewritten.unlink(missing_ok=True)


def dump_text(objcopy: str, path: Path, directory: Path) -> tuple[str | None, int]:
    _, digest, size = dump_section(objcopy, path, directory, ".text")
    return digest, size


def parse_bolt_note(path: Path, elf_data: str) -> tuple[bool, str | None, str | None]:
    data = path.read_bytes()
    endian = "<" if "little endian" in elf_data else ">"
    offset = 0
    descriptions: list[str] = []
    try:
        while offset + 12 <= len(data):
            namesz, descsz, note_type = struct.unpack_from(f"{endian}III", data, offset)
            offset += 12
            if namesz > len(data) - offset:
                return False, None, None
            name = data[offset : offset + namesz]
            offset += (namesz + 3) & ~3
            if descsz > len(data) - offset:
                return False, None, None
            raw_description = data[offset : offset + descsz]
            offset += (descsz + 3) & ~3
            if name.rstrip(b"\0") == b"GNU" and note_type == 4:
                descriptions.append(
                    raw_description.rstrip(b"\0").decode("utf-8", "strict")
                )
    except (UnicodeDecodeError, struct.error):
        return False, None, None
    if offset != len(data) or len(descriptions) != 1:
        return False, None, None
    description_text = descriptions[0]
    marker = ", command line: "
    if not description_text.startswith("BOLT revision: ") or marker not in description_text:
        return False, description_text, None
    command_line = description_text.split(marker, 1)[1]
    if not command_line:
        return False, description_text, None
    return True, description_text, command_line


def parse_program_identity(output: str) -> dict[str, Any]:
    interpreter_match = INTERPRETER_RE.search(output)
    gnu_stack_policy = "absent"
    has_gnu_relro = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("GNU_RELRO"):
            has_gnu_relro = True
        if stripped.startswith("GNU_STACK"):
            tokens = stripped.split()
            flags = next(
                (token for token in tokens[1:] if re.fullmatch(r"[RWE]+", token)), ""
            )
            gnu_stack_policy = "executable" if "E" in flags else "non-executable"
    return {
        "interpreter": interpreter_match.group(1) if interpreter_match else None,
        "gnu_stack_policy": gnu_stack_policy,
        "has_gnu_relro": has_gnu_relro,
    }


def parse_dynamic_identity(output: str) -> dict[str, Any]:
    needed: list[str] = []
    sonames: list[str] = []
    rpath: list[str] = []
    runpath: list[str] = []
    bind_now = False
    for line in output.splitlines():
        bracket = re.search(r"\[([^]]*)\]", line)
        if "(NEEDED)" in line and bracket:
            needed.append(bracket.group(1))
        elif "(SONAME)" in line and bracket:
            sonames.append(bracket.group(1))
        elif "(RPATH)" in line and bracket:
            rpath.append(bracket.group(1))
        elif "(RUNPATH)" in line and bracket:
            runpath.append(bracket.group(1))
        if "(BIND_NOW)" in line or re.search(r"\bFlags(?:_1)?:.*\bNOW\b", line):
            bind_now = True
    if len(sonames) > 1:
        fail(f"ELF has multiple DT_SONAME entries: {sonames}")
    return {
        "needed": needed,
        "soname": sonames[0] if sonames else None,
        "rpath": rpath,
        "runpath": runpath,
        "bind_now": bind_now,
    }


def parse_dynamic_symbols(output: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in output.splitlines():
        fields = line.split(maxsplit=7)
        if len(fields) < 8 or not fields[0].rstrip(":").isdigit():
            continue
        _, _, _size, symbol_type, binding, visibility, index, name = fields
        if index == "UND" or binding not in ("GLOBAL", "WEAK"):
            continue
        if visibility not in ("DEFAULT", "PROTECTED") or not name:
            continue
        result.append(
            {
                "name": name,
                "type": symbol_type,
                "binding": binding,
                "visibility": visibility,
            }
        )
    return sorted(result, key=lambda item: json.dumps(item, sort_keys=True))


def parse_symbol_versions(output: str) -> tuple[list[str], list[str]]:
    names = sorted(set(re.findall(r"\bName:\s*(\S+)", output)))
    files = sorted(set(re.findall(r"\bFile:\s*(\S+)", output)))
    return names, files


def classify_elf(path: Path, readelf: str, objcopy: str, scratch: Path) -> dict[str, Any]:
    header_output = run_checked([readelf, "-hW", str(path)])
    headers: dict[str, str] = {}
    for line in header_output.splitlines():
        match = HEADER_RE.match(line)
        if match:
            value = match.group(2)
            if match.group(1) == "Type":
                value = value.split()[0]
            headers[match.group(1)] = value
    missing_headers = {"Class", "Data", "Type", "Machine"} - headers.keys()
    if missing_headers:
        fail(f"readelf omitted required ELF headers for {path}: {sorted(missing_headers)}")

    section_output = run_checked([readelf, "-SW", str(path)])
    sections: list[dict[str, Any]] = []
    for line in section_output.splitlines():
        match = SECTION_RE.match(line)
        if match:
            sections.append(
                {
                    "index": int(match.group(1)),
                    "name": match.group(2),
                    "type": match.group(3),
                    "size": int(match.group(4), 16),
                    "flags": match.group(5),
                    "link": int(match.group(6)),
                    "info": int(match.group(7)),
                }
            )
    section_names = {section["name"] for section in sections}
    executable_sections = sorted(
        section["name"] for section in sections if "X" in section["flags"]
    )
    relocation_sections = sorted(
        section["name"]
        for section in sections
        if section["type"] in ("REL", "RELA", "RELR")
    )
    executable_indexes = {
        section["index"] for section in sections if "X" in section["flags"]
    }
    text_relocations = sorted(
        section["name"]
        for section in sections
        if section["type"] in ("REL", "RELA")
        and (
            section["info"] in executable_indexes
            or re.fullmatch(r"[.]rela?[.]text(?:[.].+)?", section["name"])
            is not None
        )
    )
    symtab = any(section["type"] == "SYMTAB" for section in sections)
    symbol_count = 0
    defined_function_symbols = 0
    if symtab:
        symbols = run_checked([readelf, "-sW", str(path)])
        for line in symbols.splitlines():
            fields = line.split(maxsplit=7)
            if len(fields) < 7 or not fields[0].rstrip(":").isdigit():
                continue
            symbol_count += 1
            if fields[3] == "FUNC" and fields[6] != "UND":
                defined_function_symbols += 1

    program_identity = parse_program_identity(run_checked([readelf, "-lW", str(path)]))
    dynamic_identity = parse_dynamic_identity(run_checked([readelf, "-dW", str(path)]))
    dynamic_symbols = parse_dynamic_symbols(
        run_checked([readelf, "--dyn-syms", "-W", str(path)])
    )
    version_names, version_files = parse_symbol_versions(
        run_checked([readelf, "--version-info", "-W", str(path)])
    )
    notes = run_checked([readelf, "-nW", str(path)])
    build_ids = [value.lower() for value in BUILD_ID_RE.findall(notes)]
    if len(set(build_ids)) > 1:
        fail(f"multiple different GNU build IDs in {path}: {build_ids}")
    build_id = build_ids[0] if build_ids else None
    text_sha256, text_size = dump_text(objcopy, path, scratch)
    bolt_note_path, bolt_note_sha256, bolt_note_size = dump_section(
        objcopy, path, scratch / "bolt-note", ".note.bolt_info"
    )
    bolt_note_valid = False
    bolt_note_description: str | None = None
    bolt_command_line: str | None = None
    if bolt_note_path is not None:
        bolt_note_valid, bolt_note_description, bolt_command_line = parse_bolt_note(
            bolt_note_path, headers["Data"]
        )
    bolt_origin_sections = sorted(
        section["name"]
        for section in sections
        if section["name"].startswith(".bolt.org.") and section["size"] > 0
    )
    cet_properties = sorted(
        value.strip()
        for value in re.findall(r"Properties:\s*([^\n]+)", notes)
        if value.strip()
    )
    if headers["Type"] == "EXEC":
        elf_role = "fixed-executable"
    elif headers["Type"] == "DYN" and program_identity["interpreter"] is not None:
        elf_role = "pie-executable"
    elif headers["Type"] == "DYN":
        elf_role = "shared-object"
    else:
        elf_role = "unsupported"

    reasons: list[str] = []
    if headers["Class"] != "ELF64":
        reasons.append("unsupported-elf-class")
    if headers["Machine"] != "Advanced Micro Devices X86-64":
        reasons.append("unsupported-machine")
    if headers["Type"] not in ("EXEC", "DYN"):
        reasons.append("unsupported-elf-type")
    if not executable_sections:
        reasons.append("no-executable-section")
    if text_sha256 is None or text_size == 0:
        reasons.append("no-text-section")
    if not symtab or symbol_count == 0:
        reasons.append("no-full-symbol-table")
    elif defined_function_symbols == 0:
        reasons.append("no-defined-function-symbol")
    if not text_relocations:
        reasons.append("no-text-relocations")
    if build_id is None:
        reasons.append("no-gnu-build-id")

    return {
        "elf_class": headers["Class"],
        "elf_data": headers["Data"],
        "elf_type": headers["Type"],
        "elf_role": elf_role,
        "machine": headers["Machine"],
        **program_identity,
        **dynamic_identity,
        "exported_dynamic_symbols": dynamic_symbols,
        "symbol_version_names": version_names,
        "symbol_version_files": version_files,
        "cet_properties": cet_properties,
        "executable_sections": executable_sections,
        "section_names": sorted(section_names),
        "has_symtab": symtab,
        "symbol_count": symbol_count,
        "defined_function_symbols": defined_function_symbols,
        "relocation_sections": relocation_sections,
        "text_relocation_sections": text_relocations,
        "executable_relocation_sections": text_relocations,
        "build_id": build_id,
        "text_sha256": text_sha256,
        "text_size": text_size,
        "has_bolt_info": ".note.bolt_info" in section_names,
        "bolt_info_sha256": bolt_note_sha256,
        "bolt_info_size": bolt_note_size,
        "bolt_info_valid": bolt_note_valid,
        "bolt_info_description": bolt_note_description,
        "bolt_info_command_line": bolt_command_line,
        "bolt_origin_sections": bolt_origin_sections,
        "eligible": not reasons,
        # These are automatic readiness findings, never reviewed terminal
        # exclusions. Later classification policy must remediate or explicitly
        # adjudicate each finding.
        "readiness_failures": reasons,
    }


def tree_snapshot(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        names.sort()
        files.sort()
        base = Path(directory)
        for name in [*names, *files]:
            path = base / name
            info = path.lstat()
            record: dict[str, Any] = {
                "path": safe_relative(path, root),
                "mode": info.st_mode,
                "uid": info.st_uid,
                "gid": info.st_gid,
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "ctime_ns": info.st_ctime_ns,
                "device": info.st_dev,
                "inode": info.st_ino,
            }
            if stat.S_ISLNK(info.st_mode):
                record["target"] = os.readlink(path)
            records.append(record)
    return records


def scan_tree(root: Path) -> tuple[list[tuple[list[str], os.stat_result]], list[dict[str, Any]]]:
    groups: dict[tuple[int, int], tuple[list[str], os.stat_result]] = {}
    symlinks: list[dict[str, Any]] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        names.sort()
        files.sort()
        base = Path(directory)
        retained_names: list[str] = []
        for name in names:
            path = base / name
            info = path.lstat()
            relative = safe_relative(path, root)
            if stat.S_ISLNK(info.st_mode):
                symlinks.append(
                    {
                        "path": relative,
                        "target": os.readlink(path),
                        "uid": info.st_uid,
                        "gid": info.st_gid,
                    }
                )
            elif stat.S_ISDIR(info.st_mode):
                retained_names.append(name)
            else:
                fail(f"unsupported non-directory entry encountered: {path}")
        names[:] = retained_names
        for name in files:
            path = base / name
            info = path.lstat()
            relative = safe_relative(path, root)
            if stat.S_ISLNK(info.st_mode):
                symlinks.append(
                    {
                        "path": relative,
                        "target": os.readlink(path),
                        "uid": info.st_uid,
                        "gid": info.st_gid,
                    }
                )
            elif stat.S_ISREG(info.st_mode):
                key = (info.st_dev, info.st_ino)
                if key not in groups:
                    groups[key] = ([], info)
                groups[key][0].append(relative)
            else:
                fail(f"unsupported non-regular entry encountered: {path}")
    result = []
    for paths, info in groups.values():
        paths.sort()
        result.append((paths, info))
    result.sort(key=lambda item: item[0][0])
    symlinks.sort(key=lambda item: item["path"])
    return result, symlinks


def write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.partial.{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def byte_tree_identity(root: Path) -> list[dict[str, Any]]:
    identity: list[dict[str, Any]] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        names.sort()
        files.sort()
        base = Path(directory)
        for name in names:
            path = base / name
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                fail(f"capture cache contains an unsupported directory entry: {path}")
            identity.append(
                {"path": safe_relative(path, root), "type": "directory", "mode": stat.S_IMODE(info.st_mode)}
            )
        for name in files:
            path = base / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                fail(f"capture cache contains an unsupported file entry: {path}")
            identity.append(
                {
                    "path": safe_relative(path, root),
                    "type": "file",
                    "mode": stat.S_IMODE(info.st_mode),
                    "size": info.st_size,
                    "sha256": sha256_file(path),
                }
            )
    return identity


def adopt_or_quarantine_capture(stage: Path, final: Path, cache: Path, fingerprint: str) -> None:
    if not final.exists() and not final.is_symlink():
        os.replace(stage, final)
        return
    reject_symlink_components(str(final), "existing capture")
    if not final.is_dir():
        fail(f"existing capture identity is not a directory: {final}")
    if byte_tree_identity(stage) == byte_tree_identity(final):
        shutil.rmtree(stage)
        return
    quarantine_root = cache / "quarantine" / "capture-mismatch"
    quarantine_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    reject_symlink_components(str(quarantine_root), "capture quarantine root")
    quarantine = quarantine_root / f"{fingerprint}.{time.time_ns()}.{os.getpid()}"
    os.replace(stage, quarantine)
    fail(
        "fresh capture differs byte-for-byte from the existing identity; "
        f"quarantined candidate at {quarantine}"
    )


def load_json(path: Path, schema: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot load manifest {path}: {error}")
    if not isinstance(document, dict) or document.get("schema") != schema:
        fail(f"manifest {path} does not use schema {schema}")
    return document


def assert_abi_identity(
    candidate: dict[str, Any], reference: dict[str, Any], context: str
) -> None:
    for key in ABI_IDENTITY_KEYS:
        if candidate.get(key) != reference.get(key):
            fail(
                f"{context} ABI/security identity mismatch for {key}: "
                f"expected {reference.get(key)!r}, got {candidate.get(key)!r}"
            )


def abi_identity(document: dict[str, Any]) -> dict[str, Any]:
    return {key: document.get(key) for key in ABI_IDENTITY_KEYS}


def file_record(path_text: str, label: str) -> dict[str, Any]:
    if not Path(path_text).is_absolute():
        fail(f"{label} path must be absolute: {path_text}")
    unresolved = reject_symlink_components(path_text, label)
    info = unresolved.lstat()
    if not stat.S_ISREG(info.st_mode):
        fail(f"{label} is not a regular file: {unresolved}")
    path = unresolved.resolve(strict=True)
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def zero_eligibility_proof(
    path_text: str | None, fingerprint: str, expected_count: int
) -> dict[str, Any] | None:
    if expected_count != 0:
        if path_text is not None:
            fail("--zero-eligible-proof is forbidden when expected eligible count is positive")
        return None
    if path_text is None:
        fail(
            "expected eligible count zero requires --zero-eligible-proof from the "
            "frozen inventory"
        )
    identity = file_record(path_text, "zero-eligibility proof")
    document = load_json(Path(identity["path"]), SCHEMA_ZERO_ELIGIBILITY)
    required = {"schema", "package_fingerprint", "eligible_count", "inventory_evidence"}
    if set(document) != required:
        fail("zero-eligibility proof fields differ from the strict schema")
    if document["package_fingerprint"] != fingerprint or document["eligible_count"] != 0:
        fail("zero-eligibility proof does not bind this fingerprint to count zero")
    inventory = document["inventory_evidence"]
    validated = validate_recorded_files([inventory], "frozen inventory evidence")[0]
    if validated != inventory:
        fail("zero-eligibility frozen inventory identity mismatch")
    return {"identity": identity, "document": document}


def validate_recorded_files(records: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(records, list) or not records:
        fail(f"{label} records must be a nonempty list")
    validated: list[dict[str, Any]] = []
    for index, expected in enumerate(records):
        if not isinstance(expected, dict) or set(expected) != {"path", "sha256", "size"}:
            fail(f"invalid {label} record at index {index}")
        actual = file_record(str(expected.get("path", "")), f"{label} file")
        if actual != expected:
            fail(f"{label} file identity mismatch: expected {expected}, got {actual}")
        validated.append(actual)
    if len({item["path"] for item in validated}) != len(validated):
        fail(f"{label} contains duplicate paths")
    return validated


def llvm_bolt_identity(path_text: str) -> dict[str, Any]:
    record = file_record(path_text, "llvm-bolt executable")
    if not os.access(record["path"], os.X_OK):
        fail(f"llvm-bolt executable is not executable: {record['path']}")
    status, stdout, stderr = run_bounded([record["path"], "--version"])
    if status != 0:
        fail(f"llvm-bolt --version failed ({status}): {(stderr or stdout).strip()}")
    version = stdout.strip()
    if "LLVM version" not in version or not version:
        fail("llvm-bolt --version did not identify an LLVM BOLT build")
    return {**record, "version": version}


def expected_bolt_argv(
    tool: dict[str, Any], input_path: Path, output_path: Path, fdata: list[dict[str, Any]]
) -> list[str]:
    return [
        str(tool["path"]),
        str(input_path),
        "-o",
        str(output_path),
        *APPROVED_BOLT_OPTIONS,
        *(f"-data={item['path']}" for item in fdata),
    ]


def validate_command_record(
    path_text: str,
    *,
    expected_argv: list[str],
    tool: dict[str, Any],
    input_record: dict[str, Any],
    output_record: dict[str, Any],
    fdata: list[dict[str, Any]],
    workloads: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    record_identity = file_record(path_text, "BOLT command record")
    document = load_json(Path(record_identity["path"]), SCHEMA_COMMAND)
    required = {
        "schema",
        "argv",
        "exit_status",
        "started_at_utc",
        "completed_at_utc",
        "tool",
        "input",
        "output",
        "option_policy_revision",
        "options",
        "fdata",
        "workload_evidence",
        "profile_evidence",
        "stdout",
        "stderr",
    }
    if set(document) != required:
        fail(
            "BOLT command record fields differ from the strict schema: "
            f"expected={sorted(required)}, got={sorted(document)}"
        )
    if document["argv"] != expected_argv:
        fail("BOLT command record argv differs from the exact reviewed invocation")
    if document["exit_status"] != 0:
        fail("BOLT command record does not record exit status zero")
    for field in ("started_at_utc", "completed_at_utc"):
        value = document[field]
        if not isinstance(value, str) or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
        ) is None:
            fail(f"BOLT command record has invalid {field}")
    if document["completed_at_utc"] < document["started_at_utc"]:
        fail("BOLT command record completion precedes its start")
    if document["tool"] != tool:
        fail("BOLT command record tool identity mismatch")
    if document["input"] != input_record:
        fail("BOLT command record input identity mismatch")
    if document["output"] != output_record:
        fail("BOLT command record output identity mismatch")
    if document["option_policy_revision"] != POLICY_REVISION:
        fail("BOLT command record option-policy revision mismatch")
    if document["options"] != APPROVED_BOLT_OPTIONS:
        fail("BOLT command record option list mismatch")
    for field, expected in (
        ("fdata", fdata),
        ("workload_evidence", workloads),
        ("profile_evidence", profiles),
    ):
        if document[field] != expected:
            fail(f"BOLT command record {field} identity mismatch")
    validate_recorded_files([document["stdout"]], "BOLT stdout")
    validate_recorded_files([document["stderr"]], "BOLT stderr")
    return document, record_identity


def verify_bolt_provenance(record: dict[str, Any]) -> None:
    if record.get("option_policy_revision") != POLICY_REVISION:
        fail("prepared output uses an unreviewed BOLT option-policy revision")
    if record.get("options") != APPROVED_BOLT_OPTIONS:
        fail("prepared output uses an unreviewed BOLT option list")
    tool = llvm_bolt_identity(str(record.get("llvm_bolt", {}).get("path", "")))
    if tool != record.get("llvm_bolt"):
        fail("prepared output llvm-bolt identity is stale or mismatched")
    for key, label in (
        ("fdata", "BOLT fdata"),
        ("workload_evidence", "BOLT workload evidence"),
        ("profile_evidence", "BOLT profile evidence"),
    ):
        validate_recorded_files(record.get(key), label)
    command_record = record.get("command_record")
    if not isinstance(command_record, dict):
        fail("prepared output lacks a command-record identity")
    actual = file_record(str(command_record.get("path", "")), "BOLT command record")
    if actual != command_record:
        fail("prepared output command-record identity is stale or mismatched")
    command = load_json(Path(actual["path"]), SCHEMA_COMMAND)
    if command != record.get("command"):
        fail("prepared output embedded command record differs from its exact file")
    if command.get("tool") != record.get("llvm_bolt"):
        fail("prepared output command/tool binding mismatch")
    if command.get("option_policy_revision") != record.get("option_policy_revision"):
        fail("prepared output command/policy binding mismatch")
    if command.get("options") != record.get("options"):
        fail("prepared output command/options binding mismatch")
    for key, label in (
        ("fdata", "BOLT fdata"),
        ("workload_evidence", "BOLT workload evidence"),
        ("profile_evidence", "BOLT profile evidence"),
    ):
        if command.get(key) != record.get(key):
            fail(f"prepared output command/{key} binding mismatch")
    validate_recorded_files([command.get("stdout")], "BOLT stdout")
    validate_recorded_files([command.get("stderr")], "BOLT stderr")
    if command.get("exit_status") != 0:
        fail("prepared output command did not exit successfully")
    if command.get("output", {}).get("sha256") != record.get("output_sha256"):
        fail("prepared output command/output hash binding mismatch")
    if command.get("input", {}).get("sha256") != record.get("source_file_sha256"):
        fail("prepared output command/input hash binding mismatch")


def command_capture(arguments: argparse.Namespace) -> None:
    ed = validate_portage_ed(arguments.ed, arguments.test_mode)
    cache = validate_cache_root(arguments.cache_root, ed, arguments.test_mode)
    fingerprint = validate_fingerprint(arguments.fingerprint)
    zero_proof = zero_eligibility_proof(
        arguments.zero_eligible_proof, fingerprint, arguments.expected_eligible_count
    )
    readelf = shutil.which(arguments.readelf)
    objcopy = shutil.which(arguments.objcopy)
    if readelf is None or objcopy is None:
        fail("capture requires readelf and objcopy")

    inputs = cache / "inputs"
    inputs.mkdir(mode=0o700, exist_ok=True)
    reject_symlink_components(str(inputs), "capture input root")
    final = inputs / fingerprint
    stage = inputs / f"{fingerprint}.partial"
    if stage.exists():
        fail(f"stale capture stage exists: {stage}")
    stage.mkdir(mode=0o700)
    before = tree_snapshot(ed)
    artifacts: list[dict[str, Any]] = []
    regular_groups, symlinks = scan_tree(ed)
    elf_total = 0
    eligible_total = 0
    try:
        (stage / "objects").mkdir(mode=0o700)
        with tempfile.TemporaryDirectory(prefix="classify-", dir=stage) as scratch_text:
            scratch_root = Path(scratch_text)
            for index, (paths, info) in enumerate(regular_groups, 1):
                if info.st_nlink != len(paths):
                    fail(
                        "regular inode has hardlinks outside ED or changed during scan: "
                        f"{paths[0]} (st_nlink={info.st_nlink}, discovered={len(paths)})"
                    )
                source = path_from_relative(ed, paths[0])
                scratch = scratch_root / f"{index:06d}.file"
                copy_noatime(source, scratch, info)
                if not is_elf(scratch):
                    scratch.unlink()
                    continue
                elf_total += 1
                classification = classify_elf(
                    scratch, readelf, objcopy, scratch_root / f"{index:06d}.sections"
                )
                artifact_id = hashlib.sha256(paths[0].encode("utf-8")).hexdigest()
                object_name: str | None = None
                if classification["eligible"]:
                    eligible_total += 1
                    object_name = f"objects/{artifact_id}.elf"
                    destination = stage / object_name
                    os.replace(scratch, destination)
                    os.chmod(destination, 0o600)
                else:
                    scratch.unlink()
                artifacts.append(
                    {
                        "artifact_id": artifact_id,
                        "canonical_path": paths[0],
                        "paths": paths,
                        "hardlink_count": len(paths),
                        "source_device": info.st_dev,
                        "source_inode": info.st_ino,
                        "file_sha256": sha256_file(stage / object_name)
                        if object_name
                        else sha256_file_noatime(source, info),
                        "size": info.st_size,
                        "metadata": metadata(source, info),
                        "cache_object": object_name,
                        **classification,
                    }
                )
        after = tree_snapshot(ed)
        if before != after:
            fail("ED metadata/topology changed during capture")
        if eligible_total != arguments.expected_eligible_count:
            fail(
                "captured BOLT-eligible count differs from the frozen inventory: "
                f"expected={arguments.expected_eligible_count}, actual={eligible_total}"
            )
        manifest = {
            "schema": SCHEMA_CAPTURE,
            "package_fingerprint": fingerprint,
            "ed_root": str(ed),
            "regular_inode_groups_total": len(regular_groups),
            "elf_total": elf_total,
            "eligible_total": eligible_total,
            "expected_eligible_count": arguments.expected_eligible_count,
            "zero_eligible_proof": zero_proof,
            "ineligible_total": elf_total - eligible_total,
            "artifacts": artifacts,
            "symlinks": symlinks,
        }
        write_json_atomic(stage / "manifest.json", manifest)
        adopt_or_quarantine_capture(stage, final, cache, fingerprint)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(final / "manifest.json")


def sha256_file_noatime(path: Path, expected: os.stat_result) -> str:
    digest = hashlib.sha256()
    fd = open_noatime(path)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            expected.st_dev,
            expected.st_ino,
            expected.st_size,
        ):
            fail(f"file changed identity while hashing: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def capture_paths(cache: Path, fingerprint: str) -> tuple[Path, dict[str, Any]]:
    root = cache / "inputs" / fingerprint
    reject_symlink_components(str(root), "capture identity root")
    manifest = load_json(root / "manifest.json", SCHEMA_CAPTURE)
    if manifest.get("package_fingerprint") != fingerprint:
        fail("capture manifest fingerprint mismatch")
    return root, manifest


def output_manifest_path(cache: Path, fingerprint: str) -> Path:
    return cache / "outputs" / fingerprint / "manifest.json"


def find_artifact(manifest: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("artifact_id") == artifact_id
    ]
    if len(matches) != 1:
        fail(f"artifact ID is absent or ambiguous: {artifact_id}")
    artifact = matches[0]
    if not artifact.get("eligible"):
        fail(f"cannot register output for ineligible artifact: {artifact_id}")
    return artifact


def command_register(arguments: argparse.Namespace) -> None:
    output_unresolved = reject_symlink_components(arguments.output, "prepared output")
    output_status = output_unresolved.lstat()
    if not stat.S_ISREG(output_status.st_mode):
        fail(f"prepared output is not a regular file: {output_unresolved}")
    output_source = output_unresolved.resolve(strict=True)
    if not output_source.is_file():
        fail(f"prepared output is not a regular file: {output_source}")
    cache = validate_cache_root(arguments.cache_root, None, arguments.test_mode)
    fingerprint = validate_fingerprint(arguments.fingerprint)
    readelf = shutil.which(arguments.readelf)
    objcopy = shutil.which(arguments.objcopy)
    if readelf is None or objcopy is None:
        fail("output registration requires readelf and objcopy")
    _, capture = capture_paths(cache, fingerprint)
    source_artifact = find_artifact(capture, arguments.artifact_id)
    input_unresolved = reject_symlink_components(arguments.input, "exact BOLT input")
    input_status = input_unresolved.lstat()
    if not stat.S_ISREG(input_status.st_mode):
        fail(f"exact BOLT input is not a regular file: {input_unresolved}")
    input_source = input_unresolved.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="bolt-output-classify-", dir=cache) as temporary:
        temporary_root = Path(temporary)
        input_classification = classify_elf(
            input_source, readelf, objcopy, temporary_root / "input"
        )
        classification = classify_elf(
            output_source, readelf, objcopy, temporary_root / "output"
        )
    if sha256_file(input_source) != source_artifact["file_sha256"]:
        fail("exact BOLT input full-file hash differs from captured input")
    if input_classification["build_id"] != source_artifact["build_id"]:
        fail("exact BOLT input GNU build ID differs from captured input")
    if input_classification["text_sha256"] != source_artifact["text_sha256"]:
        fail("exact BOLT input .text hash differs from captured input")
    assert_abi_identity(input_classification, source_artifact, "exact BOLT input")
    if not classification["has_bolt_info"] or not classification["bolt_info_valid"]:
        fail("prepared output lacks a structurally valid GNU .note.bolt_info")
    if ".bolt.org.text" not in classification["bolt_origin_sections"]:
        fail("prepared output lacks nonempty .bolt.org.text transformation evidence")
    if classification["build_id"] is None:
        fail("prepared output lacks a GNU build ID")
    assert_abi_identity(classification, source_artifact, "prepared output")

    if arguments.option_policy_revision != POLICY_REVISION:
        fail(
            "unreviewed BOLT option-policy revision: "
            f"expected {POLICY_REVISION}, got {arguments.option_policy_revision}"
        )
    if arguments.bolt_option != APPROVED_BOLT_OPTIONS:
        fail(
            "unreviewed BOLT option list/order: "
            f"expected {APPROVED_BOLT_OPTIONS}, got {arguments.bolt_option}"
        )
    tool = llvm_bolt_identity(arguments.llvm_bolt)
    fdata = [file_record(value, "BOLT fdata") for value in arguments.fdata]
    workloads = [
        file_record(value, "BOLT workload evidence")
        for value in arguments.workload_evidence
    ]
    profiles = [
        file_record(value, "BOLT profile evidence")
        for value in arguments.profile_evidence
    ]
    for records, label in (
        (fdata, "BOLT fdata"),
        (workloads, "BOLT workload evidence"),
        (profiles, "BOLT profile evidence"),
    ):
        if not records:
            fail(f"at least one {label} file is required")
        if len({item["path"] for item in records}) != len(records):
            fail(f"duplicate {label} paths are forbidden")
    input_record = file_record(str(input_source), "exact BOLT input")
    output_record = file_record(str(output_source), "prepared BOLT output")
    expected_argv = expected_bolt_argv(
        tool, input_source, output_source, fdata
    )
    try:
        note_argv = shlex.split(str(classification["bolt_info_command_line"]), posix=True)
    except ValueError as error:
        fail(f"prepared output BOLT note has an invalid command line: {error}")
    if note_argv != expected_argv:
        fail("prepared output BOLT note command differs from the exact reviewed invocation")
    command_document, command_identity = validate_command_record(
        arguments.command_record,
        expected_argv=expected_argv,
        tool=tool,
        input_record=input_record,
        output_record=output_record,
        fdata=fdata,
        workloads=workloads,
        profiles=profiles,
    )

    output_root = cache / "outputs" / fingerprint
    objects = output_root / "objects"
    objects.mkdir(mode=0o700, parents=True, exist_ok=True)
    reject_symlink_components(str(objects), "prepared output root")
    manifest_path = output_root / "manifest.json"
    if manifest_path.exists():
        document = load_json(manifest_path, SCHEMA_OUTPUT)
        if document.get("package_fingerprint") != fingerprint:
            fail("output manifest fingerprint mismatch")
        if document.get("expected_eligible_count") != capture.get(
            "expected_eligible_count"
        ) or document.get("zero_eligible_proof") != capture.get("zero_eligible_proof"):
            fail("output manifest frozen eligible-count binding mismatch")
    else:
        document = {
            "schema": SCHEMA_OUTPUT,
            "package_fingerprint": fingerprint,
            "expected_eligible_count": capture.get("expected_eligible_count"),
            "zero_eligible_proof": capture.get("zero_eligible_proof"),
            "outputs": [],
        }
    outputs = document.get("outputs")
    if not isinstance(outputs, list):
        fail("output manifest outputs is not a list")
    if any(item.get("artifact_id") == arguments.artifact_id for item in outputs):
        fail(f"output already registered for artifact: {arguments.artifact_id}")

    destination = objects / f"{arguments.artifact_id}.bolt"
    partial = objects / f".{arguments.artifact_id}.bolt.partial.{os.getpid()}"
    try:
        with output_source.open("rb") as input_stream, partial.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.chmod(partial, 0o600)
        entry = {
            "artifact_id": arguments.artifact_id,
            "output_object": f"objects/{arguments.artifact_id}.bolt",
            "output_sha256": sha256_file(partial),
            "output_build_id": classification["build_id"],
            "output_text_sha256": classification["text_sha256"],
            "source_file_sha256": source_artifact["file_sha256"],
            "source_build_id": source_artifact["build_id"],
            "source_text_sha256": source_artifact["text_sha256"],
            "elf_class": classification["elf_class"],
            "elf_type": classification["elf_type"],
            "machine": classification["machine"],
            "has_bolt_info": True,
            "abi_security_identity": abi_identity(classification),
            "source_abi_security_identity": abi_identity(source_artifact),
            "bolt_info_sha256": classification["bolt_info_sha256"],
            "bolt_info_size": classification["bolt_info_size"],
            "bolt_info_description": classification["bolt_info_description"],
            "bolt_origin_sections": classification["bolt_origin_sections"],
            "llvm_bolt": tool,
            "option_policy_revision": POLICY_REVISION,
            "options": APPROVED_BOLT_OPTIONS,
            "fdata": fdata,
            "workload_evidence": workloads,
            "profile_evidence": profiles,
            "command_record": command_identity,
            "command": command_document,
        }
        os.replace(partial, destination)
        outputs.append(entry)
        outputs.sort(key=lambda item: item["artifact_id"])
        write_json_atomic(manifest_path, document)
    finally:
        partial.unlink(missing_ok=True)
    print(destination)


def current_identity(
    source: Path,
    expected_stat: os.stat_result,
    readelf: str,
    objcopy: str,
    scratch_root: Path,
) -> tuple[dict[str, Any], str]:
    scratch = scratch_root / hashlib.sha256(str(source).encode()).hexdigest()
    copy_noatime(source, scratch, expected_stat)
    classification = classify_elf(scratch, readelf, objcopy, scratch_root / "sections")
    return classification, sha256_file(scratch)


def verify_metadata_matches(path: Path, expected: dict[str, Any]) -> None:
    actual = metadata(path)
    if actual != expected:
        fail(f"metadata mismatch for {path}: expected {expected}, got {actual}")


def apply_metadata(path: Path, expected: dict[str, Any]) -> None:
    desired_uid = int(expected["uid"])
    desired_gid = int(expected["gid"])
    current = path.stat()
    if (current.st_uid, current.st_gid) != (desired_uid, desired_gid):
        try:
            os.chown(path, desired_uid, desired_gid)
        except OSError as error:
            fail(f"cannot preserve ownership for {path}: {error}")
    os.chmod(path, int(expected["mode"], 8))
    desired_xattrs = expected.get("xattrs_base64", {})
    if not isinstance(desired_xattrs, dict):
        fail(f"invalid xattr metadata for {path}")
    for name in os.listxattr(path):
        if name not in desired_xattrs:
            try:
                os.removexattr(path, name)
            except OSError as error:
                fail(f"cannot remove unexpected xattr {name!r} from {path}: {error}")
    for name, encoded in desired_xattrs.items():
        try:
            value = base64.b64decode(encoded, validate=True)
            os.setxattr(path, name, value)
        except (ValueError, OSError) as error:
            fail(f"cannot preserve xattr {name!r} for {path}: {error}")


def stage_hardlink_group(
    source: Path, paths: list[Path], metadata_record: dict[str, Any], token: str
) -> list[tuple[Path, Path]]:
    stages: list[tuple[Path, Path]] = []
    first = paths[0].with_name(f".{paths[0].name}.bolt-partial-{token}-0")
    try:
        with source.open("rb") as input_stream, first.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        apply_metadata(first, metadata_record)
        stages.append((first, paths[0]))
        for index, path in enumerate(paths[1:], 1):
            partial = path.with_name(f".{path.name}.bolt-partial-{token}-{index}")
            os.link(first, partial, follow_symlinks=False)
            stages.append((partial, path))
        return stages
    except BaseException:
        for partial, _ in stages:
            partial.unlink(missing_ok=True)
        first.unlink(missing_ok=True)
        raise


def verify_symlinks(ed: Path, expected: Any) -> None:
    if not isinstance(expected, list):
        fail("capture symlink manifest is not a list")
    _, current = scan_tree(ed)
    if current != expected:
        fail("ED symlink topology differs from captured input")


def command_deploy(arguments: argparse.Namespace) -> None:
    ed = validate_portage_ed(arguments.ed, arguments.test_mode)
    cache = validate_cache_root(arguments.cache_root, ed, arguments.test_mode)
    fingerprint = validate_fingerprint(arguments.fingerprint)
    zero_proof = zero_eligibility_proof(
        arguments.zero_eligible_proof, fingerprint, arguments.expected_eligible_count
    )
    readelf = shutil.which(arguments.readelf)
    objcopy = shutil.which(arguments.objcopy)
    if readelf is None or objcopy is None:
        fail("deployment requires readelf and objcopy")
    _, capture = capture_paths(cache, fingerprint)
    if capture.get("expected_eligible_count") != arguments.expected_eligible_count:
        fail("deployment expected eligible count differs from captured frozen count")
    if capture.get("zero_eligible_proof") != zero_proof:
        fail("deployment zero-eligibility proof differs from captured proof")
    output_root = cache / "outputs" / fingerprint
    reject_symlink_components(str(output_root), "prepared output root")
    output_manifest = load_json(output_root / "manifest.json", SCHEMA_OUTPUT)
    if output_manifest.get("package_fingerprint") != fingerprint:
        fail("output manifest fingerprint mismatch")
    if output_manifest.get("expected_eligible_count") != arguments.expected_eligible_count:
        fail("output manifest expected eligible count mismatch")
    if output_manifest.get("zero_eligible_proof") != zero_proof:
        fail("output manifest zero-eligibility proof mismatch")
    output_entries = output_manifest.get("outputs")
    if not isinstance(output_entries, list):
        fail("output manifest outputs is not a list")
    outputs_by_id = {item.get("artifact_id"): item for item in output_entries}
    if len(outputs_by_id) != len(output_entries):
        fail("duplicate artifact IDs in output manifest")

    verify_symlinks(ed, capture.get("symlinks"))
    artifacts = capture.get("artifacts")
    if not isinstance(artifacts, list):
        fail("capture artifacts is not a list")
    eligible = [item for item in artifacts if item.get("eligible")]
    if len(eligible) != arguments.expected_eligible_count:
        fail(
            "deployment eligible artifact count differs from the frozen inventory: "
            f"expected={arguments.expected_eligible_count}, actual={len(eligible)}"
        )
    if not eligible:
        fail("deployment requested for a package with no BOLT-eligible ELF")
    if set(outputs_by_id) != {item.get("artifact_id") for item in eligible}:
        fail("prepared BOLT outputs do not exactly cover captured eligible artifacts")

    diagnostics = cache / "diagnostics" / fingerprint / "pre-deploy"
    diagnostics.mkdir(mode=0o700, parents=True, exist_ok=True)
    reject_symlink_components(str(diagnostics), "diagnostic root")
    prepared: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix="bolt-deploy-validate-", dir=diagnostics
    ) as temporary:
        scratch_root = Path(temporary)
        for index, artifact in enumerate(eligible):
            artifact_id = artifact["artifact_id"]
            relative_paths = artifact.get("paths")
            if not isinstance(relative_paths, list) or not relative_paths:
                fail(f"invalid hardlink paths for {artifact_id}")
            paths = [path_from_relative(ed, value) for value in relative_paths]
            stats = [path.lstat() for path in paths]
            if not all(stat.S_ISREG(item.st_mode) for item in stats):
                fail(f"candidate path is no longer regular: {artifact_id}")
            inode_keys = {(item.st_dev, item.st_ino) for item in stats}
            if len(inode_keys) != 1:
                fail(f"hardlink topology differs for {artifact_id}")
            verify_metadata_matches(paths[0], artifact["metadata"])
            identity, file_sha = current_identity(
                paths[0], stats[0], readelf, objcopy, scratch_root / f"current-{index}"
            )
            if identity["build_id"] != artifact["build_id"]:
                fail(f"GNU build ID mismatch for {artifact['canonical_path']}")
            if identity["text_sha256"] != artifact["text_sha256"]:
                fail(f".text hash mismatch for {artifact['canonical_path']}")
            if file_sha != artifact["file_sha256"]:
                fail(f"full-file hash mismatch for {artifact['canonical_path']}")
            assert_abi_identity(identity, artifact, f"current input {artifact_id}")

            output_record = outputs_by_id[artifact_id]
            if not isinstance(output_record, dict):
                fail(f"prepared output record is not an object: {artifact_id}")
            for key, source_key in (
                ("source_file_sha256", "file_sha256"),
                ("source_build_id", "build_id"),
                ("source_text_sha256", "text_sha256"),
            ):
                if output_record.get(key) != artifact.get(source_key):
                    fail(f"prepared output input identity mismatch for {artifact_id}: {key}")
            if output_record.get("source_abi_security_identity") != abi_identity(artifact):
                fail(f"prepared output source ABI/security identity mismatch for {artifact_id}")
            verify_bolt_provenance(output_record)
            output_object = output_record.get("output_object")
            if not isinstance(output_object, str):
                fail(f"missing output object for {artifact_id}")
            output_path = path_from_relative(output_root, output_object)
            reject_symlink_components(str(output_path), "prepared output object")
            if not output_path.is_file() or output_path.is_symlink():
                fail(f"prepared output is absent or not regular: {output_path}")
            if sha256_file(output_path) != output_record.get("output_sha256"):
                fail(f"prepared output hash mismatch for {artifact_id}")
            output_class = classify_elf(
                output_path, readelf, objcopy, scratch_root / f"output-{index}"
            )
            if not output_class["has_bolt_info"] or not output_class["bolt_info_valid"]:
                fail(f"prepared output lacks a valid BOLT note: {artifact_id}")
            if ".bolt.org.text" not in output_class["bolt_origin_sections"]:
                fail(f"prepared output lacks BOLT transformation evidence: {artifact_id}")
            assert_abi_identity(output_class, artifact, f"prepared output {artifact_id}")
            if abi_identity(output_class) != output_record.get("abi_security_identity"):
                fail(f"prepared output recorded ABI/security identity mismatch for {artifact_id}")
            for key in (
                "bolt_info_sha256",
                "bolt_info_size",
                "bolt_info_description",
                "bolt_origin_sections",
            ):
                if output_class.get(key) != output_record.get(key):
                    fail(f"prepared output BOLT transformation identity mismatch: {artifact_id}: {key}")
            prepared.append(
                {
                    "artifact": artifact,
                    "paths": paths,
                    "output": output_path,
                    "current": scratch_root / f"diagnostic-source-{index}",
                }
            )
            copy_noatime(paths[0], prepared[-1]["current"], stats[0])

        # All identities, outputs, topology, and metadata are valid before ED mutation.
        staged_groups: list[list[tuple[Path, Path]]] = []
        token = f"{os.getpid()}"
        replacement_started = False
        try:
            for item in prepared:
                staged_groups.append(
                    stage_hardlink_group(
                        item["output"], item["paths"], item["artifact"]["metadata"], token
                    )
                )
            for item in prepared:
                diagnostic = diagnostics / f"{item['artifact']['artifact_id']}.elf"
                if diagnostic.exists():
                    reject_symlink_components(str(diagnostic), "diagnostic input")
                    if diagnostic.is_symlink() or not diagnostic.is_file():
                        fail(f"diagnostic input is not a regular file: {diagnostic}")
                    if sha256_file(diagnostic) != item["artifact"]["file_sha256"]:
                        fail(f"diagnostic input collision: {diagnostic}")
                else:
                    partial = diagnostic.with_name(f".{diagnostic.name}.partial.{os.getpid()}")
                    shutil.copyfile(item["current"], partial)
                    os.chmod(partial, 0o600)
                    os.replace(partial, diagnostic)
            replacement_started = True
            for stages in staged_groups:
                for partial, destination in stages:
                    os.replace(partial, destination)

            # Post-rename verification remains inside the rollback boundary.
            verify_symlinks(ed, capture.get("symlinks"))
            final_scratch = scratch_root / "final-verification"
            for index, item in enumerate(prepared):
                paths = item["paths"]
                stats = [path.lstat() for path in paths]
                if len({(entry.st_dev, entry.st_ino) for entry in stats}) != 1:
                    fail(
                        "deployed hardlink topology mismatch: "
                        f"{item['artifact']['artifact_id']}"
                    )
                verify_metadata_matches(paths[0], item["artifact"]["metadata"])
                final_class = classify_elf(
                    paths[0], readelf, objcopy, final_scratch / f"{index}"
                )
                if not final_class["has_bolt_info"]:
                    fail(f"deployed file lacks .note.bolt_info: {paths[0]}")
                expected = outputs_by_id[item["artifact"]["artifact_id"]]
                if sha256_file(paths[0]) != expected["output_sha256"]:
                    fail(f"deployed file hash mismatch: {paths[0]}")
                assert_abi_identity(
                    final_class,
                    item["artifact"],
                    f"deployed output {item['artifact']['artifact_id']}",
                )
                if abi_identity(final_class) != expected.get("abi_security_identity"):
                    fail(f"deployed ABI/security record mismatch: {paths[0]}")
                if not final_class["bolt_info_valid"] or ".bolt.org.text" not in final_class[
                    "bolt_origin_sections"
                ]:
                    fail(f"deployed file lacks BOLT transformation evidence: {paths[0]}")
        except BaseException:
            for stages in staged_groups:
                for partial, _ in stages:
                    partial.unlink(missing_ok=True)
            # Restore every group from exact preimages after any rename, including
            # post-rename verification or signal failures.
            if replacement_started:
                for item in prepared:
                    restore = stage_hardlink_group(
                        item["current"],
                        item["paths"],
                        item["artifact"]["metadata"],
                        f"restore-{token}",
                    )
                    for partial, destination in restore:
                        os.replace(partial, destination)
            raise
    print(output_manifest_path(cache, fingerprint))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--cache-root", required=True)
        command.add_argument("--fingerprint", required=True)
        command.add_argument(
            "--lock-timeout-seconds",
            type=positive_seconds,
            default=DEFAULT_LOCK_TIMEOUT_SECONDS,
        )
        command.add_argument(
            "--tool-timeout-seconds",
            type=positive_seconds,
            default=DEFAULT_TOOL_TIMEOUT_SECONDS,
        )
        command.add_argument(
            "--tool-kill-after-seconds",
            type=positive_seconds,
            default=DEFAULT_TOOL_KILL_AFTER_SECONDS,
        )
        command.add_argument("--test-mode", action="store_true")
        command.add_argument(
            "--test-lock-hold-seconds",
            type=nonnegative_seconds,
            default=0.0,
            help=argparse.SUPPRESS,
        )

    capture = subparsers.add_parser("capture", help="capture eligible unstripped ED inputs")
    add_common_options(capture)
    capture.add_argument("--ed", required=True)
    capture.add_argument(
        "--expected-eligible-count", type=nonnegative_integer, required=True
    )
    capture.add_argument("--zero-eligible-proof")
    capture.add_argument("--readelf", default="readelf")
    capture.add_argument("--objcopy", default="objcopy")
    capture.set_defaults(function=command_capture)

    register = subparsers.add_parser("register-output", help="register one prepared BOLT output")
    add_common_options(register)
    register.add_argument("--artifact-id", required=True)
    register.add_argument("--input", required=True)
    register.add_argument("--output", required=True)
    register.add_argument("--llvm-bolt", required=True)
    register.add_argument("--option-policy-revision", required=True)
    register.add_argument("--bolt-option", action="append", default=[])
    register.add_argument("--fdata", action="append", default=[])
    register.add_argument("--workload-evidence", action="append", default=[])
    register.add_argument("--profile-evidence", action="append", default=[])
    register.add_argument("--command-record", required=True)
    register.add_argument("--readelf", default="readelf")
    register.add_argument("--objcopy", default="objcopy")
    register.set_defaults(function=command_register)

    deploy = subparsers.add_parser("deploy", help="deploy exact prepared outputs into ED")
    add_common_options(deploy)
    deploy.add_argument("--ed", required=True)
    deploy.add_argument(
        "--expected-eligible-count", type=nonnegative_integer, required=True
    )
    deploy.add_argument("--zero-eligible-proof")
    deploy.add_argument("--readelf", default="readelf")
    deploy.add_argument("--objcopy", default="objcopy")
    deploy.set_defaults(function=command_deploy)
    return parser


def main() -> int:
    global TOOL_KILL_AFTER_SECONDS, TOOL_TIMEOUT_SECONDS
    parser = build_parser()
    arguments = parser.parse_args()
    TOOL_TIMEOUT_SECONDS = arguments.tool_timeout_seconds
    TOOL_KILL_AFTER_SECONDS = arguments.tool_kill_after_seconds
    handled_signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous_handlers: dict[signal.Signals, Any] = {}

    def interrupted(signum: int, _frame: Any) -> NoReturn:
        for item, previous in previous_handlers.items():
            signal.signal(item, previous)
        raise BoltArtifactError(f"interrupted by signal {signum}")

    for item in handled_signals:
        previous_handlers[item] = signal.getsignal(item)
        signal.signal(item, interrupted)
    try:
        fingerprint = validate_fingerprint(arguments.fingerprint)
        ed = (
            validate_portage_ed(arguments.ed, arguments.test_mode)
            if hasattr(arguments, "ed")
            else None
        )
        cache = validate_cache_root(arguments.cache_root, ed, arguments.test_mode)
        with fingerprint_lock(
            cache,
            fingerprint,
            arguments.lock_timeout_seconds,
            arguments.test_mode,
            arguments.test_lock_hold_seconds,
        ):
            arguments.function(arguments)
    except BoltArtifactError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130
    finally:
        for item, previous in previous_handlers.items():
            signal.signal(item, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

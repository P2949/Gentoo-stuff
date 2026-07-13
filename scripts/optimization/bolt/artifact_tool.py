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


SCHEMA_CAPTURE = "gentoo-optimization-bolt-capture-v3"
SCHEMA_OUTPUT = "gentoo-optimization-bolt-output-v3"
SCHEMA_COMMAND = "gentoo-optimization-bolt-command-v2"
SCHEMA_INVENTORY_PROOF = "gentoo-optimization-bolt-inventory-proof-v1"
SCHEMA_WORKLOAD_PROOF = "gentoo-optimization-bolt-workload-proof-v1"
SCHEMA_PROFILE_PROOF = "gentoo-optimization-bolt-profile-quality-proof-v1"
SCHEMA_FDATA_PROOF = "gentoo-optimization-bolt-fdata-quality-proof-v1"
SCHEMA_QUALITY_COMMAND = "gentoo-optimization-bolt-quality-command-v1"
FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
BUILD_ID_RE = re.compile(r"Build ID:\s*([0-9A-Fa-f]+)")
HEADER_RE = re.compile(
    r"^\s*(Class|Data|Version|OS/ABI|ABI Version|Type|Machine|Flags):\s*(.*?)\s*$"
)
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
    "elf_ident_version",
    "elf_header_version",
    "elf_osabi",
    "elf_abi_version",
    "elf_flags",
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
    "symbol_version_mappings",
    "cet_properties",
    "gnu_stack_policy",
    "has_gnu_relro",
    "bind_now",
    "dynamic_flags",
    "has_textrel",
    "has_writable_executable_load",
    "tls_segments",
)
ELF_MAGIC = b"\x7fELF"
SYSTEM_ROOT = Path("/usr")
PRODUCTION_READELF = "/usr/bin/readelf"
PRODUCTION_OBJCOPY = "/usr/bin/objcopy"
PRODUCTION_LLVM_BOLT = "/usr/lib/llvm/22/bin/llvm-bolt"
PRODUCTION_PROFILE_TOOLS = {
    "/usr/bin/perf",
    "/usr/lib/llvm/22/bin/perf2bolt",
}
PRODUCTION_MERGE_FDATA = "/usr/lib/llvm/22/bin/merge-fdata"
PRODUCTION_INVENTORY_VALIDATOR = (
    "/usr/local/libexec/gentoo-optimization/scripts/optimization/verify/reconcile-state.py"
)
FRAMEWORK_LOCK = Path("/run/gentoo-optimization/framework-install.lock")
PRODUCTION_CACHE_ROOT = Path("/var/cache/gentoo-optimization/bolt")
PRODUCTION_EVIDENCE_ROOTS = (
    Path("/var/cache/gentoo-optimization"),
    Path("/var/lib/gentoo-optimization"),
)
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
DEFAULT_TOOL_KILL_AFTER_SECONDS = 5.0
TOOL_TIMEOUT_SECONDS = DEFAULT_TOOL_TIMEOUT_SECONDS
TOOL_KILL_AFTER_SECONDS = DEFAULT_TOOL_KILL_AFTER_SECONDS
ACTIVE_TEST_MODE = False


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
    # Production ELF inspection and BOLT identity checks must not inherit
    # loader, Python, compiler-wrapper, locale, or tracing controls from an
    # ebuild.  The four fixture variables are an intentionally narrow escape
    # hatch used only by hermetic timeout/rollback tests.
    environment = {"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"}
    if ACTIVE_TEST_MODE:
        for name in (
            "HUNG_LOCALE_FILE",
            "HUNG_PID_FILE",
            "FAIL_PATH",
            "REAL_READELF",
        ):
            if name in os.environ:
                environment[name] = os.environ[name]
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


def validate_root_owned_nonwritable_chain(path: Path, label: str) -> None:
    """Require an existing path and every ancestor to be root-owned and trusted."""
    if not path.is_absolute():
        fail(f"{label} must be absolute: {path}")
    current = Path(path.anchor)
    root_info = current.lstat()
    if root_info.st_uid != 0 or stat.S_IMODE(root_info.st_mode) & 0o022:
        fail(f"{label} has an untrusted filesystem root: {current}")
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except OSError as error:
            fail(f"cannot inspect {label} trust ancestor {current}: {error}")
        if stat.S_ISLNK(info.st_mode):
            fail(f"{label} contains a symlink component: {current}")
        if info.st_uid != 0:
            fail(f"{label} has a non-root-owned component: {current}")
        if stat.S_IMODE(info.st_mode) & 0o022:
            fail(f"{label} has a group/world-writable component: {current}")


def validate_existing_root_owned_chain(path: Path, label: str) -> None:
    """Validate every existing ancestor before a trusted directory is created."""
    current = Path(path.anchor)
    for part in (current, *(path.parts[1:])):
        if isinstance(part, str):
            current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        except OSError as error:
            fail(f"cannot inspect {label} trust ancestor {current}: {error}")
        if stat.S_ISLNK(info.st_mode):
            fail(f"{label} contains a symlink component: {current}")
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            fail(f"{label} has an untrusted ancestor: {current}")


def validate_production_evidence_path(path: Path, label: str) -> None:
    if not any(path == root or root in path.parents for root in PRODUCTION_EVIDENCE_ROOTS):
        fail(
            f"production {label} is outside trusted optimization storage: {path}"
        )
    validate_root_owned_nonwritable_chain(path, label)


def validate_private_directory(path: Path, expected_uid: int, label: str) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        fail(f"{label} is not a directory: {path}")
    if info.st_uid != expected_uid:
        fail(f"{label} has wrong owner uid {info.st_uid}; expected {expected_uid}: {path}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        fail(f"{label} is group/world-writable: {path}")


@contextlib.contextmanager
def framework_publication_lock(test_mode: bool, timeout_seconds: float) -> Iterator[None]:
    """Serialize production work with framework publication before cache locks.

    The installer takes this lock and then every extant BOLT lock.  Taking it
    first here closes the otherwise unavoidable race in which a transaction
    could create its fingerprint lock after the installer enumerated locks.
    """
    if test_mode:
        yield
        return
    validate_root_owned_nonwritable_chain(FRAMEWORK_LOCK, "framework installer lock")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(FRAMEWORK_LOCK, flags)
    except OSError as error:
        fail(f"cannot open framework installer lock {FRAMEWORK_LOCK}: {error}")
    acquired = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            fail(f"framework installer lock is not a root-owned mode-0600 regular file: {FRAMEWORK_LOCK}")
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    fail(
                        f"timed out after {timeout_seconds:g}s waiting for framework installer lock: "
                        f"{FRAMEWORK_LOCK}"
                    )
                time.sleep(min(0.05, remaining))
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def resolve_elf_tool(value: str, expected: str, label: str, test_mode: bool) -> str:
    if not test_mode and value != expected:
        fail(f"production {label} must be exactly {expected}: got {value}")
    resolved = shutil.which(value)
    if resolved is None:
        fail(f"{label} is unavailable: {value}")
    requested = Path(resolved)
    try:
        path = requested.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve {label} {requested}: {error}")
    if not path.is_file() or not os.access(path, os.X_OK):
        fail(f"{label} is not an executable regular file: {path}")
    if not test_mode:
        expected_path = Path(expected)
        validate_root_owned_nonwritable_chain(expected_path.parent, f"{label} parent")
        link_info = expected_path.lstat()
        if link_info.st_uid != 0 or stat.S_IMODE(link_info.st_mode) & 0o022:
            fail(f"production {label} entry is untrusted: {expected_path}")
        validate_root_owned_nonwritable_chain(path, label)
    return str(path)


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
        validate_existing_root_owned_chain(raw, "cache root")
    raw.mkdir(mode=0o700, parents=True, exist_ok=True)
    reject_symlink_components(str(raw), "cache root")
    path = raw.resolve(strict=True)
    if path == Path("/") or path == SYSTEM_ROOT or SYSTEM_ROOT in path.parents:
        fail(f"refusing unsafe cache root: {path}")
    if ed is not None and (path == ed or ed in path.parents or path in ed.parents):
        fail("cache root and ED must be disjoint")
    validate_private_directory(path, os.geteuid() if test_mode else 0, "cache root")
    if not test_mode:
        validate_root_owned_nonwritable_chain(path, "cache root")
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
    load_segment_flags: list[str] = []
    tls_segments: list[dict[str, Any]] = []
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
        fields = stripped.split()
        if fields and fields[0] in ("LOAD", "TLS") and len(fields) >= 8:
            # readelf -lW emits offset/vaddr/paddr/filesz/memsz, then one or
            # more flag tokens, then alignment.  Preserve security-relevant
            # flags and exact TLS sizes/alignment, not layout addresses BOLT
            # is expected to change.
            flags = "".join(fields[6:-1])
            if fields[0] == "LOAD":
                load_segment_flags.append(flags)
            else:
                try:
                    tls_segments.append(
                        {
                            "file_size": int(fields[4], 16),
                            "memory_size": int(fields[5], 16),
                            "flags": flags,
                            "alignment": int(fields[-1], 16),
                        }
                    )
                except ValueError:
                    fail(f"cannot parse TLS program header: {stripped}")
    return {
        "interpreter": interpreter_match.group(1) if interpreter_match else None,
        "gnu_stack_policy": gnu_stack_policy,
        "has_gnu_relro": has_gnu_relro,
        "load_segment_flags": load_segment_flags,
        "has_writable_executable_load": any(
            "W" in flags and "E" in flags for flags in load_segment_flags
        ),
        "tls_segments": tls_segments,
    }


def parse_dynamic_identity(output: str) -> dict[str, Any]:
    needed: list[str] = []
    sonames: list[str] = []
    rpath: list[str] = []
    runpath: list[str] = []
    bind_now = False
    has_textrel = False
    dynamic_flags: list[dict[str, str]] = []
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
        if "(TEXTREL)" in line or re.search(r"\bFlags(?:_1)?:.*\bTEXTREL\b", line):
            has_textrel = True
        flag_match = re.search(r"\((FLAGS(?:_1)?)\)\s+(?:Flags:\s*)?(.*?)\s*$", line)
        if flag_match:
            dynamic_flags.append(
                {"tag": flag_match.group(1), "value": " ".join(flag_match.group(2).split())}
            )
    if len(sonames) > 1:
        fail(f"ELF has multiple DT_SONAME entries: {sonames}")
    return {
        "needed": needed,
        "soname": sonames[0] if sonames else None,
        "rpath": rpath,
        "runpath": runpath,
        "bind_now": bind_now,
        "dynamic_flags": dynamic_flags,
        "has_textrel": has_textrel,
    }


def parse_dynamic_symbols(output: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in output.splitlines():
        fields = line.split(maxsplit=7)
        if len(fields) < 8 or not fields[0].rstrip(":").isdigit():
            continue
        _, _, size, symbol_type, binding, visibility, index, name = fields
        if index == "UND" or binding not in ("GLOBAL", "WEAK"):
            continue
        if visibility not in ("DEFAULT", "PROTECTED") or not name:
            continue
        version_default = "@@" in name
        if "@@" in name:
            base_name, version = name.rsplit("@@", 1)
        elif "@" in name:
            base_name, version = name.rsplit("@", 1)
        else:
            base_name, version = name, None
        result.append(
            {
                "name": name,
                "base_name": base_name,
                "version": version,
                "version_default": version_default,
                "size": int(size, 0),
                "type": symbol_type,
                "binding": binding,
                "visibility": visibility,
            }
        )
    return sorted(result, key=lambda item: json.dumps(item, sort_keys=True))


def parse_symbol_versions(output: str) -> tuple[list[str], list[str], list[str]]:
    names = sorted(set(re.findall(r"\bName:\s*(\S+)", output)))
    files = sorted(set(re.findall(r"\bFile:\s*(\S+)", output)))
    mappings: list[str] = []
    for line in output.splitlines():
        normalized = " ".join(line.split())
        if not normalized or "Addr:" in normalized:
            continue
        # Version-definition/requirement record offsets are layout, not the
        # symbol-to-version mapping.  Strip only that offset while retaining
        # every index, name, parent, dependency and dynsym mapping.
        normalized = re.sub(r"^0x[0-9A-Fa-f]+:\s*", "", normalized)
        mappings.append(normalized)
    return names, files, mappings


def classify_elf(path: Path, readelf: str, objcopy: str, scratch: Path) -> dict[str, Any]:
    header_output = run_checked([readelf, "-hW", str(path)])
    headers: dict[str, str] = {}
    for line in header_output.splitlines():
        match = HEADER_RE.match(line)
        if match:
            value = match.group(2)
            key = match.group(1)
            if key == "Version":
                key = "Ident Version" if "Ident Version" not in headers else "Header Version"
            if key == "Type":
                value = value.split()[0]
            headers[key] = value
    missing_headers = {
        "Class", "Data", "Ident Version", "Header Version", "OS/ABI",
        "ABI Version", "Type", "Machine", "Flags"
    } - headers.keys()
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
    version_names, version_files, version_mappings = parse_symbol_versions(
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
        if section["name"].startswith(".bolt.org.")
        and section["size"] > 0
        and "X" in section["flags"]
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
        "elf_ident_version": headers["Ident Version"],
        "elf_header_version": headers["Header Version"],
        "elf_osabi": headers["OS/ABI"],
        "elf_abi_version": headers["ABI Version"],
        "elf_flags": headers["Flags"],
        "elf_type": headers["Type"],
        "elf_role": elf_role,
        "machine": headers["Machine"],
        **program_identity,
        **dynamic_identity,
        "exported_dynamic_symbols": dynamic_symbols,
        "symbol_version_names": version_names,
        "symbol_version_files": version_files,
        "symbol_version_mappings": version_mappings,
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


def directory_identity(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for directory, names, _files in os.walk(root, topdown=True, followlinks=False):
        names.sort()
        base = Path(directory)
        for name in names:
            path = base / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                continue
            if not stat.S_ISDIR(info.st_mode):
                fail(f"unsupported non-directory entry encountered: {path}")
            result.append(
                {
                    "path": safe_relative(path, root),
                    "metadata": metadata(path, info),
                }
            )
    return result


def collect_ed_identity(
    ed: Path,
    readelf: str,
    objcopy: str,
    scratch_root: Path,
    object_root: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Classify every ELF and identify every directory/file/link in ED."""
    regular_groups, symlinks = scan_tree(ed)
    artifacts: list[dict[str, Any]] = []
    regular_identity: list[dict[str, Any]] = []
    if object_root is not None:
        object_root.mkdir(mode=0o700)
    for index, (paths, info) in enumerate(regular_groups, 1):
        if info.st_nlink != len(paths):
            fail(
                "regular inode has hardlinks outside ED or changed during scan: "
                f"{paths[0]} (st_nlink={info.st_nlink}, discovered={len(paths)})"
            )
        source = path_from_relative(ed, paths[0])
        scratch = scratch_root / f"{index:06d}.file"
        copy_noatime(source, scratch, info)
        file_sha256 = sha256_file(scratch)
        classification: dict[str, Any] | None = None
        artifact_id: str | None = None
        cache_object: str | None = None
        if is_elf(scratch):
            classification = classify_elf(
                scratch, readelf, objcopy, scratch_root / f"{index:06d}.sections"
            )
            artifact_id = hashlib.sha256(paths[0].encode("utf-8")).hexdigest()
            if object_root is not None and classification["eligible"]:
                cache_object = f"objects/{artifact_id}.elf"
                destination = object_root.parent / cache_object
                os.replace(scratch, destination)
                os.chmod(destination, 0o600)
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "canonical_path": paths[0],
                    "paths": paths,
                    "hardlink_count": len(paths),
                    "source_device": info.st_dev,
                    "source_inode": info.st_ino,
                    "file_sha256": file_sha256,
                    "size": info.st_size,
                    "metadata": metadata(source, info),
                    "cache_object": cache_object,
                    **classification,
                }
            )
        if scratch.exists():
            scratch.unlink()
        regular_identity.append(
            {
                "paths": paths,
                "hardlink_count": len(paths),
                "file_sha256": file_sha256,
                "size": info.st_size,
                "metadata": metadata(source, info),
                "elf_artifact_id": artifact_id,
                "elf_identity": classification,
            }
        )
    return artifacts, {
        "directories": directory_identity(ed),
        "regular_groups": regular_identity,
        "symlinks": symlinks,
    }


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


def canonical_command_output(path_text: str) -> Path:
    candidate = Path(path_text)
    if not candidate.is_absolute() or ".." in candidate.parts:
        fail(f"BOLT command output path must be absolute and traversal-free: {path_text}")
    unresolved = reject_symlink_components(path_text, "BOLT command output")
    return unresolved.resolve(strict=False)


def inventory_candidate_identity(artifact: dict[str, Any]) -> dict[str, Any]:
    """Stable exact candidate facts required from the frozen inventory proof."""
    return {
        "artifact_id": artifact.get("artifact_id"),
        "canonical_path": artifact.get("canonical_path"),
        "paths": artifact.get("paths"),
        "hardlink_count": artifact.get("hardlink_count"),
        "elf_class": artifact.get("elf_class"),
        "elf_data": artifact.get("elf_data"),
        "elf_type": artifact.get("elf_type"),
        "machine": artifact.get("machine"),
        "elf_role": artifact.get("elf_role"),
    }


def inventory_proof(
    path_text: str | None, fingerprint: str, expected_count: int, test_mode: bool
) -> dict[str, Any]:
    if path_text is None:
        fail("every BOLT capture/deployment requires --inventory-proof")
    identity = provenance_file_record(path_text, "BOLT inventory proof", test_mode)
    document = load_json(Path(identity["path"]), SCHEMA_INVENTORY_PROOF)
    required = {
        "schema",
        "generation_id",
        "inventory_id",
        "cpv",
        "inventory_entry_sha256",
        "package_fingerprint",
        "expected_eligible_count",
        "inventory_evidence",
        "candidates",
    }
    if set(document) != required:
        fail("BOLT inventory proof fields differ from the strict schema")
    for field in ("generation_id", "inventory_id"):
        if not isinstance(document[field], str) or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", document[field]) is None:
            fail(f"BOLT inventory proof has an invalid {field}")
    cpv = document["cpv"]
    if not isinstance(cpv, str) or re.fullmatch(
        r"[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+", cpv
    ) is None:
        fail("BOLT inventory proof has an invalid exact CPV")
    if not isinstance(document["inventory_entry_sha256"], str) or FINGERPRINT_RE.fullmatch(
        document["inventory_entry_sha256"]
    ) is None:
        fail("BOLT inventory proof has an invalid inventory entry hash")
    if document["package_fingerprint"] != fingerprint:
        fail("BOLT inventory proof package fingerprint mismatch")
    if document["expected_eligible_count"] != expected_count:
        fail("BOLT inventory proof expected eligible count mismatch")
    evidence = validate_recorded_files(
        [document["inventory_evidence"]],
        "frozen inventory evidence",
        test_mode=test_mode,
    )[0]
    if evidence != document["inventory_evidence"]:
        fail("BOLT inventory proof frozen inventory identity mismatch")
    if test_mode:
        validator_path = Path(__file__).resolve().parents[3] / "scripts/optimization/verify/reconcile-state.py"
        validator_arguments = [
            "/usr/bin/python3", "-I", str(validator_path),
            "--validate-inventory-only", "--inventory", evidence["path"],
            "--fixture-roots",
        ]
    else:
        validator_path = Path(PRODUCTION_INVENTORY_VALIDATOR)
        validator_arguments = [
            "/usr/bin/python3", "-I", str(validator_path),
            "--validate-inventory-only", "--inventory", evidence["path"],
        ]
        validate_root_owned_nonwritable_chain(validator_path, "frozen inventory validator")
    validator_identity = file_record(str(validator_path), "frozen inventory validator")
    try:
        summary = json.loads(run_checked(validator_arguments))
    except json.JSONDecodeError as error:
        fail(f"frozen inventory validator emitted invalid JSON: {error}")
    if not isinstance(summary, dict) or set(summary) != {
        "inventory_sha256", "generation_id", "inventory_id", "package_count",
        "owned_path_count", "owned_directory_count", "cpvs",
    }:
        fail("frozen inventory validator summary differs from its strict interface")
    if summary["inventory_sha256"] != evidence["sha256"]:
        fail("frozen inventory validator hash differs from the proof evidence")
    if summary["generation_id"] != document["generation_id"] or summary["inventory_id"] != document["inventory_id"]:
        fail("BOLT inventory proof generation/inventory identity is self-asserted or mismatched")
    if cpv not in summary["cpvs"]:
        fail("BOLT inventory proof CPV is absent from the strict frozen inventory")
    try:
        frozen_inventory = json.loads(Path(evidence["path"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot reopen strict frozen inventory: {error}")
    package_entries = [
        item for item in frozen_inventory["packages"]
        if isinstance(item, dict) and item.get("cpv") == cpv
    ]
    if len(package_entries) != 1 or package_entries[0].get("entry_sha256") != document["inventory_entry_sha256"]:
        fail("BOLT inventory proof CPV entry hash is absent or mismatched")
    candidates = document["candidates"]
    if not isinstance(candidates, list) or len(candidates) != expected_count:
        fail("BOLT inventory proof candidate count differs from its expected count")
    expected_fields = {
        "artifact_id", "canonical_path", "paths", "hardlink_count", "elf_class",
        "elf_data", "elf_type", "machine", "elf_role",
    }
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or set(candidate) != expected_fields:
            fail(f"BOLT inventory candidate {index} differs from the strict schema")
        if not isinstance(candidate["paths"], list) or not candidate["paths"]:
            fail(f"BOLT inventory candidate {index} has no exact paths")
        if candidate["canonical_path"] != candidate["paths"][0]:
            fail(f"BOLT inventory candidate {index} canonical path mismatch")
        if candidate["artifact_id"] != hashlib.sha256(
            candidate["canonical_path"].encode("utf-8")
        ).hexdigest():
            fail(f"BOLT inventory candidate {index} artifact identity mismatch")
    if len({item["artifact_id"] for item in candidates}) != len(candidates):
        fail("BOLT inventory proof contains duplicate candidate identities")
    owned_paths = {
        item["path"] for item in frozen_inventory["owned_paths"]
        if isinstance(item, dict) and item.get("owner_cpv") == cpv
    }
    candidate_paths = {f"/{path}" for item in candidates for path in item["paths"]}
    if not candidate_paths <= owned_paths:
        fail("BOLT inventory proof candidate path is not owned by its exact frozen CPV")
    return {
        "identity": identity,
        "document": document,
        "validator": validator_identity,
        "validation_summary": summary,
    }


def bind_inventory_candidates(proof: dict[str, Any], artifacts: list[dict[str, Any]]) -> None:
    actual = sorted(
        (inventory_candidate_identity(item) for item in artifacts if item.get("eligible")),
        key=lambda item: str(item["artifact_id"]),
    )
    expected = sorted(
        proof["document"]["candidates"], key=lambda item: str(item["artifact_id"])
    )
    if actual != expected:
        fail(
            "captured BOLT candidate paths/artifact facts differ from the frozen inventory proof"
        )


def provenance_file_record(
    path_text: str, label: str, test_mode: bool
) -> dict[str, Any]:
    record = file_record(path_text, label)
    if not test_mode:
        validate_production_evidence_path(Path(record["path"]), label)
    return record


def validate_recorded_files(
    records: Any, label: str, *, test_mode: bool = True
) -> list[dict[str, Any]]:
    if not isinstance(records, list) or not records:
        fail(f"{label} records must be a nonempty list")
    validated: list[dict[str, Any]] = []
    for index, expected in enumerate(records):
        if not isinstance(expected, dict) or set(expected) != {"path", "sha256", "size"}:
            fail(f"invalid {label} record at index {index}")
        actual = provenance_file_record(
            str(expected.get("path", "")), f"{label} file", test_mode
        )
        if actual != expected:
            fail(f"{label} file identity mismatch: expected {expected}, got {actual}")
        validated.append(actual)
    if len({item["path"] for item in validated}) != len(validated):
        fail(f"{label} contains duplicate paths")
    return validated


def validate_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ) is None:
        fail(f"{label} has an invalid UTC timestamp")
    return value


def proof_binding(
    document: dict[str, Any], capture: dict[str, Any], artifact: dict[str, Any], label: str
) -> None:
    expected = {
        "generation_id": capture.get("generation_id"),
        "inventory_id": capture.get("inventory_id"),
        "package_fingerprint": capture.get("package_fingerprint"),
        "artifact_id": artifact.get("artifact_id"),
        "input_build_id": artifact.get("build_id"),
        "input_text_sha256": artifact.get("text_sha256"),
        "input_file_sha256": artifact.get("file_sha256"),
    }
    for key, value in expected.items():
        if document.get(key) != value:
            fail(f"{label} exact input/inventory binding mismatch: {key}")


def validate_fixture_quality(document: dict[str, Any], arguments: argparse.Namespace, label: str) -> None:
    fixture_only = document.get("fixture_only")
    if not isinstance(fixture_only, bool):
        fail(f"{label} fixture_only must be boolean")
    if fixture_only and not (arguments.test_mode and arguments.fixture_quality_mode):
        fail(f"{label} is synthetic fixture-only quality evidence")
    if arguments.fixture_quality_mode and not arguments.test_mode:
        fail("--fixture-quality-mode requires --test-mode")


def validate_quality_command(
    value: Any,
    *,
    role: str,
    test_mode: bool,
    fixture_only: bool,
) -> dict[str, Any] | None:
    if value is None:
        if fixture_only and test_mode:
            return None
        fail(f"production BOLT {role} proof lacks an exact sanitized command record")
    if not isinstance(value, dict) or set(value) != {"identity", "document"}:
        fail(f"BOLT {role} command record wrapper differs from the strict schema")
    identity = provenance_file_record(
        str(value["identity"].get("path", "")), f"BOLT {role} command record", test_mode
    )
    if identity != value["identity"]:
        fail(f"BOLT {role} command record identity mismatch")
    document = load_json(Path(identity["path"]), SCHEMA_QUALITY_COMMAND)
    if document != value["document"]:
        fail(f"BOLT {role} embedded command record differs from its exact file")
    required = {
        "schema", "role", "argv", "environment", "tool", "inputs", "stdout",
        "stderr", "exit_status", "started_at_utc", "completed_at_utc", "metrics",
    }
    if set(document) != required or document["role"] != role:
        fail(f"BOLT {role} command record fields/role differ from the strict schema")
    if document["environment"] != {"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"}:
        fail(f"BOLT {role} command record did not use the sanitized environment")
    tool = document["tool"]
    if not isinstance(tool, dict) or set(tool) != {"path", "sha256", "size"}:
        fail(f"BOLT {role} command record has an invalid tool identity")
    actual_tool = file_record(str(tool.get("path", "")), f"BOLT {role} tool")
    if actual_tool != tool:
        fail(f"BOLT {role} command tool identity mismatch")
    argv = document["argv"]
    if not isinstance(argv, list) or not argv or argv[0] != tool["path"] or not all(
        isinstance(argument, str) and argument for argument in argv
    ):
        fail(f"BOLT {role} command argv is not exact")
    if not test_mode:
        validate_root_owned_nonwritable_chain(Path(tool["path"]), f"BOLT {role} tool")
        expected_tools = {
            "perf-record": {"/usr/bin/perf"},
            "perf-report": {"/usr/bin/perf"},
            "perf2bolt": {"/usr/lib/llvm/22/bin/perf2bolt"},
            "merge-fdata": {PRODUCTION_MERGE_FDATA},
        }
        if role in expected_tools and tool["path"] not in expected_tools[role]:
            fail(f"BOLT {role} command uses an unreviewed production tool")
    validate_recorded_files(document["inputs"], f"BOLT {role} command inputs", test_mode=test_mode)
    validate_recorded_files([document["stdout"]], f"BOLT {role} stdout", test_mode=test_mode)
    validate_recorded_files([document["stderr"]], f"BOLT {role} stderr", test_mode=test_mode)
    started = validate_timestamp(document["started_at_utc"], f"BOLT {role} command")
    completed = validate_timestamp(document["completed_at_utc"], f"BOLT {role} command")
    if completed < started or document["exit_status"] != 0:
        fail(f"BOLT {role} command did not complete successfully")
    if not isinstance(document["metrics"], dict):
        fail(f"BOLT {role} command has no structured output metrics")
    return document


def validate_quality_proofs(
    arguments: argparse.Namespace,
    capture: dict[str, Any],
    artifact: dict[str, Any],
    input_record: dict[str, Any],
    fdata: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    workload_required = {
        "schema", "generation_id", "inventory_id", "package_fingerprint",
        "artifact_id", "input_build_id", "input_text_sha256", "input_file_sha256",
        "workload_id", "workload_definition", "workload_log",
        "command_record",
        "started_at_utc", "completed_at_utc", "exit_status",
        "repetitions", "functional_passed", "fixture_only",
    }
    workloads: list[dict[str, Any]] = []
    for path_text in arguments.workload_evidence:
        identity = provenance_file_record(
            path_text, "BOLT workload proof", arguments.test_mode
        )
        document = load_json(Path(identity["path"]), SCHEMA_WORKLOAD_PROOF)
        if set(document) != workload_required:
            fail("BOLT workload proof fields differ from the strict schema")
        proof_binding(document, capture, artifact, "BOLT workload proof")
        if not isinstance(document["workload_id"], str) or not document["workload_id"]:
            fail("BOLT workload proof has an invalid workload ID")
        for field in ("workload_definition", "workload_log"):
            values = validate_recorded_files(
                [document[field]], f"BOLT workload {field}", test_mode=arguments.test_mode
            )
            if values[0] != document[field]:
                fail(f"BOLT workload proof {field} identity mismatch")
        started = validate_timestamp(document["started_at_utc"], "BOLT workload proof")
        completed = validate_timestamp(document["completed_at_utc"], "BOLT workload proof")
        if completed < started or document["exit_status"] != 0:
            fail("BOLT workload proof did not complete successfully")
        if type(document["repetitions"]) is not int or document["repetitions"] < 1:
            fail("BOLT workload proof has no representative repetition")
        if document["functional_passed"] is not True:
            fail("BOLT workload proof lacks functional validation")
        validate_fixture_quality(document, arguments, "BOLT workload proof")
        workload_command = validate_quality_command(
            document["command_record"],
            role="workload",
            test_mode=arguments.test_mode,
            fixture_only=document["fixture_only"],
        )
        if workload_command is not None and workload_command["metrics"] != {
            "repetitions": document["repetitions"],
            "functional_passed": document["functional_passed"],
        }:
            fail("BOLT workload command output does not support the claimed result")
        if workload_command is not None:
            input_records = workload_command["inputs"]
            if document["workload_definition"] not in input_records or not any(
                item.get("sha256") == document["input_file_sha256"] for item in input_records
            ):
                fail("BOLT workload command does not bind definition and exact input")
        workloads.append({"identity": identity, "document": document})
    if not workloads:
        fail("at least one structured BOLT workload proof is required")

    profile_required = {
        "schema", "generation_id", "inventory_id", "package_fingerprint",
        "artifact_id", "input_build_id", "input_text_sha256", "input_file_sha256",
        "profile_tools", "events", "lbr_captured", "sample_count",
        "branch_entry_count", "ignored_samples", "mismatching_samples",
        "out_of_range_samples", "thresholds", "workload_contributors",
        "profile_files", "command_records", "fdata", "fixture_only",
    }
    profiles: list[dict[str, Any]] = []
    workload_identities = [item["identity"] for item in workloads]
    for path_text in arguments.profile_evidence:
        identity = provenance_file_record(
            path_text, "BOLT profile quality proof", arguments.test_mode
        )
        document = load_json(Path(identity["path"]), SCHEMA_PROFILE_PROOF)
        if set(document) != profile_required:
            fail("BOLT profile quality proof fields differ from the strict schema")
        proof_binding(document, capture, artifact, "BOLT profile quality proof")
        tools = document["profile_tools"]
        if not isinstance(tools, list) or not tools:
            fail("BOLT profile quality proof has no tool identities")
        actual_tools: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict) or set(tool) != {"path", "sha256", "size"}:
                fail("BOLT profile quality proof has an invalid tool identity")
            actual_tool = file_record(str(tool.get("path", "")), "BOLT profile tool")
            if actual_tool != tool:
                fail("BOLT profile quality proof tool identity mismatch")
            if not arguments.test_mode:
                validate_root_owned_nonwritable_chain(Path(actual_tool["path"]), "BOLT profile tool")
            actual_tools.append(actual_tool)
        if len({item["path"] for item in actual_tools}) != len(actual_tools):
            fail("BOLT profile quality proof has duplicate tool paths")
        if not arguments.test_mode and {item["path"] for item in actual_tools} != PRODUCTION_PROFILE_TOOLS:
            fail("BOLT profile quality proof does not use the exact reviewed production tools")
        events = document["events"]
        if not isinstance(events, list) or not events or not all(
            isinstance(item, str) and item for item in events
        ):
            fail("BOLT profile quality proof has no exact events")
        validate_recorded_files(
            document["profile_files"],
            "BOLT raw/profile report evidence",
            test_mode=arguments.test_mode,
        )
        if document["lbr_captured"] is not True:
            fail("BOLT profile quality proof lacks LBR capture")
        thresholds = document["thresholds"]
        threshold_fields = {
            "minimum_samples", "minimum_branch_entries", "maximum_ignored_samples",
            "maximum_mismatch_ratio", "maximum_out_of_range_ratio",
        }
        if not isinstance(thresholds, dict) or set(thresholds) != threshold_fields:
            fail("BOLT profile quality thresholds differ from the strict schema")
        for key in ("minimum_samples", "minimum_branch_entries", "maximum_ignored_samples"):
            if type(thresholds[key]) is not int or thresholds[key] < 0:
                fail(f"BOLT profile quality threshold is invalid: {key}")
        for key in ("maximum_mismatch_ratio", "maximum_out_of_range_ratio"):
            if type(thresholds[key]) not in (int, float) or not 0 <= thresholds[key] <= 1:
                fail(f"BOLT profile quality threshold is invalid: {key}")
        counts = (
            "sample_count", "branch_entry_count", "ignored_samples",
            "mismatching_samples", "out_of_range_samples",
        )
        if any(type(document[key]) is not int or document[key] < 0 for key in counts):
            fail("BOLT profile quality proof contains invalid counts")
        sample_count = document["sample_count"]
        branches = document["branch_entry_count"]
        denominator = max(sample_count, 1)
        if sample_count < thresholds["minimum_samples"] or branches < thresholds["minimum_branch_entries"]:
            fail("BOLT profile quality proof is below its sample/LBR thresholds")
        if document["ignored_samples"] > thresholds["maximum_ignored_samples"]:
            fail("BOLT profile quality proof exceeds ignored-sample threshold")
        if document["mismatching_samples"] / denominator > thresholds["maximum_mismatch_ratio"]:
            fail("BOLT profile quality proof exceeds mismatch-ratio threshold")
        if document["out_of_range_samples"] / denominator > thresholds["maximum_out_of_range_ratio"]:
            fail("BOLT profile quality proof exceeds out-of-range threshold")
        validate_fixture_quality(document, arguments, "BOLT profile quality proof")
        commands = document["command_records"]
        if not isinstance(commands, list):
            fail("BOLT profile quality command records are not a list")
        command_documents: dict[str, dict[str, Any]] = {}
        for role in ("perf-record", "perf-report", "perf2bolt"):
            matches = [
                value for value in commands
                if isinstance(value, dict)
                and isinstance(value.get("document"), dict)
                and value["document"].get("role") == role
            ]
            if document["fixture_only"] and arguments.test_mode and not matches:
                continue
            if len(matches) != 1:
                fail(f"BOLT profile quality proof requires one exact {role} command")
            validated_command = validate_quality_command(
                matches[0], role=role, test_mode=arguments.test_mode,
                fixture_only=document["fixture_only"],
            )
            assert validated_command is not None
            command_documents[role] = validated_command
        if not document["fixture_only"]:
            profile_files = document["profile_files"]
            if not all(
                item in command_documents["perf-report"]["inputs"] for item in profile_files
            ) or not all(
                item in command_documents["perf2bolt"]["inputs"] for item in profile_files
            ):
                fail("profile commands do not bind every exact perf/profile input")
            if not any(
                item.get("sha256") == document["input_file_sha256"]
                for item in command_documents["perf2bolt"]["inputs"]
            ):
                fail("perf2bolt command does not bind the exact captured ELF")
            report_metrics = command_documents["perf-report"]["metrics"]
            expected_metrics = {
                "events": document["events"],
                "lbr_captured": document["lbr_captured"],
                "sample_count": document["sample_count"],
                "branch_entry_count": document["branch_entry_count"],
            }
            if report_metrics != expected_metrics:
                fail("perf-report command output does not support profile sample/LBR claims")
            perf2bolt_metrics = command_documents["perf2bolt"]["metrics"]
            if perf2bolt_metrics != {
                "ignored_samples": document["ignored_samples"],
                "mismatching_samples": document["mismatching_samples"],
                "out_of_range_samples": document["out_of_range_samples"],
            }:
                fail("perf2bolt command output does not support profile quality claims")
        if not document["fixture_only"] and (
            thresholds["minimum_samples"] < 1000
            or thresholds["minimum_branch_entries"] < 10000
            or thresholds["maximum_mismatch_ratio"] > 0.05
            or thresholds["maximum_out_of_range_ratio"] > 0.05
        ):
            fail("production BOLT profile thresholds are weaker than the reviewed minima")
        if document["workload_contributors"] != workload_identities:
            fail("BOLT profile proof does not bind the exact workload contributors")
        if document["fdata"] != fdata:
            fail("BOLT profile proof does not bind the exact fdata inputs")
        profiles.append({"identity": identity, "document": document})
    if not profiles:
        fail("at least one structured BOLT profile quality proof is required")

    fdata_required = {
        "schema", "generation_id", "inventory_id", "package_fingerprint",
        "artifact_id", "input_build_id", "input_text_sha256", "input_file_sha256",
        "merge_tool", "fdata", "profile_contributors", "total_functions",
        "total_samples", "command_record", "fixture_only",
    }
    fdata_proofs: list[dict[str, Any]] = []
    profile_identities = [item["identity"] for item in profiles]
    for path_text in arguments.fdata_quality_evidence:
        identity = provenance_file_record(
            path_text, "BOLT fdata quality proof", arguments.test_mode
        )
        document = load_json(Path(identity["path"]), SCHEMA_FDATA_PROOF)
        if set(document) != fdata_required:
            fail("BOLT fdata quality proof fields differ from the strict schema")
        proof_binding(document, capture, artifact, "BOLT fdata quality proof")
        merge_tool = document["merge_tool"]
        if not isinstance(merge_tool, dict) or set(merge_tool) != {"path", "sha256", "size"}:
            fail("BOLT fdata quality proof has an invalid merge-tool identity")
        actual_merge = file_record(str(merge_tool.get("path", "")), "BOLT fdata merge tool")
        if actual_merge != merge_tool:
            fail("BOLT fdata quality proof merge-tool identity mismatch")
        if not arguments.test_mode:
            validate_root_owned_nonwritable_chain(Path(actual_merge["path"]), "BOLT fdata merge tool")
            if actual_merge["path"] != PRODUCTION_MERGE_FDATA:
                fail(
                    "BOLT fdata quality proof merge tool is not the reviewed merge-fdata path"
                )
        if document["fdata"] != fdata or document["profile_contributors"] != profile_identities:
            fail("BOLT fdata quality proof contributor/input binding mismatch")
        if type(document["total_functions"]) is not int or document["total_functions"] < 1:
            fail("BOLT fdata quality proof has no functions")
        if type(document["total_samples"]) is not int or document["total_samples"] < 1:
            fail("BOLT fdata quality proof has no samples")
        validate_fixture_quality(document, arguments, "BOLT fdata quality proof")
        merge_command = validate_quality_command(
            document["command_record"], role="merge-fdata",
            test_mode=arguments.test_mode, fixture_only=document["fixture_only"],
        )
        if merge_command is not None and merge_command["metrics"] != {
            "total_functions": document["total_functions"],
            "total_samples": document["total_samples"],
        }:
            fail("merge-fdata command output does not support fdata quality claims")
        if merge_command is not None and not all(
            item in merge_command["inputs"] for item in document["profile_contributors"]
        ):
            fail("merge-fdata command does not bind every exact profile contributor")
        fdata_proofs.append({"identity": identity, "document": document})
    if not fdata_proofs:
        fail("at least one structured BOLT fdata quality proof is required")
    return workloads, profiles, fdata_proofs


def llvm_bolt_identity(path_text: str, test_mode: bool) -> dict[str, Any]:
    record = file_record(path_text, "llvm-bolt executable")
    path = Path(record["path"])
    info = path.stat()
    if stat.S_IMODE(info.st_mode) & 0o022:
        fail(f"llvm-bolt executable is group/world-writable: {path}")
    if not test_mode:
        if str(path) != PRODUCTION_LLVM_BOLT:
            fail(
                f"production llvm-bolt must be exactly {PRODUCTION_LLVM_BOLT}: {path}"
            )
        validate_root_owned_nonwritable_chain(path, "llvm-bolt executable")
        if info.st_uid != 0:
            fail(f"production llvm-bolt executable is not root-owned: {path}")
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
        *(f"-data={item['path']}" for item in fdata),
        *APPROVED_BOLT_OPTIONS,
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
    fdata_quality: list[dict[str, Any]],
    test_mode: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    record_identity = provenance_file_record(
        path_text, "BOLT command record", test_mode
    )
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
        "fdata_quality_evidence",
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
        ("fdata_quality_evidence", fdata_quality),
    ):
        if document[field] != expected:
            fail(f"BOLT command record {field} identity mismatch")
    validate_recorded_files([document["stdout"]], "BOLT stdout", test_mode=test_mode)
    validate_recorded_files(
        [document["stderr"]], "BOLT stderr", test_mode=test_mode
    )
    return document, record_identity


def verify_bolt_provenance(
    record: dict[str, Any],
    test_mode: bool,
    capture: dict[str, Any],
    artifact: dict[str, Any],
    fixture_quality_mode: bool,
) -> None:
    if record.get("option_policy_revision") != POLICY_REVISION:
        fail("prepared output uses an unreviewed BOLT option-policy revision")
    if record.get("options") != APPROVED_BOLT_OPTIONS:
        fail("prepared output uses an unreviewed BOLT option list")
    recorded_tool = record.get("llvm_bolt")
    if not isinstance(recorded_tool, dict):
        fail("prepared output lacks an llvm-bolt identity")
    tool = llvm_bolt_identity(str(recorded_tool.get("path", "")), test_mode)
    if tool != recorded_tool:
        fail("prepared output llvm-bolt identity is stale or mismatched")
    for key, label in (
        ("fdata", "BOLT fdata"),
    ):
        validate_recorded_files(record.get(key), label, test_mode=test_mode)
    for key, label, schema in (
        ("workload_evidence", "BOLT workload proof", SCHEMA_WORKLOAD_PROOF),
        ("profile_evidence", "BOLT profile proof", SCHEMA_PROFILE_PROOF),
        ("fdata_quality_evidence", "BOLT fdata proof", SCHEMA_FDATA_PROOF),
    ):
        values = record.get(key)
        if not isinstance(values, list) or not values:
            fail(f"prepared output lacks {label}")
        for value in values:
            if not isinstance(value, dict) or set(value) != {"identity", "document"}:
                fail(f"prepared output has malformed {label}")
            actual = provenance_file_record(
                str(value["identity"].get("path", "")), label, test_mode
            )
            if actual != value["identity"] or load_json(Path(actual["path"]), schema) != value["document"]:
                fail(f"prepared output {label} identity/document mismatch")
    command_record = record.get("command_record")
    if not isinstance(command_record, dict):
        fail("prepared output lacks a command-record identity")
    actual = provenance_file_record(
        str(command_record.get("path", "")), "BOLT command record", test_mode
    )
    if actual != command_record:
        fail("prepared output command-record identity is stale or mismatched")
    command = load_json(Path(actual["path"]), SCHEMA_COMMAND)
    if command != record.get("command"):
        fail("prepared output embedded command record differs from its exact file")
    revalidation_arguments = argparse.Namespace(
        test_mode=test_mode,
        fixture_quality_mode=fixture_quality_mode,
        workload_evidence=[item["identity"]["path"] for item in record["workload_evidence"]],
        profile_evidence=[item["identity"]["path"] for item in record["profile_evidence"]],
        fdata_quality_evidence=[
            item["identity"]["path"] for item in record["fdata_quality_evidence"]
        ],
    )
    expected_workloads, expected_profiles, expected_fdata_quality = validate_quality_proofs(
        revalidation_arguments,
        capture,
        artifact,
        provenance_file_record(
            str(command.get("input", {}).get("path", "")), "exact BOLT input", test_mode
        ),
        record["fdata"],
    )
    if (
        expected_workloads != record["workload_evidence"]
        or expected_profiles != record["profile_evidence"]
        or expected_fdata_quality != record["fdata_quality_evidence"]
    ):
        fail("prepared output structured quality proof revalidation mismatch")
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
        ("fdata_quality_evidence", "BOLT fdata quality evidence"),
    ):
        if command.get(key) != record.get(key):
            fail(f"prepared output command/{key} binding mismatch")
    validate_recorded_files(
        [command.get("stdout")], "BOLT stdout", test_mode=test_mode
    )
    validate_recorded_files(
        [command.get("stderr")], "BOLT stderr", test_mode=test_mode
    )
    if command.get("exit_status") != 0:
        fail("prepared output command did not exit successfully")
    command_output = command.get("output")
    command_input = command.get("input")
    if not isinstance(command_output, dict) or not isinstance(command_input, dict):
        fail("prepared output command lacks exact input/output identities")
    if command_output.get("sha256") != record.get("output_sha256"):
        fail("prepared output command/output hash binding mismatch")
    if command_input.get("sha256") != record.get("source_file_sha256"):
        fail("prepared output command/input hash binding mismatch")


def command_capture(arguments: argparse.Namespace) -> None:
    ed = validate_portage_ed(arguments.ed, arguments.test_mode)
    cache = validate_cache_root(arguments.cache_root, ed, arguments.test_mode)
    fingerprint = validate_fingerprint(arguments.fingerprint)
    proof = inventory_proof(
        arguments.inventory_proof,
        fingerprint,
        arguments.expected_eligible_count,
        arguments.test_mode,
    )
    readelf = resolve_elf_tool(
        arguments.readelf, PRODUCTION_READELF, "readelf", arguments.test_mode
    )
    objcopy = resolve_elf_tool(
        arguments.objcopy, PRODUCTION_OBJCOPY, "objcopy", arguments.test_mode
    )

    inputs = cache / "inputs"
    inputs.mkdir(mode=0o700, exist_ok=True)
    reject_symlink_components(str(inputs), "capture input root")
    final = inputs / fingerprint
    stage = inputs / f"{fingerprint}.partial"
    if stage.exists():
        fail(f"stale capture stage exists: {stage}")
    stage.mkdir(mode=0o700)
    before = tree_snapshot(ed)
    try:
        with tempfile.TemporaryDirectory(prefix="classify-", dir=stage) as scratch_text:
            scratch_root = Path(scratch_text)
            artifacts, ed_identity = collect_ed_identity(
                ed, readelf, objcopy, scratch_root, stage / "objects"
            )
        elf_total = len(artifacts)
        eligible_total = sum(bool(item["eligible"]) for item in artifacts)
        after = tree_snapshot(ed)
        if before != after:
            fail("ED metadata/topology changed during capture")
        if eligible_total != arguments.expected_eligible_count:
            fail(
                "captured BOLT-eligible count differs from the frozen inventory: "
                f"expected={arguments.expected_eligible_count}, actual={eligible_total}"
            )
        bind_inventory_candidates(proof, artifacts)
        manifest = {
            "schema": SCHEMA_CAPTURE,
            "package_fingerprint": fingerprint,
            "ed_root": str(ed),
            "generation_id": proof["document"]["generation_id"],
            "inventory_id": proof["document"]["inventory_id"],
            "inventory_proof": proof,
            "regular_inode_groups_total": len(ed_identity["regular_groups"]),
            "elf_total": elf_total,
            "eligible_total": eligible_total,
            "expected_eligible_count": arguments.expected_eligible_count,
            "ineligible_total": elf_total - eligible_total,
            "artifacts": artifacts,
            "ed_identity": ed_identity,
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
    readelf = resolve_elf_tool(
        arguments.readelf, PRODUCTION_READELF, "readelf", arguments.test_mode
    )
    objcopy = resolve_elf_tool(
        arguments.objcopy, PRODUCTION_OBJCOPY, "objcopy", arguments.test_mode
    )
    capture_root, capture = capture_paths(cache, fingerprint)
    captured_proof = capture.get("inventory_proof")
    if not isinstance(captured_proof, dict) or not isinstance(
        captured_proof.get("identity"), dict
    ):
        fail("capture lacks its strict frozen inventory proof")
    captured_count = capture.get("expected_eligible_count")
    if type(captured_count) is not int or captured_count < 0:
        fail("capture has an invalid frozen expected eligible count")
    revalidated_proof = inventory_proof(
        str(captured_proof["identity"].get("path", "")),
        fingerprint,
        captured_count,
        arguments.test_mode,
    )
    if revalidated_proof != captured_proof:
        fail("capture frozen inventory proof is stale or mismatched")
    source_artifact = find_artifact(capture, arguments.artifact_id)
    input_unresolved = reject_symlink_components(arguments.input, "exact BOLT input")
    input_status = input_unresolved.lstat()
    if not stat.S_ISREG(input_status.st_mode):
        fail(f"exact BOLT input is not a regular file: {input_unresolved}")
    input_source = input_unresolved.resolve(strict=True)
    cache_object = source_artifact.get("cache_object")
    if not isinstance(cache_object, str):
        fail("captured artifact lacks its exact cache object path")
    expected_input = path_from_relative(capture_root, cache_object).resolve(strict=True)
    if not arguments.test_mode and input_source != expected_input:
        fail(
            "exact BOLT input path is not the captured immutable object: "
            f"expected={expected_input}, got={input_source}"
        )
    if not arguments.test_mode:
        validate_production_evidence_path(input_source, "exact BOLT input")
        validate_production_evidence_path(output_source, "prepared BOLT output")
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
    tool = llvm_bolt_identity(arguments.llvm_bolt, arguments.test_mode)
    fdata = [
        provenance_file_record(value, "BOLT fdata", arguments.test_mode)
        for value in arguments.fdata
    ]
    if not fdata:
        fail("at least one BOLT fdata file is required")
    if len({item["path"] for item in fdata}) != len(fdata):
        fail("duplicate BOLT fdata paths are forbidden")
    input_record = provenance_file_record(
        str(input_source), "exact BOLT input", arguments.test_mode
    )
    if input_record["sha256"] != source_artifact["file_sha256"]:
        fail("exact BOLT input changed while registration was validating it")
    workloads, profiles, fdata_quality = validate_quality_proofs(
        arguments, capture, source_artifact, input_record, fdata
    )
    prepared_output_record = provenance_file_record(
        str(output_source), "prepared BOLT output", arguments.test_mode
    )
    command_output = canonical_command_output(arguments.command_output_path)
    if not arguments.test_mode:
        if not any(
            command_output == root or root in command_output.parents
            for root in PRODUCTION_EVIDENCE_ROOTS
        ):
            fail(
                "production BOLT command output is outside trusted optimization "
                f"storage: {command_output}"
            )
        validate_root_owned_nonwritable_chain(
            command_output.parent, "BOLT command output parent"
        )
    output_record = {
        **prepared_output_record,
        "path": str(command_output),
    }
    expected_argv = expected_bolt_argv(
        tool, input_source, command_output, fdata
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
        fdata_quality=fdata_quality,
        test_mode=arguments.test_mode,
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
        ) or document.get("inventory_proof") != capture.get("inventory_proof"):
            fail("output manifest frozen inventory binding mismatch")
    else:
        document = {
            "schema": SCHEMA_OUTPUT,
            "package_fingerprint": fingerprint,
            "generation_id": capture.get("generation_id"),
            "inventory_id": capture.get("inventory_id"),
            "expected_eligible_count": capture.get("expected_eligible_count"),
            "inventory_proof": capture.get("inventory_proof"),
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
        partial_sha256 = sha256_file(partial)
        if partial_sha256 != prepared_output_record["sha256"]:
            fail("prepared BOLT output changed while registration was publishing it")
        with tempfile.TemporaryDirectory(
            prefix="bolt-published-classify-", dir=cache
        ) as published_temporary:
            published_classification = classify_elf(
                partial, readelf, objcopy, Path(published_temporary)
            )
        assert_abi_identity(
            published_classification, classification, "published prepared output"
        )
        for key in (
            "build_id",
            "text_sha256",
            "bolt_info_sha256",
            "bolt_info_size",
            "bolt_info_description",
            "bolt_origin_sections",
        ):
            if published_classification.get(key) != classification.get(key):
                fail(f"prepared BOLT output changed during registration: {key}")
        entry = {
            "artifact_id": arguments.artifact_id,
            "output_object": f"objects/{arguments.artifact_id}.bolt",
            "output_sha256": partial_sha256,
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
            "fdata_quality_evidence": fdata_quality,
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
    proof = inventory_proof(
        arguments.inventory_proof,
        fingerprint,
        arguments.expected_eligible_count,
        arguments.test_mode,
    )
    readelf = resolve_elf_tool(
        arguments.readelf, PRODUCTION_READELF, "readelf", arguments.test_mode
    )
    objcopy = resolve_elf_tool(
        arguments.objcopy, PRODUCTION_OBJCOPY, "objcopy", arguments.test_mode
    )
    _, capture = capture_paths(cache, fingerprint)
    if capture.get("expected_eligible_count") != arguments.expected_eligible_count:
        fail("deployment expected eligible count differs from captured frozen count")
    if capture.get("inventory_proof") != proof:
        fail("deployment inventory proof differs from captured proof")
    output_root = cache / "outputs" / fingerprint
    reject_symlink_components(str(output_root), "prepared output root")
    output_manifest = load_json(output_root / "manifest.json", SCHEMA_OUTPUT)
    if output_manifest.get("package_fingerprint") != fingerprint:
        fail("output manifest fingerprint mismatch")
    if output_manifest.get("expected_eligible_count") != arguments.expected_eligible_count:
        fail("output manifest expected eligible count mismatch")
    if output_manifest.get("inventory_proof") != proof:
        fail("output manifest inventory proof mismatch")
    output_entries = output_manifest.get("outputs")
    if not isinstance(output_entries, list):
        fail("output manifest outputs is not a list")
    outputs_by_id = {item.get("artifact_id"): item for item in output_entries}
    if len(outputs_by_id) != len(output_entries):
        fail("duplicate artifact IDs in output manifest")

    artifacts = capture.get("artifacts")
    if not isinstance(artifacts, list):
        fail("capture artifacts is not a list")
    eligible = [item for item in artifacts if item.get("eligible")]
    if len(eligible) != arguments.expected_eligible_count:
        fail(
            "deployment eligible artifact count differs from the frozen inventory: "
            f"expected={arguments.expected_eligible_count}, actual={len(eligible)}"
        )
    bind_inventory_candidates(proof, artifacts)
    if not eligible:
        # A zero-candidate deployment is a verified no-op, not an optimized
        # artifact claim.  It must still prove and rescan the complete ED.
        if output_entries:
            fail("zero-candidate deployment has unexpected registered outputs")
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
        current_artifacts, current_ed_identity = collect_ed_identity(
            ed, readelf, objcopy, scratch_root / "full-ed-rescan"
        )
        if current_ed_identity != capture.get("ed_identity"):
            fail(
                "complete ED topology/file/ELF classification differs from the captured input"
            )
        captured_artifact_identity = [
            {key: value for key, value in item.items() if key not in ("source_device", "source_inode", "cache_object")}
            for item in artifacts
        ]
        current_artifact_identity = [
            {key: value for key, value in item.items() if key not in ("source_device", "source_inode", "cache_object")}
            for item in current_artifacts
        ]
        if current_artifact_identity != captured_artifact_identity:
            fail("complete current ELF set/classification differs from captured input")
        if not eligible:
            print(output_manifest_path(cache, fingerprint))
            return
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
            verify_bolt_provenance(
                output_record,
                arguments.test_mode,
                capture,
                artifact,
                arguments.fixture_quality_mode,
            )
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
            if output_class["build_id"] != output_record.get("output_build_id"):
                fail(f"prepared output GNU build ID record mismatch: {artifact_id}")
            if output_class["text_sha256"] != output_record.get("output_text_sha256"):
                fail(f"prepared output .text identity record mismatch: {artifact_id}")
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
            verify_symlinks(ed, capture.get("ed_identity", {}).get("symlinks"))
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
                _restored_artifacts, restored_identity = collect_ed_identity(
                    ed, readelf, objcopy, scratch_root / "rollback-verification"
                )
                if restored_identity != capture.get("ed_identity"):
                    fail("rollback failed to restore exact ED bytes/topology/ELF identity")
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
            "--fixture-quality-mode",
            action="store_true",
            help=argparse.SUPPRESS,
        )
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
    capture.add_argument("--inventory-proof")
    capture.add_argument("--readelf", default=PRODUCTION_READELF)
    capture.add_argument("--objcopy", default=PRODUCTION_OBJCOPY)
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
    register.add_argument("--fdata-quality-evidence", action="append", default=[])
    register.add_argument("--command-record", required=True)
    register.add_argument("--command-output-path", required=True)
    register.add_argument("--readelf", default=PRODUCTION_READELF)
    register.add_argument("--objcopy", default=PRODUCTION_OBJCOPY)
    register.set_defaults(function=command_register)

    deploy = subparsers.add_parser("deploy", help="deploy exact prepared outputs into ED")
    add_common_options(deploy)
    deploy.add_argument("--ed", required=True)
    deploy.add_argument(
        "--expected-eligible-count", type=nonnegative_integer, required=True
    )
    deploy.add_argument("--inventory-proof")
    deploy.add_argument("--readelf", default=PRODUCTION_READELF)
    deploy.add_argument("--objcopy", default=PRODUCTION_OBJCOPY)
    deploy.set_defaults(function=command_deploy)
    return parser


def main() -> int:
    global ACTIVE_TEST_MODE, TOOL_KILL_AFTER_SECONDS, TOOL_TIMEOUT_SECONDS
    parser = build_parser()
    arguments = parser.parse_args()
    TOOL_TIMEOUT_SECONDS = arguments.tool_timeout_seconds
    TOOL_KILL_AFTER_SECONDS = arguments.tool_kill_after_seconds
    ACTIVE_TEST_MODE = arguments.test_mode
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
        with framework_publication_lock(
            arguments.test_mode, arguments.lock_timeout_seconds
        ):
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

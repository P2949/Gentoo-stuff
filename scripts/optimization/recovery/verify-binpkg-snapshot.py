#!/usr/bin/env python3
"""Strict, read-only verification of a Gentoo binary-package snapshot.

The verifier never extracts an archive and never modifies the snapshot or VDB.
Its output deliberately excludes timestamps and durations so repeated runs over
the same inputs produce byte-for-byte identical JSON or text reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Callable, Iterable, Protocol, cast


SCHEMA_VERSION = 1
BUFFER_SIZE = 1024 * 1024
MAX_MANIFEST_SIZE = 16 * 1024 * 1024
ZSTD_TEST_TIMEOUT_SECONDS = 30.0
ZSTD_TEST_KILL_AFTER_SECONDS = 2.0
ZSTD_TEST_MAX_STDERR = 64 * 1024
ZSTD_TEST_DRAIN_MAX_BYTES = 256 * 1024
ZSTD_TEST_DRAIN_MAX_READS = 8
CPV_RE = re.compile(r"^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+$")
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
INNER_ARCHIVE_RE = {
    "metadata": re.compile(
        r"^metadata\.tar(?:\.(?:gz|bz2|lz4|lz|lzo|xz|zst))?$"
    ),
    "image": re.compile(r"^image\.tar(?:\.(?:gz|bz2|lz4|lz|lzo|xz|zst))?$"),
}


class Digest(Protocol):
    @property
    def digest_size(self) -> int: ...

    def update(self, data: bytes) -> None: ...

    def hexdigest(self) -> str: ...


def _md5() -> Digest:
    try:
        return hashlib.md5(usedforsecurity=False)
    except TypeError:  # pragma: no cover - compatibility with old Python
        return hashlib.md5()


def _sha1() -> Digest:
    try:
        return hashlib.sha1(usedforsecurity=False)
    except TypeError:  # pragma: no cover - compatibility with old Python
        return hashlib.sha1()


HashFactory = Callable[[], Digest]
MANIFEST_HASHES: dict[str, HashFactory] = {
    "MD5": _md5,
    "SHA1": _sha1,
    "SHA256": hashlib.sha256,
    "SHA512": hashlib.sha512,
    "BLAKE2B": hashlib.blake2b,
    "BLAKE2S": hashlib.blake2s,
    "SHA3_256": hashlib.sha3_256,
    "SHA3_512": hashlib.sha3_512,
}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    cpv: str = ""
    path: str = ""
    record: int = 0

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "code": self.code,
            "message": self.message,
        }
        if self.cpv:
            result["cpv"] = self.cpv
        if self.path:
            result["path"] = self.path
        if self.record:
            result["record"] = self.record
        return result

    def sort_key(self) -> tuple[object, ...]:
        return (self.code, self.cpv, self.path, self.record, self.message)


@dataclass
class IndexRecord:
    number: int
    fields: dict[str, str]

    @property
    def cpv(self) -> str:
        return self.fields.get("CPV", "")

    @property
    def path(self) -> str:
        return self.fields.get("PATH", "")


def _absolute_display_path(path: Path) -> str:
    return os.path.abspath(os.fspath(path))


def _parse_stanza(
    lines: list[tuple[int, str]], stanza_number: int, issues: list[Issue]
) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line_number, line in lines:
        if ":" not in line:
            issues.append(
                Issue(
                    "index_malformed_line",
                    f"line {line_number} has no key/value separator",
                    record=stanza_number,
                )
            )
            continue
        key, value = line.split(":", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            issues.append(
                Issue(
                    "index_invalid_key",
                    f"line {line_number} has invalid key {key!r}",
                    record=stanza_number,
                )
            )
            continue
        value = value.lstrip(" \t")
        if key in fields:
            issues.append(
                Issue(
                    "index_duplicate_field",
                    f"field {key} occurs more than once",
                    record=stanza_number,
                )
            )
            continue
        fields[key] = value
    return fields


def parse_packages_index(
    index_path: Path, issues: list[Issue]
) -> tuple[dict[str, str], list[IndexRecord]]:
    try:
        index_stat = index_path.lstat()
    except OSError as exc:
        issues.append(
            Issue("packages_index_unreadable", f"cannot stat Packages index: {exc}")
        )
        return {}, []
    if not stat.S_ISREG(index_stat.st_mode):
        issues.append(
            Issue("packages_index_not_regular", "Packages index is not a regular file")
        )
        return {}, []

    try:
        text = index_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        issues.append(
            Issue("packages_index_unreadable", f"cannot read Packages index: {exc}")
        )
        return {}, []

    stanzas: list[tuple[int, dict[str, str]]] = []
    current: list[tuple[int, str]] = []
    stanza_number = 0
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.removesuffix("\r")
        if not line.strip():
            if current:
                stanza_number += 1
                stanzas.append(
                    (stanza_number, _parse_stanza(current, stanza_number, issues))
                )
                current = []
            continue
        current.append((line_number, line))
    if current:
        stanza_number += 1
        stanzas.append((stanza_number, _parse_stanza(current, stanza_number, issues)))

    if not stanzas:
        issues.append(Issue("packages_index_empty", "Packages index has no stanzas"))
        return {}, []

    header_number, header = stanzas[0]
    if "CPV" in header:
        issues.append(
            Issue(
                "packages_header_missing",
                "first Packages stanza is a package record, not a global header",
                record=header_number,
            )
        )
        header = {}
        record_stanzas = stanzas
    else:
        record_stanzas = stanzas[1:]

    records: list[IndexRecord] = []
    for number, fields in record_stanzas:
        if "CPV" not in fields:
            issues.append(
                Issue(
                    "index_record_missing_cpv",
                    "non-header stanza has no CPV field",
                    record=number,
                )
            )
            continue
        records.append(IndexRecord(number=number, fields=fields))

    version = header.get("VERSION")
    if version is None:
        issues.append(Issue("packages_header_missing_version", "header has no VERSION"))
    elif version != "0":
        issues.append(
            Issue(
                "packages_header_unsupported_version",
                f"unsupported Packages VERSION {version!r}",
            )
        )

    declared_count = header.get("PACKAGES")
    if declared_count is None:
        issues.append(
            Issue("packages_header_missing_count", "header has no PACKAGES count")
        )
    elif not declared_count.isdecimal():
        issues.append(
            Issue(
                "packages_header_invalid_count",
                f"PACKAGES count is not a non-negative integer: {declared_count!r}",
            )
        )
    elif int(declared_count) != len(records):
        issues.append(
            Issue(
                "packages_header_count_mismatch",
                f"header declares {declared_count} records but parsed {len(records)}",
            )
        )

    return header, records


def enumerate_live_cpvs(vdb: Path, issues: list[Issue]) -> list[str]:
    try:
        vdb_stat = vdb.lstat()
    except OSError as exc:
        issues.append(Issue("vdb_unreadable", f"cannot stat VDB: {exc}"))
        return []
    if not stat.S_ISDIR(vdb_stat.st_mode):
        issues.append(Issue("vdb_not_directory", "VDB path is not a directory"))
        return []

    cpvs: list[str] = []
    try:
        categories = sorted(vdb.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        issues.append(Issue("vdb_unreadable", f"cannot enumerate VDB: {exc}"))
        return []

    for category in categories:
        try:
            category_stat = category.lstat()
        except OSError as exc:
            issues.append(
                Issue(
                    "vdb_entry_unreadable",
                    f"cannot stat VDB category: {exc}",
                    path=category.name,
                )
            )
            continue
        if not stat.S_ISDIR(category_stat.st_mode):
            if not category.name.startswith("."):
                issues.append(
                    Issue(
                        "vdb_unexpected_entry",
                        "non-directory entry at VDB category level",
                        path=category.name,
                    )
                )
            continue
        try:
            packages = sorted(category.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            issues.append(
                Issue(
                    "vdb_category_unreadable",
                    f"cannot enumerate VDB category: {exc}",
                    path=category.name,
                )
            )
            continue
        for package in packages:
            try:
                package_stat = package.lstat()
            except OSError as exc:
                issues.append(
                    Issue(
                        "vdb_entry_unreadable",
                        f"cannot stat VDB package entry: {exc}",
                        path=f"{category.name}/{package.name}",
                    )
                )
                continue
            if stat.S_ISDIR(package_stat.st_mode):
                cpv = f"{category.name}/{package.name}"
                if not CPV_RE.fullmatch(cpv):
                    issues.append(
                        Issue(
                            "vdb_invalid_cpv",
                            "VDB package directory is not a syntactically safe CPV",
                            cpv=cpv,
                        )
                    )
                cpvs.append(cpv)
            elif not package.name.startswith("."):
                issues.append(
                    Issue(
                        "vdb_unexpected_entry",
                        "non-directory entry at VDB package level",
                        path=f"{category.name}/{package.name}",
                    )
                )
    return sorted(cpvs)


def _safe_relative_archive_path(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/") or "\x00" in value:
        return False
    raw_parts = value.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        return False
    parsed = PurePosixPath(value)
    return not parsed.is_absolute() and parsed.as_posix() == value


def _hash_archive(path: Path) -> tuple[int, str, str]:
    md5_hash = _md5()
    sha1_hash = _sha1()
    size = 0
    with path.open("rb") as archive:
        while True:
            chunk = archive.read(BUFFER_SIZE)
            if not chunk:
                break
            size += len(chunk)
            md5_hash.update(chunk)
            sha1_hash.update(chunk)
    return size, md5_hash.hexdigest(), sha1_hash.hexdigest()


def _scan_gpkg_archives(snapshot: Path, issues: list[Issue]) -> list[str]:
    found: list[str] = []

    def onerror(exc: OSError) -> None:
        filename = exc.filename or os.fspath(snapshot)
        try:
            relative = Path(filename).relative_to(snapshot).as_posix()
        except ValueError:
            relative = filename
        issues.append(
            Issue(
                "snapshot_scan_unreadable",
                f"cannot enumerate snapshot entry: {exc.strerror or exc}",
                path=relative,
            )
        )

    for root, dirnames, filenames in os.walk(
        snapshot, topdown=True, onerror=onerror, followlinks=False
    ):
        dirnames.sort()
        filenames.sort()
        root_path = Path(root)
        for name in sorted([*dirnames, *filenames]):
            if name.endswith(".gpkg.tar"):
                found.append((root_path / name).relative_to(snapshot).as_posix())
    return sorted(set(found))


def _read_limited(fileobj: IO[bytes], limit: int) -> bytes:
    data = fileobj.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"member exceeds {limit} byte safety limit")
    return data


def _cleartext_manifest_payload(text: str) -> str:
    marker = "-----BEGIN PGP SIGNED MESSAGE-----"
    if not text.startswith(marker):
        return text
    lines = text.splitlines()
    try:
        blank = lines.index("")
        signature = lines.index("-----BEGIN PGP SIGNATURE-----", blank + 1)
    except ValueError as exc:
        raise ValueError("malformed clear-signed Manifest") from exc
    payload: list[str] = []
    for line in lines[blank + 1 : signature]:
        payload.append(line[2:] if line.startswith("- ") else line)
    return "\n".join(payload) + "\n"


def _parse_gpkg_manifest(
    data: bytes,
) -> tuple[dict[str, tuple[int, dict[str, str]]], list[tuple[str, str]]]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("Manifest is not valid UTF-8") from exc
    text = _cleartext_manifest_payload(text)
    records: dict[str, tuple[int, dict[str, str]]] = {}
    problems: list[tuple[str, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 5 or fields[0] != "DATA" or (len(fields) - 3) % 2:
            problems.append(
                (
                    "gpkg_manifest_malformed_record",
                    f"Manifest line {line_number} is not a valid DATA record",
                )
            )
            continue
        filename = fields[1]
        if (
            not filename
            or filename in (".", "..", "Manifest")
            or "/" in filename
            or "\\" in filename
        ):
            problems.append(
                (
                    "gpkg_manifest_invalid_filename",
                    f"Manifest line {line_number} has invalid member name {filename!r}",
                )
            )
            continue
        if filename in records:
            problems.append(
                (
                    "gpkg_manifest_duplicate_record",
                    f"Manifest contains duplicate record for {filename}",
                )
            )
            continue
        if not fields[2].isdecimal():
            problems.append(
                (
                    "gpkg_manifest_invalid_size",
                    f"Manifest size for {filename} is not a non-negative integer",
                )
            )
            continue
        digests: dict[str, str] = {}
        malformed = False
        for offset in range(3, len(fields), 2):
            algorithm = fields[offset].upper()
            digest = fields[offset + 1].lower()
            if algorithm in digests:
                problems.append(
                    (
                        "gpkg_manifest_duplicate_hash",
                        f"Manifest repeats {algorithm} for {filename}",
                    )
                )
                malformed = True
                continue
            factory = MANIFEST_HASHES.get(algorithm)
            if factory is None:
                problems.append(
                    (
                        "gpkg_manifest_unsupported_hash",
                        f"Manifest uses unsupported hash {algorithm} for {filename}",
                    )
                )
                malformed = True
                continue
            expected_length = factory().digest_size * 2
            if len(digest) != expected_length or not HEX_RE.fullmatch(digest):
                problems.append(
                    (
                        "gpkg_manifest_invalid_digest",
                        f"Manifest has invalid {algorithm} digest for {filename}",
                    )
                )
                malformed = True
                continue
            digests[algorithm] = digest
        if not digests:
            problems.append(
                (
                    "gpkg_manifest_no_supported_hash",
                    f"Manifest has no supported digest for {filename}",
                )
            )
            malformed = True
        if not malformed:
            records[filename] = (int(fields[2]), digests)
    return records, problems


def _hash_tar_member(
    container: tarfile.TarFile,
    member: tarfile.TarInfo,
    algorithms: Iterable[str],
) -> tuple[int, dict[str, str]]:
    hashes = {name: MANIFEST_HASHES[name]() for name in algorithms}
    extracted = container.extractfile(member)
    if extracted is None:
        raise OSError("tar reader did not return a stream for regular member")
    size = 0
    with extracted:
        while True:
            chunk = extracted.read(BUFFER_SIZE)
            if not chunk:
                break
            size += len(chunk)
            for digest in hashes.values():
                digest.update(chunk)
    return size, {name: digest.hexdigest() for name, digest in hashes.items()}


def _process_group_has_live_members(process_group: int) -> bool:
    """Return conservatively whether a Linux process group can still execute."""
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return True
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            line = (entry / "stat").read_bytes()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        separator = line.rfind(b") ")
        if separator < 0:
            return True
        fields = line[separator + 2 :].split()
        if len(fields) < 3:
            return True
        try:
            member_process_group = int(fields[2])
        except ValueError:
            return True
        if member_process_group == process_group and fields[0] not in {
            b"Z",
            b"X",
            b"x",
        }:
            return True
    return False


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    kill_after_seconds: float,
    drain: Callable[[], None],
) -> str | None:
    """TERM/KILL one private process group and boundedly reap its leader."""

    # A completed wait releases the numeric PID/PGID identity.  Never signal
    # that number again, including if an asynchronous exception arrived in the
    # narrow caller window between wait() and its cleanup-complete assignment.
    if process.returncode is not None:
        return None

    def signal_group(signum: signal.Signals) -> None:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

    def drain_until(limit: float) -> None:
        while time.monotonic() < limit:
            drain()
            if not _process_group_has_live_members(process.pid):
                return
            time.sleep(min(0.02, max(0.0, limit - time.monotonic())))

    signal_group(signal.SIGTERM)
    drain_until(time.monotonic() + kill_after_seconds)
    if _process_group_has_live_members(process.pid):
        signal_group(signal.SIGKILL)
        drain_until(time.monotonic() + kill_after_seconds)
    drain()
    if _process_group_has_live_members(process.pid):
        return "zstd process group survived SIGKILL"
    try:
        process.wait(timeout=kill_after_seconds)
    except subprocess.TimeoutExpired:
        return "cannot reap zstd after SIGTERM/SIGKILL"
    return None


def _test_zstd_member(
    container: tarfile.TarFile,
    member: tarfile.TarInfo,
    zstd_program: str,
    *,
    timeout_seconds: float = ZSTD_TEST_TIMEOUT_SECONDS,
    kill_after_seconds: float = ZSTD_TEST_KILL_AFTER_SECONDS,
    temporary_directory: Path | None = None,
) -> tuple[bool, int | None, str]:
    """Boundedly test one image stream without trusting zstd to consume stdin.

    The member is first copied into a private temporary regular file and its
    exact tar-declared size is checked before zstd receives the path.  That
    prevents a zstd process which never opens its input from blocking the
    verifier in a pipe write.  One deadline is checked between every synchronous
    local staging read/write/flush and throughout child supervision.  It cannot
    interrupt an individual local filesystem syscall which never returns.  The
    child owns a private process group; timeout and exceptional cleanup signal
    that same group with TERM and then KILL before reaping the direct child.
    Each stderr-drain pass is bounded by the same deadline, a byte quota, and a
    read-count quota; only a bounded prefix is retained.  The reviewed
    production zstd is not expected to fork or escape this session; this is not
    containment for an adversarial executable which deliberately does so.
    """
    if timeout_seconds <= 0:
        raise ValueError("zstd timeout must be positive")
    if kill_after_seconds <= 0:
        raise ValueError("zstd kill-after timeout must be positive")

    deadline = time.monotonic() + timeout_seconds
    extracted = container.extractfile(member)
    if extracted is None:
        return False, None, "tar reader did not return an image stream"

    temporary_path: Path | None = None
    staged = None
    descriptor: int | None = None
    process: subprocess.Popen[bytes] | None = None
    process_cleanup_complete = False
    selector: selectors.BaseSelector | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".gentoo-binpkg-zstd-",
            dir=temporary_directory,
        )
        temporary_path = Path(raw_path)
        staged = os.fdopen(descriptor, "w+b")
        descriptor = None
        staged_size = 0
        with extracted:
            while True:
                if time.monotonic() >= deadline:
                    return (
                        False,
                        None,
                        f"zstd test timed out after {timeout_seconds:g} seconds "
                        "while staging the image stream",
                    )
                chunk = extracted.read(BUFFER_SIZE)
                if time.monotonic() >= deadline:
                    return (
                        False,
                        None,
                        f"zstd test timed out after {timeout_seconds:g} seconds "
                        "while staging the image stream",
                    )
                if not chunk:
                    break
                staged.write(chunk)
                staged_size += len(chunk)
                if time.monotonic() >= deadline:
                    return (
                        False,
                        None,
                        f"zstd test timed out after {timeout_seconds:g} seconds "
                        "while staging the image stream",
                    )
        if staged_size != member.size:
            return (
                False,
                None,
                "staged image stream size mismatch: "
                f"read {staged_size} bytes, expected {member.size}",
            )
        if time.monotonic() >= deadline:
            return (
                False,
                None,
                f"zstd test timed out after {timeout_seconds:g} seconds "
                "while staging the image stream",
            )
        staged.flush()
        if time.monotonic() >= deadline:
            return (
                False,
                None,
                f"zstd test timed out after {timeout_seconds:g} seconds "
                "while staging the image stream",
            )

        try:
            process = subprocess.Popen(
                [zstd_program, "--quiet", "--test", os.fspath(temporary_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return False, None, f"cannot execute zstd: {exc}"

        assert process.stderr is not None
        stderr_descriptor = process.stderr.fileno()
        os.set_blocking(stderr_descriptor, False)
        selector = selectors.DefaultSelector()
        selector.register(stderr_descriptor, selectors.EVENT_READ)
        stderr_prefix = bytearray()
        stderr_truncated = False
        stderr_eof = False

        def drain_stderr(wait_seconds: float) -> None:
            nonlocal stderr_eof, stderr_truncated
            if stderr_eof:
                remaining_time = deadline - time.monotonic()
                if remaining_time > 0 and wait_seconds > 0:
                    time.sleep(min(wait_seconds, remaining_time))
                return
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                return
            events = selector.select(min(wait_seconds, remaining_time))
            drained_bytes = 0
            read_count = 0
            for key, _mask in events:
                while (
                    read_count < ZSTD_TEST_DRAIN_MAX_READS
                    and drained_bytes < ZSTD_TEST_DRAIN_MAX_BYTES
                    and time.monotonic() < deadline
                ):
                    try:
                        data = os.read(
                            key.fd,
                            min(
                                ZSTD_TEST_MAX_STDERR,
                                ZSTD_TEST_DRAIN_MAX_BYTES - drained_bytes,
                            ),
                        )
                    except BlockingIOError:
                        break
                    read_count += 1
                    drained_bytes += len(data)
                    if not data:
                        stderr_eof = True
                        selector.unregister(key.fd)
                        break
                    remaining = ZSTD_TEST_MAX_STDERR - len(stderr_prefix)
                    if remaining > 0:
                        stderr_prefix.extend(data[:remaining])
                    if len(data) > max(remaining, 0):
                        stderr_truncated = True

        timed_out = False
        cleanup_error: str | None = None
        try:
            while True:
                group_live = _process_group_has_live_members(process.pid)
                if not group_live and stderr_eof:
                    returncode = process.wait(timeout=kill_after_seconds)
                    process_cleanup_complete = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    cleanup_error = _terminate_process_group(
                        process, kill_after_seconds, lambda: drain_stderr(0)
                    )
                    process_cleanup_complete = cleanup_error is None
                    returncode = process.returncode
                    break
                drain_stderr(min(0.05, remaining))
        except BaseException:
            exceptional_cleanup_error = _terminate_process_group(
                process, kill_after_seconds, lambda: drain_stderr(0)
            )
            process_cleanup_complete = exceptional_cleanup_error is None
            raise
        finally:
            selector.close()
            selector = None
            process.stderr.close()

        stderr = bytes(stderr_prefix).decode("utf-8", errors="replace").strip()
        stderr = stderr.replace(os.fspath(temporary_path), "<staged-image>")
        if stderr_truncated:
            stderr = f"{stderr}\n[stderr truncated]" if stderr else "[stderr truncated]"
        if cleanup_error is not None:
            return False, returncode, cleanup_error
        if timed_out:
            return (
                False,
                returncode,
                f"zstd test timed out after {timeout_seconds:g} seconds",
            )
        if returncode == 0:
            return True, returncode, ""
        first_line = stderr.splitlines()[0] if stderr else "zstd rejected the stream"
        if len(first_line) > 400:
            first_line = f"{first_line[:400]} [diagnostic truncated]"
        elif stderr_truncated:
            first_line = f"{first_line} [stderr truncated]"
        return False, returncode, first_line
    except OSError as exc:
        return False, None, f"cannot stage image stream for zstd: {exc}"
    finally:
        if process is not None and not process_cleanup_complete:
            _terminate_process_group(process, kill_after_seconds, lambda: None)
        if selector is not None:
            selector.close()
        extracted.close()
        if descriptor is not None:
            os.close(descriptor)
        if staged is not None:
            staged.close()
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def validate_gpkg(
    archive_path: Path,
    display_path: str,
    cpv: str,
    zstd_program: str,
) -> tuple[dict[str, object], list[Issue]]:
    issues: list[Issue] = []
    result: dict[str, object] = {
        "image_tar_zst_streams": 0,
        "manifest_members_verified": 0,
        "status": "failed",
        "zstd_streams_tested": 0,
    }

    def issue(code: str, message: str) -> None:
        issues.append(Issue(code, message, cpv=cpv, path=display_path))

    try:
        container = tarfile.open(archive_path, mode="r:")
    except (OSError, tarfile.TarError) as exc:
        issue("gpkg_outer_tar_invalid", f"cannot open uncompressed outer tar: {exc}")
        return result, issues

    with container:
        try:
            members = container.getmembers()
        except (OSError, tarfile.TarError) as exc:
            issue("gpkg_outer_tar_invalid", f"cannot enumerate outer tar: {exc}")
            return result, issues

        if container.pax_headers:
            issue("gpkg_outer_tar_pax_extension", "outer tar has global PAX headers")
        if not members:
            issue("gpkg_outer_tar_empty", "outer tar has no members")
            return result, issues

        names: set[str] = set()
        basenames: dict[str, tarfile.TarInfo] = {}
        prefixes: set[str] = set()
        structural_failure = False
        for member in members:
            name = member.name
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or name.startswith("/")
                or "\\" in name
                or len(path.parts) != 2
                or any(part in ("", ".", "..") for part in path.parts)
            ):
                issue(
                    "gpkg_outer_invalid_member_path",
                    f"outer member has unsafe or non-canonical path {name!r}",
                )
                structural_failure = True
                continue
            if name in names:
                issue(
                    "gpkg_outer_duplicate_member",
                    f"outer tar contains duplicate member {name}",
                )
                structural_failure = True
                continue
            names.add(name)
            prefix, basename = path.parts
            prefixes.add(prefix)
            if basename in basenames:
                issue(
                    "gpkg_outer_duplicate_basename",
                    f"outer tar contains duplicate member basename {basename}",
                )
                structural_failure = True
            else:
                basenames[basename] = member
            if not member.isfile() or member.issparse():
                issue(
                    "gpkg_outer_non_regular_member",
                    f"outer member is not a non-sparse regular file: {name}",
                )
                structural_failure = True
            if member.pax_headers:
                issue(
                    "gpkg_outer_tar_pax_extension",
                    f"outer member uses a PAX extension: {name}",
                )
                structural_failure = True

        if len(prefixes) != 1:
            issue(
                "gpkg_outer_multiple_prefixes",
                "outer members are not contained in exactly one package directory",
            )
            structural_failure = True

        for required in ("gpkg-1", "Manifest"):
            if required not in basenames:
                issue(
                    "gpkg_required_member_missing",
                    f"required outer member {required} is absent",
                )
                structural_failure = True
        for kind, matcher in INNER_ARCHIVE_RE.items():
            matches = sorted(name for name in basenames if matcher.fullmatch(name))
            if len(matches) != 1:
                issue(
                    "gpkg_required_member_count",
                    f"expected exactly one {kind} archive, found {len(matches)}",
                )
                structural_failure = True

        image_zstd = sorted(
            (member for name, member in basenames.items() if name == "image.tar.zst"),
            key=lambda item: item.name,
        )
        result["image_tar_zst_streams"] = len(image_zstd)

        if structural_failure:
            return result, issues

        manifest_member = basenames["Manifest"]
        try:
            manifest_stream = container.extractfile(manifest_member)
            if manifest_stream is None:
                raise OSError("tar reader did not return a Manifest stream")
            with manifest_stream:
                manifest_data = _read_limited(manifest_stream, MAX_MANIFEST_SIZE)
            manifest, manifest_problems = _parse_gpkg_manifest(manifest_data)
        except (OSError, tarfile.TarError, ValueError) as exc:
            issue("gpkg_manifest_unreadable", str(exc))
            return result, issues

        for code, message in manifest_problems:
            issue(code, message)
        if manifest_problems:
            return result, issues

        expected_members = set(basenames) - {"Manifest"}
        manifest_members = set(manifest)
        for missing in sorted(expected_members - manifest_members):
            issue(
                "gpkg_manifest_member_missing",
                f"outer member {missing} has no Manifest record",
            )
        for extra in sorted(manifest_members - expected_members):
            issue(
                "gpkg_manifest_member_extra",
                f"Manifest names absent outer member {extra}",
            )
        if expected_members != manifest_members:
            return result, issues

        manifest_verified = True
        verified_count = 0
        for basename in sorted(expected_members):
            member = basenames[basename]
            expected_size, expected_hashes = manifest[basename]
            if member.size != expected_size:
                issue(
                    "gpkg_manifest_size_mismatch",
                    f"Manifest SIZE mismatch for {basename}: expected "
                    f"{expected_size}, tar header has {member.size}",
                )
                manifest_verified = False
                continue
            try:
                actual_size, actual_hashes = _hash_tar_member(
                    container, member, sorted(expected_hashes)
                )
            except (OSError, tarfile.TarError) as exc:
                issue(
                    "gpkg_member_unreadable",
                    f"cannot read outer member {basename}: {exc}",
                )
                manifest_verified = False
                continue
            if actual_size != expected_size:
                issue(
                    "gpkg_member_short_read",
                    f"read {actual_size} bytes for {basename}, expected {expected_size}",
                )
                manifest_verified = False
                continue
            member_ok = True
            for algorithm in sorted(expected_hashes):
                if actual_hashes[algorithm].lower() != expected_hashes[algorithm]:
                    issue(
                        "gpkg_manifest_digest_mismatch",
                        f"Manifest {algorithm} mismatch for {basename}",
                    )
                    manifest_verified = False
                    member_ok = False
            if member_ok:
                verified_count += 1
        result["manifest_members_verified"] = verified_count
        if not manifest_verified:
            return result, issues

        tested = 0
        for member in image_zstd:
            ok, returncode, detail = _test_zstd_member(
                container, member, zstd_program
            )
            if ok:
                tested += 1
            else:
                suffix = "" if returncode is None else f" (exit {returncode})"
                issue(
                    "gpkg_image_zstd_invalid",
                    f"zstd test failed for {member.name}{suffix}: {detail}",
                )
        result["zstd_streams_tested"] = tested
        if not issues:
            result["status"] = "verified"
    return result, issues


def verify_snapshot(
    snapshot_arg: Path,
    vdb_arg: Path,
    allow_extra_archives: bool,
    validate_gpkg_archives: bool,
    zstd_program: str,
) -> dict[str, object]:
    issues: list[Issue] = []
    snapshot_display = _absolute_display_path(snapshot_arg)
    vdb_display = _absolute_display_path(vdb_arg)

    try:
        snapshot_stat = snapshot_arg.lstat()
    except OSError as exc:
        issues.append(Issue("snapshot_unreadable", f"cannot stat snapshot: {exc}"))
        snapshot = snapshot_arg
    else:
        snapshot = snapshot_arg
        if not stat.S_ISDIR(snapshot_stat.st_mode):
            issues.append(
                Issue("snapshot_not_directory", "snapshot path is not a directory")
            )

    index_path = snapshot / "Packages"
    _, records = parse_packages_index(index_path, issues)
    live_cpvs = enumerate_live_cpvs(vdb_arg, issues)
    live_set = set(live_cpvs)

    records_by_cpv: dict[str, list[IndexRecord]] = defaultdict(list)
    for record in records:
        cpv = record.cpv
        if not CPV_RE.fullmatch(cpv):
            issues.append(
                Issue(
                    "index_invalid_cpv",
                    "CPV is syntactically invalid",
                    cpv=cpv,
                    record=record.number,
                )
            )
        records_by_cpv[cpv].append(record)

    missing_live_cpvs: list[str] = []
    duplicate_live_cpvs: dict[str, list[str]] = {}
    for cpv in live_cpvs:
        matches = records_by_cpv.get(cpv, [])
        if not matches:
            missing_live_cpvs.append(cpv)
            issues.append(
                Issue(
                    "live_cpv_missing_archive",
                    "live CPV has no indexed archive",
                    cpv=cpv,
                )
            )
        elif len(matches) != 1:
            paths = sorted(record.path for record in matches)
            duplicate_live_cpvs[cpv] = paths
            issues.append(
                Issue(
                    "live_cpv_archive_count",
                    f"live CPV has {len(matches)} indexed archives; exactly one is required",
                    cpv=cpv,
                )
            )

    extra_indexed: list[dict[str, object]] = []
    for record in records:
        if record.cpv not in live_set:
            extra = {
                "cpv": record.cpv,
                "path": record.path,
                "record": record.number,
            }
            extra_indexed.append(extra)
            if not allow_extra_archives:
                issues.append(
                    Issue(
                        "extra_indexed_archive",
                        "indexed archive CPV is not installed in the live VDB",
                        cpv=record.cpv,
                        path=record.path,
                        record=record.number,
                    )
                )

    archive_results: list[dict[str, object]] = []
    indexed_safe_paths: set[str] = set()
    path_records: dict[str, list[IndexRecord]] = defaultdict(list)
    hash_cache: dict[Path, tuple[int, str, str]] = {}
    gpkg_validated = 0
    image_zstd_tested = 0

    for record in records:
        cpv = record.cpv
        relative_path = record.path
        archive_result: dict[str, object] = {
            "cpv": cpv,
            "exists": False,
            "gpkg": {
                "image_tar_zst_streams": 0,
                "manifest_members_verified": 0,
                "status": "not_requested",
                "zstd_streams_tested": 0,
            },
            "md5": {"actual": None, "expected": record.fields.get("MD5")},
            "path": relative_path,
            "record": record.number,
            "regular": False,
            "sha1": {"actual": None, "expected": record.fields.get("SHA1")},
            "size": {"actual": None, "expected": record.fields.get("SIZE")},
        }
        archive_results.append(archive_result)

        for required in ("PATH", "SIZE", "MD5", "SHA1"):
            if not record.fields.get(required):
                issues.append(
                    Issue(
                        f"index_record_missing_{required.lower()}",
                        f"indexed archive record has no {required}",
                        cpv=cpv,
                        path=relative_path,
                        record=record.number,
                    )
                )

        if not _safe_relative_archive_path(relative_path):
            issues.append(
                Issue(
                    "index_unsafe_archive_path",
                    "indexed PATH is absolute, non-canonical, or traverses directories",
                    cpv=cpv,
                    path=relative_path,
                    record=record.number,
                )
            )
            continue

        indexed_safe_paths.add(relative_path)
        path_records[relative_path].append(record)
        candidate = snapshot / relative_path
        try:
            candidate_stat = candidate.lstat()
        except OSError as exc:
            issues.append(
                Issue(
                    "indexed_archive_missing",
                    f"indexed PATH cannot be statted: {exc}",
                    cpv=cpv,
                    path=relative_path,
                    record=record.number,
                )
            )
            continue
        archive_result["exists"] = True
        if not stat.S_ISREG(candidate_stat.st_mode):
            issues.append(
                Issue(
                    "indexed_archive_not_regular",
                    "indexed PATH is not a regular file",
                    cpv=cpv,
                    path=relative_path,
                    record=record.number,
                )
            )
            continue
        archive_result["regular"] = True

        try:
            resolved_candidate = candidate.resolve(strict=True)
            resolved_snapshot = snapshot.resolve(strict=True)
            resolved_candidate.relative_to(resolved_snapshot)
        except (OSError, ValueError) as exc:
            issues.append(
                Issue(
                    "indexed_archive_escapes_snapshot",
                    f"indexed archive does not resolve inside snapshot: {exc}",
                    cpv=cpv,
                    path=relative_path,
                    record=record.number,
                )
            )
            continue

        expected_size = record.fields.get("SIZE", "")
        if expected_size and not expected_size.isdecimal():
            issues.append(
                Issue(
                    "index_invalid_size",
                    f"SIZE is not a non-negative integer: {expected_size!r}",
                    cpv=cpv,
                    path=relative_path,
                    record=record.number,
                )
            )
        expected_md5 = record.fields.get("MD5", "")
        if expected_md5 and (
            len(expected_md5) != 32 or not HEX_RE.fullmatch(expected_md5)
        ):
            issues.append(
                Issue(
                    "index_invalid_md5",
                    "MD5 is not exactly 32 hexadecimal characters",
                    cpv=cpv,
                    path=relative_path,
                    record=record.number,
                )
            )
        expected_sha1 = record.fields.get("SHA1", "")
        if expected_sha1 and (
            len(expected_sha1) != 40 or not HEX_RE.fullmatch(expected_sha1)
        ):
            issues.append(
                Issue(
                    "index_invalid_sha1",
                    "SHA1 is not exactly 40 hexadecimal characters",
                    cpv=cpv,
                    path=relative_path,
                    record=record.number,
                )
            )

        try:
            if resolved_candidate not in hash_cache:
                hash_cache[resolved_candidate] = _hash_archive(resolved_candidate)
            actual_size, actual_md5, actual_sha1 = hash_cache[resolved_candidate]
        except OSError as exc:
            issues.append(
                Issue(
                    "indexed_archive_unreadable",
                    f"cannot read indexed archive: {exc}",
                    cpv=cpv,
                    path=relative_path,
                    record=record.number,
                )
            )
            continue

        archive_result["size"] = {
            "actual": actual_size,
            "expected": expected_size or None,
        }
        archive_result["md5"] = {
            "actual": actual_md5,
            "expected": expected_md5 or None,
        }
        archive_result["sha1"] = {
            "actual": actual_sha1,
            "expected": expected_sha1 or None,
        }
        if expected_size.isdecimal() and actual_size != int(expected_size):
            issues.append(
                Issue(
                    "indexed_archive_size_mismatch",
                    f"SIZE mismatch: expected {expected_size}, got {actual_size}",
                    cpv=cpv,
                    path=relative_path,
                    record=record.number,
                )
            )
        if len(expected_md5) == 32 and HEX_RE.fullmatch(expected_md5):
            if actual_md5 != expected_md5.lower():
                issues.append(
                    Issue(
                        "indexed_archive_md5_mismatch",
                        "MD5 mismatch",
                        cpv=cpv,
                        path=relative_path,
                        record=record.number,
                    )
                )
        if len(expected_sha1) == 40 and HEX_RE.fullmatch(expected_sha1):
            if actual_sha1 != expected_sha1.lower():
                issues.append(
                    Issue(
                        "indexed_archive_sha1_mismatch",
                        "SHA1 mismatch",
                        cpv=cpv,
                        path=relative_path,
                        record=record.number,
                    )
                )

        if validate_gpkg_archives and relative_path.endswith(".gpkg.tar"):
            gpkg_result, gpkg_issues = validate_gpkg(
                resolved_candidate, relative_path, cpv, zstd_program
            )
            archive_result["gpkg"] = gpkg_result
            issues.extend(gpkg_issues)
            if gpkg_result["status"] == "verified":
                gpkg_validated += 1
            image_zstd_tested += cast(int, gpkg_result["zstd_streams_tested"])

    for relative_path, matching_records in sorted(path_records.items()):
        if len(matching_records) > 1:
            issues.append(
                Issue(
                    "indexed_path_duplicate",
                    f"PATH is referenced by {len(matching_records)} index records",
                    path=relative_path,
                )
            )

    try:
        found_gpkg = _scan_gpkg_archives(snapshot, issues)
    except OSError as exc:
        issues.append(
            Issue("snapshot_scan_unreadable", f"cannot scan snapshot: {exc}")
        )
        found_gpkg = []
    unindexed_gpkg = sorted(set(found_gpkg) - indexed_safe_paths)
    for relative_path in unindexed_gpkg:
        issues.append(
            Issue(
                "unindexed_gpkg_archive",
                "*.gpkg.tar entry is not named by any Packages record",
                path=relative_path,
            )
        )

    archive_results.sort(
        key=lambda item: (
            str(item["cpv"]),
            str(item["path"]),
            cast(int, item["record"]),
        )
    )
    extra_indexed.sort(
        key=lambda item: (
            str(item["cpv"]),
            str(item["path"]),
            cast(int, item["record"]),
        )
    )
    issues.sort(key=Issue.sort_key)

    indexed_gpkg = sorted(
        path for path in indexed_safe_paths if path.endswith(".gpkg.tar")
    )
    report: dict[str, object] = {
        "archives": archive_results,
        "counts": {
            "errors": len(issues),
            "extra_indexed_archives": len(extra_indexed),
            "gpkg_archives_found": len(found_gpkg),
            "gpkg_archives_indexed": len(indexed_gpkg),
            "gpkg_archives_validated": gpkg_validated,
            "image_tar_zst_streams_tested": image_zstd_tested,
            "indexed_records": len(records),
            "indexed_unique_cpvs": len(records_by_cpv),
            "indexed_unique_paths": len(indexed_safe_paths),
            "live_cpvs": len(live_cpvs),
            "missing_live_cpvs": len(missing_live_cpvs),
            "unindexed_gpkg_archives": len(unindexed_gpkg),
        },
        "coverage": {
            "duplicate_live_cpvs": {
                cpv: duplicate_live_cpvs[cpv] for cpv in sorted(duplicate_live_cpvs)
            },
            "extra_indexed_archives": extra_indexed,
            "missing_live_cpvs": sorted(missing_live_cpvs),
            "unindexed_gpkg_archives": unindexed_gpkg,
        },
        "inputs": {
            "allow_extra_archives": allow_extra_archives,
            "packages_index": f"{snapshot_display}/Packages",
            "snapshot": snapshot_display,
            "validate_gpkg": validate_gpkg_archives,
            "vdb": vdb_display,
            "zstd": zstd_program,
        },
        "issues": [item.as_dict() for item in issues],
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if not issues else "fail",
    }
    return report


def render_json(report: dict[str, object]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_text(report: dict[str, object]) -> str:
    inputs = report["inputs"]
    counts = report["counts"]
    assert isinstance(inputs, dict)
    assert isinstance(counts, dict)
    lines = [
        f"binpkg snapshot verification: {str(report['status']).upper()}",
        f"snapshot: {inputs['snapshot']}",
        f"Packages: {inputs['packages_index']}",
        f"live VDB: {inputs['vdb']}",
        f"live CPVs: {counts['live_cpvs']}",
        f"indexed records: {counts['indexed_records']}",
        f"indexed unique paths: {counts['indexed_unique_paths']}",
        f"unindexed *.gpkg.tar: {counts['unindexed_gpkg_archives']}",
        f"validated GPKGs: {counts['gpkg_archives_validated']}",
        f"tested image.tar.zst streams: {counts['image_tar_zst_streams_tested']}",
        f"errors: {counts['errors']}",
    ]
    issues = report["issues"]
    assert isinstance(issues, list)
    if issues:
        lines.append("issues:")
        for item in issues:
            assert isinstance(item, dict)
            context = []
            for key in ("cpv", "path", "record"):
                if key in item:
                    context.append(f"{key}={item[key]}")
            suffix = f" ({', '.join(context)})" if context else ""
            lines.append(f"- {item['code']}{suffix}: {item['message']}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Strictly verify a Gentoo binpkg snapshot against the live VDB "
            "without modifying either input."
        )
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        type=Path,
        help="snapshot root containing the Packages index",
    )
    parser.add_argument(
        "--vdb",
        type=Path,
        default=Path("/var/db/pkg"),
        help="installed-package VDB root (default: /var/db/pkg)",
    )
    parser.add_argument(
        "--allow-extra-archives",
        "--allow-extra-indexed-archives",
        dest="allow_extra_archives",
        action="store_true",
        help=(
            "allow fully indexed archives for CPVs absent from the VDB; this "
            "never permits duplicate archives for a live CPV or unindexed GPKGs"
        ),
    )
    parser.add_argument(
        "--validate-gpkg",
        action="store_true",
        help=(
            "validate GLEP 78 outer tar structure and Manifest data, then run "
            "zstd --test on every verified image.tar.zst stream"
        ),
    )
    parser.add_argument(
        "--zstd",
        default="zstd",
        help="zstd executable used with --validate-gpkg (default: zstd)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "report"),
        default="json",
        help="deterministic output format (default: json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.validate_gpkg:
        zstd_path = shutil.which(args.zstd)
        if zstd_path is None:
            # Preserve the requested command in deterministic output.  Each
            # image stream will carry the corresponding execution failure.
            zstd_path = args.zstd
    else:
        zstd_path = args.zstd
    report = verify_snapshot(
        snapshot_arg=args.snapshot,
        vdb_arg=args.vdb,
        allow_extra_archives=args.allow_extra_archives,
        validate_gpkg_archives=args.validate_gpkg,
        zstd_program=zstd_path,
    )
    output = render_json(report) if args.format == "json" else render_text(report)
    try:
        sys.stdout.write(output)
    except BrokenPipeError:  # pragma: no cover - standard CLI behavior
        return 1
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

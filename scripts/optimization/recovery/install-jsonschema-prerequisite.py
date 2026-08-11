#!/usr/bin/python3 -IB
"""Authority and state core for the Phase-2 jsonschema prerequisite transaction.

This program is deliberately separate from the general PGO transaction and
from the binpkg-checkpoint state machine.  It reuses their reviewed mechanical
invariants (root-trusted paths, immutable phase records, exact process identity,
PID-namespace containment, and recovery before retry) without coupling their
different semantic state machines.

Production phases are::

    prepared -> armed -> success
                       -> rollback-in-progress -> rolled-back
                                               -> recovery-failed

Once ``armed`` is visible, the source emerge may never be executed again.

The live preparation and mutation entry points intentionally remain disabled
until their Portage VDB exclusion, same-object rollback, and monotonic counter
protocols are complete.  Hermetic tests exercise the authority, plan,
containment-contract, and durable-state primitives without touching a live
Gentoo installation.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import dataclasses
import datetime as dt
import errno
import fcntl
import grp
import hashlib
import importlib.util
import json
import os
import pty
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import termios
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn, Protocol, cast


SCHEMA = "gentoo-optimization-jsonschema-prerequisite-v1"
PHASES = {
    "prepared",
    "armed",
    "success",
    "rollback-in-progress",
    "rolled-back",
    "recovery-failed",
}
TERMINAL_PHASES = {"success", "rolled-back", "recovery-failed"}
TRANSITIONS = {
    "prepared": {"armed"},
    "armed": {"success", "rollback-in-progress"},
    "rollback-in-progress": {"rolled-back", "recovery-failed"},
}
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
CPV_PATTERN = re.compile(r"[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+-[0-9][A-Za-z0-9+_.-]*\Z")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*\Z")
EXACT_ATOM_PATTERN = re.compile(
    r"=(?P<cpv>[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+-[0-9][A-Za-z0-9+_.-]*)"
    r"::(?P<repository>[A-Za-z0-9][A-Za-z0-9+_.-]*)\Z"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SCHEDULED_LINE = re.compile(r"^\[[A-Za-z]")
NEW_SOURCE_LINE = re.compile(
    r"^\[ebuild\s+N(?:\s+[^]]*)?\]\s+"
    r"(?:=)?(?P<cpv>[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+-[0-9][A-Za-z0-9+_.-]*)"
    r"::(?P<repository>[A-Za-z0-9+_.-]+)(?:\s|$)"
)
TRANSACTION_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
PR_SET_PDEATHSIG = 1
# Candidate-A safety gates.  The authority/state implementation is executable
# for hermetic tests, but neither live preparation nor installed-package
# mutation is authorized yet.  Preparation still needs the standard Portage
# vardb lock plus pre/post process-and-handle exclusion around every live
# VDB/private-state snapshot.  Mutation additionally needs a same-object,
# held-vardb-lock rollback and monotonic counter remount/reseal handshake.
LIVE_PREPARATION_ENABLED = False
LIVE_MUTATION_ENABLED = False
CONTROL_SCHEMA = "gentoo-optimization-jsonschema-control-v1"
CONTROL_MAX_FRAME = 1024 * 1024
CONTROL_SESSION_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class TransactionError(RuntimeError):
    """The transaction cannot be proven safe."""


class TransactionInterrupted(TransactionError):
    """A managed signal interrupted the coordinator."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


def fail(message: str) -> NoReturn:
    raise TransactionError(message)


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def boot_id(proc_root: Path = Path("/proc")) -> str:
    path = proc_root / "sys/kernel/random/boot_id"
    try:
        value = path.read_text(encoding="ascii").strip()
    except OSError as error:
        fail(f"cannot read boot identity: {error}")
    if not value:
        fail("boot identity is empty")
    return value


def require_safe_id(value: str, label: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        fail(f"{label} is unsafe: {value!r}")
    return value


def require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        fail(f"{label} is not a SHA-256 digest")
    return value


def require_absolute(path: Path, label: str) -> Path:
    if not path.is_absolute() or path == Path("/") or "\0" in os.fspath(path):
        fail(f"{label} is not a safe absolute path: {path}")
    normalized = Path(os.path.normpath(path))
    if normalized != path:
        fail(f"{label} is not lexically canonical: {path}")
    return path


def require_direct_child(path: Path, parent: Path, label: str) -> Path:
    require_absolute(path, label)
    require_absolute(parent, f"{label} parent")
    if path.parent != parent:
        fail(f"{label} is not a direct child of {parent}: {path}")
    return path


def require_trust_root(path: Path) -> Path:
    """Validate the one non-mutation path allowed to name the filesystem root."""

    if path != Path("/") or not path.is_absolute() or Path(os.path.normpath(path)) != path:
        fail(f"trust root must be the canonical filesystem root: {path}")
    return path


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclasses.dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    nlink: int
    size: int

    @classmethod
    def observe(cls, path: Path, *, follow: bool = False) -> "FileIdentity":
        metadata = path.stat() if follow else path.lstat()
        return cls(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            uid=metadata.st_uid,
            gid=metadata.st_gid,
            mode=stat.S_IMODE(metadata.st_mode),
            nlink=metadata.st_nlink,
            size=metadata.st_size,
        )

    def as_json(self) -> dict[str, int]:
        return dataclasses.asdict(self)


def validate_trusted_directory(path: Path, trusted_uid: int, trusted_gid: int) -> None:
    if path == Path("/"):
        require_trust_root(path)
    else:
        require_absolute(path, "trusted directory")
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or metadata.st_gid != trusted_gid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        fail(f"untrusted directory: {path}")


def validate_ancestor_chain(path: Path, trusted_uid: int, trusted_gid: int, stop: Path) -> None:
    require_absolute(path, "trusted path")
    require_trust_root(stop)
    current = path if path.is_dir() else path.parent
    while True:
        validate_trusted_directory(current, trusted_uid, trusted_gid)
        if current == stop:
            return
        if current == current.parent or not current.is_relative_to(stop):
            fail(f"trusted path escapes {stop}: {path}")
        current = current.parent


def validate_trusted_regular(
    path: Path,
    trusted_uid: int,
    trusted_gid: int,
    *,
    executable: bool = False,
    one_link: bool = True,
) -> FileIdentity:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or metadata.st_gid != trusted_gid
        or mode & 0o022
        or (executable and not mode & 0o111)
        or (one_link and metadata.st_nlink != 1)
    ):
        fail(f"untrusted regular file: {path}")
    return FileIdentity.observe(path)


def atomic_publish_noreplace(path: Path, payload: bytes, mode: int = 0o600) -> str:
    """Publish one immutable regular file without an overwrite window."""

    require_absolute(path, "publication path")
    if path_exists(path):
        fail(f"publication destination already exists: {path}")
    partial = path.with_name(f".{path.name}.partial.{os.getpid()}")
    if path_exists(partial):
        fail(f"publication partial already exists: {partial}")
    descriptor = os.open(
        partial,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                fail("short write during immutable publication")
            offset += written
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(partial, path, follow_symlinks=False)
        fsync_directory(path.parent)
        partial.unlink()
        fsync_directory(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            partial.unlink()
            fsync_directory(partial.parent)
        raise
    if sha256_file(path) != sha256_bytes(payload):
        fail(f"published payload changed: {path}")
    return sha256_bytes(payload)


def atomic_replace_canonical(canonical: Path, phase_path: Path) -> None:
    """Atomically point a canonical hardlink at one immutable phase record."""

    if not phase_path.is_file() or phase_path.is_symlink():
        fail(f"phase record is not a regular file: {phase_path}")
    partial = canonical.with_name(f".{canonical.name}.partial.{os.getpid()}")
    if path_exists(partial):
        fail(f"canonical state partial already exists: {partial}")
    os.link(phase_path, partial, follow_symlinks=False)
    try:
        os.replace(partial, canonical)
        fsync_directory(canonical.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            partial.unlink()
        raise
    if FileIdentity.observe(canonical) != FileIdentity.observe(phase_path):
        fail("canonical state does not share the immutable phase inode")


def read_json_regular(path: Path, label: str, limit: int = 16 * 1024 * 1024) -> Any:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail(f"{label} is not a regular file: {path}")
    if metadata.st_size > limit:
        fail(f"{label} exceeds its size limit: {path}")
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot parse {label}: {error}")


def validate_state(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("transaction state is not an object")
    required = {
        "schema",
        "transaction_id",
        "phase",
        "recorded_at",
        "boot_id",
        "previous_phase",
        "previous_state_sha256",
        "prepared_state_sha256",
        "authority",
        "resolver",
        "plan",
        "private_roots",
        "child",
        "outcome",
        "recovery_contract",
        "evidence",
        "pending_total",
        "unknown_total",
        "failed_total",
    }
    if set(value) != required or value.get("schema") != SCHEMA:
        fail("transaction state has an invalid schema")
    transaction_id = value.get("transaction_id")
    phase = value.get("phase")
    if not isinstance(transaction_id, str):
        fail("transaction ID is not a string")
    require_safe_id(transaction_id, "transaction ID")
    if phase not in PHASES:
        fail("transaction state has an invalid phase")
    previous_phase = value.get("previous_phase")
    previous_sha = value.get("previous_state_sha256")
    prepared_sha = value.get("prepared_state_sha256")
    if phase == "prepared":
        if previous_phase is not None or previous_sha is not None or prepared_sha is not None:
            fail("prepared state unexpectedly has a predecessor")
    else:
        if previous_phase not in PHASES or phase not in TRANSITIONS.get(previous_phase, set()):
            fail("transaction state has an invalid phase transition")
        require_sha256(previous_sha, "previous state digest")
        require_sha256(prepared_sha, "prepared state digest")
    for key in ("authority", "resolver", "plan", "private_roots", "recovery_contract", "evidence"):
        if not isinstance(value.get(key), dict):
            fail(f"transaction state {key} is not an object")
    child = value.get("child")
    outcome = value.get("outcome")
    if child is not None and not isinstance(child, dict):
        fail("transaction child record is invalid")
    if outcome is not None and not isinstance(outcome, dict):
        fail("transaction outcome is invalid")
    raw_counts = tuple(value.get(key) for key in ("pending_total", "unknown_total", "failed_total"))
    if any(type(item) is not int or item < 0 for item in raw_counts):
        fail("transaction state totals are invalid")
    counts = cast(tuple[int, int, int], raw_counts)
    if phase == "success" and counts != (0, 0, 0):
        fail("successful transaction does not have zero totals")
    if phase in {"prepared", "armed", "rollback-in-progress"} and counts[0] < 1:
        fail("nonterminal transaction has no pending work")
    if phase in {"rolled-back", "recovery-failed"} and counts[2] < 1:
        fail("failed transaction has no failed total")
    return value


def state_path(paths: "Paths", phase: str) -> Path:
    if phase not in PHASES:
        fail(f"invalid state phase: {phase}")
    return paths.state_parent / f"jsonschema-prerequisite-{paths.transaction_id}.{phase}.json"


def publish_state(paths: "Paths", value: dict[str, Any]) -> tuple[Path, str]:
    validated = validate_state(value)
    if validated["transaction_id"] != paths.transaction_id:
        fail("state transaction ID differs from its path")
    phase = str(validated["phase"])
    destination = state_path(paths, phase)
    payload = canonical_json(validated)
    digest = atomic_publish_noreplace(destination, payload)
    atomic_replace_canonical(paths.canonical_state, destination)
    return destination, digest


def load_phase_state(paths: "Paths", phase: str) -> tuple[dict[str, Any], str]:
    path = state_path(paths, phase)
    value = validate_state(read_json_regular(path, f"{phase} transaction state"))
    if value["transaction_id"] != paths.transaction_id or value["phase"] != phase:
        fail(f"{phase} state identity differs from its path")
    return value, sha256_file(path)


def load_current_state(paths: "Paths") -> tuple[dict[str, Any], str]:
    value = validate_state(read_json_regular(paths.canonical_state, "canonical transaction state"))
    phase = str(value["phase"])
    phase_value, phase_sha = load_phase_state(paths, phase)
    if value != phase_value or FileIdentity.observe(paths.canonical_state) != FileIdentity.observe(state_path(paths, phase)):
        fail("canonical transaction state differs from its immutable phase state")
    return phase_value, phase_sha


def reconcile_state_chain(
    paths: "Paths", *, repair_canonical: bool = True
) -> tuple[dict[str, Any], str] | None:
    """Advance the canonical hardlink through one unique durable phase chain."""

    records: dict[str, tuple[dict[str, Any], str, Path]] = {}
    for phase in PHASES:
        path = state_path(paths, phase)
        if not path_exists(path):
            continue
        value = validate_state(read_json_regular(path, f"{phase} transaction state"))
        if value["transaction_id"] != paths.transaction_id or value["phase"] != phase:
            fail(f"durable {phase} state identity differs from its path")
        records[phase] = (value, sha256_file(path), path)
    if not records:
        if path_exists(paths.canonical_state):
            fail("canonical transaction state exists without any phase record")
        return None
    if "prepared" not in records:
        fail("transaction state records exist without prepared authority")
    prepared_sha = records["prepared"][1]
    successors: dict[str, list[str]] = {phase: [] for phase in PHASES}
    for phase, (value, _digest, _path) in records.items():
        if phase == "prepared":
            continue
        previous_phase = value["previous_phase"]
        if previous_phase not in records:
            fail(f"durable {phase} state lacks its predecessor")
        if value["previous_state_sha256"] != records[previous_phase][1]:
            fail(f"durable {phase} state predecessor digest differs")
        if value["prepared_state_sha256"] != prepared_sha:
            fail(f"durable {phase} state prepared digest differs")
        successors[previous_phase].append(phase)
    for phase, children in successors.items():
        if len(children) > 1:
            fail(f"transaction state chain branches after {phase}: {sorted(children)}")
    chain = ["prepared"]
    seen = {"prepared"}
    while successors[chain[-1]]:
        child = successors[chain[-1]][0]
        if child in seen:
            fail("transaction state chain contains a cycle")
        chain.append(child)
        seen.add(child)
    if seen != set(records):
        fail(f"transaction has detached or foreign phase records: {sorted(set(records) - seen)}")
    latest = chain[-1]
    latest_path = records[latest][2]
    if path_exists(paths.canonical_state):
        canonical = validate_state(read_json_regular(paths.canonical_state, "canonical transaction state"))
        canonical_phase = canonical["phase"]
        if canonical_phase not in records:
            fail("canonical state names a phase outside the durable chain")
        canonical_path = records[canonical_phase][2]
        if FileIdentity.observe(paths.canonical_state) != FileIdentity.observe(canonical_path):
            fail("canonical state is not the exact durable phase inode")
        if chain.index(canonical_phase) > chain.index(latest):
            fail("canonical state is ahead of the durable phase chain")
    canonical_is_latest = path_exists(paths.canonical_state) and (
        FileIdentity.observe(paths.canonical_state) == FileIdentity.observe(latest_path)
    )
    if not canonical_is_latest and not repair_canonical:
        fail("canonical state requires explicit recovery to advance its durable chain")
    if not canonical_is_latest:
        atomic_replace_canonical(paths.canonical_state, latest_path)
    return records[latest][0], records[latest][1]


def next_state(
    previous: dict[str, Any],
    previous_sha: str,
    phase: str,
    *,
    child: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous = validate_state(previous)
    if phase not in TRANSITIONS.get(str(previous["phase"]), set()):
        fail(f"invalid transaction transition: {previous['phase']} -> {phase}")
    prepared_sha = previous_sha if previous["phase"] == "prepared" else previous["prepared_state_sha256"]
    pending = 0 if phase in TERMINAL_PHASES else 1
    failed = 1 if phase in {"rolled-back", "recovery-failed"} else 0
    return {
        **previous,
        "phase": phase,
        "recorded_at": utc_now(),
        "boot_id": boot_id(Path(previous["evidence"].get("proc_root", "/proc"))),
        "previous_phase": previous["phase"],
        "previous_state_sha256": require_sha256(previous_sha, "previous state digest"),
        "prepared_state_sha256": require_sha256(prepared_sha, "prepared state digest"),
        "child": child,
        "outcome": outcome,
        "pending_total": pending,
        "unknown_total": 0,
        "failed_total": failed,
    }


@dataclasses.dataclass(frozen=True)
class Paths:
    transaction_id: str
    root: Path = Path("/")
    fixture_mode: bool = False

    def rooted(self, production: str) -> Path:
        path = Path(production)
        if not path.is_absolute():
            fail(f"production path is not absolute: {path}")
        return self.root / path.relative_to("/") if self.fixture_mode else path

    @property
    def state_parent(self) -> Path:
        return self.rooted("/var/lib/gentoo-optimization/state/project")

    @property
    def report_parent(self) -> Path:
        return self.rooted("/var/lib/gentoo-optimization/reports")

    @property
    def authority_parent(self) -> Path:
        return self.rooted("/var/lib/gentoo-optimization/recovery/prerequisite-authorities")

    @property
    def cache_parent(self) -> Path:
        return self.rooted("/var/cache/gentoo-optimization/prerequisite-transactions")

    @property
    def report(self) -> Path:
        return self.report_parent / f"jsonschema-prerequisite-{self.transaction_id}"

    @property
    def authority(self) -> Path:
        return self.authority_parent / self.transaction_id

    @property
    def cache(self) -> Path:
        return self.cache_parent / self.transaction_id

    @property
    def canonical_state(self) -> Path:
        return self.state_parent / f"jsonschema-prerequisite-{self.transaction_id}.json"

    @property
    def child_sidecar(self) -> Path:
        return self.report / "child.json"

    @property
    def child_completion(self) -> Path:
        return self.report / "child-completion.json"

    @property
    def recovery_child_sidecar(self) -> Path:
        return self.report / "recovery-child.json"

    @property
    def transaction_lock(self) -> Path:
        return self.state_parent / "jsonschema-prerequisite.lock"

    @property
    def framework_lock(self) -> Path:
        return self.rooted("/run/gentoo-optimization/framework-install.lock")

    @property
    def project_lock(self) -> Path:
        return self.rooted("/run/gentoo-optimization/project.lock")

    @property
    def generation_lock(self) -> Path:
        return self.rooted("/run/gentoo-optimization/generation.lock")

    @property
    def vdb(self) -> Path:
        return self.rooted("/var/db/pkg")

    @property
    def portage_config(self) -> Path:
        return self.rooted("/etc/portage")

    @property
    def portage_global_config(self) -> Path:
        return self.rooted("/usr/share/portage/config")

    @property
    def var_lib_portage(self) -> Path:
        return self.rooted("/var/lib/portage")

    @property
    def cache_edb(self) -> Path:
        return self.rooted("/var/cache/edb")

    @property
    def proc_root(self) -> Path:
        return self.rooted("/proc")

    def validate(self) -> None:
        require_safe_id(self.transaction_id, "transaction ID")
        for parent in (self.state_parent, self.report_parent, self.authority_parent, self.cache_parent):
            require_absolute(parent, "transaction parent")
        require_direct_child(self.report, self.report_parent, "report path")
        require_direct_child(self.authority, self.authority_parent, "authority path")
        require_direct_child(self.cache, self.cache_parent, "cache path")
        require_direct_child(self.canonical_state, self.state_parent, "canonical state path")
        require_direct_child(self.child_sidecar, self.report, "child sidecar path")
        require_direct_child(self.child_completion, self.report, "child completion path")
        require_direct_child(
            self.recovery_child_sidecar, self.report, "recovery child sidecar path"
        )


@dataclasses.dataclass(frozen=True)
class RepositorySpec:
    name: str
    location: Path
    sync_type: str | None
    masters: tuple[str, ...]
    key_path: Path | None = None
    max_age_days: int = 0

    def validate(self) -> None:
        if not REPOSITORY_PATTERN.fullmatch(self.name):
            fail(f"unsafe repository name: {self.name!r}")
        require_absolute(self.location, f"repository {self.name} location")
        if self.sync_type not in {None, "git", "rsync"}:
            fail(f"unsupported repository sync type for {self.name}: {self.sync_type}")
        if len(set(self.masters)) != len(self.masters):
            fail(f"repository {self.name} has duplicate masters")
        for master in self.masters:
            if not REPOSITORY_PATTERN.fullmatch(master):
                fail(f"repository {self.name} has unsafe master: {master!r}")
        if self.sync_type == "rsync" and self.key_path is None:
            fail(f"rsync repository {self.name} has no reviewed OpenPGP key")


@dataclasses.dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    status: int
    stdout: bytes
    stderr: bytes


class ControlChannel:
    """Private, ordered SOCK_SEQPACKET control channel.

    The socket descriptor is inherited only by the coordinator and the
    internal Portage supervisor.  It is marked close-on-exec immediately in
    the supervisor so ebuild/package subprocesses cannot spoof protocol
    records through stdout, stderr, or an inherited control descriptor.
    """

    def __init__(self, endpoint: socket.socket, session: str) -> None:
        if endpoint.type & socket.SOCK_SEQPACKET != socket.SOCK_SEQPACKET:
            fail("transaction control endpoint is not SOCK_SEQPACKET")
        if not CONTROL_SESSION_PATTERN.fullmatch(session):
            fail("transaction control session is invalid")
        self._endpoint = endpoint
        self._session = session
        self._send_sequence = 0
        self._receive_sequence = 0
        os.set_inheritable(endpoint.fileno(), False)

    @property
    def fileno(self) -> int:
        return self._endpoint.fileno()

    @property
    def session(self) -> str:
        return self._session

    def close(self) -> None:
        self._endpoint.close()

    def send(self, kind: str, payload: Mapping[str, Any] | None = None) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,63}", kind):
            fail(f"unsafe transaction control kind: {kind!r}")
        self._send_sequence += 1
        record = {
            "schema": CONTROL_SCHEMA,
            "session": self._session,
            "sequence": self._send_sequence,
            "kind": kind,
            "payload": dict(payload or {}),
        }
        encoded = canonical_json(record)
        if len(encoded) > CONTROL_MAX_FRAME:
            fail("transaction control frame exceeds its size limit")
        try:
            written = self._endpoint.send(encoded)
        except OSError as error:
            fail(f"cannot send transaction control frame {kind}: {error}")
        if written != len(encoded):
            fail(f"short transaction control frame write: {kind}")

    def receive(self, expected_kind: str, timeout: float) -> dict[str, Any]:
        if timeout <= 0:
            fail("transaction control timeout must be positive")
        previous_timeout = self._endpoint.gettimeout()
        self._endpoint.settimeout(timeout)
        try:
            try:
                encoded, ancillary, flags, _address = self._endpoint.recvmsg(
                    CONTROL_MAX_FRAME + 1, socket.CMSG_SPACE(4)
                )
            except TimeoutError:
                fail(f"timed out waiting for transaction control frame {expected_kind}")
            except OSError as error:
                fail(f"cannot receive transaction control frame {expected_kind}: {error}")
        finally:
            self._endpoint.settimeout(previous_timeout)
        if not encoded:
            fail(f"transaction control channel closed before {expected_kind}")
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(encoded) > CONTROL_MAX_FRAME:
            fail("transaction control frame was truncated")
        if ancillary:
            fail("transaction control frame unexpectedly carried descriptors or credentials")
        try:
            record = json.loads(encoded)
        except json.JSONDecodeError as error:
            fail(f"transaction control frame is invalid JSON: {error}")
        if not isinstance(record, dict) or set(record) != {
            "schema",
            "session",
            "sequence",
            "kind",
            "payload",
        }:
            fail("transaction control frame has an invalid schema")
        expected_sequence = self._receive_sequence + 1
        if (
            record.get("schema") != CONTROL_SCHEMA
            or record.get("session") != self._session
            or record.get("sequence") != expected_sequence
            or record.get("kind") != expected_kind
            or not isinstance(record.get("payload"), dict)
        ):
            fail(
                f"transaction control frame differs from expected "
                f"{expected_kind} sequence {expected_sequence}"
            )
        self._receive_sequence = expected_sequence
        return record["payload"]


def control_channel_pair(session: str | None = None) -> tuple[ControlChannel, ControlChannel]:
    session = session or os.urandom(32).hex()
    first, second = socket.socketpair(
        socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC
    )
    try:
        return ControlChannel(first, session), ControlChannel(second, session)
    except BaseException:
        first.close()
        second.close()
        raise


@dataclasses.dataclass(frozen=True)
class MountBinding:
    """One exact bind mount used by a contained Portage command."""

    source: Path
    target: Path
    read_only: bool

    def validate(self) -> None:
        require_absolute(self.source, "mount source")
        require_absolute(self.target, "mount target")
        source = self.source.resolve(strict=True)
        target = self.target.resolve(strict=True)
        if source == Path("/") or target == Path("/"):
            fail("transaction mount may not replace the filesystem root")
        if source.is_symlink() or target.is_symlink():
            fail("transaction mount endpoint may not be a symlink")
        if source.is_dir() != target.is_dir():
            fail(f"transaction mount endpoint types differ: {source} -> {target}")

    def as_json(self) -> dict[str, object]:
        self.validate()
        return {
            "source": os.fspath(self.source.resolve(strict=True)),
            "target": os.fspath(self.target.resolve(strict=True)),
            "read_only": self.read_only,
        }


class Runner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
        timeout: float,
        cwd: Path | None = None,
    ) -> CommandResult: ...


class SubprocessRunner:
    """Bounded direct-command runner used only outside the live emerge child."""

    def run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
        timeout: float,
        cwd: Path | None = None,
    ) -> CommandResult:
        if not argv or not Path(argv[0]).is_absolute() or timeout <= 0:
            fail("bounded runner received an unsafe command")
        # Never give a potentially long-lived descendant a captured pipe.  A
        # direct child can exit while an escaped grandchild keeps such a pipe
        # open, turning a nominal subprocess timeout into an unbounded wait for
        # EOF.  Anonymous regular files keep both completion and timeout reads
        # bounded.
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                cwd=cwd,
                env=dict(environment),
                start_new_session=True,
            )
            child_identity = process_identity(process.pid)
            if child_identity is not None and (
                child_identity["process_group"] != process.pid
                or child_identity["session"] != process.pid
            ):
                terminate_direct_process(process, 5.0)
                fail("bounded child did not enter its private process group and session")
            if child_identity is None and process.poll() is None:
                terminate_direct_process(process, 5.0)
                fail("cannot observe live bounded-child process identity")
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_direct_process(process, 5.0)
            wait_group_empty(process.pid, process.pid, timeout=5.0)
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
        return CommandResult(
            tuple(argv),
            124 if timed_out else normalize_status(process.returncode),
            stdout,
            stderr,
        )


def normalize_status(returncode: int) -> int:
    return 128 - returncode if returncode < 0 else returncode


def parse_proc_stat(payload: str, pid: int) -> dict[str, int | str]:
    marker = payload.rfind(") ")
    if marker < 0:
        fail(f"cannot parse process stat for PID {pid}")
    fields = payload[marker + 2 :].split()
    if len(fields) < 20:
        fail(f"short process stat for PID {pid}")
    return {
        "pid": pid,
        "state": fields[0],
        "ppid": int(fields[1]),
        "process_group": int(fields[2]),
        "session": int(fields[3]),
        "start_ticks": int(fields[19]),
    }


def process_identity(pid: int, proc_root: Path = Path("/proc")) -> dict[str, int | str] | None:
    try:
        return parse_proc_stat((proc_root / str(pid) / "stat").read_text(encoding="ascii"), pid)
    except FileNotFoundError:
        return None


def terminate_direct_process(process: subprocess.Popen[bytes], kill_after: float) -> None:
    """Boundedly terminate our exact unreaped direct child and private group."""

    if process.poll() is not None:
        process.wait()
        return
    identity = process_identity(process.pid)
    if identity is None or identity["process_group"] != process.pid:
        fail("cannot bind direct child process-group identity for cleanup")
    try:
        descriptor = os.pidfd_open(process.pid, 0)
    except (AttributeError, OSError) as error:
        fail(f"cannot open exact child pidfd for cleanup: {error}")
    try:
        if process_identity(process.pid) != identity:
            fail("direct child identity changed after pidfd_open")
        with contextlib.suppress(ProcessLookupError):
            signal.pidfd_send_signal(descriptor, signal.SIGTERM, None, 0)
        try:
            process.wait(timeout=kill_after)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(descriptor, signal.SIGKILL, None, 0)
            process.wait(timeout=kill_after)
    finally:
        os.close(descriptor)


def install_parent_death_signal(parent_pid: int, parent_start_ticks: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_PDEATHSIG failed")
    parent = process_identity(parent_pid)
    if parent is None or parent["start_ticks"] != parent_start_ticks or os.getppid() != parent_pid:
        fail("coordinator disappeared while child installed parent-death binding")


def tree_manifest(root: Path, *, ignore_names: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Return a deterministic content/metadata manifest without volatile timestamps."""

    root = root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        fail(f"manifest root is not a real directory: {root}")
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if any(part in ignore_names for part in Path(relative).parts):
            continue
        metadata = path.lstat()
        base: dict[str, Any] = {
            "path": relative,
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
        }
        if stat.S_ISREG(metadata.st_mode):
            base.update(type="file", size=metadata.st_size, sha256=sha256_file(path))
        elif stat.S_ISDIR(metadata.st_mode):
            base.update(type="directory")
        elif stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            if "\0" in target or "\n" in target or "\r" in target:
                fail(f"unsafe symlink target in tree: {path}")
            base.update(type="symlink", target=target)
        else:
            fail(f"unsupported object in tree manifest: {path}")
        rows.append(base)
    payload = {"schema_version": 1, "root": os.fspath(root), "rows": rows}
    payload["rows_sha256"] = sha256_bytes(canonical_json(rows))
    return payload


def write_manifest(path: Path, manifest: dict[str, Any]) -> str:
    return atomic_publish_noreplace(path, canonical_json(manifest))


def verify_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    recorded = read_json_regular(manifest_path, "tree manifest", 512 * 1024 * 1024)
    if not isinstance(recorded, dict) or recorded.get("schema_version") != 1:
        fail("tree manifest has an invalid schema")
    current = tree_manifest(root)
    if recorded != current:
        fail(f"tree authority changed: {root}")
    return current


def normalize_tree_ownership(root: Path, uid: int, gid: int) -> None:
    """Make a copied authority owned by its trustee and non-writable."""

    for path in [root, *sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True)]:
        metadata = path.lstat()
        os.chown(path, uid, gid, follow_symlinks=False)
        if not stat.S_ISLNK(metadata.st_mode):
            os.chmod(path, stat.S_IMODE(metadata.st_mode) & ~0o222, follow_symlinks=False)


def copy_tree(source: Path, destination: Path, runner: Runner, tools: Mapping[str, Path]) -> None:
    if path_exists(destination):
        fail(f"tree destination already exists: {destination}")
    destination.mkdir(mode=0o755, parents=False)
    result = runner.run(
        [os.fspath(tools["cp"]), "-a", "--reflink=auto", "--one-file-system", "--", os.fspath(source) + "/.", os.fspath(destination) + "/"],
        environment=clean_environment(),
        timeout=4 * 3600,
    )
    if result.status != 0:
        fail(f"authority clone failed for {source}: status={result.status}: {result.stderr.decode(errors='replace')}")


def clean_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    result = {
        "HOME": "/root",
        "LANG": "C",
        "LC_ALL": "C",
        "LOGNAME": "root",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "SHELL": "/bin/bash",
        "TZ": "UTC",
        "USER": "root",
    }
    if extra:
        result.update(extra)
    return result


def inspect_executable(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    metadata = resolved.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or mode & 0o022
        or not mode & 0o111
    ):
        fail(f"untrusted executable: {path}")
    validate_ancestor_chain(resolved.parent, 0, 0, Path("/"))
    return {
        "requested_path": os.fspath(path),
        "resolved_path": os.fspath(resolved),
        **FileIdentity.observe(resolved).as_json(),
        "sha256": sha256_file(resolved),
    }


def tool_manifest(tools: Mapping[str, Path]) -> dict[str, Any]:
    rows = [{"name": name, **inspect_executable(path)} for name, path in sorted(tools.items())]
    return {"schema_version": 1, "rows": rows, "rows_sha256": sha256_bytes(canonical_json(rows))}


def revalidate_tool_manifest(value: object) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("rows"), list):
        fail("tool manifest has an invalid schema")
    current = []
    for row in value["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not isinstance(row.get("requested_path"), str):
            fail("tool manifest row is invalid")
        current.append({"name": row["name"], **inspect_executable(Path(row["requested_path"]))})
    if current != value["rows"] or sha256_bytes(canonical_json(current)) != value.get("rows_sha256"):
        fail("a transaction tool identity changed")


def parse_pretend_output(text: str, installed_cpvs: set[str]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.lstrip()
        if not SCHEDULED_LINE.match(line):
            continue
        match = NEW_SOURCE_LINE.match(line)
        if match is None:
            fail(f"pretend selected a non-new or non-source action: {raw}")
        cpv = match.group("cpv")
        repository = match.group("repository")
        if not CPV_PATTERN.fullmatch(cpv) or not REPOSITORY_PATTERN.fullmatch(repository):
            fail(f"pretend selected an unsafe package identity: {raw}")
        if cpv in installed_cpvs:
            fail(f"pretend selected an already installed CPV: {cpv}")
        rows.append(
            {
                "cpv": cpv,
                "repository": repository,
                "exact_atom": f"={cpv}::{repository}",
                "normalized_display": " ".join(line.split()),
            }
        )
    if not rows or len({row["cpv"] for row in rows}) != len(rows):
        fail("pretend selection is empty or duplicated")
    if sum(row["cpv"].startswith("dev-python/jsonschema-") for row in rows) != 1:
        fail("pretend did not select exactly one jsonschema CPV")
    canonical_rows = sorted(rows, key=lambda row: (row["cpv"], row["repository"]))
    return {
        "schema_version": 1,
        "ordered_exact_atoms": [row["exact_atom"] for row in rows],
        "rows": canonical_rows,
        "rows_sha256": sha256_bytes(canonical_json(canonical_rows)),
    }


def compare_plans(expected: object, observed: object) -> None:
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        fail("pretend plan is not an object")
    for key in ("ordered_exact_atoms", "rows", "rows_sha256"):
        if expected.get(key) != observed.get(key):
            fail("exact re-pretend plan differs from the reviewed plan")


def installed_cpvs(vdb: Path) -> set[str]:
    result: set[str] = set()
    if not vdb.is_dir() or vdb.is_symlink():
        fail(f"VDB is not a real directory: {vdb}")
    for category in vdb.iterdir():
        if not category.is_dir() or category.is_symlink() or category.name.startswith("."):
            continue
        for package in category.iterdir():
            if not package.is_dir() or package.is_symlink() or package.name.startswith("."):
                continue
            cpv = f"{category.name}/{package.name}"
            if not CPV_PATTERN.fullmatch(cpv):
                fail(f"unsafe installed CPV path: {cpv}")
            result.add(cpv)
    if not result:
        fail("installed CPV set is empty")
    return result


def vdb_manifest(vdb: Path) -> dict[str, Any]:
    """Bind every installed package subtree without volatile VDB lock state.

    Category directories are intentionally excluded.  Adding and later
    removing the only package in a previously absent category may leave an
    empty category directory, which is not installed-package authority.  Each
    package subtree, including its repository, SLOT, USE, dependency and
    CONTENTS metadata, remains bound in full.
    """

    cpvs = sorted(installed_cpvs(vdb))
    packages = []
    for cpv in cpvs:
        package_root = vdb / cpv
        manifest = tree_manifest(package_root)
        packages.append(
            {
                "cpv": cpv,
                "tree": manifest,
                "tree_sha256": sha256_bytes(canonical_json(manifest)),
            }
        )
    return {
        "schema_version": 1,
        "cpvs": cpvs,
        "cpvs_sha256": sha256_bytes(("\n".join(cpvs) + "\n").encode()),
        "packages": packages,
        "packages_sha256": sha256_bytes(canonical_json(packages)),
    }


def compare_vdb(expected: object, observed: object) -> None:
    if expected != observed:
        fail("live VDB differs from the prepared package-manager authority")


def file_observation(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"path": os.fspath(path), "type": "absent"}
    base: dict[str, Any] = {"path": os.fspath(path), **FileIdentity.observe(path).as_json()}
    if stat.S_ISREG(metadata.st_mode):
        base.update(type="file", sha256=sha256_file(path))
    elif stat.S_ISLNK(metadata.st_mode):
        base.update(type="symlink", target=os.readlink(path))
    elif stat.S_ISDIR(metadata.st_mode):
        manifest = tree_manifest(path)
        base.update(
            type="directory",
            tree=manifest,
            tree_sha256=sha256_bytes(canonical_json(manifest)),
        )
    else:
        fail(f"unsupported package-manager authority object: {path}")
    return base


def selected_sets_authority(paths: Paths) -> dict[str, Any]:
    preserved = paths.var_lib_portage / "preserved_libs_registry"
    require_semantically_empty_preserved_registry(preserved)
    return {
        "world": file_observation(paths.var_lib_portage / "world"),
        "world_sets": file_observation(paths.var_lib_portage / "world_sets"),
        "mtimedb": file_observation(paths.cache_edb / "mtimedb"),
        "preserved_libs_registry": file_observation(preserved),
    }


def verify_selected_sets(paths: Paths, expected: object) -> None:
    if selected_sets_authority(paths) != expected:
        fail("selected sets or Portage resume authority changed")


def require_semantically_empty_preserved_registry(path: Path) -> None:
    value = read_json_regular(path, "preserved-libraries registry")
    if value != {}:
        fail("preserved-libraries registry is not semantically empty")


def private_portage_outputs(private_roots: Mapping[str, str]) -> dict[str, Any]:
    root_value = private_roots.get("var_lib_portage")
    if not isinstance(root_value, str):
        fail("private Portage root is absent")
    root = Path(root_value)
    return {
        name: file_observation(root / name)
        for name in ("config", "preserved_libs_registry", "repo_revisions", "world", "world_sets")
    }


def verify_private_portage_outputs(private_roots: Mapping[str, str], expected: object) -> None:
    if private_portage_outputs(private_roots) != expected:
        fail("private Portage config/sets/preserved-library authority changed")


def directory_authority(path: Path, label: str) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if not resolved.is_dir() or resolved.is_symlink():
        fail(f"{label} is not a real directory")
    validate_ancestor_chain(resolved, 0, 0, Path("/"))
    manifest = tree_manifest(resolved)
    return {
        "requested_path": os.fspath(path),
        "resolved_path": os.fspath(resolved),
        "manifest": manifest,
        "manifest_sha256": sha256_bytes(canonical_json(manifest)),
    }


def framework_authority(paths: Paths) -> dict[str, Any]:
    current = paths.rooted("/var/lib/gentoo-optimization/framework-current")
    if not current.is_symlink():
        fail("active framework selector is not a symlink")
    validate_ancestor_chain(current.parent, 0, 0, Path("/"))
    selector_metadata = current.lstat()
    if selector_metadata.st_uid != 0 or selector_metadata.st_gid != 0:
        fail("active framework selector is not root-owned")
    resolved = current.resolve(strict=True)
    if resolved.parent != current.parent:
        fail("active framework candidate is not a direct child of its trusted parent")
    return {
        "selector": file_observation(current),
        "candidate": directory_authority(resolved, "active framework candidate"),
        "stable_libexec": directory_authority(
            paths.rooted("/usr/local/libexec/gentoo-optimization"), "stable framework libexec"
        ),
        "stable_share": directory_authority(
            paths.rooted("/usr/local/share/gentoo-optimization"), "stable framework share"
        ),
        "portage_resolved_target": os.fspath(paths.portage_config.resolve(strict=True)),
    }


def verify_framework_authority(paths: Paths, expected: object) -> None:
    if framework_authority(paths) != expected:
        fail("active framework or stable bootstrap authority changed")


def discover_repositories() -> list[RepositorySpec]:
    try:
        import portage
    except ImportError as error:
        fail(f"Portage Python API is unavailable: {error}")
    result: list[RepositorySpec] = []
    names: set[str] = set()
    for config in portage.settings.repositories:
        name = str(config.name)
        if name in names:
            fail(f"duplicate configured repository: {name}")
        names.add(name)
        sync_type = getattr(config, "sync_type", None)
        key_value = getattr(config, "sync_openpgp_key_path", None)
        max_age_raw = config.module_specific_options.get("sync-rsync-verify-max-age", "0")
        try:
            max_age = int(max_age_raw or 0)
        except ValueError:
            fail(f"repository {name} has an invalid verification max age")
        spec = RepositorySpec(
            name=name,
            location=Path(config.location),
            sync_type=sync_type,
            masters=tuple(str(master.name) for master in getattr(config, "masters", ())),
            key_path=Path(key_value) if key_value else None,
            max_age_days=max_age,
        )
        spec.validate()
        result.append(spec)
    if not result or "gentoo" not in names:
        fail("configured repository set is empty or lacks gentoo")
    for spec in result:
        missing = set(spec.masters) - names
        if missing:
            fail(f"repository {spec.name} has unavailable masters: {sorted(missing)}")
    return sorted(result, key=lambda item: item.name)


def repository_vector(repositories: Sequence[RepositorySpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": repository.name,
            "location": os.fspath(repository.location.resolve(strict=True)),
            "sync_type": repository.sync_type,
            "masters": list(repository.masters),
        }
        for repository in sorted(repositories, key=lambda item: item.name)
    ]


def observe_repositories_command() -> int:
    repositories = discover_repositories()
    print(json.dumps(repository_vector(repositories), sort_keys=True), flush=True)
    return 0


class RsyncVerifier(Protocol):
    def __call__(self, repository: RepositorySpec, clone: Path) -> dict[str, Any]: ...


def verify_rsync_clone(repository: RepositorySpec, clone: Path) -> dict[str, Any]:
    """Perform full recursive Gemato CLI verification on the private clone."""

    repository.validate()
    if repository.sync_type != "rsync" or repository.key_path is None:
        fail("Gemato verification requires an rsync repository and reviewed key")
    manifest = clone / "Manifest"
    if not manifest.is_file() or manifest.is_symlink():
        fail(f"rsync repository has no trusted top Manifest: {clone}")
    key_identity = file_observation(repository.key_path)
    runner: Runner = SubprocessRunner()
    command = [
        "/usr/bin/gemato",
        "verify",
        "--quiet",
        "--openpgp-key",
        os.fspath(repository.key_path),
        "--no-refresh-keys",
        "--no-wkd",
        "--timeout",
        "30",
        "--jobs",
        "1",
        "--one-file-system",
        "--require-secure-hashes",
        "--require-signed-manifest",
        os.fspath(clone),
    ]
    verification = runner.run(
        command,
        environment=clean_environment({"GNUPG": "/usr/bin/gpg", "GNUPGCONF": "/usr/bin/gpgconf"}),
        timeout=8 * 3600,
    )
    if verification.status != 0:
        fail(
            "full recursive Gemato verification failed: "
            + verification.stderr.decode("utf-8", errors="replace")
        )
    timestamp_rows = [
        line.split(maxsplit=1)[1]
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.startswith("TIMESTAMP ")
    ]
    if len(timestamp_rows) != 1:
        fail("signed rsync repository Manifest has no unique timestamp")
    try:
        signed_at = dt.datetime.fromisoformat(timestamp_rows[0].replace("Z", "+00:00"))
    except ValueError as error:
        fail(f"signed rsync repository Manifest timestamp is invalid: {error}")
    if signed_at.tzinfo is None:
        fail("signed rsync repository Manifest timestamp lacks a timezone")
    if repository.max_age_days and dt.datetime.now(dt.timezone.utc) - signed_at > dt.timedelta(
        days=repository.max_age_days
    ):
        fail(f"rsync repository signed Manifest is older than {repository.max_age_days} days")
    result = {
        "full_recursive_verification": True,
        "top_manifest_sha256": sha256_file(manifest),
        "key": key_identity,
        "tool": inspect_executable(Path(command[0])),
        "argv": command,
        "stdout_sha256": sha256_bytes(verification.stdout),
        "stderr_sha256": sha256_bytes(verification.stderr),
        "exit_status": verification.status,
        "manifest_timestamp": signed_at.isoformat(),
    }
    for name in ("timestamp", "timestamp.chk", "timestamp.commit"):
        path = clone / "metadata" / name
        result[name.replace(".", "_")] = file_observation(path)
    return result


def python_module_authority(name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
        fail(f"unsafe Python module authority name: {name}")
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        fail(f"Python package authority is unavailable: {name}")
    roots = sorted(Path(item).resolve(strict=True) for item in spec.submodule_search_locations)
    manifests = []
    for root in roots:
        validate_ancestor_chain(root, 0, 0, Path("/"))
        manifest = tree_manifest(root)
        manifests.append(
            {
                "path": os.fspath(root),
                "manifest": manifest,
                "manifest_sha256": sha256_bytes(canonical_json(manifest)),
            }
        )
    return {
        "name": name,
        "roots": manifests,
        "roots_sha256": sha256_bytes(canonical_json(manifests)),
    }


def revalidate_python_module_authority(value: object) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        fail("Python package authority row is invalid")
    if python_module_authority(value["name"]) != value:
        fail(f"Python package authority changed: {value['name']}")


def git_environment() -> dict[str, str]:
    return clean_environment(
        {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )


def git_argv(git: Path, *arguments: str) -> list[str]:
    return [
        os.fspath(git),
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.attributesFile=/dev/null",
        *arguments,
    ]


def materialize_repository(
    repository: RepositorySpec,
    destination: Path,
    *,
    runner: Runner,
    tools: Mapping[str, Path],
    rsync_verifier: RsyncVerifier = verify_rsync_clone,
) -> dict[str, Any]:
    repository.validate()
    source = repository.location.resolve(strict=True)
    if not source.is_dir() or source.is_symlink():
        fail(f"repository source is not a real directory: {source}")
    provenance: dict[str, Any] = {
        "name": repository.name,
        "configured_location": os.fspath(repository.location),
        "source_location": os.fspath(source),
        "materialized_location": os.fspath(destination),
        "sync_type": repository.sync_type,
        "masters": list(repository.masters),
    }
    if repository.sync_type == "git":
        head = runner.run(
            git_argv(tools["git"], "-C", os.fspath(source), "rev-parse", "--verify", "HEAD^{commit}"),
            environment=git_environment(),
            timeout=60,
        )
        commit = head.stdout.decode("ascii", errors="strict").strip()
        if head.status != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            fail(f"cannot bind exact Git commit for {repository.name}")
        source_clean = runner.run(
            git_argv(
                tools["git"],
                "-C",
                os.fspath(source),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ),
            environment=git_environment(),
            timeout=60,
        )
        if source_clean.status != 0 or source_clean.stdout:
            fail(f"Git repository source is not exact and clean: {repository.name}")
        clone = runner.run(
            git_argv(
                tools["git"],
                "clone",
                "--no-hardlinks",
                "--no-checkout",
                "--",
                os.fspath(source),
                os.fspath(destination),
            ),
            environment=git_environment(),
            timeout=3600,
        )
        if clone.status != 0:
            fail(f"cannot clone Git repository {repository.name}: {clone.stderr.decode(errors='replace')}")
        checkout = runner.run(
            git_argv(
                tools["git"],
                "-C",
                os.fspath(destination),
                "checkout",
                "--detach",
                "--force",
                commit,
            ),
            environment=git_environment(),
            timeout=3600,
        )
        if checkout.status != 0:
            fail(f"cannot check out exact Git repository {repository.name}")
        repeated = runner.run(
            git_argv(
                tools["git"],
                "-C",
                os.fspath(destination),
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ),
            environment=git_environment(),
            timeout=60,
        )
        clean = runner.run(
            git_argv(
                tools["git"],
                "-C",
                os.fspath(destination),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ),
            environment=git_environment(),
            timeout=60,
        )
        if repeated.status != 0 or repeated.stdout.decode().strip() != commit or clean.status != 0 or clean.stdout:
            fail(f"materialized Git repository {repository.name} is not the exact clean commit")
        source_repeated = runner.run(
            git_argv(tools["git"], "-C", os.fspath(source), "rev-parse", "--verify", "HEAD^{commit}"),
            environment=git_environment(),
            timeout=60,
        )
        if source_repeated.status != 0 or source_repeated.stdout.decode().strip() != commit:
            fail(f"Git repository source moved during materialization: {repository.name}")
        provenance["git"] = {"commit": commit, "clean": True}
    else:
        source_before = tree_manifest(source) if repository.sync_type is None else None
        copy_tree(source, destination, runner, tools)
        if repository.sync_type == "rsync":
            provenance["rsync"] = rsync_verifier(repository, destination)
        else:
            destination_before_seal = tree_manifest(destination)
            source_after = tree_manifest(source)
            if (
                source_before is None
                or source_before["rows"] != source_after["rows"]
                or source_before["rows_sha256"] != source_after["rows_sha256"]
                or source_before["rows"] != destination_before_seal["rows"]
            ):
                fail(f"local repository changed or copied inexactly: {repository.name}")
            provenance["local"] = {
                "full_tree_bound": True,
                "source_rows_sha256": source_before["rows_sha256"],
            }
    normalize_tree_ownership(destination, 0 if os.geteuid() == 0 else os.geteuid(), 0 if os.geteuid() == 0 else os.getegid())
    manifest = tree_manifest(destination)
    manifest_path = destination.parent / f"{repository.name}.manifest.json"
    provenance["tree_manifest_path"] = os.fspath(manifest_path)
    provenance["tree_manifest_sha256"] = write_manifest(manifest_path, manifest)
    return provenance


def exact_plan_atoms(plan: object) -> list[str]:
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != 1
        or not isinstance(plan.get("ordered_exact_atoms"), list)
        or not isinstance(plan.get("rows"), list)
    ):
        fail("reviewed plan has an invalid exact atom vector")
    atoms = plan["ordered_exact_atoms"]
    if not atoms or any(not isinstance(atom, str) or EXACT_ATOM_PATTERN.fullmatch(atom) is None for atom in atoms):
        fail("reviewed plan contains an unsafe exact atom")
    if len(atoms) != len(set(atoms)):
        fail("reviewed plan contains duplicate exact atoms")
    expected_rows = []
    for row in plan["rows"]:
        if not isinstance(row, dict) or set(row) != {"cpv", "repository", "exact_atom", "normalized_display"}:
            fail("reviewed plan contains an invalid row")
        match = EXACT_ATOM_PATTERN.fullmatch(str(row["exact_atom"]))
        if match is None or match.group("cpv") != row["cpv"] or match.group("repository") != row["repository"]:
            fail("reviewed plan row identities disagree")
        expected_rows.append(row)
    if sorted(expected_rows, key=lambda row: (row["cpv"], row["repository"])) != expected_rows:
        fail("reviewed plan rows are not canonically ordered")
    if sha256_bytes(canonical_json(expected_rows)) != plan.get("rows_sha256"):
        fail("reviewed plan row digest differs")
    if set(atoms) != {row["exact_atom"] for row in expected_rows}:
        fail("reviewed plan ordered atom vector differs from its rows")
    return list(atoms)


def synthetic_vdb(root: Path, cpvs: Iterable[str]) -> None:
    if path_exists(root):
        fail(f"synthetic VDB already exists: {root}")
    root.mkdir(mode=0o700, parents=True)
    for cpv in sorted(set(cpvs)):
        if not CPV_PATTERN.fullmatch(cpv):
            fail(f"unsafe synthetic VDB CPV: {cpv}")
        category, package = cpv.split("/", 1)
        (root / category).mkdir(mode=0o700, exist_ok=True)
        (root / category / package).mkdir(mode=0o700)
    fsync_directory(root)


def verify_private_pkgdir(
    pkgdir: Path,
    cpvs: Sequence[str],
    verifier: Path,
    python: Path,
    zstd: Path,
    runner: Runner,
    evidence: Path,
) -> dict[str, Any]:
    if path_exists(evidence):
        fail(f"private PKGDIR verification evidence already exists: {evidence}")
    evidence.mkdir(mode=0o700)
    expected_vdb = evidence / "selected-vdb"
    synthetic_vdb(expected_vdb, cpvs)
    result = runner.run(
        [os.fspath(python), "-I", "-B", os.fspath(verifier), "--snapshot", os.fspath(pkgdir), "--vdb", os.fspath(expected_vdb), "--zstd", os.fspath(zstd), "--format", "json", "--validate-gpkg"],
        environment=clean_environment(),
        timeout=8 * 3600,
    )
    if result.status != 0 or result.stderr:
        fail(f"private PKGDIR verification failed with status {result.status}")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        fail(f"private PKGDIR verifier returned invalid JSON: {error}")
    expected = len(set(cpvs))
    if (
        report.get("status") != "pass"
        or report.get("counts", {}).get("live_cpvs") != expected
        or report.get("counts", {}).get("indexed_unique_cpvs") != expected
        or report.get("counts", {}).get("gpkg_archives_validated") != expected
        or report.get("counts", {}).get("errors") != 0
    ):
        fail("private PKGDIR verifier did not prove exact selected membership")
    return report


def base_state(
    paths: Paths,
    *,
    authority: dict[str, Any],
    resolver: dict[str, Any],
    plan: dict[str, Any],
    private_roots: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "transaction_id": paths.transaction_id,
        "phase": "prepared",
        "recorded_at": utc_now(),
        "boot_id": boot_id(paths.proc_root),
        "previous_phase": None,
        "previous_state_sha256": None,
        "prepared_state_sha256": None,
        "authority": authority,
        "resolver": resolver,
        "plan": plan,
        "private_roots": private_roots,
        "child": None,
        "outcome": None,
        "recovery_contract": {
            "claim": "declared-package-manager-authorities-only",
            "whole_host_byte_identity": False,
            "source_emerge_may_never_be_retried_after_armed": True,
            "live_edb_counter_is_monotonic_nonrollback_axis": True,
            "authorities": [
                "installed-cpv-set",
                "pre-existing-vdb-package-subtrees",
                "world",
                "world_sets",
                "live-pkgdir-distdir-tmpdir-logdir-ccache-nonuse",
            ],
        },
        "evidence": {"directory": os.fspath(paths.report), "proc_root": os.fspath(paths.proc_root)},
        "pending_total": 1,
        "unknown_total": 0,
        "failed_total": 0,
    }


def classify_vdb_delta(before: dict[str, Any], after: dict[str, Any], plan: object) -> dict[str, Any]:
    before_cpvs = set(before.get("cpvs", []))
    after_cpvs = set(after.get("cpvs", []))
    planned = {row["cpv"] for row in plan.get("rows", [])} if isinstance(plan, dict) and isinstance(plan.get("rows"), list) else set()
    added = sorted(after_cpvs - before_cpvs)
    removed = sorted(before_cpvs - after_cpvs)
    unexpected = sorted(set(added) - planned)
    missing = sorted(planned - set(added))
    return {
        "added": added,
        "removed": removed,
        "unexpected_added": unexpected,
        "planned_not_added": missing,
        "exact_success_delta": not removed and not unexpected and not missing,
        "rollback_eligible": not removed and not unexpected and set(added) <= planned,
    }


def rollback_order(plan: object, installed_added: Iterable[str]) -> list[str]:
    added = set(installed_added)
    atoms = exact_plan_atoms(plan)
    cpv_by_atom = {atom[1:].split("::", 1)[0]: atom for atom in atoms}
    if not added <= set(cpv_by_atom):
        fail("rollback delta contains a package outside the reviewed plan")
    ordered_cpvs = [atom[1:].split("::", 1)[0] for atom in atoms]
    return [cpv_by_atom[cpv] for cpv in reversed(ordered_cpvs) if cpv in added]


def read_counter(path: Path, label: str) -> int:
    try:
        payload = path.read_text(encoding="ascii").strip()
    except OSError as error:
        fail(f"cannot read {label}: {error}")
    if not payload.isdecimal():
        fail(f"{label} is not a nonnegative decimal counter")
    return int(payload)


def reconcile_counter_locked(
    *,
    live_edb: Path,
    private_edb: Path,
    vdb: Path,
    prepared: Mapping[str, Any],
    outcome: str,
) -> dict[str, Any]:
    """Advance, never restore, Portage's counter under the exact VDB lock."""

    live_path = live_edb / "counter"
    private_path = private_edb / "counter"
    current_vdb = vdb_manifest(vdb)
    if outcome == "success":
        delta = classify_vdb_delta(
            prepared["resolver"]["vdb_before"], current_vdb, prepared["plan"]
        )
        if not delta["exact_success_delta"]:
            fail("cannot reconcile success counter without the exact installed delta")
        package_values = [
            read_counter(vdb / row["cpv"] / "COUNTER", f"{row['cpv']} COUNTER")
            for row in prepared["plan"]["rows"]
        ]
    elif outcome == "rolled-back":
        compare_vdb(prepared["resolver"]["vdb_before"], current_vdb)
        package_values = []
    else:
        fail(f"unsupported EDB counter reconciliation outcome: {outcome}")
    before = read_counter(live_path, "live EDB counter")
    private = read_counter(private_path, "private EDB counter")
    selected = max([before, private, *package_values])
    metadata = live_path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail("live EDB counter is not a regular file")
    if selected != before:
        partial = live_path.with_name(f".{live_path.name}.partial.{os.getpid()}")
        descriptor = os.open(
            partial,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            stat.S_IMODE(metadata.st_mode),
        )
        try:
            # Portage's canonical counter payload is a decimal with no newline.
            payload = str(selected).encode("ascii")
            if os.write(descriptor, payload) != len(payload):
                fail("short write during live EDB counter reconciliation")
            os.fchown(descriptor, metadata.st_uid, metadata.st_gid)
            os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(partial, live_path)
        fsync_directory(live_path.parent)
    after = read_counter(live_path, "reconciled live EDB counter")
    if after != selected:
        fail("live EDB counter reconciliation did not publish the selected value")
    return {
        "outcome": outcome,
        "before": before,
        "private": private,
        "package_max": max(package_values) if package_values else None,
        "after": after,
    }


def tree_manifest_without_top_level(root: Path, excluded: set[str]) -> dict[str, Any]:
    manifest = tree_manifest(root)
    rows = [
        row
        for row in manifest["rows"]
        if str(row["path"]).split("/", 1)[0] not in excluded
    ]
    return {
        "schema_version": 1,
        "root": manifest["root"],
        "rows": rows,
        "rows_sha256": sha256_bytes(canonical_json(rows)),
    }


def path_is_read_only(path: Path) -> bool:
    return bool(os.statvfs(path).f_flag & os.ST_RDONLY)


def remount_bind_read_only(path: Path, read_only: bool, mount_tool: Path) -> None:
    mode = "ro" if read_only else "rw"
    result = subprocess.run(
        [
            os.fspath(mount_tool),
            "-o",
            f"remount,bind,{mode},nodev,nosuid",
            "--",
            os.fspath(path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_environment(),
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        fail(
            f"cannot remount live EDB counter view {mode}: "
            + result.stderr.decode("utf-8", errors="replace")
        )


def reconcile_counter_with_reseal(
    *,
    live_edb: Path,
    private_edb: Path,
    vdb: Path,
    prepared: Mapping[str, Any],
    outcome: str,
    remount: Callable[[Path, bool], None],
    is_read_only: Callable[[Path], bool] = path_is_read_only,
) -> dict[str, Any]:
    """Temporarily expose only the held-lock counter update, then reseal RO."""

    if not is_read_only(live_edb):
        fail("live EDB counter view is writable before reconciliation")
    before = tree_manifest_without_top_level(live_edb, {"counter"})
    reconciliation: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    try:
        remount(live_edb, False)
        if is_read_only(live_edb):
            fail("live EDB counter view remained read-only after narrow authorization")
        reconciliation = reconcile_counter_locked(
            live_edb=live_edb,
            private_edb=private_edb,
            vdb=vdb,
            prepared=prepared,
            outcome=outcome,
        )
    except BaseException as error:
        primary_error = error
    try:
        remount(live_edb, True)
        if not is_read_only(live_edb):
            fail("live EDB counter view was not resealed read-only")
    except BaseException as reseal_error:
        fail(
            "live EDB counter view reseal failed"
            + (f" after {primary_error}" if primary_error is not None else "")
            + f": {reseal_error}"
        )
    after = tree_manifest_without_top_level(live_edb, {"counter"})
    if after != before:
        fail("live EDB authority outside counter changed during reconciliation")
    if primary_error is not None:
        raise primary_error
    if reconciliation is None:
        fail("counter reconciliation produced no result")
    return {
        **reconciliation,
        "non_counter_manifest_sha256": after["rows_sha256"],
        "resealed_read_only": True,
    }


def default_tools(root: Path = Path("/")) -> dict[str, Path]:
    def tool(path: str) -> Path:
        return root / Path(path).relative_to("/") if root != Path("/") else Path(path)

    return {
        "bash": tool("/bin/bash"),
        "cp": tool("/usr/bin/cp"),
        "emerge": tool("/usr/bin/emerge"),
        "false": tool("/bin/false"),
        "gemato": tool("/usr/bin/gemato"),
        "git": tool("/usr/bin/git"),
        "gpg": tool("/usr/bin/gpg"),
        "gpgconf": tool("/usr/bin/gpgconf"),
        "mount": tool("/usr/bin/mount"),
        "python": tool("/usr/bin/python3.15"),
        "qcheck": tool("/usr/bin/qcheck"),
        "umount": tool("/usr/bin/umount"),
        "unshare": tool("/usr/bin/unshare"),
        "wget": tool("/usr/bin/wget"),
        "zstd": tool("/usr/bin/zstd"),
        "transaction": Path(__file__).resolve(strict=True),
        "snapshot_verifier": Path(__file__).with_name("verify-binpkg-snapshot.py").resolve(strict=True),
    }


def validate_root_owned_lock_ancestors(path: Path) -> None:
    current = path
    while True:
        metadata = current.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            fail(f"untrusted stable-lock ancestor: {current}")
        if current == Path("/"):
            return
        current = current.parent


def acquire_flock(
    path: Path,
    *,
    exclusive: bool,
    nonblocking: bool = True,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    expected_mode: int | None = None,
    validate_ancestors: bool = False,
) -> int:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        fail(f"stable transaction lock is absent or foreign: {path}")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_size != 0
        or (expected_uid is not None and metadata.st_uid != expected_uid)
        or (expected_gid is not None and metadata.st_gid != expected_gid)
        or (expected_mode is not None and stat.S_IMODE(metadata.st_mode) != expected_mode)
    ):
        fail(f"stable transaction lock is absent or foreign: {path}")
    if validate_ancestors:
        validate_root_owned_lock_ancestors(path.parent)
    descriptor = os.open(path, os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if nonblocking:
        operation |= fcntl.LOCK_NB
    try:
        fcntl.flock(descriptor, operation)
    except OSError:
        os.close(descriptor)
        raise
    if FileIdentity.observe(path) != FileIdentity.observe(Path(f"/proc/self/fd/{descriptor}"), follow=True):
        os.close(descriptor)
        fail(f"stable transaction lock path/fd identity changed: {path}")
    return descriptor


@contextlib.contextmanager
def transaction_locks(paths: Paths) -> Iterator[None]:
    descriptors: list[int] = []
    uid = os.geteuid() if paths.fixture_mode else 0
    fixture_gid = os.getegid()
    portage_gid = fixture_gid if paths.fixture_mode else grp.getgrnam("portage").gr_gid
    root_gid = fixture_gid if paths.fixture_mode else 0
    strict_ancestors = not paths.fixture_mode
    shared_lock_mode = 0o600 if paths.fixture_mode else 0o640
    try:
        for path, exclusive, gid, mode in (
            (paths.framework_lock, False, portage_gid, shared_lock_mode),
            (paths.project_lock, True, portage_gid, shared_lock_mode),
            (paths.generation_lock, False, portage_gid, shared_lock_mode),
            (paths.transaction_lock, True, root_gid, 0o600),
        ):
            descriptors.append(
                acquire_flock(
                    path,
                    exclusive=exclusive,
                    expected_uid=uid,
                    expected_gid=gid,
                    expected_mode=mode,
                    validate_ancestors=strict_ancestors,
                )
            )
        yield
    except BlockingIOError:
        fail("another framework, project, generation, or prerequisite transaction is active")
    finally:
        for descriptor in reversed(descriptors):
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


def validate_pre_checkpoint(path: Path, *, enforce_root_trust: bool = False) -> dict[str, Any]:
    phase_path: Path | None = None
    expected_checkpoint_id: str | None = None
    if enforce_root_trust:
        require_absolute(path, "pre-dependency checkpoint state")
        validate_ancestor_chain(path.parent, 0, 0, Path("/"))
        match = re.fullmatch(r"binpkg-checkpoint-(?P<id>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})\.json", path.name)
        if match is None:
            fail("pre-dependency checkpoint path is not the canonical checkpoint state")
        expected_checkpoint_id = match.group("id")
        phase_path = path.with_name(
            f"binpkg-checkpoint-{expected_checkpoint_id}.offline-restore-proven.json"
        )
        canonical_identity = validate_trusted_regular(path, 0, 0, one_link=False)
        phase_identity = validate_trusted_regular(phase_path, 0, 0, one_link=False)
        if (
            canonical_identity.mode != 0o600
            or phase_identity.mode != 0o600
            or canonical_identity.nlink != 2
            or phase_identity.nlink != 2
            or canonical_identity != phase_identity
        ):
            fail("checkpoint canonical/terminal phase files are not the exact trusted hardlink pair")
    value = read_json_regular(path, "pre-dependency checkpoint state")
    if not isinstance(value, dict):
        fail("pre-dependency checkpoint state is not an object")
    if (
        value.get("schema_version") != 2
        or value.get("control") != "exact-live-binpkg-checkpoint"
        or value.get("status") != "offline-restore-proven"
        or value.get("offline_restoration_tested") is not True
        or any(value.get(key) != 0 for key in ("pending_total", "unknown_total", "failed_total"))
    ):
        fail("pre-dependency checkpoint is not terminal and fully proven")
    if enforce_root_trust and value.get("checkpoint_id") != expected_checkpoint_id:
        fail("pre-dependency checkpoint ID differs from its canonical path")
    return {
        "path": os.fspath(path),
        "sha256": sha256_file(path),
        "identity": FileIdentity.observe(path).as_json(),
        "phase_path": os.fspath(phase_path) if phase_path is not None else None,
        "phase_sha256": sha256_file(phase_path) if phase_path is not None else None,
        "phase_identity": FileIdentity.observe(phase_path).as_json() if phase_path is not None else None,
        "checkpoint_id": value.get("checkpoint_id"),
        "status": "offline-restore-proven",
    }


def revalidate_pre_checkpoint_authority(value: object) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("path"), str):
        fail("prepared authority lacks its pre-dependency checkpoint")
    current = validate_pre_checkpoint(Path(value["path"]), enforce_root_trust=True)
    if current != value:
        fail("pre-dependency checkpoint authority changed after preparation")


def ensure_trusted_child_directory(
    path: Path, parent: Path, *, uid: int, gid: int, mode: int
) -> None:
    require_direct_child(path, parent, "trusted child directory")
    validate_ancestor_chain(parent, uid, gid, Path("/"))
    parent_fd = os.open(
        parent,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        try:
            os.mkdir(path.name, mode=mode, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or stat.S_IMODE(metadata.st_mode) != mode
        ):
            fail(f"trusted transaction directory has foreign identity: {path}")
        path_fd = os.open(
            path.name,
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            opened = os.fstat(path_fd)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                fail(f"trusted transaction directory changed during open: {path}")
        finally:
            os.close(path_fd)
    finally:
        os.close(parent_fd)


def prepare_directories(paths: Paths, fixture_mode: bool) -> None:
    uid = os.geteuid() if fixture_mode else 0
    gid = os.getegid() if fixture_mode else 0
    if fixture_mode:
        for parent in (paths.state_parent, paths.report_parent, paths.authority_parent, paths.cache_parent):
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(parent, 0o700)
    else:
        # These two parents are installed framework trust anchors and may not
        # be synthesized through an attacker-controlled ancestor.
        validate_ancestor_chain(paths.state_parent, uid, gid, Path("/"))
        validate_ancestor_chain(paths.report_parent, uid, gid, Path("/"))
        ensure_trusted_child_directory(
            paths.authority_parent,
            paths.rooted("/var/lib/gentoo-optimization/recovery"),
            uid=uid,
            gid=gid,
            mode=0o700,
        )
        ensure_trusted_child_directory(
            paths.cache_parent,
            paths.rooted("/var/cache/gentoo-optimization"),
            uid=uid,
            gid=gid,
            mode=0o700,
        )
    if not paths.transaction_lock.exists():
        parent_fd = os.open(
            paths.state_parent,
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            try:
                descriptor = os.open(
                    paths.transaction_lock.name,
                    os.O_CREAT
                    | os.O_EXCL
                    | os.O_WRONLY
                    | os.O_CLOEXEC
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                descriptor = -1
            if descriptor >= 0:
                try:
                    if not fixture_mode:
                        os.fchown(descriptor, 0, 0)
                    os.fchmod(descriptor, 0o600)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    expected_uid = os.geteuid() if fixture_mode else 0
    expected_gid = os.getegid() if fixture_mode else 0
    lock_identity = validate_trusted_regular(
        paths.transaction_lock, expected_uid, expected_gid, one_link=True
    )
    if lock_identity.mode != 0o600:
        fail("stable prerequisite transaction lock has an invalid mode")


def prepare_private_roots(paths: Paths) -> dict[str, Any]:
    roots = {
        "pkgdir": paths.cache / "pkgdir",
        "distdir_staging": paths.cache / "distfiles.staging",
        "distdir_runtime": paths.cache / "distfiles.runtime",
        "portage_tmpdir": paths.cache / "tmp",
        "portage_logdir": paths.cache / "logs",
        "ccache_dir": paths.cache / "ccache",
        "var_lib_portage": paths.cache / "var-lib-portage",
        "cache_edb": paths.cache / "cache-edb",
        "home": paths.cache / "home",
        "xdg_cache": paths.cache / "xdg-cache",
        "live_cache_edb_view": paths.cache / "live-cache-edb-view",
    }
    paths.cache.mkdir(mode=0o700)
    portage_gid = os.getegid() if paths.fixture_mode else grp.getgrnam("portage").gr_gid
    for key, root in roots.items():
        if key == "portage_tmpdir":
            mode, gid = 0o1777, 0 if not paths.fixture_mode else os.getegid()
        elif key in {"distdir_staging", "distdir_runtime", "portage_logdir", "ccache_dir"}:
            mode, gid = 0o2775, portage_gid
        elif key in {"home", "xdg_cache", "live_cache_edb_view"}:
            mode, gid = 0o700, 0 if not paths.fixture_mode else os.getegid()
        else:
            mode, gid = 0o755, 0 if not paths.fixture_mode else os.getegid()
        root.mkdir(mode=mode)
        os.chmod(root, mode)
        if not paths.fixture_mode:
            os.chown(root, 0, gid)
    shutil.copytree(paths.var_lib_portage, roots["var_lib_portage"], symlinks=True, dirs_exist_ok=True)
    shutil.copytree(paths.cache_edb, roots["cache_edb"], symlinks=True, dirs_exist_ok=True)
    result = {key: os.fspath(value) for key, value in roots.items()}
    result.update(
        {
            "live_var_lib_portage": os.fspath(paths.var_lib_portage.resolve(strict=True)),
            "live_cache_edb": os.fspath(paths.cache_edb.resolve(strict=True)),
        }
    )
    return result


def private_roots_baseline(private_roots: Mapping[str, str]) -> dict[str, Any]:
    """Bind prepared private-root membership before any package build."""

    rows: dict[str, Any] = {}
    for key in (
        "pkgdir",
        "distdir_runtime",
        "portage_tmpdir",
        "portage_logdir",
        "ccache_dir",
        "home",
        "xdg_cache",
        "var_lib_portage",
        "cache_edb",
    ):
        value = private_roots.get(key)
        if not isinstance(value, str):
            fail(f"private root vector lacks {key}")
        root = Path(value)
        manifest = tree_manifest(root)
        rows[key] = {
            "path": value,
            "manifest": manifest,
            "manifest_sha256": sha256_bytes(canonical_json(manifest)),
        }
    for key in ("pkgdir", "distdir_runtime", "portage_tmpdir", "ccache_dir", "home", "xdg_cache"):
        if rows[key]["manifest"]["rows"]:
            fail(f"prepared private root is not empty: {key}")
    return {"schema_version": 1, "rows": rows, "rows_sha256": sha256_bytes(canonical_json(rows))}


def verify_private_roots_baseline(value: object) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("rows"), dict):
        fail("private-root baseline has an invalid schema")
    current: dict[str, Any] = {}
    for key, row in value["rows"].items():
        if not isinstance(key, str) or not isinstance(row, dict) or not isinstance(row.get("path"), str):
            fail("private-root baseline row is invalid")
        manifest = tree_manifest(Path(row["path"]))
        current[key] = {
            "path": row["path"],
            "manifest": manifest,
            "manifest_sha256": sha256_bytes(canonical_json(manifest)),
        }
    if current != value["rows"] or sha256_bytes(canonical_json(current)) != value.get("rows_sha256"):
        fail("a prepared private root changed before transaction arming")


def plan_environment(private_roots: Mapping[str, str], *, offline: bool) -> dict[str, str]:
    environment = clean_environment(
        {
            "CCACHE_DIR": private_roots["ccache_dir"],
            "DISTDIR": private_roots["distdir_runtime"] if offline else private_roots["distdir_staging"],
            "EMERGE_LOG_DIR": private_roots["portage_logdir"],
            "EPYTHON": "python3.15",
            # FEATURES is incremental in Portage.  Negative tokens remove only
            # external/compiler caches and concurrent package installation;
            # normal sandbox, userpriv, network-sandbox and pid-sandbox policy
            # remains sourced from the frozen configuration authority.
            "FEATURES": (
                "-assume-digests -binpkg-signing -ccache -distcc "
                "-icecream -parallel-install"
            ),
            "NOCOLOR": "1",
            "PKGDIR": private_roots["pkgdir"],
            "PORTAGE_BINHOST": "",
            "PORTAGE_LOGDIR": private_roots["portage_logdir"],
            "PORTAGE_TMPDIR": private_roots["portage_tmpdir"],
            "TERM": "dumb",
            "HOME": private_roots["home"],
            "TMPDIR": private_roots["portage_tmpdir"],
            "TMP": private_roots["portage_tmpdir"],
            "TEMP": private_roots["portage_tmpdir"],
            "XDG_CACHE_HOME": private_roots["xdg_cache"],
        }
    )
    if offline:
        environment.update(
            {
                "FETCHCOMMAND": "/bin/false",
                "RESUMECOMMAND": "/bin/false",
                "GENTOO_MIRRORS": "",
                "PORTAGE_RO_DISTDIRS": private_roots["distdir_authority"],
            }
        )
    return environment


def emerge_options() -> list[str]:
    return [
        "--ignore-default-opts",
        "--verbose",
        "--tree",
        "--oneshot",
        "--with-bdeps=y",
        "--complete-graph=y",
        "--autounmask=n",
        "--autounmask-write=n",
        "--buildpkg=y",
        "--getbinpkg=n",
        "--usepkg=n",
        "--keep-going=n",
        "--fail-clean=y",
        "--noconfmem",
        "--nospinner",
        "--color=n",
        "--jobs=1",
        "--package-moves=n",
    ]


def tools_from_manifest(value: object) -> dict[str, Path]:
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        fail("tool manifest is not an object with rows")
    tools: dict[str, Path] = {}
    for row in value["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            fail("tool manifest contains an invalid row")
        requested = row.get("requested_path")
        if not isinstance(requested, str):
            fail("tool manifest row has no requested path")
        if row["name"] in tools:
            fail(f"tool manifest repeats {row['name']}")
        tools[row["name"]] = Path(requested)
    return tools


def authority_mount_bindings(
    authority: Mapping[str, Any], private_roots: Mapping[str, str]
) -> list[MountBinding]:
    """Build the complete mount authority for one Portage observation/action."""

    repositories = authority.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        fail("prepared authority has no repository vector")
    bindings: list[MountBinding] = []
    for repository in repositories:
        if not isinstance(repository, dict):
            fail("prepared repository authority row is invalid")
        source = repository.get("materialized_location")
        target = repository.get("source_location")
        if not isinstance(source, str) or not isinstance(target, str):
            fail("prepared repository authority lacks mount endpoints")
        # The authority source is also visible by its durable path inside the
        # namespace.  Seal that view read-only before exposing the same inode
        # tree at Portage's configured location.
        bindings.append(MountBinding(Path(source), Path(source), True))
        bindings.append(MountBinding(Path(source), Path(target), True))
    for key in ("portage_config", "portage_global_config"):
        row = authority.get(key)
        if not isinstance(row, dict):
            fail(f"prepared authority lacks {key}")
        source, target = row.get("materialized_location"), row.get("mount_target")
        if not isinstance(source, str) or not isinstance(target, str):
            fail(f"prepared authority {key} lacks mount endpoints")
        bindings.append(MountBinding(Path(source), Path(source), True))
        bindings.append(MountBinding(Path(source), Path(target), True))
    modules = authority.get("python_modules")
    if not isinstance(modules, list):
        fail("prepared authority lacks Python package roots")
    for module in modules:
        if not isinstance(module, dict) or not isinstance(module.get("roots"), list):
            fail("prepared Python package authority row is invalid")
        for row in module["roots"]:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                fail("prepared Python package root is invalid")
            root = Path(row["path"])
            bindings.append(MountBinding(root, root, True))
    distfiles = private_roots.get("distdir_authority")
    if not isinstance(distfiles, str):
        fail("private roots lack immutable distfile authority")
    bindings.append(MountBinding(Path(distfiles), Path(distfiles), True))
    live_cache, live_view = private_roots.get("live_cache_edb"), private_roots.get(
        "live_cache_edb_view"
    )
    if not isinstance(live_cache, str) or not isinstance(live_view, str):
        fail("private roots lack live EDB reconciliation view")
    # Capture the live EDB view before hiding its configured path with the
    # private cache bind below.
    # Keep the live EDB anchor read-only throughout package phases.  Live
    # mutation remains globally disabled until a post-action remount/reseal
    # handshake is implemented under the same VDB lock.
    bindings.append(MountBinding(Path(live_cache), Path(live_view), True))
    for key, target_key in (
        ("var_lib_portage", "live_var_lib_portage"),
        ("cache_edb", "live_cache_edb"),
    ):
        source, target = private_roots.get(key), private_roots.get(target_key)
        if not isinstance(source, str) or not isinstance(target, str):
            fail(f"private root authority lacks {key} mount endpoints")
        bindings.append(MountBinding(Path(source), Path(target), False))
    targets = [binding.target.resolve(strict=True) for binding in bindings]
    if len(targets) != len(set(targets)):
        fail("contained Portage authority repeats a mount target")
    for binding in bindings:
        binding.validate()
    return bindings


def execution_spec(
    *,
    bindings: Sequence[MountBinding],
    command: Sequence[str],
    environment: Mapping[str, str],
    network_isolated: bool,
) -> dict[str, Any]:
    if not command or not Path(command[0]).is_absolute():
        fail("contained command must name an absolute executable")
    if any("\0" in argument for argument in command):
        fail("contained command has an invalid argument")
    if any(not isinstance(key, str) or not isinstance(value, str) or "\0" in key + value for key, value in environment.items()):
        fail("contained command environment is invalid")
    rows = [binding.as_json() for binding in bindings]
    payload = {
        "schema_version": 1,
        "network_isolated": network_isolated,
        "mounts": rows,
        "command": list(command),
        "environment": dict(sorted(environment.items())),
    }
    payload["contract_sha256"] = sha256_bytes(canonical_json(payload))
    return payload


def validate_execution_spec(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "network_isolated",
        "mounts",
        "command",
        "environment",
        "contract_sha256",
    }:
        fail("contained execution contract has an invalid schema")
    if value.get("schema_version") != 1 or type(value.get("network_isolated")) is not bool:
        fail("contained execution contract has invalid version or network policy")
    expected = dict(value)
    digest = expected.pop("contract_sha256")
    if require_sha256(digest, "execution contract digest") != sha256_bytes(canonical_json(expected)):
        fail("contained execution contract digest differs")
    command = value.get("command")
    environment = value.get("environment")
    mounts = value.get("mounts")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) for item in command)
        or not Path(command[0]).is_absolute()
        or not isinstance(environment, dict)
        or any(not isinstance(key, str) or not isinstance(item, str) for key, item in environment.items())
        or not isinstance(mounts, list)
    ):
        fail("contained execution contract payload is invalid")
    parsed_bindings: list[MountBinding] = []
    for row in mounts:
        if not isinstance(row, dict) or set(row) != {"source", "target", "read_only"}:
            fail("contained execution mount row is invalid")
        if not isinstance(row["source"], str) or not isinstance(row["target"], str) or type(row["read_only"]) is not bool:
            fail("contained execution mount endpoint is invalid")
        binding = MountBinding(Path(row["source"]), Path(row["target"]), row["read_only"])
        binding.validate()
        parsed_bindings.append(binding)
    targets = [binding.target.resolve(strict=True) for binding in parsed_bindings]
    if len(targets) != len(set(targets)):
        fail("contained execution contract repeats a mount target")
    return value


def contained_argv(spec_path: Path, tools: Mapping[str, Path], *, network_isolated: bool) -> list[str]:
    argv = [
        os.fspath(tools["unshare"]),
        "--mount",
        "--pid",
        "--fork",
        "--kill-child=KILL",
        "--mount-proc",
    ]
    if network_isolated:
        argv.append("--net")
    argv.extend(
        [
            os.fspath(tools["python"]),
            "-I",
            "-B",
            os.fspath(tools["transaction"]),
            "__mount-exec",
            os.fspath(spec_path),
        ]
    )
    return argv


def run_contained_stage(
    *,
    stage: str,
    paths: Paths,
    authority: Mapping[str, Any],
    private_roots: Mapping[str, str],
    command: Sequence[str],
    environment: Mapping[str, str],
    runner: Runner,
    tools: Mapping[str, Path],
    timeout: float,
    network_isolated: bool,
) -> tuple[CommandResult, dict[str, Any]]:
    require_safe_id(stage, "contained stage")
    spec = execution_spec(
        bindings=authority_mount_bindings(authority, private_roots),
        command=command,
        environment=environment,
        network_isolated=network_isolated,
    )
    spec_path = paths.report / f"{stage}.execution.json"
    stdout_path = paths.report / f"{stage}.stdout"
    stderr_path = paths.report / f"{stage}.stderr"
    spec_sha = atomic_publish_noreplace(spec_path, canonical_json(spec))
    parent = process_identity(os.getpid(), paths.proc_root)
    if parent is None:
        fail("cannot observe coordinator identity for contained stage")
    child_argv = [
        os.fspath(tools["python"]),
        "-I",
        "-B",
        os.fspath(tools["transaction"]),
        "__pdeath-exec",
        str(parent["pid"]),
        str(parent["start_ticks"]),
        "--",
        *contained_argv(spec_path, tools, network_isolated=network_isolated),
    ]
    result = runner.run(
        child_argv,
        environment=clean_environment(),
        timeout=timeout,
    )
    stdout_sha = atomic_publish_noreplace(stdout_path, result.stdout)
    stderr_sha = atomic_publish_noreplace(stderr_path, result.stderr)
    evidence = {
        "stage": stage,
        "status": result.status,
        "spec_path": os.fspath(spec_path),
        "spec_sha256": spec_sha,
        "stdout_path": os.fspath(stdout_path),
        "stdout_sha256": stdout_sha,
        "stderr_path": os.fspath(stderr_path),
        "stderr_sha256": stderr_sha,
    }
    return result, evidence


@contextlib.contextmanager
def transaction_signal_scope() -> Iterator[None]:
    previous: dict[int, Any] = {}

    def interrupted(signum: int, _frame: object) -> NoReturn:
        raise TransactionInterrupted(signum)

    try:
        for signum in TRANSACTION_SIGNALS:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupted)
        yield
    finally:
        for recorded_signum, handler in previous.items():
            signal.signal(recorded_signum, handler)


def process_group_members(
    process_group: int, session: int, proc_root: Path = Path("/proc")
) -> list[dict[str, int | str]]:
    members: list[dict[str, int | str]] = []
    for candidate in proc_root.glob("[0-9]*"):
        try:
            identity = parse_proc_stat((candidate / "stat").read_text(encoding="ascii"), int(candidate.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
        if identity["process_group"] == process_group and identity["session"] == session and identity["state"] != "Z":
            members.append(identity)
    return sorted(members, key=lambda item: int(item["pid"]))


def wait_group_empty(
    process_group: int, session: int, *, proc_root: Path = Path("/proc"), timeout: float = 5.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_group_members(process_group, session, proc_root):
            return
        time.sleep(0.02)
    members = process_group_members(process_group, session, proc_root)
    fail(f"contained child process group retained live members: {members}")


def quiesce_recorded_child(child: Mapping[str, Any], proc_root: Path = Path("/proc")) -> None:
    required = {"boot_id", "pid", "process_group", "session", "start_ticks"}
    if not required <= set(child) or any(type(child[key]) is not int for key in required - {"boot_id"}):
        fail("recorded child identity is incomplete")
    if child["boot_id"] != boot_id(proc_root):
        return
    pid = child["pid"]
    expected = {
        "pid": pid,
        "process_group": child["process_group"],
        "session": child["session"],
        "start_ticks": child["start_ticks"],
    }
    observed = process_identity(pid, proc_root)
    if observed is not None:
        for key, value in expected.items():
            if observed[key] != value:
                fail("recorded child numeric PID now has a different identity")
        try:
            descriptor = os.pidfd_open(pid, 0)
        except (AttributeError, OSError) as error:
            fail(f"cannot open pidfd for recorded child: {error}")
        try:
            repeated = process_identity(pid, proc_root)
            if repeated is None or any(repeated[key] != value for key, value in expected.items()):
                fail("recorded child identity changed after pidfd_open")
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(descriptor, signal.SIGTERM, None, 0)
            deadline = time.monotonic() + 5.0
            while process_identity(pid, proc_root) is not None and time.monotonic() < deadline:
                time.sleep(0.02)
            if process_identity(pid, proc_root) is not None:
                with contextlib.suppress(ProcessLookupError):
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL, None, 0)
        finally:
            os.close(descriptor)
    # Never send to a numeric process group whose exact leader has vanished.
    # The kill-child PID namespace must tear down descendants; residue is an
    # explicit recovery failure rather than permission to risk another task.
    wait_group_empty(child["process_group"], child["session"], proc_root=proc_root)


def publish_child_sidecar(
    paths: Paths,
    identity: Mapping[str, Any],
    spec_path: Path,
    control_session: str | None = None,
    destination: Path | None = None,
) -> dict[str, Any]:
    child = {
        "boot_id": boot_id(paths.proc_root),
        "pid": identity["pid"],
        "process_group": identity["process_group"],
        "session": identity["session"],
        "start_ticks": identity["start_ticks"],
        "spec_path": os.fspath(spec_path),
        "spec_sha256": sha256_file(spec_path),
        "control_session_sha256": (
            sha256_bytes(control_session.encode("ascii"))
            if control_session is not None
            else None
        ),
    }
    atomic_publish_noreplace(destination or paths.child_sidecar, canonical_json(child))
    return child


def publish_child_completion(
    *,
    paths: Paths,
    prepared_sha: str,
    armed_sha: str,
    decision_state_sha: str,
    child: Mapping[str, Any],
    control_session: str,
    outcome: str,
    source_status: int,
    rollback_status: int | None,
    counter: Mapping[str, Any],
    vdb: Mapping[str, Any],
    logs: Mapping[str, Any],
    checks: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    if outcome not in {"success", "rolled-back"}:
        fail(f"invalid child-completion outcome: {outcome}")
    if type(source_status) is not int or not 0 <= source_status <= 255:
        fail("child-completion source status is invalid")
    if rollback_status is not None and (
        type(rollback_status) is not int or not 0 <= rollback_status <= 255
    ):
        fail("child-completion rollback status is invalid")
    if not CONTROL_SESSION_PATTERN.fullmatch(control_session):
        fail("child-completion control session is invalid")
    record = {
        "schema": "gentoo-optimization-jsonschema-child-completion-v1",
        "transaction_id": paths.transaction_id,
        "recorded_at": utc_now(),
        "boot_id": boot_id(paths.proc_root),
        "prepared_state_sha256": require_sha256(prepared_sha, "prepared state digest"),
        "armed_state_sha256": require_sha256(armed_sha, "armed state digest"),
        "decision_state_sha256": require_sha256(
            decision_state_sha, "decision state digest"
        ),
        "child": dict(child),
        "control_session_sha256": sha256_bytes(control_session.encode("ascii")),
        "outcome": outcome,
        "source_status": source_status,
        "rollback_status": rollback_status,
        "counter": dict(counter),
        "vdb_sha256": sha256_bytes(canonical_json(vdb)),
        "logs": dict(logs),
        "checks": dict(checks),
    }
    digest = atomic_publish_noreplace(paths.child_completion, canonical_json(record))
    return record, digest


def validate_child_completion(
    paths: Paths,
    value: object,
    *,
    prepared_sha: str,
    armed_sha: str,
) -> dict[str, Any]:
    required = {
        "schema",
        "transaction_id",
        "recorded_at",
        "boot_id",
        "prepared_state_sha256",
        "armed_state_sha256",
        "decision_state_sha256",
        "child",
        "control_session_sha256",
        "outcome",
        "source_status",
        "rollback_status",
        "counter",
        "vdb_sha256",
        "logs",
        "checks",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema") != "gentoo-optimization-jsonschema-child-completion-v1"
        or value.get("transaction_id") != paths.transaction_id
        or value.get("prepared_state_sha256") != prepared_sha
        or value.get("armed_state_sha256") != armed_sha
        or value.get("outcome") not in {"success", "rolled-back"}
        or not isinstance(value.get("child"), dict)
        or not isinstance(value.get("counter"), dict)
        or not isinstance(value.get("logs"), dict)
        or not isinstance(value.get("checks"), dict)
    ):
        fail("child-completion authority has an invalid schema or binding")
    require_sha256(value.get("control_session_sha256"), "control session digest")
    require_sha256(value.get("decision_state_sha256"), "decision state digest")
    require_sha256(value.get("vdb_sha256"), "completion VDB digest")
    source_status = value.get("source_status")
    rollback_status = value.get("rollback_status")
    if type(source_status) is not int or not 0 <= source_status <= 255:
        fail("child-completion source status is invalid")
    if rollback_status is not None and (
        type(rollback_status) is not int or not 0 <= rollback_status <= 255
    ):
        fail("child-completion rollback status is invalid")
    return value


def verify_child_completion_evidence(value: Mapping[str, Any]) -> None:
    logs = value["logs"]
    for path_key, digest_key in (
        ("stdout_path", "stdout_sha256"),
        ("stderr_path", "stderr_sha256"),
    ):
        verify_evidence_reference(logs, path_key, digest_key)
    checks = value["checks"]
    if value["outcome"] == "success":
        qcheck = checks.get("qcheck")
        if not isinstance(qcheck, list):
            fail("successful child completion lacks qcheck evidence")
        for row in qcheck:
            verify_stage_evidence(row)
        verify_evidence_reference(
            checks,
            "private_pkgdir_report",
            "private_pkgdir_report_sha256",
        )


def await_exact_portage_prompt(
    *,
    process: subprocess.Popen[bytes],
    stdout_file: Any,
    stderr_file: Any,
    prepared: Mapping[str, Any],
    timeout: float,
) -> tuple[dict[str, Any], bytes]:
    prompt = b"Would you like to merge these packages? [Yes/No] "
    deadline = time.monotonic() + timeout
    displayed = b""
    while time.monotonic() < deadline:
        stdout_file.flush()
        displayed = os.pread(stdout_file.fileno(), os.fstat(stdout_file.fileno()).st_size, 0)
        if displayed.count(prompt) == 1 and displayed.endswith(prompt):
            plan = parse_pretend_output(
                displayed.decode("utf-8", errors="strict"),
                set(prepared["resolver"]["vdb_before"]["cpvs"]),
            )
            compare_plans(prepared["plan"], plan)
            return plan, displayed
        if process.poll() is not None:
            stderr_file.flush()
            fail(
                "Portage action exited before its exact authorization prompt: "
                + os.pread(
                    stderr_file.fileno(), os.fstat(stderr_file.fileno()).st_size, 0
                ).decode("utf-8", errors="replace")
            )
        time.sleep(0.05)
    fail("timed out waiting for the exact Portage authorization prompt")


def await_json_log_marker(
    *,
    process: subprocess.Popen[bytes],
    stdout_file: Any,
    stderr_file: Any,
    prefix: bytes,
    timeout: float,
) -> tuple[dict[str, Any], bytes]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stdout_file.flush()
        payload = os.pread(stdout_file.fileno(), os.fstat(stdout_file.fileno()).st_size, 0)
        for line in payload.splitlines():
            if line.startswith(prefix):
                try:
                    value = json.loads(line[len(prefix) :])
                except json.JSONDecodeError as error:
                    fail(f"Portage terminal marker is invalid JSON: {error}")
                if not isinstance(value, dict):
                    fail("Portage terminal marker is not an object")
                return value, payload
        if process.poll() is not None:
            stderr_file.flush()
            error_payload = os.pread(
                stderr_file.fileno(), os.fstat(stderr_file.fileno()).st_size, 0
            )
            fail(
                "Portage action exited before its terminal lock marker: "
                + error_payload.decode("utf-8", errors="replace")
            )
        time.sleep(0.05)
    fail(f"timed out waiting for Portage marker {prefix.decode('ascii', errors='replace')}")


def finalize_active_logs(
    *,
    paths: Paths,
    stage: str,
    stdout_partial: Path,
    stderr_partial: Path,
    stdout_file: Any,
    stderr_file: Any,
    expected_sizes: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], bytes, bytes]:
    stdout_file.flush()
    stderr_file.flush()
    os.fsync(stdout_file.fileno())
    os.fsync(stderr_file.fileno())
    stdout = os.pread(stdout_file.fileno(), os.fstat(stdout_file.fileno()).st_size, 0)
    stderr = os.pread(stderr_file.fileno(), os.fstat(stderr_file.fileno()).st_size, 0)
    if expected_sizes is not None and (
        expected_sizes.get("stdout_size") != len(stdout)
        or expected_sizes.get("stderr_size") != len(stderr)
    ):
        fail("Portage logs changed after the no-more-output control handoff")
    stdout_final = paths.report / f"{stage}.stdout"
    stderr_final = paths.report / f"{stage}.stderr"
    os.replace(stdout_partial, stdout_final)
    os.replace(stderr_partial, stderr_final)
    fsync_directory(paths.report)
    evidence = {
        "stage": stage,
        "stdout_path": os.fspath(stdout_final),
        "stdout_sha256": sha256_bytes(stdout),
        "stdout_size": len(stdout),
        "stderr_path": os.fspath(stderr_final),
        "stderr_sha256": sha256_bytes(stderr),
        "stderr_size": len(stderr),
    }
    return evidence, stdout, stderr


def seal_standard_output() -> dict[str, int]:
    """Durably close the evidence-log boundary before control-only completion."""

    for stream in (sys.stdout, sys.stderr):
        stream.flush()
    os.fsync(1)
    os.fsync(2)
    sizes = {
        "stdout_size": os.fstat(1).st_size,
        "stderr_size": os.fstat(2).st_size,
    }
    devnull = os.open("/dev/null", os.O_WRONLY | os.O_CLOEXEC)
    try:
        os.dup2(devnull, 1, inheritable=False)
        os.dup2(devnull, 2, inheritable=False)
    finally:
        os.close(devnull)
    return sizes


def run_armed_source_child(
    *,
    paths: Paths,
    prepared: dict[str, Any],
    prepared_sha: str,
    environment: Mapping[str, str],
    tools: Mapping[str, Path],
    runner: Runner,
    timeout: float,
) -> tuple[CommandResult, dict[str, Any], dict[str, Any], str]:
    """Authorize the displayed in-memory graph, then grant exact execution."""

    stage = "source-emerge"
    parent_control, child_control = control_channel_pair()
    command = source_emerge_command(
        tools,
        prepared["plan"],
        state_path(paths, "prepared"),
        prepared_sha,
        child_control.fileno,
        child_control.session,
    )
    spec = execution_spec(
        bindings=authority_mount_bindings(prepared["authority"], prepared["private_roots"]),
        command=command,
        environment=environment,
        network_isolated=True,
    )
    spec_path = paths.report / f"{stage}.execution.json"
    atomic_publish_noreplace(spec_path, canonical_json(spec))
    stdout_partial = paths.report / f".{stage}.stdout.partial"
    stderr_partial = paths.report / f".{stage}.stderr.partial"
    for partial in (stdout_partial, stderr_partial):
        if path_exists(partial):
            fail(f"source child log partial already exists: {partial}")
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    pty_master, pty_slave = pty.openpty()
    terminal = termios.tcgetattr(pty_slave)
    terminal[3] &= ~termios.ECHO
    termios.tcsetattr(pty_slave, termios.TCSANOW, terminal)
    parent = process_identity(os.getpid(), paths.proc_root)
    if parent is None:
        fail("cannot observe transaction coordinator identity")
    barrier = [
        os.fspath(tools["python"]),
        "-I",
        "-B",
        os.fspath(tools["transaction"]),
        "__barrier",
        str(read_fd),
        str(parent["pid"]),
        str(parent["start_ticks"]),
        "--",
        *contained_argv(spec_path, tools, network_isolated=True),
    ]
    with stdout_partial.open("x+b") as stdout_file, stderr_partial.open("x+b") as stderr_file:
        process = subprocess.Popen(
            barrier,
            stdin=pty_slave,
            stdout=stdout_file,
            stderr=stderr_file,
            env=clean_environment(),
            start_new_session=True,
            pass_fds=(read_fd, child_control.fileno),
        )
        child_control.close()
        os.close(pty_slave)
        os.close(read_fd)
        identity = process_identity(process.pid, paths.proc_root)
        if identity is None or identity["process_group"] != process.pid or identity["session"] != process.pid:
            os.close(write_fd)
            terminate_direct_process(process, 5.0)
            fail("source child did not enter its private process group and session")
        child = publish_child_sidecar(
            paths, identity, spec_path, parent_control.session
        )
        try:
            if os.write(write_fd, b"G") != 1:
                fail("short source-child barrier grant")
        finally:
            os.close(write_fd)
        try:
            lock_held = parent_control.receive("LOCK_HELD", min(timeout, 30 * 60))
            expected_emerge_arguments = [
                *emerge_options(),
                "--ask=y",
                *exact_plan_atoms(prepared["plan"]),
            ]
            if lock_held != {
                "prepared_state_sha256": prepared_sha,
                "emerge_arguments_sha256": sha256_bytes(
                    canonical_json(expected_emerge_arguments)
                ),
                "vdb_sha256": sha256_bytes(
                    canonical_json(prepared["resolver"]["vdb_before"])
                ),
                "vardb_root": os.fspath(paths.vdb),
            }:
                fail("held-lock child authority differs from the prepared transaction")
            displayed_plan, displayed = await_exact_portage_prompt(
                process=process,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                prepared=prepared,
                timeout=min(timeout, 30 * 60),
            )
            if sha256_file(state_path(paths, "prepared")) != prepared_sha:
                fail("prepared state changed while Portage awaited authorization")
            verify_frozen_authorities(paths, prepared)
            verify_private_portage_outputs(
                prepared["private_roots"],
                prepared["resolver"]["private_portage_outputs_before"],
            )
            compare_vdb(prepared["resolver"]["vdb_before"], vdb_manifest(paths.vdb))
            if file_observation(paths.cache_edb / "counter") != prepared["resolver"].get(
                "live_counter_before"
            ):
                fail("live EDB counter changed before transaction arming")
            verify_selected_sets(paths, prepared["resolver"]["selected_sets_before"])
            armed = next_state(
                prepared,
                prepared_sha,
                "armed",
                child=child,
                outcome={
                    "displayed_plan": displayed_plan,
                    "displayed_prefix_sha256": sha256_bytes(displayed),
                },
            )
            _armed_path, armed_sha = publish_state(paths, armed)
            if os.write(pty_master, b"Yes\n") != 4:
                fail("short exact Portage authorization grant")
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(pty_master)
            terminate_direct_process(process, 5.0)
            raise
        try:
            action_complete = parent_control.receive("ACTION_COMPLETE", timeout)
            status = action_complete.get("status")
            if type(status) is not int or not 0 <= status <= 255:
                fail("Portage action control status is invalid")
            delta = classify_vdb_delta(
                prepared["resolver"]["vdb_before"],
                vdb_manifest(paths.vdb),
                prepared["plan"],
            )
            checks: dict[str, Any] | None = None
            postcheck_error: str | None = None
            if status == 0 and delta["exact_success_delta"]:
                try:
                    verify_frozen_authorities(paths, prepared)
                    verify_private_portage_outputs(
                        prepared["private_roots"],
                        prepared["resolver"]["private_portage_outputs_before"],
                    )
                    verify_selected_sets(
                        paths, prepared["resolver"]["selected_sets_before"]
                    )
                    checks = verify_success_artifacts(
                        paths=paths,
                        prepared=prepared,
                        runner=runner,
                        tools=tools,
                        stage_prefix="source-success",
                    )
                except TransactionError as error:
                    postcheck_error = str(error)
            else:
                postcheck_error = (
                    f"source status={status}, exact_delta={delta['exact_success_delta']}"
                )
            if postcheck_error is None and checks is not None:
                decision_state = armed
                decision_state_sha = armed_sha
                expected_outcome = "success"
                rollback_atoms: list[str] = []
            else:
                if not delta["rollback_eligible"]:
                    fail("failed source action produced a non-rollback-eligible VDB delta")
                rollback_atoms = rollback_order(prepared["plan"], delta["added"])
                rollback = next_state(
                    armed,
                    armed_sha,
                    "rollback-in-progress",
                    child=armed.get("child"),
                    outcome={
                        "source_status": status,
                        "postcheck_error": postcheck_error,
                        "delta": delta,
                    },
                )
                _rollback_path, rollback_sha = publish_state(paths, rollback)
                decision_state = rollback
                decision_state_sha = rollback_sha
                expected_outcome = "rolled-back"
            parent_control.send(
                "DECISION",
                {
                    "outcome": expected_outcome,
                    "state_sha256": decision_state_sha,
                    "rollback_atoms": rollback_atoms,
                },
            )
            terminal_ready = parent_control.receive("TERMINAL_READY", 2 * 3600)
            required_terminal_keys = {
                "outcome",
                "source_status",
                "rollback_status",
                "decision_state_sha256",
                "counter",
                "vdb_sha256",
                "stdout_size",
                "stderr_size",
            }
            if (
                set(terminal_ready) != required_terminal_keys
                or terminal_ready.get("outcome") != expected_outcome
                or terminal_ready.get("source_status") != status
                or terminal_ready.get("decision_state_sha256") != decision_state_sha
                or not isinstance(terminal_ready.get("counter"), dict)
                or type(terminal_ready.get("stdout_size")) is not int
                or type(terminal_ready.get("stderr_size")) is not int
            ):
                fail("held-lock terminal control authority is invalid")
            if expected_outcome == "success":
                if terminal_ready.get("rollback_status") is not None:
                    fail("successful child unexpectedly reported rollback status")
            elif terminal_ready.get("rollback_status") != 0:
                fail("held-lock rollback did not report exact success")
            current_vdb = vdb_manifest(paths.vdb)
            if terminal_ready.get("vdb_sha256") != sha256_bytes(
                canonical_json(current_vdb)
            ):
                fail("terminal child VDB identity differs from the live held-lock view")
            log_evidence, stdout, stderr = finalize_active_logs(
                paths=paths,
                stage=stage,
                stdout_partial=stdout_partial,
                stderr_partial=stderr_partial,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                expected_sizes=terminal_ready,
            )
            evidence = {
                **log_evidence,
                "status": status,
                "spec_path": os.fspath(spec_path),
                "spec_sha256": sha256_file(spec_path),
                "counter_reconciliation": terminal_ready["counter"],
                "postcheck_error": postcheck_error,
            }
            if expected_outcome == "success":
                verify_frozen_authorities(paths, prepared)
                verify_private_portage_outputs(
                    prepared["private_roots"],
                    prepared["resolver"]["private_portage_outputs_before"],
                )
                verify_selected_sets(paths, prepared["resolver"]["selected_sets_before"])
                final_delta = classify_vdb_delta(
                    prepared["resolver"]["vdb_before"], current_vdb, prepared["plan"]
                )
                if not final_delta["exact_success_delta"]:
                    fail("success delta changed before terminal publication")
            else:
                compare_vdb(prepared["resolver"]["vdb_before"], current_vdb)
                verify_private_portage_outputs(
                    prepared["private_roots"],
                    prepared["resolver"]["private_portage_outputs_before"],
                )
                verify_selected_sets(paths, prepared["resolver"]["selected_sets_before"])
                final_delta = classify_vdb_delta(
                    prepared["resolver"]["vdb_before"], current_vdb, prepared["plan"]
                )
            completion, completion_sha = publish_child_completion(
                paths=paths,
                prepared_sha=prepared_sha,
                armed_sha=armed_sha,
                decision_state_sha=decision_state_sha,
                child=child,
                control_session=parent_control.session,
                outcome=expected_outcome,
                source_status=status,
                rollback_status=terminal_ready.get("rollback_status"),
                counter=terminal_ready["counter"],
                vdb=current_vdb,
                logs=log_evidence,
                checks=checks or {},
            )
            completion_ref = {
                "path": os.fspath(paths.child_completion),
                "sha256": completion_sha,
            }
            if expected_outcome == "success":
                terminal_state = next_state(
                    armed,
                    armed_sha,
                    "success",
                    child=armed.get("child"),
                    outcome={
                        "source": evidence,
                        "delta": final_delta,
                        "checks": checks,
                        "child_completion": completion_ref,
                    },
                )
            else:
                terminal_state = next_state(
                    decision_state,
                    decision_state_sha,
                    "rolled-back",
                    child=decision_state.get("child"),
                    outcome={
                        "source": evidence,
                        "delta": final_delta,
                        "child_completion": completion_ref,
                    },
                )
            terminal_path, terminal_sha = publish_state(paths, terminal_state)
            parent_control.send(
                "FINALIZE",
                {
                    "completion_path": os.fspath(paths.child_completion),
                    "completion_sha256": completion_sha,
                    "terminal_state_path": os.fspath(terminal_path),
                    "terminal_state_sha256": terminal_sha,
                },
            )
            acknowledgement = parent_control.receive("FINAL_ACK", 5 * 60)
            if acknowledgement != {
                "outcome": expected_outcome,
                "completion_sha256": completion_sha,
                "terminal_state_sha256": terminal_sha,
            }:
                fail("Portage child final acknowledgement differs from durable authority")
            with transaction_signal_scope():
                process.wait(timeout=5 * 60)
        except BaseException:
            terminate_direct_process(process, 5.0)
            raise
        finally:
            parent_control.close()
            with contextlib.suppress(OSError):
                os.close(pty_master)
        wait_group_empty(process.pid, process.pid, proc_root=paths.proc_root, timeout=5.0)
        actual_status = normalize_status(process.returncode)
        if actual_status != 0:
            fail(f"held-lock Portage protocol exited with status {actual_status}")
        if (
            Path(evidence["stdout_path"]).read_bytes() != stdout
            or Path(evidence["stderr_path"]).read_bytes() != stderr
        ):
            fail("Portage child wrote after its no-more-output handoff")
    return CommandResult(tuple(command), status, stdout, stderr), evidence, terminal_state, terminal_sha


def mount_exec_command(arguments: argparse.Namespace) -> int:
    spec = validate_execution_spec(read_json_regular(arguments.spec, "contained execution contract"))
    mount = "/usr/bin/mount"
    subprocess.run([mount, "--make-rprivate", "/"], check=True)
    for row in spec["mounts"]:
        source, target = Path(row["source"]), Path(row["target"])
        subprocess.run([mount, "--bind", "--", os.fspath(source), os.fspath(target)], check=True)
        if row["read_only"]:
            subprocess.run(
                [mount, "-o", "remount,bind,ro,nodev,nosuid", "--", os.fspath(target)],
                check=True,
            )
        source_stat, target_stat = source.stat(), target.stat()
        if (source_stat.st_dev, source_stat.st_ino) != (target_stat.st_dev, target_stat.st_ino):
            fail(f"bind mount identity differs: {source} -> {target}")
        if row["read_only"] and not os.statvfs(target).f_flag & os.ST_RDONLY:
            fail(f"read-only bind mount is writable: {target}")
    command = spec["command"]
    os.execve(command[0], command, spec["environment"])
    return 125


def pdeath_exec_command(arguments: argparse.Namespace) -> int:
    if not arguments.command or arguments.command[0] != "--" or len(arguments.command) < 2:
        fail("malformed parent-death execution command")
    install_parent_death_signal(arguments.parent_pid, arguments.parent_start_ticks)
    command = arguments.command[1:]
    if not Path(command[0]).is_absolute():
        fail("parent-death executable is not absolute")
    os.execve(command[0], command, os.environ)
    return 125


def rollback_emerge_arguments(atoms: Sequence[str]) -> list[str]:
    return [
        "--ignore-default-opts",
        "--unmerge",
        "--ask=n",
        "--deselect=n",
        "--package-moves=n",
        "--noconfmem",
        "--color=n",
        *atoms,
    ]


def portage_action_command(arguments: argparse.Namespace) -> int:
    """Hold one Portage config/vardb lock through exact action or rollback."""

    if not LIVE_MUTATION_ENABLED:
        fail(
            "internal Portage mutation is disabled: preparation concurrency and "
            "post-emerge authority closure are not yet proven"
        )
    if not arguments.command or arguments.command[0] != "--" or len(arguments.command) < 2:
        fail("malformed internal Portage action command")
    if arguments.control_fd < 0 or not CONTROL_SESSION_PATTERN.fullmatch(
        arguments.control_session
    ):
        fail("internal Portage control-channel identity is invalid")
    endpoint = socket.socket(fileno=arguments.control_fd)
    channel = ControlChannel(endpoint, arguments.control_session)
    command = arguments.command[1:]
    if command[0] != "/usr/bin/emerge":
        fail("internal Portage action has an unexpected command identity")
    emerge_arguments = command[1:]
    prepared = validate_state(
        read_json_regular(arguments.prepared_state, "prepared state for Portage action")
    )
    if (
        prepared["phase"] != "prepared"
        or sha256_file(arguments.prepared_state) != arguments.prepared_sha256
    ):
        fail("internal Portage action is not bound to the exact prepared state")
    expected_atoms = exact_plan_atoms(prepared["plan"])
    expected_arguments = [*emerge_options(), "--ask=y", *expected_atoms]
    if emerge_arguments != expected_arguments:
        fail("internal Portage argv differs byte-for-byte from the reviewed action")
    try:
        import _emerge.actions as actions
        from _emerge.main import parse_opts
    except ImportError as error:
        fail(f"internal Portage API is unavailable: {error}")
    action, options, atoms = parse_opts(emerge_arguments, silent=True)
    if (
        action is not None
        or atoms != expected_atoms
        or options.get("--ask") is not True
        or options.get("--package-moves") != "n"
        or "--pretend" in options
        or "--ignore-default-opts" not in options
    ):
        fail("internal Portage action differs from the reviewed exact action")
    config = actions.load_emerge_config(action=action, args=atoms, opts=options)
    config.action, config.opts, config.args = parse_opts(emerge_arguments)
    target = config.trees._target_eroot
    target_path = Path(target)
    vdb_path = target_path / "var/db/pkg"
    settings = config.target_config.settings
    features = set(str(settings.get("FEATURES", "")).split())
    if str(settings.get("AUTOCLEAN", "")).strip().lower() != "no":
        fail("frozen Portage AUTOCLEAN must be exactly disabled")
    forbidden_features = {"assume-digests", "parallel-install", "binpkg-signing"}
    if forbidden_features & features:
        fail(
            "frozen Portage FEATURES retains transaction-forbidden behavior: "
            + ", ".join(sorted(forbidden_features & features))
        )
    require_semantically_empty_preserved_registry(
        target_path / "var/lib/portage/preserved_libs_registry"
    )
    vardb = config.trees[target]["vartree"].dbapi
    if config.target_config.trees["vartree"].dbapi is not vardb:
        fail("Portage target configuration does not share the selected vardb object")
    original_loader = actions.load_emerge_config

    def reject_reload(*_args: object, **_kwargs: object) -> NoReturn:
        fail("Portage attempted to reload configuration after VDB lock acquisition")

    source_status = 125
    rollback_status: int | None = None
    vardb.lock()
    try:
        compare_vdb(prepared["resolver"]["vdb_before"], vdb_manifest(vdb_path))
        actions.load_emerge_config = reject_reload
        channel.send(
            "LOCK_HELD",
            {
                "prepared_state_sha256": arguments.prepared_sha256,
                "emerge_arguments_sha256": sha256_bytes(canonical_json(emerge_arguments)),
                "vdb_sha256": sha256_bytes(
                    canonical_json(prepared["resolver"]["vdb_before"])
                ),
                "vardb_root": os.fspath(vdb_path),
            },
        )
        source_status = int(actions.run_action(config))
        if config.trees[target]["vartree"].dbapi is not vardb:
            fail("Portage replaced the locked vardb object during the source action")
        channel.send("ACTION_COMPLETE", {"status": source_status})
        decision = channel.receive("DECISION", 30 * 60)
        requested = decision.get("outcome")
        decision_state_sha = require_sha256(
            decision.get("state_sha256"), "coordinator decision state digest"
        )
        if requested == "success":
            if source_status != 0:
                fail("coordinator attempted to commit a failed Portage action")
            delta = classify_vdb_delta(
                prepared["resolver"]["vdb_before"], vdb_manifest(vdb_path), prepared["plan"]
            )
            if not delta["exact_success_delta"]:
                fail("coordinator attempted to commit a non-exact VDB delta")
            terminal_outcome = "success"
        elif requested == "rolled-back":
            delta = classify_vdb_delta(
                prepared["resolver"]["vdb_before"], vdb_manifest(vdb_path), prepared["plan"]
            )
            if not delta["rollback_eligible"]:
                fail("source VDB delta is not eligible for exact rollback")
            rollback_atoms = rollback_order(prepared["plan"], delta["added"])
            if decision.get("rollback_atoms") != rollback_atoms:
                fail("coordinator rollback atoms differ from the exact reverse delta")
            rollback_arguments = rollback_emerge_arguments(rollback_atoms)
            rollback_action, rollback_options, parsed_atoms = parse_opts(
                rollback_arguments, silent=True
            )
            if (
                rollback_action != "unmerge"
                or parsed_atoms != rollback_atoms
                or rollback_options.get("--ask") != "n"
                or rollback_options.get("--deselect") != "n"
                or rollback_options.get("--package-moves") != "n"
                or "--ignore-default-opts" not in rollback_options
            ):
                fail("internal Portage rollback differs from the exact reverse action")
            config.action, config.opts, config.args = parse_opts(rollback_arguments)
            rollback_status = int(actions.run_action(config))
            if config.trees[target]["vartree"].dbapi is not vardb:
                fail("Portage replaced the locked vardb object during rollback")
            if rollback_status != 0:
                fail(f"exact held-lock Portage rollback failed with status {rollback_status}")
            compare_vdb(prepared["resolver"]["vdb_before"], vdb_manifest(vdb_path))
            terminal_outcome = "rolled-back"
        else:
            fail("coordinator supplied an invalid held-lock decision")
        tools = tools_from_manifest(prepared["authority"]["tools"])
        live_edb = Path(prepared["private_roots"]["live_cache_edb_view"])
        counter = reconcile_counter_with_reseal(
            live_edb=live_edb,
            private_edb=Path(prepared["private_roots"]["cache_edb"]),
            vdb=vdb_path,
            prepared=prepared,
            outcome=terminal_outcome,
            remount=lambda path, read_only: remount_bind_read_only(
                path, read_only, tools["mount"]
            ),
        )
        sealed = seal_standard_output()
        channel.send(
            "TERMINAL_READY",
            {
                "outcome": terminal_outcome,
                "source_status": source_status,
                "rollback_status": rollback_status,
                "decision_state_sha256": decision_state_sha,
                "counter": counter,
                "vdb_sha256": sha256_bytes(canonical_json(vdb_manifest(vdb_path))),
                **sealed,
            },
        )
        finalization = channel.receive("FINALIZE", 30 * 60)
        completion_path = Path(str(finalization.get("completion_path", "")))
        terminal_state_path = Path(str(finalization.get("terminal_state_path", "")))
        completion_sha = require_sha256(
            finalization.get("completion_sha256"), "child-completion digest"
        )
        terminal_state_sha = require_sha256(
            finalization.get("terminal_state_sha256"), "terminal state digest"
        )
        expected_completion_path = (
            Path(prepared["evidence"]["directory"]) / "child-completion.json"
        )
        expected_terminal_state_path = Path(
            f"/var/lib/gentoo-optimization/state/project/"
            f"jsonschema-prerequisite-{prepared['transaction_id']}.{terminal_outcome}.json"
        )
        if (
            set(finalization)
            != {
                "completion_path",
                "completion_sha256",
                "terminal_state_path",
                "terminal_state_sha256",
            }
            or completion_path != expected_completion_path
            or terminal_state_path != expected_terminal_state_path
            or not completion_path.is_file()
            or completion_path.is_symlink()
            or sha256_file(completion_path) != completion_sha
            or not terminal_state_path.is_file()
            or terminal_state_path.is_symlink()
            or sha256_file(terminal_state_path) != terminal_state_sha
        ):
            fail("coordinator finalization lacks exact durable terminal authorities")
        channel.send(
            "FINAL_ACK",
            {
                "outcome": terminal_outcome,
                "completion_sha256": completion_sha,
                "terminal_state_sha256": terminal_state_sha,
            },
        )
    finally:
        actions.load_emerge_config = original_loader
        vardb.unlock()
        channel.close()
        for root_trees in config.trees.values():
            if "porttree" not in root_trees.lazy_items:
                root_trees["porttree"].dbapi.close_caches()
    return 0


def portage_recovery_command(arguments: argparse.Namespace) -> int:
    """Recover through one newly loaded config and one continuously held VDB lock."""

    if not LIVE_MUTATION_ENABLED:
        fail(
            "internal Portage recovery is disabled: preparation concurrency and "
            "post-emerge authority closure are not yet proven"
        )
    if arguments.control_fd < 0 or not CONTROL_SESSION_PATTERN.fullmatch(
        arguments.control_session
    ):
        fail("internal recovery control-channel identity is invalid")
    endpoint = socket.socket(fileno=arguments.control_fd)
    channel = ControlChannel(endpoint, arguments.control_session)
    prepared = validate_state(
        read_json_regular(arguments.prepared_state, "prepared state for Portage recovery")
    )
    rollback_state = validate_state(
        read_json_regular(arguments.rollback_state, "rollback state for Portage recovery")
    )
    if (
        prepared["phase"] != "prepared"
        or rollback_state["phase"] != "rollback-in-progress"
        or sha256_file(arguments.prepared_state) != arguments.prepared_sha256
        or sha256_file(arguments.rollback_state) != arguments.rollback_sha256
        or rollback_state["prepared_state_sha256"] != arguments.prepared_sha256
    ):
        fail("internal recovery is not bound to exact prepared/rollback states")
    bootstrap_atoms = list(reversed(exact_plan_atoms(prepared["plan"])))
    bootstrap_arguments = rollback_emerge_arguments(bootstrap_atoms)
    try:
        import _emerge.actions as actions
        from _emerge.main import parse_opts
    except ImportError as error:
        fail(f"internal Portage recovery API is unavailable: {error}")
    action, options, atoms = parse_opts(bootstrap_arguments, silent=True)
    if action != "unmerge" or atoms != bootstrap_atoms:
        fail("internal recovery bootstrap action is not exact unmerge")
    config = actions.load_emerge_config(action=action, args=atoms, opts=options)
    config.action, config.opts, config.args = parse_opts(bootstrap_arguments)
    target = config.trees._target_eroot
    target_path = Path(target)
    vdb_path = target_path / "var/db/pkg"
    settings = config.target_config.settings
    features = set(str(settings.get("FEATURES", "")).split())
    if str(settings.get("AUTOCLEAN", "")).strip().lower() != "no":
        fail("frozen Portage AUTOCLEAN must be exactly disabled during recovery")
    if {"assume-digests", "parallel-install", "binpkg-signing"} & features:
        fail("frozen Portage recovery FEATURES retain forbidden behavior")
    vardb = config.trees[target]["vartree"].dbapi
    if config.target_config.trees["vartree"].dbapi is not vardb:
        fail("Portage recovery target does not share the selected vardb object")
    original_loader = actions.load_emerge_config

    def reject_reload(*_args: object, **_kwargs: object) -> NoReturn:
        fail("Portage attempted to reload configuration during held-lock recovery")

    vardb.lock()
    try:
        actions.load_emerge_config = reject_reload
        delta = classify_vdb_delta(
            prepared["resolver"]["vdb_before"], vdb_manifest(vdb_path), prepared["plan"]
        )
        if not delta["rollback_eligible"]:
            fail("recovery VDB delta is not eligible for exact rollback")
        exact_atoms = rollback_order(prepared["plan"], delta["added"])
        exact_arguments = rollback_emerge_arguments(exact_atoms)
        recovery_action, recovery_options, parsed_atoms = parse_opts(
            exact_arguments, silent=True
        )
        if (
            recovery_action != "unmerge"
            or parsed_atoms != exact_atoms
            or recovery_options.get("--ask") != "n"
            or recovery_options.get("--deselect") != "n"
            or recovery_options.get("--package-moves") != "n"
            or "--ignore-default-opts" not in recovery_options
        ):
            fail("held-lock recovery argv differs from exact reverse unmerge")
        config.action, config.opts, config.args = parse_opts(exact_arguments)
        channel.send(
            "LOCK_HELD",
            {
                "prepared_state_sha256": arguments.prepared_sha256,
                "rollback_state_sha256": arguments.rollback_sha256,
                "rollback_atoms": exact_atoms,
                "vdb_sha256": sha256_bytes(canonical_json(vdb_manifest(vdb_path))),
                "vardb_root": os.fspath(vdb_path),
            },
        )
        decision = channel.receive("DECISION", 30 * 60)
        if decision != {
            "outcome": "rolled-back",
            "state_sha256": arguments.rollback_sha256,
            "rollback_atoms": exact_atoms,
        }:
            fail("coordinator recovery decision differs from held-lock authority")
        rollback_status = 0
        if exact_atoms:
            rollback_status = int(actions.run_action(config))
        if config.trees[target]["vartree"].dbapi is not vardb:
            fail("Portage replaced the locked vardb object during recovery")
        if rollback_status != 0:
            fail(f"held-lock recovery unmerge failed with status {rollback_status}")
        compare_vdb(prepared["resolver"]["vdb_before"], vdb_manifest(vdb_path))
        tools = tools_from_manifest(prepared["authority"]["tools"])
        counter = reconcile_counter_with_reseal(
            live_edb=Path(prepared["private_roots"]["live_cache_edb_view"]),
            private_edb=Path(prepared["private_roots"]["cache_edb"]),
            vdb=vdb_path,
            prepared=prepared,
            outcome="rolled-back",
            remount=lambda path, read_only: remount_bind_read_only(
                path, read_only, tools["mount"]
            ),
        )
        sealed = seal_standard_output()
        channel.send(
            "TERMINAL_READY",
            {
                "outcome": "rolled-back",
                "rollback_status": rollback_status,
                "decision_state_sha256": arguments.rollback_sha256,
                "counter": counter,
                "vdb_sha256": sha256_bytes(canonical_json(vdb_manifest(vdb_path))),
                **sealed,
            },
        )
        finalization = channel.receive("FINALIZE", 30 * 60)
        expected_completion_path = (
            Path(prepared["evidence"]["directory"]) / "child-completion.json"
        )
        expected_terminal_path = Path(
            f"/var/lib/gentoo-optimization/state/project/"
            f"jsonschema-prerequisite-{prepared['transaction_id']}.rolled-back.json"
        )
        completion_sha = require_sha256(
            finalization.get("completion_sha256"), "recovery completion digest"
        )
        terminal_sha = require_sha256(
            finalization.get("terminal_state_sha256"), "recovery terminal digest"
        )
        if (
            set(finalization)
            != {
                "completion_path",
                "completion_sha256",
                "terminal_state_path",
                "terminal_state_sha256",
            }
            or Path(str(finalization.get("completion_path", "")))
            != expected_completion_path
            or Path(str(finalization.get("terminal_state_path", "")))
            != expected_terminal_path
            or sha256_file(expected_completion_path) != completion_sha
            or sha256_file(expected_terminal_path) != terminal_sha
        ):
            fail("recovery finalization lacks exact durable terminal authorities")
        channel.send(
            "FINAL_ACK",
            {
                "outcome": "rolled-back",
                "completion_sha256": completion_sha,
                "terminal_state_sha256": terminal_sha,
            },
        )
    finally:
        actions.load_emerge_config = original_loader
        vardb.unlock()
        channel.close()
        for root_trees in config.trees.values():
            if "porttree" not in root_trees.lazy_items:
                root_trees["porttree"].dbapi.close_caches()
    return 0


def assert_no_armed_retry(paths: Paths) -> None:
    if not path_exists(paths.canonical_state):
        return
    state, _digest = load_current_state(paths)
    if state["phase"] != "prepared":
        fail(f"source emerge may not be started from transaction phase {state['phase']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build and verify the Phase-2 jsonschema prerequisite transaction contract; "
            "live preparation and mutation currently fail closed"
        )
    )
    parser.add_argument("--fixture-root", type=Path, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare = subparsers.add_parser(
        "prepare", help="disabled pending the live VDB snapshot exclusion boundary"
    )
    prepare.add_argument("transaction_id")
    prepare.add_argument("--target", default="dev-python/jsonschema")
    prepare.add_argument("--pre-checkpoint-state", required=True, type=Path)
    run = subparsers.add_parser(
        "run", help="disabled pending the held-lock rollback and counter protocol"
    )
    run.add_argument("transaction_id")
    recover = subparsers.add_parser("recover", help="reconcile an armed or rollback transaction; never rerun emerge")
    recover.add_argument("transaction_id")
    verify = subparsers.add_parser("verify", help="verify immutable state and its bound authorities")
    verify.add_argument("transaction_id")
    internal = subparsers.add_parser("__barrier", help=argparse.SUPPRESS)
    internal.add_argument("barrier_fd", type=int)
    internal.add_argument("parent_pid", type=int)
    internal.add_argument("parent_start_ticks", type=int)
    internal.add_argument("command", nargs=argparse.REMAINDER)
    mount_exec = subparsers.add_parser("__mount-exec", help=argparse.SUPPRESS)
    mount_exec.add_argument("spec", type=Path)
    pdeath = subparsers.add_parser("__pdeath-exec", help=argparse.SUPPRESS)
    pdeath.add_argument("parent_pid", type=int)
    pdeath.add_argument("parent_start_ticks", type=int)
    pdeath.add_argument("command", nargs=argparse.REMAINDER)
    portage_action = subparsers.add_parser("__portage-action", help=argparse.SUPPRESS)
    portage_action.add_argument("prepared_state", type=Path)
    portage_action.add_argument("prepared_sha256")
    portage_action.add_argument("control_fd", type=int)
    portage_action.add_argument("control_session")
    portage_action.add_argument("command", nargs=argparse.REMAINDER)
    portage_recovery = subparsers.add_parser("__portage-recovery", help=argparse.SUPPRESS)
    portage_recovery.add_argument("prepared_state", type=Path)
    portage_recovery.add_argument("prepared_sha256")
    portage_recovery.add_argument("rollback_state", type=Path)
    portage_recovery.add_argument("rollback_sha256")
    portage_recovery.add_argument("control_fd", type=int)
    portage_recovery.add_argument("control_session")
    subparsers.add_parser("__observe-repositories", help=argparse.SUPPRESS)
    return parser


def fixture_paths(arguments: argparse.Namespace) -> Paths:
    fixture = arguments.fixture_root is not None
    if fixture and os.environ.get("GENTOO_OPT_JSONSCHEMA_FIXTURE") != "1":
        fail("fixture-root requires GENTOO_OPT_JSONSCHEMA_FIXTURE=1")
    if not fixture and os.geteuid() != 0:
        fail("production prerequisite transaction requires root")
    root = arguments.fixture_root.resolve(strict=True) if fixture else Path("/")
    return Paths(require_safe_id(arguments.transaction_id, "transaction ID"), root, fixture)


def verify_evidence_reference(row: object, path_key: str, digest_key: str) -> None:
    if not isinstance(row, dict) or not isinstance(row.get(path_key), str):
        fail(f"evidence reference lacks {path_key}")
    path = Path(row[path_key])
    expected = require_sha256(row.get(digest_key), f"{path_key} digest")
    if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
        fail(f"evidence reference changed: {path}")


def verify_stage_evidence(row: object) -> None:
    for path_key, digest_key in (
        ("spec_path", "spec_sha256"),
        ("stdout_path", "stdout_sha256"),
        ("stderr_path", "stderr_sha256"),
    ):
        verify_evidence_reference(row, path_key, digest_key)


def verify_frozen_authorities(paths: Paths, prepared: Mapping[str, Any]) -> None:
    revalidate_tool_manifest(prepared["authority"]["tools"])
    revalidate_pre_checkpoint_authority(
        prepared["authority"].get("pre_dependency_checkpoint")
    )
    modules = prepared["authority"].get("python_modules")
    if not isinstance(modules, list) or not modules:
        fail("prepared authority lacks Python module identities")
    for module in modules:
        revalidate_python_module_authority(module)
    for repository in prepared["authority"]["repositories"]:
        verify_manifest(Path(repository["materialized_location"]), Path(repository["tree_manifest_path"]))
    for key in ("portage_config", "portage_global_config"):
        row = prepared["authority"].get(key)
        if not isinstance(row, dict):
            fail(f"prepared authority lacks {key}")
        verify_manifest(Path(row["materialized_location"]), Path(row["tree_manifest_path"]))
    prefetch = prepared["resolver"].get("prefetch")
    if not isinstance(prefetch, dict):
        fail("prepared resolver lacks prefetch authority")
    verify_manifest(Path(prefetch["authority"]), Path(prefetch["tree_manifest_path"]))
    verify_framework_authority(paths, prepared["authority"].get("framework"))


def verify_command(paths: Paths) -> int:
    paths.validate()
    if reconcile_state_chain(paths, repair_canonical=False) is None:
        fail("transaction has no durable state")
    state, _digest = load_current_state(paths)
    prepared, prepared_sha = load_phase_state(paths, "prepared")
    if state["phase"] != "prepared" and state["prepared_state_sha256"] != prepared_sha:
        fail("current state is not bound to the immutable prepared state")
    verify_frozen_authorities(paths, prepared)
    prefetch = prepared["resolver"]["prefetch"]
    for key in (
        "initial_pretend",
        "frozen_repository_observation",
        "exact_repretend_before_prefetch",
        "prefetch",
        "offline_exact_repretend",
    ):
        verify_stage_evidence(prepared["resolver"].get(key))
    phase = state["phase"]
    if phase == "prepared":
        verify_private_roots_baseline(prepared["resolver"].get("private_roots_before"))
        verify_private_portage_outputs(
            prepared["private_roots"], prepared["resolver"].get("private_portage_outputs_before")
        )
        compare_vdb(prepared["resolver"]["vdb_before"], vdb_manifest(paths.vdb))
        if file_observation(paths.cache_edb / "counter") != prepared["resolver"].get(
            "live_counter_before"
        ):
            fail("live EDB counter changed after transaction preparation")
        verify_selected_sets(paths, prepared["resolver"]["selected_sets_before"])
    elif phase == "rolled-back":
        verify_private_portage_outputs(
            prepared["private_roots"], prepared["resolver"].get("private_portage_outputs_before")
        )
        compare_vdb(prepared["resolver"]["vdb_before"], vdb_manifest(paths.vdb))
        verify_selected_sets(paths, prepared["resolver"]["selected_sets_before"])
    elif phase == "success":
        verify_private_portage_outputs(
            prepared["private_roots"], prepared["resolver"].get("private_portage_outputs_before")
        )
        delta = classify_vdb_delta(
            prepared["resolver"]["vdb_before"], vdb_manifest(paths.vdb), prepared["plan"]
        )
        if not delta["exact_success_delta"]:
            fail("successful state no longer has its exact installed VDB delta")
    return 0


def verify_entrypoint(paths: Paths) -> int:
    paths.validate()
    with transaction_locks(paths):
        return verify_command(paths)


def freeze_tree_authority(
    *,
    label: str,
    source: Path,
    destination: Path,
    mount_target: Path,
    runner: Runner,
    tools: Mapping[str, Path],
    uid: int,
    gid: int,
) -> dict[str, Any]:
    source = source.resolve(strict=True)
    mount_target = mount_target.resolve(strict=True)
    if not source.is_dir() or source.is_symlink() or not mount_target.is_dir() or mount_target.is_symlink():
        fail(f"{label} authority endpoints are not real directories")
    copy_tree(source, destination, runner, tools)
    normalize_tree_ownership(destination, uid, gid)
    manifest = tree_manifest(destination)
    manifest_path = destination.parent / f"{destination.name}.manifest.json"
    manifest_sha = write_manifest(manifest_path, manifest)
    return {
        "source_location": os.fspath(source),
        "mount_target": os.fspath(mount_target),
        "materialized_location": os.fspath(destination),
        "tree_manifest_path": os.fspath(manifest_path),
        "tree_manifest_sha256": manifest_sha,
    }


def require_success(result: CommandResult, stage: str) -> None:
    if result.status != 0:
        fail(
            f"{stage} failed with status {result.status}: "
            f"{result.stderr.decode('utf-8', errors='replace')}"
        )


def run_pretend_stage(
    *,
    stage: str,
    target_atoms: Sequence[str],
    paths: Paths,
    authority: Mapping[str, Any],
    private_roots: Mapping[str, str],
    runner: Runner,
    tools: Mapping[str, Path],
    installed: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    command = [os.fspath(tools["emerge"]), *emerge_options(), "--pretend", *target_atoms]
    result, evidence = run_contained_stage(
        stage=stage,
        paths=paths,
        authority=authority,
        private_roots=private_roots,
        command=command,
        environment=plan_environment(private_roots, offline=True),
        runner=runner,
        tools=tools,
        timeout=30 * 60,
        network_isolated=True,
    )
    require_success(result, stage)
    return parse_pretend_output(result.stdout.decode("utf-8", errors="strict"), installed), evidence


def prefetch_distfiles(
    *,
    plan: Mapping[str, Any],
    paths: Paths,
    authority: Mapping[str, Any],
    private_roots: dict[str, str],
    runner: Runner,
    tools: Mapping[str, Path],
    uid: int,
    gid: int,
) -> dict[str, Any]:
    command = [
        os.fspath(tools["emerge"]),
        *emerge_options(),
        "--fetchonly",
        "--ask=n",
        *exact_plan_atoms(plan),
    ]
    result, evidence = run_contained_stage(
        stage="prefetch",
        paths=paths,
        authority=authority,
        private_roots=private_roots,
        command=command,
        environment=plan_environment(private_roots, offline=False),
        runner=runner,
        tools=tools,
        timeout=8 * 3600,
        network_isolated=False,
    )
    require_success(result, "prefetch")
    distfile_authority = paths.authority / "distfiles"
    copy_tree(Path(private_roots["distdir_staging"]), distfile_authority, runner, tools)
    normalize_tree_ownership(distfile_authority, uid, gid)
    manifest = tree_manifest(distfile_authority)
    manifest_path = paths.authority / "distfiles.manifest.json"
    manifest_sha = write_manifest(manifest_path, manifest)
    private_roots["distdir_authority"] = os.fspath(distfile_authority)
    return {
        **evidence,
        "authority": os.fspath(distfile_authority),
        "tree_manifest_path": os.fspath(manifest_path),
        "tree_manifest_sha256": manifest_sha,
    }


def verify_frozen_repository_vector(
    *,
    expected: Sequence[RepositorySpec],
    paths: Paths,
    authority: Mapping[str, Any],
    private_roots: Mapping[str, str],
    runner: Runner,
    tools: Mapping[str, Path],
) -> dict[str, Any]:
    result, evidence = run_contained_stage(
        stage="frozen-repository-observation",
        paths=paths,
        authority=authority,
        private_roots=private_roots,
        command=[
            os.fspath(tools["python"]),
            "-I",
            "-B",
            os.fspath(tools["transaction"]),
            "__observe-repositories",
        ],
        environment=plan_environment(private_roots, offline=True),
        runner=runner,
        tools=tools,
        timeout=5 * 60,
        network_isolated=True,
    )
    require_success(result, "frozen repository observation")
    try:
        observed = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        fail(f"frozen repository observation is invalid JSON: {error}")
    if observed != repository_vector(expected):
        fail("frozen Portage repository/masters vector differs from discovery")
    return {**evidence, "repositories": observed}


def prepare_command(arguments: argparse.Namespace, paths: Paths) -> int:
    """Freeze every input, derive an exact plan, prefetch, and publish prepared."""

    paths.validate()
    if paths.fixture_mode:
        fail(
            "fixture CLI preparation is disabled; semantic fixtures must inject "
            "repository discovery and command results explicitly"
        )
    if not LIVE_PREPARATION_ENABLED:
        fail(
            "live jsonschema preparation is disabled: Portage VDB-lock and "
            "process/handle exclusion around authority snapshots is not yet implemented"
        )
    prepare_directories(paths, paths.fixture_mode)
    with transaction_locks(paths):
        reconciled = reconcile_state_chain(paths)
        if reconciled is not None:
            fail(f"transaction already has durable phase {reconciled[0]['phase']}")
        for candidate in (paths.report, paths.authority, paths.cache, paths.canonical_state):
            if path_exists(candidate):
                fail(f"transaction object already exists: {candidate}")
        checkpoint = validate_pre_checkpoint(arguments.pre_checkpoint_state, enforce_root_trust=True)
        paths.report.mkdir(mode=0o700)
        paths.authority.mkdir(mode=0o700)
        repositories_root = paths.authority / "repositories"
        repositories_root.mkdir(mode=0o755)
        tools = default_tools(paths.root if paths.fixture_mode else Path("/"))
        tools["transaction"] = Path(__file__).resolve(strict=True)
        tools_value = tool_manifest(tools)
        python_modules = [
            python_module_authority("_emerge"),
            python_module_authority("gemato"),
            python_module_authority("portage"),
        ]
        runner: Runner = SubprocessRunner()
        repositories: list[dict[str, Any]] = []
        repository_specs = discover_repositories()
        for repository in repository_specs:
            destination = repositories_root / repository.name
            repositories.append(
                materialize_repository(repository, destination, runner=runner, tools=tools)
            )
        for module in python_modules:
            revalidate_python_module_authority(module)
        uid = os.geteuid() if paths.fixture_mode else 0
        gid = os.getegid() if paths.fixture_mode else 0
        config = freeze_tree_authority(
            label="Portage configuration",
            source=paths.portage_config,
            destination=paths.authority / "portage-config",
            mount_target=paths.portage_config,
            runner=runner,
            tools=tools,
            uid=uid,
            gid=gid,
        )
        global_config = freeze_tree_authority(
            label="Portage global configuration",
            source=paths.portage_global_config,
            destination=paths.authority / "portage-global-config",
            mount_target=paths.portage_global_config,
            runner=runner,
            tools=tools,
            uid=uid,
            gid=gid,
        )
        private_roots = prepare_private_roots(paths)
        vdb_before = vdb_manifest(paths.vdb)
        selected_before = selected_sets_authority(paths)
        live_counter_before = file_observation(paths.cache_edb / "counter")
        authority: dict[str, Any] = {
            "tools": tools_value,
            "python_modules": python_modules,
            "repositories": repositories,
            "portage_config": config,
            "portage_global_config": global_config,
            "pre_dependency_checkpoint": checkpoint,
            "framework": framework_authority(paths),
        }
        private_roots["distdir_authority"] = private_roots["distdir_staging"]
        frozen_repositories = verify_frozen_repository_vector(
            expected=repository_specs,
            paths=paths,
            authority=authority,
            private_roots=private_roots,
            runner=runner,
            tools=tools,
        )
        # Pretend itself is offline.  Only the subsequent fetch-only stage is
        # allowed a network namespace with host networking.
        initial_plan, initial_evidence = run_pretend_stage(
            stage="initial-pretend",
            target_atoms=[arguments.target],
            paths=paths,
            authority=authority,
            private_roots=private_roots,
            runner=runner,
            tools=tools,
            installed=set(vdb_before["cpvs"]),
        )
        exact_plan, exact_evidence = run_pretend_stage(
            stage="exact-repretend-before-prefetch",
            target_atoms=exact_plan_atoms(initial_plan),
            paths=paths,
            authority=authority,
            private_roots=private_roots,
            runner=runner,
            tools=tools,
            installed=set(vdb_before["cpvs"]),
        )
        compare_plans(initial_plan, exact_plan)
        prefetch_evidence = prefetch_distfiles(
            plan=initial_plan,
            paths=paths,
            authority=authority,
            private_roots=private_roots,
            runner=runner,
            tools=tools,
            uid=uid,
            gid=gid,
        )
        offline_plan, offline_evidence = run_pretend_stage(
            stage="offline-exact-repretend",
            target_atoms=exact_plan_atoms(initial_plan),
            paths=paths,
            authority=authority,
            private_roots=private_roots,
            runner=runner,
            tools=tools,
            installed=set(vdb_before["cpvs"]),
        )
        compare_plans(initial_plan, offline_plan)
        compare_vdb(vdb_before, vdb_manifest(paths.vdb))
        verify_selected_sets(paths, selected_before)
        resolver = {
            "target": arguments.target,
            "frozen_repository_observation": frozen_repositories,
            "vdb_before": vdb_before,
            "selected_sets_before": selected_before,
            "live_counter_before": live_counter_before,
            "initial_pretend": initial_evidence,
            "exact_repretend_before_prefetch": exact_evidence,
            "prefetch": prefetch_evidence,
            "offline_exact_repretend": offline_evidence,
            "private_roots_before": private_roots_baseline(private_roots),
            "private_portage_outputs_before": private_portage_outputs(private_roots),
        }
        prepared = base_state(
            paths,
            authority=authority,
            resolver=resolver,
            plan=initial_plan,
            private_roots=private_roots,
        )
        publish_state(paths, prepared)
        return 0


def source_emerge_command(
    tools: Mapping[str, Path],
    plan: Mapping[str, Any],
    prepared_path: Path,
    prepared_sha: str,
    control_fd: int,
    control_session: str,
) -> list[str]:
    if control_fd < 0 or not CONTROL_SESSION_PATTERN.fullmatch(control_session):
        fail("source emerge control-channel identity is invalid")
    return [
        os.fspath(tools["python"]),
        "-I",
        "-B",
        os.fspath(tools["transaction"]),
        "__portage-action",
        os.fspath(prepared_path),
        require_sha256(prepared_sha, "prepared state digest"),
        str(control_fd),
        control_session,
        "--",
        os.fspath(tools["emerge"]),
        *emerge_options(),
        "--ask=y",
        *exact_plan_atoms(plan),
    ]


def qcheck_plan(
    *,
    paths: Paths,
    prepared: Mapping[str, Any],
    runner: Runner,
    tools: Mapping[str, Path],
    stage_prefix: str,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for index, row in enumerate(prepared["plan"]["rows"], start=1):
        cpv = row["cpv"]
        result, stage_evidence = run_contained_stage(
            stage=f"{stage_prefix}-qcheck-{index:03d}",
            paths=paths,
            authority=prepared["authority"],
            private_roots=prepared["private_roots"],
            command=[os.fspath(tools["qcheck"]), f"={cpv}"],
            environment=plan_environment(prepared["private_roots"], offline=True),
            runner=runner,
            tools=tools,
            timeout=30 * 60,
            network_isolated=True,
        )
        require_success(result, f"qcheck {cpv}")
        evidence.append(stage_evidence)
    return evidence


def verify_success_artifacts(
    *,
    paths: Paths,
    prepared: Mapping[str, Any],
    runner: Runner,
    tools: Mapping[str, Path],
    stage_prefix: str,
) -> dict[str, Any]:
    checks = qcheck_plan(
        paths=paths,
        prepared=prepared,
        runner=runner,
        tools=tools,
        stage_prefix=stage_prefix,
    )
    verifier = tools["snapshot_verifier"]
    report = verify_private_pkgdir(
        Path(prepared["private_roots"]["pkgdir"]),
        [row["cpv"] for row in prepared["plan"]["rows"]],
        verifier,
        tools["python"],
        tools["zstd"],
        runner,
        paths.report / f"{stage_prefix}-pkgdir-verification",
    )
    report_path = paths.report / f"{stage_prefix}-pkgdir-verification.json"
    report_sha = atomic_publish_noreplace(report_path, canonical_json(report))
    return {
        "qcheck": checks,
        "private_pkgdir_report": os.fspath(report_path),
        "private_pkgdir_report_sha256": report_sha,
    }


def run_held_lock_recovery(
    *,
    paths: Paths,
    prepared: dict[str, Any],
    prepared_sha: str,
    armed_sha: str,
    rollback_state: dict[str, Any],
    rollback_sha: str,
    tools: Mapping[str, Path],
) -> int:
    """Run exact reverse unmerge and terminal publication under one VDB lock."""

    stage = "recovery-rollback"
    parent_control, child_control = control_channel_pair()
    command = [
        os.fspath(tools["python"]),
        "-I",
        "-B",
        os.fspath(tools["transaction"]),
        "__portage-recovery",
        os.fspath(state_path(paths, "prepared")),
        prepared_sha,
        os.fspath(state_path(paths, "rollback-in-progress")),
        rollback_sha,
        str(child_control.fileno),
        child_control.session,
    ]
    spec = execution_spec(
        bindings=authority_mount_bindings(prepared["authority"], prepared["private_roots"]),
        command=command,
        environment=plan_environment(prepared["private_roots"], offline=True),
        network_isolated=True,
    )
    spec_path = paths.report / f"{stage}.execution.json"
    atomic_publish_noreplace(spec_path, canonical_json(spec))
    stdout_partial = paths.report / f".{stage}.stdout.partial"
    stderr_partial = paths.report / f".{stage}.stderr.partial"
    parent = process_identity(os.getpid(), paths.proc_root)
    if parent is None:
        fail("cannot observe recovery coordinator identity")
    barrier_read, barrier_write = os.pipe2(os.O_CLOEXEC)
    barrier = [
        os.fspath(tools["python"]),
        "-I",
        "-B",
        os.fspath(tools["transaction"]),
        "__barrier",
        str(barrier_read),
        str(parent["pid"]),
        str(parent["start_ticks"]),
        "--",
        *contained_argv(spec_path, tools, network_isolated=True),
    ]
    with stdout_partial.open("x+b") as stdout_file, stderr_partial.open("x+b") as stderr_file:
        process = subprocess.Popen(
            barrier,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            env=clean_environment(),
            start_new_session=True,
            pass_fds=(barrier_read, child_control.fileno),
        )
        child_control.close()
        os.close(barrier_read)
        identity = process_identity(process.pid, paths.proc_root)
        if (
            identity is None
            or identity["process_group"] != process.pid
            or identity["session"] != process.pid
        ):
            os.close(barrier_write)
            terminate_direct_process(process, 5.0)
            fail("recovery child did not enter its private process group/session")
        child = publish_child_sidecar(
            paths,
            identity,
            spec_path,
            parent_control.session,
            destination=paths.recovery_child_sidecar,
        )
        try:
            if os.write(barrier_write, b"G") != 1:
                fail("short recovery-child barrier grant")
        finally:
            os.close(barrier_write)
        try:
            lock_held = parent_control.receive("LOCK_HELD", 30 * 60)
            current_vdb = vdb_manifest(paths.vdb)
            delta = classify_vdb_delta(
                prepared["resolver"]["vdb_before"], current_vdb, prepared["plan"]
            )
            exact_atoms = rollback_order(prepared["plan"], delta["added"])
            if lock_held != {
                "prepared_state_sha256": prepared_sha,
                "rollback_state_sha256": rollback_sha,
                "rollback_atoms": exact_atoms,
                "vdb_sha256": sha256_bytes(canonical_json(current_vdb)),
                "vardb_root": os.fspath(paths.vdb),
            }:
                fail("recovery held-lock authority differs from live state")
            verify_frozen_authorities(paths, prepared)
            verify_private_portage_outputs(
                prepared["private_roots"],
                prepared["resolver"]["private_portage_outputs_before"],
            )
            verify_selected_sets(paths, prepared["resolver"]["selected_sets_before"])
            parent_control.send(
                "DECISION",
                {
                    "outcome": "rolled-back",
                    "state_sha256": rollback_sha,
                    "rollback_atoms": exact_atoms,
                },
            )
            terminal_ready = parent_control.receive("TERMINAL_READY", 2 * 3600)
            if (
                set(terminal_ready)
                != {
                    "outcome",
                    "rollback_status",
                    "decision_state_sha256",
                    "counter",
                    "vdb_sha256",
                    "stdout_size",
                    "stderr_size",
                }
                or terminal_ready.get("outcome") != "rolled-back"
                or terminal_ready.get("rollback_status") != 0
                or terminal_ready.get("decision_state_sha256") != rollback_sha
                or not isinstance(terminal_ready.get("counter"), dict)
                or type(terminal_ready.get("stdout_size")) is not int
                or type(terminal_ready.get("stderr_size")) is not int
            ):
                fail("recovery terminal control authority is invalid")
            current_vdb = vdb_manifest(paths.vdb)
            compare_vdb(prepared["resolver"]["vdb_before"], current_vdb)
            if terminal_ready.get("vdb_sha256") != sha256_bytes(
                canonical_json(current_vdb)
            ):
                fail("recovery child VDB identity differs from live state")
            log_evidence, stdout, stderr = finalize_active_logs(
                paths=paths,
                stage=stage,
                stdout_partial=stdout_partial,
                stderr_partial=stderr_partial,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                expected_sizes=terminal_ready,
            )
            verify_frozen_authorities(paths, prepared)
            verify_private_portage_outputs(
                prepared["private_roots"],
                prepared["resolver"]["private_portage_outputs_before"],
            )
            verify_selected_sets(paths, prepared["resolver"]["selected_sets_before"])
            source_status_value = rollback_state.get("outcome", {}).get(
                "source_status", 125
            )
            source_status = (
                source_status_value
                if type(source_status_value) is int and 0 <= source_status_value <= 255
                else 125
            )
            _completion, completion_sha = publish_child_completion(
                paths=paths,
                prepared_sha=prepared_sha,
                armed_sha=armed_sha,
                decision_state_sha=rollback_sha,
                child=child,
                control_session=parent_control.session,
                outcome="rolled-back",
                source_status=source_status,
                rollback_status=0,
                counter=terminal_ready["counter"],
                vdb=current_vdb,
                logs=log_evidence,
                checks={},
            )
            completion_ref = {
                "path": os.fspath(paths.child_completion),
                "sha256": completion_sha,
            }
            rolled_back = next_state(
                rollback_state,
                rollback_sha,
                "rolled-back",
                child=child,
                outcome={
                    "recovered": True,
                    "logs": log_evidence,
                    "counter": terminal_ready["counter"],
                    "child_completion": completion_ref,
                },
            )
            terminal_path, terminal_sha = publish_state(paths, rolled_back)
            parent_control.send(
                "FINALIZE",
                {
                    "completion_path": os.fspath(paths.child_completion),
                    "completion_sha256": completion_sha,
                    "terminal_state_path": os.fspath(terminal_path),
                    "terminal_state_sha256": terminal_sha,
                },
            )
            acknowledgement = parent_control.receive("FINAL_ACK", 5 * 60)
            if acknowledgement != {
                "outcome": "rolled-back",
                "completion_sha256": completion_sha,
                "terminal_state_sha256": terminal_sha,
            }:
                fail("recovery child final acknowledgement differs from durable authority")
            with transaction_signal_scope():
                process.wait(timeout=5 * 60)
        except BaseException:
            terminate_direct_process(process, 5.0)
            raise
        finally:
            parent_control.close()
        wait_group_empty(process.pid, process.pid, proc_root=paths.proc_root, timeout=5.0)
        if normalize_status(process.returncode) != 0:
            fail("held-lock recovery protocol exited unsuccessfully")
        if (
            Path(log_evidence["stdout_path"]).read_bytes() != stdout
            or Path(log_evidence["stderr_path"]).read_bytes() != stderr
        ):
            fail("recovery child wrote after its no-more-output handoff")
    return 0


def run_command(_arguments: argparse.Namespace, paths: Paths) -> int:
    paths.validate()
    if not LIVE_MUTATION_ENABLED:
        fail(
            "live jsonschema mutation is disabled: preparation concurrency and "
            "post-emerge authority closure are not yet proven"
        )
    with transaction_locks(paths):
        if reconcile_state_chain(paths) is None:
            fail("run requires a durable prepared transaction")
        state, prepared_sha = load_current_state(paths)
        if state["phase"] != "prepared":
            fail(f"run refuses transaction phase {state['phase']}; use recover")
        verify_command(paths)
        if path_exists(paths.child_sidecar):
            fail("a pre-existing child sidecar requires recovery before run")
        prepared = state
        tools = tools_from_manifest(prepared["authority"]["tools"])
        runner: Runner = SubprocessRunner()
        compare_vdb(prepared["resolver"]["vdb_before"], vdb_manifest(paths.vdb))
        verify_selected_sets(paths, prepared["resolver"]["selected_sets_before"])
        _result, _source_evidence, terminal, _terminal_sha = run_armed_source_child(
            paths=paths,
            prepared=prepared,
            prepared_sha=prepared_sha,
            environment=plan_environment(prepared["private_roots"], offline=True),
            tools=tools,
            runner=runner,
            timeout=12 * 3600,
        )
        if terminal["phase"] not in {"success", "rolled-back"}:
            fail(f"source child returned unexpected terminal phase {terminal['phase']}")
        return 0 if terminal["phase"] == "success" else 1


def finalize_from_child_completion(
    *,
    paths: Paths,
    state: dict[str, Any],
    state_sha: str,
    prepared: dict[str, Any],
    prepared_sha: str,
    armed_sha: str,
) -> int:
    completion = validate_child_completion(
        paths,
        read_json_regular(paths.child_completion, "child-completion authority"),
        prepared_sha=prepared_sha,
        armed_sha=armed_sha,
    )
    verify_child_completion_evidence(completion)
    expected_outcome = {
        "armed": "success",
        "rollback-in-progress": "rolled-back",
    }.get(state["phase"])
    if (
        expected_outcome is None
        or completion["outcome"] != expected_outcome
        or completion["decision_state_sha256"] != state_sha
    ):
        fail("child-completion outcome differs from the durable decision state")
    current_vdb = vdb_manifest(paths.vdb)
    if completion["vdb_sha256"] != sha256_bytes(canonical_json(current_vdb)):
        fail("child-completion VDB identity differs from live state")
    counter_after = completion["counter"].get("after")
    if type(counter_after) is not int or read_counter(
        paths.cache_edb / "counter", "live EDB counter"
    ) != counter_after:
        fail("child-completion live EDB counter authority changed")
    verify_frozen_authorities(paths, prepared)
    verify_private_portage_outputs(
        prepared["private_roots"],
        prepared["resolver"]["private_portage_outputs_before"],
    )
    verify_selected_sets(paths, prepared["resolver"]["selected_sets_before"])
    if expected_outcome == "success":
        delta = classify_vdb_delta(
            prepared["resolver"]["vdb_before"], current_vdb, prepared["plan"]
        )
        if not delta["exact_success_delta"]:
            fail("completed success no longer has its exact VDB delta")
        phase = "success"
    else:
        compare_vdb(prepared["resolver"]["vdb_before"], current_vdb)
        delta = classify_vdb_delta(
            prepared["resolver"]["vdb_before"], current_vdb, prepared["plan"]
        )
        phase = "rolled-back"
    terminal = next_state(
        state,
        state_sha,
        phase,
        child=completion["child"],
        outcome={
            "recovered_from_completion": True,
            "delta": delta,
            "checks": completion["checks"],
            "logs": completion["logs"],
            "child_completion": {
                "path": os.fspath(paths.child_completion),
                "sha256": sha256_file(paths.child_completion),
            },
        },
    )
    publish_state(paths, terminal)
    return verify_command(paths)


def recover_command(_arguments: argparse.Namespace, paths: Paths) -> int:
    paths.validate()
    if not LIVE_MUTATION_ENABLED:
        fail(
            "live jsonschema recovery is disabled: preparation concurrency, "
            "held-lock crash recovery, and post-emerge closure are not yet proven"
        )
    with transaction_locks(paths):
        if reconcile_state_chain(paths) is None:
            fail("recover requires a durable transaction")
        state, state_sha = load_current_state(paths)
        if state["phase"] == "prepared":
            if path_exists(paths.child_sidecar):
                child = read_json_regular(paths.child_sidecar, "pre-arm child sidecar")
                if not isinstance(child, dict):
                    fail("pre-arm child sidecar is invalid")
                quiesce_recorded_child(child, paths.proc_root)
                aborted = paths.report / "prearm-aborted-child.json"
                if path_exists(aborted):
                    fail("pre-arm abort evidence already exists")
                os.replace(paths.child_sidecar, aborted)
                fsync_directory(paths.report)
            return 0
        if state["phase"] in TERMINAL_PHASES:
            return verify_command(paths)
        prepared, prepared_sha = load_phase_state(paths, "prepared")
        armed, armed_sha = load_phase_state(paths, "armed")
        tools = tools_from_manifest(prepared["authority"]["tools"])
        if path_exists(paths.child_completion):
            return finalize_from_child_completion(
                paths=paths,
                state=state,
                state_sha=state_sha,
                prepared=prepared,
                prepared_sha=prepared_sha,
                armed_sha=armed_sha,
            )
        if state["phase"] == "armed":
            child = state.get("child")
            if not isinstance(child, dict):
                fail("armed transaction has no exact child identity")
            quiesce_recorded_child(child, paths.proc_root)
            delta = classify_vdb_delta(
                prepared["resolver"]["vdb_before"], vdb_manifest(paths.vdb), prepared["plan"]
            )
            # If the coordinator died before publishing terminal success, the
            # held-lock commit/counter handshake is incomplete.  Never infer
            # success from installed CPVs alone; conservatively roll back.
            if not delta["rollback_eligible"]:
                fail("crashed source transaction has a non-rollback-eligible VDB delta")
            rollback = next_state(
                state,
                state_sha,
                "rollback-in-progress",
                child=state.get("child"),
                outcome={"recovered": True, "source_status": 125, "delta": delta},
            )
            _rollback_path, rollback_sha = publish_state(paths, rollback)
            state, state_sha = rollback, rollback_sha
        if state["phase"] == "rollback-in-progress":
            child = state.get("child")
            if isinstance(child, dict):
                quiesce_recorded_child(child, paths.proc_root)
            if path_exists(paths.recovery_child_sidecar):
                recovery_child = read_json_regular(
                    paths.recovery_child_sidecar, "recovery child sidecar"
                )
                if not isinstance(recovery_child, dict):
                    fail("recovery child sidecar is invalid")
                quiesce_recorded_child(recovery_child, paths.proc_root)
                attempt = 1
                while path_exists(paths.report / f"recovery-child-aborted-{attempt:03d}.json"):
                    attempt += 1
                os.replace(
                    paths.recovery_child_sidecar,
                    paths.report / f"recovery-child-aborted-{attempt:03d}.json",
                )
                fsync_directory(paths.report)
            return run_held_lock_recovery(
                paths=paths,
                prepared=prepared,
                prepared_sha=prepared_sha,
                armed_sha=armed_sha,
                rollback_state=state,
                rollback_sha=state_sha,
                tools=tools,
            )
        fail(f"recovery refuses unknown transaction phase {state['phase']}")


def barrier_command(arguments: argparse.Namespace) -> int:
    if not arguments.command or arguments.command[0] != "--" or len(arguments.command) < 2:
        fail("malformed internal barrier command")
    install_parent_death_signal(arguments.parent_pid, arguments.parent_start_ticks)
    release = os.read(arguments.barrier_fd, 1)
    os.close(arguments.barrier_fd)
    if release != b"G":
        fail("child barrier closed without an exact grant")
    command = arguments.command[1:]
    if not Path(command[0]).is_absolute():
        fail("internal barrier executable is not absolute")
    os.execve(command[0], command, os.environ)
    return 125


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "__barrier":
            return barrier_command(arguments)
        if arguments.action == "__mount-exec":
            return mount_exec_command(arguments)
        if arguments.action == "__pdeath-exec":
            return pdeath_exec_command(arguments)
        if arguments.action == "__portage-action":
            return portage_action_command(arguments)
        if arguments.action == "__portage-recovery":
            return portage_recovery_command(arguments)
        if arguments.action == "__observe-repositories":
            return observe_repositories_command()
        paths = fixture_paths(arguments)
        if arguments.action == "prepare":
            return prepare_command(arguments, paths)
        if arguments.action == "run":
            return run_command(arguments, paths)
        if arguments.action == "recover":
            return recover_command(arguments, paths)
        if arguments.action == "verify":
            return verify_entrypoint(paths)
        fail(f"unsupported action: {arguments.action}")
    except TransactionInterrupted as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 128 + error.signum
    except TransactionError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

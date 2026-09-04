#!/usr/bin/python3.15 -IB
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

The live preparation and mutation entry points are enabled only in this
reviewed successor after the exact predecessor passed its portable boundary and
the explicitly separated, non-package-mutating Gentoo-host capability preflight.
Portage/VDB exclusion, held-lock authority snapshots, exact post-emerge
authority, rollback, counter reconciliation, and terminal durability remain
mandatory; a disabled gate is still tested as an independent fail-closed mode.
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
import pwd
import re
import shlex
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
CATEGORY_PATTERN_TEXT = r"[A-Za-z0-9_][A-Za-z0-9+_.-]*"
PACKAGE_PATTERN_TEXT = r"[A-Za-z0-9_][A-Za-z0-9+_-]*"
VERSION_PATTERN_TEXT = (
    r"[0-9]+(?:\.[0-9]+)*[a-z]?"
    r"(?:_(?:alpha|beta|pre|rc|p)[0-9]*)*"
)
VERSION_REVISION_PATTERN_TEXT = rf"{VERSION_PATTERN_TEXT}(?:-r[0-9]+)?"
CPV_PATTERN_TEXT = (
    rf"{CATEGORY_PATTERN_TEXT}/"
    rf"(?!{PACKAGE_PATTERN_TEXT}-{VERSION_REVISION_PATTERN_TEXT}-"
    rf"{VERSION_REVISION_PATTERN_TEXT}(?=\Z|::))"
    rf"{PACKAGE_PATTERN_TEXT}-{VERSION_REVISION_PATTERN_TEXT}"
)
CPV_PATTERN = re.compile(rf"{CPV_PATTERN_TEXT}\Z")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*\Z")
EXACT_ATOM_PATTERN = re.compile(
    rf"=(?P<cpv>{CPV_PATTERN_TEXT})"
    rf"::(?P<repository>[A-Za-z0-9][A-Za-z0-9+_.-]*)\Z"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SCHEDULED_LINE = re.compile(r"^\[[A-Za-z]")
NEW_SOURCE_LINE = re.compile(
    r"^\[ebuild\s+N(?:\s+[^]]*)?\]\s+"
    rf"(?:=)?(?P<cpv>{CPV_PATTERN_TEXT})"
    r"::(?P<repository>[A-Za-z0-9+_.-]+)(?:\s|$)"
)
TRANSACTION_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
# Managed Python cancellation is deliberately limited to reaping an already
# finalized direct child.  Preparation, arming, Portage mutation, rollback,
# and durable publication retain the caller's original signal dispositions:
# process death in those windows is reconciled from durable state on re-entry.
# Expanding this boundary requires a new state-transition audit.
MANAGED_SIGNAL_BOUNDARY = "terminal-child-reap-only"
PR_SET_PDEATHSIG = 1
PORTAGE_LOCK_ACQUIRE_TIMEOUT_SECONDS = 5.0
PORTAGE_LOCK_RETRY_SECONDS = 0.05
# Candidate-A prerequisite safety gates.  These literal values are enabled only
# after exact predecessor 0a2e16bb passed portable CI run 33879183160 and the
# reviewed non-package-mutating Gentoo-host capability preflight on 2026-09-04.
# They authorize only the runbook's separately guarded prerequisite transaction;
# they do not authorize Candidate-A acceptance, optimization generation, Phase
# 3, or any boot/kernel action.  Setting either value false remains an immediate
# fail-closed stop and is covered independently by the hermetic gate tests.
LIVE_PREPARATION_ENABLED = True
LIVE_MUTATION_ENABLED = True
CONTROL_SCHEMA = "gentoo-optimization-jsonschema-control-v1"
CONTROL_MAX_FRAME = 1024 * 1024
CONTROL_SESSION_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
TRANSACTION_PATH = "/usr/bin:/usr/lib/llvm/22/bin:/bin:/usr/sbin:/sbin"
PHASE_STATE_MAX_BYTES = 64 * 1024 * 1024
LOCKED_AUTHORITY_MAX_BYTES = 512 * 1024 * 1024
RECOVERY_EVIDENCE_MAX_BYTES = 512 * 1024 * 1024
RECOVERY_FAILED_REMEDIATION = {
    "method": "operator-supervised-checkpoint-and-payload-reconciliation",
    "pre_dependency_checkpoint_restore_required": True,
    "exact_admitted_payload_and_residue_reconciliation_required": True,
    "separately_reviewed_terminal_restoration_proof_required": True,
    "project_must_remain_stopped": True,
    "automatic_source_retry": False,
    "automatic_vdb_only_rollback": False,
    "whole_host_byte_identity_claim": False,
}


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
        return cls.from_stat(metadata)

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> "FileIdentity":
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
    if len(payload) > PHASE_STATE_MAX_BYTES:
        fail("transaction state exceeds its reviewed 64 MiB schema bound")
    digest = atomic_publish_noreplace(destination, payload)
    atomic_replace_canonical(paths.canonical_state, destination)
    return destination, digest


def load_phase_state(paths: "Paths", phase: str) -> tuple[dict[str, Any], str]:
    path = state_path(paths, phase)
    value = validate_state(
        read_json_regular(
            path,
            f"{phase} transaction state",
            PHASE_STATE_MAX_BYTES,
        )
    )
    if value["transaction_id"] != paths.transaction_id or value["phase"] != phase:
        fail(f"{phase} state identity differs from its path")
    return value, sha256_file(path)


def load_current_state(paths: "Paths") -> tuple[dict[str, Any], str]:
    value = validate_state(
        read_json_regular(
            paths.canonical_state,
            "canonical transaction state",
            PHASE_STATE_MAX_BYTES,
        )
    )
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
        value = validate_state(
            read_json_regular(
                path,
                f"{phase} transaction state",
                PHASE_STATE_MAX_BYTES,
            )
        )
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
        canonical = validate_state(
            read_json_regular(
                paths.canonical_state,
                "canonical transaction state",
                PHASE_STATE_MAX_BYTES,
            )
        )
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
    def preparation_attempt(self) -> Path:
        return self.state_parent / (
            f"jsonschema-prerequisite-{self.transaction_id}.preparation-attempt.json"
        )

    @property
    def locked_authority(self) -> Path:
        return self.state_parent / (
            f"jsonschema-prerequisite-{self.transaction_id}.locked-authority.json"
        )

    @property
    def child_sidecar(self) -> Path:
        return self.report / "child.json"

    @property
    def child_completion(self) -> Path:
        return self.report / "child-completion.json"

    @property
    def recovery_failure(self) -> Path:
        return self.report / "recovery-failed-evidence.json"

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
    def vdb_lockfile(self) -> Path:
        return self.rooted("/var/db/.pkg.portage_lockfile")

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
        require_direct_child(
            self.preparation_attempt,
            self.state_parent,
            "preparation-attempt path",
        )
        require_direct_child(
            self.locked_authority,
            self.state_parent,
            "locked-authority path",
        )
        require_direct_child(self.child_sidecar, self.report, "child sidecar path")
        require_direct_child(self.child_completion, self.report, "child completion path")
        require_direct_child(
            self.recovery_failure, self.report, "recovery-failed evidence path"
        )
        require_direct_child(
            self.recovery_child_sidecar, self.report, "recovery child sidecar path"
        )


def _stable_parent_identity(path: Path) -> dict[str, int]:
    return _stable_file_identity(FileIdentity.observe(path))


def _stable_file_identity(identity: FileIdentity) -> dict[str, int]:
    return {
        "device": identity.device,
        "inode": identity.inode,
        "uid": identity.uid,
        "gid": identity.gid,
        "mode": identity.mode,
    }


def publish_locked_authority(
    paths: Paths, initial_locked_window: Mapping[str, Any]
) -> dict[str, Any]:
    """Externalize the one bulky held-lock snapshot used by every phase."""

    artifact = {
        "schema": "gentoo-optimization-jsonschema-locked-authority-v1",
        "transaction_id": paths.transaction_id,
        "initial_locked_window": dict(initial_locked_window),
    }
    payload = canonical_json(artifact)
    if len(payload) > LOCKED_AUTHORITY_MAX_BYTES:
        fail("locked authority exceeds its reviewed 512 MiB schema bound")
    digest = atomic_publish_noreplace(paths.locked_authority, payload, 0o600)
    expected_uid = os.geteuid() if paths.fixture_mode else 0
    expected_gid = os.getegid() if paths.fixture_mode else 0
    identity = validate_trusted_regular(
        paths.locked_authority,
        expected_uid,
        expected_gid,
        one_link=True,
    )
    if identity.mode != 0o600 or identity.size != len(payload):
        fail("published locked authority has invalid mode or size")
    return {
        "schema": "gentoo-optimization-jsonschema-locked-authority-reference-v1",
        "path": os.fspath(paths.locked_authority),
        "sha256": digest,
        "size": len(payload),
        "identity": identity.as_json(),
        "parent_identity": _stable_parent_identity(paths.locked_authority.parent),
    }


def load_locked_authority(
    reference: object, transaction_id: str, expected_path: Path
) -> dict[str, Any]:
    """Reopen an immutable bulky authority through its exact parent descriptor."""

    if (
        not isinstance(reference, dict)
        or set(reference)
        != {"schema", "path", "sha256", "size", "identity", "parent_identity"}
        or reference.get("schema")
        != "gentoo-optimization-jsonschema-locked-authority-reference-v1"
        or not isinstance(reference.get("path"), str)
        or type(reference.get("size")) is not int
        or not 0 <= int(reference["size"]) <= LOCKED_AUTHORITY_MAX_BYTES
        or not isinstance(reference.get("identity"), dict)
        or not isinstance(reference.get("parent_identity"), dict)
    ):
        fail("locked-authority reference has an invalid schema")
    require_safe_id(transaction_id, "locked-authority transaction ID")
    path = Path(reference["path"])
    require_absolute(path, "locked-authority path")
    if (
        path != expected_path
        or path.name
        != f"jsonschema-prerequisite-{transaction_id}.locked-authority.json"
    ):
        fail("locked-authority path has a foreign transaction identity")
    require_direct_child(path, expected_path.parent, "locked-authority path")
    expected_parent = cast(dict[str, Any], reference["parent_identity"])
    if set(expected_parent) != {"device", "inode", "uid", "gid", "mode"}:
        fail("locked-authority parent identity has an invalid schema")
    expected_file = cast(dict[str, Any], reference["identity"])
    if set(expected_file) != {
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
        "nlink",
        "size",
    }:
        fail("locked-authority file identity has an invalid schema")
    if (
        expected_file.get("mode") != 0o600
        or expected_file.get("nlink") != 1
        or expected_file.get("size") != reference["size"]
    ):
        fail("locked-authority file identity is not immutable")
    production_parent = Path("/var/lib/gentoo-optimization/state/project")
    if expected_path.parent == production_parent and (
        expected_file.get("uid") != 0
        or expected_file.get("gid") != 0
        or expected_parent.get("uid") != 0
        or expected_parent.get("gid") != 0
    ):
        fail("production locked authority is not root-owned")
    try:
        parent_fd = os.open(
            path.parent,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0),
        )
    except OSError as error:
        fail(f"cannot open locked-authority parent: {error}")
    descriptor = -1
    try:
        if _stable_file_identity(
            FileIdentity.from_stat(os.fstat(parent_fd))
        ) != expected_parent:
            fail("locked-authority parent identity changed")
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except OSError as error:
            fail(f"cannot open locked-authority file: {error}")
        metadata = os.fstat(descriptor)
        if FileIdentity.from_stat(metadata).as_json() != expected_file:
            fail("locked-authority file identity changed")
        chunks: list[bytes] = []
        remaining = int(reference["size"]) + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != reference["size"]:
            fail("locked-authority size differs from its reference")
        if FileIdentity.from_stat(os.fstat(descriptor)).as_json() != expected_file:
            fail("locked-authority file changed while it was read")
        if _stable_file_identity(
            FileIdentity.from_stat(os.fstat(parent_fd))
        ) != expected_parent:
            fail("locked-authority parent changed while it was read")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)
    if sha256_bytes(payload) != require_sha256(
        reference.get("sha256"), "locked-authority digest"
    ):
        fail("locked-authority digest differs from its reference")
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"locked-authority JSON is invalid: {error}")
    if canonical_json(value) != payload:
        fail("locked-authority JSON is not exact canonical encoding")
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "transaction_id", "initial_locked_window"}
        or value.get("schema")
        != "gentoo-optimization-jsonschema-locked-authority-v1"
        or value.get("transaction_id") != transaction_id
        or not isinstance(value.get("initial_locked_window"), dict)
    ):
        fail("locked-authority artifact has an invalid schema")
    return cast(dict[str, Any], value)


def prepared_locked_window(prepared: Mapping[str, Any]) -> dict[str, Any]:
    resolver = prepared.get("resolver")
    if not isinstance(resolver, dict):
        fail("prepared state lacks resolver authority")
    evidence = prepared.get("evidence")
    if (
        "locked_authority" not in resolver
        and (
            not isinstance(evidence, dict)
            or evidence.get("proc_root") != "/proc"
        )
    ):
        legacy = resolver.get("initial_locked_window")
        if isinstance(legacy, dict):
            return legacy
        fallback = {
            "vdb": resolver.get("vdb_before"),
            "selected_sets": resolver.get("selected_sets_before"),
            "mtimedb": resolver.get("mtimedb_before"),
            "counter": resolver.get("live_counter_before"),
            "loader_directories": resolver.get("loader_before"),
            "payload_root": resolver.get("payload_root"),
        }
        if any(value is not None for value in fallback.values()):
            return fallback
    transaction_id = str(prepared.get("transaction_id", ""))
    proc_root_value = evidence.get("proc_root") if isinstance(evidence, dict) else None
    if not isinstance(proc_root_value, str):
        fail("prepared state lacks proc-root authority")
    proc_root = Path(proc_root_value)
    fixture_mode = proc_root != Path("/proc")
    root = proc_root.parent if fixture_mode else Path("/")
    expected_path = Paths(transaction_id, root, fixture_mode).locked_authority
    return load_locked_authority(
        resolver.get("locked_authority"), transaction_id, expected_path
    )["initial_locked_window"]


def prepared_vdb(prepared: Mapping[str, Any]) -> dict[str, Any]:
    value = prepared_locked_window(prepared).get("vdb")
    if not isinstance(value, dict):
        fail("locked authority lacks VDB observation")
    return value


def prepared_locked_value(prepared: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = prepared_locked_window(prepared).get(key)
    if not isinstance(value, dict):
        fail(f"locked authority lacks {key}")
    return value


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


def exact_xattrs(path: Path, *, follow_symlinks: bool = False) -> list[dict[str, str]]:
    """Bind all extended attributes, including ACLs and file capabilities."""

    try:
        names = sorted(os.listxattr(path, follow_symlinks=follow_symlinks))
        return [
            {
                "name": name,
                "value_hex": os.getxattr(
                    path, name, follow_symlinks=follow_symlinks
                ).hex(),
            }
            for name in names
        ]
    except OSError as error:
        fail(f"cannot observe extended attributes for {path}: {error}")


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
            "xattrs": exact_xattrs(path, follow_symlinks=False),
        }
        if stat.S_ISREG(metadata.st_mode):
            base.update(
                type="file",
                size=metadata.st_size,
                nlink=metadata.st_nlink,
                sha256=sha256_file(path),
            )
        elif stat.S_ISDIR(metadata.st_mode):
            base.update(type="directory")
        elif stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            if "\0" in target or "\n" in target or "\r" in target:
                fail(f"unsafe symlink target in tree: {path}")
            base.update(type="symlink", nlink=metadata.st_nlink, target=target)
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
        # The frozen live configuration selects unversioned clang/LLVM tool
        # names, whose reviewed providers are under the LLVM 22 prefix.  Keep
        # the traditional sbin roots only for Portage's root-only helpers.
        "PATH": TRANSACTION_PATH,
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
    """Bind the complete live VDB, including category and dot-file residue."""

    cpvs = sorted(installed_cpvs(vdb))
    packages = []
    counters: list[int] = []
    for cpv in cpvs:
        package_root = vdb / cpv
        manifest = tree_manifest(package_root)
        counter = read_counter(package_root / "COUNTER", f"{cpv} COUNTER")
        counters.append(counter)
        packages.append(
            {
                "cpv": cpv,
                "counter": counter,
                "tree": manifest,
                "tree_sha256": sha256_bytes(canonical_json(manifest)),
            }
        )
    complete_tree = tree_manifest(vdb)
    return {
        "schema_version": 2,
        "root_identity": FileIdentity.observe(vdb).as_json(),
        "root_xattrs": exact_xattrs(vdb, follow_symlinks=False),
        "complete_tree": complete_tree,
        "complete_tree_sha256": sha256_bytes(canonical_json(complete_tree)),
        "cpvs": cpvs,
        "cpvs_sha256": sha256_bytes(("\n".join(cpvs) + "\n").encode()),
        "packages": packages,
        "packages_sha256": sha256_bytes(canonical_json(packages)),
        "maximum_installed_counter": max(counters),
    }


def compare_vdb(expected: object, observed: object) -> None:
    if expected != observed:
        fail("live VDB differs from the prepared package-manager authority")


def file_observation(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"path": os.fspath(path), "type": "absent"}
    base: dict[str, Any] = {
        "path": os.fspath(path),
        **FileIdentity.observe(path).as_json(),
        "xattrs": exact_xattrs(path, follow_symlinks=False),
    }
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


def object_observation(path: Path) -> dict[str, Any]:
    """Observe one object only, without recursively expanding directories."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"path": os.fspath(path), "type": "absent"}
    base: dict[str, Any] = {
        "path": os.fspath(path),
        **FileIdentity.observe(path).as_json(),
        "xattrs": exact_xattrs(path, follow_symlinks=False),
    }
    if stat.S_ISREG(metadata.st_mode):
        base.update(type="file", sha256=sha256_file(path))
    elif stat.S_ISLNK(metadata.st_mode):
        base.update(type="symlink", target=os.readlink(path))
    elif stat.S_ISDIR(metadata.st_mode):
        base.update(type="directory")
    else:
        fail(f"unsupported authority object: {path}")
    return base


def selected_sets_authority(
    paths: Paths, *, ignored_cache_names: frozenset[str] = frozenset()
) -> dict[str, Any]:
    preserved = paths.var_lib_portage / "preserved_libs_registry"
    require_semantically_empty_preserved_registry(preserved)
    var_lib_tree = tree_manifest(paths.var_lib_portage)
    cache_tree = tree_manifest_without_top_level(
        paths.cache_edb, {"counter", *ignored_cache_names}
    )
    return {
        "var_lib_portage": {
            "root": object_observation(paths.var_lib_portage),
            "rows_sha256": var_lib_tree["rows_sha256"],
        },
        "cache_edb_without_counter": {
            "root": object_observation(paths.cache_edb),
            "rows_sha256": cache_tree["rows_sha256"],
        },
        "world": file_observation(paths.var_lib_portage / "world"),
        "world_sets": file_observation(paths.var_lib_portage / "world_sets"),
        "mtimedb": file_observation(paths.cache_edb / "mtimedb"),
        "preserved_libs_registry": file_observation(preserved),
    }


def verify_selected_sets(
    paths: Paths,
    expected: object,
    *,
    ignored_cache_names: frozenset[str] = frozenset(),
) -> None:
    if selected_sets_authority(
        paths, ignored_cache_names=ignored_cache_names
    ) != expected:
        fail("selected sets or Portage resume authority changed")


def mtimedb_authority(path: Path) -> dict[str, Any]:
    value = read_json_regular(path, "Portage mtimedb")
    if not isinstance(value, dict):
        fail("Portage mtimedb is not a JSON object")
    resume = value.get("resume")
    if resume not in (None, {}, []):
        fail("Portage mtimedb contains unresolved resume state")
    stable = {
        key: item
        for key, item in value.items()
        if key not in {"resume", "resume_backup"}
    }
    return {
        "observation": file_observation(path),
        "stable": stable,
        "stable_sha256": sha256_bytes(canonical_json(stable)),
        "resume_present": "resume" in value,
        "resume_backup": value.get("resume_backup"),
        "resume_backup_sha256": sha256_bytes(
            canonical_json(value.get("resume_backup"))
        ),
    }


def verify_mtimedb_transition(before: object, path: Path) -> dict[str, Any]:
    if not isinstance(before, dict) or not isinstance(before.get("stable"), dict):
        fail("prepared mtimedb authority is invalid")
    after = mtimedb_authority(path)
    if (
        after["stable"] != before["stable"]
        or after["stable_sha256"] != before.get("stable_sha256")
    ):
        fail("Portage mtimedb changed outside resume bookkeeping")
    return {
        "stable_sha256": after["stable_sha256"],
        "resume_present": after["resume_present"],
        "resume_backup_before_sha256": before.get("resume_backup_sha256"),
        "resume_backup_after_sha256": after["resume_backup_sha256"],
        "after_observation_sha256": sha256_bytes(
            canonical_json(after["observation"])
        ),
    }


def require_semantically_empty_preserved_registry(path: Path) -> None:
    value = read_json_regular(path, "preserved-libraries registry")
    if value != {}:
        fail("preserved-libraries registry is not semantically empty")


def private_portage_outputs(private_roots: Mapping[str, str]) -> dict[str, Any]:
    root_value = private_roots.get("var_lib_portage")
    if not isinstance(root_value, str):
        fail("private Portage root is absent")
    root = Path(root_value)
    preserved = root / "preserved_libs_registry"
    require_semantically_empty_preserved_registry(preserved)
    complete = tree_manifest(root)
    return {
        "complete_var_lib_portage": {
            "root": object_observation(root),
            "rows_sha256": complete["rows_sha256"],
        },
        "config": file_observation(root / "config"),
        "preserved_libs_registry": file_observation(preserved),
        "repo_revisions": file_observation(root / "repo_revisions"),
        "world": file_observation(root / "world"),
        "world_sets": file_observation(root / "world_sets"),
    }


def verify_private_portage_outputs(private_roots: Mapping[str, str], expected: object) -> None:
    if private_portage_outputs(private_roots) != expected:
        fail("private Portage config/sets/preserved-library authority changed")


def _manifest_rows(value: object, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        fail(f"{label} tree manifest is invalid")
    rows: dict[str, dict[str, Any]] = {}
    for row in value["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            fail(f"{label} tree manifest row is invalid")
        path = row["path"]
        if path in rows:
            fail(f"{label} tree manifest repeats {path}")
        rows[path] = row
    return rows


def manifest_transition(
    before: object,
    after: object,
    *,
    allowed_changed: Callable[[str], bool],
    label: str,
) -> dict[str, Any]:
    before_rows = _manifest_rows(before, f"{label} before")
    after_rows = _manifest_rows(after, f"{label} after")
    removed = sorted(set(before_rows) - set(after_rows))
    added = sorted(set(after_rows) - set(before_rows))
    changed = sorted(
        path
        for path in set(before_rows) & set(after_rows)
        if before_rows[path] != after_rows[path]
    )
    unauthorized = sorted(
        path for path in [*removed, *added, *changed] if not allowed_changed(path)
    )
    if unauthorized:
        fail(f"{label} changed outside its declared authority: {unauthorized}")
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "after_rows_sha256": after.get("rows_sha256") if isinstance(after, dict) else None,
    }


def loader_directory_authority(settings: Mapping[str, Any], root: Path) -> dict[str, Any]:
    candidates: set[Path] = set()
    for pattern in ("usr/lib*", "lib*"):
        for path in root.glob(pattern):
            if path.name != "libexec" and path.is_dir() and not path.is_symlink():
                candidates.add(path.resolve(strict=True))
    for item in str(settings.get("LDPATH", "")).split(":"):
        if not item:
            continue
        candidate = root / item.lstrip("/")
        if candidate.is_dir() and not candidate.is_symlink():
            candidates.add(candidate.resolve(strict=True))
    rows = []
    for path in sorted(candidates, key=os.fspath):
        observation = object_observation(path)
        children = [
            object_observation(child)
            for child in sorted(path.iterdir(), key=lambda item: item.name)
        ]
        metadata = path.lstat()
        rows.append(
            {
                "path": os.fspath(path),
                "mtime_ns": metadata.st_mtime_ns,
                "observation": observation,
                "immediate_children": children,
                "immediate_children_sha256": sha256_bytes(canonical_json(children)),
            }
        )
    return {
        "schema_version": 1,
        "rows": rows,
        "rows_sha256": sha256_bytes(canonical_json(rows)),
    }


def restore_loader_directory_times(value: object) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        fail("loader authority is invalid")
    for row in value["rows"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("path"), str)
            or type(row.get("mtime_ns")) is not int
        ):
            fail("loader authority row is invalid")
        path = Path(row["path"])
        if not path.is_dir() or path.is_symlink():
            fail(f"loader directory disappeared before metadata restoration: {path}")
        os.utime(
            path,
            ns=(path.lstat().st_atime_ns, row["mtime_ns"]),
            follow_symlinks=False,
        )
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _contents_paths(
    vdb: Path, cpvs: Iterable[str], *, include_parents: bool = True
) -> set[str]:
    result: set[str] = set()
    for cpv in cpvs:
        path = vdb / cpv / "CONTENTS"
        try:
            lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        except (OSError, UnicodeError) as error:
            fail(f"cannot read exact CONTENTS for {cpv}: {error}")
        for line in lines:
            if line.startswith("dir "):
                item = line[4:]
            elif line.startswith("obj "):
                fields = line[4:].rsplit(" ", 2)
                if len(fields) != 3:
                    fail(f"invalid obj CONTENTS row for {cpv}")
                item = fields[0]
            elif line.startswith("sym "):
                fields = line[4:].rsplit(" ", 1)
                if len(fields) != 2 or " -> " not in fields[0]:
                    fail(f"invalid sym CONTENTS row for {cpv}")
                item = fields[0].split(" -> ", 1)[0]
            else:
                fail(f"unsupported CONTENTS row for {cpv}: {line!r}")
            if not item.startswith("/") or "\0" in item or "\n" in item:
                fail(f"unsafe CONTENTS path for {cpv}: {item!r}")
            normalized = os.path.normpath(item)
            if normalized != item:
                fail(f"noncanonical CONTENTS path for {cpv}: {item!r}")
            result.add(item)
            if include_parents:
                parent = Path(item).parent
                while parent != Path("/"):
                    result.add(os.fspath(parent))
                    parent = parent.parent
    return result


def verify_vdb_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    outcome: str,
) -> dict[str, Any]:
    delta = classify_vdb_delta(dict(before), dict(after), plan)
    if outcome == "rolled-back":
        if before != after:
            fail("rolled-back VDB differs from the complete prepared authority")
        return delta
    if outcome != "success" or not delta["exact_success_delta"]:
        fail("successful VDB transition lacks the exact planned CPV delta")
    before_packages = {row["cpv"]: row for row in before.get("packages", [])}
    after_packages = {row["cpv"]: row for row in after.get("packages", [])}
    for cpv, row in before_packages.items():
        if after_packages.get(cpv) != row:
            fail(f"pre-existing VDB package authority changed: {cpv}")
    for key in ("device", "inode", "uid", "gid", "mode"):
        if after.get("root_identity", {}).get(key) != before.get(
            "root_identity", {}
        ).get(key):
            fail(f"live VDB root {key} authority changed")
    if after.get("root_xattrs") != before.get("root_xattrs"):
        fail("live VDB root xattrs changed")
    planned_cpvs = {str(row["cpv"]) for row in plan.get("rows", [])}
    before_rows = _manifest_rows(before.get("complete_tree"), "complete VDB before")
    after_rows = _manifest_rows(after.get("complete_tree"), "complete VDB after")

    # Every object that existed before arming is immutable.  In particular an
    # existing category directory is not a disposable scaffold: its metadata
    # and membership are part of the prepared VDB authority.  Portage's
    # ``.*.portage_lockfile`` objects are likewise never successful output;
    # only an identical pre-existing row can remain.
    missing = sorted(set(before_rows) - set(after_rows))
    changed = sorted(
        relative
        for relative in set(before_rows) & set(after_rows)
        if before_rows[relative] != after_rows[relative]
    )
    if missing or changed:
        fail(
            "successful VDB transition changed pre-existing authority: "
            f"missing={missing}, changed={changed}"
        )

    planned_prefixes = tuple(sorted(planned_cpvs))
    planned_categories = {cpv.split("/", 1)[0] for cpv in planned_cpvs}

    def is_planned_addition(relative: str) -> bool:
        if relative in planned_categories and relative not in before_rows:
            return True
        return any(
            relative == prefix or relative.startswith(prefix + "/")
            for prefix in planned_prefixes
        )

    foreign = sorted(
        relative
        for relative in set(after_rows) - set(before_rows)
        if not is_planned_addition(relative)
    )
    lock_residue = sorted(
        relative
        for relative in set(after_rows) - set(before_rows)
        if Path(relative).name.endswith(".portage_lockfile")
    )
    if foreign or lock_residue:
        fail(
            "successful VDB transition contains undeclared residue: "
            f"foreign={foreign}, lock_residue={lock_residue}"
        )
    return delta


PRIVATE_ETC_ENV_UPDATE_PATHS = frozenset(
    {
        "csh.env",
        "environment.d",
        "environment.d/10-gentoo-env.conf",
        "ld.so.cache",
        "ld.so.conf",
        "profile.env",
    }
)
PRIVATE_EDB_MUTABLE_PREFIXES = frozenset(
    {
        "counter",
        "dep",
        "mtimedb",
        "vdb_blockers.pickle",
        "vdb_metadata.pickle",
        "vdb_metadata_delta.json",
    }
)


def _copy_before_observation(prepared: Mapping[str, Any], key: str) -> dict[str, Any]:
    window = prepared_locked_window(prepared)
    copies = window.get("copies")
    if not isinstance(copies, dict) or not isinstance(copies.get("rows"), dict):
        fail("prepared resolver lacks locked private authority copies")
    row = copies["rows"].get(key)
    if not isinstance(row, dict):
        fail(f"prepared resolver lacks locked {key} copy")
    return _expanded_locked_copy_observation(row, "copy_root")


def _expanded_locked_copy_observation(
    row: Mapping[str, Any], root_key: str
) -> dict[str, Any]:
    root = row.get(root_key)
    tree = row.get("tree")
    tree_sha = row.get("tree_sha256")
    if (
        not isinstance(root, dict)
        or not isinstance(tree, dict)
        or tree_sha != sha256_bytes(canonical_json(tree))
    ):
        fail("locked private-copy authority is invalid")
    return {**root, "tree": tree, "tree_sha256": tree_sha}


def verify_declared_post_emerge_authority(
    *,
    paths: Paths,
    prepared: Mapping[str, Any],
    outcome: str,
    verify_live_views: bool = True,
    terminal_durability: object | None = None,
) -> dict[str, Any]:
    """Validate the exact host/private/VDB recovery claim at a terminal boundary."""

    if outcome not in {"success", "rolled-back"}:
        fail(f"unsupported post-emerge outcome: {outcome}")
    private_roots = prepared["private_roots"]
    initial_window = prepared_locked_window(prepared)
    copies = initial_window.get("copies", {}).get("rows", {})
    etc_row = copies.get("etc") if isinstance(copies, dict) else None
    if not isinstance(etc_row, dict):
        fail("prepared resolver lacks live /etc authority")
    etc_source_before = _expanded_locked_copy_observation(
        etc_row, "source_root"
    )
    live_etc = (
        file_observation(paths.rooted("/etc"))
        if verify_live_views
        else etc_source_before
    )
    if verify_live_views and live_etc != etc_source_before:
        fail("live /etc changed despite the private transaction view")
    private_etc_before = _copy_before_observation(prepared, "etc")
    private_etc_after = file_observation(Path(private_roots["etc"]))
    private_cache_before = _copy_before_observation(prepared, "cache_edb")
    private_cache_after = file_observation(Path(private_roots["cache_edb"]))
    etc_allowed = (
        (lambda _path: False)
        if outcome == "rolled-back"
        else (lambda path: path in PRIVATE_ETC_ENV_UPDATE_PATHS)
    )
    etc_delta = manifest_transition(
        private_etc_before.get("tree"),
        private_etc_after.get("tree"),
        allowed_changed=etc_allowed,
        label="private /etc",
    )
    cache_delta = manifest_transition(
        private_cache_before.get("tree"),
        private_cache_after.get("tree"),
        allowed_changed=lambda path: path.split("/", 1)[0]
        in PRIVATE_EDB_MUTABLE_PREFIXES,
        label="private Portage EDB",
    )
    mtimedb_delta = verify_mtimedb_transition(
        initial_window.get("mtimedb"),
        Path(private_roots["cache_edb"]) / "mtimedb",
    )
    private_root_authority = private_roots_terminal_authority(
        private_roots,
        outcome=outcome,
        portage_identity=prepared["resolver"].get("portage_build_identity"),
    )
    vdb_after = vdb_manifest(paths.vdb)
    vdb_delta = verify_vdb_transition(
        prepared_vdb(prepared),
        vdb_after,
        prepared["plan"],
        outcome=outcome,
    )
    loader_before = initial_window.get("loader_directories")
    if not isinstance(loader_before, dict) or not isinstance(loader_before.get("rows"), list):
        fail("prepared resolver lacks loader-directory authority")
    contents = _contents_paths(
        paths.vdb,
        [row["cpv"] for row in prepared["plan"]["rows"]]
        if outcome == "success"
        else [],
    )
    loader_rows: list[dict[str, Any]] = []
    for row in loader_before["rows"]:
        path = Path(row["path"])
        after = object_observation(path)
        after_children = [
            object_observation(child)
            for child in sorted(path.iterdir(), key=lambda item: item.name)
        ]
        root = os.fspath(path)
        before_object = row["observation"]
        for key in ("device", "inode", "uid", "gid", "mode", "type"):
            if after.get(key) != before_object.get(key):
                fail(f"loader directory object authority changed: {path}")
        if outcome == "rolled-back" and after != before_object:
            fail(f"rolled-back loader directory stat differs: {path}")
        before_children = {
            Path(str(item["path"])).name: item for item in row["immediate_children"]
        }
        current_children = {
            Path(str(item["path"])).name: item for item in after_children
        }
        removed = sorted(set(before_children) - set(current_children))
        changed = sorted(
            name
            for name in set(before_children) & set(current_children)
            if before_children[name] != current_children[name]
        )
        added = sorted(set(current_children) - set(before_children))
        unauthorized_added = [
            name for name in added if root.rstrip("/") + "/" + name not in contents
        ]
        if removed or changed or (outcome == "rolled-back" and added) or unauthorized_added:
            fail(f"loader directory immediate authority changed: {path}")
        transition = {"added": added, "removed": removed, "changed": changed}
        metadata = path.lstat()
        if outcome == "rolled-back" and metadata.st_mtime_ns != row["mtime_ns"]:
            fail(f"rolled-back loader directory mtime differs: {path}")
        loader_rows.append(
            {
                "path": root,
                "mtime_ns": metadata.st_mtime_ns,
                "transition": transition,
                "observation_sha256": sha256_bytes(canonical_json(after)),
            }
        )
    verify_private_portage_outputs(
        private_roots, prepared["resolver"]["private_portage_outputs_before"]
    )
    if verify_live_views:
        verify_selected_sets(paths, initial_window["selected_sets"])
    durability = (
        validate_terminal_durability_barrier(
            paths=paths,
            prepared=prepared,
            value=terminal_durability,
        )
        if terminal_durability is not None
        else None
    )
    return {
        "schema_version": 1,
        "outcome": outcome,
        "live_etc_sha256": sha256_bytes(canonical_json(live_etc)),
        "private_etc": etc_delta,
        "private_cache_edb": cache_delta,
        "private_mtimedb": mtimedb_delta,
        "private_roots": private_root_authority,
        "vdb": vdb_delta,
        "loader_directories": loader_rows,
        "terminal_durability": durability,
        "rows_sha256": sha256_bytes(
            canonical_json(
                {
                    "live_etc": live_etc,
                    "private_etc": private_etc_after,
                    "private_cache_edb": private_cache_after,
                    "private_mtimedb": mtimedb_delta,
                    "private_roots": private_root_authority,
                    "vdb": vdb_after,
                    "loader": loader_rows,
                    "terminal_durability": durability,
                }
            )
        ),
    }


def inline_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a structured terminal observation to its canonical digest."""

    payload = dict(value)
    return {
        "value": payload,
        "sha256": sha256_bytes(canonical_json(payload)),
    }


def validate_inline_authority(value: object, label: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"value", "sha256"}
        or not isinstance(value.get("value"), dict)
        or require_sha256(value.get("sha256"), f"{label} digest")
        != sha256_bytes(canonical_json(value["value"]))
    ):
        fail(f"{label} has an invalid canonical binding")
    return cast(dict[str, Any], value)


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
        "/usr/lib/python-exec/python3.15/gemato",
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
        environment=clean_environment(
            {
                "EPYTHON": "python3.15",
                "GNUPG": "/usr/bin/gpg",
                "GNUPGCONF": "/usr/bin/gpgconf",
            }
        ),
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


def external_python_module_authority(
    name: str, interpreter: Path
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
        fail(f"unsafe external Python module authority name: {name}")
    program = (
        "import importlib.util,json,sys; "
        "s=importlib.util.find_spec(sys.argv[1]); "
        "assert s is not None and s.submodule_search_locations; "
        "print(json.dumps(sorted(s.submodule_search_locations)))"
    )
    result = subprocess.run(
        [os.fspath(interpreter), "-I", "-B", "-c", program, name],
        env=clean_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or result.stderr:
        fail(f"external Python package authority is unavailable: {name}")
    try:
        raw_roots = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        fail(f"external Python package authority returned invalid JSON: {error}")
    if not isinstance(raw_roots, list) or not raw_roots:
        fail(f"external Python package authority has no roots: {name}")
    manifests = []
    for value in raw_roots:
        if not isinstance(value, str):
            fail("external Python package authority returned a foreign root")
        root = Path(value).resolve(strict=True)
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
        "interpreter": os.fspath(interpreter),
        "interpreter_identity": inspect_executable(interpreter),
        "roots": manifests,
        "roots_sha256": sha256_bytes(canonical_json(manifests)),
    }


def revalidate_python_module_authority(value: object) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        fail("Python package authority row is invalid")
    current = (
        external_python_module_authority(
            value["name"], Path(str(value["interpreter"]))
        )
        if isinstance(value.get("interpreter"), str)
        else python_module_authority(value["name"])
    )
    if current != value:
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
        source_before = tree_manifest(source, ignore_names=frozenset({".git"}))
        head = runner.run(
            git_argv(tools["git"], "-C", os.fspath(source), "rev-parse", "--verify", "HEAD^{commit}"),
            environment=git_environment(),
            timeout=60,
        )
        commit = head.stdout.decode("ascii", errors="strict").strip()
        if head.status != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            fail(f"cannot bind exact Git commit for {repository.name}")
        observation_commands = (
            (
                "status",
                git_argv(
                    tools["git"],
                    "-C",
                    os.fspath(source),
                    "status",
                    "--porcelain=v1",
                    "-z",
                    "--untracked-files=all",
                ),
            ),
            (
                "diff",
                git_argv(
                    tools["git"],
                    "-C",
                    os.fspath(source),
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                    "HEAD",
                    "--",
                ),
            ),
            (
                "untracked",
                git_argv(
                    tools["git"],
                    "-C",
                    os.fspath(source),
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ),
            ),
        )
        source_observation: dict[str, bytes] = {}
        for label, command in observation_commands:
            observed = runner.run(
                command, environment=git_environment(), timeout=60
            )
            if observed.status != 0 or observed.stderr:
                fail(f"cannot bind effective Git worktree {label}: {repository.name}")
            source_observation[label] = observed.stdout
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
        nested_git = [
            path for path in source.rglob(".git") if path != source / ".git"
        ]
        if nested_git:
            fail(f"effective Git worktree contains a nested .git authority: {repository.name}")
        source_entries = sorted(
            (path for path in source.iterdir() if path.name != ".git"),
            key=lambda path: os.fsencode(path.name),
        )
        if not source_entries:
            fail(f"effective Git worktree is empty: {repository.name}")
        materialization_commands = [
            git_argv(
                tools["git"],
                "-C",
                os.fspath(destination),
                "rm",
                "-r",
                "-f",
                "--ignore-unmatch",
                "--",
                ".",
            ),
            git_argv(
                tools["git"],
                "-C",
                os.fspath(destination),
                "clean",
                "-ffdx",
            ),
            [
                os.fspath(tools["cp"]),
                "-a",
                "--reflink=auto",
                "--one-file-system",
                "--",
                *(os.fspath(path) for path in source_entries),
                os.fspath(destination) + "/",
            ],
            git_argv(
                tools["git"],
                "-C",
                os.fspath(destination),
                "reset",
                "--mixed",
                "--quiet",
                "HEAD",
            ),
        ]
        materialization_rows: list[dict[str, Any]] = []
        for command in materialization_commands:
            is_git = command[0] == os.fspath(tools["git"])
            materialized = runner.run(
                command,
                environment=git_environment() if is_git else clean_environment(),
                timeout=4 * 3600 if not is_git else 3600,
            )
            if materialized.status != 0:
                fail(
                    f"cannot freeze effective Git worktree for {repository.name}: "
                    + materialized.stderr.decode("utf-8", errors="replace")
                )
            materialization_rows.append(
                {
                    "argv": command,
                    "exit_status": materialized.status,
                    "stdout_sha256": sha256_bytes(materialized.stdout),
                    "stderr_sha256": sha256_bytes(materialized.stderr),
                }
            )
        source_repeated = runner.run(
            git_argv(tools["git"], "-C", os.fspath(source), "rev-parse", "--verify", "HEAD^{commit}"),
            environment=git_environment(),
            timeout=60,
        )
        if source_repeated.status != 0 or source_repeated.stdout.decode().strip() != commit:
            fail(f"Git repository source moved during materialization: {repository.name}")
        for label, command in observation_commands:
            repeated_observation = runner.run(
                command, environment=git_environment(), timeout=60
            )
            if (
                repeated_observation.status != 0
                or repeated_observation.stderr
                or repeated_observation.stdout != source_observation[label]
            ):
                fail(
                    f"effective Git worktree {label} changed during materialization: "
                    f"{repository.name}"
                )
        source_after = tree_manifest(source, ignore_names=frozenset({".git"}))
        destination_effective = tree_manifest(
            destination, ignore_names=frozenset({".git"})
        )
        if (
            source_before["rows"] != source_after["rows"]
            or source_before["rows_sha256"] != source_after["rows_sha256"]
            or source_before["rows"] != destination_effective["rows"]
        ):
            fail(f"effective Git worktree changed or copied inexactly: {repository.name}")
        destination_observation: dict[str, bytes] = {}
        for label, source_command in observation_commands:
            destination_command = [
                os.fspath(destination)
                if argument == os.fspath(source)
                else argument
                for argument in source_command
            ]
            observed = runner.run(
                destination_command,
                environment=git_environment(),
                timeout=60,
            )
            if observed.status != 0 or observed.stderr:
                fail(
                    f"cannot verify frozen effective Git worktree {label}: "
                    f"{repository.name}"
                )
            destination_observation[label] = observed.stdout
        if destination_observation != source_observation:
            fail(f"frozen effective Git worktree differs from source: {repository.name}")
        provenance["git"] = {
            "commit": commit,
            "clean_checkout_verified_before_effective_materialization": True,
            "effective_worktree_bound": True,
            "effective_worktree_clean": source_observation["status"] == b"",
            "status_sha256": sha256_bytes(source_observation["status"]),
            "diff_sha256": sha256_bytes(source_observation["diff"]),
            "untracked_sha256": sha256_bytes(source_observation["untracked"]),
            "effective_tree_rows_sha256": source_before["rows_sha256"],
            "effective_materialization": {
                "tools": {
                    "cp": inspect_executable(tools["cp"]),
                    "git": inspect_executable(tools["git"]),
                },
                "rows": materialization_rows,
                "rows_sha256": sha256_bytes(canonical_json(materialization_rows)),
            },
        }
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
                "complete-live-vdb-including-category-and-dot-residue",
                "immutable-external-held-lock-authority-reference",
                "full-var-lib-portage-and-cache-edb-inputs",
                "world-and-world_sets",
                "semantic-empty-preserved-libraries-registry",
                "private-etc-and-private-edb-declared-delta",
                "live-etc-unchanged",
                "preexisting-loader-content-and-declared-loader-metadata",
                "private-pkgdir-distdir-tmpdir-logdir-ccache-thinlto-cargo-rustup-roots",
                "exact-eapi-defined-phases-ebuild-and-setup-eclasses",
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


def _observed_or_error(observer: Callable[[], object]) -> dict[str, Any]:
    """Capture evidence even when ambiguity itself prevents interpretation."""

    try:
        return {"status": "observed", "value": observer()}
    except (OSError, TransactionError) as error:
        return {
            "status": "unobservable",
            "error_type": type(error).__name__,
            "error": str(error),
        }


def raw_vdb_residue_authority(paths: Paths) -> dict[str, Any]:
    observed = file_observation(paths.vdb)
    tree = observed.get("tree")
    rows = _manifest_rows(tree, "raw recovery VDB")
    suspicious = sorted(
        relative
        for relative in rows
        if any(part.startswith("-MERGING-") for part in Path(relative).parts)
        or Path(relative).name.endswith(".portage_lockfile")
    )
    return {
        "observation": observed,
        "suspicious_rows": suspicious,
        "observation_sha256": sha256_bytes(canonical_json(observed)),
    }


def new_vdb_crash_residue(
    paths: Paths, prepared_vdb: Mapping[str, Any]
) -> list[str]:
    current = raw_vdb_residue_authority(paths)["observation"]
    current_rows = _manifest_rows(current.get("tree"), "current raw VDB")
    before_rows = _manifest_rows(
        prepared_vdb.get("complete_tree"), "prepared raw VDB"
    )
    return sorted(
        relative
        for relative, row in current_rows.items()
        if (
            any(part.startswith("-MERGING-") for part in Path(relative).parts)
            or Path(relative).name.endswith(".portage_lockfile")
        )
        and before_rows.get(relative) != row
    )


def counter_partial_paths(live_edb: Path) -> list[str]:
    return sorted(
        os.fspath(path)
        for path in live_edb.iterdir()
        if path.name.startswith(".counter.") or path.name.startswith("counter.partial.")
    )


def publish_recovery_failed(
    *,
    paths: Paths,
    rollback_state: dict[str, Any],
    rollback_sha: str,
    prepared: Mapping[str, Any],
    prepared_sha: str,
    reason: str,
) -> tuple[dict[str, Any], str]:
    """Durably stop an ambiguous armed transaction and require checkpoint repair."""

    if rollback_state.get("phase") != "rollback-in-progress":
        fail("recovery-failed publication requires rollback-in-progress authority")
    child = rollback_state.get("child")
    control_digest = child.get("control_session_sha256") if isinstance(child, dict) else None
    payload_receipts = _observed_or_error(
        lambda: load_existing_payload_admission_references(
            prepared=prepared,
            prepared_sha256=prepared_sha,
            control_session_sha256=require_sha256(
                control_digest, "failed transaction control-session digest"
            ),
        )
    )
    evidence = {
        "schema": "gentoo-optimization-jsonschema-recovery-failed-v1",
        "transaction_id": paths.transaction_id,
        "recorded_at": utc_now(),
        "prepared_state_sha256": require_sha256(
            prepared_sha, "failed transaction prepared digest"
        ),
        "rollback_state_sha256": require_sha256(
            rollback_sha, "failed transaction rollback digest"
        ),
        "reason": reason,
        "reason_sha256": sha256_bytes(reason.encode("utf-8")),
        "armed_child": child,
        "raw_vdb": _observed_or_error(lambda: raw_vdb_residue_authority(paths)),
        "payload_admissions": payload_receipts,
        "live_cache_edb": _observed_or_error(
            lambda: file_observation(paths.cache_edb)
        ),
        "counter_partials": _observed_or_error(
            lambda: counter_partial_paths(paths.cache_edb)
        ),
        "private_roots": _observed_or_error(
            lambda: {
                key: file_observation(Path(value))
                for key, value in sorted(prepared["private_roots"].items())
                if isinstance(value, str) and path_exists(Path(value))
            }
        ),
        "required_remediation": dict(RECOVERY_FAILED_REMEDIATION),
    }
    if path_exists(paths.recovery_failure):
        existing = read_json_regular(
            paths.recovery_failure,
            "recovery-failed evidence",
            RECOVERY_EVIDENCE_MAX_BYTES,
        )
        if (
            not isinstance(existing, dict)
            or existing.get("schema") != evidence["schema"]
            or existing.get("transaction_id") != paths.transaction_id
            or existing.get("prepared_state_sha256") != prepared_sha
            or existing.get("rollback_state_sha256") != rollback_sha
        ):
            fail("existing recovery-failed evidence has foreign authority")
        evidence = existing
        evidence_sha = sha256_file(paths.recovery_failure)
    else:
        evidence_payload = canonical_json(evidence)
        if len(evidence_payload) > RECOVERY_EVIDENCE_MAX_BYTES:
            fail("recovery-failed evidence exceeds its reviewed 512 MiB bound")
        evidence_sha = atomic_publish_noreplace(
            paths.recovery_failure, evidence_payload
        )
    failed = next_state(
        rollback_state,
        rollback_sha,
        "recovery-failed",
        child=rollback_state.get("child"),
        outcome={
            "reason": evidence["reason"],
            "recovery_evidence": {
                "path": os.fspath(paths.recovery_failure),
                "sha256": evidence_sha,
            },
            "operator_supervised_restoration_required": True,
            "separately_reviewed_terminal_restoration_proof_required": True,
            "project_must_remain_stopped": True,
            "rolled_back_claim": False,
        },
    )
    return publish_state(paths, failed)


def verify_recovery_failed_state(
    paths: Paths, state: Mapping[str, Any], prepared_sha: str
) -> None:
    outcome = state.get("outcome")
    if (
        not isinstance(outcome, dict)
        or outcome.get("operator_supervised_restoration_required") is not True
        or outcome.get("separately_reviewed_terminal_restoration_proof_required")
        is not True
        or outcome.get("project_must_remain_stopped") is not True
        or outcome.get("rolled_back_claim") is not False
    ):
        fail("recovery-failed state lacks its fail-closed remediation contract")
    reference = outcome.get("recovery_evidence")
    verify_evidence_reference(reference, "path", "sha256")
    if Path(str(reference["path"])) != paths.recovery_failure:
        fail("recovery-failed state names a foreign evidence path")
    evidence = read_json_regular(
        Path(str(reference["path"])),
        "recovery-failed evidence",
        RECOVERY_EVIDENCE_MAX_BYTES,
    )
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema")
        != "gentoo-optimization-jsonschema-recovery-failed-v1"
        or evidence.get("transaction_id") != paths.transaction_id
        or evidence.get("prepared_state_sha256") != prepared_sha
        or evidence.get("rollback_state_sha256")
        != state.get("previous_state_sha256")
        or evidence.get("reason") != outcome.get("reason")
        or evidence.get("reason_sha256")
        != sha256_bytes(str(evidence.get("reason", "")).encode("utf-8"))
        or evidence.get("required_remediation") != RECOVERY_FAILED_REMEDIATION
    ):
        fail("recovery-failed evidence has an invalid authority binding")


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
        payload = path.read_bytes()
    except OSError as error:
        fail(f"cannot read {label}: {error}")
    if re.fullmatch(rb"(?:0|[1-9][0-9]*)", payload) is None:
        fail(f"{label} is not a canonical nonnegative decimal counter")
    return int(payload.decode("ascii"))


def read_counter_authority(
    path: Path, label: str
) -> tuple[int, dict[str, Any]]:
    """Read one canonical counter through exact single-inode authority."""

    observation = file_observation(path)
    if observation.get("type") != "file" or observation.get("nlink") != 1:
        fail(f"{label} is not a single-link regular file")
    if observation.get("xattrs") != []:
        fail(f"{label} has unreviewed extended attributes")
    value = read_counter(path, label)
    reobserved = file_observation(path)
    if reobserved != observation:
        fail(f"{label} changed during exact observation")
    return value, observation


def stable_counter_observation(value: object, label: str) -> dict[str, Any]:
    """Validate one counter observation while excluding only its bind-view path."""

    required = {
        "path",
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
        "nlink",
        "size",
        "xattrs",
        "type",
        "sha256",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or not isinstance(value.get("path"), str)
        or not Path(str(value["path"])).is_absolute()
        or value.get("type") != "file"
        or value.get("nlink") != 1
        or value.get("xattrs") != []
        or any(
            type(value.get(key)) is not int or int(value[key]) < 0
            for key in ("device", "inode", "uid", "gid", "mode", "size")
        )
    ):
        fail(f"{label} is not exact single-inode counter authority")
    require_sha256(value.get("sha256"), f"{label} content digest")
    return {key: value[key] for key in sorted(required - {"path"})}


def validate_counter_reconciliation_authority(
    *,
    paths: Paths,
    value: object,
    expected_outcome: str,
    verify_current: bool,
) -> dict[str, Any]:
    """Validate one durable counter result and optionally its current live inode."""

    required = {
        "outcome",
        "before",
        "private",
        "package_max",
        "after",
        "intent_path",
        "intent_sha256",
        "completion_path",
        "completion_sha256",
        "live_observation",
        "non_counter_manifest_sha256",
        "resealed_read_only",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("outcome") != expected_outcome
        or expected_outcome not in {"success", "rolled-back"}
        or value.get("resealed_read_only") is not True
    ):
        fail("counter reconciliation authority has an invalid schema or outcome")
    for key in ("before", "private", "after"):
        if type(value.get(key)) is not int or int(value[key]) < 0:
            fail(f"counter reconciliation {key} is invalid")
    package_max = value.get("package_max")
    if package_max is not None and (
        type(package_max) is not int or int(package_max) < 0
    ):
        fail("counter reconciliation package maximum is invalid")
    if expected_outcome == "rolled-back" and package_max is not None:
        fail("rolled-back counter authority unexpectedly has a package maximum")
    endpoints = [int(value["before"]), int(value["private"])]
    if package_max is not None:
        endpoints.append(int(package_max))
    if value["after"] != max(endpoints):
        fail("counter reconciliation selected value differs from its endpoints")
    live_stable = stable_counter_observation(
        value.get("live_observation"), "recorded live EDB counter"
    )
    expected_payload_sha = sha256_bytes(str(value["after"]).encode("ascii"))
    if live_stable.get("sha256") != expected_payload_sha:
        fail("recorded live EDB counter content differs from its selected value")
    for path_key, digest_key, label in (
        ("intent_path", "intent_sha256", "counter reconciliation intent"),
        (
            "completion_path",
            "completion_sha256",
            "counter reconciliation completion",
        ),
    ):
        path_value = value.get(path_key)
        if not isinstance(path_value, str):
            fail(f"{label} path is invalid")
        path = Path(path_value)
        require_absolute(path, f"{label} path")
        require_direct_child(path, paths.report, f"{label} path")
        expected = require_sha256(value.get(digest_key), f"{label} digest")
        if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
            fail(f"{label} changed")
    intent = read_json_regular(
        Path(str(value["intent_path"])), "counter reconciliation intent"
    )
    completion = read_json_regular(
        Path(str(value["completion_path"])), "counter reconciliation completion"
    )
    if (
        not isinstance(intent, dict)
        or intent.get("schema")
        != "gentoo-optimization-jsonschema-counter-intent-v1"
        or intent.get("transaction_id") != paths.transaction_id
        or intent.get("outcome") != expected_outcome
        or intent.get("before") != value["before"]
        or intent.get("private") != value["private"]
        or intent.get("package_max") != package_max
        or intent.get("selected") != value["after"]
        or intent.get("payload_sha256") != expected_payload_sha
    ):
        fail("counter reconciliation intent differs from its terminal authority")
    if (
        not isinstance(completion, dict)
        or set(completion)
        != {
            "schema",
            "transaction_id",
            "prepared_state_sha256",
            "outcome",
            "intent_path",
            "intent_sha256",
            "after",
            "live_observation",
        }
        or completion.get("schema")
        != "gentoo-optimization-jsonschema-counter-completion-v1"
        or completion.get("transaction_id") != paths.transaction_id
        or completion.get("outcome") != expected_outcome
        or completion.get("intent_path") != value["intent_path"]
        or completion.get("intent_sha256") != value["intent_sha256"]
        or completion.get("after") != value["after"]
        or stable_counter_observation(
            completion.get("live_observation"),
            "counter completion live observation",
        )
        != live_stable
    ):
        fail("counter reconciliation completion differs from terminal authority")
    require_sha256(
        value.get("non_counter_manifest_sha256"),
        "counter non-counter manifest digest",
    )
    if verify_current:
        current_value, current_observation = read_counter_authority(
            paths.cache_edb / "counter", "current live EDB counter"
        )
        if (
            current_value != value["after"]
            or stable_counter_observation(
                current_observation, "current live EDB counter"
            )
            != live_stable
        ):
            fail("current live EDB counter differs from terminal authority")
        current_non_counter = tree_manifest_without_top_level(
            paths.cache_edb, {"counter"}
        )
        if (
            current_non_counter["rows_sha256"]
            != value["non_counter_manifest_sha256"]
        ):
            fail("current live EDB non-counter authority changed")
    return value


def reconcile_counter_locked(
    *,
    live_edb: Path,
    private_edb: Path,
    vdb: Path,
    prepared: Mapping[str, Any],
    outcome: str,
    fault: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Advance Portage's counter through an attributable crash-safe intent."""

    live_path = live_edb / "counter"
    private_path = private_edb / "counter"
    current_vdb = vdb_manifest(vdb)
    if outcome == "success":
        delta = classify_vdb_delta(
            prepared_vdb(prepared), current_vdb, prepared["plan"]
        )
        if not delta["exact_success_delta"]:
            fail("cannot reconcile success counter without the exact installed delta")
        package_values = [
            read_counter_authority(
                vdb / row["cpv"] / "COUNTER", f"{row['cpv']} COUNTER"
            )[0]
            for row in prepared["plan"]["rows"]
        ]
    elif outcome == "rolled-back":
        compare_vdb(prepared_vdb(prepared), current_vdb)
        package_values = []
    else:
        fail(f"unsupported EDB counter reconciliation outcome: {outcome}")
    before, live_observation_before = read_counter_authority(
        live_path, "live EDB counter"
    )
    private, _private_observation = read_counter_authority(
        private_path, "private EDB counter"
    )
    selected = max([before, private, *package_values])
    prepared_sha = sha256_bytes(canonical_json(prepared))
    report = Path(str(prepared["evidence"]["directory"]))
    token = sha256_bytes(
        f"{prepared['transaction_id']}\0{prepared_sha}\0{outcome}".encode("utf-8")
    )
    partial = live_edb / f".counter.gentoo-opt.{token}.partial"
    intent_path = report / f"counter-reconciliation-{outcome}.intent.json"
    completion_path = report / f"counter-reconciliation-{outcome}.complete.json"
    payload = str(selected).encode("ascii")
    intent = {
        "schema": "gentoo-optimization-jsonschema-counter-intent-v1",
        "transaction_id": prepared["transaction_id"],
        "prepared_state_sha256": prepared_sha,
        "outcome": outcome,
        "live_path": os.fspath(live_path),
        "partial_path": os.fspath(partial),
        "before": before,
        "private": private,
        "package_max": max(package_values) if package_values else None,
        "selected": selected,
        "payload_sha256": sha256_bytes(payload),
        "live_identity_before": {
            key: live_observation_before[key]
            for key in ("device", "inode", "uid", "gid", "mode", "nlink", "size")
        },
        "live_xattrs_before": live_observation_before["xattrs"],
    }
    if path_exists(intent_path):
        recorded = read_json_regular(intent_path, "counter reconciliation intent")
        if recorded != intent:
            # A completed replace legitimately changes only the current live
            # value/identity.  All decision fields remain bound by the intent.
            stable_keys = set(intent) - {"before", "live_identity_before"}
            recorded_before = recorded.get("before") if isinstance(recorded, dict) else None
            recorded_selected = (
                recorded.get("selected") if isinstance(recorded, dict) else None
            )
            expected_recorded_selected = (
                max([recorded_before, private, *package_values])
                if isinstance(recorded_before, int)
                and not isinstance(recorded_before, bool)
                and recorded_before >= 0
                else None
            )
            if (
                not isinstance(recorded, dict)
                or any(recorded.get(key) != intent[key] for key in stable_keys)
                or recorded_selected != expected_recorded_selected
                or before not in {recorded_before, recorded_selected}
            ):
                fail("existing counter intent differs from exact reconciliation")
            intent = recorded
            before = int(intent["before"])
    else:
        atomic_publish_noreplace(intent_path, canonical_json(intent))
    if fault is not None:
        fault("after-intent")

    selected = int(intent["selected"])
    payload = str(selected).encode("ascii")
    current, current_observation = read_counter_authority(
        live_path, "live EDB counter during reconciliation"
    )
    if current not in {int(intent["before"]), selected}:
        fail("live EDB counter differs from both intent endpoints")
    if current != selected:
        if path_exists(partial):
            partial_observation = file_observation(partial)
            if (
                partial_observation.get("type") != "file"
                or partial_observation.get("nlink") != 1
            ):
                fail("counter partial is not a single-link regular file")
            if partial_observation.get("xattrs") != []:
                fail("counter partial has unreviewed extended attributes")
            if (
                partial_observation["mode"] != current_observation["mode"]
                or partial_observation["uid"] != current_observation["uid"]
                or partial_observation["gid"] != current_observation["gid"]
                or partial.read_bytes() != payload
            ):
                # SIGKILL can land after O_EXCL creation or a short write.  The
                # exact state-bound name is attributable, live still equals the
                # intent's before endpoint, and replacement has not happened;
                # remove and durably recreate it.  Any differently named row is
                # rejected below as foreign residue.
                partial.unlink()
                fsync_directory(partial.parent)
        if not path_exists(partial):
            descriptor = os.open(
                partial,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                int(current_observation["mode"]),
            )
            try:
                if fault is not None:
                    fault("after-create")
                if os.write(descriptor, payload) != len(payload):
                    fail("short write during live EDB counter reconciliation")
                if fault is not None:
                    fault("after-write")
                os.fchown(
                    descriptor,
                    int(current_observation["uid"]),
                    int(current_observation["gid"]),
                )
                os.fchmod(descriptor, int(current_observation["mode"]))
                os.fsync(descriptor)
                if fault is not None:
                    fault("after-file-fsync")
            finally:
                os.close(descriptor)
        partial_value, partial_observation = read_counter_authority(
            partial, "counter partial before publication"
        )
        if (
            partial_value != selected
            or partial_observation["uid"] != current_observation["uid"]
            or partial_observation["gid"] != current_observation["gid"]
            or partial_observation["mode"] != current_observation["mode"]
        ):
            fail("counter partial authority changed before publication")
        os.replace(partial, live_path)
        if fault is not None:
            fault("after-replace")
        fsync_directory(live_path.parent)
        if fault is not None:
            fault("after-directory-fsync")
    elif path_exists(partial):
        fail("counter partial remains after the selected value is visible")

    foreign_partials = sorted(
        os.fspath(path)
        for path in live_edb.iterdir()
        if (
            path.name.startswith(".counter.gentoo-opt.")
            or path.name.startswith(".counter.partial.")
            or path.name.startswith("counter.partial.")
        )
        and path != partial
    )
    if foreign_partials:
        fail("foreign counter publication residue is present: " + ", ".join(foreign_partials))

    after, live_observation = read_counter_authority(
        live_path, "reconciled live EDB counter"
    )
    if after != selected:
        fail("live EDB counter reconciliation did not publish the selected value")
    completion = {
        "schema": "gentoo-optimization-jsonschema-counter-completion-v1",
        "transaction_id": prepared["transaction_id"],
        "prepared_state_sha256": prepared_sha,
        "outcome": outcome,
        "intent_path": os.fspath(intent_path),
        "intent_sha256": sha256_file(intent_path),
        "after": after,
        "live_observation": live_observation,
    }
    if path_exists(completion_path):
        if read_json_regular(completion_path, "counter completion") != completion:
            fail("counter completion differs from current exact authority")
    else:
        atomic_publish_noreplace(completion_path, canonical_json(completion))
    if fault is not None:
        fault("after-completion")
    confirmed_after, confirmed_observation = read_counter_authority(
        live_path, "live EDB counter after completion publication"
    )
    if confirmed_after != after or confirmed_observation != live_observation:
        fail("live EDB counter changed during completion publication")
    return {
        "outcome": outcome,
        "before": int(intent["before"]),
        "private": private,
        "package_max": max(package_values) if package_values else None,
        "after": after,
        "intent_path": os.fspath(intent_path),
        "intent_sha256": sha256_file(intent_path),
        "completion_path": os.fspath(completion_path),
        "completion_sha256": sha256_file(completion_path),
        "live_observation": live_observation,
    }


def attributable_counter_partial_names(
    prepared: Mapping[str, Any], live_edb: Path
) -> frozenset[str]:
    """Return only state-bound counter residue names backed by immutable intents."""

    report = Path(str(prepared["evidence"]["directory"]))
    prepared_sha = sha256_bytes(canonical_json(prepared))
    names: set[str] = set()
    for intent_path in sorted(
        report.glob("counter-reconciliation-*.intent.json"),
        key=lambda path: path.name,
    ):
        intent = read_json_regular(intent_path, "counter reconciliation intent")
        if (
            not isinstance(intent, dict)
            or intent.get("schema")
            != "gentoo-optimization-jsonschema-counter-intent-v1"
            or intent.get("transaction_id") != prepared["transaction_id"]
            or intent.get("prepared_state_sha256") != prepared_sha
            or intent.get("outcome") not in {"success", "rolled-back"}
            or not isinstance(intent.get("partial_path"), str)
        ):
            fail("counter intent has foreign transaction authority")
        partial = Path(intent["partial_path"])
        if partial.parent != live_edb or not partial.name.startswith(
            ".counter.gentoo-opt."
        ) or not partial.name.endswith(".partial"):
            fail("counter intent names an unsafe partial path")
        names.add(partial.name)
    return frozenset(names)


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
    ignored = attributable_counter_partial_names(prepared, live_edb)
    before = tree_manifest_without_top_level(live_edb, {"counter", *ignored})
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
    after = tree_manifest_without_top_level(live_edb, {"counter", *ignored})
    if after != before:
        fail("live EDB authority outside counter changed during reconciliation")
    if primary_error is not None:
        raise primary_error
    if reconciliation is None:
        fail("counter reconciliation produced no result")
    resealed_value, resealed_observation = read_counter_authority(
        live_edb / "counter", "resealed live EDB counter"
    )
    if (
        resealed_value != reconciliation["after"]
        or resealed_observation != reconciliation["live_observation"]
    ):
        fail("live EDB counter authority changed across read-only reseal")
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
        "emerge": tool("/usr/lib/python-exec/python3.15/emerge"),
        "false": tool("/bin/false"),
        "gemato": tool("/usr/lib/python-exec/python3.15/gemato"),
        "git": tool("/usr/bin/git"),
        "gpg": tool("/usr/bin/gpg"),
        "gpgconf": tool("/usr/bin/gpgconf"),
        "mount": tool("/usr/bin/mount"),
        "ldconfig": tool("/usr/bin/ldconfig"),
        "sync": tool("/usr/bin/sync"),
        "cargo": tool("/usr/bin/cargo"),
        "rustc": tool("/usr/bin/rustc"),
        "meson": tool("/usr/lib/python-exec/python3.14/meson"),
        "meson_python": tool("/usr/bin/python3.14"),
        "maturin": tool("/usr/bin/maturin"),
        "ninja": tool("/usr/bin/ninja"),
        "gpep517": tool("/usr/lib/python-exec/python3.15/gpep517"),
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
    expected_parent_uid: int | None = None,
    expected_parent_gid: int | None = None,
    expected_parent_mode: int | None = None,
    validate_ancestors: bool = False,
) -> int:
    parent_before = FileIdentity.observe(path.parent)
    if (
        (expected_parent_uid is not None and parent_before.uid != expected_parent_uid)
        or (expected_parent_gid is not None and parent_before.gid != expected_parent_gid)
        or (expected_parent_mode is not None and parent_before.mode != expected_parent_mode)
        or parent_before.nlink < 2
    ):
        fail(f"stable transaction lock parent is absent or foreign: {path.parent}")
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
    opened_identity = FileIdentity.from_stat(os.fstat(descriptor))
    if (
        FileIdentity.observe(path) != opened_identity
        or FileIdentity.observe(path.parent) != parent_before
    ):
        os.close(descriptor)
        fail(f"stable transaction lock path/fd/parent identity changed: {path}")
    return descriptor


PORTAGE_PROCESS_NAMES = frozenset(
    {"emerge", "ebuild", "ebuild.sh", "emaint", "quickpkg", "portageq"}
)


def _path_is_within(value: str, roots: Sequence[Path]) -> bool:
    value = value.removesuffix(" (deleted)")
    if not value.startswith("/"):
        return False
    candidate = Path(os.path.normpath(value))
    return any(candidate == root or candidate.is_relative_to(root) for root in roots)


def scan_package_manager_activity(
    *,
    proc_root: Path,
    protected_roots: Sequence[Path],
    excluded: Sequence[Mapping[str, int | str]] = (),
    strict_unreadable: bool = True,
) -> dict[str, Any]:
    """Find exact competing Portage processes and protected-path handles."""

    roots = tuple(
        path.resolve(strict=True)
        if path_exists(path)
        else path.parent.resolve(strict=True) / path.name
        for path in protected_roots
    )
    excluded_keys = {
        (int(row["pid"]), int(row["start_ticks"]))
        for row in excluded
        if "pid" in row and "start_ticks" in row
    }
    rows: list[dict[str, Any]] = []
    try:
        proc_entries = sorted(
            (entry for entry in proc_root.iterdir() if entry.name.isdecimal()),
            key=lambda entry: int(entry.name),
        )
    except OSError as error:
        fail(f"cannot enumerate process authority: {error}")
    for entry in proc_entries:
        pid = int(entry.name)
        identity = process_identity(pid, proc_root)
        if identity is None:
            if strict_unreadable and entry.exists():
                fail(f"cannot bind stable process identity during exclusion scan: {pid}")
            continue
        if (pid, int(identity["start_ticks"])) in excluded_keys:
            continue
        reasons: set[str] = set()
        unreadable: set[str] = set()
        command_rows: list[str] = []
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            comm = ""
            unreadable.add("comm")
        if comm in PORTAGE_PROCESS_NAMES:
            reasons.add(f"comm:{comm}")
        try:
            command_rows = [
                item.decode("utf-8", errors="surrogateescape")
                for item in (entry / "cmdline").read_bytes().split(b"\0")
                if item
            ]
        except OSError:
            command_rows = []
            unreadable.add("cmdline")
        for argument in command_rows:
            if Path(argument).name in PORTAGE_PROCESS_NAMES:
                reasons.add(f"argv:{Path(argument).name}")
        try:
            environment = (entry / "environ").read_bytes().split(b"\0")
        except OSError:
            environment = []
            unreadable.add("environ")
        for item in environment:
            key = item.split(b"=", 1)[0]
            if key in {
                b"EBUILD_PHASE",
                b"PORTAGE_BIN_PATH",
                b"PORTAGE_BUILDDIR",
                b"PORTAGE_CONFIGROOT",
                b"PORTAGE_TMPDIR",
            }:
                reasons.add(f"environment:{key.decode('ascii')}")
        for name in ("cwd", "root"):
            try:
                target = os.readlink(entry / name)
            except OSError:
                if (entry / name).exists() or entry.exists():
                    unreadable.add(name)
                continue
            if _path_is_within(target, roots):
                reasons.add(f"{name}:{target}")
        fd_root = entry / "fd"
        try:
            descriptors = sorted(fd_root.iterdir(), key=lambda item: item.name)
        except OSError:
            descriptors = []
            unreadable.add("fd")
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                if descriptor.exists():
                    unreadable.add(f"fd/{descriptor.name}")
                continue
            if _path_is_within(target, roots):
                reasons.add(f"fd/{descriptor.name}:{target}")
        try:
            maps = (entry / "maps").read_text(
                encoding="utf-8", errors="surrogateescape"
            ).splitlines()
        except OSError:
            maps = []
            unreadable.add("maps")
        for line in maps:
            path = line.split(maxsplit=5)[-1] if len(line.split(maxsplit=5)) == 6 else ""
            if _path_is_within(path, roots):
                reasons.add(f"maps:{path}")
            if "/site-packages/portage/" in path or "/site-packages/_emerge/" in path:
                reasons.add(f"portage-module:{path}")
        final_identity = process_identity(pid, proc_root)
        if final_identity is None:
            if entry.exists() and strict_unreadable:
                fail(f"cannot revalidate stable process identity during exclusion scan: {pid}")
            continue
        if final_identity != identity:
            fail(f"process identity changed during exclusion scan: {pid}")
        if strict_unreadable and unreadable:
            reasons.update(f"unreadable:{name}" for name in sorted(unreadable))
        if reasons:
            rows.append(
                {
                    "identity": identity,
                    "comm": comm,
                    "argv": command_rows,
                    "reasons": sorted(reasons),
                }
            )
    return {
        "schema_version": 1,
        "protected_roots": [os.fspath(path) for path in roots],
        "rows": rows,
        "rows_sha256": sha256_bytes(canonical_json(rows)),
    }


def require_no_package_manager_activity(
    *,
    paths: Paths,
    excluded: Sequence[Mapping[str, int | str]] = (),
) -> dict[str, Any]:
    observation = scan_package_manager_activity(
        proc_root=paths.proc_root,
        protected_roots=(
            paths.vdb,
            paths.vdb_lockfile,
            paths.var_lib_portage,
            paths.cache_edb,
        ),
        excluded=excluded,
    )
    if observation["rows"]:
        fail("a competing Portage process or protected-path handle is active")
    return observation


@dataclasses.dataclass(frozen=True)
class LockedPortageAuthority:
    config: Any
    vardb: Any
    preserved_registry: Any
    target: str
    vdb_path: Path
    lock_api: Mapping[str, str]


def portage_lock_api_authority(
    lockdir: Callable[..., Any],
    lockfile: Callable[..., Any],
    contention_error: type[BaseException],
) -> dict[str, str]:
    """Require the exact pinned Portage nonblocking-lock API identities."""

    authority = {
        "lockdir_module": str(getattr(lockdir, "__module__", "")),
        "lockdir_name": str(getattr(lockdir, "__name__", "")),
        "lockfile_module": str(getattr(lockfile, "__module__", "")),
        "lockfile_name": str(getattr(lockfile, "__name__", "")),
        "contention_error_module": str(
            getattr(contention_error, "__module__", "")
        ),
        "contention_error_name": str(
            getattr(contention_error, "__name__", "")
        ),
    }
    expected = {
        "lockdir_module": "portage.locks",
        "lockdir_name": "lockdir",
        "lockfile_module": "portage.locks",
        "lockfile_name": "lockfile",
        "contention_error_module": "portage.exception",
        "contention_error_name": "TryAgain",
    }
    if authority != expected:
        fail("loaded Portage nonblocking-lock API identity differs from policy")
    return authority


@contextlib.contextmanager
def hold_loaded_portage_locks_nonblocking(
    *,
    vardb: Any,
    registry: Any,
    lockdir: Callable[..., Any],
    lockfile: Callable[..., Any],
    contention_error: type[BaseException],
    timeout_seconds: float = PORTAGE_LOCK_ACQUIRE_TIMEOUT_SECONDS,
    retry_seconds: float = PORTAGE_LOCK_RETRY_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Iterator[None]:
    """Acquire exact loaded Portage objects by bounded nonblocking retries."""

    if timeout_seconds <= 0 or retry_seconds <= 0:
        fail("Portage lock acquisition bounds are not positive")
    if (
        getattr(vardb, "_lock", None) is not None
        or getattr(vardb, "_lock_count", None) != 0
    ):
        fail("loaded Portage vardb has pre-existing lock state")
    if getattr(registry, "_lock", None) is not None:
        fail("loaded Portage preserved-libraries registry is already locked")
    deadline = monotonic() + timeout_seconds
    vardb_locked = False
    registry_locked = False

    def retry_or_fail(label: str) -> None:
        remaining = deadline - monotonic()
        if remaining <= 0:
            fail(
                f"{label} remained contended for the bounded "
                f"{timeout_seconds:g}-second acquisition window"
            )
        sleeper(min(retry_seconds, remaining))

    try:
        while not registry_locked:
            try:
                vardb_lock = lockdir(
                    os.fspath(vardb._dbroot), flags=os.O_NONBLOCK
                )
            except contention_error:
                retry_or_fail("Portage vardb lock")
                continue
            if vardb_lock is None:
                fail("nonblocking Portage vardb lock returned no lock authority")
            vardb._lock = vardb_lock
            vardb._lock_count = 1
            vardb_locked = True
            try:
                registry_lock = lockfile(
                    os.fspath(registry._filename), flags=os.O_NONBLOCK
                )
            except contention_error:
                vardb.unlock()
                vardb_locked = False
                retry_or_fail("Portage preserved-libraries registry lock")
                continue
            if registry_lock is None:
                fail(
                    "nonblocking Portage preserved-libraries registry lock returned "
                    "no lock authority"
                )
            registry._lock = registry_lock
            registry_locked = True
        yield
    finally:
        try:
            if registry_locked:
                registry.unlock()
        finally:
            if vardb_locked:
                vardb.unlock()


def require_locked_empty_preserved_registry(vardb: Any) -> None:
    registry = vardb._plib_registry
    registry.lock()
    try:
        registry.load()
        path = Path(str(registry._filename))
        require_semantically_empty_preserved_registry(path)
        if registry.hasEntries() or registry.getPreservedLibs() != {}:
            fail("held Portage preserved-libraries registry is not empty")
    finally:
        registry.unlock()


def plan_metadata_authority(
    *,
    locked: LockedPortageAuthority,
    plan: Mapping[str, Any],
    repositories: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Admit exact frozen EAPI/phase/eclass authority before live mutation."""

    portdb = locked.config.trees[locked.target]["porttree"].dbapi
    repository_rows = {
        str(row.get("name")): row for row in repositories if isinstance(row, Mapping)
    }
    forbidden_root_phases = {"pretend", "preinst", "postinst", "prerm", "postrm"}
    rows: list[dict[str, Any]] = []
    for plan_row in plan.get("rows", []):
        cpv = str(plan_row["cpv"])
        expected_repository = str(plan_row["repository"])
        try:
            eapi, phase_text, inherited_text, observed_repository = portdb.aux_get(
                cpv, ["EAPI", "DEFINED_PHASES", "INHERITED", "repository"]
            )
            ebuild = Path(portdb.findname(cpv, myrepo=expected_repository)).resolve(
                strict=True
            )
        except (KeyError, OSError, TypeError, ValueError) as error:
            fail(f"cannot bind frozen package metadata for {cpv}: {error}")
        phases = sorted(set(str(phase_text).split()))
        inherited = sorted(set(str(inherited_text).split()))
        if str(eapi) != "8" or str(observed_repository) != expected_repository:
            fail(f"package metadata EAPI/repository is outside the reviewed closure: {cpv}")
        if forbidden_root_phases & set(phases):
            fail(f"package defines an unreviewed root-side phase: {cpv}")
        setup_eclasses: list[dict[str, Any]] = []
        if "setup" in phases:
            if (
                not cpv.startswith("dev-python/rpds-py-")
                or not {"cargo", "rust"} <= set(inherited)
            ):
                fail(f"package has an unreviewed setup phase: {cpv}")
            for eclass_name in ("cargo", "rust"):
                matches = []
                for repository in repositories:
                    source = repository.get("source_location")
                    materialized = repository.get("materialized_location")
                    if not isinstance(source, str) or not isinstance(materialized, str):
                        continue
                    source_path = Path(source) / "eclass" / f"{eclass_name}.eclass"
                    if source_path.is_file() and not source_path.is_symlink():
                        matches.append(
                            (
                                source_path.resolve(strict=True),
                                Path(materialized) / "eclass" / f"{eclass_name}.eclass",
                            )
                        )
                if not matches:
                    fail(f"reviewed setup eclass is absent: {eclass_name}")
                source_path, frozen_path = matches[0]
                if not frozen_path.is_file() or sha256_file(source_path) != sha256_file(
                    frozen_path
                ):
                    fail(f"frozen setup eclass differs: {eclass_name}")
                setup_eclasses.append(
                    {
                        "name": eclass_name,
                        "source_path": os.fspath(source_path),
                        "source_sha256": sha256_file(source_path),
                        "frozen_path": os.fspath(frozen_path),
                        "frozen_sha256": sha256_file(frozen_path),
                    }
                )
        repository = repository_rows.get(expected_repository)
        if not isinstance(repository, Mapping):
            fail(f"planned repository lacks frozen authority: {expected_repository}")
        source_root = Path(str(repository.get("source_location"))).resolve(strict=True)
        frozen_root = Path(str(repository.get("materialized_location"))).resolve(strict=True)
        if not ebuild.is_relative_to(source_root):
            fail(f"planned ebuild escapes its configured repository: {cpv}")
        frozen_ebuild = frozen_root / ebuild.relative_to(source_root)
        if not frozen_ebuild.is_file() or frozen_ebuild.is_symlink():
            fail(f"planned frozen ebuild is absent or foreign: {cpv}")
        if sha256_file(ebuild) != sha256_file(frozen_ebuild):
            fail(f"planned frozen ebuild differs from discovery: {cpv}")
        try:
            ebuild_text = frozen_ebuild.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as error:
            fail(f"cannot read frozen ebuild backend authority for {cpv}: {error}")
        backend_matches = re.findall(
            r"(?m)^\s*DISTUTILS_USE_PEP517=(?:\"([^\"]+)\"|'([^']+)'|([^\s#]+))",
            ebuild_text,
        )
        backend_values = {
            next(value for value in match if value) for match in backend_matches
        }
        if len(backend_values) > 1:
            fail(f"planned ebuild has ambiguous PEP517 backend authority: {cpv}")
        pep517_backend = next(iter(backend_values), "setuptools-default")
        if cpv.startswith("dev-python/rpds-py-") and pep517_backend != "maturin":
            fail("reviewed rpds-py closure no longer uses the exact maturin backend")
        rows.append(
            {
                "cpv": cpv,
                "repository": expected_repository,
                "eapi": str(eapi),
                "defined_phases": phases,
                "inherited": inherited,
                "ebuild_path": os.fspath(ebuild),
                "ebuild_sha256": sha256_file(ebuild),
                "frozen_ebuild_path": os.fspath(frozen_ebuild),
                "frozen_ebuild_sha256": sha256_file(frozen_ebuild),
                "reviewed_setup_eclasses": setup_eclasses,
                "pep517_backend": pep517_backend,
            }
        )
    rows.sort(key=lambda row: row["cpv"])
    return {
        "schema_version": 1,
        "rows": rows,
        "rows_sha256": sha256_bytes(canonical_json(rows)),
    }


def verify_plan_metadata_authority(prepared: Mapping[str, Any]) -> None:
    value = prepared.get("resolver", {}).get("plan_metadata")
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("rows"), list)
        or value.get("rows_sha256")
        != sha256_bytes(canonical_json(value.get("rows")))
    ):
        fail("prepared package metadata authority is invalid")
    expected_cpvs = sorted(str(row["cpv"]) for row in prepared["plan"]["rows"])
    observed_cpvs: list[str] = []
    for row in value["rows"]:
        if not isinstance(row, dict):
            fail("prepared package metadata row is invalid")
        cpv = str(row.get("cpv", ""))
        observed_cpvs.append(cpv)
        backend = row.get("pep517_backend")
        if not isinstance(backend, str) or not re.fullmatch(
            r"[A-Za-z0-9_.+-]+", backend
        ):
            fail(f"prepared package metadata has an unsafe PEP517 backend: {cpv}")
        if cpv.startswith("dev-python/rpds-py-") and backend != "maturin":
            fail("prepared rpds-py backend differs from reviewed maturin closure")
        phases = set(row.get("defined_phases", []))
        if row.get("eapi") != "8" or phases & {
            "pretend",
            "preinst",
            "postinst",
            "prerm",
            "postrm",
        }:
            fail(f"prepared package metadata no longer has admitted phases: {cpv}")
        if "setup" in phases and (
            not cpv.startswith("dev-python/rpds-py-")
            or {item.get("name") for item in row.get("reviewed_setup_eclasses", [])}
            != {"cargo", "rust"}
        ):
            fail(f"prepared package metadata has an unreviewed setup phase: {cpv}")
        for path_key, digest_key in (
            ("ebuild_path", "ebuild_sha256"),
            ("frozen_ebuild_path", "frozen_ebuild_sha256"),
        ):
            verify_evidence_reference(row, path_key, digest_key)
        for eclass in row.get("reviewed_setup_eclasses", []):
            if not isinstance(eclass, dict):
                fail("prepared setup eclass authority row is invalid")
            for path_key, digest_key in (
                ("source_path", "source_sha256"),
                ("frozen_path", "frozen_sha256"),
            ):
                verify_evidence_reference(eclass, path_key, digest_key)
    if sorted(observed_cpvs) != expected_cpvs:
        fail("prepared package metadata CPV vector differs from its plan")


@contextlib.contextmanager
def loaded_portage_authority_lock(paths: Paths) -> Iterator[LockedPortageAuthority]:
    """Load once and hold the exact VDB plus registry objects for one window."""

    try:
        import _emerge.actions as actions
        from portage.exception import TryAgain  # type: ignore[import-untyped]
        from portage.locks import lockdir, lockfile  # type: ignore[import-untyped]
    except ImportError as error:
        fail(f"Portage preparation API is unavailable: {error}")
    config = actions.load_emerge_config(
        action=None,
        args=[],
        opts={"--ignore-default-opts": True},
        env=clean_environment(),
    )
    target = config.trees._target_eroot
    if target != "/":
        fail(f"Portage preparation target EROOT is not exact live root: {target!r}")
    vardb = config.trees[target]["vartree"].dbapi
    if config.target_config.trees["vartree"].dbapi is not vardb:
        fail("Portage preparation target does not share one vardb object")
    vdb_path = Path(str(vardb._dbroot)).resolve(strict=True)
    if vdb_path != paths.vdb.resolve(strict=True):
        fail(f"Portage preparation vardb root differs: {vdb_path}")
    registry = vardb._plib_registry
    registry_path = Path(str(registry._filename)).resolve(strict=True)
    expected_registry = (
        paths.var_lib_portage / "preserved_libs_registry"
    ).resolve(strict=True)
    if registry_path != expected_registry:
        fail("Portage preserved-libraries registry path differs")
    lock_api = portage_lock_api_authority(lockdir, lockfile, TryAgain)
    try:
        with hold_loaded_portage_locks_nonblocking(
            vardb=vardb,
            registry=registry,
            lockdir=lockdir,
            lockfile=lockfile,
            contention_error=TryAgain,
        ):
            registry.load()
            require_semantically_empty_preserved_registry(expected_registry)
            if registry.hasEntries() or registry.getPreservedLibs() != {}:
                fail("locked Portage preserved-libraries registry is not empty")
            yield LockedPortageAuthority(
                config, vardb, registry, target, vdb_path, lock_api
            )
    finally:
        for root_trees in config.trees.values():
            if "porttree" not in root_trees.lazy_items:
                root_trees["porttree"].dbapi.close_caches()


def preparation_locked_snapshot(
    *,
    paths: Paths,
    private_roots: Mapping[str, str] | None,
    runner: Runner | None,
    tools: Mapping[str, Path] | None,
    publisher: Callable[[dict[str, Any]], None] | None = None,
    plan: Mapping[str, Any] | None = None,
    repositories: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Observe/copy/reobserve every live package-manager authority under lock."""

    coordinator = process_identity(os.getpid(), paths.proc_root)
    if coordinator is None:
        fail("cannot bind preparation coordinator process identity")
    scan_before = require_no_package_manager_activity(paths=paths, excluded=(coordinator,))
    with loaded_portage_authority_lock(paths) as locked:
        if locked.vardb is not locked.config.trees[locked.target]["vartree"].dbapi:
            fail("Portage preparation replaced its locked vardb object")
        scan_after_lock = require_no_package_manager_activity(
            paths=paths, excluded=(coordinator,)
        )
        vdb_before = vdb_manifest(paths.vdb)
        selected_before = selected_sets_authority(paths)
        mtimedb_before = mtimedb_authority(paths.cache_edb / "mtimedb")
        counter_value, counter_before = read_counter_authority(
            paths.cache_edb / "counter", "live EDB counter"
        )
        live_etc_before = file_observation(paths.rooted("/etc"))
        payload_root_before = object_observation(paths.rooted("/usr"))
        if payload_root_before.get("type") != "directory":
            fail("live payload root is not an exact directory")
        if counter_value < vdb_before["maximum_installed_counter"]:
            fail("live EDB counter is below the maximum installed package counter")
        copies = None
        if private_roots is not None:
            if runner is None or tools is None:
                fail("locked preparation copy lacks its exact runner/tool authority")
            copies = copy_live_private_authorities_locked(
                paths=paths,
                locked=locked,
                private_roots=private_roots,
                runner=runner,
                tools=tools,
            )
        vdb_after = vdb_manifest(paths.vdb)
        selected_after = selected_sets_authority(paths)
        counter_value_after, counter_after = read_counter_authority(
            paths.cache_edb / "counter", "live EDB counter after held observation"
        )
        live_etc_after = file_observation(paths.rooted("/etc"))
        payload_root_after = object_observation(paths.rooted("/usr"))
        if (
            vdb_after != vdb_before
            or selected_after != selected_before
            or counter_after != counter_before
            or counter_value_after != counter_value
            or live_etc_after != live_etc_before
            or payload_root_after != payload_root_before
        ):
            fail("live package-manager authority changed during held-lock observation")
        scan_after = require_no_package_manager_activity(
            paths=paths, excluded=(coordinator,)
        )
        plan_metadata = None
        if plan is not None:
            if repositories is None:
                fail("plan metadata admission lacks frozen repositories")
            plan_metadata = plan_metadata_authority(
                locked=locked, plan=plan, repositories=repositories
            )
        result = {
            "schema_version": 1,
            "portage_lock_api": dict(locked.lock_api),
            "vdb": vdb_before,
            "selected_sets": selected_before,
            "mtimedb": mtimedb_before,
            "counter": counter_before,
            "counter_value": counter_value,
            "live_etc": live_etc_before,
            "payload_root": payload_root_before,
            "copies": copies,
            "loader_directories": loader_directory_authority(
                locked.config.target_config.settings, paths.rooted("/")
            ),
            "effective_portage_policy": effective_portage_policy(
                locked.config.target_config.settings
            ),
            "native_toolchain": native_toolchain_authority(
                locked.config.target_config.settings
            ),
            "plan_metadata": plan_metadata,
            "process_exclusion": {
                "before_lock": scan_before,
                "after_lock": scan_after_lock,
                "after_snapshot": scan_after,
            },
        }
        if publisher is not None:
            publisher(result)
        return result


@dataclasses.dataclass(frozen=True)
class HeldStableLock:
    path: Path
    descriptor: int
    identity: FileIdentity
    parent_descriptor: int
    parent_identity: dict[str, int]


@dataclasses.dataclass(frozen=True)
class HeldStableLocks:
    rows: tuple[HeldStableLock, ...]

    def revalidate(self) -> None:
        for row in self.rows:
            if (
                FileIdentity.observe(row.path) != row.identity
                or FileIdentity.from_stat(os.fstat(row.descriptor)) != row.identity
                or _stable_parent_identity(row.path.parent) != row.parent_identity
                or _stable_file_identity(
                    FileIdentity.from_stat(os.fstat(row.parent_descriptor))
                )
                != row.parent_identity
            ):
                fail(f"held stable-lock authority changed: {row.path}")


@contextlib.contextmanager
def transaction_locks(paths: Paths) -> Iterator[HeldStableLocks]:
    descriptors: list[int] = []
    held_rows: list[HeldStableLock] = []
    uid = os.geteuid() if paths.fixture_mode else 0
    fixture_gid = os.getegid()
    portage_gid = fixture_gid if paths.fixture_mode else grp.getgrnam("portage").gr_gid
    root_gid = fixture_gid if paths.fixture_mode else 0
    strict_ancestors = not paths.fixture_mode
    shared_lock_mode = 0o600 if paths.fixture_mode else 0o640
    shared_parent_mode = 0o700 if paths.fixture_mode else 0o750
    transaction_parent_mode = 0o700
    try:
        for path, exclusive, gid, mode in (
            (paths.framework_lock, False, portage_gid, shared_lock_mode),
            (paths.project_lock, True, portage_gid, shared_lock_mode),
            (paths.generation_lock, False, portage_gid, shared_lock_mode),
            (paths.transaction_lock, True, root_gid, 0o600),
        ):
            descriptor = acquire_flock(
                    path,
                    exclusive=exclusive,
                    expected_uid=uid,
                    expected_gid=gid,
                    expected_mode=mode,
                    expected_parent_uid=uid,
                    expected_parent_gid=portage_gid if path != paths.transaction_lock else root_gid,
                    expected_parent_mode=(
                        transaction_parent_mode
                        if path == paths.transaction_lock
                        else shared_parent_mode
                    ),
                    validate_ancestors=strict_ancestors,
                )
            descriptors.append(descriptor)
            parent_descriptor = os.open(
                path.parent,
                os.O_RDONLY
                | os.O_CLOEXEC
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            descriptors.append(parent_descriptor)
            parent_identity = _stable_file_identity(
                FileIdentity.from_stat(os.fstat(parent_descriptor))
            )
            if _stable_parent_identity(path.parent) != parent_identity:
                fail(f"stable lock parent changed during acquisition: {path.parent}")
            held_rows.append(
                HeldStableLock(
                    path=path,
                    descriptor=descriptor,
                    identity=FileIdentity.from_stat(os.fstat(descriptor)),
                    parent_descriptor=parent_descriptor,
                    parent_identity=parent_identity,
                )
            )
        held = HeldStableLocks(tuple(held_rows))
        held.revalidate()
        yield held
        held.revalidate()
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


def allocated_tree_bytes(path: Path) -> int:
    path = path.resolve(strict=True)
    if not path.is_dir() or path.is_symlink():
        fail(f"capacity source is not a real directory: {path}")
    total = path.lstat().st_blocks * 512
    for entry in path.rglob("*"):
        metadata = entry.lstat()
        total += metadata.st_blocks * 512
    return total


def capacity_preflight(
    *,
    paths: Paths,
    repositories: Sequence[RepositorySpec],
    fixed_authority_reserve: int = 2 * 1024**3,
    fixed_cache_reserve: int = 8 * 1024**3,
) -> dict[str, Any]:
    """Prove per-filesystem capacity before any transaction-scoped materialization."""

    if fixed_authority_reserve < 0 or fixed_cache_reserve < 0:
        fail("capacity reserve is negative")
    authority_sources = [repository.location for repository in repositories]
    authority_sources.extend((paths.portage_config, paths.portage_global_config))
    cache_sources = [
        paths.var_lib_portage,
        paths.cache_edb,
        paths.rooted("/etc"),
    ]
    requirements = {
        paths.authority_parent: sum(allocated_tree_bytes(path) for path in authority_sources)
        + fixed_authority_reserve,
        paths.cache_parent: sum(allocated_tree_bytes(path) for path in cache_sources)
        + fixed_cache_reserve,
        # One recovery evidence object plus one child completion can each
        # reach the reviewed authority bound.  The state filesystem retains
        # one bulky locked-authority artifact and at most four immutable phase
        # records; the canonical state is a hardlink, not another allocation.
        paths.report_parent: 2 * RECOVERY_EVIDENCE_MAX_BYTES,
        paths.state_parent: LOCKED_AUTHORITY_MAX_BYTES + PHASE_STATE_MAX_BYTES * 4,
    }
    devices: dict[int, dict[str, Any]] = {}
    for target, raw_required in requirements.items():
        target = target.resolve(strict=True)
        metadata = target.stat()
        filesystem = os.statvfs(target)
        required = max(raw_required + raw_required // 4, raw_required + 1024**3)
        available = filesystem.f_bavail * filesystem.f_frsize
        row = devices.setdefault(
            metadata.st_dev,
            {
                "device": metadata.st_dev,
                "targets": [],
                "required_bytes": 0,
                "available_bytes": available,
            },
        )
        if row["available_bytes"] != available:
            fail("capacity observations disagree for one filesystem device")
        row["targets"].append(os.fspath(target))
        row["required_bytes"] += required
    rows = sorted(devices.values(), key=lambda row: row["device"])
    for row in rows:
        row["targets"].sort()
        if row["available_bytes"] < row["required_bytes"]:
            fail(
                f"insufficient prerequisite transaction capacity on device "
                f"{row['device']}: required={row['required_bytes']} "
                f"available={row['available_bytes']}"
            )
    return {
        "schema_version": 1,
        "rows": rows,
        "rows_sha256": sha256_bytes(canonical_json(rows)),
    }


def transaction_attempt_objects(paths: Paths) -> list[str]:
    candidates = {
        paths.report,
        paths.authority,
        paths.cache,
        paths.canonical_state,
        paths.preparation_attempt,
        paths.locked_authority,
    }
    candidates.update(paths.state_parent.glob(f"jsonschema-prerequisite-{paths.transaction_id}.*"))
    return sorted(os.fspath(path) for path in candidates if path_exists(path))


def publish_preparation_attempt(
    paths: Paths,
    *,
    capacity: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
) -> str:
    existing = transaction_attempt_objects(paths)
    if existing:
        fail(
            "transaction ID was already used or has an abandoned preparation: "
            + ", ".join(existing)
        )
    value = {
        "schema": "gentoo-optimization-jsonschema-preparation-attempt-v1",
        "transaction_id": paths.transaction_id,
        "recorded_at": utc_now(),
        "boot_id": boot_id(paths.proc_root),
        "capacity": dict(capacity),
        "pre_dependency_checkpoint": dict(checkpoint),
        "reuse_policy": "immutable-attempt-never-reuse-id",
        "status": "preparation-started-or-abandoned-until-prepared-is-durable",
    }
    return atomic_publish_noreplace(paths.preparation_attempt, canonical_json(value))


def prepare_private_roots(paths: Paths) -> dict[str, Any]:
    roots = {
        "pkgdir": paths.cache / "pkgdir",
        "distdir_staging": paths.cache / "distfiles.staging",
        "distdir_runtime": paths.cache / "distfiles.runtime",
        "portage_tmpdir": paths.cache / "tmp",
        "portage_logdir": paths.cache / "logs",
        "ccache_dir": paths.cache / "ccache",
        "thinlto_cache": paths.cache / "thinlto-cache",
        "cargo_home": paths.cache / "cargo-home",
        "rustup_home": paths.cache / "rustup-home",
        "var_lib_portage": paths.cache / "var-lib-portage",
        "cache_edb": paths.cache / "cache-edb",
        "etc": paths.cache / "etc",
        "home": paths.cache / "home",
        "xdg_cache": paths.cache / "xdg-cache",
        "live_cache_edb_view": paths.cache / "live-cache-edb-view",
    }
    paths.cache.mkdir(mode=0o700)
    portage_gid = os.getegid() if paths.fixture_mode else grp.getgrnam("portage").gr_gid
    for key, root in roots.items():
        if key in {"var_lib_portage", "cache_edb", "etc"}:
            # These exact authority copies are created only while the live
            # VDB and preserved-libraries registry locks are held.
            continue
        if key == "portage_tmpdir":
            mode, gid = 0o1777, 0 if not paths.fixture_mode else os.getegid()
        elif key in {
            "distdir_staging",
            "distdir_runtime",
            "portage_logdir",
            "ccache_dir",
            "thinlto_cache",
        }:
            mode, gid = 0o2775, portage_gid
        elif key in {
            "home",
            "xdg_cache",
            "cargo_home",
            "rustup_home",
            "live_cache_edb_view",
        }:
            mode, gid = 0o700, 0 if not paths.fixture_mode else os.getegid()
        else:
            mode, gid = 0o755, 0 if not paths.fixture_mode else os.getegid()
        root.mkdir(mode=mode)
        os.chmod(root, mode)
        if not paths.fixture_mode:
            os.chown(root, 0, gid)
    result = {key: os.fspath(value) for key, value in roots.items()}
    result.update(
        {
            "live_var_lib_portage": os.fspath(paths.var_lib_portage.resolve(strict=True)),
            "live_cache_edb": os.fspath(paths.cache_edb.resolve(strict=True)),
            "live_etc": os.fspath(paths.rooted("/etc").resolve(strict=True)),
            "live_thinlto_cache": os.fspath(
                paths.rooted("/var/tmp/thinlto-cache").resolve(strict=True)
            ),
        }
    )
    return result


def copy_live_private_authorities_locked(
    *,
    paths: Paths,
    locked: LockedPortageAuthority,
    private_roots: Mapping[str, str],
    runner: Runner,
    tools: Mapping[str, Path],
) -> dict[str, Any]:
    """Copy all mutable Portage/loader authorities during one held-lock window."""

    if (
        locked.vdb_path != paths.vdb.resolve(strict=True)
        or locked.vardb is not locked.config.trees[locked.target]["vartree"].dbapi
        or int(getattr(locked.vardb, "_lock_count", 0)) < 1
        or getattr(locked.preserved_registry, "_lock", None) is None
    ):
        fail("private authority copy is not inside the exact held Portage locks")

    rows: dict[str, Any] = {}
    for key, source in (
        ("var_lib_portage", paths.var_lib_portage),
        ("cache_edb", paths.cache_edb),
        ("etc", paths.rooted("/etc")),
    ):
        destination_value = private_roots.get(key)
        if not isinstance(destination_value, str):
            fail(f"private-root vector lacks locked authority {key}")
        destination = Path(destination_value)
        before = file_observation(source)
        copy_tree(source, destination, runner, tools)
        after = file_observation(source)
        copied = file_observation(destination)
        if before != after:
            fail(f"live {key} authority changed during its held-lock copy")
        before_tree = before.get("tree")
        copied_tree = copied.get("tree")
        if (
            not isinstance(before_tree, dict)
            or not isinstance(copied_tree, dict)
            or before_tree.get("rows") != copied_tree.get("rows")
            or before_tree.get("rows_sha256") != copied_tree.get("rows_sha256")
        ):
            fail(f"private {key} copy differs from the held live authority")
        source_root = {
            axis: value
            for axis, value in before.items()
            if axis not in {"tree", "tree_sha256"}
        }
        copy_root = {
            axis: value
            for axis, value in copied.items()
            if axis not in {"tree", "tree_sha256"}
        }
        rows[key] = {
            "source_root": source_root,
            "copy_root": copy_root,
            "tree": before_tree,
            "tree_sha256": before["tree_sha256"],
        }
    return {
        "schema_version": 1,
        "rows": rows,
        "rows_sha256": sha256_bytes(canonical_json(rows)),
    }


def private_roots_baseline(private_roots: Mapping[str, str]) -> dict[str, Any]:
    """Bind prepared private-root membership before any package build."""

    rows: dict[str, Any] = {}
    for key in (
        "pkgdir",
        "distdir_runtime",
        "portage_tmpdir",
        "portage_logdir",
        "ccache_dir",
        "thinlto_cache",
        "cargo_home",
        "rustup_home",
        "home",
        "xdg_cache",
        "var_lib_portage",
        "cache_edb",
        "etc",
    ):
        value = private_roots.get(key)
        if not isinstance(value, str):
            fail(f"private root vector lacks {key}")
        root = Path(value)
        manifest = tree_manifest(root)
        rows[key] = {
            "path": value,
            "manifest_sha256": sha256_bytes(canonical_json(manifest)),
            "row_count": len(_manifest_rows(manifest, f"prepared private {key}")),
        }
    for key in (
        "pkgdir",
        "distdir_runtime",
        "portage_tmpdir",
        "ccache_dir",
        "thinlto_cache",
        "cargo_home",
        "rustup_home",
        "home",
        "xdg_cache",
    ):
        if rows[key]["row_count"]:
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
            "manifest_sha256": sha256_bytes(canonical_json(manifest)),
            "row_count": len(_manifest_rows(manifest, f"prepared private {key}")),
        }
    if current != value["rows"] or sha256_bytes(canonical_json(current)) != value.get("rows_sha256"):
        fail("a prepared private root changed before transaction arming")


def private_roots_terminal_authority(
    private_roots: Mapping[str, str],
    *,
    outcome: str,
    portage_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply an explicit terminal policy to every isolated mutable root."""

    if outcome not in {"success", "rolled-back"}:
        fail("private-root terminal policy has an invalid outcome")
    policies = {
        "pkgdir": "retained-package-evidence",
        "distdir_runtime": "verified-prefetch-derived",
        "portage_tmpdir": "empty-portage-prefix-only",
        "portage_logdir": "retained-log-evidence",
        "ccache_dir": "must-be-empty",
        "thinlto_cache": "retained-build-evidence",
        "cargo_home": "retained-build-evidence",
        "rustup_home": "retained-build-evidence",
        "home": "must-be-empty",
        "xdg_cache": "must-be-empty",
    }
    authority_root_value = private_roots.get("distdir_authority")
    if not isinstance(authority_root_value, str):
        fail("private roots lack distfile authority")
    authority_rows = _manifest_rows(
        tree_manifest(Path(authority_root_value)), "distfile authority"
    )
    rows: dict[str, Any] = {}
    for key, policy in policies.items():
        value = private_roots.get(key)
        if not isinstance(value, str):
            fail(f"private roots lack terminal policy root {key}")
        manifest = tree_manifest(Path(value))
        manifest_rows = _manifest_rows(manifest, f"terminal private {key}")
        if policy == "must-be-empty" and manifest_rows:
            fail(f"terminal private root is not empty: {key}")
        if policy == "empty-portage-prefix-only":
            if set(manifest_rows) not in (set(), {"portage"}):
                fail("terminal PORTAGE_TMPDIR contains undeclared residue")
            if manifest_rows:
                prefix = manifest_rows["portage"]
                expected_uid = (
                    portage_identity.get("uid")
                    if isinstance(portage_identity, Mapping)
                    else None
                )
                expected_gid = (
                    portage_identity.get("gid")
                    if isinstance(portage_identity, Mapping)
                    else None
                )
                if (
                    prefix.get("type") != "directory"
                    or prefix.get("xattrs") != []
                    or prefix.get("mode") not in {0o755, 0o770, 0o775}
                    or (
                        expected_uid is not None
                        and prefix.get("uid") not in {0, expected_uid}
                    )
                    or (
                        expected_gid is not None
                        and prefix.get("gid") not in {0, expected_gid}
                    )
                ):
                    fail("terminal PORTAGE_TMPDIR prefix metadata is foreign")
        if policy == "verified-prefetch-derived":
            for relative, row in manifest_rows.items():
                if row.get("type") == "directory":
                    continue
                expected = authority_rows.get(relative)
                if expected is None:
                    fail("runtime DISTDIR contains a foreign object: " + relative)
                for axis in ("type", "size", "sha256", "target", "xattrs"):
                    if row.get(axis) != expected.get(axis):
                        fail(
                            f"runtime DISTDIR {axis} differs from frozen authority: {relative}"
                        )
        rows[key] = {
            "path": value,
            "policy": policy,
            "manifest": manifest,
            "manifest_sha256": sha256_bytes(canonical_json(manifest)),
        }
    result = {
        "schema_version": 1,
        "outcome": outcome,
        "rows": rows,
    }
    result["rows_sha256"] = sha256_bytes(canonical_json(rows))
    return result


def _terminal_durability_candidates(
    paths: Paths,
    prepared: Mapping[str, Any],
    *,
    require_mounted_views: bool,
) -> list[dict[str, Any]]:
    """Describe one stable syncfs anchor for every possibly mutated device."""

    private_roots = prepared.get("private_roots")
    if not isinstance(private_roots, dict):
        fail("prepared state lacks private roots for its durability barrier")
    initial = prepared_locked_window(prepared)
    copies = initial.get("copies", {}).get("rows", {})
    cache_copy = copies.get("cache_edb") if isinstance(copies, dict) else None
    cache_source = cache_copy.get("source_root") if isinstance(cache_copy, dict) else None
    live_edb_device = cache_source.get("device") if isinstance(cache_source, dict) else None
    if type(live_edb_device) is not int:
        fail("prepared state lacks the live EDB device authority")
    payload_root = initial.get("payload_root")
    payload_device = (
        payload_root.get("device") if isinstance(payload_root, dict) else None
    )
    if (
        not isinstance(payload_root, dict)
        or payload_root.get("type") != "directory"
        or type(payload_device) is not int
    ):
        fail("prepared state lacks the live /usr payload device authority")

    raw: list[tuple[str, Path, Path, int | None]] = [
        (
            "live-payload-root",
            paths.rooted("/usr"),
            paths.rooted("/usr"),
            int(payload_device),
        ),
        ("live-vdb", paths.vdb, paths.vdb, None),
        (
            "live-edb-counter",
            Path(str(private_roots.get("live_cache_edb_view", ""))),
            paths.cache_edb,
            live_edb_device,
        ),
    ]
    for key in (
        "pkgdir",
        "distdir_runtime",
        "portage_tmpdir",
        "portage_logdir",
        "ccache_dir",
        "thinlto_cache",
        "cargo_home",
        "rustup_home",
        "var_lib_portage",
        "cache_edb",
        "etc",
        "home",
        "xdg_cache",
    ):
        value = private_roots.get(key)
        if not isinstance(value, str):
            fail(f"prepared state lacks durability root {key}")
        raw.append((f"private-{key}", Path(value), Path(value), None))
    loader = initial.get("loader_directories")
    if not isinstance(loader, dict) or not isinstance(loader.get("rows"), list):
        fail("prepared state lacks loader durability authority")
    for index, row in enumerate(loader["rows"]):
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            fail("prepared loader durability row is invalid")
        path = Path(row["path"])
        raw.append((f"loader-{index:03d}", path, path, None))

    candidates: list[dict[str, Any]] = []
    for label, sync_path, authority_path, bound_device in raw:
        if not sync_path.is_dir() or sync_path.is_symlink():
            fail(f"terminal durability sync root is not a directory: {sync_path}")
        if not authority_path.is_dir() or authority_path.is_symlink():
            fail(
                f"terminal durability authority root is not a directory: {authority_path}"
            )
        device = authority_path.stat().st_dev if bound_device is None else bound_device
        if (
            (bound_device is None or not require_mounted_views)
            and authority_path.stat().st_dev != device
        ):
            fail(f"terminal durability authority device differs for {label}")
        if require_mounted_views and sync_path.stat().st_dev != device:
            fail(f"terminal durability mount device differs for {label}")
        candidates.append(
            {
                "label": label,
                "sync_path": os.fspath(sync_path),
                "authority_path": os.fspath(authority_path),
                "device": device,
            }
        )
    selected: dict[int, dict[str, Any]] = {}
    for row in sorted(candidates, key=lambda item: str(item["label"])):
        selected.setdefault(int(row["device"]), row)
    return [selected[device] for device in sorted(selected)]


def perform_terminal_durability_barrier(
    *,
    paths: Paths,
    prepared: Mapping[str, Any],
    tools: Mapping[str, Path],
    runner: Runner,
) -> dict[str, Any]:
    """Run bounded syncfs barriers before terminal evidence is published."""

    sync_tool = tools.get("sync")
    if sync_tool is None:
        fail("tool authority lacks sync for the terminal durability barrier")
    tool_identity = inspect_executable(sync_tool)
    rows: list[dict[str, Any]] = []
    for candidate in _terminal_durability_candidates(
        paths, prepared, require_mounted_views=True
    ):
        argv = [os.fspath(sync_tool), "-f", "--", str(candidate["sync_path"])]
        result = runner.run(
            argv,
            environment=clean_environment(),
            timeout=120,
        )
        if result.status != 0:
            fail(
                "terminal durability barrier failed for "
                f"{candidate['label']}: status={result.status}"
            )
        rows.append(
            {
                **candidate,
                "argv_sha256": sha256_bytes(canonical_json(argv)),
                "status": result.status,
                "stdout_sha256": sha256_bytes(result.stdout),
                "stderr_sha256": sha256_bytes(result.stderr),
            }
        )
    result = {
        "schema_version": 1,
        "sync_tool": tool_identity,
        "rows": rows,
    }
    result["rows_sha256"] = sha256_bytes(canonical_json(rows))
    return result


def validate_terminal_durability_barrier(
    *,
    paths: Paths,
    prepared: Mapping[str, Any],
    value: object,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("rows"), list)
    ):
        fail("terminal durability barrier has an invalid schema")
    tools = tools_from_manifest(prepared["authority"]["tools"])
    if value.get("sync_tool") != inspect_executable(tools["sync"]):
        fail("terminal durability barrier used a foreign sync executable")
    expected = _terminal_durability_candidates(
        paths, prepared, require_mounted_views=False
    )
    rows = value["rows"]
    if len(rows) != len(expected):
        fail("terminal durability barrier device coverage differs")
    empty_sha = sha256_bytes(b"")
    for recorded, candidate in zip(rows, expected, strict=True):
        argv = [os.fspath(tools["sync"]), "-f", "--", str(candidate["sync_path"])]
        if (
            not isinstance(recorded, dict)
            or {key: recorded.get(key) for key in candidate} != candidate
            or recorded.get("argv_sha256") != sha256_bytes(canonical_json(argv))
            or recorded.get("status") != 0
            or recorded.get("stdout_sha256") != empty_sha
            or recorded.get("stderr_sha256") != empty_sha
        ):
            fail("terminal durability barrier row differs from exact authority")
    if value.get("rows_sha256") != sha256_bytes(canonical_json(rows)):
        fail("terminal durability barrier digest differs")
    return cast(dict[str, Any], value)


def plan_environment(private_roots: Mapping[str, str], *, offline: bool) -> dict[str, str]:
    environment = clean_environment(
        {
            "CCACHE_DIR": private_roots["ccache_dir"],
            "CARGO_HOME": private_roots["cargo_home"],
            "DISTDIR": private_roots["distdir_runtime"] if offline else private_roots["distdir_staging"],
            "EMERGE_LOG_DIR": private_roots["portage_logdir"],
            "EPYTHON": "python3.15",
            "AUTOCLEAN": "no",
            # FEATURES is incremental in Portage.  Negative tokens remove only
            # external/compiler caches and concurrent package installation;
            # normal sandbox, userpriv, network-sandbox and pid-sandbox policy
            # remains sourced from the frozen configuration authority.
            "FEATURES": (
                "-assume-digests -binpkg-signing -ccache -distcc "
                "-icecream -parallel-install -preserve-libs -unmerge-orphans noinfo "
                "collision-protect protect-owned sandbox userpriv usersandbox "
                "network-sandbox pid-sandbox merge-sync"
            ),
            "NOCOLOR": "1",
            "PKGDIR": private_roots["pkgdir"],
            "PORTAGE_BINHOST": "",
            "PORTAGE_LOGDIR": private_roots["portage_logdir"],
            "PORTAGE_ELOG_SYSTEM": "echo",
            "PORTAGE_TMPDIR": private_roots["portage_tmpdir"],
            "UNINSTALL_IGNORE": "",
            "TERM": "dumb",
            "HOME": private_roots["home"],
            "RUSTUP_HOME": private_roots["rustup_home"],
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


def validate_frozen_portage_policy(authority: Mapping[str, Any]) -> None:
    row = authority.get("portage_config")
    if not isinstance(row, dict) or not isinstance(row.get("materialized_location"), str):
        fail("prepared authority lacks frozen Portage configuration")
    config_root = Path(row["materialized_location"])
    post_emerge = config_root / "bin/post_emerge"
    if path_exists(post_emerge):
        fail("frozen Portage configuration contains a post_emerge hook")


def effective_portage_policy(settings: Mapping[str, Any]) -> dict[str, Any]:
    features = set(str(settings.get("FEATURES", "")).split())
    required = {
        "collision-protect",
        "network-sandbox",
        "pid-sandbox",
        "protect-owned",
        "sandbox",
        "merge-sync",
        "userpriv",
        "usersandbox",
    }
    forbidden = {
        "assume-digests",
        "binpkg-signing",
        "parallel-install",
        "preserve-libs",
        "unmerge-orphans",
    }
    if not required <= features or forbidden & features:
        fail(
            "effective Portage FEATURES differ from the transaction policy: "
            f"missing={sorted(required - features)} "
            f"forbidden={sorted(forbidden & features)}"
        )
    if str(settings.get("AUTOCLEAN", "")).strip().lower() != "no":
        fail("effective Portage AUTOCLEAN is not exactly disabled")
    if str(settings.get("UNINSTALL_IGNORE", "")).strip():
        fail("effective Portage UNINSTALL_IGNORE is not empty")
    return {
        "schema_version": 1,
        "features": sorted(features),
        "autoclean": "no",
        "uninstall_ignore": "",
        "install_mask": str(settings.get("INSTALL_MASK", "")),
        "config_protect": str(settings.get("CONFIG_PROTECT", "")),
        "config_protect_mask": str(settings.get("CONFIG_PROTECT_MASK", "")),
    }


NATIVE_BUILD_COMMAND_DEFAULTS: dict[str, str] = {
    "AR": "ar",
    "CC": "cc",
    "CPP": "cpp",
    "CXX": "c++",
    "LD": "ld",
    "NM": "nm",
    "PKG_CONFIG": "pkg-config",
    "RANLIB": "ranlib",
    "STRIP": "strip",
}


def native_toolchain_authority(settings: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for variable, default in sorted(NATIVE_BUILD_COMMAND_DEFAULTS.items()):
        configured = str(settings.get(variable, "") or default)
        words = shlex.split(configured)
        if len(words) != 1 or "\0" in words[0]:
            fail(f"effective Portage {variable} is not one exact executable")
        resolved_value = shutil.which(words[0], path=clean_environment()["PATH"])
        if resolved_value is None:
            fail(f"effective Portage {variable} executable is unavailable: {words[0]}")
        resolved = Path(resolved_value).resolve(strict=True)
        result = subprocess.run(
            [os.fspath(resolved), "--version"],
            env=clean_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            fail(f"cannot observe effective Portage {variable} version")
        rows.append(
            {
                "variable": variable,
                "configured": configured,
                "resolved": os.fspath(resolved),
                "executable": inspect_executable(resolved),
                "stdout": result.stdout.decode("utf-8", errors="strict"),
                "stderr": result.stderr.decode("utf-8", errors="strict"),
            }
        )
    return {
        "schema_version": 1,
        "execution_path": TRANSACTION_PATH,
        "rows": rows,
        "rows_sha256": sha256_bytes(canonical_json(rows)),
    }


def revalidate_native_toolchain(value: object) -> None:
    if (
        not isinstance(value, dict)
        or value.get("execution_path") != TRANSACTION_PATH
        or not isinstance(value.get("rows"), list)
    ):
        fail("native build-tool authority is invalid")
    settings = {
        str(row.get("variable")): str(row.get("configured"))
        for row in value["rows"]
        if isinstance(row, dict)
    }
    if native_toolchain_authority(settings) != value:
        fail("effective native build-tool authority changed")


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


BUILD_VERSION_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "cargo": ("--version",),
    "emerge": ("--version",),
    "gpep517": ("--help",),
    "meson": ("--version",),
    "maturin": ("--version",),
    "ninja": ("--version",),
    "python": ("--version",),
    "rustc": ("-vV",),
}


def build_tool_version_authority(
    tools: Mapping[str, Path], runner: Runner
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name, arguments in sorted(BUILD_VERSION_ARGUMENTS.items()):
        path = tools.get(name)
        if path is None:
            fail(f"build-tool authority lacks {name}")
        result = runner.run(
            [os.fspath(path), *arguments],
            environment=clean_environment({"EPYTHON": "python3.15"}),
            timeout=60,
        )
        if result.status != 0:
            fail(f"cannot observe exact {name} version: status={result.status}")
        rows.append(
            {
                "name": name,
                "path": os.fspath(path),
                "arguments": list(arguments),
                "stdout": result.stdout.decode("utf-8", errors="strict"),
                "stderr": result.stderr.decode("utf-8", errors="strict"),
                "executable": inspect_executable(path),
            }
        )
    return {
        "schema_version": 1,
        "rows": rows,
        "rows_sha256": sha256_bytes(canonical_json(rows)),
    }


def revalidate_build_tool_versions(value: object) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        fail("build-tool version authority is invalid")
    tools = {
        str(row.get("name")): Path(str(row.get("path")))
        for row in value["rows"]
        if isinstance(row, dict)
    }
    current = build_tool_version_authority(tools, SubprocessRunner())
    if current != value:
        fail("build-tool binary or reported version changed")


def build_execution_scope(
    plan_metadata: Mapping[str, Any], tools: Mapping[str, Path]
) -> dict[str, Any]:
    inherited = sorted(
        {
            str(eclass)
            for row in plan_metadata.get("rows", [])
            for eclass in row.get("inherited", [])
        }
    )
    required = {"emerge", "gpep517", "python"}
    if {"cargo", "rust", "maturin"} & set(inherited):
        required.update({"cargo", "rustc"})
    if {"meson", "meson-python", "meson-r1"} & set(inherited):
        required.update({"meson", "ninja"})
    backends = {
        str(row.get("pep517_backend")) for row in plan_metadata.get("rows", [])
    }
    if "maturin" in backends:
        required.add("maturin")
    missing = sorted(required - set(tools))
    if missing:
        fail("derived build-tool scope is unavailable: " + ", ".join(missing))
    rows = [
        {"name": name, "path": os.fspath(tools[name])}
        for name in sorted(required)
    ]
    return {
        "schema_version": 1,
        "derived_from_inherited_eclasses": inherited,
        "declared_pep517_backends": sorted(backends),
        "reviewed_tools": rows,
        "reviewed_tools_sha256": sha256_bytes(canonical_json(rows)),
        "scope": (
            "explicit coordinator and eclass-derived build tools; arbitrary ebuild "
            "commands remain authorized only by the complete frozen repository, "
            "ebuild, eclass, sandbox, and admitted-payload authorities"
        ),
        "claims_exhaustive_runtime_exec_closure": False,
    }


def authority_mount_bindings(
    authority: Mapping[str, Any], private_roots: Mapping[str, str]
) -> list[MountBinding]:
    """Build the complete mount authority for one Portage observation/action."""

    repositories = authority.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        fail("prepared authority has no repository vector")
    bindings: list[MountBinding] = []
    private_etc = private_roots.get("etc")
    live_etc = private_roots.get("live_etc")
    if not isinstance(private_etc, str) or not isinstance(live_etc, str):
        fail("private roots lack complete /etc authority")
    # Expose a private writable /etc first, then seal the durable source path.
    # Nested configuration binds below therefore land inside the private view.
    bindings.append(MountBinding(Path(private_etc), Path(live_etc), False))
    bindings.append(MountBinding(Path(private_etc), Path(private_etc), True))
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
        bindings.append(MountBinding(Path(source), Path(source), True))
    thinlto, live_thinlto = private_roots.get("thinlto_cache"), private_roots.get(
        "live_thinlto_cache"
    )
    if not isinstance(thinlto, str) or not isinstance(live_thinlto, str):
        fail("private roots lack ThinLTO cache mount endpoints")
    bindings.append(MountBinding(Path(thinlto), Path(live_thinlto), False))
    bindings.append(MountBinding(Path(thinlto), Path(thinlto), True))
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
def blocked_transaction_signals(label: str) -> Iterator[set[signal.Signals]]:
    """Block every managed signal for one disposition-change boundary."""

    pthread_sigmask = getattr(signal, "pthread_sigmask", None)
    if not callable(pthread_sigmask):
        fail("jsonschema prerequisite signal lifecycle requires pthread_sigmask")
    try:
        previous_mask = pthread_sigmask(signal.SIG_BLOCK, TRANSACTION_SIGNALS)
    except (OSError, ValueError) as error:
        fail(f"cannot block managed transaction signals during {label}: {error}")
    try:
        yield set(previous_mask)
    finally:
        try:
            pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        except (OSError, ValueError) as error:
            fail(f"cannot restore the signal mask after {label}: {error}")


def raise_transaction_interrupted(signum: int, _frame: object) -> NoReturn:
    """Map one managed HUP/INT/TERM delivery to the exact shell status."""

    if signum not in TRANSACTION_SIGNALS:
        fail(f"unmanaged signal reached transaction handler: {signum}")
    raise TransactionInterrupted(signum)


def install_transaction_signal_handlers(
    handler: Callable[[int, object], None],
    previous_handlers: dict[signal.Signals, Any],
) -> None:
    """Install all managed dispositions atomically from signal delivery."""

    if previous_handlers:
        fail("transaction signal handler authority is not initially empty")
    with blocked_transaction_signals(
        "jsonschema prerequisite handler installation"
    ) as previous_mask:
        inherited_blocked = sorted(
            set(previous_mask).intersection(TRANSACTION_SIGNALS), key=int
        )
        if inherited_blocked:
            fail(
                "jsonschema prerequisite coordinator inherited blocked managed "
                "signals: "
                + ", ".join(
                    signal.Signals(signum).name for signum in inherited_blocked
                )
            )
        originals = {
            signum: signal.getsignal(signum) for signum in TRANSACTION_SIGNALS
        }
        installed: list[signal.Signals] = []
        try:
            for signum in TRANSACTION_SIGNALS:
                signal.signal(signum, handler)
                installed.append(signum)
        except (OSError, RuntimeError, ValueError) as error:
            for signum in reversed(installed):
                signal.signal(signum, originals[signum])
            fail(f"cannot install managed transaction signal handlers: {error}")
        previous_handlers.update(originals)


def restore_transaction_signal_handlers(
    previous_handlers: Mapping[signal.Signals, Any],
) -> None:
    """Restore all caller dispositions under one blocked signal set."""

    if set(previous_handlers) != set(TRANSACTION_SIGNALS):
        fail("transaction signal handler authority is incomplete")
    # Signals delivered during this handoff stay pending until the caller's
    # original dispositions are back in place.  They then retain the caller's
    # non-managed semantics outside the terminal-child reap boundary.
    with blocked_transaction_signals(
        "jsonschema prerequisite handler restoration"
    ):
        for signum in TRANSACTION_SIGNALS:
            signal.signal(signum, previous_handlers[signum])


@contextlib.contextmanager
def transaction_signal_scope() -> Iterator[None]:
    """Manage HUP/INT/TERM only while reaping a durable-terminal child."""

    previous: dict[signal.Signals, Any] = {}
    try:
        install_transaction_signal_handlers(
            raise_transaction_interrupted, previous
        )
        yield
    finally:
        if previous:
            restore_transaction_signal_handlers(previous)


def wait_for_terminal_child(
    process: subprocess.Popen[bytes], *, timeout: float
) -> None:
    """Reap after terminal publication with bounded managed cancellation."""

    with transaction_signal_scope():
        process.wait(timeout=timeout)


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
    payload_admissions: Sequence[Mapping[str, Any]],
    post_emerge_authority: Mapping[str, Any],
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
    counter_authority = validate_counter_reconciliation_authority(
        paths=paths,
        value=counter,
        expected_outcome=outcome,
        verify_current=True,
    )
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
        "counter": dict(counter_authority),
        "vdb_sha256": sha256_bytes(canonical_json(vdb)),
        "logs": dict(logs),
        "checks": dict(checks),
        "payload_admissions": [dict(row) for row in payload_admissions],
        "post_emerge_authority": dict(post_emerge_authority),
    }
    payload = canonical_json(record)
    if len(payload) > RECOVERY_EVIDENCE_MAX_BYTES:
        fail("child-completion evidence exceeds its reviewed 512 MiB bound")
    digest = atomic_publish_noreplace(paths.child_completion, payload)
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
        "payload_admissions",
        "post_emerge_authority",
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
        or not isinstance(value.get("payload_admissions"), list)
        or not isinstance(value.get("post_emerge_authority"), dict)
    ):
        fail("child-completion authority has an invalid schema or binding")
    require_sha256(value.get("control_session_sha256"), "control session digest")
    require_sha256(value.get("decision_state_sha256"), "decision state digest")
    require_sha256(value.get("vdb_sha256"), "completion VDB digest")
    validate_inline_authority(
        value.get("post_emerge_authority"), "child-completion post-emerge authority"
    )
    source_status = value.get("source_status")
    rollback_status = value.get("rollback_status")
    if type(source_status) is not int or not 0 <= source_status <= 255:
        fail("child-completion source status is invalid")
    if rollback_status is not None and (
        type(rollback_status) is not int or not 0 <= rollback_status <= 255
    ):
        fail("child-completion rollback status is invalid")
    validate_counter_reconciliation_authority(
        paths=paths,
        value=value["counter"],
        expected_outcome=str(value["outcome"]),
        verify_current=False,
    )
    return value


def verify_child_completion_evidence(value: Mapping[str, Any]) -> None:
    logs = value["logs"]
    for path_key, digest_key in (
        ("stdout_path", "stdout_sha256"),
        ("stderr_path", "stderr_sha256"),
    ):
        verify_evidence_reference(logs, path_key, digest_key)
    checks = value["checks"]
    for row in value["payload_admissions"]:
        verify_evidence_reference(row, "path", "sha256")
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
        validate_inline_authority(
            checks.get("payload_authority"),
            "child-completion success payload authority",
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
                set(prepared_vdb(prepared)["cpvs"]),
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
    held_locks: HeldStableLocks,
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
                    canonical_json(prepared_vdb(prepared))
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
            compare_vdb(prepared_vdb(prepared), vdb_manifest(paths.vdb))
            live_counter_value, live_counter_observation = read_counter_authority(
                paths.cache_edb / "counter", "live EDB counter before arming"
            )
            if (
                live_counter_observation != prepared_locked_value(
                    prepared, "counter"
                )
                or live_counter_value
                != prepared_locked_window(prepared).get("counter_value")
            ):
                fail("live EDB counter changed before transaction arming")
            verify_selected_sets(
                paths,
                prepared_locked_value(prepared, "selected_sets"),
                ignored_cache_names=attributable_counter_partial_names(
                    prepared, paths.cache_edb
                ),
            )
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
            held_locks.revalidate()
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
                prepared_vdb(prepared),
                vdb_manifest(paths.vdb),
                prepared["plan"],
            )
            checks: dict[str, Any] | None = None
            postcheck_error: str | None = None
            if status == 0 and delta["exact_success_delta"]:
                try:
                    verify_frozen_authorities(paths, prepared)
                    verify_declared_post_emerge_authority(
                        paths=paths,
                        prepared=prepared,
                        outcome="success",
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
                        "source_action_completed": True,
                        "authenticated_action_complete": True,
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
                "payload_admissions",
                "post_emerge_authority",
            }
            if (
                set(terminal_ready) != required_terminal_keys
                or terminal_ready.get("outcome") != expected_outcome
                or terminal_ready.get("source_status") != status
                or terminal_ready.get("decision_state_sha256") != decision_state_sha
                or not isinstance(terminal_ready.get("counter"), dict)
                or type(terminal_ready.get("stdout_size")) is not int
                or type(terminal_ready.get("stderr_size")) is not int
                or not isinstance(terminal_ready.get("payload_admissions"), list)
                or not isinstance(terminal_ready.get("post_emerge_authority"), dict)
            ):
                fail("held-lock terminal control authority is invalid")
            if expected_outcome == "success":
                if terminal_ready.get("rollback_status") is not None:
                    fail("successful child unexpectedly reported rollback status")
            elif terminal_ready.get("rollback_status") != 0:
                fail("held-lock rollback did not report exact success")
            payload_admissions = terminal_ready["payload_admissions"]
            planned_cpvs = sorted(row["cpv"] for row in prepared["plan"]["rows"])
            admitted_cpvs = sorted(
                str(row.get("cpv", ""))
                for row in payload_admissions
                if isinstance(row, dict)
            )
            if expected_outcome == "success" and admitted_cpvs != planned_cpvs:
                fail("successful child lacks one exact payload admission per planned CPV")
            for row in payload_admissions:
                verify_evidence_reference(row, "path", "sha256")
            if expected_outcome == "success":
                if checks is None:
                    fail("successful source action lacks artifact checks")
                checks = dict(checks)
                checks["payload_authority"] = inline_authority(
                    verify_success_payload_authority(
                        references=payload_admissions,
                        prepared=prepared,
                        prepared_sha256=prepared_sha,
                        control_session_sha256=sha256_bytes(
                            parent_control.session.encode("ascii")
                        ),
                        vdb=paths.vdb,
                    )
                )
            else:
                verify_payload_rollback_authorities(
                    payload_admissions, prepared["plan"]
                )
            child_post_emerge = validate_inline_authority(
                terminal_ready["post_emerge_authority"],
                "held-lock child post-emerge authority",
            )
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
            verify_frozen_authorities(paths, prepared)
            child_durability = child_post_emerge["value"].get(
                "terminal_durability"
            )
            post_emerge_authority = inline_authority(
                verify_declared_post_emerge_authority(
                    paths=paths,
                    prepared=prepared,
                    outcome=expected_outcome,
                    terminal_durability=child_durability,
                )
            )
            if child_post_emerge != post_emerge_authority:
                fail(
                    "held-lock child and coordinator post-emerge authorities differ"
                )
            final_delta = cast(
                dict[str, Any],
                post_emerge_authority["value"]["vdb"],
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
                payload_admissions=payload_admissions,
                post_emerge_authority=post_emerge_authority,
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
                        "post_emerge_authority": post_emerge_authority,
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
                        "post_emerge_authority": post_emerge_authority,
                        "child_completion": completion_ref,
                    },
                )
            held_locks.revalidate()
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
            wait_for_terminal_child(process, timeout=5 * 60)
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


def parsed_internal_command(arguments: argparse.Namespace, label: str) -> list[str]:
    """Validate the post-argparse shape of one REMAINDER command vector."""

    value = getattr(arguments, "command", None)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item or "\0" in item for item in value)
        or value[0] == "--"
        or not Path(value[0]).is_absolute()
    ):
        fail(f"malformed {label} command")
    return list(value)


def pdeath_exec_command(arguments: argparse.Namespace) -> int:
    command = parsed_internal_command(arguments, "parent-death execution")
    install_parent_death_signal(arguments.parent_pid, arguments.parent_start_ticks)
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


def canonical_payload_destination(relative: object, *, cpv: str) -> Path:
    """Map one exact manifest-relative name to a canonical /usr destination."""

    if (
        not isinstance(relative, str)
        or not relative
        or relative.startswith("/")
        or any(character in relative for character in ("\0", "\n", "\r"))
    ):
        fail(f"planned prerequisite payload has an unsafe relative path: {cpv}")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        fail(
            "planned prerequisite payload has a noncanonical relative path: "
            f"{cpv}: {relative}"
        )
    destination = Path("/").joinpath(*parts)
    if destination.as_posix() != "/" + relative:
        fail(
            "planned prerequisite payload path normalization differs: "
            f"{cpv}: {relative}"
        )
    usr = Path("/usr")
    if destination != usr and not destination.is_relative_to(usr):
        fail(
            "planned prerequisite payload escapes the reviewed /usr durability "
            f"root: {cpv}: {destination}"
        )
    return destination


def canonical_payload_destinations(
    rows: Mapping[str, Mapping[str, Any]], *, cpv: str
) -> list[Path]:
    destinations = [
        canonical_payload_destination(relative, cpv=cpv) for relative in rows
    ]
    if len(set(destinations)) != len(destinations):
        fail(f"planned prerequisite payload repeats a canonical destination: {cpv}")
    return sorted(destinations, key=os.fspath)


def payload_observation_paths(destinations: Iterable[Path]) -> list[Path]:
    """Return every destination and ancestor through the exact /usr root."""

    usr = Path("/usr")
    required: set[Path] = set()
    for destination in destinations:
        if destination != usr and not destination.is_relative_to(usr):
            fail(f"payload observation destination escapes /usr: {destination}")
        current = destination
        while True:
            required.add(current)
            if current == usr:
                break
            current = current.parent
            if current == Path("/"):
                fail(f"payload observation chain escaped /usr: {destination}")
    return sorted(required, key=lambda path: (len(path.parts), os.fspath(path)))


def validate_payload_observation_authority(
    *,
    destinations: Sequence[Path],
    observations: object,
    cpv: str,
    expected_payload_device: int | None = None,
) -> tuple[int, dict[str, dict[str, Any]]]:
    """Prove complete, symlink-free destination ancestry on one /usr device."""

    if not isinstance(observations, list):
        fail("payload admission destination observations are not a list")
    required = payload_observation_paths(destinations)
    required_names = {os.fspath(path) for path in required}
    by_path: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if not isinstance(observation, dict) or not isinstance(
            observation.get("path"), str
        ):
            fail("payload admission contains an invalid destination observation")
        raw_path = str(observation["path"])
        path = Path(raw_path)
        if (
            not path.is_absolute()
            or path.as_posix() != raw_path
            or raw_path in by_path
        ):
            fail("payload admission contains a noncanonical destination observation")
        by_path[raw_path] = observation
    if set(by_path) != required_names:
        fail("payload admission lacks its exact destination ancestor closure")
    root = by_path.get("/usr")
    if (
        not isinstance(root, dict)
        or root.get("type") != "directory"
        or type(root.get("device")) is not int
        or int(root["device"]) < 0
    ):
        fail("payload admission lacks an exact /usr directory device authority")
    payload_device = int(root["device"])
    if (
        expected_payload_device is not None
        and payload_device != expected_payload_device
    ):
        fail("payload /usr device differs from prepared authority")
    destination_set = set(destinations)
    for path in required:
        observation = by_path[os.fspath(path)]
        kind = observation.get("type")
        is_ancestor = any(
            destination != path and destination.is_relative_to(path)
            for destination in destination_set
        )
        if kind == "absent":
            if is_ancestor:
                fail(f"payload destination ancestor is absent: {cpv}: {path}")
            if set(observation) != {"path", "type"}:
                fail(f"absent payload destination has foreign authority: {cpv}: {path}")
            continue
        if type(observation.get("device")) is not int:
            fail(f"payload destination lacks device authority: {cpv}: {path}")
        if int(observation["device"]) != payload_device:
            fail(
                "payload destination is on a different device from /usr: "
                f"{cpv}: {path}"
            )
        if is_ancestor and kind != "directory":
            fail(f"payload destination ancestor is not a directory: {cpv}: {path}")
    return payload_device, by_path


def admit_merge_image_payload(
    *,
    mergeroot: Path,
    cpv: str,
    prepared: Mapping[str, Any],
    observed_destinations: dict[str, dict[str, Any]],
    prepared_sha256: str,
    control_session: str,
) -> dict[str, Any]:
    """Inspect the completed image immediately before Portage can touch live ROOT."""

    if cpv not in {str(row["cpv"]) for row in prepared["plan"]["rows"]}:
        fail(f"Portage attempted to merge an object outside the exact plan: {cpv}")
    manifest = tree_manifest(mergeroot)
    manifest_rows = _manifest_rows(manifest, f"{cpv} payload image")
    destinations = canonical_payload_destinations(manifest_rows, cpv=cpv)
    required_observations = payload_observation_paths(destinations)
    for candidate in required_observations:
        observed_destinations[os.fspath(candidate)] = object_observation(candidate)
    record_observations = [
        observed_destinations[os.fspath(candidate)]
        for candidate in required_observations
    ]
    prepared_payload_root = prepared_locked_value(prepared, "payload_root")
    if prepared_payload_root.get("type") != "directory" or type(
        prepared_payload_root.get("device")
    ) is not int:
        fail("prepared payload root lacks exact /usr device authority")
    payload_device, observations_by_path = validate_payload_observation_authority(
        destinations=destinations,
        observations=record_observations,
        cpv=cpv,
        expected_payload_device=int(prepared_payload_root["device"]),
    )
    payload_root_observation = observations_by_path["/usr"]
    if payload_root_observation != prepared_payload_root:
        fail("payload /usr root identity differs from prepared authority")
    loader_roots = {
        Path(str(row["path"]))
        for row in prepared_locked_value(prepared, "loader_directories")["rows"]
    }
    forbidden_prefixes = (
        Path("/etc/env.d"),
        Path("/run"),
        Path("/var/cache/edb"),
        Path("/var/db/pkg"),
        Path("/var/lib/gentoo-optimization"),
        Path("/var/lib/portage"),
    )
    paths: list[str] = []
    for relative, row in manifest_rows.items():
        destination = canonical_payload_destination(relative, cpv=cpv)
        if any(
            destination == prefix or destination.is_relative_to(prefix)
            for prefix in forbidden_prefixes
        ):
            fail(f"planned image targets transaction authority: {cpv}: {destination}")
        if row.get("xattrs") != []:
            fail(f"planned image contains unreviewed extended attributes: {cpv}: {destination}")
        if int(row.get("mode", 0)) & (stat.S_ISUID | stat.S_ISGID):
            fail(f"planned image contains set-id metadata: {cpv}: {destination}")
        if row.get("type") == "file" and row.get("nlink") != 1:
            fail(f"planned image contains a hard-linked regular file: {cpv}: {destination}")
        live_destination = observations_by_path[os.fspath(destination)]
        if live_destination.get("type") != "absent":
            if (
                row.get("type") != "directory"
                or live_destination.get("type") != "directory"
            ):
                fail(
                    f"planned image collides with a pre-existing object: {cpv}: {destination}"
                )
            for key in ("uid", "gid", "mode"):
                if row.get(key) != live_destination.get(key):
                    fail(
                        f"planned directory scaffold metadata differs: {cpv}: {destination}"
                    )
            if live_destination.get("xattrs") != []:
                fail(
                    f"planned directory scaffold has unreviewed xattrs: {cpv}: {destination}"
                )
        if destination in loader_roots or destination.parent in loader_roots:
            if (
                row.get("type") != "directory"
                or live_destination.get("type") != "directory"
            ):
                fail(
                    f"planned image directly targets a loader directory: {cpv}: {destination}"
                )
            for key in ("uid", "gid", "mode"):
                if row.get(key) != live_destination.get(key):
                    fail(
                        f"planned loader scaffold metadata differs: {cpv}: {destination}"
                    )
        paths.append(os.fspath(destination))
    destination_paths = sorted(set(paths))
    record = {
        "schema": "gentoo-optimization-jsonschema-payload-admission-v1",
        "transaction_id": prepared["transaction_id"],
        "prepared_state_sha256": require_sha256(
            prepared_sha256, "payload admission prepared digest"
        ),
        "control_session_sha256": sha256_bytes(control_session.encode("ascii")),
        "cpv": cpv,
        "mergeroot": os.fspath(mergeroot.resolve(strict=True)),
        "manifest": manifest,
        "manifest_sha256": sha256_bytes(canonical_json(manifest)),
        "payload_root_observation": payload_root_observation,
        "payload_device": payload_device,
        "preexisting_destinations": record_observations,
        "preexisting_destinations_sha256": sha256_bytes(
            canonical_json(record_observations)
        ),
        "destination_paths": destination_paths,
        "destination_paths_sha256": sha256_bytes(
            ("\n".join(destination_paths) + "\n").encode()
        ),
    }
    report = Path(str(prepared["evidence"]["directory"]))
    destination = report / (
        "payload-admission-" + sha256_bytes(cpv.encode("utf-8")) + ".json"
    )
    digest = atomic_publish_noreplace(destination, canonical_json(record))
    return {
        "cpv": cpv,
        "path": os.fspath(destination),
        "sha256": digest,
        "manifest_sha256": record["manifest_sha256"],
        "preexisting_destinations_sha256": record[
            "preexisting_destinations_sha256"
        ],
    }


def validate_payload_admission_record(
    *,
    record: object,
    path: Path,
    prepared: Mapping[str, Any],
    prepared_sha256: str,
    control_session_sha256: str,
) -> dict[str, Any]:
    required = {
        "schema",
        "transaction_id",
        "prepared_state_sha256",
        "control_session_sha256",
        "cpv",
        "mergeroot",
        "manifest",
        "manifest_sha256",
        "payload_root_observation",
        "payload_device",
        "preexisting_destinations",
        "preexisting_destinations_sha256",
        "destination_paths",
        "destination_paths_sha256",
    }
    planned = {str(row["cpv"]) for row in prepared["plan"]["rows"]}
    if (
        not isinstance(record, dict)
        or set(record) != required
        or record.get("schema")
        != "gentoo-optimization-jsonschema-payload-admission-v1"
        or record.get("transaction_id") != prepared["transaction_id"]
        or record.get("prepared_state_sha256") != prepared_sha256
        or record.get("control_session_sha256") != control_session_sha256
        or record.get("cpv") not in planned
        or not isinstance(record.get("manifest"), dict)
        or not isinstance(record.get("preexisting_destinations"), list)
        or not isinstance(record.get("destination_paths"), list)
    ):
        fail("durable payload admission has an invalid schema or binding")
    cpv = str(record["cpv"])
    expected_path = path.parent / (
        "payload-admission-" + sha256_bytes(cpv.encode("utf-8")) + ".json"
    )
    if (
        path.parent != Path(str(prepared["evidence"]["directory"]))
        or path != expected_path
    ):
        fail("payload admission path differs from its exact CPV identity")
    manifest = cast(dict[str, Any], record["manifest"])
    rows = _manifest_rows(manifest, "payload image")
    if (
        manifest.get("rows_sha256")
        != sha256_bytes(canonical_json(manifest.get("rows")))
        or record.get("manifest_sha256")
        != sha256_bytes(canonical_json(manifest))
    ):
        fail("payload admission image manifest digest differs")
    destination_paths = canonical_payload_destinations(rows, cpv=cpv)
    destinations = [os.fspath(destination) for destination in destination_paths]
    if (
        record.get("destination_paths") != destinations
        or record.get("destination_paths_sha256")
        != sha256_bytes(("\n".join(destinations) + "\n").encode())
        or record.get("preexisting_destinations_sha256")
        != sha256_bytes(canonical_json(record["preexisting_destinations"]))
    ):
        fail("payload admission destination authority digest differs")
    prepared_payload_root = prepared_locked_value(prepared, "payload_root")
    if type(prepared_payload_root.get("device")) is not int:
        fail("prepared payload root lacks device authority")
    payload_device, observations = validate_payload_observation_authority(
        destinations=destination_paths,
        observations=record["preexisting_destinations"],
        cpv=cpv,
        expected_payload_device=int(prepared_payload_root["device"]),
    )
    if (
        record.get("payload_device") != payload_device
        or record.get("payload_root_observation") != observations["/usr"]
        or record.get("payload_root_observation") != prepared_payload_root
    ):
        fail("payload admission /usr device authority differs")
    return record


def load_payload_admission_references(
    *,
    prepared: Mapping[str, Any],
    prepared_sha256: str,
    control_session: str,
) -> list[dict[str, Any]]:
    report = Path(str(prepared["evidence"]["directory"]))
    rows: list[dict[str, Any]] = []
    for path in sorted(report.glob("payload-admission-*.json"), key=lambda item: item.name):
        record = read_json_regular(path, "durable payload admission")
        record = validate_payload_admission_record(
            record=record,
            path=path,
            prepared=prepared,
            prepared_sha256=prepared_sha256,
            control_session_sha256=sha256_bytes(control_session.encode("ascii")),
        )
        rows.append(
            {
                "cpv": record["cpv"],
                "path": os.fspath(path),
                "sha256": sha256_file(path),
                "manifest_sha256": record.get("manifest_sha256"),
                "preexisting_destinations_sha256": record.get(
                    "preexisting_destinations_sha256"
                ),
            }
        )
    if len({row["cpv"] for row in rows}) != len(rows):
        fail("durable payload admissions repeat one planned CPV")
    return sorted(rows, key=lambda row: row["cpv"])


def load_existing_payload_admission_references(
    *,
    prepared: Mapping[str, Any],
    prepared_sha256: str,
    control_session_sha256: str,
) -> list[dict[str, Any]]:
    require_sha256(control_session_sha256, "payload control-session digest")
    report = Path(str(prepared["evidence"]["directory"]))
    rows: list[dict[str, Any]] = []
    for path in sorted(report.glob("payload-admission-*.json"), key=lambda item: item.name):
        record = read_json_regular(path, "durable payload admission")
        record = validate_payload_admission_record(
            record=record,
            path=path,
            prepared=prepared,
            prepared_sha256=prepared_sha256,
            control_session_sha256=control_session_sha256,
        )
        rows.append(
            {
                "cpv": record["cpv"],
                "path": os.fspath(path),
                "sha256": sha256_file(path),
                "manifest_sha256": record.get("manifest_sha256"),
                "preexisting_destinations_sha256": record.get(
                    "preexisting_destinations_sha256"
                ),
            }
        )
    if len({row["cpv"] for row in rows}) != len(rows):
        fail("existing payload admissions repeat one CPV")
    return sorted(rows, key=lambda row: row["cpv"])


def verify_payload_rollback_authorities(rows: object, plan: Mapping[str, Any]) -> None:
    if not isinstance(rows, list):
        fail("payload-admission authority is not a list")
    seen_cpvs: set[str] = set()
    references: dict[str, Mapping[str, Any]] = {}
    observations_by_path: dict[str, list[dict[str, Any]]] = {}
    for reference in rows:
        if (
            not isinstance(reference, dict)
            or not isinstance(reference.get("cpv"), str)
            or reference["cpv"] in seen_cpvs
        ):
            fail("payload-admission reference is invalid or duplicated")
        seen_cpvs.add(reference["cpv"])
        references[reference["cpv"]] = reference
        verify_evidence_reference(reference, "path", "sha256")
        record = read_json_regular(Path(reference["path"]), "payload admission")
        if (
            not isinstance(record, dict)
            or record.get("schema")
            != "gentoo-optimization-jsonschema-payload-admission-v1"
            or record.get("cpv") != reference["cpv"]
            or record.get("manifest_sha256") != reference.get("manifest_sha256")
            or record.get("preexisting_destinations_sha256")
            != reference.get("preexisting_destinations_sha256")
            or record.get("preexisting_destinations_sha256")
            != sha256_bytes(canonical_json(record.get("preexisting_destinations")))
        ):
            fail("payload-admission record differs from its durable reference")
        manifest_rows = _manifest_rows(
            record.get("manifest"), "rolled-back payload image"
        )
        destinations = canonical_payload_destinations(
            manifest_rows, cpv=str(reference["cpv"])
        )
        payload_device, observed = validate_payload_observation_authority(
            destinations=destinations,
            observations=record.get("preexisting_destinations"),
            cpv=str(reference["cpv"]),
            expected_payload_device=(
                int(record["payload_device"])
                if type(record.get("payload_device")) is int
                else None
            ),
        )
        if (
            record.get("payload_device") != payload_device
            or record.get("payload_root_observation") != observed["/usr"]
        ):
            fail("rolled-back payload /usr device authority differs")
        for observation in record["preexisting_destinations"]:
            if not isinstance(observation, dict) or not isinstance(
                observation.get("path"), str
            ):
                fail("payload-admission destination observation is invalid")
            observations_by_path.setdefault(observation["path"], []).append(
                observation
            )

    planned = {atom[1:].split("::", 1)[0] for atom in exact_plan_atoms(plan)}
    if not seen_cpvs <= planned:
        fail("payload admission names a package outside the reviewed plan")

    # MergeProcess is forked by Portage.  Receipt publication is durable, but
    # an in-memory list cannot establish an ordering across those children.
    # Therefore never guess which of two differing non-absent observations was
    # the original.  An absent observation is unambiguous (the path did not
    # exist before at least one package created it); otherwise all observations
    # must agree exactly before a rolled-back claim is possible.
    original_observations: dict[str, dict[str, Any]] = {}
    for path, candidates in observations_by_path.items():
        absent = [row for row in candidates if row.get("type") == "absent"]
        if absent:
            original_observations[path] = absent[0]
            continue
        first = candidates[0]
        comparable_first = (
            _stable_directory_observation(first)
            if first.get("type") == "directory"
            else first
        )
        if any(
            (
                _stable_directory_observation(candidate)
                if candidate.get("type") == "directory"
                else candidate
            )
            != comparable_first
            for candidate in candidates[1:]
        ):
            fail(
                "payload admissions contain ambiguous pre-merge authority: " + path
            )
        original_observations[path] = first

    for path, observation in original_observations.items():
        current = object_observation(Path(path))
        matches = (
            _stable_directory_observation(current)
            == _stable_directory_observation(observation)
            if observation.get("type") == "directory"
            else current == observation
        )
        if not matches:
            fail(
                "rolled-back payload destination differs from pre-merge authority: "
                + path
            )


def _stable_directory_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in ("path", "type", "device", "inode", "uid", "gid", "mode", "xattrs")
    }


def verify_success_payload_authority(
    *,
    references: object,
    prepared: Mapping[str, Any],
    prepared_sha256: str,
    control_session_sha256: str,
    vdb: Path,
) -> dict[str, Any]:
    """Prove installed objects against the exact fork-child image receipts."""

    if not isinstance(references, list):
        fail("successful payload authority is not a reference vector")
    planned = {str(row["cpv"]) for row in prepared["plan"]["rows"]}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reference in references:
        if not isinstance(reference, dict):
            fail("successful payload reference is not an object")
        verify_evidence_reference(reference, "path", "sha256")
        path = Path(str(reference["path"]))
        record = validate_payload_admission_record(
            record=read_json_regular(path, "successful payload admission"),
            path=path,
            prepared=prepared,
            prepared_sha256=prepared_sha256,
            control_session_sha256=control_session_sha256,
        )
        cpv = str(record["cpv"])
        if cpv in seen:
            fail("successful payload admission repeats one planned CPV")
        seen.add(cpv)
        records.append(record)
    if seen != planned:
        fail("successful payload admissions do not equal the exact plan")
    payload_devices = {record.get("payload_device") for record in records}
    payload_roots = {
        sha256_bytes(canonical_json(record.get("payload_root_observation")))
        for record in records
    }
    if (
        len(payload_devices) != 1
        or len(payload_roots) != 1
        or type(next(iter(payload_devices), None)) is not int
    ):
        fail("successful payload admissions disagree on /usr device authority")
    payload_device = int(next(iter(payload_devices)))
    recorded_payload_root = records[0]["payload_root_observation"]
    current_payload_root = object_observation(Path("/usr"))
    if (
        current_payload_root != recorded_payload_root
        or current_payload_root.get("type") != "directory"
        or current_payload_root.get("device") != payload_device
    ):
        fail("current /usr root differs from admitted payload device authority")

    image_rows: dict[str, dict[str, Any]] = {}
    original_rows: dict[str, list[dict[str, Any]]] = {}
    per_cpv_paths: dict[str, list[str]] = {}
    for record in records:
        cpv = str(record["cpv"])
        per_cpv_paths[cpv] = list(record["destination_paths"])
        for relative, row in _manifest_rows(
            record["manifest"], f"{cpv} admitted image"
        ).items():
            destination = "/" + relative
            existing = image_rows.get(destination)
            if existing is not None:
                if row.get("type") != "directory" or existing.get("type") != "directory":
                    fail("planned package images overlap a non-directory object: " + destination)
                for key in ("type", "uid", "gid", "mode", "xattrs"):
                    if row.get(key) != existing.get(key):
                        fail("planned package images disagree on directory metadata: " + destination)
            else:
                image_rows[destination] = row
        for observation in record["preexisting_destinations"]:
            original_rows.setdefault(str(observation["path"]), []).append(observation)

    observations: dict[str, dict[str, Any]] = {}
    for path, candidates in original_rows.items():
        absent = [candidate for candidate in candidates if candidate.get("type") == "absent"]
        if absent:
            observations[path] = absent[0]
            continue
        stable = _stable_directory_observation(candidates[0])
        if any(
            _stable_directory_observation(candidate) != stable
            for candidate in candidates[1:]
        ):
            fail("successful payload receipts disagree on pre-existing authority: " + path)
        observations[path] = candidates[0]

    installed_rows: list[dict[str, Any]] = []
    for destination, expected in sorted(image_rows.items()):
        current = object_observation(Path(destination))
        expected_type = expected.get("type")
        if current.get("type") != expected_type:
            fail("installed payload object type differs: " + destination)
        if current.get("device") != payload_device:
            fail("installed payload object device differs: " + destination)
        for key in ("uid", "gid", "mode", "xattrs"):
            if current.get(key) != expected.get(key):
                fail(f"installed payload {key} differs: {destination}")
        if expected_type == "file":
            for key in ("size", "sha256", "nlink"):
                if current.get(key) != expected.get(key):
                    fail(f"installed payload {key} differs: {destination}")
        elif expected_type == "symlink":
            for key in ("target", "nlink"):
                if current.get(key) != expected.get(key):
                    fail(f"installed payload {key} differs: {destination}")
        elif expected_type == "directory":
            original = observations.get(destination)
            if original is None:
                fail("payload admission lacks its directory observation: " + destination)
            if original.get("type") == "directory" and _stable_directory_observation(
                current
            ) != _stable_directory_observation(original):
                fail("pre-existing payload directory authority changed: " + destination)
        else:
            fail("payload image contains an unsupported installed object type")
        installed_rows.append(
            {
                "path": destination,
                "observation_sha256": sha256_bytes(canonical_json(current)),
            }
        )

    contents = _contents_paths(vdb, planned, include_parents=False)
    image_paths = set(image_rows)
    non_directories = {
        path for path, row in image_rows.items() if row.get("type") != "directory"
    }
    if not non_directories <= contents or not contents <= image_paths:
        fail(
            "installed VDB CONTENTS differs from admitted package images: "
            f"missing_objects={sorted(non_directories - contents)}, "
            f"foreign_contents={sorted(contents - image_paths)}"
        )
    for record in records:
        cpv = str(record["cpv"])
        package_rows = {
            "/" + relative: row
            for relative, row in _manifest_rows(
                record["manifest"], f"{cpv} admitted image"
            ).items()
        }
        package_contents = _contents_paths(vdb, [cpv], include_parents=False)
        package_objects = {
            path
            for path, row in package_rows.items()
            if row.get("type") != "directory"
        }
        if not package_objects <= package_contents or not package_contents <= set(
            package_rows
        ):
            fail(f"installed CONTENTS differs from the admitted image for {cpv}")
    result = {
        "schema_version": 1,
        "cpvs": sorted(planned),
        "payload_device": payload_device,
        "payload_root_sha256": sha256_bytes(
            canonical_json(recorded_payload_root)
        ),
        "per_cpv_paths": {key: per_cpv_paths[key] for key in sorted(per_cpv_paths)},
        "installed_rows": installed_rows,
        "contents_paths": sorted(contents),
    }
    result["rows_sha256"] = sha256_bytes(canonical_json(result))
    return result


def portage_action_command(arguments: argparse.Namespace) -> int:
    """Hold one Portage config/vardb lock through exact action or rollback."""

    if not LIVE_MUTATION_ENABLED:
        fail(
            "internal Portage mutation is disabled pending the final Candidate-A "
            "invariant audit and authoritative Gentoo-host capability proofs"
        )
    command = parsed_internal_command(arguments, "internal Portage action")
    if arguments.control_fd < 0 or not CONTROL_SESSION_PATTERN.fullmatch(
        arguments.control_session
    ):
        fail("internal Portage control-channel identity is invalid")
    endpoint = socket.socket(fileno=arguments.control_fd)
    channel = ControlChannel(endpoint, arguments.control_session)
    prepared = validate_state(
        read_json_regular(
            arguments.prepared_state,
            "prepared state for Portage action",
            PHASE_STATE_MAX_BYTES,
        )
    )
    if (
        prepared["phase"] != "prepared"
        or sha256_file(arguments.prepared_state) != arguments.prepared_sha256
    ):
        fail("internal Portage action is not bound to the exact prepared state")
    prepared_tools = tools_from_manifest(prepared["authority"]["tools"])
    if command[0] != os.fspath(prepared_tools["emerge"]):
        fail("internal Portage action has an unexpected command identity")
    emerge_arguments = command[1:]
    expected_atoms = exact_plan_atoms(prepared["plan"])
    expected_arguments = [*emerge_options(), "--ask=y", *expected_atoms]
    if emerge_arguments != expected_arguments:
        fail("internal Portage argv differs byte-for-byte from the reviewed action")
    try:
        import _emerge.actions as actions
        from _emerge.main import parse_opts
        import portage.dbapi.vartree as vartree_module
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
    policy = effective_portage_policy(settings)
    expected_policy = prepared["resolver"]["final_locked_window"].get(
        "effective_portage_policy"
    )
    if policy != expected_policy:
        fail("effective Portage action policy differs from prepared authority")
    if native_toolchain_authority(settings) != prepared["resolver"][
        "final_locked_window"
    ].get("native_toolchain"):
        fail("effective native action toolchain differs from prepared authority")
    validate_frozen_portage_policy(prepared["authority"])
    vardb = config.trees[target]["vartree"].dbapi
    if config.target_config.trees["vartree"].dbapi is not vardb:
        fail("Portage target configuration does not share the selected vardb object")
    original_loader = actions.load_emerge_config
    original_merge = vartree_module.dblink.merge
    payload_admissions: list[dict[str, Any]] = []

    def reject_reload(*_args: object, **_kwargs: object) -> NoReturn:
        fail("Portage attempted to reload configuration after VDB lock acquisition")

    def guarded_merge(link: Any, mergeroot: str, *args: Any, **kwargs: Any) -> Any:
        cpv = str(link.mycpv)
        # This callback runs in Portage's forked MergeProcess.  The receipt is
        # the authority; mutating a parent-process Python list here would be a
        # false synchronization primitive.
        admit_merge_image_payload(
            mergeroot=Path(mergeroot),
            cpv=cpv,
            prepared=prepared,
            observed_destinations={},
            prepared_sha256=arguments.prepared_sha256,
            control_session=arguments.control_session,
        )
        return original_merge(link, mergeroot, *args, **kwargs)

    source_status = 125
    rollback_status: int | None = None
    vardb.lock()
    try:
        require_locked_empty_preserved_registry(vardb)
        compare_vdb(prepared_vdb(prepared), vdb_manifest(vdb_path))
        observed_metadata = plan_metadata_authority(
            locked=LockedPortageAuthority(
                config,
                vardb,
                vardb._plib_registry,
                target,
                vdb_path,
                cast(
                    Mapping[str, str],
                    prepared_locked_window(prepared)["portage_lock_api"],
                ),
            ),
            plan=prepared["plan"],
            repositories=prepared["authority"]["repositories"],
        )
        if observed_metadata != prepared["resolver"]["plan_metadata"]:
            fail("Portage action package metadata differs from prepared authority")
        actions.load_emerge_config = reject_reload
        vartree_module.dblink.merge = guarded_merge
        channel.send(
            "LOCK_HELD",
            {
                "prepared_state_sha256": arguments.prepared_sha256,
                "emerge_arguments_sha256": sha256_bytes(canonical_json(emerge_arguments)),
                "vdb_sha256": sha256_bytes(
                    canonical_json(prepared_vdb(prepared))
                ),
                "vardb_root": os.fspath(vdb_path),
            },
        )
        source_status = int(actions.run_action(config))
        if config.trees[target]["vartree"].dbapi is not vardb:
            fail("Portage replaced the locked vardb object during the source action")
        require_locked_empty_preserved_registry(vardb)
        payload_admissions = load_payload_admission_references(
            prepared=prepared,
            prepared_sha256=arguments.prepared_sha256,
            control_session=arguments.control_session,
        )
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
                prepared_vdb(prepared), vdb_manifest(vdb_path), prepared["plan"]
            )
            if not delta["exact_success_delta"]:
                fail("coordinator attempted to commit a non-exact VDB delta")
            terminal_outcome = "success"
        elif requested == "rolled-back":
            delta = classify_vdb_delta(
                prepared_vdb(prepared), vdb_manifest(vdb_path), prepared["plan"]
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
            compare_vdb(prepared_vdb(prepared), vdb_manifest(vdb_path))
            require_locked_empty_preserved_registry(vardb)
            verify_payload_rollback_authorities(payload_admissions, prepared["plan"])
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
        if terminal_outcome == "rolled-back":
            restore_loader_directory_times(
                prepared_locked_value(prepared, "loader_directories")
            )
        terminal_paths = Paths(prepared["transaction_id"])
        durability = perform_terminal_durability_barrier(
            paths=terminal_paths,
            prepared=prepared,
            tools=tools,
            runner=SubprocessRunner(),
        )
        post_emerge_authority = inline_authority(
            verify_declared_post_emerge_authority(
                paths=terminal_paths,
                prepared=prepared,
                outcome=terminal_outcome,
                verify_live_views=False,
                terminal_durability=durability,
            )
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
                "payload_admissions": payload_admissions,
                "post_emerge_authority": post_emerge_authority,
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
        vartree_module.dblink.merge = original_merge
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
            "internal Portage recovery is disabled pending the final Candidate-A "
            "invariant audit and authoritative Gentoo-host capability proofs"
        )
    if arguments.control_fd < 0 or not CONTROL_SESSION_PATTERN.fullmatch(
        arguments.control_session
    ):
        fail("internal recovery control-channel identity is invalid")
    endpoint = socket.socket(fileno=arguments.control_fd)
    channel = ControlChannel(endpoint, arguments.control_session)
    prepared = validate_state(
        read_json_regular(
            arguments.prepared_state,
            "prepared state for Portage recovery",
            PHASE_STATE_MAX_BYTES,
        )
    )
    rollback_state = validate_state(
        read_json_regular(
            arguments.rollback_state,
            "rollback state for Portage recovery",
            PHASE_STATE_MAX_BYTES,
        )
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
    policy = effective_portage_policy(settings)
    if policy != prepared["resolver"]["final_locked_window"].get(
        "effective_portage_policy"
    ):
        fail("effective Portage recovery policy differs from prepared authority")
    if native_toolchain_authority(settings) != prepared["resolver"][
        "final_locked_window"
    ].get("native_toolchain"):
        fail("effective native recovery toolchain differs from prepared authority")
    validate_frozen_portage_policy(prepared["authority"])
    vardb = config.trees[target]["vartree"].dbapi
    if config.target_config.trees["vartree"].dbapi is not vardb:
        fail("Portage recovery target does not share the selected vardb object")
    original_loader = actions.load_emerge_config

    def reject_reload(*_args: object, **_kwargs: object) -> NoReturn:
        fail("Portage attempted to reload configuration during held-lock recovery")

    vardb.lock()
    try:
        require_locked_empty_preserved_registry(vardb)
        actions.load_emerge_config = reject_reload
        delta = classify_vdb_delta(
            prepared_vdb(prepared), vdb_manifest(vdb_path), prepared["plan"]
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
        require_locked_empty_preserved_registry(vardb)
        original_child = rollback_state.get("child")
        if not isinstance(original_child, dict) or not isinstance(
            original_child.get("control_session_sha256"), str
        ):
            fail("held-lock recovery lacks original control-session authority")
        payload_admissions = load_existing_payload_admission_references(
            prepared=prepared,
            prepared_sha256=arguments.prepared_sha256,
            control_session_sha256=original_child["control_session_sha256"],
        )
        verify_payload_rollback_authorities(payload_admissions, prepared["plan"])
        compare_vdb(prepared_vdb(prepared), vdb_manifest(vdb_path))
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
        restore_loader_directory_times(
            prepared_locked_value(prepared, "loader_directories")
        )
        terminal_paths = Paths(prepared["transaction_id"])
        durability = perform_terminal_durability_barrier(
            paths=terminal_paths,
            prepared=prepared,
            tools=tools,
            runner=SubprocessRunner(),
        )
        post_emerge_authority = inline_authority(
            verify_declared_post_emerge_authority(
                paths=terminal_paths,
                prepared=prepared,
                outcome="rolled-back",
                verify_live_views=False,
                terminal_durability=durability,
            )
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
                "payload_admissions": payload_admissions,
                "post_emerge_authority": post_emerge_authority,
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
            "Build and verify the reviewed Phase-2 jsonschema prerequisite transaction "
            "contract; live actions remain subject to exact runbook authority"
        )
    )
    parser.add_argument("--fixture-root", type=Path, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare = subparsers.add_parser(
        "prepare", help="prepare the exact runbook-authorized live prerequisite transaction"
    )
    prepare.add_argument("transaction_id")
    prepare.add_argument("--target", default="dev-python/jsonschema")
    prepare.add_argument("--pre-checkpoint-state", required=True, type=Path)
    run = subparsers.add_parser(
        "run", help="run an exact durable prepared prerequisite transaction"
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
    prepared_locked_window(prepared)
    final_window = prepared["resolver"].get("final_locked_window")
    locked_reference = prepared["resolver"].get("locked_authority")
    if (
        not isinstance(final_window, dict)
        or not isinstance(locked_reference, dict)
        or final_window.get("locked_authority_sha256")
        != locked_reference.get("sha256")
        or final_window.get("plan_metadata_sha256")
        != sha256_bytes(canonical_json(prepared["resolver"].get("plan_metadata")))
    ):
        fail("final locked window differs from external authority references")
    validate_frozen_portage_policy(prepared["authority"])
    revalidate_tool_manifest(prepared["authority"]["tools"])
    revalidate_build_tool_versions(prepared["authority"].get("build_tool_versions"))
    revalidate_native_toolchain(
        final_window.get("native_toolchain")
    )
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
    verify_plan_metadata_authority(prepared)
    if build_execution_scope(
        prepared["resolver"]["plan_metadata"],
        tools_from_manifest(prepared["authority"]["tools"]),
    ) != prepared["authority"].get("build_execution_scope"):
        fail("derived build-execution scope changed")
    attempt = prepared["authority"].get("preparation_attempt")
    verify_evidence_reference(attempt, "path", "sha256")


def verify_terminal_post_emerge_binding(
    *,
    paths: Paths,
    state: Mapping[str, Any],
    prepared: Mapping[str, Any],
    outcome: str,
) -> dict[str, Any]:
    state_outcome = state.get("outcome")
    if not isinstance(state_outcome, dict):
        fail("terminal state lacks its outcome authority")
    recorded = validate_inline_authority(
        state_outcome.get("post_emerge_authority"),
        "terminal post-emerge authority",
    )
    terminal_durability = recorded["value"].get("terminal_durability")
    if terminal_durability is None:
        fail("terminal post-emerge authority lacks its durability barrier")
    current = inline_authority(
        verify_declared_post_emerge_authority(
            paths=paths,
            prepared=prepared,
            outcome=outcome,
            terminal_durability=terminal_durability,
        )
    )
    if recorded != current:
        fail("terminal post-emerge authority differs from current state")
    completion_ref = state_outcome.get("child_completion")
    verify_evidence_reference(completion_ref, "path", "sha256")
    completion = read_json_regular(
        Path(str(completion_ref["path"])),
        "terminal child completion",
        RECOVERY_EVIDENCE_MAX_BYTES,
    )
    if (
        not isinstance(completion, dict)
        or completion.get("outcome") != outcome
        or completion.get("post_emerge_authority") != recorded
    ):
        fail("terminal child completion differs from post-emerge authority")
    validate_counter_reconciliation_authority(
        paths=paths,
        value=completion.get("counter"),
        expected_outcome=outcome,
        verify_current=True,
    )
    payload_references = completion.get("payload_admissions")
    if outcome == "success":
        payload_authority = inline_authority(
            verify_success_payload_authority(
                references=payload_references,
                prepared=prepared,
                prepared_sha256=sha256_bytes(canonical_json(prepared)),
                control_session_sha256=require_sha256(
                    completion.get("control_session_sha256"),
                    "terminal completion control-session digest",
                ),
                vdb=paths.vdb,
            )
        )
        checks = completion.get("checks")
        if (
            not isinstance(checks, dict)
            or checks.get("payload_authority") != payload_authority
        ):
            fail("terminal payload authority differs from installed state")
    else:
        verify_payload_rollback_authorities(payload_references, prepared["plan"])
    return cast(dict[str, Any], current["value"])


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
        compare_vdb(prepared_vdb(prepared), vdb_manifest(paths.vdb))
        live_counter_value, live_counter_observation = read_counter_authority(
            paths.cache_edb / "counter", "live EDB counter after preparation"
        )
        if (
            live_counter_observation != prepared_locked_value(prepared, "counter")
            or live_counter_value
            != prepared_locked_window(prepared).get("counter_value")
        ):
            fail("live EDB counter changed after transaction preparation")
        verify_selected_sets(
            paths, prepared_locked_value(prepared, "selected_sets")
        )
    elif phase in {"rolled-back", "success"}:
        verify_terminal_post_emerge_binding(
            paths=paths,
            state=state,
            prepared=prepared,
            outcome=phase,
        )
    elif phase == "recovery-failed":
        verify_recovery_failed_state(paths, state, prepared_sha)
    return 1 if phase == "recovery-failed" else 0


def verify_entrypoint(paths: Paths) -> int:
    paths.validate()
    with transaction_locks(paths) as held_locks:
        held_locks.revalidate()
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
    source_before = file_observation(source)
    copy_tree(source, destination, runner, tools)
    source_after = file_observation(source)
    if source_before != source_after:
        fail(f"{label} changed during authority materialization")
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
        "source_before": source_before,
        "source_after": source_after,
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


def validate_final_locked_window(
    *,
    initial_locked_window: Mapping[str, Any],
    final_locked_window: Mapping[str, Any],
    locked_authority: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the second held-lock observation to the first immutable snapshot."""

    for key in (
        "vdb",
        "selected_sets",
        "mtimedb",
        "counter",
        "live_etc",
        "payload_root",
    ):
        if final_locked_window.get(key) != initial_locked_window.get(key):
            fail(f"final locked {key} authority differs from initial preparation")
    for key, label in (
        ("portage_lock_api", "Portage nonblocking-lock API"),
        ("loader_directories", "loader-directory"),
        ("effective_portage_policy", "effective Portage policy"),
        ("native_toolchain", "native build-tool authority"),
    ):
        if final_locked_window.get(key) != initial_locked_window.get(key):
            fail(f"{label} changed between locked windows")
    plan_metadata = final_locked_window.get("plan_metadata")
    if not isinstance(plan_metadata, dict):
        fail("final locked window lacks exact package metadata admission")
    locked_digest = require_sha256(
        locked_authority.get("sha256"), "locked-authority artifact digest"
    )
    return {
        "schema_version": 1,
        "locked_authority_sha256": locked_digest,
        "effective_portage_policy": final_locked_window[
            "effective_portage_policy"
        ],
        "native_toolchain": final_locked_window["native_toolchain"],
        "plan_metadata_sha256": sha256_bytes(canonical_json(plan_metadata)),
    }


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
            "live jsonschema preparation is disabled pending the final Candidate-A "
            "invariant audit and authoritative Gentoo-host capability proofs"
        )
    prepare_directories(paths, paths.fixture_mode)
    with transaction_locks(paths) as held_locks:
        held_locks.revalidate()
        reconciled = reconcile_state_chain(paths)
        if reconciled is not None:
            fail(f"transaction already has durable phase {reconciled[0]['phase']}")
        existing = transaction_attempt_objects(paths)
        if existing:
            fail(
                "transaction ID was already used or has an abandoned preparation: "
                + ", ".join(existing)
            )
        checkpoint = validate_pre_checkpoint(arguments.pre_checkpoint_state, enforce_root_trust=True)
        repository_specs = discover_repositories()
        capacity = capacity_preflight(paths=paths, repositories=repository_specs)
        publish_preparation_attempt(
            paths, capacity=capacity, checkpoint=checkpoint
        )
        held_locks.revalidate()
        paths.report.mkdir(mode=0o700)
        paths.authority.mkdir(mode=0o700)
        repositories_root = paths.authority / "repositories"
        repositories_root.mkdir(mode=0o755)
        tools = default_tools(paths.root if paths.fixture_mode else Path("/"))
        tools["transaction"] = Path(__file__).resolve(strict=True)
        tools_value = tool_manifest(tools)
        runner: Runner = SubprocessRunner()
        build_tool_versions = build_tool_version_authority(tools, runner)
        python_modules = [
            python_module_authority("_emerge"),
            python_module_authority("gemato"),
            python_module_authority("flit_core"),
            python_module_authority("gpep517"),
            python_module_authority("hatchling"),
            python_module_authority("installer"),
            python_module_authority("maturin"),
            external_python_module_authority(
                "mesonbuild", tools["meson_python"]
            ),
            python_module_authority("packaging"),
            python_module_authority("portage"),
            python_module_authority("pyproject_hooks"),
            python_module_authority("scikit_build_core"),
            python_module_authority("setuptools"),
            python_module_authority("wheel"),
        ]
        repositories: list[dict[str, Any]] = []
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
        initial_locked_window = preparation_locked_snapshot(
            paths=paths,
            private_roots=private_roots,
            runner=runner,
            tools=tools,
        )
        external_locked_window = dict(initial_locked_window)
        # The locked /etc copy row already carries the one complete tree plus
        # exact live/copy root identities.  Do not embed the same tree again.
        external_locked_window.pop("live_etc", None)
        locked_authority = publish_locked_authority(
            paths, external_locked_window
        )
        vdb_before = initial_locked_window["vdb"]
        authority: dict[str, Any] = {
            "tools": tools_value,
            "python_modules": python_modules,
            "repositories": repositories,
            "portage_config": config,
            "portage_global_config": global_config,
            "pre_dependency_checkpoint": checkpoint,
            "framework": framework_authority(paths),
            "capacity_preflight": capacity,
            "build_tool_versions": build_tool_versions,
            "preparation_attempt": {
                "path": os.fspath(paths.preparation_attempt),
                "sha256": sha256_file(paths.preparation_attempt),
            },
        }
        validate_frozen_portage_policy(authority)
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
        portage_build_identity = {
            "uid": os.geteuid()
            if paths.fixture_mode
            else pwd.getpwnam("portage").pw_uid,
            "gid": os.getegid()
            if paths.fixture_mode
            else grp.getgrnam("portage").gr_gid,
        }
        resolver_base = {
            "target": arguments.target,
            "frozen_repository_observation": frozen_repositories,
            "locked_authority": locked_authority,
            "portage_build_identity": portage_build_identity,
            "initial_pretend": initial_evidence,
            "exact_repretend_before_prefetch": exact_evidence,
            "prefetch": prefetch_evidence,
            "offline_exact_repretend": offline_evidence,
            "private_roots_before": private_roots_baseline(private_roots),
            "private_portage_outputs_before": private_portage_outputs(private_roots),
        }

        def publish_prepared(final_locked_window: dict[str, Any]) -> None:
            compact_final_window = validate_final_locked_window(
                initial_locked_window=initial_locked_window,
                final_locked_window=final_locked_window,
                locked_authority=locked_authority,
            )
            authority["build_execution_scope"] = build_execution_scope(
                final_locked_window["plan_metadata"], tools
            )
            resolver = {
                **resolver_base,
                "final_locked_window": compact_final_window,
                "plan_metadata": final_locked_window["plan_metadata"],
            }
            prepared = base_state(
                paths,
                authority=authority,
                resolver=resolver,
                plan=initial_plan,
                private_roots=private_roots,
            )
            verify_frozen_authorities(paths, prepared)
            publish_state(paths, prepared)

        preparation_locked_snapshot(
            paths=paths,
            private_roots=None,
            runner=None,
            tools=None,
            plan=initial_plan,
            repositories=repositories,
            publisher=publish_prepared,
        )
        held_locks.revalidate()
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
    held_locks: HeldStableLocks,
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
                prepared_vdb(prepared), current_vdb, prepared["plan"]
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
            verify_selected_sets(
                paths,
                prepared_locked_value(prepared, "selected_sets"),
                ignored_cache_names=attributable_counter_partial_names(
                    prepared, paths.cache_edb
                ),
            )
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
                    "payload_admissions",
                    "post_emerge_authority",
                }
                or terminal_ready.get("outcome") != "rolled-back"
                or terminal_ready.get("rollback_status") != 0
                or terminal_ready.get("decision_state_sha256") != rollback_sha
                or not isinstance(terminal_ready.get("counter"), dict)
                or type(terminal_ready.get("stdout_size")) is not int
                or type(terminal_ready.get("stderr_size")) is not int
                or not isinstance(terminal_ready.get("payload_admissions"), list)
                or not isinstance(terminal_ready.get("post_emerge_authority"), dict)
            ):
                fail("recovery terminal control authority is invalid")
            current_vdb = vdb_manifest(paths.vdb)
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
            post_emerge_authority = inline_authority(
                verify_declared_post_emerge_authority(
                    paths=paths,
                    prepared=prepared,
                    outcome="rolled-back",
                    terminal_durability=validate_inline_authority(
                        terminal_ready["post_emerge_authority"],
                        "recovery child post-emerge authority",
                    )["value"].get("terminal_durability"),
                )
            )
            if validate_inline_authority(
                terminal_ready["post_emerge_authority"],
                "recovery child post-emerge authority",
            ) != post_emerge_authority:
                fail("recovery child and coordinator post-emerge authorities differ")
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
                payload_admissions=terminal_ready["payload_admissions"],
                post_emerge_authority=post_emerge_authority,
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
                    "post_emerge_authority": post_emerge_authority,
                    "child_completion": completion_ref,
                },
            )
            held_locks.revalidate()
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
            wait_for_terminal_child(process, timeout=5 * 60)
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
            "live jsonschema mutation is disabled pending the final Candidate-A "
            "invariant audit and authoritative Gentoo-host capability proofs"
        )
    with transaction_locks(paths) as held_locks:
        held_locks.revalidate()
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
        compare_vdb(prepared_vdb(prepared), vdb_manifest(paths.vdb))
        verify_selected_sets(
            paths, prepared_locked_value(prepared, "selected_sets")
        )
        _result, _source_evidence, terminal, _terminal_sha = run_armed_source_child(
            paths=paths,
            prepared=prepared,
            prepared_sha=prepared_sha,
            environment=plan_environment(prepared["private_roots"], offline=True),
            tools=tools,
            runner=runner,
            timeout=12 * 3600,
            held_locks=held_locks,
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
        read_json_regular(
            paths.child_completion,
            "child-completion authority",
            RECOVERY_EVIDENCE_MAX_BYTES,
        ),
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
    validate_counter_reconciliation_authority(
        paths=paths,
        value=completion["counter"],
        expected_outcome=expected_outcome,
        verify_current=True,
    )
    verify_frozen_authorities(paths, prepared)
    post_emerge_authority = inline_authority(
        verify_declared_post_emerge_authority(
            paths=paths,
            prepared=prepared,
            outcome=expected_outcome,
            terminal_durability=validate_inline_authority(
                completion["post_emerge_authority"],
                "child-completion post-emerge authority",
            )["value"].get("terminal_durability"),
        )
    )
    if completion["post_emerge_authority"] != post_emerge_authority:
        fail("child-completion post-emerge authority differs from live state")
    if expected_outcome == "success":
        payload_authority = inline_authority(
            verify_success_payload_authority(
                references=completion["payload_admissions"],
                prepared=prepared,
                prepared_sha256=prepared_sha,
                control_session_sha256=completion["control_session_sha256"],
                vdb=paths.vdb,
            )
        )
        if completion["checks"].get("payload_authority") != payload_authority:
            fail("child-completion payload authority differs from installed state")
    else:
        verify_payload_rollback_authorities(
            completion["payload_admissions"], prepared["plan"]
        )
    delta = cast(dict[str, Any], post_emerge_authority["value"]["vdb"])
    phase = expected_outcome
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
            "post_emerge_authority": post_emerge_authority,
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
            "live jsonschema recovery is disabled pending the final Candidate-A "
            "invariant audit and authoritative Gentoo-host capability proofs"
        )
    with transaction_locks(paths) as held_locks:
        held_locks.revalidate()
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
            return verify_command(paths)
        if state["phase"] in TERMINAL_PHASES:
            return verify_command(paths)
        prepared, prepared_sha = load_phase_state(paths, "prepared")
        prepared_locked_window(prepared)
        armed, armed_sha = load_phase_state(paths, "armed")
        tools = tools_from_manifest(prepared["authority"]["tools"])
        if path_exists(paths.child_completion):
            try:
                return finalize_from_child_completion(
                    paths=paths,
                    state=state,
                    state_sha=state_sha,
                    prepared=prepared,
                    prepared_sha=prepared_sha,
                    armed_sha=armed_sha,
                )
            except TransactionInterrupted:
                raise
            except TransactionError as error:
                child = state.get("child")
                if isinstance(child, dict):
                    quiesce_recorded_child(child, paths.proc_root)
                if state["phase"] == "armed":
                    rollback = next_state(
                        state,
                        state_sha,
                        "rollback-in-progress",
                        child=child,
                        outcome={
                            "recovered": True,
                            "source_status": 125,
                            "source_action_completed": False,
                            "authenticated_action_complete": False,
                            "completion_error": str(error),
                        },
                    )
                    _path, rollback_sha = publish_state(paths, rollback)
                    state, state_sha = rollback, rollback_sha
                publish_recovery_failed(
                    paths=paths,
                    rollback_state=state,
                    rollback_sha=state_sha,
                    prepared=prepared,
                    prepared_sha=prepared_sha,
                    reason="authenticated child completion could not be finalized: "
                    + str(error),
                )
                return 1
        if state["phase"] == "armed":
            child = state.get("child")
            if not isinstance(child, dict):
                fail("armed transaction has no exact child identity")
            quiesce_recorded_child(child, paths.proc_root)
            # Portage can copy payload into ROOT before renaming its
            # ``-MERGING-*`` VDB directory.  Without an authenticated terminal
            # completion, a CPV-set comparison cannot distinguish that state
            # from a clean pre-merge VDB.  Never guess or rerun source emerge.
            rollback = next_state(
                state,
                state_sha,
                "rollback-in-progress",
                child=state.get("child"),
                outcome={
                    "recovered": True,
                    "source_status": 125,
                    "source_action_completed": False,
                    "authenticated_action_complete": False,
                    "reason": "armed child died without authenticated completion",
                },
            )
            _rollback_path, rollback_sha = publish_state(paths, rollback)
            publish_recovery_failed(
                paths=paths,
                rollback_state=rollback,
                rollback_sha=rollback_sha,
                prepared=prepared,
                prepared_sha=prepared_sha,
                reason="armed child died without authenticated terminal completion",
            )
            return 1
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
            decision = state.get("outcome")
            if (
                not isinstance(decision, dict)
                or decision.get("source_action_completed") is not True
                or decision.get("authenticated_action_complete") is not True
            ):
                publish_recovery_failed(
                    paths=paths,
                    rollback_state=state,
                    rollback_sha=state_sha,
                    prepared=prepared,
                    prepared_sha=prepared_sha,
                    reason="rollback state lacks authenticated source-action completion",
                )
                return 1
            residue = new_vdb_crash_residue(
                paths, prepared_vdb(prepared)
            )
            if residue:
                publish_recovery_failed(
                    paths=paths,
                    rollback_state=state,
                    rollback_sha=state_sha,
                    prepared=prepared,
                    prepared_sha=prepared_sha,
                    reason="partial Portage VDB residue requires checkpoint restoration: "
                    + ", ".join(residue),
                )
                return 1
            try:
                return run_held_lock_recovery(
                    paths=paths,
                    prepared=prepared,
                    prepared_sha=prepared_sha,
                    armed_sha=armed_sha,
                    rollback_state=state,
                    rollback_sha=state_sha,
                    tools=tools,
                    held_locks=held_locks,
                )
            except TransactionInterrupted:
                raise
            except TransactionError as error:
                reconciled = reconcile_state_chain(paths)
                if reconciled is not None and reconciled[0]["phase"] in TERMINAL_PHASES:
                    return verify_command(paths)
                if path_exists(paths.child_completion):
                    try:
                        return finalize_from_child_completion(
                            paths=paths,
                            state=state,
                            state_sha=state_sha,
                            prepared=prepared,
                            prepared_sha=prepared_sha,
                            armed_sha=armed_sha,
                        )
                    except TransactionInterrupted:
                        raise
                    except TransactionError:
                        pass
                publish_recovery_failed(
                    paths=paths,
                    rollback_state=state,
                    rollback_sha=state_sha,
                    prepared=prepared,
                    prepared_sha=prepared_sha,
                    reason="held-lock rollback could not prove exact restoration: "
                    + str(error),
                )
                return 1
        fail(f"recovery refuses unknown transaction phase {state['phase']}")


def barrier_command(arguments: argparse.Namespace) -> int:
    command = parsed_internal_command(arguments, "internal barrier")
    install_parent_death_signal(arguments.parent_pid, arguments.parent_start_ticks)
    release = os.read(arguments.barrier_fd, 1)
    os.close(arguments.barrier_fd)
    if release != b"G":
        fail("child barrier closed without an exact grant")
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

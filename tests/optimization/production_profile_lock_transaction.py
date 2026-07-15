#!/usr/bin/env python3
"""Test-only coordinator for exercising the real PGO lock hierarchy safely.

The production defaults are intentionally fixed.  Alternate paths are accepted
only with an explicit private test root, so this helper cannot be used to make
an arbitrary lock namespace look like production.  Project and generation
payloads are changed in place while their stable inodes are exclusively locked;
the framework lock remains shared for the complete child lifetime.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime as dt
import errno
import fcntl
import grp
import hashlib
import json
import os
import secrets
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, NoReturn, Sequence


FRAMEWORK_LOCK = Path("/run/gentoo-optimization/framework-install.lock")
PROJECT_LOCK = Path("/run/gentoo-optimization/project.lock")
GENERATION_LOCK = Path("/run/gentoo-optimization/generation.lock")
FRAMEWORK_BASE = Path("/var/lib/gentoo-optimization")
FRAMEWORK_CURRENT = FRAMEWORK_BASE / "framework-current"
JOURNAL = Path(
    "/var/lib/gentoo-optimization/state/profile-transactions/"
    "phase-2-production-profile-locks.pending"
)
PORTAGE_GROUP = "portage"
PRODUCTION_LOCK_DIRECTORY_MODE = 0o750
PRODUCTION_LOCK_MODE = 0o640
TEST_LOCK_MODE = 0o600
JOURNAL_MODE = 0o600
PRODUCTION_JOURNAL_MODE = 0o640
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
JOURNAL_SCHEMA = "gentoo-optimization-production-profile-lock-transaction-v1"
CHILD_IDENTITY_SCHEMA = (
    "gentoo-optimization-production-profile-lock-child-identity-v1"
)
RECEIPT_SCHEMA = "gentoo-optimization-production-profile-lock-receipt-v1"
JOURNAL_FIELDS = {
    "boot_id",
    "authorization_token_sha256",
    "created_at",
    "child_contract",
    "expected_payload_sha256",
    "gate_run_id",
    "generation",
    "framework_context",
    "locks",
    "original_payload_sha256",
    "owner",
    "paths",
    "schema",
    "test_mode",
}
GENERATION_FIELDS = {"generation_id", "inventory_id", "inventory_sha256"}
LOCK_IDENTITY_FIELDS = {"device", "gid", "inode", "mode", "nlink", "uid"}
FRAMEWORK_CONTEXT_FIELDS = {
    "framework_aggregate_sha256",
    "git_commit",
    "manifest_path",
    "manifest_sha256",
    "source_aggregate_sha256",
    "target",
}
CHILD_IDENTITY_FIELDS = {
    "authorization_token_sha256",
    "boot_id",
    "child",
    "coordinator_owner",
    "created_at",
    "framework_context",
    "gate_run_id",
    "generation",
    "journal_sha256",
    "schema",
    "test_mode",
}
PROCESS_IDENTITY_FIELDS = {"pid", "process_group", "start_ticks"}
OWNER_IDENTITY_FIELDS = {"pid", "start_ticks"}
RECEIPT_FIELDS = {
    "abandoned_receipt_partial",
    "authorization",
    "authorization_token_sha256",
    "boot_id",
    "child_exit_status",
    "child_identity_sha256",
    "completed_at",
    "framework_context",
    "gate_run_id",
    "generation",
    "journal_removal_after_receipt_required",
    "lock_payload_restored_sha256",
    "locks",
    "schema",
    "status",
    "transaction_journal",
    "transaction_journal_sha256",
    "token_scan",
}
ABANDONED_PARTIAL_FIELDS = {"path", "sha256", "size"}
EXECUTABLE_IDENTITY_FIELDS = {
    "device",
    "gid",
    "inode",
    "mode",
    "nlink",
    "path",
    "sha256",
    "uid",
}
CHILD_CONTRACT_FIELDS = {
    "argv",
    "containment",
    "containment_executable",
    "environment",
    "environment_sha256",
    "evidence_output_root",
    "executable",
    "token_scan",
}
TOKEN_SCAN_CONTRACT_FIELDS = {"executable", "output", "roots"}
TOKEN_SCAN_EVIDENCE_FIELDS = {
    "output",
    "output_sha256",
    "roots",
    "scanner_executable_sha256",
    "scanner_status",
}
AUTHORIZATION_EVIDENCE_FIELDS = {
    "abandoned_partial",
    "gate_directory_created",
    "generation_parent",
    "generation_parent_gid",
    "generation_parent_mode",
    "generation_parent_uid",
    "path",
    "sha256",
}
SAFE_ID = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+_.@-"
)
HEX64 = frozenset("0123456789abcdef")
FAILPOINT_EXIT = {
    "arm-during-project": 100,
    "arm-during-generation": 101,
    "arm-after-project": 90,
    "arm-after-generation": 91,
    "restore-after-project": 92,
    "child-after-spawn": 93,
    "child-after-sidecar": 94,
    "child-after-release": 95,
    "receipt-after-partial-fsync": 96,
    "receipt-after-final-rename": 97,
    "receipt-after-journal-removal": 98,
    "child-sidecar-after-partial-fsync": 99,
    "authorization-after-partial-fsync": 102,
}
PRODUCTION_CHILD_ENVIRONMENT = {
    "HOME": "/root",
    "LANG": "C",
    "LC_ALL": "C",
    "LOGNAME": "root",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "SHELL": "/bin/bash",
    "TZ": "UTC",
    "USER": "root",
}
UNSHARE = Path("/usr/bin/unshare")
PR_SET_PDEATHSIG = 1
FORBIDDEN_COORDINATOR_ENVIRONMENT = {
    "KEEP_TEMP",
    "PORTAGE_SAMPLE_PGO_ITERATIONS",
}


class TransactionError(Exception):
    """The production-lock test transaction could not be proven safe."""


class TransactionInterrupted(Exception):
    """The coordinator received a signal while supervising its child."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


def fail(message: str) -> NoReturn:
    raise TransactionError(message)


@dataclass(frozen=True)
class Paths:
    framework: Path
    project: Path
    generation: Path
    journal: Path
    test_mode: bool
    test_root: Path | None

    @property
    def partial_journal(self) -> Path:
        return self.journal.with_name(f"{self.journal.name}.partial")

    @property
    def child_identity(self) -> Path:
        return self.journal.with_name(f"{self.journal.name}.child.json")

    @property
    def partial_child_identity(self) -> Path:
        return self.child_identity.with_name(f"{self.child_identity.name}.partial")


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    nlink: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> "FileIdentity":
        return cls(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            uid=metadata.st_uid,
            gid=metadata.st_gid,
            mode=stat.S_IMODE(metadata.st_mode),
            nlink=metadata.st_nlink,
        )

    @classmethod
    def from_json(cls, value: object, label: str) -> "FileIdentity":
        if not isinstance(value, dict) or set(value) != LOCK_IDENTITY_FIELDS:
            fail(f"{label} has an invalid lock identity schema")
        if not all(
            isinstance(value[key], int) and not isinstance(value[key], bool)
            for key in LOCK_IDENTITY_FIELDS
        ):
            fail(f"{label} lock identity fields must be integers")
        result = cls(
            device=value["device"],
            inode=value["inode"],
            uid=value["uid"],
            gid=value["gid"],
            mode=value["mode"],
            nlink=value["nlink"],
        )
        if min(result.device, result.inode, result.uid, result.gid) < 0:
            fail(f"{label} lock identity contains a negative value")
        if result.nlink < 1 or not 0 <= result.mode <= 0o7777:
            fail(f"{label} lock identity has an invalid mode or link count")
        return result

    def as_json(self) -> dict[str, int]:
        return {
            "device": self.device,
            "gid": self.gid,
            "inode": self.inode,
            "mode": self.mode,
            "nlink": self.nlink,
            "uid": self.uid,
        }


def require_safe_path(path: Path, label: str) -> Path:
    if not path.is_absolute() or path == Path("/") or ".." in path.parts:
        fail(f"{label} must be a safe non-root absolute path")
    if any(character in os.fspath(path) for character in "\n\r\t"):
        fail(f"{label} contains an unsafe character")
    return path


def path_below(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        fail(f"{label} is outside the explicit test root: {path}")
    if path == root:
        fail(f"{label} cannot be the test root itself")


def resolve_paths(arguments: argparse.Namespace) -> Paths:
    supplied = (
        arguments.test_root,
        arguments.test_framework_lock,
        arguments.test_project_lock,
        arguments.test_generation_lock,
        arguments.test_journal,
    )
    if arguments.test_mode:
        if any(value is None for value in supplied):
            fail("test mode requires a root and all four explicit test paths")
        root = require_safe_path(arguments.test_root, "test root")
        paths = Paths(
            framework=require_safe_path(
                arguments.test_framework_lock, "test framework lock"
            ),
            project=require_safe_path(arguments.test_project_lock, "test project lock"),
            generation=require_safe_path(
                arguments.test_generation_lock, "test generation lock"
            ),
            journal=require_safe_path(arguments.test_journal, "test journal"),
            test_mode=True,
            test_root=root,
        )
        for label, path in (
            ("test framework lock", paths.framework),
            ("test project lock", paths.project),
            ("test generation lock", paths.generation),
            ("test journal", paths.journal),
        ):
            path_below(path, root, label)
        values = {paths.framework, paths.project, paths.generation, paths.journal}
        if len(values) != 4:
            fail("test lock and journal paths must be distinct")
        derived = {
            paths.partial_journal,
            paths.child_identity,
            paths.partial_child_identity,
        }
        if derived & values or len(derived) != 3:
            fail("test journal sidecar paths must not collide with transaction paths")
        if values & {FRAMEWORK_LOCK, PROJECT_LOCK, GENERATION_LOCK, JOURNAL}:
            fail("test mode cannot substitute a production transaction path")
        return paths
    if any(value is not None for value in supplied):
        fail("test path overrides require explicit --test-mode")
    return Paths(
        framework=FRAMEWORK_LOCK,
        project=PROJECT_LOCK,
        generation=GENERATION_LOCK,
        journal=JOURNAL,
        test_mode=False,
        test_root=None,
    )


def production_portage_gid() -> int:
    try:
        entry = grp.getgrnam(PORTAGE_GROUP)
    except KeyError:
        fail(f"required production group does not exist: {PORTAGE_GROUP}")
    if entry.gr_gid < 1:
        fail(f"production group has an unsafe GID: {PORTAGE_GROUP}")
    return entry.gr_gid


def validate_directory_chain(path: Path, paths: Paths, label: str) -> None:
    if paths.test_mode:
        root = paths.test_root
        if root is None:  # pragma: no cover - resolve_paths guarantees this.
            fail("internal error: test root is absent")
        try:
            root_metadata = root.lstat()
        except OSError as error:
            fail(f"cannot inspect explicit test root {root}: {error}")
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or stat.S_ISLNK(root_metadata.st_mode)
            or root_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(root_metadata.st_mode) & 0o077
        ):
            fail(f"explicit test root must be an owner-private real directory: {root}")
        if path == root:
            return
        current = root
        path_below(path, root, label)
        relative = path.relative_to(root)
        components: Sequence[str] = relative.parts
        expected_uid = os.geteuid()
    else:
        current = Path("/")
        components = path.parts[1:]
        expected_uid = 0
    for component in components:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as error:
            fail(f"cannot inspect {label} component {current}: {error}")
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            fail(f"{label} contains a non-directory or symlink component: {current}")
        if metadata.st_uid != expected_uid or stat.S_IMODE(metadata.st_mode) & 0o022:
            fail(f"{label} has an untrusted writable ancestor: {current}")


def validate_parent(path: Path, paths: Paths, label: str) -> None:
    validate_directory_chain(path.parent, paths, label)
    metadata = path.parent.lstat()
    if paths.test_mode:
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            fail(f"{label} test parent must be owner-private: {path.parent}")
    else:
        if path.parent in {FRAMEWORK_LOCK.parent, JOURNAL.parent}:
            portage_gid = production_portage_gid()
            if (
                metadata.st_uid != 0
                or metadata.st_gid != portage_gid
                or stat.S_IMODE(metadata.st_mode) != PRODUCTION_LOCK_DIRECTORY_MODE
            ):
                fail(
                    "production lock/transaction directory must be "
                    f"root:portage mode-0750: {path.parent}"
                )
        elif metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
            fail(f"production journal parent is not trusted root-owned state: {path.parent}")


def expected_lock_identity(paths: Paths) -> tuple[int, int, int]:
    if paths.test_mode:
        return os.geteuid(), os.getegid(), TEST_LOCK_MODE
    return 0, production_portage_gid(), PRODUCTION_LOCK_MODE


def require_hex(value: object, length: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in HEX64 for character in value)
    ):
        fail(f"{label} is not a lowercase {length}-character hexadecimal identity")
    return value


def require_safe_id(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or len(value) > 128
        or any(character not in SAFE_ID for character in value)
    ):
        fail(f"{label} is not a safe exact identity")
    return value


def validate_framework_context(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != FRAMEWORK_CONTEXT_FIELDS:
        fail("framework context has an invalid schema")
    result: dict[str, str] = {}
    for key in ("target", "manifest_path"):
        item = value[key]
        if not isinstance(item, str):
            fail(f"framework context {key} is not a string")
        require_safe_path(Path(item), f"framework context {key}")
        result[key] = item
    result["manifest_sha256"] = require_hex(
        value["manifest_sha256"], 64, "framework manifest digest"
    )
    result["source_aggregate_sha256"] = require_hex(
        value["source_aggregate_sha256"], 64, "framework source aggregate"
    )
    result["framework_aggregate_sha256"] = require_hex(
        value["framework_aggregate_sha256"], 64, "framework aggregate"
    )
    result["git_commit"] = require_hex(
        value["git_commit"], 40, "framework Git commit"
    )
    return result


def read_small_regular(
    path: Path,
    maximum: int,
    label: str,
    *,
    expected_gid: int = 0,
    expected_mode: int = 0o600,
    expected_uid: int = 0,
) -> tuple[bytes, FileIdentity]:
    try:
        before_metadata = path.lstat()
    except OSError as error:
        fail(f"cannot inspect {label} {path}: {error}")
    before = FileIdentity.from_stat(before_metadata)
    if (
        not stat.S_ISREG(before_metadata.st_mode)
        or stat.S_ISLNK(before_metadata.st_mode)
        or before.uid != expected_uid
        or before.gid != expected_gid
        or before.mode != expected_mode
        or before.nlink != 1
        or before_metadata.st_size > maximum
    ):
        fail(
            f"{label} is not a bounded {expected_uid}:{expected_gid} "
            f"mode-{expected_mode:04o} "
            "single-link regular file"
        )
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if FileIdentity.from_stat(os.fstat(descriptor)) != before:
            fail(f"{label} changed while it was opened")
        payload = read_descriptor(descriptor, label)
        if len(payload) > maximum:
            fail(f"{label} exceeds its {maximum}-byte size limit")
        try:
            after = FileIdentity.from_stat(path.lstat())
        except OSError as error:
            fail(f"cannot revalidate {label} {path}: {error}")
        if after != before or FileIdentity.from_stat(os.fstat(descriptor)) != before:
            fail(f"{label} path or inode changed while it was read")
    finally:
        os.close(descriptor)
    return payload, before


def inspect_trusted_directory(path: Path, label: str) -> FileIdentity:
    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"cannot inspect {label} {path}: {error}")
    identity = FileIdentity.from_stat(metadata)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or identity.uid != 0
        or identity.gid != 0
        or identity.mode != 0o755
    ):
        fail(f"{label} is not a root:root mode-0755 real directory")
    return identity


def active_framework_context(paths: Paths) -> dict[str, str]:
    if paths.test_mode:
        # Hermetic transaction tests exercise lock and journal semantics without
        # constructing a second framework installer fixture.  The production
        # branch below always derives these identities from the selected,
        # immutable candidate while holding its stable framework lock.
        root = paths.test_root
        if root is None:  # pragma: no cover - resolve_paths guarantees this.
            fail("internal error: test framework context lacks its private root")
        return {
            "framework_aggregate_sha256": hashlib.sha256(
                b"test-framework-aggregate"
            ).hexdigest(),
            "git_commit": hashlib.sha1(b"test-framework-commit").hexdigest(),
            "manifest_path": os.fspath(root / "framework/install.manifest"),
            "manifest_sha256": hashlib.sha256(b"test-framework-manifest").hexdigest(),
            "source_aggregate_sha256": hashlib.sha256(
                b"test-source-aggregate"
            ).hexdigest(),
            "target": os.fspath(root / "framework"),
        }

    validate_directory_chain(FRAMEWORK_BASE, paths, "framework base")
    try:
        current_metadata = FRAMEWORK_CURRENT.lstat()
        raw_target = os.readlink(FRAMEWORK_CURRENT)
    except OSError as error:
        fail(f"cannot inspect active framework selector {FRAMEWORK_CURRENT}: {error}")
    if (
        not stat.S_ISLNK(current_metadata.st_mode)
        or current_metadata.st_uid != 0
        or current_metadata.st_gid != 0
        or current_metadata.st_nlink != 1
    ):
        fail("active framework selector is not a root-owned single-link symlink")
    target = Path(raw_target)
    if (
        not target.is_absolute()
        or target.parent != FRAMEWORK_BASE
        or not target.name.startswith("framework-")
    ):
        fail("active framework selector has an unsafe target")
    identity = target.name.removeprefix("framework-")
    require_hex(identity, 64, "active framework identity")
    try:
        target_metadata = target.lstat()
    except OSError as error:
        fail(f"cannot inspect active framework target {target}: {error}")
    if (
        not stat.S_ISDIR(target_metadata.st_mode)
        or stat.S_ISLNK(target_metadata.st_mode)
        or target_metadata.st_uid != 0
        or target_metadata.st_gid != 0
        or stat.S_IMODE(target_metadata.st_mode) & 0o022
    ):
        fail("active framework target is not a trusted root-owned immutable directory")

    manifest = target / "install.manifest"
    payload, _manifest_identity = read_small_regular(
        manifest,
        65536,
        "active framework manifest",
        expected_gid=production_portage_gid(),
        expected_mode=0o640,
    )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"active framework manifest is not UTF-8: {error}")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line == "path\tsha256\tmode\towner":
            break
        key, separator, item = line.partition("=")
        if not separator or not key or key in fields:
            fail("active framework manifest has a malformed or duplicate header")
        fields[key] = item
    required = {
        "framework_aggregate_sha256",
        "source_aggregate_sha256",
        "git_commit",
        "current_generation",
        "generated_policy",
        "frozen_inventory_sha256",
    }
    if not required <= fields.keys():
        fail("active framework manifest lacks Phase 2 identity fields")
    framework_aggregate = require_hex(
        fields["framework_aggregate_sha256"], 64, "manifest framework aggregate"
    )
    source_aggregate = require_hex(
        fields["source_aggregate_sha256"], 64, "manifest source aggregate"
    )
    commit = require_hex(fields["git_commit"], 40, "manifest Git commit")
    if framework_aggregate != identity or fields["current_generation"] != raw_target:
        fail("active framework manifest does not select its containing candidate")
    if (
        fields["generated_policy"] != "empty-v1"
        or fields["frozen_inventory_sha256"] != "none"
    ):
        fail("production-lock Phase 2 gate requires empty policy and no frozen inventory")
    generated = target / "generated-policy"
    identity_file = generated / ".identity"
    package_env = generated / "package.env"
    environment = generated / "env"
    try:
        generated_identity = inspect_trusted_directory(
            generated, "active framework generated-policy directory"
        )
        environment_identity = inspect_trusted_directory(
            environment, "active framework generated-policy env directory"
        )
        identity_payload, _identity_file_identity = read_small_regular(
            identity_file,
            128,
            "active framework generated-policy identity",
            expected_gid=production_portage_gid(),
            expected_mode=0o640,
        )
        package_payload, _package_file_identity = read_small_regular(
            package_env,
            65536,
            "active framework generated-policy package.env",
            expected_mode=0o644,
        )
        if (
            identity_payload != b"empty-v1\n"
            or package_payload != b""
            or any(environment.iterdir())
            or FileIdentity.from_stat(generated.lstat()) != generated_identity
            or FileIdentity.from_stat(environment.lstat()) != environment_identity
        ):
            fail("active framework generated policy is not the exact empty-v1 tree")
    except OSError as error:
        fail(f"cannot verify active framework generated policy: {error}")
    try:
        if FRAMEWORK_CURRENT.lstat() != current_metadata or os.readlink(
            FRAMEWORK_CURRENT
        ) != raw_target:
            fail("active framework selector changed while its context was captured")
    except OSError as error:
        fail(f"cannot revalidate active framework selector: {error}")
    return validate_framework_context(
        {
            "framework_aggregate_sha256": framework_aggregate,
            "git_commit": commit,
            "manifest_path": os.fspath(manifest),
            "manifest_sha256": hashlib.sha256(payload).hexdigest(),
            "source_aggregate_sha256": source_aggregate,
            "target": raw_target,
        }
    )


def inspect_lock(path: Path, paths: Paths, label: str) -> FileIdentity:
    validate_parent(path, paths, label)
    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"cannot inspect {label} {path}: {error}")
    expected_uid, expected_gid, expected_mode = expected_lock_identity(paths)
    identity = FileIdentity.from_stat(metadata)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or identity.uid != expected_uid
        or identity.gid != expected_gid
        or identity.mode != expected_mode
        or identity.nlink != 1
    ):
        fail(
            f"{label} is not a stable {expected_uid}:{expected_gid} "
            f"mode-{expected_mode:04o} single-link regular inode: {path}"
        )
    return identity


def inspect_journal_file(
    path: Path,
    paths: Paths,
    label: str,
    *,
    portage_readable: bool = False,
) -> FileIdentity:
    validate_parent(path, paths, label)
    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"cannot inspect {label} {path}: {error}")
    expected_uid = os.geteuid() if paths.test_mode else 0
    expected_gid = os.getegid() if paths.test_mode else 0
    expected_mode = JOURNAL_MODE
    if not paths.test_mode and portage_readable:
        expected_gid = production_portage_gid()
        expected_mode = PRODUCTION_JOURNAL_MODE
    identity = FileIdentity.from_stat(metadata)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or identity.uid != expected_uid
        or identity.gid != expected_gid
        or identity.mode != expected_mode
        or identity.nlink != 1
    ):
        fail(
            f"{label} is not a trusted {expected_uid}:{expected_gid} "
            f"mode-{expected_mode:04o} single-link regular file"
        )
    return identity


def read_verified_journal_file(
    path: Path,
    paths: Paths,
    label: str,
    *,
    portage_readable: bool = False,
) -> tuple[bytes, FileIdentity]:
    identity = inspect_journal_file(
        path, paths, label, portage_readable=portage_readable
    )
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if FileIdentity.from_stat(os.fstat(descriptor)) != identity:
            fail(f"{label} changed while it was opened")
        payload = read_descriptor(descriptor, label)
        if FileIdentity.from_stat(os.fstat(descriptor)) != identity:
            fail(f"{label} changed while it was read")
        try:
            after = FileIdentity.from_stat(path.lstat())
        except OSError as error:
            fail(f"cannot revalidate {label}: {error}")
        if after != identity:
            fail(f"{label} path changed while it was read")
        return payload, identity
    finally:
        os.close(descriptor)


def open_verified_lock(
    path: Path, paths: Paths, label: str, *, writable: bool
) -> tuple[int, FileIdentity]:
    before = inspect_lock(path, paths, label)
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        fail(f"cannot open {label} {path}: {error}")
    try:
        metadata = os.fstat(descriptor)
        observed = FileIdentity.from_stat(metadata)
        if not stat.S_ISREG(metadata.st_mode) or observed != before:
            fail(f"{label} changed while it was opened: {path}")
        return descriptor, observed
    except BaseException:
        os.close(descriptor)
        raise


def acquire_lock(descriptor: int, operation: int, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail(f"timed out after {timeout:g}s waiting for {label}")
            time.sleep(min(0.05, remaining))
        except OSError as error:
            fail(f"cannot acquire {label}: {error}")


def read_descriptor(descriptor: int, label: str) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > 65536:
                fail(f"{label} exceeds the 65536-byte payload limit")
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as error:
        fail(f"cannot read {label}: {error}")


def write_descriptor(
    descriptor: int,
    payload: bytes,
    label: str,
    *,
    partial_failpoint: str | None = None,
    selected_failpoint: str | None = None,
) -> None:
    try:
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        offset = 0
        if partial_failpoint is not None and selected_failpoint == partial_failpoint:
            prefix_length = max(1, len(payload) // 2)
            written = os.write(descriptor, payload[:prefix_length])
            if written != prefix_length:
                fail(f"short write while publishing {label} crash prefix")
            os.fsync(descriptor)
            trigger_failpoint(selected_failpoint, partial_failpoint)
            offset = prefix_length
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                fail(f"short write while publishing {label}")
            offset += written
        os.fsync(descriptor)
    except OSError as error:
        fail(f"cannot durably publish {label}: {error}")
    if read_descriptor(descriptor, label) != payload:
        fail(f"{label} differs immediately after its fsync")


def revalidate_open_path(
    descriptor: int, path: Path, expected: FileIdentity, paths: Paths, label: str
) -> None:
    observed = FileIdentity.from_stat(os.fstat(descriptor))
    current = inspect_lock(path, paths, label)
    if observed != expected or current != expected:
        fail(f"{label} inode or metadata changed while held: {path}")


@contextlib.contextmanager
def framework_lock(paths: Paths, timeout: float) -> Iterator[tuple[int, FileIdentity]]:
    descriptor, identity = open_verified_lock(
        paths.framework, paths, "framework lock", writable=False
    )
    acquired = False
    try:
        acquire_lock(descriptor, fcntl.LOCK_SH, timeout, "framework lock")
        acquired = True
        if read_descriptor(descriptor, "framework lock") != b"":
            fail("stable framework lock inode must remain empty")
        revalidate_open_path(
            descriptor, paths.framework, identity, paths, "framework lock"
        )
        yield descriptor, identity
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextlib.contextmanager
def generation_locks(
    paths: Paths, timeout: float
) -> Iterator[tuple[tuple[int, FileIdentity], tuple[int, FileIdentity]]]:
    opened: list[tuple[int, FileIdentity, Path, str]] = []
    acquired: list[int] = []
    try:
        for path, label in (
            (paths.project, "project lock"),
            (paths.generation, "generation lock"),
        ):
            descriptor, identity = open_verified_lock(path, paths, label, writable=True)
            opened.append((descriptor, identity, path, label))
            acquire_lock(descriptor, fcntl.LOCK_EX, timeout, label)
            acquired.append(descriptor)
            revalidate_open_path(descriptor, path, identity, paths, label)
        yield (opened[0][0], opened[0][1]), (opened[1][0], opened[1][1])
        for descriptor, identity, path, label in opened:
            revalidate_open_path(descriptor, path, identity, paths, label)
    finally:
        for descriptor in reversed(acquired):
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        for descriptor, _identity, _path, _label in reversed(opened):
            with contextlib.suppress(OSError):
                os.close(descriptor)


@contextlib.contextmanager
def generation_locks_shared(
    paths: Paths, timeout: float
) -> Iterator[tuple[tuple[int, FileIdentity], tuple[int, FileIdentity]]]:
    opened: list[tuple[int, FileIdentity, Path, str]] = []
    acquired: list[int] = []
    try:
        for path, label in (
            (paths.project, "project lock"),
            (paths.generation, "generation lock"),
        ):
            descriptor, identity = open_verified_lock(path, paths, label, writable=False)
            opened.append((descriptor, identity, path, label))
            acquire_lock(descriptor, fcntl.LOCK_SH, timeout, label)
            acquired.append(descriptor)
            revalidate_open_path(descriptor, path, identity, paths, label)
        yield (opened[0][0], opened[0][1]), (opened[1][0], opened[1][1])
        for descriptor, identity, path, label in opened:
            revalidate_open_path(descriptor, path, identity, paths, label)
    finally:
        for descriptor in reversed(acquired):
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        for descriptor, _identity, _path, _label in reversed(opened):
            with contextlib.suppress(OSError):
                os.close(descriptor)


def validate_generation(value: object, label: str = "generation") -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != GENERATION_FIELDS:
        fail(f"{label} must contain exactly {sorted(GENERATION_FIELDS)}")
    result: dict[str, str] = {}
    for key in ("generation_id", "inventory_id"):
        result[key] = require_safe_id(value[key], f"{label}.{key}")
    digest = value["inventory_sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in HEX64 for character in digest)
    ):
        fail(f"{label}.inventory_sha256 is not a lowercase SHA-256")
    result["inventory_sha256"] = digest
    return result


def canonical_payload(generation: dict[str, str]) -> bytes:
    return (json.dumps(generation, indent=2, sort_keys=True) + "\n").encode("utf-8")


def transaction_receipt_path(paths: Paths, generation: dict[str, str]) -> Path:
    return paths.journal.parent / (
        "phase-2-production-profile-locks-"
        f"{generation['generation_id']}.receipt.json"
    )


def transaction_authorization_path(
    paths: Paths, generation: dict[str, str], gate_run_id: str
) -> Path:
    base = (
        paths.test_root / "generation-state"
        if paths.test_mode and paths.test_root is not None
        else Path("/var/lib/gentoo-optimization/generations")
    )
    return base / generation["generation_id"] / (
        f"phase2-sample-gate-{gate_run_id}"
    ) / "transaction.authorization"


def derived_gate_artifact_paths(
    paths: Paths, generation: dict[str, str], gate_run_id: str
) -> tuple[Path, Path, Path, Path]:
    authorization = transaction_authorization_path(paths, generation, gate_run_id)
    state_root = authorization.parent
    if paths.test_mode and paths.test_root is not None:
        work_root = paths.test_root / "artifacts"
        profile_root = paths.test_root / "profile-artifacts"
    else:
        work_root = Path("/var/tmp/gentoo-optimization") / (
            f"phase2-sample-work-{gate_run_id}"
        )
        profile_root = (
            Path("/var/cache/gentoo-optimization/pgo/clang-sample")
            / f"phase2-sample-gate-{gate_run_id}"
        )
    output = state_root / "coordinator-token-scan.tsv"
    return work_root, profile_root, state_root, output


def boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
    except OSError as error:
        fail(f"cannot read live boot ID: {error}")
    if not value or any(character not in "0123456789abcdef-" for character in value):
        fail("live boot ID has an unsafe form")
    return value


def process_start_ticks(pid: int) -> str | None:
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError as error:
        fail(f"cannot inspect transaction owner PID {pid}: {error}")
    _prefix, separator, suffix = value.rpartition(") ")
    fields = suffix.split() if separator else []
    if len(fields) <= 19 or not fields[19].isdigit():
        fail(f"cannot parse transaction owner start time for PID {pid}")
    return fields[19]


def current_start_ticks() -> str:
    value = process_start_ticks(os.getpid())
    if value is None:  # pragma: no cover - the current process must exist.
        fail("cannot inspect the current transaction PID")
    return value


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def journal_document(
    paths: Paths,
    gate_run_id: str,
    generation: dict[str, str],
    framework_context: dict[str, str],
    authorization_token_sha256: str,
    child_contract: dict[str, object],
    identities: dict[str, FileIdentity],
) -> dict[str, object]:
    payload = canonical_payload(generation)
    return {
        "authorization_token_sha256": require_hex(
            authorization_token_sha256, 64, "authorization token digest"
        ),
        "boot_id": boot_id(),
        "created_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "child_contract": validate_child_contract(child_contract),
        "expected_payload_sha256": hashlib.sha256(payload).hexdigest(),
        "framework_context": framework_context,
        "gate_run_id": require_safe_id(gate_run_id, "production gate run ID"),
        "generation": generation,
        "locks": {key: value.as_json() for key, value in identities.items()},
        "original_payload_sha256": EMPTY_SHA256,
        "owner": {"pid": os.getpid(), "start_ticks": current_start_ticks()},
        "paths": {
            "framework": os.fspath(paths.framework),
            "generation": os.fspath(paths.generation),
            "project": os.fspath(paths.project),
        },
        "schema": JOURNAL_SCHEMA,
        "test_mode": paths.test_mode,
    }


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def inspect_executable_identity(
    path: Path, paths: Paths, label: str
) -> dict[str, object]:
    path = require_safe_path(path, label)
    if not paths.test_mode:
        validate_directory_chain(path.parent, paths, label)
    try:
        before_metadata = path.lstat()
    except OSError as error:
        fail(f"cannot inspect {label} {path}: {error}")
    before = FileIdentity.from_stat(before_metadata)
    trusted_uids = {0, os.geteuid()} if paths.test_mode else {0}
    trusted_gids = {0, os.getegid()} if paths.test_mode else {0}
    if (
        not stat.S_ISREG(before_metadata.st_mode)
        or stat.S_ISLNK(before_metadata.st_mode)
        or before.uid not in trusted_uids
        or before.gid not in trusted_gids
        or before.mode & 0o022
        or before.mode & 0o7000
        or not before.mode & 0o111
        or before.nlink != 1
    ):
        fail(
            f"{label} is not a trusted executable single-link regular file: {path}"
        )
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if FileIdentity.from_stat(os.fstat(descriptor)) != before:
            fail(f"{label} changed while it was opened")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        try:
            after = FileIdentity.from_stat(path.lstat())
        except OSError as error:
            fail(f"cannot revalidate {label} {path}: {error}")
        if after != before or FileIdentity.from_stat(os.fstat(descriptor)) != before:
            fail(f"{label} changed while it was hashed")
    finally:
        os.close(descriptor)
    result: dict[str, object] = {
        key: value for key, value in before.as_json().items()
    }
    result["path"] = os.fspath(path)
    result["sha256"] = digest.hexdigest()
    return result


def validate_executable_identity(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != EXECUTABLE_IDENTITY_FIELDS:
        fail(f"{label} has an invalid executable identity schema")
    path_value = value["path"]
    if not isinstance(path_value, str):
        fail(f"{label} path is not a string")
    require_safe_path(Path(path_value), f"{label} path")
    require_hex(value["sha256"], 64, f"{label} digest")
    FileIdentity.from_json(
        {key: value[key] for key in LOCK_IDENTITY_FIELDS}, label
    )
    return value


def child_environment_contract(
    gate_run_id: str,
    generation: dict[str, str],
    authorization_token_sha256: str,
    authorization_path: Path,
    work_root: Path,
) -> dict[str, str]:
    result = dict(PRODUCTION_CHILD_ENVIRONMENT)
    result["GENTOO_OPT_PRODUCTION_GATE_RUN_ID"] = gate_run_id
    result["GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID"] = generation["generation_id"]
    result["GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID"] = generation["inventory_id"]
    result["GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256"] = generation[
        "inventory_sha256"
    ]
    result["GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN_SHA256"] = (
        authorization_token_sha256
    )
    result["GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION"] = os.fspath(
        authorization_path
    )
    result["GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT"] = os.fspath(work_root)
    return result


def validate_child_contract(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != CHILD_CONTRACT_FIELDS:
        fail("child contract has an invalid schema")
    argv = value["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or any(
            not isinstance(item, str) or "\0" in item
            for item in argv
        )
    ):
        fail("child contract argv is not a nonempty exact string vector")
    executable = validate_executable_identity(
        value["executable"], "child contract executable"
    )
    if argv[0] != executable["path"]:
        fail("child contract argv[0] differs from its executable path")
    containment = value["containment"]
    if containment not in {"direct-test-v1", "pid-namespace-v1"}:
        fail("child contract has an invalid containment policy")
    containment_executable = value["containment_executable"]
    if containment == "pid-namespace-v1":
        validate_executable_identity(
            containment_executable, "child contract namespace executable"
        )
    elif containment_executable is not None:
        fail("direct-test child contract unexpectedly names a containment executable")
    environment = value["environment"]
    if (
        not isinstance(environment, dict)
        or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in environment.items()
        )
    ):
        fail("child contract environment is not an exact string mapping")
    if value["environment_sha256"] != hashlib.sha256(
        canonical_json(environment)
    ).hexdigest():
        fail("child contract environment digest is inconsistent")
    evidence_output_root = value["evidence_output_root"]
    if not isinstance(evidence_output_root, str):
        fail("child contract evidence output root is not a string")
    require_safe_path(
        Path(evidence_output_root), "child contract evidence output root"
    )
    token_scan = value["token_scan"]
    if not isinstance(token_scan, dict) or set(token_scan) != TOKEN_SCAN_CONTRACT_FIELDS:
        fail("child contract token scanner has an invalid schema")
    validate_executable_identity(
        token_scan["executable"], "child contract token scanner executable"
    )
    roots = token_scan["roots"]
    if (
        not isinstance(roots, list)
        or not roots
        or any(not isinstance(root, str) for root in roots)
    ):
        fail("child contract token scan roots are invalid")
    for root in roots:
        require_safe_path(Path(root), "child contract token scan root")
    if len(set(roots)) != len(roots):
        fail("child contract token scan roots are not unique")
    output = token_scan["output"]
    if not isinstance(output, str):
        fail("child contract token scan output is not a string")
    require_safe_path(Path(output), "child contract token scan output")
    return value


def revalidate_child_contract(
    contract: dict[str, object], paths: Paths
) -> None:
    validated = validate_child_contract(contract)
    for key, label in (
        ("executable", "child executable"),
        ("containment_executable", "PID-namespace executable"),
        ("token_scan", "authorization token scanner"),
    ):
        identity_value: object
        if key == "executable":
            identity_value = validated[key]
        elif key == "containment_executable":
            identity_value = validated[key]
            if identity_value is None:
                continue
        else:
            token_scan = validated[key]
            if not isinstance(token_scan, dict):  # pragma: no cover - validated above.
                fail("internal error: token scan contract is invalid")
            identity_value = token_scan["executable"]
        identity = validate_executable_identity(identity_value, label)
        executable_path = identity["path"]
        if not isinstance(executable_path, str):  # pragma: no cover - validated above.
            fail("internal error: executable path is invalid")
        if inspect_executable_identity(Path(executable_path), paths, label) != identity:
            fail(f"{label} identity changed after transaction arming")


def build_child_contract(
    arguments: argparse.Namespace,
    paths: Paths,
    generation: dict[str, str],
    gate_run_id: str,
    authorization_token_sha256: str,
    authorization_path: Path,
) -> dict[str, object]:
    command = list(arguments.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        fail("run requires a child command after --")
    raw_executable = Path(command[0])
    if not raw_executable.is_absolute() or raw_executable == Path("/"):
        fail("child command must use an absolute executable path")
    try:
        executable = raw_executable.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve child executable {raw_executable}: {error}")
    command[0] = os.fspath(executable)
    if any("\0" in item for item in command):
        fail("child argv contains a NUL byte")
    scanner = arguments.token_scanner
    try:
        scanner = scanner.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve authorization token scanner: {error}")
    work_root, profile_root, state_root, expected_output = derived_gate_artifact_paths(
        paths, generation, gate_run_id
    )
    evidence_output_root = require_safe_path(
        arguments.evidence_output_root, "evidence output root"
    )
    if not paths.test_mode:
        output_positions = [
            index
            for index, item in enumerate(command[:-1])
            if item == "--output-dir"
        ]
        if (
            len(output_positions) != 1
            or command[output_positions[0] + 1] != os.fspath(evidence_output_root)
        ):
            fail(
                "production child argv must contain exactly one matching "
                "--output-dir ABSOLUTE_PATH"
            )
    expected_roots = [
        os.fspath(work_root),
        os.fspath(profile_root),
        os.fspath(state_root),
        os.fspath(evidence_output_root),
    ]
    roots = [
        os.fspath(require_safe_path(root, "token scan root"))
        for root in arguments.token_scan_root
    ]
    if roots != expected_roots:
        fail("token scan roots differ from the exact derived gate artifact roots")
    output = require_safe_path(arguments.token_scan_output, "token scan output")
    if output != expected_output:
        fail("token scan output differs from the exact generation-state output")
    environment = child_environment_contract(
        gate_run_id,
        generation,
        authorization_token_sha256,
        authorization_path,
        work_root,
    )
    containment = (
        "pid-namespace-v1"
        if not paths.test_mode or arguments.test_pid_namespace
        else "direct-test-v1"
    )
    contract: dict[str, object] = {
        "argv": command,
        "containment": containment,
        "containment_executable": (
            inspect_executable_identity(UNSHARE, paths, "PID-namespace executable")
            if containment == "pid-namespace-v1"
            else None
        ),
        "environment": environment,
        "environment_sha256": hashlib.sha256(canonical_json(environment)).hexdigest(),
        "evidence_output_root": os.fspath(evidence_output_root),
        "executable": inspect_executable_identity(
            executable, paths, "child executable"
        ),
        "token_scan": {
            "executable": inspect_executable_identity(
                scanner, paths, "authorization token scanner"
            ),
            "output": os.fspath(output),
            "roots": roots,
        },
    }
    return validate_child_contract(contract)


def validate_owner_identity(value: object, label: str) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != OWNER_IDENTITY_FIELDS
        or not isinstance(value["pid"], int)
        or isinstance(value["pid"], bool)
        or value["pid"] < 1
        or not isinstance(value["start_ticks"], str)
        or not value["start_ticks"].isdigit()
    ):
        fail(f"{label} has an invalid process-owner identity")
    return {"pid": value["pid"], "start_ticks": value["start_ticks"]}


def validate_process_identity(value: object, label: str) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != PROCESS_IDENTITY_FIELDS
        or not isinstance(value["pid"], int)
        or isinstance(value["pid"], bool)
        or value["pid"] < 2
        or not isinstance(value["process_group"], int)
        or isinstance(value["process_group"], bool)
        or value["process_group"] != value["pid"]
        or not isinstance(value["start_ticks"], str)
        or not value["start_ticks"].isdigit()
    ):
        fail(f"{label} has an invalid child process-group identity")
    return {
        "pid": value["pid"],
        "process_group": value["process_group"],
        "start_ticks": value["start_ticks"],
    }


def write_journal(paths: Paths, document: dict[str, object]) -> None:
    validate_parent(paths.journal, paths, "transaction journal")
    partial = paths.partial_journal
    if paths.journal.exists() or paths.journal.is_symlink():
        fail(f"production-lock transaction journal already exists: {paths.journal}")
    if partial.exists() or partial.is_symlink():
        fail(f"production-lock transaction partial already exists: {partial}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        journal_mode = JOURNAL_MODE if paths.test_mode else PRODUCTION_JOURNAL_MODE
        descriptor = os.open(partial, flags, journal_mode)
        if not paths.test_mode:
            os.fchown(descriptor, 0, production_portage_gid())
            os.fchmod(descriptor, PRODUCTION_JOURNAL_MODE)
        payload = canonical_json(document)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                fail("short write while publishing the transaction journal")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        inspect_journal_file(
            partial,
            paths,
            "transaction journal partial",
            portage_readable=True,
        )
        os.replace(partial, paths.journal)
        fsync_directory(paths.journal.parent)
        inspect_journal_file(
            paths.journal,
            paths,
            "transaction journal",
            portage_readable=True,
        )
    except BaseException:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with contextlib.suppress(OSError):
            partial.unlink()
            fsync_directory(partial.parent)
        raise


def validate_journal_document(
    value: object, paths: Paths | None = None
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != JOURNAL_FIELDS:
        fail("transaction journal has an invalid schema")
    test_mode = value["test_mode"]
    if value["schema"] != JOURNAL_SCHEMA or type(test_mode) is not bool:
        fail("transaction journal has an invalid schema identity or mode")
    if paths is not None and test_mode is not paths.test_mode:
        fail("transaction journal belongs to a different coordinator mode")
    if value["original_payload_sha256"] != EMPTY_SHA256:
        fail("transaction journal does not describe initially empty locks")
    generation = validate_generation(value["generation"], "journal generation")
    validate_framework_context(value["framework_context"])
    validate_child_contract(value["child_contract"])
    require_safe_id(value["gate_run_id"], "journal production gate run ID")
    require_hex(
        value["authorization_token_sha256"],
        64,
        "journal authorization token digest",
    )
    expected_payload = canonical_payload(generation)
    if value["expected_payload_sha256"] != hashlib.sha256(expected_payload).hexdigest():
        fail("transaction journal expected-payload digest is inconsistent")
    journal_paths = value["paths"]
    if not isinstance(journal_paths, dict) or set(journal_paths) != {
        "framework",
        "generation",
        "project",
    }:
        fail("transaction journal has an invalid path schema")
    validated_paths: dict[str, str] = {}
    for key in ("framework", "generation", "project"):
        item = journal_paths[key]
        if not isinstance(item, str):
            fail(f"transaction journal {key} path is not a string")
        require_safe_path(Path(item), f"transaction journal {key} path")
        validated_paths[key] = item
    if len(set(validated_paths.values())) != 3:
        fail("transaction journal lock paths are not distinct")
    if paths is not None:
        expected_paths = {
            "framework": os.fspath(paths.framework),
            "generation": os.fspath(paths.generation),
            "project": os.fspath(paths.project),
        }
        if validated_paths != expected_paths:
            fail("transaction journal paths differ from this coordinator")
    locks = value["locks"]
    if not isinstance(locks, dict) or set(locks) != {
        "framework",
        "project",
        "generation",
    }:
        fail("transaction journal has an invalid lock identity set")
    for key in ("framework", "project", "generation"):
        FileIdentity.from_json(locks[key], f"journal {key}")
    validate_owner_identity(value["owner"], "journal owner")
    if not isinstance(value["boot_id"], str) or not value["boot_id"]:
        fail("transaction journal has an invalid boot identity")
    if not isinstance(value["created_at"], str) or not value["created_at"]:
        fail("transaction journal has an invalid creation time")
    return value


def child_identity_document(
    paths: Paths,
    journal: dict[str, object],
    child: dict[str, object],
) -> dict[str, object]:
    generation = validate_generation(journal["generation"], "journal generation")
    framework_context = validate_framework_context(journal["framework_context"])
    coordinator_owner = validate_owner_identity(journal["owner"], "journal owner")
    process_identity = validate_process_identity(child, "child")
    authorization_token_sha256 = require_hex(
        journal["authorization_token_sha256"],
        64,
        "journal authorization token digest",
    )
    boot_identity = journal["boot_id"]
    if not isinstance(boot_identity, str) or not boot_identity:
        fail("journal boot identity is invalid")
    return {
        "authorization_token_sha256": authorization_token_sha256,
        "boot_id": boot_identity,
        "child": process_identity,
        "coordinator_owner": coordinator_owner,
        "created_at": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "framework_context": framework_context,
        "gate_run_id": require_safe_id(
            journal["gate_run_id"], "journal production gate run ID"
        ),
        "generation": generation,
        "journal_sha256": hashlib.sha256(canonical_json(journal)).hexdigest(),
        "schema": CHILD_IDENTITY_SCHEMA,
        "test_mode": paths.test_mode,
    }


def write_child_identity(
    paths: Paths,
    journal: dict[str, object],
    child: dict[str, object],
    failpoint: str | None,
) -> dict[str, object]:
    validate_parent(paths.child_identity, paths, "child identity sidecar")
    if paths.child_identity.exists() or paths.child_identity.is_symlink():
        fail(f"child identity sidecar already exists: {paths.child_identity}")
    if paths.partial_child_identity.exists() or paths.partial_child_identity.is_symlink():
        fail(
            "child identity sidecar partial already exists: "
            f"{paths.partial_child_identity}"
        )
    repeated_journal, _journal_identity = load_journal(paths)
    if repeated_journal != journal:
        fail("transaction journal changed before child identity publication")
    document = child_identity_document(paths, journal, child)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        child_mode = JOURNAL_MODE if paths.test_mode else PRODUCTION_JOURNAL_MODE
        descriptor = os.open(paths.partial_child_identity, flags, child_mode)
        if not paths.test_mode:
            os.fchown(descriptor, 0, production_portage_gid())
            os.fchmod(descriptor, PRODUCTION_JOURNAL_MODE)
        payload = canonical_json(document)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                fail("short write while publishing the child identity sidecar")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        inspect_journal_file(
            paths.partial_child_identity,
            paths,
            "child identity sidecar partial",
            portage_readable=True,
        )
        trigger_failpoint(failpoint, "child-sidecar-after-partial-fsync")
        os.replace(paths.partial_child_identity, paths.child_identity)
        fsync_directory(paths.child_identity.parent)
        load_child_identity(paths, journal)
        repeated_journal, _journal_identity = load_journal(paths)
        if repeated_journal != journal:
            fail("transaction journal changed during child identity publication")
        return document
    except BaseException:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with contextlib.suppress(OSError):
            paths.partial_child_identity.unlink()
            fsync_directory(paths.partial_child_identity.parent)
        raise


def load_child_identity(
    paths: Paths,
    journal: dict[str, object] | None,
) -> tuple[dict[str, object], FileIdentity]:
    identity = inspect_journal_file(
        paths.child_identity,
        paths,
        "child identity sidecar",
        portage_readable=True,
    )
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(paths.child_identity, flags)
    try:
        if FileIdentity.from_stat(os.fstat(descriptor)) != identity:
            fail("child identity sidecar changed while it was opened")
        payload = read_descriptor(descriptor, "child identity sidecar")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"child identity sidecar is not valid JSON: {error}")
    if not isinstance(value, dict) or set(value) != CHILD_IDENTITY_FIELDS:
        fail("child identity sidecar has an invalid schema")
    if canonical_json(value) != payload:
        fail("child identity sidecar is not canonical JSON")
    if (
        value["schema"] != CHILD_IDENTITY_SCHEMA
        or value["test_mode"] is not paths.test_mode
    ):
        fail("child identity sidecar belongs to a different coordinator mode")
    validate_generation(value["generation"], "child identity generation")
    validate_framework_context(value["framework_context"])
    require_safe_id(value["gate_run_id"], "child identity production gate run ID")
    validate_owner_identity(value["coordinator_owner"], "child identity coordinator")
    validate_process_identity(value["child"], "child identity")
    require_hex(
        value["authorization_token_sha256"],
        64,
        "child identity authorization token digest",
    )
    require_hex(value["journal_sha256"], 64, "child identity journal digest")
    if not isinstance(value["boot_id"], str) or not value["boot_id"]:
        fail("child identity sidecar has an invalid boot identity")
    if not isinstance(value["created_at"], str) or not value["created_at"]:
        fail("child identity sidecar has an invalid creation time")
    if journal is not None:
        expected = child_identity_document(paths, journal, value["child"])
        expected["created_at"] = value["created_at"]
        if value != expected:
            fail(
                "child identity sidecar is not exactly bound to its transaction journal"
            )
    return value, identity


def authorization_payload(
    journal: dict[str, object], child_identity_sha256: str
) -> bytes:
    generation = validate_generation(journal["generation"], "journal generation")
    framework = validate_framework_context(journal["framework_context"])
    rows = (
        ("schema", "gentoo-optimization-production-profile-authorization-v1"),
        ("generation_id", generation["generation_id"]),
        ("expected_payload_sha256", str(journal["expected_payload_sha256"])),
        ("journal_sha256", hashlib.sha256(canonical_json(journal)).hexdigest()),
        ("framework_aggregate_sha256", framework["framework_aggregate_sha256"]),
        ("authorization_token_sha256", str(journal["authorization_token_sha256"])),
        ("child_identity_sha256", child_identity_sha256),
    )
    return "".join(f"{key}\t{value}\n" for key, value in rows).encode("ascii")


def publish_transaction_authorization(
    paths: Paths,
    journal: dict[str, object],
    child_identity_sha256: str,
    failpoint: str | None = None,
) -> dict[str, object]:
    child_contract = validate_child_contract(journal["child_contract"])
    environment = child_contract["environment"]
    if not isinstance(environment, dict):  # pragma: no cover - validated.
        fail("internal error: authorization environment is invalid")
    generation = validate_generation(journal["generation"], "journal generation")
    gate_run_id = require_safe_id(journal["gate_run_id"], "journal gate run ID")
    path = transaction_authorization_path(paths, generation, gate_run_id)
    if environment.get(
        "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION"
    ) != os.fspath(path):
        fail("child contract authorization path differs from its journal identity")
    generation_parent = path.parent.parent
    generations_root = generation_parent.parent
    directory_mode = 0o700 if paths.test_mode else 0o755
    expected_uid = os.geteuid() if paths.test_mode else 0
    expected_gid = os.getegid() if paths.test_mode else 0
    if paths.test_mode:
        if paths.test_root is None:  # pragma: no cover - resolved.
            fail("internal error: test root is absent")
        path_below(generations_root, paths.test_root, "test generations root")
    validate_directory_chain(generations_root, paths, "generations root")
    root_metadata = generations_root.lstat()
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or root_metadata.st_uid != expected_uid
        or root_metadata.st_gid != expected_gid
        or stat.S_IMODE(root_metadata.st_mode) != directory_mode
    ):
        fail("generations root has untrusted ownership or mode")
    if not generation_parent.exists() and not generation_parent.is_symlink():
        os.mkdir(generation_parent, directory_mode)
        if not paths.test_mode:
            os.chown(generation_parent, 0, 0)
            os.chmod(generation_parent, 0o755)
        fsync_directory(generations_root)
    generation_metadata = generation_parent.lstat()
    if (
        not stat.S_ISDIR(generation_metadata.st_mode)
        or stat.S_ISLNK(generation_metadata.st_mode)
        or generation_metadata.st_uid != expected_uid
        or generation_metadata.st_gid != expected_gid
        or stat.S_IMODE(generation_metadata.st_mode) != directory_mode
    ):
        fail("generation state parent has untrusted ownership or mode")
    if not path.parent.exists() and not path.parent.is_symlink():
        os.mkdir(path.parent, directory_mode)
        if not paths.test_mode:
            os.chown(path.parent, 0, 0)
            os.chmod(path.parent, 0o755)
        fsync_directory(generation_parent)
    gate_metadata = path.parent.lstat()
    if (
        not stat.S_ISDIR(gate_metadata.st_mode)
        or stat.S_ISLNK(gate_metadata.st_mode)
        or gate_metadata.st_uid != expected_uid
        or gate_metadata.st_gid != expected_gid
        or stat.S_IMODE(gate_metadata.st_mode) != directory_mode
    ):
        fail("production gate state directory has untrusted ownership or mode")
    partial = path.with_name(f"{path.name}.partial")
    abandoned = path.with_name(f"{path.name}.interrupted-partial")
    payload = authorization_payload(journal, child_identity_sha256)
    abandoned_evidence: dict[str, object] | None = None
    if abandoned.exists() or abandoned.is_symlink():
        abandoned_payload, _abandoned_identity = read_verified_journal_file(
            abandoned,
            paths,
            "abandoned production authorization partial",
            portage_readable=True,
        )
        abandoned_evidence = {
            "path": os.fspath(abandoned),
            "sha256": hashlib.sha256(abandoned_payload).hexdigest(),
            "size": len(abandoned_payload),
        }
    if path.exists() or path.is_symlink():
        observed, _identity = read_verified_journal_file(
            path, paths, "production authorization", portage_readable=True
        )
        if observed != payload:
            fail("existing production authorization differs from its transaction")
    elif partial.exists() or partial.is_symlink():
        if abandoned_evidence is not None:
            fail("authorization partial and abandoned partial are both visible")
        observed, _identity = read_verified_journal_file(
            partial,
            paths,
            "production authorization partial",
            portage_readable=True,
        )
        if observed == payload:
            os.replace(partial, path)
            fsync_directory(path.parent)
        else:
            os.replace(partial, abandoned)
            fsync_directory(path.parent)
            abandoned_evidence = {
                "path": os.fspath(abandoned),
                "sha256": hashlib.sha256(observed).hexdigest(),
                "size": len(observed),
            }
    if not path.exists() and not path.is_symlink():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        mode = JOURNAL_MODE if paths.test_mode else PRODUCTION_JOURNAL_MODE
        descriptor = -1
        try:
            descriptor = os.open(partial, flags, mode)
            if not paths.test_mode:
                os.fchown(descriptor, 0, production_portage_gid())
                os.fchmod(descriptor, PRODUCTION_JOURNAL_MODE)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    fail("short write while publishing production authorization")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            read_verified_journal_file(
                partial,
                paths,
                "production authorization partial",
                portage_readable=True,
            )
            trigger_failpoint(failpoint, "authorization-after-partial-fsync")
            os.replace(partial, path)
            fsync_directory(path.parent)
        except BaseException:
            if descriptor >= 0:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
            with contextlib.suppress(OSError):
                partial.unlink()
                fsync_directory(partial.parent)
            raise
    observed, _identity = read_verified_journal_file(
        path, paths, "production authorization", portage_readable=True
    )
    if observed != payload:
        fail("production authorization differs after publication")
    return {
        "abandoned_partial": abandoned_evidence,
        "gate_directory_created": True,
        "generation_parent": os.fspath(generation_parent),
        "generation_parent_gid": generation_metadata.st_gid,
        "generation_parent_mode": stat.S_IMODE(generation_metadata.st_mode),
        "generation_parent_uid": generation_metadata.st_uid,
        "path": os.fspath(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def revalidate_transaction_authorization(
    evidence: dict[str, object],
    paths: Paths,
    journal: dict[str, object],
    child_identity_sha256: str,
) -> None:
    if set(evidence) != AUTHORIZATION_EVIDENCE_FIELDS:
        fail("production authorization evidence has an invalid schema")
    path_value = evidence["path"]
    if not isinstance(path_value, str):
        fail("production authorization evidence path is invalid")
    path = Path(path_value)
    payload, _identity = read_verified_journal_file(
        path, paths, "production authorization", portage_readable=True
    )
    generation_parent = path.parent.parent
    metadata = generation_parent.lstat()
    abandoned_value = evidence["abandoned_partial"]
    abandoned_path = path.with_name(f"{path.name}.interrupted-partial")
    if abandoned_value is None:
        if abandoned_path.exists() or abandoned_path.is_symlink():
            fail("production authorization has unindexed abandoned partial evidence")
    else:
        if (
            not isinstance(abandoned_value, dict)
            or set(abandoned_value) != ABANDONED_PARTIAL_FIELDS
            or abandoned_value["path"] != os.fspath(abandoned_path)
        ):
            fail("production authorization abandoned-partial evidence is invalid")
        abandoned_payload, _abandoned_identity = read_verified_journal_file(
            abandoned_path,
            paths,
            "abandoned production authorization partial",
            portage_readable=True,
        )
        if (
            abandoned_value["size"] != len(abandoned_payload)
            or abandoned_value["sha256"]
            != hashlib.sha256(abandoned_payload).hexdigest()
        ):
            fail("production authorization abandoned-partial evidence changed")
    if (
        payload != authorization_payload(journal, child_identity_sha256)
        or evidence["sha256"] != hashlib.sha256(payload).hexdigest()
        or evidence["gate_directory_created"] is not True
        or evidence["generation_parent"] != os.fspath(generation_parent)
        or evidence["generation_parent_uid"] != metadata.st_uid
        or evidence["generation_parent_gid"] != metadata.st_gid
        or evidence["generation_parent_mode"]
        != stat.S_IMODE(metadata.st_mode)
    ):
        fail("production authorization evidence differs from its transaction")


def prepare_gate_work_root(
    paths: Paths, journal: dict[str, object]
) -> None:
    generation = validate_generation(journal["generation"], "journal generation")
    gate_run_id = require_safe_id(journal["gate_run_id"], "journal gate run ID")
    work_root, _profile_root, _state_root, _output = derived_gate_artifact_paths(
        paths, generation, gate_run_id
    )
    expected_uid = os.geteuid() if paths.test_mode else 0
    expected_gid = os.getegid() if paths.test_mode else 0
    expected_mode = 0o700 if paths.test_mode else 0o755
    if not work_root.exists() and not work_root.is_symlink():
        parent = work_root.parent
        validate_directory_chain(parent, paths, "gate work parent")
        metadata = parent.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or (not paths.test_mode and metadata.st_gid != 0)
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            fail("gate work parent has untrusted ownership or mode")
        os.mkdir(work_root, expected_mode)
        if not paths.test_mode:
            os.chown(work_root, 0, 0)
            os.chmod(work_root, expected_mode)
        fsync_directory(parent)
    metadata = work_root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or stat.S_IMODE(metadata.st_mode) != expected_mode
    ):
        fail("gate work root has untrusted ownership or mode")


def production_receipt_paths(
    paths: Paths, generation: dict[str, str]
) -> tuple[Path, Path, Path]:
    receipt = transaction_receipt_path(paths, generation)
    partial = receipt.with_name(f"{receipt.name}.partial")
    abandoned = receipt.with_name(f"{receipt.name}.interrupted-partial")
    validate_parent(receipt, paths, "production-lock transaction receipt")
    return receipt, partial, abandoned


def reject_existing_receipt_state(
    paths: Paths, generation: dict[str, str]
) -> None:
    for marker in production_receipt_paths(paths, generation):
        if marker.exists() or marker.is_symlink():
            fail(
                "requested generation already has transaction receipt state: "
                f"{marker}"
            )


def validate_receipt_document(
    value: object,
    journal: dict[str, object] | None,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != RECEIPT_FIELDS:
        fail("transaction receipt has an invalid schema")
    if value["schema"] != RECEIPT_SCHEMA:
        fail("transaction receipt has an invalid schema identity")
    status = value["status"]
    child_status = value["child_exit_status"]
    if status not in {"passed", "failed", "recovered-interrupted"}:
        fail("transaction receipt has an invalid completion status")
    if status == "recovered-interrupted":
        if child_status is not None:
            fail("recovered transaction receipt must have an unknown child status")
    elif (
        not isinstance(child_status, int)
        or isinstance(child_status, bool)
        or not 0 <= child_status <= 255
        or (status == "passed") is not (child_status == 0)
    ):
        fail("transaction receipt child status is inconsistent")
    if value["journal_removal_after_receipt_required"] is not True:
        fail("transaction receipt does not require receipt-before-journal ordering")
    if value["lock_payload_restored_sha256"] != EMPTY_SHA256:
        fail("transaction receipt does not prove exact empty lock restoration")
    generation = validate_generation(value["generation"], "receipt generation")
    framework_context = validate_framework_context(value["framework_context"])
    gate_run_id = require_safe_id(
        value["gate_run_id"], "receipt production gate run ID"
    )
    authorization_sha = require_hex(
        value["authorization_token_sha256"],
        64,
        "receipt authorization token digest",
    )
    journal_sha = require_hex(
        value["transaction_journal_sha256"], 64, "receipt journal digest"
    )
    embedded_journal = validate_journal_document(value["transaction_journal"])
    if (
        hashlib.sha256(canonical_json(embedded_journal)).hexdigest()
        != journal_sha
    ):
        fail("transaction receipt does not preserve its exact journal document")
    embedded_generation = validate_generation(
        embedded_journal["generation"], "embedded journal generation"
    )
    embedded_framework = validate_framework_context(
        embedded_journal["framework_context"]
    )
    embedded_authorization = require_hex(
        embedded_journal["authorization_token_sha256"],
        64,
        "embedded journal authorization token digest",
    )
    if (
        embedded_generation != generation
        or embedded_framework != framework_context
        or embedded_journal["gate_run_id"] != gate_run_id
        or embedded_authorization != authorization_sha
        or embedded_journal["expected_payload_sha256"]
        != hashlib.sha256(canonical_payload(generation)).hexdigest()
    ):
        fail("transaction receipt fields differ from its embedded journal")
    child_sha_value = value["child_identity_sha256"]
    if child_sha_value is not None:
        require_hex(child_sha_value, 64, "receipt child identity digest")
    if not isinstance(value["boot_id"], str) or not value["boot_id"]:
        fail("transaction receipt has an invalid boot identity")
    if not isinstance(value["completed_at"], str) or not value["completed_at"]:
        fail("transaction receipt has an invalid completion time")
    token_scan_evidence = value["token_scan"]
    authorization_evidence = value["authorization"]
    if status == "recovered-interrupted":
        if authorization_evidence is not None and (
            not isinstance(authorization_evidence, dict)
            or set(authorization_evidence) != AUTHORIZATION_EVIDENCE_FIELDS
        ):
            fail("recovered transaction authorization evidence is invalid")
    elif (
        not isinstance(authorization_evidence, dict)
        or set(authorization_evidence) != AUTHORIZATION_EVIDENCE_FIELDS
    ):
        fail("completed transaction receipt lacks authorization evidence")
    if isinstance(authorization_evidence, dict):
        authorization_path_value = authorization_evidence["path"]
        if not isinstance(authorization_path_value, str):
            fail("transaction authorization evidence path is invalid")
        require_safe_path(
            Path(authorization_path_value), "transaction authorization evidence path"
        )
        require_hex(
            authorization_evidence["sha256"],
            64,
            "transaction authorization evidence digest",
        )
        if authorization_evidence["gate_directory_created"] is not True:
            fail("transaction authorization does not record its gate directory")
        generation_parent_value = authorization_evidence["generation_parent"]
        if not isinstance(generation_parent_value, str):
            fail("transaction authorization generation parent is invalid")
        require_safe_path(
            Path(generation_parent_value),
            "transaction authorization generation parent",
        )
        for key in (
            "generation_parent_uid",
            "generation_parent_gid",
            "generation_parent_mode",
        ):
            item = authorization_evidence[key]
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                fail(f"transaction authorization {key} is invalid")
        abandoned_authorization = authorization_evidence["abandoned_partial"]
        if abandoned_authorization is not None:
            if (
                not isinstance(abandoned_authorization, dict)
                or set(abandoned_authorization) != ABANDONED_PARTIAL_FIELDS
            ):
                fail("transaction authorization abandoned partial is invalid")
            abandoned_path_value = abandoned_authorization["path"]
            if not isinstance(abandoned_path_value, str):
                fail("transaction authorization abandoned partial path is invalid")
            require_safe_path(
                Path(abandoned_path_value),
                "transaction authorization abandoned partial",
            )
            require_hex(
                abandoned_authorization["sha256"],
                64,
                "transaction authorization abandoned partial digest",
            )
            abandoned_size = abandoned_authorization["size"]
            if (
                not isinstance(abandoned_size, int)
                or isinstance(abandoned_size, bool)
                or not 0 <= abandoned_size <= 65536
            ):
                fail("transaction authorization abandoned partial size is invalid")
        embedded_child_contract = validate_child_contract(
            embedded_journal["child_contract"]
        )
        embedded_environment = embedded_child_contract["environment"]
        if (
            not isinstance(embedded_environment, dict)
            or embedded_environment.get(
                "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION"
            )
            != authorization_path_value
        ):
            fail("transaction authorization path differs from its child contract")
    if status == "recovered-interrupted":
        if token_scan_evidence is not None:
            fail("recovered transaction receipt cannot claim a completed token scan")
    else:
        if (
            not isinstance(token_scan_evidence, dict)
            or set(token_scan_evidence) != TOKEN_SCAN_EVIDENCE_FIELDS
        ):
            fail("transaction receipt token scan evidence is invalid")
        scanner_status = token_scan_evidence["scanner_status"]
        if (
            not isinstance(scanner_status, int)
            or isinstance(scanner_status, bool)
            or not 0 <= scanner_status <= 255
            or (status == "passed" and scanner_status != 0)
        ):
            fail("transaction receipt token scanner status is invalid")
        require_hex(
            token_scan_evidence["output_sha256"],
            64,
            "transaction receipt token scan output digest",
        )
        token_scan_contract = validate_child_contract(
            embedded_journal["child_contract"]
        )["token_scan"]
        if not isinstance(token_scan_contract, dict):  # pragma: no cover - validated.
            fail("internal error: embedded token scan contract is invalid")
        scanner_identity = validate_executable_identity(
            token_scan_contract["executable"], "embedded token scanner"
        )
        if (
            token_scan_evidence["output"] != token_scan_contract["output"]
            or token_scan_evidence["roots"] != token_scan_contract["roots"]
            or token_scan_evidence["scanner_executable_sha256"]
            != scanner_identity["sha256"]
        ):
            fail("transaction receipt token scan differs from its child contract")
    locks = value["locks"]
    if not isinstance(locks, dict) or set(locks) != {
        "framework",
        "project",
        "generation",
    }:
        fail("transaction receipt has an invalid lock identity set")
    for key in ("framework", "project", "generation"):
        FileIdentity.from_json(locks[key], f"receipt {key}")
    abandoned = value["abandoned_receipt_partial"]
    if abandoned is not None:
        if not isinstance(abandoned, dict) or set(abandoned) != ABANDONED_PARTIAL_FIELDS:
            fail("transaction receipt has invalid abandoned-partial evidence")
        abandoned_path = abandoned["path"]
        if not isinstance(abandoned_path, str):
            fail("transaction receipt abandoned-partial path is invalid")
        require_safe_path(Path(abandoned_path), "abandoned receipt partial")
        require_hex(abandoned["sha256"], 64, "abandoned receipt partial digest")
        if (
            not isinstance(abandoned["size"], int)
            or isinstance(abandoned["size"], bool)
            or not 0 <= abandoned["size"] <= 65536
        ):
            fail("transaction receipt abandoned-partial size is invalid")
    if journal is not None:
        if embedded_journal != journal:
            fail("transaction receipt embedded journal differs from the live journal")
        if journal_sha != hashlib.sha256(canonical_json(journal)).hexdigest():
            fail("transaction receipt is not bound to the exact journal bytes")
        if generation != validate_generation(journal["generation"], "journal generation"):
            fail("transaction receipt generation differs from its journal")
        if framework_context != validate_framework_context(journal["framework_context"]):
            fail("transaction receipt framework context differs from its journal")
        if authorization_sha != journal["authorization_token_sha256"]:
            fail("transaction receipt authorization differs from its journal")
    return value


def load_receipt_file(
    path: Path,
    paths: Paths,
    journal: dict[str, object] | None,
    label: str,
) -> tuple[dict[str, object], FileIdentity]:
    identity = inspect_journal_file(path, paths, label)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if FileIdentity.from_stat(os.fstat(descriptor)) != identity:
            fail(f"{label} changed while it was opened")
        payload = read_descriptor(descriptor, label)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"{label} is not valid JSON: {error}")
    if canonical_json(value) != payload:
        fail(f"{label} is not canonical JSON")
    validated = validate_receipt_document(value, journal)
    return validated, identity


def publish_production_receipt(
    paths: Paths,
    receipt_paths: tuple[Path, Path, Path],
    document: dict[str, object],
    journal: dict[str, object],
    failpoint: str | None,
) -> Path:
    receipt, partial, abandoned = receipt_paths
    validate_receipt_document(document, journal)
    if (
        receipt.exists()
        or receipt.is_symlink()
        or partial.exists()
        or partial.is_symlink()
    ):
        fail("production transaction receipt destination is no longer empty")
    validate_receipt_artifacts(paths, receipt_paths, document)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(partial, flags, JOURNAL_MODE)
        payload = canonical_json(document)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                fail("short write while publishing the transaction receipt")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        inspect_journal_file(partial, paths, "transaction receipt partial")
        trigger_failpoint(failpoint, "receipt-after-partial-fsync")
        os.replace(partial, receipt)
        fsync_directory(receipt.parent)
        trigger_failpoint(failpoint, "receipt-after-final-rename")
        loaded, _identity = load_receipt_file(
            receipt, paths, journal, "transaction receipt"
        )
        if loaded != document:
            fail("published transaction receipt differs from its canonical bytes")
        validate_receipt_artifacts(paths, receipt_paths, loaded)
        return receipt
    except BaseException:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise


def load_journal(paths: Paths) -> tuple[dict[str, object], FileIdentity]:
    identity = inspect_journal_file(
        paths.journal,
        paths,
        "transaction journal",
        portage_readable=True,
    )
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(paths.journal, flags)
    try:
        if FileIdentity.from_stat(os.fstat(descriptor)) != identity:
            fail("transaction journal changed while it was opened")
        payload = read_descriptor(descriptor, "transaction journal")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"transaction journal is not valid JSON: {error}")
    if canonical_json(value) != payload:
        fail("transaction journal is not canonical JSON")
    return validate_journal_document(value, paths), identity


def receipt_document(
    journal: dict[str, object],
    framework_identity: FileIdentity,
    project_identity: FileIdentity,
    generation_identity: FileIdentity,
    *,
    status: str,
    child_exit_status: int | None,
    child_identity_sha256: str | None,
    authorization: dict[str, object] | None,
    token_scan: dict[str, object] | None,
    abandoned_receipt_partial: dict[str, object] | None,
) -> dict[str, object]:
    return validate_receipt_document(
        {
            "abandoned_receipt_partial": abandoned_receipt_partial,
            "authorization_token_sha256": journal[
                "authorization_token_sha256"
            ],
            "authorization": authorization,
            "boot_id": boot_id(),
            "child_exit_status": child_exit_status,
            "child_identity_sha256": child_identity_sha256,
            "completed_at": dt.datetime.now(dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "framework_context": journal["framework_context"],
            "gate_run_id": journal["gate_run_id"],
            "generation": journal["generation"],
            "journal_removal_after_receipt_required": True,
            "lock_payload_restored_sha256": EMPTY_SHA256,
            "locks": {
                "framework": framework_identity.as_json(),
                "generation": generation_identity.as_json(),
                "project": project_identity.as_json(),
            },
            "schema": RECEIPT_SCHEMA,
            "status": status,
            "transaction_journal": journal,
            "transaction_journal_sha256": hashlib.sha256(
                canonical_json(journal)
            ).hexdigest(),
            "token_scan": token_scan,
        },
        journal,
    )


def verify_abandoned_receipt_partial(
    path: Path,
    paths: Paths,
    evidence: object,
) -> None:
    if not isinstance(evidence, dict) or set(evidence) != ABANDONED_PARTIAL_FIELDS:
        fail("abandoned receipt-partial evidence is absent or malformed")
    if evidence["path"] != os.fspath(path):
        fail("abandoned receipt-partial evidence names a different path")
    identity = inspect_journal_file(path, paths, "abandoned transaction receipt partial")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if FileIdentity.from_stat(os.fstat(descriptor)) != identity:
            fail("abandoned transaction receipt partial changed while opened")
        payload = read_descriptor(descriptor, "abandoned transaction receipt partial")
    finally:
        os.close(descriptor)
    if (
        evidence["size"] != len(payload)
        or evidence["sha256"] != hashlib.sha256(payload).hexdigest()
    ):
        fail("abandoned receipt-partial bytes differ from their receipt evidence")


def preserve_abandoned_receipt_partial(
    path: Path,
    abandoned: Path,
    paths: Paths,
) -> dict[str, object]:
    if abandoned.exists() or abandoned.is_symlink():
        fail("an abandoned transaction receipt partial is already preserved")
    identity = inspect_journal_file(path, paths, "interrupted transaction receipt partial")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if FileIdentity.from_stat(os.fstat(descriptor)) != identity:
            fail("interrupted transaction receipt partial changed while opened")
        payload = read_descriptor(descriptor, "interrupted transaction receipt partial")
    finally:
        os.close(descriptor)
    os.replace(path, abandoned)
    fsync_directory(abandoned.parent)
    evidence: dict[str, object] = {
        "path": os.fspath(abandoned),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    verify_abandoned_receipt_partial(abandoned, paths, evidence)
    return evidence


def validate_receipt_artifacts(
    paths: Paths,
    receipt_paths: tuple[Path, Path, Path],
    document: dict[str, object],
) -> None:
    _receipt, _partial, abandoned = receipt_paths
    evidence = document["abandoned_receipt_partial"]
    if evidence is None:
        if abandoned.exists() or abandoned.is_symlink():
            fail("unreferenced abandoned transaction receipt partial exists")
    else:
        verify_abandoned_receipt_partial(abandoned, paths, evidence)


def owner_is_live(document: dict[str, object]) -> bool:
    if document["boot_id"] != boot_id():
        return False
    owner = document["owner"]
    if not isinstance(owner, dict):  # pragma: no cover - load_journal validates.
        fail("internal error: journal owner is invalid")
    pid = owner.get("pid")
    start_ticks = owner.get("start_ticks")
    if not isinstance(pid, int) or not isinstance(start_ticks, str):
        fail("internal error: journal owner identity is invalid")
    return process_start_ticks(pid) == start_ticks


def trigger_failpoint(failpoint: str | None, expected: str) -> None:
    if failpoint == expected:
        os._exit(FAILPOINT_EXIT[expected])


def remove_journal(paths: Paths, expected_identity: FileIdentity) -> None:
    _document, current_identity = load_journal(paths)
    if current_identity != expected_identity:
        fail("transaction journal inode changed before removal")
    paths.journal.unlink()
    fsync_directory(paths.journal.parent)
    if paths.journal.exists() or paths.journal.is_symlink():
        fail("transaction journal remained visible after durable removal")


def remove_child_identity(paths: Paths, expected_identity: FileIdentity) -> None:
    _document, current_identity = load_child_identity(paths, None)
    if current_identity != expected_identity:
        fail("child identity sidecar inode changed before removal")
    paths.child_identity.unlink()
    fsync_directory(paths.child_identity.parent)
    if paths.child_identity.exists() or paths.child_identity.is_symlink():
        fail("child identity sidecar remained visible after durable removal")


def remove_child_identity_partial(paths: Paths) -> None:
    inspect_journal_file(
        paths.partial_child_identity,
        paths,
        "stale child identity sidecar partial",
        portage_readable=True,
    )
    paths.partial_child_identity.unlink()
    fsync_directory(paths.partial_child_identity.parent)
    if (
        paths.partial_child_identity.exists()
        or paths.partial_child_identity.is_symlink()
    ):
        fail("child identity sidecar partial remained visible after removal")


def clean_stale_partial_locked(
    paths: Paths, project_descriptor: int, generation_descriptor: int
) -> None:
    partial = paths.partial_journal
    if not partial.exists() and not partial.is_symlink():
        return
    inspect_journal_file(
        partial,
        paths,
        "stale transaction journal partial",
        portage_readable=True,
    )
    if (
        read_descriptor(project_descriptor, "project lock") != b""
        or read_descriptor(generation_descriptor, "generation lock") != b""
    ):
        fail("stale journal partial accompanies nonempty generation locks")
    partial.unlink()
    fsync_directory(partial.parent)


def arm_transaction(
    paths: Paths,
    framework_identity: FileIdentity,
    framework_context: dict[str, str],
    authorization_token_sha256: str,
    gate_run_id: str,
    generation: dict[str, str],
    child_contract: dict[str, object],
    timeout: float,
    failpoint: str | None,
) -> dict[str, object]:
    expected_payload = canonical_payload(generation)
    with generation_locks(paths, timeout) as (project, generation_lock):
        project_descriptor, project_identity = project
        generation_descriptor, generation_identity = generation_lock
        # Receipt publication uses these same generation locks.  Repeating the
        # preflight here closes the window in which another same-generation
        # coordinator can publish a receipt after this coordinator's early
        # check but before it arms the production payload.
        reject_existing_receipt_state(paths, generation)
        if paths.journal.exists() or paths.journal.is_symlink():
            fail("cannot arm while a production-lock transaction journal exists")
        for marker in (paths.child_identity, paths.partial_child_identity):
            if marker.exists() or marker.is_symlink():
                fail(f"cannot arm while a child identity marker exists: {marker}")
        clean_stale_partial_locked(paths, project_descriptor, generation_descriptor)
        if read_descriptor(project_descriptor, "project lock") != b"":
            fail("production-lock gate requires an initially empty project lock")
        if read_descriptor(generation_descriptor, "generation lock") != b"":
            fail("production-lock gate requires an initially empty generation lock")
        document = journal_document(
            paths,
            gate_run_id,
            generation,
            framework_context,
            authorization_token_sha256,
            child_contract,
            {
                "framework": framework_identity,
                "project": project_identity,
                "generation": generation_identity,
            },
        )
        write_journal(paths, document)
        write_descriptor(
            project_descriptor,
            expected_payload,
            "project lock",
            partial_failpoint="arm-during-project",
            selected_failpoint=failpoint,
        )
        trigger_failpoint(failpoint, "arm-after-project")
        write_descriptor(
            generation_descriptor,
            expected_payload,
            "generation lock",
            partial_failpoint="arm-during-generation",
            selected_failpoint=failpoint,
        )
        trigger_failpoint(failpoint, "arm-after-generation")
        if (
            read_descriptor(project_descriptor, "project lock") != expected_payload
            or read_descriptor(generation_descriptor, "generation lock")
            != expected_payload
        ):
            fail("armed generation locks do not contain one exact payload")
        return document


def test_pre_arm_pause(arguments: argparse.Namespace, paths: Paths) -> None:
    pause = arguments.test_pre_arm_pause_file
    if pause is None:
        return
    if not paths.test_mode or paths.test_root is None:
        fail("the pre-arm pause is available only with explicit test mode")
    pause = require_safe_path(pause, "test pre-arm pause")
    path_below(pause, paths.test_root, "test pre-arm pause")
    if pause in {
        paths.framework,
        paths.project,
        paths.generation,
        paths.journal,
        paths.partial_journal,
        paths.child_identity,
        paths.partial_child_identity,
    }:
        fail("test pre-arm pause collides with a transaction path")
    validate_parent(pause, paths, "test pre-arm pause")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(pause, flags, JOURNAL_MODE)
    except OSError as error:
        fail(f"cannot create test pre-arm pause: {error}")
    try:
        payload = f"{os.getpid()}\n".encode("ascii")
        if os.write(descriptor, payload) != len(payload):
            fail("short write while creating test pre-arm pause")
        os.fsync(descriptor)
        identity = FileIdentity.from_stat(os.fstat(descriptor))
    finally:
        os.close(descriptor)
    fsync_directory(pause.parent)
    deadline = time.monotonic() + min(arguments.child_timeout_seconds, 30.0)
    while True:
        try:
            observed = FileIdentity.from_stat(pause.lstat())
        except FileNotFoundError:
            break
        except OSError as error:
            fail(f"cannot revalidate test pre-arm pause: {error}")
        if observed != identity:
            fail("test pre-arm pause inode or metadata changed")
        if time.monotonic() >= deadline:
            fail("timed out waiting for test pre-arm pause release")
        time.sleep(0.02)
    fsync_directory(pause.parent)


def existing_abandoned_receipt_evidence(
    abandoned: Path,
    paths: Paths,
) -> dict[str, object]:
    identity = inspect_journal_file(
        abandoned, paths, "abandoned transaction receipt partial"
    )
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(abandoned, flags)
    try:
        if FileIdentity.from_stat(os.fstat(descriptor)) != identity:
            fail("abandoned transaction receipt partial changed while opened")
        payload = read_descriptor(descriptor, "abandoned transaction receipt partial")
    finally:
        os.close(descriptor)
    return {
        "path": os.fspath(abandoned),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def publish_or_reconcile_receipt_locked(
    paths: Paths,
    journal: dict[str, object],
    framework_identity: FileIdentity,
    project_identity: FileIdentity,
    generation_identity: FileIdentity,
    *,
    status: str,
    child_exit_status: int | None,
    child_identity_sha256: str | None,
    authorization: dict[str, object] | None,
    token_scan: dict[str, object] | None,
    failpoint: str | None,
) -> Path:
    generation = validate_generation(journal["generation"], "journal generation")
    receipt_paths = production_receipt_paths(paths, generation)
    receipt, partial, abandoned = receipt_paths
    receipt_exists = receipt.exists() or receipt.is_symlink()
    partial_exists = partial.exists() or partial.is_symlink()
    abandoned_exists = abandoned.exists() or abandoned.is_symlink()
    if receipt_exists and partial_exists:
        fail("final transaction receipt and its partial are simultaneously visible")

    if receipt_exists:
        document, _identity = load_receipt_file(
            receipt, paths, journal, "transaction receipt"
        )
        validate_receipt_artifacts(paths, receipt_paths, document)
    elif partial_exists:
        try:
            document, _identity = load_receipt_file(
                partial, paths, journal, "transaction receipt partial"
            )
        except TransactionError:
            if abandoned_exists:
                fail(
                    "both malformed and previously abandoned receipt partials exist"
                )
            abandoned_evidence = preserve_abandoned_receipt_partial(
                partial, abandoned, paths
            )
            document = receipt_document(
                journal,
                framework_identity,
                project_identity,
                generation_identity,
                status="recovered-interrupted",
                child_exit_status=None,
                child_identity_sha256=child_identity_sha256,
                authorization=None,
                token_scan=None,
                abandoned_receipt_partial=abandoned_evidence,
            )
            receipt = publish_production_receipt(
                paths, receipt_paths, document, journal, failpoint
            )
        else:
            validate_receipt_artifacts(paths, receipt_paths, document)
            os.replace(partial, receipt)
            fsync_directory(receipt.parent)
            trigger_failpoint(failpoint, "receipt-after-final-rename")
            loaded, _identity = load_receipt_file(
                receipt, paths, journal, "promoted transaction receipt"
            )
            if loaded != document:
                fail("promoted transaction receipt changed during publication")
    else:
        abandoned_evidence_value: dict[str, object] | None = (
            existing_abandoned_receipt_evidence(abandoned, paths)
            if abandoned_exists
            else None
        )
        document = receipt_document(
            journal,
            framework_identity,
            project_identity,
            generation_identity,
            status=status,
            child_exit_status=child_exit_status,
            child_identity_sha256=child_identity_sha256,
            authorization=authorization,
            token_scan=token_scan,
            abandoned_receipt_partial=abandoned_evidence_value,
        )
        receipt = publish_production_receipt(
            paths, receipt_paths, document, journal, failpoint
        )

    if child_identity_sha256 is not None:
        if document["child_identity_sha256"] != child_identity_sha256:
            fail("transaction receipt differs from the durable child identity")
    elif document["child_identity_sha256"] is not None:
        fail("transaction receipt claims an unavailable child identity")
    return receipt


def recover_transaction_locked(
    paths: Paths,
    framework_identity: FileIdentity,
    framework_context: dict[str, str],
    timeout: float,
    kill_after: float,
    failpoint: str | None,
    *,
    allow_owner_pid: int | None,
) -> bool:
    journal_exists = paths.journal.exists() or paths.journal.is_symlink()
    partial_exists = paths.partial_journal.exists() or paths.partial_journal.is_symlink()
    if not journal_exists:
        child_exists = paths.child_identity.exists() or paths.child_identity.is_symlink()
        child_partial_exists = (
            paths.partial_child_identity.exists()
            or paths.partial_child_identity.is_symlink()
        )
        if child_exists and child_partial_exists:
            fail("child identity sidecar and its partial are simultaneously visible")
        recovered_marker = partial_exists or child_exists or child_partial_exists
        child_document: dict[str, object] | None = None
        child_identity: FileIdentity | None = None
        if child_exists:
            child_document, child_identity = load_child_identity(paths, None)
            if (
                validate_framework_context(child_document["framework_context"])
                != framework_context
            ):
                fail("orphan child identity belongs to a different framework context")
            owner = validate_owner_identity(
                child_document["coordinator_owner"], "child identity coordinator"
            )
            owner_pid = owner["pid"]
            owner_ticks = owner["start_ticks"]
            if not isinstance(owner_pid, int) or not isinstance(owner_ticks, str):
                fail("internal error: child coordinator identity has invalid types")
            if (
                child_document["boot_id"] == boot_id()
                and process_start_ticks(owner_pid) == owner_ticks
                and owner_pid != allow_owner_pid
            ):
                fail(f"child identity coordinator is still active: PID {owner_pid}")
            quiesce_recorded_process_group(child_document, kill_after)
            generation = validate_generation(
                child_document["generation"], "orphan child generation"
            )
            receipt, receipt_partial, _abandoned = production_receipt_paths(
                paths, generation
            )
            if receipt_partial.exists() or receipt_partial.is_symlink():
                fail("receipt partial exists without its transaction journal")
            if not receipt.exists() and not receipt.is_symlink():
                fail("child identity sidecar exists without a durable final receipt")
            receipt_document_value, _receipt_identity = load_receipt_file(
                receipt, paths, None, "orphan transaction receipt"
            )
            validate_receipt_artifacts(
                paths,
                production_receipt_paths(paths, generation),
                receipt_document_value,
            )
            if (
                receipt_document_value["generation"]
                != child_document["generation"]
                or receipt_document_value["gate_run_id"]
                != child_document["gate_run_id"]
                or receipt_document_value["framework_context"]
                != child_document["framework_context"]
                or receipt_document_value["authorization_token_sha256"]
                != child_document["authorization_token_sha256"]
                or receipt_document_value["transaction_journal_sha256"]
                != child_document["journal_sha256"]
                or receipt_document_value["child_identity_sha256"]
                != hashlib.sha256(canonical_json(child_document)).hexdigest()
            ):
                fail("orphan child identity is not exactly bound to its final receipt")
        elif child_partial_exists:
            fail("child identity partial exists without its transaction journal")
        with generation_locks(paths, timeout) as (project, generation_lock):
            project_descriptor = project[0]
            generation_descriptor = generation_lock[0]
            if partial_exists:
                clean_stale_partial_locked(
                    paths, project_descriptor, generation_descriptor
                )
            if (
                read_descriptor(project_descriptor, "project lock") != b""
                or read_descriptor(generation_descriptor, "generation lock") != b""
            ):
                fail("production locks are nonempty without a recovery journal")
            if child_identity is not None:
                repeated_child, repeated_identity = load_child_identity(paths, None)
                if repeated_child != child_document or repeated_identity != child_identity:
                    fail("orphan child identity changed while recovery locks were acquired")
                remove_child_identity(paths, child_identity)
        return recovered_marker
    if partial_exists:
        fail("transaction journal and its partial are simultaneously visible")
    document, journal_identity = load_journal(paths)
    if validate_framework_context(document["framework_context"]) != framework_context:
        fail("active framework context differs from the transaction journal")
    journal_owner = validate_owner_identity(document["owner"], "journal owner")
    owner_allowed = (
        allow_owner_pid is not None
        and journal_owner["pid"] == allow_owner_pid
        and journal_owner["start_ticks"] == process_start_ticks(allow_owner_pid)
    )
    if owner_is_live(document) and not owner_allowed:
        fail(
            "production-lock transaction owner is still active: "
            f"PID {journal_owner['pid']}"
        )
    child_exists = paths.child_identity.exists() or paths.child_identity.is_symlink()
    child_partial_exists = (
        paths.partial_child_identity.exists()
        or paths.partial_child_identity.is_symlink()
    )
    if child_exists and child_partial_exists:
        fail("child identity sidecar and its partial are simultaneously visible")
    journal_child_document: dict[str, object] | None = None
    journal_child_identity: FileIdentity | None = None
    child_identity_sha256: str | None = None
    if child_exists:
        journal_child_document, journal_child_identity = load_child_identity(
            paths, document
        )
        child_identity_sha256 = hashlib.sha256(
            canonical_json(journal_child_document)
        ).hexdigest()
        quiesce_recorded_process_group(journal_child_document, kill_after)
    elif child_partial_exists:
        inspect_journal_file(
            paths.partial_child_identity,
            paths,
            "child identity sidecar partial",
            portage_readable=True,
        )
    expected_payload = canonical_payload(
        validate_generation(document["generation"], "journal generation")
    )
    recovered_authorization: dict[str, object] | None = None
    if child_identity_sha256 is not None:
        recovered_authorization = publish_transaction_authorization(
            paths, document, child_identity_sha256
        )
    same_boot = document["boot_id"] == boot_id()
    recorded_locks = document["locks"]
    if not isinstance(recorded_locks, dict):  # pragma: no cover - validated.
        fail("internal error: journal locks are invalid")
    if same_boot:
        recorded_framework = FileIdentity.from_json(
            recorded_locks["framework"], "journal framework"
        )
        if framework_identity != recorded_framework:
            fail("same-boot framework lock inode or metadata changed")
    with generation_locks(paths, timeout) as (project, generation_lock):
        project_descriptor, project_identity = project
        generation_descriptor, generation_identity = generation_lock
        # Re-open the journal only after both writer locks are held.
        repeated, repeated_identity = load_journal(paths)
        if repeated != document or repeated_identity != journal_identity:
            fail("transaction journal changed while recovery locks were acquired")
        if journal_child_identity is not None:
            repeated_child, repeated_child_identity = load_child_identity(paths, document)
            if (
                repeated_child != journal_child_document
                or repeated_child_identity != journal_child_identity
            ):
                fail("child identity changed while recovery locks were acquired")
        elif paths.child_identity.exists() or paths.child_identity.is_symlink():
            fail("child identity appeared while recovery locks were acquired")
        if same_boot:
            for key, observed in (
                ("project", project_identity),
                ("generation", generation_identity),
            ):
                recorded = FileIdentity.from_json(
                    recorded_locks[key], f"journal {key}"
                )
                if observed != recorded:
                    fail(f"same-boot {key} lock inode or metadata changed")
            project_payload = read_descriptor(project_descriptor, "project lock")
            generation_payload = read_descriptor(
                generation_descriptor, "generation lock"
            )
            if (
                not expected_payload.startswith(project_payload)
                or not expected_payload.startswith(generation_payload)
            ):
                fail(
                    "generation lock content is neither empty nor the journal payload "
                    "or a recoverable strict prefix"
                )
            write_descriptor(project_descriptor, b"", "project lock restoration")
            trigger_failpoint(failpoint, "restore-after-project")
            write_descriptor(
                generation_descriptor, b"", "generation lock restoration"
            )
        else:
            if (
                read_descriptor(project_descriptor, "project lock") != b""
                or read_descriptor(generation_descriptor, "generation lock") != b""
            ):
                fail("post-reboot production locks are not both empty")
        if (
            read_descriptor(project_descriptor, "project lock") != b""
            or read_descriptor(generation_descriptor, "generation lock") != b""
        ):
            fail("production generation locks were not restored to exact emptiness")
        if owner_allowed:
            return True
        publish_or_reconcile_receipt_locked(
            paths,
            document,
            framework_identity,
            project_identity,
            generation_identity,
            status="recovered-interrupted",
            child_exit_status=None,
            child_identity_sha256=child_identity_sha256,
            authorization=recovered_authorization,
            token_scan=None,
            failpoint=failpoint,
        )
        remove_journal(paths, journal_identity)
        trigger_failpoint(failpoint, "receipt-after-journal-removal")
        if journal_child_identity is not None:
            remove_child_identity(paths, journal_child_identity)
        elif child_partial_exists:
            remove_child_identity_partial(paths)
    return True


def finalize_owned_transaction_locked(
    paths: Paths,
    framework_identity: FileIdentity,
    framework_context: dict[str, str],
    timeout: float,
    kill_after: float,
    failpoint: str | None,
    *,
    child_exit_status: int,
    expected_child_identity_sha256: str,
    authorization: dict[str, object],
    token_scan: dict[str, object],
) -> Path:
    document, journal_identity = load_journal(paths)
    if validate_framework_context(document["framework_context"]) != framework_context:
        fail("active framework context differs before receipt publication")
    owner = validate_owner_identity(document["owner"], "journal owner")
    if (
        owner["pid"] != os.getpid()
        or owner["start_ticks"] != current_start_ticks()
    ):
        fail("only the exact transaction owner may publish its final receipt")
    child_document, child_identity = load_child_identity(paths, document)
    child_identity_sha256 = hashlib.sha256(canonical_json(child_document)).hexdigest()
    if child_identity_sha256 != expected_child_identity_sha256:
        fail("final receipt child identity differs from the supervised child")
    revalidate_transaction_authorization(
        authorization, paths, document, child_identity_sha256
    )
    quiesce_recorded_process_group(child_document, kill_after)

    with generation_locks(paths, timeout) as (project, generation_lock):
        project_descriptor, project_identity = project
        generation_descriptor, generation_identity = generation_lock
        repeated_journal, repeated_journal_identity = load_journal(paths)
        if repeated_journal != document or repeated_journal_identity != journal_identity:
            fail("transaction journal changed before final receipt publication")
        repeated_child, repeated_child_identity = load_child_identity(paths, document)
        if repeated_child != child_document or repeated_child_identity != child_identity:
            fail("child identity changed before final receipt publication")
        if (
            read_descriptor(project_descriptor, "restored project lock") != b""
            or read_descriptor(generation_descriptor, "restored generation lock") != b""
        ):
            fail("final receipt publication requires exactly empty generation locks")
        receipt = publish_or_reconcile_receipt_locked(
            paths,
            document,
            framework_identity,
            project_identity,
            generation_identity,
            status="passed" if child_exit_status == 0 else "failed",
            child_exit_status=child_exit_status,
            child_identity_sha256=child_identity_sha256,
            authorization=authorization,
            token_scan=token_scan,
            failpoint=failpoint,
        )
        remove_journal(paths, journal_identity)
        trigger_failpoint(failpoint, "receipt-after-journal-removal")
        remove_child_identity(paths, child_identity)
    return receipt


def process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_process_group(
    process: subprocess.Popen[bytes], kill_after: float
) -> None:
    process_group = process.pid
    if not process_group_exists(process_group):
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=0.1)
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process_group, signal.SIGTERM)
    deadline = time.monotonic() + kill_after
    while process_group_exists(process_group) and time.monotonic() < deadline:
        time.sleep(0.02)
    if process_group_exists(process_group):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process_group, signal.SIGKILL)
        deadline = time.monotonic() + kill_after
        while process_group_exists(process_group) and time.monotonic() < deadline:
            time.sleep(0.02)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=kill_after)
    if process_group_exists(process_group):
        fail(f"child process group survived SIGKILL: {process_group}")


def quiesce_recorded_process_group(
    document: dict[str, object],
    kill_after: float,
) -> None:
    child = validate_process_identity(document["child"], "recorded child")
    child_pid = child["pid"]
    process_group = child["process_group"]
    start_ticks = child["start_ticks"]
    if (
        not isinstance(child_pid, int)
        or not isinstance(process_group, int)
        or not isinstance(start_ticks, str)
    ):  # pragma: no cover - validate_process_identity narrows at runtime.
        fail("internal error: recorded child identity has invalid types")
    if document["boot_id"] != boot_id():
        return
    observed_start = process_start_ticks(child_pid)
    if observed_start is None:
        if process_group_exists(process_group):
            if process_group == os.getpgrp():
                fail("recorded orphan process group aliases the recovery coordinator")
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process_group, signal.SIGTERM)
            deadline = time.monotonic() + kill_after
            while process_group_exists(process_group) and time.monotonic() < deadline:
                time.sleep(0.02)
            if process_group_exists(process_group):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process_group, signal.SIGKILL)
                deadline = time.monotonic() + kill_after
                while (
                    process_group_exists(process_group)
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
            if process_group_exists(process_group):
                fail(f"recorded orphan process group survived SIGKILL: {process_group}")
        return
    if observed_start != start_ticks:
        fail("recorded child PID was reused by a different process")
    try:
        observed_group = os.getpgid(child_pid)
    except ProcessLookupError:
        observed_group = -1
    if observed_group != process_group:
        fail("recorded child process-group identity changed")
    if child_pid == os.getpid() or process_group == os.getpgrp():
        fail("recorded child identity aliases the recovery coordinator")

    with contextlib.suppress(ProcessLookupError):
        os.killpg(process_group, signal.SIGTERM)
    deadline = time.monotonic() + kill_after
    while process_group_exists(process_group) and time.monotonic() < deadline:
        time.sleep(0.02)
    if process_group_exists(process_group):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process_group, signal.SIGKILL)
        deadline = time.monotonic() + kill_after
        while process_group_exists(process_group) and time.monotonic() < deadline:
            time.sleep(0.02)
    if process_group_exists(process_group):
        fail(f"recorded orphan process group survived SIGKILL: {process_group}")


def normalize_child_status(returncode: int) -> int:
    if returncode < 0:
        return min(255, 128 + -returncode)
    return min(255, returncode)


def preflight_child_containment(
    contract: dict[str, object], paths: Paths
) -> None:
    revalidate_child_contract(contract, paths)
    if contract["containment"] != "pid-namespace-v1":
        return
    if os.geteuid() != 0:
        fail("PID-namespace containment requires root")
    try:
        probe = subprocess.run(
            [
                os.fspath(UNSHARE),
                "--pid",
                "--fork",
                "--kill-child=KILL",
                "--mount-proc",
                "--",
                "/bin/true",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=PRODUCTION_CHILD_ENVIRONMENT,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"PID-namespace containment preflight failed: {error}")
    if probe.returncode != 0:
        diagnostic = probe.stderr.decode("utf-8", errors="replace").strip()
        fail(
            "PID-namespace containment preflight failed"
            + (f": {diagnostic}" if diagnostic else "")
        )
    revalidate_child_contract(contract, paths)


def run_authorization_token_scan(
    contract: dict[str, object],
    authorization_token: str,
    paths: Paths,
    timeout: float,
    kill_after: float,
) -> tuple[int, dict[str, object]]:
    revalidate_child_contract(contract, paths)
    token_scan = contract["token_scan"]
    if not isinstance(token_scan, dict):  # pragma: no cover - contract validated.
        fail("internal error: token scan contract is invalid")
    executable = validate_executable_identity(
        token_scan["executable"], "authorization token scanner"
    )
    executable_path = executable["path"]
    roots = token_scan["roots"]
    output_value = token_scan["output"]
    if (
        not isinstance(executable_path, str)
        or not isinstance(roots, list)
        or not isinstance(output_value, str)
    ):  # pragma: no cover - contract validated.
        fail("internal error: token scan contract fields are invalid")
    output = Path(output_value)
    if output.exists() or output.is_symlink():
        fail(f"authorization token scan output already exists: {output}")
    read_descriptor_fd, write_descriptor_fd = os.pipe2(os.O_CLOEXEC)
    process: subprocess.Popen[bytes] | None = None
    try:
        os.set_inheritable(read_descriptor_fd, True)
        token_payload = authorization_token.encode("ascii") + b"\n"
        if os.write(write_descriptor_fd, token_payload) != len(token_payload):
            fail("short write to authorization token scanner pipe")
        os.close(write_descriptor_fd)
        write_descriptor_fd = -1
        try:
            process = subprocess.Popen(
                [
                    executable_path,
                    "--token-fd",
                    str(read_descriptor_fd),
                    "--output",
                    output_value,
                    *roots,
                ],
                start_new_session=True,
                env=PRODUCTION_CHILD_ENVIRONMENT,
                pass_fds=(read_descriptor_fd,),
            )
        except OSError:
            scanner_status = 125
        else:
            os.close(read_descriptor_fd)
            read_descriptor_fd = -1
            try:
                scanner_status = normalize_child_status(process.wait(timeout=timeout))
            except subprocess.TimeoutExpired:
                terminate_process_group(process, kill_after)
                scanner_status = 124
            if process_group_exists(process.pid):
                terminate_process_group(process, kill_after)
                fail("authorization token scanner left a live process group")
    finally:
        for descriptor in (read_descriptor_fd, write_descriptor_fd):
            if descriptor >= 0:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
        if process is not None and process_group_exists(process.pid):
            terminate_process_group(process, kill_after)
    if not output.exists() and not output.is_symlink():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(output, flags, 0o600)
        try:
            fallback = (
                f"status\tscanner-no-output\nscanner_status\t{scanner_status}\n"
            ).encode("ascii")
            if os.write(descriptor, fallback) != len(fallback):
                fail("short write while publishing scanner failure evidence")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        fsync_directory(output.parent)
        if scanner_status == 0:
            scanner_status = 125
    if not output.is_file() or output.is_symlink():
        fail("authorization token scan output is not a real regular file")
    output_metadata = output.lstat()
    expected_uid = os.geteuid() if paths.test_mode else 0
    if (
        output_metadata.st_uid != expected_uid
        or stat.S_IMODE(output_metadata.st_mode) != 0o600
        or output_metadata.st_nlink != 1
    ):
        fail("authorization token scan output has untrusted metadata")
    output_payload = output.read_bytes()
    evidence: dict[str, object] = {
        "output": output_value,
        "output_sha256": hashlib.sha256(output_payload).hexdigest(),
        "roots": roots,
        "scanner_executable_sha256": executable["sha256"],
        "scanner_status": scanner_status,
    }
    revalidate_child_contract(contract, paths)
    return scanner_status, evidence


def recover_token_scan_evidence(
    contract: dict[str, object], paths: Paths
) -> dict[str, object]:
    token_scan = validate_child_contract(contract)["token_scan"]
    if not isinstance(token_scan, dict):  # pragma: no cover - validated.
        fail("internal error: token scan contract is invalid")
    output_value = token_scan["output"]
    roots = token_scan["roots"]
    scanner = validate_executable_identity(
        token_scan["executable"], "authorization token scanner"
    )
    if not isinstance(output_value, str) or not isinstance(roots, list):
        fail("internal error: token scan recovery contract is invalid")
    output = Path(output_value)
    if not output.exists() and not output.is_symlink():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(output, flags, 0o600)
        try:
            payload = b"status\tcoordinator-exception\nscanner_status\t125\n"
            if os.write(descriptor, payload) != len(payload):
                fail("short write while publishing recovered token scan evidence")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        fsync_directory(output.parent)
    if not output.is_file() or output.is_symlink():
        fail("recovered token scan evidence is not a regular file")
    payload = output.read_bytes()
    return {
        "output": output_value,
        "output_sha256": hashlib.sha256(payload).hexdigest(),
        "roots": roots,
        "scanner_executable_sha256": scanner["sha256"],
        "scanner_status": 125,
    }


def install_parent_death_signal(parent_pid: int, parent_start_ticks: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        fail(f"cannot install child parent-death signal: errno={error_number}")
    if (
        os.getppid() != parent_pid
        or process_start_ticks(parent_pid) != parent_start_ticks
    ):
        fail("child barrier coordinator disappeared while installing parent-death signal")


def child_barrier_exec(arguments: Sequence[str]) -> int:
    if len(arguments) < 6 or arguments[4] != "--":
        print("ERROR: malformed internal child barrier invocation", file=sys.stderr)
        return 125
    try:
        barrier_descriptor = int(arguments[0])
        parent_pid = int(arguments[1])
    except ValueError:
        print("ERROR: malformed internal child barrier identity", file=sys.stderr)
        return 125
    parent_start_ticks = arguments[2]
    containment = arguments[3]
    command = list(arguments[5:])
    if (
        barrier_descriptor < 3
        or parent_pid < 2
        or not parent_start_ticks.isdigit()
        or containment not in {"direct-test-v1", "pid-namespace-v1"}
        or not command
        or not Path(command[0]).is_absolute()
    ):
        print("ERROR: unsafe internal child barrier identity", file=sys.stderr)
        return 125
    try:
        install_parent_death_signal(parent_pid, parent_start_ticks)
    except TransactionError as error:
        print(f"ERROR: child barrier pdeath setup failed: {error}", file=sys.stderr)
        return 125
    try:
        release = os.read(barrier_descriptor, 2)
    except OSError as error:
        print(f"ERROR: child barrier read failed: {error}", file=sys.stderr)
        return 125
    finally:
        with contextlib.suppress(OSError):
            os.close(barrier_descriptor)
    if release != b"G":
        print("ERROR: child barrier closed without a release grant", file=sys.stderr)
        return 125
    if (
        os.getppid() != parent_pid
        or process_start_ticks(parent_pid) != parent_start_ticks
    ):
        print("ERROR: child barrier coordinator identity disappeared", file=sys.stderr)
        return 125
    try:
        if containment == "pid-namespace-v1":
            command = [
                os.fspath(UNSHARE),
                "--pid",
                "--fork",
                "--kill-child=KILL",
                "--mount-proc",
                "--",
                *command,
            ]
        os.execve(command[0], command, os.environ)
    except OSError as error:
        print(f"ERROR: child barrier exec failed: {error}", file=sys.stderr)
        return 125


def run_child(
    arguments: argparse.Namespace,
    authorization_token: str,
    paths: Paths,
    journal: dict[str, object],
) -> tuple[int, str, dict[str, object], dict[str, object]]:
    contract = validate_child_contract(journal["child_contract"])
    command_value = contract["argv"]
    if not isinstance(command_value, list):  # pragma: no cover - validated above.
        fail("internal error: child argv is invalid")
    command = list(command_value)
    process: subprocess.Popen[bytes] | None = None
    caught_signal: int | None = None
    previous_handlers: dict[signal.Signals, Any] = {}
    read_descriptor_fd = -1
    write_descriptor_fd = -1
    child_identity_sha256: str | None = None

    def interrupted(signum: int, _frame: object) -> NoReturn:
        raise TransactionInterrupted(signum)

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupted)
    try:
        token_sha256 = hashlib.sha256(authorization_token.encode("ascii")).hexdigest()
        if journal.get("authorization_token_sha256") != token_sha256:
            fail("child authorization token is not bound to the transaction journal")
        environment_value = contract["environment"]
        if not isinstance(environment_value, dict):  # pragma: no cover - validated.
            fail("internal error: child environment contract is invalid")
        child_environment = dict(environment_value)
        expected_token_sha256 = child_environment.pop(
            "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN_SHA256", None
        )
        if expected_token_sha256 != token_sha256:
            fail("child environment token digest differs from its journal")
        child_environment["GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN"] = (
            authorization_token
        )
        revalidate_child_contract(contract, paths)
        read_descriptor_fd, write_descriptor_fd = os.pipe2(os.O_CLOEXEC)
        os.set_inheritable(read_descriptor_fd, True)
        parent_start_ticks = current_start_ticks()
        barrier_command = [
            sys.executable,
            os.fspath(Path(__file__).resolve()),
            "__child-barrier",
            str(read_descriptor_fd),
            str(os.getpid()),
            parent_start_ticks,
            str(contract["containment"]),
            "--",
            *command,
        ]
        process = subprocess.Popen(
            barrier_command,
            start_new_session=True,
            env=child_environment,
            pass_fds=(read_descriptor_fd,),
        )
        os.close(read_descriptor_fd)
        read_descriptor_fd = -1
        child_start_ticks = process_start_ticks(process.pid)
        try:
            child_process_group = os.getpgid(process.pid)
        except ProcessLookupError:
            child_process_group = -1
        if (
            child_start_ticks is None
            or child_process_group != process.pid
            or process.poll() is not None
        ):
            fail("child barrier exited before its identity could be recorded")
        trigger_failpoint(arguments.failpoint, "child-after-spawn")
        sidecar = write_child_identity(
            paths,
            journal,
            {
                "pid": process.pid,
                "process_group": child_process_group,
                "start_ticks": child_start_ticks,
            },
            arguments.failpoint,
        )
        child_identity_sha256 = hashlib.sha256(canonical_json(sidecar)).hexdigest()
        trigger_failpoint(arguments.failpoint, "child-after-sidecar")
        authorization_evidence = publish_transaction_authorization(
            paths, journal, child_identity_sha256, arguments.failpoint
        )
        prepare_gate_work_root(paths, journal)
        if os.write(write_descriptor_fd, b"G") != 1:
            fail("child barrier release grant was not written atomically")
        os.close(write_descriptor_fd)
        write_descriptor_fd = -1
        trigger_failpoint(arguments.failpoint, "child-after-release")
        try:
            returncode = process.wait(timeout=arguments.child_timeout_seconds)
        except subprocess.TimeoutExpired:
            terminate_process_group(process, arguments.kill_after_seconds)
            child_status = 124
        except TransactionInterrupted as error:
            caught_signal = error.signum
            terminate_process_group(process, arguments.kill_after_seconds)
            child_status = min(255, 128 + error.signum)
        else:
            child_status = normalize_child_status(returncode)
        if process_group_exists(process.pid):
            terminate_process_group(process, arguments.kill_after_seconds)
            if child_status == 0:
                fail("child exited successfully while its process group remained alive")
        revalidate_child_contract(contract, paths)
        scanner_status, token_scan_evidence = run_authorization_token_scan(
            contract,
            authorization_token,
            paths,
            arguments.token_scan_timeout_seconds,
            arguments.kill_after_seconds,
        )
        if scanner_status != 0 and child_status == 0:
            child_status = scanner_status
        revalidate_transaction_authorization(
            authorization_evidence, paths, journal, child_identity_sha256
        )
        return (
            child_status,
            child_identity_sha256,
            authorization_evidence,
            token_scan_evidence,
        )
    finally:
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, signal.SIG_IGN)
        if process is not None and process_group_exists(process.pid):
            terminate_process_group(process, arguments.kill_after_seconds)
        for descriptor in (read_descriptor_fd, write_descriptor_fd):
            if descriptor >= 0:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if caught_signal is not None:
            print(f"INTERRUPTED: signal={caught_signal}", file=sys.stderr)


def validate_timeouts(arguments: argparse.Namespace) -> None:
    if not 0 < arguments.lock_timeout_seconds <= 300:
        fail("lock timeout must be greater than zero and at most 300 seconds")
    if hasattr(arguments, "child_timeout_seconds"):
        if not 0 < arguments.child_timeout_seconds <= 86400:
            fail("child timeout must be greater than zero and at most 86400 seconds")
        if not 0 < arguments.token_scan_timeout_seconds <= 3600:
            fail("token scan timeout must be greater than zero and at most 3600 seconds")
    if not 0 < arguments.kill_after_seconds <= 60:
        fail("kill-after timeout must be greater than zero and at most 60 seconds")


def run_command(arguments: argparse.Namespace) -> int:
    validate_timeouts(arguments)
    paths = resolve_paths(arguments)
    if arguments.failpoint is not None and not paths.test_mode:
        fail("failpoints are forbidden on production lock paths")
    generation = validate_generation(
        {
            "generation_id": arguments.generation_id,
            "inventory_id": arguments.inventory_id,
            "inventory_sha256": arguments.inventory_sha256,
        },
        "requested generation",
    )
    gate_run_id = require_safe_id(arguments.gate_run_id, "production gate run ID")
    forbidden_environment = sorted(
        name
        for name in os.environ
        if name.startswith("GENTOO_OPT_")
        or name in FORBIDDEN_COORDINATOR_ENVIRONMENT
    )
    if forbidden_environment:
        fail(
            "production-lock coordinator inherited forbidden environment variables: "
            + ", ".join(forbidden_environment)
        )
    authorization_token = secrets.token_hex(32)
    authorization_token_sha256 = hashlib.sha256(
        authorization_token.encode("ascii")
    ).hexdigest()
    authorization_path = transaction_authorization_path(
        paths, generation, gate_run_id
    )
    child_contract = build_child_contract(
        arguments,
        paths,
        generation,
        gate_run_id,
        authorization_token_sha256,
        authorization_path,
    )
    preflight_child_containment(child_contract, paths)
    receipt_path: Path | None = None
    child_status = 1
    child_identity_sha256: str | None = None
    authorization_evidence: dict[str, object] | None = None
    token_scan_evidence: dict[str, object] | None = None
    armed_journal: dict[str, object] | None = None
    with framework_lock(paths, arguments.lock_timeout_seconds) as (_descriptor, identity):
        framework_context = active_framework_context(paths)
        recover_transaction_locked(
            paths,
            identity,
            framework_context,
            arguments.lock_timeout_seconds,
            arguments.kill_after_seconds,
            None,
            allow_owner_pid=None,
        )
        reject_existing_receipt_state(paths, generation)
        test_pre_arm_pause(arguments, paths)
        try:
            armed_journal = arm_transaction(
                paths,
                identity,
                framework_context,
                authorization_token_sha256,
                gate_run_id,
                generation,
                child_contract,
                arguments.lock_timeout_seconds,
                arguments.failpoint,
            )
            (
                child_status,
                child_identity_sha256,
                authorization_evidence,
                token_scan_evidence,
            ) = run_child(arguments, authorization_token, paths, armed_journal)
        finally:
            if armed_journal is not None:
                if active_framework_context(paths) != framework_context:
                    fail("active framework context changed during the production-lock gate")
                recover_transaction_locked(
                    paths,
                    identity,
                    framework_context,
                    arguments.lock_timeout_seconds,
                    arguments.kill_after_seconds,
                    arguments.failpoint,
                    allow_owner_pid=os.getpid(),
                )
                if child_identity_sha256 is None:
                    if paths.child_identity.exists() and not paths.child_identity.is_symlink():
                        durable_child, _durable_child_identity = load_child_identity(
                            paths, armed_journal
                        )
                        child_identity_sha256 = hashlib.sha256(
                            canonical_json(durable_child)
                        ).hexdigest()
                    else:
                        fail(
                            "child failed before a durable identity was available; "
                            "run explicit recovery"
                        )
                if authorization_evidence is None:
                    authorization_evidence = publish_transaction_authorization(
                        paths, armed_journal, child_identity_sha256
                    )
                if token_scan_evidence is None:
                    try:
                        scanner_status, token_scan_evidence = (
                            run_authorization_token_scan(
                                child_contract,
                                authorization_token,
                                paths,
                                arguments.token_scan_timeout_seconds,
                                arguments.kill_after_seconds,
                            )
                        )
                    except TransactionError:
                        token_scan_evidence = recover_token_scan_evidence(
                            child_contract, paths
                        )
                        scanner_status = 125
                    if scanner_status != 0 and child_status == 0:
                        child_status = scanner_status
                receipt_path = finalize_owned_transaction_locked(
                    paths,
                    identity,
                    framework_context,
                    arguments.lock_timeout_seconds,
                    arguments.kill_after_seconds,
                    arguments.failpoint,
                    child_exit_status=child_status,
                    expected_child_identity_sha256=child_identity_sha256,
                    authorization=authorization_evidence,
                    token_scan=token_scan_evidence,
                )
    if receipt_path is not None:
        print(f"RECEIPT: {receipt_path}")
    return child_status


def recover_command(arguments: argparse.Namespace) -> int:
    validate_timeouts(arguments)
    paths = resolve_paths(arguments)
    if arguments.failpoint is not None and not paths.test_mode:
        fail("failpoints are forbidden on production lock paths")
    with framework_lock(paths, arguments.lock_timeout_seconds) as (
        _descriptor,
        identity,
    ):
        framework_context = active_framework_context(paths)
        recovered = recover_transaction_locked(
            paths,
            identity,
            framework_context,
            arguments.lock_timeout_seconds,
            arguments.kill_after_seconds,
            arguments.failpoint,
            allow_owner_pid=None,
        )
    print("RECOVERED" if recovered else "CLEAN")
    return 0


def read_authorization_token_descriptor(descriptor: int) -> str:
    if descriptor < 0:
        fail("authorization token descriptor is negative")
    try:
        payload = os.read(descriptor, 66)
        trailing = os.read(descriptor, 1)
    except OSError as error:
        fail(f"cannot read authorization token descriptor: {error}")
    if len(payload) != 65 or not payload.endswith(b"\n") or trailing:
        fail("authorization token descriptor must contain exactly 64 hex bytes and newline")
    try:
        token = payload[:-1].decode("ascii")
    except UnicodeDecodeError:
        fail("authorization token descriptor is not ASCII")
    require_hex(token, 64, "authorization token")
    return token


def reject_active_transaction_partials(
    paths: Paths, authorization_path: Path
) -> None:
    """Refuse authorization while any unpublished transaction object exists."""
    candidates = (
        (paths.partial_journal, "journal"),
        (paths.partial_child_identity, "child identity"),
        (
            authorization_path.with_name(f"{authorization_path.name}.partial"),
            "authorization",
        ),
    )
    for partial, label in candidates:
        try:
            partial.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            fail(f"cannot inspect active {label} partial {partial}: {error}")
        fail(
            "active authorization is blocked by a partial transaction object: "
            f"{partial}"
        )


def verify_active_command(arguments: argparse.Namespace) -> int:
    validate_timeouts(arguments)
    paths = resolve_paths(arguments)
    authorization_path = require_safe_path(
        arguments.authorization, "active authorization path"
    )
    reject_active_transaction_partials(paths, authorization_path)
    token = read_authorization_token_descriptor(arguments.token_fd)
    token_sha256 = hashlib.sha256(token.encode("ascii")).hexdigest()
    with framework_lock(paths, arguments.lock_timeout_seconds) as (
        _framework_descriptor,
        framework_identity,
    ):
        framework_context = active_framework_context(paths)
        journal, journal_identity = load_journal(paths)
        active_generation = validate_generation(
            journal["generation"], "active generation"
        )
        active_gate_run_id = require_safe_id(
            journal["gate_run_id"], "active production gate run ID"
        )
        expected_authorization_path = transaction_authorization_path(
            paths, active_generation, active_gate_run_id
        )
        if authorization_path != expected_authorization_path:
            fail(
                "active authorization path differs from its exact "
                "journal-derived transaction path"
            )
        if journal["boot_id"] != boot_id():
            fail("active authorization journal belongs to another boot")
        if validate_framework_context(journal["framework_context"]) != framework_context:
            fail("active authorization framework context differs from the journal")
        if journal["authorization_token_sha256"] != token_sha256:
            fail("active authorization token differs from the journal digest")
        owner = validate_owner_identity(journal["owner"], "active journal owner")
        owner_pid = owner["pid"]
        owner_ticks = owner["start_ticks"]
        if not isinstance(owner_pid, int) or not isinstance(owner_ticks, str):
            fail("internal error: active owner identity is invalid")
        observed_owner_ticks = process_start_ticks(owner_pid)
        contract = validate_child_contract(journal["child_contract"])
        if observed_owner_ticks is None:
            if contract["containment"] != "pid-namespace-v1":
                fail("active authorization journal owner is not live")
        elif observed_owner_ticks != owner_ticks:
            fail("active authorization journal owner PID was reused")
        child, child_identity = load_child_identity(paths, journal)
        child_sha256 = hashlib.sha256(canonical_json(child)).hexdigest()
        environment = contract["environment"]
        if not isinstance(environment, dict):  # pragma: no cover - validated.
            fail("internal error: active child environment is invalid")
        if environment.get(
            "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION"
        ) != os.fspath(authorization_path):
            fail("active authorization path differs from the child contract")
        authorization_bytes, authorization_identity = read_verified_journal_file(
            authorization_path,
            paths,
            "active production authorization",
            portage_readable=True,
        )
        if authorization_bytes != authorization_payload(journal, child_sha256):
            fail("active production authorization payload is mismatched")
        revalidate_child_contract(contract, paths)
        expected_payload = canonical_payload(active_generation)
        recorded_locks = journal["locks"]
        if not isinstance(recorded_locks, dict):  # pragma: no cover - validated.
            fail("internal error: active journal lock identities are invalid")
        if framework_identity != FileIdentity.from_json(
            recorded_locks["framework"], "active framework lock"
        ):
            fail("active framework lock differs from the journal")
        with generation_locks_shared(paths, arguments.lock_timeout_seconds) as (
            project,
            generation_lock,
        ):
            reject_active_transaction_partials(paths, authorization_path)
            project_descriptor, project_identity = project
            generation_descriptor, generation_identity = generation_lock
            if project_identity != FileIdentity.from_json(
                recorded_locks["project"], "active project lock"
            ) or generation_identity != FileIdentity.from_json(
                recorded_locks["generation"], "active generation lock"
            ):
                fail("active generation lock inode identities differ from the journal")
            if (
                read_descriptor(project_descriptor, "active project lock")
                != expected_payload
                or read_descriptor(generation_descriptor, "active generation lock")
                != expected_payload
            ):
                fail("active generation lock payloads differ from the journal")
            repeated_journal, repeated_journal_identity = load_journal(paths)
            repeated_child, repeated_child_identity = load_child_identity(paths, journal)
            repeated_authorization, repeated_authorization_identity = (
                read_verified_journal_file(
                    authorization_path,
                    paths,
                    "active production authorization",
                    portage_readable=True,
                )
            )
            if (
                repeated_journal != journal
                or repeated_journal_identity != journal_identity
                or repeated_child != child
                or repeated_child_identity != child_identity
                or repeated_authorization != authorization_bytes
                or repeated_authorization_identity != authorization_identity
            ):
                fail("active authorization evidence changed during verification")
            revalidate_child_contract(contract, paths)
            if active_framework_context(paths) != framework_context:
                fail("active framework context changed during authorization verification")
            reject_active_transaction_partials(paths, authorization_path)
    print("VERIFIED")
    return 0


def add_path_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lock-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--kill-after-seconds", type=float, default=10.0)
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--test-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--test-framework-lock", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--test-project-lock", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--test-generation-lock", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--test-journal", type=Path, help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely exercise production PGO lock paths for a root-only test gate."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    run = subparsers.add_parser("run", help="arm locks, supervise one child, and restore")
    add_path_arguments(run)
    run.add_argument("--generation-id", required=True)
    run.add_argument("--inventory-id", required=True)
    run.add_argument("--inventory-sha256", required=True)
    run.add_argument("--gate-run-id", required=True)
    run.add_argument("--test-pre-arm-pause-file", type=Path, help=argparse.SUPPRESS)
    run.add_argument("--child-timeout-seconds", type=float, default=3600.0)
    run.add_argument("--token-scan-timeout-seconds", type=float, default=600.0)
    run.add_argument("--token-scanner", required=True, type=Path)
    run.add_argument("--token-scan-root", required=True, action="append", type=Path)
    run.add_argument("--token-scan-output", required=True, type=Path)
    run.add_argument("--evidence-output-root", required=True, type=Path)
    run.add_argument("--test-pid-namespace", action="store_true", help=argparse.SUPPRESS)
    run.add_argument(
        "--failpoint",
        choices=tuple(FAILPOINT_EXIT),
        help=argparse.SUPPRESS,
    )
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(function=run_command)

    recover = subparsers.add_parser(
        "recover", help="restore an interrupted transaction or prove locks empty"
    )
    add_path_arguments(recover)
    recover.add_argument(
        "--failpoint",
        choices=(
            "restore-after-project",
            "receipt-after-partial-fsync",
            "receipt-after-final-rename",
            "receipt-after-journal-removal",
        ),
        help=argparse.SUPPRESS,
    )
    recover.set_defaults(function=recover_command)

    verify = subparsers.add_parser(
        "verify-active", help="verify one live production-profile authorization"
    )
    add_path_arguments(verify)
    verify.add_argument("--token-fd", required=True, type=int)
    verify.add_argument("--authorization", required=True, type=Path)
    verify.set_defaults(function=verify_active_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if raw_arguments and raw_arguments[0] == "__child-barrier":
        try:
            return child_barrier_exec(raw_arguments[1:])
        except TransactionError as error:
            print(f"ERROR: child barrier failed: {error}", file=sys.stderr)
            return 125
    parser = build_parser()
    arguments = parser.parse_args(raw_arguments)
    try:
        paths = resolve_paths(arguments)
        if (
            not paths.test_mode
            and os.geteuid() != 0
            and arguments.action != "verify-active"
        ):
            fail("production lock transaction requires root")
        return int(arguments.function(arguments))
    except TransactionError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

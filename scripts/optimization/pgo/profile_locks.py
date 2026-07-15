#!/usr/bin/env python3
"""Stable lock hierarchy shared by PGO profile producers and consumers."""

from __future__ import annotations

import contextlib
import fcntl
import grp
import json
import os
import stat
import time
from pathlib import Path
from typing import Iterator, NoReturn


FRAMEWORK_LOCK = Path("/run/gentoo-optimization/framework-install.lock")
PROJECT_LOCK = Path("/run/gentoo-optimization/project.lock")
GENERATION_LOCK = Path("/run/gentoo-optimization/generation.lock")
PORTAGE_GROUP = "portage"
PRODUCTION_LOCK_MODE = 0o640
PRODUCTION_LOCK_DIRECTORY_MODE = 0o750
GENERATION_FIELDS = {"generation_id", "inventory_id", "inventory_sha256"}
HEX64 = frozenset("0123456789abcdef")
SAFE_ID = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+_.:@-"
)


class ProfileLockError(Exception):
    """The stable optimization lock hierarchy could not be proven."""


def fail(message: str) -> NoReturn:
    raise ProfileLockError(message)


def canonical_generation_payload(generation: dict[str, str]) -> bytes:
    """Use the same stable payload already required by BOLT transactions."""
    return (json.dumps(generation, indent=2, sort_keys=True) + "\n").encode("utf-8")


def validate_generation(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != GENERATION_FIELDS:
        fail(f"{label} must contain exactly {sorted(GENERATION_FIELDS)}")
    result: dict[str, str] = {}
    for key in ("generation_id", "inventory_id"):
        item = value[key]
        if (
            not isinstance(item, str)
            or not item
            or item in {".", ".."}
            or any(character not in SAFE_ID for character in item)
        ):
            fail(f"{label}.{key} is not a safe exact identity")
        result[key] = item
    digest = value["inventory_sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in HEX64 for character in digest)
    ):
        fail(f"{label}.inventory_sha256 is not a lowercase SHA-256")
    result["inventory_sha256"] = digest
    return result


def generation_from_fields(
    generation_id: object, inventory_id: object, inventory_sha256: object
) -> dict[str, str]:
    return validate_generation(
        {
            "generation_id": generation_id,
            "inventory_id": inventory_id,
            "inventory_sha256": inventory_sha256,
        },
        "requested generation",
    )


def production_portage_gid() -> int:
    try:
        entry = grp.getgrnam(PORTAGE_GROUP)
    except KeyError:
        fail(f"required production group does not exist: {PORTAGE_GROUP}")
    if entry.gr_gid < 1:
        fail(f"production group has an unsafe GID: {PORTAGE_GROUP}")
    return entry.gr_gid


def _validate_production_chain(path: Path, label: str, portage_gid: int) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as error:
            fail(f"cannot inspect {label} component {current}: {error}")
        if stat.S_ISLNK(metadata.st_mode):
            fail(f"{label} contains a symlink component: {current}")
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
            fail(f"{label} has an untrusted ancestor: {current}")
    try:
        parent = path.parent.lstat()
    except OSError as error:
        fail(f"cannot inspect production lock directory {path.parent}: {error}")
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_gid != portage_gid
        or stat.S_IMODE(parent.st_mode) != PRODUCTION_LOCK_DIRECTORY_MODE
    ):
        fail(
            "production lock directory must be root:portage mode-0750: "
            f"{path.parent}"
        )


def _read_descriptor(descriptor: int, label: str) -> bytes:
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
                fail(f"{label} exceeds the 65536-byte lock payload limit")
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as error:
        fail(f"cannot read {label}: {error}")


def _parse_generation_payload(payload: bytes, label: str) -> dict[str, str]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"{label} does not contain valid generation JSON: {error}")
    generation = validate_generation(value, label)
    if payload != canonical_generation_payload(generation):
        fail(f"{label} does not use the canonical generation payload")
    return generation


@contextlib.contextmanager
def profile_lock_hierarchy(
    *,
    exclusive: bool,
    expected_generation: dict[str, str] | None,
    expected_generation_id: str | None,
    timeout_seconds: float,
    test_mode: bool,
    test_paths: tuple[Path, Path, Path] | None,
) -> Iterator[dict[str, str]]:
    """Take framework, project, generation locks and prove stable identity.

    Framework is always shared. Profile publishers take project/generation
    exclusively; profile validators take them shared. Test paths are accepted
    only behind an explicit test-mode switch and must replace all three locks.
    """
    if not 0 < timeout_seconds <= 300:
        fail("lock timeout must be greater than zero and at most 300 seconds")
    if test_mode:
        if test_paths is None:
            fail("test mode requires all three explicit lock paths")
        if any(path in {FRAMEWORK_LOCK, PROJECT_LOCK, GENERATION_LOCK} for path in test_paths):
            fail("test mode cannot substitute a production lock inode")
        paths = test_paths
        expected_uid: int | None = None
        expected_gid: int | None = None
        expected_mode: int | None = None
    else:
        if test_paths is not None:
            fail("test lock paths require explicit test mode")
        paths = (FRAMEWORK_LOCK, PROJECT_LOCK, GENERATION_LOCK)
        expected_uid = 0
        expected_gid = production_portage_gid()
        expected_mode = PRODUCTION_LOCK_MODE

    descriptors: list[int] = []
    acquired: list[int] = []
    payloads: dict[str, bytes] = {}
    labels = ("framework lock", "project lock", "generation lock")
    try:
        for index, (label, path) in enumerate(zip(labels, paths, strict=True)):
            if not path.is_absolute() or path == Path("/") or ".." in path.parts:
                fail(f"{label} must be a safe non-root absolute path")
            if not test_mode:
                if expected_gid is None:  # pragma: no cover - set above
                    fail("internal error: production Portage GID is absent")
                _validate_production_chain(path, label, expected_gid)
            flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path, flags)
            except OSError as error:
                fail(f"cannot open stable {label} {path}: {error}")
            descriptors.append(descriptor)
            try:
                descriptor_metadata = os.fstat(descriptor)
                path_metadata = path.lstat()
            except OSError as error:
                fail(f"cannot inspect stable {label} {path}: {error}")
            if (
                not stat.S_ISREG(descriptor_metadata.st_mode)
                or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
                != (path_metadata.st_dev, path_metadata.st_ino)
            ):
                fail(f"{label} is not a stable regular inode: {path}")
            observed_mode = stat.S_IMODE(descriptor_metadata.st_mode)
            if test_mode:
                private_fixture = (
                    descriptor_metadata.st_uid == os.geteuid()
                    and observed_mode == 0o600
                )
                group_fixture = (
                    descriptor_metadata.st_uid == 0
                    and descriptor_metadata.st_gid > 0
                    and observed_mode == 0o640
                    and (
                        os.geteuid() == 0
                        or descriptor_metadata.st_gid
                        in {os.getegid(), *os.getgroups()}
                    )
                )
                if not private_fixture and not group_fixture:
                    fail(
                        f"{label} is not an owner-0600 or root:reader-group-0640 "
                        f"fixture inode: {path}"
                    )
            elif (
                descriptor_metadata.st_uid != expected_uid
                or descriptor_metadata.st_gid != expected_gid
                or observed_mode != expected_mode
            ):
                if expected_mode is None:  # pragma: no cover - set above
                    fail("internal error: production lock mode is absent")
                fail(
                    f"{label} is not a stable trusted mode-{expected_mode:04o} "
                    f"regular inode: {path}"
                )
            operation = fcntl.LOCK_SH if index == 0 or not exclusive else fcntl.LOCK_EX
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
                    acquired.append(descriptor)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        fail(
                            f"timed out after {timeout_seconds:g}s waiting for {label}: {path}"
                        )
                    time.sleep(min(0.05, remaining))
                except OSError as error:
                    fail(f"cannot acquire stable {label} {path}: {error}")
            payloads[label] = _read_descriptor(descriptor, label)
            try:
                after = path.lstat()
            except OSError as error:
                fail(f"cannot re-inspect stable {label} {path}: {error}")
            if (descriptor_metadata.st_dev, descriptor_metadata.st_ino) != (
                after.st_dev,
                after.st_ino,
            ):
                fail(f"{label} inode changed while held: {path}")

        if payloads["framework lock"] != b"":
            fail("stable framework lock inode must remain empty")
        project = _parse_generation_payload(payloads["project lock"], "project lock")
        generation = _parse_generation_payload(
            payloads["generation lock"], "generation lock"
        )
        if project != generation or payloads["project lock"] != payloads["generation lock"]:
            fail("project and generation locks do not carry one exact identity")
        if expected_generation is not None and generation != validate_generation(
            expected_generation, "expected generation"
        ):
            fail("stable locks do not match the requested exact generation")
        if expected_generation_id is not None and generation["generation_id"] != expected_generation_id:
            fail("stable locks do not match the requested optimization generation ID")
        try:
            yield generation
        finally:
            # Re-open the complete lock identity before release. A rename or
            # payload rewrite while the lock was held is always a failed
            # transaction, even when the guarded operation otherwise passed.
            for label, path, descriptor in zip(
                labels, paths, descriptors, strict=True
            ):
                descriptor_metadata = os.fstat(descriptor)
                path_metadata = path.lstat()
                if (descriptor_metadata.st_dev, descriptor_metadata.st_ino) != (
                    path_metadata.st_dev,
                    path_metadata.st_ino,
                ):
                    fail(f"{label} inode changed during the guarded operation: {path}")
                if _read_descriptor(descriptor, label) != payloads[label]:
                    fail(f"{label} payload changed during the guarded operation")
    finally:
        for descriptor in reversed(acquired):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass

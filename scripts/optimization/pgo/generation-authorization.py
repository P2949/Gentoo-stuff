#!/usr/bin/env python3
"""Crash-recoverable Phase-3 generation authority transaction.

The framework lock is a shared, empty coordination inode.  The project and
generation inodes carry one canonical generation payload.  This module is
deliberately separate from the Phase-2 production transaction: Phase 3 keeps
the authority resident until an explicit transition or deactivation.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(HERE))
from profile_locks import (  # noqa: E402
    GENERATION_FIELDS, canonical_generation_payload, generation_from_fields,
    validate_generation,
)

DEFAULT_ROOT = Path("/run/gentoo-optimization")
RECEIPT = Path("/var/lib/gentoo-optimization/phase3-generation-authorization.json")
SAFE_MODES = 0o640

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def inode(path: Path) -> dict[str, int]:
    s = path.stat()
    return {"dev": s.st_dev, "ino": s.st_ino, "mode": stat.S_IMODE(s.st_mode)}

def read_lock(path: Path) -> bytes:
    return path.read_bytes()

def framework_identity(current: Path, generation: dict[str, str]) -> dict[str, Any]:
    target = Path(os.path.realpath(current))
    # generation_id is the frozen inventory identity; the framework target is
    # a separate content-addressed installation identity.  Bind them through
    # the manifest's inventory digest rather than assuming their names match.
    manifest = target / "install.manifest"
    if not manifest.is_file():
        raise RuntimeError(f"framework manifest is unreadable: {manifest}")
    lines: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1); lines[k] = v
    policy = target / "generated-policy" / ".identity"
    if not policy.is_file():
        raise RuntimeError("framework generated-policy identity is missing")
    policy_id = policy.read_text(encoding="utf-8").strip()
    if not policy_id or policy_id == "empty-v1":
        raise RuntimeError("Phase-3 authority requires a non-empty generated policy")
    manifest_policy = lines.get("generated_policy", "").strip()
    if manifest_policy != policy_id:
        raise RuntimeError("framework generated-policy identity does not match manifest")
    inv = lines.get("frozen_inventory_sha256") or lines.get("candidate_inventory_sha256")
    if not inv or inv == "none" or inv != generation["inventory_sha256"]:
        raise RuntimeError("framework inventory identity does not match requested generation")
    source_aggregate = lines.get("source_aggregate_sha256", "").strip()
    framework_aggregate = lines.get("framework_aggregate_sha256", "").strip()
    for name, value in (("source aggregate", source_aggregate), ("framework aggregate", framework_aggregate)):
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise RuntimeError(f"framework {name} identity is missing or malformed")
    return {
        "target": str(target), "target_realpath": str(target),
        "manifest_sha256": digest(manifest),
        "source_aggregate_sha256": source_aggregate,
        "framework_aggregate_sha256": framework_aggregate,
        "generated_policy_manifest": manifest_policy,
        "generated_policy": policy_id,
        "frozen_inventory_sha256": inv,
        "git_commit": lines.get("git_commit", ""),
    }

def locks(root: Path) -> tuple[Path, Path, Path]:
    return (root / "framework-install.lock", root / "project.lock", root / "generation.lock")

def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(fd)
    finally: os.close(fd)

def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, sort_keys=True); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path); fsync_dir(path.parent)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass

def publish_receipt(requested: Path, record: dict[str, Any]) -> Path:
    """Publish committed authority evidence without replacing history."""
    txid = str(record["transaction_id"])
    if requested == RECEIPT:
        target = requested.parent / "phase3-authority" / f"{txid}.json"
    else:
        target = requested
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise RuntimeError(f"refusing to overwrite committed receipt: {target}")
    atomic_json(target, record)
    return target

def acquire(paths: tuple[Path, Path, Path], exclusive: bool = True) -> list[int]:
    fds: list[int] = []
    try:
        for i, p in enumerate(paths):
            fd = os.open(p, os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
            fds.append(fd)
            fcntl.flock(fd, fcntl.LOCK_SH if i == 0 else (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH))
        return fds
    except Exception:
        for fd in reversed(fds):
            os.close(fd)
        raise

def release(fds: list[int]) -> None:
    for fd in reversed(fds):
        try: fcntl.flock(fd, fcntl.LOCK_UN)
        finally: os.close(fd)

def write_payload_fd(fd: int, payload: bytes) -> None:
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        os.write(fd, payload); os.fsync(fd)
    finally: pass

def assert_locked_path(path: Path, fd: int) -> None:
    expected = os.fstat(fd)
    actual = path.stat()
    if (expected.st_dev, expected.st_ino, stat.S_IMODE(expected.st_mode),
            expected.st_uid, expected.st_gid, expected.st_nlink) != (
            actual.st_dev, actual.st_ino, stat.S_IMODE(actual.st_mode),
            actual.st_uid, actual.st_gid, actual.st_nlink):
        raise RuntimeError(f"lock inode changed: {path}")

def write_payload(fds: list[int], paths: tuple[Path, Path, Path], index: int, payload: bytes) -> None:
    assert_locked_path(paths[index], fds[index])
    write_payload_fd(fds[index], payload)
    assert_locked_path(paths[index], fds[index])

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("activate", "verify", "recover", "transition", "deactivate"))
    ap.add_argument("--generation-id"); ap.add_argument("--inventory-id"); ap.add_argument("--inventory-sha256")
    ap.add_argument("--framework-generation", required=False)
    ap.add_argument("--framework-current", type=Path)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--receipt", type=Path, default=RECEIPT)
    ap.add_argument("--journal", type=Path)
    ap.add_argument("--old-generation-id")
    a = ap.parse_args()
    root = a.root; paths = locks(root); journal = a.journal or root / "phase3-generation-authorization.journal.json"
    if a.action == "recover":
        if not journal.exists(): return 0
        data = json.loads(journal.read_text())
        desired = data.get("new_payload", "").encode()
        fds = acquire(paths)
        try:
            current = [read_lock(p) for p in paths]
            if current[0] != b"": raise RuntimeError("framework lock is not empty")
            old_payload = data.get("old_project_payload", "").encode()
            allowed = {b"", desired, old_payload}
            if all(x in allowed for x in current[1:]):
                if current[1] != desired: write_payload(fds, paths, 1, desired)
                if current[2] != desired: write_payload(fds, paths, 2, desired)
                journal.unlink(); fsync_dir(journal.parent); return 0
            raise RuntimeError("journal recovery found an unrecognized lock state")
        finally: release(fds)
    if a.action in ("activate", "transition"):
        if not all((a.generation_id, a.inventory_id, a.inventory_sha256, a.framework_generation)):
            ap.error("activation requires generation identities and --framework-generation")
        generation = generation_from_fields(a.generation_id, a.inventory_id, a.inventory_sha256)
        current = a.framework_current or (Path(a.framework_generation).parent / "framework-current")
        if os.path.realpath(current) != os.path.realpath(a.framework_generation):
            raise RuntimeError("framework-current does not resolve to --framework-generation")
        framework = framework_identity(current, generation)
        new_payload = canonical_generation_payload(generation)
    else:
        generation = None; new_payload = b""; framework = {}
    fds = acquire(paths)
    try:
        old = [read_lock(p) for p in paths]
        if old[0] != b"": raise RuntimeError("framework lock must remain empty")
        if a.action == "verify":
            if old[1] != old[2] or not old[1]: raise RuntimeError("no exact active Phase-3 generation")
            active = validate_generation(json.loads(old[1]), "active generation")
            if any((a.generation_id, a.inventory_id, a.inventory_sha256)):
                requested = generation_from_fields(a.generation_id, a.inventory_id, a.inventory_sha256)
                if active != requested: raise RuntimeError("active authority does not match requested generation")
            return 0
        if a.action == "deactivate":
            if old[1] != old[2]: raise RuntimeError("cannot deactivate split authority")
            record = {"schema_version": 1, "transaction_id": hashlib.sha256(os.urandom(32)).hexdigest(),
                      "state": "prepared", "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                      "old_project_payload": old[1].decode(), "old_generation_payload": old[2].decode(),
                      "new_payload": "", "locks": {k: inode(p) for k,p in zip(("framework","project","generation"), paths)},
                      "framework": {}, "generation": None}
            atomic_json(journal, record)
            write_payload(fds, paths, 1, b""); write_payload(fds, paths, 2, b"")
            if read_lock(paths[1]) or read_lock(paths[2]):
                raise RuntimeError("deactivation verification failed")
            record["state"] = "committed"; record["committed_at"] = time.time(); publish_receipt(a.receipt, record)
            journal.unlink(); fsync_dir(journal.parent); return 0
        if a.action == "activate" and (old[1] or old[2]): raise RuntimeError("activation requires empty runtime locks")
        if a.action == "transition":
            if old[1] != old[2] or not old[1]: raise RuntimeError("transition requires one existing exact authority")
            if not a.old_generation_id or json.loads(old[1])["generation_id"] != a.old_generation_id: raise RuntimeError("old generation identity mismatch")
        record = {"schema_version": 1, "transaction_id": hashlib.sha256(os.urandom(32)).hexdigest(), "state": "prepared", "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "old_project_payload": old[1].decode(), "old_generation_payload": old[2].decode(), "new_payload": new_payload.decode(), "locks": {k: inode(p) for k,p in zip(("framework","project","generation"),paths)}, "framework": framework, "generation": generation}
        atomic_json(journal, record)
        write_payload(fds, paths, 1, new_payload); write_payload(fds, paths, 2, new_payload)
        if read_lock(paths[1]) != new_payload or read_lock(paths[2]) != new_payload: raise RuntimeError("runtime authority verification failed")
        record["state"] = "committed"; record["committed_at"] = time.time(); publish_receipt(a.receipt, record)
        journal.unlink(); fsync_dir(journal.parent)
        return 0
    finally: release(fds)

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as e:
        raise SystemExit(f"REFUSED: {e}")

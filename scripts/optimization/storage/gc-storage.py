#!/usr/bin/env python3
"""Fail-closed storage GC driven by a retention-set report.

Dry-run is the default. Execution requires --execute and only considers rows
explicitly marked ARCHIVE_CANDIDATE; UNKNOWN and all authority states remain.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def active_portage() -> bool:
    result = subprocess.run(["pgrep", "-x", "emerge"], check=False, stdout=subprocess.DEVNULL)
    return result.returncode == 0


def acquire_locks(paths: list[Path]) -> list[object]:
    handles = []
    try:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                raise SystemExit(f"REFUSED: active storage lock: {path}")
            handles.append(handle)
    except BaseException:
        for handle in handles:
            handle.close()
        raise
    return handles


def durable_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def free_bytes(path: Path) -> int:
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retention", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--project-lock", type=Path, default=Path("/run/gentoo-optimization/project.lock"))
    parser.add_argument("--generation-lock", type=Path, default=Path("/run/gentoo-optimization/generation.lock"))
    parser.add_argument("--measurement-root", type=Path)
    parser.add_argument("--quarantine-root", type=Path, default=Path("/var/tmp/gentoo-optimization/storage-gc-quarantine"))
    args = parser.parse_args()
    report = json.loads(args.retention.read_text(encoding="utf-8"))
    if report.get("schema") != "storage-retention-set-v1":
        raise SystemExit("REFUSED: unsupported retention report schema")
    if active_portage():
        raise SystemExit("REFUSED: Portage transaction is active")
    locks = acquire_locks([args.project_lock, args.generation_lock])
    candidates = [Path(row["path"]) for row in report.get("objects", []) if row.get("state") == "ARCHIVE_CANDIDATE"]
    unknown = [row for row in report.get("objects", []) if row.get("state") == "UNKNOWN"]
    deleted: list[str] = []
    if args.measurement_root is None:
        if args.execute:
            raise SystemExit("REFUSED: --measurement-root is required for execute mode")
        measurement_root = args.retention.parent
    else:
        measurement_root = args.measurement_root
    if not measurement_root.exists():
        raise SystemExit(f"REFUSED: measurement root is unavailable: {measurement_root}")
    free_before = free_bytes(measurement_root)
    try:
        if args.execute:
            quarantine = args.quarantine_root
            quarantine.mkdir(parents=True, exist_ok=True)
            try:
                for path in candidates:
                    if not path.is_dir() or not path.is_absolute():
                        raise SystemExit(f"REFUSED: invalid candidate {path}")
                    target = quarantine / (path.name + "." + str(os.getpid()))
                    os.replace(path, target)
                    shutil.rmtree(target)
                    deleted.append(str(path))
            except BaseException:
                for original in deleted:
                    path = Path(original)
                    target = quarantine / (path.name + "." + str(os.getpid()))
                    if target.exists() and not path.exists():
                        os.replace(target, path)
                raise
    finally:
        for handle in locks:
            handle.close()
    free_after = free_bytes(measurement_root)
    receipt = {
        "schema": "storage-gc-v1",
        "timestamp": int(time.time()),
        "mode": "execute" if args.execute else "dry-run",
        "candidates": [str(p) for p in candidates],
        "deleted": deleted,
        "unknown_count": len(unknown),
        "unknown_retained": True,
        "filesystem_free_bytes_before": free_before,
        "filesystem_free_bytes_after": free_after,
        "filesystem_free_delta": free_after - free_before,
    }
    durable_write(args.receipt, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

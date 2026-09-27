#!/usr/bin/env python3
"""Collect a read-only, machine-readable storage baseline.

The report deliberately records both logical usage and filesystem capacity:
reflinked trees can make directory totals exceed physical blocks consumed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

DEFAULT_PATHS = (
    "/var",
    "/var/lib/gentoo-optimization",
    "/var/cache/gentoo-optimization",
    "/var/cache/gentoo-optimization/binpkgs",
    "/var/lib/gentoo-optimization/recovery/binpkgs",
    "/var/cache/binpkgs",
    "/var/tmp/gentoo-optimization",
    "/var/tmp/gentoo-optimization/pgo-raw",
    "/var/tmp/gentoo-portage-build",
    "/var/tmp/ccache",
    "/var/tmp/thinlto-cache",
    "/var/cache/distfiles",
    "/var/cache/gentoo-optimization/prerequisite-transactions",
    "/var/lib/gentoo-optimization/recovery/prerequisite-authorities",
    "/var/lib/gentoo-optimization/recovery/binpkgs",
    "/var/lib/gentoo-optimization/merged-profiles",
    "/var/cache/gentoo-optimization/pgo",
    "/var/cache/gentoo-optimization/bolt",
)


def filesystem(path: Path) -> dict[str, object]:
    vfs = os.statvfs(path)
    stat = os.stat(path)
    fs_type = subprocess.run(
        ["stat", "-f", "-c", "%T", os.fspath(path)],
        text=True, capture_output=True, check=False,
    ).stdout.strip() or "unknown"
    return {
        "device": stat.st_dev,
        "filesystem_type": fs_type,
        "free_bytes": vfs.f_bavail * vfs.f_frsize,
        "total_bytes": vfs.f_blocks * vfs.f_frsize,
        "mount_id": stat.st_dev,
    }


def logical_usage(path: Path) -> int | None:
    result = subprocess.run(
        ["du", "-sxB1", os.fspath(path)],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        return None
    try:
        return int(result.stdout.split()[0])
    except (IndexError, ValueError):
        return None


def counts(path: Path) -> tuple[int, int]:
    files = 0
    allocated = 0
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in names:
            candidate = Path(root) / name
            try:
                st = candidate.stat()
            except OSError:
                continue
            files += 1
            allocated += st.st_blocks * 512
    return files, allocated


def counts_with_children(path: Path) -> tuple[int, int, dict[str, tuple[int, int]]]:
    """Count a tree once while retaining direct-child file/allocated totals."""
    files = 0
    allocated = 0
    children: dict[str, tuple[int, int]] = {}
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        direct = Path(root).relative_to(path).parts
        child_name = direct[0] if direct else None
        child_files, child_allocated = children.get(child_name, (0, 0)) if child_name else (0, 0)
        for name in names:
            candidate = Path(root) / name
            try:
                st = candidate.stat()
            except OSError:
                continue
            blocks = st.st_blocks * 512
            files += 1
            allocated += blocks
            if child_name:
                child_files += 1
                child_allocated += blocks
        if child_name:
            children[child_name] = (child_files, child_allocated)
    return files, allocated, children


def reflink_probe(directory: Path) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".reflink-probe-", dir=directory) as tmp:
        src = Path(tmp) / "source"
        dst = Path(tmp) / "clone"
        src.write_bytes(b"gentoo-optimization-reflink-probe")
        result = subprocess.run(
            ["cp", "--reflink=always", os.fspath(src), os.fspath(dst)],
            text=True, capture_output=True, check=False,
        )
        return {"supported": result.returncode == 0, "stderr": result.stderr.strip()}


def entry(raw: str) -> dict[str, object]:
    path = Path(raw)
    if not path.exists():
        return {"path": raw, "exists": False}
    files, allocated, child_counts = counts_with_children(path)
    children = []
    for child in sorted(path.iterdir()):
        if child.is_dir() and not child.is_symlink():
            child_logical = logical_usage(child)
            child_files, child_allocated = child_counts.get(child.name, (0, 0))
            children.append({"path": str(child), "logical_bytes": child_logical, "allocated_bytes": child_allocated, "file_count": child_files})
    children.sort(key=lambda item: (item["logical_bytes"] is not None, item["logical_bytes"] or 0), reverse=True)
    return {
        "path": raw,
        "exists": True,
        "logical_bytes": logical_usage(path),
        "allocated_bytes": allocated,
        "file_count": files,
        "filesystem": filesystem(path),
        "children": children,
    }


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--path", action="append", dest="paths")
    args = parser.parse_args()
    paths = args.paths or list(DEFAULT_PATHS)
    report = {
        "schema": "storage-inventory-v1",
        "created_at": int(time.time()),
        "entries": [entry(item) for item in paths],
        "reflink_probe": reflink_probe(Path("/var/tmp")),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    durable_write(args.output, json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

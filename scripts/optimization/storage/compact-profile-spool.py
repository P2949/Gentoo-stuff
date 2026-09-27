#!/usr/bin/env python3
"""Compact a sealed raw-profile attempt without losing member identity."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import shutil
from pathlib import Path


def manifest(root: Path) -> list[dict[str, object]]:
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({"path": str(path.relative_to(root)), "size": path.stat().st_size, "sha256": digest})
    return rows


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


def acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit(f"REFUSED: active storage lock {path}")
    return handle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--seal-record", type=Path,
                        help="machine-readable completed/validated attempt receipt required for retirement")
    parser.add_argument("--retire-expanded", action="store_true",
                        help="remove the expanded spool only after archive reconstruction verification")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--project-lock", type=Path)
    parser.add_argument("--generation-lock", type=Path)
    args = parser.parse_args()
    root = args.attempt.resolve()
    locks = []
    if args.execute:
        project_lock = args.project_lock or Path("/run/gentoo-optimization/project.lock")
        generation_lock = args.generation_lock or Path("/run/gentoo-optimization/generation.lock")
        if os.geteuid() == 0 or args.project_lock or args.generation_lock:
            locks = [acquire_lock(project_lock), acquire_lock(generation_lock)]
    if not root.is_dir():
        raise SystemExit("REFUSED: attempt spool is not a directory")
    if (root / "pending").exists() or (root / "unresolved").exists():
        raise SystemExit("REFUSED: unresolved attempt cannot be compacted")
    members = manifest(root)
    if not members:
        raise SystemExit("REFUSED: empty attempt spool")
    payload = {"schema": "profile-archive-manifest-v1", "attempt": str(root), "members": members}
    if args.retire_expanded and not args.execute:
        raise SystemExit("REFUSED: --retire-expanded requires --execute")
    if args.retire_expanded:
        if args.seal_record is None or not args.seal_record.is_file():
            raise SystemExit("REFUSED: retirement requires a sealed attempt receipt")
        try:
            seal = json.loads(args.seal_record.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"REFUSED: unreadable seal record: {exc}")
        if seal.get("status") not in {"completed", "merged-validated", "validated"}:
            raise SystemExit("REFUSED: attempt receipt is not completed and validated")
        payload["seal_record"] = {"path": str(args.seal_record.resolve()),
                                   "sha256": hashlib.sha256(args.seal_record.read_bytes()).hexdigest()}
        for key in ("backend", "cpv", "attempt_id", "generation", "generation_id"):
            if key in seal:
                payload[key] = seal[key]
    if args.execute:
        if args.retire_expanded:
            for candidate, label in ((args.archive, "archive"), (args.manifest, "manifest"), (args.receipt, "receipt")):
                if candidate is not None and root == candidate.resolve() or (candidate is not None and root in candidate.resolve().parents):
                    raise SystemExit(f"REFUSED: {label} must be outside attempt spool")
        args.archive.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        compressed = args.archive.with_suffix(args.archive.suffix + ".zst")
        with compressed.open("wb") as archive_stream:
            compressor = subprocess.Popen(["zstd", "-T0", "-19", "-q", "-c"], stdin=subprocess.PIPE, stdout=archive_stream)
            assert compressor.stdin is not None
            with tarfile.open(fileobj=compressor.stdin, mode="w|") as tar:
                for row in members:
                    tar.add(root / str(row["path"]), arcname=str(row["path"]), recursive=False)
            compressor.stdin.close()
            if compressor.wait() != 0:
                raise SystemExit("REFUSED: streaming zstd archive failed")
        durable_write(args.manifest, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        for path in (compressed, args.manifest):
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        # Re-open through zstd/tar and compare every member before retirement.
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["tar", "--zstd", "-xf", os.fspath(compressed), "-C", tmp], check=True)
            observed = manifest(Path(tmp))
            if observed != members:
                raise SystemExit("REFUSED: compressed archive failed member verification")
        if args.retire_expanded:
            # Do not remove the seal record or archive/manifest; only retire
            # the expanded representation after a full reconstruction check.
            shutil.rmtree(root)
    result = {"schema": "profile-archive-plan-v1", "attempt": str(root),
              "members": len(members), "execute": args.execute,
              "retired_expanded": bool(args.execute and args.retire_expanded)}
    if args.receipt:
        durable_write(args.receipt, json.dumps(result, indent=2, sort_keys=True) + "\n")
    for handle in locks:
        handle.close()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

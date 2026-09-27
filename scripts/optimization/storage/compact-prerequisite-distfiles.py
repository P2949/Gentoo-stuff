#!/usr/bin/env python3
"""Deduplicate terminal prerequisite distfiles into immutable objects.

Only explicit terminal transaction state is admitted.  The default is an
inventory-only dry run; --execute first materializes and verifies a
content-addressed object for every file, writes a manifest/receipt, then
retires the expanded source file.  Unknown and nonterminal transactions stay
untouched.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256(); size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk); size += len(chunk)
    return h.hexdigest(), size


def publish_object(source: Path, target: Path) -> None:
    """Publish a verified object without silently allocating a full CoW copy."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial." + str(os.getpid()))
    same_filesystem = os.stat(source).st_dev == os.stat(target.parent).st_dev
    try:
        subprocess.run(["cp", "--reflink=always", os.fspath(source), os.fspath(partial)], check=True)
    except (OSError, subprocess.CalledProcessError):
        partial.unlink(missing_ok=True)
        if same_filesystem:
            raise SystemExit("REFUSED: reflink publication failed on same filesystem")
        shutil.copyfile(source, partial)
    observed, observed_size = digest(partial)
    expected, expected_size = digest(source)
    if observed != expected or observed_size != expected_size:
        partial.unlink(missing_ok=True)
        raise SystemExit("REFUSED: object verification failed")
    os.replace(partial, target)


def terminal_state(state_dir: Path, tx: str) -> dict[str, object] | None:
    prefix = state_dir / ("jsonschema-prerequisite-" + tx)
    for suffix in ("success", "rolled-back", "recovery-failed", "abandoned"):
        path = prefix.with_name(prefix.name + "." + suffix + ".json")
        if path.is_file():
            return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "state": suffix}
    return None


def durable_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(fd)
    finally: os.close(fd)


def acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit(f"REFUSED: active storage lock {path}")
    return handle


def active_portage() -> bool:
    return any(
        subprocess.run(["pgrep", "-x", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0
        for name in ("emerge", "ebuild", "quickpkg")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transactions", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--objects", type=Path, required=True)
    parser.add_argument("--authorities", type=Path, help="matching prerequisite authority root")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit-transactions", type=int)
    parser.add_argument("--transaction-id", action="append", default=[])
    parser.add_argument("--project-lock", type=Path)
    parser.add_argument("--generation-lock", type=Path)
    args = parser.parse_args()
    root = args.transactions.resolve(); state = args.state_dir.resolve(); objects = args.objects.resolve()
    locks = []
    if args.execute:
        if active_portage():
            raise SystemExit("REFUSED: active Portage transaction")
        project_lock = args.project_lock or Path("/run/gentoo-optimization/project.lock")
        generation_lock = args.generation_lock or Path("/run/gentoo-optimization/generation.lock")
        if os.geteuid() == 0 or args.project_lock or args.generation_lock:
            locks = [acquire_lock(project_lock), acquire_lock(generation_lock)]
    if not root.is_dir() or not state.is_dir():
        raise SystemExit("REFUSED: transaction or state directory is unavailable")
    rows: list[dict[str, object]] = []
    transactions = [p for p in sorted(root.iterdir()) if p.is_dir()]
    if args.transaction_id:
        wanted = set(args.transaction_id)
        transactions = [p for p in transactions if p.name in wanted]
    if args.limit_transactions is not None:
        transactions = transactions[:args.limit_transactions]
    for tx in transactions:
        marker = terminal_state(state, tx.name)
        source = tx / "distfiles.staging"
        if not marker or not source.is_dir():
            continue
        files = []
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            sha, size = digest(path)
            files.append({"path": str(path.relative_to(source)), "sha256": sha, "size": size})
        rows.append({"transaction_id": tx.name, "terminal": marker, "source": str(source), "files": files, "logical_bytes": sum(int(x["size"]) for x in files)})
    retired: list[dict[str, object]] = []
    if args.execute:
        objects.mkdir(parents=True, exist_ok=True)
        for row in rows:
            tx_source = Path(str(row["source"]))
            for member in row["files"]:  # type: ignore[union-attr]
                source = tx_source / str(member["path"]); sha = str(member["sha256"])
                target = objects / sha[:2] / sha
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    publish_object(source, target)
                    observed, observed_size = digest(target)
                    if observed != sha or observed_size != int(member["size"]):
                        target.unlink(missing_ok=True); raise SystemExit("REFUSED: object verification failed")
                observed, observed_size = digest(target)
                if observed != sha or observed_size != int(member["size"]):
                    raise SystemExit("REFUSED: existing object hash mismatch")
                quarantine = source.with_name("." + source.name + ".retiring." + str(os.getpid()))
                os.replace(source, quarantine)
                quarantine.unlink()
                retired.append({"transaction_id": row["transaction_id"], "path": str(member["path"]), "sha256": sha, "object": str(target)})
            if args.authorities:
                authority = args.authorities.resolve() / str(row["transaction_id"]) / "distfiles"
                if authority.is_dir():
                    if not row["files"]:
                        raise SystemExit("REFUSED: authority retirement requires a non-empty source manifest")
                    for member in row["files"]:  # type: ignore[union-attr]
                        source = authority / str(member["path"])
                        if not source.is_file():
                            raise SystemExit(f"REFUSED: authority member missing: {source}")
                        observed, size = digest(source)
                        if observed != str(member["sha256"]) or size != int(member["size"]):
                            raise SystemExit(f"REFUSED: authority hash mismatch: {source}")
                    quarantine = authority.with_name(".distfiles.retiring." + str(os.getpid()))
                    os.replace(authority, quarantine)
                    shutil.rmtree(quarantine)
                    retired.append({"transaction_id": row["transaction_id"], "authority": str(authority), "authority_retired": True})
    payload = {"schema": "gentoo-optimization-prerequisite-distfile-retirement-v1", "timestamp": int(time.time()), "mode": "execute" if args.execute else "dry-run", "transactions": rows, "retired": retired}
    durable_write(args.receipt, payload)
    for handle in locks:
        handle.close()
    print(json.dumps({"schema": payload["schema"], "mode": payload["mode"], "transactions": len(rows), "files": sum(len(x["files"]) for x in rows), "retired": len(retired)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

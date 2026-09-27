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
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path


def digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256(); size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk); size += len(chunk)
    return h.hexdigest(), size


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transactions", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--objects", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit-transactions", type=int)
    parser.add_argument("--transaction-id", action="append", default=[])
    args = parser.parse_args()
    root = args.transactions.resolve(); state = args.state_dir.resolve(); objects = args.objects.resolve()
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
                    partial = target.with_suffix(".partial." + str(os.getpid()))
                    shutil.copyfile(source, partial)
                    observed, observed_size = digest(partial)
                    if observed != sha or observed_size != int(member["size"]):
                        partial.unlink(missing_ok=True); raise SystemExit("REFUSED: object verification failed")
                    os.replace(partial, target)
                observed, observed_size = digest(target)
                if observed != sha or observed_size != int(member["size"]):
                    raise SystemExit("REFUSED: existing object hash mismatch")
                quarantine = source.with_name("." + source.name + ".retiring." + str(os.getpid()))
                os.replace(source, quarantine)
                quarantine.unlink()
                retired.append({"transaction_id": row["transaction_id"], "path": str(member["path"]), "sha256": sha, "object": str(target)})
    payload = {"schema": "gentoo-optimization-prerequisite-distfile-retirement-v1", "timestamp": int(time.time()), "mode": "execute" if args.execute else "dry-run", "transactions": rows, "retired": retired}
    durable_write(args.receipt, payload)
    print(json.dumps({"schema": payload["schema"], "mode": payload["mode"], "transactions": len(rows), "files": sum(len(x["files"]) for x in rows), "retired": len(retired)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

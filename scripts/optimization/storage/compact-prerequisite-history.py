#!/usr/bin/env python3
"""Retire expanded prerequisite transaction history safely.

The prerequisite transaction directories are historical evidence once the
transaction is terminal.  This tool records hashes and sizes first, then
retains a compact retirement receipt.  It is deliberately dry-run by default;
execution only removes explicitly ephemeral children from terminal
transactions and refuses ambiguous or active state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

EPHEMERAL = ("tmp", "ccache", "thinlto-cache", "cargo-home", "rustup-home", "home", "xdg-cache", "distfiles.runtime")
STATE_NAMES = ("terminal.json", "state.json", "transaction-state.json", "completed.json", "receipt.json")


def tree_info(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    logical = files = 0
    if path.is_file():
        data = path.read_bytes(); digest.update(data)
        return {"path": str(path), "files": 1, "logical_bytes": len(data), "sha256": digest.hexdigest()}
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        try:
            data = item.read_bytes()
            rel = str(item.relative_to(path)).encode()
        except OSError:
            continue
        digest.update(rel + b"\0" + hashlib.sha256(data).digest())
        logical += len(data); files += 1
    return {"path": str(path), "files": files, "logical_bytes": logical, "sha256": digest.hexdigest()}


def terminal_marker(root: Path) -> dict[str, object] | None:
    for name in STATE_NAMES:
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        state = str(value.get("state", value.get("status", ""))).lower()
        if state in {"completed", "success", "successful", "rolled-back", "recovery-failed", "abandoned", "terminal"} or value.get("terminal") is True:
            return {"path": str(candidate), "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(), "state": state or "terminal"}
    return None


def durable_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)


def active_users(path: Path) -> bool:
    result = subprocess.run(["fuser", os.fspath(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transactions", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--keep-child", action="append", default=[])
    args = parser.parse_args()
    root = args.transactions.resolve()
    if not root.is_dir():
        raise SystemExit("REFUSED: prerequisite transaction root is not a directory")
    keep = set(args.keep_child)
    rows: list[dict[str, object]] = []
    for tx in sorted(p for p in root.iterdir() if p.is_dir()):
        marker = terminal_marker(tx)
        children = []
        for name in EPHEMERAL:
            child = tx / name
            if child.exists():
                children.append(tree_info(child) | {"name": name, "eligible": bool(marker and name not in keep)})
        rows.append({"transaction_id": tx.name, "path": str(tx), "terminal": bool(marker), "terminal_marker": marker, "children": children})
    retired: list[dict[str, object]] = []
    if args.execute:
        for row in rows:
            if not row["terminal"]:
                continue
            tx = Path(str(row["path"]))
            if active_users(tx):
                raise SystemExit(f"REFUSED: active process uses prerequisite transaction {tx}")
            for child in row["children"]:  # type: ignore[union-attr]
                if not child["eligible"]:
                    continue
                path = tx / str(child["name"])
                quarantine = tx / ("." + str(child["name"]) + ".retiring." + str(os.getpid()))
                os.replace(path, quarantine)
                retired.append({"transaction_id": tx.name, "name": child["name"], "before": child, "quarantine": str(quarantine)})
    payload = {"schema": "gentoo-optimization-prerequisite-retirement-v1", "timestamp": int(time.time()), "mode": "execute" if args.execute else "dry-run", "transactions": rows, "retired": retired, "unknown_retained": True}
    durable_write(args.receipt, payload)
    if args.execute:
        for row in retired:
            quarantine = Path(str(row["quarantine"]))
            if quarantine.exists():
                shutil.rmtree(quarantine)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

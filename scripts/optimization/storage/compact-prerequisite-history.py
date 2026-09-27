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
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

EPHEMERAL = ("tmp", "ccache", "thinlto-cache", "cargo-home", "rustup-home", "home", "xdg-cache", "distfiles.runtime", "distfiles.staging")
STATE_NAMES = ("terminal.json", "state.json", "transaction-state.json", "completed.json", "receipt.json")
RETRY_RETIREABLE = {
    "prepared-only-consumed",
    "locked-authority-only-consumed",
    "externally-reconciled-consumed-nonterminal",
    "rollback-in-progress-with-external-reconciliation",
    "terminal-rolled-back",
    "terminal-recovery-failed",
}


def tree_info(path: Path, *, hash_payload: bool = False) -> dict[str, object]:
    digest = hashlib.sha256()
    logical = files = 0
    if path.is_file():
        data = path.read_bytes(); digest.update(data)
        return {"path": str(path), "files": 1, "logical_bytes": len(data), "sha256": digest.hexdigest()}
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        try:
            stat = item.stat()
            logical += stat.st_size; files += 1
            if hash_payload:
                data = item.read_bytes()
                rel = str(item.relative_to(path)).encode()
                digest.update(rel + b"\0" + hashlib.sha256(data).digest())
        except OSError:
            continue
    return {"path": str(path), "files": files, "logical_bytes": logical, "sha256": digest.hexdigest()}


def terminal_marker(root: Path, state_dir: Path | None = None) -> dict[str, object] | None:
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
    if state_dir is not None:
        prefix = state_dir / ("jsonschema-prerequisite-" + root.name)
        for suffix in ("success", "rolled-back", "recovery-failed", "abandoned"):
            candidate = prefix.with_name(prefix.name + "." + suffix + ".json")
            if candidate.is_file():
                try:
                    value = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                return {"path": str(candidate), "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(), "state": suffix, "record": value}
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


def free_bytes(path: Path) -> int:
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize


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
    for name in ("emerge", "ebuild", "quickpkg"):
        if subprocess.run(["pgrep", "-x", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transactions", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--keep-child", action="append", default=[])
    parser.add_argument("--transaction", action="append", default=[],
                        help="limit the batch to these transaction IDs")
    parser.add_argument("--hash-payloads", action="store_true",
                        help="hash every payload file (required for execution)")
    parser.add_argument("--state-dir", type=Path, help="external durable prerequisite state directory")
    parser.add_argument("--retry-disposition", type=Path,
                        help="validated retry-disposition authority JSON")
    parser.add_argument("--reconciliation", type=Path,
                        help="external reconciliation authority JSON")
    parser.add_argument("--project-lock", type=Path)
    parser.add_argument("--generation-lock", type=Path)
    args = parser.parse_args()
    root = args.transactions.resolve()
    if not root.is_dir():
        raise SystemExit("REFUSED: prerequisite transaction root is not a directory")
    keep = set(args.keep_child)
    locks = []
    if args.execute:
        args.hash_payloads = True
        if active_portage():
            raise SystemExit("REFUSED: active Portage transaction")
        project_lock = args.project_lock or Path("/run/gentoo-optimization/project.lock")
        generation_lock = args.generation_lock or Path("/run/gentoo-optimization/generation.lock")
        if os.geteuid() == 0 or args.project_lock or args.generation_lock:
            locks = [acquire_lock(project_lock), acquire_lock(generation_lock)]
    free_before = free_bytes(root)
    rows: list[dict[str, object]] = []
    state_dir = args.state_dir.resolve() if args.state_dir else None
    disposition = {}
    disposition_sha256 = None
    if args.retry_disposition:
        try:
            raw = args.retry_disposition.resolve().read_bytes()
            authority = json.loads(raw)
            if authority.get("schema") != "gentoo-optimization-jsonschema-prerequisite-retry-disposition-v1":
                raise ValueError("unexpected retry-disposition schema")
            authority_rows = authority.get("rows")
            if not isinstance(authority_rows, list):
                raise ValueError("retry-disposition rows missing")
            disposition = {str(row["transaction_id"]): row for row in authority_rows
                           if isinstance(row, dict) and row.get("transaction_id")}
            disposition_sha256 = hashlib.sha256(raw).hexdigest()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SystemExit(f"REFUSED: invalid retry-disposition authority: {exc}")
    reconciliation = {}
    if args.reconciliation:
        try:
            reconciliation = json.loads(args.reconciliation.resolve().read_text(encoding="utf-8"))
            if not isinstance(reconciliation, dict):
                raise ValueError("reconciliation must be an object")
        except (OSError, ValueError, TypeError) as exc:
            raise SystemExit(f"REFUSED: invalid reconciliation authority: {exc}")
    selected = set(args.transaction)
    for tx in sorted(p for p in root.iterdir() if p.is_dir() and (not selected or p.name in selected)):
        marker = terminal_marker(tx, state_dir)
        auth = disposition.get(tx.name)
        classification = auth.get("classification") if auth else None
        reconciled = tx.name in reconciliation
        authorized = bool(auth and auth.get("reusable") is False and classification in RETRY_RETIREABLE)
        if classification in {"externally-reconciled-consumed-nonterminal", "rollback-in-progress-with-external-reconciliation"} and not reconciled:
            authorized = False
        children = []
        for name in EPHEMERAL:
            child = tx / name
            if child.exists():
                children.append(tree_info(child, hash_payload=args.hash_payloads) | {"name": name, "eligible": bool((marker or authorized) and name not in keep)})
        rows.append({"transaction_id": tx.name, "path": str(tx), "terminal": bool(marker),
                     "terminal_marker": marker, "retry_disposition": auth,
                     "retry_classification": classification, "reconciled": reconciled,
                     "payload_retirement_authorized": bool(marker or authorized), "children": children})
    retired: list[dict[str, object]] = []
    if args.execute:
        for row in rows:
            if not row["payload_retirement_authorized"]:
                continue
            tx = Path(str(row["path"]))
            if active_users(tx):
                raise SystemExit(f"REFUSED: active process uses prerequisite transaction {tx}")
            for child in row["children"]:  # type: ignore[union-attr]
                if not child["eligible"]:
                    continue
                path = tx / str(child["name"])
                quarantine = tx / ("." + str(child["name"]) + ".retiring." + str(os.getpid()))
                prepared = {"schema": "gentoo-optimization-prerequisite-retirement-prepared-v1", "timestamp": int(time.time()), "transaction_id": tx.name, "child": child, "source": str(path), "quarantine": str(quarantine)}
                durable_write(args.receipt.with_suffix(args.receipt.suffix + ".prepared.json"), prepared)
                os.replace(path, quarantine)
                retired.append({"transaction_id": tx.name, "name": child["name"], "before": child, "quarantine": str(quarantine)})
    if args.execute:
        try:
            for row in retired:
                quarantine = Path(str(row["quarantine"]))
                if quarantine.exists():
                    shutil.rmtree(quarantine)
        except BaseException:
            for row in reversed(retired):
                quarantine = Path(str(row["quarantine"]))
                source = Path(str(row["before"]["path"]))
                if quarantine.exists() and not source.exists():
                    os.replace(quarantine, source)
            raise
    free_after = free_bytes(root)
    payload = {"schema": "gentoo-optimization-prerequisite-retirement-v2", "timestamp": int(time.time()), "mode": "execute" if args.execute else "dry-run", "transactions": rows, "retired": retired, "unknown_retained": True, "retry_disposition_authority": str(args.retry_disposition.resolve()) if args.retry_disposition else None, "retry_disposition_sha256": disposition_sha256, "reconciliation_authority": str(args.reconciliation.resolve()) if args.reconciliation else None, "filesystem_free_bytes_before": free_before, "filesystem_free_bytes_after": free_after, "filesystem_free_delta": free_after - free_before}
    durable_write(args.receipt, payload)
    for handle in locks:
        handle.close()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

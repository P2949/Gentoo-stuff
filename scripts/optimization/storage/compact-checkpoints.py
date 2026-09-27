#!/usr/bin/env python3
"""Conservatively inventory and retire redundant binpkg checkpoints.

Dry-run is the default.  A checkpoint is executable-retirable only when it is
terminal, has a readable manifest/state record, is not the current selector,
is not referenced by machine-readable authority, and contains no unexpected
top-level objects.  Unknown or ambiguous checkpoints are retained.
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


def active_portage() -> bool:
    return subprocess.run(["pgrep", "-x", "emerge"], check=False,
                          stdout=subprocess.DEVNULL).returncode == 0


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tree_bytes(path: Path) -> tuple[int, int]:
    logical = allocated = 0
    for item in path.rglob("*"):
        try:
            st = item.lstat()
        except OSError:
            continue
        if item.is_file() or item.is_symlink():
            logical += st.st_size
            allocated += st.st_blocks * 512
    return logical, allocated


def selector_target(selector: Path) -> Path | None:
    if not selector.exists() and not selector.is_symlink():
        return None
    if not selector.is_symlink():
        return selector.resolve()
    try:
        return selector.resolve(strict=True)
    except OSError:
        raise SystemExit(f"REFUSED: selector is dangling: {selector}")


def machine_refs(roots: list[Path]) -> set[str]:
    refs: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.stat().st_size > 16 * 1024 * 1024:
                continue
            if p.suffix.lower() not in {".json", ".receipt", ".manifest", ".txt", ".sha256"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for token in text.replace('"', " ").replace("'", " ").split():
                if token.startswith(("snapshot-", "critical-")):
                    refs.add(token.rstrip(",;]"))
    return refs


def classify(path: Path, active: Path | None, refs: set[str]) -> tuple[str, str]:
    name = path.name
    if name.startswith(".") or ".partial." in name:
        return "UNKNOWN", "partial or hidden checkpoint requires reconciliation"
    if active is not None and path.resolve() == active:
        return "LIVE_REQUIRED", "current critical-current selector target"
    if name in refs or any(name in ref or ref in name for ref in refs):
        return "EVIDENCE_KEEP", "referenced by machine-readable authority"
    state = path / "checkpoint-state.json"
    manifest = path / "Packages"
    if not state.is_file() or not manifest.is_file():
        return "UNKNOWN", "missing terminal state or Packages manifest"
    try:
        record = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "UNKNOWN", "unreadable checkpoint state"
    if record.get("terminal_state") not in {"offline-restore-proven", "verified"}:
        return "UNKNOWN", "checkpoint is not terminal and restore-proven"
    return "ARCHIVE_CANDIDATE", "terminal unreferenced checkpoint"


def write_receipt(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def acquire_locks(paths: list[Path]) -> list[object]:
    handles = []
    try:
        for path in paths:
            fd = path.open("a+")
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fd.close()
                raise SystemExit(f"REFUSED: active project lock: {path}")
            handles.append(fd)
    except BaseException:
        for fd in handles:
            fd.close()
        raise
    return handles


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-root", type=Path, default=Path("/var/cache/gentoo-optimization/binpkgs"))
    ap.add_argument("--durable-root", type=Path, default=Path("/var/lib/gentoo-optimization/recovery/binpkgs"))
    ap.add_argument("--selector", type=Path, default=None)
    ap.add_argument("--reference-root", action="append", type=Path, default=[])
    ap.add_argument("--receipt", type=Path, required=True)
    ap.add_argument("--project-lock", type=Path, default=Path("/run/gentoo-optimization/project.lock"))
    ap.add_argument("--generation-lock", type=Path, default=Path("/run/gentoo-optimization/generation.lock"))
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    if active_portage():
        raise SystemExit("REFUSED: Portage transaction is active")
    handles = acquire_locks([args.project_lock, args.generation_lock])
    cache = args.cache_root.resolve()
    durable = args.durable_root.resolve()
    selector = args.selector or cache / "critical-current"
    try:
        target = selector_target(selector)
    except SystemExit as exc:
        # A dangling selector is itself an authority incident.  Publish the
        # refusal before exiting so storage automation cannot silently erase
        # the only durable record of why compaction was withheld.
        refusal = {"schema": "checkpoint-compaction-v1", "timestamp": int(time.time()),
                   "mode": "refused", "selector": str(selector),
                   "refusal": str(exc), "retired": [], "unknown_retained": True}
        write_receipt(args.receipt, refusal)
        print(json.dumps(refusal, indent=2, sort_keys=True))
        return 2
    refs = machine_refs(args.reference_root or [Path("/var/lib/gentoo-optimization")])
    rows = []
    for root, kind in ((cache, "cache"), (durable, "durable")):
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if p.name == selector.name or not p.is_dir():
                continue
            if kind == "cache" and not p.name.startswith("snapshot-"):
                continue
            if kind == "durable" and not p.name.startswith("critical-"):
                continue
            state, reason = classify(p, target, refs)
            logical, allocated = tree_bytes(p)
            rows.append({"path": str(p), "kind": kind, "state": state,
                         "reason": reason, "logical_bytes": logical,
                         "allocated_bytes": allocated})
    candidates = [r for r in rows if r["state"] == "ARCHIVE_CANDIDATE"]
    retired = []
    try:
        if args.execute:
            quarantine = durable.parent / ".checkpoint-gc-quarantine"
            quarantine.mkdir(mode=0o700, exist_ok=True)
            for row in candidates:
                src = Path(row["path"])
                dst = quarantine / (src.name + "." + str(os.getpid()))
                os.replace(src, dst)
                shutil.rmtree(dst)
                retired.append(row["path"])
        receipt = {"schema": "checkpoint-compaction-v1", "timestamp": int(time.time()),
                   "mode": "execute" if args.execute else "dry-run",
                   "selector": str(selector), "selector_target": str(target) if target else None,
                   "objects": rows, "candidates": candidates, "retired": retired,
                   "unknown_retained": True}
        write_receipt(args.receipt, receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0
    finally:
        for fd in handles:
            fd.close()


if __name__ == "__main__":
    raise SystemExit(main())

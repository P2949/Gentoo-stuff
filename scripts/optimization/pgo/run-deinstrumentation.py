#!/usr/bin/env python3
"""Run one resumable, exact-CPV de-instrumentation batch.

The durable ``deinstrument.pending`` marker is intentionally not cleared by
this command.  A fresh census and the independent verifier must prove a clean
installed state before an operator invokes a separate marker-clear action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

MARKER = Path("/var/lib/gentoo-optimization/state/deinstrument.pending")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_plan(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"REFUSED: invalid de-instrumentation plan: {exc}")
    if data.get("schema") != "deinstrumentation-plan-v1":
        raise SystemExit("REFUSED: unsupported de-instrumentation plan schema")
    batches = data.get("batches")
    if not isinstance(batches, list) or not batches:
        raise SystemExit("REFUSED: plan contains no batches")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--batch-id", type=int, required=True)
    ap.add_argument("--receipt-dir", type=Path, required=True)
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not MARKER.exists():
        raise SystemExit("REFUSED: deinstrument.pending marker is absent")
    plan = load_plan(args.plan)
    selected = [b for b in plan["batches"] if b.get("batch_id") == args.batch_id]
    if len(selected) != 1:
        raise SystemExit(f"REFUSED: batch id is not unique: {args.batch_id}")
    batch = selected[0]
    cpvs = batch.get("cpvs")
    if not isinstance(cpvs, list) or not cpvs or any(
        not isinstance(c, str) or c.count("/") != 1 or c.startswith("/") for c in cpvs
    ):
        raise SystemExit("REFUSED: batch contains malformed CPVs")
    cpvs = sorted(set(cpvs))
    receipt = args.receipt_dir / f"batch-{args.batch_id:04d}.json"
    log = args.log_dir / f"batch-{args.batch_id:04d}.log"
    if receipt.exists() or log.exists():
        raise SystemExit("REFUSED: batch evidence already exists; refusing overwrite")
    args.receipt_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    atom_args = [f"={cpv}" for cpv in cpvs]
    env = dict(os.environ)
    env.update(
        {
            "LLVM_PROFILE_FILE": "/dev/null",
            "GENTOO_OPT_DEINSTRUMENT": "1",
            "GENTOO_OPT_MODE": "off",
        }
    )
    command = [
        "emerge",
        "--oneshot",
        "--nodeps",
        "--usepkg=n",
        "--buildpkg=n",
        "--quiet-build=y",
        *atom_args,
    ]
    started = time.time()
    if args.dry_run:
        print(json.dumps({"batch_id": args.batch_id, "cpvs": cpvs, "command": command}, sort_keys=True))
        return 0
    with log.open("x", encoding="utf-8") as stream:
        stream.write("COMMAND: " + " ".join(command) + "\n")
        stream.write("CPVS: " + " ".join(cpvs) + "\n")
        stream.flush()
        proc = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, env=env)
    finished = time.time()
    record = {
        "schema": "deinstrumentation-batch-receipt-v1",
        "batch_id": args.batch_id,
        "plan": {"path": str(args.plan.resolve()), "sha256": digest(args.plan)},
        "cpvs": cpvs,
        "marker": str(MARKER),
        "command": command,
        "environment": {"GENTOO_OPT_MODE": "off", "GENTOO_OPT_DEINSTRUMENT": "1", "LLVM_PROFILE_FILE": "/dev/null"},
        "started_epoch": started,
        "finished_epoch": finished,
        "exit_status": proc.returncode,
        "log": {"path": str(log.resolve()), "sha256": digest(log)},
        "marker_cleared": False,
    }
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(record, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())

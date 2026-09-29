#!/usr/bin/env python3
"""Extend an authenticated de-instrumentation marker after a completed batch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

MARKER = Path("/var/lib/gentoo-optimization/state/deinstrument.pending")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--batch-id", type=int, required=True)
    ap.add_argument("--receipt", type=Path, required=True)
    ap.add_argument("--marker", type=Path, default=MARKER)
    args = ap.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("REFUSED: marker extension requires root")
    if not args.marker.is_file() or args.marker.is_symlink():
        raise SystemExit("REFUSED: existing marker is not a regular file")
    try:
        old = json.loads(args.marker.read_text(encoding="utf-8"))
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"REFUSED: invalid extension input: {exc}")
    if old.get("schema") != "deinstrument-pending-v1":
        raise SystemExit("REFUSED: existing marker schema is invalid")
    old_plan = Path(old.get("plan", ""))
    old_cpvs = sorted(set(old.get("cpvs", [])))
    if not old_plan.is_file() or old.get("plan_sha256") != digest(old_plan):
        raise SystemExit("REFUSED: existing marker plan is not authenticated")
    if receipt.get("schema") != "deinstrumentation-batch-receipt-v1":
        raise SystemExit("REFUSED: predecessor receipt schema is invalid")
    predecessor_ok = receipt.get("exit_status") == 0
    if not predecessor_ok:
        failed_cpvs = set()
        for package in receipt.get("packages", []):
            if package.get("exit_status") != 0:
                failed_cpvs.add(package.get("cpv"))
        accounted = set(plan.get("accounting", {}).get("inspection_failed_cpvs", []))
        if not failed_cpvs or not failed_cpvs.issubset(accounted):
            raise SystemExit("REFUSED: failed predecessor is not explicitly accounted as inspection_failed")
    if receipt.get("plan", {}).get("path") != str(old_plan) or receipt.get("plan", {}).get("sha256") != digest(old_plan):
        raise SystemExit("REFUSED: predecessor receipt does not match marker plan")
    batches = [b for b in plan.get("batches", []) if b.get("batch_id") == args.batch_id]
    if len(batches) != 1:
        raise SystemExit("REFUSED: extension batch id is not unique")
    new_cpvs = sorted(set(batches[0].get("cpvs", [])))
    retry_cpvs = set(plan.get("retry_cpvs", []))
    if not retry_cpvs.issubset(set(old_cpvs)):
        raise SystemExit("REFUSED: retry_cpvs must already be marker-authorized")
    if not new_cpvs or (set(old_cpvs) & set(new_cpvs)) - retry_cpvs:
        raise SystemExit("REFUSED: extension batch overlaps an already-authorized CPV")
    merged = sorted(set(old_cpvs) | set(new_cpvs))
    payload = {
        "schema": "deinstrument-pending-v1",
        "state": "armed",
        "plan": str(args.plan.resolve()),
        "plan_sha256": digest(args.plan),
        "batch_id": args.batch_id,
        "cpvs": merged,
    }
    args.marker.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".deinstrument.pending.", dir=args.marker.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, args.marker)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(json.dumps({"extended": True, "batch_id": args.batch_id, "cpvs": new_cpvs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Atomically arm one reviewed de-instrumentation batch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

MARKER = Path("/var/lib/gentoo-optimization/state/deinstrument.pending")

def marker_identity(payload: dict) -> dict:
    """Return the stable marker fields used for idempotence checks."""
    return {key: value for key, value in payload.items() if key != "created_epoch"}

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--batch-id", type=int, required=True)
    ap.add_argument("--marker", type=Path, default=MARKER)
    args = ap.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("REFUSED: marker arming requires root")
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"REFUSED: invalid plan: {exc}")
    if plan.get("schema") != "deinstrumentation-plan-v1":
        raise SystemExit("REFUSED: unsupported plan schema")
    batches = [b for b in plan.get("batches", []) if b.get("batch_id") == args.batch_id]
    if len(batches) != 1 or not isinstance(batches[0].get("cpvs"), list) or not batches[0]["cpvs"]:
        raise SystemExit("REFUSED: batch is absent or empty")
    cpvs = sorted(set(batches[0]["cpvs"]))
    payload = {
        "schema": "deinstrument-pending-v1",
        "plan": str(args.plan.resolve()),
        "plan_sha256": sha256(args.plan),
        "batch_id": args.batch_id,
        "cpvs": cpvs,
        "state": "armed",
        "created_epoch": time.time(),
    }
    if args.marker.exists():
        try:
            current = json.loads(args.marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"REFUSED: existing marker is invalid: {exc}")
        # ``created_epoch`` records the first successful arm and is therefore
        # deliberately excluded from the equality check.  Re-running the
        # same authenticated arm must be idempotent rather than refusing its
        # own marker because the newly constructed timestamp differs.
        if marker_identity(current) != marker_identity(payload):
            raise SystemExit("REFUSED: a different de-instrumentation marker is already armed")
        print(json.dumps({"armed": True, "unchanged": True, "marker": str(args.marker)}, sort_keys=True))
        return 0
    args.marker.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".deinstrument.pending.", dir=args.marker.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.rename(temporary, args.marker)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(json.dumps({"armed": True, "marker": str(args.marker), "batch_id": args.batch_id}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

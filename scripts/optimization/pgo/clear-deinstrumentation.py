#!/usr/bin/env python3
"""Clear the durable de-instrumentation marker after an authenticated scan."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

MARKER = Path("/var/lib/gentoo-optimization/state/deinstrument.pending")

TERMINAL_PREBUILT = "unsupported-by-upstream-toolchain/prebuilt"

def terminal_clean(record: dict) -> bool:
    """Return whether a census record is an authenticated acceptable terminal state."""
    return record.get("terminal_disposition") == TERMINAL_PREBUILT

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan_digest(scan: dict) -> str:
    """Return the digest domain used by scan-live-instrumentation.py.

    The scanner authenticates its canonical JSON payload rather than the raw
    file bytes.  Recomputing that same unsigned payload here keeps the clear
    transition compatible with freshly produced scans.
    """
    unsigned = dict(scan)
    unsigned.pop("sha256", None)
    return hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", type=Path, required=True)
    ap.add_argument("--census", type=Path, required=True)
    ap.add_argument("--mutation-policy", type=Path, required=True)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--marker", type=Path, default=MARKER)
    args = ap.parse_args()
    if not args.scan.is_file():
        raise SystemExit(f"REFUSED: scan does not exist: {args.scan}")
    try:
        scan = json.loads(args.scan.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"REFUSED: invalid scan: {exc}")
    records = scan.get("records")
    if not isinstance(records, list):
        raise SystemExit("REFUSED: scan has no records list")
    if scan.get("record_type") != "live-instrumentation-census" or scan.get("schema_version") != 2:
        raise SystemExit("REFUSED: unsupported scan authority schema")
    if scan.get("sha256") != scan_digest(scan):
        raise SystemExit("REFUSED: scan canonical digest mismatch")
    if scan.get("source_census_sha256") != sha256(args.census):
        raise SystemExit("REFUSED: scan/census identity mismatch")
    if scan.get("mutation_policy_sha256") != sha256(args.mutation_policy):
        raise SystemExit("REFUSED: scan/mutation-policy identity mismatch")
    try:
        marker = json.loads(args.marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"REFUSED: invalid de-instrumentation marker: {exc}")
    if marker.get("schema") != "deinstrument-pending-v1":
        raise SystemExit("REFUSED: unsupported de-instrumentation marker schema")
    if marker.get("plan") != str(args.plan.resolve()) or marker.get("plan_sha256") != sha256(args.plan):
        raise SystemExit("REFUSED: marker/plan identity mismatch")
    instrumented = [
        r for r in records
        if r.get("instrumentation_markers")
        and not terminal_clean(r)
    ]
    if instrumented:
        raise SystemExit(f"REFUSED: {len(instrumented)} instrumented records remain")
    unresolved = [
        r for r in records
        if r.get("error")
        and not terminal_clean(r)
    ]
    if unresolved:
        raise SystemExit("REFUSED: scan contains unresolved inspection failures")
    if not args.marker.is_file() or args.marker.is_symlink():
        raise SystemExit("REFUSED: de-instrumentation marker is not a regular file")
    if os.geteuid() != 0:
        raise SystemExit("REFUSED: marker clear requires root")
    args.marker.unlink()
    print(json.dumps({"marker": str(args.marker), "cleared": True, "scan": str(args.scan)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

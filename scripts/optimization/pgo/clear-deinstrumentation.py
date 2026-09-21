#!/usr/bin/env python3
"""Clear the durable de-instrumentation marker after an authenticated scan."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

MARKER = Path("/var/lib/gentoo-optimization/state/deinstrument.pending")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", type=Path, required=True)
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
    instrumented = [r for r in records if r.get("instrumentation_markers")]
    if instrumented:
        raise SystemExit(f"REFUSED: {len(instrumented)} instrumented records remain")
    unexpected = [r for r in records if not r.get("error")]
    if unexpected:
        raise SystemExit("REFUSED: scan contains unexplained non-instrumented records")
    if not args.marker.is_file() or args.marker.is_symlink():
        raise SystemExit("REFUSED: de-instrumentation marker is not a regular file")
    if os.geteuid() != 0:
        raise SystemExit("REFUSED: marker clear requires root")
    args.marker.unlink()
    print(json.dumps({"marker": str(args.marker), "cleared": True, "scan": str(args.scan)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

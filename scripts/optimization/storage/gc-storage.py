#!/usr/bin/env python3
"""Fail-closed storage GC driven by a retention-set report.

Dry-run is the default. Execution requires --execute and only considers rows
explicitly marked ARCHIVE_CANDIDATE; UNKNOWN and all authority states remain.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def active_portage() -> bool:
    result = subprocess.run(["pgrep", "-x", "emerge"], check=False, stdout=subprocess.DEVNULL)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retention", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    report = json.loads(args.retention.read_text(encoding="utf-8"))
    if report.get("schema") != "storage-retention-set-v1":
        raise SystemExit("REFUSED: unsupported retention report schema")
    if active_portage():
        raise SystemExit("REFUSED: Portage transaction is active")
    candidates = [Path(row["path"]) for row in report.get("objects", []) if row.get("state") == "ARCHIVE_CANDIDATE"]
    unknown = [row for row in report.get("objects", []) if row.get("state") == "UNKNOWN"]
    deleted: list[str] = []
    if args.execute:
        quarantine = Path("/var/tmp/gentoo-optimization/storage-gc-quarantine")
        quarantine.mkdir(parents=True, exist_ok=True)
        for path in candidates:
            if not path.is_dir() or not path.is_absolute():
                raise SystemExit(f"REFUSED: invalid candidate {path}")
            target = quarantine / (path.name + "." + str(os.getpid()))
            os.replace(path, target)
            shutil.rmtree(target)
            deleted.append(str(path))
    receipt = {
        "schema": "storage-gc-v1",
        "timestamp": int(time.time()),
        "mode": "execute" if args.execute else "dry-run",
        "candidates": [str(p) for p in candidates],
        "deleted": deleted,
        "unknown_count": len(unknown),
        "unknown_retained": True,
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    temp = args.receipt.with_suffix(args.receipt.suffix + ".tmp")
    temp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, args.receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

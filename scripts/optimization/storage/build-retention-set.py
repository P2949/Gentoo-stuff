#!/usr/bin/env python3
"""Build a conservative retention set for project-owned storage.

This tool never deletes. Every object not positively classified is UNKNOWN and
therefore retained. References are gathered from machine-readable state under
the project roots; Markdown and file age are deliberately ignored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path


def references(roots: list[Path]) -> set[str]:
    needles: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                continue
            # Authority is machine-readable; avoid walking large binary
            # payloads and profile archives merely to search their bytes.
            if path.suffix.lower() not in {".json", ".txt", ".manifest", ".env", ".sha256", ".receipt"}:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            text = data.decode("utf-8", "ignore")
            needles.update(re.findall(r"phase3-live-[A-Za-z0-9._-]+", text))
            needles.update(re.findall(r"checkpoint-[A-Za-z0-9._-]+", text))
    return needles


def classify(path: Path, refs: set[str], active: str | None) -> tuple[str, str]:
    name = path.name
    if active and (name == active or active in os.fspath(path)):
        return "LIVE_REQUIRED", "active generation"
    if any(ref and ref in os.fspath(path) for ref in refs):
        return "EVIDENCE_KEEP", "referenced by machine-readable state"
    if "partial" in name or name.startswith("."):
        return "UNKNOWN", "temporary/hidden object requires terminal-state proof"
    if "pgo-raw" in os.fspath(path) and name.startswith("phase3-live-"):
        return "ARCHIVE_CANDIDATE", "unreferenced generation raw spool"
    return "UNKNOWN", "no positive retention proof"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--active-generation")
    parser.add_argument("--root", action="append", type=Path, default=[])
    parser.add_argument("--raw-root", type=Path, default=Path("/var/tmp/gentoo-optimization/pgo-raw"))
    args = parser.parse_args()
    roots = args.root or [Path("/var/lib/gentoo-optimization"), Path("/var/cache/gentoo-optimization")]
    refs = references(roots)
    objects = sorted(p for p in args.raw_root.iterdir() if p.is_dir()) if args.raw_root.is_dir() else []
    rows = []
    for path in objects:
        state, reason = classify(path, refs, args.active_generation)
        rows.append({"path": str(path), "state": state, "reason": reason})
    report = {
        "schema": "storage-retention-set-v1",
        "active_generation": args.active_generation,
        "referenced_tokens": sorted(refs),
        "objects": rows,
        "unknown_count": sum(r["state"] == "UNKNOWN" for r in rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

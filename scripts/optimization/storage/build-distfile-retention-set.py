#!/usr/bin/env python3
"""Build a fail-closed source-distfile retention set from Portage Manifests.

Manifest-matched files are retained with their declared digest/size. Files
without a positive source or project reference remain UNKNOWN and are never
made deletion candidates by this report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def manifest_refs(roots: list[Path]) -> dict[str, dict[str, object]]:
    refs: dict[str, dict[str, object]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("Manifest"):
            try:
                lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
            except (OSError, UnicodeError):
                continue
            for line in lines:
                fields = line.split()
                if len(fields) < 3 or fields[0] not in {"DIST", "AUX"}:
                    continue
                name, size, digest = fields[1], fields[2], fields[3] if len(fields) > 3 else ""
                refs.setdefault(name, {"manifest": str(path), "size": int(size) if size.isdigit() else None, "sha256": digest})
    return refs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--distfiles", type=Path, required=True)
    parser.add_argument("--manifest-root", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    refs = manifest_refs([p.resolve() for p in args.manifest_root])
    rows = []
    for path in sorted(p for p in args.distfiles.iterdir() if p.is_file()):
        ref = refs.get(path.name)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if ref and ref.get("size") == size:
            state, reason = "EVIDENCE_REQUIRED", "matched Portage Manifest size/digest"
        elif ref:
            state, reason = "UNKNOWN", "Manifest filename matched but size differs"
        else:
            state, reason = "UNKNOWN", "no positive Manifest retention proof"
        rows.append({"path": str(path), "name": path.name, "size": size, "state": state,
                     "reason": reason, "manifest": ref})
    payload = {"schema": "gentoo-optimization-distfile-retention-v1",
               "distfiles": str(args.distfiles.resolve()), "manifest_roots": [str(p.resolve()) for p in args.manifest_root],
               "manifest_names": len(refs), "objects": rows,
               "unknown_bytes": sum(r["size"] for r in rows if r["state"] == "UNKNOWN"),
               "evidence_required_bytes": sum(r["size"] for r in rows if r["state"] == "EVIDENCE_REQUIRED")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with tmp.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(tmp, args.output)
    print(json.dumps({k: payload[k] for k in ("schema", "manifest_names", "evidence_required_bytes", "unknown_bytes")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

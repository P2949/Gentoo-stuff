#!/usr/bin/env python3
"""Compact a sealed raw-profile attempt without losing member identity."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path


def manifest(root: Path) -> list[dict[str, object]]:
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({"path": str(path.relative_to(root)), "size": path.stat().st_size, "sha256": digest})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    root = args.attempt.resolve()
    if not root.is_dir():
        raise SystemExit("REFUSED: attempt spool is not a directory")
    if (root / "pending").exists() or (root / "unresolved").exists():
        raise SystemExit("REFUSED: unresolved attempt cannot be compacted")
    members = manifest(root)
    if not members:
        raise SystemExit("REFUSED: empty attempt spool")
    payload = {"schema": "profile-archive-manifest-v1", "attempt": str(root), "members": members}
    if args.execute:
        args.archive.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        partial = args.archive.with_suffix(args.archive.suffix + ".partial")
        with tarfile.open(partial, "w") as tar:
            for row in members:
                tar.add(root / str(row["path"]), arcname=str(row["path"]), recursive=False)
        compressed = args.archive.with_suffix(args.archive.suffix + ".zst")
        subprocess.run(["zstd", "-T0", "-19", "--rm", os.fspath(partial), "-o", os.fspath(compressed)], check=True)
        args.manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # Re-open through zstd/tar and compare every member before retirement.
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["tar", "--zstd", "-xf", os.fspath(compressed), "-C", tmp], check=True)
            observed = manifest(Path(tmp))
            if observed != members:
                raise SystemExit("REFUSED: compressed archive failed member verification")
    print(json.dumps({"schema": "profile-archive-plan-v1", "attempt": str(root), "members": len(members), "execute": args.execute}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

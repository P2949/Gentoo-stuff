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
import shutil
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
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--seal-record", type=Path,
                        help="machine-readable completed/validated attempt receipt required for retirement")
    parser.add_argument("--retire-expanded", action="store_true",
                        help="remove the expanded spool only after archive reconstruction verification")
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
    if args.retire_expanded and not args.execute:
        raise SystemExit("REFUSED: --retire-expanded requires --execute")
    if args.retire_expanded:
        if args.seal_record is None or not args.seal_record.is_file():
            raise SystemExit("REFUSED: retirement requires a sealed attempt receipt")
        try:
            seal = json.loads(args.seal_record.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"REFUSED: unreadable seal record: {exc}")
        if seal.get("status") not in {"completed", "merged-validated", "validated"}:
            raise SystemExit("REFUSED: attempt receipt is not completed and validated")
        payload["seal_record"] = {"path": str(args.seal_record.resolve()),
                                   "sha256": hashlib.sha256(args.seal_record.read_bytes()).hexdigest()}
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
        if args.retire_expanded:
            # Do not remove the seal record or archive/manifest; only retire
            # the expanded representation after a full reconstruction check.
            shutil.rmtree(root)
    result = {"schema": "profile-archive-plan-v1", "attempt": str(root),
              "members": len(members), "execute": args.execute,
              "retired_expanded": bool(args.execute and args.retire_expanded)}
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

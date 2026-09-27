#!/usr/bin/env python3
"""Build fail-closed authority for compacting an expanded raw-profile attempt.

The utility only authorizes compaction when an immutable, completed/validated
receipt explicitly binds the attempt and generation.  Missing or ambiguous
evidence produces KEEP_EXPANDED and never mutates the spool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    attempt = args.attempt.resolve()
    if not attempt.is_dir():
        raise SystemExit("REFUSED: attempt spool is not a directory")
    candidates = []
    for root in args.evidence_root:
        root = root.resolve()
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(value, dict):
                continue
            if value.get("status") not in {"completed", "validated", "merged-validated"}:
                continue
            bound = {str(value.get(k)) for k in ("attempt", "attempt_path", "profile_root") if value.get(k)}
            if str(attempt) in bound:
                candidates.append((path, value))
    if len(candidates) != 1:
        state = "KEEP_EXPANDED"
        reason = "no unique completed/validated receipt binds attempt" if not candidates else "ambiguous completed/validated receipts bind attempt"
    else:
        path, value = candidates[0]
        state = "COMPACTION_AUTHORIZED"
        reason = "unique immutable completed/validated receipt binds attempt"
    payload = {
        "schema": "gentoo-optimization-profile-compaction-authority-v1",
        "attempt": str(attempt),
        "decision": state,
        "reason": reason,
        "candidates": [{"path": str(p), "sha256": sha256(p), "status": v.get("status")} for p, v in candidates],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with tmp.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(tmp, args.output)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

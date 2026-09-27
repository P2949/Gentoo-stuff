#!/usr/bin/env python3
"""Emit one immutable BOLT quality-command record from a completed command."""
from __future__ import annotations
import argparse, hashlib, json, os, stat
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "gentoo-optimization-bolt-quality-command-v1"
ROOTS = (Path("/var/cache/gentoo-optimization"), Path("/var/lib/gentoo-optimization"))

def identity(path: Path) -> dict[str, object]:
    p = path.resolve(strict=True)
    st = p.stat()
    if not stat.S_ISREG(st.st_mode): raise SystemExit(f"not a regular evidence file: {p}")
    if st.st_uid != 0 or stat.S_IMODE(st.st_mode) & 0o022: raise SystemExit(f"untrusted evidence file: {p}")
    data = p.read_bytes()
    return {"path": str(p), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

def allowed(path: Path) -> bool:
    p = path.resolve(strict=True)
    return any(p == root or root in p.parents for root in ROOTS)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True)
    ap.add_argument("--argv", nargs="+")
    ap.add_argument("--argv-json")
    ap.add_argument("--tool", required=True, type=Path)
    ap.add_argument("--input", action="append", type=Path, default=[])
    ap.add_argument("--stdout", required=True, type=Path)
    ap.add_argument("--stderr", required=True, type=Path)
    ap.add_argument("--metrics", required=True, type=Path)
    ap.add_argument("--started", required=True)
    ap.add_argument("--completed", required=True)
    ap.add_argument("--exit-status", type=int, default=0)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    if args.argv is None and args.argv_json is None:
        raise SystemExit("one of --argv or --argv-json is required")
    argv = args.argv if args.argv is not None else json.loads(args.argv_json)
    if not isinstance(argv, list) or not all(isinstance(item, str) and item for item in argv):
        raise SystemExit("argv must be a nonempty string array")
    if any(not allowed(p) for p in [*args.input, args.stdout, args.stderr, args.metrics]):
        raise SystemExit("production evidence must remain under approved roots")
    tool = identity(args.tool)
    inputs = [identity(p) for p in args.input]
    stdout, stderr = identity(args.stdout), identity(args.stderr)
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    for value in (args.started, args.completed):
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    doc = {"schema": SCHEMA, "role": args.role, "argv": argv,
           "environment": {"LC_ALL":"C", "LANG":"C", "PATH":"/usr/bin:/bin"},
           "tool": tool, "inputs": inputs, "stdout": stdout, "stderr": stderr,
           "exit_status": args.exit_status, "started_at_utc": args.started,
           "completed_at_utc": args.completed, "metrics": metrics}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(args.output, 0o640)
    print(args.output)
    return 0
if __name__ == "__main__": raise SystemExit(main())

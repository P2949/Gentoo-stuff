#!/usr/bin/env python3
"""Inventory Portage multi-instance binpkgs and emit a conservative prune plan."""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, time
from pathlib import Path

PKG_RE = re.compile(r"(?P<cpv>[^/]+-[0-9][^/]*)\.gpkg\.tar(?:\.(?:zst|gz|xz))?$")

def active_portage() -> bool:
    return subprocess.run(["pgrep", "-x", "emerge"], stdout=subprocess.DEVNULL, check=False).returncode == 0

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path("/var/cache/binpkgs"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()
    if a.execute and active_portage():
        raise SystemExit("REFUSED: Portage transaction is active")
    rows = []
    if a.root.is_dir():
        for f in a.root.rglob("*"):
            if not f.is_file(): continue
            m = PKG_RE.search(f.name)
            if not m: continue
            st = f.stat()
            rows.append({"path": str(f), "cpv_hint": m.group("cpv"), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                         "sha256": hashlib.sha256(f.read_bytes()).hexdigest() if st.st_size <= 64*1024*1024 else None,
                         "state": "UNKNOWN"})
    rows.sort(key=lambda r: (r["cpv_hint"], r["mtime_ns"], r["path"]))
    report = {"schema": "binpkg-retention-v1", "timestamp": int(time.time()), "root": str(a.root),
              "mode": "execute" if a.execute else "dry-run", "objects": rows,
              "unknown_retained": True, "deleted": []}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.output.with_suffix(a.output.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, a.output)
    print(json.dumps({"schema": report["schema"], "objects": len(rows), "bytes": sum(r["size"] for r in rows), "mode": report["mode"]}, indent=2))
    return 0

if __name__ == "__main__": raise SystemExit(main())

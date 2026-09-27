#!/usr/bin/env python3
"""Inventory Portage multi-instance binpkgs and emit a conservative prune plan."""
from __future__ import annotations
import argparse, fcntl, hashlib, json, os, re, shutil, subprocess, time
from pathlib import Path

PKG_RE = re.compile(r"(?P<cpv>[^/]+-[0-9][^/]*)\.gpkg\.tar(?:\.(?:zst|gz|xz))?$")

def active_portage() -> bool:
    return subprocess.run(["pgrep", "-x", "emerge"], stdout=subprocess.DEVNULL, check=False).returncode == 0

def refs(roots: list[Path]) -> set[str]:
    found = set()
    for root in roots:
        if not root.is_dir(): continue
        for p in root.rglob("*"):
            if not p.is_file() or p.stat().st_size > 8 * 1024 * 1024: continue
            if p.suffix.lower() not in {".json", ".receipt", ".manifest", ".txt", ".sha256"}: continue
            try: text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError: continue
            found.update(re.findall(r"(?:snapshot|critical)-[A-Za-z0-9._-]+", text))
    return found

def installed_cpvs(vdb: Path) -> set[str]:
    result = set()
    if not vdb.is_dir(): return result
    for category in vdb.iterdir():
        if not category.is_dir(): continue
        for pf in category.iterdir():
            if pf.is_dir(): result.add(f"{category.name}/{pf.name}")
    return result

def locked(paths: list[Path]) -> list[object]:
    handles=[]
    try:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd=path.open("a+")
            try: fcntl.flock(fd.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                fd.close(); raise SystemExit(f"REFUSED: active storage lock: {path}")
            handles.append(fd)
    except BaseException:
        for fd in handles: fd.close()
        raise
    return handles

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path("/var/cache/binpkgs"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--prune-duplicates", action="store_true")
    p.add_argument("--reference-root", action="append", type=Path, default=[])
    p.add_argument("--vdb", type=Path, default=Path("/var/db/pkg"))
    p.add_argument("--project-lock", type=Path, default=Path("/run/gentoo-optimization/project.lock"))
    p.add_argument("--generation-lock", type=Path, default=Path("/run/gentoo-optimization/generation.lock"))
    a = p.parse_args()
    if a.execute and not a.prune_duplicates:
        raise SystemExit("REFUSED: --execute requires explicit --prune-duplicates")
    if a.execute and active_portage():
        raise SystemExit("REFUSED: Portage transaction is active")
    handles = locked([a.project_lock, a.generation_lock])
    rows = []
    references = refs(a.reference_root or [Path("/var/lib/gentoo-optimization")])
    live = installed_cpvs(a.vdb)
    if a.root.is_dir():
        for f in a.root.rglob("*"):
            if not f.is_file(): continue
            m = PKG_RE.search(f.name)
            if not m: continue
            st = f.stat()
            rel = f.relative_to(a.root)
            raw_cpv = m.group("cpv")
            # GLEP-78 archives append a build instance number after the CPV
            # (for example ``foo-1.2-3``).  Normalize that instance suffix so
            # retention groups actual CPVs rather than treating each build as
            # a distinct installed package.
            raw_cpv = re.sub(r"-[0-9]+$", "", raw_cpv)
            cpv_hint = f"{rel.parts[0]}/{raw_cpv}" if len(rel.parts) > 1 else raw_cpv
            rows.append({"path": str(f), "cpv_hint": cpv_hint, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                         "sha256": hashlib.sha256(f.read_bytes()).hexdigest() if st.st_size <= 64*1024*1024 else None,
                         "state": "UNKNOWN", "reason": "no positive retention decision"})
    rows.sort(key=lambda r: (r["cpv_hint"], r["mtime_ns"], r["path"]))
    by_cpv = {}
    for row in rows: by_cpv.setdefault(row["cpv_hint"], []).append(row)
    for cpv, group in by_cpv.items():
        group.sort(key=lambda r: (r["mtime_ns"], r["path"]), reverse=True)
        if cpv in references or any(Path(r["path"]).name in references for r in group):
            continue
        # Installed CPVs retain the newest convenience rollback instance. Older
        # duplicates are only candidates when the caller explicitly opts in.
        for row in group[1:] if cpv in live else []:
            row["state"] = "ARCHIVE_CANDIDATE"
            row["reason"] = "older duplicate for currently installed CPV"
        if group and cpv in live: group[0]["state"] = "LIVE_REQUIRED"; group[0]["reason"] = "newest instance for installed CPV"
    deleted=[]
    try:
        if a.execute:
            quarantine=a.root / ".gc-quarantine"
            quarantine.mkdir(mode=0o700, exist_ok=True)
            for row in rows:
                if row["state"] != "ARCHIVE_CANDIDATE": continue
                src=Path(row["path"]); dst=quarantine/(src.name+"."+str(os.getpid()))
                os.replace(src,dst); dst.unlink(); deleted.append(row["path"])
    finally:
        for fd in handles: fd.close()
    report = {"schema": "binpkg-retention-v1", "timestamp": int(time.time()), "root": str(a.root),
              "mode": "execute" if a.execute else "dry-run", "objects": rows,
              "unknown_retained": True, "deleted": deleted,
              "references": sorted(references), "live_cpvs": sorted(live)}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.output.with_suffix(a.output.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, a.output)
    print(json.dumps({"schema": report["schema"], "objects": len(rows), "bytes": sum(r["size"] for r in rows), "mode": report["mode"]}, indent=2))
    return 0

if __name__ == "__main__": raise SystemExit(main())

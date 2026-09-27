#!/usr/bin/env python3
"""Mark or retire content-addressed objects by explicit receipt reachability.

Fail-closed: an object is executable only when its digest is positively
unreferenced by a complete authority root; malformed or unknown objects remain
KEEP. Execute mode quarantines every candidate before unlinking it and restores
the original path if retirement fails.
"""
from __future__ import annotations
import argparse, fcntl, json, os, re, shutil, subprocess, time
from pathlib import Path

DIGEST = re.compile(r"^[0-9a-f]{64}$")

def collect(value: object, out: set[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k in {"sha256", "object_sha256"} and isinstance(v, str) and DIGEST.fullmatch(v):
                out.add(v)
            collect(v, out)
    elif isinstance(value, list):
        for item in value: collect(item, out)

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--objects",type=Path,required=True); p.add_argument("--reference-root",action="append",type=Path,required=True); p.add_argument("--output",type=Path,required=True); p.add_argument("--execute",action="store_true"); p.add_argument("--quarantine-root",type=Path,default=Path("/var/tmp/gentoo-optimization/storage-object-gc-quarantine")); a=p.parse_args()
    if a.execute and subprocess.run(["pgrep","-x","emerge"],stdout=subprocess.DEVNULL,check=False).returncode == 0:
        raise SystemExit("REFUSED: active Portage transaction")
    if a.execute and Path("/var/lib/gentoo-optimization") not in [r.resolve() for r in a.reference_root]:
        raise SystemExit("REFUSED: execute requires the complete optimization authority root")
    refs=set()
    for root in a.reference_root:
        if not root.is_dir(): continue
        for path in root.rglob("*.json"):
            try:
                if path.stat().st_size > 8 * 1024 * 1024:
                    continue
                collect(json.loads(path.read_text(encoding="utf-8")), refs)
            except (OSError,ValueError): continue
    rows=[]
    for path in sorted(a.objects.rglob("*")) if a.objects.is_dir() else []:
        if not path.is_file(): continue
        digest=path.name
        if not DIGEST.fullmatch(digest): state,reason="UNKNOWN","object name is not a SHA-256 digest"
        elif digest in refs: state,reason="LIVE_REQUIRED","referenced by machine-readable authority"
        else: state,reason="ARCHIVE_CANDIDATE","positively unreachable from supplied authority roots"
        rows.append({"path":str(path),"sha256":digest,"size":path.stat().st_size,"state":state,"reason":reason})
    deleted=[]; quarantined=[]; before=os.statvfs(a.objects).f_bavail*os.statvfs(a.objects).f_frsize
    lock_path=Path("/run/gentoo-optimization/project.lock"); lock=None
    if a.execute:
        lock_path.parent.mkdir(parents=True,exist_ok=True); lock=lock_path.open("a+"); fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        run_quarantine=a.quarantine_root / f"attempt-{os.getpid()}-{time.time_ns()}"
        run_quarantine.mkdir(parents=True,exist_ok=False)
        try:
            for row in rows:
                if row["state"] != "ARCHIVE_CANDIDATE": continue
                src=Path(row["path"]); dst=run_quarantine/src.name
                os.replace(src,dst); quarantined.append((src,dst)); deleted.append(str(src))
            for _src, dst in quarantined:
                dst.unlink()
            run_quarantine.rmdir()
        except BaseException:
            for src, dst in reversed(quarantined):
                if dst.exists() and not src.exists(): os.replace(dst,src)
            if run_quarantine.exists(): run_quarantine.rmdir()
            raise
    after=os.statvfs(a.objects).f_bavail*os.statvfs(a.objects).f_frsize
    payload={"schema":"gentoo-optimization-content-object-reachability-v1","objects":rows,"referenced_sha256_count":len(refs),"unknown_bytes":sum(r["size"] for r in rows if r["state"]=="UNKNOWN"),"archive_candidate_bytes":sum(r["size"] for r in rows if r["state"]=="ARCHIVE_CANDIDATE"),"mode":"execute" if a.execute else "dry-run","deleted":deleted,"filesystem_free_bytes_before":before,"filesystem_free_bytes_after":after,"filesystem_free_delta":after-before}
    a.output.parent.mkdir(parents=True,exist_ok=True); tmp=a.output.with_suffix(a.output.suffix+".tmp"); tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8"); os.replace(tmp,a.output)
    if lock: lock.close()
    print(json.dumps({k:payload[k] for k in ("schema","mode","referenced_sha256_count","unknown_bytes","archive_candidate_bytes","filesystem_free_delta")},indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())

#!/usr/bin/env python3
"""Retire inactive Portage build trees after extracting durable logs."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess, time
from pathlib import Path

def active() -> bool:
    return subprocess.run(["pgrep", "-x", "emerge"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0 or subprocess.run(["pgrep", "-x", "ebuild"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def durable(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); t=path.with_suffix(path.suffix+'.tmp')
    with t.open('w') as f: json.dump(obj,f,indent=2,sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(t,path); fd=os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)); os.fsync(fd); os.close(fd)

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--reports',type=Path,required=True); ap.add_argument('--receipt',type=Path,required=True); ap.add_argument('--execute',action='store_true'); a=ap.parse_args()
    root=a.root.resolve()
    if not root.is_dir(): raise SystemExit('REFUSED: build root is not a directory')
    if active(): raise SystemExit('REFUSED: emerge/ebuild is active')
    logs=[]
    for p in sorted(root.rglob('build.log')):
        if p.is_file(): logs.append({'source':str(p),'size':p.stat().st_size,'sha256':digest(p)})
    moved=[]
    if a.execute:
        for row in logs:
            src=Path(row['source']); rel=src.relative_to(root); dst=a.reports / 'portage-build-logs' / rel
            dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst); row['durable']=str(dst); row['durable_sha256']=digest(dst)
        shutil.rmtree(root)
        moved=[x['source'] for x in logs]
    out={'schema':'gentoo-optimization-portage-build-retirement-v1','timestamp':int(time.time()),'mode':'execute' if a.execute else 'dry-run','root':str(root),'logs':logs,'retired_root':a.execute,'moved_logs':len(moved)}
    durable(a.receipt,out); print(json.dumps({'schema':out['schema'],'mode':out['mode'],'logs':len(logs),'moved_logs':len(moved)})); return 0
if __name__=='__main__': raise SystemExit(main())

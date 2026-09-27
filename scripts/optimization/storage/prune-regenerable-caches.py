#!/usr/bin/env python3
"""Bound regenerable compiler caches without touching project evidence."""
from __future__ import annotations
import argparse, json, os, subprocess, time
from pathlib import Path

def size(p: Path) -> int:
    return sum(x.stat().st_size for x in p.rglob('*') if x.is_file()) if p.exists() else 0

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, action='append', required=True)
    ap.add_argument('--max-bytes', type=int, required=True)
    ap.add_argument('--receipt', type=Path, required=True)
    ap.add_argument('--execute', action='store_true')
    a = ap.parse_args()
    if subprocess.run(['pgrep','-x','emerge'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0 or subprocess.run(['pgrep','-x','ebuild'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit('REFUSED: active Portage build')
    rows=[]
    for root in a.root:
        before=size(root); removed=0; files=0
        if a.execute and before > a.max_bytes:
            for f in sorted((x for x in root.rglob('*') if x.is_file()), key=lambda x: x.stat().st_mtime):
                if before-removed <= a.max_bytes: break
                n=f.stat().st_size
                f.unlink(); removed += n; files += 1
        after=size(root)
        rows.append({'root':str(root),'before_bytes':before,'removed_bytes':removed,'removed_files':files,'after_bytes':after,'max_bytes':a.max_bytes})
    rec={'schema':'gentoo-optimization-regenerable-cache-prune-v1','executed':a.execute,'created_at':int(time.time()),'rows':rows}
    a.receipt.parent.mkdir(parents=True, exist_ok=True); a.receipt.write_text(json.dumps(rec,sort_keys=True,indent=2)+'\n')
    print(json.dumps(rec,sort_keys=True))
    return 0
if __name__ == '__main__': raise SystemExit(main())

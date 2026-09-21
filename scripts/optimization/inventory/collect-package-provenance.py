#!/usr/bin/env python3
"""Bind installed VDB provenance separately from next-build provenance."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from portage.versions import catpkgsplit
import portage

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def text(root, name):
    p=root/name
    return p.read_text(errors='replace').strip() if p.is_file() else None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--manifest',type=Path,required=True); ap.add_argument('--vdb',type=Path,default=Path('/var/db/pkg')); ap.add_argument('--ebuild-root',type=Path,default=Path('/var/db/repos')); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    if a.output.exists(): raise SystemExit('REFUSED: provenance output already exists')
    manifest=json.loads(a.manifest.read_text()); rows=[]
    for item in manifest.get('packages',[]):
        cpv=item['cpv']; cat,pf=cpv.split('/',1); root=a.vdb/cat/pf
        if not root.is_dir(): raise SystemExit(f'REFUSED: installed VDB record missing: {cpv}')
        repo=text(root,'REPOSITORY') or text(root,'repository')
        split=catpkgsplit(pf); pn=(split[1] if split and split[0] != 'null' else pf.rsplit('-',1)[0])
        ebuild_name=item.get('ebuild') or pf+'.ebuild'
        next_path=a.ebuild_root/repo/cat/pn/ebuild_name if repo else None
        if repo and (next_path is None or not next_path.is_file()):
            try:
                found=portage.create_trees()['/']['porttree'].dbapi.findname(cpv, myrepo=repo)
                if found: next_path=Path(found)
            except Exception:
                pass
        next_exists=bool(next_path and next_path.is_file())
        rows.append({'cpv':cpv,
          'installed_source':{'vdb_path':str(root),'repository':repo,'build_time':text(root,'BUILD_TIME'),'counter':text(root,'COUNTER'),'contents_sha256':sha(root/'CONTENTS') if (root/'CONTENTS').is_file() else None},
          'next_build_source':{'repository':repo,'ebuild_path':str(next_path) if next_path else None,'ebuild_sha256':sha(next_path) if next_exists else None,'available':next_exists}})
    out={'record_type':'package-provenance','schema_version':1,'source_manifest_sha256':sha(a.manifest),'records':sorted(rows,key=lambda x:x['cpv'])}
    out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps({'packages':len(rows),'next_build_available':sum(x['next_build_source']['available'] for x in rows),'next_build_unavailable':sum(not x['next_build_source']['available'] for x in rows)}))
if __name__=='__main__': main()

#!/usr/bin/env python3
"""Archive profile-use receipt inputs into a content-addressed object tree."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil
from pathlib import Path

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()
def canon(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--receipt',type=Path,required=True); ap.add_argument('--archive-root',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    if a.output.exists(): raise SystemExit('REFUSED: archival record already exists')
    receipt=json.loads(a.receipt.read_text())
    if receipt.get('schema_version') != 2 or receipt.get('mode') != 'profile-use' or receipt.get('exit_status') != 0:
        raise SystemExit('REFUSED: receipt is not a successful profile-use v2 record')
    source_sha=sha(a.receipt); objects=[]; seen=set()
    items=[]
    for key in ('ebuild','dispatcher','dispatcher_env','manifest','metadata','profile','log'):
        item=receipt.get(key)
        if isinstance(item,dict): items.append((key,item))
    post=receipt.get('post_vdb',{})
    for key in ('contents','environment'):
        item=post.get(key)
        if isinstance(item,dict): items.append(('post_vdb.'+key,item))
    for label,item in items:
        path=Path(item.get('path','')); expected=item.get('sha256')
        if not path.is_file() or not isinstance(expected,str) or sha(path)!=expected:
            raise SystemExit(f'REFUSED: {label} source artifact hash mismatch')
        if expected in seen: continue
        seen.add(expected); dest=a.archive_root/'objects'/'sha256'/expected[:2]/expected
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():
            if sha(dest)!=expected: raise SystemExit(f'REFUSED: archive object collision: {expected}')
        else:
            temporary=dest.with_name('.'+dest.name+'.tmp')
            if temporary.exists(): raise SystemExit('REFUSED: stale archival temporary exists')
            shutil.copyfile(path,temporary)
            os.replace(temporary,dest)
            if sha(dest)!=expected: raise SystemExit(f'REFUSED: archived object verification failed: {expected}')
        objects.append({'label':label,'sha256':expected,'archive_path':str(dest.resolve()),'size':dest.stat().st_size})
    out={'record_type':'profile-use-archival-v1','schema_version':1,'receipt_path':str(a.receipt.resolve()),'receipt_sha256':source_sha,'cpv':receipt['cpv'],'repository':receipt['repository'],'objects':objects}
    out['sha256']=hashlib.sha256(canon(out)).hexdigest(); a.output.parent.mkdir(parents=True,exist_ok=True)
    fd=a.output.open('x',encoding='utf-8');
    with fd: fd.write(json.dumps(out,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'objects':len(objects),'receipt_sha256':source_sha,'sha256':out['sha256']}))
if __name__=='__main__': main()

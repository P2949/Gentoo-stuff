#!/usr/bin/env python3
"""Fail-closed exact-CPV profile-use transaction runner."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, subprocess, sys, time

def sha(path: pathlib.Path) -> str:
    h=hashlib.sha256();
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--dispatcher',type=pathlib.Path,required=True)
    ap.add_argument('--cpv',required=True); ap.add_argument('--repository',required=True)
    ap.add_argument('--receipt',type=pathlib.Path,required=True); ap.add_argument('--log',type=pathlib.Path,required=True)
    a=ap.parse_args()
    if '/' not in a.cpv: raise SystemExit('REFUSED: malformed CPV')
    record=json.loads(a.dispatcher.read_text())
    if record.get('cpv') != a.cpv: raise SystemExit('REFUSED: dispatcher CPV differs from requested exact atom')
    metadata=pathlib.Path(record['metadata']); profile=metadata.parent / pathlib.Path(record['profile']).name
    payload=json.loads(metadata.read_text()); ident=payload.get('profile',{})
    if ident.get('cpv') != a.cpv: raise SystemExit('REFUSED: metadata CPV differs from requested exact atom')
    cat,pf=a.cpv.split('/',1); vdb=pathlib.Path('/var/db/pkg')/cat/pf
    if not vdb.is_dir(): raise SystemExit(f'REFUSED: exact CPV is not installed in VDB: {a.cpv}')
    repo=(vdb/'REPOSITORY').read_text().strip() if (vdb/'REPOSITORY').is_file() else (vdb/'repository').read_text().strip()
    if repo != a.repository: raise SystemExit(f'REFUSED: repository mismatch: live={repo}, expected={a.repository}')
    try:
        import portage
        porttree=portage.create_trees()['/']['porttree'].dbapi
        live_repo=porttree.aux_get(a.cpv, ['repository'])[0]
        ebuild=pathlib.Path(porttree.findname(a.cpv, myrepo=live_repo))
    except Exception as exc:
        raise SystemExit(f'REFUSED: cannot resolve exact Portage ebuild identity: {exc}')
    if live_repo != a.repository: raise SystemExit(f'REFUSED: Portage repository mismatch: {live_repo}')
    if not ebuild.is_file(): raise SystemExit('REFUSED: exact Portage ebuild is unavailable')
    digest=sha(ebuild)
    expected=ident.get('ebuild_sha256')
    if expected and digest != expected: raise SystemExit('REFUSED: ebuild SHA-256 differs from profile metadata')
    atom=f'={a.cpv}::{a.repository}'
    started=time.time()
    a.log.parent.mkdir(parents=True,exist_ok=True)
    with a.log.open('w') as out:
        proc=subprocess.run(['emerge','--oneshot','--buildpkg',atom],stdout=out,stderr=subprocess.STDOUT,env={**os.environ,'LLVM_PROFILE_FILE':'/dev/null'})
    post=(vdb/'BUILD_TIME').read_text().strip() if (vdb/'BUILD_TIME').is_file() else ''
    receipt={'schema_version':1,'cpv':a.cpv,'repository':repo,'ebuild_sha256':digest,'dispatcher_sha256':sha(a.dispatcher),'metadata_sha256':sha(metadata),'profile_sha256':sha(profile),'exit_status':proc.returncode,'log_path':str(a.log.resolve()),'log_sha256':sha(a.log),'started_epoch':started,'finished_epoch':time.time(),'post_build_time':post}
    a.receipt.parent.mkdir(parents=True,exist_ok=True); a.receipt.write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    return proc.returncode
if __name__=='__main__': raise SystemExit(main())

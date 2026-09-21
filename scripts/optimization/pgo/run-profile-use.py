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
    if a.receipt.exists() or a.log.exists():
        raise SystemExit('REFUSED: terminal profile-use evidence already exists')
    if '/' not in a.cpv: raise SystemExit('REFUSED: malformed CPV')
    record=json.loads(a.dispatcher.read_text())
    if record.get('cpv') != a.cpv: raise SystemExit('REFUSED: dispatcher CPV differs from requested exact atom')
    metadata=pathlib.Path(record['metadata'])
    payload=json.loads(metadata.read_text()); ident=payload.get('profile',{})
    profile=pathlib.Path(str(ident.get('path') or record['profile']))
    if ident.get('cpv') != a.cpv: raise SystemExit('REFUSED: metadata CPV differs from requested exact atom')
    if ident.get('repository') != a.repository:
        raise SystemExit('REFUSED: metadata repository differs from requested exact atom')
    if not isinstance(ident.get('ebuild_sha256'), str) or len(ident['ebuild_sha256']) != 64:
        raise SystemExit('REFUSED: profile metadata lacks exact ebuild identity')
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
    if digest != expected: raise SystemExit('REFUSED: ebuild SHA-256 differs from profile metadata')
    atom=f'={a.cpv}::{a.repository}'
    started=time.time()
    a.log.parent.mkdir(parents=True,exist_ok=True)
    dispatcher_env = a.dispatcher.with_suffix('.env')
    if not dispatcher_env.is_file() or dispatcher_env.is_symlink():
        raise SystemExit(f'REFUSED: exact dispatcher environment is unavailable: {dispatcher_env}')
    run_env={**os.environ,'LLVM_PROFILE_FILE':'/dev/null','GENTOO_OPT_RUNNER_DISPATCHER_ENV':str(dispatcher_env.resolve())}
    with a.log.open('w') as out:
        pretend=subprocess.run(['emerge','--oneshot','--pretend','--verbose',atom],stdout=out,stderr=subprocess.STDOUT,env={**os.environ,'LLVM_PROFILE_FILE':'/dev/null'})
        if pretend.returncode != 0:
            raise SystemExit('REFUSED: exact Portage pretend did not resolve the requested atom')
        out.flush()
        proc=subprocess.run(['emerge','--oneshot','--buildpkg',atom],stdout=out,stderr=subprocess.STDOUT,env=run_env)
    post=(vdb/'BUILD_TIME').read_text().strip() if (vdb/'BUILD_TIME').is_file() else ''
    finished=time.time()
    receipt={'schema_version':2,'cpv':a.cpv,'repository':repo,'ebuild':{'path':str(ebuild.resolve()),'sha256':digest},'dispatcher':{'path':str(a.dispatcher.resolve()),'sha256':sha(a.dispatcher)},'dispatcher_env':{'path':str(dispatcher_env.resolve()),'sha256':sha(dispatcher_env)},'metadata':{'path':str(metadata.resolve()),'sha256':sha(metadata)},'profile':{'path':str(profile.resolve()),'sha256':sha(profile)},'generation':record.get('generation'),'framework':record.get('framework'),'fingerprint':ident.get('fingerprint'),'compiler':record.get('compiler'),'backend':record.get('backend'),'mode':'profile-use','exit_status':proc.returncode,'log':{'path':str(a.log.resolve()),'sha256':sha(a.log)},'started_epoch':started,'finished_epoch':finished,'post_vdb':{'path':str(vdb.resolve()),'build_time':post}}
    a.receipt.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(receipt,sort_keys=True,indent=2)+'\n'
    fd=os.open(a.receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
    with os.fdopen(fd,'w') as out: out.write(payload)
    return proc.returncode
if __name__=='__main__': raise SystemExit(main())

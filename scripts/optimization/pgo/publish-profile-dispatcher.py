#!/usr/bin/env python3
"""Publish an immutable, exact-CPV Clang profile-use dispatcher fragment."""
from __future__ import annotations
import argparse, hashlib, json, os, re, tempfile, grp, stat
from pathlib import Path

HEX = re.compile(r'^[0-9a-f]{64}$')
CPV = re.compile(r'^[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+-[0-9]')

def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def safe(path: Path, root: Path, label: str):
    if not path.is_absolute() or path.is_symlink(): raise SystemExit(f'REFUSED: unsafe {label}')
    try: path.relative_to(root)
    except ValueError: raise SystemExit(f'REFUSED: {label} escapes trusted root')
    if not path.is_file(): raise SystemExit(f'REFUSED: missing {label}: {path}')

def write_new(path: Path, data: bytes):
    if path.exists(): raise SystemExit(f'REFUSED: output already exists: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        d=os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--metadata', type=Path, required=True)
    ap.add_argument('--fingerprint-file', type=Path, required=True)
    ap.add_argument('--cpv', required=True)
    ap.add_argument('--backend', choices=('clang-ir','rust'), default='clang-ir')
    ap.add_argument('--output-env', type=Path, required=True)
    ap.add_argument('--output-record', type=Path, required=True)
    a=ap.parse_args()
    cache=Path('/var/cache/gentoo-optimization/pgo').resolve()
    generation=Path('/var/lib/gentoo-optimization/generations').resolve()
    for p,root,label in ((a.manifest,cache,'manifest'),(a.metadata,cache,'metadata'),(a.fingerprint_file,generation,'fingerprint')): safe(p.resolve(),root,label)
    if a.metadata != Path(str(a.manifest)+'.metadata.json'): raise SystemExit('REFUSED: metadata is not the manifest sidecar')
    if not CPV.match(a.cpv) or '/' not in a.cpv: raise SystemExit('REFUSED: malformed CPV')
    lines={}
    for line in a.manifest.read_text().splitlines():
        if '=' not in line: raise SystemExit('REFUSED: malformed manifest')
        k,v=line.split('=',1)
        if k in lines: raise SystemExit('REFUSED: duplicate manifest key')
        lines[k]=v
    required={'schema','backend','fingerprint','abi','compiler_family','profile_path','profile_sha256','validation_status'}
    if set(lines)!=required or lines['schema']!='gentoo-optimization-profile-v1' or lines['backend']!=a.backend or lines['validation_status']!='passed': raise SystemExit('REFUSED: unsupported manifest')
    if not HEX.fullmatch(lines['fingerprint']) or not HEX.fullmatch(lines['profile_sha256']): raise SystemExit('REFUSED: malformed manifest identity')
    profile=Path(lines['profile_path']).resolve(); safe(profile,cache if a.backend == 'clang-ir' else generation,'profile')
    if hashlib.sha256(profile.read_bytes()).hexdigest()!=lines['profile_sha256']: raise SystemExit('REFUSED: profile digest mismatch')
    if os.geteuid() != 0: raise SystemExit('REFUSED: publication requires root-owned cache publication')
    try: portage_gid = grp.getgrnam('portage').gr_gid
    except KeyError: raise SystemExit('REFUSED: portage group is unavailable')
    for item in (profile, a.manifest, a.metadata):
        st=item.stat()
        if st.st_uid != 0: raise SystemExit(f'REFUSED: cache input is not root-owned: {item}')
        os.chown(item, 0, portage_gid); os.chmod(item, stat.S_IMODE(st.st_mode) | stat.S_IRGRP)
    os.chown(profile.parent, 0, portage_gid); os.chmod(profile.parent, stat.S_IMODE(profile.parent.stat().st_mode) | stat.S_IXGRP)
    fp=a.fingerprint_file.read_text().strip()
    if fp != f"fingerprint={lines['fingerprint']}": raise SystemExit('REFUSED: fingerprint mismatch')
    meta=json.loads(a.metadata.read_text())
    if not isinstance(meta,dict) or meta.get('schema_version') != 1: raise SystemExit('REFUSED: invalid validation metadata')
    if a.backend in ('clang-ir','rust'):
        evidence = meta.get('merge_evidence')
        if not isinstance(evidence, dict) or not isinstance(evidence.get('path'), str) or not HEX.fullmatch(str(evidence.get('sha256',''))):
            raise SystemExit('REFUSED: indexed profile merge evidence is missing')
        evidence_path = Path(evidence['path']).resolve()
        safe(evidence_path, generation, 'merge evidence')
        if hashlib.sha256(evidence_path.read_bytes()).hexdigest() != evidence['sha256']:
            raise SystemExit('REFUSED: merge evidence digest mismatch')
    env='\n'.join([
      f'GENTOO_OPT_MODE="{a.backend}-use"', 'GENTOO_OPT_ABI="amd64"', f'GENTOO_OPT_COMPILER_FAMILY="{"clang" if a.backend == "clang-ir" else "rustc"}"',
      f'GENTOO_OPT_FINGERPRINT_FILE="{a.fingerprint_file}"', f'GENTOO_OPT_PROFILE_PATH="{profile}"',
      f'GENTOO_OPT_PROFILE_MANIFEST="{a.manifest.resolve()}"', f'GENTOO_OPT_PROFILE_METADATA="{a.metadata.resolve()}"', '' ])
    rec={'schema_version':1,'cpv':a.cpv,'backend':a.backend,'fingerprint':lines['fingerprint'],'profile':str(profile),'manifest':str(a.manifest.resolve()),'metadata':str(a.metadata.resolve()),'fingerprint_file':str(a.fingerprint_file.resolve()),'state':'candidate-profile-use','sha256':''}
    rec['sha256']=digest({k:v for k,v in rec.items() if k!='sha256'})
    write_new(a.output_env,env.encode()); write_new(a.output_record,(json.dumps(rec,sort_keys=True,indent=2)+'\n').encode())
    print(json.dumps({'cpv':a.cpv,'record_sha256':rec['sha256'],'env':str(a.output_env)}))
if __name__=='__main__': main()

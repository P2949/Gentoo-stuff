#!/usr/bin/env python3
"""Publish an immutable, exact-CPV Clang profile-use dispatcher fragment."""
from __future__ import annotations
import argparse, hashlib, json, os, re, tempfile
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
    ap.add_argument('--output-env', type=Path, required=True)
    ap.add_argument('--output-record', type=Path, required=True)
    a=ap.parse_args()
    cache=Path('/var/cache/gentoo-optimization/pgo').resolve()
    generation=Path('/var/lib/gentoo-optimization/generations').resolve()
    for p,root,label in ((a.manifest,cache,'manifest'),(a.metadata,cache,'metadata'),(a.fingerprint_file,generation,'fingerprint')): safe(p.resolve(),root,label)
    if a.metadata != Path(str(a.manifest)+'.metadata.json'): raise SystemExit('REFUSED: metadata is not the manifest sidecar')
    if not CPV.fullmatch(a.cpv): raise SystemExit('REFUSED: malformed CPV')
    lines={}
    for line in a.manifest.read_text().splitlines():
        if '=' not in line: raise SystemExit('REFUSED: malformed manifest')
        k,v=line.split('=',1)
        if k in lines: raise SystemExit('REFUSED: duplicate manifest key')
        lines[k]=v
    required={'schema','backend','fingerprint','abi','compiler_family','profile_path','profile_sha256','validation_status'}
    if set(lines)!=required or lines['schema']!='gentoo-optimization-profile-v1' or lines['backend']!='clang-ir' or lines['validation_status']!='passed': raise SystemExit('REFUSED: unsupported manifest')
    if not HEX.fullmatch(lines['fingerprint']) or not HEX.fullmatch(lines['profile_sha256']): raise SystemExit('REFUSED: malformed manifest identity')
    profile=Path(lines['profile_path']).resolve(); safe(profile,cache,'profile')
    if hashlib.sha256(profile.read_bytes()).hexdigest()!=lines['profile_sha256']: raise SystemExit('REFUSED: profile digest mismatch')
    fp=a.fingerprint_file.read_text().strip()
    if fp != f"fingerprint={lines['fingerprint']}": raise SystemExit('REFUSED: fingerprint mismatch')
    meta=json.loads(a.metadata.read_text())
    if not isinstance(meta,dict) or meta.get('schema_version') != 1: raise SystemExit('REFUSED: invalid validation metadata')
    env='\n'.join([
      'GENTOO_OPT_MODE="clang-ir-use"', 'GENTOO_OPT_ABI="amd64"', 'GENTOO_OPT_COMPILER_FAMILY="clang"',
      f'GENTOO_OPT_FINGERPRINT_FILE="{a.fingerprint_file}"', f'GENTOO_OPT_PROFILE_PATH="{profile}"',
      f'GENTOO_OPT_PROFILE_MANIFEST="{a.manifest.resolve()}"', f'GENTOO_OPT_PROFILE_METADATA="{a.metadata.resolve()}"', '' ])
    rec={'schema_version':1,'cpv':a.cpv,'backend':'clang-ir','fingerprint':lines['fingerprint'],'profile':str(profile),'manifest':str(a.manifest.resolve()),'metadata':str(a.metadata.resolve()),'fingerprint_file':str(a.fingerprint_file.resolve()),'state':'candidate-profile-use','sha256':''}
    rec['sha256']=digest({k:v for k,v in rec.items() if k!='sha256'})
    write_new(a.output_env,env.encode()); write_new(a.output_record,(json.dumps(rec,sort_keys=True,indent=2)+'\n').encode())
    print(json.dumps({'cpv':a.cpv,'record_sha256':rec['sha256'],'env':str(a.output_env)}))
if __name__=='__main__': main()

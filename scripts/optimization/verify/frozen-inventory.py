#!/usr/bin/env python3
"""Fail-closed semantic verifier for a Phase-3 frozen inventory."""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from pathlib import Path

SAFE_ID = re.compile(r'^[A-Za-z0-9+_.:@-]+$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
CPV = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9+_.-]*/[A-Za-z0-9_][A-Za-z0-9+_-]*-[0-9]+(?:\.[0-9]+)*[a-z]?(?:_(?:alpha|beta|pre|rc|p)[0-9]*)*(?:-r[0-9]+)?$')

def fail(msg: str) -> None:
    raise ValueError(msg)

def canonical_path(v: object) -> bool:
    return isinstance(v, str) and v.startswith('/') and v != '/' and not re.search(r'[\x00-\x1f]', v) and '//' not in v and not re.search(r'/(?:\.{1,2})(?:/|$)', v) and not v.endswith('/')

def evidence(v: object) -> None:
    if not isinstance(v, dict) or list(v) != ['kind','path','sha256'] or not canonical_path(v.get('path')) or not SHA256.fullmatch(v.get('sha256','')) or v['kind'] not in {'binary','binpkg','command-output','config','log','manifest','profile','report','sidecar','source','transaction','other'}: fail('invalid terminal evidence')

def directory_resolution(v: object) -> None:
    if not isinstance(v, dict) or list(v) != ['evidence','reason_code','registry_version','reviewed_at','reviewed_by'] or v.get('registry_version') != '1' or v.get('reason_code') != 'not-machine-code' or not isinstance(v.get('reviewed_by'),str) or not v['reviewed_by'] or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',v.get('reviewed_at','')) or not isinstance(v.get('evidence'),list) or not v['evidence']: fail('invalid directory resolution')
    for e in v['evidence']: evidence(e)
    keys=[(e['path'],e['sha256'],e['kind']) for e in v['evidence']]
    if keys != sorted(keys) or len(keys) != len(set(keys)): fail('directory evidence not sorted and unique')

def verify(path: Path) -> dict:
    raw=path.read_bytes(); digest=hashlib.sha256(raw).hexdigest(); d=json.loads(raw)
    if list(d) != ['generation_id','inventory_id','owned_directories','owned_paths','packages','record_type','schema_version'] or d.get('schema_version') != 2 or d.get('record_type') != 'frozen-inventory' or not SAFE_ID.fullmatch(d.get('generation_id','')) or not SAFE_ID.fullmatch(d.get('inventory_id','')): fail('invalid top-level schema')
    packages=d.get('packages');
    if not isinstance(packages,list) or not packages: fail('packages must be nonempty')
    cpvs=[]
    for p in packages:
        if list(p) != ['cpv','entry_sha256'] or not CPV.fullmatch(p.get('cpv','')) or not SHA256.fullmatch(p.get('entry_sha256','')): fail('invalid package record')
        cpvs.append(p['cpv'])
    if cpvs != sorted(cpvs) or len(cpvs)!=len(set(cpvs)): fail('packages not sorted and unique')
    owners=set(cpvs)
    paths=d.get('owned_paths'); dirs=d.get('owned_directories')
    if not isinstance(paths,list) or not isinstance(dirs,list): fail('path arrays missing')
    path_keys=[]; path_set=set()
    for e in paths:
        if list(e) != ['owner_cpv','path'] or e['owner_cpv'] not in owners or not canonical_path(e['path']): fail('invalid owned path')
        k=(e['owner_cpv'],e['path']); path_keys.append(k); path_set.add(e['path'])
    dir_keys=[]
    for e in dirs:
        if list(e) != ['classification','gid','mode','owner_cpv','path','resolution','uid'] or e['owner_cpv'] not in owners or not canonical_path(e['path']) or e['classification'] != 'not-applicable' or not isinstance(e['mode'],int) or e['mode']<0 or e['mode']>4095 or not isinstance(e['uid'],int) or e['uid']<0 or not isinstance(e['gid'],int) or e['gid']<0: fail('invalid owned directory')
        directory_resolution(e['resolution']); dir_keys.append((e['owner_cpv'],e['path']))
    if path_keys != sorted(path_keys) or len(path_keys)!=len(set(path_keys)) or dir_keys != sorted(dir_keys) or len(dir_keys)!=len(set(dir_keys)): fail('ownership records not sorted and unique')
    if any(e['path'] in path_set for e in dirs): fail('file and directory namespaces overlap')
    return {'cpvs':cpvs,'generation_id':d['generation_id'],'inventory_id':d['inventory_id'],'inventory_sha256':digest,'owned_path_count':len(paths),'owned_directory_count':len(dirs),'package_count':len(packages)}

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('inventory', type=Path); a=ap.parse_args()
    try: print(json.dumps(verify(a.inventory), separators=(',',':'))); return 0
    except (OSError,ValueError, json.JSONDecodeError) as e: print(f'ERROR: {e}', file=sys.stderr); return 1
if __name__ == '__main__': raise SystemExit(main())

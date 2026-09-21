#!/usr/bin/env python3
"""Emit immutable profile-carry-forward-v1 decisions.

The source record is never modified.  Carry-forward is allowed only when the
complete profile-relevant identity is byte-for-byte equal between generations.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

REQUIRED = ('source_cpv','target_cpv','repository','ebuild_sha256',
            'package_env','package_env_content','build_controls','compiler','abi','target_triple','optimization_flags',
            'workload_revision','training_receipt','merge_evidence',
            'profile_sha256')

def canon(v):
    return json.dumps(v, sort_keys=True, separators=(',', ':')).encode()

def digest(v):
    return hashlib.sha256(canon(v)).hexdigest()

def load(path):
    obj=json.loads(Path(path).read_text())
    if not isinstance(obj, dict): raise SystemExit(f'REFUSED: {path} is not an object')
    return obj

def identity(record):
    # Include every field that can change generated code or training validity.
    keys=('source_cpv','target_cpv','repository','ebuild_sha256','package_env',
          'package_env_content','build_controls','compiler','abi','target_triple',
          'optimization_flags','workload_revision','training_receipt',
          'merge_evidence','profile_sha256')
    return {k: record.get(k) for k in keys}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--source-generation', required=True)
    ap.add_argument('--target-generation', required=True)
    ap.add_argument('--source-record', type=Path, required=True)
    ap.add_argument('--target-record', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a=ap.parse_args()
    src=load(a.source_record); dst=load(a.target_record)
    if a.output.exists():
        raise SystemExit('REFUSED: carry-forward output already exists')
    for label, rec in (('source',src),('target',dst)):
        missing=[k for k in REQUIRED if k not in rec]
        if missing: raise SystemExit(f'REFUSED: {label} record missing {", ".join(missing)}')
    src_i=identity(src); dst_i=identity(dst)
    # A CPV or repository substitution is material even if all other fields
    # happen to match.  Equality is intentionally strict and deterministic.
    carry = src_i == dst_i
    reason = 'all-profile-relevant-identities-unchanged' if carry else 'profile-relevant-identity-changed'
    out={
      'record_type':'profile-carry-forward-v1', 'schema_version':1,
      'source_generation':a.source_generation, 'target_generation':a.target_generation,
      'source_record_sha256':hashlib.sha256(a.source_record.read_bytes()).hexdigest(),
      'target_record_sha256':hashlib.sha256(a.target_record.read_bytes()).hexdigest(),
      'source_cpv':src['source_cpv'], 'target_cpv':dst['target_cpv'],
      'repository':dst['repository'], 'ebuild_sha256':dst['ebuild_sha256'],
      'package_env':dst.get('package_env'), 'package_env_content':dst.get('package_env_content'),
      'build_controls':dst.get('build_controls'), 'compiler':dst['compiler'],
      'abi':dst['abi'], 'target_triple':dst['target_triple'],
      'optimization_flags':dst['optimization_flags'], 'workload_revision':dst['workload_revision'],
      'training_receipt':dst['training_receipt'], 'merge_evidence':dst['merge_evidence'],
      'profile_sha256':dst['profile_sha256'], 'decision':'carry-forward' if carry else 'retrain',
      'reason':reason, 'source_identity_sha256':digest(src_i), 'target_identity_sha256':digest(dst_i),
    }
    out['sha256']=hashlib.sha256(canon(out)).hexdigest()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    fd=a.output.open('x', encoding='utf-8')
    with fd: fd.write(json.dumps(out,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'decision':out['decision'],'source_cpv':out['source_cpv'],'target_cpv':out['target_cpv']}))
if __name__=='__main__': main()

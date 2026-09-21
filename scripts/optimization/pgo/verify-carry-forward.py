#!/usr/bin/env python3
"""Independently verify a profile-carry-forward-v1 decision."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def canon(v): return json.dumps(v, sort_keys=True, separators=(',', ':')).encode()
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p):
    v=json.loads(Path(p).read_text())
    if not isinstance(v, dict): raise SystemExit('REFUSED: record is not an object')
    return v
def ident(r):
    return {k:r.get(k) for k in ('source_cpv','target_cpv','repository','ebuild_sha256','package_env','package_env_content','build_controls','compiler','abi','target_triple','optimization_flags','workload_revision','training_receipt','merge_evidence','profile_sha256')}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--carry-forward',type=Path,required=True); ap.add_argument('--source-record',type=Path,required=True); ap.add_argument('--target-record',type=Path,required=True); a=ap.parse_args()
    out=load(a.carry_forward); src=load(a.source_record); dst=load(a.target_record)
    required={'record_type','schema_version','source_generation','target_generation','source_record_sha256','target_record_sha256','source_cpv','target_cpv','repository','ebuild_sha256','compiler','abi','target_triple','optimization_flags','workload_revision','training_receipt','merge_evidence','profile_sha256','decision','reason','source_identity_sha256','target_identity_sha256','sha256'}
    if set(out) != required or out['record_type']!='profile-carry-forward-v1' or out['schema_version'] != 1: raise SystemExit('REFUSED: invalid carry-forward schema')
    unsigned=dict(out); declared=unsigned.pop('sha256');
    if hashlib.sha256(canon(unsigned)).hexdigest()!=declared: raise SystemExit('REFUSED: carry-forward digest mismatch')
    if sha(a.source_record)!=out['source_record_sha256'] or sha(a.target_record)!=out['target_record_sha256']: raise SystemExit('REFUSED: source/target record hash mismatch')
    si,ti=ident(src),ident(dst)
    if hashlib.sha256(canon(si)).hexdigest()!=out['source_identity_sha256'] or hashlib.sha256(canon(ti)).hexdigest()!=out['target_identity_sha256']: raise SystemExit('REFUSED: identity digest mismatch')
    expected='carry-forward' if si==ti else 'retrain'
    if out['decision']!=expected: raise SystemExit('REFUSED: carry-forward decision is inconsistent with identities')
    if out['source_cpv']!=src.get('source_cpv') or out['target_cpv']!=dst.get('target_cpv') or out['repository']!=dst.get('repository') or out['ebuild_sha256']!=dst.get('ebuild_sha256'): raise SystemExit('REFUSED: published identity does not match target/source records')
    print(f"PASS: carry-forward record independently verified ({out['decision']})")
if __name__=='__main__': main()

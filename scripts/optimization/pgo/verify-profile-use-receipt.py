#!/usr/bin/env python3
"""Independently verify an exact profile-use transaction receipt."""
from __future__ import annotations
import argparse, hashlib, json, pathlib, sys

def digest(path: pathlib.Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--receipt',type=pathlib.Path,required=True); a=ap.parse_args()
    r=json.loads(a.receipt.read_text())
    required={'schema_version','cpv','repository','ebuild_sha256','dispatcher_sha256','metadata_sha256','profile_sha256','exit_status','log_path','log_sha256','started_epoch','finished_epoch','post_build_time'}
    if set(r) != required or r['schema_version'] != 1: raise SystemExit('REFUSED: invalid receipt schema')
    if not isinstance(r['cpv'],str) or '/' not in r['cpv'] or not isinstance(r['repository'],str): raise SystemExit('REFUSED: invalid receipt package identity')
    for key in ('ebuild_sha256','dispatcher_sha256','metadata_sha256','profile_sha256','log_sha256'):
        value=r[key]
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value): raise SystemExit(f'REFUSED: invalid {key}')
    if r['exit_status'] != 0: raise SystemExit('REFUSED: transaction did not succeed')
    log=pathlib.Path(r['log_path'])
    if not log.is_absolute() or not log.is_file() or digest(log) != r['log_sha256']: raise SystemExit('REFUSED: transaction log hash mismatch')
    if r['finished_epoch'] < r['started_epoch']: raise SystemExit('REFUSED: invalid receipt timing')
    print('PASS: profile-use receipt identity and success status verified')
    return 0
if __name__=='__main__': raise SystemExit(main())

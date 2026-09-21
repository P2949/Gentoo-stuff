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
    if r.get('schema_version') == 2:
        required={'schema_version','cpv','repository','ebuild','dispatcher','dispatcher_env','manifest','metadata','profile','generation','framework','fingerprint','compiler','backend','mode','exit_status','log','started_epoch','finished_epoch','post_vdb'}
        if set(r) != required: raise SystemExit('REFUSED: invalid receipt schema')
        if r['mode'] != 'profile-use' or r['exit_status'] != 0: raise SystemExit('REFUSED: transaction did not succeed')
        for key in ('ebuild','dispatcher','dispatcher_env','manifest','metadata','profile','log'):
            item=r[key]
            p=pathlib.Path(item['path'])
            if not p.is_absolute() or not p.is_file() or digest(p) != item['sha256']:
                raise SystemExit(f'REFUSED: {key} artifact hash mismatch')
        post=r['post_vdb']
        if not isinstance(post,dict) or post.get('cpv') != r['cpv'] or post.get('repository') != r['repository']:
            raise SystemExit('REFUSED: post-merge VDB identity does not match receipt package')
        if post.get('ebuild_sha256') != r['ebuild']['sha256']:
            raise SystemExit('REFUSED: post-merge ebuild identity differs')
        for name in ('contents','environment'):
            item=post.get(name)
            if item is not None:
                p=pathlib.Path(item.get('path',''))
                if not p.is_file() or digest(p) != item.get('sha256'):
                    raise SystemExit(f'REFUSED: post-merge {name} hash mismatch')
        log_text=pathlib.Path(r['log']['path']).read_text(errors='replace')
        backend=r['backend']
        markers={
            'clang-ir': ('-fprofile-use', 'clang-ir-use'),
            'clang-sample': ('-fprofile-sample-use', 'clang-sample-use'),
            'rust': ('-Cprofile-use', 'rust-use'),
            'gcc': ('-fprofile-use', 'gcc-use'),
            'go': ('-pgo', 'go-use'),
        }.get(backend)
        if not markers or not any(marker in log_text for marker in markers):
            raise SystemExit('REFUSED: build log does not prove backend-specific profile use')
        if any(flag in log_text for flag in ('-fprofile-generate', '-fprofile-instr-generate', '-Cprofile-generate')):
            raise SystemExit('REFUSED: generation-mode profile flag appeared in use transaction')
        if not r['generation'] or not r['framework'] or not r['fingerprint']:
            raise SystemExit('REFUSED: incomplete generation/framework/fingerprint identity')
        if r['finished_epoch'] < r['started_epoch']: raise SystemExit('REFUSED: invalid receipt timing')
        print('PASS: profile-use v2 receipt independently verified')
        return 0
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

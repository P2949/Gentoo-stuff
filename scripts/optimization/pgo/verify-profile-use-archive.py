#!/usr/bin/env python3
"""Independently verify a content-addressed profile-use archival manifest."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def canon(v): return json.dumps(v, sort_keys=True, separators=(',', ':')).encode()
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--archive',type=Path,required=True); ap.add_argument('--receipt',type=Path,required=True); a=ap.parse_args()
    doc=json.loads(a.archive.read_text()); receipt=json.loads(a.receipt.read_text())
    required={'record_type','schema_version','receipt_path','receipt_sha256','cpv','repository','objects','sha256'}
    if set(doc)!=required or doc.get('record_type')!='profile-use-archival-v1' or doc.get('schema_version')!=1:
        raise SystemExit('REFUSED: invalid profile-use archival schema')
    unsigned=dict(doc); declared=unsigned.pop('sha256')
    if hashlib.sha256(canon(unsigned)).hexdigest()!=declared: raise SystemExit('REFUSED: archival manifest digest mismatch')
    if not a.receipt.is_file() or sha(a.receipt)!=doc['receipt_sha256']: raise SystemExit('REFUSED: receipt digest mismatch')
    if doc['receipt_path'] != str(a.receipt.resolve()) or doc['cpv'] != receipt.get('cpv') or doc['repository'] != receipt.get('repository'):
        raise SystemExit('REFUSED: archival receipt identity mismatch')
    if not isinstance(doc['objects'],list) or not doc['objects']: raise SystemExit('REFUSED: archival object list is empty')
    seen=set()
    for item in doc['objects']:
        if not isinstance(item,dict) or set(item)!={'label','sha256','archive_path','size'} or item['sha256'] in seen:
            raise SystemExit('REFUSED: malformed or duplicate archival object')
        seen.add(item['sha256']); path=Path(item['archive_path'])
        if not path.is_file() or path.stat().st_size != item['size'] or sha(path) != item['sha256']:
            raise SystemExit(f"REFUSED: archival object mismatch: {item.get('label')}")
    print(f"PASS: profile-use archival manifest independently verified ({len(seen)} objects)")
if __name__=='__main__': main()

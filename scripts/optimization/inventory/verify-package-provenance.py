#!/usr/bin/env python3
"""Verify immutable installed/next-build provenance coverage."""
import argparse,hashlib,json
from pathlib import Path
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--provenance',type=Path,required=True);ap.add_argument('--manifest',type=Path,required=True);a=ap.parse_args(); p=json.loads(a.provenance.read_text())
 if p.get('record_type')!='package-provenance' or p.get('schema_version')!=1: raise SystemExit('REFUSED: unsupported provenance schema')
 u=dict(p); declared=u.pop('sha256',None)
 if hashlib.sha256(json.dumps(u,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=declared: raise SystemExit('REFUSED: provenance digest mismatch')
 if p.get('source_manifest_sha256')!=sha(a.manifest): raise SystemExit('REFUSED: provenance manifest binding mismatch')
 expected=sorted(x['cpv'] for x in json.loads(a.manifest.read_text()).get('packages',[])); actual=[x.get('cpv') for x in p.get('records',[])]
 if actual!=expected or actual!=sorted(set(actual)): raise SystemExit('REFUSED: provenance coverage mismatch')
 for row in p['records']:
  if not row.get('installed_source',{}).get('vdb_path') or not row.get('next_build_source',{}).get('repository'): raise SystemExit('REFUSED: incomplete provenance identity')
 print(f'PASS: installed/next-build provenance covers {len(actual)} CPVs')
if __name__=='__main__': main()

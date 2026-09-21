#!/usr/bin/env python3
"""Verify immutable installed/next-build provenance coverage."""
import argparse,hashlib,json
from pathlib import Path
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--provenance',type=Path,required=True);ap.add_argument('--manifest',type=Path,required=True);ap.add_argument('--vdb',type=Path,default=Path('/var/db/pkg'));ap.add_argument('--ebuild-root',type=Path,default=Path('/var/db/repos'));a=ap.parse_args(); p=json.loads(a.provenance.read_text())
 if p.get('record_type')!='package-provenance' or p.get('schema_version')!=1: raise SystemExit('REFUSED: unsupported provenance schema')
 u=dict(p); declared=u.pop('sha256',None)
 if hashlib.sha256(json.dumps(u,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=declared: raise SystemExit('REFUSED: provenance digest mismatch')
 if p.get('source_manifest_sha256')!=sha(a.manifest): raise SystemExit('REFUSED: provenance manifest binding mismatch')
 expected=sorted(x['cpv'] for x in json.loads(a.manifest.read_text()).get('packages',[])); actual=[x.get('cpv') for x in p.get('records',[])]
 if actual!=expected or actual!=sorted(set(actual)): raise SystemExit('REFUSED: provenance coverage mismatch')
 for row in p['records']:
  cpv=row['cpv']; cat,pf=cpv.split('/',1); vdb=a.vdb/cat/pf
  installed=row.get('installed_source',{}); next_source=row.get('next_build_source',{})
  if Path(installed.get('vdb_path','')) != vdb or not vdb.is_dir(): raise SystemExit(f'REFUSED: installed VDB identity mismatch: {cpv}')
  if not installed.get('repository'): raise SystemExit(f'REFUSED: installed repository missing: {cpv}')
  for name in ('BUILD_TIME','COUNTER','CONTENTS'):
   observed=vdb/name
   recorded=installed.get({'BUILD_TIME':'build_time','COUNTER':'counter','CONTENTS':'contents_sha256'}[name])
   if name == 'CONTENTS':
    if not observed.is_file() or recorded != sha(observed): raise SystemExit(f'REFUSED: installed CONTENTS identity mismatch: {cpv}')
   elif observed.is_file() and observed.read_text(errors='replace').strip() != (recorded or ''): raise SystemExit(f'REFUSED: installed {name} identity mismatch: {cpv}')
  if next_source.get('repository') != installed.get('repository'): raise SystemExit(f'REFUSED: repository provenance mismatch: {cpv}')
  ebuild=next_source.get('ebuild_path'); available=next_source.get('available')
  if available:
   if not ebuild or not Path(ebuild).is_file(): raise SystemExit(f'REFUSED: next-build ebuild unavailable: {cpv}')
   if not str(Path(ebuild)).startswith(str(a.ebuild_root.resolve()) + '/'):
    raise SystemExit(f'REFUSED: next-build ebuild escapes repository root: {cpv}')
   if next_source.get('ebuild_sha256') != sha(ebuild): raise SystemExit(f'REFUSED: next-build ebuild hash mismatch: {cpv}')
  elif next_source.get('ebuild_sha256') is not None: raise SystemExit(f'REFUSED: unavailable source carries hash: {cpv}')
 print(f'PASS: installed/next-build provenance covers {len(actual)} CPVs')
if __name__=='__main__': main()

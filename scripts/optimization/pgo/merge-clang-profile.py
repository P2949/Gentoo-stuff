#!/usr/bin/env python3
"""Receipt-driven indexed LLVM profile merge and immutable publication."""
import argparse, hashlib, json, os, pathlib, subprocess, tempfile

def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--backend',choices=('clang-ir','rust'),default='clang-ir'); ap.add_argument('--receipt',required=True); ap.add_argument('--package',required=True); ap.add_argument('--raw-root',required=True); ap.add_argument('--llvm-profdata',required=True); ap.add_argument('--output',required=True); ap.add_argument('--evidence',required=True); ap.add_argument('--generation-id',required=True); ap.add_argument('--inventory-id',required=True); ap.add_argument('--inventory-sha256',required=True); a=ap.parse_args()
 receipt=json.load(open(a.receipt)); expected={'generation_id':a.generation_id,'inventory_id':a.inventory_id,'inventory_sha256':a.inventory_sha256}
 if receipt.get('state')!='completed' or receipt.get('record_type')!='profile-wave-transaction-receipt': raise SystemExit('REFUSED: receipt is not a completed profile wave')
 lane = receipt.get('lane') or receipt.get('backend')
 expected_lane = {'clang-ir': 'pgo-clang-ir', 'rust': 'pgo-rust'}[a.backend]
 # Older wave receipts bind the lane in the authenticated readiness record,
 # while newer receipts may repeat it at the top level.  If present, enforce
 # the repeated value; never invent a backend from an absent legacy field.
 if lane is not None and lane != expected_lane: raise SystemExit('REFUSED: receipt backend lane does not match requested backend')
 if a.package not in receipt.get('packages',[]) or receipt.get('generation')!=expected: raise SystemExit('REFUSED: receipt package or generation authority does not match request')
 root=pathlib.Path(a.raw_root).resolve(); listed=[]
 for item in receipt.get('profile_payloads',[]):
  if item.get('cpv') != a.package: continue
  p=pathlib.Path(item.get('path',''))
  try: p.relative_to(root)
  except ValueError: raise SystemExit('REFUSED: receipt payload escapes raw root')
  if not p.is_file() or p.stat().st_size==0 or sha(p)!=item.get('sha256'): raise SystemExit('REFUSED: receipt payload is missing or digest-mismatched')
  listed.append(p)
 if not listed: raise SystemExit('REFUSED: receipt contains no payload for package')
 # The raw root is shared by the whole generation.  Compare only the exact
 # package spool represented by this receipt; unrelated completed waves must
 # not invalidate an otherwise complete package receipt.
 package_roots={p.parent.resolve() for p in listed}
 actual=sorted(p for package_root in package_roots for p in package_root.rglob('*.profraw') if p.is_file() and p.stat().st_size>0)
 if sorted(listed)!=actual: raise SystemExit('REFUSED: raw profile directory contains unreceipted or missing payloads')
 out=pathlib.Path(a.output)
 if out.exists(): raise SystemExit('REFUSED: refusing to overwrite existing merged profile')
 out.parent.mkdir(parents=True,exist_ok=True)
 fd,tmpout=tempfile.mkstemp(prefix='.profdata-',dir=str(out.parent)); os.close(fd); os.unlink(tmpout)
 try:
  # Keep each execve argument vector bounded.  Large instrumented builds can
  # emit thousands of raw payloads, and passing every path in one invocation
  # fails before llvm-profdata starts with E2BIG.  Merge deterministic chunks,
  # then merge the chunk profiles into the exact final output.
  chunk_size = 256
  chunk_outputs = []
  for index in range(0, len(listed), chunk_size):
   chunk_fd, chunk_path = tempfile.mkstemp(prefix='.profchunk-', dir=str(out.parent))
   os.close(chunk_fd)
   os.unlink(chunk_path)
   chunk_outputs.append(chunk_path)
   subprocess.run(
    [a.llvm_profdata, 'merge', '-sparse',
     *(str(p) for p in listed[index:index + chunk_size]), '-o', chunk_path],
    check=True,
   )
  subprocess.run(
   [a.llvm_profdata, 'merge', '-sparse', *chunk_outputs, '-o', tmpout],
   check=True,
  )
  for chunk_path in chunk_outputs:
   try:
    os.unlink(chunk_path)
   except FileNotFoundError:
    pass
  shown=subprocess.run([a.llvm_profdata,'show','--counts','--all-functions',tmpout],check=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  if not shown.stdout.strip(): raise SystemExit('REFUSED: llvm-profdata produced no readable profile description')
  os.replace(tmpout,out)
 except Exception:
  try: os.unlink(tmpout)
  except FileNotFoundError: pass
  raise
 evidence={'record_type':a.backend+'-profile-merge','schema_version':2,'backend':a.backend,'receipt':str(pathlib.Path(a.receipt).resolve()),'receipt_sha256':sha(pathlib.Path(a.receipt)),'package':a.package,'generation':expected,'raw_files':[{'path':str(p),'size':p.stat().st_size,'sha256':sha(p)} for p in listed],'llvm_profdata':str(pathlib.Path(a.llvm_profdata).resolve()),'merged_profile':str(out.resolve()),'merged_sha256':sha(out),'inspection_sha256':hashlib.sha256(shown.stdout.encode()).hexdigest(),'state':'profile-merged-pending-dispatcher-authorization'}
 evidence['sha256']=hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode()).hexdigest(); fd,tmp=tempfile.mkstemp(prefix='.merge-',dir=str(pathlib.Path(a.evidence).parent))
 with os.fdopen(fd,'w') as f: json.dump(evidence,f,sort_keys=True,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
 os.replace(tmp,a.evidence); print(evidence['sha256'])
if __name__=='__main__': main()

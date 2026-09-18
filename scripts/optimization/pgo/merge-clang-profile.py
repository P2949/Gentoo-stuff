#!/usr/bin/env python3
"""Merge and independently inspect one exact Clang IR raw profile pool."""
import argparse, hashlib, json, pathlib, subprocess

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--raw-root',required=True); ap.add_argument('--llvm-profdata',required=True); ap.add_argument('--output',required=True); ap.add_argument('--evidence',required=True); a=ap.parse_args()
    raw=pathlib.Path(a.raw_root); files=sorted(p for p in raw.rglob('*.profraw') if p.is_file() and p.stat().st_size>0)
    if not files: raise SystemExit('REFUSED: raw profile pool contains no nonempty .profraw files')
    out=pathlib.Path(a.output); out.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run([a.llvm_profdata,'merge','-sparse',*(str(p) for p in files),'-o',str(out)],check=True)
    shown=subprocess.run([a.llvm_profdata,'show','--counts','--all-functions',str(out)],check=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if not shown.stdout.strip(): raise SystemExit('REFUSED: llvm-profdata produced no readable profile description')
    data=shown.stdout
    evidence={'record_type':'clang-ir-profile-merge','schema_version':1,'raw_files':[{'path':str(p),'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],'llvm_profdata':str(pathlib.Path(a.llvm_profdata).resolve()),'merged_profile':str(out.resolve()),'merged_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'inspection_sha256':hashlib.sha256(data.encode()).hexdigest(),'state':'profile-merged-pending-dispatcher-authorization'}
    evidence['sha256']=hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode()).hexdigest(); json.dump(evidence,open(a.evidence,'w'),sort_keys=True,indent=2); open(a.evidence,'a').write('\n'); print(evidence['sha256'])
if __name__=='__main__': main()

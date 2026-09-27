#!/usr/bin/env python3
"""Independently verify consumer candidate paths and provider ownership."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path
def canon(v): return json.dumps(v,sort_keys=True,separators=(",",":")).encode()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--candidates',required=True,type=Path); ap.add_argument('--elf',required=True,type=Path); ap.add_argument('--output',required=True,type=Path); a=ap.parse_args()
 c=json.loads(a.candidates.read_text()); e=json.loads(a.elf.read_text()); by={(x.get('owner_cpv'),x.get('path')):x for x in e.get('artifacts',[])}; verified=[]; rejected=[]
 for row in c.get('records',[]):
  for work in row.get('consumer_workloads',[]):
   exe=work.get('executable'); providers=work.get('expected_provider_artifacts',[]); reasons=[]
   if not exe or not os.path.isfile(exe) or os.path.islink(exe): reasons.append('consumer-executable-unavailable')
   if not providers: reasons.append('provider-artifact-missing')
   for provider in providers:
    if (row['cpv'],provider) not in by: reasons.append('provider-not-owned-by-provider-cpv')
   item={'provider_cpv':row['cpv'],'consumer_cpv':work.get('consumer_cpv'),'executable':exe,'expected_provider_artifacts':providers,'counter_proof':work.get('counter_proof')}
   if reasons: rejected.append({**item,'reasons':sorted(set(reasons))})
   else: verified.append(item)
 out={'record_type':'verified-consumer-workload-candidates','schema_version':1,'source_candidates_sha256':hashlib.sha256(a.candidates.read_bytes()).hexdigest(),'source_elf_sha256':hashlib.sha256(a.elf.read_bytes()).hexdigest(),'verified':verified,'rejected':rejected,'counts':{'verified':len(verified),'rejected':len(rejected)}}; out['sha256']=hashlib.sha256(canon(out)).hexdigest(); a.output.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps(out['counts']))
if __name__=='__main__': main()

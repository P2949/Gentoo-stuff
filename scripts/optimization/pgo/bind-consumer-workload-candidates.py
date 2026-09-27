#!/usr/bin/env python3
"""Bind deterministic consumer executable candidates without claiming training."""
from __future__ import annotations
import argparse, collections, hashlib, json
from pathlib import Path

def canon(v): return json.dumps(v,sort_keys=True,separators=(",",":")).encode()

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--consumer-plan',required=True,type=Path); ap.add_argument('--elf',required=True,type=Path); ap.add_argument('--output',required=True,type=Path); a=ap.parse_args()
 plan=json.loads(a.consumer_plan.read_text()); elf=json.loads(a.elf.read_text())
 executables=collections.defaultdict(list)
 for item in elf.get('artifacts',[]):
  if item.get('type') in {'EXEC (Executable file)','DYN (Position-Independent Executable file)'} and item.get('path','').startswith(('/bin/','/sbin/','/usr/bin/','/usr/sbin/')):
   executables[item.get('owner_cpv')].append(item)
 rows=[]
 for row in plan.get('records',[]):
  out=dict(row); candidates=[]
  for consumer in row.get('consumer_cpvs',[]):
   choices=sorted(executables.get(consumer,[]),key=lambda x:x.get('path',''))
   if not choices: continue
   exe=choices[0]
   for provider_path in row.get('elf_paths',[]):
    candidates.append({'consumer_cpv':consumer,'relationship':'consumer-candidate','executable':exe['path'],'recipe':[exe['path'],'--help'],'expected_provider_artifacts':[provider_path],'counter_proof':'pending-runtime-proof','state':'consumer-workload-candidate'})
  out['consumer_workloads']=candidates
  if candidates and out.get('state')=='needs-consumer-workload': out['state']='consumer-workload-candidate'
  rows.append(out)
 out={'record_type':'consumer-workload-candidates','schema_version':1,'source_consumer_plan_sha256':hashlib.sha256(a.consumer_plan.read_bytes()).hexdigest(),'source_elf_sha256':hashlib.sha256(a.elf.read_bytes()).hexdigest(),'records':rows}
 out['counts']=collections.Counter(x.get('state') for x in rows); out['counts']=dict(out['counts']); out['sha256']=hashlib.sha256(canon(out)).hexdigest(); a.output.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps({'records':len(rows),'candidate_workloads':sum(len(x.get('consumer_workloads',[])) for x in rows),'states':out['counts']}))
if __name__=='__main__': main()

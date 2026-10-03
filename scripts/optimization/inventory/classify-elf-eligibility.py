#!/usr/bin/env python3
"""Assign an explicit preliminary PGO/BOLT state to every ELF record."""
import argparse,json,hashlib,collections,os
from pathlib import Path
def scope_row(cpv, rows):
 for row in rows:
  selector=row.get('selector','')
  if cpv == selector or cpv.startswith(selector + '-'):
   return row
 return None
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--metadata',required=True);ap.add_argument('--output',required=True);ap.add_argument('--mutation-policy', help='generation-bound package mutation policy authority');ap.add_argument('--scope-policy');a=ap.parse_args()
 if os.path.exists(a.output): raise SystemExit('REFUSED: ELF eligibility output already exists')
 d=json.load(open(a.metadata)); identities=[(x.get('owner_cpv'),x.get('path')) for x in d['artifacts']]
 if len(set(identities)) != len(identities): raise SystemExit('REFUSED: duplicate ELF eligibility identity')
 policy={}
 if a.mutation_policy:
  p=json.load(open(a.mutation_policy))
  if p.get('record_type') != 'package-mutation-policy': raise SystemExit('REFUSED: unsupported mutation-policy schema')
  policy={row['cpv']: row for row in p.get('records', [])}
  if len(policy) != len(p.get('records', [])): raise SystemExit('REFUSED: duplicate mutation-policy CPV')
 rows=[]
 scope=[]
 if a.scope_policy:
  doc=json.load(open(a.scope_policy))
  if doc.get('schema') != 'optimization-scope-policy-v1': raise SystemExit('REFUSED: unsupported scope policy schema')
  scope=doc.get('scope', [])
 for x in d['artifacts']:
  owner=x.get('owner_cpv','')
  sr=scope_row(owner, scope)
  if sr and sr.get('bolt') is False:
   rows.append({'owner_cpv':owner,'path':x['path'],'state':'scope-excluded','reason_code':sr.get('reason_code')})
   continue
  # Category names do not establish lifecycle policy.  Kernel/firmware
  # exclusions must be supplied by the authoritative transaction-policy
  # classifier; ordinary userspace ELF owned by those categories remains
  # visible to BOLT review.
  owner_policy=policy.get(owner)
  if a.mutation_policy and owner_policy is None:
   reason='missing-mutation-policy-owner'; state='pending-eligibility-review'
  elif owner_policy and owner_policy.get('decision') == 'pending-review':
   reason='pending-mutation-policy'; state='pending-eligibility-review'
  elif (owner_policy and owner_policy.get('decision') == 'kernel-policy-exclusion') or x.get('kernel_policy_exclusion') is True:
   reason='kernel-policy-exclusion'; state='not-applicable'
  elif x.get('error'): reason='metadata-tool-failure'; state='pending-eligibility-review'
  elif x['class']!='ELF64' or x.get('machine') != 'Advanced Micro Devices X86-64': reason='unsupported-architecture'; state='not-applicable'
  elif x['type']=='REL (Relocatable file)': reason='relocatable-object'; state='not-applicable'
  elif x['type'] not in ('DYN (Shared object file)','DYN (Position-Independent Executable file)','EXEC (Executable file)'): reason='unsupported-elf-type'; state='not-applicable'
  # A missing build ID is a rebuild prerequisite, not proof that the artifact
  # can never be captured.  Keep it in the candidate accounting until the
  # package-managed pre-strip rebuild supplies an exact identity.
  elif not x['build_id']: reason='missing-build-id'; state='rebuild-required-for-bolt-capture'
  else: reason='requires-section-and-safety-review'; state='candidate-bolt-eligible'
  rows.append({'owner_cpv':x['owner_cpv'],'path':x['path'],'state':state,'reason_code':reason})
 out={'record_type':'elf-eligibility-classification','schema_version':2,'source_sha256':d['sha256'],'source_scope_policy_sha256':hashlib.sha256(Path(a.scope_policy).read_bytes()).hexdigest() if a.scope_policy else None,'records':sorted(rows,key=lambda x:x['path'])}; out['counts']=dict(collections.Counter(x['state'] for x in rows)); out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest(); json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(json.dumps(out['counts'],sort_keys=True))
if __name__=='__main__':main()

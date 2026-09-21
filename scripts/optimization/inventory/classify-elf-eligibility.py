#!/usr/bin/env python3
"""Assign an explicit preliminary PGO/BOLT state to every ELF record."""
import argparse,json,hashlib,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--metadata',required=True);ap.add_argument('--output',required=True);a=ap.parse_args(); d=json.load(open(a.metadata)); rows=[]
 for x in d['artifacts']:
  owner=x.get('owner_cpv','')
  # Kernel and firmware artifacts are outside the userspace optimization
  # authority.  Classify them before metadata parsing so an unreadable
  # firmware blob cannot become a false pending userspace BOLT obligation.
  if owner.startswith(('sys-kernel/','sys-firmware/')):
   reason='kernel-policy-exclusion'; state='not-applicable'
  elif x.get('error'): reason='metadata-tool-failure'; state='pending-eligibility-review'
  elif x['class']!='ELF64' or x.get('machine') != 'Advanced Micro Devices X86-64': reason='unsupported-architecture'; state='not-applicable'
  elif x['type']=='REL (Relocatable file)': reason='relocatable-object'; state='not-applicable'
  elif x['type'] not in ('DYN (Shared object file)','DYN (Position-Independent Executable file)','EXEC (Executable file)'): reason='unsupported-elf-type'; state='not-applicable'
  elif not x['build_id']: reason='missing-build-id'; state='pending-eligibility-review'
  else: reason='requires-section-and-safety-review'; state='candidate-bolt-eligible'
  rows.append({'owner_cpv':x['owner_cpv'],'path':x['path'],'state':state,'reason_code':reason})
 out={'record_type':'elf-eligibility-classification','schema_version':1,'source_sha256':d['sha256'],'records':sorted(rows,key=lambda x:x['path'])}; out['counts']=dict(collections.Counter(x['state'] for x in rows)); out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest(); json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(json.dumps(out['counts'],sort_keys=True))
if __name__=='__main__':main()

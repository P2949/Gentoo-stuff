#!/usr/bin/env python3
import argparse,json,hashlib,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--workloads',required=True);ap.add_argument('--elf',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();w=json.load(open(a.workloads)); e=json.load(open(a.elf)); by=collections.defaultdict(list)
 for x in e['artifacts']:by[x['owner_cpv']].append(x)
 rows=[]
 for x in w['packages']:
  if x['state']!='no-runnable-entrypoint':continue
  vals=by[x['cpv']]; rows.append({'cpv':x['cpv'],'reason_code':'no-runnable-userspace-entrypoint','elf_records':len(vals),'elf64_records':sum(y['class']=='ELF64' for y in vals),'interpreter_records':sum(bool(y['interpreter']) for y in vals),'state':'workload-exclusion'})
 out={'record_type':'workload-exclusions','schema_version':1,'source_workloads':w['sha256'],'records':rows};out['counts']=dict(collections.Counter(x['reason_code'] for x in rows));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(len(rows),sum(x['elf_records'] for x in rows),sum(x['interpreter_records'] for x in rows))
if __name__=='__main__':main()

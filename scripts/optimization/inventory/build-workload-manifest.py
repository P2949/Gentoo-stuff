#!/usr/bin/env python3
import argparse,json,hashlib,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--lanes',required=True);ap.add_argument('--elf',required=True);ap.add_argument('--output',required=True);a=ap.parse_args(); lanes=json.load(open(a.lanes)); lane={x['cpv']:x['lane'] for x in lanes['packages'] if x['lane'].startswith('pgo-')}; by=collections.defaultdict(list)
 for x in json.load(open(a.elf))['artifacts']:
  if x['owner_cpv'] not in lane:continue
  if x['type'] in ('EXEC (Executable file)','DYN (Position-Independent Executable file)') and x['interpreter']:
   by[x['owner_cpv']].append({'path':x['path'],'type':x['type'],'build_id':x['build_id']})
 rows=[]
 for cpv in sorted(lane):
  entries=sorted(by.get(cpv,[]),key=lambda x:x['path'])[:8]
  rows.append({'cpv':cpv,'lane':lane[cpv],'entrypoints':entries,'state':'workload-candidate' if entries else 'no-runnable-entrypoint'})
 out={'record_type':'representative-workload-manifest','schema_version':1,'source_lanes':lanes['sha256'],'packages':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'])
if __name__=='__main__':main()

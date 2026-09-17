#!/usr/bin/env python3
import argparse,json,hashlib,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--census',required=True);ap.add_argument('--kernel-set',required=True);ap.add_argument('--output',required=True);ap.add_argument('--vdb',default='/var/db/pkg');a=ap.parse_args()
 m=json.load(open(a.manifest)); c=json.load(open(a.census)); owners={x['owner_cpv'] for x in c['artifacts'] if x['kind'] in ('regular','symlink') and x.get('elf')}; k=set(x.strip() for x in open(a.kernel_set) if x.strip()); atoms={}
 for cpv in m['cpvs']:
  cat,pf=cpv.split('/',1); p=a.vdb+'/'+cat+'/'+pf+'/P'; atoms[cpv]=cat+'/'+(open(p).read().strip() if __import__('os').path.isfile(p) else pf.rsplit('-',1)[0])
 rows=[]
 for cpv in m['cpvs']:
  if atoms[cpv] in k: state,reason='kernel-policy-exclusion','kernel-policy-exclusion'
  elif cpv not in owners: state,reason='not-applicable','no-owned-elf-artifact'
  else: state,reason='pending-pgo-classification','owned-elf-requires-backend-and-profile'
  rows.append({'cpv':cpv,'state':state,'reason_code':reason})
 out={'record_type':'package-optimization-state','schema_version':1,'source_manifest':m['manifest_sha256'],'records':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'])
if __name__=='__main__':main()

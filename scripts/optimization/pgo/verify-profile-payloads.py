#!/usr/bin/env python3
import argparse,json,hashlib,glob,os,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bindings',required=True);ap.add_argument('--output',required=True);ap.add_argument('--exclusions',help='authoritative workload-exclusion artifact');a=ap.parse_args();b=json.load(open(a.bindings)); exclusions={}
 if a.exclusions:
  e=json.load(open(a.exclusions)); exclusions={x['cpv']:x for x in e.get('records',[])}
 rows=[]
 for x in b['records']:
  if not x['compiler']:continue
  if x['cpv'] in exclusions:
   rows.append({'cpv':x['cpv'],'lane':x['lane'],'profile_path':x['profile_path'],'payloads':[],'state':'workload-exclusion','reason_code':exclusions[x['cpv']].get('reason_code')});continue
  paths=[]
  if x['lane']=='pgo-gcc':paths=glob.glob(x['profile_path']+'/**/*.gcda',recursive=True)
  elif x['lane']=='pgo-go':paths=glob.glob(x['profile_path']+'/**/default.pgo',recursive=True)
  else:paths=glob.glob(x['profile_path']+'/**/*.profdata',recursive=True)+glob.glob(x['profile_path']+'/**/*.profraw',recursive=True)
  rows.append({'cpv':x['cpv'],'lane':x['lane'],'profile_path':x['profile_path'],'payloads':sorted(paths),'state':'profile-present' if paths else 'missing-profile'})
 out={'record_type':'profile-payload-audit','schema_version':2,'source_bindings':b['sha256'],'source_exclusions':(e.get('sha256') if a.exclusions else None),'records':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['authorization_state']='pending-profile-collection' if any(x['state']=='missing-profile' for x in rows) else 'ready-for-profile-validation';out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'],out['authorization_state'])
if __name__=='__main__':main()

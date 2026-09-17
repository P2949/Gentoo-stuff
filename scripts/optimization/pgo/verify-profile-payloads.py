#!/usr/bin/env python3
import argparse,json,hashlib,glob,os,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bindings',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();b=json.load(open(a.bindings)); rows=[]
 for x in b['records']:
  if not x['compiler']:continue
  paths=[]
  if x['lane']=='pgo-gcc':paths=glob.glob(x['profile_path']+'/**/*.gcda',recursive=True)
  elif x['lane']=='pgo-go':paths=glob.glob(x['profile_path']+'/**/default.pgo',recursive=True)
  else:paths=glob.glob(x['profile_path']+'/**/*.profdata',recursive=True)+glob.glob(x['profile_path']+'/**/*.profraw',recursive=True)
  rows.append({'cpv':x['cpv'],'lane':x['lane'],'profile_path':x['profile_path'],'payloads':sorted(paths),'state':'profile-present' if paths else 'missing-profile'})
 out={'record_type':'profile-payload-audit','schema_version':1,'source_bindings':b['sha256'],'records':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['authorization_state']='pending-profile-collection' if any(x['state']=='missing-profile' for x in rows) else 'ready-for-profile-validation';out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'],out['authorization_state'])
if __name__=='__main__':main()

#!/usr/bin/env python3
import argparse,json,hashlib,os,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--wave',required=True);ap.add_argument('--manifest',required=True);ap.add_argument('--identity',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();w=json.load(open(a.wave));m=set(json.load(open(a.manifest))['cpvs']);i=json.load(open(a.identity));bad=[]; rows=[]
 for x in w['packages']:
  ok=x['cpv'] in m and x['compiler_sha256']==i[{'pgo-clang-ir':'clang','pgo-gcc':'gcc','pgo-rust':'rustc','pgo-go':'go'}[x['lane']]]['sha256'] and x['profile_path'].startswith('/var/tmp/gentoo-optimization/pgo-raw/')
  if not ok:bad.append(x['cpv'])
  rows.append({'cpv':x['cpv'],'input_valid':ok,'execution_state':'not-authorized-framework-gate'})
 out={'record_type':'profile-wave-readiness','schema_version':1,'source_wave':w['sha256'],'records':rows,'invalid_inputs':bad,'ready_count':sum(x['input_valid'] for x in rows),'authorization_state':'pending-framework-terminal-check'};out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['ready_count'],len(rows),len(bad))
if __name__=='__main__':main()

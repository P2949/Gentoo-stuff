#!/usr/bin/env python3
import argparse,json,hashlib,datetime,os
def digest(p):return hashlib.sha256(open(p,'rb').read()).hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--wave',required=True);ap.add_argument('--readiness',required=True);ap.add_argument('--bindings',required=True);ap.add_argument('--workloads',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if os.path.exists(a.output): raise SystemExit('REFUSED: wave receipt output already exists')
 w=json.load(open(a.wave));r=json.load(open(a.readiness)); packages=[x.get('cpv') for x in w.get('packages',[])]
 if len(packages) != len(set(packages)) or any(not isinstance(x,str) or not x for x in packages): raise SystemExit('REFUSED: duplicate or malformed wave package identity')
 out={'record_type':'profile-wave-transaction-receipt','schema_version':1,'wave_sha256':digest(a.wave),'readiness_sha256':digest(a.readiness),'bindings_sha256':digest(a.bindings),'workloads_sha256':digest(a.workloads),'package_count':len(packages),'packages':packages,'state':'not-run','authorization':'pending-framework-terminal-check','profile_payloads':[]};out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['sha256'])
if __name__=='__main__':main()

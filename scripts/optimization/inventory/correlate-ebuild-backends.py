#!/usr/bin/env python3
import argparse,json,os,re,hashlib,collections
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))
from cpv import catpkgsplit
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--vdb',default='/var/db/pkg');ap.add_argument('--repos',default='/var/db/repos');ap.add_argument('--output',required=True);a=ap.parse_args();m=json.load(open(a.manifest)); rows=[]
 cpvs=m.get('cpvs',[x['cpv'] for x in m.get('packages',[])])
 if os.path.exists(a.output): raise SystemExit('REFUSED: backend correlation output already exists')
 if len(cpvs) != len(set(cpvs)) or any(not isinstance(cpv,str) or '/' not in cpv for cpv in cpvs): raise SystemExit('REFUSED: duplicate or malformed CPV in backend correlation authority')
 for cpv in cpvs:
  cat,pf=cpv.split('/',1); root=a.vdb+'/'+cat+'/'+pf
  split=catpkgsplit(pf)
  pn=open(root+'/PN').read().strip() if os.path.isfile(root+'/PN') else (split[1] if split and split[0] != 'null' else pf)
  repo=open(root+'/REPOSITORY').read().strip() if os.path.isfile(root+'/REPOSITORY') else ''
  direct=a.repos+'/'+repo+'/'+cat+'/'+pn+'/'+pf+'.ebuild' if repo else ''
  installed=root+'/'+pf+'.ebuild'; ep=direct if direct and os.path.isfile(direct) else (installed if os.path.isfile(installed) else None); text=open(ep,errors='replace').read() if ep else ''
  inherited_path=root+'/INHERITED'
  inherits=open(inherited_path,errors='replace').read().split() if os.path.isfile(inherited_path) else []
  back=[]
  for token in inherits:
   if any(x in token for x in ('cmake','meson','autotools','cargo','rust','go','python','llvm','java','scons','waf')): back.append(token)
  rows.append({'cpv':cpv,'repository':repo,'ebuild':ep,'inherits':sorted(set(inherits)),'backend_evidence':sorted(set(back)),'phase_functions':sorted(set(re.findall(r'^(src_[a-z_]+|pkg_[a-z_]+)\s*\(\)',text,re.M))),'state':'correlated' if ep else 'missing-ebuild-source'})
 out={'record_type':'ebuild-backend-correlation','schema_version':1,'source_inventory':m.get('inventory_id'),'packages':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'])
if __name__=='__main__':main()

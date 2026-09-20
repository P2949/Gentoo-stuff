#!/usr/bin/env python3
import argparse,json,hashlib,os,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--wave',required=True);ap.add_argument('--manifest',required=True);ap.add_argument('--identity',required=True);ap.add_argument('--identity-root');ap.add_argument('--output',required=True);a=ap.parse_args();w=json.load(open(a.wave));md=json.load(open(a.manifest));m=set(md.get('cpvs',[x['cpv'] for x in md.get('packages',[])]));i=json.load(open(a.identity));identity_root=a.identity_root;bad=[]; rows=[]
 spool=os.path.realpath('/var/tmp/gentoo-optimization/pgo-raw'); wave_cpvs={x['cpv'] for x in w['packages']}
 for x in w['packages']:
  p=x['profile_path']; canonical=os.path.realpath(p) if isinstance(p,str) else ''
  safe=isinstance(p,str) and p.startswith(spool+'/') and canonical.startswith(spool+'/') and not any(os.path.islink(cur) for cur in [spool]+[os.path.join(spool,*p[len(spool):].strip('/').split('/')[:n]) for n in range(1,len(p[len(spool):].strip('/').split('/'))+1)])
  if identity_root:
   key=x['cpv'].replace('/','_')
   fingerprint=any(os.path.isfile(os.path.join(identity_root, candidate)) for candidate in (os.path.join(key,'fingerprint.env'), key+'.fingerprint.env'))
  else:
   fingerprint=True
  ok=x['cpv'] in m and x['compiler_sha256']==i[{'pgo-clang-ir':'clang','pgo-gcc':'gcc','pgo-rust':'rustc','pgo-go':'go'}[x['lane']]]['sha256'] and safe and fingerprint
  if not ok:bad.append(x['cpv'])
  rows.append({'cpv':x['cpv'],'input_valid':ok,'execution_state':'not-authorized-framework-gate'})
 out={'record_type':'profile-wave-readiness','schema_version':1,'source_wave':w['sha256'],'records':rows,'invalid_inputs':bad,'ready_count':sum(x['input_valid'] for x in rows),'authorization_state':'pending-framework-terminal-check'};out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['ready_count'],len(rows),len(bad))
if __name__=='__main__':main()

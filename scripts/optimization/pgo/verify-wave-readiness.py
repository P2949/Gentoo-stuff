#!/usr/bin/env python3
import argparse,json,hashlib,os,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--wave',required=True);ap.add_argument('--manifest',required=True);ap.add_argument('--identity',required=True);ap.add_argument('--identity-root');ap.add_argument('--output',required=True);ap.add_argument('--mode',choices=('training','exhaustive-generation'),default='training');a=ap.parse_args();w=json.load(open(a.wave));md=json.load(open(a.manifest));m=set(md.get('cpvs',[x['cpv'] for x in md.get('packages',[])]));i=json.load(open(a.identity));identity_root=a.identity_root;bad=[]; rows=[]
 spool=os.path.realpath('/var/tmp/gentoo-optimization/pgo-raw')
 # Bind the declared cache destination to the exact generation as well as
 # accepting the attempt-scoped raw spool used during execution.  The planner
 # publishes canonical cache paths, while the runner remaps them into the
 # root-owned raw spool; rejecting the former here made every training wave
 # fail readiness before it could start.  Both roots remain strict descendants
 # with symlink components rejected below.
 generation = w.get('generation_id') or w.get('generation') or ''
 cache_root = os.path.realpath('/var/cache/gentoo-optimization/pgo/' + generation) if generation else ''
 wave_cpvs={x['cpv'] for x in w['packages']}
 for x in w['packages']:
  p=x['profile_path']; canonical=os.path.realpath(p) if isinstance(p,str) else ''
  roots = [spool]
  if a.mode == 'training' and cache_root:
   roots.append(cache_root)
  safe=False
  if isinstance(p,str):
   for root in roots:
    if canonical.startswith(root + '/'):
     rel=p[len(root):].strip('/')
     components=rel.split('/') if rel else []
     if not any(os.path.islink(cur) for cur in [root]+[os.path.join(root,*components[:n]) for n in range(1,len(components)+1)]):
      safe=True
      break
  if identity_root:
   key=x['cpv'].replace('/','_')
   candidates=(os.path.join(identity_root,key,'fingerprint.env'), os.path.join(identity_root,key+'.fingerprint.env'))
   fingerprint=False
   for candidate in candidates:
    if os.path.isfile(candidate):
     values={line.rstrip('\n').split('=',1)[0]:line.rstrip('\n').split('=',1)[1] for line in open(candidate,encoding='utf-8') if '=' in line}
     fingerprint = values.get('fingerprint') == x.get('identity_sha256')
     break
  else:
   fingerprint=True
  ok=x['cpv'] in m and x['compiler_sha256']==i[{'pgo-clang-ir':'clang','pgo-gcc':'gcc','pgo-rust':'rustc','pgo-go':'go'}[x['lane']]]['sha256'] and (safe or a.mode == 'exhaustive-generation') and fingerprint
  if not ok:bad.append(x['cpv'])
  rows.append({'cpv':x['cpv'],'input_valid':ok,'execution_state':'not-authorized-framework-gate'})
 out={'record_type':'profile-wave-readiness','schema_version':1,'mode':a.mode,'source_wave':w['sha256'],'records':rows,'invalid_inputs':bad,'ready_count':sum(x['input_valid'] for x in rows),'authorization_state':'pending-framework-terminal-check'};out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['ready_count'],len(rows),len(bad))
if __name__=='__main__':main()

#!/usr/bin/env python3
import argparse,json,os,subprocess,sys,time,hashlib
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--wave',required=True);ap.add_argument('--readiness',required=True);ap.add_argument('--framework-generation',required=True);ap.add_argument('--framework-current',default='/var/lib/gentoo-optimization/framework-current');ap.add_argument('--identity-root');ap.add_argument('--receipt');ap.add_argument('--execute',action='store_true');a=ap.parse_args();w=json.load(open(a.wave));r=json.load(open(a.readiness));active=os.path.realpath(a.framework_current)
 if active!=a.framework_generation:raise SystemExit(f'REFUSED: active framework {active} != authorized generation {a.framework_generation}')
 if r.get('source_wave')!=w.get('sha256') or r.get('ready_count')!=len(w['packages']) or r.get('invalid_inputs'):raise SystemExit('REFUSED: wave readiness is incomplete or belongs to another wave')
 if not a.execute:print('READY: all technical gates pass; rerun with --execute to invoke the controlled transaction');return
 # Pin the transaction to the exact installed identities recorded by the
 # readiness manifest.  Portage requires the explicit =CPV atom form when a
 # revision-qualified CPV is supplied; bare CPVs are category/package names,
 # not valid transaction atoms.
 identity_root=a.identity_root or os.path.join(os.path.dirname(a.wave),'identity')
 payloads=[]
 for item in w['packages']:
  cpv=item['cpv']; key=cpv.replace('/','_')+'.fingerprint.env'; fingerprint_file=os.path.join(identity_root,key)
  if not os.path.isfile(fingerprint_file):
   raise SystemExit(f'REFUSED: missing reviewed fingerprint file for {cpv}: {fingerprint_file}')
  profile_path=item['profile_path']; spool=os.path.realpath('/var/tmp/gentoo-optimization/pgo-raw'); canonical=os.path.realpath(profile_path)
  if not isinstance(profile_path,str) or not profile_path.startswith(spool+'/') or not canonical.startswith(spool+'/'):
   raise SystemExit(f'REFUSED: profile path escapes trusted spool: {profile_path}')
  # A retry must never merge a failed transaction's partial gcda set with a
  # fresh native training run.  The runner owns this generation spool.
  subprocess.run(['doas','rm','-rf','--',profile_path],check=True)
  # The framework requires root-owned generation spools with a sticky,
  # writable leaf so the unprivileged Portage sandbox can emit profiles.
  subprocess.run(['doas','install','-d','-o','root','-g','root','-m','01777',profile_path],check=True)
  env=os.environ.copy();env['GENTOO_OPT_WAVE_ID']=w['sha256'];env.setdefault('GENTOO_OPT_ABI','amd64')
  subprocess.run(['doas','env','GENTOO_OPT_ABI='+env['GENTOO_OPT_ABI'],'GENTOO_OPT_WAVE_ID='+env['GENTOO_OPT_WAVE_ID'],'GENTOO_OPT_FINGERPRINT_FILE='+fingerprint_file,'GENTOO_OPT_PROFILE_PATH='+profile_path,'emerge','--oneshot','--buildpkg','='+cpv],env=env,check=True)
  # Run the exact reviewed representative recipes after the instrumented
  # package transaction.  This is the profile payload collection point; a
  # recipe failure is terminal for the wave and is recorded by the caller.
  for recipe in item.get('recipes',[]):
   path=recipe.get('path'); argv=recipe.get('argv')
   if recipe.get('safe_path') is not True or not isinstance(path,str) or not isinstance(argv,list) or not argv or argv[0] != path:
    raise SystemExit(f'REFUSED: unsafe workload recipe for {cpv}: {path}')
   run_env=env.copy(); run_env.update(recipe.get('environment',{})); start=time.monotonic()
   try:
    result=subprocess.run(argv,cwd=recipe.get('cwd','/'),env=run_env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30,check=False)
   except (OSError,subprocess.TimeoutExpired) as e:
    raise SystemExit(f'REFUSED: workload recipe failed for {cpv}: {path}: {e}')
   if result.returncode != 0:
    raise SystemExit(f'REFUSED: workload recipe exited {result.returncode} for {cpv}: {path}')
   if not result.stdout:
    raise SystemExit(f'REFUSED: workload recipe produced no output for {cpv}: {path}')
  for root,dirs,files in os.walk(profile_path):
   for name in files:
    path=os.path.join(root,name)
    if os.path.isfile(path):
     with open(path,'rb') as stream: payloads.append({'cpv':cpv,'path':path,'sha256':hashlib.sha256(stream.read()).hexdigest()})
 if a.receipt:
  receipt={'record_type':'profile-wave-transaction-receipt','schema_version':1,'wave_sha256':hashlib.sha256(open(a.wave,'rb').read()).hexdigest(),'readiness_sha256':hashlib.sha256(open(a.readiness,'rb').read()).hexdigest(),'package_count':len(w['packages']),'packages':[x['cpv'] for x in w['packages']],'state':'completed','authorization':'profile-payloads-collected','profile_payloads':sorted(payloads,key=lambda x:(x['cpv'],x['path']))}
  receipt['sha256']=hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  with open(a.receipt,'w') as stream: json.dump(receipt,stream,sort_keys=True,indent=2); stream.write('\n')
if __name__=='__main__':main()

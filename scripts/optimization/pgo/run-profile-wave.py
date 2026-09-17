#!/usr/bin/env python3
import argparse,json,os,subprocess,sys
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--wave',required=True);ap.add_argument('--readiness',required=True);ap.add_argument('--framework-generation',required=True);ap.add_argument('--framework-current',default='/var/lib/gentoo-optimization/framework-current');ap.add_argument('--identity-root');ap.add_argument('--execute',action='store_true');a=ap.parse_args();w=json.load(open(a.wave));r=json.load(open(a.readiness));active=os.path.realpath(a.framework_current)
 if active!=a.framework_generation:raise SystemExit(f'REFUSED: active framework {active} != authorized generation {a.framework_generation}')
 if r.get('source_wave')!=w.get('sha256') or r.get('ready_count')!=len(w['packages']) or r.get('invalid_inputs'):raise SystemExit('REFUSED: wave readiness is incomplete or belongs to another wave')
 if not a.execute:print('READY: all technical gates pass; rerun with --execute to invoke the controlled transaction');return
 # Pin the transaction to the exact installed identities recorded by the
 # readiness manifest.  Portage requires the explicit =CPV atom form when a
 # revision-qualified CPV is supplied; bare CPVs are category/package names,
 # not valid transaction atoms.
 identity_root=a.identity_root or os.path.join(os.path.dirname(a.wave),'identity')
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
if __name__=='__main__':main()

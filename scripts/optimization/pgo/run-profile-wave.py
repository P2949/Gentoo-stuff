#!/usr/bin/env python3
import argparse,json,os,subprocess,sys
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--wave',required=True);ap.add_argument('--readiness',required=True);ap.add_argument('--framework-generation',required=True);ap.add_argument('--framework-current',default='/var/lib/gentoo-optimization/framework-current');ap.add_argument('--identity-root');ap.add_argument('--execute',action='store_true');a=ap.parse_args();w=json.load(open(a.wave));r=json.load(open(a.readiness));active=os.path.realpath(a.framework_current)
 if active!=a.framework_generation:raise SystemExit(f'REFUSED: active framework {active} != authorized generation {a.framework_generation}')
 if r.get('ready_count')!=len(w['packages']) or r.get('invalid_inputs'):raise SystemExit('REFUSED: wave readiness is incomplete')
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
  env=os.environ.copy();env['GENTOO_OPT_WAVE_ID']=w['sha256'];env.setdefault('GENTOO_OPT_ABI','amd64')
  subprocess.run(['doas','env','GENTOO_OPT_ABI='+env['GENTOO_OPT_ABI'],'GENTOO_OPT_WAVE_ID='+env['GENTOO_OPT_WAVE_ID'],'GENTOO_OPT_FINGERPRINT_FILE='+fingerprint_file,'emerge','--oneshot','--buildpkg','='+cpv],env=env,check=True)
if __name__=='__main__':main()

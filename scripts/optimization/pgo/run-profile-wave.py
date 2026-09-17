#!/usr/bin/env python3
import argparse,json,os,subprocess,sys
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--wave',required=True);ap.add_argument('--readiness',required=True);ap.add_argument('--framework-generation',required=True);ap.add_argument('--framework-current',default='/var/lib/gentoo-optimization/framework-current');ap.add_argument('--execute',action='store_true');a=ap.parse_args();w=json.load(open(a.wave));r=json.load(open(a.readiness));active=os.path.realpath(a.framework_current)
 if active!=a.framework_generation:raise SystemExit(f'REFUSED: active framework {active} != authorized generation {a.framework_generation}')
 if r.get('ready_count')!=len(w['packages']) or r.get('invalid_inputs'):raise SystemExit('REFUSED: wave readiness is incomplete')
 if not a.execute:print('READY: all technical gates pass; rerun with --execute to invoke the controlled transaction');return
 # Pin the transaction to the exact installed identities recorded by the
 # readiness manifest.  Portage requires the explicit =CPV atom form when a
 # revision-qualified CPV is supplied; bare CPVs are category/package names,
 # not valid transaction atoms.
 atoms=['='+x['cpv'] for x in w['packages']]; env=os.environ.copy();env['GENTOO_OPT_WAVE_ID']=w['sha256']; subprocess.run(['doas','emerge','--oneshot','--buildpkg']+atoms,env=env,check=True)
if __name__=='__main__':main()

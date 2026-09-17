#!/usr/bin/env python3
import argparse,json,hashlib,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--states',required=True);ap.add_argument('--backends',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();s=json.load(open(a.states));b={x['cpv']:x for x in json.load(open(a.backends))['packages']}; rows=[]
 for x in s['records']:
  cpv=x['cpv']; ev=b[cpv]['backend_evidence']
  if x['state']!='pending-pgo-classification': lane=x['state']; reason=x['reason_code']
  elif any('cargo' in z or z in ('rust','rust-toolchain') for z in ev): lane='pgo-rust';reason='cargo-or-rust-eclass'
  elif any('go' in z for z in ev): lane='pgo-go';reason='go-eclass'
  elif any(z in ev for z in ('cmake','meson','autotools','llvm.org','llvm-r1','llvm-r2','toolchain-funcs','libtool','ecm','frameworks.kde.org','xorg-3','multilib','multilib-build')): lane='pgo-clang-ir';reason='native-compiled-eclass'
  elif any('python' in z or 'java' in z or z in ('distutils-r1','pypi','ruby-fakegem','perl-module') for z in ev): lane='unsupported-by-upstream-toolchain';reason='managed-language-or-runtime-eclass'
  else: lane='pending-pgo-classification';reason='no-supported-backend-evidence'
  rows.append({'cpv':cpv,'lane':lane,'reason_code':reason,'backend_evidence':ev})
 out={'record_type':'pgo-lane-candidates','schema_version':1,'source_state':s['sha256'],'packages':rows};out['counts']=dict(collections.Counter(x['lane'] for x in rows));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'])
if __name__=='__main__':main()

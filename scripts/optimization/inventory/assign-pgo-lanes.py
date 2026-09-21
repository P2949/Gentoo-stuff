#!/usr/bin/env python3
import argparse,json,hashlib,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--states',required=True);ap.add_argument('--backends',required=True);ap.add_argument('--overrides');ap.add_argument('--output',required=True);a=ap.parse_args();s=json.load(open(a.states));b={x['cpv']:x for x in json.load(open(a.backends))['packages']}; rows=[]
 overrides={}
 if a.overrides:
  extra=json.load(open(a.overrides))
  if not isinstance(extra.get('overrides'),list): raise SystemExit('REFUSED: lane override artifact has no override list')
  for item in extra['overrides']:
   if not all(item.get(k) for k in ('cpv','lane','reason_code')): raise SystemExit('REFUSED: incomplete lane override')
   if item['cpv'] in overrides: raise SystemExit(f"REFUSED: duplicate lane override: {item['cpv']}")
   overrides[item['cpv']]=(item['lane'],item['reason_code'])
 for x in s['records']:
  cpv=x['cpv']; info=b.get(cpv,{}); artifact_languages=info.get('artifact_language_evidence',{}); ev=sorted(set(info.get('backend_evidence',[])+info.get('inherits',[])+list(artifact_languages))); phases=info.get('phase_functions',[])
  if cpv in overrides: lane,reason=overrides[cpv]
  elif x['state']!='pending-pgo-classification': lane=x['state']; reason=x['reason_code']
  elif any('python' in z or 'java' in z or 'ruby' in z or 'perl' in z or z in ('distutils-r1','pypi','ruby-fakegem','perl-module') for z in ev) or any(token in cpv.lower().split('/',1)[-1] for token in ('python','ruby','perl','openjdk','jdk')): lane='unsupported-by-upstream-toolchain';reason='managed-language-or-runtime-eclass'
  elif any('cargo' in z or z in ('rust','rust-toolchain') for z in ev): lane='pgo-rust';reason='cargo-or-rust-eclass'
  elif any('go' in z for z in ev): lane='pgo-go';reason='go-eclass'
  elif any(any(k in z for k in ('cmake','meson','autotools','llvm','toolchain-funcs','libtool','ecm','frameworks.kde.org','xorg-3','multilib','qt6-build','gstreamer')) for z in ev): lane='pgo-clang-ir';reason='native-compiled-eclass'
  else: lane='pending-pgo-classification';reason='no-supported-backend-evidence'
  row={'cpv':cpv,'lane':lane,'reason_code':reason,'backend_evidence':ev}
  if cpv in overrides: row['decision_source']='reviewed-generation-override'
  else: row['decision_source']='generic-evidence'
  rows.append(row)
 out={'record_type':'pgo-lane-candidates','schema_version':1,'source_state':s['sha256'],'packages':rows};out['counts']=dict(collections.Counter(x['lane'] for x in rows));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'])
if __name__=='__main__':main()

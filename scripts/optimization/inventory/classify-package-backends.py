#!/usr/bin/env python3
"""Collect package-level build-backend evidence without guessing from category."""
import argparse,json,os,collections,hashlib,bz2,re
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))
from contents import parse_contents_line
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--vdb',default='/var/db/pkg');ap.add_argument('--output',required=True);a=ap.parse_args()
 if os.path.exists(a.output): raise SystemExit('REFUSED: package backend output already exists')
 rows=[]; seen=set()
 for cat in sorted(os.listdir(a.vdb)):
  cd=os.path.join(a.vdb,cat)
  if not os.path.isdir(cd):continue
  for pf in sorted(os.listdir(cd)):
   root=os.path.join(cd,pf); c=os.path.join(root,'CONTENTS'); cpv=cat+'/'+pf
   if cpv in seen: raise SystemExit(f'REFUSED: duplicate package backend identity {cpv}')
   seen.add(cpv)
   if not os.path.isfile(c):continue
   paths=[]
   for line in open(c,errors='replace'):
    try: parsed=parse_contents_line(line)
    except ValueError as exc: raise SystemExit(f'REFUSED: {c}: {exc}')
    if parsed is not None and parsed[0] in ('obj','sym'): paths.append(parsed[1])
   env={}
   for n in ('CFLAGS','CXXFLAGS','LDFLAGS','CHOST','FEATURES','USE','REPOSITORY'):
    p=os.path.join(root,n)
    if os.path.isfile(p):env[n]=open(p,errors='replace').read().strip()
   inherited=[]
   inherited_path=os.path.join(root,'INHERITED')
   if os.path.isfile(inherited_path):
    inherited=open(inherited_path,errors='replace').read().split()
   qa_prebuilt=False
   envbz=os.path.join(root,'environment.bz2')
   if os.path.isfile(envbz):
    raw=bz2.open(envbz,'rt',errors='replace').read()
    qa_prebuilt=bool(re.search(r'(?m)^declare -a QA_PREBUILT=\(', raw) or re.search(r'(?m)^QA_PREBUILT=', raw))
   ex=collections.Counter()
   for p in paths:
    for ext,lang in (('.rs','rust'),('.go','go'),('.c','c'),('.cc','c++'),('.cpp','c++'),('.java','java'),('.py','python'),('.js','javascript'),('.so','elf-shared'),('.a','static-archive')):
     if p.endswith(ext):ex[lang]+=1
   rows.append({'cpv':cpv,'environment':env,'inherits':inherited,'qa_prebuilt':qa_prebuilt,
                'vdb_environment_markers':['QA_PREBUILT'] if qa_prebuilt else [],
                'artifact_language_evidence':dict(ex),'backend_state':'requires-ebuild-and-build-log-review'})
 out={'record_type':'package-backend-classification','schema_version':1,'packages':rows};out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(len(rows))
if __name__=='__main__':main()

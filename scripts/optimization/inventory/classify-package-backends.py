#!/usr/bin/env python3
"""Collect package-level build-backend evidence without guessing from category."""
import argparse,json,os,collections,hashlib
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))
from contents import parse_contents_line
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--vdb',default='/var/db/pkg');ap.add_argument('--output',required=True);a=ap.parse_args(); rows=[]
 for cat in sorted(os.listdir(a.vdb)):
  cd=os.path.join(a.vdb,cat)
  if not os.path.isdir(cd):continue
  for pf in sorted(os.listdir(cd)):
   root=os.path.join(cd,pf); c=os.path.join(root,'CONTENTS')
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
   ex=collections.Counter()
   for p in paths:
    for ext,lang in (('.rs','rust'),('.go','go'),('.c','c'),('.cc','c++'),('.cpp','c++'),('.java','java'),('.py','python'),('.js','javascript'),('.so','elf-shared'),('.a','static-archive')):
     if p.endswith(ext):ex[lang]+=1
   rows.append({'cpv':cat+'/'+pf,'environment':env,'artifact_language_evidence':dict(ex),'backend_state':'requires-ebuild-and-build-log-review'})
 out={'record_type':'package-backend-classification','schema_version':1,'packages':rows};out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(len(rows))
if __name__=='__main__':main()

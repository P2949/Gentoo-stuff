#!/usr/bin/env python3
"""Resolve live DT_NEEDED names to owned provider CPVs from ELF metadata."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path
def canon(v): return json.dumps(v,sort_keys=True,separators=(",",":")).encode()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--elf',required=True,type=Path); ap.add_argument('--output',required=True,type=Path); a=ap.parse_args()
 src=json.loads(a.elf.read_text()); providers={}
 for x in src.get('artifacts',[]):
  # DT_NEEDED names resolve through the loader's SONAME namespace.  A path
  # basename is only a compatibility fallback for inventories that predate
  # explicit SONAME capture; it must never replace an authenticated SONAME.
  names=[]
  if x.get('soname'):
   names.append(str(x['soname']))
  elif x.get('path'):
   names.append(os.path.basename(x['path']))
  if x.get('owner_cpv'):
   for name in set(names):
    providers.setdefault(name,[]).append(x)
 rows=[]; unresolved=[]
 for x in src.get('artifacts',[]):
  consumer=x.get('owner_cpv');
  for needed in x.get('needed',[]) or []:
   matches=providers.get(needed,[])
   owners=sorted({m['owner_cpv'] for m in matches if m['owner_cpv'] != consumer})
   if len(owners)==1:
    rows.append({'provider_cpv':owners[0],'consumer_cpv':consumer,'relationship':'elf-needed','evidence':{'consumer_path':x.get('path'),'needed':needed,'provider_paths':sorted(m['path'] for m in matches if m['owner_cpv']==owners[0])}})
   elif not owners: unresolved.append({'consumer_cpv':consumer,'consumer_path':x.get('path'),'needed':needed,'reason':'provider-not-owned'})
   else: unresolved.append({'consumer_cpv':consumer,'consumer_path':x.get('path'),'needed':needed,'reason':'provider-ambiguous','owners':owners})
 unique={(x['provider_cpv'],x['consumer_cpv'],x['relationship']):x for x in rows}
 out={'record_type':'live-elf-dependency-source','schema_version':1,'source_elf_sha256':hashlib.sha256(a.elf.read_bytes()).hexdigest(),'records':sorted(unique.values(),key=lambda x:(x['provider_cpv'],x['consumer_cpv'])),'unresolved':unresolved}
 out['sha256']=hashlib.sha256(canon(out)).hexdigest(); a.output.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps({'records':len(out['records']),'unresolved':len(unresolved),'sha256':out['sha256']}))
if __name__=='__main__': main()

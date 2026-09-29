#!/usr/bin/env python3
"""Resolve live DT_NEEDED names to owned provider CPVs from ELF metadata."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path
def canon(v): return json.dumps(v,sort_keys=True,separators=(",",":")).encode()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--elf',required=True,type=Path); ap.add_argument('--output',required=True,type=Path); a=ap.parse_args()
 if a.output.exists(): raise SystemExit('REFUSED: ELF dependency output already exists')
 src=json.loads(a.elf.read_text()); providers={}
 artifacts=src.get('artifacts',[])
 identities=[(x.get('owner_cpv'),x.get('path')) for x in artifacts]
 if len(set(identities)) != len(identities):
  raise SystemExit('REFUSED: duplicate ELF artifact identity')
 for x in artifacts:
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
 for x in artifacts:
  consumer=x.get('owner_cpv');
  for needed in x.get('needed',[]) or []:
   matches=providers.get(needed,[])
   owners=sorted({m['owner_cpv'] for m in matches})
   if len(owners)==1:
    for provider in matches:
     if provider.get('owner_cpv') != owners[0]:
      continue
     rows.append({'provider_cpv':owners[0],'consumer_cpv':consumer,'relationship':'elf-needed','evidence':{'consumer_path':x.get('path'),'needed':needed,'provider_path':provider.get('path'),'provider_soname':provider.get('soname')}})
   elif not owners: unresolved.append({'consumer_cpv':consumer,'consumer_path':x.get('path'),'needed':needed,'reason':'provider-not-owned'})
   else: unresolved.append({'consumer_cpv':consumer,'consumer_path':x.get('path'),'needed':needed,'reason':'provider-ambiguous','owners':owners})
 unique={(x['provider_cpv'],x['consumer_cpv'],x['relationship'],x['evidence']['consumer_path'],x['evidence']['provider_path'],x['evidence']['needed']):x for x in rows}
 out={'record_type':'live-elf-dependency-source','schema_version':1,'source_elf_sha256':hashlib.sha256(a.elf.read_bytes()).hexdigest(),'records':sorted(unique.values(),key=lambda x:(x['provider_cpv'],x['consumer_cpv'])),'unresolved':unresolved}
 out['sha256']=hashlib.sha256(canon(out)).hexdigest(); a.output.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps({'records':len(out['records']),'unresolved':len(unresolved),'sha256':out['sha256']}))
if __name__=='__main__': main()

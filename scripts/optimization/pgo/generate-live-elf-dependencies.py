#!/usr/bin/env python3
"""Resolve live DT_NEEDED names to owned provider CPVs from ELF metadata."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path
def canon(v): return json.dumps(v,sort_keys=True,separators=(",",":")).encode()
def _directory(path):
 return os.path.dirname(path or "/") or "/"
def _search_dirs(consumer):
 """Return the consumer's authenticated loader search directories.

 The metadata stores paths as installed absolute paths.  Expand only the
 loader's `$ORIGIN` token; do not interpret arbitrary shell syntax.
 RUNPATH has precedence over RPATH for the executable's direct lookup.
 """
 origin=_directory(consumer.get("path"))
 values=consumer.get("runpath") or consumer.get("rpath") or []
 if isinstance(values,str): values=[values]
 out=[]
 for raw in values:
  # Dynamic tags are colon-separated lists.  Preserve declaration order;
  # empty entries mean the loader's default search scope and are not an
  # authenticated installed directory here.
  for value in str(raw).split(":"):
   if not value: continue
   value=value.replace("${ORIGIN}",origin).replace("$ORIGIN",origin)
   if not value.startswith("/"):
    value=os.path.normpath(os.path.join(origin,value))
   out.append(os.path.normpath(value))
 return out
def _provider_matches(consumer, name, providers):
 candidates=[p for p in providers.get(name,()) if not p.get("error")]
 if not candidates:
  return []
 # ELF class and machine are part of the authenticated metadata.  A loader
 # never satisfies a 32-bit request with a 64-bit provider (or vice versa).
 cclass=consumer.get("class"); cmachine=consumer.get("machine")
 if cclass:
  same=[p for p in candidates if not p.get("class") or p.get("class")==cclass]
  if not same: return []
  candidates=same
 if cmachine:
  same=[p for p in candidates if not p.get("machine") or p.get("machine")==cmachine]
  if not same: return []
  candidates=same
 search=_search_dirs(consumer)
 if search:
  # Loader directories are searched in declaration order.  A matching
  # provider in an explicit directory wins; when none is present the loader
  # continues with its default namespace (which is represented by the
  # remaining owned providers here).  Treating an explicit RUNPATH as a
  # closed universe incorrectly marks ordinary dependencies such as libc in
  # bundled interpreters as unresolved.
  scoped=[]
  for directory in search:
   scoped.extend(p for p in candidates if _directory(p.get("path")) == directory)
   if scoped:
    candidates=scoped
    break
 return candidates
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
   matches=_provider_matches(x,needed,providers)
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

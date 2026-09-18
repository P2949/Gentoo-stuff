#!/usr/bin/env python3
"""Create an exhaustive owned-artifact census from Portage CONTENTS."""
import argparse,hashlib,json,os,stat
from concurrent.futures import ThreadPoolExecutor

def inspect_path(item):
 path,owner=item; kind='missing'; size=None; mode=None; elf=None
 try:
  st=os.lstat(path); mode=stat.S_IMODE(st.st_mode)
  if stat.S_ISLNK(st.st_mode): kind='symlink'
  elif stat.S_ISREG(st.st_mode):
   kind='regular'; size=st.st_size
   with open(path,'rb') as f: h=f.read(64)
   if h[:4]==b'\x7fELF': elf={'class':h[4],'data':h[5],'type':int.from_bytes(h[16:18],'little'),'machine':int.from_bytes(h[18:20],'little')}
  else: kind='other'
 except OSError: pass
 return {'owner_cpv':owner,'path':path,'kind':kind,'size':size,'mode':mode,'elf':elf}

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--vdb',default='/var/db/pkg'); ap.add_argument('--output',required=True); a=ap.parse_args()
 items=[]; seen=set()
 for cat in sorted(os.listdir(a.vdb)):
  cd=os.path.join(a.vdb,cat)
  if not os.path.isdir(cd): continue
  for pf in sorted(os.listdir(cd)):
   root=os.path.join(cd,pf); c=os.path.join(root,'CONTENTS')
   if not os.path.isfile(c): continue
   owner=cat+'/'+pf
   for line in open(c,errors='replace'):
    q=line.split()
    if len(q)<2 or q[0] not in ('obj','sym'): continue
    path=q[1]; key=(path,owner)
    if key in seen: continue
    seen.add(key); items.append(key)
 workers=max(1,min(32,(os.cpu_count() or 1)*2))
 with ThreadPoolExecutor(max_workers=workers) as pool:
  rows=list(pool.map(inspect_path,items))
 out={'record_type':'owned-artifact-census','schema_version':1,'artifact_count':len(rows),'artifacts':sorted(rows,key=lambda x:(x['path'],x['owner_cpv']))}
 out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 json.dump(out,open(a.output,'w'),sort_keys=True,indent=2); open(a.output,'a').write('\n')
 from collections import Counter
 print(json.dumps({'artifacts':len(rows),'kinds':Counter(x['kind'] for x in rows),'elf':sum(x['elf'] is not None for x in rows)}))
if __name__=='__main__': main()

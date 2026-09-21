#!/usr/bin/env python3
"""Build a deterministic, non-authoritative inventory from the live Portage VDB.

The output is intentionally a candidate: directory review metadata is copied only
for paths already present in the previous reviewed inventory. New directory
records are marked unresolved and must be reviewed before framework activation.
"""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, stat

def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def parse_contents_line(line):
    """Parse a Portage CONTENTS record without splitting spaces in paths."""
    line = line.rstrip("\n")
    if " " not in line:
        return None
    kind, rest = line.split(" ", 1)
    if kind == "dir":
        return kind, rest, []
    if kind == "obj":
        fields = rest.rsplit(" ", 2)
        if len(fields) != 3:
            return None
        path, digest, mtime = fields
        return kind, path, [digest, mtime]
    if kind == "sym":
        if " -> " not in rest:
            return None
        path, target = rest.split(" -> ", 1)
        return kind, path, ["->", target]
    return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--vdb',default='/var/db/pkg'); ap.add_argument('--previous',required=True)
    ap.add_argument('--directory-review', help='review JSON for newly observed directories')
    ap.add_argument('--output',required=True); ap.add_argument('--generation-id',required=True)
    a=ap.parse_args(); prev=json.load(open(a.previous))
    if pathlib.Path(a.output).exists():
        raise SystemExit('REFUSED: live inventory output already exists')
    review = json.load(open(a.directory_review)) if a.directory_review else None
    reviewed = {}
    if review is not None:
        if review.get('record_type') != 'frozen-directory-review' or not isinstance(review.get('paths'), list):
            raise SystemExit('invalid directory review')
        for item in review['paths']:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str):
                raise SystemExit('invalid directory review path')
            reviewed[item['path']] = item
    old_dirs={x['path']:x for x in prev['owned_directories']}
    packages=[]; paths=[]; owners={}; dirs={}
    for cat in sorted(os.listdir(a.vdb)):
      cdir=os.path.join(a.vdb,cat)
      if not os.path.isdir(cdir): continue
      for pf in sorted(os.listdir(cdir)):
        root=os.path.join(cdir,pf); contents=os.path.join(root,'CONTENTS')
        if not os.path.isfile(contents): continue
        cpv=f'{cat}/{pf}'
        meta={}
        for n in ('CATEGORY','PF','SLOT','EAPI','USE','REPOSITORY','BUILD_TIME','CFLAGS','CXXFLAGS','CHOST','FEATURES'):
          p=os.path.join(root,n)
          if os.path.isfile(p): meta[n]=open(p,errors='replace').read().strip()
        entries=[]
        for line in open(contents,errors='replace'):
          parsed=parse_contents_line(line)
          if parsed is None: continue
          kind,path,tail=parsed; entries.append([kind,path,tail])
          if kind != 'dir': paths.append({'owner_cpv':cpv,'path':path}); owners.setdefault(path,set()).add(cpv)
          if kind=='dir': dirs.setdefault(path,set()).add(cpv)
        packages.append({'cpv':cpv,'entry_sha256':sha({'metadata':meta,'contents':entries})})
    # Include parent directories of owned paths, using live stat data.
    for p in list(owners):
      cur=pathlib.PurePosixPath(p).parent
      while str(cur) not in ('','.', '/'):
        dirs.setdefault(str(cur), set()).update(owners[p]); cur=cur.parent
    outdirs=[]; unresolved=[]
    for p in sorted(dirs):
      old=old_dirs.get(p)
      try: s=os.stat(p); uid,gid,mode=s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode)
      except OSError: uid=gid=mode=None
      if old and old.get('uid')==uid and old.get('gid')==gid and old.get('mode')==mode:
        for owner in sorted(dirs[p]):
          rec=dict(old); rec['owner_cpv']=owner; outdirs.append(rec)
      else:
        item = reviewed.get(p)
        if item is not None and item.get('uid') == uid and item.get('gid') == gid and item.get('mode') == mode:
          resolution = {
              'evidence': [{'kind': 'report', 'path': str(pathlib.Path(a.directory_review).resolve()), 'sha256': hashlib.sha256(pathlib.Path(a.directory_review).read_bytes()).hexdigest()}],
              'reason_code': 'not-machine-code', 'registry_version': '1',
              'reviewed_at': review.get('reviewed_at', ''),
              'reviewed_by': review.get('reviewed_by', ''),
          }
          for owner in sorted(dirs[p]):
            outdirs.append({'owner_cpv':owner,'path':p,'uid':uid,'gid':gid,'mode':mode,'classification':'not-applicable','resolution':resolution})
          continue
        unresolved.append(p)
        for owner in sorted(dirs[p]):
          outdirs.append({'owner_cpv':owner,'path':p,'uid':uid,'gid':gid,'mode':mode,'classification':'unresolved','resolution':{'reason_code':'requires-directory-review','registry_version':'1'}})
    unique_paths={(x['owner_cpv'],x['path']):x for x in paths}
    result={'generation_id':a.generation_id,'inventory_id':a.generation_id+'-v1','owned_directories':sorted(outdirs,key=lambda x:(x['owner_cpv'],x['path'])),'owned_paths':sorted(unique_paths.values(),key=lambda x:(x['owner_cpv'],x['path'])),'packages':sorted(packages,key=lambda x:x['cpv']),'unresolved_directories':sorted(unresolved),'record_type':'frozen-inventory','schema_version':2}
    pathlib.Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    with open(a.output,'w') as f: json.dump(result,f,sort_keys=True,indent=2); f.write('\n')
    print(json.dumps({'packages':len(packages),'owned_paths':len(result['owned_paths']),'owned_directories':len(outdirs),'unresolved_directories':len(unresolved),'output':a.output},sort_keys=True))
if __name__=='__main__': main()

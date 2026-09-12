#!/usr/bin/env python3
"""Plan/apply a fail-closed Portage framework-target environment migration."""
from __future__ import annotations
import argparse,bz2,hashlib,json,os,re,tempfile
from pathlib import Path
TARGET_RE=re.compile(rb'^declare -x GENTOO_OPT_FRAMEWORK_TARGET="[^"]*"\n?$',re.M)

def digest(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--vdb-root',type=Path,default=Path('/var/db/pkg'));p.add_argument('--active-target',required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
 if a.apply and os.geteuid()!=0: raise SystemExit('apply requires root')
 rows=[]
 for f in sorted(a.vdb_root.glob('*/*/environment.bz2')):
  try: raw=bz2.decompress(f.read_bytes())
  except Exception: continue
  m=TARGET_RE.search(raw)
  if not m: continue
  old=m.group(0).decode(errors='replace').strip().split('="',1)[1].rstrip('"')
  if old==a.active_target: continue
  new=TARGET_RE.sub((f'declare -x GENTOO_OPT_FRAMEWORK_TARGET="{a.active_target}"\\n').encode(),raw,count=1)
  rows.append({'cpv':str(f.parent.relative_to(a.vdb_root)),'path':str(f),'old_target':old,'new_target':a.active_target,'old_sha256':digest(raw),'new_sha256':digest(new),'bytes':len(raw),'changed':raw!=new})
 if a.apply:
  backup=a.report.parent/'framework-environment-backup'; backup.mkdir(mode=0o700,parents=True,exist_ok=True)
  for r in rows:
   f=Path(r['path']); rel=f.relative_to(a.vdb_root); b=backup/rel; b.parent.mkdir(mode=0o700,parents=True,exist_ok=True); b.write_bytes(f.read_bytes()); os.chmod(b,0o600)
   raw=bz2.decompress(f.read_bytes()); new=TARGET_RE.sub((f'declare -x GENTOO_OPT_FRAMEWORK_TARGET="{a.active_target}"\\n').encode(),raw,count=1)
   fd,tmp=tempfile.mkstemp(dir=f.parent,prefix='.environment.',suffix='.bz2'); os.fchmod(fd,stat_mode:=f.stat().st_mode&0o7777); os.write(fd,bz2.compress(new,9)); os.fsync(fd); os.close(fd); os.replace(tmp,f); os.chown(f,rstat:=f.stat().st_uid,f.stat().st_gid)
 out={'schema':'framework-environment-migration-v1','active_target':a.active_target,'apply':a.apply,'count':len(rows),'rows':rows}
 a.report.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps({'apply':a.apply,'count':len(rows),'report':str(a.report)})); return 0
if __name__=='__main__': raise SystemExit(main())

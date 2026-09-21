#!/usr/bin/env python3
"""Classify kernel-policy exclusions from exact ebuild transaction evidence.

Category names are never used as the decision.  A CPV is excluded only when
its exact ebuild contains a reviewed lifecycle mutation marker or its VDB
CONTENTS proves ownership of a forbidden boot/kernel artifact.
"""
import argparse, hashlib, json, re
from pathlib import Path
FORBIDDEN=re.compile(r'(/boot/|/efi/|/sys/firmware/efi|/etc/kernel/)',re.I)
LIFECYCLE_HINT=re.compile(r'(initramfs|dracut|installkernel|efibootmgr|bootctl|grub-install)',re.I)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--vdb',default='/var/db/pkg');ap.add_argument('--ebuild-root',default='/var/db/repos');ap.add_argument('--output',required=True);a=ap.parse_args(); m=json.loads(Path(a.manifest).read_text()); rows=[]
 for item in m['packages']:
  cpv=item['cpv']; cat,pf=cpv.split('/',1); root=Path(a.vdb)/cat/pf; evidence=[]
  cont=root/'CONTENTS'
  if cont.is_file():
   for line in cont.read_text(errors='replace').splitlines():
    fields=line.split()
    if not fields: continue
    if fields[0]=='obj' and len(fields)>=2 and FORBIDDEN.search(fields[1]): evidence.append(fields[1])
    elif fields[0]=='sym' and len(fields)>=2 and FORBIDDEN.search(fields[1]): evidence.append(fields[1])
  # Locate the exact ebuild by CPV filename; source repositories are evidence,
  # not category policy.  Failure to locate it is explicit pending review.
  if Path(a.output).exists(): raise SystemExit('REFUSED: kernel-policy output already exists')
  pn=item.get('pn') or pf.rsplit('-',1)[0]
  ebuild_name=item.get('ebuild') or (pf+'.ebuild')
  repo_name=''
  for marker in ('REPOSITORY','repository'):
   marker_path=root/marker
   if marker_path.is_file(): repo_name=marker_path.read_text(errors='replace').strip(); break
  matches=[]
  if repo_name:
   candidate=Path(a.ebuild_root)/repo_name/cat/pn/ebuild_name
   if candidate.is_file(): matches=[candidate]
  if not matches:
   matches=[x for x in Path(a.ebuild_root).glob(f'*/{cat}/{pn}/{ebuild_name}') if x.is_file()]
  if len(matches)>1: raise SystemExit(f'REFUSED: exact repository/ebuild identity is ambiguous for {cpv}')
  text=''
  for candidate in matches:
   if candidate.is_file(): text=candidate.read_text(errors='replace'); break
  markers=sorted(set(LIFECYCLE_HINT.findall(text))) if text else []
  excluded=bool(evidence)
  state='kernel-policy-exclusion' if excluded else ('pending-lifecycle-review' if markers else 'userspace-transaction')
  rows.append({'cpv':cpv,'state':state,'reason_code':'owned-forbidden-artifact' if excluded else ('lifecycle-hint-review' if markers else 'no-forbidden-lifecycle-evidence'),'evidence_paths':sorted(evidence),'ebuild_markers':markers})
 out={'record_type':'kernel-policy-classification','schema_version':1,'source_manifest_sha256':hashlib.sha256(Path(a.manifest).read_bytes()).hexdigest(),'records':rows};out['counts']={}
 for x in rows: out['counts'][x['state']]=out['counts'].get(x['state'],0)+1
 out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();Path(a.output).write_text(json.dumps(out,sort_keys=True,indent=2)+'\n');print(json.dumps(out['counts'],sort_keys=True))
if __name__=='__main__':main()

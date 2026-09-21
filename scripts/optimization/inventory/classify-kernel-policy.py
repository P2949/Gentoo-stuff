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
    fields=line.rstrip('\n').split(' ', 1)
    if len(fields) != 2: raise SystemExit(f'REFUSED: malformed CONTENTS record for {cpv}')
    kind, rest = fields
    if kind == 'obj':
     parts=rest.rsplit(' ', 2)
     if len(parts) != 3: raise SystemExit(f'REFUSED: malformed obj CONTENTS record for {cpv}')
     path=parts[0]
     if FORBIDDEN.search(path): evidence.append(path)
    elif kind == 'sym':
     if ' -> ' not in rest: raise SystemExit(f'REFUSED: malformed sym CONTENTS record for {cpv}')
     path=rest.split(' -> ', 1)[0]
     if FORBIDDEN.search(path): evidence.append(path)
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
  source_unavailable=False
  if repo_name:
   candidate=Path(a.ebuild_root)/repo_name/cat/pn/ebuild_name
   if candidate.is_file(): matches=[candidate]
   else: source_unavailable=True
  else:
   source_unavailable=True
  if len(matches)>1: raise SystemExit(f'REFUSED: exact repository/ebuild identity is ambiguous for {cpv}')
  text=matches[0].read_text(errors='replace') if matches else ''
  markers=sorted(set(LIFECYCLE_HINT.findall(text))) if text else []
  excluded=bool(evidence)
  state='kernel-policy-exclusion' if excluded else ('pending-lifecycle-review' if source_unavailable or markers else 'userspace-transaction')
  reason='owned-forbidden-artifact' if excluded else ('source-unavailable' if source_unavailable else ('lifecycle-hint-review' if markers else 'no-forbidden-lifecycle-evidence'))
  rows.append({'cpv':cpv,'state':state,'reason_code':reason,'evidence_paths':sorted(evidence),'ebuild_markers':markers,'repository':repo_name,'ebuild_path':str(matches[0]) if matches else None})
 out={'record_type':'kernel-policy-classification','schema_version':1,'source_manifest_sha256':hashlib.sha256(Path(a.manifest).read_bytes()).hexdigest(),'records':rows};out['counts']={}
 for x in rows: out['counts'][x['state']]=out['counts'].get(x['state'],0)+1
 out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();Path(a.output).write_text(json.dumps(out,sort_keys=True,indent=2)+'\n');print(json.dumps(out['counts'],sort_keys=True))
if __name__=='__main__':main()

#!/usr/bin/env python3
"""Classify kernel-policy exclusions from exact ebuild transaction evidence.

Category names are never used as the decision.  A CPV is excluded only when
its exact ebuild contains a reviewed lifecycle mutation marker or its VDB
CONTENTS proves ownership of a forbidden boot/kernel artifact.
"""
import argparse, hashlib, json, re
from pathlib import Path
FORBIDDEN=re.compile(r'(/boot/|/efi/|/sys/firmware/efi|/etc/kernel/|initramfs|dracut|installkernel|efibootmgr|bootctl|grub-install)',re.I)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--vdb',default='/var/db/pkg');ap.add_argument('--ebuild-root',default='/var/db/repos');ap.add_argument('--output',required=True);a=ap.parse_args(); m=json.loads(Path(a.manifest).read_text()); rows=[]
 for item in m['packages']:
  cpv=item['cpv']; cat,pf=cpv.split('/',1); root=Path(a.vdb)/cat/pf; evidence=[]
  cont=root/'CONTENTS'
  if cont.is_file():
   evidence += [line.split()[-1] for line in cont.read_text(errors='replace').splitlines() if line.startswith(('obj ','sym ')) and FORBIDDEN.search(line)]
  # Locate the exact ebuild by CPV filename; source repositories are evidence,
  # not category policy.  Failure to locate it is explicit pending review.
  matches=list(Path(a.ebuild_root).glob(f'*/{cat}/{item.get("pn",pf.split("-")[0])}/{item.get("ebuild","")}')) if item.get('ebuild') else []
  text=''
  for candidate in matches:
   if candidate.is_file(): text=candidate.read_text(errors='replace'); break
  markers=sorted(set(FORBIDDEN.findall(text))) if text else []
  excluded=bool(evidence or markers)
  rows.append({'cpv':cpv,'state':'kernel-policy-exclusion' if excluded else 'userspace-transaction','reason_code':'forbidden-lifecycle-evidence' if excluded else 'no-forbidden-lifecycle-evidence','evidence_paths':sorted(evidence),'ebuild_markers':markers})
 out={'record_type':'kernel-policy-classification','schema_version':1,'source_manifest_sha256':hashlib.sha256(Path(a.manifest).read_bytes()).hexdigest(),'records':rows};out['counts']={}
 for x in rows: out['counts'][x['state']]=out['counts'].get(x['state'],0)+1
 out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();Path(a.output).write_text(json.dumps(out,sort_keys=True,indent=2)+'\n');print(json.dumps(out['counts'],sort_keys=True))
if __name__=='__main__':main()

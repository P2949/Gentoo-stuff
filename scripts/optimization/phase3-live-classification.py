#!/usr/bin/env python3
"""Reconcile a current VDB package boundary with prior authenticated classification."""
import argparse,datetime,hashlib,json
from pathlib import Path

def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--live-inventory',type=Path,required=True); ap.add_argument('--prior-classification',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
 live=json.loads(a.live_inventory.read_text()); old=json.loads(a.prior_classification.read_text()); by={x['cpv']:x for x in old['packages']}; rec=[]; new=[]
 for e in live['cpvs']:
  x=by.get(e['cpv'])
  if x: rec.append(dict(x,source_classification=str(a.prior_classification),source_classification_sha256=digest(a.prior_classification)))
  else: new.append(e['cpv']); rec.append({'cpv':e['cpv'],'classification':'unclassified-new-cpv','pgo_backend':'pending-backend-classification','elf_count':None,'source_classification':'new-live-cpv'})
 for x,e in zip(rec,live['cpvs']):
  if e.get('kernel_policy_exclusion'): x.update(classification='kernel-policy-exclusion',pgo_backend='kernel-policy-exclusion')
 cov={'package_count':len(rec),'reused_prior_records':len(rec)-len(new),'new_live_cpvs':len(new),'pending_backend_or_applicability':sum(str(x.get('pgo_backend','')).startswith('pending') for x in rec),'kernel_policy_exclusion':sum(x.get('pgo_backend')=='kernel-policy-exclusion' for x in rec),'unclassified':len(new)}
 out={'schema':'phase3-live-package-classification-v2','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'generation_id':live.get('generation_id'),'packages':sorted(rec,key=lambda x:x['cpv']),'coverage':cov,'provenance':{'live_inventory':str(a.live_inventory),'live_inventory_sha256':digest(a.live_inventory),'prior_classification':str(a.prior_classification),'prior_classification_sha256':digest(a.prior_classification)}}
 a.output.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps(cov,sort_keys=True))
if __name__=='__main__': main()

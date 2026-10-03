#!/usr/bin/env python3
"""Create the typed reverse-dependency graph from Portage and DT_NEEDED inputs."""
import argparse, hashlib, json
from pathlib import Path

def canon(v): return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def load(p): return json.loads(Path(p).read_text())
def verify_contract(doc, label):
 if 'record_type' not in doc:
  return
 if not isinstance(doc.get('schema_version'), int) or not isinstance(doc.get('sha256'), str):
  raise SystemExit(f'REFUSED: {label} source contract is incomplete')
 expected = {
  'Portage': ('live-portage-dependency-source', 3),
  'ELF': ('live-elf-dependency-source', 1),
 }
 record_type, schema_version = expected[label]
 if doc.get('record_type') != record_type or doc.get('schema_version') != schema_version:
  raise SystemExit(f'REFUSED: unsupported {label} source contract')
 unsigned=dict(doc); declared=unsigned.pop('sha256')
 if hashlib.sha256(canon(unsigned)).hexdigest() != declared:
  raise SystemExit(f'REFUSED: {label} source contract self-digest mismatch')
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--portage',required=True); ap.add_argument('--elf',required=True); ap.add_argument('--unresolved-review',help='authenticated review listing every permitted unresolved ELF edge'); ap.add_argument('--output',required=True); a=ap.parse_args()
 if Path(a.output).exists(): raise SystemExit('REFUSED: reverse-dependency output already exists')
 p,e=load(a.portage),load(a.elf); rows=[]
 if not isinstance(p,dict) or not isinstance(e,dict): raise SystemExit('REFUSED: reverse-dependency sources must be JSON objects')
 verify_contract(p, 'Portage'); verify_contract(e, 'ELF')
 if not isinstance(p.get('records',[]),list) or not isinstance(p.get('build_records',[]),list): raise SystemExit('REFUSED: Portage graph source has invalid record lists')
 if p.get('source_errors'): raise SystemExit('REFUSED: Portage graph source contains unresolved source errors')
 elf_edges = e.get('records', e.get('edges', []))
 if not isinstance(elf_edges,list): raise SystemExit('REFUSED: ELF graph source has invalid edge list')
 if not p.get('records') and not p.get('build_records'):
  raise SystemExit('REFUSED: Portage dependency authority is empty')
 if not elf_edges:
  raise SystemExit('REFUSED: ELF DT_NEEDED authority is empty')
 unresolved=e.get('unresolved', [])
 if unresolved:
  if not a.unresolved_review:
   raise SystemExit('REFUSED: ELF graph source contains unresolved dynamic dependency edges')
  review=load(a.unresolved_review)
  allowed=review.get('records', review.get('unresolved', [])) if isinstance(review,dict) else review
  if not isinstance(allowed,list) or sorted(allowed,key=canon) != sorted(unresolved,key=canon):
   raise SystemExit('REFUSED: unresolved ELF edge review does not exactly cover source unresolved edges')
 sources=((p,'records','portage-runtime'),(p,'build_records','portage-build'),(e,'records','elf-needed'),(e,'edges','elf-needed'))
 seen=set()
 elf_rows={}
 source_rows={}
 for source,key,rel in sources:
  if key not in source: continue
  for x in source.get(key,[]):
   provider=x.get('provider_cpv') or x.get('provider'); consumer=x.get('consumer_cpv') or x.get('consumer')
   if provider and consumer and provider != consumer:
    row={'provider_cpv':provider,'consumer_cpv':consumer,'relationship':x.get('relationship',rel),'evidence':x.get('evidence',{})}
    if row['relationship'] not in {'portage-runtime','portage-build','elf-needed'}:
     raise SystemExit(f"REFUSED: unsupported reverse-dependency relationship: {row['relationship']}")
    identity=(provider,consumer,row['relationship'])
    if identity in seen:
     if row['relationship'] != 'elf-needed':
      existing = source_rows[identity]
      # Distinct Portage dependency expressions (for example slot and USE
      # variants in one RDEPEND) prove the same typed scheduling edge. Keep
      # each authenticated expression, but reject an exact duplicate record.
      if row.get('evidence', {}) == existing.get('evidence', {}):
       raise SystemExit(f"REFUSED: duplicate reverse-dependency edge: {identity}")
      evidence = existing.get('evidence', {})
      if 'dependency_edges' in evidence:
       proofs = evidence['dependency_edges']
       if proofs and proofs[-1] == row.get('evidence', {}):
        raise SystemExit(f"REFUSED: duplicate reverse-dependency edge: {identity}")
       proofs.append(row.get('evidence', {}))
      else:
       existing['evidence'] = {'dependency_edges': [evidence, row.get('evidence', {})]}
      continue
     # Multiple owned artifacts can legitimately establish the same package
     # relationship.  Keep every artifact-level proof while projecting one
     # package-level scheduling edge.
     existing = elf_rows[identity]
     proofs = existing.setdefault('evidence', {}).setdefault('artifact_edges', [])
     proofs.append(row.get('evidence', {}))
     continue
    seen.add(identity)
    # Preserve an authenticated workload binding when the upstream source has one;
    # dropping it here makes the planner appear to have no representative consumer.
    if isinstance(x.get('workload'),dict): row['workload']=x['workload']
    if row['relationship'] == 'elf-needed':
     row['evidence'] = {"artifact_edges": [row.get('evidence', {})]}
     elf_rows[identity] = row
    else:
     source_rows[identity] = row
    rows.append(row)
 ordered=sorted(rows,key=lambda x:(x['provider_cpv'],x['consumer_cpv'],x['relationship']))
 out={'record_type':'reverse-dependency-graph','schema_version':2,'source_contract':{'portage_runtime_records':sum(1 for x in ordered if x['relationship']=='portage-runtime'),'portage_build_records':sum(1 for x in ordered if x['relationship']=='portage-build'),'elf_needed_records':sum(1 for x in ordered if x['relationship']=='elf-needed')},'portage_source_sha256':hashlib.sha256(Path(a.portage).read_bytes()).hexdigest(),'elf_source_sha256':hashlib.sha256(Path(a.elf).read_bytes()).hexdigest(),'records':ordered}
 out['sha256']=hashlib.sha256(canon(out)).hexdigest(); Path(a.output).write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps({'records':len(out['records']),'sha256':out['sha256']}))
if __name__=='__main__': main()

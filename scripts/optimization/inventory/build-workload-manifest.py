#!/usr/bin/env python3
import argparse,json,hashlib,collections
from pathlib import Path
def scope_row(cpv, rows):
 for row in rows:
  selector=row.get('selector','')
  if cpv == selector or cpv.startswith(selector + '-'):
   return row
 return None
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--lanes',required=True);ap.add_argument('--elf',required=True);ap.add_argument('--scope-policy');ap.add_argument('--output',required=True);a=ap.parse_args()
 if __import__('os').path.exists(a.output): raise SystemExit('REFUSED: workload manifest output already exists')
 lanes=json.load(open(a.lanes)); lane_rows=[x for x in lanes['packages'] if x.get('lane','').startswith('pgo-')]
 if len({x.get('cpv') for x in lane_rows}) != len(lane_rows): raise SystemExit('REFUSED: duplicate CPV in lane authority')
 lane={x['cpv']:x['lane'] for x in lane_rows}; scope=[]
 if a.scope_policy:
  doc=json.load(open(a.scope_policy))
  if doc.get('schema') != 'optimization-scope-policy-v1': raise SystemExit('REFUSED: unsupported scope policy schema')
  scope=doc.get('scope', [])
 by=collections.defaultdict(list)
 for x in json.load(open(a.elf))['artifacts']:
  if x.get('error') or not x.get('type'):
   continue
  if x['owner_cpv'] not in lane:continue
  # A runnable native entrypoint does not require PT_INTERP: statically linked
  # executables are valid workload targets too.  Preserve every entrypoint;
  # workload selection must be evidence-based, never an arbitrary first-N cut.
  if x['type'] in ('EXEC (Executable file)','DYN (Position-Independent Executable file)'):
   by[x['owner_cpv']].append({'path':x['path'],'type':x['type'],'build_id':x['build_id']})
 rows=[]
 for cpv in sorted(lane):
  sr=scope_row(cpv, scope)
  if sr and sr.get('training') is False:
   rows.append({'cpv':cpv,'lane':lane[cpv],'entrypoints':[],'state':'scope-excluded','reason':sr.get('reason_code')})
   continue
  entries=sorted(by.get(cpv,[]),key=lambda x:x['path'])
  rows.append({'cpv':cpv,'lane':lane[cpv],'entrypoints':entries,'state':'workload-candidate' if entries else 'no-runnable-entrypoint'})
 out={'record_type':'representative-workload-manifest','schema_version':2,'source_lanes':lanes['sha256'],'source_scope_policy_sha256':hashlib.sha256(Path(a.scope_policy).read_bytes()).hexdigest() if a.scope_policy else None,'packages':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'])
if __name__=='__main__':main()

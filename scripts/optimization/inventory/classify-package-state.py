#!/usr/bin/env python3
import argparse,json,hashlib,collections
from portage.versions import catpkgsplit
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--census',required=True);ap.add_argument('--kernel-set');ap.add_argument('--mutation-policy');ap.add_argument('--output',required=True);ap.add_argument('--vdb',default='/var/db/pkg');a=ap.parse_args()
 if not a.kernel_set and not a.mutation_policy: raise SystemExit('REFUSED: provide canonical --mutation-policy (legacy --kernel-set is accepted only for compatibility)')
 m=json.load(open(a.manifest)); c=json.load(open(a.census));
 # Package-level PGO applicability includes native artifacts that are not
 # installed ELF owners (archives, objects, device-code/native build tools).
 native_suffixes=('.a','.o','.lo','.bc','.ptx','.cubin')
 owners={x['owner_cpv'] for x in c['artifacts'] if x['kind'] in ('regular','symlink') and (x.get('elf') or str(x.get('path','')).endswith(native_suffixes) or x.get('native_artifact') is True)}
 decisions={}
 if a.mutation_policy:
  policy=json.load(open(a.mutation_policy))
  for row in policy.get('records',[]):
   if not isinstance(row,dict) or not row.get('cpv') or row.get('decision') not in {'userspace','kernel-policy-exclusion'}:
    raise SystemExit('REFUSED: malformed mutation-policy record')
   if row['cpv'] in decisions: raise SystemExit(f"REFUSED: duplicate mutation-policy CPV {row['cpv']}")
   decisions[row['cpv']]=row
  if not decisions: raise SystemExit('REFUSED: empty mutation-policy')
 else:
  for atom in (x.strip() for x in open(a.kernel_set) if x.strip()): decisions[atom]={'decision':'kernel-policy-exclusion','triggers':['legacy-policy-set'],'evidence':[]}
 atoms={}
 for cpv in [x['cpv'] for x in m['packages']]:
  cat,pf=cpv.split('/',1); root=a.vdb+'/'+cat+'/'+pf
  category_file=root+'/CATEGORY'; pn_file=root+'/PN'
  if not __import__('os').path.isfile(category_file):
   raise SystemExit(f'REFUSED: authoritative VDB CATEGORY metadata missing for {cpv}')
  category=open(category_file).read().strip()
  pn=open(pn_file).read().strip() if __import__('os').path.isfile(pn_file) else ''
  if not pn:
   split=catpkgsplit(pf)
   if not split or split[0] != 'null':
    raise SystemExit(f'REFUSED: cannot derive authoritative PN for {cpv}')
   pn=split[1]
  if category != cat or not category or not pn or '/' in pn:
   raise SystemExit(f'REFUSED: invalid authoritative VDB CATEGORY/PN metadata for {cpv}')
  atoms[cpv]=category+'/'+pn
 rows=[]
 for cpv in [x['cpv'] for x in m['packages']]:
  policy_row=decisions.get(cpv) or decisions.get(atoms[cpv])
  if policy_row and policy_row['decision']=='kernel-policy-exclusion': state,reason='kernel-policy-exclusion','kernel-policy-exclusion'
  elif cpv not in owners: state,reason='not-applicable','no-owned-native-artifact'
  else: state,reason='pending-pgo-classification','owned-native-artifact-requires-backend-and-profile'
  mutation=policy_row or {'decision':'userspace','triggers':[],'evidence':[]}
  rows.append({'cpv':cpv,'state':state,'reason_code':reason,'mutation_policy':mutation})
 out={'record_type':'package-optimization-state','schema_version':2,'source_inventory':m.get('inventory_id'),'mutation_policy_source':a.mutation_policy,'records':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'])
if __name__=='__main__':main()

#!/usr/bin/env python3
import argparse,json,hashlib,collections
def valid_recipes(value):
 return isinstance(value,list) and bool(value) and all(isinstance(x,dict) and (x.get('path') or x.get('executable')) and (x.get('argv') or x.get('command') or x.get('recipe')) for x in value)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bindings',required=True);ap.add_argument('--recipes',required=True);ap.add_argument('--output',required=True);ap.add_argument('--per-lane',type=int,default=4);ap.add_argument('--schedule');ap.add_argument('--mode',choices=('training','exhaustive-generation'),default='training');a=ap.parse_args();b=json.load(open(a.bindings));r=json.load(open(a.recipes)); recipe_rows=r['packages'];
 if len({row.get('cpv') for row in recipe_rows}) != len(recipe_rows): raise SystemExit('REFUSED: duplicate CPV in recipe authority')
 rec={x['cpv']:x for x in recipe_rows}; by=collections.defaultdict(list)
 if __import__('pathlib').Path(a.output).exists(): raise SystemExit('REFUSED: wave plan output already exists')
 if a.schedule:
  scheduled=json.load(open(a.schedule)); source_packages=scheduled.get('packages',[])
  if scheduled.get('state') not in {None,'planned-not-authorized'}: raise SystemExit('REFUSED: scheduler output is not an unconsumed plan')
 else:
  source_packages=b.get('records', b.get('packages', []))
 for x in source_packages:
  lane=x.get('lane')
  if a.schedule and (not isinstance(lane,str) or not lane.startswith('pgo-')):
   raise SystemExit(f"REFUSED: scheduled package has incomplete lane: {x.get('cpv')}")
  if isinstance(lane,str) and lane.startswith('pgo-'):
   if a.mode == 'training' and x['cpv'] not in rec:
    raise SystemExit(f"REFUSED: package is missing workload recipe record: {x['cpv']}")
   recipe_source=x.get('recipes') or rec.get(x['cpv'],{}).get('recipes',[])
   if a.mode == 'training' and not valid_recipes(recipe_source):
    raise SystemExit(f"REFUSED: package has incomplete workload recipe: {x['cpv']}")
   package_purpose=rec.get(x['cpv'],{}).get('purpose')
   row={'cpv':x['cpv'],'lane':x['lane'],'compiler_sha256':x.get('compiler_sha256',b.get('compiler_sha256')),'profile_path':x.get('profile_path'),'recipes':[dict(recipe, purpose=package_purpose) if package_purpose else dict(recipe) for recipe in recipe_source]}
   row['identity_sha256']=x.get('identity_sha256')
   if a.mode == 'exhaustive-generation': row['recipes']=[]
   if not row['compiler_sha256'] or not row['profile_path'] or not row['identity_sha256']: raise SystemExit(f"REFUSED: scheduled package lacks compiler/profile identity: {x['cpv']}")
   by[x['lane']].append(row)
 wave=[]
 if a.schedule:
  wave=[next(row for row in by.get(x.get('lane'),[]) if row['cpv']==x.get('cpv')) for x in source_packages if x.get('cpv') in {p.get('cpv') for rows in by.values() for p in rows}]
 else: wave=[sorted(by[lane],key=lambda x:x['cpv'])[:a.per_lane] for lane in sorted(by)]
 if not a.schedule: wave=[item for group in wave for item in group]
 out={'record_type':'profile-generation-wave-plan','schema_version':2,'generation_id':(scheduled.get('generation_id') if a.schedule else None),'source_bindings':b['sha256'],'source_recipes':r['sha256'],'source_schedule':hashlib.sha256(open(a.schedule,'rb').read()).hexdigest() if a.schedule else None,'per_lane':a.per_lane,'packages':wave,'state':'planned-not-authorized'};out['counts']=dict(collections.Counter(x['lane'] for x in wave));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'],len(wave))
if __name__=='__main__':main()

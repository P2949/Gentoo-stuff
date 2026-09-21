#!/usr/bin/env python3
import argparse,json,hashlib,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bindings',required=True);ap.add_argument('--recipes',required=True);ap.add_argument('--output',required=True);ap.add_argument('--per-lane',type=int,default=4);ap.add_argument('--schedule');a=ap.parse_args();b=json.load(open(a.bindings));r=json.load(open(a.recipes)); rec={x['cpv']:x for x in r['packages']}; by=collections.defaultdict(list)
 if a.schedule:
  scheduled=json.load(open(a.schedule)); source_packages=scheduled.get('packages',[])
  if scheduled.get('state') not in {None,'planned-not-authorized'}: raise SystemExit('REFUSED: scheduler output is not an unconsumed plan')
 else:
  source_packages=b.get('records', b.get('packages', []))
 for x in source_packages:
  if x.get('lane','').startswith('pgo-') and rec.get(x['cpv'],{}).get('recipes'):
   row={'cpv':x['cpv'],'lane':x['lane'],'compiler_sha256':x.get('compiler_sha256',b.get('compiler_sha256')),'profile_path':x.get('profile_path'),'recipes':x.get('recipes') or rec[x['cpv']]['recipes']}
   if not row['compiler_sha256'] or not row['profile_path']: raise SystemExit(f"REFUSED: scheduled package lacks compiler/profile identity: {x['cpv']}")
   by[x['lane']].append(row)
 wave=[]
 if a.schedule: wave=sorted(by.values(),key=lambda rows: rows[0]['lane'] if rows else '')
 else: wave=[sorted(by[lane],key=lambda x:x['cpv'])[:a.per_lane] for lane in sorted(by)]
 wave=[item for group in wave for item in group]
 out={'record_type':'profile-generation-wave-plan','schema_version':2,'source_bindings':b['sha256'],'source_recipes':r['sha256'],'source_schedule':hashlib.sha256(open(a.schedule,'rb').read()).hexdigest() if a.schedule else None,'per_lane':a.per_lane,'packages':wave,'state':'planned-not-authorized'};out['counts']=dict(collections.Counter(x['lane'] for x in wave));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'],len(wave))
if __name__=='__main__':main()

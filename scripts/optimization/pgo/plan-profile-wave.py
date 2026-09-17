#!/usr/bin/env python3
import argparse,json,hashlib,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bindings',required=True);ap.add_argument('--recipes',required=True);ap.add_argument('--output',required=True);ap.add_argument('--per-lane',type=int,default=4);a=ap.parse_args();b=json.load(open(a.bindings));r=json.load(open(a.recipes)); rec={x['cpv']:x for x in r['packages']}; by=collections.defaultdict(list)
 for x in b.get('records', b.get('packages', [])):
  if x['lane'].startswith('pgo-') and rec.get(x['cpv'],{}).get('recipes'):
   by[x['lane']].append({'cpv':x['cpv'],'lane':x['lane'],'compiler_sha256':x['compiler_sha256'],'profile_path':x['profile_path'],'recipes':rec[x['cpv']]['recipes']})
 wave=[]
 for lane in sorted(by):wave.extend(sorted(by[lane],key=lambda x:x['cpv'])[:a.per_lane])
 out={'record_type':'profile-generation-wave-plan','schema_version':1,'source_bindings':b['sha256'],'source_recipes':r['sha256'],'per_lane':a.per_lane,'packages':wave,'state':'planned-not-authorized'};out['counts']=dict(collections.Counter(x['lane'] for x in wave));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'],len(wave))
if __name__=='__main__':main()

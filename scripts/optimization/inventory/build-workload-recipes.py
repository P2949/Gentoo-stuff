#!/usr/bin/env python3
import argparse,json,hashlib,collections,os,re
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();m=json.load(open(a.manifest)); rows=[]
 for x in m['packages']:
  recipes=[]
  for e in x['entrypoints']:
   p=e['path']; safe=p.startswith(('/usr/bin/','/usr/sbin/','/bin/','/sbin/')) and not os.path.islink(p)
   recipes.append({'path':p,'build_id':e['build_id'],'argv':[p,'--help'],'cwd':'/','environment':{'LC_ALL':'C','LANG':'C'},'safe_path':safe,'execution_state':'not-run'})
  rows.append({'cpv':x['cpv'],'lane':x['lane'],'recipes':recipes,'state':'recipe-ready' if recipes else 'no-runnable-entrypoint'})
 out={'record_type':'representative-workload-recipes','schema_version':1,'source_manifest':m['sha256'],'packages':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['recipe_count']=sum(len(x['recipes']) for x in rows);out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'],out['recipe_count'])
if __name__=='__main__':main()

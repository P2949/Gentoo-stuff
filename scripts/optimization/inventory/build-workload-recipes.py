#!/usr/bin/env python3
import argparse,json,hashlib,collections,os,re
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();m=json.load(open(a.manifest)); rows=[]
 for x in m['packages']:
  if x['lane']=='pgo-go':
   # Go PGO requires a sampling/pprof-producing workload.  A generic
   # --help invocation is only a smoke test and cannot create default.pgo.
   rows.append({'cpv':x['cpv'],'lane':x['lane'],'recipes':[],'state':'no-profile-producing-workload','reason':'generic entrypoint smoke tests cannot collect Go pprof data'})
   continue
  recipes=[]
  for e in x['entrypoints']:
   p=e['path']; safe=p.startswith(('/usr/bin/','/usr/sbin/','/bin/','/sbin/')) and not os.path.islink(p)
   # Recovery-only helpers require an input archive and are not standalone
   # representative workloads. Prefer the package's normal compressor entry
   # point when both are present.
   if os.path.basename(p) in {'bzip2recover', 'bzip2-reference'}:
    continue
   # doas has no --help mode: it treats the option as a command-line error.
   # -L is its documented, non-destructive diagnostic action and succeeds
   # without requiring a policy file or a child command.
   if p == '/usr/bin/doas':
    argv=[p,'-L']; allow_empty_output=True
   else:
    argv=[p,'--help']; allow_empty_output=False
   if safe:
    recipes.append({'path':p,'build_id':e['build_id'],'argv':argv,'cwd':'/','environment':{'LC_ALL':'C','LANG':'C'},'safe_path':True,'allow_empty_output':allow_empty_output,'execution_state':'not-run'})
  rows.append({'cpv':x['cpv'],'lane':x['lane'],'recipes':recipes,'state':'recipe-ready' if recipes else 'no-runnable-entrypoint'})
 out={'record_type':'representative-workload-recipes','schema_version':1,'source_manifest':m['sha256'],'packages':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['recipe_count']=sum(len(x['recipes']) for x in rows);out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'],out['recipe_count'])
if __name__=='__main__':main()

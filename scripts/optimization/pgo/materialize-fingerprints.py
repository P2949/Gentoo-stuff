#!/usr/bin/env python3
"""Run the authoritative profile-identity fingerprint validator over inputs."""
import argparse, concurrent.futures, json, pathlib, subprocess

def one(record, root):
    d = root / record['cpv'].replace('/', '_'); d.mkdir(parents=True, exist_ok=True)
    inp=d/'input.json'; inp.write_text(json.dumps(record['input'], sort_keys=True))
    r=subprocess.run(['python3','scripts/optimization/pgo/profile-identity.py','fingerprint','--input',str(inp),'--metadata-out',str(d/'identity.json'),'--key-out',str(d/'fingerprint.env')], text=True, capture_output=True)
    return {'cpv':record['cpv'],'returncode':r.returncode,'stdout':r.stdout.strip(),'stderr':r.stderr.strip()}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--inputs',required=True); ap.add_argument('--output-root',required=True); ap.add_argument('--workers',type=int,default=4); ap.add_argument('--result',required=True); a=ap.parse_args()
    records=json.load(open(a.inputs))['records']; root=pathlib.Path(a.output_root); root.mkdir(parents=True,exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as ex: results=list(ex.map(lambda x:one(x,root),records))
    out={'record_type':'vdb-fingerprint-materialization','schema_version':1,'results':results,'total':len(results),'successes':sum(x['returncode']==0 for x in results),'failures':sum(x['returncode']!=0 for x in results)}
    json.dump(out,open(a.result,'w'),sort_keys=True,indent=2); open(a.result,'a').write('\n')
    print(out['successes'],out['failures'])
    if out['failures']: raise SystemExit(1)
if __name__=='__main__': main()

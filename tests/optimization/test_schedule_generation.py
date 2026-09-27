import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).parents[2]
def main():
 with tempfile.TemporaryDirectory() as td:
  p=Path(td); s=p/'s'; s.write_text(json.dumps({'generation_id':'g1','inventory_id':'i1','inventory_sha256':'a'*64,'records':[{'cpv':'app/a-1','lane':'pgo-clang-ir','state':'pending'},{'cpv':'app/b-1','lane':'pgo-gcc','state':'pending'},{'cpv':'app/c-1','lane':'pgo-clang-ir','state':'pending'}]})); attempts=p/'attempts'; attempts.mkdir(); gen={'generation_id':'g1','inventory_id':'i1','inventory_sha256':'a'*64}; (attempts/'a.json').write_text(json.dumps({'cpv':'app/a-1','generation':gen,'state':'succeeded'})); (attempts/'b.json').write_text(json.dumps({'cpv':'app/b-1','generation':gen,'state':'failed-awaiting-remediation'})); (attempts/'old.json').write_text(json.dumps({'cpv':'app/c-1','generation':{'generation_id':'old','inventory_id':'i1','inventory_sha256':'a'*64},'state':'succeeded'})); out=p/'wave.json'
  common=['--storage-path',str(p),'--storage-minimum-bytes','0','--storage-minimum-percent','0','--inventory-id','i1','--inventory-sha256','a'*64]
  subprocess.run(['python3',str(ROOT/'scripts/optimization/pgo/schedule-generation.py'),'--package-state',str(s),'--attempts',str(attempts),'--output',str(out),'--generation-id','g1','--wave-size','1',*common],check=True)
  wave=json.loads(out.read_text()); assert [x['cpv'] for x in wave['packages']]==['app/c-1']; assert wave['remaining_pending']==0; assert wave['failed_preserved']==['app/b-1']; assert wave['schema_version']==4
  b=p/'b.json'; b.write_text(json.dumps({'records':[{'cpv':'app/c-1','lane':'pgo-clang-ir','profile_path':'/profiles/c','compiler_sha256':'c'*64,'identity_sha256':'d'*64}]}))
  r=p/'r.json'; r.write_text(json.dumps({'packages':[{'cpv':'app/c-1','state':'direct-training-ready','recipes':[{'recipe_id':'c-help'}]}]}))
  out2=p/'wave2.json'
  subprocess.run(['python3',str(ROOT/'scripts/optimization/pgo/schedule-generation.py'),'--package-state',str(s),'--attempts',str(attempts),'--output',str(out2),'--generation-id','g1','--wave-size','1','--bindings',str(b),'--recipes',str(r),*common],check=True)
  enriched=json.loads(out2.read_text())['packages'][0]; assert enriched['profile_path']=='/profiles/c'; assert enriched['recipes'][0]['recipe_id']=='c-help'; assert enriched['identity_sha256']=='d'*64
  refused=p/'refused.json'
  blocked=subprocess.run(['python3',str(ROOT/'scripts/optimization/pgo/schedule-generation.py'),'--package-state',str(s),'--attempts',str(attempts),'--output',str(refused),'--generation-id','g1','--inventory-id','i1','--inventory-sha256','a'*64,'--storage-minimum-bytes',str(10**18)],capture_output=True,text=True)
  assert blocked.returncode != 0 and 'storage free-space floor' in (blocked.stdout + blocked.stderr)
 print('PASS: generation scheduler resumes, preserves attempts, and carries workload bindings')
if __name__=='__main__': main()

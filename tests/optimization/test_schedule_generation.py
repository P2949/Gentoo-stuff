import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).parents[2]
def main():
 with tempfile.TemporaryDirectory() as td:
  p=Path(td); s=p/'s'; s.write_text(json.dumps({'generation_id':'g1','records':[{'cpv':'app/a-1','lane':'pgo-clang-ir','state':'pending'},{'cpv':'app/b-1','lane':'pgo-gcc','state':'pending'},{'cpv':'app/c-1','lane':'pgo-clang-ir','state':'pending'}]})); attempts=p/'attempts'; attempts.mkdir(); (attempts/'a.json').write_text(json.dumps({'cpv':'app/a-1','generation_id':'g1','state':'succeeded'})); (attempts/'b.json').write_text(json.dumps({'cpv':'app/b-1','generation_id':'g1','state':'failed-awaiting-remediation'})); (attempts/'old.json').write_text(json.dumps({'cpv':'app/c-1','generation_id':'old','state':'succeeded'})); out=p/'wave.json'
  subprocess.run(['python3',str(ROOT/'scripts/optimization/pgo/schedule-generation.py'),'--package-state',str(s),'--attempts',str(attempts),'--output',str(out),'--generation-id','g1','--wave-size','1'],check=True)
  wave=json.loads(out.read_text()); assert [x['cpv'] for x in wave['packages']]==['app/c-1']; assert wave['remaining_pending']==0; assert wave['failed_preserved']==['app/b-1']; assert wave['schema_version']==2
  b=p/'b.json'; b.write_text(json.dumps({'records':[{'cpv':'app/c-1','lane':'pgo-clang-ir','profile_path':'/profiles/c','compiler_sha256':'c'*64}]}))
  r=p/'r.json'; r.write_text(json.dumps({'packages':[{'cpv':'app/c-1','state':'smoke-ready','recipes':[{'recipe_id':'c-help'}]}]}))
  out2=p/'wave2.json'
  subprocess.run(['python3',str(ROOT/'scripts/optimization/pgo/schedule-generation.py'),'--package-state',str(s),'--attempts',str(attempts),'--output',str(out2),'--generation-id','g1','--wave-size','1','--bindings',str(b),'--recipes',str(r)],check=True)
  enriched=json.loads(out2.read_text())['packages'][0]; assert enriched['profile_path']=='/profiles/c'; assert enriched['recipes'][0]['recipe_id']=='c-help'
 print('PASS: generation scheduler resumes, preserves attempts, and carries workload bindings')
if __name__=='__main__': main()

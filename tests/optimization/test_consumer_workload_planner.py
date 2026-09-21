import json, subprocess, tempfile
from pathlib import Path
SCRIPT=Path(__file__).parents[2]/'scripts/optimization/pgo/plan-consumer-workloads.py'
def main():
 with tempfile.TemporaryDirectory() as d:
  p=Path(d)
  (p/'w').write_text(json.dumps({'packages':[{'cpv':'dev-libs/a-1','lane':'pgo-clang-ir','state':'no-runnable-entrypoint'}],'sha256':'w'}))
  (p/'e').write_text(json.dumps({'artifacts':[],'sha256':'e'}))
  edge={'provider_cpv':'dev-libs/a-1','consumer_cpv':'app/x-1','relationship':'elf-needed'}
  (p/'r').write_text(json.dumps({'records':[edge],'sha256':'r'})); out=p/'o'
  subprocess.run(['python3',str(SCRIPT),'--workloads',str(p/'w'),'--elf',str(p/'e'),'--reverse-dependencies',str(p/'r'),'--output',str(out)],check=True)
  assert json.loads(out.read_text())['records'][0]['state']=='needs-consumer-workload'
  edge['workload']={'executable':'/usr/bin/x','recipe':['x','--self-test'],'expected_provider_artifacts':['/usr/lib/liba.so'],'counter_proof':'receipt.json'}
  (p/'r').write_text(json.dumps({'records':[edge],'sha256':'r'})); subprocess.run(['python3',str(SCRIPT),'--workloads',str(p/'w'),'--elf',str(p/'e'),'--reverse-dependencies',str(p/'r'),'--output',str(out)],check=True)
  assert json.loads(out.read_text())['records'][0]['state']=='consumer-workload-ready'
 print('PASS: consumer readiness requires representative workload binding')
if __name__=='__main__':main()

import json, subprocess, tempfile
from pathlib import Path
SCRIPT=Path(__file__).parents[2]/'scripts/optimization/pgo/plan-consumer-workloads.py'
GRAPH=Path(__file__).parents[2]/'scripts/optimization/pgo/generate-reverse-dependencies.py'
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
  (p/'r').write_text(json.dumps({'records':[edge],'sha256':'r'})); out2=p/'o2'; subprocess.run(['python3',str(SCRIPT),'--workloads',str(p/'w'),'--elf',str(p/'e'),'--reverse-dependencies',str(p/'r'),'--output',str(out2)],check=True)
  assert json.loads(out2.read_text())['records'][0]['state']=='consumer-workload-ready'
  # Integration regression: the canonical graph generator must preserve the
  # authenticated binding before the planner consumes the graph.
  build_edge={'provider_cpv':'dev-libs/a-1','consumer_cpv':'dev-util/build-1','relationship':'portage-build'}
  # The canonical generator requires both authorities to be nonempty.  Keep
  # the authenticated workload binding in the ELF edge as well as Portage's
  # runtime/build evidence so this fixture exercises the real contract.
  elf_edge=dict(edge)
  portage_edge=dict(edge); portage_edge.pop('relationship', None)
  (p/'portage').write_text(json.dumps({'records':[portage_edge],'build_records':[build_edge]})); (p/'elfsrc').write_text(json.dumps({'records':[elf_edge]})); graph=p/'graph'
  subprocess.run(['python3',str(GRAPH),'--portage',str(p/'portage'),'--elf',str(p/'elfsrc'),'--output',str(graph)],check=True)
  generated=json.loads(graph.read_text()); assert generated['records'][0]['workload']['counter_proof']=='receipt.json'
  assert any(r['relationship']=='portage-build' for r in generated['records'])
  out3=p/'o3'; subprocess.run(['python3',str(SCRIPT),'--workloads',str(p/'w'),'--elf',str(p/'e'),'--reverse-dependencies',str(graph),'--output',str(out3)],check=True)
  assert json.loads(out3.read_text())['records'][0]['state']=='consumer-workload-ready'
  duplicate=p/'duplicate-reverse.json'; duplicate.write_text(json.dumps({'records':[edge,dict(edge)]}))
  duplicate_result=subprocess.run(['python3',str(SCRIPT),'--workloads',str(p/'w'),'--elf',str(p/'e'),'--reverse-dependencies',str(duplicate),'--output',str(p/'duplicate-plan')],capture_output=True,text=True)
  assert duplicate_result.returncode != 0 and 'duplicate reverse-dependency edge' in (duplicate_result.stdout+duplicate_result.stderr)
 print('PASS: consumer readiness requires representative workload binding')
if __name__=='__main__':main()

import json, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[2]
def main():
 with tempfile.TemporaryDirectory() as td:
  p=Path(td); m=p/'m'; l=p/'l'; o=p/'sets'
  m.write_text(json.dumps({'records':[{'cpv':'app/a-1','decision':'userspace'},{'cpv':'sys-kernel/k-1','decision':'kernel-policy-exclusion'}]}))
  l.write_text(json.dumps({'packages':[{'cpv':'app/a-1','lane':'pgo-clang-ir'},{'cpv':'sys-kernel/k-1','lane':'not-applicable'}]}))
  subprocess.run(['python3',str(ROOT/'scripts/optimization/inventory/generate-optimization-sets.py'),'--mutation-policy',str(m),'--lanes',str(l),'--output-root',str(o)],check=True)
  assert (o/'pgo-clang-ir').read_text()=='app/a\n'
  assert (o/'optimization-kernel-policy-exclusion').read_text()=='sys-kernel/k\n'
  assert not (o/'manifest.json').exists()
  assert (p/'sets.manifest.json').exists()
  second=subprocess.run(['python3',str(ROOT/'scripts/optimization/inventory/generate-optimization-sets.py'),'--mutation-policy',str(m),'--lanes',str(l),'--output-root',str(p/'sets2'),'--manifest',str(p/'immutable.json')],check=True)
  duplicate=p/'duplicate.json'; duplicate.write_text(json.dumps({'records':[{'cpv':'app/a-1','decision':'userspace'},{'cpv':'app/a-1','decision':'userspace'}]}))
  refused=subprocess.run(['python3',str(ROOT/'scripts/optimization/inventory/generate-optimization-sets.py'),'--mutation-policy',str(duplicate),'--lanes',str(l),'--output-root',str(p/'sets3')],capture_output=True,text=True)
  assert refused.returncode != 0 and 'duplicate CPV in mutation policy' in (refused.stdout+refused.stderr)
  conflict_m=p/'conflict-m.json'; conflict_l=p/'conflict-l.json'
  conflict_m.write_text(json.dumps({'records':[{'cpv':'app/foo-1','decision':'userspace'},{'cpv':'app/foo-2','decision':'kernel-policy-exclusion'}]}))
  conflict_l.write_text(json.dumps({'packages':[{'cpv':'app/foo-1','lane':'pgo-clang-ir'},{'cpv':'app/foo-2','lane':'not-applicable'}]}))
  conflict=subprocess.run(['python3',str(ROOT/'scripts/optimization/inventory/generate-optimization-sets.py'),'--mutation-policy',str(conflict_m),'--lanes',str(conflict_l),'--output-root',str(p/'conflict-sets')],capture_output=True,text=True)
  assert conflict.returncode == 0
  assert (p/'conflict-sets'/'pgo-clang-ir').read_text() == '=app/foo-1\n'
  verifier=subprocess.run(['python3',str(ROOT/'scripts/optimization/inventory/verify-optimization-sets.py'),'--mutation-policy',str(conflict_m),'--lanes',str(conflict_l),'--manifest',str(p/'conflict-sets.manifest.json'),'--sets-root',str(p/'conflict-sets')],capture_output=True,text=True)
  assert verifier.returncode == 0 and 'verified 2 CPVs' in verifier.stdout
 print('PASS: optimization sets derive from canonical mutation policy')
if __name__=='__main__': main()

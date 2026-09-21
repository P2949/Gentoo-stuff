import json, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[2]
def main():
 with tempfile.TemporaryDirectory() as td:
  p=Path(td); m=p/'m'; l=p/'l'; o=p/'sets'
  m.write_text(json.dumps({'records':[{'cpv':'app/a-1','decision':'userspace'},{'cpv':'sys-kernel/k-1','decision':'kernel-policy-exclusion'}]}))
  l.write_text(json.dumps({'packages':[{'cpv':'app/a-1','lane':'pgo-clang-ir'},{'cpv':'sys-kernel/k-1','lane':'not-applicable'}]}))
  subprocess.run(['python3',str(ROOT/'scripts/optimization/inventory/generate-optimization-sets.py'),'--mutation-policy',str(m),'--lanes',str(l),'--output-root',str(o)],check=True)
  assert (o/'pgo-clang-ir').read_text()=='app/a-1\n'
  assert (o/'optimization-kernel-policy-exclusion').read_text()=='sys-kernel/k-1\n'
 print('PASS: optimization sets derive from canonical mutation policy')
if __name__=='__main__': main()

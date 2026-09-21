import json, subprocess, tempfile
from pathlib import Path
S=Path(__file__).parents[2]/'scripts/optimization/inventory/classify-kernel-policy.py'
def main():
 with tempfile.TemporaryDirectory() as d:
  p=Path(d); v=p/'vdb'/'app'/'foo-1';v.mkdir(parents=True);(v/'CONTENTS').write_text('obj /usr/bin/foo 1\n')
  m=p/'m';m.write_text(json.dumps({'packages':[{'cpv':'app/foo-1'}]}));o=p/'o'
  subprocess.run(['python3',str(S),'--manifest',str(m),'--vdb',str(p/'vdb'),'--ebuild-root',str(p/'repos'),'--output',str(o)],check=True)
  assert json.loads(o.read_text())['records'][0]['state']=='pending-lifecycle-review'
  (v/'CONTENTS').write_text('obj /boot/vmlinuz-test 1\n')
  o2=p/'o2'; subprocess.run(['python3',str(S),'--manifest',str(m),'--vdb',str(p/'vdb'),'--ebuild-root',str(p/'repos'),'--output',str(o2)],check=True)
  assert json.loads(o2.read_text())['records'][0]['state']=='kernel-policy-exclusion'
 print('PASS: kernel policy uses transaction evidence rather than category')
if __name__=='__main__':main()

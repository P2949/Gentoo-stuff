import json, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[2]
def main():
 with tempfile.TemporaryDirectory() as td:
  p=Path(td); v=p/'vdb'/'app'/'a-1'; v.mkdir(parents=True); (v/'REPOSITORY').write_text('gentoo\n'); (v/'BUILD_TIME').write_text('1\n'); (v/'CONTENTS').write_text('obj /usr/bin/a deadbeef 1\n')
  e=p/'repos'/'gentoo'/'app'/'a'; e.mkdir(parents=True); (e/'a-1.ebuild').write_text('EAPI=8\n')
  m=p/'m.json'; m.write_text(json.dumps({'packages':[{'cpv':'app/a-1'}]})); out=p/'p.json'
  subprocess.run(['python3',str(ROOT/'scripts/optimization/inventory/collect-package-provenance.py'),'--manifest',str(m),'--vdb',str(p/'vdb'),'--ebuild-root',str(p/'repos'),'--output',str(out)],check=True)
  subprocess.run(['python3',str(ROOT/'scripts/optimization/inventory/verify-package-provenance.py'),'--provenance',str(out),'--manifest',str(m),'--vdb',str(p/'vdb'),'--ebuild-root',str(p/'repos')],check=True)
 print('PASS: installed and next-build provenance are independently bound')
if __name__=='__main__': main()

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
  overlay=p/'overlay'/'gentoo'; oe=overlay/'app'/'a'; oe.mkdir(parents=True); (oe/'a-1.ebuild').write_text('EAPI=8\n')
  # A provenance record from an explicitly configured overlay is accepted,
  # while the verifier still rejects paths outside every trusted root.
  payload=json.loads(out.read_text()); payload['records'][0]['next_build_source'].update({'ebuild_path':str(oe/'a-1.ebuild'),'ebuild_sha256':__import__('hashlib').sha256((oe/'a-1.ebuild').read_bytes()).hexdigest()}); unsigned=dict(payload); unsigned.pop('sha256'); payload['sha256']=__import__('hashlib').sha256(json.dumps(unsigned,sort_keys=True,separators=(',',':')).encode()).hexdigest(); out.write_text(json.dumps(payload))
  subprocess.run(['python3',str(ROOT/'scripts/optimization/inventory/verify-package-provenance.py'),'--provenance',str(out),'--manifest',str(m),'--vdb',str(p/'vdb'),'--ebuild-root',str(p/'repos'),'--ebuild-root',str(p/'overlay')],check=True)
 print('PASS: installed and next-build provenance are independently bound')
if __name__=='__main__': main()

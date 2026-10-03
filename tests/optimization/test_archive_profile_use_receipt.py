#!/usr/bin/env python3
import hashlib, json, subprocess, tempfile
from pathlib import Path
SCRIPT=Path(__file__).parents[2]/'scripts/optimization/pgo/archive-profile-use-receipt.py'
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
  with tempfile.TemporaryDirectory() as td:
    root=Path(td); files={k:root/k for k in ('ebuild','dispatcher','dispatcher_env','manifest','metadata','profile','log','contents','environment')}
    for p in files.values(): p.write_bytes(p.name.encode())
    def item(p): return {'path':str(p),'sha256':digest(p)}
    receipt={'schema_version':2,'mode':'profile-use','exit_status':0,'cpv':'dev-libs/foo-1.0','repository':'gentoo'}
    for k in ('ebuild','dispatcher','dispatcher_env','manifest','metadata','profile','log'): receipt[k]=item(files[k])
    receipt['post_vdb']={'contents':item(files['contents']),'environment':item(files['environment'])}
    rp=root/'receipt.json'; rp.write_text(json.dumps(receipt)); out=root/'archive.json'; archive=root/'archive'
    subprocess.run(['python3',str(SCRIPT),'--receipt',str(rp),'--archive-root',str(archive),'--output',str(out)],check=True)
    doc=json.loads(out.read_text()); assert len(doc['objects']) == 9
    assert all(Path(x['archive_path']).is_file() for x in doc['objects'])
    verifier=SCRIPT.with_name('verify-profile-use-archive.py')
    subprocess.run(['python3',str(verifier),'--archive',str(out),'--receipt',str(rp)],check=True)
    tampered=json.loads(out.read_text()); tampered['objects'][0]['size'] += 1
    (root/'bad.json').write_text(json.dumps(tampered))
    assert subprocess.run(['python3',str(verifier),'--archive',str(root/'bad.json'),'--receipt',str(rp)]).returncode != 0
    assert subprocess.run(['python3',str(SCRIPT),'--receipt',str(rp),'--archive-root',str(archive),'--output',str(out)]).returncode != 0
  print('PASS: profile-use receipt archival is content-addressed and immutable')
if __name__=='__main__': main()

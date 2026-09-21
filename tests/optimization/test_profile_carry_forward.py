#!/usr/bin/env python3
import json, subprocess, tempfile
from pathlib import Path
SCRIPT=Path(__file__).parents[2]/'scripts/optimization/pgo/carry-forward-profiles.py'
BASE={
 'source_cpv':'dev-libs/foo-1.0','target_cpv':'dev-libs/foo-1.0','repository':'gentoo','ebuild_sha256':'a'*64,
 'package_env_content':[{'path':'default.conf','sha256':'b'*64}], 'build_controls':{'extra_econf':'','extra_emeson':'','extra_ecmake':''},
 'compiler':{'family':'clang','major':18},'abi':'x86-64','target_triple':'x86_64-pc-linux-gnu','optimization_flags':['-O2'],
 'workload_revision':'workload-1','training_receipt':{'sha256':'c'*64},'merge_evidence':{'sha256':'d'*64},'profile_sha256':'e'*64}
def run(src,dst):
 with tempfile.TemporaryDirectory() as td:
  p=Path(td); (p/'s.json').write_text(json.dumps(src)); (p/'t.json').write_text(json.dumps(dst)); out=p/'o.json'
  subprocess.run(['python3',str(SCRIPT),'--source-generation','g1','--target-generation','g2','--source-record',str(p/'s.json'),'--target-record',str(p/'t.json'),'--output',str(out)],check=True)
  return json.loads(out.read_text())
def main():
 assert run(BASE,BASE)['decision']=='carry-forward'
 changed=dict(BASE); changed['target_cpv']='dev-libs/foo-1.1'
 assert run(BASE,changed)['decision']=='retrain'
 changed=dict(BASE); changed['repository']='codex-local'
 assert run(BASE,changed)['decision']=='retrain'
 print('PASS: profile carry-forward requires exact identity equality')
if __name__=='__main__': main()

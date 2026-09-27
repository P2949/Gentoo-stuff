import subprocess,sys,tempfile,json
from pathlib import Path
ROOT=Path(__file__).parents[2]; TOOL=ROOT/'scripts/optimization/storage/prune-portage-build-trees.py'
with tempfile.TemporaryDirectory() as t:
 b=Path(t)/'build'; r=Path(t)/'reports'; q=Path(t)/'receipt.json'; (b/'cat/pkg/temp').mkdir(parents=True); (b/'cat/pkg/temp/build.log').write_text('log')
 subprocess.run([sys.executable,str(TOOL),'--root',str(b),'--reports',str(r),'--receipt',str(q)],check=True); assert b.exists()
 subprocess.run([sys.executable,str(TOOL),'--root',str(b),'--reports',str(r),'--receipt',str(q),'--execute'],check=True); assert not b.exists(); assert (r/'portage-build-logs/cat/pkg/temp/build.log').exists(); assert json.loads(q.read_text())['moved_logs']==1

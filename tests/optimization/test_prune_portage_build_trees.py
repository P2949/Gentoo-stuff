import subprocess,sys,tempfile,json,unittest
from pathlib import Path
ROOT=Path(__file__).parents[2]; TOOL=ROOT/'scripts/optimization/storage/prune-portage-build-trees.py'

class PortageBuildTreeRetirementTests(unittest.TestCase):
 def test_dry_run_and_execute(self):
  with tempfile.TemporaryDirectory() as t:
   b=Path(t)/'build'; r=Path(t)/'reports'; q=Path(t)/'receipt.json'; (b/'cat/pkg/temp').mkdir(parents=True); (b/'cat/pkg/temp/build.log').write_text('log')
   subprocess.run([sys.executable,str(TOOL),'--root',str(b),'--reports',str(r),'--receipt',str(q)],check=True,stdout=subprocess.DEVNULL); self.assertTrue(b.exists())
   subprocess.run([sys.executable,str(TOOL),'--root',str(b),'--reports',str(r),'--receipt',str(q),'--execute'],check=True,stdout=subprocess.DEVNULL); self.assertFalse(b.exists()); self.assertTrue((r/'portage-build-logs/cat/pkg/temp/build.log').exists()); self.assertEqual(json.loads(q.read_text())['moved_logs'],1)

if __name__ == '__main__':
 unittest.main()

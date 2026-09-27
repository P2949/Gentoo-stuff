import json, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[2]; TOOL=ROOT/'scripts/optimization/storage/prune-regenerable-caches.py'
def test_dry_run_and_bounded_execute():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'cache'; root.mkdir(); (root/'old').write_bytes(b'a'*100); (root/'new').write_bytes(b'b'*100)
        rec=Path(d)/'r.json'; subprocess.run(['python3',str(TOOL),'--root',str(root),'--max-bytes','100','--receipt',str(rec),'--execute'],check=True)
        row=json.loads(rec.read_text())['rows'][0]; assert row['after_bytes']<=100; assert row['removed_files']>=1

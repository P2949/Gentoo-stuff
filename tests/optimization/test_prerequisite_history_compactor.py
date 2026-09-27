import json
import subprocess
import sys
import tempfile
import fcntl
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
TOOL = ROOT / "scripts/optimization/storage/compact-prerequisite-history.py"

class PrerequisiteHistoryCompactorTests(unittest.TestCase):
    def test_dry_run_execute_and_lock_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "transactions"; base.mkdir()
            terminal = base / "done"; terminal.mkdir()
            (terminal / "terminal.json").write_text('{"state":"completed"}\n', encoding="utf-8")
            (terminal / "tmp").mkdir(); (terminal / "tmp" / "build.log").write_text("log", encoding="utf-8")
            active = base / "active"; active.mkdir(); (active / "tmp").mkdir(); (active / "tmp" / "keep").write_text("x", encoding="utf-8")
            receipt = Path(tmp) / "retirement.json"
            result = subprocess.run([sys.executable, str(TOOL), "--transactions", str(base), "--receipt", str(receipt)], check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout)["mode"], "dry-run")
            self.assertTrue((terminal / "tmp").exists())
            subprocess.run([sys.executable, str(TOOL), "--transactions", str(base), "--receipt", str(receipt), "--execute"], check=True, stdout=subprocess.DEVNULL)
            self.assertFalse((terminal / "tmp").exists())
            self.assertTrue((active / "tmp").exists())
            prepared = Path(str(receipt) + ".prepared.json")
            self.assertTrue(prepared.exists())
            prepared_data = json.loads(prepared.read_text(encoding="utf-8"))
            self.assertEqual(prepared_data["schema"], "gentoo-optimization-prerequisite-retirement-prepared-v1")
            self.assertEqual(prepared_data["transaction_id"], "done")
            project_lock = Path(tmp) / "project.lock"; generation_lock = Path(tmp) / "generation.lock"
            with project_lock.open("a+") as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX)
                refused = subprocess.run([sys.executable, str(TOOL), "--transactions", str(base), "--receipt", str(Path(tmp) / "locked.json"), "--execute", "--project-lock", str(project_lock), "--generation-lock", str(generation_lock)], capture_output=True, text=True)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("active storage lock", refused.stderr)


if __name__ == "__main__":
    unittest.main()

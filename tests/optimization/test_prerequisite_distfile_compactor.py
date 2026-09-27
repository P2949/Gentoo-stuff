import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
TOOL = ROOT / "scripts/optimization/storage/compact-prerequisite-distfiles.py"

class PrerequisiteDistfileCompactorTest(unittest.TestCase):
  def test_execute(self):
   with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp); txroot = base / "tx"; state = base / "state"; objects = base / "objects"; authorities = base / "authorities"
    txroot.mkdir(); state.mkdir(); authorities.mkdir()
    tx = txroot / "done"; (tx / "distfiles.staging").mkdir(parents=True)
    (tx / "distfiles.staging" / "source.tar.xz").write_bytes(b"same")
    (authorities / "done" / "distfiles").mkdir(parents=True)
    (authorities / "done" / "distfiles" / "source.tar.xz").write_bytes(b"same")
    (state / "jsonschema-prerequisite-done.success.json").write_text('{"phase":"success"}\n', encoding="utf-8")
    receipt = base / "receipt.json"
    subprocess.run([sys.executable, str(TOOL), "--transactions", str(txroot), "--state-dir", str(state), "--objects", str(objects), "--authorities", str(authorities), "--receipt", str(receipt), "--execute"], check=True)
    self.assertFalse((tx / "distfiles.staging" / "source.tar.xz").exists())
    self.assertEqual(len(list(objects.rglob("*"))), 2)
    self.assertFalse((authorities / "done" / "distfiles").exists())
    self.assertTrue(json.loads(receipt.read_text())['retired'][0]['sha256'])


if __name__ == "__main__":
    unittest.main()

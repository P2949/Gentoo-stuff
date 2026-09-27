#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
GC = ROOT / "scripts/optimization/storage/gc-storage.py"

class StorageGCTest(unittest.TestCase):
    def test_retention_and_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); candidate = root / "old"; unknown = root / "unknown"
            candidate.mkdir(); unknown.mkdir(); retention = root / "retention.json"; receipt = root / "receipt.json"
            retention.write_text(json.dumps({"schema": "storage-retention-set-v1", "objects": [
                {"path": str(candidate), "state": "ARCHIVE_CANDIDATE"}, {"path": str(unknown), "state": "UNKNOWN"}]}), encoding="utf-8")
            subprocess.run([sys.executable, str(GC), "--retention", str(retention), "--receipt", str(receipt),
                            "--project-lock", str(root / "project.lock"), "--generation-lock", str(root / "generation.lock")], check=True, stdout=subprocess.DEVNULL)
            self.assertTrue(candidate.exists() and unknown.exists()); self.assertEqual(json.loads(receipt.read_text())["mode"], "dry-run")
            execute_receipt = root / "execute-receipt.json"
            subprocess.run([sys.executable, str(GC), "--retention", str(retention), "--receipt", str(execute_receipt), "--execute",
                            "--project-lock", str(root / "project.lock"), "--generation-lock", str(root / "generation.lock"),
                            "--measurement-root", str(root), "--quarantine-root", str(root / "quarantine")], check=True, stdout=subprocess.DEVNULL)
            executed = json.loads(execute_receipt.read_text()); self.assertEqual(executed["deleted"], [str(candidate)])
            self.assertFalse(candidate.exists()); self.assertTrue(unknown.exists())


if __name__ == "__main__":
    unittest.main()

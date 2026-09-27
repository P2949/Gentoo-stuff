#!/usr/bin/env python3
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts/optimization/storage/compact-checkpoints.py"


class CheckpointCompactorTests(unittest.TestCase):
    def test_dry_run_protects_selector_and_retains_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "cache"
            durable = root / "durable"
            (cache / "snapshot-unknown").mkdir(parents=True)
            target = durable / "critical-active"
            target.mkdir(parents=True)
            old = durable / "critical-old"
            old.mkdir(parents=True)
            (old / "Packages").write_text("cat/pkg", encoding="utf-8")
            (old / "checkpoint-state.json").write_text(
                '{"terminal_state":"offline-restore-proven"}\n', encoding="utf-8"
            )
            (cache / "critical-current").symlink_to(target)
            receipt = root / "receipt.json"
            subprocess.run(
                ["python3", str(SCRIPT), "--cache-root", str(cache),
                 "--durable-root", str(durable), "--reference-root", str(root),
                 "--receipt", str(receipt)], check=True, capture_output=True, text=True
            )
            report = json.loads(receipt.read_text(encoding="utf-8"))
            states = {Path(row["path"]).name: row["state"] for row in report["objects"]}
            self.assertEqual(states["critical-active"], "LIVE_REQUIRED")
            self.assertEqual(states["snapshot-unknown"], "UNKNOWN")
            self.assertEqual(states["critical-old"], "ARCHIVE_CANDIDATE")
            self.assertEqual(report["retired"], [])

    def test_execute_requires_terminal_state_and_only_retires_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "cache"
            durable = root / "durable"
            old = durable / "critical-old"
            old.mkdir(parents=True)
            (old / "Packages").write_text("cat/pkg", encoding="utf-8")
            (old / "checkpoint-state.json").write_text(
                '{"terminal_state":"offline-restore-proven"}\n', encoding="utf-8"
            )
            receipt = root / "receipt.json"
            subprocess.run(
                ["python3", str(SCRIPT), "--cache-root", str(cache),
                 "--durable-root", str(durable), "--reference-root", str(root),
                 "--receipt", str(receipt), "--execute"], check=True,
                capture_output=True, text=True
            )
            report = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(report["retired"], [str(old)])
            self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()

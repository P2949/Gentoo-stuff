#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("retention", ROOT / "scripts/optimization/storage/build-retention-set.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader


class StorageRetentionTests(unittest.TestCase):
    def test_classification_fixture_runs_only_when_selected(self):
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            raw = root / "raw"
            state.mkdir()
            raw.mkdir()
            active = raw / "phase3-live-current"
            old = raw / "phase3-live-old"
            unknown = raw / ".partial-checkpoint"
            active.mkdir(); old.mkdir(); unknown.mkdir()
            (state / "authority.json").write_text('{"generation":"phase3-live-old"}\n', encoding="utf-8")
            refs = module.references([state])
            self.assertIn("phase3-live-old", refs)
            self.assertEqual(module.classify(active, refs, "phase3-live-current")[0], "LIVE_REQUIRED")
            self.assertEqual(module.classify(old, refs, "phase3-live-current")[0], "EVIDENCE_KEEP")
            self.assertEqual(module.classify(unknown, refs, "phase3-live-current")[0], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()

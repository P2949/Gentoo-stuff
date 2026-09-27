import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/optimization/storage/gc-content-addressed-objects.py"

class ContentObjectReachabilityTests(unittest.TestCase):
    def test_referenced_object_is_live_unknown_name_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); objects = root / "objects"; refs = root / "refs"; objects.mkdir(); refs.mkdir()
            (objects / ("a" * 64)).write_bytes(b"live")
            (objects / "not-a-digest").write_bytes(b"unknown")
            (refs / "receipt.json").write_text(json.dumps({"sha256": "a" * 64}))
            out = root / "out.json"
            subprocess.run([sys.executable, str(SCRIPT), "--objects", str(objects),
                            "--reference-root", str(refs), "--output", str(out)], check=True,
                           stdout=subprocess.DEVNULL)
            rows = {r["sha256"]: r for r in json.loads(out.read_text())["objects"]}
            self.assertEqual(rows["a" * 64]["state"], "LIVE_REQUIRED")
            self.assertEqual(rows["not-a-digest"]["state"], "UNKNOWN")

if __name__ == "__main__": unittest.main()

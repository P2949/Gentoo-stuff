import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts/optimization/storage/build-distfile-retention-set.py"


class DistfileRetentionTests(unittest.TestCase):
    def test_manifest_match_is_evidence_and_unknown_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); dist = root / "dist"; repo = root / "repo"; dist.mkdir(); repo.mkdir()
            (dist / "known.tar.xz").write_bytes(b"known")
            (dist / "unknown.tar.xz").write_bytes(b"unknown")
            (repo / "Manifest").write_text("DIST known.tar.xz 5 deadbeef\n")
            out = root / "report.json"
            subprocess.run([sys.executable, str(SCRIPT), "--distfiles", str(dist),
                            "--manifest-root", str(repo), "--output", str(out)], check=True,
                           stdout=subprocess.DEVNULL)
            rows = {r["name"]: r for r in json.loads(out.read_text())["objects"]}
            self.assertEqual(rows["known.tar.xz"]["state"], "EVIDENCE_REQUIRED")
            self.assertEqual(rows["unknown.tar.xz"]["state"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()

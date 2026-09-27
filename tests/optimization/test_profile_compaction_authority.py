import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts/optimization/storage/build-profile-compaction-authority.py"


class ProfileCompactionAuthorityTests(unittest.TestCase):
    def test_missing_binding_keeps_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attempt = root / "attempt"
            evidence = root / "evidence"
            attempt.mkdir(); evidence.mkdir()
            (attempt / "sample.profraw").write_bytes(b"profile")
            out = root / "authority.json"
            subprocess.run([sys.executable, str(SCRIPT), "--attempt", str(attempt),
                            "--evidence-root", str(evidence), "--output", str(out)], check=True,
                           stdout=subprocess.DEVNULL)
            self.assertEqual(json.loads(out.read_text())["decision"], "KEEP_EXPANDED")

    def test_unique_valid_binding_authorizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attempt = root / "attempt"
            evidence = root / "evidence"
            attempt.mkdir(); evidence.mkdir()
            (attempt / "sample.profraw").write_bytes(b"profile")
            (evidence / "receipt.json").write_text(json.dumps({
                "status": "merged-validated", "attempt": str(attempt),
                "merged_profile_validation": {"status": "validated"}
            }))
            out = root / "authority.json"
            subprocess.run([sys.executable, str(SCRIPT), "--attempt", str(attempt),
                            "--evidence-root", str(evidence), "--output", str(out)], check=True,
                           stdout=subprocess.DEVNULL)
            self.assertEqual(json.loads(out.read_text())["decision"], "COMPACTION_AUTHORIZED")


if __name__ == "__main__":
    unittest.main()

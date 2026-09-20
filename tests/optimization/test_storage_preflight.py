import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/optimization/verify/storage-preflight.py'

class StoragePreflightTests(unittest.TestCase):
    def test_report_records_pass_and_exact_usage(self):
        with tempfile.TemporaryDirectory() as d:
            report = Path(d) / 'report.json'
            p = subprocess.run([sys.executable, str(SCRIPT), '--path', '/', '--report', str(report)], text=True, capture_output=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            data = json.loads(report.read_text())
            self.assertEqual(data['state'], 'pass')
            self.assertGreater(data['free_bytes'], 0)

    def test_insufficient_floor_fails_closed(self):
        p = subprocess.run([sys.executable, str(SCRIPT), '--path', '/', '--minimum-bytes', str(10**18)], text=True, capture_output=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('REFUSED', p.stderr)

if __name__ == '__main__':
    unittest.main()

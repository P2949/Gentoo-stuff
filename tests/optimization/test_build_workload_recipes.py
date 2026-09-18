import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/optimization/inventory/build-workload-recipes.py"


class WorkloadRecipeGenerationTest(unittest.TestCase):
    def test_cabextract_uses_successful_version_probe(self):
        manifest = {
            "sha256": "manifest-test",
            "packages": [
                {
                    "cpv": "app-arch/cabextract-9999",
                    "lane": "pgo-clang-ir",
                    "entrypoints": [
                        {"path": "/usr/bin/cabextract", "build_id": None}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            output_path = Path(directory) / "recipes.json"
            manifest_path.write_text(json.dumps(manifest))
            subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(manifest_path), "--output", str(output_path)],
                check=True,
                cwd=ROOT,
            )
            record = json.loads(output_path.read_text())["packages"][0]
        self.assertEqual(record["recipes"][0]["argv"], ["/usr/bin/cabextract", "--version"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Regression test for immutable workload recipe manifests."""
import json
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/optimization/inventory/build-workload-recipes.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manifest = root / "manifest.json"
        output = root / "recipes.json"
        manifest.write_text(json.dumps({"sha256": "m", "packages": []}))
        output.write_text("immutable\n")
        result = subprocess.run([
            "python3", str(SCRIPT), "--manifest", str(manifest), "--output", str(output),
        ], capture_output=True, text=True)
        assert result.returncode != 0
        assert "workload recipe output already exists" in (result.stdout + result.stderr)
        assert output.read_text() == "immutable\n"
    print("PASS: workload recipe manifests are published write-once")


if __name__ == "__main__":
    main()

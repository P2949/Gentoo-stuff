#!/usr/bin/env python3
"""Regression tests for exact BOLT safety producer identities."""
import json
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/optimization/inventory/review-bolt-safety.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        metadata = root / "metadata.json"
        classification = root / "classification.json"
        output = root / "output.json"
        metadata.write_text(json.dumps({"sha256": "m", "artifacts": []}))
        classification.write_text(json.dumps({"records": [
            {"owner_cpv": "cat/pkg-1", "path": "/usr/bin/a", "state": "candidate-bolt-eligible"},
            {"owner_cpv": "cat/pkg-1", "path": "/usr/bin/a", "state": "candidate-bolt-eligible"},
        ]}))
        result = subprocess.run([
            "python3", str(SCRIPT), "--metadata", str(metadata),
            "--classification", str(classification), "--output", str(output),
        ], text=True, capture_output=True)
        assert result.returncode != 0
        assert "duplicate candidate BOLT identities" in result.stderr
        assert not output.exists()
    print("PASS: BOLT safety producer rejects duplicate identities")


if __name__ == "__main__":
    main()

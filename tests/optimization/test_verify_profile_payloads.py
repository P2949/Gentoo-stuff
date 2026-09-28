#!/usr/bin/env python3
"""Regression tests for immutable profile-payload audit authorities."""
import json
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/optimization/pgo/verify-profile-payloads.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bindings = root / "bindings.json"
        exclusions = root / "exclusions.json"
        output = root / "audit.json"
        bindings.write_text(json.dumps({"sha256": "b", "records": [
            {"cpv": "cat/pkg-1", "compiler": None},
            {"cpv": "cat/pkg-1", "compiler": None},
        ]}))
        exclusions.write_text(json.dumps({"records": []}))
        result = subprocess.run([
            "python3", str(SCRIPT), "--bindings", str(bindings),
            "--exclusions", str(exclusions), "--output", str(output),
        ], capture_output=True, text=True)
        assert result.returncode != 0
        assert "duplicate CPV in profile bindings" in (result.stdout + result.stderr)
        assert not output.exists()
    print("PASS: profile-payload audit rejects duplicate authorities")


if __name__ == "__main__":
    main()

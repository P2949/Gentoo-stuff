#!/usr/bin/env python3
"""Regression test for immutable per-generation policy bindings."""
import json
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/optimization/pgo/build-policy-bindings.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        lanes = root / "lanes.json"
        compilers = root / "compilers.json"
        identities = root / "identities"
        profiles = root / "profiles"
        output = root / "bindings.json"
        identities.mkdir()
        profiles.mkdir()
        lanes.write_text(json.dumps({"packages": [{"cpv": "cat/pkg-1", "lane": "unsupported"}]}))
        compilers.write_text(json.dumps({}))
        output.write_text("immutable\n")
        result = subprocess.run([
            "python3", str(SCRIPT), "--lanes", str(lanes),
            "--identity-root", str(identities), "--compiler-identities", str(compilers),
            "--profile-root", str(profiles), "--generation-id", "g1",
            "--output", str(output),
        ], capture_output=True, text=True)
        assert result.returncode != 0
        assert "policy-binding output already exists" in (result.stdout + result.stderr)
        assert output.read_text() == "immutable\n"
    print("PASS: policy bindings are published write-once")


if __name__ == "__main__":
    main()

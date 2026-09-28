#!/usr/bin/env python3
"""Regression tests for fail-closed wave readiness workload admission."""
import json
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/optimization/pgo/verify-wave-readiness.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        wave = root / "wave.json"
        manifest = root / "manifest.json"
        identity = root / "identity.json"
        output = root / "readiness.json"
        wave.write_text(json.dumps({"sha256": "w", "generation_id": "g", "packages": [{
            "cpv": "cat/pkg-1", "lane": "pgo-clang-ir",
            "profile_path": "/var/tmp/gentoo-optimization/pgo-raw/p",
            "compiler_sha256": "c" * 64, "identity_sha256": "i" * 64,
            "recipes": [],
        }]}))
        manifest.write_text(json.dumps({"cpvs": ["cat/pkg-1"]}))
        identity.write_text(json.dumps({"clang": {"sha256": "c" * 64}}))
        subprocess.run([
            "python3", str(SCRIPT), "--wave", str(wave), "--manifest", str(manifest),
            "--identity", str(identity), "--output", str(output),
        ], check=True)
        report = json.loads(output.read_text())
        assert report["invalid_inputs"] == ["cat/pkg-1"]
        assert report["ready_count"] == 0
    print("PASS: wave readiness requires a bound training recipe")


if __name__ == "__main__":
    main()

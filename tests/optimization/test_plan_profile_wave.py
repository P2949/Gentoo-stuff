#!/usr/bin/env python3
"""Regression tests for fail-closed workload admission in wave planning."""
import json
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/optimization/pgo/plan-profile-wave.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bindings = root / "bindings.json"
        recipes = root / "recipes.json"
        output = root / "wave.json"
        bindings.write_text(json.dumps({"sha256": "b", "records": [{
            "cpv": "cat/pkg-1", "lane": "pgo-clang-ir", "compiler_sha256": "c" * 64,
            "profile_path": "/profiles/p", "identity_sha256": "i" * 64,
        }]}))
        recipes.write_text(json.dumps({"sha256": "r", "packages": [{
            "cpv": "cat/pkg-1", "state": "direct-training-ready",
            "recipes": [{"recipe_id": "r1", "argv": ["--help"]}],
        }]}))
        refused = subprocess.run([
            "python3", str(SCRIPT), "--bindings", str(bindings), "--recipes", str(recipes),
            "--output", str(output),
        ], capture_output=True, text=True)
        assert refused.returncode != 0
        assert "incomplete workload recipe" in (refused.stdout + refused.stderr)
        assert not output.exists()
        duplicate = root / "duplicate-recipes.json"
        duplicate.write_text(json.dumps({"sha256": "r", "packages": [
            {"cpv": "cat/pkg-1"}, {"cpv": "cat/pkg-1"},
        ]}))
        duplicate_result = subprocess.run([
            "python3", str(SCRIPT), "--bindings", str(bindings),
            "--recipes", str(duplicate), "--output", str(root / "duplicate-wave.json"),
        ], capture_output=True, text=True)
        assert duplicate_result.returncode != 0
        assert "duplicate CPV in recipe authority" in (duplicate_result.stdout + duplicate_result.stderr)
    print("PASS: profile wave planner refuses unbound workload recipes")


if __name__ == "__main__":
    main()

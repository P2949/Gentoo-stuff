#!/usr/bin/env python3
"""Regression tests for fail-closed representative workload coverage."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts/optimization/pgo/verify-workload-coverage.py"


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def run(recipe: dict[str, object]) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        lanes = root / "lanes.json"
        recipes = root / "recipes.json"
        exclusions = root / "exclusions.json"
        output = root / "coverage.json"
        write(lanes, {"packages": [{"cpv": "cat/pkg-1", "lane": "pgo-clang-ir"}], "sha256": "l"})
        write(recipes, {"packages": [recipe], "sha256": "r"})
        write(exclusions, {"records": [], "sha256": "e"})
        subprocess.run(
            ["python3", str(SCRIPT), "--lanes", str(lanes), "--recipes", str(recipes),
             "--exclusions", str(exclusions), "--output", str(output)],
            check=True,
        )
        return json.loads(output.read_text(encoding="utf-8"))


def main() -> None:
    invalid = run({"cpv": "cat/pkg-1", "state": "direct-training-ready", "recipes": []})
    assert invalid["coverage_pass"] is False
    assert invalid["invalid_ready"] == ["cat/pkg-1"]
    identity_only = run({
        "cpv": "cat/pkg-1",
        "state": "direct-training-ready",
        "recipes": [{"recipe_id": "r1", "argv": ["--help"]}],
    })
    assert identity_only["coverage_pass"] is False
    assert identity_only["invalid_ready"] == ["cat/pkg-1"]
    valid = run({
        "cpv": "cat/pkg-1",
        "state": "direct-training-ready",
        "purpose": "training",
        "recipes": [{"recipe_id": "r1", "path": "/usr/bin/tool", "argv": ["--help"]}],
    })
    assert valid["coverage_pass"] is True
    assert valid["representative_training_coverage_pass"] is True
    print("PASS: workload coverage requires identified recipe payload")


if __name__ == "__main__":
    main()

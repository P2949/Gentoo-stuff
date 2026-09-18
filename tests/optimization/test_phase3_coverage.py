#!/usr/bin/env python3
"""Regression tests for the Phase-3 ELF coverage authority join."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts/optimization/verify/phase3-coverage.py"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def run_case(authority: dict[str, object]) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manifest = root / "manifest.json"
        lanes = root / "lanes.json"
        elf_class = root / "elf-class.json"
        elf_safety = root / "elf-safety.json"
        authority_path = root / "authority.json"
        output = root / "coverage.json"
        write_json(manifest, {"packages": [{"cpv": "cat/pkg-1"}]})
        write_json(lanes, {"packages": [{"cpv": "cat/pkg-1"}], "counts": {}})
        write_json(
            elf_class,
            {"records": [{"owner_cpv": "cat/pkg-1", "path": "/usr/bin/tool"}]},
        )
        write_json(elf_safety, {"records": [], "counts": {}})
        write_json(authority_path, authority)
        subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--manifest",
                str(manifest),
                "--lanes",
                str(lanes),
                "--elf-class",
                str(elf_class),
                "--elf-authority",
                str(authority_path),
                "--elf-safety",
                str(elf_safety),
                "--output",
                str(output),
            ],
            check=True,
            text=True,
        )
        return json.loads(output.read_text(encoding="utf-8"))


def main() -> None:
    report = run_case(
        {
            "artifacts": [
                {
                    "owner_cpv": "cat/pkg-1",
                    "path": "/usr/bin/tool",
                    "class": "ELF64",
                    "type": "DYN",
                }
            ],
            "sha256": "fixture",
        }
    )
    assert report["elf_count"] == 1
    assert report["elf_missing_classification"] == []
    assert report["coverage_pass"] is True

    try:
        run_case({"artifacts": [{"owner_cpv": "cat/pkg-1", "path": "/usr/bin/tool"}]})
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("authority without ELF metadata must not pass vacuously")

    print("PASS: Phase-3 coverage joins the authoritative ELF census")


if __name__ == "__main__":
    main()

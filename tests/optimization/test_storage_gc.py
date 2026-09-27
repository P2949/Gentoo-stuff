#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
GC = ROOT / "scripts/optimization/storage/gc-storage.py"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    candidate = root / "old"
    unknown = root / "unknown"
    candidate.mkdir(); unknown.mkdir()
    retention = root / "retention.json"
    receipt = root / "receipt.json"
    retention.write_text(json.dumps({"schema": "storage-retention-set-v1", "objects": [
        {"path": str(candidate), "state": "ARCHIVE_CANDIDATE"},
        {"path": str(unknown), "state": "UNKNOWN"},
    ]}), encoding="utf-8")
    subprocess.run([sys.executable, str(GC), "--retention", str(retention), "--receipt", str(receipt),
                    "--project-lock", str(root / "project.lock"),
                    "--generation-lock", str(root / "generation.lock")], check=True, stdout=subprocess.DEVNULL)
    assert candidate.exists() and unknown.exists()
    assert json.loads(receipt.read_text())["mode"] == "dry-run"

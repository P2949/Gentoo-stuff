#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
TOOL = ROOT / "scripts/optimization/storage/compact-profile-spool.py"
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    attempt = root / "attempt"
    attempt.mkdir()
    (attempt / "a.profraw").write_bytes(b"profile")
    archive = root / "archive.tar"
    metadata = root / "archive.json"
    result = subprocess.run([sys.executable, str(TOOL), "--attempt", str(attempt), "--archive", str(archive), "--manifest", str(metadata)], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["execute"] is False
    assert attempt.joinpath("a.profraw").exists()
    assert not archive.exists()

#!/usr/bin/env python3
"""Regression checks for exact profile-use identity refusal."""
import json, subprocess, tempfile
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/optimization/pgo/run-profile-use.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        metadata = root / "metadata.json"
        profile = root / "profile.profdata"
        dispatcher = root / "dispatcher.json"
        receipt = root / "receipt.json"
        log = root / "transaction.log"
        profile.write_bytes(b"profile")
        metadata.write_text(json.dumps({"profile": {
            "cpv": "dev-libs/foo-1.0",
            "repository": "gentoo",
            "ebuild_sha256": "a" * 64,
        }}))
        dispatcher.write_text(json.dumps({
            "cpv": "dev-libs/foo-1.0",
            "metadata": str(metadata),
            "profile": str(profile),
        }))
        bad = subprocess.run([
            "python3", str(SCRIPT), "--dispatcher", str(dispatcher),
            "--cpv", "dev-libs/foo-1.1", "--repository", "gentoo",
            "--receipt", str(receipt), "--log", str(log),
        ], capture_output=True, text=True)
        assert bad.returncode != 0
        assert "dispatcher CPV" in bad.stderr + bad.stdout
        metadata.write_text(json.dumps({"profile": {
            "cpv": "dev-libs/foo-1.0",
            "repository": "codex-local",
            "ebuild_sha256": "a" * 64,
        }}))
        bad = subprocess.run([
            "python3", str(SCRIPT), "--dispatcher", str(dispatcher),
            "--cpv", "dev-libs/foo-1.0", "--repository", "gentoo",
            "--receipt", str(receipt), "--log", str(log),
        ], capture_output=True, text=True)
        assert bad.returncode != 0
        assert "metadata repository" in bad.stderr + bad.stdout
    source = SCRIPT.read_text()
    assert "GENTOO_OPT_RUNNER_DISPATCHER_ENV" in source
    assert "with_suffix('.env')" in source
    print("PASS: profile-use runner refuses identity drift and supplies exact dispatcher handoff")

if __name__ == "__main__":
    main()

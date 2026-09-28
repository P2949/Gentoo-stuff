#!/usr/bin/env python3
"""Regression checks for exact profile-use identity refusal."""
import json, subprocess, tempfile, importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/optimization/pgo/run-profile-use.py"
SPEC = importlib.util.spec_from_file_location("profile_use_runner", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        metadata = root / "metadata.json"
        profile = root / "profile.profdata"
        dispatcher = root / "dispatcher.json"
        receipt = root / "receipt.json"
        log = root / "transaction.log"
        storage_args = ["--storage-path", str(root), "--storage-minimum-bytes", "0", "--storage-minimum-percent", "0"]
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
            "--receipt", str(receipt), "--log", str(log), *storage_args,
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
            "--receipt", str(receipt), "--log", str(log), *storage_args,
        ], capture_output=True, text=True)
        assert bad.returncode != 0
        assert "metadata repository" in bad.stderr + bad.stdout
    source = SCRIPT.read_text()
    assert "GENTOO_OPT_RUNNER_DISPATCHER_ENV" in source
    assert "with_suffix('.env')" in source
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "log"
        log.write_text("gentoo-optimization: profile-use backend clang-ir-use\n")
        assert MODULE.compilation_observed(log) is False
        log.write_text("libtool: compile: clang -c source.c -o source.o\n")
        assert MODULE.compilation_observed(log) is True
    print("PASS: profile-use runner refuses identity drift and supplies exact dispatcher handoff")

if __name__ == "__main__":
    main()

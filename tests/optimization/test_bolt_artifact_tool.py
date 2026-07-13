#!/usr/bin/env python3
"""Trust-boundary tests for the BOLT artifact transaction helper."""

from __future__ import annotations

import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "scripts/optimization/bolt/artifact_tool.py"
SPEC = importlib.util.spec_from_file_location("gentoo_bolt_artifact_tool", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class ProductionTrustTests(unittest.TestCase):
    def test_arbitrary_evidence_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bolt-untrusted-evidence.") as temporary:
            evidence = Path(temporary) / "evidence.json"
            evidence.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                TOOL.BoltArtifactError, "outside trusted optimization storage"
            ):
                TOOL.validate_production_evidence_path(evidence, "fixture evidence")

    def test_symlink_ancestor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bolt-symlink-evidence.") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            evidence = target / "evidence.json"
            evidence.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                TOOL.BoltArtifactError, "symlink component"
            ):
                TOOL.file_record(str(alias / evidence.name), "fixture evidence")

    def test_non_root_or_writable_ancestor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bolt-writable-evidence.") as temporary:
            root = Path(temporary)
            original_mode = stat.S_IMODE(root.stat().st_mode)
            root.chmod(0o777)
            try:
                with self.assertRaisesRegex(
                    TOOL.BoltArtifactError,
                    "non-root-owned component|group/world-writable component",
                ):
                    TOOL.validate_root_owned_nonwritable_chain(
                        root, "fixture evidence"
                    )
            finally:
                root.chmod(original_mode)

    def test_system_tool_chain_is_root_owned_and_nonwritable(self) -> None:
        if os.geteuid() != 0 and Path("/").stat().st_uid != 0:
            self.skipTest("filesystem root is not root-owned")
        candidate = Path("/usr/bin/env")
        if not candidate.exists() or candidate.is_symlink():
            self.skipTest("regular /usr/bin/env is unavailable")
        TOOL.validate_root_owned_nonwritable_chain(candidate, "system tool")


if __name__ == "__main__":
    unittest.main()

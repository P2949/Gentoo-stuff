#!/usr/bin/env python3
"""Tests for the package.env duplicate-policy checker."""

from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ENV_ROOT = REPOSITORY_ROOT / "portage" / "package.env"
CHECKER_PATH = (
    REPOSITORY_ROOT / "scripts" / "optimization" / "check-package-env-duplicates.py"
)
SPEC = importlib.util.spec_from_file_location("package_env_duplicate_checker", CHECKER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load package.env checker: {CHECKER_PATH}")
CHECKER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECKER
SPEC.loader.exec_module(CHECKER)


class PackageEnvPolicyTest(unittest.TestCase):
    def test_repository_has_no_duplicate_atom_environment_pairs(self) -> None:
        paths = CHECKER.policy_files(PACKAGE_ENV_ROOT)
        duplicates = CHECKER.duplicate_assignments(paths)
        self.assertFalse(
            duplicates,
            "duplicate package.env atom/environment pairs:\n"
            + CHECKER.format_duplicates(duplicates, PACKAGE_ENV_ROOT),
        )

    def test_parser_ignores_comments_and_accepts_distinct_environments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "policy"
            path.write_text(
                "\n"
                "# full-line comment\n"
                "dev-libs/example first.conf second.conf # inline comment\n"
                "dev-libs/example third.conf\n",
                encoding="utf-8",
            )
            self.assertEqual(
                [assignment.key for assignment in CHECKER.assignments_from_file(path)],
                [
                    ("dev-libs/example", "first.conf"),
                    ("dev-libs/example", "second.conf"),
                    ("dev-libs/example", "third.conf"),
                ],
            )
            self.assertEqual(CHECKER.duplicate_assignments([path]), [])

    def test_duplicate_detection_covers_same_line_and_multiple_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first_path = root / "10-first"
            second_path = root / "20-second"
            first_path.write_text(
                "dev-libs/example repeated.conf repeated.conf\n", encoding="utf-8"
            )
            second_path.write_text(
                "dev-libs/example repeated.conf\n", encoding="utf-8"
            )

            duplicates = CHECKER.duplicate_assignments([second_path, first_path])
            self.assertEqual(len(duplicates), 2)
            self.assertEqual(duplicates[0][0].path, first_path)
            self.assertEqual(duplicates[0][1].path, first_path)
            self.assertEqual(duplicates[1][0].path, first_path)
            self.assertEqual(duplicates[1][1].path, second_path)

    def test_policy_file_discovery_matches_recursive_portage_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            nested = root / "10-nested"
            hidden = root / ".hidden"
            nested.mkdir()
            hidden.mkdir()
            visible_path = nested / "20-visible"
            visible_path.write_text("dev-libs/example visible.conf\n", encoding="utf-8")
            (nested / "ignored~").write_text(
                "dev-libs/example ignored.conf\n", encoding="utf-8"
            )
            (hidden / "ignored").write_text(
                "dev-libs/example ignored.conf\n", encoding="utf-8"
            )

            self.assertEqual(CHECKER.policy_files(root), [visible_path])

    def test_cli_rejects_a_duplicate_with_both_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first_path = root / "10-first"
            second_path = root / "20-second"
            first_path.write_text(
                "dev-libs/example duplicate.conf\n", encoding="utf-8"
            )
            second_path.write_text(
                "dev-libs/example duplicate.conf\n", encoding="utf-8"
            )
            diagnostic = io.StringIO()

            with redirect_stderr(diagnostic):
                exit_status = CHECKER.main(["--package-env-root", str(root)])

            self.assertEqual(exit_status, 1)
            self.assertIn("duplicate package.env atom/environment pairs", diagnostic.getvalue())
            self.assertIn("10-first:1", diagnostic.getvalue())
            self.assertIn("20-second:1", diagnostic.getvalue())


if __name__ == "__main__":
    unittest.main()

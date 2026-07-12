#!/usr/bin/env python3
"""Tests for exact, fail-closed package.env policy validation."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ENV_ROOT = REPOSITORY_ROOT / "portage" / "package.env"
ENV_ROOT = REPOSITORY_ROOT / "portage" / "env"
POLICY_FILE = REPOSITORY_ROOT / "portage" / "package-env-policy.json"
CHECKER_PATH = (
    REPOSITORY_ROOT / "scripts" / "optimization" / "check-package-env-duplicates.py"
)
SPEC = importlib.util.spec_from_file_location("package_env_policy_checker", CHECKER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load package.env checker: {CHECKER_PATH}")
CHECKER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECKER
SPEC.loader.exec_module(CHECKER)


BASE_POLICY = {
    "schema_version": 1,
    "reviewed_multi_environment_stacks": {},
    "allowed_unmatched_atoms": {},
    "allowed_effective_atom_overlaps": [],
    "compiler_profiles": {},
    "forbidden_environment_patterns": [
        "(^|/)recovery/",
        "(^|/)(pgo-instrument|pgo-use-if-available|no-pgo-use)\\.conf$",
    ],
    "forbidden_policy_file_patterns": [
        "(^|/)50-global-pgo$",
        "(^|/)50-pgo-generated$",
    ],
    "forbidden_marker_variables": [
        "PGO_INSTRUMENT",
        "PGO_USE_IF_AVAILABLE",
        "PGO_DISABLE_USE",
        "BOLT_CAPTURE",
    ],
}

TOOLS = {
    "CC": "clang",
    "CXX": "clang++",
    "CPP": "clang-cpp",
    "LD": "ld.lld",
    "AR": "llvm-ar",
    "NM": "llvm-nm",
    "RANLIB": "llvm-ranlib",
}


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.package_env_root = root / "package.env"
        self.env_root = root / "env"
        self.policy_file = root / "package-env-policy.json"
        self.package_env_root.mkdir()
        self.env_root.mkdir()
        self.policy: dict[str, Any] = copy.deepcopy(BASE_POLICY)

    def write_assignment(self, content: str, name: str = "10-policy") -> Path:
        path = self.package_env_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_environment(self, name: str, content: str = "# fixture\n") -> Path:
        path = self.env_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_policy(self) -> None:
        self.policy_file.write_text(
            json.dumps(self.policy, indent=2) + "\n", encoding="utf-8"
        )

    def validate(
        self,
        matches: dict[str, set[str]] | None = None,
        atom_validator=None,
    ):
        self.write_policy()
        if matches is None:
            lines = CHECKER.all_policy_lines(
                CHECKER.policy_files(self.package_env_root)
            )
            matches = {
                atom: {f"{atom.split(':', 1)[0]}-1"}
                for atom in CHECKER.exact_environment_map(lines)
            }
        return CHECKER.validate_policy(
            self.package_env_root,
            self.env_root,
            self.policy_file,
            atom_validator=atom_validator or (lambda _atom: None),
            match_map=matches,
        )


class PackageEnvPolicyTest(unittest.TestCase):
    def test_repository_policy_passes_full_validation(self) -> None:
        result = CHECKER.validate_policy(
            PACKAGE_ENV_ROOT,
            ENV_ROOT,
            POLICY_FILE,
        )
        self.assertFalse(result.errors, "\n".join(result.errors))
        self.assertEqual(result.policy_file_count, 13)
        self.assertEqual(result.assignment_line_count, 137)
        self.assertEqual(result.atom_count, 135)
        self.assertEqual(result.pair_count, 146)

    def test_repository_has_exact_expected_cleanup_and_stacks(self) -> None:
        exact = CHECKER.exact_environment_map(
            CHECKER.all_policy_lines(CHECKER.policy_files(PACKAGE_ENV_ROOT))
        )
        self.assertNotIn("media-libs/SVT-AV1", exact)
        self.assertEqual(exact["media-libs/svt-av1"], ("O2.conf",))
        self.assertNotIn("dev-java/openjdk:25", exact)
        self.assertEqual(exact["gui-libs/hyprutils"], ("O2.conf",))
        self.assertEqual(
            exact["gui-wm/hyprland"], ("O3-thin-lto-no-libs.conf",)
        )
        self.assertEqual(
            exact["x11-base/xorg-server"],
            ("O2.conf", "xwayland-libbsd-overlay-cdefs-fix.conf"),
        )

    def test_repository_compiler_profiles_are_explicit_and_not_stale(self) -> None:
        policy = CHECKER.load_json_policy(POLICY_FILE)
        configured = policy["compiler_profiles"]
        for environment, entry in configured.items():
            assignments = CHECKER.environment_variable_assignments(
                ENV_ROOT / environment
            )
            self.assertEqual(
                {key: assignments.get(key) for key in CHECKER.TOOL_KEYS},
                entry["tools"],
                environment,
            )
            self.assertTrue(entry["lane"])
            self.assertTrue(entry["rationale"])
        libstdcxx = (ENV_ROOT / "libstdcxx-headers.conf").read_text(encoding="utf-8")
        self.assertNotIn("/16", libstdcxx)
        libstdcxx_assignments = CHECKER.environment_variable_assignments(
            ENV_ROOT / "libstdcxx-headers.conf"
        )
        self.assertNotIn("GCC_STDLIB_DIR", libstdcxx_assignments)
        self.assertNotIn(
            "--gcc-install-dir", libstdcxx_assignments["GCC_CXX_FLAGS"]
        )
        self.assertIn('GCC_CXX_FLAGS="-stdlib=libstdc++"', libstdcxx)

    def test_environment_parser_accepts_portage_backslash_continuations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "continued.conf"
            path.write_text(
                'COMMON_FLAGS="-O3 \\\n'
                '-pipe \\\n'
                '-march=native"\n'
                'CC="clang"\n',
                encoding="utf-8",
            )
            assignments = CHECKER.environment_variable_assignments(path)
            self.assertEqual(
                assignments["COMMON_FLAGS"].split(),
                ["-O3", "-pipe", "-march=native"],
            )
            self.assertEqual(assignments["CC"], "clang")

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

    def test_cli_rejects_duplicate_with_both_locations_before_other_checks(self) -> None:
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

    def test_cli_success_reports_explicit_portage_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example one.conf\n")
            fixture.write_environment("one.conf")
            fixture.write_policy()
            output = io.StringIO()
            with redirect_stdout(output):
                status = CHECKER.main(
                    [
                        "--package-env-root",
                        str(fixture.package_env_root),
                        "--env-root",
                        str(fixture.env_root),
                        "--policy-file",
                        str(fixture.policy_file),
                        "--skip-portage-universe",
                    ]
                )
            self.assertEqual(status, 0, output.getvalue())
            self.assertIn(
                "PORTAGE_SEMANTIC\tSKIP\tinstalled/repository atom matching explicitly skipped",
                output.getvalue(),
            )
            self.assertIn("matching explicitly skipped", output.getvalue())
            self.assertIn("PASS: exact package.env policy validated", output.getvalue())

    def test_cli_required_portage_universe_turns_semantic_skip_into_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example one.conf\n")
            fixture.write_environment("one.conf")
            fixture.write_policy()
            output = io.StringIO()
            diagnostic = io.StringIO()
            with redirect_stdout(output), redirect_stderr(diagnostic):
                status = CHECKER.main(
                    [
                        "--package-env-root",
                        str(fixture.package_env_root),
                        "--env-root",
                        str(fixture.env_root),
                        "--policy-file",
                        str(fixture.policy_file),
                        "--skip-portage-universe",
                        "--require-portage-universe",
                    ]
                )
            self.assertEqual(status, 1)
            self.assertIn("PORTAGE_SEMANTIC\tSKIP", output.getvalue())
            self.assertIn(
                "authoritative Portage package universe was required",
                diagnostic.getvalue(),
            )

    def test_missing_environment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example missing.conf\n")
            result = fixture.validate()
            self.assertTrue(any("referenced environment is unavailable" in e for e in result.errors))

    def test_unsafe_parent_environment_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example ../escape.conf\n")
            (fixture.root / "escape.conf").write_text("# outside\n", encoding="utf-8")
            result = fixture.validate()
            self.assertTrue(any("unsafe environment path" in e for e in result.errors))

    def test_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            outside = fixture.root / "outside.conf"
            outside.write_text("# outside\n", encoding="utf-8")
            (fixture.env_root / "escape.conf").symlink_to(outside)
            fixture.write_assignment("dev-libs/example escape.conf\n")
            result = fixture.validate()
            self.assertTrue(any("environment escapes env root" in e for e in result.errors))

    def test_atom_validator_error_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("bad-atom one.conf\n")
            fixture.write_environment("one.conf")

            def reject(_atom):
                raise ValueError("fixture invalid atom")

            result = fixture.validate(matches={"bad-atom": set()}, atom_validator=reject)
            self.assertTrue(any("invalid package atom" in e for e in result.errors))

    def test_dead_atom_requires_reasoned_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example one.conf\n")
            fixture.write_environment("one.conf")
            result = fixture.validate(matches={"dev-libs/example": set()})
            self.assertTrue(any("matches no installed or available" in e for e in result.errors))
            fixture.policy["allowed_unmatched_atoms"] = {
                "dev-libs/example": "Temporarily retained for an external repository."
            }
            result = fixture.validate(matches={"dev-libs/example": set()})
            self.assertFalse(result.errors, "\n".join(result.errors))

    def test_stale_dead_atom_exception_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example one.conf\n")
            fixture.write_environment("one.conf")
            fixture.policy["allowed_unmatched_atoms"] = {
                "dev-libs/example": "Fixture future package."
            }
            result = fixture.validate(
                matches={"dev-libs/example": {"dev-libs/example-1"}}
            )
            self.assertTrue(any("stale unmatched-atom exception" in e for e in result.errors))

    def test_effective_overlap_repeating_environment_is_always_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment(
                "dev-java/example common.conf\n"
                "dev-java/example:1 common.conf\n"
            )
            fixture.write_environment("common.conf")
            fixture.policy["allowed_effective_atom_overlaps"] = [
                {
                    "atoms": ["dev-java/example", "dev-java/example:1"],
                    "rationale": "Fixture overlap.",
                }
            ]
            cpv = "dev-java/example-1:1"
            result = fixture.validate(
                matches={
                    "dev-java/example": {cpv},
                    "dev-java/example:1": {cpv},
                }
            )
            self.assertTrue(any("repeats environments" in e for e in result.errors))

    def test_distinct_effective_overlap_requires_exact_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment(
                "dev-java/example generic.conf\n"
                "dev-java/example:1 slot.conf\n"
            )
            fixture.write_environment("generic.conf")
            fixture.write_environment("slot.conf")
            cpv = "dev-java/example-1:1"
            matches = {
                "dev-java/example": {cpv},
                "dev-java/example:1": {cpv},
            }
            result = fixture.validate(matches=matches)
            self.assertTrue(any("unreviewed effective atom overlap" in e for e in result.errors))
            fixture.policy["allowed_effective_atom_overlaps"] = [
                {
                    "atoms": ["dev-java/example", "dev-java/example:1"],
                    "rationale": "Generic and slot settings are deliberately orthogonal.",
                }
            ]
            result = fixture.validate(matches=matches)
            self.assertFalse(result.errors, "\n".join(result.errors))

    def test_multi_environment_stack_requires_exact_order_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example first.conf second.conf\n")
            fixture.write_environment("first.conf")
            fixture.write_environment("second.conf")
            result = fixture.validate()
            self.assertTrue(any("unreviewed multi-environment" in e for e in result.errors))
            fixture.policy["reviewed_multi_environment_stacks"] = {
                "dev-libs/example": {
                    "environments": ["second.conf", "first.conf"],
                    "rationale": "Wrong order fixture.",
                }
            }
            result = fixture.validate()
            self.assertTrue(any("reviewed stack mismatch" in e for e in result.errors))
            fixture.policy["reviewed_multi_environment_stacks"]["dev-libs/example"] = {
                "environments": ["first.conf", "second.conf"],
                "rationale": "",
            }
            result = fixture.validate()
            self.assertTrue(any("reviewed stack lacks a rationale" in e for e in result.errors))
            fixture.policy["reviewed_multi_environment_stacks"]["dev-libs/example"] = {
                "environments": ["first.conf", "second.conf"],
                "rationale": "The second fixture profile deliberately follows the first.",
            }
            result = fixture.validate()
            self.assertFalse(result.errors, "\n".join(result.errors))

    def test_stale_multi_environment_allowlist_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example one.conf\n")
            fixture.write_environment("one.conf")
            fixture.policy["reviewed_multi_environment_stacks"] = {
                "dev-libs/example": {
                    "environments": ["one.conf", "two.conf"],
                    "rationale": "Stale fixture.",
                }
            }
            result = fixture.validate()
            self.assertTrue(any("stale reviewed multi-environment" in e for e in result.errors))

    def test_unreviewed_compiler_profile_and_incomplete_tuple_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example compiler.conf\n")
            fixture.write_environment("compiler.conf", "CC=clang\n")
            result = fixture.validate()
            self.assertTrue(any("lacks a reviewed lane" in e for e in result.errors))
            fixture.policy["compiler_profiles"] = {
                "compiler.conf": {
                    "lane": "fixture",
                    "tools": TOOLS,
                    "rationale": "Fixture lane.",
                }
            }
            result = fixture.validate()
            self.assertTrue(any("does not explicitly assign every tool" in e for e in result.errors))

    def test_compiler_tool_tuple_must_match_json_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example compiler.conf\n")
            fixture.write_environment(
                "compiler.conf",
                "\n".join(f'{key}="{value}"' for key, value in TOOLS.items()) + "\n",
            )
            fixture.policy["compiler_profiles"] = {
                "compiler.conf": {
                    "lane": "fixture-clang",
                    "tools": {**TOOLS, "LD": "wrong-linker"},
                    "rationale": "Fixture exact tuple.",
                }
            }
            result = fixture.validate()
            self.assertTrue(any("compiler tool tuple mismatch" in e for e in result.errors))
            fixture.policy["compiler_profiles"]["compiler.conf"]["tools"] = TOOLS
            result = fixture.validate()
            self.assertFalse(result.errors, "\n".join(result.errors))

    def test_conflicting_compiler_lanes_in_stack_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example first.conf second.conf\n")
            for name in ("first.conf", "second.conf"):
                fixture.write_environment(
                    name,
                    "\n".join(f'{key}="{value}"' for key, value in TOOLS.items()) + "\n",
                )
            fixture.policy["reviewed_multi_environment_stacks"] = {
                "dev-libs/example": {
                    "environments": ["first.conf", "second.conf"],
                    "rationale": "Conflict fixture stack.",
                }
            }
            fixture.policy["compiler_profiles"] = {
                name: {
                    "lane": f"lane-{index}",
                    "tools": TOOLS,
                    "rationale": "Fixture lane.",
                }
                for index, name in enumerate(("first.conf", "second.conf"), start=1)
            }
            result = fixture.validate()
            self.assertTrue(any("conflicting compiler lanes" in e for e in result.errors))

    def test_forbidden_environment_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example pgo-instrument.conf\n")
            fixture.write_environment("pgo-instrument.conf", "# marker profile\n")
            result = fixture.validate()
            self.assertTrue(any("forbidden recovery/generated environment" in e for e in result.errors))

    def test_forbidden_marker_variable_is_rejected_even_with_normal_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("dev-libs/example normal.conf\n")
            fixture.write_environment("normal.conf", 'PGO_INSTRUMENT="1"\n')
            result = fixture.validate()
            self.assertTrue(any("sets forbidden stage markers" in e for e in result.errors))

    def test_active_generated_policy_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment(
                "dev-libs/example normal.conf\n", name="50-pgo-generated"
            )
            fixture.write_environment("normal.conf")
            result = fixture.validate()
            self.assertTrue(any("forbidden in recovery/generated policy file" in e for e in result.errors))

    def test_assignment_free_legacy_policy_filename_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Fixture(Path(temporary_directory))
            fixture.write_assignment("# no active assignment\n", name="50-global-pgo")
            fixture.write_assignment("dev-libs/example normal.conf\n")
            fixture.write_environment("normal.conf")
            result = fixture.validate()
            self.assertFalse(result.errors, "\n".join(result.errors))


if __name__ == "__main__":
    unittest.main()

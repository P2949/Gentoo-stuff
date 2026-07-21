from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import cast


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
POLICY_ROOT = REPOSITORY_ROOT / "optimization"


class OptimizationPolicyTests(unittest.TestCase):
    def load(self, name: str) -> dict[str, object]:
        value = json.loads((POLICY_ROOT / name).read_text(encoding="utf-8"))
        return cast(dict[str, object], value)

    def test_reviewed_non_suffix_shell_sources_are_regular_files(self) -> None:
        reviewed = (
            "optimization/fixtures/portage/capture-proxy.sh.in",
            "optimization/fixtures/portage/phase2-phase-identity-1.ebuild",
            "optimization/fixtures/portage/phase2-phase-identity-install-qa",
            "portage/bashrc",
            "portage/install-qa-check.d/zz-gentoo-optimization-bolt",
            "portage/repo.postsync.d/fix-sft-broken",
        )
        for relative in reviewed:
            with self.subTest(relative=relative):
                path = REPOSITORY_ROOT / relative
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())

    def test_policy_has_no_unproven_active_generation(self) -> None:
        policy = self.load("policy.yaml")
        self.assertEqual(policy["schema_version"], 1)
        self.assertIsNone(policy["active_generation"])

    def test_bolt_default_is_the_exact_validated_policy(self) -> None:
        policy = self.load("policy.yaml")
        options = policy["bolt"]["default_options"]  # type: ignore[index]
        self.assertEqual(
            options,
            [
                "-reorder-blocks=ext-tsp",
                "-reorder-functions=cdsort",
                "-split-functions",
                "-split-all-cold",
                "-split-eh",
                "-icf=safe",
                "-update-debug-sections",
                "-dyno-stats",
            ],
        )
        self.assertNotIn("-use-gnu-stack", options)

    def test_review_files_start_empty(self) -> None:
        exclusions = self.load("exclusions.yaml")
        overrides = self.load("package-overrides.yaml")
        self.assertEqual(exclusions, {"schema_version": 1, "exclusions": []})
        self.assertEqual(overrides, {"schema_version": 1, "overrides": []})

    def test_repository_root_contains_only_reviewed_files(self) -> None:
        """Reject accidental shell-redirection and fixture residue at repo root."""
        reviewed = {
            ".git",
            ".github",
            ".gitignore",
            ".mypy_cache",
            ".vscode",
            "LICENSE",
            "README.md",
            "bench",
            "docs",
            "local-overlay",
            "optimization",
            "plan.md",
            "plans",
            "portage",
            "scripts",
            "tests",
        }
        entries = {path.name for path in REPOSITORY_ROOT.iterdir()}
        self.assertEqual(entries - reviewed, set())
        github = REPOSITORY_ROOT / ".github"
        self.assertTrue(github.is_dir())
        self.assertFalse(github.is_symlink())
        github_entries = {
            path.relative_to(github).as_posix(): (
                "symlink"
                if path.is_symlink()
                else "directory"
                if path.is_dir()
                else "file"
                if path.is_file()
                else "other"
            )
            for path in github.rglob("*")
        }
        self.assertEqual(
            github_entries,
            {
                "workflows": "directory",
                "workflows/portable-optimization-validation.yml": "file",
            },
        )

    def test_portable_ci_actions_are_pinned_to_immutable_commits(self) -> None:
        workflow = (
            REPOSITORY_ROOT
            / ".github/workflows/portable-optimization-validation.yml"
        ).read_text(encoding="utf-8")
        uses = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
        self.assertEqual(len(uses), 2)
        for reference in uses:
            self.assertRegex(reference, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")

    def test_phase2_evidence_binds_checkpoint_and_runtime_primitives(self) -> None:
        policy = self.load("phase2-evidence-policy.json")
        manifest = self.load("phase2-tool-manifest.json")
        required_tools = cast(list[str], policy["required_tools"])
        manifest_tools = cast(list[dict[str, object]], manifest["tools"])
        manifest_names = [cast(str, entry["name"]) for entry in manifest_tools]
        checkpoint_primitives = {
            "date",
            "emaint",
            "emerge",
            "findmnt",
            "mount",
            "qcheck",
            "quickpkg",
            "sleep",
            "umount",
            "zstd",
        }
        framework_primitives = {"systemd-tmpfiles"}
        self.assertEqual(required_tools, sorted(required_tools))
        self.assertEqual(manifest_names, required_tools)
        self.assertLessEqual(
            checkpoint_primitives | framework_primitives,
            set(required_tools),
        )

        required_sources = set(cast(list[str], policy["required_sources"]))
        self.assertLessEqual(
            {
                "docs/binpkg-checkpoint-runbook.md",
                "optimization/tmpfiles/gentoo-optimization.conf",
                "scripts/optimization/recovery/create-binpkg-checkpoint.sh",
                "tests/optimization/recovery/test_create_binpkg_checkpoint.py",
            },
            required_sources,
        )

        claims = {
            cast(str, claim["claim_id"]): set(
                cast(list[str], claim["source_paths"])
            )
            for claim in cast(list[dict[str, object]], policy["plan_claims"])
        }
        self.assertLessEqual(
            {
                "docs/binpkg-checkpoint-runbook.md",
                "scripts/optimization/recovery/create-binpkg-checkpoint.sh",
                "tests/optimization/recovery/test_create_binpkg_checkpoint.py",
            },
            claims["phase2-automation"],
        )
        self.assertIn(
            "optimization/tmpfiles/gentoo-optimization.conf",
            claims["phase2-framework"],
        )


if __name__ == "__main__":
    unittest.main()

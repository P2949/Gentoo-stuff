from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit


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

        # Local Markdown links are part of the reviewed repository interface.
        # Resolve every inline target relative to its source file so stale
        # legacy documentation cannot silently point at a removed path.
        repository = REPOSITORY_ROOT.resolve(strict=True)
        inline_link = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
        for markdown in sorted(REPOSITORY_ROOT.rglob("*.md")):
            if markdown.is_symlink():
                continue
            text = markdown.read_text(encoding="utf-8")
            for raw_target in inline_link.findall(text):
                parsed = urlsplit(raw_target)
                if parsed.scheme or raw_target.startswith("#") or not parsed.path:
                    continue
                relative = Path(unquote(parsed.path))
                with self.subTest(
                    markdown=markdown.relative_to(REPOSITORY_ROOT).as_posix(),
                    target=raw_target,
                ):
                    self.assertFalse(relative.is_absolute())
                    target = (markdown.parent / relative).resolve(strict=True)
                    self.assertTrue(target.is_relative_to(repository))

        history_map = (
            REPOSITORY_ROOT / "docs/commit-history-map.md"
        ).read_text(encoding="utf-8")
        for historical_commit in (
            "d8a90a41f78c20e18a83bab3f4f1a7dd418856cc",
            "19a46b78acafcc96df6a4f4c54b0880109734354",
        ):
            with self.subTest(historical_commit=historical_commit):
                self.assertIn(historical_commit, history_map)
        self.assertIn("Evidence-bearing ancestors are immutable.", history_map)
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Phase 2 is scope-frozen until Candidate B authorization.", readme)
        bolt_legacy = (
            REPOSITORY_ROOT / "docs/bolt-global.md"
        ).read_text(encoding="utf-8")
        self.assertIn("The retired prototype made packages BOLT-ready globally", bolt_legacy)
        self.assertNotIn("This repository makes packages BOLT-ready globally", bolt_legacy)

        for wrapper_name, command in (
            ("capture-input.sh", "capture"),
            ("deploy-output.sh", "deploy"),
            ("register-output.sh", "register-output"),
        ):
            wrapper = (
                REPOSITORY_ROOT / "scripts/optimization/bolt" / wrapper_name
            ).read_text(encoding="utf-8")
            with self.subTest(wrapper=wrapper_name):
                self.assertIn(
                    'exec /usr/bin/python3 -I -B "${SCRIPT_DIR}/artifact_tool.py" '
                    f'{command} "$@"',
                    wrapper,
                )
                self.assertNotIn(
                    'exec /usr/bin/python3 -I "${SCRIPT_DIR}/artifact_tool.py"',
                    wrapper,
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
        self.assertEqual(
            re.findall(r"^\s*timeout-minutes:\s*([0-9]+)\s*$", workflow, re.MULTILINE),
            ["75"],
        )
        for required_fragment in (
            "test -x /usr/sbin/runuser",
            "grep -Fx 'util-linux: /usr/sbin/runuser'",
            "sudo ln --symbolic -- /usr/sbin/runuser /usr/bin/runuser",
            "readlink --canonicalize-existing /usr/bin/runuser",
            "^PASS[[:space:]]+framework-installer[[:space:]]+",
        ):
            with self.subTest(required_fragment=required_fragment):
                self.assertIn(required_fragment, workflow)

    def test_phase2_evidence_binds_checkpoint_and_runtime_primitives(self) -> None:
        policy = self.load("phase2-evidence-policy.json")
        manifest = self.load("phase2-tool-manifest.json")
        required_tools = cast(list[str], policy["required_tools"])
        test_execution_tools = cast(list[str], policy["test_execution_tools"])
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
        operator_primitives = {"cut", "doas"}
        self.assertEqual(required_tools, sorted(required_tools))
        self.assertEqual(manifest_names, required_tools)
        self.assertEqual(
            policy["authoritative_test_path"],
            ["/usr/bin", "/usr/lib/llvm/22/bin", "/bin"],
        )
        self.assertEqual(
            test_execution_tools,
            [
                "bash",
                "env",
                "git",
                "python3",
                "setsid",
                "shellcheck",
                "sleep",
                "timeout",
            ],
        )
        self.assertLessEqual(set(test_execution_tools), set(required_tools))
        self.assertLessEqual(
            checkpoint_primitives | framework_primitives | operator_primitives,
            set(required_tools),
        )
        manifest_by_name = {
            cast(str, entry["name"]): entry for entry in manifest_tools
        }
        self.assertEqual(
            manifest_by_name["cut"],
            {
                "name": "cut",
                "path": "/usr/bin/cut",
                "version_args": ["--version"],
            },
        )
        self.assertEqual(
            manifest_by_name["doas"],
            {
                "name": "doas",
                "path": "/usr/bin/doas",
                "version_args": ["-n", "/usr/bin/id", "-u"],
            },
        )

        production_runbook = (
            REPOSITORY_ROOT / "docs/phase2-production-profile-transaction.md"
        ).read_text(encoding="utf-8")
        materialization_boundary = production_runbook.split(
            "## Create the candidate's immutable source snapshot", 1
        )[1].split(
            "Also prove containment and recover any earlier interrupted transaction",
            1,
        )[0]
        for required_fragment in (
            "PATH=/usr/bin:/bin",
            "/usr/bin/cut",
            "/usr/bin/doas",
            "/usr/bin/git",
            "/usr/bin/timeout --signal=TERM",
            "[[ ! -e $1 && ! -L $1 ]]",
        ):
            with self.subTest(required_fragment=required_fragment):
                self.assertIn(required_fragment, materialization_boundary)
        self.assertGreaterEqual(
            materialization_boundary.count("/usr/bin/timeout --signal=TERM"), 2
        )
        self.assertNotRegex(
            materialization_boundary,
            r"(?m)^\s*(?:doas|git|test|cut|date|sha256sum|awk)\b",
        )
        self.assertNotRegex(materialization_boundary, r"\|\s*(?:awk|cut)\b")

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

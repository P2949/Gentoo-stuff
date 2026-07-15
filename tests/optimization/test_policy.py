from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import cast


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
POLICY_ROOT = REPOSITORY_ROOT / "optimization"


class OptimizationPolicyTests(unittest.TestCase):
    def load(self, name: str) -> dict[str, object]:
        value = json.loads((POLICY_ROOT / name).read_text(encoding="utf-8"))
        return cast(dict[str, object], value)

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
        allowed = {".gitignore", "LICENSE", "README.md", "plan.md"}
        root_files = {
            path.name
            for path in REPOSITORY_ROOT.iterdir()
            if path.is_file() or path.is_symlink()
        }
        self.assertEqual(root_files, allowed)


if __name__ == "__main__":
    unittest.main()

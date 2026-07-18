#!/usr/bin/env python3
"""Portable end-to-end tests for the detached Phase 2 evidence index."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[2]
TOOL = REPOSITORY / "scripts/optimization/verify/phase2-evidence.py"
POLICY_SCHEMA = "gentoo-optimization-phase2-evidence-policy-v1"
TOOL_SCHEMA = "gentoo-optimization-phase2-tool-manifest-v1"
MARKER_PREFIX = "<!-- gentoo-optimization-phase2-evidence: "
MARKER_SUFFIX = " -->"


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def validate_schema(value: object, schema: dict[str, object], root: dict[str, object], path: str = "$") -> None:
    """Validate the JSON-Schema features used by the checked-in index schema.

    This independent, dependency-free subset keeps the portable gate useful on
    hosts without the optional ``jsonschema`` package.
    """
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise AssertionError(f"{path}: unsupported schema reference {reference!r}")
        definition = reference.removeprefix("#/$defs/")
        definitions = root.get("$defs")
        if not isinstance(definitions, dict) or definition not in definitions:
            raise AssertionError(f"{path}: missing schema definition {definition}")
        target = definitions[definition]
        if not isinstance(target, dict):
            raise AssertionError(f"{path}: schema definition is not an object")
        validate_schema(value, target, root, path)
        return
    if "oneOf" in schema:
        choices = schema["oneOf"]
        if not isinstance(choices, list):
            raise AssertionError(f"{path}: oneOf is not an array")
        matches = 0
        for choice in choices:
            if not isinstance(choice, dict):
                raise AssertionError(f"{path}: oneOf choice is not an object")
            try:
                validate_schema(value, choice, root, path)
            except AssertionError:
                continue
            matches += 1
        if matches != 1:
            raise AssertionError(f"{path}: oneOf matched {matches} choices")
        return
    if "const" in schema and value != schema["const"]:
        raise AssertionError(f"{path}: value differs from const")
    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, list) or value not in choices:
            raise AssertionError(f"{path}: value is not in enum")
    expected_type = schema.get("type")
    if expected_type is not None:
        accepted = [expected_type] if isinstance(expected_type, str) else expected_type
        if not isinstance(accepted, list):
            raise AssertionError(f"{path}: invalid schema type")
        type_matches = {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }
        if not any(type_matches.get(item, False) for item in accepted):
            raise AssertionError(f"{path}: expected type {accepted!r}")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or not set(required).issubset(value):
            raise AssertionError(f"{path}: required properties are absent")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise AssertionError(f"{path}: properties is invalid")
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            child_schema = properties.get(key)
            if child_schema is None:
                if additional is False:
                    raise AssertionError(f"{path}: unexpected property {key}")
                child_schema = additional if isinstance(additional, dict) else None
            if child_schema is not None:
                if not isinstance(child_schema, dict):
                    raise AssertionError(f"{path}.{key}: invalid child schema")
                validate_schema(child, child_schema, root, f"{path}.{key}")
        minimum_properties = schema.get("minProperties")
        if isinstance(minimum_properties, int) and len(value) < minimum_properties:
            raise AssertionError(f"{path}: too few properties")
    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            raise AssertionError(f"{path}: too few items")
        if schema.get("uniqueItems") is True:
            serialized = [canonical(item) for item in value]
            if len(serialized) != len(set(serialized)):
                raise AssertionError(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                validate_schema(child, item_schema, root, f"{path}[{index}]")
    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            raise AssertionError(f"{path}: string is too short")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            import re

            if re.search(pattern, value) is None:
                raise AssertionError(f"{path}: string does not match pattern")
    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            raise AssertionError(f"{path}: integer is below minimum")
        if isinstance(maximum, int) and value > maximum:
            raise AssertionError(f"{path}: integer is above maximum")


class EvidenceFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase2-evidence.")
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.evidence = self.root / "evidence"
        self.run_id = "phase2-fixture-run"
        self.state_root = self.root / "phase-2-components"
        self.state_directory = self.state_root / self.run_id
        self.state = self.state_directory / "component.json"
        self.index_root = self.root / "phase2-evidence"
        self.index_directory = self.index_root / self.run_id
        self.index = self.index_directory / "index.json"
        self.bin = self.root / "bin"
        self.repository.mkdir(mode=0o700)
        self.evidence.mkdir(mode=0o700)
        self.bin.mkdir(mode=0o700)
        self.state_directory.mkdir(parents=True, mode=0o700)
        self.index_directory.mkdir(parents=True, mode=0o700)
        self.git = Path(shutil.which("git") or "")
        if not self.git.is_absolute():
            raise unittest.SkipTest("git is unavailable")
        self.real_script = self.bin / "fixture-tool-real"
        self.real_script.write_text(
            "#!/bin/sh\n"
            "if [ \"${1-}\" = --version ]; then\n"
            "    printf 'fixture-tool 1.0\\n'\n"
            "    exit 0\n"
            "fi\n"
            "exit 2\n",
            encoding="utf-8",
        )
        self.real_script.chmod(0o700)
        self.script_link = self.bin / "fixture-tool"
        self.script_link.symlink_to(self.real_script.name)
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "Evidence Fixture")
        self.run_git("config", "user.email", "evidence@example.invalid")
        self.write_repository()
        self.run_git("add", ".")
        self.run_git("commit", "-q", "-m", "fixture")
        self.commit = self.run_git("rev-parse", "HEAD").stdout.strip()
        self.write_external_evidence()

    def cleanup(self) -> None:
        self.temporary.cleanup()

    def run_git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [os.fspath(self.git), "-C", os.fspath(self.repository), *arguments],
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def write_repository(self) -> None:
        source = self.repository / "src/code.py"
        source.parent.mkdir()
        source.write_text("VALUE = 1\n", encoding="utf-8")
        driver = self.repository / "test-driver.sh"
        driver.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        driver.chmod(0o755)
        contract = {
            "expected_diagnostic_subtests": [
                {"subtest": "optional-observation", "test": "core"}
            ],
            "portable_allowed_required_skips": [],
            "portable_allowed_top_level_skips": [],
            "required_named_subtests": [
                {"subtest": "mandatory-branch", "test": "core"}
            ],
            "schema": "gentoo-optimization-phase2-authoritative-test-contract-v1",
            "top_level": {
                "exact_names": ["capability:fixture", "core"],
                "prefix_groups": [],
            },
            "unittest_suites": [
                {
                    "expected_count": 1,
                    "subtest_names_sha256": digest(b"python.fixture.test_core\n"),
                    "test": "core",
                }
            ],
        }
        (self.repository / "test-contract.json").write_text(
            json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        checkbox = "- [x] Exact fixture claim"
        marker = {
            "checkbox_sha256": [digest(checkbox.encode())],
            "claim_id": "fixture-claim",
            "source_sha256": {"src/code.py": digest(source.read_bytes())},
        }
        (self.repository / "plan.md").write_text(
            "# Plan\n\n"
            "# 11. Fixture Phase 2\n\n"
            "<!-- gentoo-optimization-phase2-prior-evidence: "
            "superseded-by-detached-index -->\n\n"
            f"{checkbox}\n\n{MARKER_PREFIX}{canonical(marker)}{MARKER_SUFFIX}\n\n"
            "# 12. Fixture Phase 3\n",
            encoding="utf-8",
        )
        policy = {
            "aggregate_requires_zero": True,
            "authoritative_test_contract_path": "test-contract.json",
            "component_state_path_template": (
                f"{self.state_root}/{{run_id}}/{{name}}.json"
            ),
            "index_path_template": f"{self.index_root}/{{run_id}}/index.json",
            "phase": 2,
            "phase_heading": "# 11. Fixture Phase 2",
            "phase_next_heading": "# 12. Fixture Phase 3",
            "plan_claims": [
                {"claim_id": "fixture-claim", "source_paths": ["src/code.py"]}
            ],
            "plan_path": "plan.md",
            "prior_evidence_banner": "<!-- gentoo-optimization-phase2-prior-evidence: superseded-by-detached-index -->",
            "require_all_phase_checkboxes_checked": True,
            "required_authoritative": True,
            "required_component_states": [
                {
                    "external_evidence_labels": [],
                    "name": "component",
                    "required_test_names": ["core"],
                    "required_test_prefixes": ["capability:"],
                }
            ],
            "required_passing_test_names": ["core"],
            "required_passing_test_prefixes": ["capability:"],
            "required_sources": ["plan.md", "policy.json", "src/code.py", "test-contract.json", "test-driver.sh"],
            "required_test_mode": "capabilities",
            "required_tools": ["git", "script-tool"],
            "schema": POLICY_SCHEMA,
            "source_scopes": ["plan.md", "policy.json", "src", "test-contract.json", "test-driver.sh"],
            "test_driver_path": "test-driver.sh",
            "tool_manifest_template_path": "tools-template.json",
        }
        tools_template = {
            "schema": TOOL_SCHEMA,
            "tools": [
                {"name": "git", "path": os.fspath(self.git), "version_args": ["--version"]},
                {
                    "name": "script-tool",
                    "path": os.fspath(self.script_link),
                    "version_args": ["--version"],
                },
            ],
        }
        (self.repository / "tools-template.json").write_text(
            json.dumps(tools_template, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        required_sources = policy["required_sources"]
        source_scopes = policy["source_scopes"]
        if not isinstance(required_sources, list) or not isinstance(
            source_scopes, list
        ):
            raise AssertionError("fixture policy source lists are invalid")
        required_sources.append("tools-template.json")
        required_sources.sort()
        source_scopes.append("tools-template.json")
        source_scopes.sort()
        (self.repository / "policy.json").write_text(
            json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        for tracked_regular in (
            source,
            self.repository / "plan.md",
            self.repository / "policy.json",
            self.repository / "test-contract.json",
            self.repository / "tools-template.json",
        ):
            tracked_regular.chmod(0o644)

    def write_external_evidence(self) -> None:
        for name in ("test-run-provenance.pending.json", "test-run-provenance.json"):
            try:
                (self.evidence / name).unlink()
            except FileNotFoundError:
                pass
        try:
            self.state.unlink()
        except FileNotFoundError:
            pass
        core_log = self.evidence / "core.log"
        capability_log = self.evidence / "capability.log"
        core_log.write_text("core passed\n", encoding="utf-8")
        capability_log.write_text("capability passed\n", encoding="utf-8")
        results = self.evidence / "results.tsv"
        results.write_text(
            "status\ttest\tdetail\n"
            f"PASS\tcore\texit_status=0 log={core_log}\n"
            f"PASS\tcapability:fixture\texit_status=0 log={capability_log}\n",
            encoding="utf-8",
        )
        subtests = self.evidence / "subtests.tsv"
        subtests.write_text(
            "status\trequirement\ttest\tsubtest\tdetail\n"
            "PASS\trequired\tcore\tdriver.case-completion\tcase passed\n"
            "PASS\trequired\tcore\tmandatory-branch\trequired branch passed\n"
            "PASS\trequired\tcore\tpython.fixture.test_core\tunittest passed\n"
            "PASS\trequired\tcapability:fixture\tdriver.case-completion\tcase passed\n"
            "SKIP\tdiagnostic\tcore\toptional-observation\tdiagnostic tool unavailable\n",
            encoding="utf-8",
        )
        (self.evidence / "summary.txt").write_text(
            "mode=capabilities\n"
            "authoritative=1\n"
            "pass=2\n"
            "fail=0\n"
            "skip=0\n"
            "total=2\n"
            "required_subtest_pass=4\n"
            "required_subtest_fail=0\n"
            "required_subtest_skip=0\n"
            "mandatory_internal_skip=0\n"
            "diagnostic_subtest_pass=0\n"
            "diagnostic_subtest_fail=0\n"
            "diagnostic_internal_skip=1\n"
            "subtest_total=5\n"
            "exit_status=0\n"
            "external_authority_index_preserved=0\n"
            f"results={results}\n"
            f"subtests={subtests}\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                os.fspath(TOOL),
                "run-provenance-start",
                "--repository-root",
                os.fspath(self.repository),
                "--driver",
                os.fspath(self.repository / "test-driver.sh"),
                "--git",
                os.fspath(self.git),
                "--output",
                os.fspath(self.evidence / "test-run-provenance.pending.json"),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            [
                sys.executable,
                os.fspath(TOOL),
                "run-provenance-finish",
                "--pending",
                os.fspath(self.evidence / "test-run-provenance.pending.json"),
                "--results",
                os.fspath(results),
                "--subtests",
                os.fspath(subtests),
                "--summary",
                os.fspath(self.evidence / "summary.txt"),
                "--output",
                os.fspath(self.evidence / "test-run-provenance.json"),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        shutil.copyfile(self.repository / "tools-template.json", self.evidence / "tools.json")
        subprocess.run(
            [
                sys.executable,
                os.fspath(TOOL),
                "component-state",
                "--repository-root",
                os.fspath(self.repository),
                "--policy",
                "policy.json",
                "--component",
                "component",
                "--run-id",
                self.run_id,
                "--evidence-root",
                os.fspath(self.evidence),
                "--provenance",
                os.fspath(self.evidence / "test-run-provenance.json"),
                "--results",
                os.fspath(results),
                "--subtests",
                os.fspath(subtests),
                "--summary",
                os.fspath(self.evidence / "summary.txt"),
                "--git",
                os.fspath(self.git),
                "--output",
                os.fspath(self.state),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def command(self, action: str = "capture") -> list[str]:
        if action == "verify":
            return [sys.executable, os.fspath(TOOL), "verify", "--index", os.fspath(self.index)]
        return [
            sys.executable,
            os.fspath(TOOL),
            "capture",
            "--repository-root",
            os.fspath(self.repository),
            "--policy",
            "policy.json",
            "--evidence-root",
            os.fspath(self.evidence),
            "--tools",
            os.fspath(self.evidence / "tools.json"),
            "--test-results",
            os.fspath(self.evidence / "results.tsv"),
            "--test-subtests",
            os.fspath(self.evidence / "subtests.tsv"),
            "--test-summary",
            os.fspath(self.evidence / "summary.txt"),
            "--run-id",
            self.run_id,
            "--component-state",
            f"component={self.state}",
            "--output",
            os.fspath(self.index),
        ]

    def run(self, action: str = "capture", check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command(action),
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )


class Phase2EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = EvidenceFixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_capture_and_verify_bind_script_and_symlink_identity(self) -> None:
        self.fixture.run(check=True)
        self.fixture.run("verify", check=True)
        document = json.loads(self.fixture.index.read_text(encoding="utf-8"))
        script = next(item for item in document["tools"] if item["name"] == "script-tool")
        self.assertEqual(script["requested_path"], os.fspath(self.fixture.script_link))
        self.assertEqual(script["resolved_path"], os.fspath(self.fixture.real_script))
        self.assertEqual(script["stdout"]["text"], "fixture-tool 1.0\n")
        self.assertEqual(script["shebang"]["line"], "/bin/sh")
        self.assertTrue(script["shebang"]["resolved_path"].startswith("/"))
        self.assertEqual(len(script["shebang"]["binary"]["sha256"]), 64)
        self.assertEqual(document["aggregate"], {"pending_total": 0, "unknown_total": 0, "failed_total": 0})
        self.assertIs(document["test_run"]["authoritative"], True)
        self.assertEqual(document["test_run"]["mandatory_internal_skip"], 0)
        self.assertEqual(document["test_run"]["diagnostic_internal_skip"], 1)
        self.assertEqual(
            document["test_run"]["contract_totals"],
            {
                "expected_diagnostic_subtests": 1,
                "required_named_subtests": 1,
                "top_level_tests": 2,
                "unittest_suites": 1,
                "unittest_tests": 1,
            },
        )
        self.assertEqual(
            document["test_run"]["required_named_subtests"],
            [{"status": "PASS", "subtest": "mandatory-branch", "test": "core"}],
        )
        self.assertEqual(
            document["test_run"]["subtest_totals"],
            {
                "diagnostic_fail": 0,
                "diagnostic_pass": 0,
                "diagnostic_skip": 1,
                "mandatory_internal_skip": 0,
                "required_fail": 0,
                "required_pass": 4,
                "required_skip": 0,
                "total": 5,
            },
        )
        schema = json.loads(
            (REPOSITORY / "optimization/schema/phase2-evidence-index.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validate_schema(document, schema, schema)
        component_document = json.loads(self.fixture.state.read_text(encoding="utf-8"))
        component_schema = json.loads(
            (
                REPOSITORY
                / "optimization/schema/phase2-component-state.schema.json"
            ).read_text(encoding="utf-8")
        )
        validate_schema(component_document, component_schema, component_schema)

    def test_mandatory_internal_skip_cannot_authorize_capture(self) -> None:
        subtests = self.fixture.evidence / "subtests.tsv"
        with subtests.open("a", encoding="utf-8") as output:
            output.write(
                "SKIP\trequired\tcore\tprivileged-metadata\t"
                "file capabilities were unavailable\n"
            )
        summary = self.fixture.evidence / "summary.txt"
        text = summary.read_text(encoding="utf-8")
        text = text.replace("required_subtest_skip=0", "required_subtest_skip=1")
        text = text.replace("mandatory_internal_skip=0", "mandatory_internal_skip=1")
        text = text.replace("subtest_total=5", "subtest_total=6")
        summary.write_text(text, encoding="utf-8")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mandatory internal failure or skip", result.stderr)
        self.assertFalse(self.fixture.index.exists())

    def test_subtest_ledger_tampering_invalidates_captured_index(self) -> None:
        self.fixture.run(check=True)
        subtests = self.fixture.evidence / "subtests.tsv"
        with subtests.open("a", encoding="utf-8") as output:
            output.write(
                "PASS\tdiagnostic\tcore\tlate-row\t"
                "unindexed diagnostic observation\n"
            )
        result = self.fixture.run("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("subtests", result.stderr)

    def test_forged_duplicate_and_missing_completion_subtests_are_rejected(self) -> None:
        mutations = {
            "duplicate": (
                "PASS\trequired\tcore\tdriver.case-completion\tduplicate\n",
                "duplicate structured subtest identity",
            ),
            "unknown-top-level": (
                "PASS\trequired\tforged-test\tforged-row\tforged\n",
                "unknown top-level test",
            ),
        }
        for label, (row, diagnostic) in mutations.items():
            with self.subTest(label=label):
                self.fixture.write_external_evidence()
                subtests = self.fixture.evidence / "subtests.tsv"
                with subtests.open("a", encoding="utf-8") as output:
                    output.write(row)
                result = self.fixture.run()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(diagnostic, result.stderr)

        self.fixture.write_external_evidence()
        subtests = self.fixture.evidence / "subtests.tsv"
        rows = [
            line
            for line in subtests.read_text(encoding="utf-8").splitlines()
            if "\tcore\tdriver.case-completion\t" not in line
        ]
        subtests.write_text("\n".join(rows) + "\n", encoding="utf-8")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no structured completion subtest", result.stderr)

    def test_exact_topology_rejects_deleted_and_unexpected_cases(self) -> None:
        results = self.fixture.evidence / "results.tsv"
        subtests = self.fixture.evidence / "subtests.tsv"
        summary = self.fixture.evidence / "summary.txt"

        results.write_text(
            "status\ttest\tdetail\nPASS\tcore\tcase passed\n", encoding="utf-8"
        )
        subtests.write_text(
            "\n".join(
                line
                for line in subtests.read_text(encoding="utf-8").splitlines()
                if "\tcapability:fixture\t" not in line
            )
            + "\n",
            encoding="utf-8",
        )
        text = summary.read_text(encoding="utf-8")
        text = text.replace("pass=2", "pass=1").replace("total=2", "total=1")
        text = text.replace("required_subtest_pass=4", "required_subtest_pass=3")
        text = text.replace("subtest_total=5", "subtest_total=4")
        summary.write_text(text, encoding="utf-8")
        deleted = self.fixture.run()
        self.assertNotEqual(deleted.returncode, 0)
        self.assertIn("authoritative top-level test topology", deleted.stderr)
        self.assertIn("capability:fixture", deleted.stderr)

        self.fixture.write_external_evidence()
        with (self.fixture.evidence / "results.tsv").open("a", encoding="utf-8") as output:
            output.write("PASS\tforged-extra\tforged case\n")
        with (self.fixture.evidence / "subtests.tsv").open("a", encoding="utf-8") as output:
            output.write(
                "PASS\trequired\tforged-extra\tdriver.case-completion\tcase passed\n"
            )
        summary = self.fixture.evidence / "summary.txt"
        text = summary.read_text(encoding="utf-8")
        text = text.replace("pass=2", "pass=3").replace("total=2", "total=3")
        text = text.replace("required_subtest_pass=4", "required_subtest_pass=5")
        text = text.replace("subtest_total=5", "subtest_total=6")
        summary.write_text(text, encoding="utf-8")
        unexpected = self.fixture.run()
        self.assertNotEqual(unexpected.returncode, 0)
        self.assertIn("unexpected=['forged-extra']", unexpected.stderr)

    def test_named_and_unittest_contract_rows_cannot_be_deleted_or_substituted(self) -> None:
        subtests = self.fixture.evidence / "subtests.tsv"
        summary = self.fixture.evidence / "summary.txt"
        rows = [
            line
            for line in subtests.read_text(encoding="utf-8").splitlines()
            if "\tcore\tmandatory-branch\t" not in line
        ]
        subtests.write_text("\n".join(rows) + "\n", encoding="utf-8")
        text = summary.read_text(encoding="utf-8")
        text = text.replace("required_subtest_pass=4", "required_subtest_pass=3")
        text = text.replace("subtest_total=5", "subtest_total=4")
        summary.write_text(text, encoding="utf-8")
        missing = self.fixture.run()
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("required named subtest is absent", missing.stderr)

        self.fixture.write_external_evidence()
        subtests = self.fixture.evidence / "subtests.tsv"
        subtests.write_text(
            subtests.read_text(encoding="utf-8").replace(
                "python.fixture.test_core", "python.fixture.test_substituted"
            ),
            encoding="utf-8",
        )
        substituted = self.fixture.run()
        self.assertNotEqual(substituted.returncode, 0)
        self.assertIn("unittest identity set differs", substituted.stderr)

    def test_portable_allowed_top_level_skip_waives_only_its_nested_contract(self) -> None:
        contract_path = self.fixture.root / "portable-contract.json"
        contract = {
            "expected_diagnostic_subtests": [],
            "portable_allowed_required_skips": [],
            "portable_allowed_top_level_skips": ["host-fixture"],
            "required_named_subtests": [
                {"subtest": "host-only-branch", "test": "host-fixture"}
            ],
            "schema": "gentoo-optimization-phase2-authoritative-test-contract-v1",
            "top_level": {"exact_names": ["host-fixture"], "prefix_groups": []},
            "unittest_suites": [
                {
                    "expected_count": 1,
                    "subtest_names_sha256": digest(b"python.fixture.host_only\n"),
                    "test": "host-fixture",
                }
            ],
        }
        contract_path.write_text(
            json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        results = self.fixture.root / "portable-results.tsv"
        results.write_text(
            "status\ttest\tdetail\nSKIP\thost-fixture\thost primitive absent\n",
            encoding="utf-8",
        )
        subtests = self.fixture.root / "portable-subtests.tsv"
        subtests.write_text(
            "status\trequirement\ttest\tsubtest\tdetail\n"
            "SKIP\trequired\thost-fixture\tdriver.case-completion\t"
            "host primitive absent\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                os.fspath(TOOL),
                "test-contract",
                "--contract",
                os.fspath(contract_path),
                "--results",
                os.fspath(results),
                "--subtests",
                os.fspath(subtests),
                "--mode",
                "portable-complete",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_python_distribution_inventory_binds_all_package_and_metadata_bytes(self) -> None:
        namespace = runpy.run_path(os.fspath(TOOL))
        observe = namespace["observe_python_distribution"]
        with tempfile.TemporaryDirectory(prefix="python-distribution-identity.") as temporary:
            root = Path(temporary)
            package = root / "fixture_package"
            metadata = root / "fixture-1.dist-info"
            package.mkdir()
            metadata.mkdir()
            module = package / "__init__.py"
            module.write_text("VALUE = 1\n", encoding="utf-8")
            metadata_file = metadata / "METADATA"
            metadata_file.write_text(
                "Name: fixture\nVersion: 1\n", encoding="utf-8"
            )
            external_hardlink = root / "module-hardlink"
            os.link(module, external_hardlink)
            probe = {
                "declared_files": [],
                "import_locations": [os.fspath(package)],
                "metadata_path": os.fspath(metadata),
                "name": "fixture",
                "version": "1",
            }
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=canonical(probe).encode("utf-8"),
                stderr=b"",
            )
            with mock.patch.object(namespace["subprocess"], "run", return_value=completed):
                first = observe(
                    Path(sys.executable),
                    Path(sys.executable).resolve(),
                    "fixture",
                    ["fixture_package"],
                    False,
                )
            self.assertEqual(first["entry_count"], 2)
            self.assertEqual(first["import_roots"], ["fixture_package"])
            self.assertEqual(
                sorted(item["path"] for item in first["entries"]),
                sorted([os.fspath(module), os.fspath(metadata_file)]),
            )
            self.assertEqual(
                first["entries_sha256"],
                digest(json.dumps(first["entries"], separators=(",", ":"), sort_keys=True).encode("utf-8")),
            )
            schema = json.loads(
                (
                    REPOSITORY
                    / "optimization/schema/phase2-evidence-index.schema.json"
                ).read_text(encoding="utf-8")
            )
            validate_schema(
                first,
                schema["$defs"]["python_distribution_identity"],
                schema,
            )

    def test_production_capture_rejects_external_policy_before_trust_checks(self) -> None:
        external_policy = self.fixture.root / "weak-policy.json"
        external_policy.write_text(
            (self.fixture.repository / "policy.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        command = self.fixture.command()
        policy_index = command.index("--policy") + 1
        command[policy_index] = os.fspath(external_policy)
        command.append("--production")
        result = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tracked repository evidence policy", result.stderr)

    def test_production_and_portable_indexes_cannot_cross_verification_modes(self) -> None:
        namespace = runpy.run_path(os.fspath(TOOL))
        require_mode = namespace["require_verification_mode"]
        require_boot = namespace["require_verification_boot"]
        current_boot = namespace["current_boot_id"]()
        error_type = namespace["EvidenceError"]
        require_mode(False, False)
        require_mode(True, True)
        with self.assertRaises(error_type):
            require_mode(False, True)
        with self.assertRaises(error_type):
            require_mode(True, False)
        require_boot(current_boot, True)
        require_boot("00000000-0000-0000-0000-000000000000", False)
        with self.assertRaises(error_type):
            require_boot("00000000-0000-0000-0000-000000000000", True)

    def test_containment_executables_reject_privilege_metadata(self) -> None:
        namespace = runpy.run_path(os.fspath(TOOL))
        validate = namespace["validate_live_executable_identity"]
        error_type = namespace["EvidenceError"]

        def identity(path: Path) -> dict[str, object]:
            metadata = path.lstat()
            return {
                "device": metadata.st_dev,
                "gid": metadata.st_gid,
                "inode": metadata.st_ino,
                "mode": metadata.st_mode & 0o7777,
                "nlink": metadata.st_nlink,
                "path": os.fspath(path),
                "sha256": digest(path.read_bytes()),
                "uid": metadata.st_uid,
            }

        self.fixture.real_script.chmod(0o4755)
        with self.assertRaises(error_type):
            validate(identity(self.fixture.real_script), "fixture child", False)
        self.fixture.real_script.chmod(0o700)
        with mock.patch.object(
            namespace["os"], "getxattr", return_value=b"fixture-capability"
        ):
            with self.assertRaises(error_type):
                validate(identity(self.fixture.real_script), "fixture child", False)

    def test_terminal_transaction_partial_variants_are_rejected(self) -> None:
        namespace = runpy.run_path(os.fspath(TOOL))
        require_absent = namespace["require_terminal_transaction_markers_absent"]
        error_type = namespace["EvidenceError"]
        receipt = self.fixture.root / "receipt.json"
        authorization = self.fixture.root / "transaction.authorization"
        journal = self.fixture.root / "transaction.pending"
        markers = (
            receipt.with_name(f"{receipt.name}.partial"),
            receipt.with_name(f"{receipt.name}.interrupted-partial"),
            authorization.with_name(f"{authorization.name}.partial"),
            authorization.with_name(f"{authorization.name}.interrupted-partial"),
            journal,
            journal.with_name(f"{journal.name}.partial"),
            journal.with_name(f"{journal.name}.child.json"),
            journal.with_name(f"{journal.name}.child.json.partial"),
        )
        require_absent(receipt, authorization, journal)
        for marker in markers:
            marker.write_text("partial\n", encoding="utf-8")
            with self.assertRaises(error_type):
                require_absent(receipt, authorization, journal)
            marker.unlink()

    def test_atomic_publication_never_replaces_an_existing_final(self) -> None:
        namespace = runpy.run_path(os.fspath(TOOL))
        atomic_publish = namespace["atomic_publish"]
        error_type = namespace["EvidenceError"]
        destination = self.fixture.root / "already-present.json"
        destination.write_bytes(b"original\n")
        with self.assertRaises(error_type):
            atomic_publish(destination, b"replacement\n", False)
        self.assertEqual(destination.read_bytes(), b"original\n")

    def test_sample_external_receipt_uses_coordinator_pretty_json_contract(self) -> None:
        namespace = runpy.run_path(os.fspath(TOOL))
        validate_external = namespace["validate_component_external_semantics"]
        current_boot_id = namespace["current_boot_id"]()
        pretty = namespace["pretty_json"]
        hash_bytes = namespace["sha256"]
        error_type = namespace["EvidenceError"]
        production_output = self.fixture.evidence / "production-sample-pgo"
        production_output.mkdir()
        publication_path = production_output / "publication-context.tsv"
        receipt_path = self.fixture.root / "transactions/passed.receipt.json"
        child_sidecar_path = production_output / "transaction-child-identity.json"
        coordinator_path = (
            self.fixture.repository
            / "scripts/optimization/pgo/production-profile-lock-transaction.py"
        )
        coordinator_path.parent.mkdir(parents=True)
        shutil.copyfile(
            REPOSITORY
            / "scripts/optimization/pgo/production-profile-lock-transaction.py",
            coordinator_path,
        )
        coordinator_path.chmod(0o755)

        def executable_identity(path: Path) -> dict[str, object]:
            metadata = path.lstat()
            return {
                "device": metadata.st_dev,
                "gid": metadata.st_gid,
                "inode": metadata.st_ino,
                "mode": metadata.st_mode & 0o7777,
                "nlink": metadata.st_nlink,
                "path": os.fspath(path),
                "sha256": digest(path.read_bytes()),
                "uid": metadata.st_uid,
            }

        generation = {
            "generation_id": "phase2-validation-fixture",
            "inventory_id": "phase2-validation-input-fixture",
            "inventory_sha256": "",
        }
        validation_input_payload = (
            f'{{"git_commit":"{self.fixture.commit}",'
            f'"inventory_id":"{generation["inventory_id"]}",'
            '"purpose":"phase2-sample-pgo-validation-only"}\n'
        ).encode("ascii")
        generation["inventory_sha256"] = digest(validation_input_payload)
        validation_input_path = (
            Path("/var/lib/gentoo-optimization/state/project")
            / f"{generation['inventory_id']}.json"
        )
        gate_run_id = "phase2-fixture-20260716T120000Z"
        work_root = Path(
            f"/var/tmp/gentoo-optimization/phase2-sample-work-{gate_run_id}"
        )
        profile_root = Path(
            "/var/cache/gentoo-optimization/pgo/clang-sample"
        ) / f"phase2-sample-gate-{gate_run_id}"
        state_root = (
            Path("/var/lib/gentoo-optimization/generations")
            / generation["generation_id"]
            / f"phase2-sample-gate-{gate_run_id}"
        )
        token_path = state_root / "coordinator-token-scan.tsv"
        child_path = (
            self.fixture.repository
            / "tests/optimization/test-portage-sample-pgo-integration.sh"
        )
        child_path.parent.mkdir(parents=True)
        child_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        child_path.chmod(0o755)
        child_executable = executable_identity(child_path)
        scanner_path = self.fixture.root / "authorization-token-scan.py"
        scanner_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        scanner_path.chmod(0o755)
        scanner_identity = executable_identity(scanner_path)
        unshare_path = self.fixture.root / "unshare"
        unshare_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        unshare_path.chmod(0o755)
        unshare_identity = executable_identity(unshare_path)
        authorization_digest = "c" * 64
        environment = {
            "HOME": "/root",
            "LANG": "C",
            "LC_ALL": "C",
            "LOGNAME": "root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "SHELL": "/bin/bash",
            "TZ": "UTC",
            "USER": "root",
            "GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID": generation[
                "generation_id"
            ],
            "GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID": generation["inventory_id"],
            "GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256": generation[
                "inventory_sha256"
            ],
            "GENTOO_OPT_PRODUCTION_GATE_RUN_ID": gate_run_id,
            "GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT": os.fspath(work_root),
            "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION": os.fspath(
                state_root / "transaction.authorization"
            ),
            "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN_SHA256": authorization_digest,
        }
        token_roots = [
            os.fspath(work_root),
            os.fspath(profile_root),
            os.fspath(state_root),
            os.fspath(publication_path.parent),
        ]
        child_contract = {
            "argv": [
                os.fspath(child_path),
                "--production-locks",
                "--portage-policy",
                "live",
                "--output-dir",
                os.fspath(publication_path.parent),
            ],
            "containment": "pid-namespace-v1",
            "containment_executable": unshare_identity,
            "environment": environment,
            "environment_sha256": hash_bytes(pretty(environment)),
            "evidence_output_root": os.fspath(publication_path.parent),
            "executable": child_executable,
            "token_scan": {
                "executable": scanner_identity,
                "output": os.fspath(token_path),
                "roots": token_roots,
            },
        }
        framework_context = {
            "framework_aggregate_sha256": "1" * 64,
            "git_commit": self.fixture.commit,
            "manifest_path": "/var/lib/gentoo-optimization/framework-fixture/install.manifest",
            "manifest_sha256": "2" * 64,
            "source_aggregate_sha256": "3" * 64,
            "target": "/var/lib/gentoo-optimization/framework-fixture",
        }
        lock_identity = {
            "device": 1,
            "gid": 1,
            "inode": 1,
            "mode": 0o640,
            "nlink": 1,
            "uid": 0,
        }
        lock_identities = {
            name: dict(lock_identity) for name in ("framework", "generation", "project")
        }
        journal = {
            "authorization_token_sha256": authorization_digest,
            "schema": "gentoo-optimization-production-profile-lock-transaction-v1",
            "boot_id": current_boot_id,
            "created_at": "2026-07-16T11:59:00Z",
            "gate_run_id": gate_run_id,
            "generation": generation,
            "test_mode": False,
            "child_contract": child_contract,
            "expected_payload_sha256": hash_bytes(pretty(generation)),
            "framework_context": framework_context,
            "locks": lock_identities,
            "original_payload_sha256": digest(b""),
            "owner": {"pid": 100, "start_ticks": "1"},
            "paths": {
                "framework": "/run/gentoo-optimization/framework-install.lock",
                "generation": "/run/gentoo-optimization/generation.lock",
                "project": "/run/gentoo-optimization/project.lock",
            },
        }
        token_payload = b"passed\t-\n"
        journal_sha = hash_bytes(pretty(journal))
        child_sidecar = {
            "authorization_token_sha256": authorization_digest,
            "boot_id": current_boot_id,
            "child": {"pid": 200, "process_group": 200, "start_ticks": "2"},
            "coordinator_owner": {"pid": 100, "start_ticks": "1"},
            "created_at": "2026-07-16T11:59:01Z",
            "framework_context": framework_context,
            "gate_run_id": gate_run_id,
            "generation": generation,
            "journal_sha256": journal_sha,
            "schema": "gentoo-optimization-production-profile-lock-child-identity-v1",
            "test_mode": False,
        }
        child_sidecar_payload = pretty(child_sidecar)
        receipt = {
            "abandoned_receipt_partial": None,
            "authorization": {
                "abandoned_partial": None,
                "gate_directory_created": True,
                "generation_parent": os.fspath(state_root.parent),
                "generation_parent_gid": 0,
                "generation_parent_mode": 0o755,
                "generation_parent_uid": 0,
                "path": os.fspath(state_root / "transaction.authorization"),
                "sha256": "b" * 64,
            },
            "authorization_token_sha256": authorization_digest,
            "boot_id": current_boot_id,
            "child_exit_status": 0,
            "child_identity_sha256": hash_bytes(child_sidecar_payload),
            "completed_at": "2026-07-16T12:00:00Z",
            "framework_context": framework_context,
            "gate_run_id": gate_run_id,
            "generation": generation,
            "journal_removal_after_receipt_required": True,
            "lock_payload_restored_sha256": digest(b""),
            "locks": lock_identities,
            "schema": "gentoo-optimization-production-profile-lock-receipt-v1",
            "status": "passed",
            "token_scan": {
                "output": os.fspath(token_path),
                "output_sha256": digest(token_payload),
                "roots": token_roots,
                "scanner_executable_sha256": scanner_identity["sha256"],
                "scanner_status": 0,
            },
            "transaction_journal": journal,
            "transaction_journal_sha256": journal_sha,
        }
        publication_payload = (
            f"authoritative_work_root\t{work_root}\n"
            f"published_copy\t{publication_path.parent}\n"
            "portage_policy_mode\tlive\n"
            "published_copy_semantics\thistorical-byte-evidence; validator sidecars remain bound to authoritative paths\n"
            "authoritative_work_final_identity\troot:portage:0750\n"
            f"production_work_root\t{work_root}\n"
            f"profile_artifact_root\t{profile_root}\n"
            f"generation_state_root\t{state_root}\n"
        ).encode()
        payloads = {
            "production-child-identity": child_sidecar_payload,
            "production-publication-context": publication_payload,
            "production-token-scan": token_payload,
            "production-transaction-receipt": pretty(receipt),
            "production-validation-input": validation_input_payload,
        }
        paths = {
            "production-child-identity": child_sidecar_path,
            "production-publication-context": publication_path,
            "production-token-scan": token_path,
            "production-transaction-receipt": receipt_path,
            "production-validation-input": validation_input_path,
        }
        validate_external(
            "sample-pgo",
            gate_run_id,
            payloads,
            paths,
            self.fixture.evidence,
            {"commit": self.fixture.commit, "root": os.fspath(self.fixture.repository)},
            current_boot_id,
            False,
        )
        payloads["production-validation-input"] = (
            validation_input_payload.replace(b"validation-only", b"tampered-value")
        )
        with self.assertRaises(error_type):
            validate_external(
                "sample-pgo",
                gate_run_id,
                payloads,
                paths,
                self.fixture.evidence,
                {"commit": self.fixture.commit, "root": os.fspath(self.fixture.repository)},
                current_boot_id,
                False,
            )
        payloads["production-validation-input"] = validation_input_payload
        bad_receipts: list[dict[str, object]] = []
        for mutation in (
            "test-mode",
            "containment",
            "child-hash",
            "argv",
            "framework",
            "authorization",
            "locks",
            "containment-executable",
            "scanner",
        ):
            bad = copy.deepcopy(receipt)
            if mutation == "test-mode":
                bad["transaction_journal"]["test_mode"] = True
            elif mutation == "containment":
                bad["transaction_journal"]["child_contract"]["containment"] = (
                    "direct-test-v1"
                )
            elif mutation == "child-hash":
                bad["transaction_journal"]["child_contract"]["executable"][
                    "sha256"
                ] = "0" * 64
            elif mutation == "argv":
                bad["transaction_journal"]["child_contract"]["argv"].remove(
                    "--production-locks"
                )
            elif mutation == "framework":
                bad["framework_context"]["git_commit"] = "0" * 40
            elif mutation == "authorization":
                bad["authorization"] = {}
            elif mutation == "locks":
                bad["locks"] = {}
            elif mutation == "containment-executable":
                bad["transaction_journal"]["child_contract"][
                    "containment_executable"
                ]["sha256"] = "0" * 64
            else:
                bad["transaction_journal"]["child_contract"]["token_scan"][
                    "executable"
                ]["sha256"] = "0" * 64
                bad["token_scan"]["scanner_executable_sha256"] = "0" * 64
            bad["transaction_journal_sha256"] = hash_bytes(
                pretty(bad["transaction_journal"])
            )
            bad_receipts.append(bad)
        for bad in bad_receipts:
            payloads["production-transaction-receipt"] = pretty(bad)
            with self.assertRaises(error_type):
                validate_external(
                    "sample-pgo",
                    gate_run_id,
                    payloads,
                    paths,
                    self.fixture.evidence,
                    {
                        "commit": self.fixture.commit,
                        "root": os.fspath(self.fixture.repository),
                    },
                    current_boot_id,
                    False,
                )
        payloads["production-transaction-receipt"] = pretty(receipt)
        bad_sidecar = copy.deepcopy(child_sidecar)
        bad_sidecar["created_at"] = "2026-07-16T11:59:02Z"
        payloads["production-child-identity"] = pretty(bad_sidecar)
        with self.assertRaises(error_type):
            validate_external(
                "sample-pgo",
                gate_run_id,
                payloads,
                paths,
                self.fixture.evidence,
                {
                    "commit": self.fixture.commit,
                    "root": os.fspath(self.fixture.repository),
                },
                current_boot_id,
                False,
            )
        payloads["production-child-identity"] = child_sidecar_payload
        bad_process_sidecar = copy.deepcopy(child_sidecar)
        bad_process_sidecar["child"]["process_group"] = (
            bad_process_sidecar["child"]["pid"] + 1
        )
        bad_process_payload = pretty(bad_process_sidecar)
        bad_process_receipt = copy.deepcopy(receipt)
        bad_process_receipt["child_identity_sha256"] = hash_bytes(
            bad_process_payload
        )
        payloads["production-child-identity"] = bad_process_payload
        payloads["production-transaction-receipt"] = pretty(
            bad_process_receipt
        )
        with self.assertRaises(error_type):
            validate_external(
                "sample-pgo",
                gate_run_id,
                payloads,
                paths,
                self.fixture.evidence,
                {"commit": self.fixture.commit, "root": os.fspath(self.fixture.repository)},
                current_boot_id,
                False,
            )
        payloads["production-child-identity"] = child_sidecar_payload
        payloads["production-transaction-receipt"] = pretty(receipt)
        wrong_paths = dict(paths)
        wrong_paths["production-child-identity"] = (
            self.fixture.root / "wrong-child-identity.json"
        )
        with self.assertRaises(error_type):
            validate_external(
                "sample-pgo",
                gate_run_id,
                payloads,
                wrong_paths,
                self.fixture.evidence,
                {"commit": self.fixture.commit, "root": os.fspath(self.fixture.repository)},
                current_boot_id,
                False,
            )
        bad_publication = publication_payload.replace(
            f"profile_artifact_root\t{profile_root}".encode(),
            b"profile_artifact_root\t/var/cache/wrong",
        )
        payloads["production-publication-context"] = bad_publication
        with self.assertRaises(error_type):
            validate_external(
                "sample-pgo",
                gate_run_id,
                payloads,
                paths,
                self.fixture.evidence,
                {"commit": self.fixture.commit, "root": os.fspath(self.fixture.repository)},
                current_boot_id,
                False,
            )
        payloads["production-publication-context"] = publication_payload
        payloads["production-publication-context"] = (
            publication_payload + b"unexpected\tfield\n"
        )
        with self.assertRaises(error_type):
            validate_external(
                "sample-pgo",
                gate_run_id,
                payloads,
                paths,
                self.fixture.evidence,
                {"commit": self.fixture.commit, "root": os.fspath(self.fixture.repository)},
                current_boot_id,
                False,
            )
        payloads["production-publication-context"] = publication_payload
        payloads["production-transaction-receipt"] = json.dumps(
            receipt, sort_keys=True, separators=(",", ":")
        ).encode()
        with self.assertRaises(error_type):
            validate_external(
                "sample-pgo",
                gate_run_id,
                payloads,
                paths,
                self.fixture.evidence,
                {"commit": self.fixture.commit, "root": os.fspath(self.fixture.repository)},
                current_boot_id,
                False,
            )

    def test_component_run_directory_is_immutable_and_exhaustive(self) -> None:
        self.fixture.run(check=True)
        (self.fixture.state_directory / "unreviewed.json").write_text(
            "{}\n", encoding="utf-8"
        )
        result = self.fixture.run("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("component-state run directory", result.stderr)

    def test_relocated_index_copy_cannot_become_authoritative(self) -> None:
        self.fixture.run(check=True)
        relocated_directory = self.fixture.root / "relocated-index"
        relocated_directory.mkdir()
        relocated = relocated_directory / "index.json"
        shutil.copyfile(self.fixture.index, relocated)
        result = subprocess.run(
            [sys.executable, os.fspath(TOOL), "verify", "--index", os.fspath(relocated)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("policy-pinned run path", result.stderr)

    def test_boot_and_git_provenance_tampering_block_capture(self) -> None:
        provenance_path = self.fixture.evidence / "test-run-provenance.json"
        document = json.loads(provenance_path.read_text(encoding="utf-8"))
        document["boot_id"] = "00000000-0000-0000-0000-000000000000"
        provenance_path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required boot", result.stderr)

        self.fixture.write_external_evidence()
        document = json.loads(provenance_path.read_text(encoding="utf-8"))
        document["git_requested_path"] = os.fspath(self.fixture.real_script)
        document["git_resolved_path"] = os.fspath(self.fixture.real_script)
        provenance_path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Git entry point", result.stderr)

        self.fixture.write_external_evidence()
        document = json.loads(provenance_path.read_text(encoding="utf-8"))
        document["driver"]["stat"]["inode"] += 1
        provenance_path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("provenance driver", result.stderr)

    def test_pending_test_run_cannot_finalize_with_another_boot_identity(self) -> None:
        pending = self.fixture.root / "second.pending.json"
        output = self.fixture.root / "second.provenance.json"
        start = subprocess.run(
            [
                sys.executable,
                os.fspath(TOOL),
                "run-provenance-start",
                "--repository-root",
                os.fspath(self.fixture.repository),
                "--driver",
                os.fspath(self.fixture.repository / "test-driver.sh"),
                "--git",
                os.fspath(self.fixture.git),
                "--output",
                os.fspath(pending),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(start.returncode, 0, start.stderr)
        document = json.loads(pending.read_text(encoding="utf-8"))
        document["boot_id"] = "00000000-0000-0000-0000-000000000000"
        pending.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        finish = subprocess.run(
            [
                sys.executable,
                os.fspath(TOOL),
                "run-provenance-finish",
                "--pending",
                os.fspath(pending),
                "--results",
                os.fspath(self.fixture.evidence / "results.tsv"),
                "--subtests",
                os.fspath(self.fixture.evidence / "subtests.tsv"),
                "--summary",
                os.fspath(self.fixture.evidence / "summary.txt"),
                "--output",
                os.fspath(output),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(finish.returncode, 0)
        self.assertIn("after a reboot", finish.stderr)
        self.assertFalse(output.exists())

    def test_dirty_and_untracked_worktrees_block_capture(self) -> None:
        (self.fixture.repository / "untracked").write_text("no\n", encoding="utf-8")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clean worktree", result.stderr)

    def test_documented_head_bundle_advertises_exact_commit(self) -> None:
        bundle = self.fixture.root / "source.bundle"
        self.fixture.run_git("bundle", "create", os.fspath(bundle), "HEAD")
        heads = subprocess.run(
            [os.fspath(self.fixture.git), "bundle", "list-heads", os.fspath(bundle)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.splitlines()
        self.assertEqual(heads, [f"{self.fixture.commit} HEAD"])
        self.fixture.run_git("bundle", "verify", os.fspath(bundle))

    def test_plan_marker_generator_covers_multiple_checked_lines(self) -> None:
        plan = self.fixture.repository / "plan.md"
        text = plan.read_text(encoding="utf-8")
        text = text.replace(
            "# 12. Fixture Phase 3",
            "- [x] Second exact fixture claim\n\n# 12. Fixture Phase 3",
        )
        plan.write_text(text, encoding="utf-8")
        checked_lines = [
            index
            for index, line in enumerate(text.splitlines(), 1)
            if line.startswith("- [x]")
        ]
        command = [
            sys.executable,
            os.fspath(TOOL),
            "plan-marker",
            "--repository-root",
            os.fspath(self.fixture.repository),
            "--policy",
            "policy.json",
            "--claim-id",
            "fixture-claim",
        ]
        for line_number in reversed(checked_lines):
            command.extend(("--checkbox-line", str(line_number)))
        result = subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        fragment = result.stdout.strip()[len(MARKER_PREFIX) : -len(MARKER_SUFFIX)]
        marker = json.loads(fragment)
        self.assertEqual(
            marker["checkbox_sha256"],
            sorted(digest(text.splitlines()[line - 1].encode()) for line in checked_lines),
        )
        self.assertEqual(
            result.stdout.strip(), f"{MARKER_PREFIX}{canonical(marker)}{MARKER_SUFFIX}"
        )

    def test_unlabelled_standalone_historical_hash_is_rejected(self) -> None:
        namespace = runpy.run_path(os.fspath(TOOL))
        load_policy = namespace["load_policy"]
        plan_claims = namespace["plan_claims"]
        error_type = namespace["EvidenceError"]
        plan = self.fixture.repository / "plan.md"
        text = plan.read_text(encoding="utf-8").replace(
            "# 12. Fixture Phase 3",
            f"Historical implementation digest: {'a' * 64}\n\n"
            "# 12. Fixture Phase 3",
        )
        plan.write_text(text, encoding="utf-8")
        policy = load_policy(
            self.fixture.repository, self.fixture.repository / "policy.json"
        )
        with self.assertRaises(error_type):
            plan_claims(
                plan,
                policy,
                {
                    "src/code.py": digest(
                        (self.fixture.repository / "src/code.py").read_bytes()
                    )
                },
            )

    def test_component_test_requirements_must_exist_in_aggregate_policy(self) -> None:
        namespace = runpy.run_path(os.fspath(TOOL))
        load_policy = namespace["load_policy"]
        error_type = namespace["EvidenceError"]
        policy_path = self.fixture.repository / "policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["required_component_states"][0]["required_test_names"].append(
            "unreviewed-pass-row"
        )
        policy["required_component_states"][0]["required_test_names"].sort()
        policy_path.write_text(
            json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with self.assertRaises(error_type):
            load_policy(self.fixture.repository, policy_path)

    def test_stale_checked_plan_source_hash_blocks_capture(self) -> None:
        (self.fixture.repository / "src/code.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.fixture.run_git("add", "src/code.py")
        self.fixture.run_git("commit", "-q", "-m", "change source without marker")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checked plan evidence is stale", result.stderr)

    def test_old_results_cannot_be_reused_for_a_new_clean_commit(self) -> None:
        source = self.fixture.repository / "src/code.py"
        source.write_text("VALUE = 3\n", encoding="utf-8")
        plan = self.fixture.repository / "plan.md"
        lines = plan.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line.startswith(MARKER_PREFIX):
                marker = json.loads(line[len(MARKER_PREFIX) : -len(MARKER_SUFFIX)])
                marker["source_sha256"]["src/code.py"] = digest(source.read_bytes())
                lines[index] = f"{MARKER_PREFIX}{canonical(marker)}{MARKER_SUFFIX}"
        plan.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.fixture.run_git("add", "src/code.py", "plan.md")
        self.fixture.run_git("commit", "-q", "-m", "new source and current marker")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test-run provenance belongs to another commit", result.stderr)

    def test_unmarked_checked_phase2_claim_cannot_authorize_capture(self) -> None:
        plan = self.fixture.repository / "plan.md"
        text = plan.read_text(encoding="utf-8")
        text = text.replace(
            "# 12. Fixture Phase 3",
            "- [x] Unmarked stale Phase 2 claim\n\n# 12. Fixture Phase 3",
        )
        plan.write_text(text, encoding="utf-8")
        self.fixture.run_git("add", "plan.md")
        self.fixture.run_git("commit", "-q", "-m", "unmarked checked claim")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("every checked Phase 2 checkbox", result.stderr)

    def test_post_capture_plan_edit_invalidates_without_a_hash_fixed_point(self) -> None:
        self.fixture.run(check=True)
        index_before = self.fixture.index.read_bytes()
        plan = self.fixture.repository / "plan.md"
        plan.write_text(plan.read_text(encoding="utf-8") + "\nPost-capture note.\n", encoding="utf-8")
        result = self.fixture.run("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.fixture.index.read_bytes(), index_before)
        index_document = json.loads(index_before)
        self.assertNotIn("index_sha256", index_document["plan"])
        self.assertNotIn("evidence_manifest_sha256", index_document["plan"])

    def test_evidence_and_component_state_tampering_are_rejected(self) -> None:
        self.fixture.run(check=True)
        (self.fixture.evidence / "results.tsv").write_text("tampered\n", encoding="utf-8")
        result = self.fixture.run("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test results", result.stderr)

        self.fixture.index.unlink()
        self.fixture.write_external_evidence()
        self.fixture.run(check=True)
        state = json.loads(self.fixture.state.read_text(encoding="utf-8"))
        state["pending_total"] = 1
        self.fixture.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result = self.fixture.run("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deterministic projection", result.stderr)

    def test_requested_tool_symlink_retarget_is_rejected(self) -> None:
        self.fixture.run(check=True)
        replacement = self.fixture.bin / "replacement-tool"
        replacement.write_text(
            "#!/bin/sh\nprintf 'fixture-tool 1.0\\n'\n", encoding="utf-8"
        )
        replacement.chmod(0o700)
        self.fixture.script_link.unlink()
        self.fixture.script_link.symlink_to(replacement.name)
        result = self.fixture.run("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("indexed tool topology", result.stderr)

    def test_detached_index_cannot_choose_its_own_tool_topology_or_specification(self) -> None:
        mutations = {
            "deleted": lambda tools: tools[:-1],
            "duplicated": lambda tools: [*tools, copy.deepcopy(tools[-1])],
            "requested-path-substitution": lambda tools: [
                {
                    **item,
                    "requested_path": os.fspath(self.fixture.real_script),
                    "version_argv": [
                        os.fspath(self.fixture.real_script),
                        *item["version_argv"][1:],
                    ],
                }
                if item["name"] == "script-tool"
                else item
                for item in tools
            ],
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                if self.fixture.index.exists():
                    self.fixture.index.unlink()
                self.fixture.write_external_evidence()
                self.fixture.run(check=True)
                document = json.loads(
                    self.fixture.index.read_text(encoding="utf-8")
                )
                document["tools"] = mutate(document["tools"])
                self.fixture.index.write_text(
                    json.dumps(document, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                result = self.fixture.run("verify")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("indexed tool topology", result.stderr)

    def test_unverifiable_timestamp_and_forged_driver_stat_are_rejected(self) -> None:
        self.fixture.run(check=True)
        document = json.loads(self.fixture.index.read_text(encoding="utf-8"))
        document["captured_at"] = "2099-01-01T00:00:00Z"
        self.fixture.index.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result = self.fixture.run("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("keys differ", result.stderr)

        del document["captured_at"]
        document["test_run"]["driver"]["stat"]["inode"] += 1
        self.fixture.index.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result = self.fixture.run("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test driver identity", result.stderr)


if __name__ == "__main__":
    unittest.main()

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
import stat
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Callable, cast
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
            "authoritative_test_path": [os.fspath(self.bin)],
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
            "required_tools": ["false", "git", "python3", "script-tool"],
            "schema": POLICY_SCHEMA,
            "source_scopes": ["plan.md", "policy.json", "src", "test-contract.json", "test-driver.sh"],
            "test_driver_path": "test-driver.sh",
            "test_execution_tools": ["git", "python3", "script-tool"],
            "tool_manifest_template_path": "tools-template.json",
        }
        tools_template = {
            "schema": TOOL_SCHEMA,
            "tools": [
                {
                    "name": "false",
                    "path": "/bin/false",
                    "version_args": ["--version"],
                    "version_returncodes": [1],
                },
                {"name": "git", "path": os.fspath(self.git), "version_args": ["--version"]},
                {
                    "name": "python3",
                    "path": os.fspath(Path(sys.executable)),
                    "version_args": ["--version"],
                },
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
                "--policy",
                "policy.json",
                "--git",
                os.fspath(self.git),
                "--executed-tool",
                f"git={self.git}",
                "--executed-tool",
                f"python3={Path(sys.executable)}",
                "--executed-tool",
                f"script-tool={self.script_link}",
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
    def test_candidate_b_commands_bind_their_active_python_runtime(self) -> None:
        manifest = json.loads(
            (REPOSITORY / "optimization/phase2-tool-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        reviewed_python = next(
            item for item in manifest["tools"] if item["name"] == "python3"
        )
        probe = """
import json
import pathlib
import runpy
import sys

namespace = runpy.run_path(sys.argv[1])
specification = json.loads(sys.argv[2])
specification["path"] = pathlib.Path(specification["path"])
observed = namespace["observe_tool"](specification, False)
namespace["require_active_python_matches_reviewed_tools"](
    [observed], "fixture Candidate-B command", False
)
"""
        result = subprocess.run(
            [
                reviewed_python["path"],
                "-I",
                "-B",
                "-c",
                probe,
                os.fspath(TOOL),
                json.dumps(reviewed_python, sort_keys=True),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        namespace = runpy.run_path(os.fspath(TOOL))
        require_runtime = namespace["require_active_python_matches_reviewed_tools"]
        reviewed_specification = copy.deepcopy(reviewed_python)
        reviewed_specification["path"] = Path(reviewed_specification["path"])
        observed = namespace["observe_tool"](reviewed_specification, False)
        broken = copy.deepcopy(observed)
        broken["requested_path"] = "/usr/bin/false"
        broken["resolved_path"] = "/usr/bin/false"
        with self.assertRaisesRegex(
            namespace["EvidenceError"],
            "Python runtime probe exited",
        ):
            require_runtime([broken], "fixture Candidate-B command", False)

    def test_repository_identity_bounds_attached_and_detached_head_queries(self) -> None:
        fixture = EvidenceFixture()
        self.addCleanup(fixture.cleanup)
        namespace = runpy.run_path(os.fspath(TOOL))
        repository_identity = namespace["repository_identity"]

        attached = repository_identity(fixture.git, fixture.repository)
        self.assertEqual(
            attached["head_ref"],
            fixture.run_git("symbolic-ref", "-q", "HEAD").stdout.strip(),
        )

        fixture.run_git("checkout", "--detach", "-q")
        detached = repository_identity(fixture.git, fixture.repository)
        self.assertIsNone(detached["head_ref"])

    def setUp(self) -> None:
        self.fixture = EvidenceFixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def automation_external_fixture(self) -> dict[str, Any]:
        namespace = runpy.run_path(os.fspath(TOOL))

        def pretty(value: object) -> bytes:
            return cast(bytes, namespace["pretty_json"](value))

        def write_pretty(path: Path, value: object, mode: int = 0o600) -> bytes:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = pretty(value)
            path.write_bytes(payload)
            path.chmod(mode)
            return payload

        def write_jq(path: Path, value: object, mode: int = 0o600) -> bytes:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = cast(bytes, namespace["jq_pretty_json"](value))
            path.write_bytes(payload)
            path.chmod(mode)
            return payload

        def identity(path: Path) -> dict[str, object]:
            metadata = path.lstat()
            return {
                "path": os.fspath(path),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
                "mode": stat.S_IMODE(metadata.st_mode),
                "nlink": metadata.st_nlink,
                "size": metadata.st_size,
                "sha256": digest(path.read_bytes()),
            }

        recovery = self.fixture.repository / "scripts/optimization/recovery"
        recovery.mkdir(parents=True)
        source_payloads = {
            "create-binpkg-checkpoint.sh": b"#!/usr/bin/bash\necho checkpoint\n",
            "publish-jsonschema-prerequisite-bootstrap.py": b"#!/usr/bin/python3\nprint('publisher')\n",
            "install-jsonschema-prerequisite.py": b"#!/usr/bin/python3\nprint('helper')\n",
            "verify-binpkg-snapshot.py": b"#!/usr/bin/python3\nprint('verifier')\n",
        }
        for name, payload in source_payloads.items():
            source = recovery / name
            source.write_bytes(payload)
            source.chmod(0o755)
        self.fixture.run_git("add", "scripts/optimization/recovery")
        self.fixture.run_git("commit", "-q", "-m", "Candidate A bootstrap payloads")
        candidate_a = self.fixture.run_git("rev-parse", "HEAD").stdout.strip()
        candidate_a_tree = self.fixture.run_git(
            "show", "-s", "--format=%T", candidate_a
        ).stdout.strip()
        (self.fixture.repository / "candidate-b-marker.txt").write_text(
            "truthful Candidate B claims\n", encoding="utf-8"
        )
        self.fixture.run_git("add", "candidate-b-marker.txt")
        self.fixture.run_git("commit", "-q", "-m", "Candidate B claims")
        candidate_b = self.fixture.run_git("rev-parse", "HEAD").stdout.strip()

        bootstrap = self.fixture.root / "bootstrap" / f"jsonschema-prerequisite-{candidate_a}"
        bootstrap.mkdir(parents=True)
        bootstrap.chmod(0o700)
        file_rows: list[dict[str, object]] = []
        prerequisite_payloads = {
            name: payload
            for name, payload in source_payloads.items()
            if name != "create-binpkg-checkpoint.sh"
        }
        for name in sorted(prerequisite_payloads):
            relative = f"scripts/optimization/recovery/{name}"
            source = self.fixture.repository / relative
            published = bootstrap / name
            shutil.copyfile(source, published)
            published.chmod(0o755)
            raw = self.fixture.run_git(
                "ls-tree", candidate_a, "--", relative
            ).stdout.strip()
            header, observed_path = raw.split("\t", 1)
            mode, object_type, object_id = header.split()
            self.assertEqual(object_type, "blob")
            self.assertEqual(observed_path, relative)
            file_rows.append(
                {
                    "relative": name,
                    "git": {
                        "path": relative,
                        "mode": mode,
                        "blob_oid": object_id,
                        "blob_size": len(source_payloads[name]),
                        "blob_sha256": digest(source_payloads[name]),
                    },
                    "source": identity(source),
                    "published": identity(published),
                }
            )
        python = Path(sys.executable).resolve(strict=True)
        git_config = self.fixture.repository / ".git/config"
        repository_metadata = self.fixture.repository.lstat()
        bootstrap_manifest = {
            "schema": "gentoo-optimization-jsonschema-prerequisite-bootstrap-v1",
            "commit": candidate_a,
            "tree": candidate_a_tree,
            "repository_root": os.fspath(self.fixture.repository),
            "repository_root_identity": {
                "device": repository_metadata.st_dev,
                "inode": repository_metadata.st_ino,
                "uid": repository_metadata.st_uid,
                "gid": repository_metadata.st_gid,
                "mode": stat.S_IMODE(repository_metadata.st_mode),
            },
            "repository_git_config": identity(git_config),
            "python": identity(python),
            "destination": os.fspath(bootstrap),
            "files": file_rows,
        }
        bootstrap_manifest_path = bootstrap / "bootstrap-manifest.json"
        bootstrap_manifest_payload = write_pretty(
            bootstrap_manifest_path, bootstrap_manifest
        )
        checkpoint_bootstrap = bootstrap.with_name(
            f"binpkg-checkpoint-{candidate_a}"
        )
        checkpoint_bootstrap.mkdir(mode=0o700)
        for name in ("create-binpkg-checkpoint.sh", "verify-binpkg-snapshot.py"):
            shutil.copyfile(recovery / name, checkpoint_bootstrap / name)
            (checkpoint_bootstrap / name).chmod(0o755)
        fixture_tool_root = self.fixture.root / "checkpoint-tools"
        fixture_tool_root.mkdir(mode=0o700)
        fixture_tools = {
            name: fixture_tool_root / name
            for name in (
                "emerge",
                "emerge-implementation",
                "python3.15",
                "qcheck",
                "portageq",
                "unshare",
                "zstd",
            )
        }
        for name, tool_path in fixture_tools.items():
            tool_path.write_text(f"fixture checkpoint tool: {name}\n", encoding="utf-8")
            tool_path.chmod(0o755)

        state_parent = self.fixture.root / "state/project"
        reports = self.fixture.root / "reports"
        selector_parent = self.fixture.root / "binpkgs"
        durable_parent = self.fixture.root / "durable"
        for directory in (state_parent, reports, selector_parent, durable_parent):
            directory.mkdir(parents=True, exist_ok=True)
        selector = selector_parent / "critical-current"
        old_durable = durable_parent / "critical-before-pre"
        old_durable.mkdir()
        (old_durable / "Packages").write_text("old\n", encoding="utf-8")
        snapshot_members: dict[Path, list[str]] = {
            old_durable: [f"fixture/base-{index + 1}" for index in range(9)]
        }

        def operator_manifest(root: Path) -> tuple[Path, bytes]:
            manifest_path = root / "operator-evidence.manifest.json"
            rows: list[dict[str, object]] = []

            def visit(directory: Path) -> None:
                for child in sorted(directory.iterdir(), key=lambda item: item.name):
                    relative = child.relative_to(root).as_posix()
                    if child == manifest_path:
                        continue
                    metadata = child.lstat()
                    row: dict[str, object] = {
                        "path": relative,
                        "uid": metadata.st_uid,
                        "gid": metadata.st_gid,
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "nlink": metadata.st_nlink,
                    }
                    if child.is_symlink():
                        row.update(type="symlink", target=os.readlink(child))
                    elif child.is_dir():
                        row["type"] = "directory"
                        rows.append(row)
                        visit(child)
                        continue
                    else:
                        payload = child.read_bytes()
                        row.update(type="file", size=len(payload), sha256=digest(payload))
                    rows.append(row)

            visit(root)
            return manifest_path, write_pretty(
                manifest_path, {"schema_version": 1, "rows": rows}
            )

        def checkpoint(
            checkpoint_id: str,
            *,
            live_cpvs: int,
            delta_cpvs: list[str],
            source_durable: Path,
            witness_target: Path,
            operator_files: dict[str, bytes],
        ) -> dict[str, Any]:
            report = reports / f"checkpoint-{checkpoint_id}"
            report.mkdir()
            durable = durable_parent / f"critical-{checkpoint_id}"
            cache = selector_parent / f"snapshot-{checkpoint_id}"
            durable.mkdir()
            cache.mkdir()
            durable.chmod(0o700)
            cache.chmod(0o700)
            snapshot_cpvs = sorted(set(snapshot_members[source_durable]) | set(delta_cpvs))
            self.assertEqual(len(snapshot_cpvs), live_cpvs)
            snapshot_members[cache] = snapshot_cpvs
            snapshot_members[durable] = snapshot_cpvs
            archive_rows: list[dict[str, object]] = []
            packages_records: list[tuple[str, str, bytes]] = []
            for index, cpv in enumerate(snapshot_cpvs, 1):
                relative = f"archives/{digest(cpv.encode())}.gpkg.tar"
                archive_payload = f"fixture-gpkg:{cpv}\n".encode()
                packages_records.append((cpv, relative, archive_payload))
                for snapshot in (cache, durable):
                    archive_path = snapshot / relative
                    archive_path.parent.mkdir(mode=0o755, exist_ok=True)
                    archive_path.write_bytes(archive_payload)
                    archive_path.chmod(0o644)
                archive_rows.append(
                    {
                        "cpv": cpv,
                        "exists": True,
                        "gpkg": {
                            "image_tar_zst_streams": 1,
                            "manifest_members_verified": 4,
                            "status": "verified",
                            "zstd_streams_tested": 1,
                        },
                        "md5": {
                            "actual": hashlib.md5(archive_payload).hexdigest(),
                            "expected": hashlib.md5(archive_payload).hexdigest(),
                        },
                        "path": relative,
                        "record": index + 1,
                        "regular": True,
                        "sha1": {
                            "actual": hashlib.sha1(archive_payload).hexdigest(),
                            "expected": hashlib.sha1(archive_payload).hexdigest(),
                        },
                        "size": {
                            "actual": len(archive_payload),
                            "expected": str(len(archive_payload)),
                        },
                    }
                )
            packages_payload = (
                f"PACKAGES: {live_cpvs}\nVERSION: 0\n\n"
                + "\n\n".join(
                    "\n".join(
                        (
                            f"CPV: {cpv}",
                            f"PATH: {relative}",
                            f"SIZE: {len(archive_payload)}",
                            f"MD5: {hashlib.md5(archive_payload).hexdigest()}",
                            f"SHA1: {hashlib.sha1(archive_payload).hexdigest()}",
                        )
                    )
                    for cpv, relative, archive_payload in packages_records
                )
                + "\n"
            ).encode()
            for snapshot in (cache, durable):
                (snapshot / "Packages").write_bytes(packages_payload)
                (snapshot / "Packages").chmod(0o644)
            if selector.exists() or selector.is_symlink():
                if not selector.is_symlink() or selector.resolve(strict=True) != witness_target:
                    selector.unlink()
                    selector.symlink_to(witness_target)
            else:
                selector.symlink_to(witness_target)
            old_selector_identity = cast(
                str,
                namespace["checkpoint_selector_identity"](
                    selector, "fixture source selector"
                ),
            )
            tool_manifest_path = report / "tool-identities.tsv"
            tool_lines = [
                "logical_path\tresolved_path\tlogical_stat\tsha256\tsymlink_chain"
            ]
            for tool_path in (
                checkpoint_bootstrap / "create-binpkg-checkpoint.sh",
                checkpoint_bootstrap / "verify-binpkg-snapshot.py",
                *fixture_tools.values(),
            ):
                tool_lines.append(
                    "\t".join(
                        (
                            os.fspath(tool_path),
                            os.fspath(tool_path),
                            cast(str, namespace["gnu_stat_fields"](tool_path)),
                            digest(tool_path.read_bytes()),
                            "-",
                        )
                    )
                )
            tool_identity_lines = {
                line.split("\t", 1)[0]: line for line in tool_lines[1:]
            }
            tool_manifest_path.write_text("\n".join(tool_lines) + "\n", encoding="utf-8")
            tool_manifest_path.chmod(0o600)
            report_counts = {
                "errors": 0,
                "extra_indexed_archives": 0,
                "gpkg_archives_found": live_cpvs,
                "gpkg_archives_indexed": live_cpvs,
                "gpkg_archives_validated": live_cpvs,
                "image_tar_zst_streams_tested": live_cpvs,
                "indexed_records": live_cpvs,
                "indexed_unique_cpvs": live_cpvs,
                "indexed_unique_paths": live_cpvs,
                "live_cpvs": live_cpvs,
                "missing_live_cpvs": 0,
                "unindexed_gpkg_archives": 0,
            }
            report_coverage = {
                "duplicate_live_cpvs": {},
                "extra_indexed_archives": [],
                "missing_live_cpvs": [],
                "unindexed_gpkg_archives": [],
            }
            for prefix, snapshot in (("cache-final", cache), ("durable-final", durable)):
                verification = {
                    "archives": archive_rows,
                    "counts": report_counts,
                    "coverage": report_coverage,
                    "inputs": {
                        "allow_extra_archives": False,
                        "packages_index": os.fspath(snapshot / "Packages"),
                        "snapshot": os.fspath(snapshot),
                        "validate_gpkg": True,
                        "vdb": os.fspath(self.fixture.root / "vdb"),
                        "zstd": "/usr/bin/zstd",
                    },
                    "issues": [],
                    "schema_version": 1,
                    "status": "pass",
                }
                write_pretty(report / f"{prefix}-verification.json", verification)
                (report / f"{prefix}-packages.sha256").write_text(
                    f"{digest(packages_payload)}  {snapshot / 'Packages'}\n",
                    encoding="utf-8",
                )
                archive_manifest = "cpv\trelative_path\tsize\tsha256\n" + "".join(
                    f"{cpv}\t{relative}\t{len(data)}\t{digest(data)}\n"
                    for cpv, relative, data in packages_records
                )
                (report / f"{prefix}-archives.tsv").write_text(
                    archive_manifest, encoding="utf-8"
                )
                for suffix in ("packages.sha256", "archives.tsv"):
                    (report / f"{prefix}-{suffix}").chmod(0o600)
            delta_path = report / "source-final-preactivation-verification.requested-delta-cpvs.txt"
            delta_payload = ("\n".join(delta_cpvs) + "\n").encode()
            delta_path.write_bytes(delta_payload)
            delta_path.chmod(0o600)
            artifact_preparation = report / "artifact-preparation-state.json"
            evidence_manifest_path = report / "evidence-manifest.sha256"
            source_packages_manifest = report / "source-packages.sha256"
            source_packages_manifest.write_text(
                f"{digest((source_durable / 'Packages').read_bytes())}  "
                f"{source_durable / 'Packages'}\n",
                encoding="utf-8",
            )
            source_packages_manifest.chmod(0o600)
            creation_files = sorted(
                [
                    tool_manifest_path,
                    source_packages_manifest,
                    *(
                        report / name
                        for name in (
                            "cache-final-verification.json",
                            "cache-final-packages.sha256",
                            "cache-final-archives.tsv",
                            "durable-final-verification.json",
                            "durable-final-packages.sha256",
                            "durable-final-archives.tsv",
                        )
                    ),
                ],
                key=os.fspath,
            )
            evidence_manifest_path.write_text(
                "".join(
                    f"{digest(item.read_bytes())}  {item}\n" for item in creation_files
                ),
                encoding="utf-8",
            )
            evidence_manifest_path.chmod(0o600)
            write_jq(
                artifact_preparation,
                {
                    "schema_version": 1,
                    "control": "exact-live-binpkg-checkpoint",
                    "checkpoint_id": checkpoint_id,
                    "status": "artifact-generations-verified-final-freeze-pending",
                    "prepared_at": "2026-08-22T00:00:00Z",
                    "live_cpvs": live_cpvs,
                    "source": {
                        "path": os.fspath(source_durable),
                        "packages_sha256": digest((source_durable / "Packages").read_bytes()),
                        "exact_delta_only": True,
                        "full_gpkg_payloads_validated": True,
                    },
                    "cache_checkpoint": {
                        "path": os.fspath(cache),
                        "indexed_cpvs": live_cpvs,
                        "gpkg_archives_validated": live_cpvs,
                        "image_streams_tested": live_cpvs,
                        "missing_total": 0,
                        "extra_total": 0,
                        "archive_failure_total": 0,
                        "payload_failure_total": 0,
                    },
                    "durable_checkpoint": {
                        "path": os.fspath(durable),
                        "indexed_cpvs": live_cpvs,
                        "gpkg_archives_validated": live_cpvs,
                        "image_streams_tested": live_cpvs,
                        "missing_total": 0,
                        "extra_total": 0,
                        "archive_failure_total": 0,
                        "payload_failure_total": 0,
                    },
                    "activation_intent": {
                        "selector": os.fspath(selector),
                        "target": os.fspath(durable),
                        "expected_old_identity": old_selector_identity,
                        "guard": "exclusive-lock plus exact pre-rename identity comparison",
                    },
                    "evidence": {
                        "directory": os.fspath(report),
                        "manifest_sha256": digest(evidence_manifest_path.read_bytes()),
                    },
                    "offline_restoration_tested": False,
                    "pending_total": 1,
                    "unknown_total": 0,
                    "failed_total": 0,
                },
            )
            activation_evidence_path = report / "activation-evidence-manifest.sha256"
            activation_evidence_path.write_text(
                f"{digest(evidence_manifest_path.read_bytes())}  {evidence_manifest_path}\n",
                encoding="utf-8",
            )
            activation_evidence_path.chmod(0o600)
            terminal = state_parent / (
                f"binpkg-checkpoint-{checkpoint_id}.offline-restore-proven.json"
            )
            canonical_state = state_parent / f"binpkg-checkpoint-{checkpoint_id}.json"
            intent_path = report / "activation-intent.json"
            intent = {
                "schema_version": 1,
                "checkpoint_id": checkpoint_id,
                "status": "prepared",
                "prepared_at": "2026-08-22T00:00:00Z",
                "selector": os.fspath(selector),
                "expected_old_selector_identity": old_selector_identity,
                "target": os.fspath(durable),
                "state": os.fspath(canonical_state),
                "input_bindings": {
                    "source": {
                        "path": os.fspath(source_durable),
                        "packages_sha256": digest((source_durable / "Packages").read_bytes()),
                    },
                    "verifier": {"path": os.fspath(checkpoint_bootstrap / "verify-binpkg-snapshot.py"), "sha256": digest(source_payloads["verify-binpkg-snapshot.py"])},
                    "delta": {
                        "sorted_cpvs_path": os.fspath(delta_path),
                        "sorted_cpvs_sha256": digest(delta_payload),
                        "count": len(delta_cpvs),
                    },
                    "artifact_preparation": {
                        "path": os.fspath(artifact_preparation),
                        "sha256": digest(artifact_preparation.read_bytes()),
                        "live_cpvs": live_cpvs,
                    },
                },
                "activation_evidence": {
                    "path": os.fspath(activation_evidence_path),
                    "sha256": digest(activation_evidence_path.read_bytes()),
                },
                "recovery_rule": "old=not-activated; exact-target=activated; anything-else=lost-update",
            }
            intent_payload = write_jq(intent_path, intent)
            prepared_selector = selector_parent / f"critical-current.prepared-{checkpoint_id}"
            prepared_selector.symlink_to(durable)
            active_selector_identity = cast(
                str,
                namespace["checkpoint_selector_identity"](
                    prepared_selector, "fixture prepared selector"
                ),
            )
            prepared_record_path = report / "prepared-selector.json"
            write_jq(
                prepared_record_path,
                {
                    "schema_version": 1,
                    "checkpoint_id": checkpoint_id,
                    "prepared_at": "2026-08-22T00:00:30Z",
                    "path": os.fspath(prepared_selector),
                    "target": os.fspath(durable),
                    "selector_identity": active_selector_identity,
                    "activation_intent_sha256": digest(intent_payload),
                },
            )
            witness = selector_parent / f"critical-current.previous-{checkpoint_id}"
            selector.rename(witness)
            prepared_selector.rename(selector)
            displaced_selector_identity = cast(
                str,
                namespace["checkpoint_selector_identity"](
                    witness, "fixture displaced selector"
                ),
            )
            activation_receipt_path = report / "activation-receipt.json"
            activation_receipt = {
                "schema_version": 1,
                "checkpoint_id": checkpoint_id,
                "status": "selector-activated",
                "activated_at": "2026-08-22T00:01:00Z",
                "selector": os.fspath(selector),
                "target": os.fspath(durable),
                "activated_selector_identity": active_selector_identity,
                "displaced_selector_witness": os.fspath(witness),
                "displaced_selector_identity": displaced_selector_identity,
                "activation_intent": {
                    "path": os.fspath(intent_path),
                    "sha256": digest(intent_payload),
                },
                "prepared_selector_record": {
                    "path": os.fspath(prepared_record_path),
                    "sha256": digest(prepared_record_path.read_bytes()),
                },
                "activation_evidence": {
                    "path": os.fspath(activation_evidence_path),
                    "sha256": digest(activation_evidence_path.read_bytes()),
                },
            }
            activation_receipt_payload = write_jq(
                activation_receipt_path, activation_receipt
            )
            offline = report / "offline-restore"
            offline.mkdir()
            restored_cpv, restore_relative, restore_payload = packages_records[0]
            binpkg_path = offline / "binpkg.json"
            binpkg_payload = write_jq(
                binpkg_path,
                {
                    "schema_version": 1,
                    "sequence": 1,
                    "checkpoint_id": checkpoint_id,
                    "selected_at": "2026-08-22T00:01:10Z",
                    "selected_at_unix_ns": "1",
                    "selected_snapshot": os.fspath(durable),
                    "cpv": restored_cpv,
                    "archive_relative_path": restore_relative,
                    "archive_sha256": digest(restore_payload),
                },
            )
            command_intent = offline / "command-intent.json"
            ledger_artifacts: list[Path] = []

            def offline_reference(name: str, content: bytes | None = None) -> dict[str, str]:
                evidence_path = offline / name
                evidence_path.write_bytes(content if content is not None else (name + "\n").encode())
                evidence_path.chmod(0o600)
                ledger_artifacts.append(evidence_path)
                return {
                    "path": os.fspath(evidence_path),
                    "sha256": digest(evidence_path.read_bytes()),
                }

            containment_preflight = offline_reference(
                "containment-preflight.000.json",
                pretty(
                    {
                        "schema_version": 3,
                        "emulated": True,
                        "direct_pidfd_sigterm": {
                            "exact_child_gone": True,
                            "pidfd_open": True,
                            "pidfd_send_signal": True,
                            "signal": "SIGTERM",
                            "returncode": -15,
                        },
                        "unshare_kill_child_sigkill": {
                            "descendant_pidfd_open": True,
                            "escaped_private_process_group_gone": True,
                            "escaped_setsid_descendant_gone": True,
                            "exact_namespace_child_gone": True,
                            "ipv4_errno": "ENETUNREACH",
                            "ipv4_external_unreachable": True,
                            "ipv6_errno": "EADDRNOTAVAIL",
                            "ipv6_external_unreachable": True,
                            "kill_child_signal": "SIGKILL",
                            "mount_proc": True,
                            "namespace_interfaces": ["lo"],
                            "namespace_pid": 1,
                            "network_namespace": True,
                            "network_namespace_distinct": True,
                            "pid_namespace": True,
                            "private_process_group_gone": True,
                            "supervisor_pidfd_open": True,
                            "supervisor_returncode": -9,
                            "supervisor_signal": "SIGKILL",
                        },
                    }
                ),
            )
            package_match_stdout = offline_reference(
                "portage-match.000.stdout", b"sys-apps/portage-3.0.81.1\n"
            )
            package_match_stderr = offline_reference("portage-match.000.stderr", b"")
            qcheck_before_stdout = offline_reference("portage-qcheck.before.000.stdout")
            qcheck_before_stderr = offline_reference("portage-qcheck.before.000.stderr", b"")
            qcheck_after_stdout = offline_reference("portage-qcheck.after.000.stdout")
            qcheck_after_stderr = offline_reference("portage-qcheck.after.000.stderr", b"")
            selected_before = offline_reference("selected-sets.before.000.tsv")
            selected_after = offline_reference(
                "selected-sets.after.000.tsv",
                (offline / "selected-sets.before.000.tsv").read_bytes(),
            )
            pkgdir_before = offline_reference("pkgdir.before.000.tsv")
            pkgdir_after = offline_reference(
                "pkgdir.after.000.tsv", (offline / "pkgdir.before.000.tsv").read_bytes()
            )
            vdb_before = offline_reference("vdb.before.000.tsv")
            vdb_after = offline_reference("vdb.after.000.tsv", b"changed-vdb\n")
            for stem in ("vdb.before.000.tsv", "vdb.after.000.tsv"):
                offline_reference(stem + ".paths0", b"fixture\0")
                offline_reference(stem + ".paths0.unsorted.paths0", b"fixture\0")
                offline_reference(stem + ".paths0.unsorted.paths0.stderr", b"")
                offline_reference(stem + ".paths0.sort.stderr", b"")
            pretend_stdout = offline_reference(
                "emerge.pretend.stdout.000",
                b"[binary   R    ] sys-apps/portage-3.0.81.1\n"
                b"Total: 1 package (1 reinstall, 1 binary), Size of downloads: 0 KiB\n",
            )
            pretend_stderr = offline_reference("emerge.pretend.stderr.000", b"")
            pre_command_verifier = offline_reference(
                "pre-command-verifier.000.json", pretty(verification)
            )
            pre_command_verifier_stderr = offline_reference(
                "pre-command-verifier.000.json.stderr", b""
            )
            restore_stdout = offline_reference(
                "emerge.stdout.000", b">>> Emerging binary (sys-apps/portage-3.0.81.1)\n"
            )
            restore_stderr = offline_reference("emerge.stderr.000", b"")
            package_stdout = offline_reference("qcheck.stdout.000")
            package_stderr = offline_reference("qcheck.stderr.000", b"")
            command_options = [
                "--ignore-default-opts",
                "--ask=n",
                "--autounmask=n",
                "--autounmask-write=n",
                "--buildpkg=n",
                "--getbinpkg=n",
                "--usepkgonly",
                "--binpkg-changed-deps=n",
                "--binpkg-respect-use=n",
                "--use-ebuild-visibility=n",
                "--nodeps",
                "--oneshot",
                "--verbose",
            ]
            environment = {
                "HOME": os.fspath(self.fixture.root / "root"),
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "TZ": "UTC",
                "PKGDIR": os.fspath(durable),
                "PORTAGE_BINHOST": "",
                "GENTOO_MIRRORS": "",
                "FETCHCOMMAND": "/bin/false",
                "RESUMECOMMAND": "/bin/false",
                "EPYTHON": "python3.15",
            }
            containment = {
                "network_namespace": True,
                "pid_namespace": True,
                "mount_proc": True,
                "launcher": [
                    os.fspath(fixture_tools["unshare"]),
                    "--pid",
                    "--net",
                    "--fork",
                    "--kill-child=KILL",
                    "--mount-proc",
                    "--",
                ],
                "unshare_tool_identity": tool_identity_lines[
                    os.fspath(fixture_tools["unshare"])
                ],
                "preflight": containment_preflight,
            }
            portage_implementation = {
                "cpv": "sys-apps/portage-3.0.81.1",
                "epython": "python3.15",
                "python": {
                    "path": os.fspath(fixture_tools["python3.15"]),
                    "tool_identity": tool_identity_lines[
                        os.fspath(fixture_tools["python3.15"])
                    ],
                },
                "emerge": {
                    "path": os.fspath(fixture_tools["emerge-implementation"]),
                    "tool_identity": tool_identity_lines[
                        os.fspath(fixture_tools["emerge-implementation"])
                    ],
                },
                "package_match": {
                    "tool": "portageq",
                    "tool_identity": tool_identity_lines[
                        os.fspath(fixture_tools["portageq"])
                    ],
                    "argv": [
                        os.fspath(fixture_tools["portageq"]),
                        "match",
                        "/",
                        "sys-apps/portage",
                    ],
                    "exit_status": 0,
                    "stdout": package_match_stdout,
                    "stderr": package_match_stderr,
                },
                "package_check_before": {
                    "tool": "qcheck",
                    "tool_identity": tool_identity_lines[
                        os.fspath(fixture_tools["qcheck"])
                    ],
                    "argv": [
                        os.fspath(fixture_tools["qcheck"]),
                        "=sys-apps/portage-3.0.81.1",
                    ],
                    "exit_status": 0,
                    "stdout": qcheck_before_stdout,
                    "stderr": qcheck_before_stderr,
                },
            }
            initial_selected_archive = {
                "path": os.fspath(durable / restore_relative),
                "relative_path": restore_relative,
                "size": len(restore_payload),
                "sha256": digest(restore_payload),
            }
            pretend = {
                "argv": [
                    os.fspath(fixture_tools["emerge"]),
                    *command_options,
                    "--pretend",
                    os.fspath(durable / restore_relative),
                ],
                "exit_status": 0,
                "summary": {
                    "packages": 1,
                    "reinstall": 1,
                    "binary": 1,
                    "download_kib": 0,
                },
                "logs": {"stdout": pretend_stdout, "stderr": pretend_stderr},
            }
            command_intent_payload = write_jq(
                command_intent,
                {
                    "schema_version": 3,
                    "checkpoint_id": checkpoint_id,
                    "status": "supervised-command-pending",
                    "started_at": "2026-08-22T00:01:15Z",
                    "started_at_unix_ns": "2",
                    "emerge_tool_identity": tool_identity_lines[
                        os.fspath(fixture_tools["emerge"])
                    ],
                    "environment": environment,
                    "argv": [
                        os.fspath(fixture_tools["emerge"]),
                        *command_options,
                        os.fspath(durable / restore_relative),
                    ],
                    "containment": containment,
                    "portage_implementation": portage_implementation,
                    "selected_archive": initial_selected_archive,
                    "selected_sets_before": selected_before,
                    "pkgdir_before": pkgdir_before,
                    "pretend": pretend,
                    "binpkg_evidence_sha256": digest(binpkg_payload),
                    "vdb_before": vdb_before,
                    "pre_command_verifier": pre_command_verifier,
                },
            )
            ledger_artifacts.append(command_intent)
            command_path = offline / "command.json"
            selected_archive = {
                "path": os.fspath(durable / restore_relative),
                "relative_path": restore_relative,
                "size_before": len(restore_payload),
                "sha256_before": digest(restore_payload),
                "size_after": len(restore_payload),
                "sha256_after": digest(restore_payload),
                "unchanged": True,
            }
            command_payload = write_jq(
                command_path,
                {
                    "schema_version": 4,
                    "sequence": 2,
                    "checkpoint_id": checkpoint_id,
                    "attempt": 0,
                    "started_at": "2026-08-22T00:01:20Z",
                    "started_at_unix_ns": "2",
                    "completed_at": "2026-08-22T00:01:30Z",
                    "completed_at_unix_ns": "3",
                    "exit_status": 0,
                    "offline": True,
                    "network_isolated": True,
                    "usepkgonly": True,
                    "getbinpkg": False,
                    "nodeps": True,
                    "selected_snapshot": os.fspath(durable),
                    "pkgdir": os.fspath(durable),
                    "vdb": os.fspath(self.fixture.root / "vdb"),
                    "restored_cpv": restored_cpv,
                    "binpkg_evidence_sha256": digest(binpkg_payload),
                    "command_intent_sha256": digest(command_intent_payload),
                    "retry_authorization": None,
                    "emerge_tool_identity": tool_identity_lines[
                        os.fspath(fixture_tools["emerge"])
                    ],
                    "environment": environment,
                    "containment": containment,
                    "portage_implementation": {
                        **portage_implementation,
                        "package_check_after": {
                            "tool": "qcheck",
                            "tool_identity": tool_identity_lines[
                                os.fspath(fixture_tools["qcheck"])
                            ],
                            "argv": [os.fspath(fixture_tools["qcheck"]), "=sys-apps/portage-3.0.81.1"],
                            "exit_status": 0,
                            "stdout": qcheck_after_stdout,
                            "stderr": qcheck_after_stderr,
                        },
                    },
                    "selected_archive": selected_archive,
                    "selected_sets_transition": {
                        "before": selected_before,
                        "after": selected_after,
                        "unchanged": True,
                    },
                    "pkgdir_transition": {
                        "before": pkgdir_before,
                        "after": pkgdir_after,
                        "unchanged": True,
                    },
                    "transaction_baseline_transition": {
                        "vdb": {
                            "before": vdb_before,
                            "after": vdb_after,
                            "confined_to_restored_cpv": True,
                        },
                        "selected_sets": {
                            "before": selected_before,
                            "after": selected_after,
                            "unchanged": True,
                        },
                        "pkgdir": {
                            "before": pkgdir_before,
                            "after": pkgdir_after,
                            "unchanged": True,
                        },
                    },
                    "pretend": pretend,
                    "command": [
                        os.fspath(fixture_tools["emerge"]),
                        *command_options,
                        os.fspath(durable / restore_relative),
                    ],
                    "vdb_transition": {
                        "before": vdb_before,
                        "after": vdb_after,
                        "changed": True,
                    },
                    "pre_command_verifier": pre_command_verifier,
                    "logs": {"stdout": restore_stdout, "stderr": restore_stderr},
                    "package_check": {
                        "tool": "qcheck",
                        "tool_identity": tool_identity_lines[
                            os.fspath(fixture_tools["qcheck"])
                        ],
                        "argv": [os.fspath(fixture_tools["qcheck"]), f"={restored_cpv}"],
                        "exit_status": 0,
                        "stdout": package_stdout,
                        "stderr": package_stderr,
                    },
                },
            )
            post_verifier_path = offline / "post-verifier.json"
            post_report = copy.deepcopy(verification)
            post_report["inputs"] = {
                "allow_extra_archives": False,
                "packages_index": os.fspath(durable / "Packages"),
                "snapshot": os.fspath(durable),
                "validate_gpkg": True,
                "vdb": os.fspath(self.fixture.root / "vdb"),
                "zstd": "/usr/bin/zstd",
            }
            post_report_path = offline / "post-verifier-report.json"
            _post_report_payload = write_pretty(post_report_path, post_report)
            ledger_artifacts.append(post_report_path)
            post_report_stderr = offline_reference(
                "post-verifier-report.json.stderr", b""
            )
            post_verifier_payload = write_jq(
                post_verifier_path,
                {
                    "schema_version": 1,
                    "sequence": 3,
                    "checkpoint_id": checkpoint_id,
                    "completed_at": "2026-08-22T00:01:40Z",
                    "completed_at_unix_ns": "4",
                    "command_evidence_sha256": digest(command_payload),
                    "binpkg_evidence_sha256": digest(binpkg_payload),
                    "verifier": {
                        "path": os.fspath(checkpoint_bootstrap / "verify-binpkg-snapshot.py"),
                        "sha256": digest(source_payloads["verify-binpkg-snapshot.py"]),
                    },
                    "report": post_report,
                },
            )
            ledger_path = offline / "attempt-ledger.sha256"
            ledger_path.write_text(
                "".join(
                    f"{digest(item.read_bytes())}  {item}\n" for item in ledger_artifacts
                ),
                encoding="utf-8",
            )
            ledger_path.chmod(0o600)
            receipt_evidence = {
                "command": {"path": "offline-restore/command.json", "sha256": digest(command_payload)},
                "binpkg": {"path": "offline-restore/binpkg.json", "sha256": digest(binpkg_payload)},
                "post_verifier": {"path": "offline-restore/post-verifier.json", "sha256": digest(post_verifier_payload)},
                "attempt_ledger": {"path": "offline-restore/attempt-ledger.sha256", "sha256": digest(ledger_path.read_bytes())},
            }
            offline_receipt_path = report / "offline-restore-receipt.json"
            offline_receipt = {
                "schema_version": 1,
                "checkpoint_id": checkpoint_id,
                "status": "offline-restore-proven",
                "recorded_at": "2026-08-22T00:02:00Z",
                "activation_receipt_sha256": digest(activation_receipt_payload),
                "evidence": receipt_evidence,
            }
            offline_receipt_payload = write_jq(
                offline_receipt_path, offline_receipt
            )
            state = {
                "schema_version": 2,
                "control": "exact-live-binpkg-checkpoint",
                "checkpoint_id": checkpoint_id,
                "status": "offline-restore-proven",
                "recorded_at": "2026-08-22T00:03:00Z",
                "live_cpvs": live_cpvs,
                "cache_checkpoint": {"path": os.fspath(cache)},
                "durable_checkpoint": {"path": os.fspath(durable)},
                "activation": {
                    "selector": os.fspath(selector),
                    "intent": os.fspath(intent_path),
                    "intent_sha256": digest(intent_payload),
                    "receipt": os.fspath(activation_receipt_path),
                    "receipt_sha256": digest(activation_receipt_payload),
                },
                "offline_restore": {
                    "receipt": os.fspath(offline_receipt_path),
                    "receipt_sha256": digest(offline_receipt_payload),
                    "evidence": receipt_evidence,
                },
                "offline_restoration_tested": True,
                "pending_total": 0,
                "unknown_total": 0,
                "failed_total": 0,
            }
            terminal_payload = write_jq(terminal, state)
            os.link(terminal, canonical_state)
            operator_root = reports / f"checkpoint-{checkpoint_id}-operator-evidence"
            operator_root.mkdir()
            for name, content in operator_files.items():
                operator_path = operator_root / name
                operator_path.parent.mkdir(parents=True, exist_ok=True)
                operator_path.write_bytes(content)
                operator_path.chmod(0o600)
            if not operator_files:
                (operator_root / "checkpoint-note.txt").write_text(
                    checkpoint_id + "\n", encoding="utf-8"
                )
            operator_manifest_path, operator_manifest_payload = operator_manifest(
                operator_root
            )
            return {
                "id": checkpoint_id,
                "durable": durable,
                "terminal": terminal,
                "canonical": canonical_state,
                "terminal_payload": terminal_payload,
                "receipt": offline_receipt_path,
                "receipt_payload": offline_receipt_payload,
                "operator_manifest": operator_manifest_path,
                "operator_manifest_payload": operator_manifest_payload,
                "operator_root": operator_root,
                "report": report,
            }

        pre = checkpoint(
            "pre-fixture",
            live_cpvs=10,
            delta_cpvs=["sys-apps/portage-3.0.81.1"],
            source_durable=old_durable,
            witness_target=old_durable,
            operator_files={},
        )
        transaction_id = "jsonschema-source-fixture"
        transaction_report = reports / f"jsonschema-prerequisite-{transaction_id}"
        transaction_report.mkdir()
        cpv = "dev-python/jsonschema-4.25.1"
        plan_rows = [
            {
                "cpv": cpv,
                "repository": "gentoo",
                "exact_atom": f"={cpv}::gentoo",
                "normalized_display": f"[ebuild N ] {cpv}::gentoo",
            }
        ]
        plan = {
            "schema_version": 1,
            "ordered_exact_atoms": [f"={cpv}::gentoo"],
            "rows": plan_rows,
            "rows_sha256": digest(canonical(plan_rows).encode()),
        }
        pre_identity = identity(pre["canonical"])
        pre_phase_identity = identity(pre["terminal"])
        checkpoint_authority = {
            "path": os.fspath(pre["canonical"]),
            "sha256": digest(pre["terminal_payload"]),
            "identity": {key: pre_identity[key] for key in ("device", "inode", "uid", "gid", "mode", "nlink", "size")},
            "phase_path": os.fspath(pre["terminal"]),
            "phase_sha256": digest(pre["terminal_payload"]),
            "phase_identity": {key: pre_phase_identity[key] for key in ("device", "inode", "uid", "gid", "mode", "nlink", "size")},
            "checkpoint_id": pre["id"],
            "status": "offline-restore-proven",
        }
        tool_rows = []
        for name, tool_path in (
            ("transaction", bootstrap / "install-jsonschema-prerequisite.py"),
            ("snapshot_verifier", bootstrap / "verify-binpkg-snapshot.py"),
            ("python", python),
            ("emerge", fixture_tools["emerge"]),
            ("qcheck", fixture_tools["qcheck"]),
            ("zstd", fixture_tools["zstd"]),
        ):
            metadata = tool_path.lstat()
            tool_rows.append(
                {
                    "name": name,
                    "requested_path": os.fspath(tool_path),
                    "resolved_path": os.fspath(tool_path.resolve(strict=True)),
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                    "uid": metadata.st_uid,
                    "gid": metadata.st_gid,
                    "mode": stat.S_IMODE(metadata.st_mode),
                    "nlink": metadata.st_nlink,
                    "size": metadata.st_size,
                    "sha256": digest(tool_path.read_bytes()),
                }
            )
        tool_rows.sort(key=lambda row: cast(str, row["name"]))
        prerequisite_authority_root = self.fixture.root / "prerequisite-authority"
        repository_source = self.fixture.root / "live-repository"
        repository_materialized = prerequisite_authority_root / "repositories/gentoo"
        config_source = self.fixture.root / "live-portage-config"
        config_materialized = prerequisite_authority_root / "portage-config"
        global_config_source = self.fixture.root / "live-portage-global-config"
        global_config_materialized = prerequisite_authority_root / "portage-global-config"
        python_root = self.fixture.root / "python-authority"
        for directory in (
            repository_source,
            repository_materialized,
            config_source,
            config_materialized,
            global_config_source,
            global_config_materialized,
            python_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        capacity = {
            "schema_version": 1,
            "rows": [{"device": 1, "required_bytes": 1, "available_bytes": 2}],
            "rows_sha256": digest(
                canonical(
                    [{"device": 1, "required_bytes": 1, "available_bytes": 2}]
                ).encode()
            ),
        }
        preparation = {
            "schema": "gentoo-optimization-jsonschema-preparation-attempt-v1",
            "transaction_id": transaction_id,
            "recorded_at": "2026-08-22T00:03:30Z",
            "boot_id": "fixture-boot",
            "capacity": capacity,
            "pre_dependency_checkpoint": checkpoint_authority,
            "reuse_policy": "immutable-attempt-never-reuse-id",
            "status": "preparation-started-or-abandoned-until-prepared-is-durable",
        }
        preparation_path = state_parent / (
            f"jsonschema-prerequisite-{transaction_id}.preparation-attempt.json"
        )
        preparation_payload = write_pretty(preparation_path, preparation)
        payload_root_observation = {
            "path": "/usr",
            "device": 123,
            "inode": 456,
            "uid": 0,
            "gid": 0,
            "mode": 0o755,
            "nlink": 2,
            "size": 4096,
            "xattrs": [],
            "type": "directory",
        }
        prepared_vdb_cpvs = snapshot_members[pre["durable"]]
        prepared_vdb = {
            "schema_version": 2,
            "cpvs": prepared_vdb_cpvs,
            "cpvs_sha256": digest(
                ("\n".join(prepared_vdb_cpvs) + "\n").encode()
            ),
        }
        locked_window = {
            "schema_version": 1,
            "portage_lock_api": {"fixture": True},
            "vdb": prepared_vdb,
            "selected_sets": {"fixture": True},
            "mtimedb": {"fixture": True},
            "counter": {"fixture": True},
            "counter_value": 1,
            "payload_root": payload_root_observation,
            "copies": {"fixture": True},
            "loader_directories": {
                "schema_version": 1,
                "rows": [],
                "rows_sha256": digest(canonical([]).encode()),
            },
            "effective_portage_policy": {"fixture": True},
            "native_toolchain": {"fixture": True},
            "plan_metadata": None,
            "process_exclusion": {"fixture": True},
        }
        locked_document = {
            "schema": "gentoo-optimization-jsonschema-locked-authority-v1",
            "transaction_id": transaction_id,
            "initial_locked_window": locked_window,
        }
        locked_path = state_parent / (
            f"jsonschema-prerequisite-{transaction_id}.locked-authority.json"
        )
        locked_payload = canonical(locked_document).encode()
        locked_path.write_bytes(locked_payload)
        locked_path.chmod(0o600)
        locked_metadata = locked_path.lstat()
        locked_parent_metadata = locked_path.parent.lstat()
        locked_reference = {
            "schema": "gentoo-optimization-jsonschema-locked-authority-reference-v1",
            "path": os.fspath(locked_path),
            "sha256": digest(locked_payload),
            "size": len(locked_payload),
            "identity": {
                key: value
                for key, value in identity(locked_path).items()
                if key in {"device", "inode", "uid", "gid", "mode", "nlink", "size"}
            },
            "parent_identity": {
                "device": locked_parent_metadata.st_dev,
                "inode": locked_parent_metadata.st_ino,
                "uid": locked_parent_metadata.st_uid,
                "gid": locked_parent_metadata.st_gid,
                "mode": stat.S_IMODE(locked_parent_metadata.st_mode),
            },
        }
        authority = {
            "pre_dependency_checkpoint": checkpoint_authority,
            "tools": {
                "schema_version": 1,
                "rows": tool_rows,
                "rows_sha256": digest(canonical(tool_rows).encode()),
            },
            "python_modules": [
                {"name": "fixture", "roots": [{"path": os.fspath(python_root)}]}
            ],
            "repositories": [
                {
                    "name": "gentoo",
                    "source_location": os.fspath(repository_source),
                    "materialized_location": os.fspath(repository_materialized),
                }
            ],
            "portage_config": {
                "mount_target": os.fspath(config_source),
                "materialized_location": os.fspath(config_materialized),
            },
            "portage_global_config": {
                "mount_target": os.fspath(global_config_source),
                "materialized_location": os.fspath(global_config_materialized),
            },
            "framework": {"fixture": True},
            "capacity_preflight": capacity,
            "build_tool_versions": {"fixture": True},
            "preparation_attempt": {
                "path": os.fspath(preparation_path),
                "sha256": digest(preparation_payload),
            },
            "build_execution_scope": {"fixture": True},
        }
        plan_metadata_rows = [{"cpv": cpv, "fixture": True}]
        plan_metadata = {
            "schema_version": 1,
            "rows": plan_metadata_rows,
            "rows_sha256": digest(canonical(plan_metadata_rows).encode()),
        }
        resolver = {
            "target": "dev-python/jsonschema",
            "frozen_repository_observation": {"fixture": True},
            "locked_authority": locked_reference,
            "portage_build_identity": {"fixture": True},
            "initial_pretend": {"fixture": True},
            "exact_repretend_before_prefetch": {"fixture": True},
            "prefetch": {"fixture": True},
            "offline_exact_repretend": {"fixture": True},
            "private_roots_before": {"fixture": True},
            "private_portage_outputs_before": {"fixture": True},
            "final_locked_window": {
                "schema_version": 1,
                "locked_authority_sha256": digest(locked_payload),
                "effective_portage_policy": locked_window[
                    "effective_portage_policy"
                ],
                "native_toolchain": locked_window["native_toolchain"],
                "plan_metadata_sha256": digest(canonical(plan_metadata).encode()),
            },
            "plan_metadata": plan_metadata,
        }
        private_cache = self.fixture.root / "prerequisite-private"
        private_roots = {
            "pkgdir": os.fspath(private_cache / "pkgdir"),
            "distdir_staging": os.fspath(private_cache / "distfiles.staging"),
            "distdir_runtime": os.fspath(private_cache / "distfiles.runtime"),
            "portage_tmpdir": os.fspath(private_cache / "tmp"),
            "portage_logdir": os.fspath(private_cache / "logs"),
            "ccache_dir": os.fspath(private_cache / "ccache"),
            "thinlto_cache": os.fspath(private_cache / "thinlto-cache"),
            "cargo_home": os.fspath(private_cache / "cargo-home"),
            "rustup_home": os.fspath(private_cache / "rustup-home"),
            "var_lib_portage": os.fspath(private_cache / "var-lib-portage"),
            "cache_edb": os.fspath(private_cache / "cache-edb"),
            "etc": os.fspath(private_cache / "etc"),
            "home": os.fspath(private_cache / "home"),
            "xdg_cache": os.fspath(private_cache / "xdg-cache"),
            "live_cache_edb_view": os.fspath(private_cache / "live-cache-edb-view"),
            "live_var_lib_portage": os.fspath(self.fixture.root / "var-lib-portage"),
            "live_cache_edb": os.fspath(self.fixture.root / "cache-edb"),
            "live_etc": os.fspath(self.fixture.root / "etc"),
            "live_thinlto_cache": os.fspath(self.fixture.root / "live-thinlto-cache"),
            "distdir_authority": os.fspath(private_cache / "distfiles.staging"),
        }
        for private_path in {
            Path(value) for value in private_roots.values()
        }:
            private_path.mkdir(parents=True, exist_ok=True)
        recovery_contract = {
            "claim": "declared-package-manager-authorities-only",
            "whole_host_byte_identity": False,
            "source_emerge_may_never_be_retried_after_armed": True,
            "live_edb_counter_is_monotonic_nonrollback_axis": True,
            "authorities": [
                "complete-live-vdb-including-category-and-dot-residue",
                "immutable-external-held-lock-authority-reference",
                "full-var-lib-portage-and-cache-edb-inputs",
                "world-and-world_sets",
                "semantic-empty-preserved-libraries-registry",
                "private-etc-and-private-edb-declared-delta",
                "live-etc-unchanged",
                "preexisting-loader-content-and-declared-loader-metadata",
                "private-pkgdir-distdir-tmpdir-logdir-ccache-thinlto-cargo-rustup-roots",
                "exact-eapi-defined-phases-ebuild-and-setup-eclasses",
            ],
        }
        common = {
            "schema": "gentoo-optimization-jsonschema-prerequisite-v1",
            "transaction_id": transaction_id,
            "recorded_at": "2026-08-22T00:04:00Z",
            "boot_id": "fixture-boot",
            "authority": authority,
            "resolver": resolver,
            "plan": plan,
            "private_roots": private_roots,
            "child": None,
            "recovery_contract": recovery_contract,
            "evidence": {
                "directory": os.fspath(transaction_report),
                "proc_root": "/proc",
            },
            "unknown_total": 0,
            "failed_total": 0,
        }
        prepared = {
            **common,
            "phase": "prepared",
            "previous_phase": None,
            "previous_state_sha256": None,
            "prepared_state_sha256": None,
            "outcome": None,
            "pending_total": 1,
        }
        prepared_path = state_parent / f"jsonschema-prerequisite-{transaction_id}.prepared.json"
        prepared_payload = write_pretty(prepared_path, prepared)
        source_spec_path = transaction_report / "source-emerge.execution.json"
        private_path_map = {key: Path(value) for key, value in private_roots.items()}
        source_environment = namespace["prerequisite_plan_environment"](
            private_path_map
        )
        source_mounts = [
            {
                "source": private_roots["etc"],
                "target": private_roots["live_etc"],
                "read_only": False,
            },
            {
                "source": private_roots["etc"],
                "target": private_roots["etc"],
                "read_only": True,
            },
            {
                "source": os.fspath(repository_materialized),
                "target": os.fspath(repository_materialized),
                "read_only": True,
            },
            {
                "source": os.fspath(repository_materialized),
                "target": os.fspath(repository_source),
                "read_only": True,
            },
            {
                "source": os.fspath(config_materialized),
                "target": os.fspath(config_materialized),
                "read_only": True,
            },
            {
                "source": os.fspath(config_materialized),
                "target": os.fspath(config_source),
                "read_only": True,
            },
            {
                "source": os.fspath(global_config_materialized),
                "target": os.fspath(global_config_materialized),
                "read_only": True,
            },
            {
                "source": os.fspath(global_config_materialized),
                "target": os.fspath(global_config_source),
                "read_only": True,
            },
            {
                "source": os.fspath(python_root),
                "target": os.fspath(python_root),
                "read_only": True,
            },
            {
                "source": private_roots["distdir_authority"],
                "target": private_roots["distdir_authority"],
                "read_only": True,
            },
            {
                "source": private_roots["live_cache_edb"],
                "target": private_roots["live_cache_edb_view"],
                "read_only": True,
            },
            {
                "source": private_roots["var_lib_portage"],
                "target": private_roots["live_var_lib_portage"],
                "read_only": False,
            },
            {
                "source": private_roots["var_lib_portage"],
                "target": private_roots["var_lib_portage"],
                "read_only": True,
            },
            {
                "source": private_roots["cache_edb"],
                "target": private_roots["live_cache_edb"],
                "read_only": False,
            },
            {
                "source": private_roots["cache_edb"],
                "target": private_roots["cache_edb"],
                "read_only": True,
            },
            {
                "source": private_roots["thinlto_cache"],
                "target": private_roots["live_thinlto_cache"],
                "read_only": False,
            },
            {
                "source": private_roots["thinlto_cache"],
                "target": private_roots["thinlto_cache"],
                "read_only": True,
            },
        ]
        source_control_session = "fixture-control-session"
        source_emerge_options = [
            "--ignore-default-opts",
            "--verbose",
            "--tree",
            "--oneshot",
            "--with-bdeps=y",
            "--complete-graph=y",
            "--autounmask=n",
            "--autounmask-write=n",
            "--buildpkg=y",
            "--getbinpkg=n",
            "--usepkg=n",
            "--keep-going=n",
            "--fail-clean=y",
            "--noconfmem",
            "--nospinner",
            "--color=n",
            "--jobs=1",
            "--package-moves=n",
        ]
        source_command = [
            os.fspath(python),
            "-I",
            "-B",
            os.fspath(bootstrap / "install-jsonschema-prerequisite.py"),
            "__portage-action",
            os.fspath(prepared_path),
            digest(prepared_payload),
            "9",
            source_control_session,
            "--",
            os.fspath(fixture_tools["emerge"]),
            *source_emerge_options,
            "--ask=y",
            *plan["ordered_exact_atoms"],
        ]
        source_spec_unsigned = {
            "schema_version": 1,
            "network_isolated": True,
            "mounts": source_mounts,
            "command": source_command,
            "environment": source_environment,
        }
        source_spec = {
            **source_spec_unsigned,
            "contract_sha256": digest(canonical(source_spec_unsigned).encode()),
        }
        source_spec_payload = canonical(source_spec).encode()
        source_spec_path.write_bytes(source_spec_payload)
        source_spec_path.chmod(0o600)
        child = {
            "boot_id": "fixture-boot",
            "pid": 123,
            "process_group": 123,
            "session": 123,
            "start_ticks": 456,
            "spec_path": os.fspath(source_spec_path),
            "spec_sha256": digest(source_spec_payload),
            "control_session_sha256": digest(source_control_session.encode()),
        }
        armed = {
            **common,
            "phase": "armed",
            "previous_phase": "prepared",
            "previous_state_sha256": digest(prepared_payload),
            "prepared_state_sha256": digest(prepared_payload),
            "child": child,
            "outcome": {
                "displayed_plan": plan,
                "displayed_prefix_sha256": "5" * 64,
            },
            "pending_total": 1,
        }
        armed_path = state_parent / f"jsonschema-prerequisite-{transaction_id}.armed.json"
        armed_payload = write_pretty(armed_path, armed)
        payload_record_path = transaction_report / (
            "payload-admission-" + digest(cpv.encode()) + ".json"
        )
        payload_relative = "usr/lib/python3.15/site-packages/jsonschema/__init__.py"
        payload_rows = [
            {
                "path": payload_relative,
                "uid": 0,
                "gid": 0,
                "mode": 0o644,
                "xattrs": [],
                "type": "file",
                "size": 8,
                "nlink": 1,
                "sha256": digest(b"fixture\n"),
            }
        ]
        mergeroot = Path(private_roots["portage_tmpdir"]) / "portage/jsonschema/image"
        payload_manifest = {
            "schema_version": 1,
            "root": os.fspath(mergeroot),
            "rows": payload_rows,
            "rows_sha256": digest(canonical(payload_rows).encode()),
        }
        destination_paths = ["/" + payload_relative]
        observation_names = [
            "/usr",
            "/usr/lib",
            "/usr/lib/python3.15",
            "/usr/lib/python3.15/site-packages",
            "/usr/lib/python3.15/site-packages/jsonschema",
            "/usr/lib/python3.15/site-packages/jsonschema/__init__.py",
        ]
        preexisting_destinations = [payload_root_observation]
        for index, observation_name in enumerate(observation_names[1:-1], start=1):
            preexisting_destinations.append(
                {
                    "path": observation_name,
                    "device": 123,
                    "inode": 456 + index,
                    "uid": 0,
                    "gid": 0,
                    "mode": 0o755,
                    "nlink": 2,
                    "size": 4096,
                    "xattrs": [],
                    "type": "directory",
                }
            )
        preexisting_destinations.append(
            {"path": observation_names[-1], "type": "absent"}
        )
        manifest_sha = digest(canonical(payload_manifest).encode())
        preexisting_sha = digest(canonical(preexisting_destinations).encode())
        payload_record = {
            "schema": "gentoo-optimization-jsonschema-payload-admission-v1",
            "transaction_id": transaction_id,
            "prepared_state_sha256": digest(prepared_payload),
            "control_session_sha256": digest(source_control_session.encode()),
            "cpv": cpv,
            "mergeroot": os.fspath(mergeroot),
            "manifest": payload_manifest,
            "manifest_sha256": manifest_sha,
            "payload_root_observation": payload_root_observation,
            "payload_device": 123,
            "preexisting_destinations": preexisting_destinations,
            "preexisting_destinations_sha256": preexisting_sha,
            "destination_paths": destination_paths,
            "destination_paths_sha256": digest(
                ("\n".join(destination_paths) + "\n").encode()
            ),
        }
        payload_record_payload = write_pretty(payload_record_path, payload_record)
        admission = {
            "cpv": cpv,
            "path": os.fspath(payload_record_path),
            "sha256": digest(payload_record_payload),
            "manifest_sha256": manifest_sha,
            "preexisting_destinations_sha256": preexisting_sha,
        }
        delta = {
            "added": [cpv],
            "removed": [],
            "unexpected_added": [],
            "planned_not_added": [],
            "exact_success_delta": True,
            "rollback_eligible": True,
        }
        source_stdout_path = transaction_report / "source-emerge.stdout"
        source_stderr_path = transaction_report / "source-emerge.stderr"
        source_stdout_payload = b"fixture source emerge completed\n"
        source_stderr_payload = b""
        source_stdout_path.write_bytes(source_stdout_payload)
        source_stderr_path.write_bytes(source_stderr_payload)
        for source_log in (source_stdout_path, source_stderr_path):
            source_log.chmod(0o600)
        source_logs = {
            "stage": "source-emerge",
            "stdout_path": os.fspath(source_stdout_path),
            "stdout_sha256": digest(source_stdout_payload),
            "stdout_size": len(source_stdout_payload),
            "stderr_path": os.fspath(source_stderr_path),
            "stderr_sha256": digest(source_stderr_payload),
            "stderr_size": len(source_stderr_payload),
        }
        live_counter_observation = {
            "path": private_roots["live_cache_edb_view"] + "/counter",
            "device": 123,
            "inode": 900,
            "uid": 0,
            "gid": 0,
            "mode": 0o644,
            "nlink": 1,
            "size": 2,
            "xattrs": [],
            "type": "file",
            "sha256": digest(b"11"),
        }
        counter_token = digest(
            f"{transaction_id}\0{digest(prepared_payload)}\0success".encode()
        )
        counter_intent_path = transaction_report / "counter-reconciliation-success.intent.json"
        counter_intent = {
            "schema": "gentoo-optimization-jsonschema-counter-intent-v1",
            "transaction_id": transaction_id,
            "prepared_state_sha256": digest(prepared_payload),
            "outcome": "success",
            "live_path": live_counter_observation["path"],
            "partial_path": (
                private_roots["live_cache_edb_view"]
                + f"/.counter.gentoo-opt.{counter_token}.partial"
            ),
            "before": 10,
            "private": 10,
            "package_max": 11,
            "selected": 11,
            "payload_sha256": digest(b"11"),
            "live_identity_before": {
                "device": 123,
                "inode": 899,
                "uid": 0,
                "gid": 0,
                "mode": 0o644,
                "nlink": 1,
                "size": 2,
            },
            "live_xattrs_before": [],
        }
        counter_intent_payload = canonical(counter_intent).encode()
        counter_intent_path.write_bytes(counter_intent_payload)
        counter_intent_path.chmod(0o600)
        counter_completion_path = transaction_report / "counter-reconciliation-success.complete.json"
        counter_completion = {
            "schema": "gentoo-optimization-jsonschema-counter-completion-v1",
            "transaction_id": transaction_id,
            "prepared_state_sha256": digest(prepared_payload),
            "outcome": "success",
            "intent_path": os.fspath(counter_intent_path),
            "intent_sha256": digest(counter_intent_payload),
            "after": 11,
            "live_observation": live_counter_observation,
        }
        counter_completion_payload = canonical(counter_completion).encode()
        counter_completion_path.write_bytes(counter_completion_payload)
        counter_completion_path.chmod(0o600)
        counter_authority = {
            "outcome": "success",
            "before": 10,
            "private": 10,
            "package_max": 11,
            "after": 11,
            "intent_path": os.fspath(counter_intent_path),
            "intent_sha256": digest(counter_intent_payload),
            "completion_path": os.fspath(counter_completion_path),
            "completion_sha256": digest(counter_completion_payload),
            "live_observation": live_counter_observation,
            "non_counter_manifest_sha256": "6" * 64,
            "resealed_read_only": True,
        }
        qcheck_stage = "source-success-qcheck-001"
        qcheck_spec_path = transaction_report / f"{qcheck_stage}.execution.json"
        qcheck_spec_unsigned = {
            "schema_version": 1,
            "network_isolated": True,
            "mounts": source_mounts,
            "command": [os.fspath(fixture_tools["qcheck"]), f"={cpv}"],
            "environment": source_environment,
        }
        qcheck_spec = {
            **qcheck_spec_unsigned,
            "contract_sha256": digest(canonical(qcheck_spec_unsigned).encode()),
        }
        qcheck_spec_payload = canonical(qcheck_spec).encode()
        qcheck_spec_path.write_bytes(qcheck_spec_payload)
        qcheck_spec_path.chmod(0o600)
        qcheck_stdout_path = transaction_report / f"{qcheck_stage}.stdout"
        qcheck_stderr_path = transaction_report / f"{qcheck_stage}.stderr"
        qcheck_stdout_path.write_bytes(b"")
        qcheck_stderr_path.write_bytes(b"")
        qcheck_stdout_path.chmod(0o600)
        qcheck_stderr_path.chmod(0o600)
        qcheck_evidence = {
            "stage": qcheck_stage,
            "status": 0,
            "spec_path": os.fspath(qcheck_spec_path),
            "spec_sha256": digest(qcheck_spec_payload),
            "stdout_path": os.fspath(qcheck_stdout_path),
            "stdout_sha256": digest(b""),
            "stderr_path": os.fspath(qcheck_stderr_path),
            "stderr_sha256": digest(b""),
        }
        pkgdir_report_path = transaction_report / "source-success-pkgdir-verification.json"
        private_pkgdir = Path(private_roots["pkgdir"])
        private_archive_relative = "archives/jsonschema-fixture.gpkg.tar"
        private_archive_payload = b"fixture-jsonschema-gpkg\n"
        private_archive_path = private_pkgdir / private_archive_relative
        private_archive_path.parent.mkdir(parents=True, exist_ok=True)
        private_archive_path.write_bytes(private_archive_payload)
        private_archive_path.chmod(0o644)
        private_packages_payload = (
            "PACKAGES: 1\nVERSION: 0\n\n"
            f"CPV: {cpv}\n"
            f"PATH: {private_archive_relative}\n"
            f"SIZE: {len(private_archive_payload)}\n"
            f"MD5: {hashlib.md5(private_archive_payload).hexdigest()}\n"
            f"SHA1: {hashlib.sha1(private_archive_payload).hexdigest()}\n"
        ).encode()
        (private_pkgdir / "Packages").write_bytes(private_packages_payload)
        (private_pkgdir / "Packages").chmod(0o644)
        private_selected_vdb = (
            transaction_report
            / "source-success-pkgdir-verification/selected-vdb"
            / cpv
        )
        private_selected_vdb.mkdir(parents=True)
        private_report_counts = {
            "errors": 0,
            "extra_indexed_archives": 0,
            "gpkg_archives_found": 1,
            "gpkg_archives_indexed": 1,
            "gpkg_archives_validated": 1,
            "image_tar_zst_streams_tested": 1,
            "indexed_records": 1,
            "indexed_unique_cpvs": 1,
            "indexed_unique_paths": 1,
            "live_cpvs": 1,
            "missing_live_cpvs": 0,
            "unindexed_gpkg_archives": 0,
        }
        pkgdir_report = {
            "archives": [
                {
                    "cpv": cpv,
                    "exists": True,
                    "gpkg": {
                        "image_tar_zst_streams": 1,
                        "manifest_members_verified": 4,
                        "status": "verified",
                        "zstd_streams_tested": 1,
                    },
                    "md5": {
                        "actual": hashlib.md5(private_archive_payload).hexdigest(),
                        "expected": hashlib.md5(private_archive_payload).hexdigest(),
                    },
                    "path": private_archive_relative,
                    "record": 2,
                    "regular": True,
                    "sha1": {
                        "actual": hashlib.sha1(private_archive_payload).hexdigest(),
                        "expected": hashlib.sha1(private_archive_payload).hexdigest(),
                    },
                    "size": {
                        "actual": len(private_archive_payload),
                        "expected": str(len(private_archive_payload)),
                    },
                }
            ],
            "counts": private_report_counts,
            "coverage": {
                "duplicate_live_cpvs": {},
                "extra_indexed_archives": [],
                "missing_live_cpvs": [],
                "unindexed_gpkg_archives": [],
            },
            "inputs": {
                "allow_extra_archives": False,
                "packages_index": os.fspath(private_pkgdir / "Packages"),
                "snapshot": os.fspath(private_pkgdir),
                "validate_gpkg": True,
                "vdb": os.fspath(
                    transaction_report / "source-success-pkgdir-verification/selected-vdb"
                ),
                "zstd": os.fspath(fixture_tools["zstd"]),
            },
            "schema_version": 1,
            "status": "pass",
            "issues": [],
        }
        pkgdir_report_payload = canonical(pkgdir_report).encode()
        pkgdir_report_path.write_bytes(pkgdir_report_payload)
        pkgdir_report_path.chmod(0o600)
        payload_authority_value = {
            "schema_version": 1,
            "cpvs": [cpv],
            "payload_device": 123,
            "payload_root_sha256": digest(canonical(payload_root_observation).encode()),
            "per_cpv_paths": {cpv: destination_paths},
            "installed_rows": [
                {
                    "path": destination_paths[0],
                    "observation_sha256": "7" * 64,
                }
            ],
            "contents_paths": destination_paths,
        }
        payload_authority_value["rows_sha256"] = digest(
            canonical(payload_authority_value).encode()
        )
        payload_authority = {
            "value": payload_authority_value,
            "sha256": digest(canonical(payload_authority_value).encode()),
        }
        success_checks = {
            "qcheck": [qcheck_evidence],
            "private_pkgdir_report": os.fspath(pkgdir_report_path),
            "private_pkgdir_report_sha256": digest(pkgdir_report_payload),
            "payload_authority": payload_authority,
        }
        post_value = {
            "schema_version": 1,
            "outcome": "success",
            "live_etc_sha256": "8" * 64,
            "private_etc": {"changed": []},
            "private_cache_edb": {"changed": ["counter"]},
            "private_mtimedb": {"stable_sha256": "9" * 64},
            "private_roots": {"schema_version": 1},
            "vdb": delta,
            "loader_directories": [],
            "terminal_durability": {"schema_version": 1},
            "rows_sha256": "a" * 64,
        }
        post_authority = {
            "value": post_value,
            "sha256": digest(canonical(post_value).encode()),
        }
        completion = {
            "schema": "gentoo-optimization-jsonschema-child-completion-v1",
            "transaction_id": transaction_id,
            "recorded_at": "2026-08-22T00:05:00Z",
            "boot_id": "fixture-boot",
            "prepared_state_sha256": digest(prepared_payload),
            "armed_state_sha256": digest(armed_payload),
            "decision_state_sha256": digest(armed_payload),
            "child": child,
            "control_session_sha256": digest(source_control_session.encode()),
            "outcome": "success",
            "source_status": 0,
            "rollback_status": None,
            "counter": counter_authority,
            "vdb_sha256": "4" * 64,
            "logs": source_logs,
            "checks": success_checks,
            "payload_admissions": [admission],
            "post_emerge_authority": post_authority,
        }
        completion_path = transaction_report / "child-completion.json"
        completion_payload = write_pretty(completion_path, completion)
        outcome = {
            "source": {
                **source_logs,
                "status": 0,
                "spec_path": os.fspath(source_spec_path),
                "spec_sha256": digest(source_spec_payload),
                "counter_reconciliation": counter_authority,
                "postcheck_error": None,
            },
            "delta": delta,
            "checks": success_checks,
            "post_emerge_authority": post_authority,
            "child_completion": {
                "path": os.fspath(completion_path),
                "sha256": digest(completion_payload),
            },
        }
        success = {
            **common,
            "phase": "success",
            "previous_phase": "armed",
            "previous_state_sha256": digest(armed_payload),
            "prepared_state_sha256": digest(prepared_payload),
            "child": child,
            "outcome": outcome,
            "pending_total": 0,
        }
        success_path = state_parent / f"jsonschema-prerequisite-{transaction_id}.success.json"
        success_payload = write_pretty(success_path, success)
        success_canonical = state_parent / f"jsonschema-prerequisite-{transaction_id}.json"
        os.link(success_path, success_canonical)
        plan_cpvs_payload = (cpv + "\n").encode()
        plan_atoms_payload = ("=" + cpv + "\n").encode()
        post_operator_files = {
            "jsonschema-prerequisite-added-cpvs.txt": plan_cpvs_payload,
            "delta-atoms.txt": plan_atoms_payload,
            "expected-delta-atoms.txt": plan_atoms_payload,
            "jsonschema-prerequisite-state.sha256": (
                f"{digest(success_payload)}  {success_canonical}\n"
            ).encode(),
        }
        post = checkpoint(
            "post-fixture",
            live_cpvs=11,
            delta_cpvs=[cpv],
            source_durable=pre["durable"],
            witness_target=pre["durable"],
            operator_files=post_operator_files,
        )
        payloads = {
            "jsonschema-bootstrap-manifest": bootstrap_manifest_payload,
            "jsonschema-pre-checkpoint-terminal-state": pre["terminal_payload"],
            "jsonschema-pre-checkpoint-offline-restore-receipt": pre["receipt_payload"],
            "jsonschema-pre-checkpoint-operator-manifest": pre["operator_manifest_payload"],
            "jsonschema-prerequisite-success-state": success_payload,
            "jsonschema-post-checkpoint-terminal-state": post["terminal_payload"],
            "jsonschema-post-checkpoint-offline-restore-receipt": post["receipt_payload"],
            "jsonschema-post-checkpoint-operator-manifest": post["operator_manifest_payload"],
        }
        paths = {
            "jsonschema-bootstrap-manifest": bootstrap_manifest_path,
            "jsonschema-pre-checkpoint-terminal-state": pre["terminal"],
            "jsonschema-pre-checkpoint-offline-restore-receipt": pre["receipt"],
            "jsonschema-pre-checkpoint-operator-manifest": pre["operator_manifest"],
            "jsonschema-prerequisite-success-state": success_path,
            "jsonschema-post-checkpoint-terminal-state": post["terminal"],
            "jsonschema-post-checkpoint-offline-restore-receipt": post["receipt"],
            "jsonschema-post-checkpoint-operator-manifest": post["operator_manifest"],
        }
        return {
            "namespace": namespace,
            "payloads": payloads,
            "paths": paths,
            "repository": {
                "commit": candidate_b,
                "root": os.fspath(self.fixture.repository),
            },
            "pre": pre,
            "post": post,
            "selector": selector,
            "cpv": cpv,
            "checkpoint_builder": checkpoint,
        }

    def test_automation_external_chain_is_semantically_bound(self) -> None:
        fixture = self.automation_external_fixture()
        namespace = fixture["namespace"]
        namespace["validate_component_external_semantics"](
            "automation",
            self.fixture.run_id,
            fixture["payloads"],
            fixture["paths"],
            self.fixture.evidence,
            fixture["repository"],
            fixture["namespace"]["current_boot_id"](),
            False,
        )
        core_log = self.fixture.evidence / "core.log"
        namespace["component_document"](
            {
                "name": "automation",
                "external_evidence_labels": sorted(fixture["paths"]),
                "required_test_names": ["core"],
                "required_test_prefixes": [],
            },
            self.fixture.run_id,
            fixture["repository"],
            self.fixture.evidence / "test-run-provenance.json",
            self.fixture.evidence / "results.tsv",
            self.fixture.evidence / "subtests.tsv",
            self.fixture.evidence / "summary.txt",
            {
                "core": {
                    "status": "PASS",
                    "test": "core",
                    "detail": f"exit_status=0 log={core_log}",
                }
            },
            self.fixture.evidence,
            fixture["paths"],
            namespace["current_boot_id"](),
            False,
        )

    def test_automation_external_chain_rejects_manifest_and_residue_tampering(self) -> None:
        fixture = self.automation_external_fixture()
        validate = fixture["namespace"]["validate_component_external_semantics"]
        error_type = fixture["namespace"]["EvidenceError"]
        bad_bootstrap = json.loads(
            fixture["payloads"]["jsonschema-bootstrap-manifest"]
        )
        bad_bootstrap["commit"] = "0" * 40
        original_bootstrap = fixture["payloads"]["jsonschema-bootstrap-manifest"]
        fixture["payloads"]["jsonschema-bootstrap-manifest"] = fixture[
            "namespace"
        ]["pretty_json"](bad_bootstrap)
        with self.assertRaises(error_type):
            validate(
                "automation", self.fixture.run_id, fixture["payloads"], fixture["paths"],
                self.fixture.evidence, fixture["repository"],
                fixture["namespace"]["current_boot_id"](), False,
            )
        fixture["payloads"]["jsonschema-bootstrap-manifest"] = original_bootstrap

        pre_canonical = fixture["pre"]["canonical"]
        pre_canonical.unlink()
        pre_canonical.write_bytes(fixture["pre"]["terminal_payload"])
        pre_canonical.chmod(0o600)
        with self.assertRaises(error_type):
            validate(
                "automation", self.fixture.run_id, fixture["payloads"], fixture["paths"],
                self.fixture.evidence, fixture["repository"],
                fixture["namespace"]["current_boot_id"](), False,
            )
        pre_canonical.unlink()
        os.link(fixture["pre"]["terminal"], pre_canonical)

        operator_note = fixture["pre"]["operator_root"] / "checkpoint-note.txt"
        original = operator_note.read_bytes()
        operator_note.write_bytes(b"tampered\n")
        with self.assertRaises(error_type):
            validate(
                "automation", self.fixture.run_id, fixture["payloads"], fixture["paths"],
                self.fixture.evidence, fixture["repository"],
                fixture["namespace"]["current_boot_id"](), False,
            )
        operator_note.write_bytes(original)
        fixture["selector"].unlink()
        fixture["selector"].symlink_to(fixture["pre"]["durable"])
        with self.assertRaises(error_type):
            validate(
                "automation", self.fixture.run_id, fixture["payloads"], fixture["paths"],
                self.fixture.evidence, fixture["repository"],
                fixture["namespace"]["current_boot_id"](), False,
            )
        fixture["selector"].unlink()
        fixture["selector"].symlink_to(fixture["post"]["durable"])

        partial = fixture["post"]["receipt"].with_name(
            fixture["post"]["receipt"].name + ".partial"
        )
        partial.write_text("partial\n", encoding="utf-8")
        with self.assertRaises(error_type):
            validate(
                "automation", self.fixture.run_id, fixture["payloads"], fixture["paths"],
                self.fixture.evidence, fixture["repository"],
                fixture["namespace"]["current_boot_id"](), False,
            )

    def test_automation_external_chain_rejects_cross_checkpoint_delta_mismatch(self) -> None:
        fixture = self.automation_external_fixture()
        mismatch = fixture["checkpoint_builder"](
            "post-mismatch",
            live_cpvs=11,
            delta_cpvs=["dev-python/not-the-reviewed-plan-1"],
            source_durable=fixture["pre"]["durable"],
            witness_target=fixture["pre"]["durable"],
            operator_files={
                "jsonschema-prerequisite-added-cpvs.txt": (fixture["cpv"] + "\n").encode(),
                "delta-atoms.txt": ("=" + fixture["cpv"] + "\n").encode(),
                "expected-delta-atoms.txt": ("=" + fixture["cpv"] + "\n").encode(),
                "jsonschema-prerequisite-state.sha256": (
                    f"{digest(fixture['payloads']['jsonschema-prerequisite-success-state'])}  "
                    f"{fixture['paths']['jsonschema-prerequisite-success-state'].with_name('jsonschema-prerequisite-jsonschema-source-fixture.json')}\n"
                ).encode(),
            },
        )
        fixture["selector"].unlink()
        fixture["selector"].symlink_to(mismatch["durable"])
        fixture["payloads"].update(
            {
                "jsonschema-post-checkpoint-terminal-state": mismatch["terminal_payload"],
                "jsonschema-post-checkpoint-offline-restore-receipt": mismatch["receipt_payload"],
                "jsonschema-post-checkpoint-operator-manifest": mismatch["operator_manifest_payload"],
            }
        )
        fixture["paths"].update(
            {
                "jsonschema-post-checkpoint-terminal-state": mismatch["terminal"],
                "jsonschema-post-checkpoint-offline-restore-receipt": mismatch["receipt"],
                "jsonschema-post-checkpoint-operator-manifest": mismatch["operator_manifest"],
            }
        )
        with self.assertRaises(fixture["namespace"]["EvidenceError"]):
            fixture["namespace"]["validate_component_external_semantics"](
                "automation", self.fixture.run_id, fixture["payloads"], fixture["paths"],
                self.fixture.evidence, fixture["repository"],
                fixture["namespace"]["current_boot_id"](), False,
            )

    def test_capture_and_verify_bind_script_and_symlink_identity(self) -> None:
        self.fixture.run(check=True)
        self.fixture.run("verify", check=True)
        document = json.loads(self.fixture.index.read_text(encoding="utf-8"))
        script = next(item for item in document["tools"] if item["name"] == "script-tool")
        false = next(item for item in document["tools"] if item["name"] == "false")
        self.assertEqual(false["version_status"], 1)
        self.assertIn("false (GNU coreutils)", false["stdout"]["text"])
        self.assertEqual(script["requested_path"], os.fspath(self.fixture.script_link))
        self.assertEqual(script["resolved_path"], os.fspath(self.fixture.real_script))
        self.assertEqual(script["requested_entrypoint"]["type"], "symlink")
        self.assertEqual(
            script["requested_entrypoint"]["symlink_target"],
            self.fixture.real_script.name,
        )
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

    def test_nonzero_version_status_is_bound_and_status_tampering_is_rejected(self) -> None:
        self.fixture.run(check=True)
        document = json.loads(self.fixture.index.read_text(encoding="utf-8"))
        false = next(item for item in document["tools"] if item["name"] == "false")
        self.assertEqual(false["version_status"], 1)
        false["version_status"] = 0
        self.fixture.index.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result = self.fixture.run("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("observed identity differs", result.stderr)

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

    def test_executed_tool_provenance_is_indexed_and_tamper_evident(self) -> None:
        provenance_path = self.fixture.evidence / "test-run-provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [record["name"] for record in provenance["executed_tools"]],
            ["git", "python3", "script-tool"],
        )
        for record in provenance["executed_tools"]:
            self.assertEqual(record["entrypoint"]["name"], record["name"])
            self.assertRegex(record["runtime"]["binary"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(record["runtime"]["reported_path"].startswith("/"))
        script_record = next(
            record
            for record in provenance["executed_tools"]
            if record["name"] == "script-tool"
        )
        self.assertIsNotNone(script_record["entrypoint"]["shebang"])
        self.assertEqual(
            script_record["runtime"]["binary"],
            script_record["entrypoint"]["shebang"]["binary"],
        )
        self.assertNotEqual(
            script_record["runtime"]["binary"],
            script_record["entrypoint"]["binary"],
        )

        self.fixture.run(check=True)
        index = json.loads(self.fixture.index.read_text(encoding="utf-8"))
        self.assertEqual(
            index["test_run"]["executed_tools"], provenance["executed_tools"]
        )
        self.fixture.index.unlink()

        provenance["executed_tools"][0]["runtime"]["binary"]["sha256"] = "0" * 64
        provenance_path.write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("executed tool identity changed", result.stderr)

    def test_authoritative_provenance_rejects_unreviewed_execution_path(self) -> None:
        external_policy = self.fixture.root / "external-policy.json"
        external_policy.write_bytes(
            (self.fixture.repository / "policy.json").read_bytes()
        )
        escaped = subprocess.run(
            [
                sys.executable,
                os.fspath(TOOL),
                "run-provenance-start",
                "--repository-root",
                os.fspath(self.fixture.repository),
                "--driver",
                os.fspath(self.fixture.repository / "test-driver.sh"),
                "--policy",
                os.fspath(external_policy),
                "--git",
                os.fspath(self.fixture.git),
                "--executed-tool",
                f"git={self.fixture.git}",
                "--executed-tool",
                f"python3={Path(sys.executable)}",
                "--executed-tool",
                f"script-tool={self.fixture.script_link}",
                "--output",
                os.fspath(self.fixture.root / "escaped-policy.pending.json"),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(escaped.returncode, 0)
        self.assertIn("evidence policy escapes the repository", escaped.stderr)

        pending = self.fixture.root / "authoritative-tools.pending.json"
        result = subprocess.run(
            [
                sys.executable,
                os.fspath(TOOL),
                "run-provenance-start",
                "--repository-root",
                os.fspath(self.fixture.repository),
                "--driver",
                os.fspath(self.fixture.repository / "test-driver.sh"),
                "--policy",
                "policy.json",
                "--git",
                os.fspath(self.fixture.git),
                "--executed-tool",
                f"git={self.fixture.git}",
                "--executed-tool",
                f"python3={Path(sys.executable)}",
                "--executed-tool",
                f"script-tool={self.fixture.real_script}",
                "--authoritative-tools",
                "--output",
                os.fspath(pending),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "authoritative executed tool script-tool differs from its reviewed entry point",
            result.stderr,
        )
        self.assertFalse(pending.exists())

    def test_provenance_finalization_rejects_execution_tool_replacement(self) -> None:
        pending = self.fixture.root / "replacement.pending.json"
        output = self.fixture.root / "replacement.provenance.json"
        start = subprocess.run(
            [
                sys.executable,
                os.fspath(TOOL),
                "run-provenance-start",
                "--repository-root",
                os.fspath(self.fixture.repository),
                "--driver",
                os.fspath(self.fixture.repository / "test-driver.sh"),
                "--policy",
                "policy.json",
                "--git",
                os.fspath(self.fixture.git),
                "--executed-tool",
                f"git={self.fixture.git}",
                "--executed-tool",
                f"python3={Path(sys.executable)}",
                "--executed-tool",
                f"script-tool={self.fixture.script_link}",
                "--output",
                os.fspath(pending),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(start.returncode, 0, start.stderr)
        replacement = self.fixture.bin / "fixture-tool-replacement"
        replacement.write_text(
            "#!/bin/sh\nprintf 'fixture-tool 2.0\\n'\n", encoding="utf-8"
        )
        replacement.chmod(0o700)
        self.fixture.script_link.unlink()
        self.fixture.script_link.symlink_to(replacement.name)
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
        self.assertIn("executed tool identity changed", finish.stderr)
        self.assertFalse(output.exists())

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
                "--policy",
                "policy.json",
                "--git",
                os.fspath(self.fixture.git),
                "--executed-tool",
                f"git={self.fixture.git}",
                "--executed-tool",
                f"python3={Path(sys.executable)}",
                "--executed-tool",
                f"script-tool={self.fixture.script_link}",
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

    def test_requested_tool_symlink_same_target_replacement_is_rejected(self) -> None:
        self.fixture.run(check=True)
        replacement = self.fixture.bin / "replacement-link"
        replacement.symlink_to(self.fixture.real_script.name)
        self.assertNotEqual(
            replacement.lstat().st_ino,
            self.fixture.script_link.lstat().st_ino,
        )
        os.replace(replacement, self.fixture.script_link)
        self.assertEqual(
            self.fixture.script_link.resolve(strict=True), self.fixture.real_script
        )
        result = self.fixture.run("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("indexed tool topology", result.stderr)

    def test_detached_index_cannot_choose_its_own_tool_topology_or_specification(self) -> None:
        mutations: dict[str, Callable[[list[Any]], list[Any]]] = {
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
                document["tools"] = mutate(cast(list[Any], document["tools"]))
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

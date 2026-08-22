#!/usr/bin/env python3
"""Create and revalidate one exact Phase 2 evidence boundary.

The index is deliberately not a prose scraper.  Checked Phase 2 claims must
carry canonical markers in the plan, and every marker names the exact source
hashes which prove that claim.  Capture is possible only from a clean committed
tree and verification repeats every source, tool, test, evidence-tree, and
component-state observation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import grp
import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, NoReturn, Sequence


INDEX_SCHEMA = "gentoo-optimization-phase2-evidence-index-v1"
POLICY_SCHEMA = "gentoo-optimization-phase2-evidence-policy-v1"
AUTHORITATIVE_TEST_CONTRACT_SCHEMA = (
    "gentoo-optimization-phase2-authoritative-test-contract-v1"
)
TOOL_SCHEMA = "gentoo-optimization-phase2-tool-manifest-v1"
COMPONENT_SCHEMA = "gentoo-optimization-phase2-component-state-v1"
POLICY_RELATIVE = Path("optimization/phase2-evidence-policy.json")
VERIFIER_RELATIVE = Path("scripts/optimization/verify/phase2-evidence.py")
MARKER_PREFIX = "<!-- gentoo-optimization-phase2-evidence: "
MARKER_SUFFIX = " -->"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CHECKED_RE = re.compile(r"^\s*-\s+\[[xX]\]\s+")
TOP_TEST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,1023}$")
SUBTEST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$")


class EvidenceError(RuntimeError):
    """A fail-closed evidence-contract violation."""


def fail(message: str) -> NoReturn:
    raise EvidenceError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def pretty_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json_bytes(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"{label} is not UTF-8: {error}")
    try:
        return json.loads(text, object_pairs_hook=no_duplicate_object)
    except (json.JSONDecodeError, EvidenceError) as error:
        fail(f"{label} is not strict JSON: {error}")


def require_object(value: Any, label: str, keys: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    if keys is not None and set(value) != keys:
        fail(
            f"{label} keys differ: expected {sorted(keys)!r}, found {sorted(value)!r}"
        )
    return value


def require_list(value: Any, label: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        fail(f"{label} must be {'a nonempty' if nonempty else 'an'} array")
    return value


def require_string(value: Any, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        fail(f"{label} must be a nonempty string without NUL")
    if pattern is not None and pattern.fullmatch(value) is None:
        fail(f"{label} has an invalid value: {value!r}")
    return value


def require_int(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        fail(f"{label} must be an integer >= {minimum}")
    return value


def relative_path(value: Any, label: str) -> str:
    text = require_string(value, label)
    path = Path(text)
    if path.is_absolute() or text in {".", ".."} or ".." in path.parts:
        fail(f"{label} must be a confined non-root relative path")
    if os.fspath(path) != text or any(part in {"", "."} for part in path.parts):
        fail(f"{label} is not canonical: {text!r}")
    return text


def absolute_path(value: Any, label: str) -> Path:
    text = require_string(value, label)
    path = Path(text)
    if (
        not path.is_absolute()
        or path == Path("/")
        or os.fspath(path) != text
        or ".." in path.parts
    ):
        fail(f"{label} must be a canonical absolute non-root path")
    return path


def stat_identity(metadata: os.stat_result) -> dict[str, int]:
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "nlink": metadata.st_nlink,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def read_regular(
    path: Path, label: str, *, allow_hardlinks: bool = False
) -> tuple[bytes, dict[str, int]]:
    try:
        before = path.lstat()
    except OSError as error:
        fail(f"cannot inspect {label} {path}: {error}")
    if not stat.S_ISREG(before.st_mode) or before.st_nlink < 1:
        fail(f"{label} must be a regular file: {path}")
    if not allow_hardlinks and before.st_nlink != 1:
        fail(f"{label} must be a link-count-one regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        fail(f"cannot open {label} {path}: {error}")
    try:
        opened = os.fstat(descriptor)
        if stat_identity(opened) != stat_identity(before):
            fail(f"{label} changed while it was opened: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after_path = path.lstat()
    except OSError as error:
        fail(f"cannot re-inspect {label} {path}: {error}")
    identity = stat_identity(before)
    if identity != stat_identity(after_fd) or identity != stat_identity(after_path):
        fail(f"{label} changed while it was read: {path}")
    payload = b"".join(chunks)
    if len(payload) != before.st_size:
        fail(f"short read from {label}: {path}")
    return payload, identity


def file_identity(
    path: Path, label: str, *, allow_hardlinks: bool = False
) -> dict[str, object]:
    payload, identity = read_regular(
        path, label, allow_hardlinks=allow_hardlinks
    )
    return {"path": os.fspath(path), "sha256": sha256(payload), "stat": identity}


def requested_entrypoint_identity(path: Path, label: str) -> dict[str, object]:
    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"cannot inspect {label} {path}: {error}")
    if stat.S_ISREG(metadata.st_mode):
        object_type = "regular"
        symlink_target = None
    elif stat.S_ISLNK(metadata.st_mode):
        object_type = "symlink"
        try:
            symlink_target = os.readlink(path)
        except OSError as error:
            fail(f"cannot read {label} symlink {path}: {error}")
        if not symlink_target or "\x00" in symlink_target:
            fail(f"{label} has an invalid symlink target: {path}")
    else:
        fail(f"{label} must be a regular file or symlink: {path}")
    return {
        "path": os.fspath(path),
        "stat": stat_identity(metadata),
        "symlink_target": symlink_target,
        "type": object_type,
    }


def validate_root_trust(path: Path, label: str, *, directory: bool = False) -> None:
    """Require a real root-owned, non-writable ancestry and final object."""
    current = Path("/")
    try:
        root_metadata = current.lstat()
    except OSError as error:
        fail(f"cannot inspect production {label} ancestor /: {error}")
    root_mode = stat.S_IMODE(root_metadata.st_mode)
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != 0
        or root_mode & 0o022
    ):
        fail(f"production {label} is not rooted below a trusted / directory")
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        current /= part
        try:
            metadata = current.lstat()
        except OSError as error:
            fail(f"cannot inspect production {label} ancestor {current}: {error}")
        final = index == len(parts) - 1
        if stat.S_ISLNK(metadata.st_mode):
            fail(f"production {label} traverses a symlink: {current}")
        expected_directory = directory or not final
        if expected_directory and not stat.S_ISDIR(metadata.st_mode):
            fail(f"production {label} ancestor is not a directory: {current}")
        if final and not directory and not stat.S_ISREG(metadata.st_mode):
            fail(f"production {label} is not a regular file: {current}")
        mode = stat.S_IMODE(metadata.st_mode)
        if current == Path("/var/tmp") and mode == 0o1777 and metadata.st_uid == 0:
            continue
        if metadata.st_uid != 0 or mode & 0o022:
            fail(f"production {label} is not root-owned and non-writable: {current}")


def validate_root_trusted_entrypoint(path: Path, label: str) -> None:
    """Validate a reviewed executable entry point without erasing symlink ABI.

    Gentoo's reviewed Git and Python entry points are intentional symlinks. The
    symlink itself is protected by its trusted parent directory, while the
    resolved executable receives the ordinary full-file trust check.
    """

    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"cannot inspect production {label} entry point {path}: {error}")
    if stat.S_ISLNK(metadata.st_mode):
        validate_root_trust(path.parent, f"{label} entry-point parent", directory=True)
        if metadata.st_uid != 0:
            fail(f"production {label} entry-point symlink is not root-owned: {path}")
        return
    validate_root_trust(path, label)


def load_authoritative_test_contract(path: Path) -> dict[str, Any]:
    payload, _identity = read_regular(path, "authoritative test contract")
    document = require_object(
        parse_json_bytes(payload, "authoritative test contract"),
        "authoritative test contract",
        {
            "expected_diagnostic_subtests",
            "portable_allowed_required_skips",
            "portable_allowed_top_level_skips",
            "required_named_subtests",
            "schema",
            "top_level",
            "unittest_suites",
        },
    )
    if document["schema"] != AUTHORITATIVE_TEST_CONTRACT_SCHEMA:
        fail("authoritative test contract has the wrong schema")

    topology = require_object(
        document["top_level"],
        "authoritative top-level topology",
        {"exact_names", "prefix_groups"},
    )
    exact_names = [
        require_string(item, "authoritative exact test name", TOP_TEST_NAME_RE)
        for item in require_list(
            topology["exact_names"],
            "authoritative exact test names",
            nonempty=True,
        )
    ]
    if exact_names != sorted(set(exact_names)):
        fail("authoritative exact test names must be sorted and unique")

    prefix_groups: list[dict[str, object]] = []
    observed_group_names: set[str] = set()
    observed_prefixes: list[str] = []
    for index, raw_group in enumerate(
        require_list(
            topology["prefix_groups"],
            "authoritative test prefix groups",
        )
    ):
        group = require_object(
            raw_group,
            f"authoritative test prefix_groups[{index}]",
            {"expected_count", "expected_names", "prefix"},
        )
        prefix = require_string(
            group["prefix"], f"authoritative test prefix_groups[{index}].prefix"
        )
        expected_names = [
            require_string(
                item,
                f"authoritative test prefix_groups[{index}] expected name",
                TOP_TEST_NAME_RE,
            )
            for item in require_list(
                group["expected_names"],
                f"authoritative test prefix_groups[{index}].expected_names",
                nonempty=True,
            )
        ]
        expected_count = require_int(
            group["expected_count"],
            f"authoritative test prefix_groups[{index}].expected_count",
            1,
        )
        if expected_names != sorted(set(expected_names)):
            fail(f"authoritative test prefix group {prefix!r} is not sorted and unique")
        if expected_count != len(expected_names):
            fail(f"authoritative test prefix group {prefix!r} count is stale")
        if any(not name.startswith(prefix) for name in expected_names):
            fail(f"authoritative test prefix group {prefix!r} contains a foreign name")
        overlap = observed_group_names.intersection(expected_names)
        if overlap:
            fail(f"authoritative test prefix groups overlap: {sorted(overlap)!r}")
        observed_group_names.update(expected_names)
        observed_prefixes.append(prefix)
        prefix_groups.append(
            {
                "expected_count": expected_count,
                "expected_names": expected_names,
                "prefix": prefix,
            }
        )
    if observed_prefixes != sorted(set(observed_prefixes)):
        fail("authoritative test prefix groups must be sorted by unique prefix")
    overlap = set(exact_names).intersection(observed_group_names)
    if overlap:
        fail(f"authoritative exact and prefix-group test names overlap: {sorted(overlap)!r}")
    all_top_level_names = set(exact_names).union(observed_group_names)

    portable_top_level_skips = [
        require_string(
            item, "portable allowed top-level skip", TOP_TEST_NAME_RE
        )
        for item in require_list(
            document["portable_allowed_top_level_skips"],
            "portable allowed top-level skips",
        )
    ]
    if portable_top_level_skips != sorted(set(portable_top_level_skips)):
        fail("portable allowed top-level skips must be sorted and unique")
    unknown_portable_top = set(portable_top_level_skips) - all_top_level_names
    if unknown_portable_top:
        fail(
            "portable allowed top-level skips name unknown tests: "
            f"{sorted(unknown_portable_top)!r}"
        )

    def parse_subtest_identities(key: str) -> list[dict[str, str]]:
        parsed: list[dict[str, str]] = []
        identities: list[tuple[str, str]] = []
        for index, raw in enumerate(require_list(document[key], key)):
            item = require_object(raw, f"{key}[{index}]", {"subtest", "test"})
            test_name = require_string(
                item["test"], f"{key}[{index}].test", TOP_TEST_NAME_RE
            )
            subtest_name = require_string(
                item["subtest"], f"{key}[{index}].subtest", SUBTEST_NAME_RE
            )
            if test_name not in all_top_level_names:
                fail(f"{key} refers to unknown test: {test_name}")
            parsed.append({"subtest": subtest_name, "test": test_name})
            identities.append((test_name, subtest_name))
        if identities != sorted(set(identities)):
            fail(f"{key} must be sorted and unique")
        return parsed

    expected_diagnostic_subtests = parse_subtest_identities(
        "expected_diagnostic_subtests"
    )
    portable_allowed_required_skips = parse_subtest_identities(
        "portable_allowed_required_skips"
    )

    named_subtests: list[dict[str, str]] = []
    named_identities: list[tuple[str, str]] = []
    for index, raw_subtest in enumerate(
        require_list(
            document["required_named_subtests"],
            "required named subtests",
            nonempty=True,
        )
    ):
        subtest = require_object(
            raw_subtest,
            f"required_named_subtests[{index}]",
            {"subtest", "test"},
        )
        test_name = require_string(
            subtest["test"],
            f"required_named_subtests[{index}].test",
            TOP_TEST_NAME_RE,
        )
        subtest_name = require_string(
            subtest["subtest"],
            f"required_named_subtests[{index}].subtest",
            SUBTEST_NAME_RE,
        )
        if test_name not in all_top_level_names:
            fail(f"required named subtest refers to unknown test: {test_name}")
        named_subtests.append({"subtest": subtest_name, "test": test_name})
        named_identities.append((test_name, subtest_name))
    if named_identities != sorted(set(named_identities)):
        fail("required named subtests must be sorted and unique")

    unittest_suites: list[dict[str, object]] = []
    unittest_names: list[str] = []
    for index, raw_suite in enumerate(
        require_list(
            document["unittest_suites"],
            "authoritative unittest suites",
            nonempty=True,
        )
    ):
        suite = require_object(
            raw_suite,
            f"unittest_suites[{index}]",
            {"expected_count", "subtest_names_sha256", "test"},
        )
        test_name = require_string(
            suite["test"], f"unittest_suites[{index}].test", TOP_TEST_NAME_RE
        )
        if test_name not in all_top_level_names:
            fail(f"authoritative unittest suite refers to unknown test: {test_name}")
        expected_count = require_int(
            suite["expected_count"], f"unittest_suites[{index}].expected_count", 1
        )
        names_hash = require_string(
            suite["subtest_names_sha256"],
            f"unittest_suites[{index}].subtest_names_sha256",
            SHA256_RE,
        )
        unittest_suites.append(
            {
                "expected_count": expected_count,
                "subtest_names_sha256": names_hash,
                "test": test_name,
            }
        )
        unittest_names.append(test_name)
    if unittest_names != sorted(set(unittest_names)):
        fail("authoritative unittest suites must be sorted by unique test name")

    return {
        "expected_diagnostic_subtests": expected_diagnostic_subtests,
        "portable_allowed_required_skips": portable_allowed_required_skips,
        "portable_allowed_top_level_skips": portable_top_level_skips,
        "required_named_subtests": named_subtests,
        "schema": AUTHORITATIVE_TEST_CONTRACT_SCHEMA,
        "top_level": {
            "exact_names": exact_names,
            "prefix_groups": prefix_groups,
        },
        "unittest_suites": unittest_suites,
    }


def load_policy(repository: Path, policy_path: Path) -> dict[str, Any]:
    payload, _identity = read_regular(policy_path, "Phase 2 evidence policy")
    policy = require_object(
        parse_json_bytes(payload, "Phase 2 evidence policy"),
        "Phase 2 evidence policy",
        {
            "aggregate_requires_zero",
            "authoritative_test_path",
            "authoritative_test_contract_path",
            "component_state_path_template",
            "index_path_template",
            "phase",
            "phase_heading",
            "phase_next_heading",
            "plan_claims",
            "plan_path",
            "prior_evidence_banner",
            "require_all_phase_checkboxes_checked",
            "required_authoritative",
            "required_component_states",
            "required_passing_test_names",
            "required_passing_test_prefixes",
            "required_sources",
            "required_test_mode",
            "required_tools",
            "schema",
            "source_scopes",
            "test_driver_path",
            "test_execution_tools",
            "tool_manifest_template_path",
        },
    )
    if policy["schema"] != POLICY_SCHEMA or policy["phase"] != 2:
        fail("Phase 2 evidence policy has the wrong schema or phase")
    for key in (
        "aggregate_requires_zero",
        "require_all_phase_checkboxes_checked",
        "required_authoritative",
    ):
        if not isinstance(policy[key], bool):
            fail(f"{key} must be boolean")
    authoritative_path = [
        absolute_path(item, "authoritative_test_path item")
        for item in require_list(
            policy["authoritative_test_path"],
            "authoritative_test_path",
            nonempty=True,
        )
    ]
    authoritative_path_text = [os.fspath(item) for item in authoritative_path]
    if len(authoritative_path_text) != len(set(authoritative_path_text)):
        fail("authoritative_test_path must contain unique absolute directories")
    if any(
        ":" in item or "\n" in item or "\t" in item
        for item in authoritative_path_text
    ):
        fail("authoritative_test_path contains an unsafe path component")
    policy["authoritative_test_path"] = authoritative_path_text
    for key in (
        "required_tools",
        "required_passing_test_names",
        "required_passing_test_prefixes",
        "test_execution_tools",
    ):
        values = [require_string(item, f"{key} item") for item in require_list(policy[key], key, nonempty=True)]
        if values != sorted(set(values)):
            fail(f"{key} must be sorted and unique")
        if key in {"required_tools", "test_execution_tools"}:
            for value in values:
                require_string(value, f"{key} item", NAME_RE)
        policy[key] = values
    unknown_execution_tools = set(policy["test_execution_tools"]) - set(
        policy["required_tools"]
    )
    if unknown_execution_tools:
        fail(
            "test_execution_tools contains names absent from required_tools: "
            f"{sorted(unknown_execution_tools)!r}"
        )
    component_specs: list[dict[str, object]] = []
    component_names: list[str] = []
    for index, raw in enumerate(
        require_list(
            policy["required_component_states"],
            "required_component_states",
            nonempty=True,
        )
    ):
        specification = require_object(
            raw,
            f"required_component_states[{index}]",
            {
                "external_evidence_labels",
                "name",
                "required_test_names",
                "required_test_prefixes",
            },
        )
        name = require_string(
            specification["name"],
            f"required_component_states[{index}].name",
            NAME_RE,
        )
        parsed: dict[str, object] = {"name": name}
        for key in (
            "external_evidence_labels",
            "required_test_names",
            "required_test_prefixes",
        ):
            values = [
                require_string(
                    item,
                    f"required_component_states[{index}].{key} item",
                    NAME_RE if key == "external_evidence_labels" else None,
                )
                for item in require_list(
                    specification[key], f"required_component_states[{index}].{key}"
                )
            ]
            if values != sorted(set(values)):
                fail(f"component {name} {key} must be sorted and unique")
            parsed[key] = values
        if not parsed["required_test_names"] and not parsed["required_test_prefixes"]:
            fail(f"component {name} does not require any test result")
        component_specs.append(parsed)
        component_names.append(name)
    if component_names != sorted(set(component_names)):
        fail("component state names must be sorted and unique")
    required_test_names = set(policy["required_passing_test_names"])
    required_test_prefixes = set(policy["required_passing_test_prefixes"])
    for specification in component_specs:
        name = str(specification["name"])
        component_test_names = set(
            require_list(
                specification["required_test_names"],
                f"component {name} required test names",
            )
        )
        component_test_prefixes = set(
            require_list(
                specification["required_test_prefixes"],
                f"component {name} required test prefixes",
            )
        )
        unknown_names = component_test_names - required_test_names
        unknown_prefixes = component_test_prefixes - required_test_prefixes
        if unknown_names:
            fail(
                f"component {name} requires test names absent from the aggregate "
                f"policy: {sorted(unknown_names)!r}"
            )
        if unknown_prefixes:
            fail(
                f"component {name} requires test prefixes absent from the aggregate "
                f"policy: {sorted(unknown_prefixes)!r}"
            )
    policy["required_component_states"] = component_specs
    component_template = require_string(
        policy["component_state_path_template"],
        "component_state_path_template",
    )
    suffix = "/{run_id}/{name}.json"
    if (
        not component_template.endswith(suffix)
        or component_template.count("{run_id}") != 1
        or component_template.count("{name}") != 1
    ):
        fail(
            "component_state_path_template must end exactly in "
            "/{run_id}/{name}.json"
        )
    component_root_text = component_template[: -len(suffix)]
    if "{" in component_root_text or "}" in component_root_text:
        fail("component_state_path_template has an unsupported placeholder")
    component_root = absolute_path(
        component_root_text, "component_state_path_template root"
    )
    if component_root == Path("/"):
        fail("component state root may not be /")
    policy["component_state_root"] = component_root
    index_template = require_string(
        policy["index_path_template"], "index_path_template"
    )
    index_suffix = "/{run_id}/index.json"
    if (
        not index_template.endswith(index_suffix)
        or index_template.count("{run_id}") != 1
    ):
        fail("index_path_template must end exactly in /{run_id}/index.json")
    index_root_text = index_template[: -len(index_suffix)]
    if "{" in index_root_text or "}" in index_root_text:
        fail("index_path_template has an unsupported placeholder")
    index_root = absolute_path(index_root_text, "index_path_template root")
    if index_root == Path("/"):
        fail("evidence index root may not be /")
    policy["index_root"] = index_root
    for key in (
        "authoritative_test_contract_path",
        "plan_path",
        "test_driver_path",
        "tool_manifest_template_path",
    ):
        policy[key] = relative_path(policy[key], key)
    for key in ("phase_heading", "phase_next_heading", "prior_evidence_banner"):
        policy[key] = require_string(policy[key], key)
    if not policy["phase_heading"].startswith("# ") or not policy[
        "phase_next_heading"
    ].startswith("# "):
        fail("phase headings must be exact top-level Markdown headings")
    if policy["prior_evidence_banner"] != (
        "<!-- gentoo-optimization-phase2-prior-evidence: "
        "superseded-by-detached-index -->"
    ):
        fail("prior evidence banner differs from the reviewed contract")
    for key in ("source_scopes", "required_sources"):
        values = [relative_path(item, f"{key} item") for item in require_list(policy[key], key, nonempty=True)]
        if values != sorted(set(values)):
            fail(f"{key} must be sorted and unique")
        policy[key] = values
    claims: list[dict[str, object]] = []
    claim_ids: list[str] = []
    for index, raw in enumerate(require_list(policy["plan_claims"], "plan_claims", nonempty=True)):
        claim = require_object(raw, f"plan_claims[{index}]", {"claim_id", "source_paths"})
        claim_id = require_string(claim["claim_id"], f"plan_claims[{index}].claim_id", NAME_RE)
        sources = [relative_path(item, f"plan_claims[{index}].source_paths item") for item in require_list(claim["source_paths"], f"plan_claims[{index}].source_paths", nonempty=True)]
        if sources != sorted(set(sources)):
            fail(f"plan claim {claim_id} source paths must be sorted and unique")
        claims.append({"claim_id": claim_id, "source_paths": sources})
        claim_ids.append(claim_id)
    if claim_ids != sorted(set(claim_ids)):
        fail("plan claim IDs must be sorted and unique")
    policy["plan_claims"] = claims
    if policy["plan_path"] not in policy["required_sources"]:
        fail("the plan must be a required source")
    if policy["test_driver_path"] not in policy["required_sources"]:
        fail("the test driver must be a required source")
    if policy["tool_manifest_template_path"] not in policy["required_sources"]:
        fail("the tool manifest template must be a required source")
    if policy["authoritative_test_contract_path"] not in policy["required_sources"]:
        fail("the authoritative test contract must be a required source")
    for required in policy["required_sources"]:
        if not (repository / required).exists():
            fail(f"required source is absent: {required}")
    contract_path = repository / policy["authoritative_test_contract_path"]
    policy["authoritative_test_contract"] = load_authoritative_test_contract(
        contract_path
    )
    policy["_resolved_path"] = policy_path
    return policy


def component_state_path(
    policy: dict[str, Any], run_id: str, component_name: str
) -> Path:
    require_string(run_id, "Phase 2 evidence run ID", SAFE_ID_RE)
    require_string(component_name, "Phase 2 component name", NAME_RE)
    return Path(policy["component_state_root"]) / run_id / f"{component_name}.json"


def evidence_index_path(policy: dict[str, Any], run_id: str) -> Path:
    require_string(run_id, "Phase 2 evidence run ID", SAFE_ID_RE)
    return Path(policy["index_root"]) / run_id / "index.json"


def tool_manifest(path: Path, required_names: list[str]) -> list[dict[str, Any]]:
    payload, _identity = read_regular(path, "tool manifest")
    document = require_object(
        parse_json_bytes(payload, "tool manifest"), "tool manifest", {"schema", "tools"}
    )
    if document["schema"] != TOOL_SCHEMA:
        fail("tool manifest schema is invalid")
    tools: list[dict[str, Any]] = []
    names: list[str] = []
    for index, raw in enumerate(require_list(document["tools"], "tools", nonempty=True)):
        item = require_object(raw, f"tools[{index}]")
        base_keys = {"name", "path", "version_args"}
        distribution_keys = {"python_distribution", "python_import_roots"}
        returncode_keys = {"version_returncodes"}
        if set(item) not in (
            base_keys,
            base_keys | distribution_keys,
            base_keys | returncode_keys,
            base_keys | distribution_keys | returncode_keys,
        ):
            fail(f"tools[{index}] keys differ from the tool manifest contract")
        name = require_string(item["name"], f"tools[{index}].name", NAME_RE)
        path_value = absolute_path(item["path"], f"tools[{index}].path")
        args = [require_string(arg, f"tools[{index}].version_args item") for arg in require_list(item["version_args"], f"tools[{index}].version_args", nonempty=True)]
        returncodes = [
            require_int(code, f"tools[{index}].version_returncodes item")
            for code in require_list(
                item.get("version_returncodes", [0]),
                f"tools[{index}].version_returncodes",
                nonempty=True,
            )
        ]
        if returncodes != sorted(set(returncodes)) or any(
            code > 255 for code in returncodes
        ):
            fail(f"tools[{index}].version_returncodes must be sorted unique bytes")
        python_distribution = item.get("python_distribution")
        if python_distribution is not None:
            python_distribution = require_string(
                python_distribution,
                f"tools[{index}].python_distribution",
                NAME_RE,
            )
        python_import_roots = [
            require_string(
                root,
                f"tools[{index}].python_import_roots item",
                NAME_RE,
            )
            for root in require_list(
                item.get("python_import_roots", []),
                f"tools[{index}].python_import_roots",
                nonempty=python_distribution is not None,
            )
        ]
        if python_import_roots != sorted(set(python_import_roots)):
            fail(f"tools[{index}].python_import_roots must be sorted and unique")
        if python_distribution is None and python_import_roots:
            fail(f"tools[{index}] has import roots without a Python distribution")
        tools.append(
            {
                "name": name,
                "path": path_value,
                "python_distribution": python_distribution,
                "python_import_roots": python_import_roots,
                "version_args": args,
                "version_returncodes": returncodes,
            }
        )
        names.append(name)
    if names != sorted(set(names)) or names != required_names:
        fail(f"tool manifest names must exactly equal policy names: {required_names!r}")
    return tools


def observe_python_distribution(
    requested: Path,
    resolved: Path,
    distribution_name: str,
    import_roots: list[str],
    production: bool,
) -> dict[str, object]:
    probe = """import importlib.metadata as m
import importlib.util
import json
import os
import sys
d=m.distribution(sys.argv[1])
names=json.loads(sys.argv[2])
locations=[]
for name in names:
    spec=importlib.util.find_spec(name)
    if spec is None:
        raise SystemExit(f'missing import root: {name}')
    if spec.submodule_search_locations is not None:
        locations.extend(os.path.abspath(os.fspath(item)) for item in spec.submodule_search_locations)
    elif spec.origin is not None:
        locations.append(os.path.abspath(os.fspath(spec.origin)))
files=[] if d.files is None else [os.path.abspath(os.fspath(d.locate_file(item))) for item in d.files]
print(json.dumps({'declared_files':sorted(files),'import_locations':sorted(locations),'metadata_path':os.path.abspath(os.fspath(d._path)),'name':d.metadata['Name'],'version':d.version},sort_keys=True,separators=(',',':')))
"""
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    try:
        result = subprocess.run(
            [
                os.fspath(requested),
                "-I",
                "-B",
                "-c",
                probe,
                distribution_name,
                json.dumps(import_roots, separators=(",", ":")),
            ],
            executable=os.fspath(resolved),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"cannot inventory Python distribution {distribution_name}: {error}")
    if result.returncode != 0:
        fail(
            f"Python distribution inventory exited {result.returncode} for "
            f"{distribution_name}"
        )
    document = require_object(
        parse_json_bytes(result.stdout, f"Python distribution {distribution_name}"),
        f"Python distribution {distribution_name}",
        {"declared_files", "import_locations", "metadata_path", "name", "version"},
    )
    metadata_name = require_string(
        document["name"], f"Python distribution {distribution_name} metadata name"
    )
    version = require_string(
        document["version"], f"Python distribution {distribution_name} version"
    )
    declared_paths = [
        absolute_path(item, f"Python distribution {distribution_name} declared file")
        for item in require_list(
            document["declared_files"],
            f"Python distribution {distribution_name} declared files",
        )
    ]
    declared_text = [os.fspath(path) for path in declared_paths]
    if declared_text != sorted(set(declared_text)):
        fail(f"Python distribution {distribution_name} declared files are not sorted and unique")
    root_paths = [
        absolute_path(item, f"Python distribution {distribution_name} import location")
        for item in require_list(
            document["import_locations"],
            f"Python distribution {distribution_name} import locations",
            nonempty=True,
        )
    ]
    root_paths.append(
        absolute_path(
            document["metadata_path"],
            f"Python distribution {distribution_name} metadata path",
        )
    )
    root_text = [os.fspath(path) for path in root_paths]
    if len(root_text) != len(set(root_text)):
        fail(f"Python distribution {distribution_name} roots are not unique")
    root_paths.sort(key=os.fspath)
    candidate_paths = set(declared_paths)
    for root in root_paths:
        try:
            root_metadata = root.lstat()
        except OSError as error:
            fail(f"cannot inspect Python distribution root {root}: {error}")
        if stat.S_ISREG(root_metadata.st_mode):
            candidate_paths.add(root)
            continue
        if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
            fail(f"Python distribution root is not a real directory or file: {root}")
        for directory, directory_names, file_names in os.walk(
            root, topdown=True, followlinks=False
        ):
            directory_names.sort()
            file_names.sort()
            for child_name in directory_names:
                child = Path(directory) / child_name
                child_metadata = child.lstat()
                if not stat.S_ISDIR(child_metadata.st_mode) or stat.S_ISLNK(
                    child_metadata.st_mode
                ):
                    fail(f"Python distribution tree contains a non-directory: {child}")
            for child_name in file_names:
                candidate_paths.add(Path(directory) / child_name)
    raw_paths = sorted(candidate_paths, key=os.fspath)
    if not raw_paths:
        fail(f"Python distribution {distribution_name} inventory is empty")
    entries: list[dict[str, object]] = []
    for path in raw_paths:
        if production:
            validate_root_trust(path, f"Python distribution {distribution_name} file")
        entries.append(
            file_identity(
                path,
                f"Python distribution {distribution_name} file",
                allow_hardlinks=True,
            )
        )
    return {
        "distribution": distribution_name,
        "entry_count": len(entries),
        "entries": entries,
        "entries_sha256": sha256(canonical_json(entries)),
        "import_roots": import_roots,
        "metadata_name": metadata_name,
        "version": version,
    }


def observe_tool(specification: dict[str, Any], production: bool) -> dict[str, object]:
    name = specification["name"]
    requested: Path = specification["path"]
    requested_entrypoint = requested_entrypoint_identity(
        requested, f"tool {name} requested entry point"
    )
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve tool {name}: {error}")
    if not resolved.is_absolute() or not resolved.is_file() or not os.access(resolved, os.X_OK):
        fail(f"tool {name} does not resolve to an executable regular file")
    if production:
        validate_root_trusted_entrypoint(requested, f"tool {name}")
        validate_root_trust(resolved, f"tool {name}")
    if requested_entrypoint_identity(
        requested, f"tool {name} requested entry point"
    ) != requested_entrypoint:
        fail(f"tool {name} requested entry point changed during resolution")
    binary_payload, binary_stat = read_regular(resolved, f"tool {name}")
    binary = {
        "path": os.fspath(resolved),
        "sha256": sha256(binary_payload),
        "stat": binary_stat,
    }
    shebang: dict[str, object] | None = None
    if binary_payload.startswith(b"#!"):
        first_line = binary_payload.splitlines()[0]
        try:
            shebang_text = first_line[2:].decode("utf-8").strip()
            shebang_argv = shlex.split(shebang_text)
        except (UnicodeDecodeError, ValueError) as error:
            fail(f"tool {name} has an invalid shebang: {error}")
        if not shebang_argv:
            fail(f"tool {name} has an empty shebang")
        interpreter_requested = absolute_path(
            shebang_argv[0], f"tool {name} shebang interpreter"
        )
        if interpreter_requested == Path("/usr/bin/env"):
            fail(
                f"tool {name} uses an environment-resolved shebang which cannot "
                "be authorized as an exact tool identity"
            )
        try:
            interpreter_resolved = interpreter_requested.resolve(strict=True)
        except OSError as error:
            fail(f"cannot resolve tool {name} shebang interpreter: {error}")
        if not interpreter_resolved.is_file() or not os.access(
            interpreter_resolved, os.X_OK
        ):
            fail(f"tool {name} shebang interpreter is not executable")
        if production:
            validate_root_trusted_entrypoint(
                interpreter_requested, f"tool {name} shebang interpreter"
            )
            validate_root_trust(
                interpreter_resolved, f"tool {name} shebang interpreter"
            )
        shebang = {
            "line": shebang_text,
            "argv": shebang_argv,
            "requested_path": os.fspath(interpreter_requested),
            "resolved_path": os.fspath(interpreter_resolved),
            "binary": file_identity(
                interpreter_resolved, f"tool {name} shebang interpreter"
            ),
        }
    # Execute the requested entry point so argv-zero dispatchers such as
    # perf2bolt and python-exec retain their real interface.  The resolved
    # regular file is separately hashed and trust-checked.
    argv = [os.fspath(requested), *specification["version_args"]]
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    try:
        result = subprocess.run(
            argv,
            executable=os.fspath(resolved),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"tool identity command failed for {name}: {error}")
    if result.returncode not in specification.get("version_returncodes", [0]):
        fail(f"tool identity command exited {result.returncode} for {name}")
    try:
        stdout = result.stdout.decode("utf-8")
        stderr = result.stderr.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"tool identity output is not UTF-8 for {name}: {error}")
    if not stdout and not stderr:
        fail(f"tool identity command produced no output for {name}")
    try:
        after_resolved = requested.resolve(strict=True)
    except OSError as error:
        fail(f"cannot re-resolve tool {name} after execution: {error}")
    if after_resolved != resolved:
        fail(f"tool {name} requested entry point changed during observation")
    if requested_entrypoint_identity(
        requested, f"tool {name} requested entry point"
    ) != requested_entrypoint:
        fail(f"tool {name} requested entry point object changed during observation")
    python_distribution = None
    if specification.get("python_distribution") is not None:
        python_distribution = observe_python_distribution(
            requested,
            resolved,
            str(specification["python_distribution"]),
            [str(item) for item in specification["python_import_roots"]],
            production,
        )
    return {
        "name": name,
        "python_distribution": python_distribution,
        "requested_entrypoint": requested_entrypoint,
        "requested_path": os.fspath(requested),
        "resolved_path": os.fspath(resolved),
        "binary": binary,
        "shebang": shebang,
        "version_argv": argv,
        "version_status": result.returncode,
        "stdout": {"text": stdout, "sha256": sha256(result.stdout)},
        "stderr": {"text": stderr, "sha256": sha256(result.stderr)},
    }


def process_identity(pid: int, label: str) -> tuple[int, int]:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        fail(f"{label} PID must be an integer greater than one")
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        fail(f"cannot read {label} process identity: {error}")
    delimiter = payload.rfind(") ")
    fields = payload[delimiter + 2 :].split() if delimiter >= 0 else []
    if len(fields) < 20:
        fail(f"{label} process identity is malformed")
    try:
        return int(fields[1]), int(fields[19])
    except ValueError:
        fail(f"{label} process identity contains a non-integer field")


def require_ancestor_process(pid: int, label: str) -> int:
    expected_parent, start_time = process_identity(pid, label)
    current = os.getpid()
    observed: set[int] = set()
    while current > 1 and current not in observed:
        observed.add(current)
        parent, _current_start = process_identity(current, "provenance process")
        if parent == pid:
            after_parent, after_start = process_identity(pid, label)
            if (after_parent, after_start) != (expected_parent, start_time):
                fail(f"{label} process identity changed during ancestry observation")
            return start_time
        current = parent
    fail(f"{label} is not an ancestor of the provenance process")


def process_executable_identity(pid: int, label: str) -> dict[str, object]:
    _parent, before_start = process_identity(pid, label)
    try:
        raw_path = os.readlink(f"/proc/{pid}/exe")
    except OSError as error:
        fail(f"cannot read {label} executable identity: {error}")
    if raw_path.endswith(" (deleted)"):
        fail(f"{label} executable has been unlinked")
    executable = absolute_path(raw_path, f"{label} executable path")
    try:
        executable = executable.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve {label} executable: {error}")
    identity = file_identity(executable, f"{label} executable", allow_hardlinks=True)
    _after_parent, after_start = process_identity(pid, label)
    if after_start != before_start:
        fail(f"{label} process identity changed during observation")
    return identity


def process_argv0(pid: int, label: str) -> str:
    _parent, before_start = process_identity(pid, label)
    try:
        payload = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError as error:
        fail(f"cannot read {label} command line: {error}")
    _after_parent, after_start = process_identity(pid, label)
    if after_start != before_start:
        fail(f"{label} process changed while its command line was observed")
    if not payload or b"\0" not in payload:
        fail(f"{label} command line is empty or malformed")
    raw_argv0 = payload.split(b"\0", 1)[0]
    try:
        argv0 = raw_argv0.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"{label} argv[0] is not UTF-8: {error}")
    if not argv0:
        fail(f"{label} argv[0] is empty")
    return argv0


def observe_python_runtime(
    requested: Path, resolved: Path, production: bool
) -> dict[str, object]:
    probe = (
        "import os,sys;"
        "print(os.readlink('/proc/self/exe'));"
        "print(sys.executable)"
    )
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    try:
        result = subprocess.run(
            [os.fspath(requested), "-I", "-B", "-c", probe],
            executable=os.fspath(resolved),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"cannot observe active Python runtime executable: {error}")
    if result.returncode != 0:
        fail(f"Python runtime probe exited {result.returncode}")
    try:
        lines = result.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        fail(f"Python runtime probe output is not UTF-8: {error}")
    if len(lines) != 2:
        fail("Python runtime probe did not emit exactly two paths")
    runtime_path = absolute_path(lines[0], "Python runtime /proc executable")
    reported_path = absolute_path(lines[1], "Python reported executable")
    try:
        runtime_path = runtime_path.resolve(strict=True)
        reported_path = reported_path.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve Python runtime executable identity: {error}")
    if production:
        validate_root_trust(runtime_path, "Python runtime executable")
        validate_root_trust(reported_path, "Python reported executable")
    return {
        "binary": file_identity(
            runtime_path, "Python runtime executable", allow_hardlinks=True
        ),
        "reported_path": os.fspath(reported_path),
    }


def observe_execution_tools(
    repository: Path,
    policy: dict[str, Any],
    requested_paths: dict[str, Path],
    authoritative_tools: bool,
    production: bool,
) -> list[dict[str, object]]:
    specifications = tool_manifest(
        repository / policy["tool_manifest_template_path"],
        policy["required_tools"],
    )
    specifications_by_name = {str(item["name"]): item for item in specifications}
    expected_names = policy["test_execution_tools"]
    if sorted(requested_paths) != expected_names:
        fail(
            "executed tool names differ from the policy execution core: "
            f"expected={expected_names!r} actual={sorted(requested_paths)!r}"
        )
    if authoritative_tools:
        for name in expected_names:
            requested = requested_paths[name]
            reviewed = specifications_by_name[name]["path"]
            if requested != reviewed:
                fail(
                    f"authoritative executed tool {name} differs from its reviewed "
                    f"entry point: expected={reviewed} actual={requested}"
                )
    observed: list[dict[str, object]] = []
    for name in expected_names:
        specification = specifications_by_name[name]
        requested = requested_paths[name]
        active_specification = dict(specification)
        active_specification["path"] = requested
        entrypoint = observe_tool(active_specification, production)
        resolved = Path(str(entrypoint["resolved_path"]))
        runtime: dict[str, object]
        if name == "python3":
            runtime = observe_python_runtime(requested, resolved, production)
        elif entrypoint["shebang"] is not None:
            shebang = require_object(
                entrypoint["shebang"],
                f"executed tool {name} shebang",
                {"argv", "binary", "line", "requested_path", "resolved_path"},
            )
            runtime = {
                "binary": shebang["binary"],
                "reported_path": shebang["resolved_path"],
            }
        else:
            runtime = {
                "binary": entrypoint["binary"],
                "reported_path": entrypoint["resolved_path"],
            }
        observed.append(
            {
                "entrypoint": entrypoint,
                "name": name,
                "runtime": runtime,
            }
        )
    return observed


def recorded_execution_tool_paths(value: Any) -> dict[str, Path]:
    records = require_list(value, "test-run executed tools", nonempty=True)
    mapped: dict[str, Path] = {}
    for index, raw in enumerate(records):
        record = require_object(
            raw,
            f"test-run executed_tools[{index}]",
            {"entrypoint", "name", "runtime"},
        )
        name = require_string(
            record["name"], f"test-run executed_tools[{index}].name", NAME_RE
        )
        entrypoint = require_object(
            record["entrypoint"],
            f"test-run executed_tools[{index}].entrypoint",
        )
        requested = absolute_path(
            entrypoint.get("requested_path"),
            f"test-run executed_tools[{index}] requested path",
        )
        if name in mapped:
            fail(f"duplicate test-run executed tool: {name}")
        mapped[name] = requested
    if list(mapped) != sorted(mapped):
        fail("test-run executed tools are not sorted by unique name")
    return mapped


def validate_execution_tool_records(
    value: Any,
    repository: Path,
    policy: dict[str, Any],
    authoritative_tools: bool,
    production: bool,
) -> list[dict[str, object]]:
    requested_paths = recorded_execution_tool_paths(value)
    current = observe_execution_tools(
        repository,
        policy,
        requested_paths,
        authoritative_tools,
        production,
    )
    if current != value:
        fail("test-run executed tool identity changed")
    if production and not authoritative_tools:
        fail("production evidence requires authoritative executed-tool binding")
    return current


def current_python_runtime_identity() -> dict[str, object]:
    try:
        reported = Path(sys.executable).resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve the active Python reported executable: {error}")
    return {
        "binary": process_executable_identity(
            os.getpid(), "active provenance Python"
        ),
        "reported_path": os.fspath(reported),
    }


def execution_tool_by_name(
    records: list[dict[str, object]], name: str
) -> dict[str, object]:
    matches = [record for record in records if record.get("name") == name]
    if len(matches) != 1:
        fail(f"test-run executed tools do not contain exactly one {name} record")
    return matches[0]


def observe_driver_shell(
    pid: int, execution_tools: list[dict[str, object]]
) -> dict[str, object]:
    start_time = require_ancestor_process(pid, "active test-driver Bash")
    executable = process_executable_identity(pid, "active test-driver Bash")
    argv0 = process_argv0(pid, "active test-driver Bash")
    if require_ancestor_process(pid, "active test-driver Bash") != start_time:
        fail("active test-driver Bash changed during observation")
    bash_record = execution_tool_by_name(execution_tools, "bash")
    runtime = require_object(
        bash_record.get("runtime"), "executed Bash runtime", {"binary", "reported_path"}
    )
    if executable != runtime["binary"]:
        fail("active test-driver Bash differs from the recorded Bash runtime")
    bash_entrypoint = require_object(
        bash_record.get("entrypoint"), "executed Bash entry point"
    )
    if argv0 != bash_entrypoint.get("requested_path"):
        fail("active test-driver Bash was not invoked through its requested path")
    return {
        "argv0": argv0,
        "executable": executable,
        "pid": pid,
        "start_time": start_time,
    }


def require_active_python_matches(
    execution_tools: list[dict[str, object]], label: str
) -> None:
    python_record = execution_tool_by_name(execution_tools, "python3")
    if current_python_runtime_identity() != python_record.get("runtime"):
        fail(f"{label} did not execute with the recorded Python runtime")


def require_active_python_matches_reviewed_tools(
    reviewed_tools: list[dict[str, object]], label: str, production: bool = True
) -> None:
    python_record = execution_tool_by_name(reviewed_tools, "python3")
    requested = absolute_path(
        python_record.get("requested_path"), "reviewed Python requested path"
    )
    resolved = absolute_path(
        python_record.get("resolved_path"), "reviewed Python resolved path"
    )
    # Gentoo's reviewed /usr/bin/python3 is an argv-zero-sensitive python-exec
    # dispatcher.  Its requested and resolved entry-point identities therefore
    # differ from the interpreter that ultimately owns /proc/self/exe.  Probe
    # the reviewed entry point exactly as execution-tool provenance does rather
    # than assuming the dispatcher binary is the active Python runtime.
    expected = observe_python_runtime(requested, resolved, production)
    if current_python_runtime_identity() != expected:
        fail(f"{label} did not execute with the reviewed Python runtime")


def require_execution_entrypoints_match_reviewed_tools(
    execution_tools: list[dict[str, object]],
    reviewed_tools: list[dict[str, object]],
) -> None:
    reviewed_by_name = {str(record.get("name")): record for record in reviewed_tools}
    for record in execution_tools:
        name = str(record.get("name"))
        if record.get("entrypoint") != reviewed_by_name.get(name):
            fail(
                f"executed tool {name} differs from the independently observed "
                "reviewed tool manifest"
            )


def git_command_result(
    git: Path,
    repository: Path,
    arguments: Sequence[str],
    *,
    allowed_returncodes: frozenset[int] = frozenset({0}),
) -> subprocess.CompletedProcess[bytes]:
    if not allowed_returncodes:
        fail("Git inspection requires at least one allowed return code")
    command = [
        os.fspath(git),
        "--no-pager",
        "--no-replace-objects",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "diff.external=",
        "-c",
        f"safe.directory={repository}",
        "-C",
        os.fspath(repository),
        *arguments,
    ]
    environment = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"Git inspection failed: {error}")
    if result.returncode not in allowed_returncodes:
        detail = result.stderr.decode("utf-8", "replace").strip()
        fail(f"Git inspection exited {result.returncode}: {detail}")
    return result


def git_command(git: Path, repository: Path, arguments: Sequence[str]) -> bytes:
    return git_command_result(git, repository, arguments).stdout


def repository_identity(git: Path, repository: Path) -> dict[str, object]:
    commit = git_command(git, repository, ["rev-parse", "--verify", "HEAD^{commit}"]).decode().strip()
    tree = git_command(git, repository, ["show", "-s", "--format=%T", "HEAD"]).decode().strip()
    if OID_RE.fullmatch(commit) is None or OID_RE.fullmatch(tree) is None:
        fail("Git returned an invalid commit or tree identity")
    status_output = git_command(git, repository, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if status_output:
        fail("authoritative Phase 2 evidence requires a clean worktree")
    reference_result = git_command_result(
        git,
        repository,
        ["symbolic-ref", "-q", "HEAD"],
        allowed_returncodes=frozenset({0, 1}),
    )
    head_ref = reference_result.stdout.decode().strip() if reference_result.returncode == 0 else None
    tree_listing = git_command(git, repository, ["ls-tree", "-r", "-z", "--full-tree", "HEAD"])
    if not tree_listing:
        fail("Git returned an empty full-tree listing")
    return {
        "root": os.fspath(repository),
        "commit": commit,
        "tree": tree,
        "source_tree_listing_sha256": sha256(tree_listing),
        "head_ref": head_ref,
        "clean": True,
    }


def source_inventory(git: Path, repository: Path, policy: dict[str, Any]) -> list[dict[str, object]]:
    output = git_command(git, repository, ["ls-files", "-z", "--", *policy["source_scopes"]])
    paths = [item.decode("utf-8") for item in output.split(b"\0") if item]
    if paths != sorted(set(paths)) or not paths:
        fail("Git source-scope inventory is empty, duplicated, or unsorted")
    missing = sorted(set(policy["required_sources"]) - set(paths))
    if missing:
        fail(f"required sources are not tracked in the evidence scopes: {missing!r}")
    stage_output = git_command(git, repository, ["ls-files", "--stage", "-z", "--", *paths])
    stage_by_path: dict[str, tuple[str, str]] = {}
    for raw in stage_output.split(b"\0"):
        if not raw:
            continue
        try:
            metadata_bytes, raw_path = raw.split(b"\t", 1)
            mode, object_id, stage = metadata_bytes.decode("ascii").split(" ")
            source_path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            fail(f"cannot parse Git source stage entry: {error}")
        if stage != "0" or mode not in {"100644", "100755", "120000"} or OID_RE.fullmatch(object_id) is None:
            fail(f"unsupported Git source entry: {source_path}")
        if source_path in stage_by_path:
            fail(f"duplicate Git source stage entry: {source_path}")
        stage_by_path[source_path] = (mode, object_id)
    if set(stage_by_path) != set(paths):
        fail("Git source stage inventory differs from the source scope")
    result: list[dict[str, object]] = []
    for source_path in paths:
        mode, object_id = stage_by_path[source_path]
        path = repository / source_path
        if mode == "120000":
            try:
                symlink_metadata = path.lstat()
                target = os.readlink(path).encode("utf-8")
            except OSError as error:
                fail(f"cannot inspect tracked symlink {source_path}: {error}")
            if not stat.S_ISLNK(symlink_metadata.st_mode):
                fail(f"tracked symlink is not a symlink: {source_path}")
            payload = target
            entry_type = "symlink"
        else:
            payload, identity = read_regular(path, f"tracked source {source_path}")
            expected_mode = 0o755 if mode == "100755" else 0o644
            if identity["mode"] != expected_mode:
                fail(f"tracked source mode differs from Git: {source_path}")
            entry_type = "regular"
        blob = git_command(git, repository, ["cat-file", "blob", object_id])
        if blob != payload:
            fail(f"tracked source bytes differ from Git: {source_path}")
        result.append(
            {
                "path": source_path,
                "type": entry_type,
                "git_mode": mode,
                "git_object": object_id,
                "sha256": sha256(payload),
                "size": len(payload),
            }
        )
    return result


def source_hashes(sources: list[dict[str, object]]) -> dict[str, str]:
    return {str(item["path"]): str(item["sha256"]) for item in sources}


def plan_claims(
    plan_path: Path, policy: dict[str, Any], hashes: dict[str, str]
) -> tuple[str, list[dict[str, object]]]:
    payload, _identity = read_regular(plan_path, "project plan")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        fail(f"project plan is not UTF-8: {error}")
    try:
        phase_start = lines.index(policy["phase_heading"])
        phase_end = lines.index(policy["phase_next_heading"])
    except ValueError as error:
        fail(f"project plan lacks an exact Phase 2 section boundary: {error}")
    if phase_start >= phase_end:
        fail("project plan Phase 2 section boundaries are reversed")
    phase_lines = lines[phase_start + 1 : phase_end]
    if phase_lines.count(policy["prior_evidence_banner"]) != 1:
        fail("Phase 2 must contain exactly one prior-evidence supersession banner")
    historical_pattern = re.compile(
        r"(?:SHA-256|commit `(?:[0-9a-f]{40}|[0-9a-f]{64})`|"
        r"/var/lib/gentoo-optimization/(?:reports|state)/|"
        r"(?<![0-9a-f])(?:[0-9a-f]{40}|[0-9a-f]{64})(?![0-9a-f]))"
    )
    historical_prefix = "> Historical Phase 2 evidence (superseded; not authorization): "
    for offset, line in enumerate(phase_lines, phase_start + 2):
        if line.startswith(MARKER_PREFIX):
            continue
        if historical_pattern.search(line) and not line.startswith(historical_prefix):
            fail(
                "Phase 2 historical hash/evidence prose is not explicitly superseded "
                f"at line {offset}"
            )
    checkbox_lines: dict[str, list[int]] = {}
    checked_phase_hashes: set[str] = set()
    open_phase_lines: list[int] = []
    for line_number in range(phase_start + 2, phase_end + 1):
        line = lines[line_number - 1]
        if CHECKED_RE.match(line):
            checkbox_hash = sha256(line.encode("utf-8"))
            checkbox_lines.setdefault(checkbox_hash, []).append(line_number)
            checked_phase_hashes.add(checkbox_hash)
        elif re.match(r"^\s*-\s+\[ \]\s+", line):
            open_phase_lines.append(line_number)
    if policy["require_all_phase_checkboxes_checked"] and open_phase_lines:
        fail(f"Phase 2 still has unchecked items at lines {open_phase_lines!r}")
    if not checked_phase_hashes:
        fail("Phase 2 contains no checked checklist items")
    expected_claims = {str(item["claim_id"]): item for item in policy["plan_claims"]}
    observed: dict[str, dict[str, object]] = {}
    for marker_line, line in enumerate(lines, 1):
        if not (line.startswith(MARKER_PREFIX) and line.endswith(MARKER_SUFFIX)):
            continue
        if not (phase_start + 2 <= marker_line <= phase_end):
            fail(f"Phase 2 evidence marker is outside the Phase 2 section at line {marker_line}")
        fragment = line[len(MARKER_PREFIX) : -len(MARKER_SUFFIX)]
        marker = require_object(
            parse_json_bytes(fragment.encode("utf-8"), f"plan marker at line {marker_line}"),
            f"plan marker at line {marker_line}",
            {"checkbox_sha256", "claim_id", "source_sha256"},
        )
        if canonical_json(marker).decode("utf-8") != fragment:
            fail(f"plan evidence marker at line {marker_line} is not canonical JSON")
        claim_id = require_string(marker["claim_id"], "plan marker claim_id", NAME_RE)
        checkbox_hashes = [
            require_string(value, "plan marker checkbox_sha256 item", SHA256_RE)
            for value in require_list(
                marker["checkbox_sha256"], "plan marker checkbox_sha256", nonempty=True
            )
        ]
        if checkbox_hashes != sorted(set(checkbox_hashes)):
            fail(f"plan claim {claim_id} checkbox hashes must be sorted and unique")
        if claim_id not in expected_claims or claim_id in observed:
            fail(f"unknown or duplicate plan evidence claim: {claim_id}")
        matched_lines: list[int] = []
        for checkbox_hash in checkbox_hashes:
            matches = checkbox_lines.get(checkbox_hash, [])
            if len(matches) != 1:
                fail(
                    f"plan claim {claim_id} does not bind each checkbox hash to "
                    "exactly one checked Phase 2 item"
                )
            matched_lines.append(matches[0])
        expected_sources = expected_claims[claim_id]["source_paths"]
        source_map = require_object(marker["source_sha256"], f"plan claim {claim_id} source_sha256")
        if sorted(source_map) != expected_sources:
            fail(f"plan claim {claim_id} source membership differs from policy")
        for source_path, recorded_hash in source_map.items():
            require_string(recorded_hash, f"plan claim {claim_id} hash for {source_path}", SHA256_RE)
            if hashes.get(source_path) != recorded_hash:
                fail(
                    f"checked plan evidence is stale for {claim_id}: {source_path} "
                    f"records {recorded_hash}, current tree is {hashes.get(source_path)}"
                )
        observed[claim_id] = {
            "claim_id": claim_id,
            "marker_line": marker_line,
            "checkbox_lines": sorted(matched_lines),
            "checkbox_sha256": checkbox_hashes,
            "source_sha256": source_map,
        }
    if set(observed) != set(expected_claims):
        fail(f"plan evidence claims are incomplete: missing {sorted(set(expected_claims) - set(observed))!r}")
    covered_hashes = [
        checkbox_hash
        for marker in observed.values()
        for checkbox_hash in require_list(
            marker["checkbox_sha256"], "observed checkbox hashes"
        )
    ]
    if len(covered_hashes) != len(set(covered_hashes)):
        fail("a checked Phase 2 checkbox is covered by more than one evidence claim")
    if set(covered_hashes) != checked_phase_hashes:
        fail(
            "every checked Phase 2 checkbox must be covered exactly once by the "
            "current detached-index claim markers"
        )
    return sha256(payload), [observed[name] for name in sorted(observed)]


def tree_manifest(root: Path, production: bool) -> dict[str, object]:
    if not root.is_absolute() or root == Path("/"):
        fail("evidence root must be an absolute non-root path")
    try:
        canonical = root.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve evidence root: {error}")
    if canonical != root or root.is_symlink() or not root.is_dir():
        fail("evidence root must be a canonical real directory")
    if production:
        validate_root_trust(root, "evidence root", directory=True)
    entries: list[dict[str, object]] = []

    def visit(directory: Path, relative: str) -> None:
        metadata = directory.lstat()
        if production and (
            metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            fail(
                "production evidence directory is not root-owned and "
                f"non-writable: {directory}"
            )
        entries.append({"path": relative, "type": "directory", "stat": stat_identity(metadata)})
        try:
            children = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as error:
            fail(f"cannot enumerate evidence directory {directory}: {error}")
        for child in children:
            child_path = Path(child.path)
            child_relative = child.name if relative == "." else f"{relative}/{child.name}"
            try:
                child_metadata = child.stat(follow_symlinks=False)
            except OSError as error:
                fail(f"cannot inspect evidence entry {child_path}: {error}")
            if stat.S_ISDIR(child_metadata.st_mode):
                visit(child_path, child_relative)
            elif stat.S_ISREG(child_metadata.st_mode):
                payload, identity = read_regular(child_path, "evidence file")
                if production and (identity["uid"] != 0 or identity["mode"] & 0o022):
                    fail(f"production evidence file is not root-owned and non-writable: {child_path}")
                entries.append(
                    {
                        "path": child_relative,
                        "type": "regular",
                        "stat": identity,
                        "sha256": sha256(payload),
                    }
                )
            else:
                fail(f"evidence tree contains a symlink or special object: {child_path}")

    visit(root, ".")
    entries.sort(key=lambda item: str(item["path"]))
    return {
        "root": os.fspath(root),
        "entry_count": len(entries),
        "entries_sha256": sha256(canonical_json(entries)),
        "entries": entries,
    }


def parse_subtests(
    subtests_path: Path,
    result_rows: dict[str, dict[str, str]],
    *,
    enforce_authoritative: bool = True,
) -> tuple[dict[str, int], dict[tuple[str, str], dict[str, str]]]:
    payload, _identity = read_regular(subtests_path, "structured subtest results")
    if not payload.endswith(b"\n"):
        fail("structured subtest results must end with newline")
    lines = payload.decode("utf-8").splitlines()
    if not lines or lines[0] != "status\trequirement\ttest\tsubtest\tdetail":
        fail("structured subtest results header is invalid")
    counts = {
        "required_pass": 0,
        "required_fail": 0,
        "required_skip": 0,
        "diagnostic_pass": 0,
        "diagnostic_fail": 0,
        "diagnostic_skip": 0,
        "mandatory_internal_skip": 0,
        "total": 0,
    }
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for line_number, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if (
            len(fields) != 5
            or fields[0] not in {"PASS", "FAIL", "SKIP"}
            or fields[1] not in {"required", "diagnostic"}
            or not fields[2]
            or not fields[3]
            or not fields[4]
        ):
            fail(f"invalid structured subtest row at line {line_number}")
        status_value, requirement, test_name, subtest_name, detail = fields
        if test_name not in result_rows:
            fail(f"structured subtest names an unknown top-level test: {test_name}")
        key = (test_name, subtest_name)
        if key in rows:
            fail(
                "duplicate structured subtest identity: "
                f"{test_name}/{subtest_name}"
            )
        rows[key] = {
            "status": status_value,
            "requirement": requirement,
            "detail": detail,
        }
        counts[f"{requirement}_{status_value.lower()}"] += 1
        if (
            status_value == "SKIP"
            and requirement == "required"
            and subtest_name != "driver.case-completion"
        ):
            counts["mandatory_internal_skip"] += 1
        counts["total"] += 1
    for test_name, result in result_rows.items():
        completion = rows.get((test_name, "driver.case-completion"))
        if completion is None:
            fail(
                "top-level test has no structured completion subtest: "
                f"{test_name}"
            )
        if (
            completion["requirement"] != "required"
            or completion["status"] != result["status"]
        ):
            fail(
                "structured completion status differs from the top-level result: "
                f"{test_name}"
            )
    if enforce_authoritative and (
        counts["required_fail"] != 0 or counts["required_skip"] != 0
    ):
        fail(
            "authoritative Phase 2 test run has a mandatory internal failure "
            "or skip"
        )
    if enforce_authoritative and counts["diagnostic_fail"] != 0:
        fail("authoritative Phase 2 test run has a diagnostic internal failure")
    return counts, rows


def contract_top_level_names(contract: dict[str, Any]) -> set[str]:
    topology = require_object(contract["top_level"], "authoritative topology")
    names = {str(item) for item in require_list(topology["exact_names"], "exact names")}
    for raw_group in require_list(topology["prefix_groups"], "prefix groups"):
        group = require_object(raw_group, "authoritative prefix group")
        names.update(str(item) for item in require_list(group["expected_names"], "expected names"))
    return names


def validate_authoritative_topology(
    contract: dict[str, Any], result_rows: dict[str, dict[str, str]]
) -> None:
    expected = contract_top_level_names(contract)
    observed = set(result_rows)
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing or unexpected:
        fail(
            "authoritative top-level test topology differs from its exact contract: "
            f"missing={missing!r} unexpected={unexpected!r}"
        )
    topology = require_object(contract["top_level"], "authoritative topology")
    for raw_group in require_list(topology["prefix_groups"], "prefix groups"):
        group = require_object(raw_group, "authoritative prefix group")
        prefix = str(group["prefix"])
        matches = sorted(name for name in observed if name.startswith(prefix))
        expected_names = [str(item) for item in require_list(group["expected_names"], "expected names")]
        if len(matches) != int(group["expected_count"]) or matches != expected_names:
            fail(f"authoritative top-level prefix group differs: {prefix}")


def subtest_name_set_sha256(names: Iterable[str]) -> str:
    ordered = sorted(names)
    return sha256(("\n".join(ordered) + "\n").encode("utf-8"))


def validate_authoritative_subtests(
    contract: dict[str, Any],
    subtest_rows: dict[tuple[str, str], dict[str, str]],
) -> tuple[dict[str, int], list[dict[str, str]]]:
    expected_diagnostic = {
        (str(item["test"]), str(item["subtest"]))
        for item in require_list(
            contract["expected_diagnostic_subtests"],
            "expected diagnostic subtests",
        )
    }
    observed_diagnostic_skips = {
        key
        for key, row in subtest_rows.items()
        if row["requirement"] == "diagnostic" and row["status"] == "SKIP"
    }
    if observed_diagnostic_skips != expected_diagnostic:
        fail(
            "authoritative diagnostic skip identities differ from the exact "
            f"contract: expected={sorted(expected_diagnostic)!r} "
            f"observed={sorted(observed_diagnostic_skips)!r}"
        )
    required_records: list[dict[str, str]] = []
    for raw_expected in require_list(
        contract["required_named_subtests"], "required named subtests"
    ):
        expected = require_object(raw_expected, "required named subtest")
        test_name = str(expected["test"])
        subtest_name = str(expected["subtest"])
        observed = subtest_rows.get((test_name, subtest_name))
        if observed is None:
            fail(f"required named subtest is absent: {test_name}/{subtest_name}")
        if observed["requirement"] != "required" or observed["status"] != "PASS":
            fail(f"required named subtest is not required PASS: {test_name}/{subtest_name}")
        required_records.append(
            {"status": "PASS", "subtest": subtest_name, "test": test_name}
        )

    unittest_test_total = 0
    for raw_suite in require_list(contract["unittest_suites"], "unittest suites"):
        suite = require_object(raw_suite, "authoritative unittest suite")
        test_name = str(suite["test"])
        names = sorted(
            subtest_name
            for (row_test, subtest_name), row in subtest_rows.items()
            if row_test == test_name and subtest_name.startswith("python.")
        )
        expected_count = int(suite["expected_count"])
        if len(names) != expected_count:
            fail(
                f"authoritative unittest identity count differs for {test_name}: "
                f"expected={expected_count} observed={len(names)}"
            )
        observed_hash = subtest_name_set_sha256(names)
        if observed_hash != suite["subtest_names_sha256"]:
            fail(f"authoritative unittest identity set differs for {test_name}")
        for subtest_name in names:
            row = subtest_rows[(test_name, subtest_name)]
            identity = (test_name, subtest_name)
            if identity in expected_diagnostic:
                expected_row = {"requirement": "diagnostic", "status": "SKIP"}
            else:
                expected_row = {"requirement": "required", "status": "PASS"}
            if any(row[key] != value for key, value in expected_row.items()):
                fail(
                    "authoritative unittest result differs from its exact contract: "
                    f"{test_name}/{subtest_name}"
                )
        unittest_test_total += len(names)

    totals = {
        "required_named_subtests": len(required_records),
        "expected_diagnostic_subtests": len(expected_diagnostic),
        "top_level_tests": len(contract_top_level_names(contract)),
        "unittest_suites": len(contract["unittest_suites"]),
        "unittest_tests": unittest_test_total,
    }
    return totals, required_records


def read_test_result_rows(
    results_path: Path,
) -> tuple[dict[str, int], dict[str, dict[str, str]]]:
    results_payload, _identity = read_regular(results_path, "test results")
    if not results_payload.endswith(b"\n"):
        fail("test results must end with newline")
    lines = results_payload.decode("utf-8").splitlines()
    if not lines or lines[0] != "status\ttest\tdetail":
        fail("test results header is invalid")
    rows: dict[str, dict[str, str]] = {}
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for line_number, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != 3 or fields[0] not in counts or not fields[1]:
            fail(f"invalid test result row at line {line_number}")
        status_value, name, detail = fields
        if name in rows:
            fail(f"duplicate test result name: {name}")
        rows[name] = {"status": status_value, "detail": detail}
        counts[status_value] += 1
    return counts, rows


def validate_test_contract_run(
    contract: dict[str, Any],
    results_path: Path,
    subtests_path: Path,
    mode: str,
) -> tuple[dict[str, int], dict[str, int]]:
    if mode not in {"authoritative", "portable-complete"}:
        fail("test-contract validation supports authoritative or portable-complete")
    counts, result_rows = read_test_result_rows(results_path)
    validate_authoritative_topology(contract, result_rows)
    subtest_counts, subtest_rows = parse_subtests(
        subtests_path, result_rows, enforce_authoritative=False
    )
    if counts["FAIL"] != 0 or subtest_counts["required_fail"] != 0 or subtest_counts["diagnostic_fail"] != 0:
        fail("contract-controlled test run contains a failure")

    portable_top_skips = set(contract["portable_allowed_top_level_skips"])
    for test_name, row in result_rows.items():
        allowed = {"PASS"}
        if mode == "portable-complete" and test_name in portable_top_skips:
            allowed.add("SKIP")
        if row["status"] not in allowed:
            fail(
                f"top-level test status is not allowed in {mode}: "
                f"{test_name}={row['status']}"
            )

    expected_diagnostic = {
        (str(item["test"]), str(item["subtest"]))
        for item in contract["expected_diagnostic_subtests"]
        if result_rows[str(item["test"])]["status"] != "SKIP"
    }
    observed_diagnostic_skips = {
        identity
        for identity, row in subtest_rows.items()
        if row["requirement"] == "diagnostic" and row["status"] == "SKIP"
    }
    if observed_diagnostic_skips != expected_diagnostic:
        fail(
            "diagnostic skip identities differ from the exact test contract: "
            f"expected={sorted(expected_diagnostic)!r} "
            f"observed={sorted(observed_diagnostic_skips)!r}"
        )

    portable_required_skips = {
        (str(item["test"]), str(item["subtest"]))
        for item in contract["portable_allowed_required_skips"]
    }
    observed_required_skips = {
        identity
        for identity, row in subtest_rows.items()
        if row["requirement"] == "required" and row["status"] == "SKIP"
    }
    allowed_required_skips: set[tuple[str, str]] = set()
    if mode == "portable-complete":
        allowed_required_skips.update(portable_required_skips)
        allowed_required_skips.update(
            (test_name, "driver.case-completion")
            for test_name in portable_top_skips
        )
    unexpected_required_skips = observed_required_skips - allowed_required_skips
    if unexpected_required_skips:
        fail(
            "required subtests skipped outside the portable host-only allowlist: "
            f"{sorted(unexpected_required_skips)!r}"
        )

    named_required = {
        (str(item["test"]), str(item["subtest"]))
        for item in contract["required_named_subtests"]
    }
    for identity in sorted(named_required):
        if result_rows[identity[0]]["status"] == "SKIP":
            continue
        named_row = subtest_rows.get(identity)
        if named_row is None:
            fail(f"required named subtest is absent: {identity[0]}/{identity[1]}")
        allowed_statuses = {"PASS"}
        if mode == "portable-complete" and identity in portable_required_skips:
            allowed_statuses.add("SKIP")
        if (
            named_row["requirement"] != "required"
            or named_row["status"] not in allowed_statuses
        ):
            fail(f"required named subtest has a forbidden result: {identity!r}")

    for suite in contract["unittest_suites"]:
        test_name = str(suite["test"])
        if result_rows[test_name]["status"] == "SKIP":
            continue
        names = sorted(
            subtest_name
            for (row_test, subtest_name) in subtest_rows
            if row_test == test_name and subtest_name.startswith("python.")
        )
        if len(names) != int(suite["expected_count"]):
            fail(
                f"authoritative unittest identity count differs for {test_name}: "
                f"expected={suite['expected_count']} observed={len(names)}"
            )
        if subtest_name_set_sha256(names) != suite["subtest_names_sha256"]:
            fail(f"authoritative unittest identity set differs for {test_name}")
        for subtest_name in names:
            identity = (test_name, subtest_name)
            row = subtest_rows[identity]
            if identity in expected_diagnostic:
                allowed_pairs = {("diagnostic", "SKIP")}
            else:
                allowed_pairs = {("required", "PASS")}
                if mode == "portable-complete" and identity in portable_required_skips:
                    allowed_pairs.add(("required", "SKIP"))
            if (row["requirement"], row["status"]) not in allowed_pairs:
                fail(f"unittest result differs from its mode contract: {identity!r}")
    return counts, subtest_counts


def parse_results(
    results_path: Path,
    subtests_path: Path,
    summary_path: Path,
    policy: dict[str, Any],
) -> tuple[
    dict[str, int],
    list[str],
    str,
    dict[str, dict[str, str]],
    dict[str, int],
    dict[tuple[str, str], dict[str, str]],
]:
    counts, rows = read_test_result_rows(results_path)
    contract = require_object(
        policy["authoritative_test_contract"], "authoritative test contract"
    )
    validate_authoritative_topology(contract, rows)
    required_passes: list[str] = []
    for name in policy["required_passing_test_names"]:
        if rows.get(name, {}).get("status") != "PASS":
            fail(f"required test is not PASS: {name}")
        required_passes.append(name)
    for prefix in policy["required_passing_test_prefixes"]:
        matches = [
            name
            for name, row in rows.items()
            if name.startswith(prefix) and row["status"] == "PASS"
        ]
        if len(matches) != 1:
            fail(f"required test prefix does not select exactly one PASS: {prefix}")
        required_passes.append(matches[0])
    subtest_totals, subtest_rows = parse_subtests(subtests_path, rows)
    validate_authoritative_subtests(contract, subtest_rows)
    summary_payload, _summary_identity = read_regular(summary_path, "test summary")
    if not summary_payload.endswith(b"\n"):
        fail("test summary must end with newline")
    summary: dict[str, str] = {}
    for line in summary_payload.decode("utf-8").splitlines():
        if "=" not in line:
            fail("test summary contains a non-assignment row")
        key, value = line.split("=", 1)
        if not key or key in summary:
            fail("test summary contains an empty or duplicate key")
        summary[key] = value
    required_summary = {
        "authoritative",
        "diagnostic_internal_skip",
        "diagnostic_subtest_fail",
        "diagnostic_subtest_pass",
        "exit_status",
        "external_authority_index_preserved",
        "fail",
        "mandatory_internal_skip",
        "mode",
        "pass",
        "required_subtest_fail",
        "required_subtest_pass",
        "required_subtest_skip",
        "results",
        "skip",
        "subtest_total",
        "subtests",
        "total",
    }
    if set(summary) != required_summary:
        fail("test summary keys differ from the driver contract")
    expected_counts = {
        "pass": counts["PASS"],
        "fail": counts["FAIL"],
        "skip": counts["SKIP"],
        "total": sum(counts.values()),
    }
    for count_key, count_value in expected_counts.items():
        if summary[count_key] != str(count_value):
            fail(f"test summary {count_key} differs from results.tsv")
    if summary["exit_status"] != "0" or counts["FAIL"] != 0 or counts["SKIP"] != 0:
        fail("authoritative Phase 2 test run must have zero failures and zero skips")
    if summary["authoritative"] not in {"0", "1"}:
        fail("test summary authoritative must be exactly 0 or 1")
    if policy["required_authoritative"] and summary["authoritative"] != "1":
        fail("Phase 2 evidence requires an authoritative test-driver run")
    expected_subtest_summary = {
        "required_subtest_pass": subtest_totals["required_pass"],
        "required_subtest_fail": subtest_totals["required_fail"],
        "required_subtest_skip": subtest_totals["required_skip"],
        "mandatory_internal_skip": subtest_totals["mandatory_internal_skip"],
        "diagnostic_subtest_pass": subtest_totals["diagnostic_pass"],
        "diagnostic_subtest_fail": subtest_totals["diagnostic_fail"],
        "diagnostic_internal_skip": subtest_totals["diagnostic_skip"],
        "subtest_total": subtest_totals["total"],
    }
    for count_key, count_value in expected_subtest_summary.items():
        if summary[count_key] != str(count_value):
            fail(f"test summary {count_key} differs from subtests.tsv")
    if summary["mandatory_internal_skip"] != "0":
        fail("authoritative Phase 2 test run must have mandatory_internal_skip=0")
    if summary["mode"] != policy["required_test_mode"]:
        fail("test summary mode differs from the evidence policy")
    if Path(summary["results"]) != results_path:
        fail("test summary results path differs from the indexed results path")
    if Path(summary["subtests"]) != subtests_path:
        fail("test summary subtests path differs from the indexed subtests path")
    return (
        expected_counts,
        sorted(required_passes),
        summary["mode"],
        rows,
        subtest_totals,
        subtest_rows,
    )


def parse_named_paths(
    specifications: Iterable[str], label: str
) -> dict[str, Path]:
    mapped: dict[str, Path] = {}
    for specification in specifications:
        if "=" not in specification:
            fail(f"{label} must be NAME=/absolute/path")
        name, raw_path = specification.split("=", 1)
        require_string(name, f"{label} name", NAME_RE)
        if name in mapped:
            fail(f"duplicate {label}: {name}")
        mapped[name] = absolute_path(raw_path, f"{label} {name}")
    return mapped


def selected_component_test_names(
    specification: dict[str, object], rows: dict[str, dict[str, str]]
) -> list[str]:
    selected: list[str] = []
    for name in require_list(
        specification["required_test_names"], "component required test names"
    ):
        row = rows.get(str(name))
        if row is None or row["status"] != "PASS":
            fail(f"component {specification['name']} requires PASS test {name}")
        selected.append(str(name))
    for prefix in require_list(
        specification["required_test_prefixes"],
        "component required test prefixes",
    ):
        matches = sorted(
            name
            for name, row in rows.items()
            if name.startswith(str(prefix)) and row["status"] == "PASS"
        )
        if len(matches) != 1:
            fail(
                f"component {specification['name']} test prefix {prefix!r} "
                "does not select exactly one PASS row"
            )
        selected.extend(matches)
    if len(selected) != len(set(selected)):
        fail(f"component {specification['name']} selects a test row more than once")
    return sorted(selected)


def coordinator_validate_receipt(
    repository: dict[str, object], receipt: dict[str, object]
) -> None:
    coordinator_path = (
        Path(str(repository["root"]))
        / "scripts/optimization/pgo/production-profile-lock-transaction.py"
    )
    payload, _identity = read_regular(
        coordinator_path, "production profile-lock coordinator"
    )
    module_name = f"_gentoo_opt_receipt_{sha256(payload)}"
    specification = importlib.util.spec_from_file_location(
        module_name, coordinator_path
    )
    if specification is None or specification.loader is None:
        fail("cannot load the exact production profile-lock coordinator")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        specification.loader.exec_module(module)
        validator = getattr(module, "validate_receipt_document", None)
        error_type = getattr(module, "TransactionError", None)
        if not callable(validator) or not isinstance(error_type, type):
            fail("production profile-lock coordinator lacks its receipt validator")
        try:
            validated = validator(receipt, None)
        except BaseException as error:
            if isinstance(error, error_type):
                fail(f"production coordinator rejects its receipt: {error}")
            raise
        if validated != receipt:
            fail("production coordinator receipt validator changed the document")
    except EvidenceError:
        raise
    except BaseException as error:
        fail(f"cannot execute the exact coordinator receipt validator: {error}")
    finally:
        sys.modules.pop(module_name, None)


def validate_live_executable_identity(
    value: Any, label: str, production: bool
) -> tuple[Path, dict[str, object]]:
    identity = require_object(
        value,
        label,
        {"device", "gid", "inode", "mode", "nlink", "path", "sha256", "uid"},
    )
    path = absolute_path(identity["path"], f"{label} path")
    if production:
        validate_root_trust(path, label)
    payload, metadata = read_regular(path, label)
    expected = {
        "device": metadata["device"],
        "gid": metadata["gid"],
        "inode": metadata["inode"],
        "mode": metadata["mode"],
        "nlink": metadata["nlink"],
        "path": os.fspath(path),
        "sha256": sha256(payload),
        "uid": metadata["uid"],
    }
    if identity != expected or metadata["mode"] & 0o111 == 0:
        fail(f"{label} differs from its current executable identity")
    if metadata["mode"] & 0o7000 or metadata["nlink"] != 1:
        fail(f"{label} has privileged mode bits or multiple hardlinks")
    if production and (metadata["uid"] != 0 or metadata["gid"] != 0):
        fail(f"{label} is not owned by root:root")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        fail(f"cannot open {label} for capability inspection: {error}")
    try:
        if stat_identity(os.fstat(descriptor)) != metadata:
            fail(f"{label} changed before capability inspection")
        try:
            os.getxattr(descriptor, "security.capability")
        except OSError as error:
            absent = {
                errno.ENODATA,
                errno.ENOTSUP,
                getattr(errno, "ENOATTR", errno.ENODATA),
                getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
            }
            if error.errno not in absent:
                fail(f"cannot inspect {label} file capabilities: {error}")
        else:
            fail(f"{label} carries a forbidden security.capability xattr")
    finally:
        os.close(descriptor)
    if stat_identity(path.lstat()) != metadata:
        fail(f"{label} changed after capability inspection")
    return path, expected


def require_terminal_transaction_markers_absent(
    receipt_path: Path, authorization_path: Path, journal_path: Path
) -> None:
    markers = (
        receipt_path.with_name(f"{receipt_path.name}.partial"),
        receipt_path.with_name(f"{receipt_path.name}.interrupted-partial"),
        authorization_path.with_name(f"{authorization_path.name}.partial"),
        authorization_path.with_name(
            f"{authorization_path.name}.interrupted-partial"
        ),
        journal_path,
        journal_path.with_name(f"{journal_path.name}.partial"),
        journal_path.with_name(f"{journal_path.name}.child.json"),
        journal_path.with_name(f"{journal_path.name}.child.json.partial"),
    )
    for marker in markers:
        if marker.exists() or marker.is_symlink():
            fail(f"production transaction retains a terminal marker: {marker}")


def durable_file_identity(path: Path) -> dict[str, int]:
    metadata = path.lstat()
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "nlink": metadata.st_nlink,
        "size": metadata.st_size,
    }


def require_pretty_json_object(
    payload: bytes, label: str, keys: set[str] | None = None
) -> dict[str, Any]:
    value = require_object(parse_json_bytes(payload, label), label, keys)
    if pretty_json(value) != payload:
        fail(f"{label} is not canonical pretty JSON")
    return value


def jq_pretty_json(value: object) -> bytes:
    """Render the insertion-ordered JSON format emitted by `jq -n`."""

    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def require_ordered_object(
    value: Any, label: str, keys: Sequence[str]
) -> dict[str, Any]:
    result = require_object(value, label, set(keys))
    if tuple(result) != tuple(keys):
        fail(f"{label} key order differs from the producing jq program")
    return result


def require_jq_json_object(
    payload: bytes, label: str, keys: Sequence[str]
) -> dict[str, Any]:
    value = require_ordered_object(parse_json_bytes(payload, label), label, keys)
    if jq_pretty_json(value) != payload:
        fail(f"{label} is not exact jq pretty JSON")
    return value


def require_compact_sorted_json_object(
    payload: bytes, label: str, keys: set[str] | None = None
) -> dict[str, Any]:
    value = require_object(parse_json_bytes(payload, label), label, keys)
    expected = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    if payload != expected:
        fail(f"{label} is not exact compact sorted JSON")
    return value


def read_sha256_reference(
    value: object,
    label: str,
    *,
    production: bool,
    expected_path: Path | None = None,
) -> tuple[Path, bytes]:
    reference = require_object(value, label)
    if not {"path", "sha256"}.issubset(reference):
        fail(f"{label} does not contain a path and SHA-256 reference")
    path = absolute_path(reference["path"], f"{label} path")
    if expected_path is not None and path != expected_path:
        fail(f"{label} path differs from its reviewed location")
    digest = require_string(reference["sha256"], f"{label} digest", SHA256_RE)
    if production:
        validate_root_trust(path, label)
    payload, _metadata = read_regular(path, label)
    if sha256(payload) != digest:
        fail(f"{label} payload differs from its recorded digest")
    return path, payload


def git_blob_at(
    repository: Path, commit: str, relative: str
) -> tuple[str, str, bytes]:
    raw = git_command(
        Path("/usr/bin/git"),
        repository,
        ["ls-tree", "-z", commit, "--", relative],
    )
    rows = [row for row in raw.split(b"\0") if row]
    if len(rows) != 1:
        fail(f"Git commit {commit} does not bind exactly one {relative}")
    try:
        header, raw_path = rows[0].split(b"\t", 1)
        raw_mode, raw_type, raw_oid = header.split(b" ", 2)
        mode = raw_mode.decode("ascii")
        object_type = raw_type.decode("ascii")
        oid = raw_oid.decode("ascii")
        observed_path = raw_path.decode("utf-8", "strict")
    except (ValueError, UnicodeDecodeError) as error:
        fail(f"cannot parse Git identity for {relative}: {error}")
    if (
        object_type != "blob"
        or observed_path != relative
        or OID_RE.fullmatch(oid) is None
    ):
        fail(f"Git identity for {relative} is invalid")
    payload = git_command(Path("/usr/bin/git"), repository, ["cat-file", "blob", oid])
    return mode, oid, payload


def validate_bootstrap_identity(
    raw: object,
    *,
    path: Path,
    label: str,
    production: bool,
) -> dict[str, Any]:
    value = require_object(
        raw,
        label,
        {"path", "device", "inode", "uid", "gid", "mode", "nlink", "size", "sha256"},
    )
    if value.get("path") != os.fspath(path):
        fail(f"{label} path differs")
    if production:
        validate_root_trust(path, label)
    payload, _metadata = read_regular(path, label)
    expected = {**durable_file_identity(path), "path": os.fspath(path), "sha256": sha256(payload)}
    if value != expected:
        fail(f"{label} identity changed")
    return value


def validate_jsonschema_bootstrap_manifest(
    payload: bytes,
    path: Path,
    repository: dict[str, object],
    production: bool,
) -> dict[str, Any]:
    manifest = require_pretty_json_object(
        payload,
        "jsonschema bootstrap manifest",
        {
            "schema",
            "commit",
            "tree",
            "repository_root",
            "repository_root_identity",
            "repository_git_config",
            "python",
            "destination",
            "files",
        },
    )
    candidate_repository = absolute_path(repository["root"], "Candidate-B repository root")
    candidate_commit = require_string(repository["commit"], "Candidate-B commit", OID_RE)
    bootstrap_commit = require_string(manifest["commit"], "bootstrap commit", OID_RE)
    bootstrap_tree = require_string(manifest["tree"], "bootstrap tree", OID_RE)
    destination = absolute_path(manifest["destination"], "bootstrap destination")
    source_root = absolute_path(manifest["repository_root"], "bootstrap source root")
    if (
        manifest["schema"] != "gentoo-optimization-jsonschema-prerequisite-bootstrap-v1"
        or path != destination / "bootstrap-manifest.json"
    ):
        fail("jsonschema bootstrap manifest does not name its exact published directory")
    if production and (
        destination
        != Path(
            f"/var/lib/gentoo-optimization/bootstrap/jsonschema-prerequisite-{bootstrap_commit}"
        )
        or path
        != Path(
            f"/var/lib/gentoo-optimization/bootstrap/jsonschema-prerequisite-"
            f"{bootstrap_commit}/bootstrap-manifest.json"
        )
    ):
        fail("jsonschema bootstrap is outside its canonical production root")
    ancestor = git_command_result(
        Path("/usr/bin/git"),
        candidate_repository,
        ["merge-base", "--is-ancestor", bootstrap_commit, candidate_commit],
        allowed_returncodes=frozenset({0, 1}),
    )
    if ancestor.returncode != 0:
        fail("jsonschema bootstrap source commit is not an ancestor of Candidate B")
    observed_tree = git_command(
        Path("/usr/bin/git"),
        candidate_repository,
        ["show", "-s", "--format=%T", bootstrap_commit],
    ).decode("ascii").strip()
    if observed_tree != bootstrap_tree:
        fail("jsonschema bootstrap tree differs from its source commit")
    source_root_identity = require_object(
        manifest["repository_root_identity"],
        "bootstrap repository-root identity",
        {"device", "inode", "uid", "gid", "mode"},
    )
    source_root_metadata = source_root.lstat()
    if source_root_identity != {
        "device": source_root_metadata.st_dev,
        "inode": source_root_metadata.st_ino,
        "uid": source_root_metadata.st_uid,
        "gid": source_root_metadata.st_gid,
        "mode": stat.S_IMODE(source_root_metadata.st_mode),
    }:
        fail("jsonschema bootstrap source-root identity changed")
    if production:
        validate_root_trust(source_root, "bootstrap source root", directory=True)
    validate_bootstrap_identity(
        manifest["repository_git_config"],
        path=source_root / ".git/config",
        label="bootstrap repository Git config",
        production=production,
    )
    expected_relatives = {
        "install-jsonschema-prerequisite.py",
        "publish-jsonschema-prerequisite-bootstrap.py",
        "verify-binpkg-snapshot.py",
    }
    rows = require_list(manifest["files"], "bootstrap files", nonempty=True)
    observed_relatives: set[str] = set()
    for raw_row in rows:
        row = require_object(raw_row, "bootstrap file", {"relative", "git", "source", "published"})
        relative_name = require_string(row["relative"], "bootstrap relative name")
        if relative_name not in expected_relatives or relative_name in observed_relatives:
            fail("jsonschema bootstrap file set is repeated or foreign")
        observed_relatives.add(relative_name)
        relative = f"scripts/optimization/recovery/{relative_name}"
        git_record = require_object(
            row["git"],
            f"bootstrap Git row {relative_name}",
            {"path", "mode", "blob_oid", "blob_size", "blob_sha256"},
        )
        source_mode, source_oid, source_payload = git_blob_at(
            candidate_repository, bootstrap_commit, relative
        )
        candidate_mode, _candidate_oid, candidate_payload = git_blob_at(
            candidate_repository, candidate_commit, relative
        )
        if (
            git_record.get("path") != relative
            or git_record.get("mode") != "100755"
            or source_mode != "100755"
            or candidate_mode != "100755"
            or git_record.get("blob_oid") != source_oid
            or git_record.get("blob_size") != len(source_payload)
            or git_record.get("blob_sha256") != sha256(source_payload)
            or candidate_payload != source_payload
        ):
            fail(f"bootstrap payload {relative_name} differs between Candidate A and B")
        source_identity = validate_bootstrap_identity(
            row["source"],
            path=source_root / relative,
            label=f"bootstrap source {relative_name}",
            production=production,
        )
        published_identity = validate_bootstrap_identity(
            row["published"],
            path=destination / relative_name,
            label=f"bootstrap published {relative_name}",
            production=production,
        )
        if (
            source_identity.get("sha256") != sha256(source_payload)
            or published_identity.get("sha256") != sha256(source_payload)
            or source_identity.get("mode") != 0o755
            or published_identity.get("mode") != 0o755
        ):
            fail(f"bootstrap payload {relative_name} has a foreign source or mode")
    if observed_relatives != expected_relatives:
        fail("jsonschema bootstrap manifest omits a payload")
    python_path = absolute_path(
        require_object(manifest["python"], "bootstrap Python identity").get("path"),
        "bootstrap Python path",
    )
    validate_bootstrap_identity(
        manifest["python"],
        path=python_path,
        label="bootstrap Python interpreter",
        production=production,
    )
    if production and python_path != Path("/usr/bin/python3.15"):
        fail("jsonschema bootstrap Python is not the reviewed production entry point")
    observed_entries = {entry.name for entry in destination.iterdir()}
    if observed_entries != {*expected_relatives, "bootstrap-manifest.json"}:
        fail("jsonschema bootstrap directory contains a foreign or missing object")
    destination_metadata = destination.lstat()
    if (
        not stat.S_ISDIR(destination_metadata.st_mode)
        or stat.S_IMODE(destination_metadata.st_mode) != 0o700
        or (production and (destination_metadata.st_uid != 0 or destination_metadata.st_gid != 0))
    ):
        fail("jsonschema bootstrap directory does not have exact trusted metadata")
    return {
        "commit": bootstrap_commit,
        "destination": destination,
        "python": python_path,
        "repository": candidate_repository,
        "candidate_commit": candidate_commit,
    }


def path_is_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def require_path_absent(path: Path, label: str) -> None:
    if path_is_present(path):
        fail(f"{label} remains visible: {path}")


def confined_to(path: Path, roots: Sequence[Path], label: str) -> None:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return
    fail(f"{label} is outside its reviewed roots: {path}")


def validate_sha256_manifest(
    path: Path,
    label: str,
    *,
    roots: Sequence[Path],
    production: bool,
    recursive: bool = False,
    visited: set[Path] | None = None,
) -> list[Path]:
    """Rehash every strict absolute sha256sum row in a durable manifest."""

    if visited is None:
        visited = set()
    if path in visited:
        fail(f"{label} contains a recursive manifest cycle: {path}")
    visited.add(path)
    if production:
        validate_root_trust(path, label)
    payload, _metadata = read_regular(path, label)
    try:
        lines = payload.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as error:
        fail(f"{label} is not UTF-8: {error}")
    if not lines or any(not line.endswith("\n") for line in lines):
        fail(f"{label} must be a nonempty newline-terminated sha256sum manifest")
    observed: list[Path] = []
    for index, line in enumerate(lines, 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (/[^\n\r\t\0]+)\n", line)
        if match is None:
            fail(f"{label} row {index} is not a strict absolute sha256sum row")
        digest, raw_path = match.groups()
        referenced = absolute_path(raw_path, f"{label} row {index} path")
        confined_to(referenced, roots, f"{label} row {index}")
        if referenced in observed:
            fail(f"{label} repeats a referenced path: {referenced}")
        if production:
            validate_root_trust(referenced, f"{label} referenced payload")
        referenced_payload, _referenced_stat = read_regular(
            referenced, f"{label} referenced payload"
        )
        if sha256(referenced_payload) != digest:
            fail(f"{label} referenced payload changed: {referenced}")
        observed.append(referenced)
    if recursive:
        for referenced in observed:
            if (
                referenced.name.endswith("-manifest.sha256")
                or referenced.name.endswith("-packages.sha256")
                or referenced.name
                in {"evidence-manifest.sha256", "attempt-ledger.sha256"}
            ):
                validate_sha256_manifest(
                    referenced,
                    f"{label} nested manifest",
                    roots=roots,
                    production=production,
                    recursive=True,
                    visited=visited,
                )
    return observed


def gnu_stat_fields(path: Path) -> str:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        object_type = "symbolic link"
    elif stat.S_ISDIR(metadata.st_mode):
        object_type = "directory"
    elif stat.S_ISREG(metadata.st_mode):
        object_type = "regular file"
    else:
        fail(f"selector identity names an unsupported object: {path}")
    return ":".join(
        (
            str(metadata.st_dev),
            str(metadata.st_ino),
            str(metadata.st_uid),
            str(metadata.st_gid),
            format(stat.S_IMODE(metadata.st_mode), "o"),
            str(metadata.st_nlink),
            object_type,
        )
    )


def checkpoint_selector_identity(path: Path, label: str) -> str:
    if not path.is_symlink():
        fail(f"{label} is not a symbolic link")
    target_text = os.readlink(path)
    target = absolute_path(target_text, f"{label} target")
    resolved = path.resolve(strict=True)
    if resolved != target or not resolved.is_dir() or resolved.is_symlink():
        fail(f"{label} does not resolve exactly to its absolute target")
    packages = resolved / "Packages"
    packages_payload, _packages_stat = read_regular(packages, f"{label} Packages")
    return "|".join(
        (
            gnu_stat_fields(path),
            target_text,
            os.fspath(resolved),
            gnu_stat_fields(resolved),
            sha256(packages_payload),
        )
    )


def validate_local_evidence_reference(
    value: object,
    label: str,
    *,
    root: Path,
    production: bool,
) -> Path:
    reference = require_ordered_object(value, label, ("path", "sha256"))
    path = absolute_path(reference["path"], f"{label} path")
    if path.parent != root:
        fail(f"{label} is not a direct child of its evidence root")
    if production:
        validate_root_trust(path, label)
    payload, _metadata = read_regular(path, label)
    if reference["sha256"] != sha256(payload):
        fail(f"{label} changed from its recorded digest")
    return path


def snapshot_tree_identity(
    root: Path, label: str, *, production: bool
) -> list[dict[str, object]]:
    if not root.is_dir() or root.is_symlink():
        fail(f"{label} is not a real directory")
    if production:
        validate_root_trust(root, label, directory=True)
    rows: list[dict[str, object]] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names.sort()
        file_names.sort()
        parent = Path(directory)
        for name in [*directory_names, *file_names]:
            child = parent / name
            relative = child.relative_to(root).as_posix()
            metadata = child.lstat()
            row: dict[str, object] = {
                "path": relative,
                "mode": stat.S_IMODE(metadata.st_mode),
            }
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                if production:
                    validate_root_trust(child, label, directory=True)
                row["type"] = "directory"
            elif stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                if production:
                    validate_root_trust(child, label)
                payload, _child_stat = read_regular(child, label)
                row.update(type="file", size=len(payload), sha256=sha256(payload))
            else:
                fail(f"{label} contains a symlink or special object: {child}")
            rows.append(row)
    return rows


def validate_operator_evidence_manifest(
    payload: bytes,
    path: Path,
    checkpoint_id: str,
    production: bool,
) -> Path:
    manifest = require_pretty_json_object(
        payload,
        "checkpoint operator-evidence manifest",
        {"schema_version", "rows"},
    )
    root = path.parent
    if (
        path.name != "operator-evidence.manifest.json"
        or root.name != f"checkpoint-{checkpoint_id}-operator-evidence"
        or manifest.get("schema_version") != 1
    ):
        fail("checkpoint operator-evidence manifest path or schema is invalid")
    if production:
        validate_root_trust(root, "checkpoint operator-evidence root", directory=True)
    rows: list[dict[str, object]] = []

    def visit(directory: Path) -> None:
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            child = Path(entry.path)
            relative = child.relative_to(root).as_posix()
            if relative == path.name:
                continue
            metadata = child.lstat()
            row: dict[str, object] = {
                "path": relative,
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
                "mode": stat.S_IMODE(metadata.st_mode),
                "nlink": metadata.st_nlink,
            }
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                if production:
                    validate_root_trust(child, "operator-evidence directory", directory=True)
                row["type"] = "directory"
                rows.append(row)
                visit(child)
            elif stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                if production:
                    validate_root_trust(child, "operator-evidence file")
                child_payload, _child_stat = read_regular(child, "operator-evidence file")
                row.update(type="file", size=len(child_payload), sha256=sha256(child_payload))
                rows.append(row)
            elif stat.S_ISLNK(metadata.st_mode):
                if production:
                    validate_root_trust(child.parent, "operator-evidence symlink parent", directory=True)
                    if metadata.st_uid != 0:
                        fail("operator-evidence symlink is not root-owned")
                row.update(type="symlink", target=os.readlink(child))
                rows.append(row)
            else:
                fail(f"operator-evidence tree contains a special object: {child}")

    visit(root)
    if manifest["rows"] != rows:
        fail("checkpoint operator-evidence tree differs from its complete manifest")
    return root


def parse_packages_records(payload: bytes, label: str) -> list[dict[str, str | int]]:
    """Parse the exact Packages stanza authority needed by checkpoint evidence."""

    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        fail(f"{label} is not UTF-8: {error}")
    stanzas: list[tuple[int, dict[str, str]]] = []
    current: list[tuple[int, str]] = []
    stanza_number = 0
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.removesuffix("\r")
        if not line.strip():
            if current:
                stanza_number += 1
                fields: dict[str, str] = {}
                for field_line, field_text in current:
                    if ":" not in field_text:
                        fail(f"{label} line {field_line} has no key/value separator")
                    key, value = field_text.split(":", 1)
                    if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None or key in fields:
                        fail(f"{label} line {field_line} has a repeated or unsafe field")
                    fields[key] = value.lstrip(" \t")
                stanzas.append((stanza_number, fields))
                current = []
            continue
        current.append((line_number, line))
    if current:
        stanza_number += 1
        fields = {}
        for field_line, field_text in current:
            if ":" not in field_text:
                fail(f"{label} line {field_line} has no key/value separator")
            key, value = field_text.split(":", 1)
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None or key in fields:
                fail(f"{label} line {field_line} has a repeated or unsafe field")
            fields[key] = value.lstrip(" \t")
        stanzas.append((stanza_number, fields))
    if not stanzas or "CPV" in stanzas[0][1]:
        fail(f"{label} lacks its global header")
    header = stanzas[0][1]
    records = stanzas[1:]
    declared = header.get("PACKAGES")
    if (
        header.get("VERSION") != "0"
        or declared is None
        or not declared.isdecimal()
        or int(declared) != len(records)
    ):
        fail(f"{label} header count or version is invalid")
    result: list[dict[str, str | int]] = []
    seen_cpvs: set[str] = set()
    seen_paths: set[str] = set()
    for number, fields in records:
        if not {"CPV", "PATH", "SIZE", "MD5", "SHA1"}.issubset(fields):
            fail(f"{label} record {number} lacks a required archive field")
        cpv = fields["CPV"]
        relative = fields["PATH"]
        if (
            not cpv
            or cpv in seen_cpvs
            or relative in seen_paths
            or relative_path(relative, f"{label} record path") != relative
            or not fields["SIZE"].isdecimal()
            or re.fullmatch(r"[0-9A-Fa-f]{32}", fields["MD5"]) is None
            or re.fullmatch(r"[0-9A-Fa-f]{40}", fields["SHA1"]) is None
        ):
            fail(f"{label} record {number} has a repeated or unsafe identity")
        seen_cpvs.add(cpv)
        seen_paths.add(relative)
        result.append(
            {
                "record": number,
                "cpv": cpv,
                "path": relative,
                "size": fields["SIZE"],
                "md5": fields["MD5"].lower(),
                "sha1": fields["SHA1"].lower(),
            }
        )
    return result


def validate_checkpoint_snapshot(
    *,
    snapshot: Path,
    report: Path,
    prefix: str,
    checkpoint_id: str,
    live_cpvs: int,
    production: bool,
) -> dict[str, object]:
    verification_path = report / f"{prefix}-verification.json"
    packages_manifest_path = report / f"{prefix}-packages.sha256"
    archives_manifest_path = report / f"{prefix}-archives.tsv"
    if production:
        for item, item_label in (
            (verification_path, f"{prefix} verifier report"),
            (packages_manifest_path, f"{prefix} Packages manifest"),
            (archives_manifest_path, f"{prefix} archive manifest"),
        ):
            validate_root_trust(item, item_label)
    verification_payload, _verification_stat = read_regular(
        verification_path, f"{prefix} verifier report"
    )
    verification = require_pretty_json_object(
        verification_payload,
        f"{prefix} verifier report",
        {"archives", "counts", "coverage", "inputs", "issues", "schema_version", "status"},
    )
    counts = require_object(
        verification.get("counts"),
        f"{prefix} verifier counts",
        {
            "errors",
            "extra_indexed_archives",
            "gpkg_archives_found",
            "gpkg_archives_indexed",
            "gpkg_archives_validated",
            "image_tar_zst_streams_tested",
            "indexed_records",
            "indexed_unique_cpvs",
            "indexed_unique_paths",
            "live_cpvs",
            "missing_live_cpvs",
            "unindexed_gpkg_archives",
        },
    )
    coverage = require_object(
        verification.get("coverage"),
        f"{prefix} verifier coverage",
        {
            "duplicate_live_cpvs",
            "extra_indexed_archives",
            "missing_live_cpvs",
            "unindexed_gpkg_archives",
        },
    )
    inputs = require_object(
        verification.get("inputs"),
        f"{prefix} verifier inputs",
        {
            "allow_extra_archives",
            "packages_index",
            "snapshot",
            "validate_gpkg",
            "vdb",
            "zstd",
        },
    )
    archives = require_list(
        verification.get("archives"), f"{prefix} verifier archives", nonempty=True
    )
    if (
        verification.get("schema_version") != 1
        or verification.get("status") != "pass"
        or verification.get("issues") != []
        or inputs.get("snapshot") != os.fspath(snapshot)
        or inputs.get("packages_index") != os.fspath(snapshot / "Packages")
        or inputs.get("validate_gpkg") is not True
        or inputs.get("allow_extra_archives") is not False
        or any(
            counts.get(key) != live_cpvs
            for key in (
                "live_cpvs",
                "indexed_records",
                "indexed_unique_cpvs",
                "indexed_unique_paths",
                "gpkg_archives_found",
                "gpkg_archives_indexed",
                "gpkg_archives_validated",
                "image_tar_zst_streams_tested",
            )
        )
        or any(
            counts.get(key) != 0
            for key in (
                "errors",
                "missing_live_cpvs",
                "extra_indexed_archives",
                "unindexed_gpkg_archives",
            )
        )
        or coverage.get("missing_live_cpvs") != []
        or coverage.get("extra_indexed_archives") != []
        or coverage.get("unindexed_gpkg_archives") != []
        or coverage.get("duplicate_live_cpvs") != {}
        or len(archives) != live_cpvs
    ):
        fail(f"{prefix} verifier report is not an exact full GPKG pass")
    if production and (
        inputs.get("vdb") != "/var/db/pkg" or inputs.get("zstd") != "/usr/bin/zstd"
    ):
        fail(f"{prefix} verifier inputs differ from the reviewed production tools")
    packages_payload, _packages_stat = read_regular(
        snapshot / "Packages", f"{prefix} Packages"
    )
    package_records = parse_packages_records(packages_payload, f"{prefix} Packages")
    package_records_by_cpv = {str(record["cpv"]): record for record in package_records}
    if len(package_records) != live_cpvs:
        fail(f"{prefix} Packages membership differs from the live CPV count")
    packages_manifest_payload, _packages_manifest_stat = read_regular(
        packages_manifest_path, f"{prefix} Packages manifest"
    )
    if packages_manifest_payload != (
        f"{sha256(packages_payload)}  {snapshot / 'Packages'}\n"
    ).encode("utf-8"):
        fail(f"{prefix} Packages manifest differs from the current generation")
    archive_rows: list[tuple[str, str, int, str]] = []
    seen_cpvs: set[str] = set()
    seen_relatives: set[str] = set()
    for index, raw_archive in enumerate(archives, 1):
        archive = require_object(
            raw_archive,
            f"{prefix} archive {index}",
            {"cpv", "exists", "gpkg", "md5", "path", "record", "regular", "sha1", "size"},
        )
        cpv = require_string(archive.get("cpv"), f"{prefix} archive CPV")
        relative = relative_path(archive.get("path"), f"{prefix} archive path")
        package_record = package_records_by_cpv.get(cpv)
        if (
            not relative.endswith(".gpkg.tar")
            or cpv in seen_cpvs
            or relative in seen_relatives
            or archive.get("exists") is not True
            or archive.get("regular") is not True
            or package_record is None
            or package_record.get("path") != relative
            or package_record.get("record") != archive.get("record")
        ):
            fail(f"{prefix} verifier archive set is repeated or not exact")
        seen_cpvs.add(cpv)
        seen_relatives.add(relative)
        archive_path = snapshot / relative
        if production:
            validate_root_trust(archive_path, f"{prefix} archive")
        archive_payload, _archive_stat = read_regular(archive_path, f"{prefix} archive")
        size = require_object(
            archive.get("size"), f"{prefix} archive size", {"actual", "expected"}
        )
        md5 = require_object(
            archive.get("md5"), f"{prefix} archive MD5", {"actual", "expected"}
        )
        sha1 = require_object(
            archive.get("sha1"), f"{prefix} archive SHA-1", {"actual", "expected"}
        )
        gpkg = require_object(
            archive.get("gpkg"),
            f"{prefix} archive GPKG result",
            {
                "image_tar_zst_streams",
                "manifest_members_verified",
                "status",
                "zstd_streams_tested",
            },
        )
        actual_md5 = hashlib.md5(archive_payload).hexdigest()
        actual_sha1 = hashlib.sha1(archive_payload).hexdigest()
        if (
            size.get("actual") != len(archive_payload)
            or size.get("expected") != str(len(archive_payload))
            or package_record.get("size") != str(len(archive_payload))
            or md5.get("actual") != actual_md5
            or md5.get("expected") != actual_md5
            or package_record.get("md5") != actual_md5
            or sha1.get("actual") != actual_sha1
            or sha1.get("expected") != actual_sha1
            or package_record.get("sha1") != actual_sha1
            or gpkg.get("status") != "verified"
            or gpkg.get("zstd_streams_tested") != 1
            or gpkg.get("image_tar_zst_streams") != 1
            or require_int(
                gpkg.get("manifest_members_verified"),
                f"{prefix} archive verified manifest members",
                minimum=1,
            )
            < 1
            or require_int(archive.get("record"), f"{prefix} archive record", minimum=1)
            < 1
        ):
            fail(f"{prefix} archive differs from the exact verifier result")
        archive_rows.append((cpv, relative, len(archive_payload), sha256(archive_payload)))
    if archive_rows != sorted(archive_rows):
        fail(f"{prefix} verifier archive records are not canonical")
    archive_manifest_payload, _archive_manifest_stat = read_regular(
        archives_manifest_path, f"{prefix} archive manifest"
    )
    expected_archive_manifest = "cpv\trelative_path\tsize\tsha256\n" + "".join(
        f"{cpv}\t{relative}\t{size}\t{digest}\n"
        for cpv, relative, size, digest in archive_rows
    )
    if archive_manifest_payload != expected_archive_manifest.encode("utf-8"):
        fail(f"{prefix} archive manifest differs from the current generation")
    root_metadata = snapshot.lstat()
    if (
        stat.S_IMODE(root_metadata.st_mode) != 0o700
        or (production and (root_metadata.st_uid != 0 or root_metadata.st_gid != 0))
    ):
        fail(f"{prefix} generation root metadata is not exact")
    tree = snapshot_tree_identity(snapshot, f"{prefix} generation", production=production)
    present_gpkg = sorted(
        str(row["path"])
        for row in tree
        if row.get("type") == "file" and str(row["path"]).endswith(".gpkg.tar")
    )
    if present_gpkg != sorted(seen_relatives):
        fail(f"{prefix} generation contains an unindexed or missing GPKG archive")
    return {
        "tree": tree,
        "root_mode": stat.S_IMODE(root_metadata.st_mode),
        "packages_sha256": sha256(packages_payload),
        "cpvs": sorted(seen_cpvs),
        "verification_path": verification_path,
        "packages_manifest_path": packages_manifest_path,
        "archives_manifest_path": archives_manifest_path,
    }


def validate_checkpoint_tool_identities(
    report: Path, bootstrap: dict[str, Any], *, production: bool
) -> tuple[Path, Path, dict[str, str]]:
    manifest_path = report / "tool-identities.tsv"
    if production:
        validate_root_trust(manifest_path, "checkpoint tool identities")
    payload, _metadata = read_regular(manifest_path, "checkpoint tool identities")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        fail(f"checkpoint tool identities are not UTF-8: {error}")
    if not lines or lines[0] != (
        "logical_path\tresolved_path\tlogical_stat\tsha256\tsymlink_chain"
    ):
        fail("checkpoint tool identities have a foreign header")
    rows: dict[str, tuple[str, str, str, str]] = {}
    serialized_rows: dict[str, str] = {}
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 5:
            fail("checkpoint tool identities contain a malformed row")
        logical, resolved, stat_fields_value, digest, chain = fields
        absolute_path(logical, "checkpoint logical tool")
        absolute_path(resolved, "checkpoint resolved tool")
        require_string(digest, "checkpoint tool digest", SHA256_RE)
        if logical in rows:
            fail("checkpoint tool identities repeat a logical path")
        rows[logical] = (resolved, stat_fields_value, digest, chain)
        serialized_rows[logical] = line
    checkpoint_root = bootstrap["destination"].with_name(
        f"binpkg-checkpoint-{bootstrap['commit']}"
    )
    expected = (
        (
            checkpoint_root / "create-binpkg-checkpoint.sh",
            "scripts/optimization/recovery/create-binpkg-checkpoint.sh",
        ),
        (
            checkpoint_root / "verify-binpkg-snapshot.py",
            "scripts/optimization/recovery/verify-binpkg-snapshot.py",
        ),
    )
    for path, relative in expected:
        if production:
            validate_root_trust(path, f"checkpoint bootstrap {relative}")
        path_payload, _path_stat = read_regular(path, f"checkpoint bootstrap {relative}")
        row = rows.get(os.fspath(path))
        source_mode, _source_oid, source_payload = git_blob_at(
            bootstrap["repository"], bootstrap["commit"], relative
        )
        candidate_mode, _candidate_oid, candidate_payload = git_blob_at(
            bootstrap["repository"], bootstrap["candidate_commit"], relative
        )
        if (
            row is None
            or row
            != (
                os.fspath(path),
                gnu_stat_fields(path),
                sha256(path_payload),
                "-",
            )
            or source_mode != "100755"
            or candidate_mode != "100755"
            or source_payload != path_payload
            or candidate_payload != path_payload
            or stat.S_IMODE(path.lstat().st_mode) != 0o755
            or (production and (path.lstat().st_uid != 0 or path.lstat().st_gid != 0))
        ):
            fail(f"checkpoint bootstrap {relative} differs from Candidate A or B")
    return expected[0][0], expected[1][0], serialized_rows


def validate_checkpoint_lane(
    lane: str,
    payloads: dict[str, bytes],
    paths: dict[str, Path],
    bootstrap: dict[str, Any],
    production: bool,
) -> dict[str, Any]:
    state_label = f"jsonschema-{lane}-checkpoint-terminal-state"
    receipt_label = f"jsonschema-{lane}-checkpoint-offline-restore-receipt"
    manifest_label = f"jsonschema-{lane}-checkpoint-operator-manifest"
    terminal_path = paths[state_label]
    match = re.fullmatch(
        r"binpkg-checkpoint-([A-Za-z0-9][A-Za-z0-9_.-]{0,127})\.offline-restore-proven\.json",
        terminal_path.name,
    )
    if match is None:
        fail(f"{lane} checkpoint terminal path is not canonical")
    checkpoint_id = match.group(1)
    canonical_path = terminal_path.with_name(f"binpkg-checkpoint-{checkpoint_id}.json")
    expected_state_parent = Path("/var/lib/gentoo-optimization/state/project")
    if production and terminal_path.parent != expected_state_parent:
        fail(f"{lane} checkpoint terminal state is outside the canonical production root")
    if production:
        validate_root_trust(canonical_path, f"{lane} checkpoint canonical state")
    canonical_payload, _canonical_stat = read_regular(
        canonical_path, f"{lane} checkpoint canonical state", allow_hardlinks=True
    )
    terminal_identity = durable_file_identity(terminal_path)
    canonical_identity = durable_file_identity(canonical_path)
    if (
        canonical_payload != payloads[state_label]
        or terminal_identity != canonical_identity
        or terminal_identity["nlink"] != 2
        or terminal_identity["mode"] != 0o600
    ):
        fail(f"{lane} checkpoint canonical and terminal states are not the exact hardlink pair")
    state = require_jq_json_object(
        payloads[state_label],
        f"{lane} checkpoint terminal state",
        (
            "schema_version",
            "control",
            "checkpoint_id",
            "status",
            "recorded_at",
            "live_cpvs",
            "cache_checkpoint",
            "durable_checkpoint",
            "activation",
            "offline_restore",
            "offline_restoration_tested",
            "pending_total",
            "unknown_total",
            "failed_total",
        ),
    )
    if (
        state.get("schema_version") != 2
        or state.get("control") != "exact-live-binpkg-checkpoint"
        or state.get("checkpoint_id") != checkpoint_id
        or state.get("status") != "offline-restore-proven"
        or state.get("offline_restoration_tested") is not True
        or any(state.get(key) != 0 for key in ("pending_total", "unknown_total", "failed_total"))
        or require_int(state.get("live_cpvs"), f"{lane} checkpoint live CPVs", minimum=1) < 1
    ):
        fail(f"{lane} checkpoint is not an exact clean offline-restore terminal state")
    cache = require_ordered_object(
        state["cache_checkpoint"], f"{lane} cache checkpoint", ("path",)
    )
    durable = require_ordered_object(
        state["durable_checkpoint"], f"{lane} durable checkpoint", ("path",)
    )
    cache_path = absolute_path(cache["path"], f"{lane} cache checkpoint path")
    durable_path = absolute_path(durable["path"], f"{lane} durable checkpoint path")
    if (
        cache_path.name != f"snapshot-{checkpoint_id}"
        or durable_path.name != f"critical-{checkpoint_id}"
        or not cache_path.is_dir()
        or not durable_path.is_dir()
    ):
        fail(f"{lane} checkpoint cache or durable generation is not exact")
    if production and (
        cache_path != Path(f"/var/cache/gentoo-optimization/binpkgs/snapshot-{checkpoint_id}")
        or durable_path
        != Path(f"/var/lib/gentoo-optimization/recovery/binpkgs/critical-{checkpoint_id}")
    ):
        fail(f"{lane} checkpoint generations are outside canonical production roots")
    activation = require_ordered_object(
        state["activation"],
        f"{lane} checkpoint activation",
        ("selector", "intent", "intent_sha256", "receipt", "receipt_sha256"),
    )
    selector = absolute_path(activation["selector"], f"{lane} checkpoint selector")
    intent_path = absolute_path(activation["intent"], f"{lane} activation intent")
    activation_receipt_path = absolute_path(
        activation["receipt"], f"{lane} activation receipt"
    )
    if intent_path.parent != activation_receipt_path.parent:
        fail(f"{lane} checkpoint activation records do not share one report")
    if production:
        if selector != Path("/var/cache/gentoo-optimization/binpkgs/critical-current"):
            fail(f"{lane} checkpoint selector is outside its canonical production path")
        validate_root_trust(selector.parent, f"{lane} selector parent", directory=True)
        selector_metadata = selector.lstat()
        if (
            not stat.S_ISLNK(selector_metadata.st_mode)
            or selector_metadata.st_uid != 0
            or selector_metadata.st_gid != 0
        ):
            fail(f"{lane} checkpoint selector is not an exact root-owned symlink")
    report = intent_path.parent
    if report.name != f"checkpoint-{checkpoint_id}":
        fail(f"{lane} checkpoint report path differs from its checkpoint ID")
    if production and report != Path(
        f"/var/lib/gentoo-optimization/reports/checkpoint-{checkpoint_id}"
    ):
        fail(f"{lane} checkpoint report is outside the canonical production root")
    report_metadata = report.lstat()
    if (
        not stat.S_ISDIR(report_metadata.st_mode)
        or (production and (report_metadata.st_uid, report_metadata.st_gid) != (0, 0))
        or (production and stat.S_IMODE(report_metadata.st_mode) != 0o700)
    ):
        fail(f"{lane} checkpoint report root metadata is not exact")
    _checkpoint_helper, checkpoint_verifier, checkpoint_tools = validate_checkpoint_tool_identities(
        report, bootstrap, production=production
    )
    if production:
        for referenced, label in (
            (intent_path, f"{lane} activation intent"),
            (activation_receipt_path, f"{lane} activation receipt"),
        ):
            validate_root_trust(referenced, label)
    intent_payload, _intent_stat = read_regular(intent_path, f"{lane} activation intent")
    activation_payload, _activation_stat = read_regular(
        activation_receipt_path, f"{lane} activation receipt"
    )
    if (
        sha256(intent_payload) != activation.get("intent_sha256")
        or sha256(activation_payload) != activation.get("receipt_sha256")
    ):
        fail(f"{lane} checkpoint activation references changed")
    intent = require_jq_json_object(
        intent_payload,
        f"{lane} activation intent",
        (
            "schema_version",
            "checkpoint_id",
            "status",
            "prepared_at",
            "selector",
            "expected_old_selector_identity",
            "target",
            "state",
            "input_bindings",
            "activation_evidence",
            "recovery_rule",
        ),
    )
    activation_receipt = require_jq_json_object(
        activation_payload,
        f"{lane} activation receipt",
        (
            "schema_version",
            "checkpoint_id",
            "status",
            "activated_at",
            "selector",
            "target",
            "activated_selector_identity",
            "displaced_selector_witness",
            "displaced_selector_identity",
            "activation_intent",
            "prepared_selector_record",
            "activation_evidence",
        ),
    )
    if (
        intent.get("schema_version") != 1
        or intent.get("checkpoint_id") != checkpoint_id
        or intent.get("status") != "prepared"
        or intent.get("selector") != os.fspath(selector)
        or intent.get("target") != os.fspath(durable_path)
        or intent.get("state") != os.fspath(canonical_path)
        or activation_receipt.get("schema_version") != 1
        or activation_receipt.get("checkpoint_id") != checkpoint_id
        or activation_receipt.get("status") != "selector-activated"
        or activation_receipt.get("selector") != os.fspath(selector)
        or activation_receipt.get("target") != os.fspath(durable_path)
    ):
        fail(f"{lane} checkpoint activation intent and receipt disagree")
    activation_intent_ref = require_ordered_object(
        activation_receipt["activation_intent"],
        f"{lane} activation-intent reference",
        ("path", "sha256"),
    )
    read_sha256_reference(
        activation_intent_ref,
        f"{lane} activation-intent reference",
        production=production,
        expected_path=intent_path,
    )
    prepared_ref = require_ordered_object(
        activation_receipt["prepared_selector_record"],
        f"{lane} prepared-selector reference",
        ("path", "sha256"),
    )
    prepared_path, prepared_payload = read_sha256_reference(
        prepared_ref,
        f"{lane} prepared-selector record",
        production=production,
        expected_path=report / "prepared-selector.json",
    )
    evidence_ref = require_ordered_object(
        activation_receipt["activation_evidence"],
        f"{lane} activation-evidence reference",
        ("path", "sha256"),
    )
    activation_evidence_path, _activation_evidence_payload = read_sha256_reference(
        evidence_ref,
        f"{lane} activation evidence",
        production=production,
        expected_path=report / "activation-evidence-manifest.sha256",
    )
    if activation_receipt["activation_evidence"] != intent["activation_evidence"]:
        fail(f"{lane} activation receipt does not bind the intent evidence")
    prepared_record = require_jq_json_object(
        prepared_payload,
        f"{lane} prepared-selector record",
        (
            "schema_version",
            "checkpoint_id",
            "prepared_at",
            "path",
            "target",
            "selector_identity",
            "activation_intent_sha256",
        ),
    )
    prepared_selector = selector.with_name(f"{selector.name}.prepared-{checkpoint_id}")
    if (
        prepared_record.get("schema_version") != 1
        or prepared_record.get("checkpoint_id") != checkpoint_id
        or prepared_record.get("path") != os.fspath(prepared_selector)
        or prepared_record.get("target") != os.fspath(durable_path)
        or prepared_record.get("activation_intent_sha256") != sha256(intent_payload)
        or prepared_record.get("selector_identity")
        != activation_receipt.get("activated_selector_identity")
    ):
        fail(f"{lane} prepared-selector record is not bound to activation")
    witness = absolute_path(
        activation_receipt["displaced_selector_witness"],
        f"{lane} displaced-selector witness",
    )
    if not witness.is_symlink():
        fail(f"{lane} checkpoint displaced-selector witness is absent or invalid")
    if witness != selector.with_name(f"{selector.name}.previous-{checkpoint_id}"):
        fail(f"{lane} checkpoint displaced-selector witness path is not exact")
    if production:
        validate_root_trust(witness.parent, f"{lane} witness parent", directory=True)
        if witness.lstat().st_uid != 0 or witness.lstat().st_gid != 0:
            fail(f"{lane} displaced-selector witness is not root-owned")
    displaced_identity = checkpoint_selector_identity(
        witness, f"{lane} displaced-selector witness"
    )
    if (
        require_string(
            activation_receipt["displaced_selector_identity"],
            f"{lane} displaced-selector identity",
        )
        != displaced_identity
        or intent.get("expected_old_selector_identity") != displaced_identity
    ):
        fail(f"{lane} checkpoint displaced-selector identity is not exact")
    if lane == "post" and (
        checkpoint_selector_identity(selector, "current post-checkpoint selector")
        != activation_receipt.get("activated_selector_identity")
    ):
        fail("post-checkpoint activated-selector identity changed")
    offline = require_ordered_object(
        state["offline_restore"],
        f"{lane} checkpoint offline restore",
        ("receipt", "receipt_sha256", "evidence"),
    )
    if (
        offline.get("receipt") != os.fspath(paths[receipt_label])
        or offline.get("receipt_sha256") != sha256(payloads[receipt_label])
    ):
        fail(f"{lane} checkpoint offline receipt does not match its external evidence")
    offline_receipt = require_jq_json_object(
        payloads[receipt_label],
        f"{lane} checkpoint offline-restore receipt",
        (
            "schema_version",
            "checkpoint_id",
            "status",
            "recorded_at",
            "activation_receipt_sha256",
            "evidence",
        ),
    )
    receipt_evidence = require_ordered_object(
        offline_receipt["evidence"],
        f"{lane} offline-restore evidence",
        ("command", "binpkg", "post_verifier", "attempt_ledger"),
    )
    if (
        offline_receipt.get("schema_version") != 1
        or offline_receipt.get("checkpoint_id") != checkpoint_id
        or offline_receipt.get("status") != "offline-restore-proven"
        or offline_receipt.get("activation_receipt_sha256") != sha256(activation_payload)
        or offline.get("evidence") != receipt_evidence
        or paths[receipt_label] != report / "offline-restore-receipt.json"
    ):
        fail(f"{lane} checkpoint offline-restore receipt is incoherent")
    relative_names = (
        ("command", "offline-restore/command.json"),
        ("binpkg", "offline-restore/binpkg.json"),
        ("post_verifier", "offline-restore/post-verifier.json"),
        ("attempt_ledger", "offline-restore/attempt-ledger.sha256"),
    )
    offline_payloads: dict[str, bytes] = {}
    for name, relative in relative_names:
        reference = require_ordered_object(
            receipt_evidence[name],
            f"{lane} offline-restore {name}",
            ("path", "sha256"),
        )
        if reference.get("path") != relative:
            fail(f"{lane} offline-restore {name} path is not exact")
        evidence_path = report / relative
        if production:
            validate_root_trust(evidence_path, f"{lane} offline-restore {name}")
        evidence_payload, _evidence_stat = read_regular(
            evidence_path, f"{lane} offline-restore {name}"
        )
        if sha256(evidence_payload) != reference.get("sha256"):
            fail(f"{lane} offline-restore {name} changed")
        offline_payloads[name] = evidence_payload
    binpkg_record = require_jq_json_object(
        offline_payloads["binpkg"],
        f"{lane} offline-restore binpkg",
        (
            "schema_version",
            "sequence",
            "checkpoint_id",
            "selected_at",
            "selected_at_unix_ns",
            "selected_snapshot",
            "cpv",
            "archive_relative_path",
            "archive_sha256",
        ),
    )
    restored_cpv = require_string(binpkg_record.get("cpv"), f"{lane} restored CPV")
    selected_ns = require_string(
        binpkg_record.get("selected_at_unix_ns"),
        f"{lane} restore selection time",
        re.compile(r"[0-9]+"),
    )
    archive_relative = relative_path(
        binpkg_record.get("archive_relative_path"), f"{lane} restore archive"
    )
    archive_path = durable_path / archive_relative
    archive_payload, _archive_stat = read_regular(archive_path, f"{lane} restore archive")
    if (
        binpkg_record.get("schema_version") != 1
        or binpkg_record.get("sequence") != 1
        or binpkg_record.get("checkpoint_id") != checkpoint_id
        or binpkg_record.get("selected_snapshot") != os.fspath(durable_path)
        or binpkg_record.get("archive_sha256") != sha256(archive_payload)
        or not selected_ns.isdigit()
    ):
        fail(f"{lane} offline-restore binpkg selection is incoherent")
    command = require_jq_json_object(
        offline_payloads["command"],
        f"{lane} offline-restore command",
        (
            "schema_version",
            "sequence",
            "checkpoint_id",
            "attempt",
            "started_at",
            "started_at_unix_ns",
            "completed_at",
            "completed_at_unix_ns",
            "exit_status",
            "offline",
            "network_isolated",
            "usepkgonly",
            "getbinpkg",
            "nodeps",
            "selected_snapshot",
            "pkgdir",
            "vdb",
            "restored_cpv",
            "binpkg_evidence_sha256",
            "command_intent_sha256",
            "retry_authorization",
            "emerge_tool_identity",
            "environment",
            "containment",
            "portage_implementation",
            "selected_archive",
            "selected_sets_transition",
            "pkgdir_transition",
            "transaction_baseline_transition",
            "pretend",
            "command",
            "vdb_transition",
            "pre_command_verifier",
            "logs",
            "package_check",
        ),
    )
    selected_archive = require_object(
        command.get("selected_archive"),
        f"{lane} selected restore archive",
        {
            "path",
            "relative_path",
            "size_before",
            "sha256_before",
            "size_after",
            "sha256_after",
            "unchanged",
        },
    )
    command_vdb = absolute_path(command.get("vdb"), f"{lane} restore VDB")
    attempt = require_int(command.get("attempt"), f"{lane} restore attempt", minimum=0)
    started_ns = require_string(
        command.get("started_at_unix_ns"), f"{lane} restore start time", re.compile(r"[0-9]+")
    )
    completed_ns = require_string(
        command.get("completed_at_unix_ns"),
        f"{lane} restore completion time",
        re.compile(r"[0-9]+"),
    )
    if (
        command.get("schema_version") != 4
        or command.get("sequence") != 2
        or command.get("checkpoint_id") != checkpoint_id
        or command.get("exit_status") != 0
        or command.get("offline") is not True
        or command.get("network_isolated") is not True
        or command.get("usepkgonly") is not True
        or command.get("getbinpkg") is not False
        or command.get("nodeps") is not True
        or command.get("selected_snapshot") != os.fspath(durable_path)
        or command.get("pkgdir") != os.fspath(durable_path)
        or (production and command_vdb != Path("/var/db/pkg"))
        or command.get("restored_cpv") != restored_cpv
        or command.get("binpkg_evidence_sha256") != sha256(offline_payloads["binpkg"])
        or selected_archive.get("path") != os.fspath(archive_path)
        or selected_archive.get("relative_path") != archive_relative
        or selected_archive.get("sha256_before") != sha256(archive_payload)
        or selected_archive.get("sha256_after") != sha256(archive_payload)
        or selected_archive.get("size_before") != len(archive_payload)
        or selected_archive.get("size_after") != len(archive_payload)
        or selected_archive.get("unchanged") is not True
        or int(started_ns) > int(completed_ns)
    ):
        fail(f"{lane} offline-restore command is not an exact successful restore")
    environment = require_object(
        command.get("environment"),
        f"{lane} offline-restore environment",
        {
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "TZ",
            "PKGDIR",
            "PORTAGE_BINHOST",
            "GENTOO_MIRRORS",
            "FETCHCOMMAND",
            "RESUMECOMMAND",
            "EPYTHON",
        },
    )
    if (
        environment.get("LANG") != "C"
        or environment.get("LC_ALL") != "C"
        or environment.get("TZ") != "UTC"
        or environment.get("PKGDIR") != os.fspath(durable_path)
        or environment.get("PORTAGE_BINHOST") != ""
        or environment.get("GENTOO_MIRRORS") != ""
        or environment.get("FETCHCOMMAND") != "/bin/false"
        or environment.get("RESUMECOMMAND") != "/bin/false"
        or environment.get("EPYTHON") != "python3.15"
        or (production and environment.get("HOME") != "/root")
        or (production and environment.get("PATH") != "/usr/sbin:/usr/bin:/sbin:/bin")
    ):
        fail(f"{lane} offline-restore environment is not isolated")
    containment = require_object(
        command.get("containment"),
        f"{lane} offline-restore containment",
        {
            "network_namespace",
            "pid_namespace",
            "mount_proc",
            "launcher",
            "unshare_tool_identity",
            "preflight",
        },
    )
    launcher = require_list(
        containment.get("launcher"), f"{lane} offline-restore launcher", nonempty=True
    )
    launcher_path = absolute_path(launcher[0], f"{lane} offline-restore launcher path")

    def exact_tool_identity(path: Path, nested_label: str) -> str:
        observed = checkpoint_tools.get(os.fspath(path))
        if observed is None:
            fail(f"{lane} {nested_label} is absent from checkpoint tool identities")
        return observed

    if (
        containment.get("network_namespace") is not True
        or containment.get("pid_namespace") is not True
        or containment.get("mount_proc") is not True
        or len(launcher) != 7
        or launcher[1:] != [
            "--pid",
            "--net",
            "--fork",
            "--kill-child=KILL",
            "--mount-proc",
            "--",
        ]
        or (production and launcher[0] != "/usr/bin/unshare")
        or containment.get("unshare_tool_identity")
        != exact_tool_identity(launcher_path, "unshare tool")
    ):
        fail(f"{lane} offline-restore containment is incomplete")
    offline_root = report / "offline-restore"
    referenced_paths: set[Path] = set()

    def evidence_reference(value: object, nested_label: str) -> Path:
        observed = validate_local_evidence_reference(
            value,
            f"{lane} offline-restore {nested_label}",
            root=offline_root,
            production=production,
        )
        referenced_paths.add(observed)
        return observed

    containment_preflight_path = evidence_reference(
        containment["preflight"], "containment preflight"
    )
    containment_preflight_payload, _containment_preflight_stat = read_regular(
        containment_preflight_path, f"{lane} containment preflight"
    )
    containment_preflight_value = require_object(
        parse_json_bytes(containment_preflight_payload, f"{lane} containment preflight"),
        f"{lane} containment preflight",
        {
            "schema_version",
            "emulated",
            "direct_pidfd_sigterm",
            "unshare_kill_child_sigkill",
        },
    )
    direct_pidfd = require_object(
        containment_preflight_value.get("direct_pidfd_sigterm"),
        f"{lane} containment direct pidfd proof",
        {"exact_child_gone", "pidfd_open", "pidfd_send_signal", "signal", "returncode"},
    )
    namespace_proof = require_object(
        containment_preflight_value.get("unshare_kill_child_sigkill"),
        f"{lane} containment namespace proof",
        {
            "descendant_pidfd_open",
            "escaped_private_process_group_gone",
            "escaped_setsid_descendant_gone",
            "exact_namespace_child_gone",
            "ipv4_errno",
            "ipv4_external_unreachable",
            "ipv6_errno",
            "ipv6_external_unreachable",
            "kill_child_signal",
            "mount_proc",
            "namespace_interfaces",
            "namespace_pid",
            "network_namespace",
            "network_namespace_distinct",
            "pid_namespace",
            "private_process_group_gone",
            "supervisor_pidfd_open",
            "supervisor_returncode",
            "supervisor_signal",
        },
    )
    if (
        containment_preflight_value.get("schema_version") != 3
        or containment_preflight_value.get("emulated") is production
        or direct_pidfd
        != {
            "exact_child_gone": True,
            "pidfd_open": True,
            "pidfd_send_signal": True,
            "signal": "SIGTERM",
            "returncode": -15,
        }
        or any(
            namespace_proof.get(key) is not True
            for key in (
                "descendant_pidfd_open",
                "escaped_private_process_group_gone",
                "escaped_setsid_descendant_gone",
                "exact_namespace_child_gone",
                "ipv4_external_unreachable",
                "ipv6_external_unreachable",
                "mount_proc",
                "network_namespace",
                "network_namespace_distinct",
                "pid_namespace",
                "private_process_group_gone",
                "supervisor_pidfd_open",
            )
        )
        or namespace_proof.get("ipv4_errno") != "ENETUNREACH"
        or namespace_proof.get("ipv6_errno")
        not in {"EADDRNOTAVAIL", "EAFNOSUPPORT", "ENETUNREACH"}
        or namespace_proof.get("kill_child_signal") != "SIGKILL"
        or namespace_proof.get("namespace_interfaces") != ["lo"]
        or namespace_proof.get("namespace_pid") != 1
        or namespace_proof.get("supervisor_returncode") != -9
        or namespace_proof.get("supervisor_signal") != "SIGKILL"
    ):
        fail(f"{lane} containment preflight is not the complete reviewed proof")
    if production:
        require_compact_sorted_json_object(
            containment_preflight_payload,
            f"{lane} containment preflight",
            {
                "schema_version",
                "emulated",
                "direct_pidfd_sigterm",
                "unshare_kill_child_sigkill",
            },
        )
    portage = require_object(
        command.get("portage_implementation"),
        f"{lane} Portage implementation",
        {
            "cpv",
            "epython",
            "python",
            "emerge",
            "package_match",
            "package_check_before",
            "package_check_after",
        },
    )
    portage_cpv = require_string(
        portage.get("cpv"),
        f"{lane} Portage CPV",
        re.compile(r"sys-apps/portage-[0-9][^\n\r\t\0]*"),
    )
    portage_python = require_object(
        portage.get("python"),
        f"{lane} Portage Python",
        {"path", "tool_identity"},
    )
    portage_emerge = require_object(
        portage.get("emerge"),
        f"{lane} Portage emerge implementation",
        {"path", "tool_identity"},
    )
    python_path = absolute_path(portage_python.get("path"), f"{lane} Portage Python path")
    emerge_implementation_path = absolute_path(
        portage_emerge.get("path"), f"{lane} Portage emerge implementation path"
    )
    if (
        portage.get("epython") != environment.get("EPYTHON")
        or portage_python.get("tool_identity")
        != exact_tool_identity(python_path, "Portage Python")
        or portage_emerge.get("tool_identity")
        != exact_tool_identity(emerge_implementation_path, "Portage implementation")
        or (production and python_path != Path("/usr/bin/python3.15"))
    ):
        fail(f"{lane} Portage implementation identity is not exact")

    def validate_portage_check(
        check_name: str,
        *,
        tool_name: str,
        arguments: list[str],
        exact_stdout: bytes | None = None,
    ) -> tuple[Path, Path]:
        check = require_object(
            portage.get(check_name),
            f"{lane} {check_name}",
            {"tool", "tool_identity", "argv", "exit_status", "stdout", "stderr"},
        )
        argv = require_list(check.get("argv"), f"{lane} {check_name} argv", nonempty=True)
        tool_path = absolute_path(argv[0], f"{lane} {check_name} tool path")
        stdout_path = evidence_reference(check.get("stdout"), f"{check_name} stdout")
        stderr_path = evidence_reference(check.get("stderr"), f"{check_name} stderr")
        stdout_payload, _stdout_stat = read_regular(stdout_path, f"{lane} {check_name} stdout")
        stderr_payload, _stderr_stat = read_regular(stderr_path, f"{lane} {check_name} stderr")
        production_tool = {
            "portageq": Path("/usr/bin/portageq"),
            "qcheck": Path("/usr/bin/qcheck"),
        }[tool_name]
        if (
            check.get("tool") != tool_name
            or check.get("tool_identity") != exact_tool_identity(tool_path, tool_name)
            or argv != [os.fspath(tool_path), *arguments]
            or check.get("exit_status") != 0
            or stderr_payload != b""
            or (exact_stdout is not None and stdout_payload != exact_stdout)
            or (production and tool_path != production_tool)
        ):
            fail(f"{lane} {check_name} does not prove the exact Portage operation")
        return stdout_path, stderr_path

    validate_portage_check(
        "package_match",
        tool_name="portageq",
        arguments=["match", "/", "sys-apps/portage"],
        exact_stdout=(portage_cpv + "\n").encode("utf-8"),
    )
    validate_portage_check(
        "package_check_before",
        tool_name="qcheck",
        arguments=[f"={portage_cpv}"],
    )
    validate_portage_check(
        "package_check_after",
        tool_name="qcheck",
        arguments=[f"={portage_cpv}"],
    )
    transition_references: dict[str, tuple[Path, Path]] = {}
    for transition_name in ("selected_sets_transition", "pkgdir_transition"):
        transition = require_object(
            command.get(transition_name),
            f"{lane} {transition_name}",
            {"before", "after", "unchanged"},
        )
        if transition.get("unchanged") is not True:
            fail(f"{lane} {transition_name} is not unchanged")
        before_path = evidence_reference(
            transition.get("before"), f"{transition_name} before"
        )
        after_path = evidence_reference(
            transition.get("after"), f"{transition_name} after"
        )
        if before_path.read_bytes() != after_path.read_bytes():
            fail(f"{lane} {transition_name} changed despite its unchanged claim")
        transition_references[transition_name] = (before_path, after_path)
    baseline = require_object(
        command.get("transaction_baseline_transition"),
        f"{lane} baseline transition",
        {"vdb", "selected_sets", "pkgdir"},
    )
    baseline_references: dict[str, tuple[Path, Path]] = {}
    for name in ("vdb", "selected_sets", "pkgdir"):
        expected_flag = "confined_to_restored_cpv" if name == "vdb" else "unchanged"
        transition = require_object(
            baseline.get(name),
            f"{lane} baseline {name}",
            {"before", "after", expected_flag},
        )
        before_path = evidence_reference(
            transition.get("before"), f"baseline {name} before"
        )
        after_path = evidence_reference(
            transition.get("after"), f"baseline {name} after"
        )
        if transition.get(expected_flag) is not True:
            fail(f"{lane} baseline {name} transition is not exact")
        if name != "vdb" and before_path.read_bytes() != after_path.read_bytes():
            fail(f"{lane} baseline {name} changed despite its unchanged claim")
        baseline_references[name] = (before_path, after_path)
    command_argv = require_list(
        command.get("command"), f"{lane} restore command", nonempty=True
    )
    emerge_path = absolute_path(command_argv[0], f"{lane} emerge command path")
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
    expected_command_argv = [os.fspath(emerge_path), *command_options, os.fspath(archive_path)]
    pretend = require_object(
        command.get("pretend"),
        f"{lane} offline pretend",
        {"argv", "exit_status", "summary", "logs"},
    )
    summary = require_object(
        pretend.get("summary"),
        f"{lane} offline pretend summary",
        {"packages", "reinstall", "binary", "download_kib"},
    )
    pretend_logs = require_object(
        pretend.get("logs"), f"{lane} offline pretend logs", {"stdout", "stderr"}
    )
    pretend_argv = require_list(
        pretend.get("argv"), f"{lane} restore pretend argv", nonempty=True
    )
    if (
        pretend.get("exit_status") != 0
        or summary
        != {"packages": 1, "reinstall": 1, "binary": 1, "download_kib": 0}
        or command_argv != expected_command_argv
        or pretend_argv
        != [os.fspath(emerge_path), *command_options, "--pretend", os.fspath(archive_path)]
        or command.get("emerge_tool_identity")
        != exact_tool_identity(emerge_path, "emerge command")
        or (production and emerge_path != Path("/usr/bin/emerge"))
    ):
        fail(f"{lane} offline pretend is not one binary-only reinstall")
    pretend_stdout_path = evidence_reference(pretend_logs.get("stdout"), "pretend stdout")
    pretend_stderr_path = evidence_reference(pretend_logs.get("stderr"), "pretend stderr")
    pretend_stdout_payload, _pretend_stdout_stat = read_regular(
        pretend_stdout_path, f"{lane} pretend stdout"
    )
    pretend_stderr_payload, _pretend_stderr_stat = read_regular(
        pretend_stderr_path, f"{lane} pretend stderr"
    )
    if (
        pretend_stderr_payload != b""
        or pretend_stdout_payload.count(
            b"Total: 1 package (1 reinstall, 1 binary), Size of downloads: 0 KiB\n"
        )
        != 1
        or sum(
            line.startswith(b"[binary") and b"R" in line
            for line in pretend_stdout_payload.splitlines()
        )
        != 1
    ):
        fail(f"{lane} offline pretend logs do not prove one binary reinstall")
    vdb_transition = require_object(
        command.get("vdb_transition"),
        f"{lane} VDB transition",
        {"before", "after", "changed"},
    )
    if vdb_transition.get("changed") is not True:
        fail(f"{lane} offline restore does not record a changed VDB subtree")
    vdb_before_path = evidence_reference(vdb_transition.get("before"), "VDB before")
    vdb_after_path = evidence_reference(vdb_transition.get("after"), "VDB after")
    if (
        (attempt == 0 and baseline_references["vdb"] != (vdb_before_path, vdb_after_path))
        or baseline_references["vdb"][1] != vdb_after_path
        or baseline_references["selected_sets"][1]
        != transition_references["selected_sets_transition"][1]
        or baseline_references["pkgdir"][1] != transition_references["pkgdir_transition"][1]
        or baseline_references["selected_sets"][0].read_bytes()
        != transition_references["selected_sets_transition"][0].read_bytes()
        or baseline_references["pkgdir"][0].read_bytes()
        != transition_references["pkgdir_transition"][0].read_bytes()
    ):
        fail(f"{lane} per-attempt and transaction-baseline evidence disagree")
    pre_command_verifier_path = evidence_reference(
        command.get("pre_command_verifier"), "pre-command verifier"
    )
    logs = require_object(
        command.get("logs"), f"{lane} restore logs", {"stdout", "stderr"}
    )
    restore_stdout_path = evidence_reference(logs.get("stdout"), "restore stdout")
    evidence_reference(logs.get("stderr"), "restore stderr")
    restore_stdout_payload, _restore_stdout_stat = read_regular(
        restore_stdout_path, f"{lane} restore stdout"
    )
    if (
        sum(line.startswith(b">>> Emerging binary (") for line in restore_stdout_payload.splitlines())
        != 1
        or any(
            line.startswith(b">>> Emerging (")
            and not line.startswith(b">>> Emerging binary (")
            for line in restore_stdout_payload.splitlines()
        )
    ):
        fail(f"{lane} restore output does not prove one binary and zero source merges")
    package_check = require_object(
        command.get("package_check"),
        f"{lane} restored package check",
        {"tool", "tool_identity", "argv", "exit_status", "stdout", "stderr"},
    )
    if package_check.get("exit_status") != 0:
        fail(f"{lane} restored package check did not pass")
    package_stdout_path = evidence_reference(
        package_check.get("stdout"), "package-check stdout"
    )
    package_stderr_path = evidence_reference(
        package_check.get("stderr"), "package-check stderr"
    )
    package_argv = require_list(
        package_check.get("argv"), f"{lane} restored package-check argv", nonempty=True
    )
    package_tool = absolute_path(package_argv[0], f"{lane} restored qcheck path")
    _package_stdout_payload, _package_stdout_stat = read_regular(
        package_stdout_path, f"{lane} restored qcheck stdout"
    )
    package_stderr_payload, _package_stderr_stat = read_regular(
        package_stderr_path, f"{lane} restored qcheck stderr"
    )
    if (
        package_check.get("tool") != "qcheck"
        or package_check.get("tool_identity")
        != exact_tool_identity(package_tool, "restored qcheck")
        or package_argv != [os.fspath(package_tool), f"={restored_cpv}"]
        or package_stderr_payload != b""
        or (production and package_tool != Path("/usr/bin/qcheck"))
    ):
        fail(f"{lane} restored package check is not exact")
    command_intent_path = offline_root / "command-intent.json"
    command_intent_payload, _command_intent_stat = read_regular(
        command_intent_path, f"{lane} restore command intent"
    )
    if command.get("command_intent_sha256") != sha256(command_intent_payload):
        fail(f"{lane} restore command intent changed")
    referenced_paths.add(command_intent_path)
    command_intent = require_jq_json_object(
        command_intent_payload,
        f"{lane} restore command intent",
        (
            "schema_version",
            "checkpoint_id",
            "status",
            "started_at",
            "started_at_unix_ns",
            "emerge_tool_identity",
            "environment",
            "argv",
            "containment",
            "portage_implementation",
            "selected_archive",
            "selected_sets_before",
            "pkgdir_before",
            "pretend",
            "binpkg_evidence_sha256",
            "vdb_before",
            "pre_command_verifier",
        ),
    )
    intent_archive = require_ordered_object(
        command_intent.get("selected_archive"),
        f"{lane} command-intent archive",
        ("path", "relative_path", "size", "sha256"),
    )
    intent_containment = require_object(
        command_intent.get("containment"),
        f"{lane} command-intent containment",
        {
            "network_namespace",
            "pid_namespace",
            "mount_proc",
            "launcher",
            "unshare_tool_identity",
            "preflight",
        },
    )
    intent_portage = require_object(
        command_intent.get("portage_implementation"),
        f"{lane} command-intent Portage",
        {"cpv", "epython", "python", "emerge", "package_match", "package_check_before"},
    )
    intent_pretend = require_object(
        command_intent.get("pretend"),
        f"{lane} command-intent pretend",
        {"argv", "exit_status", "summary", "logs"},
    )
    intent_pretend_logs = require_object(
        intent_pretend.get("logs"),
        f"{lane} command-intent pretend logs",
        {"stdout", "stderr"},
    )
    intent_preflight = evidence_reference(
        intent_containment.get("preflight"), "command-intent containment preflight"
    )
    intent_selected_before = evidence_reference(
        command_intent.get("selected_sets_before"), "command-intent selected sets"
    )
    intent_pkgdir_before = evidence_reference(
        command_intent.get("pkgdir_before"), "command-intent PKGDIR"
    )
    intent_vdb_before = evidence_reference(
        command_intent.get("vdb_before"), "command-intent VDB"
    )
    intent_pre_verifier = evidence_reference(
        command_intent.get("pre_command_verifier"), "command-intent pre-verifier"
    )
    intent_pretend_stdout = evidence_reference(
        intent_pretend_logs.get("stdout"), "command-intent pretend stdout"
    )
    intent_pretend_stderr = evidence_reference(
        intent_pretend_logs.get("stderr"), "command-intent pretend stderr"
    )
    intent_match = require_object(
        intent_portage.get("package_match"), f"{lane} command-intent package match"
    )
    intent_qcheck = require_object(
        intent_portage.get("package_check_before"),
        f"{lane} command-intent Portage package check",
    )
    for intent_check, nested_label in (
        (intent_match, "command-intent package-match"),
        (intent_qcheck, "command-intent package-check"),
    ):
        evidence_reference(intent_check.get("stdout"), f"{nested_label} stdout")
        intent_check_stderr = evidence_reference(
            intent_check.get("stderr"), f"{nested_label} stderr"
        )
        if intent_check_stderr.read_bytes() != b"":
            fail(f"{lane} {nested_label} has nonempty stderr")
    if (
        command_intent.get("schema_version") != 3
        or command_intent.get("checkpoint_id") != checkpoint_id
        or command_intent.get("status") != "supervised-command-pending"
        or not str(command_intent.get("started_at_unix_ns", "")).isdigit()
        or command_intent.get("emerge_tool_identity")
        != exact_tool_identity(emerge_path, "command-intent emerge")
        or command_intent.get("environment") != environment
        or command_intent.get("argv") != expected_command_argv
        or intent_containment.get("network_namespace") is not True
        or intent_containment.get("pid_namespace") is not True
        or intent_containment.get("mount_proc") is not True
        or intent_containment.get("launcher") != launcher
        or intent_containment.get("unshare_tool_identity")
        != exact_tool_identity(launcher_path, "command-intent unshare")
        or intent_portage.get("cpv") != portage_cpv
        or intent_portage.get("epython") != environment.get("EPYTHON")
        or intent_portage.get("python") != portage_python
        or intent_portage.get("emerge") != portage_emerge
        or intent_archive
        != {
            "path": os.fspath(archive_path),
            "relative_path": archive_relative,
            "size": len(archive_payload),
            "sha256": sha256(archive_payload),
        }
        or intent_selected_before != baseline_references["selected_sets"][0]
        or intent_pkgdir_before != baseline_references["pkgdir"][0]
        or intent_vdb_before != baseline_references["vdb"][0]
        or intent_pretend.get("argv")
        != [os.fspath(emerge_path), *command_options, "--pretend", os.fspath(archive_path)]
        or intent_pretend.get("exit_status") != 0
        or intent_pretend.get("summary") != summary
        or intent_pretend_stdout.read_bytes() != pretend_stdout_payload
        or intent_pretend_stderr.read_bytes() != b""
        or command_intent.get("binpkg_evidence_sha256")
        != sha256(offline_payloads["binpkg"])
        or (attempt == 0 and intent_pre_verifier != pre_command_verifier_path)
        or (
            attempt == 0
            and (
                intent_containment != containment
                or intent_portage
                != {key: portage[key] for key in portage if key != "package_check_after"}
                or intent_pretend != pretend
                or intent_selected_before
                != transition_references["selected_sets_transition"][0]
                or intent_pkgdir_before != transition_references["pkgdir_transition"][0]
                or intent_vdb_before != vdb_before_path
            )
        )
    ):
        fail(f"{lane} restore command intent is not the exact durable predecessor")

    retry_reference = command.get("retry_authorization")
    current_retry_path: Path | None = None
    if attempt == 0:
        if retry_reference is not None:
            fail(f"{lane} initial restore unexpectedly has retry authorization")
    else:
        current_retry_path = evidence_reference(
            retry_reference, "terminal retry authorization"
        )
    post_verifier = require_jq_json_object(
        offline_payloads["post_verifier"],
        f"{lane} offline-restore post verifier",
        (
            "schema_version",
            "sequence",
            "checkpoint_id",
            "completed_at",
            "completed_at_unix_ns",
            "command_evidence_sha256",
            "binpkg_evidence_sha256",
            "verifier",
            "report",
        ),
    )
    post_report = require_object(post_verifier.get("report"), f"{lane} post-verifier report")
    post_inputs = require_object(post_report.get("inputs"), f"{lane} post-verifier inputs")
    post_counts = require_object(post_report.get("counts"), f"{lane} post-verifier counts")
    verifier_binding = require_ordered_object(
        post_verifier.get("verifier"), f"{lane} post-verifier identity", ("path", "sha256")
    )
    durable_final_payload, _durable_final_stat = read_regular(
        report / "durable-final-verification.json",
        f"{lane} durable final verifier report",
    )
    durable_final_report = require_pretty_json_object(
        durable_final_payload, f"{lane} durable final verifier report"
    )
    post_report_path = offline_root / "post-verifier-report.json"
    post_report_payload, _post_report_stat = read_regular(
        post_report_path, f"{lane} standalone post-verifier report"
    )
    post_report_stderr_path = offline_root / "post-verifier-report.json.stderr"
    post_report_stderr_payload, _post_report_stderr_stat = read_regular(
        post_report_stderr_path, f"{lane} post-verifier stderr"
    )
    pre_command_payload, _pre_command_stat = read_regular(
        pre_command_verifier_path, f"{lane} pre-command verifier report"
    )
    pre_command_report = require_pretty_json_object(
        pre_command_payload, f"{lane} pre-command verifier report"
    )
    intent_pre_verifier_payload, _intent_pre_verifier_stat = read_regular(
        intent_pre_verifier, f"{lane} command-intent pre-verifier report"
    )
    intent_pre_verifier_report = require_pretty_json_object(
        intent_pre_verifier_payload,
        f"{lane} command-intent pre-verifier report",
    )
    intent_pre_verifier_stderr = intent_pre_verifier.with_name(
        intent_pre_verifier.name + ".stderr"
    )
    intent_pre_verifier_stderr_payload, _intent_pre_verifier_stderr_stat = read_regular(
        intent_pre_verifier_stderr, f"{lane} command-intent pre-verifier stderr"
    )
    pre_command_stderr = pre_command_verifier_path.with_name(
        pre_command_verifier_path.name + ".stderr"
    )
    pre_command_stderr_payload, _pre_command_stderr_stat = read_regular(
        pre_command_stderr, f"{lane} pre-command verifier stderr"
    )
    referenced_paths.update(
        {post_report_path, post_report_stderr_path, intent_pre_verifier_stderr}
    )
    retry_authorized_ns: list[int] = []
    for retry_index in range(1, attempt + 1):
        retry_path = offline_root / f"retry-intent-{retry_index:03d}.json"
        retry_payload, _retry_stat = read_regular(
            retry_path, f"{lane} retry authorization {retry_index}"
        )
        referenced_paths.add(retry_path)
        retry = require_jq_json_object(
            retry_payload,
            f"{lane} retry authorization {retry_index}",
            (
                "schema_version",
                "checkpoint_id",
                "status",
                "authorized_at",
                "authorized_at_unix_ns",
                "attempt",
                "command_intent_sha256",
                "binpkg_evidence_sha256",
                "emerge_tool_identity",
                "environment",
                "argv",
                "containment",
                "portage_implementation",
                "selected_archive",
                "selected_sets_before",
                "pkgdir_before",
                "pretend",
                "vdb_before",
                "pre_command_verifier",
            ),
        )
        retry_ns_text = str(retry.get("authorized_at_unix_ns", ""))
        retry_containment = require_object(
            retry.get("containment"), f"{lane} retry {retry_index} containment"
        )
        retry_portage = require_object(
            retry.get("portage_implementation"),
            f"{lane} retry {retry_index} Portage authority",
            {"cpv", "epython", "python", "emerge", "package_match", "package_check_before"},
        )
        retry_archive = require_object(
            retry.get("selected_archive"), f"{lane} retry {retry_index} archive"
        )
        retry_pretend = require_object(
            retry.get("pretend"), f"{lane} retry {retry_index} pretend"
        )
        retry_selected = evidence_reference(
            retry.get("selected_sets_before"), f"retry {retry_index} selected sets"
        )
        retry_pkgdir = evidence_reference(
            retry.get("pkgdir_before"), f"retry {retry_index} PKGDIR"
        )
        retry_vdb = evidence_reference(
            retry.get("vdb_before"), f"retry {retry_index} VDB"
        )
        retry_pre_verifier = evidence_reference(
            retry.get("pre_command_verifier"), f"retry {retry_index} pre-verifier"
        )
        retry_pre_payload, _retry_pre_stat = read_regular(
            retry_pre_verifier, f"{lane} retry {retry_index} pre-verifier"
        )
        retry_pre_stderr = retry_pre_verifier.with_name(
            retry_pre_verifier.name + ".stderr"
        )
        retry_pre_stderr_payload, _retry_pre_stderr_stat = read_regular(
            retry_pre_stderr, f"{lane} retry {retry_index} pre-verifier stderr"
        )
        referenced_paths.add(retry_pre_stderr)
        retry_preflight = evidence_reference(
            retry_containment.get("preflight"), f"retry {retry_index} containment preflight"
        )
        retry_pretend_logs = require_object(
            retry_pretend.get("logs"), f"{lane} retry {retry_index} pretend logs"
        )
        retry_pretend_stdout = evidence_reference(
            retry_pretend_logs.get("stdout"), f"retry {retry_index} pretend stdout"
        )
        retry_pretend_stderr = evidence_reference(
            retry_pretend_logs.get("stderr"), f"retry {retry_index} pretend stderr"
        )
        for check_name in ("package_match", "package_check_before"):
            retry_check = require_object(
                retry_portage.get(check_name),
                f"{lane} retry {retry_index} {check_name}",
            )
            retry_check_stdout = evidence_reference(
                retry_check.get("stdout"), f"retry {retry_index} {check_name} stdout"
            )
            retry_check_stderr = evidence_reference(
                retry_check.get("stderr"), f"retry {retry_index} {check_name} stderr"
            )
            if retry_check_stderr.read_bytes() != b"":
                fail(f"{lane} retry {retry_index} {check_name} has stderr")
            expected_check = {
                "package_match": (
                    "portageq",
                    ["match", "/", "sys-apps/portage"],
                    (portage_cpv + "\n").encode(),
                ),
                "package_check_before": ("qcheck", [f"={portage_cpv}"], None),
            }[check_name]
            retry_check_argv = require_list(
                retry_check.get("argv"), f"{lane} retry {retry_index} {check_name} argv"
            )
            retry_check_tool = absolute_path(
                retry_check_argv[0], f"{lane} retry {retry_index} {check_name} tool"
            )
            if (
                retry_check.get("tool") != expected_check[0]
                or retry_check.get("tool_identity")
                != exact_tool_identity(retry_check_tool, f"retry {retry_index} {check_name}")
                or retry_check_argv
                != [os.fspath(retry_check_tool), *expected_check[1]]
                or retry_check.get("exit_status") != 0
                or (
                    expected_check[2] is not None
                    and retry_check_stdout.read_bytes() != expected_check[2]
                )
            ):
                fail(f"{lane} retry {retry_index} {check_name} is not exact")
        if (
            retry.get("schema_version") != 2
            or retry.get("checkpoint_id") != checkpoint_id
            or retry.get("status") != "operator-authorized-retry"
            or retry.get("attempt") != retry_index
            or not retry_ns_text.isdigit()
            or retry.get("command_intent_sha256") != sha256(command_intent_payload)
            or retry.get("binpkg_evidence_sha256") != sha256(offline_payloads["binpkg"])
            or retry.get("emerge_tool_identity")
            != exact_tool_identity(emerge_path, f"retry {retry_index} emerge")
            or retry.get("environment") != environment
            or retry.get("argv") != expected_command_argv
            or {key: retry_containment.get(key) for key in (
                "network_namespace", "pid_namespace", "mount_proc", "launcher", "unshare_tool_identity"
            )}
            != {key: containment.get(key) for key in (
                "network_namespace", "pid_namespace", "mount_proc", "launcher", "unshare_tool_identity"
            )}
            or retry_preflight.read_bytes() != containment_preflight_payload
            or retry_portage.get("cpv") != portage_cpv
            or retry_portage.get("epython") != environment.get("EPYTHON")
            or retry_portage.get("python") != portage_python
            or retry_portage.get("emerge") != portage_emerge
            or retry_archive
            != {
                "path": os.fspath(archive_path),
                "relative_path": archive_relative,
                "size": len(archive_payload),
                "sha256": sha256(archive_payload),
            }
            or retry_selected.name != f"selected-sets.before.{retry_index:03d}.tsv"
            or retry_pkgdir.name != f"pkgdir.before.{retry_index:03d}.tsv"
            or retry_vdb.name != f"vdb.before.{retry_index:03d}.tsv"
            or retry_pre_verifier.name
            != f"pre-command-verifier.{retry_index:03d}.json"
            or retry_pre_payload != durable_final_payload
            or retry_pre_stderr_payload != b""
            or retry_pretend.get("argv")
            != [os.fspath(emerge_path), *command_options, "--pretend", os.fspath(archive_path)]
            or retry_pretend.get("exit_status") != 0
            or retry_pretend.get("summary") != summary
            or retry_pretend_stdout.name != f"emerge.pretend.stdout.{retry_index:03d}"
            or retry_pretend_stderr.name != f"emerge.pretend.stderr.{retry_index:03d}"
            or retry_pretend_stdout.read_bytes().count(
                b"Total: 1 package (1 reinstall, 1 binary), Size of downloads: 0 KiB\n"
            )
            != 1
            or retry_pretend_stderr.read_bytes() != b""
        ):
            fail(f"{lane} restore retry {retry_index} is not an exact durable authorization")
        retry_authorized_ns.append(int(retry_ns_text))
    if (
        retry_authorized_ns != sorted(set(retry_authorized_ns))
        or any(
            later <= earlier
            for earlier, later in zip(retry_authorized_ns, retry_authorized_ns[1:])
        )
        or (
            retry_authorized_ns
            and (
                retry_authorized_ns[0] <= int(str(command_intent.get("started_at_unix_ns")))
                or retry_authorized_ns[-1] > int(started_ns)
            )
        )
        or int(selected_ns) > int(str(command_intent.get("started_at_unix_ns")))
        or (
            not retry_authorized_ns
            and int(str(command_intent.get("started_at_unix_ns"))) > int(started_ns)
        )
        or (
            attempt > 0
            and current_retry_path != offline_root / f"retry-intent-{attempt:03d}.json"
        )
    ):
        fail(f"{lane} restore retry chronology or terminal reference differs")
    if (
        post_verifier.get("schema_version") != 1
        or post_verifier.get("sequence") != 3
        or post_verifier.get("checkpoint_id") != checkpoint_id
        or post_verifier.get("command_evidence_sha256") != sha256(offline_payloads["command"])
        or post_verifier.get("binpkg_evidence_sha256") != sha256(offline_payloads["binpkg"])
        or verifier_binding != intent["input_bindings"]["verifier"]
        or post_report != durable_final_report
        or pre_command_report != durable_final_report
        or intent_pre_verifier_report != durable_final_report
        or post_report_payload != pretty_json(post_report)
        or post_report_stderr_payload != b""
        or pre_command_stderr_payload != b""
        or intent_pre_verifier_stderr_payload != b""
        or post_report.get("schema_version") != 1
        or post_report.get("status") != "pass"
        or post_report.get("issues") != []
        or post_inputs.get("snapshot") != os.fspath(durable_path)
        or post_inputs.get("validate_gpkg") is not True
        or post_inputs.get("allow_extra_archives") is not False
        or post_inputs.get("packages_index") != os.fspath(durable_path / "Packages")
        or (production and post_inputs.get("vdb") != "/var/db/pkg")
        or (production and post_inputs.get("zstd") != "/usr/bin/zstd")
        or any(
            post_counts.get(key) != 0
            for key in (
                "errors",
                "missing_live_cpvs",
                "extra_indexed_archives",
                "unindexed_gpkg_archives",
            )
        )
        or any(
            post_counts.get(key) != state["live_cpvs"]
            for key in (
                "live_cpvs",
                "indexed_records",
                "indexed_unique_cpvs",
                "indexed_unique_paths",
                "gpkg_archives_found",
                "gpkg_archives_indexed",
                "gpkg_archives_validated",
                "image_tar_zst_streams_tested",
            )
        )
        or not str(post_verifier.get("completed_at_unix_ns", "")).isdigit()
        or int(completed_ns) > int(str(post_verifier.get("completed_at_unix_ns")))
    ):
        fail(f"{lane} offline-restore post verifier is not a strict pass")
    ledger_path = report / "offline-restore/attempt-ledger.sha256"
    ledger_rows = validate_sha256_manifest(
        ledger_path,
        f"{lane} offline-restore attempt ledger",
        roots=(report / "offline-restore",),
        production=production,
    )
    if not ledger_rows:
        fail(f"{lane} offline-restore attempt ledger is empty")
    if not referenced_paths.issubset(set(ledger_rows)):
        fail(f"{lane} offline-restore ledger omits a command evidence artifact")
    allowed_ledger_name = re.compile(
        r"(?:"
        r"command-intent\.json|retry-intent-[0-9]{3}\.json|"
        r"pre-command-verifier\.[0-9]{3}\.json(?:\.stderr)?|"
        r"vdb\.(?:before|after)\.[0-9]{3}\.tsv(?:\.paths0.*)?|"
        r"post-verifier-report\.json(?:\.stderr)?|"
        r"emerge\.(?:stdout|stderr)\.[0-9]{3}|"
        r"emerge\.pretend\.(?:stdout|stderr)\.[0-9]{3}|"
        r"qcheck\.(?:stdout|stderr)\.[0-9]{3}|"
        r"containment-preflight\.[0-9]{3}\.json|"
        r"selected-sets\.(?:before|after)\.[0-9]{3}\.tsv|"
        r"pkgdir\.(?:before|after)\.[0-9]{3}\.tsv|"
        r"portage-match\.[0-9]{3}\.(?:stdout|stderr)|"
        r"portage-qcheck\.(?:before|after)\.[0-9]{3}\.(?:stdout|stderr)"
        r")"
    )
    if any(allowed_ledger_name.fullmatch(item.name) is None for item in ledger_rows):
        fail(f"{lane} offline-restore ledger contains a foreign artifact name")
    required_ledger_paths = {
        command_intent_path,
        post_report_path,
        post_report_stderr_path,
        pre_command_verifier_path.with_name(pre_command_verifier_path.name + ".stderr"),
    }
    attempt_text = f"{attempt:03d}"
    required_ledger_paths.update(
        offline_root / name
        for name in (
            f"pre-command-verifier.{attempt_text}.json",
            f"pre-command-verifier.{attempt_text}.json.stderr",
            f"vdb.before.{attempt_text}.tsv",
            f"vdb.before.{attempt_text}.tsv.paths0",
            f"vdb.before.{attempt_text}.tsv.paths0.unsorted.paths0",
            f"vdb.before.{attempt_text}.tsv.paths0.unsorted.paths0.stderr",
            f"vdb.before.{attempt_text}.tsv.paths0.sort.stderr",
            f"vdb.after.{attempt_text}.tsv",
            f"vdb.after.{attempt_text}.tsv.paths0",
            f"vdb.after.{attempt_text}.tsv.paths0.unsorted.paths0",
            f"vdb.after.{attempt_text}.tsv.paths0.unsorted.paths0.stderr",
            f"vdb.after.{attempt_text}.tsv.paths0.sort.stderr",
            f"containment-preflight.{attempt_text}.json",
            f"selected-sets.before.{attempt_text}.tsv",
            f"selected-sets.after.{attempt_text}.tsv",
            f"pkgdir.before.{attempt_text}.tsv",
            f"pkgdir.after.{attempt_text}.tsv",
            f"portage-match.{attempt_text}.stdout",
            f"portage-match.{attempt_text}.stderr",
            f"portage-qcheck.before.{attempt_text}.stdout",
            f"portage-qcheck.before.{attempt_text}.stderr",
            f"portage-qcheck.after.{attempt_text}.stdout",
            f"portage-qcheck.after.{attempt_text}.stderr",
            f"emerge.pretend.stdout.{attempt_text}",
            f"emerge.pretend.stderr.{attempt_text}",
            f"emerge.stdout.{attempt_text}",
            f"emerge.stderr.{attempt_text}",
            f"qcheck.stdout.{attempt_text}",
            f"qcheck.stderr.{attempt_text}",
        )
    )
    if not required_ledger_paths.issubset(set(ledger_rows)):
        fail(f"{lane} offline-restore ledger omits a required verifier artifact")
    retry_paths = sorted(
        item for item in ledger_rows if re.fullmatch(r"retry-intent-[0-9]{3}\.json", item.name)
    )
    if [item.name for item in retry_paths] != [
        f"retry-intent-{index:03d}.json" for index in range(1, attempt + 1)
    ]:
        fail(f"{lane} offline-restore retry intents are not contiguous and exhaustive")
    primary_paths = {
        report / relative
        for _name, relative in relative_names
    }
    actual_offline_files: set[Path] = set()
    for entry in offline_root.iterdir():
        if entry.is_symlink() or not entry.is_file():
            fail(f"{lane} offline-restore directory contains a foreign object: {entry}")
        actual_offline_files.add(entry)
    if actual_offline_files != primary_paths | set(ledger_rows):
        fail(f"{lane} offline-restore directory has foreign or unbound files")
    require_path_absent(prepared_selector, f"{lane} prepared selector")
    for residue in selector.parent.glob(
        f".{selector.name}.exchange-preflight-{checkpoint_id}-*"
    ):
        fail(f"{lane} checkpoint retains exchange-preflight residue: {residue}")
    for residue in (
        terminal_path.with_name(f"{terminal_path.name}.partial"),
        activation_receipt_path.with_name(f"{activation_receipt_path.name}.partial"),
        intent_path.with_name(f"{intent_path.name}.partial"),
        paths[receipt_label].with_name(f"{paths[receipt_label].name}.partial"),
    ):
        require_path_absent(residue, f"{lane} checkpoint partial")
    for residue in canonical_path.parent.glob(f"{canonical_path.name}.partial.*"):
        fail(f"{lane} checkpoint retains canonical-state partial residue: {residue}")
    for parent, pattern in (
        (cache_path.parent, f".snapshot-{checkpoint_id}.partial.*"),
        (durable_path.parent, f".critical-{checkpoint_id}.partial.*"),
        (report.parent, f".checkpoint-{checkpoint_id}.partial.*"),
    ):
        for residue in parent.glob(pattern):
            fail(f"{lane} checkpoint retains publication residue: {residue}")
    for residue in report.rglob("*partial*"):
        fail(f"{lane} checkpoint retains nested partial residue: {residue}")
    operator_root = validate_operator_evidence_manifest(
        payloads[manifest_label], paths[manifest_label], checkpoint_id, production
    )
    if production and operator_root != Path(
        f"/var/lib/gentoo-optimization/reports/checkpoint-{checkpoint_id}-operator-evidence"
    ):
        fail(f"{lane} operator evidence is outside the canonical production root")
    operator_metadata = operator_root.lstat()
    if production and (
        (operator_metadata.st_uid, operator_metadata.st_gid) != (0, 0)
        or stat.S_IMODE(operator_metadata.st_mode) != 0o700
    ):
        fail(f"{lane} operator evidence root metadata is not exact")
    bindings = require_ordered_object(
        intent["input_bindings"],
        f"{lane} input bindings",
        ("source", "verifier", "delta", "artifact_preparation"),
    )
    source = require_ordered_object(
        bindings.get("source"), f"{lane} checkpoint source", ("path", "packages_sha256")
    )
    source_path = absolute_path(source.get("path"), f"{lane} source checkpoint")
    if production and (
        source_path.parent
        != Path("/var/lib/gentoo-optimization/recovery/binpkgs")
        or re.fullmatch(r"critical-[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", source_path.name)
        is None
    ):
        fail(f"{lane} source checkpoint is outside the canonical durable root")
    source_packages = source_path / "Packages"
    if production:
        validate_root_trust(source_packages, f"{lane} source Packages")
    source_packages_payload, _source_packages_stat = read_regular(
        source_packages, f"{lane} source Packages"
    )
    if source.get("packages_sha256") != sha256(source_packages_payload):
        fail(f"{lane} source Packages differs from the activation intent")
    verifier = require_ordered_object(
        bindings.get("verifier"), f"{lane} checkpoint verifier", ("path", "sha256")
    )
    expected_verifier = checkpoint_verifier
    if production and expected_verifier != Path(
        f"/var/lib/gentoo-optimization/bootstrap/binpkg-checkpoint-"
        f"{bootstrap['commit']}/verify-binpkg-snapshot.py"
    ):
        fail(f"{lane} checkpoint verifier path is not the canonical Candidate-A bootstrap")
    verifier_path, verifier_payload = read_sha256_reference(
        verifier,
        f"{lane} checkpoint verifier",
        production=production,
        expected_path=expected_verifier,
    )
    prerequisite_verifier = bootstrap["destination"] / "verify-binpkg-snapshot.py"
    prerequisite_verifier_payload, _prerequisite_verifier_stat = read_regular(
        prerequisite_verifier, "jsonschema prerequisite bootstrap verifier"
    )
    if (
        verifier.get("sha256") != sha256(verifier_payload)
        or verifier_payload != prerequisite_verifier_payload
        or stat.S_IMODE(verifier_path.lstat().st_mode) != 0o755
    ):
        fail(f"{lane} checkpoint verifier differs from the immutable bootstrap")
    delta = require_object(
        bindings.get("delta"),
        f"{lane} checkpoint delta",
        {"sorted_cpvs_path", "sorted_cpvs_sha256", "count"},
    )
    delta_path = absolute_path(delta["sorted_cpvs_path"], f"{lane} sorted CPV delta")
    if delta_path.parent != report:
        fail(f"{lane} sorted CPV delta is outside its report")
    if production:
        validate_root_trust(delta_path, f"{lane} sorted CPV delta")
    delta_payload, _delta_stat = read_regular(delta_path, f"{lane} sorted CPV delta")
    try:
        delta_cpvs = delta_payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        fail(f"{lane} sorted CPV delta is not UTF-8: {error}")
    if (
        not delta_cpvs
        or delta_cpvs != sorted(set(delta_cpvs))
        or delta.get("sorted_cpvs_sha256") != sha256(delta_payload)
        or delta.get("count") != len(delta_cpvs)
    ):
        fail(f"{lane} checkpoint sorted CPV delta is not exact")
    artifact = require_ordered_object(
        bindings.get("artifact_preparation"),
        f"{lane} artifact-preparation reference",
        ("path", "sha256", "live_cpvs"),
    )
    artifact_path, artifact_payload = read_sha256_reference(
        artifact,
        f"{lane} artifact-preparation state",
        production=production,
        expected_path=report / "artifact-preparation-state.json",
    )
    artifact_state = require_jq_json_object(
        artifact_payload,
        f"{lane} artifact-preparation state",
        (
            "schema_version",
            "control",
            "checkpoint_id",
            "status",
            "prepared_at",
            "live_cpvs",
            "source",
            "cache_checkpoint",
            "durable_checkpoint",
            "activation_intent",
            "evidence",
            "offline_restoration_tested",
            "pending_total",
            "unknown_total",
            "failed_total",
        ),
    )
    artifact_source = require_ordered_object(
        artifact_state.get("source"),
        f"{lane} artifact source",
        ("path", "packages_sha256", "exact_delta_only", "full_gpkg_payloads_validated"),
    )
    artifact_cache = require_ordered_object(
        artifact_state.get("cache_checkpoint"),
        f"{lane} artifact cache",
        (
            "path",
            "indexed_cpvs",
            "gpkg_archives_validated",
            "image_streams_tested",
            "missing_total",
            "extra_total",
            "archive_failure_total",
            "payload_failure_total",
        ),
    )
    artifact_durable = require_ordered_object(
        artifact_state.get("durable_checkpoint"),
        f"{lane} artifact durable",
        tuple(artifact_cache),
    )
    artifact_activation = require_ordered_object(
        artifact_state.get("activation_intent"),
        f"{lane} artifact activation intent",
        ("selector", "target", "expected_old_identity", "guard"),
    )
    artifact_evidence = require_ordered_object(
        artifact_state.get("evidence"),
        f"{lane} artifact evidence",
        ("directory", "manifest_sha256"),
    )
    clean_generation_fields = {
        "indexed_cpvs": state["live_cpvs"],
        "gpkg_archives_validated": state["live_cpvs"],
        "image_streams_tested": state["live_cpvs"],
        "missing_total": 0,
        "extra_total": 0,
        "archive_failure_total": 0,
        "payload_failure_total": 0,
    }
    if (
        artifact_state.get("schema_version") != 1
        or artifact_state.get("control") != "exact-live-binpkg-checkpoint"
        or artifact_state.get("checkpoint_id") != checkpoint_id
        or artifact_state.get("status")
        != "artifact-generations-verified-final-freeze-pending"
        or artifact_state.get("live_cpvs") != state["live_cpvs"]
        or artifact.get("live_cpvs") != state["live_cpvs"]
        or artifact_source
        != {
            "path": os.fspath(source_path),
            "packages_sha256": source["packages_sha256"],
            "exact_delta_only": True,
            "full_gpkg_payloads_validated": True,
        }
        or artifact_cache != {"path": os.fspath(cache_path), **clean_generation_fields}
        or artifact_durable != {"path": os.fspath(durable_path), **clean_generation_fields}
        or artifact_activation
        != {
            "selector": os.fspath(selector),
            "target": os.fspath(durable_path),
            "expected_old_identity": displaced_identity,
            "guard": "exclusive-lock plus exact pre-rename identity comparison",
        }
        or artifact_evidence.get("directory") != os.fspath(report)
        or artifact_state.get("offline_restoration_tested") is not False
        or artifact_state.get("pending_total") != 1
        or artifact_state.get("unknown_total") != 0
        or artifact_state.get("failed_total") != 0
    ):
        fail(f"{lane} artifact-preparation state is not an exact verified generation")
    creation_manifest = report / "evidence-manifest.sha256"
    creation_payload, _creation_stat = read_regular(
        creation_manifest, f"{lane} creation evidence manifest"
    )
    if artifact_evidence.get("manifest_sha256") != sha256(creation_payload):
        fail(f"{lane} artifact-preparation state does not bind creation evidence")
    creation_rows = validate_sha256_manifest(
        creation_manifest,
        f"{lane} creation evidence manifest",
        roots=(report, source_path, cache_path, durable_path),
        production=production,
        recursive=True,
    )
    activation_rows = validate_sha256_manifest(
        activation_evidence_path,
        f"{lane} activation evidence manifest",
        roots=(report, source_path, cache_path, durable_path),
        production=production,
        recursive=True,
    )
    cache_snapshot = validate_checkpoint_snapshot(
        snapshot=cache_path,
        report=report,
        prefix="cache-final",
        checkpoint_id=checkpoint_id,
        live_cpvs=state["live_cpvs"],
        production=production,
    )
    durable_snapshot = validate_checkpoint_snapshot(
        snapshot=durable_path,
        report=report,
        prefix="durable-final",
        checkpoint_id=checkpoint_id,
        live_cpvs=state["live_cpvs"],
        production=production,
    )
    required_creation_rows = {
        report / "source-packages.sha256",
        report / "tool-identities.tsv",
        cache_snapshot["verification_path"],
        cache_snapshot["packages_manifest_path"],
        cache_snapshot["archives_manifest_path"],
        durable_snapshot["verification_path"],
        durable_snapshot["packages_manifest_path"],
        durable_snapshot["archives_manifest_path"],
    }
    if not required_creation_rows.issubset(set(creation_rows)):
        fail(f"{lane} creation manifest omits final generation evidence")
    if creation_manifest not in activation_rows:
        fail(f"{lane} activation manifest omits the creation-evidence manifest")
    if (
        cache_snapshot["tree"] != durable_snapshot["tree"]
        or cache_snapshot["packages_sha256"] != durable_snapshot["packages_sha256"]
        or cache_snapshot["cpvs"] != durable_snapshot["cpvs"]
    ):
        fail(f"{lane} cache and durable generations differ")
    if restored_cpv not in durable_snapshot["cpvs"]:
        fail(f"{lane} offline restore CPV is absent from the durable generation")
    return {
        "id": checkpoint_id,
        "terminal": terminal_path,
        "canonical": canonical_path,
        "durable": durable_path,
        "cache": cache_path,
        "selector": selector,
        "witness": witness,
        "witness_resolved": witness.resolve(strict=True),
        "source": source_path,
        "delta_cpvs": delta_cpvs,
        "live_cpvs": state["live_cpvs"],
        "operator_root": operator_root,
        "verifier": verifier_path,
        "snapshot_cpvs": durable_snapshot["cpvs"],
        "activated_identity": require_string(
            activation_receipt.get("activated_selector_identity"),
            f"{lane} activated-selector identity",
        ),
        "displaced_identity": displaced_identity,
    }


def validate_prerequisite_object_observation(
    value: object,
    label: str,
    *,
    expected_path: Path | None = None,
) -> dict[str, Any]:
    observation = require_object(value, label)
    path = absolute_path(observation.get("path"), f"{label} path")
    if expected_path is not None and path != expected_path:
        fail(f"{label} path differs from its reviewed authority")
    kind = observation.get("type")
    if kind == "absent":
        if set(observation) != {"path", "type"}:
            fail(f"{label} absent observation carries foreign fields")
        return observation
    base_keys = {
        "path",
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
        "nlink",
        "size",
        "xattrs",
        "type",
    }
    expected_keys = {
        "directory": base_keys,
        "file": base_keys | {"sha256"},
        "symlink": base_keys | {"target"},
    }.get(kind)
    if expected_keys is None or set(observation) != expected_keys:
        fail(f"{label} has an unsupported object-observation schema")
    for key in ("device", "inode", "uid", "gid", "mode", "nlink", "size"):
        require_int(observation.get(key), f"{label} {key}")
    mode = require_int(observation.get("mode"), f"{label} mode")
    if mode > 0o7777:
        fail(f"{label} mode is outside the reviewed permission range")
    xattrs = require_list(observation.get("xattrs"), f"{label} xattrs")
    normalized_xattrs: list[tuple[str, str]] = []
    for item in xattrs:
        row = require_object(item, f"{label} xattr", {"name", "value_hex"})
        name = require_string(row.get("name"), f"{label} xattr name")
        value_hex = require_string(row.get("value_hex"), f"{label} xattr value")
        if re.fullmatch(r"(?:[0-9a-f]{2})*", value_hex) is None:
            fail(f"{label} xattr value is not lowercase hexadecimal")
        normalized_xattrs.append((name, value_hex))
    if normalized_xattrs != sorted(set(normalized_xattrs)):
        fail(f"{label} xattrs are not sorted and unique")
    if kind == "file":
        require_string(observation.get("sha256"), f"{label} file digest", SHA256_RE)
    elif kind == "symlink":
        target = require_string(observation.get("target"), f"{label} symlink target")
        if "\n" in target or "\r" in target:
            fail(f"{label} symlink target is unsafe")
    return observation


def observe_prerequisite_object(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"path": os.fspath(path), "type": "absent"}
    observation: dict[str, Any] = {
        "path": os.fspath(path),
        **durable_file_identity(path),
        "xattrs": [
            {
                "name": name,
                "value_hex": os.getxattr(path, name, follow_symlinks=False).hex(),
            }
            for name in sorted(os.listxattr(path, follow_symlinks=False))
        ],
    }
    if stat.S_ISREG(metadata.st_mode):
        payload, _identity = read_regular(path, "prerequisite installed payload")
        observation.update(type="file", sha256=sha256(payload))
    elif stat.S_ISDIR(metadata.st_mode):
        observation["type"] = "directory"
    elif stat.S_ISLNK(metadata.st_mode):
        observation.update(type="symlink", target=os.readlink(path))
    else:
        fail(f"unsupported prerequisite payload object: {path}")
    return observation


def prerequisite_contents_paths(vdb: Path, cpvs: Sequence[str]) -> list[str]:
    result: set[str] = set()
    for cpv in cpvs:
        contents_path = vdb / cpv / "CONTENTS"
        payload, _metadata = read_regular(contents_path, f"jsonschema {cpv} CONTENTS")
        try:
            lines = payload.decode("utf-8", errors="strict").splitlines()
        except UnicodeError as error:
            fail(f"jsonschema {cpv} CONTENTS is not UTF-8: {error}")
        for line in lines:
            if line.startswith("dir "):
                item = line[4:]
            elif line.startswith("obj "):
                fields = line[4:].rsplit(" ", 2)
                if len(fields) != 3:
                    fail(f"jsonschema {cpv} CONTENTS has a malformed object row")
                item = fields[0]
            elif line.startswith("sym "):
                fields = line[4:].rsplit(" ", 1)
                if len(fields) != 2 or " -> " not in fields[0]:
                    fail(f"jsonschema {cpv} CONTENTS has a malformed symlink row")
                item = fields[0].split(" -> ", 1)[0]
            else:
                fail(f"jsonschema {cpv} CONTENTS has an unsupported row")
            if (
                not item.startswith("/")
                or os.path.normpath(item) != item
                or "\0" in item
            ):
                fail(f"jsonschema {cpv} CONTENTS contains an unsafe path")
            result.add(item)
    return sorted(result)


def validate_payload_manifest(
    value: object,
    *,
    mergeroot: Path,
    cpv: str,
) -> tuple[dict[str, Any], list[Path]]:
    manifest = require_object(
        value,
        f"jsonschema payload manifest {cpv}",
        {"schema_version", "root", "rows", "rows_sha256"},
    )
    rows = require_list(
        manifest.get("rows"), f"jsonschema payload manifest rows {cpv}", nonempty=True
    )
    if (
        manifest.get("schema_version") != 1
        or manifest.get("root") != os.fspath(mergeroot)
        or manifest.get("rows_sha256") != sha256(canonical_json(rows))
    ):
        fail("jsonschema payload manifest is not canonically bound")
    paths: list[str] = []
    has_payload_object = False
    for index, raw_row in enumerate(rows):
        row = require_object(raw_row, f"jsonschema payload row {index}")
        relative = relative_path(row.get("path"), f"jsonschema payload row {index} path")
        destination = Path("/") / relative
        if destination != Path("/usr") and not destination.is_relative_to(Path("/usr")):
            fail(f"jsonschema payload escapes /usr: {cpv}: {relative}")
        kind = row.get("type")
        base_keys = {"path", "uid", "gid", "mode", "xattrs", "type"}
        expected_keys = {
            "directory": base_keys,
            "file": base_keys | {"size", "nlink", "sha256"},
            "symlink": base_keys | {"nlink", "target"},
        }.get(kind)
        if expected_keys is None or set(row) != expected_keys:
            fail(f"jsonschema payload row has a foreign schema: {relative}")
        require_int(row.get("uid"), f"jsonschema payload row {relative} uid")
        require_int(row.get("gid"), f"jsonschema payload row {relative} gid")
        mode = require_int(row.get("mode"), f"jsonschema payload row {relative} mode")
        if mode > 0o7777 or mode & (stat.S_ISUID | stat.S_ISGID):
            fail(f"jsonschema payload row has unsafe mode: {relative}")
        if row.get("xattrs") != []:
            fail(f"jsonschema payload row has unreviewed xattrs: {relative}")
        if kind == "file":
            require_int(row.get("size"), f"jsonschema payload file {relative} size")
            if row.get("nlink") != 1:
                fail(f"jsonschema payload file is hard linked: {relative}")
            require_string(
                row.get("sha256"), f"jsonschema payload file {relative} digest", SHA256_RE
            )
            has_payload_object = True
        elif kind == "symlink":
            require_int(row.get("nlink"), f"jsonschema payload symlink {relative} links", 1)
            target = require_string(
                row.get("target"), f"jsonschema payload symlink {relative} target"
            )
            if "\n" in target or "\r" in target:
                fail(f"jsonschema payload symlink target is unsafe: {relative}")
            has_payload_object = True
        paths.append(relative)
    if paths != sorted(set(paths)) or not has_payload_object:
        fail("jsonschema payload manifest is empty, repeated, or not canonical")
    return manifest, [Path("/") / item for item in paths]


def payload_observation_paths(destinations: Sequence[Path]) -> list[Path]:
    required: set[Path] = set()
    for destination in destinations:
        current = destination
        while True:
            required.add(current)
            if current == Path("/usr"):
                break
            current = current.parent
            if current == Path("/"):
                fail("jsonschema payload observation chain escapes /usr")
    return sorted(required, key=lambda item: (len(item.parts), os.fspath(item)))


def validate_payload_admission_record(
    payload: bytes,
    *,
    path: Path,
    cpv: str,
    transaction_id: str,
    prepared_sha256: str,
    control_session_sha256: str,
    prepared_payload_root: dict[str, Any],
    loader_directories: dict[str, Any],
    private_tmpdir: Path,
    production: bool,
) -> dict[str, Any]:
    record = require_pretty_json_object(
        payload,
        f"jsonschema payload admission record {cpv}",
        {
            "schema",
            "transaction_id",
            "prepared_state_sha256",
            "control_session_sha256",
            "cpv",
            "mergeroot",
            "manifest",
            "manifest_sha256",
            "payload_root_observation",
            "payload_device",
            "preexisting_destinations",
            "preexisting_destinations_sha256",
            "destination_paths",
            "destination_paths_sha256",
        },
    )
    mergeroot = absolute_path(record.get("mergeroot"), "jsonschema payload mergeroot")
    if production and not mergeroot.is_relative_to(private_tmpdir):
        fail("jsonschema payload mergeroot is outside private PORTAGE_TMPDIR")
    manifest, destinations = validate_payload_manifest(
        record.get("manifest"), mergeroot=mergeroot, cpv=cpv
    )
    manifest_rows = {
        Path("/") / str(row["path"]): row
        for row in require_list(manifest["rows"], "jsonschema payload manifest rows")
    }
    destination_names = [os.fspath(item) for item in destinations]
    observations = require_list(
        record.get("preexisting_destinations"),
        f"jsonschema payload preexisting destinations {cpv}",
        nonempty=True,
    )
    required_observations = payload_observation_paths(destinations)
    observed_by_path: dict[Path, dict[str, Any]] = {}
    for raw_observation in observations:
        observation = validate_prerequisite_object_observation(
            raw_observation, f"jsonschema payload observation {cpv}"
        )
        observed_path = Path(str(observation["path"]))
        if observed_path in observed_by_path:
            fail("jsonschema payload observations repeat a path")
        observed_by_path[observed_path] = observation
    if list(observed_by_path) != required_observations:
        fail("jsonschema payload observations are not the exact ordered ancestor closure")
    root_observation = observed_by_path.get(Path("/usr"))
    if root_observation is None or root_observation.get("type") != "directory":
        fail("jsonschema payload lacks exact /usr directory authority")
    payload_device = require_int(record.get("payload_device"), "jsonschema payload device")
    if (
        root_observation.get("device") != payload_device
        or record.get("payload_root_observation") != root_observation
        or root_observation != prepared_payload_root
    ):
        fail("jsonschema payload root differs from the prepared /usr authority")
    destination_set = set(destinations)
    for observed_path in required_observations:
        observation = observed_by_path[observed_path]
        ancestor = any(
            destination != observed_path and destination.is_relative_to(observed_path)
            for destination in destination_set
        )
        if observation.get("type") == "absent":
            if ancestor:
                fail("jsonschema payload has an absent destination ancestor")
            continue
        if observation.get("device") != payload_device:
            fail("jsonschema payload crosses the prepared /usr filesystem")
        if ancestor and observation.get("type") != "directory":
            fail("jsonschema payload destination ancestor is not a directory")
    loader_rows = require_list(
        loader_directories.get("rows"), "jsonschema prepared loader directories"
    )
    if (
        loader_directories.get("schema_version") != 1
        or loader_directories.get("rows_sha256") != sha256(canonical_json(loader_rows))
    ):
        fail("jsonschema prepared loader-directory authority is not canonical")
    loader_roots: set[Path] = set()
    for raw_loader in loader_rows:
        loader = require_object(raw_loader, "jsonschema prepared loader directory")
        loader_roots.add(
            absolute_path(loader.get("path"), "jsonschema prepared loader path")
        )
    for destination in destinations:
        row = manifest_rows[destination]
        observation = observed_by_path[destination]
        if observation.get("type") != "absent":
            if (
                row.get("type") != "directory"
                or observation.get("type") != "directory"
                or any(row.get(key) != observation.get(key) for key in ("uid", "gid", "mode"))
                or observation.get("xattrs") != []
            ):
                fail("jsonschema payload would replace a pre-existing non-directory authority")
        if destination in loader_roots or destination.parent in loader_roots:
            if (
                row.get("type") != "directory"
                or observation.get("type") != "directory"
                or any(row.get(key) != observation.get(key) for key in ("uid", "gid", "mode"))
            ):
                fail("jsonschema payload directly targets a loader directory")
    if production:
        validate_root_trust(Path("/usr"), "jsonschema payload root", directory=True)
        current = Path("/usr").lstat()
        for key, observed in (
            ("device", current.st_dev),
            ("inode", current.st_ino),
            ("uid", current.st_uid),
            ("gid", current.st_gid),
            ("mode", stat.S_IMODE(current.st_mode)),
        ):
            if root_observation.get(key) != observed:
                fail(f"jsonschema payload /usr {key} authority changed")
    expected_path = path.parent / ("payload-admission-" + sha256(cpv.encode()) + ".json")
    if (
        path != expected_path
        or record.get("schema")
        != "gentoo-optimization-jsonschema-payload-admission-v1"
        or record.get("transaction_id") != transaction_id
        or record.get("prepared_state_sha256") != prepared_sha256
        or record.get("control_session_sha256") != control_session_sha256
        or record.get("cpv") != cpv
        or record.get("manifest_sha256") != sha256(canonical_json(manifest))
        or record.get("preexisting_destinations_sha256")
        != sha256(canonical_json(observations))
        or record.get("destination_paths") != destination_names
        or record.get("destination_paths_sha256")
        != sha256(("\n".join(destination_names) + "\n").encode())
    ):
        fail("jsonschema payload admission is not an exact reviewed record")
    return record


def stable_parent_identity(path: Path) -> dict[str, int]:
    metadata = path.lstat()
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def validate_locked_prerequisite_authority(
    value: object,
    *,
    transaction_id: str,
    state_parent: Path,
    production: bool,
) -> dict[str, Any]:
    reference = require_object(
        value,
        "jsonschema locked authority reference",
        {"schema", "path", "sha256", "size", "identity", "parent_identity"},
    )
    path = absolute_path(reference.get("path"), "jsonschema locked authority path")
    expected_path = state_parent / f"jsonschema-prerequisite-{transaction_id}.locked-authority.json"
    if (
        reference.get("schema")
        != "gentoo-optimization-jsonschema-locked-authority-reference-v1"
        or path != expected_path
    ):
        fail("jsonschema locked authority reference is outside its transaction")
    payload, metadata = read_regular(path, "jsonschema locked authority")
    file_identity = {
        key: metadata[key]
        for key in ("device", "inode", "uid", "gid", "mode", "nlink", "size")
    }
    if (
        reference.get("sha256") != sha256(payload)
        or reference.get("size") != len(payload)
        or reference.get("identity") != file_identity
        or reference.get("parent_identity") != stable_parent_identity(path.parent)
        or file_identity["mode"] != 0o600
        or file_identity["nlink"] != 1
    ):
        fail("jsonschema locked authority reference does not match its immutable file")
    if production:
        validate_root_trust(path, "jsonschema locked authority")
        if file_identity["uid"] != 0 or file_identity["gid"] != 0:
            fail("jsonschema locked authority is not root owned")
    document = require_object(
        parse_json_bytes(payload, "jsonschema locked authority"),
        "jsonschema locked authority",
        {"schema", "transaction_id", "initial_locked_window"},
    )
    if canonical_json(document) != payload:
        fail("jsonschema locked authority is not canonical compact JSON")
    window = require_object(
        document.get("initial_locked_window"),
        "jsonschema initial locked window",
        {
            "schema_version",
            "portage_lock_api",
            "vdb",
            "selected_sets",
            "mtimedb",
            "counter",
            "counter_value",
            "payload_root",
            "copies",
            "loader_directories",
            "effective_portage_policy",
            "native_toolchain",
            "plan_metadata",
            "process_exclusion",
        },
    )
    payload_root = validate_prerequisite_object_observation(
        window.get("payload_root"),
        "jsonschema prepared payload root",
        expected_path=Path("/usr") if production else None,
    )
    if (
        document.get("schema") != "gentoo-optimization-jsonschema-locked-authority-v1"
        or document.get("transaction_id") != transaction_id
        or window.get("schema_version") != 1
        or payload_root.get("type") != "directory"
        or any(
            window.get(key) in (None, {}, [])
            for key in (
                "portage_lock_api",
                "vdb",
                "selected_sets",
                "mtimedb",
                "counter",
                "copies",
                "loader_directories",
                "effective_portage_policy",
                "native_toolchain",
                "process_exclusion",
            )
        )
    ):
        fail("jsonschema locked authority lacks a complete prepared observation")
    vdb = require_object(window.get("vdb"), "jsonschema prepared VDB authority")
    cpvs = require_list(vdb.get("cpvs"), "jsonschema prepared VDB CPVs", nonempty=True)
    if (
        cpvs != sorted(set(cpvs))
        or vdb.get("cpvs_sha256")
        != sha256(("\n".join(str(item) for item in cpvs) + "\n").encode())
    ):
        fail("jsonschema prepared VDB CPV authority is not canonical")
    return {"reference": reference, "document": document, "window": window}


def prerequisite_plan_environment(private_roots: dict[str, Path]) -> dict[str, str]:
    environment = {
        "AUTOCLEAN": "no",
        "CCACHE_DIR": os.fspath(private_roots["ccache_dir"]),
        "CARGO_HOME": os.fspath(private_roots["cargo_home"]),
        "DISTDIR": os.fspath(private_roots["distdir_runtime"]),
        "EMERGE_LOG_DIR": os.fspath(private_roots["portage_logdir"]),
        "EPYTHON": "python3.15",
        "FEATURES": (
            "-assume-digests -binpkg-signing -ccache -distcc "
            "-icecream -parallel-install -preserve-libs -unmerge-orphans noinfo "
            "collision-protect protect-owned sandbox userpriv usersandbox "
            "network-sandbox pid-sandbox merge-sync"
        ),
        "FETCHCOMMAND": "/bin/false",
        "GENTOO_MIRRORS": "",
        "HOME": os.fspath(private_roots["home"]),
        "LANG": "C",
        "LC_ALL": "C",
        "LOGNAME": "root",
        "NOCOLOR": "1",
        "PATH": "/usr/lib/llvm/22/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PKGDIR": os.fspath(private_roots["pkgdir"]),
        "PORTAGE_BINHOST": "",
        "PORTAGE_ELOG_SYSTEM": "echo",
        "PORTAGE_LOGDIR": os.fspath(private_roots["portage_logdir"]),
        "PORTAGE_RO_DISTDIRS": os.fspath(private_roots["distdir_authority"]),
        "PORTAGE_TMPDIR": os.fspath(private_roots["portage_tmpdir"]),
        "RESUMECOMMAND": "/bin/false",
        "RUSTUP_HOME": os.fspath(private_roots["rustup_home"]),
        "SHELL": "/bin/bash",
        "TEMP": os.fspath(private_roots["portage_tmpdir"]),
        "TERM": "dumb",
        "TMP": os.fspath(private_roots["portage_tmpdir"]),
        "TMPDIR": os.fspath(private_roots["portage_tmpdir"]),
        "TZ": "UTC",
        "UNINSTALL_IGNORE": "",
        "USER": "root",
        "XDG_CACHE_HOME": os.fspath(private_roots["xdg_cache"]),
    }
    return dict(sorted(environment.items()))


def validate_prerequisite_execution_spec(
    payload: bytes,
    *,
    child: dict[str, Any],
    prepared_path: Path,
    prepared_sha256: str,
    plan_atoms: list[str],
    private_roots: dict[str, Path],
    authority: dict[str, Any],
    tools_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    spec = require_object(
        parse_json_bytes(payload, "jsonschema source execution spec"),
        "jsonschema source execution spec",
        {
            "schema_version",
            "network_isolated",
            "mounts",
            "command",
            "environment",
            "contract_sha256",
        },
    )
    if canonical_json(spec) != payload:
        fail("jsonschema source execution spec is not canonical compact JSON")
    unsigned = dict(spec)
    contract_digest = unsigned.pop("contract_sha256")
    if contract_digest != sha256(canonical_json(unsigned)):
        fail("jsonschema source execution spec contract digest differs")
    expected_environment = prerequisite_plan_environment(private_roots)
    if spec.get("schema_version") != 1 or spec.get("network_isolated") is not True:
        fail("jsonschema source execution spec lacks required containment")
    if spec.get("environment") != expected_environment:
        fail("jsonschema source execution environment differs from the frozen roots")
    command = require_list(spec.get("command"), "jsonschema source command", nonempty=True)
    python_path = require_string(
        tools_by_name.get("python", {}).get("requested_path"),
        "jsonschema source Python path",
    )
    transaction_path = require_string(
        tools_by_name.get("transaction", {}).get("requested_path"),
        "jsonschema source transaction path",
    )
    emerge_path = require_string(
        tools_by_name.get("emerge", {}).get("requested_path"),
        "jsonschema source emerge path",
    )
    emerge_options = [
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
    if len(command) != 12 + len(emerge_options) + len(plan_atoms):
        fail("jsonschema source command length differs from the exact plan")
    control_fd = command[7]
    control_session = command[8]
    if (
        command[:8]
        != [
            python_path,
            "-I",
            "-B",
            transaction_path,
            "__portage-action",
            os.fspath(prepared_path),
            prepared_sha256,
            control_fd,
        ]
        or not isinstance(control_fd, str)
        or re.fullmatch(r"[1-9][0-9]*", control_fd) is None
        or not isinstance(control_session, str)
        or sha256(control_session.encode("ascii", errors="strict"))
        != child.get("control_session_sha256")
        or command[9:] != ["--", emerge_path, *emerge_options, "--ask=y", *plan_atoms]
    ):
        fail("jsonschema source command differs from the exact held-lock action")
    mounts = require_list(spec.get("mounts"), "jsonschema source mount authority", nonempty=True)
    expected_mounts: list[dict[str, object]] = [
        {
            "source": os.fspath(private_roots["etc"]),
            "target": os.fspath(private_roots["live_etc"]),
            "read_only": False,
        },
        {
            "source": os.fspath(private_roots["etc"]),
            "target": os.fspath(private_roots["etc"]),
            "read_only": True,
        },
    ]
    for raw_repository in require_list(
        authority.get("repositories"), "jsonschema frozen repositories", nonempty=True
    ):
        repository = require_object(raw_repository, "jsonschema frozen repository")
        source = os.fspath(
            absolute_path(repository.get("materialized_location"), "repository authority")
        )
        target = os.fspath(absolute_path(repository.get("source_location"), "repository source"))
        expected_mounts.extend(
            [
                {"source": source, "target": source, "read_only": True},
                {"source": source, "target": target, "read_only": True},
            ]
        )
    for key in ("portage_config", "portage_global_config"):
        row = require_object(authority.get(key), f"jsonschema {key}")
        source = os.fspath(absolute_path(row.get("materialized_location"), key))
        target = os.fspath(absolute_path(row.get("mount_target"), key))
        expected_mounts.extend(
            [
                {"source": source, "target": source, "read_only": True},
                {"source": source, "target": target, "read_only": True},
            ]
        )
    for raw_module in require_list(
        authority.get("python_modules"), "jsonschema Python authorities", nonempty=True
    ):
        module = require_object(raw_module, "jsonschema Python authority")
        for raw_root in require_list(
            module.get("roots"), "jsonschema Python authority roots", nonempty=True
        ):
            root = require_object(raw_root, "jsonschema Python authority root")
            root_path = os.fspath(absolute_path(root.get("path"), "Python authority root"))
            expected_mounts.append(
                {"source": root_path, "target": root_path, "read_only": True}
            )
    expected_mounts.extend(
        [
            {
                "source": os.fspath(private_roots["distdir_authority"]),
                "target": os.fspath(private_roots["distdir_authority"]),
                "read_only": True,
            },
            {
                "source": os.fspath(private_roots["live_cache_edb"]),
                "target": os.fspath(private_roots["live_cache_edb_view"]),
                "read_only": True,
            },
            {
                "source": os.fspath(private_roots["var_lib_portage"]),
                "target": os.fspath(private_roots["live_var_lib_portage"]),
                "read_only": False,
            },
            {
                "source": os.fspath(private_roots["var_lib_portage"]),
                "target": os.fspath(private_roots["var_lib_portage"]),
                "read_only": True,
            },
            {
                "source": os.fspath(private_roots["cache_edb"]),
                "target": os.fspath(private_roots["live_cache_edb"]),
                "read_only": False,
            },
            {
                "source": os.fspath(private_roots["cache_edb"]),
                "target": os.fspath(private_roots["cache_edb"]),
                "read_only": True,
            },
            {
                "source": os.fspath(private_roots["thinlto_cache"]),
                "target": os.fspath(private_roots["live_thinlto_cache"]),
                "read_only": False,
            },
            {
                "source": os.fspath(private_roots["thinlto_cache"]),
                "target": os.fspath(private_roots["thinlto_cache"]),
                "read_only": True,
            },
        ]
    )
    if mounts != expected_mounts or len({row["target"] for row in mounts}) != len(mounts):
        fail("jsonschema source mount authority differs from the frozen transaction")
    return spec


def validate_prerequisite_stage_logs(
    value: object,
    *,
    report: Path,
    stage: str,
    production: bool,
) -> dict[str, Any]:
    logs = require_object(
        value,
        f"jsonschema {stage} logs",
        {
            "stage",
            "stdout_path",
            "stdout_sha256",
            "stdout_size",
            "stderr_path",
            "stderr_sha256",
            "stderr_size",
        },
    )
    if logs.get("stage") != stage:
        fail(f"jsonschema {stage} logs name a foreign stage")
    for stream in ("stdout", "stderr"):
        stream_path = absolute_path(
            logs.get(f"{stream}_path"), f"jsonschema {stage} {stream} path"
        )
        expected = report / f"{stage}.{stream}"
        if stream_path != expected:
            fail(f"jsonschema {stage} {stream} path differs")
        if production:
            validate_root_trust(stream_path, f"jsonschema {stage} {stream}")
        stream_payload, _metadata = read_regular(
            stream_path, f"jsonschema {stage} {stream}"
        )
        if (
            logs.get(f"{stream}_sha256") != sha256(stream_payload)
            or logs.get(f"{stream}_size") != len(stream_payload)
        ):
            fail(f"jsonschema {stage} {stream} reference differs")
    return logs


def validate_prerequisite_stage_evidence(
    value: object,
    *,
    report: Path,
    stage: str,
    expected_command: list[str],
    expected_environment: dict[str, Any],
    expected_mounts: list[Any],
    production: bool,
) -> dict[str, Any]:
    evidence = require_object(
        value,
        f"jsonschema {stage} evidence",
        {
            "stage",
            "status",
            "spec_path",
            "spec_sha256",
            "stdout_path",
            "stdout_sha256",
            "stderr_path",
            "stderr_sha256",
        },
    )
    if evidence.get("stage") != stage or evidence.get("status") != 0:
        fail(f"jsonschema {stage} did not complete exactly")
    for name, suffix in (
        ("spec", "execution.json"),
        ("stdout", "stdout"),
        ("stderr", "stderr"),
    ):
        evidence_path, evidence_payload = read_sha256_reference(
            {
                "path": evidence.get(f"{name}_path"),
                "sha256": evidence.get(f"{name}_sha256"),
            },
            f"jsonschema {stage} {name}",
            production=production,
            expected_path=report / f"{stage}.{suffix}",
        )
        if name == "spec":
            spec = require_object(
                parse_json_bytes(evidence_payload, f"jsonschema {stage} execution spec"),
                f"jsonschema {stage} execution spec",
                {
                    "schema_version",
                    "network_isolated",
                    "mounts",
                    "command",
                    "environment",
                    "contract_sha256",
                },
            )
            unsigned = dict(spec)
            contract_sha = unsigned.pop("contract_sha256")
            if (
                canonical_json(spec) != evidence_payload
                or spec.get("schema_version") != 1
                or spec.get("network_isolated") is not True
                or contract_sha != sha256(canonical_json(unsigned))
                or spec.get("command") != expected_command
                or spec.get("environment") != expected_environment
                or spec.get("mounts") != expected_mounts
            ):
                fail(f"jsonschema {stage} execution contract is not exact")
        if evidence_path.parent != report:
            fail(f"jsonschema {stage} evidence escapes its report")
    return evidence


def validate_prerequisite_counter_authority(
    value: object,
    *,
    report: Path,
    transaction_id: str,
    prepared_sha256: str,
    private_roots: dict[str, Path],
    production: bool,
) -> dict[str, Any]:
    counter = require_object(
        value,
        "jsonschema counter reconciliation",
        {
            "outcome",
            "before",
            "private",
            "package_max",
            "after",
            "intent_path",
            "intent_sha256",
            "completion_path",
            "completion_sha256",
            "live_observation",
            "non_counter_manifest_sha256",
            "resealed_read_only",
        },
    )
    before = require_int(counter.get("before"), "jsonschema counter before")
    private = require_int(counter.get("private"), "jsonschema private counter")
    package_max = require_int(counter.get("package_max"), "jsonschema package counter")
    after = require_int(counter.get("after"), "jsonschema counter after")
    if (
        counter.get("outcome") != "success"
        or after != max(before, private, package_max)
        or counter.get("resealed_read_only") is not True
    ):
        fail("jsonschema counter reconciliation is not an exact successful reseal")
    live_observation = validate_prerequisite_object_observation(
        counter.get("live_observation"), "jsonschema reconciled live counter"
    )
    if (
        live_observation.get("type") != "file"
        or live_observation.get("nlink") != 1
        or live_observation.get("xattrs") != []
        or live_observation.get("sha256") != sha256(str(after).encode("ascii"))
    ):
        fail("jsonschema reconciled live counter observation is not exact")
    expected_live_path = private_roots["live_cache_edb_view"] / "counter"
    token = sha256(
        f"{transaction_id}\0{prepared_sha256}\0success".encode("utf-8")
    )
    expected_partial = expected_live_path.parent / f".counter.gentoo-opt.{token}.partial"
    require_string(
        counter.get("non_counter_manifest_sha256"),
        "jsonschema non-counter manifest digest",
        SHA256_RE,
    )
    intent_path, intent_payload = read_sha256_reference(
        {"path": counter.get("intent_path"), "sha256": counter.get("intent_sha256")},
        "jsonschema counter intent",
        production=production,
        expected_path=report / "counter-reconciliation-success.intent.json",
    )
    completion_path, completion_payload = read_sha256_reference(
        {
            "path": counter.get("completion_path"),
            "sha256": counter.get("completion_sha256"),
        },
        "jsonschema counter completion",
        production=production,
        expected_path=report / "counter-reconciliation-success.complete.json",
    )
    intent = require_object(
        parse_json_bytes(intent_payload, "jsonschema counter intent"),
        "jsonschema counter intent",
        {
            "schema",
            "transaction_id",
            "prepared_state_sha256",
            "outcome",
            "live_path",
            "partial_path",
            "before",
            "private",
            "package_max",
            "selected",
            "payload_sha256",
            "live_identity_before",
            "live_xattrs_before",
        },
    )
    completion = require_object(
        parse_json_bytes(completion_payload, "jsonschema counter completion"),
        "jsonschema counter completion",
        {
            "schema",
            "transaction_id",
            "prepared_state_sha256",
            "outcome",
            "intent_path",
            "intent_sha256",
            "after",
            "live_observation",
        },
    )
    if canonical_json(intent) != intent_payload or canonical_json(completion) != completion_payload:
        fail("jsonschema counter evidence is not canonical compact JSON")
    if (
        intent.get("schema") != "gentoo-optimization-jsonschema-counter-intent-v1"
        or intent.get("transaction_id") != transaction_id
        or intent.get("prepared_state_sha256") != prepared_sha256
        or intent.get("outcome") != "success"
        or intent.get("live_path") != os.fspath(expected_live_path)
        or intent.get("partial_path") != os.fspath(expected_partial)
        or intent.get("before") != before
        or intent.get("private") != private
        or intent.get("package_max") != package_max
        or intent.get("selected") != after
        or intent.get("payload_sha256") != sha256(str(after).encode("ascii"))
        or completion.get("schema")
        != "gentoo-optimization-jsonschema-counter-completion-v1"
        or completion.get("transaction_id") != transaction_id
        or completion.get("prepared_state_sha256") != prepared_sha256
        or completion.get("outcome") != "success"
        or completion.get("intent_path") != os.fspath(intent_path)
        or completion.get("intent_sha256") != sha256(intent_payload)
        or completion.get("after") != after
        or completion.get("live_observation") != live_observation
        or completion_path.parent != report
    ):
        fail("jsonschema counter intent/completion chain differs from terminal authority")
    live_identity_before = require_object(
        intent.get("live_identity_before"),
        "jsonschema pre-reconciliation counter identity",
        {"device", "inode", "uid", "gid", "mode", "nlink", "size"},
    )
    for key in live_identity_before:
        require_int(
            live_identity_before.get(key),
            f"jsonschema pre-reconciliation counter {key}",
        )
    if intent.get("live_xattrs_before") != []:
        fail("jsonschema pre-reconciliation counter has unreviewed xattrs")
    require_path_absent(expected_partial, "jsonschema counter reconciliation partial")
    if production:
        current_counter = Path("/var/cache/edb/counter")
        validate_root_trust(current_counter, "current live EDB counter")
        current_payload, current_metadata = read_regular(
            current_counter, "current live EDB counter"
        )
        current_stable = {
            key: current_metadata[key]
            for key in ("device", "inode", "uid", "gid", "mode", "nlink", "size")
        }
        recorded_stable = {
            key: live_observation[key]
            for key in ("device", "inode", "uid", "gid", "mode", "nlink", "size")
        }
        if (
            current_payload != str(after).encode("ascii")
            or current_stable != recorded_stable
        ):
            fail("current live EDB counter differs from terminal reconciliation")
        for residue in current_counter.parent.iterdir():
            if residue.name.startswith((".counter.gentoo-opt.", ".counter.partial.", "counter.partial.")):
                fail(f"current live EDB retains counter publication residue: {residue}")
    return counter


def validate_prerequisite_pkgdir_report(
    payload: bytes,
    *,
    pkgdir: Path,
    evidence_root: Path,
    cpvs: list[str],
    tools_by_name: dict[str, dict[str, Any]],
    production: bool,
) -> dict[str, Any]:
    report = require_object(
        parse_json_bytes(payload, "jsonschema private PKGDIR report"),
        "jsonschema private PKGDIR report",
        {"archives", "counts", "coverage", "inputs", "issues", "schema_version", "status"},
    )
    if canonical_json(report) != payload:
        fail("jsonschema private PKGDIR report is not canonical compact JSON")
    counts = require_object(
        report.get("counts"),
        "jsonschema private PKGDIR counts",
        {
            "errors",
            "extra_indexed_archives",
            "gpkg_archives_found",
            "gpkg_archives_indexed",
            "gpkg_archives_validated",
            "image_tar_zst_streams_tested",
            "indexed_records",
            "indexed_unique_cpvs",
            "indexed_unique_paths",
            "live_cpvs",
            "missing_live_cpvs",
            "unindexed_gpkg_archives",
        },
    )
    coverage = require_object(
        report.get("coverage"),
        "jsonschema private PKGDIR coverage",
        {
            "duplicate_live_cpvs",
            "extra_indexed_archives",
            "missing_live_cpvs",
            "unindexed_gpkg_archives",
        },
    )
    inputs = require_object(
        report.get("inputs"),
        "jsonschema private PKGDIR inputs",
        {
            "allow_extra_archives",
            "packages_index",
            "snapshot",
            "validate_gpkg",
            "vdb",
            "zstd",
        },
    )
    archives = require_list(
        report.get("archives"), "jsonschema private PKGDIR archives", nonempty=True
    )
    expected_count = len(cpvs)
    zstd_path = require_string(
        tools_by_name.get("zstd", {}).get("requested_path"),
        "jsonschema private PKGDIR zstd path",
    )
    selected_vdb = evidence_root / "selected-vdb"
    if (
        report.get("schema_version") != 1
        or report.get("status") != "pass"
        or report.get("issues") != []
        or inputs
        != {
            "allow_extra_archives": False,
            "packages_index": os.fspath(pkgdir / "Packages"),
            "snapshot": os.fspath(pkgdir),
            "validate_gpkg": True,
            "vdb": os.fspath(selected_vdb),
            "zstd": zstd_path,
        }
        or any(
            counts.get(key) != expected_count
            for key in (
                "gpkg_archives_found",
                "gpkg_archives_indexed",
                "gpkg_archives_validated",
                "image_tar_zst_streams_tested",
                "indexed_records",
                "indexed_unique_cpvs",
                "indexed_unique_paths",
                "live_cpvs",
            )
        )
        or any(
            counts.get(key) != 0
            for key in (
                "errors",
                "extra_indexed_archives",
                "missing_live_cpvs",
                "unindexed_gpkg_archives",
            )
        )
        or coverage
        != {
            "duplicate_live_cpvs": {},
            "extra_indexed_archives": [],
            "missing_live_cpvs": [],
            "unindexed_gpkg_archives": [],
        }
        or len(archives) != expected_count
    ):
        fail("jsonschema private PKGDIR report is not an exact full pass")
    packages_payload, _packages_stat = read_regular(
        pkgdir / "Packages", "jsonschema private PKGDIR Packages"
    )
    package_records = parse_packages_records(
        packages_payload, "jsonschema private PKGDIR Packages"
    )
    records_by_cpv = {str(item["cpv"]): item for item in package_records}
    if sorted(records_by_cpv) != cpvs:
        fail("jsonschema private PKGDIR Packages differs from the exact plan")
    observed_cpvs: list[str] = []
    for raw_archive in archives:
        archive = require_object(raw_archive, "jsonschema private PKGDIR archive")
        cpv = require_string(archive.get("cpv"), "jsonschema private PKGDIR archive CPV")
        relative = relative_path(
            archive.get("path"), "jsonschema private PKGDIR archive path"
        )
        archive_path = pkgdir / relative
        archive_payload, _archive_stat = read_regular(
            archive_path, "jsonschema private PKGDIR archive"
        )
        record = records_by_cpv.get(cpv)
        gpkg = require_object(archive.get("gpkg"), "jsonschema private GPKG result")
        size = require_object(archive.get("size"), "jsonschema private archive size")
        md5 = require_object(archive.get("md5"), "jsonschema private archive MD5")
        sha1 = require_object(archive.get("sha1"), "jsonschema private archive SHA-1")
        actual_md5 = hashlib.md5(archive_payload).hexdigest()
        actual_sha1 = hashlib.sha1(archive_payload).hexdigest()
        if (
            record is None
            or record.get("path") != relative
            or record.get("record") != archive.get("record")
            or archive.get("exists") is not True
            or archive.get("regular") is not True
            or size != {"actual": len(archive_payload), "expected": str(len(archive_payload))}
            or md5 != {"actual": actual_md5, "expected": actual_md5}
            or sha1 != {"actual": actual_sha1, "expected": actual_sha1}
            or gpkg.get("status") != "verified"
            or gpkg.get("zstd_streams_tested") != 1
            or gpkg.get("image_tar_zst_streams") != 1
            or require_int(
                gpkg.get("manifest_members_verified"),
                "jsonschema private GPKG verified members",
                1,
            )
            < 1
        ):
            fail("jsonschema private PKGDIR archive differs from its verifier")
        observed_cpvs.append(cpv)
    if observed_cpvs != cpvs:
        fail("jsonschema private PKGDIR verifier archive order differs from the plan")
    selected_cpvs = sorted(
        f"{category.name}/{package.name}"
        for category in selected_vdb.iterdir()
        if category.is_dir() and not category.is_symlink()
        for package in category.iterdir()
        if package.is_dir() and not package.is_symlink()
    )
    if selected_cpvs != cpvs:
        fail("jsonschema private PKGDIR selected VDB differs from the exact plan")
    if production:
        validate_root_trust(pkgdir, "jsonschema private PKGDIR", directory=True)
        validate_root_trust(selected_vdb, "jsonschema private selected VDB", directory=True)
    return report


def validate_prerequisite_success_state(
    payload: bytes,
    path: Path,
    pre: dict[str, Any],
    bootstrap: dict[str, Any],
    production: bool,
) -> dict[str, Any]:
    match = re.fullmatch(
        r"jsonschema-prerequisite-([A-Za-z0-9][A-Za-z0-9_.-]{0,127})\.success\.json",
        path.name,
    )
    if match is None:
        fail("jsonschema prerequisite success path is not the immutable success phase")
    transaction_id = match.group(1)
    canonical_path = path.with_name(f"jsonschema-prerequisite-{transaction_id}.json")
    prepared_path = path.with_name(
        f"jsonschema-prerequisite-{transaction_id}.prepared.json"
    )
    armed_path = path.with_name(f"jsonschema-prerequisite-{transaction_id}.armed.json")
    if production and path != Path(
        f"/var/lib/gentoo-optimization/state/project/"
        f"jsonschema-prerequisite-{transaction_id}.success.json"
    ):
        fail("jsonschema prerequisite success state is outside its canonical production root")
    if production:
        for required, label in (
            (canonical_path, "jsonschema prerequisite canonical state"),
            (prepared_path, "jsonschema prerequisite prepared state"),
            (armed_path, "jsonschema prerequisite armed state"),
        ):
            validate_root_trust(required, label)
    canonical_payload, _canonical_stat = read_regular(
        canonical_path, "jsonschema prerequisite canonical state", allow_hardlinks=True
    )
    if (
        canonical_payload != payload
        or durable_file_identity(path) != durable_file_identity(canonical_path)
        or durable_file_identity(path)["nlink"] != 2
        or durable_file_identity(path)["mode"] != 0o600
    ):
        fail("jsonschema prerequisite canonical and success states are not the exact hardlink pair")
    state_keys = {
        "schema",
        "transaction_id",
        "phase",
        "recorded_at",
        "boot_id",
        "previous_phase",
        "previous_state_sha256",
        "prepared_state_sha256",
        "authority",
        "resolver",
        "plan",
        "private_roots",
        "child",
        "outcome",
        "recovery_contract",
        "evidence",
        "pending_total",
        "unknown_total",
        "failed_total",
    }
    state = require_pretty_json_object(payload, "jsonschema prerequisite success state", state_keys)
    if (
        state.get("schema") != "gentoo-optimization-jsonschema-prerequisite-v1"
        or state.get("transaction_id") != transaction_id
        or state.get("phase") != "success"
        or any(state.get(key) != 0 for key in ("pending_total", "unknown_total", "failed_total"))
    ):
        fail("jsonschema prerequisite is not an exact clean success state")
    prepared_payload, _prepared_stat = read_regular(
        prepared_path, "jsonschema prerequisite prepared state"
    )
    armed_payload, _armed_stat = read_regular(
        armed_path, "jsonschema prerequisite armed state"
    )
    prepared = require_pretty_json_object(
        prepared_payload, "jsonschema prerequisite prepared state", state_keys
    )
    armed = require_pretty_json_object(
        armed_payload, "jsonschema prerequisite armed state", state_keys
    )
    for phase_path, phase_name in (
        (prepared_path, "prepared"),
        (armed_path, "armed"),
    ):
        phase_metadata = phase_path.lstat()
        if (
            stat.S_IMODE(phase_metadata.st_mode) != 0o600
            or phase_metadata.st_nlink != 1
            or (production and (phase_metadata.st_uid, phase_metadata.st_gid) != (0, 0))
        ):
            fail(f"jsonschema prerequisite {phase_name} state metadata is not exact")
    stable_keys = (
        "schema",
        "transaction_id",
        "authority",
        "resolver",
        "plan",
        "private_roots",
        "recovery_contract",
        "evidence",
    )
    if (
        prepared.get("schema") != "gentoo-optimization-jsonschema-prerequisite-v1"
        or prepared.get("phase") != "prepared"
        or prepared.get("transaction_id") != transaction_id
        or prepared.get("previous_phase") is not None
        or prepared.get("previous_state_sha256") is not None
        or prepared.get("prepared_state_sha256") is not None
        or prepared.get("child") is not None
        or prepared.get("outcome") is not None
        or prepared.get("pending_total") != 1
        or prepared.get("unknown_total") != 0
        or prepared.get("failed_total") != 0
        or armed.get("phase") != "armed"
        or armed.get("transaction_id") != transaction_id
        or armed.get("previous_phase") != "prepared"
        or armed.get("previous_state_sha256") != sha256(prepared_payload)
        or armed.get("prepared_state_sha256") != sha256(prepared_payload)
        or not isinstance(armed.get("child"), dict)
        or not isinstance(armed.get("outcome"), dict)
        or armed.get("pending_total") != 1
        or armed.get("unknown_total") != 0
        or armed.get("failed_total") != 0
        or state.get("previous_phase") != "armed"
        or state.get("previous_state_sha256") != sha256(armed_payload)
        or state.get("prepared_state_sha256") != sha256(prepared_payload)
        or state.get("child") != armed.get("child")
        or state.get("boot_id") != armed.get("boot_id")
        or armed.get("boot_id") != prepared.get("boot_id")
        or any(state.get(key) != prepared.get(key) for key in stable_keys)
        or any(armed.get(key) != prepared.get(key) for key in stable_keys)
    ):
        fail("jsonschema prerequisite prepared, armed, and success chain is not exact")
    armed_outcome = require_object(
        armed["outcome"],
        "jsonschema prerequisite armed outcome",
        {"displayed_plan", "displayed_prefix_sha256"},
    )
    displayed_plan = require_object(
        armed_outcome["displayed_plan"], "jsonschema displayed Portage plan"
    )
    require_string(
        armed_outcome["displayed_prefix_sha256"],
        "jsonschema displayed plan prefix digest",
        SHA256_RE,
    )
    child = require_object(
        armed.get("child"),
        "jsonschema prerequisite armed child",
        {
            "boot_id",
            "pid",
            "process_group",
            "session",
            "start_ticks",
            "spec_path",
            "spec_sha256",
            "control_session_sha256",
        },
    )
    child_spec = absolute_path(child.get("spec_path"), "jsonschema child execution spec")
    if (
        child.get("boot_id") != state.get("boot_id")
        or any(
            require_int(child.get(key), f"jsonschema child {key}", minimum=1) < 1
            for key in ("pid", "process_group", "session", "start_ticks")
        )
        or child.get("process_group") != child.get("pid")
        or child.get("session") != child.get("pid")
        or child_spec.name != "source-emerge.execution.json"
        or require_string(child.get("spec_sha256"), "jsonschema child spec digest", SHA256_RE)
        != child.get("spec_sha256")
        or require_string(
            child.get("control_session_sha256"),
            "jsonschema child control-session digest",
            SHA256_RE,
        )
        != child.get("control_session_sha256")
    ):
        fail("jsonschema prerequisite armed child identity is not exact")
    for phase in ("rollback-in-progress", "rolled-back", "recovery-failed"):
        require_path_absent(
            path.with_name(f"jsonschema-prerequisite-{transaction_id}.{phase}.json"),
            f"jsonschema prerequisite foreign {phase} branch",
        )
    recovery_contract = require_object(
        state["recovery_contract"],
        "jsonschema prerequisite recovery contract",
        {
            "claim",
            "whole_host_byte_identity",
            "source_emerge_may_never_be_retried_after_armed",
            "live_edb_counter_is_monotonic_nonrollback_axis",
            "authorities",
        },
    )
    expected_recovery_authorities = [
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
    ]
    if recovery_contract != {
        "claim": "declared-package-manager-authorities-only",
        "whole_host_byte_identity": False,
        "source_emerge_may_never_be_retried_after_armed": True,
        "live_edb_counter_is_monotonic_nonrollback_axis": True,
        "authorities": expected_recovery_authorities,
    }:
        fail("jsonschema prerequisite recovery claim differs from the reviewed bounded claim")
    authority = require_object(
        state["authority"],
        "jsonschema prerequisite authority",
        {
            "tools",
            "python_modules",
            "repositories",
            "portage_config",
            "portage_global_config",
            "pre_dependency_checkpoint",
            "framework",
            "capacity_preflight",
            "build_tool_versions",
            "preparation_attempt",
            "build_execution_scope",
        },
    )
    resolver = require_object(
        state["resolver"],
        "jsonschema prerequisite resolver",
        {
            "target",
            "frozen_repository_observation",
            "locked_authority",
            "portage_build_identity",
            "initial_pretend",
            "exact_repretend_before_prefetch",
            "prefetch",
            "offline_exact_repretend",
            "private_roots_before",
            "private_portage_outputs_before",
            "final_locked_window",
            "plan_metadata",
        },
    )
    for key in (
        "python_modules",
        "repositories",
        "portage_config",
        "portage_global_config",
        "framework",
        "capacity_preflight",
        "build_tool_versions",
        "build_execution_scope",
    ):
        if authority.get(key) in (None, {}, []):
            fail(f"jsonschema prerequisite authority {key} is empty")
    for key in resolver:
        if resolver.get(key) in (None, {}, []):
            fail(f"jsonschema prerequisite resolver {key} is empty")
    checkpoint = require_object(
        authority.get("pre_dependency_checkpoint"),
        "jsonschema prerequisite pre-checkpoint authority",
        {
            "path",
            "sha256",
            "identity",
            "phase_path",
            "phase_sha256",
            "phase_identity",
            "checkpoint_id",
            "status",
        },
    )
    if (
        checkpoint.get("path") != os.fspath(pre["canonical"])
        or checkpoint.get("phase_path") != os.fspath(pre["terminal"])
        or checkpoint.get("sha256") != sha256(pre["terminal"].read_bytes())
        or checkpoint.get("phase_sha256") != sha256(pre["terminal"].read_bytes())
        or checkpoint.get("identity") != durable_file_identity(pre["canonical"])
        or checkpoint.get("phase_identity") != durable_file_identity(pre["terminal"])
        or checkpoint.get("checkpoint_id") != pre["id"]
        or checkpoint.get("status") != "offline-restore-proven"
    ):
        fail("jsonschema prerequisite does not bind the exact pre-checkpoint hardlink pair")
    if resolver.get("target") != "dev-python/jsonschema":
        fail("jsonschema prerequisite resolver target differs from the reviewed package")
    preparation_reference = require_object(
        authority.get("preparation_attempt"),
        "jsonschema preparation-attempt reference",
        {"path", "sha256"},
    )
    preparation_path, preparation_payload = read_sha256_reference(
        preparation_reference,
        "jsonschema preparation attempt",
        production=production,
        expected_path=path.parent
        / f"jsonschema-prerequisite-{transaction_id}.preparation-attempt.json",
    )
    preparation = require_pretty_json_object(
        preparation_payload,
        "jsonschema preparation attempt",
        {
            "schema",
            "transaction_id",
            "recorded_at",
            "boot_id",
            "capacity",
            "pre_dependency_checkpoint",
            "reuse_policy",
            "status",
        },
    )
    if (
        preparation.get("schema")
        != "gentoo-optimization-jsonschema-preparation-attempt-v1"
        or preparation.get("transaction_id") != transaction_id
        or preparation.get("boot_id") != prepared.get("boot_id")
        or preparation.get("capacity") != authority.get("capacity_preflight")
        or preparation.get("pre_dependency_checkpoint") != checkpoint
        or preparation.get("reuse_policy") != "immutable-attempt-never-reuse-id"
        or preparation.get("status")
        != "preparation-started-or-abandoned-until-prepared-is-durable"
        or preparation_path.parent != path.parent
    ):
        fail("jsonschema preparation attempt differs from the durable prepared authority")
    locked = validate_locked_prerequisite_authority(
        resolver.get("locked_authority"),
        transaction_id=transaction_id,
        state_parent=path.parent,
        production=production,
    )
    locked_window = locked["window"]
    locked_vdb = require_object(
        locked_window.get("vdb"), "jsonschema locked pre-mutation VDB"
    )
    if locked_vdb.get("cpvs") != pre["snapshot_cpvs"]:
        fail("jsonschema locked VDB differs from the pre-dependency checkpoint")
    final_window = require_object(
        resolver.get("final_locked_window"),
        "jsonschema final locked window",
        {
            "schema_version",
            "locked_authority_sha256",
            "effective_portage_policy",
            "native_toolchain",
            "plan_metadata_sha256",
        },
    )
    plan_metadata = require_object(
        resolver.get("plan_metadata"),
        "jsonschema plan metadata",
        {"schema_version", "rows", "rows_sha256"},
    )
    plan_metadata_rows = require_list(
        plan_metadata.get("rows"), "jsonschema plan metadata rows", nonempty=True
    )
    if (
        final_window.get("schema_version") != 1
        or final_window.get("locked_authority_sha256")
        != locked["reference"].get("sha256")
        or final_window.get("effective_portage_policy")
        != locked_window.get("effective_portage_policy")
        or final_window.get("native_toolchain") != locked_window.get("native_toolchain")
        or final_window.get("plan_metadata_sha256") != sha256(canonical_json(plan_metadata))
        or plan_metadata.get("schema_version") != 1
        or plan_metadata.get("rows_sha256") != sha256(canonical_json(plan_metadata_rows))
    ):
        fail("jsonschema final locked window is not bound to its immutable authority")
    tools = require_object(
        authority.get("tools"),
        "jsonschema prerequisite tool manifest",
        {"schema_version", "rows", "rows_sha256"},
    )
    tool_rows = require_list(tools.get("rows"), "jsonschema prerequisite tool rows", nonempty=True)
    if (
        tools.get("schema_version") != 1
        or tools.get("rows_sha256") != sha256(canonical_json(tool_rows))
    ):
        fail("jsonschema prerequisite tool manifest is not canonically bound")
    tools_by_name: dict[str, dict[str, Any]] = {}
    observed_tool_names: list[str] = []
    for raw_row in tool_rows:
        row = require_object(
            raw_row,
            "jsonschema prerequisite tool row",
            {
                "name",
                "requested_path",
                "resolved_path",
                "device",
                "inode",
                "uid",
                "gid",
                "mode",
                "nlink",
                "size",
                "sha256",
            },
        )
        name = require_string(row.get("name"), "jsonschema prerequisite tool name")
        if name in tools_by_name:
            fail("jsonschema prerequisite tool manifest repeats a tool")
        requested = absolute_path(
            row.get("requested_path"), f"jsonschema prerequisite {name} requested path"
        )
        resolved = absolute_path(
            row.get("resolved_path"), f"jsonschema prerequisite {name} resolved path"
        )
        require_string(row.get("sha256"), f"jsonschema prerequisite {name} digest", SHA256_RE)
        for key in ("device", "inode", "uid", "gid", "mode", "nlink", "size"):
            require_int(row.get(key), f"jsonschema prerequisite {name} {key}")
        if resolved != requested.resolve(strict=True):
            fail(f"jsonschema prerequisite {name} requested/resolved identity differs")
        resolved_payload, resolved_metadata = read_regular(
            resolved, f"jsonschema prerequisite {name} executable"
        )
        if (
            row.get("sha256") != sha256(resolved_payload)
            or any(row.get(key) != resolved_metadata[key] for key in (
                "device", "inode", "uid", "gid", "mode", "nlink", "size"
            ))
            or not int(row["mode"]) & 0o111
        ):
            fail(f"jsonschema prerequisite {name} executable changed")
        if production:
            validate_root_trusted_entrypoint(requested, f"jsonschema prerequisite {name}")
            validate_root_trust(resolved, f"jsonschema prerequisite {name} resolved executable")
            if row.get("uid") != 0 or row.get("gid") != 0:
                fail(f"jsonschema prerequisite {name} executable is not root owned")
        tools_by_name[name] = row
        observed_tool_names.append(name)
    if observed_tool_names != sorted(set(observed_tool_names)):
        fail("jsonschema prerequisite tool manifest is not sorted and unique")
    if production and set(observed_tool_names) != {
        "bash",
        "cargo",
        "cp",
        "emerge",
        "false",
        "gemato",
        "git",
        "gpg",
        "gpgconf",
        "gpep517",
        "ldconfig",
        "maturin",
        "meson",
        "meson_python",
        "mount",
        "ninja",
        "python",
        "qcheck",
        "rustc",
        "snapshot_verifier",
        "sync",
        "transaction",
        "umount",
        "unshare",
        "wget",
        "zstd",
    }:
        fail("jsonschema prerequisite production tool manifest membership differs")
    expected_tools = {
        "transaction": bootstrap["destination"] / "install-jsonschema-prerequisite.py",
        "snapshot_verifier": bootstrap["destination"] / "verify-binpkg-snapshot.py",
        "python": bootstrap["python"],
    }
    for name, expected_path in expected_tools.items():
        tool_row = tools_by_name.get(name)
        if (
            tool_row is None
            or tool_row.get("requested_path") != os.fspath(expected_path)
            or tool_row.get("resolved_path") != os.fspath(expected_path.resolve(strict=True))
            or tool_row.get("sha256") != sha256(expected_path.read_bytes())
            or tool_row.get("mode") != stat.S_IMODE(expected_path.lstat().st_mode)
        ):
            fail(f"jsonschema prerequisite tool {name} differs from the bootstrap")
    plan = require_object(
        state["plan"],
        "jsonschema prerequisite plan",
        {"schema_version", "ordered_exact_atoms", "rows", "rows_sha256"},
    )
    rows = require_list(plan["rows"], "jsonschema prerequisite plan rows", nonempty=True)
    cpvs: list[str] = []
    exact_atoms: list[str] = []
    for raw_row in rows:
        row = require_object(
            raw_row,
            "jsonschema prerequisite plan row",
            {"cpv", "repository", "exact_atom", "normalized_display"},
        )
        cpv = require_string(row["cpv"], "jsonschema prerequisite CPV")
        repository_name = require_string(row["repository"], "jsonschema prerequisite repository")
        exact_atom = f"={cpv}::{repository_name}"
        if row.get("exact_atom") != exact_atom:
            fail("jsonschema prerequisite exact atom differs from its plan row")
        require_string(row["normalized_display"], "jsonschema prerequisite normalized display")
        cpvs.append(cpv)
        exact_atoms.append(exact_atom)
    ordered_atoms = require_list(
        plan["ordered_exact_atoms"], "jsonschema prerequisite ordered exact atoms"
    )
    if (
        plan.get("schema_version") != 1
        or cpvs != sorted(set(cpvs))
        or len(ordered_atoms) != len(set(ordered_atoms))
        or set(ordered_atoms) != set(exact_atoms)
        or plan.get("rows_sha256") != sha256(canonical_json(rows))
    ):
        fail("jsonschema prerequisite plan is not canonical and exact")
    if displayed_plan != plan:
        fail("jsonschema displayed Portage plan differs from the frozen exact plan")
    metadata_cpvs = [
        require_string(
            require_object(row, "jsonschema plan metadata row").get("cpv"),
            "jsonschema plan metadata CPV",
        )
        for row in plan_metadata_rows
    ]
    if metadata_cpvs != cpvs:
        fail("jsonschema plan metadata does not cover the exact plan")
    private_roots = require_object(
        state["private_roots"],
        "jsonschema prerequisite private roots",
        {
            "pkgdir",
            "distdir_staging",
            "distdir_runtime",
            "portage_tmpdir",
            "portage_logdir",
            "ccache_dir",
            "thinlto_cache",
            "cargo_home",
            "rustup_home",
            "var_lib_portage",
            "cache_edb",
            "etc",
            "home",
            "xdg_cache",
            "live_cache_edb_view",
            "live_var_lib_portage",
            "live_cache_edb",
            "live_etc",
            "live_thinlto_cache",
            "distdir_authority",
        },
    )
    private_paths = {
        key: absolute_path(value, f"jsonschema prerequisite private root {key}")
        for key, value in private_roots.items()
    }
    if production:
        cache_root = Path(
            f"/var/cache/gentoo-optimization/prerequisite-transactions/{transaction_id}"
        )
        expected_private_paths = {
            "pkgdir": cache_root / "pkgdir",
            "distdir_staging": cache_root / "distfiles.staging",
            "distdir_runtime": cache_root / "distfiles.runtime",
            "portage_tmpdir": cache_root / "tmp",
            "portage_logdir": cache_root / "logs",
            "ccache_dir": cache_root / "ccache",
            "thinlto_cache": cache_root / "thinlto-cache",
            "cargo_home": cache_root / "cargo-home",
            "rustup_home": cache_root / "rustup-home",
            "var_lib_portage": cache_root / "var-lib-portage",
            "cache_edb": cache_root / "cache-edb",
            "etc": cache_root / "etc",
            "home": cache_root / "home",
            "xdg_cache": cache_root / "xdg-cache",
            "live_cache_edb_view": cache_root / "live-cache-edb-view",
            "live_var_lib_portage": Path("/var/lib/portage"),
            "live_cache_edb": Path("/var/cache/edb"),
            "live_etc": Path("/etc"),
            "live_thinlto_cache": Path("/var/tmp/thinlto-cache"),
            "distdir_authority": Path(
                f"/var/lib/gentoo-optimization/recovery/prerequisite-authorities/"
                f"{transaction_id}/distfiles"
            ),
        }
        if private_paths != expected_private_paths:
            fail("jsonschema prerequisite private roots differ from production paths")
        authority_root = Path(
            f"/var/lib/gentoo-optimization/recovery/prerequisite-authorities/{transaction_id}"
        )
        for root, label in (
            (authority_root, "jsonschema prerequisite immutable authority"),
            (cache_root, "jsonschema prerequisite private roots"),
        ):
            validate_root_trust(root, label, directory=True)
            root_metadata = root.lstat()
            if (
                (root_metadata.st_uid, root_metadata.st_gid) != (0, 0)
                or stat.S_IMODE(root_metadata.st_mode) != 0o700
            ):
                fail(f"{label} root metadata is not exact")
    outcome = require_object(
        state["outcome"],
        "jsonschema prerequisite success outcome",
        {"source", "delta", "checks", "post_emerge_authority", "child_completion"},
    )
    source_outcome = require_object(
        outcome["source"],
        "jsonschema prerequisite source outcome",
        {
            "stage",
            "stdout_path",
            "stdout_sha256",
            "stdout_size",
            "stderr_path",
            "stderr_sha256",
            "stderr_size",
            "status",
            "spec_path",
            "spec_sha256",
            "counter_reconciliation",
            "postcheck_error",
        },
    )
    success_checks = require_object(
        outcome["checks"], "jsonschema prerequisite success checks"
    )
    if not success_checks:
        fail("jsonschema prerequisite success checks are empty")
    delta = require_object(
        outcome.get("delta"),
        "jsonschema prerequisite VDB delta",
        {
            "added",
            "removed",
            "unexpected_added",
            "planned_not_added",
            "exact_success_delta",
            "rollback_eligible",
        },
    )
    if (
        delta.get("added") != cpvs
        or delta.get("removed") != []
        or delta.get("unexpected_added") != []
        or delta.get("planned_not_added") != []
        or delta.get("exact_success_delta") is not True
        or delta.get("rollback_eligible") is not True
    ):
        fail("jsonschema prerequisite success delta differs from its exact plan")
    post_authority = require_object(
        outcome.get("post_emerge_authority"),
        "jsonschema prerequisite post-emerge authority",
        {"value", "sha256"},
    )
    post_value = require_object(post_authority["value"], "post-emerge authority value")
    required_post_keys = {
        "schema_version",
        "outcome",
        "live_etc_sha256",
        "private_etc",
        "private_cache_edb",
        "private_mtimedb",
        "private_roots",
        "vdb",
        "loader_directories",
        "terminal_durability",
        "rows_sha256",
    }
    if (
        set(post_value) != required_post_keys
        or post_authority.get("sha256") != sha256(canonical_json(post_value))
        or post_value.get("outcome") != "success"
        or post_value.get("vdb") != delta
        or any(
            post_value.get(key) in (None, {}, [])
            for key in (
                "private_etc",
                "private_cache_edb",
                "private_mtimedb",
                "private_roots",
                "terminal_durability",
            )
        )
        or post_value.get("schema_version") != 1
        or not isinstance(post_value.get("loader_directories"), list)
        or require_string(
            post_value.get("live_etc_sha256"),
            "jsonschema post-emerge live /etc digest",
            SHA256_RE,
        )
        != post_value.get("live_etc_sha256")
        or require_string(
            post_value.get("rows_sha256"),
            "jsonschema post-emerge rows digest",
            SHA256_RE,
        )
        != post_value.get("rows_sha256")
    ):
        fail("jsonschema prerequisite post-emerge authority is not canonically bound")
    completion_path, completion_payload = read_sha256_reference(
        outcome.get("child_completion"),
        "jsonschema prerequisite child completion",
        production=production,
    )
    evidence = require_object(
        state["evidence"],
        "jsonschema prerequisite evidence",
        {"directory", "proc_root"},
    )
    report = absolute_path(evidence.get("directory"), "jsonschema prerequisite report")
    if production and report != Path(
        f"/var/lib/gentoo-optimization/reports/jsonschema-prerequisite-{transaction_id}"
    ):
        fail("jsonschema prerequisite report is outside its canonical production root")
    if production and evidence.get("proc_root") != "/proc":
        fail("jsonschema prerequisite process authority is not the production /proc")
    report_metadata = report.lstat()
    if (
        not stat.S_ISDIR(report_metadata.st_mode)
        or (production and (report_metadata.st_uid, report_metadata.st_gid) != (0, 0))
        or (production and stat.S_IMODE(report_metadata.st_mode) != 0o700)
    ):
        fail("jsonschema prerequisite report root metadata is not exact")
    if child_spec != report / "source-emerge.execution.json":
        fail("jsonschema prerequisite child execution spec is outside its report")
    child_spec_payload, _child_spec_stat = read_regular(
        child_spec, "jsonschema prerequisite child execution spec"
    )
    if child.get("spec_sha256") != sha256(child_spec_payload):
        fail("jsonschema prerequisite child execution spec changed")
    source_spec = validate_prerequisite_execution_spec(
        child_spec_payload,
        child=child,
        prepared_path=prepared_path,
        prepared_sha256=sha256(prepared_payload),
        plan_atoms=exact_atoms,
        private_roots=private_paths,
        authority=authority,
        tools_by_name=tools_by_name,
    )
    if completion_path != report / "child-completion.json":
        fail("jsonschema prerequisite child completion is outside its report")
    completion = require_pretty_json_object(
        completion_payload,
        "jsonschema prerequisite child completion",
        {
            "schema",
            "transaction_id",
            "recorded_at",
            "boot_id",
            "prepared_state_sha256",
            "armed_state_sha256",
            "decision_state_sha256",
            "child",
            "control_session_sha256",
            "outcome",
            "source_status",
            "rollback_status",
            "counter",
            "vdb_sha256",
            "logs",
            "checks",
            "payload_admissions",
            "post_emerge_authority",
        },
    )
    if (
        completion.get("schema") != "gentoo-optimization-jsonschema-child-completion-v1"
        or completion.get("transaction_id") != transaction_id
        or completion.get("prepared_state_sha256") != sha256(prepared_payload)
        or completion.get("armed_state_sha256") != sha256(armed_payload)
        or completion.get("decision_state_sha256") != sha256(armed_payload)
        or completion.get("child") != armed.get("child")
        or completion.get("boot_id") != state.get("boot_id")
        or completion.get("control_session_sha256")
        != armed.get("child", {}).get("control_session_sha256")
        or completion.get("outcome") != "success"
        or completion.get("source_status") != 0
        or completion.get("rollback_status") is not None
        or completion.get("post_emerge_authority") != post_authority
        or require_string(
            completion.get("vdb_sha256"),
            "jsonschema completion VDB digest",
            SHA256_RE,
        )
        != completion.get("vdb_sha256")
    ):
        fail("jsonschema prerequisite child completion is not an exact success receipt")
    completion_counter = validate_prerequisite_counter_authority(
        completion.get("counter"),
        report=report,
        transaction_id=transaction_id,
        prepared_sha256=sha256(prepared_payload),
        private_roots=private_paths,
        production=production,
    )
    completion_logs = validate_prerequisite_stage_logs(
        completion.get("logs"),
        report=report,
        stage="source-emerge",
        production=production,
    )
    source_logs = {
        key: source_outcome[key]
        for key in (
            "stage",
            "stdout_path",
            "stdout_sha256",
            "stdout_size",
            "stderr_path",
            "stderr_sha256",
            "stderr_size",
        )
    }
    if (
        source_outcome.get("stage") != "source-emerge"
        or source_outcome.get("status") != 0
        or source_outcome.get("spec_path") != os.fspath(child_spec)
        or source_outcome.get("spec_sha256") != sha256(child_spec_payload)
        or source_outcome.get("counter_reconciliation") != completion_counter
        or source_outcome.get("postcheck_error") is not None
        or source_logs != completion_logs
        or completion.get("checks") != success_checks
    ):
        fail("jsonschema source outcome differs from its exact completion evidence")
    success_checks = require_object(
        success_checks,
        "jsonschema prerequisite success checks",
        {
            "qcheck",
            "private_pkgdir_report",
            "private_pkgdir_report_sha256",
            "payload_authority",
        },
    )
    qcheck_rows = require_list(
        success_checks.get("qcheck"), "jsonschema qcheck evidence", nonempty=True
    )
    if len(qcheck_rows) != len(cpvs):
        fail("jsonschema qcheck evidence does not cover the exact plan")
    for index, row in enumerate(qcheck_rows, 1):
        validate_prerequisite_stage_evidence(
            row,
            report=report,
            stage=f"source-success-qcheck-{index:03d}",
            expected_command=[
                require_string(
                    tools_by_name.get("qcheck", {}).get("requested_path"),
                    "jsonschema qcheck tool path",
                ),
                f"={cpvs[index - 1]}",
            ],
            expected_environment=require_object(
                source_spec.get("environment"), "jsonschema source environment"
            ),
            expected_mounts=require_list(
                source_spec.get("mounts"), "jsonschema source mounts"
            ),
            production=production,
        )
    pkgdir_report_path, pkgdir_report_payload = read_sha256_reference(
        {
            "path": success_checks.get("private_pkgdir_report"),
            "sha256": success_checks.get("private_pkgdir_report_sha256"),
        },
        "jsonschema private PKGDIR report",
        production=production,
        expected_path=report / "source-success-pkgdir-verification.json",
    )
    validate_prerequisite_pkgdir_report(
        pkgdir_report_payload,
        pkgdir=private_paths["pkgdir"],
        evidence_root=report / "source-success-pkgdir-verification",
        cpvs=cpvs,
        tools_by_name=tools_by_name,
        production=production,
    )
    if pkgdir_report_path.parent != report:
        fail("jsonschema private PKGDIR report escapes its transaction report")
    payload_authority = require_object(
        success_checks.get("payload_authority"),
        "jsonschema installed payload authority",
        {"value", "sha256"},
    )
    payload_authority_value = require_object(
        payload_authority.get("value"), "jsonschema installed payload authority value"
    )
    if payload_authority.get("sha256") != sha256(canonical_json(payload_authority_value)):
        fail("jsonschema installed payload authority is not canonically bound")
    admissions = require_list(
        completion["payload_admissions"],
        "jsonschema prerequisite payload admissions",
        nonempty=True,
    )
    admitted_cpvs: list[str] = []
    admission_records: list[dict[str, Any]] = []
    completion_control_sha = require_string(
        completion.get("control_session_sha256"),
        "jsonschema prerequisite completion control-session digest",
        SHA256_RE,
    )
    for raw_admission in admissions:
        admission = require_object(
            raw_admission,
            "jsonschema payload admission reference",
            {
                "cpv",
                "path",
                "sha256",
                "manifest_sha256",
                "preexisting_destinations_sha256",
            },
        )
        cpv = require_string(admission.get("cpv"), "jsonschema payload admission CPV")
        admission_path, admission_payload = read_sha256_reference(
            admission,
            f"jsonschema payload admission {cpv}",
            production=production,
        )
        if admission_path.parent != report:
            fail("jsonschema payload admission is outside its report")
        record = validate_payload_admission_record(
            admission_payload,
            path=admission_path,
            cpv=cpv,
            transaction_id=transaction_id,
            prepared_sha256=sha256(prepared_payload),
            control_session_sha256=completion_control_sha,
            prepared_payload_root=validate_prerequisite_object_observation(
                locked_window.get("payload_root"),
                "jsonschema prepared payload root",
            ),
            loader_directories=require_object(
                locked_window.get("loader_directories"),
                "jsonschema prepared loader directories",
            ),
            private_tmpdir=private_paths["portage_tmpdir"],
            production=production,
        )
        if (
            record.get("manifest_sha256") != admission.get("manifest_sha256")
            or record.get("preexisting_destinations_sha256")
            != admission.get("preexisting_destinations_sha256")
        ):
            fail("jsonschema payload admission reference is incoherent")
        admitted_cpvs.append(cpv)
        admission_records.append(record)
    if sorted(admitted_cpvs) != cpvs or len(admitted_cpvs) != len(set(admitted_cpvs)):
        fail("jsonschema payload admissions do not equal the exact plan")
    expected_per_cpv_paths = {
        str(record["cpv"]): record["destination_paths"]
        for record in sorted(admission_records, key=lambda item: str(item["cpv"]))
    }
    payload_rows = require_list(
        payload_authority_value.get("installed_rows"),
        "jsonschema installed payload rows",
        nonempty=True,
    )
    payload_contents = require_list(
        payload_authority_value.get("contents_paths"),
        "jsonschema installed CONTENTS paths",
        nonempty=True,
    )
    expected_payload_paths = sorted(
        {
            path_name
            for paths_for_cpv in expected_per_cpv_paths.values()
            for path_name in paths_for_cpv
        }
    )
    if (
        set(payload_authority_value)
        != {
            "schema_version",
            "cpvs",
            "payload_device",
            "payload_root_sha256",
            "per_cpv_paths",
            "installed_rows",
            "contents_paths",
            "rows_sha256",
        }
        or payload_authority_value.get("schema_version") != 1
        or payload_authority_value.get("cpvs") != cpvs
        or payload_authority_value.get("payload_device")
        != admission_records[0].get("payload_device")
        or payload_authority_value.get("payload_root_sha256")
        != sha256(canonical_json(admission_records[0]["payload_root_observation"]))
        or payload_authority_value.get("per_cpv_paths") != expected_per_cpv_paths
        or [row.get("path") for row in payload_rows] != expected_payload_paths
        or any(
            set(require_object(row, "jsonschema installed payload row"))
            != {"path", "observation_sha256"}
            or require_string(
                row.get("observation_sha256"),
                "jsonschema installed payload observation digest",
                SHA256_RE,
            )
            != row.get("observation_sha256")
            for row in payload_rows
        )
        or payload_contents != sorted(set(payload_contents))
        or not set(payload_contents).issubset(set(expected_payload_paths))
        or payload_authority_value.get("rows_sha256")
        != sha256(
            canonical_json(
                {
                    key: value
                    for key, value in payload_authority_value.items()
                    if key != "rows_sha256"
                }
            )
        )
    ):
        fail("jsonschema installed payload authority differs from its admissions")
    for residue in path.parent.glob(
        f"jsonschema-prerequisite-{transaction_id}*.partial*"
    ):
        fail(f"jsonschema prerequisite retains state partial residue: {residue}")
    for residue in path.parent.glob(
        f".jsonschema-prerequisite-{transaction_id}*.partial*"
    ):
        fail(f"jsonschema prerequisite retains state partial residue: {residue}")
    if report.is_dir():
        for residue in report.rglob("*partial*"):
            fail(f"jsonschema prerequisite retains report partial residue: {residue}")
        for entry in report.rglob("*"):
            if entry.is_symlink():
                fail(f"jsonschema prerequisite report contains a symlink: {entry}")
    if production:
        authority_root = Path(
            f"/var/lib/gentoo-optimization/recovery/prerequisite-authorities/{transaction_id}"
        )
        cache_root = Path(
            f"/var/cache/gentoo-optimization/prerequisite-transactions/{transaction_id}"
        )
        for root, label in (
            (authority_root, "jsonschema prerequisite immutable authority"),
            (cache_root, "jsonschema prerequisite private roots"),
        ):
            validate_root_trust(root, label, directory=True)
            for residue in root.rglob("*partial*"):
                fail(f"{label} retains publication residue: {residue}")
        for parent, identifier in (
            (authority_root.parent, transaction_id),
            (cache_root.parent, transaction_id),
            (report.parent, f"jsonschema-prerequisite-{transaction_id}"),
        ):
            for residue in parent.glob(f".{identifier}.partial.*"):
                fail(f"jsonschema prerequisite retains root publication residue: {residue}")
    return {"cpvs": cpvs, "canonical": canonical_path, "transaction_id": transaction_id}


def validate_automation_external_semantics(
    payloads: dict[str, bytes],
    paths: dict[str, Path],
    repository: dict[str, object],
    production: bool,
) -> None:
    bootstrap = validate_jsonschema_bootstrap_manifest(
        payloads["jsonschema-bootstrap-manifest"],
        paths["jsonschema-bootstrap-manifest"],
        repository,
        production,
    )
    pre = validate_checkpoint_lane("pre", payloads, paths, bootstrap, production)
    prerequisite = validate_prerequisite_success_state(
        payloads["jsonschema-prerequisite-success-state"],
        paths["jsonschema-prerequisite-success-state"],
        pre,
        bootstrap,
        production,
    )
    post = validate_checkpoint_lane("post", payloads, paths, bootstrap, production)
    if pre["id"] == post["id"]:
        fail("pre- and post-dependency checkpoints reuse one checkpoint ID")
    if post["source"] != pre["durable"]:
        fail("post-dependency checkpoint does not use the pre-checkpoint generation as source")
    if post["witness_resolved"] != pre["durable"]:
        fail("post-dependency checkpoint witness does not preserve the pre-checkpoint generation")
    if post["displaced_identity"] != pre["activated_identity"]:
        fail("post-dependency witness is not the exact activated pre-checkpoint selector")
    if post["delta_cpvs"] != prerequisite["cpvs"]:
        fail("post-dependency checkpoint delta differs from the prerequisite plan")
    if (
        sorted(set(post["snapshot_cpvs"]) - set(pre["snapshot_cpvs"]))
        != prerequisite["cpvs"]
        or not set(pre["snapshot_cpvs"]).issubset(set(post["snapshot_cpvs"]))
        or not set(prerequisite["cpvs"]).isdisjoint(set(pre["snapshot_cpvs"]))
    ):
        fail("post-dependency generation membership differs from the prerequisite delta")
    if post["live_cpvs"] != pre["live_cpvs"] + len(prerequisite["cpvs"]):
        fail("post-dependency checkpoint CPV count differs from the admitted prerequisite closure")
    if not post["selector"].is_symlink() or post["selector"].resolve(strict=True) != post["durable"]:
        fail("current checkpoint selector does not name the post-dependency generation")
    post_operator = post["operator_root"]
    expected_added = ("\n".join(prerequisite["cpvs"]) + "\n").encode("utf-8")
    expected_atoms = (
        "\n".join(f"={cpv}" for cpv in prerequisite["cpvs"]) + "\n"
    ).encode("utf-8")
    for name, expected in (
        ("jsonschema-prerequisite-added-cpvs.txt", expected_added),
        ("delta-atoms.txt", expected_atoms),
        ("expected-delta-atoms.txt", expected_atoms),
    ):
        evidence_path = post_operator / name
        if production:
            validate_root_trust(evidence_path, f"post-checkpoint operator {name}")
        observed, _metadata = read_regular(evidence_path, f"post-checkpoint operator {name}")
        if observed != expected:
            fail(f"post-checkpoint operator {name} differs from the prerequisite plan")
    state_digest_path = post_operator / "jsonschema-prerequisite-state.sha256"
    state_digest_payload, _state_digest_stat = read_regular(
        state_digest_path, "post-checkpoint prerequisite state digest"
    )
    expected_digest_line = (
        f"{sha256(payloads['jsonschema-prerequisite-success-state'])}  "
        f"{prerequisite['canonical']}\n"
    ).encode("utf-8")
    if state_digest_payload != expected_digest_line:
        fail("post-checkpoint operator evidence does not bind the prerequisite success state")


def validate_component_external_semantics(
    component_name: str,
    run_id: str,
    payloads: dict[str, bytes],
    paths: dict[str, Path],
    evidence_root: Path,
    repository: dict[str, object],
    expected_boot_id: str,
    production: bool,
) -> None:
    if component_name == "automation":
        validate_automation_external_semantics(
            payloads,
            paths,
            repository,
            production,
        )
        return
    if component_name == "framework-installer":
        payload = payloads["framework-install-manifest"]
        try:
            lines = payload.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            fail(f"framework install manifest is not UTF-8: {error}")
        fields: dict[str, str] = {}
        for line in lines:
            if "\t" in line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if not key or key in fields:
                fail("framework install manifest has duplicate assignment fields")
            fields[key] = value
        if (
            fields.get("schema") != "gentoo-optimization-framework-install-v4"
            or fields.get("git_commit") != repository["commit"]
            or fields.get("git_worktree") != "clean"
            or SHA256_RE.fullmatch(fields.get("source_aggregate_sha256", "")) is None
            or SHA256_RE.fullmatch(fields.get("framework_aggregate_sha256", "")) is None
        ):
            fail(
                "framework install manifest does not prove the current clean "
                "repository candidate"
            )
        return
    if component_name != "sample-pgo":
        return
    token_payload = payloads["production-token-scan"]
    if token_payload != b"passed\t-\n":
        fail("production token scan is not the exact terminal pass receipt")
    publication_payload = payloads["production-publication-context"]
    try:
        publication_lines = publication_payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        fail(f"production publication context is not UTF-8: {error}")
    publication: dict[str, str] = {}
    for line in publication_lines:
        publication_fields = line.split("\t")
        if (
            len(publication_fields) != 2
            or not publication_fields[0]
            or publication_fields[0] in publication
        ):
            fail("production publication context has an invalid or duplicate row")
        publication[publication_fields[0]] = publication_fields[1]
    expected_publication_fields = {
        "authoritative_work_final_identity",
        "authoritative_work_root",
        "generation_state_root",
        "portage_policy_mode",
        "production_work_root",
        "profile_artifact_root",
        "published_copy",
        "published_copy_semantics",
    }
    if (
        set(publication) != expected_publication_fields
        or publication.get("portage_policy_mode") != "live"
        or publication.get("authoritative_work_final_identity")
        != "root:portage:0750"
        or not publication.get("authoritative_work_root", "").startswith("/")
        or not publication.get("production_work_root", "").startswith("/")
        or not publication.get("profile_artifact_root", "").startswith("/")
        or not publication.get("generation_state_root", "").startswith("/")
    ):
        fail("production publication context does not prove the live policy lane")
    receipt_payload = payloads["production-transaction-receipt"]
    receipt = require_object(
        parse_json_bytes(
            receipt_payload,
            "production transaction receipt",
        ),
        "production transaction receipt",
        {
            "abandoned_receipt_partial",
            "authorization",
            "authorization_token_sha256",
            "boot_id",
            "child_exit_status",
            "child_identity_sha256",
            "completed_at",
            "framework_context",
            "gate_run_id",
            "generation",
            "journal_removal_after_receipt_required",
            "lock_payload_restored_sha256",
            "locks",
            "schema",
            "status",
            "token_scan",
            "transaction_journal",
            "transaction_journal_sha256",
        },
    )
    # The production coordinator's durable receipt contract is sorted,
    # two-space-indented JSON with one trailing newline (not this verifier's
    # compact marker encoding).
    if pretty_json(receipt) != receipt_payload:
        fail("production transaction receipt is not canonical JSON")
    coordinator_validate_receipt(repository, receipt)
    child_payload = payloads["production-child-identity"]
    child_identity = require_object(
        parse_json_bytes(child_payload, "production child identity"),
        "production child identity",
        {
            "authorization_token_sha256",
            "boot_id",
            "child",
            "coordinator_owner",
            "created_at",
            "framework_context",
            "gate_run_id",
            "generation",
            "journal_sha256",
            "schema",
            "test_mode",
        },
    )
    if pretty_json(child_identity) != child_payload:
        fail("production child identity is not canonical coordinator JSON")
    token_scan = require_object(
        receipt["token_scan"],
        "receipt token scan",
        {
            "output",
            "output_sha256",
            "roots",
            "scanner_executable_sha256",
            "scanner_status",
        },
    )
    generation = require_object(
        receipt["generation"],
        "receipt generation",
        {"generation_id", "inventory_id", "inventory_sha256"},
    )
    journal = require_object(receipt["transaction_journal"], "embedded transaction journal")
    child_process = require_object(
        child_identity["child"],
        "production child process identity",
        {"pid", "process_group", "start_ticks"},
    )
    child_owner = require_object(
        child_identity["coordinator_owner"],
        "production child coordinator owner",
        {"pid", "start_ticks"},
    )
    journal_owner = require_object(
        journal.get("owner"),
        "embedded journal owner",
        {"pid", "start_ticks"},
    )
    child_pid = require_int(
        child_process["pid"], "production child PID", minimum=2
    )
    child_process_group = require_int(
        child_process["process_group"],
        "production child process group",
        minimum=2,
    )
    child_start_ticks = require_string(
        child_process["start_ticks"], "production child start ticks"
    )
    owner_pid = require_int(child_owner["pid"], "coordinator owner PID", minimum=1)
    owner_start_ticks = require_string(
        child_owner["start_ticks"], "coordinator owner start ticks"
    )
    if (
        child_process_group != child_pid
        or not child_start_ticks.isdigit()
        or not owner_start_ticks.isdigit()
        or child_owner != journal_owner
        or owner_pid != journal_owner.get("pid")
    ):
        fail("production child process identities differ from the journal")
    journal_generation = require_object(
        journal.get("generation"),
        "embedded journal generation",
        {"generation_id", "inventory_id", "inventory_sha256"},
    )
    for key in ("generation_id", "inventory_id"):
        require_string(generation[key], f"receipt generation {key}", SAFE_ID_RE)
    validation_input_payload = payloads["production-validation-input"]
    expected_validation_input = (
        f'{{"git_commit":"{repository["commit"]}",'
        f'"inventory_id":"{generation["inventory_id"]}",'
        '"purpose":"phase2-sample-pgo-validation-only"}\n'
    ).encode("ascii")
    expected_validation_input_path = (
        Path("/var/lib/gentoo-optimization/state/project")
        / f"{generation['inventory_id']}.json"
    )
    if (
        paths["production-validation-input"] != expected_validation_input_path
        or validation_input_payload != expected_validation_input
        or sha256(validation_input_payload) != generation["inventory_sha256"]
    ):
        fail("production validation inventory identity is not exact")
    require_string(
        receipt["gate_run_id"], "receipt gate_run_id", SAFE_ID_RE
    )
    require_string(
        receipt["authorization_token_sha256"],
        "receipt authorization token digest",
        SHA256_RE,
    )
    require_string(
        receipt["child_identity_sha256"],
        "receipt child identity digest",
        SHA256_RE,
    )
    require_string(
        receipt["transaction_journal_sha256"],
        "receipt transaction journal digest",
        SHA256_RE,
    )
    require_string(
        token_scan["scanner_executable_sha256"],
        "receipt token scanner executable digest",
        SHA256_RE,
    )
    token_roots = [
        absolute_path(value, "receipt token scan root")
        for value in require_list(token_scan["roots"], "receipt token scan roots")
    ]
    token_output = absolute_path(token_scan["output"], "receipt token scan output")
    expected_work = Path("/var/tmp/gentoo-optimization") / (
        f"phase2-sample-work-{receipt['gate_run_id']}"
    )
    expected_profile = Path("/var/cache/gentoo-optimization/pgo/clang-sample") / (
        f"phase2-sample-gate-{receipt['gate_run_id']}"
    )
    expected_state = (
        Path("/var/lib/gentoo-optimization/generations")
        / str(generation["generation_id"])
        / f"phase2-sample-gate-{receipt['gate_run_id']}"
    )
    expected_output_root = evidence_root / "production-sample-pgo"
    if (
        receipt["schema"]
        != "gentoo-optimization-production-profile-lock-receipt-v1"
        or receipt["status"] != "passed"
        or receipt["child_exit_status"] != 0
        or receipt["journal_removal_after_receipt_required"] is not True
        or receipt["abandoned_receipt_partial"] is not None
        or receipt["boot_id"] != expected_boot_id
        or receipt["gate_run_id"] != run_id
        or receipt["child_identity_sha256"] != sha256(child_payload)
        or child_identity["schema"]
        != "gentoo-optimization-production-profile-lock-child-identity-v1"
        or child_identity["test_mode"] is not False
        or child_identity["boot_id"] != receipt["boot_id"]
        or child_identity["generation"] != generation
        or child_identity["gate_run_id"] != receipt["gate_run_id"]
        or child_identity["framework_context"] != receipt["framework_context"]
        or child_identity["authorization_token_sha256"]
        != receipt["authorization_token_sha256"]
        or child_identity["journal_sha256"]
        != receipt["transaction_journal_sha256"]
        or receipt["lock_payload_restored_sha256"] != sha256(b"")
        or journal_generation != generation
        or journal.get("schema")
        != "gentoo-optimization-production-profile-lock-transaction-v1"
        or journal.get("test_mode") is not False
        or journal.get("boot_id") != receipt["boot_id"]
        or journal.get("gate_run_id") != receipt["gate_run_id"]
        or receipt["transaction_journal_sha256"] != sha256(pretty_json(journal))
        or not isinstance(receipt["authorization"], dict)
        or receipt["authorization"].get("abandoned_partial") is not None
        or token_scan.get("scanner_status") != 0
        or token_output != paths["production-token-scan"]
        or token_scan.get("output_sha256") != sha256(token_payload)
        or len(token_roots) != 4
        or len(set(token_roots)) != 4
        or token_roots
        != [expected_work, expected_profile, expected_state, expected_output_root]
        or token_output != expected_state / "coordinator-token-scan.tsv"
        or SHA256_RE.fullmatch(str(generation.get("inventory_sha256", ""))) is None
        or paths["production-child-identity"]
        != expected_output_root / "transaction-child-identity.json"
        or paths["production-publication-context"]
        != expected_output_root / "publication-context.tsv"
    ):
        fail("production transaction receipt is not a consistent terminal pass")
    child_contract = require_object(
        journal.get("child_contract"),
        "embedded journal child contract",
        {
            "argv",
            "containment",
            "containment_executable",
            "environment",
            "environment_sha256",
            "evidence_output_root",
            "executable",
            "token_scan",
        },
    )
    recorded_child_path, child_executable = validate_live_executable_identity(
        child_contract["executable"], "embedded child executable", production
    )
    expected_child_path = (
        Path(str(repository["root"]))
        / "tests/optimization/test-portage-sample-pgo-integration.sh"
    )
    expected_argv = [
        os.fspath(expected_child_path),
        "--production-locks",
        "--portage-policy",
        "live",
        "--output-dir",
        os.fspath(expected_output_root),
    ]
    environment = require_object(
        child_contract["environment"], "embedded child environment"
    )
    expected_environment = {
        "HOME": "/root",
        "LANG": "C",
        "LC_ALL": "C",
        "LOGNAME": "root",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "SHELL": "/bin/bash",
        "TZ": "UTC",
        "USER": "root",
        "GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID": generation["generation_id"],
        "GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID": generation["inventory_id"],
        "GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256": generation[
            "inventory_sha256"
        ],
        "GENTOO_OPT_PRODUCTION_GATE_RUN_ID": receipt["gate_run_id"],
        "GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT": os.fspath(expected_work),
        "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION": os.fspath(
            expected_state / "transaction.authorization"
        ),
        "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN_SHA256": receipt[
            "authorization_token_sha256"
        ],
    }
    child_token_scan = require_object(
        child_contract["token_scan"],
        "embedded child token scan",
        {"executable", "output", "roots"},
    )
    child_scanner = require_object(
        child_token_scan["executable"], "embedded token scanner executable"
    )
    scanner_path, scanner_identity = validate_live_executable_identity(
        child_scanner, "embedded token scanner executable", production
    )
    containment_path, _containment_identity = validate_live_executable_identity(
        child_contract["containment_executable"],
        "embedded PID namespace executable",
        production,
    )
    if (
        child_contract["containment"] != "pid-namespace-v1"
        or (production and containment_path != Path("/usr/bin/unshare"))
        or child_contract["evidence_output_root"] != os.fspath(expected_output_root)
        or recorded_child_path != expected_child_path
        or child_contract["argv"] != expected_argv
        or child_contract["environment_sha256"] != sha256(pretty_json(environment))
        or environment != expected_environment
        or child_token_scan["output"] != os.fspath(token_output)
        or child_token_scan["roots"] != [os.fspath(path) for path in token_roots]
        or scanner_identity["sha256"] != token_scan["scanner_executable_sha256"]
        or (
            production
            and scanner_path
            != Path(
                "/usr/local/libexec/gentoo-optimization/pgo/authorization-token-scan.py"
            )
        )
    ):
        fail("production receipt child contract differs from the exact live gate")
    framework_context = require_object(
        receipt["framework_context"], "receipt framework context"
    )
    if framework_context.get("git_commit") != repository["commit"]:
        fail("production receipt framework belongs to another source commit")
    authorization = require_object(receipt["authorization"], "receipt authorization")
    authorization_path = absolute_path(
        authorization.get("path"), "receipt authorization path"
    )
    if authorization_path != expected_state / "transaction.authorization":
        fail("production authorization path differs from the derived gate state")
    if production:
        expected_receipt_path = Path(
            "/var/lib/gentoo-optimization/state/profile-transactions"
        ) / (
            "phase-2-production-profile-locks-"
            f"{generation['generation_id']}.receipt.json"
        )
        if paths["production-transaction-receipt"] != expected_receipt_path:
            fail("production transaction receipt path is not authoritative")
        current_link = Path("/var/lib/gentoo-optimization/framework-current")
        try:
            current_target = current_link.resolve(strict=True)
        except OSError as error:
            fail(f"cannot resolve active framework target: {error}")
        target = absolute_path(framework_context.get("target"), "framework target")
        manifest_path = absolute_path(
            framework_context.get("manifest_path"), "framework manifest path"
        )
        if current_target != target or manifest_path != target / "install.manifest":
            fail("receipt framework context is not the active complete candidate")
        validate_root_trust(target, "active framework target", directory=True)
        validate_root_trust(manifest_path, "active framework manifest")
        manifest_payload, _manifest_stat = read_regular(
            manifest_path, "active framework manifest"
        )
        if sha256(manifest_payload) != framework_context.get("manifest_sha256"):
            fail("active framework manifest digest differs from the receipt")
        manifest_fields: dict[str, str] = {}
        for line in manifest_payload.decode("utf-8").splitlines():
            if "\t" in line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if not key or key in manifest_fields:
                fail("active framework manifest has duplicate assignment fields")
            manifest_fields[key] = value
        if (
            manifest_fields.get("git_commit") != repository["commit"]
            or manifest_fields.get("source_aggregate_sha256")
            != framework_context.get("source_aggregate_sha256")
            or manifest_fields.get("framework_aggregate_sha256")
            != framework_context.get("framework_aggregate_sha256")
        ):
            fail("active framework manifest differs from the receipt context")
        validate_root_trust(authorization_path, "production authorization")
        generation_parent = expected_state.parent
        validate_root_trust(
            generation_parent,
            "production authorization generation parent",
            directory=True,
        )
        generation_parent_stat = generation_parent.lstat()
        if (
            authorization.get("gate_directory_created") is not True
            or authorization.get("generation_parent")
            != os.fspath(generation_parent)
            or authorization.get("generation_parent_uid")
            != generation_parent_stat.st_uid
            or authorization.get("generation_parent_gid")
            != generation_parent_stat.st_gid
            or authorization.get("generation_parent_mode")
            != stat.S_IMODE(generation_parent_stat.st_mode)
            or generation_parent_stat.st_uid != 0
            or generation_parent_stat.st_gid != 0
            or stat.S_IMODE(generation_parent_stat.st_mode) != 0o755
        ):
            fail("production authorization parent metadata differs from the receipt")
        authorization_payload, _authorization_stat = read_regular(
            authorization_path, "production authorization"
        )
        expected_authorization_payload = "".join(
            f"{key}\t{value}\n"
            for key, value in (
                (
                    "schema",
                    "gentoo-optimization-production-profile-authorization-v1",
                ),
                ("generation_id", generation["generation_id"]),
                (
                    "expected_payload_sha256",
                    journal.get("expected_payload_sha256"),
                ),
                ("journal_sha256", receipt["transaction_journal_sha256"]),
                (
                    "framework_aggregate_sha256",
                    framework_context.get("framework_aggregate_sha256"),
                ),
                (
                    "authorization_token_sha256",
                    receipt["authorization_token_sha256"],
                ),
                ("child_identity_sha256", receipt["child_identity_sha256"]),
            )
        ).encode("ascii")
        if (
            authorization_payload != expected_authorization_payload
            or sha256(authorization_payload) != authorization.get("sha256")
        ):
            fail("production authorization bytes differ from the receipt")
        transaction_journal = Path(
            "/var/lib/gentoo-optimization/state/profile-transactions/"
            "phase-2-production-profile-locks.pending"
        )
        require_terminal_transaction_markers_absent(
            expected_receipt_path,
            authorization_path,
            transaction_journal,
        )
        expected_lock_paths = {
            "framework": Path("/run/gentoo-optimization/framework-install.lock"),
            "generation": Path("/run/gentoo-optimization/generation.lock"),
            "project": Path("/run/gentoo-optimization/project.lock"),
        }
        journal_paths = require_object(journal.get("paths"), "journal lock paths")
        receipt_locks = require_object(receipt["locks"], "receipt lock identities")
        journal_locks = require_object(journal.get("locks"), "journal lock identities")
        for lock_name, expected_lock_path in expected_lock_paths.items():
            if journal_paths.get(lock_name) != os.fspath(expected_lock_path):
                fail("production receipt names a nonstandard stable lock path")
            validate_root_trust(expected_lock_path, f"{lock_name} stable lock")
            lock_payload, lock_stat = read_regular(
                expected_lock_path, f"{lock_name} stable lock"
            )
            live_identity = {
                key: lock_stat[key]
                for key in ("device", "gid", "inode", "mode", "nlink", "uid")
            }
            if (
                lock_payload
                or journal_locks.get(lock_name) != live_identity
                or receipt_locks.get(lock_name) != live_identity
            ):
                fail("stable lock bytes or identities differ from the receipt")
    if (
        publication["authoritative_work_root"] != os.fspath(expected_work)
        or publication["production_work_root"] != os.fspath(expected_work)
        or publication["profile_artifact_root"] != os.fspath(expected_profile)
        or publication["generation_state_root"] != os.fspath(expected_state)
    ):
        fail("production publication roots differ from the coordinator contract")
    if (
        publication.get("published_copy") != os.fspath(expected_output_root)
        or publication.get("published_copy_semantics")
        != "historical-byte-evidence; validator sidecars remain bound to authoritative paths"
    ):
        fail("production publication copy contract is not exact")
    if production:
        validate_root_trust(expected_work, "authoritative sample work", directory=True)
        work_stat = expected_work.lstat()
        try:
            portage_gid = grp.getgrnam("portage").gr_gid
        except KeyError:
            fail("production host has no portage group for evidence ownership")
        if (
            not stat.S_ISDIR(work_stat.st_mode)
            or work_stat.st_uid != 0
            or work_stat.st_gid != portage_gid
            or stat.S_IMODE(work_stat.st_mode) != 0o750
        ):
            fail("authoritative sample work root metadata differs from root:portage 0750")


def component_document(
    specification: dict[str, object],
    run_id: str,
    repository: dict[str, object],
    provenance_path: Path,
    results_path: Path,
    subtests_path: Path,
    summary_path: Path,
    rows: dict[str, dict[str, str]],
    evidence_root: Path,
    external_paths: dict[str, Path],
    expected_boot_id: str,
    production: bool,
) -> dict[str, object]:
    name = str(specification["name"])
    expected_labels = [
        require_string(value, f"component {name} external evidence label", NAME_RE)
        for value in require_list(
            specification["external_evidence_labels"],
            f"component {name} external evidence labels",
        )
    ]
    if sorted(external_paths) != expected_labels:
        fail(
            f"component {name} external evidence labels must exactly equal "
            f"{expected_labels!r}"
        )
    test_rows: list[dict[str, object]] = []
    for test_name in selected_component_test_names(specification, rows):
        row = rows[test_name]
        log_tokens = [
            token.removeprefix("log=")
            for token in row["detail"].split()
            if token.startswith("log=")
        ]
        if len(log_tokens) != 1:
            fail(f"PASS test {test_name} does not identify exactly one log path")
        log_path = absolute_path(log_tokens[0], f"PASS test {test_name} log")
        if evidence_root not in log_path.parents:
            fail(f"PASS test {test_name} log is outside the retained evidence root")
        if production:
            validate_root_trust(log_path, f"PASS test {test_name} log")
        test_rows.append(
            {
                "name": test_name,
                "status": "PASS",
                "detail": row["detail"],
                "log": file_identity(log_path, f"PASS test {test_name} log"),
            }
        )
    external: list[dict[str, object]] = []
    external_payloads: dict[str, bytes] = {}
    seen_external_inodes: set[tuple[int, int]] = set()
    for evidence_label in expected_labels:
        path = external_paths[str(evidence_label)]
        if production:
            validate_root_trust(path, f"component {name} external evidence {evidence_label}")
        payload, external_stat = read_regular(
            path,
            f"component {name} external evidence {evidence_label}",
            allow_hardlinks=(
                evidence_label.endswith("-checkpoint-terminal-state")
                or evidence_label == "jsonschema-prerequisite-success-state"
            ),
        )
        identity = {
            "path": os.fspath(path),
            "sha256": sha256(payload),
            "stat": external_stat,
        }
        inode_key = (external_stat["device"], external_stat["inode"])
        if inode_key in seen_external_inodes:
            fail(f"component {name} external evidence files must have distinct inodes")
        seen_external_inodes.add(inode_key)
        external_payloads[str(evidence_label)] = payload
        external.append({"label": evidence_label, "identity": identity})
    validate_component_external_semantics(
        name,
        run_id,
        external_payloads,
        external_paths,
        evidence_root,
        repository,
        expected_boot_id,
        production,
    )
    return {
        "schema": COMPONENT_SCHEMA,
        "component": name,
        "run_id": run_id,
        "status": "validated",
        "repository": repository,
        "test_run_provenance": file_identity(
            provenance_path, "test-run provenance"
        ),
        "test_results": file_identity(results_path, "test results"),
        "test_subtests": file_identity(subtests_path, "structured subtest results"),
        "test_summary": file_identity(summary_path, "test summary"),
        "test_rows": test_rows,
        "external_evidence": external,
        "pending_total": 0,
        "unknown_total": 0,
        "failed_total": 0,
    }


def component_states(
    specifications: list[str],
    run_id: str,
    policy: dict[str, Any],
    repository: dict[str, object],
    provenance_path: Path,
    results_path: Path,
    subtests_path: Path,
    summary_path: Path,
    rows: dict[str, dict[str, str]],
    evidence_root: Path,
    expected_boot_id: str,
    production: bool,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    require_string(run_id, "Phase 2 evidence run ID", SAFE_ID_RE)
    mapped = parse_named_paths(specifications, "component-state")
    required = {
        str(item["name"]): item for item in policy["required_component_states"]
    }
    if sorted(mapped) != sorted(required):
        fail(
            "component states must exactly equal policy names: "
            f"{sorted(required)!r}"
        )
    states: list[dict[str, object]] = []
    state_inodes: set[tuple[int, int]] = set()
    aggregate = {"pending_total": 0, "unknown_total": 0, "failed_total": 0}
    run_directory = Path(policy["component_state_root"]) / run_id
    expected_names = {f"{name}.json" for name in required}
    try:
        run_metadata = run_directory.lstat()
        observed_names = {entry.name for entry in os.scandir(run_directory)}
    except OSError as error:
        fail(f"cannot inspect component-state run directory: {error}")
    if (
        not stat.S_ISDIR(run_metadata.st_mode)
        or stat.S_ISLNK(run_metadata.st_mode)
        or observed_names != expected_names
    ):
        fail(
            "component-state run directory must contain exactly the required "
            "component JSON files"
        )
    if production:
        validate_root_trust(
            run_directory, "component-state run directory", directory=True
        )
    for name in sorted(mapped):
        path = mapped[name]
        expected_path = component_state_path(policy, run_id, name)
        if path != expected_path:
            fail(
                f"component state {name} must use policy-pinned path {expected_path}"
            )
        if production:
            validate_root_trust(path, f"component state {name}")
        payload, state_stat = read_regular(path, f"component state {name}")
        inode_key = (state_stat["device"], state_stat["inode"])
        if inode_key in state_inodes:
            fail("component state files must have distinct inodes")
        state_inodes.add(inode_key)
        document = require_object(
            parse_json_bytes(payload, f"component state {name}"),
            f"component state {name}",
            {
                "component",
                "external_evidence",
                "failed_total",
                "pending_total",
                "repository",
                "run_id",
                "schema",
                "status",
                "test_results",
                "test_subtests",
                "test_rows",
                "test_run_provenance",
                "test_summary",
                "unknown_total",
            },
        )
        if payload != pretty_json(document):
            fail(f"component state {name} is not canonical pretty JSON")
        external_paths: dict[str, Path] = {}
        for index, raw_external in enumerate(
            require_list(document["external_evidence"], f"component state {name} external_evidence")
        ):
            external = require_object(
                raw_external,
                f"component state {name} external_evidence[{index}]",
                {"identity", "label"},
            )
            evidence_label = require_string(
                external["label"],
                f"component state {name} external evidence label",
                NAME_RE,
            )
            identity = require_object(
                external["identity"],
                f"component state {name} external evidence identity",
            )
            if evidence_label in external_paths:
                fail(f"component state {name} repeats external evidence {evidence_label}")
            external_paths[evidence_label] = absolute_path(
                identity.get("path"),
                f"component state {name} external evidence path",
            )
        expected = component_document(
            required[name],
            run_id,
            repository,
            provenance_path,
            results_path,
            subtests_path,
            summary_path,
            rows,
            evidence_root,
            external_paths,
            expected_boot_id,
            production,
        )
        if document != expected:
            fail(
                f"component state {name} is not the deterministic projection of "
                "the current repository, test rows, logs, and external evidence"
            )
        totals = {
            key: require_int(document[key], f"component state {name} {key}")
            for key in aggregate
        }
        for key in aggregate:
            aggregate[key] += totals[key]
        expected_provenance = require_object(
            expected["test_run_provenance"],
            f"component {name} expected test-run provenance",
        )
        expected_test_rows = [
            require_object(item, f"component {name} expected test row")
            for item in require_list(
                expected["test_rows"], f"component {name} expected test rows"
            )
        ]
        expected_external = [
            require_object(item, f"component {name} expected external evidence")
            for item in require_list(
                expected["external_evidence"],
                f"component {name} expected external evidence",
            )
        ]
        states.append(
            {
                "name": name,
                "identity": file_identity(path, f"component state {name}"),
                "tested_code_commit": repository["commit"],
                "tested_tree": repository["tree"],
                "source_tree_listing_sha256": repository[
                    "source_tree_listing_sha256"
                ],
                "test_run_provenance_sha256": expected_provenance["sha256"],
                "test_names": [row["name"] for row in expected_test_rows],
                "external_evidence": [
                    {
                        "label": item["label"],
                        "sha256": item["identity"]["sha256"],
                    }
                    for item in expected_external
                ],
                "totals": totals,
            }
        )
    if policy["aggregate_requires_zero"] and any(aggregate.values()):
        fail(f"Phase 2 aggregate is not terminally clean: {aggregate!r}")
    return states, aggregate


def component_state_run_record(
    policy: dict[str, Any], run_id: str, production: bool
) -> dict[str, object]:
    require_string(run_id, "Phase 2 evidence run ID", SAFE_ID_RE)
    run_directory = Path(policy["component_state_root"]) / run_id
    if production:
        validate_root_trust(
            run_directory, "component-state run directory", directory=True
        )
    try:
        metadata = run_directory.lstat()
    except OSError as error:
        fail(f"cannot inspect component-state run directory: {error}")
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail("component-state run path is not a real directory")
    return {
        "run_id": run_id,
        "path": os.fspath(run_directory),
        "stat": stat_identity(metadata),
    }


def atomic_publish(path: Path, payload: bytes, production: bool) -> None:
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink() or path.exists() or path.is_symlink():
        fail("index output parent must exist and final output must be absent")
    if production:
        validate_root_trust(parent, "index output parent", directory=True)
    partial = path.with_name(f".{path.name}.partial-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(partial, flags, 0o600)
        try:
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    fail("short write while publishing evidence index")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # Link publication gives us RENAME_NOREPLACE semantics using a portable
        # kernel primitive: link(2) fails with EEXIST and can never replace a
        # concurrently created final path.  The temporary hardlink is removed
        # before the parent directory is fsynced.
        try:
            os.link(partial, path, follow_symlinks=False)
        except OSError as error:
            fail(f"cannot publish final evidence path without replacement: {error}")
        partial.unlink()
        directory_descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_timestamp(value: Any, label: str) -> dt.datetime:
    text = require_string(value, label)
    if not text.endswith("Z"):
        fail(f"{label} must be UTC with a Z suffix")
    try:
        parsed = dt.datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as error:
        fail(f"{label} is invalid: {error}")
    if parsed.microsecond != 0:
        fail(f"{label} must have whole-second precision")
    return parsed


def current_boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
    except (OSError, UnicodeError) as error:
        fail(f"cannot read current boot identity: {error}")
    if re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
    ) is None:
        fail("current boot identity has an invalid format")
    return value


def validate_recorded_git_paths(
    requested_value: Any, resolved_value: Any
) -> tuple[Path, Path]:
    requested = absolute_path(requested_value, "recorded Git requested path")
    resolved = absolute_path(resolved_value, "recorded Git resolved path")
    try:
        current_resolved = requested.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve recorded Git requested path: {error}")
    if (
        current_resolved != resolved
        or not resolved.is_file()
        or not os.access(resolved, os.X_OK)
    ):
        fail("recorded Git requested/resolved executable identity changed")
    return requested, resolved


def run_provenance_start(arguments: argparse.Namespace) -> None:
    repository = absolute_path(arguments.repository_root, "repository root").resolve(
        strict=True
    )
    driver = absolute_path(arguments.driver, "test driver").resolve(strict=True)
    if repository not in driver.parents:
        fail("test driver escapes the repository root")
    git_raw = absolute_path(arguments.git, "Git executable")
    git = git_raw.resolve(strict=True)
    policy_path = Path(arguments.policy)
    if not policy_path.is_absolute():
        policy_path = repository / relative_path(arguments.policy, "evidence policy")
    else:
        policy_path = absolute_path(arguments.policy, "evidence policy")
    try:
        policy_path.relative_to(repository)
    except ValueError:
        fail("evidence policy escapes the repository")
    if policy_path.is_symlink():
        fail("evidence policy must not be a symlink")
    try:
        resolved_policy_path = policy_path.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve evidence policy: {error}")
    if resolved_policy_path != policy_path:
        fail("evidence policy traverses a symlink or non-canonical path")
    policy = load_policy(repository, policy_path)
    executed_paths = parse_named_paths(arguments.executed_tool, "executed tool")
    execution_tools = observe_execution_tools(
        repository,
        policy,
        executed_paths,
        bool(arguments.authoritative_tools),
        bool(arguments.authoritative_tools),
    )
    executed_git = execution_tool_by_name(execution_tools, "git")
    git_entrypoint = require_object(
        executed_git.get("entrypoint"), "executed Git entrypoint"
    )
    if (
        git_entrypoint.get("requested_path") != os.fspath(git_raw)
        or git_entrypoint.get("resolved_path") != os.fspath(git)
    ):
        fail("executed Git record differs from the provenance Git entry point")
    if "python3" in policy["test_execution_tools"]:
        require_active_python_matches(execution_tools, "provenance start")
    driver_shell = None
    if "bash" in policy["test_execution_tools"]:
        if arguments.driver_bash_pid is None:
            fail("the execution-tool policy requires an active test-driver Bash PID")
        driver_shell = observe_driver_shell(
            arguments.driver_bash_pid, execution_tools
        )
    identity = repository_identity(git, repository)
    document = {
        "schema": "gentoo-optimization-phase2-test-run-pending-v2",
        "started_at": utc_now(),
        "authoritative_tools": bool(arguments.authoritative_tools),
        "boot_id": current_boot_id(),
        "repository": identity,
        "driver": file_identity(driver, "test driver"),
        "driver_shell": driver_shell,
        "executed_tools": execution_tools,
        "git_requested_path": os.fspath(git_raw),
        "git_resolved_path": os.fspath(git),
        "policy": file_identity(policy_path, "Phase 2 evidence policy"),
        "tool_manifest": file_identity(
            repository / policy["tool_manifest_template_path"],
            "reviewed tool manifest",
        ),
    }
    atomic_publish(
        absolute_path(arguments.output, "test-run provenance pending output"),
        pretty_json(document),
        False,
    )


def validate_run_provenance(
    path: Path,
    repository: dict[str, object],
    driver_source: dict[str, object],
    results_path: Path,
    subtests_path: Path,
    summary_path: Path,
    git_requested_path: Path,
    git_resolved_path: Path,
    expected_boot_id: str,
    production: bool,
    policy: dict[str, Any],
) -> dict[str, Any]:
    if production:
        validate_root_trust(path, "test-run provenance")
    payload, _identity = read_regular(path, "test-run provenance")
    document = require_object(
        parse_json_bytes(payload, "test-run provenance"),
        "test-run provenance",
        {
            "authoritative_tools",
            "boot_id",
            "completed_at",
            "driver",
            "driver_shell",
            "executed_tools",
            "git_requested_path",
            "git_resolved_path",
            "policy",
            "repository",
            "results",
            "schema",
            "started_at",
            "subtests",
            "summary",
            "tool_manifest",
        },
    )
    if document["schema"] != "gentoo-optimization-phase2-test-run-provenance-v2":
        fail("test-run provenance schema is invalid")
    if not isinstance(document["authoritative_tools"], bool):
        fail("test-run authoritative_tools is not boolean")
    started = parse_timestamp(document["started_at"], "test-run started_at")
    completed = parse_timestamp(document["completed_at"], "test-run completed_at")
    if completed < started:
        fail("test-run completion predates its start")
    if require_string(document["boot_id"], "test-run boot_id") != expected_boot_id:
        fail("test-run provenance boot identity differs from the required boot")
    if document["repository"] != repository:
        fail("test-run provenance belongs to another commit/tree/source listing")
    repository_root = absolute_path(repository.get("root"), "repository root")
    policy_path = Path(policy["_resolved_path"])
    policy_record = require_object(document["policy"], "test-run provenance policy")
    if policy_record != file_identity(policy_path, "Phase 2 evidence policy"):
        fail("test-run provenance evidence policy identity changed")
    manifest_path = repository_root / policy["tool_manifest_template_path"]
    manifest_record = require_object(
        document["tool_manifest"], "test-run provenance tool manifest"
    )
    if manifest_record != file_identity(manifest_path, "reviewed tool manifest"):
        fail("test-run provenance tool manifest identity changed")
    execution_tools = validate_execution_tool_records(
        document["executed_tools"],
        repository_root,
        policy,
        bool(document["authoritative_tools"]),
        production,
    )
    driver_shell = document["driver_shell"]
    if "bash" in policy["test_execution_tools"]:
        shell_record = require_object(
            driver_shell,
            "test-run provenance driver shell",
            {"argv0", "executable", "pid", "start_time"},
        )
        require_int(shell_record["pid"], "test-run driver shell PID", 2)
        require_int(
            shell_record["start_time"], "test-run driver shell start time", 1
        )
        bash_runtime = require_object(
            execution_tool_by_name(execution_tools, "bash").get("runtime"),
            "test-run executed Bash runtime",
            {"binary", "reported_path"},
        )
        if shell_record["executable"] != bash_runtime["binary"]:
            fail("test-run driver shell differs from the executed Bash runtime")
        bash_entrypoint = require_object(
            execution_tool_by_name(execution_tools, "bash").get("entrypoint"),
            "test-run executed Bash entry point",
        )
        if shell_record["argv0"] != bash_entrypoint.get("requested_path"):
            fail("test-run driver shell argv[0] differs from the executed Bash entry point")
    elif driver_shell is not None:
        fail("test-run provenance records a driver shell outside its tool policy")
    driver = require_object(document["driver"], "test-run provenance driver")
    driver_path = Path(str(repository["root"])) / str(driver_source["path"])
    if driver != file_identity(driver_path, "test-run provenance driver"):
        fail("test-run provenance driver differs from the indexed source")
    recorded_requested, recorded_resolved = validate_recorded_git_paths(
        document["git_requested_path"], document["git_resolved_path"]
    )
    if recorded_requested != git_requested_path or recorded_resolved != git_resolved_path:
        fail("test-run provenance Git entry point differs from the indexed tool")
    executed_git = execution_tool_by_name(execution_tools, "git")
    git_entrypoint = require_object(
        executed_git.get("entrypoint"), "test-run executed Git entrypoint"
    )
    if (
        git_entrypoint.get("requested_path") != os.fspath(recorded_requested)
        or git_entrypoint.get("resolved_path") != os.fspath(recorded_resolved)
    ):
        fail("test-run executed Git differs from its provenance Git entry point")
    if document["results"] != file_identity(results_path, "test results"):
        fail("test-run provenance results identity is stale")
    if document["subtests"] != file_identity(
        subtests_path, "structured subtest results"
    ):
        fail("test-run provenance subtest identity is stale")
    if document["summary"] != file_identity(summary_path, "test summary"):
        fail("test-run provenance summary identity is stale")
    return document


def run_provenance_finish(arguments: argparse.Namespace) -> None:
    pending_path = absolute_path(arguments.pending, "test-run pending provenance")
    pending_payload, _pending_identity = read_regular(
        pending_path, "test-run pending provenance"
    )
    pending = require_object(
        parse_json_bytes(pending_payload, "test-run pending provenance"),
        "test-run pending provenance",
        {
            "authoritative_tools",
            "boot_id",
            "driver",
            "driver_shell",
            "executed_tools",
            "git_requested_path",
            "git_resolved_path",
            "policy",
            "repository",
            "schema",
            "started_at",
            "tool_manifest",
        },
    )
    if pending["schema"] != "gentoo-optimization-phase2-test-run-pending-v2":
        fail("test-run pending provenance schema is invalid")
    if not isinstance(pending["authoritative_tools"], bool):
        fail("pending authoritative_tools is not boolean")
    parse_timestamp(pending["started_at"], "pending started_at")
    if require_string(pending["boot_id"], "pending boot_id") != current_boot_id():
        fail("test run cannot be finalized after a reboot")
    repository_record = require_object(pending["repository"], "pending repository")
    repository = absolute_path(repository_record.get("root"), "pending repository root")
    policy_record = require_object(pending["policy"], "pending evidence policy")
    policy_path = absolute_path(policy_record.get("path"), "pending evidence policy path")
    try:
        policy_path.relative_to(repository)
    except ValueError:
        fail("pending evidence policy escapes the recorded repository")
    if file_identity(policy_path, "Phase 2 evidence policy") != policy_record:
        fail("Phase 2 evidence policy changed during the test run")
    policy = load_policy(repository, policy_path)
    manifest_record = require_object(
        pending["tool_manifest"], "pending reviewed tool manifest"
    )
    manifest_path = repository / policy["tool_manifest_template_path"]
    if file_identity(manifest_path, "reviewed tool manifest") != manifest_record:
        fail("reviewed tool manifest changed during the test run")
    execution_tools = validate_execution_tool_records(
        pending["executed_tools"],
        repository,
        policy,
        bool(pending["authoritative_tools"]),
        bool(pending["authoritative_tools"]),
    )
    if "python3" in policy["test_execution_tools"]:
        require_active_python_matches(execution_tools, "provenance finalization")
    driver_shell = pending["driver_shell"]
    if "bash" in policy["test_execution_tools"]:
        if arguments.driver_bash_pid is None:
            fail("provenance finalization requires the active test-driver Bash PID")
        current_driver_shell = observe_driver_shell(
            arguments.driver_bash_pid, execution_tools
        )
        if current_driver_shell != driver_shell:
            fail("active test-driver Bash changed during the test run")
    elif driver_shell is not None:
        fail("pending provenance records a driver shell outside its tool policy")
    _git_requested, git = validate_recorded_git_paths(
        pending["git_requested_path"], pending["git_resolved_path"]
    )
    if repository_identity(git, repository) != repository_record:
        fail("repository changed during the test run")
    driver_record = require_object(pending["driver"], "pending test driver")
    driver_path = absolute_path(driver_record.get("path"), "pending test driver path")
    if file_identity(driver_path, "test driver") != driver_record:
        fail("test driver changed during the test run")
    results = absolute_path(arguments.results, "test results")
    subtests = absolute_path(arguments.subtests, "structured subtest results")
    summary = absolute_path(arguments.summary, "test summary")
    document = {
        "schema": "gentoo-optimization-phase2-test-run-provenance-v2",
        "started_at": pending["started_at"],
        "completed_at": utc_now(),
        "authoritative_tools": pending["authoritative_tools"],
        "boot_id": pending["boot_id"],
        "repository": repository_record,
        "driver": driver_record,
        "driver_shell": driver_shell,
        "executed_tools": execution_tools,
        "git_requested_path": pending["git_requested_path"],
        "git_resolved_path": pending["git_resolved_path"],
        "policy": policy_record,
        "results": file_identity(results, "test results"),
        "subtests": file_identity(subtests, "structured subtest results"),
        "summary": file_identity(summary, "test summary"),
        "tool_manifest": manifest_record,
    }
    output = absolute_path(arguments.output, "test-run provenance output")
    atomic_publish(output, pretty_json(document), False)
    try:
        pending_path.unlink()
    except OSError as error:
        fail(f"cannot remove finalized test-run pending provenance: {error}")
    directory = os.open(pending_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def component_state_command(arguments: argparse.Namespace) -> None:
    requested_repository = absolute_path(arguments.repository_root, "repository root")
    repository = requested_repository.resolve(strict=True)
    if requested_repository != repository or not repository.is_dir() or repository.is_symlink():
        fail("repository root must be a canonical real directory")
    policy_path = Path(arguments.policy)
    if not policy_path.is_absolute():
        policy_path = repository / relative_path(arguments.policy, "policy")
    policy_path = policy_path.resolve(strict=True)
    if arguments.production:
        if policy_path != repository / POLICY_RELATIVE:
            fail("production component generation requires the tracked policy")
        if Path(__file__).resolve(strict=True) != repository / VERIFIER_RELATIVE:
            fail("production component generation must execute the tracked verifier")
        validate_root_trust(repository, "source repository", directory=True)
        validate_root_trust(policy_path, "evidence policy")
        validate_root_trust(
            repository / VERIFIER_RELATIVE, "component-state generator"
        )
    policy = load_policy(repository, policy_path)
    component_name = require_string(arguments.component, "component", NAME_RE)
    component_specification = next(
        (
            item
            for item in policy["required_component_states"]
            if item["name"] == component_name
        ),
        None,
    )
    if component_specification is None:
        fail(f"unknown Phase 2 component: {component_name}")
    run_id = require_string(
        arguments.run_id, "Phase 2 evidence run ID", SAFE_ID_RE
    )
    output = absolute_path(arguments.output, "component state output")
    expected_output = component_state_path(policy, run_id, component_name)
    if output != expected_output:
        fail(
            f"component {component_name} output must use run-scoped path "
            f"{expected_output}"
        )
    run_directory = expected_output.parent
    try:
        run_metadata = run_directory.lstat()
        entries = list(os.scandir(run_directory))
    except OSError as error:
        fail(f"cannot inspect component-state run directory: {error}")
    expected_filenames = {
        f"{item['name']}.json" for item in policy["required_component_states"]
    }
    if not stat.S_ISDIR(run_metadata.st_mode) or stat.S_ISLNK(run_metadata.st_mode):
        fail("component-state run path is not a real directory")
    for entry in entries:
        if entry.name not in expected_filenames or not entry.is_file(
            follow_symlinks=False
        ):
            fail("component-state run directory contains an unexpected entry")
    if arguments.production:
        validate_root_trust(
            run_directory, "component-state run directory", directory=True
        )
    evidence_root = absolute_path(arguments.evidence_root, "evidence root")
    provenance_path = absolute_path(arguments.provenance, "test-run provenance")
    results_path = absolute_path(arguments.results, "test results")
    subtests_path = absolute_path(arguments.subtests, "structured subtest results")
    summary_path = absolute_path(arguments.summary, "test summary")
    for path, label in (
        (provenance_path, "test-run provenance"),
        (results_path, "test results"),
        (subtests_path, "structured subtest results"),
        (summary_path, "test summary"),
    ):
        if evidence_root not in path.parents:
            fail(f"{label} must be retained below the evidence root")
    git_requested = absolute_path(arguments.git, "Git executable")
    git_resolved = git_requested.resolve(strict=True)
    if arguments.production:
        tools = tool_manifest(
            repository / policy["tool_manifest_template_path"],
            policy["required_tools"],
        )
        reviewed_python = next(item for item in tools if item["name"] == "python3")
        require_active_python_matches_reviewed_tools(
            [observe_tool(reviewed_python, True)],
            "production component-state generation",
        )
        reviewed_git = next(item for item in tools if item["name"] == "git")
        if git_requested != reviewed_git["path"]:
            fail("production component generation requires the reviewed Git entry point")
        validate_root_trust(git_resolved, "Git executable")
    repository_record = repository_identity(git_resolved, repository)
    sources = source_inventory(git_resolved, repository, policy)
    source_by_path = {str(item["path"]): item for item in sources}
    driver_source = source_by_path[policy["test_driver_path"]]
    _totals, _required, _mode, rows, _subtest_totals, _subtest_rows = parse_results(
        results_path, subtests_path, summary_path, policy
    )
    validate_run_provenance(
        provenance_path,
        repository_record,
        driver_source,
        results_path,
        subtests_path,
        summary_path,
        git_requested,
        git_resolved,
        current_boot_id(),
        arguments.production,
        policy,
    )
    external_paths = parse_named_paths(
        arguments.external_evidence, "external-evidence"
    )
    document = component_document(
        component_specification,
        run_id,
        repository_record,
        provenance_path,
        results_path,
        subtests_path,
        summary_path,
        rows,
        evidence_root,
        external_paths,
        current_boot_id(),
        arguments.production,
    )
    atomic_publish(output, pretty_json(document), arguments.production)


def build_capture(arguments: argparse.Namespace) -> dict[str, object]:
    requested_repository = absolute_path(arguments.repository_root, "repository root")
    repository = requested_repository.resolve(strict=True)
    if requested_repository != repository:
        fail("repository root must be canonical and may not traverse a symlink")
    if not repository.is_dir() or repository.is_symlink():
        fail("repository root must be a canonical real directory")
    policy_path = Path(arguments.policy)
    if not policy_path.is_absolute():
        policy_path = repository / relative_path(arguments.policy, "policy")
    policy_path = policy_path.resolve(strict=True)
    if arguments.production:
        expected_policy = repository / POLICY_RELATIVE
        expected_verifier = repository / VERIFIER_RELATIVE
        if policy_path != expected_policy:
            fail(
                "production capture requires the tracked repository evidence policy"
            )
        if Path(__file__).resolve(strict=True) != expected_verifier:
            fail(
                "production capture must execute the tracked verifier from the "
                "indexed repository"
            )
        validate_root_trust(repository, "source repository", directory=True)
        validate_root_trust(expected_policy, "evidence policy")
        validate_root_trust(expected_verifier, "evidence capture executable")
    policy = load_policy(repository, policy_path)
    run_id = require_string(
        arguments.run_id, "Phase 2 evidence run ID", SAFE_ID_RE
    )
    evidence_root = absolute_path(arguments.evidence_root, "evidence root")
    output = absolute_path(arguments.output, "index output")
    expected_output = evidence_index_path(policy, run_id)
    if output != expected_output:
        fail(f"evidence index output must use run-scoped path {expected_output}")
    try:
        index_run_metadata = output.parent.lstat()
        index_run_entries = list(os.scandir(output.parent))
    except OSError as error:
        fail(f"cannot inspect evidence-index run directory: {error}")
    if (
        not stat.S_ISDIR(index_run_metadata.st_mode)
        or stat.S_ISLNK(index_run_metadata.st_mode)
        or index_run_entries
    ):
        fail("evidence-index run directory must be a new empty real directory")
    if arguments.production:
        validate_root_trust(
            output.parent, "evidence-index run directory", directory=True
        )
    if output == evidence_root or evidence_root in output.parents:
        fail("evidence index output must remain outside the evidence tree it binds")
    tools_path = absolute_path(arguments.tools, "tool manifest")
    test_results = absolute_path(arguments.test_results, "test results")
    test_subtests = absolute_path(
        arguments.test_subtests, "structured subtest results"
    )
    test_summary = absolute_path(arguments.test_summary, "test summary")
    for path, label in (
        (tools_path, "tool manifest"),
        (test_results, "test results"),
        (test_subtests, "structured subtest results"),
        (test_summary, "test summary"),
    ):
        if evidence_root not in path.parents:
            fail(f"{label} must be retained below the evidence root")
        if arguments.production:
            validate_root_trust(path, label)
    tool_specs = tool_manifest(tools_path, policy["required_tools"])
    tools_payload, _tools_identity = read_regular(tools_path, "tool manifest")
    template_payload, _template_identity = read_regular(
        repository / policy["tool_manifest_template_path"],
        "tracked tool manifest template",
    )
    if tools_payload != template_payload:
        fail("retained tool manifest differs from the tracked reviewed template")
    tools = [observe_tool(item, arguments.production) for item in tool_specs]
    if arguments.production:
        require_active_python_matches_reviewed_tools(
            tools, "production evidence capture"
        )
    git_record = next(item for item in tools if item["name"] == "git")
    git = Path(str(git_record["resolved_path"]))
    before_repository = repository_identity(git, repository)
    sources = source_inventory(git, repository, policy)
    hashes = source_hashes(sources)
    plan_hash, claims = plan_claims(repository / policy["plan_path"], policy, hashes)
    (
        totals,
        required_passes,
        mode,
        rows,
        subtest_totals,
        subtest_rows,
    ) = parse_results(
        test_results, test_subtests, test_summary, policy
    )
    contract = require_object(
        policy["authoritative_test_contract"], "authoritative test contract"
    )
    contract_totals, required_named_subtests = validate_authoritative_subtests(
        contract, subtest_rows
    )
    contract_path = repository / policy["authoritative_test_contract_path"]
    first_manifest = tree_manifest(evidence_root, arguments.production)
    second_manifest = tree_manifest(evidence_root, arguments.production)
    if first_manifest != second_manifest:
        fail("evidence tree changed while its manifest was captured")
    after_repository = repository_identity(git, repository)
    if after_repository != before_repository:
        fail("repository identity changed while evidence was captured")
    source_by_path = {str(item["path"]): item for item in sources}
    test_driver = source_by_path[policy["test_driver_path"]]
    provenance_path = evidence_root / "test-run-provenance.json"
    if (evidence_root / "test-run-provenance.pending.json").exists():
        fail("test-run provenance is still pending")
    provenance = validate_run_provenance(
        provenance_path,
        before_repository,
        test_driver,
        test_results,
        test_subtests,
        test_summary,
        Path(str(git_record["requested_path"])),
        git,
        current_boot_id(),
        arguments.production,
        policy,
    )
    require_execution_entrypoints_match_reviewed_tools(
        provenance["executed_tools"], tools
    )
    states, aggregate = component_states(
        arguments.component_state,
        run_id,
        policy,
        before_repository,
        provenance_path,
        test_results,
        test_subtests,
        test_summary,
        rows,
        evidence_root,
        str(provenance["boot_id"]),
        arguments.production,
    )
    return {
        "schema": INDEX_SCHEMA,
        "phase": 2,
        "production": bool(arguments.production),
        "run_id": run_id,
        "component_state_run": component_state_run_record(
            policy, run_id, bool(arguments.production)
        ),
        "repository": before_repository,
        "policy": file_identity(policy_path, "Phase 2 evidence policy"),
        "sources": sources,
        "plan": {"path": policy["plan_path"], "sha256": plan_hash, "claims": claims},
        "tools": tools,
        "test_run": {
            "authoritative": True,
            "contract": file_identity(
                contract_path, "authoritative test contract"
            ),
            "contract_totals": contract_totals,
            "driver": {
                "path": policy["test_driver_path"],
                "sha256": test_driver["sha256"],
                "stat": stat_identity((repository / policy["test_driver_path"]).lstat()),
            },
            "driver_shell": provenance["driver_shell"],
            "executed_tools": provenance["executed_tools"],
            "results": file_identity(test_results, "test results"),
            "subtests": file_identity(
                test_subtests, "structured subtest results"
            ),
            "summary": file_identity(test_summary, "test summary"),
            "provenance": file_identity(provenance_path, "test-run provenance"),
            "boot_id": provenance["boot_id"],
            "mode": mode,
            "totals": {"pass": totals["pass"], "fail": totals["fail"], "skip": totals["skip"], "total": totals["total"]},
            "subtest_totals": subtest_totals,
            "mandatory_internal_skip": subtest_totals["mandatory_internal_skip"],
            "diagnostic_internal_skip": subtest_totals["diagnostic_skip"],
            "required_passes": required_passes,
            "required_named_subtests": required_named_subtests,
        },
        "evidence_manifest": first_manifest,
        "component_states": states,
        "aggregate": aggregate,
    }


def validate_index_shape(index: Any) -> dict[str, Any]:
    document = require_object(
        index,
        "evidence index",
        {
            "aggregate",
            "component_state_run",
            "component_states",
            "evidence_manifest",
            "phase",
            "plan",
            "policy",
            "production",
            "repository",
            "run_id",
            "schema",
            "sources",
            "test_run",
            "tools",
        },
    )
    if document["schema"] != INDEX_SCHEMA or document["phase"] != 2 or not isinstance(document["production"], bool):
        fail("evidence index has the wrong schema, phase, or production flag")
    return document


def require_verification_mode(recorded_production: bool, requested_production: bool) -> None:
    if recorded_production != requested_production:
        fail("evidence index production mode must exactly match verification mode")


def require_verification_boot(recorded_boot_id: str, production: bool) -> None:
    require_string(recorded_boot_id, "evidence index boot ID")
    if production and recorded_boot_id != current_boot_id():
        fail(
            "production evidence authorization belongs to another boot; rerun "
            "the full gate with a new run ID"
        )


def verify_index(arguments: argparse.Namespace) -> None:
    index_path = absolute_path(arguments.index, "evidence index")
    if arguments.production:
        validate_root_trust(index_path, "detached evidence index")
    payload, _identity = read_regular(index_path, "evidence index")
    document = validate_index_shape(parse_json_bytes(payload, "evidence index"))
    if payload != pretty_json(document):
        fail("evidence index is not canonical pretty JSON")
    require_verification_mode(
        bool(document["production"]), bool(arguments.production)
    )
    production = bool(arguments.production)
    repository_record = require_object(document["repository"], "repository")
    repository_path = absolute_path(repository_record.get("root"), "repository root")
    repository = repository_path.resolve(strict=True)
    if repository != repository_path:
        fail("indexed repository root now traverses a symlink")
    if production:
        expected_policy = repository / POLICY_RELATIVE
        expected_verifier = repository / VERIFIER_RELATIVE
        if Path(__file__).resolve(strict=True) != expected_verifier:
            fail(
                "production verification must execute the tracked verifier from "
                "the indexed repository"
            )
        validate_root_trust(repository, "source repository", directory=True)
        validate_root_trust(expected_verifier, "evidence verifier executable")
    policy_record = require_object(document["policy"], "policy identity")
    policy_path = absolute_path(policy_record.get("path"), "policy path")
    if production and policy_path != repository / POLICY_RELATIVE:
        fail("production index does not bind the tracked repository evidence policy")
    if file_identity(policy_path, "Phase 2 evidence policy") != policy_record:
        fail("Phase 2 evidence policy changed")
    policy = load_policy(repository, policy_path)
    reviewed_tool_manifest = repository / policy["tool_manifest_template_path"]
    if production:
        validate_root_trust(reviewed_tool_manifest, "reviewed tool manifest")
    reviewed_tool_specs = tool_manifest(
        reviewed_tool_manifest, policy["required_tools"]
    )
    tools_raw = require_list(document["tools"], "tools", nonempty=True)
    observed_tools = [
        observe_tool(specification, production)
        for specification in reviewed_tool_specs
    ]
    if production:
        require_active_python_matches_reviewed_tools(
            observed_tools, "production evidence verification"
        )
    if observed_tools != tools_raw:
        fail(
            "indexed tool topology, reviewed specification, or observed identity "
            "differs from the tracked tool manifest"
        )
    git_record = next((item for item in observed_tools if item["name"] == "git"), None)
    if git_record is None:
        fail("evidence index has no Git tool identity")
    git = Path(str(git_record["resolved_path"]))
    repository_now = repository_identity(git, repository)
    if repository_now != repository_record:
        fail("repository commit, tree, reference, or clean status changed")
    run_id = require_string(document["run_id"], "evidence index run ID", SAFE_ID_RE)
    if index_path != evidence_index_path(policy, run_id):
        fail("detached evidence index is outside its policy-pinned run path")
    try:
        index_run_entries = {entry.name for entry in os.scandir(index_path.parent)}
    except OSError as error:
        fail(f"cannot inspect evidence-index run directory: {error}")
    if index_run_entries != {"index.json"}:
        fail("evidence-index run directory must contain exactly index.json")
    if production:
        validate_root_trust(
            index_path.parent, "evidence-index run directory", directory=True
        )
    sources_now = source_inventory(git, repository, policy)
    if sources_now != document["sources"]:
        fail("tracked Phase 2 source inventory changed")
    hashes = source_hashes(sources_now)
    plan_hash, claims = plan_claims(repository / policy["plan_path"], policy, hashes)
    plan_record = require_object(document["plan"], "plan identity")
    if plan_record != {"path": policy["plan_path"], "sha256": plan_hash, "claims": claims}:
        fail("project plan or its checked evidence markers changed")
    test_record = require_object(document["test_run"], "test run")
    results_record = require_object(test_record.get("results"), "test results identity")
    subtests_record = require_object(
        test_record.get("subtests"), "structured subtest results identity"
    )
    summary_record = require_object(test_record.get("summary"), "test summary identity")
    results_path = absolute_path(results_record.get("path"), "test results path")
    subtests_path = absolute_path(
        subtests_record.get("path"), "structured subtest results path"
    )
    summary_path = absolute_path(summary_record.get("path"), "test summary path")
    (
        totals,
        required_passes,
        mode,
        rows,
        subtest_totals,
        subtest_rows,
    ) = parse_results(
        results_path, subtests_path, summary_path, policy
    )
    if (
        file_identity(results_path, "test results") != results_record
        or file_identity(subtests_path, "structured subtest results")
        != subtests_record
        or file_identity(summary_path, "test summary") != summary_record
    ):
        fail("test results, subtests, or summary identity changed")
    contract = require_object(
        policy["authoritative_test_contract"], "authoritative test contract"
    )
    contract_totals, required_named_subtests = validate_authoritative_subtests(
        contract, subtest_rows
    )
    contract_path = repository / policy["authoritative_test_contract_path"]
    contract_record = require_object(
        test_record.get("contract"), "authoritative test contract identity"
    )
    expected_test_totals = {"pass": totals["pass"], "fail": totals["fail"], "skip": totals["skip"], "total": totals["total"]}
    if (
        test_record.get("authoritative") is not True
        or contract_record
        != file_identity(contract_path, "authoritative test contract")
        or test_record.get("contract_totals") != contract_totals
        or test_record.get("mode") != mode
        or test_record.get("totals") != expected_test_totals
        or test_record.get("subtest_totals") != subtest_totals
        or test_record.get("mandatory_internal_skip")
        != subtest_totals["mandatory_internal_skip"]
        or test_record.get("diagnostic_internal_skip")
        != subtest_totals["diagnostic_skip"]
        or test_record.get("required_passes") != required_passes
        or test_record.get("required_named_subtests")
        != required_named_subtests
    ):
        fail("test run summary fields changed")
    driver_source = next(item for item in sources_now if item["path"] == policy["test_driver_path"])
    driver_record = require_object(test_record.get("driver"), "test driver identity")
    expected_driver_record = {
        "path": policy["test_driver_path"],
        "sha256": driver_source["sha256"],
        "stat": stat_identity((repository / policy["test_driver_path"]).lstat()),
    }
    if driver_record != expected_driver_record:
        fail("test driver identity differs from the current source")
    indexed_driver_shell = test_record.get("driver_shell")
    if "bash" in policy["test_execution_tools"]:
        indexed_driver_shell = require_object(
            indexed_driver_shell, "test driver shell identity"
        )
    elif indexed_driver_shell is not None:
        fail("indexed driver-shell identity exists outside its tool policy")
    provenance_record = require_object(
        test_record.get("provenance"), "test-run provenance identity"
    )
    provenance_path = absolute_path(
        provenance_record.get("path"), "test-run provenance path"
    )
    if file_identity(provenance_path, "test-run provenance") != provenance_record:
        fail("test-run provenance identity changed")
    recorded_boot_id = require_string(test_record.get("boot_id"), "test run boot_id")
    require_verification_boot(recorded_boot_id, production)
    verified_provenance = validate_run_provenance(
        provenance_path,
        repository_now,
        driver_source,
        results_path,
        subtests_path,
        summary_path,
        Path(str(git_record["requested_path"])),
        git,
        recorded_boot_id,
        production,
        policy,
    )
    if test_record.get("executed_tools") != verified_provenance["executed_tools"]:
        fail("indexed executed-tool records differ from test-run provenance")
    if indexed_driver_shell != verified_provenance["driver_shell"]:
        fail("indexed driver-shell identity differs from test-run provenance")
    require_execution_entrypoints_match_reviewed_tools(
        verified_provenance["executed_tools"], observed_tools
    )
    evidence_record = require_object(document["evidence_manifest"], "evidence manifest")
    evidence_root = absolute_path(evidence_record.get("root"), "evidence root")
    if tree_manifest(evidence_root, production) != evidence_record:
        fail("evidence directory manifest changed")
    run_record = require_object(
        document["component_state_run"],
        "component-state run",
        {"path", "run_id", "stat"},
    )
    component_run_id = require_string(
        run_record["run_id"], "component-state run ID", SAFE_ID_RE
    )
    if component_run_id != run_id:
        fail("component-state run ID differs from the evidence index run ID")
    if run_record != component_state_run_record(policy, run_id, production):
        fail("component-state run directory identity changed")
    state_specs: list[str] = []
    for raw in require_list(document["component_states"], "component states", nonempty=True):
        state = require_object(raw, "component state record")
        identity = require_object(state.get("identity"), "component state identity")
        state_specs.append(f"{state.get('name')}={identity.get('path')}")
    states_now, aggregate_now = component_states(
        state_specs,
        run_id,
        policy,
        repository_now,
        provenance_path,
        results_path,
        subtests_path,
        summary_path,
        rows,
        evidence_root,
        recorded_boot_id,
        production,
    )
    if states_now != document["component_states"] or aggregate_now != document["aggregate"]:
        fail("component state identities or Phase 2 aggregate changed")


def marker_command(arguments: argparse.Namespace) -> None:
    repository = absolute_path(arguments.repository_root, "repository root").resolve(strict=True)
    policy_path = Path(arguments.policy)
    if not policy_path.is_absolute():
        policy_path = repository / relative_path(arguments.policy, "policy")
    policy = load_policy(repository, policy_path.resolve(strict=True))
    claim_id = require_string(arguments.claim_id, "claim ID", NAME_RE)
    claim = next((item for item in policy["plan_claims"] if item["claim_id"] == claim_id), None)
    if claim is None:
        fail(f"unknown plan claim: {claim_id}")
    plan_payload, _identity = read_regular(repository / policy["plan_path"], "project plan")
    lines = plan_payload.decode("utf-8").splitlines()
    try:
        phase_start = lines.index(policy["phase_heading"]) + 1
        phase_end = lines.index(policy["phase_next_heading"]) + 1
    except ValueError as error:
        fail(f"project plan lacks an exact Phase 2 section boundary: {error}")
    line_numbers = sorted(
        {
            require_int(line_number, "checkbox line", 1)
            for line_number in arguments.checkbox_line
        }
    )
    if not line_numbers:
        fail("at least one checkbox line is required")
    for line_number in line_numbers:
        if (
            line_number > len(lines)
            or not phase_start < line_number < phase_end
            or CHECKED_RE.match(lines[line_number - 1]) is None
        ):
            fail("selected plan line is not a checked Phase 2 Markdown checkbox")
    hashes: dict[str, str] = {}
    for source_path in claim["source_paths"]:
        payload, _source_identity = read_regular(repository / source_path, f"claim source {source_path}")
        hashes[source_path] = sha256(payload)
    marker = {
        "checkbox_sha256": sorted(
            sha256(lines[line_number - 1].encode("utf-8"))
            for line_number in line_numbers
        ),
        "claim_id": claim_id,
        "source_sha256": hashes,
    }
    print(f"{MARKER_PREFIX}{canonical_json(marker).decode('utf-8')}{MARKER_SUFFIX}")


def test_contract_command(arguments: argparse.Namespace) -> None:
    contract_path = absolute_path(arguments.contract, "authoritative test contract")
    contract = load_authoritative_test_contract(contract_path)
    results_path = absolute_path(arguments.results, "test results")
    subtests_path = absolute_path(arguments.subtests, "structured subtest results")
    counts, subtest_counts = validate_test_contract_run(
        contract, results_path, subtests_path, arguments.mode
    )
    print(
        "TEST-CONTRACT: "
        f"mode={arguments.mode} tests={sum(counts.values())} "
        f"subtests={subtest_counts['total']}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    capture = subparsers.add_parser("capture", help="create one immutable Phase 2 evidence index")
    capture.add_argument("--repository-root", required=True)
    capture.add_argument("--policy", default="optimization/phase2-evidence-policy.json")
    capture.add_argument("--evidence-root", required=True)
    capture.add_argument("--tools", required=True)
    capture.add_argument("--test-results", required=True)
    capture.add_argument("--test-subtests", required=True)
    capture.add_argument("--test-summary", required=True)
    capture.add_argument("--run-id", required=True)
    capture.add_argument("--component-state", action="append", default=[], required=True)
    capture.add_argument("--output", required=True)
    capture.add_argument("--production", action="store_true")

    verify = subparsers.add_parser("verify", help="revalidate every identity in an existing index")
    verify.add_argument("--index", required=True)
    verify.add_argument("--production", action="store_true")

    marker = subparsers.add_parser("plan-marker", help="render a canonical checked-plan evidence marker")
    marker.add_argument("--repository-root", required=True)
    marker.add_argument("--policy", default="optimization/phase2-evidence-policy.json")
    marker.add_argument("--claim-id", required=True)
    marker.add_argument("--checkbox-line", required=True, type=int, action="append")

    provenance_start = subparsers.add_parser(
        "run-provenance-start",
        help="bind a clean commit/tree and driver before a test run",
    )
    provenance_start.add_argument("--repository-root", required=True)
    provenance_start.add_argument("--driver", required=True)
    provenance_start.add_argument(
        "--policy", default="optimization/phase2-evidence-policy.json"
    )
    provenance_start.add_argument("--git", default="/usr/bin/git")
    provenance_start.add_argument(
        "--executed-tool", action="append", default=[], required=True
    )
    provenance_start.add_argument("--driver-bash-pid", type=int)
    provenance_start.add_argument("--authoritative-tools", action="store_true")
    provenance_start.add_argument("--output", required=True)

    provenance_finish = subparsers.add_parser(
        "run-provenance-finish",
        help="bind final results and summary to a pending test run",
    )
    provenance_finish.add_argument("--pending", required=True)
    provenance_finish.add_argument("--results", required=True)
    provenance_finish.add_argument("--subtests", required=True)
    provenance_finish.add_argument("--summary", required=True)
    provenance_finish.add_argument("--driver-bash-pid", type=int)
    provenance_finish.add_argument("--output", required=True)

    component = subparsers.add_parser(
        "component-state",
        help="generate one deterministic component state from retained evidence",
    )
    component.add_argument("--repository-root", required=True)
    component.add_argument(
        "--policy", default="optimization/phase2-evidence-policy.json"
    )
    component.add_argument("--component", required=True)
    component.add_argument("--run-id", required=True)
    component.add_argument("--evidence-root", required=True)
    component.add_argument("--provenance", required=True)
    component.add_argument("--results", required=True)
    component.add_argument("--subtests", required=True)
    component.add_argument("--summary", required=True)
    component.add_argument("--external-evidence", action="append", default=[])
    component.add_argument("--git", default="/usr/bin/git")
    component.add_argument("--output", required=True)
    component.add_argument("--production", action="store_true")

    test_contract = subparsers.add_parser(
        "test-contract",
        help="validate exact top-level and internal test identities for one mode",
    )
    test_contract.add_argument("--contract", required=True)
    test_contract.add_argument("--results", required=True)
    test_contract.add_argument("--subtests", required=True)
    test_contract.add_argument(
        "--mode", choices=("portable-complete", "authoritative"), required=True
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.action == "capture":
            document = build_capture(arguments)
            atomic_publish(
                absolute_path(arguments.output, "index output"),
                pretty_json(document),
                bool(arguments.production),
            )
            print(f"INDEX: {arguments.output}")
            return 0
        if arguments.action == "verify":
            verify_index(arguments)
            print("VERIFIED")
            return 0
        if arguments.action == "run-provenance-start":
            run_provenance_start(arguments)
            print(f"PENDING: {arguments.output}")
            return 0
        if arguments.action == "run-provenance-finish":
            run_provenance_finish(arguments)
            print(f"PROVENANCE: {arguments.output}")
            return 0
        if arguments.action == "component-state":
            component_state_command(arguments)
            print(f"COMPONENT: {arguments.output}")
            return 0
        if arguments.action == "test-contract":
            test_contract_command(arguments)
            return 0
        marker_command(arguments)
        return 0
    except EvidenceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

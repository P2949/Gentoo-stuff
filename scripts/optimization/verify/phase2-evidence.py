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


def read_regular(path: Path, label: str) -> tuple[bytes, dict[str, int]]:
    try:
        before = path.lstat()
    except OSError as error:
        fail(f"cannot inspect {label} {path}: {error}")
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
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


def file_identity(path: Path, label: str) -> dict[str, object]:
    payload, identity = read_regular(path, label)
    return {"path": os.fspath(path), "sha256": sha256(payload), "stat": identity}


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


def load_policy(repository: Path, policy_path: Path) -> dict[str, Any]:
    payload, _identity = read_regular(policy_path, "Phase 2 evidence policy")
    policy = require_object(
        parse_json_bytes(payload, "Phase 2 evidence policy"),
        "Phase 2 evidence policy",
        {
            "aggregate_requires_zero",
            "component_state_path_template",
            "index_path_template",
            "phase",
            "phase_heading",
            "phase_next_heading",
            "plan_claims",
            "plan_path",
            "prior_evidence_banner",
            "require_all_phase_checkboxes_checked",
            "required_component_states",
            "required_passing_test_names",
            "required_passing_test_prefixes",
            "required_sources",
            "required_test_mode",
            "required_tools",
            "schema",
            "source_scopes",
            "test_driver_path",
            "tool_manifest_template_path",
        },
    )
    if policy["schema"] != POLICY_SCHEMA or policy["phase"] != 2:
        fail("Phase 2 evidence policy has the wrong schema or phase")
    for key in ("aggregate_requires_zero", "require_all_phase_checkboxes_checked"):
        if not isinstance(policy[key], bool):
            fail(f"{key} must be boolean")
    for key in (
        "required_tools",
        "required_passing_test_names",
        "required_passing_test_prefixes",
    ):
        values = [require_string(item, f"{key} item") for item in require_list(policy[key], key, nonempty=True)]
        if values != sorted(set(values)):
            fail(f"{key} must be sorted and unique")
        if key == "required_tools":
            for value in values:
                require_string(value, f"{key} item", NAME_RE)
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
    for key in ("plan_path", "test_driver_path", "tool_manifest_template_path"):
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
    for required in policy["required_sources"]:
        if not (repository / required).exists():
            fail(f"required source is absent: {required}")
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
        item = require_object(raw, f"tools[{index}]", {"name", "path", "version_args"})
        name = require_string(item["name"], f"tools[{index}].name", NAME_RE)
        path_value = absolute_path(item["path"], f"tools[{index}].path")
        args = [require_string(arg, f"tools[{index}].version_args item") for arg in require_list(item["version_args"], f"tools[{index}].version_args", nonempty=True)]
        tools.append({"name": name, "path": path_value, "version_args": args})
        names.append(name)
    if names != sorted(set(names)) or names != required_names:
        fail(f"tool manifest names must exactly equal policy names: {required_names!r}")
    return tools


def observe_tool(specification: dict[str, Any], production: bool) -> dict[str, object]:
    name = specification["name"]
    requested: Path = specification["path"]
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve tool {name}: {error}")
    if not resolved.is_absolute() or not resolved.is_file() or not os.access(resolved, os.X_OK):
        fail(f"tool {name} does not resolve to an executable regular file")
    if production:
        validate_root_trust(resolved, f"tool {name}")
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
    if result.returncode != 0:
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
    return {
        "name": name,
        "requested_path": os.fspath(requested),
        "resolved_path": os.fspath(resolved),
        "binary": binary,
        "shebang": shebang,
        "version_argv": argv,
        "stdout": {"text": stdout, "sha256": sha256(result.stdout)},
        "stderr": {"text": stderr, "sha256": sha256(result.stderr)},
    }


def git_command(git: Path, repository: Path, arguments: Sequence[str]) -> bytes:
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
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        fail(f"Git inspection exited {result.returncode}: {detail}")
    return result.stdout


def repository_identity(git: Path, repository: Path) -> dict[str, object]:
    commit = git_command(git, repository, ["rev-parse", "--verify", "HEAD^{commit}"]).decode().strip()
    tree = git_command(git, repository, ["show", "-s", "--format=%T", "HEAD"]).decode().strip()
    if OID_RE.fullmatch(commit) is None or OID_RE.fullmatch(tree) is None:
        fail("Git returned an invalid commit or tree identity")
    status_output = git_command(git, repository, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if status_output:
        fail("authoritative Phase 2 evidence requires a clean worktree")
    reference_result = subprocess.run(
        [os.fspath(git), "--no-pager", "-c", f"safe.directory={repository}", "-C", os.fspath(repository), "symbolic-ref", "-q", "HEAD"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    if reference_result.returncode not in {0, 1}:
        fail("cannot determine the Git HEAD reference")
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


def parse_results(
    results_path: Path, summary_path: Path, policy: dict[str, Any]
) -> tuple[dict[str, int], list[str], str, dict[str, dict[str, str]]]:
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
    required_summary = {"mode", "pass", "fail", "skip", "total", "exit_status", "external_authority_index_preserved", "results"}
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
    if summary["mode"] != policy["required_test_mode"]:
        fail("test summary mode differs from the evidence policy")
    if Path(summary["results"]) != results_path:
        fail("test summary results path differs from the indexed results path")
    return expected_counts, sorted(required_passes), summary["mode"], rows


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
            path, f"component {name} external evidence {evidence_label}"
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
    identity = repository_identity(git, repository)
    document = {
        "schema": "gentoo-optimization-phase2-test-run-pending-v1",
        "started_at": utc_now(),
        "boot_id": current_boot_id(),
        "repository": identity,
        "driver": file_identity(driver, "test driver"),
        "git_requested_path": os.fspath(git_raw),
        "git_resolved_path": os.fspath(git),
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
    summary_path: Path,
    git_requested_path: Path,
    git_resolved_path: Path,
    expected_boot_id: str,
    production: bool,
) -> dict[str, Any]:
    if production:
        validate_root_trust(path, "test-run provenance")
    payload, _identity = read_regular(path, "test-run provenance")
    document = require_object(
        parse_json_bytes(payload, "test-run provenance"),
        "test-run provenance",
        {
            "boot_id",
            "completed_at",
            "driver",
            "git_requested_path",
            "git_resolved_path",
            "repository",
            "results",
            "schema",
            "started_at",
            "summary",
        },
    )
    if document["schema"] != "gentoo-optimization-phase2-test-run-provenance-v1":
        fail("test-run provenance schema is invalid")
    started = parse_timestamp(document["started_at"], "test-run started_at")
    completed = parse_timestamp(document["completed_at"], "test-run completed_at")
    if completed < started:
        fail("test-run completion predates its start")
    if require_string(document["boot_id"], "test-run boot_id") != expected_boot_id:
        fail("test-run provenance boot identity differs from the required boot")
    if document["repository"] != repository:
        fail("test-run provenance belongs to another commit/tree/source listing")
    driver = require_object(document["driver"], "test-run provenance driver")
    driver_path = Path(str(repository["root"])) / str(driver_source["path"])
    if driver != file_identity(driver_path, "test-run provenance driver"):
        fail("test-run provenance driver differs from the indexed source")
    recorded_requested, recorded_resolved = validate_recorded_git_paths(
        document["git_requested_path"], document["git_resolved_path"]
    )
    if recorded_requested != git_requested_path or recorded_resolved != git_resolved_path:
        fail("test-run provenance Git entry point differs from the indexed tool")
    if document["results"] != file_identity(results_path, "test results"):
        fail("test-run provenance results identity is stale")
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
            "boot_id",
            "driver",
            "git_requested_path",
            "git_resolved_path",
            "repository",
            "schema",
            "started_at",
        },
    )
    if pending["schema"] != "gentoo-optimization-phase2-test-run-pending-v1":
        fail("test-run pending provenance schema is invalid")
    parse_timestamp(pending["started_at"], "pending started_at")
    if require_string(pending["boot_id"], "pending boot_id") != current_boot_id():
        fail("test run cannot be finalized after a reboot")
    repository_record = require_object(pending["repository"], "pending repository")
    repository = absolute_path(repository_record.get("root"), "pending repository root")
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
    summary = absolute_path(arguments.summary, "test summary")
    document = {
        "schema": "gentoo-optimization-phase2-test-run-provenance-v1",
        "started_at": pending["started_at"],
        "completed_at": utc_now(),
        "boot_id": pending["boot_id"],
        "repository": repository_record,
        "driver": driver_record,
        "git_requested_path": pending["git_requested_path"],
        "git_resolved_path": pending["git_resolved_path"],
        "results": file_identity(results, "test results"),
        "summary": file_identity(summary, "test summary"),
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
    summary_path = absolute_path(arguments.summary, "test summary")
    for path, label in (
        (provenance_path, "test-run provenance"),
        (results_path, "test results"),
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
        reviewed_git = next(item for item in tools if item["name"] == "git")
        if git_requested != reviewed_git["path"]:
            fail("production component generation requires the reviewed Git entry point")
        validate_root_trust(git_resolved, "Git executable")
    repository_record = repository_identity(git_resolved, repository)
    sources = source_inventory(git_resolved, repository, policy)
    source_by_path = {str(item["path"]): item for item in sources}
    driver_source = source_by_path[policy["test_driver_path"]]
    _totals, _required, _mode, rows = parse_results(
        results_path, summary_path, policy
    )
    validate_run_provenance(
        provenance_path,
        repository_record,
        driver_source,
        results_path,
        summary_path,
        git_requested,
        git_resolved,
        current_boot_id(),
        arguments.production,
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
    test_summary = absolute_path(arguments.test_summary, "test summary")
    for path, label in ((tools_path, "tool manifest"), (test_results, "test results"), (test_summary, "test summary")):
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
    git_record = next(item for item in tools if item["name"] == "git")
    git = Path(str(git_record["resolved_path"]))
    before_repository = repository_identity(git, repository)
    sources = source_inventory(git, repository, policy)
    hashes = source_hashes(sources)
    plan_hash, claims = plan_claims(repository / policy["plan_path"], policy, hashes)
    totals, required_passes, mode, rows = parse_results(
        test_results, test_summary, policy
    )
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
        test_summary,
        Path(str(git_record["requested_path"])),
        git,
        current_boot_id(),
        arguments.production,
    )
    states, aggregate = component_states(
        arguments.component_state,
        run_id,
        policy,
        before_repository,
        provenance_path,
        test_results,
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
            "driver": {
                "path": policy["test_driver_path"],
                "sha256": test_driver["sha256"],
                "stat": stat_identity((repository / policy["test_driver_path"]).lstat()),
            },
            "results": file_identity(test_results, "test results"),
            "summary": file_identity(test_summary, "test summary"),
            "provenance": file_identity(provenance_path, "test-run provenance"),
            "boot_id": provenance["boot_id"],
            "mode": mode,
            "totals": {"pass": totals["pass"], "fail": totals["fail"], "skip": totals["skip"], "total": totals["total"]},
            "required_passes": required_passes,
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
    tools_raw = require_list(document["tools"], "tools", nonempty=True)
    tool_specs: list[dict[str, Any]] = []
    for raw in tools_raw:
        item = require_object(raw, "tool identity")
        name = require_string(item.get("name"), "tool name", NAME_RE)
        requested = absolute_path(item.get("requested_path"), f"tool {name} requested_path")
        argv = require_list(item.get("version_argv"), f"tool {name} version_argv", nonempty=True)
        if len(argv) < 2:
            fail(f"tool {name} version argv is incomplete")
        args = [require_string(value, f"tool {name} version argv") for value in argv[1:]]
        tool_specs.append({"name": name, "path": requested, "version_args": args})
    observed_tools = [observe_tool(item, production) for item in tool_specs]
    if observed_tools != tools_raw:
        fail("one or more tool identities changed after evidence capture")
    git_record = next((item for item in observed_tools if item["name"] == "git"), None)
    if git_record is None:
        fail("evidence index has no Git tool identity")
    git = Path(str(git_record["resolved_path"]))
    repository_now = repository_identity(git, repository)
    if repository_now != repository_record:
        fail("repository commit, tree, reference, or clean status changed")
    policy_record = require_object(document["policy"], "policy identity")
    policy_path = absolute_path(policy_record.get("path"), "policy path")
    if production and policy_path != repository / POLICY_RELATIVE:
        fail("production index does not bind the tracked repository evidence policy")
    if file_identity(policy_path, "Phase 2 evidence policy") != policy_record:
        fail("Phase 2 evidence policy changed")
    policy = load_policy(repository, policy_path)
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
    summary_record = require_object(test_record.get("summary"), "test summary identity")
    results_path = absolute_path(results_record.get("path"), "test results path")
    summary_path = absolute_path(summary_record.get("path"), "test summary path")
    totals, required_passes, mode, rows = parse_results(
        results_path, summary_path, policy
    )
    if file_identity(results_path, "test results") != results_record or file_identity(summary_path, "test summary") != summary_record:
        fail("test results or summary identity changed")
    expected_test_totals = {"pass": totals["pass"], "fail": totals["fail"], "skip": totals["skip"], "total": totals["total"]}
    if test_record.get("mode") != mode or test_record.get("totals") != expected_test_totals or test_record.get("required_passes") != required_passes:
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
    validate_run_provenance(
        provenance_path,
        repository_now,
        driver_source,
        results_path,
        summary_path,
        Path(str(git_record["requested_path"])),
        git,
        recorded_boot_id,
        production,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    capture = subparsers.add_parser("capture", help="create one immutable Phase 2 evidence index")
    capture.add_argument("--repository-root", required=True)
    capture.add_argument("--policy", default="optimization/phase2-evidence-policy.json")
    capture.add_argument("--evidence-root", required=True)
    capture.add_argument("--tools", required=True)
    capture.add_argument("--test-results", required=True)
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
    provenance_start.add_argument("--git", default="/usr/bin/git")
    provenance_start.add_argument("--output", required=True)

    provenance_finish = subparsers.add_parser(
        "run-provenance-finish",
        help="bind final results and summary to a pending test run",
    )
    provenance_finish.add_argument("--pending", required=True)
    provenance_finish.add_argument("--results", required=True)
    provenance_finish.add_argument("--summary", required=True)
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
    component.add_argument("--summary", required=True)
    component.add_argument("--external-evidence", action="append", default=[])
    component.add_argument("--git", default="/usr/bin/git")
    component.add_argument("--output", required=True)
    component.add_argument("--production", action="store_true")
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
        marker_command(arguments)
        return 0
    except EvidenceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

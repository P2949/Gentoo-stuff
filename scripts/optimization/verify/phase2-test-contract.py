#!/usr/bin/env python3
"""Generate or check the exact Phase 2 test topology without running tests."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from typing import Any


SCHEMA = "gentoo-optimization-phase2-authoritative-test-contract-v1"
TEST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,511}$")
DISCOVERY_TIMEOUT_SECONDS = 30
DISCOVERY_KILL_AFTER_SECONDS = 2


class ContractError(RuntimeError):
    pass


def regular_file(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ContractError(f"cannot inspect {label} {path}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ContractError(f"{label} must be a regular file: {path}")
    return path


def repository_path(raw: str) -> Path:
    path = Path(raw)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ContractError(f"cannot resolve repository root {path}: {error}") from error
    if not resolved.is_dir():
        raise ContractError(f"repository root is not a directory: {resolved}")
    return resolved


def process_group_has_live_members(pgid: int) -> bool:
    """Return conservatively whether a Linux process group can still execute."""
    proc = Path("/proc")
    try:
        entries = list(proc.iterdir())
    except OSError:
        return True
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            line = (entry / "stat").read_bytes()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        separator = line.rfind(b") ")
        if separator < 0:
            return True
        fields = line[separator + 2 :].split()
        if len(fields) < 3:
            return True
        state = fields[0]
        try:
            member_pgid = int(fields[2])
        except ValueError:
            return True
        if member_pgid == pgid and state not in {b"Z", b"X", b"x"}:
            return True
    return False


def signal_process_group(pgid: int, requested_signal: signal.Signals) -> None:
    try:
        os.killpg(pgid, requested_signal)
    except ProcessLookupError:
        return
    except OSError as error:
        raise ContractError(
            f"cannot signal discovery process group {pgid} with "
            f"{requested_signal.name}: {error}"
        ) from error


def quiesce_process_group(
    process: subprocess.Popen[str], label: str
) -> None:
    """Boundedly terminate and reap one start_new_session discovery tree."""
    pgid = process.pid
    signal_process_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + DISCOVERY_KILL_AFTER_SECONDS
    while time.monotonic() < deadline:
        if not process_group_has_live_members(pgid):
            break
        time.sleep(0.02)
    if process_group_has_live_members(pgid):
        signal_process_group(pgid, signal.SIGKILL)
        deadline = time.monotonic() + DISCOVERY_KILL_AFTER_SECONDS
        while time.monotonic() < deadline:
            if not process_group_has_live_members(pgid):
                break
            time.sleep(0.02)
    try:
        process.wait(timeout=DISCOVERY_KILL_AFTER_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise ContractError(f"cannot reap {label} after forced termination") from error
    if process_group_has_live_members(pgid):
        raise ContractError(f"{label} left a live process-group member after SIGKILL")


def run_checked(arguments: list[str], repository: Path, label: str) -> str:
    child_environment = os.environ.copy()
    for variable in ("BASH_ENV", "CDPATH", "ENV", "PYTHONHOME", "PYTHONPATH"):
        child_environment.pop(variable, None)
    child_environment.update(
        {
            "GENTOO_OPT_AUTHORITATIVE": "0",
            "OPTIMIZATION_TEST_CAPABILITIES": "",
            "OPTIMIZATION_TEST_MODE": "smoke",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    process = subprocess.Popen(
            arguments,
            cwd=repository,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_environment,
            start_new_session=True,
        )
    try:
        stdout, stderr = process.communicate(timeout=DISCOVERY_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        quiesce_process_group(process, label)
        raise ContractError(
            f"{label} exceeded the bounded {DISCOVERY_TIMEOUT_SECONDS}-second discovery deadline"
        ) from error
    if process_group_has_live_members(process.pid):
        quiesce_process_group(process, label)
        raise ContractError(f"{label} left a live process-group member after exit")
    if process.returncode != 0:
        diagnostic = " ".join(stderr.splitlines()).strip()
        raise ContractError(
            f"{label} failed with exit {process.returncode}: "
            f"{diagnostic or 'no diagnostic'}"
        )
    return stdout


def driver_topology(
    repository: Path,
) -> tuple[list[str], list[dict[str, str]], list[str]]:
    driver = regular_file(repository / "tests/run-optimization-tests.sh", "test driver")
    output = run_checked(
        ["bash", os.fspath(driver), "--contract-topology"],
        repository,
        "driver topology discovery",
    )
    top_level: list[str] = []
    suites: list[dict[str, str]] = []
    shell_names: list[str] = []
    for line_number, line in enumerate(output.splitlines(), 1):
        fields = line.split("\t")
        if len(fields) == 2 and fields[0] == "top-level":
            name = fields[1]
            if not TEST_NAME_RE.fullmatch(name):
                raise ContractError(
                    f"invalid top-level identity at topology line {line_number}: {name!r}"
                )
            top_level.append(name)
        elif len(fields) == 2 and fields[0] == "shell":
            name = fields[1]
            if (
                not name.startswith("bash-syntax:")
                or not TEST_NAME_RE.fullmatch(name)
            ):
                raise ContractError(
                    f"invalid shell identity at topology line {line_number}: {name!r}"
                )
            relative = Path(name.removeprefix("bash-syntax:"))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ContractError(f"unsafe shell source identity: {name!r}")
            source = regular_file(repository / relative, "shell source")
            if source.is_symlink():
                raise ContractError(f"shell source must not be a symlink: {source}")
            shell_names.append(name)
        elif len(fields) == 6 and fields[0] == "unittest":
            _kind, name, start, pattern, exclusion, name_pattern = fields
            if not TEST_NAME_RE.fullmatch(name):
                raise ContractError(
                    f"invalid unittest suite identity at topology line {line_number}: {name!r}"
                )
            relative = Path(start)
            if relative.is_absolute() or ".." in relative.parts or not start:
                raise ContractError(f"unsafe unittest start directory: {start!r}")
            suites.append(
                {
                    "test": name,
                    "start": start,
                    "pattern": pattern,
                    "exclude": exclusion,
                    "name_pattern": name_pattern,
                }
            )
        else:
            raise ContractError(
                f"malformed driver topology line {line_number}: {line!r}"
            )
    if not top_level or not suites or not shell_names:
        raise ContractError("driver topology discovery returned an empty topology")
    if len(top_level) != len(set(top_level)):
        raise ContractError("driver topology contains duplicate top-level identities")
    suite_names = [suite["test"] for suite in suites]
    if len(suite_names) != len(set(suite_names)):
        raise ContractError("driver topology contains duplicate unittest suite identities")
    if len(shell_names) != len(set(shell_names)):
        raise ContractError("driver topology contains duplicate shell identities")
    if not set(suite_names).issubset(top_level):
        raise ContractError("a unittest suite is absent from the top-level topology")
    return (
        sorted(top_level),
        sorted(suites, key=lambda item: item["test"]),
        sorted(shell_names),
    )


def unittest_identities(repository: Path, suite: dict[str, str]) -> list[str]:
    runner = regular_file(
        repository / "scripts/optimization/verify/run-unittest-suite.py",
        "unittest identity runner",
    )
    arguments = [
        sys.executable,
        "-I",
        "-B",
        os.fspath(runner),
        "--list-identities",
    ]
    if suite["exclude"]:
        arguments.extend(("--exclude-id-prefix", suite["exclude"]))
    arguments.extend(
        (
            "discover",
            "-s",
            suite["start"],
            "-p",
            suite["pattern"],
        )
    )
    if suite["name_pattern"]:
        arguments.extend(("-k", suite["name_pattern"]))
    output = run_checked(
        arguments,
        repository,
        f"unittest identity discovery for {suite['test']}",
    )
    raw_names = output.splitlines()
    if not raw_names:
        raise ContractError(f"unittest suite selected zero identities: {suite['test']}")
    if len(raw_names) != len(set(raw_names)):
        raise ContractError(f"unittest suite has duplicate identities: {suite['test']}")
    names = sorted(f"python.{name}" for name in raw_names)
    for name in names:
        if not TEST_NAME_RE.fullmatch(name):
            raise ContractError(
                f"unittest suite {suite['test']} emitted an invalid identity: {name!r}"
            )
    return names


def identity_hash(names: list[str]) -> str:
    payload = ("\n".join(sorted(names)) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    regular_file(path, "authoritative test contract")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot parse authoritative test contract {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ContractError("authoritative test contract has the wrong schema")
    return value


def generated_contract(repository: Path, contract: dict[str, Any]) -> dict[str, Any]:
    exact_names, suites, shell_names = driver_topology(repository)
    generated_suites: list[dict[str, object]] = []
    for suite in suites:
        names = unittest_identities(repository, suite)
        generated_suites.append(
            {
                "expected_count": len(names),
                "subtest_names_sha256": identity_hash(names),
                "test": suite["test"],
            }
        )
    result: dict[str, Any] = json.loads(json.dumps(contract))
    result["top_level"] = {
        "exact_names": exact_names,
        "prefix_groups": [
            {
                "expected_count": len(shell_names),
                "expected_names": shell_names,
                "prefix": "bash-syntax:",
            }
        ],
    }
    result["unittest_suites"] = generated_suites
    return result


def canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_output(path: Path | None, payload: str) -> None:
    if path is None:
        sys.stdout.write(payload)
        return
    if path.exists() or path.is_symlink():
        raise ContractError(f"refusing to replace existing output: {path}")
    path.write_text(payload, encoding="utf-8")


def main() -> int:
    global DISCOVERY_TIMEOUT_SECONDS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("generate", "check"))
    parser.add_argument("--repository-root", default=".")
    parser.add_argument(
        "--contract",
        default="optimization/phase2-authoritative-test-contract.json",
    )
    parser.add_argument("--output")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    arguments = parser.parse_args()
    try:
        if arguments.timeout_seconds < 1 or arguments.timeout_seconds > 300:
            raise ContractError("--timeout-seconds must be in the range 1..300")
        DISCOVERY_TIMEOUT_SECONDS = arguments.timeout_seconds
        repository = repository_path(arguments.repository_root)
        contract_path = Path(arguments.contract)
        if not contract_path.is_absolute():
            contract_path = repository / contract_path
        current = load_contract(contract_path)
        generated = generated_contract(repository, current)
        generated_text = canonical(generated)
        if arguments.action == "generate":
            output = Path(arguments.output) if arguments.output else None
            write_output(output, generated_text)
            return 0
        if arguments.output:
            raise ContractError("--output is valid only with generate")
        current_text = canonical(current)
        if current_text != generated_text:
            sys.stderr.writelines(
                difflib.unified_diff(
                    current_text.splitlines(keepends=True),
                    generated_text.splitlines(keepends=True),
                    fromfile=os.fspath(contract_path),
                    tofile="discovered-test-contract",
                )
            )
            print(
                "ERROR: authoritative test contract differs from deterministic discovery",
                file=sys.stderr,
            )
            return 1
        print("PASS: authoritative test contract matches deterministic discovery")
        return 0
    except (ContractError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

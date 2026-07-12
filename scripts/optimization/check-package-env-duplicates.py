#!/usr/bin/env python3
"""Validate exact, fail-closed Gentoo package.env policy."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKAGE_ENV_ROOT = REPOSITORY_ROOT / "portage" / "package.env"
DEFAULT_ENV_ROOT = REPOSITORY_ROOT / "portage" / "env"
DEFAULT_POLICY_FILE = REPOSITORY_ROOT / "portage" / "package-env-policy.json"
VCS_DIRECTORY_NAMES = frozenset({"CVS", "RCS", "SCCS", ".bzr", ".git", ".hg", ".svn"})
TOOL_KEYS = ("CC", "CXX", "CPP", "LD", "AR", "NM", "RANLIB")
FALLBACK_ATOM_RE = re.compile(
    r"^(?:[<>=~]{0,2})?[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+"
    r"(?:-[0-9][A-Za-z0-9+_.-]*)?(?::[A-Za-z0-9+_./-]+)?"
    r"(?:\[[^\]]+\])?(?:::[A-Za-z0-9+_.-]+)?$"
)
VARIABLE_ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


@dataclass(frozen=True)
class PolicyLine:
    atom: str
    environments: tuple[str, ...]
    path: Path
    line_number: int


@dataclass(frozen=True)
class Assignment:
    atom: str
    environment: str
    path: Path
    line_number: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.atom, self.environment)


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]
    notices: tuple[str, ...]
    portage_semantic_status: str
    portage_semantic_reason: str
    policy_file_count: int
    assignment_line_count: int
    atom_count: int
    pair_count: int

    @property
    def ok(self) -> bool:
        return not self.errors


class PolicyError(Exception):
    """A package.env policy file cannot be validated."""


def lines_from_file(path: Path) -> Iterator[PolicyLine]:
    """Yield active package.env lines, ignoring comments and blank lines."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise PolicyError(f"cannot read {path}: {error}") from error

    for line_number, raw_line in enumerate(lines, start=1):
        try:
            fields = shlex.split(raw_line, comments=True, posix=True)
        except ValueError as error:
            raise PolicyError(f"{path}:{line_number}: {error}") from error
        if not fields:
            continue
        if len(fields) < 2:
            raise PolicyError(
                f"{path}:{line_number}: package.env assignment has no environment file"
            )
        atom, *environments = fields
        yield PolicyLine(atom, tuple(environments), path, line_number)


def assignments_from_file(path: Path) -> Iterator[Assignment]:
    """Yield every atom/environment pair from one package.env policy file."""

    for line in lines_from_file(path):
        for environment in line.environments:
            yield Assignment(line.atom, environment, line.path, line.line_number)


def is_policy_basename(name: str) -> bool:
    """Match Portage's recursive configuration-file basename filter."""

    return not name.startswith(".") and not name.endswith("~")


def policy_files(package_env_root: Path) -> list[Path]:
    """Return recursively stacked policy files in deterministic Portage order."""

    try:
        entries = list(package_env_root.rglob("*"))
    except OSError as error:
        raise PolicyError(f"cannot enumerate {package_env_root}: {error}") from error
    paths = sorted(
        path
        for path in entries
        if path.is_file()
        and all(
            is_policy_basename(part) and part not in VCS_DIRECTORY_NAMES
            for part in path.relative_to(package_env_root).parts
        )
    )
    if not paths:
        raise PolicyError(f"no package.env policy files found below {package_env_root}")
    return paths


def all_policy_lines(paths: Iterable[Path]) -> list[PolicyLine]:
    return [line for path in sorted(paths) for line in lines_from_file(path)]


def exact_environment_map(lines: Iterable[PolicyLine]) -> dict[str, tuple[str, ...]]:
    environments: dict[str, list[str]] = {}
    for line in lines:
        environments.setdefault(line.atom, []).extend(line.environments)
    return {atom: tuple(values) for atom, values in environments.items()}


def duplicate_assignments(
    paths: Iterable[Path],
) -> list[tuple[Assignment, Assignment]]:
    """Return repeated exact atom/environment pairs and their first occurrence."""

    first_by_key: dict[tuple[str, str], Assignment] = {}
    duplicates: list[tuple[Assignment, Assignment]] = []
    for path in sorted(paths):
        for assignment in assignments_from_file(path):
            first = first_by_key.get(assignment.key)
            if first is None:
                first_by_key[assignment.key] = assignment
            else:
                duplicates.append((first, assignment))
    return duplicates


def display_location(assignment: Assignment, package_env_root: Path) -> str:
    try:
        path = assignment.path.relative_to(package_env_root.parent)
    except ValueError:
        path = assignment.path
    return f"{path}:{assignment.line_number}"


def format_duplicates(
    duplicates: Iterable[tuple[Assignment, Assignment]], package_env_root: Path
) -> str:
    lines = []
    for first, repeated in duplicates:
        lines.append(
            f"{repeated.atom} {repeated.environment}: first at "
            f"{display_location(first, package_env_root)}, repeated at "
            f"{display_location(repeated, package_env_root)}"
        )
    return "\n".join(lines)


def load_json_policy(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"cannot load reviewed policy {path}: {error}") from error
    if not isinstance(value, dict):
        raise PolicyError(f"reviewed policy {path} must contain one JSON object")
    if value.get("schema_version") != 1:
        raise PolicyError(f"reviewed policy {path} has unsupported schema_version")
    return value


def require_mapping(policy: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = policy.get(key)
    if not isinstance(value, dict):
        raise PolicyError(f"reviewed policy key {key!r} must be an object")
    return value


def require_sequence(policy: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = policy.get(key)
    if not isinstance(value, list):
        raise PolicyError(f"reviewed policy key {key!r} must be an array")
    return value


def nonempty_reason(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_safe_environment_name(environment: str) -> bool:
    if not environment or "\\" in environment:
        return False
    pure = PurePosixPath(environment)
    return (
        not pure.is_absolute()
        and all(part not in {"", ".", ".."} for part in pure.parts)
        and pure.as_posix() == environment
    )


def is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def environment_reference_errors(
    lines: Iterable[PolicyLine], env_root: Path
) -> list[str]:
    errors: list[str] = []
    try:
        resolved_root = env_root.resolve(strict=True)
    except OSError as error:
        return [f"environment root is unavailable: {env_root}: {error}"]
    if not resolved_root.is_dir():
        return [f"environment root is not a directory: {resolved_root}"]

    checked: set[str] = set()
    for line in lines:
        for environment in line.environments:
            location = f"{line.path}:{line.line_number}"
            if not is_safe_environment_name(environment):
                errors.append(f"{location}: unsafe environment path: {environment!r}")
                continue
            if environment in checked:
                continue
            checked.add(environment)
            candidate = env_root / environment
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as error:
                errors.append(
                    f"{location}: referenced environment is unavailable: "
                    f"{environment}: {error}"
                )
                continue
            if not is_below(resolved, resolved_root):
                errors.append(
                    f"{location}: environment escapes env root: {environment} -> {resolved}"
                )
            elif not resolved.is_file():
                errors.append(
                    f"{location}: referenced environment is not a regular file: {environment}"
                )
    return errors


def fallback_atom_validator(atom: str) -> None:
    if not FALLBACK_ATOM_RE.fullmatch(atom):
        raise ValueError("does not match the fallback category/package atom grammar")


def portage_atom_validator() -> Callable[[str], None] | None:
    try:
        from portage.dep import Atom  # type: ignore[import-untyped]
    except ImportError:
        return None

    def validate(atom: str) -> None:
        Atom(
            atom,
            allow_wildcard=True,
            allow_repo=True,
            allow_build_id=True,
        )

    return validate


def atom_syntax_errors(
    atoms: Iterable[str], validator: Callable[[str], None]
) -> list[str]:
    errors: list[str] = []
    for atom in atoms:
        try:
            validator(atom)
        except Exception as error:  # Portage exposes multiple InvalidAtom variants.
            errors.append(f"invalid package atom {atom!r}: {error}")
    return errors


def portage_match_map(atoms: Iterable[str]) -> tuple[dict[str, set[str]] | None, str]:
    try:
        import portage
    except ImportError:
        return None, "Portage is unavailable; installed/repository atom matching skipped"
    try:
        # Portage exposes these runtime-populated attributes without static
        # typing metadata. Resolve that dynamic boundary explicitly so a plain
        # mypy invocation remains strict everywhere else in this checker.
        portage_db = getattr(portage, "db")
        portage_root = getattr(portage, "root")
        installed_db = portage_db[portage_root]["vartree"].dbapi
        available_db = portage_db[portage_root]["porttree"].dbapi
        matches: dict[str, set[str]] = {}
        for atom in atoms:
            matches[atom] = set(installed_db.match(atom)) | set(available_db.match(atom))
    except Exception as error:
        return None, f"Portage package databases are unavailable; atom matching skipped: {error}"
    return matches, ""


def validate_unmatched_atoms(
    exact_map: Mapping[str, tuple[str, ...]],
    matches: Mapping[str, set[str]],
    allowed: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    for atom in exact_map:
        reason = allowed.get(atom)
        if not matches.get(atom):
            if not nonempty_reason(reason):
                errors.append(
                    f"package atom matches no installed or available CPV: {atom}"
                )
        elif atom in allowed:
            errors.append(f"stale unmatched-atom exception now has a match: {atom}")
    for atom, reason in allowed.items():
        if atom not in exact_map:
            errors.append(f"stale unmatched-atom exception has no assignment: {atom}")
        if not nonempty_reason(reason):
            errors.append(f"unmatched-atom exception lacks a rationale: {atom}")
    return errors


def normalized_overlap_allowlist(
    values: Sequence[Any],
) -> tuple[dict[frozenset[str], str], list[str]]:
    allowed: dict[frozenset[str], str] = {}
    errors: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            errors.append(f"effective overlap entry {index} must be an object")
            continue
        atoms = value.get("atoms")
        reason = value.get("rationale")
        if (
            not isinstance(atoms, list)
            or len(atoms) < 2
            or not all(isinstance(atom, str) and atom for atom in atoms)
            or len(set(atoms)) != len(atoms)
        ):
            errors.append(
                f"effective overlap entry {index} needs at least two unique atom strings"
            )
            continue
        if not nonempty_reason(reason):
            errors.append(f"effective overlap entry {index} lacks a rationale")
            continue
        key = frozenset(atoms)
        if key in allowed:
            errors.append(f"duplicate effective overlap allowlist entry: {sorted(key)}")
        else:
            allowed[key] = str(reason).strip()
    return allowed, errors


def validate_effective_overlaps(
    exact_map: Mapping[str, tuple[str, ...]],
    matches: Mapping[str, set[str]],
    allowed_values: Sequence[Any],
    compiler_profiles: Mapping[str, Any],
) -> list[str]:
    allowed, errors = normalized_overlap_allowlist(allowed_values)
    cpv_atoms: dict[str, list[str]] = defaultdict(list)
    for atom in exact_map:
        for cpv in matches.get(atom, set()):
            cpv_atoms[cpv].append(atom)

    observed: set[frozenset[str]] = set()
    for cpv, atoms in sorted(cpv_atoms.items()):
        unique_atoms = list(dict.fromkeys(atoms))
        if len(unique_atoms) < 2:
            continue
        key = frozenset(unique_atoms)
        observed.add(key)
        environments = [
            environment
            for atom in unique_atoms
            for environment in exact_map[atom]
        ]
        repeats = sorted(
            environment
            for environment, count in Counter(environments).items()
            if count > 1
        )
        if repeats:
            errors.append(
                f"effective atom overlap repeats environments for {cpv}: "
                f"atoms={sorted(key)}, repeated={repeats}"
            )
        if key not in allowed:
            errors.append(
                f"unreviewed effective atom overlap for {cpv}: {sorted(key)}"
            )
        lanes = {
            entry["lane"]
            for environment in environments
            if isinstance((entry := compiler_profiles.get(environment)), dict)
            and isinstance(entry.get("lane"), str)
        }
        if len(lanes) > 1:
            errors.append(
                f"conflicting compiler lanes for effective overlap {cpv}: {sorted(lanes)}"
            )

    for key in allowed:
        if key not in observed:
            errors.append(f"stale effective atom overlap allowlist entry: {sorted(key)}")
    return errors


def validate_multi_environment_allowlist(
    exact_map: Mapping[str, tuple[str, ...]], reviewed: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    observed = {atom: values for atom, values in exact_map.items() if len(values) > 1}
    for atom, environments in observed.items():
        entry = reviewed.get(atom)
        if not isinstance(entry, dict):
            errors.append(
                f"unreviewed multi-environment stack: {atom}: {' '.join(environments)}"
            )
            continue
        expected = entry.get("environments")
        if expected != list(environments):
            errors.append(
                f"reviewed stack mismatch for {atom}: expected={expected!r}, "
                f"actual={list(environments)!r}"
            )
        if not nonempty_reason(entry.get("rationale")):
            errors.append(f"reviewed stack lacks a rationale: {atom}")

    for atom, entry in reviewed.items():
        if atom not in observed:
            errors.append(f"stale reviewed multi-environment stack: {atom}")
        if not isinstance(entry, dict):
            errors.append(f"reviewed stack entry must be an object: {atom}")
    return errors


def environment_variable_assignments(path: Path) -> dict[str, str]:
    """Read literal assignment values needed for marker and tool identity checks."""

    try:
        physical_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise PolicyError(f"cannot read environment {path}: {error}") from error
    lines: list[str] = []
    pending = ""
    for physical_line in physical_lines:
        stripped = physical_line.rstrip()
        if stripped.endswith("\\") and not stripped.endswith("\\\\"):
            pending += stripped[:-1] + " "
            continue
        lines.append(pending + physical_line)
        pending = ""
    if pending:
        raise PolicyError(f"{path}: unterminated backslash continuation")

    assignments: dict[str, str] = {}
    for raw_line in lines:
        match = VARIABLE_ASSIGNMENT_RE.match(raw_line)
        if not match or raw_line.lstrip().startswith("#"):
            continue
        key, raw_value = match.groups()
        try:
            values = shlex.split(raw_value, comments=True, posix=True)
        except ValueError as error:
            raise PolicyError(f"{path}: cannot parse {key}: {error}") from error
        assignments[key] = " ".join(values)
    return assignments


def compile_patterns(values: Sequence[Any], key: str) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value:
            raise PolicyError(f"{key}[{index}] must be a nonempty regex string")
        try:
            patterns.append(re.compile(value))
        except re.error as error:
            raise PolicyError(f"invalid regex in {key}[{index}]: {error}") from error
    return patterns


def validate_forbidden_markers(
    lines: Iterable[PolicyLine],
    package_env_root: Path,
    env_root: Path,
    policy: Mapping[str, Any],
) -> list[str]:
    environment_patterns = compile_patterns(
        require_sequence(policy, "forbidden_environment_patterns"),
        "forbidden_environment_patterns",
    )
    policy_patterns = compile_patterns(
        require_sequence(policy, "forbidden_policy_file_patterns"),
        "forbidden_policy_file_patterns",
    )
    marker_values = require_sequence(policy, "forbidden_marker_variables")
    if not all(isinstance(value, str) and value for value in marker_values):
        raise PolicyError("forbidden_marker_variables must contain nonempty strings")
    marker_variables = set(marker_values)

    errors: list[str] = []
    environment_cache: dict[str, dict[str, str]] = {}
    for line in lines:
        relative_policy = line.path.relative_to(package_env_root).as_posix()
        if any(pattern.search(relative_policy) for pattern in policy_patterns):
            errors.append(
                f"active assignment is forbidden in recovery/generated policy file: "
                f"{relative_policy}:{line.line_number}"
            )
        for environment in line.environments:
            if any(pattern.search(environment) for pattern in environment_patterns):
                errors.append(
                    f"forbidden recovery/generated environment is active at "
                    f"{relative_policy}:{line.line_number}: {environment}"
                )
            if not is_safe_environment_name(environment):
                continue
            if environment not in environment_cache:
                candidate = env_root / environment
                try:
                    resolved_candidate = candidate.resolve(strict=True)
                    resolved_env_root = env_root.resolve(strict=True)
                except OSError:
                    resolved_candidate = None
                    resolved_env_root = None
                if (
                    resolved_candidate is not None
                    and resolved_env_root is not None
                    and is_below(resolved_candidate, resolved_env_root)
                    and resolved_candidate.is_file()
                ):
                    environment_cache[environment] = environment_variable_assignments(
                        resolved_candidate
                    )
                else:
                    environment_cache[environment] = {}
            found = sorted(marker_variables & environment_cache[environment].keys())
            if found:
                errors.append(
                    f"active environment {environment} sets forbidden stage markers: {found}"
                )
    return errors


def validate_compiler_profiles(
    exact_map: Mapping[str, tuple[str, ...]],
    env_root: Path,
    configured: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    referenced = {environment for values in exact_map.values() for environment in values}
    discovered: dict[str, dict[str, str]] = {}
    for environment in sorted(referenced):
        path = env_root / environment
        if not path.is_file() or not is_safe_environment_name(environment):
            continue
        assignments = environment_variable_assignments(path)
        tools = {key: assignments[key] for key in TOOL_KEYS if key in assignments}
        if tools:
            discovered[environment] = tools

    for environment in sorted(discovered.keys() - configured.keys()):
        errors.append(
            f"compiler-selecting environment lacks a reviewed lane: {environment}"
        )
    for environment in sorted(configured.keys() - discovered.keys()):
        errors.append(
            f"reviewed compiler profile is absent, unreferenced, or assigns no tools: "
            f"{environment}"
        )

    for environment in sorted(configured.keys() & discovered.keys()):
        entry = configured[environment]
        if not isinstance(entry, dict):
            errors.append(f"compiler profile entry must be an object: {environment}")
            continue
        lane = entry.get("lane")
        expected_tools = entry.get("tools")
        if not isinstance(lane, str) or not lane.strip():
            errors.append(f"compiler profile lacks a lane name: {environment}")
        if not nonempty_reason(entry.get("rationale")):
            errors.append(f"compiler profile lacks a rationale: {environment}")
        if not isinstance(expected_tools, dict):
            errors.append(f"compiler profile lacks a tools object: {environment}")
            continue
        if set(expected_tools) != set(TOOL_KEYS):
            errors.append(
                f"compiler profile must declare the complete tool tuple {TOOL_KEYS}: "
                f"{environment}"
            )
            continue
        actual_tools = discovered[environment]
        if set(actual_tools) != set(TOOL_KEYS):
            errors.append(
                f"compiler environment does not explicitly assign every tool: "
                f"{environment}: {actual_tools}"
            )
        if expected_tools != actual_tools:
            errors.append(
                f"compiler tool tuple mismatch for {environment}: "
                f"expected={expected_tools}, actual={actual_tools}"
            )

    for atom, environments in exact_map.items():
        lanes = {
            configured[environment]["lane"]
            for environment in environments
            if environment in configured
            and isinstance(configured[environment], dict)
            and isinstance(configured[environment].get("lane"), str)
        }
        if len(lanes) > 1:
            errors.append(f"conflicting compiler lanes for {atom}: {sorted(lanes)}")
    return errors


def validate_policy(
    package_env_root: Path,
    env_root: Path,
    policy_file: Path,
    *,
    atom_validator: Callable[[str], None] | None = None,
    match_map: Mapping[str, set[str]] | None = None,
    skip_portage_universe: bool = False,
) -> ValidationResult:
    package_env_root = package_env_root.resolve()
    env_root = env_root.resolve()
    paths = policy_files(package_env_root)
    lines = all_policy_lines(paths)
    exact_map = exact_environment_map(lines)
    policy = load_json_policy(policy_file)
    reviewed = require_mapping(policy, "reviewed_multi_environment_stacks")
    unmatched = require_mapping(policy, "allowed_unmatched_atoms")
    overlaps = require_sequence(policy, "allowed_effective_atom_overlaps")
    compiler_profiles = require_mapping(policy, "compiler_profiles")

    errors: list[str] = []
    notices: list[str] = []
    duplicates = duplicate_assignments(paths)
    if duplicates:
        errors.append(
            "duplicate package.env atom/environment pairs:\n"
            + format_duplicates(duplicates, package_env_root)
        )
    errors.extend(environment_reference_errors(lines, env_root))
    errors.extend(validate_multi_environment_allowlist(exact_map, reviewed))
    errors.extend(validate_forbidden_markers(lines, package_env_root, env_root, policy))
    errors.extend(validate_compiler_profiles(exact_map, env_root, compiler_profiles))

    selected_atom_validator = atom_validator
    if selected_atom_validator is None:
        selected_atom_validator = portage_atom_validator()
        if selected_atom_validator is None:
            selected_atom_validator = fallback_atom_validator
            notices.append(
                "Portage Atom is unavailable; fallback atom grammar was used"
            )
    atom_errors = atom_syntax_errors(exact_map, selected_atom_validator)
    errors.extend(atom_errors)

    resolved_matches = match_map
    semantic_status = "PASS" if match_map is not None else "SKIP"
    semantic_reason = (
        "package universe supplied by the validation caller"
        if match_map is not None
        else "installed/repository atom matching was not completed"
    )
    if resolved_matches is None and not skip_portage_universe and not atom_errors:
        resolved_matches, notice = portage_match_map(exact_map)
        if notice:
            notices.append(notice)
            semantic_reason = notice
        else:
            semantic_status = "PASS"
            semantic_reason = "live installed and repository Portage databases matched"
    elif skip_portage_universe:
        semantic_reason = "installed/repository atom matching explicitly skipped"
        notices.append(semantic_reason)
    elif atom_errors:
        semantic_reason = "atom syntax errors prevented package-universe matching"

    if resolved_matches is not None:
        errors.extend(validate_unmatched_atoms(exact_map, resolved_matches, unmatched))
        errors.extend(
            validate_effective_overlaps(
                exact_map,
                resolved_matches,
                overlaps,
                compiler_profiles,
            )
        )
    elif unmatched or overlaps:
        errors.append(
            "cannot validate unmatched/overlap exceptions without a package universe"
        )

    return ValidationResult(
        tuple(errors),
        tuple(notices),
        semantic_status,
        semantic_reason,
        len(paths),
        len(lines),
        len(exact_map),
        sum(len(values) for values in exact_map.values()),
    )


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-env-root",
        type=Path,
        default=DEFAULT_PACKAGE_ENV_ROOT,
        help="package.env directory to validate (default: repository policy)",
    )
    parser.add_argument(
        "--env-root",
        type=Path,
        default=None,
        help="Portage env directory (default: sibling env directory)",
    )
    parser.add_argument(
        "--policy-file",
        type=Path,
        default=DEFAULT_POLICY_FILE,
        help="reviewed JSON policy file",
    )
    parser.add_argument(
        "--skip-portage-universe",
        action="store_true",
        help="skip installed/repository matching (for hermetic non-Gentoo tests only)",
    )
    parser.add_argument(
        "--require-portage-universe",
        action="store_true",
        help="fail unless installed/repository atom matching was performed",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    package_env_root = options.package_env_root.resolve()

    # Preserve the focused duplicate diagnostic even for isolated legacy fixtures.
    try:
        paths = policy_files(package_env_root)
        duplicates = duplicate_assignments(paths)
    except PolicyError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if duplicates:
        print("ERROR: duplicate package.env atom/environment pairs:", file=sys.stderr)
        print(format_duplicates(duplicates, package_env_root), file=sys.stderr)
        return 1

    env_root = (
        options.env_root.resolve()
        if options.env_root is not None
        else package_env_root.parent.joinpath("env").resolve()
    )
    try:
        result = validate_policy(
            package_env_root,
            env_root,
            options.policy_file.resolve(),
            skip_portage_universe=options.skip_portage_universe,
        )
    except PolicyError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "PORTAGE_SEMANTIC\t"
        f"{result.portage_semantic_status}\t{result.portage_semantic_reason}"
    )
    for notice in result.notices:
        print(f"NOTICE: {notice}")
    if (
        options.require_portage_universe
        and result.portage_semantic_status != "PASS"
    ):
        print("ERROR: authoritative Portage package universe was required", file=sys.stderr)
        return 1
    if result.errors:
        print("ERROR: package.env policy validation failed:", file=sys.stderr)
        for validation_error in result.errors:
            print(f"- {validation_error}", file=sys.stderr)
        return 1

    print(
        "PASS: exact package.env policy validated "
        f"({result.policy_file_count} files, {result.assignment_line_count} lines, "
        f"{result.atom_count} atoms, {result.pair_count} atom/environment pairs)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

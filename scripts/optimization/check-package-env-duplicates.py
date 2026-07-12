#!/usr/bin/env python3
"""Reject duplicate atom/environment pairs in Portage package.env policy."""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKAGE_ENV_ROOT = REPOSITORY_ROOT / "portage" / "package.env"
VCS_DIRECTORY_NAMES = frozenset({"CVS", "RCS", "SCCS", ".bzr", ".git", ".hg", ".svn"})


@dataclass(frozen=True)
class Assignment:
    atom: str
    environment: str
    path: Path
    line_number: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.atom, self.environment)


class PolicyError(Exception):
    """A package.env policy file cannot be validated."""


def assignments_from_file(path: Path) -> Iterator[Assignment]:
    """Yield every atom/environment pair, ignoring comments and blank lines."""

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
        for environment in environments:
            yield Assignment(atom, environment, path, line_number)


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


def duplicate_assignments(
    paths: Iterable[Path],
) -> list[tuple[Assignment, Assignment]]:
    """Return repeated pairs along with their first occurrence."""

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


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-env-root",
        type=Path,
        default=DEFAULT_PACKAGE_ENV_ROOT,
        help="package.env directory to validate (default: repository policy)",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    package_env_root = options.package_env_root.resolve()
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

    print(
        "PASS: no duplicate package.env atom/environment pairs "
        f"across {len(paths)} policy files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

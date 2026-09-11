#!/usr/bin/env python3
"""Fail-closed pre-strip guard against catastrophic exported-ABI loss."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ELF_MAGIC = b"\x7fELF"


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) == ELF_MAGIC
    except OSError:
        return False


def inspect(path: Path) -> tuple[str, str | None, set[str]]:
    try:
        out = subprocess.check_output(
            ["/usr/bin/readelf", "-h", "-d", "--dyn-syms", "--wide", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"cannot inspect ELF {path}: {exc}") from exc
    elf_type = None
    soname = None
    result: set[str] = set()
    for line in out.splitlines():
        if line.startswith("  Type:"):
            elf_type = line.split(":", 1)[1].strip().split()[0]
        if "(SONAME)" in line and "Library soname:" in line:
            soname = line.split("Library soname:", 1)[1].strip().strip("[]")
        fields = line.split()
        if len(fields) < 8 or fields[0].rstrip(":").isdigit() is False:
            continue
        # Num Value Size Type Bind Vis Ndx Name
        if fields[4] not in {"GLOBAL", "WEAK", "UNIQUE", "GNU_UNIQUE"}:
            continue
        if fields[5] not in {"DEFAULT", "PROTECTED"} or fields[6] == "UND":
            continue
        name = fields[7]
        if name:
            result.add(name)
    return elf_type or "", soname, result


def resolve_tree_link(link: Path, tree: Path) -> Path | None:
    """Resolve one staged/live symlink without permitting tree escape."""
    try:
        target = os.readlink(link)
        raw = Path(target)
        resolved = (tree / raw.lstrip("/")) if raw.is_absolute() else (link.parent / raw)
        resolved = resolved.resolve(strict=True)
        resolved.relative_to(tree.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.is_file() and not resolved.is_symlink() else None


def compare_pair(rel: Path, installed: Path, candidate: Path, failures: list[str]) -> None:
    old_is_elf = is_elf(installed)
    new_is_elf = is_elf(candidate)
    if not old_is_elf:
        return
    if not new_is_elf:
        failures.append(f"{rel}: established ELF DSO replaced by non-ELF content")
        return
    try:
        old_type, old_soname, old = inspect(installed)
        new_type, new_soname, new = inspect(candidate)
    except RuntimeError as exc:
        failures.append(str(exc))
        return
    if old_type == "DYN" and new_type != "DYN":
        failures.append(f"{rel}: established DYN DSO replaced by ELF type {new_type or 'unknown'}")
        return
    if old_type != "DYN" or not old_soname:
        return
    if not new_soname:
        failures.append(f"{rel}: established SONAME {old_soname} disappeared")
        return
    if old_soname != new_soname:
        failures.append(f"{rel}: established SONAME changed {old_soname} -> {new_soname}")
        return
    missing = old - new
    if missing:
        sample = ",".join(sorted(missing)[:12])
        failures.append(f"{rel}: old={len(old)} new={len(new)} missing={sample}")


def main() -> int:
    if "--help" in sys.argv[1:]:
        print(__doc__)
        return 0
    raw_ed = os.environ.get("ED")
    raw_root = os.environ.get("ROOT")
    if not raw_ed or not raw_root:
        print("gentoo-optimization ABI guard: ED and ROOT are required", file=sys.stderr)
        return 1
    ed = Path(raw_ed)
    root = Path(raw_root)
    if not ed.is_absolute() or not root.is_absolute() or ed == Path("/"):
        print("gentoo-optimization ABI guard: invalid ED/ROOT context", file=sys.stderr)
        return 1
    if not ed.is_dir() or not root.is_dir():
        print("gentoo-optimization ABI guard: ED and ROOT must be directories", file=sys.stderr)
        return 1
    failures: list[str] = []
    for candidate in ed.rglob("*"):
        if ".so" not in candidate.name:
            continue
        rel = candidate.relative_to(ed)
        installed = root / rel
        if candidate.is_symlink():
            if not installed.is_symlink():
                continue
            old_target = resolve_tree_link(installed, root)
            new_target = resolve_tree_link(candidate, ed)
            if old_target is None or new_target is None:
                failures.append(f"{rel}: versioned DSO symlink target is invalid or escapes its tree")
                continue
            compare_pair(rel, old_target, new_target, failures)
            continue
        if not candidate.is_file() or not installed.is_file():
            continue
        compare_pair(rel, installed, candidate, failures)
    if failures:
        print("gentoo-optimization ABI guard: exported ABI loss", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

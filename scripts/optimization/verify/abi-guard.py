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
        # LLVM IR instrumentation emits this runtime helper into staged DSOs.
        # It is not a package-provided ABI symbol and is intentionally absent
        # from profile-use and ordinary builds; comparing it would reject the
        # valid transition from a training image to a deployable image.
        if name == "__llvm_write_custom_profile":
            continue
        # Qt deliberately versions its private ABI namespace on patch-level
        # updates (for example QtPrivate_6_11_1 -> QtPrivate_6_11_2).  These
        # symbols are explicitly tagged Qt_6_PRIVATE_API and are not part of
        # the public compatibility contract that this guard protects.
        if name and "@Qt_6_PRIVATE_API" not in name:
            result.add(name)
    return elf_type or "", soname, result


def resolve_tree_link(link: Path, tree: Path) -> Path | None:
    """Resolve one staged/live symlink without permitting tree escape."""
    try:
        tree_root = tree.resolve(strict=True)
        target = os.readlink(link)
        raw = Path(target)
        unresolved = (
            tree_root / target.lstrip("/")
            if raw.is_absolute()
            else link.parent / raw
        )
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(tree_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.is_file() else None


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
    missing = old - new
    if missing:
        sample = ",".join(sorted(missing)[:12])
        soname_note = (
            f" SONAME changed {old_soname} -> {new_soname};"
            if old_soname != new_soname
            else ""
        )
        failures.append(f"{rel}:{soname_note} old={len(old)} new={len(new)} missing={sample}")


def collect_soname_providers(
    tree: Path,
    families: set[str] | None = None,
    relative_dirs: set[Path] | None = None,
) -> dict[str, tuple[Path, set[str]]]:
    """Return established ELF DSO providers keyed by their SONAME."""
    providers: dict[str, tuple[Path, set[str]]] = {}
    if relative_dirs is not None and not relative_dirs:
        return providers
    roots = [tree] if relative_dirs is None else [tree / rel for rel in sorted(relative_dirs)]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.iterdir() if relative_dirs is not None else root.rglob("*"):
            if ".so" not in path.name:
                continue
            if families is not None and path.name.split(".so", 1)[0] + ".so" not in families:
                continue
            resolved = resolve_tree_link(path, tree) if path.is_symlink() else path
            if resolved is None or not resolved.is_file() or not is_elf(resolved):
                continue
            try:
                elf_type, soname, symbols = inspect(resolved)
            except RuntimeError:
                continue
            if elf_type == "DYN" and soname:
                providers.setdefault(soname, (resolved, symbols))
    return providers


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
    # Compare from the installed ABI-provider side as well as by relative
    # path.  A replacement such as libfoo.so.1 -> libfoo.so.2 otherwise has
    # no same-path pair and could silently remove the established ABI.
    candidate_paths = [path for path in ed.rglob("*") if ".so" in path.name]
    if not candidate_paths:
        return 0
    candidate_families = {
        path.name.split(".so", 1)[0] + ".so" for path in candidate_paths
    }
    candidate_dirs = {path.relative_to(ed).parent for path in candidate_paths}
    candidate_providers = collect_soname_providers(
        ed, candidate_families, candidate_dirs
    )
    # Restrict installed-side discovery to the candidate's library families;
    # ROOT contains the whole system, whereas ED contains one package image.
    # For example, libstdc++.so.6.0.36 and .6.0.37 share the family
    # ``libstdc++.so`` even though the versioned relative path changes.
    installed_providers = {
        soname: value
        for soname, value in collect_soname_providers(
            root, candidate_families, candidate_dirs
        ).items()
    }
    for soname, (installed_path, installed_symbols) in installed_providers.items():
        candidate = candidate_providers.get(soname)
        if candidate is None:
            failures.append(f"{installed_path.relative_to(root)}: established SONAME {soname} disappeared")
            continue
        missing = installed_symbols - candidate[1]
        if missing:
            sample = ",".join(sorted(missing)[:12])
            failures.append(
                f"{installed_path.relative_to(root)}: SONAME {soname} "
                f"provider ABI loss old={len(installed_symbols)} new={len(candidate[1])} missing={sample}"
            )
    for candidate in candidate_paths:
        rel = candidate.relative_to(ed)
        installed = root / rel
        candidate_is_link = candidate.is_symlink()
        installed_is_link = installed.is_symlink()

        if candidate_is_link:
            candidate_target = resolve_tree_link(candidate, ed)
            if candidate_target is None:
                if installed_is_link or installed.is_file():
                    failures.append(
                        f"{rel}: candidate DSO symlink target is invalid or escapes its tree"
                    )
                continue
        elif candidate.is_file():
            candidate_target = candidate
        else:
            continue

        if installed_is_link:
            installed_target = resolve_tree_link(installed, root)
            if installed_target is None:
                failures.append(
                    f"{rel}: installed DSO symlink target is invalid or escapes its tree"
                )
                continue
        elif installed.is_file():
            installed_target = installed
        else:
            continue

        compare_pair(rel, installed_target, candidate_target, failures)
    if failures:
        print("gentoo-optimization ABI guard: exported ABI loss", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

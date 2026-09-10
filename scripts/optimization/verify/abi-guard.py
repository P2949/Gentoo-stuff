#!/usr/bin/env python3
"""Fail-closed pre-strip guard against catastrophic exported-ABI loss."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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


def main() -> int:
    ed = Path(os.environ.get("ED", ""))
    root = Path(os.environ.get("ROOT", "/"))
    if not ed.is_dir() or not root.is_dir():
        return 0
    failures: list[str] = []
    for candidate in ed.rglob("*"):
        if not candidate.is_file() or ".so" not in candidate.name:
            continue
        rel = candidate.relative_to(ed)
        installed = root / rel
        if not installed.is_file():
            continue
        try:
            old_type, old_soname, old = inspect(installed)
            new_type, new_soname, new = inspect(candidate)
        except RuntimeError as exc:
            failures.append(str(exc))
            continue
        if old_type != "DYN" or new_type != "DYN" or not old_soname or old_soname != new_soname:
            continue
        if not old:
            continue
        missing = old - new
        # Losing every established export is catastrophic regardless of DSO
        # size.  For larger established ABIs also reject severe count loss or
        # loss of at least half of the previous exported names.
        catastrophic_loss = (
            missing == old
            or len(new) * 4 < len(old)
            or (
                len(old) >= 20
                and len(missing) * 2 >= len(old)
            )
        )
        if catastrophic_loss:
            sample = ",".join(sorted(missing)[:12])
            failures.append(f"{rel}: old={len(old)} new={len(new)} missing={sample}")
    if failures:
        print("gentoo-optimization ABI guard: exported ABI loss", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

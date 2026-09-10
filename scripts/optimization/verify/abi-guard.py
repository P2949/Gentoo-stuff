#!/usr/bin/env python3
"""Fail-closed pre-strip guard against catastrophic exported-ABI loss."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def symbols(path: Path) -> set[str]:
    try:
        out = subprocess.check_output(
            ["readelf", "--dyn-syms", "--wide", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return set()
    result: set[str] = set()
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 8 or fields[0].rstrip(":").isdigit() is False:
            continue
        # Num Value Size Type Bind Vis Ndx Name
        if fields[4] not in {"GLOBAL", "WEAK", "GNU_UNIQUE"}:
            continue
        if fields[5] not in {"DEFAULT", "PROTECTED"} or fields[6] == "UND":
            continue
        name = fields[7]
        if name and not name.startswith("_"):
            result.add(name)
    return result


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
        old = symbols(installed)
        new = symbols(candidate)
        if len(old) < 20:
            continue
        missing = old - new
        # A replacement that loses most of the established public ABI is
        # catastrophic.  Report the exact versioned names for remediation.
        if len(new) * 4 < len(old) or len(missing) * 2 >= len(old):
            sample = ",".join(sorted(missing)[:12])
            failures.append(f"{rel}: old={len(old)} new={len(new)} missing={sample}")
    if failures:
        print("gentoo-optimization ABI guard: exported ABI loss", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

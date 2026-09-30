#!/usr/bin/env python3
"""Materialize an exact non-instrumented overlay for baseline maintenance.

Portage gives exact package.env assignments precedence over wildcard entries.
During userspace baseline repair, this helper converts the active generated
policy's exact CPV set to a mode-only ``off`` environment.  The overlay is
temporary evidence, never a replacement for the generated policy; authenticated
profile runners reapply their dispatcher after package.env resolution.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

CPV = re.compile(r"^=[^\s/]+/[^\s/]+\S*$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--environment", default="optimization/optimization-off-mode.conf")
    args = parser.parse_args()
    try:
        lines = args.generated_policy.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"REFUSED: cannot read generated policy: {exc}")
    cpvs: set[str] = set()
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) != 2 or not CPV.fullmatch(fields[0]):
            raise SystemExit(f"REFUSED: malformed generated policy line {number}")
        cpvs.add(fields[0])
    if not cpvs:
        raise SystemExit("REFUSED: generated policy contains no exact CPVs")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{cpv} {args.environment}\n" for cpv in sorted(cpvs))
    args.output.write_text(payload, encoding="utf-8")
    print(f"materialized {len(cpvs)} exact baseline assignments: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

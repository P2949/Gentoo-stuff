#!/usr/bin/env python3
"""Reconcile one frozen optimization generation from package/artifact records."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
STATE_PATH = REPOSITORY_ROOT / "scripts/optimization/lib/state.py"
SPEC = importlib.util.spec_from_file_location("gentoo_optimization_state", STATE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - installation failure
    raise RuntimeError(f"cannot load state library: {STATE_PATH}")
STATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATE)


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise STATE.StateValidationError(f"{path}: {error}") from error


def _records(directory: Path, kind: str) -> list[dict[str, Any]]:
    if not directory.is_dir():
        raise STATE.StateValidationError(f"{kind} directory does not exist: {directory}")
    paths = sorted(directory.rglob("*.json"))
    if kind == "package" and not paths:
        raise STATE.StateValidationError(f"no package records found in {directory}")
    return [STATE.load_and_validate(path, kind) for path in paths]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages-dir", required=True, type=Path)
    parser.add_argument("--artifacts-dir", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--vdb-root", type=Path)
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.require_complete and (args.vdb_root is None or args.installed_root is None):
        print("ERROR: --require-complete requires --vdb-root and --installed-root", file=sys.stderr)
        return 2
    try:
        packages = _records(args.packages_dir, "package")
        artifacts = _records(args.artifacts_dir, "artifact")
        inventory = None
        inventory_sha256 = None
        if args.inventory is not None:
            payload = args.inventory.read_bytes()
            inventory = json.loads(payload)
            inventory_sha256 = hashlib.sha256(payload).hexdigest()
        summary = STATE.reconcile_collection(
            packages,
            artifacts,
            inventory=inventory,
            inventory_sha256=inventory_sha256,
            vdb_root=args.vdb_root,
            installed_root=args.installed_root,
        )
        digest = STATE.atomic_publish(summary, args.output)
    except (OSError, json.JSONDecodeError, STATE.StateValidationError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    counts = summary["counts"]
    print(
        f"packages={counts['package_record_total']} artifacts={counts['artifact_record_total']} "
        f"pending_total={counts['pending_total']} unknown_total={counts['unknown_total']} "
        f"failed_total={counts['failed_total']} coverage_complete={str(summary['coverage_complete']).lower()} "
        f"report_sha256={digest}"
    )
    if args.require_complete and not summary["coverage_complete"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

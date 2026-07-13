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


def _records(directory: Path, kind: str, *, secure: bool = False, fixture_mode: bool = False) -> list[dict[str, Any]]:
    if not directory.is_dir():
        raise STATE.StateValidationError(f"{kind} directory does not exist: {directory}")
    paths = sorted(directory.rglob("*.json"))
    if kind == "package" and not paths:
        raise STATE.StateValidationError(f"no package records found in {directory}")
    if secure:
        return [STATE.VALIDATORS[kind](STATE.secure_json(path, fixture_mode=fixture_mode, allowed_roots=[directory])) for path in paths]
    return [STATE.load_and_validate(path, kind) for path in paths]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages-dir", type=Path)
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--validate-inventory-only", action="store_true", help="strictly validate and summarize one frozen inventory")
    parser.add_argument("--final-system-state", type=Path)
    parser.add_argument("--vdb-root", type=Path)
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument("--strict", action="store_true", help="reopen and semantically verify every completion proof")
    parser.add_argument("--fixture-roots", action="store_true", help="allow non-live roots for hermetic tests; can never authorize completion")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.validate_inventory_only:
        if any(value is not None for value in (args.packages_dir, args.artifacts_dir, args.output, args.final_system_state, args.vdb_root, args.installed_root)) or args.require_complete or args.strict:
            print("ERROR: --validate-inventory-only accepts only --inventory and optional --fixture-roots", file=sys.stderr)
            return 2
        try:
            payload = STATE.secure_read(args.inventory, fixture_mode=args.fixture_roots, allowed_roots=[args.inventory.parent])
            inventory = STATE._inventory(json.loads(payload))
        except (OSError, json.JSONDecodeError, STATE.StateValidationError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        print(json.dumps({
            "inventory_sha256": hashlib.sha256(payload).hexdigest(),
            "generation_id": inventory["generation_id"],
            "inventory_id": inventory["inventory_id"],
            "package_count": len(inventory["packages"]),
            "owned_path_count": len(inventory["owned_paths"]),
            "owned_directory_count": len(inventory["owned_directories"]),
            "cpvs": [entry["cpv"] for entry in inventory["packages"]],
        }, sort_keys=True, separators=(",", ":")))
        return 0
    if args.packages_dir is None or args.artifacts_dir is None or args.output is None:
        print("ERROR: reconciliation requires --packages-dir, --artifacts-dir, and --output", file=sys.stderr)
        return 2
    if args.require_complete and args.fixture_roots:
        print("ERROR: --fixture-roots can never be used with --require-complete", file=sys.stderr)
        return 2
    if args.require_complete and args.final_system_state is None:
        print("ERROR: --require-complete requires --final-system-state", file=sys.stderr)
        return 2
    if args.require_complete:
        args.strict = True
        if args.vdb_root is not None and args.vdb_root != STATE.AUTHORITATIVE_VDB_ROOT:
            print("ERROR: authoritative completion requires exact --vdb-root /var/db/pkg", file=sys.stderr)
            return 2
        if args.installed_root is not None and args.installed_root != STATE.AUTHORITATIVE_INSTALLED_ROOT:
            print("ERROR: authoritative completion requires exact --installed-root /", file=sys.stderr)
            return 2
        args.vdb_root = STATE.AUTHORITATIVE_VDB_ROOT
        args.installed_root = STATE.AUTHORITATIVE_INSTALLED_ROOT
    if args.strict and (args.final_system_state is None or args.vdb_root is None or args.installed_root is None):
        print("ERROR: --strict requires --final-system-state, --vdb-root, and --installed-root", file=sys.stderr)
        return 2
    try:
        final_system_state = None
        if args.final_system_state is not None:
            if args.strict:
                final_system_state = STATE.secure_json(args.final_system_state, fixture_mode=args.fixture_roots)
            else:
                final_system_state = _json(args.final_system_state)
        packages = _records(args.packages_dir, "package", secure=args.strict, fixture_mode=args.fixture_roots)
        artifacts = _records(args.artifacts_dir, "artifact", secure=args.strict, fixture_mode=args.fixture_roots)
        inventory = None
        inventory_sha256 = None
        if args.inventory is not None:
            payload = STATE.secure_read(args.inventory, fixture_mode=args.fixture_roots, allowed_roots=[args.inventory.parent]) if args.strict else args.inventory.read_bytes()
            inventory = json.loads(payload)
            inventory_sha256 = hashlib.sha256(payload).hexdigest()
        summary = STATE.reconcile_collection(
            packages,
            artifacts,
            inventory=inventory,
            inventory_sha256=inventory_sha256,
            vdb_root=args.vdb_root,
            installed_root=args.installed_root,
            final_system_state=final_system_state,
            packages_dir=args.packages_dir,
            artifacts_dir=args.artifacts_dir,
            inventory_path=args.inventory,
            strict=args.strict,
            fixture_mode=args.fixture_roots,
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

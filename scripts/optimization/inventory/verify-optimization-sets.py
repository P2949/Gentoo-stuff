#!/usr/bin/env python3
"""Independently verify optimization set files against policy and lane authority."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

from importlib.util import module_from_spec, spec_from_file_location

ROOT = Path(__file__).resolve().parents[3]
GEN = ROOT / "scripts/optimization/inventory/generate-optimization-sets.py"
spec = spec_from_file_location("generate_optimization_sets", GEN)
assert spec and spec.loader
generator = module_from_spec(spec)
spec.loader.exec_module(generator)

LANE_SET = generator.LANE_SET

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutation-policy", type=Path, required=True)
    ap.add_argument("--lanes", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--sets-root", type=Path, required=True)
    ap.add_argument("--scope-policy", type=Path)
    args = ap.parse_args()

    policy = json.loads(args.mutation_policy.read_text())
    lanes = json.loads(args.lanes.read_text())
    manifest = json.loads(args.manifest.read_text())
    if manifest.get("record_type") != "optimization-package-sets" or manifest.get("schema_version") not in {2, 3}:
        raise SystemExit("REFUSED: invalid optimization-set manifest schema")
    if manifest.get("schema_version") == 3 and not args.scope_policy:
        raise SystemExit("REFUSED: scoped set manifest requires scope policy")
    if manifest.get("mutation_policy_sha256") != digest(args.mutation_policy):
        raise SystemExit("REFUSED: mutation-policy digest does not match set manifest")
    if manifest.get("lane_sha256") != digest(args.lanes):
        raise SystemExit("REFUSED: lane digest does not match set manifest")

    decisions = {row["cpv"]: row["decision"] for row in policy.get("records", [])}
    lane_rows = {row["cpv"]: row for row in lanes.get("packages", lanes.get("records", []))}
    if set(decisions) != set(lane_rows):
        raise SystemExit("REFUSED: policy/lane coverage mismatch")

    excluded = set()
    if args.scope_policy:
        scope = json.loads(args.scope_policy.read_text())
        if scope.get("schema") != "optimization-scope-policy-v1":
            raise SystemExit("REFUSED: unsupported scope policy schema")
        if manifest.get("scope_policy_sha256") != digest(args.scope_policy):
            raise SystemExit("REFUSED: scope-policy digest does not match set manifest")
        selectors = {row["selector"] for row in scope.get("scope", [])
                     if row.get("state") == "retained-installed-out-of-project-scope"}
        excluded = {cpv for cpv in decisions if generator.cp_atom(cpv) in selectors}
    expected = {
        "pgo-bolt-all-userspace": set(),
        "optimization-kernel-policy-exclusion": set(),
        "optimization-not-applicable": set(),
    }
    for name in LANE_SET.values():
        expected[name] = set()
    for cpv in sorted(decisions):
        if cpv in excluded:
            continue
        decision = decisions[cpv]
        lane = lane_rows[cpv].get("lane")
        if decision == "kernel-policy-exclusion":
            expected["optimization-kernel-policy-exclusion"].add(cpv)
        elif decision == "userspace":
            expected["pgo-bolt-all-userspace"].add(cpv)
            if lane in LANE_SET:
                expected[LANE_SET[lane]].add(cpv)
            elif lane in {"not-applicable", "unsupported-by-upstream-toolchain"}:
                expected["optimization-not-applicable"].add(cpv)
            else:
                raise SystemExit(f"REFUSED: unsupported lane {lane!r} for {cpv}")
        else:
            raise SystemExit(f"REFUSED: unresolved mutation decision for {cpv}")

    for name, cpvs in expected.items():
        path = args.sets_root / name
        if not path.is_file():
            raise SystemExit(f"REFUSED: missing optimization set {name}")
        entries = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        if entries != sorted(set(entries)):
            raise SystemExit(f"REFUSED: set {name} is not sorted and unique")
        actual = set()
        for entry in entries:
            if entry.startswith("="):
                cpv = entry[1:]
                if cpv not in decisions:
                    raise SystemExit(f"REFUSED: set {name} contains unknown exact CPV {cpv}")
                actual.add(cpv)
                continue
            members = {cpv for cpv in decisions if generator.cp_atom(cpv) == entry}
            if not members:
                raise SystemExit(f"REFUSED: set {name} contains unknown atom {entry}")
            actual.update(members)
        if actual != cpvs:
            raise SystemExit(f"REFUSED: set {name} coverage mismatch missing={sorted(cpvs-actual)[:5]} extra={sorted(actual-cpvs)[:5]}")
        if manifest.get("sets", {}).get(name) != len(entries):
            raise SystemExit(f"REFUSED: manifest count mismatch for {name}")
    if manifest.get("scope_excluded", []) and {row.get("cpv") for row in manifest["scope_excluded"]} != excluded:
        raise SystemExit("REFUSED: scope-excluded accounting mismatch")
    print(f"PASS: verified {len(decisions)} CPVs across {len(expected)} optimization sets")

if __name__ == "__main__":
    main()

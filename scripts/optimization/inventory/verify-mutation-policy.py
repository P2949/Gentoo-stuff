#!/usr/bin/env python3
"""Independently verify complete, non-contradictory mutation policy coverage."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def canon(value): return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    args = ap.parse_args()
    policy = json.loads(args.policy.read_text())
    if policy.get("record_type") != "package-mutation-policy" or policy.get("schema_version") != 1:
        raise SystemExit("REFUSED: unsupported mutation-policy schema")
    unsigned = dict(policy); declared = unsigned.pop("sha256", None)
    if not isinstance(declared, str) or hashlib.sha256(canon(unsigned)).hexdigest() != declared:
        raise SystemExit("REFUSED: mutation-policy digest mismatch")
    if policy.get("source_manifest_sha256") != sha(args.manifest):
        raise SystemExit("REFUSED: mutation-policy manifest binding mismatch")
    expected = sorted(row["cpv"] for row in json.loads(args.manifest.read_text()).get("packages", []))
    records = policy.get("records", [])
    actual = [row.get("cpv") for row in records]
    if actual != sorted(set(actual)) or actual != expected:
        raise SystemExit("REFUSED: mutation-policy package coverage/order mismatch")
    allowed = {"userspace", "kernel-policy-exclusion"}
    if any(row.get("decision") not in allowed for row in records):
        raise SystemExit("REFUSED: unresolved mutation decision")
    print(f"PASS: mutation policy covers {len(records)} CPVs with one decision each")

if __name__ == "__main__": main()

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
    ap.add_argument("--kernel-classification", type=Path, required=True)
    args = ap.parse_args()
    policy = json.loads(args.policy.read_text())
    if policy.get("record_type") != "package-mutation-policy" or policy.get("schema_version") != 1:
        raise SystemExit("REFUSED: unsupported mutation-policy schema")
    unsigned = dict(policy); declared = unsigned.pop("sha256", None)
    if not isinstance(declared, str) or hashlib.sha256(canon(unsigned)).hexdigest() != declared:
        raise SystemExit("REFUSED: mutation-policy digest mismatch")
    if policy.get("source_manifest_sha256") != sha(args.manifest):
        raise SystemExit("REFUSED: mutation-policy manifest binding mismatch")
    if policy.get("source_kernel_classification_sha256") != sha(args.kernel_classification):
        raise SystemExit("REFUSED: mutation-policy classification binding mismatch")
    expected = sorted(row["cpv"] for row in json.loads(args.manifest.read_text()).get("packages", []))
    records = policy.get("records", [])
    actual = [row.get("cpv") for row in records]
    if actual != sorted(set(actual)) or actual != expected:
        raise SystemExit("REFUSED: mutation-policy package coverage/order mismatch")
    allowed = {"userspace", "kernel-policy-exclusion"}
    if any(row.get("decision") not in allowed for row in records):
        raise SystemExit("REFUSED: unresolved mutation decision")
    classification = json.loads(args.kernel_classification.read_text())
    source = {row.get("cpv"): row for row in classification.get("records", [])}
    if set(source) != set(expected):
        raise SystemExit("REFUSED: source mutation classification coverage mismatch")
    for row in records:
        src = source[row["cpv"]]
        expected_decision = "kernel-policy-exclusion" if src.get("state") == "kernel-policy-exclusion" else "userspace" if src.get("state") == "userspace-transaction" else None
        if expected_decision is None and not (row["cpv"].split("/", 1)[0] in {"virtual", "acct-group", "acct-user"} and not src.get("evidence_paths")):
            raise SystemExit(f"REFUSED: unresolved source classification for {row['cpv']}")
        if expected_decision and row["decision"] != expected_decision:
            raise SystemExit(f"REFUSED: mutation decision disagrees with source for {row['cpv']}")
    print(f"PASS: mutation policy covers {len(records)} CPVs with one decision each")

if __name__ == "__main__": main()

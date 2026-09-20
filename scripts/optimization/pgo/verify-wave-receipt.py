#!/usr/bin/env python3
"""Fail-closed validation for a profile-wave transaction receipt."""
import argparse
import hashlib
import json
from pathlib import Path


def digest_document(value):
    copy = dict(value)
    copy.pop("sha256", None)
    return hashlib.sha256(
        json.dumps(copy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipt", type=Path, required=True)
    ap.add_argument("--wave", type=Path, required=True)
    ap.add_argument("--readiness", type=Path, required=True)
    args = ap.parse_args()
    receipt = json.loads(args.receipt.read_text())
    wave = json.loads(args.wave.read_text())
    readiness = json.loads(args.readiness.read_text())
    if receipt.get("record_type") != "profile-wave-transaction-receipt":
        raise SystemExit("REFUSED: receipt has an invalid record type")
    if receipt.get("schema_version") not in (2, 3):
        raise SystemExit("REFUSED: completed wave receipt has an unsupported schema")
    generation = receipt.get("generation")
    if not isinstance(generation, dict) or set(generation) != {"generation_id", "inventory_id", "inventory_sha256"}:
        raise SystemExit("REFUSED: receipt lacks an exact generation triple")
    if not all(isinstance(generation.get(k), str) and generation[k] for k in generation):
        raise SystemExit("REFUSED: receipt generation identity is malformed")
    if len(generation["inventory_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in generation["inventory_sha256"]):
        raise SystemExit("REFUSED: receipt inventory digest is not a lowercase SHA-256")
    if not isinstance(receipt.get("framework_generation"), str) or not receipt["framework_generation"].startswith("/"):
        raise SystemExit("REFUSED: receipt lacks an absolute active framework target")
    if receipt.get("sha256") != digest_document(receipt):
        raise SystemExit("REFUSED: receipt self-digest mismatch")
    if receipt.get("wave_sha256") != wave.get("sha256"):
        raise SystemExit("REFUSED: receipt is bound to a different wave")
    if receipt.get("readiness_sha256") != readiness.get("sha256"):
        raise SystemExit("REFUSED: receipt is bound to a different readiness audit")
    packages = [item.get("cpv") for item in wave.get("packages", [])]
    if receipt.get("packages") != packages or receipt.get("package_count") != len(packages):
        raise SystemExit("REFUSED: receipt package set does not match the wave")
    if receipt.get("schema_version") == 3:
        records = receipt.get("package_records")
        if not isinstance(records, list) or sorted(x.get("cpv") for x in records if isinstance(x, dict)) != sorted(packages):
            raise SystemExit("REFUSED: v3 receipt package records do not match the wave")
        for record in records:
            if not isinstance(record, dict) or not all(isinstance(record.get(k), str) and record[k] for k in ("cpv", "lane", "attempt_id", "profile_spool")):
                raise SystemExit("REFUSED: v3 package record is incomplete")
    payloads = receipt.get("profile_payloads")
    if receipt.get("state") == "completed":
        if receipt.get("authorization") != "profile-payloads-collected":
            raise SystemExit("REFUSED: completed receipt lacks payload authorization")
        if not isinstance(payloads, list) or not payloads:
            raise SystemExit("REFUSED: completed receipt contains no profile payloads")
        seen = set()
        for payload in payloads:
            if not isinstance(payload, dict):
                raise SystemExit("REFUSED: profile payload record is not an object")
            cpv = payload.get("cpv")
            path_value = payload.get("path")
            digest = payload.get("sha256")
            if cpv not in packages or not isinstance(path_value, str) or not path_value.startswith("/"):
                raise SystemExit("REFUSED: profile payload has an invalid package or path")
            path = Path(path_value)
            key = (cpv, path_value)
            if key in seen:
                raise SystemExit("REFUSED: duplicate profile payload record")
            seen.add(key)
            if not path.is_file() or not isinstance(digest, str):
                raise SystemExit("REFUSED: profile payload file is missing or has no digest")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != digest:
                raise SystemExit("REFUSED: profile payload digest mismatch")
    elif payloads not in ([], None):
        raise SystemExit("REFUSED: non-completed receipt contains profile payloads")
    print("PASS: profile-wave receipt is internally consistent")


if __name__ == "__main__":
    main()

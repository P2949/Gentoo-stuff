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
    if receipt.get("sha256") != digest_document(receipt):
        raise SystemExit("REFUSED: receipt self-digest mismatch")
    if receipt.get("wave_sha256") != wave.get("sha256"):
        raise SystemExit("REFUSED: receipt is bound to a different wave")
    if receipt.get("readiness_sha256") != readiness.get("sha256"):
        raise SystemExit("REFUSED: receipt is bound to a different readiness audit")
    packages = [item.get("cpv") for item in wave.get("packages", [])]
    if receipt.get("packages") != packages or receipt.get("package_count") != len(packages):
        raise SystemExit("REFUSED: receipt package set does not match the wave")
    payloads = receipt.get("profile_payloads")
    if receipt.get("state") == "completed":
        if receipt.get("authorization") != "profile-payloads-collected":
            raise SystemExit("REFUSED: completed receipt lacks payload authorization")
        if not isinstance(payloads, list) or not payloads:
            raise SystemExit("REFUSED: completed receipt contains no profile payloads")
    elif payloads not in ([], None):
        raise SystemExit("REFUSED: non-completed receipt contains profile payloads")
    print("PASS: profile-wave receipt is internally consistent")


if __name__ == "__main__":
    main()

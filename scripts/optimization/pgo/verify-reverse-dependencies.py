#!/usr/bin/env python3
"""Independently verify an authenticated reverse-dependency graph."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", type=Path, required=True)
    ap.add_argument("--portage", type=Path, required=True)
    ap.add_argument("--elf", type=Path, required=True)
    args = ap.parse_args()
    doc = json.loads(args.graph.read_text())
    required = {"record_type", "schema_version", "source_contract",
                "portage_source_sha256", "elf_source_sha256",
                "unresolved_review_sha256", "records", "sha256"}
    if set(doc) != required or doc.get("record_type") != "reverse-dependency-graph" or doc.get("schema_version") != 2:
        raise SystemExit("REFUSED: invalid reverse-dependency graph schema")
    unsigned = dict(doc); declared = unsigned.pop("sha256")
    if hashlib.sha256(canon(unsigned)).hexdigest() != declared:
        raise SystemExit("REFUSED: reverse-dependency graph digest mismatch")
    for key, path in (("portage_source_sha256", args.portage), ("elf_source_sha256", args.elf)):
        if doc[key] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise SystemExit(f"REFUSED: {key} does not match source")
    rows = doc["records"]
    if not isinstance(rows, list):
        raise SystemExit("REFUSED: graph records are not a list")
    seen = set()
    counts = {"portage-runtime": 0, "portage-build": 0, "elf-needed": 0}
    for row in rows:
        if not isinstance(row, dict) or not row.get("provider_cpv") or not row.get("consumer_cpv"):
            raise SystemExit("REFUSED: malformed reverse-dependency edge")
        relation = row.get("relationship")
        if relation not in counts or row["provider_cpv"] == row["consumer_cpv"]:
            raise SystemExit("REFUSED: invalid reverse-dependency edge")
        identity = (row["provider_cpv"], row["consumer_cpv"], relation)
        if identity in seen:
            raise SystemExit("REFUSED: duplicate reverse-dependency edge")
        seen.add(identity); counts[relation] += 1
    contract = doc["source_contract"]
    if contract != {"portage_runtime_records": counts["portage-runtime"],
                    "portage_build_records": counts["portage-build"],
                    "elf_needed_records": counts["elf-needed"]}:
        raise SystemExit("REFUSED: graph source-contract counts do not match records")
    review = doc["unresolved_review_sha256"]
    if review is not None and (not isinstance(review, str) or len(review) != 64 or any(c not in "0123456789abcdef" for c in review)):
        raise SystemExit("REFUSED: invalid unresolved review digest")
    print(f"PASS: reverse-dependency graph independently verified ({len(rows)} edges)")

if __name__ == "__main__":
    main()

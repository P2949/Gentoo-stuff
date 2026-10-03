#!/usr/bin/env python3
"""Independently verify a live ELF DT_NEEDED dependency-source artifact."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def canon(value): return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--source", type=Path, required=True); ap.add_argument("--elf", type=Path, required=True); a = ap.parse_args()
    doc = json.loads(a.source.read_text())
    required = {"record_type", "schema_version", "source_elf_sha256", "records", "unresolved", "sha256"}
    if set(doc) != required or doc.get("record_type") != "live-elf-dependency-source" or doc.get("schema_version") != 1:
        raise SystemExit("REFUSED: invalid live ELF dependency-source schema")
    unsigned = dict(doc); declared = unsigned.pop("sha256")
    if hashlib.sha256(canon(unsigned)).hexdigest() != declared:
        raise SystemExit("REFUSED: live ELF dependency-source digest mismatch")
    if doc["source_elf_sha256"] != hashlib.sha256(a.elf.read_bytes()).hexdigest():
        raise SystemExit("REFUSED: live ELF source digest mismatch")
    seen = set()
    for row in doc["records"]:
        if not isinstance(row, dict) or not row.get("provider_cpv") or not row.get("consumer_cpv") or row["provider_cpv"] == row["consumer_cpv"]:
            raise SystemExit("REFUSED: malformed live ELF dependency edge")
        evidence = row.get("evidence")
        if not isinstance(evidence, dict) or not evidence.get("consumer_path") or not evidence.get("needed") or not evidence.get("provider_path"):
            raise SystemExit("REFUSED: live ELF dependency edge lacks artifact evidence")
        key = (row["provider_cpv"], row["consumer_cpv"], evidence["consumer_path"], evidence["provider_path"], evidence["needed"])
        if key in seen: raise SystemExit("REFUSED: duplicate live ELF dependency edge")
        seen.add(key)
    unresolved_seen = set()
    for row in doc["unresolved"]:
        if not isinstance(row, dict) or not row.get("consumer_cpv") or not row.get("consumer_path") or not row.get("needed") or row.get("reason") not in {"provider-not-owned", "provider-ambiguous"}:
            raise SystemExit("REFUSED: malformed unresolved live ELF dependency")
        key = (row["consumer_cpv"], row["consumer_path"], row["needed"])
        if key in unresolved_seen: raise SystemExit("REFUSED: duplicate unresolved live ELF dependency")
        unresolved_seen.add(key)
        if row["reason"] == "provider-ambiguous" and not isinstance(row.get("owners"), list):
            raise SystemExit("REFUSED: ambiguous ELF dependency lacks owner set")
    print(f"PASS: live ELF dependency source independently verified ({len(seen)} edges, {len(unresolved_seen)} unresolved)")

if __name__ == "__main__": main()

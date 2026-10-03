#!/usr/bin/env python3
"""Independently verify a VDB-backed Portage dependency-source artifact."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--vdb", type=Path)
    args = ap.parse_args()
    doc = json.loads(args.source.read_text())
    required = {"record_type", "schema_version", "vdb_root", "cpv_count",
                "source_digest", "choice_review_sha256", "source_errors",
                "records", "build_records", "sha256"}
    if set(doc) != required or doc.get("record_type") != "live-portage-dependency-source" or doc.get("schema_version") != 2:
        raise SystemExit("REFUSED: invalid Portage dependency-source schema")
    declared = doc["sha256"]
    unsigned = dict(doc); unsigned.pop("sha256")
    if hashlib.sha256(canon(unsigned)).hexdigest() != declared:
        raise SystemExit("REFUSED: Portage dependency-source digest mismatch")
    if not isinstance(doc["cpv_count"], int) or doc["cpv_count"] < 0:
        raise SystemExit("REFUSED: invalid Portage CPV count")
    if not isinstance(doc["records"], list) or not isinstance(doc["build_records"], list) or not isinstance(doc["source_errors"], list):
        raise SystemExit("REFUSED: invalid Portage dependency-source lists")
    seen = set()
    unresolved_rows = {}
    for row in doc["records"] + doc["build_records"]:
        if not isinstance(row, dict) or not row.get("consumer_cpv"):
            raise SystemExit("REFUSED: malformed Portage dependency edge")
        relation = row.get("relationship")
        if relation not in {"portage-runtime", "portage-build"}:
            raise SystemExit("REFUSED: invalid Portage dependency relationship")
        if not row.get("provider_cpv"):
            evidence = row.get("evidence")
            if not isinstance(evidence, dict) or not evidence.get("error") or not evidence.get("expression_sha256"):
                raise SystemExit("REFUSED: unresolved Portage row lacks typed diagnostic evidence")
            key = (
                row["consumer_cpv"],
                evidence.get("field"),
                evidence.get("expression_sha256"),
            )
            if key in unresolved_rows:
                raise SystemExit("REFUSED: duplicate unresolved Portage diagnostic row")
            unresolved_rows[key] = (
                evidence.get("choice_operator"),
                evidence.get("choice_branch_count"),
            )
            continue
        key = (row["provider_cpv"], row["consumer_cpv"], relation,
               json.dumps(row.get("evidence", {}), sort_keys=True))
        if key in seen:
            raise SystemExit("REFUSED: duplicate Portage dependency edge")
        seen.add(key)
    unresolved_errors = {}
    for error in doc["source_errors"]:
        if not isinstance(error, dict) or error.get("stage") != "dependency-parse":
            raise SystemExit("REFUSED: source contains an untyped Portage error")
        if error.get("choice_operator") not in {"||", "^^", "??"} or not isinstance(error.get("choice_branch_count"), int) or not isinstance(error.get("expression_sha256"), str):
            raise SystemExit("REFUSED: unresolved Portage choice lacks structured evidence")
        key = (error.get("cpv"), error.get("field"), error.get("expression_sha256"))
        if key in unresolved_errors:
            raise SystemExit("REFUSED: duplicate unresolved Portage source error")
        unresolved_errors[key] = (error["choice_operator"], error["choice_branch_count"])
    if unresolved_errors != unresolved_rows:
        raise SystemExit("REFUSED: unresolved Portage rows and source errors do not match exactly")
    if args.vdb:
        requested = args.vdb.resolve()
        if Path(doc["vdb_root"]).resolve() != requested:
            raise SystemExit("REFUSED: Portage source VDB root mismatch")
        try:
            from portage.dbapi.vartree import vardbapi
            actual = Path(getattr(vardbapi(), "dbroot", requested)).resolve()
            if actual != requested:
                raise SystemExit("REFUSED: live Portage VDB root mismatch")
            if len(vardbapi().cpv_all()) != doc["cpv_count"]:
                raise SystemExit("REFUSED: Portage CPV count no longer matches source")
        except ImportError as exc:
            raise SystemExit(f"REFUSED: Portage verifier unavailable: {exc}")
    print(f"PASS: Portage dependency source independently verified ({len(seen)} edges, {len(doc['source_errors'])} unresolved)")

if __name__ == "__main__":
    main()

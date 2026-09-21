#!/usr/bin/env python3
"""Generate the single generation-bound package mutation-policy authority."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--kernel-classification", type=Path, required=True)
    ap.add_argument("--generation-id", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if args.output.exists():
        raise SystemExit("REFUSED: mutation-policy output already exists")
    manifest = json.loads(args.manifest.read_text())
    classification = json.loads(args.kernel_classification.read_text())
    packages = sorted({row["cpv"] for row in manifest.get("packages", [])})
    if not packages or len(packages) != len(manifest.get("packages", [])):
        raise SystemExit("REFUSED: manifest package CPVs are missing or duplicated")
    rows = {row.get("cpv"): row for row in classification.get("records", [])}
    if set(rows) != set(packages):
        missing = sorted(set(packages) - set(rows))
        extra = sorted(set(rows) - set(packages))
        raise SystemExit(f"REFUSED: mutation classification coverage mismatch missing={missing[:5]} extra={extra[:5]}")
    output = []
    for cpv in packages:
        source = rows[cpv]
        state = source.get("state")
        if state == "kernel-policy-exclusion":
            decision = "kernel-policy-exclusion"
            triggers = ["owns-forbidden-artifact"]
        elif state == "userspace-transaction":
            decision = "userspace"
            triggers = ["no-forbidden-lifecycle-evidence"]
        else:
            # Pending lifecycle review is not safe to classify as userspace.
            decision = "pending-review"
            triggers = [source.get("reason_code", "unresolved-lifecycle-evidence")]
        output.append({
            "cpv": cpv,
            "decision": decision,
            "triggers": sorted(set(triggers + list(source.get("ebuild_markers", [])))),
            "evidence": sorted(set(source.get("evidence_paths", []))),
            "repository": source.get("repository"),
            "ebuild_path": source.get("ebuild_path"),
        })
    if any(row["decision"] == "pending-review" for row in output):
        raise SystemExit("REFUSED: unresolved package mutation decisions remain")
    out = {
        "record_type": "package-mutation-policy",
        "schema_version": 1,
        "generation_id": args.generation_id,
        "source_manifest_sha256": sha(args.manifest),
        "source_kernel_classification_sha256": sha(args.kernel_classification),
        "records": output,
    }
    out["sha256"] = hashlib.sha256(canon(out)).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"generation_id": args.generation_id, "packages": len(output), "userspace": sum(x["decision"] == "userspace" for x in output), "kernel_policy": sum(x["decision"] == "kernel-policy-exclusion" for x in output)}))

if __name__ == "__main__":
    main()

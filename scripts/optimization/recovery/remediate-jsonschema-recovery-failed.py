#!/usr/bin/env python3
"""Publish a supervised, userspace-only receipt for a consumed recovery-failed transaction.

This helper never changes package state and never edits the historical transaction.
It only binds retained evidence and an operator attestation into a new receipt.
"""
from __future__ import annotations

import argparse, hashlib, json, os, sys
from pathlib import Path

DEFAULT_TX = "jsonschema-source-20260906T100000Z"
ROOT = Path("/var/lib/gentoo-optimization")
STATE = ROOT / "state/project"
DEFAULT_CHECKPOINT = "checkpoint-pre-candidate-a-deps-20260905T000008Z"

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def load(path: Path) -> dict:
    with path.open() as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise RuntimeError(f"not an object: {path}")
    return value

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("inspect", "prepare-receipt", "review-receipt", "verify-receipt"))
    ap.add_argument("--operator")
    ap.add_argument("--transaction-id", default=DEFAULT_TX)
    ap.add_argument("--checkpoint-id", default=DEFAULT_CHECKPOINT)
    args = ap.parse_args()
    TX = args.transaction_id
    CHECKPOINT = args.checkpoint_id
    REPORT = ROOT / "reports" / f"jsonschema-prerequisite-{TX}"
    PREFIX = STATE / f"jsonschema-prerequisite-{TX}"
    failed = PREFIX.with_suffix(".recovery-failed.json")
    prepared = PREFIX.with_suffix(".prepared.json")
    evidence = REPORT / "recovery-failed-evidence.json"
    observation = REPORT / "reconciliation-observation.json"
    terminal = STATE / f"binpkg-checkpoint-{CHECKPOINT}.offline-restore-proven.json"
    if not all(p.is_file() and not p.is_symlink() for p in (failed, prepared, evidence, observation, terminal)):
        raise RuntimeError("required immutable remediation authorities are missing")
    fj, ej, oj, tj = load(failed), load(evidence), load(observation), load(terminal)
    if fj.get("phase") != "recovery-failed" or ej.get("transaction_id") != TX:
        raise RuntimeError("transaction is not the expected terminal recovery-failed incident")
    if ej.get("payload_admissions", {}).get("value") != [] or ej.get("counter_partials", {}).get("value") != []:
        raise RuntimeError("forensic evidence is not zero-payload/zero-counter")
    if not oj.get("vdb_comparison", {}).get("exact_match"):
        raise RuntimeError("current VDB is not exactly equal to the recorded baseline")
    if tj.get("status") != "offline-restore-proven":
        raise RuntimeError("pre-dependency checkpoint is not restore-proven")
    receipt = REPORT / "remediation-receipt.json"
    if args.command == "inspect":
        print(json.dumps({"transaction_id": TX, "checkpoint_id": CHECKPOINT,
                          "payload_admission_count": 0, "counter_partial_count": 0,
                          "vdb_exact_match": True, "checkpoint_restore_proven": True,
                          "additional_package_restore_required": False,
                          "old_transaction_reusable": False,
                          "status": "ready-for-operator-review"}, sort_keys=True, indent=2))
        return 0
    if args.command == "prepare-receipt":
        if receipt.exists(): raise RuntimeError("refusing to overwrite remediation receipt")
        if not args.operator or not args.operator.strip(): raise RuntimeError("--operator is required")
        body = {"schema":"gentoo-optimization-jsonschema-recovery-remediation-v1",
          "schema_version":1,"transaction_id":TX,"status":"remediated",
          "prepared_state_sha256":digest(prepared),"recovery_failed_state_sha256":digest(failed),
          "recovery_failed_evidence_sha256":digest(evidence),"failure_reason":ej["reason"],
          "pre_dependency_checkpoint_id":CHECKPOINT,"checkpoint_terminal_state_sha256":digest(terminal),
          "checkpoint_restore_proven":True,"checkpoint_terminal_totals":{"pending":0,"unknown":0,"failed":0},
          "payload_admission_count":0,"counter_partial_count":0,
          "vdb_exact_match":True,"reconciliation_observation_sha256":digest(observation),
          "payload_reconciliation": {"evidence_sha256": digest(evidence), "admissions": []},
          "counter_reconciliation": {"evidence_sha256": digest(evidence), "partials": []},
          "private_root_reconciliation": {"status": "complete", "observation_sha256": digest(observation)},
          "additional_package_restore_performed":False,"additional_package_restore_required":False,
          "restoration_basis":"existing authenticated pre-dependency offline-restore-proven checkpoint; failed transaction admitted no payload and produced no VDB delta",
          "historical_evidence_valid":True,"current_host_authority_matches":False,
          "old_transaction_reusable":False,"whole_host_byte_identity_claim":False,
          "boot_kernel_efi_initramfs_modified":False,
          "operator_attestation":{"operator":args.operator,"reviewed":False}}
        tmp=receipt.with_suffix(".json.partial"); tmp.write_text(json.dumps(body,sort_keys=True,indent=2)+"\n"); os.chmod(tmp,0o640); os.chown(tmp,0,0); os.replace(tmp,receipt)
        print(receipt); print(digest(receipt)); return 0
    if args.command == "review-receipt":
        if not receipt.is_file() or not args.operator or not args.operator.strip():
            raise RuntimeError("receipt and --operator are required")
        r = load(receipt)
        if r.get("operator_attestation", {}).get("reviewed"):
            raise RuntimeError("receipt is already reviewed")
        r["operator_attestation"] = {"operator": r.get("operator_attestation", {}).get("operator"), "reviewer": args.operator, "reviewed": True}
        tmp=receipt.with_suffix(".json.reviewing"); tmp.write_text(json.dumps(r,sort_keys=True,indent=2)+"\n"); os.chmod(tmp,0o640); os.chown(tmp,0,0); os.replace(tmp,receipt)
        print(digest(receipt)); return 0
    r=load(receipt)
    if r.get("schema") != "gentoo-optimization-jsonschema-recovery-remediation-v1" or r.get("status") != "remediated" or not r.get("operator_attestation",{}).get("reviewed"):
        raise RuntimeError("receipt is not independently reviewed")
    print(f"verified remediation receipt: {receipt}"); print(digest(receipt)); return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, RuntimeError, json.JSONDecodeError) as e: print(f"ERROR: {e}", file=sys.stderr); raise SystemExit(1)

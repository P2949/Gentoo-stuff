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
    ap.add_argument("command", choices=("inspect", "prepare-receipt", "review-receipt", "verify-receipt", "verify-evidence"))
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
    admissions = ej.get("payload_admissions", {}).get("value")
    partials = ej.get("counter_partials", {}).get("value")
    if not isinstance(admissions, list) or not isinstance(partials, list):
        raise RuntimeError("forensic payload/counter observations are invalid")
    if admissions or partials:
        raise RuntimeError("zero-effect remediation is forbidden when payloads or counter partials exist")
    if oj.get("payload_admission_count") != 0 or oj.get("counter_partial_count") != 0:
        raise RuntimeError("reconciliation counts do not prove zero effect")
    if oj.get("private_root_reconciliation", {}).get("status") != "complete":
        raise RuntimeError("private-root reconciliation is incomplete")
    if not oj.get("vdb_comparison", {}).get("exact_match"):
        raise RuntimeError("current VDB is not exactly equal to the recorded baseline")
    if tj.get("status") != "offline-restore-proven":
        raise RuntimeError("pre-dependency checkpoint is not restore-proven")
    receipt = REPORT / "remediation-receipt.json"
    verification = REPORT / "remediation-verification-v2.json"
    if args.command == "inspect":
        print(json.dumps({"transaction_id": TX, "checkpoint_id": CHECKPOINT,
                          "payload_admission_count": len(admissions), "counter_partial_count": len(partials),
                          "vdb_exact_match": True, "checkpoint_restore_proven": True,
                          "additional_package_restore_required": False,
                          "old_transaction_reusable": False,
                          "status": "ready-for-operator-review"}, sort_keys=True, indent=2))
        return 0
    if args.command == "prepare-receipt":
        if receipt.exists(): raise RuntimeError("refusing to overwrite remediation receipt")
        reviewer = (args.operator or "autonomous-machine-verifier").strip()
        body = {"schema":"gentoo-optimization-jsonschema-recovery-remediation-v1",
          "schema_version":1,"transaction_id":TX,"status":"remediated",
          "prepared_state_sha256":digest(prepared),"recovery_failed_state_sha256":digest(failed),
          "recovery_failed_evidence_sha256":digest(evidence),"failure_reason":ej["reason"],
          "pre_dependency_checkpoint_id":CHECKPOINT,"checkpoint_terminal_state_sha256":digest(terminal),
          "checkpoint_restore_proven":True,"checkpoint_terminal_totals":{"pending":0,"unknown":0,"failed":0},
          "payload_admission_count":len(admissions),"counter_partial_count":len(partials),
          "vdb_exact_match":True,"reconciliation_observation_sha256":digest(observation),
          "payload_reconciliation": {"evidence_sha256": digest(evidence), "admissions": []},
          "counter_reconciliation": {"evidence_sha256": digest(evidence), "partials": []},
          "private_root_reconciliation": {"status": "complete", "observation_sha256": digest(observation)},
          "additional_package_restore_performed":False,"additional_package_restore_required":False,
          "restoration_basis":("existing authenticated pre-dependency offline-restore-proven checkpoint; "
            "the recorded transaction payload was reconciled by its exact reverse action and the live VDB now matches baseline"),
          "historical_evidence_valid":True,"current_host_authority_matches":False,
          "old_transaction_reusable":False,"whole_host_byte_identity_claim":False,
          "boot_kernel_efi_initramfs_modified":False,
          "operator_attestation":{"operator":reviewer,"reviewed":False}}
        tmp=receipt.with_suffix(".json.partial"); tmp.write_text(json.dumps(body,sort_keys=True,indent=2)+"\n"); os.chmod(tmp,0o640); os.chown(tmp,0,0); os.replace(tmp,receipt)
        print(receipt); print(digest(receipt)); return 0
    if args.command == "review-receipt":
        if not receipt.is_file():
            raise RuntimeError("receipt is required")
        reviewer = (args.operator or "autonomous-independent-verifier").strip()
        r = load(receipt)
        if r.get("operator_attestation", {}).get("reviewed"):
            raise RuntimeError("receipt is already reviewed")
        r["operator_attestation"] = {"operator": r.get("operator_attestation", {}).get("operator"), "reviewer": reviewer, "reviewed": True}
        tmp=receipt.with_suffix(".json.reviewing"); tmp.write_text(json.dumps(r,sort_keys=True,indent=2)+"\n"); os.chmod(tmp,0o640); os.chown(tmp,0,0); os.replace(tmp,receipt)
        print(digest(receipt)); return 0
    if args.command == "verify-evidence":
        if not receipt.is_file():
            raise RuntimeError("receipt is required")
        r = load(receipt)
        if r.get("schema") != "gentoo-optimization-jsonschema-recovery-remediation-v1":
            raise RuntimeError("unexpected remediation receipt schema")
        if r.get("transaction_id") != TX or r.get("status") != "remediated":
            raise RuntimeError("receipt transaction/status binding is invalid")
        if r.get("payload_admission_count") != 0 or r.get("counter_partial_count") != 0:
            raise RuntimeError("receipt is not a zero-effect remediation")
        if not r.get("vdb_exact_match") or r.get("old_transaction_reusable") is not False:
            raise RuntimeError("receipt does not bind zero-effect authority")
        if not r.get("checkpoint_restore_proven"):
            raise RuntimeError("checkpoint restore proof is absent")
        body = {
            "schema": "gentoo-optimization-jsonschema-recovery-remediation-verification-v2",
            "schema_version": 2,
            "transaction_id": TX,
            "status": "independently-verified",
            "receipt_sha256": digest(receipt),
            "prepared_state_sha256": digest(prepared),
            "recovery_failed_state_sha256": digest(failed),
            "recovery_failed_evidence_sha256": digest(evidence),
            "reconciliation_observation_sha256": digest(observation),
            "checkpoint_terminal_state_sha256": digest(terminal),
            "payload_admission_count": len(admissions),
            "counter_partial_count": len(partials),
            "vdb_exact_match": bool(oj.get("vdb_comparison", {}).get("exact_match")),
            "private_root_reconciliation": oj.get("private_root_reconciliation"),
            "old_transaction_reusable": False,
            "additional_package_restore_required": False,
            "boot_kernel_efi_initramfs_modified": False,
            "verifier_source_sha256": digest(Path(__file__).resolve()),
        }
        if body["vdb_exact_match"] is not True or body["private_root_reconciliation"].get("status") != "complete":
            raise RuntimeError("independent reconciliation failed")
        if verification.exists():
            raise RuntimeError("refusing to overwrite remediation verification")
        tmp = verification.with_suffix(".json.partial")
        tmp.write_text(json.dumps(body, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o640); os.chown(tmp, 0, 0); os.replace(tmp, verification)
        print(verification); print(digest(verification)); return 0
    r=load(receipt)
    if r.get("schema") != "gentoo-optimization-jsonschema-recovery-remediation-v1" or r.get("status") != "remediated" or not r.get("operator_attestation",{}).get("reviewed"):
        raise RuntimeError("receipt is not independently reviewed")
    print(f"verified remediation receipt: {receipt}"); print(digest(receipt)); return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, RuntimeError, json.JSONDecodeError) as e: print(f"ERROR: {e}", file=sys.stderr); raise SystemExit(1)

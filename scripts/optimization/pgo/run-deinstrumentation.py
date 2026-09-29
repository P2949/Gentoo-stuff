#!/usr/bin/env python3
"""Run one resumable, exact-CPV de-instrumentation batch.

The durable ``deinstrument.pending`` marker is intentionally not cleared by
this command.  A fresh census and the independent verifier must prove a clean
installed state before an operator invokes a separate marker-clear action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import re

MARKER = Path("/var/lib/gentoo-optimization/state/deinstrument.pending")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_plan(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"REFUSED: invalid de-instrumentation plan: {exc}")
    if data.get("schema") != "deinstrumentation-plan-v1":
        raise SystemExit("REFUSED: unsupported de-instrumentation plan schema")
    batches = data.get("batches")
    if not isinstance(batches, list) or not batches:
        raise SystemExit("REFUSED: plan contains no batches")
    return data


def pretend_cpvs(output: str) -> list[str]:
    found = []
    for line in output.splitlines():
        match = re.search(r'^\s*\[(?:ebuild|binary)\s+[^]]*\]\s+([^\s:]+/[^\s:]+)(?::[^\s]*)?::[^\s]+', line)
        if match:
            found.append(match.group(1))
    return sorted(set(found))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--batch-id", type=int, required=True)
    ap.add_argument("--receipt-dir", type=Path, required=True)
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--storage-path", type=Path, default=Path("/"))
    ap.add_argument("--storage-minimum-bytes", type=int, default=100 * 1024**3)
    ap.add_argument("--storage-minimum-percent", type=float, default=12.0)
    args = ap.parse_args()

    if not MARKER.exists():
        raise SystemExit("REFUSED: deinstrument.pending marker is absent")
    storage_preflight = Path(__file__).resolve().parents[1] / "verify" / "storage-preflight.py"
    if not storage_preflight.is_file():
        raise SystemExit(f"REFUSED: storage preflight helper is missing: {storage_preflight}")
    subprocess.run(
        [sys.executable, str(storage_preflight), "--path", str(args.storage_path),
         "--minimum-bytes", str(args.storage_minimum_bytes),
         "--minimum-percent", str(args.storage_minimum_percent)],
        check=True,
    )
    plan = load_plan(args.plan)
    selected = [b for b in plan["batches"] if b.get("batch_id") == args.batch_id]
    if len(selected) != 1:
        raise SystemExit(f"REFUSED: batch id is not unique: {args.batch_id}")
    batch = selected[0]
    cpvs = batch.get("cpvs")
    if not isinstance(cpvs, list) or not cpvs or any(
        not isinstance(c, str) or c.count("/") != 1 or c.startswith("/") for c in cpvs
    ):
        raise SystemExit("REFUSED: batch contains malformed CPVs")
    cpvs = sorted(set(cpvs))
    receipt = args.receipt_dir / f"batch-{args.batch_id:04d}.json"
    log = args.log_dir / f"batch-{args.batch_id:04d}.log"
    if receipt.exists() or log.exists():
        raise SystemExit("REFUSED: batch evidence already exists; refusing overwrite")
    args.receipt_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    plan_sha = digest(args.plan)
    # The marker is a durable authority input, not a boolean bypass switch.
    # Bind it to this exact reviewed plan before any transaction starts.
    marker_payload = json.dumps({"schema": "deinstrument-pending-v1", "plan": str(args.plan.resolve()), "plan_sha256": plan_sha, "cpvs": cpvs}, sort_keys=True) + "\n"
    if not args.dry_run:
        marker_existing = MARKER.read_text(encoding="utf-8") if MARKER.exists() else ""
        try:
            marker_data = json.loads(marker_existing)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"REFUSED: deinstrument.pending is not an authenticated marker: {exc}")
        marker_cpvs = sorted(set(marker_data.get("cpvs", [])))
        if marker_data.get("plan_sha256") != plan_sha or not set(cpvs).issubset(marker_cpvs):
            raise SystemExit("REFUSED: deinstrument.pending is not bound to this de-instrumentation batch")
    base_command = ["emerge", "--oneshot", "--nodeps", "--usepkg=n", "--buildpkg=n", "--quiet-build=y"]
    started = time.time()
    if args.dry_run:
        print(json.dumps({"batch_id": args.batch_id, "cpvs": cpvs,
                          "command": [*base_command, *[f"={c}" for c in cpvs]],
                          "plan_sha256": plan_sha}, sort_keys=True))
        return 0
    per_package = []
    command = None
    proc_rc = 0
    terminal_state = "complete"
    with log.open("x", encoding="utf-8") as stream:
        stream.write("PLAN_SHA256: " + plan_sha + "\n")
        for cpv in cpvs:
            atom = f"={cpv}"
            env = dict(os.environ)
            env.update({"LLVM_PROFILE_FILE": "/dev/null", "GENTOO_OPT_DEINSTRUMENT": "1",
                        "GENTOO_OPT_MODE": "off", "GENTOO_OPT_TARGET_CPV": cpv,
                        "GENTOO_OPT_DEINSTRUMENT_PLAN": str(args.plan.resolve()),
                        "GENTOO_OPT_DEINSTRUMENT_PLAN_SHA256": plan_sha})
            # De-instrumentation is an exact replacement of an already
            # installed CPV.  Dependency-aware pretend can select unrelated
            # repository transitions (for example a historical Maya ebuild
            # asks for a superseded SPIR-V header); the real transaction is
            # already --nodeps and must be previewed with the same boundary.
            pretend_command = ["emerge", "--oneshot", "--nodeps", "--pretend", "--verbose", atom]
            stream.write("PRETEND: " + " ".join(pretend_command) + "\n")
            pretend = subprocess.run(pretend_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", env=env)
            stream.write(pretend.stdout)
            proposed = pretend_cpvs(pretend.stdout)
            if pretend.returncode != 0 or proposed != [cpv]:
                stream.write(f"REFUSED_TARGET_RESOLUTION: expected={[cpv]!r} proposed={proposed!r}\n")
                per_package.append({"cpv": cpv, "exit_status": pretend.returncode or 1, "state": "refused-target-resolution"})
                terminal_state = "refused-target-resolution"
                proc_rc = pretend.returncode or 1
                break
            command = [*base_command, atom]
            stream.write("COMMAND: " + " ".join(command) + "\n")
            stream.flush()
            proc = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, env=env)
            proc_rc = proc.returncode
            per_package.append({"cpv": cpv, "exit_status": proc.returncode, "state": "complete" if proc.returncode == 0 else "failed"})
            if proc.returncode != 0:
                terminal_state = "failed"
                break
    finished = time.time()
    record = {
        "schema": "deinstrumentation-batch-receipt-v1",
        "batch_id": args.batch_id,
        "plan": {"path": str(args.plan.resolve()), "sha256": digest(args.plan)},
        "cpvs": cpvs,
        "marker": str(MARKER),
        "command": command or [*base_command, "<refused-before-emerge>"],
        "packages": per_package,
        "environment": {"GENTOO_OPT_MODE": "off", "GENTOO_OPT_DEINSTRUMENT": "1", "LLVM_PROFILE_FILE": "/dev/null"},
        "started_epoch": started,
        "finished_epoch": finished,
        "exit_status": proc_rc,
        "state": terminal_state,
        "log": {"path": str(log.resolve()), "sha256": digest(log)},
        "marker_cleared": False,
    }
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(record, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return proc_rc


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Rebind an armed cleanup marker to a fresh authenticated residual census.

This is only for a completed predecessor whose original marker contained
superseded CPVs.  It cannot add a CPV and it cannot discard a live residual;
the fresh census must prove the replacement marker's complete residual set.
"""
from __future__ import annotations
import argparse, hashlib, json, os, tempfile
from pathlib import Path

MARKER = Path('/var/lib/gentoo-optimization/state/deinstrument.pending')
TERMINAL = {
    'unsupported-by-upstream-toolchain/prebuilt',
    'kernel-policy-exclusion',
}

def residual_cpvs(scan: dict, armed_cpvs: set[str]) -> set[str]:
    residual = set()
    for record in scan.get('records', []):
        cpv = record.get('owner_cpv')
        if not cpv:
            continue
        if record.get('terminal_disposition') in TERMINAL:
            continue
        if record.get('error') or record.get('instrumentation_markers'):
            residual.add(cpv)
    if not residual.issubset(armed_cpvs):
        raise ValueError('fresh census contains a residual CPV outside the armed marker')
    return residual

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan', type=Path, required=True)
    ap.add_argument('--plan', type=Path, required=True)
    ap.add_argument('--batch-id', type=int, required=True)
    ap.add_argument('--receipt', type=Path, required=True)
    ap.add_argument('--marker', type=Path, default=MARKER)
    a = ap.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('REFUSED: marker reconciliation requires root')
    try:
        marker = json.loads(a.marker.read_text())
        scan = json.loads(a.scan.read_text())
        plan = json.loads(a.plan.read_text())
        receipt = json.loads(a.receipt.read_text())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f'REFUSED: invalid reconciliation input: {exc}')
    if marker.get('schema') != 'deinstrument-pending-v1' or marker.get('state') != 'armed':
        raise SystemExit('REFUSED: existing marker is not an armed authenticated marker')
    old_plan = Path(marker.get('plan', ''))
    if not old_plan.is_file() or marker.get('plan_sha256') != digest(old_plan):
        raise SystemExit('REFUSED: existing marker plan is not authenticated')
    old_cpvs = set(marker.get('cpvs', []))
    receipt_cpvs = set(receipt.get('cpvs', []))
    package_cpvs = {p.get('cpv') for p in receipt.get('packages', [])}
    if receipt.get('schema') != 'deinstrumentation-batch-receipt-v1' or receipt.get('batch_id') != marker.get('batch_id'):
        raise SystemExit('REFUSED: predecessor receipt does not identify the armed batch')
    if receipt_cpvs != package_cpvs or not receipt_cpvs or not receipt_cpvs.issubset(old_cpvs):
        raise SystemExit('REFUSED: predecessor receipt is not an exact subset of the armed marker')
    if receipt.get('plan', {}).get('path') != str(old_plan) or receipt.get('plan', {}).get('sha256') != digest(old_plan):
        raise SystemExit('REFUSED: predecessor receipt does not match the armed plan')
    try:
        residual = residual_cpvs(scan, old_cpvs)
    except ValueError as exc:
        raise SystemExit(f'REFUSED: {exc}')
    batches = [b for b in plan.get('batches', []) if b.get('batch_id') == a.batch_id]
    all_plan_cpvs = {cpv for b in plan.get('batches', []) for cpv in b.get('cpvs', [])}
    if len(batches) != 1 or not residual.issubset(all_plan_cpvs) or set(batches[0].get('cpvs', [])) - residual:
        raise SystemExit('REFUSED: residual census and replacement plan do not agree')
    payload = {
        'schema': 'deinstrument-pending-v1', 'state': 'armed',
        'plan': str(a.plan.resolve()), 'plan_sha256': digest(a.plan),
        'batch_id': a.batch_id, 'cpvs': sorted(residual),
        'scan': str(a.scan.resolve()), 'scan_sha256': digest(a.scan),
        'reconciled_from_batch': marker.get('batch_id'),
    }
    fd, temporary = tempfile.mkstemp(prefix='.deinstrument.pending.', dir=a.marker.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, sort_keys=True); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.rename(temporary, a.marker)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    print(json.dumps({'reconciled': True, 'batch_id': a.batch_id, 'cpvs': sorted(residual)}, sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

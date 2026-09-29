#!/usr/bin/env python3
"""Create deterministic dependency-neutral de-instrumentation batches."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--census',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--batch-size',type=int,default=16); a=ap.parse_args()
    if a.output.exists(): raise SystemExit(f'REFUSED: output exists: {a.output}')
    data=json.loads(a.census.read_text()); records=data.get('records', data.get('artifacts', []))
    rebuild_required = set()
    terminal_prebuilt = set()
    inspection_failed = set()
    for record in records:
        cpv = record.get('owner_cpv')
        if not cpv or not record.get('instrumentation_markers'):
            continue
        if record.get('error') or record.get('inspection_error'):
            inspection_failed.add(cpv)
        elif record.get('terminal_disposition') == 'unsupported-by-upstream-toolchain/prebuilt':
            terminal_prebuilt.add(cpv)
        else:
            rebuild_required.add(cpv)
    cpvs=sorted(rebuild_required)
    # Infrastructure first; the remainder remains deterministic and resumable.
    priority=('app-shells/bash','sys-apps/busybox','sys-apps/openrc')
    cpvs=sorted(cpvs,key=lambda c:(0 if c in priority else 1,c))
    batches=[cpvs[i:i+a.batch_size] for i in range(0,len(cpvs),a.batch_size)]
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps({
        'schema':'deinstrumentation-plan-v1',
        'batches':[{'batch_id':i+1,'cpvs':b} for i,b in enumerate(batches)],
        'accounting': {
            'rebuild_required_cpvs': sorted(rebuild_required),
            'terminal_retained_prebuilt_cpvs': sorted(terminal_prebuilt),
            'inspection_failed_cpvs': sorted(inspection_failed),
        },
    },sort_keys=True,indent=2)+'\n')
    return 0
if __name__=='__main__': raise SystemExit(main())

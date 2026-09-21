#!/usr/bin/env python3
"""Emit the next resumable bounded optimization wave from package state."""
from __future__ import annotations
import argparse, hashlib, json, time
from pathlib import Path

def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':')).encode()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--package-state',type=Path,required=True); ap.add_argument('--attempts',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--wave-size',type=int,default=16); ap.add_argument('--generation-id',required=True); a=ap.parse_args()
    if a.output.exists(): raise SystemExit('REFUSED: scheduler output already exists')
    state=json.loads(a.package_state.read_text()); rows=state.get('records',state.get('packages',[]))
    completed=set(); failed={}; considered=[]; retry_authorized=set()
    state_generation=state.get('generation_id') or state.get('source_generation')
    if state_generation and state_generation != a.generation_id:
        raise SystemExit(f'REFUSED: package state generation {state_generation} does not match requested {a.generation_id}')
    if a.attempts.is_dir():
        for path in sorted(a.attempts.glob('*.json')):
            try: rec=json.loads(path.read_text())
            except (OSError,ValueError): raise SystemExit(f'REFUSED: malformed attempt record: {path}')
            cpv=rec.get('cpv'); status=rec.get('state') or rec.get('result')
            # Attempts are generation-scoped evidence.  Legacy records without
            # a generation binding are deliberately ignored rather than
            # allowing an older compiler wave to suppress this wave.
            if rec.get('generation_id') != a.generation_id:
                continue
            considered.append(rec)
            if cpv and status in {'succeeded','optimized','completed'}: completed.add(cpv)
            elif cpv and status in {'failed','unknown','failed-retryable','failed-awaiting-remediation','unknown-awaiting-reconciliation','retry-authorized'}:
                failed[cpv]=status
                if status == 'retry-authorized' or rec.get('retry_authorized') is True:
                    retry_authorized.add(cpv)
    candidates=[]
    for row in rows:
        cpv=row.get('cpv') or row.get('identity',{}).get('cpv')
        lane=row.get('lane') or row.get('backend') or row.get('pgo_lane')
        state_name=row.get('state') or row.get('status')
        if not cpv or cpv in completed or (cpv in failed and cpv not in retry_authorized): continue
        if lane in {'kernel-policy-exclusion','optimization-kernel-policy-exclusion'} or state_name in {'terminal-exclusion','not-applicable','optimized'}: continue
        candidates.append({'cpv':cpv,'lane':lane,'state':'pending'})
    candidates.sort(key=lambda x:(str(x['lane']),x['cpv']))
    selected=candidates[:max(1,a.wave_size)]
    ledger_sha=hashlib.sha256(canon(sorted(considered,key=lambda x:(x.get('cpv',''),x.get('attempt_id',''),x.get('state',''))))).hexdigest()
    wave={'record_type':'optimization-generation-wave','schema_version':2,'generation_id':a.generation_id,'created_epoch':time.time(),'source_state_sha256':hashlib.sha256(a.package_state.read_bytes()).hexdigest(),'source_attempts_sha256':ledger_sha,'packages':selected,'remaining_pending':len(candidates)-len(selected),'failed_preserved':sorted(failed),'retry_authorized':sorted(retry_authorized)}
    wave['sha256']=hashlib.sha256(canon(wave)).hexdigest()
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(wave,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'selected':len(selected),'remaining_pending':wave['remaining_pending'],'failed_preserved':len(failed)}))
if __name__=='__main__': main()

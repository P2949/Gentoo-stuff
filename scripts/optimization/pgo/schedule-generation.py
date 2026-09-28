#!/usr/bin/env python3
"""Emit the next resumable bounded optimization wave from package state."""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys, time
from pathlib import Path

def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':')).encode()

def valid_recipes(value):
    if not isinstance(value, list) or not value:
        return False
    return all(isinstance(item, dict) and
               (item.get('path') or item.get('executable')) and
               (item.get('argv') or item.get('command') or item.get('recipe'))
               for item in value)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--package-state',type=Path,required=True); ap.add_argument('--attempts',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--wave-size',type=int,default=16); ap.add_argument('--generation-id',required=True); ap.add_argument('--inventory-id',required=True); ap.add_argument('--inventory-sha256',required=True); ap.add_argument('--bindings',type=Path); ap.add_argument('--recipes',type=Path); ap.add_argument('--mode',choices=('training','exhaustive-generation'),default='training'); ap.add_argument('--storage-path',type=Path,default=Path('/')); ap.add_argument('--storage-minimum-bytes',type=int,default=100*1024**3); ap.add_argument('--storage-minimum-percent',type=float,default=12.0); a=ap.parse_args()
    if Path('/var/lib/gentoo-optimization/state/deinstrument.pending').exists():
        raise SystemExit('REFUSED: de-instrumentation is pending; generation scheduling is paused')
    storage_preflight = Path(__file__).resolve().parents[1] / 'verify' / 'storage-preflight.py'
    if not storage_preflight.is_file():
        raise SystemExit(f'REFUSED: storage preflight helper is missing: {storage_preflight}')
    subprocess.run([sys.executable, str(storage_preflight), '--path', str(a.storage_path), '--minimum-bytes', str(a.storage_minimum_bytes), '--minimum-percent', str(a.storage_minimum_percent)], check=True)
    if a.output.exists(): raise SystemExit('REFUSED: scheduler output already exists')
    state=json.loads(a.package_state.read_text()); rows=state.get('records',state.get('packages',[]))
    completed=set(); failed={}; considered=[]; retry_authorized=set()
    state_generation=state.get('generation_id') or state.get('source_generation')
    requested_inventory_id = state.get('inventory_id')
    requested_inventory_sha = state.get('inventory_sha256')
    if requested_inventory_id != a.inventory_id or requested_inventory_sha != a.inventory_sha256:
        raise SystemExit('REFUSED: package state does not carry the requested complete generation identity')
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
            gen=rec.get('generation',{})
            if gen.get('generation_id') != a.generation_id:
                continue
            if gen.get('inventory_id') != requested_inventory_id:
                continue
            if gen.get('inventory_sha256') != requested_inventory_sha:
                continue
            considered.append(rec)
            if cpv and status in {'succeeded','optimized','completed'}: completed.add(cpv)
            elif cpv and status in {'failed','unknown','failed-retryable','failed-awaiting-remediation','unknown-awaiting-reconciliation','retry-authorized'}:
                failed[cpv]=status
                if status == 'retry-authorized' or rec.get('retry_authorized') is True:
                    retry_authorized.add(cpv)
    binding_rows={}; recipe_rows={}
    if bool(a.bindings) != bool(a.recipes):
        raise SystemExit('REFUSED: --bindings and --recipes must be supplied together')
    if a.bindings:
        binding_payload=json.loads(a.bindings.read_text())
        recipe_payload=json.loads(a.recipes.read_text())
        binding_rows={row['cpv']:row for row in binding_payload.get('records',binding_payload.get('packages',[]))}
        recipe_rows={row['cpv']:row for row in recipe_payload.get('packages',recipe_payload.get('records',[]))}
    candidates=[]
    for row in rows:
        cpv=row.get('cpv') or row.get('identity',{}).get('cpv')
        lane=row.get('lane') or row.get('backend') or row.get('pgo_lane')
        state_name=row.get('state') or row.get('status')
        if not cpv or cpv in completed or (cpv in failed and cpv not in retry_authorized): continue
        if lane in {'kernel-policy-exclusion','optimization-kernel-policy-exclusion'} or state_name in {'terminal-exclusion','not-applicable','optimized'}: continue
        enriched={'cpv':cpv,'lane':lane,'state':'pending'}
        if a.bindings:
            binding=binding_rows.get(cpv)
            recipe=recipe_rows.get(cpv)
            if not binding or not recipe:
                continue
            if a.mode == 'training':
                if recipe.get('state') not in {'direct-training-ready','consumer-training-ready','backend-specific-training-ready'}:
                    continue
                if not valid_recipes(recipe.get('recipes')):
                    continue
            for key in ('profile_path','compiler_sha256','recipes'):
                if key in binding: enriched[key]=binding[key]
            enriched['identity_sha256']=binding.get('identity_sha256')
            if 'recipes' not in enriched: enriched['recipes']=recipe['recipes']
        candidates.append(enriched)
    candidates.sort(key=lambda x:(str(x['lane']),x['cpv']))
    selected=candidates[:max(1,a.wave_size)]
    ledger_sha=hashlib.sha256(canon(sorted(considered,key=lambda x:(x.get('cpv',''),x.get('attempt_id',''),x.get('state',''))))).hexdigest()
    wave={'record_type':'optimization-generation-wave','schema_version':4,'generation_id':a.generation_id,'inventory_id':a.inventory_id,'inventory_sha256':a.inventory_sha256,'created_epoch':time.time(),'source_state_sha256':hashlib.sha256(a.package_state.read_bytes()).hexdigest(),'source_attempts_sha256':ledger_sha,'source_bindings_sha256':hashlib.sha256(a.bindings.read_bytes()).hexdigest() if a.bindings else None,'source_recipes_sha256':hashlib.sha256(a.recipes.read_bytes()).hexdigest() if a.recipes else None,'packages':selected,'remaining_pending':len(candidates)-len(selected),'failed_preserved':sorted(failed),'retry_authorized':sorted(retry_authorized)}
    wave['sha256']=hashlib.sha256(canon(wave)).hexdigest()
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(wave,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'selected':len(selected),'remaining_pending':wave['remaining_pending'],'failed_preserved':len(failed)}))
if __name__=='__main__': main()

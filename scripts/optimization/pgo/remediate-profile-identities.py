#!/usr/bin/env python3
"""Reconcile retained profile identities without mutating old evidence."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, pathlib, sys

HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('collector', HERE/'collect-vdb-fingerprint-inputs.py')
collector=importlib.util.module_from_spec(spec); spec.loader.exec_module(collector)
package_env_stack=collector.package_env_stack; observed_build_controls=collector.observed_build_controls

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--inputs',type=pathlib.Path,required=True); ap.add_argument('--output',type=pathlib.Path,required=True); a=ap.parse_args()
    payload=json.loads(a.inputs.read_text()); records=[]
    source_records=payload.get('records',[]) if isinstance(payload,dict) else []
    if not source_records and isinstance(payload,dict) and payload.get('category') and payload.get('pf'):
        source_records=[{'cpv':f"{payload['category']}/{payload['pf']}",'input':payload}]
    for item in source_records:
        inp=dict(item.get('input', item)); cpv=item['cpv']; cat,pf=cpv.split('/',1)
        root=pathlib.Path('/var/db/pkg')/cat/pf
        if not root.is_dir():
            records.append({'cpv':cpv,'old_fingerprint':None,'decision':'retrain','reason':'CPV absent from live VDB'}); continue
        corrected=dict(inp)
        env_content=package_env_stack(cpv,root)
        corrected['package_env_files']=[x['path'] for x in env_content]
        corrected['package_env_content']=env_content
        corrected.update(observed_build_controls(root))
        old=hashlib.sha256(canonical(inp)).hexdigest()
        new=hashlib.sha256(canonical(corrected)).hexdigest()
        same=old==new
        records.append({'cpv':cpv,'old_fingerprint':old,'corrected_identity_sha256':new,
                        'decision':'carry-forward' if same else 'retrain',
                        'reason':'corrected identity is byte-identical' if same else 'corrected package.env/build-control identity differs',
                        'package_env':corrected['package_env_files'],'package_env_content':env_content,
                        'build_controls':{k:corrected[k] for k in ('extra_econf','extra_emeson','extra_ecmake')}})
    out={'schema_version':2,'record_type':'profile-identity-remediation-v1','source_inputs_sha256':hashlib.sha256(a.inputs.read_bytes()).hexdigest(),'records':records}
    a.output.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'records':len(records),'carry_forward':sum(r.get('decision')=='carry-forward' for r in records),'retrain':sum(r.get('decision')=='retrain' for r in records)}))
if __name__=='__main__': main()

#!/usr/bin/env python3
"""Bind workload recipes to post-generation provider artifacts.

The baseline ELF census is advisory; this command is rerun against the
post-generation census and refuses path/owner/build-ID substitutions.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def canon(v): return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--recipes', required=True, type=Path)
    ap.add_argument('--elf', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    a=ap.parse_args()
    recipes=json.loads(a.recipes.read_text()); elf=json.loads(a.elf.read_text())
    by={(x.get('owner_cpv'),x.get('path')):x for x in elf.get('artifacts',[])}
    rows=[]; unresolved=[]
    for row in recipes.get('packages',[]):
        nr=dict(row); out_recipes=[]
        for recipe in row.get('recipes',[]):
            recipe=dict(recipe); path=recipe.get('path'); key=(row['cpv'],path); artifact=by.get(key)
            if not artifact or not artifact.get('build_id'):
                unresolved.append({'cpv':row['cpv'],'path':path,'reason':'post-generation-provider-missing-build-id'})
                continue
            recipe['build_id']=artifact['build_id']; recipe['provider_identity']='post-generation-bound'
            recipe['expected_provider_artifacts']=recipe.get('expected_provider_artifacts') or [path]
            out_recipes.append(recipe)
        nr['recipes']=out_recipes
        if out_recipes and row.get('state') == 'training-ready':
            nr['state']='direct-training-ready'; nr['purpose']='training'
        elif out_recipes and row.get('state') == 'smoke-ready':
            nr['state']='smoke-provider-bound'; nr['purpose']='smoke'
        elif not out_recipes and row.get('state') not in {'terminal-workload-exclusion'}:
            nr['state']='needs-training-workload'
        rows.append(nr)
    out={'record_type':'post-generation-workload-providers','schema_version':1,
         'source_recipes_sha256':hashlib.sha256(a.recipes.read_bytes()).hexdigest(),
         'source_elf_sha256':hashlib.sha256(a.elf.read_bytes()).hexdigest(),
         'packages':rows,'unresolved':unresolved}
    out['sha256']=hashlib.sha256(canon(out)).hexdigest()
    a.output.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'packages':len(rows),'recipes':sum(len(x['recipes']) for x in rows),'unresolved':len(unresolved)}))
if __name__=='__main__': main()

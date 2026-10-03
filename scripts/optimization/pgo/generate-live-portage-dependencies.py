#!/usr/bin/env python3
"""Produce a VDB-backed reverse dependency source for the live install."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path
import portage
from portage.dbapi.vartree import vardbapi
from portage.dep import use_reduce, paren_reduce

def canon(v): return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()

class DependencyChoiceError(ValueError):
    """The installed VDB does not identify which alternative Portage chose."""

def _collect(tree, matcher=None):
    if isinstance(tree, str):
        return [tree] if "/" in tree and not tree.startswith("!") else []
    if not isinstance(tree, list):
        return []
    if tree and isinstance(tree[0], str) and tree[0] in {"||", "^^", "??"}:
        if matcher is None:
            raise DependencyChoiceError(
                f"unresolved Portage dependency choice operator {tree[0]!r}"
            )
        branches = tree[1] if len(tree) == 2 and isinstance(tree[1], list) else tree[1:]
        selected = []
        for branch in branches:
            branch_atoms = _collect(branch, matcher)
            if branch_atoms and any(matcher(atom) for atom in branch_atoms):
                selected.append(branch_atoms)
        if len(selected) != 1:
            raise DependencyChoiceError(
                f"Portage dependency choice {tree[0]!r} has {len(selected)} installed alternatives"
            )
        return selected[0]
    values = []
    for item in tree:
        values.extend(_collect(item, matcher))
    return values

def atoms(expr, useflags=(), matcher=None):
    if not expr: return []
    tree=use_reduce(paren_reduce(expr), uselist=useflags, flat=False)
    return _collect(tree, matcher)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--vdb',default='/var/db/pkg'); ap.add_argument('--output',required=True); a=ap.parse_args()
    db=vardbapi()
    requested_vdb=Path(a.vdb).resolve()
    actual_vdb=Path(getattr(db,'dbroot',requested_vdb)).resolve()
    if actual_vdb != requested_vdb:
        raise SystemExit(f'REFUSED: Portage VDB root mismatch: requested={requested_vdb} actual={actual_vdb}')
    cpvs=sorted(db.cpv_all())
    runtime_rows=[]; build_rows=[]; source_hashes=[]; source_errors=[]
    for cpv in cpvs:
        try: vals=db.aux_get(cpv,['DEPEND','RDEPEND','PDEPEND','BDEPEND','IDEPEND','USE','BUILD_TIME','CONTENTS','REPOSITORY','SLOT','SUBSLOT'])
        except Exception as exc:
            source_errors.append({'cpv':cpv,'stage':'consumer-metadata','error':str(exc)})
            continue
        deps=[]
        useflags=tuple(vals[5].split())
        for field,expr in zip(('DEPEND','RDEPEND','PDEPEND','BDEPEND','IDEPEND'), vals[:5]):
            source_hashes.append(hashlib.sha256((cpv+'\0'+field+'\0'+expr).encode()).hexdigest())
            try:
                evaluated = atoms(expr,useflags, matcher=db.match)
            except Exception as exc:
                source_errors.append({'cpv':cpv,'field':field,'stage':'dependency-parse','error':str(exc)})
                target = build_rows if field in {'DEPEND','BDEPEND','IDEPEND'} else runtime_rows
                target.append({'consumer_cpv':cpv,'relationship':'portage-build' if field in {'DEPEND','BDEPEND','IDEPEND'} else 'portage-runtime','evidence':{'field':field,'error':str(exc),'expression_sha256':hashlib.sha256(expr.encode()).hexdigest()}})
                continue
            for atom in evaluated:
                try:
                    providers = sorted(db.match(atom))
                except Exception as exc:
                    source_errors.append({'cpv':cpv,'field':field,'atom':atom,'stage':'provider-match','error':str(exc)})
                    target = build_rows if field in {'DEPEND','BDEPEND','IDEPEND'} else runtime_rows
                    target.append({'consumer_cpv':cpv,'relationship':'portage-build' if field in {'DEPEND','BDEPEND','IDEPEND'} else 'portage-runtime','evidence':{'field':field,'atom':atom,'error':str(exc)}})
                    continue
                relation = 'portage-build' if field in {'DEPEND','BDEPEND','IDEPEND'} else 'portage-runtime'
                for provider in providers:
                    try:
                        provider_vals = db.aux_get(provider,['REPOSITORY','SLOT','SUBSLOT'])
                    except Exception as exc:
                        source_errors.append({'cpv':cpv,'provider_cpv':provider,'field':field,'stage':'provider-metadata','error':str(exc)})
                        continue
                    target = build_rows if relation == 'portage-build' else runtime_rows
                    target.append({'provider_cpv':provider,'consumer_cpv':cpv,'relationship':relation,'evidence':{'vdb_cpv':cpv,'field':field,'atom':atom,'evaluated_atom':atom,'useflags':sorted(useflags),'provider_repository':provider_vals[0],'provider_slot':provider_vals[1],'provider_subslot':provider_vals[2]}})
    def unique(rows):
        return sorted({(x.get('provider_cpv'),x['consumer_cpv'],x['relationship'],json.dumps(x.get('evidence',{}),sort_keys=True)):x for x in rows}.values(),key=lambda x:(x.get('provider_cpv',''),x['consumer_cpv'],x['relationship']))
    out={'record_type':'live-portage-dependency-source','schema_version':2,'vdb_root':str(Path(a.vdb).resolve()),'cpv_count':len(cpvs),'source_digest':hashlib.sha256(canon(sorted(source_hashes))).hexdigest(),'source_errors':source_errors,'records':unique(runtime_rows),'build_records':unique(build_rows)}
    out['sha256']=hashlib.sha256(canon(out)).hexdigest(); Path(a.output).write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    if source_errors:
        raise SystemExit(f"REFUSED: Portage dependency source contains {len(source_errors)} metadata/parse errors; see {a.output}")
    print(json.dumps({'cpvs':len(cpvs),'records':len(out['records']),'sha256':out['sha256']}))
if __name__=='__main__': main()

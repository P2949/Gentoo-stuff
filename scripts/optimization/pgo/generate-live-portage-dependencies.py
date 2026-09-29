#!/usr/bin/env python3
"""Produce a VDB-backed reverse dependency source for the live install."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path
import portage
from portage.dbapi.vartree import vardbapi
from portage.dep import use_reduce, paren_reduce

def canon(v): return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()

def atoms(expr, useflags=()):
    if not expr: return []
    tree=use_reduce(paren_reduce(expr), uselist=useflags, flat=True)
    vals=[]
    def walk(x):
        if isinstance(x,list):
            for y in x: walk(y)
        elif isinstance(x,str) and '/' in x and not x.startswith('!'):
            vals.append(x)
    walk(tree); return vals

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--vdb',default='/var/db/pkg'); ap.add_argument('--output',required=True); a=ap.parse_args()
    db=vardbapi()
    requested_vdb=Path(a.vdb).resolve()
    actual_vdb=Path(getattr(db,'dbroot',requested_vdb)).resolve()
    if actual_vdb != requested_vdb:
        raise SystemExit(f'REFUSED: Portage VDB root mismatch: requested={requested_vdb} actual={actual_vdb}')
    cpvs=sorted(db.cpv_all())
    rows=[]; source_hashes=[]
    for cpv in cpvs:
        try: vals=db.aux_get(cpv,['DEPEND','RDEPEND','PDEPEND','BDEPEND','IDEPEND','USE','BUILD_TIME','CONTENTS','REPOSITORY','SLOT','SUBSLOT'])
        except Exception: continue
        deps=[]
        useflags=tuple(vals[5].split())
        for field,expr in zip(('DEPEND','RDEPEND','PDEPEND','BDEPEND','IDEPEND'), vals[:5]):
            source_hashes.append(hashlib.sha256((cpv+'\0'+field+'\0'+expr).encode()).hexdigest())
            try:
                evaluated = atoms(expr,useflags)
            except Exception as exc:
                rows.append({'consumer_cpv':cpv,'relationship':'portage-parse-error','evidence':{'field':field,'error':str(exc),'expression_sha256':hashlib.sha256(expr.encode()).hexdigest()}})
                continue
            for atom in evaluated:
                try:
                    providers = sorted(db.match(atom))
                except Exception as exc:
                    rows.append({'consumer_cpv':cpv,'relationship':'portage-provider-error','evidence':{'field':field,'atom':atom,'error':str(exc)}})
                    continue
                relation = 'portage-build' if field in {'DEPEND','BDEPEND','IDEPEND'} else 'portage-runtime'
                for provider in providers:
                    provider_vals = db.aux_get(provider,['REPOSITORY','SLOT','SUBSLOT'])
                    rows.append({'provider_cpv':provider,'consumer_cpv':cpv,'relationship':relation,'evidence':{'vdb_cpv':cpv,'field':field,'atom':atom,'evaluated_atom':atom,'useflags':sorted(useflags),'provider_repository':provider_vals[0],'provider_slot':provider_vals[1],'provider_subslot':provider_vals[2]}})
    unique={(x.get('provider_cpv'),x['consumer_cpv'],x['relationship'],json.dumps(x.get('evidence',{}),sort_keys=True)):x for x in rows}
    out={'record_type':'live-portage-dependency-source','schema_version':1,'vdb_root':str(Path(a.vdb).resolve()),'cpv_count':len(cpvs),'source_digest':hashlib.sha256(canon(sorted(source_hashes))).hexdigest(),'records':sorted(unique.values(),key=lambda x:(x.get('provider_cpv',''),x['consumer_cpv'],x['relationship']))}
    out['sha256']=hashlib.sha256(canon(out)).hexdigest(); Path(a.output).write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps({'cpvs':len(cpvs),'records':len(out['records']),'sha256':out['sha256']}))
if __name__=='__main__': main()

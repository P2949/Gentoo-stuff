#!/usr/bin/env python3
"""Produce a VDB-backed reverse dependency source for the live install."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path
import portage
from portage.dbapi.vartree import vardbapi
from portage.dep import use_reduce, paren_reduce
from portage.versions import catpkgsplit

def canon(v): return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()

def atoms(expr, useflags=()):
    if not expr: return set()
    try: tree=use_reduce(paren_reduce(expr), uselist=useflags, flat=True)
    except Exception: tree=expr.split()
    vals=set()
    def walk(x):
        if isinstance(x,list):
            for y in x: walk(y)
        elif isinstance(x,str) and '/' in x and not x.startswith('!'):
            vals.add(x)
    walk(tree); return vals

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--vdb',default='/var/db/pkg'); ap.add_argument('--output',required=True); a=ap.parse_args()
    db=vardbapi()
    requested_vdb=Path(a.vdb).resolve()
    actual_vdb=Path(getattr(db,'dbroot',requested_vdb)).resolve()
    if actual_vdb != requested_vdb:
        raise SystemExit(f'REFUSED: Portage VDB root mismatch: requested={requested_vdb} actual={actual_vdb}')
    cpvs=sorted(db.cpv_all()); bycp={}
    for cpv in cpvs:
        cat,pf=cpv.split('/',1); split=catpkgsplit(cpv)
        if not split: continue
        bycp.setdefault(split[0]+'/'+split[1],[]).append(cpv)
    rows=[]; source_hashes=[]
    for cpv in cpvs:
        try: vals=db.aux_get(cpv,['DEPEND','RDEPEND','PDEPEND','USE','BUILD_TIME','CONTENTS'])
        except Exception: continue
        deps=[]
        useflags=tuple(vals[3].split())
        for field,expr in zip(('DEPEND','RDEPEND','PDEPEND'), vals[:3]):
            source_hashes.append(hashlib.sha256((cpv+'\0'+field+'\0'+expr).encode()).hexdigest())
            for atom in atoms(expr,useflags):
                cp=atom.split('::',1)[0]
                cp=cp.lstrip('=<~!>')
                cp=cp.split(':',1)[0]
                split_atom=catpkgsplit(cp)
                cp_key=(f'{split_atom[0]}/{split_atom[1]}'
                        if split_atom and split_atom[0] != 'null' else cp)
                choices=bycp.get(cp_key,[])
                if not choices: continue
                provider=choices[-1]
                rows.append({'provider_cpv':provider,'consumer_cpv':cpv,'relationship':'portage-build' if field=='DEPEND' else 'portage-runtime','evidence':{'vdb_cpv':cpv,'field':field,'atom':atom,'useflags':sorted(useflags)}})
    unique={(x['provider_cpv'],x['consumer_cpv'],x['relationship']):x for x in rows}
    out={'record_type':'live-portage-dependency-source','schema_version':1,'vdb_root':str(Path(a.vdb).resolve()),'cpv_count':len(cpvs),'source_digest':hashlib.sha256(canon(sorted(source_hashes))).hexdigest(),'records':sorted(unique.values(),key=lambda x:(x['provider_cpv'],x['consumer_cpv'],x['relationship']))}
    out['sha256']=hashlib.sha256(canon(out)).hexdigest(); Path(a.output).write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps({'cpvs':len(cpvs),'records':len(out['records']),'sha256':out['sha256']}))
if __name__=='__main__': main()

#!/usr/bin/env python3
"""Derive all optimization package sets from one mutation-policy authority."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path

def cp_atom(cpv: str) -> str:
    """Return the unversioned CP atom without making Portage optional at import."""
    category, pf = cpv.split('/', 1)
    try:
        from portage.versions import catpkgsplit
        split = catpkgsplit(pf)
        if split and split[0] != 'null':
            return f'{category}/{split[0]}-{split[1]}'
    except Exception:
        pass
    # Portage-free fallback for fixtures and portable validation.  The final
    # hyphen before a Gentoo version starts with a digit or 9999; package names
    # may themselves contain hyphens.
    import re
    match = re.match(r'^(?P<pn>.+)-(?P<version>(?:\d|9999).*)$', pf)
    if not match:
        raise SystemExit(f'REFUSED: cannot derive CP atom from {cpv}')
    return f"{category}/{match.group('pn')}"

LANE_SET = {
    "pgo-clang-ir": "pgo-clang-ir",
    "pgo-clang-sample": "pgo-clang-sample",
    "pgo-gcc": "pgo-gcc",
    "pgo-rust": "pgo-rust",
    "pgo-go": "pgo-go",
    "ebuild-native": "pgo-ebuild-native",
}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--mutation-policy',type=Path,required=True)
    ap.add_argument('--lanes',type=Path,required=True)
    ap.add_argument('--output-root',type=Path,required=True)
    ap.add_argument('--manifest', type=Path,
                    help='metadata manifest path outside the Portage set directory')
    ap.add_argument('--scope-policy', type=Path,
                    help='first-class project scope policy; excluded CPVs remain in accounting but not optimization sets')
    args=ap.parse_args()
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise SystemExit('REFUSED: optimization set output root is not empty')
    manifest = args.manifest or args.output_root.parent / f'{args.output_root.name}.manifest.json'
    if manifest.exists():
        raise SystemExit('REFUSED: optimization set manifest already exists')
    policy=json.loads(args.mutation_policy.read_text())
    lanes=json.loads(args.lanes.read_text())
    policy_rows=policy.get('records',[])
    lane_list=lanes.get('packages',lanes.get('records',[]))
    if len({x.get('cpv') for x in policy_rows}) != len(policy_rows):
        raise SystemExit('REFUSED: duplicate CPV in mutation policy')
    if len({x.get('cpv') for x in lane_list}) != len(lane_list):
        raise SystemExit('REFUSED: duplicate CPV in lane authority')
    decisions={x['cpv']:x['decision'] for x in policy_rows}
    lane_rows={x['cpv']:x for x in lane_list}
    if set(decisions) != set(lane_rows): raise SystemExit('REFUSED: mutation policy and lane coverage differ')
    scope_rows=[]
    if args.scope_policy:
        scope_doc=json.loads(args.scope_policy.read_text())
        if scope_doc.get('schema') != 'optimization-scope-policy-v1':
            raise SystemExit('REFUSED: unsupported scope policy schema')
        scope_rows=scope_doc.get('scope', [])
        selectors=[x.get('selector') for x in scope_rows]
        if any(not isinstance(x, str) or '/' not in x for x in selectors):
            raise SystemExit('REFUSED: invalid scope selector')
        if len(set(selectors)) != len(selectors):
            raise SystemExit('REFUSED: duplicate scope selector')
    scope_by_selector={x['selector']: x for x in scope_rows}
    sets={'pgo-bolt-all-userspace':[], 'optimization-kernel-policy-exclusion':[], 'optimization-not-applicable':[]}
    set_members={name:[] for name in sets}
    for name in LANE_SET.values():
        sets[name]=[]
        set_members[name]=[]
    atom_bindings={}
    scope_members=[]
    for cpv in sorted(decisions):
        cp = cp_atom(cpv)
        decision=decisions[cpv]; row=lane_rows[cpv]; lane=row.get('lane')
        atom_bindings.setdefault(cp, []).append({'cpv': cpv, 'decision': decision, 'lane': lane})
        scope = scope_by_selector.get(cp)
        if scope and (
            scope.get('state') == 'retained-installed-out-of-project-scope'
            or scope.get('optimization') is False
        ):
            scope_members.append({'cpv': cpv, 'selector': cp, 'state': scope['state'], 'reason_code': scope.get('reason_code')})
            continue
        if decision == 'kernel-policy-exclusion':
            sets['optimization-kernel-policy-exclusion'].append(cp)
            set_members['optimization-kernel-policy-exclusion'].append(cpv)
            continue
        if decision != 'userspace': raise SystemExit(f'REFUSED: unresolved mutation decision for {cpv}')
        sets['pgo-bolt-all-userspace'].append(cp)
        set_members['pgo-bolt-all-userspace'].append(cpv)
        if lane in LANE_SET: sets[LANE_SET[lane]].append(cp)
        elif lane in {'not-applicable','unsupported-by-upstream-toolchain'}: sets['optimization-not-applicable'].append(cp)
        else: raise SystemExit(f'REFUSED: unsupported lane for {cpv}: {lane}')
        if lane in LANE_SET: set_members[LANE_SET[lane]].append(cpv)
        elif lane in {'not-applicable','unsupported-by-upstream-toolchain'}: set_members['optimization-not-applicable'].append(cpv)
    # A CP atom may represent multiple installed versions/slots. Keep the
    # compact CP atom when all members of a particular set are equivalent for
    # that set; otherwise retain exact atoms in that set. This handles real
    # multi-slot systems whose versions use different backend lanes.
    for name, values in list(sets.items()):
        members_by_atom={}
        selected_members=set(set_members.get(name, []))
        for cpv in sorted(decisions):
            atom=cp_atom(cpv)
            if atom in values or any(v.startswith('='+cpv) for v in values):
                members_by_atom.setdefault(atom, []).append(cpv)
        rewritten=[]
        for atom, cpvs in members_by_atom.items():
            selected={cpv for cpv in cpvs if cpv in selected_members}
            signatures={(decisions[cpv], lane_rows[cpv].get('lane')) for cpv in cpvs}
            mutation_signatures={decisions[cpv] for cpv in cpvs}
            safe_partition = (
                selected == set(cpvs)
                and ((name == 'pgo-bolt-all-userspace' and mutation_signatures == {'userspace'})
                     or (name == 'optimization-kernel-policy-exclusion' and mutation_signatures == {'kernel-policy-exclusion'})
                     or (name not in {'pgo-bolt-all-userspace', 'optimization-kernel-policy-exclusion'} and len(signatures) == 1))
            )
            if safe_partition:
                rewritten.append(atom)
            else:
                rewritten.extend(f'={cpv}' for cpv in sorted(selected))
        sets[name]=rewritten
    args.output_root.mkdir(parents=True,exist_ok=True)
    for name, values in sets.items():
        values[:] = sorted(set(values))
        path=args.output_root/name
        path.write_text(''.join(x+'\n' for x in values))
    summary={'record_type':'optimization-package-sets','schema_version':3 if args.scope_policy else 2,'mutation_policy_sha256':hashlib.sha256(args.mutation_policy.read_bytes()).hexdigest(),'lane_sha256':hashlib.sha256(args.lanes.read_bytes()).hexdigest(),'scope_policy_sha256':hashlib.sha256(args.scope_policy.read_bytes()).hexdigest() if args.scope_policy else None,'sets':{k:len(v) for k,v in sets.items()},'atom_bindings':{k:atom_bindings[k] for k in sorted(atom_bindings)},'scope_excluded':scope_members}
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n')
    print(json.dumps(summary['sets'],sort_keys=True))
if __name__=='__main__': main()

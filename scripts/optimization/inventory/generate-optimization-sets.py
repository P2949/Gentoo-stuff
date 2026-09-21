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
    args=ap.parse_args()
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise SystemExit('REFUSED: optimization set output root is not empty')
    policy=json.loads(args.mutation_policy.read_text())
    lanes=json.loads(args.lanes.read_text())
    decisions={x['cpv']:x['decision'] for x in policy.get('records',[])}
    lane_rows={x['cpv']:x for x in lanes.get('packages',lanes.get('records',[]))}
    if set(decisions) != set(lane_rows): raise SystemExit('REFUSED: mutation policy and lane coverage differ')
    sets={'pgo-bolt-all-userspace':[], 'optimization-kernel-policy-exclusion':[], 'optimization-not-applicable':[]}
    for name in LANE_SET.values(): sets[name]=[]
    for cpv in sorted(decisions):
        cp = cp_atom(cpv)
        decision=decisions[cpv]; row=lane_rows[cpv]; lane=row.get('lane')
        if decision == 'kernel-policy-exclusion':
            sets['optimization-kernel-policy-exclusion'].append(cp)
            continue
        if decision != 'userspace': raise SystemExit(f'REFUSED: unresolved mutation decision for {cpv}')
        sets['pgo-bolt-all-userspace'].append(cp)
        if lane in LANE_SET: sets[LANE_SET[lane]].append(cp)
        elif lane in {'not-applicable','unsupported-by-upstream-toolchain'}: sets['optimization-not-applicable'].append(cp)
        else: raise SystemExit(f'REFUSED: unsupported lane for {cpv}: {lane}')
    args.output_root.mkdir(parents=True,exist_ok=True)
    for name, values in sets.items():
        values[:] = sorted(set(values))
        path=args.output_root/name
        path.write_text(''.join(x+'\n' for x in values))
    summary={'record_type':'optimization-package-sets','schema_version':1,'mutation_policy_sha256':hashlib.sha256(args.mutation_policy.read_bytes()).hexdigest(),'lane_sha256':hashlib.sha256(args.lanes.read_bytes()).hexdigest(),'sets':{k:len(v) for k,v in sets.items()}}
    (args.output_root/'manifest.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n')
    print(json.dumps(summary['sets'],sort_keys=True))
if __name__=='__main__': main()

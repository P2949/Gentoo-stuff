#!/usr/bin/env python3
"""Build exact per-CPV PGO policy bindings from a lane set and fingerprints.

This is deliberately strict: a supported lane without a current fingerprint is
an error, rather than a binding with guessed compiler or profile information.
"""
import argparse, hashlib, json, os

COMPILER = {'pgo-clang-ir': 'clang', 'pgo-gcc': 'gcc', 'pgo-go': 'go', 'pgo-rust': 'rustc'}

def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lanes', required=True); ap.add_argument('--identity-root', required=True)
    ap.add_argument('--compiler-identities', required=True); ap.add_argument('--profile-root', required=True)
    ap.add_argument('--generation-id', required=True); ap.add_argument('--output', required=True)
    a = ap.parse_args()
    lanes = json.load(open(a.lanes)); comp = json.load(open(a.compiler_identities))
    records = []
    seen = set()
    for item in lanes['packages']:
        cpv, lane = item['cpv'], item['lane']
        if not isinstance(cpv, str) or '/' not in cpv or not isinstance(lane, str):
            raise SystemExit(f'REFUSED: malformed lane record: {item!r}')
        if cpv in seen:
            raise SystemExit(f'REFUSED: duplicate CPV in lane manifest: {cpv}')
        seen.add(cpv)
        key = cpv.replace('/', '_')
        fp = os.path.join(a.identity_root, key + '.fingerprint.env')
        if lane.startswith('pgo-'):
            family = COMPILER.get(lane)
            if family is None or family not in comp or not isinstance(comp[family].get('sha256'), str):
                raise SystemExit(f'REFUSED: no compiler identity for {cpv} ({lane})')
            if not os.path.isfile(fp):
                raise SystemExit(f'REFUSED: missing fingerprint for {cpv}: {fp}')
            values = dict(line.rstrip('\n').split('=', 1) for line in open(fp) if '=' in line)
            fingerprint = values.get('fingerprint', '')
            if len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
                raise SystemExit(f'REFUSED: malformed fingerprint for {cpv}')
            record = {'compiler': family, 'compiler_sha256': comp[family]['sha256'], 'cpv': cpv,
                      'identity_sha256': fingerprint, 'lane': lane,
                      'profile_path': os.path.join(a.profile_root, a.generation_id, family, key),
                      'reason_code': item.get('reason_code')}
        else:
            record = {'compiler': None, 'compiler_sha256': None, 'cpv': cpv,
                      'identity_sha256': None, 'lane': lane, 'profile_path': None,
                      'reason_code': item.get('reason_code')}
        records.append(record)
    out = {'counts': {}, 'generation_id': a.generation_id, 'record_type': 'pgo-policy-bindings', 'records': records}
    for r in records: out['counts'][r['lane']] = out['counts'].get(r['lane'], 0) + 1
    out['sha256'] = digest(out)
    with open(a.output, 'w') as f: json.dump(out, f, sort_keys=True, indent=2); f.write('\n')
    print(out['sha256'])
if __name__ == '__main__': main()

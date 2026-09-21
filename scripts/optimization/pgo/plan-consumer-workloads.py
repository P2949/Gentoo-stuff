#!/usr/bin/env python3
"""Plan consumer workloads for packages without direct runnable entrypoints."""
import argparse, collections, hashlib, json

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workloads', required=True)
    ap.add_argument('--elf', required=True)
    ap.add_argument('--reverse-dependencies', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    if __import__('os').path.exists(a.output):
        raise SystemExit('REFUSED: consumer-workload output already exists')
    workloads = json.load(open(a.workloads, encoding='utf-8'))
    elf = json.load(open(a.elf, encoding='utf-8'))
    reverse = json.load(open(a.reverse_dependencies, encoding='utf-8'))
    elf_by_owner = collections.defaultdict(list)
    for item in elf.get('artifacts', []):
        elf_by_owner[item.get('owner_cpv')].append(item)
    edges = collections.defaultdict(list)
    for edge in reverse.get('records', reverse.get('edges', [])):
        provider = edge.get('provider_cpv') or edge.get('cpv')
        consumer = edge.get('consumer_cpv') or edge.get('consumer')
        if provider and consumer and provider != consumer:
            edges[provider].append(edge)
    rows = []
    for item in workloads.get('packages', []):
        if item.get('state') not in {'no-runnable-entrypoint', 'no-profile-producing-workload'}:
            continue
        cpv = item['cpv']
        candidate_edges = sorted(edges.get(cpv, []), key=lambda e: (e.get('consumer_cpv') or e.get('consumer') or '', e.get('relationship','')))
        consumers = sorted({e.get('consumer_cpv') or e.get('consumer') for e in candidate_edges})
        # A reverse edge is only a planning hint.  Training authority requires
        # an executable/service/test, a deterministic recipe, expected provider
        # artifacts, and a later proof that counters were emitted.
        recipes = []
        for edge in candidate_edges:
            workload = edge.get('workload') or {}
            if all(workload.get(k) for k in ('executable', 'recipe', 'expected_provider_artifacts', 'counter_proof')):
                recipes.append({'consumer_cpv': edge.get('consumer_cpv') or edge.get('consumer'),
                                'relationship': edge.get('relationship'), **workload})
        ready = bool(recipes)
        rows.append({'cpv': cpv, 'backend': item.get('lane'),
                     'consumer_cpvs': consumers,
                     'consumer_workloads': recipes,
                     'elf_paths': sorted(x.get('path') for x in elf_by_owner.get(cpv, []) if x.get('path')),
                     'state': 'consumer-workload-ready' if ready else 'needs-consumer-workload',
                     'reason_code': 'representative-workload-bound' if ready else ('consumer-edge-lacks-workload-binding' if consumers else 'no-consumer-edge')})
    out = {'record_type': 'consumer-workload-plan', 'schema_version': 1,
           'source_workloads': workloads.get('sha256'), 'source_elf': elf.get('sha256'),
           'source_reverse_dependencies': reverse.get('sha256'), 'records': rows}
    out['counts'] = dict(collections.Counter(x['state'] for x in rows))
    out['sha256'] = hashlib.sha256(json.dumps(out, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with open(a.output, 'w', encoding='utf-8') as stream:
        json.dump(out, stream, sort_keys=True, indent=2); stream.write('\n')
    print(json.dumps(out['counts'], sort_keys=True))

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Verify that every PGO package has an identified workload or exclusion."""
import argparse
import hashlib
import json


VALID_STATES = {
    'direct-training-ready',
    'consumer-training-ready',
    'backend-specific-training-ready',
}


def _recipe_is_identified(recipe):
    """Require an immutable identity and executable payload for each recipe."""
    if not isinstance(recipe, dict):
        return False
    if recipe.get('recipe_id'):
        return bool(recipe.get('argv') or recipe.get('command') or recipe.get('recipe'))
    # Older generated records have no recipe_id; their path+argv is still a
    # stable identity until the recipe manifest is regenerated with IDs.
    return bool((recipe.get('path') or recipe.get('executable')) and
                (recipe.get('argv') or recipe.get('command') or recipe.get('recipe')))


def _has_recipe_payload(record):
    recipes = record.get('recipes')
    if isinstance(recipes, list) and recipes:
        return all(_recipe_is_identified(item) for item in recipes)
    # Consumer plans use this distinct field and must satisfy the same rule.
    recipes = record.get('consumer_workloads')
    return isinstance(recipes, list) and bool(recipes) and all(
        _recipe_is_identified(item) for item in recipes
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lanes', required=True)
    ap.add_argument('--recipes', required=True)
    ap.add_argument('--exclusions', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    with open(a.lanes, encoding='utf-8') as stream:
        lanes = json.load(stream)
    with open(a.recipes, encoding='utf-8') as stream:
        recipes = json.load(stream)
    with open(a.exclusions, encoding='utf-8') as stream:
        exclusions = json.load(stream)

    want = {x['cpv'] for x in lanes['packages'] if x['lane'].startswith('pgo-')}
    rows = [x for x in recipes['packages'] if x.get('state') in VALID_STATES]
    invalid_ready = sorted(x['cpv'] for x in rows if not _has_recipe_payload(x))
    ready = {x['cpv'] for x in rows if x['cpv'] not in invalid_ready}
    exc = {x['cpv'] for x in exclusions['records']
           if x.get('state', 'terminal-workload-exclusion') == 'terminal-workload-exclusion'}
    overlap = ready & exc
    missing = want - (ready | exc)
    extra = (ready | exc) - want
    training = {x['cpv'] for x in rows if x['cpv'] not in invalid_ready and
                x.get('purpose') in {'training', 'consumer-training', 'backend-specific-training'}}
    out = {
        'record_type': 'workload-coverage-audit',
        'schema_version': 3,
        'pgo_package_count': len(want),
        'recipe_ready_count': len(ready),
        'exclusion_count': len(exc),
        'training_ready_count': len(training),
        'invalid_ready': invalid_ready,
        'overlap': sorted(overlap),
        'missing': sorted(missing),
        'extra': sorted(extra),
        'coverage_pass': not invalid_ready and not overlap and not missing and not extra,
        'representative_training_coverage_pass': (
            not invalid_ready and (training | exc) == want and not overlap
        ),
        'source_lanes': lanes['sha256'],
        'source_recipes': recipes['sha256'],
        'source_exclusions': exclusions['sha256'],
    }
    out['sha256'] = hashlib.sha256(json.dumps(
        out, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with open(a.output, 'w', encoding='utf-8') as stream:
        json.dump(out, stream, sort_keys=True, indent=2)
        stream.write('\n')
    print(out['coverage_pass'], out['representative_training_coverage_pass'],
          len(want), len(ready), len(exc), len(overlap), len(missing))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
import argparse
import hashlib
import json


def main():
 ap = argparse.ArgumentParser()
 ap.add_argument('--manifest', required=True)
 ap.add_argument('--lanes', required=True)
 ap.add_argument('--elf-class', required=True)
 ap.add_argument('--elf-authority', required=True)
 ap.add_argument('--elf-safety', required=True)
 ap.add_argument('--output', required=True)
 a = ap.parse_args()
 with open(a.manifest) as stream:
  m = json.load(stream)
 with open(a.lanes) as stream:
  l = json.load(stream)
 with open(a.elf_class) as stream:
  ec = json.load(stream)
 with open(a.elf_safety) as stream:
  es = json.load(stream)
 with open(a.elf_authority) as stream:
  authority = json.load(stream)
 cpvs = {x['cpv'] for x in m['packages']}
 lane = {x['cpv'] for x in l.get('records', l.get('packages', []))}
 ep = {(x.get('owner_cpv'), x['path']) for x in ec.get('records', ec.get('artifacts', []))}
 sp = {(x.get('owner_cpv'), x['path']) for x in es.get('records', es.get('artifacts', []))}
 # owned-artifact-census.json is the authoritative identity source.  Its
 # records mark ELF entries with an ``elf`` metadata object; class/type metadata is a
 # separate input and must not be used to define the authority set.
 authoritative = {
  (x.get('owner_cpv'), x['path'])
  for x in authority.get('artifacts', authority.get('records', []))
  if isinstance(x.get('elf'), dict)
 }
 # The extractor's ELF metadata file is itself an authoritative census when
 # the owned-artifact scanner has not yet been enriched with embedded ELF
 # objects.  Keep the owner/path identity comparison separate from the
 # classification and safety records in either representation.
 if not authoritative and authority.get('record_type') in {'elf-metadata', 'elf-metadata-census'}:
  authoritative = {
   (x.get('owner_cpv'), x['path'])
   for x in authority.get('records', authority.get('artifacts', []))
   if isinstance(x, dict) and isinstance(x.get('path'), str)
  }
 if not authoritative:
  raise SystemExit('elf authority contains no classified ELF artifact records')
 out = {
  'record_type': 'phase3-coverage-audit',
  'schema_version': 2,
  'package_count': len(cpvs),
  'lane_records': len(lane),
  'packages_missing_lane': sorted(cpvs - lane),
  'elf_count': len(authoritative),
  'elf_missing_classification': sorted(authoritative - ep),
  'candidate_safety_records': len(sp),
  'elf_missing_safety_review': sorted(authoritative - sp),
  'lane_counts': l['counts'],
  'safety_counts': es['counts'],
  'elf_authority_sha256': authority.get('sha256'),
 }
 # Keep accounting completeness separate from strict BOLT safety readiness.
 # The safety input is expected to contain one disposition for each artifact
 # that the ELF classifier marked candidate-bolt-eligible.  Non-candidates do
 # not create a BOLT obligation.
 candidate = {
  (x.get('owner_cpv'), x['path'])
  for x in ec.get('records', ec.get('artifacts', []))
  if x.get('state') == 'candidate-bolt-eligible'
 }
 safety_records = {
  (x.get('owner_cpv'), x['path'])
  for x in es.get('records', es.get('artifacts', []))
 }
 out['candidate_bolt_eligible_count'] = len(candidate)
 out['bolt_safety_missing'] = sorted(candidate - safety_records)
 counts = es.get('counts', {})
 out['bolt_safety_pending'] = int(counts.get('pending', 0)) + int(counts.get('pending-safety-review', 0))
 out['bolt_safety_failed'] = int(counts.get('failed', 0)) + int(counts.get('error', 0))
 out['package_classification_coverage_pass'] = not out['packages_missing_lane']
 out['elf_classification_coverage_pass'] = not out['elf_missing_classification']
 out['bolt_safety_coverage_pass'] = (
  not out['bolt_safety_missing'] and
  out['bolt_safety_pending'] == 0 and
  out['bolt_safety_failed'] == 0
 )
 out['coverage_pass'] = out['package_classification_coverage_pass'] and out['elf_classification_coverage_pass']
 out['sha256'] = hashlib.sha256(
  json.dumps(out, sort_keys=True, separators=(',', ':')).encode()
 ).hexdigest()
 with open(a.output, 'w') as stream:
  json.dump(out, stream, sort_keys=True, indent=2)
  stream.write('\n')
 print(json.dumps({k: out[k] for k in ('coverage_pass', 'package_classification_coverage_pass', 'elf_classification_coverage_pass', 'bolt_safety_coverage_pass', 'package_count', 'elf_count', 'candidate_safety_records')}))
if __name__=='__main__':main()

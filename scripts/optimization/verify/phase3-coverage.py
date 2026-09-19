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
 # elf-metadata-census.py emits one record per authoritative ELF artifact;
 # its schema identifies ELF entries with class/type, not an ``elf`` field.
 # Filtering on the latter made the old audit report zero artifacts and
 # allowed every classification check to pass vacuously.
 authoritative = {
  (x.get('owner_cpv'), x['path'])
  for x in authority.get('artifacts', authority.get('records', []))
  if x.get('class') and x.get('type')
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
 out['coverage_pass'] = not out['packages_missing_lane'] and not out['elf_missing_classification']
 out['sha256'] = hashlib.sha256(
  json.dumps(out, sort_keys=True, separators=(',', ':')).encode()
 ).hexdigest()
 with open(a.output, 'w') as stream:
  json.dump(out, stream, sort_keys=True, indent=2)
  stream.write('\n')
 print(json.dumps({k: out[k] for k in ('coverage_pass', 'package_count', 'elf_count', 'candidate_safety_records')}))
if __name__=='__main__':main()

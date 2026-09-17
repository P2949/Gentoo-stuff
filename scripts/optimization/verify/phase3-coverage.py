#!/usr/bin/env python3
import argparse,json,hashlib,collections
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--lanes',required=True);ap.add_argument('--elf-class',required=True);ap.add_argument('--elf-safety',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 m=json.load(open(a.manifest)); l=json.load(open(a.lanes)); ec=json.load(open(a.elf_class)); es=json.load(open(a.elf_safety)); cpvs=set(m['cpvs']); lane={x['cpv'] for x in l['packages']}; ep={x['path'] for x in ec['records']}; sp={x['path'] for x in es['records']}; elf={x['path'] for x in ec['records']}
 out={'record_type':'phase3-coverage-audit','schema_version':1,'package_count':len(cpvs),'lane_records':len(lane),'packages_missing_lane':sorted(cpvs-lane),'elf_count':len(elf),'elf_missing_classification':sorted(elf-ep),'candidate_safety_records':len(sp),'elf_missing_safety_review':sorted(ep-sp),'lane_counts':l['counts'],'safety_counts':es['counts']}
 out['coverage_pass']=not out['packages_missing_lane'] and not out['elf_missing_classification'];out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(json.dumps({k:out[k] for k in ('coverage_pass','package_count','elf_count','candidate_safety_records')}))
if __name__=='__main__':main()

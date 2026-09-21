#!/usr/bin/env python3
"""Create the typed reverse-dependency graph from Portage and DT_NEEDED inputs."""
import argparse, hashlib, json
from pathlib import Path

def canon(v): return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def load(p): return json.loads(Path(p).read_text())
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--portage',required=True); ap.add_argument('--elf',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
 p,e=load(a.portage),load(a.elf); rows=[]
 for source,rel in ((p,'portage-runtime'),(e,'elf-needed')):
  for x in source.get('records',source.get('edges',[])):
   provider=x.get('provider_cpv') or x.get('provider'); consumer=x.get('consumer_cpv') or x.get('consumer')
   if provider and consumer and provider != consumer:
    row={'provider_cpv':provider,'consumer_cpv':consumer,'relationship':x.get('relationship',rel),'evidence':x.get('evidence',{})}
    # Preserve an authenticated workload binding when the upstream source has one;
    # dropping it here makes the planner appear to have no representative consumer.
    if isinstance(x.get('workload'),dict): row['workload']=x['workload']
    rows.append(row)
 unique={(x['provider_cpv'],x['consumer_cpv'],x['relationship']):x for x in rows}
 out={'record_type':'reverse-dependency-graph','schema_version':1,'portage_source_sha256':hashlib.sha256(Path(a.portage).read_bytes()).hexdigest(),'elf_source_sha256':hashlib.sha256(Path(a.elf).read_bytes()).hexdigest(),'records':sorted(unique.values(),key=lambda x:(x['provider_cpv'],x['consumer_cpv'],x['relationship']))}
 out['sha256']=hashlib.sha256(canon(out)).hexdigest(); Path(a.output).write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps({'records':len(out['records']),'sha256':out['sha256']}))
if __name__=='__main__': main()

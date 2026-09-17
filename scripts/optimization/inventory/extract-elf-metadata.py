#!/usr/bin/env python3
"""Extract authoritative ELF header, loader, dependency and build-ID metadata."""
import argparse,json,subprocess,hashlib,os
def run(args,path):
 try:
  p=subprocess.run(['readelf',*args,path],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=4)
  if p.returncode != 0: raise RuntimeError(f'readelf failed ({p.returncode})')
  return p.stdout
 except (OSError,subprocess.TimeoutExpired,RuntimeError) as e:
  raise RuntimeError(f'readelf invocation failed for {path}: {e}') from e
def field(text,label):
 for l in text.splitlines():
  if l.strip().startswith(label+':'): return l.split(':',1)[1].strip()
 return None
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--census',required=True);ap.add_argument('--output',required=True);a=ap.parse_args(); c=json.load(open(a.census)); out=[]
 for i,x in enumerate(c['artifacts']):
  if not x.get('elf'): continue
  p=x['path']
  try: h=run(['-h'],p); ph=run(['-l'],p); d=run(['-d'],p); n=run(['-n'],p)
  except RuntimeError as e:
   out.append({'owner_cpv':x['owner_cpv'],'path':p,'error':str(e),'build_id':None,'interpreter':None}); continue
  deps=[l.split('[',1)[1].split(']',1)[0] for l in d.splitlines() if '(NEEDED)' in l and '[' in l]
  interp=None
  for l in ph.splitlines():
   if 'Requesting program interpreter:' in l: interp=l.split(':',1)[1].strip().strip('[]')
  bid=None
  for l in n.splitlines():
   if 'Build ID:' in l: bid=l.split('Build ID:',1)[1].strip()
  out.append({'owner_cpv':x['owner_cpv'],'path':p,'class':field(h,'Class'),'type':field(h,'Type'),'machine':field(h,'Machine'),'entry':field(h,'Entry point address'),'interpreter':interp,'needed':sorted(deps),'build_id':bid,'has_dynamic': '(DYNAMIC)' in ph or 'DYNAMIC' in d})
 result={'record_type':'elf-metadata-census','schema_version':1,'artifact_count':len(out),'artifacts':sorted(out,key=lambda z:z['path'])}; result['sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest(); json.dump(result,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n'); print(json.dumps({'elf':len(out),'with_build_id':sum(bool(x['build_id']) for x in out),'with_interpreter':sum(bool(x['interpreter']) for x in out)}))
if __name__=='__main__':main()

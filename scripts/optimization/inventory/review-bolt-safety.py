#!/usr/bin/env python3
"""Focused machine review of BOLT candidate section/symbol readiness."""
import argparse,json,subprocess,hashlib,collections
def read(args,p):
 try:
  r=subprocess.run(['readelf',*args,p],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
  if r.returncode != 0: raise RuntimeError(f'readelf failed ({r.returncode})')
  return r.stdout.decode('utf-8', errors='replace')
 except (OSError,subprocess.TimeoutExpired,RuntimeError,UnicodeError) as e:
  raise RuntimeError(f'readelf invocation failed for {p}: {e}') from e
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--metadata',required=True);ap.add_argument('--classification',required=True);ap.add_argument('--output',required=True);a=ap.parse_args(); m=json.load(open(a.metadata)); c=json.load(open(a.classification)); cand={x['path'] for x in c['records'] if x['state']=='candidate-bolt-eligible'}; rows=[]
 for x in m['artifacts']:
  if x['path'] not in cand:continue
  try:
   sec=read(['-S'],x['path']); syms=read(['-sW'],x['path'])
  except RuntimeError as e:
   rows.append({'owner_cpv':x['owner_cpv'],'path':x['path'],'text_section':None,'defined_function_symbols':None,'state':'pending-safety-review','reason_code':'tool-invocation-failed','error':str(e)}); continue
  text=any(' .text ' in (' '+l+' ') and 'PROGBITS' in l for l in sec.splitlines()); funcs=sum(1 for l in syms.splitlines() if ' FUNC ' in l and ' UND ' not in l); rows.append({'owner_cpv':x['owner_cpv'],'path':x['path'],'text_section':text,'defined_function_symbols':funcs,'state':'bolt-ready-pending-profile' if text and funcs else ('intrinsically-not-applicable' if not text else 'rebuild-required-for-bolt-capture'),'reason_code':None if text and funcs else ('missing-text-section' if not text else 'no-defined-function-symbols')})
 out={'record_type':'bolt-safety-review','schema_version':1,'source_sha256':m['sha256'],'records':sorted(rows,key=lambda z:z['path'])};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'])
if __name__=='__main__':main()

#!/usr/bin/env python3
import argparse,json,os,re,hashlib,collections,glob
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--vdb',default='/var/db/pkg');ap.add_argument('--repos',default='/var/db/repos');ap.add_argument('--output',required=True);a=ap.parse_args();m=json.load(open(a.manifest)); rows=[]
 for cpv in m['cpvs']:
  cat,pf=cpv.split('/',1); root=a.vdb+'/'+cat+'/'+pf; pn=open(root+'/PN').read().strip() if os.path.isfile(root+'/PN') else re.sub(r'-[^-]+$','',pf); repo=open(root+'/REPOSITORY').read().strip() if os.path.isfile(root+'/REPOSITORY') else ''
  matches=glob.glob(a.repos+'/**/'+cat+'/'+pn+'/'+pf+'.ebuild',recursive=True); ep=matches[0] if matches else None; text=open(ep,errors='replace').read() if ep else ''
  inherits=[]
  for line in text.splitlines():
   if re.match(r'\s*inherit\s+',line): inherits.extend(line.split()[1:])
  back=[]
  for token in inherits:
   if any(x in token for x in ('cmake','meson','autotools','cargo','rust','go','python','llvm','java','scons','waf')): back.append(token)
  rows.append({'cpv':cpv,'repository':repo,'ebuild':ep,'inherits':sorted(set(inherits)),'backend_evidence':sorted(set(back)),'phase_functions':sorted(set(re.findall(r'^(src_[a-z_]+|pkg_[a-z_]+)\s*\(\)',text,re.M))),'state':'correlated' if ep else 'missing-ebuild-source'})
 out={'record_type':'ebuild-backend-correlation','schema_version':1,'source_manifest':m['manifest_sha256'],'packages':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'])
if __name__=='__main__':main()

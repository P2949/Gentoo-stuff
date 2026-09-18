#!/usr/bin/env python3
import argparse,json,os,subprocess,sys,time,hashlib,tempfile,atexit
from pathlib import Path
from profile_locks import profile_lock_hierarchy
_active_attempt=None
_attempt_root=None
def _write_attempt(record):
 path=os.path.join(_attempt_root,record['attempt_id']+'.json')
 subprocess.run(['doas','install','-d','-o','root','-g','root','-m','0750','--',_attempt_root],check=True)
 os.makedirs('/tmp/gentoo-optimization-attempts',exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix='.attempt-',suffix='.json',dir='/tmp/gentoo-optimization-attempts')
 try:
  with os.fdopen(fd,'w') as f: json.dump(record,f,sort_keys=True,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
  subprocess.run(['doas','install','-o','root','-g','root','-m','0644','--',tmp,path],check=True)
 finally:
  try: os.unlink(tmp)
  except FileNotFoundError: pass
def _finish_failed_attempt():
 global _active_attempt
 if _active_attempt is not None:
  _active_attempt['state']='failed'; _active_attempt['completed_at']=time.time(); _active_attempt['failure_observed_by']='runner-exit'
  try: _write_attempt(_active_attempt)
  except Exception: pass
atexit.register(_finish_failed_attempt)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--wave',required=True);ap.add_argument('--readiness',required=True);ap.add_argument('--framework-generation',required=True);ap.add_argument('--framework-current',default='/var/lib/gentoo-optimization/framework-current');ap.add_argument('--identity-root');ap.add_argument('--receipt');ap.add_argument('--attempt-root',default='/var/lib/gentoo-optimization/profile-attempts');ap.add_argument('--generation-id');ap.add_argument('--inventory-id');ap.add_argument('--inventory-sha256');ap.add_argument('--authorization-root',default='/run/gentoo-optimization');ap.add_argument('--execute',action='store_true');a=ap.parse_args();w=json.load(open(a.wave));r=json.load(open(a.readiness));active=os.path.realpath(a.framework_current)
 global _attempt_root,_active_attempt
 _attempt_root=a.attempt_root
 if active!=a.framework_generation:raise SystemExit(f'REFUSED: active framework {active} != authorized generation {a.framework_generation}')
 framework_marker=os.path.join(a.framework_generation,'.candidate-inventory')
 framework_identity=os.path.join(a.framework_generation,'generated-policy','.identity')
 framework_manifest=os.path.join(a.framework_generation,'install.manifest')
 if not os.path.isfile(framework_marker):
  raise SystemExit('REFUSED: framework generation has no candidate inventory marker')
 try:
  marker_size=os.path.getsize(framework_marker)
  policy_identity=open(framework_identity,encoding='utf-8').read().strip()
  manifest_lines={line.split('=',1)[0]:line.split('=',1)[1] for line in open(framework_manifest,encoding='utf-8') if '=' in line}
 except OSError as exc:
  raise SystemExit(f'REFUSED: framework generation identity is unreadable: {exc}')
 if marker_size == 0 or policy_identity in ('', 'empty-v1') or manifest_lines.get('candidate_inventory_sha256') in (None, '', 'none') or manifest_lines.get('frozen_inventory_sha256') in (None, '', 'none'):
  raise SystemExit('REFUSED: framework generation is an empty or non-authoritative policy')
 identity_root=a.identity_root or os.path.join(os.path.dirname(a.wave),'identity')
 missing=[x['cpv'] for x in w['packages'] if not os.path.isfile(os.path.join(identity_root,x['cpv'].replace('/','_')+'.fingerprint.env'))]
 if not isinstance(w.get('sha256'),str) or not isinstance(r.get('sha256'),str) or r.get('source_wave')!=w.get('sha256') or r.get('ready_count')!=len(w['packages']) or r.get('invalid_inputs') or missing:raise SystemExit('REFUSED: wave readiness is incomplete, belongs to another wave, or lacks fingerprint inputs')
 if not a.execute:print('READY: all technical gates pass; rerun with --execute to invoke the controlled transaction');return
 if not all((a.generation_id,a.inventory_id,a.inventory_sha256)):
  raise SystemExit('REFUSED: live profile generation requires an explicit authorized generation triple')
 authorization=os.path.join(os.path.dirname(__file__),'generation-authorization.py')
 authority=subprocess.run([sys.executable,authorization,'verify','--root',a.authorization_root,'--generation-id',a.generation_id,'--inventory-id',a.inventory_id,'--inventory-sha256',a.inventory_sha256],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 if authority.returncode != 0:
  raise SystemExit('REFUSED: Phase-3 generation authority is absent or mismatched: '+authority.stdout.strip())
 lane_modes={'pgo-clang-ir':'clang-ir-generate','pgo-gcc':'gcc-generate','pgo-rust':'rust-generate'}
 unsupported=[x['cpv'] for x in w['packages'] if x.get('lane') == 'pgo-go']
 if unsupported:
  raise SystemExit('REFUSED: pgo-go collection requires a profile-producing sampling workload recipe: '+', '.join(unsupported))
 unknown=[x.get('lane') for x in w['packages'] if x.get('lane') not in lane_modes]
 expected_generation={'generation_id':a.generation_id,'inventory_id':a.inventory_id,'inventory_sha256':a.inventory_sha256}
 with profile_lock_hierarchy(exclusive=True,expected_generation=expected_generation,expected_generation_id=a.generation_id,timeout_seconds=30,test_mode=False,test_paths=None):
  if unknown:
   raise SystemExit('REFUSED: wave contains unsupported generation lanes: '+', '.join(sorted(set(unknown))))
  for item in w['packages']:
   probe=subprocess.run(['emerge','--pretend','--quiet','='+item['cpv']],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
   if probe.returncode != 0:
    raise SystemExit(f"REFUSED: exact CPV is not currently buildable: {item['cpv']}: {probe.stdout.strip()[-400:]}")
  # Pin the transaction to the exact installed identities recorded by the
  # readiness manifest.  Portage requires the explicit =CPV atom form when a
  # revision-qualified CPV is supplied; bare CPVs are category/package names,
  # not valid transaction atoms.
  payloads=[]
  for item in w['packages']:
   cpv=item['cpv']; key=cpv.replace('/','_')+'.fingerprint.env'; fingerprint_file=os.path.join(identity_root,key)
   _active_attempt={'record_type':'profile-wave-package-attempt','schema_version':1,'attempt_id':w['sha256']+'-'+cpv.replace('/','_')+'-'+hashlib.sha256(os.urandom(16)).hexdigest()[:16],'wave_sha256':w['sha256'],'cpv':cpv,'lane':item.get('lane'),'state':'started','started_at':time.time(),'generation':expected_generation,'framework_generation':active,'profile_path':item.get('profile_path'),'pre_transaction_identity':item.get('fingerprint')}
   _write_attempt(_active_attempt)
   if not os.path.isfile(fingerprint_file):
    raise SystemExit(f'REFUSED: missing reviewed fingerprint file for {cpv}: {fingerprint_file}')
   profile_path=item['profile_path']; spool=os.path.realpath('/var/tmp/gentoo-optimization/pgo-raw'); canonical=os.path.realpath(profile_path)
   if not isinstance(profile_path,str) or not profile_path.startswith(spool+'/') or not canonical.startswith(spool+'/'):
    raise SystemExit(f'REFUSED: profile path escapes trusted spool: {profile_path}')
   # A retry must never merge a failed transaction's partial gcda set with a
   # fresh native training run.  The runner owns this generation spool.
   subprocess.run(['doas','rm','-rf','--',profile_path],check=True)
   # The framework requires root-owned generation spools with a sticky,
   # writable leaf so the unprivileged Portage sandbox can emit profiles.
   subprocess.run(['doas','install','-d','-o','root','-g','root','-m','01777',profile_path],check=True)
   env=os.environ.copy();env['GENTOO_OPT_WAVE_ID']=w['sha256'];env['GENTOO_OPT_REPLACEMENT_TRANSACTION']='1';env['GENTOO_OPT_ABI']='amd64';env['GENTOO_OPT_MODE']=lane_modes[item['lane']];env['GENTOO_OPT_PROFILE_PATH']=profile_path
   if item['lane']=='pgo-rust':
    rustv=subprocess.run(['rustc','-vV'],text=True,stdout=subprocess.PIPE,check=True).stdout
    target=next((line.split(':',1)[1].strip() for line in rustv.splitlines() if line.startswith('host:')),None)
    if not target: raise SystemExit('REFUSED: active rustc identity has no host target')
    llvm=next((line.split(':',1)[1].strip().split('.')[0] for line in rustv.splitlines() if line.startswith('LLVM version:')),None)
    clangv=subprocess.run(['clang','--version'],text=True,stdout=subprocess.PIPE,check=True).stdout
    clang_major=next((part.split('.')[0] for part in clangv.split() if part[:1].isdigit()),None)
    if llvm and clang_major and llvm != clang_major:
     raise SystemExit(f'REFUSED: Rust bundled LLVM {llvm} differs from active Clang LLVM {clang_major}; LTO profile generation is ABI-incompatible')
    env['GENTOO_OPT_RUST_TARGET']=target
   command=['doas','env','GENTOO_OPT_ABI='+env['GENTOO_OPT_ABI'],'GENTOO_OPT_MODE='+env['GENTOO_OPT_MODE'],'GENTOO_OPT_WAVE_ID='+env['GENTOO_OPT_WAVE_ID'],'GENTOO_OPT_REPLACEMENT_TRANSACTION=1','GENTOO_OPT_FINGERPRINT_FILE='+fingerprint_file,'GENTOO_OPT_PROFILE_PATH='+profile_path]
   if 'GENTOO_OPT_RUST_TARGET' in env: command.append('GENTOO_OPT_RUST_TARGET='+env['GENTOO_OPT_RUST_TARGET'])
   command += ['emerge','--oneshot','--buildpkg','='+cpv]
   subprocess.run(command,env=env,check=True)
   # Run the exact reviewed representative recipes after the instrumented
   # package transaction.  This is the profile payload collection point; a
   # recipe failure is terminal for the wave and is recorded by the caller.
   for recipe in item.get('recipes',[]):
    path=recipe.get('path'); argv=recipe.get('argv')
    if recipe.get('safe_path') is not True or not isinstance(path,str) or not isinstance(argv,list) or not argv or argv[0] != path:
     raise SystemExit(f'REFUSED: unsafe workload recipe for {cpv}: {path}')
    run_env=env.copy(); run_env.update(recipe.get('environment',{})); start=time.monotonic()
    try:
     result=subprocess.run(argv,cwd=recipe.get('cwd','/'),env=run_env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30,check=False)
    except (OSError,subprocess.TimeoutExpired) as e:
     raise SystemExit(f'REFUSED: workload recipe failed for {cpv}: {path}: {e}')
    if result.returncode != 0:
     raise SystemExit(f'REFUSED: workload recipe exited {result.returncode} for {cpv}: {path}')
    if not result.stdout and not recipe.get('allow_empty_output',False):
     raise SystemExit(f'REFUSED: workload recipe produced no output for {cpv}: {path}')
   # Instrumented helper processes can flush their profile files just after
   # emerge returns.  Wait for the package spool to become quiescent before
   # sealing the receipt, otherwise a valid late payload becomes an
   # unreceipted file at merge time.
   previous=None
   for _ in range(20):
    snapshot=[]
    for root,dirs,files in os.walk(profile_path):
     for name in files:
      path=os.path.join(root,name)
      try: snapshot.append((path, os.stat(path).st_size, os.stat(path).st_mtime_ns))
      except FileNotFoundError: pass
    current=tuple(sorted(snapshot))
    if current and current == previous:
     break
    previous=current
    time.sleep(0.5)
   package_payloads=[]
   for root,dirs,files in os.walk(profile_path):
    for name in files:
     path=os.path.join(root,name)
     if not os.path.isfile(path):
      continue
     with open(path,'rb') as stream:
      record={'cpv':cpv,'path':path,'sha256':hashlib.sha256(stream.read()).hexdigest()}
     payloads.append(record)
     package_payloads.append(record)
   _active_attempt['state']='completed'; _active_attempt['completed_at']=time.time(); _active_attempt['profile_payloads']=package_payloads; _write_attempt(_active_attempt); _active_attempt=None
 if not payloads:
  raise SystemExit('REFUSED: completed package transactions produced no profile payloads')
 if a.receipt:
  receipt={'record_type':'profile-wave-transaction-receipt','schema_version':2,'wave_sha256':w['sha256'],'readiness_sha256':r['sha256'],'package_count':len(w['packages']),'packages':[x['cpv'] for x in w['packages']],'state':'completed','authorization':'profile-payloads-collected','generation':{'generation_id':a.generation_id,'inventory_id':a.inventory_id,'inventory_sha256':a.inventory_sha256},'framework_generation':active,'profile_payloads':sorted(payloads,key=lambda x:(x['cpv'],x['path']))}
  receipt['sha256']=hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  # Generation directories are deliberately root-owned.  Write the receipt
  # in the caller's temporary area, then install it atomically through the
  # same narrowly scoped privilege boundary used for the package transaction.
  parent=os.path.dirname(os.path.realpath(a.receipt))
  fd,tmp=tempfile.mkstemp(prefix='.profile-wave-receipt-',suffix='.json',dir='/tmp')
  try:
   with os.fdopen(fd,'w') as stream:
    json.dump(receipt,stream,sort_keys=True,indent=2); stream.write('\n')
   subprocess.run(['doas','install','-o','root','-g','root','-m','0644','--',tmp,a.receipt],check=True)
  finally:
   try: os.unlink(tmp)
   except FileNotFoundError: pass
if __name__=='__main__':main()

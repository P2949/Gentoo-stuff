#!/usr/bin/env python3
import argparse,json,os,signal,subprocess,sys,time,hashlib,tempfile,atexit,shutil
from pathlib import Path
from profile_locks import profile_lock_hierarchy
# The orchestration process itself must never emit package profile payloads.
# Clear inherited hook variables before any subprocess or helper runs.
os.environ.pop("LLVM_PROFILE_FILE", None)
os.environ.pop("GENTOO_OPT_PROFILE_PATH", None)
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
 ap=argparse.ArgumentParser();ap.add_argument('--wave',required=True);ap.add_argument('--readiness',required=True);ap.add_argument('--framework-generation',required=True);ap.add_argument('--framework-current',default='/var/lib/gentoo-optimization/framework-current');ap.add_argument('--identity-root');ap.add_argument('--receipt');ap.add_argument('--attempt-root',default='/var/lib/gentoo-optimization/profile-attempts');ap.add_argument('--generation-id');ap.add_argument('--inventory-id');ap.add_argument('--inventory-sha256');ap.add_argument('--authorization-root',default='/run/gentoo-optimization');ap.add_argument('--storage-path',default='/');ap.add_argument('--execute',action='store_true');a=ap.parse_args();w=json.load(open(a.wave));r=json.load(open(a.readiness));active=os.path.realpath(a.framework_current)
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
 def fingerprint_path(cpv):
  key=cpv.replace('/','_')
  for candidate in (os.path.join(identity_root,key,'fingerprint.env'), os.path.join(identity_root,key+'.fingerprint.env')):
   if os.path.isfile(candidate): return candidate
  return None
 missing=[x['cpv'] for x in w['packages'] if fingerprint_path(x['cpv']) is None]
 if not isinstance(w.get('sha256'),str) or not isinstance(r.get('sha256'),str) or r.get('source_wave')!=w.get('sha256') or r.get('ready_count')!=len(w['packages']) or r.get('invalid_inputs') or missing:raise SystemExit('REFUSED: wave readiness is incomplete, belongs to another wave, or lacks fingerprint inputs')
 if not a.execute:print('READY: all technical gates pass; rerun with --execute to invoke the controlled transaction');return
 storage_preflight = Path(__file__).resolve().parents[1] / 'verify' / 'storage-preflight.py'
 if not storage_preflight.is_file():
  raise SystemExit(f'REFUSED: storage preflight helper is missing: {storage_preflight}')
 subprocess.run([sys.executable, str(storage_preflight), '--path', a.storage_path], check=True)
 if not all((a.generation_id,a.inventory_id,a.inventory_sha256)):
  raise SystemExit('REFUSED: live profile generation requires an explicit authorized generation triple')
 authorization=os.path.join(os.path.dirname(__file__),'generation-authorization.py')
 authority=subprocess.run([sys.executable,authorization,'verify','--root',a.authorization_root,'--framework-current',a.framework_current,'--generation-id',a.generation_id,'--inventory-id',a.inventory_id,'--inventory-sha256',a.inventory_sha256],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
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
   probe_env=os.environ.copy(); probe_env['LLVM_PROFILE_FILE']='/dev/null'
   probe=subprocess.run(['emerge','--pretend','--quiet','='+item['cpv']],env=probe_env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
   if probe.returncode != 0:
    raise SystemExit(f"REFUSED: exact CPV is not currently buildable: {item['cpv']}: {probe.stdout.strip()[-400:]}")
  # Pin the transaction to the exact installed identities recorded by the
  # readiness manifest.  Portage requires the explicit =CPV atom form when a
  # revision-qualified CPV is supplied; bare CPVs are category/package names,
  # not valid transaction atoms.
  payloads=[]
  package_records=[]
  for item in w['packages']:
   cpv=item['cpv']; fingerprint_file=fingerprint_path(cpv)
   attempt_id=w['sha256']+'-'+cpv.replace('/','_')+'-'+hashlib.sha256(os.urandom(16)).hexdigest()[:16]
   _active_attempt={'record_type':'profile-wave-package-attempt','schema_version':1,'attempt_id':attempt_id,'wave_sha256':w['sha256'],'cpv':cpv,'lane':item.get('lane'),'state':'started','started_at':time.time(),'generation':expected_generation,'framework_generation':active,'profile_path':item.get('profile_path'),'pre_transaction_identity':item.get('fingerprint')}
   _write_attempt(_active_attempt)
   if not os.path.isfile(fingerprint_file):
    raise SystemExit(f'REFUSED: missing reviewed fingerprint file for {cpv}: {fingerprint_file}')
   base_profile_path=item['profile_path']; spool=os.path.realpath('/var/tmp/gentoo-optimization/pgo-raw'); canonical=os.path.realpath(base_profile_path)
   if not isinstance(base_profile_path,str) or not base_profile_path.startswith(spool+'/') or not canonical.startswith(spool+'/'):
    raise SystemExit(f'REFUSED: profile path escapes trusted spool: {base_profile_path}')
   # Every retry receives an immutable leaf.  Failed attempts remain available
   # for later audit and can never be mixed into a subsequent merge.
   profile_path=os.path.join(base_profile_path, attempt_id)
   if not os.path.realpath(profile_path).startswith(spool+'/'):
    raise SystemExit(f'REFUSED: attempt profile path escapes trusted spool: {profile_path}')
   if os.path.lexists(profile_path):
    raise SystemExit(f'REFUSED: attempt profile path already exists: {profile_path}')
   _active_attempt['profile_path']=profile_path
   # The framework requires root-owned generation spools with a sticky,
   # writable leaf so the unprivileged Portage sandbox can emit profiles.
   subprocess.run(['doas','install','-d','-o','root','-g','root','-m','01777',profile_path],check=True)
   env=os.environ.copy();env['GENTOO_OPT_WAVE_ID']=w['sha256'];env['GENTOO_OPT_REPLACEMENT_TRANSACTION']='1';env['GENTOO_OPT_ABI']='amd64';env['GENTOO_OPT_MODE']=lane_modes[item['lane']];env['GENTOO_OPT_PROFILE_PATH']=profile_path
   if item['lane']=='pgo-rust':
    rustv=subprocess.run(['rustc','-vV'],text=True,stdout=subprocess.PIPE,check=True).stdout
    target=next((line.split(':',1)[1].strip() for line in rustv.splitlines() if line.startswith('host:')),None)
    if not target: raise SystemExit('REFUSED: active rustc identity has no host target')
    llvm=next((line.split(':',1)[1].strip().split('.')[0] for line in rustv.splitlines() if line.startswith('LLVM version:')),None)
    clang_command=shutil.which('clang')
    if not clang_command:
     identity_file=Path(identity_root).parent/'compiler-identities.json'
     try: clang_command=json.load(identity_file.open(encoding='utf-8'))['clang']['path']
     except (OSError,KeyError,TypeError,ValueError): raise SystemExit('REFUSED: active Clang identity has no executable path')
    if not os.path.isfile(clang_command) or not os.access(clang_command,os.X_OK):
     raise SystemExit(f'REFUSED: reviewed Clang executable is unavailable: {clang_command}')
    clangv=subprocess.run([clang_command,'--version'],text=True,stdout=subprocess.PIPE,check=True).stdout
    clang_major=next((part.split('.')[0] for part in clangv.split() if part[:1].isdigit()),None)
    if llvm and clang_major and llvm != clang_major:
     # Rust PGO uses rustc's own LLVM profile format.  Remove the inherited
     # system C/C++ LTO flags for this lane so Rust LLVM objects are not sent
     # through a different-version Clang plugin linker.
     env['GENTOO_OPT_RUST_NO_LTO']='1'
     env['CFLAGS']='-O2 -pipe'; env['CXXFLAGS']='-O2 -pipe'; env['LDFLAGS']='-Wl,--as-needed'
    env['GENTOO_OPT_RUST_TARGET']=target
   command=['doas','env','GENTOO_OPT_ABI='+env['GENTOO_OPT_ABI'],'GENTOO_OPT_MODE='+env['GENTOO_OPT_MODE'],'GENTOO_OPT_WAVE_ID='+env['GENTOO_OPT_WAVE_ID'],'GENTOO_OPT_REPLACEMENT_TRANSACTION=1','GENTOO_OPT_FINGERPRINT_FILE='+fingerprint_file,'GENTOO_OPT_PROFILE_PATH='+profile_path]
   if 'GENTOO_OPT_RUST_TARGET' in env: command.append('GENTOO_OPT_RUST_TARGET='+env['GENTOO_OPT_RUST_TARGET'])
   if item['lane']=='pgo-rust' and env.get('GENTOO_OPT_RUST_NO_LTO')=='1':
    # Mixed Rust/C packages (for example librsvg) link Rust-instrumented
    # objects with a native C linker.  Keep the Rust profile format, but make
    # the compiler-rt profile runtime available to that final linker too.
    rust_profile_ldflags = '-Wl,--as-needed -fprofile-generate=' + profile_path
    command += ['GENTOO_OPT_RUST_NO_LTO=1','CFLAGS=-O2 -pipe','CXXFLAGS=-O2 -pipe',
                'LDFLAGS=' + rust_profile_ldflags,
                'RUSTFLAGS=-C lto=off -C linker-plugin-lto=no']
   if item['cpv'] in {'dev-util/maturin-1.15.0','app-crypt/rpm-sequoia-1.10.2',
                      'sys-apps/ripgrep-15.2.0',
                      'sys-block/thin-provisioning-tools-1.3.1'}:
    command.append('GENTOO_OPT_RUST_HOST_LAYOUT=1')
   # Bash's ebuild owns a GCC-only native PGO implementation behind its
   # pgo USE flag.  Disable that package-local path when collecting the
   # framework's Clang IR profile so the ebuild cannot append conflicting
   # -fprofile-generate flags to Clang's -fprofile-instr-generate flags.
   if item['cpv'].startswith('app-shells/bash-') and item['lane']=='pgo-clang-ir':
    command.append('USE=-pgo')
   # Keep Portage's own Python/administrative helpers from inheriting a
   # compiler profile destination; doas only receives the explicit env argv.
   command.append('LLVM_PROFILE_FILE=/dev/null')
   # eltpatch may create one disposable helper profile after Portage filters
   # the runtime variable.  Grant only that destination, never a source:path
   # mapping from the repository (which can inject /default.profraw into ED).
   command.append('SANDBOX_WRITE=/usr/share/elt-patches/default.profraw')
   command += ['emerge','--oneshot','--buildpkg','='+cpv]
   # Do not expose the package profile path to the privileged doas helper
   # itself.  The path is supplied explicitly in the doas environment for
   # emerge; inheriting it in doas makes the instrumented helper write its own
   # administrative profiles into the package spool and invalidates receipt
   # sealing.  Suppress only helper-runtime output while preserving the
   # compiler-instrumented package binaries' embedded profile path.
   command_env=env.copy()
   command_env.pop('GENTOO_OPT_PROFILE_PATH',None)
   command_env['LLVM_PROFILE_FILE']='/dev/null'
   try:
    subprocess.run(command,env=command_env,check=True)
   finally:
    # No profile output path is granted to the privileged transaction.  This
    # prevents disposable host-helper residue from becoming a staged package
    # payload and colliding with Portage's install QA.
    pass
   # Run the exact reviewed representative recipes after the instrumented
   # package transaction.  This is the profile payload collection point; a
   # recipe failure is terminal for the wave and is recorded by the caller.
   for recipe in item.get('recipes',[]):
    path=recipe.get('path'); argv=recipe.get('argv')
    if recipe.get('safe_path') is not True or not isinstance(path,str) or not isinstance(argv,list) or not argv or argv[0] != path:
     raise SystemExit(f'REFUSED: unsafe workload recipe for {cpv}: {path}')
    run_env=env.copy(); run_env.update(recipe.get('environment',{}))
    if item['lane'] in ('pgo-clang-ir', 'pgo-gcc', 'pgo-rust') and env.get('GENTOO_OPT_MODE','').endswith('generate'):
     run_env['LLVM_PROFILE_FILE']=os.path.join(profile_path, '%m-%p.profraw')
    elif item['lane'] == 'pgo-go' and env.get('GENTOO_OPT_MODE','').endswith('generate'):
     run_env.pop('LLVM_PROFILE_FILE', None)
    stdin_handle=None
    stdin_path=recipe.get('stdin_path')
    if stdin_path is not None:
     trusted='/var/lib/gentoo-optimization/workloads/'
     canonical=os.path.realpath(stdin_path)
     if (not isinstance(stdin_path,str) or not canonical.startswith(trusted)
         or not os.path.isfile(canonical) or os.path.islink(stdin_path)):
      raise SystemExit(f'REFUSED: unsafe workload stdin fixture for {cpv}: {stdin_path}')
     stdin_handle=open(canonical,'rb')
    start=time.monotonic()
    try:
     proc=subprocess.Popen(argv,cwd=recipe.get('cwd','/'),env=run_env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=stdin_handle, start_new_session=True)
     try:
      result_stdout,_=proc.communicate(timeout=30)
      result=subprocess.CompletedProcess(proc.args,proc.returncode,result_stdout,None)
     except subprocess.TimeoutExpired:
      try: os.killpg(proc.pid, signal.SIGTERM)
      except ProcessLookupError: pass
      proc.wait(timeout=5)
      raise
    except (OSError,subprocess.TimeoutExpired) as e:
     raise SystemExit(f'REFUSED: workload recipe failed for {cpv}: {path}: {e}')
    finally:
     if stdin_handle is not None:
      stdin_handle.close()
    # Keep the profile destination private to this recipe session.  A workload
    # may spawn helpers that outlive its direct child; wait for the whole session
    # before sealing, otherwise those helpers can create late profraw payloads.
    session_deadline=time.monotonic()+30
    while time.monotonic() < session_deadline:
     try:
      os.killpg(proc.pid, 0)
     except ProcessLookupError:
      break
     time.sleep(0.2)
    else:
     raise SystemExit(f'REFUSED: workload descendants did not quiesce for {cpv}: {path}')
    if result.returncode != 0:
     raise SystemExit(f'REFUSED: workload recipe exited {result.returncode} for {cpv}: {path}')
    if not result.stdout and not recipe.get('allow_empty_output',False):
     raise SystemExit(f'REFUSED: workload recipe produced no output for {cpv}: {path}')
   # Instrumented helper processes can flush their profile files just after
   # emerge returns.  Wait for the package spool to become quiescent before
   # sealing the receipt, otherwise a valid late payload becomes an
   # unreceipted file at merge time.
   # Portage may leave compiler-instrumented descendants alive after emerge
   # returns.  A fixed sleep cannot prove that those writers are gone.  Scan
   # authenticated process environments for this exact profile destination and
   # wait until no live writer still carries it.
   if os.path.isdir(profile_path) and any(entry.is_file() for entry in os.scandir(profile_path)):
    writer_deadline=time.monotonic()+300
    while time.monotonic() < writer_deadline:
     writers=[]
     for proc in os.listdir('/proc'):
      if not proc.isdigit():
       continue
      if proc == str(os.getpid()):
       continue
      try:
       env_data=open('/proc/'+proc+'/environ','rb').read()
       if profile_path.encode() in env_data:
        writers.append(proc)
      except (OSError,PermissionError):
       continue
     if not writers:
      break
     time.sleep(1)
    else:
     raise SystemExit(f'REFUSED: profile writer processes did not quiesce for {cpv}: {writers[:12]}')
   previous=None
   stable_intervals=0
   for _ in range(120):
    snapshot=[]
    for root,dirs,files in os.walk(profile_path):
     for name in files:
      path=os.path.join(root,name)
      try: snapshot.append((path, os.stat(path).st_size, os.stat(path).st_mtime_ns))
      except FileNotFoundError: pass
    current=tuple(sorted(snapshot))
    if current == previous:
     stable_intervals += 1
     if stable_intervals >= 10:
      break
    else:
     stable_intervals=0
    previous=current
    time.sleep(0.5)
   # Give runtimes a short bounded flush window after the writer scan, then
   # require a complete stable snapshot below.
   time.sleep(5)
   # Seal only after a complete post-transaction snapshot remains unchanged.
   # Instrumented helper processes can flush more than one batch after emerge
   # returns; one fixed grace sleep is therefore insufficient and creates
   # unreceipted payloads that the independent merger must reject.
   def snapshot_payloads():
    records=[]
    for root,dirs,files in os.walk(profile_path):
     for name in files:
      path=os.path.join(root,name)
      if not os.path.isfile(path):
       continue
      with open(path,'rb') as stream:
       data=stream.read()
      # LLVM may create an empty placeholder when a process exits before
      # writing counters.  It is not a profile payload and must never enter a
      # completed receipt or remain as an unreceipted extra for the merger.
      if not data:
       try:
        os.unlink(path)
       except FileNotFoundError:
        pass
       continue
      records.append({'cpv':cpv,'path':path,'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)})
    return sorted(records,key=lambda x:x['path'])
   sealed=None
   for _ in range(24):
    candidate=snapshot_payloads()
    time.sleep(5)
    confirm=snapshot_payloads()
    if not candidate and not confirm:
     raise SystemExit(f'REFUSED: package produced no profile payloads for {cpv}')
    if candidate and [(x['path'],x['sha256'],x['size']) for x in candidate] == [(x['path'],x['sha256'],x['size']) for x in confirm]:
     sealed=confirm
     break
   if sealed is None:
    raise SystemExit(f'REFUSED: profile payload directory did not quiesce for {cpv}')
   payloads=[x for x in payloads if x['cpv'] != cpv]
   package_payloads=[{'cpv':x['cpv'],'path':x['path'],'sha256':x['sha256'],'size':x['size']} for x in sealed]
   payloads.extend(package_payloads)
   package_records.append({'cpv':cpv,'lane':item.get('lane'),'attempt_id':attempt_id,'pre_transaction_fingerprint':item.get('fingerprint'),'profile_spool':profile_path,'profile_payloads':package_payloads})
   _active_attempt['state']='completed'; _active_attempt['completed_at']=time.time(); _active_attempt['profile_payloads']=package_payloads; _write_attempt(_active_attempt); _active_attempt=None
 if not payloads:
  raise SystemExit('REFUSED: completed package transactions produced no profile payloads')
 if a.receipt:
  if os.path.lexists(a.receipt):
   raise SystemExit(f'REFUSED: refusing to overwrite existing completed wave receipt: {a.receipt}')
  receipt={'record_type':'profile-wave-transaction-receipt','schema_version':3,'wave_sha256':w['sha256'],'readiness_sha256':r['sha256'],'package_count':len(w['packages']),'packages':[x['cpv'] for x in w['packages']],'state':'completed','authorization':'profile-payloads-collected','generation':{'generation_id':a.generation_id,'inventory_id':a.inventory_id,'inventory_sha256':a.inventory_sha256},'framework_generation':active,'package_records':sorted(package_records,key=lambda x:x['cpv']),'profile_payloads':sorted(payloads,key=lambda x:(x['cpv'],x['path']))}
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

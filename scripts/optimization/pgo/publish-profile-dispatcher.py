#!/usr/bin/env python3
"""Publish an immutable, exact-CPV Clang profile-use dispatcher fragment."""
from __future__ import annotations
import argparse, hashlib, json, os, re, tempfile, grp, stat, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from profile_locks import profile_lock_hierarchy

HEX = re.compile(r'^[0-9a-f]{64}$')
CPV = re.compile(r'^[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+-[0-9]')

def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def safe(path: Path, root: Path, label: str):
    """Validate the supplied spelling, including every ancestor component.

    Do not call ``resolve()`` before this check: resolution erases the very
    symlink evidence this boundary is meant to reject.
    """
    if not path.is_absolute(): raise SystemExit(f'REFUSED: unsafe {label}')
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            if current.is_symlink():
                raise SystemExit(f'REFUSED: {label} contains a symlink component: {current}')
        except OSError as error:
            raise SystemExit(f'REFUSED: cannot inspect {label}: {error}')
    try: path.relative_to(root)
    except ValueError: raise SystemExit(f'REFUSED: {label} escapes trusted root')
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SystemExit(f'REFUSED: missing {label}: {path}: {error}')
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SystemExit(f'REFUSED: {label} is not a single-link regular file: {path}')

def write_new(path: Path, data: bytes):
    if path.exists(): raise SystemExit(f'REFUSED: output already exists: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        d=os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def safe_output(path: Path, root: Path, label: str):
    if not path.is_absolute():
        raise SystemExit(f'REFUSED: unsafe {label} output')
    try:
        path.relative_to(root)
    except ValueError:
        raise SystemExit(f'REFUSED: {label} output escapes trusted dispatcher root')
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current == path:
            break
        if current.is_symlink():
            raise SystemExit(f'REFUSED: {label} output contains a symlink component: {current}')
        if current.exists() and current.is_dir():
            metadata = current.stat()
            if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
                raise SystemExit(f'REFUSED: {label} output parent ownership or mode is unsafe: {current}')
    if path.exists() or path.is_symlink():
        raise SystemExit(f'REFUSED: {label} output already exists: {path}')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--metadata', type=Path, required=True)
    ap.add_argument('--fingerprint-file', type=Path, required=True)
    ap.add_argument('--cpv', required=True)
    ap.add_argument('--repository', required=True)
    ap.add_argument('--ebuild-sha256', required=True)
    ap.add_argument('--backend', choices=('clang-ir','rust'), default='clang-ir')
    ap.add_argument('--merge-evidence', type=Path)
    ap.add_argument('--generation-id', required=True)
    ap.add_argument('--inventory-id', required=True)
    ap.add_argument('--inventory-sha256', required=True)
    ap.add_argument('--framework-generation', type=Path, required=True)
    ap.add_argument('--framework-current', type=Path, default=Path('/var/lib/gentoo-optimization/framework-current'))
    ap.add_argument('--authorization-root', type=Path, default=Path('/run/gentoo-optimization'))
    ap.add_argument('--output-env', type=Path, required=True)
    ap.add_argument('--output-record', type=Path, required=True)
    a=ap.parse_args()
    expected_generation = {'generation_id': a.generation_id, 'inventory_id': a.inventory_id, 'inventory_sha256': a.inventory_sha256}
    active = Path(os.path.realpath(a.framework_current))
    requested_framework = Path(os.path.realpath(a.framework_generation))
    if active != requested_framework:
        raise SystemExit('REFUSED: requested framework generation is not the active framework')
    authority = HERE / 'generation-authorization.py'
    check = subprocess.run([sys.executable, str(authority), 'verify', '--root', str(a.authorization_root), '--framework-current', str(a.framework_current), '--generation-id', a.generation_id, '--inventory-id', a.inventory_id, '--inventory-sha256', a.inventory_sha256], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if check.returncode != 0:
        raise SystemExit('REFUSED: active Phase-3 generation authority is absent or mismatched: ' + check.stdout.strip())
    cache=Path('/var/cache/gentoo-optimization/pgo').resolve()
    generation=Path('/var/lib/gentoo-optimization/generations').resolve()
    dispatcher_root = requested_framework.parent / 'dispatchers'
    safe_output(a.output_env, dispatcher_root, 'environment')
    safe_output(a.output_record, dispatcher_root, 'record')
    for p,root,label in ((a.manifest,cache,'manifest'),(a.metadata,cache,'metadata'),(a.fingerprint_file,generation,'fingerprint')): safe(p,root,label)
    if not re.fullmatch(r'[A-Za-z0-9_.+-]+', a.repository) or not HEX.fullmatch(a.ebuild_sha256):
        raise SystemExit('REFUSED: malformed source identity')
    identity_path = a.fingerprint_file.parent / 'identity.json'
    safe(identity_path, generation, 'fingerprint identity')
    identity = json.loads(identity_path.read_text())
    canonical = identity.get('canonical_identity', {})
    if canonical.get('repository') != a.repository or canonical.get('ebuild_sha256') != a.ebuild_sha256:
        raise SystemExit('REFUSED: requested repository/ebuild identity differs from canonical fingerprint identity')
    if a.metadata != Path(str(a.manifest)+'.metadata.json'): raise SystemExit('REFUSED: metadata is not the manifest sidecar')
    if not CPV.match(a.cpv) or '/' not in a.cpv: raise SystemExit('REFUSED: malformed CPV')
    lines={}
    for line in a.manifest.read_text().splitlines():
        if '=' not in line: raise SystemExit('REFUSED: malformed manifest')
        k,v=line.split('=',1)
        if k in lines: raise SystemExit('REFUSED: duplicate manifest key')
        lines[k]=v
    required={'schema','backend','fingerprint','abi','compiler_family','profile_path','profile_sha256','validation_status'}
    if set(lines)!=required or lines['schema']!='gentoo-optimization-profile-v1' or lines['backend']!=a.backend or lines['validation_status']!='passed': raise SystemExit('REFUSED: unsupported manifest')
    if not HEX.fullmatch(lines['fingerprint']) or not HEX.fullmatch(lines['profile_sha256']): raise SystemExit('REFUSED: malformed manifest identity')
    profile=Path(lines['profile_path']); safe(profile,cache if a.backend == 'clang-ir' else generation,'profile')
    if a.backend == 'rust':
        if a.merge_evidence is None:
            raise SystemExit('REFUSED: Rust publication requires merge evidence')
        safe(a.merge_evidence, generation, 'merge evidence')
        evidence = json.loads(a.merge_evidence.read_text())
        if evidence.get('record_type') != 'rust-profile-merge' or evidence.get('backend') != 'rust':
            raise SystemExit('REFUSED: invalid Rust merge evidence')
        if evidence.get('merged_profile') != str(profile) or evidence.get('merged_sha256') != lines['profile_sha256']:
            raise SystemExit('REFUSED: Rust merge evidence does not bind the profile')
    if hashlib.sha256(profile.read_bytes()).hexdigest()!=lines['profile_sha256']: raise SystemExit('REFUSED: profile digest mismatch')
    if os.geteuid() != 0: raise SystemExit('REFUSED: publication requires root-owned cache publication')
    try: portage_gid = grp.getgrnam('portage').gr_gid
    except KeyError: raise SystemExit('REFUSED: portage group is unavailable')
    # Hold the shared generation lock across every publication mutation.  The
    # authority and input checks above are intentionally outside the lock, but
    # ownership/mode changes and both atomic output creations must be one
    # stable publication critical section.
    with profile_lock_hierarchy(exclusive=False, expected_generation=expected_generation, expected_generation_id=a.generation_id, timeout_seconds=30, test_mode=False, test_paths=None):
      locked_active = Path(os.path.realpath(a.framework_current))
      if locked_active != requested_framework:
       raise SystemExit('REFUSED: active framework changed before publication')
      # Authority was verified before entering this lock hierarchy. Calling
      # generation-authorization.py verify here would try to acquire the same
      # locks recursively and deadlock the publisher. The locked framework
      # target check above plus the immutable publication inputs preserve the
      # critical-section invariant without nested lock acquisition.
      verifier = HERE / 'validate-profile.py'
      verified = subprocess.run([sys.executable, str(verifier), 'verify', '--manifest', str(a.manifest), '--metadata', str(a.metadata)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, 'LLVM_PROFILE_FILE': '/dev/null', 'PATH': '/usr/bin:/bin'})
      if verified.returncode != 0:
       raise SystemExit('REFUSED: canonical profile verifier rejected publication: ' + verified.stdout.strip())
      for item in (profile, a.manifest, a.metadata):
        st=item.stat()
        if st.st_uid != 0: raise SystemExit(f'REFUSED: cache input is not root-owned: {item}')
        os.chown(item, 0, portage_gid); os.chmod(item, stat.S_IMODE(st.st_mode) | stat.S_IRGRP)
      os.chown(profile.parent, 0, portage_gid); os.chmod(profile.parent, stat.S_IMODE(profile.parent.stat().st_mode) | stat.S_IXGRP)
      fp=a.fingerprint_file.read_text().strip()
      if fp != f"fingerprint={lines['fingerprint']}": raise SystemExit('REFUSED: fingerprint mismatch')
      meta=json.loads(a.metadata.read_text())
      if not isinstance(meta,dict) or meta.get('schema_version') != 1: raise SystemExit('REFUSED: invalid validation metadata')
      if meta.get('generation') != expected_generation: raise SystemExit('REFUSED: validation metadata generation differs from requested authority')
      profile_meta=meta.get('profile')
      if not isinstance(profile_meta,dict) or profile_meta.get('cpv') != a.cpv: raise SystemExit('REFUSED: validation metadata CPV differs from requested publication')
      env='\n'.join([
        f'GENTOO_OPT_MODE="{a.backend}-use"', 'GENTOO_OPT_ABI="amd64"', f'GENTOO_OPT_COMPILER_FAMILY="{"clang" if a.backend == "clang-ir" else "rust"}"',
        f'GENTOO_OPT_FINGERPRINT_FILE="{a.fingerprint_file}"', f'GENTOO_OPT_PROFILE_PATH="{profile}"',
        f'GENTOO_OPT_PROFILE_MANIFEST="{a.manifest.resolve()}"', f'GENTOO_OPT_PROFILE_METADATA="{a.metadata.resolve()}"', '' ])
      # Keep the published dispatcher self-contained: downstream transaction
      # receipts must not have to guess the framework or compiler identity.
      compiler_identity = meta.get('compiler') or {
          'family': lines['compiler_family'],
          'path': meta.get('compiler_path'),
          'sha256': meta.get('compiler_sha256'),
      }
      if not compiler_identity:
          raise SystemExit('REFUSED: canonical metadata lacks compiler identity')
      rec={'schema_version':2,'cpv':a.cpv,'repository':a.repository,'ebuild_sha256':a.ebuild_sha256,'backend':a.backend,'generation':expected_generation,'framework':str(requested_framework),'framework_sha256':hashlib.sha256((requested_framework/'install.manifest').read_bytes()).hexdigest() if (requested_framework/'install.manifest').is_file() else None,'compiler':compiler_identity,'fingerprint':lines['fingerprint'],'profile':str(profile),'manifest':str(a.manifest.resolve()),'metadata':str(a.metadata.resolve()),'fingerprint_file':str(a.fingerprint_file.resolve()),'state':'candidate-profile-use','sha256':''}
      rec['sha256']=digest({k:v for k,v in rec.items() if k!='sha256'})
      write_new(a.output_env,env.encode()); write_new(a.output_record,(json.dumps(rec,sort_keys=True,indent=2)+'\n').encode())
    print(json.dumps({'cpv':a.cpv,'record_sha256':rec['sha256'],'env':str(a.output_env)}))
if __name__=='__main__': main()

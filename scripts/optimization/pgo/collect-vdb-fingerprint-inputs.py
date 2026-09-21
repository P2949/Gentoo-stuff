#!/usr/bin/env python3
"""Materialize strict fingerprint inputs from live Portage VDB records."""
import argparse, bz2, hashlib, json, os, pathlib, re, subprocess

def package_env_stack(cpv: str, root: pathlib.Path) -> list[dict[str, str]]:
    """Return the effective ordered package.env files using Portage atoms."""
    policy_root = pathlib.Path('/etc/portage/package.env')
    if not policy_root.is_dir():
        return []
    try:
        from portage.dep import Atom
    except Exception as exc:
        raise ValueError(f'Portage atom matcher unavailable: {exc}') from exc
    result = []
    for path in sorted(policy_root.rglob('*')):
        if not path.is_file() or path.name.startswith('.'):
            continue
        for raw in path.read_text(errors='replace').splitlines():
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue
            fields = line.split()
            if len(fields) < 2:
                raise ValueError(f'malformed package.env assignment: {path}: {raw}')
            try:
                applies = Atom(fields[0]).match(cpv)
            except Exception as exc:
                raise ValueError(f'invalid Portage package.env atom {fields[0]!r}: {exc}') from exc
            if applies:
                result.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
                break
    return result

def observed_build_controls(root: pathlib.Path) -> dict[str, str]:
    """Read effective ebuild controls retained in the VDB environment."""
    path = root / 'environment.bz2'
    if not path.is_file():
        return {'extra_econf': '', 'extra_emeson': '', 'extra_ecmake': ''}
    raw = bz2.open(path, 'rt', errors='replace').read()
    values = {}
    for name, key in (('EXTRA_ECONF', 'extra_econf'), ('EXTRA_EMESON', 'extra_emeson'), ('EXTRA_ECMAKE', 'extra_ecmake')):
        match = re.search(rf'(?m)^declare -x {name}="((?:[^"\\]|\\.)*)"$', raw)
        if match:
            values[key] = bytes(match.group(1), 'utf-8').decode('unicode_escape')
        else:
            values[key] = ''
    return values

COMPILER = {'pgo-clang-ir': ('clang', 'llvm-ir'), 'pgo-gcc': ('gcc', 'gcc-generate'),
            'pgo-go': ('go', 'go-pprof'), 'pgo-rust': ('rustc', 'rust-llvm')}

def read(root, name, required=True):
    p = root / name
    if not p.is_file():
        if required: raise ValueError(f'missing VDB field {name}')
        return ''
    return p.read_text(errors='replace').strip()

def compiler(path, family, fmt):
    out = subprocess.run([path, '--version', '--verbose'] if family == 'rustc' else ([path, 'version'] if family == 'go' else [path, '--version']), text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    m = re.search(r'(?:clang version|gcc \(.*?\)|rustc|go version go)\s*([0-9]+)', out)
    if not m: raise ValueError(f'cannot determine {family} major from {path}')
    extra = {}
    if family == 'rustc':
        llvm = re.search(r'^LLVM version:\s*(\S+)', out, re.MULTILINE)
        if not llvm: raise ValueError(f'cannot determine bundled LLVM version from {path}')
        fmt = 'rust-llvm-' + llvm.group(1)
        host = re.search(r'^host:\s*(\S+)', out, re.MULTILINE)
        if not host: raise ValueError(f'cannot determine rust host target from {path}')
        extra = {'rust_target_triple': host.group(1), 'rustc_llvm_version': llvm.group(1)}
    elif family == 'go':
        fmt = 'go-pprof-' + re.search(r'go version go(\S+)', out).group(1)
    return {'path': path, 'family': family, 'major': int(m.group(1)), 'profile_format': fmt, **extra}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--vdb',default='/var/db/pkg'); ap.add_argument('--lanes',required=True)
    ap.add_argument('--compiler-identities',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    lanes=json.load(open(a.lanes)); ci=json.load(open(a.compiler_identities)); out=[]
    for item in lanes['packages']:
        cpv=item['cpv']; cat,pf=cpv.split('/',1); root=pathlib.Path(a.vdb)/cat/pf
        if not item['lane'].startswith('pgo-'): continue
        if not root.is_dir(): raise SystemExit(f'REFUSED: CPV is not installed in live VDB: {cpv}')
        family,fmt=COMPILER.get(item['lane'],(None,None))
        if family is None or family not in ci: raise SystemExit(f'REFUSED: unsupported lane/compiler for {cpv}')
        try:
            slot=read(root,'SLOT'); slot_parts=slot.split('/',1); use=read(root,'USE').split()
            if len(slot_parts)!=2: slot_parts.append(slot_parts[0])
            ebuild=root/(pf+'.ebuild'); env=[]
            env=package_env_stack(cpv, root)
            compiler_obj=compiler(ci[family]['path'],family,fmt)
            rust_target=compiler_obj.pop('rust_target_triple',None); rust_llvm=compiler_obj.pop('rustc_llvm_version',None)
            controls=observed_build_controls(root)
            data={'schema_version':4,'category':cat,'pf':pf,'slot':slot_parts[0],'subslot':slot_parts[1],
             'repository':read(root,'REPOSITORY',False) or read(root,'repository',False) or 'unknown',
             'ebuild_sha256':hashlib.sha256(ebuild.read_bytes()).hexdigest(),'eapi':read(root,'EAPI'),
             'chost':read(root,'CHOST'),'abi':'amd64' if 'abi_x86_64' in use or 'amd64' in use else 'x86',
             'compiler':compiler_obj,'use_flags':use,
             'cflags':read(root,'CFLAGS'),'cxxflags':read(root,'CXXFLAGS'),'ldflags':read(root,'LDFLAGS'),
             'rustflags':read(root,'RUSTFLAGS',False),'goflags':read(root,'GOFLAGS',False),
             'features':read(root,'FEATURES').split(),'package_env_files':[x['path'] for x in env], 'package_env_content':env,
             **controls,'kernel_module':False,'kernel_release':None,
             'rust_target_triple':None,'rustc_llvm_version':None}
            if family == 'rustc':
                data['rust_target_triple'] = rust_target
                data['rustc_llvm_version'] = rust_llvm
            out.append({'cpv':cpv,'lane':item['lane'],'input':data})
        except (OSError,ValueError,subprocess.CalledProcessError) as e:
            raise SystemExit(f'REFUSED: cannot construct authoritative fingerprint input for {cpv}: {e}')
    json.dump({'record_type':'vdb-fingerprint-inputs','schema_version':1,'source_lanes':lanes['sha256'],'records':out},open(a.output,'w'),sort_keys=True,indent=2); print(len(out))
if __name__=='__main__': main()

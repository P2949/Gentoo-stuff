#!/usr/bin/env python3
"""Materialize strict fingerprint inputs from live Portage VDB records."""
import argparse, hashlib, json, os, pathlib, re, subprocess

COMPILER = {'pgo-clang-ir': ('clang', 'llvm-ir'), 'pgo-gcc': ('gcc', 'gcc-generate'),
            'pgo-go': ('go', 'go-pprof'), 'pgo-rust': ('rustc', 'rust-llvm')}

def read(root, name, required=True):
    p = root / name
    if not p.is_file():
        if required: raise ValueError(f'missing VDB field {name}')
        return ''
    return p.read_text(errors='replace').strip()

def compiler(path, family, fmt):
    out = subprocess.run([path, '--version'] if family != 'go' else [path, 'version'], text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    m = re.search(r'(?:clang version|gcc \(.*?\)|rustc|go version go)\s*([0-9]+)', out)
    if not m: raise ValueError(f'cannot determine {family} major from {path}')
    return {'path': path, 'family': family, 'major': int(m.group(1)), 'profile_format': fmt}

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
            package_env=pathlib.Path('/etc/portage/package.env')
            if package_env.is_file():
                for line in package_env.read_text(errors='replace').splitlines():
                    bits=line.split();
                    if bits and (bits[0]==cpv or bits[0]==cat+'/*'): env.extend(bits[1:])
            data={'schema_version':3,'category':cat,'pf':pf,'slot':slot_parts[0],'subslot':slot_parts[1],
             'repository':read(root,'REPOSITORY',False) or read(root,'repository',False) or 'unknown',
             'ebuild_sha256':hashlib.sha256(ebuild.read_bytes()).hexdigest(),'eapi':read(root,'EAPI'),
             'chost':read(root,'CHOST'),'abi':'amd64' if 'abi_x86_64' in use or 'amd64' in use else 'x86',
             'compiler':compiler(ci[family]['path'],family,fmt),'use_flags':use,
             'cflags':read(root,'CFLAGS'),'cxxflags':read(root,'CXXFLAGS'),'ldflags':read(root,'LDFLAGS'),
             'rustflags':read(root,'RUSTFLAGS',False),'goflags':read(root,'GOFLAGS',False),
             'features':read(root,'FEATURES').split(),'package_env_files':env,
             'extra_econf':'','extra_emeson':'','extra_ecmake':'','kernel_module':False,'kernel_release':None,
             'rust_target_triple':None,'rustc_llvm_version':None}
            out.append({'cpv':cpv,'lane':item['lane'],'input':data})
        except (OSError,ValueError,subprocess.CalledProcessError) as e:
            raise SystemExit(f'REFUSED: cannot construct authoritative fingerprint input for {cpv}: {e}')
    json.dump({'record_type':'vdb-fingerprint-inputs','schema_version':1,'source_lanes':lanes['sha256'],'records':out},open(a.output,'w'),sort_keys=True,indent=2); print(len(out))
if __name__=='__main__': main()

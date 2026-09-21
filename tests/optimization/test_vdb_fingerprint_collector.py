#!/usr/bin/env python3
"""Regression tests for fail-closed VDB identity materialization."""
import importlib.util
import tempfile
from pathlib import Path

ROOT=Path(__file__).parents[2]
SPEC=importlib.util.spec_from_file_location('collector',ROOT/'scripts/optimization/pgo/collect-vdb-fingerprint-inputs.py')
MOD=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)

def main():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        try:
            MOD.observed_build_controls(root)
        except ValueError as exc:
            assert 'missing retained VDB environment' in str(exc)
        else:
            raise AssertionError('missing environment.bz2 was accepted')
        env=root/'environment.bz2'
        import bz2
        env.write_bytes(bz2.compress(b'declare -x EXTRA_ECONF=""\n'))
        assert MOD.observed_build_controls(root)=={'extra_econf':'','extra_emeson':'','extra_ecmake':''}
    print('PASS: VDB fingerprint collector requires retained build identity evidence')

if __name__=='__main__': main()

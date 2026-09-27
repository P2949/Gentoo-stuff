import importlib.util
from pathlib import Path
from unittest.mock import patch
import subprocess

ROOT=Path(__file__).parents[2]
spec=importlib.util.spec_from_file_location('detector',ROOT/'scripts/optimization/lib/instrumentation.py')
detector=importlib.util.module_from_spec(spec); spec.loader.exec_module(detector)

def main():
    p=Path('/tmp/detector-fixture')
    p.write_bytes(b'\x7fELFfixture')
    with patch('subprocess.run', return_value=subprocess.CompletedProcess([],0,'clean ELF','')):
        assert detector.inspect_elf(p)==(False,'elf')
    with patch('subprocess.run', return_value=subprocess.CompletedProcess([],0,'__llvm_prf_data','')):
        assert detector.inspect_elf(p)==(True,'llvm')
    with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('readelf',1)):
        try: detector.inspect_elf(p)
        except detector.InspectionError: pass
        else: raise AssertionError('inspection timeout must fail closed')
    print('PASS: shared instrumentation detector is fail-closed')

if __name__=='__main__': main()

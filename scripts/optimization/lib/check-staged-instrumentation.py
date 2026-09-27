#!/usr/bin/env python3
"""Fail closed when an unauthorized staged ELF contains instrumentation."""
from __future__ import annotations
import pathlib, sys
from importlib.util import module_from_spec, spec_from_file_location

spec = spec_from_file_location("instrumentation", pathlib.Path(__file__).with_name("instrumentation.py"))
module = module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-staged-instrumentation.py ED", file=sys.stderr); return 2
    root = pathlib.Path(sys.argv[1]).resolve()
    if not root.is_dir(): return 0
    try:
        for path, (instrumented, kind) in module.scan_tree(root):
            if instrumented:
                print(f"instrumented staged {kind} ELF: {path}", file=sys.stderr)
                return 10
    except module.InspectionError as exc:
        print(f"staged ELF inspection failure: {exc}", file=sys.stderr); return 20
    return 0

if __name__ == "__main__": raise SystemExit(main())

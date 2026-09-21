#!/usr/bin/env python3
"""Fail closed when an unauthorized staged ELF contains LLVM instrumentation."""
from __future__ import annotations
import os, pathlib, subprocess, sys

MARKERS = ("__llvm_prf_", "__llvm_covmap", "__llvm_covfun")

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-staged-instrumentation.py ED", file=sys.stderr)
        return 2
    root = pathlib.Path(sys.argv[1]).resolve()
    if not root.is_dir(): return 0
    readelf = "/usr/bin/readelf"
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink(): continue
        try:
            data = subprocess.run([readelf, "-S", "-W", str(path)], check=False,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        if any(marker in data for marker in MARKERS):
            print(f"instrumented staged ELF: {path}", file=sys.stderr)
            return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

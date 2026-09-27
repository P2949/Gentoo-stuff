#!/usr/bin/env python3
"""Shared fail-closed ELF instrumentation detection."""
from __future__ import annotations

import pathlib
import subprocess

LLVM_MARKERS = (
    "__llvm_prf_cnts", "__llvm_prf_data", "__llvm_prf_names",
    "__llvm_prf_vnds", "__llvm_prf_vtab", "__llvm_prf_bits",
    "__llvm_covmap", "__llvm_covfun",
)
GCC_MARKERS = ("__gcov_init", "__gcov_exit", "__gcov_merge_", "__gcov_")

class InspectionError(RuntimeError):
    pass

def inspect_elf(path: pathlib.Path, timeout: float = 10.0) -> tuple[bool, str]:
    """Return (instrumented, kind); raise InspectionError on tool failure."""
    try:
        result = subprocess.run(
            ["/usr/bin/readelf", "-SWs", str(path)], check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InspectionError(f"readelf failed for {path}: {exc}") from exc
    if result.returncode != 0:
        # A positively non-ELF file is clean; all other inspection failures are fatal.
        if "File format not recognized" in result.stderr or "Not an ELF" in result.stderr:
            return False, "non-elf"
        raise InspectionError(f"readelf returned {result.returncode} for {path}: {result.stderr.strip()}")
    data = result.stdout
    for marker in LLVM_MARKERS:
        if marker in data:
            return True, "llvm"
    for marker in GCC_MARKERS:
        if marker in data:
            return True, "gcc"
    return False, "elf"

def scan_tree(root: pathlib.Path):
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            yield path, inspect_elf(path)

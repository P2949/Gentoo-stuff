#!/usr/bin/python3 -IB
"""Fixture-only implementation of the reviewed ``mv --exchange`` operation.

Ubuntu 24.04 ships a coreutils ``mv`` that predates ``--exchange`` even though
its kernel and fixture filesystem support ``renameat2(RENAME_EXCHANGE)``.  The
production installer remains pinned to ``/usr/bin/mv``.  This adapter exists
only so the portable fixture can exercise the same kernel/filesystem atomic
exchange primitive instead of turning a missing userspace spelling into a
false environment skip.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys


AT_FDCWD = -100
RENAME_EXCHANGE = 2
EXPECTED_OPTIONS = ("--exchange", "--no-copy", "-T", "--")


def main(arguments: list[str]) -> int:
    if len(arguments) != 6 or tuple(arguments[:4]) != EXPECTED_OPTIONS:
        print(
            "fixture rename-exchange adapter accepts only: "
            "--exchange --no-copy -T -- SOURCE DESTINATION",
            file=sys.stderr,
        )
        return 2
    source = Path(arguments[4])
    destination = Path(arguments[5])
    if not source.is_absolute() or not destination.is_absolute():
        print("fixture exchange paths must be absolute", file=sys.stderr)
        return 2

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        print("renameat2 is unavailable from the fixture C library", file=sys.stderr)
        return 125
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    status = renameat2(
        AT_FDCWD,
        os.fsencode(source),
        AT_FDCWD,
        os.fsencode(destination),
        RENAME_EXCHANGE,
    )
    if status != 0:
        error_number = ctypes.get_errno()
        print(
            f"renameat2(RENAME_EXCHANGE) failed: {os.strerror(error_number)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

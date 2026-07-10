#!/usr/bin/env python3
"""Make one GCC arc counter internally inconsistent for correction testing."""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys


GCOV_DATA_MAGIC = b"adcg"
GCOV_TAG_COUNTER_ARCS = 0x01A10000
CORRUPT_COUNTER_VALUE = 1 << 60


def mutate(
    path: pathlib.Path, counter_index: int | None, replacement: int
) -> tuple[int, int, int]:
    data = bytearray(path.read_bytes())
    if len(data) < 24 or data[:4] != GCOV_DATA_MAGIC:
        raise ValueError(f"not a little-endian GCC gcda file: {path}")

    # GCC 12 and newer put magic, version, stamp, and checksum in the header.
    offset = 16
    while offset + 8 <= len(data):
        tag, length = struct.unpack_from("<II", data, offset)
        payload = offset + 8
        record_end = payload + length
        if record_end > len(data):
            raise ValueError(f"truncated gcda record at byte {offset}: {path}")
        if tag == GCOV_TAG_COUNTER_ARCS and length >= 24 and length % 8 == 0:
            counters = [
                struct.unpack_from("<Q", data, payload + index)[0]
                for index in range(0, length, 8)
            ]
            candidates = [
                index
                for index, value in enumerate(counters)
                if 0 < value < CORRUPT_COUNTER_VALUE
            ]
            if not candidates:
                raise ValueError(f"arc record has no suitable positive counter: {path}")
            selected_index = candidates[0] if counter_index is None else counter_index
            if selected_index < 0 or selected_index >= len(counters):
                raise ValueError(
                    f"counter index {selected_index} is outside 0..{len(counters) - 1}: {path}"
                )
            old_value = counters[selected_index]
            struct.pack_into(
                "<Q",
                data,
                payload + selected_index * 8,
                replacement,
            )
            path.write_bytes(data)
            return payload + selected_index * 8, old_value, replacement
        offset = record_end

    raise ValueError(f"no multi-counter arc record found: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("gcda", type=pathlib.Path)
    parser.add_argument("--counter-index", type=int)
    parser.add_argument("--value", type=int, default=CORRUPT_COUNTER_VALUE)
    args = parser.parse_args()
    if not 0 <= args.value < 1 << 64:
        parser.error("--value must fit an unsigned 64-bit counter")
    try:
        byte_offset, old_value, new_value = mutate(
            args.gcda, args.counter_index, args.value
        )
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"path={args.gcda}")
    print(f"byte_offset={byte_offset}")
    print(f"old_value={old_value}")
    print(f"new_value={new_value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

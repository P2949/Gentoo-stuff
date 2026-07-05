#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
    echo "usage: $0 <command> [args...]" >&2
    exit 2
fi

lib="${JEMALLOC_LIB:-/usr/lib64/libjemalloc.so}"

if [[ ! -e "${lib}" ]]; then
    echo "allocator library not found: ${lib}" >&2
    exit 1
fi

exec env LD_PRELOAD="${lib}" "$@"

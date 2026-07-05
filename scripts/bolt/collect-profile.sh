#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <output-perf.data> <command> [args...]" >&2
    exit 2
fi

out="$1"
shift

mkdir -p "$(dirname "${out}")"

perf record \
    -e cycles:u \
    -j any,u \
    -o "${out}" \
    -- "$@"

#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <name> <path> [path...]" >&2
    exit 2
fi

name="$1"
shift

mkdir -p bench/results
out="bench/results/${name}-size-$(date +%Y%m%d-%H%M%S).txt"

{
    echo "size report: ${name}"
    echo "date: $(date -Is)"
    echo "kernel: $(uname -r)"
    echo

    for path in "$@"; do
        echo "== ${path} =="
        if [[ ! -e "${path}" ]]; then
            echo "missing"
            echo
            continue
        fi

        ls -lh "${path}"
        file "${path}" || true
        if command -v llvm-size >/dev/null 2>&1; then
            llvm-size "${path}" || true
        elif command -v size >/dev/null 2>&1; then
            size "${path}" || true
        fi
        echo
    done
} | tee "${out}"

echo "wrote ${out}"

#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
    echo "usage: $0 <name> <iterations> <command> [args...]" >&2
    exit 2
fi

name="$1"
iterations="$2"
shift 2

mkdir -p bench/results
out="bench/results/${name}-run-$(date +%Y%m%d-%H%M%S).txt"

{
    echo "benchmark: ${name}"
    echo "iterations: ${iterations}"
    echo "date: $(date -Is)"
    echo "command: $*"
    echo "kernel: $(uname -r)"
    echo

    for i in $(seq 1 "${iterations}"); do
        echo "iteration ${i}"
        /usr/bin/time -f 'real=%e user=%U sys=%S maxrss=%M' "$@"
        echo
    done
} 2>&1 | tee "${out}"

echo "wrote ${out}"

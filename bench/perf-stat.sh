#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
    echo "usage: $0 <name> <iterations> <command> [args...]" >&2
    exit 2
fi

name="$1"
iterations="$2"
shift 2

if ! command -v perf >/dev/null 2>&1; then
    echo "perf is required" >&2
    exit 1
fi

mkdir -p bench/results
out="bench/results/${name}-perf-$(date +%Y%m%d-%H%M%S).txt"

{
    echo "perf benchmark: ${name}"
    echo "iterations: ${iterations}"
    echo "date: $(date -Is)"
    echo "command: $*"
    echo "kernel: $(uname -r)"
    echo
} > "${out}"

perf stat -r "${iterations}" "$@" 2>&1 | tee -a "${out}"

echo "wrote ${out}"

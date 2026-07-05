#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
    echo "usage: $0 <name> <iterations> <baseline-command> -- <test-command>" >&2
    exit 2
fi

name="$1"
iterations="$2"
shift 2

baseline=()
testcmd=()
side="baseline"

for arg in "$@"; do
    if [[ "$arg" == "--" ]]; then
        side="test"
        continue
    fi
    if [[ "$side" == "baseline" ]]; then
        baseline+=("$arg")
    else
        testcmd+=("$arg")
    fi
done

if [[ "${#baseline[@]}" -eq 0 || "${#testcmd[@]}" -eq 0 ]]; then
    echo "baseline and test commands are required" >&2
    exit 2
fi

mkdir -p bench/results
out="bench/results/${name}-compare-$(date +%Y%m%d-%H%M%S).txt"

{
    echo "comparison: ${name}"
    echo "iterations: ${iterations}"
    echo "date: $(date -Is)"
    echo "baseline: ${baseline[*]}"
    echo "test: ${testcmd[*]}"
    echo "kernel: $(uname -r)"
    echo

    for i in $(seq 1 "${iterations}"); do
        echo "iteration ${i} baseline"
        /usr/bin/time -f 'real=%e user=%U sys=%S maxrss=%M' "${baseline[@]}"
        echo "iteration ${i} test"
        /usr/bin/time -f 'real=%e user=%U sys=%S maxrss=%M' "${testcmd[@]}"
        echo
    done
} 2>&1 | tee "${out}"

echo "wrote ${out}"

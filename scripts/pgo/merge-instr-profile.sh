#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <raw-profile-dir> <output.profdata>" >&2
    exit 2
fi

raw_dir="$1"
output="$2"

mapfile -t profiles < <(find "${raw_dir}" -type f \( -name '*.profraw' -o -name 'default_*.profraw' \))

if [[ "${#profiles[@]}" -eq 0 ]]; then
    echo "no raw profiles found in ${raw_dir}" >&2
    exit 1
fi

mkdir -p "$(dirname "${output}")"

llvm-profdata merge -output="${output}" "${profiles[@]}"

echo "wrote ${output}"

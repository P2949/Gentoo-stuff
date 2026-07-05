#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <category/package> <perf.data>" >&2
    exit 2
fi

pkg="$1"
perf_data="$2"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
safe_pkg="${pkg//\//_}"
out_root="/opt/bolt-test/${safe_pkg}"

mkdir -p "${out_root}"

while read -r bin; do
    rel="${bin#/}"
    out="${out_root}/${rel}"
    mkdir -p "$(dirname "${out}")"

    echo "BOLT ${bin} -> ${out}"
    "${script_dir}/optimize-binary.sh" "${bin}" "${perf_data}" "${out}" || {
        echo "BOLT failed for ${bin}; continuing" >&2
    }
done < <("${script_dir}/list-package-binaries.sh" "${pkg}")

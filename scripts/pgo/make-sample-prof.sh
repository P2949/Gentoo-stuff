#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 <category> <package> <binary> <perf.data>" >&2
    exit 2
fi

category="$1"
package="$2"
binary="$3"
perf_data="$4"

profile_dir="/var/tmp/pgo-profiles/${category}/${package}"
mkdir -p "${profile_dir}"

llvm-profgen \
    --binary="${binary}" \
    --perfdata="${perf_data}" \
    --output="${profile_dir}/merged.profdata"

echo "wrote ${profile_dir}/merged.profdata"

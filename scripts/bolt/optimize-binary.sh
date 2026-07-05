#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 <binary> <perf.data> <output-binary>" >&2
    exit 2
fi

binary="$1"
perf_data="$2"
output="$3"

for tool in perf2bolt llvm-bolt; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "missing required tool: $tool" >&2
        exit 1
    }
done

test -x "${binary}"
test -f "${perf_data}"

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

fdata="${workdir}/profile.fdata"

perf2bolt "${binary}" \
    -p "${perf_data}" \
    -o "${fdata}"

mkdir -p "$(dirname "${output}")"

llvm-bolt "${binary}" \
    -o "${output}" \
    -data="${fdata}" \
    -reorder-blocks=ext-tsp \
    -reorder-functions=hfsort+ \
    -split-functions \
    -split-all-cold \
    -dyno-stats

chmod --reference="${binary}" "${output}"

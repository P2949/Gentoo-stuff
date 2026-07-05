#!/usr/bin/env bash
set -euo pipefail

root="${1:-/var/tmp/pgo-profiles}"

if [[ ! -d "${root}" ]]; then
    echo "no profile root: ${root}" >&2
    exit 0
fi

find "${root}" -path '*/merged.profdata' -type f | sort

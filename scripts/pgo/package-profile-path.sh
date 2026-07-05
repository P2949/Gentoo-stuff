#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <category> <package>" >&2
    exit 2
fi

category="$1"
package="$2"

echo "/var/tmp/pgo-profiles/${category}/${package}"

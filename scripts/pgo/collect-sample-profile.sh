#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' \
    'ERROR: the unbounded, identity-free sample collector is permanently disabled.' \
    'Use the generation workload/profile collector and exact sample-convert transaction.' \
    >&2
exit 1

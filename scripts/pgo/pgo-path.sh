#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' \
    'ERROR: the legacy PGO path alias is permanently disabled.' \
    'Use scripts/optimization/pgo/profile-identity.py profile-path.' \
    >&2
exit 1

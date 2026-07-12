#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' \
    'ERROR: the legacy package-name-only profile path helper is permanently disabled.' \
    'Use scripts/optimization/pgo/profile-identity.py profile-path with an exact family, compiler, ABI, CPV, and fingerprint.' \
    >&2
exit 1

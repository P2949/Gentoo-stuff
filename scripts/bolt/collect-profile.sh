#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' \
    'ERROR: this legacy BOLT prototype entry point is permanently disabled.' \
    'Use the exact Phase 2 pre-strip capture, registered-output, and ${ED} deployment lane.' \
    >&2
exit 1

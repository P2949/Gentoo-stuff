#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC2016 # The variable spelling is intentionally literal.
printf '%s\n' \
    'ERROR: this legacy BOLT prototype entry point is permanently disabled.' \
    'Use the exact Phase 2 pre-strip capture, registered-output, and ${ED} deployment lane.' \
    >&2
exit 1

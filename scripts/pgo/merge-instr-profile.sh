#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' \
    'ERROR: the unkeyed instrumentation-profile merger is permanently disabled.' \
    'Use the generation-, compiler-, ABI-, and fingerprint-aware Phase 2 profile validator/merger.' \
    >&2
exit 1

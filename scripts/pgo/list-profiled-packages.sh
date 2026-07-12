#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' \
    'ERROR: the legacy filename-based profile listing helper is permanently disabled.' \
    'Profile coverage must be read from exact validated package state, never inferred from a filename.' \
    >&2
exit 1

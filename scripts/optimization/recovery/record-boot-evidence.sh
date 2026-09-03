#!/usr/bin/env bash

set -Eeuo pipefail

printf '%s\n' \
    'ERROR: record-boot-evidence.sh is permanently retired.' \
    'Boot-entry, EFI-variable, kernel, and initramfs operations are outside project and LLM authority.' \
    'The project records boot-entry-independent runtime and reboot evidence only.' >&2
exit 2

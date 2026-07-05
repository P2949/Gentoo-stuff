#!/usr/bin/env bash
set -euo pipefail

rm -f "${HOME}/.config/environment.d/99-global-allocator.conf"

echo "disabled user-session allocator preload"
echo "log out and back in to clear it from new sessions"

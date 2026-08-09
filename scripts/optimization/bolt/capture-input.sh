#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT
exec /usr/bin/python3 -I -B "${SCRIPT_DIR}/artifact_tool.py" capture "$@"

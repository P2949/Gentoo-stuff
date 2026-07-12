#!/usr/bin/env bash

set -euo pipefail

cat >&2 <<'EOF'
ERROR: this legacy sample-profile producer is permanently disabled.

It used the weak CATEGORY/PN identity and wrote LLVM sample data to the
ambiguous instrumentation-profile name merged.profdata. Use the exact,
transactional interface instead:

  scripts/optimization/pgo/profile-identity.py sample-convert --help

The replacement requires an exact CPV/fingerprint/ABI/compiler identity,
validates the binary build ID and .text hash, and publishes only sample.prof.
EOF
exit 1

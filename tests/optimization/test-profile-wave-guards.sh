#!/usr/bin/env bash
set -euo pipefail

root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
framework="$root/framework"
mkdir -p "$framework/generated-policy"
printf 'empty-v1\n' >"$framework/generated-policy/.identity"
touch "$framework/.candidate-inventory"
cat >"$framework/install.manifest" <<'EOF'
candidate_inventory_sha256=none
frozen_inventory_sha256=none
EOF
ln -s "$framework" "$root/current"

cat >"$root/wave.json" <<'EOF'
{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","packages":[]}
EOF
cat >"$root/readiness.json" <<'EOF'
{"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","source_wave":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","ready_count":0,"invalid_inputs":[]}
EOF

if python3 scripts/optimization/pgo/run-profile-wave.py \
  --wave "$root/wave.json" --readiness "$root/readiness.json" \
  --framework-generation "$framework" --framework-current "$root/current" \
  >"$root/output" 2>&1; then
  echo 'FAIL: empty framework generation was accepted' >&2
  exit 1
fi
grep -q 'empty or non-authoritative policy' "$root/output"
echo 'PASS: empty framework generation fails closed'

if ! grep -Fq 'os.environ["LLVM_PROFILE_FILE"] = "/dev/null"' \
  scripts/optimization/pgo/run-profile-wave.py; then
  echo 'FAIL: profile-wave helpers may inherit LLVM default.profraw output' >&2
  exit 1
fi
echo 'PASS: profile-wave administrative helpers discard LLVM profile output'

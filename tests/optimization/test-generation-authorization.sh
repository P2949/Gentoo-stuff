#!/usr/bin/env bash
set -euo pipefail
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/run" "$ROOT/framework/generated-policy"
: > "$ROOT/run/framework-install.lock"
: > "$ROOT/run/project.lock"
: > "$ROOT/run/generation.lock"
printf 'frozen_inventory_sha256=%064d\n' 0 | tr '0' 'a' > "$ROOT/framework/install.manifest"
{
    printf 'framework_aggregate_sha256=%064d\n' 0 | tr '0' 'b'
    printf 'source_aggregate_sha256=%064d\n' 0 | tr '0' 'c'
    printf 'generated_policy=policy-test\n'
} >> "$ROOT/framework/install.manifest"
printf 'policy-test\n' > "$ROOT/framework/generated-policy/.identity"
ln -s "$ROOT/framework" "$ROOT/framework-current"
GEN=framework
INV=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
python3 scripts/optimization/pgo/generation-authorization.py activate --root "$ROOT/run" --framework-generation "$ROOT/framework" --framework-current "$ROOT/framework-current" --generation-id "$GEN" --inventory-id inventory-test --inventory-sha256 "$INV" --receipt "$ROOT/receipt.json"
test ! -s "$ROOT/run/framework-install.lock"
cmp "$ROOT/run/project.lock" "$ROOT/run/generation.lock"
python3 scripts/optimization/pgo/generation-authorization.py verify --root "$ROOT/run" --generation-id "$GEN" --inventory-id inventory-test --inventory-sha256 "$INV"
python3 scripts/optimization/pgo/generation-authorization.py deactivate --root "$ROOT/run" --receipt "$ROOT/deactivate-receipt.json"
test ! -s "$ROOT/run/project.lock" && test ! -s "$ROOT/run/generation.lock"
echo 'PASS: Phase-3 generation authority activation, verification, and deactivation'

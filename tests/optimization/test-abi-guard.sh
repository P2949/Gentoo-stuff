#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
guard=${ROOT_DIR}/scripts/optimization/verify/abi-guard.py
work=$(mktemp -d /tmp/gentoo-abi-guard.XXXXXX)
trap 'rm -rf -- "${work}"' EXIT
mkdir -p "${work}/root/usr/lib" "${work}/ed/usr/lib"
cat >"${work}/old.c" <<'EOF'
#define F(n) int abi_##n(void) { return 1; }
F(01) F(02) F(03) F(04) F(05) F(06) F(07) F(08)
F(09) F(10) F(11) F(12) F(13) F(14) F(15) F(16)
F(17) F(18) F(19) F(20) F(21) F(22) F(23) F(24)
EOF
cat >"${work}/new.c" <<'EOF'
int abi_01(void) { return 1; }
EOF
cc -shared -fPIC "${work}/old.c" -Wl,-soname,libcanary.so.1 -o "${work}/root/usr/lib/libcanary.so.1"
cc -shared -fPIC "${work}/new.c" -Wl,-soname,libcanary.so.1 -o "${work}/ed/usr/lib/libcanary.so.1"
if ED="${work}/ed" ROOT="${work}/root" python3 "${guard}"; then
    echo 'ABI guard accepted catastrophic export loss' >&2
    exit 1
fi
echo 'PASS: ABI guard rejects catastrophic exported-symbol loss'

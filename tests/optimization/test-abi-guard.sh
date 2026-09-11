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

# The catastrophic-loss candidate above is intentionally invalid.  Remove it
# before beginning independent positive cases so whole-ED validation does not
# carry the expected failure into later assertions.
rm -f -- "${work}/ed/usr/lib/libcanary.so.1"

printf 'INPUT(libcanary.so.1)\n' >"${work}/root/usr/lib/libscript.so.1"
printf 'INPUT(libcanary.so.1)\n' >"${work}/ed/usr/lib/libscript.so.1"
ED="${work}/ed" ROOT="${work}/root" python3 "${guard}"
if ED="${work}/ed" ROOT="${work}/root" python3 "${guard}" >/dev/null 2>&1; then :; fi
if env -u ED ROOT="${work}/root" python3 "${guard}" >/dev/null 2>&1; then
    echo 'ABI guard accepted missing ED context' >&2
    exit 1
fi
echo 'PASS: ABI guard skips linker scripts and rejects missing context'

cp "${work}/root/usr/lib/libcanary.so.1" "${work}/root/usr/lib/libreplace.so.1"
printf 'not an ELF DSO\n' >"${work}/ed/usr/lib/libreplace.so.1"
if ED="${work}/ed" ROOT="${work}/root" python3 "${guard}" >/dev/null 2>&1; then
    echo 'ABI guard accepted ELF to non-ELF replacement' >&2
    exit 1
fi
echo 'PASS: ABI guard rejects ELF to non-ELF replacement'
rm -f -- "${work}/ed/usr/lib/libreplace.so.1"

# Versioned SONAME symlinks must compare the resolved old/new DSOs rather than
# silently skipping the link entry itself.
cc -shared -fPIC "${work}/old.c" -Wl,-soname,libsymlink.so.1 \
    -o "${work}/root/usr/lib/libsymlink.so.1.0"
cc -shared -fPIC "${work}/new.c" -Wl,-soname,libsymlink.so.1 \
    -o "${work}/ed/usr/lib/libsymlink.so.1.1"
ln -s libsymlink.so.1.0 "${work}/root/usr/lib/libsymlink.so.1"
ln -s libsymlink.so.1.1 "${work}/ed/usr/lib/libsymlink.so.1"
if ED="${work}/ed" ROOT="${work}/root" python3 "${guard}" >/dev/null 2>&1; then
    echo 'ABI guard accepted versioned symlink export loss' >&2
    exit 1
fi
echo 'PASS: ABI guard rejects versioned symlink export loss'

cp "${work}/root/usr/lib/libsymlink.so.1.0" "${work}/ed/usr/lib/libsymlink.so.1.1"
ED="${work}/ed" ROOT="${work}/root" python3 "${guard}"
echo 'PASS: ABI guard accepts versioned symlink with retained exports'

# Absolute SONAME links are interpreted within ROOT/ED, never against the host
# filesystem, and therefore exercise the tree-rooted absolute-target path.
rm -f --     "${work}/root/usr/lib/libsymlink.so.1"     "${work}/ed/usr/lib/libsymlink.so.1"
ln -s /usr/lib/libsymlink.so.1.0 "${work}/root/usr/lib/libsymlink.so.1"
ln -s /usr/lib/libsymlink.so.1.1 "${work}/ed/usr/lib/libsymlink.so.1"
ED="${work}/ed" ROOT="${work}/root" python3 "${guard}"
echo 'PASS: ABI guard resolves absolute versioned symlinks inside each tree'

# A regular installed DSO transitioning to a staged symlink must still compare
# the established ABI against the symlink target rather than being skipped.
cp "${work}/root/usr/lib/libsymlink.so.1.0"     "${work}/root/usr/lib/libregular-to-link.so.1"
cc -shared -fPIC "${work}/new.c" -Wl,-soname,libregular-to-link.so.1     -o "${work}/ed/usr/lib/libregular-to-link.so.1.1"
ln -s libregular-to-link.so.1.1     "${work}/ed/usr/lib/libregular-to-link.so.1"

if ED="${work}/ed" ROOT="${work}/root" python3 "${guard}" >/dev/null 2>&1; then
    echo 'ABI guard accepted regular-DSO to symlink export loss' >&2
    exit 1
fi
echo 'PASS: ABI guard rejects regular-DSO to symlink export loss'

cp "${work}/root/usr/lib/libregular-to-link.so.1"     "${work}/ed/usr/lib/libregular-to-link.so.1.1"
ED="${work}/ed" ROOT="${work}/root" python3 "${guard}"
echo 'PASS: ABI guard accepts regular-DSO to symlink with retained exports'

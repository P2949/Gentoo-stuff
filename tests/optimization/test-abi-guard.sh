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
# Keep the established provider present for the independent positive cases;
# absence of this provider is itself a rejection under the installed-side
# SONAME policy exercised below.
cp "${work}/root/usr/lib/libcanary.so.1" "${work}/ed/usr/lib/libcanary.so.1"

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
if ! output=$(ED="${work}/ed" ROOT="${work}/root" python3 "${guard}" 2>&1); then
    echo 'ABI guard rejected retained versioned symlink exports' >&2
    exit 1
fi
if printf '%s\n' "$output" | grep -q 'missing=.*\bD\b'; then
    echo 'ABI guard misparsed an ELF type marker as symbol D' >&2
    exit 1
fi
echo 'PASS: ABI guard accepts versioned symlink with retained exports and filters type markers'

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

# A SONAME transition without a retained provider for the established ABI is
# rejected even when the replacement happens at a different relative path.
cc -shared -fPIC "${work}/old.c" -Wl,-soname,libtransition.so.1 \
    -o "${work}/root/usr/lib/libtransition.so.1"
cc -shared -fPIC "${work}/old.c" -Wl,-soname,libtransition.so.2 \
    -o "${work}/ed/usr/lib/libtransition.so.2"
if ED="${work}/ed" ROOT="${work}/root" python3 "${guard}" >/dev/null 2>&1; then
    echo 'ABI guard accepted disappearance of established SONAME provider' >&2
    exit 1
fi
echo 'PASS: ABI guard rejects SONAME provider disappearance'

# A genuine compatibility provider at the new path retains the old SONAME;
# the new SONAME may coexist without weakening the established ABI check.
cc -shared -fPIC "${work}/old.c" -Wl,-soname,libtransition.so.1 \
    -o "${work}/ed/usr/lib/libtransition.so.1"
ED="${work}/ed" ROOT="${work}/root" python3 "${guard}"
echo 'PASS: ABI guard accepts SONAME transition with retained compatibility provider'

# A coordinated ABI transition may authorize an exact, evidence-bound symbol
# delta for one target CPV; an unrelated target or undeclared symbol loss must
# remain rejected.
rm -rf -- "${work}/root" "${work}/ed"
mkdir -p "${work}/root/usr/lib" "${work}/ed/usr/lib"
rm -f -- "${work}/root/usr/lib/libauthorized.so.1" "${work}/ed/usr/lib/libauthorized.so.1"
cc -shared -fPIC "${work}/old.c" -Wl,-soname,libauthorized.so.1 \
    -o "${work}/root/usr/lib/libauthorized.so.1"
cc -shared -fPIC "${work}/new.c" -Wl,-soname,libauthorized.so.1 \
    -o "${work}/ed/usr/lib/libauthorized.so.1"
cat >"${work}/abi-transition.json" <<'EOF'
{"schema":"abi-transition-v1","state":"active","old_provider_cpv":"dev/authorized-1","target_cpv":"dev/authorized-2","old_sonames":["libauthorized.so.1"],"new_sonames":["libauthorized.so.1"],"artifact_paths":["usr/lib/libauthorized.so.1"],"allowed_symbol_removals":["abi_02","abi_03","abi_04","abi_05","abi_06","abi_07","abi_08","abi_09","abi_10","abi_11","abi_12","abi_13","abi_14","abi_15","abi_16","abi_17","abi_18","abi_19","abi_20","abi_21","abi_22","abi_23","abi_24"],"transition_reason":"fixture coordinated provider migration"}
EOF
authority_sha=$(sha256sum "${work}/abi-transition.json" | awk '{print $1}')
if ! CATEGORY=dev PF=authorized-2 GENTOO_OPT_ABI_TRANSITION_AUTHORITY="${work}/abi-transition.json" \
    GENTOO_OPT_ABI_TRANSITION_AUTHORITY_SHA256="${authority_sha}" \
    ED="${work}/ed" ROOT="${work}/root" python3 "${guard}"; then
    echo 'ABI guard rejected declared coordinated transition' >&2
    exit 1
fi
if CATEGORY=dev PF=other-2 GENTOO_OPT_ABI_TRANSITION_AUTHORITY="${work}/abi-transition.json" \
    GENTOO_OPT_ABI_TRANSITION_AUTHORITY_SHA256="${authority_sha}" \
    ED="${work}/ed" ROOT="${work}/root" python3 "${guard}" >/dev/null 2>&1; then
    echo 'ABI guard accepted transition authority for wrong target CPV' >&2
    exit 1
fi
echo 'PASS: ABI guard requires exact transaction-scoped transition authority'

rm -f -- "${work}/root/usr/lib/libauthorized.so.1" "${work}/ed/usr/lib/libauthorized.so.1"

cc -shared -fPIC "${work}/old.c" -Wl,-soname,libsoname-old.so.1 \
    -o "${work}/root/usr/lib/libsoname-old.so.1"
cc -shared -fPIC "${work}/old.c" -Wl,-soname,libsoname-old.so.2 \
    -o "${work}/ed/usr/lib/libsoname-old.so.2"
cat >"${work}/soname-transition.json" <<'EOF'
{"schema":"abi-transition-v1","state":"active","old_provider_cpv":"dev/soname-1","target_cpv":"dev/soname-2","old_sonames":["libsoname-old.so.1"],"new_sonames":["libsoname-old.so.2"],"artifact_paths":["usr/lib/libsoname-old.so.2"],"allowed_soname_removals":["libsoname-old.so.1"],"allowed_symbol_removals":[],"transition_reason":"fixture SONAME migration"}
EOF
soname_authority_sha=$(sha256sum "${work}/soname-transition.json" | awk '{print $1}')
if ! CATEGORY=dev PF=soname-2 GENTOO_OPT_ABI_TRANSITION_AUTHORITY="${work}/soname-transition.json" \
    GENTOO_OPT_ABI_TRANSITION_AUTHORITY_SHA256="${soname_authority_sha}" \
    ED="${work}/ed" ROOT="${work}/root" python3 "${guard}"; then
    echo 'ABI guard rejected declared SONAME transition' >&2
    exit 1
fi
echo 'PASS: ABI guard permits only declared SONAME transition'
rm -f -- "${work}/root/usr/lib/libsoname-old.so.1" "${work}/ed/usr/lib/libsoname-old.so.2"

# The LLVM runtime helper may be versioned by the provider SONAME.  Its ELF
# spelling is __llvm_write_custom_profile@@<version>; the ABI guard must
# normalize the version suffix before applying the instrumentation exemption.
cat >"${work}/profile-helper.c" <<'EOF'
int __llvm_write_custom_profile(void) { return 0; }
int retained_profile_api(void) { return 0; }
EOF
cat >"${work}/profile-helper.map" <<'EOF'
PROFILE_1 { global: __llvm_write_custom_profile; };
EOF
cc -shared -fPIC "${work}/profile-helper.c" \
    -Wl,--version-script="${work}/profile-helper.map" \
    -Wl,-soname,libprofile-helper.so.1 -o "${work}/root/usr/lib/libprofile-helper.so.1"
cat >"${work}/profile-clean.c" <<'EOF'
int retained_profile_api(void) { return 0; }
EOF
cc -shared -fPIC "${work}/profile-clean.c" \
    -Wl,--version-script="${work}/profile-helper.map" \
    -Wl,-soname,libprofile-helper.so.1 -o "${work}/ed/usr/lib/libprofile-helper.so.1"
ED="${work}/ed" ROOT="${work}/root" python3 "${guard}"
echo 'PASS: ABI guard ignores versioned LLVM profile runtime helper'

# A package with no staged DSO candidates must not trigger a ROOT-wide walk.
rm -rf -- "${work}/ed" "${work}/root"
mkdir -p "${work}/ed/opt/dotnet-nugets" "${work}/root/unrelated/deep"
for n in $(seq 1 2000); do printf x >"${work}/root/unrelated/deep/file-${n}"; done
ED="${work}/ed" ROOT="${work}/root" /usr/bin/timeout 5 python3 "${guard}"
echo 'PASS: empty staged DSO set returns without ROOT traversal'

# Provider discovery is immediate-directory scoped; nested unrelated files do
# not become candidate providers merely because the parent is a library dir.
mkdir -p "${work}/ed/usr/lib" "${work}/root/usr/lib/unrelated/deep"
cc -shared -fPIC "${work}/old.c" -Wl,-soname,libscoped.so.1 -o "${work}/root/usr/lib/libscoped.so.1"
cp "${work}/root/usr/lib/libscoped.so.1" "${work}/ed/usr/lib/libscoped.so.1"
cp "${work}/root/usr/lib/libscoped.so.1" "${work}/root/usr/lib/unrelated/deep/libscoped.so.1"
for n in $(seq 1 2000); do printf x >"${work}/root/usr/lib/unrelated/deep/file-${n}"; done
ED="${work}/ed" ROOT="${work}/root" /usr/bin/timeout 5 python3 "${guard}"
echo 'PASS: provider discovery does not recurse below candidate parent'

# An unrelated provider sharing the candidate family must not be attributed to
# the package currently merging when Portage VDB ownership proves otherwise.
rm -rf -- "${work}/root" "${work}/ed" "${work}/vdb"
mkdir -p "${work}/root/usr/lib" "${work}/ed/usr/lib" "${work}/vdb/app/test-1"
cp "${work}/old.c" "${work}/foreign.c"
cc -shared -fPIC "${work}/old.c" -Wl,-soname,libforeign.so.0 -o "${work}/root/usr/lib/libforeign.so.0"
cc -shared -fPIC "${work}/new.c" -Wl,-soname,libforeign.so.1 -o "${work}/ed/usr/lib/libforeign.so.1"
printf 'obj /usr/lib/libcandidate.so.1 deadbeef 1\n' >"${work}/vdb/app/test-1/CONTENTS"
if ! CATEGORY=app PF=test-1 GENTOO_OPT_VDB_ROOT="${work}/vdb" ED="${work}/ed" ROOT="${work}/root" python3 "${guard}"; then
    echo 'ABI guard attributed unrelated provider to current package' >&2
    exit 1
fi
echo 'PASS: provider disappearance is scoped to current package ownership'

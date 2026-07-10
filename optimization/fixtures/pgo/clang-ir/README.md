# Clang IR-PGO capability fixture

This fixture validates the instrumentation-profile path independently from
sample PGO.  It builds a two-translation-unit executable and a
two-translation-unit DSO with the active Clang/LLD pair.  Both generation and
use builds retain the system's ThinLTO shape:

```text
-flto=thin -ffat-lto-objects -funified-lto
-fforce-emit-vtables -fwhole-program-vtables -fsplit-lto-unit
-fuse-ld=lld -Wl,--lto-O3 -Wl,--lto-CGO3
```

The generation build embeds an absolute `-fprofile-generate=<raw-directory>`.
Training also sets an absolute `LLVM_PROFILE_FILE` pattern containing both
`%m` and `%p`, so independent modules and concurrent processes cannot silently
overwrite one another.  The runner uses `llvm-profdata` next to the selected
Clang binary and passes the merged profile to every use compilation and link
through an absolute `-fprofile-use=<file>`.

The negative test recompiles `workload.cpp` with a deliberately changed IR
counter layout.  In this unified-LTO configuration Clang reports the stale
function hash through the backend-plugin diagnostic, so the runner uses
`-Werror=backend-plugin`.  The compilation must fail while retaining the
explicit `hash mismatch` diagnostic.  No mismatch or missing-profile warning
is suppressed anywhere in the fixture.

Run:

```sh
./run.sh
```

Results remain at `/tmp/gentoo-optimization-phase-1-clang-ir-pgo`.  A different
absolute result directory can be supplied as the first argument.

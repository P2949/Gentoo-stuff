# Retired global PGO prototype

The old compiler-agnostic global consumer and its package-name-only storage
scheme are permanently disabled. Every helper under `scripts/pgo/` exits with
an error; none may be used to collect, merge, locate, list, or consume a
profile.

The exact replacement is documented in
[`scripts/optimization/pgo/README.md`](../scripts/optimization/pgo/README.md). It separates
Clang IR, Clang sample, GCC, Rust, and Go profile families and binds profiles
to the exact CPV, slot, repository, ebuild, compiler, ABI, flags, environment
stack, and build fingerprint. `portage/bashrc` activates only an explicit
backend mode with a validated manifest and fails the Portage build on any
missing or mismatched identity.

No generated optimization package assignment or live system optimization
generation exists yet.

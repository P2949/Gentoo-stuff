# Retired global BOLT prototype

The retired prototype made packages BOLT-ready globally, then used standalone
helpers to profile installed executables and write outputs outside Portage. It
did not bind the exact package fingerprint, captured build ID, `.text` identity,
profile evidence, registered output, or `${ED}` deployment transaction required
by Phase 2.

The prototype is permanently disabled. These historical entry points are
fail-closed stubs and must remain unusable:

- `scripts/bolt/collect-profile.sh`
- `scripts/bolt/list-package-binaries.sh`
- `scripts/bolt/optimize-binary.sh`
- `scripts/bolt/bolt-package-binaries.sh`

The only supported implementation is the exact pre-strip capture, registered
output, and fail-closed `${ED}` deployment lane documented in the
[current BOLT transaction README](../scripts/optimization/bolt/README.md).
It never modifies an installed `/usr` executable directly.

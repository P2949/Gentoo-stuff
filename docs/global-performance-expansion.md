# Global Performance Expansion

This repo applies performance experiments globally first.

The default is expected to break packages. Breakage is not failure of the
repo. Breakage is data.

## Policy

1. Add optimization globally.
2. Build as many packages as possible.
3. Demote only packages that prove they need it.
4. Demote the narrowest failing axis.
5. Re-test demoted packages after toolchain updates.

## Current Global Expansion Axes

- BOLT readiness metadata
- sample-PGO profile mapping metadata
- section splitting
- linker section GC
- linker safe ICF
- hidden visibility
- optimization remarks
- Rust panic=abort
- Rust linker layout flags
- PGO use-if-available

## Never Do This Casually

Do not remove a global axis because a single package failed.

Instead, add a package.env demotion.

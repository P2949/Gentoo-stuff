# Rust instrumentation PGO fixture

This offline Cargo project validates the active Rust compiler's LLVM
instrumentation-profile lane. The runner uses an absolute raw-profile
directory, explicitly supplies the host target triple so Cargo build scripts
remain uninstrumented, trains six processes, selects an `llvm-profdata` with
the same LLVM major as `rustc`, and rebuilds with an absolute indexed profile.

It also proves that the LLVM missing-function diagnostic is enabled with a
deliberately changed feature build. The active `rustc` only warns for malformed
indexed profile data and still exits successfully, so the runner requires the
matching `llvm-profdata` validator to reject that input before treating it as
safe. Compiler exit status alone is deliberately not accepted.
All commands, compiler versions, raw and merged profile metadata, hashes, and
functional results are retained in a caller-supplied new directory below
`/tmp` or `/var/tmp/gentoo-optimization`:

```sh
./run.sh /tmp/phase-1-rust-pgo-unique-id
```

# Clang sample-PGO capability fixture

This fixture keeps LLVM sample profiling separate from IR instrumentation. It
builds a ThinLTO PIE with line-table, unique-name, pseudo-probe, build-ID, and
relocation metadata; captures user-space branch stacks with `perf`; and passes
the exact binary and capture to the production
`profile-identity.py sample-convert` transaction. The transaction derives the GNU build ID and
`.text` SHA-256, runs the exact canonical LLVM 22 tools in an allowlisted
environment, validates the real `sample.prof` with
`llvm-profdata show --sample`, and publishes producer metadata plus a mode-0444 conversion log
containing the exact `llvm-profgen` stdout and stderr.

The fixture then calls the production `validate-profile.py produce` and
`verify` commands. This creates and reopens the sole dispatcher manifest and
sidecar transaction before any consumer compile. The compiled workload
consumes the profile only through `-fprofile-sample-use` (plus the reviewed
sample-specific `-fsample-profile-use-profi` correction mode); command evidence
must contain no IR-instrumentation consumer or generator flag.

The negative test passes the sample profile to the IR instrumentation consumer
and requires a visible format rejection. Conversion warnings, ordinary output,
the exact producer argv, the producer executable identity, and their hashes are
retained in `llvm-profgen-conversion-log.json`; the metadata binds that log by
canonical path, SHA-256, inode/stat observation, and exact stream hashes.
Nothing is reconstructed from the shell transcript.

Run with a new evidence directory below `/tmp` or the dedicated optimization
tree in `/var/tmp`:

```sh
./run.sh /tmp/phase-1-clang-sample-unique-id
```

The output directory must be new, canonical, and contain neither whitespace
nor `=`. Set `CLANG_SAMPLE_ITERATIONS` to a smaller positive value only for a
developer smoke run; authoritative capability evidence uses the reviewed
workload count. Existing profiles, metadata, conversion logs, manifests, and
sidecars are never reused.

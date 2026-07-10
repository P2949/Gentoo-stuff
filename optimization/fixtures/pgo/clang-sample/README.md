# Clang sample-PGO capability fixture

This fixture keeps LLVM sample profiling separate from IR instrumentation. It
builds a ThinLTO PIE with line-table, unique-name, pseudo-probe, build-ID, and
relocation metadata; captures user-space branch stacks with `perf`; converts
the exact binary and capture with `llvm-profgen`; and consumes the resulting
`sample.prof` only through `-fprofile-sample-use` and
`-fsample-profile-use-profi`.

The negative test passes the sample profile to the IR instrumentation consumer
and requires a visible format rejection. Conversion warnings are deliberately
retained in the evidence and must be evaluated by the later profile-quality
policy; the runner never hides them.

Run with a new evidence directory below `/tmp` or the dedicated optimization
tree in `/var/tmp`:

```sh
./run.sh /tmp/phase-1-clang-sample-unique-id
```

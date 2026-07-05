# Benchmark Harness

Benchmarking does not gate whether a global experiment is tried.
Benchmarking gates whether a package stays global or gets demoted.

Minimum strict benchmark requirements:

- 5 iterations for quick CLI tools
- 10 iterations for noisy frame-time tests
- A/B/A/B order, not all baseline then all test
- record package version
- record active env profiles
- record CPU governor
- record kernel version
- record GPU driver/Mesa version for gaming tests
- record whether system was warm or cold

Use `bench/run-command-benchmark.sh` for simple repeated timing,
`bench/perf-stat.sh` for hardware counter collection,
`bench/size-report.sh` for binary/object size snapshots, and
`bench/compare-two-commands.sh` for direct baseline/test comparisons.

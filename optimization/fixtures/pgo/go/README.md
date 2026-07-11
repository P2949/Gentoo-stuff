# Go CPU-profile PGO fixture

This dependency-free module builds a baseline command with `-pgo=off`, creates
and inspects a nonempty runtime CPU pprof profile, and rebuilds from an explicit
absolute `-pgo=<profile>` path. The runner retains `go build -x` compiler
commands and `go version -m` settings to prove that the Go compiler received a
processed PGO profile.

Negative cases reject malformed pprof data and demonstrate why an otherwise
valid profile from an unrelated Go command must be rejected by symbol matching
even when the Go toolchain accepts it as a source-stable profile input.

The runner records the language-native ID from `go tool buildid` separately
from an optional GNU build ID. A GNU note strengthens exact mapping evidence
when present but is not required for Go PGO eligibility; a later BOLT lane may
impose its own exact-input metadata requirements.

Run with a new evidence directory below `/tmp` or
`/var/tmp/gentoo-optimization`:

```sh
./run.sh /tmp/phase-1-go-pgo-unique-id
```

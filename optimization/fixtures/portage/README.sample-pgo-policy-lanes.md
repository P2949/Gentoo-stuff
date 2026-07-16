# Sample-PGO Portage policy lanes

`tests/optimization/test-portage-sample-pgo-integration.sh` runs the same real
Portage mapping build, `perf` training, `llvm-profgen` conversion, immutable
profile publication, generated `package.env` consumer rebuild, runtime check,
and tamper rejection in two explicitly selected policy lanes.

## `isolated-diagnostic`

This preserves the historical fault-localization configuration. It keeps
`userpriv`, disables compiler wrappers, disables Portage sandbox namespaces,
and enables `nostrip`. It is useful when separating an optimization-framework
failure from a sandbox interaction. It is not evidence that the framework is
compatible with the installed system's normal Portage execution policy, and it
is forbidden with `--production-locks`.

## `live`

This obtains the authoritative host policy from:

```bash
env -u FEATURES PORTAGE_CONFIGROOT=/ /usr/bin/portageq envvar FEATURES
```

The resolved value is copied verbatim into the disposable configuration root.
No sandbox feature is disabled. The optimization dispatcher deliberately adds
`-ccache`, `-distcc`, and `-icecream` only to mapping-ready and sample-use
phases so those authoritative artifacts cannot come from a compiler cache.
Receipts are compared as exact last-token-wins feature maps: off/probe phases
must equal the live baseline, while mapping/use may differ only by the final
negative state of those three reviewed wrapper features. Unknown additions,
removals, or polarity drift fail the lane. Mapping/use also require both
`CCACHE_DISABLE=1` and `SCCACHE_DISABLE=1`; ordinary phases require those
controls to be unset or zero. Every successful compile phase records exact
non-wrapper `CC` and `CXX` paths and their resolved targets, sets
`CCACHE_RECACHE=1`, and requires post-command compiler/link completion markers.

Package fingerprints use schema v3. `USE` remains order-insensitive, but
`FEATURES` is canonicalized as an effective last-token-wins state. Therefore
`ccache -ccache` and `-ccache ccache` cannot share a fingerprint. Schema-v2
fingerprint inputs are rejected rather than reinterpreted.

All writable Portage state is redirected below the disposable work root:
`PORTAGE_TMPDIR`, `PORTAGE_LOGDIR`, `PORTAGE_DEPCACHEDIR`, `DISTDIR`, `PKGDIR`,
`CCACHE_DIR`, `CCACHE_TEMPDIR`, and `SCCACHE_DIR`. Elog is constrained to the
disposable log directory. Driver `HOME`, `TMPDIR`, `XDG_CACHE_HOME`,
`XDG_CONFIG_HOME`, and `XDG_STATE_HOME` are disposable too. Both diagnostic and
production ebuild commands start from an explicit `env -i`; the coordinator's
outer production `env -i` remains an independent defense.

Before the full pipeline, the lane proves that:

* `sandbox`, `usersandbox`, `userpriv`, `mount-sandbox`, `pid-sandbox`,
  `ipc-sandbox`, and `network-sandbox` are enabled in the live policy;
* the compile phase runs as the installed `portage` user and group;
* `SANDBOX_ON=1` inside the phase;
* PID, network, IPC, and mount namespace identities differ from the driver;
* a repository write that ordinary Unix permissions explicitly permit for the
  `portage` group through a root:portage `0770` directory is outside the
  phase's recorded `SANDBOX_WRITE` prefixes and is rejected inside the Portage
  sandbox.

Before and after the pipeline, the lane re-resolves and byte-compares a trusted
live-policy identity containing literal-uid-0 ancestry checks, the complete
Portage config tree, `make.conf`, `make.globals`, the full resolved profile
chain, repository configuration/markers, and exact policy-tool hashes. It also
compares namespace/metadata sentinels for `/var/db/pkg`, `/var/cache/edb`,
`/var/log/portage`, and the live distfile/binpkg/cache/log roots. Production
generation and profile writes are checked against an exact file allowlist; the
transaction journal and child identity must remain byte- and inode-identical.
An empty live `CCACHE_TEMPDIR` is resolved to the ccache default below the
effective `CCACHE_DIR`; an empty `SCCACHE_DIR` is resolved from live
`XDG_CACHE_HOME`/`HOME`. Those derived paths and their derivation sources are
recorded and included in the protected before/after sentinels. Fixture cache
paths remain explicit children of the disposable work root.

Those properties are checked again in the mapping, ordinary-consumer, and
sample-use phase receipts. `portage-policy.tsv`,
`sandbox-enforcement.tsv`, the phase receipts, and the expected-failure build
log are included in the fixture's evidence hashes and exhaustive publication
tree.

The top-level runner invokes both lanes when `clang-sample` is explicitly
selected:

```bash
doas tests/run-optimization-tests.sh --capability clang-sample \
  --output-dir /var/tmp/gentoo-optimization/phase2-sample-policy-gate
```

An authoritative `--production-locks` invocation must explicitly select:

```text
--portage-policy live
```

The preflight is build-free, and the non-production live lane is a disposable
integration proof rather than authorization to publish a Phase 2 production
receipt. Only the
coordinator-supervised `--production-locks --portage-policy live` workflow can
produce authoritative production gate state, and literal-root trust must pass
on the Gentoo host rather than skip.

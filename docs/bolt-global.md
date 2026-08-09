# Legacy BOLT Readiness Prototype

> **Not a production deployment path.** These scripts predate the validated
> transaction and ELF-policy gates. They may create disposable test outputs,
> but they must not deploy package-managed files. Phase 2 replaces them with
> exact pre-strip capture and fail-closed `${ED}` deployment hooks.

The retired prototype made packages BOLT-ready globally by adding line-table
debug mapping, relocation metadata, and build ids to the default aggressive
tier. BOLT execution happened after build, so the prototype handled it with
scripts rather than normal compiler flags.

## Workflow

1. Build packages normally with the global BOLT-ready flags.
2. Collect a workload profile:

```bash
scripts/bolt/collect-profile.sh /var/tmp/bolt-profiles/app.perf.data command args
```

3. Optimize one binary into an explicit output path:

```bash
scripts/bolt/optimize-binary.sh /usr/bin/tool /var/tmp/bolt-profiles/app.perf.data /opt/bolt-test/tool
```

4. Or sweep package executables into `/opt/bolt-test/<category_package>`:

```bash
scripts/bolt/bolt-package-binaries.sh app-arch/zstd /var/tmp/bolt-profiles/zstd.perf.data
```

The reviewed layout spelling used by the capability fixture is
`-reorder-blocks=ext-tsp -reorder-functions=cdsort`; `hfsort+` has not passed
that gate. The legacy scripts are retained only for disposable experiments and
must not be used to promote wrappers or replace installed files.

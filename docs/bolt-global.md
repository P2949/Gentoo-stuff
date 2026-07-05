# Global BOLT Readiness

This repository makes packages BOLT-ready globally by adding line-table debug
mapping, relocation metadata, and build ids to the default aggressive tier.
BOLT execution still happens after build, so it is handled by scripts rather
than normal compiler flags.

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

The scripts never overwrite `/usr/bin` or other package-managed paths. Promote
wrappers or local replacements only after validating the optimized copy.

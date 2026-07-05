# Global Allocator Experiment

Allocator testing is runtime behavior, so this repo starts with reversible
user-session scope rather than a system loader preload.

Use `scripts/allocators/enable-user-allocator.sh <allocator>` to write:

```text
~/.config/environment.d/99-global-allocator.conf
```

Supported allocator names are `jemalloc`, `mimalloc`, and `tcmalloc`. Override
library paths with `JEMALLOC_LIB`, `MIMALLOC_LIB`, or `TCMALLOC_LIB`.

Disable the session preload with:

```bash
scripts/allocators/disable-user-allocator.sh
```

For package or app demotion, launch a command without the session preload:

```bash
scripts/allocators/run-with-system-malloc.sh command args
```

For isolated tests, use `run-with-jemalloc.sh`, `run-with-mimalloc.sh`, or
`run-with-tcmalloc.sh`.

These scripts do not edit `/etc/ld.so.preload`.

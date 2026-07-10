# GCC PGO fixture

This fixture validates GCC instrumentation PGO independently of LLVM. It builds
two executable translation units and two translation units in a shared object,
trains the instrumented pair in several processes, and rebuilds the same object
paths with `-fprofile-use`.

Run it with a new absolute output directory:

```sh
./run.sh /tmp/gentoo-optimization-gcc-pgo
```

The runner selects the compiler from the active `gcc-config` selection. It
refuses an existing output directory, never installs anything, and writes all
profiles, compiler traces, mismatch/correction evidence, functional output, and
the final machine-readable result below that output directory.

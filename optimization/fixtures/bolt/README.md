# BOLT executable, PIE, and DSO capability fixture

`run.sh` builds three distinct x86-64 ELF classes with symbols, a GNU build
ID, line information, and emitted relocations:

- a fixed-address `ET_EXEC` executable;
- a position-independent `ET_DYN` executable;
- an `ET_DYN` shared object exercised through a separate PIE driver.

For every class it records two branch-stack `perf.data` files, converts both
with `perf2bolt`, merges their fdata, runs `llvm-bolt`, and verifies the BOLT
note, functionality, ELF identity, dynamic dependencies, exported DSO ABI,
ownership intent, mode, and xattrs. The runner never changes an installed
file and accepts output only below `/tmp` or `/var/tmp/gentoo-optimization`.

Run it after the package-managed LLVM 22 BOLT tools are installed:

```sh
./run.sh /tmp/phase-1-bolt-unique-id
```

`BOLT_FIXTURE_TRAIN_ITERATIONS` may be raised when a host needs more samples;
the default is intended to provide a substantial but bounded capability run.

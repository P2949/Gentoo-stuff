# BOLT executable, PIE, and DSO capability fixture

`run.sh` builds three distinct x86-64 ELF classes with symbols, a GNU build
ID, line information, and emitted relocations:

- a fixed-address `ET_EXEC` executable;
- a position-independent `ET_DYN` executable;
- an `ET_DYN` shared object exercised through a separate PIE driver.

For every class it records two branch-stack `perf.data` files, converts both
with `perf2bolt`, merges their fdata, runs `llvm-bolt`, and verifies the BOLT
note, functionality, ELF and GNU-stack policy, interpreter, SONAME,
RPATH/RUNPATH, flags, dependency tree, symbol versions, exported DSO ABI,
ownership intent, mode, and xattrs. It rejects empty or poor conversion data,
unexplained BOLT warnings, unchanged build identities, and identity drift.
The fixed executable, PIE, DSO, and DSO driver are independently checked for
their exact ELF role. Explicit CET inputs must retain their raw GNU property
note, and GNU-stack and GNU-RELRO policies must remain intact. The only
allowlisted diagnostic is LLVM's exact duplicated-32-entry Intel Skylake LBR
workaround message; its presence or absence is retained in the profile-quality
evidence and every other BOLT warning remains fatal.
The BOLT command uses `-use-gnu-stack`; a normal `strip --strip-unneeded` copy
of every output must retain its note, `.text` identity, structure, metadata,
and functionality, matching the final pre-strip Portage deployment order. The
runner never changes an installed file and accepts output only below `/tmp` or
`/var/tmp/gentoo-optimization`.

Run it after the package-managed LLVM 22 BOLT tools are installed:

```sh
./run.sh /tmp/phase-1-bolt-unique-id
```

`BOLT_FIXTURE_TRAIN_ITERATIONS` may be raised when a host needs more samples;
the default is intended to provide a substantial but bounded capability run.

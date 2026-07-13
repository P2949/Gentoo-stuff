# Pre-strip BOLT capture and deployment hooks

These tools are the fail-closed `${ED}` transaction lane. The production hook
runs as the lexically last Portage `install-qa-check.d` command, after the
package's `post_src_install` has completed and before Portage strips `${ED}`.
It does not redefine or wrap `post_src_install`, and it never writes directly
to installed `/usr`. The package identity is the exact 64-hex fingerprint
produced by the Phase 2 identity tool.

Production invocations run as root, accept only the reviewed cache root
`/var/cache/gentoo-optimization/bolt`, require that root and its lock files to
be root-owned and non-writable by group/world, and reject every symlink path
component. Capture/deployment also require `--ed` to equal active Portage
`${ED}`/`${D}` below `${PORTAGE_BUILDDIR}`. `--test-mode` is an explicit
standalone exception for hermetic fixtures; it still requires caller-owned,
non-group/world-writable roots and all other safety/identity checks.

Capture copies each eligible unstripped ELF once per hardlink group without
changing `${ED}` and publishes `inputs/<fingerprint>/manifest.json` atomically:

```bash
capture-input.sh --ed "${ED}" --cache-root "${GENTOO_OPT_BOLT_CACHE_ROOT}" \
  --fingerprint "${GENTOO_OPT_FINGERPRINT}" \
  --expected-eligible-count "${GENTOO_OPT_BOLT_EXPECTED_ELIGIBLE_COUNT}" \
  --inventory-proof "${GENTOO_OPT_BOLT_INVENTORY_PROOF}"
```

Eligibility requires ELF64, x86-64, `ET_EXEC` or `ET_DYN`, executable code, a
full symbol table, relocation sections targeting executable sections (including
`.rel[a].text.<function>`), a GNU build ID, and a nonempty `.text`. The manifest
also records every automatic readiness failure, hardlink group,
symlink, mode, ownership intent, xattr/capability value, file hash, build ID,
and `.text` hash. A portable full-`${ED}` identity additionally covers every
directory, non-ELF regular-file hardlink group, symlink and ELF classification;
deployment reproduces and compares it before staging any replacement. The
ABI/security identity BOLT cannot change includes ELF data, role, OSABI,
ABI/header version and `e_flags`; interpreter; ordered `DT_NEEDED`; SONAME;
RPATH/RUNPATH; exported dynamic symbols including OBJECT/TLS sizes and version
binding; complete symbol-version mappings; CET; GNU-stack policy; RELRO/NOW;
dynamic flags/TEXTREL; LOAD W^X state; and TLS program headers. Registration,
pre-deploy validation and post-deploy validation enforce these fields exactly.

`--expected-eligible-count` and `--inventory-proof` are mandatory for every
positive or zero set. The strict
`gentoo-optimization-bolt-inventory-proof-v1` document binds generation ID,
inventory ID, exact CPV/frozen entry hash, package fingerprint and count to an exact inventory-evidence
path/hash/size plus every expected candidate's artifact ID, canonical path,
hardlink paths, ELF class/data/type/machine and role. Capture must reproduce
that exact set. The installed strict state reconciler revalidates the complete
frozen-inventory schema and hash, proves the CPV/entry membership and requires
every candidate path to be owned by that CPV. Zero uses the same schema with an
empty candidate list.

Readiness failures are not terminal exclusions. Missing build IDs, relocation
metadata, or symbols remain remediable pending items until the later policy
classifier records a separately reviewed terminal decision.

After an exact captured input has been profiled and optimized, register the
prepared output. Registration rejects an invalid ELF, a malformed/non-GNU
`.note.bolt_info`, a note-only forgery without nonempty executable
`.bolt.org.text`, or a note whose embedded command differs from the reviewed
invocation:

```bash
register-output.sh --cache-root "${GENTOO_OPT_BOLT_CACHE_ROOT}" \
  --fingerprint "${GENTOO_OPT_FINGERPRINT}" --artifact-id ID \
  --input "${GENTOO_OPT_BOLT_CACHE_ROOT}/inputs/${GENTOO_OPT_FINGERPRINT}/objects/ID.elf" \
  --output FILE --command-output-path FILE.partial \
  --llvm-bolt /usr/lib/llvm/22/bin/llvm-bolt \
  --option-policy-revision gentoo-system-wide-bolt-v1-cdsort-20260712 \
  --fdata MERGED.fdata --workload-evidence WORKLOAD.json \
  --profile-evidence PROFILE.json \
  --fdata-quality-evidence FDATA-QUALITY.json --command-record COMMAND.json \
  --bolt-option=-reorder-blocks=ext-tsp \
  --bolt-option=-reorder-functions=cdsort \
  --bolt-option=-split-functions --bolt-option=-split-all-cold \
  --bolt-option=-split-eh --bolt-option=-icf=safe \
  --bolt-option=-update-debug-sections --bolt-option=-dyno-stats
```

The required `--input` must match the captured full-file hash, build ID, and
`.text` hash. The BOLT profiling/optimization transaction must invoke BOLT on
that exact object and pass the same path at registration; a merely similar
installed binary is not accepted as provenance. `--command-output-path`
records the atomic `.partial` path actually passed to BOLT; it may have been
renamed to `--output`, but its recorded size and SHA-256 must equal the
published file.

Workload, profile and fdata evidence use strict JSON proof schemas. Each binds
the generation/inventory/package/artifact and exact input file/build-ID/`.text`
identity. Workloads prove bounded successful functional repetitions. Profile
proofs bind the profiling tool, exact events, LBR status, quality counts and
thresholds, and exact contributors. Fdata proofs bind the merge tool, inputs,
contributors, functions and samples. Production also requires strict sanitized
command records for the workload, perf record/report, perf2bolt and merge-fdata;
their exact tools, argv, inputs, outputs and structured result metrics must
support every claimed count. Production rejects `fixture_only`;
synthetic hook integration requires both `--test-mode` and the bounded hidden
fixture-quality switch.

Every output record binds the canonical llvm-bolt path, SHA-256 and complete
`--version` text; the exact reviewed policy revision and ordered option list;
all fdata, workload and profile evidence paths/hashes/sizes; and a strict
command record. The command record binds exact argv, zero exit status,
timestamps, tool/input/output identities, stdout/stderr, and the same evidence.
Deployment revalidates the live tool and evidence plus the immutable command
record before touching `${ED}`. Production accepts only the root-owned,
non-writable exact `/usr/lib/llvm/22/bin/llvm-bolt`; hermetic fixtures must opt into
`--test-mode` explicitly and still supply every provenance argument.
Every production fdata, workload, profile, command-record, stdout, stderr,
captured-input, and prepared-output path must also live below root-owned,
non-group/world-writable `/var/cache/gentoo-optimization` or
`/var/lib/gentoo-optimization`, with no symlink or untrusted component anywhere
in its ancestry. Arbitrary absolute paths are accepted only in explicit
hermetic test mode.

Deployment requires registered output for every eligible input. It validates
the package fingerprint, full input hash, build ID, `.text` hash, hardlinks,
symlinks, and metadata before any replacement. It stages same-inode hardlink
groups next to their destinations, atomically replaces each directory entry,
retains exact pre-deploy inputs under `diagnostics/`, and verifies the BOLT
note, output hash, metadata, and topology afterward:

```bash
deploy-output.sh --ed "${ED}" --cache-root "${GENTOO_OPT_BOLT_CACHE_ROOT}" \
  --fingerprint "${GENTOO_OPT_FINGERPRINT}" \
  --expected-eligible-count "${GENTOO_OPT_BOLT_EXPECTED_ELIGIBLE_COUNT}" \
  --inventory-proof "${GENTOO_OPT_BOLT_INVENTORY_PROOF}"
```

Deployment receives the same frozen proof and count; capture and output
manifests bind both. A zero-candidate capture is a verified terminal no-output
state, never a false optimized-artifact claim.

Capture never overwrites an existing identity blindly. A later retry always
performs a complete fresh capture at the deterministic per-fingerprint partial
path. It adopts the existing capture only when the fresh manifest, cached
objects, paths, modes, sizes and hashes match byte-for-byte. A mismatch is
published under the private `quarantine/capture-mismatch/` tree and rejected;
the authoritative existing capture is left untouched. Output registration
also refuses duplicate artifact IDs. A genuinely different build requires a
new package fingerprint.

Production capture, registration and deployment first take `LOCK_SH` on the
exact root-owned mode-0600
`/run/gentoo-optimization/framework-install.lock`, then the private
per-fingerprint lock. The installer takes `LOCK_EX` before enumerating those
locks, closing the new-lock publication race. The default bounded wait is 30
seconds. Production pins readelf/objcopy to the reviewed `/usr/bin` entry
points and llvm-bolt to `/usr/lib/llvm/22/bin/llvm-bolt`. Every child receives
only `LC_ALL=C`, `LANG=C` and fixed `PATH`, runs in a separate process group,
and has a 30-second
deadline. Timeout cleanup sends TERM, waits five seconds, then kills the whole
group; unpublished outputs are removed. The tool and grace deadlines are
configurable with `--tool-timeout-seconds` and
`--tool-kill-after-seconds`.

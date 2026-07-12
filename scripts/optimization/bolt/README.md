# Pre-strip BOLT capture and deployment hooks

These tools are the fail-closed `${ED}` transaction lane used by the Portage
`post_src_install` dispatcher. They never write directly to installed `/usr`.
The package identity is the exact 64-hex fingerprint produced by the Phase 2
identity tool.

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
  --fingerprint "${GENTOO_OPT_FINGERPRINT}"
```

Eligibility requires ELF64, x86-64, `ET_EXEC` or `ET_DYN`, executable code, a
full symbol table, relocation sections targeting executable sections (including
`.rel[a].text.<function>`), a GNU build ID, and a nonempty `.text`. The manifest
also records every automatic readiness failure, hardlink group,
symlink, mode, ownership intent, xattr/capability value, file hash, build ID,
and `.text` hash.

Readiness failures are not terminal exclusions. Missing build IDs, relocation
metadata, or symbols remain remediable pending items until the later policy
classifier records a separately reviewed terminal decision.

After an exact captured input has been profiled and optimized, register the
prepared output. Registration rejects an invalid ELF or an output without
`.note.bolt_info`:

```bash
register-output.sh --cache-root "${GENTOO_OPT_BOLT_CACHE_ROOT}" \
  --fingerprint "${GENTOO_OPT_FINGERPRINT}" --artifact-id ID \
  --input "${GENTOO_OPT_BOLT_CACHE_ROOT}/inputs/${GENTOO_OPT_FINGERPRINT}/objects/ID.elf" \
  --output FILE
```

The required `--input` must match the captured full-file hash, build ID, and
`.text` hash. The BOLT profiling/optimization transaction must invoke BOLT on
that exact object and pass the same path at registration; a merely similar
installed binary is not accepted as provenance.

Deployment requires registered output for every eligible input. It validates
the package fingerprint, full input hash, build ID, `.text` hash, hardlinks,
symlinks, and metadata before any replacement. It stages same-inode hardlink
groups next to their destinations, atomically replaces each directory entry,
retains exact pre-deploy inputs under `diagnostics/`, and verifies the BOLT
note, output hash, metadata, and topology afterward:

```bash
deploy-output.sh --ed "${ED}" --cache-root "${GENTOO_OPT_BOLT_CACHE_ROOT}" \
  --fingerprint "${GENTOO_OPT_FINGERPRINT}"
```

Capture refuses to overwrite an existing identity. Output registration also
refuses duplicate artifact IDs. A new package fingerprint is required for a
different build.

All capture, registration, and deployment work for one fingerprint is guarded
by the same private `flock` lock. The default bounded wait is 30 seconds and can
be reduced with `--lock-timeout-seconds`. Each `readelf` and `objcopy` command
runs with `LC_ALL=C`, `LANG=C`, a separate process group, and a 30-second
deadline. Timeout cleanup sends TERM, waits five seconds, then kills the whole
group; unpublished outputs are removed. The tool and grace deadlines are
configurable with `--tool-timeout-seconds` and
`--tool-kill-after-seconds`.

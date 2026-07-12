# Pre-strip BOLT capture and deployment hooks

These tools are the fail-closed `${ED}` transaction lane used by the Portage
`post_src_install` dispatcher. They never write directly to installed `/usr`.
The package identity is the exact 64-hex fingerprint produced by the Phase 2
identity tool.

Capture copies each eligible unstripped ELF once per hardlink group without
changing `${ED}` and publishes `inputs/<fingerprint>/manifest.json` atomically:

```bash
capture-input.sh --ed "${ED}" --cache-root "${GENTOO_OPT_BOLT_CACHE_ROOT}" \
  --fingerprint "${GENTOO_OPT_FINGERPRINT}"
```

Eligibility requires ELF64, x86-64, `ET_EXEC` or `ET_DYN`, executable code, a
full symbol table, `.rel.text`/`.rela.text`, a GNU build ID, and a nonempty
`.text`. The manifest also records every ineligible ELF reason, hardlink group,
symlink, mode, ownership intent, xattr/capability value, file hash, build ID,
and `.text` hash.

After an exact captured input has been profiled and optimized, register the
prepared output. Registration rejects an invalid ELF or an output without
`.note.bolt_info`:

```bash
register-output.sh --cache-root "${GENTOO_OPT_BOLT_CACHE_ROOT}" \
  --fingerprint "${GENTOO_OPT_FINGERPRINT}" --artifact-id ID --output FILE
```

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

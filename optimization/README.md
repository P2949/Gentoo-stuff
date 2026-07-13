# Gentoo system-wide optimization framework

This directory contains reviewed policy, schemas, fixtures, and eventually the
workload definitions for the system-wide PGO and BOLT project. Runtime package
identities, generation IDs, profiles, cached ELF inputs, outputs, and reports
belong below `/var/lib/gentoo-optimization` or
`/var/cache/gentoo-optimization`; they must not be committed here.

`policy.yaml` is intentionally JSON-compatible YAML so the bootstrap and
recovery environments can parse it with the Python standard library. An
`active_generation` value of `null` is authoritative: no live optimization
generation exists yet.

The exact Phase 1 BOLT default uses `ext-tsp` block ordering and `cdsort`
function ordering. A policy change to either algorithm requires the complete
fixed `ET_EXEC`, PIE, and DSO fixture gate before it can be used for package
deployment.

`exclusions.yaml` and `package-overrides.yaml` start empty. Entries may be
added only with exact package/artifact identity, a reviewed reason, and durable
evidence. They are never a substitute for an unresolved or failed state.

## Live framework trust boundary

Root must not parse or execute the installer from the user-writable checkout.
After reviewing a clean commit, publish the installer itself through trusted
system tools, record its hash, and invoke only that root-owned copy:

```sh
sha256sum scripts/optimization/install-framework.sh
doas install -d -o root -g root -m 0755 \
    /var/lib/gentoo-optimization/bootstrap
doas install -o root -g root -m 0755 -T \
    scripts/optimization/install-framework.sh \
    /var/lib/gentoo-optimization/bootstrap/install-framework.sh.partial
doas mv -T \
    /var/lib/gentoo-optimization/bootstrap/install-framework.sh.partial \
    /var/lib/gentoo-optimization/bootstrap/install-framework.sh
doas sync -f /var/lib/gentoo-optimization/bootstrap
doas sha256sum \
    /var/lib/gentoo-optimization/bootstrap/install-framework.sh
# Compare this root-owned hash with the reviewed hash printed above.
doas /var/lib/gentoo-optimization/bootstrap/install-framework.sh \
    --source-root "$PWD"
doas /var/lib/gentoo-optimization/bootstrap/install-framework.sh \
    --source-root "$PWD" --check
```

The root-owned copy requires `--source-root`, rejects a dirty production
worktree, checks that its own bytes equal the installer in the one-time source
snapshot, and aborts if any selected source or Git identity changes while the
snapshot is made. Thus root executes a previously copied regular file; it reads
the checkout only as untrusted input subject to before/snapshot/after identity
checks. Git inspection runs as the checkout owner in a minimal environment with
repository-local hooks, fsmonitor, and external diff execution disabled; root
does not execute Git-configured helpers from the checkout. Re-run the trusted
`install`/`mv` sequence whenever the installer itself changes.

The installer builds one content-addressed, root-owned candidate below
`/var/lib/gentoo-optimization/framework-<sha256>`. That candidate contains the
exact Portage configuration, `codex-local` overlay, BOLT/PGO/state runtime,
state schemas, generated-policy generation, source inventory, candidate
inventory, and canonical manifest. It verifies the candidate completely before
publishing regular root-owned entry points. It acquires the stable mode-0600
locks in the global order `framework-install -> project -> generation`, then
holds all extant BOLT transaction locks and a Portage-process quiescence gate. It
changes `/var/lib/gentoo-optimization/framework-current` last. Both
`/etc/portage` and `codex-local` resolve through that one current link. Error,
timeout, and signal paths restore the previous helper, hook, manifest,
`/etc/portage`, and current-generation target and remove unpublished partials.

The canonical manifest at
`/var/lib/gentoo-optimization/state/project/phase-2-framework-install.manifest`
records the installer, source, candidate and framework hashes; exact current and
previous generation targets; Git commit and clean/dirty identity; `jq` identity;
and every installed code/schema entry point. `--check` regenerates this
manifest byte for byte, verifies exact tree entry sets, checks the selected
profile as the `portage` user, confirms the global QA hook really sorts last,
and asks Portage to resolve the active `codex-local` repository.

The initial generated-policy generation is deliberately empty, but it is not
an unused archive: candidate-local symlinks bind its `package.env` and `env/`
directly into the same candidate's Portage configuration. A future policy
publisher must produce a trusted directory named
`generated-policy-<canonical-content-sha256>` containing a regular
`package.env` and `env/`; pass it with
`--generated-policy-generation /absolute/path` and its authoritative
`--frozen-inventory /var/lib/gentoo-optimization/generations/<id>/frozen-inventory.json`
to both install and check. The
installer snapshots, hashes, validates, and activates that policy only as part
of a new whole-framework generation. Its parser accepts exactly one valid,
non-wildcard `=category/package-version` atom and one confined
`optimization/generated/<safe>.conf` path per
unique `package.env` row. Every referenced environment must exist, every file
must be referenced, and environment files are assignment-only with a narrow
allowlist of dispatcher stage/identity variables and shell-inert values.
`export`, `+=`, sourcing, substitutions, commands, conditionals, nested paths,
and opaque/unreferenced files fail closed.
Production publication also requires every generated atom to match the live
installed universe and the supplied frozen inventory. The frozen JSON is
validated as data by the root-owned bootstrap (exact schema/keys, ordered unique
CPVs and owned paths/directories, canonical paths, owner membership, and
disjoint path namespaces), snapshotted without races, and bound by SHA-256 into
the framework aggregate and manifest. Its exact CPV set is the authority for
every generated atom; no helper copied from the mutable checkout is executed to
perform this pre-publication check.

Code/configuration ancestors and immutable generation roots are root-owned and
non-writable by group/other. Validated profiles use root:`portage` mode `0750`,
BOLT caches remain root-only mode `0700`, and the raw-PGO top-level spool is
root:root mode `0755` (traversable but not writable by build or desktop users).
The workload framework must provision each exact generation/package job leaf
separately as a root-owned sticky mode `1733` directory, or with an equally
exact reviewed ACL covering both the Portage build identity and runtime training
identity; the installer deliberately creates no writable leaf. An active optimization lane fails if an
installed helper is absent, symlinked, incorrectly owned, writable by
group/other, or different from the reviewed candidate.

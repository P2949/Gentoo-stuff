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

The reviewed checkout is not executed as root directly. After a framework
change, install it with:

```sh
doas scripts/optimization/install-framework.sh
doas scripts/optimization/install-framework.sh --check
```

The installer publishes root-owned immutable generations for both the Portage
configuration and this repository's `codex-local` overlay, points
`/etc/portage` and the overlay's `repos.conf` entry at their root-owned current
links, installs regular root-owned validation and BOLT helpers below
`/usr/local/libexec/gentoo-optimization`, and installs the lexically last
pre-strip QA hook as a regular root-owned file. Code/configuration ancestors are
root-owned mode `0755` so Portage `userpriv` can traverse them; private profile,
BOLT, and state namespaces remain mode `0700`. It records exact
source/destination hashes in
`/var/lib/gentoo-optimization/state/project/phase-2-framework-install.manifest`.
An active optimization lane fails if its installed helper is absent, symlinked,
non-root-owned, group/world-writable, or different from the reviewed source at
the framework gate.

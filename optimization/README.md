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

# Global PGO Use

This repository is global-first by design. PGO use is enabled for every
package through `portage/package.env/50-global-pgo`, but it only changes flags
when `/var/tmp/pgo-profiles/<category>/<package>/merged.profdata` exists.

The conditional logic lives in `portage/bashrc` because Portage env profiles
are assignment-only files. Packages without profiles keep the normal aggressive
global defaults.

## Paths

Use `scripts/pgo/package-profile-path.sh <category> <package>` to print the
profile root. The merged profile consumed by Portage is:

```text
/var/tmp/pgo-profiles/<category>/<package>/merged.profdata
```

Raw instrumentation profiles are collected under:

```text
/var/tmp/pgo-profiles/<category>/<package>/raw
```

## Workflow

1. Build a package with `pgo-instrument.conf` during a dedicated training pass.
2. Exercise the package workload.
3. Merge raw profiles with `scripts/pgo/merge-instr-profile.sh`.
4. Rebuild normally and let global PGO use pick up `merged.profdata`.

Sample profiles can be collected with `scripts/pgo/collect-sample-profile.sh`
and converted with `scripts/pgo/make-sample-prof.sh`.

Demote stale or harmful profiles with `no-pgo-use.conf`; do not disable global
PGO use for the whole repository because one package failed.

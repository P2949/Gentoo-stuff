# Gentoo-stuff

Gentoo Portage policy and a fail-closed framework for a system-wide PGO and
BOLT project. The project is currently in Phase 2. No optimization generation
is authorized or active, the Phase 3 installed-package inventory is not frozen,
and none of the repository-only fixtures constitute live-system coverage.

Phase 2 is scope-frozen until Candidate B authorization. Do not add a new
subsystem, optimization-policy axis, evidence category, or broad refactor
unless an existing required Phase 2 gate exposes a reproducible blocker that
cannot be fixed within the current architecture. This freeze does not permit
skipping or weakening an existing safeguard.

## Boot and kernel safety boundary

This is a userspace optimization project. Automated and LLM-directed work must
never create, edit, delete, reorder, select, or arm a firmware/EFI/bootloader
entry; set or clear `BootNext`; change `BootOrder`; write EFI variables; invoke
boot-entry management tools; or modify `/efi`, `/boot`, bootloader
configuration, kernel images, or initramfs images. Existing boot configuration
must remain unchanged and outside project authority. No project test, recovery gate,
acceptance condition, or evidence claim may depend on a recovery boot entry.

Kernel configuration, build, installation, replacement, and deployment are
human-only activities outside this project. The project must not ask a human to
perform them as a project step. Kernel lifecycle packages and artifacts are
recorded with the machine-valid terminal reason `kernel-policy-exclusion` (the
human-only system boundary), while read-only running-kernel observations may be retained solely to identify the
userspace execution environment. See [`AGENTS.md`](AGENTS.md) for the binding
agent instructions.

The default is intentionally risky and bleeding edge. Packages should start at
the most aggressive tier, then be demoted one axis at a time through
`portage/package.env` only when a real build failure, runtime bug, or
miscompilation proves the need.

Start with:

- [`plans/system-wide-pgo-bolt.md`](plans/system-wide-pgo-bolt.md) for the
  authoritative project plan and completion conditions.
- [`optimization/README.md`](optimization/README.md) for the framework trust
  boundary, atomic installation model, and policy layout.
- [`scripts/optimization/pgo/README.md`](scripts/optimization/pgo/README.md)
  and [`scripts/optimization/bolt/README.md`](scripts/optimization/bolt/README.md)
  for the profile and ELF identity contracts.
- [`portage/env/README`](portage/env/README) for the compiler-flag fallback
  ladder and local Portage policy notes.
- [`docs/commit-history-map.md`](docs/commit-history-map.md) for additive
  descriptions of generically named evidence-bearing ancestors and the rule
  prohibiting history rewrites.

For non-mutating repository validation, run:

```sh
PATH=/usr/bin:/bin /usr/bin/bash tests/run-optimization-tests.sh --mode smoke
PATH=/usr/bin:/bin /usr/bin/bash tests/run-optimization-tests.sh --mode checkpoint-smoke
PATH=/usr/bin:/bin /usr/bin/bash tests/run-optimization-tests.sh --mode portable-complete
```

Smoke is the short feedback gate. Checkpoint-smoke selects 18 exact methods:
four supervisor containment/release paths, nine portable fake-`unshare`
terminal/watchdog paths, and five checkpoint state-machine/process-group paths,
without running the complete recovery matrix.
Portable-complete runs the complete portable non-capability, non-stress suite
and reports reason-bearing environment skips; it is not an authoritative
Gentoo-host pass.

Live package-state recovery and Phase 2 authorization have exact operator
procedures:

- [`docs/binpkg-checkpoint-runbook.md`](docs/binpkg-checkpoint-runbook.md) for
  creating, activating, reconciling, and proving an exact package-managed
  rollback checkpoint. It does not manage the boot chain.
- [`docs/phase2-production-profile-transaction.md`](docs/phase2-production-profile-transaction.md)
  for Candidate A/B installation, the supervised production sample-PGO
  transaction, recovery, evidence retention, and detached authorization.

Those runbooks are live-Gentoo-only. They mutate root-owned Portage, recovery,
framework, or package state and must be executed from the exact reviewed clean
commit with the documented root-owned bootstrap and evidence paths. Do not run
them in a generic development container, do not infer authorization from a
portable pass, and do not activate a generation or freeze Phase 3 inventory
before the plan's live gates are complete.

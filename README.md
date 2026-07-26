# Gentoo-stuff

Gentoo Portage policy and a fail-closed framework for a system-wide PGO and
BOLT project. The project is currently in Phase 2. No optimization generation
is authorized or active, the Phase 3 installed-package inventory is not frozen,
and none of the repository-only fixtures constitute live-system coverage.

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

For non-mutating repository validation, run:

```sh
tests/run-optimization-tests.sh --mode smoke
tests/run-optimization-tests.sh --mode checkpoint-smoke
tests/run-optimization-tests.sh --mode portable-complete
```

Smoke is the short feedback gate. Checkpoint-smoke adds bounded timeout and
fast-reparent harness-cleanup regressions plus four recovery state-machine
paths without running the complete recovery matrix.
Portable-complete runs the complete portable non-capability, non-stress suite
and reports reason-bearing environment skips; it is not an authoritative
Gentoo-host pass.

Live recovery and Phase 2 authorization have exact operator procedures:

- [`docs/binpkg-checkpoint-runbook.md`](docs/binpkg-checkpoint-runbook.md) for
  creating, activating, reconciling, and proving an exact package-managed
  rollback checkpoint.
- [`docs/phase2-production-profile-transaction.md`](docs/phase2-production-profile-transaction.md)
  for Candidate A/B installation, the supervised production sample-PGO
  transaction, recovery, evidence retention, and detached authorization.

Those runbooks are live-Gentoo-only. They mutate root-owned Portage, recovery,
framework, or package state and must be executed from the exact reviewed clean
commit with the documented root-owned bootstrap and evidence paths. Do not run
them in a generic development container, do not infer authorization from a
portable pass, and do not activate a generation or freeze Phase 3 inventory
before the plan's live gates are complete.

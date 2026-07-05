# Gentoo-stuff Global-First Performance Expansion Plan

## Objective

Rewrite and extend the performance experiment plan so it matches the actual purpose of this repository:

> **Push every reasonable performance experiment into the default/global path first, then demote packages only when they prove they cannot build, link, run, or behave correctly with that option.**

This repository is not trying to be a conservative Gentoo configuration. It is a performance and toolchain stress-test repo. The correct default is therefore:

```text
global first
maximum optimization first
maximum compiler/linker/runtime pressure first
package demotion only after evidence
document every demotion reason
retry old demotions after major toolchain updates
```

The previous attached plan described many profiles as opt-in. That is not the desired direction. This revised plan converts those ideas into a **global-first optimization expansion**:

```text
BOLT readiness metadata          -> global by default
sample-PGO mapping metadata      -> global by default
code-size/i-cache flags          -> global by default
visibility/internalization flags -> global by default
optimization remarks             -> global by default first, demote if too noisy/broken
Rust panic/size/link layout      -> global by default first, demote if broken
PGO use                          -> global if a package profile exists
BOLT execution                   -> broad package/binary sweep tooling
allocator testing                -> global user-session experiment first, then demote apps if needed
```

This plan uses the current repo state as the base. The current repo already has:

```text
portage/make.conf with separated compile/link flag categories
aggressive clang/lld/libc++ defaults
Polly
OpenMP
ThinLTO
unified LTO
whole-program vtables
-fstrict-vtable-pointers
-ffast-math
-fno-stack-protector
-Bsymbolic
controlled Portage concurrency
buildpkg and binpkg-multi-instance rollback
documented fallback ladder in portage/env/README
docs/maintenance-checklist.md
```

The goal is to add the next optimization layer globally while preserving the repo's diagnostic discipline.

---

# Implementation Status

This section is updated as implementation items complete. After each completed
item, the plan is re-read and the next action is adjusted against the current
document.

- [x] Phase 0 — Baseline integrity check passed on this working tree.
- [x] Phase 1 — Plan text verified as global-first; strict grep checks passed.
- [x] Phase 2 — Global performance flags added to `make.conf`; strict checks passed.
- [x] Phase 3 — New global-axis demotion profiles added; strict checks passed.
- [x] Phase 4 — Global PGO use-if-available mechanism added; wildcard Portage check passed.
- [x] Phase 5 — PGO tooling scripts added; strict checks passed.
- [x] Phase 6 — BOLT broad binary sweep tooling added; strict checks and package lister probe passed.
- [x] Phase 7 — Rust global performance expansion added; pretend checks passed and `dev-util/rustup` rebuilt successfully.
- [x] Phase 8 — User-session allocator experiment tooling added; strict checks passed.
- [x] Phase 9 — Benchmark harness added; strict checks passed.
- [x] Phase 10 — `portage/env/README` updated; strict checks passed.
- [x] Phase 11 — Maintenance checklist updated; strict checks passed.
- [x] Phase 12 — Global build validation matrix attempted and recorded.
- [x] Phase 13 — Global-first performance lab docs added.
- [x] Phase 14 — Final repo-wide validation completed.

---

# Global Policy Change

## Legacy policy replaced by this plan

Earlier planning treated performance experiments as package-scoped opt-in
profiles that waited for promotion. That approach has been replaced.

```text
Add performance experiments globally first.
Demote packages only after failures or measured regressions.
Every global axis must have a narrow demotion profile.
Benchmarking is used to decide whether to keep or demote, not whether to try.
```

This repository is global-first by design.

## New policy

Replace it with:

```text
All performance options that can plausibly be made global should be added to
the default aggressive tier first.

A package should only be demoted when there is evidence:

    build failure
    configure failure
    link failure
    test failure
    runtime crash
    measurable regression
    security/boot/login breakage
    unreasonable generated-file explosion
    impossible package-specific profile path

The demotion must be narrow. Remove only the failing axis.
```

## Required documentation wording

Any documentation added by this plan should say:

```text
This repository is global-first by design.
Profiles are not promoted after proving safe.
Profiles are global until a package proves unsafe.
```

---

# Safety Model

This plan intentionally pushes risky flags globally. To make that survivable, every new global axis must have a demotion profile.

## New global axes and demotion profiles

| Global axis | Added globally where | Demotion profile |
|---|---|---|
| BOLT readiness metadata | `make.conf` compile/link flags | `no-bolt-ready.conf` |
| Sample-PGO mapping metadata | `make.conf` compile flags | `no-profile-mapping.conf` |
| Function/data sections | `make.conf` compile flags | `no-section-splitting.conf` |
| Linker GC sections / ICF | `make.conf` linker flags | `no-gc-icf.conf` |
| Hidden visibility | `make.conf` compile flags | `no-hidden-visibility.conf` |
| Optimization remarks | `make.conf` compile flags | `no-opt-remarks.conf` |
| Rust panic abort | `make.conf` `RUSTFLAGS` | `rust-unwind.conf` |
| Rust linker layout flags | `make.conf` `RUSTFLAGS` | `rust-no-layout-linkflags.conf` |
| PGO use when profile exists | global package.env env file | `no-pgo-use.conf` |
| Instrumented PGO generation | explicit global sweep mode | disable sweep / demote package |
| User-session allocator preload | scripts/environment toggles | app-specific launcher without preload |

## Demotion rule

When a package fails after the global expansion, demote in this order unless the error clearly identifies a cause:

```text
1. no-opt-remarks.conf
2. no-profile-mapping.conf
3. no-bolt-ready.conf
4. no-hidden-visibility.conf
5. no-gc-icf.conf
6. no-section-splitting.conf
7. no-pgo-use.conf
8. existing fallbacks: no-polly, no-unified-lto, thin-lto-only, no-openmp, no-lto, O2, plain
```

Reasoning:

```text
optimization remarks create lots of side files and build-system noise
profile mapping flags can confuse older assemblers/build scripts
BOLT relocation metadata can expose link/build-system assumptions
hidden visibility can break exported-symbol assumptions
ICF/gc-sections can break packages relying on unusual section retention
section splitting can expose assembler/linker bugs
PGO use can fail on stale/mismatched profiles
existing compiler/LTO/Polly demotions remain last after the new axes are tested
```

---

# Phase 0 — Baseline Integrity Check Before Editing

## Purpose

Before changing the plan or implementation, confirm the repo is in the expected cleaned-up state.

## Required checks

Run from repo root:

```bash
set -euo pipefail

test -f portage/make.conf
test -f portage/env/README
test -f docs/maintenance-checklist.md
test -d portage/env
test -d portage/package.env

grep -n 'COMMON_FLAGS=' portage/make.conf
grep -n 'RUNTIME_LINK_FLAGS=' portage/make.conf
grep -n 'FORCED_LIBS=' portage/make.conf
grep -n 'LD_OPT_FLAGS=' portage/make.conf
grep -n 'RUSTFLAGS=' portage/make.conf

grep -n 'Default policy:' portage/env/README
grep -n 'Fallback ladder' portage/env/README

find portage -name '._cfg*' -print | tee /tmp/gentoo-stuff-cfg-residue.txt
test ! -s /tmp/gentoo-stuff-cfg-residue.txt

grep -R '^sys-apps/hwloc' -n portage/package.env | tee /tmp/gentoo-stuff-hwloc.txt
test "$(wc -l < /tmp/gentoo-stuff-hwloc.txt)" -eq 1
```

## Strict acceptance criteria

All of these must be true:

```text
make.conf exists
env README exists
maintenance checklist exists
make.conf has separated COMMON_FLAGS/RUNTIME_LINK_FLAGS/FORCED_LIBS/LD_OPT_FLAGS
no ._cfg* files are tracked
sys-apps/hwloc appears once in package.env
```

## Failure handling

If any check fails:

```text
stop
fix repo state first
do not start the performance expansion on a dirty baseline
```

---

# Phase 1 — Rewrite the Plan Itself to Global-First

## Purpose

The attached plan still contains opt-in language. Replace it with global-first language.

## File to modify

```text
plans/plan.md
```

or, if keeping this as the attached plan artifact:

```text
plan.md
```

## Required removals

Remove or rewrite legacy statements that forbid global experimentation,
require package-only trials, keep package environment entries inactive until
manual testing, reject global use, or require benchmarks before the experiment
is even tried.

```text
Add performance experiments globally first.
Demote packages only after failures or measured regressions.
Every global axis must have a narrow demotion profile.
Benchmarking is used to decide whether to keep or demote, not whether to try.
```

## Required replacements

Use wording like:

```text
Add performance experiments globally first.
Demote packages only after failures or measured regressions.
Every global axis must have a narrow demotion profile.
Benchmarking is used to decide whether to keep or demote, not whether to try.
```

## Strict checks

Run:

```bash
grep -n "D[o] not add new global performance flags" plan.md && exit 1 || true
grep -n "must be opt-[i]n" plan.md && exit 1 || true
grep -n "only to selecte[d]" plan.md && exit 1 || true
grep -n "Keep entrie[s] commented" plan.md && exit 1 || true
grep -n "global-first" plan.md
grep -n "demote" plan.md
```

## Acceptance criteria

```text
No old opt-in policy remains.
The document clearly says experiments are global-first.
The document clearly says failures are handled through demotion.
```

---

# Phase 2 — Add Global Performance Flags to `make.conf`

## Purpose

Move the new performance experiments into the default aggressive tier.

This phase makes the default build emit metadata and use layout/code-size/internalization flags broadly. It deliberately increases build pressure and risk.

## File to edit

```text
portage/make.conf
```

## New flag groups to add

Add these after `UARG_FLAGS` or after the existing LTO/vtable sections.

```bash
############################################################################
# Global performance experiment flags
#
# These are intentionally global. The repository policy is to push packages
# through the most aggressive default first, then demote only packages that
# prove they cannot tolerate a specific axis.
############################################################################

# BOLT/profile-layout readiness:
# -gline-tables-only gives enough debug mapping for BOLT/sample-profile work
# without full debug info.
BOLT_READY_FLAGS="-gline-tables-only"

# Sample-PGO / AutoFDO profile mapping:
# -fdebug-info-for-profiling and -funique-internal-linkage-names improve
# sampled profile fidelity.
# -fpseudo-probe-for-profiling adds stable profile anchors for LLVM sample PGO.
PROFILE_MAPPING_FLAGS="-fdebug-info-for-profiling -funique-internal-linkage-names -fpseudo-probe-for-profiling"

# Code-size / i-cache experiment:
# split functions/data into sections so lld can discard or fold more code.
SECTION_FLAGS="-ffunction-sections -fdata-sections"

# Global hidden visibility experiment:
# This is risky for shared libraries and plugin ecosystems, but intentionally
# global for this repo. Packages that need public symbol visibility get
# demoted with no-hidden-visibility.conf.
VISIBILITY_FLAGS="-fvisibility=hidden"
CXX_VISIBILITY_FLAGS="-fvisibility-inlines-hidden"

# Optimization remarks:
# This creates .opt.yaml files and may be noisy. It is global first so the
# repo can collect optimization-miss information broadly. Packages/build
# systems that choke on these files get no-opt-remarks.conf.
OPT_REMARK_FLAGS="-fsave-optimization-record=yaml -fdiagnostics-show-hotness"

# Aggregate of the new global experiment flags.
GLOBAL_PERF_FLAGS="${BOLT_READY_FLAGS} ${PROFILE_MAPPING_FLAGS} ${SECTION_FLAGS} ${VISIBILITY_FLAGS} ${OPT_REMARK_FLAGS}"
```

## Modify `COMMON_FLAGS`

Current shape:

```bash
COMMON_FLAGS="${POLLY_FLAGS} ${AGGRO_OPT_FLAGS} ${VTABLE_OPT_FLAGS} ${LTO_FLAGS} ${LTO_UNI_FLAGS} ${UARG_FLAGS}"
```

Change to:

```bash
COMMON_FLAGS="${POLLY_FLAGS} ${AGGRO_OPT_FLAGS} ${GLOBAL_PERF_FLAGS} ${VTABLE_OPT_FLAGS} ${LTO_FLAGS} ${LTO_UNI_FLAGS} ${UARG_FLAGS}"
```

## Modify `CXXFLAGS`

Current shape:

```bash
CXXFLAGS="${COMMON_FLAGS} -stdlib=libc++"
```

Change to:

```bash
CXXFLAGS="${COMMON_FLAGS} ${CXX_VISIBILITY_FLAGS} -stdlib=libc++"
```

## Modify linker flags

Add:

```bash
# Global BOLT/code-layout linker metadata.
BOLT_READY_LD_FLAGS="-Wl,--emit-relocs -Wl,--build-id"

# Global section cleanup/folding.
# --gc-sections removes unused sections produced by -ffunction-sections and
# -fdata-sections.
# --icf=safe folds identical functions conservatively.
SECTION_LD_FLAGS="-Wl,--gc-sections -Wl,--icf=safe"
```

Current shape:

```bash
LD_OPT_FLAGS="-Wl,--as-needed -Wl,--lto-O3 -Wl,--lto-CGO3 -Wl,-O2 -Wl,-Bsymbolic -Wl,--thinlto-cache-dir=/var/tmp/thinlto-cache -Wl,--thinlto-cache-policy=cache_size_bytes=8g"
```

Change to:

```bash
LD_OPT_FLAGS="-Wl,--as-needed -Wl,--lto-O3 -Wl,--lto-CGO3 -Wl,-O2 -Wl,-Bsymbolic ${BOLT_READY_LD_FLAGS} ${SECTION_LD_FLAGS} -Wl,--thinlto-cache-dir=/var/tmp/thinlto-cache -Wl,--thinlto-cache-policy=cache_size_bytes=8g"
```

## Modify `FEATURES`

Add `splitdebug` globally.

Current `FEATURES` should already include rollback features. Ensure it contains:

```text
buildpkg
binpkg-multi-instance
collision-protect
splitdebug
```

If `splitdebug` is absent, add it.

## Strict checks

Run:

```bash
set -euo pipefail

grep -n '^BOLT_READY_FLAGS=' portage/make.conf
grep -n '^PROFILE_MAPPING_FLAGS=' portage/make.conf
grep -n '^SECTION_FLAGS=' portage/make.conf
grep -n '^VISIBILITY_FLAGS=' portage/make.conf
grep -n '^CXX_VISIBILITY_FLAGS=' portage/make.conf
grep -n '^OPT_REMARK_FLAGS=' portage/make.conf
grep -n '^GLOBAL_PERF_FLAGS=' portage/make.conf
grep -n '^BOLT_READY_LD_FLAGS=' portage/make.conf
grep -n '^SECTION_LD_FLAGS=' portage/make.conf

grep -n '^COMMON_FLAGS=' portage/make.conf | grep 'GLOBAL_PERF_FLAGS'
grep -n '^CXXFLAGS=' portage/make.conf | grep 'CXX_VISIBILITY_FLAGS'
grep -n '^LD_OPT_FLAGS=' portage/make.conf | grep 'BOLT_READY_LD_FLAGS'
grep -n '^LD_OPT_FLAGS=' portage/make.conf | grep 'SECTION_LD_FLAGS'

grep -n '^FEATURES=' portage/make.conf | grep 'buildpkg'
grep -n '^FEATURES=' portage/make.conf | grep 'binpkg-multi-instance'
grep -n '^FEATURES=' portage/make.conf | grep 'collision-protect'
grep -n '^FEATURES=' portage/make.conf | grep 'splitdebug'

bash -n portage/make.conf
```

## Portage environment check

On a live system after installing config:

```bash
emerge --info | grep -E '^(CFLAGS|CXXFLAGS|FCFLAGS|FFLAGS|LDFLAGS|FEATURES)='
```

Required output properties:

```text
CFLAGS contains -gline-tables-only
CFLAGS contains -fdebug-info-for-profiling
CFLAGS contains -funique-internal-linkage-names
CFLAGS contains -fpseudo-probe-for-profiling
CFLAGS contains -ffunction-sections
CFLAGS contains -fdata-sections
CFLAGS contains -fvisibility=hidden
CFLAGS contains -fsave-optimization-record=yaml

CXXFLAGS contains everything above
CXXFLAGS contains -fvisibility-inlines-hidden
CXXFLAGS contains -stdlib=libc++

LDFLAGS contains -Wl,--emit-relocs
LDFLAGS contains -Wl,--build-id
LDFLAGS contains -Wl,--gc-sections
LDFLAGS contains -Wl,--icf=safe

FEATURES contains splitdebug
FEATURES contains buildpkg
FEATURES contains binpkg-multi-instance
FEATURES contains collision-protect
```

## Initial build test set

Run pretend first:

```bash
emerge -pv app-arch/zstd
emerge -pv app-arch/xz-utils
emerge -pv sys-apps/coreutils
emerge -pv dev-libs/libffi
emerge -pv dev-libs/openssl
emerge -pv gui-wm/gamescope
```

Then build a small first wave:

```bash
emerge -1av app-arch/zstd
emerge -1av app-arch/xz-utils
emerge -1av sys-apps/coreutils
```

## Acceptance criteria

```text
make.conf parses
emerge --info shows all global performance flags
pretend merges complete
the small first-wave build completes or failures are assigned to a demotion profile
```

---

# Phase 3 — Add Demotion Profiles for New Global Axes

## Purpose

Every new global performance axis must have a narrow fallback.

## Files to create

```text
portage/env/no-bolt-ready.conf
portage/env/no-profile-mapping.conf
portage/env/no-section-splitting.conf
portage/env/no-gc-icf.conf
portage/env/no-hidden-visibility.conf
portage/env/no-opt-remarks.conf
portage/env/no-global-perf-extras.conf
```

## `portage/env/no-bolt-ready.conf`

```bash
############################################################################
# Remove BOLT readiness metadata.
#
# Use when -gline-tables-only, --emit-relocs, or build-id handling breaks a
# package's build, install, splitdebug, or stripping behavior.
############################################################################

COMMON_FLAGS="${POLLY_FLAGS} ${AGGRO_OPT_FLAGS} ${PROFILE_MAPPING_FLAGS} ${SECTION_FLAGS} ${VISIBILITY_FLAGS} ${OPT_REMARK_FLAGS} ${VTABLE_OPT_FLAGS} ${LTO_FLAGS} ${LTO_UNI_FLAGS} ${UARG_FLAGS}"

CFLAGS="${COMMON_FLAGS}"
CXXFLAGS="${COMMON_FLAGS} ${CXX_VISIBILITY_FLAGS} ${LIB_FLAGS}"
FCFLAGS="${COMMON_FLAGS}"
FFLAGS="${COMMON_FLAGS}"

LD_OPT_FLAGS_NO_BOLT="-Wl,--as-needed -Wl,--lto-O3 -Wl,--lto-CGO3 -Wl,-O2 -Wl,-Bsymbolic ${SECTION_LD_FLAGS} -Wl,--thinlto-cache-dir=/var/tmp/thinlto-cache -Wl,--thinlto-cache-policy=cache_size_bytes=8g"
LDFLAGS="${LDFLAGS} ${COMMON_FLAGS} ${RUNTIME_LINK_FLAGS} ${LD_OPT_FLAGS_NO_BOLT} ${FORCED_LIBS}"
```

## `portage/env/no-profile-mapping.conf`

```bash
############################################################################
# Remove sample-PGO/profile mapping metadata.
#
# Use when -fdebug-info-for-profiling, -funique-internal-linkage-names, or
# -fpseudo-probe-for-profiling breaks a package.
############################################################################

COMMON_FLAGS="${POLLY_FLAGS} ${AGGRO_OPT_FLAGS} ${BOLT_READY_FLAGS} ${SECTION_FLAGS} ${VISIBILITY_FLAGS} ${OPT_REMARK_FLAGS} ${VTABLE_OPT_FLAGS} ${LTO_FLAGS} ${LTO_UNI_FLAGS} ${UARG_FLAGS}"

CFLAGS="${COMMON_FLAGS}"
CXXFLAGS="${COMMON_FLAGS} ${CXX_VISIBILITY_FLAGS} ${LIB_FLAGS}"
FCFLAGS="${COMMON_FLAGS}"
FFLAGS="${COMMON_FLAGS}"

LDFLAGS="${LDFLAGS} ${COMMON_FLAGS} ${LD_CLEAN_FLAGS}"
```

## `portage/env/no-section-splitting.conf`

```bash
############################################################################
# Remove -ffunction-sections/-fdata-sections.
#
# Use when section splitting breaks assembler/linker behavior, increases
# binary size unreasonably, or causes runtime issues.
############################################################################

COMMON_FLAGS="${POLLY_FLAGS} ${AGGRO_OPT_FLAGS} ${BOLT_READY_FLAGS} ${PROFILE_MAPPING_FLAGS} ${VISIBILITY_FLAGS} ${OPT_REMARK_FLAGS} ${VTABLE_OPT_FLAGS} ${LTO_FLAGS} ${LTO_UNI_FLAGS} ${UARG_FLAGS}"

CFLAGS="${COMMON_FLAGS}"
CXXFLAGS="${COMMON_FLAGS} ${CXX_VISIBILITY_FLAGS} ${LIB_FLAGS}"
FCFLAGS="${COMMON_FLAGS}"
FFLAGS="${COMMON_FLAGS}"

LDFLAGS="${LDFLAGS} ${COMMON_FLAGS} ${LD_CLEAN_FLAGS}"
```

## `portage/env/no-gc-icf.conf`

```bash
############################################################################
# Remove linker section GC and identical code folding.
#
# Use when --gc-sections or --icf=safe breaks a package, removes required
# sections, or creates runtime/plugin/export problems.
############################################################################

LD_OPT_FLAGS_NO_GC_ICF="-Wl,--as-needed -Wl,--lto-O3 -Wl,--lto-CGO3 -Wl,-O2 -Wl,-Bsymbolic ${BOLT_READY_LD_FLAGS} -Wl,--thinlto-cache-dir=/var/tmp/thinlto-cache -Wl,--thinlto-cache-policy=cache_size_bytes=8g"
LD_CLEAN_FLAGS="${RUNTIME_LINK_FLAGS} ${LD_OPT_FLAGS_NO_GC_ICF} ${FORCED_LIBS}"
LDFLAGS="${LDFLAGS} ${COMMON_FLAGS} ${LD_CLEAN_FLAGS}"
```

## `portage/env/no-hidden-visibility.conf`

```bash
############################################################################
# Remove global hidden visibility.
#
# Use for shared libraries, plugin hosts, language runtimes, Wine components,
# Qt/GTK stack libraries, or any package where hidden visibility breaks public
# ABI, dlopen, symbol lookup, or plugin loading.
############################################################################

COMMON_FLAGS="${POLLY_FLAGS} ${AGGRO_OPT_FLAGS} ${BOLT_READY_FLAGS} ${PROFILE_MAPPING_FLAGS} ${SECTION_FLAGS} ${OPT_REMARK_FLAGS} ${VTABLE_OPT_FLAGS} ${LTO_FLAGS} ${LTO_UNI_FLAGS} ${UARG_FLAGS}"

CFLAGS="${COMMON_FLAGS}"
CXXFLAGS="${COMMON_FLAGS} ${LIB_FLAGS}"
FCFLAGS="${COMMON_FLAGS}"
FFLAGS="${COMMON_FLAGS}"

LDFLAGS="${LDFLAGS} ${COMMON_FLAGS} ${LD_CLEAN_FLAGS}"
```

## `portage/env/no-opt-remarks.conf`

```bash
############################################################################
# Remove optimization remark output.
#
# Use when .opt.yaml output confuses build systems, fills disks, causes
# install collisions, or creates unreasonable build-tree noise.
############################################################################

COMMON_FLAGS="${POLLY_FLAGS} ${AGGRO_OPT_FLAGS} ${BOLT_READY_FLAGS} ${PROFILE_MAPPING_FLAGS} ${SECTION_FLAGS} ${VISIBILITY_FLAGS} ${VTABLE_OPT_FLAGS} ${LTO_FLAGS} ${LTO_UNI_FLAGS} ${UARG_FLAGS}"

CFLAGS="${COMMON_FLAGS}"
CXXFLAGS="${COMMON_FLAGS} ${CXX_VISIBILITY_FLAGS} ${LIB_FLAGS}"
FCFLAGS="${COMMON_FLAGS}"
FFLAGS="${COMMON_FLAGS}"

LDFLAGS="${LDFLAGS} ${COMMON_FLAGS} ${LD_CLEAN_FLAGS}"
```

## `portage/env/no-global-perf-extras.conf`

```bash
############################################################################
# Remove all new global performance-expansion extras.
#
# This is a broad fallback. Use only after narrower demotions fail.
# It keeps the pre-expansion aggressive baseline:
# Polly, OpenMP, ThinLTO, unified LTO, whole-program vtables, O3/fast-math,
# libc++, and aggressive linker behavior.
############################################################################

COMMON_FLAGS="${POLLY_FLAGS} ${AGGRO_OPT_FLAGS} ${VTABLE_OPT_FLAGS} ${LTO_FLAGS} ${LTO_UNI_FLAGS} ${UARG_FLAGS}"

CFLAGS="${COMMON_FLAGS}"
CXXFLAGS="${COMMON_FLAGS} ${LIB_FLAGS}"
FCFLAGS="${COMMON_FLAGS}"
FFLAGS="${COMMON_FLAGS}"

LD_OPT_FLAGS_PRE_EXPANSION="-Wl,--as-needed -Wl,--lto-O3 -Wl,--lto-CGO3 -Wl,-O2 -Wl,-Bsymbolic -Wl,--thinlto-cache-dir=/var/tmp/thinlto-cache -Wl,--thinlto-cache-policy=cache_size_bytes=8g"
LDFLAGS="${LDFLAGS} ${COMMON_FLAGS} ${RUNTIME_LINK_FLAGS} ${LD_OPT_FLAGS_PRE_EXPANSION} ${FORCED_LIBS}"
```

## Strict checks

```bash
set -euo pipefail

for f in \
  portage/env/no-bolt-ready.conf \
  portage/env/no-profile-mapping.conf \
  portage/env/no-section-splitting.conf \
  portage/env/no-gc-icf.conf \
  portage/env/no-hidden-visibility.conf \
  portage/env/no-opt-remarks.conf \
  portage/env/no-global-perf-extras.conf
do
  test -f "$f"
  bash -n "$f"
done

grep -n 'emit-relocs' portage/env/no-bolt-ready.conf && exit 1 || true
grep -n 'fpseudo-probe-for-profiling' portage/env/no-profile-mapping.conf && exit 1 || true
grep -n 'ffunction-sections' portage/env/no-section-splitting.conf && exit 1 || true
grep -n 'icf=safe' portage/env/no-gc-icf.conf && exit 1 || true
grep -n 'fvisibility=hidden' portage/env/no-hidden-visibility.conf && exit 1 || true
grep -n 'fsave-optimization-record' portage/env/no-opt-remarks.conf && exit 1 || true
```

## Acceptance criteria

```text
Every new global axis has a demotion file.
Each demotion file parses as shell.
Each demotion file removes the intended axis.
No demotion file overwrites FEATURES from scratch.
```

---

# Phase 4 — Add Global PGO Use-If-Available Mechanism

## Purpose

PGO cannot blindly use a nonexistent profile. But the global-first version is:

```text
If a merged profile exists for a package, use it automatically.
If no profile exists, build with the global aggressive default.
```

This makes PGO use global across as many packages as possible without hard-failing the whole tree before profiles exist.

## Files to add

```text
portage/env/pgo-use-if-available.conf
portage/env/pgo-instrument.conf
portage/env/no-pgo-use.conf
portage/bashrc
portage/package.env/50-global-pgo
scripts/pgo/collect-sample-profile.sh
scripts/pgo/make-sample-prof.sh
scripts/pgo/merge-instr-profile.sh
scripts/pgo/pgo-path.sh
docs/pgo-global.md
```

## `portage/env/pgo-use-if-available.conf`

```bash
############################################################################
# Global PGO use-if-available.
#
# This is applied globally. The conditional profile check is implemented in
# /etc/portage/bashrc because package.env files are assignment-only profiles.
############################################################################

PGO_USE_IF_AVAILABLE="1"
```

## `portage/env/no-pgo-use.conf`

```bash
############################################################################
# Disable global PGO use-if-available for this package.
#
# Use when an existing profile is stale, harmful, or causes build failure.
############################################################################

PGO_DISABLE_USE="1"
```

## `portage/env/pgo-instrument.conf`

```bash
############################################################################
# Global-first PGO instrumentation profile.
#
# This is used for explicit training sweeps. Apply broadly during a dedicated
# PGO generation pass, not during normal world updates. The flag append is
# implemented in /etc/portage/bashrc.
############################################################################

PGO_INSTRUMENT="1"
PGO_DISABLE_USE="1"
```

## `/etc/portage/bashrc` PGO hook

The hook applies `-fprofile-use` only when the package's merged profile exists.
It also applies `-fprofile-generate` during explicit instrumentation sweeps.

## Global package.env entry

Create:

```text
portage/package.env/50-global-pgo
```

Content:

```text
# Global PGO use-if-available.
#
# This applies to every package. It only activates if:
#
#   /var/tmp/pgo-profiles/${CATEGORY}/${PN}/merged.profdata
#
# exists.
#
# Packages with stale or harmful profiles are demoted with no-pgo-use.conf.

*/* pgo-use-if-available.conf
```

## Important check about `*/*`

Before relying on this, validate that the active Portage accepts `*/*` in `package.env`.

Run:

```bash
emerge -pv app-arch/zstd | tee /tmp/package-env-wildcard-test.txt
```

Then temporarily add a harmless marker env file if needed:

```bash
echo 'TEST_PACKAGE_ENV_WILDCARD=1' > portage/env/test-wildcard.conf
echo '*/* test-wildcard.conf' > portage/package.env/99-test-wildcard
emerge --info app-arch/zstd 2>/dev/null | grep TEST_PACKAGE_ENV_WILDCARD || true
rm -f portage/env/test-wildcard.conf portage/package.env/99-test-wildcard
```

If Portage does **not** accept `*/*` in `package.env`, then do not guess. Use a generated package.env file listing installed packages:

```bash
qlist -IC | sed 's/$/ pgo-use-if-available.conf/' > portage/package.env/50-global-pgo
```

or:

```bash
emerge -epv @world | awk '/^\[ebuild/ {print $4}' | sed 's/$/ pgo-use-if-available.conf/' > portage/package.env/50-global-pgo
```

## Strict checks

```bash
set -euo pipefail

test -f portage/env/pgo-use-if-available.conf
test -f portage/env/pgo-instrument.conf
test -f portage/env/no-pgo-use.conf
test -f portage/bashrc
test -f portage/package.env/50-global-pgo

bash -n portage/env/pgo-use-if-available.conf
bash -n portage/env/pgo-instrument.conf
bash -n portage/env/no-pgo-use.conf
bash -n portage/bashrc

grep -n 'pgo-use-if-available.conf' portage/package.env/50-global-pgo
grep -n 'PGO_USE_IF_AVAILABLE' portage/env/pgo-use-if-available.conf
grep -n 'if \[\[ ! -r' portage/bashrc
grep -n 'fprofile-use' portage/bashrc
grep -n 'fprofile-generate' portage/bashrc
```

## Acceptance criteria

```text
All packages get PGO use-if-available by default.
Packages without profiles do not fail.
Packages with profiles use them automatically.
A no-pgo-use demotion exists.
A PGO instrumentation profile exists for broad training sweeps.
The conditional PGO logic lives in portage/bashrc, not assignment-only env files.
```

---

# Phase 5 — Add PGO Tooling and Scripts

## Purpose

Make PGO profile collection repeatable.

## Files to add

```text
scripts/pgo/collect-sample-profile.sh
scripts/pgo/make-sample-prof.sh
scripts/pgo/merge-instr-profile.sh
scripts/pgo/package-profile-path.sh
scripts/pgo/list-profiled-packages.sh
docs/pgo-global.md
```

## `scripts/pgo/package-profile-path.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <category> <package>" >&2
    exit 2
fi

category="$1"
package="$2"

echo "/var/tmp/pgo-profiles/${category}/${package}"
```

## `scripts/pgo/collect-sample-profile.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <output-perf.data> <command> [args...]" >&2
    exit 2
fi

out="$1"
shift

mkdir -p "$(dirname "${out}")"

perf record \
    -e cycles:u \
    -j any,u \
    -o "${out}" \
    -- "$@"
```

## `scripts/pgo/make-sample-prof.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 <category> <package> <binary> <perf.data>" >&2
    exit 2
fi

category="$1"
package="$2"
binary="$3"
perf_data="$4"

profile_dir="/var/tmp/pgo-profiles/${category}/${package}"
mkdir -p "${profile_dir}"

llvm-profgen \
    --binary="${binary}" \
    --perfdata="${perf_data}" \
    --output="${profile_dir}/merged.profdata"

echo "wrote ${profile_dir}/merged.profdata"
```

## `scripts/pgo/merge-instr-profile.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <raw-profile-dir> <output.profdata>" >&2
    exit 2
fi

raw_dir="$1"
output="$2"

mapfile -t profiles < <(find "${raw_dir}" -type f \( -name '*.profraw' -o -name 'default_*.profraw' \))

if [[ "${#profiles[@]}" -eq 0 ]]; then
    echo "no raw profiles found in ${raw_dir}" >&2
    exit 1
fi

mkdir -p "$(dirname "${output}")"

llvm-profdata merge -output="${output}" "${profiles[@]}"

echo "wrote ${output}"
```

## `scripts/pgo/list-profiled-packages.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

root="${1:-/var/tmp/pgo-profiles}"

if [[ ! -d "${root}" ]]; then
    echo "no profile root: ${root}" >&2
    exit 0
fi

find "${root}" -path '*/merged.profdata' -type f | sort
```

## Strict checks

```bash
set -euo pipefail

for f in scripts/pgo/*.sh; do
    test -x "$f"
    bash -n "$f"
done

scripts/pgo/package-profile-path.sh app-arch zstd | grep '/var/tmp/pgo-profiles/app-arch/zstd'
scripts/pgo/list-profiled-packages.sh /tmp/nonexistent-profile-root
```

## Acceptance criteria

```text
Scripts are executable.
Scripts parse with bash -n.
Profile paths match pgo-use-if-available.conf.
No script writes outside /var/tmp/pgo-profiles unless explicitly requested.
```

---

# Phase 6 — Add BOLT Infrastructure for Broad Binary Sweeps

## Purpose

Since BOLT cannot be fully expressed as a normal compile flag, the global-first approach is:

```text
make every package BOLT-ready globally
provide scripts to BOLT many package binaries
test optimized binaries without destroying package-manager ownership
promote wrappers or local replacements only after validation
```

## Files to add

```text
scripts/bolt/collect-profile.sh
scripts/bolt/optimize-binary.sh
scripts/bolt/bolt-package-binaries.sh
scripts/bolt/list-package-binaries.sh
docs/bolt-global.md
```

## `scripts/bolt/list-package-binaries.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <category/package>" >&2
    exit 2
fi

pkg="$1"

list_package_paths() {
    if command -v qlist >/dev/null 2>&1; then
        qlist -e "${pkg}"
        return
    fi

    if ! command -v portageq >/dev/null 2>&1; then
        echo "qlist or portageq is required" >&2
        exit 1
    fi

    portageq match / "${pkg}" | while read -r cpv; do
        contents="/var/db/pkg/${cpv}/CONTENTS"
        [[ -f "${contents}" ]] || continue
        awk '$1 == "obj" { print $2 }' "${contents}"
    done
}

list_package_paths | while read -r path; do
    if [[ -f "${path}" && -x "${path}" ]]; then
        file "${path}" | grep -q 'ELF' && echo "${path}" || true
    fi
done
```

## `scripts/bolt/collect-profile.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <output-perf.data> <command> [args...]" >&2
    exit 2
fi

out="$1"
shift

mkdir -p "$(dirname "${out}")"

perf record \
    -e cycles:u \
    -j any,u \
    -o "${out}" \
    -- "$@"
```

## `scripts/bolt/optimize-binary.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 <binary> <perf.data> <output-binary>" >&2
    exit 2
fi

binary="$1"
perf_data="$2"
output="$3"

for tool in perf2bolt llvm-bolt; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "missing required tool: $tool" >&2
        exit 1
    }
done

test -x "${binary}"
test -f "${perf_data}"

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

fdata="${workdir}/profile.fdata"

perf2bolt "${binary}" \
    -p "${perf_data}" \
    -o "${fdata}"

mkdir -p "$(dirname "${output}")"

llvm-bolt "${binary}" \
    -o "${output}" \
    -data="${fdata}" \
    -reorder-blocks=ext-tsp \
    -reorder-functions=hfsort+ \
    -split-functions \
    -split-all-cold \
    -dyno-stats

chmod --reference="${binary}" "${output}"
```

## `scripts/bolt/bolt-package-binaries.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <category/package> <perf.data>" >&2
    exit 2
fi

pkg="$1"
perf_data="$2"

safe_pkg="${pkg//\//_}"
out_root="/opt/bolt-test/${safe_pkg}"

mkdir -p "${out_root}"

while read -r bin; do
    rel="${bin#/}"
    out="${out_root}/${rel}"
    mkdir -p "$(dirname "${out}")"

    echo "BOLT ${bin} -> ${out}"
    scripts/bolt/optimize-binary.sh "${bin}" "${perf_data}" "${out}" || {
        echo "BOLT failed for ${bin}; continuing" >&2
    }
done < <(scripts/bolt/list-package-binaries.sh "${pkg}")
```

## Strict checks

```bash
set -euo pipefail

for f in scripts/bolt/*.sh; do
    test -x "$f"
    bash -n "$f"
done

grep -n 'emit-relocs' portage/make.conf
grep -n 'build-id' portage/make.conf
grep -n 'gline-tables-only' portage/make.conf
```

## Acceptance criteria

```text
Global build flags make BOLT possible.
Scripts can list package ELF executables.
Scripts never overwrite /usr/bin directly.
Optimized binaries are written under /opt/bolt-test by default.
Failures on one binary do not stop a whole package sweep.
```

---

# Phase 7 — Add Global Rust Performance Expansion

## Purpose

Push Rust package optimization further by default.

Current global Rust flags already use native CPU, ThinLTO, one codegen unit, and clang/lld. This phase adds global BOLT/layout and panic-abort experiments.

## File to edit

```text
portage/make.conf
```

## Modify `RUSTFLAGS`

Current form resembles:

```bash
RUSTFLAGS="${RUSTFLAGS} -C embed-bitcode=yes -C lto=thin -C codegen-units=1 -C target-cpu=native -C opt-level=3 -Clinker=clang -Clinker-plugin-lto -Clink-arg=-fuse-ld=lld -Clink-arg=-funified-lto"
```

Change to include:

```text
-C panic=abort
-C debuginfo=1
-Clink-arg=-Wl,--build-id
-Clink-arg=-Wl,--emit-relocs
-Clink-arg=-Wl,--gc-sections
-Clink-arg=-Wl,--icf=safe
```

Suggested final shape:

```bash
RUSTFLAGS="${RUSTFLAGS} -C embed-bitcode=yes -C lto=thin -C codegen-units=1 -C target-cpu=native -C opt-level=3 -C panic=abort -C debuginfo=1 -Clinker=clang -Clinker-plugin-lto -Clink-arg=-fuse-ld=lld -Clink-arg=-funified-lto -Clink-arg=-Wl,--build-id -Clink-arg=-Wl,--emit-relocs -Clink-arg=-Wl,--gc-sections -Clink-arg=-Wl,--icf=safe"
```

## Files to add

```text
portage/env/rust-unwind.conf
portage/env/rust-no-layout-linkflags.conf
portage/env/rust-no-panic-abort-no-layout.conf
```

## `portage/env/rust-unwind.conf`

```bash
############################################################################
# Rust demotion: remove panic=abort.
#
# Use for Rust packages/crates that require unwinding behavior.
############################################################################

RUSTFLAGS="${RUSTFLAGS/ -C panic=abort/}"
```

If Bash substitution proves unreliable in Portage env, use an explicit known-good replacement:

```bash
RUSTFLAGS="-C embed-bitcode=yes -C lto=thin -C codegen-units=1 -C target-cpu=native -C opt-level=3 -C debuginfo=1 -Clinker=clang -Clinker-plugin-lto -Clink-arg=-fuse-ld=lld -Clink-arg=-funified-lto -Clink-arg=-Wl,--build-id -Clink-arg=-Wl,--emit-relocs -Clink-arg=-Wl,--gc-sections -Clink-arg=-Wl,--icf=safe"
```

## `portage/env/rust-no-layout-linkflags.conf`

```bash
############################################################################
# Rust demotion: remove BOLT/section linker layout flags.
#
# Use when Rust package links fail because lld rejects --emit-relocs,
# --gc-sections, or --icf=safe.
############################################################################

RUSTFLAGS="-C embed-bitcode=yes -C lto=thin -C codegen-units=1 -C target-cpu=native -C opt-level=3 -C panic=abort -C debuginfo=1 -Clinker=clang -Clinker-plugin-lto -Clink-arg=-fuse-ld=lld -Clink-arg=-funified-lto"
```

## `portage/env/rust-no-panic-abort-no-layout.conf`

```bash
############################################################################
# Rust broad demotion: remove panic=abort and layout linker flags.
#
# Use only after narrower Rust demotions fail.
############################################################################

RUSTFLAGS="-C embed-bitcode=yes -C lto=thin -C codegen-units=1 -C target-cpu=native -C opt-level=3 -Clinker=clang -Clinker-plugin-lto -Clink-arg=-fuse-ld=lld -Clink-arg=-funified-lto"
```

## Strict checks

```bash
set -euo pipefail

grep -n '^RUSTFLAGS=' portage/make.conf | grep 'panic=abort'
grep -n '^RUSTFLAGS=' portage/make.conf | grep 'debuginfo=1'
grep -n '^RUSTFLAGS=' portage/make.conf | grep 'emit-relocs'
grep -n '^RUSTFLAGS=' portage/make.conf | grep 'gc-sections'
grep -n '^RUSTFLAGS=' portage/make.conf | grep 'icf=safe'

for f in \
  portage/env/rust-unwind.conf \
  portage/env/rust-no-layout-linkflags.conf \
  portage/env/rust-no-panic-abort-no-layout.conf
do
  test -f "$f"
  bash -n "$f"
done
```

## Rust build tests

Pretend:

```bash
emerge -pv dev-util/rustup
emerge -pv dev-lang/rust
emerge -pv dev-util/cargo-c
```

Build smaller Rust package first:

```bash
emerge -1av dev-util/rustup
```

Then a larger one only after the small package works.

## Acceptance criteria

```text
RUSTFLAGS globally include the new layout and panic flags.
Rust demotion env files exist.
At least one small Rust package builds or receives a documented demotion.
```

---

# Phase 8 — Add Allocator Global Runtime Experiment

## Purpose

Allocator performance is runtime behavior, not a normal compile flag. The global-first approach is to provide a reversible user-session-wide allocator preload experiment.

Do not start by writing `/etc/ld.so.preload`. That can break login, sudo, Portage, and recovery. Start with user-session/global-desktop scope.

## Files to add

```text
scripts/allocators/enable-user-allocator.sh
scripts/allocators/disable-user-allocator.sh
scripts/allocators/run-with-system-malloc.sh
scripts/allocators/run-with-jemalloc.sh
scripts/allocators/run-with-mimalloc.sh
scripts/allocators/run-with-tcmalloc.sh
docs/allocator-global.md
```

## `scripts/allocators/enable-user-allocator.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <jemalloc|mimalloc|tcmalloc>" >&2
    exit 2
fi

allocator="$1"

case "${allocator}" in
    jemalloc) lib="${JEMALLOC_LIB:-/usr/lib64/libjemalloc.so}" ;;
    mimalloc) lib="${MIMALLOC_LIB:-/usr/lib64/libmimalloc.so}" ;;
    tcmalloc) lib="${TCMALLOC_LIB:-/usr/lib64/libtcmalloc.so}" ;;
    *) echo "unknown allocator: ${allocator}" >&2; exit 2 ;;
esac

if [[ ! -e "${lib}" ]]; then
    echo "allocator library not found: ${lib}" >&2
    exit 1
fi

mkdir -p "${HOME}/.config/environment.d"

cat > "${HOME}/.config/environment.d/99-global-allocator.conf" <<EOF
LD_PRELOAD=${lib}
EOF

echo "enabled user-session allocator preload: ${lib}"
echo "log out and back in for environment.d consumers to inherit it"
```

## `scripts/allocators/disable-user-allocator.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

rm -f "${HOME}/.config/environment.d/99-global-allocator.conf"

echo "disabled user-session allocator preload"
echo "log out and back in to clear it from new sessions"
```

## `scripts/allocators/run-with-system-malloc.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

exec env -u LD_PRELOAD "$@"
```

## Per-command wrappers

Create `run-with-jemalloc.sh`, `run-with-mimalloc.sh`, and `run-with-tcmalloc.sh` for demotion/isolated testing.

## Strict checks

```bash
set -euo pipefail

for f in scripts/allocators/*.sh; do
    test -x "$f"
    bash -n "$f"
done

scripts/allocators/run-with-system-malloc.sh env | grep LD_PRELOAD && exit 1 || true
```

## Acceptance criteria

```text
There is a global user-session allocator toggle.
There is a clean disable script.
There is an app-level system malloc demotion wrapper.
No script edits /etc/ld.so.preload.
```

---

# Phase 9 — Add Benchmark Harness With Strict Before/After Requirements

## Purpose

The repo should collect evidence, but evidence does not decide whether to try global flags. It decides whether to keep them or demote packages.

## Files to add

```text
bench/README.md
bench/run-command-benchmark.sh
bench/perf-stat.sh
bench/size-report.sh
bench/compare-two-commands.sh
bench/results/.gitkeep
```

## `bench/compare-two-commands.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
    echo "usage: $0 <name> <iterations> <baseline-command> -- <test-command>" >&2
    exit 2
fi

name="$1"
iterations="$2"
shift 2

baseline=()
testcmd=()
side="baseline"

for arg in "$@"; do
    if [[ "$arg" == "--" ]]; then
        side="test"
        continue
    fi
    if [[ "$side" == "baseline" ]]; then
        baseline+=("$arg")
    else
        testcmd+=("$arg")
    fi
done

if [[ "${#baseline[@]}" -eq 0 || "${#testcmd[@]}" -eq 0 ]]; then
    echo "baseline and test commands are required" >&2
    exit 2
fi

mkdir -p bench/results
out="bench/results/${name}-compare-$(date +%Y%m%d-%H%M%S).txt"

{
    echo "comparison: ${name}"
    echo "iterations: ${iterations}"
    echo "date: $(date -Is)"
    echo "baseline: ${baseline[*]}"
    echo "test: ${testcmd[*]}"
    echo

    for i in $(seq 1 "${iterations}"); do
        echo "iteration ${i} baseline"
        /usr/bin/time -f 'real=%e user=%U sys=%S maxrss=%M' "${baseline[@]}"
        echo "iteration ${i} test"
        /usr/bin/time -f 'real=%e user=%U sys=%S maxrss=%M' "${testcmd[@]}"
        echo
    done
} | tee "${out}"

echo "wrote ${out}"
```

## Strict benchmark rules

Add to `bench/README.md`:

```text
Benchmarking does not gate whether a global experiment is tried.
Benchmarking gates whether a package stays global or gets demoted.

Minimum strict benchmark requirements:
    5 iterations for quick CLI tools
    10 iterations for noisy frame-time tests
    A/B/A/B order, not all baseline then all test
    record package version
    record active env profiles
    record CPU governor
    record kernel version
    record GPU driver/Mesa version for gaming tests
    record whether system was warm or cold
```

## Strict checks

```bash
set -euo pipefail

test -x bench/run-command-benchmark.sh
test -x bench/perf-stat.sh
test -x bench/size-report.sh
test -x bench/compare-two-commands.sh
test -f bench/README.md
test -f bench/results/.gitkeep

grep -n 'Benchmarking gates whether a package stays global' bench/README.md
grep -n 'A/B/A/B' bench/README.md
```

---

# Phase 10 — Update `portage/env/README`

## Purpose

The fallback ladder must document the new global-first axes.

## File to edit

```text
portage/env/README
```

## Add after default policy

```text
Global performance expansion policy:
    This repository applies new performance experiments globally first.

    BOLT readiness metadata, sample-PGO mapping metadata, section splitting,
    linker GC/ICF, hidden visibility, optimization remarks, Rust panic=abort,
    and Rust layout linker flags are default pressure axes.

    Packages that fail are demoted narrowly. Do not remove these axes globally
    merely because they are risky. They are now part of the experiment.

New global axes:
    BOLT/profile-layout readiness
        -gline-tables-only
        -Wl,--emit-relocs
        -Wl,--build-id

    Sample-PGO mapping readiness
        -fdebug-info-for-profiling
        -funique-internal-linkage-names
        -fpseudo-probe-for-profiling

    Code-size / i-cache pressure
        -ffunction-sections
        -fdata-sections
        -Wl,--gc-sections
        -Wl,--icf=safe

    Visibility/internalization pressure
        -fvisibility=hidden
        -fvisibility-inlines-hidden for C++

    Optimization remarks
        -fsave-optimization-record=yaml
        -fdiagnostics-show-hotness

    Rust expansion
        -C panic=abort
        -C debuginfo=1
        -Clink-arg=-Wl,--emit-relocs
        -Clink-arg=-Wl,--build-id
        -Clink-arg=-Wl,--gc-sections
        -Clink-arg=-Wl,--icf=safe
```

## Add to fallback ladder

```text
New global-performance demotions, least to most broad:
    no-opt-remarks.conf
        Removes only optimization record output. Try first when packages
        produce excessive .opt.yaml noise, install unexpected YAML files, or
        fail because build systems scan generated files.

    no-profile-mapping.conf
        Removes sample-PGO mapping metadata while keeping BOLT readiness,
        section splitting, visibility, LTO, Polly, and aggressive defaults.

    no-bolt-ready.conf
        Removes BOLT readiness compile/link metadata while keeping the rest
        of the new global performance expansion.

    no-hidden-visibility.conf
        Removes hidden visibility. Use for shared libraries, plugin hosts,
        language runtimes, Wine, Qt/GTK stack libraries, and packages with
        public ABI/export assumptions.

    no-gc-icf.conf
        Removes linker section GC and identical code folding.

    no-section-splitting.conf
        Removes -ffunction-sections and -fdata-sections.

    no-pgo-use.conf
        Prevents automatic use of an available package PGO profile.

    no-global-perf-extras.conf
        Removes every new global performance-expansion axis while keeping
        the older aggressive baseline. Use only after narrower demotions fail.
```

## Strict checks

```bash
grep -n 'Global performance expansion policy' portage/env/README
grep -n 'no-opt-remarks.conf' portage/env/README
grep -n 'no-profile-mapping.conf' portage/env/README
grep -n 'no-bolt-ready.conf' portage/env/README
grep -n 'no-hidden-visibility.conf' portage/env/README
grep -n 'no-gc-icf.conf' portage/env/README
grep -n 'no-section-splitting.conf' portage/env/README
grep -n 'no-global-perf-extras.conf' portage/env/README
```

---

# Phase 11 — Update Maintenance Checklist

## File to edit

```text
docs/maintenance-checklist.md
```

## Add section

```markdown
## After Global Performance Expansion Changes

- [ ] Confirm new performance flags are global in `make.conf`.
- [ ] Confirm every new global axis has a demotion env file.
- [ ] Confirm no demotion env file overwrites `FEATURES` from scratch.
- [ ] Run `bash -n` on every `portage/env/*.conf`.
- [ ] Run `emerge --info` and verify CFLAGS/CXXFLAGS/LDFLAGS/RUSTFLAGS contain the expected global expansion flags.
- [ ] Run pretend merges for the first-wave test set:
      - `app-arch/zstd`
      - `app-arch/xz-utils`
      - `sys-apps/coreutils`
      - `dev-libs/libffi`
      - `dev-libs/openssl`
      - `gui-wm/gamescope`
- [ ] Build the small first-wave test set:
      - `app-arch/zstd`
      - `app-arch/xz-utils`
      - `sys-apps/coreutils`
- [ ] If a package fails, create the narrowest package.env demotion.
- [ ] Record the exact failure reason in the package.env comment.
- [ ] Do not remove global flags from make.conf unless the entire system cannot bootstrap.
```

## Add section

```markdown
## After PGO Profile Changes

- [ ] Confirm `pgo-use-if-available.conf` is applied globally.
- [ ] Confirm packages without profiles do not fail.
- [ ] Confirm packages with `/var/tmp/pgo-profiles/<category>/<pn>/merged.profdata` use `-fprofile-use`.
- [ ] Check stale profile warnings.
- [ ] Demote stale profiles with `no-pgo-use.conf`, not by disabling global PGO use.
```

## Add section

```markdown
## After BOLT Sweeps

- [ ] Confirm binaries were built with `--emit-relocs`.
- [ ] Confirm binaries were built with `--build-id`.
- [ ] Confirm BOLT outputs go to `/opt/bolt-test`, not directly over `/usr`.
- [ ] Compare BOLT binary against package-managed binary.
- [ ] Keep original binpkg rollback available.
```

## Strict checks

```bash
grep -n 'After Global Performance Expansion Changes' docs/maintenance-checklist.md
grep -n 'After PGO Profile Changes' docs/maintenance-checklist.md
grep -n 'After BOLT Sweeps' docs/maintenance-checklist.md
```

---

# Phase 12 — Global Build Validation Matrix

## Purpose

Validate the new global default across package classes.

## First-wave packages

```text
app-arch/zstd
app-arch/xz-utils
sys-apps/coreutils
dev-libs/libffi
dev-libs/openssl
dev-libs/libsodium
dev-libs/nettle
dev-libs/libxml2
sys-libs/zlib
```

## C++ packages

```text
dev-libs/boost
media-libs/libjxl
gui-wm/gamescope
games-util/mangohud
```

## Runtime/interpreter packages

```text
dev-lang/python
dev-lang/ruby
dev-lang/perl
dev-lang/lua
```

## Toolchain packages

```text
llvm-core/llvm
llvm-core/clang
llvm-runtimes/openmp
sys-devel/gcc
dev-lang/rust
```

## Gaming/media packages

```text
app-emulation/wine-staging
gui-wm/gamescope
games-util/mangohud
media-video/ffmpeg
media-video/obs-studio
```

## ROCm/GPU packages

```text
dev-util/hip
dev-libs/rocr-runtime
dev-libs/rocm-comgr
dev-libs/rocm-device-libs
sys-apps/hwloc
```

## Strict pretend pass

```bash
while read -r pkg; do
    echo "== pretend ${pkg} =="
    emerge -pv "${pkg}" || exit 1
done <<'EOF'
app-arch/zstd
app-arch/xz-utils
sys-apps/coreutils
dev-libs/libffi
dev-libs/openssl
dev-libs/libsodium
dev-libs/nettle
dev-libs/libxml2
sys-libs/zlib
gui-wm/gamescope
games-util/mangohud
media-video/ffmpeg
sys-apps/hwloc
EOF
```

## Strict build pass

Build small packages first:

```bash
emerge -1av app-arch/zstd app-arch/xz-utils sys-apps/coreutils
```

Then expand only after small package pass:

```bash
emerge -1av dev-libs/libffi dev-libs/openssl dev-libs/libsodium dev-libs/nettle
```

Then C++/gaming:

```bash
emerge -1av gui-wm/gamescope games-util/mangohud
```

## Implementation notes

```text
2026-07-06: strict pretend matrix passed for the listed validation set.
2026-07-06: small build wave passed for app-arch/zstd, app-arch/xz-utils,
sys-apps/coreutils.
2026-07-06: expanded library build wave passed for dev-libs/libffi,
dev-libs/openssl, dev-libs/libsodium, dev-libs/nettle.
2026-07-06: C++/gaming build wave passed for gui-wm/gamescope,
games-util/mangohud.
```

## Failure classification template

When a package fails, add a package.env comment using this template:

```text
# <date>: demoted because <exact failure>.
# Failed axis: <opt-remarks|profile-mapping|bolt-ready|hidden-visibility|gc-icf|section-splitting|pgo-use|other>.
# Error signature: <one-line compiler/linker/runtime error>.
# Retry after: <LLVM bump|package bump|libc++ bump|lld bump|unknown>.
category/package demotion-profile.conf
```

## Acceptance criteria

```text
Every failure has a narrow demotion.
No unexplained broad demotions are added.
No global flag is removed from make.conf because one package failed.
```

---

# Phase 13 — Documentation for Global-First Performance Lab

## Files to add

```text
docs/global-performance-expansion.md
docs/pgo-global.md
docs/bolt-global.md
docs/allocator-global.md
bench/README.md
```

## `docs/global-performance-expansion.md` required content

```markdown
# Global Performance Expansion

This repo applies performance experiments globally first.

The default is expected to break packages. Breakage is not failure of the
repo. Breakage is data.

## Policy

1. Add optimization globally.
2. Build as many packages as possible.
3. Demote only packages that prove they need it.
4. Demote the narrowest failing axis.
5. Re-test demoted packages after toolchain updates.

## Current global expansion axes

- BOLT readiness metadata
- sample-PGO profile mapping metadata
- section splitting
- linker section GC
- linker safe ICF
- hidden visibility
- optimization remarks
- Rust panic=abort
- Rust linker layout flags
- PGO use-if-available

## Never do this casually

Do not remove a global axis because a single package failed.

Instead, add a package.env demotion.
```

## Strict checks

```bash
test -f docs/global-performance-expansion.md
grep -n 'global' docs/global-performance-expansion.md
grep -n 'demote' docs/global-performance-expansion.md
grep -n 'Breakage is data' docs/global-performance-expansion.md
```

## Implementation notes

```text
2026-07-06: docs/global-performance-expansion.md added and strict checks
passed.
```

---

# Phase 14 — Final Repo-Wide Validation

Run all final checks before calling the plan implemented.

## 14.1 File existence

```bash
set -euo pipefail

for f in \
  portage/env/no-bolt-ready.conf \
  portage/env/no-profile-mapping.conf \
  portage/env/no-section-splitting.conf \
  portage/env/no-gc-icf.conf \
  portage/env/no-hidden-visibility.conf \
  portage/env/no-opt-remarks.conf \
  portage/env/no-global-perf-extras.conf \
  portage/env/pgo-use-if-available.conf \
  portage/env/pgo-instrument.conf \
  portage/env/no-pgo-use.conf \
  portage/env/rust-unwind.conf \
  portage/env/rust-no-layout-linkflags.conf \
  portage/env/rust-no-panic-abort-no-layout.conf
do
  test -f "$f"
done
```

## 14.2 Shell parse

```bash
for f in portage/env/*.conf scripts/**/*.sh bench/*.sh; do
    [[ -f "$f" ]] || continue
    case "$f" in
        *.conf|*.sh)
            bash -n "$f"
            ;;
    esac
done
```

## 14.3 Global flag presence

```bash
grep -n '^GLOBAL_PERF_FLAGS=' portage/make.conf
grep -n '^COMMON_FLAGS=' portage/make.conf | grep 'GLOBAL_PERF_FLAGS'
grep -n '^CXXFLAGS=' portage/make.conf | grep 'CXX_VISIBILITY_FLAGS'
grep -n '^LD_OPT_FLAGS=' portage/make.conf | grep 'BOLT_READY_LD_FLAGS'
grep -n '^LD_OPT_FLAGS=' portage/make.conf | grep 'SECTION_LD_FLAGS'
grep -n '^RUSTFLAGS=' portage/make.conf | grep 'panic=abort'
```

## 14.4 Demotion coverage

```bash
for axis in \
  no-bolt-ready \
  no-profile-mapping \
  no-section-splitting \
  no-gc-icf \
  no-hidden-visibility \
  no-opt-remarks \
  no-global-perf-extras \
  no-pgo-use
do
    test -f "portage/env/${axis}.conf"
done
```

## 14.5 FEATURES inheritance

```bash
grep -R '^FEATURES=' -n portage/env | while read -r line; do
    echo "$line" | grep '\${FEATURES}' >/dev/null || {
        echo "BAD FEATURES override: $line" >&2
        exit 1
    }
done
```

## 14.6 No stale config-protect residue

```bash
find portage -name '._cfg*' -print | tee /tmp/cfg-residue.txt
test ! -s /tmp/cfg-residue.txt
```

## 14.7 Documentation

```bash
grep -n 'Global performance expansion policy' portage/env/README
grep -n 'After Global Performance Expansion Changes' docs/maintenance-checklist.md
test -f docs/global-performance-expansion.md
```

## 14.8 Live environment

After deploying to `/etc/portage`, run:

```bash
emerge --info | grep -E '^(CFLAGS|CXXFLAGS|FCFLAGS|FFLAGS|LDFLAGS|RUSTFLAGS|FEATURES|EMERGE_DEFAULT_OPTS|MAKEOPTS)='
```

Required live properties:

```text
CFLAGS has BOLT_READY_FLAGS
CFLAGS has PROFILE_MAPPING_FLAGS
CFLAGS has SECTION_FLAGS
CFLAGS has VISIBILITY_FLAGS
CFLAGS has OPT_REMARK_FLAGS
CXXFLAGS has CXX_VISIBILITY_FLAGS
LDFLAGS has BOLT_READY_LD_FLAGS
LDFLAGS has SECTION_LD_FLAGS
RUSTFLAGS has panic=abort
RUSTFLAGS has emit-relocs/build-id/gc-sections/icf
FEATURES has splitdebug/buildpkg/binpkg-multi-instance/collision-protect
```

## Implementation notes

```text
2026-07-06: final repo-wide validation passed, including file existence,
shell parsing, global flag presence, demotion coverage, FEATURES inheritance,
config-residue check, documentation checks, and live emerge --info property
checks.
```

---

# Recommended Implementation Order

```text
1. Rewrite the plan text to global-first.
2. Add new global flag groups to make.conf.
3. Add global linker layout flags to make.conf.
4. Add splitdebug to FEATURES.
5. Add new demotion profiles.
6. Add global PGO use-if-available.
7. Add PGO scripts.
8. Add BOLT scripts.
9. Add global Rust flags and Rust demotion profiles.
10. Add allocator user-session global experiment scripts.
11. Add benchmark harness.
12. Update portage/env/README.
13. Update docs/maintenance-checklist.md.
14. Add docs/global-performance-expansion.md.
15. Run final repo-wide validation.
16. Run live emerge --info validation.
17. Run first-wave pretend merges.
18. Run first-wave builds.
19. Add package.env demotions only for packages that fail.
```

---

# Recommended Commit Grouping

## Commit 1

```text
globalize performance experiment policy

Rewrite the plan and documentation around the repository's real policy:
new performance axes are added globally first, then packages are demoted
only after failures or regressions.
```

## Commit 2

```text
add global performance expansion flags

Add BOLT/profile mapping metadata, section splitting, hidden visibility,
optimization remarks, linker emit-relocs/build-id/gc-sections/icf, and
splitdebug to the default aggressive tier.
```

## Commit 3

```text
add demotions for global performance axes

Add narrow fallback env profiles for BOLT readiness, profile mapping,
section splitting, linker GC/ICF, hidden visibility, optimization remarks,
PGO use, and the full global performance expansion layer.
```

## Commit 4

```text
add global PGO use-if-available

Apply PGO profile use globally when package profiles exist, add
instrumentation profile support, and provide scripts for collecting and
merging profiles.
```

## Commit 5

```text
add BOLT package sweep tooling

Add scripts to collect BOLT profiles, list package binaries, and produce
BOLT-optimized copies under /opt/bolt-test without overwriting package-owned
files.
```

## Commit 6

```text
expand Rust default optimization

Add global Rust panic=abort, debuginfo, and linker layout flags with narrow
Rust demotion profiles for packages that require unwinding or cannot link
with the layout flags.
```

## Commit 7

```text
add allocator and benchmark infrastructure

Add user-session allocator preload toggles, per-command allocator wrappers,
and benchmark helpers for timing, perf counters, binary size, and A/B
comparison.
```

## Commit 8

```text
document global-first performance lab

Update env README, maintenance checklist, and performance docs with strict
validation rules for the global-first optimization model.
```

---

# Final Desired State

After implementation, the repo should satisfy all of these:

```text
1. All new performance experiments are global by default when technically possible.
2. BOLT readiness metadata is global.
3. Sample-PGO mapping metadata is global.
4. Section splitting is global.
5. Linker GC and safe ICF are global.
6. Hidden visibility is global.
7. Optimization remarks are global.
8. Rust panic=abort and linker layout flags are global.
9. PGO use is global for any package with an available merged profile.
10. BOLT tooling can sweep package executables broadly.
11. Allocator testing can be enabled for the whole user session.
12. Every new global axis has a narrow demotion profile.
13. Demotions are package-specific and evidence-based.
14. No new global axis is removed because one package fails.
15. Strict validation commands exist for every step.
16. Documentation clearly says this is a global-first optimization stress-test repo.
```

The finished repo should be a serious stress-test of how far Gentoo package optimization can be pushed globally, while still remaining debuggable because every risky axis has a narrow fallback.

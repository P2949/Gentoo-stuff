# Exhaustive System-Wide PGO and BOLT Implementation Plan for Gentoo

## Progress summary

- **Project state:** active; Phase 0 recovery and evidence foundation in progress.
- **Dedicated branch:** `feat/system-wide-pgo-bolt`.
- **Starting repository commit:** `c04773564da826abdeea3660568701d040cc89d0`.
- **Optimization generation:** not established; inventory is not yet frozen.
- **Strict coverage totals:** pending the Phase 3 live inventory; no zero-coverage claim has been made.
- **Last plan review:** 2026-07-10; the complete 1,745-line source plan was read before execution began.
- **Safety gate:** no optimization rebuild may start until the Phase 0 rollback path is captured and restoration-tested.

## Document purpose

This document is an execution plan for an LLM coding/system-administration agent working against the `P2949/Gentoo-stuff` repository and the live Gentoo installation that uses it.

The agent must implement an exhaustive, package-accounted optimization pipeline. By the end of the plan:

1. Every package installed in `/var/db/pkg` must have been inventoried, classified, rebuilt from source during the project, and assigned a final optimization state.
2. Every package that produces code eligible for a supported PGO mechanism must have been rebuilt using that mechanism and a valid representative profile.
3. Every installed 64-bit x86 ELF executable or shared object that is technically safe and valid for BOLT must have been BOLT-optimized from an exact PGO-built input before Portage stripping and deployment.
4. Every artifact that cannot physically receive PGO or BOLT must have a machine-verifiable exclusion reason. Nothing may be silently skipped.
5. The final system rebuild must run with PGO-use and BOLT deployment enabled together, so the files installed at completion are the final PGO+BOLT variants rather than temporary training binaries.
6. The active kernel must be handled through its supported kernel-specific profile optimization lane rather than through generic userspace flags.
7. The final strict coverage verifier must report zero unclassified, pending, stale, mismatched, or failed eligible packages and artifacts.

This document must remain in the repository at:

```text
plans/system-wide-pgo-bolt.md
```

The implementing agent must update the checkboxes, status tables, decisions, discovered exceptions, commands, and results in this document after every implementation step. After each completed step, the agent must re-read this document before deciding the next action.

---

# 1. Non-negotiable interpretation of “all packages”

A literal demand that every package be “compiled with both PGO and BOLT” is technically impossible because many installed packages contain only scripts, headers, fonts, firmware, configuration, data, documentation, JVM/Python bytecode, static metadata, or architecture-specific objects that BOLT does not support. BOLT is a post-link optimizer for supported ELF machine-code objects; it cannot optimize shell scripts, Python source, Java bytecode, firmware blobs, static archives as archives, 32-bit x86 ELF, GPU code objects, eBPF objects, kernel modules through the normal userspace path, or packages that install no machine code.

The plan therefore defines successful full-system coverage as follows:

- **All installed packages are included in the project.**
- **All eligible code receives the strongest valid PGO mechanism.**
- **All BOLT-eligible deployed 64-bit x86 ELF executables and DSOs receive BOLT.**
- **All non-eligible packages and artifacts are explicitly proven non-applicable.**
- **There are no discretionary “not worth optimizing” exclusions.** Low expected benefit is not an exclusion reason.
- **A package may be excluded only because the optimization is technically inapplicable, impossible to profile safely, unsupported by its compiler/toolchain, or demonstrably breaks correctness after package-specific remediation has been attempted.**

The final report must distinguish:

```text
optimized
not-applicable
unsupported-by-upstream-toolchain
unsafe-to-profile
correctness-failure-after-remediation
binary-only-no-rebuild-source
```

Only `optimized` and an evidence-backed terminal exclusion are acceptable final states. `pending`, `unknown`, `not-tested`, `skipped`, and `failed-without-analysis` are forbidden at completion.

---

# 2. Agent operating contract

The implementing agent must follow these rules throughout the project.

## 2.1 Work from evidence

- Inspect the live system; do not assume the uploaded repository reflects every currently deployed file.
- Treat `/var/db/pkg`, live `/etc/portage`, active compiler versions, active kernel, current package graph, and actual installed ELF files as the source of truth.
- Record every command and important result in the project state directory.
- Do not infer that a package used PGO merely because a flag was configured. Verify build logs and profiles.
- Do not infer that a file was BOLTed merely because the package was marked BOLT-enabled. Verify `.note.bolt_info` in the installed ELF.

## 2.2 Never silently weaken the goal

- Do not quietly convert “all packages” into a small hand-picked package list.
- Do not skip libraries. Instrumented libraries must be trained through reverse dependencies or test workloads.
- Do not skip rarely used executables solely because expected performance benefit is small.
- Do not declare success while any eligible package or ELF remains unprofiled.
- Do not apply empty, unrelated, stale, or mismatched profiles merely to make coverage numbers appear complete.

## 2.3 Protect system recoverability

- Never remove the last known-good binary package for a critical package.
- Never replace the only bootable kernel.
- Never apply a BOLT output when build ID or `.text` identity checks fail.
- Never reuse PGO raw data across incompatible compiler profile formats.
- Never apply Clang profile flags to GCC or Rust builds by accident.
- Never apply generic userspace PGO flags to kernel or kernel-module builds.
- Stop the affected package lane on correctness failure, restore the known-good binpkg, document the failure, and remediate before continuing.

## 2.4 Preserve reproducibility

Every generated profile and BOLT output must be associated with:

- CPV and repository;
- SLOT and SUBSLOT;
- ABI and CHOST;
- compiler executable and complete version;
- compiler profile format family;
- ebuild SHA-256;
- USE flags;
- relevant Portage environment flags;
- source/distfile identity when available;
- input ELF GNU build ID;
- input ELF `.text` SHA-256;
- workload set version;
- profile generation ID;
- date and host identity.

## 2.5 Keep repository changes reviewable

- Work on a dedicated Git branch.
- Commit at the end of every major phase.
- Keep generated machine state, raw profiles, perf data, cached binaries, and logs out of Git.
- Keep policies, schemas, scripts, workload definitions, documentation, and stable package decisions in Git.
- Run `shellcheck` on shell scripts and syntax/format validation on JSON, YAML, and Python before committing.

---

# 3. Existing repository defects that must be corrected first

The current repository contains a useful starting framework, but the existing PGO/BOLT mechanism must not be used for the system-wide rebuild until these defects are fixed.

## 3.1 Remove mixed instrumentation/sample-profile handling

The current sample-profile converter writes an `llvm-profgen` sample profile to a file named `merged.profdata`, while the global hook consumes that file with `-fprofile-use`. Instrumentation and sample profiles are different formats and must use different compiler options.

Required replacement:

```text
Clang IR instrumentation:
    raw/*.profraw
    merged.profdata
    -fprofile-generate
    -fprofile-use

Clang sample PGO:
    perf/*.data
    sample.prof
    -fprofile-sample-use
    -fsample-profile-use-profi
```

The new implementation must use distinct directories, file names, states, validators, and flags.

## 3.2 Remove compiler-family leakage

Delete the global rule that enables LLVM `-fprofile-use` for every package based only on a file existing. The replacement must choose a backend after detecting the package’s actual compiler lane:

```text
clang-ir
gcc-gcov
rust-llvm-ir
go-pprof
ebuild-native
clang-sample
kernel-autofdo
not-applicable
```

A Clang indexed profile must never be passed to GCC. A Rust profile must be tied to the exact `rustc`/LLVM profile format. Generic C/C++ flags must not be added to `FCFLAGS` or `FFLAGS` unless a separate tested Fortran lane is implemented.

## 3.3 Replace the current weak profile key

The current path based only on `${CATEGORY}/${PN}` must be removed. It allows different versions, slots, ABIs, compilers, and configurations to collide.

Use a generation-aware fingerprint described in section 7.

## 3.4 Remove Clang-incompatible profile flags

Do not use `-fprofile-correction` in the Clang path. Keep GCC correction behavior only inside the GCC lane.

## 3.5 Move BOLT from installed stripped files to cached exact inputs and pre-strip deployment

The current scripts process `/usr/bin/...` after Portage installation and write test copies under `/opt/bolt-test`. That is acceptable only as an early experiment. The final pipeline must:

1. capture the exact unstripped PGO-built ELF from `${ED}` during `post_src_install`;
2. train the corresponding installed PGO binary;
3. run `perf2bolt` and `llvm-bolt` against the cached unstripped exact input;
4. verify input build ID and `.text` hash;
5. during the final package rebuild, replace the matching file inside `${ED}` with the prepared BOLT output;
6. allow Portage to perform its normal splitdebug/strip/binpkg/deployment handling afterward.

## 3.6 Make BOLT readiness stage-aware

The current global flags add line tables, sample mapping metadata, relocation sections, optimization records, and section splitting to every package at all times. Refactor them into independent stage profiles:

```text
profile-map-ready.conf
bolt-capture-ready.conf
bolt-gcc-ready.conf
pgo-*-generate.conf
pgo-*-use.conf
bolt-deploy.conf
```

Only the stages and packages that need a flag should receive it. The final requirement remains exhaustive, but unrelated profile mechanisms must not be conflated.

---

# 4. Required repository layout

Create this structure. Adapt naming only when there is a strong repository convention requiring it.

```text
plans/
└── system-wide-pgo-bolt.md

optimization/
├── README.md
├── policy.yaml
├── exclusions.yaml
├── package-overrides.yaml
├── schema/
│   ├── package-state.schema.json
│   ├── artifact-state.schema.json
│   └── workload.schema.json
├── workloads/
│   ├── common/
│   ├── app-arch/
│   ├── app-shells/
│   ├── dev-lang/
│   ├── dev-libs/
│   ├── media-libs/
│   ├── media-video/
│   ├── net-misc/
│   ├── sys-apps/
│   └── ...
└── fixtures/

scripts/optimization/
├── lib/
│   ├── common.sh
│   ├── portage.sh
│   ├── elf.sh
│   ├── profile.sh
│   └── state.py
├── inventory/
│   ├── inventory-installed.sh
│   ├── inventory-artifacts.sh
│   ├── detect-build-backend.py
│   ├── build-reverse-deps.py
│   └── generate-portage-sets.py
├── pgo/
│   ├── profile-key.sh
│   ├── prepare-generation.sh
│   ├── merge-clang-ir.sh
│   ├── merge-gcc.sh
│   ├── merge-rust.sh
│   ├── validate-profile.py
│   ├── generate-package-env.py
│   └── report-profile-coverage.py
├── train/
│   ├── run-workload.sh
│   ├── run-all-workloads.sh
│   ├── run-system-session.sh
│   ├── train-library-closures.py
│   └── validate-workloads.py
├── bolt/
│   ├── capture-input.sh
│   ├── inventory-candidates.sh
│   ├── collect-system-profile.sh
│   ├── convert-profile.sh
│   ├── merge-fdata.sh
│   ├── optimize-candidate.sh
│   ├── validate-output.sh
│   └── generate-deployment-manifest.py
└── verify/
    ├── verify-package-pgo.py
    ├── verify-installed-bolt.py
    ├── verify-runtime.sh
    └── verify-coverage.py

portage/env/optimization/
├── pgo-clang-ir-generate.conf
├── pgo-clang-ir-use.conf
├── pgo-clang-sample-use.conf
├── pgo-gcc-generate.conf
├── pgo-gcc-use.conf
├── pgo-rust-generate.conf
├── pgo-rust-use.conf
├── pgo-go-use.conf
├── bolt-capture-ready.conf
├── bolt-gcc-ready.conf
├── bolt-deploy.conf
└── optimization-off.conf

portage/package.env/
├── 50-pgo-generated
├── 51-bolt-capture-generated
└── 52-bolt-deploy-generated
```

Generated `package.env` files may be checked into Git only if they contain stable reviewed policy. Machine-specific CPV fingerprints and temporary generation IDs belong under `/var/lib/gentoo-optimization`, not Git.

---

# 5. Live state directory layout

Create persistent and temporary state with explicit ownership and permissions.

```text
/var/lib/gentoo-optimization/
├── inventory/
├── state/
├── reports/
├── deployment/
├── generations/
└── locks/

/var/cache/gentoo-optimization/
├── pgo/
│   ├── clang-ir/
│   ├── clang-sample/
│   ├── gcc/
│   ├── rust/
│   ├── go/
│   └── ebuild-native/
├── bolt/
│   ├── inputs/
│   ├── perf/
│   ├── fdata/
│   └── outputs/
├── build-logs/
└── binpkgs/

/var/tmp/gentoo-optimization/
├── pgo-raw/
├── perf/
├── workloads/
└── staging/
```

Requirements:

- `/var/lib/gentoo-optimization` and `/var/cache/gentoo-optimization` must be root-owned and not generally writable.
- The active Clang/Rust runtime raw-profile generation directory may need mode `01777` so desktop applications and service users can emit unique `%p` profile files. Limit this permission to the exact generation-specific raw directory and remove the broad write permission after training.
- Add only the required paths to Portage sandbox configuration.
- Never make the complete profile cache world-writable.
- Store a `generation.json` file in every generation directory.

---

# 6. Package and artifact state model

Create one package state file per installed CPV and one artifact record per owned machine-code file.

Minimum package record:

```json
{
  "cpv": "category/package-version-rN",
  "cp": "category/package",
  "repository": "gentoo",
  "slot": "0",
  "subslot": "0",
  "abis": ["amd64", "x86"],
  "ebuild_sha256": "...",
  "use_flags": ["..."],
  "build_backend": "clang-ir",
  "compiler": {
    "path": "/usr/bin/clang",
    "version": "...",
    "profile_format": "llvm-ir-v..."
  },
  "fingerprint": "sha256:...",
  "pgo": {
    "eligibility": "eligible",
    "mode": "clang-ir",
    "generation_id": "...",
    "profile_path": "...",
    "profile_valid": true,
    "build_verified": true,
    "status": "optimized"
  },
  "bolt": {
    "candidate_count": 4,
    "optimized_count": 4,
    "excluded_count": 0,
    "status": "optimized"
  },
  "final_status": "optimized",
  "notes": []
}
```

Minimum artifact record:

```json
{
  "owner_cpv": "category/package-version-rN",
  "installed_path": "/usr/bin/example",
  "canonical_path": "/usr/libexec/example-real",
  "elf_class": 64,
  "elf_type": "DYN",
  "machine": "Advanced Micro Devices X86-64",
  "abi": "amd64",
  "build_id": "...",
  "text_sha256": "...",
  "has_symbols": true,
  "has_text_relocations": true,
  "setuid": false,
  "file_capabilities": [],
  "bolt_eligibility": "eligible",
  "bolt_profile_samples": 12345,
  "bolt_profile_stale_percent": 0.0,
  "bolt_output_path": "...",
  "installed_has_bolt_note": true,
  "status": "optimized"
}
```

Every terminal exclusion must contain:

```json
{
  "reason_code": "not-machine-code",
  "evidence": ["file output...", "readelf output..."],
  "reviewed": true
}
```

---

# 7. Build identity and generation keys

## 7.1 Create a stable package fingerprint

Implement `scripts/optimization/pgo/profile-key.sh` or an equivalent Python tool. Hash a canonical, sorted representation of:

```text
CATEGORY
PF
SLOT
SUBSLOT
repository
EBUILD SHA-256
EAPI
CHOST
ABI
active compiler realpath
active compiler complete --version output
LLVM major or GCC major/profile format
USE flags
CFLAGS
CXXFLAGS
LDFLAGS
RUSTFLAGS
GOFLAGS
FEATURES that affect output
selected package.env files
relevant EXTRA_ECONF/EXTRA_EMESON/EXTRA_ECMAKE values
kernel release for kernel-module packages
```

Do not include volatile timestamps.

The tool must emit both a human-readable metadata file and a SHA-256 key.

## 7.2 Create a system optimization generation ID

A system-wide run must have a generation ID derived from:

```text
inventory hash
active package versions
compiler versions
profile policy revision
workload suite revision
CHOST
CPU architecture
Git commit of Gentoo-stuff
```

Example shape:

```text
2026-07-10-clang22-amd64-<12-char-hash>
```

The ID is descriptive; the full hash remains authoritative.

## 7.3 Keep profile families separate

Never merge raw profiles from incompatible profile runtimes.

Required top-level separation:

```text
clang-ir/<clang-major>/<generation>/<abi>/
rust/<rustc-version>/<bundled-llvm-major>/<generation>/<abi>/
gcc/<gcc-major>/<cpv>/<fingerprint>/<abi>/
go/<go-version>/<cpv>/<fingerprint>/<binary>/
clang-sample/<clang-major>/<cpv>/<fingerprint>/<build-id>/
kernel/<kernel-release>/<config-hash>/
```

---

# 8. Eligibility matrix

The classifier must assign every installed package and artifact to one of these lanes.

| Package/artifact type | PGO lane | BOLT lane |
|---|---|---|
| Clang-built C/C++ executable or DSO | Clang IR-PGO by default; sample PGO fallback | Eligible when x86-64 ELF requirements pass |
| Clang-built static library | Clang IR-PGO; train through linked consumers | Archive itself not BOLTed; final linked consumers are candidates |
| GCC-built C/C++ | ebuild-native PGO if available; otherwise GCC gcov PGO | Eligible with `-fno-reorder-blocks-and-partition` and all BOLT checks |
| Rust binary/DSO | Rust LLVM instrumentation PGO | Candidate only after exact ELF validation and package tests |
| Go main binary | Go pprof PGO | Experimental candidate; require package-specific correctness validation |
| Pure Python/shell/Perl/Ruby package | Not applicable; optimize interpreter and native extensions | Not applicable unless package also owns eligible ELF |
| Python/Ruby/etc. native extension | Native compiler lane | Candidate if it is a supported x86-64 DSO and validation passes |
| Java/JVM bytecode package | Not applicable to bytecode; optimize JVM/native libraries | Native launcher/JNI DSOs may be candidates |
| Kernel image | Kernel AutoFDO, then Propeller where supported | Do not use generic userspace BOLT lane |
| Kernel module | Kernel build lane only | Generic BOLT not applicable |
| eBPF, SPIR-V, AMDGPU, firmware | Toolchain-specific optimization only | Not applicable |
| 32-bit x86 ELF | PGO in separate `x86` ABI lane if compiler supports it | Not applicable because BOLT lane is x86-64 only |
| Binary-only package | No rebuild PGO unless upstream binary already proves it | Do not rewrite unless source/reproducible package integration exists |
| Headers, fonts, themes, docs, metadata, data | Not applicable | Not applicable |

The classifier must examine installed contents rather than relying only on package category or ebuild language.

---

# 9. Phase 0 — Create the project, backup, and rollback base

## 9.1 Create and initialize the plan

- [x] Add this document to `plans/system-wide-pgo-bolt.md`.
- [x] Create a dedicated Git branch such as `feat/system-wide-pgo-bolt`.
- [x] Record the starting repository commit.
- [x] Add a progress summary at the top of this document.

## 9.2 Capture the live configuration

- [ ] Archive live `/etc/portage` separately from the repository copy.
- [ ] Record `emerge --info`.
- [ ] Record `eselect profile show`.
- [ ] Record `clang --version`, `ld.lld --version`, `llvm-profdata --version`, `llvm-profgen --version`, `llvm-bolt --version`, `perf --version`, `gcc --version`, `rustc -vV`, `cargo -V`, and `go version` where installed.
- [ ] Record active kernel release and kernel configuration.
- [ ] Record filesystem free space for `/var/tmp`, `/var/cache`, `/var/lib`, and the binpkg location.
- [ ] Record current `@world`, custom sets, and installed CPVs.

## 9.3 Create known-good recovery artifacts

- [ ] Ensure a bootable rescue environment exists.
- [ ] Preserve at least one known-good kernel and initramfs entry.
- [ ] Run a full binary-package backup or `quickpkg` snapshot for installed packages.
- [ ] Copy critical bootstrap binpkgs to a directory that normal binpkg cleanup will not remove.
- [ ] Include at least Portage, Python, libc, libgcc/compiler-rt, libunwind, libc++, shell, coreutils, tar, xz, zstd, rsync, OpenRC, PAM, util-linux, grep, sed, awk, findutils, Clang/LLVM, GCC/binutils, and filesystem tools.
- [ ] Verify restoration of one non-critical package from the snapshot before proceeding.

## 9.4 Establish a rollback command file

Create and test a documented recovery sequence that can:

1. disable all optimization package.env files;
2. restore critical binpkgs;
3. rebuild preserved libraries;
4. regenerate initramfs/bootloader configuration when required;
5. return to the known-good kernel.

Do not proceed until the rollback path has been tested.

---

# 10. Phase 1 — Validate hardware and tool capabilities

## 10.1 Validate perf branch-stack support

- [ ] Confirm the i5-10600K exposes usable Intel LBR support.
- [ ] Run a small `perf record -e cycles:u -j any,u` test.
- [ ] Confirm `perf report` contains branch-stack data.
- [ ] Confirm kernel permissions permit the required system-wide and user-space profiling.
- [ ] Record any temporary `perf_event_paranoid` changes and restore policy after profiling.

## 10.2 Validate Clang IR-PGO

Build a small multi-file executable and DSO using the active Clang with:

```text
-fprofile-generate
```

Run it, merge its profiles with `llvm-profdata`, rebuild with:

```text
-fprofile-use=<absolute-profile>
```

Verify that:

- raw profiles are written by multiple processes;
- the merged profile is readable;
- the final link succeeds with the system ThinLTO setup;
- profile mismatch diagnostics are visible and not blindly suppressed.

## 10.3 Validate sample PGO

- [ ] Build a sample-mapping-ready test binary.
- [ ] collect `perf.data` with branch stacks;
- [ ] convert it with `llvm-profgen`;
- [ ] rebuild with `-fprofile-sample-use` and `-fsample-profile-use-profi`;
- [ ] verify the profile is not accepted through `-fprofile-use`.

## 10.4 Validate GCC PGO separately

- [ ] Build a GCC test program with GCC generation flags.
- [ ] train it and rebuild with GCC use flags.
- [ ] verify the profile directory and correction/mismatch behavior.
- [ ] confirm no LLVM profile file is involved.

## 10.5 Validate Rust PGO

- [ ] Build a small Cargo project with absolute `-Cprofile-generate` path.
- [ ] use `--target` so build scripts are not instrumented accidentally;
- [ ] train, merge with compatible `llvm-profdata`, and rebuild using `-Cprofile-use`;
- [ ] enable missing-function warnings for validation.

## 10.6 Validate Go PGO

- [ ] Build a small Go command.
- [ ] generate a CPU pprof workload profile.
- [ ] rebuild with an explicit absolute `-pgo=<profile>` path.
- [ ] verify the build log proves PGO was enabled.

## 10.7 Validate BOLT on executable, PIE, and DSO fixtures

For each fixture:

- link with symbols, build ID, and `--emit-relocs`;
- collect branch-stack data;
- run `perf2bolt`;
- run `llvm-bolt`;
- verify `.note.bolt_info`;
- run functionality tests;
- compare ownership, mode, xattrs, and dynamic dependencies.

Do not implement the live deployment hook until all fixture classes pass.

---

# 11. Phase 2 — Refactor the repository framework

## 11.1 Remove the unsafe global consumer

- [ ] Delete or disable `portage/package.env/50-global-pgo` in its current form.
- [ ] Remove `pgo-use-if-available.conf` and `pgo-instrument.conf` or replace them with backend-specific mode files.
- [ ] Ensure absence of a profile file can never silently change an unrelated package’s compiler flags.

## 11.2 Rewrite `portage/bashrc`

Implement named functions with a strict mode dispatch. Suggested environment variable:

```text
GENTOO_OPT_MODE=
    off
    clang-ir-generate
    clang-ir-use
    clang-sample-use
    gcc-generate
    gcc-use
    rust-generate
    rust-use
    go-use
    bolt-capture
    bolt-deploy
```

The hook must:

1. compute or load the package fingerprint;
2. detect ABI and compiler family;
3. refuse incompatible mode/compiler combinations;
4. append each flag exactly once;
5. use absolute profile paths;
6. avoid C/C++ profile flags in `FCFLAGS`/`FFLAGS` by default;
7. disable ccache for profile generation and use passes unless a specific verified cache strategy is implemented;
8. log the selected backend, fingerprint, and profile path;
9. fail closed if a requested use profile is absent, malformed, or mismatched;
10. expose `post_src_install` hooks for BOLT input capture and deployment.

Keep profile generation flags out of build scripts and host tools where the language workflow requires it, especially Cargo build scripts.

## 11.3 Implement stage-specific env files

Create the files listed in section 4. Each file must contain only mode markers and the minimum flags for that mode. Avoid copying the entire global flag stack into every env file.

## 11.4 Fix sample-profile scripts

- [ ] Write sample profiles to `sample.prof` or another unmistakable sample-profile name.
- [ ] Validate them with an LLVM sample-profile-aware command.
- [ ] Consume only through `-fprofile-sample-use`.
- [ ] Preserve binary build ID and `.text` hash metadata.

## 11.5 Implement BOLT input capture hook

During `post_src_install`, when `GENTOO_OPT_MODE=bolt-capture` or an equivalent marker is active:

1. enumerate regular ELF files under `${ED}`;
2. resolve hardlink groups without duplicating work;
3. classify ELF class, type, machine, executable sections, symbols, relocations, build ID, and `.text` hash;
4. copy eligible unstripped candidates to the BOLT input cache;
5. preserve relative install path, ownership intent, mode, xattrs, capabilities metadata, and symlink topology;
6. write an artifact manifest;
7. never modify `${ED}` in capture mode.

## 11.6 Implement BOLT deployment hook

During final `post_src_install` with BOLT deployment enabled:

1. enumerate candidate files under `${ED}`;
2. compute exact build ID and `.text` hash;
3. locate the prepared BOLT output for the matching package fingerprint and input identity;
4. fail if the input identity differs;
5. preserve a copy for diagnostics;
6. replace the file in `${ED}` while preserving mode, ownership intent, xattrs, capabilities metadata, and hardlink topology;
7. verify the replacement is a valid ELF and contains `.note.bolt_info`;
8. allow Portage’s later strip/splitdebug/binpkg steps to proceed normally.

The hook must not modify installed `/usr` files directly.

## 11.7 Add automated tests

Create fixture tests for:

- package fingerprint stability;
- compiler-lane rejection;
- ABI separation;
- missing profile rejection;
- IR/sample format separation;
- BOLT candidate classification;
- build ID mismatch rejection;
- `.text` mismatch rejection;
- hardlink handling;
- symlink handling;
- setuid and file-capability metadata preservation;
- package with no ELF files;
- package with mixed 32/64-bit files.

Commit this phase before touching the live system.

---

# 12. Phase 3 — Inventory every installed package and artifact

## 12.1 Freeze package state before inventory

- [ ] Sync repositories.
- [ ] Complete the normal system update first.
- [ ] Resolve all blockers, preserved libraries, and configuration updates.
- [ ] Run depclean in pretend mode and decide whether intentional orphans remain in scope.
- [ ] Freeze package changes during the optimization generation except for fixes required by the project.

## 12.2 Generate an all-installed set

Inventory every CPV in `/var/db/pkg`, not only direct `@world` entries. Generate:

```text
/etc/portage/sets/pgo-bolt-all-installed
```

Prefer CP atoms in the persistent set and store the exact starting CPV separately in the generation manifest. Include intentional orphan packages.

Generate additional sets from classification:

```text
@pgo-ebuild-native
@pgo-clang-ir
@pgo-clang-sample
@pgo-gcc
@pgo-rust
@pgo-go
@pgo-kernel
@bolt-capture
@bolt-deploy
@optimization-not-applicable
```

## 12.3 Inventory owned files

For every installed CPV:

- parse `CONTENTS`;
- identify regular files, symlinks, and hardlinks;
- run ELF classification on regular files;
- record architecture, ELF type, interpreter, dynamic dependencies, symbols, relocations, build ID, executable sections, debug state, and owner package;
- detect `.a`, `.o`, kernel modules, eBPF, GPU objects, firmware, bytecode, scripts, and data;
- record setuid/setgid bits and file capabilities;
- deduplicate identical inodes and build IDs.

## 12.4 Detect build backend

Use multiple sources:

- installed package environment;
- ebuild/eclass inheritance;
- build logs where available;
- package.env compiler overrides;
- ELF `.comment` and producer metadata;
- language-specific metadata such as `go version -m`;
- file contents and owned artifact types.

Do not classify solely from package category.

## 12.5 Build reverse dependency information

Libraries and static archives need consumers for training. Build a reverse-dependency graph using Portage dependency metadata and dynamic ELF `NEEDED` relationships.

For every library package, identify:

- direct executable consumers;
- test suites;
- package build-time consumers;
- desktop or service workloads that load the library;
- static consumers requiring an instrumented dependency/consumer rebuild chain.

## 12.6 Produce the initial coverage report

The report must list all installed CPVs and all discovered artifacts. At this phase, zero packages may remain `unclassified`.

Commit the inventory/classifier implementation and the stable policy decisions, but do not commit machine-generated inventory JSON.

---

# 13. Phase 4 — Build the workload framework

PGO and BOLT quality depend on representative execution. Exhaustive coverage requires workloads for every eligible package or for a consumer closure that executes its code.

## 13.1 Workload contract

Every workload script must:

- be non-destructive by default;
- use a temporary working directory;
- have a timeout;
- write a structured log;
- declare packages and artifacts it intends to train;
- support a quick smoke mode and a fuller training mode;
- clean up processes, mounts, loop devices, namespaces, sockets, and temporary files;
- avoid external network dependence unless explicitly required;
- return nonzero on incomplete training;
- report which target binaries/DSOs actually accumulated samples or raw counters.

## 13.2 Required workload classes

Implement reusable harnesses for:

- compilers and linkers: compile/link C, C++, Rust, assembly, LTO, shared-library, and template-heavy corpora;
- shells and interpreters: execute representative script suites;
- compression/archive tools: compress, decompress, archive, extract, verify, and stream representative corpora;
- media codecs: decode and encode small audio/video/image fixtures through multiple codecs;
- graphics stack: shader compilation, Vulkan/OpenGL capability tests, off-screen rendering, and gamescope/wlroots paths;
- databases: temporary database creation, CRUD, indexing, transactions, vacuum/check operations;
- network tools and daemons: isolated network namespace with local client/server traffic;
- filesystem utilities: loopback image files, temporary filesystems, fsck/read-only inspection, and safe destructive operations limited to disposable images;
- package/build tools: pretend dependency resolution, source unpack/configure/compile fixtures, archive and patch workflows;
- cryptography: digest, symmetric, asymmetric, certificate, and TLS loopback operations;
- text processing: grep/sed/awk/regex/Unicode/XML/JSON workloads;
- GUI applications: headless or nested compositor startup, scripted open/render/close actions where feasible;
- desktop stack: representative Sway/Wayland session operations and IPC;
- game stack: shader-cache generation, Wine/Proton startup, DXVK/VKD3D test workloads, and safe game benchmark paths where available;
- services: temporary configurations and isolated ports;
- privileged utilities: containers/namespaces/loopback files only; never operate on real disks, filesystems, users, boot records, or network configuration.

## 13.3 Library training closure

For each library package:

1. choose multiple reverse-dependency consumers covering distinct APIs;
2. run the package’s own tests where feasible;
3. verify the library’s instrumented module produced raw counters or received perf samples;
4. add another consumer if coverage is empty or obviously narrow;
5. record the closure in package state.

## 13.4 Rare and dangerous command handling

For utilities that cannot be safely run against the real system:

- use disposable disk images, namespaces, fake roots, temporary user databases, mock services, or test fixtures;
- use read-only/help/startup execution only as an initial smoke test, not as the sole representative profile when real functionality can be safely simulated;
- if no safe representative execution can be constructed after documented attempts, use terminal reason `unsafe-to-profile` with exact evidence. This is a last resort and requires review.

## 13.5 Automated real-system session

Create a system workload orchestration script that runs a broad representative session while:

- the full instrumented package set is installed for PGO training; and later
- `perf record -a -e cycles:u -j any,u` is active for BOLT profiling.

The script must include compile, media, graphics, desktop, network, filesystem-image, interpreter, compression, package-manager, and service workloads. Multiple sessions must be collected and merged to avoid one workload dominating the profile.

---

# 14. Phase 5 — Bootstrap optimized toolchains first

The compiler, linker, profile tools, Portage’s Python interpreter, and build tools affect every later rebuild. Optimize and validate them before the exhaustive sweeps.

## 14.1 Bootstrap rules

- Keep one known-good unoptimized toolchain available.
- Do not use a toolchain to profile itself until a complete bootstrap cycle is defined.
- Do not BOLT the only copy of `clang`, `ld.lld`, `llvm-profdata`, `perf2bolt`, or `llvm-bolt`.
- Preserve versioned real binaries and symlink topology.

## 14.2 Clang/LLVM/LLD lane

Implement an upstream-compatible multi-stage LLVM PGO build:

1. stage-1 compiler from known-good toolchain;
2. instrumented stage-2 compiler;
3. representative compiler workload covering C, C++, templates, ThinLTO, linking, debug info, OpenMP host code, and common Gentoo build patterns;
4. merge profile;
5. stage-3 PGO compiler;
6. capture exact unstripped stage-3 Clang/LLD binaries;
7. BOLT profile them through a substantial compilation corpus;
8. create BOLT outputs;
9. install through the package-managed hook;
10. compare compile-time performance and run LLVM/Clang tests appropriate to the ebuild.

Do not rely on the generic per-package hook for the compiler bootstrap if the LLVM build system provides a more correct multistage flow.

## 14.3 GCC/binutils lane

- Prefer Gentoo ebuild-supported `pgo`/bootstrap mechanisms.
- Apply BOLT readiness to GCC-built candidates with `-fno-reorder-blocks-and-partition`.
- Validate compiler test suites and compile/link fixtures.

## 14.4 Python and Portage lane

- Prefer the ebuild’s native Python PGO flow.
- Resolve the repository’s current `-pgo` override only after testing the active JIT configuration separately.
- Rebuild Portage after the final optimized Python is installed.
- Verify emerge dependency resolution, binpkg operations, config protection, and preserved rebuild behavior.

## 14.5 Rust toolchain lane

- Use Rust’s supported instrumentation PGO workflow for `rustc` where the Gentoo build permits it.
- Train with representative crate builds.
- Keep Rust profile data isolated by exact rustc and bundled LLVM versions.
- BOLT the real versioned `rustc` binary only after exact-input validation.

## 14.6 Go toolchain lane

- Optimize the Go compiler/toolchain only through Go-supported PGO inputs where possible.
- Do not pass Clang profile flags into Go compilation.

## 14.7 Verify the bootstrap toolchain

Before continuing:

- compile and run C/C++/Rust/Go fixture suites;
- build and link shared libraries;
- perform ThinLTO and non-LTO builds;
- run an actual small Portage package build;
- verify profile tools can read the formats generated by the active compilers.

Commit the toolchain phase.

---

# 15. Phase 6 — Generate PGO instrumentation builds for the full installed system

This is the first exhaustive rebuild.

## 15.1 Establish the generation

- [ ] Freeze the inventory and generation ID.
- [ ] Create generation-specific raw directories.
- [ ] clear only raw data for this generation, never unrelated profiles;
- [ ] create package/ABI/compiler manifests;
- [ ] generate backend-specific package.env assignments from the classifier.

## 15.2 Clang IR-PGO generation

For eligible Clang-built C/C++ packages:

- add IR instrumentation flags to compile and final link;
- use a generation-scoped absolute runtime profile pattern containing module/build identity and PID;
- keep amd64 and x86 raw pools separate;
- disable ccache;
- ensure Portage sandbox permits only the generation raw path;
- record every compile/link invocation proving instrumentation was present.

A system-generation raw pool may be merged into a generation-wide indexed profile for Clang packages. This is useful for static-library functions whose counters are emitted by consumer binaries. The generation and compiler/ABI boundaries must remain strict.

## 15.3 GCC generation

For GCC-built packages without a correct native ebuild PGO flow:

- use a package/fingerprint/ABI-specific GCC profile directory;
- use GCC generation flags only;
- keep generated `.gcda`/related data isolated;
- add the GCC BOLT compatibility flag for later BOLT-ready builds, not blindly to Clang packages.

## 15.4 Rust generation

- apply absolute `-Cprofile-generate` paths through RUSTFLAGS;
- use `--target` behavior or eclass-specific controls so Cargo build scripts are not accidentally instrumented;
- isolate by rustc version, bundled LLVM profile version, target triple, and package fingerprint;
- disable cargo/ccache paths that could reuse non-instrumented objects.

## 15.5 Go generation/profile-source build

Go PGO is sampling/pprof-based rather than an instrumentation rebuild. Build the baseline package variant required for profile collection and record exact source/build metadata. Do not add Clang IR flags.

## 15.6 Ebuild-native PGO packages

Packages with an ebuild-supported PGO flow should use that flow unless testing proves it broken. Record the ebuild’s internal generation/training/use behavior and final build log.

Do not add generic PGO on top of native ebuild PGO unless explicitly designed and benchmarked.

## 15.7 Rebuild order

Use a dependency-complete rebuild with build dependencies included. Start with bootstrap/system libraries, then the remainder of the installed set. Suggested command shape, adjusted after pretend review:

```bash
emerge -eav @pgo-bolt-all-installed \
  --with-bdeps=y \
  --complete-graph=y \
  --backtrack=1000 \
  --keep-going
```

`--keep-going` is acceptable for discovery, but every failure must return to pending and be resolved before the phase is complete.

## 15.8 Instrumentation-build completion gate

The phase is complete only when:

- every PGO-eligible package has an installed instrumented or valid native-PGO training variant;
- every failed package has been remediated and rebuilt;
- no package accidentally used a different compiler family’s flags;
- all critical commands still function;
- the system can reboot into the instrumented userspace if a reboot is needed for service/library training.

Commit framework changes and package-specific compatibility decisions discovered during the sweep.

---

# 16. Phase 7 — Train the complete instrumented system

## 16.1 Training environment

- enable the generation-specific profile-write directory;
- confirm normal users and service users can emit unique files;
- set environment overrides only where needed;
- restart long-lived processes so they load instrumented DSOs;
- reboot if required to ensure all active services and desktop processes use instrumented libraries;
- verify several known packages emit profiles before running the full suite.

## 16.2 Natural build-workload training

Use the instrumented system to build a substantial package/corpus workload. This naturally trains:

- Clang/GCC/binutils;
- shells;
- Python and Portage;
- compression and archive libraries;
- libc/libc++/libunwind where eligible;
- build systems;
- linkers;
- filesystem and text tools.

Do not count this as sufficient coverage for desktop, media, graphics, network, or service packages.

## 16.3 Run all workload classes

- [ ] compiler/build workload;
- [ ] shell/interpreter workload;
- [ ] compression/archive workload;
- [ ] media encode/decode workload;
- [ ] graphics/Vulkan/OpenGL/shader workload;
- [ ] compositor/desktop workload;
- [ ] network client/server workload;
- [ ] database workload;
- [ ] cryptography/TLS workload;
- [ ] filesystem loop-image workload;
- [ ] service workload;
- [ ] Wine/Proton/gaming-stack workload;
- [ ] package-specific workloads generated from uncovered targets.

## 16.4 Coverage-driven iteration

After the first pass:

1. map raw profiles to package/artifact identities;
2. identify eligible packages with zero counters;
3. identify libraries with no consumer execution;
4. generate additional package-specific workloads;
5. rerun until every eligible package has meaningful matching data or reaches a reviewed terminal exclusion.

A raw file existing is not enough. It must contain nonzero counters matching code in the target package.

## 16.5 Stop and flush

Gracefully stop instrumented daemons and applications so counters flush. For programs that may terminate abnormally, use continuous-profile mode only after a dedicated compatibility test.

Lock the raw profile generation when training is complete and make it read-only.

---

# 17. Phase 8 — Merge and validate PGO profiles

## 17.1 Clang IR merge

Merge generation-compatible raw profiles into the Clang generation profile. Validate:

- profile format readability;
- nonzero function counts;
- module/build mappings;
- ABI separation;
- compiler major/profile format compatibility;
- absence of corrupt or truncated inputs.

Preserve raw inputs until the final optimized system passes validation.

## 17.2 GCC merge/use preparation

Use GCC’s package-specific profile structure and validate that expected `.gcda` data corresponds to current objects/source identity.

## 17.3 Rust merge

Use an `llvm-profdata` compatible with the Rust-generated raw format. Never assume the system Clang tool’s format is compatible without testing.

## 17.4 Go profile preparation

For every Go main binary:

- produce or merge valid CPU pprof data;
- ensure symbolization, inlined frames, and function start-line information are adequate;
- keep profiles binary/workload-specific;
- never use one executable’s profile for unrelated main packages.

## 17.5 Sample PGO fallback

For Clang packages where instrumentation is unsupported or unusable:

1. build profile-mapping-ready exact binaries;
2. collect branch-stack perf data;
3. convert with `llvm-profgen`;
4. validate the sample profile;
5. mark the package `clang-sample-use`;
6. never merge or consume it as IR instrumentation data.

## 17.6 Profile quality gate

A package may enter PGO-use only if:

- the profile is readable by the exact intended compiler family;
- the profile contains matching nonzero data;
- training workloads are recorded;
- no compiler/ABI/fingerprint boundary is violated;
- mismatch/stale diagnostics remain below the policy threshold;
- a package-specific smoke build succeeds.

Generate a signed-off profile coverage report before the PGO-use sweep.

---

# 18. Phase 9 — Full PGO-use rebuild and BOLT input capture

This is the second exhaustive rebuild. It produces the exact PGO-optimized binaries used for BOLT profiling.

## 18.1 Generate final PGO-use package assignments

Generate package.env entries from the validated state database:

- native ebuild PGO;
- Clang IR use;
- Clang sample use;
- GCC use;
- Rust use;
- Go use;
- explicit non-applicable packages with no generic profile flags.

No package may select a use mode without a validated profile.

## 18.2 Enable BOLT capture readiness

For BOLT candidate builds:

- preserve usable symbol information for the cached input;
- link with GNU build ID;
- link with relocation information such as `--emit-relocs`;
- for GCC-built candidates, disable incompatible block partitioning;
- ensure the capture hook copies candidates before Portage strips them.

## 18.3 Run the exhaustive PGO build

Rebuild all installed packages with PGO-use and BOLT capture enabled. Do not deploy BOLT yet.

The capture hook must populate the exact input cache for every eligible ELF.

## 18.4 Verify PGO application

For every eligible package:

- parse the build log for the correct profile-use flag/backend;
- reject missing-profile and out-of-date-profile failures;
- run package tests/smoke tests;
- verify no generation flags remain in deployed files;
- record the final PGO build fingerprint.

## 18.5 Verify captured BOLT inputs

For each candidate:

- cached unstripped file exists;
- installed runtime file has matching code identity/build ID as appropriate;
- `.rela.text` or equivalent required relocation information is present in the cached input;
- symbol table is usable;
- artifact manifest records installed path and owner;
- no duplicate symlink/hardlink candidate is processed independently.

Do not proceed to BOLT profiling while any eligible artifact lacks a valid captured input.

---

# 19. Phase 10 — Collect exhaustive BOLT profiles

## 19.1 Collect multiple system-wide sessions

Use branch-stack cycle profiles. Collect separate sessions for major workload families so they can be converted and merged deliberately.

Example command shape:

```bash
perf record \
  -e cycles:u \
  -j any,u \
  -a \
  -o /var/cache/gentoo-optimization/bolt/perf/<session>.perf.data \
  -- <workload-or-orchestrator>
```

Collect at least:

- full build/compile session;
- desktop/GUI/graphics session;
- media session;
- network/service/database session;
- compression/text/filesystem session;
- gaming/Wine/Proton session;
- package-specific sessions for uncovered candidates.

## 19.2 Convert profiles per exact candidate

For every cached input ELF:

1. run `perf2bolt` against each relevant perf session;
2. discard only sessions that contain no mapping/samples for that candidate;
3. record conversion diagnostics;
4. merge nonempty fdata inputs with `merge-fdata`;
5. associate final fdata with input build ID and `.text` hash.

## 19.3 Coverage-driven BOLT workload generation

For every eligible artifact with zero or inadequate samples:

- identify the owning package and canonical executable/DSO;
- run its package workload or a reverse-dependency consumer under perf;
- for libraries, use multiple consumers;
- for daemons, use an isolated instance;
- for privileged/destructive tools, use disposable namespaces/images;
- repeat conversion and merge.

No candidate may be considered optimized with empty profile data.

## 19.4 BOLT profile quality policy

Record at minimum:

- profiled function count;
- total function count;
- stale function/profile percentage;
- branch sample count;
- whether relocations were detected;
- whether profile was collected from exact input identity;
- workload sources.

Set conservative rejection thresholds. A default policy may reject:

- zero profiled functions;
- corrupt branch data;
- significant stale-profile diagnostics;
- build ID or `.text` mismatch;
- unsupported control-flow patterns reported as unsafe;
- files with critical BOLT warnings that cannot be explained.

Threshold exceptions require package-specific evidence and tests.

---

# 20. Phase 11 — Produce and validate every BOLT output

## 20.1 Default BOLT command policy

Start from a reviewed default such as:

```text
-reorder-blocks=ext-tsp
-reorder-functions=hfsort+
-split-functions
-split-all-cold
-split-eh
-icf=1
-use-gnu-stack
-dyno-stats
```

Do not assume one option set works for every binary. Maintain package/artifact overrides for unsupported EH, jump-table, assembly, DSO, privileged, Go, or Rust cases.

## 20.2 Optimize cached inputs only

Run `llvm-bolt` against the cached unstripped exact PGO input, never an unrelated current `/usr` file.

Store output under:

```text
/var/cache/gentoo-optimization/bolt/outputs/<cpv>/<fingerprint>/<build-id>/<relative-path>
```

## 20.3 Validate every output

For each BOLT output:

- ELF parser/readelf succeeds;
- `.note.bolt_info` exists;
- dynamic loader/interpreter is unchanged;
- `NEEDED`, SONAME, RPATH/RUNPATH, symbol versions, and exported ABI remain valid;
- mode, ownership intent, xattrs, capabilities, and hardlink metadata are preserved in manifest;
- package workload passes against the BOLT output;
- ABI comparison tools pass for public libraries where applicable;
- runtime dependencies resolve;
- a benchmark or perf-stat comparison shows no material regression where measurable.

## 20.4 Test privileged artifacts separately

Setuid/setgid and file-capability binaries require an isolated deployment test. Verify both functionality and security metadata. Do not drop privileges or capabilities through file replacement.

## 20.5 Test shared libraries through consumers

For each BOLTed DSO:

- run direct library tests;
- run multiple reverse-dependency consumers;
- run symbol/version checks;
- run `revdep-rebuild`-equivalent linkage checks in the staging/test environment.

## 20.6 Generate deployment manifest

Only outputs that pass all validation enter the deployment manifest. The manifest must map exact package fingerprint + input identity + relative path to the output file and expected metadata.

The strict goal requires every BOLT-eligible candidate to appear in this manifest. Remaining candidates must be remediated or moved to a reviewed terminal exclusion with evidence.

---

# 21. Phase 12 — Final exhaustive PGO+BOLT rebuild

This is the completion rebuild. It must install final PGO-use binaries and apply matching BOLT outputs inside `${ED}` before Portage stripping.

## 21.1 Preflight

- [ ] All installed CPVs still match the frozen generation or have been re-profiled.
- [ ] All validated PGO profiles are present.
- [ ] All BOLT deployment outputs match expected input identities.
- [ ] All package-specific env assignments are generated.
- [ ] All previous build failures are resolved.
- [ ] Rollback binpkgs remain available.
- [ ] There is sufficient disk space.

## 21.2 Enable final modes

For each package, final mode must combine:

```text
correct PGO-use backend
+
BOLT deployment for matching eligible artifacts
```

Non-machine-code packages still participate in the full rebuild/set processing and receive a final `not-applicable` record.

## 21.3 Execute the full rebuild

Run a pretend first. Then run the dependency-complete exhaustive rebuild. Use the complete all-installed set rather than relying only on `@world`.

The BOLT deployment hook must fail closed on missing or mismatched outputs. Do not let a package silently install an un-BOLTed eligible binary.

## 21.4 Resolve all failures

For any package failure:

1. preserve log and state;
2. restore package from known-good binpkg if the system is affected;
3. classify root cause as PGO, BOLT, unrelated existing flags, package bug, nondeterministic build, or tooling bug;
4. fix the narrowest cause;
5. regenerate profile/output if identity changed;
6. rebuild and retest;
7. update this plan and package policy.

The final rebuild is not complete while `--keep-going` failures remain.

## 21.5 Complete graph repair

After the full rebuild:

```bash
emerge -av @preserved-rebuild
revdep-rebuild
emaint --check world
```

Use the appropriate installed tools and review all output. Rebuild any affected reverse dependencies with the same final PGO+BOLT modes.

---

# 22. Phase 13 — Kernel-specific full-system optimization lane

Do not pass generic userspace PGO/BOLT flags to the kernel.

## 22.1 Preserve a known-good kernel

- keep the current known-good boot entry;
- create a distinct version/localversion for each training and optimized kernel;
- never overwrite the only working kernel or initramfs.

## 22.2 AutoFDO stage

For a Clang-built supported kernel:

1. enable kernel AutoFDO configuration;
2. build and install a profile-collection kernel without a supplied profile;
3. boot it;
4. run representative full-system workloads;
5. collect kernel branch profiles with the architecture-appropriate event;
6. convert with a kernel-aware profile generator;
7. rebuild with the AutoFDO profile;
8. boot and validate.

## 22.3 Propeller stage

Where the active kernel/toolchain supports it:

1. use the AutoFDO kernel as the base;
2. enable Propeller metadata/configuration;
3. build and boot the Propeller training kernel;
4. collect a second representative kernel profile;
5. generate compile-time and link-time Propeller profiles;
6. build the final AutoFDO+Propeller kernel;
7. install as a separate boot entry;
8. boot and validate graphics, storage, networking, audio, input, ZFS/module compatibility, suspend/resume if used, and gaming workloads.

## 22.4 Kernel modules

Rebuild all external modules against the final optimized kernel. Verify module loading, vermagic, symbol versions, and initramfs inclusion.

Record kernel optimization separately from userspace BOLT coverage.

---

# 23. Phase 14 — Strict verification and acceptance

## 23.1 Package coverage verifier

Run the strict verifier across every CPV in `/var/db/pkg`. It must fail if:

- a CPV is absent from state;
- final CPV differs from the recorded generation without reprocessing;
- an eligible PGO package lacks proof of profile-use build;
- a profile is stale, missing, unreadable, or from the wrong compiler/ABI;
- an eligible ELF lacks a BOLT output/note;
- an exclusion lacks evidence;
- an artifact ownership/path changed without reinventory;
- any status is pending/unknown/failed.

## 23.2 Installed ELF verifier

Rescan the live filesystem from package CONTENTS. For every eligible installed ELF:

- verify `.note.bolt_info`;
- verify GNU build ID and deployed identity records;
- verify dynamic linkage;
- verify permissions, ownership, xattrs, capabilities, setuid/setgid, symlink and hardlink topology;
- verify public library SONAME/versioning;
- verify no instrumented generation runtime remains enabled in final binaries.

## 23.3 PGO proof verifier

For each optimized package, retain:

- final build log;
- correct backend/profile-use invocation;
- profile metadata and workload references;
- profile validation output;
- final package fingerprint;
- package smoke/test results.

## 23.4 Runtime validation suite

At minimum validate:

- reboot and login;
- OpenRC services;
- networking and DNS;
- Sway/Wayland session;
- GPU/Vulkan/OpenGL;
- audio/PipeWire;
- Steam/Proton/Wine;
- gamescope;
- browser or WebKit stack where installed;
- Python/Portage operations;
- C/C++/Rust/Go compilation;
- filesystem tools on disposable images;
- package install, uninstall, binpkg restore, and preserved rebuild;
- media encode/decode;
- SSH/local network tools if used;
- ZFS or other external modules if present.

## 23.5 Performance sanity checks

Compare selected high-impact workloads with the recorded baseline:

- Clang build corpus;
- linker workload;
- Python workload;
- compression/decompression;
- media encode/decode;
- shader compilation;
- startup of major applications;
- selected games/benchmarks;
- kernel/system workload.

Do not require every tiny utility to show a speedup, but reject material reproducible regressions caused by profile or BOLT choices.

## 23.6 Required final report

Generate a report with at least:

```text
installed_packages_total
packages_rebuilt_from_source_total
pgo_eligible_total
pgo_optimized_total
pgo_not_applicable_total_by_reason
pgo_terminal_exclusion_total_by_reason
installed_elf_total
bolt_eligible_total
bolt_optimized_total
bolt_not_applicable_total_by_reason
bolt_terminal_exclusion_total_by_reason
kernel_optimization_state
pending_total
unknown_total
failed_total
```

Strict completion requires:

```text
packages_rebuilt_from_source_total == installed_packages_total
pgo_optimized_total == pgo_eligible_total
bolt_optimized_total == bolt_eligible_total
pending_total == 0
unknown_total == 0
failed_total == 0
```

Terminal exclusions must not be counted as eligible. The classifier must prove why they are not eligible or why the upstream/tooling correctness boundary makes optimization impossible.

---

# 24. Phase 15 — Maintenance after completion

A system-wide profile optimization is invalidated incrementally by package updates. Implement maintenance rather than treating this as a one-time experiment.

## 24.1 Package update invalidation

When a package changes:

- compare CPV, ebuild hash, compiler, ABI, flags, source identity, build ID, and `.text` hash;
- invalidate only affected PGO/BOLT states and dependent static-library closures;
- never silently reuse exact-build BOLT output across a mismatch;
- allow only explicitly supported source-stable profile reuse, such as Go’s intended workflow or reviewed sample-PGO reuse;
- queue the package for retraining/rebuild.

## 24.2 Scheduled generation refresh

Create documented commands for:

- incremental package update optimization;
- periodic full workload/profile refresh;
- stale profile reporting;
- profile cache pruning while retaining rollback generations;
- rebuilding a package with optimization disabled for troubleshooting;
- restoring the latest known-good binpkg.

## 24.3 Portage update integration

Normal world updates must either:

1. fail closed when a package update would install an unprofiled eligible artifact; or
2. place the package in a clearly reported temporary baseline state and immediately queue its generation, training, PGO rebuild, BOLT profiling, and final deployment before declaring the system optimized again.

The system-wide “fully optimized” status must be revoked whenever eligible pending work exists.

---

# 25. Package-specific remediation decision tree

For each failing package, follow this order.

## PGO generation failure

1. Verify compiler family and flags.
2. Remove only incompatible profile flag from host/build tools.
3. separate ABI passes;
4. disable ccache;
5. fix profile runtime output path/sandbox;
6. use ebuild-native PGO if available;
7. switch Clang IR-PGO to Clang sample PGO if instrumentation is the blocker;
8. use language-native PGO for Rust/Go;
9. classify terminal unsupported only after evidence.

## PGO-use failure

1. verify fingerprint/compiler/ABI;
2. validate profile format;
3. inspect stale/missing-function diagnostics;
4. regenerate with exact source and flags;
5. broaden training workload;
6. separate colliding profiles;
7. try package-native or sample PGO fallback;
8. never suppress a real mismatch merely to complete the build.

## BOLT capture failure

1. confirm ELF64 x86-64 ET_EXEC/ET_DYN;
2. confirm symbols and relocations;
3. resolve stripping order;
4. add package-specific link flags;
5. handle symlink/hardlink real binary;
6. for GCC add the required block-partitioning compatibility flag;
7. classify unsupported object types explicitly.

## BOLT profile failure

1. verify perf branch stacks;
2. verify the exact binary was executing;
3. run package-specific workload;
4. train DSO through reverse dependencies;
5. merge multiple nonempty fdata profiles;
6. reject empty data.

## BOLT correctness failure

1. restore known-good package;
2. preserve input/output/profile/logs;
3. reduce BOLT options narrowly;
4. test EH/jump-table/ICF/splitting overrides;
5. retest ABI and runtime;
6. report upstream-quality reproducer where possible;
7. use a reviewed terminal exclusion only when no safe BOLT configuration works.

---

# 26. Required Git phase commits

Use clear commits resembling:

1. `docs: add exhaustive system-wide PGO and BOLT plan`
2. `refactor: separate PGO backends and exact build identities`
3. `feat: add installed package and ELF inventory pipeline`
4. `feat: add workload and profile coverage framework`
5. `feat: add pre-strip BOLT capture and deployment hooks`
6. `feat: add strict PGO and BOLT verification`
7. `portage: add generated system-wide optimization policy`
8. `docs: record completed optimization generation and results`

Do not combine unrelated existing Portage cleanup into these commits unless necessary for the optimization pipeline.

---

# 27. Final completion checklist

The implementing agent may mark this plan complete only after every item below is true.

## Framework

- [ ] Instrumentation and sample profiles are fully separated.
- [ ] Compiler-family leakage is impossible and tested.
- [ ] Package/ABI/compiler fingerprints are implemented.
- [ ] BOLT capture happens on unstripped `${ED}` files.
- [ ] BOLT deployment happens in `${ED}` before Portage stripping.
- [ ] Mismatch checks fail closed.
- [ ] State schemas and strict verifier are implemented.

## Inventory and classification

- [ ] Every installed CPV is inventoried.
- [ ] Every owned file is classified.
- [ ] Every package has a PGO backend or evidence-backed non-applicable state.
- [ ] Every native ELF has a BOLT eligibility state.
- [ ] Zero package or artifact records are unknown.

## PGO

- [ ] Toolchain bootstrap PGO is complete.
- [ ] Full instrumentation/profile-source sweep is complete.
- [ ] All workload classes ran successfully.
- [ ] All eligible packages have valid representative profile data.
- [ ] Full PGO-use rebuild completed.
- [ ] Build logs prove correct profile use for every eligible package.

## BOLT

- [ ] Exact PGO-built input exists for every eligible ELF.
- [ ] Every eligible ELF has nonempty matching fdata.
- [ ] Every BOLT output passed structural, ABI, metadata, and runtime validation.
- [ ] Final deployment manifest covers every eligible ELF.
- [ ] Installed eligible ELFs contain `.note.bolt_info`.

## Final system

- [ ] Final exhaustive rebuild applied PGO and BOLT together.
- [ ] `@preserved-rebuild` is clean.
- [ ] Reverse-dependency checks are clean.
- [ ] System rebooted successfully.
- [ ] Desktop, graphics, audio, network, package manager, compiler, media, gaming, storage, and service tests pass.
- [ ] Active kernel uses the completed supported kernel profile-optimization lane.
- [ ] Strict report has `pending=0`, `unknown=0`, and `failed=0`.
- [ ] Rollback binpkgs and known-good kernel remain available.
- [ ] This document contains final counts, benchmark results, exceptions, and the generation ID.

---

# 28. Final agent instruction

Implement the complete plan, not merely the framework. Do not stop after adding scripts, flags, profiles, or documentation. Continue through inventory, exhaustive rebuilds, training, profile validation, BOLT conversion, final package-managed deployment, reboot, runtime testing, and strict coverage verification.

After every completed item:

1. update this document;
2. update package/artifact state;
3. preserve logs and evidence;
4. re-read the plan;
5. verify the completed work fully satisfies the item rather than only approximating it;
6. proceed to the next unresolved item.

The project is complete only when the live installed system—not merely the repository—passes the final strict completion conditions.

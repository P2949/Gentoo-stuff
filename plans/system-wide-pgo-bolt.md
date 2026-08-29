# Exhaustive System-Wide PGO and BOLT Implementation Plan for Gentoo

## Progress summary

- **Project state:** active; Phase 0 and Phase 1 are complete and Phase 2 is active. The current branch head is the latest Candidate-A implementation revision under repository-boundary correction; it remains non-authorizing pending the exact clean portable, CI, live-host, and supervised production gates. No Candidate-A revision has been installed or accepted by the complete host and production gates, and no generated package assignment is active. Phase 2 is scope-frozen until Candidate B authorization: no new subsystem, optimization-policy axis, evidence category, or broad refactor may be added unless an existing required gate exposes a reproducible blocker that cannot be fixed within the current architecture.
- **Dedicated branch:** `feat/system-wide-pgo-bolt`.
- **Starting repository commit:** `c04773564da826abdeea3660568701d040cc89d0`.
- **Optimization generation:** not established; inventory is not yet frozen.
- **Starting live package count:** 1,181 CPVs; this is evidence capture only, not the frozen Phase 3 inventory.
- **Current non-frozen live package count:** 1,220 installed CPVs in the read-only 2026-08-09 `/var/db/pkg` refresh; sorted CPV-list SHA-256 `a5b75bd995f68d74d869b2d5996dcd345e326741f4d1c329a9dbc876edb630ff`. This unchanged observation is live progress evidence, not the frozen Phase 3 inventory, and must be recomputed immediately before the first live mutation.
- **Strict coverage totals:** pending the Phase 3 live inventory; no zero-coverage claim has been made.
- **Current Phase 2 authorization:** none. No detached Phase 2 evidence index exists for the Candidate-A implementation, and the installed framework predates it.
- **Last plan review:** 2026-08-29; Candidate-A repository-boundary stabilization is in progress. The complete rejected-predecessor sequence and exact workflow evidence are recorded below and in `docs/commit-history-map.md`. Most recently, commits `9dd5960958c397216dd4c60b6212658f10015dca`, `ea191dce00f19db8ea0dda20ded85ab594d40db3`, `0f91f5ab68d7917206e79d4cb688b3a93ab5f182`, and `6462859e207b3545d9b699c242250022ad8c9f26` were tested by exact GitHub Actions runs `32601934693`, `33104209384`, `33170596519`, and `33188537276`; every run stopped at the static authoritative-contract preflight before `portable-complete`. Those CI runs prove stale test topology only. Defects found by separate direct review are attributed independently below. Commit `6462859e207b3545d9b699c242250022ad8c9f26` fixes the prerequisite producer/verifier serialization mismatch, the non-portable root-trust fixture, and non-discriminating negative evidence tests, but remains rejected and non-authorizing because its exact contract and portable CI boundary are red. Its pre-correction discovery baseline is 34 exact top-level cases, 59 Bash sources, 44 evidence tests, 323 main Python tests, one dedicated stress test, and 79 recovery tests. No live mutation, Candidate-A acceptance, Phase 2 authorization, optimization generation, or frozen inventory resulted. Any successor remains non-authorizing pending a coherent exact contract, fresh clean portable and CI boundary, and the live-host and supervised production gates. The complete plan must be re-read after this correction, after the exact clean portable/CI gates, and after each live checkpoint or package operation. The absence of a generation, frozen inventory, or installed-system coverage claim remains binding.
- **Safety gate:** passed on 2026-07-11. Protected binpkg restoration, exact independent `/efi` recovery assets, an actual `BootCurrent=0004` recovery boot, manifest-backed zero-override rollback defaults, and separate executable-tested Clang/libc++ and GCC/libstdc++ recovery lanes are verified.
- **BOLT capability gate:** the historical Phase 1 gate passed on 2026-07-12 with package-managed LLVM BOLT 22.1.8. Candidate A must rerun the current four-class gate; the old proof authorizes hook development only and does not claim that any installed-system candidate has been BOLT-optimized.

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

The implementing agent must update the checkboxes, status tables, decisions, discovered exceptions, commands, and results in this document after every implementation step. After each completed step, the agent must re-read this document before deciding the next action. The one deliberate exception is the Phase 2 Candidate-B authorization freeze: after Candidate B commits the truthful checked claims and generated claim markers, this plan must not change while the complete gate is rerun. The root-owned detached index and component states carry that post-commit evidence. Any required plan correction creates Candidate C and requires the complete gate to run again.

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

# 3. Starting repository defects and their resolution status

These were defects in the project's starting framework. The ledger below
distinguishes repository implementation from live Candidate-A validation and
Candidate-B authorization. Repository-resolved does not authorize Phase 2,
activate an optimization generation, or permit a system-wide rebuild.

| Starting defect | Repository implementation | Live/authorization status |
| --- | --- | --- |
| Mixed Clang IR and sample-profile handling | Resolved by the distinct `merged.profdata`/`sample.prof` stores, validators, states, and consumers in §11.4. | Open: all four §11.4 live sample-PGO claims remain unchecked. |
| Compiler-family leakage | Resolved by the fail-closed backend/ABI dispatcher in §11.1–§11.2. | Open: the exact installed Candidate A and frozen Candidate B must pass the complete live gate. |
| Weak `${CATEGORY}/${PN}` profile key | Resolved by the versioned package/compiler/ABI/build fingerprint contract in §7.1 and §11.2. | Open: live Candidate A/B proof remains required. Independently observing and hashing the exact ebuild used by each installed build remains Phase 3 provenance work. |
| Clang receiving GCC correction flags | Resolved; profile correction is confined to the GCC lane. | Open: current installed-candidate live revalidation is required. |
| BOLT operating on installed stripped files | Resolved in the cached exact-input, registration, and pre-strip `${ED}` hooks in §11.5–§11.6. | Open: the current exact candidate must pass live capture/deployment and four-class BOLT gates. |
| Globally enabled BOLT/sample readiness | Resolved by the stage-only policy in checked §11.3. | Open: the installed candidate must prove ordinary/off builds are clean and stage assignments are exact. |

## 3.1 Remove mixed instrumentation/sample-profile handling

At project start, the sample-profile converter wrote an `llvm-profgen` sample
profile to a file named `merged.profdata`, while the global hook consumed that
file with `-fprofile-use`. Instrumentation and sample profiles are different
formats and must use different compiler options.

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

At project start, a global rule enabled LLVM `-fprofile-use` for every package
based only on a file existing. Its replacement must choose a backend after
detecting the package's actual compiler lane:

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

At project start, the path based only on `${CATEGORY}/${PN}` allowed different
versions, slots, ABIs, compilers, and configurations to collide. It must remain
absent.

Use a generation-aware fingerprint described in section 7.

## 3.4 Remove Clang-incompatible profile flags

Do not use `-fprofile-correction` in the Clang path. Keep GCC correction behavior only inside the GCC lane.

## 3.5 Move BOLT from installed stripped files to cached exact inputs and pre-strip deployment

At project start, the scripts processed `/usr/bin/...` after Portage
installation and wrote test copies under `/opt/bolt-test`. That was acceptable
only as an early experiment. The final pipeline must:

1. capture the exact unstripped PGO-built ELF from `${ED}` during `post_src_install`;
2. train the corresponding installed PGO binary;
3. run `perf2bolt` and `llvm-bolt` against the cached unstripped exact input;
4. verify input build ID and `.text` hash;
5. during the final package rebuild, replace the matching file inside `${ED}` with the prepared BOLT output;
6. allow Portage to perform its normal splitdebug/strip/binpkg/deployment handling afterward.

## 3.6 Make BOLT readiness stage-aware

At project start, global flags added line tables, sample mapping metadata,
relocation sections, optimization records, and section splitting to every
package at all times. They must remain separated into independent stage
profiles:

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

`FEATURES` is not an unordered token list. Its fingerprint identity is the
effective Portage last-token-wins state, canonicalized by feature name after
applying every positive and negative token. Raw token sorting is forbidden
because it can collapse opposite effective policies into one build identity.

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

- [x] Archive live `/etc/portage` separately from the repository copy.
- [x] Record `emerge --info`.
- [x] Record `eselect profile show`.
- [x] Record `clang --version`, `ld.lld --version`, `llvm-profdata --version`, `llvm-profgen --version`, `llvm-bolt --version`, `perf --version`, `gcc --version`, `rustc -vV`, `cargo -V`, and `go version` where installed. (`llvm-bolt`/`perf2bolt` absence is explicitly recorded.)
- [x] Record active kernel release and kernel configuration.
- [x] Record filesystem free space for `/var/tmp`, `/var/cache`, `/var/lib`, and the binpkg location.
- [x] Record current `@world`, custom sets, and installed CPVs.

## 9.3 Create known-good recovery artifacts

- [x] Ensure a bootable rescue environment exists.
- [x] Preserve at least one known-good kernel and initramfs entry.
- [x] Run a full binary-package backup or `quickpkg` snapshot for installed packages.
- [x] Copy critical bootstrap binpkgs to a directory that normal binpkg cleanup will not remove.
- [x] Include at least Portage, Python, libc, libgcc/compiler-rt, libunwind, libc++, shell, coreutils, tar, xz, zstd, rsync, OpenRC, PAM, util-linux, grep, sed, awk, findutils, Clang/LLVM, GCC/binutils, and filesystem tools.
- [x] Verify restoration of one non-critical package from the snapshot before proceeding.

### Phase 0 evidence and decisions

- Live evidence is rooted at `/var/lib/gentoo-optimization`; caches and recovery artifacts are under `/var/cache/gentoo-optimization`. The Phase 0 top-level roots were root-owned mode `0755`; security-sensitive descendants deliberately use stricter `0700`, `0750`, `0640`, `0440`, and `0550` policies. No profile pool is world-writable.
- A Phase 2 root-trust audit on 2026-07-13 found that the live filesystem root and `/etc` had drifted to root-owned mode `0777`, which made any root-owned `/etc/portage` publication replaceable by an unprivileged local user. Both were immediately restored to the standard mode `0755` before any framework publication. The root-owned before/after report is `/var/lib/gentoo-optimization/reports/phase-2-root-trust-ancestor-remediation-20260713.log` (SHA-256 `054da0a00e57c3951f2cc4cfb1ee486147e5c4d8c8cbb3ef0a18464d498b58a4`); project state is `/var/lib/gentoo-optimization/state/project/root-trust-ancestor-remediation.json` (SHA-256 `51cf57b9378dad41ef7cac29098d9fdfe248e6fdaffc85dfb7cc62884c9eddf5`) with zero pending, unknown, or failed items for this remediation. The framework installer now treats every root-owned destination ancestor as a fail-closed trust condition; this remediation does not replace its per-run checks.
- At Phase 0, `/etc/portage` was a live symlink to this repository's `portage/` tree; the starting archive is `/var/lib/gentoo-optimization/reports/phase-0-live-etc-portage.tar.zst` with SHA-256 `1f4c812aa2c26e700f4181d3bea550266ec0aa6d4433b41feb318b6d108e4b1f`. It now resolves through `/var/lib/gentoo-optimization/framework-current/portage`; the selected installed candidate predates Candidate A and cannot validate the current source revision.
- The ESP is mounted at `/efi`, not `/boot`. Its starting raw image is `/var/cache/gentoo-optimization/binpkgs/esp-starting-20260710.img.zst` (verified by `zstd -t`, SHA-256 `83b3f46cc843427e9058cbcb07418c5e93ec943a3f176af60eebe75e81a33447`).
- NVRAM entry `Boot0200` originally referenced `7.1.2-cachyos2` `-old` paths that were absent. Exact hash-matching copies now occupy those managed paths, but they remain rotatable and are not the authoritative independent recovery generation; the uniquely named `Boot0004`/`Boot0005` generation below supersedes them.
- The active kernel is `7.1.2-cachyos2`; its recorded config SHA-256 is `cc8c2e2c90cc47720e027c0bf5b7fc8d438a183efa30f15e6bf77c5083ebe6a6` and enables `CONFIG_AUTOFDO_CLANG`, `CONFIG_PROPELLER_CLANG`, and perf events.
- At capture, `/var/tmp`, `/var/cache`, `/var/lib`, and `PKGDIR=/var/cache/binpkgs` share the root XFS filesystem with 213,653,905,408 bytes available. Existing binpkgs consume 13 GiB and distfiles 40 GiB; later preflight must account for snapshot/profile/BOLT growth.
- The protected full snapshot is `/var/cache/gentoo-optimization/binpkgs/snapshot-20260710` (root-owned mode `0700`, 7.4 GiB). Its `Packages` index contains exactly 1,181 unique CPVs; set comparison against live `/var/db/pkg` reports zero missing and zero extra CPVs, and `emaint -c binhost` passes. A second archive-level verifier checked all 1,181 indexed outer GPKG manifests, hashes, sizes, and embedded `image.tar.zst` streams with zero missing, extra, unindexed, or failed records. Its machine-readable evidence is `phase-0-binpkg-payload-verification.json` (SHA-256 `3a64e7ded1deb7c00f05bd75f1bc9c8471159f0774b7d663966cf377d74f09d7`). Configuration files were deliberately excluded from quickpkg and are covered separately by configuration archives.
- The durable critical recovery copy is `/var/lib/gentoo-optimization/recovery/binpkgs/critical-20260710` (root-owned mode `0700`), created with XFS copy-on-write reflinks and exposed through the root-owned `critical-current` link. It is outside normal Portage/cache cleanup scope, has the same clean 1,181-record index, and currently retains the complete snapshot rather than a narrow subset. Verification explicitly found all 58 installed CPVs spanning the required bootstrap/toolchain/filesystem package families; no required CPV was absent. Evidence is in `phase-0-critical-binpkg-verification.log` and `phase-0-persistent-critical-binpkgs.log`.
- `app-admin/ps_mem-3.14-r1` was actually reinstalled from the protected snapshot with `--usepkgonly --getbinpkg=n --nodeps`; Portage reported one binary reinstall and zero downloads. `equery check` passed all 16 files both before and after, and the restored command's smoke test passed. Evidence is in `phase-0-binpkg-restore-pretend.log` and `phase-0-binpkg-restore-test.log`.
- Root-only, checksum-tested starting archives of `/etc`, `/lib/modules/7.1.2-cachyos2`, and `/efi/EFI/Gentoo` are under `/var/lib/gentoo-optimization/recovery/boot`; see `phase-0-config-modules-efi-archives.log`.
- A uniquely named, non-managed recovery generation exists at `/efi/EFI/Gentoo/recovery/pgo-known-good-20260710`. Custom entry `Boot0004` (`Gentoo PGO Known Good 20260710`) references its preserved kernel and initramfs; custom BootOrder-neutral entry `Boot0005` (`Gentoo PGO Rescue Shell 20260710`) references the same assets and adds `rd.break=pre-mount`. The rescue entry was created with `--create-only`, so it did not enter `BootOrder`. Both entries use the now-boot-proven kernel/initramfs pair, and `lsinitrd` validates the rescue userspace.
- The authoritative recovery identity is now the root-owned mode `0600` manifest `/var/lib/gentoo-optimization/recovery/authoritative-known-good.manifest` (SHA-256 `dbeb5fd9c4b15479c32909eae0c6866c25d9747b439897eb0d16eda28ec7f4e0`). Its strict schema identifies independent `Boot0004`, the two exact `/efi/EFI/Gentoo/recovery/pgo-known-good-20260710` paths, and their hashes. Unknown, duplicate, missing, unsafe, symlinked, non-independent, malformed, or non-recovery-tree identities fail closed. The managed `Boot0200` generation is available only through the explicit `--legacy-managed-default` fallback or a complete explicit identity override.
- `preserve-boot` writes a separate loader-compatible schema-v1 authoritative candidate rather than silently replacing the proven identity. The candidate is promoted only after a successful boot; its exact loader/EFI/hash round trip is fixture-tested.
- `BootNext=0004` was armed and the machine actually rebooted from starting boot ID `6be5e262-683f-449b-83ff-d421e47fcca7` into boot ID `986afeaa-bfac-4815-80a4-9459bf4e080f`. Host-level evidence proves `BootCurrent=0004`, kernel `7.1.2-cachyos2`, root `/dev/nvme0n1p5`, writable XFS root, writable `/efi` on `/dev/nvme0n1p1`, matching kernel/initramfs hashes, clean OpenRC services, and successful Portage/Python/shell/C/C++/network probes. The authoritative pass record is `/var/lib/gentoo-optimization/reports/recovery/boot-evidence/20260710202218-phase0-known-good-recheck-20260710T201800Z-986afeaa-bfac-4815-80a4-9459bf4e080f.log` with zero probe, validation, and total failures.
- The first automatic post-boot capture is deliberately retained as failed evidence rather than hidden. Every boot identity and asset check passed, but the hook initially treated OpenRC's documented empty-set `rc-status --crashed` exit status 1 as a probe error. The completed marker was archived as `boot-validation-phase0-attempt1.completed`; the hook now accepts only the empty-output status-1 case, still rejects any reported service name, and has regression tests for both outcomes. The installed corrected hook SHA-256 is `14d0d97c650c6375b56b6fe83744f363266551506fba1289f16e2575801ef73b`.
- `thermald` was removed from the default runlevel after a foreground diagnostic proved that it exits with `Non mobile platform` on this desktop i5-10600K. This is a technically justified service-policy correction, not a hidden boot failure; evidence is `phase-0-thermald-service-remediation.log`.
- Primary evidence logs: `phase-0-state-layout.log`, `phase-0-portage-archive.log`, `phase-0-system-toolchain-info.log`, `phase-0-absolute-toolchain-versions.log`, `phase-0-active-kernel-config.log`, `phase-0-filesystem-capacity.log`, `phase-0-efi-boot-entries.log`, `phase-0-esp-image-backup.log`, and `phase-0-known-good-kernel-preservation.log` in `/var/lib/gentoo-optimization/reports`, plus starting world/set/CPV records in `/var/lib/gentoo-optimization/inventory`.
- Full snapshot construction and coverage evidence is in `phase-0-full-quickpkg-snapshot.log` and `phase-0-full-snapshot-coverage.log`.
- A fresh independent read-only refresh ran the requested `efibootmgr -v`, `rc-update show`, and protected-snapshot listings. It proves that at the 2026-07-12 refresh the normal boot was `BootCurrent=01FF`, independent recovery entries `Boot0004`/`Boot0005` remained present, and both starting snapshot copies remained root-owned mode `0700` with exactly 1,181 GPKGs. This does not replace the earlier actual `BootCurrent=0004` recovery-boot proof and `rc-update show` is configuration evidence, not runtime service-health evidence. The corrected log is `/var/lib/gentoo-optimization/reports/independent-live-verification-refresh-20260712-corrected.log` (SHA-256 `3bc033d54c14a83951fb08fbeb4f7db40a0057c631993be46b1c50d2b3f0a39b`). The first capture is retained with SHA-256 `e167cadcf5be220a0e69be477b308f17827e2f935350164602cde1e9608bb3c4` because it queried the wrong `/var/lib/.../critical-current` path before the corrected `/var/cache/.../critical-current` check.
- [x] Create and restoration-test a second exact current-system checkpoint before Phase 2 while retaining the immutable 1,181-CPV baseline.
- The new cache checkpoint `/var/cache/gentoo-optimization/binpkgs/snapshot-pre-phase2-20260712` and durable copy `/var/lib/gentoo-optimization/recovery/binpkgs/critical-pre-phase2-20260712` are root-owned mode `0700`. Each independently verifies 1,217 indexed/live CPVs, 1,217 outer GPKG manifests, and 1,217 embedded zstd payload streams with zero missing, extra, unindexed, archive, or payload failures. `critical-current` was atomically retargeted only after both passes; both original 1,181-CPV generations remain intact. An actual offline `--usepkgonly` restoration of `app-admin/ps_mem-3.14-r1` selected one binary reinstall and zero downloads, retained the command hash/version, passed `equery check`, and was followed by a third 1,217/1,217 verifier pass. Evidence is `/var/lib/gentoo-optimization/reports/phase-1-pre-phase2-checkpoint-20260712` (manifest SHA-256 `150857821814fc01659c99c24c07d1d9e7bfb63cd70918facd12f82ad0c4e0a5`); state is `/var/lib/gentoo-optimization/state/project/pre-phase2-binpkg-checkpoint.json` (SHA-256 `af0fc3ba431925ec37b4872353ab3c78231e0df2bed0cc2fde1a121ad1332de2`).

## 9.4 Establish a rollback command file

Create and test a documented recovery sequence that can:

1. disable all optimization package.env files;
2. restore critical binpkgs;
3. rebuild preserved libraries;
4. regenerate initramfs/bootloader configuration when required;
5. return to the known-good kernel.

Do not proceed until the rollback path has been tested.

- [x] The documented rollback sequence is implemented and restoration-tested. Its zero-override identity comes from the authoritative manifest and selects independent `Boot0004`; a live read-only preflight validated all 1,181 protected records and the exact assets (`rollback-20260711T012123Z-1181179.log`, SHA-256 `abae78aff70f516bba839233cdeee73e40d9e5d79a98801b9cf5b9a952503b50`). A live zero-override `--dry-run all` ended with `efibootmgr -n 0004` and contained zero `Boot0200`, managed `*-old`, or `/boot/` references (`rollback-20260711T012430Z-1191925.log`, SHA-256 `00688b83a6302fdca32a258651f4b7e26f4d0d3f7382643be1dd0cc027370bcc`). `app-admin/ps_mem-3.14-r1` was actually restored offline from the snapshot, and the independent Boot0004 path was actually booted.
- [x] The recovery kill switch preserves the installed C++ ABI through separate conservative lanes. Its Clang lane retains libc++, LLD, compiler-rt, and libunwind while clearing project PGO/BOLT/LTO/Polly/OpenMP/visibility axes; its GCC lane retains GCC/binutils and libstdc++. The fixture actually compiles, links, and runs both C++ programs, verifies `libc++.so.1` with no `libstdc++` for Clang and `libstdc++.so.6` with no `libc++` for GCC, repeats every live `gcc.conf` selector after the global Clang assignment, and also proves explicit-only legacy Boot0200 plus authoritative-candidate round trips. Bash syntax, a freshly hash-verified ShellCheck 0.11.0 with zero diagnostics, boot-evidence fixtures, and 10/10 Python tests pass. The compact evidence record is `phase-0-recovery-review-remediation-summary.log` (SHA-256 `a5a1e292ed05c614e279e91b215c75d681106a9d75a0772b83f48dfa48933d0c`); live Phase 0 state is `/var/lib/gentoo-optimization/state/project/phase-0.json` (SHA-256 `1042dbac14d0fb160503f7932816083b3b160222711b4a2db0bae526689be8dc`).

---

# 10. Phase 1 — Validate hardware and tool capabilities

## 10.1 Validate perf branch-stack support

- [x] Confirm the i5-10600K exposes usable Intel LBR support.
- [x] Run a small `perf record -e cycles:u -j any,u` test.
- [x] Confirm `perf report` contains branch-stack data.
- [x] Confirm kernel permissions permit the required system-wide and user-space profiling.
- [x] Record any temporary `perf_event_paranoid` changes and restore policy after profiling.

### Phase 1.1 evidence

- The i5-10600K is Intel family 6 model 165; the CPU PMU reports `pmu_name=skylake` and `branches=32`, and boot diagnostics report `Skylake events, 32-deep LBR`. The active kernel has `CONFIG_PERF_EVENTS=y`.
- Exact user capture with `perf record -e cycles:u -j any,u` produced 11,443 samples; all 11,443 carried decodable branch stacks, with 366,145 entries and a maximum depth of 32. `perf report` decoded the fixture's `main`/`mix0`–`mix3` branch pairs with zero lost samples.
- An unprivileged system-wide user-space capture produced 51,673 samples with 1,643,410 decoded branch entries and maximum depth 32, proving the permissions needed for the later `-a -e cycles:u -j any,u` sessions.
- `perf_event_paranoid=-1` and `kptr_restrict=0` were unchanged before, during, and after validation; no temporary sysctl change occurred. The nonfatal perf metadata/libbpf and absent `/proc/schedstat` warnings did not affect branch capture or decoding. Evidence and checksums are under `/var/lib/gentoo-optimization/reports/phase-1-perf-lbr`; `validation-summary.log` reports `result=PASS`.
- The complete perf/LBR evidence tree is now `root:root`; every directory is mode `0755`, no non-root entry remains, and a before/after content-manifest hash proves that ownership remediation did not change any evidence payload. The audit is `/var/lib/gentoo-optimization/reports/phase-1-perf-lbr-ownership-remediation.log` (SHA-256 `fa31e74d9f9b4c8d68b9850b487c15deec8cc5322151b626055fd6b98358fc58`); the updated capability state is `/var/lib/gentoo-optimization/state/capabilities/perf-lbr.json` (SHA-256 `aab12248c34b91cf8578c0edb11d5e37c8c8c192e804caccec1cdb36b8e076df`).

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

- [x] raw profiles are written by multiple processes;
- [x] the merged profile is readable;
- [x] the final link succeeds with the system ThinLTO setup;
- [x] profile mismatch diagnostics are visible and not blindly suppressed.

### Phase 1.2 evidence

- The active Clang/LLD/`llvm-profdata` 22.1.8 toolchain built a multi-translation-unit executable and DSO in both generation and use modes while retaining the live ThinLTO/unified-LTO axes. Six concurrent processes produced 12 nonempty `%m-%p.profraw` files across two instrumented module signatures.
- The merged LLVM IR profile contains 18 functions and has SHA-256 `092e0fbe18323e052de45a1d9bbe5abb75284155220ddeda1009f83a9f06bea0`. The profile-use DSO and executable have SHA-256 `242bb932df23d83b0ec2296cba14dd44950bb2bb0e2faeace1fe1e36c45df4ee` and `3735c1f3f73912cd68316747638c22a25e7d78ec827fa86a1fe25159cb19fda2`; the embedded LLVM LTO section and four cache entries prove the final link used the intended path.
- A deliberate control-flow mismatch failed with exit 1 and the visible backend-plugin hash-mismatch diagnostic. Repeated runs retained stable command, summary, merged-profile, and final-output identities. The fixture passes `bash -n`, ShellCheck 0.11.0, its evidence checksum manifest, and functional tests.
- Authoritative evidence is under `/var/lib/gentoo-optimization/reports/phase-1-clang-ir-pgo`; the independently produced superseded run is retained beside it. The capability record is `/var/lib/gentoo-optimization/state/capabilities/clang-ir-pgo.json` (SHA-256 `a809b6353a5ec3558a65ea27a55f406666c5ece921e7e5167a101f8677ad3a8a`).

## 10.3 Validate sample PGO

- [x] Build a sample-mapping-ready test binary.
- [x] collect `perf.data` with branch stacks;
- [x] convert it with `llvm-profgen`;
- [x] rebuild with `-fprofile-sample-use` and `-fsample-profile-use-profi`;
- [x] verify the profile is not accepted through `-fprofile-use`.

### Phase 1.3 evidence

- A two-translation-unit PIE built with ThinLTO, build ID, emitted relocations, line tables, unique internal names, and pseudo-probe/sample-profile mapping flags. `perf 7.1` recorded `cycles:u` with `-j any,u`; its metadata explicitly reports `BRANCH_STACK` and `USER|ANY`.
- `llvm-profgen` produced a readable five-function extbinary sample profile with SHA-256 `0a4c5b06ad86b12eb0512215f3ca0391d6195c10510b148984d894e073bda955`. The exact profile-use rebuild accepted both required sample-use flags and produced the same `dad2bb283c577f34` workload checksum as the training binary. Passing that profile to the IR-instrumentation consumer failed with the expected bad-magic diagnostic.
- The final hardened rerun emitted 38 retained diagnostics: `0.33% (59/17887)` of samples crossed function boundaries and `1.13% (203/17887)` had reversed or long ranges across an unconditional jump. This capability result is accepted because exact-binary conversion, profile parsing, profile use, and functional equality passed; these diagnostics remain inputs to the conservative Phase 10 profile-quality threshold rather than being hidden.
- Authoritative evidence is under `/var/lib/gentoo-optimization/reports/phase-1-clang-sample`; all earlier harness and pre-safety-review runs are retained under distinct report directories. The runner now refuses output outside `/tmp` and `/var/tmp/gentoo-optimization` and records its own SHA-256. The capability record is `/var/lib/gentoo-optimization/state/capabilities/clang-sample-pgo.json` (SHA-256 `e9014ea7d4e9ab8bc1b2c2f2b05421ead1f52483aaea34966f76b0698abc7f6c`).

## 10.4 Validate GCC PGO separately

- [x] Build a GCC test program with GCC generation flags.
- [x] train it and rebuild with GCC use flags.
- [x] verify the profile directory and correction/mismatch behavior.
- [x] confirm no LLVM profile file is involved.

### Phase 1.4 evidence

- The exact active `sys-devel/gcc-17.0.9999` compiler built a four-translation-unit PIE/DSO fixture with an absolute GCC-specific profile pool. Six training executions produced four `.gcda` files, each reporting `OBJECT_SUMMARY runs=6`; four IPA profile dumps prove that the use build read edge counts and made profile feedback available.
- Generation, normal profile-use, and corrected-profile workloads are functionally identical. The original profile pool remained byte-identical across use builds, and the final reviewed use executable/DSO have SHA-256 `5d6024bc6d7e4684fc5441b7ae70127c3151dba7ee41051a1927b68793943ed7` and `8eb0630ca44a71d112cc23d726609e610e3683419dbff29ed04d11422640c661`.
- A deliberately inconsistent `.gcda` failed without correction and succeeded only with GCC's visible `correcting inconsistent profile data` path. A changed control-flow hash and an empty absolute profile pool each failed with exit 1. `LLVM_PROFILE_FILE` was unset and no `.profraw`, `.profdata`, or `.prof` file participated.
- The authoritative report is `/var/lib/gentoo-optimization/reports/phase-1-gcc-pgo`; the agent-original and pre-ShellCheck root-review runs remain in separate superseded directories. The reviewed runner passes ShellCheck 0.11.0 and its exact source manifest. Live capability state is `/var/lib/gentoo-optimization/state/capabilities/gcc-pgo.json` (SHA-256 `98385f629ed427d045d1bc636da907ab5839c7949dc24ee96c344b3d6ddcfe9e`).

## 10.5 Validate Rust PGO

- [x] Build a small Cargo project with absolute `-Cprofile-generate` path.
- [x] use `--target` so build scripts are not instrumented accidentally;
- [x] train, merge with compatible `llvm-profdata`, and rebuild using `-Cprofile-use`;
- [x] enable missing-function warnings for validation.

### Phase 1.5 evidence and binding validator decision

- Active `rustc 1.99.0-nightly` commit `3659db0d3e2cd634c766fcda79ed118eca31a9fd` reports bundled LLVM 22.1.8, which was explicitly paired with `/usr/lib/llvm/22/bin/llvm-profdata`. Cargo received the explicit `x86_64-unknown-linux-gnu` target; recorded invocations prove target crates received the absolute instrumentation/use flags while the host build script received neither.
- Six concurrent processes produced six distinct `%m-%p.profraw` files. The compatible merger produced a readable 14-function profile with SHA-256 `b310ea88a5db5b8d0e7655c470c0d01f530d8cebfe3517ae9d57b9e8713b3dc3`; the profile-use build was diagnostic-clean and functionally matched the generation build.
- `-Cllvm-args=-pgo-warn-missing-function` was verified with a feature-changed build that emitted 14 visible missing-function warnings. Critically, this rustc returned success for both that mismatch and a malformed indexed-profile file even with `-Dwarnings`; the exact compatible `llvm-profdata show` rejected the malformed input with exit 1. Therefore Phase 2 must validate Rust indexed profiles externally and fail closed before invoking rustc—compiler exit status alone is not acceptable proof.
- The complete authoritative report is `/var/lib/gentoo-optimization/reports/phase-1-rust-pgo`; failed attempts 1–4 and superseded passes 5–6 are retained separately. Live capability state is `/var/lib/gentoo-optimization/state/capabilities/rust-pgo.json` (SHA-256 `069a12c2a89383552c5d2c7bb9f65e345824b658d065d70bf9f443e2e6a8d2ad`).

## 10.6 Validate Go PGO

- [x] Build a small Go command.
- [x] generate a CPU pprof workload profile.
- [x] rebuild with an explicit absolute `-pgo=<profile>` path.
- [x] verify the build log proves PGO was enabled.

### Phase 1.6 evidence and binding validator decision

- Active `go1.27-devel_e37f65a2e6` built a dependency-free baseline command and collected a 56-sample CPU pprof profile. The profile's mapping matches the exact baseline path and available GNU build ID `1d50ccfd0a3af949c79e4974722bfda4380309b3`, and the decoded data contains target workload symbols, nonzero function start lines, and reconstructed inline frames. The language-native `go tool buildid` identity is recorded separately.
- The use build consumed the explicit absolute `-pgo=/tmp/phase-1-go-pgo-20260711T-buildid-reviewed/cpu.pprof` input. Its trace contains the workload package's processed `-pgoprofile` compiler argument and a profile-driven hot-node budget decision; `go version -m` retains the exact absolute PGO build setting. Baseline and use outputs match, and the use binary has SHA-256 `ad6672ec98c937d27eb4821f6f1e71c5f260b96383a576e0eba302649e5b5cb9`.
- Malformed pprof data failed with exit 1. Critically, a structurally valid profile from the unrelated fixture command was accepted by the compiler with exit 0 even though it contained no target symbols. Phase 2 must therefore validate target symbols, function metadata, and available mapping/build identity evidence before enabling `-pgo`; Go compiler success alone is not profile-relevance proof.
- Go PGO eligibility now requires the Go build ID but accepts any supported nonempty hexadecimal GNU build-ID encoding when a GNU note exists; it does not require a GNU note at all. BOLT exact-input eligibility remains a separate stricter decision. The complete authoritative report is `/var/lib/gentoo-optimization/reports/phase-1-go-pgo`; the pre-review report, low-sample failure, and superseded passes are retained separately. Live capability state is `/var/lib/gentoo-optimization/state/capabilities/go-pgo.json` (SHA-256 `78dad9e807e22b89e393155bc7b7077681c0a18d615328c526cd0ae626c980e1`).

## 10.7 Validate BOLT on executable, PIE, and DSO fixtures

- [x] Install the package-managed `llvm-core/bolt-22.1.8` tools and verify their exact runtime identities and dependency closure.
- [x] Pass the fixed-address `ET_EXEC` fixture and preserve its required ELF, metadata, and functional properties.
- [x] Pass the PIE fixture and preserve its required ELF, metadata, and functional properties.
- [x] Pass the shared-object fixture and preserve its required ABI, ELF, metadata, and consumer behavior.
- [x] Record exact profiles, inputs, outputs, hashes, structured timeout rows, and machine-readable zero-pending capability state.

For each fixture:

- link with symbols, build ID, and `--emit-relocs`;
- collect branch-stack data;
- run `perf2bolt`;
- run `llvm-bolt`;
- verify `.note.bolt_info`;
- run functionality tests;
- compare ownership, mode, xattrs, and dynamic dependencies.

Do not implement the live deployment hook until all fixture classes pass.

### Phase 1.7 packaging and capability completion evidence

- `pkgcheck 0.10.40` reports no findings for `pkgcheck scan --repo codex-local llvm-core/bolt` at tested code commit `a97a5dbcffdfd74b03ae151a0e77dee67aa0d6a2`. The exact ebuild, Manifest, and metadata hashes are retained in `/var/lib/gentoo-optimization/reports/phase-1-bolt-pkgcheck-20260712.log` (SHA-256 `c1ccc0995643ab8fe7e0b8541602f65e56f4db1ba412b6f98ae2e5a4e7b6563e`). This proves repository lint only; it is not evidence that the tools install or that any fixture class passes.
- The first explicit static-closure stage build was externally interrupted with exit 143 at Ninja step 876 of 1,825. Its root-owned log remains `/var/lib/gentoo-optimization/reports/phase-1-bolt-portage-stage-build-explicit-closure-20260712.log` (SHA-256 `35845dc05e0f45d40ddc5c893c32db148d59abe31b7209a6ccd1fbc67fd561ed`) with its status record beside it; no package was installed and no partial output is accepted. The superseded four-pending state is retained as `/var/lib/gentoo-optimization/reports/superseded-phase-1-bolt-running-state-20260712.json` (SHA-256 `1a62f9a480ad1a9022db2d32a3f3c0d456f4c05bd9a44e2d2606d067ae68aeb0`). The successful clean retry and current zero-pending capability state are recorded below.
- The cron-isolated clean retry completed all 1,825 Ninja steps plus install/package with exit 0. The build log SHA-256 is `b6f2b749e7c8122ce665047ff9b18258ecfec8533d436dadd38eee9c7c96d5c2` and status SHA-256 is `7ce78c5ccefbe5755a83c4b95da0b02437f9af5d7eff2a0637d0d44ef413a296`. Staged inspection proves both tools are ELF64 x86-64 PIE with IBT/SHSTK, NX stack, RELRO/NOW, resolved libc++ dependencies, and no RPATH/RUNPATH, `libLLVM`, or `libstdc++`; `perf2bolt` is the intended relative argv[0]-dispatch symlink. Evidence is `/var/lib/gentoo-optimization/reports/phase-1-bolt-retry-readonly-inspection-20260712.log` (SHA-256 `b8b58c1c580ee2f479130579caf4f54346f4b74abd6607912b6667e2a80d1a05`).
- The GPKG has SHA-256 `6ad2c8ee905cec697c0c99a306b870445b1432d4f8793fcca0d11ca0c77196f2`; Portage's GPKG implementation verified its Manifest and all nine extracted payload entries. The exact binary-only pretend selected one new package and zero downloads, and the exact binary-only install completed one of one. A root-only reflink is preserved under `/var/lib/gentoo-optimization/recovery/binpkgs/phase-1-bolt-20260712`. Live VDB ownership and file checks pass. Installed `llvm-bolt` has build ID `5d69c312203f61c9ae92fd7978f0cc2b5d78e6fc` and SHA-256 `6a5fc31e4c840586129125a4f6c0205089d9797d2d2f09f7e13d2fb1019dbcad`; `merge-fdata` has build ID `01479a9a4d5ab7ee1f8afca002d2cc78a634e178` and SHA-256 `9729d47832dd417902057642bcfb589eaee1454807065911aca35310031f1214`. Install and live-verification log SHA-256 values are `cc8f89ca36ab92e7b437292c07d3291093f443a7ce99de79b916d842b47e7ab8` and `c3233aed6d3c2aaf6b6c099fe611ec7d95fc6d19d1cf9b7321676f837370d13c`.
- Three fail-closed fixture attempts are retained rather than hidden. The first proved that placing LLVM tools before `/usr/bin` incorrectly shadows GNU `readelf`; the second rejected deprecated numeric `-icf=1`; the third proved LLVM 22's `-use-gnu-stack` workaround consumes the explicit `PT_GNU_STACK` header. The binding policy at tested code commit `bfebd22151f16a6e519cd39e5f55ff6529761b2c` is `-icf=safe` with `-use-gnu-stack` disabled. A direct control proved normal `strip --strip-unneeded` preserves `PT_GNU_STACK`, `.note.bolt_info`, `.text`, metadata, and functionality without that workaround.
- The exact validated layout policy is `-reorder-blocks=ext-tsp -reorder-functions=cdsort`. Authoritative `commands.log` rows prove that literal spelling ran for ET_EXEC, PIE, and DSO and is embedded in the retained BOLT notes. The Phase 11 default and explicit-output prototype now use `cdsort`; `hfsort+` is not treated as an equivalent tested spelling. A quick-suite static gate rejects `hfsort+`, invalid `cdfsort`, `-use-gnu-stack`, missing tokens, duplicates, and ordering drift.
- The authoritative driver reports 44 passes, zero failures, five explicit non-selected capability skips, and 49 unique rows. ET_EXEC, PIE, and DSO each use two profiles; all 27 timed stages completed with no timeout. Across six conversions, sample counts are 1,135–1,239, branch-stack entries are 36,314–39,634, ignored samples are zero, mismatch ratios are 1.2–1.3%, and out-of-range ratios are 0.5%. Every output and its stripped copy has a BOLT note and passes identity, GNU-stack/RELRO/CET, dependency, ownership/mode/xattr, functionality, and applicable DSO-export checks. Root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-1-bolt-authoritative-20260712` (manifest SHA-256 `13960d54c7fac63e8446f166f1a7b169ca5e73dc3e7355399d1415f86d0cd9f3`, results SHA-256 `361542af36ce5c4fd6870317660141fde0d50af6ce7446d51ad5b1f4c9778d84`, validation-summary SHA-256 `dc275b7ee9f005cd03deaecbaccf3578c3c4c08761c34d71f1d5ed26d80ca3b4`). Capability state is `/var/lib/gentoo-optimization/state/capabilities/bolt.json` (SHA-256 `ec3af0b43c087deb84d6ad4475c88f4ff9ca5412e02feaaab87126b1843021f4`) with `pending_total=0`, `unknown_total=0`, and `failed_total=0` for this fixture gate only.

## 10.8 Keep capability validation repeatable

- [x] Add a single top-level driver for shell syntax, ShellCheck, Python compilation/tests, recovery fixtures, and supported PGO/BOLT capability fixtures.
- [x] Make unavailable capability dependencies produce an explicit `SKIP: <reason>` rather than disappearing.
- [x] Test dependency preflight hermetically so an unavailable capability runner cannot execute accidentally.
- [x] Directly test successful and failed BOLT artifact transactions, timeout exits 124/137, missing/stale outputs, stdout publication, and exact structured status rows.
- [x] Clean an active partial and terminate its complete workload process group on outer `EXIT`/`HUP`/`INT`/`TERM`, while preserving published finals and unrelated partials.
- [x] Derive the timed-stage contract from a declared registry and reject unknown, missing, duplicate, or reordered evidence.
- [x] Bound every top-level test case in its own process group with TERM-to-KILL cleanup, recorded deadline metadata, and longer per-capability overrides.
- [x] Enforce the exact validated BOLT block/function layout spelling in every current command producer with a static drift test.

### Phase 1.8 evidence

- `tests/run-optimization-tests.sh` provides `quick` and `capabilities` modes plus explicit per-capability selection. It confines output to a new canonical absolute directory below `/tmp` or `/var/tmp/gentoo-optimization`, rejects unsafe path characters before creating anything, and preflights every external command used by the selected fixture.
- The non-recursive driver self-test proves canonical unsafe paths are rejected without creation and proves a dependency-incomplete BOLT lane emits exactly one reason-bearing skip, never invokes its stub runner, reports every missing dependency, and exits successfully with zero failure rows.
- The exact post-BOLT-fixture-hardening quick run reports 38 passes, zero failures, six explicit capability skips, and 44 unique result rows. It includes Bash syntax, Manifest-verified ShellCheck 0.11.0, Python source compilation, Python unit tests, the driver self-test, and both recovery fixtures. Root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-1-test-driver-post-bolt-fixture` (tree-manifest SHA-256 `b9a99cc9cc11c0caa0ce6590ceb07c95def85f7d90138746078b210b71ca319d`); ShellCheck provenance is `/var/lib/gentoo-optimization/reports/phase-1-shellcheck-0.11.0-provenance.log` (SHA-256 `a2e4885cd37d7ce70d45dfdbade644521d5bfa8f952c3c4333086c23f2909730`); capability state is `/var/lib/gentoo-optimization/state/capabilities/optimization-test-driver.json` (SHA-256 `2abc5fb04a7b3ea0d27adaa45293d5c4e2f7508a096c587410c1fe4710b98a78`).
- The BOLT transaction implementation is tested at exact code commit `7e23736c3779adb3c553c98841dc49fd9b8c145e`. Three independent direct-fixture repetitions cover atomic generated/stdout success, ordinary failure, synthetic deadline 124 and forced-kill 137, zero-exit missing output, stale final/partial cleanup, exact rows, all four running-stage outer signals, post-rename and post-status signal boundaries, and exact registry order/membership/uniqueness; zero workload process survived. Bash syntax, ShellCheck 0.11.0, and the CLI self-test pass. Root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-1-bolt-transaction-tests-20260712.log` (SHA-256 `aab59df8e5c7d28d1d91fd36cb6b1076f04b3233671b6d869db1bd9fbc876b4a`); state is `/var/lib/gentoo-optimization/state/capabilities/bolt-transaction.json` (SHA-256 `6b3467ba61fb30f4499903b341483bd49e14e19fec753e8ef3a37aaea098d35e`).
- The superseding authoritative quick run at exact code commit `7845ab44737e03057d56fffa4dae3903a65332f3` reports 43 passes, zero failures, six explicit capability skips, and 49 unique result rows. It additionally proves the subprocess-heavy unit tests run without `PYTHONPYCACHEPREFIX`, the live Portage semantic package-policy gate passes, the Clang/libc++ and GCC/libstdc++ recovery probes compile and execute, and the BOLT transaction fixture passes inside the top-level driver. Root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-1-review-final-quick-20260712` (evidence-manifest SHA-256 `fba97be4c59614910d9b2ef539b9ad23f4abf415829387f6f3418c33c7c82948`, summary SHA-256 `0984498be098e8006b142de8e78b96da23e4a72363d952c51125ac0f7d58adcf`, results SHA-256 `445b5ff069d624f8635f775b12184dde98d583cad6fa56aa5ca52fd8ec2e1715`). The then-current Phase 1 capability state was `/var/lib/gentoo-optimization/state/capabilities/optimization-test-driver.json` (SHA-256 `f51319cc8e8e0cf3c8f9c0547ace06bfe43693111cad7828f85da653b6b1f3e4`); the earlier 38-pass aggregate and state are retained only as historical evidence.
- The final Phase 1 boundary driver gives every case a default 1,800-second deadline and 10-second forced-kill grace, supports normalized per-capability overrides, and preserves the compatible three-column PASS/FAIL/SKIP results format. Its hermetic self-test runs a TERM-resistant runner and child, accepts only timeout exits 124/137 as the recorded failure, and proves neither process survives. The superseding root-owned quick run reports 45 passes, zero failures, six intentional non-selected capability skips, and 51 rows; ShellCheck 0.11.0, both recovery ABI lanes, 30 package-policy tests, the strict live Portage gate, the BOLT transaction fixture, and exact `ext-tsp`/`cdsort` policy all pass. Evidence is `/var/lib/gentoo-optimization/reports/phase-1-boundary-quick-20260712-authoritative` (manifest SHA-256 `c18e2c09c80fd4a30a6836e443a6332072de97482d2d33f6854330e893b9dfe2`, summary SHA-256 `f7240a8f19738a03ee06c26337c9eda67e26ad2d7bf5e7ff8b81f323ed09302d`, results SHA-256 `20deb0875f5c007e60f74685973bb5c3ce9c7592aff864242f4c265cc8133fd6`); the Phase 1 boundary driver-state SHA-256 was `6f92a9805d291d84050446af86b561e44d3763b2ffe6d8f3ba754d19b99a15d9`. The earlier aggregates remain historical evidence.

## 10.9 Keep the optimization branch reviewable

- [x] Remove broad plan-ignore rules and prove both plan paths remain visible to Git.
- [x] Preserve unrelated MangoHud/O2 remediation on a separate branch rather than carrying it in the optimization history.
- [x] Audit the Hyprland-related policy against the live VDB, repositories, installed ELFs, retained binpackages, and runtime before deciding whether it is prerequisite or unrelated work.
- [x] Collapse redundant Hyprland/hyprutils environment stacks and remove inactive `include-string.conf` and stale `qtutils` policy without changing the proven installed ABI lane.

### Phase 1.9 evidence

- `.gitignore` contains only targeted transient-file rules; `/plan.md`, `*plan*`, and `./plans/` are absent, and `git check-ignore` reports neither `plan.md` nor `plans/system-wide-pgo-bolt.md` as ignored.
- Unrelated MangoHud/O2 work is preserved at commit `1ec0f6563354956408c2468a05945c4a5be08c74` on `fix/mangohud-and-o2-remediation`; the active optimization `HEAD` contains zero MangoHud paths. Later user-owned Portage edits in the shared worktree remain untouched and are not part of this evidence claim.
- The root-owned audit is `/var/lib/gentoo-optimization/reports/phase-1-review-branch-hygiene.log` (SHA-256 `87ff7c7c749a866c8354b6c21648e45273d4013064d70be2168364ebd98d30cc`); state is `/var/lib/gentoo-optimization/state/project/review-branch-hygiene.json` (SHA-256 `880c4efeb5172ac335a37af691f031b0aa5cd6f5f5ae3c0a3953be5a3345eba5`).
- The Hyprland policy is a live buildability and ABI prerequisite, not an unrelated optimization experiment: 14 installed packages originate in hyproverlay, and the selected Hyprland requires glaze 7, which is unavailable in Gentoo but installed from that overlay. Across 21 audited installed packages, all 41 VDB-recorded ELFs exist and are `lddtree`-clean; 33 directly use libc++, none directly use libstdc++, and Hyprland, XDPH, and hyprpolkitagent were running. Retained binpackages prove severe hidden-visibility export loss: hyprutils 6 to 303/383 exports, hyprwire 4 to 269, iniparser 0 to 28, sdbus-c++ 2 to 420, and re2 0 to 497.
- The confusing stacks are resolved: hyprutils now has only the conservative `O2.conf` terminal tier and Hyprland only `O3-thin-lto-no-libs.conf`. Mandatory overlay, public-ABI, libde265, and XDPH policies remain; hyprutils promotion, muParser `-openmp`, and narrower executable-only XDPH/hyprpolkit lanes require controlled rebuild proof before the Phase 3 freeze. Root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-1-hyprland-prerequisite-audit-20260712` (`REPORT.md` SHA-256 `98f8ff892590f5ebb225e7f6e0a3c433afd1055f25e97c0bfac08220f5c662d2`, manifest SHA-256 `0f96e6e0eda8f626b43fe26489e25ffd5c46eb1ff8540ea0bc62df0ba04f28b1`); state is `/var/lib/gentoo-optimization/state/project/hyprland-prerequisite-scope.json` (SHA-256 `a1045b378fe33c38b03866b9c7ba7afd459f537bf72a1112a02a467b1ba4b36c`). This is prerequisite-scope evidence, not a Phase 2 fingerprint or PGO/BOLT coverage claim.

## 10.10 Validate exact package environment policy before fingerprints

- [x] Reject exact duplicate atom/environment pairs recursively across the package policy tree.
- [x] Reject missing or escaping environment paths, invalid or dead atoms, unreviewed effective overlaps, and forbidden recovery/generated/PGO/BOLT assignments.
- [x] Require exact ordered rationales for intentional multi-environment stacks and exact reviewed compiler profiles with complete, non-conflicting tool tuples.
- [x] Run the semantic validator against the live Gentoo Portage universe and make unavailable semantic validation an explicit reason-bearing skip elsewhere.
- [x] Preserve the three policy changes that alter live resolved flags as an explicit source-rebuild queue rather than claiming them complete from configuration inspection.
- [x] Fail closed when tracked compiler, flag, or stage-marker variables appear in unsupported shell constructs instead of silently ignoring them.
- [x] Complete and verify every queued source rebuild caused by changed effective policy, preserve its successful binpkg, and close reverse dependencies with zero unresolved failures.

### Phase 1.10 evidence

- At the Phase 1 boundary, the superseding strict live validator covered 13 policy files, 138 active lines, 136 exact atoms, 147 atom/environment pairs, 10 reviewed ordered multi-environment stacks, five reviewed compiler profiles, and all 1,217 then-installed CPVs. It rejects `export`, `+=`, multiple/chained/conditional assignments, command substitution, and `source`/dot-source whenever they can mutate tracked tools, `*FLAGS`, or stage markers. Portage semantic validation, 30 unit tests, plain mypy, and the boundary suite passed with zero duplicate, path, atom, overlap, stack, compiler, generated-stage, or unsupported-shell ambiguity. The later read-only 2026-07-16 universe contains 1,220 CPVs and must be revalidated by Candidate A.
- The exact source-only transaction rebuilt `dev-util/colm-0.14.7-r4`, `media-libs/svt-av1-4.1.0`, and `dev-util/ragel-7.0.4-r3` with three source emerges, three completed merges, and zero binary-use markers. Colm and Ragel used Clang with the active GCC 17 libstdc++ lane and no stale GCC-major pin. SVT-AV1 used `O2.conf` on x86 and amd64, and its ebuild-native generation, training encode, and `-fprofile-use` stages ran for both ABIs. Every owned-file check, runtime/version probe, SONAME/dependency/relocation check, and protected GPKG validation passed. Both SVT DSOs lacked GNU build IDs at the Phase 1 boundary; that is explicitly carried into Phase 2 as an exact-input/BOLT eligibility blocker, not misreported as BOLT-ready.
- The first post-rebuild `revdep-rebuild -ipv` exposed a pre-existing unversioned `libnewt.so` edge. A first source retry failed safely because global hidden visibility removed `newt_*`; a public-ABI retry restored 168 exports but was rejected because LLD was misdetected and the SONAME remained absent. The final versioned user patch recognizes LLD's GNU-linker compatibility. Installed `libnewt` now has build ID `b028f92f45fd04764cd120c5f4dc3b364fba3fa1`, SONAME `libnewt.so.0.52`, 168 exported functions, and `whiptail` names that versioned SONAME. `equery check`, runtime help, and the final full reverse-dependency scan pass. Failed attempts and the rejected binpkg remain preserved; successful state is `/var/lib/gentoo-optimization/state/project/newt-soname-remediation.json` (SHA-256 `ac3f676fa68db96975dae30b12ab88541056e2d46ab87f061480f5c4124030e4`).
- Root-owned queue evidence is `/var/lib/gentoo-optimization/reports/phase-1-package-policy-remediation-final-corrected-20260712.log` (SHA-256 `ddea59eaeeadf19756ea2b5c5304479680db6bea7334c62e9c740330ae52a61a`); all four successful remediation GPKGs have verified manifests and payload streams. The reviewed JSON policy SHA-256 was `92ee537829e44e9c2c0fe450baea98996dbff927f1ec6f989b6cf832fd10d611`, the hardened checker SHA-256 was `2e327641d74c83f2b63a26bdb8b175ff9eb0281df48ef9d6787e8b474780f5db`, and the Phase 1 boundary package-policy state was `/var/lib/gentoo-optimization/state/project/package-env-policy.json` (SHA-256 `9c7d565ca77b6d1e580e7566e79dabc07a0f53663bb8a8713d6a1f0b2f43e44c`) with `pending_total=0`, `unknown_total=0`, and `failed_total=0`.

## 10.11 Close the Phase 1 boundary

- [x] Preserve one exact root-owned state record covering every Phase 1 capability and closure gate with zero phase-local pending, unknown, or failed items.
- [x] Record the exact clean implementation boundary commit after all capability, recovery, package-policy, and strict test gates pass.
- [x] Confirm that Phase 1 completion does not establish an optimization generation, freeze the installed inventory, or claim installed-system PGO/BOLT coverage.

The clean implementation boundary is commit `f2b32d357dec78e19d707051480ab8525170704b`; progress-plan commit `b1962e581f68cfe5c9964fa5c9e93e62c8126424` follows it without changing the tested implementation. Aggregate state is `/var/lib/gentoo-optimization/state/project/phase-1.json` (SHA-256 `a0f9ec94a19e31537996559a641d18b75441bd89df721d278c7a180dcfdc021b`) with `pending_total=0`, `unknown_total=0`, and `failed_total=0`. This is a Phase 1 boundary claim only; Phase 3 inventory and final coverage totals remain unestablished.

---

# 11. Phase 2 — Refactor the repository framework

### 2026-07-26 Candidate-A pre-activation checkpoint

- Candidate A is repository/pre-activation implementation only. The current
  branch head is the latest committed implementation revision; every later
  source correction creates a new candidate boundary. No Candidate-A revision has been
  installed as the live candidate or accepted by the complete host and
  production sample-PGO gates.
- No current detached Phase 2 evidence index exists. The four sample-profile
  items in §11.4, the combined automation gate, the framework live gate, and
  the evidence-index gate remain open.
- Phase 2 uses the non-circular two-pass procedure in
  `docs/phase2-production-profile-transaction.md`: commit and live-test
  Candidate A; update the plan and generated claim markers truthfully in
  Candidate B; reinstall B and rerun the entire authoritative gate under a new
  run ID. Only B's root-owned detached index can authorize the phase.
- No optimization generation or frozen inventory exists, and no installed
  package or ELF coverage claim follows from this repository checkpoint.

### 2026-07-29 Candidate-A repository correction checkpoint

- GitHub Actions run `30226349306` tested exact commit
  `1d77b16ada380ade6396c91e33907d7bc13942a9` and failed. The failure is retained
  as superseded diagnostic evidence, not a Candidate-A pass: CI used the
  distribution's ShellCheck 0.9.0 instead of the reviewed 0.11.0 identity, and
  two portable recovery cases overclaimed userspace emulation of kernel
  `unshare --pid --fork --kill-child=KILL` parent-death behavior.
- The current Candidate-A correction scope is intentionally bounded. It fixes the portable
  fake-`unshare` observation path without changing production procfs child
  observation, gives that adapter exact readiness and terminal receipts plus
  bounded same-session drain/rescan, early-watchdog, stalled-watchdog, inherited-
  signal, and post-terminal signal-race coverage, makes restored-content
  conversion-log fixtures use a guaranteed distinct inode and atomic
  replacement, confines raw fork/subreaper ownership to a dedicated
  single-threaded checkpoint test supervisor, separates the deterministic
  portable cleanup adapter from mandatory real-host containment proof, routes
  `git symbolic-ref` through the bounded hardened Git observer, and pins the CI
  ShellCheck 0.11.0 archive and executable by SHA-256. A non-root portable run
  now records the reviewed required skip when a complete live Portage policy is
  root-private; authoritative root execution must still prove that policy and
  cannot accept the skip.
- These corrections are not authorization. The dirty-tree contract check may
  bind the current test topology, but the final clean-commit contract, full
  portable boundary, green CI run for that exact commit, live recovery
  checkpoints, installed Candidate-A gate, supervised sample-PGO transaction,
  frozen Candidate B, and detached evidence index all remain pending. No §11.4
  or §11.7 checkbox is closed by this checkpoint.

### 2026-07-30 Candidate-A execution-identity closure checkpoint

- The test driver now starts through the exact `/usr/bin/bash` shebang and
  loads a reviewed execution core from the evidence policy and tool manifest.
  Authoritative mode requires the policy-declared
  `/usr/bin:/usr/lib/llvm/22/bin:/bin` PATH, invokes the reviewed `bash`, `env`,
  `git`, `python3`, `setsid`, `shellcheck`, `sleep`, and `timeout` entry points,
  and rejects a PATH shadow before any test case. Every reviewed manifest tool
  whose command name is its requested-path basename is resolved through that
  PATH and must select the exact requested entry point; this extends the check
  beyond the eight-tool execution core to utilities such as `readelf`, `nm`,
  `objcopy`, `strip`, `file`, and `cc`. Repository-root discovery uses Bash
  builtins and parameter expansion. Authoritative policy parsing uses the
  absolute reviewed `/usr/bin/python3` bootstrap before binding the complete
  execution core; portable parsing may use another PATH-selected Python but is
  non-authorizing and records the resulting execution identity. The active
  Bash command-line `argv[0]` must be the exact reviewed entry point;
  an inode alias such as `/bin/bash` is rejected. Gentoo's intentional
  `/usr/bin/git` and `/usr/bin/python3` symlink entry points remain the
  requested ABI while their resolved executable bytes are independently
  hashed. An authoritative ShellCheck override may name only the same reviewed
  manifest entry point; it cannot substitute another executable. A portable
  override remains non-authorizing and is recorded.
- Test-run provenance schema v2 records every selected execution-core entry
  point with requested-object lstat identity and symlink target, resolved path,
  version output, file SHA-256, and device/inode metadata. It separately
  records the actual Python runtime and binds the active driver Bash PID, start
  identity, ancestry, executable, and command-line entry point. Start and
  finish re-observe these identities; authoritative runs validate root-owned
  non-writable ancestry at both boundaries. Candidate-B component-state,
  capture, and verification commands independently require their active Python
  runtime to equal the final interpreter reached through the reviewed Python
  entry point; on this Gentoo host that distinguishes the `python-exec`
  dispatcher bytes from the actual `/usr/bin/python3.14` runtime. Git
  cleanliness observation is bounded and runs with system/global configuration
  and fsmonitor disabled.
  Capture and detached verification compare the finalized records with the
  independently observed reviewed tool manifest. An external or
  symlink-escaping evidence policy is rejected at provenance start.
- The checkpoint process supervisor now enumerates `/proc/self/task`
  immediately before its raw fork and fails closed unless the only native task
  is its own PID. The fake-namespace fixture no longer uses Python
  `preexec_fn`; a small executable parent-death wrapper performs the exact
  `PR_SET_PDEATHSIG`/parent recheck/exec sequence. Supervisor, process-group,
  and child receipts publish through private partial files, file fsync,
  `os.replace`, and parent-directory fsync. Focused regressions cover the
  single-task success path, deliberate multithreaded refusal before target
  execution, and refusal when the wrapper's expected parent is already wrong;
  the target never executes in either refusal case.
- The portable live-Portage policy lane no longer accepts a broad
  `PermissionError` substring. It recognizes only a versioned one-line record
  for the canonical root-owned `0600`
  `.gentoo-optimization-source-hash` observation (status 73), or the separately
  verified root-identity-remapped review boundary (status 74). Wrong paths,
  observations, modes, reasons, or additional stderr are rejected, and
  authoritative mode cannot accept either skip.
- Deterministic discovery now binds 33 top-level cases, 58 shell sources, 36
  evidence tests, one dedicated stress test, 201 main tests, and 70 recovery
  tests. The exact evidence, stress, main, and recovery identity hashes are
  `cc631c2bba201c6b754ac6344ee167adde640d6caac083b20fa0bf027f3f7f06`,
  `99dbe6b4806232fcb55e043b38a940d720b28775d387824f2cd98aeed4e65c84`,
  `2bcd4a21b69e2234d4fcdc09ee42ddd613c6ac959a70cb89d97e1585feb18fc4`,
  and `7b7222cb6798c7d7e3379e8544f3dc11625e3bb868be5f933406d7ddb84ed533`.
  The regenerated tracked contract exactly matched discovery. These were
  source-topology facts at that checkpoint, not an exact-commit authorization.
- That checkpoint's non-authorizing validation passed all 36 evidence tests
  with no skips, and the isolated then-current-tree mirror completed all 201
  main tests with
  194 required passes, six exact reviewed required host skips, and one
  diagnostic stress skip. Its 73-test production-transaction subset contains
  71 required passes, one reviewed root-only PID-namespace skip, and the one
  diagnostic stress skip. The complete recovery matrix completed 70 methods
  with 67 required passes and the three exact opt-in host-primitive skips. The
  smoke driver reports 61 passes, zero failures, one dirty-tree provenance skip,
  eight mode-selection skips, zero required-subtest failures, and zero mandatory
  internal skips. The structured ledgers and SHA-256 values are
  `/tmp/candidate-evidence-post-review-subtests.tsv`
  (`3ec986a654333b13f23535a0b125e1c004634d5a7db440d8ee0e38b838b53132`),
  `/tmp/candidate-main-post-review-subtests.tsv`
  (`b6bd423cc0fc0431bf591fef7e5fb58ea3d518c8e0d32e57060ed65d4a35966f`),
  `/tmp/candidate-production-post-review-subtests.tsv`
  (`e6162f4debfef0d7e4adcda21af43fc3452d79242d8564dfd61792d2c4229104`),
  and `/tmp/candidate-recovery-final-subtests.tsv`
  (`079391adacf7d7dbe11e764c371d7e24602d6f6d36b85b5175a9d328ffdaa210`).
  The smoke evidence directory is
  `/tmp/gentoo-opt-smoke-candidate-a-20260730-execution-identity-v4`; its
  summary, results, and subtest SHA-256 values are respectively
  `6cfe41bb7138f64deabee2d825283b8b3267e5052769321d17dfd28473d5aedd`,
  `3a92b35962fd8e489d47c415757de2e8d0666b0c0e7a887729df11211bdf99b1`,
  and `942eb99bca290126a29137292fd375bf8adcc179e09f50fe8a094150cd7b9970`.
  The driver CLI/self-provenance fixture, Bash syntax for all 58 sources, and
  exact hash-pinned ShellCheck 0.11.0 pass. The structured live-policy fixture
  exits zero while surfacing its exact reviewed required `live-portage.policy`
  skip in this managed non-authoritative boundary; the live Gentoo gate must
  execute that branch rather than skip it.
  The post-review driver fixture log is
  `/tmp/candidate-driver-selftest-post-review.log` with SHA-256
  `f78aa9c62eabdfbb7c7baa4db16c1eda7d35321ac066feb75a11fe975fee41b8`;
  its execution-core shadow matrix uses a hermetic reviewed manifest so the
  portable CI host does not depend on Gentoo-only production tool paths.
  These are dirty-tree/mirror diagnostics: the clean exact-source portable
  driver and exact-SHA CI result remain required before installing Candidate A.
- No §11.4 or §11.7 checkbox is closed by this checkpoint. No framework was
  installed, no package was emerged, no optimization generation was activated,
  and the Phase 3 inventory remains unfrozen.

### 2026-08-09 Candidate-A repository-boundary closure checkpoint

- Commit `1a00db30048b999d7f093b008714d9266f7dbb28` passed exact-SHA GitHub
  Actions run `30725879325` with 77 top-level passes, zero failures, 14
  reviewed portable skips, 379 required-subtest passes, zero required-subtest
  failures, 23 reviewed required skips, nine mandatory portable skips, and one
  diagnostic skip. Its root-owned 194-file retained artifact is
  `/var/tmp/gentoo-optimization/ci/1a00db30048b999d7f093b008714d9266f7dbb28/run-30725879325`;
  verified `CONTENT-SHA256SUMS` SHA-256 is
  `e21bd3f512b7c13f183a2a98b95596052afdbcf10a0db887de9ffc3ada6793fe`.
  That green repository boundary is superseded and non-authorizing because it
  predates the exact Portage-3.0.81.1 offline-restore and recovery-timeout
  corrections in its descendant.
- Exact descendant `1729e9a26bcf6d3fff466e05e04dc80c8dc0fbba` was tested by
  GitHub Actions run `30747706125` from 2026-08-02 12:23:08Z through
  12:44:02Z and is rejected. The run recorded 76 top-level passes, one
  failure, 14 skips, 377 required-subtest passes, two required-subtest
  failures, 23 required skips, nine mandatory internal skips, one diagnostic
  skip, 403 total subtests, and exit status one. Its 201-test main suite had
  one failure and four reviewed skips; pinned ShellCheck passed; the complete
  70-test recovery suite passed. The failure was exact: journal visibility was
  used as readiness even though SIGTERM ownership still began inside
  `run_child`, so the coordinator received the default disposition and
  returned raw `-15` instead of the handled exit `143`. The portable
  `framework-installer` fixture also skipped because Ubuntu exposes `runuser`
  at `/usr/sbin/runuser` while the evidence PATH selected `/usr/bin:/bin`.
- The rejected run is durably retained at
  `/var/tmp/gentoo-optimization/ci/1729e9a26bcf6d3fff466e05e04dc80c8dc0fbba/run-30747706125`
  as `root:root`, directory mode `0750`, file mode `0640`. Its 196-row
  `CONTENT-SHA256SUMS` verifies all other files and has SHA-256
  `7ac952778cfad9db440a4bc4fe371a6f84efbc4b873b0a0eb3ea98a61f162e55`.
  SHA-256 values for `summary.txt`, `results.tsv`, `subtests.tsv`,
  `test-contract.log`, `test-run-provenance.json`, the main-suite log, and the
  recovery-suite log are respectively
  `c39e25b2e719de5a8ace5ad957cb9e65286eb66043ea17722017d939b960c108`,
  `3602ee3dc127059322fa8c4e58e8a2fa502ace267470ff5b761e8fa56b60dbcb`,
  `0f452deaf5d77ce3fe345097aff9cbee643c0dd7dbbea1e238a6ddfa3b77b2cb`,
  `1c0b95479700f26507417e169ef8114bcf1e9d4230e5467fb62030332558fda1`,
  `6ff5973fe3e8a2127ee57ae4c5873a979e19305b4d61793698fea6799b23b8fb`,
  `bc060c09192d3a4bc5d556f089516a09cd151a0fee7d135d8232d64de5ffaa16`,
  and `d35b489efbaeec353a0c3d2a5ee8485a40d7cf6926a79dc12f98e28b72ea48bc`.
  This is rejected-predecessor evidence and has no authorization effect.
- The repository correction moves HUP/INT/TERM ownership to the complete
  transaction. It blocks the complete signal set with `pthread_sigmask` while
  installing and restoring dispositions before any pre-arm pause or journal
  publication, and rejects an inherited mask that already blocks any managed
  signal. A pre-commit interruption terminates the exact supervised child,
  restores the exact lock state, preserves any valid prepared passed receipt
  as abandoned evidence, publishes a deterministic `recovered-interrupted`
  receipt when a journal exists, removes journal and child sidecars, and
  returns `128 + signal` (`143` for SIGTERM). Only final receipt rename plus
  parent-directory fsync is masked; the committed state is recorded before
  unmasking, so a pending signal after that boundary is explicitly post-commit
  and bounded cleanup retains the truthful child result. First-signal ownership
  is itself masked and deterministic. Test boundaries cover pre-arm,
  child-before-spawn, post-spawn, child sidecar, authorization, release, wait,
  token scan, receipt partial fsync, final rename, and terminal cleanup. The
  manifest-selected `/usr/bin/python3` suite completes 77
  production-transaction methods: 75 pass and two are the reviewed
  diagnostic/root-host skips. The original journal-visible race also passes 50
  consecutive focused repetitions.
- The recovery verifier no longer streams a GPKG member through an unbounded
  `zstd` pipe. It stages each member in a private `0600` regular file, verifies
  the exact tar-declared size, and runs `zstd --quiet --test FILE` in a private
  session. One monotonic deadline is checked between every synchronous local
  tar/file staging operation and continuously during child supervision; no
  userspace timeout can interrupt a local filesystem syscall which never
  returns. Each stderr drain is deadline-, byte-, and read-count-bounded while
  retaining at most 64 KiB, including continuously writing and early-closed
  stderr cases. Timeout and exceptional cleanup use same-session process-group
  TERM, KILL, bounded reap, and residue checks for the exact reviewed `zstd`;
  the random temporary path is removed from diagnostics and unlinked on every
  outcome. All 19 verifier tests pass independently with `ResourceWarning`
  promoted to an error under both available Python 3.14 and 3.15 lanes. Nine
  new cases cover success, malformed input, pre- and post-consumption hangs, a
  TERM-ignoring parent and descendant, finite and unbounded stderr writers, an
  early-closed stderr hang, and staging-time deadline cleanup.
- Portable CI now verifies that Ubuntu's `util-linux` owns
  `/usr/sbin/runuser`, publishes and verifies the reviewed
  `/usr/bin/runuser` entry point, and requires an explicit
  `framework-installer` `PASS` row. Repository policy also verifies local
  Markdown links; the broken legacy PGO link is corrected; BOLT prototype
  wording is historical; `docs/commit-history-map.md` gives additive
  descriptions to generically named evidence-bearing ancestors and forbids
  rewriting them. The BOLT inventory validator now invokes its trusted Python
  entry point as `/usr/bin/python3 -I -B`; isolated mode alone ignores
  `PYTHONDONTWRITEBYTECODE` and had left a hidden `__pycache__` in the source
  tree during the hook fixture. The three current BOLT wrappers also use
  `-I -B`, while the byte-exact deployed-v1 migration fixture remains
  historical. Focused artifact-tool and repository-policy regressions bind the
  exact no-bytecode argv. The installed pre-Candidate framework still has the
  historical deployed-v1 handoff; it currently has no bytecode residue, but no
  legacy helper may be executed as root before the atomic v2 migration. The
  tracked deterministic contract now binds 206 main tests
  with identity SHA-256
  `7c95b508cc1d5d271358c6da73d0631e15c9fada25c1584dcb2c791a744a31a7`
  and 79 recovery tests with identity SHA-256
  `778339462268073b3435c4de8f8a60124e3bd8e83d5bc5b886217684b74cbe65`;
  the other topology identities remain unchanged. This is working-tree
  implementation evidence only until the complete exact-clean local and
  exact-SHA CI boundaries pass.
- The first complete dirty-tree portable attempt used the driver's default
  2,700-second recovery-case deadline and failed safely when that deadline
  expired while the 57th of 79 recovery methods was running. It recorded 77
  top-level passes, one timeout failure, 13 reviewed skips, 369 required
  subtest passes, one required failure, 23 required skips, ten mandatory
  portable skips, one diagnostic skip, 394 total subtests, and exit status one.
  No functional assertion had failed; the exact recovery process group was
  terminated, the result row recorded
  `process_group_cleanup=terminated-residual`, and the partial result was
  rejected. The root-owned retained tree
  is
  `/var/lib/gentoo-optimization/reports/candidate-a-closure-precommit-timeout-20260809`;
  its 195-row `CONTENT-SHA256SUMS` verifies and has SHA-256
  `3e6e9cb67f1e411c38940fee16abb7e74cf08bc78e0c74026164b71afb1aed91`.
  SHA-256 values for `summary.txt`, `results.tsv`, `subtests.tsv`, and the
  incomplete recovery log are respectively
  `0008bd031de77a1563e5c157f25086ff2379a388299d56b29855746c59015176`,
  `150bdd2277908797ffdde69a93c2bd3d2935bac6c9015b63b15b66645335a861`,
  `07976623bf92dec16b71e1ded11fafd5955d8ce90b882e5859796dceb7cfb119`,
  and `22a585cd47b530681dff417fef346a4195d3e8480949d67ee10b900ae7d79888`.
  This is rejected deadline evidence, not a test pass or authorization.
- A subsequent dirty-tree complete precheck gave the recovery case a bounded
  7,200-second diagnostic deadline and completed it in 1,277 seconds. The full
  driver recorded 78 top-level passes, zero failures, 13 reviewed skips, 394
  required-subtest passes, zero required failures, 23 required skips, ten
  mandatory portable skips, one diagnostic skip, 418 total subtests, and exit
  status zero. All 206 main methods completed in 49 seconds with five reviewed
  skips; all 79 recovery methods completed with three reviewed host skips; the
  framework-installer, pinned ShellCheck 0.11.0, and complete BOLT-hook fixture
  passed. Its `summary.txt`, `results.tsv`, `subtests.tsv`, main-suite log,
  recovery-suite log, framework-installer log, and BOLT-hook log have SHA-256
  values
  `897adac696a4370a597c63bc15b1caf6b27a9da85de0f27d7402bb6a7dba077a`,
  `91bae396633266856edf57d36d644bc378a0c767cd37dfeac2d440a0e352e5b0`,
  `15d50496f0e7d7325a7127e2fd5b7a84e1b9cc388816c0d266fabed78a380c3d`,
  `1f4ffca1e4250eb942d02b57026349de6c1ed03c5abcac2965514318279f0474`,
  `9bc9959641832e06026975f272989166ee69cf4fdf59dc926fad3c61cac78317`,
  `fd48a5e05fcf216fbe69590d32260ceb3677a28f131a85c43c9a5bb2bf2a0429`,
  and `b435e9ea6e057bcbb80c8fe5efa44d13884f1486c4c2811545e336e54d96f9cf`.
  The exact contract and static-contract logs have SHA-256
  `a8bda87cfbc5e2b4ba50838dd5013a5f98efdb62fac2141bbeeb5ed574223061`
  and `0cfdd89b45877f22c328cf8d9a1b3cc89443225e9af81e6d88da78fdba16c050`.
- The current-tree smoke and 18-method checkpoint-smoke reruns also completed
  with zero failures and zero mandatory internal skips. Smoke recorded
  61 passes and nine reviewed mode/provenance skips; its summary, results, and
  subtest SHA-256 values are
  `08ae81573b313ee646ed0a64dfc4fb2255d69705e1062ab44f76397794d7613d`,
  `fd68f95c49ca4db3a6c81570645f98234bcdb1d87d3b15a3e0c25a8c4988e1fb`,
  and `942eb99bca290126a29137292fd375bf8adcc179e09f50fe8a094150cd7b9970`.
  Checkpoint-smoke recorded 62 passes, eight reviewed skips, 81 required
  subtest passes, and no required failure; its corresponding hashes are
  `75ab305383568ff0312164c131f9ec3978168cd75548cb6f1066db298885c749`,
  `4583f0ff98846b9c33c852fd2cda091dd86514f3461b13dd2890c00988f93110`,
  and `6170906cfad5e069be03a589f0df4667a0e9dc1d4bc14b6d26ad684144470b44`.
  No matching process or repository bytecode residue survived either run.
- Those three green outputs are sealed byte-for-byte below the root-owned
  mode-`0750` tree
  `/var/lib/gentoo-optimization/reports/candidate-a-closure-precommit-20260809`;
  files are mode `0640`, all objects are `root:root`, and its 504-row
  location-independent `CONTENT-SHA256SUMS` verifies with SHA-256
  `68f66d1cc4c8fff5fc0068216a94256f365d89fc0359180f1b0bc17d086cf2fc`.
  `NONAUTHORIZING.txt` explicitly records the dirty source state and has
  SHA-256
  `21bfe0c521fbd1e6c1a0cf53f9f1bd0d58174b3e5ab6a98a119a95c6cc13d631`.
  These are explicitly dirty-tree, non-authorizing implementation diagnostics.
  The complete portable run preceded the final plan/runbook wording and this
  evidence update, and none of the runs has clean-commit provenance. They do
  not substitute for the fresh exact-clean local rerun or exact-SHA CI gate.
- A read-only 2026-08-09 refresh observed the same 1,220 live CPVs and sorted
  CPV-list SHA-256
  `a5b75bd995f68d74d869b2d5996dcd345e326741f4d1c329a9dbc876edb630ff`,
  no active Portage mutation, and the old protected 1,217-package selector.
  The reviewed `jsonschema` closure remains absent. No package, framework,
  selector, project state, optimization generation, or frozen inventory was
  changed. The complete live observation must run again immediately before
  checkpoint mutation.
- No §11.4 or §11.7 checkbox is closed by this checkpoint. The exact-clean
  contract/provenance check, smoke, checkpoint-smoke, complete portable gate,
  exact-SHA GitHub workflow,
  two live recovery checkpoints with two exact offline
  restoration/finalization proofs, installed Candidate-A gate,
  supervised sample-PGO transaction, frozen Candidate B, and detached index
  remain required.

### 2026-08-14 rejected predecessor and prerequisite-integration review

- Commit `03e8917dc69a1cda1e9d7063e16b6a2ce0ff61fa` is a rejected
  predecessor. Its exact GitHub Actions run `31543998224` completed checkout,
  portable dependency and reviewed ShellCheck installation, and the Ubuntu
  `runuser` compatibility proof, then stopped before `portable-complete` at the
  authoritative contract check. The tracked contract expected 206 main tests;
  deterministic discovery found 256 with identity SHA-256
  `e3471337c6119f38534dbeaf89e4c301ca22924d41403bd3e7742464bb3a523a`.
  This is a deterministic repository-boundary rejection, not a flaky result.
  The contract must be regenerated only after the prerequisite transaction and
  its test topology reach their intended final source shape.
- That revision implemented substantial but deliberately non-live
  prerequisite machinery in
  `scripts/optimization/recovery/install-jsonschema-prerequisite.py`: durable
  transaction states and reconciliation groundwork, frozen Git/rsync/local
  repository authorities, exact resolver plans, private mutable package roots,
  namespace/PTTY containment, authenticated child control, rollback/recovery
  groundwork, and a semantic fixture suite. Its
  `LIVE_PREPARATION_ENABLED = False` and `LIVE_MUTATION_ENABLED = False` gates
  remain intentional. The old inline runbook path must not be treated as a
  substitute; one completed, immutable, evidence-bound helper path is required
  before any live prerequisite operation.
- The exact run produced no live mutation and no Candidate-A or Phase 2
  authorization. No package, framework, selector, project state, optimization
  generation, or frozen Phase 3 inventory was changed. All four §11.4 boxes
  and all three §11.7 boxes remain open. The plan still contains 80 checked and
  69 open boxes; this implementation/review checkpoint changes no checkbox.
- Portable CI must create an evidence root and exact checkout provenance before
  its static contract check, retain the combined contract diagnostic and exit
  status there, and always upload that root. This preserves evidence for an
  exact SHA even when the driver cannot start. A future green successor still
  must retain the exact checkout, reviewed tools and ShellCheck, Ubuntu
  `runuser` compatibility proof, mandatory framework-installer pass, complete
  portable gate, and exact-SHA provenance.

### 2026-08-22 rejected predecessor and prerequisite-boundary review

- Commit `dcc6b8fc76e1bff20a25a06cda4aadda580b3885` was the latest rejected
  predecessor at this review. Its broad prerequisite/bootstrap/evidence scope is recorded
  additively in `docs/commit-history-map.md`; the misleading narrow commit
  subject is historical evidence and must not be amended or rebased.
- Exact GitHub Actions run `31880559624` stopped at the static authoritative
  contract check before `portable-complete` ran. The tracked contract expected
  36 evidence tests and 206 main tests; deterministic discovery found 37 and
  290. That run proves only stale topology. Separate direct testing—not CI—
  found two obsolete held-lock mock signatures, one obsolete raw-JSON hash
  expectation, a root-fixture/production bootstrap trust conflation, missing
  prerequisite signal-transition proof, absent Candidate-B prerequisite-chain
  semantics, and mechanically executable retired BOLT prototypes.
- The correction boundary is deliberately narrow: make the prerequisite and
  root bootstrap fixtures exact, define and test managed signal cancellation,
  bind pre-checkpoint -> prerequisite success -> post-checkpoint evidence into
  the existing `automation` component, and replace the four legacy BOLT
  entrypoints with fail-closed stubs plus an authoritative regression. The
  authoritative contract is regenerated only after that topology is final.
- `LIVE_PREPARATION_ENABLED` and `LIVE_MUTATION_ENABLED` remain false until the
  final invariant audit classifies each prerequisite property as implemented,
  hermetically proven, host-proof pending, or missing, and every missing item
  is closed. The real pidfd/PID-namespace detached-session teardown, XFS
  exchange, privileged metadata, Gemato/Portage locking, and four-class BOLT
  primitives were the remaining host-capability lanes; their exact results are
  recorded below. Installed-candidate lock integration, disposable/live
  Portage phase identity, authoritative zero-skip execution, and supervised
  production behavior still require the immutable Candidate-A framework.
- The bounded prerequisite correction later committed as
  `9dd5960958c397216dd4c60b6212658f10015dca` had 92 deterministic test identities:
  88 portable passes and four intentional authoritative-host skips. The same
  four host identities pass under an actual-root clean environment and prove
  trusted host tools, pidfd plus PID/network namespace kill-child teardown,
  exact nonblocking VDB and preserved-library-registry lock contention and
  unwind, and full recursive Gemato verification of a copied rsync authority.
  An independent post-fix audit found no remaining P0/P1 defect in the managed
  signal, exact counter, Portage-lock, payload-device, durability, or
  recovery-failed closures. Both live gates remain false, so these results are
  implementation and prerequisite-capability evidence, not a live transaction
  or Candidate-A acceptance.
- The bootstrap publisher fixture now distinguishes explicit production
  authority from effective UID/GID. Its 11 identities pass both unprivileged
  and under an actual-root clean environment while production ancestry trust
  remains strict. The four retired `scripts/bolt/*.sh` entrypoints are now
  fail-closed stubs; the new `no-legacy-bolt` fixture and the current BOLT
  command-policy fixture both pass. These source and test identities must be
  included in the final authoritative contract after the remaining evidence
  semantics stop changing.
- The exact real-host detached-session containment regression
  `test_pid_namespace_kills_escaped_setsid_descendant_before_scan` passes. This
  closes the previously sandbox-only uncertainty for that primitive, but does
  not replace the later installed-framework authoritative gate.
- The actual Gentoo-host checkpoint boundary passes all three opt-in methods
  for file/directory durability, pidfd signaling, and PID/network namespace
  kill-child teardown, and the session/subreaper harness passes all eight
  methods. The two production-profile pidfd and escaped-`setsid` containment
  methods also pass. The corresponding log SHA-256 values are
  `83e65fff562640e52071da04b1d46b7d6404fa61db2d79044e63924294a094bc`,
  `677ee2e850c0aac6e4c160db828ab4af19c26a7a17be46c3501ee8920fbaf8ae`,
  `b66892832c30fd55727f0e6ff8a8ba76409cf5bddaef9d4c41bfa318063de149`,
  and `49b943d806fafd0fa10b909cc4734ecb2d8cc02e03c37f51bc7a3f47699566da`.
- Root preflight exercised `/usr/bin/mv --exchange --no-copy -T` plus `sync -f`
  at all six exact production destination parents on XFS device 66307, with no
  residue; its evidence SHA-256 is
  `d1a1d248df6e0ca8b17dd03f7480814ee447986497dab360446f0bb23b099aba`.
  The authoritative fake-root installer fixture separately passed its two
  required rows with zero skips while using the same production `mv` path.
- The privileged BOLT metadata transaction passes all three root-only methods
  for file capabilities, setuid/setgid hardlink groups, xattrs, deployment and
  rollback. The full root BOLT hook fixture passes user-xattr,
  file-capability, and mixed-ABI rows; its sole reviewed skip is the
  Candidate-A framework-lock integration which cannot execute before that
  framework is installed. The real four-class gate passes ET_EXEC, dynamic
  PIE, static PIE, and DSO with all 36 bounded stages at status zero and all
  479 evidence hashes verified. The metadata, hook-fragment, validation,
  timeout, and complete BOLT evidence-manifest SHA-256 values are respectively
  `0d18cf3017a83f41e7c1d30435734192f752f6ae1adef13386e6cf27f731c2bf`,
  `6220976283463a301409a0989b7382c9e4d8ed08787130900cdcf9a31fdf61aa`,
  `47ae2f5c42c9437d1677e77d01a7ca55d69920856d20b435f1cf818d42f888d4`,
  `a24210bb51f240f661ef9cc2bfe5043398068e30a23a94e3369b13f3fc0cc79d`,
  and `f1768ae63a2b86855e8117c784f4d54bf15ff079acfe19beed3414d67e621cce`.
  These dirty-tree host proofs close capability uncertainty only; they do not
  authorize Candidate A or Phase 2.
- The live `::gentoo` rsync authority remains dated 2026-07-11, older than the
  prerequisite helper's reviewed three-day signed-repository age limit. The
  helper correctly fails closed. Before live prerequisite preparation, perform
  a separately reviewed Phase-2 prerequisite source refresh, then repeat full
  Gemato verification, dependency resolution, VDB/config/tool/activity and
  capacity checks. That refresh is not the Phase-3 repository-sync checkbox and
  grants no permission to begin Phase 3.
- This review changes no checkbox. All four Section 11.4 live sample-profile
  claims and all three Section 11.7 acceptance/authorization claims remain
  open; no package, selector, framework, optimization generation, or frozen
  inventory was changed.

### 2026-08-29 rejected successors and evidence-contract correction review

- Commit `9dd5960958c397216dd4c60b6212658f10015dca` is rejected,
  non-authorizing predecessor evidence. Exact GitHub Actions run `32601934693`
  stopped at the static authoritative-contract preflight before
  `portable-complete`: the tracked 58 Bash, 36 evidence, and 206 main-test
  identities had become 59, 40, and 309, and `no-legacy-bolt` was absent from
  the exact top-level contract. That CI result proves stale topology only.
  Separate direct review—not the aborted workflow—found incomplete repository
  and resolver evidence semantics; both prerequisite live gates remained
  deliberately false.
- Commit `ea191dce00f19db8ea0dda20ded85ab594d40db3` is rejected,
  non-authorizing predecessor evidence. Exact run `33104209384` again stopped
  at the static contract preflight before `portable-complete`; main-test
  discovery had advanced from the tracked 206 identities to 310. Separate
  direct review found a verifier syntax error, stale Git fixture expectations,
  and validator/schema wiring defects. CI did not execute or discover those
  defects.
- Commit `0f91f5ab68d7917206e79d4cb688b3a93ab5f182` is rejected,
  non-authorizing predecessor evidence. Exact run `33170596519` stopped at the
  static contract preflight before `portable-complete`; deterministic
  discovery had reached 59 Bash sources, 40 evidence tests, and 323 main
  tests against the older 58/36/206 contract. Separate direct review found
  that prerequisite-originated digests used incompatible producer/verifier
  JSON encodings, the portable automation fixture invoked production
  root-trust validation on `/tmp` executables, and negative chain tests could
  pass while their untouched base fixture was already invalid.
- Commit `6462859e207b3545d9b699c242250022ad8c9f26` is the latest
  rejected, non-authorizing predecessor. It independently matches the
  prerequisite producer's indented, sorted, newline-terminated digest
  serialization in the verifier, uses a fixture-local executable observation
  without weakening production root trust, and makes each chain-tamper test
  begin from a freshly validated base and require the intended rejection.
  The positive producer-to-verifier chain and its six focused tamper cases
  pass. These changes close the three defects found directly at `0f91f5ab`.
- Exact run `33188537276` for `6462859e207b3545d9b699c242250022ad8c9f26`
  nevertheless stopped at the static contract preflight before
  `portable-complete`. Its deterministic pre-correction discovery baseline is
  34 exact top-level cases, 59 Bash sources, 44 evidence tests, 323 main
  Python tests, one dedicated stress test, and 79 recovery tests. The final
  reviewed contract must also include `no-legacy-bolt` and the four exact
  `jsonschema` prerequisite authoritative-host methods as permitted required
  skips in portable mode; authoritative mode must execute them and remains a
  zero-required-skip boundary. Regenerate the contract only after the bounded
  correction topology is final, inspect its complete diff, and require exact
  deterministic reproduction.
- The first pinned ShellCheck 0.11.0 run after that contract correction exposed
  only missing `SC2016` annotations for the intentionally literal `${ED}` text
  in the four fail-closed legacy BOLT stubs and their exact-content regression.
  The successor adds those narrow annotations without changing a test identity;
  the exact five-file ShellCheck, Bash-syntax, and `no-legacy-bolt` checks pass.
- The first complete main-suite run then exposed one fixture assumption rather
  than a production-policy defect: the driver's deliberate `umask 077` filtered
  a requested `0755` temporary Portage prefix to `0700`. The fixture now applies
  its intended exact mode explicitly; the focused method and all 105
  prerequisite tests pass under the driver umask with only the four reviewed
  authoritative-host skips. Production mode validation remains unchanged.
- This additive review preserves the Phase 2 scope freeze and changes no
  checkbox. All four Section 11.4 live sample-profile claims and all three
  Section 11.7 acceptance/authorization claims remain open. Both prerequisite
  live gates remain deliberately false pending the reviewed gate-enabled
  successor and its exact clean repository boundary. No package, selector,
  installed framework, optimization generation, or frozen inventory was
  changed, and Phase 3 must not begin.

## 11.1 Remove the unsafe global consumer

- [x] Delete or disable `portage/package.env/50-global-pgo` in its current form.
- [x] Remove `pgo-use-if-available.conf` and `pgo-instrument.conf` or replace them with backend-specific mode files.
- [x] Ensure absence of a profile file can never silently change an unrelated package’s compiler flags.

### Immediate legacy-consumer neutralization evidence

- The live `/etc/portage` now resolves through `framework-current/portage`; its selected installed candidate predates Candidate A. In the repository, `50-global-pgo` is assignment-free, the obsolete `pgo-instrument.conf`, `pgo-use-if-available.conf`, and `no-pgo-use.conf` files are deleted, backend-specific files live only below `portage/env/optimization/`, and no generated package assignments are active.
- The exact dispatcher now leaves all flags byte-identical in unset/`off` mode and still fails closed if either stale `PGO_USE_IF_AVAILABLE=1` or `PGO_INSTRUMENT=1` is supplied. The focused dispatcher fixture covers those invariants; the final Candidate-A repository gate must bind the complete Bash-syntax and pinned-ShellCheck results to the exact clean source revision.

> Historical Phase 2 evidence (superseded; not authorization): the dormant implementation deletion was recorded in `/var/lib/gentoo-optimization/reports/phase-1-dead-legacy-pgo-removal.log` (SHA-256 `6acdd8d373f3bd9bd54e6afda5e4a75c5bd5ab8a41995fa8260f98338378f101`) and `/var/lib/gentoo-optimization/state/project/legacy-pgo-neutralization.json` (SHA-256 `aeba82f79f4e6ec7028d9db60a30a4999203d4cfb434f552a1adac928f2e897d`). Those records prove the older revision only. No package build may proceed through the legacy lane.

## 11.2 Rewrite `portage/bashrc`

- [x] Implement and validate the strict backend/ABI/fingerprint dispatcher described below.

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

Active modes require an exact package fingerprint, explicit ABI and compiler family, an absolute profile path, and—for every use mode—a manifest whose backend, fingerprint, ABI, compiler family, validation status, and payload SHA-256 match. Fingerprint schema v3 preserves the effective last-token-wins `FEATURES` state. Mapping and consumer identities remain distinct. Format validation is backend-specific: Clang IR and Rust indexed profiles, Clang sample profiles, GCC gcov directories, and Go pprof data never share a consumer. Flags append exactly once; generation/use disables compiler caches; `FCFLAGS` and `FFLAGS` remain untouched; GCC correction remains confined to the GCC lane; Rust target isolation and the installed Go tool's `go version` interface are tested. Composite capture/deploy stages retain Rust/Go language-lane behavior, preserve the pre-existing Portage hook chain, and invoke only the exact executable wrapper interface during Portage's pre-strip `post_src_install` boundary.

> Historical Phase 2 evidence (superseded; not authorization): the first dispatcher implementation was commit `2b18e6134a5abd85996d48274a492a8080828a0b`. Its 19-case root-owned evidence is `/var/lib/gentoo-optimization/reports/phase-2-dispatcher-hook-chain-20260712` (manifest SHA-256 `0db50cbfe5232d9c9ca5c3874198de483c407a4081dfbc2bcbbeee926dc0d1b7`); component state SHA-256 was `af388f101eb9ac16f5fabcdcc79f54531e7166b06296ccc0b16c09ab295bb288`, tested `portage/bashrc` SHA-256 was `9c8eedc4760d82a5116859abb6be22d9a701fd34d0756495a0758f2464887414`, and fixture SHA-256 was `d21aee1119e932ea8bdb807d6b9693c8634b33733a6649fa671683586d46c09f`. These hashes do not describe Candidate A. `active_generation` remains null and `inventory_frozen` remains false.

## 11.3 Implement stage-specific env files

- [x] Create and validate assignment-only, minimum-marker stage environment files.

Create the files listed in section 4. Each file must contain only mode markers and the minimum flags for that mode. Avoid copying the entire global flag stack into every env file.

The stage files contain only `GENTOO_OPT_MODE`, `GENTOO_OPT_BOLT_STAGE`, or the narrowly scoped readiness marker required by that stage; package fingerprints, ABI identities, generation IDs, and profile paths remain machine-generated state outside Git. Repository tests syntax-check every stage file and prove the files are not assigned to live packages. Candidate A must still revalidate them through the installed live framework.

## 11.4 Fix sample-profile scripts

### 2026-07-26 Candidate-A architecture checkpoint

- Production runtime helpers now live under `scripts/optimization/pgo/`, not
  `tests/`. The root-owned coordinator constructs an exact clean child
  environment, validates every trusted executable identity, contains the child
  in a kill-child PID namespace, and recovers crash windows from one coherent
  PID/process-group/start-time identity. It generates the bearer internally,
  supervises stable framework/project/generation locks, and scans retained
  roots after containment to prove that the raw token did not persist.
- The containment preflight is now functional rather than attribute-based: it
  creates a disposable child, opens a pidfd, signals that exact child through
  `pidfd_send_signal`, verifies the expected signal exit and no process-group
  residue, and combines that proof with a real kill-child PID-namespace probe.
  Production Candidate A/B runs must pass; portable denial is a structured,
  reason-bearing skip only. Recovery reads PID, process group, state, and start
  identity from one `/proc/<pid>/stat` snapshot, and deterministic replacement
  fixtures pre-create a distinct inode before `os.replace()`.
- Production and root-host containment continue to discover the real
  `unshare --fork` child by repeated `/proc/<supervisor>/task/<supervisor>/children`
  observations. Only the seven independent fake-`unshare` unit cases inject a
  fixture-owned observer; it accepts a child PID only after an fsynced receipt
  binds the queried supervisor exactly. This isolates host procfs scheduling
  from portable fault injection without weakening the production path.
- The integration has separate isolated-diagnostic and normal live-Portage
  policy lanes. Only the production coordinator lane can authorize Phase 2.
  The live lane binds the complete effective last-token-wins `FEATURES` state,
  normal sandbox/userpriv/namespace policy, exact direct compiler paths,
  disposable Portage/log/depcache/dist/binpkg/compiler-cache roots, and
  before/after sentinels for the corresponding live roots.
- The repository implementation writes only `sample.prof`, publishes it
  transactionally under the full generation/inventory lock hierarchy,
  validates it with sample-aware `llvm-profdata show --sample`, and records the
  exact mapped binary build ID, `.text` SHA-256, full-file identity, perf
  identity, conversion command, and immutable conversion log. The dispatcher
  has only the `-fprofile-sample-use`/`-fsample-profile-use-profi` sample
  consumer; the IR consumer remains separate.
- Both restored-content conversion-log regressions now construct a second
  regular file, assert that its device/inode identity differs from the recorded
  observation, restore the exact bytes and mode there, and publish it with
  `os.replace()`. They therefore test an observable same-content replacement;
  they no longer claim that a current-state validator can detect perfectly
  restored in-place history without an external historical authority.
- Mapping-input and consumer-build fingerprints are distinct identities. The
  Portage fixture binds the observed mapping receipt, representative perf
  data, `llvm-profgen` conversion, immutable manifest/sidecar, generated
  assignment, exact consumer receipt, runtime equivalence, and tamper/mismatch
  rejection. Every predicted flag, effective feature, USE flag, ordered
  package environment, build axis, and artifact identity must match.
- Production locks and immutable profile artifacts are root:`portage` readable
  without becoming Portage-writable: runtime directories are `0750`, stable
  locks and profile/metadata/manifest files are `0640`, conversion/perf inputs
  are `0440`, and the mapped input is `0550`.
- Repository fixtures exist for syntax, pinned ShellCheck, strict runtime
  typing, unit behavior, transaction recovery, and portable policy. Focused
  current-tree tests are non-authorizing; the exact complete portable boundary,
  green CI run, installed candidate, and supervised live Portage chain still
  must pass. The four boxes below remain open until that live proof exists.

- [ ] Write sample profiles to `sample.prof` or another unmistakable sample-profile name.
- [ ] Validate them with an LLVM sample-profile-aware command.
- [ ] Consume only through `-fprofile-sample-use`.
- [ ] Preserve binary build ID and `.text` hash metadata.

## 11.5 Implement BOLT input capture hook

- [x] Implement and validate non-mutating, hardlink-aware exact ELF capture from `${ED}`.

During `post_src_install`, when `GENTOO_OPT_MODE=bolt-capture` or an equivalent marker is active:

1. enumerate regular ELF files under `${ED}`;
2. resolve hardlink groups without duplicating work;
3. classify ELF class, type, machine, executable sections, symbols, relocations, build ID, and `.text` hash;
4. copy eligible unstripped candidates to the BOLT input cache;
5. preserve relative install path, ownership intent, mode, xattrs, capabilities metadata, and symlink topology;
6. write an artifact manifest;
7. never modify `${ED}` in capture mode.

The capture transaction enumerates every regular inode group and symlink below the supplied staging root, refuses external hardlinks and unsafe/symlinked roots, and copies each eligible inode once with no-atime reads. ELF64 x86-64 `ET_EXEC`/`ET_DYN` readiness requires executable code, a nonempty `.text`, a defined function symbol, full symbols, `.rel[a].text`, and a GNU build ID. The manifest records the full file hash, build ID, `.text` hash, class/type/machine, hardlink/symlink topology, mode/UID/GID, xattrs, and file capability. Automatic failures are deliberately named `readiness_failures`; they remain remediable pending classifications and never become terminal exclusions without separate reviewed evidence. Captured objects are private mode `0600`, and before/after tree evidence proves capture does not mutate `${ED}`.

## 11.6 Implement BOLT deployment hook

- [x] Implement and validate exact-input, rollback-safe BOLT deployment inside `${ED}`.

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

Output registration now requires both the prepared BOLT output and its exact captured input, and rejects a full-file, build-ID, or `.text` mismatch before publishing anything. Deployment requires exact output coverage for all eligible artifacts, validates every input and output before mutation, preserves diagnostic preimages, stages same-inode groups, atomically replaces only staging-tree entries, and keeps final BOLT-note/hash/metadata/topology verification inside the rollback boundary. A forced post-rename verifier failure restores all three fixture inputs byte-for-byte and restores the two-name hardlink group before returning failure. The root-only fixture is designed to prove setuid, a real file capability, user xattrs, ownership intent, hardlinks, symlinks, and runtime behavior survive; its required privileged branch must rerun against the exact Candidate A. The non-root fixture emits an explicit capability skip and exercises the remaining invariants. The tool refuses `/`, `/usr` and descendants, overlapping roots, symlink components, prepared-output symlinks, and installed `/usr` modification.

The current repository architecture retains the 2026-07-15 privilege-metadata remediation but awaits Candidate-A live revalidation. Both deployment and rollback stage in the only safe order: private copy, ownership, every hardlink, ordinary/capability xattrs, and final setuid/setgid mode last, followed by completed-inode-group verification and an inode fsync before rename. The root-only matrix includes `04755`, `02755`, `06755`, user xattrs, `security.capability`, hardlink groups, and rollback, but it must rerun without a privileged-metadata skip against the exact Candidate A. Static PIE is a distinct fourth real BOLT class: `ET_DYN` without `PT_INTERP` but with `DF_1_PIE` is an executable, while an `ET_DYN` object without the PIE flag remains a DSO. The four-class perf/BOLT and root staging fixtures must rerun against Candidate A. Production framework/project/generation locks are root:`portage` `0640` below a root:`portage` `0750` runtime directory; the disposable Portage gate must prove compile phases run as `portage` while `pre_src_install`, `src_install`, `post_src_install`, and the final install-QA hook run as root, so BOLT does not require disabling `userpriv`.

> Historical Phase 2 evidence (superseded; not authorization): §11.5–11.6 evidence was `/var/lib/gentoo-optimization/reports/phase-2-bolt-hooks-20260712` (manifest SHA-256 `1d01fe4e7770ec2ad787c00aa05248281fced4d5946b7b7adb48cf9f59ed7cad`) and component state SHA-256 `57469e99bef2df7a96c7170fe55171b7e8257c27ab5468555b452dbaeb92ecf2`. Its tested artifact-tool SHA-256 was `f43d1a5fe95dae797071a19a25243e5e19eb3e455d7cf9efe67117bd54a2ad9b` and fixture SHA-256 was `af6b37802b79e0814d2a6d6625b4ba15d637953e73ac735ce32980dfeec36225`; none describes Candidate A. No installed artifact has been captured or deployed by this repository gate, and no installed-system BOLT coverage is claimed.

## 11.7 Add automated tests

- [ ] Complete the combined Phase 2 automation gate; repository coverage exists for BOLT classification, mismatch, topology, privileged metadata, no-ELF, mixed-ABI, and the complete sample pipeline, while the exact clean Candidate-A host gate and supervised production transaction remain pending.
- [ ] Install and validate the candidate-complete framework through its exact clean commit, immutable inventory, crash-consistent activation, stable bootstrap ABI, and final `--check` gate.
- [ ] Capture and verify the root-owned detached Phase 2 evidence index at its policy-pinned run-scoped path with exact current-boot, source, tool, test, component-state, and production-receipt identities.

The framework publisher is candidate-complete and crash-consistent: Portage, overlay, helpers, schemas, QA logic, generated policy, and the manifest live inside one immutable candidate. First migration installs a fail-closed Portage guard before its fsynced activation journal; normal upgrades change behavior only through the atomic `framework-current` rename. Each installed bashrc embeds and exports its exact candidate target, and stable shell/Python/QA bootstraps honor that pin, so a build begun on generation A cannot call generation B after an upgrade; re-sourcing another generation fails closed. Candidate Python helpers run isolated with bytecode writes disabled, and a terminal inventory check must prove helper execution did not change the immutable candidate. Fixed bootstrap bytes are an invariant upgrade ABI and a changed renderer is rejected before external publication. The hermetic fixture contains SIGKILL cases on both sides of activation, same-generation re-source, an old-bound process across activation, cross-generation rejection, and incompatible bootstrap migration; the exact final-tree fixture run and live filesystem exchange proof remain pending. This remains a repository/framework claim until the clean live install and combined host gate pass.

Python bootstrap schema v2 uses `#!/usr/bin/python3 -IB` and an exact
`/usr/bin/python3 -I -B` handoff, so bytecode is disabled before standard-library
imports. The only accepted migration source is the byte-exact ten-helper schema
actually deployed by installed framework commit
`8a1200915d2693fd7486a421a9b232f638e9840c`; an independent golden renderer and
ten SHA-256 values bind it. The never-deployed twelve-helper hybrid Git
predecessor is deliberately unsupported. Migration atomically exchanges the
complete helper tree. At both injected `SIGKILL` boundaries the fixture compares
type, relative path, mode, UID/GID, regular-file SHA-256, and symlink target for
the entire tree. After the post-exchange crash it runs strict `--check` and old
and newly added representative helpers before any repair/idempotence pass. The
same exchange path still requires proof on the live XFS destinations.

The detached evidence contract avoids self-referential plan hashes and stale prose authorization. Candidate A is a non-authorizing implementation/live precheck. After A passes, generated claim markers and truthful boxes are committed as Candidate B; the complete gate then reruns against B with no later plan edit. The sole accepted index path is `/var/lib/gentoo-optimization/state/project/phase2-evidence/<run-id>/index.json`. It binds one clean commit/tree, the current boot, active immutable candidate, exact production transaction receipt and validation input, required tool and test identities, eleven immutable run-scoped component states, exact directory membership, and aggregate `pending_total=0`, `unknown_total=0`, `failed_total=0`. Reboot, source drift, candidate drift, partial transaction debris, extra state entries, or any plan correction invalidates authorization and requires a new run ID and complete rerun.

The test driver has distinct `smoke`, `checkpoint-smoke`, `portable-complete`,
`stress`, `capabilities`, and `authoritative` modes. `checkpoint-smoke` selects
18 exact methods: four supervisor containment/release paths, nine portable
fake-`unshare` terminal/watchdog paths, and five checkpoint state-machine/
process-group paths. The complete 79-method recovery matrix remains part of
`portable-complete`. Every top-level case publishes a
structured completion row; every conditional shell branch and every Python
`unittest` method publishes its own required/diagnostic row. Atomic shell
fixtures with no conditional branch are represented by their fail-closed
completion row. Legacy `SKIP-SUBTEST`, `HOST-SKIP`, and Python `unittest` skips
are surfaced rather than hidden by a top-level pass. The exact
top-level topology, exact unittest identities/counts, structured ledger hash,
and zero-discovery rule are reviewed inputs. Authoritative mode requires zero
top-level skips and zero required internal skips; portable skips require an
exact allowlist and cannot grow silently. The detached verifier reloads the
tracked reviewed tool manifest and rejects deleted, duplicated, reordered, or
substituted index tool specifications before re-observation. The reviewed
host/tool boundary includes containment, atomic-publication, ELF,
metadata, hashing, text-processing, Git/tar, compiler, Portage, and profiling
primitives plus the reviewed core `jsonschema` schema-validation distribution
closure (`attrs`, `referencing`, `jsonschema-specifications`, `rpds-py`, and the
conditional `typing-extensions`). The boundary separately binds the stable
bootstrap's requested `/bin/bash` path (in addition to `/usr/bin/bash`) and the
installer's exact `/usr/bin/tr` text-processing primitive.

The test-execution core is a separate reviewed subset of that complete tool
manifest. Repository-root discovery uses only Bash builtins. Authoritative
policy parsing uses the absolute reviewed `/usr/bin/python3` bootstrap before
the complete core is bound; portable bootstrap selection is non-authorizing and
recorded. Authoritative runs use the policy-pinned PATH and exact requested
entry points for every reviewed manifest tool addressable by its command name,
including Bash, environment, Git, Python, session/deadline control, ShellCheck,
bounded polling, ELF/binutils tools, the default compiler, and file inspection,
while portable runs may select another absolute entry point but must record it.
Authoritative Bash must also have the exact reviewed path in command-line
`argv[0]`, not merely matching executable bytes. An authoritative ShellCheck
override must equal the manifest entry point; portable alternatives are
recorded. Provenance binds requested
object lstat/symlink identity, resolved/version/file identity, the active Bash
process, and the Python runtime at both start and finish; the detached index
then requires exact equality with independently re-observed reviewed tools.
Candidate-B state, capture, and verification operations additionally bind their
own active Python runtime after the reviewed Gentoo `python-exec` entry point
has selected its final interpreter, and Git cleanliness is observed by the same
bounded isolated execution boundary. The workflow invokes `/usr/bin/bash` and
`/usr/bin/python3` explicitly and uploads the successful result, subtest,
summary, contract, and provenance evidence as well as failure evidence.

A read-only 2026-08-02 live-host PATH preflight used the reviewed authoritative
`/usr/bin:/usr/lib/llvm/22/bin:/bin` order and proved that all 69
basename-addressable tools in predecessor `44ead3d`'s 77-entry manifest
resolved to that exact requested entry point (`failures=0`). Its focused nine
version probes passed; it did not execute every declared manifest version
probe. The successor manifest at the 2026-08-09 checkpoint had 79 entries and
71 basename-addressable tools after adding the `/bin/bash` bootstrap and
`/usr/bin/tr`; later prerequisite/tool-authority work changed that boundary
again. The final exact candidate requires a fresh complete preflight rather
than inheriting either historical observation.
The root-owned 25-file historical preflight is retained at
`/var/tmp/gentoo-optimization/candidate-a-source-preflight-20260802/live-tool-preflight-44ead3d`;
its relative `CONTENT-SHA256SUMS` verifies and has SHA-256
`e0625f66807a8455387e59c028980de2b112da1075436240efc0c94c97efab30`.
The complete path-resolution TSV is
`/var/tmp/gentoo-optimization/candidate-a-source-preflight-20260802/full-manifest-path.tsv`
with SHA-256
`98008e7f6d880d2ee9a482bcc022cf2a7164839c29d92093a8b4f6a707323c0b`; the
focused LLVM/core TSV is
`/var/tmp/gentoo-optimization/candidate-a-source-preflight-20260802/focused-path.tsv`
with SHA-256
`33cde6f50941cdbec916d8648bfbb0fd95d8dee89f5125abb3765f7eef82fa2e`.
This is a non-mutating historical-host coherence observation only; it does not
substitute for the fresh exact-candidate authoritative or Candidate-B gates.

The Candidate-A correction removes raw `os.fork()` and process-wide child-
subreaper mutation from the multi-test unittest process. A dedicated
`checkpoint_process_supervisor.py` remains in the driver's exact case process
group and requires `/proc/self/task` to contain exactly its own PID immediately
before raw fork. It starts each checkpoint target in a private session, writes
target output to regular files rather than inherited capture pipes, and binds
parent/target PID and start identities. It must drain every recorded live
fixture descendant, reap the exact target and every adopted child returned by
`waitpid`, and publish a typed v4 terminal receipt. The caller independently
rejects any recorded residual PID/start identity that still exists; a reused
numeric PID with a different start identity is not confused with the old
process. The target-release pending-signal observation is the documented
linearization point: an interruption already handled or pending before that
point closes/rejects the release gate, while a later interruption is a
post-commit cancellation that drives bounded teardown. External fixture
barriers exercise pre-fork, pre-release, and masked-pending interruption, and
the receipt proves whether target release was ever committed. The
portable fake-namespace watchdog is a deterministic test adapter only; the
three opt-in root-host checkpoint methods remain the mandatory proof of real
pidfds, `unshare --kill-child`, and filesystem durability. The helper's normal,
interrupted, hard-deadline, malformed-receipt, and exact-driver paths must all
pass before the exact clean Candidate-A repository boundary can be accepted for
live precheck. Phase 2 authorization still requires the frozen Candidate-B
rerun and its detached evidence index.

All Git observations used by the detached evidence verifier now share one
60-second hardened runner with system/global configuration disabled. Attached
and detached `git symbolic-ref -q HEAD` outcomes are handled explicitly as
return codes 0 and 1; an unbounded direct subprocess is no longer used. The
portable workflow no longer accepts the mutable Ubuntu ShellCheck package as
the reviewed identity: it downloads the official 0.11.0 archive, verifies
archive SHA-256
`8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198`,
verifies executable SHA-256
`4da528ddb3a4d1b7b24a59d4e16eb2f5fd960f4bd9a3708a15baddbdf1d5a55b`,
and checks the exact reported version before running the driver. Local success
with both 0.9.0 and 0.11.0 is diagnostic only; the final clean candidate still
requires a green exact-SHA workflow run.

The superseded pre-prerequisite deterministic contract at that checkpoint
discovered 33 top-level cases, 58 shell sources, 36 evidence tests, one
dedicated stress test, 206 main tests, and 79 recovery tests. The exact unittest
identity hashes
are `cc631c2bba201c6b754ac6344ee167adde640d6caac083b20fa0bf027f3f7f06`,
`99dbe6b4806232fcb55e043b38a940d720b28775d387824f2cd98aeed4e65c84`,
`7c95b508cc1d5d271358c6da73d0631e15c9fada25c1584dcb2c791a744a31a7`,
and `778339462268073b3435c4de8f8a60124e3bd8e83d5bc5b886217684b74cbe65`
for evidence, stress, main, and recovery respectively. The superseded
pre-execution-identity 18-method `checkpoint-smoke` run at
`/tmp/gentoo-opt-checkpoint-smoke-candidate-a-20260729-v8` passed with 62
top-level passes, zero failures, eight exact mode/provenance skips, 81 required
subtest passes, zero required failures, and zero mandatory internal skips. Its
`summary.txt`, `results.tsv`, and `subtests.tsv` SHA-256 values are respectively
`f9868614ed3723856bf068a6ad8f2388aa008e73daaef7a61437e9354fe1436e`,
`0150a9b1258b118e270b4ce30b42b31d5c301019dc11d21ef6a44f0267ac3363`,
and `6170906cfad5e069be03a589f0df4667a0e9dc1d4bc14b6d26ad684144470b44`.
Its four supervisor, nine fake-namespace terminal/watchdog, and five
state-machine/process-group methods all passed through the exact project
driver of that superseded source revision.

Commit `44ead3d66670a1ee8a9d3aace8fc0945cbb2d130` then passed exact-SHA GitHub
Actions run `30565522289` (`portable-complete`) on 2026-07-30 from
17:20:22Z through 17:43:21Z. The retained artifact reports 77 top-level passes,
zero failures, 14 reviewed portable skips, 379 required subtest passes, zero
required subtest failures, 23 reviewed required skips, nine mandatory internal
skips, one diagnostic internal skip, and exit status zero. SHA-256 values for
`results.tsv`, `subtests.tsv`, `summary.txt`, the contract log, and provenance
are respectively
`c75637997db3eb50aca200971b253393aff3c9c2ee07511568687edc3345ed81`,
`c26bfa7d890a54da2f0639d563d049ee46eedcd2ebd80f3dfdaee01c42d1a8b4`,
`4d49b0bf0e641086f37d1e73a07e34c23ca173ff9ef14b722e45b84322ef3bb0`,
`94603fd460ca913eec48fbdb14cdeeb1a9da62aaefc0e5b9332c418e21cd092c`,
and `eb51aa3d58410bfedaf90e39fa9f8942d3a6af0a9312ee2152cd377b40eac2c3`.
The root-owned retained artifact is
`/var/tmp/gentoo-optimization/ci/44ead3d66670a1ee8a9d3aace8fc0945cbb2d130/run-30565522289`
(`root:root`, directory mode `0750`, file mode `0640`). Its location-independent
`CONTENT-SHA256SUMS` covers 199 files and verifies; that manifest's SHA-256 is
`b760c7dcb750a319c648c24718226be71b78e07608245178f3174e24f0aee627`.
This proves that predecessor's portable boundary only; it is superseded by the
current plan, runbook, and workflow corrections and never passed the live-host
or supervised production gates. The current branch head therefore requires a
fresh exact-driver `checkpoint-smoke`, complete clean `portable-complete`, and
exact-SHA CI run.

The first complete superseded-tree diagnostic at
`/tmp/gentoo-opt-portable-complete-candidate-a-20260729-v6` proved all 198 main
and all 68 recovery tests, the framework installer, evidence contract, BOLT
hooks, and the remaining executed fixtures, but correctly remained failed and
non-authorizing: old repository bytecode residue predated the run, and the
non-root live-policy probe treated an unreadable root-private installed
candidate as an unexpected error. Its `summary.txt`, `results.tsv`, and
`subtests.tsv` SHA-256 values are respectively
`f20ed2b83f42e8f5c4b69851685935d4e366fab6b9324535622f47d1e8397820`,
`75a39b1ed7cc2cc2b06c8f6e8af5cc1de30cf327bb6971c915f906aba55dd882`,
and `ce1ebb56ca77b1a1d64ce17cb44eb185cc6797e0f6c6665b8b2067ff5ae63963`.
The residue is removed, and a direct rerun of the corrected production-
environment fixture exits zero while publishing the reviewed required
`live-portage.policy` skip. The complete portable boundary and exact-clean-
commit CI result for the current branch head remain pending.

The following non-authorizing 2026-07-26 checkpoint is retained as historical
evidence for the superseded in-process child-subreaper harness; its evidence
and recovery counts and identity hashes are not the current contract. That dirty-tree
stabilization checkpoint had exact
deterministic discovery of 33 top-level cases, 58 shell sources, 31 evidence
tests, one dedicated stress test, 198 main tests, and 56 recovery tests. The
reviewed portable skip policy is sorted and its exact contract check passes.
The new child-subreaper harness passed a 20-cycle fast fork/`setsid` escape
stress, preserved an unrelated baseline child, and restored the caller's prior
subreaper state. The retained stress log is
`/tmp/checkpoint-fast-escape-stress-20260726.log`, contains 20 terminal `PASS`
rows, and has SHA-256
`9a6a1d441ffedbae736b6356e4e615599bd179f021e97afbe0425498fe2454cb`.
The six-selected-method `checkpoint-smoke` run at
`/tmp/gentoo-opt-checkpoint-smoke-containment-final-20260726` passed with 62 top-level
passes, zero failures, eight exact mode/provenance skips, 69 required subtest
passes, zero required failures, and zero mandatory internal skips. Its
`summary.txt`, `results.tsv`, and `subtests.tsv` SHA-256 values are respectively
`0904e999aaed0a160563acb0d68f4b1016b836355a61f90e5f309811f5e5eeea`,
`56027b2eb3a459a1646d58dfe93ed166974df4adc440918d97bd88610f581e84`, and
`14c6b25606d4b90d7db1da9e24d7e8c9f2d134c97ce8ae9dda04ce6b27c57958`.
The exact topology hashes are
`fccf6c55f17dbc065cbea1d39ff8e206fbb25e05ea5cb9352af31059fffec55d`
for the 198-test main suite and
`41a53c7e9d481ddb0a9020ee74b670d1c64c245e1abbf0db524cb6378d16294b`
for the 56-test recovery suite. The complete dirty-tree recovery matrix passed
all 56 tests with the three exact root-host capability
methods surfaced as portable required skips; its structured ledger at
`/tmp/recovery-complete-current-subtests.tsv` has SHA-256
`422fc0277a5c4cbd7c85ecf80f21cbb1068df9971096e9ca0464913d9e3bcac5`.
The exact root-host rerun and complete mirror precheck are recorded below;
clean-commit provenance and the current green CI run remain pending and are not
inferred from this dirty-tree result.

Containment preflight evidence now distinguishes a disposable exact-child
pidfd `SIGTERM` proof from a real `unshare --pid --fork --kill-child=KILL`
proof. The latter runs from a coherent host `/proc` view, kills the exact
supervisor through its pidfd with `SIGKILL`, requires return code `-SIGKILL`,
and proves teardown of the namespace child and an escaped `setsid` descendant
including both private process groups. The checkpoint's tracked-child and VDB
lock launchers use parent-death `SIGKILL`, so a coordinator `SIGKILL` cannot be
defeated by an `unshare` implementation that survives or forwards `SIGTERM`.
The three root-host checkpoint primitive tests were rerun against checkpoint
script SHA-256
`077eba6ba27f642f3840475a5b503176c3cc46d2095d7fb27f681ef885cc2df3`
and fixture SHA-256
`f5a9c1be08ac1c53e45f978b58644d18f706416708ed7183eccb95fa481f9290`;
all three passed with zero skips. Their structured ledger at
`/tmp/checkpoint-host-capability-subtests-final.tsv` has SHA-256
`657f96ff703ed04ff08435c0cd9b8a7ad3c850b529db9041907ee3415413f49c`.

An earlier clean-mirror precheck reached every portable case; its terminal
results were not retained and are treated only as an unbound diagnostic
observation. The observed failure was the dispatcher fixture treating the managed namespace's
overflow UID for exact `/usr/bin/jq` as a production ownership failure. The
bounded fixture path now continues to execute exact `/usr/bin/jq` while
waiving only that unobservable ownership assertion; production retains the
complete root/ancestor check. The dispatcher subsequently passed all 45 cases
both in the live checkout and a fresh `/tmp` mirror. This earlier failed run is
diagnostic only. The subsequent exact source mirror at
`/tmp/gentoo-candidate-a-mirror-20260726-v2` was created by applying the
then-uncommitted source patch whose source and applied-copy SHA-256 values both equal
`cdb08db28802cfff7900eb188d7c8ff9bfb69e03f296e163ae29c6a295dabbbb`.
Its complete `portable-complete` run at
`/tmp/gentoo-opt-portable-complete-candidate-a-mirror-v2` passed with 78
top-level passes, zero failures, and 13 exact portable skips; it recorded 355
required subtest passes, zero required failures, 26 required portable skips,
one diagnostic skip, and zero unexpected results. The SHA-256 values for
`summary.txt`, `results.tsv`, `subtests.tsv`, and `test-contract.log` are
respectively
`de85099bfc95cde0fa24f2e7479e32a6d7b42a786e55ad16a61d4c068b9f621f`,
`7029f960da5f6423185118b952590203e44d41541a0afec798810d211e8493a7`,
`f724a19cd09adf5298da66a3efa3609a9d08e7f3c4b116ecf9f64ef55e98c1c1`,
and `b4777b5842b67dce71f32c13acee03d741f5676d2ce468ad58b1be814b7b346e`.
This remains a dirty-source, non-authorizing mirror precheck: exact
clean-commit provenance and a green current-commit CI run remain required.
The mirror, smoke, checkpoint-smoke, recovery, stress, host-capability, patch,
and evidence ledgers cited in this checkpoint were copied byte-for-byte into
the root-owned durable tree
`/var/lib/gentoo-optimization/reports/candidate-a-repository-precheck-20260726`;
the individual hashes above continue to identify the preserved payloads.

The system Python does not yet contain that closure, so an exact current-CPV
recovery checkpoint must precede its source installation. Checkpoint creation
has no retirement transition and deliberately ends at
`selector-activated-offline-restore-pending`; therefore the pre-install
checkpoint must complete its own exact offline binary restoration and reach
`offline-restore-proven` before the closure is installed. The post-install CPV
set then requires a second independently verified checkpoint and a second exact
offline restoration/finalization. Both canonical checkpoint states must report
`pending_total=0`, `unknown_total=0`, and `failed_total=0`; restoring only the
post-install checkpoint would leave the pre-install state nonterminal and is
forbidden.

Repository authorization commands no longer use visual hash comparison.
Immutable bundle and installer copies are compared programmatically, root Git
materialization runs with no system configuration and a private empty HOME,
and root-owned state/cache/evidence existence checks run through `doas`. A
portable GitHub Actions workflow is configured to run the exact
portable-complete contract. The rejected `1729e9a` run skipped the hermetic
framework installer because the runner's forced PATH did not expose Ubuntu's
`/usr/sbin/runuser`; the current workflow correction verifies that package
identity, exposes the reviewed `/usr/bin/runuser` entry point, and makes a
`framework-installer` `PASS` row mandatory. A green run proving that correction
for the final Candidate-A commit is required before live installation. Mutable
Ubuntu package versions and the current Node action-runtime deprecation warning
remain deferred CI-maintenance issues, not permission to weaken or skip this
gate. The 300-cycle crash workload is confined to `stress` and `authoritative`
modes.
All of this remains non-authorizing until one clean Candidate A passes live and
one frozen Candidate B reruns the complete gate into its detached index.

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

Commit clean Candidate A before its live precheck. After A passes, commit
Candidate B with truthful checked claims, then rerun the entire gate and
authorize only through B's detached index.

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
-reorder-functions=cdsort
-split-functions
-split-all-cold
-split-eh
-icf=safe
-dyno-stats
```

The literal `ext-tsp`/`cdsort` pair above is the Phase 1 validated default.
Changing either spelling requires the complete ET_EXEC, PIE, and DSO gate to
run again; `hfsort+` is not assumed equivalent from documentation alone.

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

For the Phase 2 Candidate-B authorization run only, the truthful plan update and
claim markers are committed before the full rerun. The plan then remains
immutable; detached root-owned state and evidence record the result. Any later
plan correction creates a new candidate and invalidates that authorization.

The project is complete only when the live installed system—not merely the repository—passes the final strict completion conditions.

# Exact PGO identity tools

`profile-identity.py` is the fail-closed identity boundary between package
classification and the Portage PGO dispatcher. It does not infer a profile
from `CATEGORY/PN`, and it never searches for a profile merely because a file
exists.

This is a userspace-only tool. Kernel images, kernel modules, kernel build or
installation packages, initramfs assets, and the boot chain are excluded under
the terminal reason `kernel-policy-exclusion`. No LLM or automated project
action may configure/build/install a kernel or initramfs, modify `/efi` or
`/boot`, or create or alter a boot entry. The running kernel may be observed
read-only only as host context.

## Package fingerprint

The input is one strict JSON object. Unknown or missing fields are errors. Its
fields correspond to plan section 7.1: exact package/slot/repository/ebuild,
CHOST and ABI, the active compiler, complete flag sets, output-affecting
FEATURES, the ordered `package.env` stack, and build-system arguments. Rust
fingerprints also require the exact target triple and the bundled LLVM version reported by the
exact `rustc`; the target must appear in that compiler's target list. These
fields must be null for every non-Rust compiler. The tool resolves and
executes the compiler itself and records its real path, binary SHA-256 and full
version output. Volatile fields such as timestamps cannot be added to the
schema.

```sh
scripts/optimization/pgo/profile-identity.py fingerprint \
    --input /var/lib/gentoo-optimization/identity/dev-util.example.json \
    --metadata-out /var/lib/gentoo-optimization/identity/dev-util.example.metadata.json \
    --key-out /var/lib/gentoo-optimization/identity/dev-util.example.fingerprint.env
```

The command prints only the 64-character SHA-256 key. Both outputs are
optional. The environment file is atomically written as exactly:

```text
fingerprint=<64 lowercase hexadecimal characters>
```

Output paths must be non-root absolute paths and may not traverse symlinks.
`amd64` and `x86` are distinct and are the only accepted ABI names.

## Profile-family paths

`profile-path` constructs, but does not create, a path below a non-root
absolute profile root. Conditional arguments are strict: missing and
extraneous family fields are rejected. The layouts match plan section 7.3 and
use `sample.prof` for Clang sample PGO and `default.pgo` for Go.

```sh
scripts/optimization/pgo/profile-identity.py profile-path \
    --root /var/lib/gentoo-optimization/profiles \
    --family clang-sample --compiler-major 22 \
    --cpv dev-util/example-1.2.3-r1 --fingerprint "${fingerprint}" \
    --build-id "${build_id}"
```

The families are `clang-ir`, `rust`, `gcc`, `go`, and `clang-sample`. They have
non-overlapping compiler/generation/package/ABI identity axes. Rust paths
additionally separate the exact Rust language/compiler
version, bundled LLVM major, complete bundled LLVM version, and target triple.
A caller must never substitute one family path for another.

## Clang sample profiles

Produce a profile from exact binary and perf inputs with `sample-convert`:

```sh
scripts/optimization/pgo/profile-identity.py sample-convert \
    --llvm-profgen /usr/lib/llvm/22/bin/llvm-profgen \
    --llvm-profdata /usr/lib/llvm/22/bin/llvm-profdata \
    --readelf /usr/lib/llvm/22/bin/llvm-readelf \
    --objcopy /usr/lib/llvm/22/bin/llvm-objcopy \
    --binary /absolute/path/to/unstripped-input \
    --perf-data /absolute/path/to/perf.data \
    --profile-out /absolute/profile/tree/sample.prof \
    --metadata-out /absolute/profile/tree/sample-metadata.json \
    --conversion-log-out /absolute/profile/tree/llvm-profgen-conversion-log.json \
    --cpv dev-util/example-1.2.3-r1 --fingerprint "${fingerprint}" \
    --abi amd64 --clang-major 22 \
    --optimization-generation-id generation-20260713-a \
    --inventory-id inventory-20260713-a \
    --inventory-sha256 "${inventory_sha256}" \
    --workload-revision workloads-sha256-a1 \
    --source-identity-sha256 "${source_identity_sha256}" \
    --production-host "$(hostname)" --production-date 2026-07-13
```

`--debug-binary` supplies a separate exact DWARF input when required. The
producer invokes LLVM tools of the requested major, derives the GNU build ID
and `.text` SHA-256 from the binary, and records hashes plus exact inode/stat
observations of the binary, optional debug binary and perf data, as well as
tool, command, profile and validation identities. Sample producer metadata is
schema version 4. It also records the canonical path, SHA-256, and complete
inode/stat observation of the required sibling
`llvm-profgen-conversion-log.json`. That mode-0440, link-count-one JSON record
preserves the exact converter stdout and stderr, their individual hashes, the
combined command-output hash, exact argv, exit status, and exact producer
path/hash. Validation reopens the record and proves all of those bindings;
restoring its original bytes after a replacement still fails through the stat
observation.

Every input observation is
captured before `llvm-profgen` and required to remain byte-for-byte and
metadata-identical afterward. A change is rejected even if the caller restores
the original bytes before the converter exits, closing the conversion TOCTOU
window.
`llvm-profgen` can write only `sample.prof.partial`; the conversion log is also
created at a private `.partial` path. A successful sample-aware validation
precedes publication. The profile and log are renamed, fsynced, observed, and
only then bound into the atomically written producer metadata. Ordinary
failure, timeout, missing output and caught interruption remove every partial
and unpublished transaction output. Existing final profiles, logs, or metadata
are immutable inputs and are never reused or overwritten.
The two metadata destinations are intentionally fixed:
`sample-metadata.json` and `llvm-profgen-conversion-log.json` must be siblings
of `sample.prof`.

There is no external-profile recording escape hatch. `sample-record` is kept
only as an unconditional error for callers that have not migrated; it never
writes metadata. Only this transactional `sample-convert` command can create
sample provenance.

Production metadata is required, not inferred: the exact optimization
generation/inventory/inventory-SHA-256 triple, workload revision, a SHA-256
identity for the source/distfile set, production host, and a valid
`YYYY-MM-DD` production date. These reproducibility fields
are deliberately outside the canonical package fingerprint, so recording a
production date cannot make an otherwise identical build identity unstable.

`sample-convert` and later `sample-validate` both run the sample-aware command
`llvm-profdata show --sample`. Validation requires the same profile SHA-256,
size, absolute path, tool identity, mapping-input fingerprint, ABI, Clang
major, build ID, `.text` SHA-256, exact source observations, and reproducibility
metadata. An IR instrumentation profile, a profile named
`merged.profdata`, a missing profile, an LLVM-major mismatch, or changed
metadata is rejected. The dispatcher consumes this family only with
`-fprofile-sample-use`; this tool never emits compiler flags.

`profile-identity.py` never publishes a Portage dispatcher manifest or its
strict sidecar. `validate-profile.py` is the sole authority for that atomic
manifest/sidecar transaction after it independently verifies this producer
metadata. This prevents a second, weaker manifest implementation from drifting
away from the dispatcher's validation contract.

Every producer takes the stable framework lock shared, then the project and
generation locks exclusively. Every validator takes the same hierarchy
shared. Both generation locks must contain the same canonical full generation
triple before work begins and must retain the same inode and payload until it
ends. In production `/run/gentoo-optimization` is root:`portage` mode `0750`
and these three stable locks are root:`portage` mode `0640`: Portage readers
can open and share-lock them, while only root can publish or mutate state.
Profile-use manifests distinguish the mapping/training input fingerprint
(`--sample-input-fingerprint`) from the consumer-build fingerprint
(`--fingerprint`); both identities are retained in the strict sidecar. Profile,
metadata, manifest, and sidecar outputs are mode `0640` with the trusted parent
group, while the immutable conversion log is mode `0440`, so Portage can read
them without making the cache world-readable.

The obsolete `scripts/pgo/make-sample-prof.sh` entry point is retained only as
an unconditional error explaining the migration. It cannot create a weak
`CATEGORY/PN/merged.profdata` sample profile.

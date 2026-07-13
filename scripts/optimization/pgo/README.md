# Exact PGO identity tools

`profile-identity.py` is the fail-closed identity boundary between package
classification and the Portage PGO dispatcher. It does not infer a profile
from `CATEGORY/PN`, and it never searches for a profile merely because a file
exists.

## Package fingerprint

The input is one strict JSON object. Unknown or missing fields are errors. Its
fields correspond to plan section 7.1: exact package/slot/repository/ebuild,
CHOST and ABI, the active compiler, complete flag sets, output-affecting
FEATURES, the ordered `package.env` stack, build-system arguments and the
kernel release when the package builds kernel modules. Rust fingerprints also
require the exact target triple and the bundled LLVM version reported by the
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

The families are `clang-ir`, `rust`, `gcc`, `go`, `clang-sample`, and
`kernel`. They have non-overlapping compiler/generation/package/ABI identity
axes. Rust paths additionally separate the exact Rust language/compiler
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
    --cpv dev-util/example-1.2.3-r1 --fingerprint "${fingerprint}" \
    --abi amd64 --clang-major 22 \
    --optimization-generation-id generation-20260713-a \
    --workload-revision workloads-sha256-a1 \
    --source-identity-sha256 "${source_identity_sha256}" \
    --production-host "$(hostname)" --production-date 2026-07-13
```

`--debug-binary` supplies a separate exact DWARF input when required. The
producer invokes LLVM tools of the requested major, derives the GNU build ID
and `.text` SHA-256 from the binary, and records hashes plus exact inode/stat
observations of the binary, optional debug binary and perf data, as well as
tool, command, profile and validation identities. Every input observation is
captured before `llvm-profgen` and required to remain byte-for-byte and
metadata-identical afterward. A change is rejected even if the caller restores
the original bytes before the converter exits, closing the conversion TOCTOU
window.
`llvm-profgen` can write only `sample.prof.partial`; a successful sample-aware
validation precedes the atomic rename to `sample.prof`. Ordinary failure,
timeout, missing output and interruption remove the partial and any unpublished
transaction outputs. Existing final profiles are immutable and are never
reused or overwritten.

There is no external-profile recording escape hatch. `sample-record` is kept
only as an unconditional error for callers that have not migrated; it never
writes metadata. Only this transactional `sample-convert` command can create
sample provenance.

Production metadata is required, not inferred: optimization generation ID,
workload revision, a SHA-256 identity for the source/distfile set, production
host, and a valid `YYYY-MM-DD` production date. These reproducibility fields
are deliberately outside the canonical package fingerprint, so recording a
production date cannot make an otherwise identical build identity unstable.

`sample-convert` and later `sample-validate` both run the sample-aware command
`llvm-profdata show --sample`. Validation requires the same profile SHA-256,
size, absolute path, tool identity, package fingerprint, ABI, Clang major,
build ID, `.text` SHA-256, exact source observations, and reproducibility
metadata. An IR instrumentation profile, a profile named
`merged.profdata`, a missing profile, an LLVM-major mismatch, or changed
metadata is rejected. The dispatcher consumes this family only with
`-fprofile-sample-use`; this tool never emits compiler flags.

`profile-identity.py` never publishes a Portage dispatcher manifest or its
strict sidecar. `validate-profile.py` is the sole authority for that atomic
manifest/sidecar transaction after it independently verifies this producer
metadata. This prevents a second, weaker manifest implementation from drifting
away from the dispatcher's validation contract.

The obsolete `scripts/pgo/make-sample-prof.sh` entry point is retained only as
an unconditional error explaining the migration. It cannot create a weak
`CATEGORY/PN/merged.profdata` sample profile.

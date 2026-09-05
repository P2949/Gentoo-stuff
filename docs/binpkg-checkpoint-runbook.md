# Exact binpkg checkpoint runbook

This procedure creates and activates a complete package-managed recovery
checkpoint. It does not authorize an optimization generation. Run it only from
the live Gentoo host as root, with package mutations stopped, and preserve the
entire report and state trees.

This is package-state recovery only. It must not inspect as a gate, create,
edit, delete, reorder, select, or arm any boot entry; set or clear `BootNext`;
change `BootOrder`; write EFI variables; invoke boot-entry management tools;
or write `/efi`, `/boot`, bootloader configuration, kernel images, or initramfs
images. Kernel configuration/build/install/deployment is human-only and outside
this project. If any checkpoint or restore path would cross that boundary,
stop before mutation; do not add a recovery-entry prerequisite and do not ask a
human to create one for the project.

## Transaction states

Each checkpoint ID has three immutable state records. The canonical
`binpkg-checkpoint-ID.json` is an atomic hardlink to the latest one.

| Immutable state | Selector | Meaning |
| --- | --- | --- |
| `*.prepared.json` | exact old selector | Prepared selector is durable; activation is pending. |
| `*.selector-activated-offline-restore-pending.json` | exact durable checkpoint | Activation receipt and displaced-selector witness are durable; an offline restore is still required. |
| `*.offline-restore-proven.json` | exact durable checkpoint | The offline command, selected archive, fresh post-restore verification, and terminal receipt are bound; `pending_total=0`. |

The selector transaction uses these same-parent names:

```text
/var/cache/gentoo-optimization/binpkgs/critical-current
/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-ID
/var/cache/gentoo-optimization/binpkgs/critical-current.previous-ID
```

The first is live, the second is the durable prepared selector before exchange
and the displaced old selector immediately after exchange, and the third is
the immutable displaced-selector witness. Any other combination is foreign and
reconciliation fails closed.

## 1. Materialize immutable source, then enter a clean root shell

As the desktop user, first complete **Create the candidate's immutable source
snapshot** in `docs/phase2-production-profile-transaction.md`. That procedure
creates and verifies the user-owned bundle before any root operation, then
publishes a root-owned bundle and exact detached checkout without executing
from the mutable desktop checkout. Do not enter the checkpoint root shell
until that complete procedure has produced the reviewed `COMMIT` and checkout
path.

Then enter the clean root shell. Re-enter the two reviewed literal values,
change to the immutable checkout explicitly, and keep this shell and these
exact operator paths for the complete transaction. Do not return to the
caller's ambient environment between sections.

```bash
/usr/bin/doas /usr/bin/env -i HOME=/root USER=root LOGNAME=root \
  SHELL=/bin/bash PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  LANG=C LC_ALL=C TZ=UTC /bin/bash --noprofile --norc
set -Eeuo pipefail
umask 077
test "$EUID" -eq 0
COMMIT=REVIEWED_COMMIT
CHECKOUT_SOURCE=/var/lib/gentoo-optimization/bootstrap/source-checkouts/REVIEWED_RUN_ID
test -d "$CHECKOUT_SOURCE"
test ! -L "$CHECKOUT_SOURCE"
cd "$CHECKOUT_SOURCE"
test "$PWD" = "$CHECKOUT_SOURCE"
test "$(/usr/bin/git -c core.hooksPath=/dev/null -c core.fsmonitor=false \
  -c core.attributesFile=/dev/null rev-parse --verify 'HEAD^{commit}')" = "$COMMIT"
if ! CHECKOUT_STATUS=$(/usr/bin/git -c core.hooksPath=/dev/null \
  -c core.fsmonitor=false -c core.attributesFile=/dev/null \
  status --porcelain=v1 --untracked-files=all); then
  exit 1
fi
test -z "$CHECKOUT_STATUS"
```

Publish both reviewed files from that immutable checkout as one new private
directory. Refuse both existing objects and broken symlinks; never copy over
an old bootstrap.

```bash
PARENT=/var/lib/gentoo-optimization/bootstrap
DEST=$PARENT/binpkg-checkpoint-$COMMIT
STAGE=$PARENT/.binpkg-checkpoint-$COMMIT.partial.$$
test ! -e "$DEST"
test ! -L "$DEST"
test ! -e "$STAGE"
test ! -L "$STAGE"
/usr/bin/install -d -o root -g root -m 0700 "$STAGE"
/usr/bin/install -o root -g root -m 0755 \
  scripts/optimization/recovery/create-binpkg-checkpoint.sh "$STAGE/"
/usr/bin/install -o root -g root -m 0755 \
  scripts/optimization/recovery/verify-binpkg-snapshot.py "$STAGE/"
SCRIPT_SHA=$(/usr/bin/sha256sum scripts/optimization/recovery/create-binpkg-checkpoint.sh | /usr/bin/awk '{print $1}')
VERIFIER_SHA=$(/usr/bin/sha256sum scripts/optimization/recovery/verify-binpkg-snapshot.py | /usr/bin/awk '{print $1}')
test "$SCRIPT_SHA" = "$(/usr/bin/sha256sum "$STAGE/create-binpkg-checkpoint.sh" | /usr/bin/awk '{print $1}')"
test "$VERIFIER_SHA" = "$(/usr/bin/sha256sum "$STAGE/verify-binpkg-snapshot.py" | /usr/bin/awk '{print $1}')"
/usr/bin/sync -f "$STAGE/create-binpkg-checkpoint.sh"
/usr/bin/sync -f "$STAGE/verify-binpkg-snapshot.py"
/usr/bin/sync -f "$STAGE"
/usr/bin/mv --no-clobber --no-copy -T -- "$STAGE" "$DEST"
if test -e "$STAGE" || test -L "$STAGE"; then
  test "$SCRIPT_SHA" = "$(/usr/bin/sha256sum "$DEST/create-binpkg-checkpoint.sh" | /usr/bin/awk '{print $1}')"
  test "$VERIFIER_SHA" = "$(/usr/bin/sha256sum "$DEST/verify-binpkg-snapshot.py" | /usr/bin/awk '{print $1}')"
  /usr/bin/rm -rf -- "$STAGE"
fi
test ! -e "$STAGE" && test ! -L "$STAGE"
test -d "$DEST"
test "$SCRIPT_SHA" = "$(/usr/bin/sha256sum "$DEST/create-binpkg-checkpoint.sh" | /usr/bin/awk '{print $1}')"
test "$VERIFIER_SHA" = "$(/usr/bin/sha256sum "$DEST/verify-binpkg-snapshot.py" | /usr/bin/awk '{print $1}')"
/usr/bin/sync -f "$DEST"
/usr/bin/sync -f "$PARENT"
```

The script binds its Bash interpreter, every external tool, the verifier,
Portage lock implementation, active `make.conf`, and stable lock inodes. Before
taking those locks it can safely initialize an absent
`/run/gentoo-optimization` as `root:portage 0750` and its three empty,
single-link `root:portage 0640` lock files using same-parent no-replace
publication. The Candidate framework also installs the matching tmpfiles rule,
which recreates this exact runtime ABI after reboot. Any existing foreign
directory, file, symlink, payload, owner, mode, or link count fails closed.

## 2. Bind the source and exact live delta

Continue in the clean root shell from section 1 and create a private evidence
directory. Root-private existence checks therefore cannot accidentally become
false permission-denied results.

```bash
ID=pre-candidate-a-deps-YYYYMMDDTHHMMSSZ
BOOTSTRAP=/var/lib/gentoo-optimization/bootstrap/binpkg-checkpoint-$COMMIT
EVIDENCE=/root/checkpoint-evidence-$ID
CHECKPOINT_CACHE=/var/cache/gentoo-optimization/binpkgs/snapshot-$ID
CHECKPOINT_DURABLE=/var/lib/gentoo-optimization/recovery/binpkgs/critical-$ID
CHECKPOINT_REPORT=/var/lib/gentoo-optimization/reports/checkpoint-$ID
CHECKPOINT_STATE=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$ID.json
CHECKPOINT_PREPARED_STATE=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$ID.prepared.json
CHECKPOINT_ACTIVATED_STATE=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$ID.selector-activated-offline-restore-pending.json
CHECKPOINT_RESTORED_STATE=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$ID.offline-restore-proven.json
PREPARED_SELECTOR=/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-$ID
SELECTOR_WITNESS=/var/cache/gentoo-optimization/binpkgs/critical-current.previous-$ID
for path in \
  "$EVIDENCE" "$CHECKPOINT_CACHE" "$CHECKPOINT_DURABLE" "$CHECKPOINT_REPORT" \
  "$CHECKPOINT_STATE" "$CHECKPOINT_PREPARED_STATE" \
  "$CHECKPOINT_ACTIVATED_STATE" "$CHECKPOINT_RESTORED_STATE" \
  "$PREPARED_SELECTOR" "$SELECTOR_WITNESS"; do
  test ! -e "$path"
  test ! -L "$path"
done
/usr/bin/install -d -o root -g root -m 0700 "$EVIDENCE"
```

Reject any visible package mutation before capturing inputs:

```bash
scan_portage_processes() {
  local output=$1 proc pid comm argument matched
  : >"$output"
  for proc in /proc/[0-9]*; do
    test -d "$proc" || continue
    pid=${proc##*/}
    test "$pid" != "$$" || continue
    comm=
    IFS= read -r comm <"$proc/comm" 2>/dev/null || continue
    matched=0
    case $comm in
      emerge|ebuild|ebuild.sh|emaint|quickpkg) matched=1 ;;
    esac
    if test -r "$proc/cmdline"; then
      while IFS= read -r -d '' argument; do
        case ${argument##*/} in
          emerge|ebuild|ebuild.sh|emaint|quickpkg) matched=1 ;;
        esac
      done <"$proc/cmdline"
    fi
    test "$matched" -eq 0 || printf '%s\t%s\n' "$pid" "$comm" >>"$output"
  done
}
scan_portage_processes "$EVIDENCE/portage-processes.preflight.txt"
if test -s "$EVIDENCE/portage-processes.preflight.txt"; then
  /usr/bin/cat "$EVIDENCE/portage-processes.preflight.txt" >&2
  printf '%s\n' 'ERROR: a Portage mutation process is active' >&2
  exit 1
fi
```

Capture the absolute selector target and exact input hashes:

```bash
BINPKG_SOURCE=$(/usr/bin/readlink -e /var/cache/gentoo-optimization/binpkgs/critical-current)
BINPKG_SOURCE_PACKAGES_SHA256=$(/usr/bin/sha256sum "$BINPKG_SOURCE/Packages" | /usr/bin/cut -d' ' -f1)
VERIFIER=$BOOTSTRAP/verify-binpkg-snapshot.py
VERIFIER_SHA256=$(/usr/bin/sha256sum "$VERIFIER" | /usr/bin/cut -d' ' -f1)
printf '%s\n' "$BINPKG_SOURCE" >"$EVIDENCE/source-target.txt"
printf '%s  %s\n' "$BINPKG_SOURCE_PACKAGES_SHA256" "$BINPKG_SOURCE/Packages" \
  >"$EVIDENCE/source-Packages.sha256"
printf '%s  %s\n' "$VERIFIER_SHA256" "$VERIFIER" \
  >"$EVIDENCE/verifier.sha256"
```

Run the pinned direct verifier and require that its only failures are the exact
source-to-live delta. Then materialize the exact atom file:

```bash
set +e
/usr/bin/python3 -I -B "$VERIFIER" \
  --snapshot "$BINPKG_SOURCE" --vdb /var/db/pkg --zstd /usr/bin/zstd \
  --format json --validate-gpkg \
  >"$EVIDENCE/source-verification.json" \
  2>"$EVIDENCE/source-verification.stderr"
VERIFY_STATUS=$?
set -e
test "$VERIFY_STATUS" -eq 1
/usr/bin/jq -e '
  .schema_version == 1 and .status == "fail" and
  .counts.missing_live_cpvs > 0 and
  .counts.errors == .counts.missing_live_cpvs and
  .counts.extra_indexed_archives == 0 and
  .counts.unindexed_gpkg_archives == 0 and
  ([.issues[].code] | all(. == "live_cpv_missing_archive"))
' "$EVIDENCE/source-verification.json" >/dev/null
/usr/bin/jq -r '.coverage.missing_live_cpvs[] | "=" + .' \
  "$EVIDENCE/source-verification.json" | LC_ALL=C /usr/bin/sort -u \
  >"$EVIDENCE/delta-atoms.txt"
test "$(/usr/bin/wc -l <"$EVIDENCE/delta-atoms.txt")" -eq \
  "$(/usr/bin/jq -r '.counts.missing_live_cpvs' "$EVIDENCE/source-verification.json")"
mapfile -t DELTA_ATOMS <"$EVIDENCE/delta-atoms.txt"
test "${#DELTA_ATOMS[@]}" -gt 0
```

Record a conservative no-reflink space bound. `qsize -b -f` measures installed
payload bytes for the delta. Each new checkpoint generation needs one source
copy, one delta copy, and 20 percent transaction/index overhead. When the cache
and durable parents share a filesystem, both generations must fit in the same
free-space pool. When they are on different filesystems, one complete
generation must fit independently on each filesystem:

```bash
SOURCE_BYTES=$(/usr/bin/du -sx --block-size=1 "$BINPKG_SOURCE" | /usr/bin/awk '{print $1}')
/usr/bin/qsize -q -b -f -S "${DELTA_ATOMS[@]}" \
  >"$EVIDENCE/qsize.stdout" 2>"$EVIDENCE/qsize.stderr"
mapfile -t DELTA_SIZE_VALUES < <(
  /usr/bin/awk '/^[[:space:]]*Totals:/ {print $(NF-1)}' "$EVIDENCE/qsize.stdout"
)
test "${#DELTA_SIZE_VALUES[@]}" -eq 1
[[ ${DELTA_SIZE_VALUES[0]} =~ ^[0-9]+$ ]]
DELTA_BYTES=${DELTA_SIZE_VALUES[0]}
test "$SOURCE_BYTES" -gt 0
test "$DELTA_BYTES" -gt 0
CACHE_PARENT=/var/cache/gentoo-optimization/binpkgs
DURABLE_PARENT=/var/lib/gentoo-optimization/recovery/binpkgs
test -d "$CACHE_PARENT"
test -d "$DURABLE_PARENT"

CACHE_DEVICE=$(/usr/bin/stat -Lc '%d' -- "$CACHE_PARENT")
DURABLE_DEVICE=$(/usr/bin/stat -Lc '%d' -- "$DURABLE_PARENT")
CACHE_AVAILABLE_BYTES=$(/usr/bin/df --output=avail --block-size=1 "$CACHE_PARENT" |
  /usr/bin/awk 'NR == 2 {print $1}')
DURABLE_AVAILABLE_BYTES=$(/usr/bin/df --output=avail --block-size=1 "$DURABLE_PARENT" |
  /usr/bin/awk 'NR == 2 {print $1}')
[[ $CACHE_DEVICE =~ ^[0-9]+$ ]]
[[ $DURABLE_DEVICE =~ ^[0-9]+$ ]]
[[ $CACHE_AVAILABLE_BYTES =~ ^[0-9]+$ ]]
[[ $DURABLE_AVAILABLE_BYTES =~ ^[0-9]+$ ]]

GENERATION_RAW_BYTES=$((SOURCE_BYTES + DELTA_BYTES))
GENERATION_REQUIRED_BYTES=$(((GENERATION_RAW_BYTES * 120 + 99) / 100))
CACHE_REQUIRED_BYTES=$GENERATION_REQUIRED_BYTES
DURABLE_REQUIRED_BYTES=$GENERATION_REQUIRED_BYTES
if [[ $CACHE_DEVICE == "$DURABLE_DEVICE" ]]; then
  SPACE_LAYOUT=shared-filesystem
  SPACE_ENFORCEMENT=combined-two-generation-bound
  SHARED_RAW_BYTES=$((2 * GENERATION_RAW_BYTES))
  SHARED_REQUIRED_BYTES=$(((SHARED_RAW_BYTES * 120 + 99) / 100))
  if ((CACHE_AVAILABLE_BYTES < DURABLE_AVAILABLE_BYTES)); then
    SHARED_AVAILABLE_BYTES=$CACHE_AVAILABLE_BYTES
  else
    SHARED_AVAILABLE_BYTES=$DURABLE_AVAILABLE_BYTES
  fi
  if ((SHARED_AVAILABLE_BYTES >= SHARED_REQUIRED_BYTES)); then
    SPACE_SUFFICIENT=yes
  else
    SPACE_SUFFICIENT=no
  fi
else
  SPACE_LAYOUT=split-filesystems
  SPACE_ENFORCEMENT=independent-one-generation-bounds
  SHARED_RAW_BYTES=0
  SHARED_REQUIRED_BYTES=0
  SHARED_AVAILABLE_BYTES=0
  if ((CACHE_AVAILABLE_BYTES >= CACHE_REQUIRED_BYTES &&
      DURABLE_AVAILABLE_BYTES >= DURABLE_REQUIRED_BYTES)); then
    SPACE_SUFFICIENT=yes
  else
    SPACE_SUFFICIENT=no
  fi
fi

{
  printf 'layout=%s\n' "$SPACE_LAYOUT"
  printf 'enforcement=%s\n' "$SPACE_ENFORCEMENT"
  printf 'sufficient=%s\n' "$SPACE_SUFFICIENT"
  printf 'cache_parent=%s\n' "$CACHE_PARENT"
  printf 'cache_device=%s\n' "$CACHE_DEVICE"
  printf 'cache_available_bytes=%s\n' "$CACHE_AVAILABLE_BYTES"
  printf 'cache_required_bytes=%s\n' "$CACHE_REQUIRED_BYTES"
  printf 'durable_parent=%s\n' "$DURABLE_PARENT"
  printf 'durable_device=%s\n' "$DURABLE_DEVICE"
  printf 'durable_available_bytes=%s\n' "$DURABLE_AVAILABLE_BYTES"
  printf 'durable_required_bytes=%s\n' "$DURABLE_REQUIRED_BYTES"
  printf 'source_bytes=%s\n' "$SOURCE_BYTES"
  printf 'delta_bytes=%s\n' "$DELTA_BYTES"
  printf 'generation_raw_bytes=%s\n' "$GENERATION_RAW_BYTES"
  printf 'generation_required_bytes=%s\n' "$GENERATION_REQUIRED_BYTES"
  printf 'shared_raw_bytes=%s\n' "$SHARED_RAW_BYTES"
  printf 'shared_required_bytes=%s\n' "$SHARED_REQUIRED_BYTES"
  printf 'shared_available_bytes=%s\n' "$SHARED_AVAILABLE_BYTES"
} >"$EVIDENCE/space-preflight.txt"

{
  /usr/bin/stat -Lc 'path=%n device=%d mode=%a owner=%u group=%g' -- \
    "$CACHE_PARENT" "$DURABLE_PARENT"
  /usr/bin/df --block-size=1 -- "$CACHE_PARENT" "$DURABLE_PARENT"
  /usr/bin/findmnt --target "$CACHE_PARENT" \
    --output TARGET,SOURCE,FSTYPE,OPTIONS --noheadings
  /usr/bin/findmnt --target "$DURABLE_PARENT" \
    --output TARGET,SOURCE,FSTYPE,OPTIONS --noheadings
} >"$EVIDENCE/filesystem-preflight.txt"

test "$SPACE_SUFFICIENT" = yes
```

`space-preflight.txt` is the machine-readable decision record. A
`shared-filesystem` result enforces the combined two-generation bound against
the lower of the two observed free-space values. A `split-filesystems` result
enforces the one-generation bound separately against both values. Preserve
`filesystem-preflight.txt` with it so the device, mount, filesystem, and raw
`df` observations can be audited later.

The checkpoint implementation uses `cp -a --reflink=auto` for both clone legs.
That retains copy-on-write cloning when the source and destination support it
and performs a full copy across filesystems or when reflinks are unavailable.
The conservative calculation above reserves space for the full-copy path. The
transaction publishes `clone-policy.json` before cloning and binds both clone
legs, the exact `cp` tool path, `reflink_policy=auto`, full-copy fallback, and
cross-filesystem support into the checkpoint evidence.

## 3. Create and activate

Choose a unique ID containing the purpose and UTC time. Reusing an ID is
forbidden except through `--reconcile` or `--finalize-offline-restore`.

```bash
"$BOOTSTRAP/create-binpkg-checkpoint.sh" \
  --expected-source-target "$BINPKG_SOURCE" \
  --expected-source-packages-sha256 "$BINPKG_SOURCE_PACKAGES_SHA256" \
  --expected-verifier-sha256 "$VERIFIER_SHA256" \
  "$ID" "${DELTA_ATOMS[@]}" \
  >"$EVIDENCE/create.stdout" 2>"$EVIDENCE/create.stderr"
```

Before cloning or `quickpkg`, the script performs a real two-way
`mv --exchange --no-copy -T` test in the selector’s actual parent, verifies
both symlink inodes and targets, exchanges them back, removes them, and fsyncs
the parent. It then validates every GPKG payload and writes complete archive
SHA-256 manifests for both final generations.

Successful creation intentionally stops in
`selector-activated-offline-restore-pending`; this is not a terminal recovery
claim.

An empty source-to-live delta may be used only to prove that a failed package
attempt installed nothing and that the live VDB is unchanged. It does not by
itself satisfy `offline-restore-proven`: terminal offline restoration must use
the normal evidence receipt and perform an exact binary restoration of the
selected `--restore-cpv`. Do not publish a no-op receipt or manufacture a
second restore protocol for an empty delta.

## 4. Reconcile an interrupted activation

Do not remove a prepared selector, witness, intent, receipt, state, mount, or
journal manually. Invoke the same immutable script, exact bindings, atom list,
and ID with `--reconcile`:

```bash
"$BOOTSTRAP/create-binpkg-checkpoint.sh" \
  --reconcile \
  --expected-source-target "$BINPKG_SOURCE" \
  --expected-source-packages-sha256 "$BINPKG_SOURCE_PACKAGES_SHA256" \
  --expected-verifier-sha256 "$VERIFIER_SHA256" \
  "$ID" "${DELTA_ATOMS[@]}" \
  >"$EVIDENCE/reconcile.stdout" 2>"$EVIDENCE/reconcile.stderr"
```

Reconciliation is idempotent. It removes only exact checkpoint-owned exchange
preflight residue and an exact checkpoint-owned `make.conf` bind mount. It then
classifies:

```text
old selector + exact prepared selector + no receipt/witness
    resume exact CAS under the Portage mutation freeze

new selector + displaced old selector
    publish the named witness, receipt, and activated state

new selector + exact witness + exact receipt/state
    verify and converge idempotently

any foreign selector, witness, receipt, state, target, or identity
    fail closed without replacing the foreign object
```

Preserve stderr and the report directory on every failure. A transaction whose
activation intent is incomplete is non-activated and must be classified before
a new checkpoint ID is used.

## 5. Prove one supervised offline binary-only restoration

Choose exactly one explicit CPV from the bound delta. For the 1,220-CPV
pre-dependency checkpoint, use the smallest reviewed delta package,
`dev-util/ftjam-2.5.3_rc2-r3`; do not depend on atom-file ordering. For a later
checkpoint, replace it with one explicitly reviewed CPV from that checkpoint's
bound delta. The checkpoint program, not the operator, selects and hashes the
matching archive, captures the VDB before and after, proves that only the
selected CPV's VDB subtree changed, runs `qcheck`, reruns the complete GPKG
verifier, and publishes the immutable attempt ledger and terminal receipt.

Portage 3.0.81.1 has no `emerge --offline` option. Literal network isolation is
provided by the already pidfd-bound launcher using a fresh PID and network
namespace with a private `/proc`; its functional preflight proves a distinct
network namespace, only loopback, and unreachable IPv4 and IPv6. The contained
command also clears binhosts and mirrors, replaces both fetch commands with
`/bin/false`, disables remote binpkg retrieval, and permits only the exact local
binary package. For the reviewed pre-dependency restoration, derive that one
canonical archive path from the immutable verification report (the finalizer
performs and binds the same selection):

```bash
RESTORE_CPV=dev-util/ftjam-2.5.3_rc2-r3
mapfile -t RESTORE_RELATIVES < <(/usr/bin/jq -er --arg cpv "$RESTORE_CPV" \
  '.archives[] | select(.cpv == $cpv) | .path' \
  "$CHECKPOINT_REPORT/durable-final-verification.json")
test "${#RESTORE_RELATIVES[@]}" -eq 1
ARCHIVE_PATH=$CHECKPOINT_DURABLE/${RESTORE_RELATIVES[0]}
test -f "$ARCHIVE_PATH"
test ! -L "$ARCHIVE_PATH"
```

The resulting contained mutation command is exactly:

```text
/usr/bin/unshare --pid --net --fork --kill-child=KILL --mount-proc --
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin TZ=UTC \
    PKGDIR=/var/lib/gentoo-optimization/recovery/binpkgs/critical-ID \
    PORTAGE_BINHOST= GENTOO_MIRRORS= \
    FETCHCOMMAND=/bin/false RESUMECOMMAND=/bin/false EPYTHON=python3.15 \
    /usr/bin/emerge --ignore-default-opts --ask=n --autounmask=n \
      --autounmask-write=n --buildpkg=n --getbinpkg=n --usepkgonly \
      --binpkg-changed-deps=n --binpkg-respect-use=n \
      --use-ebuild-visibility=n --nodeps --oneshot --verbose \
      "$ARCHIVE_PATH"
```

The implementation's parent-death-bound Python launcher wraps `unshare`, and
the private PID/network namespace runs a bounded `timeout` before the clean
`env`; `command-intent.json` and `command.json` bind the exact unshare argv,
tool identity, containment-preflight hash,
environment, selected absolute `.gpkg.tar` path, and Portage argv. It forces and
binds `EPYTHON=python3.15`, `/usr/bin/python3.15`, the selected
`/usr/lib/python-exec/python3.15/emerge`, and the exact installed
`sys-apps/portage` CPV. Immediately before mutation it reruns the functional
containment proof, runs the same exact archive command with `--pretend`, and
requires exactly one binary reinstall with zero downloads. It also requires
`qcheck` of the bound Portage CPV before and after the attempt, rehashes the
selected archive before and after, proves the complete `PKGDIR` tree did not
change, and proves `/var/lib/portage/world` and `world_sets` retained their exact
absence/type, ownership, mode, link count, timestamps, size, and content hash.
Before publishing any retry intent or invoking `emerge` again, it requires the
current VDB to be either unchanged from attempt zero or changed only inside the
exact restore CPV, and requires selected-set and `PKGDIR` state to equal the
attempt-zero baselines.
After any ambiguous attempt, terminal validation compares the final VDB,
selected sets, and `PKGDIR` against attempt zero's authoritative baselines—not
only against the successful retry's pre-state.

Run the finalizer with no externally created evidence envelopes:

```bash
RESTORE_CPV=dev-util/ftjam-2.5.3_rc2-r3
printf '%s\n' "${DELTA_ATOMS[@]}" | /usr/bin/grep -Fqx -- "=$RESTORE_CPV"
"$BOOTSTRAP/create-binpkg-checkpoint.sh" \
  --finalize-offline-restore \
  --restore-cpv "$RESTORE_CPV" \
  --expected-source-target "$BINPKG_SOURCE" \
  --expected-source-packages-sha256 "$BINPKG_SOURCE_PACKAGES_SHA256" \
  --expected-verifier-sha256 "$VERIFIER_SHA256" \
  "$ID" "${DELTA_ATOMS[@]}" \
  >"$EVIDENCE/finalize.stdout" 2>"$EVIDENCE/finalize.stderr"
```

A SIGKILL after the immutable command intent but before `command.json` leaves
an intentionally ambiguous attempt. A normal rerun fails closed. First prove
that no Portage process remains, inspect the immutable intent, retry records,
logs, and current VDB, and only then authorize a new complete attempt:

```bash
test -f "/var/lib/gentoo-optimization/reports/checkpoint-$ID/offline-restore/command-intent.json"
scan_portage_processes "$EVIDENCE/portage-processes.retry.txt"
test ! -s "$EVIDENCE/portage-processes.retry.txt"
"$BOOTSTRAP/create-binpkg-checkpoint.sh" \
  --finalize-offline-restore \
  --retry-interrupted-offline-restore \
  --restore-cpv "$RESTORE_CPV" \
  --expected-source-target "$BINPKG_SOURCE" \
  --expected-source-packages-sha256 "$BINPKG_SOURCE_PACKAGES_SHA256" \
  --expected-verifier-sha256 "$VERIFIER_SHA256" \
  "$ID" "${DELTA_ATOMS[@]}" \
  >"$EVIDENCE/finalize-retry.stdout" \
  2>"$EVIDENCE/finalize-retry.stderr"
```

Every retry gets a contiguous immutable `retry-intent-NNN.json`; terminal
evidence binds the complete attempt ledger. Never delete an attempt record or
reuse partial output. Once terminal, the same finalizer invocation is
idempotent: it reruns read-only full archive verification and must not invoke
`emerge` again.

## 6. Preserve and review

Preserve these trees and include their manifests and hashes in the project
evidence ledger:

```text
/var/cache/gentoo-optimization/binpkgs/snapshot-ID
/var/cache/gentoo-optimization/binpkgs/critical-current
/var/cache/gentoo-optimization/binpkgs/critical-current.previous-ID
/var/lib/gentoo-optimization/recovery/binpkgs/critical-ID
/var/lib/gentoo-optimization/reports/checkpoint-ID
/root/checkpoint-evidence-ID
/var/lib/gentoo-optimization/reports/checkpoint-ID-operator-evidence
/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-ID*.json
```

Before proceeding, verify the canonical and immutable terminal state are the
same inode, the selector targets `critical-ID`, the witness retains the exact
old selector identity, the restore receipt hashes all four internally generated evidence
files, and no `critical-current.prepared-ID` or exchange-preflight object
remains. Update and reread the complete project plan only after those checks.

```bash
STATE_PARENT=/var/lib/gentoo-optimization/state/project
REPORT=/var/lib/gentoo-optimization/reports/checkpoint-$ID
CANONICAL=$STATE_PARENT/binpkg-checkpoint-$ID.json
TERMINAL=$STATE_PARENT/binpkg-checkpoint-$ID.offline-restore-proven.json
WITNESS=/var/cache/gentoo-optimization/binpkgs/critical-current.previous-$ID
test "$(/usr/bin/stat -c '%d:%i' "$CANONICAL")" = "$(/usr/bin/stat -c '%d:%i' "$TERMINAL")"
test "$(/usr/bin/readlink /var/cache/gentoo-optimization/binpkgs/critical-current)" = \
  "/var/lib/gentoo-optimization/recovery/binpkgs/critical-$ID"
test -L "$WITNESS"
test "$(/usr/bin/readlink "$WITNESS")" = "$BINPKG_SOURCE"
WITNESS_FIELDS=$(/usr/bin/stat -c '%d:%i:%u:%g:%a:%h:%F' "$WITNESS")
WITNESS_TARGET=$(/usr/bin/readlink "$WITNESS")
WITNESS_RESOLVED=$(/usr/bin/readlink -e "$WITNESS")
WITNESS_TARGET_FIELDS=$(/usr/bin/stat -c '%d:%i:%u:%g:%a:%h:%F' "$WITNESS_RESOLVED")
WITNESS_PACKAGES_SHA=$(/usr/bin/sha256sum "$WITNESS_RESOLVED/Packages" | /usr/bin/awk '{print $1}')
WITNESS_IDENTITY="$WITNESS_FIELDS|$WITNESS_TARGET|$WITNESS_RESOLVED|$WITNESS_TARGET_FIELDS|$WITNESS_PACKAGES_SHA"
test "$WITNESS_IDENTITY" = "$(/usr/bin/jq -r '.displaced_selector_identity' "$REPORT/activation-receipt.json")"
/usr/bin/jq -e '
  .status == "offline-restore-proven" and
  .offline_restoration_tested == true and
  .pending_total == 0 and .unknown_total == 0 and .failed_total == 0 and
  (.offline_restore.receipt_sha256 | test("^[0-9a-f]{64}$")) and
  ([.offline_restore.evidence[]] | length == 4)
' "$CANONICAL" >/dev/null
RESTORE_RECEIPT=$REPORT/offline-restore-receipt.json
test "$(/usr/bin/sha256sum "$RESTORE_RECEIPT" | /usr/bin/cut -d' ' -f1)" = \
  "$(/usr/bin/jq -r '.offline_restore.receipt_sha256' "$CANONICAL")"
for NAME in command binpkg post_verifier attempt_ledger; do
  REL=$(/usr/bin/jq -r --arg name "$NAME" '.evidence[$name].path' "$RESTORE_RECEIPT")
  EXPECTED=$(/usr/bin/jq -r --arg name "$NAME" '.evidence[$name].sha256' "$RESTORE_RECEIPT")
  test "$EXPECTED" = "$(/usr/bin/sha256sum "$REPORT/$REL" | /usr/bin/cut -d' ' -f1)"
done
test ! -e "/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-$ID"
test ! -L "/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-$ID"
if ! EXCHANGE_RESIDUE=$(/usr/bin/find \
  /var/cache/gentoo-optimization/binpkgs -maxdepth 1 \
  -name ".critical-current.exchange-preflight-$ID-*" -print -quit); then
  exit 1
fi
test -z "$EXCHANGE_RESIDUE"
/usr/bin/find "$REPORT" -xdev \( -type f -o -type l \) -print0 >"$EVIDENCE/final-report.paths0"
/usr/bin/find "$STATE_PARENT" -maxdepth 1 \( -name "binpkg-checkpoint-$ID.json" -o \
  -name "binpkg-checkpoint-$ID.*.json" \) \( -type f -o -type l \) -print0 \
  >>"$EVIDENCE/final-report.paths0"
/usr/bin/python3 -I -B - "$EVIDENCE/final-report.paths0" \
  >"$EVIDENCE/final-evidence-manifest.json" <<'PY'
import hashlib, json, os, stat, sys
from pathlib import Path
paths = sorted(set(Path(os.fsdecode(item)) for item in Path(sys.argv[1]).read_bytes().split(b"\0") if item))
rows = []
for path in paths:
    st = path.lstat()
    row = {"path": str(path), "uid": st.st_uid, "gid": st.st_gid,
           "mode": stat.S_IMODE(st.st_mode), "nlink": st.st_nlink}
    if stat.S_ISLNK(st.st_mode):
        row.update(type="symlink", target=os.readlink(path))
    elif stat.S_ISREG(st.st_mode):
        row.update(type="file", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    else:
        raise SystemExit(f"unexplained object: {path}")
    rows.append(row)
print(json.dumps(rows, indent=2, sort_keys=True))
PY
```

The private operator evidence under `/root` is not disposable scratch data.
Publish an exact copy into the durable report tree and verify the complete copy
against a relative-path manifest. Keep the `/root` source as well as the
durable copy through Candidate B authorization; do not make the copy by hand.
Define the helper once in the same clean root shell so it can also publish the
post-install checkpoint evidence later in this runbook:

```bash
checkpoint_evidence_manifest() {
  local root=$1 action=$2 manifest=$3
  /usr/bin/python3 -I -B - "$root" "$action" "$manifest" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
action = sys.argv[2]
manifest = Path(sys.argv[3])
if action not in {"write", "verify"}:
    raise SystemExit("invalid manifest action")
root_stat = root.lstat()
if not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink():
    raise SystemExit("evidence root is not a trusted directory")
if manifest.parent != root or manifest.name != "operator-evidence.manifest.json":
    raise SystemExit("manifest must be the reviewed direct child")

rows = []

def visit(directory: Path) -> None:
    for entry in sorted(os.scandir(directory), key=lambda item: item.name):
        path = Path(entry.path)
        relative = path.relative_to(root).as_posix()
        if relative == manifest.name:
            continue
        value = path.lstat()
        row = {
            "path": relative,
            "uid": value.st_uid,
            "gid": value.st_gid,
            "mode": stat.S_IMODE(value.st_mode),
            "nlink": value.st_nlink,
        }
        if stat.S_ISDIR(value.st_mode):
            row["type"] = "directory"
            rows.append(row)
            visit(path)
        elif stat.S_ISREG(value.st_mode):
            row.update(
                type="file",
                size=value.st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            rows.append(row)
        elif stat.S_ISLNK(value.st_mode):
            row.update(type="symlink", target=os.readlink(path))
            rows.append(row)
        else:
            raise SystemExit(f"unexplained evidence object: {path}")

visit(root)
document = {"schema_version": 1, "rows": rows}
encoded = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
if action == "verify":
    if manifest.is_symlink() or not manifest.is_file():
        raise SystemExit("evidence manifest is absent or untrusted")
    if manifest.read_bytes() != encoded:
        raise SystemExit("evidence tree does not match its manifest")
else:
    if manifest.exists() or manifest.is_symlink():
        raise SystemExit("refusing to replace an evidence manifest")
    temporary = manifest.with_name(manifest.name + f".partial.{os.getpid()}")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, manifest)
        directory_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()
PY
}

publish_operator_evidence() {
  local source=$1 destination=$2
  local report_parent=/var/lib/gentoo-optimization/reports
  local destination_name=${destination##*/}
  local stage=$report_parent/.$destination_name.partial.$$
  local source_manifest=$source/operator-evidence.manifest.json
  test "${destination%/*}" = "$report_parent"
  test -d "$source"
  test ! -L "$source"
  test ! -e "$destination"
  test ! -L "$destination"
  test ! -e "$stage"
  test ! -L "$stage"
  checkpoint_evidence_manifest "$source" write "$source_manifest"
  checkpoint_evidence_manifest "$source" verify "$source_manifest"
  /usr/bin/install -d -o root -g root -m 0700 "$stage"
  /usr/bin/cp -a --reflink=auto -- "$source/." "$stage/"
  checkpoint_evidence_manifest \
    "$stage" verify "$stage/operator-evidence.manifest.json"
  /usr/bin/find "$stage" -xdev -type f -exec /usr/bin/sync -f -- '{}' +
  while IFS= read -r directory; do
    /usr/bin/sync -f -- "$directory"
  done < <(/usr/bin/find "$stage" -xdev -depth -type d -print)
  /usr/bin/mv --no-clobber --no-copy -T -- "$stage" "$destination"
  test ! -e "$stage"
  test ! -L "$stage"
  checkpoint_evidence_manifest \
    "$destination" verify "$destination/operator-evidence.manifest.json"
  test "$(/usr/bin/sha256sum "$source_manifest" | /usr/bin/cut -d' ' -f1)" = \
    "$(/usr/bin/sha256sum "$destination/operator-evidence.manifest.json" | \
      /usr/bin/cut -d' ' -f1)"
  /usr/bin/sync -f -- "$destination"
  /usr/bin/sync -f -- "$report_parent"
}

publish_checkpoint_operator_evidence() {
  local checkpoint_id=$1 source=$2
  publish_operator_evidence "$source" \
    "/var/lib/gentoo-optimization/reports/checkpoint-$checkpoint_id-operator-evidence"
}

publish_checkpoint_operator_evidence "$ID" "$EVIDENCE"
test -d "/root/checkpoint-evidence-$ID"
test -d "/var/lib/gentoo-optimization/reports/checkpoint-$ID-operator-evidence"
```

## 7. Install the reviewed `jsonschema` closure through the prerequisite transaction

The first checkpoint and its supervised offline restore must already be
terminal.  The prerequisite transaction is a separate, durable state machine;
it does not authorize Phase 2 and it never permits an armed source transaction
to be rerun.  Continue in the same clean root shell and first revalidate the
terminal checkpoint and its retained operator evidence:

```bash
PRE_CHECKPOINT_ID=$ID
PRE_CHECKPOINT_DURABLE=$CHECKPOINT_DURABLE
PRE_CHECKPOINT_TERMINAL=$CHECKPOINT_RESTORED_STATE
test -f "$PRE_CHECKPOINT_TERMINAL"
test ! -L "$PRE_CHECKPOINT_TERMINAL"
/usr/bin/jq -e '
  .status == "offline-restore-proven" and
  .pending_total == 0 and .unknown_total == 0 and .failed_total == 0
' "$PRE_CHECKPOINT_TERMINAL" >/dev/null
test "$(/usr/bin/readlink -e \
  /var/cache/gentoo-optimization/binpkgs/critical-current)" = \
  "$PRE_CHECKPOINT_DURABLE"
checkpoint_evidence_manifest \
  "/var/lib/gentoo-optimization/reports/checkpoint-$PRE_CHECKPOINT_ID-operator-evidence" \
  verify \
  "/var/lib/gentoo-optimization/reports/checkpoint-$PRE_CHECKPOINT_ID-operator-evidence/operator-evidence.manifest.json"
```

The reviewed gate-enabled successor sets both `LIVE_PREPARATION_ENABLED` and
`LIVE_MUTATION_ENABLED` to `True` in source control. Check those literal gates
in the exact clean checkout **before** publishing anything. Never change the
constants on the host. Before prerequisite publication, require the exact
portable repository boundary and the separately reviewed non-package-mutating
prerequisite/host-capability boundary. The complete installed-candidate
authoritative zero-required-skip gate remains later: it requires immutable
Candidate A and must not be treated as a prerequisite for this bootstrap.

```bash
PREREQUISITE_PUBLISHER_SOURCE=$CHECKOUT_SOURCE/scripts/optimization/recovery/publish-jsonschema-prerequisite-bootstrap.py
PREREQUISITE_HELPER_SOURCE=$CHECKOUT_SOURCE/scripts/optimization/recovery/install-jsonschema-prerequisite.py
for path in "$PREREQUISITE_PUBLISHER_SOURCE" "$PREREQUISITE_HELPER_SOURCE"; do
  test -f "$path"
  test ! -L "$path"
done
if ! PREREQUISITE_GATE=$(
  /usr/bin/python3.15 -I -B - "$PREREQUISITE_HELPER_SOURCE" <<'PY'
import ast
import sys
from pathlib import Path

path = Path(sys.argv[1])
tree = ast.parse(path.read_bytes(), filename=str(path))
values = {}
for node in tree.body:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        continue
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    value = node.value
    for target in targets:
        if isinstance(target, ast.Name) and target.id in {
            "LIVE_PREPARATION_ENABLED", "LIVE_MUTATION_ENABLED"
        }:
            if not isinstance(value, ast.Constant) or type(value.value) is not bool:
                raise SystemExit(f"non-literal live gate: {target.id}")
            values[target.id] = value.value
if values != {
    "LIVE_PREPARATION_ENABLED": True,
    "LIVE_MUTATION_ENABLED": True,
}:
    print("live prerequisite preparation/mutation remains disabled")
    raise SystemExit(77)
print("live prerequisite preparation/mutation enabled")
PY
); then
  printf 'ERROR: %s; stop before prepare; bootstrap publication is forbidden\n' \
    "$PREREQUISITE_GATE" >&2
  exit 1
fi
test "$PREREQUISITE_GATE" = \
  'live prerequisite preparation/mutation enabled'
```

Bootstrap publication is itself a bounded live filesystem mutation.  After
the source gate succeeds, publish from the same checkout bound in section 1.
The trust anchor is not a bare `git status`: the publisher requires root-owned,
non-group/world-writable directories from `/` through the canonical checkout,
its private `.git` directory, and the canonical
`/var/lib/gentoo-optimization/bootstrap` parent (which is exactly
`root:root 0755`).  It rejects tracked changes, ordinary untracked files, and
ignored worktree residue; binds the exact HEAD commit, tree, `100755` path,
blob object ID, blob size and blob SHA-256 for every payload; and compares each
worktree file byte-for-byte with that blob.

Each new payload and the exact-schema manifest is fsynced, the complete
private `0700` directory is published with `RENAME_NOREPLACE`, and the `0755`
parent is fsynced.  Publication never overwrites or reuses a destination.

```bash
PREREQUISITE_BOOTSTRAP=$(
  /usr/bin/python3.15 -I -B "$PREREQUISITE_PUBLISHER_SOURCE" publish \
    --repository-root "$CHECKOUT_SOURCE" --commit "$COMMIT"
)
test "$PREREQUISITE_BOOTSTRAP" = \
  "/var/lib/gentoo-optimization/bootstrap/jsonschema-prerequisite-$COMMIT"
test -d "$PREREQUISITE_BOOTSTRAP"
test ! -L "$PREREQUISITE_BOOTSTRAP"
PREREQUISITE_PUBLISHER=$PREREQUISITE_BOOTSTRAP/publish-jsonschema-prerequisite-bootstrap.py
PREREQUISITE_HELPER=$PREREQUISITE_BOOTSTRAP/install-jsonschema-prerequisite.py
PREREQUISITE_VERIFIER=$PREREQUISITE_BOOTSTRAP/verify-binpkg-snapshot.py
PREREQUISITE_MANIFEST=$PREREQUISITE_BOOTSTRAP/bootstrap-manifest.json
for path in \
  "$PREREQUISITE_PUBLISHER" "$PREREQUISITE_HELPER" \
  "$PREREQUISITE_VERIFIER" "$PREREQUISITE_MANIFEST"; do
  test -f "$path"
  test ! -L "$path"
done
VERIFIED_PREREQUISITE_BOOTSTRAP=$(
  /usr/bin/python3.15 -I -B "$PREREQUISITE_PUBLISHER" verify \
    --commit "$COMMIT"
)
test "$VERIFIED_PREREQUISITE_BOOTSTRAP" = "$PREREQUISITE_BOOTSTRAP"
/usr/bin/jq -e --arg commit "$COMMIT" \
  --arg checkout "$CHECKOUT_SOURCE" \
  --arg destination "$PREREQUISITE_BOOTSTRAP" '
  .schema == "gentoo-optimization-jsonschema-prerequisite-bootstrap-v1" and
  (keys | sort) == ([
    "commit", "destination", "files", "python", "repository_git_config",
    "repository_root", "repository_root_identity", "schema", "tree"
  ] | sort) and
  .commit == $commit and .repository_root == $checkout and
  .destination == $destination and
  (.tree | test("^[0-9a-f]{40}$")) and
  .python.path == "/usr/bin/python3.15" and
  .python.uid == 0 and .python.gid == 0 and .python.mode == 493 and
  (.python.sha256 | test("^[0-9a-f]{64}$")) and
  ([.files[].relative] | sort) == [
    "install-jsonschema-prerequisite.py",
    "publish-jsonschema-prerequisite-bootstrap.py",
    "verify-binpkg-snapshot.py"
  ] and
  ([.files[] | select(
    (.git.path !=
      ("scripts/optimization/recovery/" + .relative)) or
    (.source.path != ($checkout + "/" + .git.path)) or
    (.source.uid != 0) or (.source.gid != 0) or
    (.source.mode != 493) or (.source.nlink != 1) or
    (.source.sha256 | test("^[0-9a-f]{64}$") | not) or
    (.git.mode != "100755") or
    (.git.blob_oid | test("^[0-9a-f]{40}$") | not) or
    (.git.blob_sha256 | test("^[0-9a-f]{64}$") | not) or
    (.git.blob_sha256 != .source.sha256) or
    (.git.blob_size != .source.size) or
    (.published.uid != 0) or (.published.gid != 0) or
    (.published.mode != 493) or (.published.nlink != 1) or
    (.published.sha256 != .source.sha256)
  )] | length) == 0
' "$PREREQUISITE_MANIFEST" >/dev/null
```

The published publisher is the only allowed execution path.  It revalidates
the complete manifest and every published identity before replacing itself
with `/usr/bin/python3.15 -I -B` and the published transaction helper.  Never
execute the helper from `CHECKOUT_SOURCE` or call the helper directly.  Its
`exec` boundary accepts only the four public commands `prepare`, `run`,
`recover`, and `verify`; all fixture/internal helper entrypoints are rejected.

After those gates are enabled by a later reviewed candidate, use only this
exact CLI.  `prepare` freezes and revalidates repository, Portage
configuration, VDB/private state, tools, exact source-only plan, distfiles and
private mutable roots before publishing `prepared`.  `run` may start only from
that state.  On any nonzero or interrupted run, invoke `recover` once; never
invoke `prepare` or `run` again for the same transaction ID.

```bash
INSTALL_ID=jsonschema-source-YYYYMMDDTHHMMSSZ
INSTALL_STATE=/var/lib/gentoo-optimization/state/project/jsonschema-prerequisite-$INSTALL_ID.json
INSTALL_ATTEMPT=/var/lib/gentoo-optimization/state/project/jsonschema-prerequisite-$INSTALL_ID.preparation-attempt.json
INSTALL_REPORT=/var/lib/gentoo-optimization/reports/jsonschema-prerequisite-$INSTALL_ID
INSTALL_AUTHORITY=/var/lib/gentoo-optimization/recovery/prerequisite-authorities/$INSTALL_ID
INSTALL_CACHE=/var/cache/gentoo-optimization/prerequisite-transactions/$INSTALL_ID
for path in \
  "$INSTALL_STATE" "$INSTALL_ATTEMPT" "$INSTALL_REPORT" \
  "$INSTALL_AUTHORITY" "$INSTALL_CACHE"; do
  test ! -e "$path"
  test ! -L "$path"
done
PREREQUISITE_EXEC=(
  /usr/bin/python3.15 -I -B "$PREREQUISITE_PUBLISHER" exec
  --commit "$COMMIT" --
)
"${PREREQUISITE_EXEC[@]}" prepare "$INSTALL_ID" \
  --pre-checkpoint-state "$PRE_CHECKPOINT_TERMINAL"
"${PREREQUISITE_EXEC[@]}" verify "$INSTALL_ID"

set +e
"${PREREQUISITE_EXEC[@]}" run "$INSTALL_ID"
INSTALL_STATUS=$?
set -e
if test "$INSTALL_STATUS" -ne 0; then
  "${PREREQUISITE_EXEC[@]}" recover "$INSTALL_ID"
fi
"${PREREQUISITE_EXEC[@]}" verify "$INSTALL_ID"
/usr/bin/jq -e '
  .phase == "success" and
  .pending_total == 0 and .unknown_total == 0 and .failed_total == 0
' "$INSTALL_STATE" >/dev/null
```

After coordinator death or reboot, start a fresh clean root shell exactly as
shown at the beginning of section 1.  Do not rerun `publish`, `prepare`, or
`run`, and do not depend on functions or variables inherited from the dead
shell.  Re-enter the reviewed values explicitly, revalidate the existing
published bootstrap, reconstruct only the public execution vector, and use
only `recover` followed by `verify`:

```bash
set -Eeuo pipefail
umask 077
test "$EUID" -eq 0
COMMIT=REVIEWED_COMMIT
INSTALL_ID=jsonschema-source-ORIGINAL_TIMESTAMPZ
PREREQUISITE_BOOTSTRAP=/var/lib/gentoo-optimization/bootstrap/jsonschema-prerequisite-$COMMIT
PREREQUISITE_PUBLISHER=$PREREQUISITE_BOOTSTRAP/publish-jsonschema-prerequisite-bootstrap.py
INSTALL_STATE=/var/lib/gentoo-optimization/state/project/jsonschema-prerequisite-$INSTALL_ID.json
INSTALL_ATTEMPT=/var/lib/gentoo-optimization/state/project/jsonschema-prerequisite-$INSTALL_ID.preparation-attempt.json
test -f "$PREREQUISITE_PUBLISHER"
test ! -L "$PREREQUISITE_PUBLISHER"
test -f "$INSTALL_ATTEMPT"
test ! -L "$INSTALL_ATTEMPT"
test "$(/usr/bin/python3.15 -I -B "$PREREQUISITE_PUBLISHER" verify \
  --commit "$COMMIT")" = "$PREREQUISITE_BOOTSTRAP"
PREREQUISITE_EXEC=(
  /usr/bin/python3.15 -I -B "$PREREQUISITE_PUBLISHER" exec
  --commit "$COMMIT" --
)
"${PREREQUISITE_EXEC[@]}" recover "$INSTALL_ID"
"${PREREQUISITE_EXEC[@]}" verify "$INSTALL_ID"
```

A recovered `rolled-back` or `recovery-failed` state is terminal evidence but
is not permission to enter section 8.  Preserve the bootstrap manifest, every
immutable transaction state, `INSTALL_REPORT`, `INSTALL_AUTHORITY`, and
`INSTALL_CACHE` through Candidate B authorization.  Allocate a new ID after a
failed pre-`prepared` attempt.  The immutable preparation-attempt object means
an ID is consumed before repository/config/VDB preparation begins: never
delete forensic residue, republish its bootstrap, or reuse that ID for any
command.

## 8. Create, replay, and finalize the post-install checkpoint

The second checkpoint is independent. It starts from the terminal
pre-dependency selector and its bound delta must be exactly the CPVs added by
the source-only transaction. Choose a new ID. Reusing the pre-install ID or
leaving the pre-install checkpoint nonterminal is forbidden.

```bash
POST_CHECKPOINT_ID=post-candidate-a-jsonschema-YYYYMMDDTHHMMSSZ
test "$POST_CHECKPOINT_ID" != "$PRE_CHECKPOINT_ID"
ID=$POST_CHECKPOINT_ID
EVIDENCE=/root/checkpoint-evidence-$ID
CHECKPOINT_CACHE=/var/cache/gentoo-optimization/binpkgs/snapshot-$ID
CHECKPOINT_DURABLE=/var/lib/gentoo-optimization/recovery/binpkgs/critical-$ID
CHECKPOINT_REPORT=/var/lib/gentoo-optimization/reports/checkpoint-$ID
CHECKPOINT_STATE=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$ID.json
CHECKPOINT_PREPARED_STATE=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$ID.prepared.json
CHECKPOINT_ACTIVATED_STATE=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$ID.selector-activated-offline-restore-pending.json
CHECKPOINT_RESTORED_STATE=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$ID.offline-restore-proven.json
PREPARED_SELECTOR=/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-$ID
SELECTOR_WITNESS=/var/cache/gentoo-optimization/binpkgs/critical-current.previous-$ID
for path in \
  "$EVIDENCE" "$CHECKPOINT_CACHE" "$CHECKPOINT_DURABLE" "$CHECKPOINT_REPORT" \
  "$CHECKPOINT_STATE" "$CHECKPOINT_PREPARED_STATE" \
  "$CHECKPOINT_ACTIVATED_STATE" "$CHECKPOINT_RESTORED_STATE" \
  "$PREPARED_SELECTOR" "$SELECTOR_WITNESS"; do
  test ! -e "$path"
  test ! -L "$path"
done
/usr/bin/install -d -o root -g root -m 0700 "$EVIDENCE"
scan_portage_processes "$EVIDENCE/portage-processes.preflight.txt"
test ! -s "$EVIDENCE/portage-processes.preflight.txt"

BINPKG_SOURCE=$(/usr/bin/readlink -e /var/cache/gentoo-optimization/binpkgs/critical-current)
test "$BINPKG_SOURCE" = "$PRE_CHECKPOINT_DURABLE"
BINPKG_SOURCE_PACKAGES_SHA256=$(
  /usr/bin/sha256sum "$BINPKG_SOURCE/Packages" | /usr/bin/cut -d' ' -f1
)
VERIFIER=$BOOTSTRAP/verify-binpkg-snapshot.py
VERIFIER_SHA256=$(/usr/bin/sha256sum "$VERIFIER" | /usr/bin/cut -d' ' -f1)
printf '%s\n' "$BINPKG_SOURCE" >"$EVIDENCE/source-target.txt"
printf '%s  %s\n' "$BINPKG_SOURCE_PACKAGES_SHA256" "$BINPKG_SOURCE/Packages" \
  >"$EVIDENCE/source-Packages.sha256"
printf '%s  %s\n' "$VERIFIER_SHA256" "$VERIFIER" \
  >"$EVIDENCE/verifier.sha256"

set +e
/usr/bin/python3 -I -B "$VERIFIER" \
  --snapshot "$BINPKG_SOURCE" --vdb /var/db/pkg --zstd /usr/bin/zstd \
  --format json --validate-gpkg \
  >"$EVIDENCE/source-verification.json" \
  2>"$EVIDENCE/source-verification.stderr"
VERIFY_STATUS=$?
set -e
test "$VERIFY_STATUS" -eq 1
/usr/bin/jq -e '
  .schema_version == 1 and .status == "fail" and
  .counts.missing_live_cpvs > 0 and
  .counts.errors == .counts.missing_live_cpvs and
  .counts.extra_indexed_archives == 0 and
  .counts.unindexed_gpkg_archives == 0 and
  ([.issues[].code] | all(. == "live_cpv_missing_archive"))
' "$EVIDENCE/source-verification.json" >/dev/null
/usr/bin/jq -r '.coverage.missing_live_cpvs[] | "=" + .' \
  "$EVIDENCE/source-verification.json" | LC_ALL=C /usr/bin/sort -u \
  >"$EVIDENCE/delta-atoms.txt"
test "$(/usr/bin/wc -l <"$EVIDENCE/delta-atoms.txt")" -eq \
  "$(/usr/bin/jq -r '.counts.missing_live_cpvs' "$EVIDENCE/source-verification.json")"
test -f "$INSTALL_STATE"
test ! -L "$INSTALL_STATE"
/usr/bin/jq -e '
  .phase == "success" and
  .pending_total == 0 and .unknown_total == 0 and .failed_total == 0
' "$INSTALL_STATE" >/dev/null
/usr/bin/sha256sum "$INSTALL_STATE" \
  >"$EVIDENCE/jsonschema-prerequisite-state.sha256"
/usr/bin/jq -er '.plan.rows[].cpv' "$INSTALL_STATE" | \
  LC_ALL=C /usr/bin/sort -u \
  >"$EVIDENCE/jsonschema-prerequisite-added-cpvs.txt"
test "$(/usr/bin/wc -l \
  <"$EVIDENCE/jsonschema-prerequisite-added-cpvs.txt")" -eq \
  "$(/usr/bin/jq -er '.plan.rows | length' "$INSTALL_STATE")"
/usr/bin/sed 's/^/=/' "$EVIDENCE/jsonschema-prerequisite-added-cpvs.txt" \
  >"$EVIDENCE/expected-delta-atoms.txt"
/usr/bin/cmp --silent \
  "$EVIDENCE/expected-delta-atoms.txt" "$EVIDENCE/delta-atoms.txt"
mapfile -t DELTA_ATOMS <"$EVIDENCE/delta-atoms.txt"
test "${#DELTA_ATOMS[@]}" -gt 0
```

Now rerun the complete conservative space and filesystem preflight block from
section 2 with this new `BINPKG_SOURCE`, `DELTA_ATOMS`, and `EVIDENCE`. Do not
invoke checkpoint creation unless the newly written decision is sufficient:

```bash
test -f "$EVIDENCE/space-preflight.txt"
test "$(/usr/bin/awk -F= '$1 == "sufficient" {print $2}' \
  "$EVIDENCE/space-preflight.txt")" = yes
test -f "$EVIDENCE/filesystem-preflight.txt"

"$BOOTSTRAP/create-binpkg-checkpoint.sh" \
  --expected-source-target "$BINPKG_SOURCE" \
  --expected-source-packages-sha256 "$BINPKG_SOURCE_PACKAGES_SHA256" \
  --expected-verifier-sha256 "$VERIFIER_SHA256" \
  "$ID" "${DELTA_ATOMS[@]}" \
  >"$EVIDENCE/create.stdout" 2>"$EVIDENCE/create.stderr"
```

If creation is interrupted, replay only through the exact reconciliation
interface; never remove or recreate its objects:

```bash
"$BOOTSTRAP/create-binpkg-checkpoint.sh" \
  --reconcile \
  --expected-source-target "$BINPKG_SOURCE" \
  --expected-source-packages-sha256 "$BINPKG_SOURCE_PACKAGES_SHA256" \
  --expected-verifier-sha256 "$VERIFIER_SHA256" \
  "$ID" "${DELTA_ATOMS[@]}" \
  >"$EVIDENCE/reconcile.stdout" 2>"$EVIDENCE/reconcile.stderr"
```

Select the exact installed `jsonschema` CPV—not
`jsonschema-specifications`—and run the second supervised offline binary-only
restoration/finalization:

```bash
mapfile -t JSONSCHEMA_RESTORE_CPVS < <(
  /usr/bin/grep -E '^dev-python/jsonschema-[0-9]' \
    "$EVIDENCE/jsonschema-prerequisite-added-cpvs.txt"
)
test "${#JSONSCHEMA_RESTORE_CPVS[@]}" -eq 1
RESTORE_CPV=${JSONSCHEMA_RESTORE_CPVS[0]}
printf '%s\n' "${DELTA_ATOMS[@]}" | /usr/bin/grep -Fqx -- "=$RESTORE_CPV"
"$BOOTSTRAP/create-binpkg-checkpoint.sh" \
  --finalize-offline-restore \
  --restore-cpv "$RESTORE_CPV" \
  --expected-source-target "$BINPKG_SOURCE" \
  --expected-source-packages-sha256 "$BINPKG_SOURCE_PACKAGES_SHA256" \
  --expected-verifier-sha256 "$VERIFIER_SHA256" \
  "$ID" "${DELTA_ATOMS[@]}" \
  >"$EVIDENCE/finalize.stdout" 2>"$EVIDENCE/finalize.stderr"
```

An interrupted second restore uses the same reviewed retry gate as section 5:

```bash
test -f "$CHECKPOINT_REPORT/offline-restore/command-intent.json"
scan_portage_processes "$EVIDENCE/portage-processes.retry.txt"
test ! -s "$EVIDENCE/portage-processes.retry.txt"
"$BOOTSTRAP/create-binpkg-checkpoint.sh" \
  --finalize-offline-restore \
  --retry-interrupted-offline-restore \
  --restore-cpv "$RESTORE_CPV" \
  --expected-source-target "$BINPKG_SOURCE" \
  --expected-source-packages-sha256 "$BINPKG_SOURCE_PACKAGES_SHA256" \
  --expected-verifier-sha256 "$VERIFIER_SHA256" \
  "$ID" "${DELTA_ATOMS[@]}" \
  >"$EVIDENCE/finalize-retry.stdout" \
  2>"$EVIDENCE/finalize-retry.stderr"
```

Both checkpoint generations must now be terminal, the new selector must target
the post-install generation, and its displaced-selector witness must preserve
the terminal pre-install generation:

```bash
POST_CANONICAL=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$POST_CHECKPOINT_ID.json
POST_TERMINAL=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$POST_CHECKPOINT_ID.offline-restore-proven.json
test "$(/usr/bin/stat -c '%d:%i' "$POST_CANONICAL")" = \
  "$(/usr/bin/stat -c '%d:%i' "$POST_TERMINAL")"
for state in "$PRE_CHECKPOINT_TERMINAL" "$POST_TERMINAL"; do
  /usr/bin/jq -e '
    .status == "offline-restore-proven" and
    .offline_restoration_tested == true and
    .pending_total == 0 and .unknown_total == 0 and .failed_total == 0
  ' "$state" >/dev/null
done
test "$(/usr/bin/readlink -e /var/cache/gentoo-optimization/binpkgs/critical-current)" = \
  "$CHECKPOINT_DURABLE"
POST_WITNESS=/var/cache/gentoo-optimization/binpkgs/critical-current.previous-$POST_CHECKPOINT_ID
test -L "$POST_WITNESS"
test "$(/usr/bin/readlink -e "$POST_WITNESS")" = "$PRE_CHECKPOINT_DURABLE"
POST_WITNESS_FIELDS=$(/usr/bin/stat -c '%d:%i:%u:%g:%a:%h:%F' "$POST_WITNESS")
POST_WITNESS_TARGET=$(/usr/bin/readlink "$POST_WITNESS")
POST_WITNESS_RESOLVED=$(/usr/bin/readlink -e "$POST_WITNESS")
POST_WITNESS_TARGET_FIELDS=$(/usr/bin/stat -c '%d:%i:%u:%g:%a:%h:%F' \
  "$POST_WITNESS_RESOLVED")
POST_WITNESS_PACKAGES_SHA=$(
  /usr/bin/sha256sum "$POST_WITNESS_RESOLVED/Packages" | /usr/bin/awk '{print $1}'
)
POST_WITNESS_IDENTITY="$POST_WITNESS_FIELDS|$POST_WITNESS_TARGET|$POST_WITNESS_RESOLVED|$POST_WITNESS_TARGET_FIELDS|$POST_WITNESS_PACKAGES_SHA"
test "$POST_WITNESS_IDENTITY" = \
  "$(/usr/bin/jq -r '.displaced_selector_identity' "$CHECKPOINT_REPORT/activation-receipt.json")"
POST_RESTORE_RECEIPT=$CHECKPOINT_REPORT/offline-restore-receipt.json
test "$(/usr/bin/sha256sum "$POST_RESTORE_RECEIPT" | /usr/bin/cut -d' ' -f1)" = \
  "$(/usr/bin/jq -r '.offline_restore.receipt_sha256' "$POST_CANONICAL")"
for NAME in command binpkg post_verifier attempt_ledger; do
  REL=$(/usr/bin/jq -r --arg name "$NAME" '.evidence[$name].path' \
    "$POST_RESTORE_RECEIPT")
  EXPECTED=$(/usr/bin/jq -r --arg name "$NAME" '.evidence[$name].sha256' \
    "$POST_RESTORE_RECEIPT")
  test "$EXPECTED" = \
    "$(/usr/bin/sha256sum "$CHECKPOINT_REPORT/$REL" | /usr/bin/cut -d' ' -f1)"
done
test ! -e "/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-$ID"
test ! -L "/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-$ID"
if ! POST_EXCHANGE_RESIDUE=$(/usr/bin/find \
  /var/cache/gentoo-optimization/binpkgs -maxdepth 1 \
  -name ".critical-current.exchange-preflight-$ID-*" -print -quit); then
  exit 1
fi
test -z "$POST_EXCHANGE_RESIDUE"

/usr/bin/find "$CHECKPOINT_REPORT" -xdev \( -type f -o -type l \) -print0 \
  >"$EVIDENCE/final-report.paths0"
/usr/bin/find /var/lib/gentoo-optimization/state/project -maxdepth 1 \
  \( -name "binpkg-checkpoint-$ID.json" -o \
     -name "binpkg-checkpoint-$ID.*.json" \) \
  \( -type f -o -type l \) -print0 >>"$EVIDENCE/final-report.paths0"
/usr/bin/python3 -I -B - "$EVIDENCE/final-report.paths0" \
  >"$EVIDENCE/final-evidence-manifest.json" <<'PY'
import hashlib, json, os, stat, sys
from pathlib import Path

paths = sorted(set(Path(os.fsdecode(item)) for item in
                   Path(sys.argv[1]).read_bytes().split(b"\0") if item))
rows = []
for path in paths:
    value = path.lstat()
    row = {"path": str(path), "uid": value.st_uid, "gid": value.st_gid,
           "mode": stat.S_IMODE(value.st_mode), "nlink": value.st_nlink}
    if stat.S_ISLNK(value.st_mode):
        row.update(type="symlink", target=os.readlink(path))
    elif stat.S_ISREG(value.st_mode):
        row.update(type="file", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    else:
        raise SystemExit(f"unexplained object: {path}")
    rows.append(row)
print(json.dumps(rows, indent=2, sort_keys=True))
PY
publish_checkpoint_operator_evidence "$POST_CHECKPOINT_ID" "$EVIDENCE"
checkpoint_evidence_manifest \
  "/var/lib/gentoo-optimization/reports/checkpoint-$POST_CHECKPOINT_ID-operator-evidence" \
  verify \
  "/var/lib/gentoo-optimization/reports/checkpoint-$POST_CHECKPOINT_ID-operator-evidence/operator-evidence.manifest.json"
```

Only after both terminal states, the source-install evidence, and both durable
operator-evidence copies verify may the complete project plan be updated and
reread. Neither checkpoint authorizes Phase 2 or an optimization generation.

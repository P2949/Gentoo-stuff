# Exact binpkg checkpoint runbook

This procedure creates and activates a complete package-managed recovery
checkpoint. It does not authorize an optimization generation. Run it only from
the live Gentoo host as root, with package mutations stopped, and preserve the
entire report and state trees.

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
until that complete procedure has produced the reviewed `COMMIT` and `SOURCE`.

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
SOURCE=/var/lib/gentoo-optimization/bootstrap/source-checkouts/REVIEWED_RUN_ID
test -d "$SOURCE"
test ! -L "$SOURCE"
cd "$SOURCE"
test "$PWD" = "$SOURCE"
test "$(/usr/bin/git -c core.hooksPath=/dev/null -c core.fsmonitor=false \
  -c core.attributesFile=/dev/null rev-parse --verify 'HEAD^{commit}')" = "$COMMIT"
test -z "$(/usr/bin/git -c core.hooksPath=/dev/null -c core.fsmonitor=false \
  -c core.attributesFile=/dev/null status --porcelain=v1 --untracked-files=all)"
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
SOURCE=$(readlink -e /var/cache/gentoo-optimization/binpkgs/critical-current)
SOURCE_PACKAGES_SHA256=$(sha256sum "$SOURCE/Packages" | cut -d' ' -f1)
VERIFIER=$BOOTSTRAP/verify-binpkg-snapshot.py
VERIFIER_SHA256=$(sha256sum "$VERIFIER" | cut -d' ' -f1)
printf '%s\n' "$SOURCE" >"$EVIDENCE/source-target.txt"
printf '%s  %s\n' "$SOURCE_PACKAGES_SHA256" "$SOURCE/Packages" \
  >"$EVIDENCE/source-Packages.sha256"
printf '%s  %s\n' "$VERIFIER_SHA256" "$VERIFIER" \
  >"$EVIDENCE/verifier.sha256"
```

Run the pinned direct verifier and require that its only failures are the exact
source-to-live delta. Then materialize the exact atom file:

```bash
set +e
/usr/bin/python3 -I -B "$VERIFIER" \
  --snapshot "$SOURCE" --vdb /var/db/pkg --zstd /usr/bin/zstd \
  --format json --validate-gpkg \
  >"$EVIDENCE/source-verification.json" \
  2>"$EVIDENCE/source-verification.stderr"
VERIFY_STATUS=$?
set -e
test "$VERIFY_STATUS" -eq 1
jq -e '
  .schema_version == 1 and .status == "fail" and
  .counts.missing_live_cpvs > 0 and
  .counts.errors == .counts.missing_live_cpvs and
  .counts.extra_indexed_archives == 0 and
  .counts.unindexed_gpkg_archives == 0 and
  ([.issues[].code] | all(. == "live_cpv_missing_archive"))
' "$EVIDENCE/source-verification.json" >/dev/null
jq -r '.coverage.missing_live_cpvs[] | "=" + .' \
  "$EVIDENCE/source-verification.json" | LC_ALL=C sort -u \
  >"$EVIDENCE/delta-atoms.txt"
test "$(wc -l <"$EVIDENCE/delta-atoms.txt")" -eq \
  "$(jq -r '.counts.missing_live_cpvs' "$EVIDENCE/source-verification.json")"
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
SOURCE_BYTES=$(du -sx --block-size=1 "$SOURCE" | awk '{print $1}')
qsize -q -b -f -S "${DELTA_ATOMS[@]}" \
  >"$EVIDENCE/qsize.stdout" 2>"$EVIDENCE/qsize.stderr"
mapfile -t DELTA_SIZE_VALUES < <(
  awk '/^[[:space:]]*Totals:/ {print $(NF-1)}' "$EVIDENCE/qsize.stdout"
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

CACHE_DEVICE=$(stat -Lc '%d' -- "$CACHE_PARENT")
DURABLE_DEVICE=$(stat -Lc '%d' -- "$DURABLE_PARENT")
CACHE_AVAILABLE_BYTES=$(df --output=avail --block-size=1 "$CACHE_PARENT" |
  awk 'NR == 2 {print $1}')
DURABLE_AVAILABLE_BYTES=$(df --output=avail --block-size=1 "$DURABLE_PARENT" |
  awk 'NR == 2 {print $1}')
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
  stat -Lc 'path=%n device=%d mode=%a owner=%u group=%g' -- \
    "$CACHE_PARENT" "$DURABLE_PARENT"
  df --block-size=1 -- "$CACHE_PARENT" "$DURABLE_PARENT"
  findmnt --target "$CACHE_PARENT" \
    --output TARGET,SOURCE,FSTYPE,OPTIONS --noheadings
  findmnt --target "$DURABLE_PARENT" \
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
  --expected-source-target "$SOURCE" \
  --expected-source-packages-sha256 "$SOURCE_PACKAGES_SHA256" \
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

## 4. Reconcile an interrupted activation

Do not remove a prepared selector, witness, intent, receipt, state, mount, or
journal manually. Invoke the same immutable script, exact bindings, atom list,
and ID with `--reconcile`:

```bash
"$BOOTSTRAP/create-binpkg-checkpoint.sh" \
  --reconcile \
  --expected-source-target "$SOURCE" \
  --expected-source-packages-sha256 "$SOURCE_PACKAGES_SHA256" \
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
  --expected-source-target "$SOURCE" \
  --expected-source-packages-sha256 "$SOURCE_PACKAGES_SHA256" \
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
  --expected-source-target "$SOURCE" \
  --expected-source-packages-sha256 "$SOURCE_PACKAGES_SHA256" \
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
test "$(stat -c '%d:%i' "$CANONICAL")" = "$(stat -c '%d:%i' "$TERMINAL")"
test "$(readlink /var/cache/gentoo-optimization/binpkgs/critical-current)" = \
  "/var/lib/gentoo-optimization/recovery/binpkgs/critical-$ID"
test -L "$WITNESS"
test "$(readlink "$WITNESS")" = "$SOURCE"
WITNESS_FIELDS=$(stat -c '%d:%i:%u:%g:%a:%h:%F' "$WITNESS")
WITNESS_TARGET=$(readlink "$WITNESS")
WITNESS_RESOLVED=$(readlink -e "$WITNESS")
WITNESS_TARGET_FIELDS=$(stat -c '%d:%i:%u:%g:%a:%h:%F' "$WITNESS_RESOLVED")
WITNESS_PACKAGES_SHA=$(sha256sum "$WITNESS_RESOLVED/Packages" | awk '{print $1}')
WITNESS_IDENTITY="$WITNESS_FIELDS|$WITNESS_TARGET|$WITNESS_RESOLVED|$WITNESS_TARGET_FIELDS|$WITNESS_PACKAGES_SHA"
test "$WITNESS_IDENTITY" = "$(jq -r '.displaced_selector_identity' "$REPORT/activation-receipt.json")"
jq -e '
  .status == "offline-restore-proven" and
  .offline_restoration_tested == true and
  .pending_total == 0 and .unknown_total == 0 and .failed_total == 0 and
  (.offline_restore.receipt_sha256 | test("^[0-9a-f]{64}$")) and
  ([.offline_restore.evidence[]] | length == 4)
' "$CANONICAL" >/dev/null
RESTORE_RECEIPT=$REPORT/offline-restore-receipt.json
test "$(sha256sum "$RESTORE_RECEIPT" | cut -d' ' -f1)" = \
  "$(jq -r '.offline_restore.receipt_sha256' "$CANONICAL")"
for NAME in command binpkg post_verifier attempt_ledger; do
  REL=$(jq -r --arg name "$NAME" '.evidence[$name].path' "$RESTORE_RECEIPT")
  EXPECTED=$(jq -r --arg name "$NAME" '.evidence[$name].sha256' "$RESTORE_RECEIPT")
  test "$EXPECTED" = "$(sha256sum "$REPORT/$REL" | cut -d' ' -f1)"
done
test ! -e "/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-$ID"
test ! -L "/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-$ID"
test -z "$(find /var/cache/gentoo-optimization/binpkgs -maxdepth 1 \
  -name ".critical-current.exchange-preflight-$ID-*" -print -quit)"
find "$REPORT" -xdev \( -type f -o -type l \) -print0 >"$EVIDENCE/final-report.paths0"
find "$STATE_PARENT" -maxdepth 1 \( -name "binpkg-checkpoint-$ID.json" -o \
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

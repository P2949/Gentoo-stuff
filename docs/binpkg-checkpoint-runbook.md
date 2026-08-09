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
BINPKG_SOURCE=$(readlink -e /var/cache/gentoo-optimization/binpkgs/critical-current)
BINPKG_SOURCE_PACKAGES_SHA256=$(sha256sum "$BINPKG_SOURCE/Packages" | cut -d' ' -f1)
VERIFIER=$BOOTSTRAP/verify-binpkg-snapshot.py
VERIFIER_SHA256=$(sha256sum "$VERIFIER" | cut -d' ' -f1)
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
SOURCE_BYTES=$(du -sx --block-size=1 "$BINPKG_SOURCE" | awk '{print $1}')
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
test "$(stat -c '%d:%i' "$CANONICAL")" = "$(stat -c '%d:%i' "$TERMINAL")"
test "$(readlink /var/cache/gentoo-optimization/binpkgs/critical-current)" = \
  "/var/lib/gentoo-optimization/recovery/binpkgs/critical-$ID"
test -L "$WITNESS"
test "$(readlink "$WITNESS")" = "$BINPKG_SOURCE"
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

## 7. Install the reviewed `jsonschema` closure from source only

The first checkpoint must be terminal and durably preserved before installing
the schema-validation dependency. Continue in the same clean root shell. This
block accepts only new source ebuilds, binds the exact pretend selection and
ebuild/repository identities, imposes one deadline on the real emerge, and
requires the installed CPV delta to equal the pretend selection exactly. A
timeout or ordinary failure is not permission to start the post-install
checkpoint.

```bash
PRE_CHECKPOINT_ID=$ID
PRE_CHECKPOINT_DURABLE=$CHECKPOINT_DURABLE
PRE_CHECKPOINT_TERMINAL=$CHECKPOINT_RESTORED_STATE
test -f "$PRE_CHECKPOINT_TERMINAL"
test ! -L "$PRE_CHECKPOINT_TERMINAL"
jq -e '
  .status == "offline-restore-proven" and
  .pending_total == 0 and .unknown_total == 0 and .failed_total == 0
' "$PRE_CHECKPOINT_TERMINAL" >/dev/null
test "$(readlink -e /var/cache/gentoo-optimization/binpkgs/critical-current)" = \
  "$PRE_CHECKPOINT_DURABLE"
checkpoint_evidence_manifest \
  "/var/lib/gentoo-optimization/reports/checkpoint-$PRE_CHECKPOINT_ID-operator-evidence" \
  verify \
  "/var/lib/gentoo-optimization/reports/checkpoint-$PRE_CHECKPOINT_ID-operator-evidence/operator-evidence.manifest.json"

INSTALL_ID=jsonschema-source-YYYYMMDDTHHMMSSZ
INSTALL_EVIDENCE=/root/jsonschema-source-install-$INSTALL_ID
INSTALL_DURABLE=/var/lib/gentoo-optimization/reports/jsonschema-source-install-$INSTALL_ID-evidence
test ! -e "$INSTALL_EVIDENCE"
test ! -L "$INSTALL_EVIDENCE"
test ! -e "$INSTALL_DURABLE"
test ! -L "$INSTALL_DURABLE"
/usr/bin/install -d -o root -g root -m 0700 "$INSTALL_EVIDENCE"

scan_portage_processes "$INSTALL_EVIDENCE/portage-processes.preflight.txt"
test ! -s "$INSTALL_EVIDENCE/portage-processes.preflight.txt"

capture_installed_cpvs() {
  local output=$1
  /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
    EPYTHON=python3.15 /usr/bin/python3.15 -I -B - >"$output" <<'PY'
import portage

cpvs = sorted(str(cpv) for cpv in portage.db["/"]["vartree"].dbapi.cpv_all())
if not cpvs or len(cpvs) != len(set(cpvs)):
    raise SystemExit("installed CPV observation is empty or duplicated")
print("\n".join(cpvs))
PY
  test -s "$output"
  test "$(( $(/usr/bin/wc -l <"$output") ))" -gt 0
  test "$(LC_ALL=C /usr/bin/sort -u "$output" | /usr/bin/sha256sum | \
    /usr/bin/cut -d' ' -f1)" = \
    "$(/usr/bin/sha256sum "$output" | /usr/bin/cut -d' ' -f1)"
}

capture_installed_cpvs "$INSTALL_EVIDENCE/installed-cpvs.before.txt"
/usr/bin/sha256sum "$INSTALL_EVIDENCE/installed-cpvs.before.txt" \
  >"$INSTALL_EVIDENCE/installed-cpvs.before.sha256"

capture_selected_sets() {
  local output=$1
  /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
    /usr/bin/python3 -I -B - "$output" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

rows = []
for path in (Path("/var/lib/portage/world"), Path("/var/lib/portage/world_sets")):
    try:
        value = path.lstat()
    except FileNotFoundError:
        rows.append({"path": str(path), "type": "absent"})
        continue
    row = {"path": str(path), "uid": value.st_uid, "gid": value.st_gid,
           "mode": stat.S_IMODE(value.st_mode), "nlink": value.st_nlink,
           "mtime_ns": value.st_mtime_ns, "size": value.st_size}
    if stat.S_ISREG(value.st_mode):
        row.update(type="file", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    elif stat.S_ISLNK(value.st_mode):
        row.update(type="symlink", target=os.readlink(path))
    else:
        raise SystemExit(f"untrusted selected-set object: {path}")
    rows.append(row)
Path(sys.argv[1]).write_text(
    json.dumps({"schema_version": 1, "rows": rows}, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

capture_selected_sets "$INSTALL_EVIDENCE/selected-sets.before.json"

: >"$INSTALL_EVIDENCE/tool-identities.txt"
for tool in /usr/bin/emerge /usr/bin/git /usr/bin/python3 \
  /usr/bin/python3.15 /usr/bin/qcheck /usr/bin/tar /usr/bin/timeout; do
  resolved=$(/usr/bin/readlink -e "$tool")
  test -n "$resolved"
  test -f "$resolved"
  test ! -L "$resolved"
  /usr/bin/stat -Lc \
    'path=%n device=%d inode=%i uid=%u gid=%g mode=%a size=%s' \
    "$resolved" >>"$INSTALL_EVIDENCE/tool-identities.txt"
  printf 'sha256=%s\n' \
    "$(/usr/bin/sha256sum "$resolved" | /usr/bin/cut -d' ' -f1)" \
    >>"$INSTALL_EVIDENCE/tool-identities.txt"
done

EMERGE_OPTIONS=(
  --ignore-default-opts
  --verbose
  --tree
  --oneshot
  --with-bdeps=y
  --autounmask=n
  --autounmask-write=n
  --buildpkg=y
  --getbinpkg=n
  --usepkg=n
)
PRETEND_COMMAND=(
  /usr/bin/emerge "${EMERGE_OPTIONS[@]}" --pretend dev-python/jsonschema
)
SOURCE_EMERGE_COMMAND=(
  /usr/bin/emerge "${EMERGE_OPTIONS[@]}" --ask=n dev-python/jsonschema
)
printf '%s\0' /usr/bin/timeout --signal=TERM --kill-after=60s 600s \
  /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
  EPYTHON=python3.15 NOCOLOR=1 TERM=dumb \
  "${PRETEND_COMMAND[@]}" >"$INSTALL_EVIDENCE/pretend-command.argv0"
set +e
/usr/bin/timeout --signal=TERM --kill-after=60s 600s \
  /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
  EPYTHON=python3.15 NOCOLOR=1 TERM=dumb \
  "${PRETEND_COMMAND[@]}" \
  >"$INSTALL_EVIDENCE/pretend.stdout" \
  2>"$INSTALL_EVIDENCE/pretend.stderr"
PRETEND_STATUS=$?
set -e
printf '%s\n' "$PRETEND_STATUS" >"$INSTALL_EVIDENCE/pretend.status"
scan_portage_processes "$INSTALL_EVIDENCE/portage-processes.post-pretend.txt"
{
  printf 'status=%s\n' "$PRETEND_STATUS"
  printf 'portage_residue_rows=%s\n' \
    "$(/usr/bin/wc -l <"$INSTALL_EVIDENCE/portage-processes.post-pretend.txt")"
} >"$INSTALL_EVIDENCE/pretend-outcome.txt"
test ! -s "$INSTALL_EVIDENCE/portage-processes.post-pretend.txt"
test "$PRETEND_STATUS" -eq 0

/usr/bin/python3 -I -B - \
  "$INSTALL_EVIDENCE/pretend.stdout" \
  "$INSTALL_EVIDENCE/pretend-selection.json" \
  "$INSTALL_EVIDENCE/pretend-cpvs.txt" <<'PY'
import json
import re
import sys
from pathlib import Path

source, json_output, cpv_output = map(Path, sys.argv[1:])
exact = re.compile(r"^\[ebuild\s+N\s*\]\s+(\S+?)::([A-Za-z0-9_.+-]+)(?:\s|$)")
scheduled = re.compile(r"^\[[A-Za-z]")
rows = []
for raw in source.read_text(encoding="utf-8").splitlines():
    line = raw.lstrip()
    if not scheduled.match(line):
        continue
    match = exact.match(line)
    if match is None:
        raise SystemExit(f"pretend selected a non-new or non-source action: {raw}")
    cpv, repository = match.groups()
    if "/" not in cpv or not re.search(r"-[0-9]", cpv):
        raise SystemExit(f"invalid selected CPV: {cpv}")
    rows.append({"cpv": cpv, "repository": repository})
if not rows or len({row["cpv"] for row in rows}) != len(rows):
    raise SystemExit("pretend selection is empty or duplicated")
if sum(row["cpv"].startswith("dev-python/jsonschema-") for row in rows) != 1:
    raise SystemExit("pretend did not select exactly one jsonschema CPV")
rows.sort(key=lambda row: row["cpv"])
json_output.write_text(
    json.dumps({"schema_version": 1, "rows": rows}, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
cpv_output.write_text(
    "".join(f"{row['cpv']}\n" for row in rows), encoding="utf-8"
)
PY
test -s "$INSTALL_EVIDENCE/pretend-cpvs.txt"
test -z "$(/usr/bin/comm -12 \
  "$INSTALL_EVIDENCE/installed-cpvs.before.txt" \
  "$INSTALL_EVIDENCE/pretend-cpvs.txt")"

capture_ebuild_provenance() {
  local output=$1
  /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
    EPYTHON=python3.15 /usr/bin/python3.15 -I -B - \
    "$INSTALL_EVIDENCE/pretend-selection.json" >"$output" <<'PY'
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import portage

selection = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["rows"]
portdb = portage.db["/"]["porttree"].dbapi
repositories = portage.settings.repositories
repository_heads = {}
rows = []
for selected in selection:
    cpv = selected["cpv"]
    repository = selected["repository"]
    config = repositories[repository]
    configured_location = Path(config.location)
    if configured_location.is_symlink():
        raise SystemExit(f"symlinked repository location: {configured_location}")
    location = configured_location.resolve(strict=True)
    if not location.is_dir():
        raise SystemExit(f"untrusted repository location: {location}")
    configured_ebuild = Path(portdb.findname(cpv, myrepo=repository))
    if configured_ebuild.is_symlink():
        raise SystemExit(f"symlinked ebuild path: {configured_ebuild}")
    ebuild = configured_ebuild.resolve(strict=True)
    if not ebuild.is_file() or not ebuild.is_relative_to(location):
        raise SystemExit(f"untrusted ebuild path: {ebuild}")
    if repository not in repository_heads:
        result = subprocess.run(
            ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c",
             "core.fsmonitor=false", "-c", "core.attributesFile=/dev/null",
             "-C", str(location), "rev-parse", "--verify", "HEAD^{commit}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=60,
            check=True, env={"HOME": "/root", "LANG": "C", "LC_ALL": "C",
                             "PATH": "/usr/bin:/bin", "TZ": "UTC",
                             "GIT_CONFIG_GLOBAL": "/dev/null",
                             "GIT_CONFIG_NOSYSTEM": "1"},
        )
        head = result.stdout.strip()
        if not __import__("re").fullmatch(r"[0-9a-f]{40}", head):
            raise SystemExit("invalid repository commit identity")
        repository_heads[repository] = head
    value = ebuild.stat()
    rows.append({
        "cpv": cpv,
        "repository": repository,
        "repository_location": str(location),
        "repository_commit": repository_heads[repository],
        "ebuild_path": str(ebuild),
        "ebuild_device": value.st_dev,
        "ebuild_inode": value.st_ino,
        "ebuild_mode": stat.S_IMODE(value.st_mode),
        "ebuild_sha256": hashlib.sha256(ebuild.read_bytes()).hexdigest(),
    })
print(json.dumps({"schema_version": 1, "rows": rows}, indent=2, sort_keys=True))
PY
}

capture_ebuild_provenance \
  "$INSTALL_EVIDENCE/ebuild-provenance.before.json"

capture_selected_binpkg_state() {
  local output=$1
  /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
    EPYTHON=python3.15 /usr/bin/python3.15 -I -B - \
    "$INSTALL_EVIDENCE/pretend-cpvs.txt" >"$output" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import portage
from portage.getbinpkg import PackageIndex

selected = set(Path(sys.argv[1]).read_text(encoding="utf-8").splitlines())
if not selected:
    raise SystemExit("selected CPV set is empty")
pkgdir = Path(portage.settings["PKGDIR"]).resolve(strict=True)
packages_path = pkgdir / "Packages"
if packages_path.is_symlink() or not packages_path.is_file():
    raise SystemExit("trusted binary package index is absent")
index = PackageIndex()
with packages_path.open("r", encoding="utf-8") as input_file:
    index.read(input_file)
rows = []
for package in index.packages:
    cpv = package.get("CPV")
    if cpv not in selected:
        continue
    relative = package.get("PATH")
    if not isinstance(relative, str) or not relative:
        raise SystemExit(f"indexed binary package has no path: {cpv}")
    candidate = pkgdir / relative
    if candidate.is_symlink():
        raise SystemExit(f"symlinked binary package: {candidate}")
    path = candidate.resolve(strict=True)
    value = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(value.st_mode):
        raise SystemExit(f"untrusted binary package: {path}")
    if not path.is_relative_to(pkgdir) or not path.name.endswith(".gpkg.tar"):
        raise SystemExit(f"unexpected binary package path: {path}")
    rows.append({
        "cpv": cpv,
        "build_id": package.get("BUILD_ID"),
        "repository": package.get("REPO"),
        "path": str(path),
        "device": value.st_dev,
        "inode": value.st_ino,
        "mode": stat.S_IMODE(value.st_mode),
        "size": value.st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
rows.sort(key=lambda row: (row["cpv"], row["path"]))
print(json.dumps({"schema_version": 1, "pkgdir": str(pkgdir),
                  "packages_path": str(packages_path),
                  "packages_device": packages_path.stat().st_dev,
                  "packages_inode": packages_path.stat().st_ino,
                  "packages_mode": stat.S_IMODE(packages_path.stat().st_mode),
                  "packages_size": packages_path.stat().st_size,
                  "packages_sha256": hashlib.sha256(packages_path.read_bytes()).hexdigest(),
                  "rows": rows}, indent=2, sort_keys=True))
PY
}

capture_selected_binpkg_state "$INSTALL_EVIDENCE/binpkgs.before.json"
jq -e '.schema_version == 1 and (.rows | length) == 0' \
  "$INSTALL_EVIDENCE/binpkgs.before.json" >/dev/null

scan_portage_processes "$INSTALL_EVIDENCE/portage-processes.pre-emerge.txt"
test ! -s "$INSTALL_EVIDENCE/portage-processes.pre-emerge.txt"
printf '%s\0' /usr/bin/timeout --signal=TERM --kill-after=120s 21600s \
  /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
  EPYTHON=python3.15 NOCOLOR=1 TERM=dumb "${SOURCE_EMERGE_COMMAND[@]}" \
  >"$INSTALL_EVIDENCE/source-emerge-command.argv0"
set +e
/usr/bin/timeout --signal=TERM --kill-after=120s 21600s \
  /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
  EPYTHON=python3.15 NOCOLOR=1 TERM=dumb \
  "${SOURCE_EMERGE_COMMAND[@]}" \
  >"$INSTALL_EVIDENCE/source-emerge.stdout" \
  2>"$INSTALL_EVIDENCE/source-emerge.stderr"
EMERGE_STATUS=$?
set -e
printf '%s\n' "$EMERGE_STATUS" >"$INSTALL_EVIDENCE/source-emerge.status"
scan_portage_processes "$INSTALL_EVIDENCE/portage-processes.post-emerge.txt"
{
  printf 'status=%s\n' "$EMERGE_STATUS"
  printf 'portage_residue_rows=%s\n' \
    "$(/usr/bin/wc -l <"$INSTALL_EVIDENCE/portage-processes.post-emerge.txt")"
} >"$INSTALL_EVIDENCE/source-emerge-outcome.txt"
test ! -s "$INSTALL_EVIDENCE/portage-processes.post-emerge.txt"
test "$EMERGE_STATUS" -eq 0

capture_installed_cpvs "$INSTALL_EVIDENCE/installed-cpvs.after.txt"
capture_selected_sets "$INSTALL_EVIDENCE/selected-sets.after.json"
/usr/bin/cmp --silent \
  "$INSTALL_EVIDENCE/selected-sets.before.json" \
  "$INSTALL_EVIDENCE/selected-sets.after.json"
/usr/bin/comm -13 \
  "$INSTALL_EVIDENCE/installed-cpvs.before.txt" \
  "$INSTALL_EVIDENCE/installed-cpvs.after.txt" \
  >"$INSTALL_EVIDENCE/installed-cpvs.added.txt"
/usr/bin/comm -23 \
  "$INSTALL_EVIDENCE/installed-cpvs.before.txt" \
  "$INSTALL_EVIDENCE/installed-cpvs.after.txt" \
  >"$INSTALL_EVIDENCE/installed-cpvs.removed.txt"
test ! -s "$INSTALL_EVIDENCE/installed-cpvs.removed.txt"
/usr/bin/cmp --silent \
  "$INSTALL_EVIDENCE/pretend-cpvs.txt" \
  "$INSTALL_EVIDENCE/installed-cpvs.added.txt"
/usr/bin/sha256sum "$INSTALL_EVIDENCE/installed-cpvs.after.txt" \
  >"$INSTALL_EVIDENCE/installed-cpvs.after.sha256"

: >"$INSTALL_EVIDENCE/qcheck.stdout"
: >"$INSTALL_EVIDENCE/qcheck.stderr"
while IFS= read -r cpv; do
  if ! /usr/bin/qcheck -q -v "=$cpv" \
      >>"$INSTALL_EVIDENCE/qcheck.stdout" \
      2>>"$INSTALL_EVIDENCE/qcheck.stderr"; then
    printf 'qcheck failed for %s\n' "$cpv" >&2
    exit 1
  fi
done <"$INSTALL_EVIDENCE/installed-cpvs.added.txt"

capture_ebuild_provenance \
  "$INSTALL_EVIDENCE/ebuild-provenance.after.json"
/usr/bin/cmp --silent \
  "$INSTALL_EVIDENCE/ebuild-provenance.before.json" \
  "$INSTALL_EVIDENCE/ebuild-provenance.after.json"

capture_selected_binpkg_state "$INSTALL_EVIDENCE/binpkgs.after.json"
jq -e --slurpfile selected "$INSTALL_EVIDENCE/pretend-selection.json" '
  .schema_version == 1 and
  (.rows | length) == ($selected[0].rows | length) and
  ([.rows[].cpv] | sort) == ([$selected[0].rows[].cpv] | sort) and
  ([.rows[].cpv] | length) == ([.rows[].cpv] | unique | length) and
  ([.rows[] | select(
    (.sha256 | test("^[0-9a-f]{64}$") | not) or
    (.size <= 0) or (.mode != 420)
  )] | length) == 0
' "$INSTALL_EVIDENCE/binpkgs.after.json" >/dev/null
test "$(jq -r '.packages_sha256' "$INSTALL_EVIDENCE/binpkgs.before.json")" != \
  "$(jq -r '.packages_sha256' "$INSTALL_EVIDENCE/binpkgs.after.json")"
: >"$INSTALL_EVIDENCE/binpkg-tar.stdout"
: >"$INSTALL_EVIDENCE/binpkg-tar.stderr"
while IFS= read -r archive; do
  if ! /usr/bin/tar --list --file "$archive" \
      >>"$INSTALL_EVIDENCE/binpkg-tar.stdout" \
      2>>"$INSTALL_EVIDENCE/binpkg-tar.stderr"; then
    printf 'GPKG tar validation failed for %s\n' "$archive" >&2
    exit 1
  fi
done < <(jq -er '.rows[].path' "$INSTALL_EVIDENCE/binpkgs.after.json")

/usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
  EPYTHON=python3.15 /usr/bin/python3.15 -I -B - \
  "$INSTALL_EVIDENCE/pretend-selection.json" \
  >"$INSTALL_EVIDENCE/installed-vdb-provenance.json" <<'PY'
import json
import sys
from pathlib import Path

import portage

selection = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["rows"]
vartree = portage.db["/"]["vartree"].dbapi
rows = []
for selected in selection:
    cpv = selected["cpv"]
    values = vartree.aux_get(cpv, ["repository", "EAPI", "SLOT", "BUILD_TIME"])
    repository, eapi, slot, build_time = values
    if repository != selected["repository"]:
        raise SystemExit(f"installed repository mismatch for {cpv}")
    if not eapi or not slot or not build_time.isdigit():
        raise SystemExit(f"incomplete installed metadata for {cpv}")
    rows.append({"cpv": cpv, "repository": repository, "eapi": eapi,
                 "slot": slot, "build_time": int(build_time)})
print(json.dumps({"schema_version": 1, "rows": rows}, indent=2, sort_keys=True))
PY

run_schema_probe() {
  local interpreter=$1 label=$2 resolved
  test -x "$interpreter"
  resolved=$(/usr/bin/readlink -e "$interpreter")
  test -n "$resolved"
  test -f "$resolved"
  test ! -L "$resolved"
  {
    printf 'requested=%s\n' "$interpreter"
    printf 'resolved=%s\n' "$resolved"
    /usr/bin/stat -Lc 'device=%d inode=%i uid=%u gid=%g mode=%a size=%s' \
      "$resolved"
    printf 'sha256=%s\n' \
      "$(/usr/bin/sha256sum "$resolved" | /usr/bin/cut -d' ' -f1)"
    "$interpreter" --version
  } >"$INSTALL_EVIDENCE/python-$label.identity.txt" 2>&1
  /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC \
    "$interpreter" -I -B - \
    >"$INSTALL_EVIDENCE/draft-2020-12-$label.stdout" \
    2>"$INSTALL_EVIDENCE/draft-2020-12-$label.stderr" <<'PY'
from jsonschema import Draft202012Validator, ValidationError

schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema)
validator.validate({"value": 1})
try:
    validator.validate({"value": "not-an-integer"})
except ValidationError:
    pass
else:
    raise SystemExit("negative Draft 2020-12 probe unexpectedly passed")
print("Draft 2020-12 positive and negative probes passed")
PY
}

run_schema_probe /usr/bin/python3 active-python3
run_schema_probe /usr/bin/python3.15 portage-python3.15

printf '%s\n' \
  'source-only: --usepkg=n --getbinpkg=n --buildpkg=y' \
  >"$INSTALL_EVIDENCE/source-only-policy.txt"
publish_operator_evidence "$INSTALL_EVIDENCE" "$INSTALL_DURABLE"
checkpoint_evidence_manifest \
  "$INSTALL_DURABLE" verify "$INSTALL_DURABLE/operator-evidence.manifest.json"
```

Retain both `/root/jsonschema-source-install-$INSTALL_ID` and the verified
durable copy through Candidate B authorization. If any command above fails,
stop before section 8, keep the root evidence directory unchanged, and publish
it to a distinct reviewed failure-evidence destination before attempting any
remediation; never overwrite the successful destination named above.

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

BINPKG_SOURCE=$(readlink -e /var/cache/gentoo-optimization/binpkgs/critical-current)
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
jq -e '
  .schema_version == 1 and .status == "fail" and
  .counts.missing_live_cpvs > 0 and
  .counts.errors == .counts.missing_live_cpvs and
  .counts.extra_indexed_archives == 0 and
  .counts.unindexed_gpkg_archives == 0 and
  ([.issues[].code] | all(. == "live_cpv_missing_archive"))
' "$EVIDENCE/source-verification.json" >/dev/null
jq -r '.coverage.missing_live_cpvs[] | "=" + .' \
  "$EVIDENCE/source-verification.json" | LC_ALL=C /usr/bin/sort -u \
  >"$EVIDENCE/delta-atoms.txt"
test "$(/usr/bin/wc -l <"$EVIDENCE/delta-atoms.txt")" -eq \
  "$(jq -r '.counts.missing_live_cpvs' "$EVIDENCE/source-verification.json")"
/usr/bin/sed 's/^/=/' "$INSTALL_EVIDENCE/installed-cpvs.added.txt" \
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
    "$INSTALL_EVIDENCE/installed-cpvs.added.txt"
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
test "$(stat -c '%d:%i' "$POST_CANONICAL")" = \
  "$(stat -c '%d:%i' "$POST_TERMINAL")"
for state in "$PRE_CHECKPOINT_TERMINAL" "$POST_TERMINAL"; do
  jq -e '
    .status == "offline-restore-proven" and
    .offline_restoration_tested == true and
    .pending_total == 0 and .unknown_total == 0 and .failed_total == 0
  ' "$state" >/dev/null
done
test "$(readlink -e /var/cache/gentoo-optimization/binpkgs/critical-current)" = \
  "$CHECKPOINT_DURABLE"
POST_WITNESS=/var/cache/gentoo-optimization/binpkgs/critical-current.previous-$POST_CHECKPOINT_ID
test -L "$POST_WITNESS"
test "$(readlink -e "$POST_WITNESS")" = "$PRE_CHECKPOINT_DURABLE"
POST_WITNESS_FIELDS=$(stat -c '%d:%i:%u:%g:%a:%h:%F' "$POST_WITNESS")
POST_WITNESS_TARGET=$(readlink "$POST_WITNESS")
POST_WITNESS_RESOLVED=$(readlink -e "$POST_WITNESS")
POST_WITNESS_TARGET_FIELDS=$(stat -c '%d:%i:%u:%g:%a:%h:%F' \
  "$POST_WITNESS_RESOLVED")
POST_WITNESS_PACKAGES_SHA=$(
  sha256sum "$POST_WITNESS_RESOLVED/Packages" | awk '{print $1}'
)
POST_WITNESS_IDENTITY="$POST_WITNESS_FIELDS|$POST_WITNESS_TARGET|$POST_WITNESS_RESOLVED|$POST_WITNESS_TARGET_FIELDS|$POST_WITNESS_PACKAGES_SHA"
test "$POST_WITNESS_IDENTITY" = \
  "$(jq -r '.displaced_selector_identity' "$CHECKPOINT_REPORT/activation-receipt.json")"
POST_RESTORE_RECEIPT=$CHECKPOINT_REPORT/offline-restore-receipt.json
test "$(sha256sum "$POST_RESTORE_RECEIPT" | cut -d' ' -f1)" = \
  "$(jq -r '.offline_restore.receipt_sha256' "$POST_CANONICAL")"
for NAME in command binpkg post_verifier attempt_ledger; do
  REL=$(jq -r --arg name "$NAME" '.evidence[$name].path' \
    "$POST_RESTORE_RECEIPT")
  EXPECTED=$(jq -r --arg name "$NAME" '.evidence[$name].sha256' \
    "$POST_RESTORE_RECEIPT")
  test "$EXPECTED" = \
    "$(sha256sum "$CHECKPOINT_REPORT/$REL" | cut -d' ' -f1)"
done
test ! -e "/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-$ID"
test ! -L "/var/cache/gentoo-optimization/binpkgs/critical-current.prepared-$ID"
test -z "$(find /var/cache/gentoo-optimization/binpkgs -maxdepth 1 \
  -name ".critical-current.exchange-preflight-$ID-*" -print -quit)"

find "$CHECKPOINT_REPORT" -xdev \( -type f -o -type l \) -print0 \
  >"$EVIDENCE/final-report.paths0"
find /var/lib/gentoo-optimization/state/project -maxdepth 1 \
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

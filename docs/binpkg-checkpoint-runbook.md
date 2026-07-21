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

## 1. Publish immutable bootstrap inputs without replacement

Materialize the reviewed commit through the root-owned, ambient-config-isolated
Git bundle procedure. Then publish both reviewed files as one new private
directory. Refuse both existing objects and broken symlinks; never copy over an
old bootstrap.

```bash
COMMIT=REVIEWED_COMMIT
PARENT=/var/lib/gentoo-optimization/bootstrap
DEST=$PARENT/binpkg-checkpoint-$COMMIT
STAGE=$PARENT/.binpkg-checkpoint-$COMMIT.partial.$$
doas test ! -e "$DEST"
doas test ! -L "$DEST"
doas test ! -e "$STAGE"
doas test ! -L "$STAGE"
doas install -d -o root -g root -m 0700 "$STAGE"
doas install -o root -g root -m 0755 \
  scripts/optimization/recovery/create-binpkg-checkpoint.sh "$STAGE/"
doas install -o root -g root -m 0755 \
  scripts/optimization/recovery/verify-binpkg-snapshot.py "$STAGE/"
SCRIPT_SHA=$(sha256sum scripts/optimization/recovery/create-binpkg-checkpoint.sh | awk '{print $1}')
VERIFIER_SHA=$(sha256sum scripts/optimization/recovery/verify-binpkg-snapshot.py | awk '{print $1}')
test "$SCRIPT_SHA" = "$(doas sha256sum "$STAGE/create-binpkg-checkpoint.sh" | awk '{print $1}')"
test "$VERIFIER_SHA" = "$(doas sha256sum "$STAGE/verify-binpkg-snapshot.py" | awk '{print $1}')"
doas sync -f "$STAGE/create-binpkg-checkpoint.sh"
doas sync -f "$STAGE/verify-binpkg-snapshot.py"
doas sync -f "$STAGE"
doas mv --no-clobber --no-copy -T -- "$STAGE" "$DEST"
if doas test -e "$STAGE" || doas test -L "$STAGE"; then
  test "$SCRIPT_SHA" = "$(doas sha256sum "$DEST/create-binpkg-checkpoint.sh" | awk '{print $1}')"
  test "$VERIFIER_SHA" = "$(doas sha256sum "$DEST/verify-binpkg-snapshot.py" | awk '{print $1}')"
  doas rm -rf -- "$STAGE"
fi
doas test ! -e "$STAGE" && doas test ! -L "$STAGE"
doas test -d "$DEST"
test "$SCRIPT_SHA" = "$(doas sha256sum "$DEST/create-binpkg-checkpoint.sh" | awk '{print $1}')"
test "$VERIFIER_SHA" = "$(doas sha256sum "$DEST/verify-binpkg-snapshot.py" | awk '{print $1}')"
doas sync -f "$DEST"
doas sync -f "$PARENT"
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

Enter a clean root shell and create a private evidence directory. Every later
command in this runbook runs in that shell, so root-private existence checks do
not accidentally become false permission-denied results.

```bash
doas env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C LC_ALL=C TZ=UTC /bin/bash --noprofile --norc
set -Eeuo pipefail
umask 077
ID=pre-candidate-a-deps-YYYYMMDDTHHMMSSZ
COMMIT=REVIEWED_COMMIT
BOOTSTRAP=/var/lib/gentoo-optimization/bootstrap/binpkg-checkpoint-$COMMIT
EVIDENCE=/root/checkpoint-evidence-$ID
install -d -o root -g root -m 0700 "$EVIDENCE"
```

Reject any visible package mutation before capturing inputs:

```bash
if pgrep -af '(^|/)(emerge|ebuild|ebuild\.sh|emaint|quickpkg)( |$)' \
    >"$EVIDENCE/portage-processes.preflight.txt"; then
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
python3 -I -B "$VERIFIER" \
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
payload bytes for the delta. Two source copies plus two delta copies and 20
percent transaction/index overhead must fit:

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
REQUIRED_BYTES=$(( (2 * SOURCE_BYTES + 2 * DELTA_BYTES) * 120 / 100 ))
AVAILABLE_BYTES=$(df --output=avail --block-size=1 \
  /var/cache/gentoo-optimization/binpkgs | tail -1 | tr -d ' ')
printf 'source_bytes=%s\ndelta_bytes=%s\nrequired_bytes=%s\navailable_bytes=%s\n' \
  "$SOURCE_BYTES" "$DELTA_BYTES" "$REQUIRED_BYTES" "$AVAILABLE_BYTES" \
  >"$EVIDENCE/space-preflight.txt"
test "$AVAILABLE_BYTES" -ge "$REQUIRED_BYTES"
df --block-size=1 /var/cache/gentoo-optimization/binpkgs \
  /var/lib/gentoo-optimization/recovery/binpkgs \
  >"$EVIDENCE/df.preflight.txt"
```

If cache and durable parents are on different filesystems, apply the bound to
each filesystem independently rather than pooling free space.

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

Choose exactly one CPV from the bound delta. The checkpoint program, not the
operator, selects and hashes the matching archive, captures the VDB before and
after, runs the exact trusted command below, proves that only the selected CPV's
VDB subtree changed, runs `qcheck`, reruns the complete GPKG verifier, and
publishes the immutable attempt ledger and terminal receipt.

```text
/usr/bin/emerge --ignore-default-opts --offline --usepkgonly \
  --getbinpkg=n --nodeps --oneshot =CATEGORY/PACKAGE-VERSION
```

Run the finalizer with no externally created evidence envelopes:

```bash
RESTORE_CPV=${DELTA_ATOMS[0]#=}
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
! pgrep -af '(^|/)(emerge|ebuild|ebuild\.sh|emaint|quickpkg)( |$)'
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
python3 -I -B - "$EVIDENCE/final-report.paths0" \
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

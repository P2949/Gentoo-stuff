# Phase 2 production profile transaction and evidence runbook

This is the operator contract for closing Phase 2. It does not activate an
optimization generation and it does not freeze the Phase 3 inventory. The only
authorized production sample-PGO exercise is a disposable validation generation
supervised by the installed, candidate-bound coordinator.

All commands below run from one exact clean commit. Replace values in angle
brackets once, record them, and do not reuse a run ID or output directory. Stop
on the first unexpected result. Never remove or edit a transaction journal,
child sidecar, authorization file, receipt, token-scan result, or stable lock by
hand.

## Trust and non-circular evidence model

The final evidence index is deliberately detached from both Git and the evidence
directory:

1. Six canonical markers in the plan bind current source hashes to the exact
   checked Phase 2 checklist lines they prove.
2. A clean commit fixes the plan, policy, source, tests, and marker bytes.
3. The live gate produces root-owned test evidence and component states for that
   same commit.
4. The detached index binds the commit/tree, complete plan hash, source
   inventory, live tools, test rows, complete evidence-tree manifest, and every
   component-state hash.

The plan must not record the detached index hash or evidence-manifest hash. That
would create an impossible hash fixed point because the index hashes the plan.
For the same reason, the tracked plan cannot truthfully embed the current commit
ID, post-commit test totals, or hashes of state/evidence produced for its own
commit: adding those values necessarily creates a different commit which has
not run the recorded gate. Plan markers therefore contain only precomputable
source hashes, exact checked-line identities, commands, and acceptance
requirements. The detached root-owned index is the sole holder of the current
commit/tree, current test totals, current component-state hashes, and current
evidence-manifest hash. Any literal commit, totals, or evidence hashes retained
in Phase 2 prose must be labeled historical and superseded.

The plan may state the stable directory and naming convention only. The detached
index is the sole current authorization object. Any edit to the plan after
capture invalidates the index and requires a new commit, live gate, component
states, and index.

## Close Phase 2 with two exact passes

Never check a live Phase 2 requirement from repository-only evidence. Closure
uses two commits/passes:

1. Commit the complete implementation as clean candidate A while the live
   Phase 2 checkboxes remain open. Install A and run the complete host gate and
   coordinator-supervised production sample transaction described below with a
   preliminary, never-reused run ID. Preserve its raw logs and receipt. It is
   implementation proof only: the open plan blocks component/index capture, so
   A cannot authorize Phase 2.
2. Only after A passes, update and reread the entire plan, check the now-proven
   Phase 2 items, add the exact claim markers, and commit clean candidate B.
   Allocate a new run ID, reinstall B, and rerun every host and production gate
   from the beginning. Generate components and the detached index only for B.

No source, test, policy, plan, or marker edit is allowed after candidate B. Any
change creates candidate C and requires the complete authoritative pass again.
The A result never substitutes for the B result; it merely makes the B plan
checkboxes truthful before B is committed.

After the A precheck and before committing B, migrate the Phase 2 plan section
as follows:

- Add exactly one
  `<!-- gentoo-optimization-phase2-prior-evidence: superseded-by-detached-index -->`
  banner.
- Prefix every older Phase 2 paragraph which names a commit, SHA-256, or
  `/var/lib/gentoo-optimization/reports` or `state` path with
  `> Historical Phase 2 evidence (superseded; not authorization): `.
- Check every genuinely complete Phase 2 item. An unchecked item blocks the
  boundary.
- Cover every checked Phase 2 checkbox exactly once across the six policy claim
  markers. Generate each marker instead of typing it:

```sh
python3 scripts/optimization/verify/phase2-evidence.py plan-marker \
  --repository-root "$PWD" \
  --claim-id phase2-dispatcher \
  --checkbox-line <LINE> [--checkbox-line <LINE> ...]
```

The claim IDs are `phase2-automation`, `phase2-bolt-hooks`,
`phase2-dispatcher`, `phase2-evidence-index`, `phase2-framework`, and
`phase2-sample-pgo`. Insert the emitted one-line comment inside Phase 2. Capture
rejects missing, duplicate, non-canonical, out-of-section, stale, or incomplete
coverage.

## Create candidate B's immutable source snapshot

Make the working tree clean and commit the plan migration. The commit used below
is candidate B; there must be no later plan-only commit.

```sh
git status --short
git diff --check
COMMIT=$(git rev-parse --verify 'HEAD^{commit}')
test -z "$(git status --porcelain=v1 --untracked-files=all)"
test -z "$(git ls-files -s | awk '$1 == 160000 { print }')"
printf '%s\n' "$COMMIT"
```

Create a bundle as the desktop user, then copy and verify it before root consumes
it. A bundle avoids executing root from the mutable checkout and preserves the
exact original commit and Git metadata needed by the installer tests.

```sh
SHORT=$(printf '%s' "$COMMIT" | cut -c1-12)
UTC_RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
RUN_ID=phase2-${SHORT}-${UTC_RUN_ID}
BUNDLE_USER=/var/tmp/gentoo-${RUN_ID}.bundle.user
BUNDLE=/var/lib/gentoo-optimization/bootstrap/source-bundles/${RUN_ID}.bundle
SOURCE=/var/lib/gentoo-optimization/bootstrap/source-checkouts/${RUN_ID}

test "$(git rev-parse --verify 'HEAD^{commit}')" = "$COMMIT"
test ! -e "$BUNDLE_USER"
git bundle create "$BUNDLE_USER" HEAD
BUNDLE_SHA256=$(sha256sum "$BUNDLE_USER" | awk '{print $1}')
git bundle verify "$BUNDLE_USER"
test "$(git bundle list-heads "$BUNDLE_USER" | awk '$2 == "HEAD" {print $1}')" = "$COMMIT"

doas install -d -o root -g root -m 0700 "${BUNDLE%/*}" "${SOURCE%/*}"
doas test ! -e "$BUNDLE"
doas test ! -e "${BUNDLE}.partial"
doas test ! -e "$SOURCE"
doas test ! -e "${SOURCE}.partial"
doas install -o root -g root -m 0600 -T "$BUNDLE_USER" "${BUNDLE}.partial"
doas sha256sum "${BUNDLE}.partial"
# The printed digest must equal BUNDLE_SHA256 before continuing.
doas mv --no-clobber --no-copy -T "${BUNDLE}.partial" "$BUNDLE"
doas test ! -e "${BUNDLE}.partial"
doas sync -f "${BUNDLE%/*}"
test "$(doas git bundle list-heads "$BUNDLE" | awk '$2 == "HEAD" {print $1}')" = "$COMMIT"
doas git -c core.hooksPath=/dev/null -c core.fsmonitor=false \
  clone --no-checkout "$BUNDLE" "${SOURCE}.partial"
doas git -C "${SOURCE}.partial" -c core.hooksPath=/dev/null \
  checkout --detach "$COMMIT"
doas test -z "$(doas git -C "${SOURCE}.partial" status --porcelain=v1 --untracked-files=all)"
doas mv --no-clobber --no-copy -T "${SOURCE}.partial" "$SOURCE"
doas test ! -e "${SOURCE}.partial"
doas sync -f "$SOURCE" "${SOURCE%/*}"
doas test "$(doas git -C "$SOURCE" rev-parse --verify 'HEAD^{commit}')" = "$COMMIT"
doas git -C "$SOURCE" bundle verify "$BUNDLE"
```

Retain the bundle, its digest, and the root-owned checkout. Do not rebuild either
in place. If any verification differs, allocate new paths.

## Install and preflight the exact candidate

Publish the installer from the immutable checkout and compare both hashes before
executing it:

```sh
BOOTSTRAP=/var/lib/gentoo-optimization/bootstrap/install-framework.sh
doas install -o root -g root -m 0755 -T \
  "$SOURCE/scripts/optimization/install-framework.sh" "${BOOTSTRAP}.partial"
doas sha256sum "$SOURCE/scripts/optimization/install-framework.sh" "${BOOTSTRAP}.partial"
doas mv -T "${BOOTSTRAP}.partial" "$BOOTSTRAP"
doas sync -f "${BOOTSTRAP%/*}"

doas "$BOOTSTRAP" --source-root "$SOURCE"
doas "$BOOTSTRAP" --source-root "$SOURCE" --check
readlink -e /var/lib/gentoo-optimization/framework-current
```

Also prove containment and recover any earlier interrupted transaction before
starting tests:

```sh
doas /usr/bin/unshare --pid --fork --kill-child=KILL --mount-proc -- /bin/true
COORD=/usr/local/libexec/gentoo-optimization/pgo/production-profile-lock-transaction.py
SCAN=/usr/local/libexec/gentoo-optimization/pgo/authorization-token-scan.py
doas /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C LC_ALL=C TZ=UTC "$COORD" recover
```

`CLEAN` means no recovery was needed; `RECOVERED` means the coordinator safely
restored an interrupted transaction. Any error is a hard stop. Recovery must
leave the journal and its `.partial`/`.child.json` objects absent and the stable
lock payloads empty. Preserve the generated recovery receipt.

## Run the complete host gate

The authoritative runner is the root-owned checkout, not the desktop checkout.
Use one new parent directory. The evidence policy requires capabilities mode,
all required named cases, six PGO/BOLT capability passes, zero failures, and
zero skips.

```sh
EVIDENCE_ROOT=/var/tmp/gentoo-optimization/phase2-authoritative-${RUN_ID}
test ! -e "$EVIDENCE_ROOT"
doas /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/lib/llvm/22/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C LC_ALL=C TZ=UTC \
  SHELLCHECK=/var/lib/gentoo-optimization/test-tools/shellcheck-0.11.0 \
  "$SOURCE/tests/run-optimization-tests.sh" --mode capabilities \
  --capability all --output-dir "$EVIDENCE_ROOT"

awk -F '\t' 'NR > 1 { count[$1]++ } END {
  printf "PASS=%d FAIL=%d SKIP=%d TOTAL=%d\n",
    count["PASS"], count["FAIL"], count["SKIP"], NR - 1
}' "$EVIDENCE_ROOT/results.tsv"
grep -Fx 'mode=capabilities' "$EVIDENCE_ROOT/summary.txt"
grep -Fx 'fail=0' "$EVIDENCE_ROOT/summary.txt"
grep -Fx 'skip=0' "$EVIDENCE_ROOT/summary.txt"
```

The isolated sample lane remains a diagnostic cross-check. The separately named
`portage-sample-pgo-live-policy-integration` is authoritative for normal live
Portage `FEATURES`; both must pass. In the live lane, retain the exact ordered
`FEATURES` string resolved from the host, the exact ordered effective token
sequence observed by every Portage phase, and the final clean-environment
re-resolution after the workload. `FEATURES` is last-token-wins policy: a
sorted set is not an acceptable identity. The lane must also bind its
disposable `PORTAGE_TMPDIR`, `PORTAGE_LOGDIR`, `PORTAGE_DEPCACHEDIR`, `DISTDIR`,
`PKGDIR`, `CCACHE_DIR`, `CCACHE_TEMPDIR`, and `SCCACHE_DIR`. Every ebuild entry
starts from an explicit clean environment. Retain and byte-compare
`live-policy-before.json` with `live-policy-after.json` and
`protected-live-state.before.json` with `protected-live-state.after.json`.
Those sentinels cover the trusted live configuration/profile/repository/tool
identity and `/var/db/pkg`, `/var/cache/edb`, `/var/log/portage`, plus the live
dependency-cache, log, distfile, and binpkg roots. `portage-policy.tsv` must
record the disposable roots and exact reviewed stage deltas. Any unexpected
live-state mutation or final policy re-resolution drift is a hard failure.

A direct `--portage-policy live` invocation is still diagnostic and cannot
authorize Phase 2. Neither it nor the isolated lane may produce an
authorization object, even when all functional checks pass. The only
authoritative sample result is the `env -i` coordinator-supervised
`--production-locks --portage-policy live` transaction below, followed by
successful component-state generation and detached-index capture and
verification for the same clean commit.

## Run the supervised production-lock sample-PGO gate

Create a disposable validation identity record. It is not a Phase 3 frozen
inventory and must never be assigned as the active optimization generation.
Publish it root-owned, retain it, and use its exact digest:

```sh
GENERATION_ID=phase2-validation-${RUN_ID}
INVENTORY_ID=phase2-validation-input-${RUN_ID}
VALIDATION_INPUT=/var/lib/gentoo-optimization/state/project/${INVENTORY_ID}.json

doas install -d -o root -g root -m 0700 "${VALIDATION_INPUT%/*}"
doas test ! -e "$VALIDATION_INPUT"
doas test ! -e "${VALIDATION_INPUT}.partial"
doas /usr/bin/env -i COMMIT="$COMMIT" INVENTORY_ID="$INVENTORY_ID" \
  VALIDATION_INPUT="$VALIDATION_INPUT" PATH=/usr/bin:/bin \
  /bin/bash --noprofile --norc -c '
    set -euo pipefail
    set -o noclobber
    umask 0137
    printf '\''{"git_commit":"%s","inventory_id":"%s","purpose":"phase2-sample-pgo-validation-only"}\n'\'' \
      "$COMMIT" "$INVENTORY_ID" >"${VALIDATION_INPUT}.partial"
    chown root:root -- "${VALIDATION_INPUT}.partial"
    sync -f -- "${VALIDATION_INPUT}.partial" "${VALIDATION_INPUT%/*}"
    mv --no-clobber --no-copy -T -- "${VALIDATION_INPUT}.partial" "$VALIDATION_INPUT"
    test ! -e "${VALIDATION_INPUT}.partial"
  '
doas sync -f "${VALIDATION_INPUT%/*}"
INVENTORY_SHA256=$(doas sha256sum "$VALIDATION_INPUT" | awk '{print $1}')

WORK=/var/tmp/gentoo-optimization/phase2-sample-work-${RUN_ID}
PROFILE=/var/cache/gentoo-optimization/pgo/clang-sample/phase2-sample-gate-${RUN_ID}
STATE=/var/lib/gentoo-optimization/generations/${GENERATION_ID}/phase2-sample-gate-${RUN_ID}
PRODUCTION_EVIDENCE=${EVIDENCE_ROOT}/production-sample-pgo
TOKEN_SCAN=${STATE}/coordinator-token-scan.tsv
test ! -e "$WORK" && test ! -e "$PROFILE" && test ! -e "$STATE" && \
  test ! -e "$PRODUCTION_EVIDENCE"
```

The coordinator creates a 256-bit bearer token internally, writes only its
SHA-256 to durable state, passes the raw token to the child in its exact clean
environment, and passes it to the token scanner over an inherited file
descriptor. Operators never generate, print, export, or store the raw token.

```sh
doas /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C LC_ALL=C TZ=UTC \
  "$COORD" run \
  --generation-id "$GENERATION_ID" \
  --inventory-id "$INVENTORY_ID" \
  --inventory-sha256 "$INVENTORY_SHA256" \
  --gate-run-id "$RUN_ID" \
  --child-timeout-seconds 86400 \
  --token-scan-timeout-seconds 3600 \
  --kill-after-seconds 10 \
  --token-scanner "$SCAN" \
  --token-scan-root "$WORK" \
  --token-scan-root "$PROFILE" \
  --token-scan-root "$STATE" \
  --token-scan-root "$PRODUCTION_EVIDENCE" \
  --token-scan-output "$TOKEN_SCAN" \
  --evidence-output-root "$PRODUCTION_EVIDENCE" \
  -- "$SOURCE/tests/optimization/test-portage-sample-pgo-integration.sh" \
  --production-locks --portage-policy live \
  --output-dir "$PRODUCTION_EVIDENCE"
```

The four token-scan roots and their order are an exact coordinator contract.
The child executable and scanner must remain unchanged from arming through
completion. PID-namespace containment kills even escaped session/process-group
descendants if the coordinator dies.

On success, the coordinator prints the receipt path. Verify and retain:

```sh
JOURNAL=/var/lib/gentoo-optimization/state/profile-transactions/phase-2-production-profile-locks.pending
RECEIPT=${JOURNAL%/*}/phase-2-production-profile-locks-${GENERATION_ID}.receipt.json
doas test ! -e "$JOURNAL" && doas test ! -e "${JOURNAL}.partial" && \
  doas test ! -e "${JOURNAL}.child.json" && \
  doas test ! -e "${JOURNAL}.child.json.partial"
doas test ! -e "${RECEIPT}.partial" && \
  doas test ! -e "${RECEIPT}.interrupted-partial" && \
  doas test ! -e "${STATE}/transaction.authorization.partial" && \
  doas test ! -e "${STATE}/transaction.authorization.interrupted-partial"
doas jq -e '.status == "passed" and .child_exit_status == 0 and
  .token_scan.scanner_status == 0' "$RECEIPT"
doas grep -Fx $'passed\t-' "$TOKEN_SCAN"
doas "$BOOTSTRAP" --source-root "$SOURCE" --check
```

If the coordinator, terminal, kernel, or machine is interrupted, do not rerun
the child. Run the clean-environment `recover` command from the preflight
section, inspect the `recovered-interrupted` receipt, confirm no process remains,
allocate a new run ID and all-new artifact paths, and restart from preflight.
Never reuse partial profiles or evidence.

## Component states and detached evidence index

After the complete gate, generate eleven new root-owned component projections.
These do not replace the richer Phase 0/1 capability records. They live in the
dedicated immutable `phase-2-components/<RUN_ID>` namespace and are generated
by the evidence tool, never handwritten. A partial failed namespace remains as
unauthorized evidence; a retry uses a new run ID and new directory. Nothing is
archived, replaced, or selected through a mutable current pointer. Each
canonical document binds the run ID, clean commit, tree,
full-tree aggregate, finalized test-run provenance, results and summary,
required PASS rows and their log hashes, labeled external receipts, and three
zero terminal totals.

| Evidence name | Required state input |
| --- | --- |
| `automation` | Phase 2 complete-run aggregate |
| `bolt-hooks` | BOLT capture/deploy hook gate |
| `capability-bolt` | four-class BOLT capability |
| `capability-clang-ir` | Clang IR PGO capability |
| `capability-clang-sample` | Clang sample PGO capability |
| `capability-gcc` | GCC PGO capability |
| `capability-go` | Go PGO capability |
| `capability-rust` | Rust PGO capability |
| `dispatcher` | dispatcher/ABI/fingerprint gate |
| `framework-installer` | installed-candidate/check gate |
| `sample-pgo` | supervised production-lock sample gate |

`/var/tmp` is the sole sticky-boundary exception in production trust checks: it
must be exactly root-owned mode `01777`. The evidence root and every directory
and file below it must still be root-owned and group/other non-writable, with no
symlinks or special files. Copy the reviewed tool manifest and source bundle
into that evidence root so its manifest retains both:

```sh
doas install -o root -g root -m 0600 -T \
  "$SOURCE/optimization/phase2-tool-manifest.json" \
  "$EVIDENCE_ROOT/phase2-tools.json"
doas install -o root -g root -m 0600 -T "$BUNDLE" \
  "$EVIDENCE_ROOT/source-${COMMIT}.bundle"
doas sync -f "$EVIDENCE_ROOT"
```

Generate the deterministic state files. The framework component binds the
installed candidate manifest. The sample component additionally binds the
coordinator receipt, token-persistence scan, publication context, child
identity, and the exact retained validation-inventory JSON whose path, commit,
inventory ID, purpose, bytes, and SHA-256 feed the receipt. Existing state
output is a hard stop: never overwrite a component projection from another
evidence run.

```sh
EVIDENCE_TOOL=$SOURCE/scripts/optimization/verify/phase2-evidence.py
EVIDENCE_PY=/usr/bin/python3
PROVENANCE=$EVIDENCE_ROOT/test-run-provenance.json
RESULTS=$EVIDENCE_ROOT/results.tsv
SUMMARY=$EVIDENCE_ROOT/summary.txt
COMPONENT_PARENT=/var/lib/gentoo-optimization/state/project/phase-2-components
COMPONENT_ROOT=${COMPONENT_PARENT}/${RUN_ID}
FRAMEWORK_TARGET=$(doas readlink -e /var/lib/gentoo-optimization/framework-current)
FRAMEWORK_MANIFEST=$FRAMEWORK_TARGET/install.manifest
PUBLICATION_CONTEXT=$PRODUCTION_EVIDENCE/publication-context.tsv
CHILD_IDENTITY=$PRODUCTION_EVIDENCE/transaction-child-identity.json
doas install -d -o root -g root -m 0700 "$COMPONENT_PARENT"
doas test ! -e "$COMPONENT_ROOT"
doas install -d -o root -g root -m 0700 "$COMPONENT_ROOT"

phase2_evidence_tool() {
  doas /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
    PATH=/usr/lib/llvm/22/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C LC_ALL=C TZ=UTC PYTHONDONTWRITEBYTECODE=1 \
    "$EVIDENCE_PY" -I -B "$EVIDENCE_TOOL" "$@"
}

for component in automation bolt-hooks capability-bolt capability-clang-ir \
  capability-clang-sample capability-gcc capability-go capability-rust \
  dispatcher; do
  doas test ! -e "$COMPONENT_ROOT/${component}.json"
  phase2_evidence_tool component-state --production \
    --repository-root "$SOURCE" --component "$component" --run-id "$RUN_ID" \
    --evidence-root "$EVIDENCE_ROOT" --provenance "$PROVENANCE" \
    --results "$RESULTS" --summary "$SUMMARY" --git /usr/bin/git \
    --output "$COMPONENT_ROOT/${component}.json"
done

doas test ! -e "$COMPONENT_ROOT/framework-installer.json"
phase2_evidence_tool component-state --production \
  --repository-root "$SOURCE" --component framework-installer --run-id "$RUN_ID" \
  --evidence-root "$EVIDENCE_ROOT" --provenance "$PROVENANCE" \
  --results "$RESULTS" --summary "$SUMMARY" --git /usr/bin/git \
  --external-evidence framework-install-manifest="$FRAMEWORK_MANIFEST" \
  --output "$COMPONENT_ROOT/framework-installer.json"

doas test ! -e "$COMPONENT_ROOT/sample-pgo.json"
phase2_evidence_tool component-state --production \
  --repository-root "$SOURCE" --component sample-pgo --run-id "$RUN_ID" \
  --evidence-root "$EVIDENCE_ROOT" --provenance "$PROVENANCE" \
  --results "$RESULTS" --summary "$SUMMARY" --git /usr/bin/git \
  --external-evidence production-child-identity="$CHILD_IDENTITY" \
  --external-evidence production-publication-context="$PUBLICATION_CONTEXT" \
  --external-evidence production-token-scan="$TOKEN_SCAN" \
  --external-evidence production-transaction-receipt="$RECEIPT" \
  --external-evidence production-validation-input="$VALIDATION_INPUT" \
  --output "$COMPONENT_ROOT/sample-pgo.json"

INDEX_PARENT=/var/lib/gentoo-optimization/state/project/phase2-evidence
INDEX_DIR=${INDEX_PARENT}/${RUN_ID}
INDEX=${INDEX_DIR}/index.json
doas install -d -o root -g root -m 0700 "$INDEX_PARENT"
doas test ! -e "$INDEX_DIR"
doas install -d -o root -g root -m 0700 "$INDEX_DIR"

phase2_evidence_tool capture \
  --production \
  --repository-root "$SOURCE" \
  --evidence-root "$EVIDENCE_ROOT" \
  --tools "$EVIDENCE_ROOT/phase2-tools.json" \
  --test-results "$EVIDENCE_ROOT/results.tsv" \
  --test-summary "$EVIDENCE_ROOT/summary.txt" \
  --run-id "$RUN_ID" \
  --component-state automation="$COMPONENT_ROOT/automation.json" \
  --component-state bolt-hooks="$COMPONENT_ROOT/bolt-hooks.json" \
  --component-state capability-bolt="$COMPONENT_ROOT/capability-bolt.json" \
  --component-state capability-clang-ir="$COMPONENT_ROOT/capability-clang-ir.json" \
  --component-state capability-clang-sample="$COMPONENT_ROOT/capability-clang-sample.json" \
  --component-state capability-gcc="$COMPONENT_ROOT/capability-gcc.json" \
  --component-state capability-go="$COMPONENT_ROOT/capability-go.json" \
  --component-state capability-rust="$COMPONENT_ROOT/capability-rust.json" \
  --component-state dispatcher="$COMPONENT_ROOT/dispatcher.json" \
  --component-state framework-installer="$COMPONENT_ROOT/framework-installer.json" \
  --component-state sample-pgo="$COMPONENT_ROOT/sample-pgo.json" \
  --output "$INDEX"

phase2_evidence_tool verify --production --index "$INDEX"
doas jq -e '.aggregate == {
  "failed_total": 0, "pending_total": 0, "unknown_total": 0
}' "$INDEX"
```

Capture rejects a dirty or untracked worktree, wrong commit in any state,
missing/extra component or tool, changed requested symlink target, changed
resolved executable bytes, version-output drift, any failed/skipped required
test, stale checked-plan hash, unmarked checked item, unchecked Phase 2 item,
evidence-tree mutation, and any nonzero aggregate. Production verification also
requires the detached index, evidence, states, and resolved tools to have
root-owned non-writable real-path trust. The policy-pinned
`phase2-evidence/<RUN_ID>/index.json` is the sole authorization path; a copied
index is only an unauthoritative byte-for-byte historical copy.

Production verification is valid only on the same boot recorded by the test
provenance and production transaction receipt. If the machine reboots before
Phase 3 begins, preserve this run as historical evidence, allocate a new run
ID, and rerun the complete host gate, production sample transaction, component
states, and index capture. A prior index can never authorize a later boot.

Preserve the immutable checkout, bundle, complete evidence root, all component
states, validation-inventory JSON, transaction receipt,
authorization/sidecar/token-scan evidence, and detached index. Run
`verify --production` immediately before declaring the Phase 2 boundary and
again immediately before Phase 3 begins on that same boot.

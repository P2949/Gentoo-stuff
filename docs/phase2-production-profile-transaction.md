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
set -Eeuo pipefail
/usr/bin/python3 -I -B scripts/optimization/verify/phase2-evidence.py plan-marker \
  --repository-root "$PWD" \
  --claim-id phase2-dispatcher \
  --checkbox-line <LINE> [--checkbox-line <LINE> ...]
```

The claim IDs are `phase2-automation`, `phase2-bolt-hooks`,
`phase2-dispatcher`, `phase2-evidence-index`, `phase2-framework`, and
`phase2-sample-pgo`. Insert the emitted one-line comment inside Phase 2. Capture
rejects missing, duplicate, non-canonical, out-of-section, stale, or incomplete
coverage.

## Create the candidate's immutable source snapshot

Use this complete materialization procedure for candidate A and repeat it with
all-new paths for candidate B. For A, make the implementation working tree
clean and commit it while the live Phase 2 boxes remain open. For B, first
commit the truthful plan migration and canonical claim markers after A has
passed. There must be no later plan-only commit for either recorded run.

Start one clean reviewed operator shell first, keep that same shell open, and
run every source, bundle, and bootstrap block below inside it. The Git helper
uses a private environment and bounds every Git observation or mutation with a
ten-minute TERM/KILL deadline. A timeout returns nonzero and is a hard stop; it
does not authorize reusing a partial path.

The reviewed host-tool manifest binds `/usr/bin/doas` with the noninteractive
functional probe `-n /usr/bin/id -u`, which must exit zero and print exactly
`0`; this OpenDoas entry point has no successful version-printing option. The
operator transaction uses that exact entry point everywhere and never relies on
a PATH-selected privilege escalator.

```sh
OPERATOR=$(/usr/bin/id -un)
/usr/bin/env -i \
  HOME="$HOME" USER="$OPERATOR" LOGNAME="$OPERATOR" SHELL=/bin/bash \
  LANG=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC \
  /usr/bin/bash --noprofile --norc
```

```sh
set -Eeuo pipefail
[[ ${BASH} == /usr/bin/bash ]]
[[ ${PATH} == /usr/bin:/bin ]]
readonly GIT_TIMEOUT_SECONDS=600
readonly GIT_KILL_AFTER_SECONDS=10

user_git() {
  /usr/bin/env -i \
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_OPTIONAL_LOCKS=0 \
    HOME=/nonexistent LANG=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC \
    /usr/bin/timeout --signal=TERM \
    --kill-after="${GIT_KILL_AFTER_SECONDS}" "${GIT_TIMEOUT_SECONDS}" \
    /usr/bin/git --no-pager --no-replace-objects \
    -c core.hooksPath=/dev/null -c core.fsmonitor=false \
    -c core.attributesFile=/dev/null -c diff.external= "$@"
}

user_git status --short
user_git diff --check
COMMIT=$(user_git rev-parse --verify 'HEAD^{commit}')
WORKTREE_STATUS=$(user_git status --porcelain=v1 --untracked-files=all)
GITLINK_STATUS=$(user_git ls-files -s | /usr/bin/awk '$1 == 160000 { print }')
[[ -z ${WORKTREE_STATUS} ]]
[[ -z ${GITLINK_STATUS} ]]
printf '%s\n' "$COMMIT"
```

Create a bundle as the desktop user, then copy and verify it before root consumes
it. A bundle avoids executing root from the mutable checkout and preserves the
exact original commit and Git metadata needed by the installer tests.

```sh
set -Eeuo pipefail
[[ ${BASH} == /usr/bin/bash ]]
[[ ${PATH} == /usr/bin:/bin ]]
declare -F user_git >/dev/null
SHORT=$(printf '%s' "$COMMIT" | /usr/bin/cut -c1-12)
UTC_RUN_ID=$(/usr/bin/date -u +%Y%m%dT%H%M%SZ)
RUN_ID=phase2-${SHORT}-${UTC_RUN_ID}
BUNDLE_USER=/var/tmp/gentoo-${RUN_ID}.bundle.user
BUNDLE=/var/lib/gentoo-optimization/bootstrap/source-bundles/${RUN_ID}.bundle
SOURCE=/var/lib/gentoo-optimization/bootstrap/source-checkouts/${RUN_ID}
ROOT_GIT_HOME=/var/lib/gentoo-optimization/bootstrap/git-homes/${RUN_ID}

OBSERVED_COMMIT=$(user_git rev-parse --verify 'HEAD^{commit}')
[[ ${OBSERVED_COMMIT} == "${COMMIT}" ]]
[[ ! -e ${BUNDLE_USER} && ! -L ${BUNDLE_USER} ]]
user_git bundle create "$BUNDLE_USER" HEAD
BUNDLE_SHA256=$(/usr/bin/sha256sum "$BUNDLE_USER" | /usr/bin/awk '{print $1}')
user_git bundle verify "$BUNDLE_USER"
BUNDLE_HEAD=$(user_git bundle list-heads "$BUNDLE_USER" |
  /usr/bin/awk '$2 == "HEAD" {print $1}')
[[ ${BUNDLE_HEAD} == "${COMMIT}" ]]

root_run() {
  if [[ $# -eq 0 || $1 != /* ]]; then
    printf 'root_run requires an absolute command path\n' >&2
    return 64
  fi
  /usr/bin/doas /usr/bin/env -i \
    HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
    LANG=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC "$@"
}

root_absent() {
  root_run /usr/bin/bash --noprofile --norc -c \
    '[[ ! -e $1 && ! -L $1 ]]' bash "$1"
}

root_run /usr/bin/install -d -o root -g root -m 0700 \
  "${BUNDLE%/*}" "${SOURCE%/*}" "${ROOT_GIT_HOME%/*}"
root_absent "$ROOT_GIT_HOME"
root_run /usr/bin/install -d -o root -g root -m 0700 "$ROOT_GIT_HOME"
root_absent "$BUNDLE"
root_absent "${BUNDLE}.partial"
root_absent "$SOURCE"
root_absent "${SOURCE}.partial"
root_run /usr/bin/install -o root -g root -m 0600 -T \
  "$BUNDLE_USER" "${BUNDLE}.partial"
COPIED_BUNDLE_SHA256=$(root_run /usr/bin/sha256sum "${BUNDLE}.partial" |
  /usr/bin/awk '{print $1}')
[[ ${COPIED_BUNDLE_SHA256} == "${BUNDLE_SHA256}" ]]
root_run /usr/bin/mv --no-clobber --no-copy -T "${BUNDLE}.partial" "$BUNDLE"
root_absent "${BUNDLE}.partial"
root_run /usr/bin/sync -f "${BUNDLE%/*}"

root_git() {
  /usr/bin/doas /usr/bin/env -i \
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_OPTIONAL_LOCKS=0 \
    HOME="$ROOT_GIT_HOME" \
    LANG=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC \
    /usr/bin/timeout --signal=TERM \
    --kill-after="${GIT_KILL_AFTER_SECONDS}" "${GIT_TIMEOUT_SECONDS}" \
    /usr/bin/git --no-pager --no-replace-objects \
    -c core.hooksPath=/dev/null -c core.fsmonitor=false \
    -c core.attributesFile=/dev/null -c diff.external= "$@"
}

ROOT_BUNDLE_HEAD=$(root_git bundle list-heads "$BUNDLE" |
  /usr/bin/awk '$2 == "HEAD" {print $1}')
[[ ${ROOT_BUNDLE_HEAD} == "${COMMIT}" ]]
root_git clone --no-checkout "$BUNDLE" "${SOURCE}.partial"
root_git -C "${SOURCE}.partial" checkout --detach "$COMMIT"
ROOT_WORKTREE_STATUS=$(root_git -C "${SOURCE}.partial" \
  status --porcelain=v1 --untracked-files=all)
[[ -z ${ROOT_WORKTREE_STATUS} ]]
root_run /usr/bin/mv --no-clobber --no-copy -T "${SOURCE}.partial" "$SOURCE"
root_absent "${SOURCE}.partial"
root_run /usr/bin/sync -f "$SOURCE" "${SOURCE%/*}"
ROOT_SOURCE_COMMIT=$(root_git -C "$SOURCE" rev-parse --verify 'HEAD^{commit}')
[[ ${ROOT_SOURCE_COMMIT} == "${COMMIT}" ]]
root_git -C "$SOURCE" bundle verify "$BUNDLE"
```

Retain each candidate's bundle, digest, private Git home, and root-owned
checkout. Do not rebuild any of them in place. If any verification differs,
allocate new paths. Candidate A stops after the complete host and supervised
production transaction precheck; component-state and detached-index generation
remain blocked by its deliberately open plan. Candidate B repeats every step
with a new run ID and is the only pass that may continue into index capture.

## Install and preflight the exact candidate

Publish the installer from the immutable checkout and compare both hashes before
executing it:

```sh
set -Eeuo pipefail
[[ ${BASH} == /usr/bin/bash ]]
[[ ${PATH} == /usr/bin:/bin ]]
declare -F root_run >/dev/null
declare -F root_absent >/dev/null
BOOTSTRAP=/var/lib/gentoo-optimization/bootstrap/install-framework.sh
SOURCE_BOOTSTRAP_SHA256=$(root_run /usr/bin/sha256sum \
  "$SOURCE/scripts/optimization/install-framework.sh" | /usr/bin/awk '{print $1}')
root_absent "${BOOTSTRAP}.partial"
root_run /usr/bin/install -o root -g root -m 0755 -T \
  "$SOURCE/scripts/optimization/install-framework.sh" "${BOOTSTRAP}.partial"
COPIED_BOOTSTRAP_SHA256=$(root_run /usr/bin/sha256sum "${BOOTSTRAP}.partial" |
  /usr/bin/awk '{print $1}')
[[ ${COPIED_BOOTSTRAP_SHA256} == "${SOURCE_BOOTSTRAP_SHA256}" ]]
root_run /usr/bin/mv --no-copy -T "${BOOTSTRAP}.partial" "$BOOTSTRAP"
root_run /usr/bin/sync -f "${BOOTSTRAP%/*}"

root_run "$BOOTSTRAP" --source-root "$SOURCE"
root_run "$BOOTSTRAP" --source-root "$SOURCE" --check
root_run /usr/bin/readlink -e /var/lib/gentoo-optimization/framework-current
```

Also prove containment and recover any earlier interrupted transaction before
starting tests:

```sh
set -Eeuo pipefail
COORD=/usr/local/libexec/gentoo-optimization/pgo/production-profile-lock-transaction.py
SCAN=/usr/local/libexec/gentoo-optimization/pgo/authorization-token-scan.py
CONTAINMENT_PREFLIGHT=$(doas /usr/bin/env -i \
  HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/bin:/bin \
  LANG=C LC_ALL=C TZ=UTC "$COORD" preflight-containment)
test "$CONTAINMENT_PREFLIGHT" = PREFLIGHT-PASS
doas /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/bin:/bin \
  LANG=C LC_ALL=C TZ=UTC "$COORD" recover
```

The containment command must print exactly `PREFLIGHT-PASS`; it functionally
proves both kill-child PID namespaces and exact pidfd open/signal/close/teardown
using the same root-trusted primitives as the production transaction. `CLEAN`
means no recovery was needed; `RECOVERED` means the coordinator safely restored
an interrupted transaction. Any error is a hard stop. Recovery must
leave the journal and its `.partial`/`.child.json` objects absent and the stable
lock payloads empty. Preserve the generated recovery receipt.

## Run the complete host gate

The authoritative runner is the root-owned checkout, not the desktop checkout.
Use one new parent directory. The evidence policy requires authoritative mode,
all portable and stress cases, all required named cases, six PGO/BOLT
capability passes, zero failures, zero top-level skips, and zero required
internal skips.

```sh
set -Eeuo pipefail
EVIDENCE_ROOT=/var/tmp/gentoo-optimization/phase2-authoritative-${RUN_ID}
doas test ! -e "$EVIDENCE_ROOT"
doas /usr/bin/env -i HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC \
  /usr/bin/python3 -I -B -c \
  'from jsonschema import Draft202012Validator; Draft202012Validator.check_schema({"$schema": "https://json-schema.org/draft/2020-12/schema"})'
doas /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/bin:/usr/lib/llvm/22/bin:/bin \
  LANG=C LC_ALL=C TZ=UTC \
  SHELLCHECK=/var/lib/gentoo-optimization/test-tools/shellcheck-0.11.0 \
  /usr/bin/bash "$SOURCE/tests/run-optimization-tests.sh" --mode authoritative \
  --capability all --output-dir "$EVIDENCE_ROOT"

doas awk -F '\t' 'NR > 1 { count[$1]++ } END {
  printf "PASS=%d FAIL=%d SKIP=%d TOTAL=%d\n",
    count["PASS"], count["FAIL"], count["SKIP"], NR - 1
}' "$EVIDENCE_ROOT/results.tsv"
doas grep -Fx 'mode=authoritative' "$EVIDENCE_ROOT/summary.txt"
doas grep -Fx 'authoritative=1' "$EVIDENCE_ROOT/summary.txt"
doas grep -Fx 'fail=0' "$EVIDENCE_ROOT/summary.txt"
doas grep -Fx 'skip=0' "$EVIDENCE_ROOT/summary.txt"
doas grep -Fx 'required_subtest_fail=0' "$EVIDENCE_ROOT/summary.txt"
doas grep -Fx 'required_subtest_skip=0' "$EVIDENCE_ROOT/summary.txt"
doas grep -Fx 'mandatory_internal_skip=0' "$EVIDENCE_ROOT/summary.txt"
```

The authoritative driver must itself be invoked with `/usr/bin/bash` as Bash
argv zero. Before it trusts PATH, repository-path setup and diagnostics use
only Bash builtins. It then pins the reviewed `bash`, `env`, `git`, `python3`,
`setsid`, `shellcheck`, `sleep`, and `timeout` entry points and rejects any PATH
shadow before running a case. The ShellCheck case uses the already-bound
entry point rather than independently accepting `SHELLCHECK`; portable runs
record their selected ShellCheck entry point. Finalized provenance records the
requested entry points and resolved executable identities, additionally binds
the active Bash process and Python runtime that executed the evidence helper,
and is independently compared with the reviewed tool manifest by the detached
index. A matching version string alone is not an execution identity.

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
set -Eeuo pipefail
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
doas test ! -e "$WORK" && doas test ! -e "$PROFILE" && \
  doas test ! -e "$STATE" && doas test ! -e "$PRODUCTION_EVIDENCE"
```

The coordinator creates a 256-bit bearer token internally, writes only its
SHA-256 to durable state, passes the raw token to the child in its exact clean
environment, and passes it to the token scanner over an inherited file
descriptor. Operators never generate, print, export, or store the raw token.

```sh
set -Eeuo pipefail
doas /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
  PATH=/usr/bin:/bin \
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
set -Eeuo pipefail
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
section and inspect the resulting terminal receipt. An interruption before
durable receipt rename plus parent-directory fsync produces
`recovered-interrupted`; an interruption after that commit boundary preserves
the already truthful `passed` or `failed` receipt and exact child status even
though the coordinator itself returns `128 + signal`. In either case, confirm
that no process remains, allocate a new run ID and all-new artifact paths, and
restart from preflight. Never reuse partial profiles or evidence.

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
set -Eeuo pipefail
doas install -o root -g root -m 0600 -T \
  "$SOURCE/optimization/phase2-tool-manifest.json" \
  "$EVIDENCE_ROOT/phase2-tools.json"
doas install -o root -g root -m 0600 -T "$BUNDLE" \
  "$EVIDENCE_ROOT/source-${COMMIT}.bundle"
doas sync -f "$EVIDENCE_ROOT"
```

Generate the deterministic state files. The automation component binds the
immutable Candidate-A bootstrap plus the complete pre-checkpoint, prerequisite
success, and post-checkpoint chain. Its semantic verifier replays every durable
reference, requires both checkpoint lanes to be `offline-restore-proven` at
`0/0/0`, proves the prerequisite plan is the post-checkpoint delta, and proves
the Candidate-A bootstrap commit is an ancestor whose three executable payloads
are byte- and mode-identical in Candidate B. The framework component binds the
installed candidate manifest. The sample component additionally binds the
coordinator receipt, token-persistence scan, publication context, child
identity, and the exact retained validation-inventory JSON whose path, commit,
inventory ID, purpose, bytes, and SHA-256 feed the receipt. Existing state
output is a hard stop: never overwrite a component projection from another
evidence run.

```sh
set -Eeuo pipefail
EVIDENCE_TOOL=$SOURCE/scripts/optimization/verify/phase2-evidence.py
EVIDENCE_PY=/usr/bin/python3
PROVENANCE=$EVIDENCE_ROOT/test-run-provenance.json
RESULTS=$EVIDENCE_ROOT/results.tsv
SUBTESTS=$EVIDENCE_ROOT/subtests.tsv
SUMMARY=$EVIDENCE_ROOT/summary.txt
COMPONENT_PARENT=/var/lib/gentoo-optimization/state/project/phase-2-components
COMPONENT_ROOT=${COMPONENT_PARENT}/${RUN_ID}
FRAMEWORK_TARGET=$(doas readlink -e /var/lib/gentoo-optimization/framework-current)
FRAMEWORK_MANIFEST=$FRAMEWORK_TARGET/install.manifest
PUBLICATION_CONTEXT=$PRODUCTION_EVIDENCE/publication-context.tsv
CHILD_IDENTITY=$PRODUCTION_EVIDENCE/transaction-child-identity.json
: "${PREREQUISITE_SOURCE_COMMIT:?re-enter the exact Candidate-A bootstrap commit}"
: "${PRE_CHECKPOINT_ID:?re-enter the terminal pre-dependency checkpoint ID}"
: "${JSONSCHEMA_INSTALL_ID:?re-enter the successful prerequisite transaction ID}"
: "${POST_CHECKPOINT_ID:?re-enter the terminal post-dependency checkpoint ID}"
JSONSCHEMA_BOOTSTRAP_MANIFEST=/var/lib/gentoo-optimization/bootstrap/jsonschema-prerequisite-$PREREQUISITE_SOURCE_COMMIT/bootstrap-manifest.json
JSONSCHEMA_PRE_CHECKPOINT_TERMINAL=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$PRE_CHECKPOINT_ID.offline-restore-proven.json
JSONSCHEMA_PRE_CHECKPOINT_RECEIPT=/var/lib/gentoo-optimization/reports/checkpoint-$PRE_CHECKPOINT_ID/offline-restore-receipt.json
JSONSCHEMA_PRE_CHECKPOINT_OPERATOR_MANIFEST=/var/lib/gentoo-optimization/reports/checkpoint-$PRE_CHECKPOINT_ID-operator-evidence/operator-evidence.manifest.json
JSONSCHEMA_PREREQUISITE_SUCCESS=/var/lib/gentoo-optimization/state/project/jsonschema-prerequisite-$JSONSCHEMA_INSTALL_ID.success.json
JSONSCHEMA_POST_CHECKPOINT_TERMINAL=/var/lib/gentoo-optimization/state/project/binpkg-checkpoint-$POST_CHECKPOINT_ID.offline-restore-proven.json
JSONSCHEMA_POST_CHECKPOINT_RECEIPT=/var/lib/gentoo-optimization/reports/checkpoint-$POST_CHECKPOINT_ID/offline-restore-receipt.json
JSONSCHEMA_POST_CHECKPOINT_OPERATOR_MANIFEST=/var/lib/gentoo-optimization/reports/checkpoint-$POST_CHECKPOINT_ID-operator-evidence/operator-evidence.manifest.json
doas install -d -o root -g root -m 0700 "$COMPONENT_PARENT"
doas test ! -e "$COMPONENT_ROOT"
doas install -d -o root -g root -m 0700 "$COMPONENT_ROOT"

phase2_evidence_tool() {
  doas /usr/bin/env -i HOME=/root USER=root LOGNAME=root SHELL=/bin/bash \
    PATH=/usr/bin:/usr/lib/llvm/22/bin:/bin \
    LANG=C LC_ALL=C TZ=UTC PYTHONDONTWRITEBYTECODE=1 \
    "$EVIDENCE_PY" -I -B "$EVIDENCE_TOOL" "$@"
}

doas test ! -e "$COMPONENT_ROOT/automation.json"
phase2_evidence_tool component-state --production \
  --repository-root "$SOURCE" --component automation --run-id "$RUN_ID" \
  --evidence-root "$EVIDENCE_ROOT" --provenance "$PROVENANCE" \
  --results "$RESULTS" --subtests "$SUBTESTS" --summary "$SUMMARY" \
  --git /usr/bin/git \
  --external-evidence jsonschema-bootstrap-manifest="$JSONSCHEMA_BOOTSTRAP_MANIFEST" \
  --external-evidence jsonschema-post-checkpoint-offline-restore-receipt="$JSONSCHEMA_POST_CHECKPOINT_RECEIPT" \
  --external-evidence jsonschema-post-checkpoint-operator-manifest="$JSONSCHEMA_POST_CHECKPOINT_OPERATOR_MANIFEST" \
  --external-evidence jsonschema-post-checkpoint-terminal-state="$JSONSCHEMA_POST_CHECKPOINT_TERMINAL" \
  --external-evidence jsonschema-pre-checkpoint-offline-restore-receipt="$JSONSCHEMA_PRE_CHECKPOINT_RECEIPT" \
  --external-evidence jsonschema-pre-checkpoint-operator-manifest="$JSONSCHEMA_PRE_CHECKPOINT_OPERATOR_MANIFEST" \
  --external-evidence jsonschema-pre-checkpoint-terminal-state="$JSONSCHEMA_PRE_CHECKPOINT_TERMINAL" \
  --external-evidence jsonschema-prerequisite-success-state="$JSONSCHEMA_PREREQUISITE_SUCCESS" \
  --output "$COMPONENT_ROOT/automation.json"

for component in bolt-hooks capability-bolt capability-clang-ir \
  capability-clang-sample capability-gcc capability-go capability-rust \
  dispatcher; do
  doas test ! -e "$COMPONENT_ROOT/${component}.json"
  phase2_evidence_tool component-state --production \
    --repository-root "$SOURCE" --component "$component" --run-id "$RUN_ID" \
    --evidence-root "$EVIDENCE_ROOT" --provenance "$PROVENANCE" \
    --results "$RESULTS" --subtests "$SUBTESTS" --summary "$SUMMARY" \
    --git /usr/bin/git \
    --output "$COMPONENT_ROOT/${component}.json"
done

doas test ! -e "$COMPONENT_ROOT/framework-installer.json"
phase2_evidence_tool component-state --production \
  --repository-root "$SOURCE" --component framework-installer --run-id "$RUN_ID" \
  --evidence-root "$EVIDENCE_ROOT" --provenance "$PROVENANCE" \
  --results "$RESULTS" --subtests "$SUBTESTS" --summary "$SUMMARY" \
  --git /usr/bin/git \
  --external-evidence framework-install-manifest="$FRAMEWORK_MANIFEST" \
  --output "$COMPONENT_ROOT/framework-installer.json"

doas test ! -e "$COMPONENT_ROOT/sample-pgo.json"
phase2_evidence_tool component-state --production \
  --repository-root "$SOURCE" --component sample-pgo --run-id "$RUN_ID" \
  --evidence-root "$EVIDENCE_ROOT" --provenance "$PROVENANCE" \
  --results "$RESULTS" --subtests "$SUBTESTS" --summary "$SUMMARY" \
  --git /usr/bin/git \
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
  --test-subtests "$EVIDENCE_ROOT/subtests.tsv" \
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
doas jq -e '.test_run.totals.fail == 0 and .test_run.totals.skip == 0 and
  .test_run.mandatory_internal_skip == 0 and
  .test_run.subtest_totals.required_fail == 0 and
  .test_run.subtest_totals.required_skip == 0' "$INDEX"
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

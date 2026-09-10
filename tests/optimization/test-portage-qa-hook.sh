#!/usr/bin/env bash
# The fixture deliberately supplies variables/functions that are consumed only
# by the dynamically selected QA hook below.
# SC2317/SC2329 cover callbacks invoked indirectly by traps and fixture hooks.
# shellcheck disable=SC1090,SC2034,SC2317,SC2329
set -Eeuo pipefail
IFS=$'\n\t'

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
HOOK=${ROOT}/portage/install-qa-check.d/zz-gentoo-optimization-bolt
ABI_GUARD=${ROOT}/scripts/optimization/verify/abi-guard.py
TMP=$(mktemp -d "${TMPDIR:-/tmp}/gentoo-opt-qa-hook.XXXXXX")
trap 'rm -rf -- "${TMP}"' EXIT HUP INT TERM
PASS=0
FAIL=0
PASSING_GUARD=${TMP}/passing-abi-guard.py
printf '#!/usr/bin/env python3\n' > "${PASSING_GUARD}"
printf 'raise SystemExit(0)\n' >> "${PASSING_GUARD}"
chmod +x "${PASSING_GUARD}"

run_case() {
    local name=$1
    shift
    if ("$@"); then
        printf 'PASS: %s\n' "${name}"
        PASS=$((PASS + 1))
    else
        printf 'FAIL: %s\n' "${name}" >&2
        FAIL=$((FAIL + 1))
    fi
}

new_marker() {
    local name=$1
    PORTAGE_TMPDIR=${TMP}/${name}
    PORTAGE_BUILDDIR=${PORTAGE_TMPDIR}/portage/app-test/fixture-1
    mkdir -p -- "${PORTAGE_BUILDDIR}"
    : > "${PORTAGE_BUILDDIR}/.installed"
    GENTOO_OPT_TEST_MODE=1
    GENTOO_OPT_ABI_GUARD=${PASSING_GUARD}
    export PORTAGE_TMPDIR PORTAGE_BUILDDIR GENTOO_OPT_TEST_MODE GENTOO_OPT_ABI_GUARD
}

case_off_is_noop() (
    new_marker off
    GENTOO_OPT_MODE=off
    source "${HOOK}"
    [[ -f ${PORTAGE_BUILDDIR}/.installed ]]
)

case_test_override_forbidden_in_ebuild_phase() (
    new_marker ebuild-override
    EBUILD_PHASE=install
    export EBUILD_PHASE
    die() { exit 97; }
    set +e
    ( source "${HOOK}" ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 97 && ! -e ${PORTAGE_BUILDDIR}/.installed ]]
)

case_lost_active_state_is_fatal() (
    new_marker lost
    GENTOO_OPT_MODE=bolt-capture
    unset GENTOO_OPT_ACTIVE_BOLT_STAGE
    die() { exit 91; }
    set +e
    ( source "${HOOK}" ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 91 && ! -e ${PORTAGE_BUILDDIR}/.installed ]]
)

case_mismatched_active_state_is_fatal() (
    new_marker mismatch
    GENTOO_OPT_MODE=clang-ir-use
    GENTOO_OPT_BOLT_STAGE=capture
    GENTOO_OPT_ACTIVE_BOLT_STAGE=deploy
    die() { exit 92; }
    set +e
    ( source "${HOOK}" ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 92 && ! -e ${PORTAGE_BUILDDIR}/.installed ]]
)

case_missing_transaction_function_is_fatal() (
    new_marker missing-function
    GENTOO_OPT_MODE=bolt-deploy
    GENTOO_OPT_ACTIVE_BOLT_STAGE=deploy
    unset -f gentoo_opt_post_src_install gentoo_opt_post_install_abort 2>/dev/null || :
    die() { exit 93; }
    set +e
    ( source "${HOOK}" ) >/dev/null 2>&1
    status=$?
    set -e
    [[ ${status} -eq 93 && ! -e ${PORTAGE_BUILDDIR}/.installed ]]
)

case_active_transaction_runs_exactly_once() (
    new_marker active
    GENTOO_OPT_MODE=rust-generate
    GENTOO_OPT_BOLT_STAGE=capture
    GENTOO_OPT_ACTIVE_BOLT_STAGE=capture
    TRANSACTION_LOG=${TMP}/transaction.log
    gentoo_opt_post_src_install() { printf 'ran\n' >> "${TRANSACTION_LOG}"; }
    gentoo_opt_post_install_abort() { return 97; }
    source "${HOOK}"
    [[ $(wc -l < "${TRANSACTION_LOG}") -eq 1 ]]
    [[ -f ${PORTAGE_BUILDDIR}/.installed ]]
)


case_abi_guard_tracks_cpp_and_unique_exports() (
    python3 - "${ABI_GUARD}" <<'PY_ABI_CPP'
from pathlib import Path
from unittest import mock
import os
import runpy
import sys

namespace = runpy.run_path(
    os.fspath(Path(sys.argv[1]))
)

sample = """\
  Type: DYN (Shared object file)\n\
 0x000000000000000e (SONAME)             Library soname: [libfixture.so.1]\n\
Symbol table '.dynsym' contains 5 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     1: 0000000000001000    16 FUNC    GLOBAL DEFAULT   12 _ZN7Example3fooEv
     2: 0000000000001010    16 FUNC    WEAK   DEFAULT   12 _ZN7Example3barEv
     3: 0000000000002000     8 OBJECT  UNIQUE DEFAULT   23 unique_public
     4: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND ignored_undefined
     5: 0000000000001020    16 FUNC    LOCAL  DEFAULT   12 ignored_local
"""

with mock.patch.object(
    namespace["subprocess"],
    "check_output",
    return_value=sample,
):
    observed_type, observed_soname, observed = namespace["inspect"](
        Path("/tmp/libfixture.so")
    )

assert observed_type == "DYN"
assert observed_soname == "libfixture.so.1"
assert observed == {
    "_ZN7Example3fooEv",
    "_ZN7Example3barEv",
    "unique_public",
}, observed
PY_ABI_CPP
)

case_abi_guard_rejects_small_complete_abi_replacement() (
    python3 - "${ABI_GUARD}" <<'PY_ABI_SMALL'
from pathlib import Path
from unittest import mock
import contextlib
import io
import os
import runpy
import sys
import tempfile

namespace = runpy.run_path(
    os.fspath(Path(sys.argv[1]))
)

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)

    ed = root / "ed"
    installed_root = root / "root"

    candidate = (
        ed
        / "usr/lib64/libtiny.so.1"
    )

    installed = (
        installed_root
        / "usr/lib64/libtiny.so.1"
    )

    candidate.parent.mkdir(
        parents=True
    )

    installed.parent.mkdir(
        parents=True
    )

    candidate.write_bytes(b"\x7fELFcandidate")
    installed.write_bytes(b"\x7fELFinstalled")

    old_exports = {
        "_ZN4Tiny3oneEv",
        "_ZN4Tiny3twoEv",
        "_ZN4Tiny5threeEv",
    }

    replacement_exports = {
        "_ZN4Tiny7foreignEv",
        "_ZN4Tiny8differentEv",
    }

    def fake_inspect(path: Path):
        if path == installed:
            return ("DYN", "libtiny.so.1", old_exports)

        if path == candidate:
            return ("DYN", "libtiny.so.1", replacement_exports)

        return ("", None, set())

    namespace["main"].__globals__["inspect"] = fake_inspect

    stderr = io.StringIO()

    with (
        mock.patch.dict(
            os.environ,
            {
                "ED": os.fspath(ed),
                "ROOT": os.fspath(installed_root),
            },
            clear=False,
        ),
        contextlib.redirect_stderr(stderr),
    ):
        result = namespace["main"]()

    assert result == 1, result
    assert (
        "gentoo-optimization ABI guard: exported ABI loss"
        in stderr.getvalue()
    )
PY_ABI_SMALL
)

case_abi_guard_failure_invalidates_install() (
    new_marker abi-guard-failure

    failing_guard=${TMP}/failing-abi-guard.py

    cat >"${failing_guard}" <<'PY_ABI_HOOK'
#!/usr/bin/env python3
import os
from pathlib import Path
Path(os.environ["ABI_GUARD_SENTINEL"]).write_text("ran\n")
raise SystemExit(1)
PY_ABI_HOOK
    chmod +x "${failing_guard}"
    ABI_GUARD_SENTINEL=${TMP}/failing-guard-ran
    export ABI_GUARD_SENTINEL

    GENTOO_OPT_ABI_GUARD=${failing_guard}
    GENTOO_OPT_TEST_MODE=1
    GENTOO_OPT_MODE=off

    die() {
        exit 98
    }

    set +e

    (
        source "${HOOK}"
    ) >/dev/null 2>&1

    status=$?

    set -e

    [[ ${status} -eq 98 ]]
    [[ -f ${TMP}/failing-guard-ran ]]
    [[ ! -e ${PORTAGE_BUILDDIR}/.installed ]]
)

[[ -f ${HOOK} ]] || {
    printf 'FAIL: hook is absent: %s\n' "${HOOK}" >&2
    exit 1
}
run_case 'off state is a strict no-op' case_off_is_noop
run_case 'test ABI override is forbidden in ebuild phase' case_test_override_forbidden_in_ebuild_phase
run_case 'lost active state invalidates the install' case_lost_active_state_is_fatal
run_case 'requested/active mismatch invalidates the install' case_mismatched_active_state_is_fatal
run_case 'missing transaction function invalidates the install' case_missing_transaction_function_is_fatal
run_case 'active transaction runs exactly once' case_active_transaction_runs_exactly_once
run_case 'ABI guard tracks C++ and GNU-unique exports' case_abi_guard_tracks_cpp_and_unique_exports
run_case 'ABI guard rejects complete replacement of a small ABI' case_abi_guard_rejects_small_complete_abi_replacement
run_case 'ABI guard failure invalidates the install' case_abi_guard_failure_invalidates_install
printf 'SUMMARY: pass=%d fail=%d total=%d\n' "${PASS}" "${FAIL}" "$((PASS + FAIL))"
((FAIL == 0))

#!/usr/bin/env python3
"""Hermetic tests for the production profile-lock transaction coordinator."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest


HERE = pathlib.Path(__file__).resolve().parent
TOOL = HERE / "production_profile_lock_transaction.py"
TOKEN_SCANNER = HERE / "authorization_token_scan.py"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
GENERATION = {
    "generation_id": "phase2-production-lock-test",
    "inventory_id": "phase2-production-lock-inventory",
    "inventory_sha256": hashlib.sha256(b"fixture-inventory").hexdigest(),
}
GATE_RUN_ID = "phase2-production-lock-test-run"


class Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="gentoo-production-profile-locks."
        )
        self.root = pathlib.Path(self.temporary.name)
        self.run = self.root / "run"
        self.state = self.root / "state"
        self.run.mkdir(mode=0o700)
        self.state.mkdir(mode=0o700)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir(mode=0o700)
        self.profile_artifacts = self.root / "profile-artifacts"
        self.profile_artifacts.mkdir(mode=0o700)
        self.evidence_output = self.root / "evidence-output"
        self.evidence_output.mkdir(mode=0o700)
        self.generation_state = self.root / "generation-state"
        self.generation_state.mkdir(mode=0o700)
        self.token_scanner = self.root / "authorization-token-scan.py"
        shutil.copyfile(TOKEN_SCANNER, self.token_scanner)
        self.token_scanner.chmod(0o700)
        self.framework = self.run / "framework-install.lock"
        self.project = self.run / "project.lock"
        self.generation = self.run / "generation.lock"
        self.journal = self.state / "production-profile-locks.pending"
        self.child_identity = pathlib.Path(f"{self.journal}.child.json")
        self.child_identity_partial = pathlib.Path(f"{self.child_identity}.partial")
        self.receipt = self.state / (
            "phase-2-production-profile-locks-"
            f"{GENERATION['generation_id']}.receipt.json"
        )
        self.receipt_partial = pathlib.Path(f"{self.receipt}.partial")
        self.receipt_abandoned = pathlib.Path(
            f"{self.receipt}.interrupted-partial"
        )
        self.authorization = (
            self.generation_state
            / GENERATION["generation_id"]
            / f"phase2-sample-gate-{GATE_RUN_ID}"
            / "transaction.authorization"
        )
        self.token_scan_output = (
            self.authorization.parent / "coordinator-token-scan.tsv"
        )
        for path in (self.framework, self.project, self.generation):
            path.touch(mode=0o600)
            path.chmod(0o600)
        self.identities = {
            path: self.identity(path)
            for path in (self.framework, self.project, self.generation)
        }

    def cleanup(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def identity(path: pathlib.Path) -> tuple[int, int, int, int, int, int, int]:
        metadata = path.stat()
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            metadata.st_gid,
            stat.S_IMODE(metadata.st_mode),
            metadata.st_nlink,
            metadata.st_size,
        )

    def path_arguments(self) -> list[str]:
        return [
            "--test-mode",
            "--test-root",
            os.fspath(self.root),
            "--test-framework-lock",
            os.fspath(self.framework),
            "--test-project-lock",
            os.fspath(self.project),
            "--test-generation-lock",
            os.fspath(self.generation),
            "--test-journal",
            os.fspath(self.journal),
            "--lock-timeout-seconds",
            "2",
        ]

    def run_arguments(
        self,
        command: list[str] | None = None,
        failpoint: str | None = None,
        child_timeout: float = 10,
        pre_arm_pause: pathlib.Path | None = None,
        token_scanner: pathlib.Path | None = None,
        test_pid_namespace: bool = False,
    ) -> list[str]:
        arguments = [
            sys.executable,
            os.fspath(TOOL),
            "run",
            *self.path_arguments(),
            "--generation-id",
            GENERATION["generation_id"],
            "--inventory-id",
            GENERATION["inventory_id"],
            "--inventory-sha256",
            GENERATION["inventory_sha256"],
            "--gate-run-id",
            GATE_RUN_ID,
            "--child-timeout-seconds",
            str(child_timeout),
            "--kill-after-seconds",
            "1",
            "--token-scanner",
            os.fspath(token_scanner or self.token_scanner),
            "--token-scan-root",
            os.fspath(self.artifacts),
            "--token-scan-root",
            os.fspath(self.profile_artifacts),
            "--token-scan-root",
            os.fspath(self.authorization.parent),
            "--token-scan-root",
            os.fspath(self.evidence_output),
            "--token-scan-output",
            os.fspath(self.token_scan_output),
            "--evidence-output-root",
            os.fspath(self.evidence_output),
        ]
        if failpoint is not None:
            arguments.extend(("--failpoint", failpoint))
        if pre_arm_pause is not None:
            arguments.extend(("--test-pre-arm-pause-file", os.fspath(pre_arm_pause)))
        if test_pid_namespace:
            arguments.append("--test-pid-namespace")
        arguments.append("--")
        arguments.extend(command or [sys.executable, "-c", "raise SystemExit(0)"])
        return arguments

    def recover_arguments(self, failpoint: str | None = None) -> list[str]:
        arguments = [
            sys.executable,
            os.fspath(TOOL),
            "recover",
            *self.path_arguments(),
        ]
        if failpoint is not None:
            arguments.extend(("--failpoint", failpoint))
        return arguments

    def assert_restored(self, test: unittest.TestCase) -> None:
        for path in (self.framework, self.project, self.generation):
            test.assertEqual(path.read_bytes(), b"", path)
            observed = self.identity(path)
            expected = self.identities[path]
            test.assertEqual(observed, expected, path)
            test.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), EMPTY_SHA256)
        test.assertFalse(self.journal.exists())
        test.assertFalse(pathlib.Path(f"{self.journal}.partial").exists())
        test.assertFalse(self.child_identity.exists())
        test.assertFalse(self.child_identity_partial.exists())
        receipts = list(
            self.state.glob("phase-2-production-profile-locks-*.receipt.json")
        )
        test.assertLessEqual(len(receipts), 1)
        for receipt in receipts:
            document = json.loads(receipt.read_text(encoding="utf-8"))
            test.assertEqual(
                receipt.read_bytes(),
                (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(),
            )
            test.assertEqual(
                document["schema"],
                "gentoo-optimization-production-profile-lock-receipt-v1",
            )
            test.assertEqual(document["lock_payload_restored_sha256"], EMPTY_SHA256)
            test.assertEqual(document["gate_run_id"], GATE_RUN_ID)
            test.assertEqual(
                document["transaction_journal"]["gate_run_id"], GATE_RUN_ID
            )
            test.assertTrue(document["journal_removal_after_receipt_required"])
            test.assertIn(
                document["status"],
                {"passed", "failed", "recovered-interrupted"},
            )
            if document["status"] == "recovered-interrupted":
                test.assertIsNone(document["child_exit_status"])
            else:
                test.assertEqual(
                    document["status"] == "passed",
                    document["child_exit_status"] == 0,
                )
                test.assertRegex(document["child_identity_sha256"], r"^[0-9a-f]{64}$")
                if document["status"] == "passed":
                    test.assertEqual(document["token_scan"]["scanner_status"], 0)
                test.assertEqual(
                    document["token_scan"]["output"],
                    os.fspath(self.token_scan_output),
                )
                test.assertEqual(
                    document["authorization"]["path"], os.fspath(self.authorization)
                )
            test.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
            abandoned = document["abandoned_receipt_partial"]
            if abandoned is not None:
                abandoned_path = pathlib.Path(abandoned["path"])
                test.assertTrue(abandoned_path.is_file())
                test.assertEqual(
                    hashlib.sha256(abandoned_path.read_bytes()).hexdigest(),
                    abandoned["sha256"],
                )
                abandoned_path.unlink()
            receipt.unlink()
        if self.token_scan_output.exists():
            self.token_scan_output.unlink()
        if self.authorization.exists():
            self.authorization.unlink()
        authorization_partial = self.authorization.with_name(
            f"{self.authorization.name}.partial"
        )
        if authorization_partial.exists():
            authorization_partial.unlink()
        authorization_abandoned = self.authorization.with_name(
            f"{self.authorization.name}.interrupted-partial"
        )
        if authorization_abandoned.exists():
            authorization_abandoned.unlink()
        for directory in (self.authorization.parent, self.authorization.parent.parent):
            if directory.exists():
                directory.rmdir()


class ProductionProfileLockTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def completed(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def assert_processes_gone(self, pids: list[int]) -> None:
        deadline = time.monotonic() + 3
        live = pids
        while time.monotonic() < deadline:
            live = []
            for pid in pids:
                try:
                    state = pathlib.Path(f"/proc/{pid}/stat").read_text().split()[2]
                except FileNotFoundError:
                    continue
                if state != "Z":
                    live.append(pid)
            if not live:
                break
            time.sleep(0.02)
        self.assertFalse(live, f"child process group survived: {live}")

    def test_success_and_child_failure_restore_exact_empty_inodes(self) -> None:
        marker = self.fixture.root / "child-marker"
        successful = self.completed(
            self.fixture.run_arguments(
                [
                    sys.executable,
                    "-c",
                    f"import pathlib; pathlib.Path({os.fspath(marker)!r}).write_text('ok')",
                ]
            )
        )
        self.assertEqual(successful.returncode, 0, successful.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "ok")
        self.fixture.assert_restored(self)

        failed = self.completed(
            self.fixture.run_arguments(
                [sys.executable, "-c", "raise SystemExit(23)"]
            )
        )
        self.assertEqual(failed.returncode, 23, failed.stderr)
        self.fixture.assert_restored(self)

    def test_child_receives_one_coordinator_generated_authorization_token(self) -> None:
        marker = self.fixture.root / "child-environment.json"
        child_code = "\n".join(
            (
                "import json, os, pathlib, re",
                "token = os.environ.get('GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN', '')",
                "authorization = os.environ.get('GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION')",
                "assert re.fullmatch(r'[0-9a-f]{64}', token)",
                f"assert authorization == {os.fspath(self.fixture.authorization)!r}",
                f"pathlib.Path({os.fspath(marker)!r}).write_text(json.dumps(dict(os.environ), sort_keys=True))",
            )
        )
        completed = self.completed(
            self.fixture.run_arguments([sys.executable, "-c", child_code])
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        child_environment = json.loads(marker.read_text(encoding="utf-8"))
        token = child_environment["GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN"]
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        self.assertEqual(
            child_environment,
            {
                "GENTOO_OPT_PRODUCTION_GATE_GENERATION_ID": GENERATION[
                    "generation_id"
                ],
                "GENTOO_OPT_PRODUCTION_GATE_INVENTORY_ID": GENERATION[
                    "inventory_id"
                ],
                "GENTOO_OPT_PRODUCTION_GATE_INVENTORY_SHA256": GENERATION[
                    "inventory_sha256"
                ],
                "GENTOO_OPT_PRODUCTION_GATE_RUN_ID": GATE_RUN_ID,
                "GENTOO_OPT_PRODUCTION_GATE_WORK_ROOT": os.fspath(
                    self.fixture.artifacts
                ),
                "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION": os.fspath(
                    self.fixture.authorization
                ),
                "GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN": token,
                "HOME": "/root",
                "LANG": "C",
                "LC_ALL": "C",
                "LOGNAME": "root",
                "PATH": (
                    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                ),
                "SHELL": "/bin/bash",
                "TZ": "UTC",
                "USER": "root",
            },
        )
        receipts = list(
            self.fixture.state.glob("phase-2-production-profile-locks-*.receipt.json")
        )
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(
            receipt["authorization_token_sha256"],
            hashlib.sha256(token.encode("ascii")).hexdigest(),
        )
        self.fixture.assert_restored(self)

    def test_verify_active_accepts_exact_live_state_and_rejects_bad_token(self) -> None:
        marker = self.fixture.root / "verify-active-results.json"
        verify_arguments = [
            sys.executable,
            os.fspath(TOOL),
            "verify-active",
            *self.fixture.path_arguments(),
            "--token-fd",
            "0",
            "--authorization",
            os.fspath(self.fixture.authorization),
        ]
        child_code = "\n".join(
            (
                "import json, os, pathlib, subprocess",
                f"arguments = {verify_arguments!r}",
                "token = os.environ['GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN']",
                "good = subprocess.run(arguments, input=(token + '\\n').encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)",
                "bad = subprocess.run(arguments, input=(('0' * 64) + '\\n').encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)",
                f"pathlib.Path({os.fspath(marker)!r}).write_text(json.dumps([good.returncode, good.stdout.decode(), bad.returncode, bad.stderr.decode()]))",
                "assert good.returncode == 0 and bad.returncode == 1",
            )
        )
        completed = self.completed(
            self.fixture.run_arguments([sys.executable, "-c", child_code])
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        good_status, good_output, bad_status, bad_error = json.loads(
            marker.read_text(encoding="utf-8")
        )
        self.assertEqual((good_status, good_output), (0, "VERIFIED\n"))
        self.assertEqual(bad_status, 1)
        self.assertIn("token differs", bad_error)
        self.fixture.assert_restored(self)

    def test_verify_active_rejects_alternate_authorization_path(self) -> None:
        marker = self.fixture.root / "verify-active-alternate-path-results.json"
        alternate = self.fixture.authorization.with_name(
            "alternate-transaction.authorization"
        )
        verify_arguments = [
            sys.executable,
            os.fspath(TOOL),
            "verify-active",
            *self.fixture.path_arguments(),
            "--token-fd",
            "0",
            "--authorization",
            os.fspath(alternate),
        ]
        child_code = "\n".join(
            (
                "import json, os, pathlib, subprocess",
                f"arguments = {verify_arguments!r}",
                f"authorization = pathlib.Path({os.fspath(self.fixture.authorization)!r})",
                f"alternate = pathlib.Path({os.fspath(alternate)!r})",
                "token = os.environ['GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN']",
                "alternate.write_bytes(authorization.read_bytes())",
                "alternate.chmod(0o600)",
                "try:",
                "    completed = subprocess.run(arguments, input=(token + '\\n').encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)",
                "finally:",
                "    alternate.unlink()",
                f"pathlib.Path({os.fspath(marker)!r}).write_text(json.dumps([completed.returncode, completed.stderr.decode()]))",
                "assert completed.returncode == 1",
                "assert 'journal-derived transaction path' in completed.stderr.decode()",
            )
        )
        completed = self.completed(
            self.fixture.run_arguments([sys.executable, "-c", child_code])
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        status, error = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(status, 1)
        self.assertIn("journal-derived transaction path", error)
        self.fixture.assert_restored(self)

    def test_verify_active_rejects_every_partial_transaction_object(self) -> None:
        marker = self.fixture.root / "verify-active-partial-results.json"
        verify_arguments = [
            sys.executable,
            os.fspath(TOOL),
            "verify-active",
            *self.fixture.path_arguments(),
            "--token-fd",
            "0",
            "--authorization",
            os.fspath(self.fixture.authorization),
        ]
        partial_paths = {
            "journal": pathlib.Path(f"{self.fixture.journal}.partial"),
            "child-identity": self.fixture.child_identity_partial,
            "authorization": self.fixture.authorization.with_name(
                f"{self.fixture.authorization.name}.partial"
            ),
        }
        child_code = "\n".join(
            (
                "import json, os, pathlib, subprocess",
                f"arguments = {verify_arguments!r}",
                f"partial_paths = {dict((label, os.fspath(path)) for label, path in partial_paths.items())!r}",
                "token = os.environ['GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN']",
                "results = []",
                "for partial_label, partial_value in partial_paths.items():",
                "    partial = pathlib.Path(partial_value)",
                "    for object_kind in ('regular', 'directory', 'symlink', 'fifo'):",
                "        if object_kind == 'regular':",
                "            partial.write_bytes(b'stale')",
                "        elif object_kind == 'directory':",
                "            partial.mkdir()",
                "        elif object_kind == 'symlink':",
                "            partial.symlink_to(partial.with_name(partial.name + '.missing'))",
                "        else:",
                "            os.mkfifo(partial, 0o600)",
                "        try:",
                "            completed = subprocess.run(arguments, input=(token + '\\n').encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)",
                "            results.append([partial_label, object_kind, completed.returncode, completed.stderr.decode()])",
                "        finally:",
                "            partial.rmdir() if object_kind == 'directory' else partial.unlink()",
                f"pathlib.Path({os.fspath(marker)!r}).write_text(json.dumps(results))",
                "assert len(results) == 12",
                "assert all(item[2] == 1 and 'partial transaction object' in item[3] for item in results)",
            )
        )
        completed = self.completed(
            self.fixture.run_arguments([sys.executable, "-c", child_code])
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        results = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(len(results), 12)
        self.assertEqual(
            {(result[0], result[1]) for result in results},
            {
                (partial_label, object_kind)
                for partial_label in partial_paths
                for object_kind in ("regular", "directory", "symlink", "fifo")
            },
        )
        for partial_label, object_kind, status, error in results:
            with self.subTest(partial=partial_label, object_kind=object_kind):
                self.assertEqual(status, 1)
                self.assertIn("partial transaction object", error)
        self.fixture.assert_restored(self)

    def test_raw_token_leak_is_detected_after_child_containment_ends(self) -> None:
        leak = self.fixture.artifacts / "raw-token-leak"
        child_code = "\n".join(
            (
                "import os, pathlib",
                "token = os.environ['GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_TOKEN']",
                f"pathlib.Path({os.fspath(leak)!r}).write_text(token)",
            )
        )
        completed = self.completed(
            self.fixture.run_arguments([sys.executable, "-c", child_code])
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = json.loads(self.fixture.receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["token_scan"]["scanner_status"], 1)
        leak.unlink()
        self.fixture.assert_restored(self)

    def test_scanner_no_output_publishes_failed_transaction_evidence(self) -> None:
        scanner = self.fixture.root / "no-output-scanner.py"
        scanner.write_text("#!/usr/bin/python3\nraise SystemExit(2)\n", encoding="utf-8")
        scanner.chmod(0o700)
        completed = self.completed(
            self.fixture.run_arguments(token_scanner=scanner)
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        receipt = json.loads(self.fixture.receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["token_scan"]["scanner_status"], 2)
        self.assertIn(
            b"scanner-no-output", self.fixture.token_scan_output.read_bytes()
        )
        self.fixture.assert_restored(self)

    def test_scanner_substitution_after_arming_fails_with_indexed_output(self) -> None:
        scanner = self.fixture.root / "mutable-scanner.py"
        shutil.copyfile(self.fixture.token_scanner, scanner)
        scanner.chmod(0o700)
        child_code = (
            f"import pathlib; pathlib.Path({os.fspath(scanner)!r}).unlink()"
        )
        completed = self.completed(
            self.fixture.run_arguments(
                [sys.executable, "-c", child_code], token_scanner=scanner
            )
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = json.loads(self.fixture.receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["token_scan"]["scanner_status"], 125)
        self.fixture.assert_restored(self)

    def test_partial_arm_after_project_write_recovers(self) -> None:
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="arm-after-project")
        )
        self.assertEqual(crashed.returncode, 90, crashed.stderr)
        self.assertTrue(self.fixture.journal.is_file())
        self.assertNotEqual(self.fixture.project.read_bytes(), b"")
        self.assertEqual(self.fixture.generation.read_bytes(), b"")
        journal_bytes = self.fixture.journal.read_bytes()
        document = json.loads(journal_bytes)
        self.assertEqual(
            set(document["framework_context"]),
            {
                "framework_aggregate_sha256",
                "git_commit",
                "manifest_path",
                "manifest_sha256",
                "source_aggregate_sha256",
                "target",
            },
        )
        self.assertEqual(
            journal_bytes,
            (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(),
        )
        self.assertEqual(stat.S_IMODE(self.fixture.journal.stat().st_mode), 0o600)
        expected_payload = (
            json.dumps(GENERATION, indent=2, sort_keys=True) + "\n"
        ).encode()
        self.assertEqual(self.fixture.project.read_bytes(), expected_payload)

        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("RECOVERED", recovered.stdout)
        self.fixture.assert_restored(self)

    def test_partial_in_place_arm_writes_are_recoverable(self) -> None:
        expected_payload = (
            json.dumps(GENERATION, indent=2, sort_keys=True) + "\n"
        ).encode()
        for failpoint, expected_status, partial_path in (
            ("arm-during-project", 100, self.fixture.project),
            ("arm-during-generation", 101, self.fixture.generation),
        ):
            with self.subTest(failpoint=failpoint):
                crashed = self.completed(
                    self.fixture.run_arguments(failpoint=failpoint)
                )
                self.assertEqual(crashed.returncode, expected_status, crashed.stderr)
                partial_payload = partial_path.read_bytes()
                self.assertNotEqual(partial_payload, b"")
                self.assertNotEqual(partial_payload, expected_payload)
                self.assertTrue(expected_payload.startswith(partial_payload))
                recovered = self.completed(self.fixture.recover_arguments())
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.fixture.assert_restored(self)

    def test_authorization_partial_is_promoted_by_explicit_recovery(self) -> None:
        self.assertFalse(self.fixture.authorization.parent.parent.exists())
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="authorization-after-partial-fsync")
        )
        self.assertEqual(crashed.returncode, 102, crashed.stderr)
        partial = self.fixture.authorization.with_name(
            f"{self.fixture.authorization.name}.partial"
        )
        self.assertTrue(partial.is_file())
        self.assertFalse(self.fixture.authorization.exists())
        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertTrue(self.fixture.authorization.is_file())
        self.assertFalse(partial.exists())
        receipt = json.loads(self.fixture.receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "recovered-interrupted")
        self.assertEqual(
            receipt["authorization"]["path"], os.fspath(self.fixture.authorization)
        )
        self.fixture.assert_restored(self)

    def test_malformed_authorization_partial_is_preserved_and_indexed(self) -> None:
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="child-after-sidecar")
        )
        self.assertEqual(crashed.returncode, 94, crashed.stderr)
        self.fixture.authorization.parent.mkdir(parents=True, mode=0o700)
        self.fixture.authorization.parent.parent.chmod(0o700)
        self.fixture.authorization.parent.chmod(0o700)
        partial = self.fixture.authorization.with_name(
            f"{self.fixture.authorization.name}.partial"
        )
        malformed = b"malformed authorization partial\n"
        partial.write_bytes(malformed)
        partial.chmod(0o600)
        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        receipt = json.loads(self.fixture.receipt.read_text(encoding="utf-8"))
        abandoned = receipt["authorization"]["abandoned_partial"]
        self.assertEqual(abandoned["sha256"], hashlib.sha256(malformed).hexdigest())
        abandoned_path = pathlib.Path(abandoned["path"])
        self.assertEqual(abandoned_path.read_bytes(), malformed)
        self.fixture.assert_restored(self)

    def test_boolean_lock_identity_is_rejected_as_non_integer(self) -> None:
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="arm-after-generation")
        )
        self.assertEqual(crashed.returncode, 91, crashed.stderr)
        original = self.fixture.journal.read_bytes()
        document = json.loads(original)
        document["locks"]["project"]["uid"] = False
        self.fixture.journal.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        rejected = self.completed(self.fixture.recover_arguments())
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("fields must be integers", rejected.stderr)
        self.fixture.journal.write_bytes(original)
        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.fixture.assert_restored(self)

    def test_complete_arm_before_child_recovers(self) -> None:
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="arm-after-generation")
        )
        self.assertEqual(crashed.returncode, 91, crashed.stderr)
        self.assertEqual(
            self.fixture.project.read_bytes(), self.fixture.generation.read_bytes()
        )
        self.assertNotEqual(self.fixture.project.read_bytes(), b"")

        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.fixture.assert_restored(self)

    def test_interrupted_restore_recovers_mixed_empty_and_expected(self) -> None:
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="restore-after-project")
        )
        self.assertEqual(crashed.returncode, 92, crashed.stderr)
        self.assertEqual(self.fixture.project.read_bytes(), b"")
        self.assertNotEqual(self.fixture.generation.read_bytes(), b"")
        self.assertTrue(self.fixture.journal.is_file())

        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.fixture.assert_restored(self)

    def test_unknown_initial_payload_is_rejected_without_journal(self) -> None:
        self.fixture.project.write_bytes(b"unknown\n")
        completed = self.completed(self.fixture.run_arguments())
        self.assertEqual(completed.returncode, 1)
        self.assertIn("nonempty without a recovery journal", completed.stderr)
        self.assertEqual(self.fixture.project.read_bytes(), b"unknown\n")
        self.assertEqual(self.fixture.generation.read_bytes(), b"")
        self.assertFalse(self.fixture.journal.exists())

    def test_inherited_optimization_environment_is_rejected_before_arming(self) -> None:
        marker = self.fixture.root / "forbidden-environment-reached-child"
        child = [
            sys.executable,
            "-c",
            f"import pathlib; pathlib.Path({os.fspath(marker)!r}).touch()",
        ]
        forbidden = {
            "GENTOO_OPT_EXPECTED_PACKAGE_FINGERPRINT": "malicious-fingerprint",
            "GENTOO_OPT_PRODUCTION_GATE_RUN_ID": "mismatching-inherited-run",
            "GENTOO_OPT_TEST_PROFILE_VALIDATOR": "/malicious/validator",
            "GENTOO_OPT_TEST_SAMPLE_FIXTURE_ROOT": "/malicious/fixture",
            "KEEP_TEMP": "1",
            "PORTAGE_SAMPLE_PGO_ITERATIONS": "1",
        }
        clean_environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GENTOO_OPT_")
            and key not in {"KEEP_TEMP", "PORTAGE_SAMPLE_PGO_ITERATIONS"}
        }
        completed = subprocess.run(
            self.fixture.run_arguments(child),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **clean_environment,
                "PYTHONDONTWRITEBYTECODE": "1",
                **forbidden,
            },
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("inherited forbidden environment variables", completed.stderr)
        for name in forbidden:
            self.assertIn(name, completed.stderr)
        self.assertFalse(marker.exists())
        self.fixture.assert_restored(self)

    def test_gate_run_id_must_be_a_safe_exact_identity(self) -> None:
        arguments = self.fixture.run_arguments()
        position = arguments.index("--gate-run-id") + 1
        arguments[position] = "../unsafe-run"
        completed = self.completed(arguments)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("production gate run ID is not a safe exact identity", completed.stderr)
        self.fixture.assert_restored(self)

    def test_unknown_recovery_payload_and_inode_replacement_fail_closed(self) -> None:
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="arm-after-generation")
        )
        self.assertEqual(crashed.returncode, 91, crashed.stderr)
        self.fixture.generation.write_bytes(b"unreviewed\n")
        unknown = self.completed(self.fixture.recover_arguments())
        self.assertEqual(unknown.returncode, 1)
        self.assertIn("neither empty nor the journal payload", unknown.stderr)
        self.assertTrue(self.fixture.journal.exists())
        self.assertEqual(self.fixture.generation.read_bytes(), b"unreviewed\n")

        # Return to the journal payload, then replace one stable inode.  The
        # recovery code must not truncate the replacement.
        document = json.loads(self.fixture.journal.read_text(encoding="utf-8"))
        payload = (
            json.dumps(document["generation"], indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        self.fixture.generation.write_bytes(payload)
        self.fixture.project.unlink()
        self.fixture.project.write_bytes(payload)
        self.fixture.project.chmod(0o600)
        replaced = self.completed(self.fixture.recover_arguments())
        self.assertEqual(replaced.returncode, 1)
        self.assertIn("same-boot project lock inode or metadata changed", replaced.stderr)
        self.assertTrue(self.fixture.journal.exists())
        self.assertEqual(self.fixture.project.read_bytes(), payload)

    def test_stale_partial_is_removed_only_with_empty_locks(self) -> None:
        partial = pathlib.Path(f"{self.fixture.journal}.partial")
        partial.write_text("incomplete", encoding="utf-8")
        partial.chmod(0o600)
        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("RECOVERED", recovered.stdout)
        self.fixture.assert_restored(self)

        partial.write_text("incomplete", encoding="utf-8")
        partial.chmod(0o600)
        self.fixture.project.write_bytes(b"unknown")
        rejected = self.completed(self.fixture.recover_arguments())
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("stale journal partial accompanies nonempty", rejected.stderr)
        self.assertTrue(partial.exists())

    def test_preexisting_receipt_state_is_never_deleted_or_overwritten(self) -> None:
        receipt = self.fixture.state / (
            "phase-2-production-profile-locks-"
            f"{GENERATION['generation_id']}.receipt.json"
        )
        partial = pathlib.Path(f"{receipt}.partial")
        partial.write_text("stale partial\n", encoding="utf-8")
        partial.chmod(0o600)
        completed = self.completed(self.fixture.run_arguments())
        self.assertEqual(completed.returncode, 1)
        self.assertIn("already has transaction receipt state", completed.stderr)
        self.assertEqual(partial.read_text(encoding="utf-8"), "stale partial\n")
        partial.unlink()
        self.fixture.assert_restored(self)

        receipt.write_text("existing authoritative receipt\n", encoding="utf-8")
        receipt.chmod(0o600)
        rejected = self.completed(self.fixture.run_arguments())
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("already has transaction receipt state", rejected.stderr)
        self.assertEqual(receipt.read_text(encoding="utf-8"), "existing authoritative receipt\n")
        receipt.unlink()
        self.fixture.assert_restored(self)

    def test_live_owner_blocks_a_second_recovery(self) -> None:
        # A pause child keeps the coordinator and journal live after arming.
        child = subprocess.Popen(
            self.fixture.run_arguments(
                [sys.executable, "-c", "import time; time.sleep(30)"]
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        try:
            deadline = time.monotonic() + 5
            while not self.fixture.journal.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(self.fixture.journal.exists())
            second = self.completed(self.fixture.recover_arguments())
            self.assertEqual(second.returncode, 1)
            self.assertIn("owner is still active", second.stderr)
        finally:
            child.send_signal(signal.SIGTERM)
            stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 143, f"{stdout}\n{stderr}")
        self.fixture.assert_restored(self)

    def test_framework_shared_lock_is_held_and_generation_writers_are_released(self) -> None:
        marker = self.fixture.root / "lock-observations"
        child_code = "\n".join(
            (
                "import fcntl, json, pathlib",
                f"framework = open({os.fspath(self.fixture.framework)!r}, 'rb')",
                "try:",
                "    fcntl.flock(framework, fcntl.LOCK_EX | fcntl.LOCK_NB)",
                "except BlockingIOError:",
                "    framework_blocked = True",
                "else:",
                "    framework_blocked = False",
                f"project = open({os.fspath(self.fixture.project)!r}, 'rb')",
                f"generation = open({os.fspath(self.fixture.generation)!r}, 'rb')",
                "fcntl.flock(project, fcntl.LOCK_EX | fcntl.LOCK_NB)",
                "fcntl.flock(generation, fcntl.LOCK_EX | fcntl.LOCK_NB)",
                "same_payload = project.read() == generation.read() != b''",
                f"pathlib.Path({os.fspath(marker)!r}).write_text(json.dumps([framework_blocked, same_payload]))",
            )
        )
        completed = self.completed(
            self.fixture.run_arguments([sys.executable, "-c", child_code])
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8")), [True, True])
        self.fixture.assert_restored(self)

    def test_child_timeout_terminates_process_group_and_restores(self) -> None:
        completed = self.completed(
            self.fixture.run_arguments(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                child_timeout=0.1,
            )
        )
        self.assertEqual(completed.returncode, 124, completed.stderr)
        self.fixture.assert_restored(self)

    def test_same_boot_framework_inode_replacement_is_rejected(self) -> None:
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="arm-after-generation")
        )
        self.assertEqual(crashed.returncode, 91, crashed.stderr)
        self.fixture.framework.unlink()
        self.fixture.framework.touch(mode=0o600)
        self.fixture.framework.chmod(0o600)
        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 1)
        self.assertIn(
            "same-boot framework lock inode or metadata changed", recovered.stderr
        )
        self.assertTrue(self.fixture.journal.exists())
        self.assertNotEqual(self.fixture.project.read_bytes(), b"")
        self.assertNotEqual(self.fixture.generation.read_bytes(), b"")

    def test_test_root_must_be_owner_private_and_not_a_symlink(self) -> None:
        self.fixture.root.chmod(0o755)
        exposed = self.completed(self.fixture.recover_arguments())
        self.assertEqual(exposed.returncode, 1)
        self.assertIn("test root must be an owner-private", exposed.stderr)
        self.fixture.root.chmod(0o700)

        link = self.fixture.root.parent / f"{self.fixture.root.name}.link"
        link.symlink_to(self.fixture.root, target_is_directory=True)
        arguments = self.fixture.recover_arguments()
        replacements = {
            os.fspath(self.fixture.root): os.fspath(link),
            os.fspath(self.fixture.framework): os.fspath(link / "run" / self.fixture.framework.name),
            os.fspath(self.fixture.project): os.fspath(link / "run" / self.fixture.project.name),
            os.fspath(self.fixture.generation): os.fspath(
                link / "run" / self.fixture.generation.name
            ),
            os.fspath(self.fixture.journal): os.fspath(
                link / "state" / self.fixture.journal.name
            ),
        }
        arguments = [replacements.get(argument, argument) for argument in arguments]
        try:
            linked = self.completed(arguments)
        finally:
            link.unlink()
        self.assertEqual(linked.returncode, 1)
        self.assertIn("test root must be an owner-private real directory", linked.stderr)
        self.fixture.assert_restored(self)

    def test_signal_terminates_complete_child_process_group_and_restores(self) -> None:
        pid_file = self.fixture.root / "child-pids"
        child_code = "\n".join(
            (
                "import os, pathlib, subprocess, time",
                "grandchild = subprocess.Popen([%r, '-c', 'import time; time.sleep(60)'])"
                % sys.executable,
                "pathlib.Path(%r).write_text(f'{os.getpid()} {grandchild.pid}')"
                % os.fspath(pid_file),
                "time.sleep(60)",
            )
        )
        coordinator = subprocess.Popen(
            self.fixture.run_arguments([sys.executable, "-c", child_code]),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(pid_file.exists())
        child_pid, grandchild_pid = map(int, pid_file.read_text().split())
        coordinator.send_signal(signal.SIGTERM)
        stdout, stderr = coordinator.communicate(timeout=5)
        self.assertEqual(coordinator.returncode, 143, f"{stdout}\n{stderr}")
        self.fixture.assert_restored(self)
        self.assert_processes_gone([child_pid, grandchild_pid])

    @unittest.skipUnless(
        os.geteuid() == 0 and pathlib.Path("/usr/bin/unshare").is_file(),
        "requires root and util-linux unshare",
    )
    def test_pid_namespace_kills_escaped_setsid_descendant_before_scan(self) -> None:
        escaped_marker = self.fixture.artifacts / "escaped-descendant-ran"
        grandchild_code = (
            "import pathlib,time; time.sleep(1.5); "
            f"pathlib.Path({os.fspath(escaped_marker)!r}).write_text('escaped')"
        )
        child_code = "\n".join(
            (
                "import subprocess, sys",
                f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}], start_new_session=True)",
                "raise SystemExit(0)",
            )
        )
        completed = self.completed(
            self.fixture.run_arguments(
                [sys.executable, "-c", child_code], test_pid_namespace=True
            )
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        time.sleep(2)
        self.assertFalse(escaped_marker.exists())
        receipt = json.loads(self.fixture.receipt.read_text(encoding="utf-8"))
        self.assertEqual(
            receipt["transaction_journal"]["child_contract"]["containment"],
            "pid-namespace-v1",
        )
        self.fixture.assert_restored(self)

    def test_sigkill_coordinator_recovery_terminates_recorded_process_group(self) -> None:
        pid_file = self.fixture.root / "orphan-child-pids"
        child_code = "\n".join(
            (
                "import os, pathlib, subprocess, time",
                "grandchild = subprocess.Popen([%r, '-c', 'import time; time.sleep(60)'])"
                % sys.executable,
                "pathlib.Path(%r).write_text(f'{os.getpid()} {grandchild.pid}')"
                % os.fspath(pid_file),
                "time.sleep(60)",
            )
        )
        coordinator = subprocess.Popen(
            self.fixture.run_arguments([sys.executable, "-c", child_code]),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        deadline = time.monotonic() + 5
        while (
            not pid_file.exists() or not self.fixture.child_identity.exists()
        ) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(pid_file.exists())
        self.assertTrue(self.fixture.child_identity.is_file())
        child_pid, grandchild_pid = map(int, pid_file.read_text().split())
        try:
            coordinator.kill()
            coordinator.wait(timeout=5)
            self.assertEqual(coordinator.returncode, -signal.SIGKILL)

            recovered = self.completed(self.fixture.recover_arguments())
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIn("RECOVERED", recovered.stdout)
            self.assert_processes_gone([child_pid, grandchild_pid])
            receipt = json.loads(self.fixture.receipt.read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "recovered-interrupted")
            self.assertIsNone(receipt["child_exit_status"])
            self.fixture.assert_restored(self)
        finally:
            with self.assertRaises((ProcessLookupError, PermissionError)):
                os.killpg(child_pid, signal.SIGKILL)
            if coordinator.stdout is not None:
                coordinator.stdout.close()
            if coordinator.stderr is not None:
                coordinator.stderr.close()

    def test_child_barrier_crash_windows_never_run_unrecorded_workload(self) -> None:
        for failpoint, expected_status, expects_sidecar, expects_partial in (
            ("child-after-spawn", 93, False, False),
            ("child-sidecar-after-partial-fsync", 99, False, True),
            ("child-after-sidecar", 94, True, False),
        ):
            with self.subTest(failpoint=failpoint):
                marker = self.fixture.root / f"workload-{failpoint}"
                command = [
                    sys.executable,
                    "-c",
                    f"import pathlib; pathlib.Path({os.fspath(marker)!r}).write_text('ran')",
                ]
                crashed = self.completed(
                    self.fixture.run_arguments(command, failpoint=failpoint)
                )
                self.assertEqual(crashed.returncode, expected_status, crashed.stderr)
                self.assertFalse(marker.exists())
                self.assertEqual(self.fixture.child_identity.exists(), expects_sidecar)
                self.assertEqual(
                    self.fixture.child_identity_partial.exists(), expects_partial
                )
                recovered = self.completed(self.fixture.recover_arguments())
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertFalse(marker.exists())
                self.fixture.assert_restored(self)

    def test_child_sidecar_is_bound_to_exact_journal_bytes(self) -> None:
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="child-after-sidecar")
        )
        self.assertEqual(crashed.returncode, 94, crashed.stderr)
        original = self.fixture.child_identity.read_bytes()
        document = json.loads(original)
        document["journal_sha256"] = "0" * 64
        self.fixture.child_identity.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        rejected = self.completed(self.fixture.recover_arguments())
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("not exactly bound", rejected.stderr)
        self.assertTrue(self.fixture.journal.exists())
        self.fixture.child_identity.write_bytes(original)
        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.fixture.assert_restored(self)

    def test_authorization_gate_symlink_is_rejected_before_child_release(self) -> None:
        generation_parent = self.fixture.authorization.parent.parent
        generation_parent.mkdir(mode=0o700)
        self.fixture.authorization.parent.symlink_to(
            self.fixture.artifacts, target_is_directory=True
        )
        marker = self.fixture.artifacts / "symlink-child-ran"
        rejected = self.completed(
            self.fixture.run_arguments(
                [
                    sys.executable,
                    "-c",
                    f"import pathlib; pathlib.Path({os.fspath(marker)!r}).touch()",
                ]
            )
        )
        self.assertEqual(rejected.returncode, 1)
        self.assertFalse(marker.exists())
        self.assertTrue(self.fixture.journal.is_file())
        self.fixture.authorization.parent.unlink()
        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.fixture.assert_restored(self)

    def test_authorization_inode_substitution_after_release_fails_closed(self) -> None:
        child_code = "\n".join(
            (
                "import os, pathlib",
                "path = pathlib.Path(os.environ['GENTOO_OPT_PRODUCTION_PROFILE_TRANSACTION_AUTHORIZATION'])",
                "path.unlink()",
                "path.write_text('substituted\\n')",
                "path.chmod(0o600)",
            )
        )
        rejected = self.completed(
            self.fixture.run_arguments([sys.executable, "-c", child_code])
        )
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("production authorization", rejected.stderr)
        self.assertTrue(self.fixture.journal.is_file())

    def test_receipt_crash_windows_reconcile_before_journal_removal(self) -> None:
        for failpoint, expected_status in (
            ("receipt-after-partial-fsync", 96),
            ("receipt-after-final-rename", 97),
            ("receipt-after-journal-removal", 98),
        ):
            with self.subTest(failpoint=failpoint):
                crashed = self.completed(
                    self.fixture.run_arguments(failpoint=failpoint)
                )
                self.assertEqual(crashed.returncode, expected_status, crashed.stderr)
                if failpoint != "receipt-after-journal-removal":
                    self.assertTrue(self.fixture.journal.exists())
                self.assertTrue(
                    self.fixture.receipt.exists()
                    or self.fixture.receipt_partial.exists()
                )
                recovered = self.completed(self.fixture.recover_arguments())
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                receipt = json.loads(
                    self.fixture.receipt.read_text(encoding="utf-8")
                )
                self.assertEqual(receipt["status"], "passed")
                self.assertEqual(receipt["child_exit_status"], 0)
                self.fixture.assert_restored(self)

    def test_malformed_interrupted_receipt_partial_is_preserved_and_indexed(self) -> None:
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="arm-after-generation")
        )
        self.assertEqual(crashed.returncode, 91, crashed.stderr)
        self.fixture.receipt_partial.write_text("incomplete receipt\n", encoding="utf-8")
        self.fixture.receipt_partial.chmod(0o600)
        payload = self.fixture.receipt_partial.read_bytes()
        recovered = self.completed(self.fixture.recover_arguments())
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        receipt = json.loads(self.fixture.receipt.read_text(encoding="utf-8"))
        evidence = receipt["abandoned_receipt_partial"]
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(self.fixture.receipt_abandoned.read_bytes(), payload)
        self.fixture.assert_restored(self)

    def test_crashed_receipt_is_reconciled_before_any_second_child_runs(self) -> None:
        crashed = self.completed(
            self.fixture.run_arguments(failpoint="receipt-after-partial-fsync")
        )
        self.assertEqual(crashed.returncode, 96, crashed.stderr)
        marker = self.fixture.root / "duplicate-child"
        second = self.completed(
            self.fixture.run_arguments(
                [
                    sys.executable,
                    "-c",
                    f"import pathlib; pathlib.Path({os.fspath(marker)!r}).write_text('ran')",
                ]
            )
        )
        self.assertEqual(second.returncode, 1)
        self.assertIn("already has transaction receipt state", second.stderr)
        self.assertFalse(marker.exists())
        self.assertTrue(self.fixture.receipt.exists())
        self.fixture.assert_restored(self)

    def test_receipt_preflight_is_repeated_atomically_before_arming(self) -> None:
        pause = self.fixture.root / "pre-arm.pause"
        duplicate_marker = self.fixture.root / "delayed-duplicate-child"
        delayed = subprocess.Popen(
            self.fixture.run_arguments(
                [
                    sys.executable,
                    "-c",
                    f"import pathlib; pathlib.Path({os.fspath(duplicate_marker)!r}).touch()",
                ],
                pre_arm_pause=pause,
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        try:
            deadline = time.monotonic() + 5
            while not pause.exists() and time.monotonic() < deadline:
                if delayed.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertTrue(pause.is_file(), "delayed coordinator missed pre-arm pause")

            winner = self.completed(self.fixture.run_arguments())
            self.assertEqual(winner.returncode, 0, winner.stderr)
            self.assertTrue(self.fixture.receipt.is_file())
            pause.unlink()
            stdout, stderr = delayed.communicate(timeout=5)
            self.assertEqual(delayed.returncode, 1, f"{stdout}\n{stderr}")
            self.assertIn("already has transaction receipt state", stderr)
            self.assertFalse(duplicate_marker.exists())
            self.fixture.assert_restored(self)
        finally:
            if pause.exists():
                pause.unlink()
            if delayed.poll() is None:
                delayed.kill()
                delayed.wait(timeout=5)
            if delayed.stdout is not None:
                delayed.stdout.close()
            if delayed.stderr is not None:
                delayed.stderr.close()

    def test_second_coordinator_cannot_cross_live_or_final_receipt_boundary(self) -> None:
        marker = self.fixture.root / "second-child-marker"
        first = subprocess.Popen(
            self.fixture.run_arguments(
                [sys.executable, "-c", "import time; time.sleep(30)"]
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        try:
            deadline = time.monotonic() + 5
            while not self.fixture.child_identity.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(self.fixture.child_identity.exists())
            second = self.completed(
                self.fixture.run_arguments(
                    [
                        sys.executable,
                        "-c",
                        f"import pathlib; pathlib.Path({os.fspath(marker)!r}).write_text('ran')",
                    ]
                )
            )
            self.assertEqual(second.returncode, 1)
            self.assertIn("owner is still active", second.stderr)
            self.assertFalse(marker.exists())
        finally:
            first.send_signal(signal.SIGTERM)
            stdout, stderr = first.communicate(timeout=5)
        self.assertEqual(first.returncode, 143, f"{stdout}\n{stderr}")
        self.assertTrue(self.fixture.receipt.exists())
        third = self.completed(self.fixture.run_arguments())
        self.assertEqual(third.returncode, 1)
        self.assertIn("already has transaction receipt state", third.stderr)
        self.fixture.assert_restored(self)

    def test_test_path_overrides_require_explicit_guard(self) -> None:
        arguments = [
            sys.executable,
            os.fspath(TOOL),
            "recover",
            "--test-root",
            os.fspath(self.fixture.root),
            "--test-framework-lock",
            os.fspath(self.fixture.framework),
            "--test-project-lock",
            os.fspath(self.fixture.project),
            "--test-generation-lock",
            os.fspath(self.fixture.generation),
            "--test-journal",
            os.fspath(self.fixture.journal),
        ]
        completed = self.completed(arguments)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("require explicit --test-mode", completed.stderr)
        self.fixture.assert_restored(self)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Subprocess integration proof for prerequisite terminal-reap signals."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "optimization"
    / "recovery"
    / "install-jsonschema-prerequisite.py"
)
SPEC = importlib.util.spec_from_file_location(
    "gentoo_jsonschema_prerequisite_signal_integration", TOOL_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load jsonschema prerequisite transaction: {TOOL_PATH}")
TOOL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TOOL
SPEC.loader.exec_module(TOOL)


HELPER_MODE = "--managed-signal-helper"
CHILD_MODE = "--managed-signal-child"


def wait_for_file(path: Path, process: subprocess.Popen[bytes], timeout: float) -> Any:
    """Read one immutable helper record without permitting a silent stall."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return TOOL.read_json_regular(path, path.name)
        status = process.poll()
        if status is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"managed-signal helper exited early with {status}: "
                f"stdout={stdout!r}, stderr={stderr!r}"
            )
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for managed-signal record: {path}")


def managed_signal_child(
    ready_fd: int,
    parent_pid: int,
    parent_start_ticks: int,
    signum: int,
    grant_path: Path,
) -> int:
    """Bind parent death, then deliver one granted signal from the direct child."""

    TOOL.install_parent_death_signal(parent_pid, parent_start_ticks)
    os.write(ready_fd, b"R")
    os.close(ready_fd)
    deadline = time.monotonic() + 20.0
    while not grant_path.is_file():
        if time.monotonic() >= deadline:
            return 70
        time.sleep(0.01)
    if os.getppid() != parent_pid:
        return 71
    time.sleep(0.1)
    os.kill(parent_pid, signum)
    while True:
        signal.pause()


def managed_signal_helper(root: Path, signum: int) -> int:
    """Run one real delivery inside the durable-terminal reap boundary."""

    if signum not in TOOL.TRANSACTION_SIGNALS:
        raise AssertionError(f"unmanaged helper signal: {signum}")
    root = root.resolve(strict=True)
    transaction_id = f"managed-signal-{signum}"
    proc_boot = root / "proc" / "sys" / "kernel" / "random"
    proc_boot.mkdir(parents=True)
    (proc_boot / "boot_id").write_text(
        Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii"),
        encoding="ascii",
    )
    paths = TOOL.Paths(transaction_id, root, True)
    paths.state_parent.mkdir(parents=True)
    ready_path = root / "signal-ready.json"
    proof_path = root / "signal-proof.json"
    grant_path = root / "signal.grant"
    coordinator = TOOL.process_identity(os.getpid())
    if coordinator is None:
        raise AssertionError("cannot observe managed-signal coordinator")
    pthread_sigmask = getattr(signal, "pthread_sigmask", None)
    if not callable(pthread_sigmask):
        raise AssertionError("pthread_sigmask is unavailable")
    original_handlers = {
        managed: signal.getsignal(managed) for managed in TOOL.TRANSACTION_SIGNALS
    }
    original_mask = set(pthread_sigmask(signal.SIG_BLOCK, set()))
    read_fd, write_fd = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    child_identity: dict[str, int | str] | None = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                os.fspath(Path(__file__).resolve()),
                CHILD_MODE,
                str(write_fd),
                str(coordinator["pid"]),
                str(coordinator["start_ticks"]),
                str(signum),
                os.fspath(grant_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=TOOL.clean_environment(),
            close_fds=True,
            pass_fds=(write_fd,),
            start_new_session=True,
        )
        os.close(write_fd)
        write_fd = -1
        readable, _writable, _exceptional = select.select([read_fd], [], [], 5.0)
        if not readable or os.read(read_fd, 1) != b"R":
            child_stdout, child_stderr = process.communicate(timeout=5.0)
            raise AssertionError(
                "direct child did not establish parent-death readiness: "
                f"status={process.returncode}, stdout={child_stdout!r}, "
                f"stderr={child_stderr!r}"
            )
        os.close(read_fd)
        read_fd = -1
        child_identity = TOOL.process_identity(process.pid)
        if child_identity is None or any(
            child_identity[key] != expected
            for key, expected in (
                ("ppid", coordinator["pid"]),
                ("process_group", process.pid),
                ("session", process.pid),
            )
        ):
            raise AssertionError(
                f"direct child lacks its exact private identity: {child_identity}"
            )
        prepared = TOOL.base_state(
            paths,
            authority={"fixture": "managed-signal-integration"},
            resolver={"vdb_before": {"cpvs": []}},
            plan={"ordered_exact_atoms": [], "rows": []},
            private_roots={"fixture": "managed-signal-integration"},
        )
        _prepared_path, prepared_sha256 = TOOL.publish_state(paths, prepared)
        armed = TOOL.next_state(
            prepared,
            prepared_sha256,
            "armed",
            child=child_identity,
            outcome={"terminal_reap_pending": True},
        )
        _armed_path, armed_sha256 = TOOL.publish_state(paths, armed)
        terminal = TOOL.next_state(
            armed,
            armed_sha256,
            "success",
            child=child_identity,
            outcome={"publication": "durable-before-managed-signal"},
        )
        terminal_path, terminal_sha256 = TOOL.publish_state(paths, terminal)

        class ReadyWait:
            """Expose readiness only after the production signal scope is active."""

            def wait(self, *, timeout: float) -> int:
                TOOL.atomic_publish_noreplace(
                    ready_path,
                    TOOL.canonical_json(
                        {
                            "schema": 1,
                            "boundary": TOOL.MANAGED_SIGNAL_BOUNDARY,
                            "transaction_id": transaction_id,
                            "signal": signum,
                            "signal_name": signal.Signals(signum).name,
                            "terminal_state_path": os.fspath(terminal_path),
                            "terminal_state_sha256": terminal_sha256,
                            "grant_path": os.fspath(grant_path),
                            "coordinator": coordinator,
                            "child": child_identity,
                        }
                    ),
                )
                return process.wait(timeout=timeout)

        try:
            TOOL.wait_for_terminal_child(ReadyWait(), timeout=20.0)
        except TOOL.TransactionInterrupted as error:
            restored_handlers = all(
                signal.getsignal(managed) == original_handlers[managed]
                for managed in TOOL.TRANSACTION_SIGNALS
            )
            restored_mask = set(pthread_sigmask(signal.SIG_BLOCK, set()))
            TOOL.terminate_direct_process(process, 5.0)
            TOOL.wait_group_empty(process.pid, process.pid, timeout=5.0)
            child_after = TOOL.process_identity(process.pid)
            group_after = TOOL.process_group_members(process.pid, process.pid)
            TOOL.atomic_publish_noreplace(
                proof_path,
                TOOL.canonical_json(
                    {
                        "schema": 1,
                        "boundary": TOOL.MANAGED_SIGNAL_BOUNDARY,
                        "caught_signal": error.signum,
                        "exit_status": 128 + error.signum,
                        "terminal_state_path": os.fspath(terminal_path),
                        "terminal_state_sha256": terminal_sha256,
                        "handlers_restored_before_cleanup": restored_handlers,
                        "mask_restored_before_cleanup": restored_mask == original_mask,
                        "managed_signals_blocked_after": sorted(
                            int(managed)
                            for managed in restored_mask.intersection(
                                TOOL.TRANSACTION_SIGNALS
                            )
                        ),
                        "child": child_identity,
                        "child_returncode": process.returncode,
                        "child_normalized_status": TOOL.normalize_status(
                            process.returncode
                        ),
                        "child_identity_after": child_after,
                        "child_group_members_after": group_after,
                        "direct_child_reaped": (
                            process.poll() is not None and child_after is None
                        ),
                    }
                ),
            )
            return 128 + error.signum
        raise AssertionError("direct child exited without a managed signal")
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
        if process is not None and process.poll() is None:
            TOOL.terminate_direct_process(process, 5.0)
        if process is not None:
            TOOL.wait_group_empty(process.pid, process.pid, timeout=5.0)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


class ManagedSignalIntegrationTests(unittest.TestCase):
    def test_real_hup_int_term_exit_and_reap_without_residue(self) -> None:
        expected_statuses = {
            signal.SIGHUP: 129,
            signal.SIGINT: 130,
            signal.SIGTERM: 143,
        }
        for signum, expected_status in expected_statuses.items():
            with self.subTest(signum=signal.Signals(signum).name):
                with tempfile.TemporaryDirectory(
                    prefix=f"jsonschema-signal-{int(signum)}-"
                ) as temporary:
                    root = Path(temporary).resolve()
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-I",
                            "-B",
                            os.fspath(Path(__file__).resolve()),
                            HELPER_MODE,
                            os.fspath(root),
                            str(signum),
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=TOOL.clean_environment(),
                        cwd=REPOSITORY_ROOT,
                        start_new_session=True,
                    )
                    ready: Any = None
                    try:
                        ready = wait_for_file(
                            root / "signal-ready.json", process, 10.0
                        )
                        self.assertEqual(
                            ready["boundary"], "terminal-child-reap-only"
                        )
                        self.assertEqual(ready["signal"], signum)
                        self.assertEqual(
                            ready["signal_name"], signal.Signals(signum).name
                        )
                        coordinator = ready["coordinator"]
                        child = ready["child"]
                        self.assertEqual(coordinator["pid"], process.pid)
                        self.assertEqual(coordinator["process_group"], process.pid)
                        self.assertEqual(coordinator["session"], process.pid)
                        observed_coordinator = TOOL.process_identity(process.pid)
                        self.assertIsNotNone(observed_coordinator)
                        assert observed_coordinator is not None
                        for key in (
                            "pid",
                            "ppid",
                            "process_group",
                            "session",
                            "start_ticks",
                        ):
                            self.assertEqual(
                                observed_coordinator[key], coordinator[key]
                            )
                        self.assertEqual(child["ppid"], process.pid)
                        self.assertEqual(child["process_group"], child["pid"])
                        self.assertEqual(child["session"], child["pid"])
                        observed_child = TOOL.process_identity(child["pid"])
                        self.assertIsNotNone(observed_child)
                        assert observed_child is not None
                        for key in (
                            "pid",
                            "ppid",
                            "process_group",
                            "session",
                            "start_ticks",
                        ):
                            self.assertEqual(observed_child[key], child[key])
                        paths = TOOL.Paths(ready["transaction_id"], root, True)
                        terminal_path = Path(ready["terminal_state_path"])
                        self.assertEqual(
                            terminal_path, TOOL.state_path(paths, "success")
                        )
                        terminal = TOOL.read_json_regular(
                            terminal_path, "durable terminal signal fixture"
                        )
                        self.assertEqual(terminal["phase"], "success")
                        self.assertEqual(
                            terminal["outcome"]["publication"],
                            "durable-before-managed-signal",
                        )
                        self.assertEqual(terminal["child"], child)
                        self.assertEqual(
                            (
                                terminal["pending_total"],
                                terminal["unknown_total"],
                                terminal["failed_total"],
                            ),
                            (0, 0, 0),
                        )
                        current, current_sha256 = TOOL.load_current_state(paths)
                        self.assertEqual(current, terminal)
                        self.assertEqual(
                            current_sha256, ready["terminal_state_sha256"]
                        )
                        self.assertEqual(
                            TOOL.FileIdentity.observe(paths.canonical_state),
                            TOOL.FileIdentity.observe(terminal_path),
                        )
                        self.assertEqual(
                            TOOL.sha256_file(terminal_path),
                            ready["terminal_state_sha256"],
                        )
                        grant_path = Path(ready["grant_path"])
                        self.assertEqual(grant_path, root / "signal.grant")
                        TOOL.atomic_publish_noreplace(grant_path, b"deliver\n")
                        stdout, stderr = process.communicate(timeout=10.0)
                        self.assertEqual(process.returncode, expected_status)
                        self.assertEqual(stdout, b"")
                        self.assertEqual(stderr, b"")
                        proof = TOOL.read_json_regular(
                            root / "signal-proof.json", "managed signal proof"
                        )
                        self.assertEqual(proof["caught_signal"], signum)
                        self.assertEqual(proof["exit_status"], expected_status)
                        self.assertEqual(
                            proof["terminal_state_sha256"],
                            ready["terminal_state_sha256"],
                        )
                        self.assertTrue(proof["handlers_restored_before_cleanup"])
                        self.assertTrue(proof["mask_restored_before_cleanup"])
                        self.assertEqual(proof["managed_signals_blocked_after"], [])
                        self.assertTrue(proof["direct_child_reaped"])
                        self.assertIsNone(proof["child_identity_after"])
                        self.assertEqual(proof["child_group_members_after"], [])
                        self.assertEqual(proof["child_returncode"], -signal.SIGTERM)
                        self.assertEqual(proof["child_normalized_status"], 143)
                        self.assertIsNone(TOOL.process_identity(child["pid"]))
                        self.assertEqual(
                            TOOL.process_group_members(child["pid"], child["pid"]),
                            [],
                        )
                    finally:
                        if process.poll() is None:
                            with contextlib.suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5.0)
                        process.communicate(timeout=1.0)
                        if isinstance(ready, dict) and isinstance(
                            ready.get("child"), dict
                        ):
                            child = ready["child"]
                            observed = TOOL.process_identity(child["pid"])
                            if observed is not None and all(
                                observed.get(key) == child.get(key)
                                for key in (
                                    "pid",
                                    "process_group",
                                    "session",
                                    "start_ticks",
                                )
                            ):
                                with contextlib.suppress(OSError, ProcessLookupError):
                                    descriptor = os.pidfd_open(child["pid"], 0)
                                    try:
                                        signal.pidfd_send_signal(
                                            descriptor, signal.SIGKILL, None, 0
                                        )
                                    finally:
                                        os.close(descriptor)
                            deadline = time.monotonic() + 5.0
                            while (
                                TOOL.process_identity(child["pid"]) is not None
                                and time.monotonic() < deadline
                            ):
                                time.sleep(0.01)
                        self.assertIsNone(TOOL.process_identity(process.pid))
                        self.assertEqual(
                            TOOL.process_group_members(process.pid, process.pid), []
                        )


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == HELPER_MODE:
        raise SystemExit(managed_signal_helper(Path(sys.argv[2]), int(sys.argv[3])))
    if len(sys.argv) == 7 and sys.argv[1] == CHILD_MODE:
        raise SystemExit(
            managed_signal_child(
                int(sys.argv[2]),
                int(sys.argv[3]),
                int(sys.argv[4]),
                int(sys.argv[5]),
                Path(sys.argv[6]),
            )
        )
    unittest.main()

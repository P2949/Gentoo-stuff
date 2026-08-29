#!/usr/bin/env python3
"""Hermetic tests for the jsonschema prerequisite transaction."""

from __future__ import annotations

import argparse
import copy
import contextlib
import errno
import io
import importlib.util
import inspect
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, cast


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "optimization"
    / "recovery"
    / "install-jsonschema-prerequisite.py"
)
SPEC = importlib.util.spec_from_file_location(
    "gentoo_jsonschema_prerequisite_transaction", TOOL_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load jsonschema prerequisite transaction: {TOOL_PATH}")
TOOL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TOOL
SPEC.loader.exec_module(TOOL)


PRETEND_LINES = (
    "[ebuild  N     ] dev-python/attrs-25.3.0::gentoo",
    "[ebuild  N     ] dev-python/jsonschema-4.25.1::gentoo",
)


def fixture_plan(lines: Sequence[str] = PRETEND_LINES) -> dict[str, Any]:
    return TOOL.parse_pretend_output("\n".join(lines) + "\n", set())


def make_fixture_paths(root: Path, transaction_id: str = "fixture-transaction") -> Any:
    proc = root / "proc/sys/kernel/random"
    proc.mkdir(parents=True)
    (proc / "boot_id").write_text(
        "11111111-2222-3333-4444-555555555555\n", encoding="ascii"
    )
    paths = TOOL.Paths(transaction_id, root, True)
    paths.state_parent.mkdir(parents=True)
    return paths


def make_base_state(paths: Any) -> dict[str, Any]:
    return TOOL.base_state(
        paths,
        authority={"fixture": True},
        resolver={"vdb_before": {"cpvs": []}},
        plan=fixture_plan(),
        private_roots={"fixture": True},
    )


def make_writable(root: Path) -> None:
    """Undo authority sealing so TemporaryDirectory can remove the fixture."""

    if not root.exists():
        return
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        try:
            path.chmod(0o700 if path.is_dir() else 0o600)
        except FileNotFoundError:
            pass


def filesystem_snapshot(root: Path) -> list[tuple[object, ...]]:
    """Capture durable fixture identity axes without atime noise."""

    rows: list[tuple[object, ...]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        metadata = path.lstat()
        payload: object = None
        if path.is_symlink():
            payload = os.readlink(path)
        elif path.is_file():
            payload = TOOL.sha256_file(path)
        rows.append(
            (
                path.relative_to(root).as_posix(),
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                payload,
            )
        )
    return rows


class HermeticProductionPaths(TOOL.Paths):
    """Use production gate ordering while keeping every resolved path in a fixture."""

    def rooted(self, production: str) -> Path:
        path = Path(production)
        if not path.is_absolute():
            raise AssertionError(f"test production path is not absolute: {path}")
        return self.root / path.relative_to("/")


class CopyRunner:
    """Minimal injected runner for copy-tree materialization fixtures."""

    def __init__(self, *, mutate_source_after_copy: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.mutate_source_after_copy = mutate_source_after_copy

    def run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
        timeout: float,
        cwd: Path | None = None,
    ) -> Any:
        del cwd
        command = tuple(argv)
        self.calls.append(command)
        if timeout <= 0 or environment.get("LC_ALL") != "C":
            raise AssertionError("materializer did not use its bounded clean runner")
        source = Path(command[-2])
        destination = Path(command[-1])
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
        if self.mutate_source_after_copy:
            (source / "drift-after-copy").write_text("changed\n", encoding="utf-8")
        return TOOL.CommandResult(command, 0, b"", b"")


class GitRunner:
    """Scripted effective-worktree runner with injectable authority drift."""

    COMMIT = "a" * 40
    DRIFTED_COMMIT = "b" * 40

    def __init__(
        self,
        *,
        drift_final_source_head: bool = False,
        mutate_source_after_copy: bool = False,
        corrupt_destination_after_copy: bool = False,
        observation_drift_label: str | None = None,
        destination_observation_mismatch_label: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.rev_parse_calls = 0
        self.drift_final_source_head = drift_final_source_head
        self.mutate_source_after_copy = mutate_source_after_copy
        self.corrupt_destination_after_copy = corrupt_destination_after_copy
        self.observation_drift_label = observation_drift_label
        self.destination_observation_mismatch_label = (
            destination_observation_mismatch_label
        )
        self.source: Path | None = None
        self.destination: Path | None = None
        self.source_observation_calls = {
            "status": 0,
            "diff": 0,
            "untracked": 0,
        }

    def run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
        timeout: float,
        cwd: Path | None = None,
    ) -> Any:
        del cwd
        command = tuple(argv)
        self.calls.append(command)
        if timeout <= 0:
            raise AssertionError("Git materializer did not use its bounded runner")
        if Path(command[0]).name == "cp":
            if environment.get("LC_ALL") != "C":
                raise AssertionError("effective worktree copy used a foreign environment")
            separator = command.index("--")
            destination = Path(command[-1])
            for value in command[separator + 1 : -1]:
                source = Path(value)
                target = destination / source.name
                if source.is_dir() and not source.is_symlink():
                    shutil.copytree(source, target, symlinks=True)
                elif source.is_symlink():
                    target.symlink_to(os.readlink(source))
                else:
                    shutil.copy2(source, target)
            if self.mutate_source_after_copy:
                if self.source is None:
                    raise AssertionError("source was not observed before effective copy")
                (self.source / "drift-after-copy").write_text(
                    "changed\n", encoding="utf-8"
                )
            if self.corrupt_destination_after_copy:
                (destination / "profiles/repo_name").write_text(
                    "corrupt destination\n", encoding="utf-8"
                )
            return TOOL.CommandResult(command, 0, b"", b"")
        if environment.get("GIT_CONFIG_NOSYSTEM") != "1":
            raise AssertionError("Git materializer did not use its isolated runner")
        if "rev-parse" in command:
            self.rev_parse_calls += 1
            observed = Path(command[command.index("-C") + 1])
            if self.source is None:
                self.source = observed
            commit = (
                self.DRIFTED_COMMIT
                if self.drift_final_source_head and self.rev_parse_calls == 3
                else self.COMMIT
            )
            return TOOL.CommandResult(command, 0, (commit + "\n").encode(), b"")
        label = (
            "status"
            if "status" in command
            else "diff"
            if "diff" in command
            else "untracked"
            if "ls-files" in command
            else None
        )
        if label is not None:
            observed = Path(command[command.index("-C") + 1])
            # The clean-checkout probe intentionally omits the NUL status flag.
            if label == "status" and "-z" not in command:
                return TOOL.CommandResult(command, 0, b"", b"")
            if observed == self.source:
                self.source_observation_calls[label] += 1
                payload = (
                    f"{label}-drift".encode()
                    if self.observation_drift_label == label
                    and self.source_observation_calls[label] == 2
                    else b""
                )
            elif observed == self.destination:
                payload = (
                    f"{label}-destination-mismatch".encode()
                    if self.destination_observation_mismatch_label == label
                    else b""
                )
            else:
                raise AssertionError(
                    f"observation used an unknown Git worktree: {observed}"
                )
            return TOOL.CommandResult(command, 0, payload, b"")
        if "clone" in command:
            self.destination = Path(command[-1])
            shutil.copytree(Path(command[-2]), Path(command[-1]), symlinks=True)
            return TOOL.CommandResult(command, 0, b"", b"")
        if "checkout" in command:
            return TOOL.CommandResult(command, 0, b"", b"")
        if "rm" in command:
            destination = Path(command[command.index("-C") + 1])
            for child in list(destination.iterdir()):
                if child.name == ".git":
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            return TOOL.CommandResult(command, 0, b"", b"")
        if "clean" in command or "reset" in command:
            return TOOL.CommandResult(command, 0, b"", b"")
        raise AssertionError(f"unexpected scripted Git command: {command}")


class FakePromptProcess:
    def __init__(self, status: int | None = None) -> None:
        self.status = status

    def poll(self) -> int | None:
        return self.status


class InternalCommandParserTests(unittest.TestCase):
    def test_pdeath_exec_remainder_excludes_argparse_separator(self) -> None:
        arguments = TOOL.build_parser().parse_args(
            [
                "__pdeath-exec",
                "101",
                "202",
                "--",
                "/usr/bin/true",
                "--fixture-option",
            ]
        )

        self.assertEqual(arguments.action, "__pdeath-exec")
        self.assertEqual(arguments.parent_pid, 101)
        self.assertEqual(arguments.parent_start_ticks, 202)
        self.assertEqual(
            arguments.command, ["/usr/bin/true", "--fixture-option"]
        )

    def test_barrier_remainder_excludes_argparse_separator(self) -> None:
        arguments = TOOL.build_parser().parse_args(
            [
                "__barrier",
                "7",
                "101",
                "202",
                "--",
                "/usr/bin/true",
                "--fixture-option",
            ]
        )

        self.assertEqual(arguments.action, "__barrier")
        self.assertEqual(arguments.barrier_fd, 7)
        self.assertEqual(arguments.parent_pid, 101)
        self.assertEqual(arguments.parent_start_ticks, 202)
        self.assertEqual(
            arguments.command, ["/usr/bin/true", "--fixture-option"]
        )

    def test_portage_action_remainder_excludes_argparse_separator(self) -> None:
        arguments = TOOL.build_parser().parse_args(
            [
                "__portage-action",
                "/tmp/prepared.json",
                "a" * 64,
                "9",
                "fixture-session",
                "--",
                "/usr/bin/true",
                "--fixture-option",
            ]
        )

        self.assertEqual(arguments.action, "__portage-action")
        self.assertEqual(arguments.prepared_state, Path("/tmp/prepared.json"))
        self.assertEqual(arguments.prepared_sha256, "a" * 64)
        self.assertEqual(arguments.control_fd, 9)
        self.assertEqual(arguments.control_session, "fixture-session")
        self.assertEqual(
            arguments.command, ["/usr/bin/true", "--fixture-option"]
        )


class ManagedSignalBoundaryTests(unittest.TestCase):
    def test_hup_int_and_term_map_to_exact_interruption(self) -> None:
        for signum in TOOL.TRANSACTION_SIGNALS:
            with self.subTest(signum=signal.Signals(signum).name):
                with self.assertRaises(TOOL.TransactionInterrupted) as raised:
                    TOOL.raise_transaction_interrupted(signum, None)
                self.assertEqual(raised.exception.signum, signum)

        with self.assertRaisesRegex(
            TOOL.TransactionError, "unmanaged signal reached transaction handler"
        ):
            TOOL.raise_transaction_interrupted(signal.SIGUSR1, None)

    def test_handler_install_and_restore_are_atomic_signal_boundaries(self) -> None:
        events: list[tuple[object, ...]] = []
        prior_mask = {signal.SIGUSR1}
        originals = {signum: object() for signum in TOOL.TRANSACTION_SIGNALS}

        def pthread_sigmask(how: int, mask: object) -> set[signal.Signals]:
            events.append(
                (
                    "mask",
                    how,
                    frozenset(cast(Iterable[signal.Signals], mask)),
                )
            )
            return set(prior_mask)

        def getsignal(signum: signal.Signals) -> object:
            events.append(("get", signum))
            return originals[signum]

        def set_signal(signum: signal.Signals, handler: object) -> None:
            events.append(("set", signum, handler))

        previous: dict[signal.Signals, object] = {}
        with (
            unittest.mock.patch.object(
                TOOL.signal, "pthread_sigmask", side_effect=pthread_sigmask
            ),
            unittest.mock.patch.object(
                TOOL.signal, "getsignal", side_effect=getsignal
            ),
            unittest.mock.patch.object(
                TOOL.signal, "signal", side_effect=set_signal
            ),
        ):
            TOOL.install_transaction_signal_handlers(
                TOOL.raise_transaction_interrupted, previous
            )
            TOOL.restore_transaction_signal_handlers(previous)

        self.assertEqual(previous, originals)
        block_indexes = [
            index
            for index, event in enumerate(events)
            if event[:2] == ("mask", signal.SIG_BLOCK)
        ]
        restore_indexes = [
            index
            for index, event in enumerate(events)
            if event[:2] == ("mask", signal.SIG_SETMASK)
        ]
        self.assertEqual(len(block_indexes), 2)
        self.assertEqual(len(restore_indexes), 2)
        self.assertLess(block_indexes[0], restore_indexes[0])
        self.assertLess(restore_indexes[0], block_indexes[1])
        self.assertLess(block_indexes[1], restore_indexes[1])
        for signum in TOOL.TRANSACTION_SIGNALS:
            install = events.index(
                ("set", signum, TOOL.raise_transaction_interrupted)
            )
            restore = events.index(("set", signum, originals[signum]))
            self.assertLess(block_indexes[0], install)
            self.assertLess(install, restore_indexes[0])
            self.assertLess(block_indexes[1], restore)
            self.assertLess(restore, restore_indexes[1])

    def test_scope_restores_dispositions_after_interruption(self) -> None:
        originals = {signum: object() for signum in TOOL.TRANSACTION_SIGNALS}
        installed: dict[signal.Signals, object] = {}

        def pthread_sigmask(_how: int, _mask: object) -> set[signal.Signals]:
            return set()

        def set_signal(signum: signal.Signals, handler: object) -> None:
            installed[signum] = handler

        with (
            unittest.mock.patch.object(
                TOOL.signal, "pthread_sigmask", side_effect=pthread_sigmask
            ),
            unittest.mock.patch.object(
                TOOL.signal,
                "getsignal",
                side_effect=lambda signum: originals[signum],
            ),
            unittest.mock.patch.object(
                TOOL.signal, "signal", side_effect=set_signal
            ),
        ):
            with self.assertRaises(TOOL.TransactionInterrupted) as raised:
                with TOOL.transaction_signal_scope():
                    handler = cast(
                        Callable[[int, object], object],
                        installed[signal.SIGTERM],
                    )
                    self.assertIs(handler, TOOL.raise_transaction_interrupted)
                    handler(signal.SIGTERM, None)
            self.assertEqual(raised.exception.signum, signal.SIGTERM)

        self.assertEqual(installed, originals)

    def test_inherited_blocked_managed_signal_is_rejected(self) -> None:
        pthread_sigmask = getattr(signal, "pthread_sigmask", None)
        if not callable(pthread_sigmask):
            self.skipTest("pthread_sigmask is unavailable")
        original_mask = pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            previous: dict[signal.Signals, object] = {}
            with self.assertRaisesRegex(
                TOOL.TransactionError,
                "inherited blocked managed signals: SIGTERM",
            ):
                TOOL.install_transaction_signal_handlers(
                    TOOL.raise_transaction_interrupted, previous
                )
            self.assertFalse(previous)
            self.assertIn(
                signal.SIGTERM, pthread_sigmask(signal.SIG_BLOCK, set())
            )
        finally:
            pthread_sigmask(signal.SIG_SETMASK, original_mask)

    def test_managed_scope_is_only_the_terminal_child_reap_boundary(self) -> None:
        self.assertEqual(
            TOOL.MANAGED_SIGNAL_BOUNDARY, "terminal-child-reap-only"
        )
        events: list[object] = []

        @contextlib.contextmanager
        def managed_scope() -> Any:
            events.append("scope-enter")
            try:
                yield
            finally:
                events.append("scope-exit")

        class Process:
            def wait(self, *, timeout: float) -> None:
                events.append(("wait", timeout))

        with unittest.mock.patch.object(
            TOOL, "transaction_signal_scope", side_effect=managed_scope
        ):
            TOOL.wait_for_terminal_child(Process(), timeout=17.0)
        self.assertEqual(
            events, ["scope-enter", ("wait", 17.0), "scope-exit"]
        )

        helper_source = inspect.getsource(TOOL.wait_for_terminal_child)
        self.assertEqual(helper_source.count("transaction_signal_scope"), 1)
        for function in (
            TOOL.run_armed_source_child,
            TOOL.run_held_lock_recovery,
        ):
            source = inspect.getsource(function)
            self.assertEqual(source.count("wait_for_terminal_child"), 1)
            self.assertNotIn("transaction_signal_scope", source)
            self.assertLess(
                source.rfind("publish_state("),
                source.index("wait_for_terminal_child"),
            )

        for function in (
            TOOL.prepare_command,
            TOOL.run_command,
            TOOL.recover_command,
        ):
            self.assertNotIn(
                "transaction_signal_scope", inspect.getsource(function)
            )


class PlanContractTests(unittest.TestCase):
    def test_parse_pretend_preserves_displayed_atom_order_and_sorts_rows(self) -> None:
        plan = fixture_plan(tuple(reversed(PRETEND_LINES)))

        self.assertEqual(
            plan["ordered_exact_atoms"],
            [
                "=dev-python/jsonschema-4.25.1::gentoo",
                "=dev-python/attrs-25.3.0::gentoo",
            ],
        )
        self.assertEqual(
            [row["cpv"] for row in plan["rows"]],
            ["dev-python/attrs-25.3.0", "dev-python/jsonschema-4.25.1"],
        )
        self.assertEqual(TOOL.exact_plan_atoms(plan), plan["ordered_exact_atoms"])

    def test_parse_pretend_rejects_non_source_action(self) -> None:
        with self.assertRaisesRegex(
            TOOL.TransactionError, "pretend selected a non-new or non-source action"
        ):
            fixture_plan(
                (
                    PRETEND_LINES[0],
                    "[binary  N     ] dev-python/jsonschema-4.25.1::gentoo",
                )
            )

    def test_parse_pretend_rejects_installed_cpv(self) -> None:
        with self.assertRaisesRegex(
            TOOL.TransactionError, "pretend selected an already installed CPV"
        ):
            TOOL.parse_pretend_output(
                "\n".join(PRETEND_LINES), {"dev-python/attrs-25.3.0"}
            )

    def test_parse_pretend_rejects_missing_jsonschema(self) -> None:
        with self.assertRaisesRegex(
            TOOL.TransactionError, "did not select exactly one jsonschema CPV"
        ):
            fixture_plan((PRETEND_LINES[0],))

    def test_parse_pretend_rejects_duplicate_cpv(self) -> None:
        with self.assertRaisesRegex(
            TOOL.TransactionError, "selection is empty or duplicated"
        ):
            fixture_plan((*PRETEND_LINES, PRETEND_LINES[0]))

    def test_compare_plans_binds_order_not_only_membership(self) -> None:
        expected = fixture_plan()
        observed = copy.deepcopy(expected)
        observed["ordered_exact_atoms"].reverse()

        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "exact re-pretend plan differs from the reviewed plan",
        ):
            TOOL.compare_plans(expected, observed)

    def test_exact_plan_atoms_rejects_digest_and_row_identity_drift(self) -> None:
        plan = fixture_plan()
        plan["rows"][0]["repository"] = "overlay"

        with self.assertRaisesRegex(
            TOOL.TransactionError, "reviewed plan row identities disagree"
        ):
            TOOL.exact_plan_atoms(plan)

    def test_source_command_executes_internal_ask_gate_with_exact_atoms(self) -> None:
        tools = {
            "python": Path("/fixture/python"),
            "transaction": Path("/fixture/transaction"),
            "emerge": Path("/fixture/emerge"),
        }
        prepared = Path("/fixture/prepared.json")
        digest = "a" * 64
        plan = fixture_plan(tuple(reversed(PRETEND_LINES)))

        session = "b" * 64
        command = TOOL.source_emerge_command(
            tools,
            plan,
            prepared,
            digest,
            9,
            session,
        )

        self.assertEqual(
            command[:11],
            [
                "/fixture/python",
                "-I",
                "-B",
                "/fixture/transaction",
                "__portage-action",
                "/fixture/prepared.json",
                digest,
                "9",
                session,
                "--",
                "/fixture/emerge",
            ],
        )
        self.assertIn("--ask=y", command)
        self.assertIn("--package-moves=n", command)
        self.assertNotIn("--ask=n", command)
        self.assertNotIn("--pretend", command)
        self.assertEqual(command[-2:], plan["ordered_exact_atoms"])


class StateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.paths = make_fixture_paths(self.root)
        self.prepared = make_base_state(self.paths)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_base_state_has_exact_prepared_schema_and_recovery_claim(self) -> None:
        validated = TOOL.validate_state(self.prepared)

        self.assertEqual(validated["phase"], "prepared")
        self.assertEqual(
            validated["recovery_contract"]["claim"],
            "declared-package-manager-authorities-only",
        )
        self.assertFalse(
            validated["recovery_contract"]["whole_host_byte_identity"]
        )
        self.assertTrue(
            validated["recovery_contract"]
            ["source_emerge_may_never_be_retried_after_armed"]
        )

    def test_live_entrypoints_remain_fail_closed_pending_final_candidate_a_proof(self) -> None:
        self.assertIs(TOOL.LIVE_PREPARATION_ENABLED, False)
        self.assertIs(TOOL.LIVE_MUTATION_ENABLED, False)

    def test_state_schema_rejects_unknown_fields(self) -> None:
        state = dict(self.prepared)
        state["unreviewed"] = True
        with self.assertRaisesRegex(
            TOOL.TransactionError, "transaction state has an invalid schema"
        ):
            TOOL.validate_state(state)

    def test_transition_chain_binds_every_predecessor_digest(self) -> None:
        prepared_sha = TOOL.sha256_bytes(TOOL.canonical_json(self.prepared))
        child = {"pid": 123, "start_ticks": 456}
        armed = TOOL.next_state(
            self.prepared, prepared_sha, "armed", child=child, outcome={"gate": "yes"}
        )
        armed_sha = TOOL.sha256_bytes(TOOL.canonical_json(armed))
        rollback = TOOL.next_state(
            armed, armed_sha, "rollback-in-progress", child=child
        )
        rollback_sha = TOOL.sha256_bytes(TOOL.canonical_json(rollback))
        rolled_back = TOOL.next_state(
            rollback, rollback_sha, "rolled-back", outcome={"restored": True}
        )

        self.assertEqual(armed["prepared_state_sha256"], prepared_sha)
        self.assertEqual(rollback["previous_state_sha256"], armed_sha)
        self.assertEqual(rollback["prepared_state_sha256"], prepared_sha)
        self.assertEqual(rolled_back["previous_state_sha256"], rollback_sha)
        self.assertEqual(
            (rolled_back["pending_total"], rolled_back["failed_total"]), (0, 1)
        )
        TOOL.validate_state(rolled_back)

    def test_invalid_transition_is_distinctly_rejected(self) -> None:
        prepared_sha = TOOL.sha256_bytes(TOOL.canonical_json(self.prepared))
        with self.assertRaisesRegex(
            TOOL.TransactionError, "invalid transaction transition: prepared -> success"
        ):
            TOOL.next_state(self.prepared, prepared_sha, "success")

    def test_immutable_publication_and_no_retry_after_armed(self) -> None:
        prepared_path, prepared_sha = TOOL.publish_state(self.paths, self.prepared)
        TOOL.assert_no_armed_retry(self.paths)
        armed = TOOL.next_state(
            self.prepared,
            prepared_sha,
            "armed",
            child={"pid": 123},
            outcome={"authorized": True},
        )
        armed_path, _armed_sha = TOOL.publish_state(self.paths, armed)

        loaded, _digest = TOOL.load_current_state(self.paths)
        self.assertEqual(loaded["phase"], "armed")
        self.assertNotEqual(prepared_path.stat().st_ino, armed_path.stat().st_ino)
        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "source emerge may not be started from transaction phase armed",
        ):
            TOOL.assert_no_armed_retry(self.paths)

    def test_reconcile_advances_missing_canonical_to_unique_durable_successor(self) -> None:
        _prepared_path, prepared_sha = TOOL.publish_state(self.paths, self.prepared)
        armed = TOOL.next_state(
            self.prepared,
            prepared_sha,
            "armed",
            child={"pid": 123},
            outcome={"authorized": True},
        )
        armed_path = TOOL.state_path(self.paths, "armed")
        armed_sha = TOOL.atomic_publish_noreplace(
            armed_path, TOOL.canonical_json(armed)
        )
        self.paths.canonical_state.unlink()

        reconciled = TOOL.reconcile_state_chain(self.paths)

        self.assertIsNotNone(reconciled)
        assert reconciled is not None
        self.assertEqual(reconciled[0]["phase"], "armed")
        self.assertEqual(reconciled[1], armed_sha)
        self.assertEqual(
            TOOL.FileIdentity.observe(self.paths.canonical_state),
            TOOL.FileIdentity.observe(armed_path),
        )
        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "source emerge may not be started from transaction phase armed",
        ):
            TOOL.assert_no_armed_retry(self.paths)

    def test_reconcile_advances_stale_canonical_to_unique_successor(self) -> None:
        prepared_path, prepared_sha = TOOL.publish_state(self.paths, self.prepared)
        armed = TOOL.next_state(self.prepared, prepared_sha, "armed")
        armed_path = TOOL.state_path(self.paths, "armed")
        TOOL.atomic_publish_noreplace(armed_path, TOOL.canonical_json(armed))
        self.assertEqual(
            TOOL.FileIdentity.observe(self.paths.canonical_state),
            TOOL.FileIdentity.observe(prepared_path),
        )

        state, _digest = TOOL.reconcile_state_chain(self.paths) or ({}, "")

        self.assertEqual(state.get("phase"), "armed")
        self.assertEqual(
            TOOL.FileIdentity.observe(self.paths.canonical_state),
            TOOL.FileIdentity.observe(armed_path),
        )

    def test_reconcile_rejects_branched_durable_chain(self) -> None:
        _prepared_path, prepared_sha = TOOL.publish_state(self.paths, self.prepared)
        armed = TOOL.next_state(self.prepared, prepared_sha, "armed")
        armed_path = TOOL.state_path(self.paths, "armed")
        armed_sha = TOOL.atomic_publish_noreplace(
            armed_path, TOOL.canonical_json(armed)
        )
        success = TOOL.next_state(armed, armed_sha, "success")
        rollback = TOOL.next_state(armed, armed_sha, "rollback-in-progress")
        TOOL.atomic_publish_noreplace(
            TOOL.state_path(self.paths, "success"), TOOL.canonical_json(success)
        )
        TOOL.atomic_publish_noreplace(
            TOOL.state_path(self.paths, "rollback-in-progress"),
            TOOL.canonical_json(rollback),
        )

        with self.assertRaisesRegex(
            TOOL.TransactionError, "transaction state chain branches after armed"
        ):
            TOOL.reconcile_state_chain(self.paths)

    def test_reconcile_rejects_foreign_predecessor_digest(self) -> None:
        _prepared_path, prepared_sha = TOOL.publish_state(self.paths, self.prepared)
        armed = TOOL.next_state(self.prepared, prepared_sha, "armed")
        armed["previous_state_sha256"] = "f" * 64
        TOOL.atomic_publish_noreplace(
            TOOL.state_path(self.paths, "armed"), TOOL.canonical_json(armed)
        )

        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "durable armed state predecessor digest differs",
        ):
            TOOL.reconcile_state_chain(self.paths)

    def test_canonical_copy_cannot_impersonate_immutable_phase_inode(self) -> None:
        _prepared_path, prepared_sha = TOOL.publish_state(self.paths, self.prepared)
        armed = TOOL.next_state(self.prepared, prepared_sha, "armed")
        armed_path, _armed_sha = TOOL.publish_state(self.paths, armed)
        payload = armed_path.read_bytes()
        self.paths.canonical_state.unlink()
        self.paths.canonical_state.write_bytes(payload)

        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "canonical transaction state differs from its immutable phase state",
        ):
            TOOL.load_current_state(self.paths)

    def test_delta_classification_and_reverse_rollback_order(self) -> None:
        before = {"cpvs": ["sys-apps/coreutils-9.7"]}
        after = {
            "cpvs": [
                "sys-apps/coreutils-9.7",
                "dev-python/attrs-25.3.0",
                "dev-python/jsonschema-4.25.1",
            ]
        }
        plan = fixture_plan()

        delta = TOOL.classify_vdb_delta(before, after, plan)

        self.assertTrue(delta["exact_success_delta"])
        self.assertTrue(delta["rollback_eligible"])
        self.assertEqual(
            TOOL.rollback_order(plan, delta["added"]),
            [
                "=dev-python/jsonschema-4.25.1::gentoo",
                "=dev-python/attrs-25.3.0::gentoo",
            ],
        )

    def test_unreviewed_delta_is_not_rollback_eligible(self) -> None:
        delta = TOOL.classify_vdb_delta(
            {"cpvs": ["sys-apps/coreutils-9.7"]},
            {
                "cpvs": [
                    "sys-apps/coreutils-9.7",
                    "dev-python/jsonschema-4.25.1",
                    "dev-python/hostile-1.0",
                ]
            },
            fixture_plan(),
        )
        self.assertFalse(delta["exact_success_delta"])
        self.assertFalse(delta["rollback_eligible"])
        self.assertEqual(delta["unexpected_added"], ["dev-python/hostile-1.0"])
        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "rollback delta contains a package outside the reviewed plan",
        ):
            TOOL.rollback_order(fixture_plan(), delta["added"])


class LiveGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.paths = make_fixture_paths(self.root, "live-gate-fixture")
        TOOL.prepare_directories(self.paths, True)
        self.paths.framework_lock.parent.mkdir(mode=0o700, parents=True)
        for lock in (
            self.paths.framework_lock,
            self.paths.project_lock,
            self.paths.generation_lock,
        ):
            lock.write_bytes(b"")
            lock.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def publish_prepared(self) -> tuple[dict[str, Any], str]:
        prepared = make_base_state(self.paths)
        _path, digest = TOOL.publish_state(self.paths, prepared)
        return prepared, digest

    def test_prepare_gate_fails_before_creating_any_transaction_object(self) -> None:
        production_paths = HermeticProductionPaths(
            "disabled-prepare", self.root / "production-root", False
        )
        production_paths.root.mkdir(mode=0o700)
        before = filesystem_snapshot(production_paths.root)

        with self.assertRaisesRegex(
            TOOL.TransactionError, "live jsonschema preparation is disabled"
        ):
            TOOL.prepare_command(
                argparse.Namespace(
                    target="dev-python/jsonschema",
                    pre_checkpoint_state=self.root / "must-not-be-read.json",
                ),
                production_paths,
            )

        self.assertEqual(filesystem_snapshot(production_paths.root), before)

    def test_run_gate_rejects_prepared_transaction_without_durable_write(self) -> None:
        self.publish_prepared()
        before = filesystem_snapshot(self.root)

        with unittest.mock.patch.object(TOOL, "verify_command", return_value=0):
            with self.assertRaisesRegex(
                TOOL.TransactionError, "live jsonschema mutation is disabled"
            ):
                TOOL.run_command(argparse.Namespace(), self.paths)

        self.assertEqual(filesystem_snapshot(self.root), before)

    def test_recover_gate_rejects_armed_transaction_without_durable_write(self) -> None:
        prepared, prepared_sha = self.publish_prepared()
        armed = TOOL.next_state(
            prepared,
            prepared_sha,
            "armed",
            child={"fixture": True},
            outcome={"authorized": True},
        )
        TOOL.publish_state(self.paths, armed)
        before = filesystem_snapshot(self.root)

        with self.assertRaisesRegex(
            TOOL.TransactionError, "live jsonschema recovery is disabled"
        ):
            TOOL.recover_command(argparse.Namespace(), self.paths)

        self.assertEqual(filesystem_snapshot(self.root), before)

    def test_internal_portage_action_gate_precedes_state_or_command_access(self) -> None:
        before = filesystem_snapshot(self.root)
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            status = TOOL.main(
                [
                    "__portage-action",
                    os.fspath(self.root / "must-not-be-read.json"),
                    "a" * 64,
                    "9",
                    "b" * 64,
                    "--",
                    "/usr/bin/emerge",
                ]
            )

        self.assertEqual(status, 1)
        self.assertIn("internal Portage mutation is disabled", stderr.getvalue())
        self.assertEqual(filesystem_snapshot(self.root), before)


class RootTrustTests(unittest.TestCase):
    def test_filesystem_root_is_allowed_only_as_the_ancestor_trust_anchor(self) -> None:
        self.assertEqual(TOOL.require_trust_root(Path("/")), Path("/"))
        with self.assertRaisesRegex(TOOL.TransactionError, "not a safe absolute path"):
            TOOL.require_absolute(Path("/"), "mutation path")
        TOOL.validate_ancestor_chain(Path("/etc"), 0, 0, Path("/"))

    def test_writable_ancestor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = root / "child"
            child.mkdir(mode=0o700)
            root.chmod(0o777)
            with self.assertRaisesRegex(TOOL.TransactionError, "untrusted directory"):
                TOOL.validate_ancestor_chain(
                    child, os.geteuid(), os.getegid(), Path("/")
                )

    def test_symlink_ancestor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir(mode=0o700)
            linked = root / "linked"
            linked.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(TOOL.TransactionError, "untrusted directory"):
                TOOL.validate_ancestor_chain(
                    linked, os.geteuid(), os.getegid(), Path("/")
                )

    def test_stable_lock_rejects_group_or_world_writable_inode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "transaction.lock"
            lock.write_bytes(b"")
            lock.chmod(0o666)

            with self.assertRaisesRegex(
                TOOL.TransactionError, "stable transaction lock is absent or foreign"
            ):
                TOOL.acquire_flock(lock, exclusive=True)

    def test_stable_lock_rejects_an_extra_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = root / "transaction.lock"
            alias = root / "transaction.lock.alias"
            lock.write_bytes(b"")
            lock.chmod(0o600)
            os.link(lock, alias)

            with self.assertRaisesRegex(
                TOOL.TransactionError, "stable transaction lock is absent or foreign"
            ):
                TOOL.acquire_flock(lock, exclusive=True)

    def test_stable_lock_rejects_a_symlink_and_accepts_exact_regular_inode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = root / "transaction.lock"
            link = root / "transaction.lock.link"
            lock.write_bytes(b"")
            lock.chmod(0o600)
            link.symlink_to(lock)

            with self.assertRaisesRegex(
                TOOL.TransactionError, "stable transaction lock is absent or foreign"
            ):
                TOOL.acquire_flock(link, exclusive=True)

            descriptor = TOOL.acquire_flock(lock, exclusive=True)
            try:
                self.assertEqual(
                    TOOL.FileIdentity.observe(lock),
                    TOOL.FileIdentity.observe(
                        Path(f"/proc/self/fd/{descriptor}"), follow=True
                    ),
                )
            finally:
                os.close(descriptor)


class ManifestAndPrivateRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        make_writable(self.root)
        self.temporary.cleanup()

    def test_tree_manifest_binds_content_mode_and_symlink_target(self) -> None:
        authority = self.root / "authority"
        authority.mkdir()
        payload = authority / "payload"
        payload.write_text("reviewed\n", encoding="utf-8")
        payload.chmod(0o640)
        (authority / "link").symlink_to("payload")
        manifest_path = self.root / "authority.manifest.json"
        manifest = TOOL.tree_manifest(authority)
        TOOL.write_manifest(manifest_path, manifest)
        self.assertEqual(TOOL.verify_manifest(authority, manifest_path), manifest)

        payload.chmod(0o600)
        with self.assertRaisesRegex(TOOL.TransactionError, "tree authority changed"):
            TOOL.verify_manifest(authority, manifest_path)

    def test_private_root_baseline_detects_post_prepare_content(self) -> None:
        roots: dict[str, str] = {}
        for key in (
            "pkgdir",
            "distdir_runtime",
            "portage_tmpdir",
            "portage_logdir",
            "ccache_dir",
            "thinlto_cache",
            "cargo_home",
            "rustup_home",
            "home",
            "xdg_cache",
            "var_lib_portage",
            "cache_edb",
            "etc",
        ):
            path = self.root / key
            path.mkdir()
            roots[key] = os.fspath(path)
        (Path(roots["var_lib_portage"]) / "world").write_text(
            "@world\n", encoding="utf-8"
        )
        baseline = TOOL.private_roots_baseline(roots)
        TOOL.verify_private_roots_baseline(baseline)

        (Path(roots["pkgdir"]) / "unexpected.gpkg.tar").write_bytes(b"foreign")
        with self.assertRaisesRegex(
            TOOL.TransactionError, "prepared private root changed before transaction arming"
        ):
            TOOL.verify_private_roots_baseline(baseline)

    def test_private_portage_outputs_bind_declared_files(self) -> None:
        root = self.root / "var-lib-portage"
        root.mkdir()
        for name in ("config", "repo_revisions", "world", "world_sets"):
            (root / name).write_text(f"{name}\n", encoding="utf-8")
        (root / "preserved_libs_registry").write_text("{}\n", encoding="utf-8")
        roots = {"var_lib_portage": os.fspath(root)}
        expected = TOOL.private_portage_outputs(roots)
        TOOL.verify_private_portage_outputs(roots, expected)

        (root / "world").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "private Portage config/sets/preserved-library authority changed",
        ):
            TOOL.verify_private_portage_outputs(roots, expected)


class RepositoryMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "profiles").mkdir()
        (self.source / "profiles/repo_name").write_text("fixture\n", encoding="utf-8")
        self.tools = {"cp": Path("/usr/bin/cp"), "git": Path("/usr/bin/git")}

    def tearDown(self) -> None:
        make_writable(self.root)
        self.temporary.cleanup()

    def git_test_environment(self) -> dict[str, str]:
        home = self.root / "git-home"
        home.mkdir(mode=0o700, exist_ok=True)
        return {**TOOL.git_environment(), "HOME": os.fspath(home)}

    def run_real_git(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [os.fspath(self.tools["git"]), *arguments],
            cwd=self.source,
            env=self.git_test_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=10.0,
        )

    def initialize_real_git_repository(self) -> str:
        if not self.tools["git"].is_file():
            self.skipTest("portable real-Git materialization requires /usr/bin/git")
        (self.source / ".gitignore").write_text(
            "*.ignored\n", encoding="utf-8"
        )
        (self.source / "tracked-delete").write_text(
            "delete me\n", encoding="utf-8"
        )
        self.run_real_git("init", "--quiet")
        self.run_real_git("add", "--all")
        self.run_real_git(
            "-c",
            "user.name=Gentoo optimization fixture",
            "-c",
            "user.email=fixture.invalid@example.invalid",
            "commit",
            "--quiet",
            "--message=fixture authority",
        )
        return self.run_real_git(
            "rev-parse", "--verify", "HEAD^{commit}"
        ).stdout.decode("ascii").strip()

    def real_git_observation(self, label: str) -> bytes:
        arguments = {
            "status": (
                "-C",
                os.fspath(self.source),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ),
            "diff": (
                "-C",
                os.fspath(self.source),
                "diff",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "HEAD",
                "--",
            ),
            "untracked": (
                "-C",
                os.fspath(self.source),
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ),
        }[label]
        result = subprocess.run(
            TOOL.git_argv(self.tools["git"], *arguments),
            env=self.git_test_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=10.0,
        )
        self.assertEqual(result.stderr, b"")
        return result.stdout

    def materialize_real_git(self, destination: Path) -> dict[str, Any]:
        environment = self.git_test_environment()
        with unittest.mock.patch.object(
            TOOL, "git_environment", return_value=environment
        ):
            return TOOL.materialize_repository(
                TOOL.RepositorySpec("overlay", self.source, "git", ()),
                destination,
                runner=TOOL.SubprocessRunner(),
                tools=self.tools,
            )

    def assert_effective_git_authority(
        self,
        *,
        result: Mapping[str, Any],
        destination: Path,
        commit: str,
        status: bytes,
        diff: bytes,
        untracked: bytes,
    ) -> None:
        git = result["git"]
        self.assertEqual(git["commit"], commit)
        self.assertIs(
            git["clean_checkout_verified_before_effective_materialization"], True
        )
        self.assertIs(git["effective_worktree_bound"], True)
        self.assertIs(git["effective_worktree_clean"], status == b"")
        self.assertEqual(git["status_sha256"], TOOL.sha256_bytes(status))
        self.assertEqual(git["diff_sha256"], TOOL.sha256_bytes(diff))
        self.assertEqual(git["untracked_sha256"], TOOL.sha256_bytes(untracked))
        self.assertEqual(
            git["effective_tree_rows_sha256"],
            TOOL.tree_manifest(
                self.source, ignore_names=frozenset({".git"})
            )["rows_sha256"],
        )

        materialization = git["effective_materialization"]
        self.assertEqual(
            materialization["tools"],
            {
                "cp": TOOL.inspect_executable(self.tools["cp"]),
                "git": TOOL.inspect_executable(self.tools["git"]),
            },
        )
        source_entries = sorted(
            (path for path in self.source.iterdir() if path.name != ".git"),
            key=lambda path: os.fsencode(path.name),
        )
        expected_commands = [
            TOOL.git_argv(
                self.tools["git"],
                "-C",
                os.fspath(destination),
                "rm",
                "-r",
                "-f",
                "--ignore-unmatch",
                "--",
                ".",
            ),
            TOOL.git_argv(
                self.tools["git"],
                "-C",
                os.fspath(destination),
                "clean",
                "-ffdx",
            ),
            [
                os.fspath(self.tools["cp"]),
                "-a",
                "--reflink=auto",
                "--one-file-system",
                "--",
                *(os.fspath(path) for path in source_entries),
                os.fspath(destination) + "/",
            ],
            TOOL.git_argv(
                self.tools["git"],
                "-C",
                os.fspath(destination),
                "reset",
                "--mixed",
                "--quiet",
                "HEAD",
            ),
        ]
        rows = materialization["rows"]
        self.assertEqual([row["argv"] for row in rows], expected_commands)
        self.assertTrue(all(row["exit_status"] == 0 for row in rows))
        self.assertTrue(
            all(
                row["stderr_sha256"] == TOOL.sha256_bytes(b"")
                for row in rows
            )
        )
        self.assertEqual(
            materialization["rows_sha256"],
            TOOL.sha256_bytes(TOOL.canonical_json(rows)),
        )
        manifest_path = Path(result["tree_manifest_path"])
        self.assertEqual(
            result["tree_manifest_sha256"], TOOL.sha256_file(manifest_path)
        )
        TOOL.verify_manifest(destination, manifest_path)

    def test_rsync_materialization_requires_injected_full_verifier(self) -> None:
        (self.source / "Manifest").write_text(
            "TIMESTAMP 2026-08-11T00:00:00Z\n", encoding="utf-8"
        )
        key = self.root / "gentoo.asc"
        key.write_text("fixture-key\n", encoding="utf-8")
        repository = TOOL.RepositorySpec(
            "gentoo", self.source, "rsync", (), key_path=key, max_age_days=0
        )
        destination = self.root / "materialized"
        runner = CopyRunner()
        calls: list[Path] = []

        def full_verifier(spec: Any, clone: Path) -> dict[str, Any]:
            self.assertEqual(spec, repository)
            self.assertEqual(
                (clone / "Manifest").read_text(encoding="utf-8"),
                (self.source / "Manifest").read_text(encoding="utf-8"),
            )
            calls.append(clone)
            return {
                "full_recursive_verification": True,
                "fixture_verifier": "accepted-exact-clone",
            }

        result = TOOL.materialize_repository(
            repository,
            destination,
            runner=runner,
            tools=self.tools,
            rsync_verifier=full_verifier,
        )

        self.assertEqual(calls, [destination])
        self.assertTrue(result["rsync"]["full_recursive_verification"])
        self.assertEqual(result["rsync"]["fixture_verifier"], "accepted-exact-clone")
        self.assertEqual(len(runner.calls), 1)
        TOOL.verify_manifest(destination, Path(result["tree_manifest_path"]))

    def test_rsync_verifier_failure_cannot_publish_authority_manifest(self) -> None:
        (self.source / "Manifest").write_text(
            "TIMESTAMP 2026-08-11T00:00:00Z\n", encoding="utf-8"
        )
        repository = TOOL.RepositorySpec(
            "gentoo", self.source, "rsync", (), key_path=self.root / "key"
        )
        destination = self.root / "materialized"

        def reject(_spec: Any, _clone: Path) -> dict[str, Any]:
            raise TOOL.TransactionError("fixture full verification rejected clone")

        with self.assertRaisesRegex(
            TOOL.TransactionError, "fixture full verification rejected clone"
        ):
            TOOL.materialize_repository(
                repository,
                destination,
                runner=CopyRunner(),
                tools=self.tools,
                rsync_verifier=reject,
            )
        self.assertFalse((self.root / "gentoo.manifest.json").exists())

    def test_local_repository_change_during_copy_is_rejected(self) -> None:
        repository = TOOL.RepositorySpec("local", self.source, None, ())
        with self.assertRaisesRegex(
            TOOL.TransactionError, "local repository changed or copied inexactly"
        ):
            TOOL.materialize_repository(
                repository,
                self.root / "materialized",
                runner=CopyRunner(mutate_source_after_copy=True),
                tools=self.tools,
            )

    def test_git_source_head_drift_after_checkout_is_rejected(self) -> None:
        repository = TOOL.RepositorySpec("overlay", self.source, "git", ())
        runner = GitRunner(drift_final_source_head=True)
        with self.assertRaisesRegex(
            TOOL.TransactionError, "Git repository source moved during materialization"
        ):
            TOOL.materialize_repository(
                repository,
                self.root / "materialized",
                runner=runner,
                tools=self.tools,
            )
        self.assertEqual(runner.rev_parse_calls, 3)

    def test_git_clean_effective_worktree_authority_is_exactly_recorded(self) -> None:
        repository = TOOL.RepositorySpec("overlay", self.source, "git", ())
        runner = GitRunner()
        destination = self.root / "materialized"
        result = TOOL.materialize_repository(
            repository,
            destination,
            runner=runner,
            tools=self.tools,
        )

        self.assert_effective_git_authority(
            result=result,
            destination=destination,
            commit=GitRunner.COMMIT,
            status=b"",
            diff=b"",
            untracked=b"",
        )
        self.assertEqual(runner.rev_parse_calls, 3)

    def test_git_effective_source_content_drift_during_copy_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "effective Git worktree changed or copied inexactly",
        ):
            TOOL.materialize_repository(
                TOOL.RepositorySpec("overlay", self.source, "git", ()),
                self.root / "materialized",
                runner=GitRunner(mutate_source_after_copy=True),
                tools=self.tools,
            )

    def test_git_effective_destination_content_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "effective Git worktree changed or copied inexactly",
        ):
            TOOL.materialize_repository(
                TOOL.RepositorySpec("overlay", self.source, "git", ()),
                self.root / "materialized",
                runner=GitRunner(corrupt_destination_after_copy=True),
                tools=self.tools,
            )

    def test_git_nested_dot_git_authority_is_rejected(self) -> None:
        nested = self.source / "vendor/.git"
        nested.mkdir(parents=True)
        (nested / "config").write_text("foreign\n", encoding="utf-8")

        with self.assertRaisesRegex(
            TOOL.TransactionError, "contains a nested .git authority"
        ):
            TOOL.materialize_repository(
                TOOL.RepositorySpec("overlay", self.source, "git", ()),
                self.root / "materialized",
                runner=GitRunner(),
                tools=self.tools,
            )

    def test_git_effective_tree_digest_drift_is_rejected(self) -> None:
        real_tree_manifest = TOOL.tree_manifest
        source_calls = 0

        def mismatched_tree_manifest(
            root: Path, *, ignore_names: frozenset[str] = frozenset()
        ) -> dict[str, Any]:
            nonlocal source_calls
            result = real_tree_manifest(root, ignore_names=ignore_names)
            if root == self.source and ignore_names == frozenset({".git"}):
                source_calls += 1
                if source_calls == 2:
                    result = {**result, "rows_sha256": "0" * 64}
            return result

        with (
            unittest.mock.patch.object(
                TOOL, "tree_manifest", side_effect=mismatched_tree_manifest
            ),
            self.assertRaisesRegex(
                TOOL.TransactionError,
                "effective Git worktree changed or copied inexactly",
            ),
        ):
            TOOL.materialize_repository(
                TOOL.RepositorySpec("overlay", self.source, "git", ()),
                self.root / "materialized",
                runner=GitRunner(),
                tools=self.tools,
            )

    def test_git_source_observation_drift_is_rejected_per_axis(self) -> None:
        for label in ("status", "diff", "untracked"):
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(
                    TOOL.TransactionError,
                    f"effective Git worktree {label} changed during materialization",
                ),
            ):
                TOOL.materialize_repository(
                    TOOL.RepositorySpec("overlay", self.source, "git", ()),
                    self.root / f"materialized-{label}",
                    runner=GitRunner(observation_drift_label=label),
                    tools=self.tools,
                )

    def test_git_destination_observation_mismatch_is_rejected_per_axis(self) -> None:
        for label in ("status", "diff", "untracked"):
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(
                    TOOL.TransactionError,
                    "frozen effective Git worktree differs from source",
                ),
            ):
                TOOL.materialize_repository(
                    TOOL.RepositorySpec("overlay", self.source, "git", ()),
                    self.root / f"materialized-{label}",
                    runner=GitRunner(
                        destination_observation_mismatch_label=label
                    ),
                    tools=self.tools,
                )

    def test_real_git_materialization_executes_exact_detached_clone_contract(self) -> None:
        commit = self.initialize_real_git_repository()
        observations = {
            label: self.real_git_observation(label)
            for label in ("status", "diff", "untracked")
        }
        destination = self.root / "materialized-real-git"
        result = self.materialize_real_git(destination)

        self.assert_effective_git_authority(
            result=result,
            destination=destination,
            commit=commit,
            status=observations["status"],
            diff=observations["diff"],
            untracked=observations["untracked"],
        )
        self.assertEqual(
            self.run_real_git(
                "status", "--porcelain=v1", "--untracked-files=all"
            ).stdout,
            observations["status"].replace(b"\0", b"\n"),
        )

    def test_real_git_modified_and_deleted_tracked_bytes_are_frozen(self) -> None:
        commit = self.initialize_real_git_repository()
        (self.source / "profiles/repo_name").write_text(
            "modified fixture\n", encoding="utf-8"
        )
        (self.source / "tracked-delete").unlink()
        observations = {
            label: self.real_git_observation(label)
            for label in ("status", "diff", "untracked")
        }
        self.assertNotEqual(observations["status"], b"")
        self.assertNotEqual(observations["diff"], b"")

        destination = self.root / "materialized-dirty-tracked"
        result = self.materialize_real_git(destination)

        self.assert_effective_git_authority(
            result=result,
            destination=destination,
            commit=commit,
            status=observations["status"],
            diff=observations["diff"],
            untracked=observations["untracked"],
        )
        self.assertEqual(
            (destination / "profiles/repo_name").read_text(encoding="utf-8"),
            "modified fixture\n",
        )
        self.assertFalse((destination / "tracked-delete").exists())

    def test_real_git_staged_observation_that_cannot_be_reproduced_is_rejected(
        self,
    ) -> None:
        self.initialize_real_git_repository()
        (self.source / "profiles/repo_name").write_text(
            "staged fixture\n", encoding="utf-8"
        )
        self.run_real_git("add", "profiles/repo_name")

        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "frozen effective Git worktree differs from source",
        ):
            self.materialize_real_git(self.root / "materialized-staged")

    def test_real_git_untracked_bytes_are_frozen_and_observed(self) -> None:
        commit = self.initialize_real_git_repository()
        (self.source / "local-overlay.conf").write_text(
            "untracked authority\n", encoding="utf-8"
        )
        observations = {
            label: self.real_git_observation(label)
            for label in ("status", "diff", "untracked")
        }
        self.assertNotEqual(observations["status"], b"")
        self.assertNotEqual(observations["untracked"], b"")

        destination = self.root / "materialized-untracked"
        result = self.materialize_real_git(destination)

        self.assert_effective_git_authority(
            result=result,
            destination=destination,
            commit=commit,
            status=observations["status"],
            diff=observations["diff"],
            untracked=observations["untracked"],
        )
        self.assertEqual(
            (destination / "local-overlay.conf").read_text(encoding="utf-8"),
            "untracked authority\n",
        )

    def test_real_git_ignored_bytes_are_bound_by_effective_tree(self) -> None:
        commit = self.initialize_real_git_repository()
        baseline_tree = TOOL.tree_manifest(
            self.source, ignore_names=frozenset({".git"})
        )["rows_sha256"]
        (self.source / "local.ignored").write_text(
            "ignored but effective\n", encoding="utf-8"
        )
        observations = {
            label: self.real_git_observation(label)
            for label in ("status", "diff", "untracked")
        }
        self.assertEqual(observations, {"status": b"", "diff": b"", "untracked": b""})

        destination = self.root / "materialized-ignored"
        result = self.materialize_real_git(destination)

        self.assert_effective_git_authority(
            result=result,
            destination=destination,
            commit=commit,
            status=b"",
            diff=b"",
            untracked=b"",
        )
        self.assertNotEqual(result["git"]["effective_tree_rows_sha256"], baseline_tree)
        self.assertEqual(
            (destination / "local.ignored").read_text(encoding="utf-8"),
            "ignored but effective\n",
        )


class ExecutionAndPromptContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def directory(self, name: str) -> Path:
        path = self.root / name
        path.mkdir()
        return path

    def test_authority_mounts_bind_frozen_inputs_and_private_outputs(self) -> None:
        repository_source = self.directory("repository-source")
        repository_target = self.directory("repository-target")
        config_source = self.directory("config-source")
        config_target = self.directory("config-target")
        global_source = self.directory("global-source")
        global_target = self.directory("global-target")
        private_var_lib = self.directory("private-var-lib")
        live_var_lib = self.directory("live-var-lib")
        private_edb = self.directory("private-edb")
        live_edb = self.directory("live-edb")
        live_edb_view = self.directory("live-edb-view")
        private_etc = self.directory("private-etc")
        live_etc = self.directory("live-etc")
        thinlto_cache = self.directory("thinlto-cache")
        live_thinlto_cache = self.directory("live-thinlto-cache")
        python_package = self.directory("python-package")
        distfile_authority = self.directory("distfile-authority")
        authority = {
            "repositories": [
                {
                    "materialized_location": os.fspath(repository_source),
                    "source_location": os.fspath(repository_target),
                }
            ],
            "portage_config": {
                "materialized_location": os.fspath(config_source),
                "mount_target": os.fspath(config_target),
            },
            "portage_global_config": {
                "materialized_location": os.fspath(global_source),
                "mount_target": os.fspath(global_target),
            },
            "python_modules": [{"roots": [{"path": os.fspath(python_package)}]}],
        }
        private_roots = {
            "var_lib_portage": os.fspath(private_var_lib),
            "live_var_lib_portage": os.fspath(live_var_lib),
            "cache_edb": os.fspath(private_edb),
            "live_cache_edb": os.fspath(live_edb),
            "live_cache_edb_view": os.fspath(live_edb_view),
            "etc": os.fspath(private_etc),
            "live_etc": os.fspath(live_etc),
            "distdir_authority": os.fspath(distfile_authority),
            "thinlto_cache": os.fspath(thinlto_cache),
            "live_thinlto_cache": os.fspath(live_thinlto_cache),
        }

        bindings = TOOL.authority_mount_bindings(authority, private_roots)

        self.assertEqual(len(bindings), 17)
        writable_targets = {
            binding.target for binding in bindings if not binding.read_only
        }
        self.assertEqual(
            writable_targets,
            {live_var_lib, live_edb, live_etc, live_thinlto_cache},
        )
        live_view_bindings = [
            binding for binding in bindings if binding.target == live_edb_view
        ]
        self.assertEqual(len(live_view_bindings), 1)
        self.assertTrue(live_view_bindings[0].read_only)
        self.assertTrue(
            all(
                binding.read_only
                for binding in bindings
                if binding.target not in writable_targets
            )
        )

    def test_execution_spec_digest_and_mount_targets_are_fail_closed(self) -> None:
        source = self.directory("source")
        target = self.directory("target")
        spec = TOOL.execution_spec(
            bindings=[TOOL.MountBinding(source, target, True)],
            command=["/fixture/program", "argument"],
            environment={"LC_ALL": "C"},
            network_isolated=True,
        )
        self.assertEqual(TOOL.validate_execution_spec(spec), spec)

        tampered = copy.deepcopy(spec)
        tampered["environment"]["LC_ALL"] = "hostile"
        with self.assertRaisesRegex(
            TOOL.TransactionError, "contained execution contract digest differs"
        ):
            TOOL.validate_execution_spec(tampered)

        repeated = copy.deepcopy(spec)
        repeated["mounts"].append(dict(repeated["mounts"][0]))
        unsigned = dict(repeated)
        unsigned.pop("contract_sha256")
        repeated["contract_sha256"] = TOOL.sha256_bytes(TOOL.canonical_json(unsigned))
        with self.assertRaisesRegex(
            TOOL.TransactionError, "contained execution contract repeats a mount target"
        ):
            TOOL.validate_execution_spec(repeated)

    def test_prompt_read_uses_pread_and_preserves_log_writer_offsets(self) -> None:
        plan = fixture_plan()
        prepared = {"plan": plan, "resolver": {"vdb_before": {"cpvs": []}}}
        display = (
            "\n".join(PRETEND_LINES)
            + "\n\nWould you like to merge these packages? [Yes/No] "
        ).encode()
        with tempfile.TemporaryFile("w+b") as stdout_file, tempfile.TemporaryFile(
            "w+b"
        ) as stderr_file:
            stdout_file.write(display)
            stderr_file.write(b"diagnostic-prefix")
            stdout_offset = stdout_file.tell()
            stderr_offset = stderr_file.tell()

            observed, observed_prefix = TOOL.await_exact_portage_prompt(
                process=FakePromptProcess(),
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                prepared=prepared,
                timeout=0.5,
            )

            self.assertEqual(observed, plan)
            self.assertEqual(observed_prefix, display)
            self.assertEqual(stdout_file.tell(), stdout_offset)
            self.assertEqual(stderr_file.tell(), stderr_offset)

    def test_prompt_rejects_a_different_displayed_graph_before_authorization(self) -> None:
        prepared = {
            "plan": fixture_plan(),
            "resolver": {"vdb_before": {"cpvs": []}},
        }
        display = (
            "[ebuild  N     ] dev-python/jsonschema-4.26.0::gentoo\n"
            "Would you like to merge these packages? [Yes/No] "
        ).encode()
        with tempfile.TemporaryFile("w+b") as stdout_file, tempfile.TemporaryFile(
            "w+b"
        ) as stderr_file:
            stdout_file.write(display)
            stdout_offset = stdout_file.tell()
            with self.assertRaisesRegex(
                TOOL.TransactionError,
                "exact re-pretend plan differs from the reviewed plan",
            ):
                TOOL.await_exact_portage_prompt(
                    process=FakePromptProcess(),
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                    prepared=prepared,
                    timeout=0.5,
                )
            self.assertEqual(stdout_file.tell(), stdout_offset)

    def test_prompt_reports_child_exit_without_moving_stderr_offset(self) -> None:
        prepared = {
            "plan": fixture_plan(),
            "resolver": {"vdb_before": {"cpvs": []}},
        }
        with tempfile.TemporaryFile("w+b") as stdout_file, tempfile.TemporaryFile(
            "w+b"
        ) as stderr_file:
            stderr_file.write(b"fixture child rejected action\n")
            stderr_offset = stderr_file.tell()
            with self.assertRaisesRegex(
                TOOL.TransactionError,
                "Portage action exited before its exact authorization prompt: "
                "fixture child rejected action",
            ):
                TOOL.await_exact_portage_prompt(
                    process=FakePromptProcess(65),
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                    prepared=prepared,
                    timeout=0.5,
                )
            self.assertEqual(stderr_file.tell(), stderr_offset)


class LockedAuthorityArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.paths = make_fixture_paths(self.root, "locked-artifact")
        self.initial = {
            "schema_version": 1,
            "vdb": {"schema_version": 2, "cpvs": [], "marker": "artifact-only"},
        }
        self.reference = TOOL.publish_locked_authority(self.paths, self.initial)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepared(self) -> dict[str, Any]:
        state = make_base_state(self.paths)
        state["resolver"] = {"locked_authority": self.reference}
        return state

    def test_phase_state_carries_only_exact_external_reference(self) -> None:
        state = self.prepared()
        state_path, _digest = TOOL.publish_state(self.paths, state)

        self.assertNotIn(b"artifact-only", state_path.read_bytes())
        self.assertLess(state_path.stat().st_size, TOOL.PHASE_STATE_MAX_BYTES)
        self.assertEqual(TOOL.prepared_vdb(state)["marker"], "artifact-only")

    def test_reference_rejects_missing_replaced_hardlinked_and_mode_drift(self) -> None:
        path = self.paths.locked_authority
        for mutation, pattern in (
            ("missing", "cannot open locked-authority file"),
            ("replacement", "file identity changed"),
            ("hardlink", "file identity changed"),
            ("mode", "file identity changed"),
        ):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary).resolve()
                    paths = make_fixture_paths(root, f"artifact-{mutation}")
                    reference = TOOL.publish_locked_authority(paths, self.initial)
                    target = paths.locked_authority
                    if mutation == "missing":
                        target.unlink()
                    elif mutation == "replacement":
                        replacement = target.with_name("replacement")
                        replacement.write_bytes(target.read_bytes())
                        replacement.chmod(0o600)
                        os.replace(replacement, target)
                    elif mutation == "hardlink":
                        os.link(target, target.with_name("alias"))
                    else:
                        target.chmod(0o640)
                    with self.assertRaisesRegex(TOOL.TransactionError, pattern):
                        TOOL.load_locked_authority(
                            reference, paths.transaction_id, target
                        )

    def test_reference_rejects_foreign_parent_and_oversize_claim(self) -> None:
        alternate_parent = self.root / "alternate"
        alternate_parent.mkdir()
        alternate = alternate_parent / self.paths.locked_authority.name
        shutil.copy2(self.paths.locked_authority, alternate)
        foreign = copy.deepcopy(self.reference)
        foreign["path"] = os.fspath(alternate)
        foreign["identity"] = TOOL.FileIdentity.observe(alternate).as_json()
        foreign["parent_identity"] = TOOL._stable_parent_identity(alternate_parent)
        with self.assertRaisesRegex(
            TOOL.TransactionError, "foreign transaction identity"
        ):
            TOOL.load_locked_authority(
                foreign, self.paths.transaction_id, self.paths.locked_authority
            )

        oversize = copy.deepcopy(self.reference)
        oversize["size"] = TOOL.LOCKED_AUTHORITY_MAX_BYTES + 1
        with self.assertRaisesRegex(TOOL.TransactionError, "invalid schema"):
            TOOL.load_locked_authority(
                oversize,
                self.paths.transaction_id,
                self.paths.locked_authority,
            )

    def test_live_scale_authority_above_old_16_mib_limit_round_trips(self) -> None:
        rows = [
            {
                "path": f"dev-python/pkg-{index:05d}/" + "x" * 320,
                "type": "file",
                "mode": 0o644,
                "uid": 0,
                "gid": 0,
                "nlink": 1,
                "size": 1,
                "sha256": "a" * 64,
                "xattrs": [],
            }
            for index in range(46_515)
        ]
        initial = {
            "schema_version": 1,
            "vdb": {
                "schema_version": 2,
                "cpvs": ["sys-apps/base-1"],
                "complete_tree": {"schema_version": 1, "rows": rows},
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            paths = make_fixture_paths(Path(temporary).resolve(), "live-scale")
            reference = TOOL.publish_locked_authority(paths, initial)
            self.assertGreater(reference["size"], 16 * 1024 * 1024)
            state = make_base_state(paths)
            state["resolver"] = {"locked_authority": reference}
            TOOL.publish_state(paths, state)
            loaded, _digest = TOOL.load_phase_state(paths, "prepared")
            self.assertEqual(
                TOOL.prepared_vdb(loaded)["complete_tree"]["rows"][-1]["path"],
                rows[-1]["path"],
            )


class CounterCrashAuthorityTests(unittest.TestCase):
    def _fixture(self, transaction_id: str) -> tuple[Any, dict[str, Any], Path, Path, Path]:
        root = Path(self.temporary.name).resolve() / transaction_id
        root.mkdir()
        paths = make_fixture_paths(root, transaction_id)
        paths.report.mkdir(parents=True)
        live_edb = paths.cache_edb
        private_edb = root / "private-edb"
        vdb = root / "vdb"
        for directory in (live_edb, private_edb, vdb):
            directory.mkdir(parents=True)
        (live_edb / "counter").write_bytes(b"5")
        (private_edb / "counter").write_bytes(b"7")
        prepared = make_base_state(paths)
        prepared["evidence"]["directory"] = os.fspath(paths.report)
        prepared["resolver"] = {"vdb_before": {"cpvs": ["sys-apps/base-1"]}}
        return paths, prepared, live_edb, private_edb, vdb

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _set_user_xattr(self, path: Path) -> None:
        if not hasattr(os, "setxattr"):
            if os.environ.get("GENTOO_OPT_RUN_JSONSCHEMA_HOST_CAPABILITIES") == "1":
                self.fail("authoritative host lacks the user-xattr Python API")
            self.skipTest("fixture platform lacks the user-xattr Python API")
        try:
            os.setxattr(path, "user.gentoo-opt-counter-test", b"foreign")
        except OSError as error:
            unsupported = {
                value
                for value in (
                    getattr(errno, "ENOTSUP", None),
                    getattr(errno, "EOPNOTSUPP", None),
                )
                if value is not None
            }
            if error.errno not in unsupported:
                raise
            if os.environ.get("GENTOO_OPT_RUN_JSONSCHEMA_HOST_CAPABILITIES") == "1":
                self.fail(
                    "authoritative host fixture filesystem lacks user xattrs: "
                    f"{error}"
                )
            self.skipTest(f"fixture filesystem lacks user xattrs: {error}")

    def test_every_counter_publication_fault_is_idempotently_reconciled(self) -> None:
        stages = (
            "after-intent",
            "after-create",
            "after-write",
            "after-file-fsync",
            "after-replace",
            "after-directory-fsync",
            "after-completion",
        )
        for stage in stages:
            with self.subTest(stage=stage):
                _paths, prepared, live, private, vdb = self._fixture(stage)

                def fault(observed: str) -> None:
                    if observed == stage:
                        raise RuntimeError("injected counter crash")

                with unittest.mock.patch.object(
                    TOOL,
                    "vdb_manifest",
                    return_value={"cpvs": ["sys-apps/base-1"]},
                ):
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        TOOL.reconcile_counter_locked(
                            live_edb=live,
                            private_edb=private,
                            vdb=vdb,
                            prepared=prepared,
                            outcome="rolled-back",
                            fault=fault,
                        )
                    result = TOOL.reconcile_counter_locked(
                        live_edb=live,
                        private_edb=private,
                        vdb=vdb,
                        prepared=prepared,
                        outcome="rolled-back",
                    )
                self.assertEqual(result["after"], 7)
                self.assertEqual((live / "counter").read_bytes(), b"7")
                self.assertEqual(
                    [path.name for path in live.iterdir() if path.name != "counter"],
                    [],
                )

    def test_counter_reconciliation_rejects_foreign_partial(self) -> None:
        _paths, prepared, live, private, vdb = self._fixture("foreign-partial")
        (live / ".counter.gentoo-opt.foreign.partial").write_bytes(b"7")
        with unittest.mock.patch.object(
            TOOL,
            "vdb_manifest",
            return_value={"cpvs": ["sys-apps/base-1"]},
        ):
            with self.assertRaisesRegex(TOOL.TransactionError, "foreign counter"):
                TOOL.reconcile_counter_locked(
                    live_edb=live,
                    private_edb=private,
                    vdb=vdb,
                    prepared=prepared,
                    outcome="rolled-back",
                )

    def test_counter_reconciliation_rejects_hardlinked_live_counter(self) -> None:
        paths, prepared, live, private, vdb = self._fixture("hardlinked-counter")
        alias = live / "counter-alias"
        os.link(live / "counter", alias)
        with unittest.mock.patch.object(
            TOOL,
            "vdb_manifest",
            return_value={"cpvs": ["sys-apps/base-1"]},
        ):
            with self.assertRaisesRegex(
                TOOL.TransactionError, "not a single-link regular file"
            ):
                TOOL.reconcile_counter_locked(
                    live_edb=live,
                    private_edb=private,
                    vdb=vdb,
                    prepared=prepared,
                    outcome="rolled-back",
                )
        self.assertEqual((live / "counter").read_bytes(), b"5")
        self.assertEqual(alias.read_bytes(), b"5")
        self.assertEqual(list(paths.report.iterdir()), [])

    def test_counter_reconciliation_rejects_live_counter_xattrs(self) -> None:
        paths, prepared, live, private, vdb = self._fixture("xattr-counter")
        self._set_user_xattr(live / "counter")
        with unittest.mock.patch.object(
            TOOL,
            "vdb_manifest",
            return_value={"cpvs": ["sys-apps/base-1"]},
        ):
            with self.assertRaisesRegex(
                TOOL.TransactionError, "has unreviewed extended attributes"
            ):
                TOOL.reconcile_counter_locked(
                    live_edb=live,
                    private_edb=private,
                    vdb=vdb,
                    prepared=prepared,
                    outcome="rolled-back",
                )
        self.assertEqual((live / "counter").read_bytes(), b"5")
        self.assertEqual(list(paths.report.iterdir()), [])

    def test_counter_authority_is_revalidated_after_read_only_reseal(self) -> None:
        _paths, prepared, live, private, vdb = self._fixture("reseal-xattr")
        probe = live / "xattr-probe"
        probe.write_bytes(b"probe")
        self._set_user_xattr(probe)
        os.removexattr(probe, "user.gentoo-opt-counter-test")
        probe.unlink()
        mount_state = {"read_only": True}

        def remount(_path: Path, read_only: bool) -> None:
            mount_state["read_only"] = read_only
            if read_only:
                os.setxattr(
                    live / "counter",
                    "user.gentoo-opt-counter-test",
                    b"after-publication",
                )

        with unittest.mock.patch.object(
            TOOL,
            "vdb_manifest",
            return_value={"cpvs": ["sys-apps/base-1"]},
        ):
            with self.assertRaisesRegex(
                TOOL.TransactionError,
                "resealed live EDB counter has unreviewed extended attributes",
            ):
                TOOL.reconcile_counter_with_reseal(
                    live_edb=live,
                    private_edb=private,
                    vdb=vdb,
                    prepared=prepared,
                    outcome="rolled-back",
                    remount=remount,
                    is_read_only=lambda _path: mount_state["read_only"],
                )

    def test_terminal_counter_authority_rejects_value_inode_link_and_xattr_drift(self) -> None:
        for drift in ("value", "inode", "hardlink", "xattr"):
            with self.subTest(drift=drift):
                paths, prepared, live, private, vdb = self._fixture(
                    "terminal-" + drift
                )
                mount_state = {"read_only": True}

                def remount(_path: Path, read_only: bool) -> None:
                    mount_state["read_only"] = read_only

                with unittest.mock.patch.object(
                    TOOL,
                    "vdb_manifest",
                    return_value={"cpvs": ["sys-apps/base-1"]},
                ):
                    authority = TOOL.reconcile_counter_with_reseal(
                        live_edb=live,
                        private_edb=private,
                        vdb=vdb,
                        prepared=prepared,
                        outcome="rolled-back",
                        remount=remount,
                        is_read_only=lambda _path: mount_state["read_only"],
                    )
                TOOL.validate_counter_reconciliation_authority(
                    paths=paths,
                    value=authority,
                    expected_outcome="rolled-back",
                    verify_current=True,
                )
                counter = live / "counter"
                if drift == "value":
                    counter.write_bytes(b"8")
                elif drift == "inode":
                    replacement = live / "counter-replacement"
                    replacement.write_bytes(counter.read_bytes())
                    replacement.chmod(counter.stat().st_mode & 0o7777)
                    os.replace(replacement, counter)
                elif drift == "hardlink":
                    os.link(counter, live / "counter-alias")
                else:
                    self._set_user_xattr(counter)
                with self.assertRaises(TOOL.TransactionError):
                    TOOL.validate_counter_reconciliation_authority(
                        paths=paths,
                        value=authority,
                        expected_outcome="rolled-back",
                        verify_current=True,
                    )


class RecoveryFailedAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.paths = make_fixture_paths(self.root, "recovery-failed")
        self.paths.report.mkdir(parents=True)
        package = self.paths.vdb / "sys-apps/base-1"
        package.mkdir(parents=True)
        (package / "COUNTER").write_bytes(b"1")
        self.paths.cache_edb.mkdir(parents=True)
        (self.paths.cache_edb / "counter").write_bytes(b"1")
        self.prepared = make_base_state(self.paths)
        self.prepared["evidence"]["directory"] = os.fspath(self.paths.report)
        prepared_path, self.prepared_sha = TOOL.publish_state(
            self.paths, self.prepared
        )
        self.assertEqual(prepared_path, TOOL.state_path(self.paths, "prepared"))
        child = {"control_session_sha256": "c" * 64}
        armed = TOOL.next_state(
            self.prepared,
            self.prepared_sha,
            "armed",
            child=child,
            outcome={"authorized": True},
        )
        _armed_path, armed_sha = TOOL.publish_state(self.paths, armed)
        self.rollback = TOOL.next_state(
            armed,
            armed_sha,
            "rollback-in-progress",
            child=child,
            outcome={
                "source_action_completed": False,
                "authenticated_action_complete": False,
            },
        )
        _rollback_path, self.rollback_sha = TOOL.publish_state(
            self.paths, self.rollback
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_ambiguous_armed_death_publishes_exact_stopped_remediation(self) -> None:
        failed_path, failed_sha = TOOL.publish_recovery_failed(
            paths=self.paths,
            rollback_state=self.rollback,
            rollback_sha=self.rollback_sha,
            prepared=self.prepared,
            prepared_sha=self.prepared_sha,
            reason="authenticated child completion is absent after SIGKILL",
        )
        failed = TOOL.read_json_regular(
            failed_path, "fixture recovery-failed state", TOOL.PHASE_STATE_MAX_BYTES
        )
        self.assertRegex(failed_sha, r"^[0-9a-f]{64}$")
        self.assertEqual(failed["phase"], "recovery-failed")
        TOOL.verify_recovery_failed_state(
            self.paths, failed, self.prepared_sha
        )
        evidence = TOOL.read_json_regular(
            self.paths.recovery_failure,
            "fixture recovery evidence",
            TOOL.RECOVERY_EVIDENCE_MAX_BYTES,
        )
        self.assertEqual(
            evidence["required_remediation"], TOOL.RECOVERY_FAILED_REMEDIATION
        )
        self.assertTrue(failed["outcome"]["project_must_remain_stopped"])

        tampered = copy.deepcopy(failed)
        tampered["outcome"]["reason"] = "different"
        with self.assertRaisesRegex(
            TOOL.TransactionError, "invalid authority binding"
        ):
            TOOL.verify_recovery_failed_state(
                self.paths, tampered, self.prepared_sha
            )


class PrivateRootAndPolicyClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.roots: dict[str, str] = {}
        for key in (
            "pkgdir",
            "distdir_runtime",
            "portage_tmpdir",
            "portage_logdir",
            "ccache_dir",
            "thinlto_cache",
            "cargo_home",
            "rustup_home",
            "home",
            "xdg_cache",
            "distdir_authority",
        ):
            path = self.root / key
            path.mkdir()
            self.roots[key] = os.fspath(path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_private_tmp_scaffold_and_isolated_build_residue_are_exact(self) -> None:
        prefix = Path(self.roots["portage_tmpdir"]) / "portage"
        prefix.mkdir()
        # mkdir's requested mode is filtered by the driver's deliberate 077 umask.
        prefix.chmod(0o755)
        (Path(self.roots["thinlto_cache"]) / "cache-entry").write_bytes(b"x")
        result = TOOL.private_roots_terminal_authority(
            self.roots,
            outcome="success",
            portage_identity={"uid": os.geteuid(), "gid": os.getegid()},
        )
        self.assertEqual(
            result["rows"]["portage_tmpdir"]["policy"],
            "empty-portage-prefix-only",
        )
        self.assertEqual(
            result["rows"]["thinlto_cache"]["policy"],
            "retained-build-evidence",
        )

        prefix.chmod(0o777)
        with self.assertRaisesRegex(TOOL.TransactionError, "prefix metadata"):
            TOOL.private_roots_terminal_authority(
                self.roots,
                outcome="success",
                portage_identity={"uid": os.geteuid(), "gid": os.getegid()},
            )

    def test_policy_requires_merge_sync_and_exact_reverse_unmerge_guards(self) -> None:
        environment = TOOL.plan_environment(
            {
                **self.roots,
                "distdir_staging": self.roots["distdir_runtime"],
            },
            offline=True,
        )
        features = set(environment["FEATURES"].split())
        self.assertIn("merge-sync", features)
        self.assertIn("-preserve-libs", features)
        self.assertIn("-unmerge-orphans", features)
        self.assertEqual(environment["UNINSTALL_IGNORE"], "")
        self.assertEqual(environment["CARGO_HOME"], self.roots["cargo_home"])
        self.assertEqual(environment["RUSTUP_HOME"], self.roots["rustup_home"])
        self.assertEqual(environment["PATH"], TOOL.TRANSACTION_PATH)

        settings = {
            "FEATURES": "collision-protect protect-owned sandbox userpriv "
            "usersandbox network-sandbox pid-sandbox",
            "AUTOCLEAN": "no",
            "UNINSTALL_IGNORE": "",
        }
        with self.assertRaisesRegex(TOOL.TransactionError, "missing=.*merge-sync"):
            TOOL.effective_portage_policy(settings)

    def test_exact_wrapper_native_and_backend_scope_is_bound(self) -> None:
        tools = TOOL.default_tools()
        self.assertEqual(
            tools["emerge"], Path("/usr/lib/python-exec/python3.15/emerge")
        )
        self.assertEqual(
            tools["gemato"], Path("/usr/lib/python-exec/python3.15/gemato")
        )
        self.assertEqual(
            tools["gpep517"], Path("/usr/lib/python-exec/python3.15/gpep517")
        )
        self.assertEqual(tools["meson"], Path("/usr/lib/python-exec/python3.14/meson"))
        self.assertTrue({"CPP", "NM"} <= set(TOOL.NATIVE_BUILD_COMMAND_DEFAULTS))
        scope = TOOL.build_execution_scope(
            {
                "rows": [
                    {
                        "inherited": ["cargo", "rust", "meson-r1"],
                        "pep517_backend": "maturin",
                    }
                ]
            },
            tools,
        )
        names = {row["name"] for row in scope["reviewed_tools"]}
        self.assertTrue(
            {"cargo", "rustc", "meson", "ninja", "maturin", "gpep517"}
            <= names
        )
        self.assertFalse(scope["claims_exhaustive_runtime_exec_closure"])


class PayloadAdmissionAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.report = self.root / "report"
        self.report.mkdir()
        self.mergeroot = self.root / "image"
        payload = self.mergeroot / "usr/lib/python3.15/site-packages/demo.py"
        payload.parent.mkdir(parents=True)
        payload.write_text("reviewed\n", encoding="utf-8")
        self.cpv = "dev-python/jsonschema-4.25.1"
        self.prepared = {
            "transaction_id": "payload-fixture",
            "plan": {
                "rows": [{"cpv": self.cpv}],
                "ordered_exact_atoms": [f"={self.cpv}::gentoo"],
            },
            "resolver": {
                "loader_before": {
                    "rows": [{"path": "/usr/lib"}],
                }
            },
            "evidence": {
                "directory": os.fspath(self.report),
                "proc_root": os.fspath(self.root / "proc"),
            },
        }
        self.prepared_sha = "a" * 64
        self.control = "b" * 64
        manifest = TOOL.tree_manifest(self.mergeroot)
        self.image_rows = {
            "/" + str(row["path"]): row for row in manifest["rows"]
        }
        self.prepared["resolver"]["payload_root"] = self.observation(
            Path("/usr")
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def observation(self, path: Path) -> dict[str, Any]:
        row = self.image_rows.get(os.fspath(path))
        if row is None or row.get("type") != "directory":
            return {"path": os.fspath(path), "type": "absent"}
        return {
            "path": os.fspath(path),
            "type": "directory",
            "device": 1,
            "inode": abs(hash(os.fspath(path))) + 1,
            "uid": row["uid"],
            "gid": row["gid"],
            "mode": row["mode"],
            "nlink": 2,
            "size": 0,
            "xattrs": [],
        }

    def test_fork_child_publishes_durable_python_scaffold_receipt(self) -> None:
        with unittest.mock.patch.object(
            TOOL, "object_observation", side_effect=self.observation
        ):
            pid = os.fork()
            if pid == 0:
                try:
                    TOOL.admit_merge_image_payload(
                        mergeroot=self.mergeroot,
                        cpv=self.cpv,
                        prepared=self.prepared,
                        observed_destinations={},
                        prepared_sha256=self.prepared_sha,
                        control_session=self.control,
                    )
                except BaseException:
                    os._exit(2)
                os._exit(0)
            _pid, status = os.waitpid(pid, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(os.WEXITSTATUS(status), 0)
        references = TOOL.load_payload_admission_references(
            prepared=self.prepared,
            prepared_sha256=self.prepared_sha,
            control_session=self.control,
        )
        self.assertEqual([row["cpv"] for row in references], [self.cpv])

    def test_non_usr_distinct_device_payload_is_rejected_before_observation(self) -> None:
        mergeroot = self.root / "foreign-device-image"
        payload = mergeroot / "opt/jsonschema-prerequisite/foreign.py"
        payload.parent.mkdir(parents=True)
        payload.write_text("foreign-device\n", encoding="utf-8")
        observer = unittest.mock.Mock(
            return_value={
                "path": "/opt",
                "type": "directory",
                "device": 999_999,
                "inode": 1,
                "uid": os.getuid(),
                "gid": os.getgid(),
                "mode": 0o755,
                "xattrs": [],
            }
        )
        with unittest.mock.patch.object(
            TOOL, "object_observation", observer
        ):
            with self.assertRaisesRegex(
                TOOL.TransactionError,
                "escapes the reviewed /usr durability root",
            ):
                TOOL.admit_merge_image_payload(
                    mergeroot=mergeroot,
                    cpv=self.cpv,
                    prepared=self.prepared,
                    observed_destinations={},
                    prepared_sha256=self.prepared_sha,
                    control_session=self.control,
                )
        observer.assert_not_called()
        self.assertEqual(list(self.report.iterdir()), [])

    def test_under_usr_distinct_device_payload_is_rejected_before_receipt(self) -> None:
        def split_device(path: Path) -> dict[str, Any]:
            observation = self.observation(path)
            if os.fspath(path) == "/usr/lib":
                observation = {**observation, "device": 999_999}
            return observation

        with unittest.mock.patch.object(
            TOOL, "object_observation", side_effect=split_device
        ):
            with self.assertRaisesRegex(
                TOOL.TransactionError, "different device from /usr"
            ):
                TOOL.admit_merge_image_payload(
                    mergeroot=self.mergeroot,
                    cpv=self.cpv,
                    prepared=self.prepared,
                    observed_destinations={},
                    prepared_sha256=self.prepared_sha,
                    control_session=self.control,
                )
        self.assertEqual(list(self.report.iterdir()), [])

    def test_rehashed_payload_record_cannot_introduce_non_usr_path(self) -> None:
        with unittest.mock.patch.object(
            TOOL, "object_observation", side_effect=self.observation
        ):
            reference = TOOL.admit_merge_image_payload(
                mergeroot=self.mergeroot,
                cpv=self.cpv,
                prepared=self.prepared,
                observed_destinations={},
                prepared_sha256=self.prepared_sha,
                control_session=self.control,
            )
        path = Path(reference["path"])
        record = TOOL.read_json_regular(path, "payload fixture")
        self.assertIsInstance(record, dict)
        record = cast(dict[str, Any], record)
        manifest = cast(dict[str, Any], record["manifest"])
        rows = cast(list[dict[str, Any]], manifest["rows"])
        rows[0]["path"] = "opt"
        manifest["rows_sha256"] = TOOL.sha256_bytes(TOOL.canonical_json(rows))
        record["manifest_sha256"] = TOOL.sha256_bytes(
            TOOL.canonical_json(manifest)
        )
        destinations = sorted("/" + str(row["path"]) for row in rows)
        record["destination_paths"] = destinations
        record["destination_paths_sha256"] = TOOL.sha256_bytes(
            ("\n".join(destinations) + "\n").encode()
        )
        path.write_bytes(TOOL.canonical_json(record))

        with self.assertRaisesRegex(
            TOOL.TransactionError,
            "escapes the reviewed /usr durability root",
        ):
            TOOL.validate_payload_admission_record(
                record=record,
                path=path,
                prepared=self.prepared,
                prepared_sha256=self.prepared_sha,
                control_session_sha256=TOOL.sha256_bytes(
                    self.control.encode("ascii")
                ),
            )

    def test_rehashed_payload_record_rejects_traversal_and_device_drift(self) -> None:
        with unittest.mock.patch.object(
            TOOL, "object_observation", side_effect=self.observation
        ):
            reference = TOOL.admit_merge_image_payload(
                mergeroot=self.mergeroot,
                cpv=self.cpv,
                prepared=self.prepared,
                observed_destinations={},
                prepared_sha256=self.prepared_sha,
                control_session=self.control,
            )
        path = Path(reference["path"])
        original = cast(
            dict[str, Any], TOOL.read_json_regular(path, "payload fixture")
        )

        traversal = copy.deepcopy(original)
        traversal_manifest = cast(dict[str, Any], traversal["manifest"])
        traversal_rows = cast(list[dict[str, Any]], traversal_manifest["rows"])
        traversal_rows[0]["path"] = "usr/../etc"
        traversal_manifest["rows_sha256"] = TOOL.sha256_bytes(
            TOOL.canonical_json(traversal_rows)
        )
        traversal["manifest_sha256"] = TOOL.sha256_bytes(
            TOOL.canonical_json(traversal_manifest)
        )
        traversal_destinations = sorted(
            "/" + str(row["path"]) for row in traversal_rows
        )
        traversal["destination_paths"] = traversal_destinations
        traversal["destination_paths_sha256"] = TOOL.sha256_bytes(
            ("\n".join(traversal_destinations) + "\n").encode()
        )
        with self.assertRaisesRegex(
            TOOL.TransactionError, "noncanonical relative path"
        ):
            TOOL.validate_payload_admission_record(
                record=traversal,
                path=path,
                prepared=self.prepared,
                prepared_sha256=self.prepared_sha,
                control_session_sha256=TOOL.sha256_bytes(
                    self.control.encode("ascii")
                ),
            )

        device_drift = copy.deepcopy(original)
        observations = cast(
            list[dict[str, Any]], device_drift["preexisting_destinations"]
        )
        lib = next(row for row in observations if row["path"] == "/usr/lib")
        lib["device"] = 999_999
        device_drift["preexisting_destinations_sha256"] = TOOL.sha256_bytes(
            TOOL.canonical_json(observations)
        )
        with self.assertRaisesRegex(
            TOOL.TransactionError, "different device from /usr"
        ):
            TOOL.validate_payload_admission_record(
                record=device_drift,
                path=path,
                prepared=self.prepared,
                prepared_sha256=self.prepared_sha,
                control_session_sha256=TOOL.sha256_bytes(
                    self.control.encode("ascii")
                ),
            )

    def test_success_payload_proof_detects_live_content_drift(self) -> None:
        with unittest.mock.patch.object(
            TOOL, "object_observation", side_effect=self.observation
        ):
            TOOL.admit_merge_image_payload(
                mergeroot=self.mergeroot,
                cpv=self.cpv,
                prepared=self.prepared,
                observed_destinations={},
                prepared_sha256=self.prepared_sha,
                control_session=self.control,
            )
        references = TOOL.load_payload_admission_references(
            prepared=self.prepared,
            prepared_sha256=self.prepared_sha,
            control_session=self.control,
        )
        content_path = next(
            path for path, row in self.image_rows.items() if row.get("type") == "file"
        )

        def installed(path: Path) -> dict[str, Any]:
            expected = self.image_rows.get(os.fspath(path))
            if expected is None:
                return {"path": os.fspath(path), "type": "absent"}
            if expected.get("type") == "directory":
                return self.observation(path)
            return {"path": os.fspath(path), "device": 1, **expected}

        with unittest.mock.patch.object(
            TOOL, "object_observation", side_effect=installed
        ), unittest.mock.patch.object(
            TOOL, "_contents_paths", return_value={content_path}
        ):
            authority = TOOL.verify_success_payload_authority(
                references=references,
                prepared=self.prepared,
                prepared_sha256=self.prepared_sha,
                control_session_sha256=TOOL.sha256_bytes(
                    self.control.encode("ascii")
                ),
                vdb=self.root / "vdb",
            )
            self.assertEqual(authority["cpvs"], [self.cpv])

            def drifted(path: Path) -> dict[str, Any]:
                row = installed(path)
                if os.fspath(path) == content_path:
                    row = {**row, "sha256": "f" * 64}
                return row

            with unittest.mock.patch.object(
                TOOL, "object_observation", side_effect=drifted
            ):
                with self.assertRaisesRegex(
                    TOOL.TransactionError, "installed payload sha256 differs"
                ):
                    TOOL.verify_success_payload_authority(
                        references=references,
                        prepared=self.prepared,
                        prepared_sha256=self.prepared_sha,
                        control_session_sha256=TOOL.sha256_bytes(
                            self.control.encode("ascii")
                        ),
                        vdb=self.root / "vdb",
                    )

            def device_drifted(path: Path) -> dict[str, Any]:
                row = installed(path)
                if os.fspath(path) == content_path:
                    row = {**row, "device": 999_999}
                return row

            with unittest.mock.patch.object(
                TOOL, "object_observation", side_effect=device_drifted
            ):
                with self.assertRaisesRegex(
                    TOOL.TransactionError, "installed payload object device differs"
                ):
                    TOOL.verify_success_payload_authority(
                        references=references,
                        prepared=self.prepared,
                        prepared_sha256=self.prepared_sha,
                        control_session_sha256=TOOL.sha256_bytes(
                            self.control.encode("ascii")
                        ),
                        vdb=self.root / "vdb",
                    )


class BoundedLoadedPortageLockTests(unittest.TestCase):
    class Contended(Exception):
        pass

    def _objects(self, events: list[object]) -> tuple[Any, Any]:
        class Vardb:
            _dbroot = "/fixture/vdb"
            _lock: object | None = None
            _lock_count = 0

            def unlock(self) -> None:
                events.append("vardb-unlock")
                if self._lock is None or self._lock_count != 1:
                    raise AssertionError("vardb unlock lacks exact held state")
                self._lock = None
                self._lock_count = 0

        class Registry:
            _filename = "/fixture/preserved_libs_registry"
            _lock: object | None = None

            def unlock(self) -> None:
                events.append("registry-unlock")
                if self._lock is None:
                    raise AssertionError("registry unlock lacks exact held state")
                self._lock = None

        return Vardb(), Registry()

    def test_exact_pinned_nonblocking_api_identity_is_bound(self) -> None:
        def lockdir(*_args: Any, **_kwargs: Any) -> object:
            return object()

        def lockfile(*_args: Any, **_kwargs: Any) -> object:
            return object()

        class TryAgain(Exception):
            pass

        lockdir.__module__ = "portage.locks"
        lockfile.__module__ = "portage.locks"
        TryAgain.__module__ = "portage.exception"
        authority = TOOL.portage_lock_api_authority(
            lockdir, lockfile, TryAgain
        )
        self.assertEqual(authority["lockdir_name"], "lockdir")
        self.assertEqual(authority["contention_error_name"], "TryAgain")

        lockfile.__module__ = "fixture.foreign"
        with self.assertRaisesRegex(
            TOOL.TransactionError, "API identity differs from policy"
        ):
            TOOL.portage_lock_api_authority(lockdir, lockfile, TryAgain)

    def test_lock_order_and_reverse_unlock_survive_body_error(self) -> None:
        events: list[object] = []
        vardb, registry = self._objects(events)

        def lockdir(path: str, *, flags: int) -> object:
            events.append(("vardb-lock", path, flags))
            return object()

        def lockfile(path: str, *, flags: int) -> object:
            events.append(("registry-lock", path, flags))
            return object()

        with self.assertRaisesRegex(RuntimeError, "injected held-body error"):
            with TOOL.hold_loaded_portage_locks_nonblocking(
                vardb=vardb,
                registry=registry,
                lockdir=lockdir,
                lockfile=lockfile,
                contention_error=self.Contended,
            ):
                events.append("body")
                self.assertIsNotNone(vardb._lock)
                self.assertEqual(vardb._lock_count, 1)
                self.assertIsNotNone(registry._lock)
                raise RuntimeError("injected held-body error")
        self.assertEqual(
            events,
            [
                ("vardb-lock", "/fixture/vdb", os.O_NONBLOCK),
                (
                    "registry-lock",
                    "/fixture/preserved_libs_registry",
                    os.O_NONBLOCK,
                ),
                "body",
                "registry-unlock",
                "vardb-unlock",
            ],
        )
        self.assertIsNone(vardb._lock)
        self.assertEqual(vardb._lock_count, 0)
        self.assertIsNone(registry._lock)

    def test_registry_contention_releases_vardb_before_bounded_retry(self) -> None:
        events: list[object] = []
        vardb, registry = self._objects(events)
        clock = [0.0]
        registry_attempts = 0

        def lockdir(_path: str, *, flags: int) -> object:
            self.assertEqual(flags, os.O_NONBLOCK)
            events.append("vardb-lock")
            return object()

        def lockfile(_path: str, *, flags: int) -> object:
            nonlocal registry_attempts
            self.assertEqual(flags, os.O_NONBLOCK)
            registry_attempts += 1
            events.append("registry-lock")
            if registry_attempts == 1:
                raise self.Contended()
            return object()

        def sleep(duration: float) -> None:
            events.append(("sleep", duration))
            clock[0] += duration

        with TOOL.hold_loaded_portage_locks_nonblocking(
            vardb=vardb,
            registry=registry,
            lockdir=lockdir,
            lockfile=lockfile,
            contention_error=self.Contended,
            timeout_seconds=1.0,
            retry_seconds=0.1,
            monotonic=lambda: clock[0],
            sleeper=sleep,
        ):
            events.append("body")
        self.assertEqual(
            events,
            [
                "vardb-lock",
                "registry-lock",
                "vardb-unlock",
                ("sleep", 0.1),
                "vardb-lock",
                "registry-lock",
                "body",
                "registry-unlock",
                "vardb-unlock",
            ],
        )
        self.assertIsNone(vardb._lock)
        self.assertEqual(vardb._lock_count, 0)
        self.assertIsNone(registry._lock)

    def test_contended_vardb_expires_at_exact_bound_without_leaking_state(self) -> None:
        events: list[object] = []
        vardb, registry = self._objects(events)
        clock = [10.0]

        def lockdir(_path: str, *, flags: int) -> object:
            self.assertEqual(flags, os.O_NONBLOCK)
            events.append("attempt")
            raise self.Contended()

        def sleep(duration: float) -> None:
            events.append(("sleep", duration))
            clock[0] += duration

        with self.assertRaisesRegex(
            TOOL.TransactionError, "bounded 0.2-second acquisition window"
        ):
            with TOOL.hold_loaded_portage_locks_nonblocking(
                vardb=vardb,
                registry=registry,
                lockdir=lockdir,
                lockfile=lambda *_args, **_kwargs: object(),
                contention_error=self.Contended,
                timeout_seconds=0.2,
                retry_seconds=0.05,
                monotonic=lambda: clock[0],
                sleeper=sleep,
            ):
                self.fail("contended lock unexpectedly entered held body")
        self.assertLessEqual(clock[0], 10.2 + 1e-9)
        self.assertLessEqual(events.count("attempt"), 6)
        self.assertIsNone(vardb._lock)
        self.assertEqual(vardb._lock_count, 0)
        self.assertIsNone(registry._lock)

    def test_registry_acquisition_error_releases_exact_vardb_object(self) -> None:
        events: list[object] = []
        vardb, registry = self._objects(events)

        def broken_registry(_path: str, *, flags: int) -> object:
            self.assertEqual(flags, os.O_NONBLOCK)
            raise RuntimeError("injected registry API failure")

        with self.assertRaisesRegex(RuntimeError, "registry API failure"):
            with TOOL.hold_loaded_portage_locks_nonblocking(
                vardb=vardb,
                registry=registry,
                lockdir=lambda _path, *, flags: object(),
                lockfile=broken_registry,
                contention_error=self.Contended,
            ):
                self.fail("broken registry unexpectedly entered held body")
        self.assertEqual(events, ["vardb-unlock"])
        self.assertIsNone(vardb._lock)
        self.assertEqual(vardb._lock_count, 0)
        self.assertIsNone(registry._lock)


class PreparationLockedBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.paths = make_fixture_paths(self.root, "locked-preparation")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _patches(
        self,
        events: list[str],
        *,
        vdb_values: Sequence[dict[str, Any]] | None = None,
    ) -> contextlib.ExitStack:
        vardb = object()
        vartree = type("Vartree", (), {"dbapi": vardb})()
        config = type(
            "Config",
            (),
            {
                "trees": {"/": {"vartree": vartree}},
                "target_config": type("Target", (), {"settings": {}})(),
            },
        )()
        locked = TOOL.LockedPortageAuthority(
            config=config,
            vardb=vardb,
            preserved_registry=object(),
            target="/",
            vdb_path=self.paths.vdb,
            lock_api={"fixture": "nonblocking"},
        )

        @contextlib.contextmanager
        def held(actual_paths: Any) -> Any:
            self.assertIs(actual_paths, self.paths)
            events.append("lock-enter")
            try:
                yield locked
            finally:
                events.append("lock-exit")

        def exclusion(**_kwargs: Any) -> dict[str, Any]:
            events.append("scan")
            return {"schema_version": 1, "rows": [], "rows_sha256": "0" * 64}

        stable_vdb = {
            "schema_version": 2,
            "cpvs": ["sys-apps/base-1"],
            "maximum_installed_counter": 5,
        }
        stack = contextlib.ExitStack()
        stack.enter_context(
            unittest.mock.patch.object(
                TOOL, "process_identity", return_value={"pid": os.getpid(), "start_ticks": 1}
            )
        )
        stack.enter_context(
            unittest.mock.patch.object(
                TOOL, "require_no_package_manager_activity", side_effect=exclusion
            )
        )
        stack.enter_context(
            unittest.mock.patch.object(
                TOOL, "loaded_portage_authority_lock", side_effect=held
            )
        )
        stack.enter_context(
            unittest.mock.patch.object(
                TOOL,
                "vdb_manifest",
                side_effect=list(vdb_values) if vdb_values is not None else None,
                return_value=stable_vdb,
            )
        )
        for name, value in (
            ("selected_sets_authority", {"sets": "stable"}),
            ("mtimedb_authority", {"mtimedb": "stable"}),
            ("file_observation", {"file": "stable"}),
            ("loader_directory_authority", {"rows": []}),
            ("effective_portage_policy", {"policy": "stable"}),
            ("native_toolchain_authority", {"native": "stable"}),
        ):
            stack.enter_context(unittest.mock.patch.object(TOOL, name, return_value=value))
        stack.enter_context(
            unittest.mock.patch.object(
                TOOL,
                "read_counter_authority",
                return_value=(5, {"file": "stable"}),
            )
        )
        stack.enter_context(
            unittest.mock.patch.object(
                TOOL,
                "object_observation",
                return_value={
                    "path": os.fspath(self.paths.rooted("/usr")),
                    "type": "directory",
                    "device": 1,
                },
            )
        )
        return stack

    def test_two_windows_scan_before_and_inside_lock_and_publish_under_final_lock(self) -> None:
        events: list[str] = []

        def copied(**kwargs: Any) -> dict[str, Any]:
            self.assertIs(kwargs["locked"].vardb, kwargs["locked"].config.trees["/"]["vartree"].dbapi)
            self.assertEqual(events[-1], "scan")
            events.append("copy")
            return {"schema_version": 1, "rows": {}}

        with self._patches(events), unittest.mock.patch.object(
            TOOL, "copy_live_private_authorities_locked", side_effect=copied
        ):
            initial = TOOL.preparation_locked_snapshot(
                paths=self.paths,
                private_roots={"fixture": "copy"},
                runner=object(),
                tools={},
            )
            published: list[dict[str, Any]] = []

            def publisher(value: dict[str, Any]) -> None:
                events.append("publish")
                self.assertEqual(events[-2], "scan")
                published.append(value)

            final = TOOL.preparation_locked_snapshot(
                paths=self.paths,
                private_roots=None,
                runner=None,
                tools=None,
                publisher=publisher,
            )
        self.assertEqual(len(published), 1)
        self.assertIs(published[0], final)
        self.assertEqual(events.count("scan"), 6)
        self.assertEqual(events.count("lock-enter"), 2)
        self.assertEqual(events.count("lock-exit"), 2)
        self.assertLess(events.index("copy"), events.index("lock-exit"))
        self.assertLess(events.index("publish"), len(events) - 1)
        self.assertEqual(events[-1], "lock-exit")
        self.assertEqual(initial["vdb"], final["vdb"])

    def test_held_window_drift_prevents_publication(self) -> None:
        events: list[str] = []
        before = {
            "schema_version": 2,
            "cpvs": ["sys-apps/base-1"],
            "maximum_installed_counter": 5,
        }
        after = {**before, "cpvs": ["sys-apps/base-1", "sys-apps/foreign-1"]}
        publisher = unittest.mock.Mock()
        with self._patches(events, vdb_values=(before, after)):
            with self.assertRaisesRegex(
                TOOL.TransactionError, "changed during held-lock observation"
            ):
                TOOL.preparation_locked_snapshot(
                    paths=self.paths,
                    private_roots=None,
                    runner=None,
                    tools=None,
                    publisher=publisher,
                )
        publisher.assert_not_called()

    def test_preparation_requires_exact_counter_authority_before_publication(self) -> None:
        events: list[str] = []
        publisher = unittest.mock.Mock()
        with self._patches(events), unittest.mock.patch.object(
            TOOL,
            "read_counter_authority",
            side_effect=TOOL.TransactionError(
                "live EDB counter is not a single-link regular file"
            ),
        ):
            with self.assertRaisesRegex(
                TOOL.TransactionError, "not a single-link regular file"
            ):
                TOOL.preparation_locked_snapshot(
                    paths=self.paths,
                    private_roots=None,
                    runner=None,
                    tools=None,
                    publisher=publisher,
                )
        publisher.assert_not_called()

    def test_second_window_must_match_first_before_compact_binding(self) -> None:
        initial = {
            "portage_lock_api": {"api": 1},
            "vdb": {"v": 1},
            "selected_sets": {"s": 1},
            "mtimedb": {"m": 1},
            "counter": {"c": 1},
            "live_etc": {"e": 1},
            "payload_root": {"type": "directory", "device": 1},
            "loader_directories": {"l": 1},
            "effective_portage_policy": {"p": 1},
            "native_toolchain": {"n": 1},
        }
        final = {**copy.deepcopy(initial), "plan_metadata": {"rows": []}}
        reference = {"sha256": "a" * 64}
        compact = TOOL.validate_final_locked_window(
            initial_locked_window=initial,
            final_locked_window=final,
            locked_authority=reference,
        )
        self.assertEqual(compact["locked_authority_sha256"], "a" * 64)
        for key in initial:
            with self.subTest(key=key):
                drifted = copy.deepcopy(final)
                drifted[key] = {"drift": True}
                with self.assertRaises(TOOL.TransactionError):
                    TOOL.validate_final_locked_window(
                        initial_locked_window=initial,
                        final_locked_window=drifted,
                        locked_authority=reference,
                    )


class StableLockAndProcessExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_held_lock_revalidates_exact_path_fd_and_parent_identity(self) -> None:
        parent = self.root / "locks"
        parent.mkdir(mode=0o700)
        lock = parent / "transaction.lock"
        lock.touch(mode=0o600)
        descriptor = TOOL.acquire_flock(
            lock,
            exclusive=True,
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            expected_mode=0o600,
            expected_parent_uid=os.geteuid(),
            expected_parent_gid=os.getegid(),
            expected_parent_mode=0o700,
        )
        parent_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        held = TOOL.HeldStableLocks(
            (
                TOOL.HeldStableLock(
                    path=lock,
                    descriptor=descriptor,
                    identity=TOOL.FileIdentity.from_stat(os.fstat(descriptor)),
                    parent_descriptor=parent_descriptor,
                    parent_identity=TOOL._stable_file_identity(
                        TOOL.FileIdentity.from_stat(os.fstat(parent_descriptor))
                    ),
                ),
            )
        )
        try:
            held.revalidate()
            replacement = parent / "replacement"
            replacement.touch(mode=0o600)
            os.replace(replacement, lock)
            with self.assertRaisesRegex(TOOL.TransactionError, "authority changed"):
                held.revalidate()
        finally:
            os.close(parent_descriptor)
            os.close(descriptor)

    def test_neutral_process_with_protected_vdb_handle_is_detected(self) -> None:
        proc = self.root / "proc"
        process = proc / "4321"
        (process / "fd").mkdir(parents=True)
        fields = ["S", "1", "4321", "4321", *("0" for _ in range(15)), "77"]
        (process / "stat").write_text(
            "4321 (sleep) " + " ".join(fields) + "\n", encoding="ascii"
        )
        (process / "comm").write_text("sleep\n", encoding="ascii")
        (process / "cmdline").write_bytes(b"/bin/sleep\x0010\x00")
        (process / "environ").write_bytes(b"PATH=/bin\x00")
        (process / "maps").write_text("", encoding="ascii")
        protected = self.root / "var/db/pkg"
        protected.mkdir(parents=True)
        os.symlink(protected, process / "fd/3")
        os.symlink(self.root, process / "cwd")
        os.symlink(self.root, process / "root")
        observation = TOOL.scan_package_manager_activity(
            proc_root=proc, protected_roots=(protected,)
        )
        self.assertEqual(len(observation["rows"]), 1)
        self.assertTrue(
            any(reason.startswith("fd/3:") for reason in observation["rows"][0]["reasons"])
        )

    def test_pty_master_remains_open_through_held_lock_terminal_marker(self) -> None:
        program = (
            "import json, sys\n"
            "print('GENTOO_OPT_PORTAGE_ACTION_COMPLETE ' + "
            "json.dumps({'status': 0}, sort_keys=True), flush=True)\n"
            "decision = sys.stdin.readline()\n"
            "print('GENTOO_OPT_FIXTURE_ACK ' + "
            "json.dumps({'decision': decision}, sort_keys=True), flush=True)\n"
        )
        master, slave = os.openpty()
        process: subprocess.Popen[bytes] | None = None
        try:
            with tempfile.TemporaryFile("w+b") as stdout_file, tempfile.TemporaryFile(
                "w+b"
            ) as stderr_file:
                process = subprocess.Popen(
                    [sys.executable, "-I", "-B", "-c", program],
                    stdin=slave,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
                os.close(slave)
                slave = -1
                marker, _payload = TOOL.await_json_log_marker(
                    process=process,
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                    prefix=b"GENTOO_OPT_PORTAGE_ACTION_COMPLETE ",
                    timeout=2.0,
                )
                self.assertEqual(marker, {"status": 0})
                self.assertIsNone(process.poll())
                self.assertEqual(os.write(master, b"GENTOO_OPT_ROLLBACK\n"), 20)
                acknowledgement, _payload = TOOL.await_json_log_marker(
                    process=process,
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                    prefix=b"GENTOO_OPT_FIXTURE_ACK ",
                    timeout=2.0,
                )
                self.assertEqual(
                    acknowledgement, {"decision": "GENTOO_OPT_ROLLBACK\n"}
                )
                process.wait(timeout=2.0)
                self.assertEqual(process.returncode, 0)
        finally:
            if slave >= 0:
                os.close(slave)
            with contextlib.suppress(OSError):
                os.close(master)
            if process is not None and process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2.0)


class PostEmergeAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.paths = make_fixture_paths(self.root, "post-emerge")
        for directory in (
            self.paths.vdb,
            self.paths.var_lib_portage,
            self.paths.cache_edb,
            self.paths.rooted("/etc"),
            self.paths.rooted("/usr/lib"),
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._write_package("sys-apps/base-1", 1)
        (self.paths.var_lib_portage / "preserved_libs_registry").write_text(
            "{}\n", encoding="utf-8"
        )
        (self.paths.var_lib_portage / "world").write_text("sys-apps/base\n", encoding="utf-8")
        (self.paths.cache_edb / "counter").write_bytes(b"1")
        (self.paths.cache_edb / "mtimedb").write_text("{}\n", encoding="utf-8")
        (self.paths.rooted("/etc") / "baseline.conf").write_text(
            "reviewed\n", encoding="utf-8"
        )

        private = self.root / "private"
        private.mkdir()
        self.private_roots: dict[str, str] = {}
        for key in (
            "pkgdir",
            "distdir_runtime",
            "portage_tmpdir",
            "portage_logdir",
            "ccache_dir",
            "thinlto_cache",
            "cargo_home",
            "rustup_home",
            "home",
            "xdg_cache",
            "distdir_authority",
        ):
            directory = private / key
            directory.mkdir()
            self.private_roots[key] = os.fspath(directory)
        for key, source in (
            ("var_lib_portage", self.paths.var_lib_portage),
            ("cache_edb", self.paths.cache_edb),
            ("etc", self.paths.rooted("/etc")),
        ):
            destination = private / key
            shutil.copytree(source, destination)
            self.private_roots[key] = os.fspath(destination)
        self.private_roots.update(
            {
                "live_var_lib_portage": os.fspath(self.paths.var_lib_portage),
                "live_cache_edb": os.fspath(self.paths.cache_edb),
                "live_cache_edb_view": os.fspath(self.paths.cache_edb),
                "live_etc": os.fspath(self.paths.rooted("/etc")),
                "live_thinlto_cache": self.private_roots["thinlto_cache"],
            }
        )
        copies = {
            key: self._copy_row(source, Path(self.private_roots[key]))
            for key, source in (
                ("var_lib_portage", self.paths.var_lib_portage),
                ("cache_edb", self.paths.cache_edb),
                ("etc", self.paths.rooted("/etc")),
            )
        }
        initial = {
            "schema_version": 1,
            "vdb": TOOL.vdb_manifest(self.paths.vdb),
            "selected_sets": TOOL.selected_sets_authority(self.paths),
            "mtimedb": TOOL.mtimedb_authority(self.paths.cache_edb / "mtimedb"),
            "counter": TOOL.file_observation(self.paths.cache_edb / "counter"),
            "counter_value": 1,
            "live_etc": TOOL.file_observation(self.paths.rooted("/etc")),
            "payload_root": TOOL.object_observation(
                self.paths.rooted("/usr")
            ),
            "copies": {
                "schema_version": 1,
                "rows": copies,
                "rows_sha256": TOOL.sha256_bytes(TOOL.canonical_json(copies)),
            },
            "loader_directories": TOOL.loader_directory_authority({}, self.root),
            "effective_portage_policy": {"fixture": True},
            "native_toolchain": {"fixture": True},
            "plan_metadata": None,
        }
        self.plan = fixture_plan((PRETEND_LINES[1],))
        self.prepared = make_base_state(self.paths)
        self.prepared["plan"] = self.plan
        self.prepared["private_roots"] = dict(self.private_roots)
        self.prepared["resolver"] = {
            "initial_locked_window": initial,
            "private_portage_outputs_before": TOOL.private_portage_outputs(
                self.private_roots
            ),
            "portage_build_identity": {
                "uid": os.geteuid(),
                "gid": os.getegid(),
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_package(self, cpv: str, counter: int) -> None:
        package = self.paths.vdb / cpv
        package.mkdir(parents=True)
        (package / "COUNTER").write_bytes(str(counter).encode("ascii"))
        (package / "CONTENTS").write_text("", encoding="utf-8")

    def _copy_row(self, source: Path, destination: Path) -> dict[str, Any]:
        source_value = TOOL.file_observation(source)
        destination_value = TOOL.file_observation(destination)
        self.assertEqual(source_value["tree"]["rows"], destination_value["tree"]["rows"])
        return {
            "source_root": {
                key: value
                for key, value in source_value.items()
                if key not in {"tree", "tree_sha256"}
            },
            "copy_root": {
                key: value
                for key, value in destination_value.items()
                if key not in {"tree", "tree_sha256"}
            },
            "tree": source_value["tree"],
            "tree_sha256": source_value["tree_sha256"],
        }

    def test_success_binds_private_etc_edb_mtimedb_loader_and_vdb(self) -> None:
        private_etc = Path(self.private_roots["etc"])
        private_edb = Path(self.private_roots["cache_edb"])
        (private_etc / "profile.env").write_text("export REVIEWED=1\n", encoding="utf-8")
        (private_edb / "counter").write_bytes(b"2")
        (private_edb / "mtimedb").write_text(
            '{"resume_backup":{"mergelist":[]}}\n', encoding="utf-8"
        )
        self._write_package("dev-python/jsonschema-4.25.1", 2)
        result = TOOL.verify_declared_post_emerge_authority(
            paths=self.paths,
            prepared=self.prepared,
            outcome="success",
        )
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["private_etc"]["added"], ["profile.env"])
        self.assertTrue(result["vdb"]["exact_success_delta"])
        self.assertEqual(
            result["private_mtimedb"]["stable_sha256"],
            TOOL.sha256_bytes(TOOL.canonical_json({})),
        )

        (private_etc / "foreign.conf").write_text("foreign\n", encoding="utf-8")
        with self.assertRaisesRegex(TOOL.TransactionError, "private /etc changed outside"):
            TOOL.verify_declared_post_emerge_authority(
                paths=self.paths,
                prepared=self.prepared,
                outcome="success",
            )

    def test_rollback_restores_loader_time_and_rejects_mtimedb_stable_drift(self) -> None:
        loader = self.paths.rooted("/usr/lib")
        expected_mtime = loader.lstat().st_mtime_ns
        os.utime(loader, ns=(loader.lstat().st_atime_ns, expected_mtime + 1_000_000))
        TOOL.restore_loader_directory_times(
            self.prepared["resolver"]["initial_locked_window"]["loader_directories"]
        )
        result = TOOL.verify_declared_post_emerge_authority(
            paths=self.paths,
            prepared=self.prepared,
            outcome="rolled-back",
        )
        self.assertEqual(result["outcome"], "rolled-back")
        self.assertEqual(loader.lstat().st_mtime_ns, expected_mtime)

        (Path(self.private_roots["cache_edb"]) / "mtimedb").write_text(
            '{"foreign":"drift"}\n', encoding="utf-8"
        )
        with self.assertRaisesRegex(TOOL.TransactionError, "outside resume bookkeeping"):
            TOOL.verify_declared_post_emerge_authority(
                paths=self.paths,
                prepared=self.prepared,
                outcome="rolled-back",
            )

    def test_terminal_durability_deduplicates_devices_and_binds_failure(self) -> None:
        sync = Path("/usr/bin/sync")
        self.prepared["authority"] = {"tools": TOOL.tool_manifest({"sync": sync})}

        class Runner:
            def __init__(self, status: int) -> None:
                self.status = status
                self.calls: list[tuple[str, ...]] = []

            def run(self, argv: Sequence[str], **_kwargs: Any) -> Any:
                self.calls.append(tuple(argv))
                return TOOL.CommandResult(tuple(argv), self.status, b"", b"")

        runner = Runner(0)
        barrier = TOOL.perform_terminal_durability_barrier(
            paths=self.paths,
            prepared=self.prepared,
            tools={"sync": sync},
            runner=runner,
        )
        self.assertEqual(len(runner.calls), len({row["device"] for row in barrier["rows"]}))
        TOOL.validate_terminal_durability_barrier(
            paths=self.paths, prepared=self.prepared, value=barrier
        )
        tampered = copy.deepcopy(barrier)
        tampered["rows"][0]["status"] = 1
        with self.assertRaisesRegex(TOOL.TransactionError, "row differs"):
            TOOL.validate_terminal_durability_barrier(
                paths=self.paths, prepared=self.prepared, value=tampered
            )
        with self.assertRaisesRegex(TOOL.TransactionError, "barrier failed"):
            TOOL.perform_terminal_durability_barrier(
                paths=self.paths,
                prepared=self.prepared,
                tools={"sync": sync},
                runner=Runner(1),
            )

    def test_success_vdb_rejects_preexisting_drift_and_dot_lock_residue(self) -> None:
        before = copy.deepcopy(
            self.prepared["resolver"]["initial_locked_window"]["vdb"]
        )
        self._write_package("dev-python/jsonschema-4.25.1", 2)
        after = TOOL.vdb_manifest(self.paths.vdb)
        TOOL.verify_vdb_transition(before, after, self.plan, outcome="success")

        residue = self.paths.vdb / ".jsonschema.portage_lockfile"
        residue.write_bytes(b"")
        with self.assertRaisesRegex(TOOL.TransactionError, "undeclared residue"):
            TOOL.verify_vdb_transition(
                before,
                TOOL.vdb_manifest(self.paths.vdb),
                self.plan,
                outcome="success",
            )
        residue.unlink()
        (self.paths.vdb / "sys-apps/base-1/COUNTER").write_bytes(b"9")
        with self.assertRaisesRegex(TOOL.TransactionError, "pre-existing VDB package"):
            TOOL.verify_vdb_transition(
                before,
                TOOL.vdb_manifest(self.paths.vdb),
                self.plan,
                outcome="success",
            )


class CapacityAndAttemptLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.paths = make_fixture_paths(self.root, "capacity")
        for directory in (
            self.paths.report_parent,
            self.paths.authority_parent,
            self.paths.cache_parent,
            self.paths.portage_config,
            self.paths.portage_global_config,
            self.paths.var_lib_portage,
            self.paths.cache_edb,
            self.paths.rooted("/etc"),
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.repository = self.root / "repository"
        self.repository.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_capacity_includes_external_artifact_phase_chain_and_recovery_evidence(self) -> None:
        filesystem = type("Statvfs", (), {"f_bavail": 1 << 50, "f_frsize": 1})()
        spec = TOOL.RepositorySpec("gentoo", self.repository, None, ())
        with unittest.mock.patch.object(TOOL.os, "statvfs", return_value=filesystem):
            result = TOOL.capacity_preflight(
                paths=self.paths,
                repositories=(spec,),
                fixed_authority_reserve=0,
                fixed_cache_reserve=0,
            )
        required = sum(row["required_bytes"] for row in result["rows"])
        raw_schema_floor = (
            TOOL.LOCKED_AUTHORITY_MAX_BYTES
            + TOOL.PHASE_STATE_MAX_BYTES * 4
            + TOOL.RECOVERY_EVIDENCE_MAX_BYTES * 2
        )
        self.assertGreater(required, raw_schema_floor)

        insufficient = type("Statvfs", (), {"f_bavail": 1, "f_frsize": 1})()
        with unittest.mock.patch.object(TOOL.os, "statvfs", return_value=insufficient):
            with self.assertRaisesRegex(TOOL.TransactionError, "insufficient"):
                TOOL.capacity_preflight(
                    paths=self.paths,
                    repositories=(spec,),
                    fixed_authority_reserve=0,
                    fixed_cache_reserve=0,
                )

    def test_preparation_attempt_makes_abandonment_and_id_reuse_explicit(self) -> None:
        capacity = {"schema_version": 1, "rows": [], "rows_sha256": "a" * 64}
        checkpoint = {"path": "/fixture/checkpoint", "sha256": "b" * 64}
        digest = TOOL.publish_preparation_attempt(
            self.paths, capacity=capacity, checkpoint=checkpoint
        )
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        with self.assertRaisesRegex(
            TOOL.TransactionError, "already used or has an abandoned preparation"
        ):
            TOOL.publish_preparation_attempt(
                self.paths, capacity=capacity, checkpoint=checkpoint
            )


RUN_HOST_CAPABILITIES = (
    os.environ.get("GENTOO_OPT_RUN_JSONSCHEMA_HOST_CAPABILITIES") == "1"
    or os.environ.get("GENTOO_OPT_AUTHORITATIVE") == "1"
)


@unittest.skipUnless(
    RUN_HOST_CAPABILITIES,
    "requires GENTOO_OPT_RUN_JSONSCHEMA_HOST_CAPABILITIES=1 on the authoritative Gentoo host",
)
class AuthoritativeHostCapabilityTests(unittest.TestCase):
    """Read-only/fixture host proofs; never perform a package mutation."""

    def require_root(self) -> None:
        self.assertEqual(
            os.geteuid(),
            0,
            "authoritative jsonschema host capabilities must run as root",
        )

    def test_live_tools_and_package_manager_paths_are_root_trusted(self) -> None:
        self.require_root()
        tools = TOOL.default_tools()
        # The transaction and snapshot verifier are Candidate-A bootstrap
        # payloads, not independently installed host tools.  Their root-owned
        # immutable publication is proved by the bootstrap fixture; a dirty
        # developer checkout must not be mistaken for that production
        # authority by this host-capability proof.
        bootstrap_payloads = {"transaction", "snapshot_verifier"}
        self.assertEqual(bootstrap_payloads, bootstrap_payloads & set(tools))
        host_tools = {
            name: path
            for name, path in tools.items()
            if name not in bootstrap_payloads
        }
        manifest = TOOL.tool_manifest(host_tools)
        self.assertEqual(
            [row["name"] for row in manifest["rows"]], sorted(host_tools)
        )
        for row in manifest["rows"]:
            self.assertEqual(row["uid"], 0, row["name"])
            self.assertEqual(row["gid"], 0, row["name"])
            self.assertEqual(row["mode"] & 0o022, 0, row["name"])
        for path in (
            Path("/etc/portage"),
            Path("/usr/share/portage/config"),
            Path("/var/db/pkg"),
            Path("/var/lib/portage"),
            Path("/var/cache/edb"),
        ):
            resolved = path.resolve(strict=True)
            metadata = resolved.lstat()
            self.assertTrue(stat.S_ISDIR(metadata.st_mode), resolved)
            self.assertFalse(stat.S_ISLNK(metadata.st_mode), resolved)
            self.assertEqual(metadata.st_uid, 0, resolved)
            self.assertEqual(stat.S_IMODE(metadata.st_mode) & 0o022, 0, resolved)

    def test_real_rsync_repository_clone_passes_full_recursive_gemato(self) -> None:
        self.require_root()
        repositories = [
            repository
            for repository in TOOL.discover_repositories()
            if repository.name == "gentoo"
        ]
        self.assertEqual(len(repositories), 1)
        repository = repositories[0]
        self.assertEqual(repository.sync_type, "rsync")
        self.assertIsNotNone(repository.key_path)
        production_max_age_days = repository.max_age_days
        self.assertGreater(
            production_max_age_days,
            0,
            "production rsync authority must retain a finite freshness bound",
        )
        fixture_max_age_days = 36_500
        self.assertGreater(fixture_max_age_days, production_max_age_days)

        def full_recursive_fixture_verifier(
            configured_repository: TOOL.RepositorySpec,
            clone: Path,
        ) -> dict[str, Any]:
            # Materialization must still receive the configured production
            # authority.  Only this read-only capability fixture substitutes
            # a reviewed, deliberately broad age window so a stale local
            # mirror cannot hide whether recursive Gemato verification works.
            self.assertEqual(configured_repository, repository)
            fixture_repository = TOOL.RepositorySpec(
                name=configured_repository.name,
                location=configured_repository.location,
                sync_type=configured_repository.sync_type,
                masters=configured_repository.masters,
                key_path=configured_repository.key_path,
                max_age_days=fixture_max_age_days,
            )
            fixture_repository.validate()
            return TOOL.verify_rsync_clone(fixture_repository, clone)

        parent = Path(
            os.environ.get(
                "GENTOO_OPT_JSONSCHEMA_HOST_TMPDIR",
                os.fspath(repository.location.resolve(strict=True).parent),
            )
        ).resolve(strict=True)
        with tempfile.TemporaryDirectory(
            prefix=".gentoo-opt-jsonschema-gemato.", dir=parent
        ) as temporary:
            root = Path(temporary)
            try:
                result = TOOL.materialize_repository(
                    repository,
                    root / "gentoo",
                    runner=TOOL.SubprocessRunner(),
                    tools=TOOL.default_tools(),
                    rsync_verifier=full_recursive_fixture_verifier,
                )
                self.assertTrue(result["rsync"]["full_recursive_verification"])
                self.assertEqual(
                    repository.max_age_days, production_max_age_days
                )
                TOOL.verify_manifest(
                    root / "gentoo", Path(result["tree_manifest_path"])
                )
            finally:
                make_writable(root)

    def test_real_mount_pid_network_namespace_kills_child_with_supervisor(self) -> None:
        self.require_root()
        with tempfile.TemporaryDirectory(
            prefix="gentoo-opt-jsonschema-unshare."
        ) as temporary:
            root = Path(temporary)
            receipt = root / "child-ready"
            helper = root / "namespace-child.sh"
            helper.write_text(
                "#!/bin/bash\n"
                "set -Eeuo pipefail\n"
                "printf '%s\\n' READY >\"$1\"\n"
                "trap '' TERM\n"
                "while :; do /bin/sleep 1; done\n",
                encoding="utf-8",
            )
            helper.chmod(0o700)
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen(
                    [
                        "/usr/bin/unshare",
                        "--mount",
                        "--pid",
                        "--fork",
                        "--kill-child=KILL",
                        "--mount-proc",
                        "--net",
                        "--",
                        "/bin/bash",
                        os.fspath(helper),
                        os.fspath(receipt),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    start_new_session=True,
                )
                child_pid: int | None = None
                child_identity: dict[str, int | str] | None = None
                try:
                    deadline = time.monotonic() + 10.0
                    while time.monotonic() < deadline and not receipt.is_file():
                        if process.poll() is not None:
                            break
                        time.sleep(0.02)
                    if not receipt.is_file():
                        output.seek(0)
                        self.fail(
                            "unshare containment child did not publish its outer PID: "
                            + output.read().decode("utf-8", errors="replace")
                        )
                    self.assertEqual(
                        receipt.read_text(encoding="ascii"), "READY\n"
                    )
                    children_path = (
                        Path("/proc")
                        / str(process.pid)
                        / "task"
                        / str(process.pid)
                        / "children"
                    )
                    children = children_path.read_text(encoding="ascii").split()
                    self.assertEqual(
                        len(children),
                        1,
                        f"unshare supervisor child vector is not exact: {children}",
                    )
                    child_pid = int(children[0])
                    self.assertGreater(child_pid, 1)
                    child_identity = TOOL.process_identity(child_pid)
                    self.assertIsNotNone(child_identity)
                    pidfd = os.pidfd_open(process.pid, 0)
                    try:
                        # SIGKILL proves the kill-child parent-death contract:
                        # the unshare supervisor cannot defer or handle it,
                        # and its namespace child must then receive KILL.
                        signal.pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
                    finally:
                        os.close(pidfd)
                    process.wait(timeout=10.0)
                    deadline = time.monotonic() + 5.0
                    while (
                        TOOL.process_identity(child_pid) is not None
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.02)
                    self.assertIsNone(
                        TOOL.process_identity(child_pid),
                        "kill-child namespace retained its exact outer child",
                    )
                finally:
                    if process.poll() is None:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5.0)
                    if child_pid is not None and child_identity is not None:
                        current = TOOL.process_identity(child_pid)
                        exact_keys = (
                            "pid",
                            "process_group",
                            "session",
                            "start_ticks",
                        )
                        if current is not None and all(
                            current[key] == child_identity[key] for key in exact_keys
                        ):
                            with contextlib.suppress(ProcessLookupError, OSError):
                                pidfd = os.pidfd_open(child_pid, 0)
                                try:
                                    signal.pidfd_send_signal(
                                        pidfd, signal.SIGKILL, None, 0
                                    )
                                finally:
                                    os.close(pidfd)

    def test_real_portage_lock_objects_accept_nonblocking_tokens_and_release(self) -> None:
        self.require_root()
        program = (
            "import os, subprocess, sys\n"
            "from _emerge.actions import load_emerge_config\n"
            "from portage.exception import TryAgain\n"
            "from portage.locks import lockdir, lockfile\n"
            "assert lockdir.__module__ == 'portage.locks'\n"
            "assert lockfile.__module__ == 'portage.locks'\n"
            "assert TryAgain.__module__ == 'portage.exception'\n"
            "config = load_emerge_config(action=None, args=[], "
            "opts={'--ignore-default-opts': True})\n"
            "target = config.trees._target_eroot\n"
            "vardb = config.trees[target]['vartree'].dbapi\n"
            "assert config.target_config.trees['vartree'].dbapi is vardb\n"
            "registry = vardb._plib_registry\n"
            "assert vardb._lock is None and vardb._lock_count == 0\n"
            "assert registry._lock is None\n"
            "vardb._lock = lockdir(vardb._dbroot, flags=os.O_NONBLOCK)\n"
            "vardb._lock_count = 1\n"
            "try:\n"
            "    registry._lock = lockfile(registry._filename, flags=os.O_NONBLOCK)\n"
            "    try:\n"
            "        vardb_token = vardb._lock\n"
            "        registry_token = registry._lock\n"
            "        contender = (\n"
            "            'import os, sys\\n'\n"
            "            'from portage.exception import TryAgain\\n'\n"
            "            'from portage.locks import lockdir, lockfile, unlockdir, unlockfile\\n'\n"
            "            'for name, acquire, release, path in ((\\\"vardb\\\", lockdir, unlockdir, sys.argv[1]), (\\\"registry\\\", lockfile, unlockfile, sys.argv[2])):\\n'\n"
            "            '    try:\\n'\n"
            "            '        token = acquire(path, flags=os.O_NONBLOCK)\\n'\n"
            "            '    except TryAgain:\\n'\n"
            "            '        print(\\\"CONTENDED_\\\" + name, flush=True)\\n'\n"
            "            '    else:\\n'\n"
            "            '        release(token)\\n'\n"
            "            '        raise SystemExit(\\\"unexpectedly acquired \\\" + name)\\n'\n"
            "        )\n"
            "        result = subprocess.run(\n"
            "            [sys.executable, '-I', '-B', '-c', contender, str(vardb._dbroot), str(registry._filename)],\n"
            "            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,\n"
            "            env=dict(os.environ), timeout=10, check=False,\n"
            "        )\n"
            "        assert result.returncode == 0, result.stderr.decode(errors='replace')\n"
            "        assert result.stdout.count(b'CONTENDED_') == 2, result.stdout\n"
            "        assert b'CONTENDED_vardb' in result.stdout\n"
            "        assert b'CONTENDED_registry' in result.stdout\n"
            "        assert vardb._lock is vardb_token and vardb._lock_count == 1\n"
            "        assert registry._lock is registry_token\n"
            "        print('GENTOO_OPT_VDB_REGISTRY_LOCKED=1', flush=True)\n"
            "    finally:\n"
            "        registry.unlock()\n"
            "finally:\n"
            "    vardb.unlock()\n"
            "assert vardb._lock is None and vardb._lock_count == 0\n"
            "assert registry._lock is None\n"
        )
        result = TOOL.SubprocessRunner().run(
            ["/usr/bin/python3.15", "-I", "-B", "-c", program],
            environment=TOOL.clean_environment(),
            timeout=30.0,
        )
        self.assertEqual(
            result.status,
            0,
            result.stderr.decode("utf-8", errors="replace"),
        )
        self.assertIn(b"GENTOO_OPT_VDB_REGISTRY_LOCKED=1", result.stdout)


if __name__ == "__main__":
    unittest.main()

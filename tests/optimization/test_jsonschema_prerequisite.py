#!/usr/bin/env python3
"""Hermetic tests for the jsonschema prerequisite transaction."""

from __future__ import annotations

import argparse
import copy
import contextlib
import io
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from typing import Any, Mapping, Sequence


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
    """Scripted Git authority runner with an optional final source-head drift."""

    COMMIT = "a" * 40
    DRIFTED_COMMIT = "b" * 40

    def __init__(self, *, drift_final_source_head: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.rev_parse_calls = 0
        self.drift_final_source_head = drift_final_source_head

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
        if timeout <= 0 or environment.get("GIT_CONFIG_NOSYSTEM") != "1":
            raise AssertionError("Git materializer did not use its isolated runner")
        if "rev-parse" in command:
            self.rev_parse_calls += 1
            commit = (
                self.DRIFTED_COMMIT
                if self.drift_final_source_head and self.rev_parse_calls == 3
                else self.COMMIT
            )
            return TOOL.CommandResult(command, 0, (commit + "\n").encode(), b"")
        if "status" in command:
            return TOOL.CommandResult(command, 0, b"", b"")
        if "clone" in command:
            shutil.copytree(Path(command[-2]), Path(command[-1]), symlinks=True)
            return TOOL.CommandResult(command, 0, b"", b"")
        if "checkout" in command:
            return TOOL.CommandResult(command, 0, b"", b"")
        raise AssertionError(f"unexpected scripted Git command: {command}")


class FakePromptProcess:
    def __init__(self, status: int | None = None) -> None:
        self.status = status

    def poll(self) -> int | None:
        return self.status


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

    def test_live_entrypoints_remain_fail_closed_without_lock_and_counter_handshakes(self) -> None:
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
            "home",
            "xdg_cache",
            "var_lib_portage",
            "cache_edb",
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
        for name in (
            "config",
            "preserved_libs_registry",
            "repo_revisions",
            "world",
            "world_sets",
        ):
            (root / name).write_text(f"{name}\n", encoding="utf-8")
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

    def test_git_exact_commit_is_recorded_after_independent_checkout(self) -> None:
        repository = TOOL.RepositorySpec("overlay", self.source, "git", ())
        runner = GitRunner()
        result = TOOL.materialize_repository(
            repository,
            self.root / "materialized",
            runner=runner,
            tools=self.tools,
        )

        self.assertEqual(result["git"], {"commit": GitRunner.COMMIT, "clean": True})
        self.assertEqual(runner.rev_parse_calls, 3)
        TOOL.verify_manifest(
            self.root / "materialized", Path(result["tree_manifest_path"])
        )

    def test_real_git_materialization_executes_exact_detached_clone_contract(self) -> None:
        git = Path("/usr/bin/git")
        if not git.is_file():
            self.skipTest("portable real-Git materialization requires /usr/bin/git")
        environment = {
            **TOOL.git_environment(),
            "HOME": os.fspath(self.root / "empty-home"),
        }
        (self.root / "empty-home").mkdir(mode=0o700)

        def run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                [os.fspath(git), *arguments],
                cwd=self.source,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=10.0,
            )

        run_git("init", "--quiet")
        run_git("add", "--all")
        run_git(
            "-c",
            "user.name=Gentoo optimization fixture",
            "-c",
            "user.email=fixture.invalid@example.invalid",
            "commit",
            "--quiet",
            "--message=fixture authority",
        )
        commit = run_git("rev-parse", "--verify", "HEAD^{commit}").stdout.decode(
            "ascii"
        ).strip()

        destination = self.root / "materialized-real-git"
        result = TOOL.materialize_repository(
            TOOL.RepositorySpec("overlay", self.source, "git", ()),
            destination,
            runner=TOOL.SubprocessRunner(),
            tools=self.tools,
        )

        self.assertEqual(result["git"], {"commit": commit, "clean": True})
        self.assertEqual(
            run_git("status", "--porcelain=v1", "--untracked-files=all").stdout,
            b"",
        )
        TOOL.verify_manifest(destination, Path(result["tree_manifest_path"]))


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
            "distdir_authority": os.fspath(distfile_authority),
        }

        bindings = TOOL.authority_mount_bindings(authority, private_roots)

        self.assertEqual(len(bindings), 11)
        writable_targets = {
            binding.target for binding in bindings if not binding.read_only
        }
        self.assertEqual(writable_targets, {live_var_lib, live_edb})
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
        manifest = TOOL.tool_manifest(tools)
        self.assertEqual(
            [row["name"] for row in manifest["rows"]], sorted(tools)
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
            TOOL.validate_trusted_directory(resolved, 0, 0)

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
                )
                self.assertTrue(result["rsync"]["full_recursive_verification"])
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
                        signal.pidfd_send_signal(pidfd, signal.SIGTERM, None, 0)
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

    def test_real_portage_vardb_object_can_be_locked_and_released(self) -> None:
        self.require_root()
        program = (
            "from _emerge.actions import load_emerge_config\n"
            "config = load_emerge_config(action=None, args=[], "
            "opts={'--ignore-default-opts': True})\n"
            "target = config.trees._target_eroot\n"
            "vardb = config.trees[target]['vartree'].dbapi\n"
            "assert config.target_config.trees['vartree'].dbapi is vardb\n"
            "vardb.lock()\n"
            "try:\n"
            "    print('GENTOO_OPT_VDB_LOCKED=1', flush=True)\n"
            "finally:\n"
            "    vardb.unlock()\n"
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
        self.assertIn(b"GENTOO_OPT_VDB_LOCKED=1", result.stdout)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Bound and reap one checkpoint-fixture command in a dedicated process.

This helper deliberately owns every raw fork and PR_SET_CHILD_SUBREAPER call.
The unittest process remains an ordinary process, while this single-threaded
helper adopts and removes fixture descendants before publishing its receipt.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import select
import signal
import sys
import time
from typing import NamedTuple


PR_SET_PDEATHSIG = 1
PR_SET_CHILD_SUBREAPER = 36
NATURAL_EXIT_SECONDS = 3.0
TERM_SECONDS = 3.0
KILL_SECONDS = 3.0
TARGET_READY_SECONDS = 3.0
TERMINAL_STATES = {"Z", "X", "x"}
INTERRUPTION_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


class ProcessIdentity(NamedTuple):
    pid: int
    ppid: int
    process_group: int
    session: int
    state: str
    start_time: int


def read_process_identity(pid: int) -> ProcessIdentity | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    delimiter = text.rfind(") ")
    if delimiter < 0:
        return None
    fields = text[delimiter + 2 :].split()
    if len(fields) < 20:
        return None
    try:
        return ProcessIdentity(
            pid=pid,
            ppid=int(fields[1]),
            process_group=int(fields[2]),
            session=int(fields[3]),
            state=fields[0],
            start_time=int(fields[19]),
        )
    except ValueError:
        return None


def identity_is_live(identity: ProcessIdentity) -> bool:
    current = read_process_identity(identity.pid)
    return (
        current is not None
        and current.start_time == identity.start_time
        and current.state not in TERMINAL_STATES
    )


def snapshot_descendants(root_pid: int) -> dict[tuple[int, int], ProcessIdentity]:
    identities: dict[int, ProcessIdentity] = {}
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError:
        return {}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        identity = read_process_identity(int(entry.name))
        if identity is not None:
            identities[identity.pid] = identity

    descendants: dict[tuple[int, int], ProcessIdentity] = {}
    frontier = {root_pid}
    while frontier:
        children = {
            identity.pid
            for identity in identities.values()
            if identity.ppid in frontier and identity.pid != root_pid
        }
        children -= {identity.pid for identity in descendants.values()}
        if not children:
            break
        for pid in children:
            identity = identities[pid]
            descendants[(identity.pid, identity.start_time)] = identity
        frontier = children
    return descendants


def set_prctl(option: int, argument: int, label: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, argument, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, f"{label}: {os.strerror(error_number)}")


def identity_payload(identity: ProcessIdentity) -> dict[str, int | str]:
    return {
        "pid": identity.pid,
        "ppid": identity.ppid,
        "process_group": identity.process_group,
        "session": identity.session,
        "state": identity.state,
        "start_time": identity.start_time,
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(path.name + f".partial.{os.getpid()}")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def signal_identities(
    identities: dict[tuple[int, int], ProcessIdentity],
    signum: signal.Signals,
) -> None:
    """Signal exact fixture descendants without touching the caller's group."""
    protected_group = os.getpgrp()
    groups = sorted(
        {
            current.process_group
            for identity in identities.values()
            if (current := read_process_identity(identity.pid)) is not None
            and current.start_time == identity.start_time
            and current.state not in TERMINAL_STATES
            and current.process_group > 0
            and current.process_group != protected_group
        }
    )
    for process_group in groups:
        if not any(
            (current := read_process_identity(identity.pid)) is not None
            and current.start_time == identity.start_time
            and current.state not in TERMINAL_STATES
            and current.process_group == process_group
            for identity in identities.values()
        ):
            continue
        try:
            os.killpg(process_group, signum)
        except ProcessLookupError:
            pass

    for identity in identities.values():
        current = read_process_identity(identity.pid)
        if (
            current is None
            or current.start_time != identity.start_time
            or current.state in TERMINAL_STATES
        ):
            continue
        try:
            os.kill(identity.pid, signum)
        except ProcessLookupError:
            pass


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-parent-pid", type=int, required=True)
    parser.add_argument("--expected-parent-start", type=int, required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--stdout", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--fixture-hang-after-ready", action="store_true")
    parser.add_argument("--fixture-interrupt-before-fork", type=Path)
    parser.add_argument("--fixture-interrupt-before-release", type=Path)
    parser.add_argument("--fixture-interrupt-after-release-mask", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if arguments.command[:1] == ["--"]:
        arguments.command = arguments.command[1:]
    if not arguments.command:
        parser.error("a command is required after --")
    if arguments.timeout <= 0:
        parser.error("--timeout must be positive")
    if (
        int(arguments.fixture_interrupt_before_fork is not None)
        + int(arguments.fixture_interrupt_before_release is not None)
        + int(arguments.fixture_interrupt_after_release_mask is not None)
        > 1
    ):
        parser.error("only one fixture interruption point may be selected")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    target_pid: int | None = None
    target_start_time: int | None = None
    target_identity: ProcessIdentity | None = None
    target_returncode: int | None = None
    timed_out = False
    interruption_signal: int | None = None
    residual_before_cleanup: dict[tuple[int, int], ProcessIdentity] = {}
    reaped_pids: list[int] = []
    survivors: dict[tuple[int, int], ProcessIdentity] = {}
    release_writer: int | None = None
    release_committed = False

    def close_release_writer() -> None:
        nonlocal release_writer
        descriptor = release_writer
        release_writer = None
        if descriptor is None:
            return
        try:
            os.close(descriptor)
        except OSError:
            pass

    def note_interruption(signum: int, _frame: object) -> None:
        nonlocal interruption_signal
        if interruption_signal is None:
            interruption_signal = signum
        if not release_committed:
            close_release_writer()

    def reject_interruption(stage: str) -> None:
        if interruption_signal is not None:
            raise InterruptedError(
                "checkpoint fixture supervisor was interrupted by signal "
                f"{interruption_signal} {stage}"
            )

    def wait_for_fixture_interruption(path: Path, stage: str) -> None:
        supervisor = read_process_identity(os.getpid())
        if supervisor is None:
            raise RuntimeError("cannot bind fixture interruption barrier")
        write_json(
            path,
            {
                "schema_version": 1,
                "stage": stage,
                "supervisor": identity_payload(supervisor),
            },
        )
        deadline = time.monotonic() + TARGET_READY_SECONDS
        while interruption_signal is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if interruption_signal is None:
            raise RuntimeError(
                f"fixture interruption barrier expired before signal at {stage}"
            )

    def wait_for_fixture_pending_interruption(path: Path, stage: str) -> None:
        supervisor = read_process_identity(os.getpid())
        if supervisor is None:
            raise RuntimeError("cannot bind fixture pending-interruption barrier")
        write_json(
            path,
            {
                "schema_version": 1,
                "stage": stage,
                "supervisor": identity_payload(supervisor),
            },
        )
        deadline = time.monotonic() + TARGET_READY_SECONDS
        while (
            not set(signal.sigpending()).intersection(INTERRUPTION_SIGNALS)
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        if not set(signal.sigpending()).intersection(INTERRUPTION_SIGNALS):
            raise RuntimeError(
                f"fixture pending-interruption barrier expired before signal at {stage}"
            )

    def release_target() -> None:
        """Release once, with the pending-signal check as the linearization point."""
        nonlocal interruption_signal, release_committed
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, INTERRUPTION_SIGNALS)
        try:
            if arguments.fixture_interrupt_after_release_mask is not None:
                wait_for_fixture_pending_interruption(
                    arguments.fixture_interrupt_after_release_mask,
                    "after-release-mask",
                )
            pending = set(signal.sigpending()).intersection(INTERRUPTION_SIGNALS)
            if interruption_signal is None and pending:
                interruption_signal = int(min(pending, key=int))
            reject_interruption("before target release")
            descriptor = release_writer
            if descriptor is None:
                raise RuntimeError("target release descriptor was closed before release")
            # A cancellation observed before this assignment rejects release.
            # Signals arriving after the pending-set observation are ordered
            # after this commitment and drive normal post-release teardown.
            release_committed = True
            if os.write(descriptor, b"R") != 1:
                raise RuntimeError("target exec release barrier write was incomplete")
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def reap_available() -> None:
        nonlocal target_returncode
        while True:
            try:
                pid, wait_status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return
            reaped_pids.append(pid)
            if pid == target_pid:
                target_returncode = os.waitstatus_to_exitcode(wait_status)

    def current_descendants() -> dict[tuple[int, int], ProcessIdentity]:
        return {
            key: identity
            for key, identity in snapshot_descendants(os.getpid()).items()
            if identity.state not in TERMINAL_STATES
        }

    def drain_with_signal(
        requested_signal: signal.Signals,
        seconds: float,
    ) -> dict[tuple[int, int], ProcessIdentity]:
        signalled: set[tuple[int, int]] = set()
        deadline = time.monotonic() + seconds
        remaining = current_descendants()
        while remaining and time.monotonic() < deadline:
            newly_observed = {
                key: identity
                for key, identity in remaining.items()
                if key not in signalled
            }
            if newly_observed:
                signal_identities(newly_observed, requested_signal)
                signalled.update(newly_observed)
            reap_available()
            time.sleep(0.02)
            remaining = current_descendants()
        reap_available()
        return current_descendants()

    try:
        for signum in INTERRUPTION_SIGNALS:
            signal.signal(signum, note_interruption)
        parent = read_process_identity(arguments.expected_parent_pid)
        if parent is None or parent.start_time != arguments.expected_parent_start:
            raise RuntimeError("fixture parent changed before supervisor binding")
        set_prctl(PR_SET_CHILD_SUBREAPER, 1, "checkpoint fixture PR_SET_CHILD_SUBREAPER")
        set_prctl(PR_SET_PDEATHSIG, signal.SIGTERM, "checkpoint supervisor PR_SET_PDEATHSIG")
        parent = read_process_identity(arguments.expected_parent_pid)
        if (
            os.getppid() != arguments.expected_parent_pid
            or parent is None
            or parent.start_time != arguments.expected_parent_start
        ):
            raise RuntimeError("fixture parent changed during supervisor binding")

        environment_payload = json.loads(arguments.environment.read_text(encoding="utf-8"))
        if not isinstance(environment_payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in environment_payload.items()
        ):
            raise RuntimeError("fixture environment is not a string mapping")

        if arguments.fixture_interrupt_before_fork is not None:
            wait_for_fixture_interruption(
                arguments.fixture_interrupt_before_fork,
                "before-target-creation",
            )
        reject_interruption("before target creation")

        stdout_descriptor = os.open(
            arguments.stdout,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        stderr_descriptor = os.open(
            arguments.stderr,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        ready_reader, ready_writer = os.pipe2(os.O_CLOEXEC)
        release_reader, release_writer = os.pipe2(os.O_CLOEXEC)
        supervisor_pid = os.getpid()
        reject_interruption("before target creation")
        target_pid = os.fork()
        if target_pid == 0:
            try:
                for signum in INTERRUPTION_SIGNALS:
                    signal.signal(signum, signal.SIG_DFL)
                os.close(ready_reader)
                if release_writer is None:
                    raise RuntimeError("target inherited no release descriptor")
                os.close(release_writer)
                os.setsid()
                if os.getppid() != supervisor_pid:
                    raise RuntimeError("target supervisor changed before parent-death binding")
                set_prctl(PR_SET_PDEATHSIG, signal.SIGKILL, "target PR_SET_PDEATHSIG")
                if os.getppid() != supervisor_pid:
                    raise RuntimeError("target supervisor changed during parent-death binding")
                null_descriptor = os.open(
                    "/dev/null", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                )
                os.dup2(null_descriptor, 0)
                os.dup2(stdout_descriptor, 1)
                os.dup2(stderr_descriptor, 2)
                for descriptor in (null_descriptor, stdout_descriptor, stderr_descriptor):
                    if descriptor > 2:
                        os.close(descriptor)
                os.chdir(arguments.cwd)
                if os.write(ready_writer, b"R") != 1:
                    raise RuntimeError("target readiness barrier write was incomplete")
                os.close(ready_writer)
                if os.read(release_reader, 1) != b"R":
                    raise RuntimeError("target exec release barrier was not satisfied")
                os.close(release_reader)
                os.execvpe(arguments.command[0], arguments.command, environment_payload)
            except BaseException as error:
                try:
                    os.write(2, f"fixture target exec failed: {error!r}\n".encode())
                finally:
                    os._exit(127)

        os.close(ready_writer)
        os.close(release_reader)
        os.close(stdout_descriptor)
        os.close(stderr_descriptor)
        try:
            readable, _, _ = select.select([ready_reader], [], [], TARGET_READY_SECONDS)
            if not readable or os.read(ready_reader, 1) != b"R":
                raise RuntimeError("target did not satisfy its bounded readiness barrier")
            target_identity = read_process_identity(target_pid)
            if target_identity is None:
                raise RuntimeError("target disappeared before supervisor identity capture")
            if (
                target_identity.ppid != supervisor_pid
                or target_identity.process_group != target_pid
                or target_identity.session != target_pid
            ):
                raise RuntimeError(
                    "target did not enter its exact private session: "
                    f"{target_identity!r}"
                )
            target_start_time = target_identity.start_time
            supervisor_identity = read_process_identity(os.getpid())
            if supervisor_identity is None:
                raise RuntimeError("cannot read supervisor identity for readiness receipt")
            write_json(
                arguments.ready,
                {
                    "schema_version": 1,
                    "supervisor": identity_payload(supervisor_identity),
                    "target": identity_payload(target_identity),
                },
            )
            if arguments.fixture_interrupt_before_release is not None:
                wait_for_fixture_interruption(
                    arguments.fixture_interrupt_before_release,
                    "before-target-release",
                )
            release_target()
        finally:
            os.close(ready_reader)
            close_release_writer()

        if arguments.fixture_hang_after_ready:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            time.sleep(300)

        target_deadline = time.monotonic() + arguments.timeout
        while (
            target_returncode is None
            and interruption_signal is None
            and time.monotonic() < target_deadline
        ):
            try:
                waited_pid, wait_status = os.waitpid(target_pid, os.WNOHANG)
            except ChildProcessError as error:
                raise RuntimeError("target was reaped outside its supervisor") from error
            if waited_pid == target_pid:
                reaped_pids.append(waited_pid)
                target_returncode = os.waitstatus_to_exitcode(wait_status)
                break
            time.sleep(0.02)

        if target_returncode is None and interruption_signal is None:
            timed_out = True
            residual_before_cleanup = current_descendants()
        elif interruption_signal is not None:
            residual_before_cleanup = current_descendants()
        else:
            natural_deadline = time.monotonic() + NATURAL_EXIT_SECONDS
            while time.monotonic() < natural_deadline:
                reap_available()
                residual_before_cleanup = current_descendants()
                if not residual_before_cleanup:
                    break
                time.sleep(0.02)

        survivors = current_descendants()
        if survivors:
            survivors = drain_with_signal(signal.SIGTERM, TERM_SECONDS)
        if survivors:
            survivors = drain_with_signal(signal.SIGKILL, KILL_SECONDS)
        reap_available()
        survivors = current_descendants()
        supervisor_identity = read_process_identity(os.getpid())
        if supervisor_identity is None:
            raise RuntimeError("cannot read supervisor identity for receipt")
        write_json(
            arguments.receipt,
            {
                "cleanup_survivors": [
                    identity_payload(identity) for identity in sorted(survivors.values())
                ],
                "interruption_signal": interruption_signal,
                "reaped_pids": sorted(set(reaped_pids)),
                "residual_before_cleanup": [
                    identity_payload(identity)
                    for identity in sorted(residual_before_cleanup.values())
                ],
                "schema_version": 4,
                "supervisor": identity_payload(supervisor_identity),
                "target": identity_payload(target_identity),
                "target_release_committed": release_committed,
                "target_returncode": target_returncode,
                "timed_out": timed_out,
            },
        )
        return 128 + interruption_signal if interruption_signal is not None else 0
    except BaseException as error:
        survivors = current_descendants()
        if survivors:
            survivors = drain_with_signal(signal.SIGTERM, TERM_SECONDS)
        if survivors:
            survivors = drain_with_signal(signal.SIGKILL, KILL_SECONDS)
        reap_available()
        write_json(
            arguments.receipt,
            {
                "cleanup_survivors": [
                    identity_payload(identity)
                    for identity in sorted(survivors.values())
                ],
                "error": f"{type(error).__name__}: {error}",
                "interruption_signal": interruption_signal,
                "reaped_pids": sorted(set(reaped_pids)),
                "schema_version": 4,
                "target": (
                    identity_payload(target_identity)
                    if target_identity is not None
                    else None
                ),
                "target_release_committed": release_committed,
                "target_returncode": target_returncode,
                "timed_out": timed_out,
            },
        )
        return 128 + interruption_signal if interruption_signal is not None else 70


if __name__ == "__main__":
    raise SystemExit(main())

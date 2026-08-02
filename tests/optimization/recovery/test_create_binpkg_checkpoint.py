from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from typing import BinaryIO, NamedTuple


REPOSITORY = Path(__file__).resolve().parents[3]
CHECKPOINT = (
    REPOSITORY
    / "scripts"
    / "optimization"
    / "recovery"
    / "create-binpkg-checkpoint.sh"
)
PROCESS_SUPERVISOR = Path(__file__).with_name("checkpoint_process_supervisor.py")

TOOLS = (
    "bash",
    "chmod",
    "chown",
    "cmp",
    "cp",
    "date",
    "env",
    "emerge",
    "find",
    "findmnt",
    "flock",
    "getent",
    "install",
    "jq",
    "ln",
    "mount",
    "qcheck",
    "readlink",
    "rm",
    "setsid",
    "sha256sum",
    "sleep",
    "sort",
    "stat",
    "sync",
    "timeout",
    "umount",
    "unshare",
    "zstd",
)


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


def process_identity_is_current(identity: ProcessIdentity) -> bool:
    current = read_process_identity(identity.pid)
    return current is not None and current.start_time == identity.start_time


def process_identity_is_live(identity: ProcessIdentity) -> bool:
    current = read_process_identity(identity.pid)
    return (
        current is not None
        and current.start_time == identity.start_time
        and current.state not in {"Z", "X", "x"}
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


def read_capture(stream: BinaryIO) -> str:
    stream.flush()
    stream.seek(0)
    return stream.read().decode("utf-8", errors="replace")


def signal_observed_processes(
    root: ProcessIdentity,
    observed: dict[tuple[int, int], ProcessIdentity],
    signum: signal.Signals,
) -> None:
    identities = {**observed, (root.pid, root.start_time): root}
    groups = sorted(
        {
            identity.process_group
            for identity in identities.values()
            if identity.process_group > 0 and process_identity_is_live(identity)
        }
    )
    for process_group in groups:
        group_is_current = False
        for identity in identities.values():
            current = read_process_identity(identity.pid)
            if (
                current is not None
                and current.start_time == identity.start_time
                and current.state not in {"Z", "X", "x"}
                and current.process_group == process_group
            ):
                group_is_current = True
                break
        if not group_is_current:
            continue
        try:
            os.killpg(process_group, signum)
        except ProcessLookupError:
            pass

    # A descendant may have moved into a group whose leader disappeared between
    # observation and signalling.  This numeric fallback is confined to private
    # fixture descendant sessions and is not evidence for production pidfd
    # containment; the authoritative host probes exercise that separately.
    for identity in identities.values():
        if not process_identity_is_live(identity):
            continue
        try:
            os.kill(identity.pid, signum)
        except ProcessLookupError:
            pass


def surviving_processes(
    identities: dict[tuple[int, int], ProcessIdentity],
) -> list[ProcessIdentity]:
    return [identity for identity in identities.values() if process_identity_is_live(identity)]


def live_session_processes(session: int) -> list[ProcessIdentity]:
    processes: list[ProcessIdentity] = []
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError:
        return processes
    for entry in entries:
        if not entry.name.isdigit():
            continue
        identity = read_process_identity(int(entry.name))
        if (
            identity is not None
            and identity.session == session
            and identity.state not in {"Z", "X", "x"}
        ):
            processes.append(identity)
    return sorted(processes, key=lambda item: (item.pid, item.start_time))


def wait_process_identity_stopped(identity: ProcessIdentity, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while process_identity_is_live(identity) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not process_identity_is_live(identity)


def terminate_process_tree(
    process: subprocess.Popen[bytes],
    root: ProcessIdentity,
    observed: dict[tuple[int, int], ProcessIdentity],
    *,
    term_seconds: float = 3.0,
    kill_seconds: float = 3.0,
) -> list[ProcessIdentity]:
    observed.update(snapshot_descendants(root.pid))
    signal_observed_processes(root, observed, signal.SIGTERM)
    deadline = time.monotonic() + term_seconds
    while time.monotonic() < deadline:
        observed.update(snapshot_descendants(root.pid))
        if process.poll() is not None and not surviving_processes(observed):
            break
        time.sleep(0.02)

    observed.update(snapshot_descendants(root.pid))
    if process.poll() is None or surviving_processes(observed):
        signal_observed_processes(root, observed, signal.SIGKILL)
    try:
        process.wait(timeout=kill_seconds)
    except subprocess.TimeoutExpired:
        # The direct child is the private session leader and must not escape a
        # bounded test even if its signal handlers are broken.
        if process_identity_is_current(root):
            try:
                os.kill(root.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=kill_seconds)

    deadline = time.monotonic() + kill_seconds
    survivors = surviving_processes(observed)
    while survivors and time.monotonic() < deadline:
        time.sleep(0.02)
        survivors = surviving_processes(observed)
    return survivors


SUPERVISOR_NATURAL_EXIT_SECONDS = 3.0
SUPERVISOR_TERM_SECONDS = 3.0
SUPERVISOR_KILL_SECONDS = 3.0

IDENTITY_PAYLOAD_KEYS = {
    "pid",
    "ppid",
    "process_group",
    "session",
    "state",
    "start_time",
}
SUCCESS_RECEIPT_KEYS = {
    "cleanup_survivors",
    "interruption_signal",
    "reaped_pids",
    "residual_before_cleanup",
    "schema_version",
    "supervisor",
    "target",
    "target_release_committed",
    "target_returncode",
    "timed_out",
}


def require_exact_integer(value: object, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError(f"{label} is not an exact integer: {value!r}")
    if positive and value <= 0:
        raise AssertionError(f"{label} is not positive: {value!r}")
    return value


def parse_identity_payload(payload: object, label: str) -> ProcessIdentity:
    if not isinstance(payload, dict) or set(payload) != IDENTITY_PAYLOAD_KEYS:
        raise AssertionError(f"{label} has the wrong identity schema: {payload!r}")
    state = payload["state"]
    if not isinstance(state, str) or len(state) != 1:
        raise AssertionError(f"{label} has an invalid process state: {state!r}")
    return ProcessIdentity(
        pid=require_exact_integer(payload["pid"], f"{label}.pid", positive=True),
        ppid=require_exact_integer(payload["ppid"], f"{label}.ppid", positive=True),
        process_group=require_exact_integer(
            payload["process_group"], f"{label}.process_group", positive=True
        ),
        session=require_exact_integer(
            payload["session"], f"{label}.session", positive=True
        ),
        state=state,
        start_time=require_exact_integer(
            payload["start_time"], f"{label}.start_time", positive=True
        ),
    )


def parse_identity_list(payload: object, label: str) -> list[ProcessIdentity]:
    if not isinstance(payload, list):
        raise AssertionError(f"{label} is not a list: {payload!r}")
    return [
        parse_identity_payload(item, f"{label}[{index}]")
        for index, item in enumerate(payload)
    ]


def validate_supervisor_ready_receipt(
    payload: object,
    *,
    helper_identity: ProcessIdentity,
    parent_identity: ProcessIdentity,
) -> tuple[ProcessIdentity, ProcessIdentity]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "supervisor",
        "target",
    }:
        raise AssertionError(f"checkpoint supervisor readiness has the wrong schema: {payload!r}")
    if require_exact_integer(payload["schema_version"], "readiness.schema_version") != 1:
        raise AssertionError(f"checkpoint supervisor readiness has the wrong version: {payload!r}")
    supervisor = parse_identity_payload(payload["supervisor"], "readiness.supervisor")
    target = parse_identity_payload(payload["target"], "readiness.target")
    if (
        supervisor.pid != helper_identity.pid
        or supervisor.start_time != helper_identity.start_time
        or supervisor.ppid != parent_identity.pid
        or supervisor.process_group != parent_identity.process_group
        or supervisor.session != parent_identity.session
    ):
        raise AssertionError(
            "checkpoint supervisor readiness does not bind the exact helper: "
            f"parent={parent_identity!r} helper={helper_identity!r} "
            f"receipt={supervisor!r}"
        )
    if (
        target.ppid != supervisor.pid
        or target.process_group != target.pid
        or target.session != target.pid
        or target.state in {"Z", "X", "x"}
    ):
        raise AssertionError(
            "checkpoint supervisor readiness does not bind an exact private target: "
            f"{target!r}"
        )
    return supervisor, target


def validate_success_supervisor_receipt(
    payload: object,
    *,
    helper_status: int,
    helper_identity: ProcessIdentity,
    parent_identity: ProcessIdentity,
    readiness_payload: object,
    identity_reader=read_process_identity,
) -> tuple[int, bool, list[ProcessIdentity]]:
    if helper_status != 0:
        raise AssertionError(f"checkpoint process supervisor failed with status {helper_status}")
    if not isinstance(payload, dict) or set(payload) != SUCCESS_RECEIPT_KEYS:
        raise AssertionError(f"checkpoint supervisor receipt has the wrong schema: {payload!r}")
    if require_exact_integer(payload["schema_version"], "receipt.schema_version") != 4:
        raise AssertionError(f"checkpoint supervisor receipt has the wrong version: {payload!r}")
    ready_supervisor, ready_target = validate_supervisor_ready_receipt(
        readiness_payload,
        helper_identity=helper_identity,
        parent_identity=parent_identity,
    )
    supervisor = parse_identity_payload(payload["supervisor"], "receipt.supervisor")
    target = parse_identity_payload(payload["target"], "receipt.target")
    if (
        supervisor.pid != ready_supervisor.pid
        or supervisor.start_time != ready_supervisor.start_time
        or supervisor.ppid != parent_identity.pid
        or supervisor.process_group != parent_identity.process_group
        or supervisor.session != parent_identity.session
    ):
        raise AssertionError(
            "checkpoint supervisor receipt does not bind the exact helper: "
            f"ready={ready_supervisor!r} final={supervisor!r}"
        )
    if target != ready_target:
        raise AssertionError(
            "checkpoint supervisor receipt changed the bound target identity: "
            f"ready={ready_target!r} final={target!r}"
        )
    cleanup_survivors = parse_identity_list(
        payload["cleanup_survivors"], "receipt.cleanup_survivors"
    )
    if cleanup_survivors:
        raise AssertionError(
            "checkpoint process supervisor left non-zombie fixture processes: "
            f"{cleanup_survivors!r}"
        )
    residual = parse_identity_list(
        payload["residual_before_cleanup"], "receipt.residual_before_cleanup"
    )
    residual_keys = [(identity.pid, identity.start_time) for identity in residual]
    if residual_keys != sorted(set(residual_keys)):
        raise AssertionError(
            "receipt.residual_before_cleanup is not an exact sorted identity set: "
            f"{residual!r}"
        )
    timed_out = payload["timed_out"]
    if type(timed_out) is not bool:
        raise AssertionError(f"receipt.timed_out is not an exact boolean: {timed_out!r}")
    if payload["interruption_signal"] is not None:
        raise AssertionError(
            "successful checkpoint supervisor receipt records an interruption: "
            f"{payload['interruption_signal']!r}"
        )
    if payload["target_release_committed"] is not True:
        raise AssertionError(
            "successful checkpoint supervisor receipt does not prove its target "
            f"release commitment: {payload['target_release_committed']!r}"
        )
    returncode = require_exact_integer(
        payload["target_returncode"], "receipt.target_returncode"
    )
    reaped_pids_payload = payload["reaped_pids"]
    if not isinstance(reaped_pids_payload, list):
        raise AssertionError(f"receipt.reaped_pids is not a list: {reaped_pids_payload!r}")
    reaped_pids = [
        require_exact_integer(value, f"receipt.reaped_pids[{index}]", positive=True)
        for index, value in enumerate(reaped_pids_payload)
    ]
    if reaped_pids != sorted(set(reaped_pids)) or target.pid not in reaped_pids:
        raise AssertionError(
            "checkpoint supervisor receipt does not prove exact target reaping: "
            f"target={target.pid} reaped={reaped_pids!r}"
        )
    current_target = identity_reader(target.pid)
    if current_target is not None and current_target.start_time == target.start_time:
        raise AssertionError(
            "checkpoint supervisor returned while its exact target identity remained: "
            f"{current_target!r}"
        )
    for identity in residual:
        current = identity_reader(identity.pid)
        if current is not None and current.start_time == identity.start_time:
            raise AssertionError(
                "checkpoint supervisor returned while a recorded residual identity "
                f"remained: expected={identity!r} current={current!r}"
            )
    if residual and not timed_out:
        raise AssertionError(
            "checkpoint command exited with non-zombie fixture descendants: "
            f"{residual!r}; cleanup_survivors=[]"
        )
    return returncode, timed_out, residual


def bind_fake_unshare_adapter(
    path: Path,
    *,
    coordinator: ProcessIdentity,
) -> dict[str, ProcessIdentity]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "supervisor",
        "child",
        "watchdog",
    }:
        raise AssertionError(f"fake-unshare adapter receipt has the wrong schema: {payload!r}")
    if require_exact_integer(payload["schema_version"], "adapter.schema_version") != 1:
        raise AssertionError(f"fake-unshare adapter receipt has the wrong version: {payload!r}")
    recorded = {
        name: parse_identity_payload(payload[name], f"adapter.{name}")
        for name in ("supervisor", "child", "watchdog")
    }
    supervisor = recorded["supervisor"]
    child = recorded["child"]
    watchdog = recorded["watchdog"]
    if (
        supervisor.ppid != coordinator.pid
        or supervisor.process_group != supervisor.pid
        or supervisor.session != supervisor.pid
        or child.ppid != supervisor.pid
        or child.process_group != child.pid
        or child.session != child.pid
        or watchdog.ppid != supervisor.pid
        or watchdog.process_group != supervisor.process_group
        or watchdog.session != supervisor.session
    ):
        raise AssertionError(
            "fake-unshare adapter identities do not form the expected private topology: "
            f"coordinator={coordinator!r} receipt={recorded!r}"
        )
    for name, expected in recorded.items():
        current = read_process_identity(expected.pid)
        if (
            current is None
            or current.start_time != expected.start_time
            or current.ppid != expected.ppid
            or current.process_group != expected.process_group
            or current.session != expected.session
            or current.state in {"Z", "X", "x"}
        ):
            raise AssertionError(
                f"fake-unshare adapter {name} identity changed before binding: "
                f"expected={expected!r} current={current!r}"
            )
    return recorded


def validate_fake_unshare_terminal(
    path: Path,
    *,
    ready: dict[str, ProcessIdentity],
    expected_terminating: bool,
    expected_child_returncode: int | None = None,
    expected_watchdog_status: int = 0,
    expected_watchdog_exited_before_child: bool = False,
    expected_watchdog_cleanup_timed_out: bool = False,
    expected_signal: signal.Signals = signal.SIGTERM,
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_keys = {
        "schema_version",
        "supervisor",
        "child",
        "watchdog",
        "terminating",
        "termination_signal",
        "watchdog_exited_before_child",
        "watchdog_cleanup_timed_out",
        "child_returncode",
        "watchdog_status",
        "remaining_members",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise AssertionError(
            f"fake-unshare terminal receipt has the wrong schema: {payload!r}"
        )
    if require_exact_integer(payload["schema_version"], "adapter-terminal.schema_version") != 1:
        raise AssertionError(
            f"fake-unshare terminal receipt has the wrong version: {payload!r}"
        )
    recorded = {
        name: parse_identity_payload(payload[name], f"adapter-terminal.{name}")
        for name in ("supervisor", "child", "watchdog")
    }
    if recorded != ready:
        raise AssertionError(
            "fake-unshare terminal receipt does not bind its exact readiness identities: "
            f"ready={ready!r} terminal={recorded!r}"
        )
    terminating = payload["terminating"]
    if type(terminating) is not bool or terminating is not expected_terminating:
        raise AssertionError(
            "fake-unshare terminal receipt has the wrong termination state: "
            f"expected={expected_terminating!r} actual={terminating!r}"
        )
    termination_signal = payload["termination_signal"]
    if expected_terminating:
        if (
            require_exact_integer(
                termination_signal, "adapter-terminal.termination_signal", positive=True
            )
            != int(expected_signal)
        ):
            raise AssertionError(
                "fake-unshare terminal receipt has the wrong signal: "
                f"expected={int(expected_signal)} actual={termination_signal!r}"
            )
    elif termination_signal is not None:
        raise AssertionError(
            "fake-unshare normal terminal receipt records a signal: "
            f"{termination_signal!r}"
        )
    watchdog_exited_before_child = payload["watchdog_exited_before_child"]
    if (
        type(watchdog_exited_before_child) is not bool
        or watchdog_exited_before_child is not expected_watchdog_exited_before_child
    ):
        raise AssertionError(
            "fake-unshare terminal receipt has the wrong early-watchdog state: "
            f"expected={expected_watchdog_exited_before_child!r} "
            f"actual={watchdog_exited_before_child!r}"
        )
    watchdog_cleanup_timed_out = payload["watchdog_cleanup_timed_out"]
    if (
        type(watchdog_cleanup_timed_out) is not bool
        or watchdog_cleanup_timed_out is not expected_watchdog_cleanup_timed_out
    ):
        raise AssertionError(
            "fake-unshare terminal receipt has the wrong watchdog-timeout state: "
            f"expected={expected_watchdog_cleanup_timed_out!r} "
            f"actual={watchdog_cleanup_timed_out!r}"
        )
    child_returncode = require_exact_integer(
        payload["child_returncode"], "adapter-terminal.child_returncode"
    )
    if (
        expected_child_returncode is not None
        and child_returncode != expected_child_returncode
    ):
        raise AssertionError(
            "fake-unshare terminal receipt has the wrong child status: "
            f"expected={expected_child_returncode} actual={child_returncode}"
        )
    watchdog_status = require_exact_integer(
        payload["watchdog_status"], "adapter-terminal.watchdog_status"
    )
    if watchdog_status != expected_watchdog_status:
        raise AssertionError(
            "fake-unshare watchdog has the wrong terminal status: "
            f"expected={expected_watchdog_status} actual={watchdog_status}"
        )
    remaining = parse_identity_list(
        payload["remaining_members"], "adapter-terminal.remaining_members"
    )
    remaining_keys = [(identity.pid, identity.start_time) for identity in remaining]
    if remaining_keys != sorted(set(remaining_keys)):
        raise AssertionError(
            "fake-unshare terminal remaining-member identities are not exact and sorted: "
            f"{remaining!r}"
        )
    if remaining:
        raise AssertionError(
            f"fake-unshare terminal receipt records live session members: {remaining!r}"
        )
    for name, expected in ready.items():
        current = read_process_identity(expected.pid)
        if (
            current is not None
            and current.start_time == expected.start_time
            and current.state not in {"Z", "X", "x"}
        ):
            raise AssertionError(
                "fake-unshare terminal receipt returned while an exact readiness "
                f"identity remained live: name={name} expected={expected!r} "
                f"current={current!r}"
            )
    independently_live = live_session_processes(ready["child"].session)
    if independently_live:
        raise AssertionError(
            "fake-unshare terminal receipt claimed an empty recorded session while "
            f"members remain: {independently_live!r}"
        )


def read_fixture_process_identity(path: Path, label: str) -> ProcessIdentity:
    fields = path.read_text(encoding="ascii").split()
    if len(fields) != 6:
        raise AssertionError(f"{label} has the wrong identity field count: {fields!r}")
    try:
        identity = ProcessIdentity(
            pid=int(fields[0]),
            ppid=int(fields[1]),
            process_group=int(fields[2]),
            session=int(fields[3]),
            state=fields[4],
            start_time=int(fields[5]),
        )
    except ValueError as error:
        raise AssertionError(f"{label} has a non-integer identity field: {fields!r}") from error
    if (
        identity.pid <= 0
        or identity.ppid <= 0
        or identity.process_group <= 0
        or identity.session <= 0
        or len(identity.state) != 1
        or identity.start_time <= 0
    ):
        raise AssertionError(f"{label} has an invalid process identity: {identity!r}")
    return identity


def require_current_fixture_topology(
    expected: ProcessIdentity,
    label: str,
) -> ProcessIdentity:
    current = read_process_identity(expected.pid)
    if (
        current is None
        or current.state in {"Z", "X", "x"}
        or current.pid != expected.pid
        or current.ppid != expected.ppid
        or current.process_group != expected.process_group
        or current.session != expected.session
        or current.start_time != expected.start_time
    ):
        raise AssertionError(
            f"{label} changed before the fixture bound it: "
            f"expected={expected!r} current={current!r}"
        )
    return current


def exact_signal(identity: ProcessIdentity, signum: signal.Signals) -> None:
    current = read_process_identity(identity.pid)
    if (
        current is None
        or current.start_time != identity.start_time
        or current.state in {"Z", "X", "x"}
    ):
        return
    try:
        os.kill(identity.pid, signum)
    except ProcessLookupError:
        pass


def signal_private_fixture_identities(
    identities: dict[tuple[int, int], ProcessIdentity],
    signum: signal.Signals,
    *,
    protected_group: int,
) -> None:
    groups: set[int] = set()
    for identity in identities.values():
        current = read_process_identity(identity.pid)
        if (
            current is not None
            and current.start_time == identity.start_time
            and current.state not in {"Z", "X", "x"}
            and current.process_group > 0
            and current.process_group != protected_group
        ):
            groups.add(current.process_group)
    for process_group in sorted(groups):
        try:
            os.killpg(process_group, signum)
        except ProcessLookupError:
            pass
    for identity in identities.values():
        exact_signal(identity, signum)


def force_stop_and_reap_supervisor(
    helper: subprocess.Popen[bytes],
    helper_identity: ProcessIdentity,
    *,
    parent_identity: ProcessIdentity,
    ready_path: Path,
) -> list[ProcessIdentity]:
    """Freeze a wedged helper and its exact private tree before killing it."""
    observed: dict[tuple[int, int], ProcessIdentity] = {}
    exact_signal(helper_identity, signal.SIGSTOP)
    target_identity: ProcessIdentity | None = None
    readiness_error: BaseException | None = None
    if ready_path.is_file() and not ready_path.is_symlink():
        try:
            ready_payload = json.loads(ready_path.read_text(encoding="utf-8"))
            _, target_identity = validate_supervisor_ready_receipt(
                ready_payload,
                helper_identity=helper_identity,
                parent_identity=parent_identity,
            )
        except BaseException as error:
            # A malformed early receipt must not bypass emergency teardown.
            readiness_error = error
        else:
            observed[(target_identity.pid, target_identity.start_time)] = target_identity
            current_target = read_process_identity(target_identity.pid)
            if (
                current_target is not None
                and current_target.start_time == target_identity.start_time
                and current_target.process_group == target_identity.pid
                and current_target.session == target_identity.pid
            ):
                try:
                    os.killpg(target_identity.pid, signal.SIGSTOP)
                except ProcessLookupError:
                    pass

    stable_scans = 0
    previous_keys: set[tuple[int, int]] | None = None
    freeze_deadline = time.monotonic() + 3
    while time.monotonic() < freeze_deadline and stable_scans < 3:
        current = snapshot_descendants(helper_identity.pid)
        observed.update(current)
        signal_private_fixture_identities(
            current,
            signal.SIGSTOP,
            protected_group=parent_identity.process_group,
        )
        current_keys = set(current)
        if current_keys == previous_keys and all(
            (identity := read_process_identity(pid)) is None
            or identity.start_time != start_time
            or identity.state in {"T", "t", "Z", "X", "x", "D"}
            for pid, start_time in current_keys
        ):
            stable_scans += 1
        else:
            stable_scans = 0
        previous_keys = current_keys
        time.sleep(0.02)
    freeze_converged = stable_scans >= 3

    if target_identity is not None:
        current_target = read_process_identity(target_identity.pid)
        if (
            current_target is not None
            and current_target.start_time == target_identity.start_time
            and current_target.process_group == target_identity.pid
            and current_target.session == target_identity.pid
        ):
            try:
                os.killpg(target_identity.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    # Keep the stopped subreaper alive until repeated scans prove that no live
    # descendant remains.  This closes the old snapshot-then-kill fork window:
    # a child created just before its parent was stopped is adopted or remains
    # below this helper and is discovered by a later scan before helper death.
    empty_scans = 0
    kill_deadline = time.monotonic() + 3
    while time.monotonic() < kill_deadline and empty_scans < 3:
        current = snapshot_descendants(helper_identity.pid)
        observed.update(current)
        live_current = {
            key: identity
            for key, identity in current.items()
            if process_identity_is_live(identity)
        }
        if live_current:
            empty_scans = 0
            signal_private_fixture_identities(
                live_current,
                signal.SIGKILL,
                protected_group=parent_identity.process_group,
            )
        else:
            empty_scans += 1
        time.sleep(0.02)
    kill_converged = empty_scans >= 3

    exact_signal(helper_identity, signal.SIGKILL)
    try:
        helper.wait(timeout=SUPERVISOR_KILL_SECONDS)
    except subprocess.TimeoutExpired:
        exact_signal(helper_identity, signal.SIGKILL)
        helper.wait(timeout=SUPERVISOR_KILL_SECONDS)

    deadline = time.monotonic() + SUPERVISOR_KILL_SECONDS
    survivors = surviving_processes(observed)
    while survivors and time.monotonic() < deadline:
        signal_private_fixture_identities(
            {(item.pid, item.start_time): item for item in survivors},
            signal.SIGKILL,
            protected_group=parent_identity.process_group,
        )
        time.sleep(0.02)
        survivors = surviving_processes(observed)
    if not freeze_converged:
        raise AssertionError(
            "checkpoint supervisor emergency teardown could not freeze a stable "
            f"descendant closure; cleanup_survivors={survivors!r}"
        )
    if not kill_converged:
        raise AssertionError(
            "checkpoint supervisor emergency teardown could not drain its frozen "
            f"descendant closure; cleanup_survivors={survivors!r}"
        )
    if readiness_error is not None:
        raise AssertionError(
            "checkpoint supervisor published malformed readiness before its "
            f"hard deadline; cleanup_survivors={survivors!r}"
        ) from readiness_error
    return survivors


def run_contained_command(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    started_pids: list[int] | None = None,
    fixture_force_supervisor_deadline: bool = False,
    fixture_force_supervisor_deadline_marker: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    # Raw fork and PR_SET_CHILD_SUBREAPER are confined to a dedicated,
    # single-threaded helper.  It remains in this process group so the project
    # driver's case-level TERM/KILL cleanup cannot lose it, while the command
    # itself enters a private session inside the helper.
    parent = read_process_identity(os.getpid())
    if parent is None:
        raise AssertionError("cannot capture checkpoint fixture parent identity")
    if not PROCESS_SUPERVISOR.is_file() or PROCESS_SUPERVISOR.is_symlink():
        raise AssertionError(f"checkpoint process supervisor is unavailable: {PROCESS_SUPERVISOR}")
    if fixture_force_supervisor_deadline != (
        fixture_force_supervisor_deadline_marker is not None
    ):
        raise AssertionError(
            "forced supervisor deadline requires exactly one fixture readiness marker"
        )
    with tempfile.TemporaryDirectory(prefix="checkpoint-supervisor-") as directory:
        root = Path(directory)
        environment_path = root / "environment.json"
        stdout_path = root / "target.stdout"
        stderr_path = root / "target.stderr"
        ready_path = root / "ready.json"
        receipt_path = root / "receipt.json"
        environment_path.write_text(json.dumps(env, sort_keys=True) + "\n", encoding="utf-8")
        helper_command = [
            sys.executable,
            "-I",
            "-B",
            str(PROCESS_SUPERVISOR),
            "--expected-parent-pid",
            str(parent.pid),
            "--expected-parent-start",
            str(parent.start_time),
            "--cwd",
            cwd,
            "--timeout",
            str(timeout),
            "--environment",
            str(environment_path),
            "--stdout",
            str(stdout_path),
            "--stderr",
            str(stderr_path),
            "--ready",
            str(ready_path),
            "--receipt",
            str(receipt_path),
        ]
        if fixture_force_supervisor_deadline:
            helper_command.append("--fixture-hang-after-ready")
        helper_command.extend(("--", *command))
        with tempfile.TemporaryFile() as helper_stdout, tempfile.TemporaryFile() as helper_stderr:
            helper = subprocess.Popen(
                helper_command,
                cwd="/",
                env={
                    "HOME": "/nonexistent",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                stdin=subprocess.DEVNULL,
                stdout=helper_stdout,
                stderr=helper_stderr,
            )
            helper_identity = read_process_identity(helper.pid)
            if helper_identity is None:
                helper.kill()
                helper.wait(timeout=3)
                raise AssertionError("checkpoint process supervisor disappeared before identity capture")
            if fixture_force_supervisor_deadline:
                assert fixture_force_supervisor_deadline_marker is not None
                marker_deadline = time.monotonic() + 10
                while (
                    not fixture_force_supervisor_deadline_marker.is_file()
                    and helper.poll() is None
                    and time.monotonic() < marker_deadline
                ):
                    time.sleep(0.02)
                if not fixture_force_supervisor_deadline_marker.is_file():
                    survivors = force_stop_and_reap_supervisor(
                        helper,
                        helper_identity,
                        parent_identity=parent,
                        ready_path=ready_path,
                    )
                    raise AssertionError(
                        "forced supervisor fallback target did not satisfy its "
                        f"fixture readiness barrier; cleanup_survivors={survivors!r}"
                    )
                helper_deadline = 0.2
            else:
                helper_deadline = (
                    timeout
                    + SUPERVISOR_NATURAL_EXIT_SECONDS
                    + SUPERVISOR_TERM_SECONDS
                    + SUPERVISOR_KILL_SECONDS
                    + 5
                )
            try:
                helper_status = helper.wait(timeout=helper_deadline)
            except subprocess.TimeoutExpired as error:
                survivors = force_stop_and_reap_supervisor(
                    helper,
                    helper_identity,
                    parent_identity=parent,
                    ready_path=ready_path,
                )
                raise AssertionError(
                    "checkpoint process supervisor exceeded its bounded deadline; "
                    f"cleanup_survivors={survivors!r}"
                ) from error
            helper_output = read_capture(helper_stdout)
            helper_error = read_capture(helper_stderr)

        stdout = stdout_path.read_text(encoding="utf-8", errors="replace") \
            if stdout_path.is_file() else ""
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace") \
            if stderr_path.is_file() else ""
        if not receipt_path.is_file():
            raise AssertionError(
                "checkpoint process supervisor exited without a receipt: "
                f"status={helper_status}\nhelper stdout:\n{helper_output}\n"
                f"helper stderr:\n{helper_error}\ntarget stdout:\n{stdout}\n"
                f"target stderr:\n{stderr}"
            )
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        if "error" in payload or helper_status != 0:
            raise AssertionError(
                "checkpoint process supervisor failed: "
                f"status={helper_status} error={payload.get('error', 'none')}\n"
                f"helper stdout:\n{helper_output}\nhelper stderr:\n{helper_error}\n"
                f"target stdout:\n{stdout}\ntarget stderr:\n{stderr}"
            )
        if not ready_path.is_file() or ready_path.is_symlink():
            raise AssertionError("checkpoint process supervisor omitted its exact readiness receipt")
        readiness_payload = json.loads(ready_path.read_text(encoding="utf-8"))
        returncode, timed_out, _residual = validate_success_supervisor_receipt(
            payload,
            helper_status=helper_status,
            helper_identity=helper_identity,
            parent_identity=parent,
            readiness_payload=readiness_payload,
        )
        target = parse_identity_payload(payload["target"], "receipt.target")
        if started_pids is not None:
            started_pids.append(target.pid)
        if timed_out:
            raise subprocess.TimeoutExpired(
                command,
                timeout,
                output=stdout,
                stderr=stderr,
            )
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)


FAKE_PORTAGE = r'''#!/usr/bin/python3
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


def parse_records(packages: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    cpv = ""
    archive = ""
    for line in packages.read_text(encoding="utf-8").splitlines() + [""]:
        if line.startswith("CPV: "):
            cpv = line[5:]
        elif line.startswith("PATH: "):
            archive = line[6:]
        elif not line and cpv:
            records.append((cpv, archive))
            cpv = archive = ""
    return records


def write_records(pkgdir: Path, records: list[tuple[str, str]]) -> None:
    stanzas = [f"PACKAGES: {len(records)}\nVERSION: 0"]
    for cpv, relative in sorted(records):
        data = (pkgdir / relative).read_bytes()
        stanzas.append(
            "\n".join(
                (
                    f"CPV: {cpv}",
                    f"PATH: {relative}",
                    f"SIZE: {len(data)}",
                    f"MD5: {hashlib.md5(data).hexdigest()}",
                    f"SHA1: {hashlib.sha1(data).hexdigest()}",
                )
            )
        )
    temporary = pkgdir / "Packages.new"
    temporary.write_text("\n\n".join(stanzas) + "\n", encoding="utf-8")
    os.replace(temporary, pkgdir / "Packages")


root = Path(os.environ["HOME"]).parent
control = root / "control"
invoked = Path(sys.argv[0]).name
with (control / "frontends.log").open("a", encoding="utf-8") as stream:
    stream.write(invoked + "\n")

if invoked == "quickpkg":
    if (control / "hang-quickpkg").exists():
        import subprocess
        import time

        child_code = """
import ctypes, os, signal, sys, time
expected_parent = int(sys.argv[1])
if os.getppid() != expected_parent:
    raise SystemExit(91)
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGKILL) != 0:
    raise OSError(ctypes.get_errno(), 'PR_SET_PDEATHSIG failed')
if os.getppid() != expected_parent:
    raise SystemExit(92)
time.sleep(300)
"""
        child = subprocess.Popen(
            [
                "/usr/bin/python3", "-I", "-B", "-c", child_code,
                str(os.getpid()),
            ]
        )
        (control / "active-pids").write_text(
            f"{os.getpid()}\n{child.pid}\n", encoding="utf-8"
        )
        time.sleep(300)
    pkgdir = Path(os.environ["PKGDIR"])
    records = parse_records(pkgdir / "Packages")
    known = {cpv for cpv, _ in records}
    for atom in (arg for arg in sys.argv[1:] if arg.startswith("=")):
        cpv = atom[1:]
        if cpv in known:
            continue
        category, package_version = cpv.split("/", 1)
        package = package_version.rsplit("-", 1)[0]
        relative = f"{category}/{package}/{package_version}.gpkg.tar"
        archive = pkgdir / relative
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(("fake-gpkg:" + cpv).encode())
        records.append((cpv, relative))
        known.add(cpv)
    write_records(pkgdir, records)
    if (control / "mutate-vdb").exists():
        target = root / "var/db/pkg/cat/new-2/BUILD_TIME"
        old = target.read_bytes()
        target.write_bytes(b"X" * len(old))
    raise SystemExit(0)

if invoked == "emaint":
    raise SystemExit(0)

if invoked == "emerge":
    expected_options = [
        "--ignore-default-opts",
        "--ask=n",
        "--autounmask=n",
        "--autounmask-write=n",
        "--buildpkg=n",
        "--getbinpkg=n",
        "--usepkgonly",
        "--binpkg-changed-deps=n",
        "--binpkg-respect-use=n",
        "--use-ebuild-visibility=n",
        "--nodeps",
        "--oneshot",
        "--verbose",
    ]
    expected_environment = {
        "PORTAGE_BINHOST": "",
        "GENTOO_MIRRORS": "",
        "FETCHCOMMAND": "/bin/false",
        "RESUMECOMMAND": "/bin/false",
        "EPYTHON": "python3.15",
    }
    observed_environment = {name: os.environ.get(name) for name in expected_environment}
    if observed_environment != expected_environment:
        raise SystemExit(f"unexpected emerge isolation environment: {observed_environment!r}")
    pkgdir = Path(os.environ["PKGDIR"])
    if sys.argv[1:] == [*expected_options, "--help"]:
        print("fixture emerge help: --usepkgonly --getbinpkg --use-ebuild-visibility")
        raise SystemExit(0)
    archive = pkgdir / "cat/new/new-2.gpkg.tar"
    if sys.argv[1:] == [*expected_options, "--pretend", str(archive)]:
        print("[binary   R    ] cat/new-2::fixture 0 KiB")
        print("Total: 1 package (1 reinstall, 1 binary), Size of downloads: 0 KiB")
        raise SystemExit(0)
    expected = [*expected_options, str(archive)]
    if sys.argv[1:] != expected:
        raise SystemExit(f"unexpected emerge arguments: {sys.argv[1:]!r}")
    if pkgdir.name != "critical-fixture":
        raise SystemExit("emerge PKGDIR is not the activated durable checkpoint")
    target = root / "var/db/pkg/cat/new-2/BUILD_TIME"
    restore_log = control / "restore-ran"
    attempt = len(restore_log.read_text().splitlines()) if restore_log.exists() else 0
    print("\n>>> Emerging binary (1 of 1) cat/new-2::fixture")
    target.write_text(f"restored-by-binpkg-{attempt}\n", encoding="utf-8")
    if attempt == 0 and (control / "mutate-foreign-vdb-during-restore").exists():
        (root / "var/db/pkg/cat/base-1/BUILD_TIME").write_text(
            "foreign-offline-restore-mutation\n", encoding="utf-8"
        )
    if attempt == 0 and (control / "mutate-selected-set-during-restore").exists():
        (root / "var/lib/portage/world").write_text(
            "cat/foreign-selected-package\n", encoding="utf-8"
        )
    if attempt == 0 and (control / "mutate-pkgdir-during-restore").exists():
        # Change a nested, still trusted directory so the complete PKGDIR
        # metadata manifest observes drift without changing the activated
        # selector target directory identity or invalidating archive payloads.
        (pkgdir / "cat").chmod(0o700)
    with restore_log.open("a", encoding="utf-8") as stream:
        stream.write("=cat/new-2\n")
    raise SystemExit(0)

if invoked == "qcheck":
    if sys.argv[1:] == ["=sys-apps/portage-3.0.81.1"]:
        print("sys-apps/portage-3.0.81.1: 0 out of 1 files failed")
        raise SystemExit(0)
    if sys.argv[1:] != ["=cat/new-2"]:
        raise SystemExit(f"unexpected qcheck arguments: {sys.argv[1:]!r}")
    print("cat/new-2: 0 out of 1 files failed")
    raise SystemExit(0)

if invoked == "portageq":
    if sys.argv[1:] == ["match", "/", "sys-apps/portage"]:
        print("sys-apps/portage-3.0.81.1")
        raise SystemExit(0)
    if sys.argv[1:] != ["envvar", "FEATURES"]:
        raise SystemExit("unexpected portageq arguments")
    if (control / "mutate-vdb-late").exists():
        target = root / "var/db/pkg/cat/new-2/BUILD_TIME"
        target.write_bytes(b"L" * len(target.read_bytes()))
    print("sandbox userpriv network-sandbox")
    raise SystemExit(0)

raise SystemExit(f"unexpected python-exec frontend: {invoked}")
'''


FAKE_VERIFIER = r'''from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--snapshot", required=True, type=Path)
parser.add_argument("--vdb", required=True, type=Path)
parser.add_argument("--zstd", required=True)
parser.add_argument("--format", required=True)
parser.add_argument("--validate-gpkg", action="store_true")
args = parser.parse_args()
root = Path(os.environ["HOME"]).parent
control = root / "control"
with (control / "verifier-calls.tsv").open("a", encoding="utf-8") as stream:
    stream.write(f"{args.snapshot}\t{int(args.validate_gpkg)}\t{args.zstd}\n")

if (control / "create-cache-collision").exists() and args.snapshot.name == "source":
    collision = root / "var/cache/gentoo-optimization/binpkgs/snapshot-fixture"
    collision.mkdir(mode=0o700)
    (collision / "sentinel").write_text("do-not-replace", encoding="utf-8")

records: list[tuple[str, str]] = []
cpv = ""
archive_path = ""
for line in (args.snapshot / "Packages").read_text(encoding="utf-8").splitlines():
    if line.startswith("CPV: "):
        cpv = line[5:]
    elif line.startswith("PATH: "):
        archive_path = line[6:]
    elif not line and cpv:
        records.append((cpv, archive_path))
        cpv = archive_path = ""
if cpv:
    records.append((cpv, archive_path))
record_cpvs = [item[0] for item in records]
live = sorted(
    str(path.relative_to(args.vdb))
    for category in args.vdb.iterdir()
    if category.is_dir()
    for path in category.iterdir()
    if path.is_dir()
)
missing = sorted(set(live) - set(record_cpvs))
issues = [
    {"code": "live_cpv_missing_archive", "cpv": cpv,
     "message": "live CPV has no indexed archive"}
    for cpv in missing
]

is_durable_final = args.snapshot.name == "critical-fixture"
if is_durable_final and (control / "replace-trusted-tool").exists():
    tool = root / "tools/usr/bin/sort"
    tool.write_bytes(tool.read_bytes() + b"\n")
if is_durable_final and (control / "replace-selector").exists():
    external = root / "var/lib/gentoo-optimization/recovery/binpkgs/external"
    external.mkdir(mode=0o700, exist_ok=True)
    (external / "Packages").write_text(
        (root / "var/lib/gentoo-optimization/recovery/binpkgs/source/Packages").read_text(),
        encoding="utf-8",
    )
    selector = root / "var/cache/gentoo-optimization/binpkgs/critical-current"
    temporary = selector.with_name("external-selector.partial")
    temporary.symlink_to(external)
    os.replace(temporary, selector)

injected = is_durable_final and (control / "fail-durable-final").exists()
if injected:
    issues.append({"code": "fixture_injected_failure", "message": "injected"})

errors = len(issues)
validated = len(records) if args.validate_gpkg else 0
report = {
    "schema_version": 1,
    "status": "pass" if errors == 0 else "fail",
    "inputs": {
        "snapshot": str(args.snapshot),
        "vdb": str(args.vdb),
        "validate_gpkg": args.validate_gpkg,
        "zstd": args.zstd,
    },
    "counts": {
        "errors": errors,
        "extra_indexed_archives": 0,
        "gpkg_archives_found": len(records),
        "gpkg_archives_indexed": len(records),
        "gpkg_archives_validated": validated,
        "image_tar_zst_streams_tested": validated,
        "indexed_records": len(records),
        "indexed_unique_cpvs": len(set(record_cpvs)),
        "indexed_unique_paths": len(records),
        "live_cpvs": len(live),
        "missing_live_cpvs": len(missing),
        "unindexed_gpkg_archives": 0,
    },
    "coverage": {
        "duplicate_live_cpvs": {},
        "extra_indexed_archives": [],
        "missing_live_cpvs": missing,
        "unindexed_gpkg_archives": [],
    },
    "archives": [
        {
            "cpv": cpv,
            "path": archive_path,
            "size": {
                "actual": (args.snapshot / archive_path).stat().st_size,
                "expected": str((args.snapshot / archive_path).stat().st_size),
            },
            "md5": {"actual": "0" * 32, "expected": "0" * 32},
            "sha1": {"actual": "0" * 40, "expected": "0" * 40},
            "gpkg": {
                "status": "pass" if args.validate_gpkg else "not_requested",
                "image_tar_zst_streams": 1 if args.validate_gpkg else 0,
                "zstd_streams_tested": 1 if args.validate_gpkg else 0,
            },
        }
        for cpv, archive_path in records
    ],
    "issues": issues,
}
print(json.dumps(report, indent=2, sort_keys=True))
raise SystemExit(0 if not issues else 1)
'''


FAKE_MV = r'''#!/usr/bin/python3
from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
control = root / "control"
destination = Path(sys.argv[-1]) if len(sys.argv) >= 3 else Path("/")
race_marker = control / f"concurrent-winner-{destination.name}"
foreign_marker = control / f"concurrent-foreign-winner-{destination.name}"
if "--no-clobber" in sys.argv and (race_marker.exists() or foreign_marker.exists()):
    source = Path(sys.argv[-2])
    if source.is_dir():
        mode = 0o700 if foreign_marker.exists() else 0o750
        destination.mkdir(mode=mode)
        destination.chmod(mode)
    else:
        destination.touch(mode=0o666 if foreign_marker.exists() else source.stat().st_mode & 0o777)
        destination.chmod(0o666 if foreign_marker.exists() else source.stat().st_mode & 0o777)
    raise SystemExit(0)
if "--exchange" in sys.argv:
    left = Path(sys.argv[-2])
    right = Path(sys.argv[-1])
    is_selector_exchange = right.name == "critical-current"
    is_preflight = ".exchange-preflight-" in left.name
    if is_preflight and (control / "exchange-unsupported").exists():
        raise SystemExit(95)
    if is_selector_exchange and (control / "kill-before-exchange").exists():
        os.kill(os.getppid(), 9)
        raise SystemExit(137)
    if is_selector_exchange and (control / "race-selector-at-exchange").exists():
        (control / "race-selector-at-exchange").unlink()
        external = root / "var/lib/gentoo-optimization/recovery/binpkgs/external-near-cas"
        external.mkdir(mode=0o700, exist_ok=True)
        (external / "Packages").write_text(
            (root / "var/lib/gentoo-optimization/recovery/binpkgs/source/Packages").read_text(),
            encoding="utf-8",
        )
        replacement = right.with_name("near-cas-external.partial")
        replacement.symlink_to(external)
        os.replace(replacement, right)
    temporary = left.with_name(left.name + ".fixture-exchange")
    os.rename(left, temporary)
    os.rename(right, left)
    os.rename(temporary, right)
    if is_selector_exchange and (control / "kill-after-exchange").exists():
        os.kill(os.getppid(), 9)
    raise SystemExit(0)
if (
    (control / "mv-noop-cache").exists()
    and "--no-clobber" in sys.argv
    and destination.name == "snapshot-fixture"
):
    raise SystemExit(0)
real = Path(__file__).with_name("mv.real")
os.execv(real, [str(real), *sys.argv[1:]])
'''


FAKE_MOUNT_TOOLS = r'''#!/usr/bin/python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
control = root / "control"
invoked = Path(sys.argv[0]).name
marker = control / "make-conf-overlay-active"
if invoked == "mount":
    source = Path(sys.argv[-2])
    target = Path(sys.argv[-1])
    backup = target.with_name(target.name + ".fixture-unmounted")
    os.rename(target, backup)
    os.link(source, target)
    marker.write_text(str(source) + "\n" + str(target) + "\n" + str(backup) + "\n", encoding="utf-8")
    raise SystemExit(0)
if invoked == "umount":
    source, target_text, backup_text = marker.read_text().splitlines()
    target = Path(target_text)
    backup = Path(backup_text)
    target.unlink()
    os.rename(backup, target)
    marker.unlink(missing_ok=True)
    raise SystemExit(0)
if invoked == "findmnt":
    target = Path(sys.argv[sys.argv.index("--target") + 1])
    if marker.exists():
        source, mounted_target, _backup = marker.read_text().splitlines()
        filesystem = {
            "target": mounted_target,
            "source": "/dev/fake[/checkpoint-freeze]",
            "fstype": "none",
            "options": "rw,bind",
        }
    else:
        filesystem = {
            "target": str(root),
            "source": "/dev/fake",
            "fstype": "xfs",
            "options": "rw",
        }
    print(json.dumps({"filesystems": [filesystem]}, sort_keys=True))
    raise SystemExit(0)
raise SystemExit("unexpected mount helper frontend")
'''


FAKE_FIND = r'''#!/usr/bin/python3
from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
marker = root / "control/find-fail-once"
if marker.exists():
    marker.unlink()
    raise SystemExit(73)
real = Path(__file__).with_name("find.real")
os.execv(real, [str(real), *sys.argv[1:]])
'''


FAKE_CP = r'''#!/usr/bin/python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
with (root / "control/cp-invocations.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\n")
if "--reflink=always" in sys.argv[1:]:
    raise SystemExit("fixture rejects cross-filesystem-unsafe --reflink=always")
real = Path(__file__).with_name("cp.real")
os.execv(real, [str(real), *sys.argv[1:]])
'''


FAKE_SYNC = r'''#!/bin/sh
if [ "$#" -ne 3 ] || [ "$1" != "-f" ] || [ "$2" != "--" ]; then
    echo "unexpected fake sync arguments" >&2
    exit 64
fi
if [ ! -e "$3" ] && [ ! -L "$3" ]; then
    echo "fake sync target is absent: $3" >&2
    exit 66
fi
exit 0
'''


FAKE_UNSHARE = r'''#!/usr/bin/python3
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

original_arguments = list(sys.argv[1:])
fixture_root = Path(__file__).resolve().parents[3]
with (fixture_root / "control/unshare-invocations.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(original_arguments) + "\n")
arguments = list(original_arguments)
while arguments and arguments[0] != "--":
    arguments.pop(0)
if not arguments or arguments.pop(0) != "--" or not arguments:
    raise SystemExit("unexpected fake unshare arguments")

launcher = r"""
import os
import subprocess
import sys
expected_parent = int(sys.argv[1])
release_descriptor = int(sys.argv[2])
command = sys.argv[3:]
if os.getppid() != expected_parent:
    raise SystemExit(91)
if os.read(release_descriptor, 1) != b'R':
    raise SystemExit(93)
os.close(release_descriptor)
if os.getppid() != expected_parent:
    raise SystemExit(92)
# Remain the exact private session/group leader while the command runs.  The
# separately parent-death-bound watchdog can therefore signal the group without
# relying on a reusable numeric PGID after its leader disappeared.
process = subprocess.Popen(command)
returncode = process.wait()
raise SystemExit(returncode if returncode >= 0 else 128 - returncode)
"""

watchdog_code = r"""
import ctypes
import os
from pathlib import Path
import select
import signal
import sys
import time

expected_parent = int(sys.argv[1])
expected_parent_start = int(sys.argv[2])
child_pid = int(sys.argv[3])
child_start = int(sys.argv[4])
child_process_group = int(sys.argv[5])
child_session = int(sys.argv[6])
control_descriptor = int(sys.argv[7])
ready_descriptor = int(sys.argv[8])
control_root = Path(sys.argv[9])
terminal = {'Z', 'X', 'x'}
parent_died = False

def identity(pid):
    try:
        text = Path(f'/proc/{pid}/stat').read_text(encoding='ascii')
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = text[text.rfind(') ') + 2:].split()
    if len(fields) < 20:
        return None
    return int(fields[1]), int(fields[2]), int(fields[3]), fields[0], int(fields[19])

def live_session_members():
    members = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        current = identity(int(entry.name))
        if (
            current is not None
            and current[2] == child_session
            and current[3] not in terminal
        ):
            members.append((int(entry.name), current[1], current[4]))
    return members

def publish_marker(path, text):
    partial = path.with_name(path.name + f'.partial.{os.getpid()}')
    partial.write_text(text, encoding='ascii')
    with partial.open('rb') as stream:
        os.fsync(stream.fileno())
    os.replace(partial, path)

def parent_death(_signum, _frame):
    global parent_died
    parent_died = True

signal.signal(signal.SIGTERM, parent_death)
signal.signal(signal.SIGHUP, parent_death)
signal.signal(signal.SIGINT, parent_death)
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGTERM) != 0:
    raise OSError(ctypes.get_errno(), 'watchdog PR_SET_PDEATHSIG failed')
parent = identity(expected_parent)
if (
    os.getppid() != expected_parent
    or parent is None
    or parent[4] != expected_parent_start
):
    parent_died = True
if os.write(ready_descriptor, b'R') != 1:
    raise SystemExit(94)
os.close(ready_descriptor)

while not parent_died:
    readable, _, _ = select.select([control_descriptor], [], [], 0.02)
    if readable:
        command = os.read(control_descriptor, 1)
        if command != b'N':
            parent_died = True
        break
    parent = identity(expected_parent)
    if (
        os.getppid() != expected_parent
        or parent is None
        or parent[4] != expected_parent_start
    ):
        parent_died = True
os.close(control_descriptor)

child = identity(child_pid)
if (
    child_process_group != child_pid
    or child_session != child_pid
    or (
        child is not None
        and (
            child[4] != child_start
            or child[1] != child_process_group
            or child[2] != child_session
        )
    )
):
    raise SystemExit(76)
if (control_root / 'force-watchdog-pre-drain-failure').exists():
    raise SystemExit(80)
if (control_root / 'force-watchdog-stop-before-drain').exists():
    publish_marker(control_root / 'watchdog-stopped-before-drain', 'ready\n')
    os.kill(os.getpid(), signal.SIGSTOP)
# The exact session leader was bound before workload release.  It may already
# be reaped after a termination signal while other groups in that same session
# remain live.  A present-but-reused/mismatched leader is rejected above; an
# absent leader requires draining the still-anchored recorded session.
deadline = time.monotonic() + 15
empty_scans = 0
first_scan = True
while time.monotonic() < deadline:
    members = live_session_members()
    if not members:
        empty_scans += 1
        if empty_scans >= 3:
            raise SystemExit(
                79 if (control_root / 'force-watchdog-failure').exists() else 0
            )
        time.sleep(0.02)
        continue
    empty_scans = 0
    if first_scan and (control_root / 'exercise-late-session-group').exists():
        first_scan = False
        publish_marker(control_root / 'watchdog-first-snapshot', 'ready\n')
        late_deadline = time.monotonic() + 5
        while not (control_root / 'late-session-pid').is_file() and time.monotonic() < late_deadline:
            time.sleep(0.01)
        if not (control_root / 'late-session-pid').is_file():
            raise SystemExit(77)
        publish_marker(control_root / 'late-session-visible', 'ready\n')
        bound_deadline = time.monotonic() + 5
        while not (control_root / 'late-session-bound').is_file() and time.monotonic() < bound_deadline:
            time.sleep(0.01)
        if not (control_root / 'late-session-bound').is_file():
            raise SystemExit(78)
    else:
        first_scan = False
    for pid, process_group, start_time in members:
        current = identity(pid)
        if (
            current is None
            or current[1] != process_group
            or current[2] != child_session
            or current[4] != start_time
            or current[3] in terminal
        ):
            continue
        pidfd = None
        pidfd_signalled = False
        try:
            if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
                try:
                    pidfd = os.pidfd_open(pid)
                    current = identity(pid)
                    if (
                        current is None
                        or current[1] != process_group
                        or current[2] != child_session
                        or current[4] != start_time
                        or current[3] in terminal
                    ):
                        continue
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                    pidfd_signalled = True
                except (OSError, ProcessLookupError):
                    # Portable fixture hosts may expose the Python pidfd API
                    # while denying the syscall.  Fall back only inside this
                    # already-bound private test session and revalidate again.
                    pass
            if not pidfd_signalled:
                current = identity(pid)
                if (
                    current is not None
                    and current[1] == process_group
                    and current[2] == child_session
                    and current[4] == start_time
                    and current[3] not in terminal
                ):
                    os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            # The repeated observation loop proves eventual emptiness.  A task
            # that changes identity or exits during this exact-signal attempt is
            # simply re-observed on the next pass.
            pass
        finally:
            if pidfd is not None:
                os.close(pidfd)
    if not live_session_members():
        empty_scans = 1
    if empty_scans >= 3:
        raise SystemExit(
            79 if (control_root / 'force-watchdog-failure').exists() else 0
        )
    time.sleep(0.02)
raise SystemExit(75)
"""

def identity(pid: int):
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = text[text.rfind(") ") + 2:].split()
    if len(fields) < 20:
        return None
    return int(fields[1]), int(fields[2]), int(fields[3]), fields[0], int(fields[19])

def identity_payload(pid: int, current):
    return {
        "pid": pid,
        "ppid": current[0],
        "process_group": current[1],
        "session": current[2],
        "state": current[3],
        "start_time": current[4],
    }

def live_session_identities(session_id: int):
    members = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        current = identity(pid)
        if current is not None and current[2] == session_id and current[3] not in {'Z', 'X', 'x'}:
            members.append(identity_payload(pid, current))
    return sorted(members, key=lambda item: (item['pid'], item['start_time']))

def signal_exact_member(member) -> None:
    pid = member['pid']
    current = identity(pid)
    if (
        current is None
        or current[1] != member['process_group']
        or current[2] != member['session']
        or current[3] in {'Z', 'X', 'x'}
        or current[4] != member['start_time']
    ):
        return
    pidfd = None
    pidfd_signalled = False
    try:
        if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
            try:
                pidfd = os.pidfd_open(pid)
                current = identity(pid)
                if (
                    current is None
                    or current[1] != member['process_group']
                    or current[2] != member['session']
                    or current[3] in {'Z', 'X', 'x'}
                    or current[4] != member['start_time']
                ):
                    return
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                pidfd_signalled = True
            except (OSError, ProcessLookupError):
                pass
        if not pidfd_signalled:
            current = identity(pid)
            if (
                current is not None
                and current[1] == member['process_group']
                and current[2] == member['session']
                and current[3] not in {'Z', 'X', 'x'}
                and current[4] == member['start_time']
            ):
                os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    finally:
        if pidfd is not None:
            os.close(pidfd)

def bounded_outer_session_drain(session_id: int):
    deadline = time.monotonic() + 5
    empty_scans = 0
    while time.monotonic() < deadline:
        members = live_session_identities(session_id)
        if not members:
            empty_scans += 1
            if empty_scans >= 3:
                return []
            time.sleep(0.02)
            continue
        empty_scans = 0
        for member in members:
            signal_exact_member(member)
        time.sleep(0.02)
    return live_session_identities(session_id)

def publish_json(path: Path, payload) -> None:
    partial = path.with_name(path.name + f".partial.{os.getpid()}")
    partial.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    with partial.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(partial, path)
    directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)

parent = identity(os.getpid())
if parent is None:
    raise SystemExit("fake unshare cannot capture its own identity")
release_reader, release_writer = os.pipe()
child = subprocess.Popen(
    [
        "/usr/bin/python3", "-I", "-B", "-c", launcher,
        str(os.getpid()), str(release_reader), *arguments,
    ],
    pass_fds=(release_reader,),
    start_new_session=True,
)
os.close(release_reader)
child_identity = None
deadline = time.monotonic() + 3
while child_identity is None and child.poll() is None and time.monotonic() < deadline:
    child_identity = identity(child.pid)
    if child_identity is None:
        time.sleep(0.01)
if child_identity is None:
    child.kill()
    child.wait(timeout=2)
    raise SystemExit("fake unshare child disappeared before identity capture")
if child_identity[1:3] != (child.pid, child.pid):
    child.kill()
    child.wait(timeout=2)
    raise SystemExit("fake unshare child did not enter a private session")

watchdog_control_reader, watchdog_control_writer = os.pipe()
watchdog_ready_reader, watchdog_ready_writer = os.pipe()
fixture_root = Path(__file__).resolve().parents[3]
watchdog = subprocess.Popen(
    [
        "/usr/bin/python3", "-I", "-B", "-c", watchdog_code,
        str(os.getpid()), str(parent[4]), str(child.pid), str(child_identity[4]),
        str(child_identity[1]), str(child_identity[2]),
        str(watchdog_control_reader), str(watchdog_ready_writer),
        str(fixture_root / "control"),
    ],
    pass_fds=(watchdog_control_reader, watchdog_ready_writer),
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
os.close(watchdog_control_reader)
os.close(watchdog_ready_writer)
readable, _, _ = select.select([watchdog_ready_reader], [], [], 3)
if not readable or os.read(watchdog_ready_reader, 1) != b"R":
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=2)
    watchdog.kill()
    watchdog.wait(timeout=2)
    raise SystemExit("fake unshare watchdog did not bind before release")
os.close(watchdog_ready_reader)
watchdog_identity = identity(watchdog.pid)
if watchdog_identity is None:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=2)
    watchdog.kill()
    watchdog.wait(timeout=2)
    raise SystemExit("fake unshare watchdog disappeared after readiness")
adapter_receipt = fixture_root / "control/fake-unshare-adapter-ready.json"
adapter_payload = {
    "schema_version": 1,
    "supervisor": identity_payload(os.getpid(), parent),
    "child": identity_payload(child.pid, child_identity),
    "watchdog": identity_payload(watchdog.pid, watchdog_identity),
}
termination_signal = None
termination_started_at = None
watchdog_request_sent = False

def request_watchdog(command: bytes) -> None:
    global watchdog_request_sent
    if watchdog_request_sent:
        return
    try:
        os.write(watchdog_control_writer, command)
    except BrokenPipeError:
        pass
    watchdog_request_sent = True

def terminate(signum: int, _frame: object) -> None:
    global termination_signal, termination_started_at
    if termination_signal is None:
        termination_signal = signum
        termination_started_at = time.monotonic()
    request_watchdog(b"T")

blocked_signals = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
previous_signal_mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked_signals)
signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)
signal.signal(signal.SIGHUP, terminate)
publish_json(adapter_receipt, adapter_payload)
# The pending-set observation is this fixture adapter's release-commit
# linearization point.  A signal already pending here prevents release; a
# signal that becomes pending afterward is ordered post-commit even if Python
# delivers its handler just after the one-byte release write.
pending_before_release = set(signal.sigpending()) & blocked_signals
try:
    if pending_before_release:
        termination_signal = min(int(item) for item in pending_before_release)
        termination_started_at = time.monotonic()
        request_watchdog(b"T")
    elif os.write(release_writer, b"R") != 1:
        request_watchdog(b"T")
        raise SystemExit("fake unshare child release was incomplete")
finally:
    os.close(release_writer)
    # This private adapter owns its three handled lifecycle signals.  Do not
    # reintroduce an inherited block that would defeat the bounded cleanup
    # deadline after release commitment.
    signal.pthread_sigmask(
        signal.SIG_SETMASK, set(previous_signal_mask) - blocked_signals
    )
watchdog_exited_before_child = False
watchdog_cleanup_timed_out = False
watchdog_status = None
while child.poll() is None:
    watchdog_status = watchdog.poll()
    if watchdog_status is not None:
        current_child = identity(child.pid)
        watchdog_exited_before_child = (
            current_child is not None
            and current_child[4] == child_identity[4]
            and current_child[3] not in {'Z', 'X', 'x'}
        )
        if watchdog_exited_before_child:
            bounded_outer_session_drain(child_identity[2])
        break
    if (
        termination_started_at is not None
        and time.monotonic() - termination_started_at >= 5
    ):
        watchdog_exited_before_child = True
        watchdog_cleanup_timed_out = True
        signal_exact_member(identity_payload(watchdog.pid, watchdog_identity))
        try:
            watchdog.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        watchdog_status = 124
        bounded_outer_session_drain(child_identity[2])
        break
    time.sleep(0.01)
if child.poll() is None:
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        signal_exact_member(identity_payload(child.pid, child_identity))
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
if watchdog_status is None and not watchdog_request_sent:
    # Normal completion still drains every same-session fixture background
    # group.  This adapter deliberately does not claim to emulate kernel PID
    # namespace teardown for a descendant that escapes with setsid().
    request_watchdog(b"N")
os.close(watchdog_control_writer)
if watchdog_status is None:
    normal_watchdog_deadline = time.monotonic() + 18.0
    while watchdog.poll() is None:
        effective_deadline = normal_watchdog_deadline
        if termination_started_at is not None:
            effective_deadline = min(
                effective_deadline, termination_started_at + 5.0
            )
        remaining = effective_deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.02, remaining))
    if watchdog.poll() is None:
        watchdog_cleanup_timed_out = True
        signal_exact_member(identity_payload(watchdog.pid, watchdog_identity))
        try:
            watchdog.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        watchdog_status = 124
        bounded_outer_session_drain(child_identity[2])
    else:
        watchdog_status = watchdog.returncode
else:
    try:
        watchdog.wait(timeout=2)
    except subprocess.TimeoutExpired:
        signal_exact_member(identity_payload(watchdog.pid, watchdog_identity))
        try:
            watchdog.wait(timeout=2)
        except subprocess.TimeoutExpired:
            watchdog_status = 125
signal.pthread_sigmask(signal.SIG_BLOCK, blocked_signals)
pending_at_terminal_commit = set(signal.sigpending()) & blocked_signals
if termination_signal is None and pending_at_terminal_commit:
    termination_signal = min(int(item) for item in pending_at_terminal_commit)
# This immutable snapshot is the terminal-status linearization point.  Signals
# pending here are reflected in both receipt and status; later signals remain
# blocked and are ordered after the adapter's completed result.
terminal_signal = termination_signal
child_returncode = child.returncode if child.returncode is not None else 125
remaining_members = live_session_identities(child_identity[2])
terminal_receipt = fixture_root / "control/fake-unshare-adapter-terminal.json"
publish_json(
    terminal_receipt,
    {
        "schema_version": 1,
        "supervisor": adapter_payload["supervisor"],
        "child": adapter_payload["child"],
        "watchdog": adapter_payload["watchdog"],
        "terminating": terminal_signal is not None,
        "termination_signal": terminal_signal,
        "watchdog_exited_before_child": watchdog_exited_before_child,
        "watchdog_cleanup_timed_out": watchdog_cleanup_timed_out,
        "child_returncode": child_returncode,
        "watchdog_status": watchdog_status,
        "remaining_members": remaining_members,
    },
)
if (fixture_root / "control/pause-after-terminal-publication").exists():
    publish_json(
        fixture_root / "control/fake-unshare-terminal-paused.json",
        {"schema_version": 1},
    )
    pause_deadline = time.monotonic() + 5
    while (
        not (fixture_root / "control/fake-unshare-terminal-release").is_file()
        and time.monotonic() < pause_deadline
    ):
        time.sleep(0.01)
    if not (fixture_root / "control/fake-unshare-terminal-release").is_file():
        raise SystemExit("fake unshare terminal-publication pause timed out")
if watchdog_exited_before_child:
    raise SystemExit(
        "fake unshare watchdog exited before its bound child: "
        + str(watchdog_status)
    )
if watchdog_cleanup_timed_out:
    raise SystemExit("fake unshare watchdog cleanup timed out: " + str(watchdog_status))
if watchdog_status != 0:
    raise SystemExit(f"fake unshare watchdog failed: {watchdog_status}")
if remaining_members:
    raise SystemExit(
        "fake unshare watchdog returned with live recorded-session members: "
        + repr(remaining_members)
    )
if terminal_signal is not None:
    raise SystemExit(128 + terminal_signal)
raise SystemExit(child.returncode)
'''


class CheckpointFixture:
    def __init__(self, root: Path, *, extra_live_cpv: str | None = None) -> None:
        self.root = root
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.control = root / "control"
        self.vdb = root / "var/db/pkg"
        self.cache_parent = root / "var/cache/gentoo-optimization/binpkgs"
        self.durable_parent = root / "var/lib/gentoo-optimization/recovery/binpkgs"
        self.report_parent = root / "var/lib/gentoo-optimization/reports"
        self.state_parent = root / "var/lib/gentoo-optimization/state/project"
        self.lock = self.state_parent / "binpkg-checkpoint.lock"
        self.source = self.durable_parent / "source"
        self.selector = self.cache_parent / "critical-current"
        self.tool_root = root / "tools"
        self.script = root / "bootstrap/create-binpkg-checkpoint.sh"
        self.verifier = self.script.parent / "verify-binpkg-snapshot.py"
        self.last_coordinator_pid: int | None = None
        root.chmod(0o700)
        for directory in (
            self.control,
            self.vdb,
            self.cache_parent,
            self.durable_parent,
            self.report_parent,
            self.state_parent,
            self.tool_root / "usr/bin",
            self.tool_root / "usr/lib/python-exec",
            self.tool_root / "usr/lib/python-exec/python3.15",
            self.tool_root / "usr/sbin",
            self.tool_root / "sbin",
            self.tool_root / "bin",
            self.script.parent,
            root / "root",
            root / "var/lib/portage",
            root / "etc/portage",
            root / "run/gentoo-optimization",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (root / "run/gentoo-optimization").chmod(0o750)
        for lock_name in ("framework-install.lock", "project.lock", "generation.lock"):
            lock = root / "run/gentoo-optimization" / lock_name
            lock.touch(mode=0o640)
            lock.chmod(0o640)
        self._install_tools()
        shutil.copy2(CHECKPOINT, self.script)
        self.script.chmod(0o755)
        self.verifier.write_text(FAKE_VERIFIER, encoding="utf-8")
        self.verifier.chmod(0o755)
        (root / "etc/portage/make.conf").write_text(
            'FEATURES="sandbox userpriv parallel-install"\n', encoding="utf-8"
        )
        self._install_cpv("cat/base-1")
        self._install_cpv("cat/new-2")
        if extra_live_cpv:
            self._install_cpv(extra_live_cpv)
        self.source.mkdir(mode=0o700)
        archive = self.source / "cat/base/base-1.gpkg.tar"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"fake-gpkg:cat/base-1")
        self._write_source_packages(archive)
        self.selector.symlink_to(self.source)

    def _install_tools(self) -> None:
        destination = self.tool_root / "usr/bin"
        for name in TOOLS:
            if name in {"emerge", "qcheck", "sync"}:
                continue
            source = Path("/usr/bin") / name
            if not source.exists():
                raise unittest.SkipTest(f"required fixture host tool is absent: {source}")
            shutil.copy2(source.resolve(), destination / name)
            (destination / name).chmod(0o755)
        python = Path("/usr/bin/python3")
        if not python.exists():
            raise unittest.SkipTest("/usr/bin/python3 is absent")
        shutil.copy2(python.resolve(), destination / "python3")
        (destination / "python3").chmod(0o755)
        shutil.copy2(python.resolve(), destination / "python3.15")
        (destination / "python3.15").chmod(0o755)

        shutil.copy2(Path("/usr/bin/mv").resolve(), destination / "mv.real")
        (destination / "mv.real").chmod(0o755)
        (destination / "mv").write_text(FAKE_MV, encoding="utf-8")
        (destination / "mv").chmod(0o755)
        shutil.copy2(Path("/usr/bin/find").resolve(), destination / "find.real")
        (destination / "find.real").chmod(0o755)
        (destination / "find").write_text(FAKE_FIND, encoding="utf-8")
        (destination / "find").chmod(0o755)
        shutil.copy2(Path("/usr/bin/cp").resolve(), destination / "cp.real")
        (destination / "cp.real").chmod(0o755)
        (destination / "cp").write_text(FAKE_CP, encoding="utf-8")
        (destination / "cp").chmod(0o755)
        (destination / "sync").write_text(FAKE_SYNC, encoding="utf-8")
        (destination / "sync").chmod(0o755)
        for name in ("findmnt", "mount", "umount"):
            (destination / name).write_text(FAKE_MOUNT_TOOLS, encoding="utf-8")
            (destination / name).chmod(0o755)
        (destination / "unshare").write_text(FAKE_UNSHARE, encoding="utf-8")
        (destination / "unshare").chmod(0o755)

        dispatcher = self.tool_root / "usr/lib/python-exec/python-exec2"
        dispatcher.write_text(FAKE_PORTAGE, encoding="utf-8")
        dispatcher.chmod(0o755)
        implementation = self.tool_root / "usr/lib/python-exec/python3.15/emerge"
        implementation.write_text(FAKE_PORTAGE, encoding="utf-8")
        implementation.chmod(0o755)
        (destination / "quickpkg").symlink_to("../lib/python-exec/python-exec2")
        (destination / "emaint").symlink_to("../lib/python-exec/python-exec2")
        (destination / "portageq").symlink_to("../lib/python-exec/python-exec2")
        (destination / "emerge").symlink_to("../lib/python-exec/python-exec2")
        (destination / "qcheck").symlink_to("../lib/python-exec/python-exec2")

    def _install_cpv(self, cpv: str) -> None:
        package = self.vdb / cpv
        package.mkdir(parents=True)
        (package / "BUILD_TIME").write_text("1234567890\n", encoding="utf-8")

    def _write_source_packages(self, archive: Path) -> None:
        data = archive.read_bytes()
        relative = archive.relative_to(self.source)
        text = textwrap.dedent(
            f"""\
            PACKAGES: 1
            VERSION: 0

            CPV: cat/base-1
            PATH: {relative}
            SIZE: {len(data)}
            MD5: {hashlib.md5(data).hexdigest()}
            SHA1: {hashlib.sha1(data).hexdigest()}
            """
        )
        (self.source / "Packages").write_text(text, encoding="utf-8")

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256((self.source / "Packages").read_bytes()).hexdigest()

    def command(
        self,
        *atoms: str,
        identifier: str = "fixture",
        action: str = "create",
        extra_options: tuple[str, ...] = (),
    ) -> list[str]:
        command = [
            str(self.tool_root / "usr/bin/bash"),
            str(self.script),
            "--fixture-mode",
            "--fixture-root",
            str(self.root),
            "--fixture-owner",
            f"{self.uid}:{self.gid}",
            "--tool-root",
            str(self.tool_root),
            "--expected-source-target",
            str(self.source),
            "--expected-source-packages-sha256",
            self.source_sha256,
            "--expected-verifier-sha256",
            hashlib.sha256(self.verifier.read_bytes()).hexdigest(),
        ]
        if action == "reconcile":
            command.append("--reconcile")
        elif action == "finalize":
            command.append("--finalize-offline-restore")
        elif action != "create":
            raise ValueError(action)
        command.extend(extra_options)
        command.extend(
            [
            identifier,
            *(atoms or ("=cat/new-2",)),
            ]
        )
        return command

    def run(
        self,
        *atoms: str,
        identifier: str = "fixture",
        action: str = "create",
        extra_options: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        started_pids: list[int] = []
        result = run_contained_command(
            self.command(
                *atoms,
                identifier=identifier,
                action=action,
                extra_options=extra_options,
            ),
            cwd="/",
            env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            timeout=60,
            started_pids=started_pids,
        )
        self.last_coordinator_pid = started_pids[0]
        return result

    def run_command(
        self,
        command: list[str],
        *,
        timeout: float = 60,
    ) -> subprocess.CompletedProcess[str]:
        started_pids: list[int] = []
        result = run_contained_command(
            command,
            cwd="/",
            env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            timeout=timeout,
            started_pids=started_pids,
        )
        self.last_coordinator_pid = started_pids[0]
        return result

    def fake_unshare_command(self, workload: str) -> list[str]:
        return [
            str(self.tool_root / "usr/bin/unshare"),
            "--pid",
            "--fork",
            "--kill-child=KILL",
            "--mount-proc",
            "--",
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            workload,
            str(self.control),
        ]

    def marker(self, name: str) -> None:
        (self.control / name).touch()


class CheckpointHarnessTest(unittest.TestCase):
    def test_success_receipt_validation_rejects_malformed_schema_and_identity(
        self,
    ) -> None:
        parent = ProcessIdentity(41001, 1, 41001, 41001, "S", 101)
        helper = ProcessIdentity(41002, parent.pid, parent.process_group, parent.session, "S", 102)
        target = ProcessIdentity(41003, helper.pid, 41003, 41003, "R", 103)

        def encoded(identity: ProcessIdentity) -> dict[str, int | str]:
            return {
                "pid": identity.pid,
                "ppid": identity.ppid,
                "process_group": identity.process_group,
                "session": identity.session,
                "state": identity.state,
                "start_time": identity.start_time,
            }

        readiness = {
            "schema_version": 1,
            "supervisor": encoded(helper),
            "target": encoded(target),
        }
        valid = {
            "cleanup_survivors": [],
            "interruption_signal": None,
            "reaped_pids": [target.pid],
            "residual_before_cleanup": [],
            "schema_version": 4,
            "supervisor": encoded(helper),
            "target": encoded(target),
            "target_release_committed": True,
            "target_returncode": 0,
            "timed_out": False,
        }
        self.assertEqual(
            validate_success_supervisor_receipt(
                valid,
                helper_status=0,
                helper_identity=helper,
                parent_identity=parent,
                readiness_payload=readiness,
                identity_reader=lambda _pid: None,
            ),
            (0, False, []),
        )

        malformed: dict[str, dict[str, object]] = {}
        for name in (
            "missing-supervisor",
            "cleanup-not-list",
            "timed-out-not-bool",
            "interrupted-success",
            "helper-start-changed",
            "release-not-committed",
            "target-not-reaped",
        ):
            malformed[name] = json.loads(json.dumps(valid))
        del malformed["missing-supervisor"]["supervisor"]
        malformed["cleanup-not-list"]["cleanup_survivors"] = False
        malformed["timed-out-not-bool"]["timed_out"] = 0
        malformed["interrupted-success"]["interruption_signal"] = signal.SIGTERM
        assert isinstance(malformed["helper-start-changed"]["supervisor"], dict)
        malformed["helper-start-changed"]["supervisor"]["start_time"] = helper.start_time + 1
        malformed["release-not-committed"]["target_release_committed"] = False
        malformed["target-not-reaped"]["reaped_pids"] = []
        for name, payload in malformed.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                validate_success_supervisor_receipt(
                    payload,
                    helper_status=0,
                    helper_identity=helper,
                    parent_identity=parent,
                    readiness_payload=readiness,
                    identity_reader=lambda _pid: None,
                )

        with self.assertRaisesRegex(AssertionError, "exact target identity remained"):
            validate_success_supervisor_receipt(
                valid,
                helper_status=0,
                helper_identity=helper,
                parent_identity=parent,
                readiness_payload=readiness,
                identity_reader=lambda _pid: target,
            )

        residual = ProcessIdentity(41004, helper.pid, 41004, 41004, "S", 104)
        live_residual = json.loads(json.dumps(valid))
        live_residual["timed_out"] = True
        live_residual["residual_before_cleanup"] = [encoded(residual)]
        with self.assertRaisesRegex(AssertionError, "recorded residual identity remained"):
            validate_success_supervisor_receipt(
                live_residual,
                helper_status=0,
                helper_identity=helper,
                parent_identity=parent,
                readiness_payload=readiness,
                identity_reader=(
                    lambda pid: residual if pid == residual.pid else None
                ),
            )
        reused_pid = residual._replace(start_time=residual.start_time + 1)
        self.assertEqual(
            validate_success_supervisor_receipt(
                live_residual,
                helper_status=0,
                helper_identity=helper,
                parent_identity=parent,
                readiness_payload=readiness,
                identity_reader=(
                    lambda pid: reused_pid if pid == residual.pid else None
                ),
            ),
            (0, True, [residual]),
        )

    def test_process_supervisor_proves_single_native_task_before_fork(self) -> None:
        completed = run_contained_command(
            ["/bin/true"],
            cwd="/",
            env={
                "HOME": "/nonexistent",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
            timeout=3,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_process_supervisor_rejects_multithreaded_interpreter_before_fork(
        self,
    ) -> None:
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        harness = textwrap.dedent(
            """\
            import os
            import runpy
            import sys
            import threading

            helper = sys.argv[1]
            helper_arguments = sys.argv[2:]
            stop = threading.Event()
            started = threading.Event()

            def native_worker():
                started.set()
                stop.wait()

            worker = threading.Thread(target=native_worker, name="fixture-native-worker")
            worker.start()
            if not started.wait(2):
                os._exit(88)
            try:
                sys.argv = [helper, *helper_arguments]
                runpy.run_path(helper, run_name="__main__")
            finally:
                stop.set()
                worker.join(timeout=2)
                if worker.is_alive():
                    os._exit(89)
            """
        )
        with tempfile.TemporaryDirectory(
            prefix="checkpoint-multithread-refusal-"
        ) as directory:
            root = Path(directory)
            environment = root / "environment.json"
            stdout = root / "target.stdout"
            stderr = root / "target.stderr"
            ready = root / "ready.json"
            receipt = root / "receipt.json"
            marker = root / "target-started"
            environment.write_text(
                json.dumps(
                    {
                        "HOME": "/nonexistent",
                        "LANG": "C",
                        "LC_ALL": "C",
                        "PATH": "/usr/bin:/bin",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                "-I",
                "-B",
                "-c",
                harness,
                str(PROCESS_SUPERVISOR),
                "--expected-parent-pid",
                str(parent.pid),
                "--expected-parent-start",
                str(parent.start_time),
                "--cwd",
                "/",
                "--timeout",
                "3",
                "--environment",
                str(environment),
                "--stdout",
                str(stdout),
                "--stderr",
                str(stderr),
                "--ready",
                str(ready),
                "--receipt",
                str(receipt),
                "--",
                sys.executable,
                "-I",
                "-B",
                "-c",
                (
                    "from pathlib import Path; "
                    f"Path({str(marker)!r}).touch()"
                ),
            ]
            completed = subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                env={
                    "HOME": "/nonexistent",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            self.assertEqual(
                completed.returncode,
                70,
                completed.stderr.decode("utf-8", errors="replace"),
            )
            self.assertFalse(
                marker.exists(), "multithreaded helper executed its target"
            )
            self.assertFalse(
                ready.exists(), "multithreaded helper published target readiness"
            )
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertIn(
                "checkpoint fixture supervisor is not single-threaded before fork: "
                "native_tasks=",
                payload["error"],
            )
            self.assertIsNone(payload["target"])
            self.assertIs(payload["target_release_committed"], False)
            self.assertEqual(payload["cleanup_survivors"], [])

    def test_interruption_before_fork_or_release_commitment_never_executes_target(
        self,
    ) -> None:
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        stage_contract = {
            "fork": ("--fixture-interrupt-before-fork", "before-target-creation"),
            "release": ("--fixture-interrupt-before-release", "before-target-release"),
            "masked-release": (
                "--fixture-interrupt-after-release-mask",
                "after-release-mask",
            ),
        }
        for stage, (fixture_option, barrier_stage) in stage_contract.items():
            with self.subTest(stage=stage), tempfile.TemporaryDirectory(
                prefix=f"checkpoint-interrupt-{stage}-"
            ) as directory:
                root = Path(directory)
                environment = root / "environment.json"
                stdout = root / "target.stdout"
                stderr = root / "target.stderr"
                ready = root / "ready.json"
                receipt = root / "receipt.json"
                interruption_barrier = root / "interruption-barrier.json"
                marker = root / "target-started"
                environment.write_text(
                    json.dumps(
                        {
                            "HOME": "/nonexistent",
                            "LANG": "C",
                            "LC_ALL": "C",
                            "PATH": "/usr/bin:/bin",
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                command = [
                    sys.executable,
                    "-I",
                    "-B",
                    str(PROCESS_SUPERVISOR),
                    "--expected-parent-pid",
                    str(parent.pid),
                    "--expected-parent-start",
                    str(parent.start_time),
                    "--cwd",
                    "/",
                    "--timeout",
                    "10",
                    "--environment",
                    str(environment),
                    "--stdout",
                    str(stdout),
                    "--stderr",
                    str(stderr),
                    "--ready",
                    str(ready),
                    "--receipt",
                    str(receipt),
                    fixture_option,
                    str(interruption_barrier),
                    "--",
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    (
                        "from pathlib import Path; "
                        f"Path({str(marker)!r}).write_text('started', encoding='ascii')"
                    ),
                ]
                helper = subprocess.Popen(
                    command,
                    cwd="/",
                    env={
                        "HOME": "/nonexistent",
                        "LANG": "C",
                        "LC_ALL": "C",
                        "PATH": "/usr/bin:/bin",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                helper_identity = read_process_identity(helper.pid)
                self.assertIsNotNone(helper_identity)
                assert helper_identity is not None
                barrier_deadline = time.monotonic() + 10
                while (
                    not interruption_barrier.is_file()
                    and helper.poll() is None
                    and time.monotonic() < barrier_deadline
                ):
                    time.sleep(0.01)
                if not interruption_barrier.is_file():
                    if helper.poll() is None:
                        survivors = force_stop_and_reap_supervisor(
                            helper,
                            helper_identity,
                            parent_identity=parent,
                            ready_path=ready,
                        )
                    else:
                        survivors = []
                    self.fail(
                        "checkpoint supervisor omitted its interruption barrier; "
                        f"status={helper.poll()} cleanup_survivors={survivors!r}"
                    )
                barrier = json.loads(
                    interruption_barrier.read_text(encoding="utf-8")
                )
                self.assertEqual(
                    set(barrier), {"schema_version", "stage", "supervisor"}
                )
                self.assertEqual(barrier["schema_version"], 1)
                self.assertEqual(
                    barrier["stage"], barrier_stage,
                )
                barrier_supervisor = parse_identity_payload(
                    barrier["supervisor"], "interruption-barrier.supervisor"
                )
                self.assertEqual(
                    (barrier_supervisor.pid, barrier_supervisor.start_time),
                    (helper_identity.pid, helper_identity.start_time),
                )
                current_helper = read_process_identity(helper_identity.pid)
                self.assertIsNotNone(current_helper)
                assert current_helper is not None
                self.assertEqual(current_helper.start_time, helper_identity.start_time)
                os.kill(helper_identity.pid, signal.SIGTERM)
                try:
                    helper_status = helper.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    survivors = force_stop_and_reap_supervisor(
                        helper,
                        helper_identity,
                        parent_identity=parent,
                        ready_path=ready,
                    )
                    self.fail(
                        "interrupted checkpoint supervisor exceeded its deadline; "
                        f"cleanup_survivors={survivors!r}"
                    )

                self.assertEqual(helper_status, 128 + signal.SIGTERM)
                self.assertFalse(marker.exists(), "interrupted target executed its command")
                payload = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertEqual(
                    set(payload),
                    {
                        "cleanup_survivors",
                        "error",
                        "interruption_signal",
                        "reaped_pids",
                        "schema_version",
                        "target",
                        "target_release_committed",
                        "target_returncode",
                        "timed_out",
                    },
                )
                self.assertEqual(payload["schema_version"], 4)
                self.assertEqual(payload["interruption_signal"], signal.SIGTERM)
                self.assertIs(payload["timed_out"], False)
                self.assertIs(payload["target_release_committed"], False)
                expected_stage = "creation" if stage == "fork" else "release"
                self.assertIn(f"before target {expected_stage}", payload["error"])
                self.assertEqual(
                    parse_identity_list(
                        payload["cleanup_survivors"], "receipt.cleanup_survivors"
                    ),
                    [],
                )
                reaped_pids = payload["reaped_pids"]
                self.assertIsInstance(reaped_pids, list)
                self.assertEqual(reaped_pids, sorted(set(reaped_pids)))
                if stage == "fork":
                    self.assertIsNone(payload["target"])
                    self.assertFalse(ready.exists())
                else:
                    target = parse_identity_payload(payload["target"], "receipt.target")
                    self.assertIn(target.pid, reaped_pids)
                    current = read_process_identity(target.pid)
                    self.assertTrue(
                        current is None or current.start_time != target.start_time,
                        f"interrupted target remained: {current!r}",
                    )
                    readiness = json.loads(ready.read_text(encoding="utf-8"))
                    _supervisor, ready_target = validate_supervisor_ready_receipt(
                        readiness,
                        helper_identity=helper_identity,
                        parent_identity=parent,
                    )
                    self.assertEqual(ready_target, target)

    def test_forced_supervisor_deadline_freezes_and_drains_private_escape(
        self,
    ) -> None:
        child_identity: ProcessIdentity | None = None
        with tempfile.TemporaryDirectory(prefix="checkpoint-forced-fallback-") as directory:
            marker = Path(directory) / "child.identity"
            code = textwrap.dedent(
                f"""\
                import subprocess
                import time
                from pathlib import Path

                child = subprocess.Popen(["/usr/bin/sleep", "300"], start_new_session=True)
                fields = Path(f"/proc/{{child.pid}}/stat").read_text().rsplit(") ", 1)[1].split()
                Path({str(marker)!r}).write_text(
                    f"{{child.pid}} {{fields[19]}}\\n", encoding="ascii"
                )
                time.sleep(300)
                """
            )
            started = time.monotonic()
            with self.assertRaisesRegex(
                AssertionError,
                r"exceeded its bounded deadline; cleanup_survivors=\[\]",
            ):
                run_contained_command(
                    ["/usr/bin/python3", "-I", "-B", "-c", code],
                    cwd="/",
                    env={
                        "HOME": "/nonexistent",
                        "LANG": "C",
                        "LC_ALL": "C",
                        "PATH": "/usr/bin:/bin",
                    },
                    timeout=60,
                    fixture_force_supervisor_deadline=True,
                    fixture_force_supervisor_deadline_marker=marker,
                )
            self.assertLess(time.monotonic() - started, 10)
            self.assertTrue(marker.is_file(), "forced fallback target never published readiness")
            child_pid, child_start = (
                int(value) for value in marker.read_text(encoding="ascii").split()
            )
            child_identity = ProcessIdentity(child_pid, 0, 0, 0, "?", child_start)
            self.assertFalse(
                process_identity_is_live(child_identity),
                f"forced supervisor fallback left a private escape: "
                f"{read_process_identity(child_pid)!r}",
            )

    def test_subreaper_catches_fast_setsid_escape_without_touching_baseline_child(
        self,
    ) -> None:
        unrelated = subprocess.Popen(
            ["/usr/bin/sleep", "300"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        unrelated_identity = read_process_identity(unrelated.pid)
        self.assertIsNotNone(unrelated_identity)
        assert unrelated_identity is not None
        escaped_identity: ProcessIdentity | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="checkpoint-fast-escape-") as directory:
                marker = Path(directory) / "escaped.identity"
                code = textwrap.dedent(
                    f"""\
                    import os
                    import signal
                    from pathlib import Path

                    marker = Path({str(marker)!r})
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    pid = os.fork()
                    if pid != 0:
                        os._exit(0)
                    os.setsid()
                    fields = Path(f"/proc/{{os.getpid()}}/stat").read_text().rsplit(") ", 1)[1].split()
                    marker.write_text(f"{{os.getpid()}} {{fields[19]}}\\n", encoding="ascii")
                    os.execl("/usr/bin/sleep", "sleep", "300")
                    """
                )
                started = time.monotonic()
                with self.assertRaisesRegex(
                    AssertionError,
                    "exited with non-zombie fixture descendants",
                ):
                    run_contained_command(
                        ["/usr/bin/python3", "-I", "-B", "-c", code],
                        cwd="/",
                        env={
                            "HOME": "/nonexistent",
                            "LANG": "C",
                            "LC_ALL": "C",
                            "PATH": "/usr/bin:/bin",
                        },
                        timeout=3,
                    )
                self.assertLess(time.monotonic() - started, 12)
                escaped_pid, escaped_start = (
                    int(value) for value in marker.read_text(encoding="ascii").split()
                )
                escaped_identity = ProcessIdentity(
                    escaped_pid,
                    0,
                    0,
                    0,
                    "?",
                    escaped_start,
                )
                self.assertFalse(
                    process_identity_is_live(escaped_identity),
                    f"fast setsid escape survived: {read_process_identity(escaped_pid)!r}",
                )
                self.assertTrue(
                    process_identity_is_live(unrelated_identity),
                    "pre-existing exact child was collateral fixture cleanup",
                )
        finally:
            if escaped_identity is not None and process_identity_is_live(escaped_identity):
                os.kill(escaped_identity.pid, signal.SIGKILL)
            if process_identity_is_current(unrelated_identity):
                unrelated.kill()
            unrelated.wait(timeout=3)

    def test_target_return_code_preserves_exit_and_signal_semantics(self) -> None:
        environment = {
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
        }
        ordinary = run_contained_command(
            ["/bin/sh", "-c", "exit 23"],
            cwd="/",
            env=environment,
            timeout=3,
        )
        self.assertEqual(ordinary.returncode, 23)
        signalled = run_contained_command(
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                "-c",
                "import os, signal; os.kill(os.getpid(), signal.SIGUSR1)",
            ],
            cwd="/",
            env=environment,
            timeout=3,
        )
        self.assertEqual(signalled.returncode, -signal.SIGUSR1)

    def test_timeout_kills_detached_descendant_without_waiting_for_pipe_eof(self) -> None:
        code = textwrap.dedent(
            """\
            import os
            import subprocess
            import time
            from pathlib import Path

            child = subprocess.Popen(["/usr/bin/sleep", "300"], start_new_session=True)
            fields = Path(f"/proc/{child.pid}/stat").read_text().rsplit(") ", 1)[1].split()
            print(f"{child.pid} {fields[19]}", flush=True)
            time.sleep(300)
            """
        )
        started = time.monotonic()
        child_pid: int | None = None
        child_start: int | None = None
        try:
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                run_contained_command(
                    ["/usr/bin/python3", "-I", "-B", "-c", code],
                    cwd="/",
                    env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                    timeout=0.5,
                )
            self.assertLess(time.monotonic() - started, 8)
            output = str(caught.exception.output).strip()
            child_pid, child_start = (int(value) for value in output.split())
            identity = read_process_identity(child_pid)
            self.assertTrue(
                identity is None
                or identity.start_time != child_start
                or identity.state in {"Z", "X", "x"},
                f"detached timeout child survived: {identity!r}",
            )
        finally:
            if child_pid is not None and child_start is not None:
                identity = read_process_identity(child_pid)
                if (
                    identity is not None
                    and identity.start_time == child_start
                    and identity.state not in {"Z", "X", "x"}
                ):
                    os.kill(child_pid, signal.SIGKILL)


class CreateBinpkgCheckpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = CheckpointFixture(Path(self.temporary.name).resolve())
        self.old_selector_inode = self.fixture.selector.lstat().st_ino

    def assert_selector_unchanged(self) -> None:
        self.assertEqual(os.readlink(self.fixture.selector), str(self.fixture.source))
        self.assertEqual(self.fixture.selector.lstat().st_ino, self.old_selector_inode)

    def report(self) -> Path:
        return self.fixture.report_parent / "checkpoint-fixture"

    def offline_evidence_options(self) -> tuple[str, ...]:
        return ("--restore-cpv", "cat/new-2")

    def test_success_is_exact_journaled_and_activates_last(self) -> None:
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS: checkpoint=fixture live_cpvs=2", result.stdout)
        cache = self.fixture.cache_parent / "snapshot-fixture"
        durable = self.fixture.durable_parent / "critical-fixture"
        self.assertTrue(cache.is_dir())
        self.assertTrue(durable.is_dir())
        self.assertEqual(os.readlink(self.fixture.selector), str(durable))

        state = json.loads(
            (self.fixture.state_parent / "binpkg-checkpoint-fixture.json").read_text()
        )
        self.assertEqual(
            state["status"],
            "selector-activated-offline-restore-pending",
        )
        self.assertEqual(state["live_cpvs"], 2)
        self.assertEqual(state["pending_total"], 1)
        self.assertFalse(state["offline_restoration_tested"])
        self.assertEqual(state["activation"]["selector"], str(self.fixture.selector))
        self.assertTrue((self.report() / "evidence-manifest.sha256").is_file())
        self.assertTrue((self.report() / "journal-preactivation-manifest.sha256").is_file())
        self.assertTrue((self.report() / "activation-intent.json").is_file())
        self.assertTrue((self.report() / "activation-evidence-manifest.sha256").is_file())
        self.assertTrue((self.report() / "prepared-selector.json").is_file())
        self.assertTrue((self.report() / "activation-receipt.json").is_file())
        witness = self.fixture.cache_parent / "critical-current.previous-fixture"
        self.assertTrue(witness.is_symlink())
        self.assertEqual(os.readlink(witness), str(self.fixture.source))
        self.assertFalse(
            (self.fixture.cache_parent / "critical-current.prepared-fixture").exists()
        )
        activated_state = (
            self.fixture.state_parent
            / "binpkg-checkpoint-fixture.selector-activated-offline-restore-pending.json"
        )
        self.assertEqual(state, json.loads(activated_state.read_text()))
        self.assertEqual(
            os.stat(self.fixture.state_parent / "binpkg-checkpoint-fixture.json").st_ino,
            os.stat(activated_state).st_ino,
        )

        phases = [
            json.loads(path.read_text())["phase"]
            for path in sorted((self.report() / "journal").glob("*.json"))
        ]
        self.assertEqual(phases[-1], "prepared-for-final-freeze")
        self.assertLess(phases.index("durable-published"), phases.index("prepared-for-final-freeze"))

        calls = [line.split("\t") for line in (self.fixture.control / "verifier-calls.tsv").read_text().splitlines()]
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(validate == "1" for _, validate, _ in calls))
        self.assertIn([str(cache), "1", str(self.fixture.tool_root / "usr/bin/zstd")], calls)
        self.assertIn([str(durable), "1", str(self.fixture.tool_root / "usr/bin/zstd")], calls)
        self.assertEqual(
            (self.fixture.control / "frontends.log").read_text().splitlines(),
            ["portageq", "qcheck", "emerge", "quickpkg", "emaint", "emaint", "portageq"],
        )
        clone_policy = json.loads((self.report() / "clone-policy.json").read_text())
        containment = json.loads(
            (self.report() / "containment-preflight.json").read_text()
        )
        self.assertEqual(
            containment,
            {
                "schema_version": 3,
                "emulated": True,
                "direct_pidfd_sigterm": {
                    "exact_child_gone": True,
                    "pidfd_open": True,
                    "pidfd_send_signal": True,
                    "signal": "SIGTERM",
                    "returncode": -signal.SIGTERM,
                },
                "unshare_kill_child_sigkill": {
                    "descendant_pidfd_open": True,
                    "escaped_private_process_group_gone": True,
                    "escaped_setsid_descendant_gone": True,
                    "exact_namespace_child_gone": True,
                    "ipv4_errno": "ENETUNREACH",
                    "ipv4_external_unreachable": True,
                    "ipv6_errno": "EADDRNOTAVAIL",
                    "ipv6_external_unreachable": True,
                    "kill_child_signal": "SIGKILL",
                    "mount_proc": True,
                    "namespace_interfaces": ["lo"],
                    "namespace_pid": 1,
                    "network_namespace": True,
                    "network_namespace_distinct": True,
                    "pid_namespace": True,
                    "private_process_group_gone": True,
                    "supervisor_pidfd_open": True,
                    "supervisor_returncode": -signal.SIGKILL,
                    "supervisor_signal": "SIGKILL",
                },
            },
        )
        self.assertIsNotNone(self.fixture.last_coordinator_pid)
        coordinator_pid = self.fixture.last_coordinator_pid
        assert coordinator_pid is not None
        expected_cache_partial = (
            self.fixture.cache_parent / f".snapshot-fixture.partial.{coordinator_pid}"
        )
        expected_durable_partial = (
            self.fixture.durable_parent / f".critical-fixture.partial.{coordinator_pid}"
        )
        cache_partial = Path(clone_policy["clone_legs"][0]["destination"])
        durable_partial = Path(clone_policy["clone_legs"][1]["destination"])
        cache_partial_match = re.fullmatch(
            r"\.snapshot-fixture\.partial\.(\d+)", cache_partial.name
        )
        durable_partial_match = re.fullmatch(
            r"\.critical-fixture\.partial\.(\d+)", durable_partial.name
        )
        self.assertIsNotNone(cache_partial_match)
        self.assertIsNotNone(durable_partial_match)
        assert cache_partial_match is not None
        assert durable_partial_match is not None
        self.assertEqual(cache_partial, expected_cache_partial)
        self.assertEqual(durable_partial, expected_durable_partial)
        self.assertEqual(cache_partial_match.group(1), durable_partial_match.group(1))
        expected_clone_legs = [
            {
                "source": str(self.fixture.source),
                "destination": str(cache_partial),
            },
            {
                "source": str(self.fixture.cache_parent / "snapshot-fixture"),
                "destination": str(durable_partial),
            },
        ]
        expected_copy_tool = str(self.fixture.tool_root / "usr/bin/cp")
        self.assertEqual(
            clone_policy,
            {
                "schema_version": 1,
                "copy_tool": expected_copy_tool,
                "archive_mode": True,
                "reflink_policy": "auto",
                "full_copy_fallback": True,
                "cross_filesystem_supported": True,
                "clone_legs": expected_clone_legs,
            },
        )
        clone_invocations = [
            json.loads(line)
            for line in (self.fixture.control / "cp-invocations.jsonl").read_text().splitlines()
            if "--reflink=auto" in line
        ]
        self.assertEqual(
            clone_invocations,
            [
                [
                    "-a",
                    "--reflink=auto",
                    "--",
                    expected_clone_legs[0]["source"],
                    expected_clone_legs[0]["destination"],
                ],
                [
                    "-a",
                    "--reflink=auto",
                    "--",
                    expected_clone_legs[1]["source"],
                    expected_clone_legs[1]["destination"],
                ],
            ],
        )
        tools = (self.report() / "tool-identities.tsv").read_text()
        tool_rows = [line.split("\t") for line in tools.splitlines()]
        self.assertEqual(
            tool_rows[0],
            [
                "logical_path",
                "resolved_path",
                "logical_stat",
                "sha256",
                "symlink_chain",
            ],
        )
        copy_tool_rows = [row for row in tool_rows[1:] if row[0] == expected_copy_tool]
        self.assertEqual(len(copy_tool_rows), 1)
        self.assertEqual(len(copy_tool_rows[0]), 5)
        self.assertEqual(clone_policy["copy_tool"], copy_tool_rows[0][0])
        self.assertIn(str(self.fixture.tool_root / "usr/bin/quickpkg"), tools)
        self.assertIn("python-exec2", tools)
        self.assertFalse((self.fixture.control / "make-conf-overlay-active").exists())
        cache_manifest = (self.report() / "cache-final-archives.tsv").read_text().splitlines()
        durable_manifest = (self.report() / "durable-final-archives.tsv").read_text().splitlines()
        self.assertEqual(cache_manifest[0], "cpv\trelative_path\tsize\tsha256")
        self.assertEqual(len(cache_manifest), 3)
        self.assertEqual(len(durable_manifest), 3)
        for root, rows in ((cache, cache_manifest), (durable, durable_manifest)):
            for row in rows[1:]:
                _cpv, relative, size, digest = row.split("\t")
                archive = root / relative
                self.assertEqual(archive.stat().st_size, int(size))
                self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), digest)
        self.assertTrue((self.report() / "make-conf-source.identity").is_file())
        lock_receipt = json.loads((self.report() / "portage-vdb-lock.ready.json").read_text())
        self.assertEqual(lock_receipt["implementation"], "fixture-fcntl-lockf")

    def test_early_exchange_preflight_fails_before_expensive_publication(self) -> None:
        self.fixture.marker("exchange-unsupported")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not support atomic mv --exchange", result.stderr)
        self.assert_selector_unchanged()
        self.assertFalse(self.report().exists())

    def test_runtime_lock_initialization_accepts_exact_concurrent_winners(self) -> None:
        for name in (
            "gentoo-optimization",
            "framework-install.lock",
            "project.lock",
            "generation.lock",
            "binpkg-checkpoint.lock",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture = CheckpointFixture(Path(directory).resolve())
                runtime = fixture.root / "run/gentoo-optimization"
                if name == "gentoo-optimization":
                    shutil.rmtree(runtime)
                elif name == "binpkg-checkpoint.lock":
                    fixture.lock.unlink(missing_ok=True)
                else:
                    (runtime / name).unlink()
                fixture.marker(f"concurrent-winner-{name}")
                result = fixture.run()
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_lock_initialization_rejects_foreign_concurrent_winners(self) -> None:
        for name in (
            "gentoo-optimization",
            "framework-install.lock",
            "project.lock",
            "generation.lock",
            "binpkg-checkpoint.lock",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture = CheckpointFixture(Path(directory).resolve())
                runtime = fixture.root / "run/gentoo-optimization"
                if name == "gentoo-optimization":
                    shutil.rmtree(runtime)
                elif name == "binpkg-checkpoint.lock":
                    fixture.lock.unlink(missing_ok=True)
                else:
                    (runtime / name).unlink()
                fixture.marker(f"concurrent-foreign-winner-{name}")
                result = fixture.run()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("foreign", result.stderr)
                self.assertEqual(os.readlink(fixture.selector), str(fixture.source))
        self.assertFalse((self.fixture.cache_parent / "snapshot-fixture").exists())

    def test_exchange_preflight_sigkill_residue_is_exactly_recoverable(self) -> None:
        for crash in (
            "exchange-preflight-first-created",
            "exchange-preflight-created",
            "exchange-preflight-swapped",
            "exchange-preflight-restored",
        ):
            with self.subTest(crash=crash), tempfile.TemporaryDirectory() as directory:
                fixture = CheckpointFixture(Path(directory).resolve())
                fixture.marker(f"crash-{crash}")
                first = fixture.run()
                self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
                (fixture.control / f"crash-{crash}").unlink()
                second = fixture.run()
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertFalse(
                    any(fixture.cache_parent.glob(".critical-current.exchange-preflight-*"))
                )

    def test_every_post_intent_crash_reconciles_to_one_exact_activated_state(self) -> None:
        crash_points = (
            "before-intent-publication",
            "after-intent",
            "after-prepared-selector",
            "after-prepared-selector-activation-pending-phase-publication",
            "after-prepared-state",
            "after-exchange",
            "after-displaced-verified",
            "after-witness",
            "after-receipt",
            "after-selector-activated-offline-restore-pending-phase-publication",
            "after-activated-state",
        )
        for crash in crash_points:
            with self.subTest(crash=crash), tempfile.TemporaryDirectory() as directory:
                fixture = CheckpointFixture(Path(directory).resolve())
                fixture.marker(f"crash-{crash}")
                first = fixture.run()
                self.assertEqual(first.returncode, -signal.SIGKILL, (first.stdout, first.stderr))
                (fixture.control / f"crash-{crash}").unlink()
                recovered = fixture.run(action="reconcile")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                durable = fixture.durable_parent / "critical-fixture"
                self.assertEqual(os.readlink(fixture.selector), str(durable))
                self.assertFalse(
                    (fixture.cache_parent / "critical-current.prepared-fixture").exists()
                )
                witness = fixture.cache_parent / "critical-current.previous-fixture"
                self.assertEqual(os.readlink(witness), str(fixture.source))
                state = json.loads(
                    (fixture.state_parent / "binpkg-checkpoint-fixture.json").read_text()
                )
                self.assertEqual(
                    state["status"], "selector-activated-offline-restore-pending"
                )
                again = fixture.run(action="reconcile")
                self.assertEqual(again.returncode, 0, again.stderr)

    def test_incomplete_intent_partial_is_classified_without_activation(self) -> None:
        self.fixture.marker("crash-before-intent-publication")
        first = self.fixture.run()
        self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
        (self.fixture.control / "crash-before-intent-publication").unlink()
        partial = self.report() / "activation-intent.json.partial"
        partial.write_text('{"schema_version":1', encoding="utf-8")
        result = self.fixture.run(action="reconcile")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete activation intent was durably classified", result.stderr)
        self.assert_selector_unchanged()
        self.assertFalse(partial.exists())
        self.assertTrue((self.report() / "activation-intent.incomplete").is_file())

    def test_reconciliation_rejects_foreign_selector_and_foreign_witness(self) -> None:
        self.fixture.marker("crash-after-intent")
        first = self.fixture.run()
        self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
        (self.fixture.control / "crash-after-intent").unlink()
        foreign = self.fixture.durable_parent / "foreign"
        foreign.mkdir(mode=0o700)
        shutil.copy2(self.fixture.source / "Packages", foreign / "Packages")
        replacement = self.fixture.cache_parent / "foreign-selector"
        replacement.symlink_to(foreign)
        os.replace(replacement, self.fixture.selector)
        result = self.fixture.run(action="reconcile")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("foreign selector", result.stderr)
        self.assertEqual(os.readlink(self.fixture.selector), str(foreign))

        with tempfile.TemporaryDirectory() as directory:
            fixture = CheckpointFixture(Path(directory).resolve())
            fixture.marker("crash-after-witness")
            first = fixture.run()
            self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
            (fixture.control / "crash-after-witness").unlink()
            foreign = fixture.durable_parent / "foreign-witness"
            foreign.mkdir(mode=0o700)
            shutil.copy2(fixture.source / "Packages", foreign / "Packages")
            witness = fixture.cache_parent / "critical-current.previous-fixture"
            replacement = fixture.cache_parent / "foreign-witness-selector"
            replacement.symlink_to(foreign)
            os.replace(replacement, witness)
            result = fixture.run(action="reconcile")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("witness is foreign", result.stderr)
            self.assertEqual(os.readlink(witness), str(foreign))

    def test_same_target_replacement_of_activated_selector_is_rejected(self) -> None:
        self.fixture.marker("crash-after-witness")
        first = self.fixture.run()
        self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
        (self.fixture.control / "crash-after-witness").unlink()
        target = self.fixture.durable_parent / "critical-fixture"
        replacement = self.fixture.cache_parent / "same-target-replacement"
        replacement.symlink_to(target)
        os.replace(replacement, self.fixture.selector)
        result = self.fixture.run(action="reconcile")
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, r"prepared[- ]selector")

    def test_tampered_prepared_selector_record_is_rejected_before_exchange(self) -> None:
        self.fixture.marker("crash-after-prepared-selector")
        first = self.fixture.run()
        self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
        (self.fixture.control / "crash-after-prepared-selector").unlink()
        record = self.report() / "prepared-selector.json"
        payload = json.loads(record.read_text())
        payload["target"] = str(self.fixture.source)
        record.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        old_inode = self.fixture.selector.lstat().st_ino
        result = self.fixture.run(action="reconcile")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prepared-selector record is incoherent", result.stderr)
        self.assertEqual(self.fixture.selector.lstat().st_ino, old_inode)
        self.assertEqual(os.readlink(self.fixture.selector), str(self.fixture.source))

    def test_foreign_near_exchange_update_is_restored_after_sigkill(self) -> None:
        self.fixture.marker("race-selector-at-exchange")
        self.fixture.marker("kill-after-exchange")
        first = self.fixture.run()
        self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
        (self.fixture.control / "kill-after-exchange").unlink()
        recovered = self.fixture.run(action="reconcile")
        self.assertNotEqual(recovered.returncode, 0)
        self.assertIn("lost update and rolled it back", recovered.stderr)
        self.assertTrue(os.readlink(self.fixture.selector).endswith("external-near-cas"))

    def test_offline_restore_finalizer_binds_all_evidence_and_is_idempotent(self) -> None:
        created = self.fixture.run()
        self.assertEqual(created.returncode, 0, created.stderr)
        options = self.offline_evidence_options()
        finalized = self.fixture.run(action="finalize", extra_options=options)
        self.assertEqual(finalized.returncode, 0, finalized.stderr)
        command = json.loads((self.report() / "offline-restore/command.json").read_text())
        self.assertEqual(command["schema_version"], 4)
        self.assertTrue(command["offline"])
        self.assertTrue(command["network_isolated"])
        self.assertEqual(
            command["command"],
            [
                str(self.fixture.tool_root / "usr/bin/emerge"),
                "--ignore-default-opts",
                "--ask=n",
                "--autounmask=n",
                "--autounmask-write=n",
                "--buildpkg=n",
                "--getbinpkg=n",
                "--usepkgonly",
                "--binpkg-changed-deps=n",
                "--binpkg-respect-use=n",
                "--use-ebuild-visibility=n",
                "--nodeps",
                "--oneshot",
                "--verbose",
                str(
                    self.fixture.durable_parent
                    / "critical-fixture/cat/new/new-2.gpkg.tar"
                ),
            ],
        )
        self.assertEqual(
            command["environment"],
            {
                "HOME": str(self.fixture.root / "root"),
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": ":".join(
                    str(self.fixture.tool_root / suffix)
                    for suffix in ("usr/sbin", "usr/bin", "sbin", "bin")
                ),
                "TZ": "UTC",
                "PKGDIR": str(self.fixture.durable_parent / "critical-fixture"),
                "PORTAGE_BINHOST": "",
                "GENTOO_MIRRORS": "",
                "FETCHCOMMAND": "/bin/false",
                "RESUMECOMMAND": "/bin/false",
                "EPYTHON": "python3.15",
            },
        )
        self.assertEqual(command["pretend"]["summary"], {
            "packages": 1,
            "reinstall": 1,
            "binary": 1,
            "download_kib": 0,
        })
        self.assertTrue(command["selected_archive"]["unchanged"])
        self.assertTrue(command["selected_sets_transition"]["unchanged"])
        self.assertTrue(command["pkgdir_transition"]["unchanged"])
        self.assertEqual(command["portage_implementation"]["cpv"], "sys-apps/portage-3.0.81.1")
        self.assertEqual(
            command["containment"]["launcher"],
            [
                str(self.fixture.tool_root / "usr/bin/unshare"),
                "--pid",
                "--net",
                "--fork",
                "--kill-child=KILL",
                "--mount-proc",
                "--",
            ],
        )
        unshare_invocations = [
            json.loads(line)
            for line in (self.fixture.control / "unshare-invocations.jsonl").read_text().splitlines()
        ]
        self.assertTrue(
            any(
                invocation[:6]
                == ["--pid", "--net", "--fork", "--kill-child=KILL", "--mount-proc", "--"]
                and str(self.fixture.tool_root / "usr/bin/emerge") in invocation
                for invocation in unshare_invocations
            ),
            unshare_invocations,
        )
        state_path = self.fixture.state_parent / "binpkg-checkpoint-fixture.json"
        terminal_path = (
            self.fixture.state_parent
            / "binpkg-checkpoint-fixture.offline-restore-proven.json"
        )
        state = json.loads(state_path.read_text())
        self.assertEqual(state["status"], "offline-restore-proven")
        self.assertTrue(state["offline_restoration_tested"])
        self.assertEqual(state["pending_total"], 0)
        receipt = self.report() / "offline-restore-receipt.json"
        receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
        self.assertEqual(state["offline_restore"]["receipt_sha256"], receipt_sha)
        receipt_payload = json.loads(receipt.read_text())
        self.assertEqual(state["offline_restore"]["evidence"], receipt_payload["evidence"])
        for name in ("command", "binpkg", "post_verifier", "attempt_ledger"):
            evidence = receipt_payload["evidence"][name]
            imported = self.report() / evidence["path"]
            self.assertEqual(hashlib.sha256(imported.read_bytes()).hexdigest(), evidence["sha256"])
        self.assertEqual(os.stat(state_path).st_ino, os.stat(terminal_path).st_ino)
        again = self.fixture.run(action="finalize", extra_options=options)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(hashlib.sha256(receipt.read_bytes()).hexdigest(), receipt_sha)

    def test_creation_evidence_manifest_members_never_change_on_reconcile_or_finalize(self) -> None:
        self.assertEqual(self.fixture.run().returncode, 0)
        manifest = self.report() / "evidence-manifest.sha256"
        members = {
            Path(line.split("  ", 1)[1]): line.split("  ", 1)[0]
            for line in manifest.read_text().splitlines()
        }
        self.assertEqual(self.fixture.run(action="reconcile").returncode, 0)
        finalized = self.fixture.run(
            action="finalize", extra_options=self.offline_evidence_options()
        )
        self.assertEqual(finalized.returncode, 0, finalized.stderr)
        for path, expected in members.items():
            self.assertTrue(path.is_file(), path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected, path)

    def test_offline_restore_finalizer_rejects_external_self_attested_evidence(self) -> None:
        self.assertEqual(self.fixture.run().returncode, 0)
        forged = self.fixture.control / "forged.json"
        forged.write_text('{}\n', encoding="utf-8")
        result = self.fixture.run(
            action="finalize",
            extra_options=self.offline_evidence_options()
            + ("--offline-command-evidence", str(forged)),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown option", result.stderr)
        self.assertFalse(
            (self.fixture.state_parent / "binpkg-checkpoint-fixture.offline-restore-proven.json").exists()
        )

    def test_failed_fresh_post_verification_leaves_no_transaction_debris(self) -> None:
        self.assertEqual(self.fixture.run().returncode, 0)
        self.fixture.marker("fail-durable-final")
        result = self.fixture.run(
            action="finalize", extra_options=self.offline_evidence_options()
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact GPKG payload verification failed", result.stderr)
        restore_dir = self.report() / "offline-restore"
        self.assertEqual(list(restore_dir.glob("*.partial*")), [])
        self.assertFalse((self.report() / "offline-restore-receipt.json").exists())

    def test_finalizer_rejects_creation_report_or_any_archive_tamper(self) -> None:
        for target in ("report", "other-archive"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                fixture = CheckpointFixture(Path(directory).resolve())
                self.assertEqual(fixture.run().returncode, 0)
                report = fixture.report_parent / "checkpoint-fixture"
                if target == "report":
                    path = report / "durable-final-verification.json"
                    payload = json.loads(path.read_text())
                    payload["counts"]["indexed_records"] += 1
                    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
                else:
                    archive = fixture.durable_parent / "critical-fixture/cat/base/base-1.gpkg.tar"
                    archive.write_bytes(archive.read_bytes() + b"tamper")
                result = fixture.run(
                    action="finalize", extra_options=("--restore-cpv", "cat/new-2")
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, r"creation evidence|durable archive changed")

    def test_offline_finalization_crash_windows_converge_idempotently(self) -> None:
        for crash in (
            "after-offline-evidence",
            "after-offline-receipt",
            "after-offline-restore-proven-phase-publication",
            "after-offline-restored-state",
        ):
            with self.subTest(crash=crash), tempfile.TemporaryDirectory() as directory:
                fixture = CheckpointFixture(Path(directory).resolve())
                self.assertEqual(fixture.run().returncode, 0)
                holder = CreateBinpkgCheckpointTest()
                holder.fixture = fixture
                options = holder.offline_evidence_options()
                fixture.marker(f"crash-{crash}")
                first = fixture.run(action="finalize", extra_options=options)
                self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
                (fixture.control / f"crash-{crash}").unlink()
                recovered = fixture.run(action="finalize", extra_options=options)
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                state = json.loads(
                    (fixture.state_parent / "binpkg-checkpoint-fixture.json").read_text()
                )
                self.assertEqual(state["status"], "offline-restore-proven")
                self.assertEqual(state["pending_total"], 0)
                restore_dir = fixture.report_parent / "checkpoint-fixture/offline-restore"
                self.assertEqual(list(restore_dir.glob(".*post-verifier*")), [])
                self.assertEqual(list(restore_dir.glob("*.partial*")), [])

    def test_ambiguous_offline_command_crashes_require_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CheckpointFixture(Path(directory).resolve())
            self.assertEqual(fixture.run().returncode, 0)
            options = ("--restore-cpv", "cat/new-2")
            fixture.marker("crash-after-offline-preparation")
            first = fixture.run(action="finalize", extra_options=options)
            self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
            restore_dir = fixture.report_parent / "checkpoint-fixture/offline-restore"
            self.assertFalse((restore_dir / "command-intent.json").exists())
            self.assertTrue((restore_dir / "emerge.pretend.stdout.000").is_file())
            (fixture.control / "crash-after-offline-preparation").unlink()
            recovered = fixture.run(action="finalize", extra_options=options)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIn("reconciled", recovered.stderr)
            command = json.loads((restore_dir / "command.json").read_text())
            self.assertEqual(command["attempt"], 0)
            self.assertIsNone(command["retry_authorization"])

        for crash in ("before-offline-command", "after-offline-command"):
            with self.subTest(crash=crash), tempfile.TemporaryDirectory() as directory:
                fixture = CheckpointFixture(Path(directory).resolve())
                self.assertEqual(fixture.run().returncode, 0)
                options = ("--restore-cpv", "cat/new-2")
                fixture.marker(f"crash-{crash}")
                first = fixture.run(action="finalize", extra_options=options)
                self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
                (fixture.control / f"crash-{crash}").unlink()
                refused = fixture.run(action="finalize", extra_options=options)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("attempt is ambiguous", refused.stderr)
                recovered = fixture.run(
                    action="finalize",
                    extra_options=options + ("--retry-interrupted-offline-restore",),
                )
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                command = json.loads(
                    (fixture.report_parent / "checkpoint-fixture/offline-restore/command.json").read_text()
                )
                self.assertEqual(command["attempt"], 1)
                self.assertIsNotNone(command["retry_authorization"])

        for marker, diagnostic in (
            (
                "mutate-foreign-vdb-during-restore",
                "VDB transition escaped restored CPV",
            ),
            (
                "mutate-selected-set-during-restore",
                "selected/world state differs from the first attempt baseline before retry",
            ),
            (
                "mutate-pkgdir-during-restore",
                "PKGDIR differs from the first attempt baseline before retry",
            ),
        ):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as directory:
                fixture = CheckpointFixture(Path(directory).resolve())
                self.assertEqual(fixture.run().returncode, 0)
                options = ("--restore-cpv", "cat/new-2")
                fixture.marker(marker)
                fixture.marker("crash-after-offline-command")
                first = fixture.run(action="finalize", extra_options=options)
                self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
                (fixture.control / "crash-after-offline-command").unlink()
                rejected = fixture.run(
                    action="finalize",
                    extra_options=options + ("--retry-interrupted-offline-restore",),
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn(diagnostic, rejected.stderr)
                restore_dir = fixture.report_parent / "checkpoint-fixture/offline-restore"
                self.assertFalse((restore_dir / "command.json").exists())
                self.assertFalse((restore_dir / "retry-intent-001.json").exists())
                self.assertEqual(
                    (fixture.control / "restore-ran").read_text().splitlines(),
                    ["=cat/new-2"],
                )

    def test_receipt_and_state_tampering_are_rejected_during_reconciliation(self) -> None:
        for object_name in (
            "receipt-target",
            "receipt-prepared-path",
            "receipt-evidence-sha",
            "state-status",
            "state-cache-path",
            "state-live-cpvs",
            "state-receipt-path",
        ):
            with self.subTest(object_name=object_name), tempfile.TemporaryDirectory() as directory:
                fixture = CheckpointFixture(Path(directory).resolve())
                self.assertEqual(fixture.run().returncode, 0)
                if object_name.startswith("state-"):
                    path = (
                        fixture.state_parent
                        / "binpkg-checkpoint-fixture.selector-activated-offline-restore-pending.json"
                    )
                    payload = json.loads(path.read_text())
                    if object_name == "state-status":
                        payload["status"] = "tampered"
                    elif object_name == "state-cache-path":
                        payload["cache_checkpoint"]["path"] = str(fixture.source)
                    elif object_name == "state-live-cpvs":
                        payload["live_cpvs"] += 1
                    else:
                        payload["activation"]["receipt"] = str(fixture.source / "receipt")
                else:
                    path = fixture.report_parent / "checkpoint-fixture/activation-receipt.json"
                    payload = json.loads(path.read_text())
                    if object_name == "receipt-target":
                        payload["target"] = str(fixture.source)
                    elif object_name == "receipt-prepared-path":
                        payload["prepared_selector_record"]["path"] = str(fixture.source)
                    else:
                        payload["activation_evidence"]["sha256"] = "0" * 64
                path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
                result = fixture.run(action="reconcile")
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, r"incoherent|phase state")

    def test_reconcile_and_finalize_require_the_exact_original_input_bindings(self) -> None:
        cases = ("source", "source-sha", "verifier", "delta")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = CheckpointFixture(Path(directory).resolve())
                self.assertEqual(fixture.run().returncode, 0)
                extras: tuple[str, ...] = ()
                atoms: tuple[str, ...] = ()
                if case == "source":
                    extras = (
                        "--expected-source-target",
                        str(fixture.durable_parent / "not-the-source"),
                    )
                elif case == "source-sha":
                    extras = ("--expected-source-packages-sha256", "0" * 64)
                elif case == "verifier":
                    alternate = fixture.script.parent / "alternate-verifier.py"
                    alternate.write_text(FAKE_VERIFIER + "\n# alternate\n", encoding="utf-8")
                    alternate.chmod(0o755)
                    extras = (
                        "--verifier",
                        str(alternate),
                        "--expected-verifier-sha256",
                        hashlib.sha256(alternate.read_bytes()).hexdigest(),
                    )
                else:
                    atoms = ("=cat/base-1",)
                result = fixture.run(*atoms, action="reconcile", extra_options=extras)
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(
                    result.stderr,
                    r"activation intent is invalid|delta atoms differ|verifier digest|trusted tool identity changed",
                )
        self.assertEqual(self.fixture.run().returncode, 0)
        options = self.offline_evidence_options() + (
            "--expected-source-packages-sha256",
            "0" * 64,
        )
        result = self.fixture.run(action="finalize", extra_options=options)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("activation intent is invalid", result.stderr)

    def test_bound_artifact_preparation_state_tamper_is_rejected(self) -> None:
        self.assertEqual(self.fixture.run().returncode, 0)
        preparation = self.report() / "artifact-preparation-state.json"
        payload = json.loads(preparation.read_text())
        payload["live_cpvs"] += 1
        preparation.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        result = self.fixture.run(action="reconcile")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("artifact preparation state changed", result.stderr)

    def test_sigkill_inside_exchange_is_reconciled_without_guessing(self) -> None:
        self.fixture.marker("kill-after-exchange")
        first = self.fixture.run()
        self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
        (self.fixture.control / "kill-after-exchange").unlink()
        recovered = self.fixture.run(action="reconcile")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(
            os.readlink(self.fixture.selector),
            str(self.fixture.durable_parent / "critical-fixture"),
        )

    def test_all_mutation_paths_must_be_canonical_direct_children(self) -> None:
        result = self.fixture.run(
            extra_options=(
                "--selector",
                str(self.fixture.cache_parent / "nested/../critical-current"),
            )
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not lexically canonical", result.stderr)
        self.assert_selector_unchanged()
        nested = self.fixture.cache_parent / "nested"
        nested.mkdir(mode=0o700)
        result = self.fixture.run(
            extra_options=("--selector", str(nested / "critical-current"),)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a direct child", result.stderr)
        self.assert_selector_unchanged()
        result = self.fixture.run(
            extra_options=("--state-parent", "/tmp/checkpoint-fixture-escape",)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("escapes the fake root", result.stderr)
        self.assert_selector_unchanged()

    def test_duplicate_and_nonexact_atoms_are_rejected_without_activation(self) -> None:
        for atoms in (
            ("cat/new-2",),
            ("=cat/new-2:0",),
            ("=cat/new-2", "=cat/new-2"),
        ):
            with self.subTest(atoms=atoms):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = CheckpointFixture(Path(directory).resolve())
                    inode = fixture.selector.lstat().st_ino
                    result = fixture.run(*atoms)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(os.readlink(fixture.selector), str(fixture.source))
                    self.assertEqual(fixture.selector.lstat().st_ino, inode)

    def test_requested_atoms_must_equal_complete_source_live_delta(self) -> None:
        result = self.fixture.run("=cat/base-1", "=cat/new-2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("complete exact source-to-live CPV delta", result.stderr)
        self.assert_selector_unchanged()
        self.assertIn("selector_unchanged=true", (self.report() / "failure.txt").read_text())

    def test_content_hash_detects_same_size_vdb_mutation(self) -> None:
        self.fixture.marker("mutate-vdb")
        before = (self.fixture.vdb / "cat/new-2/BUILD_TIME").stat()
        result = self.fixture.run()
        after = (self.fixture.vdb / "cat/new-2/BUILD_TIME").stat()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before.st_size, after.st_size)
        self.assertIn("VDB content or metadata changed", result.stderr)
        self.assert_selector_unchanged()
        self.assertFalse((self.fixture.cache_parent / "snapshot-fixture").exists())

    def test_final_payload_failure_leaves_selector_and_state_untouched(self) -> None:
        self.fixture.marker("fail-durable-final")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact GPKG payload verification failed", result.stderr)
        self.assert_selector_unchanged()
        self.assertFalse((self.fixture.state_parent / "binpkg-checkpoint-fixture.json").exists())
        self.assertTrue((self.fixture.durable_parent / "critical-fixture").is_dir())
        self.assertIn("selector_unchanged=true", (self.report() / "failure.txt").read_text())

    def test_no_clobber_zero_status_without_move_is_rejected_by_inode_postconditions(self) -> None:
        self.fixture.marker("mv-noop-cache")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("left staging source in place", result.stderr)
        self.assert_selector_unchanged()
        self.assertFalse((self.fixture.cache_parent / "snapshot-fixture").exists())

    def test_preexisting_publication_collision_is_never_replaced(self) -> None:
        self.fixture.marker("create-cache-collision")
        result = self.fixture.run()
        collision = self.fixture.cache_parent / "snapshot-fixture"
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((collision / "sentinel").read_text(), "do-not-replace")
        self.assertIn("publication destination already exists", result.stderr)
        self.assert_selector_unchanged()

    def test_lost_selector_update_is_detected_and_not_overwritten(self) -> None:
        self.fixture.marker("replace-selector")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source selector identity changed (lost update)", result.stderr)
        self.assertTrue(os.readlink(self.fixture.selector).endswith("/external"))
        self.assertFalse(os.readlink(self.fixture.selector).endswith("/critical-fixture"))
        self.assertIn("selector_unchanged=false", (self.report() / "failure.txt").read_text())

    def test_tool_replacement_mid_creation_fails_before_selector_mutation(self) -> None:
        self.fixture.marker("replace-trusted-tool")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("trusted tool identity changed", result.stderr)
        self.assert_selector_unchanged()

    def test_near_exchange_lost_update_is_atomically_captured_and_restored(self) -> None:
        self.fixture.marker("race-selector-at-exchange")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("near-rename lost update and rolled it back", result.stderr)
        self.assertTrue(os.readlink(self.fixture.selector).endswith("/external-near-cas"))
        self.assertFalse(os.readlink(self.fixture.selector).endswith("/critical-fixture"))
        self.assertFalse((self.fixture.control / "make-conf-overlay-active").exists())

    def test_exclusive_lock_rejects_concurrent_transaction(self) -> None:
        self.fixture.lock.touch(mode=0o600)
        with self.fixture.lock.open("r+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another binpkg checkpoint transaction holds the lock", result.stderr)
        self.assert_selector_unchanged()

    def test_sigkill_before_vdb_lock_parent_binding_rejects_adopter_without_harness_cleanup(self) -> None:
        self.fixture.marker("vdb-lock-prebind-hold")
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.command(),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("checkpoint coordinator disappeared before identity capture")
            observed: dict[tuple[int, int], ProcessIdentity] = {}
            holder: ProcessIdentity | None = None
            try:
                entered = self.fixture.control / "vdb-lock-prebind-entered"
                deadline = time.monotonic() + 20
                while not entered.is_file() and process.poll() is None and time.monotonic() < deadline:
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.02)
                if not entered.is_file():
                    self.fail(
                        "VDB lock holder did not enter the pre-binding barrier: "
                        f"rc={process.poll()}\nstdout:\n{read_capture(stdout_file)}\n"
                        f"stderr:\n{read_capture(stderr_file)}"
                    )
                holder_pid = int(entered.read_text().strip())
                holder = read_process_identity(holder_pid)
                self.assertIsNotNone(holder)
                assert holder is not None
                self.assertEqual(holder.ppid, root.pid)
                observed[(holder.pid, holder.start_time)] = holder

                os.kill(root.pid, signal.SIGKILL)
                self.assertEqual(process.wait(timeout=10), -signal.SIGKILL)
                (self.fixture.control / "vdb-lock-prebind-release").touch()
                self.assertTrue(
                    wait_process_identity_stopped(holder, 10),
                    "pre-binding holder adopted the coordinator's adopter",
                )
                self.assertFalse((self.report() / "portage-vdb-lock.ready.json").exists())
                self.assert_selector_unchanged()
            finally:
                (self.fixture.control / "vdb-lock-prebind-release").touch()
                observed.update(snapshot_descendants(root.pid))
                if holder is not None:
                    observed[(holder.pid, holder.start_time)] = holder
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "pre-binding SIGKILL cleanup left non-zombie processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_sigkill_after_vdb_lock_ready_terminates_holder_before_harness_cleanup(self) -> None:
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.command(),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("checkpoint coordinator disappeared before identity capture")
            observed: dict[tuple[int, int], ProcessIdentity] = {}
            holder: ProcessIdentity | None = None
            try:
                ready = self.report() / "portage-vdb-lock.ready.json"
                deadline = time.monotonic() + 20
                while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.02)
                if not ready.is_file():
                    self.fail(
                        "VDB lock holder did not become ready before coordinator exit: "
                        f"rc={process.poll()}\nstdout:\n{read_capture(stdout_file)}\n"
                        f"stderr:\n{read_capture(stderr_file)}"
                    )
                holder_pid = int(json.loads(ready.read_text())["pid"])
                holder = read_process_identity(holder_pid)
                self.assertIsNotNone(holder)
                assert holder is not None
                self.assertEqual(holder.ppid, root.pid)
                observed[(holder.pid, holder.start_time)] = holder

                # Kill only the coordinator.  The assertion below occurs before
                # the outer fixture harness is allowed to signal the holder.
                os.kill(root.pid, signal.SIGKILL)
                self.assertEqual(process.wait(timeout=10), -signal.SIGKILL)
                self.assertTrue(
                    wait_process_identity_stopped(holder, 10),
                    "VDB lock holder outlived its exact coordinator after SIGKILL",
                )
                self.assert_selector_unchanged()
            finally:
                observed.update(snapshot_descendants(root.pid))
                if holder is not None:
                    observed[(holder.pid, holder.start_time)] = holder
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "post-ready SIGKILL cleanup left non-zombie processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_cleanup_deadline_expiry_is_bounded_and_portable_adapter_drains_children(self) -> None:
        self.fixture.marker("hang-quickpkg")
        self.fixture.marker("force-cleanup-deadline-expiry")
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.command(),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("checkpoint coordinator disappeared before identity capture")
            observed: dict[tuple[int, int], ProcessIdentity] = {}
            try:
                active = self.fixture.control / "active-pids"
                deadline = time.monotonic() + 20
                while (
                    not active.is_file()
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.02)
                if not active.is_file():
                    self.fail(
                        "cleanup-deadline fixture did not reach active state: "
                        f"rc={process.poll()} active={active.exists()}\n"
                        f"stdout:\n{read_capture(stdout_file)}\n"
                        f"stderr:\n{read_capture(stderr_file)}"
                    )
                observed.update(snapshot_descendants(root.pid))
                for pid_text in active.read_text().splitlines():
                    identity = read_process_identity(int(pid_text))
                    if identity is not None:
                        observed[(identity.pid, identity.start_time)] = identity
                adapter = bind_fake_unshare_adapter(
                    self.fixture.control / "fake-unshare-adapter-ready.json",
                    coordinator=root,
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )

                started = time.monotonic()
                os.kill(root.pid, signal.SIGTERM)
                self.assertEqual(process.wait(timeout=5), 143)
                self.assertLess(time.monotonic() - started, 5)
                stderr = read_capture(stderr_file)
                self.assertRegex(
                    stderr,
                    r"ERROR: active-child cleanup deadline expired: pid=\d+ start=\d+ state=[^\s]",
                )
                self.assertIn(
                    "ERROR: checkpoint cleanup incomplete: active_child_status=5 "
                    "VDB_lock_status=0 make_conf_overlay_status=0",
                    stderr,
                )
                failure = (self.report() / "failure.txt").read_text()
                self.assertIn("active_child_cleanup_status=5", failure)
                teardown_deadline = time.monotonic() + 10
                survivors = surviving_processes(observed)
                while survivors and time.monotonic() < teardown_deadline:
                    time.sleep(0.02)
                    survivors = surviving_processes(observed)
                self.assertEqual(
                    survivors,
                    [],
                    "deterministic portable adapter did not drain the bounded "
                    f"coordinator failure: {survivors!r}",
                )
                self.assert_selector_unchanged()
            finally:
                observed.update(snapshot_descendants(root.pid))
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "cleanup-deadline regression left non-zombie processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_vdb_lock_cleanup_deadline_expiry_is_bounded_and_never_waits_live_holder(self) -> None:
        self.fixture.marker("force-cleanup-deadline-expiry")
        self.fixture.marker("fail-after-vdb-lock-ready")
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.command(),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("checkpoint coordinator disappeared before identity capture")
            observed: dict[tuple[int, int], ProcessIdentity] = {}
            holder: ProcessIdentity | None = None
            try:
                ready = self.report() / "portage-vdb-lock.ready.json"
                setup_deadline = time.monotonic() + 20
                while (
                    not ready.is_file()
                    and process.poll() is None
                    and time.monotonic() < setup_deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.02)
                if not ready.is_file():
                    self.fail(
                        "VDB cleanup-deadline fixture did not publish readiness: "
                        f"rc={process.poll()}\nstdout:\n{read_capture(stdout_file)}\n"
                        f"stderr:\n{read_capture(stderr_file)}"
                    )
                ready_holder = read_process_identity(int(json.loads(ready.read_text())["pid"]))
                if ready_holder is not None:
                    observed[(ready_holder.pid, ready_holder.start_time)] = ready_holder
                started = time.monotonic()
                if process.poll() is None:
                    process.wait(timeout=5)
                self.assertLess(time.monotonic() - started, 5)
                self.assertNotEqual(process.returncode, 0)
                stderr = read_capture(stderr_file)
                match = re.search(
                    r"ERROR: VDB-lock cleanup deadline expired: "
                    r"pid=(\d+) start=(\d+) state=([^\s])",
                    stderr,
                )
                self.assertIsNotNone(match, stderr)
                assert match is not None
                holder = ProcessIdentity(
                    pid=int(match.group(1)),
                    ppid=root.pid,
                    process_group=0,
                    session=0,
                    state=match.group(3),
                    start_time=int(match.group(2)),
                )
                observed[(holder.pid, holder.start_time)] = holder
                self.assertIn(
                    "ERROR: checkpoint cleanup incomplete: active_child_status=0 "
                    "VDB_lock_status=5 make_conf_overlay_status=0",
                    stderr,
                )
                failure = (self.report() / "failure.txt").read_text()
                self.assertIn("active_child_cleanup_status=0", failure)
                self.assertIn("VDB_lock_cleanup_status=5", failure)
                self.assertTrue(
                    wait_process_identity_stopped(holder, 10),
                    "VDB holder outlived the bounded coordinator failure",
                )
                self.assert_selector_unchanged()
            finally:
                observed.update(snapshot_descendants(root.pid))
                if holder is not None:
                    observed[(holder.pid, holder.start_time)] = holder
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "VDB cleanup-deadline regression left non-zombie processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_portable_adapter_drains_tracked_descendants_after_coordinator_sigkill(self) -> None:
        self.fixture.marker("hang-quickpkg")
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.command(),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("checkpoint coordinator disappeared before identity capture")
            observed: dict[tuple[int, int], ProcessIdentity] = {}
            active_identities: list[ProcessIdentity] = []
            try:
                active = self.fixture.control / "active-pids"
                deadline = time.monotonic() + 20
                while not active.is_file() and process.poll() is None and time.monotonic() < deadline:
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.02)
                if not active.is_file():
                    self.fail(
                        "SIGKILL regression did not reach the tracked workload: "
                        f"rc={process.poll()}\nstdout:\n{read_capture(stdout_file)}\n"
                        f"stderr:\n{read_capture(stderr_file)}"
                    )
                for pid_text in active.read_text().splitlines():
                    identity = read_process_identity(int(pid_text))
                    self.assertIsNotNone(identity)
                    assert identity is not None
                    active_identities.append(identity)
                    observed[(identity.pid, identity.start_time)] = identity
                adapter = bind_fake_unshare_adapter(
                    self.fixture.control / "fake-unshare-adapter-ready.json",
                    coordinator=root,
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                os.kill(root.pid, signal.SIGKILL)
                self.assertEqual(process.wait(timeout=10), -signal.SIGKILL)
                teardown_deadline = time.monotonic() + 10
                survivors = surviving_processes(observed)
                while survivors and time.monotonic() < teardown_deadline:
                    time.sleep(0.02)
                    survivors = surviving_processes(observed)
                self.assertEqual(
                    survivors,
                    [],
                    "portable adapter left tracked descendants after coordinator SIGKILL: "
                    f"{survivors!r}",
                )
                self.assert_selector_unchanged()
            finally:
                observed.update(snapshot_descendants(root.pid))
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "coordinator-SIGKILL regression left non-zombie processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_fake_unshare_terminal_commit_keeps_late_signal_and_receipt_consistent(
        self,
    ) -> None:
        self.fixture.marker("pause-after-terminal-publication")
        workload = textwrap.dedent(
            """
            from pathlib import Path
            import sys
            import time

            control = Path(sys.argv[1])
            (control / "terminal-workload-ready").write_text(
                "ready", encoding="ascii"
            )
            deadline = time.monotonic() + 10
            while (
                not (control / "terminal-workload-release").is_file()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            if not (control / "terminal-workload-release").is_file():
                raise SystemExit(91)
            raise SystemExit(0)
            """
        )
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        observed: dict[tuple[int, int], ProcessIdentity] = {}
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.fake_unshare_command(workload),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("fake-unshare supervisor disappeared before identity capture")
            adapter: dict[str, ProcessIdentity] | None = None
            try:
                ready_path = self.fixture.control / "fake-unshare-adapter-ready.json"
                workload_ready = self.fixture.control / "terminal-workload-ready"
                deadline = time.monotonic() + 10
                while (
                    (not ready_path.is_file() or not workload_ready.is_file())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.01)
                if not ready_path.is_file() or not workload_ready.is_file():
                    self.fail(
                        "terminal-commit adapter did not publish readiness: "
                        f"rc={process.poll()} stderr={read_capture(stderr_file)!r}"
                    )
                adapter = bind_fake_unshare_adapter(ready_path, coordinator=parent)
                self.assertEqual(
                    (adapter["supervisor"].pid, adapter["supervisor"].start_time),
                    (root.pid, root.start_time),
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                (self.fixture.control / "terminal-workload-release").touch()
                paused = self.fixture.control / "fake-unshare-terminal-paused.json"
                deadline = time.monotonic() + 10
                while (
                    not paused.is_file()
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                if not paused.is_file():
                    self.fail(
                        "adapter did not reach its post-publication terminal barrier: "
                        f"rc={process.poll()} stderr={read_capture(stderr_file)!r}"
                    )
                exact_signal(root, signal.SIGTERM)
                self.assertTrue(process_identity_is_live(root))
                (self.fixture.control / "fake-unshare-terminal-release").touch()
                self.assertEqual(process.wait(timeout=10), 0)
                validate_fake_unshare_terminal(
                    self.fixture.control / "fake-unshare-adapter-terminal.json",
                    ready=adapter,
                    expected_terminating=False,
                    expected_child_returncode=0,
                )
            finally:
                observed.update(snapshot_descendants(root.pid))
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "terminal-commit adapter cleanup left live processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_fake_unshare_unblocks_inherited_handled_signals(self) -> None:
        workload = (
            "from pathlib import Path; import sys, time; "
            "Path(sys.argv[1], 'inherited-mask-workload-ready').write_text("
            "'ready', encoding='ascii'); time.sleep(300)"
        )
        adapter_command = self.fixture.fake_unshare_command(workload)
        inherited_mask_launcher = (
            "import os, signal, sys; "
            "signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM}); "
            "os.execv(sys.argv[1], sys.argv[1:])"
        )
        command = [
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            inherited_mask_launcher,
            *adapter_command,
        ]
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        observed: dict[tuple[int, int], ProcessIdentity] = {}
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                command,
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("fake-unshare supervisor disappeared before identity capture")
            adapter: dict[str, ProcessIdentity] | None = None
            try:
                ready_path = self.fixture.control / "fake-unshare-adapter-ready.json"
                workload_ready = self.fixture.control / "inherited-mask-workload-ready"
                deadline = time.monotonic() + 10
                while (
                    (not ready_path.is_file() or not workload_ready.is_file())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.01)
                if not ready_path.is_file() or not workload_ready.is_file():
                    self.fail(
                        "inherited-mask adapter did not publish readiness: "
                        f"rc={process.poll()} stderr={read_capture(stderr_file)!r}"
                    )
                adapter = bind_fake_unshare_adapter(ready_path, coordinator=parent)
                self.assertEqual(
                    (adapter["supervisor"].pid, adapter["supervisor"].start_time),
                    (root.pid, root.start_time),
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                started = time.monotonic()
                exact_signal(root, signal.SIGTERM)
                self.assertEqual(process.wait(timeout=10), 143)
                self.assertLess(time.monotonic() - started, 8)
                validate_fake_unshare_terminal(
                    self.fixture.control / "fake-unshare-adapter-terminal.json",
                    ready=adapter,
                    expected_terminating=True,
                    expected_child_returncode=-signal.SIGKILL,
                )
            finally:
                observed.update(snapshot_descendants(root.pid))
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "inherited-mask adapter cleanup left live processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_fake_unshare_watchdog_rescans_late_same_session_group_after_sigterm(
        self,
    ) -> None:
        self.fixture.marker("exercise-late-session-group")
        workload = textwrap.dedent(
            r"""
            import os
            from pathlib import Path
            import subprocess
            import sys
            import time

            control = Path(sys.argv[1])

            def publish_identity(path: Path) -> None:
                text = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="ascii")
                fields = text[text.rfind(") ") + 2:].split()
                payload = " ".join(
                    (
                        str(os.getpid()), fields[1], fields[2], fields[3],
                        fields[0], fields[19],
                    )
                )
                partial = path.with_name(path.name + f".partial.{os.getpid()}")
                partial.write_text(payload, encoding="ascii")
                with partial.open("rb") as stream:
                    os.fsync(stream.fileno())
                os.replace(partial, path)

            os.setpgid(0, 0)
            publish_identity(control / "late-session-spawner")
            deadline = time.monotonic() + 10
            while (
                not (control / "watchdog-first-snapshot").is_file()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            if not (control / "watchdog-first-snapshot").is_file():
                raise SystemExit(91)
            late_code = (
                "import os\n"
                "from pathlib import Path\n"
                "import sys\n"
                "import time\n"
                "os.setpgid(0, 0)\n"
                "path = Path(sys.argv[1])\n"
                "text = Path(f'/proc/{os.getpid()}/stat').read_text(encoding='ascii')\n"
                "fields = text[text.rfind(') ') + 2:].split()\n"
                "payload = ' '.join((str(os.getpid()), fields[1], fields[2], fields[3], fields[0], fields[19]))\n"
                "partial = path.with_name(path.name + f'.partial.{os.getpid()}')\n"
                "partial.write_text(payload, encoding='ascii')\n"
                "stream = partial.open('rb')\n"
                "os.fsync(stream.fileno())\n"
                "stream.close()\n"
                "os.replace(partial, path)\n"
                "time.sleep(300)\n"
            )
            subprocess.Popen(
                [
                    "/usr/bin/python3", "-I", "-B", "-c", late_code,
                    str(control / "late-session-pid"),
                ]
            )
            time.sleep(300)
            """
        )
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        observed: dict[tuple[int, int], ProcessIdentity] = {}
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.fake_unshare_command(workload),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("fake-unshare supervisor disappeared before identity capture")
            adapter: dict[str, ProcessIdentity] | None = None
            spawner: ProcessIdentity | None = None
            late: ProcessIdentity | None = None
            try:
                ready_path = self.fixture.control / "fake-unshare-adapter-ready.json"
                spawner_path = self.fixture.control / "late-session-spawner"
                deadline = time.monotonic() + 10
                while (
                    (not ready_path.is_file() or not spawner_path.is_file())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.01)
                if not ready_path.is_file() or not spawner_path.is_file():
                    self.fail(
                        "late-group adapter did not publish its bound topology: "
                        f"rc={process.poll()} stdout={read_capture(stdout_file)!r} "
                        f"stderr={read_capture(stderr_file)!r}"
                    )
                adapter = bind_fake_unshare_adapter(ready_path, coordinator=parent)
                self.assertEqual(
                    (adapter["supervisor"].pid, adapter["supervisor"].start_time),
                    (root.pid, root.start_time),
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                spawner = read_fixture_process_identity(
                    spawner_path, "late-session spawner"
                )
                self.assertEqual(spawner.process_group, spawner.pid)
                self.assertEqual(spawner.session, adapter["child"].session)
                require_current_fixture_topology(spawner, "late-session spawner")
                observed[(spawner.pid, spawner.start_time)] = spawner

                exact_signal(root, signal.SIGTERM)
                late_path = self.fixture.control / "late-session-pid"
                visible_path = self.fixture.control / "late-session-visible"
                deadline = time.monotonic() + 10
                while (
                    (not late_path.is_file() or not visible_path.is_file())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                if not late_path.is_file() or not visible_path.is_file():
                    self.fail(
                        "watchdog did not expose the post-snapshot group before cleanup: "
                        f"rc={process.poll()} stdout={read_capture(stdout_file)!r} "
                        f"stderr={read_capture(stderr_file)!r}"
                    )
                late = read_fixture_process_identity(late_path, "late-session child")
                self.assertEqual(late.process_group, late.pid)
                self.assertEqual(late.session, adapter["child"].session)
                require_current_fixture_topology(late, "late-session child")
                observed[(late.pid, late.start_time)] = late
                (self.fixture.control / "late-session-bound").touch()

                self.assertEqual(process.wait(timeout=20), 143)
                validate_fake_unshare_terminal(
                    self.fixture.control / "fake-unshare-adapter-terminal.json",
                    ready=adapter,
                    expected_terminating=True,
                )
                self.assertTrue(wait_process_identity_stopped(spawner, 5))
                self.assertTrue(wait_process_identity_stopped(late, 5))
            finally:
                observed.update(snapshot_descendants(root.pid))
                if spawner is not None:
                    observed[(spawner.pid, spawner.start_time)] = spawner
                if late is not None:
                    observed[(late.pid, late.start_time)] = late
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "late-group adapter cleanup left non-zombie processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_fake_unshare_watchdog_drains_normal_same_session_background_group(
        self,
    ) -> None:
        workload = textwrap.dedent(
            r"""
            import os
            from pathlib import Path
            import subprocess
            import sys
            import time

            control = Path(sys.argv[1])
            background_code = (
                "import os\n"
                "from pathlib import Path\n"
                "import sys\n"
                "import time\n"
                "os.setpgid(0, 0)\n"
                "path = Path(sys.argv[1])\n"
                "text = Path(f'/proc/{os.getpid()}/stat').read_text(encoding='ascii')\n"
                "fields = text[text.rfind(') ') + 2:].split()\n"
                "payload = ' '.join((str(os.getpid()), fields[1], fields[2], fields[3], fields[0], fields[19]))\n"
                "partial = path.with_name(path.name + f'.partial.{os.getpid()}')\n"
                "partial.write_text(payload, encoding='ascii')\n"
                "stream = partial.open('rb')\n"
                "os.fsync(stream.fileno())\n"
                "stream.close()\n"
                "os.replace(partial, path)\n"
                "time.sleep(300)\n"
            )
            subprocess.Popen(
                [
                    "/usr/bin/python3", "-I", "-B", "-c", background_code,
                    str(control / "normal-session-background"),
                ]
            )
            deadline = time.monotonic() + 10
            while (
                not (control / "normal-session-background").is_file()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            if not (control / "normal-session-background").is_file():
                raise SystemExit(92)
            deadline = time.monotonic() + 10
            while (
                not (control / "normal-session-parent-release").is_file()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            if not (control / "normal-session-parent-release").is_file():
                raise SystemExit(93)
            raise SystemExit(0)
            """
        )
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        observed: dict[tuple[int, int], ProcessIdentity] = {}
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.fake_unshare_command(workload),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("fake-unshare supervisor disappeared before identity capture")
            adapter: dict[str, ProcessIdentity] | None = None
            background: ProcessIdentity | None = None
            try:
                ready_path = self.fixture.control / "fake-unshare-adapter-ready.json"
                background_path = self.fixture.control / "normal-session-background"
                deadline = time.monotonic() + 10
                while (
                    (not ready_path.is_file() or not background_path.is_file())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.01)
                if not ready_path.is_file() or not background_path.is_file():
                    self.fail(
                        "normal-exit adapter did not publish its bound topology: "
                        f"rc={process.poll()} stdout={read_capture(stdout_file)!r} "
                        f"stderr={read_capture(stderr_file)!r}"
                    )
                adapter = bind_fake_unshare_adapter(ready_path, coordinator=parent)
                self.assertEqual(
                    (adapter["supervisor"].pid, adapter["supervisor"].start_time),
                    (root.pid, root.start_time),
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                background = read_fixture_process_identity(
                    background_path, "normal same-session background"
                )
                self.assertEqual(background.process_group, background.pid)
                self.assertEqual(background.session, adapter["child"].session)
                require_current_fixture_topology(
                    background, "normal same-session background"
                )
                observed[(background.pid, background.start_time)] = background
                (self.fixture.control / "normal-session-parent-release").touch()

                self.assertEqual(process.wait(timeout=20), 0)
                validate_fake_unshare_terminal(
                    self.fixture.control / "fake-unshare-adapter-terminal.json",
                    ready=adapter,
                    expected_terminating=False,
                    expected_child_returncode=0,
                )
                self.assertTrue(wait_process_identity_stopped(background, 5))
            finally:
                observed.update(snapshot_descendants(root.pid))
                if background is not None:
                    observed[(background.pid, background.start_time)] = background
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "normal background-group adapter cleanup left live processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_fake_unshare_watchdog_failure_dominates_sigterm_status(self) -> None:
        self.fixture.marker("force-watchdog-failure")
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        observed: dict[tuple[int, int], ProcessIdentity] = {}
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            workload = (
                "from pathlib import Path; import sys, time; "
                "Path(sys.argv[1], 'watchdog-failure-workload-ready').write_text("
                "'ready', encoding='ascii'); time.sleep(300)"
            )
            process = subprocess.Popen(
                self.fixture.fake_unshare_command(workload),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("fake-unshare supervisor disappeared before identity capture")
            adapter: dict[str, ProcessIdentity] | None = None
            try:
                ready_path = self.fixture.control / "fake-unshare-adapter-ready.json"
                workload_ready = self.fixture.control / "watchdog-failure-workload-ready"
                deadline = time.monotonic() + 10
                while (
                    (not ready_path.is_file() or not workload_ready.is_file())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.01)
                if not ready_path.is_file() or not workload_ready.is_file():
                    self.fail(
                        "watchdog-failure adapter did not publish readiness: "
                        f"rc={process.poll()} stderr={read_capture(stderr_file)!r}"
                    )
                adapter = bind_fake_unshare_adapter(ready_path, coordinator=parent)
                self.assertEqual(
                    (adapter["supervisor"].pid, adapter["supervisor"].start_time),
                    (root.pid, root.start_time),
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                exact_signal(root, signal.SIGTERM)
                self.assertEqual(process.wait(timeout=20), 1)
                stderr = read_capture(stderr_file)
                self.assertIn("fake unshare watchdog failed: 79", stderr)
                self.assertNotEqual(process.returncode, 143)
                validate_fake_unshare_terminal(
                    self.fixture.control / "fake-unshare-adapter-terminal.json",
                    ready=adapter,
                    expected_terminating=True,
                    expected_child_returncode=-signal.SIGKILL,
                    expected_watchdog_status=79,
                )
            finally:
                observed.update(snapshot_descendants(root.pid))
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "watchdog-failure adapter cleanup left live processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_fake_unshare_watchdog_pre_drain_failure_is_bounded_and_non_sigterm(
        self,
    ) -> None:
        self.fixture.marker("force-watchdog-pre-drain-failure")
        workload = textwrap.dedent(
            """
            import os
            from pathlib import Path
            import sys
            import time

            os.setpgid(0, 0)
            Path(sys.argv[1], "pre-drain-workload-ready").write_text(
                "ready", encoding="ascii"
            )
            time.sleep(300)
            """
        )
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        observed: dict[tuple[int, int], ProcessIdentity] = {}
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.fake_unshare_command(workload),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("fake-unshare supervisor disappeared before identity capture")
            adapter: dict[str, ProcessIdentity] | None = None
            try:
                ready_path = self.fixture.control / "fake-unshare-adapter-ready.json"
                workload_ready = self.fixture.control / "pre-drain-workload-ready"
                deadline = time.monotonic() + 10
                while (
                    (not ready_path.is_file() or not workload_ready.is_file())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.01)
                if not ready_path.is_file() or not workload_ready.is_file():
                    self.fail(
                        "pre-drain failure adapter did not publish readiness: "
                        f"rc={process.poll()} stderr={read_capture(stderr_file)!r}"
                    )
                adapter = bind_fake_unshare_adapter(ready_path, coordinator=parent)
                self.assertEqual(
                    (adapter["supervisor"].pid, adapter["supervisor"].start_time),
                    (root.pid, root.start_time),
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                started = time.monotonic()
                exact_signal(root, signal.SIGTERM)
                self.assertEqual(process.wait(timeout=15), 1)
                self.assertLess(time.monotonic() - started, 12)
                stderr = read_capture(stderr_file)
                self.assertIn(
                    "fake unshare watchdog exited before its bound child: 80", stderr
                )
                self.assertNotEqual(process.returncode, 143)
                validate_fake_unshare_terminal(
                    self.fixture.control / "fake-unshare-adapter-terminal.json",
                    ready=adapter,
                    expected_terminating=True,
                    expected_child_returncode=-signal.SIGKILL,
                    expected_watchdog_status=80,
                    expected_watchdog_exited_before_child=True,
                )
            finally:
                observed.update(snapshot_descendants(root.pid))
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "pre-drain watchdog-failure cleanup left live processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_fake_unshare_watchdog_stall_after_short_child_uses_original_deadline(
        self,
    ) -> None:
        self.fixture.marker("force-watchdog-stop-before-drain")
        workload = textwrap.dedent(
            """
            from pathlib import Path
            import sys
            import time

            Path(sys.argv[1], "short-child-workload-ready").write_text(
                "ready", encoding="ascii"
            )
            time.sleep(0.25)
            raise SystemExit(0)
            """
        )
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        observed: dict[tuple[int, int], ProcessIdentity] = {}
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.fake_unshare_command(workload),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("fake-unshare supervisor disappeared before identity capture")
            adapter: dict[str, ProcessIdentity] | None = None
            try:
                ready_path = self.fixture.control / "fake-unshare-adapter-ready.json"
                workload_ready = self.fixture.control / "short-child-workload-ready"
                deadline = time.monotonic() + 10
                while (
                    (not ready_path.is_file() or not workload_ready.is_file())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.01)
                if not ready_path.is_file() or not workload_ready.is_file():
                    self.fail(
                        "short-child stalled-watchdog adapter omitted readiness: "
                        f"rc={process.poll()} stderr={read_capture(stderr_file)!r}"
                    )
                adapter = bind_fake_unshare_adapter(ready_path, coordinator=parent)
                self.assertEqual(
                    (adapter["supervisor"].pid, adapter["supervisor"].start_time),
                    (root.pid, root.start_time),
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                started = time.monotonic()
                exact_signal(root, signal.SIGTERM)
                self.assertEqual(process.wait(timeout=10), 1)
                elapsed = time.monotonic() - started
                self.assertGreater(elapsed, 4)
                self.assertLess(elapsed, 8)
                stderr = read_capture(stderr_file)
                self.assertIn("fake unshare watchdog cleanup timed out: 124", stderr)
                self.assertNotEqual(process.returncode, 143)
                validate_fake_unshare_terminal(
                    self.fixture.control / "fake-unshare-adapter-terminal.json",
                    ready=adapter,
                    expected_terminating=True,
                    expected_child_returncode=0,
                    expected_watchdog_status=124,
                    expected_watchdog_exited_before_child=False,
                    expected_watchdog_cleanup_timed_out=True,
                )
            finally:
                observed.update(snapshot_descendants(root.pid))
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "short-child stalled-watchdog cleanup left live processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_fake_unshare_watchdog_stall_after_child_then_late_signal_is_bounded(
        self,
    ) -> None:
        self.fixture.marker("force-watchdog-stop-before-drain")
        workload = textwrap.dedent(
            """
            from pathlib import Path
            import sys
            import time

            control = Path(sys.argv[1])
            (control / "late-signal-workload-ready").write_text(
                "ready", encoding="ascii"
            )
            deadline = time.monotonic() + 10
            while (
                not (control / "late-signal-workload-release").is_file()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            if not (control / "late-signal-workload-release").is_file():
                raise SystemExit(92)
            time.sleep(0.1)
            raise SystemExit(0)
            """
        )
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        observed: dict[tuple[int, int], ProcessIdentity] = {}
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.fake_unshare_command(workload),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("fake-unshare supervisor disappeared before identity capture")
            adapter: dict[str, ProcessIdentity] | None = None
            try:
                ready_path = self.fixture.control / "fake-unshare-adapter-ready.json"
                workload_ready = self.fixture.control / "late-signal-workload-ready"
                deadline = time.monotonic() + 10
                while (
                    (not ready_path.is_file() or not workload_ready.is_file())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.01)
                if not ready_path.is_file() or not workload_ready.is_file():
                    self.fail(
                        "late-signal stalled-watchdog adapter omitted readiness: "
                        f"rc={process.poll()} stderr={read_capture(stderr_file)!r}"
                    )
                adapter = bind_fake_unshare_adapter(ready_path, coordinator=parent)
                self.assertEqual(
                    (adapter["supervisor"].pid, adapter["supervisor"].start_time),
                    (root.pid, root.start_time),
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                (self.fixture.control / "late-signal-workload-release").touch()
                stopped_marker = self.fixture.control / "watchdog-stopped-before-drain"
                deadline = time.monotonic() + 10
                current_watchdog = read_process_identity(adapter["watchdog"].pid)
                while (
                    (
                        not stopped_marker.is_file()
                        or current_watchdog is None
                        or current_watchdog.start_time != adapter["watchdog"].start_time
                        or current_watchdog.state != "T"
                    )
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                    current_watchdog = read_process_identity(adapter["watchdog"].pid)
                if (
                    not stopped_marker.is_file()
                    or current_watchdog is None
                    or current_watchdog.start_time != adapter["watchdog"].start_time
                    or current_watchdog.state != "T"
                ):
                    self.fail(
                        "watchdog did not stop after the normal child completed: "
                        f"rc={process.poll()} current={current_watchdog!r} "
                        f"stderr={read_capture(stderr_file)!r}"
                    )
                started = time.monotonic()
                exact_signal(root, signal.SIGTERM)
                self.assertEqual(process.wait(timeout=10), 1)
                elapsed = time.monotonic() - started
                self.assertGreater(elapsed, 4)
                self.assertLess(elapsed, 8)
                stderr = read_capture(stderr_file)
                self.assertIn("fake unshare watchdog cleanup timed out: 124", stderr)
                validate_fake_unshare_terminal(
                    self.fixture.control / "fake-unshare-adapter-terminal.json",
                    ready=adapter,
                    expected_terminating=True,
                    expected_child_returncode=0,
                    expected_watchdog_status=124,
                    expected_watchdog_exited_before_child=False,
                    expected_watchdog_cleanup_timed_out=True,
                )
            finally:
                observed.update(snapshot_descendants(root.pid))
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "late-signal stalled-watchdog cleanup left live processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_fake_unshare_watchdog_stall_is_bounded_and_non_sigterm(self) -> None:
        self.fixture.marker("force-watchdog-stop-before-drain")
        workload = textwrap.dedent(
            """
            from pathlib import Path
            import sys
            import time

            Path(sys.argv[1], "stalled-watchdog-workload-ready").write_text(
                "ready", encoding="ascii"
            )
            time.sleep(300)
            """
        )
        parent = read_process_identity(os.getpid())
        self.assertIsNotNone(parent)
        assert parent is not None
        observed: dict[tuple[int, int], ProcessIdentity] = {}
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.fake_unshare_command(workload),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("fake-unshare supervisor disappeared before identity capture")
            adapter: dict[str, ProcessIdentity] | None = None
            try:
                ready_path = self.fixture.control / "fake-unshare-adapter-ready.json"
                workload_ready = self.fixture.control / "stalled-watchdog-workload-ready"
                deadline = time.monotonic() + 10
                while (
                    (not ready_path.is_file() or not workload_ready.is_file())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.01)
                if not ready_path.is_file() or not workload_ready.is_file():
                    self.fail(
                        "stalled-watchdog adapter did not publish readiness: "
                        f"rc={process.poll()} stderr={read_capture(stderr_file)!r}"
                    )
                adapter = bind_fake_unshare_adapter(ready_path, coordinator=parent)
                self.assertEqual(
                    (adapter["supervisor"].pid, adapter["supervisor"].start_time),
                    (root.pid, root.start_time),
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                started = time.monotonic()
                exact_signal(root, signal.SIGTERM)
                self.assertEqual(process.wait(timeout=15), 1)
                self.assertLess(time.monotonic() - started, 12)
                stderr = read_capture(stderr_file)
                self.assertIn(
                    "fake unshare watchdog exited before its bound child: 124", stderr
                )
                self.assertNotEqual(process.returncode, 143)
                validate_fake_unshare_terminal(
                    self.fixture.control / "fake-unshare-adapter-terminal.json",
                    ready=adapter,
                    expected_terminating=True,
                    expected_child_returncode=-signal.SIGKILL,
                    expected_watchdog_status=124,
                    expected_watchdog_exited_before_child=True,
                    expected_watchdog_cleanup_timed_out=True,
                )
            finally:
                observed.update(snapshot_descendants(root.pid))
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "stalled-watchdog adapter cleanup left live processes: "
                            f"{cleanup_survivors!r}"
                        )

    def test_signal_terminates_active_process_group_and_preserves_selector_inode(self) -> None:
        self.fixture.marker("hang-quickpkg")
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                self.fixture.command(),
                cwd="/",
                env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            root = read_process_identity(process.pid)
            if root is None:
                process.kill()
                process.wait(timeout=3)
                self.fail("checkpoint coordinator disappeared before identity capture")
            self.assertEqual((root.process_group, root.session), (root.pid, root.pid))
            observed: dict[tuple[int, int], ProcessIdentity] = {}
            try:
                active = self.fixture.control / "active-pids"
                deadline = time.monotonic() + 20
                while not active.exists() and process.poll() is None and time.monotonic() < deadline:
                    observed.update(snapshot_descendants(root.pid))
                    time.sleep(0.05)
                if not active.exists():
                    survivors = terminate_process_tree(process, root, observed)
                    stdout = read_capture(stdout_file)
                    stderr = read_capture(stderr_file)
                    self.fail(
                        "tracked child did not become active: "
                        f"rc={process.returncode} survivors={survivors!r}\n{stdout}\n{stderr}"
                    )
                pids = [int(item) for item in active.read_text().splitlines()]
                active_identities = {
                    identity.pid: identity
                    for pid in pids
                    if (identity := read_process_identity(pid)) is not None
                }
                self.assertEqual(set(active_identities), set(pids))
                observed.update(snapshot_descendants(root.pid))
                adapter = bind_fake_unshare_adapter(
                    self.fixture.control / "fake-unshare-adapter-ready.json",
                    coordinator=root,
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in adapter.values()
                    }
                )
                try:
                    os.killpg(root.process_group, signal.SIGTERM)
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    survivors = terminate_process_tree(process, root, observed)
                    stdout = read_capture(stdout_file)
                    stderr = read_capture(stderr_file)
                    self.fail(
                        "checkpoint did not handle SIGTERM within 20 seconds: "
                        f"survivors={survivors!r}\n{stdout}\n{stderr}"
                    )
                stdout = read_capture(stdout_file)
                stderr = read_capture(stderr_file)
                self.assertEqual(process.returncode, 143, (stdout, stderr))
                validate_fake_unshare_terminal(
                    self.fixture.control / "fake-unshare-adapter-terminal.json",
                    ready=adapter,
                    expected_terminating=True,
                )
                observed.update(
                    {
                        (identity.pid, identity.start_time): identity
                        for identity in active_identities.values()
                    }
                )
                residue_deadline = time.monotonic() + 10
                survivors = surviving_processes(observed)
                while survivors and time.monotonic() < residue_deadline:
                    time.sleep(0.05)
                    survivors = surviving_processes(observed)
                if survivors:
                    self.fail(
                        "tracked processes survived: "
                        f"{survivors!r}\nstdout:\n{stdout}\nstderr:\n{stderr}"
                    )
            finally:
                observed.update(snapshot_descendants(root.pid))
                if process.poll() is None or surviving_processes(observed):
                    cleanup_survivors = terminate_process_tree(process, root, observed)
                    if cleanup_survivors and sys.exc_info()[0] is None:
                        self.fail(
                            "signal-test cleanup left non-zombie processes: "
                            f"{cleanup_survivors!r}"
                        )
        self.assert_selector_unchanged()
        failure = (self.report() / "failure.txt").read_text()
        self.assertIn("status=143", failure)
        self.assertIn("selector_unchanged=true", failure)

    def test_source_target_and_digest_are_explicitly_bound(self) -> None:
        command = self.fixture.command()
        digest_index = command.index("--expected-source-packages-sha256") + 1
        command[digest_index] = "0" * 64
        result = self.fixture.run_command(command)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source Packages digest", result.stderr)
        self.assert_selector_unchanged()


@unittest.skipUnless(
    os.environ.get("GENTOO_OPT_RUN_CHECKPOINT_HOST_CAPABILITIES") == "1",
    "set GENTOO_OPT_RUN_CHECKPOINT_HOST_CAPABILITIES=1 for real host primitives",
)
class CheckpointHostCapabilityTest(unittest.TestCase):
    @staticmethod
    def _start_identity(pid: int) -> tuple[int, int] | None:
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            return None
        return int(fields[2]), int(fields[19])

    @staticmethod
    def _children(pid: int) -> tuple[int, ...] | None:
        try:
            payload = Path(f"/proc/{pid}/task/{pid}/children").read_text()
        except (FileNotFoundError, ProcessLookupError):
            return None
        return tuple(int(field) for field in payload.split())

    @staticmethod
    def _group_exists(process_group: int) -> bool:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            observed = read_process_identity(int(entry.name))
            if observed is not None and observed.process_group == process_group:
                return True
        return False

    def test_host_pidfd_open_and_send_signal_are_functional(self) -> None:
        child = subprocess.Popen(["/usr/bin/sleep", "300"])
        try:
            descriptor = os.pidfd_open(child.pid, 0)
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            finally:
                os.close(descriptor)
            self.assertEqual(child.wait(timeout=10), -signal.SIGTERM)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_host_file_and_directory_sync_are_functional(self) -> None:
        sync = Path("/usr/bin/sync")
        if not sync.is_file():
            self.skipTest("required host primitive is absent: /usr/bin/sync")
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            payload = root / "payload"
            payload.write_bytes(b"checkpoint-fsync-probe\n")
            for target in (payload, root):
                result = subprocess.run(
                    [str(sync), "-f", "--", str(target)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    (target, result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace")),
                )

    def test_host_kill_child_pid_namespace_is_functional(self) -> None:
        namespace_code = textwrap.dedent(
            """\
            import os
            import signal
            import socket
            import sys

            parent_network_namespace = (int(sys.argv[1]), int(sys.argv[2]))
            current_network_stat = os.stat("/proc/self/ns/net")
            current_network_namespace = (
                current_network_stat.st_dev,
                current_network_stat.st_ino,
            )
            if current_network_namespace == parent_network_namespace:
                raise SystemExit("network namespace was not isolated")
            if sorted(name for _index, name in socket.if_nameindex()) != ["lo"]:
                raise SystemExit("isolated network namespace has a non-loopback interface")

            descendant = os.fork()
            if descendant == 0:
                os.setsid()
                signal.pause()
                raise SystemExit(90)
            signal.pause()
            raise SystemExit(91)
            """
        )
        parent_network_stat = os.stat("/proc/self/ns/net")
        with tempfile.TemporaryFile() as stderr_file:
            supervisor = subprocess.Popen(
                [
                    "/usr/bin/unshare",
                    "--pid",
                    "--net",
                    "--fork",
                    "--kill-child=KILL",
                    "--mount-proc",
                    "--",
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    namespace_code,
                    str(parent_network_stat.st_dev),
                    str(parent_network_stat.st_ino),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
                start_new_session=True,
            )
            child_pid: int | None = None
            descendant_pid: int | None = None
            supervisor_pidfd = -1
            child_pidfd = -1
            descendant_pidfd = -1
            supervisor_identity = read_process_identity(supervisor.pid)
            if supervisor_identity is None:
                supervisor.kill()
                supervisor.wait(timeout=3)
                self.fail("unshare supervisor disappeared before identity capture")
            self.assertEqual(
                (supervisor_identity.process_group, supervisor_identity.session),
                (supervisor.pid, supervisor.pid),
            )
            deadline = time.monotonic() + 10
            try:
                while time.monotonic() < deadline and supervisor.poll() is None:
                    children = self._children(supervisor.pid)
                    if children is not None and len(children) > 1:
                        self.fail(f"unshare supervisor has unexpected children: {children}")
                    if children:
                        child_pid = children[0]
                        break
                    time.sleep(0.02)
                if child_pid is None:
                    stderr = read_capture(stderr_file)
                    self.fail(f"unshare did not publish a namespace child: {stderr}")
                child_identity = read_process_identity(child_pid)
                self.assertIsNotNone(child_identity)
                assert child_identity is not None
                self.assertEqual(child_identity.ppid, supervisor.pid)
                self.assertEqual(child_identity.process_group, supervisor.pid)
                child_network_stat = os.stat(f"/proc/{child_pid}/ns/net")
                self.assertNotEqual(
                    (child_network_stat.st_dev, child_network_stat.st_ino),
                    (parent_network_stat.st_dev, parent_network_stat.st_ino),
                )
                network_devices = Path(f"/proc/{child_pid}/net/dev").read_text(
                    encoding="ascii"
                )
                interfaces = sorted(
                    line.split(":", 1)[0].strip()
                    for line in network_devices.splitlines()
                    if ":" in line
                )
                self.assertEqual(interfaces, ["lo"])
                while time.monotonic() < deadline:
                    descendants = self._children(child_pid)
                    if descendants is not None and len(descendants) > 1:
                        self.fail(f"namespace child has unexpected descendants: {descendants}")
                    if descendants:
                        descendant_pid = descendants[0]
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(descendant_pid, "namespace child did not create its setsid descendant")
                assert descendant_pid is not None
                descendant_identity = read_process_identity(descendant_pid)
                self.assertIsNotNone(descendant_identity)
                assert descendant_identity is not None
                self.assertEqual(descendant_identity.ppid, child_pid)
                self.assertEqual(
                    (descendant_identity.process_group, descendant_identity.session),
                    (descendant_pid, descendant_pid),
                )
                supervisor_pidfd = os.pidfd_open(supervisor.pid, 0)
                child_pidfd = os.pidfd_open(child_pid, 0)
                descendant_pidfd = os.pidfd_open(descendant_pid, 0)
                for expected in (
                    supervisor_identity,
                    child_identity,
                    descendant_identity,
                ):
                    repeated = read_process_identity(expected.pid)
                    self.assertIsNotNone(repeated)
                    assert repeated is not None
                    # Scheduling state is transient (for example R -> S) and is
                    # not part of process incarnation identity.  Revalidate
                    # every stable relationship and the start-time witness.
                    self.assertEqual(
                        (
                            repeated.pid,
                            repeated.ppid,
                            repeated.process_group,
                            repeated.session,
                            repeated.start_time,
                        ),
                        (
                            expected.pid,
                            expected.ppid,
                            expected.process_group,
                            expected.session,
                            expected.start_time,
                        ),
                    )
                signal.pidfd_send_signal(supervisor_pidfd, signal.SIGKILL)
                self.assertEqual(supervisor.wait(timeout=10), -signal.SIGKILL)
                residue_deadline = time.monotonic() + 10
                while time.monotonic() < residue_deadline:
                    if (
                        not process_identity_is_current(child_identity)
                        and not process_identity_is_current(descendant_identity)
                        and not self._group_exists(supervisor_identity.process_group)
                        and not self._group_exists(descendant_identity.process_group)
                    ):
                        break
                    time.sleep(0.02)
                self.assertFalse(
                    process_identity_is_current(child_identity),
                    "--kill-child=KILL left the exact namespace child alive",
                )
                self.assertFalse(
                    process_identity_is_current(descendant_identity),
                    "PID namespace teardown left the escaped setsid descendant alive",
                )
                self.assertFalse(self._group_exists(supervisor_identity.process_group))
                self.assertFalse(self._group_exists(descendant_identity.process_group))
            finally:
                if supervisor.poll() is None:
                    if supervisor_pidfd >= 0:
                        signal.pidfd_send_signal(supervisor_pidfd, signal.SIGKILL)
                    else:
                        supervisor.kill()
                    supervisor.wait(timeout=10)
                for pid, descriptor in (
                    (child_pid, child_pidfd),
                    (descendant_pid, descendant_pidfd),
                ):
                    if pid is not None and descriptor >= 0 and read_process_identity(pid) is not None:
                        try:
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                for descriptor in (descendant_pidfd, child_pidfd, supervisor_pidfd):
                    if descriptor >= 0:
                        os.close(descriptor)


if __name__ == "__main__":
    unittest.main()

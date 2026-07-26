from __future__ import annotations

import ctypes
import fcntl
import hashlib
import json
import os
import re
import select
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
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


PR_SET_PDEATHSIG = 1
PR_SET_CHILD_SUBREAPER = 36
PR_GET_CHILD_SUBREAPER = 37
SUPERVISOR_NATURAL_EXIT_SECONDS = 3.0
SUPERVISOR_TERM_SECONDS = 3.0
SUPERVISOR_KILL_SECONDS = 3.0
TARGET_READY_SECONDS = 3.0
_CONTAINMENT_LOCK = threading.Lock()


def set_prctl(option: int, argument: int, label: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, argument, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, f"{label}: {os.strerror(error_number)}")


def get_child_subreaper() -> int:
    value = ctypes.c_int()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_GET_CHILD_SUBREAPER, ctypes.byref(value), 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            f"checkpoint fixture PR_GET_CHILD_SUBREAPER: {os.strerror(error_number)}",
        )
    return value.value


def identity_payload(identity: ProcessIdentity) -> dict[str, int | str]:
    return {
        "pid": identity.pid,
        "ppid": identity.ppid,
        "process_group": identity.process_group,
        "session": identity.session,
        "state": identity.state,
        "start_time": identity.start_time,
    }


def signal_identity_set(
    identities: dict[tuple[int, int], ProcessIdentity],
    signum: signal.Signals,
) -> None:
    if not identities:
        return
    first_key = next(iter(identities))
    first = identities[first_key]
    remaining = dict(identities)
    del remaining[first_key]
    signal_observed_processes(first, remaining, signum)


def write_supervisor_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(path.name + f".partial.{os.getpid()}")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def supervise_command_process(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    stdout_descriptor: int,
    stderr_descriptor: int,
    receipt: Path,
    expected_parent: int,
) -> None:
    target_pid: int | None = None
    target_start_time: int | None = None
    target_returncode: int | None = None
    timed_out = False
    residual_before_cleanup: dict[tuple[int, int], ProcessIdentity] = {}
    reaped_pids: list[int] = []
    survivors: dict[tuple[int, int], ProcessIdentity] = {}

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
            if identity.state not in {"Z", "X", "x"}
        }

    def drain_with_signal(
        requested_signal: signal.Signals,
        seconds: float,
    ) -> dict[tuple[int, int], ProcessIdentity]:
        signalled: set[tuple[int, int]] = set()
        deadline = time.monotonic() + seconds
        survivors = current_descendants()
        while survivors and time.monotonic() < deadline:
            newly_observed = {
                key: identity
                for key, identity in survivors.items()
                if key not in signalled
            }
            if newly_observed:
                signal_identity_set(newly_observed, requested_signal)
                signalled.update(newly_observed)
            reap_available()
            time.sleep(0.02)
            survivors = current_descendants()
        reap_available()
        return current_descendants()

    try:
        if os.getppid() != expected_parent:
            raise RuntimeError("supervisor parent changed before parent-death binding")
        set_prctl(PR_SET_PDEATHSIG, signal.SIGKILL, "supervisor PR_SET_PDEATHSIG")
        if os.getppid() != expected_parent:
            raise RuntimeError("supervisor parent changed during parent-death binding")
        set_prctl(
            PR_SET_CHILD_SUBREAPER,
            1,
            "checkpoint fixture PR_SET_CHILD_SUBREAPER",
        )
        os.setsid()

        supervisor_pid = os.getpid()
        ready_reader, ready_writer = os.pipe2(os.O_CLOEXEC)
        release_reader, release_writer = os.pipe2(os.O_CLOEXEC)
        target_pid = os.fork()
        if target_pid == 0:
            try:
                os.close(ready_reader)
                os.close(release_writer)
                os.setsid()
                if os.getppid() != supervisor_pid:
                    raise RuntimeError("target supervisor changed before parent-death binding")
                set_prctl(PR_SET_PDEATHSIG, signal.SIGKILL, "target PR_SET_PDEATHSIG")
                if os.getppid() != supervisor_pid:
                    raise RuntimeError("target supervisor changed during parent-death binding")
                null_descriptor = os.open("/dev/null", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
                os.dup2(null_descriptor, 0)
                os.dup2(stdout_descriptor, 1)
                os.dup2(stderr_descriptor, 2)
                if null_descriptor > 2:
                    os.close(null_descriptor)
                if stdout_descriptor > 2:
                    os.close(stdout_descriptor)
                if stderr_descriptor > 2 and stderr_descriptor != stdout_descriptor:
                    os.close(stderr_descriptor)
                os.chdir(cwd)
                if os.write(ready_writer, b"R") != 1:
                    raise RuntimeError("target readiness barrier write was incomplete")
                os.close(ready_writer)
                if os.read(release_reader, 1) != b"R":
                    raise RuntimeError("target exec release barrier was not satisfied")
                os.close(release_reader)
                os.execvpe(command[0], command, env)
            except BaseException as error:
                try:
                    os.write(2, f"fixture target exec failed: {error!r}\n".encode())
                finally:
                    os._exit(127)

        os.close(ready_writer)
        os.close(release_reader)
        os.close(stdout_descriptor)
        if stderr_descriptor != stdout_descriptor:
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
            if os.write(release_writer, b"R") != 1:
                raise RuntimeError("target exec release barrier write was incomplete")
        finally:
            os.close(ready_reader)
            os.close(release_writer)

        target_deadline = time.monotonic() + timeout
        while target_returncode is None and time.monotonic() < target_deadline:
            try:
                waited_pid, wait_status = os.waitpid(target_pid, os.WNOHANG)
            except ChildProcessError as error:
                raise RuntimeError("target was reaped outside its supervisor") from error
            if waited_pid == target_pid:
                reaped_pids.append(waited_pid)
                target_returncode = os.waitstatus_to_exitcode(wait_status)
                break
            time.sleep(0.02)

        if target_returncode is None:
            timed_out = True
            residual_before_cleanup = current_descendants()
        else:
            natural_deadline = time.monotonic() + SUPERVISOR_NATURAL_EXIT_SECONDS
            while time.monotonic() < natural_deadline:
                reap_available()
                residual_before_cleanup = current_descendants()
                if not residual_before_cleanup:
                    break
                time.sleep(0.02)

        survivors = current_descendants()
        if survivors:
            survivors = drain_with_signal(signal.SIGTERM, SUPERVISOR_TERM_SECONDS)
        if survivors:
            survivors = drain_with_signal(signal.SIGKILL, SUPERVISOR_KILL_SECONDS)
        reap_available()
        survivors = current_descendants()
        write_supervisor_json(
            receipt,
            {
                "cleanup_survivors": [
                    identity_payload(identity)
                    for identity in sorted(survivors.values())
                ],
                "reaped_pids": sorted(set(reaped_pids)),
                "residual_before_cleanup": [
                    identity_payload(identity)
                    for identity in sorted(residual_before_cleanup.values())
                ],
                "schema_version": 1,
                "target_pid": target_pid,
                "target_returncode": target_returncode,
                "target_start_time": target_start_time,
                "timed_out": timed_out,
            },
        )
        os._exit(0)
    except BaseException as error:
        survivors = {}
        try:
            survivors = current_descendants()
            if survivors:
                survivors = drain_with_signal(signal.SIGTERM, SUPERVISOR_TERM_SECONDS)
            if survivors:
                survivors = drain_with_signal(signal.SIGKILL, SUPERVISOR_KILL_SECONDS)
            reap_available()
            write_supervisor_json(
                receipt,
                {
                    "cleanup_survivors": [
                        identity_payload(identity)
                        for identity in sorted(survivors.values())
                    ],
                    "error": f"{type(error).__name__}: {error}",
                    "reaped_pids": sorted(set(reaped_pids)),
                    "schema_version": 1,
                    "target_pid": target_pid,
                    "target_returncode": target_returncode,
                    "target_start_time": target_start_time,
                    "timed_out": timed_out,
                },
            )
        finally:
            os._exit(70)


def post_baseline_descendants(
    parent_pid: int,
    baseline: dict[tuple[int, int], ProcessIdentity],
) -> dict[tuple[int, int], ProcessIdentity]:
    return {
        key: identity
        for key, identity in snapshot_descendants(parent_pid).items()
        if key not in baseline
    }


def reap_exact_direct_children(
    parent_pid: int,
    identities: dict[tuple[int, int], ProcessIdentity],
) -> None:
    for identity in identities.values():
        current = read_process_identity(identity.pid)
        if (
            current is None
            or current.start_time != identity.start_time
            or current.ppid != parent_pid
            or current.state not in {"Z", "X", "x"}
        ):
            continue
        try:
            os.waitpid(identity.pid, os.WNOHANG)
        except ChildProcessError:
            pass


def drain_post_baseline_descendants(
    parent_pid: int,
    baseline: dict[tuple[int, int], ProcessIdentity],
) -> tuple[dict[tuple[int, int], ProcessIdentity], list[ProcessIdentity]]:
    observed: dict[tuple[int, int], ProcessIdentity] = {}

    def drain(signum: signal.Signals, seconds: float) -> None:
        signalled: set[tuple[int, int]] = set()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            current = post_baseline_descendants(parent_pid, baseline)
            observed.update(current)
            newly_observed = {
                key: identity
                for key, identity in current.items()
                if key not in signalled and process_identity_is_live(identity)
            }
            if newly_observed:
                signal_identity_set(newly_observed, signum)
                signalled.update(newly_observed)
            reap_exact_direct_children(parent_pid, observed)
            current = post_baseline_descendants(parent_pid, baseline)
            observed.update(current)
            if not current:
                return
            time.sleep(0.02)

    drain(signal.SIGTERM, SUPERVISOR_TERM_SECONDS)
    if post_baseline_descendants(parent_pid, baseline):
        drain(signal.SIGKILL, SUPERVISOR_KILL_SECONDS)
    reap_exact_direct_children(parent_pid, observed)
    survivors = surviving_processes(observed)
    return observed, survivors


def terminate_forked_supervisor(
    supervisor_pid: int,
    supervisor_identity: ProcessIdentity,
    *,
    parent_pid: int,
    parent_baseline: dict[tuple[int, int], ProcessIdentity],
) -> tuple[dict[tuple[int, int], ProcessIdentity], list[ProcessIdentity]]:
    # The caller is itself temporarily a child subreaper.  Killing this exact
    # supervisor therefore reparents every surviving fixture descendant to the
    # caller instead of init, including a child that forked between /proc scans.
    if process_identity_is_live(supervisor_identity):
        try:
            os.kill(supervisor_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + SUPERVISOR_TERM_SECONDS
    while process_identity_is_live(supervisor_identity) and time.monotonic() < deadline:
        time.sleep(0.02)
    if process_identity_is_live(supervisor_identity):
        try:
            os.kill(supervisor_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        os.waitpid(supervisor_pid, 0)
    except ChildProcessError:
        pass
    return drain_post_baseline_descendants(parent_pid, parent_baseline)


def run_contained_command_under_subreaper(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    parent_pid: int,
    parent_baseline: dict[tuple[int, int], ProcessIdentity],
    started_pids: list[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    # The supervisor is a Linux child subreaper, so double-forked descendants
    # are adopted in-kernel even when every parent exits between /proc polls.
    # Regular files replace PIPE, preventing an escaped descriptor from turning
    # result collection into an unbounded EOF wait.
    with (
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
        tempfile.TemporaryDirectory(prefix="checkpoint-supervisor-") as directory,
    ):
        receipt = Path(directory) / "receipt.json"
        expected_parent = os.getpid()
        supervisor_pid = os.fork()
        if supervisor_pid == 0:
            supervise_command_process(
                command,
                cwd=cwd,
                env=env,
                timeout=timeout,
                stdout_descriptor=stdout_file.fileno(),
                stderr_descriptor=stderr_file.fileno(),
                receipt=receipt,
                expected_parent=expected_parent,
            )
            os._exit(71)

        supervisor_identity = read_process_identity(supervisor_pid)
        if supervisor_identity is None:
            try:
                os.waitpid(supervisor_pid, 0)
            except ChildProcessError:
                pass
            raise AssertionError("checkpoint supervisor disappeared before identity capture")
        supervisor_deadline = (
            time.monotonic()
            + timeout
            + SUPERVISOR_NATURAL_EXIT_SECONDS
            + SUPERVISOR_TERM_SECONDS
            + SUPERVISOR_KILL_SECONDS
            + 5
        )
        supervisor_wait_status: int | None = None
        while time.monotonic() < supervisor_deadline:
            waited_pid, wait_status = os.waitpid(supervisor_pid, os.WNOHANG)
            if waited_pid == supervisor_pid:
                supervisor_wait_status = wait_status
                break
            time.sleep(0.02)
        if supervisor_wait_status is None:
            observed, survivors = terminate_forked_supervisor(
                supervisor_pid,
                supervisor_identity,
                parent_pid=parent_pid,
                parent_baseline=parent_baseline,
            )
            raise AssertionError(
                "checkpoint child-subreaper exceeded its bounded deadline; "
                f"adopted={sorted(observed.values())!r}; survivors={survivors!r}"
            )

        stdout = read_capture(stdout_file)
        stderr = read_capture(stderr_file)
        if not receipt.is_file():
            raise AssertionError(
                "checkpoint child-subreaper exited without a receipt: "
                f"status={os.waitstatus_to_exitcode(supervisor_wait_status)}\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        target_pid = payload.get("target_pid")
        if not isinstance(target_pid, int) or target_pid <= 0:
            raise AssertionError(f"checkpoint supervisor returned an invalid target PID: {payload!r}")
        if started_pids is not None:
            started_pids.append(target_pid)
        cleanup_survivors = payload.get("cleanup_survivors")
        if cleanup_survivors:
            raise AssertionError(
                "checkpoint child-subreaper left non-zombie fixture processes: "
                f"{cleanup_survivors!r}\nstdout:\n{stdout}\nstderr:\n{stderr}"
            )
        if "error" in payload:
            raise AssertionError(
                f"checkpoint child-subreaper failed: {payload['error']}\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        residual = payload.get("residual_before_cleanup")
        timed_out = payload.get("timed_out") is True
        if residual and not timed_out:
            raise AssertionError(
                "checkpoint command exited with non-zombie fixture descendants: "
                f"{residual!r}; cleanup_survivors=[]\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        if timed_out:
            raise subprocess.TimeoutExpired(
                command,
                timeout,
                output=stdout,
                stderr=stderr,
            )
        returncode = payload.get("target_returncode")
        if not isinstance(returncode, int):
            raise AssertionError(f"checkpoint supervisor omitted target return code: {payload!r}")
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def run_contained_command(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    started_pids: list[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    # PR_SET_CHILD_SUBREAPER is process-wide.  Serialize fixture invocations,
    # preserve the caller's original setting, and record every pre-existing
    # descendant by exact PID/start identity so fallback cleanup cannot touch it.
    with _CONTAINMENT_LOCK:
        parent_pid = os.getpid()
        original_subreaper = get_child_subreaper()
        parent_baseline = snapshot_descendants(parent_pid)
        if original_subreaper != 1:
            set_prctl(
                PR_SET_CHILD_SUBREAPER,
                1,
                "checkpoint fixture parent PR_SET_CHILD_SUBREAPER",
            )
        try:
            return run_contained_command_under_subreaper(
                command,
                cwd=cwd,
                env=env,
                timeout=timeout,
                parent_pid=parent_pid,
                parent_baseline=parent_baseline,
                started_pids=started_pids,
            )
        finally:
            observed, survivors = drain_post_baseline_descendants(
                parent_pid,
                parent_baseline,
            )
            if original_subreaper != 1:
                set_prctl(
                    PR_SET_CHILD_SUBREAPER,
                    original_subreaper,
                    "checkpoint fixture parent PR_SET_CHILD_SUBREAPER restore",
                )
            if observed:
                raise AssertionError(
                    "checkpoint outer subreaper adopted fixture descendants; "
                    f"adopted={sorted(observed.values())!r}; survivors={survivors!r}"
                )


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
            ["/usr/bin/python3", "-I", "-B", "-c", child_code, str(os.getpid())]
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
    expected = ["--ignore-default-opts", "--offline", "--usepkgonly", "--getbinpkg=n", "--nodeps", "--oneshot", "=cat/new-2"]
    if sys.argv[1:] != expected:
        raise SystemExit(f"unexpected emerge arguments: {sys.argv[1:]!r}")
    pkgdir = Path(os.environ["PKGDIR"])
    if pkgdir.name != "critical-fixture":
        raise SystemExit("emerge PKGDIR is not the activated durable checkpoint")
    target = root / "var/db/pkg/cat/new-2/BUILD_TIME"
    restore_log = control / "restore-ran"
    attempt = len(restore_log.read_text().splitlines()) if restore_log.exists() else 0
    target.write_text(f"restored-by-binpkg-{attempt}\n", encoding="utf-8")
    with restore_log.open("a", encoding="utf-8") as stream:
        stream.write("=cat/new-2\n")
    raise SystemExit(0)

if invoked == "qcheck":
    if sys.argv[1:] != ["=cat/new-2"]:
        raise SystemExit(f"unexpected qcheck arguments: {sys.argv[1:]!r}")
    print("cat/new-2: 0 out of 1 files failed")
    raise SystemExit(0)

if invoked == "portageq":
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

import os
import signal
import subprocess
import sys
import time

arguments = list(sys.argv[1:])
while arguments and arguments[0] != "--":
    arguments.pop(0)
if not arguments or arguments.pop(0) != "--" or not arguments:
    raise SystemExit("unexpected fake unshare arguments")

launcher = r"""
import ctypes
import os
import signal
import sys
expected_parent = int(sys.argv[1])
command = sys.argv[2:]
if os.getppid() != expected_parent:
    raise SystemExit(91)
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGKILL) != 0:
    raise OSError(ctypes.get_errno(), 'PR_SET_PDEATHSIG failed')
if os.getppid() != expected_parent:
    raise SystemExit(92)
os.execv(command[0], command)
"""
child = subprocess.Popen(
    ["/usr/bin/python3", "-I", "-B", "-c", launcher, str(os.getpid()), *arguments],
    start_new_session=True,
)
terminating = False

def terminate(signum: int, _frame: object) -> None:
    global terminating
    terminating = True
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)
signal.signal(signal.SIGHUP, terminate)
while child.poll() is None:
    time.sleep(0.01)
if terminating:
    raise SystemExit(128 + signal.SIGTERM)
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
            self.tool_root / "usr/sbin",
            self.tool_root / "sbin",
            self.tool_root / "bin",
            self.script.parent,
            root / "root",
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

    def marker(self, name: str) -> None:
        (self.control / name).touch()


class CheckpointHarnessTest(unittest.TestCase):
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
            ["quickpkg", "emaint", "emaint", "portageq"],
        )
        clone_policy = json.loads((self.report() / "clone-policy.json").read_text())
        containment = json.loads(
            (self.report() / "containment-preflight.json").read_text()
        )
        self.assertEqual(
            containment,
            {
                "schema_version": 2,
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
                    "kill_child_signal": "SIGKILL",
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

    def test_cleanup_deadline_expiry_is_bounded_reported_and_children_die_with_parent(self) -> None:
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
                self.assertTrue(
                    all(wait_process_identity_stopped(identity, 10) for identity in observed.values()),
                    f"children outlived bounded coordinator failure: {surviving_processes(observed)!r}",
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

    def test_coordinator_sigkill_cannot_orphan_term_surviving_tracked_descendants(self) -> None:
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
                os.kill(root.pid, signal.SIGKILL)
                self.assertEqual(process.wait(timeout=10), -signal.SIGKILL)
                self.assertTrue(
                    all(wait_process_identity_stopped(identity, 10) for identity in active_identities),
                    f"tracked descendants survived coordinator SIGKILL: "
                    f"{surviving_processes(observed)!r}",
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
                observed.update(snapshot_descendants(root.pid))
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
                residue_deadline = time.monotonic() + 5
                survivors = [
                    identity
                    for identity in active_identities.values()
                    if process_identity_is_live(identity)
                ]
                while survivors and time.monotonic() < residue_deadline:
                    time.sleep(0.05)
                    survivors = [
                        identity
                        for identity in active_identities.values()
                        if process_identity_is_live(identity)
                    ]
                if survivors:
                    self.fail(f"tracked processes survived: {survivors!r}")
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

            descendant = os.fork()
            if descendant == 0:
                os.setsid()
                signal.pause()
                raise SystemExit(90)
            signal.pause()
            raise SystemExit(91)
            """
        )
        with tempfile.TemporaryFile() as stderr_file:
            supervisor = subprocess.Popen(
                [
                    "/usr/bin/unshare",
                    "--pid",
                    "--fork",
                    "--kill-child=KILL",
                    "--mount-proc",
                    "--",
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    namespace_code,
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

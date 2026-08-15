#!/usr/bin/python3.15 -IB
"""Publish and execute the immutable jsonschema prerequisite bootstrap.

The source checkout is already materialized by the root-owned Candidate-A
bundle procedure.  This program adds the narrower bootstrap boundary needed
before the framework itself can be installed: it proves the checkout is the
exact clean commit, copies the transaction helper and snapshot verifier into
one private root-owned directory, records both source and published file
identities, and publishes the directory with RENAME_NOREPLACE.  The ``exec``
entrypoint revalidates that directory and only then executes the published
transaction helper.
"""

from __future__ import annotations

import argparse
import ctypes
import dataclasses
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn, Sequence


SCHEMA = "gentoo-optimization-jsonschema-prerequisite-bootstrap-v1"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
TRANSACTION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
PUBLISHER_RELATIVE = Path(
    "scripts/optimization/recovery/publish-jsonschema-prerequisite-bootstrap.py"
)
PAYLOAD_RELATIVES = (
    PUBLISHER_RELATIVE,
    Path("scripts/optimization/recovery/install-jsonschema-prerequisite.py"),
    Path("scripts/optimization/recovery/verify-binpkg-snapshot.py"),
)
PRODUCTION_PARENT = Path("/var/lib/gentoo-optimization/bootstrap")
PYTHON = Path("/usr/bin/python3.15")
PUBLIC_HELPER_COMMANDS = frozenset({"prepare", "run", "recover", "verify"})
AT_FDCWD = -100
RENAME_NOREPLACE = 1
MAX_SOURCE_SIZE = 16 * 1024 * 1024


class BootstrapError(RuntimeError):
    """The bootstrap cannot be proven safe."""


def fail(message: str) -> NoReturn:
    raise BootstrapError(message)


@dataclasses.dataclass(frozen=True)
class RuntimeAuthority:
    uid: int
    gid: int
    parent: Path
    python: Path
    production: bool


@dataclasses.dataclass(frozen=True)
class Identity:
    path: str
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    nlink: int
    size: int
    sha256: str

    def json(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def resolved_exact(path: Path, label: str) -> Path:
    if not path.is_absolute():
        fail(f"{label} is not absolute: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve {label}: {error}")
    if resolved != path:
        fail(f"{label} is not canonical or contains a symlink: {path}")
    return resolved


def validate_directory(path: Path, uid: int, gid: int, mode: int | None = None) -> None:
    metadata = path.lstat()
    actual_mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or actual_mode & 0o022
        or (mode is not None and actual_mode != mode)
    ):
        fail(f"untrusted directory: {path}")


def validate_tree(path: Path, stop: Path, uid: int, gid: int) -> None:
    current = path
    while True:
        validate_directory(current, uid, gid)
        if current == stop:
            return
        if current == current.parent or not current.is_relative_to(stop):
            fail(f"trusted directory escapes its root: {path}")
        current = current.parent


def validate_runtime_parent(authority: RuntimeAuthority) -> None:
    expected_mode = 0o755 if authority.production else 0o700
    validate_directory(
        authority.parent,
        authority.uid,
        authority.gid,
        expected_mode,
    )
    if authority.production:
        validate_tree(authority.parent, Path("/"), 0, 0)


def identity_from_stat(path: Path, metadata: os.stat_result, digest: str) -> Identity:
    return Identity(
        path=os.fspath(path),
        device=metadata.st_dev,
        inode=metadata.st_ino,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        mode=stat.S_IMODE(metadata.st_mode),
        nlink=metadata.st_nlink,
        size=metadata.st_size,
        sha256=digest,
    )


def read_trusted_file(path: Path, uid: int, gid: int) -> tuple[bytes, Identity]:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        fail(f"cannot open trusted source {path}: {error}")
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != uid
            or before.st_gid != gid
            or mode & 0o022
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > MAX_SOURCE_SIZE
        ):
            fail(f"untrusted source file: {path}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                fail(f"short read from trusted source: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            fail(f"trusted source grew while read: {path}")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_uid,
            before.st_gid,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_uid,
            after.st_gid,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            fail(f"trusted source changed while read: {path}")
        payload = b"".join(chunks)
        digest = hashlib.sha256(payload).hexdigest()
        return payload, identity_from_stat(path, after, digest)
    finally:
        os.close(descriptor)


def clean_environment() -> dict[str, str]:
    return {
        "HOME": "/root",
        "USER": "root",
        "LOGNAME": "root",
        "SHELL": "/bin/bash",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def git_bytes(
    repository: Path, *arguments: str, valid: tuple[int, ...] = (0,)
) -> bytes:
    command = [
        "/usr/bin/git",
        "--no-pager",
        "--no-replace-objects",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.attributesFile=/dev/null",
        "-c",
        "diff.external=",
        "-C",
        os.fspath(repository),
        *arguments,
    ]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
            env=clean_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"bounded Git observation failed: {error}")
    if result.returncode not in valid:
        fail(
            f"Git observation exited {result.returncode}: "
            f"{result.stderr.decode('utf-8', 'replace').strip() or '<no diagnostic>'}"
        )
    return result.stdout


def git_command(repository: Path, *arguments: str, valid: tuple[int, ...] = (0,)) -> str:
    return git_bytes(repository, *arguments, valid=valid).decode("utf-8", "strict")


def validate_repository(
    repository: Path, commit: str, uid: int, gid: int
) -> tuple[str, Identity]:
    resolved_exact(repository, "repository root")
    validate_directory(repository, uid, gid)
    if uid == 0 and gid == 0:
        validate_tree(repository, Path("/"), uid, gid)
    top = git_command(repository, "rev-parse", "--show-toplevel").strip()
    if top != os.fspath(repository):
        fail("Git top-level differs from the reviewed repository root")
    head = git_command(repository, "rev-parse", "--verify", "HEAD^{commit}").strip()
    if head != commit:
        fail(f"repository HEAD differs from reviewed commit {commit}")
    tree = git_command(repository, "rev-parse", "--verify", "HEAD^{tree}").strip()
    if not COMMIT_PATTERN.fullmatch(tree):
        fail("repository HEAD tree identity is invalid")
    git_directory_text = git_command(repository, "rev-parse", "--absolute-git-dir").strip()
    git_directory = resolved_exact(Path(git_directory_text), "repository Git directory")
    if git_directory != repository / ".git":
        fail("repository does not use its reviewed private .git directory")
    validate_tree(git_directory, repository, uid, gid)
    _config_payload, config_identity = read_trusted_file(
        git_directory / "config", uid, gid
    )
    status = git_command(
        repository,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
    )
    if status:
        fail("repository checkout has tracked, untracked, or ignored residue")
    return tree, config_identity


def git_blob_authority(repository: Path, relative: Path) -> dict[str, object]:
    raw = git_bytes(repository, "ls-tree", "-z", "HEAD", "--", relative.as_posix())
    rows = [row for row in raw.split(b"\0") if row]
    if len(rows) != 1:
        fail(f"HEAD does not bind exactly one bootstrap source: {relative}")
    try:
        header, raw_path = rows[0].split(b"\t", 1)
        raw_mode, raw_type, raw_oid = header.split(b" ", 2)
        mode = raw_mode.decode("ascii")
        object_type = raw_type.decode("ascii")
        oid = raw_oid.decode("ascii")
        path = raw_path.decode("utf-8", "strict")
    except (ValueError, UnicodeDecodeError) as error:
        fail(f"cannot parse HEAD bootstrap source identity for {relative}: {error}")
    if (
        mode != "100755"
        or object_type != "blob"
        or not COMMIT_PATTERN.fullmatch(oid)
        or path != relative.as_posix()
    ):
        fail(f"HEAD bootstrap source identity is invalid for {relative}")
    size_text = git_command(repository, "cat-file", "-s", oid).strip()
    if not size_text.isdigit() or not 0 < int(size_text) <= MAX_SOURCE_SIZE:
        fail(f"HEAD bootstrap blob size is invalid for {relative}")
    blob = git_bytes(repository, "cat-file", "blob", oid)
    if len(blob) != int(size_text):
        fail(f"HEAD bootstrap blob size changed for {relative}")
    return {
        "path": relative.as_posix(),
        "mode": mode,
        "blob_oid": oid,
        "blob_size": len(blob),
        "blob_sha256": hashlib.sha256(blob).hexdigest(),
    }


def write_file(path: Path, payload: bytes, mode: int, uid: int, gid: int) -> Identity:
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                fail(f"short write while publishing {path}")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    digest = hashlib.sha256(payload).hexdigest()
    return identity_from_stat(path, metadata, digest)


def rename_noreplace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        fail("libc does not expose renameat2 for no-replace bootstrap publication")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        AT_FDCWD,
        os.fsencode(source),
        AT_FDCWD,
        os.fsencode(destination),
        RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        if error in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            fail("filesystem does not support no-replace bootstrap publication")
        fail(f"cannot publish bootstrap without replacement: {os.strerror(error)}")


def destination(parent: Path, commit: str) -> Path:
    return parent / f"jsonschema-prerequisite-{commit}"


def python_identity(python: Path) -> Identity:
    resolved_exact(python, "bootstrap Python interpreter")
    validate_tree(python.parent, Path("/"), 0, 0)
    _payload, identity = read_trusted_file(python, 0, 0)
    if identity.mode != 0o755 or not os.access(python, os.X_OK):
        fail("bootstrap Python interpreter is not root-owned executable mode 0755")
    return identity


def validate_public_helper_command(command: Sequence[str]) -> None:
    if not command or command[0] not in PUBLIC_HELPER_COMMANDS:
        fail(
            "bootstrap exec accepts only the public prepare, run, recover, "
            "or verify transaction-helper commands"
        )
    action = command[0]
    if action == "prepare":
        if (
            len(command) != 4
            or not TRANSACTION_ID_PATTERN.fullmatch(command[1])
            or command[2] != "--pre-checkpoint-state"
            or not Path(command[3]).is_absolute()
            or os.path.normpath(command[3]) != command[3]
        ):
            fail(
                "bootstrap prepare requires exactly: prepare TRANSACTION_ID "
                "--pre-checkpoint-state ABSOLUTE_CANONICAL_PATH"
            )
        return
    if len(command) != 2 or not TRANSACTION_ID_PATTERN.fullmatch(command[1]):
        fail(
            f"bootstrap {action} requires exactly one valid transaction ID"
        )


def manifest_integer(value: object, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        fail(f"bootstrap manifest {label} is invalid")
    return value


def validate_identity_value(
    raw: object,
    *,
    label: str,
    expected_path: Path,
    uid: int,
    gid: int,
    expected_mode: int | None,
) -> dict[str, object]:
    identity_keys = {
        "path",
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
        "nlink",
        "size",
        "sha256",
    }
    if not isinstance(raw, dict) or set(raw) != identity_keys:
        fail(f"bootstrap manifest {label} identity schema is invalid")
    if raw.get("path") != os.fspath(expected_path):
        fail(f"bootstrap manifest {label} path mapping is invalid")
    for key in ("device", "inode", "uid", "gid", "mode", "nlink", "size"):
        manifest_integer(raw.get(key), f"{label}.{key}")
    if (
        raw.get("uid") != uid
        or raw.get("gid") != gid
        or (
            expected_mode is not None
            and raw.get("mode") != expected_mode
        )
        or (
            expected_mode is None
            and int(raw.get("mode", 0)) & 0o022
        )
        or raw.get("nlink") != 1
        or manifest_integer(raw.get("size"), f"{label}.size", 1) > MAX_SOURCE_SIZE
        or not SHA256_PATTERN.fullmatch(str(raw.get("sha256", "")))
    ):
        fail(f"bootstrap manifest {label} identity is invalid")
    return raw


def validate_manifest_directory(
    directory: Path,
    authority: RuntimeAuthority,
) -> dict[str, object]:
    uid = authority.uid
    gid = authority.gid
    validate_runtime_parent(authority)
    validate_directory(directory, uid, gid, 0o700)
    manifest_path = directory / "bootstrap-manifest.json"
    payload, manifest_identity = read_trusted_file(manifest_path, uid, gid)
    if manifest_identity.mode != 0o600:
        fail("bootstrap manifest mode is not 0600")
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        fail(f"cannot parse bootstrap manifest: {error}")
    manifest_keys = {
        "schema",
        "commit",
        "tree",
        "repository_root",
        "repository_root_identity",
        "repository_git_config",
        "python",
        "destination",
        "files",
    }
    if not isinstance(value, dict) or set(value) != manifest_keys:
        fail("bootstrap manifest top-level schema is invalid")
    if value.get("schema") != SCHEMA:
        fail("bootstrap manifest schema is invalid")
    if not COMMIT_PATTERN.fullmatch(str(value.get("commit", ""))):
        fail("bootstrap manifest commit identity is invalid")
    if not COMMIT_PATTERN.fullmatch(str(value.get("tree", ""))):
        fail("bootstrap manifest tree identity is invalid")
    if value.get("destination") != os.fspath(directory):
        fail("bootstrap manifest destination differs from its directory")
    repository_root_text = value.get("repository_root")
    if (
        not isinstance(repository_root_text, str)
        or not Path(repository_root_text).is_absolute()
        or Path(repository_root_text) == Path("/")
        or os.path.normpath(repository_root_text) != repository_root_text
    ):
        fail("bootstrap manifest repository root is invalid")
    repository_root = Path(repository_root_text)
    repository_identity = value.get("repository_root_identity")
    if not isinstance(repository_identity, dict) or set(repository_identity) != {
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
    }:
        fail("bootstrap manifest repository root identity schema is invalid")
    for key in ("device", "inode", "uid", "gid", "mode"):
        manifest_integer(repository_identity.get(key), f"repository_root.{key}")
    if (
        repository_identity.get("uid") != uid
        or repository_identity.get("gid") != gid
        or int(repository_identity.get("mode", 0)) & 0o022
    ):
        fail("bootstrap manifest repository root identity is invalid")
    validate_identity_value(
        value.get("repository_git_config"),
        label="repository Git config",
        expected_path=repository_root / ".git/config",
        uid=uid,
        gid=gid,
        expected_mode=None,
    )
    if value.get("python") != python_identity(authority.python).json():
        fail("bootstrap Python interpreter identity changed")
    rows = value.get("files")
    if not isinstance(rows, list) or len(rows) != len(PAYLOAD_RELATIVES):
        fail("bootstrap manifest file set is invalid")
    expected_names = {relative.name for relative in PAYLOAD_RELATIVES}
    observed_names: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict) or set(raw) != {
            "relative",
            "git",
            "source",
            "published",
        }:
            fail("bootstrap manifest file row is invalid")
        name = raw.get("relative")
        if not isinstance(name, str) or name not in expected_names or name in observed_names:
            fail("bootstrap manifest repeats or invents a payload")
        observed_names.add(name)
        relative = next(
            item for item in PAYLOAD_RELATIVES if item.name == name
        )
        git = raw.get("git")
        if (
            not isinstance(git, dict)
            or set(git)
            != {"path", "blob_oid", "blob_sha256", "blob_size", "mode"}
            or git.get("path") != relative.as_posix()
            or git.get("mode") != "100755"
            or not COMMIT_PATTERN.fullmatch(str(git.get("blob_oid", "")))
            or not SHA256_PATTERN.fullmatch(str(git.get("blob_sha256", "")))
            or manifest_integer(git.get("blob_size"), "Git blob size", 1)
            > MAX_SOURCE_SIZE
        ):
            fail("bootstrap manifest Git blob authority is invalid")
        source = validate_identity_value(
            raw.get("source"),
            label=f"source {name}",
            expected_path=repository_root / relative,
            uid=uid,
            gid=gid,
            expected_mode=0o755,
        )
        published = validate_identity_value(
            raw.get("published"),
            label=f"published {name}",
            expected_path=directory / name,
            uid=uid,
            gid=gid,
            expected_mode=0o755,
        )
        if (
            git.get("blob_size") != source.get("size")
            or git.get("blob_sha256") != source.get("sha256")
            or source.get("size") != published.get("size")
            or source.get("sha256") != published.get("sha256")
        ):
            fail(f"bootstrap Git/source/published authority differs for {name}")
        path = directory / name
        payload_bytes, identity = read_trusted_file(path, uid, gid)
        del payload_bytes
        actual = identity.json()
        if actual != published:
            fail(f"published bootstrap payload identity changed: {path}")
        if source.get("sha256") != published.get("sha256"):
            fail(f"published bootstrap payload differs from its source: {path}")
        if identity.mode != 0o755:
            fail(f"published bootstrap payload mode is not 0755: {path}")
    if observed_names != expected_names:
        fail("bootstrap manifest omits a payload")
    actual_names = {entry.name for entry in directory.iterdir()}
    if actual_names != expected_names | {"bootstrap-manifest.json"}:
        fail("bootstrap directory contains an unexplained object")
    return value


def publish(arguments: argparse.Namespace, authority: RuntimeAuthority) -> int:
    uid = authority.uid
    gid = authority.gid
    parent = authority.parent
    commit = arguments.commit
    if not COMMIT_PATTERN.fullmatch(commit):
        fail("reviewed commit is not a lowercase 40-character Git object ID")
    repository = resolved_exact(arguments.repository_root, "repository root")
    tree, git_config_identity = validate_repository(repository, commit, uid, gid)
    bound_python = python_identity(authority.python)
    validate_runtime_parent(authority)
    final = destination(parent, commit)
    stage = parent / f".{final.name}.partial.{os.getpid()}"
    if path_exists(final) or path_exists(stage):
        fail("bootstrap destination or publication partial already exists")
    source_rows: list[tuple[Path, bytes, Identity, dict[str, object]]] = []
    for relative in PAYLOAD_RELATIVES:
        source = repository / relative
        if source.parent.resolve(strict=True) != source.parent:
            fail(f"bootstrap source parent contains a symlink: {source.parent}")
        validate_tree(source.parent, repository, uid, gid)
        payload, identity = read_trusted_file(source, uid, gid)
        git_authority = git_blob_authority(repository, relative)
        raw_blob = git_bytes(
            repository,
            "cat-file",
            "blob",
            str(git_authority["blob_oid"]),
        )
        if raw_blob != payload:
            fail(f"worktree source differs byte-for-byte from HEAD blob: {relative}")
        source_rows.append((relative, payload, identity, git_authority))
    os.mkdir(stage, 0o700)
    os.chown(stage, uid, gid)
    os.chmod(stage, 0o700)
    try:
        rows: list[dict[str, object]] = []
        for relative, payload, source_identity, git_authority in source_rows:
            staged_identity = write_file(stage / relative.name, payload, 0o755, uid, gid)
            published_identity = dataclasses.replace(
                staged_identity, path=os.fspath(final / relative.name)
            )
            rows.append(
                {
                    "relative": relative.name,
                    "git": git_authority,
                    "source": source_identity.json(),
                    "published": published_identity.json(),
                }
            )
        current_tree, current_git_config = validate_repository(
            repository, commit, uid, gid
        )
        if current_tree != tree or current_git_config != git_config_identity:
            fail("repository tree changed during bootstrap publication")
        for relative, original_payload, source_identity, git_authority in source_rows:
            _current_payload, current_identity = read_trusted_file(
                repository / relative, uid, gid
            )
            if current_identity != source_identity:
                fail(f"bootstrap source identity changed during publication: {relative}")
            if _current_payload != original_payload:
                fail(f"bootstrap source bytes changed during publication: {relative}")
            if git_blob_authority(repository, relative) != git_authority:
                fail(f"HEAD bootstrap blob changed during publication: {relative}")
        repository_metadata = repository.lstat()
        manifest = {
            "schema": SCHEMA,
            "commit": commit,
            "tree": tree,
            "repository_root": os.fspath(repository),
            "repository_root_identity": {
                "device": repository_metadata.st_dev,
                "inode": repository_metadata.st_ino,
                "uid": repository_metadata.st_uid,
                "gid": repository_metadata.st_gid,
                "mode": stat.S_IMODE(repository_metadata.st_mode),
            },
            "repository_git_config": git_config_identity.json(),
            "python": bound_python.json(),
            "destination": os.fspath(final),
            "files": rows,
        }
        write_file(
            stage / "bootstrap-manifest.json",
            canonical_json(manifest),
            0o600,
            uid,
            gid,
        )
        fsync_directory(stage)
        if python_identity(authority.python) != bound_python:
            fail("bootstrap Python interpreter changed during publication")
        rename_noreplace(stage, final)
        fsync_directory(parent)
    except BaseException:
        if path_exists(stage):
            for child in stage.iterdir():
                child.unlink()
            stage.rmdir()
            fsync_directory(parent)
        raise
    validate_manifest_directory(final, authority)
    print(final)
    return 0


def execute(arguments: argparse.Namespace, authority: RuntimeAuthority) -> NoReturn:
    commit = arguments.commit
    if not COMMIT_PATTERN.fullmatch(commit):
        fail("reviewed commit is not a lowercase 40-character Git object ID")
    directory = destination(authority.parent, commit)
    current = Path(__file__).resolve(strict=True)
    if current != directory / PUBLISHER_RELATIVE.name:
        fail("exec must be invoked through the published bootstrap publisher")
    manifest = validate_manifest_directory(directory, authority)
    if manifest.get("commit") != commit:
        fail("bootstrap manifest commit differs from the requested commit")
    command = list(arguments.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        fail("bootstrap exec requires a transaction-helper command")
    validate_public_helper_command(command)
    python = authority.python
    metadata = python.stat()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(python, os.X_OK):
        fail(f"bootstrap Python interpreter is not executable: {python}")
    helper = directory / "install-jsonschema-prerequisite.py"
    argv = [os.fspath(python), "-I", "-B", os.fspath(helper), *command]
    os.execve(python, argv, clean_environment())


def verify_published(
    arguments: argparse.Namespace, authority: RuntimeAuthority
) -> int:
    commit = arguments.commit
    if not COMMIT_PATTERN.fullmatch(commit):
        fail("reviewed commit is not a lowercase 40-character Git object ID")
    directory = destination(authority.parent, commit)
    current = Path(__file__).resolve(strict=True)
    if current != directory / PUBLISHER_RELATIVE.name:
        fail("verify must be invoked through the published bootstrap publisher")
    manifest = validate_manifest_directory(directory, authority)
    if manifest.get("commit") != commit:
        fail("bootstrap manifest commit differs from the requested commit")
    print(directory)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="publish or execute the immutable jsonschema prerequisite bootstrap"
    )
    parser.add_argument(
        "--fixture-destination-parent", type=Path, help=argparse.SUPPRESS
    )
    parser.add_argument("--fixture-python", type=Path, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="action", required=True)
    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--repository-root", required=True, type=Path)
    publish_parser.add_argument("--commit", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--commit", required=True)
    execute_parser = subparsers.add_parser("exec")
    execute_parser.add_argument("--commit", required=True)
    execute_parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def runtime_authority(arguments: argparse.Namespace) -> RuntimeAuthority:
    fixture = arguments.fixture_destination_parent is not None
    if fixture:
        if os.environ.get("GENTOO_OPT_JSONSCHEMA_BOOTSTRAP_FIXTURE") != "1":
            fail("fixture destination requires the explicit fixture environment")
        if arguments.fixture_python is None:
            fail("fixture destination requires an explicit fixture Python interpreter")
        uid = os.getuid()
        gid = os.getgid()
        parent = resolved_exact(arguments.fixture_destination_parent, "fixture destination parent")
        python = resolved_exact(arguments.fixture_python, "fixture Python interpreter")
    else:
        if arguments.fixture_python is not None:
            fail("fixture Python override is forbidden in production")
        if os.geteuid() != 0:
            fail("production bootstrap publication and execution require root")
        uid = 0
        gid = 0
        parent = PRODUCTION_PARENT
        resolved_exact(parent, "production bootstrap parent")
        python = PYTHON
    authority = RuntimeAuthority(
        uid=uid,
        gid=gid,
        parent=parent,
        python=python,
        production=not fixture,
    )
    validate_runtime_parent(authority)
    return authority


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        authority = runtime_authority(arguments)
        if arguments.action == "publish":
            return publish(arguments, authority)
        if arguments.action == "verify":
            return verify_published(arguments, authority)
        if arguments.action == "exec":
            execute(arguments, authority)
        fail(f"unsupported bootstrap action: {arguments.action}")
    except BootstrapError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

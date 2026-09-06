#!/usr/bin/env python3
"""Fail closed if a one-shot authorization token persisted in gate artifacts."""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import stat
import sys
from collections.abc import Sequence


class ScanError(Exception):
    """The scan could not prove that every supplied root is token-free."""


def path_identity(path: pathlib.Path) -> str:
    return hashlib.sha256(os.fsencode(path)).hexdigest()


def read_token(descriptor: int) -> bytes:
    payload = os.read(descriptor, 66)
    if len(payload) != 65 or not payload.endswith(b"\n"):
        raise ScanError("authorization token descriptor has an invalid payload")
    if os.read(descriptor, 1):
        raise ScanError("authorization token descriptor contains trailing bytes")
    token = payload[:-1]
    if any(byte not in b"0123456789abcdef" for byte in token):
        raise ScanError("authorization token is not lowercase hexadecimal")
    return token


def stable_regular_contains(path: pathlib.Path, token: bytes) -> bool:
    before = os.lstat(path)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if identity != (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ):
            raise ScanError(
                f"token scan input changed while opening: {path_identity(path)}"
            )
        overlap = b""
        found = False
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            combined = overlap + chunk
            if token in combined:
                found = True
                break
            overlap = combined[-(len(token) - 1) :]
        after = os.fstat(descriptor)
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ScanError(
                f"token scan input changed while reading: {path_identity(path)}"
            )
        return found
    finally:
        os.close(descriptor)


def stable_xattrs_contain(path: pathlib.Path, token: bytes) -> bool:
    """Inspect every xattr without following a symlink or accepting a race."""

    before = os.lstat(path)
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    found = False
    names = sorted(os.listxattr(path, follow_symlinks=False))
    for name in names:
        value = os.getxattr(path, name, follow_symlinks=False)
        if token in os.fsencode(name) or token in value:
            found = True
    after = os.lstat(path)
    if identity != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ScanError(
            f"token scan xattr input changed while reading: {path_identity(path)}"
        )
    return found


def scan_roots(roots: Sequence[pathlib.Path], token: bytes) -> list[str]:
    leaks: set[str] = set()

    def walk_error(error: OSError) -> None:
        raise ScanError(f"token scan traversal failed: errno={error.errno}")

    for root in roots:
        if not root.is_absolute() or root == pathlib.Path("/"):
            raise ScanError("token scan root must be an absolute non-root path")
        root_metadata = os.lstat(root)
        if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(
            root_metadata.st_mode
        ):
            raise ScanError("token scan root is not a real directory")
        if token in os.fsencode(root.name) or stable_xattrs_contain(root, token):
            leaks.add(path_identity(root))
        for directory, directory_names, file_names in os.walk(
            root, topdown=True, onerror=walk_error, followlinks=False
        ):
            directory_names[:] = sorted(directory_names)
            for name in sorted(directory_names + file_names):
                path = pathlib.Path(directory, name)
                metadata = os.lstat(path)
                name_contains = token in os.fsencode(name)
                xattr_contains = stable_xattrs_contain(path, token)
                if stat.S_ISLNK(metadata.st_mode):
                    target_contains = token in os.fsencode(os.readlink(path))
                    if name_contains or target_contains or xattr_contains:
                        leaks.add(path_identity(path))
                elif stat.S_ISDIR(metadata.st_mode):
                    if name_contains or xattr_contains:
                        leaks.add(path_identity(path))
                elif stat.S_ISREG(metadata.st_mode):
                    if (
                        name_contains
                        or xattr_contains
                        or stable_regular_contains(path, token)
                    ):
                        leaks.add(path_identity(path))
                elif stat.S_ISFIFO(metadata.st_mode) and path.parent.name == ".ipc" and name in {"in", "out"}:
                    # Portage's userpriv build IPC endpoints are named FIFOs;
                    # they cannot contain the bearer bytes and are part of the
                    # coordinator's disposable runtime roots.
                    continue
                else:
                    raise ScanError(
                        "token scan found an unsupported object: "
                        f"{path_identity(path)}"
                    )
    return sorted(leaks)


def publish_result(output: pathlib.Path, leaks: Sequence[str]) -> None:
    if not output.is_absolute() or output == pathlib.Path("/"):
        raise ScanError("token scan output must be an absolute non-root path")
    parent = output.parent
    metadata = os.lstat(parent)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ScanError("token scan output parent is not a real directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(output, flags, 0o600)
    try:
        rows = ["status\tpath_sha256\n"]
        if leaks:
            rows.extend(f"leaked\t{identity}\n" for identity in leaks)
        else:
            rows.append("passed\t-\n")
        payload = "".join(rows).encode("ascii")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise ScanError("short write while publishing token scan evidence")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_descriptor = os.open(
        parent, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--token-fd", required=True, type=int)
    result.add_argument("--output", required=True, type=pathlib.Path)
    result.add_argument("roots", nargs="+", type=pathlib.Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        token = read_token(arguments.token_fd)
        leaks = scan_roots(arguments.roots, token)
        publish_result(arguments.output, leaks)
    except (OSError, ScanError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if leaks:
        print("ERROR: raw coordinator authorization persisted in gate artifacts", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

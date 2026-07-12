#!/usr/bin/env python3
"""Fail-closed pre-strip BOLT capture and ${ED} deployment transactions."""

from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn


SCHEMA_CAPTURE = "gentoo-optimization-bolt-capture-v1"
SCHEMA_OUTPUT = "gentoo-optimization-bolt-output-v1"
FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
BUILD_ID_RE = re.compile(r"Build ID:\s*([0-9A-Fa-f]+)")
HEADER_RE = re.compile(r"^\s*(Class|Data|Type|Machine):\s*(.*?)\s*$")
SECTION_RE = re.compile(
    r"^\s*\[\s*\d+\]\s+(\S+)\s+(\S+)\s+"
    r"[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+"
    r"\S+\s+(\S*)\s+"
)
ELF_MAGIC = b"\x7fELF"


class BoltArtifactError(RuntimeError):
    """A fail-closed capture or deployment error."""


def fail(message: str) -> NoReturn:
    raise BoltArtifactError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_checked(argv: list[str]) -> str:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        fail(f"cannot execute {argv[0]}: {error}")
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        fail(f"command failed ({completed.returncode}): {' '.join(argv)}: {detail}")
    return completed.stdout


def validate_root(path_text: str, label: str) -> Path:
    path = Path(path_text).resolve(strict=True)
    if not path.is_dir():
        fail(f"{label} is not a directory: {path}")
    if path == Path("/") or path == Path("/usr"):
        fail(f"refusing unsafe {label}: {path}")
    return path


def validate_cache_root(path_text: str, ed: Path) -> Path:
    raw = Path(path_text)
    raw.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = raw.resolve(strict=True)
    if path in (Path("/"), Path("/usr")):
        fail(f"refusing unsafe cache root: {path}")
    if path == ed or ed in path.parents:
        fail("cache root must not be inside ED")
    return path


def validate_fingerprint(value: str) -> str:
    if FINGERPRINT_RE.fullmatch(value) is None:
        fail("package fingerprint must be exactly 64 lowercase hexadecimal characters")
    return value


def safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        fail(f"path escaped root {root}: {path}")
    text = relative.as_posix()
    if text in ("", ".") or text.startswith("/") or ".." in relative.parts:
        fail(f"unsafe relative path: {text!r}")
    return text


def path_from_relative(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        fail(f"unsafe manifest path: {relative!r}")
    result = root.joinpath(*candidate.parts)
    try:
        result.relative_to(root)
    except ValueError:
        fail(f"manifest path escaped root: {relative!r}")
    return result


def read_xattrs(path: Path, *, follow_symlinks: bool = False) -> dict[str, str]:
    try:
        names = os.listxattr(path, follow_symlinks=follow_symlinks)
    except OSError as error:
        if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            return {}
        fail(f"cannot list xattrs for {path}: {error}")
    result: dict[str, str] = {}
    for name in sorted(names):
        try:
            value = os.getxattr(path, name, follow_symlinks=follow_symlinks)
        except OSError as error:
            fail(f"cannot read xattr {name!r} for {path}: {error}")
        result[name] = base64.b64encode(value).decode("ascii")
    return result


def metadata(path: Path, status: os.stat_result | None = None) -> dict[str, Any]:
    info = status if status is not None else path.lstat()
    xattrs = read_xattrs(path)
    return {
        "mode": format(stat.S_IMODE(info.st_mode), "04o"),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "xattrs_base64": xattrs,
        "capability_base64": xattrs.get("security.capability"),
    }


def open_noatime(path: Path) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    noatime = getattr(os, "O_NOATIME", 0)
    if not noatime:
        fail("O_NOATIME is unavailable; refusing a capture that could mutate ED atime")
    try:
        return os.open(path, flags | noatime)
    except OSError as error:
        fail(f"cannot read {path} without modifying atime: {error}")


def copy_noatime(source: Path, destination: Path, expected: os.stat_result) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = open_noatime(source)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            fail(f"file changed type during capture: {source}")
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            expected.st_dev,
            expected.st_ino,
            expected.st_size,
        ):
            fail(f"file changed identity during capture: {source}")
        with os.fdopen(fd, "rb", closefd=False) as input_stream:
            with destination.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
    finally:
        os.close(fd)
    if destination.stat().st_size != expected.st_size:
        fail(f"short copy while capturing {source}")


def is_elf(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(4) == ELF_MAGIC


def dump_text(objcopy: str, path: Path, directory: Path) -> tuple[str | None, int]:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = directory / "text.section"
    completed = subprocess.run(
        [objcopy, "--dump-section", f".text={output}", str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0 or not output.is_file():
        return None, 0
    return sha256_file(output), output.stat().st_size


def classify_elf(path: Path, readelf: str, objcopy: str, scratch: Path) -> dict[str, Any]:
    header_output = run_checked([readelf, "-hW", str(path)])
    headers: dict[str, str] = {}
    for line in header_output.splitlines():
        match = HEADER_RE.match(line)
        if match:
            value = match.group(2)
            if match.group(1) == "Type":
                value = value.split()[0]
            headers[match.group(1)] = value
    missing_headers = {"Class", "Data", "Type", "Machine"} - headers.keys()
    if missing_headers:
        fail(f"readelf omitted required ELF headers for {path}: {sorted(missing_headers)}")

    section_output = run_checked([readelf, "-SW", str(path)])
    sections: list[dict[str, str]] = []
    for line in section_output.splitlines():
        match = SECTION_RE.match(line)
        if match:
            sections.append(
                {"name": match.group(1), "type": match.group(2), "flags": match.group(3)}
            )
    section_names = {section["name"] for section in sections}
    executable_sections = sorted(
        section["name"] for section in sections if "X" in section["flags"]
    )
    relocation_sections = sorted(
        section["name"]
        for section in sections
        if section["type"] in ("REL", "RELA", "RELR")
    )
    text_relocations = sorted(
        name for name in relocation_sections if name in (".rel.text", ".rela.text")
    )
    symtab = any(section["type"] == "SYMTAB" for section in sections)
    symbol_count = 0
    if symtab:
        symbols = run_checked([readelf, "-sW", str(path)])
        symbol_count = sum(
            1 for line in symbols.splitlines() if re.match(r"^\s*\d+:\s", line)
        )

    notes = run_checked([readelf, "-nW", str(path)])
    build_ids = [value.lower() for value in BUILD_ID_RE.findall(notes)]
    if len(set(build_ids)) > 1:
        fail(f"multiple different GNU build IDs in {path}: {build_ids}")
    build_id = build_ids[0] if build_ids else None
    text_sha256, text_size = dump_text(objcopy, path, scratch)

    reasons: list[str] = []
    if headers["Class"] != "ELF64":
        reasons.append("unsupported-elf-class")
    if headers["Machine"] != "Advanced Micro Devices X86-64":
        reasons.append("unsupported-machine")
    if headers["Type"] not in ("EXEC", "DYN"):
        reasons.append("unsupported-elf-type")
    if not executable_sections:
        reasons.append("no-executable-section")
    if text_sha256 is None or text_size == 0:
        reasons.append("no-text-section")
    if not symtab or symbol_count == 0:
        reasons.append("no-full-symbol-table")
    if not text_relocations:
        reasons.append("no-text-relocations")
    if build_id is None:
        reasons.append("no-gnu-build-id")

    return {
        "elf_class": headers["Class"],
        "elf_data": headers["Data"],
        "elf_type": headers["Type"],
        "machine": headers["Machine"],
        "executable_sections": executable_sections,
        "section_names": sorted(section_names),
        "has_symtab": symtab,
        "symbol_count": symbol_count,
        "relocation_sections": relocation_sections,
        "text_relocation_sections": text_relocations,
        "build_id": build_id,
        "text_sha256": text_sha256,
        "text_size": text_size,
        "has_bolt_info": ".note.bolt_info" in section_names,
        "eligible": not reasons,
        "terminal_reasons": reasons,
    }


def tree_snapshot(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        names.sort()
        files.sort()
        base = Path(directory)
        for name in [*names, *files]:
            path = base / name
            info = path.lstat()
            record: dict[str, Any] = {
                "path": safe_relative(path, root),
                "mode": info.st_mode,
                "uid": info.st_uid,
                "gid": info.st_gid,
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "ctime_ns": info.st_ctime_ns,
                "device": info.st_dev,
                "inode": info.st_ino,
            }
            if stat.S_ISLNK(info.st_mode):
                record["target"] = os.readlink(path)
            records.append(record)
    return records


def scan_tree(root: Path) -> tuple[list[tuple[list[str], os.stat_result]], list[dict[str, Any]]]:
    groups: dict[tuple[int, int], tuple[list[str], os.stat_result]] = {}
    symlinks: list[dict[str, Any]] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        names.sort()
        files.sort()
        base = Path(directory)
        retained_names: list[str] = []
        for name in names:
            path = base / name
            info = path.lstat()
            relative = safe_relative(path, root)
            if stat.S_ISLNK(info.st_mode):
                symlinks.append(
                    {
                        "path": relative,
                        "target": os.readlink(path),
                        "uid": info.st_uid,
                        "gid": info.st_gid,
                    }
                )
            elif stat.S_ISDIR(info.st_mode):
                retained_names.append(name)
            else:
                fail(f"unsupported non-directory entry encountered: {path}")
        names[:] = retained_names
        for name in files:
            path = base / name
            info = path.lstat()
            relative = safe_relative(path, root)
            if stat.S_ISLNK(info.st_mode):
                symlinks.append(
                    {
                        "path": relative,
                        "target": os.readlink(path),
                        "uid": info.st_uid,
                        "gid": info.st_gid,
                    }
                )
            elif stat.S_ISREG(info.st_mode):
                key = (info.st_dev, info.st_ino)
                if key not in groups:
                    groups[key] = ([], info)
                groups[key][0].append(relative)
            else:
                fail(f"unsupported non-regular entry encountered: {path}")
    result = []
    for paths, info in groups.values():
        paths.sort()
        result.append((paths, info))
    result.sort(key=lambda item: item[0][0])
    symlinks.sort(key=lambda item: item["path"])
    return result, symlinks


def write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.partial.{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path, schema: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot load manifest {path}: {error}")
    if not isinstance(document, dict) or document.get("schema") != schema:
        fail(f"manifest {path} does not use schema {schema}")
    return document


def command_capture(arguments: argparse.Namespace) -> None:
    ed = validate_root(arguments.ed, "ED")
    cache = validate_cache_root(arguments.cache_root, ed)
    fingerprint = validate_fingerprint(arguments.fingerprint)
    readelf = shutil.which(arguments.readelf)
    objcopy = shutil.which(arguments.objcopy)
    if readelf is None or objcopy is None:
        fail("capture requires readelf and objcopy")

    inputs = cache / "inputs"
    inputs.mkdir(mode=0o700, exist_ok=True)
    final = inputs / fingerprint
    if final.exists() or final.is_symlink():
        fail(f"capture already exists; refusing overwrite: {final}")
    stage = inputs / f".{fingerprint}.partial.{os.getpid()}"
    if stage.exists():
        fail(f"stale capture stage exists: {stage}")
    stage.mkdir(mode=0o700)
    before = tree_snapshot(ed)
    artifacts: list[dict[str, Any]] = []
    regular_groups, symlinks = scan_tree(ed)
    elf_total = 0
    eligible_total = 0
    try:
        (stage / "objects").mkdir(mode=0o700)
        with tempfile.TemporaryDirectory(prefix="classify-", dir=stage) as scratch_text:
            scratch_root = Path(scratch_text)
            for index, (paths, info) in enumerate(regular_groups, 1):
                source = path_from_relative(ed, paths[0])
                scratch = scratch_root / f"{index:06d}.file"
                copy_noatime(source, scratch, info)
                if not is_elf(scratch):
                    scratch.unlink()
                    continue
                elf_total += 1
                classification = classify_elf(
                    scratch, readelf, objcopy, scratch_root / f"{index:06d}.sections"
                )
                artifact_id = hashlib.sha256(paths[0].encode("utf-8")).hexdigest()
                object_name: str | None = None
                if classification["eligible"]:
                    eligible_total += 1
                    object_name = f"objects/{artifact_id}.elf"
                    destination = stage / object_name
                    os.replace(scratch, destination)
                    os.chmod(destination, stat.S_IMODE(info.st_mode))
                else:
                    scratch.unlink()
                artifacts.append(
                    {
                        "artifact_id": artifact_id,
                        "canonical_path": paths[0],
                        "paths": paths,
                        "hardlink_count": len(paths),
                        "source_device": info.st_dev,
                        "source_inode": info.st_ino,
                        "file_sha256": sha256_file(stage / object_name)
                        if object_name
                        else sha256_file_noatime(source, info),
                        "size": info.st_size,
                        "metadata": metadata(source, info),
                        "cache_object": object_name,
                        **classification,
                    }
                )
        after = tree_snapshot(ed)
        if before != after:
            fail("ED metadata/topology changed during capture")
        manifest = {
            "schema": SCHEMA_CAPTURE,
            "package_fingerprint": fingerprint,
            "ed_root": str(ed),
            "regular_inode_groups_total": len(regular_groups),
            "elf_total": elf_total,
            "eligible_total": eligible_total,
            "ineligible_total": elf_total - eligible_total,
            "artifacts": artifacts,
            "symlinks": symlinks,
        }
        write_json_atomic(stage / "manifest.json", manifest)
        os.replace(stage, final)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(final / "manifest.json")


def sha256_file_noatime(path: Path, expected: os.stat_result) -> str:
    digest = hashlib.sha256()
    fd = open_noatime(path)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            expected.st_dev,
            expected.st_ino,
            expected.st_size,
        ):
            fail(f"file changed identity while hashing: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def capture_paths(cache: Path, fingerprint: str) -> tuple[Path, dict[str, Any]]:
    root = cache / "inputs" / fingerprint
    manifest = load_json(root / "manifest.json", SCHEMA_CAPTURE)
    if manifest.get("package_fingerprint") != fingerprint:
        fail("capture manifest fingerprint mismatch")
    return root, manifest


def output_manifest_path(cache: Path, fingerprint: str) -> Path:
    return cache / "outputs" / fingerprint / "manifest.json"


def find_artifact(manifest: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("artifact_id") == artifact_id
    ]
    if len(matches) != 1:
        fail(f"artifact ID is absent or ambiguous: {artifact_id}")
    artifact = matches[0]
    if not artifact.get("eligible"):
        fail(f"cannot register output for ineligible artifact: {artifact_id}")
    return artifact


def command_register(arguments: argparse.Namespace) -> None:
    output_source = Path(arguments.output).resolve(strict=True)
    if not output_source.is_file() or output_source.is_symlink():
        fail(f"prepared output is not a regular file: {output_source}")
    cache_raw = Path(arguments.cache_root)
    cache_raw.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache = cache_raw.resolve(strict=True)
    if cache in (Path("/"), Path("/usr")):
        fail(f"refusing unsafe cache root: {cache}")
    fingerprint = validate_fingerprint(arguments.fingerprint)
    readelf = shutil.which(arguments.readelf)
    objcopy = shutil.which(arguments.objcopy)
    if readelf is None or objcopy is None:
        fail("output registration requires readelf and objcopy")
    _, capture = capture_paths(cache, fingerprint)
    source_artifact = find_artifact(capture, arguments.artifact_id)
    with tempfile.TemporaryDirectory(prefix="bolt-output-classify-", dir=cache) as temporary:
        classification = classify_elf(
            output_source, readelf, objcopy, Path(temporary)
        )
    if not classification["has_bolt_info"]:
        fail("prepared output lacks .note.bolt_info")
    for key in ("elf_class", "elf_type", "machine"):
        if classification[key] != source_artifact[key]:
            fail(f"prepared output {key} differs from captured input")

    output_root = cache / "outputs" / fingerprint
    objects = output_root / "objects"
    objects.mkdir(mode=0o700, parents=True, exist_ok=True)
    manifest_path = output_root / "manifest.json"
    if manifest_path.exists():
        document = load_json(manifest_path, SCHEMA_OUTPUT)
        if document.get("package_fingerprint") != fingerprint:
            fail("output manifest fingerprint mismatch")
    else:
        document = {
            "schema": SCHEMA_OUTPUT,
            "package_fingerprint": fingerprint,
            "outputs": [],
        }
    outputs = document.get("outputs")
    if not isinstance(outputs, list):
        fail("output manifest outputs is not a list")
    if any(item.get("artifact_id") == arguments.artifact_id for item in outputs):
        fail(f"output already registered for artifact: {arguments.artifact_id}")

    destination = objects / f"{arguments.artifact_id}.bolt"
    partial = objects / f".{arguments.artifact_id}.bolt.partial.{os.getpid()}"
    try:
        with output_source.open("rb") as input_stream, partial.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        entry = {
            "artifact_id": arguments.artifact_id,
            "output_object": f"objects/{arguments.artifact_id}.bolt",
            "output_sha256": sha256_file(partial),
            "output_build_id": classification["build_id"],
            "output_text_sha256": classification["text_sha256"],
            "source_file_sha256": source_artifact["file_sha256"],
            "source_build_id": source_artifact["build_id"],
            "source_text_sha256": source_artifact["text_sha256"],
            "elf_class": classification["elf_class"],
            "elf_type": classification["elf_type"],
            "machine": classification["machine"],
            "has_bolt_info": True,
        }
        os.replace(partial, destination)
        outputs.append(entry)
        outputs.sort(key=lambda item: item["artifact_id"])
        write_json_atomic(manifest_path, document)
    finally:
        partial.unlink(missing_ok=True)
    print(destination)


def current_identity(
    source: Path,
    expected_stat: os.stat_result,
    readelf: str,
    objcopy: str,
    scratch_root: Path,
) -> tuple[dict[str, Any], str]:
    scratch = scratch_root / hashlib.sha256(str(source).encode()).hexdigest()
    copy_noatime(source, scratch, expected_stat)
    classification = classify_elf(scratch, readelf, objcopy, scratch_root / "sections")
    return classification, sha256_file(scratch)


def verify_metadata_matches(path: Path, expected: dict[str, Any]) -> None:
    actual = metadata(path)
    if actual != expected:
        fail(f"metadata mismatch for {path}: expected {expected}, got {actual}")


def apply_metadata(path: Path, expected: dict[str, Any]) -> None:
    desired_uid = int(expected["uid"])
    desired_gid = int(expected["gid"])
    current = path.stat()
    if (current.st_uid, current.st_gid) != (desired_uid, desired_gid):
        try:
            os.chown(path, desired_uid, desired_gid)
        except OSError as error:
            fail(f"cannot preserve ownership for {path}: {error}")
    os.chmod(path, int(expected["mode"], 8))
    desired_xattrs = expected.get("xattrs_base64", {})
    if not isinstance(desired_xattrs, dict):
        fail(f"invalid xattr metadata for {path}")
    for name in os.listxattr(path):
        if name not in desired_xattrs:
            try:
                os.removexattr(path, name)
            except OSError as error:
                fail(f"cannot remove unexpected xattr {name!r} from {path}: {error}")
    for name, encoded in desired_xattrs.items():
        try:
            value = base64.b64decode(encoded, validate=True)
            os.setxattr(path, name, value)
        except (ValueError, OSError) as error:
            fail(f"cannot preserve xattr {name!r} for {path}: {error}")


def stage_hardlink_group(
    source: Path, paths: list[Path], metadata_record: dict[str, Any], token: str
) -> list[tuple[Path, Path]]:
    stages: list[tuple[Path, Path]] = []
    first = paths[0].with_name(f".{paths[0].name}.bolt-partial-{token}-0")
    try:
        with source.open("rb") as input_stream, first.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        apply_metadata(first, metadata_record)
        stages.append((first, paths[0]))
        for index, path in enumerate(paths[1:], 1):
            partial = path.with_name(f".{path.name}.bolt-partial-{token}-{index}")
            os.link(first, partial, follow_symlinks=False)
            stages.append((partial, path))
        return stages
    except BaseException:
        for partial, _ in stages:
            partial.unlink(missing_ok=True)
        first.unlink(missing_ok=True)
        raise


def verify_symlinks(ed: Path, expected: Any) -> None:
    if not isinstance(expected, list):
        fail("capture symlink manifest is not a list")
    _, current = scan_tree(ed)
    if current != expected:
        fail("ED symlink topology differs from captured input")


def command_deploy(arguments: argparse.Namespace) -> None:
    ed = validate_root(arguments.ed, "ED")
    cache = validate_cache_root(arguments.cache_root, ed)
    fingerprint = validate_fingerprint(arguments.fingerprint)
    readelf = shutil.which(arguments.readelf)
    objcopy = shutil.which(arguments.objcopy)
    if readelf is None or objcopy is None:
        fail("deployment requires readelf and objcopy")
    _, capture = capture_paths(cache, fingerprint)
    output_root = cache / "outputs" / fingerprint
    output_manifest = load_json(output_root / "manifest.json", SCHEMA_OUTPUT)
    if output_manifest.get("package_fingerprint") != fingerprint:
        fail("output manifest fingerprint mismatch")
    output_entries = output_manifest.get("outputs")
    if not isinstance(output_entries, list):
        fail("output manifest outputs is not a list")
    outputs_by_id = {item.get("artifact_id"): item for item in output_entries}
    if len(outputs_by_id) != len(output_entries):
        fail("duplicate artifact IDs in output manifest")

    verify_symlinks(ed, capture.get("symlinks"))
    artifacts = capture.get("artifacts")
    if not isinstance(artifacts, list):
        fail("capture artifacts is not a list")
    eligible = [item for item in artifacts if item.get("eligible")]
    if not eligible:
        fail("deployment requested for a package with no BOLT-eligible ELF")
    if set(outputs_by_id) != {item.get("artifact_id") for item in eligible}:
        fail("prepared BOLT outputs do not exactly cover captured eligible artifacts")

    diagnostics = cache / "diagnostics" / fingerprint / "pre-deploy"
    diagnostics.mkdir(mode=0o700, parents=True, exist_ok=True)
    prepared: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="bolt-deploy-validate-", dir=cache) as temporary:
        scratch_root = Path(temporary)
        for index, artifact in enumerate(eligible):
            artifact_id = artifact["artifact_id"]
            relative_paths = artifact.get("paths")
            if not isinstance(relative_paths, list) or not relative_paths:
                fail(f"invalid hardlink paths for {artifact_id}")
            paths = [path_from_relative(ed, value) for value in relative_paths]
            stats = [path.lstat() for path in paths]
            if not all(stat.S_ISREG(item.st_mode) for item in stats):
                fail(f"candidate path is no longer regular: {artifact_id}")
            inode_keys = {(item.st_dev, item.st_ino) for item in stats}
            if len(inode_keys) != 1:
                fail(f"hardlink topology differs for {artifact_id}")
            verify_metadata_matches(paths[0], artifact["metadata"])
            identity, file_sha = current_identity(
                paths[0], stats[0], readelf, objcopy, scratch_root / f"current-{index}"
            )
            if identity["build_id"] != artifact["build_id"]:
                fail(f"GNU build ID mismatch for {artifact['canonical_path']}")
            if identity["text_sha256"] != artifact["text_sha256"]:
                fail(f".text hash mismatch for {artifact['canonical_path']}")
            if file_sha != artifact["file_sha256"]:
                fail(f"full-file hash mismatch for {artifact['canonical_path']}")

            output_record = outputs_by_id[artifact_id]
            for key, source_key in (
                ("source_file_sha256", "file_sha256"),
                ("source_build_id", "build_id"),
                ("source_text_sha256", "text_sha256"),
            ):
                if output_record.get(key) != artifact.get(source_key):
                    fail(f"prepared output input identity mismatch for {artifact_id}: {key}")
            output_object = output_record.get("output_object")
            if not isinstance(output_object, str):
                fail(f"missing output object for {artifact_id}")
            output_path = path_from_relative(output_root, output_object)
            if not output_path.is_file() or output_path.is_symlink():
                fail(f"prepared output is absent or not regular: {output_path}")
            if sha256_file(output_path) != output_record.get("output_sha256"):
                fail(f"prepared output hash mismatch for {artifact_id}")
            output_class = classify_elf(
                output_path, readelf, objcopy, scratch_root / f"output-{index}"
            )
            if not output_class["has_bolt_info"]:
                fail(f"prepared output lacks .note.bolt_info: {artifact_id}")
            for key in ("elf_class", "elf_type", "machine"):
                if output_class[key] != artifact[key]:
                    fail(f"prepared output {key} mismatch for {artifact_id}")
            prepared.append(
                {
                    "artifact": artifact,
                    "paths": paths,
                    "output": output_path,
                    "current": scratch_root / f"diagnostic-source-{index}",
                }
            )
            copy_noatime(paths[0], prepared[-1]["current"], stats[0])

        # All identities, outputs, topology, and metadata are valid before ED mutation.
        staged_groups: list[list[tuple[Path, Path]]] = []
        token = f"{os.getpid()}"
        try:
            for item in prepared:
                staged_groups.append(
                    stage_hardlink_group(
                        item["output"], item["paths"], item["artifact"]["metadata"], token
                    )
                )
            for item in prepared:
                diagnostic = diagnostics / f"{item['artifact']['artifact_id']}.elf"
                if diagnostic.exists():
                    if sha256_file(diagnostic) != item["artifact"]["file_sha256"]:
                        fail(f"diagnostic input collision: {diagnostic}")
                else:
                    partial = diagnostic.with_name(f".{diagnostic.name}.partial.{os.getpid()}")
                    shutil.copyfile(item["current"], partial)
                    os.replace(partial, diagnostic)
            for stages in staged_groups:
                for partial, destination in stages:
                    os.replace(partial, destination)
        except BaseException:
            for stages in staged_groups:
                for partial, _ in stages:
                    partial.unlink(missing_ok=True)
            # Restore every group from the exact pre-deploy copies when any rename ran.
            for item in prepared:
                restore = stage_hardlink_group(
                    item["current"], item["paths"], item["artifact"]["metadata"], f"restore-{token}"
                )
                for partial, destination in restore:
                    os.replace(partial, destination)
            raise

    # Verify final identity, BOLT note, metadata, hardlinks, and symlink topology.
    verify_symlinks(ed, capture.get("symlinks"))
    with tempfile.TemporaryDirectory(prefix="bolt-deploy-verify-", dir=cache) as temporary:
        for index, item in enumerate(prepared):
            paths = item["paths"]
            stats = [path.lstat() for path in paths]
            if len({(entry.st_dev, entry.st_ino) for entry in stats}) != 1:
                fail(f"deployed hardlink topology mismatch: {item['artifact']['artifact_id']}")
            verify_metadata_matches(paths[0], item["artifact"]["metadata"])
            final_class = classify_elf(
                paths[0], readelf, objcopy, Path(temporary) / f"{index}"
            )
            if not final_class["has_bolt_info"]:
                fail(f"deployed file lacks .note.bolt_info: {paths[0]}")
            expected = outputs_by_id[item["artifact"]["artifact_id"]]
            if sha256_file(paths[0]) != expected["output_sha256"]:
                fail(f"deployed file hash mismatch: {paths[0]}")
    print(output_manifest_path(cache, fingerprint))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="capture eligible unstripped ED inputs")
    capture.add_argument("--ed", required=True)
    capture.add_argument("--cache-root", required=True)
    capture.add_argument("--fingerprint", required=True)
    capture.add_argument("--readelf", default="readelf")
    capture.add_argument("--objcopy", default="objcopy")
    capture.set_defaults(function=command_capture)

    register = subparsers.add_parser("register-output", help="register one prepared BOLT output")
    register.add_argument("--cache-root", required=True)
    register.add_argument("--fingerprint", required=True)
    register.add_argument("--artifact-id", required=True)
    register.add_argument("--output", required=True)
    register.add_argument("--readelf", default="readelf")
    register.add_argument("--objcopy", default="objcopy")
    register.set_defaults(function=command_register)

    deploy = subparsers.add_parser("deploy", help="deploy exact prepared outputs into ED")
    deploy.add_argument("--ed", required=True)
    deploy.add_argument("--cache-root", required=True)
    deploy.add_argument("--fingerprint", required=True)
    deploy.add_argument("--readelf", default="readelf")
    deploy.add_argument("--objcopy", default="objcopy")
    deploy.set_defaults(function=command_deploy)
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    handled_signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous_handlers: dict[signal.Signals, Any] = {}

    def interrupted(signum: int, _frame: Any) -> NoReturn:
        for item, previous in previous_handlers.items():
            signal.signal(item, previous)
        raise BoltArtifactError(f"interrupted by signal {signum}")

    for item in handled_signals:
        previous_handlers[item] = signal.getsignal(item)
        signal.signal(item, interrupted)
    try:
        arguments.function(arguments)
    except BoltArtifactError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130
    finally:
        for item, previous in previous_handlers.items():
            signal.signal(item, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

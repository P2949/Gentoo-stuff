from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import signal
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[3]
VERIFIER = (
    REPOSITORY
    / "scripts"
    / "optimization"
    / "recovery"
    / "verify-binpkg-snapshot.py"
)


def load_verifier_module():
    module_name = "gentoo_optimization_verify_binpkg_snapshot_test_target"
    spec = importlib.util.spec_from_file_location(module_name, VERIFIER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load verifier module from {VERIFIER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


VERIFIER_MODULE = load_verifier_module()


class SnapshotFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.snapshot = root / "snapshot"
        self.vdb = root / "vdb"
        self.snapshot.mkdir()
        self.vdb.mkdir()

    def install(self, cpv: str) -> None:
        (self.vdb / cpv).mkdir(parents=True)

    def add_archive(self, relative: str, data: bytes) -> Path:
        path = self.snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    @staticmethod
    def record(cpv: str, relative: str, data: bytes, **overrides: str) -> dict[str, str]:
        fields = {
            "CPV": cpv,
            "PATH": relative,
            "SIZE": str(len(data)),
            "MD5": hashlib.md5(data).hexdigest(),
            "SHA1": hashlib.sha1(data).hexdigest(),
        }
        fields.update(overrides)
        return fields

    def write_index(self, records: list[dict[str, str]]) -> None:
        stanzas = [f"PACKAGES: {len(records)}\nVERSION: 0"]
        order = ("CPV", "PATH", "SIZE", "MD5", "SHA1")
        for record in records:
            stanzas.append(
                "\n".join(f"{key}: {record[key]}" for key in order if key in record)
            )
        (self.snapshot / "Packages").write_text(
            "\n\n".join(stanzas) + "\n", encoding="utf-8"
        )


def make_inner_tar(prefix: str, filename: str, data: bytes) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        directory = tarfile.TarInfo(prefix)
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)
        member = tarfile.TarInfo(f"{prefix}/{filename}")
        member.size = len(data)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def zstd_compress(data: bytes) -> bytes:
    return subprocess.run(
        ["zstd", "--quiet", "--stdout", "-"],
        input=data,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout


def manifest_for(members: list[tuple[str, bytes]]) -> bytes:
    lines = []
    for name, data in members:
        lines.append(
            " ".join(
                (
                    "DATA",
                    name,
                    str(len(data)),
                    "BLAKE2B",
                    hashlib.blake2b(data).hexdigest(),
                    "SHA512",
                    hashlib.sha512(data).hexdigest(),
                )
            )
        )
    return ("\n".join(lines) + "\n").encode()


def make_gpkg(
    *, image_data: bytes | None = None, duplicate_identifier: bool = False
) -> bytes:
    metadata = zstd_compress(make_inner_tar("metadata", "CPV", b"cat/pkg-1\n"))
    image = (
        zstd_compress(make_inner_tar("image", "payload", b"payload"))
        if image_data is None
        else image_data
    )
    content_members = [
        ("gpkg-1", b""),
        ("metadata.tar.zst", metadata),
        ("image.tar.zst", image),
    ]
    manifest = manifest_for(content_members)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in [*content_members, ("Manifest", manifest)]:
            member = tarfile.TarInfo(f"pkg-1/{name}")
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
            if duplicate_identifier and name == "gpkg-1":
                archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def open_tar_member(data: bytes) -> tuple[tarfile.TarFile, tarfile.TarInfo]:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo("image.tar.zst")
        member.size = len(data)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(data))
    output.seek(0)
    archive = tarfile.open(fileobj=output, mode="r:")
    return archive, archive.getmember("image.tar.zst")


class VerifyBinpkgSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = SnapshotFixture(Path(self.temporary.name))

    def fake_zstd(self, source: str, name: str = "zstd") -> Path:
        tool = self.fixture.root / name
        tool.write_text(f"#!/usr/bin/python3\n{source}", encoding="utf-8")
        tool.chmod(0o755)
        return tool

    def run_zstd_member(
        self,
        tool: Path,
        *,
        data: bytes = b"compressed image payload",
        timeout_seconds: float = 1.0,
        kill_after_seconds: float = 0.2,
    ) -> tuple[bool, int | None, str]:
        archive, member = open_tar_member(data)
        self.addCleanup(archive.close)
        temporary_directory = self.fixture.root / "zstd-temporary"
        temporary_directory.mkdir(exist_ok=True)
        result = VERIFIER_MODULE._test_zstd_member(
            archive,
            member,
            str(tool),
            timeout_seconds=timeout_seconds,
            kill_after_seconds=kill_after_seconds,
            temporary_directory=temporary_directory,
        )
        self.assertEqual(list(temporary_directory.iterdir()), [])
        return result

    @staticmethod
    def process_is_live(pid: int) -> bool:
        try:
            line = Path(f"/proc/{pid}/stat").read_bytes()
        except FileNotFoundError:
            return False
        separator = line.rfind(b") ")
        if separator < 0:
            return True
        fields = line[separator + 2 :].split()
        return not fields or fields[0] not in {b"Z", b"X", b"x"}

    def run_verifier(self, *arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        process = subprocess.run(
            [
                sys.executable,
                str(VERIFIER),
                "--snapshot",
                str(self.fixture.snapshot),
                "--vdb",
                str(self.fixture.vdb),
                *arguments,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(process.stderr, "")
        return process, json.loads(process.stdout)

    @staticmethod
    def issue_codes(report: dict) -> set[str]:
        return {issue["code"] for issue in report["issues"]}

    def add_one_valid_record(self, data: bytes = b"archive") -> None:
        cpv = "cat/pkg-1"
        relative = "cat/pkg/pkg-1-1.gpkg.tar"
        self.fixture.install(cpv)
        self.fixture.add_archive(relative, data)
        self.fixture.write_index([self.fixture.record(cpv, relative, data)])

    def test_zstd_member_fake_success_consumes_staged_input(self) -> None:
        tool = self.fake_zstd(
            "import pathlib, sys\n"
            "payload = pathlib.Path(sys.argv[-1]).read_bytes()\n"
            "raise SystemExit(0 if payload == b'expected payload' else 91)\n"
        )
        self.assertEqual(
            self.run_zstd_member(tool, data=b"expected payload"),
            (True, 0, ""),
        )

    def test_zstd_member_fake_malformed_stream_reports_first_line(self) -> None:
        tool = self.fake_zstd(
            "import pathlib, sys\n"
            "pathlib.Path(sys.argv[-1]).read_bytes()\n"
            "sys.stderr.write(f'{sys.argv[-1]}: malformed zstd stream\\n'"
            " + 'secondary detail\\n')\n"
            "raise SystemExit(7)\n"
        )
        self.assertEqual(
            self.run_zstd_member(tool),
            (False, 7, "<staged-image>: malformed zstd stream"),
        )

    def test_zstd_member_hang_is_bounded_in_private_process_group(self) -> None:
        identity = self.fixture.root / "zstd-hang.identity"
        tool = self.fake_zstd(
            "import os, time\n"
            f"open({str(identity)!r}, 'w', encoding='utf-8').write("
            "f'{os.getpid()} {os.getpgrp()}\\n')\n"
            "while True:\n"
            "    time.sleep(1)\n"
        )
        started = time.monotonic()
        ok, returncode, detail = self.run_zstd_member(
            tool, timeout_seconds=1.0, kill_after_seconds=0.2
        )
        elapsed = time.monotonic() - started
        self.assertFalse(ok)
        self.assertEqual(returncode, -signal.SIGTERM)
        self.assertEqual(detail, "zstd test timed out after 1 seconds")
        self.assertLess(elapsed, 3.0)
        pid, process_group = map(int, identity.read_text(encoding="utf-8").split())
        self.assertEqual(process_group, pid)
        self.assertFalse(self.process_is_live(pid))

    def test_zstd_member_consumes_input_but_never_exits(self) -> None:
        consumed = self.fixture.root / "zstd-consumed"
        tool = self.fake_zstd(
            "import pathlib, sys, time\n"
            "pathlib.Path(sys.argv[-1]).read_bytes()\n"
            f"pathlib.Path({str(consumed)!r}).write_text('consumed\\n', "
            "encoding='utf-8')\n"
            "while True:\n"
            "    time.sleep(1)\n"
        )
        started = time.monotonic()
        ok, returncode, detail = self.run_zstd_member(
            tool, timeout_seconds=1.0, kill_after_seconds=0.2
        )
        elapsed = time.monotonic() - started
        self.assertFalse(ok)
        self.assertEqual(returncode, -signal.SIGTERM)
        self.assertEqual(detail, "zstd test timed out after 1 seconds")
        self.assertEqual(consumed.read_text(encoding="utf-8"), "consumed\n")
        self.assertLess(elapsed, 3.0)

    def test_zstd_member_closed_stderr_hang_sleeps_until_bounded_timeout(
        self,
    ) -> None:
        identity = self.fixture.root / "zstd-closed-stderr.identity"
        tool = self.fake_zstd(
            "import os, time\n"
            f"open({str(identity)!r}, 'w', encoding='utf-8').write("
            "f'{os.getpid()} {os.getpgrp()}\\n')\n"
            "os.close(2)\n"
            "while True:\n"
            "    time.sleep(1)\n"
        )
        started = time.monotonic()
        cpu_started = time.process_time()
        ok, returncode, detail = self.run_zstd_member(
            tool, timeout_seconds=1.0, kill_after_seconds=0.2
        )
        cpu_elapsed = time.process_time() - cpu_started
        elapsed = time.monotonic() - started
        self.assertFalse(ok)
        self.assertEqual(returncode, -signal.SIGTERM)
        self.assertEqual(detail, "zstd test timed out after 1 seconds")
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 3.0)
        self.assertLess(cpu_elapsed, 0.5)
        pid, process_group = map(int, identity.read_text(encoding="utf-8").split())
        self.assertEqual(process_group, pid)
        self.assertFalse(self.process_is_live(pid))

    def test_zstd_member_term_ignoring_group_is_killed_and_reaped(self) -> None:
        identity = self.fixture.root / "zstd-ignore-term.identity"
        tool = self.fake_zstd(
            "import os, pathlib, signal, sys, time\n"
            "pathlib.Path(sys.argv[-1]).read_bytes()\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    while True:\n"
            "        time.sleep(1)\n"
            f"open({str(identity)!r}, 'w', encoding='utf-8').write("
            "f'{os.getpid()} {os.getpgrp()} {child}\\n')\n"
            "while True:\n"
            "    time.sleep(1)\n"
        )
        started = time.monotonic()
        ok, returncode, detail = self.run_zstd_member(
            tool, timeout_seconds=1.0, kill_after_seconds=0.2
        )
        elapsed = time.monotonic() - started
        self.assertFalse(ok)
        self.assertEqual(returncode, -signal.SIGKILL)
        self.assertEqual(detail, "zstd test timed out after 1 seconds")
        self.assertLess(elapsed, 3.0)
        pid, process_group, child = map(
            int, identity.read_text(encoding="utf-8").split()
        )
        self.assertEqual(process_group, pid)
        self.assertFalse(self.process_is_live(pid))
        self.assertFalse(self.process_is_live(child))

    def test_zstd_member_stderr_flood_is_drained_and_diagnostic_is_bounded(
        self,
    ) -> None:
        tool = self.fake_zstd(
            "import os\n"
            "chunk = b'x' * 65536\n"
            "for _ in range(128):\n"
            "    os.write(2, chunk)\n"
            "raise SystemExit(9)\n"
        )
        started = time.monotonic()
        ok, returncode, detail = self.run_zstd_member(tool, timeout_seconds=2.0)
        elapsed = time.monotonic() - started
        self.assertFalse(ok)
        self.assertEqual(returncode, 9)
        self.assertIn("diagnostic truncated", detail)
        self.assertLessEqual(len(detail), 425)
        self.assertLess(elapsed, 2.0)

    def test_zstd_member_infinite_stderr_cannot_starve_shared_deadline(self) -> None:
        identity = self.fixture.root / "zstd-infinite-stderr.identity"
        tool = self.fake_zstd(
            "import os\n"
            f"open({str(identity)!r}, 'w', encoding='utf-8').write("
            "f'{os.getpid()} {os.getpgrp()}\\n')\n"
            "chunk = b'y' * 65536\n"
            "while True:\n"
            "    os.write(2, chunk)\n"
        )
        started = time.monotonic()
        ok, returncode, detail = self.run_zstd_member(
            tool, timeout_seconds=1.0, kill_after_seconds=0.2
        )
        elapsed = time.monotonic() - started
        self.assertFalse(ok)
        self.assertEqual(returncode, -signal.SIGTERM)
        self.assertEqual(detail, "zstd test timed out after 1 seconds")
        self.assertLess(elapsed, 3.0)
        pid, process_group = map(int, identity.read_text(encoding="utf-8").split())
        self.assertEqual(process_group, pid)
        self.assertFalse(self.process_is_live(pid))

    def test_zstd_member_complete_deadline_includes_staging_and_cleans_temp(
        self,
    ) -> None:
        class SlowStream(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                time.sleep(0.08)
                return super().read(size)

        class SlowContainer:
            def extractfile(self, _member: tarfile.TarInfo) -> SlowStream:
                return SlowStream(b"data")

        temporary_directory = self.fixture.root / "slow-zstd-temporary"
        temporary_directory.mkdir()
        started = time.monotonic()
        result = VERIFIER_MODULE._test_zstd_member(
            SlowContainer(),
            tarfile.TarInfo("image.tar.zst"),
            str(self.fixture.root / "must-not-execute"),
            timeout_seconds=0.02,
            kill_after_seconds=0.1,
            temporary_directory=temporary_directory,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(
            result,
            (
                False,
                None,
                "zstd test timed out after 0.02 seconds while staging the image stream",
            ),
        )
        self.assertLess(elapsed, 0.3)
        self.assertEqual(list(temporary_directory.iterdir()), [])

    def test_valid_snapshot_and_json_are_deterministic(self) -> None:
        self.add_one_valid_record()
        first_process, first = self.run_verifier()
        second_process, second = self.run_verifier()
        self.assertEqual(first_process.returncode, 0)
        self.assertEqual(first["status"], "pass")
        self.assertEqual(first["counts"]["live_cpvs"], 1)
        self.assertEqual(first_process.stdout, second_process.stdout)
        self.assertEqual(first, second)

    def test_live_cpv_must_have_exactly_one_indexed_archive(self) -> None:
        cpv = "cat/pkg-1"
        first = b"first"
        second = b"second"
        paths = ("cat/pkg/pkg-1-1.gpkg.tar", "cat/pkg/pkg-1-2.gpkg.tar")
        self.fixture.install(cpv)
        self.fixture.add_archive(paths[0], first)
        self.fixture.add_archive(paths[1], second)
        self.fixture.write_index(
            [
                self.fixture.record(cpv, paths[0], first),
                self.fixture.record(cpv, paths[1], second),
            ]
        )
        process, report = self.run_verifier()
        self.assertEqual(process.returncode, 1)
        self.assertIn("live_cpv_archive_count", self.issue_codes(report))

    def test_missing_live_cpv_is_reported(self) -> None:
        self.add_one_valid_record()
        self.fixture.install("cat/other-2")
        process, report = self.run_verifier()
        self.assertEqual(process.returncode, 1)
        self.assertEqual(report["coverage"]["missing_live_cpvs"], ["cat/other-2"])
        self.assertIn("live_cpv_missing_archive", self.issue_codes(report))

    def test_extra_indexed_archive_requires_explicit_switch(self) -> None:
        live_data = b"live"
        extra_data = b"extra"
        live_path = "cat/pkg/pkg-1-1.gpkg.tar"
        extra_path = "cat/old/old-1-1.gpkg.tar"
        self.fixture.install("cat/pkg-1")
        self.fixture.add_archive(live_path, live_data)
        self.fixture.add_archive(extra_path, extra_data)
        self.fixture.write_index(
            [
                self.fixture.record("cat/pkg-1", live_path, live_data),
                self.fixture.record("cat/old-1", extra_path, extra_data),
            ]
        )

        rejected, rejected_report = self.run_verifier()
        allowed, allowed_report = self.run_verifier("--allow-extra-archives")
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("extra_indexed_archive", self.issue_codes(rejected_report))
        self.assertEqual(allowed.returncode, 0)
        self.assertEqual(allowed_report["status"], "pass")
        self.assertEqual(allowed_report["counts"]["extra_indexed_archives"], 1)

    def test_unindexed_gpkg_is_detected_even_when_extras_are_allowed(self) -> None:
        self.add_one_valid_record()
        orphan = "orphan/unindexed.gpkg.tar"
        self.fixture.add_archive(orphan, b"orphan")
        process, report = self.run_verifier("--allow-extra-archives")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(report["coverage"]["unindexed_gpkg_archives"], [orphan])
        self.assertIn("unindexed_gpkg_archive", self.issue_codes(report))

    def test_size_md5_and_sha1_are_all_verified(self) -> None:
        cpv = "cat/pkg-1"
        data = b"actual archive"
        path = "cat/pkg/pkg-1-1.gpkg.tar"
        self.fixture.install(cpv)
        self.fixture.add_archive(path, data)
        self.fixture.write_index(
            [
                self.fixture.record(
                    cpv,
                    path,
                    data,
                    SIZE="1",
                    MD5="0" * 32,
                    SHA1="0" * 40,
                )
            ]
        )
        process, report = self.run_verifier()
        self.assertEqual(process.returncode, 1)
        codes = self.issue_codes(report)
        self.assertIn("indexed_archive_size_mismatch", codes)
        self.assertIn("indexed_archive_md5_mismatch", codes)
        self.assertIn("indexed_archive_sha1_mismatch", codes)

    def test_missing_and_non_regular_paths_fail(self) -> None:
        records = []
        for cpv, path in (
            ("cat/missing-1", "cat/missing/missing-1.gpkg.tar"),
            ("cat/directory-1", "cat/directory/directory-1.gpkg.tar"),
        ):
            self.fixture.install(cpv)
            records.append(self.fixture.record(cpv, path, b"unused"))
        (self.fixture.snapshot / records[1]["PATH"]).mkdir(parents=True)
        self.fixture.write_index(records)
        process, report = self.run_verifier()
        self.assertEqual(process.returncode, 1)
        codes = self.issue_codes(report)
        self.assertIn("indexed_archive_missing", codes)
        self.assertIn("indexed_archive_not_regular", codes)

    @unittest.skipUnless(shutil.which("zstd"), "zstd is required for GPKG tests")
    def test_gpkg_manifest_and_image_stream_validate(self) -> None:
        gpkg = make_gpkg()
        self.add_one_valid_record(gpkg)
        process, report = self.run_verifier("--validate-gpkg")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(report["counts"]["gpkg_archives_validated"], 1)
        self.assertEqual(report["counts"]["image_tar_zst_streams_tested"], 1)
        self.assertEqual(report["archives"][0]["gpkg"]["status"], "verified")

    @unittest.skipUnless(shutil.which("zstd"), "zstd is required for GPKG tests")
    def test_invalid_image_stream_fails_after_valid_manifest(self) -> None:
        gpkg = make_gpkg(image_data=b"not a zstd stream")
        self.add_one_valid_record(gpkg)
        process, report = self.run_verifier("--validate-gpkg")
        repeated_process, repeated_report = self.run_verifier("--validate-gpkg")
        self.assertEqual(process.returncode, 1)
        self.assertIn("gpkg_image_zstd_invalid", self.issue_codes(report))
        self.assertEqual(report["counts"]["image_tar_zst_streams_tested"], 0)
        self.assertEqual(process.stdout, repeated_process.stdout)
        self.assertEqual(report, repeated_report)

    @unittest.skipUnless(shutil.which("zstd"), "zstd is required for GPKG tests")
    def test_duplicate_outer_member_is_rejected(self) -> None:
        gpkg = make_gpkg(duplicate_identifier=True)
        self.add_one_valid_record(gpkg)
        process, report = self.run_verifier("--validate-gpkg")
        self.assertEqual(process.returncode, 1)
        self.assertIn("gpkg_outer_duplicate_member", self.issue_codes(report))


if __name__ == "__main__":
    unittest.main()

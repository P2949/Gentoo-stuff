from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
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


class VerifyBinpkgSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = SnapshotFixture(Path(self.temporary.name))

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
        self.assertEqual(process.returncode, 1)
        self.assertIn("gpkg_image_zstd_invalid", self.issue_codes(report))
        self.assertEqual(report["counts"]["image_tar_zst_streams_tested"], 0)

    @unittest.skipUnless(shutil.which("zstd"), "zstd is required for GPKG tests")
    def test_duplicate_outer_member_is_rejected(self) -> None:
        gpkg = make_gpkg(duplicate_identifier=True)
        self.add_one_valid_record(gpkg)
        process, report = self.run_verifier("--validate-gpkg")
        self.assertEqual(process.returncode, 1)
        self.assertIn("gpkg_outer_duplicate_member", self.issue_codes(report))


if __name__ == "__main__":
    unittest.main()

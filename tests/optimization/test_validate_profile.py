from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts/optimization/pgo/validate-profile.py"
FINGERPRINT = "a" * 64


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProfileValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="profile-validator-test-")
        self.root = Path(self.temporary.name).resolve()
        self.bin = self.root / "bin"
        self.profiles = self.root / "profiles"
        self.bin.mkdir()
        self.profiles.mkdir()
        self._write_tools()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_executable(self, name: str, source: str) -> Path:
        path = self.bin / name
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        path.chmod(0o755)
        return path

    def _write_tools(self) -> None:
        self.clang = self._write_executable(
            "clang",
            r"""
            #!/usr/bin/env python3
            import os
            import sys
            if os.environ.get("LC_ALL") != "C" or os.environ.get("LANG") != "C":
                raise SystemExit(9)
            if sys.argv[1:] != ["--version"]:
                raise SystemExit(2)
            print("clang version 22.1.8")
            """,
        )
        self.gcc = self._write_executable(
            "gcc",
            r"""
            #!/usr/bin/env python3
            import sys
            if sys.argv[1:] != ["--version"]:
                raise SystemExit(2)
            print("gcc (Gentoo fake) 15.1.0")
            """,
        )
        self.rustc = self._write_executable(
            "rustc",
            r"""
            #!/usr/bin/env python3
            import sys
            if sys.argv[1:] == ["--version"]:
                print("rustc 1.90.0")
            elif sys.argv[1:] == ["-vV"]:
                print("rustc 1.90.0\nLLVM version: 21.1.8")
            else:
                raise SystemExit(2)
            """,
        )
        self.llvm22 = self._write_executable(
            "llvm-profdata-22",
            r"""
            #!/usr/bin/env python3
            import pathlib
            import os
            import sys
            if os.environ.get("LC_ALL") != "C" or os.environ.get("LANG") != "C":
                raise SystemExit(9)
            args = sys.argv[1:]
            if args == ["--version"]:
                print("LLVM version 22.1.8")
                raise SystemExit(0)
            if not args or args[0] != "show":
                raise SystemExit(2)
            profile = pathlib.Path(args[-1])
            kind = profile.read_text(encoding="utf-8").strip()
            if "--sample" in args:
                if kind != "SAMPLE":
                    raise SystemExit(3)
                if "--all-functions" in args and "--counts" in args:
                    print("Function: target.hot")
                    print("120, 20, 2 sampled lines")
                else:
                    print("sample validation")
            else:
                if kind != "IR" or "--all-functions" not in args or "--counts" not in args:
                    raise SystemExit(4)
                print("Counters:\n  target:\n    Block counts: [12]")
                print("Total functions: 1")
                print("Maximum function count: 12")
            """,
        )
        self.llvm21 = self._write_executable(
            "llvm-profdata-21",
            self.llvm22.read_text(encoding="utf-8").replace(
                "LLVM version 22.1.8", "LLVM version 21.1.8"
            ),
        )
        self.sample_readelf = self._write_executable(
            "llvm-readelf-22",
            r"""
            #!/usr/bin/env python3
            import sys
            if sys.argv[1:] == ["--version"]:
                print("LLVM version 22.1.8")
                raise SystemExit(0)
            if len(sys.argv) == 3 and sys.argv[1] == "-n":
                print("    Build ID: abcdef1234567890")
                raise SystemExit(0)
            raise SystemExit(2)
            """,
        )
        self.sample_objcopy = self._write_executable(
            "llvm-objcopy-22",
            r"""
            #!/usr/bin/env python3
            import pathlib
            import shutil
            import sys
            if sys.argv[1:] == ["--version"]:
                print("LLVM version 22.1.8")
                raise SystemExit(0)
            if len(sys.argv) == 5 and sys.argv[1] == "--dump-section":
                section, output = sys.argv[2].split("=", 1)
                if section != ".text":
                    raise SystemExit(3)
                pathlib.Path(output).write_bytes(b"TEXT\n")
                shutil.copyfile(sys.argv[3], sys.argv[4])
                raise SystemExit(0)
            raise SystemExit(2)
            """,
        )
        self.gcov = self._write_executable(
            "gcov-tool",
            r"""
            #!/usr/bin/env python3
            import pathlib
            import os
            import sys
            if os.environ.get("LC_ALL") != "C" or os.environ.get("LANG") != "C":
                raise SystemExit(9)
            args = sys.argv[1:]
            if args == ["--version"]:
                print("gcov-tool (Gentoo 15.1.0) 15.1.0")
                raise SystemExit(0)
            if len(args) != 4 or args[:2] != ["overlap", "-f"] or args[2] != args[3]:
                raise SystemExit(2)
            count = len(list(pathlib.Path(args[2]).rglob("*.gcda")))
            print(f"    gcda files: {count} {count} {count}")
            print(f"     hot files: {count} {count} {count}")
            """,
        )
        self.go = self._write_executable(
            "go",
            r"""
            #!/usr/bin/env python3
            import pathlib
            import sys
            args = sys.argv[1:]
            if args == ["version"]:
                print("go version go1.27 linux/amd64")
                raise SystemExit(0)
            if len(args) == 3 and args[:2] == ["tool", "buildid"]:
                print("go-build-id-exact")
                raise SystemExit(0)
            if len(args) == 5 and args[:3] == ["tool", "pprof", "-raw"]:
                binary, profile = args[3:]
                kind = pathlib.Path(profile).read_text(encoding="utf-8").strip()
                if kind in {"GO-GOOD", "GO-NATIVE"}:
                    symbol = "example.com/target.Hot"
                elif kind == "GO-UNRELATED":
                    symbol = "example.com/unrelated.Hot"
                else:
                    raise SystemExit(3)
                print("PeriodType: cpu nanoseconds")
                print("Samples:")
                print("samples/count cpu/nanoseconds")
                print("          3   30000000: 1")
                print("Locations")
                print(f"     1: 0x401000 M=1 {symbol} /src/work.go:12:0 s=10")
                print("Mappings")
                mapping_id = "go-build-id-exact" if kind == "GO-NATIVE" else "abcdef1234567890"
                print(f"1: 0x400000/0x500000/0x0 {binary} {mapping_id} [FN]")
                raise SystemExit(0)
            raise SystemExit(2)
            """,
        )
        self.readelf = self._write_executable(
            "readelf",
            """
            #!/usr/bin/env python3
            import sys
            if len(sys.argv) != 3 or sys.argv[1] != "-n":
                raise SystemExit(2)
            print("    Build ID: abcdef1234567890")
            """,
        )
        self.readelf_no_id = self._write_executable(
            "readelf-no-id",
            """
            #!/usr/bin/env python3
            import sys
            if len(sys.argv) != 3 or sys.argv[1] != "-n":
                raise SystemExit(2)
            print("No GNU build ID")
            """,
        )

    def _common(
        self,
        backend: str,
        profile: Path,
        compiler_family: str,
        compiler: Path,
        compiler_major: int,
        profile_tool: Path,
        profile_tool_major: int,
        manifest: Path | None = None,
        metadata: Path | None = None,
    ) -> list[str]:
        manifest_path = manifest or (self.root / f"{backend}.manifest")
        return [
            os.fspath(VALIDATOR),
            "produce",
            "--backend",
            backend,
            "--profile",
            os.fspath(profile),
            "--fingerprint",
            FINGERPRINT,
            "--abi",
            "amd64",
            "--compiler-family",
            compiler_family,
            "--compiler",
            os.fspath(compiler),
            "--compiler-sha256",
            sha256(compiler),
            "--compiler-major",
            str(compiler_major),
            "--profile-tool",
            os.fspath(profile_tool),
            "--profile-tool-sha256",
            sha256(profile_tool),
            "--profile-tool-major",
            str(profile_tool_major),
            "--manifest-out",
            os.fspath(manifest_path),
            "--metadata-out",
            os.fspath(metadata or Path(os.fspath(manifest_path) + ".metadata.json")),
        ]

    def _run(self, arguments: list[str], success: bool) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            arguments,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if success and completed.returncode != 0:
            self.fail(f"command failed:\n{completed.stderr}")
        if not success and completed.returncode == 0:
            self.fail("command unexpectedly succeeded")
        return completed

    def _assert_manifest(
        self, path: Path, backend: str, family: str, profile: Path, profile_hash: str
    ) -> None:
        expected = (
            "schema=gentoo-optimization-profile-v1\n"
            f"backend={backend}\n"
            f"fingerprint={FINGERPRINT}\n"
            "abi=amd64\n"
            f"compiler_family={family}\n"
            f"profile_path={profile}\n"
            f"profile_sha256={profile_hash}\n"
            "validation_status=passed\n"
        )
        self.assertEqual(path.read_text(encoding="ascii"), expected)
        self.assertEqual(len(expected.splitlines()), 8)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        metadata = Path(os.fspath(path) + ".metadata.json")
        self.assertEqual(metadata.stat().st_mode & 0o777, 0o600)
        self._run(
            [
                os.fspath(VALIDATOR),
                "verify",
                "--manifest",
                os.fspath(path),
                "--metadata",
                os.fspath(metadata),
            ],
            success=True,
        )

    def test_clang_ir_success_and_atomic_existing_output_rejection(self) -> None:
        profile = self.profiles / "merged.profdata"
        profile.write_text("IR\n", encoding="utf-8")
        manifest = self.root / "clang.manifest"
        arguments = self._common(
            "clang-ir", profile, "clang", self.clang, 22, self.llvm22, 22, manifest
        )
        self._run(arguments, success=True)
        self._assert_manifest(manifest, "clang-ir", "clang", profile, sha256(profile))
        original = manifest.read_bytes()
        self._run(arguments, success=False)
        self.assertEqual(manifest.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".*.partial.*")), [])

        # Consumer verification re-observes the complete compiler tuple.
        self.clang.write_text(
            self.clang.read_text(encoding="utf-8") + "\n# changed\n",
            encoding="utf-8",
        )
        completed = self._run(
            [
                os.fspath(VALIDATOR),
                "verify",
                "--manifest",
                os.fspath(manifest),
                "--metadata",
                os.fspath(Path(os.fspath(manifest) + ".metadata.json")),
            ],
            success=False,
        )
        self.assertIn("compiler binary SHA-256", completed.stderr)

    def test_cross_family_compiler_is_rejected_without_manifest(self) -> None:
        profile = self.profiles / "cross.profdata"
        profile.write_text("IR\n", encoding="utf-8")
        manifest = self.root / "cross.manifest"
        arguments = self._common(
            "clang-ir", profile, "clang", self.gcc, 15, self.llvm22, 22, manifest
        )
        completed = self._run(arguments, success=False)
        self.assertIn("requested clang family", completed.stderr)
        self.assertFalse(manifest.exists())

    def test_x86_abi_is_preserved_as_a_distinct_manifest_axis(self) -> None:
        profile = self.profiles / "x86.profdata"
        profile.write_text("IR\n", encoding="utf-8")
        manifest = self.root / "x86.manifest"
        arguments = self._common(
            "clang-ir", profile, "clang", self.clang, 22, self.llvm22, 22, manifest
        )
        arguments[arguments.index("--abi") + 1] = "x86"
        self._run(arguments, success=True)
        rows = manifest.read_text(encoding="ascii").splitlines()
        self.assertEqual(rows[3], "abi=x86")
        metadata = Path(os.fspath(manifest) + ".metadata.json")
        self._run(
            [
                os.fspath(VALIDATOR),
                "verify",
                "--manifest",
                os.fspath(manifest),
                "--metadata",
                os.fspath(metadata),
            ],
            success=True,
        )

    def test_atomic_pair_refuses_stale_sidecar_without_publishing_manifest(self) -> None:
        profile = self.profiles / "atomic.profdata"
        profile.write_text("IR\n", encoding="utf-8")
        manifest = self.root / "atomic.manifest"
        metadata = Path(os.fspath(manifest) + ".metadata.json")
        metadata.write_text("stale\n", encoding="utf-8")
        arguments = self._common(
            "clang-ir",
            profile,
            "clang",
            self.clang,
            22,
            self.llvm22,
            22,
            manifest,
            metadata,
        )
        self._run(arguments, success=False)
        self.assertFalse(manifest.exists())
        self.assertEqual(metadata.read_text(encoding="utf-8"), "stale\n")
        self.assertEqual(list(self.root.glob(".*.partial.*")), [])

    def test_rust_binds_bundled_llvm_major(self) -> None:
        profile = self.profiles / "rust.profdata"
        profile.write_text("IR\n", encoding="utf-8")
        manifest = self.root / "rust.manifest"
        arguments = self._common(
            "rust", profile, "rust", self.rustc, 1, self.llvm21, 21, manifest
        ) + ["--rust-llvm-major", "21"]
        self._run(arguments, success=True)
        self._assert_manifest(manifest, "rust", "rust", profile, sha256(profile))

        rejected = self.root / "rust-wrong.manifest"
        wrong = self._common(
            "rust", profile, "rust", self.rustc, 1, self.llvm22, 22, rejected
        ) + ["--rust-llvm-major", "21"]
        self._run(wrong, success=False)
        self.assertFalse(rejected.exists())

    def _sample_metadata(self, profile: Path, unknown: bool = False) -> Path:
        version_stdout = "LLVM version 22.1.8\n"
        validation_stdout = "sample validation\n"
        profiled_binary = self.profiles / "sample-input"
        profiled_binary.write_text("profiled binary\n", encoding="utf-8")
        perf_data = self.profiles / "perf.data"
        perf_data.write_text("perf data\n", encoding="utf-8")

        def recorded_tool(path: Path, version: str) -> dict[str, str]:
            return {
                "realpath": os.fspath(path),
                "sha256": sha256(path),
                "version_stderr": "",
                "version_stdout": version,
            }

        def observation(path: Path) -> dict[str, int | str]:
            path_stat = path.stat()
            return {
                "ctime_ns": path_stat.st_ctime_ns,
                "device": path_stat.st_dev,
                "gid": path_stat.st_gid,
                "inode": path_stat.st_ino,
                "link_count": path_stat.st_nlink,
                "mode": path_stat.st_mode,
                "mtime_ns": path_stat.st_mtime_ns,
                "sha256": sha256(path),
                "size": path_stat.st_size,
                "uid": path_stat.st_uid,
            }

        metadata: dict[str, object] = {
            "compiler": {"family": "clang", "major": 22},
            "input_identity": {
                "build_id": "abcdef1234567890",
                "text_sha256": hashlib.sha256(b"TEXT\n").hexdigest(),
            },
            "package": {
                "abi": "amd64",
                "cpv": "dev-util/example-1.0",
                "fingerprint": FINGERPRINT,
            },
            "profile_family": "clang-sample",
            "profile_format": "llvm-sample",
            "profile_path": os.fspath(profile),
            "profile_sha256": sha256(profile),
            "profile_size": profile.stat().st_size,
            "reproducibility": {
                "optimization_generation_id": "generation-20260713-a",
                "production_date": "2026-07-13",
                "production_host": "gentoo-fixture",
                "source_identity_sha256": "e" * 64,
                "workload_revision": "workloads-sha256-a1",
            },
            "schema_version": 2,
            "source": {
                "kind": "llvm-profgen",
                "binary_path": os.fspath(profiled_binary),
                "binary_sha256": sha256(profiled_binary),
                "binary_observation": observation(profiled_binary),
                "debug_binary_path": None,
                "debug_binary_sha256": None,
                "debug_binary_observation": None,
                "perf_data_path": os.fspath(perf_data),
                "perf_data_sha256": sha256(perf_data),
                "perf_data_observation": observation(perf_data),
                "producer": recorded_tool(self.llvm22, version_stdout),
                "readelf": recorded_tool(self.sample_readelf, version_stdout),
                "objcopy": recorded_tool(self.sample_objcopy, version_stdout),
                "command_arguments": [
                    f"--binary={profiled_binary}",
                    f"--perfdata={perf_data}",
                    "--format=extbinary",
                    "--show-detailed-warning",
                    f"--output={profile}.partial",
                ],
                "command_output_sha256": "c" * 64,
            },
            "validation": {
                "command_arguments": ["show", "--sample", os.fspath(profile)],
                "output_sha256": hashlib.sha256(
                    (validation_stdout + "\0").encode("utf-8")
                ).hexdigest(),
            },
            "validator": {
                "realpath": os.fspath(self.llvm22),
                "sha256": sha256(self.llvm22),
                "version_stderr": "",
                "version_stdout": version_stdout,
            },
        }
        if unknown:
            metadata["unreviewed"] = True
        path = self.profiles / ("sample-unknown.json" if unknown else "sample.json")
        path.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def test_sample_profile_requires_exact_metadata_and_nonzero_samples(self) -> None:
        profile = self.profiles / "sample.prof"
        profile.write_text("SAMPLE\n", encoding="utf-8")
        metadata = self._sample_metadata(profile)
        manifest = self.root / "sample.manifest"
        arguments = self._common(
            "clang-sample", profile, "clang", self.clang, 22, self.llvm22, 22, manifest
        ) + ["--sample-metadata", os.fspath(metadata)]
        self._run(arguments, success=True)
        self._assert_manifest(manifest, "clang-sample", "clang", profile, sha256(profile))
        sidecar_path = Path(os.fspath(manifest) + ".metadata.json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sample_metadata = json.loads(metadata.read_text(encoding="utf-8"))
        self.assertEqual(
            sidecar["backend_proof"]["reproducibility"],
            sample_metadata["reproducibility"],
        )

        unknown_manifest = self.root / "sample-unknown.manifest"
        unknown_arguments = self._common(
            "clang-sample",
            profile,
            "clang",
            self.clang,
            22,
            self.llvm22,
            22,
            unknown_manifest,
        ) + ["--sample-metadata", os.fspath(self._sample_metadata(profile, unknown=True))]
        completed = self._run(unknown_arguments, success=False)
        self.assertIn("unknown unreviewed", completed.stderr)
        self.assertFalse(unknown_manifest.exists())

        external_metadata = self._sample_metadata(profile)
        external_data = json.loads(external_metadata.read_text(encoding="utf-8"))
        external_data["source"] = {"kind": "external"}
        external_metadata = self.profiles / "sample-external.json"
        external_metadata.write_text(
            json.dumps(external_data, sort_keys=True) + "\n", encoding="utf-8"
        )
        external_manifest = self.root / "sample-external.manifest"
        external_arguments = self._common(
            "clang-sample",
            profile,
            "clang",
            self.clang,
            22,
            self.llvm22,
            22,
            external_manifest,
        ) + ["--sample-metadata", os.fspath(external_metadata)]
        completed = self._run(external_arguments, success=False)
        self.assertIn("require llvm-profgen", completed.stderr)
        self.assertFalse(external_manifest.exists())

    def test_sample_sidecar_reproducibility_tamper_is_rejected(self) -> None:
        profile = self.profiles / "sample.prof"
        profile.write_text("SAMPLE\n", encoding="utf-8")
        sample_metadata = self._sample_metadata(profile)
        manifest = self.root / "sample-sidecar-tamper.manifest"
        arguments = self._common(
            "clang-sample",
            profile,
            "clang",
            self.clang,
            22,
            self.llvm22,
            22,
            manifest,
        ) + ["--sample-metadata", os.fspath(sample_metadata)]
        self._run(arguments, success=True)
        sidecar_path = Path(os.fspath(manifest) + ".metadata.json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["backend_proof"]["reproducibility"]["production_host"] = (
            "tampered-host"
        )
        sidecar_path.write_text(
            json.dumps(sidecar, sort_keys=True) + "\n", encoding="utf-8"
        )
        completed = self._run(
            [
                os.fspath(VALIDATOR),
                "verify",
                "--manifest",
                os.fspath(manifest),
                "--metadata",
                os.fspath(sidecar_path),
            ],
            success=False,
        )
        self.assertIn("no longer matches", completed.stderr)

    def test_sample_metadata_v2_rejects_downgrade_missing_unknown_and_tampering(self) -> None:
        profile = self.profiles / "sample.prof"
        profile.write_text("SAMPLE\n", encoding="utf-8")

        mutations: dict[str, Callable[[dict[str, Any]], object]] = {
            "schema-v1": lambda data: data.__setitem__("schema_version", 1),
            "missing-reproducibility": lambda data: data.pop("reproducibility"),
            "unknown-reproducibility": lambda data: data["reproducibility"].__setitem__(
                "unreviewed", "value"
            ),
            "tampered-generation": lambda data: data["reproducibility"].__setitem__(
                "optimization_generation_id", "../wrong-generation"
            ),
            "tampered-workload": lambda data: data["reproducibility"].__setitem__(
                "workload_revision", "wrong/workload"
            ),
            "tampered-source": lambda data: data["reproducibility"].__setitem__(
                "source_identity_sha256", "not-a-sha256"
            ),
            "tampered-host": lambda data: data["reproducibility"].__setitem__(
                "production_host", "wrong host"
            ),
            "tampered-date": lambda data: data["reproducibility"].__setitem__(
                "production_date", "2026-02-30"
            ),
            "missing-observation": lambda data: data["source"].pop(
                "binary_observation"
            ),
            "unknown-observation": lambda data: data["source"][
                "binary_observation"
            ].__setitem__("unreviewed", 1),
            "tampered-observation": lambda data: data["source"][
                "binary_observation"
            ].__setitem__("inode", 0),
            "tampered-build-id": lambda data: data["input_identity"].__setitem__(
                "build_id", "1234567890abcdef"
            ),
            "tampered-text-sha256": lambda data: data[
                "input_identity"
            ].__setitem__("text_sha256", "f" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                metadata_path = self._sample_metadata(profile)
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                mutate(data)
                metadata_path = self.profiles / f"sample-{name}.json"
                metadata_path.write_text(
                    json.dumps(data, sort_keys=True) + "\n", encoding="utf-8"
                )
                manifest = self.root / f"sample-{name}.manifest"
                arguments = self._common(
                    "clang-sample",
                    profile,
                    "clang",
                    self.clang,
                    22,
                    self.llvm22,
                    22,
                    manifest,
                ) + ["--sample-metadata", os.fspath(metadata_path)]
                self._run(arguments, success=False)
                self.assertFalse(manifest.exists())

    def test_sample_observation_rejects_restored_content(self) -> None:
        profile = self.profiles / "sample.prof"
        profile.write_text("SAMPLE\n", encoding="utf-8")
        metadata_path = self._sample_metadata(profile)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        binary = Path(metadata["source"]["binary_path"])
        original = binary.read_bytes()
        original_sha256 = sha256(binary)
        binary.unlink()
        binary.write_bytes(b"temporary different content\n")
        binary.write_bytes(original)
        self.assertEqual(sha256(binary), original_sha256)

        manifest = self.root / "sample-restored-content.manifest"
        arguments = self._common(
            "clang-sample",
            profile,
            "clang",
            self.clang,
            22,
            self.llvm22,
            22,
            manifest,
        ) + ["--sample-metadata", os.fspath(metadata_path)]
        completed = self._run(arguments, success=False)
        self.assertIn("exact recorded observation", completed.stderr)
        self.assertFalse(manifest.exists())

    def test_gcc_directory_is_hashed_like_the_dispatcher(self) -> None:
        profile = self.profiles / "gcc"
        (profile / "nested").mkdir(parents=True)
        (profile / "one.gcda").write_bytes(b"one-counts")
        (profile / "nested/two.gcda").write_bytes(b"two-counts")
        manifest = self.root / "gcc.manifest"
        arguments = self._common(
            "gcc", profile, "gcc", self.gcc, 15, self.gcov, 15, manifest
        )
        self._run(arguments, success=True)
        digest = hashlib.sha256()
        for relative in ("nested/two.gcda", "one.gcda"):
            digest.update(f"./{relative}".encode("utf-8") + b"\0")
            digest.update(sha256(profile / relative).encode("ascii") + b"\n")
        self._assert_manifest(manifest, "gcc", "gcc", profile, digest.hexdigest())

    def _go_arguments(self, profile: Path, manifest: Path) -> list[str]:
        binary = self.root / "go-target"
        if not binary.exists():
            binary.write_text("exact Go binary\n", encoding="utf-8")
            binary.chmod(0o755)
        return self._common(
            "go", profile, "go", self.go, 1, self.go, 1, manifest
        ) + [
            "--go-binary",
            os.fspath(binary),
            "--go-binary-sha256",
            sha256(binary),
            "--go-build-id",
            "go-build-id-exact",
            "--go-target-package",
            "example.com/target",
            "--go-target-symbol",
            "example.com/target.Hot",
            "--readelf",
            os.fspath(self.readelf),
            "--readelf-sha256",
            sha256(self.readelf),
        ]

    def test_go_proves_mapping_build_ids_function_metadata_and_target_symbols(self) -> None:
        profile = self.profiles / "cpu.pprof"
        profile.write_text("GO-GOOD\n", encoding="utf-8")
        manifest = self.root / "go.manifest"
        self._run(self._go_arguments(profile, manifest), success=True)
        self._assert_manifest(manifest, "go", "go", profile, sha256(profile))
        metadata = json.loads(
            Path(os.fspath(manifest) + ".metadata.json").read_text(encoding="utf-8")
        )
        proof = metadata["backend_proof"]
        self.assertEqual(proof["go_build_id"], "go-build-id-exact")
        self.assertEqual(
            proof["mapping_identity"],
            {"type": "gnu-build-id", "value": "abcdef1234567890"},
        )
        metadata["unreviewed"] = True
        metadata_path = Path(os.fspath(manifest) + ".metadata.json")
        metadata_path.write_text(
            json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8"
        )
        completed = self._run(
            [
                os.fspath(VALIDATOR),
                "verify",
                "--manifest",
                os.fspath(manifest),
                "--metadata",
                os.fspath(metadata_path),
            ],
            success=False,
        )
        self.assertIn("unknown unreviewed", completed.stderr)

    def test_go_native_build_id_is_valid_without_a_gnu_build_id(self) -> None:
        profile = self.profiles / "native.pgo"
        profile.write_text("GO-NATIVE\n", encoding="utf-8")
        manifest = self.root / "go-native.manifest"
        arguments = self._go_arguments(profile, manifest)
        readelf_index = arguments.index("--readelf") + 1
        hash_index = arguments.index("--readelf-sha256") + 1
        arguments[readelf_index] = os.fspath(self.readelf_no_id)
        arguments[hash_index] = sha256(self.readelf_no_id)
        self._run(arguments, success=True)
        self._assert_manifest(manifest, "go", "go", profile, sha256(profile))
        metadata = json.loads(
            Path(os.fspath(manifest) + ".metadata.json").read_text(encoding="utf-8")
        )
        self.assertIsNone(metadata["backend_proof"]["gnu_build_id"])
        self.assertEqual(
            metadata["backend_proof"]["mapping_identity"],
            {"type": "go-build-id", "value": "go-build-id-exact"},
        )

    def test_structurally_valid_unrelated_go_profile_is_rejected(self) -> None:
        profile = self.profiles / "unrelated.pprof"
        profile.write_text("GO-UNRELATED\n", encoding="utf-8")
        manifest = self.root / "go-unrelated.manifest"
        completed = self._run(self._go_arguments(profile, manifest), success=False)
        self.assertIn("declared target package", completed.stderr)
        self.assertFalse(manifest.exists())

    def test_symlinked_profile_is_rejected(self) -> None:
        real_profile = self.profiles / "real.profdata"
        real_profile.write_text("IR\n", encoding="utf-8")
        profile = self.profiles / "link.profdata"
        profile.symlink_to(real_profile)
        manifest = self.root / "symlink.manifest"
        arguments = self._common(
            "clang-ir", profile, "clang", self.clang, 22, self.llvm22, 22, manifest
        )
        self._run(arguments, success=False)
        self.assertFalse(manifest.exists())


if __name__ == "__main__":
    unittest.main()

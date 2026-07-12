#!/usr/bin/env python3
"""Hermetic tests for package fingerprints and sample-profile identity."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = (
    REPOSITORY_ROOT / "scripts" / "optimization" / "pgo" / "profile-identity.py"
)
SPEC = importlib.util.spec_from_file_location("optimization_profile_identity", TOOL_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load profile identity tool: {TOOL_PATH}")
TOOL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TOOL
SPEC.loader.exec_module(TOOL)


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.compiler = self.write_executable(
            "clang",
            "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then\n"
            "  printf '%s\\n' 'clang version 22.1.8 (Gentoo fixture)'\n"
            "  exit 0\n"
            "fi\n"
            "exit 64\n",
        )
        self.profdata = self.write_executable(
            "llvm-profdata",
            "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then\n"
            "  printf '%s\\n' 'LLVM version 22.1.8'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = show ] && [ \"$2\" = --sample ] && "
            "[ -f \"$3\" ] && grep -q '^SAMPLE$' \"$3\"; then\n"
            "  printf '%s\\n' 'Function: fixture, Total samples: 100'\n"
            "  exit 0\n"
            "fi\n"
            "printf '%s\\n' 'unsupported or non-sample profile' >&2\n"
            "exit 65\n",
        )
        self.profgen = self.write_executable(
            "llvm-profgen",
            "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then\n"
            "  printf '%s\\n' 'LLVM version 22.1.8'\n"
            "  exit 0\n"
            "fi\n"
            "output= perfdata=\n"
            "for argument do\n"
            "  case $argument in\n"
            "    --output=*) output=${argument#--output=} ;;\n"
            "    --perfdata=*) perfdata=${argument#--perfdata=} ;;\n"
            "  esac\n"
            "done\n"
            "case $(cat \"$perfdata\") in\n"
            "  FAIL_AFTER_OUTPUT) printf '%s\\n' SAMPLE >\"$output\"; exit 23 ;;\n"
            "  TIMEOUT) trap '' TERM; (trap '' TERM; sleep 30) & wait ;;\n"
            "  NO_OUTPUT) exit 0 ;;\n"
            "esac\n"
            "printf '%s\\n' SAMPLE >\"$output\"\n"
            "printf '%s\\n' 'conversion complete'\n",
        )
        self.readelf = self.write_executable(
            "llvm-readelf",
            "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then\n"
            "  printf '%s\\n' 'LLVM version 22.1.8'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = -n ]; then\n"
            f"  printf '%s\\n' '    Build ID: {'c' * 40}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 66\n",
        )
        self.objcopy = self.write_executable(
            "llvm-objcopy",
            "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then\n"
            "  printf '%s\\n' 'LLVM version 22.1.8'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = --dump-section ]; then\n"
            "  output=${2#.text=}\n"
            "  printf '%s\\n' TEXT >\"$output\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 67\n",
        )
        self.binary = self.root / "profiled-binary"
        self.binary.write_bytes(b"ELF fixture binary\n")
        self.debug_binary = self.root / "profiled-binary.debug"
        self.debug_binary.write_bytes(b"DWARF fixture data\n")
        self.perf_data = self.root / "perf.data"
        self.perf_data.write_text("SUCCESS\n", encoding="ascii")

    def write_executable(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "category": "dev-util",
            "pf": "example-1.2.3-r1",
            "slot": "0",
            "subslot": "0",
            "repository": "gentoo",
            "ebuild_sha256": "a" * 64,
            "eapi": "8",
            "chost": "x86_64-pc-linux-gnu",
            "abi": "amd64",
            "compiler": {
                "path": os.fspath(self.compiler),
                "family": "clang",
                "major": 22,
                "profile_format": "llvm-instr-v22",
            },
            "use_flags": ["abi_x86_64", "pgo", "split-usr"],
            "cflags": "-O3 -pipe",
            "cxxflags": "-O3 -pipe -stdlib=libc++",
            "ldflags": "-fuse-ld=lld -Wl,--build-id=sha1",
            "rustflags": "",
            "goflags": "",
            "features": ["buildpkg", "sandbox", "usersandbox"],
            "package_env_files": ["clang-22.conf", "pgo/clang-ir-use.conf"],
            "extra_econf": "--enable-example",
            "extra_emeson": "",
            "extra_ecmake": "-DEXAMPLE=ON",
            "kernel_module": False,
            "kernel_release": None,
        }

    def write_manifest(self, value: dict[str, Any], name: str = "input.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = TOOL.main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def fingerprint(
        self, manifest: dict[str, Any], *, outputs: bool = False
    ) -> tuple[int, str, str]:
        input_path = self.write_manifest(manifest)
        arguments = ["fingerprint", "--input", os.fspath(input_path)]
        if outputs:
            arguments += [
                "--metadata-out",
                os.fspath(self.root / "metadata.json"),
                "--key-out",
                os.fspath(self.root / "fingerprint.env"),
            ]
        return self.invoke(*arguments)

    def sample_arguments(self, profile: Path) -> list[str]:
        return [
            "--profile",
            os.fspath(profile),
            "--llvm-profdata",
            os.fspath(self.profdata),
            "--cpv",
            "dev-util/example-1.2.3-r1",
            "--fingerprint",
            "b" * 64,
            "--abi",
            "amd64",
            "--clang-major",
            "22",
            "--build-id",
            "c" * 40,
            "--text-sha256",
            "d" * 64,
        ]

    def conversion_arguments(
        self, profile: Path, metadata: Path, *, include_debug: bool = False
    ) -> list[str]:
        arguments = [
            "--llvm-profgen", os.fspath(self.profgen),
            "--llvm-profdata", os.fspath(self.profdata),
            "--readelf", os.fspath(self.readelf),
            "--objcopy", os.fspath(self.objcopy),
            "--binary", os.fspath(self.binary),
            "--perf-data", os.fspath(self.perf_data),
            "--profile-out", os.fspath(profile),
            "--metadata-out", os.fspath(metadata),
            "--cpv", "dev-util/example-1.2.3-r1",
            "--fingerprint", "b" * 64,
            "--abi", "amd64",
            "--clang-major", "22",
        ]
        if include_debug:
            arguments.extend(["--debug-binary", os.fspath(self.debug_binary)])
        return arguments


class FingerprintTest(unittest.TestCase):
    def test_stable_canonical_fingerprint_and_atomic_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            original = fixture.manifest()
            status, stdout, stderr = fixture.fingerprint(original, outputs=True)
            self.assertEqual(status, 0, stderr)
            fingerprint = stdout.strip()
            self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
            self.assertEqual(
                (fixture.root / "fingerprint.env").read_text(encoding="ascii"),
                f"fingerprint={fingerprint}\n",
            )
            metadata = json.loads(
                (fixture.root / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["fingerprint"], fingerprint)
            self.assertEqual(metadata["fingerprint_id"], f"sha256:{fingerprint}")
            self.assertNotIn("timestamp", json.dumps(metadata).lower())
            self.assertEqual(
                metadata["canonical_identity"]["compiler"]["realpath"],
                os.fspath(fixture.compiler.resolve()),
            )

            reordered = copy.deepcopy(original)
            reordered["use_flags"].reverse()
            reordered["features"].reverse()
            second_status, second_stdout, second_stderr = fixture.fingerprint(reordered)
            self.assertEqual(second_status, 0, second_stderr)
            self.assertEqual(second_stdout.strip(), fingerprint)

            compiler_alias = fixture.root / "active-clang"
            compiler_alias.symlink_to(fixture.compiler)
            aliased = copy.deepcopy(original)
            aliased["compiler"]["path"] = os.fspath(compiler_alias)
            alias_status, alias_stdout, alias_stderr = fixture.fingerprint(aliased)
            self.assertEqual(alias_status, 0, alias_stderr)
            self.assertEqual(alias_stdout.strip(), fingerprint)
            self.assertFalse(list(fixture.root.glob("*.partial")))

    def test_every_build_axis_and_ordered_environment_stack_affect_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            original = fixture.manifest()
            status, stdout, stderr = fixture.fingerprint(original)
            self.assertEqual(status, 0, stderr)
            baseline = stdout.strip()
            mutations: dict[str, object] = {
                "abi": "x86",
                "slot": "1",
                "subslot": "1",
                "repository": "local",
                "ebuild_sha256": "e" * 64,
                "eapi": "9",
                "chost": "i686-pc-linux-gnu",
                "cflags": "-O2 -pipe",
                "cxxflags": "-O2 -pipe -stdlib=libc++",
                "ldflags": "-fuse-ld=lld",
                "rustflags": "-Copt-level=3",
                "goflags": "-trimpath",
                "extra_econf": "--disable-example",
                "extra_emeson": "-Dexample=true",
                "extra_ecmake": "",
            }
            for field, replacement in mutations.items():
                with self.subTest(field=field):
                    changed = copy.deepcopy(original)
                    changed[field] = replacement
                    result, changed_stdout, changed_stderr = fixture.fingerprint(changed)
                    self.assertEqual(result, 0, changed_stderr)
                    self.assertNotEqual(changed_stdout.strip(), baseline)

            changed = copy.deepcopy(original)
            changed["package_env_files"].reverse()
            result, changed_stdout, changed_stderr = fixture.fingerprint(changed)
            self.assertEqual(result, 0, changed_stderr)
            self.assertNotEqual(changed_stdout.strip(), baseline)

    def test_compiler_binary_family_major_and_format_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            original = fixture.manifest()
            status, stdout, stderr = fixture.fingerprint(original)
            self.assertEqual(status, 0, stderr)
            baseline = stdout.strip()

            replacement = fixture.write_executable(
                "clang-rebuilt",
                fixture.compiler.read_text(encoding="utf-8") + "# different binary\n",
            )
            changed = copy.deepcopy(original)
            changed["compiler"]["path"] = os.fspath(replacement)
            result, changed_stdout, changed_stderr = fixture.fingerprint(changed)
            self.assertEqual(result, 0, changed_stderr)
            self.assertNotEqual(changed_stdout.strip(), baseline)

            for field, value in (("major", 21), ("family", "gcc"), ("profile_format", "gcc-gcov-v17")):
                with self.subTest(field=field):
                    invalid = copy.deepcopy(original)
                    invalid["compiler"][field] = value
                    result, _output, diagnostic = fixture.fingerprint(invalid)
                    self.assertEqual(result, 1)
                    self.assertIn("ERROR:", diagnostic)

    def test_schema_is_fail_closed_and_kernel_release_is_conditional(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for mutation in ("missing", "unknown", "duplicate", "traversal"):
                with self.subTest(mutation=mutation):
                    invalid = fixture.manifest()
                    if mutation == "missing":
                        del invalid["features"]
                    elif mutation == "unknown":
                        invalid["timestamp"] = "volatile"
                    elif mutation == "duplicate":
                        invalid["use_flags"].append("pgo")
                    else:
                        invalid["package_env_files"] = ["../escape.conf"]
                    status, _stdout, stderr = fixture.fingerprint(invalid)
                    self.assertEqual(status, 1)
                    self.assertIn("ERROR:", stderr)

            module = fixture.manifest()
            module["kernel_module"] = True
            module["kernel_release"] = "7.1.2-cachyos2"
            status, _stdout, stderr = fixture.fingerprint(module)
            self.assertEqual(status, 0, stderr)
            module["kernel_release"] = None
            status, _stdout, _stderr = fixture.fingerprint(module)
            self.assertEqual(status, 1)

    def test_only_amd64_and_x86_abi_lanes_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            invalid = fixture.manifest()
            invalid["abi"] = "arm64"
            status, _stdout, stderr = fixture.fingerprint(invalid)
            self.assertEqual(status, 1)
            self.assertIn("amd64", stderr)

    def test_atomic_outputs_reject_relative_symlink_and_symlink_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            input_path = fixture.write_manifest(fixture.manifest())
            status, _stdout, stderr = fixture.invoke(
                "fingerprint", "--input", os.fspath(input_path),
                "--metadata-out", "relative-metadata.json",
            )
            self.assertEqual(status, 1)
            self.assertIn("absolute", stderr)

            preflight_metadata = fixture.root / "must-not-be-written.json"
            status, _stdout, stderr = fixture.invoke(
                "fingerprint", "--input", os.fspath(input_path),
                "--metadata-out", os.fspath(preflight_metadata),
                "--key-out", "relative-key.env",
            )
            self.assertEqual(status, 1)
            self.assertFalse(preflight_metadata.exists())

            same_output = fixture.root / "same-output"
            status, _stdout, stderr = fixture.invoke(
                "fingerprint", "--input", os.fspath(input_path),
                "--metadata-out", os.fspath(same_output),
                "--key-out", os.fspath(same_output),
            )
            self.assertEqual(status, 1)
            self.assertFalse(same_output.exists())

            dangling = fixture.root / "dangling.json"
            dangling.symlink_to(fixture.root / "does-not-exist")
            status, _stdout, stderr = fixture.invoke(
                "fingerprint", "--input", os.fspath(input_path),
                "--metadata-out", os.fspath(dangling),
            )
            self.assertEqual(status, 1)
            self.assertTrue(dangling.is_symlink())

            real_parent = fixture.root / "real-parent"
            real_parent.mkdir()
            linked_parent = fixture.root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            status, _stdout, stderr = fixture.invoke(
                "fingerprint", "--input", os.fspath(input_path),
                "--metadata-out", os.fspath(linked_parent / "metadata.json"),
            )
            self.assertEqual(status, 1)
            self.assertIn("symlink", stderr)
            self.assertFalse((real_parent / "metadata.json").exists())


class ProfileFamilyPathTest(unittest.TestCase):
    def test_families_have_nonoverlapping_exact_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            root = fixture.root / "profiles"
            common = ["profile-path", "--root", os.fspath(root)]
            cases = {
                "clang-ir": [
                    "--family", "clang-ir", "--compiler-major", "22",
                    "--generation", "generation-a", "--abi", "amd64",
                ],
                "rust": [
                    "--family", "rust", "--language-version", "1.88.0",
                    "--compiler-major", "20", "--generation", "generation-a",
                    "--abi", "amd64",
                ],
                "gcc": [
                    "--family", "gcc", "--compiler-major", "17", "--cpv",
                    "dev-util/example-1.2.3-r1", "--fingerprint", "a" * 64,
                    "--abi", "x86",
                ],
                "go": [
                    "--family", "go", "--language-version", "1.27.0",
                    "--cpv", "dev-util/example-1.2.3-r1", "--fingerprint", "a" * 64,
                    "--binary", "example",
                ],
                "clang-sample": [
                    "--family", "clang-sample", "--compiler-major", "22",
                    "--cpv", "dev-util/example-1.2.3-r1", "--fingerprint", "a" * 64,
                    "--build-id", "b" * 40,
                ],
                "kernel": [
                    "--family", "kernel", "--kernel-release", "7.1.2-cachyos2",
                    "--config-hash", "c" * 64,
                ],
            }
            outputs: set[str] = set()
            for family, arguments in cases.items():
                with self.subTest(family=family):
                    status, stdout, stderr = fixture.invoke(*(common + arguments))
                    self.assertEqual(status, 0, stderr)
                    output = stdout.strip()
                    self.assertTrue(output.startswith(os.fspath(root / family) + "/"))
                    outputs.add(output)
            self.assertEqual(len(outputs), len(cases))
            self.assertTrue(next(item for item in outputs if "/clang-sample/" in item).endswith("/sample.prof"))
            self.assertTrue(next(item for item in outputs if "/go/" in item).endswith("/default.pgo"))

    def test_missing_extraneous_and_unsafe_components_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            root = os.fspath(fixture.root / "profiles")
            cases = (
                ["profile-path", "--root", root, "--family", "clang-ir", "--compiler-major", "22", "--abi", "amd64"],
                ["profile-path", "--root", root, "--family", "clang-ir", "--generation", "g", "--abi", "amd64"],
                ["profile-path", "--root", root, "--family", "rust", "--language-version", "1.88", "--generation", "g", "--abi", "amd64"],
                ["profile-path", "--root", root, "--family", "gcc", "--cpv", "dev-util/example-1.2.3", "--fingerprint", "a" * 64, "--abi", "amd64"],
                ["profile-path", "--root", root, "--family", "clang-ir", "--compiler-major", "22", "--generation", "../bad", "--abi", "amd64"],
                ["profile-path", "--root", root, "--family", "clang-ir", "--compiler-major", "22", "--generation", "g", "--abi", "x32"],
                ["profile-path", "--root", root, "--family", "kernel", "--kernel-release", "7.1", "--config-hash", "a" * 64, "--abi", "amd64"],
                ["profile-path", "--root", "/", "--family", "kernel", "--kernel-release", "7.1", "--config-hash", "a" * 64],
            )
            for arguments in cases:
                status, _stdout, stderr = fixture.invoke(*arguments)
                self.assertEqual(status, 1)
                self.assertIn("ERROR:", stderr)

            real_root = fixture.root / "real-profile-root"
            real_root.mkdir()
            linked_root = fixture.root / "linked-profile-root"
            linked_root.symlink_to(real_root, target_is_directory=True)
            status, _stdout, stderr = fixture.invoke(
                "profile-path", "--root", os.fspath(linked_root), "--family", "kernel",
                "--kernel-release", "7.1", "--config-hash", "a" * 64,
            )
            self.assertEqual(status, 1)
            self.assertIn("symlink", stderr)


class SampleProfileTest(unittest.TestCase):
    def test_sample_profile_record_and_exact_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            profile = fixture.root / "sample.prof"
            profile.write_text("SAMPLE\n", encoding="ascii")
            metadata_path = fixture.root / "sample-metadata.json"
            arguments = fixture.sample_arguments(profile)
            status, stdout, stderr = fixture.invoke(
                "sample-record", *arguments, "--metadata-out", os.fspath(metadata_path)
            )
            self.assertEqual(status, 0, stderr)
            self.assertRegex(stdout.strip(), r"^[0-9a-f]{64}$")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["profile_family"], "clang-sample")
            self.assertEqual(metadata["profile_format"], "llvm-sample")
            self.assertEqual(metadata["profile_path"], os.fspath(profile))
            self.assertEqual(metadata["input_identity"]["build_id"], "c" * 40)
            self.assertEqual(metadata["input_identity"]["text_sha256"], "d" * 64)
            self.assertEqual(metadata["validation"]["command_arguments"][1], "--sample")

            status, validate_stdout, validate_stderr = fixture.invoke(
                "sample-validate", *arguments, "--metadata", os.fspath(metadata_path)
            )
            self.assertEqual(status, 0, validate_stderr)
            self.assertEqual(validate_stdout, stdout)
            self.assertFalse(list(fixture.root.glob("*.partial")))

    def test_ir_profile_missing_profile_and_ambiguous_name_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for name, content in (
                ("sample.prof", "IR-INSTRUMENTATION\n"),
                ("merged.profdata", "SAMPLE\n"),
            ):
                with self.subTest(name=name):
                    profile = fixture.root / name
                    profile.write_text(content, encoding="ascii")
                    status, _stdout, stderr = fixture.invoke(
                        "sample-record",
                        *fixture.sample_arguments(profile),
                        "--metadata-out",
                        os.fspath(fixture.root / f"{name}.json"),
                    )
                    self.assertEqual(status, 1)
                    self.assertIn("ERROR:", stderr)
            missing = fixture.root / "missing" / "sample.prof"
            status, _stdout, stderr = fixture.invoke(
                "sample-record",
                *fixture.sample_arguments(missing),
                "--metadata-out",
                os.fspath(fixture.root / "missing.json"),
            )
            self.assertEqual(status, 1)
            self.assertIn("ERROR:", stderr)

    def test_sample_metadata_cannot_replace_the_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            profile = fixture.root / "sample.prof"
            profile.write_text("SAMPLE\n", encoding="ascii")
            original = profile.read_bytes()
            status, _stdout, stderr = fixture.invoke(
                "sample-record",
                *fixture.sample_arguments(profile),
                "--metadata-out",
                os.fspath(profile),
            )
            self.assertEqual(status, 1)
            self.assertIn("must not replace", stderr)
            self.assertEqual(profile.read_bytes(), original)

    def test_profile_content_identity_tool_and_metadata_mismatches_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            profile = fixture.root / "sample.prof"
            profile.write_text("SAMPLE\n", encoding="ascii")
            metadata_path = fixture.root / "sample-metadata.json"
            arguments = fixture.sample_arguments(profile)
            status, _stdout, stderr = fixture.invoke(
                "sample-record", *arguments, "--metadata-out", os.fspath(metadata_path)
            )
            self.assertEqual(status, 0, stderr)

            mismatches = (
                ("content", None),
                ("expected-build-id", None),
                ("unknown-metadata", None),
                ("validator-major", None),
            )
            for mismatch, _unused in mismatches:
                with self.subTest(mismatch=mismatch):
                    profile.write_text("SAMPLE\n", encoding="ascii")
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    metadata.pop("unexpected", None)
                    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
                    current_arguments = list(arguments)
                    if mismatch == "content":
                        profile.write_text("SAMPLE\nchanged\n", encoding="ascii")
                    elif mismatch == "expected-build-id":
                        index = current_arguments.index("--build-id") + 1
                        current_arguments[index] = "e" * 40
                    elif mismatch == "unknown-metadata":
                        metadata["unexpected"] = True
                        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
                    else:
                        index = current_arguments.index("--clang-major") + 1
                        current_arguments[index] = "21"
                    status, _stdout, diagnostic = fixture.invoke(
                        "sample-validate",
                        *current_arguments,
                        "--metadata",
                        os.fspath(metadata_path),
                    )
                    self.assertEqual(status, 1)
                    self.assertIn("ERROR:", diagnostic)


class SampleConversionTest(unittest.TestCase):
    def test_transactional_conversion_records_exact_inputs_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            output = fixture.root / "profiles" / "sample.prof"
            metadata_path = fixture.root / "profiles" / "sample-metadata.json"
            output.parent.mkdir()
            partial = output.with_name("sample.prof.partial")
            partial.write_text("STALE\n", encoding="ascii")
            status, stdout, stderr = fixture.invoke(
                "sample-convert",
                *fixture.conversion_arguments(
                    output, metadata_path, include_debug=True
                ),
            )
            self.assertEqual(status, 0, stderr)
            self.assertEqual(output.read_text(encoding="ascii"), "SAMPLE\n")
            self.assertFalse(partial.exists())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            source = metadata["source"]
            self.assertEqual(source["kind"], "llvm-profgen")
            self.assertEqual(source["binary_path"], os.fspath(fixture.binary))
            self.assertEqual(
                source["binary_sha256"],
                hashlib.sha256(fixture.binary.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                source["perf_data_sha256"],
                hashlib.sha256(fixture.perf_data.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                source["debug_binary_sha256"],
                hashlib.sha256(fixture.debug_binary.read_bytes()).hexdigest(),
            )
            self.assertIn(
                f"--debug-binary={fixture.debug_binary}", source["command_arguments"]
            )
            self.assertEqual(metadata["input_identity"]["build_id"], "c" * 40)
            expected_text_sha = hashlib.sha256(b"TEXT\n").hexdigest()
            self.assertEqual(
                metadata["input_identity"]["text_sha256"], expected_text_sha
            )
            self.assertEqual(metadata["profile_sha256"], stdout.strip())

            validation_arguments = fixture.sample_arguments(output)
            text_index = validation_arguments.index("--text-sha256") + 1
            validation_arguments[text_index] = expected_text_sha
            status, validate_stdout, validate_stderr = fixture.invoke(
                "sample-validate",
                *validation_arguments,
                "--metadata",
                os.fspath(metadata_path),
            )
            self.assertEqual(status, 0, validate_stderr)
            self.assertEqual(validate_stdout, stdout)

    def test_failure_timeout_and_missing_output_leave_no_transaction_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for mode in ("FAIL_AFTER_OUTPUT", "TIMEOUT", "NO_OUTPUT"):
                with self.subTest(mode=mode):
                    fixture.perf_data.write_text(mode + "\n", encoding="ascii")
                    case_root = fixture.root / mode.lower()
                    output = case_root / "sample.prof"
                    metadata_path = case_root / "sample-metadata.json"
                    arguments = fixture.conversion_arguments(output, metadata_path)
                    if mode == "TIMEOUT":
                        arguments.extend(
                            [
                                "--timeout-seconds", "0.15",
                                "--kill-after-seconds", "0.1",
                            ]
                        )
                    status, _stdout, stderr = fixture.invoke(
                        "sample-convert", *arguments
                    )
                    self.assertEqual(status, 1)
                    self.assertIn("ERROR:", stderr)
                    if mode == "TIMEOUT":
                        self.assertIn("timed out", stderr)
                    self.assertFalse(output.exists())
                    self.assertFalse(metadata_path.exists())
                    self.assertFalse(output.with_name("sample.prof.partial").exists())

    def test_ambiguous_destination_and_preexisting_final_are_never_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            ambiguous = fixture.root / "merged.profdata"
            metadata_path = fixture.root / "metadata.json"
            status, _stdout, stderr = fixture.invoke(
                "sample-convert",
                *fixture.conversion_arguments(ambiguous, metadata_path),
            )
            self.assertEqual(status, 1)
            self.assertIn("sample.prof", stderr)
            self.assertFalse(ambiguous.exists())

            final = fixture.root / "sample.prof"
            final.write_text("PREEXISTING\n", encoding="ascii")
            status, _stdout, stderr = fixture.invoke(
                "sample-convert",
                *fixture.conversion_arguments(final, metadata_path),
            )
            self.assertEqual(status, 1)
            self.assertIn("already exists", stderr)
            self.assertEqual(final.read_text(encoding="ascii"), "PREEXISTING\n")
            self.assertFalse(metadata_path.exists())


if __name__ == "__main__":
    unittest.main()

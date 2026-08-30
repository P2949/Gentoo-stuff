#!/usr/bin/env python3
"""Hermetic tests for package fingerprints and sample-profile identity."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXACT_CPV_CONTRACT_PATH = REPOSITORY_ROOT / "optimization/exact-cpv-contract.json"
TOOL_PATH = (
    REPOSITORY_ROOT / "scripts" / "optimization" / "pgo" / "profile-identity.py"
)
SPEC = importlib.util.spec_from_file_location("optimization_profile_identity", TOOL_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load profile identity tool: {TOOL_PATH}")
TOOL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TOOL
SPEC.loader.exec_module(TOOL)

PROFILE_LOCKS = sys.modules.get("profile_locks")
if PROFILE_LOCKS is None:
    raise RuntimeError("profile lock module was not loaded with the identity tool")


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
        self.rustc = self.write_executable(
            "rustc",
            "#!/bin/sh\n"
            "if [ \"$1 $2\" = '--print target-list' ]; then\n"
            "  printf '%s\\n' x86_64-unknown-linux-gnu i686-unknown-linux-gnu\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1 $2\" = '--version --verbose' ]; then\n"
            "  printf '%s\\n' 'rustc 1.88.0 (fixture 2026-01-01)' "
            "'host: x86_64-unknown-linux-gnu' 'LLVM version: 20.1.7'\n"
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
            "[ \"${HOME-}\" = /nonexistent ] || exit 72\n"
            "[ \"${LANG-}\" = C ] || exit 72\n"
            "[ \"${LANGUAGE-}\" = C ] || exit 72\n"
            "[ \"${LC_ALL-}\" = C ] || exit 72\n"
            "[ \"${PATH-}\" = /usr/bin:/bin ] || exit 72\n"
            "[ \"${TZ-}\" = UTC ] || exit 72\n"
            "[ -z \"${LD_PRELOAD-}${LD_LIBRARY_PATH-}${COMPILER_PATH-}\" ] || exit 72\n"
            "[ -z \"${GCC_EXEC_PREFIX-}${RUSTC_WRAPPER-}${PYTHONPATH-}\" ] || exit 72\n"
            "if [ \"$1\" = --version ]; then\n"
            "  printf '%s\\n' 'LLVM version 22.1.8'\n"
            "  exit 0\n"
            "fi\n"
            "output= perfdata= binary=\n"
            "for argument do\n"
            "  case $argument in\n"
            "    --output=*) output=${argument#--output=} ;;\n"
            "    --perfdata=*) perfdata=${argument#--perfdata=} ;;\n"
            "    --binary=*) binary=${argument#--binary=} ;;\n"
            "  esac\n"
            "done\n"
            "case $(cat \"$perfdata\") in\n"
            "  FAIL_AFTER_OUTPUT) printf '%s\\n' SAMPLE >\"$output\"; exit 23 ;;\n"
            "  TIMEOUT) trap '' TERM; (trap '' TERM; sleep 30) & wait ;;\n"
            "  NO_OUTPUT) exit 0 ;;\n"
            "  MUTATE_RESTORE) original=$(cat \"$perfdata\"); "
            "printf '%s\\n' CHANGED >\"$perfdata\"; "
            "printf '%s\\n' \"$original\" >\"$perfdata\" ;;\n"
            "  MUTATE_BINARY_RESTORE) original=$(cat \"$binary\"); "
            "printf '%s\\n' CHANGED >\"$binary\"; "
            "printf '%s\\n' \"$original\" >\"$binary\" ;;\n"
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
            "  [ \"$#\" -eq 4 ] || exit 68\n"
            "  output=${2#.text=}\n"
            "  printf '%s\\n' TEXT >\"$output\"\n"
            "  cp -- \"$3\" \"$4\"\n"
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
        self.generation = {
            "generation_id": "generation-20260713-a",
            "inventory_id": "inventory-20260713-a",
            "inventory_sha256": "9" * 64,
        }
        self.framework_lock = self.root / "framework.lock"
        self.project_lock = self.root / "project.lock"
        self.generation_lock = self.root / "generation.lock"
        self.framework_lock.write_bytes(b"")
        payload = PROFILE_LOCKS.canonical_generation_payload(self.generation)
        self.project_lock.write_bytes(payload)
        self.generation_lock.write_bytes(payload)
        for lock in (
            self.framework_lock,
            self.project_lock,
            self.generation_lock,
        ):
            lock.chmod(0o600)

    def write_executable(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 3,
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
            "rust_target_triple": None,
            "rustc_llvm_version": None,
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
            "--optimization-generation-id", "generation-20260713-a",
            "--inventory-id", "inventory-20260713-a",
            "--inventory-sha256", "9" * 64,
            "--workload-revision", "workloads-sha256-a1",
            "--source-identity-sha256", "e" * 64,
            "--production-host", "gentoo-fixture",
            "--production-date", "2026-07-13",
            *self.lock_arguments(),
        ]

    def lock_arguments(self) -> list[str]:
        return [
            "--test-mode",
            "--test-framework-lock", os.fspath(self.framework_lock),
            "--test-project-lock", os.fspath(self.project_lock),
            "--test-generation-lock", os.fspath(self.generation_lock),
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
            "--conversion-log-out",
            os.fspath(profile.parent / "llvm-profgen-conversion-log.json"),
            "--cpv", "dev-util/example-1.2.3-r1",
            "--fingerprint", "b" * 64,
            "--abi", "amd64",
            "--clang-major", "22",
            "--optimization-generation-id", "generation-20260713-a",
            "--inventory-id", "inventory-20260713-a",
            "--inventory-sha256", "9" * 64,
            "--workload-revision", "workloads-sha256-a1",
            "--source-identity-sha256", "e" * 64,
            "--production-host", "gentoo-fixture",
            "--production-date", "2026-07-13",
            *self.lock_arguments(),
        ]
        if include_debug:
            arguments.extend(["--debug-binary", os.fspath(self.debug_binary)])
        return arguments


class ProfileLockTests(unittest.TestCase):
    def test_production_lock_policy_resolves_exact_portage_group(self) -> None:
        entry = type("GroupEntry", (), {"gr_gid": 250})()
        with mock.patch.object(PROFILE_LOCKS.grp, "getgrnam", return_value=entry) as lookup:
            self.assertEqual(PROFILE_LOCKS.production_portage_gid(), 250)
        lookup.assert_called_once_with("portage")
        self.assertEqual(PROFILE_LOCKS.PRODUCTION_LOCK_MODE, 0o640)
        self.assertEqual(PROFILE_LOCKS.PRODUCTION_LOCK_DIRECTORY_MODE, 0o750)
        with mock.patch.object(PROFILE_LOCKS.grp, "getgrnam", side_effect=KeyError):
            with self.assertRaisesRegex(
                PROFILE_LOCKS.ProfileLockError, "required production group"
            ):
                PROFILE_LOCKS.production_portage_gid()

    def test_writer_reader_hierarchy_and_exact_payload_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            paths = (
                fixture.framework_lock,
                fixture.project_lock,
                fixture.generation_lock,
            )
            with PROFILE_LOCKS.profile_lock_hierarchy(
                exclusive=True,
                expected_generation=fixture.generation,
                expected_generation_id=None,
                timeout_seconds=1,
                test_mode=True,
                test_paths=paths,
            ) as observed:
                self.assertEqual(observed, fixture.generation)
                for lock in paths:
                    blocked = subprocess.run(
                        ["flock", "-n", os.fspath(lock), "-c", "true"],
                        check=False,
                    )
                    self.assertNotEqual(blocked.returncode, 0)
            with PROFILE_LOCKS.profile_lock_hierarchy(
                exclusive=False,
                expected_generation=None,
                expected_generation_id=fixture.generation["generation_id"],
                timeout_seconds=1,
                test_mode=True,
                test_paths=paths,
            ):
                for lock in paths:
                    shared = subprocess.run(
                        ["flock", "-n", "-s", os.fspath(lock), "-c", "true"],
                        check=False,
                    )
                    self.assertEqual(shared.returncode, 0)
            fixture.project_lock.write_text(
                json.dumps(fixture.generation, sort_keys=True), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                PROFILE_LOCKS.ProfileLockError, "canonical generation payload"
            ):
                with PROFILE_LOCKS.profile_lock_hierarchy(
                    exclusive=True,
                    expected_generation=fixture.generation,
                    expected_generation_id=None,
                    timeout_seconds=1,
                    test_mode=True,
                    test_paths=paths,
                ):
                    self.fail("noncanonical lock payload was accepted")


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

    def test_features_preserve_portage_last_token_wins_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            disabled = fixture.manifest()
            disabled["features"] = ["sandbox", "ccache", "-ccache"]
            enabled = fixture.manifest()
            enabled["features"] = ["sandbox", "-ccache", "ccache"]

            disabled_status, disabled_stdout, disabled_stderr = fixture.fingerprint(
                disabled
            )
            enabled_status, enabled_stdout, enabled_stderr = fixture.fingerprint(enabled)
            self.assertEqual(disabled_status, 0, disabled_stderr)
            self.assertEqual(enabled_status, 0, enabled_stderr)
            self.assertNotEqual(disabled_stdout.strip(), enabled_stdout.strip())

            repeated = copy.deepcopy(disabled)
            repeated["features"] = ["ccache", "sandbox", "ccache", "-ccache"]
            repeated_status, repeated_stdout, repeated_stderr = fixture.fingerprint(
                repeated
            )
            self.assertEqual(repeated_status, 0, repeated_stderr)
            self.assertEqual(repeated_stdout.strip(), disabled_stdout.strip())

            reordered_use = copy.deepcopy(disabled)
            reordered_use["use_flags"].reverse()
            use_status, use_stdout, use_stderr = fixture.fingerprint(reordered_use)
            self.assertEqual(use_status, 0, use_stderr)
            self.assertEqual(use_stdout.strip(), disabled_stdout.strip())

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

    def test_rust_target_and_bundled_llvm_are_exact_fingerprint_axes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            rust = fixture.manifest()
            rust["compiler"] = {
                "path": os.fspath(fixture.rustc),
                "family": "rustc",
                "major": 1,
                "profile_format": "rust-llvm-v20",
            }
            rust["rust_target_triple"] = "x86_64-unknown-linux-gnu"
            rust["rustc_llvm_version"] = "20.1.7"
            status, stdout, stderr = fixture.fingerprint(rust)
            self.assertEqual(status, 0, stderr)
            baseline = stdout.strip()

            alternate = copy.deepcopy(rust)
            alternate["rust_target_triple"] = "i686-unknown-linux-gnu"
            status, stdout, stderr = fixture.fingerprint(alternate)
            self.assertEqual(status, 0, stderr)
            self.assertNotEqual(stdout.strip(), baseline)

            for field, value in (
                ("rust_target_triple", "aarch64-unknown-linux-gnu"),
                ("rustc_llvm_version", "19.1.7"),
            ):
                invalid = copy.deepcopy(rust)
                invalid[field] = value
                status, _stdout, stderr = fixture.fingerprint(invalid)
                self.assertEqual(status, 1)
                self.assertIn("ERROR:", stderr)

            leaked = fixture.manifest()
            leaked["rust_target_triple"] = "x86_64-unknown-linux-gnu"
            status, _stdout, stderr = fixture.fingerprint(leaked)
            self.assertEqual(status, 1)
            self.assertIn("must be null", stderr)

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

            legacy = fixture.manifest()
            legacy["schema_version"] = 2
            status, _stdout, stderr = fixture.fingerprint(legacy)
            self.assertEqual(status, 1)
            self.assertIn("unsupported fingerprint schema_version", stderr)

    def test_repository_has_no_legacy_package_fingerprint_producer(self) -> None:
        legacy_schema = '"schema_version"' + ": 2"
        candidates = []
        for relative_root in ("optimization", "scripts", "tests"):
            for path in (REPOSITORY_ROOT / relative_root).rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".sh"}:
                    continue
                source = path.read_text(encoding="utf-8")
                if '"package_env_files"' in source and '"ebuild_sha256"' in source:
                    candidates.append(path)
        self.assertGreaterEqual(len(candidates), 3)
        for path in candidates:
            with self.subTest(path=path.relative_to(REPOSITORY_ROOT)):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn(legacy_schema, source)
                if path == TOOL_PATH:
                    self.assertIn("SCHEMA_VERSION = 3", source)
                else:
                    self.assertIn('"schema_version": 3', source)

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
                    "--compiler-major", "20", "--rustc-llvm-version", "20.1.7",
                    "--target-triple", "x86_64-unknown-linux-gnu",
                    "--generation", "generation-a",
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
        exact_cpv_contract = json.loads(
            EXACT_CPV_CONTRACT_PATH.read_text(encoding="utf-8")
        )
        for cpv in exact_cpv_contract["valid_cpvs"]:
            with self.subTest(cpv=cpv, expected="valid"):
                self.assertEqual(TOOL.require_cpv(cpv), cpv)
        for cpv in exact_cpv_contract["invalid_cpvs"]:
            with self.subTest(cpv=cpv, expected="invalid"):
                with self.assertRaisesRegex(
                    TOOL.IdentityError, "is not an exact Gentoo CPV"
                ):
                    TOOL.require_cpv(cpv)

        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            root = os.fspath(fixture.root / "profiles")
            cases = (
                ["profile-path", "--root", root, "--family", "clang-ir", "--compiler-major", "22", "--abi", "amd64"],
                ["profile-path", "--root", root, "--family", "clang-ir", "--generation", "g", "--abi", "amd64"],
                ["profile-path", "--root", root, "--family", "rust", "--language-version", "1.88", "--compiler-major", "20", "--rustc-llvm-version", "20.1.7", "--generation", "g", "--abi", "amd64"],
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


class DisabledExternalSampleRecorderTest(unittest.TestCase):
    def test_external_recording_is_disabled_even_for_valid_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            profile = fixture.root / "sample.prof"
            profile.write_text("SAMPLE\n", encoding="ascii")
            metadata_path = fixture.root / "sample-metadata.json"
            status, stdout, stderr = fixture.invoke("sample-record")
            self.assertEqual(status, 1)
            self.assertEqual(stdout, "")
            self.assertIn("permanently disabled", stderr)
            self.assertFalse(metadata_path.exists())
            self.assertFalse((fixture.root / "sample.manifest").exists())

    def test_disabled_recording_never_accepts_profile_content_or_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for name, content in (
                ("sample.prof", "IR-INSTRUMENTATION\n"),
                ("merged.profdata", "SAMPLE\n"),
            ):
                with self.subTest(name=name):
                    profile = fixture.root / name
                    profile.write_text(content, encoding="ascii")
                    status, _stdout, stderr = fixture.invoke("sample-record")
                    self.assertEqual(status, 1)
                    self.assertIn("ERROR:", stderr)
            status, _stdout, stderr = fixture.invoke("sample-record")
            self.assertEqual(status, 1)
            self.assertIn("ERROR:", stderr)

    def test_sample_metadata_cannot_replace_the_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            profile = fixture.root / "sample.prof"
            profile.write_text("SAMPLE\n", encoding="ascii")
            original = profile.read_bytes()
            status, _stdout, stderr = fixture.invoke("sample-record")
            self.assertEqual(status, 1)
            self.assertIn("permanently disabled", stderr)
            self.assertEqual(profile.read_bytes(), original)

    def test_disabled_recorder_never_publishes_metadata_or_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            profile = fixture.root / "sample.prof"
            profile.write_text("SAMPLE\n", encoding="ascii")
            metadata_path = fixture.root / "sample-metadata.json"
            status, _stdout, stderr = fixture.invoke("sample-record")
            self.assertEqual(status, 1)
            self.assertIn("permanently disabled", stderr)
            self.assertFalse(metadata_path.exists())
            self.assertFalse((fixture.root / "sample.manifest").exists())


class SampleConversionTest(unittest.TestCase):
    def test_profile_payload_is_explicitly_fsynced_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.prof.partial"
            path.write_text("SAMPLE\n", encoding="ascii")
            with mock.patch.object(TOOL.os, "fsync", wraps=os.fsync) as fsync:
                TOOL.fsync_regular_file(path, "sample profile")
            fsync.assert_called_once()

    def test_transactional_conversion_records_exact_inputs_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            output = fixture.root / "profiles" / "sample.prof"
            metadata_path = fixture.root / "profiles" / "sample-metadata.json"
            output.parent.mkdir()
            partial = output.with_name("sample.prof.partial")
            partial.write_text("STALE\n", encoding="ascii")
            conversion_log_partial = output.parent / (
                "llvm-profgen-conversion-log.json.partial"
            )
            conversion_log_partial.write_text("STALE LOG\n", encoding="ascii")
            binary_hash_before = hashlib.sha256(fixture.binary.read_bytes()).hexdigest()
            binary_stat_before = fixture.binary.stat()
            binary_xattrs_before = {
                name: os.getxattr(fixture.binary, name)
                for name in os.listxattr(fixture.binary)
            }
            status, stdout, stderr = fixture.invoke(
                "sample-convert",
                *fixture.conversion_arguments(
                    output, metadata_path, include_debug=True
                ),
            )
            self.assertEqual(status, 0, stderr)
            self.assertEqual(output.read_text(encoding="ascii"), "SAMPLE\n")
            self.assertFalse(partial.exists())
            self.assertFalse(conversion_log_partial.exists())
            self.assertEqual(
                hashlib.sha256(fixture.binary.read_bytes()).hexdigest(),
                binary_hash_before,
            )
            binary_stat_after = fixture.binary.stat()
            self.assertEqual(binary_stat_after.st_ino, binary_stat_before.st_ino)
            self.assertEqual(binary_stat_after.st_mode, binary_stat_before.st_mode)
            self.assertEqual(binary_stat_after.st_size, binary_stat_before.st_size)
            self.assertEqual(binary_stat_after.st_mtime_ns, binary_stat_before.st_mtime_ns)
            self.assertEqual(binary_stat_after.st_ctime_ns, binary_stat_before.st_ctime_ns)
            self.assertEqual(
                {
                    name: os.getxattr(fixture.binary, name)
                    for name in os.listxattr(fixture.binary)
                },
                binary_xattrs_before,
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], 4)
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
            self.assertEqual(
                source["binary_observation"]["sha256"],
                source["binary_sha256"],
            )
            self.assertEqual(
                source["perf_data_observation"]["sha256"],
                source["perf_data_sha256"],
            )
            conversion_log = output.parent / "llvm-profgen-conversion-log.json"
            self.assertEqual(source["conversion_log_path"], os.fspath(conversion_log))
            self.assertEqual(
                source["conversion_log_sha256"],
                hashlib.sha256(conversion_log.read_bytes()).hexdigest(),
            )
            self.assertEqual(output.stat().st_mode & 0o777, 0o640)
            self.assertEqual(metadata_path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(conversion_log.stat().st_mode & 0o777, 0o440)
            self.assertEqual(output.stat().st_gid, output.parent.stat().st_gid)
            self.assertEqual(metadata_path.stat().st_gid, output.parent.stat().st_gid)
            self.assertEqual(conversion_log.stat().st_gid, output.parent.stat().st_gid)
            conversion_record = json.loads(conversion_log.read_text(encoding="utf-8"))
            self.assertEqual(conversion_record["stdout"], "conversion complete\n")
            self.assertEqual(conversion_record["stderr"], "")
            self.assertEqual(conversion_record["exit_status"], 0)
            self.assertEqual(
                conversion_record["command_arguments"],
                source["command_arguments"],
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
            self.assertEqual(
                metadata["reproducibility"],
                {
                    "optimization_generation_id": "generation-20260713-a",
                    "inventory_id": "inventory-20260713-a",
                    "inventory_sha256": "9" * 64,
                    "production_date": "2026-07-13",
                    "production_host": "gentoo-fixture",
                    "source_identity_sha256": "e" * 64,
                    "workload_revision": "workloads-sha256-a1",
                },
            )
            self.assertFalse(metadata_path.with_suffix(".manifest").exists())

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
                    self.assertFalse(
                        output.parent.joinpath(
                            "llvm-profgen-conversion-log.json"
                        ).exists()
                    )
                    self.assertFalse(
                        output.parent.joinpath(
                            "llvm-profgen-conversion-log.json.partial"
                        ).exists()
                    )

    def test_reproducibility_metadata_is_required_and_validated_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            bad_output = fixture.root / "bad" / "sample.prof"
            bad_metadata = fixture.root / "bad" / "sample-metadata.json"
            bad_arguments = fixture.conversion_arguments(bad_output, bad_metadata)
            date_index = bad_arguments.index("--production-date") + 1
            bad_arguments[date_index] = "2026-02-30"
            status, _stdout, stderr = fixture.invoke(
                "sample-convert", *bad_arguments
            )
            self.assertEqual(status, 1)
            self.assertIn("valid calendar date", stderr)
            self.assertFalse(bad_output.exists())
            self.assertFalse(bad_metadata.exists())

            output = fixture.root / "good" / "sample.prof"
            metadata_path = fixture.root / "good" / "sample-metadata.json"
            status, _stdout, stderr = fixture.invoke(
                "sample-convert",
                *fixture.conversion_arguments(output, metadata_path),
            )
            self.assertEqual(status, 0, stderr)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["reproducibility"]["unexpected"] = "not-allowed"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            validation_arguments = fixture.sample_arguments(output)
            text_index = validation_arguments.index("--text-sha256") + 1
            validation_arguments[text_index] = hashlib.sha256(b"TEXT\n").hexdigest()
            status, _stdout, stderr = fixture.invoke(
                "sample-validate",
                *validation_arguments,
                "--metadata",
                os.fspath(metadata_path),
            )
            self.assertEqual(status, 1)
            self.assertIn("unknown unexpected", stderr)

    def test_input_change_during_profgen_fails_even_when_content_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for mode, changed_path, diagnostic in (
                ("MUTATE_RESTORE", fixture.perf_data, "perf data changed"),
                (
                    "MUTATE_BINARY_RESTORE",
                    fixture.binary,
                    "profiled binary changed",
                ),
            ):
                with self.subTest(mode=mode):
                    fixture.perf_data.write_text(mode + "\n", encoding="ascii")
                    output = fixture.root / mode / "sample.prof"
                    metadata_path = fixture.root / mode / "sample-metadata.json"
                    original_hash = hashlib.sha256(changed_path.read_bytes()).hexdigest()
                    status, _stdout, stderr = fixture.invoke(
                        "sample-convert",
                        *fixture.conversion_arguments(output, metadata_path),
                    )
                    self.assertEqual(status, 1)
                    self.assertIn(diagnostic, stderr)
                    self.assertEqual(
                        hashlib.sha256(changed_path.read_bytes()).hexdigest(),
                        original_hash,
                    )
                    self.assertFalse(output.exists())
                    self.assertFalse(metadata_path.exists())
                    self.assertFalse(
                        output.with_name("sample.prof.partial").exists()
                    )

    def test_conversion_log_is_exact_and_restored_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            output = fixture.root / "profiles" / "sample.prof"
            metadata_path = fixture.root / "profiles" / "sample-metadata.json"
            status, _stdout, stderr = fixture.invoke(
                "sample-convert", *fixture.conversion_arguments(output, metadata_path)
            )
            self.assertEqual(status, 0, stderr)
            conversion_log = output.parent / "llvm-profgen-conversion-log.json"
            original = conversion_log.read_bytes()
            original_hash = hashlib.sha256(original).hexdigest()
            original_stat = conversion_log.stat()
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            recorded_observation = metadata["source"]["conversion_log_observation"]
            replacement = conversion_log.with_name(
                f".{conversion_log.name}.restored-content"
            )
            replacement.write_bytes(b"temporary hostile replacement\n")
            replacement.write_bytes(original)
            replacement.chmod(original_stat.st_mode & 0o7777)
            prepared_stat = replacement.stat()
            self.assertNotEqual(
                (prepared_stat.st_dev, prepared_stat.st_ino),
                (
                    recorded_observation["device"],
                    recorded_observation["inode"],
                ),
            )
            os.replace(replacement, conversion_log)
            installed_stat = conversion_log.stat()
            self.assertEqual(
                (installed_stat.st_dev, installed_stat.st_ino),
                (prepared_stat.st_dev, prepared_stat.st_ino),
            )
            self.assertNotEqual(
                (installed_stat.st_dev, installed_stat.st_ino),
                (
                    recorded_observation["device"],
                    recorded_observation["inode"],
                ),
            )
            self.assertEqual(hashlib.sha256(conversion_log.read_bytes()).hexdigest(), original_hash)

            validation_arguments = fixture.sample_arguments(output)
            text_index = validation_arguments.index("--text-sha256") + 1
            validation_arguments[text_index] = hashlib.sha256(b"TEXT\n").hexdigest()
            status, _stdout, stderr = fixture.invoke(
                "sample-validate",
                *validation_arguments,
                "--metadata",
                os.fspath(metadata_path),
            )
            self.assertEqual(status, 1)
            self.assertIn("exact recorded observation", stderr)

    def test_converter_environment_is_allowlisted_and_tool_aliases_are_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            producer_alias = fixture.root / "llvm-profgen-alias"
            producer_alias.symlink_to(fixture.profgen)
            output = fixture.root / "profiles" / "sample.prof"
            metadata_path = fixture.root / "profiles" / "sample-metadata.json"
            arguments = fixture.conversion_arguments(output, metadata_path)
            arguments[arguments.index("--llvm-profgen") + 1] = os.fspath(producer_alias)
            hostile = {
                "LD_PRELOAD": "/must/not/load.so",
                "LD_LIBRARY_PATH": "/must/not/search",
                "COMPILER_PATH": "/must/not/search",
                "GCC_EXEC_PREFIX": "/must/not/search",
                "RUSTC_WRAPPER": "/must/not/run",
                "PYTHONPATH": "/must/not/import",
                "HOME": "/must/not/use",
                "TZ": "Hostile/Zone",
            }
            with mock.patch.dict(os.environ, hostile, clear=False):
                status, _stdout, stderr = fixture.invoke("sample-convert", *arguments)
            self.assertEqual(status, 0, stderr)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(
                metadata["source"]["producer"]["realpath"],
                os.fspath(fixture.profgen.resolve()),
            )

    def test_ambiguous_destination_and_preexisting_final_are_never_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            ambiguous = fixture.root / "merged.profdata"
            metadata_path = fixture.root / "sample-metadata.json"
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

            clean_root = fixture.root / "log-collision"
            log_output = clean_root / "sample.prof"
            log_metadata = clean_root / "sample-metadata.json"
            conversion_log = clean_root / "llvm-profgen-conversion-log.json"
            clean_root.mkdir()
            conversion_log.write_text("PREEXISTING\n", encoding="ascii")
            status, _stdout, stderr = fixture.invoke(
                "sample-convert",
                *fixture.conversion_arguments(log_output, log_metadata),
            )
            self.assertEqual(status, 1)
            self.assertIn("already exists", stderr)
            self.assertEqual(conversion_log.read_text(encoding="ascii"), "PREEXISTING\n")
            self.assertFalse(log_output.exists())

    def test_legacy_weak_sample_producer_is_unconditionally_disabled(self) -> None:
        legacy = REPOSITORY_ROOT / "scripts" / "pgo" / "make-sample-prof.sh"
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [os.fspath(legacy), "cat", "pkg", "/bin/true", "/tmp/perf.data"],
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
                text=True,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("permanently disabled", completed.stderr)
            self.assertIn("sample-convert", completed.stderr)
            self.assertNotIn("wrote", completed.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()

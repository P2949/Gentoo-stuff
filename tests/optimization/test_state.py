from __future__ import annotations

import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = REPOSITORY_ROOT / "scripts/optimization/lib/state.py"
SPEC = importlib.util.spec_from_file_location("optimization_state", STATE_PATH)
assert SPEC is not None and SPEC.loader is not None
STATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATE)


def reason(code: str) -> dict[str, object]:
    return {
        "reason_code": code,
        "evidence": [f"/var/lib/gentoo-optimization/reports/{code}.log"],
        "reviewed": True,
    }


def package_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "package",
        "cpv": "app-test/example-1.0",
        "cp": "app-test/example",
        "repository": "gentoo",
        "slot": "0",
        "subslot": "0",
        "abis": ["amd64"],
        "ebuild_sha256": "1" * 64,
        "use_flags": ["abi_x86_64", "pie"],
        "build_backend": "clang-ir",
        "compiler": {
            "family": "clang",
            "path": "/usr/bin/clang",
            "realpath": "/usr/lib/llvm/22/bin/clang",
            "version": "clang version 22.1.8",
            "profile_format": "llvm-ir-v22",
        },
        "fingerprint": f"sha256:{'2' * 64}",
        "pgo": {
            "eligibility": "eligible",
            "mode": "clang-ir",
            "generation_id": "generation-test",
            "profile_path": "/var/cache/gentoo-optimization/pgo/clang-ir/22/generation-test/amd64/merged.profdata",
            "profile_valid": True,
            "build_verified": True,
            "terminal_reason": None,
            "status": "optimized",
        },
        "bolt": {
            "candidate_count": 1,
            "optimized_count": 1,
            "excluded_count": 0,
            "terminal_reason": None,
            "status": "optimized",
        },
        "final_status": "optimized",
        "notes": [],
    }


def artifact_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "artifact",
        "owner_cpv": "app-test/example-1.0",
        "package_fingerprint": f"sha256:{'2' * 64}",
        "installed_path": "/usr/bin/example",
        "canonical_path": "/usr/bin/example",
        "elf_class": 64,
        "elf_type": "DYN",
        "machine": "Advanced Micro Devices X86-64",
        "abi": "amd64",
        "build_id": "ab12",
        "text_sha256": "3" * 64,
        "has_symbols": True,
        "has_text_relocations": True,
        "setuid": False,
        "setgid": False,
        "file_capabilities": [],
        "hardlink_paths": ["/usr/bin/example"],
        "symlink_paths": [],
        "bolt_eligibility": "eligible",
        "terminal_reason": None,
        "bolt_profile_samples": 42,
        "bolt_profile_stale_percent": 0.0,
        "bolt_output_path": "/var/cache/gentoo-optimization/bolt/outputs/example",
        "installed_has_bolt_note": True,
        "status": "optimized",
    }


class PackageStateTests(unittest.TestCase):
    def test_valid_optimized_package(self) -> None:
        self.assertEqual(STATE.validate_package(package_record())["final_status"], "optimized")

    def test_unknown_key_is_rejected(self) -> None:
        record = package_record()
        record["unreviewed"] = True
        with self.assertRaisesRegex(STATE.StateValidationError, "extra=.*unreviewed"):
            STATE.validate_package(record)

    def test_optimized_profile_requires_proof(self) -> None:
        record = package_record()
        record["pgo"]["profile_valid"] = False  # type: ignore[index]
        with self.assertRaisesRegex(STATE.StateValidationError, "requires generation"):
            STATE.validate_package(record)

    def test_terminal_package_requires_reviewed_evidence(self) -> None:
        record = package_record()
        record["build_backend"] = "not-applicable"
        record["compiler"] = None
        record["pgo"] = {
            "eligibility": "not-applicable",
            "mode": "not-applicable",
            "generation_id": None,
            "profile_path": None,
            "profile_valid": False,
            "build_verified": False,
            "terminal_reason": None,
            "status": "not-applicable",
        }
        record["bolt"] = {
            "candidate_count": 0,
            "optimized_count": 0,
            "excluded_count": 0,
            "terminal_reason": reason("not-machine-code"),
            "status": "not-applicable",
        }
        record["final_status"] = "not-applicable"
        with self.assertRaisesRegex(STATE.StateValidationError, "required for a terminal state"):
            STATE.validate_package(record)

    def test_arrays_must_be_canonical(self) -> None:
        record = package_record()
        record["use_flags"] = ["pie", "abi_x86_64"]
        with self.assertRaisesRegex(STATE.StateValidationError, "must be sorted"):
            STATE.validate_package(record)


class ArtifactStateTests(unittest.TestCase):
    def test_valid_optimized_artifact(self) -> None:
        self.assertEqual(STATE.validate_artifact(artifact_record())["status"], "optimized")

    def test_abi_and_elf_class_cannot_cross(self) -> None:
        record = artifact_record()
        record["elf_class"] = 32
        with self.assertRaisesRegex(STATE.StateValidationError, "amd64 ABI cannot"):
            STATE.validate_artifact(record)

    def test_identity_must_be_complete_for_optimized_artifact(self) -> None:
        record = artifact_record()
        record["build_id"] = None
        with self.assertRaisesRegex(STATE.StateValidationError, "requires identities"):
            STATE.validate_artifact(record)

    def test_installed_path_must_have_topology_record(self) -> None:
        record = artifact_record()
        record["installed_path"] = "/usr/bin/example-link"
        with self.assertRaisesRegex(STATE.StateValidationError, "must appear"):
            STATE.validate_artifact(record)

    def test_nonapplicable_artifact_requires_reason(self) -> None:
        record = artifact_record()
        record["bolt_eligibility"] = "not-applicable"
        record["status"] = "not-applicable"
        record["terminal_reason"] = None
        with self.assertRaisesRegex(STATE.StateValidationError, "required for a terminal state"):
            STATE.validate_artifact(record)


class PublicationTests(unittest.TestCase):
    def test_atomic_publish_is_canonical_and_private(self) -> None:
        record = package_record()
        with tempfile.TemporaryDirectory(prefix="optimization-state-test.") as temporary:
            output = Path(temporary) / "nested" / "package.json"
            digest = STATE.atomic_publish(STATE.validate_package(record), output)
            payload = output.read_bytes()
            self.assertEqual(digest, __import__("hashlib").sha256(payload).hexdigest())
            self.assertEqual(json.loads(payload), record)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertFalse(list(output.parent.glob("*.partial")))

    def test_output_symlink_is_rejected(self) -> None:
        record = package_record()
        with tempfile.TemporaryDirectory(prefix="optimization-state-test.") as temporary:
            root = Path(temporary)
            target = root / "target.json"
            output = root / "output.json"
            target.write_text("do not replace\n", encoding="utf-8")
            output.symlink_to(target)
            with self.assertRaisesRegex(STATE.StateValidationError, "must not be a symlink"):
                STATE.atomic_publish(STATE.validate_package(record), output)
            self.assertEqual(target.read_text(encoding="utf-8"), "do not replace\n")

    def test_parent_symlink_is_rejected(self) -> None:
        record = package_record()
        with tempfile.TemporaryDirectory(prefix="optimization-state-test.") as temporary:
            root = Path(temporary)
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(STATE.StateValidationError, "traverses symlink"):
                STATE.atomic_publish(STATE.validate_package(record), alias / "state.json")
            self.assertFalse((real / "state.json").exists())


class SchemaSyncTests(unittest.TestCase):
    def test_package_schema_keys_match_validator(self) -> None:
        schema = json.loads(
            (REPOSITORY_ROOT / "optimization/schema/package-state.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(set(schema["required"]), STATE.PACKAGE_KEYS)
        self.assertEqual(set(schema["properties"]), STATE.PACKAGE_KEYS)

    def test_artifact_schema_keys_match_validator(self) -> None:
        schema = json.loads(
            (REPOSITORY_ROOT / "optimization/schema/artifact-state.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(set(schema["required"]), STATE.ARTIFACT_KEYS)
        self.assertEqual(set(schema["properties"]), STATE.ARTIFACT_KEYS)


if __name__ == "__main__":
    unittest.main()

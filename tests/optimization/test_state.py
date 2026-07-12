#!/usr/bin/env python3
"""Tests for version-2 package/component and artifact state contracts."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any


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


def compiler(family: str) -> dict[str, str]:
    versions = {
        "clang": ("/usr/bin/clang", "/usr/lib/llvm/22/bin/clang", "clang 22.1.8", "llvm-ir-v22"),
        "gcc": ("/usr/bin/gcc", "/usr/x86_64-pc-linux-gnu/gcc-bin/17/gcc", "gcc 17.0.1", "gcc-gcov-v17"),
        "rust": ("/usr/bin/rustc", "/usr/bin/rustc", "rustc 1.88.0", "rust-llvm-ir-v20"),
    }
    path, realpath, version, profile_format = versions[family]
    return {
        "family": family,
        "path": path,
        "realpath": realpath,
        "sha256": ({"clang": "d", "gcc": "e", "rust": "f"}[family]) * 64,
        "version": version,
        "profile_format": profile_format,
    }


def optimized_pgo(mode: str, marker: str) -> dict[str, object]:
    return {
        "eligibility": "eligible",
        "mode": mode,
        "generation_id": "generation-test",
        "profile_path": f"/var/cache/gentoo-optimization/pgo/{mode}/{marker}.profdata",
        "profile_sha256": marker * 64,
        "profile_valid": True,
        "build_verified": True,
        "terminal_reason": None,
        "status": "optimized",
    }


def nonapplicable_pgo() -> dict[str, object]:
    return {
        "eligibility": "not-applicable",
        "mode": "not-applicable",
        "generation_id": None,
        "profile_path": None,
        "profile_sha256": None,
        "profile_valid": False,
        "build_verified": False,
        "terminal_reason": reason("not-machine-code"),
        "status": "not-applicable",
    }


def component(
    component_id: str,
    abi: str,
    backend: str,
    family: str | None,
    fingerprint_marker: str,
    *,
    component_kind: str = "native",
    target_triple: str | None = None,
    pgo: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "component_id": component_id,
        "component_kind": component_kind,
        "abi": abi,
        "target_triple": target_triple,
        "build_backend": backend,
        "compiler": None if family is None else compiler(family),
        "fingerprint": f"sha256:{fingerprint_marker * 64}",
        "pgo": nonapplicable_pgo() if pgo is None else pgo,
    }


def package_record() -> dict[str, Any]:
    identities = [
        component(
            "native-amd64",
            "amd64",
            "clang-ir",
            "clang",
            "1",
            pgo=optimized_pgo("clang-ir", "a"),
        ),
        component(
            "native-x86",
            "x86",
            "gcc-gcov",
            "gcc",
            "2",
            pgo=optimized_pgo("gcc-gcov", "b"),
        ),
        component(
            "rust-amd64",
            "amd64",
            "rust-llvm-ir",
            "rust",
            "3",
            component_kind="rust",
            target_triple="x86_64-unknown-linux-gnu",
            pgo=optimized_pgo("rust-llvm-ir", "c"),
        ),
        component(
            "share-data",
            "none",
            "not-applicable",
            None,
            "4",
            component_kind="script-data",
        ),
    ]
    return {
        "schema_version": 2,
        "record_type": "package",
        "cpv": "app-test/example-suite-1.0-r2",
        "cp": "app-test/example-suite",
        "repository": "gentoo",
        "slot": "0",
        "subslot": "0",
        "abis": ["amd64", "x86"],
        "ebuild_sha256": "5" * 64,
        "use_flags": ["abi_x86_32", "abi_x86_64", "pgo"],
        "build_identities": identities,
        "aggregate": {
            "component_count": 4,
            "artifact_count": 4,
            "pgo": {
                "eligible_count": 3,
                "optimized_count": 3,
                "excluded_count": 0,
                "not_applicable_count": 1,
                "pending_count": 0,
                "failed_count": 0,
                "status": "optimized",
            },
            "bolt": {
                "candidate_count": 3,
                "optimized_count": 2,
                "excluded_count": 1,
                "not_applicable_count": 1,
                "pending_count": 0,
                "failed_count": 0,
                "status": "optimized-with-exclusions",
            },
        },
        "final_status": "optimized-with-exclusions",
        "terminal_reason": None,
        "notes": [],
    }


def elf_metadata(elf_type: str = "DYN", elf_class: int = 64) -> dict[str, object]:
    return {
        "class": elf_class,
        "type": elf_type,
        "machine": "Advanced Micro Devices X86-64" if elf_class == 64 else "Intel 80386",
        "build_id": "ab12cd34",
        "text_sha256": "7" * 64,
        "has_symbols": True,
        "has_text_relocations": True,
        "has_executable_sections": True,
        "interpreter": "/lib64/ld-linux-x86-64.so.2" if elf_type in {"EXEC", "DYN"} else None,
        "needed": ["libc.so.6"] if elf_type in {"EXEC", "DYN"} else [],
    }


def artifact_record() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "record_type": "artifact",
        "owner_cpv": "app-test/example-suite-1.0-r2",
        "owner_cp": "app-test/example-suite",
        "owner_component_id": "native-amd64",
        "owner_component_fingerprint": f"sha256:{'1' * 64}",
        "kind": "elf",
        "format": "ELF",
        "installed_path": "/usr/bin/example",
        "canonical_path": "/usr/bin/example",
        "content_sha256": "6" * 64,
        "size": 4096,
        "abi": "amd64",
        "elf": elf_metadata(),
        "setuid": False,
        "setgid": False,
        "file_capabilities": [],
        "hardlink_paths": ["/usr/bin/example"],
        "symlink_paths": [],
        "bolt": {
            "eligibility": "eligible",
            "terminal_reason": None,
            "profile_samples": 42,
            "profile_stale_percent": 0.0,
            "output_path": "/var/cache/gentoo-optimization/bolt/outputs/example",
            "installed_has_bolt_note": True,
            "status": "optimized",
        },
        "final_status": "optimized",
    }


def noneligible_artifact(kind: str) -> dict[str, Any]:
    record = artifact_record()
    record["kind"] = kind
    record["format"] = {
        "static-archive": "GNU ar",
        "relocatable-object": "ELF relocatable",
        "kernel-module": "Linux kernel module",
        "ebpf": "ELF eBPF",
        "gpu-object": "SPIR-V",
        "firmware": "device firmware",
        "bytecode": "Python bytecode",
        "script": "POSIX shell",
        "data": "application data",
        "elf": "ELF",
    }[kind]
    record["installed_path"] = f"/usr/lib/example/{kind}"
    record["canonical_path"] = record["installed_path"]
    record["hardlink_paths"] = [record["installed_path"]]
    record["symlink_paths"] = []
    if kind in {"relocatable-object", "kernel-module", "ebpf"}:
        record["elf"] = elf_metadata("REL")
        record["abi"] = "other" if kind == "ebpf" else "amd64"
    elif kind == "elf":
        record["elf"] = elf_metadata()
        record["abi"] = "amd64"
    else:
        record["elf"] = None
        record["abi"] = "amd64" if kind == "static-archive" else (
            "other" if kind == "gpu-object" else None
        )
    record["bolt"] = {
        "eligibility": "not-applicable",
        "terminal_reason": reason(f"not-bolt-{kind}"),
        "profile_samples": 0,
        "profile_stale_percent": None,
        "output_path": None,
        "installed_has_bolt_note": False,
        "status": "not-applicable",
    }
    record["final_status"] = "not-applicable"
    return record


class PackageStateTests(unittest.TestCase):
    def test_valid_multi_abi_mixed_backend_package(self) -> None:
        record = STATE.validate_package(package_record())
        identities = record["build_identities"]
        self.assertEqual({item["abi"] for item in identities}, {"amd64", "x86", "none"})
        self.assertEqual(
            {item["build_backend"] for item in identities},
            {"clang-ir", "gcc-gcov", "rust-llvm-ir", "not-applicable"},
        )
        self.assertEqual(record["final_status"], "optimized-with-exclusions")

    def test_valid_package_level_terminal_exclusion_is_evidence_backed(self) -> None:
        record = package_record()
        identity = component(
            "unsafe-data",
            "none",
            "not-applicable",
            None,
            "9",
            component_kind="script-data",
        )
        identity["pgo"] = {
            "eligibility": "terminal-exclusion",
            "mode": "not-applicable",
            "generation_id": None,
            "profile_path": None,
            "profile_sha256": None,
            "profile_valid": False,
            "build_verified": False,
            "terminal_reason": reason("unsafe-to-profile"),
            "status": "terminal-exclusion",
        }
        record["abis"] = []
        record["build_identities"] = [identity]
        record["aggregate"] = {
            "component_count": 1,
            "artifact_count": 0,
            "pgo": {
                "eligible_count": 0,
                "optimized_count": 0,
                "excluded_count": 1,
                "not_applicable_count": 0,
                "pending_count": 0,
                "failed_count": 0,
                "status": "terminal-exclusion",
            },
            "bolt": {
                "candidate_count": 0,
                "optimized_count": 0,
                "excluded_count": 0,
                "not_applicable_count": 0,
                "pending_count": 0,
                "failed_count": 0,
                "status": "not-applicable",
            },
        }
        record["final_status"] = "terminal-exclusion"
        record["terminal_reason"] = reason("package-unsafe-to-profile")
        self.assertEqual(
            STATE.validate_package(record)["final_status"], "terminal-exclusion"
        )

    def test_exact_versioned_cpv_must_be_tied_to_cp(self) -> None:
        for cpv, cp in (
            ("app-test/example-suite", "app-test/example-suite"),
            ("app-test/other-1.0", "app-test/example-suite"),
            ("app-test/example-suite-live", "app-test/example-suite"),
        ):
            with self.subTest(cpv=cpv):
                record = package_record()
                record["cpv"] = cpv
                record["cp"] = cp
                with self.assertRaisesRegex(STATE.StateValidationError, "exact versioned CPV"):
                    STATE.validate_package(record)

    def test_component_ids_fingerprints_abis_and_order_are_exact(self) -> None:
        mutations = ("duplicate-id", "duplicate-fingerprint", "wrong-abis", "wrong-order")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                record = package_record()
                identities = record["build_identities"]
                if mutation == "duplicate-id":
                    identities[1]["component_id"] = identities[0]["component_id"]
                elif mutation == "duplicate-fingerprint":
                    identities[1]["fingerprint"] = identities[0]["fingerprint"]
                elif mutation == "wrong-abis":
                    record["abis"] = ["amd64"]
                else:
                    identities[0], identities[1] = identities[1], identities[0]
                with self.assertRaises(STATE.StateValidationError):
                    STATE.validate_package(record)

    def test_rust_target_and_compiler_backend_must_match_component(self) -> None:
        record = package_record()
        record["build_identities"][2]["target_triple"] = None
        with self.assertRaisesRegex(STATE.StateValidationError, "target_triple"):
            STATE.validate_package(record)
        record = package_record()
        record["build_identities"][1]["compiler"] = compiler("clang")
        with self.assertRaisesRegex(STATE.StateValidationError, "incompatible"):
            STATE.validate_package(record)
        record = package_record()
        record["build_identities"][0]["compiler"]["sha256"] = "not-exact"
        with self.assertRaisesRegex(STATE.StateValidationError, "lowercase SHA-256"):
            STATE.validate_package(record)

    def test_eligible_pgo_cannot_claim_terminal_state(self) -> None:
        record = package_record()
        pgo = record["build_identities"][0]["pgo"]
        pgo.update(
            {
                "status": "not-applicable",
                "mode": "not-applicable",
                "generation_id": None,
                "profile_path": None,
                "profile_sha256": None,
                "profile_valid": False,
                "build_verified": False,
                "terminal_reason": reason("contradiction"),
            }
        )
        with self.assertRaisesRegex(STATE.StateValidationError, "eligible PGO cannot"):
            STATE.validate_package(record)

    def test_terminal_and_failed_component_states_require_reviewed_evidence(self) -> None:
        record = package_record()
        record["build_identities"][3]["pgo"]["terminal_reason"] = None
        with self.assertRaisesRegex(STATE.StateValidationError, "required for a terminal"):
            STATE.validate_package(record)
        record = package_record()
        terminal = record["build_identities"][3]["pgo"]["terminal_reason"]
        terminal["reviewed"] = False
        with self.assertRaisesRegex(STATE.StateValidationError, "must be true"):
            STATE.validate_package(record)

    def test_optimized_pgo_requires_exact_profile_and_build_proof(self) -> None:
        for field, value in (
            ("profile_sha256", None),
            ("profile_valid", False),
            ("build_verified", False),
        ):
            with self.subTest(field=field):
                record = package_record()
                record["build_identities"][0]["pgo"][field] = value
                with self.assertRaises(STATE.StateValidationError):
                    STATE.validate_package(record)

    def test_pgo_and_bolt_aggregate_accounting_is_exact(self) -> None:
        record = package_record()
        record["aggregate"]["pgo"]["eligible_count"] = 2
        with self.assertRaisesRegex(STATE.StateValidationError, "counts do not match"):
            STATE.validate_package(record)
        record = package_record()
        record["aggregate"]["bolt"]["excluded_count"] = 0
        with self.assertRaisesRegex(STATE.StateValidationError, "cover every candidate"):
            STATE.validate_package(record)

    def test_final_status_cannot_hide_exclusions_or_failures(self) -> None:
        record = package_record()
        record["final_status"] = "optimized"
        with self.assertRaisesRegex(STATE.StateValidationError, "optimized-with-exclusions"):
            STATE.validate_package(record)
        record = package_record()
        record["aggregate"]["bolt"].update(
            {
                "optimized_count": 2,
                "excluded_count": 0,
                "failed_count": 1,
                "status": "failed",
            }
        )
        record["final_status"] = "failed"
        record["terminal_reason"] = None
        with self.assertRaisesRegex(STATE.StateValidationError, "terminal or failed"):
            STATE.validate_package(record)

    def test_arrays_and_unknown_keys_are_rejected(self) -> None:
        record = package_record()
        record["use_flags"] = ["pgo", "abi_x86_64"]
        with self.assertRaisesRegex(STATE.StateValidationError, "must be sorted"):
            STATE.validate_package(record)
        record = package_record()
        record["unreviewed"] = True
        with self.assertRaisesRegex(STATE.StateValidationError, "extra=.*unreviewed"):
            STATE.validate_package(record)


class ArtifactStateTests(unittest.TestCase):
    def test_valid_optimized_elf_artifact(self) -> None:
        record = STATE.validate_artifact(artifact_record())
        self.assertEqual(record["kind"], "elf")
        self.assertEqual(record["final_status"], "optimized")

    def test_valid_terminal_elf_exclusion_is_evidence_backed(self) -> None:
        record = artifact_record()
        record["elf"]["has_symbols"] = False
        record["bolt"] = {
            "eligibility": "terminal-exclusion",
            "terminal_reason": reason("missing-symbols"),
            "profile_samples": 0,
            "profile_stale_percent": None,
            "output_path": None,
            "installed_has_bolt_note": False,
            "status": "terminal-exclusion",
        }
        record["final_status"] = "terminal-exclusion"
        self.assertEqual(
            STATE.validate_artifact(record)["final_status"], "terminal-exclusion"
        )

    def test_every_required_artifact_kind_is_representable(self) -> None:
        for kind in sorted(STATE.ARTIFACT_KINDS):
            with self.subTest(kind=kind):
                record = artifact_record() if kind == "elf" else noneligible_artifact(kind)
                self.assertEqual(STATE.validate_artifact(record)["kind"], kind)

    def test_artifact_owner_cpv_is_exact_and_tied_to_cp(self) -> None:
        record = artifact_record()
        record["owner_cpv"] = "app-test/example-suite"
        with self.assertRaisesRegex(STATE.StateValidationError, "exact versioned CPV"):
            STATE.validate_artifact(record)
        record = artifact_record()
        record["owner_cp"] = "app-test/other"
        with self.assertRaisesRegex(STATE.StateValidationError, "exact versioned CPV"):
            STATE.validate_artifact(record)

    def test_kind_specific_elf_contract_is_strict(self) -> None:
        record = noneligible_artifact("script")
        record["elf"] = elf_metadata()
        with self.assertRaisesRegex(STATE.StateValidationError, "must be null for script"):
            STATE.validate_artifact(record)
        record = noneligible_artifact("kernel-module")
        record["elf"]["type"] = "DYN"
        with self.assertRaisesRegex(STATE.StateValidationError, "must be REL"):
            STATE.validate_artifact(record)
        record = artifact_record()
        record["elf"]["type"] = "REL"
        with self.assertRaisesRegex(STATE.StateValidationError, "relocatable-object"):
            STATE.validate_artifact(record)

    def test_nonelf_artifact_cannot_be_bolt_eligible(self) -> None:
        record = noneligible_artifact("data")
        record["bolt"] = copy.deepcopy(artifact_record()["bolt"])
        record["final_status"] = "optimized"
        with self.assertRaisesRegex(STATE.StateValidationError, "amd64 executable/shared ELF"):
            STATE.validate_artifact(record)

    def test_eligible_artifact_cannot_claim_terminal_status(self) -> None:
        record = artifact_record()
        record["bolt"].update(
            {
                "status": "terminal-exclusion",
                "terminal_reason": reason("contradiction"),
                "profile_samples": 0,
                "profile_stale_percent": None,
                "output_path": None,
                "installed_has_bolt_note": False,
            }
        )
        record["final_status"] = "terminal-exclusion"
        with self.assertRaisesRegex(STATE.StateValidationError, "eligible BOLT artifact cannot"):
            STATE.validate_artifact(record)

    def test_terminal_and_failed_artifacts_require_reviewed_evidence(self) -> None:
        record = noneligible_artifact("firmware")
        record["bolt"]["terminal_reason"] = None
        with self.assertRaisesRegex(STATE.StateValidationError, "required for a terminal"):
            STATE.validate_artifact(record)
        record = artifact_record()
        record["bolt"].update(
            {
                "status": "failed",
                "terminal_reason": None,
                "profile_samples": 0,
                "profile_stale_percent": None,
                "output_path": None,
                "installed_has_bolt_note": False,
            }
        )
        record["final_status"] = "failed"
        with self.assertRaisesRegex(STATE.StateValidationError, "terminal or failed"):
            STATE.validate_artifact(record)

    def test_optimized_elf_requires_identity_readiness_samples_and_note(self) -> None:
        mutations = (
            ("build-id", "elf", "build_id", None),
            ("relocations", "elf", "has_text_relocations", False),
            ("samples", "bolt", "profile_samples", 0),
            ("output", "bolt", "output_path", None),
            ("note", "bolt", "installed_has_bolt_note", False),
        )
        for name, section, key, value in mutations:
            with self.subTest(name=name):
                record = artifact_record()
                record[section][key] = value
                with self.assertRaises(STATE.StateValidationError):
                    STATE.validate_artifact(record)

    def test_final_status_cannot_contradict_bolt_state(self) -> None:
        record = noneligible_artifact("bytecode")
        record["final_status"] = "optimized"
        with self.assertRaisesRegex(STATE.StateValidationError, "must be not-applicable"):
            STATE.validate_artifact(record)

    def test_topology_abi_and_lowercase_build_id_are_exact(self) -> None:
        record = artifact_record()
        record["installed_path"] = "/usr/bin/example-link"
        with self.assertRaisesRegex(STATE.StateValidationError, "must appear"):
            STATE.validate_artifact(record)
        record = artifact_record()
        record["abi"] = "x86"
        with self.assertRaisesRegex(STATE.StateValidationError, "x86 ABI cannot"):
            STATE.validate_artifact(record)
        record = artifact_record()
        record["elf"]["build_id"] = "ABCD"
        with self.assertRaisesRegex(STATE.StateValidationError, "lowercase hexadecimal"):
            STATE.validate_artifact(record)
        record = artifact_record()
        record["canonical_path"] = "/usr/bin/../bin/example"
        record["hardlink_paths"] = ["/usr/bin/../bin/example"]
        with self.assertRaisesRegex(STATE.StateValidationError, "canonical absolute"):
            STATE.validate_artifact(record)


class PublicationTests(unittest.TestCase):
    def test_atomic_publish_is_canonical_and_private(self) -> None:
        record = package_record()
        with tempfile.TemporaryDirectory(prefix="optimization-state-test.") as temporary:
            output = Path(temporary) / "nested" / "package.json"
            digest = STATE.atomic_publish(STATE.validate_package(record), output)
            payload = output.read_bytes()
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertEqual(json.loads(payload), record)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertFalse(list(output.parent.glob("*.partial")))

    def test_output_and_parent_symlinks_are_rejected(self) -> None:
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
        self.assertEqual(schema["properties"]["schema_version"]["const"], 2)
        self.assertEqual(set(schema["required"]), STATE.PACKAGE_KEYS)
        self.assertEqual(set(schema["properties"]), STATE.PACKAGE_KEYS)
        self.assertEqual(
            set(schema["$defs"]["build_identity"]["required"]), STATE.BUILD_IDENTITY_KEYS
        )

    def test_artifact_schema_keys_match_validator(self) -> None:
        schema = json.loads(
            (REPOSITORY_ROOT / "optimization/schema/artifact-state.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], 2)
        self.assertEqual(set(schema["required"]), STATE.ARTIFACT_KEYS)
        self.assertEqual(set(schema["properties"]), STATE.ARTIFACT_KEYS)
        self.assertEqual(set(schema["$defs"]["elf"]["required"]), STATE.ELF_KEYS)


if __name__ == "__main__":
    unittest.main()

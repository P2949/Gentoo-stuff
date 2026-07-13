#!/usr/bin/env python3
"""Focused semantic, schema-parity, publication, and reconciliation tests."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = REPOSITORY_ROOT / "scripts/optimization/lib/state.py"
RECONCILE_PATH = REPOSITORY_ROOT / "scripts/optimization/verify/reconcile-state.py"
SPEC = importlib.util.spec_from_file_location("optimization_state", STATE_PATH)
assert SPEC is not None and SPEC.loader is not None
STATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATE)

H = {
    name: hashlib.sha256(name.encode()).hexdigest()
    for name in (
        "inventory", "entry", "contents", "environment", "vdb", "ebuild",
        "manifest", "source", "build", "transaction", "install", "binpkg",
        "equery", "smoke", "revdep", "profile-manifest", "profile-sidecar",
        "profile", "training", "validation", "diagnostic", "installed",
        "workload", "config", "image", "modules", "boot", "artifact", "text",
        "metadata", "perf", "merge", "output", "deploy", "rollback", "xattr",
        "graph", "failure",
    )
}


def evidence(name: str, kind: str = "log") -> dict[str, str]:
    return {
        "path": f"/var/lib/gentoo-optimization/evidence/{name}",
        "sha256": H.get(name, hashlib.sha256(name.encode()).hexdigest()),
        "kind": kind,
    }


def resolution(code: str = "not-machine-code") -> dict[str, Any]:
    return {
        "registry_version": "1",
        "reason_code": code,
        "reviewed_by": "optimization-reviewer",
        "reviewed_at": "2026-07-13T01:02:03Z",
        "evidence": [evidence("validation", "report")],
    }


def generation(inventory_sha: str = H["inventory"]) -> dict[str, str]:
    return {"generation_id": "generation-test", "inventory_id": "inventory-test", "inventory_sha256": inventory_sha}


def target(abi: str, *, kernel_release: str | None = None) -> dict[str, Any]:
    if abi == "amd64":
        triple, architecture, machine, elf_class = "x86_64-pc-linux-gnu", "x86_64", "Advanced Micro Devices X86-64", 64
    elif abi == "x86":
        triple, architecture, machine, elf_class = "i686-pc-linux-gnu", "i686", "Intel 80386", 32
    else:
        triple, architecture, machine, elf_class = "bpf-unknown-none", "bpf", "Linux BPF", 64
    return {
        "triple": triple, "architecture": architecture, "machine": machine,
        "abi": abi, "elf_class": elf_class, "endianness": "little",
        "libc": "glibc" if abi in {"amd64", "x86"} else None,
        "cxx_abi": "libc++" if abi in {"amd64", "x86"} else None,
        "kernel_release": kernel_release,
    }


def tool(role: str, family: str, marker: str | None = None) -> dict[str, Any]:
    marker = marker or role
    command = {"cc": "clang", "cxx": "clang++", "fc": "gfortran", "rustc": "rustc", "go": "go", "linker": "ld.lld", "archiver": "llvm-ar", "profiler": "perf", "llvm-bolt": "llvm-bolt", "perf2bolt": "perf2bolt", "merge-fdata": "merge-fdata"}[role]
    return {
        "role": role, "family": family, "path": f"/usr/bin/{command}",
        "realpath": f"/usr/lib/toolchain/bin/{command}",
        "sha256": hashlib.sha256(marker.encode()).hexdigest(),
        "version": f"{command} test-version", "target_triple": "x86_64-pc-linux-gnu",
    }


def toolchain(languages: list[str], backend: str, marker: str) -> dict[str, Any]:
    values: dict[str, Any] = {role: None for role in ("cc", "cxx", "fc", "rustc", "go", "linker", "archiver", "profiler")}
    if "c" in languages:
        values["cc"] = tool("cc", "gcc" if backend == "gcc-gcov" else "clang", marker + "cc")
    if "c++" in languages:
        values["cxx"] = tool("cxx", "clang", marker + "cxx")
    if "fortran" in languages:
        values["fc"] = tool("fc", "gcc", marker + "fc")
        # gcc-gcov's family check is anchored in cc; keep the complete tuple.
        values["cc"] = tool("cc", "gcc", marker + "cc")
    if "rust" in languages:
        values["rustc"] = tool("rustc", "rust", marker + "rustc")
    if "go" in languages:
        values["go"] = tool("go", "go", marker + "go")
    values["linker"] = tool("linker", "lld", marker + "linker")
    values["archiver"] = tool("archiver", "llvm-binutils", marker + "archiver")
    values["profiler"] = tool("profiler", "perf", marker + "profiler")
    values["runtimes"] = [{"name": "libc", "abi": "amd64", "path": "/lib64/libc.so.6", "sha256": hashlib.sha256((marker + "runtime").encode()).hexdigest(), "version": "glibc-test"}]
    values["environment_fingerprint"] = f"sha256:{hashlib.sha256((marker + 'environment').encode()).hexdigest()}"
    return values


def workload() -> dict[str, Any]:
    return {"workload_id": "representative-cli", "revision": "revision-1", "evidence": [evidence("workload", "manifest")]}


def optimized_pgo(backend: str, toolchain_fingerprint: str) -> dict[str, Any]:
    return {
        "eligibility": "eligible", "mode": backend, "status": "optimized",
        "generation_id": "generation-test", "manifest": evidence("profile-manifest", "manifest"),
        "sidecar": evidence("profile-sidecar", "sidecar"), "profile": evidence("profile", "profile"),
        "toolchain_fingerprint": toolchain_fingerprint, "workload_refs": [workload()],
        "training_evidence": [evidence("training", "log")], "validation_evidence": [evidence("validation", "report")],
        "build_use": {"build_log": evidence("build", "log"), "flags": ["-fprofile-use=/exact/profile"], "diagnostics": [evidence("diagnostic", "command-output")], "installed_evidence": [evidence("installed", "report")]},
        "resolution": None,
    }


def nonapplicable_pgo() -> dict[str, Any]:
    return {
        "eligibility": "not-applicable", "mode": "not-applicable", "status": "not-applicable",
        "generation_id": None, "manifest": None, "sidecar": None, "profile": None,
        "toolchain_fingerprint": None, "workload_refs": [], "training_evidence": [],
        "validation_evidence": [], "build_use": None, "resolution": resolution(),
    }


def component(component_id: str, kind: str, languages: list[str], abi: str, backend: str) -> dict[str, Any]:
    tc = None if backend == "not-applicable" else toolchain(languages, backend, component_id)
    tc_fingerprint = None if tc is None else tc["environment_fingerprint"]
    pgo_state = nonapplicable_pgo() if tc_fingerprint is None else optimized_pgo(backend, tc_fingerprint)
    is_kernel = kind == "kernel"
    item: dict[str, Any] = {
        "component_id": component_id, "component_kind": kind,
        "languages": sorted(languages), "abi": abi,
        "target": None if abi == "none" else target(abi, kernel_release="6.18.0-test" if is_kernel else None),
        "build_backend": backend, "toolchain": tc,
        "fingerprint": f"sha256:{hashlib.sha256(component_id.encode()).hexdigest()}",
        "pgo": pgo_state,
        "kernel": None,
    }
    if is_kernel:
        item["kernel"] = {
            "release": "6.18.0-test", "config": evidence("config", "config"),
            "image": evidence("image", "binary"), "modules_manifest": evidence("modules", "manifest"),
            "boot_entry_id": "Boot0004", "boot_evidence": [evidence("boot", "report")],
        }
    return item


def all_components() -> list[dict[str, Any]]:
    items = [
        component("01-clang-ir", "native", ["c"], "amd64", "clang-ir"),
        component("02-clang-sample", "native", ["c++"], "amd64", "clang-sample"),
        component("03-gcc-gcov", "native", ["fortran"], "x86", "gcc-gcov"),
        component("04-rust", "rust", ["rust"], "amd64", "rust-llvm-ir"),
        component("05-go", "go", ["go"], "amd64", "go-pprof"),
        component("06-native", "native", ["other"], "amd64", "ebuild-native"),
        component("07-kernel", "kernel", ["c"], "amd64", "kernel-autofdo"),
        component("08-jvm", "jvm", ["jvm"], "none", "not-applicable"),
        component("09-script-data", "script-data", ["data", "python", "shell"], "none", "not-applicable"),
    ]
    return sorted(items, key=lambda item: item["component_id"])


def source_rebuild(live_identity_sha: str) -> dict[str, Any]:
    return {
        "required": True, "status": "succeeded", "generation_id": "generation-test",
        "transaction_id": "source-rebuild-transaction-1", "source_only": True,
        "attempts": [{"attempt_id": "attempt-001", "started_at": "2026-07-13T01:00:00Z", "completed_at": "2026-07-13T01:01:00Z", "result": "succeeded", "environment_fingerprint": f"sha256:{H['source']}", "build_log": evidence("build", "log"), "failure_evidence": []}],
        "proof": {
            "transaction_log": evidence("transaction", "transaction"), "install_log": evidence("install", "log"),
            "binpkg": {"path": "/var/cache/binpkgs/app-test/example-suite-1.0-r2.gpkg.tar", "sha256": H["binpkg"], "format": "gpkg"},
            "equery_check": {"status": "passed", "evidence": [evidence("equery", "command-output")]},
            "smoke_tests": [{"name": "cli-version", "status": "passed", "evidence": [evidence("smoke", "command-output")]}],
            "reverse_dependencies": {"status": "passed", "evidence": [evidence("revdep", "report")]},
            "installed_vdb_identity_sha256": live_identity_sha,
        },
        "resolution": None,
    }


def package_record(*, inventory_sha: str = H["inventory"], artifact_count: int = 0, bolt_counts: dict[str, Any] | None = None, vdb_path: str = "/var/db/pkg/app-test/example-suite-1.0-r2", contents_sha: str = H["contents"], environment_sha: str | None = H["environment"]) -> dict[str, Any]:
    components = all_components()
    live: dict[str, Any] = {
        "vdb_path": vdb_path, "contents_sha256": contents_sha, "repository": "gentoo", "slot": "0", "subslot": "0",
        "build_time": "1783904400", "counter": "17", "environment_bz2_sha256": environment_sha, "identity_sha256": "",
    }
    live["identity_sha256"] = STATE.vdb_identity_sha256(live)
    if bolt_counts is None:
        bolt_counts = {"candidate_count": 0, "optimized_count": 0, "excluded_count": 0, "not_applicable_count": artifact_count, "pending_count": 0, "unknown_count": 0, "failed_count": 0, "status": "not-applicable"}
    return {
        "schema_version": 3, "record_type": "package", "generation": generation(inventory_sha),
        "identity": {"cpv": "app-test/example-suite-1.0-r2", "cp": "app-test/example-suite", "repository": "gentoo", "slot": "0", "subslot": "0"},
        "frozen_inventory_entry": {"entry_sha256": H["entry"], "installed_at_freeze": True}, "live_instance": live,
        "source": {"ebuild": evidence("ebuild", "source"), "manifest": evidence("manifest", "manifest"), "distfiles": [evidence("source", "source")], "source_fingerprint": f"sha256:{H['source']}"},
        "abis": ["amd64", "x86"], "languages": sorted({language for item in components for language in item["languages"]}), "use_flags": ["abi_x86_32", "abi_x86_64", "pgo"],
        "components": components, "source_rebuild": source_rebuild(live["identity_sha256"]),
        "graphs": {"consumer_refs": [], "workload_refs": [workload()], "reverse_dependency_refs": []},
        "aggregate": {
            "component_count": len(components), "artifact_count": artifact_count,
            "pgo": {"eligible_count": 7, "optimized_count": 7, "excluded_count": 0, "not_applicable_count": 2, "pending_count": 0, "unknown_count": 0, "failed_count": 0, "status": "optimized"},
            "bolt": bolt_counts,
        },
        "final_status": "optimized", "resolution": None, "notes": [],
    }


def elf_metadata(role: str = "pie-executable", abi: str = "amd64") -> dict[str, Any]:
    elf_type: str
    pie: bool
    interpreter: str | None
    if role == "executable":
        elf_type, pie, interpreter = "EXEC", False, "/lib64/ld-linux-x86-64.so.2"
    elif role in {"pie-executable", "shared-library", "plugin"}:
        elf_type, pie, interpreter = "DYN", role == "pie-executable", "/lib64/ld-linux-x86-64.so.2" if role == "pie-executable" else None
    else:
        elf_type, pie, interpreter = "REL", False, None
    machine = target(abi)["machine"]
    return {
        "class": target(abi)["elf_class"], "type": elf_type, "machine": machine,
        "build_id": "ab12cd34", "text_sha256": H["text"], "has_symbols": True,
        "has_relocations": True, "has_executable_sections": True,
        "soname": "libexample.so.1" if role == "shared-library" else None,
        "rpath": [], "runpath": [],
        "exports": [{"name": "example_api", "version": "EXAMPLE_1.0", "binding": "GLOBAL", "visibility": "DEFAULT", "type": "FUNC"}],
        "symbol_versions": [{"name": "EXAMPLE_1.0", "provider": "libexample.so.1", "default": True}],
        "debug": {"has_debug_info": True, "has_full_symtab": True, "separate_debug_path": "/usr/lib/debug/usr/bin/example.debug", "separate_debug_sha256": H["validation"], "gnu_debuglink": "example.debug"},
        "runtime_instrumentation": {"pgo_markers": ["profile-use-verified"], "bolt_note": role in {"executable", "pie-executable", "shared-library", "plugin"}, "build_id_note": True, "cet_properties": ["IBT", "SHSTK"]},
        "dynamic_linkage": {"is_dynamic": role not in {"relocatable", "kernel-module", "ebpf"}, "pie": pie, "interpreter": interpreter, "needed": ["libc.so.6"] if role not in {"relocatable", "kernel-module", "ebpf"} else []},
    }


def optimized_bolt() -> dict[str, Any]:
    return {
        "eligibility": "eligible", "status": "optimized", "generation_id": "generation-test", "resolution": None,
        "capture": {"input_path": "/var/cache/gentoo-optimization/bolt/inputs/example", "input_sha256": H["artifact"], "input_text_sha256": H["text"], "input_build_id": "ab12cd34", "manifest": evidence("manifest", "manifest"), "metadata_snapshot": evidence("metadata", "report")},
        "perf_profiles": [{"workload_id": "representative-cli", "perf_data": evidence("perf", "profile"), "perf_tool": tool("profiler", "perf"), "samples": 1200, "branch_entries": 24000, "lost_samples": 0}],
        "fdata": {"path": "/var/cache/gentoo-optimization/bolt/fdata/example.fdata", "sha256": H["profile"], "merge_log": evidence("merge", "log"), "sample_count": 1200, "stale_percent": 0.1},
        "tools": {"llvm_bolt": tool("llvm-bolt", "llvm-binutils"), "perf2bolt": tool("perf2bolt", "llvm-binutils"), "merge_fdata": tool("merge-fdata", "llvm-binutils")},
        "options": ["-icf=safe", "-reorder-blocks=ext-tsp", "-reorder-functions=cdsort"],
        "output": {"path": "/var/cache/gentoo-optimization/bolt/outputs/example", "sha256": H["output"], "text_sha256": H["installed"], "build_id": "cd34ef56", "bolt_note": True, "verification": [evidence("validation", "report")]},
        "deployment": {"transaction_id": "bolt-deploy-1", "prestrip_path": "/var/tmp/portage/app-test/example/image/usr/bin/example", "deploy_log": evidence("deploy", "log"), "rollback_artifact": evidence("rollback", "binary"), "installed_sha256": H["output"], "metadata_verified": True, "runtime_verified": True},
    }


def artifact_record(*, inventory_sha: str = H["inventory"], kind: str = "elf", path: str = "/usr/bin/example") -> dict[str, Any]:
    role_map = {"elf": "pie-executable", "static-archive": "archive", "relocatable-object": "relocatable", "kernel-image": "kernel-image", "kernel-module": "kernel-module", "ebpf": "ebpf", "gpu-object": "gpu-object", "firmware": "firmware", "bytecode": "bytecode", "script": "script", "data": "data"}
    role = role_map[kind]
    abi = "other" if kind in {"ebpf", "gpu-object"} else ("none" if kind in {"firmware", "bytecode", "script", "data"} else "amd64")
    machine_target = None if abi == "none" else target(abi, kernel_release="6.18.0-test" if kind in {"kernel-image", "kernel-module"} else None)
    elf = elf_metadata(role, abi) if kind in STATE.ELF_REQUIRED_KINDS else None
    kernel = None
    if kind in {"kernel-image", "kernel-module"}:
        is_module = kind == "kernel-module"
        kernel = {"release": "6.18.0-test", "artifact_type": "module" if is_module else "image", "module_name": "example" if is_module else None, "vermagic": "6.18.0-test SMP" if is_module else None, "config_sha256": H["config"], "signed": True, "signature_key_id": "test-key", "boot_entry_id": None if is_module else "Boot0004", "boot_evidence": [] if is_module else [evidence("boot", "report")]}
    optimized = kind == "elf"
    bolt = optimized_bolt() if optimized else {"eligibility": "not-applicable", "status": "not-applicable", "generation_id": None, "resolution": resolution("bolt-not-elf"), "capture": None, "perf_profiles": [], "fdata": None, "tools": None, "options": [], "output": None, "deployment": None}
    artifact_id = hashlib.sha256((kind + path).encode()).hexdigest()
    return {
        "schema_version": 3, "record_type": "artifact", "generation": generation(inventory_sha), "artifact_id": f"sha256:{artifact_id}",
        "owner": {"cpv": "app-test/example-suite-1.0-r2", "cp": "app-test/example-suite", "component_id": "01-clang-ir", "component_fingerprint": f"sha256:{hashlib.sha256('01-clang-ir'.encode()).hexdigest()}"},
        "kind": kind, "format": "ELF" if elf else kind, "role": role, "installed_path": path, "canonical_path": path,
        "content_sha256": H["output"] if optimized else H["artifact"], "size": 4096, "abi": abi, "target": machine_target,
        "metadata": {"mode": 0o755, "uid": 0, "gid": 0, "mtime_ns": 1783904400000000000, "xattrs": [{"name": "user.test", "value_sha256": H["xattr"]}], "file_capabilities": [], "selinux_context": None},
        "topology": {"device": 2049, "inode": 1001, "link_count": 1, "hardlink_paths": [path], "symlinks": []},
        "elf": elf, "kernel": kernel, "graphs": {"consumer_refs": [], "workload_refs": [workload()], "reverse_dependency_refs": []},
        "bolt": bolt, "final_status": "optimized" if optimized else "not-applicable", "resolution": None if optimized else resolution("bolt-not-elf"),
    }


class PackageContractTests(unittest.TestCase):
    def test_all_backends_languages_abis_and_kernel_are_representable(self) -> None:
        record = STATE.validate_package(package_record())
        self.assertEqual({item["build_backend"] for item in record["components"]}, STATE.BACKENDS)
        self.assertEqual(set(record["languages"]), STATE.LANGUAGES - {"other"} | {"other"})
        kernel = next(item for item in record["components"] if item["component_kind"] == "kernel")
        self.assertEqual(kernel["kernel"]["boot_entry_id"], "Boot0004")

    def test_live_vdb_and_rebuild_proof_are_cryptographically_bound(self) -> None:
        record = package_record()
        record["live_instance"]["counter"] = "18"
        with self.assertRaisesRegex(STATE.StateValidationError, "identity_sha256"):
            STATE.validate_package(record)
        record = package_record()
        record["source_rebuild"]["proof"]["installed_vdb_identity_sha256"] = "0" * 64
        with self.assertRaisesRegex(STATE.StateValidationError, "must equal live_instance"):
            STATE.validate_package(record)

    def test_source_rebuild_requires_attempt_log_binpkg_checks_and_transaction(self) -> None:
        mutations: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            ("source-only", lambda r: r["source_rebuild"].__setitem__("source_only", False)),
            ("attempt", lambda r: r["source_rebuild"].__setitem__("attempts", [])),
            ("binpkg", lambda r: r["source_rebuild"]["proof"]["binpkg"].__setitem__("sha256", "bad")),
            ("smoke", lambda r: r["source_rebuild"]["proof"].__setitem__("smoke_tests", [])),
            ("revdep", lambda r: r["source_rebuild"]["proof"]["reverse_dependencies"].__setitem__("evidence", [])),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                record = package_record(); mutate(record)
                with self.assertRaises(STATE.StateValidationError):
                    STATE.validate_package(record)

    def test_optimized_pgo_requires_exact_manifest_sidecar_tool_workload_and_build_use(self) -> None:
        for field in ("manifest", "sidecar", "profile", "toolchain_fingerprint", "build_use"):
            with self.subTest(field=field):
                record = package_record(); record["components"][0]["pgo"][field] = None
                with self.assertRaises(STATE.StateValidationError):
                    STATE.validate_package(record)
        record = package_record(); record["components"][0]["pgo"]["workload_refs"] = []
        with self.assertRaisesRegex(STATE.StateValidationError, "workload"):
            STATE.validate_package(record)

    def test_tool_tuple_target_and_runtime_identity_are_strict(self) -> None:
        record = package_record(); record["components"][0]["toolchain"]["cc"]["family"] = "gcc"
        with self.assertRaisesRegex(STATE.StateValidationError, "requires a pure Clang"):
            STATE.validate_package(record)
        record = package_record(); record["components"][2]["target"]["elf_class"] = 64
        with self.assertRaisesRegex(STATE.StateValidationError, "x86 must map"):
            STATE.validate_package(record)
        record = package_record(); record["components"][3]["toolchain"]["rustc"] = None
        with self.assertRaisesRegex(STATE.StateValidationError, "required for rust"):
            STATE.validate_package(record)

    def test_unknown_failed_terminal_and_not_applicable_need_registry_evidence(self) -> None:
        record = package_record(); record["components"][-1]["pgo"]["resolution"] = None
        with self.assertRaisesRegex(STATE.StateValidationError, "required"):
            STATE.validate_package(record)
        record = package_record(); record["components"][-1]["pgo"]["resolution"]["reason_code"] = "free-form"
        with self.assertRaisesRegex(STATE.StateValidationError, "must be one of"):
            STATE.validate_package(record)

    def test_aggregate_cannot_hide_pending_unknown_or_failed(self) -> None:
        for status, count_key in (("pending", "pending_count"), ("unknown", "unknown_count"), ("failed", "failed_count")):
            with self.subTest(status=status):
                record = package_record()
                pgo = record["components"][0]["pgo"]
                pgo.update({"status": status, "generation_id": None if status == "unknown" else "generation-test", "manifest": None, "sidecar": None, "profile": None, "toolchain_fingerprint": None, "workload_refs": [], "training_evidence": [], "validation_evidence": [], "build_use": None, "resolution": resolution("classification-unknown" if status == "unknown" else "verification-failed") if status in {"unknown", "failed"} else None})
                if status == "unknown":
                    pgo["eligibility"] = "unknown"; pgo["mode"] = "not-applicable"
                aggregate = record["aggregate"]["pgo"]
                aggregate["eligible_count"] -= 1 if status == "unknown" else 0
                aggregate["optimized_count"] -= 1
                aggregate[count_key] += 1
                aggregate["status"] = status
                record["final_status"] = status
                record["resolution"] = resolution("classification-unknown" if status == "unknown" else "verification-failed") if status in {"unknown", "failed"} else None
                self.assertEqual(STATE.validate_package(record)["final_status"], status)


class ArtifactContractTests(unittest.TestCase):
    def test_every_artifact_kind_and_role_is_representable(self) -> None:
        for index, kind in enumerate(sorted(STATE.ARTIFACT_KINDS)):
            with self.subTest(kind=kind):
                record = artifact_record(kind=kind, path=f"/usr/lib/example/{index}-{kind}")
                self.assertEqual(STATE.validate_artifact(record)["kind"], kind)

    def test_strict_installed_metadata_and_topology(self) -> None:
        record = artifact_record(); record["metadata"]["mode"] = 0o10000
        with self.assertRaisesRegex(STATE.StateValidationError, "permission"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["topology"]["link_count"] = 2
        with self.assertRaisesRegex(STATE.StateValidationError, "hardlink"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["topology"]["symlinks"] = [{"path": "/usr/bin/example", "target": "example.real"}]
        with self.assertRaisesRegex(STATE.StateValidationError, "must not overlap"):
            STATE.validate_artifact(record)

    def test_elf_contract_covers_soname_paths_exports_versions_debug_and_runtime(self) -> None:
        record = artifact_record(); record["elf"]["runpath"] = ["$ORIGIN"]
        with self.assertRaisesRegex(STATE.StateValidationError, "canonical absolute"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["elf"]["debug"]["separate_debug_sha256"] = None
        with self.assertRaisesRegex(STATE.StateValidationError, "set together"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["elf"]["exports"].append(copy.deepcopy(record["elf"]["exports"][0]))
        with self.assertRaisesRegex(STATE.StateValidationError, "sorted and unique"):
            STATE.validate_artifact(record)

    def test_optimized_bolt_requires_capture_perf_fdata_tools_exact_options_output_and_deploy(self) -> None:
        for field, empty in (("capture", None), ("perf_profiles", []), ("fdata", None), ("tools", None), ("options", []), ("output", None), ("deployment", None)):
            with self.subTest(field=field):
                record = artifact_record(); record["bolt"][field] = empty
                with self.assertRaises(STATE.StateValidationError):
                    STATE.validate_artifact(record)
        record = artifact_record(); record["bolt"]["deployment"]["metadata_verified"] = False
        with self.assertRaisesRegex(STATE.StateValidationError, "must be true"):
            STATE.validate_artifact(record)

    def test_machine_abi_role_and_kernel_state_are_exact(self) -> None:
        record = artifact_record(); record["target"]["machine"] = "Intel 80386"
        with self.assertRaisesRegex(STATE.StateValidationError, "amd64 must map"):
            STATE.validate_artifact(record)
        record = artifact_record(kind="kernel-module", path="/lib/modules/6.18.0-test/example.ko"); record["kernel"] = None
        with self.assertRaisesRegex(STATE.StateValidationError, "required for a kernel artifact"):
            STATE.validate_artifact(record)
        record = artifact_record(kind="kernel-image", path="/efi/vmlinuz-test.efi"); record["kernel"]["boot_evidence"] = []
        with self.assertRaisesRegex(STATE.StateValidationError, "boot entry and boot evidence"):
            STATE.validate_artifact(record)


class CollectionTests(unittest.TestCase):
    def collection(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
        inventory = {"schema_version": 1, "record_type": "frozen-inventory", "generation_id": "generation-test", "inventory_id": "inventory-test", "cpvs": ["app-test/example-suite-1.0-r2"], "owned_paths": [{"owner_cpv": "app-test/example-suite-1.0-r2", "path": "/usr/bin/example"}]}
        payload = STATE.canonical_bytes(inventory); inv_sha = hashlib.sha256(payload).hexdigest()
        bolt_counts = {"candidate_count": 1, "optimized_count": 1, "excluded_count": 0, "not_applicable_count": 0, "pending_count": 0, "unknown_count": 0, "failed_count": 0, "status": "optimized"}
        package = package_record(inventory_sha=inv_sha, artifact_count=1, bolt_counts=bolt_counts)
        artifact = artifact_record(inventory_sha=inv_sha)
        return package, artifact, inventory, payload

    def test_exact_inventory_owner_component_and_aggregate_reconciliation(self) -> None:
        package, artifact, inventory, payload = self.collection()
        summary = STATE.reconcile_collection([package], [artifact], inventory=inventory, inventory_sha256=hashlib.sha256(payload).hexdigest())
        self.assertTrue(summary["coverage_complete"])
        self.assertEqual(summary["counts"]["pending_total"], 0)
        self.assertEqual(summary["counts"]["unknown_total"], 0)
        self.assertEqual(summary["counts"]["failed_total"], 0)
        self.assertEqual(summary["counts"]["source_rebuild_succeeded_total"], 1)

    def test_inventory_cpv_owned_path_and_generation_must_be_exact(self) -> None:
        package, artifact, inventory, payload = self.collection()
        for mutation, message in (
            (lambda: inventory["cpvs"].append("app-test/extra-1"), "exact CPV mismatch"),
            (lambda: inventory["owned_paths"][0].__setitem__("path", "/usr/bin/other"), "owned-path mismatch"),
            (lambda: (artifact["generation"].__setitem__("generation_id", "other"), artifact["bolt"].__setitem__("generation_id", "other")), "different generation"),
        ):
            with self.subTest(message=message):
                package, artifact, inventory, payload = self.collection(); mutation()
                with self.assertRaisesRegex(STATE.StateValidationError, message):
                    STATE.reconcile_collection([package], [artifact], inventory=inventory, inventory_sha256=hashlib.sha256(payload).hexdigest())

    def test_owner_component_fingerprint_aggregate_and_topology_ambiguity_fail(self) -> None:
        package, artifact, inventory, payload = self.collection(); artifact["owner"]["component_fingerprint"] = f"sha256:{'0' * 64}"
        with self.assertRaisesRegex(STATE.StateValidationError, "fingerprint mismatch"):
            STATE.reconcile_collection([package], [artifact])
        package, artifact, inventory, payload = self.collection(); package["aggregate"]["artifact_count"] = 2; package["aggregate"]["bolt"]["not_applicable_count"] = 1
        with self.assertRaisesRegex(STATE.StateValidationError, "artifact_count"):
            STATE.reconcile_collection([package], [artifact])
        package, artifact, inventory, payload = self.collection(); second = copy.deepcopy(artifact); second["artifact_id"] = f"sha256:{'f' * 64}"
        with self.assertRaisesRegex(STATE.StateValidationError, "ambiguous path"):
            STATE.reconcile_collection([package], [artifact, second])

    def test_graph_references_must_resolve(self) -> None:
        package, artifact, _inventory, _payload = self.collection()
        package["graphs"]["consumer_refs"] = [{"cpv": "app-test/missing-1", "component_id": None, "evidence": [evidence("graph", "report")]}]
        with self.assertRaisesRegex(STATE.StateValidationError, "absent CPV"):
            STATE.reconcile_collection([package], [artifact])

    def test_live_vdb_cpv_contents_owner_and_symlink_types_are_exact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="state-vdb.") as temporary:
            root = Path(temporary); instance = root / "app-test/example-suite-1.0-r2"; instance.mkdir(parents=True)
            contents = "obj /usr/bin/example deadbeef 1783904400\n"
            (instance / "CONTENTS").write_text(contents, encoding="utf-8")
            (instance / "repository").write_text("gentoo\n", encoding="utf-8")
            (instance / "SLOT").write_text("0\n", encoding="utf-8")
            (instance / "BUILD_TIME").write_text("1783904400\n", encoding="utf-8")
            (instance / "COUNTER").write_text("17\n", encoding="utf-8")
            (instance / "environment.bz2").write_bytes(b"environment")
            package, artifact, inventory, payload = self.collection()
            package["live_instance"].update({"vdb_path": str(instance), "contents_sha256": hashlib.sha256(contents.encode()).hexdigest(), "environment_bz2_sha256": hashlib.sha256(b"environment").hexdigest()})
            package["live_instance"]["identity_sha256"] = STATE.vdb_identity_sha256(package["live_instance"])
            package["source_rebuild"]["proof"]["installed_vdb_identity_sha256"] = package["live_instance"]["identity_sha256"]
            summary = STATE.reconcile_collection([package], [artifact], vdb_root=root)
            self.assertTrue(summary["coverage_complete"])
            (instance / "CONTENTS").write_text("sym /usr/bin/example -> example.real 1783904400\n", encoding="utf-8")
            package["live_instance"]["contents_sha256"] = hashlib.sha256((instance / "CONTENTS").read_bytes()).hexdigest()
            package["live_instance"]["identity_sha256"] = STATE.vdb_identity_sha256(package["live_instance"])
            package["source_rebuild"]["proof"]["installed_vdb_identity_sha256"] = package["live_instance"]["identity_sha256"]
            with self.assertRaisesRegex(STATE.StateValidationError, "topology type mismatch"):
                STATE.reconcile_collection([package], [artifact], vdb_root=root)

    def test_cli_atomically_publishes_summary_and_require_complete(self) -> None:
        package, artifact, inventory, payload = self.collection()
        with tempfile.TemporaryDirectory(prefix="state-cli.") as temporary:
            root = Path(temporary); packages = root / "packages"; artifacts = root / "artifacts"; packages.mkdir(); artifacts.mkdir()
            inventory_path = root / "inventory.json"; inventory_path.write_bytes(payload)
            # The canonical payload hash was already embedded in these records.
            (packages / "package.json").write_bytes(STATE.canonical_bytes(package)); (artifacts / "artifact.json").write_bytes(STATE.canonical_bytes(artifact))
            output = root / "report/reconciliation.json"
            result = subprocess.run([sys.executable, str(RECONCILE_PATH), "--packages-dir", str(packages), "--artifacts-dir", str(artifacts), "--inventory", str(inventory_path), "--output", str(output), "--require-complete"], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(output.read_text())["coverage_complete"])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertFalse(list(output.parent.glob("*.partial")))


class PublicationAndSchemaTests(unittest.TestCase):
    def test_atomic_publication_is_private_canonical_and_symlink_safe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="state-publish.") as temporary:
            root = Path(temporary); output = root / "state/package.json"
            record = STATE.validate_package(package_record()); digest = STATE.atomic_publish(record, output)
            self.assertEqual(digest, hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            target = root / "target.json"; target.write_text("preserve\n"); alias = root / "alias.json"; alias.symlink_to(target)
            with self.assertRaisesRegex(STATE.StateValidationError, "must not be a symlink"):
                STATE.atomic_publish(record, alias)
            self.assertEqual(target.read_text(), "preserve\n")

    def test_schema_json_parses_and_top_level_keys_match(self) -> None:
        package_schema = json.loads((REPOSITORY_ROOT / "optimization/schema/package-state.schema.json").read_text())
        artifact_schema = json.loads((REPOSITORY_ROOT / "optimization/schema/artifact-state.schema.json").read_text())
        self.assertEqual(package_schema["properties"]["schema_version"]["const"], 3)
        self.assertEqual(set(package_schema["required"]), STATE.PACKAGE_KEYS)
        self.assertEqual(set(artifact_schema["required"]), STATE.ARTIFACT_KEYS)

    def test_draft_202012_schema_parity_positive_and_negative(self) -> None:
        try:
            import jsonschema  # type: ignore[import-untyped]
        except ImportError:
            self.skipTest("jsonschema unavailable: Draft 2020-12 parity cannot run")
        pairs = (("package-state.schema.json", package_record(), STATE.validate_package), ("artifact-state.schema.json", artifact_record(), STATE.validate_artifact))
        for filename, record, validator in pairs:
            with self.subTest(filename=filename):
                schema = json.loads((REPOSITORY_ROOT / "optimization/schema" / filename).read_text())
                jsonschema.Draft202012Validator.check_schema(schema)
                jsonschema.Draft202012Validator(schema).validate(record)
                validator(record)
                invalid = copy.deepcopy(record); invalid["schema_version"] = 2
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.Draft202012Validator(schema).validate(invalid)
                with self.assertRaises(STATE.StateValidationError):
                    validator(invalid)


if __name__ == "__main__":
    unittest.main()

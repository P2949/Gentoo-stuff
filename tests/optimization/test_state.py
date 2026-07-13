#!/usr/bin/env python3
"""Focused semantic, schema-parity, publication, and reconciliation tests."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
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
        "graph", "failure", "receipt", "command",
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


def toolchain(languages: list[str], backend: str, marker: str, abi: str) -> dict[str, Any]:
    values: dict[str, Any] = {role: None for role in ("cc", "cxx", "fc", "rustc", "go", "linker", "archiver", "profiler")}
    if "c" in languages:
        values["cc"] = tool("cc", "gcc" if backend == "gcc-gcov" else "clang", marker + "cc")
    if "c++" in languages:
        values["cxx"] = tool("cxx", "gcc" if backend == "gcc-gcov" else "clang", marker + "cxx")
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
    values["runtimes"] = [{"name": "libc", "abi": abi, "path": "/lib32/libc.so.6" if abi == "x86" else "/lib64/libc.so.6", "sha256": hashlib.sha256((marker + "runtime").encode()).hexdigest(), "version": "glibc-test"}]
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
        "build_use": {"build_log": evidence("build", "log"), "flags": ["-fprofile-use=/exact/profile"], "diagnostics": [evidence("diagnostic", "command-output")], "installed_evidence": [evidence("installed", "report")], "validator_receipt": evidence("receipt", "report")},
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
    tc = None if backend == "not-applicable" else toolchain(languages, backend, component_id, abi)
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
            "binpkg": {"path": "/var/cache/binpkgs/app-test/example-suite-1.0-r2.gpkg.tar", "sha256": H["binpkg"], "format": "gpkg", "production_marker": evidence("receipt", "manifest")},
            "equery_check": {"status": "passed", "evidence": [evidence("equery", "command-output")]},
            "smoke_tests": [{"name": "cli-version", "status": "passed", "evidence": [evidence("smoke", "command-output")]}],
            "reverse_dependencies": {"status": "passed", "evidence": [evidence("revdep", "report")]},
            "installed_vdb_identity_sha256": live_identity_sha,
            "active_modes": ["pgo-use"],
            "portage_transaction_receipt": evidence("transaction", "transaction"),
            "binpkg_validation_receipt": evidence("receipt", "report"),
        },
        "resolution": None,
    }


def package_record(*, inventory_sha: str = H["inventory"], artifact_count: int = 0, bolt_counts: dict[str, Any] | None = None, vdb_path: str = "/var/db/pkg/app-test/example-suite-1.0-r2", contents_sha: str = H["contents"], environment_sha: str | None = H["environment"]) -> dict[str, Any]:
    components = all_components()
    live: dict[str, Any] = {
        "vdb_path": vdb_path, "contents_sha256": contents_sha, "metadata_tree_sha256": H["vdb"], "repository": "gentoo", "slot_raw": "0", "slot": "0", "subslot": "0",
        "build_time": "1783904400", "counter": "17", "environment_bz2_sha256": environment_sha, "identity_sha256": "",
    }
    live["identity_sha256"] = STATE.vdb_identity_sha256(live)
    if bolt_counts is None:
        bolt_counts = {"candidate_count": 0, "optimized_count": 0, "excluded_count": 0, "not_applicable_count": artifact_count, "pending_count": 0, "unknown_count": 0, "failed_count": 0, "status": "not-applicable"}
    rebuilt = source_rebuild(live["identity_sha256"])
    if bolt_counts["optimized_count"]:
        rebuilt["proof"]["active_modes"] = ["bolt-deploy", "pgo-use"]
    frozen_payload = {"cpv": "app-test/example-suite-1.0-r2", "repository": "gentoo", "slot_raw": "0", "contents_sha256": H["contents"], "metadata_tree_sha256": H["vdb"]}
    frozen_sha = hashlib.sha256(STATE.canonical_bytes(frozen_payload)).hexdigest()
    return {
        "schema_version": 4, "record_type": "package", "generation": generation(inventory_sha),
        "identity": {"cpv": "app-test/example-suite-1.0-r2", "cp": "app-test/example-suite", "repository": "gentoo", "slot": "0", "subslot": "0"},
        "frozen_inventory_entry": {"entry_sha256": frozen_sha, "installed_at_freeze": True, "payload": frozen_payload}, "live_instance": live,
        "source": {"ebuild": evidence("ebuild", "source"), "manifest": evidence("manifest", "manifest"), "distfiles": [evidence("source", "source")], "source_fingerprint": f"sha256:{H['source']}"},
        "abis": ["amd64", "x86"], "languages": sorted({language for item in components for language in item["languages"]}), "use_flags": ["abi_x86_32", "abi_x86_64", "pgo"],
        "components": components, "source_rebuild": rebuilt,
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
        "class": target(abi)["elf_class"], "data_encoding": "little", "type": elf_type, "machine": machine,
        "build_id": "ab12cd34", "text_sha256": H["text"], "has_symbols": True,
        "has_relocations": True, "has_executable_sections": True,
        "soname": "libexample.so.1" if role == "shared-library" else None,
        "rpath": [], "runpath": [],
        "exports": [{"name": "example_api", "version": "EXAMPLE_1.0", "binding": "GLOBAL", "visibility": "DEFAULT", "type": "FUNC"}],
        "symbol_versions": [{"name": "EXAMPLE_1.0", "provider": "libexample.so.1", "default": True}],
        "debug": {"has_debug_info": True, "has_full_symtab": True, "separate_debug_path": "/usr/lib/debug/usr/bin/example.debug", "separate_debug_sha256": H["validation"], "gnu_debuglink": "example.debug"},
        "runtime_instrumentation": {"pgo_markers": [], "bolt_note": role in {"executable", "pie-executable", "shared-library", "plugin"}, "build_id_note": True, "cet_properties": ["IBT", "SHSTK"]},
        "dynamic_linkage": {"is_dynamic": role not in {"relocatable", "kernel-module", "ebpf"}, "pie": pie, "interpreter": interpreter, "needed": ["libc.so.6"] if role not in {"relocatable", "kernel-module", "ebpf"} else []},
        "security": {"gnu_stack": "non-executable", "relro": True, "bind_now": True, "writable_executable_load": False},
    }


def optimized_bolt() -> dict[str, Any]:
    llvm = tool("llvm-bolt", "llvm-binutils")
    command_record = evidence("command", "manifest")
    partial = "/var/cache/gentoo-optimization/bolt/outputs/example.partial"
    return {
        "eligibility": "eligible", "status": "optimized", "generation_id": "generation-test", "resolution": None,
        "capture": {"input_path": "/var/cache/gentoo-optimization/bolt/inputs/example", "input_sha256": H["artifact"], "input_text_sha256": H["text"], "input_build_id": "ab12cd34", "manifest": evidence("manifest", "manifest"), "metadata_snapshot": evidence("metadata", "report")},
        "perf_profiles": [{"workload_id": "representative-cli", "perf_data": evidence("perf", "profile"), "perf_tool": tool("profiler", "perf"), "samples": 1200, "branch_entries": 24000, "lost_samples": 0}],
        "fdata": {"path": "/var/cache/gentoo-optimization/bolt/fdata/example.fdata", "sha256": H["profile"], "merge_log": evidence("merge", "log"), "input_sample_count": 1200, "fdata_record_count": 24000, "count_evidence": evidence("diagnostic", "command-output"), "stale_percent": 0.1},
        "tools": {"llvm_bolt": llvm, "perf2bolt": tool("perf2bolt", "llvm-binutils"), "merge_fdata": tool("merge-fdata", "llvm-binutils")},
        "option_policy_revision": STATE.BOLT_POLICY_REVISION,
        "options": STATE.BOLT_APPROVED_ARGV.copy(),
        "command": {"argv": [llvm["realpath"], "/var/cache/gentoo-optimization/bolt/inputs/example", "-o", partial, "-data=/var/cache/gentoo-optimization/bolt/fdata/example.fdata", *STATE.BOLT_APPROVED_ARGV], "output_partial_path": partial, "record": command_record},
        "output": {"path": "/var/cache/gentoo-optimization/bolt/outputs/example", "sha256": H["output"], "text_sha256": H["installed"], "build_id": "cd34ef56", "bolt_note": True, "note_binding": {"input_sha256": H["artifact"], "fdata_sha256": H["profile"], "option_policy_revision": STATE.BOLT_POLICY_REVISION, "command_record_sha256": command_record["sha256"]}, "verification": [evidence("validation", "report")]},
        "deployment": {"transaction_id": "bolt-deploy-1", "prestrip_path": "/var/tmp/portage/app-test/example/image/usr/bin/example", "prestrip_deployed_sha256": H["output"], "deploy_log": evidence("deploy", "log"), "rollback_artifact": evidence("rollback", "binary"), "installed_sha256": H["installed"], "post_strip_verification": [evidence("validation", "report")], "metadata_verified": True, "runtime_verified": True},
    }


def artifact_record(*, inventory_sha: str = H["inventory"], kind: str = "elf", path: str = "/usr/bin/example") -> dict[str, Any]:
    role_map = {"elf": "pie-executable", "static-archive": "archive", "relocatable-object": "relocatable", "kernel-image": "kernel-image", "kernel-module": "kernel-module", "ebpf": "ebpf", "gpu-object": "gpu-object", "firmware": "firmware", "bytecode": "bytecode", "script": "script", "data": "data", "symlink": "symlink"}
    role = role_map[kind]
    abi = "other" if kind in {"ebpf", "gpu-object"} else ("none" if kind in {"firmware", "bytecode", "script", "data", "symlink"} else "amd64")
    machine_target = None if abi == "none" else target(abi, kernel_release="6.18.0-test" if kind in {"kernel-image", "kernel-module"} else None)
    elf = elf_metadata(role, abi) if kind in STATE.ELF_REQUIRED_KINDS else None
    kernel = None
    if kind in {"kernel-image", "kernel-module"}:
        is_module = kind == "kernel-module"
        kernel = {"release": "6.18.0-test", "artifact_type": "module" if is_module else "image", "module_name": "example" if is_module else None, "vermagic": "6.18.0-test SMP" if is_module else None, "config_sha256": H["config"], "signed": True, "signature_key_id": "test-key", "boot_entry_id": None if is_module else "Boot0004", "boot_evidence": [] if is_module else [evidence("boot", "report")]}
    optimized = kind == "elf"
    if optimized and elf is not None:
        elf["build_id"] = "cd34ef56"
        elf["text_sha256"] = H["installed"]
    bolt = optimized_bolt() if optimized else {"eligibility": "not-applicable", "status": "not-applicable", "generation_id": None, "resolution": resolution("bolt-not-elf"), "capture": None, "perf_profiles": [], "fdata": None, "tools": None, "option_policy_revision": None, "options": [], "command": None, "output": None, "deployment": None}
    artifact_id = hashlib.sha256((kind + path).encode()).hexdigest()
    record = {
        "schema_version": 4, "record_type": "artifact", "generation": generation(inventory_sha), "artifact_id": f"sha256:{artifact_id}",
        "owner": {"cpv": "app-test/example-suite-1.0-r2", "cp": "app-test/example-suite", "component_id": "01-clang-ir", "component_fingerprint": f"sha256:{hashlib.sha256('01-clang-ir'.encode()).hexdigest()}"},
        "kind": kind, "format": "ELF" if elf else kind, "role": role, "installed_path": path, "canonical_path": path,
        "content_sha256": H["installed"] if optimized else H["artifact"], "size": 4096, "abi": abi, "target": machine_target,
        "metadata": {"file_type": "regular", "device_major": None, "device_minor": None, "mode": 0o755, "uid": 0, "gid": 0, "mtime_ns": 1783904400000000000, "xattrs": [{"name": "user.test", "value_sha256": H["xattr"]}], "file_capabilities": [], "selinux_context": None},
        "topology": {"device": 2049, "inode": 1001, "link_count": 1, "hardlink_paths": [path], "symlinks": []},
        "elf": elf, "kernel": kernel, "graphs": {"consumer_refs": [], "workload_refs": [workload()], "reverse_dependency_refs": []},
        "bolt": bolt, "final_status": "optimized" if optimized else "not-applicable", "resolution": None if optimized else resolution("bolt-not-elf"),
    }
    if kind == "symlink":
        record["metadata"]["file_type"] = "symlink"
        record["topology"]["hardlink_paths"] = []
        record["topology"]["symlinks"] = [{"path": path, "target": "/missing-target"}]
        record["size"] = len("/missing-target")
        record["content_sha256"] = hashlib.sha256(b"/missing-target").hexdigest()
    return record


def materialize_evidence(value: Any, root: Path) -> None:
    """Move all evidence claims into one hermetic trusted tree with real hashes."""
    if isinstance(value, dict):
        if set(value) == {"path", "sha256", "kind"}:
            payload = value["path"].encode()
            path = root / hashlib.sha256(payload).hexdigest()
            path.write_bytes(payload)
            value["path"] = str(path)
            value["sha256"] = hashlib.sha256(payload).hexdigest()
            return
        for child in value.values():
            materialize_evidence(child, root)
    elif isinstance(value, list):
        for child in value:
            materialize_evidence(child, root)


def strict_fixture(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes, dict[str, Any], dict[str, Path]]:
    generation_root = root / "generation"; evidence_root = generation_root / "evidence"
    profiles_root = root / "profiles"; bolt_root = root / "bolt"; binpkg_root = root / "binpkgs"
    packages_dir = generation_root / "packages"; artifacts_dir = generation_root / "artifacts"
    for directory in (evidence_root, profiles_root, bolt_root, binpkg_root, packages_dir, artifacts_dir):
        directory.mkdir(parents=True, exist_ok=True)
    installed_root = root / "installed"; installed = installed_root / "usr/bin/example"
    installed.parent.mkdir(parents=True); installed.write_bytes(b"artifact"); installed.chmod(0o755)
    vdb_root = root / "vdb"; instance = vdb_root / "app-test/example-suite-1.0-r2"; instance.mkdir(parents=True)
    contents = "dir /usr/bin\nobj /usr/bin/example deadbeef 1783904400\n"
    (instance / "CONTENTS").write_text(contents)
    (instance / "repository").write_text("gentoo\n"); (instance / "SLOT").write_text("0/0\n")
    (instance / "BUILD_TIME").write_text("1783904400\n"); (instance / "COUNTER").write_text("17\n")
    (instance / "environment.bz2").write_bytes(b"environment")

    package = package_record(artifact_count=1, bolt_counts={"candidate_count": 0, "optimized_count": 0, "excluded_count": 0, "not_applicable_count": 1, "pending_count": 0, "unknown_count": 0, "failed_count": 0, "status": "not-applicable"})
    script_component = component("09-script-data", "script-data", ["data", "python", "shell"], "none", "not-applicable")
    package["components"] = [script_component]; package["abis"] = []; package["languages"] = script_component["languages"]
    package["aggregate"]["component_count"] = 1
    package["aggregate"]["pgo"] = {"eligible_count": 0, "optimized_count": 0, "excluded_count": 0, "not_applicable_count": 1, "pending_count": 0, "unknown_count": 0, "failed_count": 0, "status": "not-applicable"}
    package["graphs"]["workload_refs"] = []; package["final_status"] = "not-applicable"; package["resolution"] = resolution()
    package["source_rebuild"]["proof"]["active_modes"] = []
    package["source_rebuild"]["proof"]["portage_transaction_receipt"] = evidence("package-transaction", "transaction")
    package["source_rebuild"]["proof"]["binpkg_validation_receipt"] = evidence("binpkg-validation", "report")
    package["source_rebuild"]["proof"]["binpkg"]["production_marker"] = evidence("binpkg-production-marker", "manifest")
    package["live_instance"].update({
        "vdb_path": str(instance), "contents_sha256": hashlib.sha256(contents.encode()).hexdigest(),
        "metadata_tree_sha256": STATE.vdb_metadata_tree_sha256(instance), "slot_raw": "0/0",
        "environment_bz2_sha256": hashlib.sha256(b"environment").hexdigest(),
    })
    package["live_instance"]["identity_sha256"] = STATE.vdb_identity_sha256(package["live_instance"])
    package["source_rebuild"]["proof"]["installed_vdb_identity_sha256"] = package["live_instance"]["identity_sha256"]
    binpkg = binpkg_root / "example.gpkg.tar"; binpkg.write_bytes(b"binpkg")
    package["source_rebuild"]["proof"]["binpkg"].update({"path": str(binpkg), "sha256": hashlib.sha256(b"binpkg").hexdigest()})

    artifact = artifact_record(kind="data")
    artifact["owner"].update({"component_id": script_component["component_id"], "component_fingerprint": script_component["fingerprint"]})
    artifact["graphs"]["workload_refs"] = []
    installed_stat = installed.lstat()
    artifact.update({"content_sha256": hashlib.sha256(b"artifact").hexdigest(), "size": len(b"artifact")})
    artifact["metadata"].update({"mode": stat.S_IMODE(installed_stat.st_mode), "uid": installed_stat.st_uid, "gid": installed_stat.st_gid, "mtime_ns": installed_stat.st_mtime_ns, "xattrs": []})
    artifact["topology"].update({"device": installed_stat.st_dev, "inode": installed_stat.st_ino, "link_count": installed_stat.st_nlink})

    frozen_payload = package["frozen_inventory_entry"]["payload"]
    frozen_sha = hashlib.sha256(STATE.canonical_bytes(frozen_payload)).hexdigest()
    package["frozen_inventory_entry"]["entry_sha256"] = frozen_sha
    inventory = {
        "schema_version": 2, "record_type": "frozen-inventory", "generation_id": "generation-test", "inventory_id": "inventory-test",
        "packages": [{"cpv": package["identity"]["cpv"], "entry_sha256": frozen_sha}],
        "owned_paths": [{"owner_cpv": package["identity"]["cpv"], "path": "/usr/bin/example"}],
        "owned_directories": [{"owner_cpv": package["identity"]["cpv"], "path": "/usr/bin", "mode": stat.S_IMODE(installed.parent.stat().st_mode), "uid": installed.parent.stat().st_uid, "gid": installed.parent.stat().st_gid, "classification": "not-applicable", "resolution": resolution("not-machine-code")}],
    }
    materialize_evidence(inventory, evidence_root)
    inventory_payload = STATE.canonical_bytes(inventory); inventory_sha = hashlib.sha256(inventory_payload).hexdigest()
    package["generation"] = generation(inventory_sha); artifact["generation"] = generation(inventory_sha)
    inventory_path = generation_root / "inventory.json"; inventory_path.write_bytes(inventory_payload)

    materialize_evidence(package, evidence_root); materialize_evidence(artifact, evidence_root)
    build_log_evidence = package["source_rebuild"]["attempts"][0]["build_log"]
    transaction_id = package["source_rebuild"]["transaction_id"]
    build_marker = f"gentoo-optimization-transaction-v1\tgeneration=generation-test\tcpv=app-test/example-suite-1.0-r2\ttransaction={transaction_id}\tsource_only=true\tactive_modes=none\n".encode()
    Path(build_log_evidence["path"]).write_bytes(build_marker)
    build_log_evidence["sha256"] = hashlib.sha256(build_marker).hexdigest()
    marker_evidence = package["source_rebuild"]["proof"]["binpkg"]["production_marker"]
    marker_document = {"schema": "gentoo-optimization-binpkg-production-v1", "generation": generation(inventory_sha), "cpv": package["identity"]["cpv"], "transaction_id": transaction_id, "active_modes": [], "binpkg_sha256": package["source_rebuild"]["proof"]["binpkg"]["sha256"], "vdb_identity_sha256": package["live_instance"]["identity_sha256"]}
    marker_payload = STATE.canonical_bytes(marker_document); Path(marker_evidence["path"]).write_bytes(marker_payload); marker_evidence["sha256"] = hashlib.sha256(marker_payload).hexdigest()
    package_receipt_evidence = package["source_rebuild"]["proof"]["portage_transaction_receipt"]
    package_receipt = {
        "schema": "gentoo-optimization-source-rebuild-v1", "generation": generation(inventory_sha), "cpv": package["identity"]["cpv"], "transaction_id": transaction_id, "source_only": True,
        "emerge_argv": ["/usr/bin/emerge", "--usepkg=n", "--buildpkg=y", "=app-test/example-suite-1.0-r2"], "active_modes": [],
        "started_at": "2026-07-13T01:00:00Z", "completed_at": "2026-07-13T01:01:00Z",
        "pre_vdb": {"build_time": "1783904300", "counter": "16", "identity_sha256": H["vdb"]},
        "post_vdb": {"build_time": package["live_instance"]["build_time"], "counter": package["live_instance"]["counter"], "identity_sha256": package["live_instance"]["identity_sha256"]},
        "build_log": build_log_evidence, "profiles": [], "bolt_artifacts": [],
        "binpkg": {"path": package["source_rebuild"]["proof"]["binpkg"]["path"], "sha256": package["source_rebuild"]["proof"]["binpkg"]["sha256"], "format": "gpkg"},
    }
    package_receipt_payload = STATE.canonical_bytes(package_receipt); Path(package_receipt_evidence["path"]).write_bytes(package_receipt_payload); package_receipt_evidence["sha256"] = hashlib.sha256(package_receipt_payload).hexdigest()
    validators: dict[str, dict[str, str]] = {}
    validator_dir = root / "validators"; validator_dir.mkdir()
    for key in ("state_runtime", "reconciler_runtime", "profile", "readelf", "getcap", "uname", "efibootmgr", "rc_status"):
        path = validator_dir / key
        path.write_text("#!/bin/sh\nexit 0\n"); path.chmod(0o755)
        validators[key] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    binpkg_validator = validator_dir / "binpkg_snapshot"
    binpkg_validator.write_text("#!/bin/sh\nprintf '%s\\n' '{\"status\":\"pass\"}'\n"); binpkg_validator.chmod(0o755)
    validators["binpkg_snapshot"] = {"path": str(binpkg_validator), "sha256": hashlib.sha256(binpkg_validator.read_bytes()).hexdigest()}
    generation_identity = generation(inventory_sha)
    lock_payload = STATE.canonical_bytes(generation_identity)
    locks: dict[str, dict[str, str]] = {}
    for key in ("framework", "project", "generation"):
        path = generation_root / f"{key}.lock"
        payload = b"" if key == "framework" else lock_payload
        path.write_bytes(payload)
        locks[key] = {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest()}
    final_state: dict[str, Any] = {
        "schema_version": 1, "record_type": "final-system-state", "generation": generation_identity,
        "trusted_roots": {"generation_root": str(generation_root), "evidence_root": str(evidence_root), "profiles_root": str(profiles_root), "bolt_root": str(bolt_root), "binpkg_snapshot": str(binpkg_root), "packages_dir": str(packages_dir), "artifacts_dir": str(artifacts_dir), "inventory": str(inventory_path)},
        "locks": locks, "validators": validators,
        "registries": {"workloads": [], "dependency_edges": []},
        "final_transaction": {"transaction_id": "final-transaction", "completed_at": "2026-07-13T02:00:00Z", "active_modes": [], "portage_receipt": evidence("final-transaction", "transaction"), "vdb_receipt": evidence("validation", "report"), "binpkg_snapshot_receipt": evidence("binpkg-validation", "report")},
        "boot": {"boot_id": "fixture-boot", "kernel_release": "fixture-kernel", "boot_current": "0004", "efi_root": "/efi", "kernel_image": evidence("image", "binary"), "initramfs": evidence("boot", "binary"), "efi_loader": evidence("image", "binary"), "modules_manifest": evidence("modules", "manifest"), "efibootmgr_output_sha256": H["boot"], "openrc_output_sha256": H["validation"], "reboot_evidence": [evidence("boot", "report")]},
    }
    materialize_evidence(final_state["final_transaction"], evidence_root); materialize_evidence(final_state["boot"], evidence_root)
    final_receipt_evidence = final_state["final_transaction"]["portage_receipt"]
    final_receipt = {"schema": "gentoo-optimization-final-portage-transaction-v1", "generation": generation(inventory_sha), "transaction_id": "final-transaction", "completed_at": "2026-07-13T02:00:00Z", "packages": [{"cpv": package["identity"]["cpv"], "path": package_receipt_evidence["path"], "sha256": package_receipt_evidence["sha256"]}]}
    final_receipt_payload = STATE.canonical_bytes(final_receipt); Path(final_receipt_evidence["path"]).write_bytes(final_receipt_payload); final_receipt_evidence["sha256"] = hashlib.sha256(final_receipt_payload).hexdigest()
    binpkg_receipt_evidence = final_state["final_transaction"]["binpkg_snapshot_receipt"]
    binpkg_output = b'{"status":"pass"}\n'; Path(binpkg_receipt_evidence["path"]).write_bytes(binpkg_output); binpkg_receipt_evidence["sha256"] = hashlib.sha256(binpkg_output).hexdigest()
    package["source_rebuild"]["proof"]["binpkg_validation_receipt"] = copy.deepcopy(binpkg_receipt_evidence)
    # Lock evidence contains authoritative generation JSON and must not be rematerialized.
    (packages_dir / "package.json").write_bytes(STATE.canonical_bytes(package))
    (artifacts_dir / "artifact.json").write_bytes(STATE.canonical_bytes(artifact))
    return package, artifact, inventory, inventory_payload, final_state, {"vdb": vdb_root, "installed": installed_root, "packages": packages_dir, "artifacts": artifacts_dir, "inventory": inventory_path}


def mutate_live_magic(artifact: dict[str, Any], paths: dict[str, Path]) -> None:
    path = paths["installed"] / "usr/bin/example"
    payload = b"\x7fELFbad!"
    path.write_bytes(payload)
    os.utime(path, ns=(artifact["metadata"]["mtime_ns"], artifact["metadata"]["mtime_ns"]))
    artifact["content_sha256"] = hashlib.sha256(payload).hexdigest()


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
        record = package_record(); record["graphs"]["workload_refs"] = []
        with self.assertRaisesRegex(STATE.StateValidationError, "does not register component PGO"):
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
        record = artifact_record(); record["installed_path"] = "/usr/bin/example-link"; record["topology"]["symlinks"] = [{"path": "/usr/bin/example-link", "target": "example"}]
        self.assertEqual(STATE.validate_artifact(record)["installed_path"], "/usr/bin/example-link")
        record["topology"]["symlinks"][0]["target"] = "outside"
        with self.assertRaisesRegex(STATE.StateValidationError, "outside this artifact topology"):
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
        for field, empty in (("capture", None), ("perf_profiles", []), ("fdata", None), ("tools", None), ("options", []), ("command", None), ("output", None), ("deployment", None)):
            with self.subTest(field=field):
                record = artifact_record(); record["bolt"][field] = empty
                with self.assertRaises(STATE.StateValidationError):
                    STATE.validate_artifact(record)
        record = artifact_record(); record["bolt"]["deployment"]["metadata_verified"] = False
        with self.assertRaisesRegex(STATE.StateValidationError, "must be true"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["bolt"]["fdata"]["input_sample_count"] = 1199
        with self.assertRaisesRegex(STATE.StateValidationError, "perf input sample"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["bolt"]["deployment"]["installed_sha256"] = "0" * 64
        with self.assertRaisesRegex(STATE.StateValidationError, "final installed artifact hash"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["bolt"]["deployment"]["prestrip_deployed_sha256"] = "0" * 64
        with self.assertRaisesRegex(STATE.StateValidationError, "exact BOLT output hash"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["bolt"]["deployment"]["installed_sha256"] = H["output"]; record["content_sha256"] = H["output"]
        self.assertEqual(STATE.validate_artifact(record)["bolt"]["status"], "optimized")
        record = artifact_record(); record["bolt"]["deployment"]["post_strip_verification"] = []
        with self.assertRaisesRegex(STATE.StateValidationError, "must contain evidence"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["elf"]["runtime_instrumentation"]["bolt_note"] = False
        with self.assertRaisesRegex(STATE.StateValidationError, "must carry the installed note"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["graphs"]["workload_refs"] = []
        with self.assertRaisesRegex(STATE.StateValidationError, "does not register BOLT"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["bolt"]["options"][0], record["bolt"]["options"][1] = record["bolt"]["options"][1], record["bolt"]["options"][0]
        with self.assertRaisesRegex(STATE.StateValidationError, "ordered option policy"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["bolt"]["option_policy_revision"] = "unreviewed"
        with self.assertRaisesRegex(STATE.StateValidationError, "ordered option policy"):
            STATE.validate_artifact(record)
        record = artifact_record(); record["bolt"]["command"]["argv"][1] = "/wrong/input"
        with self.assertRaisesRegex(STATE.StateValidationError, "exact reviewed argv"):
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
        provisional = package_record()
        inventory = {"schema_version": 2, "record_type": "frozen-inventory", "generation_id": "generation-test", "inventory_id": "inventory-test", "packages": [{"cpv": "app-test/example-suite-1.0-r2", "entry_sha256": provisional["frozen_inventory_entry"]["entry_sha256"]}], "owned_paths": [{"owner_cpv": "app-test/example-suite-1.0-r2", "path": "/usr/bin/example"}], "owned_directories": []}
        payload = STATE.canonical_bytes(inventory); inv_sha = hashlib.sha256(payload).hexdigest()
        bolt_counts = {"candidate_count": 1, "optimized_count": 1, "excluded_count": 0, "not_applicable_count": 0, "pending_count": 0, "unknown_count": 0, "failed_count": 0, "status": "optimized"}
        package = package_record(inventory_sha=inv_sha, artifact_count=1, bolt_counts=bolt_counts)
        artifact = artifact_record(inventory_sha=inv_sha)
        return package, artifact, inventory, payload

    def test_exact_inventory_owner_component_and_aggregate_reconciliation(self) -> None:
        package, artifact, inventory, payload = self.collection()
        summary = STATE.reconcile_collection([package], [artifact], inventory=inventory, inventory_sha256=hashlib.sha256(payload).hexdigest())
        self.assertTrue(summary["inventory_verified"])
        self.assertFalse(summary["vdb_verified"])
        self.assertFalse(summary["installed_artifacts_verified"])
        self.assertFalse(summary["coverage_complete"])
        self.assertEqual(summary["counts"]["pending_total"], 0)
        self.assertEqual(summary["counts"]["unknown_total"], 0)
        self.assertEqual(summary["counts"]["failed_total"], 0)
        self.assertEqual(summary["counts"]["source_rebuild_succeeded_total"], 1)

    def test_unresolved_leaf_state_is_derived_and_blocks_completion(self) -> None:
        package, artifact, inventory, payload = self.collection()
        artifact["bolt"] = {"eligibility": "eligible", "status": "pending", "generation_id": None, "resolution": None, "capture": None, "perf_profiles": [], "fdata": None, "tools": None, "option_policy_revision": None, "options": [], "command": None, "output": None, "deployment": None}
        artifact["final_status"] = "pending"
        package["aggregate"]["bolt"].update({"optimized_count": 0, "pending_count": 1, "status": "pending"})
        package["source_rebuild"]["proof"]["active_modes"] = ["pgo-use"]
        package["final_status"] = "pending"
        summary = STATE.reconcile_collection([package], [artifact], inventory=inventory, inventory_sha256=hashlib.sha256(payload).hexdigest())
        self.assertEqual(summary["counts"]["pending_total"], 1)
        self.assertEqual(summary["counts"]["unknown_total"], 0)
        self.assertEqual(summary["counts"]["failed_total"], 0)
        self.assertFalse(summary["coverage_complete"])

    def test_inventory_cpv_owned_path_and_generation_must_be_exact(self) -> None:
        package, artifact, inventory, payload = self.collection()
        for mutation, message in (
            (lambda: inventory["packages"].append({"cpv": "app-test/extra-1", "entry_sha256": H["entry"]}), "exact CPV mismatch"),
            (lambda: inventory["owned_paths"][0].__setitem__("path", "/usr/bin/other"), "owned-path mismatch"),
            (lambda: (artifact["generation"].__setitem__("generation_id", "other"), artifact["bolt"].__setitem__("generation_id", "other")), "different generation"),
        ):
            with self.subTest(message=message):
                package, artifact, inventory, payload = self.collection(); mutation()
                with self.assertRaisesRegex(STATE.StateValidationError, message):
                    STATE.reconcile_collection([package], [artifact], inventory=inventory, inventory_sha256=hashlib.sha256(payload).hexdigest())
        package, artifact, inventory, payload = self.collection(); inventory["packages"][0]["entry_sha256"] = "0" * 64
        with self.assertRaisesRegex(STATE.StateValidationError, "entry hash mismatch"):
            STATE.reconcile_collection([package], [artifact], inventory=inventory, inventory_sha256=hashlib.sha256(payload).hexdigest())

    def test_owner_component_fingerprint_aggregate_and_topology_ambiguity_fail(self) -> None:
        package, artifact, inventory, payload = self.collection(); artifact["owner"]["component_fingerprint"] = f"sha256:{'0' * 64}"
        with self.assertRaisesRegex(STATE.StateValidationError, "fingerprint mismatch"):
            STATE.reconcile_collection([package], [artifact])
        package, artifact, inventory, payload = self.collection(); package["aggregate"]["artifact_count"] = 2; package["aggregate"]["bolt"]["not_applicable_count"] = 1
        with self.assertRaisesRegex(STATE.StateValidationError, "artifact_count"):
            STATE.reconcile_collection([package], [artifact])
        package, artifact, inventory, payload = self.collection(); second = copy.deepcopy(artifact); second["artifact_id"] = f"sha256:{'f' * 64}"; second["topology"]["inode"] = 1002
        with self.assertRaisesRegex(STATE.StateValidationError, "ambiguous path"):
            STATE.reconcile_collection([package], [artifact, second])
        package, artifact, inventory, payload = self.collection(); second = copy.deepcopy(artifact); second["artifact_id"] = f"sha256:{'f' * 64}"; second["installed_path"] = "/usr/bin/second"; second["canonical_path"] = "/usr/bin/second"; second["topology"]["hardlink_paths"] = ["/usr/bin/second"]
        with self.assertRaisesRegex(STATE.StateValidationError, "split inode"):
            STATE.reconcile_collection([package], [artifact, second])

    def test_graph_references_must_resolve(self) -> None:
        package, artifact, _inventory, _payload = self.collection()
        package["graphs"]["consumer_refs"] = [{"cpv": "app-test/missing-1", "component_id": None, "evidence": [evidence("graph", "report")]}]
        with self.assertRaisesRegex(STATE.StateValidationError, "absent CPV"):
            STATE.reconcile_collection([package], [artifact])

    def test_live_vdb_cpv_contents_owner_and_symlink_types_are_exact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="state-vdb.") as temporary:
            root = Path(temporary); vdb_root = root / "vdb"; instance = vdb_root / "app-test/example-suite-1.0-r2"; instance.mkdir(parents=True)
            installed_root = root / "installed"; installed_file = installed_root / "usr/bin/example"; installed_file.parent.mkdir(parents=True); installed_file.write_bytes(b"installed"); installed_file.chmod(0o755)
            os.setxattr(installed_file, "user.test", b"xattr")
            contents = "obj /usr/bin/example deadbeef 1783904400\n"
            (instance / "CONTENTS").write_text(contents, encoding="utf-8")
            (instance / "repository").write_text("gentoo\n", encoding="utf-8")
            (instance / "SLOT").write_text("0\n", encoding="utf-8")
            (instance / "BUILD_TIME").write_text("1783904400\n", encoding="utf-8")
            (instance / "COUNTER").write_text("17\n", encoding="utf-8")
            (instance / "environment.bz2").write_bytes(b"environment")
            package, artifact, inventory, payload = self.collection()
            package["live_instance"].update({"vdb_path": str(instance), "contents_sha256": hashlib.sha256(contents.encode()).hexdigest(), "metadata_tree_sha256": STATE.vdb_metadata_tree_sha256(instance), "environment_bz2_sha256": hashlib.sha256(b"environment").hexdigest()})
            package["live_instance"]["identity_sha256"] = STATE.vdb_identity_sha256(package["live_instance"])
            package["source_rebuild"]["proof"]["installed_vdb_identity_sha256"] = package["live_instance"]["identity_sha256"]
            installed_stat = installed_file.lstat()
            artifact["size"] = installed_stat.st_size
            artifact["metadata"].update({"mode": stat.S_IMODE(installed_stat.st_mode), "uid": installed_stat.st_uid, "gid": installed_stat.st_gid, "mtime_ns": installed_stat.st_mtime_ns})
            artifact["topology"].update({"device": installed_stat.st_dev, "inode": installed_stat.st_ino, "link_count": installed_stat.st_nlink})
            summary = STATE.reconcile_collection([package], [artifact], inventory=inventory, inventory_sha256=hashlib.sha256(payload).hexdigest(), vdb_root=vdb_root, installed_root=installed_root)
            self.assertFalse(summary["coverage_complete"])
            self.assertTrue(summary["vdb_verified"])
            self.assertTrue(summary["installed_artifacts_verified"])
            packages_dir = root / "records/packages"; artifacts_dir = root / "records/artifacts"; packages_dir.mkdir(parents=True); artifacts_dir.mkdir(parents=True)
            inventory_path = root / "inventory.json"; inventory_path.write_bytes(payload)
            (packages_dir / "package.json").write_bytes(STATE.canonical_bytes(package)); (artifacts_dir / "artifact.json").write_bytes(STATE.canonical_bytes(artifact))
            report = root / "report.json"
            cli = subprocess.run([sys.executable, str(RECONCILE_PATH), "--packages-dir", str(packages_dir), "--artifacts-dir", str(artifacts_dir), "--inventory", str(inventory_path), "--vdb-root", str(vdb_root), "--installed-root", str(installed_root), "--output", str(report)], text=True, capture_output=True, check=False)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            installed_file.write_bytes(b"tamperedd"); os.utime(installed_file, ns=(installed_stat.st_atime_ns, installed_stat.st_mtime_ns))
            with self.assertRaisesRegex(STATE.StateValidationError, "live content hash mismatch"):
                STATE.reconcile_collection([package], [artifact], inventory=inventory, inventory_sha256=hashlib.sha256(payload).hexdigest(), vdb_root=vdb_root, installed_root=installed_root)
            installed_file.write_bytes(b"installed"); os.utime(installed_file, ns=(installed_stat.st_atime_ns, installed_stat.st_mtime_ns))
            (instance / "CONTENTS").write_text("sym /usr/bin/example -> example.real 1783904400\n", encoding="utf-8")
            package["live_instance"]["contents_sha256"] = hashlib.sha256((instance / "CONTENTS").read_bytes()).hexdigest()
            package["live_instance"]["metadata_tree_sha256"] = STATE.vdb_metadata_tree_sha256(instance)
            package["live_instance"]["identity_sha256"] = STATE.vdb_identity_sha256(package["live_instance"])
            package["source_rebuild"]["proof"]["installed_vdb_identity_sha256"] = package["live_instance"]["identity_sha256"]
            with self.assertRaisesRegex(STATE.StateValidationError, "topology type mismatch"):
                STATE.reconcile_collection([package], [artifact], inventory=inventory, inventory_sha256=hashlib.sha256(payload).hexdigest(), vdb_root=vdb_root, installed_root=installed_root)

    def test_cli_atomically_publishes_summary_and_require_complete(self) -> None:
        package, artifact, inventory, payload = self.collection()
        with tempfile.TemporaryDirectory(prefix="state-cli.") as temporary:
            root = Path(temporary); packages = root / "packages"; artifacts = root / "artifacts"; packages.mkdir(); artifacts.mkdir()
            inventory_path = root / "inventory.json"; inventory_path.write_bytes(payload)
            # The canonical payload hash was already embedded in these records.
            (packages / "package.json").write_bytes(STATE.canonical_bytes(package)); (artifacts / "artifact.json").write_bytes(STATE.canonical_bytes(artifact))
            output = root / "report/reconciliation.json"
            result = subprocess.run([sys.executable, str(RECONCILE_PATH), "--packages-dir", str(packages), "--artifacts-dir", str(artifacts), "--inventory", str(inventory_path), "--output", str(output)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(output.read_text())["coverage_complete"])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertFalse(list(output.parent.glob("*.partial")))
            strict = subprocess.run([sys.executable, str(RECONCILE_PATH), "--packages-dir", str(packages), "--artifacts-dir", str(artifacts), "--inventory", str(inventory_path), "--output", str(output), "--require-complete"], text=True, capture_output=True, check=False)
            self.assertEqual(strict.returncode, 2)
            self.assertIn("requires --final-system-state", strict.stderr)

    def test_strict_fixture_reopens_every_proof_but_can_never_complete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="state-strict.") as temporary:
            root = Path(temporary)
            package, artifact, inventory, payload, final_state, paths = strict_fixture(root)
            summary = STATE.reconcile_collection(
                [package], [artifact], inventory=inventory,
                inventory_sha256=hashlib.sha256(payload).hexdigest(),
                vdb_root=paths["vdb"], installed_root=paths["installed"],
                final_system_state=final_state, packages_dir=paths["packages"],
                artifacts_dir=paths["artifacts"], inventory_path=paths["inventory"],
                strict=True, fixture_mode=True,
            )
            self.assertTrue(summary["strict_verified"])
            self.assertFalse(summary["authoritative_verified"])
            self.assertFalse(summary["coverage_complete"])

    def test_strict_fixture_rejects_missing_tampered_and_symlinked_proof(self) -> None:
        for mutation, message in (
            ("missing", "unavailable"), ("tampered", "hash mismatch"), ("symlink", "symlink"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="state-proof.") as temporary:
                root = Path(temporary)
                package, artifact, inventory, payload, final_state, paths = strict_fixture(root)
                proof = Path(package["source"]["ebuild"]["path"])
                if mutation == "missing":
                    proof.unlink()
                elif mutation == "tampered":
                    proof.write_bytes(b"tampered")
                else:
                    target = root / "target"; target.write_bytes(proof.read_bytes()); proof.unlink(); proof.symlink_to(target)
                with self.assertRaisesRegex(STATE.StateValidationError, message):
                    STATE.reconcile_collection(
                        [package], [artifact], inventory=inventory,
                        inventory_sha256=hashlib.sha256(payload).hexdigest(),
                        vdb_root=paths["vdb"], installed_root=paths["installed"],
                        final_system_state=final_state, packages_dir=paths["packages"],
                        artifacts_dir=paths["artifacts"], inventory_path=paths["inventory"],
                        strict=True, fixture_mode=True,
                    )

    def test_strict_fixture_rejects_magic_terminal_graph_directory_and_validator_lies(self) -> None:
        mutations: tuple[tuple[str, Callable[[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Path]], Any], str], ...] = (
            ("magic", lambda p, a, i, f, paths: mutate_live_magic(a, paths), "ELF"),
            ("terminal", lambda p, a, i, f, paths: a["resolution"].__setitem__("reason_code", "firmware-not-rebuildable"), "contradicts"),
            ("graph", lambda p, a, i, f, paths: f["registries"]["dependency_edges"].append({"consumer_cpv": p["identity"]["cpv"], "consumer_component_id": p["components"][0]["component_id"], "provider_cpv": p["identity"]["cpv"], "provider_component_id": p["components"][0]["component_id"], "evidence": [p["source"]["ebuild"]]}), "graph"),
            ("directory", lambda p, a, i, f, paths: (paths["installed"] / "usr/bin").chmod(0o700), "metadata differs"),
            ("validator", lambda p, a, i, f, paths: Path(f["validators"]["binpkg_snapshot"]["path"]).write_text("#!/bin/sh\nexit 1\n"), "hash mismatch"),
        )
        for name, mutation, message in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix="state-negative.") as temporary:
                root = Path(temporary)
                package, artifact, inventory, payload, final_state, paths = strict_fixture(root)
                mutation(package, artifact, inventory, final_state, paths)
                with self.assertRaisesRegex(STATE.StateValidationError, message):
                    STATE.reconcile_collection(
                        [package], [artifact], inventory=inventory,
                        inventory_sha256=hashlib.sha256(payload).hexdigest(),
                        vdb_root=paths["vdb"], installed_root=paths["installed"],
                        final_system_state=final_state, packages_dir=paths["packages"],
                        artifacts_dir=paths["artifacts"], inventory_path=paths["inventory"],
                        strict=True, fixture_mode=True,
                    )

    def test_raw_slot_preserves_equal_subslot_and_named_forms(self) -> None:
        for raw, expected in (("0", ("0", "0")), ("0/0", ("0", "0")), ("3.0/3", ("3.0", "3")), ("llvm/22", ("llvm", "22"))):
            with self.subTest(raw=raw):
                self.assertEqual(STATE.parse_slot(raw), expected)
        for raw in ("", "/0", "0/", "0/0/1", " 0"):
            with self.subTest(invalid=raw), self.assertRaises(STATE.StateValidationError):
                STATE.parse_slot(raw)

    def test_cli_inventory_authority_and_fixture_completion_guard(self) -> None:
        with tempfile.TemporaryDirectory(prefix="state-inventory-cli.") as temporary:
            root = Path(temporary)
            _package, _artifact, _inventory, payload, _final, paths = strict_fixture(root)
            result = subprocess.run([sys.executable, str(RECONCILE_PATH), "--validate-inventory-only", "--inventory", str(paths["inventory"]), "--fixture-roots"], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["inventory_sha256"], hashlib.sha256(payload).hexdigest())
            guarded = subprocess.run([sys.executable, str(RECONCILE_PATH), "--packages-dir", str(paths["packages"]), "--artifacts-dir", str(paths["artifacts"]), "--inventory", str(paths["inventory"]), "--output", str(root / "report"), "--require-complete", "--fixture-roots"], text=True, capture_output=True, check=False)
            self.assertEqual(guarded.returncode, 2)
            self.assertIn("can never", guarded.stderr)


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
        final_schema = json.loads((REPOSITORY_ROOT / "optimization/schema/final-system-state.schema.json").read_text())
        self.assertEqual(package_schema["properties"]["schema_version"]["const"], 4)
        self.assertEqual(artifact_schema["properties"]["schema_version"]["const"], 4)
        self.assertEqual(set(package_schema["required"]), STATE.PACKAGE_KEYS)
        self.assertEqual(set(artifact_schema["required"]), STATE.ARTIFACT_KEYS)
        self.assertEqual(set(final_schema["required"]), STATE.FINAL_SYSTEM_KEYS)

    def test_draft_202012_schema_parity_positive_and_negative(self) -> None:
        try:
            import jsonschema  # type: ignore[import-untyped]
        except ImportError:
            self.skipTest("jsonschema unavailable: Draft 2020-12 parity cannot run")
        with tempfile.TemporaryDirectory(prefix="state-schema-final.") as temporary:
            _package, _artifact, _inventory, _payload, final_state, _paths = strict_fixture(Path(temporary))
            pairs = (("package-state.schema.json", package_record(), STATE.validate_package), ("artifact-state.schema.json", artifact_record(), STATE.validate_artifact), ("final-system-state.schema.json", final_state, STATE.validate_final_system_state))
            for filename, record, validator in pairs:
                with self.subTest(filename=filename):
                    schema = json.loads((REPOSITORY_ROOT / "optimization/schema" / filename).read_text())
                    jsonschema.Draft202012Validator.check_schema(schema)
                    jsonschema.Draft202012Validator(schema).validate(record)
                    validator(record)
                    invalid = copy.deepcopy(record); invalid["schema_version"] = 99
                    with self.assertRaises(jsonschema.ValidationError):
                        jsonschema.Draft202012Validator(schema).validate(invalid)
                    with self.assertRaises(STATE.StateValidationError):
                        validator(invalid)


if __name__ == "__main__":
    unittest.main()

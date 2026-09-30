#!/usr/bin/env python3
"""Focused regressions for de-instrumentation terminal-state accounting."""
from __future__ import annotations

import importlib.util
import bz2
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

class DeinstrumentationStateTests(unittest.TestCase):
    def test_scanner_decodes_portage_declarations_and_prebuilt_marker(self):
        module = load("scan_live_instrumentation", ROOT / "scripts/optimization/pgo/scan-live-instrumentation.py")
        with tempfile.TemporaryDirectory() as tmp:
            vdb = Path(tmp) / "app" / "demo-1"
            vdb.mkdir(parents=True)
            (vdb / "environment.bz2").write_bytes(bz2.compress(
                b'declare -x GENTOO_OPT_MODE="gcc-generate"\n'
                b'declare -x GENTOO_OPT_PROFILE_PATH="/var/tmp/p"\n'
                b'declare -- QA_PREBUILT="*"\n'
            ))
            values = module.decode_environment(Path(tmp), "app/demo-1")
            self.assertEqual(values["GENTOO_OPT_MODE"], "gcc-generate")
            self.assertEqual(values["GENTOO_OPT_PROFILE_PATH"], "/var/tmp/p")
            self.assertEqual(values["QA_PREBUILT"], "present")

    def test_planner_partitions_terminal_prebuilt_and_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            census = tmp / "census.json"
            plan = tmp / "plan.json"
            census.write_text(json.dumps({"records": [
                {"owner_cpv": "vendor/a-1", "instrumentation_markers": ["prf"],
                 "terminal_disposition": "unsupported-by-upstream-toolchain/prebuilt"},
                {"owner_cpv": "app/a-1", "instrumentation_markers": ["prf"]},
                {"owner_cpv": "app/b-1", "instrumentation_markers": ["prf"], "error": "readelf"},
                {"owner_cpv": "app/c-1", "instrumentation_markers": [], "error": "missing staged artifact"},
            ]}))
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/optimization/pgo/plan-deinstrumentation.py"),
                "--census", str(census), "--output", str(plan),
            ], check=True, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            data = json.loads(plan.read_text())
            self.assertEqual(data["batches"][0]["cpvs"], ["app/a-1"])
            self.assertEqual(data["accounting"]["terminal_retained_prebuilt_cpvs"], ["vendor/a-1"])
            self.assertEqual(data["accounting"]["inspection_failed_cpvs"], ["app/b-1", "app/c-1"])

    def test_scanner_honors_authenticated_kernel_policy_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            census = tmp / "census.json"
            policy = tmp / "policy.json"
            output = tmp / "scan.json"
            census.write_text(json.dumps({"artifacts": [{
                "owner_cpv": "sys-kernel/linux-firmware-1",
                "path": "/bin/false", "kind": "regular",
                "elf": {"class": 2, "type": 2, "machine": 62},
            }]}))
            policy.write_text(json.dumps({"records": [{
                "cpv": "sys-kernel/linux-firmware-1",
                "decision": "kernel-policy-exclusion",
            }]}))
            subprocess.run([
                sys.executable, str(ROOT / "scripts/optimization/pgo/scan-live-instrumentation.py"),
                "--census", str(census), "--output", str(output),
                "--mutation-policy", str(policy), "--vdb", str(tmp / "vdb"),
            ], check=True)
            data = json.loads(output.read_text())
            self.assertEqual(data["records"], [])

    def test_terminal_prebuilt_is_acceptable_clean_state(self):
        module = load("clear_deinstrumentation", ROOT / "scripts/optimization/pgo/clear-deinstrumentation.py")
        self.assertTrue(module.terminal_clean({
            "instrumentation_markers": ["prf"],
            "terminal_disposition": "unsupported-by-upstream-toolchain/prebuilt",
        }))
        self.assertFalse(module.terminal_clean({"instrumentation_markers": ["prf"]}))

    def test_abseil_deinstrumentation_retains_abi_vtables(self):
        bashrc = (ROOT / "portage/bashrc").read_text()
        self.assertIn("${CATEGORY-}/${PF-} == dev-cpp/abseil-cpp-*", bashrc)
        self.assertIn("gentoo_opt_append_flag_once CXXFLAGS -fforce-emit-vtables", bashrc)

    def test_extended_marker_remains_armed(self):
        source = (ROOT / "scripts/optimization/pgo/extend-deinstrumentation.py").read_text()
        self.assertIn('"state": "armed"', source)

    def test_extension_requires_exact_predecessor_batch_identity(self):
        module = load("extend_deinstrumentation", ROOT / "scripts/optimization/pgo/extend-deinstrumentation.py")
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.json"
            plan.write_text("{}\n")
            marker = {"batch_id": 4, "cpvs": ["app/a-1"]}
            receipt = {
                "schema": "deinstrumentation-batch-receipt-v1",
                "batch_id": 5,
                "cpvs": ["app/a-1"],
                "packages": [{"cpv": "app/a-1"}],
                "plan": {"path": str(plan), "sha256": module.digest(plan)},
            }
            with self.assertRaises(ValueError):
                module.validate_predecessor_receipt(receipt, marker, plan)

    def test_marker_arm_identity_ignores_creation_timestamp(self):
        module = load("arm_deinstrumentation", ROOT / "scripts/optimization/pgo/arm-deinstrumentation.py")
        first = {"schema": "deinstrument-pending-v1", "state": "armed", "batch_id": 1,
                 "cpvs": ["app/a-1"], "created_epoch": 1.0}
        retry = dict(first, created_epoch=2.0)
        self.assertEqual(module.marker_identity(first), module.marker_identity(retry))

    def test_reconciliation_residuals_are_subset_and_terminal_states_drop_out(self):
        module = load("reconcile_deinstrumentation", ROOT / "scripts/optimization/pgo/reconcile-deinstrumentation.py")
        scan = {"records": [
            {"owner_cpv": "app/a-1", "instrumentation_markers": ["prf"]},
            {"owner_cpv": "vendor/prebuilt-1", "instrumentation_markers": ["prf"],
             "terminal_disposition": "unsupported-by-upstream-toolchain/prebuilt"},
            {"owner_cpv": "kernel/fw-1", "error": "readelf", "terminal_disposition": "kernel-policy-exclusion"},
        ]}
        self.assertEqual(module.residual_cpvs(scan, {"app/a-1", "vendor/prebuilt-1", "kernel/fw-1"}), {"app/a-1"})
        with self.assertRaises(ValueError):
            module.residual_cpvs({"records": [{"owner_cpv": "new/pkg-1", "instrumentation_markers": ["prf"]}]}, {"app/a-1"})

    def test_undefined_gcov_runtime_reference_is_not_instrumentation(self):
        module = load("instrumentation", ROOT / "scripts/optimization/lib/instrumentation.py")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lib.so"
            path.write_bytes(b"\x7fELF")
            class Result:
                returncode = 0
                stdout = b" 1: 0 FUNC GLOBAL DEFAULT 1 foo\n 2: 0 NOTYPE WEAK DEFAULT UND __gcov_flush\n"
                stderr = b""
            original = module.subprocess.run
            module.subprocess.run = lambda *args, **kwargs: Result()
            try:
                self.assertEqual(module.inspect_elf(path), (False, "elf"))
            finally:
                module.subprocess.run = original

if __name__ == "__main__":
    unittest.main()

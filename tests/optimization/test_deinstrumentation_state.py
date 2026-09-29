#!/usr/bin/env python3
"""Focused regressions for de-instrumentation terminal-state accounting."""
from __future__ import annotations

import importlib.util
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
            ]}))
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/optimization/pgo/plan-deinstrumentation.py"),
                "--census", str(census), "--output", str(plan),
            ], check=True, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            data = json.loads(plan.read_text())
            self.assertEqual(data["batches"][0]["cpvs"], ["app/a-1"])
            self.assertEqual(data["accounting"]["terminal_retained_prebuilt_cpvs"], ["vendor/a-1"])
            self.assertEqual(data["accounting"]["inspection_failed_cpvs"], ["app/b-1"])

    def test_terminal_prebuilt_is_acceptable_clean_state(self):
        module = load("clear_deinstrumentation", ROOT / "scripts/optimization/pgo/clear-deinstrumentation.py")
        self.assertTrue(module.terminal_clean({
            "instrumentation_markers": ["prf"],
            "terminal_disposition": "unsupported-by-upstream-toolchain/prebuilt",
        }))
        self.assertFalse(module.terminal_clean({"instrumentation_markers": ["prf"]}))

if __name__ == "__main__":
    unittest.main()

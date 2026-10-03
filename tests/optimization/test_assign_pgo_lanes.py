import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/inventory/assign-pgo-lanes.py"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ReviewedLaneOverrideTests(unittest.TestCase):
    def test_override_is_explicit_policy_input(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            state = {"records": [{"cpv": "cat/pkg-1", "state": "pending-pgo-classification", "reason_code": "pending"}]}
            state["sha256"] = digest(state)
            (p / "state.json").write_text(json.dumps(state))
            (p / "backends.json").write_text(json.dumps({"packages": [{"cpv": "cat/pkg-1", "backend_evidence": []}]}))
            (p / "overrides.json").write_text(json.dumps({"overrides": [{"cpv": "cat/pkg-1", "lane": "pgo-clang-ir", "reason_code": "reviewed-generation-policy"}]}))
            output = p / "lanes.json"
            subprocess.run(["python3", str(SCRIPT), "--states", str(p / "state.json"), "--backends", str(p / "backends.json"), "--overrides", str(p / "overrides.json"), "--output", str(output)], check=True)
            row = json.loads(output.read_text())["packages"][0]
            self.assertEqual(row["lane"], "pgo-clang-ir")
            second = subprocess.run(["python3", str(SCRIPT), "--states", str(p / "state.json"), "--backends", str(p / "backends.json"), "--output", str(output)], capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("lane output already exists", second.stdout + second.stderr)
            self.assertEqual(row["decision_source"], "reviewed-generation-override")

    def test_prebuilt_evidence_cannot_receive_compilable_lane(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            state = {"records": [{"cpv": "cat/prebuilt-1", "state": "pending-pgo-classification", "reason_code": "pending"}]}
            state["sha256"] = digest(state)
            (p / "state.json").write_text(json.dumps(state))
            (p / "backends.json").write_text(json.dumps({"packages": [{
                "cpv": "cat/prebuilt-1", "backend_evidence": ["cmake"], "qa_prebuilt": True
            }]}))
            output = p / "lanes.json"
            subprocess.run(["python3", str(SCRIPT), "--states", str(p / "state.json"), "--backends", str(p / "backends.json"), "--output", str(output)], check=True)
            row = json.loads(output.read_text())["packages"][0]
            self.assertEqual(row["lane"], "unsupported-by-upstream-toolchain")
            self.assertEqual(row["reason_code"], "prebuilt-artifact-no-compile-evidence")

    def test_managed_eclass_does_not_hide_native_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            state = {"records": [{"cpv": "dev-libs/native-python-1", "state": "pending-pgo-classification", "reason_code": "pending"}]}
            state["sha256"] = digest(state)
            (p / "state.json").write_text(json.dumps(state))
            (p / "backends.json").write_text(json.dumps({"packages": [{
                "cpv": "dev-libs/native-python-1", "inherits": ["distutils-r1", "cmake"],
                "backend_evidence": ["cmake"], "artifact_language_evidence": {"c": 2, "elf-shared": 1}
            }]}))
            output = p / "lanes.json"
            subprocess.run(["python3", str(SCRIPT), "--states", str(p / "state.json"), "--backends", str(p / "backends.json"), "--output", str(output)], check=True)
            row = json.loads(output.read_text())["packages"][0]
            self.assertEqual(row["lane"], "pgo-clang-ir")

    def test_rust_toolchain_setup_and_rs_artifacts_do_not_select_rust(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            state = {"records": [{"cpv": "dev-libs/native-1", "state": "pending-pgo-classification", "reason_code": "pending"}]}
            state["sha256"] = digest(state)
            (p / "state.json").write_text(json.dumps(state))
            (p / "backends.json").write_text(json.dumps({"packages": [{
                "cpv": "dev-libs/native-1", "inherits": ["rust-toolchain", "meson"],
                "backend_evidence": [], "artifact_language_evidence": {"rust": 4, "elf-shared": 1}
            }]}))
            output = p / "lanes.json"
            subprocess.run(["python3", str(SCRIPT), "--states", str(p / "state.json"), "--backends", str(p / "backends.json"), "--output", str(output)], check=True)
            row = json.loads(output.read_text())["packages"][0]
            self.assertEqual(row["lane"], "pgo-clang-ir")

    def test_rust_bin_qa_prebuilt_wins_over_toolchain_setup(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            state = {"records": [{"cpv": "dev-lang/rust-bin-1", "state": "pending-pgo-classification", "reason_code": "pending"}]}
            state["sha256"] = digest(state)
            (p / "state.json").write_text(json.dumps(state))
            (p / "backends.json").write_text(json.dumps({"packages": [{
                "cpv": "dev-lang/rust-bin-1", "inherits": ["rust-toolchain"],
                "qa_prebuilt": True, "backend_evidence": []
            }]}))
            output = p / "lanes.json"
            subprocess.run(["python3", str(SCRIPT), "--states", str(p / "state.json"), "--backends", str(p / "backends.json"), "--output", str(output)], check=True)
            row = json.loads(output.read_text())["packages"][0]
            self.assertEqual(row["lane"], "unsupported-by-upstream-toolchain")


if __name__ == "__main__":
    unittest.main()

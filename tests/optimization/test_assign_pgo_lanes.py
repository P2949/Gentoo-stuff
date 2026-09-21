import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/inventory/assign-pgo-lanes.py"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main():
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
        assert row["lane"] == "pgo-clang-ir"
        assert row["decision_source"] == "reviewed-generation-override"
    print("PASS: lane exceptions require reviewed policy input")


if __name__ == "__main__":
    main()

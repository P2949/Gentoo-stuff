import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/inventory/classify-package-state.py"


def run_case(manifest, output, vdb, census, policy):
    return subprocess.run([
        "python3", str(SCRIPT), "--manifest", str(manifest),
        "--census", str(census), "--mutation-policy", str(policy),
        "--output", str(output), "--vdb", str(vdb),
    ], text=True, capture_output=True)


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        vdb = root / "vdb" / "app" / "a-1"
        vdb.mkdir(parents=True)
        (vdb / "CATEGORY").write_text("app\n")
        (vdb / "PN").write_text("a\n")
        manifest = root / "manifest.json"
        census = root / "census.json"
        policy = root / "policy.json"
        output = root / "state.json"
        census.write_text(json.dumps({"artifacts": []}))
        unsigned = {"record_type": "package-mutation-policy", "schema_version": 1,
                    "records": [{"cpv": "app/a-1", "decision": "userspace", "triggers": [], "evidence": []}]}
        import hashlib
        unsigned["sha256"] = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        policy.write_text(json.dumps(unsigned))

        manifest.write_text(json.dumps({"packages": [{"cpv": "app/a-1"}, {"cpv": "app/a-1"}]}))
        result = run_case(manifest, output, root / "vdb", census, policy)
        assert result.returncode != 0 and "duplicate or missing CPV" in result.stderr

        manifest.write_text(json.dumps({"packages": [{"cpv": "app/a-1"}]}))
        assert run_case(manifest, output, root / "vdb", census, policy).returncode == 0
        second = run_case(manifest, output, root / "vdb", census, policy)
        assert second.returncode != 0 and "already exists" in second.stderr
    print("PASS: package-state authority rejects duplicate CPVs and overwrite")


if __name__ == "__main__":
    main()

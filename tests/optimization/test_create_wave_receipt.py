import json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/create-wave-receipt.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); files = []
        for name, data in [("wave", {"packages": [{"cpv": "app/a-1"}, {"cpv": "app/a-1"}]}), ("readiness", {}), ("bindings", {}), ("workloads", {})]:
            p = root / f"{name}.json"; p.write_text(json.dumps(data)); files.append(p)
        out = root / "receipt.json"
        cmd = ["python3", str(SCRIPT), "--wave", str(files[0]), "--readiness", str(files[1]), "--bindings", str(files[2]), "--workloads", str(files[3]), "--output", str(out)]
        r = subprocess.run(cmd, text=True, capture_output=True)
        assert r.returncode != 0 and "duplicate" in r.stderr
        files[0].write_text(json.dumps({"packages": [{"cpv": "app/a-1"}]}))
        assert subprocess.run(cmd, check=False).returncode == 0
        r = subprocess.run(cmd, text=True, capture_output=True)
        assert r.returncode != 0 and "already exists" in r.stderr
    print("PASS: wave receipt rejects duplicate identities and overwrite")

if __name__ == "__main__": main()

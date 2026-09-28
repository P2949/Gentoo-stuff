import json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/materialize-fingerprints.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); inputs = root / "inputs.json"; outroot = root / "materialized"; result = root / "result.json"
        inputs.write_text(json.dumps({"records": [{"cpv": "app/a-1"}, {"cpv": "app/a-1"}]}))
        r = subprocess.run(["python3", str(SCRIPT), "--inputs", str(inputs), "--output-root", str(outroot), "--result", str(result)], text=True, capture_output=True)
        assert r.returncode != 0 and "duplicate" in r.stderr
        inputs.write_text(json.dumps({"records": []}))
        assert subprocess.run(["python3", str(SCRIPT), "--inputs", str(inputs), "--output-root", str(outroot), "--result", str(result)], check=False).returncode == 0
        r = subprocess.run(["python3", str(SCRIPT), "--inputs", str(inputs), "--output-root", str(outroot), "--result", str(root / "second.json")], text=True, capture_output=True)
        assert r.returncode != 0 and "output root already exists" in r.stderr
    print("PASS: fingerprint materialization rejects duplicate identities and overwrite")

if __name__ == "__main__": main()

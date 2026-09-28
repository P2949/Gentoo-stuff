import json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/classify-no-entrypoint.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); w = root / "w.json"; e = root / "e.json"; out = root / "o.json"
        w.write_text(json.dumps({"packages": [{"cpv": "app/a-1", "state": "no-runnable-entrypoint"}, {"cpv": "app/a-1", "state": "no-runnable-entrypoint"}]}))
        e.write_text(json.dumps({"artifacts": []}))
        cmd = ["python3", str(SCRIPT), "--workloads", str(w), "--elf", str(e), "--output", str(out)]
        r = subprocess.run(cmd, text=True, capture_output=True)
        assert r.returncode != 0 and "duplicate" in r.stderr
        w.write_text(json.dumps({"packages": []}))
        assert subprocess.run(cmd, check=False).returncode == 0
        r = subprocess.run(cmd, text=True, capture_output=True)
        assert r.returncode != 0 and "already exists" in r.stderr
    print("PASS: workload exclusion classifier rejects duplicate identities and overwrite")

if __name__ == "__main__": main()

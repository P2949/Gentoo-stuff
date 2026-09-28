import json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/inventory/scan-owned-artifacts.py"

def invoke(vdb, output):
    return subprocess.run(["python3", str(SCRIPT), "--vdb", str(vdb), "--output", str(output)], text=True, capture_output=True)

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); vdb = root / "vdb"; pkg = vdb / "app" / "a-1"; pkg.mkdir(parents=True)
        (pkg / "CONTENTS").write_text("obj /tmp/a 1\nobj /tmp/a 1\n")
        out = root / "census.json"
        result = invoke(vdb, out)
        assert result.returncode != 0 and "duplicate owned artifact identity" in result.stderr
        (pkg / "CONTENTS").write_text("obj /tmp/a 1\n")
        assert invoke(vdb, out).returncode == 0
        result = invoke(vdb, out)
        assert result.returncode != 0 and "already exists" in result.stderr
    print("PASS: owned-artifact census rejects duplicate identities and overwrite")

if __name__ == "__main__": main()

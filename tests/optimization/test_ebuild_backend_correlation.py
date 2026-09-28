import json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/inventory/correlate-ebuild-backends.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); manifest = root / "manifest.json"; output = root / "out.json"
        manifest.write_text(json.dumps({"cpvs": ["app/a-1", "app/a-1"]}))
        r = subprocess.run(["python3", str(SCRIPT), "--manifest", str(manifest), "--output", str(output)], text=True, capture_output=True)
        assert r.returncode != 0 and "duplicate" in r.stderr
        manifest.write_text(json.dumps({"cpvs": ["app/a-1"]}))
        # Missing VDB source is a valid correlated record, but publication is immutable.
        assert subprocess.run(["python3", str(SCRIPT), "--manifest", str(manifest), "--output", str(output)], check=False).returncode == 0
        r = subprocess.run(["python3", str(SCRIPT), "--manifest", str(manifest), "--output", str(output)], text=True, capture_output=True)
        assert r.returncode != 0 and "already exists" in r.stderr
    print("PASS: ebuild backend correlation rejects duplicate CPVs and overwrite")

if __name__ == "__main__": main()

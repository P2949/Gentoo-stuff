import json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/inventory/extract-elf-metadata.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); census = root / "census.json"; out = root / "elf.json"
        census.write_text(json.dumps({"artifacts": [
            {"owner_cpv": "app/a-1", "path": "/bin/true", "elf": {"class": 2}},
            {"owner_cpv": "app/a-1", "path": "/bin/true", "elf": {"class": 2}},
        ]}))
        r = subprocess.run(["python3", str(SCRIPT), "--census", str(census), "--output", str(out)], text=True, capture_output=True)
        assert r.returncode != 0 and "duplicate ELF identity" in r.stderr
        census.write_text(json.dumps({"artifacts": []}))
        assert subprocess.run(["python3", str(SCRIPT), "--census", str(census), "--output", str(out)], check=False).returncode == 0
        r = subprocess.run(["python3", str(SCRIPT), "--census", str(census), "--output", str(out)], text=True, capture_output=True)
        assert r.returncode != 0 and "already exists" in r.stderr
    print("PASS: ELF metadata census rejects duplicate identities and overwrite")

if __name__ == "__main__": main()

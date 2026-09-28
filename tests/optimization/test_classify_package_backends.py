import bz2, json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/inventory/classify-package-backends.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); vdb = root / "vdb"; pkg = vdb / "app" / "a-1"; pkg.mkdir(parents=True)
        (pkg / "CONTENTS").write_text("")
        (pkg / "INHERITED").write_text("cmake\n")
        with bz2.open(pkg / "environment.bz2", "wt") as stream:
            stream.write('declare -a QA_PREBUILT=( [0]="/usr/bin/a" )\n')
        out = root / "out.json"
        assert subprocess.run(["python3", str(SCRIPT), "--vdb", str(vdb), "--output", str(out)], check=False).returncode == 0
        row = json.loads(out.read_text())["packages"][0]
        assert row["inherits"] == ["cmake"]
        assert row["qa_prebuilt"] is True
        assert row["vdb_environment_markers"] == ["QA_PREBUILT"]
        r = subprocess.run(["python3", str(SCRIPT), "--vdb", str(vdb), "--output", str(out)], text=True, capture_output=True)
        assert r.returncode != 0 and "already exists" in r.stderr
    print("PASS: package backend classification publishes write-once")

if __name__ == "__main__": main()

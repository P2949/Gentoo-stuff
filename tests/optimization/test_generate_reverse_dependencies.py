#!/usr/bin/env python3
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/generate-reverse-dependencies.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        portage = root / "portage.json"
        elf = root / "elf.json"
        output = root / "graph.json"
        portage.write_text(json.dumps({"records": [{"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1"}], "build_records": []}))
        elf.write_text(json.dumps({"records": [{"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1", "evidence": {"needed": "lib.so"}}]}))
        subprocess.run(["python3", str(SCRIPT), "--portage", str(portage), "--elf", str(elf), "--output", str(output)], check=True)
        records = json.loads(output.read_text())["records"]
        assert {row["relationship"] for row in records} == {"portage-runtime", "elf-needed"}
        elf_duplicate = root / "elf-duplicate.json"
        elf_duplicate.write_text(json.dumps({"records": [
            {"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1", "evidence": {"consumer_path": "/usr/bin/a", "needed": "lib.so"}},
            {"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1", "evidence": {"consumer_path": "/usr/bin/b", "needed": "lib.so"}},
        ]}))
        elf_duplicate_output = root / "elf-duplicate-graph.json"
        subprocess.run(["python3", str(SCRIPT), "--portage", str(portage), "--elf", str(elf_duplicate), "--output", str(elf_duplicate_output)], check=True)
        elf_rows = [row for row in json.loads(elf_duplicate_output.read_text())["records"] if row["relationship"] == "elf-needed"]
        assert len(elf_rows) == 1 and len(elf_rows[0]["evidence"]["artifact_edges"]) == 2
        refused = root / "refused.json"
        empty = root / "empty.json"
        empty.write_text(json.dumps({"records": []}))
        result = subprocess.run(["python3", str(SCRIPT), "--portage", str(empty), "--elf", str(elf), "--output", str(refused)], capture_output=True, text=True)
        assert result.returncode != 0 and "Portage dependency authority is empty" in result.stderr
        duplicate = root / "duplicate.json"
        duplicate.write_text(json.dumps({"records": [
            {"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1"},
            {"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1"},
        ], "build_records": []}))
        duplicate_result = subprocess.run(["python3", str(SCRIPT), "--portage", str(duplicate), "--elf", str(elf), "--output", str(root / "duplicate-graph.json")], capture_output=True, text=True)
        assert duplicate_result.returncode != 0 and "duplicate reverse-dependency edge" in duplicate_result.stderr
    print("PASS: reverse-dependency graph requires Portage and DT_NEEDED authority")


if __name__ == "__main__":
    main()

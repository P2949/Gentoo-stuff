import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts/optimization/inventory/build-workload-manifest.py"


def test_static_and_all_entrypoints_are_retained(tmp_path: Path) -> None:
    lanes = tmp_path / "lanes.json"
    elfs = tmp_path / "elf.json"
    output = tmp_path / "manifest.json"
    lanes.write_text(json.dumps({"packages": [{"cpv": "cat/pkg-1", "lane": "pgo-clang-ir"}], "sha256": "lanes"}))
    records = []
    for index in range(12):
        records.append({
            "owner_cpv": "cat/pkg-1",
            "path": f"/usr/bin/tool-{index}",
            "type": "EXEC (Executable file)" if index == 0 else "DYN (Position-Independent Executable file)",
            "build_id": f"id-{index}",
        })
    elfs.write_text(json.dumps({"artifacts": records}))
    subprocess.run(["python3", str(SCRIPT), "--lanes", str(lanes), "--elf", str(elfs), "--output", str(output)], check=True)
    package = json.loads(output.read_text())["packages"][0]
    assert package["state"] == "workload-candidate"
    assert len(package["entrypoints"]) == 12
    assert package["entrypoints"][0]["path"] == "/usr/bin/tool-0"


if __name__ == "__main__":
    test_static_and_all_entrypoints_are_retained(Path(__import__("tempfile").mkdtemp()))
    print("PASS: workload manifest retains static and all entrypoints")

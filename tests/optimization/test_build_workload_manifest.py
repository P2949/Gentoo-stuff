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
    second = subprocess.run(["python3", str(SCRIPT), "--lanes", str(lanes), "--elf", str(elfs), "--output", str(output)], capture_output=True, text=True)
    assert second.returncode != 0
    assert "workload manifest output already exists" in (second.stdout + second.stderr)
    duplicate_lanes = tmp_path / "duplicate-lanes.json"
    duplicate_lanes.write_text(json.dumps({"packages": [
        {"cpv": "cat/pkg-1", "lane": "pgo-clang-ir"},
        {"cpv": "cat/pkg-1", "lane": "pgo-gcc"},
    ], "sha256": "lanes"}))
    duplicate_output = tmp_path / "duplicate-manifest.json"
    duplicate_result = subprocess.run(["python3", str(SCRIPT), "--lanes", str(duplicate_lanes), "--elf", str(elfs), "--output", str(duplicate_output)], capture_output=True, text=True)
    assert duplicate_result.returncode != 0
    assert "duplicate CPV in lane authority" in (duplicate_result.stdout + duplicate_result.stderr)
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"schema": "optimization-scope-policy-v1", "scope": [{
        "selector": "cat/pkg", "state": "retained-installed-out-of-project-scope",
        "reason_code": "test-scope", "introduced_boundary": "test",
        "retain_installed": True, "unmerge": False, "optimization": False,
        "training": False, "bolt": False,
    }]}))
    scoped_output = tmp_path / "scoped-manifest.json"
    subprocess.run(["python3", str(SCRIPT), "--lanes", str(lanes), "--elf", str(elfs),
                    "--scope-policy", str(scope), "--output", str(scoped_output)], check=True)
    scoped = json.loads(scoped_output.read_text())["packages"][0]
    assert scoped["state"] == "scope-excluded"
    assert scoped["entrypoints"] == []


if __name__ == "__main__":
    test_static_and_all_entrypoints_are_retained(Path(__import__("tempfile").mkdtemp()))
    print("PASS: workload manifest retains static and all entrypoints")

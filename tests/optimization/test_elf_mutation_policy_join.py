#!/usr/bin/env python3
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/inventory/classify-elf-eligibility.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        metadata = root / "metadata.json"
        policy = root / "policy.json"
        output = root / "output.json"
        metadata.write_text(json.dumps({"sha256": "b" * 64, "artifacts": [{
            "owner_cpv": "sys-kernel/scx-1",
            "path": "/usr/bin/scx",
            "class": "ELF64",
            "machine": "Advanced Micro Devices X86-64",
            "type": "DYN (Position-Independent Executable file)",
            "build_id": "a" * 40,
        }]}))
        policy.write_text(json.dumps({
            "record_type": "package-mutation-policy",
            "records": [{"cpv": "sys-kernel/scx-1", "decision": "kernel-policy-exclusion"}],
        }))
        subprocess.run([
            "python3", str(SCRIPT), "--metadata", str(metadata),
            "--mutation-policy", str(policy), "--output", str(output)
        ], check=True)
        records = json.loads(output.read_text())["records"]
        assert records[0]["state"] == "not-applicable"
        assert records[0]["reason_code"] == "kernel-policy-exclusion"
        duplicate_metadata = root / "duplicate-metadata.json"
        duplicate_metadata.write_text(json.dumps({"sha256": "b" * 64, "artifacts": [
            metadata_data for metadata_data in json.loads(metadata.read_text())["artifacts"] * 2
        ]}))
        duplicate_result = subprocess.run([
            "python3", str(SCRIPT), "--metadata", str(duplicate_metadata),
            "--mutation-policy", str(policy), "--output", str(root / "duplicate-output.json")
        ], capture_output=True, text=True)
        assert duplicate_result.returncode != 0 and "duplicate ELF eligibility identity" in duplicate_result.stderr
    print("PASS: ELF eligibility joins mutation-policy kernel exclusions")


if __name__ == "__main__":
    main()

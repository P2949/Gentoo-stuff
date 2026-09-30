#!/usr/bin/env python3
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/generate-live-elf-dependencies.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "elf.json"
        output = root / "edges.json"
        source.write_text(json.dumps({"artifacts": [
            {"owner_cpv": "dev/provider-2", "path": "/usr/lib/libprovider.so.2", "soname": "libprovider.so.2"},
            {"owner_cpv": "app/consumer-1", "path": "/usr/bin/consumer", "needed": ["libprovider.so.2"]},
            {"owner_cpv": "dev/other-1", "path": "/opt/other/libprovider.so.2", "soname": "libother.so.1"},
        ]}))
        subprocess.run(["python3", str(SCRIPT), "--elf", str(source), "--output", str(output)], check=True)
        records = json.loads(output.read_text())["records"]
        assert records[0]["provider_cpv"] == "dev/provider-2"
        assert records[0]["consumer_cpv"] == "app/consumer-1"
        same_cpv = root / "same-cpv.json"
        same_output = root / "same-cpv-edges.json"
        same_cpv.write_text(json.dumps({"artifacts": [
            {"owner_cpv": "app/consumer-1", "path": "/usr/bin/consumer", "needed": ["libprivate.so.1"]},
            {"owner_cpv": "app/consumer-1", "path": "/usr/lib/libprivate.so.1", "soname": "libprivate.so.1"},
        ]}))
        subprocess.run(["python3", str(SCRIPT), "--elf", str(same_cpv), "--output", str(same_output)], check=True)
        same_records = json.loads(same_output.read_text())["records"]
        assert len(same_records) == 1
        assert same_records[0]["provider_cpv"] == "app/consumer-1"
        assert same_records[0]["evidence"]["provider_path"] == "/usr/lib/libprivate.so.1"
        scoped = root / "scoped.json"
        scoped_output = root / "scoped-edges.json"
        scoped.write_text(json.dumps({"artifacts": [
            {"owner_cpv": "dev/system-1", "path": "/usr/lib/libx.so.1", "soname": "libx.so.1", "class": "ELF64", "machine": "Advanced Micro Devices X86-64"},
            {"owner_cpv": "dev/vendor-1", "path": "/opt/vendor/lib/libx.so.1", "soname": "libx.so.1", "class": "ELF64", "machine": "Advanced Micro Devices X86-64"},
            {"owner_cpv": "app/scoped-1", "path": "/opt/vendor/bin/app", "needed": ["libx.so.1"], "runpath": ["$ORIGIN/../lib"], "class": "ELF64", "machine": "Advanced Micro Devices X86-64"},
        ]}))
        subprocess.run(["python3", str(SCRIPT), "--elf", str(scoped), "--output", str(scoped_output)], check=True)
        scoped_records = json.loads(scoped_output.read_text())["records"]
        assert scoped_records[0]["provider_cpv"] == "dev/vendor-1"
        assert scoped_records[0]["evidence"]["provider_path"] == "/opt/vendor/lib/libx.so.1"
        refused = subprocess.run(["python3", str(SCRIPT), "--elf", str(source), "--output", str(output)], capture_output=True, text=True)
        assert refused.returncode != 0 and "output already exists" in refused.stderr
        duplicate = root / "duplicate.json"
        duplicate.write_text(json.dumps({"artifacts": [
            {"owner_cpv": "dev/provider-2", "path": "/usr/lib/libprovider.so.2", "soname": "libprovider.so.2"},
            {"owner_cpv": "dev/provider-2", "path": "/usr/lib/libprovider.so.2", "soname": "libprovider.so.2"},
        ]}))
        duplicate_result = subprocess.run(["python3", str(SCRIPT), "--elf", str(duplicate), "--output", str(root / "duplicate-edges.json")], capture_output=True, text=True)
        assert duplicate_result.returncode != 0 and "duplicate ELF artifact identity" in duplicate_result.stderr
    print("PASS: ELF dependency resolution uses authenticated SONAMEs")


if __name__ == "__main__":
    main()

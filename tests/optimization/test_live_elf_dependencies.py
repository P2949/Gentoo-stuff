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
    print("PASS: ELF dependency resolution uses authenticated SONAMEs")


if __name__ == "__main__":
    main()

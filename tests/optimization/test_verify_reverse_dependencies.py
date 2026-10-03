#!/usr/bin/env python3
import hashlib, json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/verify-reverse-dependencies.py"

def canon(v): return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); portage = root / "portage"; elf = root / "elf"; graph = root / "graph"
        portage.write_text("portage")
        elf.write_text("elf")
        doc = {
            "record_type": "reverse-dependency-graph", "schema_version": 2,
            "source_contract": {"portage_runtime_records": 1, "portage_build_records": 0, "elf_needed_records": 0},
            "portage_source_sha256": hashlib.sha256(portage.read_bytes()).hexdigest(),
            "elf_source_sha256": hashlib.sha256(elf.read_bytes()).hexdigest(),
            "unresolved_review_sha256": None,
            "records": [{"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1", "relationship": "portage-runtime", "evidence": {}}],
        }
        unsigned = dict(doc); doc["sha256"] = hashlib.sha256(canon(unsigned)).hexdigest(); graph.write_text(json.dumps(doc))
        subprocess.run(["python3", str(SCRIPT), "--graph", str(graph), "--portage", str(portage), "--elf", str(elf)], check=True)
        broken = dict(doc); broken["source_contract"] = dict(doc["source_contract"]); broken["source_contract"]["portage_runtime_records"] = 2
        unsigned = dict(broken); unsigned.pop("sha256", None); broken["sha256"] = hashlib.sha256(canon(unsigned)).hexdigest(); graph.write_text(json.dumps(broken))
        assert subprocess.run(["python3", str(SCRIPT), "--graph", str(graph), "--portage", str(portage), "--elf", str(elf)]).returncode != 0
    print("PASS: reverse-dependency graph verifier enforces source and edge contracts")

if __name__ == "__main__": main()

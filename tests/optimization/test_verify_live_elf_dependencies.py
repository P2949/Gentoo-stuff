#!/usr/bin/env python3
import hashlib, json, subprocess, tempfile
from pathlib import Path
ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/verify-live-elf-dependencies.py"
def canon(v): return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()
def main():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); elf=root/"elf.json"; source=root/"source.json"; elf.write_text("elf")
        doc={"record_type":"live-elf-dependency-source","schema_version":1,"source_elf_sha256":hashlib.sha256(elf.read_bytes()).hexdigest(),"records":[{"provider_cpv":"dev/lib-1","consumer_cpv":"app/tool-1","relationship":"elf-needed","evidence":{"consumer_path":"/usr/bin/tool","provider_path":"/usr/lib/lib.so","needed":"lib.so"}}],"unresolved":[{"consumer_cpv":"app/tool-1","consumer_path":"/usr/bin/tool","needed":"missing.so","reason":"provider-not-owned"}]}
        doc["sha256"]=hashlib.sha256(canon(doc)).hexdigest(); source.write_text(json.dumps(doc))
        subprocess.run(["python3",str(SCRIPT),"--source",str(source),"--elf",str(elf)],check=True)
        broken=dict(doc); broken["source_elf_sha256"]="0"*64; broken.pop("sha256"); broken["sha256"]=hashlib.sha256(canon(broken)).hexdigest(); source.write_text(json.dumps(broken))
        assert subprocess.run(["python3",str(SCRIPT),"--source",str(source),"--elf",str(elf)]).returncode != 0
    print("PASS: live ELF dependency verifier enforces source and artifact contracts")
if __name__ == "__main__": main()

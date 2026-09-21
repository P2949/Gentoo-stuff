import json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
GEN = ROOT / "scripts/optimization/inventory/generate-mutation-policy.py"
VER = ROOT / "scripts/optimization/inventory/verify-mutation-policy.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest = root / "manifest.json"
        classification = root / "kernel.json"
        policy = root / "policy.json"
        manifest.write_text(json.dumps({"packages": [{"cpv": "app/a-1"}, {"cpv": "dev/b-2"}]}))
        classification.write_text(json.dumps({"records": [
            {"cpv": "app/a-1", "state": "userspace-transaction", "reason_code": "no-forbidden-lifecycle-evidence", "ebuild_markers": [], "evidence_paths": [], "repository": "gentoo", "ebuild_path": "/repo/a.ebuild"},
            {"cpv": "dev/b-2", "state": "kernel-policy-exclusion", "reason_code": "owned-forbidden-artifact", "ebuild_markers": [], "evidence_paths": ["/boot/x"], "repository": "gentoo", "ebuild_path": "/repo/b.ebuild"},
        ]}))
        subprocess.run(["python3", str(GEN), "--manifest", str(manifest), "--kernel-classification", str(classification), "--generation-id", "g1", "--output", str(policy)], check=True)
        subprocess.run(["python3", str(VER), "--policy", str(policy), "--manifest", str(manifest)], check=True)
        assert [x["decision"] for x in json.loads(policy.read_text())["records"]] == ["userspace", "kernel-policy-exclusion"]
    print("PASS: canonical mutation policy generation and verification")

if __name__ == "__main__": main()

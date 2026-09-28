import json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
GEN = ROOT / "scripts/optimization/inventory/generate-mutation-policy.py"
VER = ROOT / "scripts/optimization/inventory/verify-mutation-policy.py"
STATE = ROOT / "scripts/optimization/inventory/classify-package-state.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest = root / "manifest.json"
        classification = root / "kernel.json"
        policy = root / "policy.json"
        manifest.write_text(json.dumps({"packages": [{"cpv": "app/a-1"}, {"cpv": "virtual/b-2"}, {"cpv": "dev/b-2"}]}))
        classification.write_text(json.dumps({"records": [
            {"cpv": "app/a-1", "state": "userspace-transaction", "reason_code": "no-forbidden-lifecycle-evidence", "ebuild_markers": [], "evidence_paths": [], "repository": "gentoo", "ebuild_path": "/repo/a.ebuild"},
            {"cpv": "virtual/b-2", "state": "pending-lifecycle-review", "reason_code": "source-unavailable", "ebuild_markers": [], "evidence_paths": [], "repository": "gentoo", "ebuild_path": None},
            {"cpv": "dev/b-2", "state": "kernel-policy-exclusion", "reason_code": "owned-forbidden-artifact", "ebuild_markers": [], "evidence_paths": ["/boot/x"], "repository": "gentoo", "ebuild_path": "/repo/b.ebuild"},
        ]}))
        subprocess.run(["python3", str(GEN), "--manifest", str(manifest), "--kernel-classification", str(classification), "--generation-id", "g1", "--output", str(policy)], check=True)
        subprocess.run(["python3", str(VER), "--policy", str(policy), "--manifest", str(manifest), "--kernel-classification", str(classification)], check=True)
        assert [x["decision"] for x in json.loads(policy.read_text())["records"]] == ["userspace", "kernel-policy-exclusion", "userspace"]
        # Portage's catpkgsplit returns a null category component for
        # metadata-only zero-version records.  The classifier must still
        # derive the authoritative PN from the structural tuple.
        vdb = root / "vdb" / "acct-group" / "audio-0-r3"
        vdb.mkdir(parents=True)
        (vdb / "CATEGORY").write_text("acct-group\n")
        (vdb / "CONTENTS").write_text("\n")
        census = root / "census.json"
        census.write_text(json.dumps({"artifacts": []}))
        state_manifest = root / "state-manifest.json"
        state_manifest.write_text(json.dumps({"packages": [{"cpv": "acct-group/audio-0-r3"}]}))
        state_policy = root / "state-policy.json"
        state_policy.write_text(json.dumps({"record_type": "package-mutation-policy", "schema_version": 1,
            "records": [{"cpv": "acct-group/audio-0-r3", "decision": "userspace", "triggers": [], "evidence": []}],
            "sha256": ""}))
        unsigned = json.loads(state_policy.read_text()); import hashlib
        unsigned["sha256"] = hashlib.sha256(json.dumps({k:v for k,v in unsigned.items() if k != "sha256"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        state_policy.write_text(json.dumps(unsigned))
        state_out = root / "state.json"
        subprocess.run(["python3", str(STATE), "--manifest", str(state_manifest), "--census", str(census), "--mutation-policy", str(state_policy), "--vdb", str(root / "vdb"), "--output", str(state_out)], check=True)
        assert json.loads(state_out.read_text())["records"][0]["state"] == "not-applicable"
    print("PASS: canonical mutation policy generation and verification")

if __name__ == "__main__": main()

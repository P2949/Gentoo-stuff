#!/usr/bin/env python3
import hashlib, importlib.util, json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/verify-live-portage-dependencies.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "source.json"
        doc = {
            "record_type": "live-portage-dependency-source", "schema_version": 2,
            "vdb_root": "/var/db/pkg", "cpv_count": 1,
            "source_digest": "a" * 64, "choice_review_sha256": None,
            "source_errors": [{"cpv": "app/c-1", "field": "RDEPEND", "stage": "dependency-parse",
                               "expression_sha256": "b" * 64, "choice_operator": "||", "choice_branch_count": 2}],
            "records": [{"consumer_cpv": "app/c-1", "relationship": "portage-runtime",
                          "evidence": {"field": "RDEPEND", "error": "unresolved",
                                       "expression_sha256": "b" * 64,
                                       "choice_operator": "||", "choice_branch_count": 2}}],
            "build_records": [],
        }
        doc["sha256"] = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        path.write_text(json.dumps(doc))
        subprocess.run(["python3", str(SCRIPT), "--source", str(path)], check=True)
        mismatch = dict(doc)
        mismatch["records"] = []
        mismatch.pop("sha256", None)
        mismatch["sha256"] = hashlib.sha256(json.dumps(mismatch, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        path.write_text(json.dumps(mismatch))
        assert subprocess.run(["python3", str(SCRIPT), "--source", str(path)]).returncode != 0
        broken = dict(doc); broken["source_errors"] = [{"stage": "metadata"}]
        broken.pop("sha256", None)
        broken["sha256"] = hashlib.sha256(json.dumps(broken, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        path.write_text(json.dumps(broken))
        assert subprocess.run(["python3", str(SCRIPT), "--source", str(path)]).returncode != 0
    print("PASS: Portage dependency source verifier rejects untyped errors")

if __name__ == "__main__": main()

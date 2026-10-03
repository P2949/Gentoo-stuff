#!/usr/bin/env python3
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/generate-reverse-dependencies.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        portage = root / "portage.json"
        elf = root / "elf.json"
        output = root / "graph.json"
        portage.write_text(json.dumps({
            "records": [{"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1"}],
            "build_records": [{"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1", "evidence": {"field": "DEPEND"}}],
        }))
        elf.write_text(json.dumps({"records": [{"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1", "evidence": {"needed": "lib.so"}}]}))
        subprocess.run(["python3", str(SCRIPT), "--portage", str(portage), "--elf", str(elf), "--output", str(output)], check=True)
        records = json.loads(output.read_text())["records"]
        assert {row["relationship"] for row in records} == {"portage-runtime", "portage-build", "elf-needed"}
        elf_duplicate = root / "elf-duplicate.json"
        elf_duplicate.write_text(json.dumps({"records": [
            {"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1", "evidence": {"consumer_path": "/usr/bin/a", "needed": "lib.so"}},
            {"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1", "evidence": {"consumer_path": "/usr/bin/b", "needed": "lib.so"}},
        ]}))
        elf_duplicate_output = root / "elf-duplicate-graph.json"
        subprocess.run(["python3", str(SCRIPT), "--portage", str(portage), "--elf", str(elf_duplicate), "--output", str(elf_duplicate_output)], check=True)
        elf_rows = [row for row in json.loads(elf_duplicate_output.read_text())["records"] if row["relationship"] == "elf-needed"]
        assert len(elf_rows) == 1 and len(elf_rows[0]["evidence"]["artifact_edges"]) == 2
        refused = root / "refused.json"
        empty = root / "empty.json"
        empty.write_text(json.dumps({"records": []}))
        result = subprocess.run(["python3", str(SCRIPT), "--portage", str(empty), "--elf", str(elf), "--output", str(refused)], capture_output=True, text=True)
        assert result.returncode != 0 and "Portage dependency authority is empty" in result.stderr
        errored = root / "errored.json"
        errored.write_text(json.dumps({"records": [], "build_records": [], "source_errors": [{"cpv": "app/tool-1", "stage": "consumer-metadata"}]}))
        errored_result = subprocess.run(["python3", str(SCRIPT), "--portage", str(errored), "--elf", str(elf), "--output", str(root / "errored-graph.json")], capture_output=True, text=True)
        assert errored_result.returncode != 0 and "source errors" in errored_result.stderr
        typed_portage = root / "typed-portage.json"
        typed_portage.write_text(json.dumps({
            "record_type": "live-portage-dependency-source", "schema_version": 2,
            "sha256": "0" * 64, "records": [], "build_records": [],
        }))
        typed_result = subprocess.run(["python3", str(SCRIPT), "--portage", str(typed_portage), "--elf", str(elf), "--output", str(root / "typed-graph.json")], capture_output=True, text=True)
        assert typed_result.returncode != 0 and "unsupported Portage source contract" in typed_result.stderr
        unresolved_elf = root / "unresolved-elf.json"
        unresolved_elf.write_text(json.dumps({"records": elf.read_text() and [{"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1"}], "unresolved": [{"consumer_cpv": "app/tool-1", "needed": "missing.so"}]}))
        unresolved_result = subprocess.run(["python3", str(SCRIPT), "--portage", str(portage), "--elf", str(unresolved_elf), "--output", str(root / "unresolved-graph.json")], capture_output=True, text=True)
        assert unresolved_result.returncode != 0 and "unresolved dynamic dependency" in unresolved_result.stderr
        review = root / "review.json"
        review_doc = {
            "record_type": "elf-unresolved-review", "schema_version": 1,
            "source_elf_sha256": __import__("hashlib").sha256(unresolved_elf.read_bytes()).hexdigest(),
            "records": [{"consumer_cpv": "app/tool-1", "needed": "missing.so"}],
        }
        review_doc["sha256"] = __import__("hashlib").sha256(json.dumps(review_doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        review.write_text(json.dumps(review_doc))
        reviewed_result = subprocess.run(["python3", str(SCRIPT), "--portage", str(portage), "--elf", str(unresolved_elf), "--unresolved-review", str(review), "--output", str(root / "reviewed-graph.json")], capture_output=True, text=True)
        assert reviewed_result.returncode == 0
        assert json.loads((root / "reviewed-graph.json").read_text())["unresolved_review_sha256"] == review_doc["sha256"]
        review_doc["source_elf_sha256"] = "0" * 64
        review_doc.pop("sha256", None)
        review_doc["sha256"] = __import__("hashlib").sha256(json.dumps(review_doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        review.write_text(json.dumps(review_doc))
        stale_review_result = subprocess.run(["python3", str(SCRIPT), "--portage", str(portage), "--elf", str(unresolved_elf), "--unresolved-review", str(review), "--output", str(root / "stale-reviewed-graph.json")], capture_output=True, text=True)
        assert stale_review_result.returncode != 0 and "source digest" in stale_review_result.stderr
        duplicate = root / "duplicate.json"
        duplicate.write_text(json.dumps({"records": [
            {"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1"},
            {"provider_cpv": "dev/lib-1", "consumer_cpv": "app/tool-1"},
        ], "build_records": []}))
        duplicate_result = subprocess.run(["python3", str(SCRIPT), "--portage", str(duplicate), "--elf", str(elf), "--output", str(root / "duplicate-graph.json")], capture_output=True, text=True)
        assert duplicate_result.returncode != 0 and "duplicate reverse-dependency edge" in duplicate_result.stderr
    print("PASS: reverse-dependency graph requires Portage and DT_NEEDED authority")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Census LLVM/GCC-instrumented runtime ELFs owned by the live VDB.

The VDB/CONTENTS-derived census is the authority for ownership; this tool
never walks arbitrary filesystem paths.  ELF sections are the primary signal
so stripped symbols cannot hide an instrumented binary.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _readelf(path: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["/usr/bin/readelf", "-SWn", "-h", "--", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


def inspect(item: dict) -> dict:
    path = item["path"]
    result = {
        "owner_cpv": item["owner_cpv"],
        "path": path,
        "kind": item.get("kind"),
        "instrumentation_markers": [],
        "build_id": None,
        "elf_type": None,
        "status": "clean-normal",
        "error": None,
    }
    try:
        rc, text = _readelf(path)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["status"] = "instrumentation-unknown-origin"
        result["error"] = f"readelf: {exc}"
        return result
    if rc != 0:
        result["status"] = "instrumentation-unknown-origin"
        result["error"] = f"readelf exit {rc}"
        return result
    type_match = re.search(r"Type:\s+([^\n]+)", text)
    if type_match:
        result["elf_type"] = type_match.group(1).strip()
    build_match = re.search(r"Build ID:\s*([0-9A-Fa-f]+)", text)
    if build_match:
        result["build_id"] = build_match.group(1).lower()
    markers = []
    for section in ("__llvm_prf_cnts", "__llvm_prf_data", "__llvm_prf_names", "__llvm_profile_runtime"):
        if re.search(rf"\b{re.escape(section)}\b", text):
            markers.append(section)
    if re.search(r"\b\.gcov\b|\b\.gcda\b|\b\.gcno\b", text):
        markers.append("gcc-gcov")
    result["instrumentation_markers"] = sorted(set(markers))
    if markers:
        result["status"] = "instrumented-unknown-origin"
    return result


def vdb_identity(cpv: str, vdb: Path) -> dict:
    category, pf = cpv.split("/", 1)
    root = vdb / category / pf
    values = {}
    for name in ("repository", "SLOT", "SUBSLOT", "BUILD_TIME", "CONTENTS", "environment.bz2"):
        path = root / name
        if path.is_file():
            if name in {"CONTENTS", "environment.bz2"}:
                values[f"{name}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                values[name] = path.read_text(errors="replace").strip()
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--vdb", default="/var/db/pkg")
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise SystemExit(f"REFUSED: output already exists: {output}")
    census = json.loads(Path(args.census).read_text())
    items = [x for x in census.get("artifacts", []) if x.get("kind") == "regular" and x.get("elf")]
    workers = max(1, min(32, (os.cpu_count() or 1) * 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(inspect, items))
    vdb = Path(args.vdb)
    for record in records:
        if record["instrumentation_markers"]:
            record["vdb_identity"] = vdb_identity(record["owner_cpv"], vdb)
    records.sort(key=lambda row: (row["path"], row["owner_cpv"]))
    out = {
        "record_type": "live-instrumentation-census",
        "schema_version": 1,
        "source_census_sha256": census.get("sha256"),
        "artifact_count": len(items),
        "records": [x for x in records if x["instrumentation_markers"] or x["error"]],
    }
    out["counts"] = {}
    for row in out["records"]:
        out["counts"][row["status"]] = out["counts"].get(row["status"], 0) + 1
    unsigned = json.dumps(out, sort_keys=True, separators=(",", ":")).encode()
    out["sha256"] = hashlib.sha256(unsigned).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"artifact_count": len(items), "instrumented": len(out["records"]), "counts": out["counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()

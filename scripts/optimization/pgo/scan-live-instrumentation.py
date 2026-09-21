#!/usr/bin/env python3
"""Census LLVM/GCC-instrumented runtime ELFs owned by the live VDB.

The VDB/CONTENTS-derived census is the authority for ownership; this tool
never walks arbitrary filesystem paths.  ELF sections are the primary signal
so stripped symbols cannot hide an instrumented binary.
"""
import argparse
import bz2
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
    for section in ("__llvm_prf_cnts", "__llvm_prf_data", "__llvm_prf_names",
                    "__llvm_prf_vnds", "__llvm_prf_vtab", "__llvm_prf_bits",
                    "__llvm_covmap", "__llvm_covfun"):
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


def decode_environment(vdb: Path, cpv: str) -> dict:
    """Decode only scalar VDB environment assignments; never source shell."""
    category, pf = cpv.split("/", 1)
    path = vdb / category / pf / "environment.bz2"
    if not path.is_file():
        return {}
    try:
        text = bz2.decompress(path.read_bytes()).decode("utf-8", errors="replace")
    except (OSError, EOFError, ValueError) as exc:
        return {"_decode_error": str(exc)}
    wanted = {
        "CFLAGS", "CXXFLAGS", "LDFLAGS", "RUSTFLAGS", "CARGO_BUILD_RUSTFLAGS",
        "CARGO_ENCODED_RUSTFLAGS", "GENTOO_OPT_MODE", "GENTOO_OPT_PROFILE_PATH",
        "GENTOO_OPT_WAVE_ID", "GENTOO_OPT_TARGET_CPV", "GENTOO_OPT_GENERATION",
    }
    values = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if match and match.group(1) in wanted:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            values[match.group(1)] = value
    return values


def classify_origin(environment: dict) -> str:
    mode = environment.get("GENTOO_OPT_MODE", "")
    profile = environment.get("GENTOO_OPT_PROFILE_PATH", "")
    target = environment.get("GENTOO_OPT_TARGET_CPV", "")
    if mode.endswith("-generate"):
        if target and profile and target not in profile:
            return "leaked-generation-cobuild"
        return "authorized-wave-target-residue"
    if mode in {"off", "", "unset"} and any("profile" in environment.get(k, "").lower() for k in ("CFLAGS", "CXXFLAGS", "RUSTFLAGS")):
        return "stale-profile-environment"
    if profile:
        return "unknown-origin"
    return "pre-framework-or-unknown"


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
    # Accept both the current census schema (kind/elf booleans) and the
    # earlier authoritative ELF census (class/type fields).  A schema that
    # cannot prove an ELF record is never treated as an eligible artifact.
    items = []
    for item in census.get("artifacts", []):
        is_regular = item.get("kind", "regular") == "regular"
        is_elf = bool(item.get("elf")) or bool(item.get("class")) or bool(item.get("type"))
        if is_regular and is_elf:
            items.append(item)
    workers = max(1, min(32, (os.cpu_count() or 1) * 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(inspect, items))
    vdb = Path(args.vdb)
    for record in records:
        if record["instrumentation_markers"]:
            record["vdb_identity"] = vdb_identity(record["owner_cpv"], vdb)
            record["environment"] = decode_environment(vdb, record["owner_cpv"])
            record["origin"] = classify_origin(record["environment"])
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

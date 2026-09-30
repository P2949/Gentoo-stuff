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

from importlib.util import module_from_spec, spec_from_file_location
_spec = spec_from_file_location('instrumentation', Path(__file__).parents[1] / 'lib' / 'instrumentation.py')
_detector = module_from_spec(_spec); assert _spec.loader; _spec.loader.exec_module(_detector)


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
    result = {"owner_cpv": item["owner_cpv"], "path": path, "kind": item.get("kind"),
              "instrumentation_markers": [], "build_id": None, "elf_type": None,
              "status": "clean-normal", "error": None}
    try:
        instrumented, kind = _detector.inspect_elf(Path(path))
        meta = subprocess.run(["/usr/bin/readelf", "-SWn", "-h", "--", path], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False, timeout=30, text=True)
        if meta.returncode != 0:
            raise _detector.InspectionError(f"readelf metadata exit {meta.returncode}: {meta.stderr.strip()}")
        text = meta.stdout
        type_match = re.search(r"Type:\s+([^\n]+)", text)
        if type_match: result["elf_type"] = type_match.group(1).strip()
        build_match = re.search(r"Build ID:\s*([0-9A-Fa-f]+)", text)
        if build_match: result["build_id"] = build_match.group(1).lower()
        if instrumented:
            result["instrumentation_markers"] = [_detector.LLVM_MARKERS[0] if kind == "llvm" else _detector.GCC_MARKERS[0]]
            result["status"] = "instrumented-unknown-origin"
    except (OSError, subprocess.TimeoutExpired, _detector.InspectionError) as exc:
        result["status"] = "instrumentation-inspection-failed"
        result["error"] = str(exc)
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
        # VDB environment snapshots are shell declarations (normally
        # ``declare -x NAME=...``), while small fixtures may use plain
        # assignments.  Decode both forms without sourcing the snapshot.
        match = re.match(
            r"^(?:declare\s+(?:-[^\s]+\s+)?|)([A-Za-z_][A-Za-z0-9_]*)=(.*)$",
            line,
        )
        if match and match.group(1) in wanted:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            values[match.group(1)] = value
    if re.search(r"(?m)^declare\s+(?:-[^\s]+\s+)?QA_PREBUILT=", text) or re.search(r"(?m)^QA_PREBUILT=", text):
        values["QA_PREBUILT"] = "present"
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
    parser.add_argument("--mutation-policy", help="authenticated package mutation-policy JSON")
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise SystemExit(f"REFUSED: output already exists: {output}")
    census = json.loads(Path(args.census).read_text())
    policy = {}
    mutation_policy_sha256 = None
    mutation_policy_generation = None
    if args.mutation_policy:
        policy_data = json.loads(Path(args.mutation_policy).read_text())
        if policy_data.get("record_type") != "package-mutation-policy" or policy_data.get("schema_version") != 1:
            raise SystemExit("REFUSED: unsupported mutation-policy schema")
        declared = policy_data.get("sha256")
        unsigned = dict(policy_data)
        unsigned.pop("sha256", None)
        expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if not isinstance(declared, str) or declared != expected:
            raise SystemExit("REFUSED: mutation-policy digest mismatch")
        census_generation = census.get("generation_id") or census.get("inventory_id")
        mutation_policy_generation = policy_data.get("generation_id")
        if census_generation and mutation_policy_generation and census_generation != mutation_policy_generation:
            raise SystemExit("REFUSED: mutation-policy generation binding mismatch")
        mutation_policy_sha256 = hashlib.sha256(Path(args.mutation_policy).read_bytes()).hexdigest()
        policy = {
            item.get("cpv"): item
            for item in policy_data.get("records", [])
            if item.get("decision") == "kernel-policy-exclusion"
        }
    # Accept both the current census schema (kind/elf booleans) and the
    # earlier authoritative ELF census (class/type fields).  A schema that
    # cannot prove an ELF record is never treated as an eligible artifact.
    items = []
    for item in census.get("artifacts", []):
        is_regular = item.get("kind", "regular") == "regular"
        is_elf = bool(item.get("elf")) or bool(item.get("class")) or bool(item.get("type"))
        if is_regular and is_elf:
            if item.get("owner_cpv") in policy:
                items.append({**item, "_terminal_policy": "kernel-policy-exclusion"})
            else:
                items.append(item)
    workers = max(1, min(32, (os.cpu_count() or 1) * 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(inspect, items))
    for record, item in zip(records, items):
        if item.get("_terminal_policy"):
            record["status"] = "terminal-policy-exclusion"
            record["terminal_disposition"] = item["_terminal_policy"]
            record["instrumentation_markers"] = []
            record["error"] = None
    vdb = Path(args.vdb)
    for record in records:
        if record["instrumentation_markers"]:
            record["vdb_identity"] = vdb_identity(record["owner_cpv"], vdb)
            record["environment"] = decode_environment(vdb, record["owner_cpv"])
            if record.get("environment", {}).get("QA_PREBUILT") == "present":
                record["origin"] = "terminal-prebuilt-unsupported"
                record["terminal_disposition"] = "unsupported-by-upstream-toolchain/prebuilt"
            else:
                record["origin"] = classify_origin(record["environment"])
    records.sort(key=lambda row: (row["path"], row["owner_cpv"]))
    out = {
        "record_type": "live-instrumentation-census",
        "schema_version": 2,
        "source_census_sha256": census.get("sha256"),
        "mutation_policy_sha256": mutation_policy_sha256,
        "mutation_policy_generation_id": mutation_policy_generation,
        "artifact_count": len(items),
        # Keep terminal policy exclusions in the signed accounting output;
        # dropping them made the scan impossible to audit against policy.
        "records": [x for x in records if x.get("terminal_disposition") or x["instrumentation_markers"] or x["error"]],
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

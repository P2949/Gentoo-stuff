#!/usr/bin/env python3
"""Materialize a content-addressed package.env tree from exact bindings."""
import argparse, hashlib, json, pathlib, shutil

ENV = {
    "pgo-clang-ir": "pgo-clang-ir-generate.conf",
    "pgo-gcc": "pgo-gcc-generate.conf",
    "pgo-go": "pgo-go-use.conf",
    "pgo-rust": "pgo-rust-generate.conf",
    "not-applicable": "optimization-off.conf",
    "unsupported-by-upstream-toolchain": "optimization-off.conf",
    "kernel-policy-exclusion": "optimization-off.conf",
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bindings", type=pathlib.Path, required=True)
    ap.add_argument("--env-root", type=pathlib.Path, required=True)
    ap.add_argument("--output-root", type=pathlib.Path, required=True)
    ap.add_argument("--identity", required=True)
    a = ap.parse_args()
    b = json.loads(a.bindings.read_text())
    records = b.get("records")
    if not isinstance(records, list) or not records:
        raise SystemExit("REFUSED: binding manifest has no records")
    if b.get("sha256") != hashlib.sha256(json.dumps({k:v for k,v in b.items() if k != 'sha256'}, sort_keys=True, separators=(",", ":")).encode()).hexdigest():
        raise SystemExit("REFUSED: binding manifest digest mismatch")
    out = a.output_root
    if out.exists():
        raise SystemExit("REFUSED: policy output already exists")
    (out / "env").mkdir(parents=True)
    lines = []
    seen = set()
    for row in sorted(records, key=lambda x: x["cpv"]):
        cpv, lane = row.get("cpv"), row.get("lane")
        if not isinstance(cpv, str) or not isinstance(lane, str) or lane not in ENV:
            raise SystemExit("REFUSED: binding has invalid CPV or lane")
        if cpv in seen:
            raise SystemExit(f"REFUSED: duplicate CPV binding: {cpv}")
        seen.add(cpv)
        name = ENV[lane]
        source = a.env_root / name
        if not source.is_file():
            raise SystemExit(f"REFUSED: missing reviewed environment: {source}")
        target = out / "env" / name
        if not target.exists(): shutil.copyfile(source, target)
        lines.append(f"={cpv} optimization/generated/{name}")
    (out / "package.env").write_text("\n".join(lines) + "\n")
    print(json.dumps({"records": len(records), "output": str(out), "identity": a.identity}))

if __name__ == "__main__": main()

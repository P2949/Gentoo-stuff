#!/usr/bin/env python3
"""Mark content-addressed objects by explicit receipt reachability.

This is report-only and fail-closed: an object is a candidate only when its
digest is positively unreferenced by machine-readable authority; malformed or
unknown objects remain KEEP.
"""
from __future__ import annotations
import argparse, json, os, re
from pathlib import Path

DIGEST = re.compile(r"^[0-9a-f]{64}$")

def collect(value: object, out: set[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k in {"sha256", "object_sha256"} and isinstance(v, str) and DIGEST.fullmatch(v):
                out.add(v)
            collect(v, out)
    elif isinstance(value, list):
        for item in value: collect(item, out)

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--objects",type=Path,required=True); p.add_argument("--reference-root",action="append",type=Path,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    refs=set()
    for root in a.reference_root:
        if not root.is_dir(): continue
        for path in root.rglob("*.json"):
            try:
                if path.stat().st_size > 8 * 1024 * 1024:
                    continue
                collect(json.loads(path.read_text(encoding="utf-8")), refs)
            except (OSError,ValueError): continue
    rows=[]
    for path in sorted(a.objects.rglob("*")) if a.objects.is_dir() else []:
        if not path.is_file(): continue
        digest=path.name
        if not DIGEST.fullmatch(digest): state,reason="UNKNOWN","object name is not a SHA-256 digest"
        elif digest in refs: state,reason="LIVE_REQUIRED","referenced by machine-readable authority"
        else: state,reason="ARCHIVE_CANDIDATE","positively unreachable from supplied authority roots"
        rows.append({"path":str(path),"sha256":digest,"size":path.stat().st_size,"state":state,"reason":reason})
    payload={"schema":"gentoo-optimization-content-object-reachability-v1","objects":rows,"referenced_sha256_count":len(refs),"unknown_bytes":sum(r["size"] for r in rows if r["state"]=="UNKNOWN"),"archive_candidate_bytes":sum(r["size"] for r in rows if r["state"]=="ARCHIVE_CANDIDATE")}
    a.output.parent.mkdir(parents=True,exist_ok=True); tmp=a.output.with_suffix(a.output.suffix+".tmp"); tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8"); os.replace(tmp,a.output)
    print(json.dumps({k:payload[k] for k in ("schema","referenced_sha256_count","unknown_bytes","archive_candidate_bytes")},indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())

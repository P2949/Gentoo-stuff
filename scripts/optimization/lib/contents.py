"""Strict Portage VDB CONTENTS parsing shared by inventory producers."""
from __future__ import annotations

def parse_contents_line(line: str):
    raw = line.rstrip("\n")
    if not raw.strip():
        return None
    try:
        kind, rest = raw.split(" ", 1)
    except ValueError as exc:
        raise ValueError("missing CONTENTS fields") from exc
    if kind == "dir":
        return kind, rest, []
    if kind == "obj":
        fields = rest.rsplit(" ", 2)
        if len(fields) not in (2, 3):
            raise ValueError("malformed obj CONTENTS record")
        path = fields[0]
        tail = fields[1:]
        return kind, path, tail
    if kind == "sym":
        if " -> " not in rest:
            raise ValueError("malformed sym CONTENTS record")
        path, target = rest.split(" -> ", 1)
        return kind, path, ["->", target]
    raise ValueError(f"unsupported CONTENTS record type: {kind}")

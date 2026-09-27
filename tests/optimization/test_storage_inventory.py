#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "inventory_storage", ROOT / "scripts/optimization/storage/inventory-storage.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "payload").write_bytes(b"payload")
    output = root / "report.json"
    # Avoid probing the host filesystem in the fixture; the report contract is
    # exercised through entry() and the writer is covered by the CLI gate.
    item = MODULE.entry(str(root / "a"))
    assert item["exists"] is True
    assert item["file_count"] == 1
    assert item["allocated_bytes"] >= 0
    files, allocated, children = MODULE.counts_with_children(root / "a")
    assert (files, allocated) == (1, item["allocated_bytes"])
    assert children["b"][0] == 1
    assert json.loads(json.dumps(item))["path"] == str(root / "a")

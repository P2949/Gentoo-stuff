#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "inventory_storage", ROOT / "scripts/optimization/storage/inventory-storage.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader


class StorageInventoryTests(unittest.TestCase):
    def test_entry_and_children_are_discovery_safe(self):
        # Importing this module must not probe the host filesystem.  The
        # fixture is created only when the selected test executes.
        SPEC.loader.exec_module(MODULE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a" / "b").mkdir(parents=True)
            (root / "a" / "b" / "payload").write_bytes(b"payload")
            item = MODULE.entry(str(root / "a"))
            self.assertTrue(item["exists"])
            self.assertEqual(item["file_count"], 1)
            self.assertGreaterEqual(item["allocated_bytes"], 0)
            files, allocated, children = MODULE.counts_with_children(root / "a")
            self.assertEqual((files, allocated), (1, item["allocated_bytes"]))
            self.assertEqual(children["b"][0], 1)
            self.assertEqual(json.loads(json.dumps(item))["path"], str(root / "a"))


if __name__ == "__main__":
    unittest.main()

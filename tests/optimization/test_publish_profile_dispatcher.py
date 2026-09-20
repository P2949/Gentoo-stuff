from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PUBLISHER = ROOT / "scripts/optimization/pgo/publish-profile-dispatcher.py"


def load_publisher():
    spec = importlib.util.spec_from_file_location("publish_profile_dispatcher", PUBLISHER)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load dispatcher publisher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DispatcherPathTrustTests(unittest.TestCase):
    def test_safe_rejects_symlinked_final_path(self) -> None:
        publisher = load_publisher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("trusted\n", encoding="utf-8")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(SystemExit):
                publisher.safe(link, root, "fixture")

    def test_safe_rejects_symlinked_ancestor_after_path_spelling(self) -> None:
        publisher = load_publisher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real"
            real.mkdir()
            payload = real / "payload"
            payload.write_text("trusted\n", encoding="utf-8")
            ancestor = root / "ancestor"
            ancestor.symlink_to(real, target_is_directory=True)
            with self.assertRaises(SystemExit):
                publisher.safe(ancestor / "payload", root, "fixture")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import errno
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest


SCANNER_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts/optimization/pgo/authorization-token-scan.py"
)
SCANNER_SPEC = importlib.util.spec_from_file_location(
    "gentoo_optimization_authorization_token_scan", SCANNER_PATH
)
if SCANNER_SPEC is None or SCANNER_SPEC.loader is None:
    raise RuntimeError(f"cannot load authorization token scanner: {SCANNER_PATH}")
scanner = importlib.util.module_from_spec(SCANNER_SPEC)
sys.modules[SCANNER_SPEC.name] = scanner
SCANNER_SPEC.loader.exec_module(scanner)


TOKEN = b"a" * 64


class AuthorizationTokenScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name) / "root"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_clean_tree_and_cross_chunk_regular_leak(self) -> None:
        (self.root / "clean").write_bytes(b"ordinary evidence")
        self.assertEqual(scanner.scan_roots([self.root], TOKEN), [])
        leak = self.root / "cross-chunk"
        leak.write_bytes(b"x" * (1024 * 1024 - 20) + TOKEN + b"tail")
        self.assertEqual(
            scanner.scan_roots([self.root], TOKEN),
            [scanner.path_identity(leak)],
        )

    def test_names_and_symlink_targets_are_scanned_without_following(self) -> None:
        named = self.root / TOKEN.decode("ascii")
        named.mkdir()
        target = self.root / "target"
        target.write_text("clean", encoding="ascii")
        link = self.root / "link"
        link.symlink_to(TOKEN.decode("ascii"))
        self.assertEqual(
            scanner.scan_roots([self.root], TOKEN),
            sorted([scanner.path_identity(named), scanner.path_identity(link)]),
        )

    def test_special_object_fails_closed(self) -> None:
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(scanner.ScanError, "unsupported object"):
            scanner.scan_roots([self.root], TOKEN)

    def test_xattr_names_and_values_are_scanned(self) -> None:
        name_leak = self.root / "xattr-name"
        value_leak = self.root / "xattr-value"
        name_leak.write_text("clean", encoding="ascii")
        value_leak.write_text("clean", encoding="ascii")
        try:
            os.setxattr(name_leak, "user." + TOKEN.decode("ascii"), b"clean")
            os.setxattr(value_leak, "user.fixture", b"prefix-" + TOKEN + b"-suffix")
        except OSError as error:
            if error.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
                self.skipTest("fixture filesystem lacks user xattrs")
            raise
        self.assertEqual(
            scanner.scan_roots([self.root], TOKEN),
            sorted(
                [
                    scanner.path_identity(name_leak),
                    scanner.path_identity(value_leak),
                ]
            ),
        )

    def test_token_descriptor_and_atomic_result_contract(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        try:
            os.write(write_descriptor, TOKEN + b"\n")
        finally:
            os.close(write_descriptor)
        try:
            self.assertEqual(scanner.read_token(read_descriptor), TOKEN)
        finally:
            os.close(read_descriptor)
        output = pathlib.Path(self.temporary.name) / "result.tsv"
        scanner.publish_result(output, [])
        self.assertEqual(output.read_text(encoding="ascii"), "status\tpath_sha256\npassed\t-\n")
        self.assertEqual(output.stat().st_mode & 0o7777, 0o600)
        with self.assertRaises(FileExistsError):
            scanner.publish_result(output, [])


if __name__ == "__main__":
    unittest.main()

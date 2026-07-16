#!/usr/bin/env python3
"""Trust-boundary tests for the BOLT artifact transaction helper."""

from __future__ import annotations

import base64
import grp
import importlib.util
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "scripts/optimization/bolt/artifact_tool.py"
SPEC = importlib.util.spec_from_file_location("gentoo_bolt_artifact_tool", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class ProductionTrustTests(unittest.TestCase):
    def test_arbitrary_evidence_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bolt-untrusted-evidence.") as temporary:
            evidence = Path(temporary) / "evidence.json"
            evidence.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                TOOL.BoltArtifactError, "outside trusted optimization storage"
            ):
                TOOL.validate_production_evidence_path(evidence, "fixture evidence")

    def test_symlink_ancestor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bolt-symlink-evidence.") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            evidence = target / "evidence.json"
            evidence.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                TOOL.BoltArtifactError, "symlink component"
            ):
                TOOL.file_record(str(alias / evidence.name), "fixture evidence")

    def test_non_root_or_writable_ancestor_is_rejected(self) -> None:
        if Path("/").stat().st_uid != 0:
            self.skipTest(
                "filesystem root is not root-owned in this managed namespace"
            )
        with tempfile.TemporaryDirectory(prefix="bolt-writable-evidence.") as temporary:
            root = Path(temporary)
            original_mode = stat.S_IMODE(root.stat().st_mode)
            root.chmod(0o777)
            try:
                with self.assertRaisesRegex(
                    TOOL.BoltArtifactError,
                    "non-root-owned component|group/world-writable component",
                ):
                    TOOL.validate_root_owned_nonwritable_chain(
                        root, "fixture evidence"
                    )
            finally:
                root.chmod(original_mode)

    def test_system_tool_chain_is_root_owned_and_nonwritable(self) -> None:
        if os.geteuid() != 0 and Path("/").stat().st_uid != 0:
            self.skipTest("filesystem root is not root-owned")
        candidate = Path("/usr/bin/env")
        if not candidate.exists() or candidate.is_symlink():
            self.skipTest("regular /usr/bin/env is unavailable")
        TOOL.validate_root_owned_nonwritable_chain(candidate, "system tool")

    def test_declared_production_lock_modes_allow_portage_readers_only(self) -> None:
        self.assertEqual(TOOL.PRODUCTION_LOCK_GROUP, "portage")
        self.assertEqual(TOOL.PRODUCTION_LOCK_DIRECTORY_MODE, 0o750)
        self.assertEqual(TOOL.PRODUCTION_STABLE_LOCK_MODE, 0o640)

    def test_root_portage_lock_metadata_is_enforced(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("root is required to construct exact root:portage metadata")
        try:
            grp.getgrnam(TOOL.PRODUCTION_LOCK_GROUP)
        except KeyError:
            self.skipTest(
                "portable root environment has no production Portage group"
            )
        portage_gid = TOOL.production_lock_group_gid()
        with tempfile.TemporaryDirectory(prefix="bolt-portage-lock.") as temporary:
            directory = Path(temporary)
            lock = directory / "project.lock"
            os.chown(directory, 0, portage_gid)
            directory.chmod(0o750)
            lock.write_text("fixture\n", encoding="utf-8")
            os.chown(lock, 0, portage_gid)
            lock.chmod(0o640)
            TOOL.validate_production_portage_lock(lock, "fixture project lock")

            lock.chmod(0o600)
            with self.assertRaisesRegex(TOOL.BoltArtifactError, "mode-0640"):
                TOOL.validate_production_portage_lock(lock, "fixture project lock")
            lock.chmod(0o640)

            directory.chmod(0o755)
            with self.assertRaisesRegex(TOOL.BoltArtifactError, "mode 0750"):
                TOOL.validate_production_portage_lock(lock, "fixture project lock")


class HardlinkMetadataTransactionTests(unittest.TestCase):
    def metadata_record(
        self, mode: int, xattrs: dict[str, bytes] | None = None
    ) -> dict[str, object]:
        encoded = {
            name: base64.b64encode(value).decode("ascii")
            for name, value in (xattrs or {}).items()
        }
        return {
            "mode": format(mode, "04o"),
            "uid": os.geteuid(),
            "gid": os.getegid(),
            "xattrs_base64": encoded,
            "capability_base64": encoded.get("security.capability"),
        }

    def exercise_deploy_and_rollback(
        self, expected: dict[str, object]
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="bolt-hardlink-metadata.") as temporary:
            root = Path(temporary)
            original = root / "original.preimage"
            replacement = root / "replacement.bolt"
            destinations = [root / "program", root / "program-alias"]
            original.write_bytes(b"original bytes\n")
            replacement.write_bytes(b"optimized bytes\n")

            deployed = TOOL.stage_hardlink_group(
                replacement, destinations, expected, "deploy"
            )
            # Returning from the staging primitive is the pre-rename commit
            # boundary: the complete temporary inode group must already have
            # its exact final ownership, xattrs/capability, and privilege mode.
            for partial, _ in deployed:
                TOOL.verify_metadata_matches(partial, expected)
            self.assertEqual(
                len({partial.stat().st_ino for partial, _ in deployed}), 1
            )
            self.assertFalse(any(path.exists() for path in destinations))
            for partial, destination in deployed:
                os.replace(partial, destination)
            self.assertEqual(destinations[0].read_bytes(), b"optimized bytes\n")
            self.assertEqual(
                len({path.stat().st_ino for path in destinations}), 1
            )
            for path in destinations:
                TOOL.verify_metadata_matches(path, expected)

            restored = TOOL.stage_hardlink_group(
                original, destinations, expected, "rollback"
            )
            for partial, _ in restored:
                TOOL.verify_metadata_matches(partial, expected)
            for partial, destination in restored:
                os.replace(partial, destination)
            self.assertEqual(destinations[0].read_bytes(), b"original bytes\n")
            self.assertEqual(
                len({path.stat().st_ino for path in destinations}), 1
            )
            for path in destinations:
                TOOL.verify_metadata_matches(path, expected)

    def test_setuid_setgid_and_combined_hardlink_groups_rollback(self) -> None:
        for mode in (0o4755, 0o2755, 0o6755):
            with self.subTest(mode=oct(mode)):
                self.exercise_deploy_and_rollback(self.metadata_record(mode))

    def test_user_xattr_hardlink_group_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bolt-xattr-probe.") as temporary:
            probe = Path(temporary) / "probe"
            probe.write_bytes(b"probe\n")
            try:
                os.setxattr(probe, "user.gentoo-bolt-test", b"preserved")
            except OSError as error:
                self.skipTest(f"user xattrs are unavailable: {error}")
        self.exercise_deploy_and_rollback(
            self.metadata_record(
                0o4755, {"user.gentoo-bolt-test": b"preserved"}
            )
        )

    def test_security_capability_hardlink_group_rollback(self) -> None:
        setcap = shutil.which("setcap")
        if setcap is None:
            self.skipTest("setcap is unavailable")
        with tempfile.TemporaryDirectory(prefix="bolt-capability-probe.") as temporary:
            probe = Path(temporary) / "probe"
            probe.write_bytes(b"probe\n")
            result = subprocess.run(
                [setcap, "cap_net_bind_service=ep", str(probe)],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                self.skipTest(
                    "security.capability cannot be created by this user/filesystem: "
                    + result.stderr.strip()
                )
            capability = os.getxattr(probe, "security.capability")
        self.exercise_deploy_and_rollback(
            self.metadata_record(0o755, {"security.capability": capability})
        )


class ElfRoleClassificationTests(unittest.TestCase):
    def test_static_pie_uses_df_1_pie_instead_of_dso_role(self) -> None:
        cc = shutil.which("cc")
        readelf = shutil.which("readelf")
        objcopy = shutil.which("objcopy")
        if cc is None or readelf is None or objcopy is None:
            self.skipTest("cc, readelf, and objcopy are required")
        with tempfile.TemporaryDirectory(prefix="bolt-static-pie.") as temporary:
            root = Path(temporary)
            source = root / "fixture.c"
            static_pie = root / "fixture-static-pie"
            dso = root / "libfixture.so"
            source.write_text(
                "int fixture(int value) { return value + 1; }\n"
                "int main(void) { return fixture(0) != 1; }\n",
                encoding="utf-8",
            )
            static_result = subprocess.run(
                [
                    cc,
                    "-O2",
                    "-g",
                    "-static-pie",
                    "-Wl,--build-id=sha1,--emit-relocs",
                    str(source),
                    "-o",
                    str(static_pie),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            if static_result.returncode != 0:
                self.skipTest(
                    "toolchain cannot build a static PIE fixture: "
                    + static_result.stderr.strip()
                )
            subprocess.run(
                [
                    cc,
                    "-O2",
                    "-g",
                    "-fPIC",
                    "-shared",
                    "-Wl,--build-id=sha1,--emit-relocs,-soname,libfixture.so",
                    str(source),
                    "-o",
                    str(dso),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            static_identity = TOOL.classify_elf(
                static_pie, readelf, objcopy, root / "static-sections"
            )
            dso_identity = TOOL.classify_elf(
                dso, readelf, objcopy, root / "dso-sections"
            )
            self.assertEqual(static_identity["elf_type"], "DYN")
            self.assertIsNone(static_identity["interpreter"])
            self.assertTrue(
                TOOL.has_dynamic_flag(static_identity, "FLAGS_1", "PIE")
            )
            self.assertEqual(static_identity["elf_role"], "pie-executable")
            self.assertEqual(dso_identity["elf_type"], "DYN")
            self.assertIsNone(dso_identity["interpreter"])
            self.assertFalse(TOOL.has_dynamic_flag(dso_identity, "FLAGS_1", "PIE"))
            self.assertEqual(dso_identity["elf_role"], "shared-object")


if __name__ == "__main__":
    unittest.main()

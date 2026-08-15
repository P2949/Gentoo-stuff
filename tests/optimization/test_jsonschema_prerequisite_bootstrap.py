from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PUBLISHER = (
    REPOSITORY_ROOT
    / "scripts/optimization/recovery/publish-jsonschema-prerequisite-bootstrap.py"
)
PYTHON = Path(sys.executable).resolve(strict=True)


class JsonschemaPrerequisiteBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(strict=True)
        self.root.chmod(0o700)
        self.repository = self.root / "source"
        self.destination_parent = self.root / "bootstrap"
        self.repository.mkdir(mode=0o700)
        self.destination_parent.mkdir(mode=0o700)
        self.executed_helper_marker = self.root / "executed-helper.txt"
        recovery = self.repository / "scripts/optimization/recovery"
        recovery.mkdir(parents=True)
        self.publisher = recovery / SOURCE_PUBLISHER.name
        shutil.copyfile(SOURCE_PUBLISHER, self.publisher)
        self.publisher.chmod(0o755)
        self.helper = recovery / "install-jsonschema-prerequisite.py"
        self.helper.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "if sys.argv[1] not in {'prepare', 'run', 'recover', 'verify'}:\n"
            "    raise SystemExit(98)\n"
            f"Path({os.fspath(self.executed_helper_marker)!r}).write_text("
            "__file__ + '\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.helper.chmod(0o755)
        self.verifier = recovery / "verify-binpkg-snapshot.py"
        self.verifier.write_text("raise SystemExit(0)\n", encoding="utf-8")
        self.verifier.chmod(0o755)
        self.git("init", "--quiet")
        self.git("config", "user.name", "Bootstrap Fixture")
        self.git("config", "user.email", "bootstrap-fixture@example.invalid")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "fixture")
        self.commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.tree = self.git("rev-parse", "HEAD^{tree}").stdout.strip()

    def git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/usr/bin/git", "-C", os.fspath(self.repository), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=check,
            env={
                "HOME": os.fspath(self.root),
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
                "TZ": "UTC",
            },
        )

    def invoke(
        self,
        *arguments: str,
        publisher: Path | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                os.fspath(PYTHON),
                "-I",
                "-B",
                os.fspath(publisher or self.publisher),
                "--fixture-destination-parent",
                os.fspath(self.destination_parent),
                "--fixture-python",
                os.fspath(PYTHON),
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=check,
            env={
                "GENTOO_OPT_JSONSCHEMA_BOOTSTRAP_FIXTURE": "1",
                "HOME": os.fspath(self.root),
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
                "TZ": "UTC",
            },
        )

    def publish(self) -> Path:
        result = self.invoke(
            "publish",
            "--repository-root",
            os.fspath(self.repository),
            "--commit",
            self.commit,
            check=True,
        )
        self.assertEqual(result.stderr, "")
        return Path(result.stdout.strip())

    def test_publish_binds_exact_commit_tree_and_source_and_destination_identities(self) -> None:
        destination = self.publish()
        self.assertEqual(
            destination,
            self.destination_parent / f"jsonschema-prerequisite-{self.commit}",
        )
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        manifest = json.loads(
            (destination / "bootstrap-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["schema"],
            "gentoo-optimization-jsonschema-prerequisite-bootstrap-v1",
        )
        self.assertEqual(manifest["commit"], self.commit)
        self.assertEqual(manifest["tree"], self.tree)
        self.assertEqual(manifest["repository_root"], os.fspath(self.repository))
        self.assertEqual(manifest["destination"], os.fspath(destination))
        self.assertEqual(manifest["python"]["path"], os.fspath(PYTHON))
        self.assertEqual(manifest["python"]["uid"], 0)
        self.assertEqual(manifest["python"]["gid"], 0)
        self.assertRegex(manifest["python"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            {row["relative"] for row in manifest["files"]},
            {
                "publish-jsonschema-prerequisite-bootstrap.py",
                "install-jsonschema-prerequisite.py",
                "verify-binpkg-snapshot.py",
            },
        )
        for row in manifest["files"]:
            self.assertEqual(row["git"]["mode"], "100755")
            self.assertEqual(
                row["git"]["path"],
                "scripts/optimization/recovery/" + row["relative"],
            )
            self.assertRegex(row["git"]["blob_oid"], r"^[0-9a-f]{40}$")
            self.assertRegex(row["git"]["blob_sha256"], r"^[0-9a-f]{64}$")
            source = row["source"]
            published = row["published"]
            self.assertEqual(source["uid"], os.getuid())
            self.assertEqual(source["gid"], os.getgid())
            self.assertEqual(source["mode"], 0o755)
            self.assertEqual(source["nlink"], 1)
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(row["git"]["blob_size"], source["size"])
            self.assertEqual(row["git"]["blob_sha256"], source["sha256"])
            self.assertEqual(published["uid"], os.getuid())
            self.assertEqual(published["gid"], os.getgid())
            self.assertEqual(published["mode"], 0o755)
            self.assertEqual(published["nlink"], 1)
            self.assertEqual(published["sha256"], source["sha256"])
            self.assertEqual(
                published["path"], os.fspath(destination / row["relative"])
            )
        self.assertEqual(
            stat.S_IMODE((destination / "bootstrap-manifest.json").stat().st_mode),
            0o600,
        )
        verified = self.invoke(
            "verify",
            "--commit",
            self.commit,
            publisher=destination / self.publisher.name,
            check=True,
        )
        self.assertEqual(verified.stdout.strip(), os.fspath(destination))

    def test_python_override_is_fixture_only_and_production_python_is_fixed(self) -> None:
        source = SOURCE_PUBLISHER.read_text(encoding="utf-8")
        self.assertIn('PYTHON = Path("/usr/bin/python3.15")', source)
        self.assertIn(
            'PRODUCTION_PARENT = Path("/var/lib/gentoo-optimization/bootstrap")',
            source,
        )
        self.assertIn("expected_mode = 0o755 if authority.production else 0o700", source)
        self.assertIn(
            'validate_tree(authority.parent, Path("/"), 0, 0)',
            source,
        )
        result = subprocess.run(
            [
                os.fspath(PYTHON),
                "-I",
                "-B",
                os.fspath(self.publisher),
                "--fixture-python",
                os.fspath(PYTHON),
                "verify",
                "--commit",
                self.commit,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
            env={
                "GENTOO_OPT_JSONSCHEMA_BOOTSTRAP_FIXTURE": "1",
                "HOME": os.fspath(self.root),
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
                "TZ": "UTC",
            },
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("fixture Python override is forbidden in production", result.stderr)

    def test_publish_refuses_a_dirty_or_different_checkout(self) -> None:
        self.helper.write_text("raise SystemExit(99)\n", encoding="utf-8")
        dirty = self.invoke(
            "publish",
            "--repository-root",
            os.fspath(self.repository),
            "--commit",
            self.commit,
        )
        self.assertEqual(dirty.returncode, 1)
        self.assertIn("tracked, untracked, or ignored residue", dirty.stderr)
        self.git("checkout", "--", os.fspath(self.helper.relative_to(self.repository)))
        different = self.invoke(
            "publish",
            "--repository-root",
            os.fspath(self.repository),
            "--commit",
            "0" * 40,
        )
        self.assertEqual(different.returncode, 1)
        self.assertIn("repository HEAD differs", different.stderr)

    def test_publish_refuses_ignored_worktree_residue(self) -> None:
        ignore = self.repository / ".gitignore"
        ignore.write_text("*.ignored\n", encoding="utf-8")
        self.git("add", ".gitignore")
        self.git("commit", "--quiet", "-m", "bind ignored-path policy")
        self.commit = self.git("rev-parse", "HEAD").stdout.strip()
        (self.repository / "unreviewed.ignored").write_text(
            "unreviewed\n", encoding="utf-8"
        )
        self.assertEqual(
            self.git(
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ).stdout,
            "",
        )
        result = self.invoke(
            "publish",
            "--repository-root",
            os.fspath(self.repository),
            "--commit",
            self.commit,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("tracked, untracked, or ignored residue", result.stderr)

    def test_publish_compares_worktree_bytes_with_exact_head_blob(self) -> None:
        relative = self.helper.relative_to(self.repository)
        self.git("update-index", "--assume-unchanged", os.fspath(relative))
        self.helper.write_text("raise SystemExit(97)\n", encoding="utf-8")
        self.assertEqual(
            self.git("status", "--porcelain=v1", "--untracked-files=all").stdout,
            "",
        )
        result = self.invoke(
            "publish",
            "--repository-root",
            os.fspath(self.repository),
            "--commit",
            self.commit,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("differs byte-for-byte from HEAD blob", result.stderr)

    def test_publish_is_no_replace_and_never_reuses_a_commit_destination(self) -> None:
        destination = self.publish()
        original = (destination / "bootstrap-manifest.json").read_bytes()
        repeated = self.invoke(
            "publish",
            "--repository-root",
            os.fspath(self.repository),
            "--commit",
            self.commit,
        )
        self.assertEqual(repeated.returncode, 1)
        self.assertIn("already exists", repeated.stderr)
        self.assertEqual(
            (destination / "bootstrap-manifest.json").read_bytes(), original
        )

    def test_exec_runs_only_the_revalidated_published_helper(self) -> None:
        destination = self.publish()
        result = self.invoke(
            "exec",
            "--commit",
            self.commit,
            "--",
            "verify",
            "fixture-id",
            publisher=destination / self.publisher.name,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.executed_helper_marker.read_text(encoding="utf-8").strip(),
            os.fspath(destination / self.helper.name),
        )

    def test_exec_accepts_only_exact_public_command_shapes(self) -> None:
        destination = self.publish()
        valid: tuple[tuple[str, ...], ...] = (
            ("prepare", "fixture-id", "--pre-checkpoint-state", "/state.json"),
            ("run", "fixture-id"),
            ("recover", "fixture-id"),
            ("verify", "fixture-id"),
        )
        for command in valid:
            with self.subTest(valid=command):
                self.executed_helper_marker.unlink(missing_ok=True)
                result = self.invoke(
                    "exec",
                    "--commit",
                    self.commit,
                    "--",
                    *command,
                    publisher=destination / self.publisher.name,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(self.executed_helper_marker.is_file())

        invalid: tuple[tuple[str, ...], ...] = (
            ("prepare", "fixture-id"),
            ("prepare", "fixture-id", "--target", "dev-python/jsonschema"),
            ("prepare", "fixture-id", "--pre-checkpoint-state", "relative"),
            ("run", "fixture-id", "extra"),
            ("recover", "--fixture-root"),
            ("verify", "bad/id"),
        )
        for command in invalid:
            with self.subTest(invalid=command):
                self.executed_helper_marker.unlink(missing_ok=True)
                result = self.invoke(
                    "exec",
                    "--commit",
                    self.commit,
                    "--",
                    *command,
                    publisher=destination / self.publisher.name,
                )
                self.assertEqual(result.returncode, 1)
                self.assertFalse(self.executed_helper_marker.exists())

    def test_exec_refuses_published_payload_tampering(self) -> None:
        destination = self.publish()
        published_helper = destination / self.helper.name
        published_helper.write_text("raise SystemExit(0)\n", encoding="utf-8")
        result = self.invoke(
            "exec",
            "--commit",
            self.commit,
            "--",
            "verify",
            "fixture-id",
            publisher=destination / self.publisher.name,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("identity changed", result.stderr)
        self.assertFalse(self.executed_helper_marker.exists())

    def test_verify_refuses_manifest_source_mapping_or_blob_sha256_tampering(self) -> None:
        destination = self.publish()
        manifest_path = destination / "bootstrap-manifest.json"
        original: dict[str, Any] = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        for label in ("top-level schema", "source mapping", "blob SHA-256"):
            with self.subTest(label=label):
                value: dict[str, Any] = json.loads(json.dumps(original))
                if label == "top-level schema":
                    value["unreviewed"] = True
                elif label == "source mapping":
                    value["files"][0]["source"]["path"] = value["files"][1][
                        "source"
                    ]["path"]
                else:
                    value["files"][0]["git"]["blob_sha256"] = "0" * 64
                manifest_path.write_text(
                    json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                result = self.invoke(
                    "verify",
                    "--commit",
                    self.commit,
                    publisher=destination / self.publisher.name,
                )
                self.assertEqual(result.returncode, 1)
        manifest_path.write_text(
            json.dumps(original, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_exec_rejects_internal_or_unknown_helper_commands(self) -> None:
        destination = self.publish()
        for command in ("__barrier", "arbitrary"):
            with self.subTest(command=command):
                self.executed_helper_marker.unlink(missing_ok=True)
                result = self.invoke(
                    "exec",
                    "--commit",
                    self.commit,
                    "--",
                    command,
                    "fixture-id",
                    publisher=destination / self.publisher.name,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("accepts only the public", result.stderr)
                self.assertFalse(self.executed_helper_marker.exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import shutil
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[3]
CHECKPOINT = (
    REPOSITORY
    / "scripts"
    / "optimization"
    / "recovery"
    / "create-binpkg-checkpoint.sh"
)

TOOLS = (
    "bash",
    "chmod",
    "chown",
    "cmp",
    "cp",
    "date",
    "env",
    "find",
    "findmnt",
    "flock",
    "install",
    "jq",
    "ln",
    "mount",
    "readlink",
    "setsid",
    "sha256sum",
    "sleep",
    "sort",
    "stat",
    "sync",
    "timeout",
    "umount",
    "unshare",
    "zstd",
)


FAKE_PORTAGE = r'''#!/usr/bin/python3
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


def parse_records(packages: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    cpv = ""
    archive = ""
    for line in packages.read_text(encoding="utf-8").splitlines() + [""]:
        if line.startswith("CPV: "):
            cpv = line[5:]
        elif line.startswith("PATH: "):
            archive = line[6:]
        elif not line and cpv:
            records.append((cpv, archive))
            cpv = archive = ""
    return records


def write_records(pkgdir: Path, records: list[tuple[str, str]]) -> None:
    stanzas = [f"PACKAGES: {len(records)}\nVERSION: 0"]
    for cpv, relative in sorted(records):
        data = (pkgdir / relative).read_bytes()
        stanzas.append(
            "\n".join(
                (
                    f"CPV: {cpv}",
                    f"PATH: {relative}",
                    f"SIZE: {len(data)}",
                    f"MD5: {hashlib.md5(data).hexdigest()}",
                    f"SHA1: {hashlib.sha1(data).hexdigest()}",
                )
            )
        )
    temporary = pkgdir / "Packages.new"
    temporary.write_text("\n\n".join(stanzas) + "\n", encoding="utf-8")
    os.replace(temporary, pkgdir / "Packages")


root = Path(os.environ["HOME"]).parent
control = root / "control"
invoked = Path(sys.argv[0]).name
with (control / "frontends.log").open("a", encoding="utf-8") as stream:
    stream.write(invoked + "\n")

if invoked == "quickpkg":
    if (control / "hang-quickpkg").exists():
        import subprocess
        import time

        child = subprocess.Popen(["/usr/bin/python3", "-c", "import time; time.sleep(300)"])
        (control / "active-pids").write_text(
            f"{os.getpid()}\n{child.pid}\n", encoding="utf-8"
        )
        time.sleep(300)
    pkgdir = Path(os.environ["PKGDIR"])
    records = parse_records(pkgdir / "Packages")
    known = {cpv for cpv, _ in records}
    for atom in (arg for arg in sys.argv[1:] if arg.startswith("=")):
        cpv = atom[1:]
        if cpv in known:
            continue
        category, package_version = cpv.split("/", 1)
        package = package_version.rsplit("-", 1)[0]
        relative = f"{category}/{package}/{package_version}.gpkg.tar"
        archive = pkgdir / relative
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(("fake-gpkg:" + cpv).encode())
        records.append((cpv, relative))
        known.add(cpv)
    write_records(pkgdir, records)
    if (control / "mutate-vdb").exists():
        target = root / "var/db/pkg/cat/new-2/BUILD_TIME"
        old = target.read_bytes()
        target.write_bytes(b"X" * len(old))
    raise SystemExit(0)

if invoked == "emaint":
    raise SystemExit(0)

if invoked == "portageq":
    if sys.argv[1:] != ["envvar", "FEATURES"]:
        raise SystemExit("unexpected portageq arguments")
    if (control / "mutate-vdb-late").exists():
        target = root / "var/db/pkg/cat/new-2/BUILD_TIME"
        target.write_bytes(b"L" * len(target.read_bytes()))
    print("sandbox userpriv network-sandbox")
    raise SystemExit(0)

raise SystemExit(f"unexpected python-exec frontend: {invoked}")
'''


FAKE_VERIFIER = r'''from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--snapshot", required=True, type=Path)
parser.add_argument("--vdb", required=True, type=Path)
parser.add_argument("--zstd", required=True)
parser.add_argument("--format", required=True)
parser.add_argument("--validate-gpkg", action="store_true")
args = parser.parse_args()
root = Path(os.environ["HOME"]).parent
control = root / "control"
with (control / "verifier-calls.tsv").open("a", encoding="utf-8") as stream:
    stream.write(f"{args.snapshot}\t{int(args.validate_gpkg)}\t{args.zstd}\n")

if (control / "create-cache-collision").exists() and args.snapshot.name == "source":
    collision = root / "var/cache/gentoo-optimization/binpkgs/snapshot-fixture"
    collision.mkdir(mode=0o700)
    (collision / "sentinel").write_text("do-not-replace", encoding="utf-8")

records: list[tuple[str, str]] = []
cpv = ""
archive_path = ""
for line in (args.snapshot / "Packages").read_text(encoding="utf-8").splitlines():
    if line.startswith("CPV: "):
        cpv = line[5:]
    elif line.startswith("PATH: "):
        archive_path = line[6:]
    elif not line and cpv:
        records.append((cpv, archive_path))
        cpv = archive_path = ""
if cpv:
    records.append((cpv, archive_path))
record_cpvs = [item[0] for item in records]
live = sorted(
    str(path.relative_to(args.vdb))
    for category in args.vdb.iterdir()
    if category.is_dir()
    for path in category.iterdir()
    if path.is_dir()
)
missing = sorted(set(live) - set(record_cpvs))
issues = [
    {"code": "live_cpv_missing_archive", "cpv": cpv,
     "message": "live CPV has no indexed archive"}
    for cpv in missing
]

is_durable_final = args.snapshot.name == "critical-fixture"
if is_durable_final and (control / "replace-selector").exists():
    external = root / "var/lib/gentoo-optimization/recovery/binpkgs/external"
    external.mkdir(mode=0o700, exist_ok=True)
    (external / "Packages").write_text(
        (root / "var/lib/gentoo-optimization/recovery/binpkgs/source/Packages").read_text(),
        encoding="utf-8",
    )
    selector = root / "var/cache/gentoo-optimization/binpkgs/critical-current"
    temporary = selector.with_name("external-selector.partial")
    temporary.symlink_to(external)
    os.replace(temporary, selector)

injected = is_durable_final and (control / "fail-durable-final").exists()
if injected:
    issues.append({"code": "fixture_injected_failure", "message": "injected"})

errors = len(issues)
validated = len(records) if args.validate_gpkg else 0
report = {
    "schema_version": 1,
    "status": "pass" if errors == 0 else "fail",
    "inputs": {
        "snapshot": str(args.snapshot),
        "vdb": str(args.vdb),
        "validate_gpkg": args.validate_gpkg,
        "zstd": args.zstd,
    },
    "counts": {
        "errors": errors,
        "extra_indexed_archives": 0,
        "gpkg_archives_found": len(records),
        "gpkg_archives_indexed": len(records),
        "gpkg_archives_validated": validated,
        "image_tar_zst_streams_tested": validated,
        "indexed_records": len(records),
        "indexed_unique_cpvs": len(set(record_cpvs)),
        "indexed_unique_paths": len(records),
        "live_cpvs": len(live),
        "missing_live_cpvs": len(missing),
        "unindexed_gpkg_archives": 0,
    },
    "coverage": {
        "duplicate_live_cpvs": {},
        "extra_indexed_archives": [],
        "missing_live_cpvs": missing,
        "unindexed_gpkg_archives": [],
    },
    "archives": [
        {
            "cpv": cpv,
            "path": archive_path,
            "size": {
                "actual": (args.snapshot / archive_path).stat().st_size,
                "expected": str((args.snapshot / archive_path).stat().st_size),
            },
            "md5": {"actual": "0" * 32, "expected": "0" * 32},
            "sha1": {"actual": "0" * 40, "expected": "0" * 40},
            "gpkg": {
                "status": "pass" if args.validate_gpkg else "not_requested",
                "image_tar_zst_streams": 1 if args.validate_gpkg else 0,
                "zstd_streams_tested": 1 if args.validate_gpkg else 0,
            },
        }
        for cpv, archive_path in records
    ],
    "issues": issues,
}
print(json.dumps(report, indent=2, sort_keys=True))
raise SystemExit(0 if not issues else 1)
'''


FAKE_MV = r'''#!/usr/bin/python3
from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
control = root / "control"
destination = Path(sys.argv[-1]) if len(sys.argv) >= 3 else Path("/")
if "--exchange" in sys.argv:
    left = Path(sys.argv[-2])
    right = Path(sys.argv[-1])
    if (control / "kill-before-exchange").exists():
        os.kill(os.getppid(), 9)
        raise SystemExit(137)
    if (control / "race-selector-at-exchange").exists():
        (control / "race-selector-at-exchange").unlink()
        external = root / "var/lib/gentoo-optimization/recovery/binpkgs/external-near-cas"
        external.mkdir(mode=0o700, exist_ok=True)
        (external / "Packages").write_text(
            (root / "var/lib/gentoo-optimization/recovery/binpkgs/source/Packages").read_text(),
            encoding="utf-8",
        )
        replacement = right.with_name("near-cas-external.partial")
        replacement.symlink_to(external)
        os.replace(replacement, right)
    temporary = left.with_name(left.name + ".fixture-exchange")
    os.rename(left, temporary)
    os.rename(right, left)
    os.rename(temporary, right)
    if (control / "kill-after-exchange").exists():
        os.kill(os.getppid(), 9)
    raise SystemExit(0)
if (
    (control / "mv-noop-cache").exists()
    and "--no-clobber" in sys.argv
    and destination.name == "snapshot-fixture"
):
    raise SystemExit(0)
real = Path(__file__).with_name("mv.real")
os.execv(real, [str(real), *sys.argv[1:]])
'''


FAKE_MOUNT_TOOLS = r'''#!/usr/bin/python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
control = root / "control"
invoked = Path(sys.argv[0]).name
marker = control / "make-conf-overlay-active"
if invoked == "mount":
    source = Path(sys.argv[-2])
    target = Path(sys.argv[-1])
    backup = target.with_name(target.name + ".fixture-unmounted")
    os.rename(target, backup)
    os.link(source, target)
    marker.write_text(str(source) + "\n" + str(target) + "\n" + str(backup) + "\n", encoding="utf-8")
    raise SystemExit(0)
if invoked == "umount":
    source, target_text, backup_text = marker.read_text().splitlines()
    target = Path(target_text)
    backup = Path(backup_text)
    target.unlink()
    os.rename(backup, target)
    marker.unlink(missing_ok=True)
    raise SystemExit(0)
if invoked == "findmnt":
    target = Path(sys.argv[sys.argv.index("--target") + 1])
    if marker.exists():
        source, mounted_target, _backup = marker.read_text().splitlines()
        filesystem = {
            "target": mounted_target,
            "source": source,
            "fstype": "none",
            "options": "rw,bind",
        }
    else:
        filesystem = {
            "target": str(root),
            "source": "/dev/fake",
            "fstype": "xfs",
            "options": "rw",
        }
    print(json.dumps({"filesystems": [filesystem]}, sort_keys=True))
    raise SystemExit(0)
raise SystemExit("unexpected mount helper frontend")
'''


FAKE_FIND = r'''#!/usr/bin/python3
from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
marker = root / "control/find-fail-once"
if marker.exists():
    marker.unlink()
    raise SystemExit(73)
real = Path(__file__).with_name("find.real")
os.execv(real, [str(real), *sys.argv[1:]])
'''


FAKE_UNSHARE = r'''#!/usr/bin/python3
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

arguments = list(sys.argv[1:])
while arguments and arguments[0] != "--":
    arguments.pop(0)
if not arguments or arguments.pop(0) != "--" or not arguments:
    raise SystemExit("unexpected fake unshare arguments")

child = subprocess.Popen(arguments, start_new_session=True)
terminating = False

def terminate(signum: int, _frame: object) -> None:
    global terminating
    terminating = True
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)
signal.signal(signal.SIGHUP, terminate)
while child.poll() is None:
    time.sleep(0.01)
if terminating:
    raise SystemExit(128 + signal.SIGTERM)
raise SystemExit(child.returncode)
'''


class CheckpointFixture:
    def __init__(self, root: Path, *, extra_live_cpv: str | None = None) -> None:
        self.root = root
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.control = root / "control"
        self.vdb = root / "var/db/pkg"
        self.cache_parent = root / "var/cache/gentoo-optimization/binpkgs"
        self.durable_parent = root / "var/lib/gentoo-optimization/recovery/binpkgs"
        self.report_parent = root / "var/lib/gentoo-optimization/reports"
        self.state_parent = root / "var/lib/gentoo-optimization/state/project"
        self.lock = self.state_parent / "binpkg-checkpoint.lock"
        self.source = self.durable_parent / "source"
        self.selector = self.cache_parent / "critical-current"
        self.tool_root = root / "tools"
        self.script = root / "bootstrap/create-binpkg-checkpoint.sh"
        self.verifier = self.script.parent / "verify-binpkg-snapshot.py"
        root.chmod(0o700)
        for directory in (
            self.control,
            self.vdb,
            self.cache_parent,
            self.durable_parent,
            self.report_parent,
            self.state_parent,
            self.tool_root / "usr/bin",
            self.tool_root / "usr/lib/python-exec",
            self.tool_root / "usr/sbin",
            self.tool_root / "sbin",
            self.tool_root / "bin",
            self.script.parent,
            root / "root",
            root / "etc/portage",
            root / "run/gentoo-optimization",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (root / "run/gentoo-optimization").chmod(0o750)
        for lock_name in ("framework-install.lock", "project.lock", "generation.lock"):
            lock = root / "run/gentoo-optimization" / lock_name
            lock.touch(mode=0o640)
            lock.chmod(0o640)
        self._install_tools()
        shutil.copy2(CHECKPOINT, self.script)
        self.script.chmod(0o755)
        self.verifier.write_text(FAKE_VERIFIER, encoding="utf-8")
        self.verifier.chmod(0o755)
        (root / "etc/portage/make.conf").write_text(
            'FEATURES="sandbox userpriv parallel-install"\n', encoding="utf-8"
        )
        self._install_cpv("cat/base-1")
        self._install_cpv("cat/new-2")
        if extra_live_cpv:
            self._install_cpv(extra_live_cpv)
        self.source.mkdir(mode=0o700)
        archive = self.source / "cat/base/base-1.gpkg.tar"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"fake-gpkg:cat/base-1")
        self._write_source_packages(archive)
        self.selector.symlink_to(self.source)

    def _install_tools(self) -> None:
        destination = self.tool_root / "usr/bin"
        for name in TOOLS:
            source = Path("/usr/bin") / name
            if not source.exists():
                raise unittest.SkipTest(f"required fixture host tool is absent: {source}")
            shutil.copy2(source.resolve(), destination / name)
            (destination / name).chmod(0o755)
        python = Path("/usr/bin/python3")
        if not python.exists():
            raise unittest.SkipTest("/usr/bin/python3 is absent")
        shutil.copy2(python.resolve(), destination / "python3")
        (destination / "python3").chmod(0o755)

        shutil.copy2(Path("/usr/bin/mv").resolve(), destination / "mv.real")
        (destination / "mv.real").chmod(0o755)
        (destination / "mv").write_text(FAKE_MV, encoding="utf-8")
        (destination / "mv").chmod(0o755)
        shutil.copy2(Path("/usr/bin/find").resolve(), destination / "find.real")
        (destination / "find.real").chmod(0o755)
        (destination / "find").write_text(FAKE_FIND, encoding="utf-8")
        (destination / "find").chmod(0o755)
        for name in ("findmnt", "mount", "umount"):
            (destination / name).write_text(FAKE_MOUNT_TOOLS, encoding="utf-8")
            (destination / name).chmod(0o755)
        (destination / "unshare").write_text(FAKE_UNSHARE, encoding="utf-8")
        (destination / "unshare").chmod(0o755)

        dispatcher = self.tool_root / "usr/lib/python-exec/python-exec2"
        dispatcher.write_text(FAKE_PORTAGE, encoding="utf-8")
        dispatcher.chmod(0o755)
        (destination / "quickpkg").symlink_to("../lib/python-exec/python-exec2")
        (destination / "emaint").symlink_to("../lib/python-exec/python-exec2")
        (destination / "portageq").symlink_to("../lib/python-exec/python-exec2")

    def _install_cpv(self, cpv: str) -> None:
        package = self.vdb / cpv
        package.mkdir(parents=True)
        (package / "BUILD_TIME").write_text("1234567890\n", encoding="utf-8")

    def _write_source_packages(self, archive: Path) -> None:
        data = archive.read_bytes()
        relative = archive.relative_to(self.source)
        text = textwrap.dedent(
            f"""\
            PACKAGES: 1
            VERSION: 0

            CPV: cat/base-1
            PATH: {relative}
            SIZE: {len(data)}
            MD5: {hashlib.md5(data).hexdigest()}
            SHA1: {hashlib.sha1(data).hexdigest()}
            """
        )
        (self.source / "Packages").write_text(text, encoding="utf-8")

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256((self.source / "Packages").read_bytes()).hexdigest()

    def command(self, *atoms: str, identifier: str = "fixture") -> list[str]:
        return [
            str(self.tool_root / "usr/bin/bash"),
            str(self.script),
            "--fixture-mode",
            "--fixture-root",
            str(self.root),
            "--fixture-owner",
            f"{self.uid}:{self.gid}",
            "--tool-root",
            str(self.tool_root),
            "--expected-source-target",
            str(self.source),
            "--expected-source-packages-sha256",
            self.source_sha256,
            "--expected-verifier-sha256",
            hashlib.sha256(self.verifier.read_bytes()).hexdigest(),
            identifier,
            *(atoms or ("=cat/new-2",)),
        ]

    def run(self, *atoms: str, identifier: str = "fixture") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command(*atoms, identifier=identifier),
            cwd="/",
            env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )

    def marker(self, name: str) -> None:
        (self.control / name).touch()


class CreateBinpkgCheckpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = CheckpointFixture(Path(self.temporary.name).resolve())
        self.old_selector_inode = self.fixture.selector.lstat().st_ino

    def assert_selector_unchanged(self) -> None:
        self.assertEqual(os.readlink(self.fixture.selector), str(self.fixture.source))
        self.assertEqual(self.fixture.selector.lstat().st_ino, self.old_selector_inode)

    def report(self) -> Path:
        return self.fixture.report_parent / "checkpoint-fixture"

    def test_success_is_exact_journaled_and_activates_last(self) -> None:
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS: checkpoint=fixture live_cpvs=2", result.stdout)
        cache = self.fixture.cache_parent / "snapshot-fixture"
        durable = self.fixture.durable_parent / "critical-fixture"
        self.assertTrue(cache.is_dir())
        self.assertTrue(durable.is_dir())
        self.assertEqual(os.readlink(self.fixture.selector), str(durable))

        state = json.loads(
            (self.fixture.state_parent / "binpkg-checkpoint-fixture.json").read_text()
        )
        self.assertEqual(
            state["status"],
            "verified-final-freeze-held-selector-activation-pending",
        )
        self.assertEqual(state["live_cpvs"], 2)
        self.assertEqual(state["pending_total"], 1)
        self.assertFalse(state["offline_restoration_tested"])
        self.assertEqual(state["activation"]["selector"], str(self.fixture.selector))
        self.assertTrue((self.report() / "evidence-manifest.sha256").is_file())
        self.assertTrue((self.report() / "journal-preactivation-manifest.sha256").is_file())
        self.assertTrue((self.report() / "activation-intent.json").is_file())
        self.assertTrue((self.report() / "activation-evidence-manifest.sha256").is_file())

        phases = [
            json.loads(path.read_text())["phase"]
            for path in sorted((self.report() / "journal").glob("*.json"))
        ]
        self.assertEqual(phases[-1], "prepared-for-final-freeze")
        self.assertLess(phases.index("durable-published"), phases.index("prepared-for-final-freeze"))

        calls = [line.split("\t") for line in (self.fixture.control / "verifier-calls.tsv").read_text().splitlines()]
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(validate == "1" for _, validate, _ in calls))
        self.assertIn([str(cache), "1", str(self.fixture.tool_root / "usr/bin/zstd")], calls)
        self.assertIn([str(durable), "1", str(self.fixture.tool_root / "usr/bin/zstd")], calls)
        self.assertEqual(
            (self.fixture.control / "frontends.log").read_text().splitlines(),
            ["quickpkg", "emaint", "emaint", "portageq"],
        )
        tools = (self.report() / "tool-identities.tsv").read_text()
        self.assertIn(str(self.fixture.tool_root / "usr/bin/quickpkg"), tools)
        self.assertIn("python-exec2", tools)
        self.assertFalse((self.fixture.control / "make-conf-overlay-active").exists())

    def test_duplicate_and_nonexact_atoms_are_rejected_without_activation(self) -> None:
        for atoms in (
            ("cat/new-2",),
            ("=cat/new-2:0",),
            ("=cat/new-2", "=cat/new-2"),
        ):
            with self.subTest(atoms=atoms):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = CheckpointFixture(Path(directory).resolve())
                    inode = fixture.selector.lstat().st_ino
                    result = fixture.run(*atoms)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(os.readlink(fixture.selector), str(fixture.source))
                    self.assertEqual(fixture.selector.lstat().st_ino, inode)

    def test_requested_atoms_must_equal_complete_source_live_delta(self) -> None:
        result = self.fixture.run("=cat/base-1", "=cat/new-2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("complete exact source-to-live CPV delta", result.stderr)
        self.assert_selector_unchanged()
        self.assertIn("selector_unchanged=true", (self.report() / "failure.txt").read_text())

    def test_content_hash_detects_same_size_vdb_mutation(self) -> None:
        self.fixture.marker("mutate-vdb")
        before = (self.fixture.vdb / "cat/new-2/BUILD_TIME").stat()
        result = self.fixture.run()
        after = (self.fixture.vdb / "cat/new-2/BUILD_TIME").stat()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before.st_size, after.st_size)
        self.assertIn("VDB content or metadata changed", result.stderr)
        self.assert_selector_unchanged()
        self.assertFalse((self.fixture.cache_parent / "snapshot-fixture").exists())

    def test_final_payload_failure_leaves_selector_and_state_untouched(self) -> None:
        self.fixture.marker("fail-durable-final")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact GPKG payload verification failed", result.stderr)
        self.assert_selector_unchanged()
        self.assertFalse((self.fixture.state_parent / "binpkg-checkpoint-fixture.json").exists())
        self.assertTrue((self.fixture.durable_parent / "critical-fixture").is_dir())
        self.assertIn("selector_unchanged=true", (self.report() / "failure.txt").read_text())

    def test_no_clobber_zero_status_without_move_is_rejected_by_inode_postconditions(self) -> None:
        self.fixture.marker("mv-noop-cache")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("left staging source in place", result.stderr)
        self.assert_selector_unchanged()
        self.assertFalse((self.fixture.cache_parent / "snapshot-fixture").exists())

    def test_preexisting_publication_collision_is_never_replaced(self) -> None:
        self.fixture.marker("create-cache-collision")
        result = self.fixture.run()
        collision = self.fixture.cache_parent / "snapshot-fixture"
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((collision / "sentinel").read_text(), "do-not-replace")
        self.assertIn("publication destination already exists", result.stderr)
        self.assert_selector_unchanged()

    def test_lost_selector_update_is_detected_and_not_overwritten(self) -> None:
        self.fixture.marker("replace-selector")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source selector identity changed (lost update)", result.stderr)
        self.assertTrue(os.readlink(self.fixture.selector).endswith("/external"))
        self.assertFalse(os.readlink(self.fixture.selector).endswith("/critical-fixture"))
        self.assertIn("selector_unchanged=false", (self.report() / "failure.txt").read_text())

    def test_near_exchange_lost_update_is_atomically_captured_and_restored(self) -> None:
        self.fixture.marker("race-selector-at-exchange")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("near-rename lost update and rolled it back", result.stderr)
        self.assertTrue(os.readlink(self.fixture.selector).endswith("/external-near-cas"))
        self.assertFalse(os.readlink(self.fixture.selector).endswith("/critical-fixture"))
        self.assertFalse((self.fixture.control / "make-conf-overlay-active").exists())

    def test_exclusive_lock_rejects_concurrent_transaction(self) -> None:
        self.fixture.lock.touch(mode=0o600)
        with self.fixture.lock.open("r+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another binpkg checkpoint transaction holds the lock", result.stderr)
        self.assert_selector_unchanged()

    def test_signal_terminates_active_process_group_and_preserves_selector_inode(self) -> None:
        self.fixture.marker("hang-quickpkg")
        process = subprocess.Popen(
            self.fixture.command(),
            cwd="/",
            env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        active = self.fixture.control / "active-pids"
        deadline = time.monotonic() + 20
        while not active.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not active.exists():
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            self.fail(f"tracked child did not become active: rc={process.returncode}\n{stdout}\n{stderr}")
        pids = [int(item) for item in active.read_text().splitlines()]
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 143, (stdout, stderr))
        for pid in pids:
            residue_deadline = time.monotonic() + 5
            while Path(f"/proc/{pid}").exists() and time.monotonic() < residue_deadline:
                time.sleep(0.05)
            self.assertFalse(Path(f"/proc/{pid}").exists(), f"tracked process survived: {pid}")
        self.assert_selector_unchanged()
        failure = (self.report() / "failure.txt").read_text()
        self.assertIn("status=143", failure)
        self.assertIn("selector_unchanged=true", failure)

    def test_source_target_and_digest_are_explicitly_bound(self) -> None:
        command = self.fixture.command()
        digest_index = command.index("--expected-source-packages-sha256") + 1
        command[digest_index] = "0" * 64
        result = subprocess.run(
            command,
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source Packages digest", result.stderr)
        self.assert_selector_unchanged()


if __name__ == "__main__":
    unittest.main()

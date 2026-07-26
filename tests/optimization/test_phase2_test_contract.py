from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPOSITORY_ROOT / "scripts/optimization/verify/phase2-test-contract.py"
UNITTEST_RUNNER = REPOSITORY_ROOT / "scripts/optimization/verify/run-unittest-suite.py"


class Phase2TestContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase2-test-contract.")
        self.repository = Path(self.temporary.name) / "repository"
        (self.repository / "tests/optimization").mkdir(parents=True)
        (self.repository / "scripts/optimization/verify").mkdir(parents=True)
        (self.repository / "optimization").mkdir()
        shutil.copy2(GENERATOR, self.repository / "scripts/optimization/verify/phase2-test-contract.py")
        shutil.copy2(UNITTEST_RUNNER, self.repository / "scripts/optimization/verify/run-unittest-suite.py")
        self.marker = self.repository / "executed.marker"
        driver = self.repository / "tests/run-optimization-tests.sh"
        driver.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "[[ ${1:-} == --contract-topology ]] || exit 2\n"
            "printf 'top-level\\tcore\\n'\n"
            "printf 'top-level\\tphase2-evidence-contract\\n'\n"
            "printf 'top-level\\tpython-unit-tests:tests/optimization\\n'\n"
            "printf 'shell\\tbash-syntax:tests/run-optimization-tests.sh\\n'\n"
            "printf 'unittest\\tpython-unit-tests:tests/optimization\\t"
            "tests/optimization\\ttest_*.py\\ttest_phase2_evidence.\\t\\n'\n"
            "printf 'unittest\\tphase2-evidence-contract\\t"
            "tests/optimization\\ttest_phase2_evidence.py\\t\\t\\n'\n",
            encoding="utf-8",
        )
        driver.chmod(0o755)
        (self.repository / "tests/optimization/test_main.py").write_text(
            textwrap.dedent(
                f"""
                from pathlib import Path
                import unittest

                class MainTests(unittest.TestCase):
                    def test_alpha(self):
                        Path({str(self.marker)!r}).write_text("executed", encoding="utf-8")

                    def test_beta(self):
                        Path({str(self.marker)!r}).write_text("executed", encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )
        (self.repository / "tests/optimization/test_phase2_evidence.py").write_text(
            "import unittest\n"
            "class EvidenceTests(unittest.TestCase):\n"
            "    def test_contract(self):\n"
            "        self.fail('identity discovery executed a test')\n",
            encoding="utf-8",
        )
        self.contract = self.repository / "optimization/phase2-authoritative-test-contract.json"
        self.contract.write_text(
            json.dumps(
                {
                    "schema": "gentoo-optimization-phase2-authoritative-test-contract-v1",
                    "manual_policy": {"preserved": True},
                    "top_level": {"exact_names": [], "prefix_groups": []},
                    "unittest_suites": [],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_tool(self, action: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                action,
                "--repository-root",
                str(self.repository),
                "--contract",
                str(self.contract),
                *extra,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_generation_is_sorted_deterministic_and_executes_no_tests(self) -> None:
        first = self.run_tool("generate")
        second = self.run_tool("generate")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertFalse(self.marker.exists())
        generated = json.loads(first.stdout)
        self.assertEqual(generated["manual_policy"], {"preserved": True})
        self.assertEqual(
            generated["top_level"]["exact_names"],
            ["core", "phase2-evidence-contract", "python-unit-tests:tests/optimization"],
        )
        suites = {item["test"]: item for item in generated["unittest_suites"]}
        self.assertEqual(suites["phase2-evidence-contract"]["expected_count"], 1)
        self.assertEqual(
            suites["python-unit-tests:tests/optimization"]["expected_count"], 2
        )
        shell_names = generated["top_level"]["prefix_groups"][0]["expected_names"]
        self.assertEqual(shell_names, ["bash-syntax:tests/run-optimization-tests.sh"])

    def test_check_reports_unified_diff_then_accepts_generated_contract(self) -> None:
        stale = self.run_tool("check")
        self.assertEqual(stale.returncode, 1)
        self.assertIn("--- ", stale.stderr)
        self.assertIn("+++ discovered-test-contract", stale.stderr)
        generated = self.run_tool("generate")
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.contract.write_text(generated.stdout, encoding="utf-8")
        current = self.run_tool("check")
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertIn("matches deterministic discovery", current.stdout)

    def test_import_failure_cannot_become_a_successful_failedtest_identity(self) -> None:
        (self.repository / "tests/optimization/test_import_failure.py").write_text(
            "raise RuntimeError('deliberate import failure')\n", encoding="utf-8"
        )
        result = self.run_tool("generate")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unittest discovery reported import/load failures", result.stderr)
        self.assertNotIn("_FailedTest", result.stdout)

    def test_import_hang_is_stopped_by_the_discovery_deadline(self) -> None:
        (self.repository / "tests/optimization/test_import_hang.py").write_text(
            "import time\ntime.sleep(60)\n", encoding="utf-8"
        )
        result = self.run_tool("generate", "--timeout-seconds", "1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("exceeded the bounded 1-second discovery deadline", result.stderr)

    def test_import_timeout_kills_spawned_process_group_descendant(self) -> None:
        child_pid = self.repository / "spawned-child.pid"
        child_ready = self.repository / "spawned-child.ready"
        (self.repository / "tests/optimization/test_import_descendant.py").write_text(
            textwrap.dedent(
                f"""
                from pathlib import Path
                import subprocess
                import sys
                import time

                child = subprocess.Popen([
                    sys.executable,
                    "-c",
                    "import signal,time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "open({str(child_ready)!r}, 'w').close(); time.sleep(60)",
                ])
                stat_fields = Path(f"/proc/{{child.pid}}/stat").read_text(
                    encoding="utf-8"
                ).rsplit(") ", 1)[1].split()
                Path({str(child_pid)!r}).write_text(
                    f"{{child.pid}} {{stat_fields[19]}}", encoding="utf-8"
                )
                for _ in range(200):
                    if Path({str(child_ready)!r}).exists():
                        break
                    time.sleep(0.01)
                else:
                    raise RuntimeError(
                        "spawned descendant did not reach readiness barrier"
                    )
                time.sleep(60)
                """
            ),
            encoding="utf-8",
        )
        # Discovery's deadline must leave room for the imported module to prove
        # that its SIGTERM-ignoring descendant is ready.  The PID/start identity
        # is published immediately after spawn, so a slow readiness marker can
        # never turn cleanup verification into a missing-PID race.
        result = self.run_tool("generate", "--timeout-seconds", "5")
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "exceeded the bounded 5-second discovery deadline", result.stderr
        )
        self.assertTrue(child_pid.is_file())
        self.assertTrue(child_ready.is_file())
        raw_pid, recorded_start_time = child_pid.read_text(encoding="utf-8").split()
        pid = int(raw_pid)
        for _ in range(200):
            try:
                stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            except FileNotFoundError:
                break
            fields = stat_line.rsplit(") ", 1)[1].split()
            if fields[19] != recorded_start_time or fields[0] in {"Z", "X", "x"}:
                break
            time.sleep(0.01)
        else:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
            self.fail(f"spawned discovery descendant survived cleanup: pid {pid}")


if __name__ == "__main__":
    unittest.main()

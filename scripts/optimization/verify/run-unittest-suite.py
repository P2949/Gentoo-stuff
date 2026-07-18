#!/usr/bin/env python3
"""Run unittest while publishing every result to the driver subtest ledger."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import traceback
import unittest
from typing import Any


STRUCTURED_FRAGMENT: Path | None = None


def clean(value: object) -> str:
    return " ".join(str(value).replace("\t", " ").splitlines()).strip() or "no detail"


class StructuredResult(unittest.TextTestResult):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if STRUCTURED_FRAGMENT is None:
            raise RuntimeError("GENTOO_OPT_SUBTEST_RESULTS is required")
        self.fragment = STRUCTURED_FRAGMENT
        self.recorded = 0
        self.skipped_required = 0
        self.skipped_diagnostic = 0
        self._subtest_index = 0

    def record(
        self,
        status: str,
        requirement: str,
        name: str,
        detail: object,
    ) -> None:
        row = "\t".join((status, requirement, clean(name), clean(detail))) + "\n"
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.fragment, flags)
        try:
            payload = row.encode("utf-8")
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("short write to structured subtest fragment")
                offset += written
        finally:
            os.close(descriptor)
        self.recorded += 1

    @staticmethod
    def test_name(test: unittest.case.TestCase) -> str:
        return f"python.{test.id()}"

    @staticmethod
    def error_detail(error: Any) -> str:
        return "".join(traceback.format_exception(*error))

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        super().addSuccess(test)
        self.record("PASS", "required", self.test_name(test), "unittest passed")

    def addFailure(self, test: unittest.case.TestCase, err: Any) -> None:
        super().addFailure(test, err)
        self.record("FAIL", "required", self.test_name(test), self.error_detail(err))

    def addError(self, test: unittest.case.TestCase, err: Any) -> None:
        super().addError(test, err)
        self.record("FAIL", "required", self.test_name(test), self.error_detail(err))

    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        diagnostic_prefix = "DIAGNOSTIC:"
        requirement = "diagnostic" if reason.startswith(diagnostic_prefix) else "required"
        detail = reason.removeprefix(diagnostic_prefix).strip() if requirement == "diagnostic" else reason
        self.record("SKIP", requirement, self.test_name(test), detail)
        if requirement == "diagnostic":
            self.skipped_diagnostic += 1
        else:
            self.skipped_required += 1

    def addExpectedFailure(self, test: unittest.case.TestCase, err: Any) -> None:
        super().addExpectedFailure(test, err)
        self.record(
            "PASS",
            "diagnostic",
            self.test_name(test),
            "unittest expected failure was observed",
        )

    def addUnexpectedSuccess(self, test: unittest.case.TestCase) -> None:
        super().addUnexpectedSuccess(test)
        self.record(
            "FAIL",
            "required",
            self.test_name(test),
            "unittest unexpectedly succeeded",
        )

    def addSubTest(
        self,
        test: unittest.case.TestCase,
        subtest: Any,
        err: Any,
    ) -> None:
        super().addSubTest(test, subtest, err)
        if err is None:
            return
        self._subtest_index += 1
        self.record(
            "FAIL",
            "required",
            f"{self.test_name(test)}.subtest-{self._subtest_index}",
            self.error_detail(err),
        )


class StructuredRunner(unittest.TextTestRunner):
    resultclass = StructuredResult


def main() -> int:
    global STRUCTURED_FRAGMENT
    if len(sys.argv) < 2:
        print("ERROR: pass unittest discovery or test-name arguments", file=sys.stderr)
        return 2
    raw_fragment = os.environ.get("GENTOO_OPT_SUBTEST_RESULTS", "")
    if not raw_fragment:
        print("ERROR: GENTOO_OPT_SUBTEST_RESULTS is required", file=sys.stderr)
        return 2
    STRUCTURED_FRAGMENT = Path(raw_fragment)
    # These variables are a private driver-to-result-channel contract, not
    # authority that test imports, test methods, or subprocesses may inherit.
    # The driver makes the authoritative decision after this process exits.
    for variable in (
        "GENTOO_OPT_AUTHORITATIVE",
        "GENTOO_OPT_SUBTEST_RESULTS",
        "GENTOO_OPT_TEST_CASE",
    ):
        os.environ.pop(variable, None)
    repository = Path(__file__).resolve().parents[3]
    if not repository.is_dir():
        print("ERROR: cannot resolve the repository root for unittest", file=sys.stderr)
        return 2
    sys.path.insert(0, os.fspath(repository))
    program = unittest.main(
        module=None,
        argv=[sys.argv[0], *sys.argv[1:]],
        testRunner=StructuredRunner,
        exit=False,
    )
    result = program.result
    if not isinstance(result, StructuredResult):
        print("ERROR: unittest did not use the structured result contract", file=sys.stderr)
        return 2
    print(
        "STRUCTURED-UNITTEST: "
        f"testsRun={result.testsRun} rows={result.recorded} "
        f"required_skip={result.skipped_required} "
        f"diagnostic_skip={result.skipped_diagnostic}"
    )
    if result.testsRun == 0:
        print("ERROR: unittest discovery executed zero tests", file=sys.stderr)
        return 2
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

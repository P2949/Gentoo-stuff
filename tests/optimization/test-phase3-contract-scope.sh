#!/usr/bin/env bash
set -euo pipefail
root=$(mktemp -d); trap 'rm -rf "$root"' EXIT
mkdir -p "$root/repository/tests/optimization" "$root/repository/scripts/optimization/verify" "$root/repository/optimization"
cp scripts/optimization/verify/phase2-test-contract.py "$root/repository/scripts/optimization/verify/"
cp scripts/optimization/verify/run-unittest-suite.py "$root/repository/scripts/optimization/verify/"
cat > "$root/repository/tests/run-optimization-tests.sh" <<'DRIVER'
#!/usr/bin/env bash
[[ ${1:-} == --contract-topology ]] || exit 2
printf 'top-level\tcore\n'
printf 'top-level\tpython-unit-tests:tests/optimization\n'
printf 'shell\tbash-syntax:tests/run-optimization-tests.sh\n'
printf 'shell\tbash-syntax:tests/optimization/new-phase3.sh\n'
printf 'unittest\tpython-unit-tests:tests/optimization\ttests/optimization\ttest_*.py\t\t\n'
DRIVER
chmod +x "$root/repository/tests/run-optimization-tests.sh"
printf '#!/usr/bin/env bash\nexit 0\n' > "$root/repository/tests/optimization/new-phase3.sh"
chmod +x "$root/repository/tests/optimization/new-phase3.sh"
cat > "$root/repository/tests/optimization/test_empty.py" <<'PY2'
import unittest
class Empty(unittest.TestCase):
    def test_empty(self):
        pass
PY2
cat > "$root/repository/optimization/phase2-authoritative-test-contract.json" <<'JSON'
{"schema":"gentoo-optimization-phase2-authoritative-test-contract-v1","top_level":{"exact_names":["core","python-unit-tests:tests/optimization"],"prefix_groups":[{"expected_count":1,"expected_names":["bash-syntax:tests/run-optimization-tests.sh"],"prefix":"bash-syntax:"}]},"unittest_suites":[{"expected_count":1,"subtest_names_sha256":"e9c29e01853b0f4502b53980acc821abe6b826c46f0f0087e76d51bbf9ab7695","test":"python-unit-tests:tests/optimization"}]}
JSON
python3 scripts/optimization/verify/phase2-test-contract.py check --repository-root "$root/repository" --contract "$root/repository/optimization/phase2-authoritative-test-contract.json" >/dev/null
echo 'PASS: additive Phase-3 shell discovery is accepted while frozen names remain enforced'

#!/usr/bin/env bash
set -euo pipefail

root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
printf 'profile-payload\n' >"$root/payload.profraw"
digest=$(sha256sum "$root/payload.profraw" | awk '{print $1}')
python3 - "$root" "$digest" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1]); digest = sys.argv[2]
wave = {'sha256': 'a' * 64, 'packages': [{'cpv': 'app/test-1'}]}
readiness = {'sha256': 'b' * 64}
receipt = {'record_type': 'profile-wave-transaction-receipt', 'schema_version': 1,
           'wave_sha256': wave['sha256'], 'readiness_sha256': readiness['sha256'],
           'package_count': 1, 'packages': ['app/test-1'], 'state': 'completed',
           'authorization': 'profile-payloads-collected',
           'profile_payloads': [{'cpv': 'app/test-1', 'path': str(root / 'payload.profraw'), 'sha256': digest}]}
receipt['sha256'] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
for name, value in [('wave.json', wave), ('readiness.json', readiness), ('receipt.json', receipt)]:
    (root / name).write_text(json.dumps(value))
PY
python3 scripts/optimization/pgo/verify-wave-receipt.py \
  --receipt "$root/receipt.json" --wave "$root/wave.json" --readiness "$root/readiness.json"
python3 - "$root" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1]) / 'receipt.json'; d = json.loads(p.read_text()); d['profile_payloads'] = []; d.pop('sha256', None); d['sha256'] = __import__('hashlib').sha256(json.dumps(d, sort_keys=True, separators=(',', ':')).encode()).hexdigest(); p.write_text(json.dumps(d))
PY
if python3 scripts/optimization/pgo/verify-wave-receipt.py --receipt "$root/receipt.json" --wave "$root/wave.json" --readiness "$root/readiness.json"; then
  echo 'FAIL: empty completed receipt was accepted' >&2
  exit 1
fi
echo 'PASS: empty completed receipt rejected'

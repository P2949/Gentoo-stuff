import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
TOOL = ROOT / "scripts/optimization/storage/compact-prerequisite-distfiles.py"

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp); txroot = base / "tx"; state = base / "state"; objects = base / "objects"
    txroot.mkdir(); state.mkdir()
    tx = txroot / "done"; (tx / "distfiles.staging").mkdir(parents=True)
    (tx / "distfiles.staging" / "source.tar.xz").write_bytes(b"same")
    (state / "jsonschema-prerequisite-done.success.json").write_text('{"phase":"success"}\n', encoding="utf-8")
    receipt = base / "receipt.json"
    subprocess.run([sys.executable, str(TOOL), "--transactions", str(txroot), "--state-dir", str(state), "--objects", str(objects), "--receipt", str(receipt), "--execute"], check=True)
    assert not (tx / "distfiles.staging" / "source.tar.xz").exists()
    assert len(list(objects.rglob("*"))) == 2
    assert json.loads(receipt.read_text())['retired'][0]['sha256']

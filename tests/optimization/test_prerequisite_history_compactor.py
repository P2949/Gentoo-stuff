import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
TOOL = ROOT / "scripts/optimization/storage/compact-prerequisite-history.py"

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp) / "transactions"; base.mkdir()
    terminal = base / "done"; terminal.mkdir()
    (terminal / "terminal.json").write_text('{"state":"completed"}\n', encoding="utf-8")
    (terminal / "tmp").mkdir(); (terminal / "tmp" / "build.log").write_text("log", encoding="utf-8")
    active = base / "active"; active.mkdir(); (active / "tmp").mkdir(); (active / "tmp" / "keep").write_text("x", encoding="utf-8")
    receipt = Path(tmp) / "retirement.json"
    result = subprocess.run([sys.executable, str(TOOL), "--transactions", str(base), "--receipt", str(receipt)], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["mode"] == "dry-run"
    assert (terminal / "tmp").exists()
    subprocess.run([sys.executable, str(TOOL), "--transactions", str(base), "--receipt", str(receipt), "--execute"], check=True)
    assert not (terminal / "tmp").exists()
    assert (active / "tmp").exists()

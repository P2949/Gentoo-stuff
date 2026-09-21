import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts/optimization/inventory/generate-live-inventory.py"


def test_malformed_contents_record_fails_closed(tmp_path: Path) -> None:
    vdb = tmp_path / "vdb" / "cat" / "pkg-1"
    vdb.mkdir(parents=True)
    (vdb / "CATEGORY").write_text("cat\n")
    (vdb / "CONTENTS").write_text("unknown /usr/bin/not-supported\n")
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"owned_directories": []}))
    output = tmp_path / "inventory.json"
    result = subprocess.run(
        ["python3", str(SCRIPT), "--vdb", str(tmp_path / "vdb"), "--previous", str(previous),
         "--output", str(output), "--generation-id", "fixture"],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "malformed CONTENTS" in result.stderr or "unsupported CONTENTS" in result.stderr
    assert not output.exists()


if __name__ == "__main__":
    import tempfile
    test_malformed_contents_record_fails_closed(Path(tempfile.mkdtemp()))
    print("PASS: malformed CONTENTS records fail closed")

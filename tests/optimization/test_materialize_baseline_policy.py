import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/materialize-baseline-policy.py"


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        policy = root / "generated"
        output = root / "overlay"
        policy.write_text(
            "# generated\n=app/z-2 optimization/generated/pgo.conf\n"
            "=app/a-1 optimization/generated/pgo.conf\n"
            "=app/z-2 optimization/generated/pgo.conf\n"
        )
        result = subprocess.run(
            ["python3", str(SCRIPT), "--generated-policy", str(policy), "--output", str(output)],
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert output.read_text() == (
            "=app/a-1 optimization/optimization-off-mode.conf\n"
            "=app/z-2 optimization/optimization-off-mode.conf\n"
        )
        policy.write_text("app/a-1 optimization/generated/pgo.conf\n")
        result = subprocess.run(
            ["python3", str(SCRIPT), "--generated-policy", str(policy), "--output", str(output)],
            text=True,
            capture_output=True,
        )
        assert result.returncode != 0 and "malformed" in result.stderr
    print("PASS: baseline policy materializer deduplicates and rejects malformed assignments")


if __name__ == "__main__":
    main()

"""End-to-end CLI checks with exact exit codes and private temporary output."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/ai-watermark-scrambler/scripts"
FIXTURES = ROOT / "tests/fixtures"


def run(script: str, *args: str | Path, expected: int = 0, stdout=None) -> None:
    command = [sys.executable, str(SCRIPTS / script), *(str(arg) for arg in args)]
    result = subprocess.run(command, stdout=stdout, check=False)
    if result.returncode != expected:
        raise RuntimeError(f"{script}: expected exit {expected}, got {result.returncode}")


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="watermark-smoke-") as directory:
            output = Path(directory)
            source = FIXTURES / "sample_watermarked.txt"
            run("inspect_text.py", source, expected=1)
            run("clean_text.py", source, "-o", output / "text.txt", "--stats")
            run("inspect_text.py", output / "text.txt")
            run("rewrite_text.py", source, "--backend", "print-prompt", stdout=subprocess.DEVNULL)
            for name in ("sample_ai.md", "sample_ai.html", "sample_meta.svg"):
                source = FIXTURES / name
                dest = output / name
                run("inspect_file.py", source, expected=1)
                run("clean_file.py", source, "-o", dest)
                run("inspect_file.py", dest)
    except (OSError, RuntimeError) as exc:
        print(f"smoke failed: {exc}", file=sys.stderr)
        return 1
    print("smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

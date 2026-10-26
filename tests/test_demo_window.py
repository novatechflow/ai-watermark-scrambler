"""Explicit context windows must control the experiment, including both mode."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))
from watermark_qa import run_pair


@pytest.mark.parametrize("mode,window", [("both", 4), ("context", 4), ("context", None)])
def test_cli_uses_requested_context_window(mode, window):
    command = [sys.executable, str(ROOT / "demo/watermark_qa.py"), "--mode", mode]
    if window is not None:
        command.extend(["--window", str(window)])
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    expected_window = window if window is not None else 1
    expected = run_pair(n=400, key="research-key", gamma=0.25, rate=0.3,
                        seed=2026, mode="context", window=expected_window)
    rows = [line for line in result.stdout.splitlines() if line.startswith("context/")]
    assert len(rows) == 1
    assert rows[0].startswith(f"context/{expected_window} ")
    assert rows[0].endswith(f"{expected['z_rewritten']:.2f}")
    assert ("unigram" in result.stdout) == (mode == "both")


def test_unigram_mode_rejects_an_unused_window():
    result = subprocess.run(
        [sys.executable, str(ROOT / "demo/watermark_qa.py"), "--mode", "unigram", "--window", "4"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "--window" in result.stderr
    assert not result.stdout

"""Empty experiments and invalid history windows are input errors."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))
from watermark_qa import VOCAB, generate, run_pair, z_score


@pytest.mark.parametrize("args", [
    ["--tokens", "0"], ["--tokens", "-5"],
    ["--mode", "context", "--window", "0"],
    ["--mode", "context", "--window", "-2"],
    ["--mode", "both", "--window", "0"],
])
def test_cli_rejects_invalid_sample_parameters(args):
    result = subprocess.run(
        [sys.executable, str(ROOT / "demo/watermark_qa.py"), *args],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert not result.stdout
    assert "Traceback" not in result.stderr
    assert "positive" in result.stderr


@pytest.mark.parametrize("n", [0, -5])
def test_direct_calls_reject_nonpositive_token_count(n):
    with pytest.raises(ValueError, match="positive"):
        generate(n, VOCAB, "key", "context", 0.25, 1)
    with pytest.raises(ValueError, match="positive"):
        run_pair(n=n, key="key", gamma=0.25, rate=0.3, seed=1, mode="context")


@pytest.mark.parametrize("window", [0, -2])
def test_direct_calls_reject_nonpositive_context_window(window):
    with pytest.raises(ValueError, match="positive"):
        generate(10, VOCAB, "key", "context", 0.25, 1, window)
    with pytest.raises(ValueError, match="positive"):
        z_score([VOCAB[0]], VOCAB, "key", "context", 0.25, window)


def test_internal_zero_window_is_valid_for_unigram():
    row = run_pair(n=10, key="key", gamma=0.25, rate=0.3, seed=1, mode="unigram", window=0)
    assert row["tokens"] == 10
    assert row["z_marked"] > 0

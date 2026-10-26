"""Degenerate green-list sizes must be rejected before statistical scoring."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))
from watermark_qa import VOCAB, effective_gamma, generate, z_score


@pytest.mark.parametrize("gamma", ["0.999", "1", "nan", "inf"])
def test_cli_rejects_degenerate_gamma(gamma):
    result = subprocess.run(
        [sys.executable, str(ROOT / "demo/watermark_qa.py"), "--gamma", gamma],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "gamma" in result.stderr
    assert "Traceback" not in result.stderr
    assert not result.stdout


def test_direct_generation_and_scoring_reject_all_green_vocabulary():
    with pytest.raises(ValueError, match="gamma"):
        generate(10, VOCAB, "key", "context", 0.999, 1)
    with pytest.raises(ValueError, match="gamma"):
        z_score([VOCAB[0]], VOCAB, "key", "context", 0.999)


def test_single_word_vocabulary_has_no_valid_green_fraction():
    with pytest.raises(ValueError, match="vocabulary"):
        effective_gamma(1, 0.25)

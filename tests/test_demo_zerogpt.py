"""Classifier comparisons must use complete samples, without paid test calls."""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo"))
import watermark_qa as qa


@pytest.mark.parametrize("length", [4, 15000])
def test_classifier_receives_the_complete_sample(length, monkeypatch):
    text = "é" * length
    sent = []
    report = {"documents": [{"verdict": "human", "ai_score": 12.47}]}

    def urlopen(request, timeout):
        sent.append(json.loads(request.data)["document"])
        return io.BytesIO(json.dumps(report).encode())

    monkeypatch.setattr(qa.urllib.request, "urlopen", urlopen)
    assert qa.zerogpt_detect(text, "offline-key") == report
    assert sent == [text]


def test_oversized_sample_is_rejected_before_request(monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("oversized text must not trigger an API call")

    monkeypatch.setattr(qa.urllib.request, "urlopen", unexpected_request)
    with pytest.raises(RuntimeError, match="15000"):
        qa.zerogpt_detect("x" * 15001, "offline-key")


def test_oversized_pair_skips_both_classifier_calls(monkeypatch, capsys):
    row = qa.run_pair(n=10, key="key", gamma=0.25, rate=0.3, seed=1, mode="context")
    row["marked_text"] = "x" * 15001
    row["rewritten_text"] = "short sample"
    monkeypatch.setattr(qa, "run_pair", lambda **kwargs: row)
    monkeypatch.setattr(sys, "argv", ["watermark_qa.py", "--mode", "context", "--zerogpt"])
    monkeypatch.setenv("ZEROGPT_API_KEY", "offline-key")

    def unexpected_call(*args, **kwargs):
        pytest.fail("an incomplete comparison must not trigger either API call")

    monkeypatch.setattr(qa, "zerogpt_detect", unexpected_call)
    assert qa.main() == 0
    output = capsys.readouterr().out
    assert "context/1" in output
    assert "ZeroGPT skipped" in output
    assert "15000" in output

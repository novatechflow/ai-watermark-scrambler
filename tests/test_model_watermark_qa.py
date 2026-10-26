"""Pipeline contracts without importing model packages or downloading weights."""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))
import model_watermark_qa as qa


def arguments(tmp_path, count=1):
    prompts = tmp_path / "prompts.json"
    prompts.write_text(json.dumps([{"id": f"p{i}", "prompt": "Write about a garden."} for i in range(count)]))
    return qa.parse_args(["--prompts", str(prompts), "--output", str(tmp_path / "report.json")])


class FakeModel:
    calls = []

    def __init__(self, args):
        self.calls = []
        FakeModel.calls = self.calls

    def generate(self, prompt, seed, watermarked):
        self.calls.append((prompt, seed, watermarked))
        if watermarked:
            text = "Source 24 beds.\u200b"
        elif len(self.calls) % 3 == 0:
            text = "There are 24 planted beds."
        else:
            text = "A control garden."
        return {"text": text, "generated_tokens": 20, "hit_token_limit": False}

    def information(self):
        return {"vocab_size": 1000, "resolved_revision": qa.REVISION,
                "inference_device": "cpu", "dtype": "torch.float32"}

    def detect(self, text):
        z = 10.0 if text.startswith("Source") else 2.0
        return {"z": z, "detected": z > 4, "tokens_scored": 20, "status": "scored"}


class FakeSemantics:
    def compare(self, source, rewritten):
        return {"cosine_similarity": 0.9, "all_tokens_embedded": True}


def test_complete_pipeline_uses_unwatermarked_controls_and_rewrite(tmp_path, monkeypatch):
    monkeypatch.setattr(qa.importlib.metadata, "version", lambda _: "test-version")
    args = arguments(tmp_path)
    report = qa.experiment(args, FakeModel, FakeSemantics)
    assert [call[2] for call in FakeModel.calls] == [False, True, False]
    assert FakeModel.calls[0][1] == FakeModel.calls[1][1]
    assert FakeModel.calls[2][1] == args.seeds[0] + 1
    assert str(args.hashing_key) not in FakeModel.calls[2][0]
    assert "Source 24 beds." in FakeModel.calls[2][0]
    assert "\u200b" not in FakeModel.calls[2][0]
    row = report["rows"][0]
    assert row["layer_a"]["removed_count"] == 1
    assert row["scores"]["marked"] == row["scores"]["layer_a"]
    assert report["summary"]["detected_then_undetected"] == 1
    assert report["summary"]["meaning_preservation"] == "manual_review_required"
    assert json.loads(args.output.read_text())["status"] == "complete"


def test_failure_preserves_completed_samples_and_marks_report_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(qa.importlib.metadata, "version", lambda _: "test-version")

    class FailingModel(FakeModel):
        def generate(self, *args):
            if len(self.calls) == 3:
                raise RuntimeError("generation failed")
            return super().generate(*args)

    args = arguments(tmp_path, count=2)
    with pytest.raises(RuntimeError, match="generation failed"):
        qa.experiment(args, FailingModel, FakeSemantics)
    report = json.loads(args.output.read_text())
    assert report["status"] == "failed"
    assert len(report["rows"]) == 1
    assert "summary" not in report


def test_similarity_coverage_rejects_missing_or_duplicate_tokens():
    qa.check_token_coverage(811, [254, 254, 254, 49])
    with pytest.raises(ValueError, match="every input token"):
        qa.check_token_coverage(811, [254])
    with pytest.raises(ValueError, match="every input token"):
        qa.check_token_coverage(811, [254, 254, 254, 254])


def test_semantic_failure_preserves_generated_text_and_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(qa.importlib.metadata, "version", lambda _: "test-version")

    class FailingSemantics:
        def compare(self, *args):
            raise RuntimeError("semantic scorer failed")

    args = arguments(tmp_path)
    with pytest.raises(RuntimeError, match="semantic scorer failed"):
        qa.experiment(args, FakeModel, FailingSemantics)
    report = json.loads(args.output.read_text())
    assert report["status"] == "failed"
    assert report["rows"][0]["status"] == "generated"
    assert report["rows"][0]["texts"]["marked"]
    assert report["rows"][0]["scores"]["marked"]["z"] == 10


def test_numeric_drift_and_identical_output_are_not_called_preserved():
    same = qa.diagnostics("24 beds", "24 beds")
    assert same["identical_text"]
    changed = qa.diagnostics("24 beds and 50,000 seeds", "25 beds and 50000 seeds")
    assert changed["missing_numbers"] == ["24"]
    assert changed["added_numbers"] == ["25"]
    assert changed["meaning_preservation"] == "manual_review_required"


def test_failed_report_serialization_preserves_existing_file(tmp_path):
    path = tmp_path / "report.json"
    path.write_text("original")
    with pytest.raises(ValueError):
        qa.save_report(path, {"z": float("nan")})
    assert path.read_text() == "original"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("flags", [
    ["--window", "0"], ["--gamma", "nan"], ["--bias", "inf"],
    ["--seeds", "1", "1"], ["--seeds", "-1"], ["--model", "custom/model"],
    ["--max-new-tokens", "10"],
])
def test_invalid_arguments_fail_before_loading_models(flags):
    with pytest.raises(SystemExit) as error:
        qa.parse_args(flags)
    assert error.value.code == 2


def test_help_works_in_the_lightweight_environment():
    result = subprocess.run(
        [sys.executable, str(ROOT / "demo/model_watermark_qa.py"), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--revision" in result.stdout


def test_reference_report_scores_and_summary_are_internally_consistent():
    report = json.loads((ROOT / "demo/example_model_run.json").read_text())
    assert report["status"] == "complete"
    gamma = report["watermark"]["gamma"]
    threshold = report["watermark"]["threshold"]
    for row in report["rows"]:
        for score in row["scores"].values():
            count = score["tokens_scored"]
            expected = (score["green_tokens"] - gamma * count) / math.sqrt(count * gamma * (1 - gamma))
            assert score["z"] == pytest.approx(expected)
            assert score["detected"] == (score["z"] > threshold)
        assert row["quality"]["meaning_preservation"] == "manual_review_required"
    # Check accounting, never require an attack to succeed or quality to pass.
    assert report["summary"] == qa.summarize(report["rows"])

"""Smoke checks fail on unexpected inspector statuses and honor PYTHON."""

import subprocess
import sys
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))


@pytest.mark.parametrize("status", [0, 2])
def test_smoke_rejects_unexpected_dirty_inspection(status, monkeypatch, tmp_path):
    import smoke

    temporary_directory = smoke.tempfile.TemporaryDirectory
    monkeypatch.setattr(smoke.tempfile, "TemporaryDirectory", lambda **kwargs: temporary_directory(dir=tmp_path, **kwargs))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, status)

    monkeypatch.setattr(smoke.subprocess, "run", run)
    assert smoke.main() == 1
    assert len(calls) == 1
    assert not list(tmp_path.iterdir())


def test_smoke_rejects_residual_marks(monkeypatch):
    import smoke

    def run(command, **kwargs):
        script = Path(command[1]).name
        # Dirty fixture is correctly flagged, but cleaned output is still dirty.
        return subprocess.CompletedProcess(command, 1 if script == "inspect_text.py" else 0)

    monkeypatch.setattr(smoke.subprocess, "run", run)
    assert smoke.main() == 1


def test_make_smoke_uses_selected_interpreter_and_cleans_temp_outputs(tmp_path):
    temporary = tmp_path / "smoke temp"
    temporary.mkdir()
    marker = tmp_path / "interpreter-used"
    wrapper = tmp_path / "python with spaces"
    wrapper.write_text(
        f"#!{sys.executable}\nimport os\nfrom pathlib import Path\nimport sys\n"
        f"Path({str(marker)!r}).write_text('used')\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o755)
    result = subprocess.run(
        ["make", "smoke", f"PYTHON={wrapper}"], cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "TMPDIR": str(temporary)},
    )
    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "used"
    assert "smoke ok" in result.stdout
    assert not list(temporary.iterdir())

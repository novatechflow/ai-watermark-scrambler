"""Backup suffixes must not change the input container format."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / "skills/ai-watermark-scrambler/scripts/clean_file.py"


@pytest.mark.parametrize("extension", ["md", "markdown", "mdx", "html", "htm"])
def test_in_place_container_uses_original_format(tmp_path, extension):
    markdown = extension in ("md", "markdown", "mdx")
    text = (
        "---\ngenerator: Claude\ntitle: Notes\n---\nHello\u200b\n"
        if markdown else '<meta name="generator" content="ChatGPT"><p>Hello\u200b</p>'
    )
    source = tmp_path / f"notes.{extension}"
    source.write_text(text, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(CLI), str(source), "--in-place", "--json"],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["format"] == ("markdown" if markdown else "html")
    cleaned = source.read_text(encoding="utf-8")
    assert "Hello" in cleaned
    assert "\u200b" not in cleaned
    assert "generator" not in cleaned
    assert source.with_suffix(source.suffix + ".bak").read_bytes() == text.encode("utf-8")

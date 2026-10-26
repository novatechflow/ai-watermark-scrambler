"""In-place backups must never write through an existing link."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/ai-watermark-scrambler/scripts"


@pytest.mark.parametrize("cli", ["clean_file.py", "clean_text.py", "clean_image.py"])
@pytest.mark.parametrize("dangling", [False, True])
def test_backup_symlink_is_refused(tmp_path, cli, dangling):
    source = tmp_path / "input.txt"
    source.write_bytes(b"hello\xe2\x80\x8b\n")
    target = tmp_path / "unrelated.txt"
    if not dangling:
        target.write_bytes(b"unrelated content")
    backup = source.with_suffix(".txt.bak")
    backup.symlink_to(target)

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / cli), str(source), "--in-place"],
        capture_output=True, text=True,
    )

    assert result.returncode == 2
    assert "symlink" in result.stderr
    assert source.read_bytes() == b"hello\xe2\x80\x8b\n"
    assert backup.is_symlink()
    if dangling:
        assert not target.exists()
    else:
        assert target.read_bytes() == b"unrelated content"


def test_backup_replaces_hardlink_without_overwriting_target(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    from common import create_in_place_backup

    source = tmp_path / "input.txt"
    source.write_bytes(b"original\r\n")
    target = tmp_path / "unrelated.txt"
    target.write_bytes(b"unrelated content")
    backup = source.with_suffix(".txt.bak")
    backup.hardlink_to(target)

    assert create_in_place_backup(source) == backup
    assert backup.read_bytes() == source.read_bytes()
    assert target.read_bytes() == b"unrelated content"

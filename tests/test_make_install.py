"""Skill installation must handle spaces and protect existing directories."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def checkout(tmp_path):
    project = tmp_path / "checkout with spaces"
    project.mkdir()
    shutil.copyfile(ROOT / "Makefile", project / "Makefile")
    skill = project / "skills/ai-watermark-scrambler"
    skill.mkdir(parents=True)
    return project, skill


def install(project, destination):
    return subprocess.run(
        ["make", "install-skill", f"SKILLS_DIR={destination}"],
        cwd=project, capture_output=True, text=True,
    )


def test_install_and_reinstall_support_spaced_paths(tmp_path):
    project, skill = checkout(tmp_path)
    destination = tmp_path / "installed skills"
    for _ in range(2):
        result = install(project, destination)
        assert result.returncode == 0, result.stderr
        link = destination / skill.name
        assert link.is_symlink()
        assert link.resolve() == skill
        assert not (skill / skill.name).exists()


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_install_refuses_existing_non_symlink(tmp_path, kind):
    project, skill = checkout(tmp_path)
    destination = tmp_path / "installed"
    destination.mkdir()
    existing = destination / skill.name
    if kind == "directory":
        existing.mkdir()
        sentinel = existing / "keep.txt"
    else:
        sentinel = existing
    sentinel.write_text("keep")
    result = install(project, destination)
    assert result.returncode != 0
    assert "refus" in result.stderr.lower()
    assert sentinel.read_text() == "keep"
    if kind == "directory":
        assert list(existing.iterdir()) == [sentinel]


def test_install_replaces_stale_symlink(tmp_path):
    project, skill = checkout(tmp_path)
    destination = tmp_path / "installed skills"
    destination.mkdir()
    link = destination / skill.name
    link.symlink_to(tmp_path / "old-checkout")
    result = install(project, destination)
    assert result.returncode == 0, result.stderr
    assert link.is_symlink()
    assert link.resolve() == skill

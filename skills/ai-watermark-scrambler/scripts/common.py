"""Shared helpers for ai-watermark-scrambler scripts."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent.parent
SKILL_VENV = SKILL_ROOT / ".venv"


def skill_venv_site_packages() -> Path:
    """Where ensure_tools.py installs the optional extras for this interpreter."""
    v = sys.version_info
    return SKILL_VENV / f"lib/python{v.major}.{v.minor}/site-packages"


def activate_skill_venv() -> None:
    """Make the skill's own venv importable when the scripts run under a bare python3.

    Appended, not prepended, so an ambient environment keeps priority.
    """
    site = skill_venv_site_packages()
    if site.is_dir() and str(site) not in sys.path:
        sys.path.append(str(site))


activate_skill_venv()

EXIT_OK = 0
EXIT_ERROR = 1  # also: residual C2PA/AI signals after a clean
EXIT_BAD_INPUT = 2
EXIT_DEGRADED = 3  # cleaner ran without the tools it needs; output not fully cleaned


def exit_code(result: dict) -> int:
    if result.get("meta", {}).get("degraded"):
        return EXIT_DEGRADED
    if result.get("still_has_c2pa") or result.get("still_has_ai_metadata"):
        return EXIT_ERROR
    return EXIT_OK


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def read_text_input(path: str | None) -> str:
    if path is None or path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8", errors="surrogateescape")
    except OSError as exc:
        eprint(f"error: {exc}")
        raise SystemExit(EXIT_BAD_INPUT) from exc


def write_text_output(text: str, path: str | None) -> None:
    if path is None or path == "-":
        if text and not text.endswith("\n"):
            text += "\n"
        sys.stdout.flush()
        sys.stdout.buffer.write(text.encode("utf-8", errors="surrogateescape"))
        sys.stdout.buffer.flush()
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_file(out, text)


def reject_symlink_in_place(path: Path) -> str | None:
    """Return an error message if --in-place would write through a symlink."""
    if path.is_symlink():
        return (
            f"--in-place refuses symlinks: {path} -> {path.resolve()}; "
            "pass the resolved path if that is what you meant"
        )
    return None


def write_text_file(path: Path, text: str) -> None:
    """Write text read back with surrogateescape without truncating on encode errors."""
    path.write_bytes(text.encode("utf-8", errors="surrogateescape"))


def create_in_place_backup(path: Path) -> Path:
    """Back up bytes atomically without writing through source or backup links."""
    error = reject_symlink_in_place(path)
    if error:
        raise ValueError(error)
    backup = path.with_suffix(path.suffix + ".bak")
    if backup.is_symlink():
        raise ValueError(f"--in-place refuses backup symlinks: {backup}")
    # Replacing the directory entry also avoids modifying any hardlink target.
    # A symlink appearing after the check is replaced, never followed.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(path.read_bytes())
            temporary.close()
            os.replace(temporary_path, backup)
        finally:
            temporary_path.unlink(missing_ok=True)
    return backup


def emit_json(data: Any) -> None:
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def cleaned_path(src: Path, suffix: str = ".cleaned") -> Path:
    """path/to/file.ext -> path/to/file.cleaned.ext"""
    return src.with_name(f"{src.stem}{suffix}{src.suffix}")


IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
CONTAINER_EXTS = {".svg", ".pdf", ".docx", ".odt", ".html", ".htm", ".md", ".markdown", ".mdx"}
TEXT_EXTS = {
    ".txt",
    ".text",
    ".css",
    ".js",
    ".py",
    ".rs",
    ".go",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
}


def detect_kind(path: Path) -> str:
    """Dispatch a file to the text, image, or container pipeline.

    Extension first; otherwise sniff the bytes. Containers win over text for
    html/md, which carry metadata as well as prose.
    """
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in CONTAINER_EXTS:
        return "container"
    if ext in TEXT_EXTS:
        return "text"

    # Deferred: both modules import this one.
    from container_meta import detect_container_format
    from image_meta import detect_image_format

    data = path.read_bytes()
    if detect_image_format(data) in ("png", "jpeg"):
        return "image"
    if detect_container_format(path, data) != "unknown":
        return "container"
    return "text"


def tool_arg(path: Path) -> str:
    """Absolute path for external tools, so a leading '-' is never read as an option."""
    return str(Path(path).resolve())

#!/usr/bin/env python3
"""Unified clean: text Layer A, PNG/JPEG metadata, and document containers."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    EXIT_BAD_INPUT,
    EXIT_OK,
    cleaned_path,
    create_in_place_backup,
    detect_kind,
    eprint,
    exit_code,
    write_text_file,
)
from container_meta import ZipTooLarge, clean_container  # noqa: E402
from image_meta import clean_image  # noqa: E402
from text_unicode import clean_text  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", type=Path)
    p.add_argument("-o", "--output", type=Path)
    p.add_argument("--in-place", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--nfkc", action="store_true", help="Text: NFKC normalize")
    p.add_argument("--aggressive-homoglyphs", action="store_true")
    p.add_argument(
        "--keep-non-ai-metadata",
        action="store_true",
        help="Images: only drop C2PA/AI-looking segments",
    )
    p.add_argument(
        "--as",
        dest="force_type",
        choices=("auto", "text", "image", "container"),
        default="auto",
    )
    args = p.parse_args()

    if not args.path.is_file():
        eprint(f"not a file: {args.path}")
        return EXIT_BAD_INPUT

    kind = args.force_type if args.force_type != "auto" else detect_kind(args.path)

    if args.in_place:
        try:
            bak = create_in_place_backup(args.path)
        except (OSError, ValueError) as exc:
            eprint(f"error: {exc}")
            return EXIT_BAD_INPUT
        dest = args.path
        src = bak
    else:
        src = args.path
        dest = args.output or cleaned_path(args.path)

    if kind == "text":
        text = src.read_text(encoding="utf-8", errors="surrogateescape")
        cleaned, stats = clean_text(
            text,
            nfkc=args.nfkc,
            aggressive_homoglyphs=args.aggressive_homoglyphs,
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_text_file(dest, cleaned)
        result = {
            "kind": "text",
            "input": str(args.path),
            "output": str(dest),
            "stats": stats,
        }
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            eprint(
                f"wrote {dest} removed={stats['removed_count']} replaced={stats['replaced_count']}"
            )
        return EXIT_OK

    if kind == "image":
        try:
            result = clean_image(
                src,
                dest,
                strip_all_metadata=not args.keep_non_ai_metadata,
            )
        except (OSError, ValueError) as e:
            eprint(f"error: {e}")
            return EXIT_BAD_INPUT
        result = {"kind": "image", **result}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            eprint(f"wrote {result['output']} ({result['bytes_in']} -> {result['bytes_out']})")
            for a in result["actions"]:
                eprint(f"  - {a}")
            if result["still_has_c2pa"] or result["still_has_ai_metadata"]:
                eprint("warning: residual C2PA/AI signals may remain")
        return exit_code(result)

    try:
        result = clean_container(
            src,
            dest,
            nfkc=args.nfkc,
            aggressive_homoglyphs=args.aggressive_homoglyphs,
        )
    except (OSError, ValueError, ZipTooLarge, zipfile.BadZipFile) as e:
        eprint(f"error: {e}")
        return EXIT_BAD_INPUT
    result = {"kind": "container", **result}
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        eprint(f"wrote {result['output']} format={result['format']}")
        for a in result["actions"]:
            eprint(f"  - {a}")
        if result["still_has_c2pa"] or result["still_has_ai_metadata"]:
            eprint("warning: residual C2PA/AI signals may remain")
            for f in result.get("post_findings") or []:
                eprint(f"  ! {f}")
        if result.get("meta", {}).get("degraded"):
            eprint("warning: cleaner ran in degraded mode; output is not fully cleaned")
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())

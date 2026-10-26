#!/usr/bin/env python3
"""Strip C2PA and AI-related metadata from PNG/JPEG."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    EXIT_BAD_INPUT,
    EXIT_ERROR,
    cleaned_path,
    create_in_place_backup,
    eprint,
    exit_code,
)
from image_meta import clean_image  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", type=Path, help="Input PNG or JPEG")
    p.add_argument("-o", "--output", type=Path, help="Output path (default: *.cleaned.*)")
    p.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite input (writes .bak backup first)",
    )
    p.add_argument(
        "--keep-non-ai-metadata",
        action="store_true",
        help="Only drop segments/chunks that look like C2PA/AI (less aggressive)",
    )
    p.add_argument("--json", action="store_true", help="JSON result on stdout")
    args = p.parse_args()

    if not args.path.is_file():
        eprint(f"not a file: {args.path}")
        return EXIT_BAD_INPUT

    src = args.path
    if args.in_place:
        try:
            bak = create_in_place_backup(args.path)
        except (OSError, ValueError) as exc:
            eprint(f"error: {exc}")
            return EXIT_BAD_INPUT
        src = bak
        dest = args.path
    else:
        dest = args.output or cleaned_path(args.path)

    try:
        result = clean_image(
            src,
            dest,
            strip_all_metadata=not args.keep_non_ai_metadata,
        )
    except (OSError, ValueError) as e:
        eprint(f"error: {e}")
        return EXIT_BAD_INPUT
    except Exception as e:
        eprint(f"error: {e}")
        return EXIT_ERROR

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        eprint(f"wrote {result['output']} ({result['bytes_in']} -> {result['bytes_out']})")
        for a in result["actions"]:
            eprint(f"  - {a}")
        if result["still_has_c2pa"] or result["still_has_ai_metadata"]:
            eprint("warning: residual C2PA/AI signals may remain")
            for f in result.get("post_findings") or []:
                eprint(f"  ! {f}")
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())

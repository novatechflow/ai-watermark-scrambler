#!/usr/bin/env python3
"""Research harness: green-list z-score before and after an unkeyed rewrite.

The key is ours, so the score is computable. A vendor detector is not.
ZeroGPT, when ZEROGPT_API_KEY is set, is a separate AI-text classifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "ai-watermark-scrambler" / "scripts"))

from text_unicode import clean_text  # noqa: E402

BOS = "\0"
ZEROGPT_URL = "https://app.zerogpt.net/api/v1/text/detect"

VOCAB = """
about above across after again against almost along already also
always among animal another answer around asked because become before
begin being below between black bring build called cannot change
children city close color come common could country course decided
different does during early earth enough every example family few
find first follow food form found four friend from game
gave general give going great green group grow half hand
happened hard head heard help high himself house however idea
important into itself just keep kind knew known large last
later learn leave left life light line little live long
look made make many might money more most mother move
much must name near need never next night number often
once only open order other over own paper part people
place plant play point put read really right river said
same saw say school second see seem sentence set several
should show side small something sound spell start state still
story study such system take tell than that their them
then there these they thing think those thought three through
together too took tree under until upon use usually very
want water way well went were what when where which while
white whole will with without word work world would write
""".split()


def green_ids(vocab_size: int, key: str, context: str, gamma: float) -> set[int]:
    order = list(range(vocab_size))
    digest = hashlib.sha256(f"{key}\n{context}".encode()).digest()
    random.Random(int.from_bytes(digest[:8], "big")).shuffle(order)
    k = max(1, int(round(vocab_size * gamma)))
    return set(order[:k])


def effective_gamma(vocab_size: int, gamma: float) -> float:
    return max(1, int(round(vocab_size * gamma))) / vocab_size


def _context(history: list[str], mode: str, window: int) -> str:
    if mode != "context":
        return ""
    span = history[-window:] if window else history
    return "\n".join(span) if span else BOS


def z_score(
    tokens: list[str],
    vocab: list[str],
    key: str,
    mode: str,
    gamma: float,
    window: int = 1,
) -> float:
    """Green-list z-score. `window` is how many previous tokens the hash uses."""
    if not tokens:
        return 0.0
    index = {word: i for i, word in enumerate(vocab)}
    g = effective_gamma(len(vocab), gamma)
    green = 0
    history: list[str] = []
    for tok in tokens:
        ctx = _context(history, mode, window)
        if index[tok] in green_ids(len(vocab), key, ctx, gamma):
            green += 1
        history.append(tok)
    n = len(tokens)
    return (green - g * n) / math.sqrt(n * g * (1 - g))


def generate(
    n: int,
    vocab: list[str],
    key: str,
    mode: str,
    gamma: float,
    seed: int,
    window: int = 1,
) -> list[str]:
    rng = random.Random(seed)
    history: list[str] = []
    out: list[str] = []
    for _ in range(n):
        greens = [vocab[i] for i in green_ids(len(vocab), key, _context(history, mode, window), gamma)]
        tok = rng.choice(greens)
        out.append(tok)
        history.append(tok)
    return out


def unkeyed_rewrite(tokens: list[str], vocab: list[str], rate: float, seed: int) -> list[str]:
    """Replace tokens without the watermark key. This stands in for a model rewrite."""
    rng = random.Random(seed)
    out: list[str] = []
    for tok in tokens:
        if rng.random() >= rate:
            out.append(tok)
            continue
        choice = rng.choice(vocab)
        if choice == tok:
            choice = vocab[(vocab.index(tok) + 1) % len(vocab)]
        out.append(choice)
    return out


def layer_a(tokens: list[str]) -> tuple[list[str], dict]:
    cleaned, stats = clean_text(" ".join(tokens))
    return cleaned.split(), stats


def zerogpt_detect(text: str, api_key: str, timeout: float = 30) -> dict:
    body = json.dumps({"document": text[:15000]}).encode()
    req = urllib.request.Request(
        ZEROGPT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"ZeroGPT HTTP {e.code}: {detail}") from e


def run_pair(
    *,
    n: int,
    key: str,
    gamma: float,
    rate: float,
    seed: int,
    mode: str,
    window: int = 1,
) -> dict:
    vocab = VOCAB
    marked = generate(n, vocab, key, mode, gamma, seed, window)
    after_a, stats = layer_a(marked)
    rewritten = unkeyed_rewrite(marked, vocab, rate, seed + 1)
    return {
        "mode": mode,
        "window": window,
        "tokens": n,
        "z_marked": z_score(marked, vocab, key, mode, gamma, window),
        "z_after_layer_a": z_score(after_a, vocab, key, mode, gamma, window),
        "layer_a_removed": stats["removed_count"],
        "z_rewritten": z_score(rewritten, vocab, key, mode, gamma, window),
        "marked_text": " ".join(marked),
        "rewritten_text": " ".join(rewritten),
    }


def _print_row(row: dict) -> None:
    label = row["mode"] if row["mode"] == "unigram" else f"context/{row['window']}"
    print(
        f"{label:12}  marked {row['z_marked']:7.2f}  "
        f"after Layer A {row['z_after_layer_a']:7.2f}  "
        f"after unkeyed rewrite {row['z_rewritten']:7.2f}"
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tokens", type=int, default=400)
    p.add_argument("--gamma", type=float, default=0.25)
    p.add_argument("--rewrite-rate", type=float, default=0.3)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--key", default="research-key")
    p.add_argument("--window", type=int, default=None, help="Context window; defaults to 1 in context mode or 1 and 8 in both mode")
    p.add_argument("--mode", choices=("context", "unigram", "both"), default="both")
    p.add_argument(
        "--zerogpt",
        action="store_true",
        help="Also score the context sample with ZeroGPT when ZEROGPT_API_KEY is set",
    )
    args = p.parse_args()
    if not 0 < args.gamma < 1:
        print("gamma must be between 0 and 1", file=sys.stderr)
        return 2
    if not 0 <= args.rewrite_rate <= 1:
        print("rewrite-rate must be between 0 and 1", file=sys.stderr)
        return 2

    if args.mode == "both":
        specs = (
            (("context", 1), ("context", 8), ("unigram", 0))
            if args.window is None else (("context", args.window), ("unigram", 0))
        )
    elif args.mode == "context":
        specs = (("context", args.window if args.window is not None else 1),)
    else:
        if args.window is not None:
            p.error("--window requires context or both mode")
        specs = (("unigram", 0),)
    rows = [
        run_pair(
            n=args.tokens,
            key=args.key,
            gamma=args.gamma,
            rate=args.rewrite_rate,
            seed=args.seed,
            mode=mode,
            window=window,
        )
        for mode, window in specs
    ]
    print(
        f"tokens={args.tokens} gamma={args.gamma} rewrite_rate={args.rewrite_rate} "
        f"seed={args.seed}"
    )
    for row in rows:
        _print_row(row)

    if args.zerogpt:
        api_key = os.environ.get("ZEROGPT_API_KEY", "")
        if not api_key:
            print("ZeroGPT skipped: ZEROGPT_API_KEY is not set")
        else:
            sample = next(r for r in rows if r["mode"] == "context") if args.mode != "unigram" else rows[0]
            for label in ("marked_text", "rewritten_text"):
                try:
                    report = zerogpt_detect(sample[label], api_key)
                except (RuntimeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                    print(f"ZeroGPT {label}: {e}")
                    continue
                doc = (report.get("documents") or [{}])[0]
                print(
                    f"ZeroGPT {label}: verdict={doc.get('verdict')} "
                    f"ai_score={doc.get('ai_score')} tier={doc.get('confidence_tier')}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

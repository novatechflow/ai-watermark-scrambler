# Research harness

This directory is the setup used to study how a rewrite changes a statistical text watermark. It is not part of the cleaner. A run does not certify that a vendor detector will miss the text.

The cleaner scrambles token choice. The score that measures that scramble is a green-list z-score, and computing it requires the watermark key. Vendor keys are not public, so this harness generates the document itself, with a key it holds, then scores the same document after Layer A and after an unkeyed rewrite.

## What a run does

1. Build a 400-token document. Each token is drawn from a green list of about a quarter of a fixed vocabulary. The list is a hash of the key and the previous tokens ([Kirchenbauer et al., 2023](https://arxiv.org/abs/2301.10226)). `context/1` hashes one previous token. `context/8` hashes eight. `unigram` hashes only the key ([Zhao et al., 2023](https://arxiv.org/abs/2306.17439)).
2. Run Layer A (`clean_text`). The mark is not a hidden character, so the z-score does not move.
3. Replace about 30% of tokens with vocabulary words chosen without the key. That is a stand-in for a Layer B rewrite. The skill's `print-prompt` backend only prints a prompt and does not change tokens, so it is not what this step calls.

The z-score is `(greens - γn) / sqrt(nγ(1-γ))`. On the default seed the harness prints:

```
tokens=400 gamma=0.25 rewrite_rate=0.3 seed=2026
context/1     marked   34.53  after Layer A   34.53  after unkeyed rewrite   19.54
context/8     marked   34.53  after Layer A   34.53  after unkeyed rewrite    1.67
unigram       marked   34.53  after Layer A   34.53  after unkeyed rewrite   24.85
```

A one-token context resynchronizes as soon as the next original pair is intact, so a light rewrite only dents it. An eight-token window stays wrong until eight clean tokens in a row have passed, so the same rewrite drops it through the usual detection region (z around 4). A unigram list does not depend on neighbors, so each replacement is green by chance and most of the score remains. That is why a single light synonym pass is not the whole of Layer B, and why `simplify` is the weak strength.

## Run

Token counts and context windows must be positive integers. Zero or negative
values are rejected before generating or reporting a sample.

From the repository root:

```bash
make qa
python3 demo/watermark_qa.py --tokens 400 --gamma 0.25 --rewrite-rate 0.3 --seed 2026
```

`tests/test_watermark_qa.py` pins the default rows.

Gamma must be between 0 and 1 and leave at least one vocabulary entry outside
the rounded green list. Values such as `--gamma 0.999` are rejected because the
effective fraction becomes 1 and has no defined z-score.

## ZeroGPT and other classifiers

ZeroGPT, GPTZero, and the same class guess whether prose reads like a model. They do not have the watermark key and they do not compute this z-score. A human verdict can sit on text whose green-list score is still high, and the reverse happens too. The synthetic word list above is also a poor input for those classifiers.

The hook is there for a side-by-side look at a real paragraph, not as a pass/fail. Set `ZEROGPT_API_KEY` and call the [ZeroGPT text endpoint](https://zerogpt.net/api-integration) (`POST https://app.zerogpt.net/api/v1/text/detect`):

```bash
ZEROGPT_API_KEY=... python3 demo/watermark_qa.py --zerogpt
```

The script prints `verdict` and `ai_score` for the context sample before and after the rewrite. A missing key skips the call. A failed call is reported and does not change the z-score result.

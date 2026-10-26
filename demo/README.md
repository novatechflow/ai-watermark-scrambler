# Research demos

This directory is the setup used to study how a rewrite changes a statistical text watermark. It is not part of the cleaner. A run does not certify that a vendor detector will miss the text.

Two experiments are available: `make qa` is a fast synthetic token-substitution
test with no model dependency; `make qa-model` generates actual prose and an
actual model paraphrase under a public, controlled watermark scheme.

## Public-model prose experiment

The [checked-in pilot and quality review](RESULTS.md) include the full report.
It found lower detector scores and substantial quality problems; it does not
claim a successful meaning-preserving attack.

```bash
make model-tools                 # isolated demo/.venv; optional torch/transformers
make qa-model                    # first run downloads public model weights
# More independent samples; every selected prompt is run for every seed:
make qa-model MODEL_QA_ARGS='--seeds 2026 7 42 --output demo/runs/multi-seed.json'
```

The default models are immutable revisions of
[Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) and
[all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2).
Allow a few GB of disk space and several minutes on a CPU. Automatic device
selection uses Metal/float16 when available and CPU/float32 otherwise;
`--device cpu` forces CPU inference. Dependencies, model
cache (`demo/.cache/`), and generated reports (`demo/runs/`) are separate from
the skill and ordinary tests. They are not committed. Downloads need network
access; inference runs locally. No paid API or proprietary detector is used.

For each prompt in `prose_prompts.json` and each seed, the harness:

1. Generates an unwatermarked control and a watermarked article with matched
   generation settings and seed. The public Hugging Face
   [WatermarkLogitsProcessor/WatermarkDetector](https://huggingface.co/docs/transformers/v5.20.2/internal/generation_utils#transformers.WatermarkLogitsProcessor)
   implements a keyed green-list logit bias based on the Kirchenbauer scheme.
   This is an actual sampling watermark, not the synthetic word-list generator.
2. Applies the skill's Layer A cleaner to the watermarked text.
3. Uses the skill's paraphrase prompt with the same checkpoint, explicitly
   disabling the watermark processor. The paraphraser receives neither the key
   nor detector feedback. Reusing the weights does not reapply this watermark
   when its processor is disabled; this says
   nothing about disabling a proprietary provider's watermark.
4. Scores the completion text alone, after retokenization, in every condition
   with the same key, vocabulary, CPU RNG device and settings. The public
   watermark processor always generates its seeded permutations on CPU, even
   when model inference uses Metal. Repeated n-grams are
   counted once. The configured threshold is z > 4 by default.
5. Records full-text semantic embedding cosine similarity, text length ratio,
   identical outputs, and missing/added numeric literals. Every embedding token
   is covered by bounded chunks; long passages are not silently clipped.

The report contains the full prompts and texts, seeds, immutable model
revisions, watermark parameters, generation limits, token counts, scores,
package versions, and hashes of the harness and prompt file. It is saved after
every completed sample. Interrupted/failed runs are not reported as complete.
Generated texts and detector scores are checkpointed before semantic scoring,
so a scorer failure does not discard the underlying sample.
Seeds and recorded versions support repeatable comparisons; exact token output
can still vary across hardware and runtime versions.
Truncated generations are flagged. Null detections and poor paraphrases stay in
the results; the harness does not retry until a favorable outcome appears.

Cosine similarity is a diagnostic, **not proof that all facts were preserved**.
Numeric checks are literal checks: writing a number in words can be flagged,
while other factual changes can be missed. Inspect the saved text for omissions,
hallucinations, repetition, and incomplete endings. Report watermark scores
together with these quality diagnostics, never as a standalone success rate.

This demonstrates behavior for this public scheme, model, prompts, seeds and
parameters only. It does not measure Claude/Gemini watermarks, prove universal
removal, or establish calibrated detection accuracy from a few examples.

To use another compatible causal chat model, pass `--model` and an immutable
40-character `--revision`. Use `--help` for token limits, bias, threshold, context
window and CPU thread controls. The key is intentionally public research data.

## Synthetic experiment

The cleaner scrambles token choice. The score that measures that scramble is a green-list z-score, and computing it requires the watermark key. Vendor keys are not public, so this harness generates the document itself, with a key it holds, then scores the same document after Layer A and after an unkeyed rewrite.

## What a run does

1. Build a 400-token document. Each token is drawn from a green list of about a quarter of a fixed vocabulary. The list is a hash of the key and the previous tokens ([Kirchenbauer et al., 2023](https://arxiv.org/abs/2301.10226)). `context/1` hashes one previous token. `context/8` hashes eight. `unigram` hashes only the key ([Zhao et al., 2023](https://arxiv.org/abs/2306.17439)).
2. Run Layer A (`clean_text`). The mark is not a hidden character, so the z-score does not move.
3. Replace about 30% of tokens with vocabulary words chosen without the key. That is a stand-in for a Layer B rewrite. The skill's `print-prompt` backend only prints a prompt and does not change tokens, so it is not what this step calls.

This is a synthetic substitution experiment, not an LLM paraphrase or a test
of any vendor's watermark detector. It checks the expected behavior of the
schemes implemented here. Semantic preservation and real model rewriting need
separate evaluation.

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

`--window N` selects that context window in either `--mode context` or the
default `--mode both`. With an explicit window, both mode prints that context
row and a unigram row; without one it keeps the three-row comparison above.
Unigram-only mode rejects `--window` because it does not use a context.

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

If either sample exceeds ZeroGPT's 15,000-character request limit, the entire
classifier comparison is explicitly skipped before making any requests. Text is
never silently truncated: the watermark scores always cover the full sample.

# Public-model pilot: signal reduction with visible quality failures

This is a three-prompt, one-seed pilot of the public-model experiment, not an
accuracy benchmark or a demonstration of meaning-preserving watermark removal.
The complete machine-readable evidence, including all generated text and the
prompts, is in [example_model_run.json](example_model_run.json).

The run used the pinned Qwen2.5-0.5B-Instruct checkpoint, Transformers 5.20.2,
PyTorch 2.14.1, Metal/float16 inference, and CPU watermark permutations and
detection. The public green-list watermark used gamma 0.25, bias 2.0,
`lefthash`, context width 1, key 15485863, and threshold **z > 4**. Repeated
n-grams were counted once. Generation used seed 2026; the unwatermarked
paraphrase used seed 2027 and the skill's paraphrase prompt. Each completion
was limited to 256 new tokens, with a minimum of 128.

| Prompt | Unwatermarked control z | Watermarked z | After Layer A z | Paraphrased z | Embedding cosine |
| --- | ---: | ---: | ---: | ---: | ---: |
| Garden | 0.23 | 11.90 | 11.90 | 4.64 | 0.811 |
| Library | 0.20 | 12.67 | 12.67 | 1.99 | 0.871 |
| Repair | -0.51 | 10.06 | 10.06 | -1.67 | 0.865 |

The detector flagged all three watermarked samples and none of their controls.
Layer A left their scores unchanged. Every paraphrase had a lower z-score;
two crossed below the configured threshold. These observations are restricted
to these samples and parameters. With only three samples, they do not estimate
false-positive rates, detection power, or robustness across models and keys.

## Quality review

- **Garden:** the watermarked source hit the generation limit and ends mid-sentence.
  Its rewrite is shorter (word-count ratio 0.673), omits the source's detail
  about 6 school spaces, and remains detectable at z = 4.64.
- **Library:** the rewrite retains the source's numeric literals and has cosine
  0.871, but the text still needs factual review. The source invents names and
  details beyond the input facts and omits the 3-packet borrowing limit. The
  rewrite changes “Located in a quaint town” to “Stacked beneath a rural town.”
  Numeric retention and a high embedding score do not establish equivalence.
- **Repair:** the rewrite drops the source's 8-person detail and changes monthly
  gatherings into “weekly projects.” Its cosine remains 0.865 despite that
  contradiction. The unwatermarked control also hit the generation limit.

The small model and watermark bias produced weak prose in places. Both source
generation quality and paraphrase quality must be assessed before claiming a
usable rewrite. This pilot demonstrates that the harness measures a real
public watermark and exposes quality problems; it does **not** establish a
successful meaning-preserving attack, nor any result about proprietary
Claude/Gemini watermarks.

## Reproduce or extend

```bash
make model-tools
make qa-model MODEL_QA_ARGS='--seeds 2026 --output demo/runs/reproduction.json'
```

The reference JSON records immutable model revisions, package versions,
generation settings, full prompts and outputs, token counts, and hashes of the
harness and prompt file. Exact text can vary across hardware/runtime versions.
The checked-in reference is a fixed observation, not a golden test requiring
every future paraphrase to evade detection. Use multiple seeds and prompts,
inspect capped outputs, and evaluate a stronger compatible model before drawing
broader conclusions. Keep unfavorable results alongside favorable ones.

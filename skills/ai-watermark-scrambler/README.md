# ai-watermark-scrambler

Scrambles and blurs **statistical text watermarks**. That is the job: the token-sampling signal (green-list, SynthID-Text, Claude's embedded text watermark, and the same class) lives in word choice, and a rewrite changes that choice. It does not certify that a detector with the vendor key will miss it.

The same scripts also strip marks that ride along with the file:

- **Layer A**: invisible Unicode (zero-width, bidi, tag chars, variation selectors, exotic spaces). Deterministic and verifiable. This is not the statistical watermark. Emoji ZWJ sequences, emoji VS16, flag tag sequences, and ZWNJ or ZWJ between Arabic, Indic, or Myanmar letters stay. Stray copies are removed.
- **Container metadata**: C2PA / EXIF / XMP / document properties on PNG, JPEG, SVG, PDF, DOCX, ODT, HTML, Markdown.

Pixel, audio, video, and visible watermarks are a different thing and are not this tool.

Covers Claude, Gemini / SynthID-class, OpenAI, and open-LLM marks. `SKILL.md` is the agent-facing workflow; this file is the human-facing one. An agent only loads `SKILL.md` from a skill directory (`~/.grok/skills`, `~/.claude/skills`, `~/.cursor/skills`, or the matching project directory). The repo README has the symlink commands, how a statistical text watermark is embedded, why a rewrite blurs it, and the paper list. `make install-skill` links into `~/.grok/skills`. Override the destination with `SKILLS_DIR`.

## Install

The scripts run on the standard library alone. Optional extras only remove degraded modes:

Run the following commands from this skill directory (the directory containing
this README and `scripts/`). For repository development, use the root setup
under Development below.

```bash
python3 scripts/ensure_tools.py            # status table
python3 scripts/ensure_tools.py --install  # create .venv, install requirements.txt
```

That installs `pikepdf` (PDF strip + rewrite) and `c2pa-python` (C2PA inspect) into the skill's own `.venv`, which the scripts discover on their own. `exiftool` has no pip package and must come from a system package manager; `qpdf` and `c2patool` are only needed when their pip equivalents are absent.

## Quickstart

```bash
python3 scripts/inspect_file.py notes.md              # look first
python3 scripts/clean_file.py notes.md -o notes.cleaned.md
python3 scripts/inspect_file.py notes.cleaned.md      # verify container metadata
python3 scripts/inspect_text.py notes.cleaned.md      # check Unicode in the source
```

`inspect_file` / `clean_file` dispatch on file type. The per-type scripts are also available directly:

| Script | Purpose |
| --- | --- |
| `inspect_text.py` / `clean_text.py` | Layer A on text or stdin (`-`) |
| `inspect_image.py` / `clean_image.py` | PNG / JPEG metadata |
| `inspect_file.py` / `clean_file.py` | Unified: text, image, container |
| `rewrite_text.py` | Layer B rewrite hook |
| `ensure_tools.py` | Optional-tool status and venv setup |

Useful flags: `--json` (machine-readable report), `--in-place` (writes a `.bak` first), `--stats`, `--nfkc`, `--aggressive-homoglyphs`, `--keep-non-ai-metadata` (images: drop only C2PA/AI-looking segments), `--as` (force the dispatch type).

## Layer B: scramble and blur the statistical watermark

This is the point of the tool. A statistical watermark is a bias on the next token. Scrambling changes which tokens appear and in what order. Blurring is that same change seen from the detector: the green-list score falls because the tokens moved. Default backend prints the prompt and calls no model:

```bash
python3 scripts/rewrite_text.py draft.md --backend print-prompt

export WATERMARKS_REWRITE_BACKEND=ollama
export WATERMARKS_REWRITE_MODEL=llama3.2
export WATERMARKS_REWRITE_BASE_URL=http://127.0.0.1:11434
python3 scripts/rewrite_text.py draft.md -o draft.rewritten.md --strength paraphrase
```

Strengths, all of them aimed at the statistical signal: `paraphrase` (default: high lexical diversity, stable sentence order), `simplify` (shorter common words for content words only, same order), `backtranslate` (pivot language and back), `structural` (outline, then new wording and sentence order; most drift). Backends: `print-prompt`, `ollama`, `openai-compatible`. See `references/watermark-attacks.md`.

Prefer a rewrite model **different from the suspected origin**: rewriting Claude text with Claude can re-stamp it. Layer A runs again on the result unless `--no-layer-a-after`.

For code, prefer a formatter (`prettier`, `black`, `gofmt`) plus Layer A; statistical marks in code are weak and rewriting risks correctness.

## Format support

| Format | What is removed |
| --- | --- |
| Text / code | Invisible Unicode, bidi controls, exotic spaces |
| PNG / JPEG | C2PA (APP11/JUMBF), text chunks, EXIF/XMP. Color data stays: PNG ICC, gamma, sRGB, and cHRM; JPEG JFIF, ICC APP2, and Adobe APP14 |
| SVG | `<metadata>` blocks, XMP packets |
| PDF | XMP, document info, and C2PA associated-file manifests via `pikepdf`; fallback metadata stripping needs `exiftool` plus `qpdf` to remove prior revisions |
| DOCX / ODT | AI provenance properties; DOCX `customXml` parts and relationships; ODT `meta:generator`; invisible Unicode in document text. Unrelated document properties may remain |
| HTML | AI `generator` meta, provenance meta names, provenance JSON-LD, `data-ai*`. Description, Open Graph, a CMS generator, and ordinary JSON-LD stay |
| Markdown | Provenance key names in YAML frontmatter. A title or description that mentions a vendor stays |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Clean: nothing found, or everything removed |
| 1 | Marks found (inspect), residual C2PA/AI signals after a clean, or a rewrite/tool-setup failure |
| 2 | Bad input (missing file, unsupported type, or a file the cleaner cannot read) |
| 3 | Degraded: the cleaner ran without the tools it needed; output is not fully cleaned |

Inspect scripts exit 1 on a dirty file. Handle that expected status explicitly
in pipelines; suppressing every error would also hide bad input and crashes.

For inspect/clean commands, a missing path or an unsupported type is exit 2.
`tests/test_cli_contract.py` checks those cases and the flag lists above.
An exit 1 from `rewrite_text.py` or `ensure_tools.py` is an operational failure,
not evidence that a watermark was detected.

## Residual risk after a clean

A successful strip does not mean no provenance remains. What survives:

| Channel | Survives our clean? | How to check |
| --- | --- | --- |
| Invisible Unicode | Removed in supported text paths; script/emoji controls intentionally stay | `inspect_text` on text sources or extracted document text |
| Hard-bound C2PA manifest | Removed where supported; verify | Content Credentials verify, `c2pa-python` |
| C2PA **soft binding** (in-content link to a remote manifest) | **Yes, by design** | vendor detector only |
| Pixel / audio / video watermarks (SynthID-media) | **Yes** | SynthID Detector |
| Visible overlays (logos, stock stamps) | **Yes** | Look at the image |
| Statistical text watermark | Scrambled and blurred by Layer B, not provably gone | vendor detector / secret key |
| Data-driven model backdoors | **Yes** | out of reach |

Report the three buckets separately: what was verifiably removed (counts, actions), what was best-effort (Layer B), and what was never in scope. Layer B cannot be gold-verified without vendor detectors or keys, so no "undetectable" claim is supportable.

`inspect_file` dispatches HTML, Markdown, DOCX, and ODT to metadata inspection;
it does not certify their Unicode text is clean. PDF and SVG cleaning handles
metadata, not Layer A text.

## Reference docs

- `references/mark-classes.md`: Unicode / sampling / C2PA / containers
- `references/vendor-notes.md`: Claude, Gemini/SynthID, OpenAI, open-LLM
- `references/removal-matrix.md`: which layer applies when
- `references/how-claude-marks.md`: Anthropic-specific detail
- `references/watermark-attacks.md`: sampling, pixel, and visible watermarks

## Development

From the repository root:

```bash
make dev     # repo .venv: optional extras + pytest from requirements-dev.txt
make test    # pytest (tests/)
make smoke   # end-to-end run over tests/fixtures
make qa      # synthetic green-list experiment
```

`make tools` is the separate setup for optional extras in the skill's own venv;
it does not install the test runner. Keep the skill `requirements.txt` inside
the skill so a standalone installation does not depend on repository files.

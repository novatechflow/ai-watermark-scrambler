# ai-watermark-scrambler

An agent skill whose job is to scramble and blur **statistical text watermarks**: the token-sampling signal (green-list, SynthID-Text, Claude's embedded text watermark, and the same class). The signal lives in word choice. A rewrite changes that choice. It does not certify that a detector with the vendor key will miss it.

The same tools also strip marks that ride along with the text:

- **Layer A**: invisible Unicode (zero-width, bidi, tag chars, variation selectors, exotic spaces). Deterministic, verifiable. This is not the statistical watermark. Emoji sequences and script-required joiners stay.
- **Metadata**: C2PA / EXIF / XMP / document properties on PNG, JPEG, SVG, PDF, DOCX, ODT, HTML, Markdown.

Pixel, audio, video, and visible watermarks are a different thing and are not this tool.

Covers Claude, Gemini / SynthID-class, OpenAI, and open-LLM marks.

Tool usage, flags, exit codes, and the residual-risk table live in
**[`skills/ai-watermark-scrambler/README.md`](skills/ai-watermark-scrambler/README.md)**. This file covers the repository.

The agent contract is [`skills/ai-watermark-scrambler/SKILL.md`](skills/ai-watermark-scrambler/SKILL.md). Cloning this repo does not load it. Agents only read a skill from a discovery directory, and `skills/` at the repo root is not one.

## How a statistical text watermark works

Nothing is hidden between the letters. At each step the model was choosing the next token, and the watermark biased that choice.

A green-list scheme ([Kirchenbauer et al., 2023](https://arxiv.org/abs/2301.10226)) hashes the recent tokens with a secret key and splits the vocabulary into a green list and a red list. It adds a small logit boost to the green tokens. The sentence still reads normally. The mark is the excess of green tokens. A detector that has the key counts them and turns the excess into a z-score. Long text is easier to call. Short text often is not.

[SynthID-Text](https://www.nature.com/articles/s41586-024-08025-4) (Dathathri et al., Nature 2024) does the same job with tournament sampling: a keyed score picks among candidate tokens. Claude's embedded text watermark is this class. Anthropic describes it as woven into the text, surviving copy-paste, and weakening under paraphrase, translation, or a heavy edit ([Help Center](https://support.claude.com/en/articles/16266773-how-claude-marks-ai-generated-content)). The algorithm and the key are not public.

A context-hash list is redrawn from the previous tokens. A context-free list, the unigram watermark ([Zhao et al., 2023](https://arxiv.org/abs/2306.17439)), paints each token green or red on its own. That one survives local edits better, because changing a word does not reshuffle the lists after it.

The bias is applied where the model had a real choice. Function words are predictable, so forcing them sounds wrong. The signal sits in content words: `purchase` versus `buy`, `approximately` versus `about`.

## Why a rewrite blurs it

The detector's evidence is "more green tokens than chance." Replace those tokens and the evidence shrinks. That is the blur. It is not a certificate.

- A context-hash mark also loses alignment. Change one word and the green lists after it are rebuilt, so later words that you did not touch stop counting.
- A context-free mark does not. The simpler word is green or red on its own. You only gain on the words you change, and only when the replacement is off the list.
- The rewrite has to come from somewhere else. Running the text back through the same watermarked model writes a new statistical mark in place of the old one.

The strengths in `rewrite_text.py` are that attack, split by how much of the token sequence moves:

| Strength | What it changes | Where it comes from |
| --- | --- | --- |
| `simplify` | Content words become shorter, more common synonyms. Order stays. | Synonym substitution. A context-hash mark moves. A context-free mark often stays. [Cheng et al., 2025](https://arxiv.org/abs/2505.05190) |
| `paraphrase` | New wording, same sentence order | Lexical diversity, the DIPPER knob ([Krishna et al., 2023](https://arxiv.org/abs/2303.13408)) |
| `backtranslate` | Out to another language and back | The Kirchenbauer robustness evaluation |
| `structural` | Outline, then new wording and a new sentence order | DIPPER's order knob, plus a rebuild |

One pass often leaves a unigram watermark detectable. Querying the watermarked model to learn the green list and then swapping ([Wu and Chandrasekaran, 2024](https://arxiv.org/abs/2403.14719); [Jovanović et al., 2024](https://arxiv.org/abs/2402.19361)) is not what this skill does.

Attack detail, and the pixel and visible marks this tool does not touch, is in [`references/watermark-attacks.md`](skills/ai-watermark-scrambler/references/watermark-attacks.md).

The harness used for this research generates a document with a key it holds, runs Layer A, then an unkeyed rewrite, and prints the green-list z-score. The write-up and the default numbers are in [`demo/README.md`](demo/README.md). `make qa` runs it again.

An optional `make qa-model` experiment generates real prose with a public
watermark processor, runs an actual model paraphrase with that processor
disabled, and reports detector scores alongside semantic-similarity and numeric
change diagnostics. Run `make model-tools` first; these heavier dependencies
and model downloads are isolated from the skill and normal setup. Results apply
to the selected public scheme, not proprietary vendor watermarks. See the
[experiment protocol](demo/README.md#public-model-prose-experiment).

## Sources

- [Kirchenbauer et al., 2023. *A Watermark for Large Language Models.*](https://arxiv.org/abs/2301.10226) Green list, logit bias, z-score.
- [Zhao et al., 2023. *Provable Robust Watermarking for AI-Generated Text.*](https://arxiv.org/abs/2306.17439) Context-free unigram list. Harder to break with a light edit.
- [Dathathri et al., 2024. *Scalable watermarking for identifying large language model outputs.* Nature.](https://www.nature.com/articles/s41586-024-08025-4) SynthID-Text.
- [Krishna et al., 2023. *Paraphrasing evades detectors of AI-generated text.*](https://arxiv.org/abs/2303.13408) DIPPER. Lexical diversity and order diversity.
- [Cheng et al., 2025. *Revealing Weaknesses in Text Watermarking Through Self-Information Rewrite Attacks.*](https://arxiv.org/abs/2505.05190) Synonym substitution versus rewriting only the surprising tokens.
- [Wu and Chandrasekaran, 2024. *Bypassing LLM Watermarks with Color-Aware Substitutions.*](https://arxiv.org/abs/2403.14719) Needs the watermarked model. Not implemented here.
- [Jovanović et al., 2024. *Watermark Stealing in Large Language Models.*](https://arxiv.org/abs/2402.19361) Learns the green list by querying. Not implemented here.
- [Anthropic. *How Claude marks AI-generated content.*](https://support.claude.com/en/articles/16266773-how-claude-marks-ai-generated-content) Embedded text watermark plus C2PA. Detection of the text mark is a private preview.
- [C2PA specification.](https://spec.c2pa.org/) Hard-bound manifests in the file. A different channel from the statistical text mark.

## Use it from an agent

`make install-skill` symlinks the skill into `~/.grok/skills/ai-watermark-scrambler`. Grok then exposes `/ai-watermark-scrambler` and can load the skill when the request matches the description. The same directory works for any agent that reads a `SKILL.md` plus the `scripts/` beside it.

| Agent | Put the skill here | What you get |
| --- | --- | --- |
| Grok, all projects | `~/.grok/skills/ai-watermark-scrambler` (`make install-skill`) | `/ai-watermark-scrambler`, and automatic use when the description matches |
| Grok, this repo only | `<repo>/.grok/skills/ai-watermark-scrambler` | Same, for anyone who clones the repo |
| Claude Code | `~/.claude/skills/ai-watermark-scrambler` or `<repo>/.claude/skills/ai-watermark-scrambler` | Claude reads the same `SKILL.md`. Grok scans these directories too |
| Cursor | `~/.cursor/skills/ai-watermark-scrambler` or `<repo>/.cursor/skills/ai-watermark-scrambler` | Cursor reads the same `SKILL.md`. Grok scans these directories too |
| Anything that can run Python | Point the agent at `skills/ai-watermark-scrambler/SKILL.md` | No skill directory. The agent follows the workflow and runs the scripts |

```bash
# all Grok projects
make install-skill

# this repo only, so a clone is enough
mkdir -p .grok/skills
ln -sfn ../../skills/ai-watermark-scrambler .grok/skills/ai-watermark-scrambler

# Claude Code, all projects
mkdir -p ~/.claude/skills
ln -sfn "$(pwd)/skills/ai-watermark-scrambler" ~/.claude/skills/ai-watermark-scrambler
```

The slash command is the directory name, `ai-watermark-scrambler`. The names in the description (`/remove-ai-marks`, `/remove-claude-marks`) are trigger phrases, not extra commands.

What the agent actually does:

1. Inspect with `inspect_file.py`. Exit 1 means marks were found, not that the command failed.
2. Write a `*.cleaned.*` copy with `clean_file.py`. That cleans supported text and metadata channels; see the format table in the skill README. It does not touch the statistical watermark.
3. For prose, offer a Layer B rewrite. `rewrite_text.py --backend print-prompt` only prints the prompt. The statistical scramble happens when the agent, or `--backend ollama`, rewrites the text. `simplify` is the lighter pass. Use a model other than the one that likely wrote the text.
4. Inspect again. Container inspection reports metadata; use `inspect_text.py` for Unicode in text sources, or extract DOCX/ODT text before checking it. Say what was verified, which scramble ran, and that the statistical signal is not certified gone.

`make test` and `make smoke` check the scripts. They do not check that an agent loaded the skill.

## Quick start

```bash
make dev      # create repo .venv; install optional extras and pytest
make test     # pytest
make smoke    # end-to-end CLI run over tests/fixtures
make qa       # synthetic watermark research harness

python3 skills/ai-watermark-scrambler/scripts/inspect_file.py notes.md
python3 skills/ai-watermark-scrambler/scripts/clean_file.py notes.md -o notes.cleaned.md
```

## Layout

```
skills/ai-watermark-scrambler/
  SKILL.md          agent-facing workflow
  README.md         human-facing tool documentation
  references/       mark classes, vendor notes, removal matrix, Claude specifics
  scripts/          CLI entry points and supporting modules
  requirements.txt  optional extras
tests/              pytest suite and fixtures
demo/               research harness for the green-list z-score
Makefile            dev / tools / test / smoke / qa / model-tools / qa-model / install-skill / clean
requirements-dev.txt repo development dependencies (optional extras + pytest)
```

## Make targets

| Target | Does |
| --- | --- |
| `dev` | Creates the repo `.venv` if needed and installs `requirements-dev.txt` |
| `tools` | Creates `skills/ai-watermark-scrambler/.venv` and installs the optional extras |
| `test` | Runs pytest |
| `smoke` | Checks exact inspector exit codes and re-inspects cleaned fixtures in private temporary output |
| `qa` | Runs the green-list research harness. Write-up: [`demo/README.md`](demo/README.md) |
| `model-tools` | Installs optional real-model research dependencies in `demo/.venv` |
| `qa-model` | Runs the public-model prose/paraphrase experiment; `MODEL_QA_ARGS` supplies flags |
| `install-skill` | Symlinks the skill into `~/.grok/skills/` (`SKILLS_DIR` overrides that directory) |
| `clean` | Drops `__pycache__`, `.pytest_cache`, and the repo/skill venvs; preserves the optional model environment, cache and reports |

## Dependencies

Two separate install paths. Do not confuse them:

| Situation | Use | Gets |
| --- | --- | --- |
| Skill installed on a host, no repo | `ensure_tools.py --install` | Skill extras into `<skill>/.venv` |
| Working on this repo | `pip install -r requirements-dev.txt` | Skill extras **+ pytest** |

Keep `requirements-dev.txt` for repo development and the skill's
`requirements.txt` for its optional runtime extras. A root `requirements.txt`
would imply a separate runtime dependency set, but the scripts have no required
third-party runtime dependencies. For a managed development environment, use
`make dev`; the pip command above is for an environment you have already activated.

- The skill is self-contained: it ships its own `requirements.txt` and installs it into its own venv, so a fresh host needs nothing from this repo.
- `requirements-dev.txt` exists only to add the test runner. Nothing at runtime reads it.
- The scripts run on the Python standard library alone.
- Optional extras only remove degraded modes; they are never required.
- `pikepdf`: PDF strip and rewrite. The alternative is `exiftool` plus `qpdf`; `qpdf` alone only rewrites the file. Without a working strip-and-rewrite path, PDF cleans exit 3 and may retain metadata or prior revisions.
- `c2pa-python`: C2PA manifest inspection.
- `exiftool`: no pip package; install with `brew install exiftool` or `apt install libimage-exiftool-perl`.
- Run `python3 skills/ai-watermark-scrambler/scripts/ensure_tools.py` for a status table.

## Tests

- Suite covers the Unicode and container cleaners, image metadata paths, the Layer B rewrite hook, security properties (zip bombs, symlink writes, path handling), and a CLI contract pinning documented exit codes and flags against real `--help` output.
- Tests needing `pikepdf` or `c2pa` skip themselves when absent, so the suite passes with or without the extras.
- `make test` uses `.venv/bin/python` when a repo-level venv exists, `python3` otherwise.
- Python Make targets share the `PYTHON` override; for example, `make qa PYTHON=python3.13`.
- The skill venv at `skills/ai-watermark-scrambler/.venv` is added to `sys.path` by the scripts, so no activation needed.

## CI

- Runs `make test` and `make smoke` on push, pull request, and manual dispatch.
- Matrix: Python 3.11, 3.12, 3.13.
- Installs `exiftool` so its branches are exercised; the pip extras install non-fatally, since a missing wheel must not fail a build the suite is designed to survive.

## Scope

| Channel | Removed? |
| --- | --- |
| Invisible Unicode | Yes (verify by re-inspecting) |
| Hard-bound C2PA manifest | Supported containers; verify output and exit status |
| Container metadata (EXIF, XMP, doc props) | Supported fields only; unrelated DOCX/ODT properties may remain |
| Statistical text watermark | Reduced, not provably gone |
| C2PA **soft binding** | No (survives by design) |
| Pixel / audio / video watermarks (SynthID-media) | No |
| Visible overlays (logos, stock stamps) | No |
| Data-driven model backdoors | No |

A clean does not mean no AI provenance remains. Check the residual-risk table in the
skill README before relying on a result.

## Intended use

Provenance marks travel with a file whether or not you want them to. The marks this tool
removes routinely carry more than "an AI touched this": EXIF holds GPS coordinates and
device serials, XMP and document properties hold author names and editing history, and
invisible Unicode survives copy-paste into systems that never expected it. Stripping that
from your own documents before you publish them is ordinary hygiene.

Built for:

- Removing metadata from files you own or are authorized to process, before release.
- Sanitizing text where zero-width characters break downstream parsing, diffs, or search.
- Research on watermark robustness, and red-teaming provenance schemes to find where they
  fail.

Not built for defeating disclosure obligations. Where a person or institution is owed an
honest answer about how something was made (academic submissions, journalism, legal
attestations, contexts with an explicit AI-disclosure policy), removing the mark does not
change the answer you owe them, and this tool will not make an undisclosed submission
truthful.

Two limits worth stating plainly, because they cut both ways: the results are best-effort,
not guarantees, and a passing inspection is not proof of anything (see Scope above). Local
law on metadata removal, evidence handling, and document tampering is yours to check.

## License

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

Copyright 2026 Novatechflow

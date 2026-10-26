#!/usr/bin/env python3
"""Controlled public-model experiment; no claims about proprietary watermarks."""

from __future__ import annotations

import argparse
import importlib.metadata
import hashlib
import json
import math
import os
import platform
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills/ai-watermark-scrambler/scripts"))
from rewrite_text import build_prompt
from text_unicode import clean_text

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


def diagnostics(source: str, rewritten: str) -> dict:
    def numbers(text):
        return {value.replace(",", "") for value in re.findall(r"\b\d+(?:[,.]\d+)*\b", text)}

    before, after = numbers(source), numbers(rewritten)
    return {
        "identical_text": source == rewritten,
        "word_count_ratio": len(rewritten.split()) / max(1, len(source.split())),
        "missing_numbers": sorted(before - after),
        "added_numbers": sorted(after - before),
        "meaning_preservation": "manual_review_required",
    }


def check_token_coverage(expected: int, chunk_counts: list[int]) -> None:
    if expected <= 0 or sum(chunk_counts) != expected:
        raise ValueError("semantic chunks must cover every input token exactly once")


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            json.dump(report, out, indent=2, ensure_ascii=False, allow_nan=False)
            out.write("\n")
            out.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class CPUWatermarkAdapter:
    """Run the unmodified public processor on CPU tensors, then restore device."""

    def __init__(self, processor):
        self.processor = processor

    def __call__(self, input_ids, scores):
        return self.processor(input_ids.to("cpu"), scores.to("cpu")).to(scores.device)


class PublicModel:
    def __init__(self, args):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, WatermarkDetector, WatermarkingConfig, WatermarkLogitsProcessor

        self.torch, self.args = torch, args
        torch.set_num_threads(args.threads)
        self.device = "mps" if args.device == "auto" and torch.backends.mps.is_available() else args.device
        if self.device == "auto":
            self.device = "cpu"
        if self.device == "mps" and not torch.backends.mps.is_available():
            raise ValueError("MPS was requested but is unavailable")
        dtype = torch.float16 if self.device == "mps" else torch.float32
        self.tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision, trust_remote_code=False)
        self.model = AutoModelForCausalLM.from_pretrained(
            args.model, revision=args.revision, trust_remote_code=False,
            use_safetensors=True, dtype=dtype, attn_implementation="eager",
        ).to(self.device).eval()
        green_count = int(self.model.config.vocab_size * args.gamma)
        if not 0 < green_count < self.model.config.vocab_size:
            raise ValueError("gamma must yield a nonempty green list and red list for this vocabulary")
        self.watermark = WatermarkingConfig(
            greenlist_ratio=args.gamma, bias=args.bias, hashing_key=args.hashing_key,
            seeding_scheme="lefthash", context_width=args.window,
        )
        # Keep the processor's seeded permutation on CPU, even with Metal inference.
        self.processor = CPUWatermarkAdapter(WatermarkLogitsProcessor(
            vocab_size=self.model.config.vocab_size, device="cpu",
            greenlist_ratio=args.gamma, bias=args.bias, hashing_key=args.hashing_key,
            seeding_scheme="lefthash", context_width=args.window,
        ))
        # Detector matches the processor's RNG device, vocabulary and settings.
        self.detector = WatermarkDetector(
            model_config=self.model.config, device="cpu", watermarking_config=self.watermark,
            ignore_repeated_ngrams=True,
        )

    def information(self):
        return {"vocab_size": self.model.config.vocab_size,
                "resolved_revision": getattr(self.model.config, "_commit_hash", self.args.revision),
                "inference_device": self.device, "dtype": str(self.model.dtype),
                "watermark_rng_device": "cpu"}

    def generate(self, prompt: str, seed: int, watermarked: bool) -> dict:
        started = time.perf_counter()
        messages = [{"role": "user", "content": prompt}]
        inputs = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.device)
        self.torch.manual_seed(seed)
        with self.torch.inference_mode():
            output = self.model.generate(
                **inputs, do_sample=True, temperature=0.8, top_p=0.95, top_k=0,
                min_new_tokens=self.args.min_new_tokens, max_new_tokens=self.args.max_new_tokens,
                # Explicitly disable the processor for control and paraphrase calls.
                watermarking_config=None,
                logits_processor=[self.processor] if watermarked else None,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        completion = output[0, inputs["input_ids"].shape[1]:]
        text = self.tokenizer.decode(completion, skip_special_tokens=True).strip()
        if not text:
            raise RuntimeError("model returned an empty completion")
        seconds = time.perf_counter() - started
        print(f"  {'watermarked' if watermarked else 'unwatermarked'} completion: {len(completion)} tokens in {seconds:.1f}s", file=sys.stderr, flush=True)
        return {
            "text": text, "generated_tokens": len(completion),
            "hit_token_limit": len(completion) == self.args.max_new_tokens,
            "seconds": seconds,
        }

    def detect(self, text: str) -> dict:
        # Retokenize the actual text in every condition; never score the prompt.
        ids = self.tokenizer(text, add_special_tokens=False, return_tensors="pt")["input_ids"]
        if ids.shape[1] <= self.args.window:
            return {"status": "insufficient_tokens", "z": None, "detected": None, "tokens": ids.shape[1]}
        result = self.detector(ids, return_dict=True, z_threshold=self.args.threshold)
        z = float(result.z_score[0])
        if not math.isfinite(z) or int(result.num_tokens_scored[0]) <= 0:
            raise RuntimeError("detector produced an undefined score")
        return {
            "status": "scored", "tokens": ids.shape[1],
            "tokens_scored": int(result.num_tokens_scored[0]),
            "green_tokens": int(result.num_green_tokens[0]), "z": z,
            "detected": bool(result.prediction[0]),
        }


class SemanticScorer:
    """MiniLM mean pooling over every token, with no silent prefix truncation."""

    def __init__(self):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL, revision=EMBED_REVISION, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(
            EMBED_MODEL, revision=EMBED_REVISION, trust_remote_code=False, use_safetensors=True,
        ).eval()

    def embed(self, text):
        ids = self.tokenizer.encode(text, add_special_tokens=False)
        inputs = self.tokenizer(
            text, max_length=min(256, self.model.config.max_position_embeddings),
            truncation=True, return_overflowing_tokens=True, stride=0, padding=True,
            return_special_tokens_mask=True, return_tensors="pt",
        )
        specials = inputs.pop("special_tokens_mask")
        inputs.pop("overflow_to_sample_mapping", None)
        counts = ((1 - specials) * inputs["attention_mask"]).sum(dim=1).tolist()
        check_token_coverage(len(ids), counts)
        vectors, weights = [], []
        for index, count in enumerate(counts):
            chunk = {name: values[index:index + 1] for name, values in inputs.items()}
            with self.torch.inference_mode():
                hidden = self.model(**chunk).last_hidden_state
            mask = chunk["attention_mask"].unsqueeze(-1)
            vector = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            vectors.append(vector[0] * count)
            weights.append(count)
        vector = self.torch.stack(vectors).sum(dim=0) / sum(weights)
        return self.torch.nn.functional.normalize(vector, dim=0), len(ids)

    def compare(self, source, rewritten):
        before, n_before = self.embed(source)
        after, n_after = self.embed(rewritten)
        return {
            "cosine_similarity": float(self.torch.dot(before, after).clamp(-1, 1)),
            "source_tokens_embedded": n_before, "rewrite_tokens_embedded": n_after,
            "all_tokens_embedded": True,
            "note": "Chunk-pooled similarity is a diagnostic, not factual equivalence or entailment.",
        }


def summarize(rows):
    scored = [row for row in rows if row["scores"]["marked"]["z"] is not None and row["scores"]["paraphrased"]["z"] is not None]
    return {
        "samples": len(rows), "scored_pairs": len(scored),
        "marked_detected": sum(row["scores"]["marked"]["detected"] is True for row in rows),
        "control_detected": sum(row["scores"]["control"]["detected"] is True for row in rows),
        "paraphrased_detected": sum(row["scores"]["paraphrased"]["detected"] is True for row in rows),
        "lower_z_pairs": sum(row["scores"]["paraphrased"]["z"] < row["scores"]["marked"]["z"] for row in scored),
        "detected_then_undetected": sum(row["scores"]["marked"]["detected"] is True and row["scores"]["paraphrased"]["detected"] is False for row in rows),
        "numeric_drift_pairs": sum(bool(row["quality"]["missing_numbers"] or row["quality"]["added_numbers"]) for row in rows),
        "meaning_preservation": "manual_review_required",
    }


def experiment(args, backend_factory=PublicModel, semantic_factory=SemanticScorer):
    prompts = json.loads(args.prompts.read_text(encoding="utf-8"))
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompts must be a nonempty JSON list")
    for item in prompts:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip() or not isinstance(item.get("prompt"), str) or not item["prompt"].strip():
            raise ValueError("each prompt needs a string id and nonempty prompt")
    if len({item["id"] for item in prompts}) != len(prompts):
        raise ValueError("prompt ids must be unique")
    report = {
        "schema_version": 1, "status": "running", "created_utc": datetime.now(timezone.utc).isoformat(),
        "model": {"id": args.model, "revision": args.revision},
        "embedding_model": {"id": EMBED_MODEL, "revision": EMBED_REVISION},
        "watermark": {"implementation": "transformers.WatermarkingConfig/WatermarkDetector", "seeding_scheme": "lefthash",
                      "gamma": args.gamma, "bias": args.bias, "hashing_key": args.hashing_key, "context_width": args.window,
                      "threshold": args.threshold, "ignore_repeated_ngrams": True},
        "generation": {"temperature": 0.8, "top_p": 0.95, "top_k": 0,
                       "min_new_tokens": args.min_new_tokens, "max_new_tokens": args.max_new_tokens,
                       "requested_device": args.device, "threads": args.threads, "seeds": args.seeds},
        "runtime": {"python": platform.python_version(), "platform": platform.platform(),
                    "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "prompts_sha256": hashlib.sha256(args.prompts.read_bytes()).hexdigest()},
        "notes": ["Same checkpoint; watermark processor disabled for control and paraphrase.",
                  "Public research key; paraphrase prompt receives no key or detector feedback.",
                  "All planned samples retained, including null detections and quality failures.",
                  "Not a proprietary watermark test; similarity does not establish preservation of all facts."],
        "rows": [],
    }
    save_report(args.output, report)
    try:
        backend, semantic = backend_factory(args), semantic_factory()
        information = backend.information()
        report["model"].update(information)
        report["generation"].update(device=information["inference_device"], dtype=information["dtype"])
        report["runtime"]["packages"] = {
            name: importlib.metadata.version(name) for name in ("torch", "transformers", "tokenizers", "safetensors", "numpy", "huggingface-hub")
        }
        for item in prompts:
            for seed in args.seeds:
                print(f"{item['id']} seed={seed}: control, watermark, paraphrase", file=sys.stderr, flush=True)
                control = backend.generate(item["prompt"], seed, False)
                marked = backend.generate(item["prompt"], seed, True)
                after_a, stats = clean_text(marked["text"])
                rewrite_prompt = build_prompt("paraphrase", after_a, lang="French", original_lang="English")
                rewritten = backend.generate(rewrite_prompt, seed + 1, False)
                paraphrased, rewrite_stats = clean_text(rewritten["text"])
                texts = {"control": control["text"], "marked": marked["text"], "layer_a": after_a, "paraphrased": paraphrased}
                row = {"id": item["id"], "seed": seed, "prompt": item["prompt"], "rewrite_prompt": rewrite_prompt,
                       "texts": texts, "status": "generated",
                       "generation": {"control": control, "marked": marked, "paraphrase": rewritten},
                       "layer_a": stats, "rewrite_layer_a": rewrite_stats}
                report["rows"].append(row)
                save_report(args.output, report)
                scores = row["scores"] = {stage: backend.detect(text) for stage, text in texts.items()}
                save_report(args.output, report)
                quality = row["quality"] = {**diagnostics(after_a, paraphrased), **semantic.compare(after_a, paraphrased)}
                row["status"] = "complete"
                save_report(args.output, report)
                print(f"{item['id']}: z {scores['marked']['z']} -> {scores['paraphrased']['z']}; cosine {quality['cosine_similarity']:.3f}", flush=True)
        report.update(status="complete", summary=summarize(report["rows"]))
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        save_report(args.output, report)
        raise
    save_report(args.output, report)
    return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--revision", help="Immutable model commit; required for a custom model")
    parser.add_argument("--prompts", type=Path, default=HERE / "prose_prompts.json")
    parser.add_argument("--output", type=Path, default=HERE / "runs/latest.json")
    parser.add_argument("--seeds", type=int, nargs="+", default=[2026])
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--min-new-tokens", type=int, default=128)
    parser.add_argument("--gamma", type=float, default=0.25)
    parser.add_argument("--bias", type=float, default=2.0)
    parser.add_argument("--hashing-key", type=int, default=15485863)
    parser.add_argument("--window", type=int, default=1)
    parser.add_argument("--threshold", type=float, default=4.0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--device", choices=("auto", "cpu", "mps"), default="auto")
    args = parser.parse_args(argv)
    if args.revision is None and args.model == MODEL:
        args.revision = REVISION
    if args.revision is None or not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("--revision must be an immutable 40-character model commit")
    if not 1 <= args.min_new_tokens <= args.max_new_tokens or not 0 < args.window < args.max_new_tokens or args.threads <= 0:
        parser.error("require 1 <= min-new-tokens <= max-new-tokens, 0 < window < max-new-tokens, and positive threads")
    if not 0 < args.gamma < 1 or not math.isfinite(args.bias) or args.bias <= 0 or not math.isfinite(args.threshold) or args.threshold <= 0:
        parser.error("gamma must be in (0,1); bias and threshold must be finite and positive")
    if args.hashing_key <= 0 or args.hashing_key >= 2**31 or len(set(args.seeds)) != len(args.seeds):
        parser.error("use a positive hashing key below 2**31 and unique seeds")
    if any(seed < 0 or seed >= 2**63 - 1 for seed in args.seeds):
        parser.error("seeds must be nonnegative and below 2**63 - 1")
    return args


def main(argv=None):
    args = parse_args(argv)
    os.environ.setdefault("HF_HOME", str(HERE / ".cache/huggingface"))
    try:
        report = experiment(args)
    except Exception as exc:
        print(f"experiment failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        if isinstance(exc, ImportError):
            print("Install the optional dependencies with make model-tools.", file=sys.stderr)
        return 1
    print(json.dumps(report["summary"], indent=2))
    print(f"Full report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

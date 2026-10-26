.PHONY: dev test smoke tools install-skill qa clean

SCRIPTS := skills/ai-watermark-scrambler/scripts
PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

tools:
	python3 $(SCRIPTS)/ensure_tools.py --install

dev:
	@if [ ! -x .venv/bin/python ]; then "$(PYTHON)" -m venv .venv; fi
	.venv/bin/python -m pip install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest

smoke:
	-python3 $(SCRIPTS)/inspect_text.py tests/fixtures/sample_watermarked.txt
	python3 $(SCRIPTS)/clean_text.py tests/fixtures/sample_watermarked.txt -o /tmp/wm.cleaned.txt --stats
	python3 $(SCRIPTS)/rewrite_text.py tests/fixtures/sample_watermarked.txt --backend print-prompt >/dev/null
	-python3 $(SCRIPTS)/inspect_file.py tests/fixtures/sample_ai.md
	python3 $(SCRIPTS)/clean_file.py tests/fixtures/sample_ai.md -o /tmp/sample_ai.cleaned.md
	python3 $(SCRIPTS)/clean_file.py tests/fixtures/sample_ai.html -o /tmp/sample_ai.cleaned.html
	python3 $(SCRIPTS)/clean_file.py tests/fixtures/sample_meta.svg -o /tmp/sample_meta.cleaned.svg
	@echo "smoke ok"

qa:
	$(PYTHON) demo/watermark_qa.py

SKILLS_DIR ?= $(HOME)/.grok/skills

install-skill:
	mkdir -p $(SKILLS_DIR)
	ln -sfn $(CURDIR)/skills/ai-watermark-scrambler $(SKILLS_DIR)/ai-watermark-scrambler
	@echo "linked -> $(SKILLS_DIR)/ai-watermark-scrambler"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .venv skills/ai-watermark-scrambler/.venv

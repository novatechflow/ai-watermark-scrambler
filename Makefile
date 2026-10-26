.PHONY: test smoke tools install-skill qa clean

SCRIPTS := skills/ai-watermark-scrambler/scripts
PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

tools:
	python3 $(SCRIPTS)/ensure_tools.py --install

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
	@set -eu; \
	dest="$(SKILLS_DIR)/ai-watermark-scrambler"; \
	if [ -e "$$dest" ] && [ ! -L "$$dest" ]; then \
		echo "refusing to replace non-symlink destination: $$dest" >&2; \
		exit 1; \
	fi; \
	mkdir -p "$(SKILLS_DIR)"; \
	ln -sfn "$(CURDIR)/skills/ai-watermark-scrambler" "$$dest"; \
	echo "linked -> $$dest"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .venv skills/ai-watermark-scrambler/.venv

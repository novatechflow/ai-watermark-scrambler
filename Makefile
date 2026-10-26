.PHONY: dev test smoke tools install-skill qa model-tools qa-model clean

SCRIPTS := skills/ai-watermark-scrambler/scripts
PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

tools:
	"$(PYTHON)" $(SCRIPTS)/ensure_tools.py --install

dev:
	@if [ ! -x .venv/bin/python ]; then "$(PYTHON)" -m venv .venv; fi
	.venv/bin/python -m pip install -r requirements-dev.txt

test:
	"$(PYTHON)" -m pytest

smoke:
	"$(PYTHON)" tests/smoke.py

qa:
	"$(PYTHON)" demo/watermark_qa.py

# The real-model experiment is optional; normal setup/tests never download models.
model-tools:
	@if [ ! -x demo/.venv/bin/python ]; then "$(PYTHON)" -m venv demo/.venv; fi
	demo/.venv/bin/python -m pip install -r demo/requirements.txt

qa-model:
	demo/.venv/bin/python demo/model_watermark_qa.py $(MODEL_QA_ARGS)

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

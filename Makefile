.PHONY: test smoke tools install-skill qa clean

SCRIPTS := skills/ai-watermark-scrambler/scripts
PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

tools:
	"$(PYTHON)" $(SCRIPTS)/ensure_tools.py --install

test:
	"$(PYTHON)" -m pytest

smoke:
	"$(PYTHON)" tests/smoke.py

qa:
	"$(PYTHON)" demo/watermark_qa.py

SKILLS_DIR ?= $(HOME)/.grok/skills

install-skill:
	mkdir -p $(SKILLS_DIR)
	ln -sfn $(CURDIR)/skills/ai-watermark-scrambler $(SKILLS_DIR)/ai-watermark-scrambler
	@echo "linked -> $(SKILLS_DIR)/ai-watermark-scrambler"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .venv skills/ai-watermark-scrambler/.venv

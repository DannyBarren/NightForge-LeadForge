.PHONY: help install test dry-run sample demo ui

PYTHON ?= python3

help:
	@echo "NightForge LeadForge — common commands"
	@echo ""
	@echo "  make install    Install Python dependencies"
	@echo "  make test       Run the test suite (pytest -q)"
	@echo "  make dry-run    Validate config + API keys (no LLM calls)"
	@echo "  make sample     End-to-end sample run (3 leads)"
	@echo "  make demo       Live-demo driver: dry-run -> sample -> newest CSV + cost"
	@echo "  make ui         Launch the Streamlit UI"

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest tests/ -q

dry-run:
	$(PYTHON) scripts/run_overnight.py --dry-run

sample:
	$(PYTHON) scripts/run_overnight.py --sample

demo:
	@bash scripts/demo.sh

ui:
	$(PYTHON) -m streamlit run app.py

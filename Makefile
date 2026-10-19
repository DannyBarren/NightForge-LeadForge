.PHONY: help install test lint eval dry-run sample demo ui research-sample score-sample serve

PYTHON ?= python3

help:
	@echo "NightForge LeadForge — common commands"
	@echo ""
	@echo "  Production (nightforge/)"
	@echo "  make install          Install pinned Python dependencies"
	@echo "  make test             pytest -q"
	@echo "  make lint             ruff check ."
	@echo "  make eval             Offline labeled eval harness (no paid APIs)"
	@echo "  make research-sample  LangGraph sample run, 3 households, no keys needed"
	@echo "  make score-sample     Score the demo demand events (offline)"
	@echo "  make serve            FastAPI on NIGHTFORGE_HOST:NIGHTFORGE_PORT"
	@echo ""
	@echo "  Prototype (leadforge/ + Streamlit)"
	@echo "  make dry-run          Validate config + API keys (no LLM calls)"
	@echo "  make sample           CrewAI sample run (3 leads)"
	@echo "  make demo             Live-demo driver: dry-run -> sample -> newest CSV"
	@echo "  make ui               Launch the Streamlit UI"

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest tests/ -q

lint:
	$(PYTHON) -m ruff check .

eval:
	$(PYTHON) -m evals.harness

research-sample:
	$(PYTHON) -m nightforge.cli research --sample

score-sample:
	$(PYTHON) -m nightforge.cli score --sample

serve:
	$(PYTHON) -m nightforge.cli serve

dry-run:
	$(PYTHON) scripts/run_overnight.py --dry-run

sample:
	$(PYTHON) scripts/run_overnight.py --sample

demo:
	@bash scripts/demo.sh

ui:
	$(PYTHON) -m streamlit run app.py

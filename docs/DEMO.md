# 5-minute demo

Setup before the call: virtualenv active, `pip install -r requirements.txt` done, `OPENAI_API_KEY` in `.env`. A `TAVILY_API_KEY` is nice but not required. Have a terminal and a browser window ready.

## Minute 0–2: the CLI

```bash
make demo
```

Three blocks scroll by. Narrate them:

1. **Dry-run.** Prints the location, industries, model, max leads, budget, and early-stop, then the preflight result. No LLM call has happened yet. Point out that a missing key fails here, for free, and the error names the variable and the URL to fix it.
2. **Sample run.** Three leads through discovery, research, and pitch. Parallel research is capped at 4 so a live demo does not trip search rate limits.
3. **Newest output.** The CSV path, the row count, the estimated cost, and the token log path.

The line to stop on:

```text
DEMO READY: 3 lead(s) | est. cost $0.00xx
  CSV:  data/output/leads_<run_id>.csv
```

Then open the token log and scroll it. Every agent call is a row: agent, phase, which lead, input tokens, output tokens, dollars, running total. Say: this is why the run can stop before the next call instead of after the invoice.

## Minute 2–5: the UI

```bash
make ui
```

Walk the four tabs. On **Configuration**, turn **Sample mode** on before running.

- **1. Setup** — dependency check and key status. Keys are password fields; saved values go to a local `.env` with owner-only permissions.
- **2. Configuration** — zip code, max leads, budget slider, industries, workers, sample mode. The budget slider stops at $10 and warns above $8.
- **3. Run Pipeline** — the preflight panel. The Start button is disabled until preflight passes. Start it and let the progress bar walk the phases.
- **4. Results** — the metrics row (discovered, researched, pitched, estimated cost, budget left), then the table, then expand one lead: pains, Barren fit, the full draft email, sources. Every row shows `HUMAN REVIEW: YES`.

Close on the export: this CSV is the deliverable, and a person reads it before anything leaves the building.

## If a search key is missing

Preflight warns instead of failing, and search falls back to DuckDuckGo. Say it out loud: no key means DuckDuckGo, which is slower and the lead quality drops. Sample mode still produces three complete rows, because seed leads and the fallback pitch cover for it. That is the point — the demo does not depend on a third-party API being up.

## What not to do on a call

- Do not toggle human review off. Every row ships `Human Review: YES` and that is the product.
- Do not raise the budget past $10. Preflight will refuse it and you will spend the demo debugging your own guardrail.
- Do not run full mode (30 leads) live. It is minutes long and costs real money. Sample mode is the demo.
- Do not claim it sends email. It does not, and there is no code path that could.

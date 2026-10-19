# 5-minute demo

Setup: virtualenv active, `pip install -r requirements.txt` done. **No API key is required** for the production sample path. Have a terminal ready.

## Minute 0–2: score and research, offline

```bash
make score-sample
make research-sample
```

Narrate `score-sample` first. Three fictional households, three bands:

- HVAC 85001 — three public signals in 48 hours → **hot**
- Plumbing 78701 — one strong ask plus a weather flag → **warm**
- Electrical 80202 — one weak mention → **log**

Warm is kept visible on purpose. Nothing is filtered out for you. Every lead still says human review is required.

Then `research-sample`. Same three households through the LangGraph: discover (seeds), research (fallback dossier), pitch (deterministic draft), export, emit signals. Point at:

- `Human Review: YES` on every row
- estimated cost `$0.0000` with no keys
- the scored artifact the graph emits so `score --sample` and `research --sample` agree

If you have an `OPENAI_API_KEY` and want to show a dry-run gate:

```bash
python -m nightforge.cli research --dry-run
```

No LLM call happens. Preflight and the governor's first `can_afford` print and stop.

## Minute 2–4: the eval report

```bash
make eval
```

Twelve labeled cases, all offline. Scoring policy, governor admission, sample-graph export, no send routes, webhook fail-closed, human review pinned. This is the artifact to leave on screen if someone asks "how do you know it still does the thing."

## Minute 4–5: the API surface

```bash
make serve
```

`GET http://127.0.0.1:8000/health` — governor ready, adapter stub. Mention what is missing on purpose: no `/send`, no OAuth helper, no vendor write. `POST /webhooks/jobber` without a signature is rejected.

## Optional: Streamlit prototype

The CrewAI shop-finding UI still exists:

```bash
make ui
```

Walk Setup → Configuration (sample mode on) → Run → Results. Do not present this as the production architecture. It is the frozen prototype sitting beside `nightforge/`.

## What not to do on a call

- Do not claim it sends email or texts a homeowner. It does not.
- Do not run a 30-lead live job. It costs real money and is minutes long.
- Do not invent Jobber credentials. The adapter is a stub until a real secret exists outside the repo.
- Do not raise the budget past $10. Preflight will refuse it.

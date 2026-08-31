# Architecture

One run is: preflight → discovery → parallel research → pitch → export. `leadforge/main.py` owns that sequence. Everything else is a part it calls.

## Files

| File | Job |
| --- | --- |
| `leadforge/main.py` | Orchestrates the run. Owns `run_pipeline`, the sample-mode seed top-up, the fallback pitch, and the progress callback the UI subscribes to. |
| `leadforge/agents.py` | Three CrewAI `Agent` definitions: `lead_discovery_agent`, `research_agent`, `pitch_strategist_agent`. Role, goal, backstory, tools, `max_iter`, `max_rpm`. |
| `leadforge/tasks.py` | Prompt templates and the exact JSON shape each agent must return. |
| `leadforge/tools.py` | `UnifiedWebSearchTool` (Tavily → Brave → DuckDuckGo), `FetchPublicPageTool` (single GET, HTML stripped, truncated), `GoogleSheetsAppendTool`. |
| `leadforge/models.py` | Pydantic v2 models: `DiscoveredLead`, `LeadResearch`, `PitchOutput`, `RunManifest`, and `to_export_row`. |
| `leadforge/config_loader.py` | Merges `config/settings.yaml` with `LEADFORGE_*` env vars into an `AppConfig`. Clamps budget to $10 and workers to 8. |
| `leadforge/run_context.py` | `RunOptions` — per-run overrides from the UI or CLI, applied on top of `AppConfig` without mutating it. |
| `leadforge/preflight.py` | Key and config validation before any spend. |
| `leadforge/cost_tracker.py` | Thread-safe token and dollar accounting, budget decisions, `token_usage_<run_id>.json`. |
| `leadforge/guardrails.py` | Lead cap, per-phase budget checks, remaining-pipeline projection, worker cap, stop state. |
| `leadforge/json_utils.py` | Pulls the first JSON array or object out of agent output, with a `json_repair` pass. Raises only `ValueError`. |
| `leadforge/llm_factory.py` | Builds the CrewAI `LLM` for openai, anthropic, or xai. Raises if the provider key is missing. |
| `leadforge/sample_data.py` | Three labeled demo seed leads with `example.com` sites and 555 numbers. |
| `leadforge/export.py` | Writes CSV, JSON, and manifest. Owns `EXPORT_COLUMNS`. Optional Sheets append. |
| `app.py` | Streamlit UI: Setup, Configuration, Run Pipeline, Results. |
| `scripts/run_overnight.py` | CLI: `--dry-run`, `--sample`, `--max-leads N`. |
| `scripts/demo.sh` | The `make demo` driver. |

`agents.py`, `tasks.py`, `tools.py`, and `main.py` also exist at the repo root as thin re-export shims over the `leadforge/` versions.

## Preflight

`run_preflight(config)` returns `PreflightResult(ok, errors, warnings)`. It runs before any LLM call — from the CLI, from `run_pipeline`, and from the UI's Run tab, where the Start button stays disabled until it passes.

Errors (run blocked):

- Provider key missing for the configured provider. The message names the variable, the fix, and the URL to get a key.
- Unknown `LEADFORGE_LLM_PROVIDER`.
- `config/settings.yaml` missing.
- `LEADFORGE_BUDGET_USD` above the $10 ceiling.
- `LEADFORGE_STOP_PROJECTED_USD` above the budget.

Warnings (run proceeds):

- No `TAVILY_API_KEY` or `BRAVE_API_KEY` — search falls back to DuckDuckGo, which is slower and less reliable.
- Budget between $8 and $10 — over the recommended cap, under the ceiling.

## Sample mode and fallbacks

Sample mode exists so a live demo cannot dead-end on someone else's API. `RunOptions(sample_mode=True)` caps leads at 3 and parallel research at 4 (`SAMPLE_PARALLEL_RESEARCH_CAP`). Three fallbacks stack on top:

1. **Discovery failure.** In sample mode a discovery exception is logged and swallowed instead of raised. Budget stops still propagate.
2. **Seed top-up.** If discovery returns fewer leads than the target, `sample_seed_leads()` fills the gap, skipping any company already discovered. Outside sample mode this never runs.
3. **Per-lead fallbacks.** A failed research call returns a `LeadResearch` built from the discovery-stage fields. A failed pitch call returns `_fallback_pitch`, a deterministic `PitchOutput` with a complete draft email, `confidence=low`, and `human_review_required=True`.

The result: a sample run produces three fully populated rows even when search and the LLM are both misbehaving.

## CostTracker

`CostTracker` holds a `threading.Lock` because research runs in a thread pool. Every mutation and every budget read goes through it.

- `estimate_usd(in, out)` prices tokens from `pricing_usd_per_million` in `settings.yaml`, falling back to `DEFAULT_PRICING` for unknown models.
- `record(...)` appends a `UsageRecord` (agent, phase, lead, model, tokens, dollars, running total). `record_from_crew_usage(...)` reads CrewAI's usage object when it exposes one; `main._run_crew` falls back to a chars/4 heuristic when it does not, so an unmetered call still costs something on the books.
- `can_afford(in, out)` is the pre-call gate. `should_stop()` is the post-call check.
- Two thresholds: actual spend at or above `budget_usd`, or projected spend at or above `min(stop_projected_usd, budget_usd)`. Either one stops the run.
- `write_log(run_id)` writes `token_usage_<run_id>.json` with the full per-call record list.

## Guardrails

`Guardrails` wraps `CostTracker` with pipeline-shaped checks and holds the run's stop state.

- `cap_leads(leads)` truncates the discovery list to `max_leads_processed` and logs the cut. This is the last line of defense against an agent returning 200 leads.
- `check_budget_before_phase(phase, est_in, est_out)` raises `GuardrailViolation` if the phase cannot be afforded.
- `check_remaining_pipeline(...)` projects the cost of all *remaining* research and pitch work using the `EST_*` constants, so the run stops when it can no longer finish, not when it has already overspent.
- `max_parallel_research()` reads `LEADFORGE_PARALLEL_RESEARCH` if set, otherwise config, and clamps to 1–8 either way. `RunOptions.effective_parallel_research` applies the same 1–8 clamp and the sample-mode cap of 4.
- `mark_stopped` / `sync_stop_from_cost` mirror the tracker's decision into run state. A `GuardrailViolation` is caught in `run_pipeline` and recorded on the manifest as `stopped_early` with a `stop_reason`. Whatever was completed still exports. A budget stop is not a crash.

## Export contract

`export.EXPORT_COLUMNS` and `PitchOutput.to_export_row()` define one shape, used by the CSV writer, the optional Sheets append, and the UI table:

`Company`, `Owner`, `Email`, `Phone`, `LinkedIn`, `Industry`, `Location`, `Lead Scope`, `Pains`, `Barren Fit`, `Draft Email`, `Pitch Angle`, `Specific Value Props`, `Recommended Next Step`, `Confidence`, `Human Review`, `Sources`

Rules the writer enforces: `csv.DictWriter` uses `extrasaction="ignore"`, so an extra model field cannot silently widen the file. List fields are joined with ` | ` and sources are capped at 5. `Human Review` is `"YES"` whenever `human_review_required` is true, and `main._pitch_one` sets that flag to `True` on every pitch after validation — including LLM output that tried to set it false. There is no export path that produces `NO`.

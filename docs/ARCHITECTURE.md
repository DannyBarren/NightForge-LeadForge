# Architecture

The production system is `nightforge/`: a LangGraph research graph, a governor, a scoring spine, and a FastAPI edge. `leadforge/` is the CrewAI + Streamlit prototype that the production package sits beside. Shared contracts (`DiscoveredLead`, `LeadResearch`, `PitchOutput`, `RunManifest`, `EXPORT_COLUMNS`) live in `leadforge/models.py` and `leadforge/export.py` so the CSV shape does not fork.

## Production: `nightforge/`

One research run is:

`preflight → governor_discovery → discover → governor_research → research → governor_pitch → pitch → export → emit_signals`

Each spending phase is wrapped in a conditional edge. If the governor refuses, the graph goes to `halt`, then still `export`s whatever completed.

The graph looks for **homeowners**, not shops. Prompts are in `nightforge/discovery/prompts.py`. Demo seeds are in `nightforge/discovery/sample_demand.py` and are the same three households as `nightforge/scoring/sample_events.py`.

### Files

| File | Job |
| --- | --- |
| `nightforge/graphs/research.py` | LangGraph assembly and node implementations. Parallel research is a thread pool inside one node so submit-time budget projection matches the prototype. |
| `nightforge/graphs/inbound.py` | Placeholder. No nodes yet. |
| `nightforge/governor.py` | Pre-call gate: per-run cap, daily ceiling, loop breakers, ledger. Search is metered here; it was not in the prototype. |
| `nightforge/scoring/` | `features` → `heuristic` → `assigner` → ranked JSON. `train` counts labels and will not fit until the threshold is met. |
| `nightforge/api/app.py` | Health, runs, ingest, leads, decisions, Jobber webhook. No send/OAuth routes. |
| `nightforge/adapters/` | `FieldServiceAdapter` protocol. Jobber verifies HMAC-SHA256; writes raise "stub". |
| `nightforge/cli.py` | `research`, `score`, `train`, `serve`. |
| `nightforge/config.py` | Process settings (`NIGHTFORGE_*`). Per-run budget is not a process global. |

### Governor

Four layers, fail closed:

1. **Per-run cap.** $8 recommended, $10 hard ceiling, $7.25 projected early stop. Same numbers as the prototype.
2. **Per-day, per-client ceiling.** A run that would exceed it is deferred and never starts.
3. **Loop protection.** Iterations, tool calls, identical repeats, retryable HTTP. Budget is a poor backstop against a wedged agent.
4. **Ledger.** Unmetered calls use a chars/4 floor. A call is never booked at zero.

`GovernorHalt` is a clean stop. `GovernorDeferred` means "try later." `BreakerTripped` is a halt subclass.

### Scoring

Rules, in order, from `nightforge/scoring/heuristic.py`:

1. Already a customer, out of area, or duplicate in 30 days → `DROP`, score 0.
2. Three or more agreeing signals in 48 hours → `hot`.
3. Two signals, or one strong signal plus a weather trigger → `warm`.
4. One weak signal → `log`.
5. Qualifying leads go to `REVIEW`. Nothing emits `ROUTE`.

Weather events set a flag and do not count as demand signals. Strong sources are `community_board_post`, `public_qa_thread`, `public_review`.

### Service edge

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | Governor + adapter state. Import-light. |
| POST | `/runs/research` | Sample mode by default. |
| GET | `/runs/{run_id}` | In-memory store. |
| POST | `/ingest/signals` | Public demand events. |
| GET | `/leads` | Ranked list, every band. |
| POST | `/leads/{lead_id}/decision` | Human accept/reject; later job outcome. |
| POST | `/webhooks/jobber` | Verify and store. Fail closed. |

Run and lead stores are process-local. Artifacts on disk survive a restart; the in-memory index does not.

## Prototype: `leadforge/` (kept, not deleted)

One prototype run is: preflight → discovery → parallel research → pitch → export. `leadforge/main.py` owns that sequence. Three CrewAI agents search for **shops**, not homeowners.

Root `agents.py`, `tasks.py`, `tools.py`, and `main.py` are thin re-export shims. `app.py` is the Streamlit UI. `scripts/run_overnight.py` and `scripts/demo.sh` drive the prototype CLI.

See the file table in git history on `archive/crewai-prototype` if you need the original narrative. The production README does not treat this path as current.

### What carried over

- Pydantic contracts and `EXPORT_COLUMNS`
- Pre-call budget idea (ported, then extended)
- Sample mode's guarantee of 3 complete rows
- `Human Review: YES` and the absence of a send path

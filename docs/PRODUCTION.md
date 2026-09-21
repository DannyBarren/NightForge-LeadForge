# Production branches

This repo carries three long-lived branches. They are not stages of one line of work —
each one has a different job, and only one of them is where code gets written.

| Branch | What it is | Commit here? |
| --- | --- | --- |
| `archive/crewai-prototype` | Frozen CrewAI + Streamlit snapshot | No |
| `production/langgraph` | LangGraph + FastAPI working branch | Yes |
| `main` | Public portfolio snapshot | No |

## `archive/crewai-prototype`

The frozen CrewAI + Streamlit snapshot. It is the pipeline described in
[ARCHITECTURE.md](ARCHITECTURE.md): `leadforge/main.py` orchestrating discovery →
parallel research → pitch → export, three CrewAI agents, and the Streamlit UI in
`app.py`.

It is kept as a reference point, not as a maintained branch. Nothing new lands here.

## `production/langgraph`

The working branch. This is where the LangGraph + FastAPI production system gets built,
in a new `nightforge/` package that lives **beside** the prototype rather than replacing
it. `leadforge/`, `app.py`, and the CrewAI pipeline stay in the tree and stay working.

What carries over rather than being rewritten:

- The Pydantic contracts in `leadforge/models.py` — `DiscoveredLead`, `LeadResearch`,
  `PitchOutput`, `RunManifest`.
- `CostTracker` and `Guardrails`, ported into the new package. The pre-call budget gate
  is the point of the whole design; it does not get dropped in the port.
- Sample mode's guarantee of 3 complete rows when search or the LLM is down.
- `Human Review: YES` on every export row, and the absence of any send path.

All branches for agent work (`agent/*`, `cursor/*`) open their pull requests into
`production/langgraph`.

## `main`

The public portfolio snapshot. It is what someone sees when they open the repo, and it
is **not** the working branch. It moves only when a finished piece of work is promoted
to it deliberately.

Because it is public: no pricing, no DPA text, no commercial strategy, no client names.

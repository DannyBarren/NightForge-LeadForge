"""Streamlit UI for NightForge LeadForge."""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import dotenv_values, load_dotenv, set_key

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
OUTPUT_DIR = ROOT / "data" / "output"

MIN_LEADS = 5
MAX_LEADS = 40
DEFAULT_MAX_LEADS = 30
RECOMMENDED_BUDGET_USD = 8.0
HARD_BUDGET_CEILING_USD = 10.0
DEFAULT_PARALLEL_RESEARCH = 6

DEFAULT_INDUSTRIES = [
    "HVAC",
    "plumbing",
    "electrical",
    "roofing",
    "general contractor",
    "garage doors",
    "pest control",
    "auto repair",
]

DEPENDENCY_IMPORTS = {
    "streamlit": "streamlit",
    "crewai": "crewai",
    "langchain": "langchain",
    "tavily-python": "tavily",
    "duckduckgo-search": "duckduckgo_search",
    "pandas": "pandas",
    "python-dotenv": "dotenv",
}

PHASE_PROGRESS = {
    "preflight": 5,
    "discovery": 15,
    "discovery_complete": 35,
    "research": 45,
    "research_complete": 70,
    "pitch": 78,
    "pitch_complete": 92,
    "complete": 100,
    "stopped": 100,
}

RESULTS_PREVIEW_COLUMNS = [
    "Company",
    "Owner",
    "Industry",
    "Location",
    "Lead Scope",
    "Email",
    "Phone",
    "Confidence",
]

SECRET_KEYS = {
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
}


def configure_page() -> None:
    st.set_page_config(
        page_title="NightForge LeadForge",
        page_icon="🌙",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        :root {
            --nf-accent: #6366f1;
            --nf-accent-2: #22d3ee;
            --nf-border: rgba(255, 255, 255, 0.08);
            --nf-panel: rgba(255, 255, 255, 0.03);
            --nf-muted: #94a3b8;
        }
        .main .block-container { padding-top: 1.6rem; max-width: 1240px; }
        div[data-testid="stMetricValue"] { font-size: 1.55rem; font-weight: 700; }
        div[data-testid="stMetric"] {
            background: var(--nf-panel);
            border: 1px solid var(--nf-border);
            border-radius: 0.85rem;
            padding: 0.75rem 1rem;
        }

        .hero-card {
            border: 1px solid var(--nf-border);
            border-radius: 1.1rem;
            padding: 1.5rem 1.7rem;
            background:
                radial-gradient(1200px 200px at 0% 0%, rgba(99,102,241,0.22), transparent 60%),
                radial-gradient(1200px 200px at 100% 100%, rgba(34,211,238,0.16), transparent 55%),
                rgba(255,255,255,0.02);
            margin-bottom: 1.1rem;
        }
        .hero-title {
            font-size: 2.0rem; font-weight: 800; letter-spacing: -0.02em;
            margin: 0 0 0.35rem 0;
            background: linear-gradient(90deg, #c7d2fe, #a5f3fc);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .hero-sub { color: #cbd5e1; font-size: 1.02rem; margin: 0; }
        .hero-steps { color: var(--nf-muted); font-size: 0.92rem; margin-top: 0.7rem; }

        .leadforge-card {
            border: 1px solid var(--nf-border);
            border-radius: 0.9rem;
            padding: 1rem 1.2rem;
            background: var(--nf-panel);
        }
        .small-muted { color: var(--nf-muted); font-size: 0.9rem; }

        .nf-pill {
            display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
            font-size: 0.75rem; font-weight: 700; letter-spacing: 0.02em;
            border: 1px solid var(--nf-border); margin-right: 0.35rem;
        }
        .nf-pill-high { background: rgba(34,197,94,0.15); color: #86efac; border-color: rgba(34,197,94,0.3); }
        .nf-pill-medium { background: rgba(234,179,8,0.15); color: #fde68a; border-color: rgba(234,179,8,0.3); }
        .nf-pill-low { background: rgba(148,163,184,0.15); color: #cbd5e1; border-color: rgba(148,163,184,0.3); }
        .nf-pill-review { background: rgba(99,102,241,0.16); color: #c7d2fe; border-color: rgba(99,102,241,0.32); }
        .nf-pill-scope { background: rgba(34,211,238,0.14); color: #a5f3fc; border-color: rgba(34,211,238,0.3); }

        .stTabs [data-baseweb="tab-list"] { gap: 0.4rem; }
        .stTabs [data-baseweb="tab"] {
            border-radius: 0.7rem 0.7rem 0 0; padding: 0.3rem 0.9rem;
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(90deg, var(--nf-accent), #818cf8);
            border: none; font-weight: 700;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def read_env() -> dict[str, str]:
    load_dotenv(ENV_PATH, override=False)
    return {k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None}


def dependency_status() -> dict[str, bool]:
    return {
        package: importlib.util.find_spec(import_name) is not None
        for package, import_name in DEPENDENCY_IMPORTS.items()
    }


def render_environment_check() -> None:
    statuses = dependency_status()
    missing = [pkg for pkg, ok in statuses.items() if not ok]

    st.subheader("Environment check")
    st.caption(f"Python {sys.version.split()[0]} at `{sys.executable}`")
    cols = st.columns(4)
    for idx, (pkg, ok) in enumerate(statuses.items()):
        cols[idx % 4].metric(pkg, "Installed" if ok else "Missing")

    if missing:
        st.error("Install missing dependencies before running the pipeline.")
        st.code(
            "python3 -m venv .venv\n"
            "source .venv/bin/activate\n"
            "pip install -r requirements.txt\n"
            "streamlit run app.py",
            language="bash",
        )
    else:
        st.success("Core dependencies look installed.")


def parse_industries(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def valid_zip(zip_code: str) -> bool:
    return bool(re.fullmatch(r"\d{5}(-\d{4})?", zip_code.strip()))


def as_int(value: str | int | None, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def as_float(value: str | float | None, default: float, *, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def display_secret_state(settings: dict[str, str], key: str) -> str:
    return "Saved" if settings.get(key) or os.getenv(key) else "Missing"


def apply_runtime_env(settings: dict[str, Any], *, save_to_file: bool) -> None:
    if save_to_file:
        ENV_PATH.touch(exist_ok=True)

    for key, value in settings.items():
        if value is None:
            continue
        string_value = str(value).strip()
        if not string_value:
            continue
        os.environ[key] = string_value
        if save_to_file:
            set_key(str(ENV_PATH), key, string_value)

    if save_to_file:
        try:
            ENV_PATH.chmod(0o600)
        except OSError:
            pass

    os.environ.setdefault("CREWAI_TELEMETRY", "false")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    try:
        from leadforge.config_loader import get_config

        get_config.cache_clear()
    except Exception:
        pass


def current_settings(existing_env: dict[str, str]) -> dict[str, str]:
    keys = [
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "ANTHROPIC_API_KEY",
        "XAI_API_KEY",
        "LEADFORGE_LLM_PROVIDER",
        "LEADFORGE_MODEL",
        "LEADFORGE_TARGET_ZIP",
        "LEADFORGE_TARGET_INDUSTRIES",
        "LEADFORGE_MAX_LEADS",
        "LEADFORGE_BUDGET_USD",
        "LEADFORGE_STOP_PROJECTED_USD",
        "LEADFORGE_PARALLEL_RESEARCH",
    ]
    return {key: os.getenv(key) or existing_env.get(key, "") for key in keys}


def validate_inputs(
    *,
    zip_code: str,
    industries: list[str],
    max_leads: int,
    budget_cap: float,
    parallel_research: int,
    provider: str,
    keys: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    if not valid_zip(zip_code):
        errors.append("Enter a valid 5-digit US zip code, for example 85001.")
    if not industries:
        errors.append("Enter at least one target industry.")
    if max_leads < MIN_LEADS or max_leads > MAX_LEADS:
        errors.append(f"Max leads must be between {MIN_LEADS} and {MAX_LEADS}.")
    if budget_cap < 1 or budget_cap > HARD_BUDGET_CEILING_USD:
        errors.append("Budget cap must be between $1 and the hard $10 ceiling.")
    if budget_cap > RECOMMENDED_BUDGET_USD:
        errors.append("Budget cap is above the recommended $8. Lower it unless you intentionally want the extra room.")
    if parallel_research < 1 or parallel_research > 8:
        errors.append("Parallel research workers must be between 1 and 8.")
    required_key = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "xai": "XAI_API_KEY",
    }.get(provider)
    if required_key and not keys.get(required_key):
        errors.append(f"{required_key} is required when provider={provider}.")
    return errors


def build_options(
    *,
    zip_code: str,
    industries: list[str],
    max_leads: int,
    budget_cap: float,
    parallel_research: int,
    provider: str,
    model: str,
    sample_mode: bool,
) -> Any:
    from leadforge.run_context import RunOptions

    return RunOptions(
        sample_mode=sample_mode,
        max_leads=max_leads,
        target_zip=zip_code,
        target_industries=industries,
        budget_usd=budget_cap,
        stop_projected_usd=max(0.5, min(budget_cap * 0.9, budget_cap)),
        parallel_research=parallel_research,
        llm_provider=provider,
        llm_model=model,
    )


def load_results(summary: dict[str, Any] | None) -> tuple[Any | None, Path | None]:
    csv_path: Path | None = None
    if summary:
        path_value = summary.get("export_paths", {}).get("csv")
        if path_value:
            csv_path = Path(path_value)

    if not csv_path or not csv_path.exists():
        candidates = sorted(OUTPUT_DIR.glob("leads_*.csv"), key=lambda p: p.stat().st_mtime)
        csv_path = candidates[-1] if candidates else None

    if not csv_path or not csv_path.exists():
        return None, None

    import pandas as pd

    try:
        return pd.read_csv(csv_path).fillna(""), csv_path
    except pd.errors.EmptyDataError:
        return None, csv_path


def render_sidebar(settings: dict[str, str] | None = None) -> None:
    st.sidebar.markdown("## 🌙 NightForge")
    st.sidebar.caption("Autonomous lead discovery · public research · Barren outreach drafts")
    st.sidebar.divider()

    if settings is not None:
        st.sidebar.markdown("**Keys**")
        checks = [
            ("OpenAI", "OPENAI_API_KEY"),
            ("Tavily", "TAVILY_API_KEY"),
            ("Brave", "BRAVE_API_KEY"),
        ]
        for label, key in checks:
            ok = bool(settings.get(key) or os.getenv(key))
            st.sidebar.markdown(
                f"{'🟢' if ok else '⚪'} {label} — {'ready' if ok else 'not set'}"
            )
        st.sidebar.divider()

    st.sidebar.markdown("**Best first run**")
    st.sidebar.caption("Zip code + Tavily key + 20–35 leads + $8 cap + 4–6 workers.")
    st.sidebar.markdown("**Live demo (fastest)**")
    st.sidebar.code("make demo", language="bash")
    st.sidebar.divider()
    st.sidebar.markdown("**Safety**")
    st.sidebar.caption("Public data only · human review required · no emails are sent.")


def _lead_badges(confidence: str, scope: str, human_review: str) -> str:
    conf = confidence if confidence in ("high", "medium", "low") else "low"
    pills = [f'<span class="nf-pill nf-pill-{conf}">{confidence.upper()} CONFIDENCE</span>']
    if scope:
        pills.append(f'<span class="nf-pill nf-pill-scope">{scope.upper()}</span>')
    pills.append(f'<span class="nf-pill nf-pill-review">HUMAN REVIEW: {human_review or "YES"}</span>')
    return " ".join(pills)


def render_results(summary: dict[str, Any] | None) -> None:
    st.header("Results")
    df, csv_path = load_results(summary)

    if summary:
        col_a, col_b, col_c, col_d, col_e = st.columns(5)
        col_a.metric("Discovered", summary.get("leads_discovered", 0))
        col_b.metric("Researched", summary.get("leads_researched", 0))
        col_c.metric("Pitched", summary.get("leads_pitched", 0))
        col_d.metric("Est. cost", f"${summary.get('estimated_cost_usd', 0):.4f}")
        remaining = summary.get("cost_summary", {}).get("budget_remaining_usd")
        col_e.metric("Budget left", f"${remaining:.4f}" if remaining is not None else "n/a")
        if summary.get("stopped_early"):
            st.warning(f"Run stopped early: {summary.get('stop_reason')}")
        if summary.get("token_log"):
            st.caption(f"Token usage log: {summary['token_log']}")

    if df is None or csv_path is None:
        st.info("No results yet. Run the pipeline to generate leads.")
        return

    st.success(f"Loaded {len(df)} lead rows from `{csv_path.name}`.")
    st.caption("Every row is a draft and requires human review before any outreach. No emails are sent.")
    preview_cols = [col for col in RESULTS_PREVIEW_COLUMNS if col in df.columns]
    st.dataframe(df[preview_cols] if preview_cols else df, use_container_width=True, hide_index=True)

    dl_cols = st.columns(2)
    dl_cols[0].download_button(
        "⬇ Download CSV",
        data=csv_path.read_bytes(),
        file_name=csv_path.name,
        mime="text/csv",
        use_container_width=True,
    )
    json_path = csv_path.with_suffix(".json")
    if json_path.exists():
        dl_cols[1].download_button(
            "⬇ Download JSON",
            data=json_path.read_bytes(),
            file_name=json_path.name,
            mime="application/json",
            use_container_width=True,
        )

    st.subheader("Lead details")
    for idx, row in df.iterrows():
        company = str(row.get("Company", f"Lead {idx + 1}"))
        confidence = str(row.get("Confidence", "")).strip().lower() or "unknown"
        scope = str(row.get("Lead Scope", "")).strip()
        title = f"{idx + 1}.  {company}  ·  {confidence} confidence"
        if scope:
            title += f"  ·  {scope}"
        with st.expander(title):
            st.markdown(_lead_badges(confidence, scope, str(row.get("Human Review", "YES"))), unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            c1.markdown(f"**Owner:** {row.get('Owner', '') or 'Unknown'}")
            c1.markdown(f"**Industry:** {row.get('Industry', '')}")
            c1.markdown(f"**Location:** {row.get('Location', '')}")
            c2.markdown(f"**Email:** {row.get('Email', '') or '_Not found_'}")
            c2.markdown(f"**Phone:** {row.get('Phone', '') or '_Not found_'}")
            c2.markdown(f"**LinkedIn:** {row.get('LinkedIn', '') or '_Not found_'}")

            if row.get("Recommended Next Step", ""):
                st.info(f"**Next step:** {row.get('Recommended Next Step', '')}")

            st.markdown("**Pains**")
            st.write(row.get("Pains", ""))
            st.markdown("**Barren Fit**")
            st.write(row.get("Barren Fit", ""))
            st.markdown("**Draft Email**")
            st.code(str(row.get("Draft Email", "")), language="text")
            if row.get("Specific Value Props", ""):
                st.markdown("**Specific Value Props**")
                st.write(row.get("Specific Value Props", ""))
            if row.get("Sources", ""):
                st.markdown("**Sources**")
                for source in str(row.get("Sources", "")).split(" | "):
                    if source:
                        st.write(source)


def main() -> None:
    configure_page()
    existing_env = read_env()
    settings = current_settings(existing_env)
    render_sidebar(settings)

    st.markdown(
        """
        <div class="hero-card">
        <p class="hero-title">NightForge LeadForge</p>
        <p class="hero-sub">Discover trades SMB leads, research public buying signals, and draft
        personalized Barren outreach — all under a hard $10 budget.</p>
        <p class="hero-steps">1 · Add keys &nbsp;→&nbsp; 2 · Set your zip &amp; targets &nbsp;→&nbsp;
        3 · Run the pipeline &nbsp;→&nbsp; 4 · Review &amp; export leads</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    setup_tab, config_tab, run_tab, results_tab = st.tabs(
        ["1. Setup", "2. Configuration", "3. Run Pipeline", "4. Results"]
    )

    with setup_tab:
        st.header("Setup")
        render_environment_check()
        st.divider()

        st.subheader("API keys")
        st.caption("Password fields are not echoed. If enabled, saved values go to local `.env` with owner-only permissions where supported.")
        save_to_env = st.toggle("Save keys and settings to .env", value=True)

        status_cols = st.columns(3)
        status_cols[0].metric("OpenAI", display_secret_state(settings, "OPENAI_API_KEY"))
        status_cols[1].metric("Tavily", display_secret_state(settings, "TAVILY_API_KEY"))
        status_cols[2].metric("Brave", display_secret_state(settings, "BRAVE_API_KEY"))

        col_a, col_b = st.columns(2)
        with col_a:
            openai_input = st.text_input("OpenAI API key", type="password", placeholder="sk-... or already saved")
            tavily_input = st.text_input("Tavily API key (recommended)", type="password", placeholder="tvly-... or already saved")
        with col_b:
            brave_input = st.text_input("Brave Search API key (optional fallback)", type="password", placeholder="optional")
            provider_options = ["openai", "anthropic", "xai"]
            selected_provider = settings["LEADFORGE_LLM_PROVIDER"] or "openai"
            provider = st.selectbox(
                "LLM provider",
                provider_options,
                index=provider_options.index(selected_provider) if selected_provider in provider_options else 0,
            )
            model = st.text_input("Model", value=settings["LEADFORGE_MODEL"] or "gpt-4o-mini")

        with st.expander("Optional alternate provider keys"):
            anthropic_input = st.text_input("Anthropic API key", type="password", placeholder="optional")
            xai_input = st.text_input("xAI API key", type="password", placeholder="optional")

        if st.button("Save setup", type="primary"):
            secret_settings = {
                "OPENAI_API_KEY": openai_input or settings["OPENAI_API_KEY"],
                "TAVILY_API_KEY": tavily_input or settings["TAVILY_API_KEY"],
                "BRAVE_API_KEY": brave_input or settings["BRAVE_API_KEY"],
                "ANTHROPIC_API_KEY": anthropic_input or settings["ANTHROPIC_API_KEY"],
                "XAI_API_KEY": xai_input or settings["XAI_API_KEY"],
                "LEADFORGE_LLM_PROVIDER": provider,
                "LEADFORGE_MODEL": model,
            }
            apply_runtime_env(secret_settings, save_to_file=save_to_env)
            st.success(f"Setup saved{' to .env' if save_to_env else ' for this session'}. Continue to Configuration.")

    with config_tab:
        st.header("Configuration")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            zip_code = st.text_input(
                "Required zip code",
                value=settings["LEADFORGE_TARGET_ZIP"],
                placeholder="Example: 85001",
                help="Used for local lead discovery within roughly 100 miles.",
            )
        with col_b:
            max_leads = st.slider(
                "Max leads",
                min_value=MIN_LEADS,
                max_value=MAX_LEADS,
                value=as_int(settings["LEADFORGE_MAX_LEADS"], DEFAULT_MAX_LEADS, min_value=MIN_LEADS, max_value=MAX_LEADS),
                step=1,
            )
        with col_c:
            budget_cap = st.slider(
                "Budget cap (USD)",
                min_value=1.0,
                max_value=HARD_BUDGET_CEILING_USD,
                value=as_float(settings["LEADFORGE_BUDGET_USD"], RECOMMENDED_BUDGET_USD, min_value=1.0, max_value=HARD_BUDGET_CEILING_USD),
                step=0.5,
                help="Recommended: $8. Hard ceiling: $10.",
            )

        industries_text = st.text_input(
            "Target industries",
            value=settings["LEADFORGE_TARGET_INDUSTRIES"] or ", ".join(DEFAULT_INDUSTRIES),
            help="Comma-separated. Trades are prioritized in the discovery prompt.",
        )
        industries = parse_industries(industries_text)

        col_d, col_e = st.columns(2)
        with col_d:
            parallel_research = st.slider(
                "Parallel research workers",
                min_value=1,
                max_value=8,
                value=as_int(settings["LEADFORGE_PARALLEL_RESEARCH"], DEFAULT_PARALLEL_RESEARCH, min_value=1, max_value=8),
                help="5-7 is recommended. Lower this if search APIs rate limit.",
            )
        with col_e:
            sample_mode = st.toggle("Sample mode (3 leads)", value=False)

        if budget_cap <= RECOMMENDED_BUDGET_USD:
            st.success("Budget is within the recommended $8 cap.")
        else:
            st.warning("Budget is above the recommended $8 cap but still within the $10 hard ceiling.")

        st.info("Discovery will return both local leads near the zip code and high-quality remote-ready US trades leads.")

        if st.button("Save configuration"):
            blocking_errors = []
            if not valid_zip(zip_code):
                blocking_errors.append("Enter a valid 5-digit US zip code, for example 85001.")
            if not industries:
                blocking_errors.append("Enter at least one target industry.")
            if max_leads < MIN_LEADS or max_leads > MAX_LEADS:
                blocking_errors.append(f"Max leads must be between {MIN_LEADS} and {MAX_LEADS}.")
            if budget_cap < 1 or budget_cap > HARD_BUDGET_CEILING_USD:
                blocking_errors.append("Budget cap must be between $1 and the hard $10 ceiling.")
            if blocking_errors:
                for error in blocking_errors:
                    st.error(error)
            else:
                apply_runtime_env(
                    {
                        "LEADFORGE_TARGET_ZIP": zip_code,
                        "LEADFORGE_TARGET_INDUSTRIES": ", ".join(industries),
                        "LEADFORGE_MAX_LEADS": max_leads,
                        "LEADFORGE_BUDGET_USD": budget_cap,
                        "LEADFORGE_STOP_PROJECTED_USD": round(min(budget_cap * 0.9, budget_cap), 2),
                        "LEADFORGE_PARALLEL_RESEARCH": parallel_research,
                    },
                    save_to_file=save_to_env if "save_to_env" in locals() else True,
                )
                st.success("Configuration saved.")

    with run_tab:
        st.header("Run Pipeline")
        st.markdown(
            """
            <div class="leadforge-card">
            <b>Pipeline:</b> LeadDiscoveryAgent -> ResearchAgent x workers -> PitchStrategistAgent -> CSV/JSON export.<br>
            <span class="small-muted">Public data only. All outreach drafts require human review.</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        active_zip = zip_code if "zip_code" in locals() else settings["LEADFORGE_TARGET_ZIP"]
        active_industries = industries if "industries" in locals() else parse_industries(settings["LEADFORGE_TARGET_INDUSTRIES"] or ", ".join(DEFAULT_INDUSTRIES))
        active_max_leads = max_leads if "max_leads" in locals() else as_int(settings["LEADFORGE_MAX_LEADS"], DEFAULT_MAX_LEADS, min_value=MIN_LEADS, max_value=MAX_LEADS)
        active_budget = budget_cap if "budget_cap" in locals() else as_float(settings["LEADFORGE_BUDGET_USD"], RECOMMENDED_BUDGET_USD, min_value=1.0, max_value=HARD_BUDGET_CEILING_USD)
        active_parallel = parallel_research if "parallel_research" in locals() else as_int(settings["LEADFORGE_PARALLEL_RESEARCH"], DEFAULT_PARALLEL_RESEARCH, min_value=1, max_value=8)
        active_provider = provider if "provider" in locals() else (settings["LEADFORGE_LLM_PROVIDER"] or "openai")
        active_model = model if "model" in locals() else (settings["LEADFORGE_MODEL"] or "gpt-4o-mini")
        active_sample = sample_mode if "sample_mode" in locals() else False
        active_keys = {
            "OPENAI_API_KEY": (openai_input if "openai_input" in locals() else "") or settings["OPENAI_API_KEY"],
            "ANTHROPIC_API_KEY": (anthropic_input if "anthropic_input" in locals() else "") or settings["ANTHROPIC_API_KEY"],
            "XAI_API_KEY": (xai_input if "xai_input" in locals() else "") or settings["XAI_API_KEY"],
            "TAVILY_API_KEY": (tavily_input if "tavily_input" in locals() else "") or settings["TAVILY_API_KEY"],
            "BRAVE_API_KEY": (brave_input if "brave_input" in locals() else "") or settings["BRAVE_API_KEY"],
        }

        run_errors = validate_inputs(
            zip_code=active_zip,
            industries=active_industries,
            max_leads=active_max_leads,
            budget_cap=active_budget,
            parallel_research=active_parallel,
            provider=active_provider,
            keys=active_keys,
        )
        blocking_run_errors = [e for e in run_errors if "recommended $8" not in e]

        summary_cols = st.columns(5)
        summary_cols[0].metric("Zip", active_zip or "missing")
        summary_cols[1].metric("Leads", "3 sample" if active_sample else active_max_leads)
        summary_cols[2].metric("Budget", f"${active_budget:.2f}")
        summary_cols[3].metric("Workers", active_parallel)
        summary_cols[4].metric("Model", active_model)

        for error in blocking_run_errors:
            st.error(error)
        for warning in [e for e in run_errors if "recommended $8" in e]:
            st.warning(warning)

        if not blocking_run_errors:
            run_options = build_options(
                zip_code=active_zip,
                industries=active_industries,
                max_leads=active_max_leads,
                budget_cap=active_budget,
                parallel_research=active_parallel,
                provider=active_provider,
                model=active_model,
                sample_mode=active_sample,
            )
            apply_runtime_env(
                {
                    **active_keys,
                    "LEADFORGE_LLM_PROVIDER": active_provider,
                    "LEADFORGE_MODEL": active_model,
                    "LEADFORGE_TARGET_ZIP": active_zip,
                    "LEADFORGE_TARGET_INDUSTRIES": ", ".join(active_industries),
                    "LEADFORGE_MAX_LEADS": active_max_leads,
                    "LEADFORGE_BUDGET_USD": active_budget,
                    "LEADFORGE_STOP_PROJECTED_USD": round(min(active_budget * 0.9, active_budget), 2),
                    "LEADFORGE_PARALLEL_RESEARCH": active_parallel,
                },
                save_to_file=False,
            )

            from leadforge.config_loader import get_config, resolve_industries, resolve_location
            from leadforge.preflight import run_preflight

            cfg = run_options.apply_to_config(get_config())
            preflight = run_preflight(cfg)
            with st.expander("Preflight details", expanded=not preflight.ok):
                st.write(f"Location: {resolve_location(cfg)}")
                st.write(f"Industries: {', '.join(resolve_industries(cfg))}")
                st.write(f"Model: {cfg.llm.provider}/{cfg.llm.model}")
                st.write(f"Budget: ${cfg.guardrails.budget_usd:.2f} (early stop at ${cfg.guardrails.stop_projected_usd:.2f})")
                for warning in preflight.warnings:
                    st.warning(warning)
                for error in preflight.errors:
                    st.error(error)
                if preflight.ok:
                    st.success("Preflight passed. Ready to run.")

            if st.button("Start Lead Generation", type="primary", disabled=not preflight.ok, use_container_width=True):
                progress_bar = st.progress(0, text="Starting LeadForge...")
                status_box = st.status("Running LeadForge agents...", expanded=True)
                event_log: list[str] = []

                def progress_callback(phase: str, message: str, data: dict[str, Any] | None = None) -> None:
                    event_log.append(message)
                    progress_bar.progress(PHASE_PROGRESS.get(phase, 50), text=message)
                    status_box.write(message)

                try:
                    from leadforge.main import run_pipeline

                    summary = run_pipeline(run_options, progress_callback=progress_callback)
                    st.session_state["last_summary"] = summary
                    st.session_state["event_log"] = event_log
                    if summary.get("stopped_early"):
                        status_box.update(label=f"Stopped early: {summary.get('stop_reason')}", state="error")
                        st.warning(f"Stopped early: {summary.get('stop_reason')}")
                    else:
                        status_box.update(label="Lead generation complete", state="complete")
                        st.success(
                            f"Generated {summary.get('leads_pitched', 0)} pitch-ready leads "
                            f"for an estimated ${summary.get('estimated_cost_usd', 0):.4f}."
                        )
                    st.info("Open the Results tab to review and download the CSV.")
                except Exception as exc:
                    status_box.update(label="Pipeline failed", state="error")
                    st.error("Pipeline failed. Review the error below, then check API keys and search quotas.")
                    st.exception(exc)

    with results_tab:
        render_results(st.session_state.get("last_summary"))


if __name__ == "__main__":
    main()

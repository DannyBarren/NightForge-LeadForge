"""Load YAML settings and merge with environment overrides."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    root = _project_root()
    load_dotenv(root / ".env", override=False)


def load_yaml_config() -> dict[str, Any]:
    path = _project_root() / "config" / "settings.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    xai_api_key: str | None = None
    tavily_api_key: str | None = None
    brave_api_key: str | None = None
    google_sheets_credentials_json: str | None = None
    google_sheet_id: str | None = None
    google_sheet_worksheet: str = "LeadForge Output"

    leadforge_llm_provider: str | None = None
    leadforge_model: str | None = None
    leadforge_target_zip: str | None = None
    leadforge_target_location: str | None = None
    leadforge_target_industries: str | None = None
    leadforge_max_leads: int | None = None
    leadforge_budget_usd: float | None = None
    leadforge_stop_projected_usd: float | None = None
    leadforge_parallel_research: int | None = None


class GuardrailsConfig(BaseModel):
    max_leads_processed: int = 40
    discovery_target_min: int = 30
    discovery_target_max: int = 50
    per_agent_max_output_tokens: int = 6000
    max_parallel_research: int = 6
    budget_usd: float = 8.0
    stop_projected_usd: float = 7.25
    human_review_required: bool = True


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 0.25
    max_tokens: int = 4096


class AppConfig(BaseModel):
    guardrails: GuardrailsConfig
    llm: LLMConfig
    icp: dict[str, Any] = Field(default_factory=dict)
    project: dict[str, Any] = Field(default_factory=dict)
    search: dict[str, Any] = Field(default_factory=dict)
    export: dict[str, Any] = Field(default_factory=dict)
    pricing_usd_per_million: dict[str, dict[str, float]] = Field(default_factory=dict)
    env: EnvSettings = Field(default_factory=EnvSettings)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    _load_dotenv()
    raw = load_yaml_config()
    env = EnvSettings()

    guardrails = GuardrailsConfig(**raw.get("guardrails", {}))
    llm = LLMConfig(**raw.get("llm", {}))

    if env.leadforge_llm_provider:
        llm.provider = env.leadforge_llm_provider
    if env.leadforge_model:
        llm.model = env.leadforge_model
    if env.leadforge_max_leads is not None:
        guardrails.max_leads_processed = max(1, min(env.leadforge_max_leads, 40))
    if env.leadforge_budget_usd is not None:
        guardrails.budget_usd = max(0.5, min(env.leadforge_budget_usd, 10.0))
    if env.leadforge_stop_projected_usd is not None:
        guardrails.stop_projected_usd = min(
            max(env.leadforge_stop_projected_usd, 0.25),
            guardrails.budget_usd,
        )
    elif guardrails.stop_projected_usd > guardrails.budget_usd:
        guardrails.stop_projected_usd = guardrails.budget_usd
    if env.leadforge_parallel_research is not None:
        guardrails.max_parallel_research = max(1, min(8, env.leadforge_parallel_research))

    icp = raw.get("icp", {})
    if env.leadforge_target_zip:
        icp["zip_code"] = env.leadforge_target_zip.strip()
        icp["geography_override"] = (
            f"ZIP {icp['zip_code']} local market (~100 mile radius) plus remote-ready US leads"
        )
    if env.leadforge_target_location:
        if icp.get("zip_code"):
            icp["geography_override"] = (
                f"{env.leadforge_target_location} around ZIP {icp['zip_code']} "
                "plus remote-ready US leads"
            )
        else:
            icp["geography_override"] = env.leadforge_target_location
    if env.leadforge_target_industries:
        icp["industries_override"] = [
            s.strip() for s in env.leadforge_target_industries.split(",") if s.strip()
        ]

    return AppConfig(
        guardrails=guardrails,
        llm=llm,
        icp=icp,
        project=raw.get("project", {}),
        search=raw.get("search", {}),
        export=raw.get("export", {}),
        pricing_usd_per_million=raw.get("pricing_usd_per_million", {}),
        env=env,
    )


def resolve_location(cfg: AppConfig) -> str:
    zip_code = cfg.icp.get("zip_code")
    if zip_code:
        return cfg.icp.get("geography_override") or f"ZIP {zip_code}"
    return cfg.icp.get("geography_override") or cfg.icp.get(
        "geography_default", "United States"
    )


def resolve_industries(cfg: AppConfig) -> list[str]:
    return cfg.icp.get("industries_override") or cfg.icp.get("industries", [])

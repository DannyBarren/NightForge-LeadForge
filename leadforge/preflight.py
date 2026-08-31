"""Validate configuration and API keys before an expensive overnight run."""

from __future__ import annotations

import os
from dataclasses import dataclass

from leadforge.config_loader import AppConfig, _project_root

# Human-readable hints for how to obtain each provider key.
_KEY_HELP = {
    "OPENAI_API_KEY": "https://platform.openai.com/api-keys",
    "ANTHROPIC_API_KEY": "https://console.anthropic.com/settings/keys",
    "XAI_API_KEY": "https://console.x.ai",
    "TAVILY_API_KEY": "https://app.tavily.com",
    "BRAVE_API_KEY": "https://brave.com/search/api",
}


@dataclass
class PreflightResult:
    ok: bool
    errors: list[str]
    warnings: list[str]


def _has_key(*names: str) -> bool:
    return any(os.getenv(n) for n in names)


def _missing_key_message(key: str, provider: str) -> str:
    where = _KEY_HELP.get(key, "your provider dashboard")
    return (
        f"{key} is required (LLM provider = {provider}). "
        f"Fix: add `{key}=...` to your .env file (or export {key}=... in your shell), "
        f"then re-run. Get a key at {where}."
    )


def run_preflight(config: AppConfig, *, require_search: bool = True) -> PreflightResult:
    errors: list[str] = []
    warnings: list[str] = []

    provider = config.llm.provider.lower()
    provider_key = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "xai": "XAI_API_KEY",
    }.get(provider)
    if provider_key is None:
        errors.append(
            f"Unknown LLM provider '{config.llm.provider}'. "
            "Set LEADFORGE_LLM_PROVIDER to one of: openai, anthropic, xai."
        )
    elif not _has_key(provider_key):
        errors.append(_missing_key_message(provider_key, provider))

    if require_search:
        if not _has_key("TAVILY_API_KEY", "BRAVE_API_KEY"):
            warnings.append(
                "No TAVILY_API_KEY or BRAVE_API_KEY found — discovery/research will fall "
                "back to DuckDuckGo only (slower, less reliable). "
                f"For best results add TAVILY_API_KEY ({_KEY_HELP['TAVILY_API_KEY']})."
            )

    settings_path = _project_root() / "config" / "settings.yaml"
    if not settings_path.is_file():
        errors.append(
            f"Missing config file: {settings_path}. "
            "Restore config/settings.yaml from the repository."
        )

    if config.guardrails.budget_usd > 10.0:
        errors.append(
            f"LEADFORGE_BUDGET_USD={config.guardrails.budget_usd} exceeds the $10 hard "
            "ceiling. Lower it to $10.00 or less."
        )
    elif config.guardrails.budget_usd > 8.0:
        warnings.append(
            f"LEADFORGE_BUDGET_USD={config.guardrails.budget_usd} exceeds the recommended "
            "$8 cap (still under the $10 ceiling)."
        )
    if config.guardrails.stop_projected_usd > config.guardrails.budget_usd:
        errors.append(
            "LEADFORGE_STOP_PROJECTED_USD "
            f"({config.guardrails.stop_projected_usd}) cannot exceed LEADFORGE_BUDGET_USD "
            f"({config.guardrails.budget_usd}). Lower the early-stop value."
        )

    return PreflightResult(ok=not errors, errors=errors, warnings=warnings)

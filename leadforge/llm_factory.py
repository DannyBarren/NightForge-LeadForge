"""Configure cheap LLMs for CrewAI (OpenAI, Anthropic, xAI Grok)."""

from __future__ import annotations

import os

from crewai import LLM

from leadforge.config_loader import AppConfig


def build_llm(config: AppConfig, *, max_tokens: int | None = None) -> LLM:
    """Return a CrewAI LLM instance for the configured cheap model."""
    llm_cfg = config.llm
    provider = llm_cfg.provider.lower()
    model = llm_cfg.model
    out_tokens = max_tokens or min(
        llm_cfg.max_tokens,
        config.guardrails.per_agent_max_output_tokens,
    )

    if provider == "openai":
        if not config.env.openai_api_key and not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY required for openai provider")
        return LLM(
            model=model,
            temperature=llm_cfg.temperature,
            max_tokens=out_tokens,
        )

    if provider == "anthropic":
        if not config.env.anthropic_api_key and not os.getenv("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY required for anthropic provider")
        return LLM(
            model=f"anthropic/{model}",
            temperature=llm_cfg.temperature,
            max_tokens=out_tokens,
        )

    if provider == "xai":
        # Grok via OpenAI-compatible API
        api_key = config.env.xai_api_key or os.getenv("XAI_API_KEY")
        if not api_key:
            raise ValueError("XAI_API_KEY required for xai provider")
        return LLM(
            model=model,
            api_key=api_key,
            base_url="https://api.x.ai/v1",
            temperature=llm_cfg.temperature,
            max_tokens=out_tokens,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")

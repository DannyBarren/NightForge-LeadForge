"""Process-level settings for the NightForge API and graphs.

Per-run knobs (budget, lead caps, worker counts) are **not** here. Those live in
``leadforge.config_loader.AppConfig`` and are passed explicitly through graph
state, so two concurrent runs cannot read each other's limits out of a process
global. This module holds only what is genuinely per-process: where the API
binds, how it logs, and which environment it thinks it is in.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict

from leadforge.config_loader import AppConfig, get_config

# Not configurable. Every research export row is flagged for human review, and
# there is no send path for anything to bypass that flag on.
HUMAN_REVIEW_REQUIRED: Final[bool] = True


class NightForgeSettings(BaseSettings):
    """Env-driven process settings, prefixed ``NIGHTFORGE_``."""

    model_config = SettingsConfigDict(
        env_prefix="NIGHTFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"


@lru_cache(maxsize=1)
def get_settings() -> NightForgeSettings:
    return NightForgeSettings()


def leadforge_config() -> AppConfig:
    """The shared guardrail/pricing config, loaded from ``config/settings.yaml``.

    Re-exported so graph code has one import path for run configuration without
    reaching into the prototype package directly.
    """
    return get_config()

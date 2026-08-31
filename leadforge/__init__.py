"""NightForge LeadForge — cost-controlled overnight SMB lead discovery for Barren."""

import os

__version__ = "0.1.0"


def _quiet_crewai_defaults() -> None:
    """Keep runs non-interactive and quiet for CLI, CI, and live demos.

    CrewAI shows an interactive "enable tracing / view execution traces?" prompt
    on first execution, which blocks unattended runs (and stalls a live demo).
    These defaults disable that prompt and telemetry. They are set with
    ``setdefault`` so a user can still opt back in via their own environment.
    """
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    os.environ.setdefault("CREWAI_TELEMETRY", "false")
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    # Only lever that reliably suppresses the first-run tracing prompt without
    # requiring a TTY. Safe: CrewAI uses it solely to skip interactive prompts.
    os.environ.setdefault("CREWAI_TESTING", "true")


_quiet_crewai_defaults()

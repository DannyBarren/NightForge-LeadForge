"""Robust JSON extraction from LLM / CrewAI outputs.

These helpers are intentionally defensive: they never raise anything other than
``ValueError`` so callers can treat "no parseable JSON" as a normal, handled
outcome instead of a crash.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _loads(candidate: str) -> Any:
    """Parse JSON, trying a lenient repair pass before giving up."""
    try:
        return json.loads(candidate)
    except (ValueError, TypeError):
        pass
    # Opportunistic repair (json_repair ships with crewai). Never let this
    # dependency being absent or misbehaving crash the pipeline.
    try:
        from json_repair import repair_json

        repaired = repair_json(candidate, return_objects=True)
        if repaired not in (None, "", [], {}):
            return repaired
    except Exception:
        pass
    raise ValueError("Could not parse JSON payload from agent output")


def extract_json_array(text: str) -> list[dict[str, Any]]:
    """Pull first JSON array from agent output. Raises ValueError if none."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty agent output")

    m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text, re.IGNORECASE)
    if m:
        data = _loads(m.group(1))
        return data if isinstance(data, list) else [data]

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        data = _loads(text[start : end + 1])
        return data if isinstance(data, list) else [data]

    raise ValueError("No JSON array found in agent output")


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull first JSON object from agent output. Raises ValueError if none."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty agent output")

    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if m:
        data = _loads(m.group(1))
        if isinstance(data, dict):
            return data

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        data = _loads(text[start : end + 1])
        if isinstance(data, dict):
            return data

    raise ValueError("No JSON object found in agent output")

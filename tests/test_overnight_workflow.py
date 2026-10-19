"""Regression checks for the overnight workflow gate.

The previous file failed GitHub's push-time validation because a step used
``if: ${{ secrets.* }}``. That produced 15 zero-second red runs and never
started a job. See the comments at the top of the workflow file.
"""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "overnight-leads.yml"


def test_overnight_workflow_parses_as_yaml():
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert payload["name"] == "Overnight LeadForge"


def test_overnight_workflow_is_dispatch_only():
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML 1.1 treats the key `on` as boolean True.
    triggers = payload.get("on", payload.get(True))
    assert "workflow_dispatch" in triggers
    assert "push" not in triggers
    assert "schedule" not in triggers


def test_overnight_workflow_never_uses_secrets_in_if():
    """GitHub rejects `secrets` as a named-value inside `if:`."""
    for line_no, line in enumerate(WORKFLOW.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("if:"):
            assert "secrets." not in stripped, f"line {line_no}: {stripped}"


def test_overnight_workflow_gates_on_a_step_output_not_a_hard_fail():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "has_key" in text
    assert "Skipping the overnight pipeline" in text
    assert "OPENAI_API_KEY secret is required for overnight runs." not in text

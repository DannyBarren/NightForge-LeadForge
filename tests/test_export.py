"""Tests for CSV export column format."""

from leadforge.models import ConfidenceLevel, PitchOutput


def test_export_row_columns():
    pitch = PitchOutput(
        company="Acme HVAC",
        owner="Sam",
        pains="scheduling",
        barren_fit="fits",
        draft_email="Hi",
        pitch_angle="angle",
        confidence=ConfidenceLevel.HIGH,
        human_review_required=True,
        research_sources=["https://example.com"],
    )
    row = pitch.to_export_row()
    assert row["Company"] == "Acme HVAC"
    assert "Email" in row
    assert "Specific Value Props" in row
    assert row["Human Review"] == "YES"
    assert row["Confidence"] == "high"
    assert "example.com" in row["Sources"]

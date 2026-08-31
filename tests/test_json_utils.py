"""Tests for JSON extraction from LLM outputs."""

import pytest

from leadforge.json_utils import extract_json_array, extract_json_object


def test_extract_array_from_fence():
    text = 'Here:\n```json\n[{"company": "A"}]\n```'
    assert extract_json_array(text) == [{"company": "A"}]


def test_extract_object_raw():
    text = 'Output: {"company": "B", "owner": "Jane"} end'
    data = extract_json_object(text)
    assert data["company"] == "B"


def test_extract_array_empty_raises():
    with pytest.raises(ValueError):
        extract_json_array("")

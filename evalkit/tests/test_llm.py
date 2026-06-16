"""Tests for the LLM client JSON parsing (tolerant of fenced/prose wrapping)."""

import pytest
from evalkit.llm import JudgeLLMConfig, parse_json_block


def test_parse_raw_json():
    assert parse_json_block('{"grounding": 4, "faithfulness": 3}') == {
        "grounding": 4,
        "faithfulness": 3,
    }


def test_parse_fenced_json_with_lang():
    text = '```json\n{"grounding": 4}\n```'
    assert parse_json_block(text) == {"grounding": 4}


def test_parse_fenced_json_without_lang():
    text = '```\n{"winner": "Tie"}\n```'
    assert parse_json_block(text) == {"winner": "Tie"}


def test_parse_json_wrapped_in_prose():
    text = 'Here is my judgment:\n```json\n{"grounding": 5}\n```\nThanks.'
    assert parse_json_block(text) == {"grounding": 5}


def test_parse_multiline_json_object():
    text = '```\n{\n  "grounding": 4,\n  "faithfulness": 3\n}\n```'
    assert parse_json_block(text) == {"grounding": 4, "faithfulness": 3}


def test_parse_raises_on_malformed():
    with pytest.raises(ValueError, match="could not parse"):
        parse_json_block("not json at all")


def test_judge_config_reads_judge_llm_model_env(monkeypatch):
    monkeypatch.setenv("JUDGE_LLM_MODEL", "some/other-model")
    cfg = JudgeLLMConfig()
    assert cfg.model == "some/other-model"


def test_judge_config_reads_judge_llm_base_url_env(monkeypatch):
    monkeypatch.setenv("JUDGE_LLM_BASE_URL", "https://example.test/v1")
    cfg = JudgeLLMConfig()
    assert cfg.base_url == "https://example.test/v1"

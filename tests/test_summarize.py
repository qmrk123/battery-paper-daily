"""Tests for the summarize stage — no network; a fake client stands in for Anthropic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

import pytest

from pipeline.models import Paper
from pipeline.summarize import (
    build_user_content, parse_tool_result, summarize_papers,
    parse_cli_result, _extract_json, ClaudeAuthError, parse_gemini_result,
)

LABELS = {"li-metal": "리튬 금속 음극", "high-ni-ncm": "High-Ni NCM 양극"}


def test_build_user_content_includes_fields():
    p = Paper(id="W1", source="openalex", title="Ni-rich cathode study",
              url="u", published="2026-08-14", topics=["high-ni-ncm"],
              venue="J. Power Sources", abstract_en="We show ...")
    txt = build_user_content(p, LABELS)
    assert "High-Ni NCM 양극" in txt
    assert "Ni-rich cathode study" in txt
    assert "J. Power Sources" in txt
    assert "We show ..." in txt


def test_build_user_content_no_abstract():
    p = Paper(id="W1", source="openalex", title="T", url="u",
              published="2026-08-14", topics=["li-metal"])
    assert "(초록 없음)" in build_user_content(p, LABELS)


class _Block:
    def __init__(self, data):
        self.type = "tool_use"
        self.input = data


def test_parse_tool_result_topics_and_summary():
    obj = [_Block({"topics": ["li-metal"], "summary_ko": "요약입니다."})]
    assert parse_tool_result(obj) == (["li-metal"], "요약입니다.")
    dct = [{"type": "tool_use", "input": {"topics": [], "summary_ko": "무관."}}]
    assert parse_tool_result(dct) == ([], "무관.")
    # bogus topic ids are filtered out
    bogus = [_Block({"topics": ["li-metal", "not-a-topic"], "summary_ko": "s"})]
    assert parse_tool_result(bogus) == (["li-metal"], "s")
    assert parse_tool_result([{"type": "text", "text": "hi"}]) is None


class _Msgs:
    def __init__(self, fn): self.create = fn


class FakeClient:
    """Returns canned topics: a 'magnet' title classifies to [] (off-topic)."""
    def __init__(self):
        self.messages = _Msgs(self._create)
        self.calls = 0

    def _create(self, **kw):
        self.calls += 1
        user = kw["messages"][0]["content"]
        off_topic = "magnet" in user.lower()
        return type("R", (), {"content": [
            _Block({"topics": [] if off_topic else ["high-ni-ncm"],
                    "summary_ko": "무관 논문." if off_topic else "핵심 요약 2문장."})
        ]})()


def test_summarize_papers_classifies_and_gates():
    papers = [
        Paper(id="W1", source="openalex", title="Ni-rich cathode", url="u",
              published="2026-08-14", topics=["li-metal"], abstract_en="a"),  # mis-tagged
        Paper(id="W2", source="arxiv", title="Antiperovskite magnet", url="u",
              published="2026-08-14", topics=["li-metal"], abstract_en="b"),
    ]
    client = FakeClient()
    stats = summarize_papers(papers, LABELS, client=client, workers=2)
    assert stats["processed"] == 2 and stats["irrelevant"] == 1 and stats["errors"] == 0
    assert papers[0].topics == ["high-ni-ncm"]      # reclassified by the LLM
    assert papers[0].relevant is True and papers[0].summary_ko
    assert papers[1].topics == [] and papers[1].relevant is False


def _envelope(result_text, is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "result": result_text})


def test_parse_cli_result_plain_and_fenced():
    ok = _envelope('{"topics": ["li-metal"], "summary_ko": "핵심 요약."}')
    assert parse_cli_result(ok) == (["li-metal"], "핵심 요약.")
    fenced = _envelope('```json\n{"topics": [], "summary_ko": "무관."}\n```')
    assert parse_cli_result(fenced) == ([], "무관.")
    chatty = _envelope('결과: {"topics": ["li-rich"], "summary_ko": "요약"} 끝.')
    assert parse_cli_result(chatty) == (["li-rich"], "요약")


def test_parse_cli_result_not_logged_in_raises():
    with pytest.raises(ClaudeAuthError):
        parse_cli_result(_envelope("Not logged in · Please run /login", is_error=True))


def test_parse_cli_result_generic_error_returns_none():
    assert parse_cli_result(_envelope("some other failure", is_error=True)) is None


def test_parse_gemini_result():
    ok = {"candidates": [{"content": {"parts": [
        {"text": '{"topics": ["high-ni-ncm"], "summary_ko": "핵심 요약."}'}]}}]}
    assert parse_gemini_result(ok) == (["high-ni-ncm"], "핵심 요약.")
    fenced = {"candidates": [{"content": {"parts": [
        {"text": '```json\n{"topics": [], "summary_ko": "무관."}\n```'}]}}]}
    assert parse_gemini_result(fenced) == ([], "무관.")
    assert parse_gemini_result({"candidates": []}) is None
    assert parse_gemini_result({}) is None


def test_extract_json_variants():
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert _extract_json("no json here") is None


def test_summarize_skips_already_done_unless_force():
    p = Paper(id="W1", source="openalex", title="X", url="u",
              published="2026-08-14", topics=["li-metal"],
              abstract_en="a", summary_ko="이미 있음")
    client = FakeClient()
    summarize_papers([p], LABELS, client=client)
    assert client.calls == 0                       # skipped
    summarize_papers([p], LABELS, client=client, force=True)
    assert client.calls == 1                       # forced redo

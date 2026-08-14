"""Tests for the summarize stage — no network; a fake client stands in for Anthropic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.models import Paper
from pipeline.summarize import (
    build_user_content, parse_tool_result, summarize_papers,
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


def test_parse_tool_result_object_and_dict():
    obj = [_Block({"relevant": True, "summary_ko": "요약입니다."})]
    assert parse_tool_result(obj) == (True, "요약입니다.")
    dct = [{"type": "tool_use", "input": {"relevant": False, "summary_ko": "무관."}}]
    assert parse_tool_result(dct) == (False, "무관.")
    # text-only response (model didn't call the tool) -> None
    assert parse_tool_result([{"type": "text", "text": "hi"}]) is None


class _Msgs:
    def __init__(self, fn): self.create = fn


class FakeClient:
    """Returns a canned tool_use based on whether the title says 'magnet'."""
    def __init__(self):
        self.messages = _Msgs(self._create)
        self.calls = 0

    def _create(self, **kw):
        self.calls += 1
        user = kw["messages"][0]["content"]
        off_topic = "magnet" in user.lower()
        return type("R", (), {"content": [
            _Block({"relevant": not off_topic,
                    "summary_ko": "무관 논문." if off_topic else "핵심 요약 2문장."})
        ]})()


def test_summarize_papers_sets_fields_and_gate():
    papers = [
        Paper(id="W1", source="openalex", title="Ni-rich cathode", url="u",
              published="2026-08-14", topics=["high-ni-ncm"], abstract_en="a"),
        Paper(id="W2", source="arxiv", title="Antiperovskite magnet", url="u",
              published="2026-08-14", topics=["li-metal"], abstract_en="b"),
    ]
    client = FakeClient()
    stats = summarize_papers(papers, LABELS, client=client, workers=2)
    assert stats["processed"] == 2 and stats["irrelevant"] == 1 and stats["errors"] == 0
    assert papers[0].relevant is True and papers[0].summary_ko
    assert papers[1].relevant is False


def test_summarize_skips_already_done_unless_force():
    p = Paper(id="W1", source="openalex", title="X", url="u",
              published="2026-08-14", topics=["li-metal"],
              abstract_en="a", summary_ko="이미 있음")
    client = FakeClient()
    summarize_papers([p], LABELS, client=client)
    assert client.calls == 0                       # skipped
    summarize_papers([p], LABELS, client=client, force=True)
    assert client.calls == 1                       # forced redo

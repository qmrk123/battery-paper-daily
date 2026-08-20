"""Load and represent topic configuration (config/topics.yaml)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "topics.yaml"


@dataclass
class Topic:
    id: str
    label_ko: str
    label_en: str
    emoji: str
    queries: list[str]
    arxiv: Optional[str]
    include: list[re.Pattern] = field(default_factory=list)
    exclude: list[re.Pattern] = field(default_factory=list)
    context: list[re.Pattern] = field(default_factory=list)

    def matches(self, text: str) -> bool:
        """Stage-2 precision filter: (>=1 include) AND (0 exclude) AND
        (>=1 context, if a context gate is configured).

        Empty include list means 'accept anything the search returned'.
        """
        if self.exclude and any(p.search(text) for p in self.exclude):
            return False
        if self.include and not any(p.search(text) for p in self.include):
            return False
        if self.context and not any(p.search(text) for p in self.context):
            return False
        return True


@dataclass
class Config:
    window_days: int
    max_per_topic: int
    types: list[str]
    topics: list[Topic]
    min_journal_metric: float = 0.0   # OpenAlex 2yr_mean_citedness gate (0 = off)
    include_preprints: bool = True     # keep arXiv preprints (no journal metric)
    allow_journals: list[str] = field(default_factory=list)  # if set, keep ONLY these journals
    context: list[re.Pattern] = field(default_factory=list)  # shared battery/electrochem gate
    journal_gate: list[re.Pattern] = field(default_factory=list)  # tighter gate for journal-first

    def topic(self, tid: str) -> Topic:
        for t in self.topics:
            if t.id == tid:
                return t
        raise KeyError(tid)

    def topic_meta(self) -> list[dict]:
        return [
            {"id": t.id, "label_ko": t.label_ko,
             "label_en": t.label_en, "emoji": t.emoji}
            for t in self.topics
        ]


def _compile(patterns: Optional[list[str]]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in (patterns or [])]


def load_config(path: Path = CONFIG_PATH) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    d = raw.get("defaults", {})
    default_context = d.get("context")            # shared gate unless overridden
    topics = [
        Topic(
            id=t["id"],
            label_ko=t["label_ko"],
            label_en=t["label_en"],
            emoji=t.get("emoji", "📄"),
            queries=t.get("queries", []),
            arxiv=t.get("arxiv"),
            include=_compile(t.get("include")),
            exclude=_compile(t.get("exclude")),
            context=_compile(t.get("context", default_context)),
        )
        for t in raw.get("topics", [])
    ]
    return Config(
        window_days=int(d.get("window_days", 7)),
        max_per_topic=int(d.get("max_per_topic", 200)),
        types=list(d.get("types", ["article", "review", "preprint"])),
        topics=topics,
        min_journal_metric=float(d.get("min_journal_metric", 0.0)),
        include_preprints=bool(d.get("include_preprints", True)),
        allow_journals=list(d.get("allow_journals", []) or []),
        context=_compile(default_context),
        journal_gate=_compile(d.get("journal_gate") or default_context),
    )

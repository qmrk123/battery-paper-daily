"""Phase 4 — Korean summary + LLM relevance gate (Anthropic Claude).

One Haiku 4.5 call per paper does BOTH jobs (cheaper, and the gate removes the
keyword-only false positives the regex stage can't):
  * relevant  -> is this genuinely about one of its battery-electrode topics?
  * summary_ko -> 2-4 sentence Korean summary of the key contribution.

Structured output is forced via a tool schema, so parsing never guesses.
Needs ANTHROPIC_API_KEY in the environment. Run:

    python -m pipeline.summarize --date 2026-08-14          # fill missing summaries
    python -m pipeline.summarize --date 2026-08-14 --force   # redo all
"""
from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from .config import load_config
from .models import Paper
from .store import Store

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = 600
WORKERS = int(os.environ.get("SUMMARIZE_WORKERS", "6"))

SYSTEM = (
    "당신은 이차전지(배터리) 소재 연구자를 돕는 조수입니다. 주어진 논문의 제목과 초록, "
    "그리고 배정된 주제를 보고 두 가지를 판단합니다.\n"
    "1) 관련성(relevant): 이 논문이 배정된 전극 소재 주제(리튬/소듐 금속 음극, "
    "High-Ni NCM 양극, Li-rich 양극 등 이차전지 전극 소재)에 실제로 해당하면 true. "
    "키워드만 우연히 겹치는 물리·촉매·무관 분야 논문이면 false.\n"
    "2) 한글 요약(summary_ko): 핵심 기여·발견·방법을 2~4문장으로 요약. 전문 용어는 "
    "유지하되 간결하게. 광고·과장 표현 금지, 초록에 없는 내용 추측 금지. 초록이 없으면 "
    "제목에 근거해 1~2문장으로 보수적으로 작성하고 문장 끝에 '(제목 기반 추정)'을 덧붙입니다.\n"
    "반드시 record 도구로만, 요약은 한국어로 답하세요."
)

TOOL = {
    "name": "record",
    "description": "주제 관련성 판정과 한글 요약을 기록",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant": {
                "type": "boolean",
                "description": "배정된 배터리 전극 소재 주제에 실제로 해당하면 true",
            },
            "summary_ko": {
                "type": "string",
                "description": "핵심 기여/발견을 담은 2~4문장 한글 요약",
            },
        },
        "required": ["relevant", "summary_ko"],
    },
}


def build_user_content(paper: Paper, topic_labels: dict[str, str]) -> str:
    topics_ko = ", ".join(topic_labels.get(t, t) for t in (paper.topics or [])) or "미지정"
    abstract = paper.abstract_en or "(초록 없음)"
    return (
        f"배정 주제: {topics_ko}\n"
        f"제목: {paper.title}\n"
        f"저널: {paper.venue or '미상'}\n"
        f"초록: {abstract}"
    )


def parse_tool_result(content) -> Optional[tuple[bool, str]]:
    """Extract (relevant, summary_ko) from a Messages API response's content."""
    for block in content:
        btype = getattr(block, "type", None) or (isinstance(block, dict) and block.get("type"))
        if btype == "tool_use":
            data = getattr(block, "input", None)
            if data is None and isinstance(block, dict):
                data = block.get("input")
            if isinstance(data, dict) and "summary_ko" in data:
                return bool(data.get("relevant", True)), str(data.get("summary_ko", "")).strip()
    return None


def summarize_one(client, paper: Paper, topic_labels: dict[str, str],
                  model: str = MODEL, retries: int = 3) -> Optional[tuple[bool, str]]:
    backoff = 2.0
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM,
                tools=[TOOL],
                tool_choice={"type": "tool", "name": "record"},
                messages=[{"role": "user", "content": build_user_content(paper, topic_labels)}],
            )
            return parse_tool_result(resp.content)
        except Exception as e:
            if attempt == retries - 1:
                raise
            # transient (rate limit / overloaded / network): back off and retry
            time.sleep(backoff)
            backoff *= 2
    return None


def summarize_papers(papers: list[Paper], topic_labels: dict[str, str],
                     client=None, model: str = MODEL, force: bool = False,
                     workers: int = WORKERS, log=print) -> dict:
    """Fill summary_ko + relevant for papers that need it. Returns stats."""
    if client is None:
        from anthropic import Anthropic          # imported lazily so tests don't need it
        client = Anthropic()

    todo = [p for p in papers if force or not p.summary_ko]
    stats = {"total": len(papers), "processed": 0, "irrelevant": 0, "errors": 0}
    if not todo:
        log("  nothing to summarize")
        return stats

    def work(p: Paper):
        return p, summarize_one(client, p, topic_labels, model=model)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(work, p) for p in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                p, result = fut.result()
            except Exception as e:
                stats["errors"] += 1
                log(f"  [{i}/{len(todo)}] error: {e}")
                continue
            if not result:
                stats["errors"] += 1
                log(f"  [{i}/{len(todo)}] no tool result: {p.title[:50]}")
                continue
            relevant, summary = result
            p.relevant = relevant
            p.summary_ko = summary
            stats["processed"] += 1
            if not relevant:
                stats["irrelevant"] += 1
            if i % 10 == 0 or i == len(todo):
                log(f"  [{i}/{len(todo)}] done ({stats['irrelevant']} flagged off-topic)")
    return stats


def run(date: str, force: bool = False, log=print) -> dict:
    cfg = load_config()
    labels = {t.id: t.label_ko for t in cfg.topics}
    store = Store()
    papers = store.load_day(date)
    if not papers:
        log(f"no data for {date}")
        return {}
    log(f"== summarize {date}: {len(papers)} papers (model={MODEL}) ==")
    stats = summarize_papers(papers, labels, force=force, log=log)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.save_day(date, papers, generated_at=now)
    store.write_index(cfg.topic_meta(), updated_at=now)
    log(f"== processed={stats['processed']} off-topic={stats['irrelevant']} "
        f"errors={stats['errors']} -> saved data/{date}.json ==")
    return stats


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Korean summaries + relevance gate")
    ap.add_argument("--date", default=_today(), help="day file to summarize (default: today UTC)")
    ap.add_argument("--force", action="store_true", help="redo papers that already have a summary")
    args = ap.parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set."); return 2
    run(args.date, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase 4 — Korean summary + LLM relevance gate.

One model call per paper does BOTH jobs (cheaper, and the gate removes the
keyword-only false positives the regex stage can't):
  * relevant  -> is this genuinely about one of its battery-electrode topics?
  * summary_ko -> 2-4 sentence Korean summary of the key contribution.

Two backends, auto-selected:
  * SDK  — Anthropic API (metered). Used when ANTHROPIC_API_KEY is set.
  * CLI  — the Claude Code `claude` binary in headless print mode, billed against
           your Claude subscription (no API credits). Used otherwise. Requires a
           one-time `claude setup-token` and CLAUDE_CODE_OAUTH_TOKEN in the env.

    python -m pipeline.summarize --date 2026-08-14          # fill missing summaries
    python -m pipeline.summarize --date 2026-08-14 --force   # redo all
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from glob import glob
from pathlib import Path
from typing import Callable, Optional

from .config import load_config
from .models import Paper
from .store import Store

SDK_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
CLI_MODEL = os.environ.get("CLAUDE_MODEL", "haiku")
MAX_TOKENS = 600

SYSTEM = (
    "당신은 이차전지(배터리) 소재 연구자를 돕는 조수입니다. 주어진 논문의 제목과 초록, "
    "그리고 배정된 주제를 보고 두 가지를 판단합니다.\n"
    "1) 관련성(relevant): 이 논문이 배정된 전극 소재 주제(리튬/소듐 금속 음극, "
    "High-Ni NCM 양극, Li-rich 양극 등 이차전지 전극 소재)에 실제로 해당하면 true. "
    "키워드만 우연히 겹치는 물리·촉매·무관 분야 논문이면 false.\n"
    "2) 한글 요약(summary_ko): 핵심 기여·발견·방법을 2~4문장으로 요약. 전문 용어는 "
    "유지하되 간결하게. 광고·과장 표현 금지, 초록에 없는 내용 추측 금지. 초록이 없으면 "
    "제목에 근거해 1~2문장으로 보수적으로 작성하고 문장 끝에 '(제목 기반 추정)'을 덧붙입니다."
)
CLI_JSON_SUFFIX = (
    '\n\n반드시 JSON 객체 하나만 출력하세요. 마크다운 코드블록이나 설명 없이 순수 JSON만:\n'
    '{"relevant": true 또는 false, "summary_ko": "한글 요약"}'
)

# SDK: force structured output via a tool
TOOL = {
    "name": "record",
    "description": "주제 관련성 판정과 한글 요약을 기록",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant": {"type": "boolean",
                         "description": "배정된 배터리 전극 소재 주제에 실제로 해당하면 true"},
            "summary_ko": {"type": "string",
                           "description": "핵심 기여/발견을 담은 2~4문장 한글 요약"},
        },
        "required": ["relevant", "summary_ko"],
    },
}


class ClaudeAuthError(RuntimeError):
    """The Claude CLI is not authenticated (run `claude setup-token`)."""


# ---------------------------------------------------------------- shared

def build_user_content(paper: Paper, topic_labels: dict[str, str]) -> str:
    topics_ko = ", ".join(topic_labels.get(t, t) for t in (paper.topics or [])) or "미지정"
    abstract = paper.abstract_en or "(초록 없음)"
    return (
        f"배정 주제: {topics_ko}\n"
        f"제목: {paper.title}\n"
        f"저널: {paper.venue or '미상'}\n"
        f"초록: {abstract}"
    )


def _coerce(data: dict) -> Optional[tuple[bool, str]]:
    if isinstance(data, dict) and "summary_ko" in data:
        return bool(data.get("relevant", True)), str(data.get("summary_ko", "")).strip()
    return None


# ---------------------------------------------------------------- SDK backend

def parse_tool_result(content) -> Optional[tuple[bool, str]]:
    """Extract (relevant, summary_ko) from a Messages API response's content."""
    for block in content:
        btype = getattr(block, "type", None) or (isinstance(block, dict) and block.get("type"))
        if btype == "tool_use":
            data = getattr(block, "input", None)
            if data is None and isinstance(block, dict):
                data = block.get("input")
            if isinstance(data, dict):
                return _coerce(data)
    return None


def summarize_one_sdk(client, paper: Paper, topic_labels: dict[str, str],
                      model: str = SDK_MODEL, retries: int = 3) -> Optional[tuple[bool, str]]:
    backoff = 2.0
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=model, max_tokens=MAX_TOKENS, system=SYSTEM,
                tools=[TOOL], tool_choice={"type": "tool", "name": "record"},
                messages=[{"role": "user", "content": build_user_content(paper, topic_labels)}],
            )
            return parse_tool_result(resp.content)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(backoff); backoff *= 2
    return None


# ---------------------------------------------------------------- CLI backend

def resolve_claude_cli() -> Optional[str]:
    """Find the `claude` binary: $CLAUDE_CLI, then the bundled desktop-app copy,
    then PATH."""
    override = os.environ.get("CLAUDE_CLI")
    if override and Path(override).exists():
        return override
    cands: list[str] = []
    for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += glob(str(Path(base) / "Claude" / "claude-code" / "*" / "claude.exe"))
    if cands:
        def ver(p: str):
            m = re.search(r"claude-code[\\/]([0-9.]+)", p)
            return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
        return sorted(cands, key=ver)[-1]
    return shutil.which("claude")


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1 or j < i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except Exception:
        return None


def parse_cli_result(stdout: str) -> Optional[tuple[bool, str]]:
    """Parse the `claude -p --output-format json` envelope. Raises ClaudeAuthError
    if the CLI reports it is not logged in."""
    env = json.loads(stdout)                       # may raise on garbage
    result = env.get("result", "") if isinstance(env, dict) else ""
    if isinstance(result, str) and "not logged in" in result.lower():
        raise ClaudeAuthError(result.strip())
    if isinstance(env, dict) and env.get("is_error"):
        return None
    data = _extract_json(result) if isinstance(result, str) else None
    return _coerce(data) if data else None


def summarize_one_cli(cli: str, paper: Paper, topic_labels: dict[str, str],
                      model: str = CLI_MODEL, retries: int = 3,
                      timeout: int = 120) -> Optional[tuple[bool, str]]:
    args = [cli, "-p", "--output-format", "json", "--model", model,
            "--append-system-prompt", SYSTEM + CLI_JSON_SUFFIX]
    content = build_user_content(paper, topic_labels)
    backoff = 3.0
    for attempt in range(retries):
        try:
            proc = subprocess.run(args, input=content, capture_output=True,
                                  text=True, encoding="utf-8", timeout=timeout)
        except subprocess.TimeoutExpired:
            if attempt == retries - 1:
                return None
            continue
        out = (proc.stdout or "").strip()
        if out:
            return parse_cli_result(out)           # raises ClaudeAuthError
        if attempt == retries - 1:
            raise RuntimeError((proc.stderr or "claude CLI produced no output")[:300])
        time.sleep(backoff); backoff *= 2
    return None


# ---------------------------------------------------------------- orchestration

def _make_summarize_fn(labels, client, backend, log) -> Optional[tuple[Callable, int]]:
    """Return (fn, default_workers) or None if no backend is available."""
    if client is not None:
        return (lambda p: summarize_one_sdk(client, p, labels)), 6
    if backend == "sdk" or (backend is None and os.environ.get("ANTHROPIC_API_KEY")):
        from anthropic import Anthropic
        c = Anthropic()
        log(f"  backend: Anthropic API (metered), model={SDK_MODEL}")
        return (lambda p: summarize_one_sdk(c, p, labels)), 6
    cli = resolve_claude_cli()
    if cli:
        tok = "CLAUDE_CODE_OAUTH_TOKEN" in os.environ
        log(f"  backend: Claude subscription CLI, model={CLI_MODEL} "
            f"(token env: {'set' if tok else 'MISSING — run `claude setup-token`'})")
        return (lambda p: summarize_one_cli(cli, p, labels)), 3
    return None


def summarize_papers(papers: list[Paper], topic_labels: dict[str, str],
                     client=None, backend: Optional[str] = None, force: bool = False,
                     workers: Optional[int] = None, log=print) -> dict:
    """Fill summary_ko + relevant for papers that need it. Returns stats.
    `client` (SDK-style) is honored for tests; otherwise a backend is auto-picked."""
    made = _make_summarize_fn(topic_labels, client, backend, log)
    if made is None:
        raise RuntimeError(
            "No summarizer available. Set ANTHROPIC_API_KEY (API), or run "
            "`claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN (subscription).")
    fn, default_workers = made
    workers = workers or default_workers

    todo = [p for p in papers if force or not p.summary_ko]
    stats = {"total": len(papers), "processed": 0, "irrelevant": 0, "errors": 0}
    if not todo:
        log("  nothing to summarize")
        return stats

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fn, p): p for p in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            p = futures[fut]
            try:
                result = fut.result()
            except ClaudeAuthError as e:
                raise RuntimeError(
                    f"Claude CLI not logged in ({e}). Run `claude setup-token` and "
                    f"set CLAUDE_CODE_OAUTH_TOKEN, then retry.") from e
            except Exception as e:
                stats["errors"] += 1
                log(f"  [{i}/{len(todo)}] error: {str(e)[:120]}")
                continue
            if not result:
                stats["errors"] += 1
                log(f"  [{i}/{len(todo)}] no result: {p.title[:50]}")
                continue
            relevant, summary = result
            p.relevant, p.summary_ko = relevant, summary
            stats["processed"] += 1
            stats["irrelevant"] += int(not relevant)
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
    log(f"== summarize {date}: {len(papers)} papers ==")
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
    ap.add_argument("--setup-token", action="store_true",
                    help="run `claude setup-token` (one-time subscription auth) and exit")
    args = ap.parse_args(argv)
    if args.setup_token:
        cli = resolve_claude_cli()
        if not cli:
            print("claude CLI not found. Set CLAUDE_CLI to its path."); return 2
        print(f"launching: {cli} setup-token")
        print("Authorize in the browser, then copy the token and set it, e.g.:")
        print('  PowerShell:  $env:CLAUDE_CODE_OAUTH_TOKEN = "<token>"')
        return subprocess.call([cli, "setup-token"])
    try:
        run(args.date, force=args.force)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

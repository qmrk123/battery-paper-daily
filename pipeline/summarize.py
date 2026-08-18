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
import threading
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

TOPIC_IDS = ["li-metal", "na-metal", "high-ni-ncm", "li-rich"]

SYSTEM = (
    "당신은 이차전지(배터리) 소재 연구자를 돕는 조수입니다. 주어진 논문의 제목·초록과 "
    "'배정 후보 주제'를 보고 두 가지를 출력합니다.\n"
    "1) 분류(topics): 이 논문이 실제로 다루는 주제를 아래 4가지 중 모두 고르세요(복수 가능). "
    "배정 후보 주제는 참고만 하고, 반드시 실제 내용 기준으로 정확히 분류하세요. 4가지 중 "
    "어디에도 해당하지 않으면 빈 배열([]).\n"
    "   - li-metal: 리튬 금속 '음극'(anode) — 리튬 금속 전지, 덴드라이트, anode-free 리튬, "
    "Li 도금/벗김, Li 음극 계면·집전체·SEI\n"
    "   - na-metal: 소듐 금속 '음극' — 소듐 금속 전지, Na anode-free, Na 덴드라이트/SEI\n"
    "   - high-ni-ncm: High-Ni NCM/NMC '양극'(cathode) — Ni-rich 층상 산화물 양극(NCM/NMC811 등)\n"
    "   - li-rich: Li-rich/Li-excess '양극' — 리튬 과잉 층상/무질서암염(DRX) 양극, 음이온(산소) 산화환원\n"
    "   ★ 음극(anode) 논문을 양극(cathode) 주제로, 또는 그 반대로 분류하지 마세요. "
    "예: 'anode-free lithium'은 li-metal(음극)이지 high-ni-ncm(양극)이 아님. "
    "Li-S(황 양극)·Li-O2(산소 양극)·일반 LiCoO2·순수 물리/촉매 논문은 4가지 중 없음([]).\n"
    "2) 한글 요약(summary_ko): 핵심 기여·발견·방법을 2~4문장으로. 전문 용어는 유지하되 간결하게. "
    "광고·과장 금지, 초록에 없는 내용 추측 금지. 초록이 없으면 제목에 근거해 1~2문장으로 "
    "보수적으로 쓰고 문장 끝에 '(제목 기반 추정)'을 덧붙입니다."
)
CLI_JSON_SUFFIX = (
    '\n\n반드시 JSON 객체 하나만 출력하세요. 마크다운/설명 없이 순수 JSON만:\n'
    '{"topics": ["li-metal" 등 해당 주제 배열, 없으면 []], "summary_ko": "한글 요약"}'
)

# SDK: force structured output via a tool
TOOL = {
    "name": "record",
    "description": "주제 분류와 한글 요약을 기록",
    "input_schema": {
        "type": "object",
        "properties": {
            "topics": {"type": "array",
                       "items": {"type": "string", "enum": TOPIC_IDS},
                       "description": "실제로 해당하는 주제(복수 가능, 없으면 빈 배열)"},
            "summary_ko": {"type": "string",
                           "description": "핵심 기여/발견을 담은 2~4문장 한글 요약"},
        },
        "required": ["topics", "summary_ko"],
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


def _coerce(data: dict) -> Optional[tuple[list[str], str]]:
    """Return (topics, summary_ko). topics filtered to the valid ids."""
    if isinstance(data, dict) and "summary_ko" in data:
        topics = [t for t in (data.get("topics") or []) if t in TOPIC_IDS]
        return topics, str(data.get("summary_ko", "")).strip()
    return None


# ---------------------------------------------------------------- SDK backend

def parse_tool_result(content) -> Optional[tuple[list[str], str]]:
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
                      model: str = SDK_MODEL, retries: int = 3) -> Optional[tuple[list[str], str]]:
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

def claude_cli_globs() -> list[str]:
    """Candidate glob patterns for the bundled `claude` binary (Windows desktop
    app), covering both the plain Roaming copy and the MSIX-packaged copy."""
    pats: list[str] = []
    appdata = os.environ.get("APPDATA")
    local = os.environ.get("LOCALAPPDATA")
    for base in (appdata, local):
        if base:
            pats.append(str(Path(base) / "Claude" / "claude-code" / "*" / "claude.exe"))
    if local:  # e.g. ...\Packages\Claude_xxxx\LocalCache\Roaming\Claude\claude-code\*\claude.exe
        pats.append(str(Path(local) / "Packages" / "Claude_*" / "LocalCache" /
                        "Roaming" / "Claude" / "claude-code" / "*" / "claude.exe"))
    # POSIX/npm global installs
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if home:
        pats.append(str(Path(home) / ".claude" / "*" / "claude"))
    return pats


def resolve_claude_cli() -> Optional[str]:
    """Find the `claude` binary: $CLAUDE_CLI, then PATH, then the bundled
    desktop-app copies (newest version)."""
    override = os.environ.get("CLAUDE_CLI")
    if override and Path(override).exists():
        return override
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    cands: list[str] = []
    for pat in claude_cli_globs():
        cands += glob(pat)
    if cands:
        def ver(p: str):
            m = re.search(r"claude-code[\\/]([0-9.]+)", p)
            return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
        return sorted(cands, key=ver)[-1]
    return None


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


def parse_cli_result(stdout: str) -> Optional[tuple[list[str], str]]:
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
                      timeout: int = 120) -> Optional[tuple[list[str], str]]:
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


# ---------------------------------------------------------------- Gemini backend

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/"
                   "models/{model}:generateContent")
_GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "topics": {"type": "ARRAY", "items": {"type": "STRING", "enum": TOPIC_IDS}},
        "summary_ko": {"type": "STRING"},
    },
    "required": ["topics", "summary_ko"],
}


def parse_gemini_result(data: dict) -> Optional[tuple[list[str], str]]:
    """Pull (relevant, summary_ko) from a Gemini generateContent response."""
    try:
        cands = data.get("candidates") or []
        parts = ((cands[0] or {}).get("content") or {}).get("parts") or []
        text = parts[0].get("text", "")
    except Exception:
        return None
    obj = _extract_json(text) if text else None
    return _coerce(obj) if obj else None


def pick_gemini_model(api_key: str) -> str:
    """Query ListModels and choose a current Flash text model — robust to Google
    renaming/deprecating versioned ids (e.g. gemini-2.5-flash going 404)."""
    import requests
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": api_key}, timeout=30)
        r.raise_for_status()
        names = [m["name"].split("/")[-1]
                 for m in (r.json().get("models") or [])
                 if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    except Exception:
        return "gemini-flash-latest"
    bad = ("vision", "thinking", "image", "audio", "tts", "live", "embedding")
    flash = [n for n in names if "flash" in n.lower()
             and not any(b in n.lower() for b in bad)]
    if not flash:
        return names[0] if names else "gemini-flash-latest"

    def score(n: str) -> tuple:
        nl = n.lower()
        m = re.search(r"gemini-(\d+)\.?(\d*)-flash", nl)
        ver = int(m.group(1)) * 100 + (int(m.group(2) or 0)) if m else 0
        return (
            nl == "gemini-flash-latest",              # stable always-current alias
            "lite" not in nl,                          # prefer full over lite
            not any(x in nl for x in ("preview", "exp")),
            ver,
        )
    return sorted(flash, key=score)[-1]


# Free-tier pacing: reserve one request slot per interval across all threads
# (Gemini free tier is ~10 RPM). Sleep happens outside the lock so HTTP calls
# still overlap; only their start times are spaced.
_GEM_MIN_INTERVAL = float(os.environ.get("GEMINI_MIN_INTERVAL", "10.0"))
_gem_lock = threading.Lock()
_gem_next = 0.0


def _gem_throttle() -> None:
    global _gem_next
    with _gem_lock:
        now = time.monotonic()
        wait = max(0.0, _gem_next - now)
        _gem_next = max(now, _gem_next) + _GEM_MIN_INTERVAL
    if wait:
        time.sleep(wait)


def summarize_one_gemini(api_key: str, paper: Paper, topic_labels: dict[str, str],
                         model: str = GEMINI_MODEL, retries: int = 4,
                         timeout: int = 60) -> Optional[tuple[list[str], str]]:
    import requests
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": build_user_content(paper, topic_labels)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _GEMINI_SCHEMA,
            "temperature": 0.3,
            "maxOutputTokens": 800,
        },
    }
    url = GEMINI_ENDPOINT.format(model=model)
    backoff = 5.0
    for attempt in range(retries):
        _gem_throttle()
        r = requests.post(url, params={"key": api_key}, json=body, timeout=timeout)
        if r.status_code == 200:
            return parse_gemini_result(r.json())
        if r.status_code in (429, 500, 502, 503) and attempt < retries - 1:
            time.sleep(backoff); backoff *= 2  # free tier is ~10 RPM
            continue
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
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
    gem = os.environ.get("GEMINI_API_KEY")
    if backend == "gemini" or (backend is None and gem):
        model = os.environ.get("GEMINI_MODEL") or pick_gemini_model(gem)
        log(f"  backend: Gemini free tier, model={model}")
        return (lambda p: summarize_one_gemini(gem, p, labels, model=model)), 2
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
            "No summarizer available. Set GEMINI_API_KEY (free, recommended) or "
            "ANTHROPIC_API_KEY (metered API).")
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
            topics, summary = result
            p.topics = topics                    # LLM reclassifies (supersedes regex)
            p.relevant = bool(topics)            # empty topics -> off-topic -> hidden
            p.summary_ko = summary
            stats["processed"] += 1
            stats["irrelevant"] += int(not topics)
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


def run_catchup(limit: int, force: bool = False, log=print) -> dict:
    """Summarize papers across ALL day/month files, up to `limit` total, oldest
    handled last (newest first). `force` re-does already-summarized papers too —
    used to re-classify existing data after a prompt change. Stays within a
    free-tier daily quota by capping at `limit`."""
    cfg = load_config()
    labels = {t.id: t.label_ko for t in cfg.topics}
    store = Store()
    total = {"processed": 0, "errors": 0}
    for date in store.list_dates():                 # newest first
        if total["processed"] >= limit:
            break
        papers = store.load_day(date)
        if not any(force or not p.summary_ko for p in papers):
            continue
        log(f"-- catchup {date} --")
        stats = summarize_papers(papers, labels, force=force, log=log)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.save_day(date, papers, generated_at=now)
        store.write_index(cfg.topic_meta(), updated_at=now)
        total["processed"] += stats["processed"]
        total["errors"] += stats["errors"]
    log(f"== catchup: processed={total['processed']} errors={total['errors']} ==")
    return total


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Korean summaries + relevance gate")
    ap.add_argument("--date", default=_today(), help="day file to summarize (default: today UTC)")
    ap.add_argument("--force", action="store_true", help="redo papers that already have a summary")
    ap.add_argument("--catchup", type=int, metavar="N", default=None,
                    help="drain up to N still-null summaries across ALL files (backfill backlog)")
    ap.add_argument("--reclassify", type=int, metavar="N", default=None,
                    help="re-summarize up to N already-done papers across ALL files (new prompt)")
    ap.add_argument("--setup-token", action="store_true",
                    help="run `claude setup-token` (one-time subscription auth) and exit")
    args = ap.parse_args(argv)
    if args.setup_token:
        cli = resolve_claude_cli()
        if not cli:
            print("claude CLI not found. Searched PATH and:")
            for pat in claude_cli_globs():
                print(f"  {pat}")
            print("Set CLAUDE_CLI to the full path of claude.exe and retry.")
            return 2
        print(f"launching: {cli} setup-token")
        print("Authorize in the browser, then copy the token and set it, e.g.:")
        print('  PowerShell:  $env:CLAUDE_CODE_OAUTH_TOKEN = "<token>"')
        return subprocess.call([cli, "setup-token"])
    try:
        if args.reclassify is not None:
            run_catchup(args.reclassify, force=True)
        elif args.catchup is not None:
            run_catchup(args.catchup)
        else:
            run(args.date, force=args.force)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

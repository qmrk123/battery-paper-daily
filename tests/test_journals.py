"""Tests for the journal-quality gate (offline — cache provided, no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.models import Paper
from pipeline.journals import enrich_and_filter, is_allowed_journal, _norm


def _p(pid, source="openalex", journal_id=None):
    return Paper(id=pid, source=source, title=pid, url="u",
                 published="2026-08-18", journal_id=journal_id)


CACHE = {
    "S_top": {"name": "Advanced Materials", "metric": 22.0},
    "S_mid": {"name": "Chemistry of Materials", "metric": 6.3},
    "S_low": {"name": "Journal of Materials Science", "metric": 3.9},
    "S_unknown": {"name": "New Journal", "metric": None},
}


def test_journal_gate_keeps_above_drops_below():
    cands = {
        "a": _p("a", journal_id="S_top"),
        "b": _p("b", journal_id="S_mid"),
        "c": _p("c", journal_id="S_low"),
    }
    kept, dropped = enrich_and_filter(cands, threshold=6.0, cache=CACHE)
    assert set(kept) == {"a", "b"}          # 22.0 and 6.3 pass; 3.9 dropped
    assert dropped == 1
    assert kept["a"].journal_metric == 22.0
    assert kept["b"].journal_metric == 6.3


def test_journal_gate_excludes_arxiv_unless_allowed():
    cands = {"x": _p("x", source="arxiv")}
    kept, dropped = enrich_and_filter(cands, threshold=6.0, cache=CACHE)
    assert kept == {} and dropped == 1

    kept2, dropped2 = enrich_and_filter(
        {"x": _p("x", source="arxiv")}, threshold=6.0, include_preprints=True, cache=CACHE)
    assert set(kept2) == {"x"} and dropped2 == 0


def test_allowlist_membership():
    allow = {_norm(n) for n in ["Advanced Materials", "Journal of Energy Chemistry"]}
    assert is_allowed_journal("Advanced Materials", allow)
    assert is_allowed_journal("Nature Communications", allow)   # Nature sister
    assert is_allowed_journal("Nature Energy", allow)
    assert not is_allowed_journal("ACS Nano", allow)
    assert not is_allowed_journal("Small", allow)
    assert not is_allowed_journal(None, allow)


def test_journal_gate_allowlist_mode():
    def paper(pid, venue, jid="S_top"):
        p = Paper(id=pid, source="openalex", title=pid, url="u",
                  published="2026-08-18", venue=venue, journal_id=jid)
        return p
    cands = {
        "a": paper("a", "Advanced Materials"),
        "b": paper("b", "ACS Nano"),               # not on this allowlist
        "c": paper("c", "Nature Energy"),           # Nature sister -> allowed
    }
    kept, dropped = enrich_and_filter(
        cands, threshold=6.0, allow_journals=["Advanced Materials"], cache=CACHE)
    assert set(kept) == {"a", "c"} and dropped == 1


def test_journal_gate_keeps_unknown_metric():
    # a real journal with no metric in OpenAlex is kept (benefit of the doubt)
    cands = {"u": _p("u", journal_id="S_unknown"), "n": _p("n", journal_id="S_missing")}
    kept, dropped = enrich_and_filter(cands, threshold=6.0, cache=CACHE)
    assert set(kept) == {"u", "n"} and dropped == 0

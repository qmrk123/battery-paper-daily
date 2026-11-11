"""Unit tests for the pure pipeline logic (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.openalex import reconstruct_abstract, _clean_doi, _short_id
from pipeline.config import load_config
from pipeline.models import Paper, clean_ws, canonical_key, strip_latex
from pipeline.fetch import select_new, _prefer


def test_reconstruct_abstract_orders_words():
    inv = {"Lithium": [0], "metal": [1], "anodes": [2], "are": [3], "promising": [4]}
    assert reconstruct_abstract(inv) == "Lithium metal anodes are promising"


def test_reconstruct_abstract_handles_repeats_and_gaps():
    inv = {"the": [0, 3], "cathode": [1], "is": [2], "stable": [4]}
    assert reconstruct_abstract(inv) == "the cathode is the stable"


def test_reconstruct_abstract_empty():
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None


def test_clean_doi_and_id():
    assert _clean_doi("https://doi.org/10.1/x") == "10.1/x"
    assert _clean_doi(None) is None
    assert _short_id("https://openalex.org/W123") == "W123"


def test_clean_ws():
    assert clean_ws("  a\n b  ") == "a b"
    assert clean_ws("") is None
    assert clean_ws(None) is None


def test_clean_ws_strips_html_and_entities():
    assert clean_ws("<i>Operando</i> study of Li&#8722;rich") == "Operando study of Li−rich"
    assert clean_ws("NMC<sub>811</sub> &amp; LLZO") == "NMC 811 & LLZO"


def test_strip_latex():
    assert strip_latex(r"(Li$_2$\textit{M})\textit{Ch}O ($M$ = Fe)") == "(Li2M)ChO (M = Fe)"
    assert strip_latex(r"\textbf{High}-energy") == "High-energy"
    assert strip_latex("plain title") == "plain title"


def test_canonical_key_collapses_arxiv_variants():
    # arXiv-source record and OpenAlex-indexed copy of the same preprint
    k_arxiv = canonical_key("10.48550/arXiv.2608.10910", "arxiv:2608.10910")
    k_openalex = canonical_key("https://doi.org/10.48550/arxiv.2608.10910", "W555")
    assert k_arxiv == k_openalex == "arxiv:2608.10910"
    # ordinary journal DOI
    assert canonical_key("10.1021/acsami.6c08647", "W1") == "doi:10.1021/acsami.6c08647"
    # no DOI -> id
    assert canonical_key(None, "W9") == "W9"


def test_prefer_keeps_journal_with_abstract():
    journal = Paper(id="W1", source="openalex", title="T", url="u",
                    published="2026-08-11", abstract_en="full text")
    preprint = Paper(id="arxiv:1", source="arxiv", title="T", url="u",
                     published="2026-08-11", abstract_en="full text")
    assert _prefer(preprint, journal) is journal   # journal of record wins
    no_abs = Paper(id="W2", source="openalex", title="T", url="u", published="x")
    assert _prefer(no_abs, preprint) is preprint    # abstract beats no-abstract


def test_topic_filter_precision():
    cfg = load_config()
    ncm = cfg.topic("ncm")
    # true positive
    assert ncm.matches("Single-crystal Ni-rich NCM811 cathode for Li-ion batteries")
    # false positives that plagued the naive search
    assert not ncm.matches("Nickel production via ion-exchange technology")
    assert not ncm.matches("Effect of nickel addition on corrosion behavior of Al-Mg alloy")
    assert not ncm.matches("P2-type Na0.67 layered cathode for sodium-ion batteries")


def test_topic_filter_li_rich_excludes_sodium():
    cfg = load_config()
    lr = cfg.topic("li-rich")
    assert lr.matches("Anionic redox in Li-rich layered oxide cathodes")
    assert not lr.matches("Na-rich P2 oxygen redox sodium cathode")


def test_context_gate_kills_non_battery_physics():
    cfg = load_config()
    lr = cfg.topic("li-rich")
    # real arXiv false positive: matches "lithium-rich" but has no battery context
    magnet = ("Magnetism in antiperovskite lithium-rich diluted magnets: "
              "we report the magnetic properties and anisotropy of the sublattice")
    assert not lr.matches(magnet)
    # a genuine Li-rich cathode paper still passes the gate
    assert lr.matches("Lithium-rich layered oxide cathode with reversible "
                      "oxygen redox and high capacity retention on cycling")


def test_select_new_stamps_first_seen_and_dedups():
    p1 = Paper(id="W1", source="openalex", title="A", url="u", published="2026-08-13")
    p2 = Paper(id="W2", source="openalex", title="B", url="u", published="2026-08-14")
    seen = {"W1": "2026-08-10"}
    new = select_new({"W1": p1, "W2": p2}, seen, "2026-08-14")
    assert [p.id for p in new] == ["W2"]         # W1 already seen
    assert new[0].first_seen == "2026-08-14"
    assert seen["W2"] == "2026-08-14"            # ledger updated


def test_paper_roundtrip_and_topic_merge():
    p = Paper(id="W1", source="openalex", title="A", url="u", published="2026-08-14")
    p.merge_topics(["li-metal"])
    p.merge_topics(["li-metal", "na-metal"])
    assert p.topics == ["li-metal", "na-metal"]
    assert Paper.from_dict(p.to_dict()).topics == ["li-metal", "na-metal"]


def test_rebuild_month_aggregates_dedups_and_scopes(tmp_path):
    import json
    from pipeline.store import Store
    st = Store(data_dir=tmp_path)

    def day(name, papers):
        (tmp_path / name).write_text(
            json.dumps({"date": name[:-5], "generated_at": "t",
                        "count": len(papers), "papers": papers}),
            encoding="utf-8")

    # W1 appears in two days; the copy carrying a summary must win the dedup
    day("2026-08-01.json", [
        {"id": "W1", "title": "A", "published": "2026-08-01"},
        {"id": "W2", "title": "B", "published": "2026-08-01", "summary_ko": "요약"},
    ])
    day("2026-08-02.json", [
        {"id": "W1", "title": "A", "published": "2026-08-02", "summary_ko": "richer"},
        {"id": "W3", "title": "C", "published": "2026-08-02"},
    ])
    day("2026-07-30.json", [{"id": "W9", "title": "Z", "published": "2026-07-30"}])

    n = st.rebuild_month("2026-08", generated_at="t")
    out = json.loads((tmp_path / "2026-08.json").read_text(encoding="utf-8"))
    ids = {p["id"] for p in out["papers"]}
    assert n == 3 and ids == {"W1", "W2", "W3"}           # July file out of scope; W1 deduped
    w1 = next(p for p in out["papers"] if p["id"] == "W1")
    assert w1["summary_ko"] == "richer"                   # richer record kept
    assert out["papers"][0]["published"] == "2026-08-02"  # newest-published first


def test_index_dates_collapses_completed_months(tmp_path):
    import json
    from pipeline.store import Store
    st = Store(data_dir=tmp_path)

    def touch(name):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"date": name, "count": 0, "papers": []}), encoding="utf-8")

    # Aug + Sep each have per-day files AND a monthly aggregate; Oct is the live month;
    # Jul exists only as an aggregate (older backfill).
    for name in ("2026-07", "2026-08", "2026-08-01", "2026-08-31",
                 "2026-09", "2026-09-15", "2026-10", "2026-10-01"):
        touch(name)

    dates = st.index_dates()
    # live month (Oct) keeps its day; completed months collapse to the aggregate only
    assert "2026-10-01" in dates
    assert "2026-08-01" not in dates and "2026-08-31" not in dates
    assert "2026-09-15" not in dates
    # every monthly aggregate is retained
    assert {"2026-07", "2026-08", "2026-09", "2026-10"} <= set(dates)


def test_index_dates_keeps_days_when_month_aggregate_missing(tmp_path):
    import json
    from pipeline.store import Store
    st = Store(data_dir=tmp_path)

    def touch(name):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"date": name, "count": 0, "papers": []}), encoding="utf-8")

    # Aug has days but NO aggregate -> must not be collapsed (nothing may vanish)
    for name in ("2026-08-01", "2026-09", "2026-09-02"):
        touch(name)
    dates = st.index_dates()
    assert "2026-08-01" in dates          # kept: no 2026-08 aggregate to fall back on
    assert "2026-09-02" in dates          # kept: Sep is the live (newest) month


def test_search_text_normalizes_unicode_hyphens():
    # Wiley/Angew metadata uses U+2010/U+2011 hyphens; topic regexes are written with
    # ASCII '-', so "Ni‐Rich" / "Lithium‐Metal" silently failed the precision filter.
    cfg = load_config()
    by = {t.id: t for t in cfg.topics}
    ncm = Paper(id="W1", source="openalex", url="u", published="2026-11-01", title="Engineering Li+ Transport in Ni‐Rich Layered Cathodes",
                abstract_en="Nickel‐rich layered cathodes for lithium‑ion batteries.")
    lim = Paper(id="W2", source="openalex", url="u", published="2026-11-01", title="Electrolyte for High‐Voltage Lithium‐Metal Batteries",
                abstract_en="Stable cycling of lithium‐metal anodes with Ni‐rich cathodes.")
    assert by["ncm"].matches(ncm.search_text)
    assert by["li-metal"].matches(lim.search_text)


def test_search_text_rejoins_split_formulas():
    # clean_ws turns <sub> tags into spaces ("Na 3 V 2 (PO 4 ) 2 F 3"), which broke
    # formula patterns like na3v2 / lini0.5mn1.5.
    cfg = load_config()
    by = {t.id: t for t in cfg.topics}
    nvpf = Paper(id="W3", source="openalex", url="u", published="2026-11-01", title=clean_ws("High-Energy Na<sub>3</sub>V<sub>2</sub>(PO<sub>4</sub>)<sub>2</sub>F<sub>3</sub> Cathodes"),
                 abstract_en="Sodium-ion battery cathode with long cycling life.")
    assert by["na-cathode"].matches(nvpf.search_text)
    lnmo = Paper(id="W4", source="openalex", url="u", published="2026-11-01", title=clean_ws("Stabilizing LiNi<sub>0.5</sub>Mn<sub>1.5</sub>O<sub>4</sub> at High Voltage"),
                 abstract_en="A lithium battery cathode with improved capacity retention.")
    assert by["other-cathode"].matches(lnmo.search_text)


class _FakeOA:
    """search_source stub: yields canned Papers per source id."""
    def __init__(self, by_sid):
        self.by_sid = by_sid

    def search_source(self, sid, from_date, max_results=3000, types=None):
        yield from self.by_sid.get(sid, [])


def _p(pid, title, abstract="", venue="Advanced Materials", doi=None):
    return Paper(id=pid, source="openalex", url="u", published="2026-11-01",
                 title=title, abstract_en=abstract, venue=venue, doi=doi)


def test_topic_gated_journal_first_keeps_only_topic_matches():
    from pipeline.fetch import add_journal_candidates, GatherResult
    cfg = load_config()
    on = _p("W1", "Engineering Li Transport in Ni‐Rich Layered Cathodes",
            "Nickel-rich layered cathodes for lithium-ion batteries.", doi="10.1/a")
    off = _p("W2", "Graphite anode prelithiation for lithium-ion batteries",
             "Electrolyte additive improves cycling of graphite.", doi="10.1/b")
    res = GatherResult()
    add_journal_candidates(cfg, "2026-10-28", {"Advanced Materials": "S1"}, res,
                           _FakeOA({"S1": [on, off]}), topic_gate=True, log=lambda m: None)
    assert set(res.candidates) == {"doi:10.1/a"}               # off-topic battery paper not kept
    assert res.candidates["doi:10.1/a"].topics == ["ncm"]      # topic assigned from regex


def test_crossref_item_to_paper_uses_created_date_and_strips_jats():
    from pipeline.crossref import item_to_paper
    item = {"DOI": "10.1039/D6EE01899H", "title": ["Redox mediators in Li–S batteries"],
            "created": {"date-parts": [[2026, 11, 9]]}, "published": {"date-parts": [[2026]]},
            "abstract": "<jats:title>Abstract</jats:title><jats:p>Lithium–sulfur <jats:italic>cathode</jats:italic> study.</jats:p>",
            "author": [{"given": "A", "family": "Kim"}], "URL": "https://doi.org/10.1039/D6EE01899H"}
    p = item_to_paper(item, "Energy & Environmental Science")
    assert p.published == "2026-11-09"            # year-only published -> deposit date
    assert p.doi == "10.1039/d6ee01899h" and p.url == "https://doi.org/10.1039/d6ee01899h"
    assert p.abstract_en == "Lithium–sulfur cathode study."
    assert p.venue == "Energy & Environmental Science" and p.authors == ["A Kim"]

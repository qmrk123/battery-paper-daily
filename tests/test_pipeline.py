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

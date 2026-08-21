/* 전지 소재 논문 데일리 — front-end. Reads data/index.json + data/<date>.json. */
"use strict";

const TOPIC_CODE = {
  "li-metal": "LM", "na-metal": "NM", "ncm": "NC", "li-rich": "LR",
  "lfp": "LF", "other-cathode": "OC",
};
const TOPIC_VAR = {
  "li-metal": "--li-metal", "na-metal": "--na-metal", "ncm": "--ncm",
  "li-rich": "--li-rich", "lfp": "--lfp", "other-cathode": "--other-cathode",
};

const state = {
  topics: [],            // [{id,label_ko,label_en,emoji}]
  topicById: {},
  papers: [],            // papers for the loaded day (date mode)
  active: "all",
  // ---- corpus search / filter (search mode) ----
  mode: "date",          // "date" | "search"
  corpus: null,          // all visible papers, lazily fetched from data/corpus.json
  corpusById: {},        // id -> paper (for resolving 'related' ids)
  results: [],           // current search/filter result set
  query: "",
  range: 0,              // 0 = all time, else last-N-days on publication date
  filters: { oa: false, img: false, bmk: false, watch: false },
  reco: false,           // recommendation feed (papers similar to your bookmarks)
};

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls) => { const n = document.createElement(tag); if (cls) n.className = cls; return n; };
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])));

/* ---------- personal state (localStorage: bookmarks / watched groups / read) ---------- */
const LS = {
  read(key) { try { return new Set(JSON.parse(localStorage.getItem(key) || "[]")); } catch (e) { return new Set(); } },
  write(key, s) { try { localStorage.setItem(key, JSON.stringify([...s])); } catch (e) {} },
};
const bookmarks = LS.read("bpd-bookmarks");   // paper ids saved to the reading list
const watched = LS.read("bpd-watch");         // author display names being followed
const readIds = LS.read("bpd-read");          // paper ids already opened

const isBookmarked = (id) => bookmarks.has(id);
const isRead = (id) => readIds.has(id);
const isWatchedPaper = (p) => (p.authors || []).some((a) => watched.has(a));

function toggleBookmark(id) {
  if (bookmarks.has(id)) bookmarks.delete(id); else bookmarks.add(id);
  LS.write("bpd-bookmarks", bookmarks); refresh();
}
function toggleWatch(name) {
  if (watched.has(name)) watched.delete(name); else watched.add(name);
  LS.write("bpd-watch", watched); refresh();
}
function markRead(id) {
  if (!id || readIds.has(id)) return;
  readIds.add(id); LS.write("bpd-read", readIds);
  const c = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  if (c) c.classList.add("is-read");
}

/* ---------- theme ---------- */
function initTheme() {
  const saved = localStorage.getItem("bpd-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  $("#theme-toggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || "dark";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("bpd-theme", next);
  });
}

/* ---------- data ----------
   Two modes: normal site fetches data/*.json; a self-contained snapshot embeds
   everything on window.__BPD__ = {index, days:{date:dayObj}} so the single file
   works offline (and inside a sandboxed preview that blocks fetch). */
const EMB = (typeof window !== "undefined") ? window.__BPD__ : null;

async function getJSON(path) {
  const r = await fetch(path, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}

async function boot() {
  initTheme();
  let index;
  try {
    index = EMB ? EMB.index : await getJSON("data/index.json");
  } catch (e) {
    return fail("데이터를 불러오지 못했습니다. 로컬에서 파일을 직접 열면(file://) " +
                "브라우저 보안 정책으로 막힙니다 — 간이 서버로 열어주세요.", e);
  }
  state.topics = index.topics || [];
  state.topicById = Object.fromEntries(state.topics.map((t) => [t.id, t]));

  const dates = index.dates || [];
  const sel = $("#date-select");
  if (!dates.length) return fail("아직 수집된 날짜가 없습니다.");
  const days = dates.filter((d) => d.length === 10);
  const months = dates.filter((d) => d.length === 7);
  const opt = (v, label) => { const o = el("option"); o.value = v; o.textContent = label; return o; };
  const monthLabel = (m) => `${m.slice(0, 4)}년 ${parseInt(m.slice(5, 7), 10)}월`;
  if (days.length) {
    const g = document.createElement("optgroup"); g.label = "일간";
    days.forEach((d) => g.appendChild(opt(d, d))); sel.appendChild(g);
  }
  if (months.length) {
    const g = document.createElement("optgroup"); g.label = "월간 아카이브";
    months.forEach((m) => g.appendChild(opt(m, monthLabel(m)))); sel.appendChild(g);
  }
  sel.value = days[0] || months[0];
  sel.addEventListener("change", () => { exitSearch(); loadDay(sel.value); updateStepButtons(); });
  initDateStep();
  initSearch();
  initCardActions();

  buildTabs();
  $("#colophon-meta").textContent =
    `updated ${(index.updated_at || "").replace("T", " ").slice(0, 16)} · ${dates.length}일치`;
  await loadDay(dates[0]);
  loadDigest();
  // watchers get the corpus eagerly so the "⭐ 워치 (N)" new-paper badge shows on load
  if (watched.size) { await ensureCorpus(); updateWatchBadge(); }
}

/* ---------- weekly AI brief (data/digest.json) ---------- */
async function loadDigest() {
  const host = $("#digest");
  if (!host) return;
  let d;
  try { d = EMB ? EMB.digest : await getJSON("data/digest.json"); }
  catch (e) { host.hidden = true; return; }
  if (!d || !(d.sections || []).length) { host.hidden = true; return; }
  const sections = d.sections.map((s) => {
    const hls = (s.highlights || []).map((h) =>
      `<a class="digest__hl" href="${esc(h.url)}" target="_blank" rel="noopener">` +
      `<span class="digest__hl-venue">${esc(h.venue || "")}</span>${esc(h.title || "")}</a>`).join("");
    return `<div class="digest__section"><h4 class="digest__h">${esc(s.heading)}</h4>` +
      `<p class="digest__text">${esc(s.text)}</p>` +
      (hls ? `<div class="digest__hls">${hls}</div>` : "") + `</div>`;
  }).join("");
  host.innerHTML =
    `<details class="digest__wrap" open>` +
    `<summary class="digest__summary"><span class="digest__badge">🗞 브리핑</span>` +
    `<span class="digest__title">${esc(d.title || "주간 브리핑")}</span></summary>` +
    (d.overall ? `<p class="digest__overall">${esc(d.overall)}</p>` : "") +
    `<div class="digest__grid">${sections}</div></details>`;
  host.dataset.ready = "1";
  host.hidden = state.mode !== "date";       // only in the default (date) view
}

/* ---------- date stepper (▲ newer / ▼ older, one option at a time) ---------- */
function initDateStep() {
  const newer = $("#date-newer"), older = $("#date-older");
  if (!newer || !older) return;
  newer.addEventListener("click", () => stepDate(-1));  // toward index 0 = newest
  older.addEventListener("click", () => stepDate(+1));  // toward the end = oldest
  updateStepButtons();
}

function stepDate(delta) {
  const sel = $("#date-select");
  const opts = [...sel.options];
  const i = opts.findIndex((o) => o.value === sel.value);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= opts.length) return;   // clamp at both ends
  sel.value = opts[j].value;
  exitSearch();
  loadDay(sel.value);
  updateStepButtons();
}

function updateStepButtons() {
  const sel = $("#date-select");
  const newer = $("#date-newer"), older = $("#date-older");
  if (!newer || !older) return;
  const opts = [...sel.options];
  const i = opts.findIndex((o) => o.value === sel.value);
  newer.disabled = i <= 0;
  older.disabled = i < 0 || i >= opts.length - 1;
}

function buildTabs() {
  const tabs = $("#tabs");
  tabs.innerHTML = "";
  const mk = (id, labelKo, emoji) => {
    const b = el("button", "tab");
    b.type = "button";
    b.dataset.topic = id;
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(id === state.active));
    if (id !== "all") b.style.setProperty("--accent", `var(${TOPIC_VAR[id]})`);
    b.innerHTML =
      (id === "all"
        ? `<span>전체</span>`
        : `<span class="tab__code">${TOPIC_CODE[id] || ""}</span><span>${esc(emoji || "")} ${esc(labelKo)}</span>`) +
      `<span class="tab__count" data-count="${id}"></span>`;
    b.addEventListener("click", () => setActive(id));
    tabs.appendChild(b);
  };
  mk("all");
  state.topics.forEach((t) => mk(t.id, t.label_ko, t.emoji));
}

async function loadDay(date) {
  status(`${date} 데이터를 불러오는 중…`);
  try {
    const day = EMB ? EMB.days[date] : await getJSON(`data/${date}.json`);
    state.papers = (day && day.papers) || [];
  } catch (e) {
    return fail(`${date} 데이터를 불러오지 못했습니다.`, e);
  }
  refresh();
}

// LLM relevance gate hides off-topic papers; not-yet-summarized (null) still show.
const visible = (p) => p.relevant !== false;

/* ---------- corpus search / filter (search mode) ---------- */
// The list currently in view: search/filter results, or the loaded day.
function baseList() {
  return state.mode === "search" ? state.results : state.papers;
}
function searchActive() {
  const f = state.filters;
  return state.reco || state.query.trim() !== "" || state.range > 0 ||
         f.oa || f.img || f.bmk || f.watch;
}

// Recommendation feed: papers most often surfaced as "related" to your bookmarks,
// ranked by how many bookmarks point to them (then recency). Reuses precomputed
// related ids — no model/embeddings needed.
function recommend() {
  const score = new Map();
  bookmarks.forEach((id) => {
    const p = state.corpusById[id];
    ((p && p.related) || []).forEach((rid) => {
      if (!bookmarks.has(rid)) score.set(rid, (score.get(rid) || 0) + 1);
    });
  });
  return [...score.entries()]
    .map(([id, s]) => ({ p: state.corpusById[id], s }))
    .filter((x) => x.p)
    .sort((a, b) => b.s - a.s || (b.p.published || "").localeCompare(a.p.published || ""))
    .map((x) => x.p);
}

// How many recent (14d) papers by a watched author you haven't opened yet.
function watchNewCount() {
  if (!state.corpus || !watched.size) return 0;
  const cutoff = Date.now() - 14 * 864e5;
  return state.corpus.filter((p) => isWatchedPaper(p) && !readIds.has(p.id) &&
    Date.parse((p.published || p.first_seen || "") + "T00:00:00Z") >= cutoff).length;
}
function updateWatchBadge() {
  const chip = $("#f-watch");
  if (!chip) return;
  const n = watchNewCount();
  chip.textContent = n > 0 ? `⭐ 워치 (${n})` : "⭐ 워치";
}

async function ensureCorpus() {
  if (state.corpus) return;
  if (EMB) {                                  // self-contained snapshot: union its days
    const by = {};
    for (const d of Object.values(EMB.days || {}))
      for (const p of (d.papers || [])) if (p.relevant !== false) by[p.id] = p;
    state.corpus = Object.values(by);
  } else {
    state.corpus = (await getJSON("data/corpus.json")).papers || [];
  }
  state.corpusById = Object.fromEntries(state.corpus.map((p) => [p.id, p]));
}

function computeResults() {
  if (state.reco) return recommend();
  const toks = state.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const cutoff = state.range > 0 ? Date.now() - state.range * 864e5 : 0;
  return state.corpus.filter((p) => {
    if (p.relevant === false) return false;
    if (state.filters.oa && !(p.oa_status && p.oa_status.toLowerCase() !== "closed")) return false;
    if (state.filters.img && !(p.image && p.image.cached)) return false;
    if (state.filters.bmk && !bookmarks.has(p.id)) return false;
    if (state.filters.watch && !isWatchedPaper(p)) return false;
    if (cutoff) {
      const ts = Date.parse((p.published || p.first_seen || "") + "T00:00:00Z");
      if (!ts || ts < cutoff) return false;
    }
    if (toks.length) {
      const hay = `${p.title || ""} ${p.abstract_en || ""} ${(p.authors || []).join(" ")} ${p.venue || ""} ${p.doi || ""}`.toLowerCase();
      if (!toks.every((tk) => hay.includes(tk))) return false;
    }
    return true;
  }).sort((a, b) => (b.published || "").localeCompare(a.published || ""));
}

async function refresh() {
  if (searchActive()) {
    state.mode = "search";
    status("전체 아카이브 검색 중…");
    await ensureCorpus();
    state.results = computeResults();
  } else {
    state.mode = "date";
  }
  updateWatchBadge();
  const dg = $("#digest");
  if (dg && dg.dataset.ready) dg.hidden = state.mode !== "date";   // hide the brief while searching
  updateCounts();
  render();
}

function initSearch() {
  const box = $("#search"), clear = $("#search-clear"), range = $("#range");
  let t = 0;
  box.addEventListener("input", () => {
    state.query = box.value;
    clear.hidden = box.value === "";
    clearTimeout(t);
    t = setTimeout(refresh, 160);            // debounce keystrokes
  });
  clear.addEventListener("click", () => {
    box.value = ""; state.query = ""; clear.hidden = true; box.focus(); refresh();
  });
  range.addEventListener("change", () => {
    state.range = parseInt(range.value, 10) || 0; refresh();
  });
  const chip = (id, key) => $(id).addEventListener("click", () => {
    state.filters[key] = !state.filters[key];
    $(id).setAttribute("aria-pressed", String(state.filters[key]));
    refresh();
  });
  chip("#f-oa", "oa");
  chip("#f-img", "img");
  chip("#f-watch", "watch");
  chip("#f-bmk", "bmk");
  $("#f-reco").addEventListener("click", () => {
    state.reco = !state.reco;
    $("#f-reco").setAttribute("aria-pressed", String(state.reco));
    refresh();
  });
  $("#export-bib").addEventListener("click", () => exportCurrent("bib"));
  $("#export-ris").addEventListener("click", () => exportCurrent("ris"));
}

function exitSearch() {
  // picking a specific date drops the corpus-search overlay and resets its controls
  state.query = ""; state.range = 0; state.reco = false;
  state.filters = { oa: false, img: false, bmk: false, watch: false };
  state.mode = "date";
  const box = $("#search"), clear = $("#search-clear"), range = $("#range");
  if (box) box.value = "";
  if (clear) clear.hidden = true;
  if (range) range.value = "0";
  ["#f-oa", "#f-img", "#f-watch", "#f-bmk", "#f-reco"].forEach((id) => {
    const b = $(id); if (b) b.setAttribute("aria-pressed", "false");
  });
}

function updateCounts() {
  const vis = baseList().filter(visible);
  const count = (id) => id === "all"
    ? vis.length
    : vis.filter((p) => (p.topics || []).includes(id)).length;
  document.querySelectorAll(".tab__count").forEach((n) => {
    n.textContent = count(n.dataset.count);
  });
}

function setActive(id) {
  state.active = id;
  document.querySelectorAll(".tab").forEach((t) =>
    t.setAttribute("aria-selected", String(t.dataset.topic === id)));
  render();
}

/* ---------- render ---------- */
// The exact list on screen now: active-topic + visibility applied to the base set.
// Shared by render() and the BibTeX/RIS export so they always match.
function currentList() {
  const src = baseList();
  return (state.active === "all"
    ? src
    : src.filter((p) => (p.topics || []).includes(state.active))
  ).filter(visible);
}

function render() {
  const wrap = $("#cards");
  const list = currentList();

  if (!list.length) {
    wrap.innerHTML = "";
    let msg;
    if (state.reco) msg = bookmarks.size
      ? "추천할 유사 논문을 찾지 못했습니다."
      : "논문을 🔖 북마크하면, 비슷한 논문 추천이 여기 나타납니다.";
    else msg = state.mode === "search"
      ? "검색·필터에 맞는 논문이 없습니다."
      : "이 날짜에는 해당 소재의 새 논문이 없습니다.";
    return status(msg, true);
  }
  if (state.reco) status(`✨ 북마크 기반 추천 · ${list.length}건`);
  else if (state.mode === "search") status(`🔍 전체 검색 · ${list.length}건`);
  else $("#status").textContent = "";
  wrap.innerHTML = "";
  list.forEach((p, i) => wrap.appendChild(card(p, i)));
}

function card(p, i) {
  const accentTopic = state.active === "all" ? (p.topics || [])[0] : state.active;
  const accentVar = TOPIC_VAR[accentTopic] || "--text-faint";
  const code = TOPIC_CODE[accentTopic] || "··";

  const c = el("article", "card");
  c.dataset.id = p.id;
  if (isBookmarked(p.id)) c.classList.add("is-bookmarked");
  if (isWatchedPaper(p)) c.classList.add("is-watched");
  if (isRead(p.id)) c.classList.add("is-read");
  c.style.setProperty("--accent", `var(${accentVar})`);
  c.style.animationDelay = `${Math.min(i * 22, 300)}ms`;

  // thumbnail (image is filled in Phase 3; graceful placeholder until then)
  const thumb = el("div", "card__thumb");
  if (p.image && p.image.cached) {
    const img = el("img"); img.src = p.image.cached; img.alt = ""; img.loading = "lazy";
    thumb.appendChild(img);
  } else {
    thumb.classList.add("card__thumb--ph");
    const s = el("span", "card__code"); s.textContent = code; thumb.appendChild(s);
  }

  const body = el("div", "card__body");
  const dateStr = esc(p.published || p.first_seen || "");
  const isArxiv = p.source === "arxiv";
  body.innerHTML = `
    <div class="card__eyebrow">
      ${p.venue ? `<span class="card__venue">${esc(p.venue)}</span><span class="dot">•</span>` : ""}
      <span>${dateStr}</span>
      ${isArxiv ? `<span class="dot">•</span><span>preprint</span>` : ""}
    </div>
    <h3 class="card__title"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></h3>
    ${authorsLine(p)}
    ${summaryBlock(p)}
    ${abstractBlock(p)}
    <div class="card__foot">${footBadges(p, accentTopic)}</div>`;

  c.appendChild(thumb);
  c.appendChild(body);
  return c;
}

function authorsLine(p) {
  const a = p.authors || [];
  if (!a.length) return "";
  // Which group? The last author (senior/corresponding) is the signal, so show
  // the first author (lead) + the last 3 rather than the first few. Short lists
  // shown in full; the complete author list is on hover (title=).
  const full = a.join(", ");
  const lastName = a[a.length - 1];
  const wcls = watched.has(lastName) ? " is-watched" : "";
  const last = `<strong class="au-last${wcls}" data-author="${esc(lastName)}" ` +
               `role="button" tabindex="0" title="이 저자·그룹 워치/해제">${esc(lastName)}</strong>`;
  let shown;
  if (a.length === 1) {
    shown = last;
  } else if (a.length <= 5) {
    shown = `${a.slice(0, -1).map(esc).join(", ")}, ${last}`;
  } else {
    // first author (lead) + … + last two + LAST (group/corresponding, bold)
    shown = `${esc(a[0])} … ${a.slice(-3, -1).map(esc).join(", ")}, ${last}`;
  }
  return `<p class="card__authors" title="${esc(full)}">${shown}</p>`;
}

function summaryBlock(p) {
  if (p.summary_ko) {
    return `<p class="card__summary"><span class="tag-ko">KO</span>${esc(p.summary_ko)}</p>`;
  }
  if (p.abstract_en) {
    const preview = p.abstract_en.length > 200 ? p.abstract_en.slice(0, 200) + "…" : p.abstract_en;
    return `<p class="card__summary card__summary--placeholder"><span class="tag-ko">원문</span>${esc(preview)}</p>`;
  }
  return `<p class="card__summary card__summary--placeholder">초록이 제공되지 않는 논문입니다.</p>`;
}

function abstractBlock(p) {
  if (!p.abstract_en) return "";
  return `<details class="abstract"><summary>초록 전체</summary><p>${esc(p.abstract_en)}</p></details>`;
}

function footBadges(p, accentTopic) {
  const out = [];
  out.push(`<button type="button" class="badge card__bm" data-bm="${esc(p.id)}" ` +
           `aria-pressed="${isBookmarked(p.id)}" title="북마크(리딩리스트)에 저장/해제">🔖</button>`);
  out.push(`<button type="button" class="badge card__rel-btn" aria-expanded="false" ` +
           `title="비슷한 논문 보기">🧭 관련</button>`);
  if (typeof p.journal_metric === "number") {
    out.push(`<span class="badge badge--metric" title="OpenAlex 2년 평균 피인용 (IF 유사 지표)">📈 ${p.journal_metric.toFixed(1)}</span>`);
  }
  const oa = (p.oa_status || "").toLowerCase();
  if (oa && oa !== "closed") out.push(`<span class="badge badge--oa">OA · ${esc(oa)}</span>`);
  else out.push(`<span class="badge badge--closed">closed</span>`);

  (p.topics || []).forEach((t) => {
    if (t === accentTopic) return;
    const meta = state.topicById[t];
    out.push(`<span class="badge badge--topic" style="--accent:var(${TOPIC_VAR[t] || "--text-faint"})">${esc(meta ? meta.label_ko : t)}</span>`);
  });

  if (p.doi) out.push(`<a class="badge" href="https://doi.org/${esc(p.doi)}" target="_blank" rel="noopener">DOI</a>`);
  out.push(`<a class="badge badge--link" href="${esc(p.url)}" target="_blank" rel="noopener">원문 ↗</a>`);
  return out.join("");
}

/* ---------- status helpers ---------- */
function status(msg, empty) {
  const s = $("#status");
  s.textContent = msg;
  s.classList.toggle("status--empty", !!empty);
}
function fail(msg, err) {
  if (err) console.error(err);
  $("#cards").innerHTML = "";
  status(msg);
}

/* ---------- card interactions (delegated) + citation export ---------- */
function initCardActions() {
  const cards = $("#cards");
  cards.addEventListener("click", (e) => {
    const bm = e.target.closest(".card__bm");
    if (bm) { e.preventDefault(); toggleBookmark(bm.dataset.bm); return; }
    const rel = e.target.closest(".card__rel-btn");
    if (rel) { e.preventDefault(); showRelated(rel); return; }
    const au = e.target.closest(".au-last");
    if (au && au.dataset.author) { e.preventDefault(); toggleWatch(au.dataset.author); return; }
    const link = e.target.closest(".card__title a");
    if (link) { const c = link.closest(".card"); if (c) markRead(c.dataset.id); }  // opens in a new tab; also dims as read
  });
  cards.addEventListener("keydown", (e) => {                 // Enter/Space follows a focused author
    if (e.key !== "Enter" && e.key !== " ") return;
    const au = e.target.closest(".au-last");
    if (au && au.dataset.author) { e.preventDefault(); toggleWatch(au.dataset.author); }
  });
}

async function showRelated(btn) {
  const card = btn.closest(".card");
  let box = card.querySelector(".card__related");
  if (box) {                                    // already loaded → just toggle
    box.hidden = !box.hidden;
    btn.setAttribute("aria-expanded", String(!box.hidden));
    return;
  }
  btn.textContent = "🧭 …";
  await ensureCorpus();
  const p = state.corpusById[card.dataset.id];
  const rel = ((p && p.related) || []).map((id) => state.corpusById[id]).filter(Boolean);
  box = el("div", "card__related");
  box.innerHTML = rel.length
    ? rel.map((q) => `<a class="related__item" href="${esc(q.url)}" target="_blank" rel="noopener">` +
        `<span class="related__venue">${esc(q.venue || "")}</span>${esc(q.title)}</a>`).join("")
    : `<span class="related__empty">비슷한 논문을 찾지 못했습니다.</span>`;
  card.querySelector(".card__body").appendChild(box);
  btn.textContent = "🧭 관련";
  btn.setAttribute("aria-expanded", "true");
}

function _bibKey(p) {
  const surname = ((p.authors && p.authors[0]) || "anon").split(/\s+/).pop().replace(/[^A-Za-z]/g, "") || "anon";
  const year = (p.published || "").slice(0, 4) || "nd";
  return `${surname}${year}_${String(p.id || "").replace(/[^A-Za-z0-9]/g, "")}`;
}
function toBibtex(list) {
  return list.map((p) => {
    const rows = [
      ["title", p.title], ["author", (p.authors || []).join(" and ")],
      ["journal", p.venue], ["year", (p.published || "").slice(0, 4)],
      ["doi", p.doi], ["url", p.url],
    ].filter(([, v]) => v);
    return `@article{${_bibKey(p)},\n` +
      rows.map(([k, v]) => `  ${k} = {${v}}`).join(",\n") + "\n}";
  }).join("\n\n") + "\n";
}
function toRIS(list) {
  return list.map((p) => {
    const L = ["TY  - JOUR"];
    (p.authors || []).forEach((au) => L.push(`AU  - ${au}`));
    if (p.title) L.push(`TI  - ${p.title}`);
    if (p.venue) L.push(`JO  - ${p.venue}`);
    const y = (p.published || "").slice(0, 4); if (y) L.push(`PY  - ${y}`);
    if (p.doi) L.push(`DO  - ${p.doi}`);
    if (p.url) L.push(`UR  - ${p.url}`);
    L.push("ER  - ");
    return L.join("\n");
  }).join("\n\n") + "\n";
}
function downloadText(name, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = el("a"); a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function exportCurrent(kind) {
  const list = currentList();
  if (!list.length) return status("내보낼 논문이 없습니다.", true);
  const stamp = state.mode === "search" ? "search" : ($("#date-select").value || "feed");
  if (kind === "ris") downloadText(`battery-papers_${stamp}.ris`, toRIS(list));
  else downloadText(`battery-papers_${stamp}.bib`, toBibtex(list));
}

boot();

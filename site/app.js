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
  papers: [],            // papers for the loaded day
  active: "all",
};

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls) => { const n = document.createElement(tag); if (cls) n.className = cls; return n; };
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])));

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
  sel.addEventListener("change", () => loadDay(sel.value));

  buildTabs();
  $("#colophon-meta").textContent =
    `updated ${(index.updated_at || "").replace("T", " ").slice(0, 16)} · ${dates.length}일치`;
  await loadDay(dates[0]);
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
  updateCounts();
  render();
}

// LLM relevance gate hides off-topic papers; not-yet-summarized (null) still show.
const visible = (p) => p.relevant !== false;

function updateCounts() {
  const vis = state.papers.filter(visible);
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
function render() {
  const wrap = $("#cards");
  const list = (state.active === "all"
    ? state.papers
    : state.papers.filter((p) => (p.topics || []).includes(state.active))
  ).filter(visible);

  if (!list.length) {
    wrap.innerHTML = "";
    return status("이 날짜에는 해당 소재의 새 논문이 없습니다.", true);
  }
  $("#status").textContent = "";
  wrap.innerHTML = "";
  list.forEach((p, i) => wrap.appendChild(card(p, i)));
}

function card(p, i) {
  const accentTopic = state.active === "all" ? (p.topics || [])[0] : state.active;
  const accentVar = TOPIC_VAR[accentTopic] || "--text-faint";
  const code = TOPIC_CODE[accentTopic] || "··";

  const c = el("article", "card");
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
  const shown = a.length <= 5
    ? a.map(esc).join(", ")
    : `${esc(a[0])} … ${a.slice(-3).map(esc).join(", ")}`;
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

boot();

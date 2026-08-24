// Embed every corpus paper with all-MiniLM-L6-v2 (the SAME model the browser runs
// for the query) and write data/embeddings.json as base64-packed int8 vectors, so
// neural free-text search ranks the whole corpus client-side by cosine similarity.
//
//   node embed_corpus.mjs [repoRoot] [--only-new]
//
// --only-new keeps existing embeddings and embeds only ids missing from them
// (used by CI so the daily run doesn't re-embed the whole corpus every time).
import { pipeline, env } from '@xenova/transformers';
import fs from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
const onlyNew = args.includes('--only-new');
const ROOT = path.resolve(args.find((a) => !a.startsWith('--')) || path.join(import.meta.dirname, '..', '..'));
const dataDir = path.join(ROOT, 'data');
const outPath = path.join(dataDir, 'embeddings.json');

const DIM = 384, SCALE = 127;
env.allowLocalModels = false;

// Union the per-day/-month data files (visible papers, deduped) — same set the site
// searches. Reading data/ directly avoids depending on build_site running first.
const pool = new Map();
for (const f of fs.readdirSync(dataDir)) {
  if (!/^\d{4}-\d{2}(-\d{2})?\.json$/.test(f)) continue;
  for (const p of (JSON.parse(fs.readFileSync(path.join(dataDir, f), 'utf8')).papers || [])) {
    if (p.id && p.relevant !== false && !pool.has(p.id)) pool.set(p.id, p);
  }
}
const papers = [...pool.values()].sort((a, b) => (b.published || '').localeCompare(a.published || ''));

// existing vectors (for --only-new): id -> Int8Array slice
let prev = null;
if (onlyNew && fs.existsSync(outPath)) {
  const j = JSON.parse(fs.readFileSync(outPath, 'utf8'));
  const buf = new Int8Array(Buffer.from(j.b64, 'base64'));
  prev = new Map(j.ids.map((id, i) => [id, buf.subarray(i * DIM, (i + 1) * DIM)]));
}

const todo = papers.filter((p) => !(prev && prev.has(p.id)));
console.log(`corpus ${papers.length} | to embed ${todo.length}${onlyNew ? ' (only-new)' : ''}`);

let extractor = null;
if (todo.length) {
  console.log('loading model Xenova/all-MiniLM-L6-v2 …');
  extractor = await pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2', { quantized: true });
}

const ids = [];
const bytes = new Int8Array(papers.length * DIM);
let n = 0, done = 0;
for (const p of papers) {
  ids.push(p.id);
  const off = n * DIM;
  if (prev && prev.has(p.id)) {
    bytes.set(prev.get(p.id), off);                       // reuse
  } else {
    const text = `${p.title || ''} ${p.abstract_en || ''}`.replace(/\s+/g, ' ').trim().slice(0, 1400) || (p.title || 'battery');
    const out = await extractor(text, { pooling: 'mean', normalize: true });
    for (let k = 0; k < DIM; k++) bytes[off + k] = Math.max(-127, Math.min(127, Math.round(out.data[k] * SCALE)));
    if (++done % 200 === 0) console.log(`  embedded ${done}/${todo.length}`);
  }
  n++;
}

const b64 = Buffer.from(bytes.buffer).toString('base64');
fs.writeFileSync(outPath, JSON.stringify({ model: 'Xenova/all-MiniLM-L6-v2', dim: DIM, scale: SCALE, count: ids.length, ids, b64 }));
console.log(`wrote ${path.relative(ROOT, outPath)} — ${ids.length} vectors, ${(b64.length / 1024).toFixed(0)} KB base64`);

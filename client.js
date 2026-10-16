/* Specimen Sheet: the birds and wildlife around the panel, cut out and packed onto one printed
   page. The server (server.py) reduces the source to species rows with a picture each; this file
   cuts the pictures out and packs the sheet in the render browser (static/sheet.js), then prints
   the captions, the header and the foot over it. A finished sheet is sent back to the server and
   comes inlined with the next data, so a page is packed once per change, not once per render. */
import { buildSheet, quickSheet, drawIntoMount } from './static/sheet.js';

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const hexRgb = (hex) => { const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || '')); return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null; };
const SOURCE_NAME = { birdnet_go: 'BirdNET-Go', inaturalist: 'iNaturalist', gbif: 'GBIF' };

/* the printed header: the place and the period in small capitals, the date to the right, over a double rule */
function head(d) {
  const bits = [d.title, d.area, d.period].filter(Boolean).map(esc).join(' · ');
  return `<div class="ph"><span class="ph-k">${bits}</span><b class="ph-d">${esc(d.date)}</b></div><div class="ph-rule"></div>`;
}

function foot(d, extra = []) {
  const mode = d.footer || 'show';
  if (mode === 'hide') return '';
  const bits = [...(mode === 'credits' ? [] : [...(d.notices || []), ...(d.status || []), ...extra]), ...(d.credits || [])];
  return bits.length ? `<div class="credit">${bits.map(esc).join(' · ')}</div>` : '';
}

function message(d, title, body, hint) {
  return `${head(d)}<div class="msg"><div class="msg-t">${esc(title)}</div><div class="msg-b">${esc(body)}</div>${hint ? `<div class="msg-h">${esc(hint)}</div>` : ''}</div>${foot(d)}`;
}

/* After dark, when nothing has called for a while: the moon and the last bird heard. */
function asleep(d) {
  const sky = d.sky || {}, a = d.asleep || {};
  const ph = sky.moon || 0, k = Math.cos(ph * 2 * Math.PI), right = ph < 0.5;
  const moon = `<svg class="nt-moon" viewBox="-52 -52 104 104" aria-hidden="true"><circle r="48" class="lit"/><path class="dark" d="M0,-48 A48,48 0 0,${right ? 0 : 1} 0,48 A${(48 * Math.abs(k)).toFixed(1)},48 0 0,${(right ? k > 0 : k < 0) ? 0 : 1} 0,-48 Z"/></svg>`;
  const quiet = a.quiet_min >= 60 ? `${Math.floor(a.quiet_min / 60)} h ${a.quiet_min % 60} min` : `${a.quiet_min || 0} min`;
  return `${head(d)}<div class="nt">${moon}<div class="nt-t">The garden is asleep${a.since ? ` since ${esc(a.since)}` : ''}</div><div class="nt-s">Nothing heard for ${quiet}${a.last ? `. Last was the ${esc(a.last)}` : ''}.</div><div class="nt-f">${esc(sky.moon_name || '')}${sky.sunrise ? ` · sunrise ${esc(sky.sunrise)}` : ''} · ${d.species_total} species to-day</div></div>${foot(d)}`;
}

/* ---- building the sheet without holding the page ----
   The packer runs in one module worker (OffscreenCanvas) so the page stays responsive; the page
   builds the sheet itself where workers are missing or one fails. */
const WORKER_PATH = './static/sheet.js';
let WORKER = null;
let CHAIN = Promise.resolve();
function packOffThread(args, onCut) {
  if (typeof Worker === 'undefined' || typeof OffscreenCanvas === 'undefined') return buildSheet({ ...args, onCut });
  const run = () => new Promise((resolve) => {
    try { if (!WORKER) WORKER = new Worker(new URL(WORKER_PATH, import.meta.url), { type: 'module' }); } catch { resolve(buildSheet({ ...args, onCut })); return; }
    const w = WORKER;
    let done = false;
    const finish = (sheet) => { if (done) return; done = true; clearTimeout(timer); w.onmessage = null; w.onerror = null; resolve(sheet); };
    const drop = () => { if (WORKER === w) WORKER = null; try { w.terminate(); } catch { /* gone */ } };
    const timer = setTimeout(() => { drop(); finish(buildSheet({ ...args, onCut })); }, 90000);
    w.onmessage = (e) => {
      if (e.data && e.data.kind === 'cut') { if (onCut) { try { onCut(e.data.i, e.data.bitmap, e.data.blend); } catch { /* placeholder gone */ } } return; }
      finish(e.data && e.data.ok ? e.data.sheet : buildSheet({ ...args, onCut }));
    };
    w.onerror = () => { drop(); finish(buildSheet({ ...args, onCut })); };
    w.postMessage(args);
  });
  CHAIN = CHAIN.then(run, run);
  return CHAIN;
}

/* A cheap key of everything that decides the picture: names, picture sizes, factors, caption
   lines, page shape and look. The same key means the same sheet. */
const fnv = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(36); };
function keyOf(a) {
  const parts = a.specimens.map((x) => `${x.name}|${x.sci}|${x.src ? x.src.length : 0}|${x.cut ? x.cut.length : 0}|${x.factor}|${x.on_paper ? 1 : 0}${x.classic ? 'c' : ''}|${(x.lines || []).join('/')}`);
  return `ss2:${a.ratio.toFixed(4)}:${a.size}:${a.label.toFixed(4)}:${a.paper}:${a.layout}:${a.zoom}:${fnv(parts.join(';'))}`;
}
const SHEETS = new Map();

/* The finished sheet goes back to the server, which inlines it with the next data for this cell. */
function send(d, key, sheet) {
  if (!d.cell_id || typeof fetch !== 'function') return;
  try {
    const url = new URL('./sheet', import.meta.url);
    fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cell: d.cell_id, key, sheet }) }).catch(() => {});
  } catch { /* no fetch here */ }
}

function buildArgs(d) {
  const w = d.cell_w || 800, h = d.cell_h || 480;
  let specimens = d.species.map((s) => ({
    name: s.name, sci: s.sci, src: s.pic ? s.pic.src : null, cut: s.pic ? s.pic.cut || null : null,
    on_paper: !!(s.pic && s.pic.on_paper), classic: !!(s.pic && s.pic.classic), whole: false, factor: Number(s.factor) || 1, aspect: Number(s.pic && s.pic.aspect) || 0.8,
    paper: hexRgb(s.pic && s.pic.paper), neutral: false,
    lines: d.labels === 'none' ? [] : (s.lines || [s.name]),
  }));
  let label = Math.max(0.017, 13 / h) * (d.text_scale || 1);
  const ink = d.panel === 'colour' ? '#2a2116' : '#000000';
  let layout = ['cluster', 'grid', 'hero'].includes(d.layout) ? d.layout : 'cluster';
  if (layout === 'hero') { specimens = specimens.slice(0, 1); label *= 1.9; layout = 'cluster'; }
  return { specimens, ratio: w / h, size: 1400, label, paper: d.paper, ink, layout, facing: 'inward', zoom: Math.max(0.4, Math.min(1, (Number(d.scale) || 100) / 100)) };
}

async function build(d, args, key, onCut) {
  if (typeof document === 'undefined') return null;
  try {
    const sheet = await packOffThread(args, onCut);
    if (sheet) { SHEETS.set(key, sheet); send(d, key, sheet); }
    return sheet;
  } catch { return null; }
}

function captionOf(s, lines) {
  return lines.map((t, i) => `<span class="sh-ln${i === 0 ? ' sh-name' : ''}${i > 0 && t === s.sci ? ' sh-sci' : ''}${i > 0 && t !== s.sci ? ' sh-sm' : ''}">${esc(t).replace(/\n/g, '<br>')}</span>`).join('');
}

function sheet(d, made) {
  const bySci = Object.fromEntries(d.species.map((s) => [s.sci || s.name, s]));
  const tags = made.items.map((m) => {
    const lines = m.lines || [];
    if (!lines.length) return '';
    const s = bySci[m.sci || m.name] || { name: m.name, sci: m.sci };
    return `<span class="sh-l" style="left:${(m.at[0] * 100).toFixed(2)}%;top:${(m.at[1] * 100).toFixed(2)}%">${captionOf(s, lines)}</span>`;
  }).join('');
  const mounts = made.items.filter((m) => m.mount).length;
  const extra = made.quick ? ['cutting the pictures out'] : mounts ? [`${mounts} picture${mounts === 1 ? '' : 's'} still to come`] : [];
  const page = `<div class="sh-page" style="--r:${made.ratio};--lf:${made.label}"${made.quick ? ' data-quick' : ''}>${made.quick ? '' : `<img src="${made.src}" alt="" decoding="async">`}${tags}</div>`;
  const also = d.layout === 'hero' && d.species.length > 1 && d.labels !== 'none'
    ? `<div class="hero-also"><i>also</i> ${d.species.slice(1).map((s) => `<span>${esc(s.name)}${s.count ? ` <b>${esc(String(s.count))}</b>` : ''}</span>`).join('<em>·</em>')}</div>` : '';
  return `${head(d)}<div class="sh">${page}</div>${also}${foot(d, extra)}`;
}

/* With no canvas (a test in node, or a browser that refused): the pictures as a plain grid. */
function montage(d) {
  const tiles = d.species.map((s) => `<figure>${s.pic ? `<img src="${s.pic.src}" alt="">` : '<div class="nopic"></div>'}<figcaption>${esc(s.name)}</figcaption></figure>`).join('');
  return `${head(d)}<div class="mont n${Math.min(d.species.length, 8)}">${tiles}</div>${foot(d)}`;
}

export default async function render(shadow, ctx) {
  const d = { ...((ctx && ctx.data) || {}) };
  d.cell_w = (ctx && ctx.cell && ctx.cell.w) || 0;
  d.cell_h = (ctx && ctx.cell && ctx.cell.h) || 0;
  d.species = Array.isArray(d.species) ? d.species : [];
  const size = (ctx && ctx.cell && ctx.cell.size) || 'md';
  let body;
  if (!d.source) body = `<div class="msg"><div class="msg-t">Specimen Sheet</div><div class="msg-b">No data from the server yet.</div></div>`;
  else if (d.state === 'error' || d.state === 'nowhere') body = message(d, d.title || 'Specimen Sheet', d.error || 'Something went wrong.', d.hint);
  else if (d.state === 'empty') body = message(d, d.title || SOURCE_NAME[d.source] || 'Nature', `Nothing ${d.source === 'birdnet_go' ? 'heard' : 'recorded'} ${[d.area, d.period.replace(/^heard /, '')].filter(Boolean).join(' ')}.`, d.hint);
  else if (d.state === 'painting') body = message(d, d.title || SOURCE_NAME[d.source] || 'Nature', 'Nothing painted yet.', d.hint);
  else if (d.state === 'asleep') body = asleep(d);
  else {
    const args = buildArgs(d);
    const key = keyOf(args);
    // the sheet the server kept for this cell, when it is the sheet these inputs make; else the
    // page at once with every specimen as an empty mount, each drawn in as its cut-out is ready,
    // then the finished sheet in place
    let made = SHEETS.get(key) || (d.sheet && d.sheet.key === key ? d.sheet.sheet : null);
    if (!made && typeof document !== 'undefined') {
      let quick = null;
      try { quick = quickSheet(args); } catch { quick = null; }
      if (quick) { paint(shadow, d, size, sheet(d, quick)); const slot = shadow.querySelector('[data-quick]'); if (slot) slot.prepend(quick.canvas); }
      made = await build(d, args, key, quick ? (i, pic, blend) => drawIntoMount(quick, i, pic, blend) : null);
    }
    body = made ? sheet(d, made) : montage(d);
  }
  paint(shadow, d, size, body);
}

function paint(shadow, d, size, body) {
  const paper = { cream: '#f3ead6', white: '#ffffff', aged: '#e9dcc0' }[d.paper] || '#f3ead6';
  shadow.innerHTML = `
    <style>${d.font_css || ''}</style>
    <div class="ss size-${esc(size)} lv-${esc(d.panel || 'colour')}" style="--paper:${paper};--ts:${Number(d.text_scale) > 0 ? Number(d.text_scale) : 1}">${body}</div>
    <style>
      :host { display: block; height: 100%; }
      .ss { container-type: size; height: 100%; width: 100%; overflow: hidden; position: relative; display: flex; flex-direction: column; background: var(--paper); color: #2a2116;
        --ink: #2a2116; --serif: 'IM Fell English', Georgia, serif; --sc: 'IM Fell English SC', Georgia, serif; font-family: var(--serif); font-variant-numeric: tabular-nums; }
      .lv-gray16, .lv-gray4, .lv-mono { color: #000; --ink: #000; }
      .lv-mono { --serif: 'Archivo Narrow', Arial, sans-serif; --sc: 'Archivo Narrow', Arial, sans-serif; }
      .ph { display: flex; justify-content: space-between; align-items: baseline; gap: 2cqmin; padding: 1.2cqmin 2.4cqmin .3cqmin; flex: 0 0 auto; }
      .ph-k { font-family: var(--sc); font-size: 2.4cqmin; letter-spacing: .13em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .ph-d { font-weight: 400; font-size: 3.2cqmin; letter-spacing: .04em; white-space: nowrap; }
      .ph-rule { border-top: 1px solid var(--ink); position: relative; margin: 0 2.4cqmin .2cqmin; flex: 0 0 auto; }
      .ph-rule::after { content: ""; position: absolute; left: 0; right: 0; top: 2px; border-top: 1px solid var(--ink); }
      .credit { flex: 0 0 auto; font-size: 1.8cqmin; opacity: .65; padding: .3cqmin 2.4cqmin .7cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-style: italic; }
      /* the sheet fills what is left between the rule and the foot */
      .sh { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; padding: 0 .6cqmin; }
      .sh-page { position: relative; aspect-ratio: var(--r); max-width: 100%; max-height: 100%; width: min(100%, calc((100cqh - 9cqmin) * var(--r))); container-type: size; }
      .sh-page > img, .sh-page > canvas { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: fill; display: block; }
      .hero-also { flex: 0 0 auto; padding: .4cqmin 2.4cqmin .6cqmin; font-family: var(--sc); font-size: 2.6cqmin; letter-spacing: .08em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: center; }
      .hero-also i { font-family: var(--serif); font-style: italic; opacity: .7; margin-right: .4em; }
      .hero-also em { font-style: normal; opacity: .5; margin: 0 .5em; }
      .hero-also b { font-weight: 400; opacity: .75; }
      .sh-l { position: absolute; transform: translate(-50%, 12%); white-space: nowrap; text-align: center; line-height: 1.15; font-family: var(--sc); font-size: calc(var(--lf) * 100cqh); letter-spacing: .04em; color: var(--ink); }
      .sh-ln { display: block; }
      .sh-sci { font-family: var(--serif); font-style: italic; letter-spacing: 0; }
      .sh-sm { font-family: var(--serif); font-style: italic; letter-spacing: 0; font-size: .88em; opacity: .85; }
      .lv-mono .sh-l { -webkit-text-stroke: .3px currentColor; }
      .mont { flex: 1; min-height: 0; display: grid; gap: 1.6cqmin; padding: .6cqmin 2.4cqmin 1cqmin; grid-template-columns: repeat(4, 1fr); grid-auto-rows: 1fr; }
      .mont.n1 { grid-template-columns: 1fr; } .mont.n2 { grid-template-columns: repeat(2, 1fr); } .mont.n3 { grid-template-columns: repeat(3, 1fr); }
      .mont figure { margin: 0; min-height: 0; display: flex; flex-direction: column; align-items: center; }
      .mont img { flex: 1; min-height: 0; width: 100%; object-fit: contain; mix-blend-mode: multiply; }
      .mont .nopic { flex: 1; width: 60%; border: 1px dashed var(--ink); border-radius: 50%; opacity: .5; }
      .mont figcaption { font-family: var(--sc); font-size: 2.2cqmin; letter-spacing: .04em; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
      .msg { flex: 1; display: flex; flex-direction: column; justify-content: center; gap: 1.4cqmin; padding: 4cqmin 6cqmin; text-align: center; }
      .msg-t { font-family: var(--sc); font-size: 5cqmin; letter-spacing: .06em; }
      .msg-b { font-size: 3.4cqmin; }
      .msg-h { font-size: 2.6cqmin; font-style: italic; opacity: .75; }
      .nt { flex: 1; min-height: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1.2cqmin; text-align: center; padding: 2cqmin 6cqmin; }
      .nt-moon { width: 22cqmin; height: 22cqmin; }
      .nt-moon .lit { fill: var(--paper); stroke: var(--ink); stroke-width: 2; }
      .nt-moon .dark { fill: var(--ink); }
      .nt-t { font-family: var(--sc); font-size: 5.2cqmin; letter-spacing: .06em; }
      .nt-s { font-size: 3.2cqmin; font-style: italic; }
      .nt-f { font-family: var(--sc); font-size: 2.3cqmin; letter-spacing: .12em; opacity: .8; }
    </style>`;
}

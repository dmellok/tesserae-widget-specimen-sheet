/* Specimen Sheet: the birds and wildlife around the panel, cut out and laid
   out together on one printed page. The server (server.py) reduces the
   source to species rows with a picture each; this file cuts the pictures
   out and packs the sheet in the render browser (sheet.js), then prints
   the labels, the header and the foot over it. Everything arrives inlined,
   because the render sandbox has no network. */
import { buildSheet } from './sheet.js';

const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const SOURCE_NAME = { birdnet_go: 'BirdNET-Go', inaturalist: 'iNaturalist', gbif: 'GBIF' };

/* the printed header: the place and the period in small caps, the date to the right, over a double rule */
function head(d) {
  const bits = [d.title, d.area].filter(Boolean).map(esc).join(' · ');
  return `<div class="ph"><span class="ph-k">${bits}</span><b class="ph-d">${esc(d.date)}</b></div><div class="ph-rule"></div>`;
}

function foot(d, extra = []) {
  const mode = d.footer || 'show';
  if (mode === 'hide') return '';
  const bits = [...(mode === 'credits' ? [] : [...(d.notices || []), ...(d.status || []), ...extra]), ...(d.credits || [])];
  return bits.length ? `<div class="credit">${bits.map(esc).join(' · ')}</div>` : '';
}

/* ---------------- the quiet pages ---------------- */

function message(d, title, body, hint) {
  return `${head(d)}<div class="msg"><div class="msg-t">${esc(title)}</div><div class="msg-b">${esc(body)}</div>${hint ? `<div class="msg-h">${esc(hint)}</div>` : ''}</div>${foot(d)}`;
}

/* After dark, when nothing has called for a while: the moon and the last bird heard. */
function asleep(d) {
  const sky = d.sky || {};
  const a = d.asleep || {};
  const ph = sky.moon || 0;
  const k = Math.cos(ph * 2 * Math.PI);
  const right = ph < 0.5;
  const moon = `<svg class="nt-moon" viewBox="-52 -52 104 104" aria-hidden="true">
      <circle r="48" class="lit"/>
      <path class="dark" d="M0,-48 A48,48 0 0,${right ? 0 : 1} 0,48 A${(48 * Math.abs(k)).toFixed(1)},48 0 0,${(right ? k > 0 : k < 0) ? 0 : 1} 0,-48 Z"/>
    </svg>`;
  const quiet = a.quiet_min >= 60 ? `${Math.floor(a.quiet_min / 60)} h ${a.quiet_min % 60} min` : `${a.quiet_min || 0} min`;
  return `${head(d)}
    <div class="nt">
      ${moon}
      <div class="nt-t">The garden is asleep${a.since ? ` since ${esc(a.since)}` : ''}</div>
      <div class="nt-s">Nothing heard for ${quiet}${a.last ? `. Last was the ${esc(a.last)}` : ''}.</div>
      <div class="nt-f">${esc(sky.moon_name || '')}${sky.sunrise ? ` · sunrise ${esc(sky.sunrise)}` : ''} · ${d.species_total} species to-day</div>
    </div>${foot(d)}`;
}

/* ---------------- the sheet ---------------- */

async function build(d) {
  if (typeof document === 'undefined') return null;
  const w = d.cell_w || 800, h = d.cell_h || 480;
  const specimens = d.species.map((s) => ({
    name: s.name, sci: s.sci, src: s.pic ? s.pic.src : null, cut: s.pic ? s.pic.cut || null : null,
    on_paper: !!(s.pic && s.pic.on_paper), factor: Number(s.factor) || 1,
    lines: d.labels === 'none' ? [] : d.labels === 'key' ? ['00'] : (s.lines || [s.name]),
  }));
  const label = Math.max(0.017, 13 / h) * (d.text_scale || 1);
  const ink = d.panel === 'colour' ? '#2a2116' : '#000000';
  try {
    return await buildSheet({ specimens, ratio: w / h, size: 1400, label, paper: d.paper, ink });
  } catch { return null; }
}

function labelOf(s, lines) {
  return (lines || [s.name]).map((t, i) => `<span class="sh-ln${i === 0 ? ' sh-name' : ''}${i > 0 && t === s.sci ? ' sh-sci' : ''}${i > 0 && t !== s.sci ? ' sh-sm' : ''}">${esc(t)}</span>`).join('');
}

function sheet(d, made) {
  const bySci = Object.fromEntries(d.species.map((s) => [s.sci || s.name, s]));
  const labels = d.labels || 'caption';
  const tags = made.items.map((m, i) => {
    const s = bySci[m.sci || m.name] || { name: m.name, sci: m.sci, lines: [m.name] };
    const [x, y] = m.at;
    if (labels === 'none') return '';
    const text = labels === 'key' ? String(i + 1) : labelOf(s, s.lines);
    return `<span class="sh-l${labels === 'tag' ? ' sh-tag' : ''}${labels === 'key' ? ' sh-num' : ''}" style="left:${(x * 100).toFixed(2)}%;top:${(y * 100).toFixed(2)}%">${text}</span>`;
  }).join('');
  const mounts = made.items.filter((m) => m.mount).length;
  const extra = mounts ? [`${mounts} picture${mounts === 1 ? '' : 's'} still to come`] : [];
  const page = `<div class="sh-page" style="--r:${made.ratio};--lf:${made.label}"><img src="${made.src}" alt="">${tags}</div>`;
  if (labels === 'key') {
    const rows = made.items.map((m, i) => { const s = bySci[m.sci || m.name] || {}; return `<li><em>${i + 1}.</em><span>${esc(m.name)}${s.sci ? `<i>${esc(s.sci)}</i>` : ''}</span><b>${s.count || ''}</b></li>`; }).join('');
    return `${head(d)}<div class="cl"><div class="cl-pic">${page}</div><ol class="cl-key">${rows}</ol></div>${foot(d, extra)}`;
  }
  return `${head(d)}<div class="sh">${page}</div>${foot(d, extra)}`;
}

/* With no canvas (a test in node, or a browser that refused): the pictures
   as a plain grid, so the page is never empty. */
function montage(d) {
  const tiles = d.species.map((s) => `<figure>${s.pic ? `<img src="${s.pic.src}" alt="">` : '<div class="nopic"></div>'}<figcaption>${esc(s.name)}</figcaption></figure>`).join('');
  return `${head(d)}<div class="mont n${Math.min(d.species.length, 8)}">${tiles}</div>${foot(d)}`;
}

/* ---------------- render ---------------- */

export default async function render(shadow, ctx) {
  const d = { ...((ctx && ctx.data) || {}) };
  d.cell_w = (ctx && ctx.cell && ctx.cell.w) || 0;
  d.cell_h = (ctx && ctx.cell && ctx.cell.h) || 0;
  d.species = Array.isArray(d.species) ? d.species : [];
  const size = (ctx && ctx.cell && ctx.cell.size) || 'md';
  let body;
  if (!d.source) body = `<div class="msg"><div class="msg-t">Specimen Sheet</div><div class="msg-b">No data from the server yet.</div></div>`;
  else if (d.state === 'error' || d.state === 'nowhere') body = message(d, d.title || 'Specimen Sheet', d.error || 'Something went wrong.', d.hint);
  else if (d.state === 'empty') body = message(d, d.title || SOURCE_NAME[d.source] || 'Nature', `Nothing recorded ${d.area ? d.area + ' ' : ''}${d.period || ''}.`.replace('  ', ' '), d.hint);
  else if (d.state === 'asleep') body = asleep(d);
  else {
    const made = await build(d);
    body = made ? sheet(d, made) : montage(d);
  }
  const paper = { cream: '#f3ead6', white: '#ffffff', aged: '#e9dcc0' }[d.paper] || '#f3ead6';
  shadow.innerHTML = `
    <style>${d.font_css || ''}</style>
    <div class="ss size-${esc(size)} lv-${esc(d.panel || 'colour')}" style="--paper:${paper};--ts:${Number(d.text_scale) > 0 ? Number(d.text_scale) : 1}">${body}</div>
    <style>
      :host { display: block; height: 100%; }
      .ss {
        container-type: size; height: 100%; width: 100%; overflow: hidden; position: relative;
        display: flex; flex-direction: column;
        background: var(--paper); color: #2a2116;
        --ink: #2a2116; --serif: 'IM Fell English', Georgia, serif; --sc: 'IM Fell English SC', Georgia, serif;
        font-family: var(--serif); font-variant-numeric: tabular-nums;
      }
      .lv-gray16, .lv-gray4, .lv-mono { color: #000; --ink: #000; }
      .lv-mono { --serif: 'Archivo Narrow', Arial, sans-serif; --sc: 'Archivo Narrow', Arial, sans-serif; }
      .ph { display: flex; justify-content: space-between; align-items: baseline; gap: 2cqmin; padding: 1.6cqmin 2.8cqmin .4cqmin; flex: 0 0 auto; }
      .ph-k { font-family: var(--sc); font-size: 2.4cqmin; letter-spacing: .13em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .ph-d { font-weight: 400; font-size: 3.4cqmin; letter-spacing: .04em; white-space: nowrap; }
      .ph-rule { border-top: 1px solid var(--ink); position: relative; margin: 0 2.8cqmin .4cqmin; flex: 0 0 auto; }
      .ph-rule::after { content: ""; position: absolute; left: 0; right: 0; top: 2px; border-top: 1px solid var(--ink); }
      .credit { flex: 0 0 auto; font-size: 1.9cqmin; opacity: .65; padding: .5cqmin 2.8cqmin .9cqmin; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-style: italic; }

      /* the sheet: the page fills what is left between the rule and the foot */
      .sh { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; padding: 0 1cqmin; }
      .sh-page { position: relative; aspect-ratio: var(--r); max-width: 100%; max-height: 100%; width: min(100%, calc((100cqh - 12cqmin) * var(--r))); container-type: size; }
      .sh-page > img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: fill; display: block; }
      .sh-l { position: absolute; transform: translate(-50%, 12%); white-space: nowrap; text-align: center; line-height: 1.15; font-family: var(--sc); font-size: calc(var(--lf) * 100cqh); letter-spacing: .04em; color: var(--ink); }
      .sh-ln { display: block; }
      .sh-sci { font-family: var(--serif); font-style: italic; letter-spacing: 0; }
      .sh-sm { font-family: var(--serif); font-style: italic; letter-spacing: 0; font-size: .88em; opacity: .85; }
      .sh-tag { background: var(--paper); box-shadow: 0 0 0 1px var(--ink); padding: .18em .55em .22em; transform: translate(-50%, 18%); }
      .sh-num { font-family: var(--serif); font-size: calc(var(--lf) * 160cqh); }
      .lv-mono .sh-l { -webkit-text-stroke: .3px currentColor; }

      /* the numbered key beside the page */
      .cl { flex: 1; min-height: 0; display: flex; gap: 2.4cqmin; padding: 0 2.8cqmin; }
      .cl-pic { flex: 1 1 0; min-width: 0; min-height: 0; display: flex; align-items: center; justify-content: center; }
      .cl-pic .sh-page { width: min(100%, calc((100cqh - 12cqmin) * var(--r))); }
      .cl-key { flex: 0 0 30%; min-width: 0; margin: 0; padding: 1cqmin 0 0; list-style: none; display: flex; flex-direction: column; gap: .5cqmin; overflow: hidden; font-size: calc(2.7cqmin * var(--ts)); }
      .cl-key li { display: flex; align-items: baseline; gap: 1.2cqmin; border-bottom: 1px solid color-mix(in srgb, var(--ink) 25%, transparent); padding-bottom: .3cqmin; }
      .cl-key em { font-style: normal; min-width: 2.6cqmin; }
      .cl-key span { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--sc); }
      .cl-key span i { display: block; font-family: var(--serif); font-style: italic; font-size: .8em; opacity: .8; }
      .cl-key b { font-weight: 400; }
      @container (max-aspect-ratio: 1) { .cl { flex-direction: column; } .cl-key { flex: 0 0 auto; max-height: 36%; } }

      /* no canvas: a grid of what there is */
      .mont { flex: 1; min-height: 0; display: grid; gap: 1.6cqmin; padding: .6cqmin 2.8cqmin 1cqmin; grid-template-columns: repeat(4, 1fr); grid-auto-rows: 1fr; }
      .mont.n1 { grid-template-columns: 1fr; } .mont.n2 { grid-template-columns: repeat(2, 1fr); } .mont.n3 { grid-template-columns: repeat(3, 1fr); }
      .mont figure { margin: 0; min-height: 0; display: flex; flex-direction: column; align-items: center; }
      .mont img { flex: 1; min-height: 0; width: 100%; object-fit: contain; mix-blend-mode: multiply; }
      .mont .nopic { flex: 1; width: 60%; border: 1px dashed var(--ink); border-radius: 50%; opacity: .5; }
      .mont figcaption { font-family: var(--sc); font-size: 2.2cqmin; letter-spacing: .04em; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }

      /* messages and the asleep page */
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

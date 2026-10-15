/* The specimen sheet, built in the render browser: each specimen's picture
   is cut out (a plate or painting on plain paper by spreading in from the
   edges; a picture that arrived with alpha by its alpha; a photo as a soft
   vignette, never a rectangle), the cut-outs are packed onto one page in an
   oval, biggest in the middle, and the page comes back as one JPEG with
   each specimen's box and label anchor as fractions of the page.

   A specimen with no picture yet is packed as an empty mount of the size it
   will take, so nothing jumps when its picture lands.

   Step 1 of the v2 plan: the packer is the v1 oval packer with the other
   arrangements removed; mirrored pairing, the balance score and the label
   pass come in step 3. */

const CELL = 6;

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('picture would not decode'));
    img.src = src;
  });
}

function canvas(w, h) {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(w));
  c.height = Math.max(1, Math.round(h));
  return c;
}

function median(values) {
  const a = Float64Array.from(values).sort();
  return a.length ? a[a.length >> 1] : 0;
}

/* A box blur of radius r on a float grid, run twice to come near a Gaussian. */
function boxBlur(src, w, h, r, passes = 1) {
  let a = Float32Array.from(src);
  const b = new Float32Array(w * h);
  for (let p = 0; p < passes; p++) {
    for (let y = 0; y < h; y++) {
      let sum = 0, n = 0;
      for (let x = -r; x <= r; x++) { const xx = Math.min(w - 1, Math.max(0, x)); sum += a[y * w + xx]; n++; }
      for (let x = 0; x < w; x++) {
        b[y * w + x] = sum / n;
        const out = x - r, inn = x + r + 1;
        sum += a[y * w + Math.min(w - 1, inn)] - a[y * w + Math.max(0, out)];
      }
    }
    for (let x = 0; x < w; x++) {
      let sum = 0, n = 0;
      for (let y = -r; y <= r; y++) { const yy = Math.min(h - 1, Math.max(0, y)); sum += b[yy * w + x]; n++; }
      for (let y = 0; y < h; y++) {
        a[y * w + x] = sum / n;
        const out = y - r, inn = y + r + 1;
        sum += b[Math.min(h - 1, inn) * w + x] - b[Math.max(0, out) * w + x];
      }
    }
  }
  return a;
}

function dilate(m, w, h, r) {
  const out = new Uint8Array(w * h);
  const row = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let v = 0;
    for (let k = -r; k <= r && !v; k++) { const xx = x + k; if (xx >= 0 && xx < w && m[y * w + xx]) v = 1; }
    row[y * w + x] = v;
  }
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let v = 0;
    for (let k = -r; k <= r && !v; k++) { const yy = y + k; if (yy >= 0 && yy < h && row[yy * w + x]) v = 1; }
    out[y * w + x] = v;
  }
  return out;
}
function erode(m, w, h, r) {
  const inv = new Uint8Array(w * h);
  for (let i = 0; i < inv.length; i++) inv[i] = m[i] ? 0 : 1;
  const d = dilate(inv, w, h, r);
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const edge = x < r || y < r || x >= w - r || y >= h - r;
    out[y * w + x] = d[y * w + x] || edge ? 0 : 1;
  }
  return out;
}

/* The paper, found by spreading in from the edges over anything that looks
   like paper; what the spread cannot reach is the specimen. */
function paperMask(data, sw, sh, paper) {
  const n = sw * sh;
  const L = new Float32Array(n), diff = new Uint8Array(n), sat = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const r = data[i * 4], g = data[i * 4 + 1], b = data[i * 4 + 2];
    L[i] = 0.299 * r + 0.587 * g + 0.114 * b;
    diff[i] = Math.max(Math.abs(r - paper[0]), Math.abs(g - paper[1]), Math.abs(b - paper[2]));
    sat[i] = Math.max(r, g, b) - Math.min(r, g, b);
  }
  const blur = boxBlur(L, sw, sh, 2, 2);
  const hp = new Float32Array(n);
  for (let i = 0; i < n; i++) hp[i] = Math.min(255, Math.abs(L[i] - blur[i]) * 8);
  const texture = boxBlur(hp, sw, sh, 2, 1);
  const paperL = 0.299 * paper[0] + 0.587 * paper[1] + 0.114 * paper[2];
  const paperlike = new Uint8Array(n), washlike = new Uint8Array(n), cand = new Uint8Array(n), edge = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    paperlike[i] = diff[i] < 24 ? 1 : 0;
    washlike[i] = texture[i] < 24 && sat[i] < 26 && L[i] > paperL - 95 ? 1 : 0;
    cand[i] = paperlike[i] | washlike[i];
    edge[i] = sat[i] < 26 && L[i] > paperL - 95 ? 1 : 0;
  }
  const bg = new Uint8Array(n);
  const stack = [];
  const seed = (i) => { if (cand[i] && !bg[i]) { bg[i] = 1; stack.push(i); } };
  for (let x = 0; x < sw; x++) { seed(x); seed((sh - 1) * sw + x); }
  for (let y = 0; y < sh; y++) { seed(y * sw); seed(y * sw + sw - 1); }
  while (stack.length) {
    const i = stack.pop();
    const x = i % sw, y = (i - x) / sw;
    if (x > 0) seed(i - 1);
    if (x < sw - 1) seed(i + 1);
    if (y > 0) seed(i - sw);
    if (y < sh - 1) seed(i + sw);
  }
  let reach = new Uint8Array(n);
  for (let i = 0; i < n; i++) reach[i] = bg[i] && washlike[i] && !paperlike[i] ? 1 : 0;
  for (let k = 0; k < 8; k++) {
    const grown = dilate(reach, sw, sh, 1);
    for (let i = 0; i < n; i++) grown[i] = grown[i] && (edge[i] || bg[i]) ? 1 : 0;
    reach = grown;
  }
  let obj = new Uint8Array(n);
  for (let i = 0; i < n; i++) obj[i] = bg[i] || reach[i] ? 0 : 1;
  obj = dilate(erode(obj, sw, sh, 1), sw, sh, 2);
  return obj;
}

function bbox(m, w, h) {
  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (m[y * w + x]) {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  return x1 < 0 ? null : [x0, y0, x1 + 1, y1 + 1];
}

/* A picture on plain paper, cut out: the specimen cropped with the paper
   turned white; its hard mask; the paper colour. Printed by multiply. */
export function cutout(img) {
  const k0 = Math.min(1, 1000 / Math.max(img.naturalWidth, img.naturalHeight));
  const cw = Math.max(1, Math.round(img.naturalWidth * k0)), ch = Math.max(1, Math.round(img.naturalHeight * k0));
  const full = canvas(cw, ch);
  const fctx = full.getContext('2d', { willReadFrequently: true });
  fctx.drawImage(img, 0, 0, cw, ch);
  const px = fctx.getImageData(0, 0, cw, ch).data;
  const b = Math.max(2, Math.floor(Math.min(cw, ch) / 40));
  const samples = [[], [], []];
  const step = Math.max(1, Math.floor((cw * b * 2) / 4000));
  for (let y = 0; y < ch; y++) {
    if (y >= b && y < ch - b) continue;
    for (let x = 0; x < cw; x += step) { const i = (y * cw + x) * 4; samples[0].push(px[i]); samples[1].push(px[i + 1]); samples[2].push(px[i + 2]); }
  }
  const paper = samples.map((s) => Math.round(median(s)));
  const k = Math.min(1, 320 / Math.max(cw, ch));
  const sw = Math.max(1, Math.round(cw * k)), sh = Math.max(1, Math.round(ch * k));
  const small = canvas(sw, sh);
  const sctx = small.getContext('2d', { willReadFrequently: true });
  sctx.drawImage(full, 0, 0, sw, sh);
  const sm = paperMask(sctx.getImageData(0, 0, sw, sh).data, sw, sh, paper);
  let box = bbox(erode(sm, sw, sh, 3), sw, sh) || bbox(sm, sw, sh);
  if (!box) return null;
  const pad = Math.max(cw, ch) / 60;
  box = [Math.max(0, Math.floor(box[0] / k - pad)), Math.max(0, Math.floor(box[1] / k - pad)), Math.min(cw, Math.ceil(box[2] / k + pad)), Math.min(ch, Math.ceil(box[3] / k + pad))];
  const bw = box[2] - box[0], bh = box[3] - box[1];
  if (bw < 2 || bh < 2) return null;
  const hard = new Uint8Array(bw * bh);
  for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) {
    const sx = Math.min(sw - 1, Math.floor((box[0] + x) * k)), sy = Math.min(sh - 1, Math.floor((box[1] + y) * k));
    hard[y * bw + x] = sm[sy * sw + sx];
  }
  const soft = boxBlur(Float32Array.from(hard), bw, bh, 2, 2);
  const cut = canvas(bw, bh);
  const cctx = cut.getContext('2d');
  const out = cctx.createImageData(bw, bh);
  const lut = paper.map((c) => { const t = new Uint8Array(256); for (let i = 0; i < 256; i++) t[i] = Math.min(255, Math.round(i * 255 / Math.max(1, c))); return t; });
  for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) {
    const i = ((box[1] + y) * cw + box[0] + x) * 4, o = (y * bw + x) * 4;
    const m = Math.min(1, Math.max(0, soft[y * bw + x]));
    out.data[o] = Math.round(lut[0][px[i]] * m + 255 * (1 - m));
    out.data[o + 1] = Math.round(lut[1][px[i + 1]] * m + 255 * (1 - m));
    out.data[o + 2] = Math.round(lut[2][px[i + 2]] * m + 255 * (1 - m));
    out.data[o + 3] = 255;
  }
  cctx.putImageData(out, 0, 0);
  return { cut, mask: { w: bw, h: bh, data: hard }, paper, blend: 'multiply' };
}

/* A picture that arrived already cut out (a PNG with alpha): its alpha is
   the mask and nothing is guessed. Printed by multiply over white. */
export function fromAlpha(img) {
  const k0 = Math.min(1, 1000 / Math.max(img.naturalWidth, img.naturalHeight));
  const cw = Math.max(1, Math.round(img.naturalWidth * k0)), ch = Math.max(1, Math.round(img.naturalHeight * k0));
  const full = canvas(cw, ch);
  const fctx = full.getContext('2d', { willReadFrequently: true });
  fctx.drawImage(img, 0, 0, cw, ch);
  const px = fctx.getImageData(0, 0, cw, ch).data;
  const hardFull = new Uint8Array(cw * ch);
  for (let i = 0; i < cw * ch; i++) hardFull[i] = px[i * 4 + 3] > 127 ? 1 : 0;
  let box = bbox(hardFull, cw, ch);
  if (!box) return null;
  const pad = Math.max(cw, ch) / 60;
  box = [Math.max(0, Math.floor(box[0] - pad)), Math.max(0, Math.floor(box[1] - pad)), Math.min(cw, Math.ceil(box[2] + pad)), Math.min(ch, Math.ceil(box[3] + pad))];
  const bw = box[2] - box[0], bh = box[3] - box[1];
  if (bw < 2 || bh < 2) return null;
  const hard = new Uint8Array(bw * bh);
  const cut = canvas(bw, bh);
  const cctx = cut.getContext('2d');
  const out = cctx.createImageData(bw, bh);
  for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) {
    const i = ((box[1] + y) * cw + box[0] + x) * 4, o = (y * bw + x) * 4;
    const a = px[i + 3] / 255;
    hard[y * bw + x] = a > 0.5 ? 1 : 0;
    out.data[o] = Math.round(px[i] * a + 255 * (1 - a));
    out.data[o + 1] = Math.round(px[i + 1] * a + 255 * (1 - a));
    out.data[o + 2] = Math.round(px[i + 2] * a + 255 * (1 - a));
    out.data[o + 3] = 255;
  }
  cctx.putImageData(out, 0, 0);
  return { cut, mask: { w: bw, h: bh, data: hard }, paper: [255, 255, 255], blend: 'multiply' };
}

/* A photo as a soft oval vignette: an ellipse inscribed in the picture,
   feathered at the edge, so it reads as a mounted print rather than a
   pasted rectangle. Printed as it is, with its alpha. */
export function vignette(img) {
  const k0 = Math.min(1, 900 / Math.max(img.naturalWidth, img.naturalHeight));
  const w = Math.max(2, Math.round(img.naturalWidth * k0)), h = Math.max(2, Math.round(img.naturalHeight * k0));
  const cut = canvas(w, h);
  const ctx = cut.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, w, h);
  const id = ctx.getImageData(0, 0, w, h);
  const hard = new Uint8Array(w * h);
  const cx = w / 2, cy = h / 2, rx = w * 0.47, ry = h * 0.47;
  const feather = Math.max(4, Math.min(w, h) * 0.07);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const dx = (x + 0.5 - cx) / rx, dy = (y + 0.5 - cy) / ry;
    const d = Math.sqrt(dx * dx + dy * dy); // 1 at the ellipse's edge
    // distance to the edge in pixels, roughly, along the shorter radius
    const px = (1 - d) * Math.min(rx, ry);
    const a = Math.min(1, Math.max(0, px / feather));
    id.data[(y * w + x) * 4 + 3] = Math.round(255 * a);
    hard[y * w + x] = a > 0.5 ? 1 : 0;
  }
  ctx.putImageData(id, 0, 0);
  return { cut, mask: { w, h, data: hard }, paper: [255, 255, 255], blend: 'source-over' };
}

/* An empty mount for a specimen with no picture yet: an oval of the size
   it will take, drawn as a dotted outline. */
export function mount(aspect = 0.8) {
  const w = Math.round(400 * aspect), h = 400;
  const hard = new Uint8Array(w * h);
  const cx = w / 2, cy = h / 2, rx = w * 0.48, ry = h * 0.48;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const dx = (x + 0.5 - cx) / rx, dy = (y + 0.5 - cy) / ry;
    hard[y * w + x] = dx * dx + dy * dy <= 1 ? 1 : 0;
  }
  return { cut: null, mask: { w, h, data: hard }, paper: [255, 255, 255], blend: 'mount' };
}

/* A mask as a canvas (white on black), for scaling. */
function maskCanvas(mask) {
  const c = canvas(mask.w, mask.h);
  const ctx = c.getContext('2d');
  const id = ctx.createImageData(mask.w, mask.h);
  for (let i = 0; i < mask.data.length; i++) { const v = mask.data[i] ? 255 : 0; id.data[i * 4] = v; id.data[i * 4 + 1] = v; id.data[i * 4 + 2] = v; id.data[i * 4 + 3] = 255; }
  ctx.putImageData(id, 0, 0);
  return c;
}

/* Place cut-outs on a W x H page in an oval, biggest first, each with room
   under it for its label, then nudged inward until the group is tight.
   (x, y, scale) per specimen; scale 0 when one does not fit at all. */
export function packSheet(items, W, H, label) {
  const gw = Math.floor(W / CELL), gh = Math.floor(H / CELL);
  const cx = gw / 2, cy = gh * 0.47, rx = gw * 0.48, ry = gh * 0.45;
  const inside = new Uint8Array(gw * gh);
  for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) {
    inside[y * gw + x] = x >= gw * 0.04 && x < gw * 0.96 && y >= gh * 0.06 && y < gh * 0.93 ? 1 : 0;
  }
  const labelRows = Math.max(2, Math.floor((label * 1.5 * H) / CELL));
  const gap = 1;

  const footprint = (b, scale) => {
    const mw = b.mask.w, mh = b.mask.h;
    const tw = Math.max(1, Math.floor((mw * scale) / CELL)), th = Math.max(1, Math.floor((mh * scale) / CELL));
    const c = canvas(tw, th);
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(b.maskCanvas, 0, 0, tw, th);
    const d = ctx.getImageData(0, 0, tw, th).data;
    let body = new Uint8Array(tw * th);
    for (let i = 0; i < tw * th; i++) body[i] = d[i * 4] > 40 ? 1 : 0;
    if (Math.min(tw, th) > 2) body = dilate(body, tw, th, gap);
    const lines = b.lines && b.lines.length ? b.lines : [];
    const rows = lines.length ? labelRows * lines.length : 0;
    const nameW = lines.length ? Math.floor(Math.max(...lines.map((t, i) => t.length * (i === 0 ? 0.74 : 0.5))) * label * H / CELL) + 3 : 0;
    const fw = Math.max(tw, nameW), fh = th + rows + gap;
    const fp = new Uint8Array(fw * fh);
    const off = Math.floor((fw - tw) / 2);
    for (let y = 0; y < th; y++) for (let x = 0; x < tw; x++) fp[y * fw + off + x] = body[y * tw + x];
    if (lines.length) {
      const lx = Math.floor((fw - nameW) / 2);
      for (let y = th + 1; y < th + rows; y++) for (let x = lx; x < lx + nameW; x++) fp[y * fw + x] = 1;
    }
    return { fp, fw, fh, off };
  };

  const overlaps = (occ, x, y, f) => {
    for (let yy = 0; yy < f.fh; yy++) for (let xx = 0; xx < f.fw; xx++) if (f.fp[yy * f.fw + xx] && occ[(y + yy) * gw + x + xx]) return true;
    return false;
  };
  const within = (x, y, f) => {
    for (let yy = 0; yy < f.fh; yy++) for (let xx = 0; xx < f.fw; xx++) if (f.fp[yy * f.fw + xx] && !inside[(y + yy) * gw + x + xx]) return false;
    return true;
  };
  const stamp = (occ, x, y, f, on) => {
    for (let yy = 0; yy < f.fh; yy++) for (let xx = 0; xx < f.fw; xx++) if (f.fp[yy * f.fw + xx]) occ[(y + yy) * gw + x + xx] = on;
  };
  const order = (f) => {
    const cands = [];
    for (let ty = 0; ty < gh - f.fh; ty += 2) for (let tx = 0; tx < gw - f.fw; tx += 2) {
      const d = ((tx + f.fw / 2 - cx) / rx) ** 2 + ((ty + f.fh / 2 - cy) / ry) ** 2;
      cands.push({ tx, ty, d });
    }
    cands.sort((a, b) => a.d - b.d);
    return cands;
  };
  const sgn = (v) => (v > 0 ? 1 : v < 0 ? -1 : 0);
  const pull = (x, y, f) => {
    const bx = x + f.fw / 2, by = y + f.fh / 2;
    return [Math.abs(cx - bx) > 2 ? sgn(cx - bx) : 0, Math.abs(cy - by) > 2 ? sgn(cy - by) : 0];
  };
  const compact = (occ, placed) => {
    for (let round = 0; round < 600; round++) {
      let moved = false;
      for (let i = placed.length - 1; i >= 0; i--) {
        const p = placed[i];
        const [dx, dy] = pull(p.x, p.y, p.f);
        if (!dx && !dy) continue;
        stamp(occ, p.x, p.y, p.f, 0);
        for (const [sx, sy] of [[dx, dy], [dx, 0], [0, dy]]) {
          if (!sx && !sy) continue;
          const nx = p.x + sx, ny = p.y + sy;
          if (nx < 0 || ny < 0 || nx + p.f.fw > gw || ny + p.f.fh > gh) continue;
          if (overlaps(occ, nx, ny, p.f) || !within(nx, ny, p.f)) continue;
          p.x = nx; p.y = ny; moved = true;
          break;
        }
        stamp(occ, p.x, p.y, p.f, 1);
      }
      if (!moved) break;
    }
  };
  const attempt = (unit) => {
    const occ = new Uint8Array(gw * gh);
    const placed = [];
    for (const b of items) {
      const scale = unit * b.factor;
      const f = footprint(b, scale);
      if (f.fh >= gh || f.fw >= gw) return null;
      let spot = null;
      for (const c of order(f)) {
        if (c.d > 2.2) break;
        if (overlaps(occ, c.tx, c.ty, f) || !within(c.tx, c.ty, f)) continue;
        spot = c;
        break;
      }
      if (!spot) return null;
      stamp(occ, spot.tx, spot.ty, f, 1);
      placed.push({ x: spot.tx, y: spot.ty, f, scale });
      compact(occ, placed);
    }
    return placed.map((p) => [(p.x + p.f.off) * CELL, p.y * CELL, p.scale]);
  };

  const area = items.reduce((s, b) => s + b.mask.w * b.mask.h * b.factor ** 2, 0);
  let room = 0;
  for (let i = 0; i < inside.length; i++) room += inside[i];
  room *= CELL * CELL;
  let unit = Math.sqrt((0.5 * room) / Math.max(1, area));
  let best = null;
  for (let i = 0; i < 12 && !best; i++) { best = attempt(unit); if (!best) unit *= 0.88; }
  if (!best) return items.map(() => [0, 0, 0]);
  for (let i = 0; i < 16; i++) {
    const bigger = attempt(unit * 1.05);
    if (!bigger) break;
    unit *= 1.05; best = bigger;
  }
  return best;
}

const PAPERS = { cream: [243, 234, 214], white: [255, 255, 255], aged: [233, 220, 192] };

/* The page. ``specimens``: [{name, sci, src, cut, on_paper, factor, lines}]
   where ``src`` is the picture (or null for a mount), ``cut`` a PNG with
   alpha when one arrived, and ``on_paper`` says the picture is a plate or
   painting on plain paper. Returns null when nothing could be placed. */
export async function buildSheet({ specimens, ratio, size, label, paper = 'cream', ink = '#2a2116' }) {
  const loaded = await Promise.all(specimens.map(async (b) => {
    try {
      let got = null;
      if (b.cut) { got = fromAlpha(await loadImage(b.cut)); if (got && !b.on_paper) got.blend = 'source-over'; }
      else if (b.src && b.on_paper) got = cutout(await loadImage(b.src));
      else if (b.src) got = vignette(await loadImage(b.src));
      if (!got) got = mount(0.8);
      return { ...b, ...got, maskCanvas: maskCanvas(got.mask) };
    } catch { return { ...b, ...mount(0.8), maskCanvas: null }; }
  })).then((all) => all.map((b) => (b.maskCanvas ? b : { ...b, maskCanvas: maskCanvas(b.mask) })));
  if (!loaded.length) return null;
  const W = ratio >= 1 ? size : Math.round(size * ratio), H = ratio >= 1 ? Math.round(size / ratio) : size;
  loaded.sort((a, b) => b.factor ** 2 * b.mask.w * b.mask.h - a.factor ** 2 * a.mask.w * a.mask.h);
  const bg = PAPERS[paper] || PAPERS.cream;
  const sheet = canvas(W, H);
  const ctx = sheet.getContext('2d');
  ctx.fillStyle = `rgb(${bg.join(',')})`;
  ctx.fillRect(0, 0, W, H);
  const spots = packSheet(loaded, W, H, label);
  const shown = [];
  loaded.forEach((b, i) => {
    const [x, y, scale] = spots[i];
    if (scale <= 0) return;
    const w = Math.max(1, Math.round(b.mask.w * scale)), h = Math.max(1, Math.round(b.mask.h * scale));
    if (b.blend === 'mount') {
      ctx.save();
      ctx.strokeStyle = ink;
      ctx.globalAlpha = 0.45;
      ctx.lineWidth = Math.max(1, Math.round(W / 700));
      ctx.setLineDash([ctx.lineWidth * 3, ctx.lineWidth * 3]);
      ctx.beginPath();
      ctx.ellipse(x + w / 2, y + h / 2, w * 0.48, h * 0.48, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    } else {
      ctx.globalCompositeOperation = b.blend;
      ctx.drawImage(b.cut, x, y, w, h);
      ctx.globalCompositeOperation = 'source-over';
    }
    shown.push({ name: b.name, sci: b.sci, mount: b.blend === 'mount', box: [x / W, y / H, w / W, h / H], at: [(x + w / 2) / W, (y + h) / H] });
  });
  if (!shown.length) return null;
  return { src: sheet.toDataURL('image/jpeg', 0.9), items: shown, paper: `rgb(${bg.join(',')})`, ratio, label, w: W, h: H };
}

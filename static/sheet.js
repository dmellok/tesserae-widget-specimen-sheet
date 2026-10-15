/* The specimen sheet, built in the render browser: each specimen's picture
   is cut out (a plate or painting on plain paper by spreading in from the
   edges; a picture that arrived with alpha by its alpha; a photo as a soft
   vignette, never a rectangle), the cut-outs are packed onto one page in an
   oval, biggest in the middle, and the page comes back as one JPEG with
   each specimen's box and label anchor as fractions of the page.

   A specimen with no picture yet is packed as an empty mount of the size it
   will take, so nothing jumps when its picture lands.

   The packer lays the page out balanced: the largest specimen in the
   middle, the rest in pairs of like size mirrored across the page's axis
   and turned to face in, several attempts scored for balance and the best
   kept, then settled inward as pairs. */

const CELL = 6;

/* The packer runs the same in the page and in a worker (client.js starts
   one when it can, so the page stays responsive while the sheet is built);
   in a worker a picture is an ImageBitmap and a canvas an OffscreenCanvas. */
const IN_WORKER = typeof WorkerGlobalScope !== 'undefined' && typeof self !== 'undefined' && self instanceof WorkerGlobalScope;

function loadImage(src) {
  if (IN_WORKER) {
    return fetch(src).then((r) => { if (!r.ok) throw new Error('picture would not load'); return r.blob(); }).then((b) => createImageBitmap(b)).then((bm) => {
      bm.naturalWidth = bm.width; bm.naturalHeight = bm.height; return bm;
    });
  }
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('picture would not decode'));
    img.src = src;
  });
}

function canvas(w, h) {
  const cw = Math.max(1, Math.round(w)), ch = Math.max(1, Math.round(h));
  if (IN_WORKER) return new OffscreenCanvas(cw, ch);
  const c = document.createElement('canvas');
  c.width = cw;
  c.height = ch;
  return c;
}

/* The finished sheet as a JPEG data URL, from either kind of canvas. */
async function toJpeg(c) {
  if (!IN_WORKER) return c.toDataURL('image/jpeg', 0.9);
  const blob = await c.convertToBlob({ type: 'image/jpeg', quality: 0.9 });
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return `data:image/jpeg;base64,${btoa(s)}`;
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
export function cutout(img, model = null) {
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
  const sd = sctx.getImageData(0, 0, sw, sh).data;
  const sm = paperMask(sd, sw, sh, paper);
  // A white bird on white paper is what the spread takes for paper, so a model's mask that
  // passed its check is added: what it kept is never given up, and the edges still add the
  // perch and the foliage it left out.
  if (model) {
    const mc = canvas(sw, sh);
    const mctx = mc.getContext('2d', { willReadFrequently: true });
    mctx.drawImage(model, 0, 0, sw, sh);
    const md = mctx.getImageData(0, 0, sw, sh).data;
    const kept = new Uint8Array(sw * sh);
    for (let i = 0; i < sw * sh; i++) kept[i] = md[i * 4 + 3] > 127 ? 1 : 0;
    const grown = dilate(kept, sw, sh, 1);
    for (let i = 0; i < sw * sh; i++) if (grown[i]) sm[i] = 1;
  }
  dropShadows(sm, sd, sw, sh, paper);
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
  return { cut, mask: { w: bw, h: bh, data: hard }, paper, blend: 'multiply', box, frame: [cw, ch] };
}

/* The soft shadow a painting leaves under its subject: smooth, a neutral darkening of
   the paper (it is not drawn, only cast), and open to the paper at its outer edge. The
   paper spread takes it for part of the specimen, and printed by multiply it shows as a
   grey smudge, so what it reaches is dropped from the mask, a model's included. The
   spread stops at the drawing's own outline (never smooth), so a pale patch the outline
   encloses, a white bird's breast, is kept. */
function dropShadows(sm, sd, sw, sh, paper) {
  const n = sw * sh;
  const L = new Float32Array(n);
  for (let i = 0; i < n; i++) L[i] = 0.299 * sd[i * 4] + 0.587 * sd[i * 4 + 1] + 0.114 * sd[i * 4 + 2];
  const blur = boxBlur(L, sw, sh, 2, 2);
  const hp = new Float32Array(n);
  for (let i = 0; i < n; i++) hp[i] = Math.min(255, Math.abs(L[i] - blur[i]) * 8);
  const texture = boxBlur(hp, sw, sh, 2, 1);
  const cand = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    if (!sm[i]) continue;
    const rc = [sd[i * 4] / paper[0], sd[i * 4 + 1] / paper[1], sd[i * 4 + 2] / paper[2]];
    const mean = (rc[0] + rc[1] + rc[2]) / 3;
    const spread = Math.max(...rc) - Math.min(...rc);
    if (mean > 0.7 && mean < 0.985 && spread < 0.07 && texture[i] < 18) cand[i] = mean < 0.93 ? 2 : 1;
  }
  const drop = new Uint8Array(n);
  const stack = [];
  const open = (i) => {
    const x = i % sw, y = (i - x) / sw;
    return x === 0 || y === 0 || x === sw - 1 || y === sh - 1 || !sm[i - 1] || !sm[i + 1] || !sm[i - sw] || !sm[i + sw];
  };
  // the shadow proper (clearly darker than paper) is followed from the open edge; its faint
  // fringe (barely darker, which a white bird's own plumage also is) only a few steps out
  for (let i = 0; i < n; i++) if (cand[i] === 2 && open(i)) { drop[i] = 1; stack.push(i); }
  while (stack.length) {
    const i = stack.pop();
    const x = i % sw, y = (i - x) / sw;
    const go = (j) => { if (cand[j] === 2 && !drop[j]) { drop[j] = 1; stack.push(j); } };
    if (x > 0) go(i - 1);
    if (x < sw - 1) go(i + 1);
    if (y > 0) go(i - sw);
    if (y < sh - 1) go(i + sw);
  }
  for (let step = 0; step < 5; step++) {
    const next = dilate(drop, sw, sh, 1);
    for (let i = 0; i < n; i++) if (next[i] && !drop[i] && cand[i]) drop[i] = 1;
  }
  for (let i = 0; i < n; i++) if (drop[i]) sm[i] = 0;
}

/* How much of a cut-out is drawing, in pixels of its mask. */
function inkOf(got) { let n = 0; const d = got.mask.data; for (let i = 0; i < d.length; i++) n += d[i]; return n; }

/* Whether a picture on plain paper is to be cut by its edges rather than by
   the model's mask. The model keeps the bird and drops the branch it sits
   on, the foliage round it, the log a fungus grows from, all of which are
   the drawing; the edge cut keeps everything drawn on the paper. So a plate
   or painting is cut by its edges whenever the paper was plain enough to
   find: the cut is well under the whole frame, not just specks, and does
   not fill its own box the way a painted background wash does (a bird on a
   branch fills well under three quarters of its box; a plate with a sky
   behind the bird fills nearly all of it). An aged, textured or vignetted
   plate fails this and keeps the model's mask. */
function perchKept(edge) {
  if (!edge) return false;
  const [fw, fh] = edge.frame;
  const edgeInk = inkOf(edge);
  if (edgeInk >= 0.8 * fw * fh || edgeInk <= 0.01 * fw * fh) return false;
  // the mask closed (blurred and thresholded) so a speckled wash reads as the
  // solid block it is, then its share of the cut's own box
  const { w, h, data } = edge.mask;
  const r = Math.max(2, Math.round(Math.max(w, h) / 80));
  const soft = boxBlur(Float32Array.from(data), w, h, r, 2);
  let filled = 0;
  for (let i = 0; i < soft.length; i++) if (soft[i] > 0.2) filled++;
  return filled < 0.72 * w * h;
}

/* A picture that arrived already cut out (a PNG with alpha): its alpha is
   the mask and nothing is guessed; the alpha travels with the cut, so a
   plate prints by multiply and a photo over the paper. */
export function fromAlpha(img, paper = null) {
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
  // the picture's own paper scaled to white, so a cream wash under a bird does not tint the sheet
  const lut = paper ? paper.map((c) => { const t = new Uint8Array(256); for (let i = 0; i < 256; i++) t[i] = Math.min(255, Math.round(i * 255 / Math.max(1, c))); return t; }) : null;
  for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) {
    const i = ((box[1] + y) * cw + box[0] + x) * 4, o = (y * bw + x) * 4;
    const a = px[i + 3] / 255;
    hard[y * bw + x] = a > 0.5 ? 1 : 0;
    out.data[o] = lut ? lut[0][px[i]] : px[i];
    out.data[o + 1] = lut ? lut[1][px[i + 1]] : px[i + 1];
    out.data[o + 2] = lut ? lut[2][px[i + 2]] : px[i + 2];
    out.data[o + 3] = px[i + 3];
  }
  cctx.putImageData(out, 0, 0);
  return { cut, mask: { w: bw, h: bh, data: hard }, paper: [255, 255, 255], blend: 'multiply', box, frame: [cw, ch] };
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
/* ---------------- the packer ---------------- */

/* Which way a specimen faces, read from its mask: the head of a bird or an
   animal in profile sits in the top rows, off to one side of the body's
   centre. 'left', 'right', or null when the mask has no say (a plant, a
   fungus, a bird seen head-on). */
export function faceOf(mask) {
  const { w, h, data } = mask;
  let sx = 0, n = 0, hx = 0, hn = 0;
  const headRows = Math.max(1, Math.floor(h * 0.3));
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (data[y * w + x]) {
    sx += x; n++;
    if (y < headRows) { hx += x; hn++; }
  }
  if (!n || !hn) return null;
  const d = hx / hn - sx / n;
  if (Math.abs(d) < w * 0.03) return null;
  return d > 0 ? 'right' : 'left';
}

/* A label longer than ``max`` characters is broken at its spaces into lines of about that
   length, so a long name does not claim a strip of paper far wider than its specimen. */
export function wrapLabel(text, max = LABEL_CHARS) {
  const out = [];
  let cur = '';
  for (const w of String(text).split(' ')) {
    if (cur && (cur + ' ' + w).length > max) { out.push(cur); cur = w; } else cur = cur ? cur + ' ' + w : w;
  }
  if (cur) out.push(cur);
  return out.join('\n');
}
const LABEL_CHARS = 20;

function labelStrip(lines, label, H) {
  if (!lines || !lines.length) return { rows: 0, nameW: 0 };
  // the name and its lines, each wrapped; the name's own lines are the wide small capitals
  const segs = lines.flatMap((t, i) => wrapLabel(t).split('\n').map((u) => [u, i === 0 ? 0.62 : 0.44]));
  const rows = Math.max(2, Math.floor((label * 1.5 * H) / CELL)) * segs.length;
  const nameW = Math.floor(Math.max(...segs.map(([u, k]) => u.length * k)) * label * H / CELL) + 3;
  return { rows, nameW };
}

/* A specimen's footprint on the cell grid at ``scale``, mirrored when
   ``flip``: the body (alpha over 40), with a strip under it for the label.
   ``mass`` is the body's cell count, for the balance score. */
/* A mask halved by taking the maximum, again and again: a cell of the footprint asks the level whose
   pixels are no bigger than the cell, so a leg or a tail one pixel wide still sets its cell. */
const PYRAMIDS = new WeakMap();
function pyramidOf(mask) {
  let levels = PYRAMIDS.get(mask.data);
  if (levels) return levels;
  levels = [{ w: mask.w, h: mask.h, data: mask.data }];
  for (;;) {
    const a = levels[levels.length - 1];
    if (a.w <= 1 && a.h <= 1) break;
    const w = Math.ceil(a.w / 2), h = Math.ceil(a.h / 2), data = new Uint8Array(w * h);
    for (let y = 0; y < a.h; y++) for (let x = 0; x < a.w; x++) if (a.data[y * a.w + x]) data[(y >> 1) * w + (x >> 1)] = 1;
    levels.push({ w, h, data });
  }
  PYRAMIDS.set(mask.data, levels);
  return levels;
}

function footprintOf(b, scale, flip, label, H, gap = 0) {
  // a cell is set when any of the mask it covers is, so a footprint never undercounts a thin part
  // and two specimens never share a pixel; the footprint covers the whole drawn width
  const tw = Math.max(1, Math.ceil((b.mask.w * scale) / CELL - 1e-6)), th = Math.max(1, Math.ceil((b.mask.h * scale) / CELL - 1e-6));
  const c = CELL / scale;
  const levels = pyramidOf(b.mask);
  const L = Math.min(levels.length - 1, Math.max(0, Math.floor(Math.log2(Math.max(1, c)))));
  const lv = levels[L], q = 2 ** L;
  let body = new Uint8Array(tw * th);
  let mass = 0;
  for (let y = 0; y < th; y++) {
    const y0 = Math.max(0, Math.floor((y * c) / q)), y1 = Math.min(lv.h, Math.max(y0 + 1, Math.ceil(((y + 1) * c) / q)));
    for (let x = 0; x < tw; x++) {
      let a = x * c, e = (x + 1) * c;
      if (flip) { const t = b.mask.w - e; e = b.mask.w - a; a = t; }
      const x0 = Math.max(0, Math.floor(a / q)), x1 = Math.min(lv.w, Math.max(x0 + 1, Math.ceil(e / q)));
      let hit = 0;
      for (let yy = y0; yy < y1 && !hit; yy++) for (let xx = x0; xx < x1; xx++) if (lv.data[yy * lv.w + xx]) { hit = 1; break; }
      if (hit) { body[y * tw + x] = 1; mass++; }
    }
  }
  // air round each specimen: the body grown by ``gap`` cells on every side, so
  // neighbours keep their distance; ``pad`` is where the picture sits inside it
  const pad = gap && Math.min(tw, th) > 4 ? gap : 0;
  let bw = tw, bh = th;
  const raw = body;
  if (pad) {
    const grown = new Uint8Array((tw + 2 * pad) * (th + 2 * pad));
    for (let y = 0; y < th; y++) for (let x = 0; x < tw; x++) grown[(y + pad) * (tw + 2 * pad) + x + pad] = body[y * tw + x];
    bw = tw + 2 * pad; bh = th + 2 * pad;
    body = dilate(grown, bw, bh, pad);
  }
  const { rows, nameW } = labelStrip(b.lines, label, H);
  const fw = Math.max(bw, nameW);
  const off = Math.floor((fw - bw) / 2);
  const lx = Math.floor((fw - nameW) / 2);
  // the name rises into the air beside a leg or under a perch: it sits just
  // under the lowest ink in its own columns, never above the body's lower half
  let lt = bh + 1;
  if (rows && lt > 0) {
    let low = -1;
    for (let y = bh - 1; y >= 0 && low < 0; y--) for (let x = Math.max(0, lx - off); x < Math.min(bw, lx - off + nameW); x++) if (body[y * bw + x]) { low = y; break; }
    lt = Math.max(Math.floor(bh * 0.55), Math.min(bh + 1, low + 2));
  }
  const fh = Math.max(bh, rows ? lt + rows - 1 : 0);
  // three layers: the body itself, the body with its air, and the label strip. The air of two
  // neighbours may overlap, and a strip may lie in a neighbour's air, so footprints interlock;
  // a body never touches anything, and a strip never crosses a body or another strip.
  const fp = new Uint8Array(fw * fh), fb = new Uint8Array(fw * fh), fa = new Uint8Array(fw * fh), fs = new Uint8Array(fw * fh);
  for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) if (body[y * bw + x]) { fa[y * fw + off + x] = 1; fp[y * fw + off + x] = 1; }
  for (let y = 0; y < th; y++) for (let x = 0; x < tw; x++) if (raw[y * tw + x]) fb[(y + pad) * fw + off + pad + x] = 1;
  if (rows) for (let y = lt; y < lt + rows - 1; y++) for (let x = lx; x < lx + nameW; x++) { fs[y * fw + x] = 1; fp[y * fw + x] = 1; }
  return { fp, fb, fa, fs, fw, fh, off: off + pad, pad, tw, th, mass, flip, lt };
}

/* Place cut-outs on a W x H page, balanced: the largest in the middle, the
   rest in pairs of like size on either side of it, each pair mirrored
   across the page's axis and turned to face in; several attempts, the best
   by a balance score kept. (x, y, scale, flip) per specimen in the order
   given; scale 0 when one did not fit. */
/* A small deterministic generator for the scatter layout's origins. */
function rand(seed) {
  let a = (seed * 2654435761) >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

/* ``layout``: cluster (the largest in the middle, the rest round it in
   mirrored pairs), band (along the middle of the page), column (down it),
   scatter (spread over the whole page from seeded origins, no pairs, no
   settling inward). ``facing`` inward turns a cut-out to face the middle;
   painted leaves every picture as it is. */
export function packSheet(items, W, H, label, gap = 2, fast = false, layout = 'cluster', facing = 'inward') {
  const gw = Math.floor(W / CELL), gh = Math.floor(H / CELL);
  // band and column are loose swathes, two or three deep, not a single file: a file of eight is tiny
  const shapes = { cluster: [0.48, 0.44], band: [0.48, 0.24], column: [0.24, 0.46], scatter: [0.48, 0.44], packed: [0.5, 0.47] };
  const [kx, ky] = shapes[layout] || shapes.cluster;
  const scatter = layout === 'scatter';
  const packed = layout === 'packed';
  const cx = gw / 2, cy = gh * 0.52, rx = gw * kx, ry = gh * ky;
  const inside = new Uint8Array(gw * gh);
  let room = 0;
  for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) {
    const ok = x >= gw * 0.03 && x < gw * 0.97 && y >= gh * 0.05 && y < gh * 0.95;
    inside[y * gw + x] = ok ? 1 : 0;
    room += ok;
  }
  const cap = (0.55 * Math.min(W, H)); // the biggest specimen never dictates the scale

  const grid = () => ({ b: new Uint16Array(gw * gh), a: new Uint16Array(gw * gh), s: new Uint16Array(gw * gh) });
  const overlaps = (occ, x, y, f) => {
    for (let yy = 0; yy < f.fh; yy++) for (let xx = 0; xx < f.fw; xx++) {
      const k = yy * f.fw + xx, g = (y + yy) * gw + x + xx;
      if (f.fb[k] && (occ.a[g] || occ.s[g])) return true;
      if (f.fa[k] && occ.b[g]) return true;
      if (f.fs[k] && (occ.b[g] || occ.s[g])) return true;
    }
    return false;
  };
  const within = (x, y, f) => {
    if (x < 0 || y < 0 || x + f.fw > gw || y + f.fh > gh) return false;
    for (let yy = 0; yy < f.fh; yy++) for (let xx = 0; xx < f.fw; xx++) if (f.fp[yy * f.fw + xx] && !inside[(y + yy) * gw + x + xx]) return false;
    return true;
  };
  const stamp = (occ, x, y, f, on) => {
    const d = on ? 1 : -1;
    for (let yy = 0; yy < f.fh; yy++) for (let xx = 0; xx < f.fw; xx++) {
      const k = yy * f.fw + xx, g = (y + yy) * gw + x + xx;
      if (f.fb[k]) occ.b[g] += d;
      if (f.fa[k]) occ.a[g] += d;
      if (f.fs[k]) occ.s[g] += d;
    }
  };
  const free = (occ, x, y, f) => within(x, y, f) && !overlaps(occ, x, y, f);

  /* candidate top-left spots for a footprint, along an outward spiral on
     the oval from (ox, oy), one turn every 0.05 of the radius; ``side``
     keeps to one half of the page (-1 left, 1 right, 0 either) */
  const spiral = function* (f, ox, oy, start, side, maxR = 1.7) {
    const seen = new Set();
    for (let r = 0; r <= maxR; r += 0.03) {
      const n = Math.max(8, Math.round(r * 72));
      for (let k = 0; k < n; k++) {
        const t = start + (2 * Math.PI * k) / n;
        const x = Math.round((ox + r * rx * Math.cos(t) - f.fw / 2) / 2) * 2;
        const y = Math.round((oy + r * ry * Math.sin(t) - f.fh / 2) / 2) * 2;
        if (side && Math.sign(x + f.fw / 2 - cx) === -side && Math.abs(x + f.fw / 2 - cx) > 1) continue;
        const key = y * gw + x;
        if (seen.has(key)) continue;
        seen.add(key);
        yield [x, y];
      }
    }
  };
  const findSpot = (occ, f, ox, oy, start, side, maxR) => {
    for (const [x, y] of spiral(f, ox, oy, start, side, maxR)) if (free(occ, x, y, f)) return [x, y];
    return null;
  };

  /* one attempt: a start angle for the spiral, and which of each pair goes left */
  const attempt = (unit, start, leftFirst) => {
    const occ = grid();
    const placed = new Array(items.length).fill(null);
    const scaleOf = (b) => Math.min(unit * b.factor, cap / Math.max(b.mask.w, b.mask.h));
    const put = (i, f, x, y) => { stamp(occ, x, y, f, 1); placed[i] = { x, y, f, scale: scaleOf(items[i]) }; };
    const foot = (b, scale, flip) => footprintOf(b, scale, flip, label, H, gap);
    /* the facing a specimen needs on a side: toward the middle */
    const flipFor = (b, side) => {
      if (facing === 'painted' || !b.flippable || !b.face) return false;
      return side < 0 ? b.face === 'left' : b.face === 'right';
    };
    const order = items.map((_b, i) => i); // already largest first
    const groups = [];
    if (scatter || packed) for (const i of order) groups.push([i]);
    else {
      if (order.length) groups.push([order[0]]);
      for (let i = 1; i + 1 < order.length; i += 2) groups.push([order[i], order[i + 1]]);
      if (order.length > 1 && order.length % 2 === 0) groups.push([order[order.length - 1]]);
    }
    let centreTurn = 0;
    const origins = rand(Math.round(start * 1000) + (leftFirst ? 7 : 0));
    for (const g of groups) {
      if (g.length === 1) {
        const i = g[0], b = items[i];
        const f = foot(b, scaleOf(b), false);
        if (f.fh >= gh || f.fw >= gw) return null;
        let spot = null;
        if (scatter) {
          const ox = gw * (0.08 + 0.84 * origins()), oy = gh * (0.1 + 0.8 * origins());
          spot = findSpot(occ, f, ox, oy, start, 0, 3);
        } else if (packed || !placed.some(Boolean)) {
          // each in turn on the free spot nearest the middle, then turned to face it
          spot = findSpot(occ, f, cx, cy, start, 0);
          if (spot && packed && facing !== 'painted' && b.flippable && b.face) {
            const side = spot[0] + f.fw / 2 < cx ? -1 : 1;
            if (flipFor(b, side)) {
              const ff = foot(b, scaleOf(b), true);
              if (free(occ, spot[0], spot[1], ff)) { put(i, ff, spot[0], spot[1]); continue; }
            }
          }
        } else {
          // the leftover goes on the centre line, above or below, whichever balances
          const [a, bb] = (centreTurn++ % 2 === 0) ? [-Math.PI / 2, Math.PI / 2] : [Math.PI / 2, -Math.PI / 2];
          for (const ang of [a, bb]) {
            for (const [x, y] of spiral(f, cx, cy, ang, 0)) {
              if (Math.abs(x + f.fw / 2 - cx) > gw * 0.08) continue;
              if (free(occ, x, y, f)) { spot = [x, y]; break; }
            }
            if (spot) break;
          }
          if (!spot) spot = findSpot(occ, f, cx, cy, start, 0);
        }
        if (!spot) return null;
        put(i, f, spot[0], spot[1]);
        continue;
      }
      const [p, q] = leftFirst ? g : [g[1], g[0]];
      const bp = items[p], bq = items[q];
      const fp = foot(bp, scaleOf(bp), flipFor(bp, -1));
      const fq = foot(bq, scaleOf(bq), flipFor(bq, 1));
      if (fp.fh >= gh || fp.fw >= gw || fq.fh >= gh || fq.fw >= gw) return null;
      const sp = findSpot(occ, fp, cx, cy, start, -1) || findSpot(occ, fp, cx, cy, start, 0);
      if (!sp) return null;
      put(p, fp, sp[0], sp[1]);
      // the mirror of p's spot, then the nearest free spot to it, then anywhere on the right
      const mx = Math.round((2 * cx - (sp[0] + fp.fw / 2) - fq.fw / 2) / 2) * 2;
      const my = Math.round((sp[1] + fp.fh / 2 - fq.fh / 2) / 2) * 2;
      let sq = free(occ, mx, my, fq) ? [mx, my] : findSpot(occ, fq, mx + fq.fw / 2, my + fq.fh / 2, 0, 1, 0.5);
      if (!sq) sq = findSpot(occ, fq, cx, cy, start + Math.PI, 1) || findSpot(occ, fq, cx, cy, start + Math.PI, 0);
      if (!sq) return null;
      put(q, fq, sq[0], sq[1]);
    }
    if (!scatter) settle(occ, placed, groups);
    return placed;
  };

  /* nudge everything inward one cell at a time while it stays clear; a pair
     moves as a pair, mirrored, so the symmetry it was given is kept */
  const settle = (occ, placed, groups) => {
    const sgn = (v) => (v > 0 ? 1 : v < 0 ? -1 : 0);
    const pull = (p) => {
      const bx = p.x + p.f.fw / 2, by = p.y + p.f.fh / 2;
      return [Math.abs(cx - bx) > 2 ? sgn(cx - bx) : 0, Math.abs(cy - by) > 2 ? sgn(cy - by) : 0];
    };
    for (let round = 0; round < 400; round++) {
      let moved = false;
      for (const g of groups) {
        const ps = g.map((i) => placed[i]).filter(Boolean);
        if (!ps.length) continue;
        const [dx, dy] = pull(ps[0]);
        if (!dx && !dy) continue;
        const steps = ps.length === 2 ? [[dx, dy, -dx, dy], [dx, 0, -dx, 0], [0, dy, 0, dy]] : [[dx, dy], [dx, 0], [0, dy]];
        for (const p of ps) stamp(occ, p.x, p.y, p.f, 0);
        let done = false;
        // the members are off the grid while a move is tried, so each one's new spot is put down
        // before the next is checked: a pair closing on the middle must not close on itself
        const clear = (moves) => {
          let ok = true, k = 0;
          for (; k < ps.length && ok; k++) { ok = free(occ, moves[k][0], moves[k][1], ps[k].f); if (ok) stamp(occ, moves[k][0], moves[k][1], ps[k].f, 1); }
          for (let j = 0; j < (ok ? k : k - 1); j++) stamp(occ, moves[j][0], moves[j][1], ps[j].f, 0);
          return ok;
        };
        for (const s of steps) {
          const moves = ps.map((p, k) => [p.x + s[k * 2], p.y + s[k * 2 + 1]]);
          if ((moves.every((_m, k) => s[k * 2] || s[k * 2 + 1]) || (ps.length === 2 && (s[0] || s[1]))) && clear(moves)) {
            // both members may move; a member already on the axis skips its own step
            ps.forEach((p, k) => { p.x = moves[k][0]; p.y = moves[k][1]; });
            done = true;
            break;
          }
        }
        for (const p of ps) stamp(occ, p.x, p.y, p.f, 1);
        if (done) moved = true;
      }
      if (!moved) break;
    }
  };

  /* the packer stops growing once one specimen can no longer be placed by its search, which
     leaves gaps a single bird would fill. So each specimen is then made a little bigger where
     it stands, the smallest first, for as long as its footprint (caption strip and air
     included) stays clear of the others and inside the margins. No specimen grows past twice
     its size, so the sizes still read as they were given. */
  const expand = (placed, relocate = true) => {
    const occ = grid();
    const live = placed.map((p, i) => p && { ...p, i, base: p.scale, stuck: -9 });
    for (const p of live) if (p) stamp(occ, p.x, p.y, p.f, 1);
    const ps = live.filter(Boolean);
    const top = (0.6 * Math.min(W, H));
    /* where a grown specimen may go: on its own spot, nudged a cell, then on rings round it, out
       to a sixth of the page, so one hemmed in where it stands can still grow into a gap near by */
    const R = Math.ceil(Math.min(gw, gh) * 0.16);
    const near = function* (nx, ny) {
      for (const [dx, dy] of [[0, 0], [0, 1], [0, -1], [1, 0], [-1, 0], [1, 1], [-1, 1], [1, -1], [-1, -1]]) yield [nx + dx, ny + dy];
      for (let r = 2; r <= R; r += 2) for (let k = -r; k <= r; k += 2) { yield [nx + k, ny - r]; yield [nx + k, ny + r]; yield [nx - r, ny + k]; yield [nx + r, ny + k]; }
    };
    for (let round = 0; round < 80; round++) {
      let grew = false;
      for (const p of [...ps].sort((a, b) => a.scale - b.scale)) {
        if (round - p.stuck < 4) continue;
        const b = items[p.i];
        const ns = p.scale * 1.05;
        if (ns > p.base * 2.5 || ns * Math.max(b.mask.w, b.mask.h) > top) continue;
        const nf = footprintOf(b, ns, p.f.flip, label, H, gap);
        const bx = p.x + p.f.off + p.f.tw / 2, by = p.y + p.f.pad + p.f.th / 2;
        const nx = Math.round(bx - nf.off - nf.tw / 2), ny = Math.round(by - nf.pad - nf.th / 2);
        stamp(occ, p.x, p.y, p.f, 0);
        let spot = null;
        for (const [x, y] of near(nx, ny)) if (free(occ, x, y, nf)) { spot = [x, y]; break; }
        // hemmed in where it stands: the innermost gap on the page that takes it, as the packer places
        if (!spot && relocate) spot = findSpot(occ, nf, cx, cy, -Math.PI / 2, 0);
        if (spot) { p.x = spot[0]; p.y = spot[1]; p.f = nf; p.scale = ns; grew = true; } else p.stuck = round;
        stamp(occ, p.x, p.y, p.f, 1);
      }
      if (!grew) break;
    }
    return live.map((p) => p && { x: p.x, y: p.y, f: p.f, scale: p.scale });
  };

  /* how balanced a finished attempt is: lower is better */
  const score = (placed) => {
    let M = 0, mx = 0, my = 0;
    const quad = [0, 0, 0, 0];
    let x0 = gw, y0 = gh, x1 = 0, y1 = 0;
    for (const p of placed) {
      if (!p) continue;
      const bx = p.x + p.f.off + p.f.tw / 2, by = p.y + p.f.pad + p.f.th / 2;
      M += p.f.mass; mx += p.f.mass * bx; my += p.f.mass * by;
      quad[(bx < cx ? 0 : 1) + (by < cy ? 0 : 2)] += p.f.mass;
      x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x + p.f.fw); y1 = Math.max(y1, p.y + p.f.fh);
    }
    if (!M) return 1e9;
    const sideways = Math.abs(mx / M - cx) / gw;
    const updown = Math.abs(my / M - cy) / gh;
    const qm = M / 4;
    const quadSpread = Math.sqrt(quad.reduce((s, q) => s + (q - qm) ** 2, 0) / 4) / qm;
    const spread = 1 - ((x1 - x0) * (y1 - y0)) / room; // a group that fills the page scores better
    return 3 * sideways + 2 * updown + 0.6 * quadSpread + 0.5 * spread;
  };

  /* the finished arrangement, scaled up as one about its centre until it
     meets the page's margins: the packer stops growing once a pair can no
     longer be placed by its search, which leaves paper round the group that
     a plain enlargement of the whole group can use. Scaling everything by
     the same factor keeps every clearance and the symmetry; the captions
     keep their own size, so the strips reserved for them only get roomier.
     The largest specimen stays under 0.72 of the short side and nothing is
     blown up past 1.6 times, so pictures keep their detail. */
  const fill = (placed) => {
    const ps = placed.filter(Boolean);
    if (!ps.length) return placed;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of ps) { x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x + p.f.fw); y1 = Math.max(y1, p.y + p.f.fh); }
    const ux0 = gw * 0.03, ux1 = gw * 0.97, uy0 = gh * 0.05, uy1 = gh * 0.95;
    let s = Math.min((ux1 - ux0) / Math.max(1, x1 - x0), (uy1 - uy0) / Math.max(1, y1 - y0), 1.6);
    for (const p of ps) s = Math.min(s, (0.72 * Math.min(W, H)) / Math.max(1, p.f.tw * CELL, p.f.th * CELL));
    if (s <= 1.02) return placed;
    const bcx = (x0 + x1) / 2, bcy = (y0 + y1) / 2, ucx = (ux0 + ux1) / 2, ucy = (uy0 + uy1) / 2;
    return placed.map((p) => p && { ...p, x: ucx + (p.x - bcx) * s, y: ucy + (p.y - bcy) * s, scale: p.scale * s, f: { ...p.f, fw: p.f.fw * s, fh: p.f.fh * s, tw: p.f.tw * s, th: p.f.th * s, off: p.f.off * s, pad: p.f.pad * s, lt: p.f.lt * s } });
  };

  const area = items.reduce((s, b) => s + b.mask.w * b.mask.h * b.factor ** 2, 0);
  let unit = Math.sqrt((0.55 * room * CELL * CELL) / Math.max(1, area));
  // fast: one start, one handedness and no growth, for a placeholder laid out
  // before any picture is decoded; the fill pass still brings it to the margins
  const starts = fast ? [-Math.PI / 2] : [-Math.PI / 2, 0, Math.PI / 2, Math.PI];
  const hands = fast ? [true] : [true, false];
  const tryAll = (u) => {
    let best = null, bestScore = Infinity;
    for (const start of starts) for (const leftFirst of hands) {
      const got = attempt(u, start, leftFirst);
      if (!got) continue;
      const sc = score(got);
      if (sc < bestScore) { best = got; bestScore = sc; }
    }
    return best;
  };
  let best = null;
  for (let i = 0; i < 24 && !best; i++) { best = tryAll(unit); if (!best) unit *= 0.93; }
  if (!best) return items.map(() => [0, 0, 0, false]);
  // a packed page grows in smaller steps, since each specimen is placed on its own
  const grow = packed ? 1.03 : 1.06;
  for (let i = 0; i < (fast ? 0 : packed ? 30 : 16); i++) {
    const bigger = tryAll(unit * grow);
    if (!bigger) break;
    unit *= grow; best = bigger;
  }
  if (!fast && !scatter) {
    // grow, pull everything in to close the gaps growth opened, grow again
    best = expand(best);
    for (let k = 0; k < 2; k++) {
      const occ = grid();
      for (const p of best) if (p) stamp(occ, p.x, p.y, p.f, 1);
      settle(occ, best, best.map((_p, i) => [i]));
      best = expand(best);
    }
  }
  best = fill(best);
  return best.map((p) => (p ? [(p.x + p.f.off) * CELL, (p.y + p.f.pad) * CELL, p.scale, p.f.flip, (p.y + p.f.lt - p.f.pad - 1) * CELL] : [0, 0, 0, false]));
}

const PAPERS = { cream: [243, 234, 214], white: [255, 255, 255], aged: [233, 220, 192] };

/* The page. ``specimens``: [{name, sci, src, cut, on_paper, paper, neutral, factor, lines}]
   where ``src`` is the picture (or null for a mount), ``cut`` a PNG with
   alpha when one arrived, ``on_paper`` says the picture is a plate or
   painting on plain paper, ``paper`` its paper colour as [r, g, b] and
   ``neutral`` asks for that paper scaled to white. Returns null when
   nothing could be placed. */
/* The grid layout: a cell each, like a drawer of specimens, in the order
   the rows came (the Order option), each fitted under its label strip. */
function gridSpots(items, W, H, label, gap) {
  const n = items.length;
  const cols = Math.max(1, Math.min(n, Math.round(Math.sqrt((n * W) / H)) || 1));
  const rows = Math.ceil(n / cols);
  const mx = W * 0.03, my = H * 0.04;
  const cw = (W - 2 * mx) / cols, ch = (H - 2 * my) / rows;
  const pad = Math.max(gap * CELL, W / 100);
  return items.map((b, i) => {
    const strip = label * H * Math.max(1, (b.lines || []).length) * 1.3 + pad;
    const aw = Math.max(4, cw - 2 * pad), ah = Math.max(4, ch - strip - pad);
    const scale = Math.max(0.001, Math.min(aw / b.mask.w, ah / b.mask.h));
    const w = b.mask.w * scale, h = b.mask.h * scale;
    return [mx + (i % cols) * cw + (cw - w) / 2, my + Math.floor(i / cols) * ch + pad + (ah - h) / 2, scale, false];
  });
}

/* A picture kept whole: a tile, a card, a framed portrait, an illuminated
   page, a window; its rectangle is the mask and it prints over the paper. */
export function whole(img) {
  const k0 = Math.min(1, 1000 / Math.max(img.naturalWidth, img.naturalHeight));
  const w = Math.max(1, Math.round(img.naturalWidth * k0)), h = Math.max(1, Math.round(img.naturalHeight * k0));
  const c = canvas(w, h);
  c.getContext('2d').drawImage(img, 0, 0, w, h);
  return { cut: c, mask: { w, h, data: new Uint8Array(w * h).fill(1) }, paper: [255, 255, 255], blend: 'source-over', box: [0, 0, w, h], frame: [w, h] };
}

function drawMount(ctx, x, y, w, h, ink, W) {
  ctx.save();
  ctx.strokeStyle = ink;
  ctx.globalAlpha = 0.45;
  ctx.lineWidth = Math.max(1, Math.round(W / 700));
  ctx.setLineDash([ctx.lineWidth * 3, ctx.lineWidth * 3]);
  ctx.beginPath();
  ctx.ellipse(x + w / 2, y + h / 2, w * 0.48, h * 0.48, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

const sheetSize = (ratio, size) => (ratio >= 1 ? [size, Math.round(size / ratio)] : [Math.round(size * ratio), size]);
/* Specimens packed into the middle k of the page: the layout runs on a sheet k times the size with
   the caption size scaled to match, then moves to the centre, so the arrangement is the same and
   the margin is wider. */
function layoutSpots(items, W, H, label, gap, fast, layout, facing, zoom) {
  const k = Math.max(0.3, Math.min(1, Number(zoom) || 1));
  const w = W * k, h = H * k, l = label / k;
  const spots = layout === 'grid' ? gridSpots(items, w, h, l, gap) : packSheet(items, w, h, l, gap, fast, layout, facing);
  if (k === 1) return spots;
  const dx = (W - w) / 2, dy = (H - h) / 2;
  return spots.map((s) => (s[2] > 0 ? [s[0] + dx, s[1] + dy, s[2], s[3], s[4] === undefined ? undefined : s[4] + dy] : s));
}

/* The placeholder page, laid out at once from nothing but each specimen's
   aspect and factor: every specimen as an empty mount of about the size it
   will take, packed the quick way, drawn on a canvas the page shows while
   the real sheet is built. ``slots`` gives each specimen's box on that
   canvas (by its index in ``specimens``), so a cut-out can be drawn into
   its mount as soon as it is ready. Synchronous, a few milliseconds. */
export function quickSheet({ specimens, ratio, size, label, paper = 'cream', ink = '#2a2116', spacing = 'airy', layout = 'cluster', facing = 'inward', zoom = 1 }) {
  const items = specimens.map((b, idx) => { const m = mount(Number(b.aspect) > 0 ? Number(b.aspect) : 0.8); return { ...b, ...m, idx, maskCanvas: maskCanvas(m.mask), flippable: false, face: null }; });
  if (!items.length) return null;
  const [W, H] = sheetSize(ratio, size);
  if (layout !== 'grid') items.sort((a, b) => b.factor ** 2 * b.mask.w * b.mask.h - a.factor ** 2 * a.mask.w * a.mask.h);
  const bg = PAPERS[paper] || PAPERS.cream;
  const page = canvas(W, H);
  const ctx = page.getContext('2d');
  ctx.fillStyle = `rgb(${bg.join(',')})`;
  ctx.fillRect(0, 0, W, H);
  const gapQ = { close: 0, airy: 2, open: 4 }[spacing] ?? 2;
  const spots = layoutSpots(items, W, H, label, gapQ, true, layout, facing, zoom);
  const shown = [];
  const slots = [];
  items.forEach((b, i) => {
    const [x, y, scale] = spots[i];
    if (scale <= 0) return;
    const w = Math.max(1, Math.round(b.mask.w * scale)), h = Math.max(1, Math.round(b.mask.h * scale));
    drawMount(ctx, x, y, w, h, ink, W);
    slots[b.idx] = { x, y, w, h, on_paper: !!b.on_paper };
    shown.push({ name: b.name, sci: b.sci, mount: true, flip: false, box: [x / W, y / H, w / W, h / H], at: [(x + w / 2) / W, (y + h) / H] });
  });
  if (!shown.length) return null;
  return { canvas: page, slots, paper: `rgb(${bg.join(',')})`, items: shown, ratio, label, w: W, h: H, quick: true };
}

/* A cut-out drawn into its placeholder mount: fitted inside the mount's
   box, the dashed oval painted over with paper first, printed by multiply
   on paper or over it for a photo. ``pic`` is a canvas or an ImageBitmap. */
export function drawIntoMount(quick, i, pic, blend) {
  const slot = quick.slots[i];
  if (!slot || !quick.canvas) return;
  const ctx = quick.canvas.getContext('2d');
  const pw = pic.width || pic.naturalWidth, ph = pic.height || pic.naturalHeight;
  if (!pw || !ph) return;
  const k = Math.min(slot.w / pw, slot.h / ph);
  const w = pw * k, h = ph * k, x = slot.x + (slot.w - w) / 2, y = slot.y + (slot.h - h) / 2;
  ctx.save();
  ctx.fillStyle = quick.paper;
  ctx.fillRect(slot.x - 2, slot.y - 2, slot.w + 4, slot.h + 4);
  ctx.globalCompositeOperation = blend === 'multiply' ? 'multiply' : 'source-over';
  ctx.drawImage(pic, x, y, w, h);
  ctx.restore();
}

export async function buildSheet({ specimens, ratio, size, label, paper = 'cream', ink = '#2a2116', spacing = 'airy', layout = 'cluster', facing = 'inward', zoom = 1, onCut = null }) {
  const loaded = await Promise.all(specimens.map(async (b, idx) => {
    try {
      let got = null, byEdges = false, model = null;
      if (b.whole && b.src) got = whole(await loadImage(b.src));
      else if (b.cut) {
        const cutImg = await loadImage(b.cut);
        got = fromAlpha(cutImg, b.neutral && b.on_paper ? b.paper : null);
        if (got && !b.on_paper && !b.classic) got.blend = 'source-over';
        if (!b.classic) model = cutImg;
      }
      if (!b.whole && b.src && b.on_paper) {
        // a plate or painting on plain paper is cut by its edges when that keeps
        // the perch, the foliage, the log a fungus grows from (perchKept)
        const edge = cutout(await loadImage(b.src), model);
        if (perchKept(edge)) { got = edge; byEdges = true; }
      }
      if (!got && b.src) got = vignette(await loadImage(b.src));
      if (!got) got = mount(0.8);
      if (onCut && got.blend !== 'mount') { try { await onCut(idx, got.cut, got.blend); } catch { /* the page may have moved on */ } }
      // a plate cut by its edges may carry lettering, so it is never mirrored
      const flippable = got.blend !== 'mount' && !byEdges && !b.whole && (!!b.cut || !b.on_paper);
      return { ...b, ...got, maskCanvas: maskCanvas(got.mask), flippable, face: flippable ? faceOf(got.mask) : null };
    } catch { return { ...b, ...mount(0.8), maskCanvas: null, flippable: false, face: null }; }
  })).then((all) => all.map((b) => (b.maskCanvas ? b : { ...b, maskCanvas: maskCanvas(b.mask) })));
  if (!loaded.length) return null;
  const [W, H] = sheetSize(ratio, size);
  if (layout !== 'grid') loaded.sort((a, b) => b.factor ** 2 * b.mask.w * b.mask.h - a.factor ** 2 * a.mask.w * a.mask.h);
  const bg = PAPERS[paper] || PAPERS.cream;
  const sheet = canvas(W, H);
  const ctx = sheet.getContext('2d');
  ctx.fillStyle = `rgb(${bg.join(',')})`;
  ctx.fillRect(0, 0, W, H);
  const gapN = { close: 0, airy: 2, open: 4 }[spacing] ?? 2;
  const spots = layoutSpots(loaded, W, H, label, gapN, false, layout, facing, zoom);
  const shown = [];
  loaded.forEach((b, i) => {
    const [x, y, scale, flip, labelY] = spots[i];
    if (scale <= 0) return;
    const w = Math.max(1, Math.round(b.mask.w * scale)), h = Math.max(1, Math.round(b.mask.h * scale));
    if (b.blend === 'mount') {
      drawMount(ctx, x, y, w, h, ink, W);
    } else {
      ctx.save();
      ctx.globalCompositeOperation = b.blend;
      if (flip) { ctx.translate(x + w, y); ctx.scale(-1, 1); ctx.drawImage(b.cut, 0, 0, w, h); }
      else ctx.drawImage(b.cut, x, y, w, h);
      ctx.restore();
    }
    shown.push({ name: b.name, sci: b.sci, mount: b.blend === 'mount', flip: !!flip, box: [x / W, y / H, w / W, h / H], at: [(x + w / 2) / W, (labelY !== undefined ? Math.min(labelY, y + h + 2 * CELL) : y + h) / H] });
  });
  if (!shown.length) return null;
  return { src: await toJpeg(sheet), items: shown, paper: `rgb(${bg.join(',')})`, ratio, label, w: W, h: H };
}

/* As a worker: one message in with buildSheet's argument, one out with its
   result (or the error), then the page ends the worker. */
if (IN_WORKER) {
  self.onmessage = async (e) => {
    const onCut = async (i, cut, blend) => {
      const bitmap = await createImageBitmap(cut);
      self.postMessage({ kind: 'cut', i, bitmap, blend }, [bitmap]);
    };
    try { self.postMessage({ ok: true, sheet: await buildSheet({ ...e.data, onCut }) }); }
    catch (err) { self.postMessage({ ok: false, error: String(err && err.message || err) }); }
  };
}

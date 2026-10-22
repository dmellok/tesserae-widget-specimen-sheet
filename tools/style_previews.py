"""Paint the style picker's sample pictures: one great tit, from one openly
licensed Wikimedia Commons photograph, in every style, through fal.ai, as
small JPEGs in static/styles/ that the cell's Style option points at
(``choices[].image``). About US$0.04 a style.

    FAL_KEY=... python3 tools/style_previews.py            # the missing ones
    python3 tools/style_previews.py --key-file ~/fal_key --only gould,sumie --force

Each painting is asked for the way a cluster cell asks for one: the payload
server.py's _paint_payload builds (photo reference, default model, no custom
words, no native perch, not dapper, not the hero), its key from paint.key_for
and its prompt from paint.prompt_for, in the cell's 3:4. The reference and its
credit are in REFERENCE below and in ATTRIBUTION.md; the full-size paintings
go to tools/out/styles/ (not shipped). Needs a Tesserae checkout beside this
repo (or TESSERAE_REPO) for app.fal."""

from __future__ import annotations

import argparse
import importlib.util
import io
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TESSERAE = Path(os.environ.get("TESSERAE_REPO") or ROOT.parent / "tesserae")
if str(_TESSERAE) not in sys.path:
    sys.path.insert(0, str(_TESSERAE))
from app import fal  # noqa: E402

OUT = ROOT / "static" / "styles"
FULL = ROOT / "tools" / "out" / "styles"
THUMB = (320, 240)
LARGE = (800, 600)  # the picker's enlarged view, in static/styles/large/
ASPECT = "3:4"  # a cell's painting
REFERENCE = {
    "name": "Great Tit",
    "sci": "Parus major",
    "kind": "Aves",
    "title": "File:Great tit side-on.jpg",
    "src": "https://upload.wikimedia.org/wikipedia/commons/e/ef/Great_tit_side-on.jpg",
    "by": "Francis C. Franklin",
    "lic": "CC BY-SA 3.0",
    "link": "https://commons.wikimedia.org/wiki/File:Great_tit_side-on.jpg",
}
UA = "TesseraeStylePreviews/1.0 (https://github.com/dmellok/tesserae-widget-specimen-sheet)"


def _sibling(name: str):
    # under the name the widget's modules cache each other by, as server.py loads them
    full = f"_tesserae_plugins.specimen_sheet.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


def _paint_module():
    # in server.py's order: masks before paint, since each reads the other
    for name in ("net", "kinds", "sources", "masks", "classic"):
        _sibling(name)
    return _sibling("paint")


def _reference() -> bytes:
    cache = FULL / "reference.jpg"
    if cache.exists():
        return cache.read_bytes()
    req = urllib.request.Request(REFERENCE["src"], headers={"User-Agent": UA})
    raw = urllib.request.urlopen(req, timeout=60).read()
    FULL.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(raw)
    return raw


def _small(raw: bytes, edge: int = 1600) -> bytes:
    """The reference cut down to a size the model needs, as a JPEG."""
    from PIL import Image

    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.thumbnail((edge, edge), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


def _strip(edge):
    """A strip of paper at a cut's edge with its specks taken out, to be drawn out."""
    from PIL import ImageFilter

    return edge.filter(ImageFilter.MedianFilter(11)).filter(ImageFilter.BoxBlur(6))


def thumbnail(raw: bytes, size: tuple[int, int] = THUMB) -> bytes:
    from PIL import Image, ImageChops, ImageFilter, ImageOps

    im = Image.open(io.BytesIO(raw)).convert("RGB")
    # close in on the bird: the paper is what the corners show, and the
    # sample is cut to what differs from it, with a generous margin, since a
    # small bird on its perch is compact and a tight cut loses the paper
    w, h = im.size
    corners = [
        im.getpixel(xy) for xy in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3))
    ]
    paper = tuple(sorted(c[i] for c in corners)[1] for i in range(3))
    # the median drops the paper's specks and grain, which would widen the cut to the page
    diff = (
        ImageChops.difference(im, Image.new("RGB", im.size, paper))
        .convert("L")
        .filter(ImageFilter.MedianFilter(7))
    )
    diff = diff.point(lambda v: 255 if v > 32 else 0)
    box = diff.getbbox()
    if box and (box[2] - box[0]) < w * 0.97:
        x0, y0, x1, y1 = box
        bw, bh = x1 - x0, y1 - y0
        # widen to the sample's shape, with a margin of a fifth round it
        tw = max(bw * 1.4, bh * 1.4 * size[0] / size[1])
        th = tw * size[1] / size[0]
        # kept inside the painting where it fits, so as little as can be is filled in
        cx = min(max((x0 + x1) / 2, tw / 2), w - tw / 2) if tw <= w else w / 2
        cy = min(max((y0 + y1) / 2, th / 2), h - th / 2) if th <= h else h / 2
        crop = (int(cx - tw / 2), int(cy - th / 2), int(cx + tw / 2), int(cy + th / 2))
        inner = im.crop(
            (max(0, crop[0]), max(0, crop[1]), min(w, crop[2]), min(h, crop[3]))
        )
        # what is filled in is the paper at the cut's own edges, often whiter than the corners
        iw, ih = inner.size
        edge = [
            inner.getpixel((x, y))
            for x in (1, iw - 2)
            for y in range(0, ih, max(1, ih // 40))
        ]
        edge += [
            inner.getpixel((x, y))
            for y in (1, ih - 2)
            for x in range(0, iw, max(1, iw // 40))
        ]
        fill = tuple(sorted(c[i] for c in edge)[len(edge) // 2] for i in range(3))
        bg = Image.new("RGB", (crop[2] - crop[0], crop[3] - crop[1]), fill)
        ox, oy = max(0, -crop[0]), max(0, -crop[1])
        # the paper's own edge drawn out across what is filled in, so no seam shows
        right, below = bg.width - ox - iw, bg.height - oy - ih
        if ox:
            bg.paste(_strip(inner.crop((0, 0, 12, ih))).resize((ox, ih)), (0, oy))
        if right > 0:
            bg.paste(
                _strip(inner.crop((iw - 12, 0, iw, ih))).resize((right, ih)),
                (ox + iw, oy),
            )
        if oy:
            bg.paste(
                _strip(bg.crop((0, oy, bg.width, oy + 12))).resize((bg.width, oy)),
                (0, 0),
            )
        if below > 0:
            bg.paste(
                _strip(bg.crop((0, oy + ih - 12, bg.width, oy + ih))).resize(
                    (bg.width, below)
                ),
                (0, oy + ih),
            )
        bg.paste(inner, (ox, oy))
        im, paper = bg, fill
    im = (
        ImageOps.pad(im, size, method=Image.LANCZOS, color=paper)
        if im.width / im.height < 1.2
        else ImageOps.fit(im, size, method=Image.LANCZOS)
    )
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80, optimize=True, progressive=True)
    return out.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--key-file", default="")
    ap.add_argument("--only", default="", help="comma-separated style ids")
    ap.add_argument(
        "--force", action="store_true", help="paint again even when the sample exists"
    )
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument(
        "--rethumb",
        action="store_true",
        help="remake the samples from the kept paintings, nothing painted",
    )
    a = ap.parse_args()
    only = [s for s in a.only.split(",") if s]
    if a.rethumb:
        n = 0
        OUT.mkdir(parents=True, exist_ok=True)
        for f in sorted(FULL.glob("*.jpg")):
            if f.stem != "reference" and (not only or f.stem in only):
                (OUT / f.name).write_bytes(thumbnail(f.read_bytes()))
                (OUT / "large").mkdir(exist_ok=True)
                (OUT / "large" / f.name).write_bytes(thumbnail(f.read_bytes(), LARGE))
                n += 1
        print(f"{n} samples made again from the kept paintings")
        return 0
    key = (
        Path(a.key_file).expanduser().read_text(encoding="utf-8").strip()
        if a.key_file
        else ""
    ) or os.environ.get("FAL_KEY", "").strip()
    if not key:
        print("no fal.ai key: --key-file or FAL_KEY", file=sys.stderr)
        return 2
    P = _paint_module()
    styles = [s for s in P.STYLES if not only or s in only]
    todo = [s for s in styles if a.force or not (OUT / f"{s}.jpg").exists()]
    ref = fal.data_uri(_small(_reference()))
    OUT.mkdir(parents=True, exist_ok=True)
    FULL.mkdir(parents=True, exist_ok=True)
    data_dir = FULL / "data"  # where paint.about keeps the species' Wikipedia summary
    data_dir.mkdir(parents=True, exist_ok=True)
    client = fal.FalClient(key)
    credit = f"{REFERENCE['by']}, {REFERENCE['lic']}, {REFERENCE['link']}"

    def one(style: str) -> str:
        # server.py's _paint_payload for a cell's row with its own photo
        job = {
            "style": style,
            "name": REFERENCE["name"],
            "sci": REFERENCE["sci"],
            "kind": REFERENCE["kind"],
            "how": "",
            "model": P.DEFAULT_MODEL,
            "ref_mode": "photo",
            "ref": ref,
            "ref_credit": credit,
            "key": P.key_for(
                REFERENCE["sci"],
                style,
                REFERENCE["kind"],
                False,
                P.DEFAULT_MODEL,
                "mixed",
                "",
                False,
            ),
        }
        refs, used, _credit = P.references(job, data_dir, True)
        label, how = P.style_of(style)
        prompt = P.prompt_for({**job, "how": how, "ref_used": used}, data_dir)
        try:
            raw = client.generate(
                P.DEFAULT_MODEL, prompt, refs=refs, aspect=ASPECT, retries=1
            )
        except Exception as exc:  # noqa: BLE001 - one style failing leaves the rest
            return f"{style}: failed ({exc})"
        (FULL / f"{style}.jpg").write_bytes(raw)
        (OUT / f"{style}.jpg").write_bytes(thumbnail(raw))
        (OUT / "large").mkdir(exist_ok=True)
        (OUT / "large" / f"{style}.jpg").write_bytes(thumbnail(raw, LARGE))
        return f"{style}: ok"

    with ThreadPoolExecutor(max_workers=max(1, a.parallel)) as pool:
        for line in pool.map(one, todo):
            print(line, flush=True)
    print(
        f"{len(todo)} asked, about US${client.spent_cents / 100:.2f} spent", flush=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

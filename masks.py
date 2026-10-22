"""Cut-outs: every picture on the sheet gets an alpha mask from a
background-removal model (fal.ai's BiRefNet), the mask is checked before
it is trusted, and a bad one is thrown away so the page falls back to a
vignette rather than printing a rectangle. A mask is made once per picture
and kept as a cut-out PNG in Tesserae's "cutouts" collection for the widget
(``app.artwork``, 0.463 and later), which queues, budgets and lists them.
Loaded by server.py by path.

The check (plan, section 3.2): the foreground must cover 4 to 85 percent
of the frame, touch at most two of the frame's edges, and have one piece
that clearly outweighs the rest; that piece is kept (a plate's second bird
or its caption is dropped), small holes in it are filled, and the edge is
feathered a pixel."""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    full = f"_tesserae_plugins.specimen_sheet.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, _HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


N = _sibling("net")

try:
    from app import artwork  # Tesserae 0.463+
except ImportError:  # an older Tesserae: no cut-outs are made, the sheet says why
    artwork = None  # type: ignore[assignment]

P = _sibling("paint")

MIN_COVER, MAX_COVER = 0.04, 0.85
MAX_EDGES = 2
MAIN_SHARE = 0.35  # the largest piece must hold this much of the foreground (a plate's second bird is dropped)
HOLE_SHARE = 0.01  # holes smaller than this share of the piece are filled
BAD = "bad: "  # the model answered, the mask was no good: kept as the failure's reason

CUTS: Any = (
    artwork.Collection(
        "specimen_sheet",
        "cutouts",
        make=lambda job: make(job),
        label="Specimen Sheet",
        title="Cutting out pictures",
        budget_setting="mask_daily_usd",
        count_setting="mask_daily_cap",
        parallel_setting="mask_parallel",
        per_preview_setting="mask_per_refresh",
        default_cents=600,
        typical_cents=2,
        parallel=6,
        per_preview=6,
        retry_after_s=24 * 3600,
        parent=P.ART,
        keep_versions=False,
    )
    if artwork is not None
    else P._NoArtwork("cutouts")
)


def folder(data_dir: Path) -> Path:
    return CUTS.folder(Path(data_dir))


def adopt_old_masks(data_dir: Path) -> bool:
    """Before 0.9.0 the cut-outs lived in masks/ as ``<key>.cut.png`` (``g_``
    before a painting's key) with ``.bad`` and ``.miss`` markers; they become
    the collection's, under the painting's own key. Once."""
    data_dir = Path(data_dir)
    old = data_dir / "masks"
    new = CUTS.folder(data_dir)
    if new.exists() or not old.is_dir():
        return False
    new.mkdir(parents=True, exist_ok=True)
    import json
    import os
    import time

    with contextlib.suppress(OSError):
        for f in old.iterdir():
            name = f.name
            key = name[2:] if name.startswith("g_") else name
            if name.endswith(".cut.png"):
                os.replace(f, new / (key[: -len(".cut.png")] + ".png"))
            elif name.endswith(".bad"):
                got = N.read_json(f, {})
                why = (
                    str(got.get("why") or "no good")
                    if isinstance(got, dict)
                    else "no good"
                )
                N.write(
                    new / (key[: -len(".bad")] + ".err"),
                    json.dumps({"why": BAD + why, "at": int(time.time())}).encode(),
                )
            elif (
                name.endswith(".cut_gray16.png")
                or name.endswith(".cut_gray4.png")
                or name.endswith(".cut_mono.png")
            ):
                pass  # converted copies: made again as needed
    with contextlib.suppress(OSError):
        for f in old.iterdir():
            f.unlink()
        old.rmdir()
    return True


# ----------------------------------------------------------------------
# The check
# ----------------------------------------------------------------------


def _components(fg: Any) -> list[tuple[int, Any]]:
    """Connected pieces of a boolean grid as (size, mask), largest first.
    Pure numpy, on a grid small enough that the Python loop is cheap."""
    import numpy as np

    h, w = fg.shape
    seen = np.zeros_like(fg, dtype=bool)
    out = []
    ys, xs = np.nonzero(fg)
    for y0, x0 in zip(ys.tolist(), xs.tolist(), strict=True):
        if seen[y0, x0]:
            continue
        piece = np.zeros_like(fg, dtype=bool)
        stack = [(y0, x0)]
        seen[y0, x0] = True
        n = 0
        while stack:
            y, x = stack.pop()
            piece[y, x] = True
            n += 1
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and fg[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        out.append((n, piece))
    out.sort(key=lambda p: -p[0])
    return out


def check(alpha: Any) -> tuple[Any | None, str]:
    """(cleaned alpha as a PIL L image at the input's size, '') when the mask
    is good, else (None, why). ``alpha`` is a PIL L image."""
    import numpy as np
    from PIL import Image, ImageFilter

    w, h = alpha.size
    k = min(1.0, 256 / max(w, h))
    sw, sh = max(1, int(w * k)), max(1, int(h * k))
    small = np.asarray(alpha.resize((sw, sh), Image.Resampling.BILINEAR)) > 127
    cover = float(small.mean())
    if cover < MIN_COVER:
        return None, f"foreground covers {cover:.0%} of the frame"
    if cover > MAX_COVER:
        return (
            None,
            f"foreground covers {cover:.0%} of the frame (the background was kept)",
        )
    edges = 0
    for strip, length in (
        (small[0, :], sw),
        (small[-1, :], sw),
        (small[:, 0], sh),
        (small[:, -1], sh),
    ):
        if strip.sum() > 0.05 * length:
            edges += 1
    if edges > MAX_EDGES:
        return None, f"foreground touches {edges} edges of the frame"
    pieces = _components(small)
    if not pieces:
        return None, "no foreground"
    total = sum(n for n, _p in pieces)
    main_n, main = pieces[0]
    if main_n < MAIN_SHARE * total:
        return (
            None,
            f"the largest of {len(pieces)} pieces holds only {main_n / total:.0%} of the foreground",
        )
    if min(main.any(axis=0).sum(), main.any(axis=1).sum()) < 20 * k:
        return None, "the piece is too small"
    # fill holes: pieces of background inside the main piece, small ones only
    holes = _components(~main)
    for n, hole in holes:
        touches = (
            hole[0, :].any()
            or hole[-1, :].any()
            or hole[:, 0].any()
            or hole[:, -1].any()
        )
        if not touches and n < HOLE_SHARE * main_n:
            main |= hole
    # back to full size: keep the model's soft edge where it agrees with the kept piece
    keep = Image.fromarray((main * 255).astype(np.uint8)).resize(
        (w, h), Image.Resampling.BILINEAR
    )
    keep = keep.filter(ImageFilter.MaxFilter(3))
    # a filled hole is solid inside the piece; the model's own soft edge is
    # kept round the outline, where the eroded piece does not reach
    inner = keep.filter(ImageFilter.MinFilter(7))
    filled = np.maximum(np.asarray(alpha), np.asarray(inner))
    cleaned = Image.fromarray(np.minimum(filled, np.asarray(keep)).astype(np.uint8))
    return cleaned.filter(ImageFilter.GaussianBlur(0.7)), ""


def paper_cut(picture: bytes) -> Any:
    """A picture on plain paper cut by its paper colour alone, as a PIL RGBA
    image: the paper is the median of the edges, alpha grows with distance
    from it, specks are dropped and pinholes closed. For a painting whose
    model mask failed the check (a tiny subject, say); never for a photo."""
    import numpy as np
    from PIL import Image, ImageFilter

    im = Image.open(io.BytesIO(picture)).convert("RGB")
    a = np.asarray(im).astype(np.int16)
    h, w = a.shape[:2]
    b = max(2, min(w, h) // 40)
    edges = np.concatenate(
        [
            a[:b].reshape(-1, 3),
            a[-b:].reshape(-1, 3),
            a[:, :b].reshape(-1, 3),
            a[:, -b:].reshape(-1, 3),
        ]
    )
    paper = np.median(edges, axis=0)
    diff = np.abs(a - paper).max(axis=2)
    alpha = Image.fromarray(np.clip((diff - 10) * 6, 0, 255).astype(np.uint8))
    alpha = (
        alpha.filter(ImageFilter.MinFilter(3))
        .filter(ImageFilter.MaxFilter(5))
        .filter(ImageFilter.GaussianBlur(0.7))
    )
    out = im.convert("RGBA")
    out.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 64 else 0).getbbox()
    if box:
        pad = max(w, h) // 60
        out = out.crop(
            (
                max(0, box[0] - pad),
                max(0, box[1] - pad),
                min(w, box[2] + pad),
                min(h, box[3] + pad),
            )
        )
    return out


def crop_box(size: tuple[int, int], alpha: Any) -> tuple[int, int, int, int]:
    """Where the cut-out is cropped from its picture: the specimen in ``alpha``
    (a PIL L image, the picture's size) with a little room, in pixels."""
    from PIL import ImageFilter

    w, h = size
    box = (
        alpha.point(lambda v: 255 if v > 64 else 0)
        .filter(ImageFilter.MinFilter(3))
        .getbbox()
        or alpha.getbbox()
    )
    if not box:
        return (0, 0, w, h)
    pad = max(w, h) // 60
    return (
        max(0, box[0] - pad),
        max(0, box[1] - pad),
        min(w, box[2] + pad),
        min(h, box[3] + pad),
    )


def box_fractions(box: tuple[int, int, int, int], size: tuple[int, int]) -> list[float]:
    w, h = size
    return [
        round(box[0] / w, 5),
        round(box[1] / h, 5),
        round(box[2] / w, 5),
        round(box[3] / h, 5),
    ]


def cut_png(picture: bytes, alpha: Any) -> bytes:
    """The picture with ``alpha`` (a PIL L image, the picture's size) as its
    alpha, cropped to the specimen with a little room (``crop_box``), as PNG
    bytes."""
    from PIL import Image

    im = Image.open(io.BytesIO(picture)).convert("RGB")
    if alpha.size != im.size:
        alpha = alpha.resize(im.size, Image.Resampling.BILINEAR)
    box = crop_box(im.size, alpha)
    out = im.crop(box).convert("RGBA")
    out.putalpha(alpha.crop(box))
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def find_box(cut: bytes, picture: bytes) -> list[float] | None:
    """Where a kept cut-out sits in its picture, as fractions of the picture
    (x0, y0, x1, y1), for a cut-out kept before its box was: it is a crop of
    the picture at the picture's own size, so its colours are found in the
    picture by correlation (on the grey levels, through the FFT) and the
    match is checked on the colours. None when it is not there."""
    import numpy as np
    from PIL import Image

    try:
        c = Image.open(io.BytesIO(cut)).convert("RGB")
        p = Image.open(io.BytesIO(picture)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    w, h = c.size
    pw, ph = p.size
    if w > pw or h > ph or w < 2 or h < 2:
        return None
    if (w, h) == (pw, ph):
        return [0.0, 0.0, 1.0, 1.0]
    big = np.asarray(p.convert("L"), dtype=np.float64)
    small = np.asarray(c.convert("L"), dtype=np.float64)
    # one offset for both, so the sum of squared differences is exact (and the FFT's
    # numbers stay small)
    mid = float(big.mean())
    big = big - mid
    small = small - mid
    fh, fw = ph + h, pw + w
    corr = np.fft.irfft2(
        np.fft.rfft2(big, (fh, fw)) * np.conj(np.fft.rfft2(small, (fh, fw))),
        (fh, fw),
    )[: ph - h + 1, : pw - w + 1]
    # the sum of squares under each window, from an integral image
    sq = np.pad(big * big, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    win = sq[h:, w:] - sq[:-h, w:] - sq[h:, :-w] + sq[:-h, :-w]
    ssd = win - 2 * corr + float((small * small).sum())
    y, x = np.unravel_index(int(np.argmin(ssd)), ssd.shape)
    a = np.asarray(p, dtype=np.int16)[y : y + h, x : x + w]
    b = np.asarray(c, dtype=np.int16)
    if float(np.abs(a - b).mean()) > 6:
        return None
    return box_fractions((int(x), int(y), int(x) + w, int(y) + h), (pw, ph))


def box_of(key: str, data_dir: Path, source: str = "") -> list[float] | None:
    """The kept cut-out's box in its picture, as fractions (x0, y0, x1, y1):
    kept with the cut-out since 0.13.0, else found once in ``source`` (or the
    picture the cut-out was made from) and kept beside it. None when it
    cannot be told."""
    import json

    data_dir = Path(data_dir)
    meta = CUTS.meta(key, data_dir)
    got = meta.get("box")
    if isinstance(got, list) and len(got) == 4:
        try:
            box = [float(v) for v in got]
        except (TypeError, ValueError):
            box = []
        if len(box) == 4 and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1:
            return box
    if got == "none":
        return None
    path = CUTS.path(key, data_dir)
    payload = meta.get("payload") if isinstance(meta.get("payload"), dict) else {}
    source = source or str(meta.get("source") or payload.get("source") or "")
    if path is None or not source:
        return None
    try:
        box = find_box(path.read_bytes(), Path(source).read_bytes())
    except OSError:
        return None
    with contextlib.suppress(OSError):
        N.write(
            CUTS.folder(data_dir) / f"{key}.json",
            json.dumps({**meta, "box": box or "none"}, ensure_ascii=False).encode(),
        )
    return box


# ----------------------------------------------------------------------
# The kept cut-outs
# ----------------------------------------------------------------------


def state(key: str, data_dir: Path) -> str:
    """'ready', 'bad' (the model answered, the mask was no good), 'failed'
    (recently), 'running' or 'none'."""
    st = CUTS.state(key, data_dir)
    if st == "failed" and CUTS.why_failed(key, data_dir).startswith(BAD):
        return "bad"
    return st


def why_bad(key: str, data_dir: Path) -> str:
    why = CUTS.why_failed(key, data_dir)
    return why[len(BAD) :] if why.startswith(BAD) else why


def cut_url(key: str, data_dir: Path, levels: str = "colour") -> str | None:
    """The kept cut-out as a PNG data URL, converted for the panel."""
    return CUTS.url(key, data_dir, px=4000, levels=levels, cut=True) or None


def configure(settings: dict[str, Any] | None) -> None:
    CUTS.configure(settings)


def per_fetch() -> int:
    return CUTS.per_preview()


def budget_used(data_dir: Path) -> int:
    return CUTS.budget_used(Path(data_dir))


def make(job: Any) -> tuple[bytes, dict[str, Any]]:
    """Ask the model for the picture's mask, check it, keep the cut-out: on a
    worker thread, through the collection's budget. The payload names the
    picture's file (``source``)."""
    source = str(job.payload.get("source") or "")
    try:
        picture = Path(source).read_bytes() if source else b""
    except OSError:
        picture = b""
    if not picture:
        raise P._art_error(
            "the picture to cut out has gone; it is fetched again on the next refresh"
        )
    raw = job.fal.generate(
        "birefnet",
        "",
        refs=["data:image/jpeg;base64," + base64.b64encode(picture).decode()],
    )
    if raw[:2] != b"\x89P":
        raise P._art_error("the cut-out model answered no mask")
    try:
        from PIL import Image

        alpha = Image.open(io.BytesIO(raw)).convert("RGBA").getchannel("A")
    except Exception as exc:  # noqa: BLE001
        raise P._art_error(f"the mask would not decode ({exc})") from exc
    cleaned, why = check(alpha)
    if cleaned is None:
        raise P._art_error(BAD + why)
    size = Image.open(io.BytesIO(picture)).size
    if cleaned.size != size:
        cleaned = cleaned.resize(size, Image.Resampling.BILINEAR)
    box = box_fractions(crop_box(size, cleaned), size)
    return cut_png(picture, cleaned), {"title": job.title, "source": source, "box": box}


def stop(key: str, data_dir: Path) -> bool:
    return CUTS.stop(key, Path(data_dir))


def stop_all(data_dir: Path) -> int:
    return CUTS.stop_all(Path(data_dir))


def running(data_dir: Path) -> list[dict[str, Any]]:
    return [
        {"key": r["key"], "name": r["title"], "seconds": r["seconds"]}
        for r in CUTS.progress(Path(data_dir), None)["running"]
    ]

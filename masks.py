"""Cut-outs: every picture on the sheet gets an alpha mask from a
background-removal model (fal.ai's BiRefNet), the mask is checked before
it is trusted, and a bad one is thrown away so the page falls back to a
vignette rather than printing a rectangle. A mask is made once per picture
in a background thread and kept beside it under data/masks/. Loaded by
server.py by path.

The check (plan, section 3.2): the foreground must cover 4 to 85 percent
of the frame, touch at most two of the frame's edges, and be mostly one
piece; the largest piece is kept, small holes in it are filled, and the
edge is feathered a pixel."""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import sys
import threading
import time
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

FAL_CUTOUT = "https://fal.run/fal-ai/birefnet/v2"
FAL_TIMEOUT_S = 90
PER_FETCH = 6  # masks started per refresh
DAILY_CAP = 300  # masks a day; they cost well under a cent each
RETRY_S = 6 * 3600  # after the model or the network failed
BAD_S = 30 * 86400  # after the model answered but the mask was no good
MIN_COVER, MAX_COVER = 0.04, 0.85
MAX_EDGES = 2
MAIN_SHARE = 0.8  # the largest piece must hold this much of the foreground
HOLE_SHARE = 0.01  # holes smaller than this share of the piece are filled

_RUNNING: set[str] = set()
_LOCK = threading.Lock()


def folder(data_dir: Path) -> Path:
    f = data_dir / "masks"
    f.mkdir(parents=True, exist_ok=True)
    return f


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
            f"the largest piece holds only {main_n / total:.0%} of the foreground",
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


def cut_png(picture: bytes, alpha: Any) -> bytes:
    """The picture with ``alpha`` (a PIL L image, the picture's size) as its
    alpha, cropped to the specimen with a little room, as PNG bytes."""
    from PIL import Image, ImageFilter

    im = Image.open(io.BytesIO(picture)).convert("RGB")
    if alpha.size != im.size:
        alpha = alpha.resize(im.size, Image.Resampling.BILINEAR)
    box = (
        alpha.point(lambda v: 255 if v > 64 else 0)
        .filter(ImageFilter.MinFilter(3))
        .getbbox()
        or alpha.getbbox()
    )
    if not box:
        box = (0, 0, im.width, im.height)
    pad = max(im.size) // 60
    box = (
        max(0, box[0] - pad),
        max(0, box[1] - pad),
        min(im.width, box[2] + pad),
        min(im.height, box[3] + pad),
    )
    out = im.crop(box).convert("RGBA")
    out.putalpha(alpha.crop(box))
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ----------------------------------------------------------------------
# The kept cut-outs
# ----------------------------------------------------------------------


def state(key: str, data_dir: Path) -> str:
    """'ready', 'bad' (the model answered, the mask was no good), 'failed'
    (recently), 'running' or 'none'."""
    f = folder(data_dir)
    with _LOCK:
        if key in _RUNNING:
            return "running"
    if (f / f"{key}.cut.png").exists():
        return "ready"
    with contextlib.suppress(OSError):
        if time.time() - (f / f"{key}.bad").stat().st_mtime < BAD_S:
            return "bad"
    with contextlib.suppress(OSError):
        if time.time() - (f / f"{key}.miss").stat().st_mtime < RETRY_S:
            return "failed"
    return "none"


def why_bad(key: str, data_dir: Path) -> str:
    got = N.read_json(folder(data_dir) / f"{key}.bad", {})
    return str(got.get("why") or "") if isinstance(got, dict) else ""


def cut_url(key: str, data_dir: Path, levels: str = "colour") -> str | None:
    """The kept cut-out as a PNG data URL, converted for the panel."""
    f = folder(data_dir)
    path = f / f"{key}.cut.png"
    if levels != "colour":
        conv = f / f"{key}.cut_{levels}.png"
        if not conv.exists() and path.exists():
            with contextlib.suppress(Exception):
                from PIL import Image, ImageOps

                im = Image.open(path).convert("RGBA")
                a = im.getchannel("A")
                cutoff = {"gray16": 0.5, "gray4": 1.0, "mono": 1.0}.get(levels, 1.0)
                g = ImageOps.autocontrast(im.convert("L"), cutoff=cutoff)
                if levels == "mono":
                    g = g.point([round(255 * (i / 255) ** 0.8) for i in range(256)])
                out = g.convert("RGBA")
                out.putalpha(a)
                buf = io.BytesIO()
                out.save(buf, "PNG", optimize=True)
                N.write(conv, buf.getvalue())
        path = conv
    try:
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return None


def budget_used(data_dir: Path) -> int:
    got = N.read_json(folder(data_dir) / "budget.json", {})
    if isinstance(got, dict) and got.get("day") == time.strftime("%Y-%m-%d"):
        return int(got.get("used") or 0)
    return 0


def _spend(data_dir: Path) -> None:
    # several masks are made side by side: the count is read and written under the lock
    with _LOCK:
        N.write(
            folder(data_dir) / "budget.json",
            json.dumps(
                {"day": time.strftime("%Y-%m-%d"), "used": budget_used(data_dir) + 1}
            ).encode(),
        )


def make(key: str, picture: bytes, data_dir: Path, fal_key: str) -> str | None:
    """Ask the model for the picture's mask, check it, keep the cut-out.
    Blocking; run through ``start``. Returns None, or why there is no cut."""
    f = folder(data_dir)
    body = json.dumps(
        {
            "image_url": "data:image/jpeg;base64," + base64.b64encode(picture).decode(),
            "model": "General Use (Light)",
            "operating_resolution": "1024x1024",
            "output_format": "png",
            "refine_foreground": True,
        }
    ).encode()
    _spend(data_dir)
    answer, err = N.get_json(
        FAL_CUTOUT,
        fal_key,
        auth_scheme="Key",
        body=body,
        timeout=FAL_TIMEOUT_S,
        detail=True,
    )
    url = (
        str((answer.get("image") or {}).get("url") or "")
        if isinstance(answer, dict)
        else ""
    )
    raw = None
    if url:
        raw, err = N.http(url, accept="image/*", timeout=60)
    if not raw or raw[:2] != b"\x89P":
        (f / f"{key}.miss").touch()
        return f"the cut-out model answered {err or 'nothing'}"
    try:
        from PIL import Image

        alpha = Image.open(io.BytesIO(raw)).convert("RGBA").getchannel("A")
    except Exception as exc:  # noqa: BLE001
        (f / f"{key}.miss").touch()
        return f"the mask would not decode ({exc})"
    cleaned, why = check(alpha)
    if cleaned is None:
        N.write(
            f / f"{key}.bad", json.dumps({"why": why, "at": int(time.time())}).encode()
        )
        return why
    N.write(f / f"{key}.cut.png", cut_png(picture, cleaned))
    with contextlib.suppress(OSError):
        (f / f"{key}.bad").unlink()
    with contextlib.suppress(OSError):
        (f / f"{key}.miss").unlink()
    return None


def start(key: str, picture: bytes, data_dir: Path, fal_key: str) -> bool:
    """Make the mask in the background; False when one is already under way
    or the day's allowance is used."""
    if not fal_key or budget_used(data_dir) >= DAILY_CAP:
        return False
    with _LOCK:
        if key in _RUNNING:
            return False
        _RUNNING.add(key)

    def run() -> None:
        try:
            make(key, picture, data_dir, fal_key)
        except Exception as exc:  # noqa: BLE001 - a thread must not die loudly
            with contextlib.suppress(OSError):
                (folder(data_dir) / f"{key}.miss").touch()
            N.write(folder(data_dir) / f"{key}.err", str(exc).encode())
        finally:
            with _LOCK:
                _RUNNING.discard(key)

    threading.Thread(target=run, name=f"specimen-mask-{key[:8]}", daemon=True).start()
    return True

"""Classic plates: the cut-out bird plates of Fugleramme by Arne Giacomo
(https://github.com/arnegiacomo/fugleramme), 1200 hand-coloured plates of
706 species cut from their pages, offered under CC BY-SA 4.0. Only the index
(classic.json) is bundled; a plate is fetched from the pinned revision the
first time a species needs it and kept in the widget's data folder. Loaded by
server.py by path."""

from __future__ import annotations

import base64
import hashlib
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
M = _sibling("masks")

RAW = "https://raw.githubusercontent.com/arnegiacomo/fugleramme/{pin}/assets/artwork/classic/birds/{stem}.webp"
LICENCE = "CC BY-SA 4.0"
PLATE_PX = 700
REFERENCE_PX = 768
PAPER = (243, 234, 214)

ARTISTS = {
    "gould": "John Gould",
    "vonwright": "the von Wright brothers",
    "audubon": "J. J. Audubon",
    "dresser": "H. E. Dresser",
    "fuertes": "L. A. Fuertes",
    "keulemans": "J. G. Keulemans",
    "levaillant": "F. Levaillant",
    "bree": "C. R. Bree",
    "siebold": "P. F. von Siebold",
    "swainson": "W. Swainson",
    "cassin": "J. Cassin",
    "naumann": "J. F. Naumann",
    "temminck": "C. J. Temminck",
    "lear": "Edward Lear",
    "sharpe": "R. B. Sharpe",
}

_INDEX: dict[str, Any] = {}


def index() -> dict[str, Any]:
    if not _INDEX:
        data = N.read_json(_HERE / "classic.json", {})
        _INDEX.update(data if isinstance(data, dict) else {})
    return _INDEX


def species_count() -> int:
    plates = index().get("plates")
    return len(plates) if isinstance(plates, dict) else 0


def lookup(sci: str) -> dict[str, Any] | None:
    """The plate for a species: one of its variants, the same one every time."""
    plates = index().get("plates")
    if not isinstance(plates, dict):
        return None
    words = str(sci or "").lower().split()
    for name in (" ".join(words), " ".join(words[:2])):
        variants = plates.get(name)
        if variants:
            row = variants[
                int(hashlib.sha1(name.encode()).hexdigest(), 16) % len(variants)
            ]
            stem, source, url, box, w, h = row
            return {
                "stem": stem,
                "source": source,
                "url": url,
                "box": box,
                "cut": [w, h],
                "pin": index().get("pin", ""),
            }
    return None


def artist_of(entry: dict[str, Any]) -> str:
    return ARTISTS.get(entry["source"], str(entry["source"]).title())


def credit_of(entry: dict[str, Any]) -> str:
    return f"a plate by {artist_of(entry)}, via Fugleramme · {LICENCE}"


def credit_for(artists: list[str]) -> str:
    """One line for the foot of a sheet drawn from several plates."""
    names = (
        ", ".join(artists[:-1]) + " and " + artists[-1]
        if len(artists) > 1
        else artists[0]
    )
    return f"Classic plates after {names}, cut by Arne Giacomo (Fugleramme), {LICENCE}"


def _fetch(entry: dict[str, Any], data_dir: Path) -> Any:
    """The plate as an RGBA image, or None."""
    raw = N.cached_get(data_dir, RAW.format(pin=entry["pin"], stem=entry["stem"]))
    if not raw:
        return None
    try:
        from PIL import Image

        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:  # noqa: BLE001 - an undecodable plate is just skipped
        return None


def _over(im: Any, colour: tuple[int, int, int], longest: int) -> bytes:
    from PIL import Image

    im = im.copy()
    im.thumbnail((longest, longest), Image.Resampling.LANCZOS)
    flat = Image.new("RGB", im.size, colour)
    flat.paste(im, mask=im.getchannel("A"))
    out = io.BytesIO()
    flat.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def plate(sci: str, data_dir: Path, levels: str) -> dict[str, Any] | None:
    """The sheet's picture of a species: the plate's own cut-out kept as its
    mask (so no cut-out job is ever needed) and a flat copy on paper."""
    entry = lookup(sci)
    if not entry:
        return None
    key = f"c_{hashlib.sha1(entry['stem'].encode()).hexdigest()[:20]}"
    cut_path = M.folder(data_dir) / f"{key}.cut.png"
    colour = data_dir / "img" / f"{key}.jpg"
    if cut_path.exists() and colour.exists():
        try:
            raw = colour.read_bytes()
        except OSError:
            return None
    else:
        im = _fetch(entry, data_dir)
        if im is None:
            return None
        from PIL import Image

        small = im.copy()
        small.thumbnail((PLATE_PX, PLATE_PX), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        small.save(out, "PNG", optimize=True)
        raw = _over(im, PAPER, PLATE_PX)
        N.write(cut_path, out.getvalue())
        N.write(colour, raw)
    src = N.render(raw, PLATE_PX, None, colour, levels)
    if not src:
        return None
    x0, y0, x1, y1 = entry["box"]
    w, h = entry["cut"]
    bird = max((x1 - x0) * w, (y1 - y0) * h) / max(w, h)
    return {
        "src": src,
        "kind": "plate",
        "on_paper": False,
        "credit": credit_of(entry),
        "key": key,
        "aspect": N.aspect_of(src),
        "file": str(colour),
        "bird": round(bird, 3),
        "classic": True,
        "artist": artist_of(entry),
    }


def reference(sci: str, data_dir: Path) -> tuple[str, str] | None:
    """The plate on white as a data URL, for the painter, and its credit."""
    entry = lookup(sci)
    if not entry:
        return None
    im = _fetch(entry, data_dir)
    if im is None:
        return None
    raw = _over(im, (255, 255, 255), REFERENCE_PX)
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode(), credit_of(entry)

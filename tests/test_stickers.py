"""The bird sticker index and the files cut from a sticker, with no network."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def _load():
    name = "_tesserae_plugins.specimen_sheet.stickers"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "stickers.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _sticker_bytes() -> bytes:
    im = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    for x in range(100, 300):
        for y in range(80, 220):
            im.putpixel((x, y), (200, 60, 40, 255))
    out = io.BytesIO()
    im.save(out, "WEBP", lossless=True)
    return out.getvalue()


def test_index_covers_every_living_species():
    st = _load()
    assert st.species_count() == 11064
    hit = st.lookup("Pica pica")
    assert hit and hit["family"] == "Passeriformes/Corvidae" and hit["stem"] == "Pica_pica"
    assert hit["url"].startswith(
        "https://raw.githubusercontent.com/alchaincyf/huashu-bird-sticker/"
    )
    assert len(hit["pin"]) == 40, "pinned to a revision, not a branch"
    assert st.lookup("Pica pica pica") == hit, "a subspecies falls back to the species"
    assert st.lookup("Vulpes vulpes") is None


def test_files_keep_the_alpha_and_measure_the_bird(tmp_path):
    st = _load()
    calls: list[str] = []

    def get(data_dir: Path, url: str) -> bytes | None:
        calls.append(url)
        return _sticker_bytes()

    got = st.files("Parus major", tmp_path, get, 350, (243, 234, 214))
    assert got and calls and calls[0].endswith("/Passeriformes/Paridae/Parus_major.webp")
    flat, cut, bird = got
    page = Image.open(io.BytesIO(flat)).convert("RGB")
    corner = page.getpixel((2, 2))
    assert all(abs(a - b) <= 12 for a, b in zip(corner, (243, 234, 214), strict=True)), (
        "the flat copy sits on paper"
    )
    png = Image.open(io.BytesIO(cut))
    assert png.mode == "RGBA" and png.getpixel((1, 1))[3] == 0
    assert 0.8 < bird <= 1.0, "trimmed to the sticker, so the bird fills most of it"
    ref = st.reference("Parus major", tmp_path, get)
    assert ref and ref[0].startswith("data:image/jpeg;base64,") and "huashu-bird-sticker" in ref[1]


def test_manifest_offers_stickers_as_plates_and_as_a_reference():
    m = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    by = {o["name"]: o for o in m["cell_options"]}
    assert [c["value"] for c in by["plates"]["choices"]] == [
        "classic",
        "classic_stickers",
        "stickers",
    ]
    assert by["plates"]["default"] == "classic" and by["plates"]["show_if"] == {"pictures": "free"}
    assert [c["value"] for c in by["reference"]["choices"]] == ["photo", "sticker", "sticker_photo"]
    assert by["reference"]["show_if"] == {"pictures": "gen"}

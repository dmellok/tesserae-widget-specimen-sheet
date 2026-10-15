"""AI paintings: each species painted once per style from a reference photo
of it, through fal.ai's nano-banana edit model, on plain white paper so
the cut-out is clean; kept under data/gen/ with its credit, earlier
versions archived on a repaint, a daily cap on spend, a few new paintings
per refresh in background threads, and one dapper specimen a day in a
small hat. Loaded by server.py by path."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import sys
import threading
import time
import urllib.parse
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
K = _sibling("kinds")
S = _sibling("sources")

FAL_EDIT = "https://fal.run/fal-ai/nano-banana/edit"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
ABOUT_MAX = 400
GEN_TIMEOUT_S = 120
GEN_DAILY_CAP = 12
GEN_CAPS = (6, 12, 24, 48, 96)
GEN_RETRY_S = 6 * 3600
PER_FETCH = 3  # new paintings started per refresh
BATCH_PARALLEL = 3
PROMPT_VERSION = 1

NOTE_NO_KEY = "AI paintings need a fal.ai key in this widget's settings."
NOTE_GEN_CAP = "AI paintings: to-day's limit of {cap} is used up; more to-morrow."

# The styles a specimen may be painted in. {kind} is the subject's noun; the
# words are the Backyard Birds widget's, with "plain white paper" in place of
# cream so the cut-out is clean. Groups for the paintings page's pickers.
STYLES: dict[str, tuple[str, str]] = {
    "gould": (
        "Hand-coloured lithograph (Gould)",
        "a plate from a nineteenth-century natural history book: a hand-coloured lithograph in the manner of John Gould, soft watercolour washes over fine engraved linework, on plain pure white paper",
    ),
    "audubon": (
        "Watercolour after Audubon",
        "a life-size watercolour in the manner of John James Audubon's Birds of America: a lively natural pose, watercolour and pastel with fine detail in every mark, on plain pure white paper",
    ),
    "engraving": (
        "Black-ink engraving",
        "a black-ink copperplate engraving from a Victorian encyclopaedia of natural history: fine cross-hatching and stippling, no colour at all, on plain pure white paper",
    ),
    "woodblock": (
        "Japanese woodblock print",
        "a Japanese kacho-e woodblock print in the manner of Ohara Koson: flat areas of colour with a fine dark keyline and the texture of washi paper, the {kind} alone on plain pure white paper with no background wash",
    ),
    "linocut": (
        "Linocut",
        "a bold linocut print in black ink with one muted colour: strong carved lines, simple shapes, a little texture from the block, on plain pure white paper",
    ),
    "harper": (
        "Mid-century modern (Harper)",
        "a mid-century modern illustration in the manner of Charley Harper: the {kind} reduced to circles, triangles and clean geometric shapes, flat colour in a handful of tones, no outlines, true to the species' markings, on plain pure white paper",
    ),
    "watercolour": (
        "Modern watercolour",
        "a loose, airy modern watercolour with soft wet edges, a few precise details and plenty of white space, on plain pure white paper",
    ),
    "sketch": (
        "Field sketch",
        "a naturalist's field sketch in graphite with a light watercolour wash: loose construction lines, a few annotations of shape only, on plain pure white paper",
    ),
    "pixel": (
        "Pixel art",
        "16-bit pixel art sprite of the {kind} with a limited palette, crisp square pixels and no anti-aliasing, true to the species' colours, on plain pure white paper",
    ),
    "origami": (
        "Origami",
        "a flat, minimal origami illustration of the {kind}: folded from bright paper in bold flat planes of the species' own colours, every facet a clean flat shape with crisp straight creases drawn as fine lines, no shading, no gradients and no studio light, graphic and poster-like, on plain pure white paper",
    ),
    "hats": (
        "Hats and monocles",
        "a realistic hand-coloured lithograph in the manner of John Gould on plain pure white paper, except that the {kind} wears a small hat that suits its personality, and perhaps a monocle, painted with the same care",
    ),
}
STYLE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Plates and prints",
        (
            "gould",
            "audubon",
            "engraving",
            "woodblock",
            "linocut",
            "harper",
            "watercolour",
            "sketch",
        ),
    ),
    ("Playful", ("pixel", "origami", "hats")),
)
STYLE_IDS = (*STYLES, "custom")
CUSTOM_EXAMPLE = (
    "an Art Nouveau illustration with sinuous flowing lines, a muted palette of sage, "
    "ochre and dusty rose, on plain pure white paper"
)
_CUSTOM_RE = re.compile(r"custom[a-z]{8}")

GEN = (
    "Paint only the {kind} in the reference photo, the {name} ({sci}), as {style}. "
    "Keep its colours, markings, shape and proportions exactly as in the photo: {pose}. "
    "The subject alone on plain, pure white paper with a clear margin all round, as a "
    "specimen cut from its surroundings: no shadow under it, no wash, no vignette, no "
    "ground, soil, leaf litter, grass, rocks, wood, bark, logs, water or sky, no other "
    "creatures or plants, no scenery, no text, no lettering, no border, no signature."
    "{about}{dapper}"
)
DAPPER = (
    " The {kind} is dapper: it wears a small, well-made hat that suits it, and perhaps a "
    "monocle, painted with the same care as the rest."
)
# A bird is painted perched or in flight; the sheet mixes them so a page is
# not eight profiles on branches. A third of species fly.
POSES = {
    "perched": "perched on a simple branch, the whole bird and its perch",
    "flight": "in flight with its wings spread, seen from the side a little below, the whole bird",
}

# The scene: the sheet's cut-outs on one collage, repainted by the model as a
# single picture with one background. Opt-in, one painting per re-compose.
SCENES = {
    "auto": "the specimens gathered in an oval cluster, like a Victorian vignette, on branches and foliage that join them",
    "floor": "growing together on a forest floor among leaf litter, moss and a few fallen twigs",
    "log": "growing on and around one fallen, mossy log",
    "bank": "on a shaded woodland bank among ferns, moss and roots",
    "bed": "growing together in one garden bed, with the soil and a few stones",
    "branch": "all perched along one shared branch",
    "tree": "perched through the branches of one tree",
    "birdbath": "gathered round a stone bird bath in a garden",
    "wire": "sitting in a row along a telephone wire against the sky",
    "feeder": "gathered at and around a hanging bird feeder",
    "meadow": "on the ground and low stems of a wildflower meadow",
    "sky": "all in flight together across an open sky",
    "wetland": "among reeds and still water at the edge of a wetland",
    "garden": "in a cottage garden, on a fence, shrubs and a path",
}
FIDELITY = {
    "exact": "Keep every specimen exactly where it is, at the same size and pose, with its own colours and markings unchanged.",
    "free": "Move and resize the specimens as the scene needs, but keep each one's own colours, markings and proportions.",
}
GEN_SCENE = (
    "This picture is a collage of {n} specimens, each painted on its own: {names}. Repaint it "
    "as one seamless picture, {style}, with all of them together in a single natural "
    "scene: {scene}, one continuous background, the same light and paper throughout. "
    "{fidelity} No text, no labels, no numbers, no border, no signature."
)
SCENE_EVERY = ("3", "1", "6", "24")
# What "auto" means for a page that is mostly one kind of thing.
SCENE_AUTO_BY_KIND = {
    "Fungi": "floor",
    "Plantae": "bed",
    "Protozoa": "log",
    "Insecta": "meadow",
    "Arachnida": "meadow",
    "Amphibia": "wetland",
    "Reptilia": "bank",
    "Mammalia": "bank",
}


def scene_for(scene: str, kinds: list[str]) -> str:
    """The scene to paint: the one asked for, or for "auto" the one that
    suits the page's commonest kind (birds keep the vignette)."""
    if scene != "auto" or not kinds:
        return scene
    top = max(set(kinds), key=kinds.count)
    return SCENE_AUTO_BY_KIND.get(top, "auto")


_PAINTING: set[str] = set()
_SINCE: dict[str, tuple[float, str, str]] = {}  # key -> (since, name, style)
_FINISHED: list[tuple[str, float]] = []
_BATCH: set[str] = set()
_LOCK = threading.Lock()
# Custom prompts live in the cell, not the folder: the fetch sets the tag
# for the call so the painting threads it starts know the words.
_CUSTOM: dict[str, str] = {}


# ----------------------------------------------------------------------
# Keys, styles, prompts
# ----------------------------------------------------------------------


def gen_dir(data_dir: Path) -> Path:
    folder = data_dir / "gen"
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def custom_tag(how: str) -> str:
    """A style id for a custom prompt: 'custom' plus eight letters of its hash."""
    h = hashlib.sha1(how.encode()).hexdigest()
    return "custom" + "".join(chr(ord("a") + int(c, 16) % 26) for c in h[:8])


def register_custom(how: str) -> str:
    tag = custom_tag(how)
    with _LOCK:
        _CUSTOM[tag] = how
    return tag


def style_of(style: str | None) -> tuple[str, str]:
    """(label, how) for a style id, a custom tag, or the default."""
    style = str(style or "gould")
    if style in STYLES:
        return STYLES[style]
    if _CUSTOM_RE.fullmatch(style):
        how = _CUSTOM.get(style, "")
        label = (
            "Custom: " + (how[:44].rstrip() + "…" if len(how) > 45 else how)
            if how
            else "Custom style"
        )
        return label, how
    return STYLES["gould"]


def flies(sci: str) -> bool:
    return int(hashlib.sha1(sci.encode()).hexdigest()[:4], 16) % 3 == 0


def key_for(sci: str, style: str, kind: str = "Aves", dapper: bool = False) -> str:
    """The painting's file key: species slug, style, flight for a flying
    bird, dapper for the day's dapper specimen."""
    key = f"{N.slug(sci)}--{style}"
    if kind == "Aves" and flies(sci):
        key += "--flight"
    if dapper:
        key += "--dapper"
    return key


def dapper_pick(scis: list[str], day: str) -> str:
    """Which species is dapper to-day: the same one all day, another to-morrow."""
    if not scis:
        return ""
    ordered = sorted(scis)
    return ordered[
        int(hashlib.sha1(f"{day}|{'|'.join(ordered)}".encode()).hexdigest()[:8], 16)
        % len(ordered)
    ]


def scene_key(style: str, scene: str, fidelity: str) -> str:
    return f"scene--{style}--{scene}--{fidelity}"


def collage(
    keys: list[str], data_dir: Path, ratio: float, size: int = 1400
) -> dict[str, Any] | None:
    """The paintings under ``keys``, their kept cut-outs pasted on one cream
    sheet in an oval, largest first, as the reference the scene is painted
    from: {path, tag, boxes}. Cached on the paintings it is made of; None
    until every painting has its cut-out."""
    from PIL import Image

    folder = gen_dir(data_dir)
    masks = data_dir / "masks"
    M = _sibling("masks")
    stamps = []
    for k in keys:
        # the model's cut-out, or the painting cut by its paper when the model's failed
        try:
            stamps.append(f"{k}:{int((masks / f'g_{k}.cut.png').stat().st_mtime)}")
        except OSError:
            try:
                stamps.append(f"{k}:paper:{int((folder / f'{k}.jpg').stat().st_mtime)}")
            except OSError:
                return None
    if not keys:
        return None
    tag = hashlib.sha1(f"{'|'.join(stamps)}|{ratio:.3f}|{size}".encode()).hexdigest()[
        :16
    ]
    path, info_path = folder / f"collage_{tag}.jpg", folder / f"collage_{tag}.json"
    info = N.read_json(info_path, None)
    if path.exists() and isinstance(info, dict):
        return {"path": str(path), "tag": tag, "boxes": info["boxes"]}
    W, H = (size, int(size / ratio)) if ratio >= 1 else (int(size * ratio), size)
    cuts = []
    for k in keys:
        with contextlib.suppress(OSError):
            if (masks / f"g_{k}.cut.png").exists():
                cuts.append((k, Image.open(masks / f"g_{k}.cut.png").convert("RGBA")))
            else:
                cuts.append((k, M.paper_cut((folder / f"{k}.jpg").read_bytes())))
    if len(cuts) != len(keys):
        return None
    cuts.sort(key=lambda c: -(c[1].width * c[1].height))
    sheet = Image.new("RGB", (W, H), (243, 234, 214))
    n = len(cuts)
    # the largest in the middle, the rest round an oval, each in a slot of the ring
    slots = [(0.5, 0.5)] + [
        (
            0.5 + 0.36 * math.cos(2 * math.pi * i / max(1, n - 1) - math.pi / 2),
            0.5 + 0.34 * math.sin(2 * math.pi * i / max(1, n - 1) - math.pi / 2),
        )
        for i in range(n - 1)
    ]
    big = 0.42 if n > 1 else 0.8
    small = max(0.16, min(0.3, 1.1 / max(1, n)))
    boxes = []
    for i, ((k, im), (sx, sy)) in enumerate(zip(cuts, slots, strict=False)):
        box = (big if i == 0 else small) * min(W, H)
        scale = min(box / im.width, box / im.height)
        fit = im.resize(
            (max(1, int(im.width * scale)), max(1, int(im.height * scale))),
            Image.Resampling.LANCZOS,
        )
        x, y = int(sx * W - fit.width / 2), int(sy * H - fit.height / 2)
        x, y = max(0, min(W - fit.width, x)), max(0, min(H - fit.height, y))
        sheet.paste(fit, (x, y), fit)
        boxes.append(
            {
                "key": k,
                "box": [
                    round(x / W, 4),
                    round(y / H, 4),
                    round(fit.width / W, 4),
                    round(fit.height / H, 4),
                ],
            }
        )
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=88)
    N.write(path, buf.getvalue())
    N.write(info_path, json.dumps({"boxes": boxes}).encode())
    with contextlib.suppress(OSError):
        for old in folder.glob("collage_*"):
            if (
                not old.stem.startswith(f"collage_{tag}")
                and time.time() - old.stat().st_mtime > 86400
            ):
                old.unlink()
    return {"path": str(path), "tag": tag, "boxes": boxes}


def about(sci: str, data_dir: Path) -> str:
    """A sentence or two on what the species looks like, from its English
    Wikipedia summary, kept a month; '' when there is none."""
    if not sci:
        return ""
    folder = data_dir / "img"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"about_{N.slug(sci)}.json"
    with contextlib.suppress(OSError):
        if time.time() - path.stat().st_mtime < 30 * 86400:
            got = N.read_json(path, {})
            return str(got.get("about", "")) if isinstance(got, dict) else ""
    answer, err = N.get_json(
        WIKI_SUMMARY
        + urllib.parse.quote(sci.strip().replace(" ", "_"))
        + "?redirect=true"
    )
    text = ""
    if not err and isinstance(answer, dict):
        text = " ".join(str(answer.get("extract") or "").split())
        if len(text) > ABOUT_MAX:
            cut = text[:ABOUT_MAX]
            text = cut[: cut.rfind(". ") + 1] if ". " in cut else cut
    if not err:
        N.write(path, json.dumps({"about": text}).encode())
    return text


def prompt_for(job: dict[str, Any], data_dir: Path) -> str:
    noun, pose = K.noun_of(job.get("kind"))
    if job.get("kind") == "Aves":
        pose = POSES["flight" if "--flight" in job["key"] else "perched"]
    _label, how = style_of(job.get("style"))
    how = str(job.get("how") or how).replace("{kind}", noun)
    words = about(job.get("sci") or "", data_dir)
    return GEN.format(
        kind=noun,
        name=job.get("name") or job.get("sci") or "",
        sci=job.get("sci") or "",
        style=how,
        pose=pose,
        about=f" About the subject: {words}" if words else "",
        dapper=DAPPER.format(kind=noun) if job["key"].endswith("--dapper") else "",
    )


# ----------------------------------------------------------------------
# The budget and the kept paintings
# ----------------------------------------------------------------------


def budget_used(folder: Path) -> int:
    used = N.read_json(folder / "budget.json", {})
    return int(used.get(time.strftime("%Y-%m-%d"), 0)) if isinstance(used, dict) else 0


def _spend(folder: Path) -> None:
    with _LOCK:
        N.write(
            folder / "budget.json",
            json.dumps({time.strftime("%Y-%m-%d"): budget_used(folder) + 1}).encode(),
        )


def daily_cap(settings: dict[str, Any] | None) -> int:
    with contextlib.suppress(TypeError, ValueError):
        cap = int((settings or {}).get("gen_daily_cap") or GEN_DAILY_CAP)
        if cap in GEN_CAPS:
            return cap
    return GEN_DAILY_CAP


def archive(folder: Path, key: str) -> None:
    """Move a painting (and its credit) into gen/archive/."""
    arch = folder / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for ext in (".jpg", ".json"):
        with contextlib.suppress(OSError):
            os.replace(folder / f"{key}{ext}", arch / f"{key}__{stamp}{ext}")
    drop_derived(folder, key)


def drop_derived(folder: Path, key: str) -> None:
    """Remove the resized copies, paper colours and the cut-out made from a
    painting, after it is replaced or deleted."""
    pattern = re.compile(rf"{re.escape(key)}(_\d+(_\w+)?\.jpg|(_\w+)?\.paper)")
    with contextlib.suppress(OSError):
        for f in folder.iterdir():
            if pattern.fullmatch(f.name):
                with contextlib.suppress(OSError):
                    f.unlink()
    masks = folder.parent / "masks"
    with contextlib.suppress(OSError):
        for f in masks.glob(f"g_{key}.*"):
            f.unlink()


def state(key: str, folder: Path) -> str:
    """'ready', 'running', 'failed' (within GEN_RETRY_S) or 'none'."""
    with _LOCK:
        if key in _PAINTING:
            return "running"
    if (folder / f"{key}.jpg").exists():
        return "ready"
    err = N.read_json(folder / f"{key}.err", None)
    if isinstance(err, dict) and time.time() - float(err.get("at") or 0) < GEN_RETRY_S:
        return "failed"
    return "none"


def why_failed(key: str, folder: Path) -> str:
    err = N.read_json(folder / f"{key}.err", None)
    return str(err.get("why") or "") if isinstance(err, dict) else ""


# ----------------------------------------------------------------------
# Painting
# ----------------------------------------------------------------------


def paint(job: dict[str, Any], data_dir: Path, fal_key: str) -> str | None:
    """Make one painting and keep it under job["key"]. Blocking: run it
    through ``start``. ``job`` is {key, style, name, sci, kind} and may carry
    ``ref`` (an openly licensed photo of the record itself) with
    ``ref_credit``, and ``how`` for a custom style. Returns None, or why it
    failed (also kept in <key>.err)."""
    folder = gen_dir(data_dir)
    key = job["key"]
    err_path = folder / f"{key}.err"

    def fail(why: str) -> str:
        info = {k: job.get(k) for k in ("name", "sci", "style", "kind")}
        N.write(
            err_path, json.dumps({"why": why, "at": int(time.time()), **info}).encode()
        )
        return why

    label, how = style_of(job.get("style"))
    how = str(job.get("how") or how)
    if not how:
        return fail(
            "the custom prompt behind this painting is not known; paint it again from the widget"
        )
    aspect = "3:4"
    ref_credit = ""
    if job.get("collage"):
        # the scene: the sheet's own cut-outs, repainted as one picture
        try:
            sheet = Path(job["collage"]).read_bytes()
        except OSError:
            return fail(
                "the collage to paint from has gone; it is made again on the next refresh"
            )
        image_url = "data:image/jpeg;base64," + base64.b64encode(sheet).decode()
        names = job.get("names") or []
        noun = K.noun_of(job.get("kind"))[0] if job.get("kind") else "specimen"
        prompt = GEN_SCENE.format(
            n=len(names),
            names="; ".join(names),
            style=how.replace("{kind}", noun),
            scene=SCENES.get(job.get("scene") or "auto", SCENES["auto"]),
            fidelity=FIDELITY.get(job.get("fidelity") or "exact", FIDELITY["exact"]),
        )
        aspect = str(job.get("aspect") or "4:3")
    else:
        image_url, ref_credit = (
            str(job.get("ref") or ""),
            str(job.get("ref_credit") or ""),
        )
        if not image_url and job.get("sci"):
            image_url, ref_credit = S.inat_reference(job["sci"], data_dir)
        if not image_url:
            return fail("no openly licensed reference photo of the species")
        prompt = prompt_for({**job, "how": how}, data_dir)
    _spend(folder)
    body = json.dumps(
        {
            "prompt": prompt,
            "image_urls": [image_url],
            "num_images": 1,
            "output_format": "jpeg",
            "aspect_ratio": aspect,
        }
    ).encode()
    answer, err = N.get_json(
        FAL_EDIT,
        fal_key,
        auth_scheme="Key",
        body=body,
        timeout=GEN_TIMEOUT_S,
        detail=True,
    )
    if err:
        if err.startswith(("HTTP 401", "HTTP 403")):
            err = "fal.ai refused the key or the account has no balance (" + err + ")"
        return fail(err if err.startswith("fal") else f"fal.ai answered {err}")
    images = answer.get("images") if isinstance(answer, dict) else None
    out_url = str(images[0].get("url") or "") if images else ""
    raw, err = (
        N.http(out_url, accept="image/*", timeout=60) if out_url else (None, "no image")
    )
    if not raw:
        return fail(f"fal.ai sent no picture ({err})")
    credit = f"AI painting, {label.split(' (')[0].lower()} (fal.ai nano-banana)"
    if job.get("collage"):
        credit = f"AI scene, {label.split(' (')[0].lower()} (fal.ai nano-banana), from each specimen's own painting"
    elif ref_credit:
        credit += f" after {ref_credit}"
    if (folder / f"{key}.jpg").exists():  # a repaint keeps the old one
        archive(folder, key)
    meta = {
        "credit": credit,
        "made": int(time.time()),
        "style": job.get("style") or "gould",
        "name": job.get("name", ""),
        "sci": job.get("sci", ""),
        "kind": job.get("kind", ""),
        "prompt_version": PROMPT_VERSION,
    }
    if job.get("collage"):
        meta["tag"] = job.get("tag", "")
        meta["boxes"] = job.get("boxes") or []
        meta["scene"] = job.get("scene", "auto")
    if _CUSTOM_RE.fullmatch(str(job.get("style") or "")):
        meta["how"] = how
    N.write(folder / f"{key}.json", json.dumps(meta).encode())
    N.write(folder / f"{key}.jpg", raw)
    drop_derived(folder, key)
    with contextlib.suppress(OSError):
        err_path.unlink()
    return None


def start(job: dict[str, Any], data_dir: Path, fal_key: str) -> bool:
    """Paint in the background; False when this key is already being painted."""
    key = job["key"]
    if not job.get("how"):
        job["how"] = style_of(job.get("style"))[1]
    with _LOCK:
        if key in _PAINTING:
            return False
        _PAINTING.add(key)
        _SINCE[key] = (
            time.time(),
            str(job.get("name") or key),
            str(job.get("style") or "gould"),
        )

    def run() -> None:
        try:
            paint(job, data_dir, fal_key)
        except Exception as exc:  # noqa: BLE001 - a thread must not die loudly
            N.write(
                gen_dir(data_dir) / f"{key}.err",
                json.dumps({"why": str(exc)[:200], "at": int(time.time())}).encode(),
            )
        finally:
            with _LOCK:
                _PAINTING.discard(key)
                _SINCE.pop(key, None)
                _FINISHED.append((key, time.time()))
                del _FINISHED[:-20]

    threading.Thread(target=run, name=f"specimen-paint-{key[:16]}", daemon=True).start()
    return True


def start_batch(
    jobs: list[dict[str, Any]], data_dir: Path, fal_key: str, cap: int
) -> bool:
    """Paint ``jobs`` a few at a time from one background thread, stopping at
    the daily cap. False when a batch in this style is already running."""
    if not jobs:
        return False
    tag = str(jobs[0].get("style") or "gould")
    with _LOCK:
        if tag in _BATCH:
            return False
        _BATCH.add(tag)
    folder = gen_dir(data_dir)

    def run() -> None:
        mine: set[str] = set()

        def in_flight() -> int:
            with _LOCK:
                return len(mine & _PAINTING)

        try:
            for job in jobs:
                while in_flight() >= BATCH_PARALLEL:
                    time.sleep(0.5)
                if budget_used(folder) >= cap:
                    break
                if start(job, data_dir, fal_key):
                    mine.add(job["key"])
            while in_flight():
                time.sleep(0.5)
        finally:
            with _LOCK:
                _BATCH.discard(tag)

    threading.Thread(target=run, name=f"specimen-batch-{tag}", daemon=True).start()
    return True


def may_start(key: str, folder: Path, fal_key: str, cap: int) -> tuple[bool, str]:
    """(go ahead, why not): not while running, not within GEN_RETRY_S of a
    failure, not without a key, not past the daily cap."""
    st = state(key, folder)
    if st != "none":
        return False, st
    if not fal_key:
        return False, "no_key"
    if budget_used(folder) >= cap:
        return False, "capped"
    return True, ""


def progress(folder: Path, cap: int, has_key: bool) -> dict[str, Any]:
    """What is painting right now, for the paintings page's live ticker."""
    now = time.time()
    with _LOCK:
        running = [
            {
                "key": k,
                "name": name,
                "style": style_of(style)[0],
                "seconds": int(now - since),
            }
            for k, (since, name, style) in _SINCE.items()
        ]
        batches = [style_of(t)[0] for t in sorted(_BATCH)]
        finished = [{"key": k, "at": int(at)} for k, at in _FINISHED]
    return {
        "running": running,
        "batches": batches,
        "finished": finished,
        "used": budget_used(folder),
        "cap": cap,
        "has_key": has_key,
    }

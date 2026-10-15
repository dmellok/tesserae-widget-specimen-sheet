"""Specimen Sheet: the birds and wildlife around a panel, cut out and laid
out together on one printed page. One widget, three sources (BirdNET-Go,
iNaturalist, GBIF), one page.

The server reduces whichever source is chosen to the same species rows,
drops the excluded ones, gives each the best picture it has to-day (a
classic plate, the record's own photo), sizes it against the others and
writes its label; client.js cuts the pictures out and packs the sheet in
the render browser. Helper modules beside this file are loaded by path."""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import importlib.util
import json
import random
import math
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    full = f"_tesserae_plugins.specimen_sheet.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


N = _sibling("net")
K = _sibling("kinds")
S = _sibling("sources")
M = _sibling("masks")
C = _sibling("classic")
P = _sibling("paint")

COMMONS_FILE = "https://commons.wikimedia.org/wiki/Special:FilePath/"

SOURCES = S.SOURCES
WINDOWS = ("today", "week")
DAYS = {"7d": 7, "14d": 14, "30d": 30, "90d": 90, "365d": 365}
RADII = ("10", "2", "5", "25", "50")
QUALITIES = ("research", "")
SIZINGS = ("compact", "true", "busy", "same")
LABELS = ("caption", "tag", "key", "none")
LABEL_LINES = ("name", "sci", "count", "last", "flag", "who", "size")
TEXT_SCALES = ("100", "60", "80", "125", "150")
PAPERS = ("cream", "white", "aged")
SPACINGS = ("airy", "close", "open")
LAYOUTS = ("cluster", "packed", "scatter", "band", "column", "grid", "hero")
FACINGS = ("inward", "painted")
LEVELS = ("colour", "gray16", "gray4", "mono")
NIGHTS = ("off", "asleep", "owls")
FOOTERS = ("show", "credits", "hide")
NAME_LOCALES = (
    "birdnet",
    "en",
    "de",
    "fr",
    "es",
    "it",
    "nl",
    "pt",
    "sv",
    "da",
    "nb",
    "fi",
    "pl",
    "cs",
    "ja",
    "zh",
    "ru",
)
PICTURE_PX = 700
PICTURES = ("free", "gen")
PLATE_SOURCES = ("classic", "public")
REF_MODES = ("photo", "plate", "both")
ARRANGEMENTS = ("sheet", "scene")
SHEET_MAX = 32
# Which species make the sheet when more were heard or recorded than fit,
# and the order of a numbered key.
ORDERS = ("most", "least", "rarest", "latest", "newest", "alpha", "mix")
RETURN_AFTER_DAYS = 45
QUIET_MIN = 90
ERR_NOWHERE = "Set a place in this cell (or the app's location)."
NOTE_NO_KEY = "A fal.ai key in this widget's settings cuts every specimen out cleanly."
ERR_NOWHERE_INAT = (
    "Set a place in this cell (or the app's location), or an iNaturalist username."
)

# Where the row's text comes from in the demo garden and the rarity log.
_NAMING: set[str] = set()
_LOCK = threading.Lock()


# ----------------------------------------------------------------------
# Options
# ----------------------------------------------------------------------


def _tz(options: dict[str, Any]) -> tzinfo | None:
    for value in (options.get("timezone"), options.get("location")):
        if isinstance(value, dict):
            value = value.get("timezone")
        if isinstance(value, str) and value.strip():
            with contextlib.suppress(ValueError, KeyError, OSError):
                return ZoneInfo(value.strip())
    return None


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(value))))
    except (TypeError, ValueError):
        return default


def _choice(value: Any, allowed: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in allowed else allowed[0]


def _picks(value: Any, allowed: tuple[str, ...] | None = None) -> list[str]:
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value, list):
        return []
    out = [str(v) for v in value if str(v)]
    return [v for v in out if v in allowed] if allowed else out


def _place(options: dict[str, Any]) -> tuple[float, float, str] | None:
    """(lat, lon, name) from the cell's location (or the app's, promoted)."""
    try:
        lat, lon = float(options.get("latitude")), float(options.get("longitude"))
    except (TypeError, ValueError):
        return None
    name = ""
    loc = options.get("location")
    if isinstance(loc, dict):
        name = str(loc.get("label") or loc.get("name") or "")
    elif isinstance(loc, str):
        name = loc
    name = name or str(options.get("label") or "")
    return lat, lon, name.split(",")[0].strip()


# ----------------------------------------------------------------------
# Excluding specimens
# ----------------------------------------------------------------------


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def exclude_rules(*texts: str) -> list[str]:
    """The exclude lists (the cell's and the plugin's) as folded lines."""
    out: list[str] = []
    for text in texts:
        for line in str(text or "").replace(",", "\n").splitlines():
            line = _fold(line.strip().strip("*") and line.strip() or "")
            if line and line not in out:
                out.append(line)
    return out


def is_excluded(row: dict[str, Any], rules: list[str]) -> bool:
    """A rule is a common or scientific name, a genus, a kind word (Insecta,
    insects, Birds), or a glob such as ``*gull``; case and accents folded."""
    if not rules:
        return False
    name, sci = _fold(row.get("name")), _fold(row.get("sci"))
    genus = sci.split(" ")[0] if sci else ""
    fine, group = _fold(row.get("fine")), _fold(row.get("group"))
    kind = str(row.get("kind") or "")
    for r in rules:
        if "*" in r or "?" in r:
            if fnmatch.fnmatchcase(name, r) or fnmatch.fnmatchcase(sci, r):
                return True
            continue
        if r in (name, sci, genus, fine, group) or (fine and r == fine + "s"):
            return True
        if kind in K.GROUP_WORDS.get(r, set()):
            return True
    return False


# ----------------------------------------------------------------------
# The rarity log (BirdNET-Go): new and returning birds
# ----------------------------------------------------------------------


def _days_heard(data_dir: Path) -> dict[str, int]:
    """How many of the logged days each species was heard on, by its key
    in the rarity log (the scientific name, else the common name)."""
    log = N.read_json(data_dir / "rarity.json", {})
    seen = log.get("seen") if isinstance(log, dict) else None
    if not isinstance(seen, dict):
        return {}
    return {k: len(v) for k, v in seen.items() if isinstance(v, list)}


def _order_rows(
    rows: list[dict[str, Any]],
    order: str,
    source: str,
    data_dir: Path,
    today: str,
) -> None:
    """Sort the rows for the Order option, in place. Most and least go by
    the count; rarest by the fewest days heard in the rarity log (BirdNET-Go)
    or the fewest records (the others); latest by the time last heard or the
    date last seen; newest puts the day's new arrivals and returns first;
    alpha A to Z; mix shuffles with the day as the seed, so the pick changes
    daily and holds for the day."""

    def name(r: dict[str, Any]) -> str:
        return str(r.get("name") or "").lower()

    def when(r: dict[str, Any]) -> str:
        return str(r.get("last_iso") or "")

    if order == "least":
        rows.sort(key=lambda r: (r["count"], name(r)))
    elif order == "alpha":
        rows.sort(key=name)
    elif order == "latest" or (order == "newest" and source != "birdnet_go"):
        if source == "birdnet_go":
            rows.sort(
                key=lambda r: (
                    -(_minutes(r.get("last") or "") or 0),
                    -r["count"],
                    name(r),
                )
            )
        else:
            rows.sort(key=lambda r: (-r["count"], name(r)))
            rows.sort(key=when, reverse=True)
    elif order == "newest":
        rank = {"new": 0, "back": 1}
        rows.sort(
            key=lambda r: (rank.get(str(r.get("flag") or ""), 2), -r["count"], name(r))
        )
    elif order == "rarest":
        heard = _days_heard(data_dir) if source == "birdnet_go" else {}
        rows.sort(
            key=lambda r: (
                heard.get(str(r.get("sci") or r.get("name") or ""), 0),
                r["count"],
                name(r),
            )
        )
    elif order == "mix":
        rows.sort(key=name)
        random.Random(f"{today}|{source}").shuffle(rows)
    else:
        rows.sort(key=lambda r: (-r["count"], name(r)))


def _rarity(data_dir: Path, rows: list[dict[str, Any]], today: str) -> None:
    """Flag a bird heard again after a long absence from a log the widget
    keeps of the days each species was heard here."""
    path = data_dir / "rarity.json"
    log = N.read_json(path, {})
    log = log if isinstance(log, dict) else {}
    days = log.get("days") if isinstance(log.get("days"), list) else []
    seen = log.get("seen") if isinstance(log.get("seen"), dict) else {}
    changed = today not in days
    if changed:
        days.append(today)
    for r in rows:
        key = r.get("sci") or r["name"]
        hits = seen.setdefault(key, [])
        before = [d for d in hits if d < today]
        if before and not r.get("flag"):
            with contextlib.suppress(ValueError):
                away = (
                    datetime.strptime(today, "%Y-%m-%d")
                    - datetime.strptime(before[-1], "%Y-%m-%d")
                ).days
                if away >= RETURN_AFTER_DAYS:
                    months = round(away / 30)
                    r["flag"] = "back"
                    r["flag_text"] = (
                        f"back after {months} month{'' if months == 1 else 's'}"
                        if months >= 2
                        else f"back after {away} days"
                    )
        if today not in hits:
            hits.append(today)
            changed = True
    if changed:
        days = sorted(days)[-400:]
        seen = {k: sorted(v)[-400:] for k, v in seen.items()}
        N.write(path, json.dumps({"days": days, "seen": seen}).encode())


# ----------------------------------------------------------------------
# Bird names in another language (BirdNET-Go)
# ----------------------------------------------------------------------


def _names(data_dir: Path, rows: list[dict[str, Any]], locale: str, place: str) -> None:
    """Give each bird its common name in ``locale`` from iNaturalist, looked
    up once per species in a background thread and kept."""
    if locale == "birdnet":
        return
    folder = data_dir / "names"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{N.slug(locale)}_{N.slug(place or 'any')}.json"
    have = N.read_json(path, {})
    have = have if isinstance(have, dict) else {}
    missing = [r["sci"] for r in rows if r.get("sci") and r["sci"] not in have]
    for r in rows:
        got = have.get(r.get("sci") or "")
        if got:
            r["name"] = got
    if not missing:
        return
    job = str(path)
    with _LOCK:
        if job in _NAMING:
            return
        _NAMING.add(job)

    def run() -> None:
        try:
            found = N.read_json(path, {})
            found = found if isinstance(found, dict) else {}
            for sci in missing[:40]:
                q: dict[str, Any] = {
                    "q": sci,
                    "rank": "species",
                    "per_page": 1,
                    "locale": locale,
                }
                if place:
                    q["preferred_place_id"] = place
                data, err = N.get_json(f"{S.INAT_TAXA}?{urllib.parse.urlencode(q)}")
                found_rows = (
                    data.get("results") if isinstance(data, dict) and not err else None
                )
                found[sci] = (
                    str((found_rows[0] or {}).get("preferred_common_name") or "")
                    if found_rows
                    else ""
                )
                time.sleep(0.4)
            N.write(path, json.dumps(found, ensure_ascii=False).encode())
        finally:
            with _LOCK:
                _NAMING.discard(job)

    threading.Thread(target=run, name="specimen-names", daemon=True).start()


# ----------------------------------------------------------------------
# The sky: sunrise, sunset and the moon
# ----------------------------------------------------------------------


def _sun_times(lat: float, lon: float, now: datetime) -> tuple[int, int] | None:
    """(sunrise, sunset) as minutes of the local day, by the usual NOAA
    approximation; None where the sun does not rise or set that day."""
    n = now.timetuple().tm_yday
    offset = (now.utcoffset() or timedelta(0)).total_seconds() / 3600
    out = []
    for rising in (True, False):
        t = n + (((6 if rising else 18) - lon / 15) / 24)
        m = 0.9856 * t - 3.289
        lng = (
            m
            + 1.916 * math.sin(math.radians(m))
            + 0.020 * math.sin(math.radians(2 * m))
            + 282.634
        ) % 360
        ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(lng)))) % 360
        ra += (math.floor(lng / 90) - math.floor(ra / 90)) * 90
        ra /= 15
        sin_dec = 0.39782 * math.sin(math.radians(lng))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = (
            math.cos(math.radians(90.833)) - sin_dec * math.sin(math.radians(lat))
        ) / (cos_dec * math.cos(math.radians(lat)))
        if cos_h > 1 or cos_h < -1:
            return None
        h = (
            360 - math.degrees(math.acos(cos_h))
            if rising
            else math.degrees(math.acos(cos_h))
        ) / 15
        ut = (h + ra - 0.06571 * t - 6.622 - lon / 15) % 24
        out.append(int(round(((ut + offset) % 24) * 60)))
    return out[0], out[1]


def _moon(now: datetime) -> tuple[float, str]:
    epoch = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    phase = (((now - epoch).total_seconds() / 86400) / 29.530588853) % 1
    names = (
        "new moon",
        "waxing crescent",
        "first quarter",
        "waxing gibbous",
        "full moon",
        "waning gibbous",
        "last quarter",
        "waning crescent",
    )
    return round(phase, 3), names[int((phase * 8 + 0.5) % 8)]


def _sky(place: tuple[float, float, str] | None, now: datetime) -> dict[str, Any]:
    if not place:
        return {"dark": False}
    times = _sun_times(place[0], place[1], now)
    phase, moon = _moon(now)
    if times is None:
        return {"dark": False, "moon": phase, "moon_name": moon}
    rise, set_ = times
    mins = now.hour * 60 + now.minute
    return {
        "dark": mins < rise or mins > set_,
        "sunrise": f"{rise // 60:02d}:{rise % 60:02d}",
        "sunset": f"{set_ // 60:02d}:{set_ % 60:02d}",
        "sunset_min": set_,
        "moon": phase,
        "moon_name": moon,
    }


def _minutes(hhmm: str) -> int | None:
    m = re.fullmatch(r"(\d{2}):(\d{2})", hhmm or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


# ----------------------------------------------------------------------
# Pictures: a plate, or the record's own photo
# ----------------------------------------------------------------------


def _pack(data_dir: Path) -> dict[str, Any]:
    """The bundled plates, with any accepted on the paintings page over them."""
    plates: dict[str, Any] = {}
    data = N.read_json(HERE / "plates.json", {})
    if isinstance(data, dict) and isinstance(data.get("plates"), dict):
        plates = dict(data["plates"])
    extra = N.read_json(data_dir / "plates_extra.json", {})
    if isinstance(extra, dict):
        plates.update(extra)
    return plates


def _plate(
    sci: str, pack: dict[str, Any], data_dir: Path, levels: str
) -> dict[str, Any] | None:
    entry = pack.get(sci)
    if not isinstance(entry, dict) or not entry.get("file"):
        return None
    url = COMMONS_FILE + urllib.parse.quote(str(entry["file"])) + "?width=2000"
    raw = N.cached_get(data_dir, url)
    if not raw:
        return None
    key = hashlib.sha1(
        f"{entry['file']}|{entry.get('box')}|{PICTURE_PX}".encode()
    ).hexdigest()[:20]
    colour = data_dir / "img" / f"p_{key}.jpg"
    src = N.render(raw, PICTURE_PX, entry.get("box"), colour, "colour")
    if levels != "colour":
        src = N.render(raw, PICTURE_PX, entry.get("box"), colour, levels)
    if not src:
        return None
    source = str(entry.get("source") or "Classic plate")
    return {
        "src": src,
        "kind": "plate",
        "on_paper": True,
        "credit": f"{source} · public domain",
        "paper": N.paper_of(src, data_dir / "img" / f"p_{key}.paper", levels),
        "key": f"p_{key}",
        "aspect": N.aspect_of(src),
        "file": str(colour),
    }


def _photo(
    row: dict[str, Any], base: str, token: str, data_dir: Path, levels: str
) -> dict[str, Any] | None:
    url, credit = str(row.get("photo") or ""), str(row.get("photo_credit") or "")
    if not url and row.get("thumb") and base:
        thumb = str(row["thumb"])
        url = thumb if "://" in thumb else base + "/" + thumb.lstrip("/")
        credit = "via BirdNET-Go"
    tok = token if base and url.startswith(base + "/") else ""
    if not url and row.get("sci"):
        url, credit = S.inat_reference(row["sci"], data_dir)
    if not url:
        return None
    raw = N.cached_get(data_dir, url, tok)
    if not raw:
        return None
    key = hashlib.sha1(f"{url}|{PICTURE_PX}".encode()).hexdigest()[:20]
    colour = data_dir / "img" / f"f_{key}.jpg"
    src = N.render(raw, PICTURE_PX, None, colour, "colour")
    if levels != "colour":
        src = N.render(raw, PICTURE_PX, None, colour, levels)
    if not src:
        return None
    return {
        "src": src,
        "kind": "photo",
        "on_paper": False,
        "credit": credit or "photo",
        "key": f"f_{key}",
        "aspect": N.aspect_of(src),
        "file": str(colour),
    }


def _painting(key: str, data_dir: Path, levels: str) -> dict[str, Any] | None:
    """A kept painting, resized for the sheet, or None."""
    folder = P.gen_dir(data_dir)
    img = folder / f"{key}.jpg"
    if not img.exists():
        return None
    meta = N.read_json(folder / f"{key}.json", {})
    meta = meta if isinstance(meta, dict) else {}
    try:
        raw = img.read_bytes()
    except OSError:
        return None
    src = N.render(raw, PICTURE_PX, None, folder / f"{key}_{PICTURE_PX}.jpg", levels)
    if not src:
        return None
    return {
        "src": src,
        "kind": "painting",
        "on_paper": True,
        "credit": str(meta.get("credit") or "AI painting"),
        "paper": N.paper_of(src, folder / f"{key}.paper", levels),
        "key": f"g_{key}",
        "aspect": N.aspect_of(src),
        "file": str(img),
    }


def _paint_job(key: str, row: dict[str, Any], gen: dict[str, Any]) -> dict[str, Any]:
    job = {
        "key": key,
        "style": gen["style"],
        "name": row["name"],
        "sci": row["sci"],
        "kind": row.get("kind") or "Aves",
        "how": gen.get("how") or "",
        "model": gen.get("model", P.DEFAULT_MODEL),
        "ref_mode": gen.get("ref_mode", "photo"),
    }
    if row.get("ref"):
        job["ref"], job["ref_credit"] = row["ref"], row.get("ref_credit") or ""
    return job


HELD_MAX = 400
_HELD_LOCK = threading.Lock()


def held(data_dir: Path) -> dict[str, dict[str, Any]]:
    """Paintings a sheet would have started, waiting for the Start button."""
    got = N.read_json(data_dir / "held_paints.json", {})
    return got if isinstance(got, dict) else {}


def _hold(data_dir: Path, job: dict[str, Any]) -> None:
    with _HELD_LOCK:
        got = held(data_dir)
        if job["key"] in got:
            return
        got[job["key"]] = job
        for old in list(got)[: max(0, len(got) - HELD_MAX)]:
            del got[old]
        N.write(data_dir / "held_paints.json", json.dumps(got).encode())


def _unhold(data_dir: Path, keys: list[str] | None = None) -> None:
    with _HELD_LOCK:
        got = held(data_dir)
        for k in keys if keys is not None else list(got):
            got.pop(k, None)
        N.write(data_dir / "held_paints.json", json.dumps(got).encode())


WANTED_S = 30 * 86400


def _note_wanted(data_dir: Path, gen: dict[str, Any]) -> None:
    """The painting settings a sheet last rendered with, so Render now can paint
    the species on a frame in them without waiting for the next refresh."""
    path = data_dir / "wanted_paints.json"
    now = time.time()
    got = N.read_json(path, {})
    got = got if isinstance(got, dict) else {}
    got = {
        k: v
        for k, v in got.items()
        if isinstance(v, dict) and now - float(v.get("at") or 0) < WANTED_S
    }
    model = gen.get("model", P.DEFAULT_MODEL)
    got[f"{gen['style']}|{model}|{gen.get('ref_mode', 'photo')}"] = {
        "style": gen["style"],
        "how": gen.get("how") or "",
        "model": model,
        "ref_mode": gen.get("ref_mode", "photo"),
        "at": now,
    }
    N.write(path, json.dumps(got).encode())


def wanted_configs(data_dir: Path) -> list[dict[str, Any]]:
    got = N.read_json(data_dir / "wanted_paints.json", {})
    if not isinstance(got, dict):
        return []
    now = time.time()
    return [
        v
        for v in got.values()
        if isinstance(v, dict)
        and now - float(v.get("at") or 0) < WANTED_S
        and (v.get("style") in P.STYLES or P._CUSTOM_RE.fullmatch(str(v.get("style"))))
    ]


def render_now(data_dir: Path, fal_key: str, cap: int) -> tuple[int, int]:
    """Paint what is missing now: what a button-mode refresh held, plus the
    species on a frame in the last three days in each setting a sheet last used.
    (started, found)."""
    folder = P.gen_dir(data_dir)
    jobs = {j["key"]: j for j in held(data_dir).values()}
    for cfg in wanted_configs(data_dir):
        for name, sci, kind in on_frame_species(data_dir):
            key = P.key_for(sci, cfg["style"], kind, False, cfg["model"])
            jobs.setdefault(
                key,
                _paint_job(
                    key,
                    {"name": name, "sci": sci, "kind": kind},
                    cfg,
                ),
            )
    ready = [
        j
        for j in jobs.values()
        if not (folder / f"{j['key']}.jpg").exists()
        and not (j.get("collage") and not Path(j["collage"]).exists())
    ]
    _unhold(data_dir)
    room = max(0, cap - P.budget_used(folder))
    begin = [j for j in ready if P.state(j["key"], folder) == "none"][:room]
    P.start_batch(begin, data_dir, fal_key, cap)
    return len(begin), len(ready)


LIVE_S = 6


def _live_refresh(states: dict[str, int]) -> int:
    """Seconds after which the editor should ask for the sheet again, while a
    painting or a cut-out is on the way; 0 once there is nothing to wait for."""
    busy = ("running", "paint_running", "paint_waiting")
    return LIVE_S if any(states.get(k) for k in busy) else 0


def _unpainted_hint(n: int, states: dict[str, int], has_key: bool, hold: bool) -> str:
    """What an only-painted sheet says while none of its species is painted."""
    many = f"{n} specimen{'' if n == 1 else 's'}"
    if not has_key:
        return "Painting needs a fal.ai key in the plugin settings."
    if states.get("paint_held") or hold:
        return f"{many} waiting for Start painting on the paintings page."
    if states.get("paint_capped"):
        return f"{many} to paint; today's painting limit is used up."
    return f"Painting {many}. The sheet fills in as each one finishes."


def _attach_pictures(
    rows: list[dict[str, Any]],
    base: str,
    token: str,
    data_dir: Path,
    levels: str,
    fal_key: str = "",
    gen: dict[str, Any] | None = None,
    classic: bool = False,
) -> dict[str, int]:
    """Each row's best picture to-day, up the ladder: its AI painting when
    one is kept (``gen`` carries the style, the custom words, the day's
    dapper species and the cap; a few new paintings are started per refresh),
    else a classic plate (Fugleramme's, cut already), else a plate from the
    bundled pack, else the record's own photo (or BirdNET-Go's
    thumbnail, or, for a bird, the first openly licensed iNaturalist photo).
    Then its cut-out: a kept mask rides along as ``cut``; a picture without
    one starts a mask job in the background. Returns the states counted."""
    pack = _pack(data_dir)
    states: dict[str, int] = {}
    folder = P.gen_dir(data_dir) if gen else None
    started_paint = 0
    only_painted = bool(gen and gen.get("only_painted"))

    def wanted(row: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal started_paint
        if not gen or not row.get("sci"):
            return None
        dapper = row["sci"] == gen.get("dapper_sci")
        key = P.key_for(
            row["sci"],
            gen["style"],
            row.get("kind") or "Aves",
            dapper,
            gen.get("model", P.DEFAULT_MODEL),
        )
        pic = _painting(key, data_dir, levels)
        if pic:
            states["painted"] = states.get("painted", 0) + 1
            # a tile, a card, a framed portrait: the picture is the object, kept whole
            pic["whole"] = gen["style"] in P.WHOLE
            return pic
        go, why = P.may_start(key, folder, fal_key, gen["cap"])
        if go and gen.get("hold"):
            _hold(data_dir, _paint_job(key, row, gen))
            why = "held"
        elif go and started_paint < P.per_fetch():
            job = _paint_job(key, row, gen)
            if P.start(job, data_dir, fal_key, gen["cap"]):
                started_paint += 1
                why = "running"
        elif go:
            why = "waiting"
        states["paint_" + (why or "waiting")] = (
            states.get("paint_" + (why or "waiting"), 0) + 1
        )
        return None

    def best(row: dict[str, Any]) -> dict[str, Any] | None:
        return (
            (classic and C.plate(row["sci"], data_dir, levels))
            or _plate(row["sci"], pack, data_dir, levels)
            or _photo(row, base, token, data_dir, levels)
        )

    # paintings are decided in order (a few new ones per refresh); the rest side by side
    painted = [wanted(r) for r in rows]
    with ThreadPoolExecutor(max_workers=6) as pool:
        pics = list(
            pool.map(
                lambda rp: rp[1] or (None if only_painted else best(rp[0])),
                zip(rows, painted, strict=True),
            )
        )
    started = 0
    for row, pic in zip(rows, pics, strict=True):
        row["pic"] = pic
        if not pic or not pic.get("key"):
            continue
        st = M.state(pic["key"], data_dir)
        if st == "ready":
            pic["cut"] = M.cut_url(pic["key"], data_dir, levels)
            st = "ready" if pic["cut"] else "failed"
        elif st == "none" and fal_key and started < M.per_fetch():
            try:
                picture = Path(pic["file"]).read_bytes()
            except OSError:
                picture = b""
            if picture and M.start(
                pic["key"], picture, data_dir, fal_key, str(row.get("name") or "")
            ):
                started += 1
                st = "running"
        elif st == "none" and not fal_key:
            st = "no_key"
        if st == "bad" and pic.get("kind") != "painting":
            # the model could not find one specimen in it: a soft oval, never a
            # rectangle. A painting is on plain white paper by construction, so
            # its edges cut it cleanly even when the subject is tiny.
            pic["on_paper"] = False
        pic["cut_state"] = st
        pic.pop("file", None)
        states[st] = states.get(st, 0) + 1
    return states


def _remember(data_dir: Path, rows: list[dict[str, Any]]) -> None:
    """Every species the widget has shown, for the paintings page."""
    path = data_dir / "species_seen.json"
    seen = N.read_json(path, {})
    seen = seen if isinstance(seen, dict) else {}
    changed = False
    for r in rows:
        if r.get("sci") and seen.get(r["sci"]) != [r["name"], r.get("kind") or "Aves"]:
            seen[r["sci"]] = [r["name"], r.get("kind") or "Aves"]
            changed = True
    if changed:
        N.write(path, json.dumps(seen, ensure_ascii=False).encode())


ON_FRAME_S = 3 * 86400


def _note_shown(data_dir: Path, shown: list[dict[str, Any]]) -> None:
    """The species a sheet is showing now, kept for ON_FRAME_S, so the paintings
    page can paint what is on a frame and not everything ever seen."""
    path = data_dir / "species_shown.json"
    now = time.time()
    held = N.read_json(path, {})
    held = held if isinstance(held, dict) else {}
    held = {
        k: v
        for k, v in held.items()
        if isinstance(v, list) and len(v) == 3 and now - float(v[2]) < ON_FRAME_S
    }
    for r in shown:
        if r.get("sci"):
            held[r["sci"]] = [r["name"], r.get("kind") or "Aves", now]
    N.write(path, json.dumps(held, ensure_ascii=False).encode())


def on_frame_species(data_dir: Path) -> list[tuple[str, str, str]]:
    held = N.read_json(data_dir / "species_shown.json", {})
    if not isinstance(held, dict):
        return []
    now = time.time()
    out = [
        (str(v[0]), sci, str(v[1]))
        for sci, v in held.items()
        if isinstance(v, list) and len(v) == 3 and now - float(v[2]) < ON_FRAME_S
    ]
    return sorted(out, key=lambda t: t[0].lower())


def known_species(data_dir: Path) -> list[tuple[str, str, str]]:
    seen = N.read_json(data_dir / "species_seen.json", {})
    if not isinstance(seen, dict):
        return []
    out = [
        (str(v[0]), sci, str(v[1]))
        for sci, v in seen.items()
        if isinstance(v, list) and len(v) == 2
    ]
    return sorted(out, key=lambda t: t[0].lower())


# ----------------------------------------------------------------------
# Sizes and labels
# ----------------------------------------------------------------------


def _lengths() -> dict[str, float]:
    data = N.read_json(HERE / "sizes.json", {})
    if isinstance(data, dict) and isinstance(data.get("length_cm"), dict):
        return {k: float(v) for k, v in data["length_cm"].items()}
    return {}


def _size(rows: list[dict[str, Any]], sizing: str) -> None:
    """``factor``: how large each is drawn against the others. True to
    life from the bird length table (against a 20 cm bird) or the kind's
    factor, compressed so a wren stays readable beside a crow, or compressed
    further so the page packs close; the busiest
    biggest on a gentle curve; or all alike."""
    lengths = _lengths()
    top = max((r["count"] for r in rows), default=1) or 1
    for r in rows:
        cm = lengths.get(r["sci"])
        if cm:
            r["size_cm"] = cm
        if sizing == "same":
            r["factor"] = 1.0
        elif sizing == "busy":
            r["factor"] = round(0.65 + (r["count"] / top) ** 0.325, 2)
        elif cm and sizing == "compact":
            r["factor"] = round(max(0.7, min(1.6, (cm / 20.0) ** 0.4)), 2)
        elif cm:
            r["factor"] = round(max(0.55, min(2.0, (cm / 20.0) ** 0.75)), 2)
        else:
            r["factor"] = float(r.get("factor") or 1.0)
        bird = (r.get("pic") or {}).get("bird")
        if bird:
            # a plate whose bird fills little of the cut-out (a long perch, a
            # spray of leaves) is drawn larger so the bird, not the cut-out,
            # keeps its size against the others
            r["factor"] = round(r["factor"] * max(0.7, min(1.6, 0.75 / bird)), 2)


def _count_text(r: dict[str, Any], source: str, window: str) -> str:
    n = int(r.get("count") or 0)
    if source == "birdnet_go":
        word = "call" if n == 1 else "calls"
        return f"{n} {word}" + (" this week" if window == "week" else "")
    if source == "gbif":
        return f"{n} record{'' if n == 1 else 's'}"
    return f"{n} obs."


def _label_lines(
    r: dict[str, Any], lines: tuple[str, ...], source: str, window: str
) -> list[str]:
    out: list[str] = []
    for line in lines:
        if line == "name":
            out.append(r["name"])
        elif line == "sci" and r.get("sci") and r["sci"] != r["name"]:
            out.append(r["sci"])
        elif line == "count":
            bit = _count_text(r, source, window)
            if source == "birdnet_go" and window == "today" and r.get("last"):
                bit += f" · last {r['last']}"
            out.append(bit)
        elif line == "last" and source != "birdnet_go" and r.get("last"):
            out.append(r["last"])
        elif line == "flag" and r.get("flag"):
            out.append(
                r.get("flag_text")
                or ("new to-day" if r["flag"] == "new" else r["flag"])
            )
        elif line == "who" and r.get("who"):
            out.append(f"by {r['who']}")
        elif line == "size" and r.get("size_cm"):
            out.append(f"L {int(r['size_cm'])} cm")
    return out


# ----------------------------------------------------------------------
# fetch
# ----------------------------------------------------------------------


def _cached_rows(
    cache: Path, max_age_s: float, load: Any
) -> tuple[dict[str, Any] | None, str | None]:
    """The source's answer, kept for ``max_age_s``; a stale answer beats an
    empty page while the source is busy."""
    raw = None
    with contextlib.suppress(OSError):
        if cache.exists() and time.time() - cache.stat().st_mtime < max_age_s:
            raw = N.read_json(cache, None)
    if isinstance(raw, dict):
        return raw, None
    raw, err = load()
    if err or raw is None:
        stale = N.read_json(cache, None) if cache.exists() else None
        if isinstance(stale, dict):
            stale["stale_error"] = err
            return stale, None
        return None, err or "no answer"
    N.write(cache, json.dumps(raw).encode())
    return raw, None


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    source = _choice(options.get("source"), SOURCES)
    window = _choice(options.get("window"), WINDOWS)
    days = DAYS.get(str(options.get("days") or ""), 30)
    quality = _choice(options.get("quality"), QUALITIES)
    radius_km = _choice(options.get("radius_km"), RADII)
    login = str(options.get("user_login") or "").strip()
    count = _int(options.get("count"), 8, 2, SHEET_MAX)
    order = str(options.get("order") or "most")
    order = order if order in ORDERS else "most"
    sizing = _choice(options.get("sizing"), SIZINGS)
    labels = _choice(options.get("labels"), LABELS)
    picked = options.get("label_lines")
    lines = (
        tuple(v for v in LABEL_LINES if v in _picks(picked)) or ("name",)
        if picked is not None
        else ("name", "count")
    )
    text_scale = int(_choice(options.get("text_size"), TEXT_SCALES)) / 100
    paper = _choice(options.get("paper"), PAPERS)
    spacing = _choice(options.get("spacing"), SPACINGS)
    layout = _choice(options.get("layout"), LAYOUTS)
    facing = _choice(options.get("facing"), FACINGS)
    neutral = options.get("neutral") in (True, "true", "True", 1, "1", "on")
    levels = _choice(options.get("panel"), LEVELS)
    night = _choice(options.get("night"), NIGHTS) if source == "birdnet_go" else "off"
    footer = _choice(options.get("footer"), FOOTERS)
    title = str(options.get("title") or "").strip()
    min_conf = _int(options.get("min_confidence"), 70, 10, 99)
    refresh_min = _int(options.get("refresh_min"), 15, 1, 24 * 60)
    name_locale = _choice(options.get("names"), NAME_LOCALES)
    name_place = str(options.get("names_place") or "").strip()
    base = S.base_url(options.get("url")) if source == "birdnet_go" else ""
    token = str(options.get("token") or "").strip()
    fal_key = str((settings or {}).get("fal_api_key") or "").strip()
    pictures = _choice(options.get("pictures"), PICTURES)
    art_style = _choice(options.get("art_style"), P.STYLE_IDS)
    how = ""
    if options.get("style_mode") == "builder":
        art_style = P.register_custom(P.build_how(options))
    elif art_style == "custom":
        how = (
            " ".join(str(options.get("art_prompt") or "").split())[:800]
            or P.CUSTOM_EXAMPLE
        )
        art_style = P.register_custom(how)
    dapper = options.get("dapper") in (True, "true", "True", 1, "1", "on")
    hold = options.get("paint_start") == "button"
    only_painted = options.get("unpainted") == "hide"
    plates = _choice(options.get("plate_source"), PLATE_SOURCES)
    ref_mode = _choice(options.get("paint_ref"), REF_MODES)
    model = P.model_of(options.get("model"))
    arrangement = (
        _choice(options.get("arrangement"), ARRANGEMENTS)
        if pictures == "gen"
        else "sheet"
    )
    scene = _choice(options.get("scene"), tuple(P.SCENES))
    fidelity = _choice(options.get("scene_fidelity"), tuple(P.FIDELITY))
    scene_every = int(_choice(options.get("scene_every"), P.SCENE_EVERY))
    rules = exclude_rules(
        str(options.get("exclude") or ""), str((settings or {}).get("exclude") or "")
    )
    now = datetime.now(_tz(options))
    today = now.date()
    data_dir = Path(ctx.get("data_dir") or ".")
    with contextlib.suppress(OSError):
        data_dir.mkdir(parents=True, exist_ok=True)
    place = _place(options)

    def out(**kw: Any) -> dict[str, Any]:
        base_out = {
            "source": source,
            "title": title,
            "area": "",
            "period": "",
            "date": now.strftime("%a %-d %b")
            if sys.platform != "win32"
            else now.strftime("%a %d %b"),
            "date_iso": now.strftime("%Y-%m-%d"),
            "now": now.strftime("%H:%M"),
            "species": [],
            "species_total": 0,
            "obs_total": 0,
            "excluded": 0,
            "count": count,
            "sizing": sizing,
            "labels": labels,
            "label_lines": list(lines),
            "text_scale": text_scale,
            "paper": paper,
            "neutral": neutral,
            "spacing": spacing,
            "scale": _int(options.get("scale"), 100, 40, 100),
            "layout": layout,
            "facing": facing,
            "panel": levels,
            "footer": footer,
            "night": night,
            "sky": {"dark": False},
            "asleep": None,
            "live_refresh_s": 0,
            "state": "ok",
            "error": "",
            "hint": "",
            "credits": [],
            "notices": [],
            "status": [],
            "font_css": N.font_css(("im_fell", "im_fell_sc", "archivo_narrow")),
            "demo": False,
            "pictures": pictures,
            "art_style": art_style,
            "arrangement": arrangement,
            "scene": None,
        }
        base_out.update(kw)
        return base_out

    # --- the rows, from whichever source ---------------------------------
    rows: list[dict[str, Any]] = []
    species_total = obs_total = 0
    area = period = ""
    demo = False
    stale: str | None = None
    if source == "birdnet_go":
        period = "this week" if window == "week" else "to-day"
        if not base:
            demo = True
            region = S.demo_region(*(place[:2] if place else (None, None)))
            raw = S.demo_birdnet(region, now)
            title = title or "Demo garden"
        else:
            slug = hashlib.sha1(f"{base}|{min_conf}|{window}".encode()).hexdigest()[:12]
            raw, err = _cached_rows(
                data_dir / f"result_{slug}.json",
                refresh_min * 60,
                lambda: S.from_birdnet(
                    base, token, min_conf, window, now.strftime("%Y-%m-%d")
                ),
            )
            if raw is None:
                return out(
                    state="error",
                    error=err or S.ERR_UNREACHABLE_BIRDNET.format(url=base),
                    title=title or "Backyard",
                )
            stale = raw.get("stale_error")
            title = title or "Backyard"
        rows = S.rows_birdnet(raw, window)
        period = "heard " + period
        if not demo:
            _rarity(data_dir, rows, now.strftime("%Y-%m-%d"))
            _names(data_dir, rows, name_locale, name_place)
    elif source == "inaturalist":
        if not place and not login:
            return out(
                state="nowhere", error=ERR_NOWHERE_INAT, title=title or "Nature nearby"
            )
        taxa = [t for t in _picks(options.get("kinds_inat")) if t.isdigit()]
        q = S.inat_query(place, radius_km, login, taxa, days, quality, today)
        slug = hashlib.sha1(json.dumps(q, sort_keys=True).encode()).hexdigest()[:12]
        raw, err = _cached_rows(
            data_dir / f"result_{slug}.json", refresh_min * 60, lambda: S.from_inat(q)
        )
        if raw is None:
            return out(
                state="error",
                error=err or S.ERR_UNREACHABLE_INAT,
                title=title or (place[2] if place else login),
            )
        stale = raw.get("stale_error")
        rows = S.rows_inat(raw, today)
        species_total, obs_total = raw.get("species_total", 0), raw.get("obs_total", 0)
        title = title or (place[2] if place and place[2] else login)
        area = f"within {radius_km} km" if place else f"by {login}"
        period = {
            7: "this week",
            14: "this fortnight",
            30: "this month",
            90: "these three months",
            365: "this year",
        }.get(days, f"in {days} days")
    else:
        if not place:
            return out(
                state="nowhere", error=ERR_NOWHERE, title=title or "Nature nearby"
            )
        taxa = [
            t
            for t in _picks(options.get("kinds_gbif"))
            if re.fullmatch(r"\d+(\+\d+)*", t)
        ]
        q = S.gbif_query(place, radius_km, taxa, now.month)
        slug = hashlib.sha1(
            f"{round(place[0], 2)},{round(place[1], 2)}|{radius_km}|{','.join(sorted(taxa))}|{now.month}".encode()
        ).hexdigest()[:12]
        raw, err = _cached_rows(
            data_dir / f"result_{slug}.json", 30 * 86400, lambda: S.from_gbif(q)
        )
        if raw is None:
            return out(
                state="error",
                error=err or S.ERR_UNREACHABLE_GBIF,
                title=title or place[2],
            )
        stale = raw.get("stale_error")
        rows = S.rows_gbif(raw, today, data_dir, lookups=count + 8)
        species_total, obs_total = len(rows), int(raw.get("count") or 0)
        title = title or place[2]
        area = f"within {radius_km} km"
        period = "in " + now.strftime("%B")
    if source != "inaturalist":
        species_total = species_total or len(rows)
    obs_total = obs_total or sum(r["count"] for r in rows)

    # --- exclusions ------------------------------------------------------
    _remember(data_dir, rows)
    kept = [r for r in rows if not is_excluded(r, rules)]
    excluded = len(rows) - len(kept)
    rows = kept
    _order_rows(rows, order, source, data_dir, now.strftime("%Y-%m-%d"))

    # --- night (BirdNET-Go) ----------------------------------------------
    sky = _sky(place, now) if source == "birdnet_go" else {"dark": False}
    asleep = None
    if night != "off" and sky.get("dark") and rows:
        latest = max(rows, key=lambda r: _minutes(r.get("last") or "") or 0)
        last_min = _minutes(latest.get("last") or "")
        quiet = (
            (now.hour * 60 + now.minute - last_min) if last_min is not None else None
        )
        if night == "owls":
            sunset = int(sky.get("sunset_min") or 0)
            night_rows = [
                r for r in rows if (_minutes(r.get("last") or "") or 0) >= sunset
            ]
            if night_rows:
                rows = night_rows
            else:
                asleep = {
                    "since": latest.get("last") or "",
                    "quiet_min": quiet or 0,
                    "last": latest["name"],
                }
        elif quiet is not None and quiet >= QUIET_MIN:
            asleep = {
                "since": latest.get("last") or "",
                "quiet_min": quiet,
                "last": latest["name"],
            }

    shown = [dict(r) for r in rows[:count]]
    _note_shown(data_dir, shown)
    if not rows:
        hint = {
            "birdnet_go": "The microphone has heard nothing yet.",
            "inaturalist": "Try a wider circle, a longer period or more kinds.",
            "gbif": "Try a wider circle or more kinds.",
        }[source]
        return out(
            state="empty",
            title=title,
            area=area,
            period=period,
            species_total=0,
            excluded=excluded,
            hint=hint,
            sky=sky,
            demo=demo,
        )

    # --- pictures, sizes, labels ------------------------------------------
    P.configure(settings)
    M.configure(settings)
    gen = None
    if pictures == "gen":
        gen = {
            "style": art_style,
            "how": how,
            "model": model,
            "cap": P.daily_cap(settings),
            "hold": hold,
            "only_painted": only_painted,
            "ref_mode": ref_mode,
            "dapper_sci": P.dapper_pick(
                [r["sci"] for r in shown if r.get("sci")], now.strftime("%Y-%m-%d")
            )
            if dapper
            else "",
        }
    if gen and not demo:
        _note_wanted(data_dir, gen)
    mask_states = _attach_pictures(
        shown, base, token, data_dir, levels, fal_key, gen, plates == "classic"
    )
    unpainted = 0
    if gen and only_painted:
        unpainted = len([r for r in shown if not r.get("pic")])
        shown = [r for r in shown if r.get("pic")]
        if not shown:
            return out(
                state="painting",
                title=title,
                area=area,
                period=period,
                species_total=species_total,
                excluded=excluded,
                hint=_unpainted_hint(unpainted, mask_states, bool(fal_key), hold),
                sky=sky,
                demo=demo,
                live_refresh_s=_live_refresh(mask_states),
            )
    _size(shown, sizing)
    for r in shown:
        r["count_text"] = _count_text(r, source, window)
        r["lines"] = _label_lines(r, lines, source, window)
        r.pop("hourly", None)
    credits: list[str] = []
    artists: list[str] = []
    for r in shown:
        pic = r.get("pic") or {}
        if pic.get("classic"):
            if pic["artist"] not in artists:
                artists.append(pic["artist"])
            continue
        c = pic.get("credit")
        if c and c not in credits:
            credits.append(c)
    if artists:
        credits.insert(0, C.credit_for(artists))
    notices: list[str] = []
    status: list[str] = []
    if stale:
        notices.append(
            f"{ {'birdnet_go': 'BirdNET-Go', 'inaturalist': 'iNaturalist', 'gbif': 'GBIF'}[source] } not answering; showing the last answer"
        )
    waiting = [r["name"] for r in shown if not r.get("pic")]
    if waiting:
        status.append(f"{len(waiting)} without a picture yet")
    if mask_states.get("running"):
        n = mask_states["running"]
        status.append(f"{n} cut-out{'' if n == 1 else 's'} on the way")
    if mask_states.get("no_key"):
        notices.append(NOTE_NO_KEY)
    if gen:
        want = len([r for r in shown if r.get("sci")])
        have = mask_states.get("painted", 0)
        if mask_states.get("paint_running") or mask_states.get("paint_waiting"):
            status.append(
                f"painting {have + 1} of {want}" if have < want else "painting"
            )
        if mask_states.get("paint_held"):
            n = mask_states["paint_held"]
            status.append(
                f"{n} painting{'' if n == 1 else 's'} waiting for Start painting"
            )
        if mask_states.get("paint_no_key"):
            notices.append(P.NOTE_NO_KEY)
        if mask_states.get("paint_capped"):
            status.append(P.NOTE_GEN_CAP.format(cap=gen["cap"]))
        if mask_states.get("paint_failed"):
            n = mask_states["paint_failed"]
            notices.append(
                f"{n} painting{'' if n == 1 else 's'} failed; tried again later"
            )
    scene_out = None
    if gen and arrangement == "scene":
        scene_out, more = _scene(
            shown, gen, scene, fidelity, scene_every, fal_key, data_dir, levels, ctx
        )
        status += [m_ for m_ in more if m_ not in status]
    return out(
        title=title,
        area=area,
        period=period,
        species=shown,
        species_total=species_total,
        obs_total=obs_total,
        excluded=excluded,
        sky=sky,
        asleep=asleep,
        state="asleep" if asleep else "ok",
        live_refresh_s=_live_refresh(mask_states),
        credits=([scene_out["pic"]["credit"]] if scene_out else credits),
        notices=notices,
        status=status,
        demo=demo,
        scene=scene_out,
    )


def _scene(
    shown: list[dict[str, Any]],
    gen: dict[str, Any],
    scene: str,
    fidelity: str,
    every_h: int,
    fal_key: str,
    data_dir: Path,
    levels: str,
    ctx: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """The sheet repainted as one scene: once every specimen is painted and
    cut out, their cut-outs go on a collage and fal.ai repaints it, at most
    once per ``every_h`` hours for a given line-up. Until then the sheet
    shows. (the scene payload or None, status lines)."""
    folder = P.gen_dir(data_dir)
    keys = [
        str(r["pic"]["key"])[2:]
        for r in shown
        if (r.get("pic") or {}).get("kind") == "painting"
    ]
    if len(keys) < 2 or len(keys) < len(shown):
        return None, ["composing the scene once every specimen is painted"]
    # every specimen cut out: by the model, or, for a painting the model's
    # mask failed on, by its own paper
    if not all(
        (r.get("pic") or {}).get("cut_state") in ("ready", "bad") for r in shown
    ):
        return None, ["composing the scene once every specimen is cut out"]
    w, h = int(ctx.get("cell_w") or 800), int(ctx.get("cell_h") or 480)
    ratio = w / h if h else 1.6
    scene = P.scene_for(scene, [str(r.get("kind") or "") for r in shown])
    if scene in P.GROUNDED:
        fidelity = "free"
    made = P.collage(
        keys, data_dir, ratio, layout="ground" if scene in P.GROUNDED else "ring"
    )
    if not made:
        return None, ["composing the scene"]
    skey = P.scene_key(gen["style"], scene, fidelity, gen.get("model", P.DEFAULT_MODEL))
    meta = N.read_json(folder / f"{skey}.json", {})
    meta = meta if isinstance(meta, dict) else {}
    have = (folder / f"{skey}.jpg").exists()
    age_h = (time.time() - float(meta.get("made") or 0)) / 3600 if have else 1e9
    status: list[str] = []
    if not have or (meta.get("tag") != made["tag"] and age_h >= every_h):
        go, why = P.may_start(skey, folder, fal_key, gen["cap"])
        if go:
            by_key = {str(r["pic"]["key"])[2:]: r for r in shown}
            boxes = [b for b in made["boxes"] if b["key"] in by_key]
            job = {
                "key": skey,
                "style": gen["style"],
                "how": gen.get("how") or "",
                "model": gen.get("model", P.DEFAULT_MODEL),
                "name": "the scene",
                "sci": "",
                "kind": "",
                "collage": made["path"],
                "tag": made["tag"],
                "boxes": [
                    {
                        **b,
                        "name": by_key[b["key"]]["name"],
                        "sci": by_key[b["key"]]["sci"],
                    }
                    for b in boxes
                ],
                "names": [
                    f"the {by_key[b['key']]['name']} ({by_key[b['key']]['sci']})"
                    for b in boxes
                ],
                "scene": scene,
                "fidelity": fidelity,
                "aspect": "4:3" if ratio >= 1 else "3:4",
            }
            if gen.get("hold"):
                _hold(data_dir, job)
                status.append("the scene waits for Start painting")
            else:
                P.start(job, data_dir, fal_key, gen["cap"])
                status.append("composing the scene (fal.ai)")
        elif why == "running":
            status.append("composing the scene (fal.ai)")
        elif why == "capped":
            status.append(P.NOTE_GEN_CAP.format(cap=gen["cap"]))
    if not have:
        return None, status
    try:
        raw = (folder / f"{skey}.jpg").read_bytes()
    except OSError:
        return None, status
    src = N.render(raw, 1600, None, folder / f"{skey}_1600.jpg", levels)
    if not src:
        return None, status
    return {
        "pic": {
            "src": src,
            "kind": "scene",
            "credit": str(meta.get("credit") or "AI scene (fal.ai nano-banana)"),
            "paper": N.paper_of(src, folder / f"{skey}.paper", levels),
        },
        "items": [
            {"name": b.get("name", ""), "sci": b.get("sci", ""), "box": b["box"]}
            for b in meta.get("boxes") or []
            if isinstance(b, dict) and b.get("box")
        ],
        "fidelity": str(meta.get("fidelity") or fidelity),
        "stale": meta.get("tag") != made["tag"],
    }, status


# ----------------------------------------------------------------------
# The paintings page
# ----------------------------------------------------------------------

_SLUG_RE = re.compile(r"[a-z0-9_]{1,60}(?:--[a-z0-9_]{1,24}){0,4}")
_ARCHIVED_RE = re.compile(r"([a-z0-9_]{1,60}(?:--[a-z0-9_]{1,24}){0,4})__(\d{8}-\d{6})")


def _gallery(data_dir: Path, cap: int) -> dict[str, Any]:
    """Everything the paintings page shows, read from gen/: one entry per
    painting key (a species in a style, perched or in flight, dapper or not)."""
    folder = P.gen_dir(data_dir)
    entries: dict[str, dict[str, Any]] = {}

    def entry(key: str) -> dict[str, Any]:
        parts = key.split("--")
        style = parts[1] if len(parts) > 1 else "gould"
        return entries.setdefault(
            key,
            {
                "slug": key,
                "name": parts[0].replace("_", " ").capitalize(),
                "sci": "",
                "kind": "",
                "style": style,
                "style_label": P.style_of(style)[0],
                "flight": "flight" in parts[2:],
                "dapper": "dapper" in parts[2:],
                "model": next(
                    (
                        P.MODELS[x[2:]]["label"]
                        for x in parts[2:]
                        if x.startswith("m_") and x[2:] in P.MODELS
                    ),
                    "",
                ),
                "active": None,
                "archive": [],
                "error": None,
                "cut": "none",
                "cut_why": "",
            },
        )

    def take(e: dict[str, Any], meta: Any) -> None:
        if not isinstance(meta, dict):
            return
        if meta.get("name"):
            e["name"] = str(meta["name"])
        e["sci"] = e["sci"] or str(meta.get("sci") or "")
        e["kind"] = e["kind"] or str(meta.get("kind") or "")
        if meta.get("how") and not e.get("how"):
            e["how"] = str(meta["how"])
            words = e["how"]
            e["style_label"] = "Custom: " + (
                words[:44].rstrip() + "…" if len(words) > 45 else words
            )

    with contextlib.suppress(OSError):
        for f in folder.glob("*.jpg"):
            if (
                not _SLUG_RE.fullmatch(f.stem)
                or not (folder / f"{f.stem}.json").exists()
            ):
                continue  # a resized copy, not a painting
            meta = N.read_json(folder / f"{f.stem}.json", {})
            e = entry(f.stem)
            take(e, meta)
            e["active"] = {
                "file": f.name,
                "credit": str(meta.get("credit") or ""),
                "made": time.strftime(
                    "%d %b %Y",
                    time.localtime(int(meta.get("made") or f.stat().st_mtime)),
                ),
            }
            e["cut"] = M.state(f"g_{f.stem}", data_dir)
            e["cut_why"] = (
                M.why_bad(f"g_{f.stem}", data_dir) if e["cut"] == "bad" else ""
            )
        for f in sorted((folder / "archive").glob("*.jpg"), reverse=True):
            m = _ARCHIVED_RE.fullmatch(f.stem)
            if not m:
                continue
            e = entry(m.group(1))
            take(e, N.read_json(f.with_suffix(".json"), {}))
            e["archive"].append(
                {
                    "file": f.name,
                    "made": datetime.strptime(m.group(2), "%Y%m%d-%H%M%S").strftime(
                        "%d %b"
                    ),
                }
            )
        for f in folder.glob("*.err"):
            if _SLUG_RE.fullmatch(f.stem):
                err = N.read_json(f, {})
                e = entry(f.stem)
                take(e, err)
                e["error"] = (
                    str(err.get("why") or "failed")
                    if isinstance(err, dict)
                    else "failed"
                )
    with P._LOCK:
        running = set(P._PAINTING)
    known = {N.slug(sci): (n, sci, k) for n, sci, k in known_species(data_dir)}
    for key, e in entries.items():
        e["painting"] = key in running
        base = key.split("--")[0]
        if base in known:
            name, sci, kind = known[base]
            e["sci"] = e["sci"] or sci
            e["kind"] = e["kind"] or kind
            if e["name"].lower() == base.replace("_", " "):
                e["name"] = name
    ordered = sorted(
        entries.values(),
        key=lambda e: (e["active"] is None, e["name"].lower(), e["style"]),
    )
    return {
        "paintings": ordered,
        "used": P.budget_used(folder),
        "cap": cap,
        "masks_used": M.budget_used(data_dir),
        "running": sorted(running),
        "styles": [(k, v[0]) for k, v in P.STYLES.items()],
        "custom_example": P.CUSTOM_EXAMPLE,
        "style_tree": _style_tree(ordered),
        "style_groups": [
            (g, [(k, P.STYLES[k][0]) for k in ids]) for g, ids in P.STYLE_GROUPS
        ],
        "kinds": [(k, v[0]) for k, v in K.KINDS.items()],
        "models": [(k, f"{v['label']} ({v['cost']})") for k, v in P.MODELS.items()],
    }


def _style_tree(paintings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The styles by kind and subgroup, each with a painting to preview it: the
    preview species' if there is one, else the newest painting in that style."""
    shown: dict[str, tuple[bool, str]] = {}
    for e in paintings:
        if not e.get("active") or e.get("dapper") or e.get("flight"):
            continue
        mine = e["sci"] == P.PREVIEW_SPECIES[1] and not e.get("model")
        if e["style"] not in shown or (mine and not shown[e["style"]][0]):
            shown[e["style"]] = (mine, e["active"]["file"])
    return [
        {
            "name": top,
            "subs": [
                {
                    "name": sub,
                    "styles": [
                        {
                            "id": i,
                            "label": P.STYLES[i][0],
                            "file": shown.get(i, (False, ""))[1],
                        }
                        for i in ids
                    ],
                }
                for sub, ids in subs
            ],
        }
        for top, subs in P.STYLE_TREE
    ]


def _job_for(e: dict[str, Any]) -> dict[str, Any] | None:
    if not e["sci"]:
        return None
    job = {
        "key": e["slug"],
        "style": e["style"],
        "name": e["name"],
        "sci": e["sci"],
        "kind": e.get("kind") or "",
    }
    if e.get("how"):
        job["how"] = e["how"]
    for part in e["slug"].split("--")[2:]:
        if part.startswith("m_"):
            job["model"] = part[2:]
    return job


def blueprint() -> Any:
    """The page under Widgets: every AI painting this widget has made, with
    earlier versions kept, buttons to paint, repaint, cut out again, delete
    or bring back a version, and the species seen for the Leave out lists."""
    from flask import (
        Blueprint,
        abort,
        current_app,
        flash,
        jsonify,
        redirect,
        render_template,
        request,
        send_from_directory,
        url_for,
    )

    bp = Blueprint("specimen_sheet_admin", __name__, template_folder="templates")

    def plugin() -> Any:
        registry = current_app.config["PLUGIN_REGISTRY"]
        for pl in registry.plugins.values():
            with contextlib.suppress(OSError, AttributeError):
                if Path(pl.path).resolve() == HERE:
                    return pl
        abort(404)

    def data_dir() -> Path:
        path = Path(plugin().data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def settings() -> dict[str, Any]:
        store = current_app.config.get("SETTINGS_STORE")
        if store is None:
            return {}
        pl = plugin()
        got = store.get_for_runtime("plugins", pl.id, pl.manifest.get("settings", []))
        return got if isinstance(got, dict) else {}

    def fal_key() -> str:
        return str(settings().get("fal_api_key") or "").strip()

    def cap() -> int:
        P.configure(settings())
        M.configure(settings())
        return P.daily_cap(settings())

    def slug_or_404(slug: str) -> str:
        if not _SLUG_RE.fullmatch(slug):
            abort(404)
        return slug

    def back() -> Any:
        return redirect(url_for("specimen_sheet_admin.index"))

    def start(job: dict[str, Any], label: str) -> None:
        key = fal_key()
        folder = P.gen_dir(data_dir())
        if not key:
            flash("Add a fal.ai key in this widget's settings first.", "warn")
        elif P.budget_used(folder) >= cap():
            flash(
                f"To-day's limit of {cap()} paintings is used up. Raise it in this widget's settings.",
                "warn",
            )
        elif not P.start(job, data_dir(), key, cap()):
            flash(f"{label} is already being painted.", "info")
        else:
            with contextlib.suppress(OSError):
                (folder / f"{job['key']}.err").unlink()
            flash(
                f"Painting {label}. It takes up to a minute; this page redraws when it lands.",
                "success",
            )

    @bp.get("/")
    def index() -> str:
        dd = data_dir()
        return render_template(
            "specimen_sheet/admin.html",
            g=_gallery(dd, cap()),
            known=known_species(dd),
            has_key=bool(fal_key()),
            held=len(held(dd)),
            wanted=len(wanted_configs(dd)),
        )

    @bp.get("/progress")
    def progress() -> Any:
        got = P.progress(P.gen_dir(data_dir()), cap(), bool(fal_key()))
        got["cutting"] = M.running()
        return jsonify(got)

    def stopped(n: int) -> Any:
        if "application/json" in request.headers.get("Accept", ""):
            return jsonify({"stopped": n})
        flash(
            f"Stopped {n} render{'s' if n != 1 else ''}."
            if n
            else "Nothing was being rendered.",
            "info",
        )
        return back()

    @bp.post("/stop")
    def stop_one() -> Any:
        key = request.form.get("key", "")
        if not key:
            abort(400)
        n = int(P.stop(key, data_dir())) + int(M.stop(key))
        return stopped(min(n, 1))

    @bp.post("/stop-all")
    def stop_all() -> Any:
        return stopped(P.stop_all(data_dir()) + M.stop_all())

    @bp.get("/painting/<path:name>")
    def image(name: str) -> Any:
        folder = P.gen_dir(data_dir())
        stem, _, ext = name.rpartition(".")
        if ext != "jpg":
            abort(404)
        if _SLUG_RE.fullmatch(stem):
            return send_from_directory(folder, name)
        if stem.startswith("archive/") and _ARCHIVED_RE.fullmatch(stem[8:]):
            return send_from_directory(folder / "archive", name[8:])
        abort(404)

    @bp.post("/paint")
    def paint() -> Any:
        sci = request.form.get("sci", "").strip()
        name = request.form.get("name", "").strip()
        known = {s_: (n, k) for n, s_, k in known_species(data_dir())}
        kind = request.form.get("kind", "").strip()
        if not name:
            name = known.get(sci, (sci, ""))[0]
        if not kind:
            kind = known.get(sci, ("", "Aves"))[1]
        if not sci or not re.fullmatch(r"[A-Z][a-z-]+ [a-z-]+( [a-z-]+)?", sci):
            flash("Give the scientific name, for example Gymnorhina tibicen.", "warn")
            return back()
        if kind not in K.KINDS:
            kind = "Aves"
        style = _choice(request.form.get("style"), P.STYLE_IDS)
        job: dict[str, Any] = {
            "style": style,
            "name": name[:80],
            "sci": sci,
            "kind": kind,
        }
        if style == "custom":
            how = (
                " ".join(request.form.get("how", "").split())[:800] or P.CUSTOM_EXAMPLE
            )
            job["style"] = P.register_custom(how)
            job["how"] = how
        job["model"] = P.model_of(request.form.get("model"))
        job["key"] = P.key_for(
            sci, job["style"], kind, request.form.get("dapper") == "1", job["model"]
        )
        start(job, f"the {name[:80]}")
        return back()

    @bp.post("/preview")
    def preview() -> Any:
        style = request.form.get("style", "")
        if style not in P.STYLES:
            abort(400)
        name, sci, kind = P.PREVIEW_SPECIES
        job = {
            "style": style,
            "name": name,
            "sci": sci,
            "kind": kind,
            "model": P.DEFAULT_MODEL,
        }
        job["key"] = P.key_for(sci, style, kind, False, job["model"])
        start(job, f"a preview in {P.STYLES[style][0]}")
        return back()

    @bp.post("/<slug>/repaint")
    def repaint(slug: str) -> Any:
        slug = slug_or_404(slug)
        e = next(
            (e for e in _gallery(data_dir(), cap())["paintings"] if e["slug"] == slug),
            None,
        )
        job = _job_for(e) if e else None
        if job is None:
            abort(404)
        start(job, f"the {e['name']}")
        return back()

    @bp.post("/<slug>/recut")
    def recut(slug: str) -> Any:
        slug = slug_or_404(slug)
        dd = data_dir()
        img = P.gen_dir(dd) / f"{slug}.jpg"
        if not img.exists():
            abort(404)
        for f in M.folder(dd).glob(f"g_{slug}.*"):
            with contextlib.suppress(OSError):
                f.unlink()
        key = fal_key()
        if not key:
            flash("Add a fal.ai key in this widget's settings first.", "warn")
        elif M.start(f"g_{slug}", img.read_bytes(), dd, key, slug, force=True):
            flash("Cutting the painting out again; it takes a few seconds.", "success")
        else:
            flash(
                "That cut-out is already being made, or to-day's cut-outs are used up.",
                "info",
            )
        return back()

    @bp.post("/<slug>/delete")
    def delete(slug: str) -> Any:
        slug = slug_or_404(slug)
        folder = P.gen_dir(data_dir())
        for f in (
            folder / f"{slug}.jpg",
            folder / f"{slug}.json",
            folder / f"{slug}.err",
        ):
            with contextlib.suppress(OSError):
                f.unlink()
        P.drop_derived(folder, slug)
        flash(
            "Painting deleted. With AI paintings on, the species is painted again the next time it is shown.",
            "success",
        )
        return back()

    def retire(keys: list[str]) -> int:
        folder = P.gen_dir(data_dir())
        done = 0
        for key in keys:
            if not _SLUG_RE.fullmatch(key) or not (folder / f"{key}.jpg").exists():
                continue
            P.archive(folder, key)
            with contextlib.suppress(OSError):
                (folder / f"{key}.err").unlink()
            done += 1
        return done

    def retired(n: int) -> None:
        if n:
            flash(
                f"{n} painting{'' if n == 1 else 's'} moved to earlier versions. The widget paints them again as the species are shown, within the daily limit.",
                "success",
            )
        else:
            flash("No paintings were selected.", "info")

    @bp.post("/budget-reset")
    def budget_reset() -> Any:
        N.write(
            P.gen_dir(data_dir()) / "budget.json",
            json.dumps({time.strftime("%Y-%m-%d"): 0}).encode(),
        )
        flash("To-day's count is back to 0.", "success")
        return back()

    @bp.post("/bulk-delete")
    def bulk_delete() -> Any:
        retired(retire(request.form.getlist("keys")))
        return back()

    @bp.post("/render-now")
    def render_now_route() -> Any:
        key = fal_key()
        if not key:
            flash("Add a fal.ai key in this widget's settings first.", "warn")
            return back()
        n, found = render_now(data_dir(), key, cap())
        if not found:
            flash(
                "Nothing is missing: every species on a frame already has its painting. A sheet that has not rendered yet shows nothing here; open the dashboard or the editor once.",
                "info",
            )
        elif not n:
            flash(
                f"To-day's limit of {cap()} paintings is used up, or those are already being painted. Raise it in this widget's settings.",
                "warn",
            )
        else:
            flash(
                f"Painting {n} species, a few at a time. Watch the job tray at the bottom right.",
                "success",
            )
        return back()

    @bp.post("/clear-held")
    def clear_held() -> Any:
        n = len(held(data_dir()))
        _unhold(data_dir())
        flash(f"Dropped {n} waiting painting{'' if n == 1 else 's'}.", "info")
        return back()

    @bp.post("/style-paint")
    def style_paint() -> Any:
        style = request.form.get("art", "")
        if style not in P.STYLES:
            abort(400)
        key = fal_key()
        if not key:
            flash("Add a fal.ai key in this widget's settings first.", "warn")
            return back()
        folder = P.gen_dir(data_dir())
        jobs = []
        for name, sci, kind in on_frame_species(data_dir()):
            k = P.key_for(sci, style, kind)
            if not (folder / f"{k}.jpg").exists():
                jobs.append(
                    {"key": k, "style": style, "name": name, "sci": sci, "kind": kind}
                )
        label = P.STYLES[style][0]
        room = max(0, cap() - P.budget_used(folder))
        if not on_frame_species(data_dir()):
            flash(
                "No species has been on a frame in the last three days; the sheet has to show some first.",
                "info",
            )
        elif not jobs:
            flash(
                f"Every species on a frame already has a painting in {label}.", "info"
            )
        elif not room:
            flash(
                f"To-day's limit of {cap()} paintings is used up. Raise it in this widget's settings.",
                "warn",
            )
        elif not P.start_batch(jobs, data_dir(), key, cap()):
            flash(
                f"{label} is already being painted for the species on a frame.", "info"
            )
        else:
            n = min(len(jobs), room)
            flash(
                f"Painting {n} species in {label}, a few at a time"
                + (
                    f"; {len(jobs) - n} more wait for to-morrow's limit"
                    if len(jobs) > n
                    else ""
                )
                + ". This page redraws as they arrive.",
                "success",
            )
        return back()

    @bp.post("/style-delete")
    def style_delete() -> Any:
        style = request.form.get("art", "")
        if style not in P.STYLES and not P._CUSTOM_RE.fullmatch(style):
            abort(400)
        keys = [
            e["slug"]
            for e in _gallery(data_dir(), cap())["paintings"]
            if e["style"] == style and e["active"]
        ]
        retired(retire(keys))
        return back()

    @bp.post("/<slug>/clear-error")
    def clear_error(slug: str) -> Any:
        slug = slug_or_404(slug)
        with contextlib.suppress(OSError):
            (P.gen_dir(data_dir()) / f"{slug}.err").unlink()
        return back()

    @bp.post("/archive/<stem>/use")
    def use_archived(stem: str) -> Any:
        import os

        m = _ARCHIVED_RE.fullmatch(stem)
        if not m:
            abort(404)
        folder = P.gen_dir(data_dir())
        slug, src = m.group(1), folder / "archive" / f"{stem}.jpg"
        if not src.exists():
            abort(404)
        meta = N.read_json(src.with_suffix(".json"), {})
        if (folder / f"{slug}.jpg").exists():
            P.archive(folder, slug)
        os.replace(src, folder / f"{slug}.jpg")
        if not isinstance(meta, dict) or not meta:
            meta = {"credit": "AI painting", "made": int(time.time())}
        N.write(folder / f"{slug}.json", json.dumps(meta).encode())
        with contextlib.suppress(OSError):
            src.with_suffix(".json").unlink()
        P.drop_derived(folder, slug)
        flash("That version is the species' painting again.", "success")
        return back()

    @bp.post("/archive/<stem>/delete")
    def delete_archived(stem: str) -> Any:
        if not _ARCHIVED_RE.fullmatch(stem):
            abort(404)
        arch = P.gen_dir(data_dir()) / "archive"
        for f in (arch / f"{stem}.jpg", arch / f"{stem}.json"):
            with contextlib.suppress(OSError):
                f.unlink()
        return back()

    return bp

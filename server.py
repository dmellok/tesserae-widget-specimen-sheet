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
import logging
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
T = _sibling("tray")

COMMONS_FILE = "https://commons.wikimedia.org/wiki/Special:FilePath/"

SOURCES = S.SOURCES
WINDOWS = ("today", "week")
DAYS = {"7d": 7, "14d": 14, "30d": 30, "90d": 90, "365d": 365}
RADII = ("10", "2", "5", "25", "50")
QUALITIES = ("research", "")
SIZINGS = ("compact", "true", "same")
LABELS = ("caption", "none")
LABEL_LINES = ("name", "sci", "count", "last", "flag", "who", "size")
TEXT_SCALES = ("100", "60", "80", "125", "150")
PAPERS = ("cream", "white", "aged")
LAYOUTS = ("cluster", "grid", "hero", "year")
MARKS = ("off", "new", "rare", "both")
TALLY = ("digits", "strokes")
# The year list: this many species at most, the earliest arrivals first
YEAR_MAX = 120
# Rare: heard or recorded on this share of the logged days at most, once the log holds LOG_MIN days
RARE_SHARE = 0.05
LOG_MIN_DAYS = 14
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
HERO_PX = 1400
PICTURES = ("free", "gen")
SHEET_MAX = 32
# Which species make the sheet when more were heard or recorded than fit,
# and the order of a numbered key.
ORDERS = ("most", "rarest", "latest", "mix")
ARRANGEMENTS = ("sheet", "scene")
SCENE_WORDS_GROUNDED = ("floor", "log", "bank", "bed", "meadow", "wetland", "garden", "bath", "ground", "grass", "path", "pond", "lawn")
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
logger = logging.getLogger("app.plugins.specimen_sheet")


# ----------------------------------------------------------------------
# Options
# ----------------------------------------------------------------------


def _tz(options: dict[str, Any]) -> tzinfo:
    """IANA zone carried on the location option, or the server's own zone.
    Never None: ``datetime.now(None)`` is naive, and the moon arithmetic
    (an aware epoch) fails on it when a location carries no zone
    (backyard-birds #1)."""
    for value in (options.get("timezone"), options.get("location")):
        if isinstance(value, dict):
            value = value.get("timezone")
        if isinstance(value, str) and value.strip():
            with contextlib.suppress(ValueError, KeyError, OSError):
                return ZoneInfo(value.strip())
    return datetime.now().astimezone().tzinfo or timezone.utc


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


def _log_seen(data_dir: Path) -> tuple[list[str], dict[str, list[str]]]:
    """The widget's log: the days it ran here, and the days each species was
    on the sheet, by the scientific name (else the common name)."""
    log = N.read_json(data_dir / "rarity.json", {})
    if not isinstance(log, dict):
        return [], {}
    days = log.get("days") if isinstance(log.get("days"), list) else []
    seen = log.get("seen") if isinstance(log.get("seen"), dict) else {}
    return [str(d) for d in days], {k: [str(d) for d in v] for k, v in seen.items() if isinstance(v, list)}


def _mark_rows(rows: list[dict[str, Any]], data_dir: Path, marks: str, today: str) -> None:
    """Give each row its mark for the page: ``new`` for a species new here or
    back after an absence (the flag), ``rare`` for one on the sheet on few of
    the logged days (one in twenty at most), once the log holds LOG_MIN_DAYS days. Nothing marks a
    page the widget has only just started keeping."""
    if marks == "off":
        return
    days, seen = _log_seen(data_dir)
    enough = len(days) >= LOG_MIN_DAYS
    rare_max = max(1, round(len(days) * RARE_SHARE))
    for r in rows:
        key = str(r.get("sci") or r.get("name") or "")
        hits = seen.get(key, [])
        fresh = r.get("flag") in ("new", "back") or (enough and hits and all(d >= today for d in hits))
        # rare: on the sheet on few days, and not merely new (seen before to-day)
        rare = enough and any(d < today for d in hits) and len(hits) <= rare_max
        mark = ""
        if marks in ("new", "both") and fresh:
            mark = "new"
        elif marks in ("rare", "both") and rare:
            mark = "rare"
        if mark:
            r["mark"] = mark


def _year_rows(
    data_dir: Path, rows: list[dict[str, Any]], today: str, source: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every species on the sheet this year, from the widget's own log: the
    earliest arrivals first, each with the days it was on the sheet and
    whether it is on to-day's. Rows the sheet has now are kept whole (their
    pictures and counts); the rest are made from the log and what the widget
    remembers of them. (the rows, the year's summary)."""
    year = today[:4]
    days, seen = _log_seen(data_dir)
    by_key = {str(r.get("sci") or r.get("name") or ""): r for r in rows}
    known = {sci: (name, kind) for name, sci, kind in known_species(data_dir)}
    out: list[dict[str, Any]] = []
    # the sheet's own rows count as to-day's even before the log has them (the first day, the demo)
    for key, r in by_key.items():
        if key and today not in seen.get(key, []):
            seen[key] = sorted(set(seen.get(key, [])) | {today})
    for key, hits in seen.items():
        this_year = sorted(d for d in hits if d.startswith(year))
        if not this_year:
            continue
        r = by_key.get(key)
        if r is None:
            name, kind = known.get(key, (key, "Aves"))
            r = {"sci": key if key in known else "", "name": name, "kind": kind, "count": 0, "factor": 1.0, "flag": ""}
        r = dict(r)
        r["year_days"] = len(this_year)
        r["first_day"] = this_year[0]
        r["today"] = today in this_year
        out.append(r)
    out.sort(key=lambda r: (r["first_day"], str(r.get("name") or "").lower()))
    out = out[:YEAR_MAX]
    since = ""
    if out:
        with contextlib.suppress(ValueError):
            since = datetime.strptime(out[0]["first_day"], "%Y-%m-%d").strftime("%-d %b" if sys.platform != "win32" else "%d %b")
    summary = {
        "n": len(out),
        "today": len([r for r in out if r["today"]]),
        "since": since,
        "days": len([d for d in days if d.startswith(year)]),
        "year": year,
    }
    return out, summary


def _day_page(
    rows: list[dict[str, Any]], sky: dict[str, Any], now: datetime
) -> dict[str, Any] | None:
    """The dawn chorus page from an hour before sunrise to two and a half
    after: the morning's birds in the order they started, with the first
    song; the evening page from an hour before sunset to an hour after: the
    last songs, latest first. The rows are reordered in place."""
    rise = _minutes(str(sky.get("sunrise") or ""))
    set_ = sky.get("sunset_min")
    if rise is None or not isinstance(set_, int):
        return None
    mins = now.hour * 60 + now.minute
    if rise - 60 <= mins <= rise + 150:
        sung = [r for r in rows if _minutes(r.get("first") or "") is not None]
        if not sung:
            return None
        sung.sort(key=lambda r: (_minutes(r["first"]) or 0, -int(r.get("count") or 0)))
        rows[:] = sung + [r for r in rows if r not in sung]
        return {
            "kind": "dawn",
            "label": "Dawn chorus",
            "sun": f"sunrise {sky['sunrise']}",
            "song": f"first song {sung[0]['first']} {sung[0]['name']}",
        }
    if set_ - 60 <= mins <= set_ + 60:
        sung = [r for r in rows if _minutes(r.get("last") or "") is not None]
        if not sung:
            return None
        sung.sort(key=lambda r: (-(_minutes(r["last"]) or 0), -int(r.get("count") or 0)))
        rows[:] = sung + [r for r in rows if r not in sung]
        return {
            "kind": "dusk",
            "label": "Evening",
            "sun": f"sunset {sky['sunset']}",
            "song": f"last song {sung[0]['last']} {sung[0]['name']}",
        }
    return None


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
    if now.tzinfo is None:  # a naive clock is read as the server's
        now = now.astimezone()
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


def _painting(key: str, data_dir: Path, levels: str, px: int = PICTURE_PX) -> dict[str, Any] | None:
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
    src = N.render(raw, px, None, folder / f"{key}_{px}.jpg", levels)
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
        "subject": gen.get("subject", ""),
        "origin": gen.get("origin", "panel"),
    }
    if gen.get("plants"):
        job["perch"] = P.perch_for(row["sci"], gen["plants"])
        job["plants"] = ", ".join(gen["plants"])
    if gen.get("hero_sci") and row["sci"] == gen["hero_sci"]:
        job["hero"] = True
        job["aspect"] = gen.get("hero_aspect", "3:4")
    if row.get("ref"):
        job["ref"], job["ref_credit"] = row["ref"], row.get("ref_credit") or ""
    return job


HELD_MAX = 400
_HELD_LOCK = threading.Lock()
SETTLE_S = 20
SEEN_S = 86400
# The options that decide how a cell looks, not which species it shows: a cell
# changing one of these is the same cell in a new look, and the paintings its
# previews held or queued for the old look are no longer wanted.
LOOK_OPTIONS = frozenset(
    (
        "token", "pictures", "style", "build_medium", "build_period", "build_palette",
        "build_line", "build_texture", "build_mood", "build_extra", "pose", "model",
        "unpainted", "dapper", "arrangement", "scene", "scene_fidelity", "scene_every",
        "sizing", "scale", "hero_note", "hero_frame", "labels", "label_lines", "text_size",
        "paper", "panel", "header", "footer", "title", "refresh_min", "names", "names_place",
    )
)


def subject_id(options: dict[str, Any]) -> str:
    """What a cell shows, whatever it looks like: a digest of the options that
    choose its species."""
    return hashlib.sha1(
        json.dumps(
            {k: v for k, v in sorted(options.items()) if k not in LOOK_OPTIONS},
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()[:16]


def cull(data_dir: Path, subject: str, keep: set[str]) -> tuple[int, int]:
    """A preview of a cell in a new look drops what its previews held or
    queued for the old one: the held and the queued paintings of the same
    subject whose keys this look does not want. A painting under way is left
    to finish, being paid for. (queued dropped, held dropped)."""
    stale = [
        k for k, j in held(data_dir).items()
        if j.get("subject") == subject and k not in keep
    ]
    if stale:
        _unhold(data_dir, stale)
    gone = P.cull_queued(subject, keep)
    if stale or gone:
        logger.info(
            "specimen_sheet: dropped %d queued, %d held painting(s) for a look no longer wanted",
            len(gone),
            len(stale),
        )
    return len(gone), len(stale)


def preview_settled(data_dir: Path, cell_id: str, now: float) -> bool:
    """Whether a cell's settings have stood for SETTLE_S seconds of previews:
    the first preview of a setting notes the time, and one coming back with
    the same setting SETTLE_S later is taken as kept, so its paintings start
    without a button. A setting changed is a new cell, so the clock restarts."""
    path = data_dir / "preview_seen.json"
    with _HELD_LOCK:
        got = N.read_json(path, {})
        got = {k: float(v) for k, v in got.items() if isinstance(got, dict) and now - float(v) < SEEN_S} if isinstance(got, dict) else {}
        first = got.get(cell_id)
        if first is None:
            got[cell_id] = now
        N.write(path, json.dumps(got).encode())
    return first is not None and now - first >= SETTLE_S


def held(data_dir: Path) -> dict[str, dict[str, Any]]:
    """Paintings a preview would have started, waiting for Paint now."""
    got = N.read_json(data_dir / "held_paints.json", {})
    return got if isinstance(got, dict) else {}


def _hold(data_dir: Path, job: dict[str, Any], cell_id: str = "") -> None:
    with _HELD_LOCK:
        got = held(data_dir)
        if job["key"] in got:
            return
        got[job["key"]] = {**job, "cell": cell_id}
        for old in list(got)[: max(0, len(got) - HELD_MAX)]:
            del got[old]
        N.write(data_dir / "held_paints.json", json.dumps(got).encode())


def _unhold(data_dir: Path, keys: list[str] | None = None) -> None:
    with _HELD_LOCK:
        got = held(data_dir)
        for k in keys if keys is not None else list(got):
            got.pop(k, None)
        N.write(data_dir / "held_paints.json", json.dumps(got).encode())


def purge_all(data_dir: Path) -> dict[str, int]:
    """Every painting, every earlier version, every painting's cut-out and
    every held painting, gone; the day's count stays. What was removed."""
    P.stop_all(data_dir)
    M.stop_all()
    folder = P.gen_dir(data_dir)
    out = {"paintings": 0, "versions": 0, "cut_outs": 0}
    with contextlib.suppress(OSError):
        for f in folder.iterdir():
            if f.is_file() and f.name != "budget.json":
                if f.suffix == ".jpg":
                    out["paintings"] += 1
                f.unlink()
        arch = folder / "archive"
        if arch.is_dir():
            for f in arch.iterdir():
                if f.suffix == ".jpg":
                    out["versions"] += 1
                f.unlink()
        masks = data_dir / "masks"
        if masks.is_dir():
            for f in masks.iterdir():
                if f.name.startswith("g_"):
                    if f.name.endswith(".cut.png"):
                        out["cut_outs"] += 1
                    f.unlink()
    _unhold(data_dir)
    T.waiting_over(0)
    return out


def start_held(data_dir: Path, fal_key: str, cap: int, subject: str = "") -> int:
    """Paint what previews held back, for one cell when ``subject`` is given (the
    dashboard whose card was pressed, not every dashboard ever previewed),
    within the daily limit. The number started."""
    folder = P.gen_dir(data_dir)
    jobs = [j for j in held(data_dir).values() if not subject or j.get("subject") == subject]
    _unhold(data_dir, [j["key"] for j in jobs])
    ready = [j for j in jobs if not (folder / f"{j['key']}.jpg").exists() and P.state(j["key"], folder) == "none"]
    room = max(0, cap - P.budget_used(folder))
    begin = ready[:room]
    P.start_batch(begin, data_dir, fal_key, cap)
    T.waiting_over(len(begin))
    return len(begin)


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
    plants = list(gen.get("plants") or [])
    got[f"{gen['style']}|{model}|{gen.get('pose', 'mixed')}|{','.join(plants)}"] = {
        "style": gen["style"],
        "how": gen.get("how") or "",
        "model": model,
        "ref_mode": gen.get("ref_mode", "photo"),
        "pose": gen.get("pose", "mixed"),
        "plants": plants,
        "at": now,
    }
    N.write(path, json.dumps(got).encode())


def _note_dashboard(
    data_dir: Path, ctx: dict[str, Any], subject: str, gen: dict[str, Any], shown: list[dict[str, Any]]
) -> None:
    """Which dashboard a panel's sheet is on and the paintings it wants, so the
    paintings page can group them by dashboard. Kept for WANTED_S. A dashboard
    is its name and subject; an edit to the dashboard makes a new subject, and
    the old one goes once it has not rendered for an hour, two sheets on one
    dashboard rendering together both staying."""
    path = data_dir / "dashboards.json"
    now = time.time()
    got = N.read_json(path, {})
    got = got if isinstance(got, dict) else {}
    got = {
        k: v
        for k, v in got.items()
        if isinstance(v, dict) and now - float(v.get("at") or 0) < WANTED_S
    }
    name = " ".join(str(ctx.get("page_name") or "").split())[:80]
    got = {
        k: v
        for k, v in got.items()
        if not (v.get("name") == name and k != f"{name}|{subject}" and now - float(v.get("at") or 0) > 3600)
    }
    got[f"{name}|{subject}"] = {
        "name": name,
        "icon": str(ctx.get("page_icon") or "")[:40],
        "style": gen["style"],
        "style_label": P.style_of(gen["style"])[0],
        "keys": list(dict.fromkeys(gen.get("keys") or [])),
        "species": [r["name"] for r in shown if r.get("sci")][:60],
        "at": now,
    }
    N.write(path, json.dumps(got, ensure_ascii=False).encode())


def dashboards(data_dir: Path) -> list[dict[str, Any]]:
    """The dashboards with a sheet on a panel in the last WANTED_S, latest first."""
    got = N.read_json(data_dir / "dashboards.json", {})
    if not isinstance(got, dict):
        return []
    now = time.time()
    out = [
        v
        for v in got.values()
        if isinstance(v, dict) and now - float(v.get("at") or 0) < WANTED_S and isinstance(v.get("keys"), list)
    ]
    return sorted(out, key=lambda v: -float(v.get("at") or 0))


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
    """Paint what is missing now: the species on a frame in the last three days
    in each setting a sheet last used. (started, found)."""
    folder = P.gen_dir(data_dir)
    jobs: dict[str, dict[str, Any]] = {}
    for cfg in wanted_configs(data_dir):
        for name, sci, kind in on_frame_species(data_dir):
            key = P.key_for(
                sci, cfg["style"], kind, False, cfg["model"], cfg.get("pose", "mixed"),
                P.perch_for(sci, cfg.get("plants") or []),
            )
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
    room = max(0, cap - P.budget_used(folder))
    begin = [j for j in ready if P.state(j["key"], folder) == "none"][:room]
    P.start_batch(begin, data_dir, fal_key, cap)
    return len(begin), len(ready)


LIVE_S = 6


def _live_refresh(states: dict[str, int]) -> int:
    """Seconds after which the editor should ask for the sheet again, while a
    painting or a cut-out is on the way; 0 once there is nothing to wait for."""
    # a held painting keeps the editor coming back too: the preview that comes back with the
    # same settings SETTLE_S later is what starts them
    busy = ("running", "paint_running", "paint_waiting", "deferred", "paint_held")
    return LIVE_S if any(states.get(k) for k in busy) else 0


def _unpainted_hint(n: int, states: dict[str, int], has_key: bool) -> str:
    """What an only-painted sheet says while none of its species is painted."""
    many = f"{n} specimen{'' if n == 1 else 's'}"
    if not has_key:
        return "Painting needs a fal.ai key in the plugin settings."
    if states.get("paint_held"):
        return f"{many} waiting for Paint now in the job tray."
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
    budget_s: float = 7.0,
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
    deadline = time.time() + budget_s
    states: dict[str, int] = {}
    folder = P.gen_dir(data_dir) if gen else None
    started_paint = 0
    only_painted = bool(gen and gen.get("only_painted"))

    def wanted(row: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal started_paint
        if not gen or not row.get("sci"):
            return None
        dapper = row["sci"] == gen.get("dapper_sci")
        hero = bool(gen.get("hero_sci")) and row["sci"] == gen["hero_sci"]
        key = P.key_for(
            row["sci"],
            gen["style"],
            row.get("kind") or "Aves",
            dapper,
            gen.get("model", P.DEFAULT_MODEL),
            gen.get("pose", "mixed"),
            P.perch_for(row["sci"], gen.get("plants") or []),
            hero,
        )
        gen.setdefault("keys", []).append(key)
        pic = _painting(key, data_dir, levels, HERO_PX if hero else PICTURE_PX)
        if pic:
            states["painted"] = states.get("painted", 0) + 1
            # a hero on its painted backdrop is the whole plate, kept entire
            pic["whole"] = hero
            return pic
        go, why = P.may_start(key, folder, fal_key, gen["cap"])
        if go and gen.get("hold"):
            _hold(data_dir, _paint_job(key, row, gen), str(gen.get("cell") or ""))
            why = "held"
        elif go and started_paint < int(gen.get("per_fetch") or P.per_fetch()):
            job = _paint_job(key, row, gen)
            if P.start(job, data_dir, fal_key, gen["cap"]):
                started_paint += 1
                why = "running"
                _unhold(data_dir, [key])
        elif go:
            why = "waiting"
        states["paint_" + (why or "waiting")] = (
            states.get("paint_" + (why or "waiting"), 0) + 1
        )
        return None

    def best(row: dict[str, Any]) -> dict[str, Any] | None:
        # past the deadline, a picture not yet fetched waits for the next refresh: the
        # page's composer gives a widget twelve seconds, and a cold place with a dozen
        # plates and photos to fetch took longer, which left the panel with no page at all
        if time.time() > deadline:
            states["deferred"] = states.get("deferred", 0) + 1
            return None
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


ON_FRAME_S = 86400


def _note_shown(data_dir: Path, shown: list[dict[str, Any]]) -> None:
    """The species a panel is showing now, kept for ON_FRAME_S, so the paintings
    page can paint what is on a frame and not everything ever previewed."""
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
    if r.get("year_days"):
        n = int(r["year_days"])
        return f"{n} day{'' if n == 1 else 's'}"
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
            if source == "birdnet_go" and window == "today" and r.get("last") and not r.get("year_days"):
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
    scale = _int(options.get("scale"), 100, 40, 250)
    if scale > 100:
        # bigger birds on the same page: fewer of them, in the order's priority
        count = max(2, round(count / (scale / 100) ** 2))
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
    layout = _choice(options.get("layout"), LAYOUTS)
    marks = _choice(options.get("marks"), MARKS)
    tally = _choice(options.get("tally"), TALLY)
    day_pages = _choice(options.get("day_pages"), ("off", "on"))
    levels = _choice(options.get("panel"), LEVELS)
    night = _choice(options.get("night"), NIGHTS) if source == "birdnet_go" else "off"
    footer = _choice(options.get("footer"), FOOTERS)
    header = _choice(options.get("header"), ("top", "bottom", "hidden"))
    title = str(options.get("title") or "").strip()
    min_conf = _int(options.get("min_confidence"), 70, 10, 99)
    refresh_min = _int(options.get("refresh_min"), 15, 1, 24 * 60)
    name_locale = _choice(options.get("names"), NAME_LOCALES)
    name_place = str(options.get("names_place") or "").strip()
    base = S.base_url(options.get("url")) if source == "birdnet_go" else ""
    token = str(options.get("token") or "").strip()
    fal_key = str((settings or {}).get("fal_api_key") or "").strip()
    pictures = _choice(options.get("pictures"), PICTURES)
    style = str(options.get("style") or "gould")
    how = ""
    if style == "builder":
        # the words ride in every job, since a job held for later or queued past a restart
        # cannot find them in this process's register and would paint the example instead
        how = P.build_how(options)
        art_style = P.register_custom(how)
    else:
        art_style = _choice(style, P.STYLE_IDS)
    only_painted = options.get("unpainted") == "hide"
    dapper = options.get("dapper") in (True, "true", "True", 1, "1", "on")
    pose = _choice(options.get("pose"), ("mixed", "perched", "flight"))
    hero_note = options.get("hero_note", True) not in (False, "false", "False", 0, "0", "off")
    hero_frame = _choice(options.get("hero_frame"), ("plain", "ornate", "painted"))
    if hero_frame == "painted" and pictures != "gen":
        hero_frame = "plain"
    model = P.model_of(options.get("model"))
    arrangement = (
        _choice(options.get("arrangement"), ARRANGEMENTS) if pictures == "gen" else "sheet"
    )
    scene_text = " ".join(str(options.get("scene") or "").split())[:600]
    fidelity = _choice(options.get("scene_fidelity"), ("free", "exact"))
    scene_every = int(_choice(options.get("scene_every"), ("3", "1", "6", "24")))
    # the sheet this cell last sent back, inlined when it is still the one these inputs make
    cell_id = hashlib.sha1(
        json.dumps({k: v for k, v in sorted(options.items()) if k != "token"}, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    subject = subject_id(options)
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
            "scale": scale,
            "layout": layout,
            "panel": levels,
            "footer": footer,
            "header": header,
            "night": night,
            "sky": {"dark": False},
            "asleep": None,
            "day_page": None,
            "year": None,
            "marks": marks,
            "tally": tally,
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
            "cell_id": cell_id,
            "sheet": None,
            "arrangement": arrangement,
            "scene": None,
            "hero_frame": hero_frame if layout == "hero" else "plain",
            "hero_note": hero_note,
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
    today_str = now.strftime("%Y-%m-%d")
    if not demo and source != "birdnet_go":
        _rarity(data_dir, rows, today_str)
    _order_rows(rows, order, source, data_dir, today_str)
    _mark_rows(rows, data_dir, marks, today_str)

    # --- night (BirdNET-Go) ----------------------------------------------
    sky = _sky(place, now) if source == "birdnet_go" else {"dark": False}
    asleep = None
    day_page = None
    if day_pages == "on" and source == "birdnet_go" and rows:
        day_page = _day_page(rows, sky, now)
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

    year = None
    if layout == "year":
        rows, year = _year_rows(data_dir, rows, today_str, source)
        count = YEAR_MAX
    shown = [dict(r) for r in rows[:count]]
    if not ctx.get("preview"):
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
            "only_painted": only_painted,
            "ref_mode": "photo",
            "pose": pose,
            "plants": P.plants_for(*place[:2]) if place else [],
            # a preview looks: a painting it would start is held for Paint now in the job tray,
            # so an option tried in the editor costs nothing; one that stands SETTLE_S is kept
            "hold": bool(ctx.get("preview")) and not preview_settled(data_dir, cell_id, time.time()),
            "cell": cell_id,
            "subject": subject,
            "origin": "preview" if ctx.get("preview") else "panel",
            "keys": [],
            # a panel paints everything its sheet needs at once, within the daily limit; a
            # preview a few at a time
            "per_fetch": P.per_fetch() if ctx.get("preview") else SHEET_MAX,
            "hero_sci": (shown[0].get("sci") or "") if layout == "hero" and hero_frame == "painted" else "",
            "hero_aspect": "4:3" if int(ctx.get("cell_w") or 800) >= int(ctx.get("cell_h") or 480) else "3:4",
            "dapper_sci": P.dapper_pick(
                [r["sci"] for r in shown if r.get("sci")], now.strftime("%Y-%m-%d")
            )
            if dapper
            else "",
        }
    if gen and not demo and not ctx.get("preview"):
        _note_wanted(data_dir, gen)
    mask_states = _attach_pictures(
        shown, base, token, data_dir, levels, fal_key, gen, True
    )
    if gen:
        # one line in the server log per fetch, so a panel without paintings can be read:
        # how many were painted, started, held for Paint now, waiting on the limit or a key
        logger.info(
            "specimen_sheet: %s render, %d species, painted %d, started %d, held %d, capped %d, no key %d, failed %d",
            "preview" if ctx.get("preview") else "panel",
            len(shown),
            mask_states.get("painted", 0),
            mask_states.get("paint_running", 0),
            mask_states.get("paint_held", 0),
            mask_states.get("paint_capped", 0),
            mask_states.get("paint_no_key", 0),
            mask_states.get("paint_failed", 0),
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
                hint=_unpainted_hint(unpainted, mask_states, bool(fal_key)),
                sky=sky,
                demo=demo,
                live_refresh_s=_live_refresh(mask_states),
            )
    _size(shown, sizing)
    if layout == "hero" and hero_note and shown and shown[0].get("sci"):
        shown[0]["about"] = P.about(shown[0]["sci"], data_dir)
    for r in shown:
        r["count_text"] = _count_text(r, source, window)
        r["lines"] = _label_lines(r, lines, source, window)
        if tally == "strokes":
            r["tally_line"] = r["count_text"]
            r["tally"] = int(r.get("year_days") or r.get("count") or 0)
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
    if mask_states.get("deferred"):
        n = mask_states["deferred"]
        status.append(f"{n} picture{'' if n == 1 else 's'} on the way")
    elif waiting:
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
            n = len([j for j in held(data_dir).values() if j.get("subject") == subject])
            status.append(f"{n} painting{'' if n == 1 else 's'} waiting for Paint now in the job tray")
            T.waiting(n, lambda: start_held(data_dir, fal_key, P.daily_cap(settings), subject))
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
            shown, gen, scene_text, fidelity, scene_every, fal_key, data_dir, levels, ctx
        )
        status += [m_ for m_ in more if m_ not in status]
    # a preview in a new look drops what the previews of this cell held or queued for the old one
    if gen and ctx.get("preview") and not demo:
        cull(data_dir, subject, set(gen.get("keys") or []))
    if gen and not demo and not ctx.get("preview"):
        _note_dashboard(data_dir, ctx, subject, gen, shown)
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
        day_page=day_page,
        year=year,
        state="asleep" if asleep else "ok",
        live_refresh_s=_live_refresh(mask_states),
        credits=([scene_out["pic"]["credit"]] if scene_out else credits),
        notices=notices,
        status=status,
        demo=demo,
        sheet=stored_sheet(data_dir, cell_id),
        scene=scene_out,
    )


def _scene(
    shown: list[dict[str, Any]],
    gen: dict[str, Any],
    text: str,
    fidelity: str,
    every_h: int,
    fal_key: str,
    data_dir: Path,
    levels: str,
    ctx: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """The sheet repainted as one scene: once every specimen is painted and
    cut out, their cut-outs go on a collage and fal.ai repaints it in the
    scene ``text`` asks for, at most once per ``every_h`` hours for a given
    line-up. Until then the sheet shows. (the scene payload or None, status)."""
    folder = P.gen_dir(data_dir)
    keys = [
        str(r["pic"]["key"])[2:]
        for r in shown
        if (r.get("pic") or {}).get("kind") == "painting"
    ]
    if len(keys) < 2 or len(keys) < len(shown):
        return None, ["composing the scene once every specimen is painted"]
    if not all(
        (r.get("pic") or {}).get("cut_state") in ("ready", "bad") for r in shown
    ):
        return None, ["composing the scene once every specimen is cut out"]
    w, h = int(ctx.get("cell_w") or 800), int(ctx.get("cell_h") or 480)
    ratio = w / h if h else 1.6
    text = text or P.SCENES["auto"]
    # a scene where the specimens stand on something: the model sets them down,
    # since the collage's ring would leave half of them in the air
    grounded = any(word in text.lower() for word in SCENE_WORDS_GROUNDED)
    if grounded:
        fidelity = "free"
    made = P.collage(keys, data_dir, ratio, layout="ground" if grounded else "ring")
    if not made:
        return None, ["composing the scene"]
    scene_id = hashlib.sha1(text.encode()).hexdigest()[:12]
    skey = P.scene_key(gen["style"], scene_id, fidelity, gen.get("model", P.DEFAULT_MODEL))
    gen.setdefault("keys", []).append(skey)
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
                    {**b, "name": by_key[b["key"]]["name"], "sci": by_key[b["key"]]["sci"]}
                    for b in boxes
                ],
                "names": [
                    f"the {by_key[b['key']]['name']} ({by_key[b['key']]['sci']})"
                    for b in boxes
                ],
                "scene_text": text,
                "plants": ", ".join(gen.get("plants") or []),
                "fidelity": fidelity,
                "aspect": "4:3" if ratio >= 1 else "3:4",
                "subject": gen.get("subject", ""),
                "origin": gen.get("origin", "panel"),
            }
            if gen.get("hold"):
                _hold(data_dir, job, str(gen.get("cell") or ""))
                status.append("the scene waits for Paint now")
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
            "credit": str(meta.get("credit") or "AI scene (fal.ai)"),
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


SHEETS_KEPT = 40


def _sheets_dir(data_dir: Path) -> Path:
    path = data_dir / "sheets"
    with contextlib.suppress(OSError):
        path.mkdir(parents=True, exist_ok=True)
    return path


def _decodes(src: str) -> bool:
    """Whether a sheet's picture is an image this server can open, at least 100
    px each way, with something on it: a browser that could not draw into its
    canvas still encodes a page of plain paper, and that is not a sheet."""
    m = re.match(r"data:image/(?:jpeg|png);base64,([A-Za-z0-9+/=\s]+)$", src)
    if not m:
        return False
    try:
        import base64
        import io

        from PIL import Image

        raw = base64.b64decode(m.group(1))
        Image.open(io.BytesIO(raw)).verify()
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if im.size[0] < 100 or im.size[1] < 100:
            return False
        small = im.resize((80, max(1, round(80 * im.size[1] / im.size[0]))))
        px = list(small.getdata())
        r0, g0, b0 = px[0]
        ink = sum(1 for r, g, b in px if abs(r - r0) + abs(g - g0) + abs(b - b0) > 60)
        return ink > len(px) * 0.003
    except Exception:  # noqa: BLE001 - anything that is not a picture
        return False


def stored_sheet(data_dir: Path, cell_id: str) -> dict[str, Any] | None:
    """The sheet the page last sent back for this cell: ``{key, sheet}``, the key
    being the page's own digest of what went into it, so the page can tell
    whether it is still the sheet these inputs make."""
    path = _sheets_dir(data_dir) / f"{cell_id}.json"
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(got, dict) and got.get("key") and isinstance(got.get("sheet"), dict)):
        return None
    # a sheet kept before the ink check, by a browser that drew nothing into it, goes
    if not _decodes(str(got["sheet"].get("src") or "")):
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    return got


def store_sheet(data_dir: Path, cell_id: str, key: str, sheet: dict[str, Any]) -> None:
    """Keep a finished sheet for its cell, the oldest cells' sheets dropped past
    ``SHEETS_KEPT``."""
    folder = _sheets_dir(data_dir)
    (folder / f"{cell_id}.json").write_text(
        json.dumps({"key": key, "sheet": sheet}), encoding="utf-8"
    )
    kept = sorted(folder.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in kept[SHEETS_KEPT:]:
        with contextlib.suppress(OSError):
            old.unlink()


_SLUG_RE = re.compile(r"[a-z0-9_]{1,60}(?:--[a-z0-9_]{1,24}){0,7}")
_ARCHIVED_RE = re.compile(r"([a-z0-9_]{1,60}(?:--[a-z0-9_]{1,24}){0,7})__(\d{8}-\d{6})")


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
    # the paintings grouped by the dashboard showing them, in the sheet's order, then the rest
    groups: list[dict[str, Any]] = []
    placed: set[str] = set()
    for d in dashboards(data_dir):
        have = [entries[k] for k in d["keys"] if k in entries]
        if not have:
            continue
        placed.update(e["slug"] for e in have)
        groups.append(
            {
                "name": d.get("name") or "",
                "icon": d.get("icon") or "",
                "style_label": d.get("style_label") or "",
                "paintings": have,
                "missing": len([k for k in d["keys"] if k not in entries or not entries[k]["active"]]),
            }
        )
    rest = [e for e in ordered if e["slug"] not in placed]
    return {
        "paintings": ordered,
        "groups": groups,
        "rest": rest,
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
    """The styles, each with a painting to preview it: the
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
            "name": "Styles",
            "subs": [
                {
                    "name": group,
                    "styles": [
                        {
                            "id": i,
                            "label": P.STYLES[i][0],
                            "file": shown.get(i, (False, ""))[1],
                        }
                        for i in ids
                    ],
                }
                for group, ids in P.STYLE_GROUPS
            ],
        }
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
            wanted=len(wanted_configs(dd)),
            held=len(held(dd)),
        )

    @bp.post("/paint-held")
    def paint_held() -> Any:
        key = fal_key()
        if not key:
            flash("Add a fal.ai key in this widget's settings first.", "warn")
            return back()
        n = start_held(data_dir(), key, cap())
        flash(f"Painting {n} held painting{'' if n == 1 else 's'}." if n else "Nothing is waiting, or to-day's limit is used up.", "success" if n else "info")
        return back()

    @bp.post("/purge")
    def purge() -> Any:
        got = purge_all(data_dir())
        flash(
            f"Gone: {got['paintings']} painting{'' if got['paintings'] == 1 else 's'}, {got['versions']} earlier version{'' if got['versions'] == 1 else 's'}, {got['cut_outs']} cut-out{'' if got['cut_outs'] == 1 else 's'}.",
            "success",
        )
        return back()

    @bp.get("/beacon")
    def beacon() -> Any:
        """A word from a page about what it drew, into the server log; the
        renderer's own browser can say it too, since a GET is allowed through."""
        what = re.sub(r"[^a-z_]", "", str(request.args.get("what") or ""))[:20]
        bits = {k: str(v)[:120] for k, v in request.args.items() if k != "what"}
        logger.info("specimen_sheet: page %s %s", what or "?", " ".join(f"{k}={v}" for k, v in sorted(bits.items())))
        return ("", 204)

    @bp.post("/sheet")
    def sheet_back() -> Any:
        """The page sends the sheet it packed; the next fetch for that cell inlines it."""
        body = request.get_json(silent=True) or {}
        cell = str(body.get("cell") or "")
        key = str(body.get("key") or "")
        sheet = body.get("sheet")
        if not re.fullmatch(r"[0-9a-f]{16}", cell) or not key or not isinstance(sheet, dict) or not isinstance(sheet.get("src"), str):
            abort(400)
        if len(sheet["src"]) > 4_000_000:
            abort(413)
        if not _decodes(sheet["src"]):
            abort(400)
        store_sheet(data_dir(), cell, key[:200], sheet)
        return jsonify({"ok": True})

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
        retired(retire(list(dict.fromkeys(request.form.getlist("keys")))))
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

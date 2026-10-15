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

COMMONS_FILE = "https://commons.wikimedia.org/wiki/Special:FilePath/"

SOURCES = S.SOURCES
WINDOWS = ("today", "week")
DAYS = {"7d": 7, "14d": 14, "30d": 30, "90d": 90, "365d": 365}
RADII = ("10", "2", "5", "25", "50")
QUALITIES = ("research", "")
SIZINGS = ("true", "busy", "same")
LABELS = ("caption", "tag", "key", "none")
LABEL_LINES = ("name", "sci", "count", "last", "flag", "who", "size")
TEXT_SCALES = ("100", "60", "80", "125", "150")
PAPERS = ("cream", "white", "aged")
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
SHEET_MAX = 16
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
        "file": str(colour),
    }


def _attach_pictures(
    rows: list[dict[str, Any]],
    base: str,
    token: str,
    data_dir: Path,
    levels: str,
    fal_key: str = "",
) -> dict[str, int]:
    """Each row's best picture to-day: a plate from the pack, else the
    record's own photo (or BirdNET-Go's thumbnail, or, for a bird, the first
    openly licensed iNaturalist photo). Fetched side by side. Then its
    cut-out: a kept mask rides along as ``cut``; a picture without one
    starts a mask job in the background (a few per refresh, with a key) and
    the page cuts it out itself meanwhile. Returns the mask states counted."""
    pack = _pack(data_dir)

    def best(row: dict[str, Any]) -> dict[str, Any] | None:
        return _plate(row["sci"], pack, data_dir, levels) or _photo(
            row, base, token, data_dir, levels
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        pics = list(pool.map(best, rows))
    states: dict[str, int] = {}
    started = 0
    for row, pic in zip(rows, pics, strict=True):
        row["pic"] = pic
        if not pic or not pic.get("key"):
            continue
        st = M.state(pic["key"], data_dir)
        if st == "ready":
            pic["cut"] = M.cut_url(pic["key"], data_dir, levels)
            st = "ready" if pic["cut"] else "failed"
        elif st == "none" and fal_key and started < M.PER_FETCH:
            try:
                picture = Path(pic["file"]).read_bytes()
            except OSError:
                picture = b""
            if picture and M.start(pic["key"], picture, data_dir, fal_key):
                started += 1
                st = "running"
        elif st == "none" and not fal_key:
            st = "no_key"
        if st == "bad":
            # the model could not find one specimen in it: a soft oval, never a rectangle
            pic["on_paper"] = False
        pic["cut_state"] = st
        pic.pop("file", None)
        states[st] = states.get(st, 0) + 1
    return states


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
    factor, compressed so a wren stays readable beside a crow; the busiest
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
        elif cm:
            r["factor"] = round(max(0.55, min(2.0, (cm / 20.0) ** 0.75)), 2)
        else:
            r["factor"] = float(r.get("factor") or 1.0)


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
            "panel": levels,
            "footer": footer,
            "night": night,
            "sky": {"dark": False},
            "asleep": None,
            "state": "ok",
            "error": "",
            "hint": "",
            "credits": [],
            "notices": [],
            "status": [],
            "font_css": N.font_css(("im_fell", "im_fell_sc", "archivo_narrow")),
            "demo": False,
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
        area = "heard " + period
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
    kept = [r for r in rows if not is_excluded(r, rules)]
    excluded = len(rows) - len(kept)
    rows = kept
    rows.sort(key=lambda r: (-r["count"], r["name"]))

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
    if not rows:
        hint = {
            "birdnet_go": "Nothing heard yet " + period + ".",
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
    mask_states = _attach_pictures(shown, base, token, data_dir, levels, fal_key)
    _size(shown, sizing)
    for r in shown:
        r["count_text"] = _count_text(r, source, window)
        r["lines"] = _label_lines(r, lines, source, window)
        r.pop("hourly", None)
    credits: list[str] = []
    for r in shown:
        c = (r.get("pic") or {}).get("credit")
        if c and c not in credits:
            credits.append(c)
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
        credits=credits,
        notices=notices,
        status=status,
        demo=demo,
    )

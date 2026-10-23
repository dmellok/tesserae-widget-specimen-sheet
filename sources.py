"""The three sources, each reduced to the same species rows before anything
is drawn: BirdNET-Go (what the microphone heard to-day), iNaturalist (what
people found near a place lately) and GBIF (what has been recorded near a
place in this month of the year). The demo garden stands in for BirdNET-Go
until an address is set. Loaded by server.py by path.

A row:
    sci, name, kind (iconic taxon), group, fine (a finer kind label),
    factor (size against the others), count, unit (calls | obs. | records),
    first, last (as text), last_iso, ago_days, flag ("new" | "back" | ""),
    who, place, photo (url), photo_credit, ref, ref_credit (an openly
    licensed picture of this very record, for a painter), thumb (BirdNET-Go's
    own thumbnail path), hourly, conf.
"""

from __future__ import annotations

import importlib.util
import random
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
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

SOURCES = ("birdnet_go", "inaturalist", "gbif")

ERR_UNREACHABLE_BIRDNET = "Couldn't reach BirdNET-Go at {url}."
ERR_AUTH_BIRDNET = (
    "BirdNET-Go asked for a login. Add an API token in this cell's settings."
)
ERR_UNREACHABLE_INAT = "Couldn't reach iNaturalist."
ERR_BUSY_INAT = "iNaturalist is busy (it answered {err}); back on the next refresh."
ERR_UNREACHABLE_GBIF = "Couldn't reach GBIF."
ERR_BUSY_GBIF = "GBIF is busy (it answered {err}); back on the next refresh."

INAT_SPECIES = "https://api.inaturalist.org/v1/observations/species_counts"
INAT_OBS = "https://api.inaturalist.org/v1/observations"
INAT_TAXA = "https://api.inaturalist.org/v1/taxa"
INAT_LICENSED = "cc0,cc-by,cc-by-nc,cc-by-sa,cc-by-nc-sa,cc-by-nd,cc-by-nc-nd"
INAT_SPECIES_PER_PAGE = 60
INAT_OBS_PER_PAGE = 30

GBIF_OCCURRENCES = "https://api.gbif.org/v1/occurrence/search"
GBIF_SPECIES = "https://api.gbif.org/v1/species"
GBIF_BASIS = "HUMAN_OBSERVATION"
GBIF_LIMIT = 150
GBIF_LOOKUP_DAYS = 180


def _hhmm(value: str | None) -> str:
    m = re.search(r"(\d{2}):(\d{2})", str(value or ""))
    return f"{m.group(1)}:{m.group(2)}" if m else ""


def _day_words(iso: str, today: date) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    words = f"{d} {date(2000, mo, 1).strftime('%b')}"
    return words if y >= today.year else f"{words} {y}"


def _days_ago(iso: str, today: date) -> int | None:
    try:
        return max(0, (today - date.fromisoformat(str(iso)[:10])).days)
    except (ValueError, TypeError):
        return None


def _row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sci": "",
        "name": "",
        "kind": "Aves",
        "group": "Birds",
        "fine": "Bird",
        "factor": 1.0,
        "count": 0,
        "unit": "calls",
        "first": "",
        "last": "",
        "last_iso": "",
        "first_iso": "",
        "ever": None,
        "first_ever": "",
        "ago_days": None,
        "flag": "",
        "who": "",
        "place": "",
        "photo": "",
        "photo_credit": "",
        "ref": "",
        "ref_credit": "",
        "thumb": "",
        "hourly": [],
        "conf": 0.0,
    }
    base.update(kw)
    return base


# ----------------------------------------------------------------------
# BirdNET-Go
# ----------------------------------------------------------------------


def base_url(raw: str) -> str:
    raw = str(raw or "").strip().rstrip("/")
    if raw and "://" not in raw:
        raw = "http://" + raw
    return raw


# Fugleramme's rolling windows (its lookback_hours), by the Window option's value; "today"
# (BirdNET-Go's day as it fills) and "week" (the last seven days by date) are the widget's own.
WINDOW_HOURS = {
    "0.25": 0.25,
    "0.5": 0.5,
    "1": 1.0,
    "3": 3.0,
    "6": 6.0,
    "12": 12.0,
    "24": 24.0,
    "72": 72.0,
    "720": 720.0,
    "0": 0.0,
}
# A window under a day is counted off the detections feed, which takes no date filter: this
# many rows at most, as Fugleramme does (api.py _WINDOW_SCAN).
WINDOW_SCAN = 1000
NEW_HOURS = 24  # a bird first heard this recently is new here (Fugleramme's NEW_HOURS)


def window_hours(window: str) -> float | None:
    """The window in hours, 0 for all time; None for "today" and "week"."""
    return WINDOW_HOURS.get(str(window))


def _stamp(value: Any, tz: Any) -> datetime | None:
    """A BirdNET-Go time stamp, read in the cell's zone when it carries no offset (BirdNET-Go
    before 20260712 stamped its summary bare)."""
    try:
        at = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=tz)


def _birdnet_err(err: str | None, base: str) -> str | None:
    if not err:
        return None
    if err == "auth":
        return ERR_AUTH_BIRDNET
    if err == "unreachable":
        return ERR_UNREACHABLE_BIRDNET.format(url=base)
    return f"BirdNET-Go answered {err}. Is the address right?"


def _thumb_of(sci: str) -> str:
    return "/api/v2/media/image/" + urllib.parse.quote(sci) if sci else ""


def _from_summary(
    rows: list[Any], since: datetime | None, tz: Any
) -> list[dict[str, Any]]:
    """Species rows from BirdNET-Go's species summary. Its dates round outwards, so a row
    whose newest call is before ``since`` was not heard in the window (Fugleramme's
    _heard_since); the counts still cover the rounded range."""
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("common_name"):
            continue
        last = _stamp(r.get("last_heard") or r.get("latest_heard"), tz)
        if since is not None and last is not None and last < since:
            continue
        first = _stamp(r.get("first_heard"), tz)
        out.append(
            {
                "name": str(r["common_name"]),
                "sci": str(r.get("scientific_name") or ""),
                "count": int(r.get("count") or 0),
                "hourly": [],
                "first": _hhmm(r.get("first_heard")),
                "last": _hhmm(r.get("last_heard") or r.get("latest_heard")),
                "first_iso": first.isoformat() if first else "",
                "last_iso": last.isoformat() if last else "",
                "conf": round(float(r.get("max_confidence") or 0), 2),
                "new": bool(r.get("is_new_species")),
                "thumb": str(r.get("thumbnail_url") or ""),
            }
        )
    return out


def _from_feed(
    feed: list[Any], since: datetime, min_conf: int, tz: Any
) -> list[dict[str, Any]] | None:
    """A window under a day counted off the newest detections, or None when the feed ran out
    before reaching back to ``since`` and the counts would be short (Fugleramme's _counted)."""
    by: dict[str, dict[str, Any]] = {}
    oldest = None
    for d in feed:
        if not isinstance(d, dict) or not d.get("commonName"):
            continue
        at = _stamp(d.get("timestamp"), tz) or _stamp(
            f"{d.get('date')}T{d.get('time')}", tz
        )
        if at is None:
            continue
        oldest = at if oldest is None or at < oldest else oldest
        if d.get("verified") == "false_positive":
            continue
        conf = float(d.get("confidence") or 0)
        if conf * 100 < min_conf or at < since:
            continue
        sci = str(d.get("scientificName") or "")
        e = by.setdefault(
            sci or str(d["commonName"]),
            {
                "name": str(d["commonName"]),
                "sci": sci,
                "count": 0,
                "hourly": [],
                "first_at": at,
                "last_at": at,
                "conf": 0.0,
                "new": False,
                "thumb": _thumb_of(sci),
            },
        )
        e["count"] += 1
        e["first_at"] = min(e["first_at"], at)
        e["last_at"] = max(e["last_at"], at)
        e["conf"] = max(e["conf"], round(conf, 2))
    if len(feed) >= WINDOW_SCAN and (oldest is None or oldest > since):
        return None
    out = []
    for e in sorted(by.values(), key=lambda e: (-e["count"], e["name"])):
        first, last = e.pop("first_at"), e.pop("last_at")
        e.update(
            first=first.strftime("%H:%M"),
            last=last.strftime("%H:%M"),
            first_iso=first.isoformat(),
            last_iso=last.isoformat(),
        )
        out.append(e)
    return out


def _recent(feed: Any, min_conf: int) -> list[dict[str, Any]]:
    out = []
    for d in feed if isinstance(feed, list) else []:
        if not isinstance(d, dict) or not d.get("commonName"):
            continue
        if d.get("verified") == "false_positive":
            continue
        if float(d.get("confidence") or 0) * 100 < min_conf:
            continue
        out.append(
            {
                "name": str(d["commonName"]),
                "sci": str(d.get("scientificName") or ""),
                "time": _hhmm(d.get("time") or d.get("timestamp")),
                "conf": round(float(d.get("confidence") or 0), 2),
            }
        )
        if len(out) >= 20:
            break
    return out


def from_birdnet(
    base: str,
    token: str,
    min_conf: int,
    window: str = "today",
    today: str = "",
    *,
    now: datetime | None = None,
    life: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    """BirdNET-Go's species for the window: the day as it fills (its daily species, with
    hours), the last seven days by date, or one of Fugleramme's rolling windows, counted as
    Fugleramme counts them: a window under a day off the detections feed when the feed reaches
    back that far, anything longer from the species summary over the dates it touches, rounded
    outward, keeping only the species last heard inside the window; all time is the whole
    summary. With ``life`` (or a rolling window) the station's whole record rides along too, for
    the rarest all time, the newest arrival and the new marks: by scientific name, the count and
    when it was first heard."""
    now = now or datetime.now().astimezone()
    tz = now.tzinfo
    today = today or now.strftime("%Y-%m-%d")
    hours = window_hours(window)
    summary_url = f"{base}/api/v2/analytics/species/summary"
    feed: Any = None
    species: list[dict[str, Any]] | None = None
    life_rows: list[Any] | None = None
    if hours is None and window == "week":
        start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=6)).strftime(
            "%Y-%m-%d"
        )
        got, err = N.get_json(
            f"{summary_url}?start_date={start}&end_date={today}&min_confidence={min_conf}",
            token,
        )
        if err:
            return None, _birdnet_err(err, base)
        if not isinstance(got, list):
            return None, "BirdNET-Go's species summary wasn't in the expected shape."
        species = _from_summary(got, None, tz)
    elif hours is None:
        got, err = N.get_json(
            f"{base}/api/v2/analytics/species/daily?min_confidence={min_conf}", token
        )
        if err:
            return None, _birdnet_err(err, base)
        if not isinstance(got, list):
            return None, "BirdNET-Go's species summary wasn't in the expected shape."
        species = []
        for s in got:
            if not isinstance(s, dict) or not s.get("common_name"):
                continue
            hourly = s.get("hourly_counts")
            first = _hhmm(s.get("first_heard"))
            last = _hhmm(s.get("latest_heard") or s.get("last_heard"))
            species.append(
                {
                    "name": str(s["common_name"]),
                    "sci": str(s.get("scientific_name") or ""),
                    "count": int(s.get("count") or 0),
                    "hourly": [int(h or 0) for h in hourly][:24]
                    if isinstance(hourly, list)
                    else [],
                    "first": first,
                    "last": last,
                    "first_iso": f"{today}T{first}" if first else "",
                    "last_iso": f"{today}T{last}" if last else "",
                    "conf": round(float(s.get("max_confidence") or 0), 2),
                    "new": bool(s.get("is_new_species")),
                    "thumb": str(s.get("thumbnail_url") or ""),
                }
            )
    else:
        life = True
        since = now - timedelta(hours=hours) if hours > 0 else None
        if since is not None and hours < 24:
            feed, err = N.get_json(
                f"{base}/api/v2/detections/recent?limit={WINDOW_SCAN}", token
            )
            if err:
                return None, _birdnet_err(err, base)
            if isinstance(feed, list):
                species = _from_feed(feed, since, min_conf, tz)
        if species is None:
            q = (
                f"?start_date={since.strftime('%Y-%m-%d')}&end_date={today}"
                if since is not None
                else ""
            )
            got, err = N.get_json(summary_url + q, token)
            if err:
                return None, _birdnet_err(err, base)
            if not isinstance(got, list):
                return (
                    None,
                    "BirdNET-Go's species summary wasn't in the expected shape.",
                )
            species = _from_summary(got, since, tz)
            if since is None:
                life_rows = got
    if feed is None:
        feed, _rerr = N.get_json(f"{base}/api/v2/detections/recent?limit=20", token)
    out: dict[str, Any] = {"species": species, "recent": _recent(feed, min_conf)}
    if life:
        if life_rows is None:
            got, _lerr = N.get_json(summary_url, token)
            life_rows = got if isinstance(got, list) else None
        if life_rows is not None:
            ever = {}
            for r in life_rows:
                if isinstance(r, dict) and r.get("scientific_name"):
                    first = _stamp(r.get("first_heard"), tz)
                    ever[str(r["scientific_name"])] = {
                        "count": int(r.get("count") or 0),
                        "first_iso": first.isoformat() if first else "",
                        "thumb": str(r.get("thumbnail_url") or ""),
                    }
            out["life"] = ever
            fresh = now - timedelta(hours=NEW_HOURS)
            for s in species:
                e = ever.get(s["sci"])
                if not e:
                    continue
                s["thumb"] = s.get("thumb") or e["thumb"]
                at = _stamp(e["first_iso"], tz)
                if hours is not None and at is not None and at >= fresh:
                    s["new"] = True
    return out, None


def rows_birdnet(raw: dict[str, Any], window: str) -> list[dict[str, Any]]:
    """BirdNET-Go's species as rows. Every bird is a bird; the sheet's size
    table gives the factor later. ``ever`` and ``first_ever`` are the station's whole record
    of the species (how many calls, when first heard), when it came along."""
    out = []
    life = raw.get("life") if isinstance(raw.get("life"), dict) else {}
    for s in raw.get("species", []):
        out.append(
            _row(
                sci=s.get("sci") or "",
                name=s.get("name") or "",
                kind="Aves",
                group="Birds",
                fine="Bird",
                count=int(s.get("count") or 0),
                unit="calls",
                first=s.get("first") or "",
                last=s.get("last") or "",
                last_iso=s.get("last_iso") or "",
                first_iso=s.get("first_iso") or "",
                ever=(life.get(s.get("sci") or "") or {}).get("count"),
                first_ever=(life.get(s.get("sci") or "") or {}).get("first_iso") or "",
                flag="new" if s.get("new") else "",
                thumb=s.get("thumb") or "",
                hourly=s.get("hourly") or [],
                conf=float(s.get("conf") or 0),
            )
        )
    return out


# (common name, scientific name, weight, pattern). Names follow the eBird /
# Clements spelling BirdNET uses. Pattern: dawn (chorus peak, dusk echo),
# day (steady through daylight), night (owls).
_DEMO: dict[str, list[tuple[str, str, float, str]]] = {
    "au": [
        ("Australian Magpie", "Gymnorhina tibicen", 9, "dawn"),
        ("Rainbow Lorikeet", "Trichoglossus moluccanus", 8, "day"),
        ("Noisy Miner", "Manorina melanocephala", 10, "day"),
        ("Red Wattlebird", "Anthochaera carunculata", 6, "day"),
        ("Laughing Kookaburra", "Dacelo novaeguineae", 3, "dawn"),
        ("Superb Fairywren", "Malurus cyaneus", 4, "dawn"),
        ("Magpie-lark", "Grallina cyanoleuca", 4, "day"),
        ("Gray Butcherbird", "Cracticus torquatus", 3, "dawn"),
        ("Spotted Pardalote", "Pardalotus punctatus", 3, "day"),
        ("Sulphur-crested Cockatoo", "Cacatua galerita", 3, "day"),
        ("Eastern Spinebill", "Acanthorhynchus tenuirostris", 2, "dawn"),
        ("Southern Boobook", "Ninox boobook", 2, "night"),
    ],
    "eu": [
        ("Eurasian Blackbird", "Turdus merula", 9, "dawn"),
        ("European Robin", "Erithacus rubecula", 8, "dawn"),
        ("Great Tit", "Parus major", 7, "day"),
        ("Eurasian Blue Tit", "Cyanistes caeruleus", 6, "day"),
        ("Common Chaffinch", "Fringilla coelebs", 5, "dawn"),
        ("House Sparrow", "Passer domesticus", 7, "day"),
        ("Eurasian Wren", "Troglodytes troglodytes", 4, "dawn"),
        ("Common Wood-Pigeon", "Columba palumbus", 5, "day"),
        ("Eurasian Magpie", "Pica pica", 3, "day"),
        ("Carrion Crow", "Corvus corone", 3, "day"),
        ("European Greenfinch", "Chloris chloris", 2, "day"),
        ("Tawny Owl", "Strix aluco", 2, "night"),
    ],
    "na": [
        ("American Robin", "Turdus migratorius", 9, "dawn"),
        ("Northern Cardinal", "Cardinalis cardinalis", 8, "dawn"),
        ("Blue Jay", "Cyanocitta cristata", 6, "day"),
        ("Black-capped Chickadee", "Poecile atricapillus", 6, "day"),
        ("Mourning Dove", "Zenaida macroura", 5, "dawn"),
        ("House Finch", "Haemorhous mexicanus", 6, "day"),
        ("Song Sparrow", "Melospiza melodia", 5, "dawn"),
        ("Carolina Wren", "Thryothorus ludovicianus", 4, "dawn"),
        ("American Crow", "Corvus brachyrhynchos", 4, "day"),
        ("Downy Woodpecker", "Dryobates pubescens", 3, "day"),
        ("Dark-eyed Junco", "Junco hyemalis", 3, "day"),
        ("Great Horned Owl", "Bubo virginianus", 2, "night"),
    ],
}


def demo_region(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return "na"
    if lat < -8 and lon > 100:
        return "au"
    if -30 <= lon <= 60 and lat > 30:
        return "eu"
    return "na"


def _activity(pattern: str, hour: int) -> float:
    if pattern == "night":
        return 1.0 if hour >= 20 or hour <= 4 else 0.0
    if hour < 5 or hour > 20:
        return 0.0
    if pattern == "dawn":
        return {5: 1.0, 6: 2.6, 7: 2.2, 8: 1.3, 18: 0.9, 19: 1.0}.get(hour, 0.45)
    return 0.5 if hour in (5, 20) else 1.0


def demo_birdnet(
    region: str, now: datetime, hours: float | None = None
) -> dict[str, Any]:
    """A day of detections up to now, the same on every call within a
    minute: hourly counts are seeded per (date, hour), so a finished hour
    never changes between refreshes and the current hour only grows. One
    bird a day is flagged new so that state can be seen too. A window under
    a day (``hours``) counts only the calls inside it; the garden keeps no
    days before to-day, so a longer window is the day."""
    cut = (
        now.hour * 60 + now.minute - round(hours * 60)
        if hours is not None and 0 < hours < 24
        else -1
    )
    day_seed = now.strftime("%Y%m%d") + region
    birds = _DEMO[region]
    new_idx = random.Random(day_seed + "new").randrange(len(birds))
    species = []
    log: list[tuple[int, str]] = []
    for i, (name, sci, weight, pattern) in enumerate(birds):
        if (
            i >= 6
            and pattern != "night"
            and random.Random(day_seed + name).random() < 0.35
        ):
            continue
        hourly = [0] * 24
        for hour in range(now.hour + 1):
            rate = weight * _activity(pattern, hour)
            if rate <= 0:
                continue
            rng = random.Random(f"{day_seed}{name}{hour}")
            k = min(59, int(rate * 0.6 + rng.random() * rate * 0.6))
            minutes = sorted(rng.sample(range(60), k))
            if hour == now.hour:
                minutes = [m for m in minutes if m <= now.minute]
            hourly[hour] = len(minutes)
            log.extend((hour * 60 + m, name) for m in minutes)
        heard = sorted(m for m, n in log if n == name and m >= cut)
        if not sum(hourly) or not heard:
            continue
        rng = random.Random(day_seed + name + "conf")
        species.append(
            {
                "name": name,
                "sci": sci,
                "count": sum(hourly) if cut < 0 else len(heard),
                "hourly": hourly,
                "first": f"{heard[0] // 60:02d}:{heard[0] % 60:02d}",
                "last": f"{heard[-1] // 60:02d}:{heard[-1] % 60:02d}",
                "first_iso": f"{now:%Y-%m-%d}T{heard[0] // 60:02d}:{heard[0] % 60:02d}",
                "last_iso": f"{now:%Y-%m-%d}T{heard[-1] // 60:02d}:{heard[-1] % 60:02d}",
                "conf": round(0.78 + rng.random() * 0.2, 2),
                "new": i == new_idx,
                "thumb": "",
            }
        )
    log = [(m, n) for m, n in log if m >= cut]
    log.sort(reverse=True)
    recent = [
        {"name": name, "time": f"{minute // 60:02d}:{minute % 60:02d}"}
        for minute, name in log[:20]
    ]
    return {"species": species, "recent": recent, "region": region}


# ----------------------------------------------------------------------
# iNaturalist
# ----------------------------------------------------------------------


def inat_query(
    place: tuple[float, float, str] | None,
    radius_km: str,
    login: str,
    taxa: list[str],
    days: int,
    quality: str,
    today: date,
) -> dict[str, str]:
    q: dict[str, str] = {
        "d1": (today - timedelta(days=days)).isoformat(),
        "photo_license": INAT_LICENSED,
        "locale": "en",
    }
    if place:
        q["lat"], q["lng"], q["radius"] = (
            f"{place[0]:.4f}",
            f"{place[1]:.4f}",
            radius_km,
        )
    if login:
        q["user_login"] = login
    if taxa:
        q["taxon_id"] = ",".join(taxa)
    if quality:
        q["quality_grade"] = quality
    return q


def from_inat(q: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    """iNaturalist's species counts and newest observations for a query."""
    sq = urllib.parse.urlencode({**q, "per_page": str(INAT_SPECIES_PER_PAGE)})
    species, err = N.get_json(f"{INAT_SPECIES}?{sq}")
    if err == "unreachable":
        return None, ERR_UNREACHABLE_INAT
    if err:
        return None, ERR_BUSY_INAT.format(err=err)
    if not isinstance(species, dict) or not isinstance(species.get("results"), list):
        return None, "iNaturalist's answer wasn't in the expected shape."
    oq = urllib.parse.urlencode(
        {
            **q,
            "photos": "true",
            "order_by": "observed_on",
            "order": "desc",
            "per_page": str(INAT_OBS_PER_PAGE),
        }
    )
    obs, err = N.get_json(f"{INAT_OBS}?{oq}")
    rows = obs.get("results") if isinstance(obs, dict) and not err else None
    return {
        "species": species["results"],
        "species_total": int(species.get("total_results") or 0),
        "obs": rows if isinstance(rows, list) else [],
        "obs_total": int(obs.get("total_results") or 0)
        if isinstance(obs, dict) and not err
        else 0,
    }, None


def inat_credit(photo: dict[str, Any] | None) -> str:
    """'© Kevin Yonando (CC BY-NC) · iNaturalist' from a photo record."""
    if not photo:
        return "iNaturalist"
    who = str(photo.get("attribution_name") or "").strip() or (
        str(photo.get("attribution") or "").removeprefix("(c) ").split(",")[0].strip()
    )
    lic = str(photo.get("license_code") or "").upper().replace("-", " ")
    return (
        f"© {who}{f' ({lic})' if lic else ''} · iNaturalist" if who else "iNaturalist"
    )


def _medium(url: str) -> str:
    return re.sub(r"/(square|small|thumb)\.", "/medium.", url)


def rows_inat(raw: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Species rows from iNaturalist's two answers: the species counts, each
    with its newest observation's photo, observer and date."""
    newest: dict[int, dict[str, Any]] = {}
    for r in raw.get("obs", []):
        t = r.get("taxon") or {}
        if t.get("id") and t["id"] not in newest:
            newest[t["id"]] = r

    def photo_of(r: dict[str, Any] | None) -> tuple[str, str, bool]:
        p = (r or {}).get("photos") or []
        p0 = p[0] if p and isinstance(p[0], dict) else None
        if not p0 or not p0.get("url"):
            return "", "", False
        return _medium(str(p0["url"])), inat_credit(p0), bool(p0.get("license_code"))

    out = []
    for s in raw.get("species", []):
        t = s.get("taxon") or {}
        name = str(t.get("preferred_common_name") or t.get("name") or "").strip()
        if not name:
            continue
        group, fine, factor = K.kind_of(t)
        n = newest.get(t.get("id"))
        own, own_credit, own_lic = photo_of(n)
        dp = t.get("default_photo") or {}
        dp_url = str(dp.get("medium_url") or _medium(str(dp.get("url") or "")))
        seen = str((n or {}).get("observed_on") or "")
        row = _row(
            sci=str(t.get("name") or ""),
            name=name,
            kind=str(t.get("iconic_taxon_name") or "Animalia"),
            group=group,
            fine=fine,
            factor=factor,
            count=int(s.get("count") or 0),
            unit="obs.",
            last=_day_words(seen, today),
            last_iso=seen,
            ago_days=_days_ago(seen, today) if seen else None,
            who=str(((n or {}).get("user") or {}).get("login") or ""),
            place=str((n or {}).get("place_guess") or "").split(",")[0],
            photo=own or dp_url,
            photo_credit=own_credit if own else inat_credit(dp) if dp_url else "",
        )
        if own and own_lic:
            row["ref"], row["ref_credit"] = own, own_credit
        elif dp_url and dp.get("license_code"):
            row["ref"], row["ref_credit"] = dp_url, inat_credit(dp)
        out.append(row)
    return out


def inat_reference(sci: str, data_dir: Path) -> tuple[str, str]:
    """(image URL, credit) of the first openly licensed iNaturalist photo of
    a species, kept for a month; the last resort for a bird with no plate
    and no thumbnail of its own."""
    folder = data_dir / "img"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"inat_{N.slug(sci)}.json"
    hit = N.read_json(path, None)
    if isinstance(hit, dict):
        try:
            if time.time() - path.stat().st_mtime < 30 * 86400:
                return str(hit.get("url", "")), str(hit.get("credit", ""))
        except OSError:
            pass
    url = credit = ""
    q = urllib.parse.urlencode({"q": sci, "rank": "species", "per_page": 1})
    found, err = N.get_json(f"{INAT_TAXA}?{q}")
    results = found.get("results") if isinstance(found, dict) and not err else None
    if results:
        taxon, err = N.get_json(f"{INAT_TAXA}/{int(results[0].get('id') or 0)}")
        rows = taxon.get("results") if isinstance(taxon, dict) and not err else None
        photos = (
            [tp.get("photo") or {} for tp in (rows[0].get("taxon_photos") or [])]
            if rows
            else []
        )
        photo = next((p for p in photos if p.get("license_code")), None)
        if photo:
            url = str(photo.get("url") or "").replace("/square.", "/large.")
            credit = inat_credit(photo)
    if results is not None:
        N.write(path, __import__("json").dumps({"url": url, "credit": credit}).encode())
    return url, credit


# ----------------------------------------------------------------------
# GBIF
# ----------------------------------------------------------------------


def gbif_query(
    place: tuple[float, float, str], radius_km: str, taxa: list[str], month: int
) -> list[tuple[str, str]]:
    """The occurrence search as GBIF takes it: a circle, the month of the
    year, citizen-science records, a page of 150, and the kinds asked for as
    repeated taxonKey values (a choice may carry several, joined with +)."""
    q = [
        ("geoDistance", f"{place[0]:.4f},{place[1]:.4f},{radius_km}km"),
        ("month", str(month)),
        ("basisOfRecord", GBIF_BASIS),
        ("limit", str(GBIF_LIMIT)),
    ]
    for t in taxa:
        for key in t.split("+"):
            if key.isdigit():
                q.append(("taxonKey", key))
    return q


def _recorder(value: Any) -> str:
    who = str(value or "").strip().split("|")[0].split(";")[0].strip()
    if re.fullmatch(r"[0-9a-f]{32,}", who):
        return ""
    return who[:40]


def _licence_words(value: str) -> str:
    v = str(value or "").strip()
    m = re.search(
        r"creativecommons\.org/(licenses|publicdomain)/([a-z-]+)/([0-9.]+)", v
    )
    if m:
        kind = (
            "CC0"
            if m.group(1) == "publicdomain"
            else "CC " + m.group(2).upper().replace("-", " ")
        )
        return f"{kind} {m.group(3)}"
    return v.replace("http://", "").replace("https://", "")[:24]


def gbif_credit(m: dict[str, Any]) -> str:
    who = str(
        m.get("rightsHolder") or m.get("creator") or m.get("publisher") or ""
    ).strip()
    lic = _licence_words(str(m.get("license") or ""))
    return f"© {who}{f' ({lic})' if lic else ''} · GBIF" if who else "GBIF"


def _still(media: Any) -> dict[str, Any] | None:
    for m in media if isinstance(media, list) else []:
        if (
            not isinstance(m, dict)
            or str(m.get("type") or "StillImage") != "StillImage"
        ):
            continue
        url = str(m.get("identifier") or "")
        if not url.startswith("http"):
            continue
        return {
            **m,
            "identifier": re.sub(r"/original\.(jpe?g|png)$", r"/medium.\1", url),
        }
    return None


def from_gbif(q: list[tuple[str, str]]) -> tuple[dict[str, Any] | None, str | None]:
    """GBIF's page of occurrences aggregated by species: count, the names
    GBIF gives it, its newest record (when, who, where, a picture); most
    recorded first."""
    answer, err = N.get_json(f"{GBIF_OCCURRENCES}?{urllib.parse.urlencode(q)}")
    if err == "unreachable":
        return None, ERR_UNREACHABLE_GBIF
    if err:
        return None, ERR_BUSY_GBIF.format(err=err)
    if not isinstance(answer, dict) or not isinstance(answer.get("results"), list):
        return None, "GBIF's answer wasn't in the expected shape."
    by: dict[str, dict[str, Any]] = {}
    for r in answer["results"]:
        if not isinstance(r, dict):
            continue
        sci = str(r.get("species") or "").strip()
        if not sci:
            continue
        when = str(r.get("eventDate") or "")[:10]
        rec = {
            "species": sci,
            "speciesKey": r.get("speciesKey"),
            "vernacularName": str(r.get("vernacularName") or "").strip() or None,
            "eventDate": when,
            "recordedBy": _recorder(r.get("recordedBy")),
            "place": str(r.get("locality") or r.get("stateProvince") or "")
            .split(",")[0]
            .strip(),
            "media": _still(r.get("media")),
            "taxon": K.taxon_of_gbif(r),
        }
        e = by.setdefault(sci, {**rec, "count": 0})
        e["count"] += 1
        if not e.get("vernacularName") and rec["vernacularName"]:
            e["vernacularName"] = rec["vernacularName"]
        if not e.get("media") and rec["media"]:
            e["media"] = rec["media"]
        if when > str(e.get("eventDate") or ""):
            e.update(eventDate=when, recordedBy=rec["recordedBy"], place=rec["place"])
            if rec["media"]:
                e["media"] = rec["media"]
    species = sorted(by.values(), key=lambda e: (-e["count"], e["species"]))
    return {
        "count": int(answer.get("count") or 0),
        "sample": len(answer["results"]),
        "species": species,
    }, None


def _gbif_lookup(path: Path, url: str) -> Any:
    """A GBIF species lookup kept on disk for half a year; None when it
    could not be had (and not asked again for a day)."""
    try:
        age = time.time() - path.stat().st_mtime
        hit = N.read_json(path, "missing")
        if hit != "missing" and (
            age < GBIF_LOOKUP_DAYS * 86400 and hit is not None or age < 86400
        ):
            return hit
    except OSError:
        pass
    answer, err = N.get_json(url)
    if err:
        N.write(path, b"null")
        return None
    N.write(path, __import__("json").dumps(answer).encode())
    return answer


def _gbif_name(key: Any, data_dir: Path) -> str:
    if not key:
        return ""
    got = _gbif_lookup(
        data_dir / f"names_{int(key)}.json",
        f"{GBIF_SPECIES}/{int(key)}/vernacularNames?limit=50",
    )
    rows = got.get("results") if isinstance(got, dict) else None
    for r in rows or []:
        if (
            isinstance(r, dict)
            and r.get("language") in ("eng", "")
            and r.get("vernacularName")
        ):
            return str(r["vernacularName"]).strip()
    return ""


def _gbif_media(key: Any, data_dir: Path) -> dict[str, Any] | None:
    if not key:
        return None
    got = _gbif_lookup(
        data_dir / f"media_{int(key)}.json", f"{GBIF_SPECIES}/{int(key)}/media?limit=20"
    )
    rows = got.get("results") if isinstance(got, dict) else None
    return _still(rows)


def rows_gbif(
    raw: dict[str, Any], today: date, data_dir: Path, lookups: int = 24
) -> list[dict[str, Any]]:
    """Species rows from the aggregated answer. A species the records name
    no common name or picture for is looked up on GBIF, the first
    ``lookups`` of them side by side."""
    entries = [
        e for e in raw.get("species", []) if isinstance(e, dict) and e.get("species")
    ]
    need = [
        e
        for e in entries[:lookups]
        if not e.get("vernacularName") or not e.get("media")
    ]
    names: dict[Any, str] = {}
    media: dict[Any, dict[str, Any] | None] = {}
    if need:
        with ThreadPoolExecutor(max_workers=6) as pool:
            got_names = list(
                pool.map(
                    lambda e: (
                        _gbif_name(e.get("speciesKey"), data_dir)
                        if not e.get("vernacularName")
                        else ""
                    ),
                    need,
                )
            )
            got_media = list(
                pool.map(
                    lambda e: (
                        _gbif_media(e.get("speciesKey"), data_dir)
                        if not e.get("media")
                        else None
                    ),
                    need,
                )
            )
        for e, n, m in zip(need, got_names, got_media, strict=True):
            names[e.get("speciesKey")] = n
            media[e.get("speciesKey")] = m
    out = []
    for e in entries:
        group, fine, factor = K.kind_of(e.get("taxon"))
        key = e.get("speciesKey")
        name = str(e.get("vernacularName") or names.get(key) or "")
        pic = e.get("media") if isinstance(e.get("media"), dict) else media.get(key)
        seen = str(e.get("eventDate") or "")
        row = _row(
            sci=e["species"],
            name=name or e["species"],
            kind=str((e.get("taxon") or {}).get("iconic_taxon_name") or "Animalia"),
            group=group,
            fine=fine,
            factor=factor,
            count=int(e.get("count") or 0),
            unit="records",
            last=_day_words(seen, today),
            last_iso=seen,
            ago_days=_days_ago(seen, today) if seen else None,
            who=str(e.get("recordedBy") or "")[:40],
            place=str(e.get("place") or ""),
            photo=str(pic.get("identifier") or "") if pic else "",
            photo_credit=gbif_credit(pic) if pic else "",
        )
        if pic and re.search(
            r"creativecommons|CC[ -]?(0|BY)", str(pic.get("license") or ""), re.I
        ):
            row["ref"], row["ref_credit"] = row["photo"], row["photo_credit"]
        out.append(row)
    return out

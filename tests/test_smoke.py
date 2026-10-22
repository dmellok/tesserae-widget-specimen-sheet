"""specimen_sheet: the three sources reduced to one row shape, exclusions,
pictures, sizes, labels, the quiet pages, the manifest, and a render smoke
test of client.js in node (where there is no canvas, so the montage
fallback is what renders). No network: every outbound call is patched."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
# The widget paints through Tesserae's ``app.artwork`` (0.463+): the server's
# repo must be importable. A checkout beside this one, or TESSERAE_REPO.
import os  # noqa: E402
import sys  # noqa: E402

_TESSERAE = Path(os.environ.get("TESSERAE_REPO") or ROOT.parent / "tesserae")
if str(_TESSERAE) not in sys.path:
    sys.path.insert(0, str(_TESSERAE))
from app import artwork, fal as fal_mod  # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location("specimen_server", ROOT / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


server = _load()
N, S, K = server.N, server.S, server.K
P, M = server.P, server.M


def _busy() -> bool:
    for c in (P.ART, M.CUTS):
        pr = c.progress(Path("."), {})
        if pr["running"] or pr["queued"]:
            return True
    return False


MELBOURNE = {
    "latitude": -37.81,
    "longitude": 144.96,
    "location": {"label": "Melbourne", "timezone": "Australia/Melbourne"},
}


def _jpeg(w: int = 300, h: int = 400, colour=(200, 120, 40)) -> bytes:
    im = Image.new("RGB", (w, h), (243, 234, 214))
    ImageDraw.Draw(im).ellipse((w * 0.3, h * 0.25, w * 0.7, h * 0.7), fill=colour)
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


BIRDNET_DAILY = [
    {
        "common_name": "Australian Magpie",
        "scientific_name": "Gymnorhina tibicen",
        "count": 52,
        "hourly_counts": [0] * 6 + [9, 12, 8] + [2] * 15,
        "first_heard": "06:12",
        "latest_heard": "17:40",
        "max_confidence": 0.93,
        "is_new_species": False,
        "thumbnail_url": "/api/v2/media/species/magpie.jpg",
    },
    {
        "common_name": "Rock Dove",
        "scientific_name": "Columba livia",
        "count": 14,
        "hourly_counts": [1] * 24,
        "first_heard": "07:01",
        "latest_heard": "16:02",
        "max_confidence": 0.8,
        "is_new_species": True,
        "thumbnail_url": "/api/v2/media/species/dove.jpg",
    },
    {
        "common_name": "Spotted Dove",
        "scientific_name": "Spilopelia chinensis",
        "count": 9,
        "hourly_counts": [1] * 24,
        "first_heard": "07:30",
        "latest_heard": "15:00",
        "max_confidence": 0.8,
    },
    {
        "common_name": "Silver Gull",
        "scientific_name": "Chroicocephalus novaehollandiae",
        "count": 3,
        "hourly_counts": [],
        "first_heard": "09:00",
        "latest_heard": "09:10",
        "max_confidence": 0.7,
    },
]


def _taxon(tid, name, common, iconic, ancestors=(), licensed=True):
    return {
        "id": tid,
        "name": name,
        "preferred_common_name": common,
        "iconic_taxon_name": iconic,
        "rank": "species",
        "ancestor_ids": list(ancestors),
        "default_photo": {
            "id": tid * 10,
            "license_code": "cc-by-nc" if licensed else None,
            "attribution": "(c) A. Naturalist, some rights reserved (CC BY-NC)",
            "attribution_name": "A. Naturalist",
            "medium_url": f"https://inaturalist-open-data.s3.amazonaws.com/photos/{tid * 10}/medium.jpg",
        },
    }


INAT_SPECIES = [
    {
        "count": 52,
        "taxon": _taxon(1, "Agrotis infusa", "Bogong Moth", "Insecta", (47158, 47157)),
    },
    {"count": 30, "taxon": _taxon(2, "Cygnus atratus", "Black Swan", "Aves", (3,))},
    {
        "count": 21,
        "taxon": _taxon(3, "Amanita muscaria", "Fly Agaric", "Fungi", (47170, 50814)),
    },
    {
        "count": 9,
        "taxon": _taxon(
            5,
            "Trichosurus vulpecula",
            "Common Brushtail Possum",
            "Mammalia",
            (40151, 848319),
        ),
    },
]
INAT_OBS = [
    {
        "id": 100,
        "observed_on": "2026-10-13",
        "place_guess": "Hawthorn East, VIC, AU",
        "user": {"login": "paperdaisy16"},
        "taxon": INAT_SPECIES[1]["taxon"],
        "photos": [
            {
                "id": 500,
                "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/500/square.jpg",
                "license_code": "cc-by-nc",
                "attribution": "(c) paperdaisy16, some rights reserved (CC BY-NC)",
            }
        ],
    }
]
GBIF_RESULTS = [
    {
        "species": "Gymnorhina tibicen",
        "speciesKey": 2482593,
        "vernacularName": "Australian Magpie",
        "eventDate": "2025-10-04T09:00:00",
        "recordedBy": "Jane Birder",
        "locality": "Royal Park, Melbourne",
        "kingdom": "Animalia",
        "phylum": "Chordata",
        "class": "Aves",
        "order": "Passeriformes",
        "family": "Artamidae",
        "media": [
            {
                "type": "StillImage",
                "identifier": "https://inaturalist-open-data.s3.amazonaws.com/photos/1/original.jpeg",
                "license": "http://creativecommons.org/licenses/by-nc/4.0/",
                "rightsHolder": "Jane Birder",
            }
        ],
    },
    {
        "species": "Gymnorhina tibicen",
        "speciesKey": 2482593,
        "eventDate": "2025-10-01",
        "kingdom": "Animalia",
        "phylum": "Chordata",
        "class": "Aves",
    },
    {
        "species": "Vanessa kershawi",
        "speciesKey": 1898286,
        "eventDate": "2024-10-20",
        "kingdom": "Animalia",
        "phylum": "Arthropoda",
        "class": "Insecta",
        "order": "Lepidoptera",
        "family": "Nymphalidae",
    },
]


@pytest.fixture
def offline():
    """No network: BirdNET-Go, iNaturalist, GBIF, Commons and the photos all
    answer from here."""
    calls: list[str] = []

    def fake_http(url, token="", accept="application/json", **kw):  # noqa: ARG001
        calls.append(url)
        if "/api/v2/analytics/species/daily" in url or "/species/summary" in url:
            return json.dumps(BIRDNET_DAILY).encode(), None
        if "/api/v2/detections/recent" in url:
            return json.dumps([]).encode(), None
        if "species_counts" in url:
            return json.dumps(
                {"total_results": 273, "results": INAT_SPECIES}
            ).encode(), None
        if "/v1/observations?" in url:
            return json.dumps(
                {"total_results": 744, "results": INAT_OBS}
            ).encode(), None
        if "api.gbif.org/v1/occurrence/search" in url:
            return json.dumps({"count": 196733, "results": GBIF_RESULTS}).encode(), None
        if "api.gbif.org/v1/species/1898286/vernacularNames" in url:
            return json.dumps(
                {
                    "results": [
                        {"language": "eng", "vernacularName": "Australian Painted Lady"}
                    ]
                }
            ).encode(), None
        if "api.gbif.org/v1/species/" in url:
            return json.dumps({"results": []}).encode(), None
        if "inaturalist.org/v1/taxa?" in url:
            return json.dumps({"results": [{"id": 7}]}).encode(), None
        if "inaturalist.org/v1/taxa/7" in url:
            photo = {
                "url": "https://inat.example/photos/1/square.jpg",
                "license_code": "cc-by",
                "attribution_name": "B. Botanist",
            }
            return json.dumps(
                {"results": [{"taxon_photos": [{"photo": photo}]}]}
            ).encode(), None
        if accept == "image/*":
            return _jpeg(), None
        return None, "unreachable"

    with patch.object(N, "http", side_effect=fake_http):
        yield calls


def _fetch(tmp_path, offline, **opts):  # noqa: ARG001
    settings = {"exclude": opts.pop("always_exclude", "")}
    options = {**MELBOURNE, **opts}
    return server.fetch(
        options, settings, ctx={"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    )


# ----------------------------------------------------------------------
# BirdNET-Go
# ----------------------------------------------------------------------


def test_demo_garden_without_an_address(tmp_path, offline):
    r = _fetch(tmp_path, offline)
    assert r["state"] == "ok" and r["demo"] and r["source"] == "birdnet_go"
    assert r["title"] == "Demo garden" and r["period"] == "heard to-day"
    assert 2 <= len(r["species"]) <= 8
    s = r["species"][0]
    assert s["unit"] == "calls" and s["count"] >= s["count"] and s["kind"] == "Aves"
    assert s["lines"][0] == s["name"] and " calls" in s["lines"][1]
    # the magpie has a plate in the pack: on paper, printed by multiply
    magpie = next((x for x in r["species"] if x["sci"] == "Gymnorhina tibicen"), None)
    if magpie:
        assert magpie["pic"]["kind"] == "plate"
        assert magpie["pic"]["src"].startswith("data:image/")
        assert magpie["size_cm"] == 40 and magpie["factor"] > 1
    assert "font-family:'IM Fell English'" in r["font_css"]
    assert not [u for u in offline if "birdnet" in u]


def test_birdnet_rows_and_cache(tmp_path, offline):
    r = _fetch(
        tmp_path,
        offline,
        url="birdnet.local:8080",
        label_lines=["name", "sci", "count", "flag"],
    )
    assert r["state"] == "ok" and not r["demo"]
    assert offline[0].startswith(
        "http://birdnet.local:8080/api/v2/analytics/species/daily?min_confidence=70"
    )
    names = [s["name"] for s in r["species"]]
    assert names[:2] == ["Australian Magpie", "Rock Dove"]
    magpie = r["species"][0]
    assert magpie["lines"] == [
        "Australian Magpie",
        "Gymnorhina tibicen",
        "52 calls · last 17:40",
    ]
    dove = r["species"][1]
    assert dove["flag"] == "new" and dove["lines"][-1] == "new to-day"
    assert (
        magpie["pic"]["kind"] == "plate"
    )  # a plate wins: the thumbnail is never fetched
    assert not any(u.endswith("/magpie.jpg") for u in offline)
    assert dove["pic"]["kind"] in ("photo", "plate")
    if dove["pic"]["kind"] == "photo":
        assert dove["pic"]["credit"] == "via BirdNET-Go"
    n = len(offline)
    r2 = _fetch(tmp_path, offline, url="birdnet.local:8080")
    assert r2["species"][0]["name"] == "Australian Magpie"
    assert not [u for u in offline[n:] if "analytics" in u], "the answer is cached"


def test_birdnet_unreachable_is_an_error_page(tmp_path, offline):
    with patch.object(N, "http", return_value=(None, "unreachable")):
        r = _fetch(tmp_path, offline, url="http://nowhere.local")
    assert r["state"] == "error" and "nowhere.local" in r["error"]


def test_week_window(tmp_path, offline):
    r = _fetch(tmp_path, offline, url="birdnet.local", window="week")
    assert "/species/summary?start_date=" in offline[0]
    assert (
        r["period"] == "heard this week"
        and r["species"][0]["lines"][1] == "52 calls this week"
    )


def test_night_asleep_and_owls(tmp_path, offline):
    late = datetime(2026, 10, 15, 23, 30)
    with patch.object(
        server,
        "_sky",
        return_value={
            "dark": True,
            "sunrise": "06:20",
            "sunset": "19:40",
            "sunset_min": 19 * 60 + 40,
            "moon": 0.5,
            "moon_name": "full moon",
        },
    ):
        with patch.object(server, "datetime") as dt:
            dt.now.return_value = late
            dt.strptime = datetime.strptime
            r = _fetch(tmp_path, offline, url="birdnet.local", night="asleep")
            assert r["state"] == "asleep" and r["asleep"]["since"] == "17:40"
            assert (
                r["asleep"]["last"] == "Australian Magpie"
                and r["asleep"]["quiet_min"] == 350
            )
            r = _fetch(tmp_path, offline, url="birdnet.local", night="owls")
            # nobody called after sunset: asleep too
            assert r["state"] == "asleep"
            r = _fetch(tmp_path, offline, url="birdnet.local", night="off")
            assert r["state"] == "ok"


# ----------------------------------------------------------------------
# iNaturalist and GBIF
# ----------------------------------------------------------------------


def test_inat_rows(tmp_path, offline):
    r = _fetch(
        tmp_path,
        offline,
        source="inaturalist",
        kinds_inat=["3", "47158"],
        days="30d",
        radius_km="25",
        label_lines=["name", "count", "last", "who"],
    )
    assert r["state"] == "ok"
    first = offline[0]
    assert (
        first.startswith(S.INAT_SPECIES)
        and "taxon_id=3%2C47158" in first
        and "radius=25" in first
    )
    assert (
        r["title"] == "Melbourne"
        and r["area"] == "within 25 km"
        and r["period"] == "this month"
    )
    assert r["species_total"] == 273 and r["obs_total"] == 744
    moth = r["species"][0]
    assert (moth["name"], moth["kind"], moth["fine"], moth["unit"]) == (
        "Bogong Moth",
        "Insecta",
        "Moth",
        "obs.",
    )
    assert moth["lines"] == ["Bogong Moth", "52 obs."]
    swan = r["species"][1]
    assert swan["last"] == "13 Oct" and swan["who"] == "paperdaisy16"
    assert swan["lines"] == ["Black Swan", "30 obs.", "13 Oct", "by paperdaisy16"]
    assert (
        swan["ref"].endswith("/500/medium.jpg") and "paperdaisy16" in swan["ref_credit"]
    )
    assert swan["pic"]["kind"] in ("photo", "plate") and not swan["pic"]["on_paper"]
    possum = r["species"][3]
    assert possum["fine"] == "Marsupial" and possum["factor"] == 1.5


def test_inat_needs_a_place_or_a_user(tmp_path, offline):
    r = server.fetch({"source": "inaturalist"}, {}, ctx={"data_dir": str(tmp_path)})
    assert r["state"] == "nowhere"
    r = server.fetch(
        {"source": "inaturalist", "user_login": "kayden"},
        {},
        ctx={"data_dir": str(tmp_path)},
    )
    assert (
        r["state"] == "ok"
        and r["area"] == "by kayden"
        and "user_login=kayden" in offline[0]
    )


def test_gbif_rows(tmp_path, offline):
    r = _fetch(
        tmp_path,
        offline,
        source="gbif",
        kinds_gbif=["212", "220+196"],
        label_lines=["name", "count", "last"],
    )
    assert r["state"] == "ok"
    first = offline[0]
    assert (
        "geoDistance=-37.8100%2C144.9600%2C10km" in first
        and "taxonKey=212" in first
        and "taxonKey=220" in first
        and "taxonKey=196" in first
    )
    assert r["area"] == "within 10 km" and r["period"].startswith("in ")
    magpie, lady = r["species"]
    assert (
        magpie["count"] == 2
        and magpie["unit"] == "records"
        and magpie["lines"] == ["Australian Magpie", "2 records", "4 Oct 2025"]
    )
    assert (
        magpie["who"] == "Jane Birder"
        and "Jane Birder" in magpie["photo_credit"]
        and magpie["pic"]["kind"] == "plate"
    )
    assert (
        lady["name"] == "Australian Painted Lady"
        and lady["fine"] == "Butterfly"
        and lady["kind"] == "Insecta"
    )
    assert r["obs_total"] == 196733


# ----------------------------------------------------------------------
# Exclusions, sizes, labels
# ----------------------------------------------------------------------


def test_scale_and_count_are_sliders_with_bounds(tmp_path, offline):
    m = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    opts = {o["name"]: o for o in m["cell_options"]}
    assert opts["count"]["type"] == "slider" and opts["count"]["max"] == 32
    assert opts["scale"]["type"] == "slider" and (
        opts["scale"]["min"],
        opts["scale"]["max"],
    ) == (40, 250)
    assert _fetch(tmp_path, offline)["scale"] == 100
    assert _fetch(tmp_path, offline, scale="70")["scale"] == 70
    assert _fetch(tmp_path, offline, scale="5")["scale"] == 40
    assert _fetch(tmp_path, offline, scale="500")["scale"] == 250
    big = _fetch(tmp_path, offline, count="16", scale="200")
    assert len(big["species"]) <= 4 and big["scale"] == 200
    assert len(_fetch(tmp_path, offline, count="99")["species"]) <= 32


def test_style_builder_composes_a_custom_style(tmp_path, offline):
    P = server.P
    how = P.build_how(
        {"build_medium": "linocut", "build_palette": "bw", "build_extra": "with a moon"}
    )
    assert how.startswith("a linocut print of the {kind}")
    assert "no grey mid-tones" in how and "with a moon" in how
    assert how.endswith("on plain pure white paper")
    built = _fetch(
        tmp_path, offline, pictures="gen", style="builder", build_medium="linocut"
    )
    prebuilt = _fetch(tmp_path, offline, pictures="gen", style="engraving")
    assert built["art_style"].startswith("custom")
    assert prebuilt["art_style"] == "engraving"


def test_built_style_words_survive_a_hold_and_a_restart(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "style": "builder",
        "build_medium": "linocut",
        "build_extra": "with a crescent moon",
        "count": "2",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480, "preview": True}
    server.fetch(options, settings, ctx=ctx)
    held = list(server.held(tmp_path).values())
    assert len(held) == 2 and all(
        "with a crescent moon" in j["payload"]["how"] for j in held
    )
    # the process forgets its custom register, as a restart would; the held jobs still paint in their words
    server.P._CUSTOM.clear()
    assert server.start_held(tmp_path, "k", 24) == 2
    _settle_paint()
    assert len(offline_paint) == 2
    for pr in offline_paint:
        assert "linocut" in pr["prompt"] and "with a crescent moon" in pr["prompt"]
        assert server.P.CUSTOM_EXAMPLE[:30] not in pr["prompt"]


def test_cut_out_prompts_rule_out_backgrounds():
    P = server.P
    for key in ("gould", "mucha", "folkart", "stainedglass"):
        assert key in P.STYLES
    assert "no background, backdrop, frame" in P.GEN
    for key in ("mucha", "folkart", "stainedglass"):
        assert P.STYLE_REVISIONS.get(key) == 2
        assert "roundel" not in P.STYLES[key][1] and "sunburst" not in P.STYLES[key][1]


def test_order_picks_and_sorts_the_sheet(tmp_path, offline):
    most = [s["count"] for s in _fetch(tmp_path, offline, count="16")["species"]]
    assert most == sorted(most, reverse=True)
    # the rarity log has one day, so rarest falls to the fewest calls
    rare = [
        s["count"]
        for s in _fetch(tmp_path, offline, count="16", order="rarest")["species"]
    ]
    assert rare == sorted(rare)
    latest = [
        s["last"]
        for s in _fetch(tmp_path, offline, count="16", order="latest")["species"]
    ]
    assert latest == sorted(latest, reverse=True)
    mix = [
        s["name"] for s in _fetch(tmp_path, offline, count="16", order="mix")["species"]
    ]
    names = [s["name"] for s in _fetch(tmp_path, offline, count="16")["species"]]
    assert sorted(mix) == sorted(names) and mix == [
        s["name"] for s in _fetch(tmp_path, offline, count="16", order="mix")["species"]
    ]
    assert _fetch(tmp_path, offline, order="nonsense")["species"][0]["count"] == most[0]


def test_exclude_by_name_genus_kind_and_glob(tmp_path, offline):
    r = _fetch(tmp_path, offline, url="birdnet.local", exclude="Columba\n*gull")
    names = [s["name"] for s in r["species"]]
    assert names == ["Australian Magpie", "Spotted Dove"] and r["excluded"] == 2
    r = _fetch(
        tmp_path,
        offline,
        url="birdnet.local",
        exclude="australian magpié, Spilopelia chinensis",
    )
    assert [s["name"] for s in r["species"]] == ["Rock Dove", "Silver Gull"]
    r = _fetch(tmp_path, offline, url="birdnet.local", always_exclude="birds")
    assert r["state"] == "empty" and r["excluded"] == 4
    r = _fetch(tmp_path, offline, source="inaturalist", exclude="Insecta\nfungi")
    assert [s["name"] for s in r["species"]] == [
        "Black Swan",
        "Common Brushtail Possum",
    ]


def test_exclusion_frees_a_place(tmp_path, offline):
    r = _fetch(tmp_path, offline, url="birdnet.local", count="2", exclude="Rock Dove")
    assert [s["name"] for s in r["species"]] == ["Australian Magpie", "Spotted Dove"]


def test_sizes(tmp_path, offline):
    r = _fetch(tmp_path, offline, url="birdnet.local", sizing="true")
    by = {s["sci"]: s for s in r["species"]}
    assert by["Gymnorhina tibicen"]["size_cm"] > by["Columba livia"]["size_cm"]
    assert min(by["Gymnorhina tibicen"]["factor"], by["Columba livia"]["factor"]) > 0.55
    r = _fetch(tmp_path, offline, url="birdnet.local", sizing="same")
    # all alike, but a plate whose bird fills little of its cut-out is still drawn larger for it
    assert {s["factor"] for s in r["species"] if not s["pic"].get("bird")} <= {1.0}


def test_size_line(tmp_path, offline):
    r = _fetch(tmp_path, offline, url="birdnet.local", label_lines=["name", "size"])
    assert r["species"][0]["lines"] == ["Australian Magpie", "L 40 cm"]


def test_labels_none_still_sends_lines(tmp_path, offline):
    r = _fetch(tmp_path, offline, url="birdnet.local", labels="none")
    assert r["labels"] == "none" and r["species"][0]["lines"]


# ----------------------------------------------------------------------
# Manifest and client
# ----------------------------------------------------------------------


def test_manifest_is_well_formed():
    m = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    names = {o["name"] for o in m["cell_options"]}
    assert m["name"] == "Specimen Sheet" and "exclude" in names and "label" not in names
    for o in m["cell_options"]:
        for key in o.get("show_if") or {}:
            assert key in names, f"{o['name']} shows on unknown option {key}"
        if o["type"] in ("select", "multiselect"):
            values = [c["value"] for c in o["choices"]]
            d = o.get("default")
            for v in d if isinstance(d, list) else [d]:
                assert v in values, f"{o['name']} default {v!r} is not a choice"
    assert {s["name"] for s in m["settings"]} == {
        "fal_api_key",
        "gen_daily_usd",
        "gen_per_refresh",
        "gen_parallel",
        "mask_daily_usd",
        "mask_per_refresh",
        "exclude",
    }


@pytest.mark.parametrize("state", ["ok", "empty", "asleep", "error"])
def test_client_renders_every_state(tmp_path, offline, state):
    if state == "ok":
        data = _fetch(tmp_path, offline, url="birdnet.local")
    elif state == "empty":
        data = _fetch(tmp_path, offline, url="birdnet.local", always_exclude="birds")
    elif state == "asleep":
        data = _fetch(tmp_path, offline, url="birdnet.local")
        data.update(
            state="asleep",
            asleep={"since": "17:40", "quiet_min": 350, "last": "Australian Magpie"},
            sky={
                "dark": True,
                "moon": 0.3,
                "moon_name": "waxing crescent",
                "sunrise": "06:20",
            },
        )
    else:
        with patch.object(N, "http", return_value=(None, "unreachable")):
            data = _fetch(tmp_path, offline, url="http://nowhere.local")
    assert data["state"] == state
    expect = {
        "ok": 'class="mont',
        "empty": "Nothing heard",
        "asleep": "asleep since 17:40",
        "error": "nowhere.local",
    }[state]
    script = (
        f"import('file://{ROOT}/client.js').then(async (m) => {{ const s = {{innerHTML: ''}};"
        f" await m.default(s, {{data: {json.dumps(data)}, cell: {{size: 'md', w: 800, h: 480}}}});"
        f" if (!s.innerHTML.includes({json.dumps(expect)})) throw new Error('missing: ' + s.innerHTML.slice(0, 400));"
        " console.log('ok'); })"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


# ----------------------------------------------------------------------
# Cut-outs
# ----------------------------------------------------------------------

M = server.M


def _alpha(w=300, h=400, draw=None):
    im = Image.new("L", (w, h), 0)
    (draw or (lambda d: d.ellipse((80, 100, 220, 300), fill=255)))(ImageDraw.Draw(im))
    return im


def test_mask_check_accepts_one_clean_piece_and_fills_pinholes():
    def blob(d):
        d.ellipse((80, 100, 220, 300), fill=255)
        d.rectangle((150, 200, 153, 203), fill=0)  # a pinhole
        d.ellipse((10, 10, 14, 14), fill=255)  # a speck

    cleaned, why = M.check(_alpha(draw=blob))
    assert cleaned is not None and why == ""
    assert cleaned.getpixel((151, 201)) > 200, "the pinhole is filled"
    assert cleaned.getpixel((12, 12)) < 60, "the speck is dropped"


def test_mask_check_rejects_backgrounds_edges_and_pairs():
    assert (
        "covers"
        in M.check(_alpha(draw=lambda d: d.rectangle((0, 0, 299, 399), fill=255)))[1]
    )
    assert (
        "covers"
        in M.check(_alpha(draw=lambda d: d.ellipse((140, 190, 160, 210), fill=255)))[1]
    )
    assert (
        "edges"
        in M.check(_alpha(draw=lambda d: d.rectangle((0, 0, 299, 250), fill=255)))[1]
    )

    def three(d):
        d.ellipse((10, 150, 90, 250), fill=255)
        d.ellipse((110, 150, 190, 250), fill=255)
        d.ellipse((210, 150, 290, 250), fill=255)

    assert "largest of 3 pieces" in M.check(_alpha(draw=three))[1]

    def pair(d):  # a plate with two birds keeps the larger
        d.ellipse((20, 60, 180, 340), fill=255)
        d.ellipse((200, 150, 280, 250), fill=255)

    cleaned, why = M.check(_alpha(draw=pair))
    assert cleaned is not None and why == ""
    assert cleaned.getpixel((100, 200)) > 200 and cleaned.getpixel((240, 200)) < 40


def _settle(timeout=10.0):
    import time

    end = time.time() + timeout
    while _busy() and time.time() < end:
        time.sleep(0.02)
    assert not _busy()


@pytest.fixture
def offline_masks(offline):
    """fal.ai answers with a mask: a clean oval for most pictures, a frame
    for anything whose request carries the marker."""

    class _Sent(list):
        answers: dict[str, Any] = {}

    sent: Any = _Sent()
    sent.answers = {}
    answers = sent.answers

    def fake_fal(url, body, key, timeout):
        assert key == "k"
        if "birefnet" in url:
            sent.append(json.dumps(body).encode())
            return {"image": {"url": "https://fal.example/mask.png"}}
        handler = answers.get("paint")
        if handler is not None:
            return handler(url, body)
        return {"images": [{"url": "https://fal.example/out.jpg"}]}

    def fake_download(url, timeout=60.0):
        if url == "https://fal.example/mask.png":
            im = Image.new("RGBA", (300, 400), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse((80, 100, 220, 300), fill=(255, 255, 255, 255))
            buf = io.BytesIO()
            im.save(buf, "PNG")
            return buf.getvalue()
        return _jpeg()

    with (
        patch.object(fal_mod, "_http_json", side_effect=fake_fal),
        patch.object(fal_mod, "_download", side_effect=fake_download),
    ):
        yield sent
    P.ART.stop_all(Path("."))
    M.CUTS.stop_all(Path("."))


def test_masks_are_made_once_and_ride_along(tmp_path, offline_masks):
    settings = {"fal_api_key": "k", "exclude": ""}
    options = {**MELBOURNE, "url": "birdnet.local"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, settings, ctx=ctx)
    # classic plates ride in already cut; the one species without one needs a mask
    need = [s for s in r["species"] if not s["pic"].get("classic")]
    assert len(need) == 1 and r["status"] == ["1 cut-out on the way"]
    assert all(s["pic"]["cut_state"] == "running" for s in need)
    _settle()
    assert (
        len(offline_masks) == 1
        and b'"image_url": "data:image/jpeg;base64,' in offline_masks[0]
    )
    assert list((tmp_path / "cutouts").glob("*.png")), (
        "the cut-out is kept in the cutouts collection"
    )
    r = server.fetch(options, settings, ctx=ctx)
    assert r["status"] == [] and all(
        s["pic"]["cut_state"] == "ready" for s in r["species"]
    )
    assert all(
        s["pic"]["cut"].startswith("data:image/png;base64,") for s in r["species"]
    )
    assert len(offline_masks) == 1, "a kept mask is never asked for again"
    assert M.budget_used(tmp_path) == 2, "one cut-out at two cents"
    # a greyscale panel gets the cut-out converted, from the same mask
    r = server.fetch({**options, "panel": "gray4"}, settings, ctx=ctx)
    assert all(s["pic"]["cut"] for s in r["species"]) and len(offline_masks) == 1


def test_masks_need_a_key_and_a_bad_one_is_remembered(tmp_path, offline_masks):
    options = {**MELBOURNE, "url": "birdnet.local"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, {}, ctx=ctx)
    assert server.NOTE_NO_KEY in r["notices"]
    assert not offline_masks and all(
        s["pic"]["cut_state"] == "no_key"
        for s in r["species"]
        if not s["pic"].get("classic")
    )
    # the model answers a mask that is the whole frame: bad, kept as bad, not retried
    real = fal_mod._download.side_effect

    def framed(url, timeout=60.0):
        if url == "https://fal.example/mask.png":
            im = Image.new("RGBA", (300, 400), (255, 255, 255, 255))
            buf = io.BytesIO()
            im.save(buf, "PNG")
            return buf.getvalue()
        return real(url, timeout)

    fal_mod._download.side_effect = framed
    server.fetch(options, {"fal_api_key": "k"}, ctx=ctx)
    _settle()
    bad = [e for e in M.CUTS.errors(tmp_path) if e["why"].startswith(M.BAD)]
    assert len(bad) == 1
    r = server.fetch(options, {"fal_api_key": "k"}, ctx=ctx)
    need = [s for s in r["species"] if not s["pic"].get("classic")]
    assert (
        need and all(s["pic"]["cut_state"] == "bad" for s in need) and not r["status"]
    )
    assert "covers" in M.why_bad(need[0]["pic"]["key"], tmp_path)


# ----------------------------------------------------------------------
# Paintings
# ----------------------------------------------------------------------

P = server.P


def _settle_paint(timeout=15.0):
    _settle(timeout)


@pytest.fixture
def offline_paint(offline_masks):
    """fal.ai paints too: nano-banana answers a picture, Wikipedia a sentence."""
    prompts: list[dict] = []
    real = N.http.side_effect

    def paint_answer(url, body):
        prompts.append({**body, "_url": url})
        return {"images": [{"url": "https://fal.example/out.jpg"}]}

    offline_masks.answers["paint"] = paint_answer  # type: ignore[attr-defined]

    def fake_http(url, token="", accept="application/json", **kw):  # noqa: ARG001
        if "wikipedia.org/api/rest_v1/page/summary/Gymnorhina_tibicen" in url:
            return json.dumps(
                {
                    "extract": "The Australian magpie is a black and white passerine bird native to Australia. It has a solid wedge-shaped bluish-white and black bill."
                }
            ).encode(), None
        if "wikipedia.org" in url:
            return None, "HTTP 404"
        return real(url, token, accept, **kw)

    N.http.side_effect = fake_http
    return prompts


def test_keys_poses_and_dapper():
    assert P.key_for("Gymnorhina tibicen", "gould") in (
        "gymnorhina_tibicen--gould--p2",
        "gymnorhina_tibicen--gould--p2--flight",
    )
    assert P.key_for("Amanita muscaria", "gould", "Fungi").endswith("--gould--p2")
    assert P.key_for("Gymnorhina tibicen", "hats", dapper=True).endswith("--dapper")
    scis = ["Gymnorhina tibicen", "Columba livia", "Spilopelia chinensis"]
    pick = P.dapper_pick(scis, "2026-10-15")
    assert pick in scis and P.dapper_pick(list(reversed(scis)), "2026-10-15") == pick
    assert len({P.dapper_pick(scis, f"2026-10-{d:02d}") for d in range(1, 31)}) > 1
    assert P.custom_tag("x") == P.custom_tag("x") and P.custom_tag("x") != P.custom_tag(
        "y"
    )


def test_only_the_species_on_the_frame_are_painted(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "12"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "style": "engraving",
        "count": 2,
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    shown = {s["sci"] for s in r["species"]}
    assert len(shown) == 2
    assert len(offline_paint) == 2, "no painting for the species left off the sheet"
    assert {sci for _name, sci, _kind in server.on_frame_species(tmp_path)} == shown
    assert len(server.known_species(tmp_path)) > 2, (
        "the list of species seen still holds them all"
    )


def test_paintings_climb_the_ladder(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "12"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "style": "engraving",
        "dapper": True,
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, settings, ctx=ctx)
    # until the paintings land, the plate and the photos show, and the foot says so
    assert (
        r["species"][0]["pic"]["kind"] == "plate" and "painting 1 of 4" in r["status"]
    )
    _settle_paint()
    assert len(offline_paint) == 4, "a panel paints everything its sheet needs at once"
    prompt = next(
        pr["prompt"] for pr in offline_paint if "(Gymnorhina tibicen)" in pr["prompt"]
    )
    assert (
        "copperplate engraving" in prompt
        and "pure white paper" in prompt
        and "(Gymnorhina tibicen)" in prompt
    )
    assert "About the subject: The Australian magpie" in prompt
    assert all(
        pr["image_urls"] == ["https://inat.example/photos/1/large.jpg"]
        for pr in offline_paint
    )
    r = server.fetch(options, settings, ctx=ctx)
    kinds = [s["pic"]["kind"] for s in r["species"]]
    assert kinds == ["painting"] * 4 and len(offline_paint) == 4
    assert not [x for x in r["status"] if "painting" in x]
    assert all(s["pic"]["on_paper"] and "--" in s["pic"]["key"] for s in r["species"])
    # paintings get masks like any other picture
    _settle_paint()
    r = server.fetch(options, settings, ctx=ctx)
    assert all(s["pic"].get("cut") for s in r["species"])
    assert P.budget_used(tmp_path / "paintings") == 16, (
        "four Nano Banana paintings at four cents"
    )
    # switching the style paints again; switching back costs nothing
    r = server.fetch({**options, "style": "gould"}, settings, ctx=ctx)
    _settle_paint()
    assert len(offline_paint) == 8
    r = server.fetch(options, settings, ctx=ctx)
    assert len(offline_paint) == 8 and all(
        s["pic"]["kind"] == "painting" for s in r["species"]
    )


def test_only_painted_leaves_the_rest_off_until_they_land(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "12"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "style": "engraving",
        "unpainted": "hide",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, settings, ctx=ctx)
    assert r["state"] == "painting" and not r.get("species")
    assert "Painting 4 specimens" in r["hint"]
    _settle_paint()
    r = server.fetch(options, settings, ctx=ctx)
    assert len(r["species"]) == 4 and all(
        s["pic"]["kind"] == "painting" for s in r["species"]
    )
    shown = server.N.read_json(tmp_path / "species_shown.json", {})
    assert len(shown) == 4
    r = server.fetch({**options, "unpainted": "show"}, settings, ctx=ctx)
    assert len(r["species"]) == 4


def test_sheet_asks_to_be_refreshed_while_paintings_are_on_the_way(
    tmp_path, offline_paint
):
    settings = {"fal_api_key": "k", "gen_daily_cap": "12"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "style": "engraving",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, settings, ctx=ctx)
    assert r["live_refresh_s"] == server.LIVE_S
    assert r["live_pending"] >= 1, (
        "the paintings on the way are counted for a warm-ahead"
    )
    for _ in range(4):
        _settle_paint()
        r = server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    r = server.fetch(options, settings, ctx=ctx)
    assert r["live_refresh_s"] == 0
    plain = server.fetch({**MELBOURNE, "url": "birdnet.local"}, {}, ctx=ctx)
    assert plain["live_refresh_s"] == 0 and plain["live_pending"] == 0


def test_paintings_need_a_key_and_respect_the_cap(tmp_path, offline_paint):
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, {}, ctx=ctx)
    assert P.NOTE_NO_KEY in r["notices"] and not offline_paint
    assert r["species"][0]["pic"]["kind"] == "plate"
    N.write(
        tmp_path / "paintings" / "budget.json",
        json.dumps({__import__("time").strftime("%Y-%m-%d"): 24}).encode(),
    )
    r = server.fetch(options, {"fal_api_key": "k", "gen_daily_cap": "6"}, ctx=ctx)
    assert not offline_paint and any(
        "budget of US$0.24" in x for x in r["status"] + r["notices"]
    )


def test_scene_is_painted_once_everything_is_cut_out(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "arrangement": "scene",
        "scene": "sitting in a row along a telephone wire against the sky",
        "scene_fidelity": "exact",
        "count": "4",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, settings, ctx=ctx)
    assert (
        r["scene"] is None
        and "composing the scene once every specimen is painted" in r["status"]
    )
    _settle_paint()
    server.fetch(options, settings, ctx=ctx)  # the masks for the four paintings
    _settle_paint()
    r = server.fetch(options, settings, ctx=ctx)
    assert r["scene"] is None and "composing the scene (fal.ai)" in r["status"]
    _settle_paint()
    scene = [pr for pr in offline_paint if "collage of 4 specimens" in pr["prompt"]]
    assert len(scene) == 1
    assert (
        "telephone wire" in scene[0]["prompt"]
        and "Keep every specimen exactly where it is" in scene[0]["prompt"]
    )
    assert (
        scene[0]["image_urls"][0].startswith("data:image/jpeg;base64,")
        and scene[0]["aspect_ratio"] == "4:3"
    )
    assert "the Australian Magpie (Gymnorhina tibicen)" in scene[0]["prompt"]
    r = server.fetch(options, settings, ctx=ctx)
    assert (
        r["scene"]
        and r["scene"]["pic"]["kind"] == "scene"
        and len(r["scene"]["items"]) == 4
    )
    assert r["scene"]["items"][0]["box"] and not r["scene"]["stale"]
    assert (
        r["credits"] == [r["scene"]["pic"]["credit"]] and "AI scene" in r["credits"][0]
    )
    r = server.fetch(options, settings, ctx=ctx)
    assert len([pr for pr in offline_paint if "collage" in pr["prompt"]]) == 1, (
        "the same line-up paints no second scene"
    )
    # a scene with the specimens on the ground frees the model to set them down
    grounded = {**options, "scene": "on the lawn of a cottage garden"}
    r = server.fetch(grounded, settings, ctx=ctx)
    _settle_paint()
    lawn = [pr for pr in offline_paint if "cottage garden" in pr["prompt"]]
    assert len(lawn) == 1 and "Move and resize the specimens" in lawn[0]["prompt"]
    script = (
        f"import('file://{ROOT}/client.js').then(async (m) => {{ const s = {{innerHTML: ''}};"
        f" await m.default(s, {{data: {json.dumps(r)}, cell: {{size: 'md', w: 800, h: 480}}}});"
        " if (!s.innerHTML.includes('sc-legend') || !s.innerHTML.includes('Australian Magpie')) throw new Error(s.innerHTML.slice(0, 300));"
        " console.log('ok'); })"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr


def test_dapper_is_one_species_a_day(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "dapper": True,
        "count": "4",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    dapper = [pr for pr in offline_paint if "is dapper" in pr["prompt"]]
    assert len(dapper) == 1
    assert len(list((tmp_path / "paintings").glob("*--dapper.jpg"))) == 1


def test_pose_and_native_plants(tmp_path, offline_paint):
    P = server.P
    assert (
        P.plants_for(-37.81, 144.96)[0] == "eucalyptus"
        and P.plants_for(51.5, -0.1)[0] == "oak"
    )
    assert P.plants_for(None, None) == [] and P.plants_for(0, -160) == []
    plants = P.plants_for(-37.81, 144.96)
    assert P.perch_for("Gymnorhina tibicen", plants) in plants
    assert P.perch_for("Gymnorhina tibicen", plants) == P.perch_for(
        "Gymnorhina tibicen", plants
    )
    assert P.key_for("Gymnorhina tibicen", "gould", pose="flight").endswith("--flight")
    assert "--on_" in P.key_for(
        "Gymnorhina tibicen", "gould", pose="perched", perch="golden wattle"
    )
    assert P.key_for(
        "Gymnorhina tibicen", "gould", pose="flight", perch="golden wattle"
    ).endswith("--flight")
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "pose": "perched",
        "count": "3",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    assert len(offline_paint) == 3
    for pr in offline_paint:
        assert (
            "in flight" not in pr["prompt"]
            and "a plant of the country where it was seen" in pr["prompt"]
        )
        assert any(p in pr["prompt"] for p in plants)
    names = {f.name for f in (tmp_path / "paintings").glob("*.jpg")}
    assert names and all("--on_" in n for n in names)
    # all in flight: no perch, new paintings
    server.fetch({**options, "pose": "flight"}, settings, ctx=ctx)
    _settle_paint()
    assert len(offline_paint) == 6 and all(
        "in flight" in pr["prompt"] for pr in offline_paint[3:]
    )
    assert all(
        "--flight" in f.name
        for f in (tmp_path / "paintings").glob("*.jpg")
        if "--on_" not in f.name
    )


def test_hero_note_and_painted_backdrop(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {**MELBOURNE, "url": "birdnet.local", "layout": "hero", "count": "4"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, settings, ctx=ctx)
    assert r["hero_note"] and r["hero_frame"] == "plain"
    assert "magpie" in r["species"][0]["about"].lower()
    assert "about" not in r["species"][1]
    r = server.fetch(
        {**options, "hero_note": False, "hero_frame": "ornate"}, settings, ctx=ctx
    )
    assert "about" not in r["species"][0] and r["hero_frame"] == "ornate"
    # painted needs AI paintings; with them the hero gets a plate of its own, shown whole
    r = server.fetch({**options, "hero_frame": "painted"}, settings, ctx=ctx)
    assert r["hero_frame"] == "plain"
    painted = {**options, "hero_frame": "painted", "pictures": "gen"}
    r = server.fetch(painted, settings, ctx=ctx)
    _settle_paint()
    hero = [pr for pr in offline_paint if "ornate framed plate" in pr["prompt"]]
    assert (
        len(hero) == 1
        and "(Gymnorhina tibicen)" in hero[0]["prompt"]
        and hero[0]["aspect_ratio"] == "4:3"
    )
    assert len(list((tmp_path / "paintings").glob("*--hero.jpg"))) == 1
    r = server.fetch(painted, settings, ctx=ctx)
    assert (
        r["species"][0]["pic"]["whole"] and r["species"][0]["pic"]["kind"] == "painting"
    )
    assert not r["species"][1]["pic"].get("whole")
    for frame in ("ornate", "painted"):
        data = (
            r
            if frame == "painted"
            else server.fetch({**options, "hero_frame": "ornate"}, settings, ctx=ctx)
        )
        script = (
            f"import('file://{ROOT}/client.js').then(async (m) => {{ const s = {{innerHTML: ''}};"
            f" await m.default(s, {{data: {json.dumps(data)}, cell: {{size: 'md', w: 800, h: 480}}}});"
            " const h = s.innerHTML; if (!h.includes('hero-note') || !h.includes('hero-also')) throw new Error(h.slice(0, 300));"
            + (
                " if (!h.includes('hero-whole') || !h.includes('hero-title')) throw new Error('no hero page');"
                if frame == "painted"
                else " if (!h.includes('orn-c')) throw new Error('no frame');"
            )
            + " console.log('ok'); })"
        )
        out = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert out.returncode == 0, out.stderr


def test_previews_hold_paintings_for_paint_now(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "count": "3"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480, "preview": True}
    r = server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    assert not offline_paint, "a preview starts nothing"
    assert len(server.held(tmp_path)) == 3
    assert any(
        "3 pictures waiting for Paint now" in x for x in r["status"] + r["notices"]
    )
    assert r["live_refresh_s"] > 0, (
        "the editor keeps coming back, so the settling starts them"
    )
    # the same preview again within the settling time holds nothing new
    server.fetch(options, settings, ctx=ctx)
    assert len(server.held(tmp_path)) == 3 and not offline_paint
    # the host says the dashboard has settled: it paints
    r = server.fetch(options, settings, ctx={**ctx, "settled": True})
    _settle_paint()
    assert len(offline_paint) == 3 and not server.held(tmp_path)
    assert not any("waiting" in x for x in r["status"])
    # a changed look is the same cell in a new look: held again, the old look's held dropped,
    # and Paint now starts that cell's and no other's
    r = server.fetch({**options, "style": "woodblock"}, settings, ctx=ctx)
    assert len(server.held(tmp_path)) == 3 and any(
        "3 pictures waiting" in x for x in r["status"] + r["notices"]
    )
    other = {
        **options,
        "style": "linocut",
        "count": "2",
    }  # another cell: fewer species shown
    server.fetch(other, settings, ctx=ctx)
    assert len(server.held(tmp_path)) == 5
    woodblock = [
        j["subject"]
        for j in server.held(tmp_path).values()
        if j["payload"]["style"] == "woodblock"
    ][0]
    started = server.start_held(tmp_path, "k", 24, woodblock)
    _settle_paint()
    assert started == 3 and len(offline_paint) == 6
    assert {j["payload"]["style"] for j in server.held(tmp_path).values()} == {
        "linocut"
    }
    P.ART.unhold(tmp_path)
    r = server.fetch(options, settings, ctx={**ctx, "preview": False})
    assert all(s["pic"]["kind"] == "painting" for s in r["species"])
    # a panel render with new species starts them all straight away
    more = server.fetch(
        {**options, "count": "4"}, settings, ctx={**ctx, "preview": False}
    )
    _settle_paint()
    assert len(offline_paint) == 7 and not server.held(tmp_path) and more["species"][3]


def test_every_style_is_offered():
    m = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    art = next(o for o in m["cell_options"] if o["name"] == "style")
    assert [c["value"] for c in art["choices"]] == list(P.STYLES) + ["builder"]
    assert len(P.STYLES) == 13
    for k, (label, how) in P.STYLES.items():
        assert "pure white paper" in how, k
        assert label and len(label) < 40, k


def test_models_have_their_own_keys_and_request_shapes(tmp_path, offline_paint):
    assert P.key_for("Gymnorhina tibicen", "gould", model="nano_banana") == P.key_for(
        "Gymnorhina tibicen", "gould"
    )
    assert P.key_for("Gymnorhina tibicen", "gould", model="flux_kontext").endswith(
        "--m_flux_kontext"
    )
    assert P.model_of("nope") == "nano_banana"
    for model in P.MODELS:
        body = fal_mod.request_body(model, "p", ["https://x/y.jpg"], "3:4")
        assert body["prompt"] == "p" and (
            "image_url" in body or body["image_urls"] == ["https://x/y.jpg"]
        )
    assert (
        fal_mod.request_body("gpt_image", "p", ["u"], "4:3")["image_size"]
        == "landscape_4_3"
    )
    real = N.http.side_effect
    urls: list[str] = []

    def spy(url, token="", accept="application/json", **kw):
        urls.append(url)
        return real(url, token, accept, **kw)

    N.http.side_effect = spy
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "model": "flux_kontext",
        "count": "4",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    server.fetch(options, {"fal_api_key": "k"}, ctx=ctx)
    _settle_paint()
    assert any(
        p["_url"] == "https://fal.run/fal-ai/flux-pro/kontext" for p in offline_paint
    )
    keys = [f.stem for f in (tmp_path / "paintings").glob("*--m_flux_kontext.jpg")]
    assert len(keys) == 4
    meta = json.loads((tmp_path / "paintings" / f"{keys[0]}.json").read_text())
    assert (
        meta["model"] == "flux_kontext"
        and "FLUX.1 Kontext Pro on fal.ai" in meta["credit"]
    )
    assert all(e["meta"]["model"] == "flux_kontext" for e in P.ART.listing(tmp_path))
    # the unknown-style fallback is gone: an unknown id fails rather than painting Gould
    assert P.style_of("nope")[1] == ""


def _wait(cond, timeout=5.0):
    import time

    end = time.time() + timeout
    while not cond() and time.time() < end:
        time.sleep(0.02)
    assert cond()


@pytest.fixture
def held_paint(tmp_path):
    """paint() waits on a gate, so the queue can be looked at mid-flight."""
    import threading

    gate = threading.Event()
    seen: list[str] = []

    def slow(job):
        seen.append(job.key)
        gate.wait(5)
        if job.stopped:
            raise artwork.ArtworkError("stopped")
        return _jpeg(), {"credit": "slow", "style": job.payload.get("style", "gould")}

    with patch.object(P, "make", slow):
        yield gate, seen
    gate.set()
    _wait(lambda: not _busy())


def _jobs(n):
    return [
        {"key": f"bird{i}--gould", "style": "gould", "name": f"Bird {i}", "sci": "x"}
        for i in range(n)
    ]


def test_limits_are_the_owners_to_set():
    P, M = server.P, server.M
    assert P.daily_cap({"gen_daily_cap": 500}) == 2000, (
        "an older count of paintings, at four cents each"
    )
    assert P.daily_cap({"gen_daily_cap": "7"}) == 28
    assert P.daily_cap({"gen_daily_cap": 0}) == P.UNLIMITED
    assert P.daily_cap({"gen_daily_usd": "4"}) == 400 and P.daily_cap({}) == 400
    assert P.daily_cap({"gen_daily_cap": "lots"}) == 400, "unreadable: the default"
    P.configure({"gen_per_refresh": 9, "gen_parallel": 1})
    M.configure({"mask_per_refresh": 20})
    assert (
        P.per_fetch(),
        P.ART._parallel,
        M.CUTS.cap({"mask_daily_cap": 0}),
        M.per_fetch(),
    ) == (9, 1, P.UNLIMITED, 20)
    assert M.CUTS.cap({"mask_daily_usd": "6"}) == 600 and M.CUTS.cap({}) == 600
    P.configure({"gen_per_refresh": 0, "gen_parallel": 99})
    assert (P.per_fetch(), P.ART._parallel) == (1, 8)
    P.configure({})
    M.configure({})


def test_perched_birds_keep_a_leafy_branch_and_flying_birds_have_none(tmp_path):
    perched = {
        "key": "x--audubon",
        "style": "audubon",
        "name": "Wren",
        "sci": "Troglodytes troglodytes",
        "kind": "Aves",
    }
    flying = {**perched, "key": "x--audubon--flight"}
    p = P.prompt_for(perched, tmp_path)
    f = P.prompt_for(flying, tmp_path)
    assert "branch with a few leaves" in p and "among a few leaves" in p
    assert "no branch, perch, twig or ground" in f
    assert "branch with a few leaves" not in f and "among a few leaves" not in f
    for style in ("woodblock", "japonisme", "chinoiserie", "artnouveau", "fairytale"):
        job = {**flying, "key": f"x--{style}--flight", "style": style}
        text = P.prompt_for(job, tmp_path)
        # only the pose line may name a branch, and only to rule it out
        assert text.count("branch") == 1 and "no branch" in text, style


# ----------------------------------------------------------------------
# Classic plates (Fugleramme) and the reference picture
# ----------------------------------------------------------------------

C = server.C


def _rgba_webp() -> bytes:
    im = Image.new("RGBA", (300, 400), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((60, 100, 240, 300), fill=(180, 90, 30, 255))
    buf = io.BytesIO()
    im.save(buf, "WEBP")
    return buf.getvalue()


@pytest.fixture
def offline_classic(offline):
    real = N.http.side_effect

    def fake_http(url, token="", accept="application/json", **kw):  # noqa: ARG001
        if "raw.githubusercontent.com/arnegiacomo/fugleramme" in url:
            return _rgba_webp(), None
        return real(url, token, accept, **kw)

    N.http.side_effect = fake_http
    return offline


def test_classic_index_finds_a_species_and_its_variants():
    assert C.species_count() > 600
    dove = C.lookup("Columba livia")
    assert dove and dove["pin"] and len(dove["box"]) == 4
    assert C.lookup("Columba livia domestica") == dove  # a subspecies uses its species
    assert C.lookup("Columba livia") == dove  # the same variant every time
    assert C.lookup("Nonexistus birdus") is None
    assert "CC BY-SA 4.0" in C.credit_of(dove)


def test_classic_plates_ride_in_already_cut(tmp_path, offline_classic):
    r = _fetch(
        tmp_path,
        offline_classic,
        url="birdnet.local",
        sizing="true",
    )
    by = {s["sci"]: s for s in r["species"]}
    dove = by["Columba livia"]["pic"]
    assert dove["kind"] == "plate" and dove["classic"] and not dove["on_paper"]
    assert dove["cut"].startswith("data:image/png") and dove["cut_state"] == "ready"
    assert dove["key"].startswith("c_") and "file" not in dove
    # a bird that is little of its cut-out is drawn larger for it
    assert 0.7 <= dove["bird"] * 2 and by["Columba livia"]["factor"] > 0
    # the foot carries one credit line for all the plates, with the licence
    plate_lines = [c for c in r["credits"] if "Fugleramme" in c]
    assert len(plate_lines) == 1 and "CC BY-SA 4.0" in plate_lines[0]
    assert not any(c.startswith("classic plate after") for c in r["credits"])


def test_reference_picture_modes(tmp_path, offline_classic):
    job = {
        "key": "columba_livia--gould",
        "sci": "Columba livia",
        "kind": "Aves",
        "ref": "https://inat.example/p.jpg",
        "ref_credit": "A. Observer",
    }
    pics, used, credit = P.references({**job, "ref_mode": "photo"}, tmp_path)
    assert (pics, used) == (["https://inat.example/p.jpg"], "photo")
    pics, used, credit = P.references({**job, "ref_mode": "plate"}, tmp_path)
    assert used == "plate" and len(pics) == 1 and pics[0].startswith("data:image/jpeg")
    assert "Fugleramme" in credit
    pics, used, credit = P.references({**job, "ref_mode": "both"}, tmp_path)
    assert used == "both" and pics[0] == job["ref"] and pics[1].startswith("data:")
    assert credit.startswith("A. Observer and a plate by")
    # one picture only for a model that takes one: the photo
    pics, used, _ = P.references({**job, "ref_mode": "both"}, tmp_path, multi=False)
    assert (pics, used) == ([job["ref"]], "photo")
    # in flight, or with no plate for the species, the photo stays
    flight = {**job, "key": "columba_livia--gould--flight", "ref_mode": "plate"}
    assert P.references(flight, tmp_path)[1] == "photo"
    nope = {**job, "sci": "Nonexistus birdus", "ref_mode": "plate"}
    assert P.references(nope, tmp_path)[1] == "photo"


def test_every_option_type_is_one_the_server_installs():
    allowed = {
        "string",
        "textarea",
        "variables_textarea",
        "entity_overrides",
        "location_search",
        "number",
        "slider",
        "select",
        "multiselect",
        "boolean",
        "color",
    }
    manifest = json.loads(
        (Path(__file__).resolve().parent.parent / "plugin.json").read_text(
            encoding="utf-8"
        )
    )
    bad = [
        (o["name"], o["type"])
        for o in manifest["cell_options"]
        if o["type"] not in allowed
    ]
    assert not bad


def test_pictures_past_the_deadline_wait_for_the_next_refresh(tmp_path, offline):
    rows = [
        {"name": n, "sci": c, "count": 1, "kind": "Aves"}
        for n, c in (
            ("Australian Magpie", "Gymnorhina tibicen"),
            ("Rock Dove", "Columba livia"),
        )
    ]
    states = server._attach_pictures(rows, "", "", tmp_path, "colour", budget_s=-1)
    assert states.get("deferred") == 2 and all(r["pic"] is None for r in rows)
    assert server._live(states)["live_refresh_s"] > 0
    rows = [
        {
            "name": "Australian Magpie",
            "sci": "Gymnorhina tibicen",
            "count": 1,
            "kind": "Aves",
        }
    ]
    states = server._attach_pictures(rows, "", "", tmp_path, "colour")
    assert not states.get("deferred") and rows[0]["pic"]


def test_a_stored_sheet_has_to_be_a_picture(tmp_path):
    import base64
    import io
    from PIL import Image
    from PIL import ImageDraw

    page = Image.new("RGB", (120, 160), (243, 234, 214))
    ImageDraw.Draw(page).ellipse((30, 40, 90, 120), fill=(40, 30, 20))
    buf = io.BytesIO()
    page.save(buf, "JPEG")
    good = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    assert server._decodes(good)
    blank = io.BytesIO()
    Image.new("RGB", (120, 160), (243, 234, 214)).save(blank, "JPEG")
    assert not server._decodes(
        "data:image/jpeg;base64," + base64.b64encode(blank.getvalue()).decode()
    ), "plain paper is not a sheet"
    assert not server._decodes("data:image/jpeg;base64,AAAA")
    assert not server._decodes("https://example.com/x.jpg")
    assert not server._decodes(
        "data:text/plain;base64," + base64.b64encode(b"hello").decode()
    )
    tiny = io.BytesIO()
    Image.new("RGB", (8, 8)).save(tiny, "PNG")
    assert not server._decodes(
        "data:image/png;base64," + base64.b64encode(tiny.getvalue()).decode()
    )


def test_a_blank_stored_sheet_is_dropped_when_read(tmp_path):
    import base64
    import io
    from PIL import Image

    blank = io.BytesIO()
    Image.new("RGB", (120, 160), (243, 234, 214)).save(blank, "JPEG")
    src = "data:image/jpeg;base64," + base64.b64encode(blank.getvalue()).decode()
    server.store_sheet(tmp_path, "a" * 16, "k", {"src": src, "items": [{"name": "x"}]})
    assert (tmp_path / "sheets" / ("a" * 16 + ".json")).exists()
    assert server.stored_sheet(tmp_path, "a" * 16) is None
    assert not (tmp_path / "sheets" / ("a" * 16 + ".json")).exists()


def test_purge_takes_everything_but_the_count(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "count": "3"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    gen = tmp_path / "paintings"
    made = len(list(gen.glob("*.jpg")))
    assert made >= 1 and (gen / "budget.json").exists()
    server.fetch(
        {**options, "style": "woodblock"}, settings, ctx={**ctx, "preview": True}
    )
    assert server.held(tmp_path)
    (gen / "archive").mkdir(exist_ok=True)
    (gen / "archive" / "x__20260101-000000.jpg").write_bytes(b"x")
    (tmp_path / "cutouts").mkdir(exist_ok=True)
    (tmp_path / "cutouts" / "x.png").write_bytes(b"x")
    (tmp_path / "cutouts" / "f_keep.png").write_bytes(b"x")
    got = server.purge_all(tmp_path)
    assert (
        got["paintings"] == made
        and got["versions"] == 1
        and got["cut_outs"] == made + 2
    ), "every cut-out goes with the paintings, theirs included"
    assert (
        not [f for f in gen.iterdir() if f.suffix in (".jpg", ".png")]
        and (gen / "budget.json").exists()
    )
    assert not list((tmp_path / "cutouts").glob("*.png"))
    assert not list((gen / "archive").iterdir()) and not server.held(tmp_path)
    assert server.P.budget_used(gen) >= made, "the day's count stays"


def test_a_new_look_drops_the_old_looks_held_paintings(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "count": "3",
        "style": "gould",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480, "preview": True}
    server.fetch(options, settings, ctx=ctx)
    held = server.held(tmp_path)
    assert held and all("--gould" in k for k in held)
    # the same cell, a new style: what was held for the old one goes
    server.fetch({**options, "style": "woodblock"}, settings, ctx=ctx)
    held = server.held(tmp_path)
    assert held and all("--woodblock" in k for k in held), list(held)
    # a different cell (another place) with its own held paintings is left alone
    other = {
        **options,
        "latitude": -33.87,
        "longitude": 151.21,
        "location": {"label": "Sydney", "timezone": "Australia/Sydney"},
    }
    server.fetch(other, settings, ctx=ctx)
    assert any("--gould" in k for k in server.held(tmp_path))
    server.fetch({**options, "style": "linocut"}, settings, ctx=ctx)
    kept = server.held(tmp_path)
    assert (
        any("--gould" in k for k in kept)
        and any("--linocut" in k for k in kept)
        and not any("--woodblock" in k for k in kept)
    )


def test_a_place_without_a_zone_still_has_a_sky(tmp_path, offline):
    """A cell with coordinates but no timezone (backyard-birds #1): the clock
    falls back to the server's zone instead of a naive time the moon
    arithmetic rejects."""
    r = server.fetch(
        {"latitude": -37.81, "longitude": 144.96},
        {},
        ctx={"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480},
    )
    assert r["state"] == "ok" and r["sky"]["moon_name"]
    assert server._moon(datetime(2026, 10, 11, 8, 14))[1]


def test_paintings_group_by_dashboard(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "count": "3",
        "style": "gould",
    }
    ctx = {
        "data_dir": str(tmp_path),
        "cell_w": 800,
        "cell_h": 480,
        "page_name": "Kitchen",
        "page_icon": "ph-coffee",
    }
    server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    server.fetch(
        {**options, "style": "woodblock", "count": "2"},
        settings,
        ctx={**ctx, "page_name": "Study", "page_icon": ""},
    )
    _settle_paint()
    # a preview is not a dashboard
    server.fetch(
        {**options, "style": "linocut"},
        settings,
        ctx={**ctx, "preview": True, "page_name": "Draft"},
    )
    got = P.ART.dashboards(tmp_path)
    assert [d["name"] for d in got] == ["Study", "Kitchen"]
    assert [(d["name"], d["facet_label"], len(d["keys"])) for d in got] == [
        ("Study", "Japanese woodblock print", 2),
        ("Kitchen", "Hand-coloured lithograph (Gould)", 3),
    ]
    assert all(P.ART.path(k, tmp_path) is not None for d in got for k in d["keys"])


def _log(tmp_path, days, seen):
    (tmp_path / "rarity.json").write_text(json.dumps({"days": days, "seen": seen}))


def test_marks_new_and_rare(tmp_path, offline):
    from datetime import date, timedelta

    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(40, 0, -1)]
    # the magpie every day, the rock dove (flagged new by BirdNET-Go) never before, the gull once
    # (one day in forty, plus to-day: within one in twenty)
    _log(
        tmp_path,
        days,
        {"Gymnorhina tibicen": days, "Chroicocephalus novaehollandiae": days[3:4]},
    )
    r = _fetch(tmp_path, offline, url="birdnet.local", marks="both", count="8")
    marks = {s["name"]: s.get("mark", "") for s in r["species"]}
    assert marks["Rock Dove"] == "new" and marks["Australian Magpie"] == ""
    assert marks["Spotted Dove"] == "new", "first on the log to-day: new here"
    assert marks["Silver Gull"] == "rare", "on the log once in forty days"
    r = _fetch(tmp_path, offline, url="birdnet.local", marks="rare", count="8")
    marks = {s["name"]: s.get("mark", "") for s in r["species"]}
    assert (
        marks["Rock Dove"] == ""
        and marks["Silver Gull"] == "rare"
        and marks["Australian Magpie"] == ""
    )
    # too short a log marks nothing rare, but a flagged bird is still new
    _log(tmp_path, days[-3:], {"Chroicocephalus novaehollandiae": days[-1:]})
    r = _fetch(tmp_path, offline, url="birdnet.local", marks="rare", count="8")
    assert not any(s.get("mark") for s in r["species"])
    r = _fetch(tmp_path, offline, url="birdnet.local", marks="new", count="8")
    assert {s["name"]: s.get("mark", "") for s in r["species"]}["Rock Dove"] == "new"
    assert r["marks"] == "new"


def test_tally_strokes_mark_the_count_line(tmp_path, offline):
    r = _fetch(tmp_path, offline, url="birdnet.local", tally="strokes")
    s = r["species"][0]
    assert (
        r["tally"] == "strokes"
        and s["tally"] == s["count"]
        and s["tally_line"].endswith("calls")
    )
    assert any(ln.startswith(s["tally_line"]) for ln in s["lines"])
    r = _fetch(tmp_path, offline, url="birdnet.local")
    assert "tally_line" not in r["species"][0]


def test_year_list_from_the_log(tmp_path, offline):
    from datetime import date

    today = date.today()
    y = today.year
    d = lambda m, dd: f"{y}-{m:02d}-{dd:02d}"  # noqa: E731
    first = date(y, 1, 3)
    if first > today:
        first = today
    days = [first.isoformat(), today.isoformat()]
    (tmp_path / "species_seen.json").write_text(
        json.dumps({"Corvus mellori": ["Little Raven", "Aves"]})
    )
    _log(
        tmp_path,
        days,
        {
            "Gymnorhina tibicen": [first.isoformat(), today.isoformat()],
            "Corvus mellori": [first.isoformat()],
            "Turdus merula": [f"{y - 1}-06-01"],
        },
    )
    r = _fetch(tmp_path, offline, url="birdnet.local", layout="year", count="2")
    # the log's two this year, and the four birds heard to-day, the magpie among both
    assert r["year"]["n"] == 5 and r["year"]["today"] == 4
    names = [s["name"] for s in r["species"]]
    assert (
        names[0] in ("Australian Magpie", "Little Raven")
        and "Turdus merula" not in names
    )
    raven = next(s for s in r["species"] if s["name"] == "Little Raven")
    assert (
        raven["year_days"] == 1
        and raven["today"] is False
        and raven["lines"][1] == "1 day"
    )
    magpie = next(s for s in r["species"] if s["name"] == "Australian Magpie")
    assert (
        magpie["year_days"] == 2
        and magpie["today"] is True
        and magpie["lines"][1] == "2 days"
    )
    assert len(r["species"]) > 2, "the year list is not cut to the sheet's count"


def test_dawn_and_dusk_pages(tmp_path, offline, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Australia/Melbourne"))
    mins = now.hour * 60 + now.minute

    def sky_with(rise, set_):
        return lambda place, when: {
            "dark": False,
            "sunrise": f"{rise // 60:02d}:{rise % 60:02d}",
            "sunset": f"{set_ // 60:02d}:{set_ % 60:02d}",
            "sunset_min": set_,
            "moon": 0.5,
            "moon_name": "Full",
        }

    # half an hour before sunrise: the dawn page, the birds in the order they started
    monkeypatch.setattr(
        server, "_sky", sky_with((mins + 30) % 1440, (mins + 600) % 1440)
    )
    r = _fetch(tmp_path, offline, url="birdnet.local", day_pages="on", count="8")
    assert r["day_page"]["kind"] == "dawn" and r["day_page"]["sun"].startswith(
        "sunrise"
    )
    firsts = [s["first"] for s in r["species"]]
    assert firsts == sorted(firsts) and r["day_page"]["song"].startswith(
        f"first song {firsts[0]}"
    )
    # just after sunset: the evening page, the last songs latest first (a fixed clock, so the
    # window never wraps midnight whatever the hour this runs)
    rows = [dict(s) for s in r["species"]]
    sky = sky_with(6 * 60, 18 * 60 + 10)(None, None)
    page = server._day_page(rows, sky, datetime(2026, 10, 17, 18, 30))
    assert (
        page["kind"] == "dusk"
        and page["sun"] == "sunset 18:10"
        and page["song"] == "last song 17:40 Australian Magpie"
    )
    lasts = [s["last"] for s in rows]
    assert lasts == sorted(lasts, reverse=True)
    # midday: no page; and none without the option
    assert server._day_page(rows, sky, datetime(2026, 10, 17, 12, 0)) is None
    assert _fetch(tmp_path, offline, url="birdnet.local", count="8")["day_page"] is None


def test_render_now_paints_only_what_the_panels_ask_for(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    kitchen = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "count": "3",
        "style": "gould",
    }
    study = {**kitchen, "style": "woodblock", "count": "2"}
    ctx = {
        "data_dir": str(tmp_path),
        "cell_w": 800,
        "cell_h": 480,
        "page_name": "Kitchen",
    }
    server.fetch(kitchen, settings, ctx=ctx)
    server.fetch(study, settings, ctx={**ctx, "page_name": "Study"})
    _settle_paint()
    # an editor-only look paints nothing and asks for nothing
    server.fetch(
        {**kitchen, "style": "linocut", "count": "4"},
        settings,
        ctx={**ctx, "preview": True},
    )
    assert len(offline_paint) == 5
    gen = tmp_path / "paintings"
    gone = sorted(gen.glob("*--gould*.jpg"))[0]
    gone.unlink()
    (gen / gone.name.replace(".jpg", ".json")).unlink()
    started, found = server.render_now(tmp_path, "k", 24)
    _settle_paint()
    assert (started, found) == (1, 1), (
        "one painting missing from one panel: one painted, not every species in every style"
    )
    assert len(offline_paint) == 6 and gone.exists()
    assert not list(gen.glob("*--linocut*")), "the editor's look is not a panel's"
    # nothing missing: nothing painted
    assert server.render_now(tmp_path, "k", 24) == (0, 0)
    # one dashboard's own button
    gone2 = sorted(gen.glob("*--woodblock*.jpg"))[0]
    gone2.unlink()
    who = next(d["id"] for d in P.ART.dashboards(tmp_path) if d["name"] == "Kitchen")
    assert server.render_now(tmp_path, "k", 24, who) == (0, 0), (
        "the kitchen is whole; the study's gap is not its"
    )
    who = next(d["id"] for d in P.ART.dashboards(tmp_path) if d["name"] == "Study")
    assert server.render_now(tmp_path, "k", 24, who) == (1, 1)
    _settle_paint()


def test_a_pale_bird_gets_a_contour_line(tmp_path, offline_paint):
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "count": "2"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    server.fetch(options, {"fal_api_key": "k"}, ctx=ctx)
    _settle_paint()
    assert offline_paint and all(
        "very pale bird is drawn with a fine, clear dark contour line" in pr["prompt"]
        for pr in offline_paint
    )
    # the whole plate (a hero on its backdrop) keeps the paper out of it: no contour clause
    server.fetch(
        {**options, "layout": "hero", "hero_frame": "painted", "count": "1"},
        {"fal_api_key": "k"},
        ctx=ctx,
    )
    _settle_paint()
    assert (
        "contour line" not in offline_paint[-1]["prompt"]
        and "ornate" in offline_paint[-1]["prompt"]
    )


def test_an_older_tesserae_still_draws_the_sheet(tmp_path, offline_paint, monkeypatch):
    """Without app.artwork (Tesserae before 0.463) the sheet loads, shows the
    plates and photographs, starts nothing and says what it is missing."""
    monkeypatch.setattr(P, "ART", P._NoArtwork("paintings"))
    monkeypatch.setattr(M, "CUTS", P._NoArtwork("cutouts"))
    settings = {"fal_api_key": "k", "gen_daily_usd": "4"}
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "count": "3"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, settings, ctx=ctx)
    assert r["state"] == "ok" and P.NEEDS_HOST in r["notices"] and not offline_paint
    assert all(
        s["pic"] and s["pic"]["kind"] in ("plate", "photo") for s in r["species"]
    )
    assert r["live_refresh_s"] == 0 and r["live_pending"] == 0


def test_a_spent_budget_fills_the_sheet_from_the_collection(
    tmp_path, offline_paint, monkeypatch
):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Australia/Melbourne")).date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(20, 0, -1)]
    kept = {
        "Turdus merula": ("Common Blackbird", "Aves"),
        "Corvus coronoides": ("Australian Raven", "Aves"),
        "Anas superciliosa": ("Pacific Black Duck", "Aves"),
        "Dacelo novaeguineae": ("Laughing Kookaburra", "Aves"),  # excluded by the cell
        "Agrotis infusa": ("Bogong Moth", "Insecta"),  # not a kind this sheet shows
    }
    for sci, (name, kind) in kept.items():
        P.ART.keep(
            P.key_for(sci, "engraving", kind),
            tmp_path,
            _jpeg(),
            {
                "style": "engraving",
                "name": name,
                "sci": sci,
                "kind": kind,
                "credit": f"AI painting of the {name}",
            },
        )
    P.ART.keep(  # another style is not this page's
        P.key_for("Cacatua galerita", "gould"),
        tmp_path,
        _jpeg(),
        {
            "style": "gould",
            "name": "Sulphur-crested Cockatoo",
            "sci": "Cacatua galerita",
        },
    )
    P.ART.keep(  # nor a hero's whole plate
        P.key_for("Cacatua galerita", "engraving", hero=True),
        tmp_path,
        _jpeg(),
        {
            "style": "engraving",
            "name": "Sulphur-crested Cockatoo",
            "sci": "Cacatua galerita",
        },
    )
    # the raven was on the sheet five days, the blackbird once; the duck never
    _log(
        tmp_path,
        days,
        {"Corvus coronoides": days[-5:], "Turdus merula": days[3:4]},
    )
    N.write(
        tmp_path / "paintings" / "budget.json",
        json.dumps({__import__("time").strftime("%Y-%m-%d"): 24}).encode(),
    )
    settings = {"fal_api_key": "k", "gen_daily_cap": "6"}
    options = {
        **MELBOURNE,
        "url": "birdnet.local",
        "pictures": "gen",
        "style": "engraving",
        "when_spent": "rare",
        "exclude": "Laughing Kookaburra",
    }
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    # past the deadline, a painting never cut out waits: none of these is cut yet
    monkeypatch.setattr(server, "KEPT_BY_S", -1.0)
    r = server.fetch(options, settings, ctx=ctx)
    assert not any(s.get("kept") for s in r["species"])
    monkeypatch.setattr(server, "KEPT_BY_S", 4.5)
    r = server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    assert not [p for p in offline_paint if "prompt" in p], "nothing is painted"
    names = [s["name"] for s in r["species"]]
    assert names == [
        "Australian Magpie",
        "Common Blackbird",
        "Australian Raven",
        "Pacific Black Duck",
    ], "to-day's leader keeps its place; the rarest of the kept follow"
    lead, *rest = r["species"]
    assert lead["pic"]["kind"] == "plate" and not lead.get("kept")
    assert all(
        s["kept"] and s["pic"]["kind"] == "painting" and not s["pic"]["whole"]
        for s in rest
    )
    assert rest[0]["lines"][-1].startswith("on the sheet once, ")
    assert rest[1]["lines"][-1].startswith("on the sheet 5 days, last ")
    assert rest[2]["lines"][-1] == "from the collection"
    assert all(not s.get("mark") and "_paint" not in s for s in r["species"])
    assert r["period"] == "heard to-day, and 3 from the collection"
    assert (
        "3 of the rarest from the collection while the budget is spent" in r["status"]
    )
    assert "AI painting of the Common Blackbird" in r["credits"]
    # never logged as seen to-day: the log, the year list and the frame leave them out
    log = N.read_json(tmp_path / "rarity.json", {})
    assert today.isoformat() in log["seen"]["Gymnorhina tibicen"]
    assert today.isoformat() not in log["seen"]["Corvus coronoides"]
    assert "Anas superciliosa" not in log["seen"]
    assert "Anas superciliosa" not in N.read_json(tmp_path / "species_shown.json", {})
    # once cut, a kept painting comes with its cut-out, even past the deadline
    monkeypatch.setattr(server, "KEPT_BY_S", -1.0)
    r = server.fetch(options, settings, ctx=ctx)
    assert all(s["pic"].get("cut") for s in r["species"] if s.get("kept"))
    assert len([s for s in r["species"] if s.get("kept")]) == 3
    monkeypatch.setattr(server, "KEPT_BY_S", 4.5)
    # leaving the unpainted off: the page is all from the collection
    r = server.fetch({**options, "unpainted": "hide"}, settings, ctx=ctx)
    assert r["period"] == "from the collection" and all(
        s.get("kept") for s in r["species"]
    )
    # at random: the same three for the hour, whatever renders it
    a = server.fetch({**options, "when_spent": "random"}, settings, ctx=ctx)
    b = server.fetch(
        {**options, "when_spent": "random"}, settings, ctx={**ctx, "preview": True}
    )
    assert [s["name"] for s in a["species"]] == [s["name"] for s in b["species"]]
    assert sorted(s["sci"] for s in a["species"] if s.get("kept")) == sorted(
        ["Turdus merula", "Corvus coronoides", "Anas superciliosa"]
    )
    assert "3 more from the collection while the budget is spent" in a["status"]
    # left alone, and on a hero or the year list, the sheet is to-day's own
    for opts in ({"when_spent": "keep"}, {"layout": "hero"}, {"layout": "year"}):
        r = server.fetch({**options, **opts}, settings, ctx=ctx)
        assert not any(s.get("kept") for s in r["species"]), opts
        assert "from the collection" not in r["period"]


def test_fugleramme_packings_keep_specimens_apart_and_on_the_page():
    script = (
        f"import('file://{ROOT}/static/pack.js').then((m) => {{"
        " const blob = (w, h, s) => { const d = new Uint8Array(w * h);"
        "   for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {"
        "     const dx = (x - w * 0.45) / (w * 0.35), dy = (y - h * 0.45) / (h * 0.3);"
        "     const head = ((x - w * 0.75) / (w * 0.13)) ** 2 + ((y - h * 0.25) / (h * 0.13)) ** 2 < 1;"
        "     if (dx * dx + dy * dy < 1 || head || (Math.abs(x - w * 0.45) < 2 && y > h * 0.7)) d[y * w + x] = 1; }"
        "   return { w, h, data: d }; };"
        " for (const packer of ['spiral', 'voids']) for (const [W, H] of [[1400, 840], [840, 1400]]) {"
        "   const its = Array.from({ length: 14 }, (_, i) => ({ mask: blob(160 + (i * 37) % 140, 130 + (i * 53) % 110, i),"
        "     factor: 0.6 + (i % 5) / 5, lines: ['Bird ' + i], flippable: true, sci: 'Avis ' + i }));"
        "   const out = m.packSheet(its, W, H, 13 / H, { packer });"
        "   const occ = new Uint8Array(W * H);"
        "   out.forEach((p, i) => { if (!p) throw new Error(packer + ' left one off');"
        "     const k = its[i].mask;"
        "     for (let y = 0; y < k.h; y++) for (let x = 0; x < k.w; x++) { if (!k.data[y * k.w + x]) continue;"
        "       const sx = p.flip ? k.w - 1 - x : x;"
        "       const X = Math.floor(p.x + (sx + 0.5) * p.w / k.w), Y = Math.floor(p.y + (y + 0.5) * p.h / k.h);"
        "       if (X < 0 || Y < 0 || X >= W || Y >= H) throw new Error(packer + ' off the page');"
        "       if (occ[Y * W + X] && occ[Y * W + X] !== i + 1) throw new Error(packer + ' overlap');"
        "       occ[Y * W + X] = i + 1; } });"
        "   const same = m.packSheet(its, W, H, 13 / H, { packer });"
        "   if (JSON.stringify(same) !== JSON.stringify(out)) throw new Error(packer + ' not repeatable'); }"
        " console.log('ok'); })"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr


def test_packing_option_reaches_the_page(tmp_path):
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    assert server.fetch(MELBOURNE, {}, ctx=ctx)["packing"] == "balanced"
    got = server.fetch({**MELBOURNE, "packing": "voids"}, {}, ctx=ctx)
    assert got["packing"] == "voids"
    assert (
        server.fetch({**MELBOURNE, "packing": "nonsense"}, {}, ctx=ctx)["packing"]
        == "balanced"
    )


def _painting_and_mask(w=360, h=480):
    """A painting on paper (a textured bird on a branch) and the model's alpha for
    the bird alone, both the picture's size."""
    im = Image.new("RGB", (w, h), (246, 241, 230))
    d = ImageDraw.Draw(im)
    for i in range(0, w, 7):  # paper grain the correlation can lock onto
        d.line((i, 0, i + 30, h), fill=(240 - i % 9, 236, 226))
    d.line((40, 330, 320, 300), fill=(110, 80, 50), width=6)
    d.ellipse((120, 170, 250, 320), fill=(90, 70, 160), outline=(20, 20, 20), width=3)
    alpha = Image.new("L", (w, h), 0)
    ImageDraw.Draw(alpha).ellipse((120, 170, 250, 320), fill=255)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), alpha


def test_a_cut_out_knows_where_it_sits_in_its_picture(tmp_path):
    picture, alpha = _painting_and_mask()
    box = M.crop_box((360, 480), alpha)
    assert box[0] > 100 and box[2] < 270, "the cut-out is cropped to the specimen"
    cut = M.cut_png(picture, alpha)
    assert Image.open(io.BytesIO(cut)).size == (box[2] - box[0], box[3] - box[1])
    want = M.box_fractions(box, (360, 480))
    assert M.find_box(cut, picture) == want
    other = Image.new("RGB", (360, 480), (10, 200, 10))
    buf = io.BytesIO()
    other.save(buf, "PNG")
    assert M.find_box(cut, buf.getvalue()) is None, "not found in another picture"
    # a cut-out kept before its box was: found once in its picture and kept beside it
    src = tmp_path / "painting.png"
    src.write_bytes(picture)
    folder = M.folder(tmp_path)
    folder.mkdir(parents=True)
    (folder / "k1.png").write_bytes(cut)
    (folder / "k1.json").write_text(
        json.dumps({"title": "t", "payload": {"source": str(src)}}), encoding="utf-8"
    )
    assert M.box_of("k1", tmp_path) == want
    kept = json.loads((folder / "k1.json").read_text(encoding="utf-8"))
    assert kept["box"] == want and kept["title"] == "t"
    src.unlink()
    assert M.box_of("k1", tmp_path) == want, "read back, not found again"


def test_a_new_cut_out_keeps_its_box(tmp_path):
    picture, alpha = _painting_and_mask()
    src = tmp_path / "painting.png"
    src.write_bytes(picture)
    rgba = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    rgba.putalpha(alpha)
    buf = io.BytesIO()
    rgba.save(buf, "PNG")

    class _Fal:
        def generate(self, *a, **k):
            return buf.getvalue()

    class _Job:
        payload = {"source": str(src)}
        title = "a bird"
        fal = _Fal()

    raw, meta = M.make(_Job())
    assert meta["box"] == M.find_box(raw, picture)
    x0, y0, x1, y1 = meta["box"]
    assert 0.25 < x0 < x1 < 0.8 and 0.3 < y0 < y1 < 0.72


def test_the_model_mask_lands_where_the_specimen_is():
    # a cut-out cropped to a bird in the lower right quarter of its picture, laid on the
    # picture's grid at its box: it lands there, not stretched over the whole frame
    script = (
        f"import('file://{ROOT}/static/cut.js').then((m) => {{"
        " const mw = 50, mh = 40, a = new Uint8ClampedArray(mw * mh * 4);"
        " for (let y = 0; y < mh; y++) for (let x = 0; x < mw; x++) a[(y * mw + x) * 4 + 3] = 255;"
        " const sw = 200, sh = 160, k = m.modelMask(a, mw, mh, [0.5, 0.5, 0.75, 0.75], sw, sh);"
        " let n = 0, x0 = sw, y0 = sh, x1 = -1, y1 = -1;"
        " for (let y = 0; y < sh; y++) for (let x = 0; x < sw; x++) if (k[y * sw + x]) { n++;"
        "   x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }"
        " const box = [x0, y0, x1 + 1, y1 + 1].join(',');"
        " if (box !== '100,80,150,120' || n !== 50 * 40) throw new Error(box + ' ' + n);"
        " const none = m.modelMask(a, mw, mh, [0.5, 0.5, 0.5, 0.75], sw, sh);"
        " if (none.some((v) => v)) throw new Error('an empty box drew something');"
        " console.log('ok'); })"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr


def test_the_cut_box_rides_to_the_page():
    script = (
        f"import('file://{ROOT}/client.js').then(async (m) => {{ const s = {{innerHTML: ''}};"
        " const pic = (b) => ({ src: 'data:image/png;base64,AA', cut: 'data:image/png;base64,AA', on_paper: true, cut_box: b });"
        " const data = { source: 'inaturalist', state: 'ok', layout: 'cluster', area: 'x', period: 'y',"
        "   species: [{ name: 'A', sci: 'A a', pic: pic([0.1, 0.2, 0.6, 0.9]) }, { name: 'B', sci: 'B b', pic: pic([0.5, 0.5, 0.2, 0.9]) }] };"
        " await m.default(s, { data, cell: { size: 'md', w: 800, h: 480 } });"
        " console.log('ok'); })"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    src = (ROOT / "client.js").read_text(encoding="utf-8")
    assert "cut_box: cutBox(s.pic)" in src and "x.cut_box" in src


def test_a_beetle_is_painted_with_its_wing_covers_closed(tmp_path):
    assert K.noun_of("Insecta", "Beetle")[1] != K.noun_of("Insecta")[1]
    assert "closed" in K.noun_of("Insecta", "Beetle")[1]
    assert K.noun_of("Insecta", "Moth") == K.noun_of("Insecta")
    beetle = P.key_for("Coccinella transversalis", "gould", "Insecta", fine="Beetle")
    assert beetle.endswith("--b3") and beetle != P.key_for(
        "Coccinella transversalis", "gould", "Insecta"
    )
    job = {
        "key": beetle,
        "style": "gould",
        "name": "Transverse Ladybird",
        "sci": "Coccinella transversalis",
        "kind": "Insecta",
        "fine": "Beetle",
    }
    prompt = P.prompt_for(job, tmp_path)
    assert "wing covers (elytra) closed" in prompt and "wings open" not in prompt
    payload, _t = server._paint_payload(
        {
            "name": "x",
            "sci": "Coccinella transversalis",
            "kind": "Insecta",
            "fine": "Beetle",
        },
        {"style": "gould"},
    )
    assert payload["fine"] == "Beetle"


def test_a_long_hero_note_shows_whole_lines_above_the_also_line(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    import functools
    import http.server
    import threading

    note = (
        "The Australian wood duck, maned duck or maned goose is a dabbling duck found "
        "throughout much of Australia. It is the only living species in the genus "
        "Chenonetta. It grazes on grasses, clover and other herbs, and occasionally on "
        "insects, and is often seen in flocks of up to several hundred birds around farm "
        "dams, wetlands, parks and golf courses, nesting in tree hollows near water."
    )
    species = [
        {
            "name": "Australian Wood Duck",
            "sci": "Chenonetta jubata",
            "about": note,
            "count": 3,
            "lines": ["Australian Wood Duck"],
        },
        *(
            {
                "name": f"Bird {i}",
                "sci": f"Avis {i}",
                "count": i,
                "lines": [f"Bird {i}"],
            }
            for i in range(5)
        ),
    ]
    data = {
        "source": "inaturalist",
        "state": "ok",
        "layout": "hero",
        "hero_note": True,
        "hero_frame": "ornate",
        "area": "Melbourne",
        "period": "this month",
        "text_scale": 1,
        "species": species,
    }
    (tmp_path / "data.json").write_text(json.dumps(data), encoding="utf-8")
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(ROOT)
    )
    handler.log_message = lambda *a: None  # type: ignore[method-assign]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_api.sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"no browser: {exc}")
            for w, h in ((1200, 800), (800, 480), (480, 800)):
                pg = b.new_page(viewport={"width": w, "height": h})
                pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/README.md")
                got = pg.evaluate(
                    """async ([data, w, h]) => {
                      const host = document.createElement('div'); host.style.cssText = `width:${w}px;height:${h}px`;
                      document.body.style.margin = '0'; document.body.appendChild(host);
                      const shadow = host.attachShadow({ mode: 'open' });
                      const m = await import('/client.js');
                      await m.default(shadow, { data: { ...data, cell_w: w, cell_h: h }, cell: { w, h, size: 'lg' } });
                      const n = shadow.querySelector('.hero-note'), a = shadow.querySelector('.hero-also');
                      const lh = parseFloat(getComputedStyle(n).lineHeight);
                      return { bottom: n.getBoundingClientRect().bottom, top: a.getBoundingClientRect().top, ch: n.clientHeight, lh };
                    }""",
                    [data, w, h],
                )
                pg.close()
                assert got["bottom"] <= got["top"] + 0.5, (w, h, got)
                lines = got["ch"] / got["lh"]
                assert abs(lines - round(lines)) < 0.05 and 1 <= round(lines) <= 3, (
                    w,
                    h,
                    got,
                )
            b.close()
    finally:
        srv.shutdown()

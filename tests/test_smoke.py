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
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("specimen_server", ROOT / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


server = _load()
N, S, K = server.N, server.S, server.K

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
    assert r["title"] == "Demo garden" and r["area"] == "heard to-day"
    assert 2 <= len(r["species"]) <= 8
    s = r["species"][0]
    assert s["unit"] == "calls" and s["count"] >= s["count"] and s["kind"] == "Aves"
    assert s["lines"][0] == s["name"] and " calls" in s["lines"][1]
    # the magpie has a plate in the pack: on paper, printed by multiply
    magpie = next((x for x in r["species"] if x["sci"] == "Gymnorhina tibicen"), None)
    if magpie:
        assert magpie["pic"]["kind"] == "plate" and magpie["pic"]["on_paper"]
        assert magpie["pic"]["src"].startswith("data:image/jpeg")
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
    # the thumbnail came through BirdNET-Go with the address joined
    assert any(
        u == "http://birdnet.local:8080/api/v2/media/species/dove.jpg" for u in offline
    )
    assert (
        magpie["pic"]["kind"] == "plate"
    )  # the pack wins: its thumbnail is never fetched
    assert not any(u.endswith("/magpie.jpg") for u in offline)
    assert dove["pic"]["kind"] == "photo" and dove["pic"]["credit"] == "via BirdNET-Go"
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
        r["period"] == "this week"
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
    assert swan["pic"]["kind"] == "photo" and not swan["pic"]["on_paper"]
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
    assert by["Gymnorhina tibicen"]["factor"] > by["Columba livia"]["factor"] > 0.55
    r = _fetch(tmp_path, offline, url="birdnet.local", sizing="busy")
    f = [s["factor"] for s in r["species"]]
    assert f[0] == 1.65 and f == sorted(f, reverse=True)
    r = _fetch(tmp_path, offline, url="birdnet.local", sizing="same")
    assert {s["factor"] for s in r["species"]} == {1.0}


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
    assert {s["name"] for s in m["settings"]} == {"fal_api_key", "exclude"}


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
        "empty": "Nothing recorded",
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

    def two(d):
        d.ellipse((20, 100, 140, 300), fill=255)
        d.ellipse((160, 100, 280, 300), fill=255)

    assert "largest piece" in M.check(_alpha(draw=two))[1]


def _settle(timeout=10.0):
    import time

    end = time.time() + timeout
    while M._RUNNING and time.time() < end:
        time.sleep(0.02)
    assert not M._RUNNING


@pytest.fixture
def offline_masks(offline):
    """fal.ai answers with a mask: a clean oval for most pictures, a frame
    for anything whose request carries the marker."""
    real = N.http.side_effect
    sent: list[bytes] = []

    def fake_http(url, token="", accept="application/json", **kw):  # noqa: ARG001
        if "birefnet" in url:
            sent.append(kw.get("body") or b"")
            assert token == "k" and kw.get("auth_scheme") == "Key"
            return json.dumps(
                {"image": {"url": "https://fal.example/mask.png"}}
            ).encode(), None
        if url == "https://fal.example/mask.png":
            im = Image.new("RGBA", (300, 400), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse((80, 100, 220, 300), fill=(255, 255, 255, 255))
            buf = io.BytesIO()
            im.save(buf, "PNG")
            return buf.getvalue(), None
        return real(url, token, accept, **kw)

    N.http.side_effect = fake_http
    return sent


def test_masks_are_made_once_and_ride_along(tmp_path, offline_masks):
    settings = {"fal_api_key": "k", "exclude": ""}
    options = {**MELBOURNE, "url": "birdnet.local"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, settings, ctx=ctx)
    assert r["status"] == ["4 cut-outs on the way"]
    assert all(s["pic"]["cut_state"] == "running" for s in r["species"])
    _settle()
    assert (
        len(offline_masks) == 3
        and b'"image_url": "data:image/jpeg;base64,' in offline_masks[0]
    )
    assert len(list((tmp_path / "masks").glob("*.cut.png"))) == 3
    r = server.fetch(options, settings, ctx=ctx)
    assert r["status"] == [] and all(
        s["pic"]["cut_state"] == "ready" for s in r["species"]
    )
    assert all(
        s["pic"]["cut"].startswith("data:image/png;base64,") for s in r["species"]
    )
    assert len(offline_masks) == 3, "a kept mask is never asked for again"
    assert M.budget_used(tmp_path) == 3
    # a greyscale panel gets the cut-out converted, from the same mask
    r = server.fetch({**options, "panel": "gray4"}, settings, ctx=ctx)
    assert all(s["pic"]["cut"] for s in r["species"]) and len(offline_masks) == 3


def test_masks_need_a_key_and_a_bad_one_is_remembered(tmp_path, offline_masks):
    options = {**MELBOURNE, "url": "birdnet.local"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, {}, ctx=ctx)
    assert (
        M.NOTE_NO_KEY
        if hasattr(M, "NOTE_NO_KEY")
        else server.NOTE_NO_KEY in r["notices"]
    )
    assert not offline_masks and all(
        s["pic"]["cut_state"] == "no_key" for s in r["species"]
    )
    # the model answers a mask that is the whole frame: bad, kept as bad, not retried
    real = N.http.side_effect

    def framed(url, token="", accept="application/json", **kw):
        if url == "https://fal.example/mask.png":
            im = Image.new("RGBA", (300, 400), (255, 255, 255, 255))
            buf = io.BytesIO()
            im.save(buf, "PNG")
            return buf.getvalue(), None
        return real(url, token, accept, **kw)

    N.http.side_effect = framed
    server.fetch(options, {"fal_api_key": "k"}, ctx=ctx)
    _settle()
    assert (
        not list((tmp_path / "masks").glob("*.cut.png"))
        and len(list((tmp_path / "masks").glob("*.bad"))) == 3
    )
    r = server.fetch(options, {"fal_api_key": "k"}, ctx=ctx)
    assert all(s["pic"]["cut_state"] == "bad" for s in r["species"]) and not r["status"]
    assert "covers" in M.why_bad(r["species"][0]["pic"]["key"], tmp_path)

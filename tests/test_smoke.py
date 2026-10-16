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
    assert magpie["pic"]["kind"] == "plate"  # a plate wins: the thumbnail is never fetched
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
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "style": "builder", "build_medium": "linocut", "build_extra": "with a crescent moon", "count": "2"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480, "preview": True}
    server.fetch(options, settings, ctx=ctx)
    held = list(server.held(tmp_path).values())
    assert len(held) == 2 and all("with a crescent moon" in j["how"] for j in held)
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
        "gen_daily_cap",
        "gen_per_refresh",
        "gen_parallel",
        "mask_daily_cap",
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
    # classic plates ride in already cut; the one species without one needs a mask
    need = [s for s in r["species"] if not s["pic"].get("classic")]
    assert len(need) == 1 and r["status"] == ["1 cut-out on the way"]
    assert all(s["pic"]["cut_state"] == "running" for s in need)
    _settle()
    assert (
        len(offline_masks) == 1
        and b'"image_url": "data:image/jpeg;base64,' in offline_masks[0]
    )
    assert list((tmp_path / "masks").glob("*.cut.png"))
    r = server.fetch(options, settings, ctx=ctx)
    assert r["status"] == [] and all(
        s["pic"]["cut_state"] == "ready" for s in r["species"]
    )
    assert all(
        s["pic"]["cut"].startswith("data:image/png;base64,") for s in r["species"]
    )
    assert len(offline_masks) == 1, "a kept mask is never asked for again"
    assert M.budget_used(tmp_path) == 1
    # a greyscale panel gets the cut-out converted, from the same mask
    r = server.fetch({**options, "panel": "gray4"}, settings, ctx=ctx)
    assert all(s["pic"]["cut"] for s in r["species"]) and len(offline_masks) == 1


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
        s["pic"]["cut_state"] == "no_key"
        for s in r["species"]
        if not s["pic"].get("classic")
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
    assert len(list((tmp_path / "masks").glob("*.bad"))) == 1
    r = server.fetch(options, {"fal_api_key": "k"}, ctx=ctx)
    need = [s for s in r["species"] if not s["pic"].get("classic")]
    assert need and all(s["pic"]["cut_state"] == "bad" for s in need) and not r["status"]
    assert "covers" in M.why_bad(need[0]["pic"]["key"], tmp_path)


# ----------------------------------------------------------------------
# Paintings
# ----------------------------------------------------------------------

P = server.P


def _settle_paint(timeout=15.0):
    import time

    end = time.time() + timeout
    while (P._PAINTING or M._RUNNING) and time.time() < end:
        time.sleep(0.02)
    assert not P._PAINTING and not M._RUNNING


@pytest.fixture
def offline_paint(offline_masks):
    """fal.ai paints too: nano-banana answers a picture, Wikipedia a sentence."""
    real = N.http.side_effect
    prompts: list[dict] = []

    def fake_http(url, token="", accept="application/json", **kw):  # noqa: ARG001
        if (
            "nano-banana" in url
            or "flux-pro/kontext" in url
            or "gpt-image" in url
            or "seedream" in url
        ):
            prompts.append(json.loads(kw["body"]))
            assert token == "k"
            return json.dumps(
                {"images": [{"url": "https://fal.example/out.jpg"}]}
            ).encode(), None
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
    assert all(
        s["pic"]["on_paper"] and s["pic"]["key"].startswith("g_") for s in r["species"]
    )
    # paintings get masks like any other picture
    _settle_paint()
    r = server.fetch(options, settings, ctx=ctx)
    assert all(s["pic"].get("cut") for s in r["species"])
    assert P.budget_used(tmp_path / "gen") == 4
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
    for _ in range(4):
        _settle_paint()
        r = server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    r = server.fetch(options, settings, ctx=ctx)
    assert r["live_refresh_s"] == 0
    plain = server.fetch({**MELBOURNE, "url": "birdnet.local"}, {}, ctx=ctx)
    assert plain["live_refresh_s"] == 0


def test_paintings_need_a_key_and_respect_the_cap(tmp_path, offline_paint):
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, {}, ctx=ctx)
    assert P.NOTE_NO_KEY in r["notices"] and not offline_paint
    assert r["species"][0]["pic"]["kind"] == "plate"
    N.write(
        tmp_path / "gen" / "budget.json",
        json.dumps({__import__("time").strftime("%Y-%m-%d"): 6}).encode(),
    )
    r = server.fetch(options, {"fal_api_key": "k", "gen_daily_cap": "6"}, ctx=ctx)
    assert not offline_paint and any("limit of 6" in x for x in r["status"])


def test_admin_template_renders(tmp_path, offline_paint):
    import jinja2
    from flask import Flask

    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "count": "4"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    server.fetch(options, {"fal_api_key": "k"}, ctx=ctx)
    _settle_paint()
    server.fetch(
        options, {"fal_api_key": "k"}, ctx=ctx
    )  # the paintings are ready: their masks start
    _settle_paint()
    app = Flask("t", template_folder=str(ROOT / "templates"))
    stubs = {
        "_base.html": "{% block title %}{% endblock %}{% block main %}{% endblock %}",
        "_components.html": (
            "{% macro card_head(t, icon_name='', level=2, blurb='') %}<h2>{{ t }}</h2>{{ blurb }}{% endmacro %}"
            "{% macro button(t, icon='', variant='', disabled=False) %}<button>{{ t }}</button>{% endmacro %}"
            "{% macro empty_state(i, t, b) %}<p>{{ t }} {{ b }}</p>{% endmacro %}"
        ),
    }
    app.jinja_loader = jinja2.ChoiceLoader(
        [jinja2.DictLoader(stubs), jinja2.FileSystemLoader(str(ROOT / "templates"))]
    )
    app.register_blueprint(server.blueprint(), url_prefix="/plugins/specimen_sheet")
    with app.test_request_context("/plugins/specimen_sheet/"):
        from flask import render_template

        g = server._gallery(tmp_path, 12)
        html = render_template(
            "specimen_sheet/admin.html",
            g=g,
            known=server.known_species(tmp_path),
            has_key=True,
            wanted=1,
            held=2,
        )
        idle = render_template(
            "specimen_sheet/admin.html",
            g=g,
            known=[],
            has_key=True,
            wanted=1,
            held=0,
        )
    assert "Render now" in idle and "paint-held" not in idle
    assert "Paint the 2 waiting" in html and "Purge everything" in html
    assert "Styles" in html
    assert "bb-style-tile" in html and "Preview" in html
    assert g["used"] == 4 and len([e for e in g["paintings"] if e["active"]]) == 4
    assert (
        "Australian Magpie" in html
        and "Gymnorhina tibicen" in html
        and "Silver Gull" in html
    )
    assert "Cut out again" in html and "No paintings yet" not in html
    assert "Not on a dashboard" in html or "bb-group" in html
    assert any(e["cut"] == "ready" for e in g["paintings"])


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
    assert r["credits"] == [r["scene"]["pic"]["credit"]] and "AI scene" in r["credits"][0]
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
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "dapper": True, "count": "4"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    dapper = [pr for pr in offline_paint if "is dapper" in pr["prompt"]]
    assert len(dapper) == 1
    assert len(list((tmp_path / "gen").glob("*--dapper.jpg"))) == 1


def test_pose_and_native_plants(tmp_path, offline_paint):
    P = server.P
    assert P.plants_for(-37.81, 144.96)[0] == "eucalyptus" and P.plants_for(51.5, -0.1)[0] == "oak"
    assert P.plants_for(None, None) == [] and P.plants_for(0, -160) == []
    plants = P.plants_for(-37.81, 144.96)
    assert P.perch_for("Gymnorhina tibicen", plants) in plants
    assert P.perch_for("Gymnorhina tibicen", plants) == P.perch_for("Gymnorhina tibicen", plants)
    assert P.key_for("Gymnorhina tibicen", "gould", pose="flight").endswith("--flight")
    assert "--on_" in P.key_for("Gymnorhina tibicen", "gould", pose="perched", perch="golden wattle")
    assert P.key_for("Gymnorhina tibicen", "gould", pose="flight", perch="golden wattle").endswith("--flight")
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "pose": "perched", "count": "3"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    assert len(offline_paint) == 3
    for pr in offline_paint:
        assert "in flight" not in pr["prompt"] and "a plant of the country where it was seen" in pr["prompt"]
        assert any(p in pr["prompt"] for p in plants)
    names = {f.name for f in (tmp_path / "gen").glob("*.jpg")}
    assert names and all("--on_" in n for n in names)
    # all in flight: no perch, new paintings
    server.fetch({**options, "pose": "flight"}, settings, ctx=ctx)
    _settle_paint()
    assert len(offline_paint) == 6 and all("in flight" in pr["prompt"] for pr in offline_paint[3:])
    assert all("--flight" in f.name for f in (tmp_path / "gen").glob("*.jpg") if "--on_" not in f.name)


def test_hero_note_and_painted_backdrop(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {**MELBOURNE, "url": "birdnet.local", "layout": "hero", "count": "4"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    r = server.fetch(options, settings, ctx=ctx)
    assert r["hero_note"] and r["hero_frame"] == "plain"
    assert "magpie" in r["species"][0]["about"].lower()
    assert "about" not in r["species"][1]
    r = server.fetch({**options, "hero_note": False, "hero_frame": "ornate"}, settings, ctx=ctx)
    assert "about" not in r["species"][0] and r["hero_frame"] == "ornate"
    # painted needs AI paintings; with them the hero gets a plate of its own, shown whole
    r = server.fetch({**options, "hero_frame": "painted"}, settings, ctx=ctx)
    assert r["hero_frame"] == "plain"
    painted = {**options, "hero_frame": "painted", "pictures": "gen"}
    r = server.fetch(painted, settings, ctx=ctx)
    _settle_paint()
    hero = [pr for pr in offline_paint if "ornate framed plate" in pr["prompt"]]
    assert len(hero) == 1 and "(Gymnorhina tibicen)" in hero[0]["prompt"] and hero[0]["aspect_ratio"] == "4:3"
    assert len(list((tmp_path / "gen").glob("*--hero.jpg"))) == 1
    r = server.fetch(painted, settings, ctx=ctx)
    assert r["species"][0]["pic"]["whole"] and r["species"][0]["pic"]["kind"] == "painting"
    assert not r["species"][1]["pic"].get("whole")
    for frame in ("ornate", "painted"):
        data = r if frame == "painted" else server.fetch({**options, "hero_frame": "ornate"}, settings, ctx=ctx)
        script = (
            f"import('file://{ROOT}/client.js').then(async (m) => {{ const s = {{innerHTML: ''}};"
            f" await m.default(s, {{data: {json.dumps(data)}, cell: {{size: 'md', w: 800, h: 480}}}});"
            " const h = s.innerHTML; if (!h.includes('hero-note') || !h.includes('hero-also')) throw new Error(h.slice(0, 300));"
            + (" if (!h.includes('hero-whole') || !h.includes('hero-title')) throw new Error('no hero page');" if frame == "painted" else " if (!h.includes('orn-c')) throw new Error('no frame');")
            + " console.log('ok'); })"
        )
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr


def test_previews_hold_paintings_for_paint_now(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "count": "3"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480, "preview": True}
    r = server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    assert not offline_paint, "a preview starts nothing"
    assert len(server.held(tmp_path)) == 3
    assert any("3 paintings waiting for Paint now" in x for x in r["status"])
    assert r["live_refresh_s"] > 0, "the editor keeps coming back, so the settling starts them"
    # the same preview again within the settling time holds nothing new
    server.fetch(options, settings, ctx=ctx)
    assert len(server.held(tmp_path)) == 3 and not offline_paint
    # a preview of the same settings twenty seconds on is a kept dashboard: it paints
    seen = json.loads((tmp_path / "preview_seen.json").read_text())
    (tmp_path / "preview_seen.json").write_text(json.dumps({k: v - 30 for k, v in seen.items()}))
    r = server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    assert len(offline_paint) == 3 and not server.held(tmp_path)
    assert not any("waiting" in x for x in r["status"])
    # a changed look is the same cell in a new look: held again, the old look's held dropped,
    # and Paint now starts that cell's and no other's
    r = server.fetch({**options, "style": "woodblock"}, settings, ctx=ctx)
    assert len(server.held(tmp_path)) == 3 and any("3 paintings waiting" in x for x in r["status"])
    other = {**options, "style": "linocut", "count": "2"}  # another cell: fewer species shown
    server.fetch(other, settings, ctx=ctx)
    assert len(server.held(tmp_path)) == 5
    woodblock = [j["subject"] for j in server.held(tmp_path).values() if j["style"] == "woodblock"][0]
    started = server.start_held(tmp_path, "k", 24, woodblock)
    _settle_paint()
    assert started == 3 and len(offline_paint) == 6
    assert {j["style"] for j in server.held(tmp_path).values()} == {"linocut"}
    server._unhold(tmp_path)
    r = server.fetch(options, settings, ctx={**ctx, "preview": False})
    assert all(s["pic"]["kind"] == "painting" for s in r["species"])
    # a panel render with new species starts them all straight away
    more = server.fetch({**options, "count": "4"}, settings, ctx={**ctx, "preview": False})
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
        body = P.request_body(model, "p", "https://x/y.jpg", "3:4")
        assert body["prompt"] == "p" and (
            "image_url" in body or body["image_urls"] == ["https://x/y.jpg"]
        )
    assert P.request_body("gpt_image", "p", "u", "4:3")["image_size"] == "landscape_4_3"
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
    assert any(u == "https://fal.run/fal-ai/flux-pro/kontext" for u in urls)
    keys = [f.stem for f in (tmp_path / "gen").glob("*--m_flux_kontext.jpg")]
    assert len(keys) == 4
    meta = json.loads((tmp_path / "gen" / f"{keys[0]}.json").read_text())
    assert (
        meta["model"] == "flux_kontext"
        and "FLUX.1 Kontext Pro on fal.ai" in meta["credit"]
    )
    g = server._gallery(tmp_path, 12)
    assert all(
        e["model"] == "FLUX.1 Kontext Pro" for e in g["paintings"] if e["active"]
    )
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

    def slow(job, data_dir, fal_key, stop=None):  # noqa: ARG001
        seen.append(job["key"])
        gate.wait(5)
        if stop is not None and stop.is_set():
            return P.STOPPED
        return None

    with patch.object(P, "paint", slow):
        yield gate, seen
    gate.set()
    _wait(lambda: not P._PAINTING)


def _jobs(n):
    return [
        {"key": f"bird{i}--gould", "style": "gould", "name": f"Bird {i}", "sci": "x"}
        for i in range(n)
    ]


def test_queue_runs_a_few_at_a_time(tmp_path, held_paint):
    gate, seen = held_paint
    for job in _jobs(5):
        assert P.start(job, tmp_path, "k")
    assert not P.start(_jobs(1)[0], tmp_path, "k")  # already queued
    _wait(lambda: len(seen) == P.BATCH_PARALLEL)
    got = P.progress(P.gen_dir(tmp_path), 24, True)
    assert len(got["running"]) == P.BATCH_PARALLEL and len(got["queued"]) == 2
    gate.set()
    _wait(lambda: not P._PAINTING)
    assert len(seen) == 5


def test_stop_one_waiting_and_one_running(tmp_path, held_paint):
    gate, seen = held_paint
    for job in _jobs(5):
        P.start(job, tmp_path, "k")
    _wait(lambda: len(seen) == 3)
    assert P.stop("bird4--gould", tmp_path)  # waiting
    assert P.stop("bird0--gould", tmp_path)  # under way
    assert not P.stop("nobody--gould", tmp_path)
    assert P.state("bird4--gould", P.gen_dir(tmp_path)) == "failed"
    _wait(lambda: "bird0--gould" not in P._PAINTING)
    # the freed slot takes the next waiting job; the stopped one never starts
    _wait(lambda: "bird3--gould" in seen)
    gate.set()
    _wait(lambda: not P._PAINTING)
    assert "bird4--gould" not in seen
    err = N.read_json(P.gen_dir(tmp_path) / "bird0--gould.err", {})
    assert err["why"] == "stopped"


def test_stop_all_empties_the_queue(tmp_path, held_paint):
    gate, seen = held_paint
    for job in _jobs(6):
        P.start(job, tmp_path, "k")
    _wait(lambda: len(seen) == 3)
    assert P.stop_all(tmp_path) == 6
    _wait(lambda: not P._PAINTING)
    assert len(seen) == 3 and not P.progress(P.gen_dir(tmp_path), 24, True)["queued"]
    assert not list(P.gen_dir(tmp_path).glob("*.jpg"))
    gate.set()


def test_queue_drops_jobs_past_the_cap(tmp_path):
    ran: list[str] = []
    folder = P.gen_dir(tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    N.write(
        folder / "budget.json",
        json.dumps({__import__("time").strftime("%Y-%m-%d"): 4}).encode(),
    )
    with patch.object(P, "paint", lambda job, *a, **k: ran.append(job["key"])):
        P.start(_jobs(1)[0], tmp_path, "k", cap=4)
        _wait(lambda: not P._PAINTING)
    assert ran == []


def test_stopped_cutout_is_dropped_and_holds_the_sheet(tmp_path):
    import threading

    gate = threading.Event()

    def slow(key, picture, data_dir, fal_key, stop=None):  # noqa: ARG001
        gate.wait(5)
        return None

    with patch.object(M, "make", slow):
        assert M.start("a", b"x", tmp_path, "k", "A bird")
        assert M.running()[0]["name"] == "A bird"
        assert M.stop_all() == 1
        assert not M.running() and not M._RUNNING
        assert not M.start("b", b"x", tmp_path, "k")  # held after a stop
        assert M.start("b", b"x", tmp_path, "k", force=True)
        gate.set()
        _wait(lambda: not M._RUNNING)
    M._HOLD_UNTIL = 0.0


def test_limits_are_the_owners_to_set():
    P, M = server.P, server.M
    assert P.daily_cap({"gen_daily_cap": 500}) == 500
    assert P.daily_cap({"gen_daily_cap": "7"}) == 7
    assert P.daily_cap({"gen_daily_cap": 0}) == P.UNLIMITED
    assert P.daily_cap({"gen_daily_cap": ""}) == P.GEN_DAILY_CAP
    assert P.daily_cap({"gen_daily_cap": "lots"}) == P.GEN_DAILY_CAP
    assert P.daily_cap({}) == P.GEN_DAILY_CAP
    try:
        P.configure({"gen_per_refresh": 9, "gen_parallel": 1})
        M.configure({"mask_daily_cap": 0, "mask_per_refresh": 20})
        assert (P.per_fetch(), P._PARALLEL, M._cap, M.per_fetch()) == (
            9,
            1,
            P.UNLIMITED,
            20,
        )
        P.configure({"gen_per_refresh": 0, "gen_parallel": 99})
        assert (P.per_fetch(), P._PARALLEL) == (1, 8)
    finally:
        P.configure({})
        M.configure({})
    assert (P.per_fetch(), P._PARALLEL, M._cap) == (
        P.PER_FETCH,
        P.BATCH_PARALLEL,
        M.DAILY_CAP,
    )


def test_tray_batches_paintings_and_cutouts():
    tray = P.T

    class FakeJobs:
        def __init__(self):
            self.log = []

        def report(self, source, key, title, **kw):
            self.log.append(
                ("report", key, kw["progress"], kw["detail"], kw["stop"] is not None)
            )

        def finish(self, source, key, detail=""):
            self.log.append(("finish", key, detail))

        def cancel(self, source, key, detail=""):
            self.log.append(("cancel", key, detail))

    fake = FakeJobs()
    old, tray.jobs = tray.jobs, fake
    try:
        b = tray.Batch("paint", "Painting specimens")
        b.add("a", lambda: None)
        b.add("b", lambda: None)
        b.add("b", lambda: None)  # queued twice counts once
        b.done("a")
        b.done("a")  # idempotent
        assert fake.log[-1] == ("report", "paint", 0.5, "1 of 2", True)
        b.done("b")
        assert fake.log[-1] == ("finish", "paint", "2 finished")
        b.add("c", lambda: None)  # a new batch starts from zero
        assert fake.log[-1] == ("report", "paint", 0.0, "0 of 1", True)
        b.done("c", stopped=True)
        assert fake.log[-1] == ("cancel", "paint", "0 of 1 finished")
        tray.jobs = None
        b.add("d", lambda: None)  # no tray: no error
        b.done("d")
    finally:
        tray.jobs = old


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


def test_style_previews_come_from_paintings_of_the_preview_species(tmp_path):
    folder = P.gen_dir(tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    for slug in ("corvus_corax--gould", "parus_major--gould", "parus_major--engraving"):
        (folder / f"{slug}.jpg").write_bytes(b"x")
        (folder / f"{slug}.json").write_text(
            json.dumps({"sci": slug.split("--")[0].replace("_", " ").capitalize()})
        )
    tree = server._gallery(tmp_path, 12)["style_tree"]
    files = {
        s["id"]: s["file"] for t in tree for sub in t["subs"] for s in sub["styles"]
    }
    assert files["gould"] == "parus_major--gould.jpg" and files["engraving"]
    assert "anime" not in files


def test_every_option_type_is_one_the_server_installs():
    allowed = {
        "string", "textarea", "variables_textarea", "entity_overrides", "location_search",
        "number", "slider", "select", "multiselect", "boolean", "color",
    }
    manifest = json.loads((Path(__file__).resolve().parent.parent / "plugin.json").read_text(encoding="utf-8"))
    bad = [(o["name"], o["type"]) for o in manifest["cell_options"] if o["type"] not in allowed]
    assert not bad


def test_pictures_past_the_deadline_wait_for_the_next_refresh(tmp_path, offline):
    rows = [{"name": n, "sci": c, "count": 1, "kind": "Aves"} for n, c in (("Australian Magpie", "Gymnorhina tibicen"), ("Rock Dove", "Columba livia"))]
    states = server._attach_pictures(rows, "", "", tmp_path, "colour", budget_s=-1)
    assert states.get("deferred") == 2 and all(r["pic"] is None for r in rows)
    assert server._live_refresh(states) > 0
    rows = [{"name": "Australian Magpie", "sci": "Gymnorhina tibicen", "count": 1, "kind": "Aves"}]
    states = server._attach_pictures(rows, "", "", tmp_path, "colour")
    assert not states.get("deferred") and rows[0]["pic"]


def test_a_stored_sheet_has_to_be_a_picture(tmp_path):
    import base64, io
    from PIL import Image
    from PIL import ImageDraw
    page = Image.new("RGB", (120, 160), (243, 234, 214))
    ImageDraw.Draw(page).ellipse((30, 40, 90, 120), fill=(40, 30, 20))
    buf = io.BytesIO(); page.save(buf, "JPEG")
    good = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    assert server._decodes(good)
    blank = io.BytesIO(); Image.new("RGB", (120, 160), (243, 234, 214)).save(blank, "JPEG")
    assert not server._decodes("data:image/jpeg;base64," + base64.b64encode(blank.getvalue()).decode()), "plain paper is not a sheet"
    assert not server._decodes("data:image/jpeg;base64,AAAA")
    assert not server._decodes("https://example.com/x.jpg")
    assert not server._decodes("data:text/plain;base64," + base64.b64encode(b"hello").decode())
    tiny = io.BytesIO(); Image.new("RGB", (8, 8)).save(tiny, "PNG")
    assert not server._decodes("data:image/png;base64," + base64.b64encode(tiny.getvalue()).decode())


def test_a_blank_stored_sheet_is_dropped_when_read(tmp_path):
    import base64, io
    from PIL import Image
    blank = io.BytesIO(); Image.new("RGB", (120, 160), (243, 234, 214)).save(blank, "JPEG")
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
    gen = tmp_path / "gen"
    made = len(list(gen.glob("*.jpg")))
    assert made >= 1 and (gen / "budget.json").exists()
    server.fetch({**options, "style": "woodblock"}, settings, ctx={**ctx, "preview": True})
    assert server.held(tmp_path)
    (gen / "archive").mkdir(exist_ok=True)
    (gen / "archive" / "x__20260101-000000.jpg").write_bytes(b"x")
    (tmp_path / "masks").mkdir(exist_ok=True)
    (tmp_path / "masks" / "g_x.cut.png").write_bytes(b"x")
    (tmp_path / "masks" / "f_keep.cut.png").write_bytes(b"x")
    got = server.purge_all(tmp_path)
    assert got["paintings"] == made and got["versions"] == 1 and got["cut_outs"] == 1
    assert [f.name for f in gen.iterdir() if f.is_file()] == ["budget.json"]
    assert not list((gen / "archive").iterdir()) and not server.held(tmp_path)
    assert (tmp_path / "masks" / "f_keep.cut.png").exists()
    assert server.P.budget_used(gen) >= made, "the day's count stays"


def test_a_new_look_drops_the_old_looks_held_paintings(tmp_path, offline_paint):
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "count": "3", "style": "gould"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480, "preview": True}
    server.fetch(options, settings, ctx=ctx)
    held = server.held(tmp_path)
    assert held and all("--gould" in k for k in held)
    # the same cell, a new style: what was held for the old one goes
    server.fetch({**options, "style": "woodblock"}, settings, ctx=ctx)
    held = server.held(tmp_path)
    assert held and all("--woodblock" in k for k in held), list(held)
    # a different cell (another place) with its own held paintings is left alone
    other = {**options, "latitude": -33.87, "longitude": 151.21, "location": {"label": "Sydney", "timezone": "Australia/Sydney"}}
    server.fetch(other, settings, ctx=ctx)
    assert any("--gould" in k for k in server.held(tmp_path))
    server.fetch({**options, "style": "linocut"}, settings, ctx=ctx)
    kept = server.held(tmp_path)
    assert any("--gould" in k for k in kept) and any("--linocut" in k for k in kept) and not any("--woodblock" in k for k in kept)


def test_a_new_look_drops_the_old_looks_queued_paintings(tmp_path, offline_paint, monkeypatch):
    """Queued, not yet painting: the workers are kept from starting, so the
    queue holds what a settled preview started; the panel's own jobs stay."""
    monkeypatch.setattr(P, "_work", lambda: None)
    monkeypatch.setattr(server, "SETTLE_S", 0)
    settings = {"fal_api_key": "k", "gen_daily_cap": "24"}
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "count": "3", "style": "gould"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    try:
        # the panel queues its own paintings in the pencil style
        server.fetch({**options, "style": "pencil"}, settings, ctx=ctx)
        assert any("--pencil" in j["key"] for j, *_r in P._QUEUE)
        # the editor: a settled preview queues gould, then moves on to woodblock
        server.fetch(options, settings, ctx={**ctx, "preview": True})
        server.fetch(options, settings, ctx={**ctx, "preview": True})
        assert any("--gould" in j["key"] for j, *_r in P._QUEUE)
        server.fetch({**options, "style": "woodblock"}, settings, ctx={**ctx, "preview": True})
        queued = [j["key"] for j, *_r in P._QUEUE]
        assert not any("--gould" in k for k in queued), queued
        assert any("--pencil" in k for k in queued), "the panel's own paintings stay"
        assert not list((tmp_path / "gen").glob("*--gould*.err")), "nothing written: gould can be picked again"
        # picked again, it is queued again
        server.fetch(options, settings, ctx={**ctx, "preview": True})
        server.fetch(options, settings, ctx={**ctx, "preview": True})
        assert any("--gould" in j["key"] for j, *_r in P._QUEUE)
    finally:
        with P._LOCK:
            P._QUEUE.clear()
            P._PAINTING.clear()
            P._WORKERS = 0



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
    options = {**MELBOURNE, "url": "birdnet.local", "pictures": "gen", "count": "3", "style": "gould"}
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480, "page_name": "Kitchen", "page_icon": "ph-coffee"}
    server.fetch(options, settings, ctx=ctx)
    _settle_paint()
    server.fetch({**options, "style": "woodblock", "count": "2"}, settings, ctx={**ctx, "page_name": "Study", "page_icon": ""})
    _settle_paint()
    # a preview is not a dashboard
    server.fetch({**options, "style": "linocut"}, settings, ctx={**ctx, "preview": True, "page_name": "Draft"})
    got = server.dashboards(tmp_path)
    assert [d["name"] for d in got] == ["Study", "Kitchen"]
    g = server._gallery(tmp_path, 24)
    assert [(x["name"], x["style_label"], len(x["paintings"])) for x in g["groups"]] == [
        ("Study", "Japanese woodblock print", 2),
        ("Kitchen", "Hand-coloured lithograph (Gould)", 3),
    ]
    assert g["rest"] == []
    # the dashboard edited to show fewer: once its old self has not rendered for an hour it goes,
    # and the painting nothing shows any more falls under the rest
    data = json.loads((tmp_path / "dashboards.json").read_text())
    for v in data.values():
        if v["name"] == "Kitchen":
            v["at"] -= 7200
    (tmp_path / "dashboards.json").write_text(json.dumps(data))
    server.fetch({**options, "count": "2"}, settings, ctx=ctx)
    g = server._gallery(tmp_path, 24)
    assert [(x["name"], len(x["paintings"])) for x in g["groups"]] == [("Kitchen", 2), ("Study", 2)]
    assert len(g["rest"]) == 1



def _log(tmp_path, days, seen):
    (tmp_path / "rarity.json").write_text(json.dumps({"days": days, "seen": seen}))


def test_marks_new_and_rare(tmp_path, offline):
    from datetime import date, timedelta

    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(40, 0, -1)]
    # the magpie every day, the rock dove (flagged new by BirdNET-Go) never before, the gull once
    # (one day in forty, plus to-day: within one in twenty)
    _log(tmp_path, days, {"Gymnorhina tibicen": days, "Chroicocephalus novaehollandiae": days[3:4]})
    r = _fetch(tmp_path, offline, url="birdnet.local", marks="both", count="8")
    marks = {s["name"]: s.get("mark", "") for s in r["species"]}
    assert marks["Rock Dove"] == "new" and marks["Australian Magpie"] == ""
    assert marks["Spotted Dove"] == "new", "first on the log to-day: new here"
    assert marks["Silver Gull"] == "rare", "on the log once in forty days"
    r = _fetch(tmp_path, offline, url="birdnet.local", marks="rare", count="8")
    marks = {s["name"]: s.get("mark", "") for s in r["species"]}
    assert marks["Rock Dove"] == "" and marks["Silver Gull"] == "rare" and marks["Australian Magpie"] == ""
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
    assert r["tally"] == "strokes" and s["tally"] == s["count"] and s["tally_line"].endswith("calls")
    assert any(ln.startswith(s["tally_line"]) for ln in s["lines"])
    r = _fetch(tmp_path, offline, url="birdnet.local")
    assert "tally_line" not in r["species"][0]


def test_year_list_from_the_log(tmp_path, offline):
    from datetime import date, timedelta

    today = date.today()
    y = today.year
    d = lambda m, dd: f"{y}-{m:02d}-{dd:02d}"  # noqa: E731
    first = date(y, 1, 3)
    if first > today:
        first = today
    days = [first.isoformat(), today.isoformat()]
    (tmp_path / "species_seen.json").write_text(json.dumps({"Corvus mellori": ["Little Raven", "Aves"]}))
    _log(tmp_path, days, {
        "Gymnorhina tibicen": [first.isoformat(), today.isoformat()],
        "Corvus mellori": [first.isoformat()],
        "Turdus merula": [f"{y - 1}-06-01"],
    })
    r = _fetch(tmp_path, offline, url="birdnet.local", layout="year", count="2")
    # the log's two this year, and the four birds heard to-day, the magpie among both
    assert r["year"]["n"] == 5 and r["year"]["today"] == 4
    names = [s["name"] for s in r["species"]]
    assert names[0] in ("Australian Magpie", "Little Raven") and "Turdus merula" not in names
    raven = next(s for s in r["species"] if s["name"] == "Little Raven")
    assert raven["year_days"] == 1 and raven["today"] is False and raven["lines"][1] == "1 day"
    magpie = next(s for s in r["species"] if s["name"] == "Australian Magpie")
    assert magpie["year_days"] == 2 and magpie["today"] is True and magpie["lines"][1] == "2 days"
    assert len(r["species"]) > 2, "the year list is not cut to the sheet's count"


def test_dawn_and_dusk_pages(tmp_path, offline, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Australia/Melbourne"))
    mins = now.hour * 60 + now.minute

    def sky_with(rise, set_):
        return lambda place, when: {
            "dark": False, "sunrise": f"{rise // 60:02d}:{rise % 60:02d}",
            "sunset": f"{set_ // 60:02d}:{set_ % 60:02d}", "sunset_min": set_, "moon": 0.5, "moon_name": "Full",
        }

    # half an hour before sunrise: the dawn page, the birds in the order they started
    monkeypatch.setattr(server, "_sky", sky_with((mins + 30) % 1440, (mins + 600) % 1440))
    r = _fetch(tmp_path, offline, url="birdnet.local", day_pages="on", count="8")
    assert r["day_page"]["kind"] == "dawn" and r["day_page"]["sun"].startswith("sunrise")
    firsts = [s["first"] for s in r["species"]]
    assert firsts == sorted(firsts) and r["day_page"]["song"].startswith(f"first song {firsts[0]}")
    # just after sunset: the evening page, the last songs latest first (a fixed clock, so the
    # window never wraps midnight whatever the hour this runs)
    rows = [dict(s) for s in r["species"]]
    sky = sky_with(6 * 60, 18 * 60 + 10)(None, None)
    page = server._day_page(rows, sky, datetime(2026, 10, 17, 18, 30))
    assert page["kind"] == "dusk" and page["sun"] == "sunset 18:10" and page["song"] == "last song 17:40 Australian Magpie"
    lasts = [s["last"] for s in rows]
    assert lasts == sorted(lasts, reverse=True)
    # midday: no page; and none without the option
    assert server._day_page(rows, sky, datetime(2026, 10, 17, 12, 0)) is None
    assert _fetch(tmp_path, offline, url="birdnet.local", count="8")["day_page"] is None

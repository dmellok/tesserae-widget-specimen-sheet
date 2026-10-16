"""The packing bench: real specimens, packed by static/sheet.js in a browser, measured.

    python tools/bench.py build          # fetch the corpus into tools/out/corpus (network)
    python tools/bench.py run [--sheet DIR] [--cases a,b] [--tag name]

Each case is a list of specimens as client.js hands them to buildSheet, with the cell it
is drawn in. The run reports, per case, the ink on the page (labels left off), the sum of
the specimens' boxes as a share of the page, the smallest specimen's short side in pixels,
and the pack time, and writes tools/out/bench/<tag>_<case>.jpg to look at.
"""
from __future__ import annotations

import argparse
import base64
import functools
import http.server
import importlib.util
import json
import sys
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
CORPUS = OUT / "corpus"
MELBOURNE = (-37.81, 144.96, "Melbourne")
UK_BIRDS = [("Eurasian Blackbird", "Turdus merula"), ("Great Tit", "Parus major"), ("Eurasian Wren", "Troglodytes troglodytes"), ("European Robin", "Erithacus rubecula"), ("Common Wood Pigeon", "Columba palumbus"), ("Eurasian Magpie", "Pica pica"), ("Blue Tit", "Cyanistes caeruleus"), ("Common Chaffinch", "Fringilla coelebs"), ("Eurasian Jay", "Garrulus glandarius"), ("House Sparrow", "Passer domesticus"), ("Great Spotted Woodpecker", "Dendrocopos major"), ("Long-tailed Tit", "Aegithalos caudatus"), ("Eurasian Goldfinch", "Carduelis carduelis"), ("Eurasian Nuthatch", "Sitta europaea"), ("Common Kingfisher", "Alcedo atthis")]


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"ss_{name}", ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"ss_{name}"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _specimens(server: Any, rows: list[dict[str, Any]], source: str, lines: tuple[str, ...], sizing: str) -> list[dict[str, Any]]:
    server._size(rows, sizing)
    out = []
    for r in rows:
        p = r.get("pic") or {}
        if not p.get("src"):
            continue
        out.append({
            "name": r["name"], "sci": r.get("sci", ""), "src": p.get("src"), "cut": p.get("cut"),
            "on_paper": bool(p.get("on_paper")), "classic": bool(p.get("classic")), "whole": False,
            "factor": r["factor"], "aspect": p.get("aspect") or 0.8, "paper": None, "neutral": False,
            "lines": server._label_lines(r, lines, source, "today"),
        })
    return out


def build() -> None:
    server = _module("server")
    sources = _module("sources")
    data = OUT / "data"
    data.mkdir(parents=True, exist_ok=True)
    CORPUS.mkdir(parents=True, exist_ok=True)
    cases: dict[str, dict[str, Any]] = {}
    for tag, classic in (("birds_classic", True), ("birds_public", False)):
        rows = [{"name": n, "sci": c, "count": 10 + 3 * i, "kind": "Aves", "last": "17:40"} for i, (n, c) in enumerate(UK_BIRDS)]
        server._attach_pictures(rows, "", "", data, "colour", classic=classic)
        cases[tag] = {"source": "birdnet_go", "cell": [1200, 1600], "specimens": _specimens(server, rows, "birdnet_go", ("name", "count"), "compact")}
    today = date.today()
    for tag, taxa, n in (("beetles_gbif", ["1470"], 13), ("birds_gbif_32", ["212"], 32)):
        q = sources.gbif_query(MELBOURNE, "10", taxa, datetime.now().month)
        raw, err = sources.from_gbif(q)
        if not raw:
            print("gbif", tag, err)
            continue
        rows = sources.rows_gbif(raw, today, data, lookups=n + 8)[: n + 4]
        server._attach_pictures(rows, "", "", data, "colour", classic=False)
        spec = _specimens(server, rows, "gbif", ("name", "count"), "compact")[:n]
        cases[tag] = {"source": "gbif", "cell": [1200, 1600], "specimens": spec}
    cases["birds_three"] = {"source": "birdnet_go", "cell": [800, 480], "specimens": cases["birds_classic"]["specimens"][:3]}
    cases["birds_small_cell"] = {"source": "birdnet_go", "cell": [400, 240], "specimens": cases["birds_classic"]["specimens"][:6]}
    for tag, case in cases.items():
        (CORPUS / f"{tag}.json").write_text(json.dumps(case), encoding="utf-8")
        print(tag, len(case["specimens"]), "specimens")


_PAGE_JS = """async ([spec, cell]) => {
  const m = await import('/static/sheet.js');
  const [w, h] = cell;
  const label = Math.max(0.017, 13 / h);
  const t = performance.now();
  const sheet = await m.buildSheet({ specimens: spec, ratio: w / h, size: 1400, label, paper: 'cream', ink: '#2a2116', spacing: 'airy', layout: 'cluster', facing: 'inward', zoom: 1 });
  const ms = performance.now() - t;
  if (!sheet) return { ms, none: true };
  const img = new Image(); img.src = sheet.src; await img.decode();
  const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
  const x = c.getContext('2d'); x.drawImage(img, 0, 0);
  const d = x.getImageData(0, 0, c.width, c.height).data; let ink = 0;
  for (let i = 0; i < d.length; i += 4) if (Math.abs(d[i] - 243) + Math.abs(d[i + 1] - 234) + Math.abs(d[i + 2] - 214) > 40) ink++;
  let boxes = 0, smallest = Infinity;
  for (const it of sheet.items) { boxes += it.box[2] * it.box[3]; smallest = Math.min(smallest, Math.min(it.box[2] * c.width, it.box[3] * c.height)); }
  x.fillStyle = '#2a2116'; x.textAlign = 'center'; x.textBaseline = 'top';
  const fs = label * c.height; x.font = `${fs}px Georgia`;
  for (const it of sheet.items) { const s = spec.find((q) => (q.sci || q.name) === (it.sci || it.name)); const lines = (s && s.lines) || [it.name];
    lines.flatMap((l) => (m.wrapLabel && m.wrapsAt && m.wrapsAt(label) ? m.wrapLabel(l) : l).split('\\n')).forEach((ln, k) => x.fillText(ln, it.at[0] * c.width, it.at[1] * c.height + fs * 0.25 + k * fs * 1.15)); }
  return { src: c.toDataURL('image/jpeg', 0.9), ink: ink / (c.width * c.height), boxes, smallest: Math.round(smallest), ms: Math.round(ms), n: sheet.items.length, w: c.width, h: c.height };
}"""


def run(sheet_dir: Path, cases: list[str] | None, tag: str) -> None:
    from playwright.sync_api import sync_playwright

    (OUT / "bench").mkdir(parents=True, exist_ok=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(sheet_dir))
    handler.log_message = lambda *a: None  # type: ignore[method-assign]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    files = sorted(CORPUS.glob("*.json"))
    if cases:
        files = [f for f in files if f.stem in cases]
    print(f"{'case':18} {'n':>3} {'ink':>6} {'boxes':>6} {'small':>6} {'ms':>6}")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.on("console", lambda msg: print("  LOG", msg.text))
        pg.goto(f"http://127.0.0.1:{port}/README.md")
        for f in files:
            case = json.loads(f.read_text(encoding="utf-8"))
            res = pg.evaluate(_PAGE_JS, [case["specimens"], case["cell"]])
            if res.get("none"):
                print(f"{f.stem:18} nothing placed ({res['ms']} ms)")
                continue
            (OUT / "bench" / f"{tag}_{f.stem}.jpg").write_bytes(base64.b64decode(res["src"].split(",")[1]))
            print(f"{f.stem:18} {res['n']:3} {res['ink']:6.3f} {res['boxes']:6.3f} {res['smallest']:6} {res['ms']:6}")
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["build", "run"])
    ap.add_argument("--sheet", default=str(ROOT), help="directory whose static/sheet.js is packed with")
    ap.add_argument("--cases", default="")
    ap.add_argument("--tag", default="run")
    a = ap.parse_args()
    if a.what == "build":
        build()
    else:
        run(Path(a.sheet), [c for c in a.cases.split(",") if c], a.tag)

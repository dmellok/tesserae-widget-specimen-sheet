"""Render the widget as a page would: server.fetch() for the data, client.js in a browser.

    python tools/preview.py [--w 1200 --h 1600] [--tag name] [key=value ...]

Options are the cell's (source=gbif location=... count=13); the screenshot lands in
tools/out/preview_<tag>.png and the page's console goes to stdout.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import importlib.util
import json
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
MELBOURNE = {"latitude": -37.81, "longitude": 144.96, "location": {"label": "Melbourne", "timezone": "Australia/Melbourne"}}


def main() -> None:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--w", type=int, default=1200)
    ap.add_argument("--h", type=int, default=1600)
    ap.add_argument("--tag", default="demo")
    ap.add_argument("pairs", nargs="*")
    a = ap.parse_args()
    options = dict(MELBOURNE)
    for pair in a.pairs:
        k, _, v = pair.partition("=")
        options[k] = json.loads(v) if v[:1] in "[{" else v
    spec = importlib.util.spec_from_file_location("ss_server", ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)
    sys.modules["ss_server"] = server
    spec.loader.exec_module(server)  # type: ignore[union-attr]
    data_dir = OUT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    data = server.fetch(options, {}, ctx={"data_dir": str(data_dir), "cell_w": a.w, "cell_h": a.h})
    print("state", data["state"], "species", len(data["species"]), "stored sheet", bool(data.get("sheet")))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *x: None  # type: ignore[method-assign]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    html = f"""<!doctype html><html><body style="margin:0;background:#888"><div id="cell" style="width:{a.w}px;height:{a.h}px"></div>
    <script type="module">
      const m = await import('/client.js');
      const host = document.getElementById('cell');
      const shadow = host.attachShadow({{ mode: 'open' }});
      const data = await (await fetch('/tools/out/preview_data.json')).json();
      const t = performance.now();
      await m.default(shadow, {{ data, cell: {{ w: {a.w}, h: {a.h}, size: 'lg' }} }});
      window.__done = performance.now() - t;
    </script></body></html>"""
    (OUT / "preview_data.json").write_text(json.dumps(data), encoding="utf-8")
    (OUT / "preview.html").write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": a.w, "height": a.h})
        pg.on("console", lambda msg: print("  LOG", msg.type, msg.text[:200]))
        pg.on("pageerror", lambda e: print("  PAGEERROR", str(e)[:300]))
        pg.on("request", lambda r: print("  POST", r.url) if r.method == "POST" else None)
        pg.goto(f"http://127.0.0.1:{port}/tools/out/preview.html")
        pg.wait_for_function("window.__done !== undefined", timeout=120000)
        print("render ms", round(pg.evaluate("window.__done")))
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(OUT / f"preview_{a.tag}.png"))
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()

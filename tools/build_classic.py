"""Build classic.json, the index of Fugleramme's classic plates, from its
manifest.json and geometry.json.

    python tools/build_classic.py <folder with manifest.json and geometry.json> <commit>

The commit is the arnegiacomo/fugleramme revision the plates are fetched from."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def main(folder: str, pin: str) -> None:
    manifest = json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))
    geometry = json.loads((Path(folder) / "geometry.json").read_text(encoding="utf-8"))
    plates: dict[str, list[list[object]]] = {}
    for file in sorted(manifest):
        geo = geometry.get(file)
        stem = re.sub(r"-\d+$", "", file.removeprefix("birds/").removesuffix(".webp"))
        parts = stem.split("-")
        if len(parts) < 2 or "x" in parts or not geo:
            continue
        sci = " ".join(parts).lower()
        entry = manifest[file]
        plates.setdefault(sci, []).append(
            [
                file.removeprefix("birds/").removesuffix(".webp"),
                entry["source"],
                entry["url"],
                [round(v, 3) for v in geo["box"]],
                geo["cut"][0],
                geo["cut"][1],
            ]
        )
    out = {"pin": pin, "plates": plates}
    (HERE / "classic.json").write_text(
        json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
    print(len(plates), "species,", sum(len(v) for v in plates.values()), "plates")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

# Attribution

## Classic plates

The classic plates are the cut-out bird artwork of **Fugleramme** by Arne Giacomo,
<https://github.com/arnegiacomo/fugleramme>, taken from the `assets/artwork/classic/birds`
folder at commit `9976c817b4d7105123daa4906272cc8b14c27ed1`.

- Artwork: CC BY-SA 4.0 (<https://creativecommons.org/licenses/by-sa/4.0/>). The plates are
  cut from scans of works by John Gould, the von Wright brothers, J. J. Audubon, H. E. Dresser,
  L. A. Fuertes, J. G. Keulemans, F. Levaillant, C. R. Bree, P. F. von Siebold, W. Swainson,
  J. Cassin, J. F. Naumann, C. J. Temminck, Edward Lear and R. B. Sharpe; the source
  `ATTRIBUTION.md` in that repository lists each work and where its scan came from.
- Code: MIT. The Spiral and Voids packings in `static/pack.js` are adapted from Fugleramme's
  `render/packing.py` and the size search in `render/collage.py`; its copyright and permission
  notice are kept at the head of that part of the file.

What this widget ships is only `classic.json`, an index (file name, source page, trimmed
bounding box, size) built by `tools/build_classic.py` from that repository's `manifest.json` and
`geometry.json`. A plate itself is downloaded from the pinned commit the first time a species
needs it and kept in the widget's data folder. It is resized, and for the greyscale and
black-and-white settings converted, which is an adaptation under the licence; the credit
"Classic plates after A, B and C, cut by Arne Giacomo (Fugleramme), CC BY-SA 4.0" is printed at
the foot of any sheet that shows one. A painting made with a classic plate as its reference
picture is a derivative of it and carries the same licence.

## Bundled plates

The public-domain plates in `plates.json` are credited to their sources on the sheet.

## Style samples

The pictures in `static/styles/`, one per painting style, shown in the Style picker, are AI
paintings (Nano Banana on fal.ai) of a great tit, made by `tools/style_previews.py` from one
photograph, used as the reference picture:

- "Great tit side-on" by Francis C. Franklin, Wikimedia Commons,
  <https://commons.wikimedia.org/wiki/File:Great_tit_side-on.jpg>, CC BY-SA 3.0
  (<https://creativecommons.org/licenses/by-sa/3.0/>).

The samples are adaptations of that photograph and carry the same licence, CC BY-SA 3.0, with
the same credit.


## Bird stickers

The bird stickers are **huashu-bird-sticker** by Huashu (花叔), <https://github.com/alchaincyf/huashu-bird-sticker>:
one transparent sticker with a white edge for each of the 11,064 living bird species of the IOC
World Bird List v15.2, drawn by an image model from a reference photograph (or a board of
several) and reviewed against it for field marks. The repository records the reference, its
author and licence per species; the reference photographs are not included there and not used
here.

- Stickers: PolyForm Shield 1.0.0 (<https://polyformproject.org/licenses/shield/1.0.0/>),
  copyright 2026 Huashu (alchaincyf). Any use, including commercial, is permitted except a
  product that competes with the licensor's line of business, Mingji (鸣迹), a bird-watching
  notebook and bird identification app with an illustrated field guide. Required notice:
  "Copyright (c) 2026 Huashu (alchaincyf), https://github.com/alchaincyf/huashu-bird-sticker".
- Index data (`data/species.json`, from which `stickers.json` here is built): MIT.

What this widget ships is only `stickers.json`, an index (family folder, file stem and review
score per species) built by `tools/build_stickers.py` from that repository's `data/species.json`
at the pinned revision. A sticker is downloaded from that revision the first time a species needs
it and kept in the widget's data folder; it is resized and, for greyscale and black-and-white
panels, converted. The credit "Bird stickers by Huashu, huashu-bird-sticker
(github.com/alchaincyf/huashu-bird-sticker)" is printed at the foot of any sheet that shows one.
A painting made with a sticker as its reference picture is credited to the sticker as well.

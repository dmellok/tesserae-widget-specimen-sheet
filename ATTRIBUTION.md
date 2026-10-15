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
- Code: MIT. No Fugleramme code is used here.

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

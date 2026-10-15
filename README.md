# Specimen Sheet

A [Tesserae](https://github.com/dmellok/tesserae) widget: the birds and wildlife around the panel, cut out and laid out together on one printed page.

![The demo garden, painted after Gould](screenshots/lg.png)

One widget, three sources, one page:

| Source | What the sheet shows | The count under a name |
| --- | --- | --- |
| **BirdNET-Go** | The birds the microphone heard to-day (or this week) | calls, with when it was last heard |
| **iNaturalist** | What people have found near a place lately, or one person's own observations | observations, with the date and the observer |
| **GBIF** | What has been recorded near a place in this month of the year | records |

Until you point it at a BirdNET-Go server it runs a **demo garden** for the cell's location, so the page can be tried without a microphone. iNaturalist and GBIF need a place (or, for iNaturalist, a username).

## The page

Each specimen is cut out of its picture and the cut-outs are packed onto one sheet of paper: the largest in the middle, the rest in pairs of like size on either side of it, mirrored across the page and turned to face in, sized true to life (a crow large, a wren small) or by how often each was heard or recorded. Several arrangements are tried and the most balanced kept. Sizes default to close to life with the range narrowed, so the page packs tighter (true to life, by how often each was heard, or all one size are the other choices). A caption is tucked into the air under its specimen's outline rather than under its bounding box. Labels sit under each specimen as captions or as specimen tags, or the page carries numbers with a key beside it. A species with no picture yet is drawn as an empty mount of the size it will take, so nothing jumps when its picture lands. The sheet is packed in a worker, so the page (and the editor whose preview it is) never waits on it, and a finished sheet is remembered in the browser under what went into it, so a refresh with the same pictures shows at once. A classic plate's own paper is scaled to white and printed onto the sheet's paper, so the pale halo round a plate disappears. A plate or painting on plain paper is cut by its edges whenever that keeps more of the drawing than the model's mask did, so a bird keeps the branch it was painted on.

![Eleven birds](screenshots/extra-1.png)

**Layout** sets the arrangement: a cluster round the largest specimen in mirrored pairs, a packed sheet that fits each specimen into the nearest gap left by its neighbours (the densest page, though a bird sits wherever it fits rather than in pairs), a scatter over the whole page, a band along the middle, a column down a tall panel, a grid with a cell each, or a hero: one specimen alone, the first by Order (the latest, the rarest, the most heard), with the others named beneath, for a small frame. **Facing** turns each specimen toward the middle or leaves it as painted. **Order** picks which species make the sheet when more were heard or recorded than fit, and the order of a numbered key: the most or least heard, the rarest here (the fewest days heard in the log the widget keeps), the most recently heard or seen, new arrivals first, A to Z, or a mix that changes daily. **Leave out** drops species from the sheet, one per line: a common or scientific name, a genus (`Columba` drops every pigeon in it), a kind (`Insecta`, `insects`, `Birds`) or a pattern such as `*gull`. An excluded species frees its place for the next one. The plugin's settings carry an **Always leave out** list that applies to every cell; the widget's page under Widgets lists every species it has shown, to copy from.

After dark, with BirdNET-Go, the page can show the garden asleep (the moon, when it went quiet, the last bird heard) or keep only the night's birds.

## Pictures

A species gets the best picture the widget has to-day and moves up as better ones arrive:

1. **A classic plate**, cut out already and laid out as it is: with **Classic plates** set to Fugleramme's, the plates cut by Arne Giacomo's [Fugleramme](https://github.com/arnegiacomo/fugleramme) (some 1,200 hand-coloured plates of 700 species after Gould, the von Wright brothers, Audubon and others, CC BY-SA 4.0, fetched from GitHub the first time a species needs one and credited at the foot of the page); then the bundled public-domain plates (Audubon, Gould, Lear and others), for the birds the pack covers. A plate that comes cut out needs no cut-out job, and a bird is sized by the bird in it, not by the margin round it.
2. **The record's own photo** (iNaturalist, GBIF) or BirdNET-Go's thumbnail, credited at the foot of the page.
3. **An AI painting**, with **Pictures** set to AI paintings and a fal.ai key: each species is painted once per style from a photo of it, through the model you pick on fal.ai (Nano Banana by default; Nano Banana Pro, FLUX.1 Kontext Pro, GPT Image 2 or Seedream 4), on plain white paper so it cuts out cleanly, at about US$0.04 a species, three new paintings per refresh (both numbers are settings) under a daily limit you set. **Reference picture** says what the model is shown to paint from: the photo, the classic plate of the species, or both (a bird in flight, or a species with no plate, always uses the photo, and FLUX.1 Kontext takes only one picture). **Start paintings** can be set to a button: a refresh, or editing the form, then spends nothing and only notes what is missing (the foot says "3 paintings waiting for Start painting"). The paintings page always carries the button: **Start painting** when paintings are waiting, **Render now** otherwise, which paints the species that have been on a frame in the last three days in each style, model and reference a sheet last rendered with. The sheet shows the plate or photo until each painting lands, and the foot counts "painting 3 of 8".

Sixty-eight styles in three kinds, each in subgroups, or your own prompt. **Realistic**: *plates and paintings* (hand-coloured lithograph after Gould, watercolour after Audubon, scientific plate after Keulemans, modern watercolour, black-ink engraving, field sketch) and *greys and tones, for grey panels* (pencil study, toned lithograph, platinum print, charcoal, mezzotint). **Decorative**: *Nouveau and Deco* (Mucha, Art Nouveau illustration, Arts and Crafts after Morris, Jugendstil, Glasgow style, Vienna Secession, Art Deco, Beardsley ink, a Nouveau stained window, a Golden Age fairy-tale plate), *prints and posters* (Japanese woodblock, linocut, mid-century modern after Charley Harper, gouache poster, risograph, screen print, vintage stamp), *around the world* (Japonisme, chinoiserie, a Persian miniature, a medieval bestiary, a Byzantine icon, folk art, a Delft tile, ink and wash, an Edwardian cigarette card), *glass, thread and paper* (stained glass, mosaic, embroidery, paper cut, pointillism), *black and white, for 1-bit panels* (scraperboard, woodcut, stipple, silhouette, ink brush; drawn without mid-tones) and *graphic greys* (halftone, technical drawing). **Fun**: *anime* (anime, shonen action, manga ink, retro 90s anime), *made of things* (pixel art, origami, needle felt, a clay figure, brick-built), *characters* (grumpy, superhero, a Victorian portrait, a pirate, hats and monocles) and *birds aren't real* (a surveillance unit in watercolour, a camera for a head in watercolour, a patent drawing, a clockwork automaton, a drone). The style list in the widget form is grouped the same way. The styles whose picture is a whole object (the tile, the card, the portrait in its frame, the illuminated page, the window) are kept whole on the sheet rather than cut out. A third of the birds are painted in flight so a page is not all perches; a fungus is cut from its log, an insect pinned as in an entomological plate. **Model** picks which fal.ai model paints: Nano Banana is faithful and cheap, Nano Banana Pro follows an unusual instruction better, FLUX.1 Kontext Pro adheres most strongly to a style, GPT Image 2 is a good likeness with a narrower range, Seedream 4 is the budget one; each model keeps its own paintings, so switching throws nothing away. **One dapper specimen a day** paints one species in a small hat, and perhaps a monocle, in whatever style the sheet is in: the same one all day, another to-morrow.

### Cut-outs

With a fal.ai key, every picture on the sheet (plate, photo or painting) gets an alpha mask from a background-removal model (BiRefNet, well under a cent a picture, once per picture). The mask is checked before it is trusted: the foreground has to be a sensible share of the frame, touch at most two edges, and have one piece that clearly outweighs the rest; that piece is kept (a plate's second bird or its caption is dropped), pinholes filled, specks removed. A mask that fails the check is remembered and the picture falls back to a soft oval, never a rectangle. Without a key, a plate on plain paper is cut by spreading in from its edges and a photo is shown as a soft oval.

### The painted scene

With AI paintings on, **Arrangement** can ask fal.ai to repaint the whole sheet as one picture with one background: on a shared branch, through a tree, round a bird bath, along a wire, at a feeder, in a meadow, all in flight, at a wetland's edge, in a cottage garden, or as a vignette of foliage. It is painted once every specimen is painted and cut out, again when the line-up changes and no more often than the interval you pick, at about US$0.04 each time. The model takes liberties with a scene whatever it is asked, so the names go in a legend under the picture rather than on the specimens.

![A scene on a shared branch](screenshots/extra-2.png)

## The paintings page

Under **Widgets → Specimen Sheet**: a Styles card that lays out every style by kind and subgroup with a preview of each (a Great Tit painted in it; a style without one has a Preview button that paints it, about US$0.04, and the preview shows beside the style picker); every painting with its credit and cut-out state, a form to paint one species in any style (dapper or not), a button to paint the species that have been on a frame in the last three days in one style (not everything ever seen), repaint, cut out again, delete, earlier versions kept and restorable, to-day's count against the limit with a reset, a render queue (what fal.ai is painting now and what waits, up to three at a time, with a Stop on each line and a Stop all), and the list of species seen. A stopped painting is not started again by the sheet for six hours, and Stop all also pauses the sheet's own cut-outs for half an hour; a painting already under way has been paid for, so its result is dropped rather than recalled.

Paintings and cut-outs in progress also show in Tesserae's job tray at the bottom right of every admin page (Tesserae 0.454 and later): a bar counting the batch, a Stop that stops it all, and it stays put as you move between pages.

## Panels

Colour, greyscale and black-and-white. Pictures are converted and contrast-stretched for the greyscale settings; on black and white the type is thickened so the fine serif survives dithering.

## Settings

Plugin settings: the fal.ai key; the limits, all yours to set (the paintings made in a day, how many one refresh may start, how many are made at once, the cut-outs in a day and per refresh; 0 on a daily limit is no limit); and the Always leave out list. The key is only ever sent to `fal.run`.

## Network

The manifest declares `network:*` because the BirdNET-Go address is whatever you type in. What the widget reaches: your BirdNET-Go address; `api.inaturalist.org` and `inaturalist-open-data.s3.amazonaws.com`; `api.gbif.org`; `commons.wikimedia.org` and `upload.wikimedia.org` for plates; `raw.githubusercontent.com` for Fugleramme's classic plates; `en.wikipedia.org` for a sentence on a species when painting it; and `fal.run` plus `fal.media` only with a key.

## Files

Under the widget's data folder: `result_*.json` (the source's answers, kept for the refresh interval), `img/` (fetched pictures and resized copies), `masks/` (cut-outs and the day's count), `gen/` (paintings, their credits, scenes and collages, the day's count) and `gen/archive/` (earlier paintings), `rarity.json` (the days each bird was heard), `species_seen.json`.

## Where it came from

A rebuild of the Backyard Birds, Nature Nearby and Nature This Month collage pages as one widget, dedicated to the page. Those widgets keep their other layouts. The specimen sheet, the day's birds cut from old plates and laid out together on one page with the larger birds in the middle, follows [Fugleramme](https://github.com/arnegiacomo/fugleramme) by Arne Giacomo, a BirdNET-Go bird frame for a Raspberry Pi and an e-ink panel; the layout code here is a separate implementation, but with **Classic plates** on, the plates themselves are theirs (see Licence).

## Fonts

IM Fell English (SIL Open Font License) for the printed page; Archivo Narrow (SIL OFL) on black-and-white panels. Licences are in `fonts/`.

## Licence

AGPL-3.0-or-later for the code. The bundled plates are public domain. The classic plates from Fugleramme (cut out and recoloured by Arne Giacomo from scans of works by Gould, the von Wright brothers, Audubon and others) are offered under CC BY-SA 4.0, are fetched at run time and not shipped in this repository, and are credited on every sheet that shows one; a painting made after a classic plate, and any recut copy of one, carries the same licence. See `ATTRIBUTION.md`. Photos keep their observers' licences, as credited; AI paintings are credited to the model and the photo they were painted after.

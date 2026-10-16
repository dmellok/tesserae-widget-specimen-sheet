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

Each specimen is cut out of its picture and the cut-outs are packed onto one sheet of paper, each as large as the page allows. The packer rasters every outline in three layers (the body, a little air round it, and the strip its caption needs) so the footprints interlock: a body never touches another's air or caption, air may overlap air, and a caption may sit in a neighbour's air. The largest goes first, each on the nearest free spot to the middle and turned to face it; the common scale is the largest at which everything fits; then each specimen grows where it stands or moves to the nearest gap that takes it, the whole is pulled inward, and the arrangement is scaled to the margins. Sizes are close to life by default (a crow large, a wren small, the range narrowed so the page packs tighter), true to life, or all alike.

Captions are measured, not guessed, and a page carries what it can: every line, wrapped, on a tall page; the name alone on a middling one or when many specimens would stack too much caption; none on a small cell, where a caption would be wider than its specimen.

![Eleven birds](screenshots/extra-1.png)

**How many** is a slider from 2 to 32 specimens, and **Scale** runs from 40 to 250%: below 100 it packs the arrangement into the middle of the page with wider margins, above 100 it shows fewer, bigger specimens (the first by Order; 200% shows about a quarter as many). **Layout** is a cluster, a grid with a cell each, or a hero: one specimen alone, the first by Order, with the others named beneath. A hero can carry **a note about the specimen** (a sentence or two from Wikipedia) under the picture, and have **an ornate rule frame** drawn round it, or, with AI paintings on, **an ornate backdrop painted in the style**: a plate of its own, the specimen large on a patterned or foliate ground within an ornamental border, shown whole. **Order** picks which species make the sheet when more were heard or recorded than fit: the most heard, the rarest (new arrivals first), the latest, or a daily mix. **Labels** are captions (the lines you pick: name, scientific name, count, last seen, new or returning, observer, length) or none. **Title line** puts the place, the period and the date above the page, below it, or nowhere, so the specimens get the room.

A finished sheet is sent back to the server and inlined with the next data for its cell, so a cell is packed once per change rather than once per render, and a panel never waits on the packer. Until then the page shows every specimen as an empty mount, each drawn in as its cut-out is ready.

After dark, with BirdNET-Go, the page can show the garden asleep (the moon, when it went quiet, the last bird heard) or keep only the night's birds.

## Pictures

Up the ladder, for each species: an AI painting when one is kept, else a classic plate (Fugleramme's, about 700 species, cut already), else a plate from the bundled pack, else the record's own photo (or, for a bird, the first openly licensed iNaturalist photo). Plates and paintings on plain paper are cut by their edges, so a bird keeps its branch; a photo is a soft oval unless the model cuts it out.

**AI paintings** need a fal.ai key. Twelve styles (a Gould lithograph, a watercolour after Audubon, a modern watercolour, an engraving, a field sketch, a pencil study, a Japanese woodblock print, a linocut, Art Nouveau after Mucha, folk art, ink and wash, stained glass) or **Build my own**: a pick for each element of a style (medium, period or school, palette, line, texture, mood) and a line of your own words, joined into a prompt; each combination is a style of its own, painted once per species and kept. Each painting is made from the species' reference photo, the bird or beetle alone on plain white paper, so it cuts out cleanly. A few new paintings start per refresh, within the daily limit; **Until a species is painted** can leave unpainted species off the sheet. While a dashboard is being edited nothing is painted: the editor's previews hold their paintings and the job tray shows "N paintings waiting" with a **Paint now** button (Tesserae 0.457 and later); a panel's own render, and Render now on the paintings page, paint as usual. Change the style, pose or model while editing and the paintings held or queued for the look you left are dropped (one already under way finishes, being paid for); a panel's own paintings are never dropped. The paintings page groups the paintings by the dashboard showing them, in the sheet's order, with the ones no panel has shown in the last month under Not on a dashboard.

**Pose** paints every bird perched, every bird in flight, or a mix (about a third in flight, by name). A perched bird sits on a plant native to the cell's location: a magpie seen in Melbourne on a wattle or a eucalyptus, one seen in London on a hawthorn or an oak, from a table of regions and their plants; the scene names the same plants. Changing the pose paints every bird again, within the daily limit.

**One dapper specimen a day** paints one species, picked by the date, in a small hat in whatever style the sheet is in: its own painting, kept beside the plain one.

### The scene

With AI paintings on, **Arrangement** can ask fal.ai to repaint the whole sheet as one picture with one background: the sheet's cut-outs go on a collage and the model paints them into the scene you describe. **The scene** is a line in your own words, with examples to start from (a Victorian vignette, one shared branch, through a tree, round a bird bath, along a wire, at a feeder, in a meadow, a cottage garden, the forest floor, a wetland edge, in flight); the model may move and resize the specimens as the scene needs, or keep every one where the sheet put it. The scene is painted once every specimen on it is painted and cut out, and again when the line-up changes, no more often than you set; until then the sheet shows.

### Cut-outs

With a fal.ai key, every picture on the sheet (plate, photo or painting) gets an alpha mask from a background-removal model (BiRefNet, well under a cent a picture, once per picture). The mask is checked before it is trusted: the foreground has to cover a sensible share of the frame, touch at most two edges and hold together as one piece; a mask that fails is kept as bad, not retried, and the page cuts the picture itself. The soft shadow a painting leaves under its subject is left out, and a white bird keeps its white feathers.

## The paintings page

Under Widgets: every painting this widget has made, with earlier versions kept; buttons to paint a species in a style, repaint, cut out again, delete or bring a version back; Render now, which paints what the sheets on your frames are missing within the daily limit; and the species seen, for the Leave out lists.

## Panels

Colour, greyscale and black-and-white. Pictures are converted and contrast-stretched for the greyscale settings; on black and white the type is thickened so the fine serif survives dithering.

## Settings

Plugin settings: the fal.ai key; the limits, all yours to set (the paintings made in a day, how many one refresh may start, how many are made at once, the cut-outs in a day and per refresh; 0 on a daily limit is no limit); and the Always leave out list. The key is only ever sent to `fal.run`.

## Network

The manifest declares `network:*` because the BirdNET-Go address is whatever you type in. What the widget reaches: your BirdNET-Go address; `api.inaturalist.org` and `inaturalist-open-data.s3.amazonaws.com`; `api.gbif.org`; `commons.wikimedia.org` and `upload.wikimedia.org` for plates; `raw.githubusercontent.com` for Fugleramme's classic plates; `en.wikipedia.org` for a sentence on a species when painting it; and `fal.run` plus `fal.media` only with a key.

## Files

Under the widget's data folder: `result_*.json` (the source's answers, kept for the refresh interval), `img/` (fetched pictures and resized copies), `masks/` (cut-outs and the day's count), `gen/` (paintings, their credits, the day's count) and `gen/archive/` (earlier paintings), `rarity.json` (the days each bird was heard), `species_seen.json`, and `sheets/` (the last sheet each cell sent back).

## Where it came from

A rebuild of the Backyard Birds, Nature Nearby and Nature This Month collage pages as one widget, dedicated to the page. Those widgets keep their other layouts. The specimen sheet, the day's birds cut from old plates and laid out together on one page with the larger birds in the middle, follows [Fugleramme](https://github.com/arnegiacomo/fugleramme) by Arne Giacomo, a BirdNET-Go bird frame for a Raspberry Pi and an e-ink panel; the layout code here is a separate implementation, but with **Classic plates** on, the plates themselves are theirs (see Licence).

## Fonts

IM Fell English (SIL Open Font License) for the printed page; Archivo Narrow (SIL OFL) on black-and-white panels. Licences are in `fonts/`.

## Licence

AGPL-3.0-or-later for the code. The bundled plates are public domain. The classic plates from Fugleramme (cut out and recoloured by Arne Giacomo from scans of works by Gould, the von Wright brothers, Audubon and others) are offered under CC BY-SA 4.0, are fetched at run time and not shipped in this repository, and are credited on every sheet that shows one; a painting made after a classic plate, and any recut copy of one, carries the same licence. See `ATTRIBUTION.md`. Photos keep their observers' licences, as credited; AI paintings are credited to the model and the photo they were painted after.

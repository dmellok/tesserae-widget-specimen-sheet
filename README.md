# Specimen Sheet

A [Tesserae](https://github.com/dmellok/tesserae) widget: the birds and wildlife around the panel, cut out and laid out together on one printed page.

One widget, three sources, one page:

| Source | What the sheet shows | The count under a name |
| --- | --- | --- |
| **BirdNET-Go** | The birds the microphone heard to-day (or this week) | calls, with when it was last heard |
| **iNaturalist** | What people have found near a place lately, or one person's own observations | observations, with the date and the observer |
| **GBIF** | What has been recorded near a place in this month of the year | records |

Until you point it at a BirdNET-Go server it runs a **demo garden** for the cell's location, so the page can be tried without a microphone. iNaturalist and GBIF need a place (or, for iNaturalist, a username).

## The page

Each specimen is cut out of its picture and the cut-outs are packed onto one sheet of paper, the biggest in the middle, the rest round it, sized true to life (a crow large, a wren small) or by how often each was heard or recorded. Labels sit under each specimen as captions or as specimen tags, or the page carries numbers with a key beside it. The picture is whatever the widget has for the species to-day: a classic natural-history plate from the bundled pack (Audubon, Gould, Lear and others, public domain) or the record's own photo, credited at the foot of the page. A species with no picture yet is drawn as an empty mount of the size it will take.

**Leave out** drops species from the sheet, one per line: a common or scientific name, a genus (`Columba` drops every pigeon in it), a kind (`Insecta`, `insects`, `Birds`) or a pattern such as `*gull`. An excluded species frees its place for the next one. The plugin's settings carry an **Always leave out** list that applies to every cell.

After dark, with BirdNET-Go, the page can show the garden asleep (the moon, when it went quiet, the last bird heard) or keep only the night's birds.

## Panels

Colour, greyscale and black-and-white. Pictures are converted and contrast-stretched for the greyscale settings; on black and white the type is thickened so the fine serif survives dithering.

## Where it is going

This is the first step of a rebuild of the Backyard Birds, Nature Nearby and Nature This Month collage pages as one widget. Still to come, in order: model-made cut-outs for every picture (so a plate with a painted sky no longer arrives as a rectangle), a balanced packer, AI paintings in a short list of styles, and the painted scene. The plan is in the Tesserae repo under `notes/nature-collage-v2-plan.md`.

## Network

The manifest declares `network:*` because the BirdNET-Go address is whatever you type in. What the widget reaches: your BirdNET-Go address, `api.inaturalist.org` and `inaturalist-open-data.s3.amazonaws.com`, `api.gbif.org`, and `commons.wikimedia.org` / `upload.wikimedia.org` for plates.

## Fonts

IM Fell English (SIL Open Font License) for the printed page; Archivo Narrow (SIL OFL) on black-and-white panels. Licences are in `fonts/`.

## Licence

AGPL-3.0-or-later. Plates are public domain; photos keep their observers' licences, as credited.

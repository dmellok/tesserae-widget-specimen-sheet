"""AI paintings: each species painted once per style from a reference photo
of it, through fal.ai's nano-banana edit model, on plain white paper so
the cut-out is clean; kept under data/gen/ with its credit, earlier
versions archived on a repaint, a daily cap on spend, a few new paintings
per refresh in background threads, and one dapper specimen a day in a
small hat. Loaded by server.py by path."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    full = f"_tesserae_plugins.specimen_sheet.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, _HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


N = _sibling("net")
K = _sibling("kinds")
S = _sibling("sources")
C = _sibling("classic")

try:
    from app import artwork  # Tesserae 0.463+: the pictures a widget makes and keeps
except (
    ImportError
):  # an older Tesserae: the sheet still draws, without paintings, and says why
    artwork = None  # type: ignore[assignment]

NEEDS_HOST = "AI paintings need Tesserae 0.463 or later; this server is older, so the plates and photographs show."


class _NoArt:
    """What a collection opens to on a Tesserae without app.artwork: every
    request answers no_host, nothing is started, the note says why."""

    key = ""
    preview = False
    settled = True
    subject = ""
    cell = ""
    cap = 0
    started = 0
    pending = 0
    busy = False
    c = None

    def __init__(self) -> None:
        self.states: dict[str, int] = {}
        self.asked: dict[str, Any] = {}
        self.data_dir = Path(".")

    def request(
        self, key: str, payload: dict[str, Any], *, title: str = "", want: bool = True
    ) -> Any:
        from types import SimpleNamespace

        self.states["no_host"] = self.states.get("no_host", 0) + 1
        return SimpleNamespace(
            key=key,
            state="no_host",
            ready=False,
            path=None,
            meta={},
            why=NEEDS_HOST,
            credit="",
        )

    def url(self, *a: Any, **k: Any) -> str:
        return ""

    def picture(self, *a: Any, **k: Any) -> None:
        return None

    def live(self) -> dict[str, int]:
        return {"live_refresh_s": 0, "live_pending": 0}

    def notes(self) -> list[str]:
        return [NEEDS_HOST] if self.states.get("no_host") else []

    def status(self) -> list[str]:
        return []

    def finish(self, *a: Any, **k: Any) -> None:
        return None


class _NoArtwork:
    """A collection on an older Tesserae: it opens to _NoArt and answers
    nothing to everything else (the paintings page does not exist there)."""

    actions: list[Any] = []
    extra_template: Any = None
    extra_context: Any = None
    _DEFAULTS: dict[str, Any] = {
        "state": "none",
        "why_failed": "",
        "held": {},
        "dashboards": [],
        "listing": [],
        "errors": [],
        "versions": [],
        "budget_used": 0,
        "cap": 0,
        "adopt": False,
        "purge": {"pictures": 0, "versions": 0},
        "render_now": (0, 0),
        "start_held": 0,
        "progress": {
            "running": [],
            "queued": [],
            "finished": [],
            "used": 0,
            "cap": 0,
            "used_text": "",
            "cap_text": "",
            "held": 0,
        },
    }

    def __init__(self, name: str) -> None:
        self.name = name
        self.href = "/plugins/specimen_sheet/"

    def open(
        self,
        ctx: dict[str, Any],
        settings: dict[str, Any] | None,
        *,
        subject: str = "",
        cell: str = "",
    ) -> _NoArt:
        return _NoArt()

    def folder(self, data_dir: Path) -> Path:
        return Path(data_dir) / self.name

    def action(self, *a: Any, **k: Any) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        default = self._DEFAULTS.get(name)
        return lambda *a, **k: default


def _art_error(why: str) -> Exception:
    return artwork.ArtworkError(why) if artwork is not None else RuntimeError(why)


FAL_EDIT = "https://fal.run/fal-ai/nano-banana/edit"
# The models a painting may be made with, all on fal.ai and all painting
# from a reference picture so the species stays right. The default keeps
# the plain key; another model's paintings carry its id in theirs.
MODELS: dict[str, dict[str, str]] = {
    "nano_banana": {
        "label": "Nano Banana",
        "endpoint": "fal-ai/nano-banana/edit",
        "cost": "about US$0.04",
    },
    "nano_banana_pro": {
        "label": "Nano Banana Pro",
        "endpoint": "fal-ai/nano-banana-pro/edit",
        "cost": "about US$0.15",
    },
    "flux_kontext": {
        "label": "FLUX.1 Kontext Pro",
        "endpoint": "fal-ai/flux-pro/kontext",
        "cost": "about US$0.04",
    },
    "gpt_image": {
        "label": "GPT Image 2",
        "endpoint": "openai/gpt-image-2/edit",
        "cost": "about US$0.05",
    },
    "seedream": {
        "label": "Seedream 4",
        "endpoint": "fal-ai/bytedance/seedream/v4/edit",
        "cost": "about US$0.03",
    },
}
DEFAULT_MODEL = "nano_banana"


def model_of(model: str | None) -> str:
    return model if model in MODELS else DEFAULT_MODEL


# Styles Nano Banana keeps too close to the photograph (a watercolour for sumi-e, an
# outlined drawing for stained glass): a cell left on Nano Banana paints these with
# GPT Image 2. Only the painting's payload carries it; its key keeps the cell's
# model, so a style moving model keeps the paintings it has.
STYLE_MODEL = {
    "sumie": "gpt_image",
    "stainedglass": "gpt_image",
}


def model_for(style: str | None, model: str | None) -> str:
    """The model a painting in ``style`` is made with: the cell's own, or for a
    style Nano Banana cannot make, GPT Image 2 in its place."""
    model = model_of(model)
    return STYLE_MODEL.get(str(style or ""), model) if model == DEFAULT_MODEL else model


def request_body(
    model: str, prompt: str, image_url: str | list[str], aspect: str
) -> dict[str, Any]:
    """The model's own request shape: the reference picture (or pictures,
    for the models that take several), one output."""
    urls = [image_url] if isinstance(image_url, str) else list(image_url)
    portrait = aspect == "3:4"
    if model == "flux_kontext":
        return {
            "prompt": prompt,
            "image_url": urls[0],
            "num_images": 1,
            "output_format": "jpeg",
            "aspect_ratio": aspect,
        }
    if model == "gpt_image":
        return {
            "prompt": prompt,
            "image_urls": urls,
            "image_size": "portrait_4_3" if portrait else "landscape_4_3",
            "background": "opaque",
            "quality": "medium",
            "output_format": "jpeg",
            "num_images": 1,
        }
    if model == "seedream":
        return {
            "prompt": prompt,
            "image_urls": urls,
            "image_size": "portrait_4_3" if portrait else "landscape_4_3",
            "num_images": 1,
        }
    body: dict[str, Any] = {
        "prompt": prompt,
        "image_urls": urls,
        "num_images": 1,
        "output_format": "jpeg",
        "aspect_ratio": aspect,
    }
    if model == "nano_banana_pro":
        body["resolution"] = "1K"
    return body


WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
ABOUT_MAX = 400
GEN_TIMEOUT_S = 120
GEN_DAILY_CAP = 12
UNLIMITED = 10**9
GEN_RETRY_S = 6 * 3600
PER_FETCH = 3  # new paintings started per refresh
BATCH_PARALLEL = 3
_PARALLEL = BATCH_PARALLEL
_PER_FETCH = PER_FETCH
STOPPED = "stopped"
PROMPT_VERSION = 2
# A style reworded after it shipped gets a revision here; its paintings are
# keyed with it, so only that style is painted again.
STYLE_REVISIONS: dict[str, int] = {
    "cctv": 2,
    "mucha": 2,
    "glasgow": 2,
    "deco": 2,
    "folkart": 2,
    "stainedglass": 2,
}

NOTE_NO_KEY = (
    "AI pictures need a fal.ai key: this widget's own, or the app's under Settings."
)
NOTE_GEN_CAP = "AI paintings: to-day's limit of {cap} is used up; more to-morrow."

# The styles a specimen may be painted in. {kind} is the subject's noun; the
# words are the Backyard Birds widget's, with "plain white paper" in place of
# cream so the cut-out is clean. Groups for the paintings page's pickers.
STYLES: dict[str, tuple[str, str]] = {
    "gould": (
        "Hand-coloured lithograph (Gould)",
        "a plate from a nineteenth-century natural history book: a hand-coloured lithograph in the manner of John Gould, soft watercolour washes over fine engraved linework, on plain pure white paper",
    ),
    "audubon": (
        "Watercolour after Audubon",
        "a life-size watercolour in the manner of John James Audubon's Birds of America: a lively natural pose among a few leaves, watercolour and pastel with fine detail in every mark, on plain pure white paper",
    ),
    "engraving": (
        "Black-ink engraving",
        "a black-ink copperplate engraving from a Victorian encyclopaedia of natural history: fine cross-hatching and stippling, no colour at all, on plain pure white paper",
    ),
    "woodblock": (
        "Japanese woodblock print",
        "a Japanese kacho-e woodblock print in the manner of Ohara Koson: flat areas of colour with a fine dark keyline, a single flowering branch and the texture of washi paper, the {kind} alone on plain pure white paper with no background wash",
    ),
    "linocut": (
        "Linocut",
        "a bold linocut print in black ink with one muted colour: strong carved lines, simple shapes, a little texture from the block, on plain pure white paper",
    ),
    "harper": (
        "Mid-century modern (Harper)",
        "a mid-century modern illustration in the manner of Charley Harper: the {kind} reduced to circles, triangles and clean geometric shapes, flat colour in a handful of tones, no outlines, true to the species' markings, on plain pure white paper",
    ),
    "watercolour": (
        "Modern watercolour",
        "a loose, airy modern watercolour with soft wet edges, a few precise details and plenty of white space, on plain pure white paper",
    ),
    "sketch": (
        "Field sketch",
        "a naturalist's field sketch in graphite with a light watercolour wash: loose construction lines, a few annotations of shape only, on plain pure white paper",
    ),
    "anime": (
        "Anime",
        "a hand-drawn anime film illustration of the {kind}: clean confident line art, soft cel shading, gentle colour, large expressive eyes, still true to the species' markings, on plain pure white paper",
    ),
    "shonen": (
        "Shonen action",
        "a bold shonen manga illustration of the {kind} in a dynamic action pose: thick confident ink lines, hard cel shading, dramatic foreshortening, vivid colour, true to the species' markings, on plain pure white paper",
    ),
    "manga": (
        "Manga ink",
        "a black-and-white manga illustration of the {kind}: crisp ink line art with screentone dot shading and solid blacks, no colour and no grey wash, on plain pure white paper",
    ),
    "anime_retro": (
        "Retro 90s anime",
        "a 1990s anime cel of the {kind}: slightly muted hand-painted cel colours, a dark keyline, simple two-tone shading and a touch of film grain, true to the species' markings, on plain pure white paper",
    ),
    "pixel": (
        "Pixel art",
        "16-bit pixel art sprite of the {kind} with a limited palette, crisp square pixels and no anti-aliasing, true to the species' colours, on plain pure white paper",
    ),
    "origami": (
        "Origami",
        "a flat, minimal origami illustration of the {kind}: folded from bright paper in bold flat planes of the species' own colours, every facet a clean flat shape with crisp straight creases drawn as fine lines, no shading, no gradients and no studio light, graphic and poster-like, on plain pure white paper",
    ),
    "hats": (
        "Hats and monocles",
        "a realistic hand-coloured lithograph in the manner of John Gould on plain pure white paper, except that the {kind} wears a small hat that suits its personality, and perhaps a monocle, painted with the same care",
    ),
}
# ---- black and white, for 1-bit panels: no mid-tones to dither ----
STYLES.update(
    {
        "scraperboard": (
            "Scraperboard",
            "a scraperboard illustration: white lines scratched out of solid black, crisp high contrast, every feather and texture made of fine white strokes, no grey tones at all, the {kind} alone on plain pure white paper",
        ),
        "woodcut": (
            "Woodcut",
            "a bold woodcut print in solid black ink: heavy carved white gouges for the light, no second colour and no grey, the {kind} alone on plain pure white paper",
        ),
        "stipple": (
            "Stipple",
            "a stipple engraving made entirely of small black dots on white, denser where it is dark, as on a banknote portrait, no lines and no grey wash, the {kind} alone on plain pure white paper",
        ),
        "silhouette": (
            "Silhouette",
            "a solid black paper-cut silhouette of the {kind} in profile, as in a Victorian scissor-cut, with a single small white cut for the eye and a crisp edge, on plain pure white paper",
        ),
        "inkbrush": (
            "Ink brush",
            "the {kind} in a few confident strokes of a loaded ink brush, bold and calligraphic, pure black on white with no grey wash, on plain pure white paper",
        ),
        # ---- four greys ----
        "pencil": (
            "Pencil study",
            "a graphite pencil study with firm outlines and hatching in three distinct tones, no soft gradients, as in a draughtsman's notebook, the {kind} alone on plain pure white paper",
        ),
        "litho_toned": (
            "Lithograph, toned",
            "an uncoloured lithographic proof of a nineteenth-century natural history plate: crayon on stone in three flat greys with fine linework, no colour, the {kind} alone on plain pure white paper",
        ),
        "halftone": (
            "Halftone print",
            "a coarse halftone screen print of the {kind}: the picture made of a regular grid of black dots, larger where it is dark, in black, white and one mid grey, the {kind} alone on plain pure white paper",
        ),
        "blueprint": (
            "Technical drawing",
            "a precise technical line drawing of the {kind} in dark blue ink as on an engineer's sheet: clean contour lines, a few dimension lines and small labels in draughtsman's lettering, no shading, on plain pure white paper",
        ),
        # ---- greyscale ----
        "platinum": (
            "Platinum print",
            "a soft, tonal monochrome study of the {kind} in warm greys, finely detailed, like an early platinum print, the {kind} alone against plain pure white paper",
        ),
        "charcoal": (
            "Charcoal",
            "a dramatic charcoal drawing with rich blacks, smudged soft tones and a few white chalk highlights, the {kind} alone on plain pure white paper",
        ),
        "mezzotint": (
            "Mezzotint",
            "a mezzotint engraving: velvety dark tones with the {kind} emerging from deep black into light, very fine grain, no colour, the {kind} alone on plain pure white paper",
        ),
        # ---- colour, clean ----
        "mucha": (
            "Art Nouveau (Mucha)",
            "an Art Nouveau illustration in the manner of Alphonse Mucha: the {kind} drawn with flowing, sinuous contours, pastel flat colour with a crisp dark outline, on plain pure white paper",
        ),
        "gouache": (
            "Gouache poster",
            "a mid-century travel-poster illustration in flat opaque gouache: the {kind} in five or six clean tones with no gradients and crisp edges, on plain pure white paper",
        ),
        "keulemans": (
            "Scientific plate (Keulemans)",
            "a precise scientific bird-plate in the manner of J. G. Keulemans: tight accurate drawing with every feather tract defined, hand-coloured, the {kind} alone on plain pure white paper",
        ),
        "stamp": (
            "Vintage stamp",
            "a postage-stamp engraving of the {kind}: fine engraved line work in one dark ink with a single flat spot colour, the {kind} alone on plain pure white paper",
        ),
        "stainedglass": (
            "Stained glass",
            "a leaded stained-glass picture of the {kind}: built entirely of many small cut pieces of translucent coloured glass in the species' colours, two dozen or more pieces in the {kind} alone, joined by thick dark lead came of one even width, each feather group, the cap, the cheek, the eye, the bill, each leg, each leaf and each length of branch its own flat piece, every piece a single colour of mottled, streaky, rippled antique glass with light glowing through it and nothing painted or drawn inside it, the lead lines tracing every piece's edge like a jigsaw, the leaf stalks and twigs glass pieces too and no bare lead line anywhere, the glass pieces of the {kind} and its perch only, with no window frame, panel edge or background glass round them, the {kind} alone on plain pure white paper",
        ),
        "risograph": (
            "Risograph print",
            "a risograph print of the {kind} in two overprinted spot colours with grainy texture and slight misregistration, bold and graphic, on plain pure white paper",
        ),
        # ---- birds aren't real ----
        "surveillance": (
            "Surveillance unit (watercolour)",
            "a loose watercolour field-guide painting of a government surveillance robot built to look like the {kind}: the plumage painted true to the species, but plainly a machine where it shows, with a large glass camera lens for an eye, a thin antenna rising from its head, an open panel on its side showing circuitry and a small battery, and a serial number stencilled on its leg ring, on plain pure white paper",
        ),
        "patent": (
            "Patent drawing",
            "a cutaway patent drawing of the {kind} as a machine: fine black line work on white, parts numbered with leader lines, a camera module, a battery and a transmitter labelled, 'Fig. 1' in draughtsman's lettering, no shading, on plain pure white paper",
        ),
        "automaton": (
            "Clockwork automaton",
            "a Victorian clockwork automaton of the {kind}: brass and enamel feathers, visible gears at the wing joint, a wind-up key in its back, painted as a natural-history plate, the {kind} alone on plain pure white paper",
        ),
        "cctv": (
            "Camera head (watercolour)",
            "a watercolour painting of the {kind} in the species' true colours and markings, loose transparent washes with soft wet edges and a few fine pen lines for the feathers, except that its head is, plainly and unmistakably, {camera}, sitting directly on the shoulders where the head should be with no neck, stalk or mount between, turned the way the head would face, the body, wings, tail and feet still those of the {kind}, on plain pure white paper",
        ),
        "drone": (
            "Drone",
            "a flat vector-style illustration of a military drone built to look like the {kind}: the body a matte grey machine with panel seams and a registration number, four small rotors set into the wings, a camera lens for an eye, a red LED and a landing skid for feet, only the colours and markings of the species painted on, clean flat colour, on plain pure white paper",
        ),
        "artnouveau": (
            "Art Nouveau illustration",
            "an Art Nouveau illustration with sinuous flowing lines, a muted palette and decorative leaves curling round the branch, on plain pure white paper",
        ),
        "artsandcrafts": (
            "Arts and Crafts (Morris)",
            "an Arts and Crafts illustration in the manner of William Morris: the {kind} set among dense, repeating stylised foliage and flowers like a wallpaper or tapestry panel, flat muted earth, olive and indigo colours with a firm dark outline, on plain unbleached paper",
        ),
        "jugendstil": (
            "Jugendstil",
            "a Jugendstil illustration, the German Art Nouveau: squarer, more geometric ornament, a bold even outline, flat areas of muted colour, the {kind} framed in a decorative cartouche of stylised leaves, on plain cream paper",
        ),
        "glasgow": (
            "Glasgow style",
            "a Glasgow style illustration in the manner of Charles Rennie Mackintosh and the Macdonald sisters: a tall elongated composition, the {kind} drawn with stylised, elongated lines, pale washed colours with a few strong dark lines and a great deal of empty space round it, on plain pure white paper",
        ),
        "secession": (
            "Vienna Secession",
            "a Vienna Secession illustration in the manner of Klimt and Moser: the {kind}'s plumage true to life but its surroundings made of flat decorative shapes, gold, black and jewel colours, mosaic-like patterned areas, spirals and squares, on plain cream paper",
        ),
        "deco": (
            "Art Deco",
            "an Art Deco illustration: stepped geometry, the {kind} simplified into clean curves and chevrons, a palette of gold, black, teal and cream with flat colour and crisp edges, on plain pure white paper",
        ),
        "beardsley": (
            "Beardsley ink",
            "a black-and-white ink illustration in the manner of Aubrey Beardsley: bold sweeping lines, large flat black masses against white, ornamental dots and fine hatching, no colour at all, on plain pure white paper",
        ),
        "nouveauglass": (
            "Nouveau stained window",
            "a Tiffany-style Art Nouveau stained-glass window of the {kind}: leaded curving lines around pieces of opalescent coloured glass, wisteria and dragonfly ornament, soft light through the glass, on a plain pale background",
        ),
        "fairytale": (
            "Golden Age fairy-tale plate",
            "a Golden Age illustration in the manner of Arthur Rackham and Edmund Dulac: fine ink line with soft muted watercolour wash, gnarled branches and twisted roots as a frame, a faintly magical atmosphere, on plain toned paper",
        ),
        "japonisme": (
            "Japonisme",
            "a Japonisme illustration, Western Art Nouveau borrowing from ukiyo-e: a flat {kind} on a branch, an asymmetric composition with an ornamented border and a small seal-like roundel, soft flat colours and a fine keyline, on plain cream paper",
        ),
        "chinoiserie": (
            "Chinoiserie",
            "a hand-painted chinoiserie wallpaper panel: the {kind} on a flowering branch with delicate blossom and butterflies, soft flat colours on a pale ground, fine brushwork, on plain pale paper",
        ),
        "persian": (
            "Persian miniature",
            "a Persian miniature painting: flat jewel colours, gold leaf, flattened perspective, the {kind} among tulips and stylised flowers under a patterned sky, with a gold and blue illuminated border, on plain cream paper",
        ),
        "bestiary": (
            "Medieval bestiary",
            "a page from a medieval bestiary: the {kind} painted in red, blue and green tempera with gold leaf on a vellum ground, drawn as a manuscript illuminator would, with a vine and acanthus border, no text",
        ),
        "byzantine": (
            "Byzantine icon",
            "a Byzantine icon panel of the {kind}: a burnished gold ground, a formal frontal pose with a halo-like circle behind the head, deep reds and blues in egg tempera, fine gold lines in the feathers",
        ),
        "folkart": (
            "Folk art",
            "a folk-art painting of the {kind} in the manner of Pennsylvania Dutch fraktur and Scandinavian rosemaling: the {kind} decorated with scrolls, hearts and tulips worked into its own plumage, flat bright colours with a dark outline, on plain pure white paper",
        ),
        "delft": (
            "Delft tile",
            "a Dutch Delft tile painting of the {kind}: cobalt blue on white tin-glazed earthenware, the {kind} in a tiled square with corner ornaments, brushy blue washes and a fine crackle in the glaze",
        ),
        "sumie": (
            "Ink and wash",
            "a Japanese sumi-e brush painting in black sumi ink, in the spirit of a Zen master's freehand brushwork: the {kind} suggested, not described, in perhaps a dozen bold, wet strokes of a large soft brush, each stroke made once and left, its bristle marks and tapering end showing, the body one or two broad pools of grey ink bleeding softly into the paper, the dark markings a few strokes of solid black, no feather drawn on its own and no fine detail beyond the eye, the branch in fast dry-brush strokes with ragged edges, a monochrome painting in black and grey ink, the species' colours given only as a pale tint dropped into one wash; the whole {kind} large on the page, about two thirds of its width, its bill, tail tip and the branch's ends all well clear of the edges, on plain pure white paper",
        ),
        "cigarette": (
            "Edwardian cigarette card",
            "an Edwardian cigarette card chromolithograph of the {kind}: a small framed picture with a decorative border, slightly faded printed inks, fine detail, and a blank panel below for the name, on plain cream card",
        ),
        "felt": (
            "Needle felt",
            "a needle-felted wool figure of the {kind}, soft and fuzzy, in the species' true colours and markings, photographed, on plain pure white paper",
        ),
        "clay": (
            "Clay figure",
            "a hand-sculpted plasticine figure of the {kind} for a stop-motion film, chunky with a slight sheen and visible thumbprints, in the species' true colours, on plain pure white paper",
        ),
        "bricks": (
            "Brick-built",
            "a model of the {kind} built from small plastic toy bricks, studs showing, in the species' colours, on plain pure white paper",
        ),
        "screenprint": (
            "Screen print poster",
            "a mid-century screen-printed nature poster: flat shapes of colour, simple bold forms and a slightly textured ink, on plain pure white paper",
        ),
        "embroidery": (
            "Embroidery",
            "satin-stitch embroidery in silk thread on plain natural linen, every feather in stitches of the species' true colours, on plain pure white paper",
        ),
        "mosaic": (
            "Mosaic",
            "a mosaic of small square tesserae of stone and glass in the species' colours, set in pale grout, on plain pure white paper",
        ),
        "papercut": (
            "Paper cut",
            "a layered cut-paper collage of the {kind}, with sheets of coloured paper in the species' colours and soft shadows between the layers, on plain pure white paper",
        ),
        "pointillism": (
            "Pointillism",
            "a pointillist painting made entirely of small dots of pure colour that blend at a distance, on plain pure white paper",
        ),
        "grumpy": (
            "Grumpy (plate style)",
            "a hand-coloured lithograph from a nineteenth-century natural history book, soft watercolour washes over fine engraved linework on pure white paper, of a comically plump {kind}: an almost perfectly round ball of feathers with a big head, a stubby tail and small feet, like a cartoon game character. Give it a very expressive, furious face: heavy dark brows of feathers slanting down over narrowed eyes, a hard glare and a determined scowl. Paint all of it with real, detailed feathers, keeping the species' true colours, markings and bill so it is still plainly that {kind}",
        ),
        "superhero": (
            "Superhero",
            "a realistic natural history painting on plain cream paper of the {kind} striking a heroic pose with a small flowing cape, dramatic light, plumage still true to life, on plain pure white paper",
        ),
        "portrait": (
            "Victorian portrait",
            "a stiff, dignified Victorian oil portrait of the {kind} as a family ancestor, in a small gilt oval frame, plumage painted true to life, on plain cream paper",
        ),
        "pirate": (
            "Pirate",
            "a realistic hand-coloured lithograph in the manner of John Gould on pure white paper, except that the {kind} is a pirate with an eye patch, a small tricorn hat and a cross scowl",
        ),
    }
)
# Three kinds of look, each in subgroups: realistic (true to the bird, as a
# naturalist's plate or study), decorative (the bird as a pattern, a print or a
# craft) and fun (a joke, a cartoon, a costume).
STYLE_TREE: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    (
        "Realistic",
        (
            (
                "Plates and paintings",
                ("gould", "audubon", "keulemans", "watercolour", "engraving", "sketch"),
            ),
            (
                "Greys and tones, for grey panels",
                ("pencil", "litho_toned", "platinum", "charcoal", "mezzotint"),
            ),
        ),
    ),
    (
        "Decorative",
        (
            (
                "Nouveau and Deco",
                (
                    "mucha",
                    "artnouveau",
                    "artsandcrafts",
                    "jugendstil",
                    "glasgow",
                    "secession",
                    "deco",
                    "beardsley",
                    "nouveauglass",
                    "fairytale",
                ),
            ),
            (
                "Prints and posters",
                (
                    "woodblock",
                    "linocut",
                    "harper",
                    "gouache",
                    "risograph",
                    "screenprint",
                    "stamp",
                ),
            ),
            (
                "Around the world",
                (
                    "japonisme",
                    "chinoiserie",
                    "persian",
                    "bestiary",
                    "byzantine",
                    "folkart",
                    "delft",
                    "sumie",
                    "cigarette",
                ),
            ),
            (
                "Glass, thread and paper",
                ("stainedglass", "mosaic", "embroidery", "papercut", "pointillism"),
            ),
            (
                "Black and white, for 1-bit panels",
                ("scraperboard", "woodcut", "stipple", "silhouette", "inkbrush"),
            ),
            ("Graphic greys, for grey panels", ("halftone", "blueprint")),
        ),
    ),
    (
        "Fun",
        (
            ("Anime", ("anime", "shonen", "manga", "anime_retro")),
            ("Made of things", ("pixel", "origami", "felt", "clay", "bricks")),
            ("Characters", ("grumpy", "superhero", "portrait", "pirate", "hats")),
            (
                "Birds aren't real",
                ("surveillance", "cctv", "patent", "automaton", "drone"),
            ),
        ),
    ),
)
# What the admin's select and the manifest offer: one group per subgroup, named
# "Realistic · Plates and paintings" so a flat dropdown still reads as a tree.
KEPT_STYLES = (
    "gould",
    "audubon",
    "watercolour",
    "engraving",
    "sketch",
    "pencil",
    "woodblock",
    "linocut",
    "mucha",
    "folkart",
    "sumie",
    "stainedglass",
    "pointillism",
)
STYLES = {k: STYLES[k] for k in KEPT_STYLES}
STYLE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (("Styles", KEPT_STYLES),)
# The species a style's preview is painted from: a familiar bird that is not
# in flight and needs no unusual pose.
PREVIEW_SPECIES = ("Great Tit", "Parus major", "Aves")
STYLE_IDS = (*STYLES, "custom")
# The camera a species gets for a head, picked from its name so a sheet has a mix.
CAMERAS = (
    "a white bullet-style CCTV security camera with a black lens and a sun hood",
    "a black dome security camera, the lens behind its smoked glass hemisphere",
    "a pan-tilt-zoom security camera",
    "a vintage 1960s cine camera with a turret of three lenses",
    "a boxy 1980s camcorder with a red recording light",
    "a big shoulder-mounted television news camera with a long lens and a microphone on top",
    "a small square action camera in a waterproof case",
    "a Super 8 home-movie camera with its pistol grip",
    "an old film camera with two round film reels on top",
    "a sleek modern handheld camcorder with its flip-out screen open",
    "a broadcast studio camera on a short pedestal",
    "a police body-worn camera, black with a wide lens",
    "a round webcam with a glowing blue ring round its lens",
    "a big DSLR camera with a long telephoto lens and a lens hood",
    "an old brass-and-leather bellows camera",
    "a doorbell camera with a wide fisheye lens",
    "a drone's gimbal camera, three-axis, with a small lens",
)


# How often each camera comes up: the video cameras three times as often as
# the stills cameras, the bellows camera rarest of all.
CAMERA_WEIGHTS = {
    "bellows": 1,
    "DSLR": 2,
    "webcam": 2,
    "doorbell": 2,
    "dome": 2,
    "gimbal": 2,
}


def camera_for(sci: str) -> str:
    pool = [
        c
        for c in CAMERAS
        for _ in range(next((w for k, w in CAMERA_WEIGHTS.items() if k in c), 3))
    ]
    return pool[
        int(hashlib.sha1(("camera|" + sci).encode()).hexdigest()[:4], 16) % len(pool)
    ]


# Styles drawn without colour: the prompt asks for the markings, not the colours.
MONO = {
    "manga",
    "engraving",
    "scraperboard",
    "woodcut",
    "stipple",
    "silhouette",
    "inkbrush",
    "pencil",
    "litho_toned",
    "halftone",
    "blueprint",
    "platinum",
    "charcoal",
    "mezzotint",
    "patent",
    "beardsley",
}
# Styles whose picture is a whole object (a tile, a card, a framed portrait,
# an illuminated page, a window): kept whole on the sheet, never cut out.
WHOLE: frozenset[str] = frozenset(
    (
        "portrait",
        "cigarette",
        "delft",
        "bestiary",
        "persian",
        "byzantine",
        "nouveauglass",
        "secession",
        "jugendstil",
        "artsandcrafts",
        "chinoiserie",
        "japonisme",
        "fairytale",
    )
)
CUSTOM_EXAMPLE = (
    "an Art Nouveau illustration with sinuous flowing lines, a muted palette of sage, "
    "ochre and dusty rose, on plain pure white paper"
)
_CUSTOM_RE = re.compile(r"custom[a-z]{8}")

# The style builder: one pick per element of a style, joined into the same kind
# of free-text "how" a custom prompt is. (value, label, phrase); an empty phrase
# leaves that element to the model.
BUILDER: dict[str, tuple[str, str, tuple[tuple[str, str, str], ...]]] = {
    "medium": (
        "Medium",
        "watercolour",
        (
            ("watercolour", "Watercolour", "a loose watercolour painting"),
            ("gouache", "Gouache", "a flat gouache painting"),
            ("oil", "Oil paint", "an oil painting with visible brushwork"),
            ("pastel", "Soft pastel", "a soft pastel drawing"),
            ("coloured_pencil", "Coloured pencil", "a coloured-pencil drawing"),
            ("pencil", "Graphite pencil", "a graphite pencil drawing"),
            ("ink", "Pen and ink", "a pen-and-ink drawing"),
            ("engraving", "Engraving", "a copperplate engraving"),
            ("lithograph", "Hand-coloured lithograph", "a hand-coloured lithograph"),
            ("woodblock", "Woodblock", "a woodblock print"),
            ("linocut", "Linocut", "a linocut print"),
            ("screenprint", "Screen print", "a screen print with a few flat inks"),
            ("vector", "Flat vector", "a flat vector illustration"),
            ("papercut", "Paper cut", "layered cut paper"),
            (
                "stained_glass",
                "Stained glass",
                "stained glass with thick black lead lines",
            ),
            ("embroidery", "Embroidery", "embroidery in coloured thread"),
        ),
    ),
    "period": (
        "Period or school",
        "",
        (
            ("", "None in particular", ""),
            (
                "victorian",
                "Victorian natural history",
                "in the manner of a Victorian natural history plate",
            ),
            (
                "nouveau",
                "Art Nouveau",
                "in the Art Nouveau manner, with sinuous flowing lines",
            ),
            (
                "deco",
                "Art Deco",
                "in the Art Deco manner, with stepped geometry and clean curves",
            ),
            (
                "midcentury",
                "Mid-century modern",
                "in the mid-century modern manner, with simplified shapes",
            ),
            (
                "japanese",
                "Japanese",
                "in the Japanese manner, with a fine dark keyline",
            ),
            (
                "arts_crafts",
                "Arts and Crafts",
                "in the Arts and Crafts manner, with stylised natural forms",
            ),
            (
                "folk",
                "Folk art",
                "in the manner of folk art, with decorative patterning worked into the bird",
            ),
            ("modern", "Contemporary", "in a contemporary illustration style"),
        ),
    ),
    "palette": (
        "Palette",
        "",
        (
            ("", "True to the species", ""),
            ("muted", "Muted and earthy", "in a muted, earthy palette"),
            ("bright", "Bright and saturated", "in bright, saturated colour"),
            ("pastel", "Soft pastels", "in soft pastel colours"),
            ("warm", "Warm tones", "in a palette of warm reds, ochres and browns"),
            ("cool", "Cool tones", "in a palette of cool blues, greens and greys"),
            (
                "two_colour",
                "Two colours",
                "in just two inks, one dark and one accent colour",
            ),
            ("grey", "Greys", "in tones of grey only, no colour"),
            (
                "bw",
                "Black and white",
                "in black ink only, no colour and no grey mid-tones, drawn so it survives a 1-bit panel",
            ),
        ),
    ),
    "line": (
        "Line",
        "",
        (
            ("", "Medium's own", ""),
            ("fine", "Fine, delicate line", "with fine, delicate linework"),
            ("bold", "Bold outline", "with a bold dark outline"),
            ("none", "No outline", "with no outline, the shapes made by colour alone"),
            ("sketchy", "Loose and sketchy", "with loose, sketchy lines"),
        ),
    ),
    "texture": (
        "Texture",
        "",
        (
            ("", "Medium's own", ""),
            ("smooth", "Smooth and clean", "with clean, smooth, flat colour"),
            ("brush", "Visible brush strokes", "with visible brush strokes"),
            ("grain", "Grainy, printed", "with a grainy printed texture"),
            ("hatching", "Hatching", "shaded with fine hatching and cross-hatching"),
            ("dots", "Dots and stipple", "shaded with dots and stipple"),
        ),
    ),
    "mood": (
        "Mood",
        "",
        (
            ("", "Neutral", ""),
            ("calm", "Calm and delicate", "calm and delicate in feeling"),
            ("graphic", "Bold and graphic", "bold and graphic in feeling"),
            ("whimsical", "Whimsical", "whimsical and playful in feeling"),
            ("dramatic", "Dramatic", "dramatic, with strong contrast"),
        ),
    ),
}


def build_how(options: dict[str, Any]) -> str:
    """The style builder's picks as one style description."""
    parts: list[str] = []
    for name, (_label, default, choices) in BUILDER.items():
        pick = str(options.get(f"build_{name}") or default)
        phrase = next((p for v, _l, p in choices if v == pick), "")
        if name == "medium":
            phrase = (phrase or choices[0][2]) + " of the {kind}"
        if phrase:
            parts.append(phrase)
    extra = " ".join(str(options.get("build_extra") or "").split())[:300]
    if extra:
        parts.append(extra)
    return ", ".join(parts) + ", on plain pure white paper"


GEN_WHOLE = (
    "Paint the {kind} in the reference photo, the {name} ({sci}), as {style}. "
    "Keep its colours, markings, shape and proportions exactly as in the photo: {pose}. "
    "The picture complete as the style describes, filling the frame edge to edge: "
    "no text, no lettering, no signature.{about}{dapper}"
)

# A fresh illustration of the species in the style, the photo for identity only: asked to
# "paint the bird in the photo" the model restyles the photo, pose, crop and lighting and all.
GEN = (
    "Draw the {name} ({sci}) afresh as {style}: a new illustration of the species in that "
    "style, not the photo restyled. The reference photo is for identity only, its colours, "
    "markings, bill, feet and proportions kept exactly; the composition, pose and lighting "
    "are the illustration's own: {pose}. The {kind} alone on plain, pure white paper with a "
    "clear margin all round and nothing else on the page: no shadow, wash, vignette, ground, "
    "sky, scenery, other creatures, text, border or signature. The style says how the {kind} "
    "and its perch are drawn and nothing else: no background, backdrop, frame, roundel, "
    "halo, panel, pattern, sunburst or ornament round or behind them."
    "{outline}{about}{dapper}"
)
# A white bird on white paper is lost to the cut-out: it gets a contour line.
OUTLINE = (
    " A white or very pale {kind} is drawn with a fine, clear dark contour line along its whole "
    "outline, so that none of it is lost against the white paper."
)
# The hero plate: one specimen large on an ornate backdrop in its style, the whole picture kept.
GEN_HERO = (
    "Paint the {kind} in the reference photo, the {name} ({sci}), as {style}, as the single "
    "subject of an ornate framed plate: the {kind} large in the middle, {pose}, on a decorative "
    "backdrop in the same style, a patterned or foliate ground within a drawn ornamental border "
    "that fills the whole picture to its edges, the page's own art in that style and nothing "
    "modern. Keep its colours, markings, shape and proportions exactly as in the photo. "
    "No text, no lettering, no numbers, no signature.{about}{dapper}"
)
DAPPER = (
    " The {kind} is dapper: it wears a small, well-made hat that suits it, and perhaps a "
    "monocle, painted with the same care as the rest."
)
# A bird is painted perched or in flight; the sheet mixes them so a page is
# not eight profiles on branches. A third of species fly.
POSES = {
    "native": (
        "perched on a natural, gently curving branch of {plant}, a plant of the country "
        "where it was seen, with a few of its leaves (and its berry, bud or blossom where "
        "it would have one), painted with the same care as the bird, the whole bird and "
        "its perch, the branch ending cleanly well inside the margin"
    ),
    "perched": (
        "perched on a natural, gently curving branch with a few leaves (and a berry, "
        "bud or blossom where the species would have one), painted with the same care "
        "as the bird, the whole bird and its perch, the branch ending cleanly well "
        "inside the margin"
    ),
    "flight": (
        "in flight with its wings spread, seen from the side a little below, the whole "
        "bird, held in the air with no branch, perch, twig or ground under it"
    ),
}
# A style that puts its bird on a branch cannot do that for a bird in flight.
FLIGHT_HOW = (
    (" among a few leaves", ""),
    ("a single flowering branch and ", ""),
    (
        "decorative leaves curling round the branch",
        "decorative leaves curling in its wake",
    ),
    (
        "gnarled branches and twisted roots as a frame",
        "a frame of curling leaves and tendrils",
    ),
    ("a flat {kind} on a branch", "a flat {kind} in flight"),
    (
        "the {kind} on a flowering branch with delicate blossom and butterflies",
        "the {kind} in flight among drifting blossom petals and butterflies",
    ),
)

# The scene: the sheet's cut-outs on one collage, repainted by the model as a
# single picture with one background. Opt-in, one painting per re-compose.
SCENES = {
    "auto": "the specimens gathered in an oval cluster, like a Victorian vignette, on branches and foliage that join them",
    "floor": "growing together on a forest floor among leaf litter, moss and a few fallen twigs",
    "log": "growing on and around one fallen, mossy log",
    "bank": "on a shaded woodland bank among ferns, moss and roots",
    "bed": "growing together in one garden bed, with the soil and a few stones",
    "branch": "all perched along one shared branch",
    "tree": "perched through the branches of one tree",
    "birdbath": "gathered round a stone bird bath in a garden",
    "wire": "sitting in a row along a telephone wire against the sky",
    "feeder": "gathered at and around a hanging bird feeder",
    "meadow": "on the ground and low stems of a wildflower meadow",
    "sky": "all in flight together across an open sky",
    "wetland": "among reeds and still water at the edge of a wetland",
    "garden": "in a cottage garden, on a fence, shrubs and a path",
}
FIDELITY = {
    "exact": "Keep every specimen exactly where it is, at the same size and pose, with its own colours and markings unchanged.",
    "free": "Move and resize the specimens as the scene needs, but keep each one's own colours, markings and proportions.",
}
GEN_SCENE = (
    "This picture is a collage of {n} specimens, each painted on its own: {names}. Repaint it "
    "as one seamless picture, {style}, with all of them together in a single natural "
    "scene: {scene}, one continuous background, the same light and paper throughout. "
    "{fidelity}{plants} No text, no labels, no numbers, no border, no signature."
)
GEN_PLANTS = " The plants and trees in the scene are those native to where these were seen: {plants}."
SCENE_EVERY = ("3", "1", "6", "24")
# What "auto" means for a page that is mostly one kind of thing.
SCENE_AUTO_BY_KIND = {
    "Fungi": "floor",
    "Plantae": "bed",
    "Protozoa": "log",
    "Insecta": "meadow",
    "Arachnida": "meadow",
    "Amphibia": "wetland",
    "Reptilia": "bank",
    "Mammalia": "bank",
}


# Scenes where the specimens stand on something: the model is left free to
# set them down, since the collage's ring would leave half of them in the air.
GROUNDED = {"floor", "log", "bank", "bed", "meadow", "wetland", "garden", "birdbath"}


def scene_for(scene: str, kinds: list[str]) -> str:
    """The scene to paint: the one asked for, or for "auto" the one that
    suits the page's commonest kind (birds keep the vignette)."""
    if scene != "auto" or not kinds:
        return scene
    top = max(set(kinds), key=kinds.count)
    return SCENE_AUTO_BY_KIND.get(top, "auto")


_LOCK = threading.Lock()
# Custom prompts live in the cell, not the folder: the fetch sets the tag
# for the call so the painting threads it starts know the words.
_CUSTOM: dict[str, str] = {}


# ----------------------------------------------------------------------
# Keys, styles, prompts
# ----------------------------------------------------------------------


def custom_tag(how: str) -> str:
    """A style id for a custom prompt: 'custom' plus eight letters of its hash."""
    h = hashlib.sha1(how.encode()).hexdigest()
    return "custom" + "".join(chr(ord("a") + int(c, 16) % 26) for c in h[:8])


def register_custom(how: str) -> str:
    tag = custom_tag(how)
    with _LOCK:
        _CUSTOM[tag] = how
    return tag


def style_of(style: str | None) -> tuple[str, str]:
    """(label, how) for a style id, a custom tag, or the default."""
    style = str(style or "gould")
    if style in STYLES:
        return STYLES[style]
    if _CUSTOM_RE.fullmatch(style):
        how = _CUSTOM.get(style, "")
        label = (
            "Custom: " + (how[:44].rstrip() + "…" if len(how) > 45 else how)
            if how
            else "Custom style"
        )
        return label, how
    # an unknown style id is a bug, not a request for Gould: paint() fails on the empty prompt
    return f"Unknown style {style}", ""


def flies(sci: str) -> bool:
    return int(hashlib.sha1(sci.encode()).hexdigest()[:4], 16) % 3 == 0


PLANT_REGIONS: tuple[tuple[str, float, float, float, float, tuple[str, ...]], ...] = (
    (
        "Australia",
        -45,
        -10,
        110,
        155,
        (
            "eucalyptus",
            "banksia",
            "golden wattle",
            "bottlebrush",
            "grevillea",
            "tea-tree",
            "she-oak",
            "lilly pilly",
        ),
    ),
    (
        "New Zealand",
        -48,
        -34,
        165,
        179,
        ("pōhutukawa", "kōwhai", "mānuka", "harakeke flax", "tōtara", "kahikatea"),
    ),
    (
        "British Isles",
        49,
        61,
        -11,
        2,
        (
            "oak",
            "hawthorn",
            "blackthorn",
            "hazel",
            "rowan",
            "elder",
            "silver birch",
            "holly",
        ),
    ),
    (
        "Mediterranean",
        35,
        45,
        -10,
        36,
        ("olive", "cork oak", "fig", "almond", "Aleppo pine", "carob", "wild rose"),
    ),
    (
        "Northern Europe",
        45,
        71,
        -5,
        32,
        (
            "oak",
            "beech",
            "Scots pine",
            "silver birch",
            "rowan",
            "hazel",
            "lime",
            "wild cherry",
        ),
    ),
    (
        "Japan and Korea",
        30,
        46,
        125,
        146,
        ("cherry", "Japanese maple", "bamboo", "plum", "pine", "camellia", "zelkova"),
    ),
    (
        "East Asia",
        18,
        54,
        97,
        125,
        ("ginkgo", "bamboo", "plum", "magnolia", "camphor", "pine"),
    ),
    (
        "South Asia",
        5,
        36,
        66,
        97,
        ("neem", "banyan", "mango", "peepal", "tamarind", "flame of the forest"),
    ),
    (
        "Southeast Asia",
        -11,
        24,
        95,
        141,
        ("frangipani", "rambutan", "banana", "bamboo", "mangrove", "flame tree"),
    ),
    (
        "Eastern North America",
        24,
        50,
        -100,
        -52,
        (
            "red oak",
            "sugar maple",
            "flowering dogwood",
            "American beech",
            "sumac",
            "sassafras",
            "eastern redbud",
            "serviceberry",
        ),
    ),
    (
        "Western North America",
        24,
        60,
        -170,
        -100,
        (
            "Douglas fir",
            "Pacific madrone",
            "manzanita",
            "Garry oak",
            "red alder",
            "big-leaf maple",
            "ponderosa pine",
            "toyon",
        ),
    ),
    (
        "Mexico and Central America",
        7,
        24,
        -118,
        -77,
        ("ceiba", "mesquite", "guava", "ahuehuete cypress", "frangipani"),
    ),
    (
        "South America",
        -56,
        12,
        -82,
        -34,
        (
            "araucaria",
            "ceibo",
            "jacaranda",
            "brazilwood",
            "yerba mate",
            "quebracho",
            "ombú",
        ),
    ),
    (
        "Southern Africa",
        -35,
        -15,
        10,
        41,
        (
            "protea",
            "sweet thorn acacia",
            "aloe",
            "yellowwood",
            "wild olive",
            "coral tree",
        ),
    ),
    (
        "Tropical Africa",
        -15,
        20,
        -18,
        52,
        ("umbrella thorn acacia", "baobab", "marula", "fig", "mopane"),
    ),
    (
        "North Africa and the Middle East",
        20,
        42,
        -18,
        66,
        ("date palm", "olive", "cedar of Lebanon", "pomegranate", "tamarisk"),
    ),
    (
        "Russia and Central Asia",
        40,
        75,
        32,
        180,
        ("silver birch", "Siberian larch", "Scots pine", "aspen", "bird cherry"),
    ),
)


def plants_for(lat: float | None, lon: float | None) -> list[str]:
    """Plants native to where the panel is, for a perch and a scene; the first
    region whose box holds the place, [] when none does."""
    if lat is None or lon is None:
        return []
    for _name, lat0, lat1, lon0, lon1, plants in PLANT_REGIONS:
        if lat0 <= lat <= lat1 and lon0 <= lon <= lon1:
            return list(plants)
    return []


def perch_for(sci: str, plants: list[str]) -> str:
    """The plant a species perches on: one of the place's, the same one for the
    species every time, '' with no plants."""
    if not plants:
        return ""
    return plants[
        int(hashlib.sha1(("perch|" + sci).encode()).hexdigest()[:6], 16) % len(plants)
    ]


def in_flight(sci: str, kind: str, pose: str = "mixed") -> bool:
    """Whether a specimen is painted in flight: a bird, when the pose asks for
    it, or for the mixed pose a third of species by name."""
    if kind != "Aves":
        return False
    return pose == "flight" or (pose != "perched" and flies(sci))


def key_for(
    sci: str,
    style: str,
    kind: str = "Aves",
    dapper: bool = False,
    model: str = DEFAULT_MODEL,
    pose: str = "mixed",
    perch: str = "",
    hero: bool = False,
    fine: str = "",
) -> str:
    """The painting's file key: species slug, style, flight for a flying
    bird, the plant a perched bird sits on when the place has native ones,
    a tag for a finer kind with a pose of its own (a beetle),
    dapper for the day's dapper specimen, the model when it is not the
    default (so switching models never repaints what another made)."""
    key = f"{N.slug(sci)}--{style}"
    if style in STYLE_REVISIONS:
        key += f"--r{STYLE_REVISIONS[style]}"
    if PROMPT_VERSION > 1:
        key += f"--p{PROMPT_VERSION}"
    if in_flight(sci, kind, pose):
        key += "--flight"
    elif perch and kind == "Aves":
        key += f"--on_{N.slug(perch)}"
    if K.fine_tag(fine):
        key += f"--{K.fine_tag(fine)}"
    if dapper:
        key += "--dapper"
    if hero:
        key += "--hero"
    if model_of(model) != DEFAULT_MODEL:
        key += f"--m_{model_of(model)}"
    return key


def dapper_pick(scis: list[str], day: str) -> str:
    """Which species is dapper to-day: the same one all day, another to-morrow."""
    if not scis:
        return ""
    ordered = sorted(scis)
    return ordered[
        int(hashlib.sha1(f"{day}|{'|'.join(ordered)}".encode()).hexdigest()[:8], 16)
        % len(ordered)
    ]


def scene_key(style: str, scene: str, fidelity: str, model: str = DEFAULT_MODEL) -> str:
    key = f"scene--{style}--{scene}--{fidelity}"
    if model_of(model) != DEFAULT_MODEL:
        key += f"--m_{model_of(model)}"
    return key


def collage(
    keys: list[str],
    data_dir: Path,
    ratio: float,
    size: int = 1400,
    layout: str = "ring",
) -> dict[str, Any] | None:
    """The paintings under ``keys``, their kept cut-outs pasted on one cream
    sheet in an oval, largest first, as the reference the scene is painted
    from: {path, tag, boxes}. Cached on the paintings it is made of; None
    until every painting has its cut-out."""
    from PIL import Image

    folder = gen_dir(data_dir)
    M = _sibling("masks")
    stamps = []
    for k in keys:
        # the model's cut-out, or the painting cut by its paper when the model's failed
        cut = M.CUTS.path(k, data_dir)
        painting = ART.path(k, data_dir)
        try:
            if cut is not None:
                stamps.append(f"{k}:{int(cut.stat().st_mtime)}")
            elif painting is not None:
                stamps.append(f"{k}:paper:{int(painting.stat().st_mtime)}")
            else:
                return None
        except OSError:
            return None
    if not keys:
        return None
    tag = hashlib.sha1(
        f"{'|'.join(stamps)}|{ratio:.3f}|{size}|{layout}".encode()
    ).hexdigest()[:16]
    path, info_path = folder / f"collage_{tag}.jpg", folder / f"collage_{tag}.json"
    info = N.read_json(info_path, None)
    if path.exists() and isinstance(info, dict):
        return {"path": str(path), "tag": tag, "boxes": info["boxes"]}
    W, H = (size, int(size / ratio)) if ratio >= 1 else (int(size * ratio), size)
    cuts = []
    for k in keys:
        with contextlib.suppress(OSError):
            cut = M.CUTS.path(k, data_dir)
            painting = ART.path(k, data_dir)
            if cut is not None:
                cuts.append((k, Image.open(cut).convert("RGBA")))
            elif painting is not None:
                cuts.append((k, M.paper_cut(painting.read_bytes())))
    if len(cuts) != len(keys):
        return None
    cuts.sort(key=lambda c: -(c[1].width * c[1].height))
    sheet = Image.new("RGB", (W, H), (243, 234, 214))
    n = len(cuts)
    if layout == "ground":
        # standing on one baseline, the largest in the middle, the rest to
        # either side, so a scene of things that grow starts off grounded
        xs = [0.5]
        for k in range(1, n):
            step = (k + 1) // 2
            xs.append(
                0.5 + (0.44 / max(1, (n - 1) // 2 + 0.5)) * step * (1 if k % 2 else -1)
            )
        slots = [(x, 0.72) for x in xs]
    else:
        slots = [(0.5, 0.5)]
    slots = (
        slots
        if layout == "ground"
        else slots
        + [
            (
                0.5 + 0.36 * math.cos(2 * math.pi * i / max(1, n - 1) - math.pi / 2),
                0.5 + 0.34 * math.sin(2 * math.pi * i / max(1, n - 1) - math.pi / 2),
            )
            for i in range(n - 1)
        ]
    )
    big = 0.42 if n > 1 else 0.8
    small = max(0.16, min(0.3, 1.1 / max(1, n)))
    boxes = []
    for i, ((k, im), (sx, sy)) in enumerate(zip(cuts, slots, strict=False)):
        if layout == "ground":
            big, small = 0.5, max(0.2, min(0.34, 1.4 / max(1, n)))
        box = (big if i == 0 else small) * min(W, H)
        scale = min(box / im.width, box / im.height)
        fit = im.resize(
            (max(1, int(im.width * scale)), max(1, int(im.height * scale))),
            Image.Resampling.LANCZOS,
        )
        x = int(sx * W - fit.width / 2)
        # on the ground the bottoms line up; on the ring the centres do
        y = (
            int(sy * H - fit.height)
            if layout == "ground"
            else int(sy * H - fit.height / 2)
        )
        x, y = max(0, min(W - fit.width, x)), max(0, min(H - fit.height, y))
        sheet.paste(fit, (x, y), fit)
        boxes.append(
            {
                "key": k,
                "box": [
                    round(x / W, 4),
                    round(y / H, 4),
                    round(fit.width / W, 4),
                    round(fit.height / H, 4),
                ],
            }
        )
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=88)
    N.write(path, buf.getvalue())
    N.write(info_path, json.dumps({"boxes": boxes}).encode())
    with contextlib.suppress(OSError):
        for old in folder.glob("collage_*"):
            if (
                not old.stem.startswith(f"collage_{tag}")
                and time.time() - old.stat().st_mtime > 86400
            ):
                old.unlink()
    return {"path": str(path), "tag": tag, "boxes": boxes}


def about(sci: str, data_dir: Path) -> str:
    """A sentence or two on what the species looks like, from its English
    Wikipedia summary, kept a month; '' when there is none."""
    if not sci:
        return ""
    folder = data_dir / "img"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"about_{N.slug(sci)}.json"
    with contextlib.suppress(OSError):
        if time.time() - path.stat().st_mtime < 30 * 86400:
            got = N.read_json(path, {})
            return str(got.get("about", "")) if isinstance(got, dict) else ""
    answer, err = N.get_json(
        WIKI_SUMMARY
        + urllib.parse.quote(sci.strip().replace(" ", "_"))
        + "?redirect=true"
    )
    text = ""
    if not err and isinstance(answer, dict):
        text = " ".join(str(answer.get("extract") or "").split())
        if len(text) > ABOUT_MAX:
            cut = text[:ABOUT_MAX]
            text = cut[: cut.rfind(". ") + 1] if ". " in cut else cut
    if not err:
        N.write(path, json.dumps({"about": text}).encode())
    return text


PLATE_NOTE = (
    " The reference is an old illustrated plate, a guide to the bird's shape and"
    " markings only. Make a new painting of this bird in the style above, with its"
    " own pose, perch and brushwork: do not reproduce the plate, its composition,"
    " its foliage or its pale paper edge."
)
BOTH_NOTE = (
    " A second reference picture is an old illustrated plate of the same species:"
    " use it only to check the bird's shape and markings against the photo, and do"
    " not copy its brushwork or paper."
)


def references(
    job: dict[str, Any], data_dir: Path, multi: bool = True
) -> tuple[list[str], str, str]:
    """The pictures to paint from, how they were used ('photo', 'plate' or
    'both') and their credit. ``ref_mode`` asks for the photo, a classic
    plate or both. A bird painted in flight keeps the photo, since the plates
    are all perched; a species with no plate keeps the photo; a model that
    takes one picture gets the plate or the photo, not both."""
    photo, photo_credit = str(job.get("ref") or ""), str(job.get("ref_credit") or "")
    mode = str(job.get("ref_mode") or "photo")
    flying = "--flight" in str(job.get("key") or "")
    plate = None
    if (
        mode != "photo"
        and not flying
        and job.get("sci")
        and job.get("kind", "Aves") == "Aves"
    ):
        plate = C.reference(job["sci"], data_dir)
    if plate is None:
        return ([photo], "photo", photo_credit) if photo else ([], "photo", "")
    if mode == "plate" or not multi:
        if mode == "both" and photo:
            return [photo], "photo", photo_credit
        return [plate[0]], "plate", plate[1]
    if not photo:
        photo, photo_credit = S.inat_reference(job.get("sci") or "", data_dir)
    if not photo:
        return [plate[0]], "plate", plate[1]
    return [photo, plate[0]], "both", f"{photo_credit} and {plate[1]}"


def prompt_for(job: dict[str, Any], data_dir: Path) -> str:
    noun, pose = K.noun_of(job.get("kind"), job.get("fine"))
    flying = job.get("kind") == "Aves" and "--flight" in job["key"]
    if job.get("kind") == "Aves":
        pose = POSES["flight" if flying else "perched"]
        if not flying and job.get("perch"):
            pose = POSES["native"].format(plant=job["perch"])
    _label, how = style_of(job.get("style"))
    how = str(job.get("how") or how)
    if flying:
        for perched, aloft in FLIGHT_HOW:
            how = how.replace(perched, aloft)
    how = how.replace("{kind}", noun).replace(
        "{camera}", camera_for(job.get("sci") or "")
    )
    words = about(job.get("sci") or "", data_dir)
    gen = (
        GEN_HERO
        if job.get("hero")
        else GEN_WHOLE
        if str(job.get("style") or "") in WHOLE
        else GEN
    )
    if str(job.get("style") or "") in MONO:
        gen = gen.replace(
            "its colours, markings, bill, feet and proportions kept exactly",
            "its markings, bill, feet and proportions kept exactly, rendered without any colour: black and white (and grey where the style has it) only",
        )
    text = gen.format(
        kind=noun,
        name=job.get("name") or job.get("sci") or "",
        sci=job.get("sci") or "",
        style=how,
        pose=pose,
        about=f" About the subject: {words}" if words else "",
        dapper=DAPPER.format(kind=noun) if job["key"].endswith("--dapper") else "",
        # a cut-out subject needs its edge even when it is white; a whole plate does not
        outline=OUTLINE.format(kind=noun)
        if gen is not GEN_HERO and gen is not GEN_WHOLE
        else "",
    )
    used = job.get("ref_used")
    if used == "plate":
        text = text.replace("reference photo", "reference plate").replace(
            "as in the photo", "as in the plate"
        )
        text += PLATE_NOTE
    elif used == "both":
        text += BOTH_NOTE
    return text


# ----------------------------------------------------------------------
# The budget and the kept paintings
# ----------------------------------------------------------------------


ART: Any = (
    artwork.Collection(
        "specimen_sheet",
        "paintings",
        make=lambda job: make(job),
        label="Specimen Sheet",
        title="Painting specimens",
        facet="style",
        facet_label=lambda s: style_of(s)[0],
    )
    if artwork is not None
    else _NoArtwork("paintings")
)
UNLIMITED = artwork.UNLIMITED if artwork is not None else 10**9
OLD_DERIVED_RE = r".*(_\d+(_\w+)?\.jpg|(_\w+)?\.paper)"


def _old_job(job: dict[str, Any]) -> tuple[dict[str, Any], str]:
    payload = {k: v for k, v in job.items() if k not in ("key", "subject", "origin")}
    return payload, str(job.get("name") or job.get("key") or "")


def adopt_old_install(data_dir: Path) -> bool:
    """Before 0.9.0 the paintings lived in gen/ with the day's limit as a count
    of paintings; they become the collection's, with what the previews held and
    the dashboards asked for."""
    return ART.adopt(
        Path(data_dir),
        "gen",
        drop=OLD_DERIVED_RE,
        budget_counts=True,
        job_to_payload=_old_job,
    )


def gen_dir(data_dir: Path) -> Path:
    """The paintings' folder, the collection's, for the tools and the tests."""
    return ART.folder(Path(data_dir))


def _data_dir(path: Path) -> Path:
    path = Path(path)
    return path.parent if path.name == ART.name else path


def budget_used(path: Path) -> int:
    return ART.budget_used(_data_dir(path))


def whole(value: Any, default: int, low: int = 0, high: int = 100000) -> int:
    """A setting as a whole number in range; ``default`` when blank or not a number."""
    with contextlib.suppress(TypeError, ValueError):
        if str(value).strip() != "":
            return min(high, max(low, int(float(value))))
    return default


def daily_cap(settings: dict[str, Any] | None) -> int:
    """The day's painting budget in US cents (an older count of paintings
    still counts at the typical price); UNLIMITED for 0."""
    return ART.cap(settings)


def dollars(cents: int) -> str:
    return "no limit" if cents >= UNLIMITED else f"US${cents / 100:.2f}"


def configure(settings: dict[str, Any] | None) -> None:
    ART.configure(settings)


def per_fetch() -> int:
    return ART.per_preview()


def archive(path: Path, key: str) -> None:
    ART.archive(key, _data_dir(path))


def drop_derived(path: Path, key: str) -> None:
    ART.drop_derived(key, _data_dir(path))


def state(key: str, path: Path) -> str:
    return ART.state(key, _data_dir(path))


def why_failed(key: str, path: Path) -> str:
    return ART.why_failed(key, _data_dir(path))


# ----------------------------------------------------------------------
# Painting
# ----------------------------------------------------------------------


def make(job: Any) -> tuple[bytes, dict[str, Any]]:
    """One painting, on a worker thread: the reference pictures and the
    prompt from the payload, the picture from fal.ai through the collection's
    budget. Raises ArtworkError with why not. The payload is {style, name,
    sci, kind} and may carry ``ref`` (an openly licensed photo of the record
    itself) with ``ref_credit``, ``how`` for a custom style, ``collage`` for
    the scene."""
    j = dict(job.payload)
    j["key"] = job.key
    data_dir = job.data_dir
    label, how = style_of(j.get("style"))
    how = str(j.get("how") or how)
    if not how:
        raise _art_error(
            "the custom prompt behind this painting is not known; paint it again from the widget"
        )
    aspect = str(j.get("aspect") or "3:4") if j.get("hero") else "3:4"
    ref_credit = ""
    model = model_of(j.get("model"))
    if j.get("collage"):
        # the scene: the sheet's own cut-outs, repainted as one picture
        try:
            sheet = Path(j["collage"]).read_bytes()
        except OSError:
            raise _art_error(
                "the collage to paint from has gone; it is made again on the next refresh"
            ) from None
        refs: list[str] = ["data:image/jpeg;base64," + base64.b64encode(sheet).decode()]
        names = j.get("names") or []
        noun = K.noun_of(j.get("kind"))[0] if j.get("kind") else "specimen"
        prompt = GEN_SCENE.format(
            n=len(names),
            names="; ".join(names),
            style=how.replace("{kind}", noun),
            scene=str(j.get("scene_text") or "").strip()
            or SCENES.get(j.get("scene") or "auto", SCENES["auto"]),
            fidelity=FIDELITY.get(j.get("fidelity") or "exact", FIDELITY["exact"]),
            plants=GEN_PLANTS.format(plants=j["plants"]) if j.get("plants") else "",
        )
        aspect = str(j.get("aspect") or "4:3")
    else:
        pictures, used, ref_credit = references(j, data_dir, model != "flux_kontext")
        if not pictures and j.get("sci"):
            photo, ref_credit = S.inat_reference(j["sci"], data_dir)
            pictures = [photo] if photo else []
        if not pictures:
            raise _art_error("no openly licensed reference photo of the species")
        refs = list(pictures)
        prompt = prompt_for({**j, "how": how, "ref_used": used}, data_dir)
    raw = job.fal.generate(model, prompt, refs=refs, aspect=aspect, retries=1)
    mname = MODELS[model]["label"]
    credit = f"AI painting, {label.split(' (')[0].lower()} ({mname} on fal.ai)"
    if j.get("collage"):
        credit = f"AI scene, {label.split(' (')[0].lower()} ({mname} on fal.ai), from each specimen's own painting"
    elif ref_credit:
        credit += f" after {ref_credit}"
    meta: dict[str, Any] = {
        "credit": credit,
        "style": j.get("style") or "gould",
        "name": j.get("name", ""),
        "sci": j.get("sci", ""),
        "kind": j.get("kind", ""),
        "prompt_version": PROMPT_VERSION,
        "model": model,
    }
    if j.get("collage"):
        meta["tag"] = j.get("tag", "")
        meta["boxes"] = j.get("boxes") or []
        meta["scene"] = j.get("scene", "auto")
    if _CUSTOM_RE.fullmatch(str(j.get("style") or "")):
        meta["how"] = how
    return raw, meta

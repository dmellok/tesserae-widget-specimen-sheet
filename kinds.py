"""What a species is: the group it is filed under, the noun a painter calls
it, how a plate would pose it, and how large it is drawn beside the others
on a specimen sheet. Keyed on iNaturalist's iconic taxa; GBIF's class,
kingdom, order and family names are folded into the same keys. Loaded by
server.py by path."""

from __future__ import annotations

from typing import Any

# iconic taxon -> (group label, noun, pose for the painter, size factor)
KINDS: dict[str, tuple[str, str, str, float]] = {
    "Aves": (
        "Birds",
        "bird",
        "perched on a simple branch, the whole bird and its perch",
        1.0,
    ),
    "Mammalia": (
        "Mammals",
        "mammal",
        "standing in profile, the whole animal from nose to tail, nothing under its feet",
        1.5,
    ),
    "Reptilia": (
        "Reptiles",
        "reptile",
        "the whole animal seen from the side, nothing under it",
        1.0,
    ),
    "Amphibia": (
        "Amphibians",
        "amphibian",
        "the whole animal seen from the side a little above, nothing under it",
        0.75,
    ),
    "Actinopterygii": (
        "Fishes",
        "fish",
        "seen from the side as in a fish plate, the whole fish, no water",
        1.0,
    ),
    "Insecta": (
        "Insects",
        "insect",
        "the whole insect alone, seen from above with its wings open and every leg shown, as a pinned specimen in an entomological plate, on nothing",
        0.6,
    ),
    "Arachnida": (
        "Spiders",
        "spider",
        "the whole animal alone, seen from above with every leg shown, on nothing",
        0.55,
    ),
    "Mollusca": (
        "Molluscs",
        "mollusc",
        "the whole animal with its shell, seen from the side, on nothing",
        0.6,
    ),
    "Plantae": (
        "Plants",
        "plant",
        "a single cut stem with its leaves and flower or fruit, as a pressed specimen in a botanical plate, no soil, no pot, no ground",
        1.25,
    ),
    "Fungi": (
        "Fungi",
        "fungus",
        "the whole fruiting body alone in exactly the form the photo shows, whether a cap on a stem, a stemless bracket or shelf, a cup, a club, a coral, a puffball or a crust, cut cleanly from whatever it grew on, as a specimen in a mycological plate, with no ground, soil, leaf litter, wood, bark or log around it",
        0.85,
    ),
    "Chromista": (
        "Seaweeds",
        "seaweed",
        "a single frond laid flat as a pressed specimen in a seaweed plate, no rock, no sand, no water",
        0.9,
    ),
    "Protozoa": (
        "Slime moulds",
        "slime mould",
        "the organism alone, as in a naturalist's plate, with no log, bark or leaf under it",
        0.55,
    ),
    "Animalia": (
        "Other animals",
        "animal",
        "the whole animal alone, seen from the side as in a naturalist's plate, on nothing",
        0.8,
    ),
}

# Finer than the iconic taxon where a plate would say so: (iNaturalist
# ancestor taxon id, label, size factor). First match wins, so the list runs
# from the most specific kinds to the broadest.
FINER: tuple[tuple[int, str, float], ...] = (
    (47224, "Butterfly", 0.7),
    (47157, "Moth", 0.65),
    (47792, "Dragonfly", 0.7),
    (47208, "Beetle", 0.55),
    (47336, "Ant", 0.45),
    (47221, "Bee", 0.5),
    (47201, "Wasp", 0.5),
    (47822, "Fly", 0.5),
    (47744, "Bug", 0.5),
    (47651, "Grasshopper", 0.6),
    (48112, "Mantis", 0.65),
    (47118, "Spider", 0.55),
    (848319, "Marsupial", 1.5),
    (40268, "Bat", 0.7),
    (43698, "Rodent", 0.7),
    (41573, "Carnivore", 1.4),
    (152871, "Whale", 2.0),
    (26172, "Lizard", 1.0),
    (39532, "Turtle", 1.0),
    (20979, "Frog", 0.7),
    (47273, "Shark", 1.6),
    (47114, "Snail", 0.55),
    (47186, "Crab", 0.7),
    (47534, "Jellyfish", 0.9),
    (47549, "Sea star", 0.8),
    (47217, "Orchid", 1.0),
    (47604, "Daisy", 1.0),
    (47148, "Rose family", 1.1),
    (136329, "Conifer", 1.3),
    (121943, "Fern", 1.1),
    (311249, "Moss", 0.55),
    (50814, "Mushroom", 0.85),
    (54743, "Lichen", 0.6),
)


def kind_of(taxon: dict[str, Any] | None) -> tuple[str, str, float]:
    """(group label, kind label, size factor) for an iNaturalist-shaped taxon
    record: ``iconic_taxon_name`` and ``ancestor_ids``."""
    t = taxon or {}
    group, noun, _pose, factor = KINDS.get(
        str(t.get("iconic_taxon_name") or ""), KINDS["Animalia"]
    )
    anc = t.get("ancestor_ids") or []
    for tid, label, f in FINER:
        if tid in anc:
            return group, label, f
    return group, noun.capitalize(), factor


# A finer kind the iconic taxon's pose would paint wrongly: (pose, the tag its
# paintings' keys carry, so a pose reworded here is painted again). An insect
# "with its wings open" came out as a beetle with its wing covers raised and a
# second closed pair still over its back, and asked for in flight the models
# still gave about one beetle in three a second shell, so a beetle is painted
# as a set specimen with its wing covers closed.
FINE_POSES: dict[str, tuple[str, str]] = {
    "Beetle": (
        "the whole beetle alone, seen from directly above as a set specimen in an "
        "entomological plate: its wing covers (elytra) closed and meeting in one straight "
        "line down the middle of its back, no wings showing, its six legs and both "
        "antennae spread evenly to either side, on nothing",
        "b3",
    ),
}


def noun_of(iconic: str | None, fine: str | None = None) -> tuple[str, str]:
    """(noun, pose) a painter uses for an iconic taxon, or for the finer kind
    when it has a pose of its own."""
    _g, noun, pose, _f = KINDS.get(str(iconic or ""), KINDS["Animalia"])
    own = FINE_POSES.get(str(fine or ""))
    return noun, own[0] if own else pose


def fine_tag(fine: str | None) -> str:
    """The key tag of a finer kind with a pose of its own, else ''."""
    own = FINE_POSES.get(str(fine or ""))
    return own[1] if own else ""


# ----------------------------------------------------------------------
# GBIF's names, folded into the same keys
# ----------------------------------------------------------------------

_ICONIC_BY_CLASS = {
    "Aves": "Aves",
    "Mammalia": "Mammalia",
    "Amphibia": "Amphibia",
    "Insecta": "Insecta",
    "Arachnida": "Arachnida",
    "Squamata": "Reptilia",
    "Testudines": "Reptilia",
    "Crocodylia": "Reptilia",
    "Reptilia": "Reptilia",
}
_ICONIC_BY_KINGDOM = {
    "Plantae": "Plantae",
    "Fungi": "Fungi",
    "Chromista": "Chromista",
    "Protozoa": "Protozoa",
}
_FINER_BY_NAME = {
    "Lepidoptera": 47157,
    "Odonata": 47792,
    "Coleoptera": 47208,
    "Hymenoptera": 47201,
    "Formicidae": 47336,
    "Apidae": 47221,
    "Diptera": 47822,
    "Hemiptera": 47744,
    "Orthoptera": 47651,
    "Mantodea": 48112,
    "Araneae": 47118,
    "Chiroptera": 40268,
    "Rodentia": 43698,
    "Carnivora": 41573,
    "Cetacea": 152871,
    "Squamata": 26172,
    "Testudines": 39532,
    "Anura": 20979,
    "Elasmobranchii": 47273,
    "Gastropoda": 47114,
    "Decapoda": 47186,
    "Cnidaria": 47534,
    "Echinodermata": 47549,
    "Orchidaceae": 47217,
    "Asteraceae": 47604,
    "Rosaceae": 47148,
    "Pinopsida": 136329,
    "Polypodiopsida": 121943,
    "Bryophyta": 311249,
    "Agaricomycetes": 50814,
    "Lecanoromycetes": 54743,
    "Diprotodontia": 848319,
    "Dasyuromorphia": 848319,
    "Peramelemorphia": 848319,
    # the butterfly families, since the backbone has no superfamily to name them by
    "Nymphalidae": 47224,
    "Pieridae": 47224,
    "Lycaenidae": 47224,
    "Papilionidae": 47224,
    "Hesperiidae": 47224,
    "Riodinidae": 47224,
}
_NOT_FISH = set(_ICONIC_BY_CLASS)


def taxon_of_gbif(r: dict[str, Any]) -> dict[str, Any]:
    """A GBIF record's names as ``kind_of`` reads them."""
    kingdom, phylum, cls = (
        str(r.get("kingdom") or ""),
        str(r.get("phylum") or ""),
        str(r.get("class") or ""),
    )
    iconic = _ICONIC_BY_CLASS.get(cls) or _ICONIC_BY_KINGDOM.get(kingdom) or ""
    if not iconic:
        if phylum == "Mollusca":
            iconic = "Mollusca"
        elif phylum == "Chordata" and cls not in _NOT_FISH:
            iconic = "Actinopterygii"
        elif kingdom == "Animalia":
            iconic = "Animalia"
    # family before order: a butterfly's family says butterfly, its order only moth
    names = [str(r.get(k) or "") for k in ("family", "order", "class", "phylum")]
    ancestors = [_FINER_BY_NAME[n] for n in names if n in _FINER_BY_NAME]
    return {"iconic_taxon_name": iconic, "ancestor_ids": ancestors}


# Words the exclude list may use for a whole kind, folded to lower case:
# the iconic taxon, its group label, and the finer kind labels.
GROUP_WORDS: dict[str, set[str]] = {}
for _iconic, (_group, _noun, _pose, _f) in KINDS.items():
    for w in (_iconic, _group, _noun, _noun + "s"):
        GROUP_WORDS.setdefault(w.lower(), set()).add(_iconic)

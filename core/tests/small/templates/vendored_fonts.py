"""Read what the vendored icon font can draw: its ligature names.

Material Symbols renders by ligature: an element's text is the glyph's
name, and the font's ``GSUB`` table substitutes the glyph for that
letter sequence.  So "which glyphs does this woff2 carry" is not a
question about glyph *names* -- Google's subsetter strips those to
``glyph00012`` -- but about which letter sequences the ``GSUB`` ligature
lookups map.  This walks those lookups and spells each ligature back
into text through the font's ``cmap``, which is exactly what a browser
matches an element's text against.

Two modules ask: ``test_grr_page_icon_font.py`` for the browse page's
eight glyphs and ``test_resource_page_sorter.py`` for the sorter's
three, both against the one file gain vendors, reached here through the
registry the publisher reads so the bytes checked are the bytes
published.  Decoding WOFF2 needs ``brotli``, a dev-group dependency for
this purpose alone (gain#1400).
"""
from __future__ import annotations

import io

from fontTools.ttLib import TTFont
from gain.templates.static_assets import (
    MATERIAL_SYMBOLS_FONT_PATH,
    repository_static_files,
)

#: The typeface every page sets, and the icon face only the pages that
#: draw a glyph declare -- as ``@font-face`` names them.
TEXT_FONT = "Roboto"
ICON_FONT = "Material Symbols Outlined"

#: ``GSUB`` lookup types: 4 is a ligature substitution, 7 an extension
#: wrapping another lookup.  Both are read because Google's subsetter
#: emits either: the vendored icon font wraps its ligature lookup in an
#: extension, Roboto's are plain type 4.
_LIGATURE = 4
_EXTENSION = 7


def vendored_icon_font() -> bytes:
    """The icon font as the publisher will write it into a repository."""
    return dict(repository_static_files())[MATERIAL_SYMBOLS_FONT_PATH]


def ligatures_in(woff2: bytes) -> frozenset[str]:
    """Every letter sequence the font substitutes a single glyph for.

    ``fi`` and ``ffl`` for a text face; ``folder`` and ``unfold_more``
    for the icon face -- the names an element carrying the icon class
    can hold and have drawn.
    """
    font = TTFont(io.BytesIO(woff2))
    text_of = {glyph: chr(code) for code, glyph in font.getBestCmap().items()}
    names: set[str] = set()
    for lookup in font["GSUB"].table.LookupList.Lookup:
        for subtable in lookup.SubTable:
            if lookup.LookupType == _EXTENSION:
                subtable = subtable.ExtSubTable
            if getattr(subtable, "LookupType", _LIGATURE) != _LIGATURE:
                continue
            for first, ligatures in subtable.ligatures.items():
                names.update(
                    text_of[first] + "".join(
                        text_of[glyph] for glyph in ligature.Component)
                    for ligature in ligatures
                )
    return frozenset(names)

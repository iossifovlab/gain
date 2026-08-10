"""Markdown rendering for GAIn's generated documentation pages.

``render_markdown`` is the one place GAIn turns documentation prose into
HTML.  It has the same ``str -> str`` contract as ``markdown2.markdown``
and post-processes its output: a raw ``<`` (or ``</``) whose tag name is
neither a known HTML/SVG/MathML element nor hyphenated is escaped to
``&lt;``, so prose like ``values <thresh are dropped`` reaches the reader
whole instead of being swallowed by the browser as a bogus tag.

**This is not a sanitizer.**  A recognized element -- including
``<script>`` -- passes through untouched, attributes and all: GRR content
is trusted by authorship (ADR 0016), and the characterization tests in
``tests/small/annotation/test_annotate_doc_trust.py`` pin that trust.
Hyphenated names pass too, so custom elements from out-of-tree plugin
documentation keep rendering.

Accepted limitations: prose whose accidental tag name collides with a
real element name (``values <var are dropped``, ``<b``, ``<a``, ...) is
still eaten by the browser -- rescuing it would mean guessing the
author's intent.  So is prose a browser reads as a *bogus comment*:
``</`` followed by a non-letter, and ``<!`` outside a real comment or
doctype, each consume to the next ``>`` and are left alone here.

Escaping happens on the rendered HTML, not the Markdown source:
markdown2 already escapes ``<`` inside code spans and fenced blocks, and
pre-escaping the source would make it re-escape the ``&`` of a
pre-inserted ``&lt;`` into visible garbage.  The content of raw-text
elements (``<script>``, ``<style>``, ...) is exempt from the rescue:
character references do not decode there, so an inserted ``&lt;`` would
reach the script engine or CSS parser as five literal characters instead
of the author's ``<``.
"""
from __future__ import annotations

import re

from markdown2 import markdown

#: Element names a browser recognizes -- HTML per the WHATWG living
#: standard plus obsolete names browsers still tokenize, and the SVG and
#: MathML names (lowercased; HTML parsing is case-insensitive) so
#: deliberate inline figures and formulas keep rendering.  Membership
#: means "a browser treats this as a real element", nothing more.
_KNOWN_ELEMENTS = frozenset({
    "a", "abbr", "address", "area", "article", "aside", "audio", "b",
    "base", "bdi", "bdo", "blockquote", "body", "br", "button", "canvas",
    "caption", "cite", "code", "col", "colgroup", "data", "datalist",
    "dd", "del", "details", "dfn", "dialog", "div", "dl", "dt", "em",
    "embed", "fieldset", "figcaption", "figure", "footer", "form", "h1",
    "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup", "hr",
    "html", "i", "iframe", "img", "input", "ins", "kbd", "label",
    "legend", "li", "link", "main", "map", "mark", "menu", "meta",
    "meter", "nav", "noscript", "object", "ol", "optgroup", "option",
    "output", "p", "picture", "pre", "progress", "q", "rp", "rt", "ruby",
    "s", "samp", "script", "search", "section", "select", "slot",
    "small", "source", "span", "strong", "style", "sub", "summary",
    "selectedcontent", "sup", "table", "tbody", "td", "template",
    "textarea", "tfoot", "th", "thead", "time", "title", "tr", "track",
    "u", "ul", "var", "video", "wbr",
    # Obsolete but still tokenized as elements by browsers.
    "acronym", "applet", "basefont", "bgsound", "big", "blink", "center",
    "dir", "font", "frame", "frameset", "isindex", "keygen", "listing",
    "marquee", "menuitem", "nobr", "noembed", "noframes", "param",
    "plaintext", "rb", "rtc", "spacer", "strike", "tt", "xmp",
    # SVG
    "svg", "animate", "animatemotion", "animatetransform", "circle",
    "clippath", "defs", "desc", "ellipse", "feblend", "fecolormatrix",
    "fecomponenttransfer", "fecomposite", "feconvolvematrix",
    "fediffuselighting", "fedisplacementmap", "fedistantlight",
    "fedropshadow", "feflood", "fefunca", "fefuncb", "fefuncg",
    "fefuncr", "fegaussianblur", "feimage", "femerge", "femergenode",
    "femorphology", "feoffset", "fepointlight", "fespecularlighting",
    "fespotlight", "fetile", "feturbulence", "filter", "foreignobject",
    "g", "image", "line", "lineargradient", "marker", "mask", "metadata",
    "mpath", "path", "pattern", "polygon", "polyline", "radialgradient",
    "rect", "set", "stop", "switch", "symbol", "text", "textpath",
    "tspan", "use", "view",
    # MathML
    "math", "annotation", "maction", "menclose", "merror", "mfrac",
    "mglyph", "mi", "mmultiscripts", "mn", "mo", "mover", "mpadded",
    "mphantom", "mprescripts", "mroot", "mrow", "ms", "mspace", "msqrt",
    "mstyle", "msub", "msubsup", "msup", "mtable", "mtd", "mtext",
    "mtr", "munder", "munderover", "semantics",
})

#: A ``<`` that a browser would read as opening a start or end tag: an
#: optional ``/``, then a name starting with an ASCII letter and running
#: to the first character that cannot be part of a tag name.
_TAG_OPENER = re.compile(r"<(/?)([A-Za-z][^\t\n\f\r />]*)")

#: A whole raw-text element, open tag through matching close tag (or the
#: end of the document while unclosed, as in a browser).  Both the name
#: after ``<`` and after ``</`` must be followed by a character that ends
#: a tag name, exactly as the tokenizer requires -- ``<scripty>`` opens
#: no script, and ``</scriptx>`` closes none.
_RAW_TEXT_ELEMENT = re.compile(
    r"<(script|style|xmp|iframe|noembed|noframes)(?=[\t\n\f\r />])[^>]*>"
    r".*?"
    r"(?:</\1(?=[\t\n\f\r />])[^>]*>|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _rescue_bogus_tag(match: re.Match[str]) -> str:
    slash, name = match.groups()
    # The exact-hit test first: markdown2's own output is lowercase and
    # hyphen-free, so almost every match resolves there.  The fallback is
    # ASCII-only lowering because that is how browsers compare tag names;
    # str.lower() folds more than the tokenizer does (e.g. Kelvin-sign K).
    if (name in _KNOWN_ELEMENTS or "-" in name
            or (name.isascii() and name.lower() in _KNOWN_ELEMENTS)):
        return match.group(0)
    return f"&lt;{slash}{name}"


def _escape_bogus_tags(html: str) -> str:
    """Escape bogus tag openers everywhere outside raw-text elements."""
    out = []
    pos = 0
    for raw in _RAW_TEXT_ELEMENT.finditer(html):
        out.append(_TAG_OPENER.sub(_rescue_bogus_tag, html[pos:raw.start()]))
        out.append(raw.group(0))
        pos = raw.end()
    out.append(_TAG_OPENER.sub(_rescue_bogus_tag, html[pos:]))
    return "".join(out)


def render_markdown(text: str, **kwargs: object) -> str:
    """Render Markdown to HTML, rescuing prose from bogus tags.

    Keyword options (``extras=...`` and the rest) pass through to
    ``markdown2.markdown``.  Returns a plain ``str``: the attribute side
    channel of markdown2's return type (``toc_html``, ``metadata``) is
    not carried over.
    """
    return _escape_bogus_tags(markdown(text, **kwargs))

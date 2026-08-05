"""Reduce rendered Markdown to markup that cannot run script.

The documentation a page renders is a concatenation of two things with
different provenance: prose a resource's own ``genomic_resource.yaml``
supplies -- remote GRR content this project does not author -- and
sentences the annotators write about themselves in this repository,
which legitimately carry HTML (every built-in annotator appends a
``More info`` anchor, the spliceai plugin puts ``<br/>`` and ``<em>`` in
an attribute description).  The two are joined into one string long
before anything renders it, so the sink cannot tell them apart.

Escaping the whole string therefore costs the project its own links, and
trusting the whole string is the injection this module exists to close.
What separates them is not who wrote the markup but what the markup can
do: this sanitizer keeps the small vocabulary documentation prose is
written in and turns everything else back into visible text.  A
``<script>``, an ``on*`` handler and a ``javascript:`` URL all become
text a reader can see -- which also shows a maintainer exactly what a
resource tried to inject.

The page is rebuilt from what :mod:`html.parser` reads rather than
patched with regular expressions: every attribute value is re-quoted and
re-escaped on the way out, so nothing in a value can end its attribute
and start another one.
"""
from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

#: Tags documentation prose is written in.  Markdown renders to this
#: vocabulary, and the annotators' in-tree documentation uses a subset of
#: it.  A tag outside the list is shown as text, so adding one is a
#: decision to let resource metadata emit it.
ALLOWED_TAGS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title", "target", "rel"}),
    "blockquote": frozenset(),
    "br": frozenset(),
    "code": frozenset(),
    "dd": frozenset(),
    "del": frozenset(),
    "dl": frozenset(),
    "dt": frozenset(),
    "em": frozenset(),
    "h1": frozenset(),
    "h2": frozenset(),
    "h3": frozenset(),
    "h4": frozenset(),
    "h5": frozenset(),
    "h6": frozenset(),
    "hr": frozenset(),
    "i": frozenset(),
    "b": frozenset(),
    "li": frozenset(),
    "ol": frozenset(),
    "p": frozenset(),
    "pre": frozenset(),
    "strong": frozenset(),
    "sub": frozenset(),
    "sup": frozenset(),
    "table": frozenset(),
    "tbody": frozenset(),
    "td": frozenset(),
    "th": frozenset(),
    "thead": frozenset(),
    "tr": frozenset(),
    "ul": frozenset(),
}

#: Attributes whose value a browser resolves as a URL.
URL_ATTRIBUTES = frozenset({"href", "src"})

#: Schemes a URL attribute may name.  Everything else -- ``javascript:``
#: first among them -- is refused; a URL naming no scheme at all is
#: relative and stays.
SAFE_URL_SCHEMES = frozenset({"http", "https", "ftp", "mailto", "tel"})

#: Tags with no closing tag, emitted self-closed.
_VOID_TAGS = frozenset({"br", "hr"})

_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")

# A browser drops ASCII whitespace and C0 controls while resolving a
# URL, so `java\tscript:...` runs.  They go before the scheme is read.
_URL_NOISE_RE = re.compile(r"[\x00-\x20\x7f]")


def is_safe_url(value: str) -> bool:
    """Would a browser follow ``value`` without running script?

    The scheme is read the way a browser reads it -- after dropping the
    whitespace and control characters it ignores, and case-insensitively
    -- rather than by matching a literal spelling, because the parser has
    already resolved ``javascript&#58;`` and ``JAVASCRIPT&#x3a;`` to the
    same runnable URL.
    """
    collapsed = _URL_NOISE_RE.sub("", value)
    scheme = _SCHEME_RE.match(collapsed)
    if scheme is None:
        return True
    return scheme.group(0)[:-1].lower() in SAFE_URL_SCHEMES


def _is_allowed(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
    """May ``tag`` stay markup, with exactly the attributes it carries?

    A tag carrying anything unrecognized -- an ``on*`` handler, a
    ``style``, a URL with a scheme that runs script -- is refused whole
    rather than stripped down, so the payload stays visible on the page
    instead of leaving a tag that no longer says what it tried to do.
    """
    allowed_attributes = ALLOWED_TAGS.get(tag)
    if allowed_attributes is None:
        return False
    for name, value in attrs:
        if name not in allowed_attributes:
            return False
        if name in URL_ATTRIBUTES and value is not None \
                and not is_safe_url(value):
            return False
    return True


class _Sanitizer(HTMLParser):
    """Rebuild a document, keeping only markup that cannot run script."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._open_tags: list[str] = []

    @property
    def result(self) -> str:
        """The rebuilt document, with every tag it opened closed.

        An end tag is emitted only for a start tag this sanitizer itself
        emitted, and whatever is still open when the input ends is closed
        here.  Documentation is rendered *into* a page, so a stray
        ``</p>`` from a resource must not close an element the page
        opened, and an unclosed ``<em>`` must not run on into the rest of
        it.
        """
        closing = "".join(
            f"</{tag}>" for tag in reversed(self._open_tags)
        )
        return "".join(self._parts) + closing

    def _emit_as_text(self, source: str) -> None:
        self._parts.append(escape(source, quote=False))

    def _emit_tag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        """Write a start tag, self-closed only where HTML says it is.

        Whether the source spelled the tag ``<tag>`` or ``<tag/>`` is
        not an input here: HTML5 reads the slash on a non-void element
        as nothing at all, so honouring it would emit a tag that opens
        an element the sanitizer then never closes.
        """
        rendered = [tag]
        rendered.extend(
            name if value is None else f'{name}="{escape(value)}"'
            for name, value in attrs
        )
        closing = " />" if tag in _VOID_TAGS else ">"
        self._parts.append(f"<{' '.join(rendered)}{closing}")

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        if _is_allowed(tag, attrs):
            self._emit_tag(tag, attrs)
            if tag not in _VOID_TAGS:
                self._open_tags.append(tag)
        else:
            self._emit_as_text(self.get_starttag_text() or f"<{tag}>")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        """Read ``<tag/>`` as the start tag a browser reads it as.

        HTML5 ignores the trailing slash on an element that is not void,
        so ``<a href="..."/>`` opens an anchor exactly as ``<a
        href="...">`` does -- and a formatting element a browser has
        opened is carried across the markup that follows.  Emitting it
        self-closed and not recording it would therefore hand the rest
        of the page to whatever a resource wrote: the ``result``
        invariant holds only if this form is tracked like any other
        start tag.  A void tag is unaffected -- it has no end tag, and
        :meth:`_emit_tag` still writes it self-closed.
        """
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if tag in self._open_tags:
            self._open_tags.pop(len(self._open_tags) - 1
                                - self._open_tags[::-1].index(tag))
            self._parts.append(f"</{tag}>")
        else:
            self._emit_as_text(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self._emit_as_text(data)

    def handle_comment(self, data: str) -> None:
        self._emit_as_text(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self._emit_as_text(f"<!{decl}>")

    def unknown_decl(self, data: str) -> None:
        self._emit_as_text(f"<![{data}]>")

    def handle_pi(self, data: str) -> None:
        self._emit_as_text(f"<?{data}>")


def sanitize_html(html: str) -> str:
    """Return ``html`` with every tag it may not emit turned into text."""
    sanitizer = _Sanitizer()
    sanitizer.feed(html)
    sanitizer.close()
    return sanitizer.result

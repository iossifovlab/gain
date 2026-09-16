"""Read the stylesheet a rendered template carries: its rules, one by one.

Every template test that asserts something about a page's CSS reaches the
stylesheet through here.  Before iossifovlab/gain#1322 two of them carried
their own reader -- the same rule regex, the same comment strip, the same
"first ``<style>``" lookup, the same flat-sheet caveat written out twice --
and the two had already parted on what "the declarations for ``td``"
means: one matched the whole selector text, so ``td, th { ... }`` answered
for neither, while the other split the list, so it answered for both.  A
fix to one reader silently missed the other.

That difference is deliberate and stays with the consumers.  What is
shared is the raw material both need:

* :func:`rules_in` returns every rule of the markup's first ``<style>``
  as a :class:`Rule`, in source order, with its selector *list* already
  split and its declarations as ``(property, value)`` pairs.  A rule
  stated twice arrives twice; a selector list arrives as a list.  Folding
  later rules over earlier ones (the cascade a page's own tables resolve
  to) is its one consumer's projection, written there.
* :func:`declared_for` is the other projection, shared by the modules
  that compare a page's sheet with its description's: every rule that
  names a selector contributes, and the declarations come back sorted.

It takes **markup**, never a bare stylesheet.  Handing raw CSS to the rule
regex would quietly make everything between one ``}`` and the next ``{``
part of the following rule's selector list -- wrong for one rule only, and
silently.  There is no caller that wants the sheet without reading it, so
there is no entry point that could make that mistake.

Flat by assumption: the sheets read here have no at-rules, so nothing
nests.  Wrap one rule in an ``@media`` block and this reads the two as
separate rules with a stray selector between them.  A test that needed
more would be reading the wrong thing.
"""
from __future__ import annotations

import re
from typing import NamedTuple

#: One rule of a stylesheet: everything up to ``{`` is the selector list,
#: everything to the matching ``}`` is the declarations.
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

#: Whitespace that spans a line break: a value wrapped for readability.
#: Only this is folded.  A CSS value can carry significant spaces inside
#: quotes -- the shared sheet's list marker is ``'-  '``, two of them --
#: and collapsing every run would report a value no sheet contains.
_LINE_WRAP = re.compile(r"\s*\n\s*")


class Rule(NamedTuple):
    """One rule of a stylesheet, as its selector list and declarations."""

    selectors: list[str]
    declarations: list[tuple[str, str]]


#: A ``<style>`` element's body.  The tag may carry attributes: a reader
#: that matched only the bare tag would go blind -- and every "no third
#: party" assertion built on it vacuous -- the day a template wrote
#: ``<style type="text/css">``.
_STYLE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.DOTALL)

#: What a ``url()`` loads: ``url(<it>)``, quoted or not.  Exported: the
#: origin scanner in ``page_origins`` reads the same thing, and two
#: spellings of it would part the way the two readers gain#1322 folded
#: did.
CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+?)['\"]?\s*\)")


def stylesheets_in(markup: str) -> list[str]:
    """The body of every ``<style>`` of the markup, in source order.

    Every one, not only the first: a resource page declares its icon
    face inside the sorter's own block, the second one (gain#1400).
    """
    return _STYLE.findall(markup)


def _rules_of(stylesheet: str) -> list[Rule]:
    """The rules of one stylesheet's text, in source order."""
    return [
        Rule(
            [selector.strip() for selector in selectors.split(",")],
            declarations_in(body),
        )
        for selectors, body in _CSS_RULE.findall(
            _CSS_COMMENT.sub("", stylesheet))
    ]


def rules_in(markup: str) -> list[Rule]:
    """Every rule of ``markup``'s first ``<style>``, in source order.

    ``markup`` is a whole page or a fragment of one -- a description's
    shadow root -- and it is that markup's own first ``<style>`` that is
    read, so a page and a shadow root inside it each answer for
    themselves.
    """
    stylesheets = stylesheets_in(markup)
    assert stylesheets, "the markup carries no <style>"
    return _rules_of(stylesheets[0])


def font_faces_in(markup: str) -> dict[str, str]:
    """Family -> ``src`` url of every ``@font-face`` the markup declares.

    Across every ``<style>`` of the page, since gain#1400 put the faces
    inside them: the url is as written, relative to the page, for the
    caller to resolve against wherever the page was published.  A face
    declared twice for one family answers with the last.
    """
    faces: dict[str, str] = {}
    for stylesheet in stylesheets_in(markup):
        for rule in _rules_of(stylesheet):
            if rule.selectors != ["@font-face"]:
                continue
            declarations = dict(rule.declarations)
            url = CSS_URL.search(declarations["src"])
            assert url is not None, declarations["src"]
            faces[declarations["font-family"].strip("'\"")] = url.group(1)
    return faces


def declarations_in(block: str) -> list[tuple[str, str]]:
    """A declaration block as ``(property, value)`` pairs, in source order.

    A block, not a sheet: the body of one rule, or an element's own
    ``style`` attribute.  The trap :func:`rules_in` guards against -- raw
    CSS handed to the rule regex -- does not exist here, because a block
    has no braces to misread.
    """
    return [
        (property_.strip(), _LINE_WRAP.sub(" ", value).strip())
        for property_, _, value in (
            declaration.partition(":") for declaration in block.split(";")
        )
        if property_.strip()
    ]


def declared_for(markup: str, selector: str) -> list[str]:
    """Return what ``markup``'s first ``<style>`` declares for ``selector``.

    Rules are matched on the selector appearing in the rule's selector
    *list*, so ``td, th { ... }`` answers for ``td`` and for ``th`` alike,
    and every rule that names it contributes.  Compound selectors that
    would also reach the element -- ``#resource-table th``,
    ``.scrollable-table-container td`` -- are deliberately left out: what
    is compared is the rule a bare element gets on each side of the shadow
    boundary, not the full cascade any one element resolves to.

    Declarations come back as ``property: value`` strings, sorted, because
    this is used to compare two sheets and neither the order rules were
    written in nor the indentation they were written at is part of what a
    reader gets.
    """
    return sorted(
        f"{property_}: {value}"
        for rule in rules_in(markup)
        if selector in rule.selectors
        for property_, value in rule.declarations
    )

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
  to) or letting every rule that names a selector contribute (comparing
  what two sheets say) is each consumer's projection, written once there.

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


def rules_in(markup: str) -> list[Rule]:
    """Every rule of ``markup``'s first ``<style>``, in source order.

    ``markup`` is a whole page or a fragment of one -- a description's
    shadow root -- and it is that markup's own first ``<style>`` that is
    read, so a page and a shadow root inside it each answer for
    themselves.
    """
    assert "<style>" in markup, "the markup carries no <style>"
    opening = markup.index("<style>") + len("<style>")
    stylesheet = markup[opening:markup.index("</style>", opening)]
    return [
        Rule(
            [selector.strip() for selector in selectors.split(",")],
            declarations_in(body),
        )
        for selectors, body in _CSS_RULE.findall(
            _CSS_COMMENT.sub("", stylesheet))
    ]


#: Every ``<style>`` of a page, not only the first: a resource page
#: declares its icon face inside the sorter's own block, the second one.
_STYLE = re.compile(r"<style>(.*?)</style>", re.DOTALL)

#: The file a ``src`` declaration loads: ``url(<it>) format(...)``.
_URL = re.compile(r"url\(\s*['\"]?([^'\")]+?)['\"]?\s*\)")


def font_faces_in(markup: str) -> dict[str, str]:
    """Family -> ``src`` url of every ``@font-face`` the markup declares.

    Across every ``<style>`` of the page, since gain#1400 put the faces
    inside them: the url is as written, relative to the page, for the
    caller to resolve against wherever the page was published.  A face
    declared twice for one family answers with the last.
    """
    faces: dict[str, str] = {}
    for stylesheet in _STYLE.findall(markup):
        for selectors, body in _CSS_RULE.findall(
                _CSS_COMMENT.sub("", stylesheet)):
            if selectors.strip() != "@font-face":
                continue
            declarations = dict(declarations_in(body))
            url = _URL.search(declarations["src"])
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

# 30. The resource page's description renders behind a shadow root, on purpose

- **Status:** accepted
- **Date:** 2026-09-16
- **Issues:** [gain#1321](https://github.com/iossifovlab/gain/issues/1321)
  (this record); [gain#1279](https://github.com/iossifovlab/gain/issues/1279)
  (the drift that raised the question);
  [gain#1452](https://github.com/iossifovlab/gain/issues/1452) and
  [gain#1289](https://github.com/iossifovlab/gain/issues/1289) (the seam's
  first two test cases after gain#1279)
- **Related:** [ADR 0016](0016-grr-content-is-trusted-by-authorship.md), which
  is why the boundary below is *not* a security boundary

## Context

A resource page renders `meta.description` — curator-authored Markdown, turned
into HTML by `render_markdown` — into a declarative shadow root:
`<template shadowrootmode="open">` inside the Description cell. A shadow root
is a style boundary. The page's stylesheet — the base sheet, the breadcrumb
and copy-button sheets, and the per-type `styles_template` each
implementation names — does not reach inside it; only inherited properties
(`color`, `font-family`) cross. The shadow root carries a `<style>` of its
own.

Nobody had written down why. `git log -S shadowrootmode` bottoms out at the
May 2026 bulk template move, and the comment at the site said *that* the
boundary exists, not what it is for. The cost of that omission arrived with
gain#1279: the shadow root's `<style>` was a hand-copied *subset* of the page's
rules — an `img` cap and two link rules — and it had fallen behind, so the
published SFARI gene-score pages showed a borderless description table under
browser-default black headings, on a page whose own tables and headings were
styled. gain#1279 fixed the drift by extracting the element rules both sides
need into one partial (`content_styles.jinja`) that the page's sheet and the
shadow root's `<style>` both include. It deliberately did **not** ask whether
the boundary should exist at all, and filed gain#1321 to ask.

The question was worth asking, because the evidence for removal looked
strong. Measured on a rendered gene-score page at triage:

* Of every selector in the page's stylesheet, exactly **one** would wrongly
  reach a description rendered in the light DOM: `#resource-table th
  { text-align: end; width: 100px }`, because a description's table headers
  are descendants of `#resource-table`. One more would reach it harmlessly —
  the `:where(.page-content) img { max-width: 100% }` floor, whose `:where()`
  contributes nothing to specificity, so it ties the description's own bare
  `img` cap and loses to it on source order. Everything else either
  names a class, id or attribute a description never carries, or is a bare
  element rule the description wants anyway. The four sheets added after
  gain#1279 (breadcrumb, copy button, two font faces — gain#1400, gain#1477)
  add no bare-element selector.
* Nothing outside the template depends on the markup. The `shadowrootmode`
  occurrences in `web_api`, `spliceai_annotator` and gpf test fixtures are
  stale rendered `index.html` snapshots that no test parses.
* Across the 335 `genomic_resource.yaml` files in the checked-out `grr`,
  `grr_sfari` and `grr_bench`, no description carries a raw `<style>`,
  `<script>`, `id=` or `class=`.

So removing the boundary would have cost scoping one selector and deleting
one partial, and would have made the per-type sheets reach the description
"for free". That last consequence is where the evidence turned.

## Decision

**A resource page renders its description into a declarative shadow root,
and the boundary is deliberate.** It stays, for three reasons, and the
alternative is recorded as rejected.

### Authored markup stays local — containment of accidents, not of threats

`render_markdown` passes raw HTML through: a curator who wants a linked
directory tree writes the `<pre>` themselves, and gain#1452 supports exactly
that. The same door admits a `<style>` element, an `id`, a class. Inside a
shadow root those stay the description's own. A curator's
`<style>table { … }</style>`, written to tune their table, cannot restyle the
page's chrome; an `id="resource-table"` cannot collide with the page's; the
page's scripts — `getElementById`, `querySelectorAll("[data-modal-trigger]")`,
the copy handler — cannot find description content and act on it by mistake.

This is **not** a security boundary. [ADR 0016](0016-grr-content-is-trusted-by-authorship.md)
decides that GAIn does not defend against a hostile GRR: a `<script>` in a
description runs, shadow root or no shadow root, and this record does not
change that. What the boundary contains is the *accident* — an author styling
their description and reaching the page — and that is worth containing on a
page whose chrome the author never sees while writing.

That no published description carries such markup today is not an argument
against the boundary. It is one edit away, the edit is a legitimate one, and
its blast radius without the boundary is the whole page.

### Per-type sheets are page-layout decisions and must not reach content

Two per-type sheets — the gene score's and the genomic score's — set a bare
`table { table-layout: fixed }`. That is right for the page's own score
tables, which have many columns of similar width. It is wrong for the
two-column category table a curator writes into a description, which reads
better sized by its content; gain#1279 looked at exactly this case on the
SFARI pages and judged the mismatch acceptable — forcing equal columns on
that table being arguably the worse result — rather than a defect to fix.
The boundary is what lets a per-type sheet be written as a bare
element rule and still stop at the page's own markup. Without it every such
rule would need scoping to the page's tables, by every author of every future
per-type sheet, forever.

### Shared element rules travel through one partial, included on both sides

The description does need most of what the page's bare-element rules say —
borders, header tint, padding, heading colour, list markers, link colours,
the monospace family for `pre` and `code`. Those live in the content-styles
partial, which `base_styles.jinja` includes for the page and the shadow
root's `<style>` includes for the description, so both sides read one text
and cannot drift. Which side a rule lives on is decided by its *selector*,
mechanically: a rule naming only elements and pseudo-elements belongs in the
partial, because elements are all rendered Markdown is made of; a rule naming
a class, id or attribute addresses markup a template emits and stays with the
page. The partial's own header states that rule and its one exception
(`body`, which a shadow root has no element to match); this record does not
restate the mechanism, it records why the mechanism is the right answer to
"how does a description get the page's look" rather than removing the wall.

The mechanism has been exercised twice since gain#1279 without being re-cut:
gain#1452 added the monospace rules for `pre` and `code` to the partial and
they reached both sides; gain#1289 decided fenced code is never highlighted
server-side, so no per-language rules needed a home on either side.

### The Summary cell needs no boundary, and no longer has one

The Summary cell was also wrapped in a shadow root. The template autoescapes
(`resource_template.jinja` is not among the `MARKDOWN_TEMPLATES`), so that
root held escaped text and nothing else — no markup for the boundary to keep
local, no `<style>` for it to keep out. It was a second shadow root on the
page with no reason of its own, and a reader would rightly have asked whether
it shared this record's reason. It does not. This record removes it, so the
page has exactly one shadow root and it is the one explained here.

### The rejected alternative

Remove the boundary: drop `shadowrootmode` from the Description row, scope
`#resource-table th` to the page's own field cells, delete the content-styles
partial, let the page's sheet style the description directly. Its cost, for
the record: the scoping of one selector now, plus the scoping of
`table-layout: fixed` in two per-type sheets — or accepting fixed layout on
description tables, which gain#1279 had already judged worse — plus the same
scoping for every future page rule whose ancestor selector a description also
matches, plus the loss of the containment above. Against that it would have
saved one partial and one `<style>` element per page.

## Consequences

The operating rules for the seam, which are what a later author actually
needs:

* **A new element rule the description needs goes into the content-styles
  partial.** It is never copied into the shadow root's `<style>` by hand —
  that is the drift gain#1279 closed, and `test_resource_page_description_styles`
  fences it by asserting the two sides declare the same thing.
* **A rule a per-type sheet adds does not reach the description, and that
  is intended.** `test_resource_page_shadow_root` pins it for
  `table-layout: fixed` on a gene-score page. Whoever wants a per-type rule
  inside a description has found a case this record did not foresee, and
  should amend it rather than include the sheet.
* **A description-only rule lives in the shadow root's own `<style>`.** The
  `img` cap is the example: the page sizes the thumbnails it emits itself, so
  the cap has no page counterpart, by design.
* **A page rule that names page chrome needs no scoping** — `#resource-table
  th` is the standing example. That convenience is a debt against the
  boundary: whoever removes the boundary later inherits the scoping.
* **The description is the only shadow root on a resource page**, and
  `test_resource_page_shadow_root` names the decision when it goes missing
  and counts the page's shadow roots to one. A second one needs a reason of
  its own, written down.

The cost this record carries forward. Two `<style>` elements per page instead
of one, and a seam a newcomer has to learn — the page's look does not simply
apply to the description, it is *shared into* it through the partial. And
the flip side of the scripts not reaching in by mistake: they do not reach in
on purpose either. The sortable-table script's `querySelectorAll("table")`,
the modal image zoom and the copy handler all stop at the boundary, so a
figure in a description gets the `img` cap and no zoom, and a description
table gets no sorter however its headers are marked. That is accepted here,
not overlooked; a description that needs page behaviour is the case that
would reopen this record. The partial's header and the comment at the shadow
root both point here.

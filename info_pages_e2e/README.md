# info_pages_e2e

Browser coverage for the client-side JavaScript on the GRR's generated
info pages.

## Why this exists

The pages `grr_manage repo-info` writes carry real behaviour — sortable
statistics tables, and on the index page a search, a tree view and a
column resizer. gain-core's CI image is `python:3.12-slim` and has no JS
runtime, so the tests in `core/tests/small/` can only assert the *markup
contract* the templates emit by reading the rendered source. Nothing was
executing any of that JavaScript (iossifovlab/gain#987).

This suite does. It covers the resource pages' sortable tables, and the
index page's search and tree view.

## Why it is separate from `web_e2e`

`web_e2e` drives the GAIn web application: a running `web_api`, a
`web_ui` frontend, an annotation pipeline. This suite drives *files*.
There is no service to start, no network, and no state to seed — a run
is under two seconds — and `web_e2e/Dockerfile.playwright` copies only
`web_e2e/`, so it could not reach a fixture generated anywhere else
anyway.

## Running it

The pages are generated, never committed: a committed page is a snapshot
of a template that has since moved on, and this suite exists to catch a
sorter that stopped working — not to notice that a copy of last month's
markup still sorts. So generate them first, from the repository root:

```bash
uv run python info_pages_e2e/generate_fixtures.py info_pages_e2e/fixtures
cd info_pages_e2e
npm ci
npx playwright test
```

`fixtures/` is git-ignored. Regenerate it after touching any template
under `core/gain/templates/`; the suite refuses to run without it rather
than skipping (`global-setup.ts`).

## How the pages reach the browser

`serving.ts` answers every request the page under test makes by reading
a file, and aborts anything it does not recognise. Three things are
served: the generated GRR, and the two packages the templates load from
a CDN — jQuery and sqlite-wasm, vendored as `devDependencies` at the
same pinned versions the templates name, so `npm ci` puts them on disk.

sqlite-wasm is matched by URL *prefix* rather than by the one URL
`grr_scripts.jinja` imports. `dist/index.mjs` locates its `sqlite3.wasm`
through `import.meta.url`, and fulfilling the module at its CDN address
leaves that address as the module's own URL — so the wasm is a second
request to the same origin. Allowing only the imported URL loads the
module and then starves it, and the page swallows the failure.

The GRR is served from a **virtual origin**, `https://grr.test/`.
Nothing listens on a socket; the origin exists only inside Playwright's
router. It is there because the index page cannot work from `file:` at
all: its search reads `.CONTENTS.sqlite3.gz` through `fetch()`, and
Chromium refuses `fetch()` of a `file:` URL in the renderer —
*URL scheme "file" is not supported*. Two ways around that were measured
and both are dead ends. It is not a permission decision, so
`--allow-file-access-from-files` does not lift it; and no request is
ever issued, so `page.route` never sees one to answer from disk either.

Serving over https removes the restriction instead of working around it,
and costs no fidelity — a published GRR *is* served over https, so this
is the transport the pages are written for. The resource-page specs use
the same origin, so there is one definition of what the suite allows
rather than two that can drift.

Type-check the specs with `npx tsc --noEmit` — Playwright transpiles
TypeScript without type-checking it, so nothing else does.

## In CI

`Dockerfile` is two stages. The first installs gain-core and runs
`generate_fixtures.py`; the second is the Playwright image, and copies
the generated HTML across. The image that runs the tests therefore
carries no Python, no gain and no GRR sources — just static pages and a
browser, which is what a published GRR page is.

The Jenkins stage runs it with `--network none`. That is an assertion
rather than a precaution: the suite answers every request from disk and
aborts whatever it does not recognise, so a test that grew a dependency
on the network fails there instead of passing slowly.

## The fixtures, and why they are shaped like that

`generate_fixtures.py` builds two GRRs, both from
`gain.genomic_resources.testing.info_page_fixtures`, which ships in the
wheel — so the builder stage can import them having installed nothing
but `gain-core`.

Two rather than one because they are tuned against each other: folders
added to the Coverage GRR would move the rows its sort assertions read,
and a sort trap added to the browse GRR would make its search assertions
depend on row order.

### `fixtures/browse` — for the index page

A repository laid out to be navigated: three top-level folders, a
four-segment path to descend, and two resource types. It carries two
search terms whose *reason* for matching differs — `marmoset` reaches
its resource only through that resource's summary, `phylop` only through
its id. The pair is what makes either half falsifiable: a page that had
stopped searching summaries fails the first and passes the second.

An unqualified FTS5 `MATCH` searches every indexed column — `full_id`,
`id`, `type`, `description`, `summary`, a score's `score_ids` and
`score_descriptions`, and every label — so a term that leaked into any
other column would still match, from the wrong one, leaving the browser
assertions green while proving something weaker than they claim. Nothing
in the fixture enforces that;
`core/tests/small/genomic_resources/test_info_page_browse_fixture.py`
does, in Python, so retuning a summary fails on that commit rather than
quietly hollowing out this suite.

It is published with `repo-index` alone rather than the full statistics
pass: the index page is assembled from `.CONTENTS` and the search index,
both of which `repo-index` writes from the manifests the builders
already produced. That also means it has no pages *inside* its resource
directories — `repo-index` writes none.

### `fixtures/grr` — for the resource pages

A three-contig position score. Its Coverage table is arranged so that a
*wrong* sort is distinguishable from *no* sort:

- covered-position counts are 9, 10 and 2 — as text those order
  `10 < 2 < 9`, so a column that lost its `data-sort="number"` would
  still sort, just wrongly
- the labelled genome resolves `chr1` and `chr2` but not `chr10`, so
  `chr10` has no coverage fraction and that cell carries no
  `data-sort-value` — the sorter has to treat it as *no value* rather
  than as zero
- the *all chromosomes* total sits in `<tfoot>`, which no comparator may
  reach

It is not realized from the `test_fixtures/mini-GRR` submodule:
mini-GRR is GAIn's onboarding example, and traps like these would make
it a worse teaching repository — the same reasoning that kept four
supplement resource types out of it in iossifovlab/gain#991.

`core/tests/small/genomic_resources/test_info_page_sortable_tables.py`
imports the *same* fixture, which is why it lives in the shipped
`testing` package rather than in either project. The two suites are
complementary: that one pins what the templates emit, this one pins what
the browser does with it — and because they share the fixture, retuning
the traps cannot leave one of them quietly asserting nothing.

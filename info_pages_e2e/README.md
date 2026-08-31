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

This suite does. It covers the resource pages' sortable tables today;
the index page's own behaviour is not driven here yet.

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
uv run python info_pages_e2e/generate_fixtures.py info_pages_e2e/fixtures/grr
cd info_pages_e2e
npm ci
npx playwright test
```

`fixtures/` is git-ignored. Regenerate it after touching any template
under `core/gain/templates/`; the suite refuses to run without it rather
than skipping (`global-setup.ts`).

Type-check the specs with `npx tsc --noEmit` — Playwright transpiles
TypeScript without type-checking it, so nothing else does.

## In CI

`Dockerfile` is two stages. The first installs gain-core and runs
`generate_fixtures.py`; the second is the Playwright image, and copies
the generated HTML across. The image that runs the tests therefore
carries no Python, no gain and no GRR sources — just static pages and a
browser, which is what a published GRR page is.

The Jenkins stage runs it with `--network none`. That is an assertion
rather than a precaution: the suite aborts every non-`file:` request, so
a test that grew a dependency on the network fails there instead of
passing slowly.

## The fixture, and why it is shaped like that

`generate_fixtures.py` builds a three-contig position score — from
`gain.genomic_resources.testing.info_page_fixtures`, which ships in the
wheel, so the builder stage can import it having installed nothing but
`gain-core`. Its Coverage table is arranged so that a *wrong* sort is
distinguishable from *no* sort:

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

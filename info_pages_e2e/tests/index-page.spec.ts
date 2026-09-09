import { expect, test, type Page } from '@playwright/test';

import {
  BROWSE_ID_ONLY_RESOURCE_ID,
  BROWSE_ID_ONLY_TERM,
  BROWSE_RESOURCE_COUNT,
  BROWSE_SUMMARY_ONLY_RESOURCE_ID,
  BROWSE_SUMMARY_ONLY_TERM,
  BROWSE_TOP_LEVEL_FOLDERS,
  FIXTURE_BROWSE_GRR,
  FIXTURE_GRR,
} from '../fixtures';
import { indexPageUrl, serveGrr } from '../serving';

/**
 * The resource rows a visitor can actually see.
 *
 * A search does not remove rows, it hides them and rewrites the ones it
 * keeps, so counting `tbody tr` counts the whole repository however
 * narrow the search was. Only the `:visible` filter reads what is on the
 * screen.
 */
function visibleRows(page: Page) {
  return page.locator('#resource-table tbody tr:visible');
}

/** The resource ids those rows name, in the order they are shown. */
function visibleResourceIds(page: Page) {
  return visibleRows(page).locator('td.id-cell a');
}

/** Type a term into the search box and run the search. */
async function search(page: Page, term: string): Promise<void> {
  await page.locator('#search-field').fill(term);
  await page.locator('#search-field').press('Enter');
}

test('the index page loads its search index with the network off', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_GRR);
  await page.goto(indexPageUrl());

  /* `#status` is written by `UpdateStatus`, which is only ever reached
   * after the page has deserialized `.CONTENTS.sqlite3.gz` and queried
   * it -- so this is the whole offline load path in one DOM assertion.
   *
   * It is the assertion to make, because every *cheaper* signal lies.
   * The rows are rendered by the template and are already in the markup;
   * `#search-container` is shown synchronously, outside the promise that
   * awaits the database. A page whose sqlite-wasm never loaded therefore
   * looks completely normal -- full table, visible search box -- and
   * only this line stays empty. */
  await expect(page.locator('#status')).toHaveText('2 resources');
});

test('a term matching only a summary filters the table to that resource', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_BROWSE_GRR);
  await page.goto(indexPageUrl());
  await expect(page.locator('#status')).toHaveText(
    `${BROWSE_RESOURCE_COUNT} resources`,
  );

  await search(page, BROWSE_SUMMARY_ONLY_TERM);

  /* The term appears in no id anywhere in the fixture -- pinned by
   * `test_info_page_browse_fixture.py` -- so the only way this row can
   * be here is that the page searched the summary column. */
  await expect(visibleResourceIds(page)).toHaveText([
    BROWSE_SUMMARY_ONLY_RESOURCE_ID,
  ]);
});

test('a term matching only an id filters the table to that resource', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_BROWSE_GRR);
  await page.goto(indexPageUrl());
  await expect(page.locator('#status')).toHaveText(
    `${BROWSE_RESOURCE_COUNT} resources`,
  );

  await search(page, BROWSE_ID_ONLY_TERM);

  /* The pair is what makes either half falsifiable. A page that had
   * stopped searching summaries would fail the test above and pass this
   * one; a page that searched nothing at all would show the whole
   * repository in both. Neither is distinguishable from a single
   * search that happens to return one row. */
  await expect(visibleResourceIds(page)).toHaveText([
    BROWSE_ID_ONLY_RESOURCE_ID,
  ]);
});

test('the hierarchical view lists the repository\'s top-level folders', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_BROWSE_GRR);
  await page.goto(indexPageUrl());

  await page.locator('#hierarchical-view-btn').click();

  /* The tree is folded up from `window.rowData`, which the templates
   * build by scraping the rendered table with jQuery. So this is also
   * the assertion that fails if the vendored jQuery stops being served:
   * the scrape never runs, `rowData` stays empty, and the tree renders
   * as an empty list rather than as an error. */
  await expect(page.locator('#hierarchical-list .hv-folder .hv-name'))
    .toHaveText(BROWSE_TOP_LEVEL_FOLDERS);
});

/*
 * The hosts the harness is allowed to answer for, spelled out here
 * rather than imported from `serving.ts`: a test that asked the helper
 * what it permits and then checked it permitted that would pass however
 * wide the helper had been opened.
 */
const SERVED_HOSTS = ['grr.test', 'ajax.googleapis.com', 'cdn.jsdelivr.net'];

test('the harness refuses every request it does not serve itself', async ({
  page,
}) => {
  const requested: string[] = [];
  const failed = new Set<string>();
  page.on('request', (request) => requested.push(request.url()));
  page.on('requestfailed', (request) => failed.add(request.url()));

  await serveGrr(page, FIXTURE_BROWSE_GRR);
  await page.goto(indexPageUrl());
  await expect(page.locator('#status')).toHaveText(
    `${BROWSE_RESOURCE_COUNT} resources`,
  );

  /* The pages link a Google Fonts stylesheet for the sort indicator's
   * glyphs, so there is always at least one request that must not be
   * answered -- which is what keeps the check below from passing
   * vacuously on a page that happened to ask for nothing. */
  const fonts = requested.filter((url) => url.includes('fonts.googleapis.com'));
  expect(fonts.length).toBeGreaterThan(0);
  expect(fonts.filter((url) => !failed.has(url))).toEqual([]);

  /* Nothing else got through either. The Jenkins stage runs this suite
   * under `docker run --network none`, so a page that grew a dependency
   * on the network would hang there; failing here instead is the whole
   * point of aborting rather than allowing. */
  const letThrough = requested.filter(
    (url) => !failed.has(url) && !SERVED_HOSTS.includes(new URL(url).host),
  );
  expect(letThrough).toEqual([]);
});

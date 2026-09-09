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

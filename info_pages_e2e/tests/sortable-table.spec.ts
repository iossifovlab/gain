import { expect, test, type Page } from '@playwright/test';

import { COVERAGE_RESOURCE, infoPageUrl } from '../fixtures';

/**
 * The info page's client-side sorter, driven in a browser.
 *
 * `core/tests/small/genomic_resources/test_info_page_sortable_tables.py`
 * covers the markup contract these tests rely on -- which columns carry
 * `data-sort`, which cells carry `data-sort-value`. It cannot cover what
 * happens when the header is clicked, because gain-core's CI image has no
 * JS runtime. That is what this file is for.
 */

/**
 * The Coverage table, addressed through the heading that introduces it.
 *
 * By heading rather than by position, because the page carries other
 * tables -- Files, for one -- and a positional selector would keep
 * matching after a template moved the sections around, just against the
 * wrong table.
 */
function coverageTable(page: Page) {
  return page
    .getByRole('heading', { name: 'Coverage', exact: true })
    .locator('xpath=following::table[1]');
}

/** One of that table's column headers, by its visible text. */
function columnHeader(page: Page, name: string) {
  return coverageTable(page).getByRole('columnheader', { name });
}

test.beforeEach(async ({ page }) => {
  /* The page links a Google Fonts stylesheet for the sort indicator's
   * three glyphs. Refusing every non-`file:` request keeps the suite
   * honestly offline -- the CI container has no egress, and a test that
   * silently depended on one would pass here and hang there. */
  await page.route(
    (url) => url.protocol !== 'file:',
    (route) => route.abort(),
  );
  await page.goto(infoPageUrl(COVERAGE_RESOURCE));
});

test('the sorter wires up the sortable headers', async ({ page }) => {
  const table = coverageTable(page);

  await expect(table.locator('thead th')).toHaveCount(4);
  /* `sortable` is added by the script and by nothing else, so this fails
   * if the script did not run at all -- which is the one thing the
   * template tests in gain-core cannot tell. */
  await expect(table.locator('thead th.sortable')).toHaveCount(4);
});

test('a numeric column sorts by number, not by text', async ({ page }) => {
  const table = coverageTable(page);

  await columnHeader(page, 'Covered positions').click();

  /* Counts 2, 9, 10. Compared as text they would be "10" < "2" < "9",
   * putting chr2 first -- so this pins the comparator the column asked
   * for, not merely that the rows moved. */
  await expect(table.locator('tbody td:first-child'))
    .toHaveText(['chr10', 'chr1', 'chr2']);
});

test('a second click reverses the order and the reported direction',
  async ({ page }) => {
    const table = coverageTable(page);
    const header = columnHeader(page, 'Covered positions');

    await header.click();
    await expect(header).toHaveAttribute('aria-sort', 'ascending');

    await header.click();

    /* aria-sort is the sorter's only record of direction: it is the
     * screen-reader state, the CSS hook for the indicator, and what the
     * next click reads. Asserting it alongside the row order is what
     * catches the two drifting apart. */
    await expect(header).toHaveAttribute('aria-sort', 'descending');
    await expect(table.locator('tbody td:first-child'))
      .toHaveText(['chr2', 'chr1', 'chr10']);
  });

test('the all-chromosomes total stays put in either direction',
  async ({ page }) => {
    const table = coverageTable(page);
    const header = columnHeader(page, 'Covered positions');
    /* 21 = 9 + 10 + 2. The largest count in the table is 10, so a total
     * that had been dragged into the body would sort to an end rather
     * than stay where it is -- both assertions below would see it. */
    const total = ['all chromosomes', '21', '', '3'];

    await expect(table.locator('tfoot td')).toHaveText(total);

    await header.click();

    await expect(table.locator('tbody tr')).toHaveCount(3);
    await expect(table.locator('tfoot td')).toHaveText(total);

    await header.click();

    await expect(table.locator('tbody tr')).toHaveCount(3);
    await expect(table.locator('tfoot td')).toHaveText(total);
  });

test('a cell with no sort key sinks to the bottom in either direction',
  async ({ page }) => {
    const table = coverageTable(page);
    /* chr10 resolves no length from the labelled genome, so it has no
     * coverage fraction and its cell carries no `data-sort-value`. "No
     * value" is not a value: it must not sort as 0 (which would float it
     * to the top ascending) nor as the empty string. */
    const header = columnHeader(page, 'Covered %');
    const fractions = table.locator('tbody td:nth-child(3)');

    /* Sort by another column first. Ascending order on this one happens
     * to be the order the page is rendered in, so without scrambling
     * the rows the ascending assertion below would be satisfied by a
     * click that did nothing at all. */
    await columnHeader(page, 'Covered positions').click();
    await expect(fractions).toHaveText(['', '9.00%', '20.00%']);

    await header.click();

    await expect(fractions).toHaveText(['9.00%', '20.00%', '']);

    await header.click();

    await expect(fractions).toHaveText(['20.00%', '9.00%', '']);
  });

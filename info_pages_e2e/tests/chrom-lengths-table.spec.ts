import { expect, test, type Page } from '@playwright/test';

import { COVERAGE_RESOURCE, FIXTURE_GRR } from '../fixtures';
import { infoPageUrl, serveGrr } from '../serving';
import { columnHeader, tableAfterHeading } from '../tables';

/**
 * The Chromosome lengths table, driven in a browser (gain#1579).
 *
 * `core/tests/small/genomic_resources/test_info_page_chrom_lengths.py`
 * pins the markup -- the columns the stored file yields, the marker on
 * a contig the genome does not list, which cells carry a sort key --
 * and `sortable-table.spec.ts` pins what the sorter does with a key, a
 * missing key and a direction, on the Coverage table of the same page.
 * What neither can tell is what this table looks like once the script
 * has run: that it was wired up at all, that the marker is drawn, and
 * that the marker nested in a contig cell does not become part of what
 * the contig column sorts on. That is what this file is for.
 *
 * The Coverage fixture is the partial-overlap case: a tabix score over
 * chr1, chr2 and chr10 labelled with a genome listing chr1 and chr2.
 */

function lengthsTable(page: Page) {
  return tableAfterHeading(page, 'Chromosome lengths');
}

test.beforeEach(async ({ page }) => {
  await serveGrr(page, FIXTURE_GRR);
  await page.goto(infoPageUrl(COVERAGE_RESOURCE));
});

test('the sorter wires up every header', async ({ page }) => {
  const table = lengthsTable(page);

  /* `sortable` is added by the script and by nothing else. */
  await expect(table.locator('thead th.sortable')).toHaveCount(3);
  await expect(table.locator('tbody tr')).toHaveCount(3);
});

test('the contig the genome does not list is marked, and only it',
  async ({ page }) => {
    const table = lengthsTable(page);
    const marked = table.locator('tbody tr:has(.unlisted-marker)');

    await expect(marked).toHaveCount(1);
    await expect(marked.locator('td:first-child')).toContainText('chr10');
    await expect(marked.locator('.unlisted-marker')).toBeVisible();
    await expect(marked.locator('td:nth-child(2)')).toHaveText('');
  });

test('the marker does not leak into the contig sort', async ({ page }) => {
  const table = lengthsTable(page);
  const header = columnHeader(table, 'Chromosome');

  await header.click();
  await header.click();

  /* Natural order, descending then ascending: chr10 at one end each
   * time. A sorter reading the visible text instead of the key would
   * put "chr10 not in genome" between chr1 and chr2 both ways. The
   * cell's text includes the marker, hence the prefix match. */
  await expect(table.locator('tbody td:first-child'))
    .toHaveText([/^chr10/, /^chr2$/, /^chr1$/]);

  await header.click();

  await expect(table.locator('tbody td:first-child'))
    .toHaveText([/^chr1$/, /^chr2$/, /^chr10/]);
});

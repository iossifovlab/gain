import { expect, test, type Page } from '@playwright/test';

import { COVERAGE_RESOURCE, FIXTURE_GRR } from '../fixtures';
import { infoPageUrl, serveGrr } from '../serving';

/**
 * The Chromosome lengths table, driven in a browser (gain#1579).
 *
 * `core/tests/small/genomic_resources/test_info_page_chrom_lengths.py`
 * pins the markup: the columns the stored file yields, the marker on a
 * contig the genome does not list, which cells carry a sort key. What
 * it cannot pin is what the sorter does with a column that has a gap
 * in it, which is what this file is for.
 *
 * The Coverage fixture is the partial-overlap case: a tabix score over
 * chr1, chr2 and chr10 labelled with a genome listing chr1 (100) and
 * chr2 (50), so the genome column has two lengths and one empty cell.
 */

/** The lengths table, addressed through the heading that introduces it. */
function lengthsTable(page: Page) {
  return page
    .getByRole('heading', { name: 'Chromosome lengths', exact: true })
    .locator('xpath=following::table[1]');
}

function columnHeader(page: Page, name: string) {
  return lengthsTable(page).getByRole('columnheader', { name });
}

test.beforeEach(async ({ page }) => {
  await serveGrr(page, FIXTURE_GRR);
  await page.goto(infoPageUrl(COVERAGE_RESOURCE));
});

test('one row per contig, one column per stored source', async ({ page }) => {
  const table = lengthsTable(page);

  /* By role and name, not by text: the sorter appends its indicator
   * glyph to every header it wires up. That it did wire them up is the
   * `sortable` count -- the class is added by the script and by nothing
   * else. */
  for (const name of ['Chromosome', 'reference_genome', 'tabix_estimate']) {
    await expect(columnHeader(page, name)).toHaveCount(1);
  }
  await expect(table.locator('thead th.sortable')).toHaveCount(3);
  await expect(table.locator('tbody tr')).toHaveCount(3);
  await expect(table.locator('tbody td:nth-child(3)'))
    .toHaveText(['12', '12', '3']);
});

test('the contig the genome does not list is marked, and only it',
  async ({ page }) => {
    const table = lengthsTable(page);
    const marked = table.locator('tbody tr.unlisted-contig');

    await expect(marked).toHaveCount(1);
    await expect(marked.locator('td:first-child')).toContainText('chr10');
    await expect(marked.locator('.unlisted-marker')).toBeVisible();
    await expect(marked.locator('td:nth-child(2)')).toHaveText('');
  });

test('the empty genome cell sinks to the bottom in either direction',
  async ({ page }) => {
    const table = lengthsTable(page);
    const header = columnHeader(page, 'reference_genome');
    const genome = table.locator('tbody td:nth-child(2)');

    /* Scramble first: the rendered order already has the gap last, so
     * an ascending click that did nothing would pass below. */
    await columnHeader(page, 'tabix_estimate').click();
    await expect(genome).toHaveText(['', '100', '50']);

    await header.click();

    await expect(header).toHaveAttribute('aria-sort', 'ascending');
    await expect(genome).toHaveText(['50', '100', '']);

    await header.click();

    await expect(header).toHaveAttribute('aria-sort', 'descending');
    await expect(genome).toHaveText(['100', '50', '']);
  });

test('the marker does not leak into the contig sort', async ({ page }) => {
  const table = lengthsTable(page);
  const header = columnHeader(page, 'Chromosome');

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

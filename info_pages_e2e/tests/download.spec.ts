import * as fs from 'fs';

import { expect, test, type Page } from '@playwright/test';

import {
  FIXTURE_LABELLED_GRR,
  LABELLED_LIST_LABEL,
  LABELLED_NUMBER_LABEL,
  LABELLED_ONE_ITEM_RESOURCE_ID,
  LABELLED_QUOTED_SUMMARY,
  LABELLED_SCALAR_LABEL,
  LABELLED_SCORE_TYPE,
  LABELLED_SEARCH_TERM,
  LABELLED_VERSIONED_RESOURCE_ID,
  LABELLED_VERSIONED_RESOURCE_VERSION,
} from '../fixtures';
import { GRR_ORIGIN, indexPageUrl, serveGrr } from '../serving';

/** How many resources the labelled GRR holds. */
const LABELLED_RESOURCE_COUNT = 4;

/** The fields of one record of the download, in any order. */
const RECORD_FIELDS = [
  'id', 'labels', 'size_bytes', 'summary', 'type', 'version',
];

/** An ISO 8601 time in UTC, as `Date.prototype.toISOString` writes it. */
const ISO_8601_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

/** Open the labelled GRR's index page and wait for its search index. */
async function openLabelledIndex(page: Page): Promise<void> {
  await serveGrr(page, FIXTURE_LABELLED_GRR);
  await page.goto(indexPageUrl());
  await expect(page.locator('#status')).toHaveText(
    `${LABELLED_RESOURCE_COUNT} resources`);
}

/** Type a term into the search box and run the search. */
async function search(page: Page, term: string): Promise<void> {
  await page.locator('#search-field').fill(term);
  await page.locator('#search-field').press('Enter');
}

/** The Download button. */
function downloadButton(page: Page) {
  return page.locator('#download-btn');
}

/** Choose Download -> JSON and return the name and the parsed file. */
async function downloadJson(
  page: Page,
): Promise<{ name: string; body: any }> {
  await downloadButton(page).click();
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('menuitem', { name: 'JSON' }).click(),
  ]);
  const file = await download.path();
  return {
    name: download.suggestedFilename(),
    body: JSON.parse(fs.readFileSync(file, 'utf8')),
  };
}

test('Download -> JSON writes the rows the table shows, in its order', async ({
  page,
}) => {
  await openLabelledIndex(page);
  await expect(downloadButton(page)).toHaveText(
    `Download ${LABELLED_RESOURCE_COUNT} ▾`);

  await search(page, LABELLED_SEARCH_TERM);
  await page.locator('#type-filter').selectOption(LABELLED_SCORE_TYPE);
  /* Two clicks sort the ID column descending: an order that differs from
   * the default one, so the file must follow the table to match it. */
  await page.locator('#id-col-header').click();
  await page.locator('#id-col-header').click();
  const versionedFullId =
    `${LABELLED_VERSIONED_RESOURCE_ID}(${LABELLED_VERSIONED_RESOURCE_VERSION})`;
  const rows = page.locator('#resource-table tbody tr:visible');
  await expect(rows.locator('td.id-cell a')).toHaveText([
    versionedFullId, LABELLED_ONE_ITEM_RESOURCE_ID,
  ]);
  await expect(downloadButton(page)).toHaveText('Download 2 ▾');

  const { name, body } = await downloadJson(page);

  const shownBytes = await rows.evaluateAll(
    (trs) => trs.map((tr) => Number((tr as HTMLElement).dataset.bytes)));
  expect(body.repository).toBe(GRR_ORIGIN);
  expect(body.search).toEqual({
    q: LABELLED_SEARCH_TERM, type: LABELLED_SCORE_TYPE, folder: '',
  });
  expect(body.generated).toMatch(ISO_8601_UTC);
  expect(body.resources.map((r: any) => [r.id, r.version])).toEqual([
    [LABELLED_VERSIONED_RESOURCE_ID, LABELLED_VERSIONED_RESOURCE_VERSION],
    [LABELLED_ONE_ITEM_RESOURCE_ID, '0'],
  ]);
  for (const record of body.resources) {
    expect(Object.keys(record).sort()).toEqual(RECORD_FIELDS);
    expect(record.type).toBe(LABELLED_SCORE_TYPE);
  }
  expect(body.resources.map((r: any) => r.size_bytes)).toEqual(shownBytes);
  expect(shownBytes.every((n) => Number.isInteger(n) && n > 0)).toBe(true);
  const [versioned, oneItem] = body.resources;
  expect(versioned.summary).toBe(LABELLED_QUOTED_SUMMARY);
  expect(versioned.labels).toEqual({
    [LABELLED_LIST_LABEL]: ['RNA', 'ATAC'],
    [LABELLED_SCALAR_LABEL]: 'basal ganglia',
    [LABELLED_NUMBER_LABEL]: '12',
  });
  expect(oneItem.labels[LABELLED_LIST_LABEL]).toEqual(['RNA']);
  expect(name).toBe(
    `all-${LABELLED_SEARCH_TERM}-${body.generated.slice(0, 10)}.json`);
});

test('Download -> JSON writes an empty list label as an empty array', async ({
  page,
}) => {
  await openLabelledIndex(page);

  const { body } = await downloadJson(page);

  expect(body.resources).toHaveLength(LABELLED_RESOURCE_COUNT);
  expect(body.search).toEqual({ q: '', type: '', folder: '' });
  const labelled = body.resources.filter(
    (r: any) => Array.isArray(r.labels[LABELLED_LIST_LABEL])
      && r.labels[LABELLED_LIST_LABEL].length === 0);
  expect(labelled).toHaveLength(1);
  const unlabelled = body.resources.filter(
    (r: any) => r.type === 'genome');
  expect(unlabelled.map((r: any) => r.labels)).toEqual([{}]);
});

test('a long search term with unsafe characters gives a safe file name', async ({
  page,
}) => {
  await openLabelledIndex(page);
  const term =
    'cortex/"basal ganglia" <RNA|ATAC>: what?*' + ' atlas'.repeat(10);

  await search(page, term);
  await expect(page.locator('#search-field')).toHaveValue(term);
  const { name, body } = await downloadJson(page);

  expect(body.search.q).toBe(term);
  /* The stem is cut to 64 characters, so the whole name is 80. */
  expect(name).toBe(
    'all-cortex-basal-ganglia-RNA-ATAC-what-atlas-atlas-atlas-atlas-a'
    + `-${body.generated.slice(0, 10)}.json`);
  expect(name).toHaveLength(80);
});

test('with the search module blocked there is no Download button', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_LABELLED_GRR);
  await page.route(
    (url) => url.href.includes('sqlite-wasm'),
    (route) => route.abort(),
  );

  /* `load` fires after every module script has run or failed, so the
   * module that would add the button has had its chance. */
  await page.goto(indexPageUrl(), { waitUntil: 'load' });
  /* The button is made after async steps, which `load` does not wait
   * for. Wait until the page makes no more requests. */
  await page.waitForLoadState('networkidle');

  /* The block is what kept the button away: the module that imports
   * sqlite-wasm never ran, so it set no `window.dbLoaded`. Without the
   * block, this check fails. */
  expect(await page.evaluate(() => typeof (window as any).dbLoaded)).toBe(
    'undefined');
  await expect(page.locator('#resource-table tbody tr')).toHaveCount(
    LABELLED_RESOURCE_COUNT);
  await expect(downloadButton(page)).toHaveCount(0);
});

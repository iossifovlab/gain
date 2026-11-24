import * as fs from 'fs';

import { expect, test, type Page } from '@playwright/test';

import {
  FIXTURE_GRR,
  FIXTURE_LABELLED_GRR,
  LABELLED_EMPTY_LIST_RESOURCE_ID,
  LABELLED_FACETS,
  LABELLED_GENOME_TYPE,
  LABELLED_ONE_ITEM_RESOURCE_ID,
  LABELLED_QUOTED_TISSUE,
  LABELLED_SEARCH_TERM,
  LABELLED_VERSIONED_RESOURCE_ID,
  LABELLED_VERSIONED_RESOURCE_VERSION,
} from '../fixtures';
import { indexPageUrl, serveGrr } from '../serving';

/*
 * The extended search of the root page (ADR 0045, gain#2501), on the
 * labelled GRR. Its labels:
 *
 *   atlas/expression  tissue "basal ganglia", modality [RNA, ATAC], donors 12
 *   atlas/cortex      tissue "cortex",        modality [RNA],       donors 3
 *   atlas/liver       tissue "liver's lobe",  modality [],          donors 0
 *   genomes/reference no labels
 */

/** How many resources the labelled GRR holds. */
const LABELLED_RESOURCE_COUNT = 4;

/** Open the labelled GRR's index page at `hash` and wait for `count`. */
async function openLabelledIndex(
  page: Page, hash = '', count = LABELLED_RESOURCE_COUNT,
): Promise<void> {
  await serveGrr(page, FIXTURE_LABELLED_GRR);
  await page.goto(indexPageUrl() + hash);
  await expect(page.locator('#status')).toHaveText(`${count} resources`);
}

function extendedSearchButton(page: Page) {
  return page.locator('#extended-search-btn');
}

function dialog(page: Page) {
  return page.locator('#extended-search-dialog');
}

/** One facet of the dialog, by its label key. */
function facet(page: Page, label: string) {
  return dialog(page).locator(`.facet[data-label="${label}"]`);
}

/** The values a facet shows, with their counts, in their order. */
async function facetValues(
  page: Page, label: string,
): Promise<[string, string][]> {
  return facet(page, label).locator('.facet-value').evaluateAll(
    (rows) => rows.map((row) => [
      row.querySelector('.facet-value-name')!.textContent!,
      row.querySelector('.facet-value-count')!.textContent!,
    ] as [string, string]));
}

/** Tick or untick one value of a facet in the dialog. */
async function tick(page: Page, label: string, value: string): Promise<void> {
  await facet(page, label).getByRole('checkbox', { name: value }).click();
}

/** The full id of the versioned score, as the table lists it. */
const VERSIONED_FULL_ID =
  `${LABELLED_VERSIONED_RESOURCE_ID}(${LABELLED_VERSIONED_RESOURCE_VERSION})`;

/** The full ids that the table shows, in its order. */
function visibleResourceIds(page: Page) {
  return page.locator('#resource-table tbody tr:visible td.id-cell a');
}

/** The resource names that the tree lists. */
function treeResourceNames(page: Page) {
  return page.locator('#hierarchical-list .hv-resource .hv-name');
}

/** The query part of the address, decoded. */
function addressQuery(page: Page): string {
  const hash = new URL(page.url()).hash;
  const cut = hash.indexOf('?');
  return cut < 0 ? '' : decodeURIComponent(hash.slice(cut + 1));
}

function applyButton(page: Page) {
  return dialog(page).locator('.es-apply');
}

function chips(page: Page) {
  return page.locator('#search-chips .search-chip');
}

function badge(page: Page) {
  return page.locator('#extended-search-badge');
}

async function openDialog(page: Page): Promise<void> {
  await extendedSearchButton(page).click();
  await expect(dialog(page)).toBeVisible();
}

test('the dialog lists the facets in file order with their counts', async ({
  page,
}) => {
  await openLabelledIndex(page);

  await openDialog(page);

  await expect(dialog(page).locator('.facet h4')).toHaveText(
    LABELLED_FACETS.map((f) => f.title));
  expect(await facetValues(page, 'tissue')).toEqual([
    ['basal ganglia', '1'], ['cortex', '1'], ["liver's lobe", '1'],
  ]);
  expect(await facetValues(page, 'modality')).toEqual([
    ['RNA', '2'], ['ATAC', '1'],
  ]);
  expect(await facetValues(page, 'donors')).toEqual([
    ['0', '1'], ['12', '1'], ['3', '1'],
  ]);
});

test('ticks in two facets apply as one filter, with chips, badge and address', async ({
  page,
}) => {
  await openLabelledIndex(page);
  await openDialog(page);

  await tick(page, 'modality', 'RNA');
  await tick(page, 'tissue', 'cortex');
  await expect(applyButton(page)).toHaveText('Show 1 resources');
  await applyButton(page).click();

  await expect(dialog(page)).toBeHidden();
  await expect(visibleResourceIds(page)).toHaveText(
    [LABELLED_ONE_ITEM_RESOURCE_ID]);
  await expect(page.locator('#status')).toHaveText('1 resources');
  await expect(chips(page)).toHaveText(['Tissue: cortex×', 'Modality: RNA×']);
  await expect(badge(page)).toHaveText('2');
  expect(addressQuery(page)).toBe('label.tissue=cortex&label.modality=RNA');
});

test('values of one facet are alternatives, and their count ignores the own ticks', async ({
  page,
}) => {
  await openLabelledIndex(page);
  await openDialog(page);

  await tick(page, 'tissue', 'cortex');
  await tick(page, 'tissue', 'basal ganglia');

  expect(await facetValues(page, 'tissue')).toEqual([
    ['basal ganglia', '1'], ['cortex', '1'], ["liver's lobe", '1'],
  ]);
  expect(await facetValues(page, 'modality')).toEqual([
    ['RNA', '2'], ['ATAC', '1'],
  ]);
  await expect(applyButton(page)).toHaveText('Show 2 resources');
  await applyButton(page).click();
  await expect(visibleResourceIds(page)).toHaveText(
    [LABELLED_ONE_ITEM_RESOURCE_ID, VERSIONED_FULL_ID]);
  expect(addressQuery(page)).toBe(
    'label.tissue=basal ganglia&label.tissue=cortex');
});

test('the counts cover the term', async ({ page }) => {
  await openLabelledIndex(page, `#?q=${LABELLED_SEARCH_TERM}`, 3);

  await openDialog(page);

  expect(await facetValues(page, 'tissue')).toEqual([
    ['basal ganglia', '1'], ['cortex', '1'],
  ]);
});

test('the type holds together with the ticks, in the filter and the counts', async ({
  page,
}) => {
  await openLabelledIndex(
    page, `#?type=${LABELLED_GENOME_TYPE}&label.modality=RNA`, 0);

  await openDialog(page);

  expect(await facetValues(page, 'tissue')).toEqual([]);
  expect(await facetValues(page, 'modality')).toEqual([['RNA', '0']]);
  await expect(applyButton(page)).toHaveText('Show 0 resources');
});

test('Cancel drops the draft', async ({ page }) => {
  await openLabelledIndex(page);
  await openDialog(page);
  await tick(page, 'modality', 'ATAC');

  await dialog(page).getByRole('button', { name: 'Cancel' }).click();

  await expect(dialog(page)).toBeHidden();
  await expect(page.locator('#status')).toHaveText('4 resources');
  await expect(badge(page)).toBeHidden();
  await openDialog(page);
  await expect(
    facet(page, 'modality').getByRole('checkbox', { name: 'ATAC' }),
  ).not.toBeChecked();
});

test('Clear all empties the draft, and the applied ticks stay until applied', async ({
  page,
}) => {
  await openLabelledIndex(page, '#?label.modality=RNA', 2);
  await openDialog(page);

  await dialog(page).getByRole('button', { name: 'Clear all' }).click();

  await expect(dialog(page).getByRole('checkbox', { checked: true }))
    .toHaveCount(0);
  await expect(applyButton(page)).toHaveText('Show 4 resources');
  await expect(page.locator('#status')).toHaveText('2 resources');
  await expect(badge(page)).toHaveText('1');

  await applyButton(page).click();

  await expect(page.locator('#status')).toHaveText('4 resources');
  await expect(chips(page)).toHaveCount(0);
  await expect(badge(page)).toBeHidden();
  expect(addressQuery(page)).toBe('');
});

test('a value whose count drops to 0 is hidden', async ({ page }) => {
  await openLabelledIndex(page);
  await openDialog(page);

  await tick(page, 'modality', 'RNA');

  expect(await facetValues(page, 'tissue')).toEqual([
    ['basal ganglia', '1'], ['cortex', '1'],
  ]);
  expect(await facetValues(page, 'donors')).toEqual([
    ['12', '1'], ['3', '1'],
  ]);
  await expect(applyButton(page)).toHaveText('Show 2 resources');
});

test('a ticked value stays at count 0', async ({ page }) => {
  await openLabelledIndex(
    page, '#?label.tissue=cortex&label.modality=ATAC', 0);

  await openDialog(page);

  expect(await facetValues(page, 'tissue')).toEqual([
    ['basal ganglia', '1'], ['cortex', '0'],
  ]);
  expect(await facetValues(page, 'modality')).toEqual([
    ['RNA', '1'], ['ATAC', '0'],
  ]);
  await expect(applyButton(page)).toHaveText('Show 0 resources');
});

test('removing a chip filters again', async ({ page }) => {
  await openLabelledIndex(page, '#?label.tissue=cortex&label.modality=RNA', 1);

  await page.getByRole('button', { name: 'Remove Tissue: cortex' }).click();

  await expect(visibleResourceIds(page)).toHaveText(
    [LABELLED_ONE_ITEM_RESOURCE_ID, VERSIONED_FULL_ID]);
  await expect(chips(page)).toHaveText(['Modality: RNA×']);
  await expect(badge(page)).toHaveText('1');
  expect(addressQuery(page)).toBe('label.modality=RNA');
});

test('a reload restores the ticks', async ({ page }) => {
  await openLabelledIndex(page);
  await openDialog(page);
  await tick(page, 'modality', 'ATAC');
  await applyButton(page).click();
  await expect(page.locator('#status')).toHaveText('1 resources');

  await page.reload();

  await expect(page.locator('#status')).toHaveText('1 resources');
  await expect(visibleResourceIds(page)).toHaveText([VERSIONED_FULL_ID]);
  await expect(chips(page)).toHaveText(['Modality: ATAC×']);
});

test('Back from two ticks to one tick with the same term filters again', async ({
  page,
}) => {
  await openLabelledIndex(
    page, `#?q=${LABELLED_SEARCH_TERM}&label.modality=RNA`, 2);
  await openDialog(page);
  await tick(page, 'modality', 'ATAC');
  await tick(page, 'tissue', 'basal ganglia');
  await applyButton(page).click();
  await expect(page.locator('#status')).toHaveText('1 resources');

  await page.goBack();

  await expect(page.locator('#status')).toHaveText('2 resources');
  await expect(visibleResourceIds(page)).toHaveText(
    [LABELLED_ONE_ITEM_RESOURCE_ID, VERSIONED_FULL_ID]);
  await expect(chips(page)).toHaveText(['Modality: RNA×']);
  expect(addressQuery(page)).toBe(
    `q=${LABELLED_SEARCH_TERM}&label.modality=RNA`);
});

test('table, then tree, then table keeps the ticks and the filter', async ({
  page,
}) => {
  await openLabelledIndex(page, '#?label.tissue=cortex', 1);

  await page.locator('#hierarchical-view-btn').click();
  await expect(treeResourceNames(page)).toHaveCount(0);
  await expect(page.locator('#hierarchical-list .hv-folder .hv-name'))
    .toHaveText(['atlas']);
  expect(addressQuery(page)).toBe('label.tissue=cortex');
  await page.locator('#table-view-btn').click();

  expect(addressQuery(page)).toBe('label.tissue=cortex');
  await expect(visibleResourceIds(page)).toHaveText(
    [LABELLED_ONE_ITEM_RESOURCE_ID]);
});

test('in a folder of the tree, the ticks prune it to the matches', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_LABELLED_GRR);

  await page.goto(`${indexPageUrl()}#/atlas?label.modality=RNA`);

  await expect(treeResourceNames(page)).toHaveText(['cortex', `expression(${LABELLED_VERSIONED_RESOURCE_VERSION})`]);
});

test('a term that FTS rejects, with a tick, lists every resource with the value', async ({
  page,
}) => {
  await openLabelledIndex(page, '#?q=%22&label.modality=RNA', 2);

  await expect(page.locator('#status-error')).not.toBeEmpty();
  await expect(visibleResourceIds(page)).toHaveText(
    [LABELLED_ONE_ITEM_RESOURCE_ID, VERSIONED_FULL_ID]);
});

test('a ticked value that contains a quote filters correctly', async ({
  page,
}) => {
  await openLabelledIndex(page);
  await openDialog(page);

  await tick(page, 'tissue', LABELLED_QUOTED_TISSUE);
  await applyButton(page).click();

  await expect(visibleResourceIds(page)).toHaveText(
    [LABELLED_EMPTY_LIST_RESOURCE_ID]);
  await expect(page.locator('#status-error')).toBeEmpty();
  expect(addressQuery(page)).toBe(`label.tissue=${LABELLED_QUOTED_TISSUE}`);
});

test('a GRR without search.yaml shows no Extended search button', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_GRR);
  await page.goto(indexPageUrl());
  await expect(page.locator('#status')).toHaveText('2 resources');

  await expect(extendedSearchButton(page)).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Extended search/ }))
    .toHaveCount(0);
});

/** Choose Download -> JSON and return the parsed file. */
async function downloadJson(page: Page): Promise<any> {
  await page.locator('#download-btn').click();
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('menuitem', { name: 'JSON' }).click(),
  ]);
  return JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
}

test('with ticks applied, Download -> JSON holds only the matches', async ({
  page,
}) => {
  await openLabelledIndex(page, '#?label.modality=RNA', 2);

  const table = await downloadJson(page);
  await page.locator('#hierarchical-view-btn').click();
  await page.locator('#hierarchical-list .hv-folder .hv-name')
    .getByText('atlas', { exact: true }).click();
  await expect(page.locator('#download-btn')).toHaveText('Download 2 ▾');
  const tree = await downloadJson(page);

  expect(table.resources.map((r: any) => r.id)).toEqual(
    [LABELLED_ONE_ITEM_RESOURCE_ID, LABELLED_VERSIONED_RESOURCE_ID]);
  expect(tree.search.folder).toBe('atlas');
  expect(tree.resources.map((r: any) => r.id)).toEqual(
    [LABELLED_ONE_ITEM_RESOURCE_ID, LABELLED_VERSIONED_RESOURCE_ID]);
});

import * as fs from 'fs';

import { expect, test, type Page } from '@playwright/test';

import {
  BROWSE_ID_ONLY_RESOURCE_ID,
  BROWSE_RESOURCE_COUNT,
  FIXTURE_BROWSE_GRR,
  FIXTURE_LABELLED_GRR,
  FIXTURE_NESTED_GRR,
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

/** Choose Download -> `format` and return the name and the text of the file. */
async function downloadFile(
  page: Page, format: 'JSON' | 'CSV',
): Promise<{ name: string; text: string }> {
  await downloadButton(page).click();
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('menuitem', { name: format }).click(),
  ]);
  const file = await download.path();
  return {
    name: download.suggestedFilename(),
    text: fs.readFileSync(file, 'utf8'),
  };
}

/** Choose Download -> JSON and return the name and the parsed file. */
async function downloadJson(
  page: Page,
): Promise<{ name: string; body: any }> {
  const { name, text } = await downloadFile(page, 'JSON');
  return { name, body: JSON.parse(text) };
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
    labels: {},
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
  expect(body.search).toEqual(
    { q: '', type: '', folder: '', labels: {} });
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

/**
 * Split RFC 4180 text into records of cells. Each record must end in
 * CRLF. A quoted cell may hold a comma, a CRLF and a doubled quote.
 */
function parseCsv(text: string): string[][] {
  const records: string[][] = [];
  let record: string[] = [];
  let i = 0;
  while (i < text.length) {
    let cell = '';
    if (text[i] === '"') {
      i += 1;
      while (!(text[i] === '"' && text[i + 1] !== '"')) {
        if (i >= text.length) throw new Error('unclosed quote');
        cell += text[i];
        i += text[i] === '"' ? 2 : 1;
      }
      i += 1;
    } else {
      while (i < text.length && text[i] !== ',' && text[i] !== '\r'
        && text[i] !== '\n') {
        cell += text[i];
        i += 1;
      }
    }
    record.push(cell);
    if (text[i] === ',') {
      i += 1;
    } else if (text.startsWith('\r\n', i)) {
      records.push(record);
      record = [];
      i += 2;
    } else {
      throw new Error(`no CRLF after a record, at ${i}`);
    }
  }
  if (record.length) throw new Error('the last record has no CRLF');
  return records;
}

test('Download -> CSV writes the records of the JSON download', async ({
  page,
}) => {
  await openLabelledIndex(page);
  await search(page, LABELLED_SEARCH_TERM);
  await expect(downloadButton(page)).toHaveText('Download 3 ▾');
  const { body } = await downloadJson(page);

  const { name, text } = await downloadFile(page, 'CSV');

  const records = parseCsv(text);
  expect(records[0]).toEqual(
    ['id', 'type', 'version', 'size_bytes', 'summary', 'labels']);
  expect(records.slice(1)).toEqual(body.resources.map((r: any) => [
    r.id, r.type, r.version, String(r.size_bytes), r.summary,
    JSON.stringify(r.labels),
  ]));
  expect(text.startsWith(
    'id,type,version,size_bytes,summary,labels\r\n')).toBe(true);
  expect(text.endsWith('\r\n')).toBe(true);
  expect(text).toContain(
    ',"Expression atlas, with the ""basal"" set.",');
  expect(text).toContain(
    ',"{""modality"":[""RNA"",""ATAC""],""tissue"":""basal ganglia"",'
    + '""donors"":""12""}"\r\n');
  expect(name).toMatch(
    new RegExp(`^all-${LABELLED_SEARCH_TERM}-\\d{4}-\\d{2}-\\d{2}\\.csv$`));
});

test('Download -> CSV quotes a cell that holds a line break', async ({
  page,
}) => {
  await openLabelledIndex(page);
  await search(page, LABELLED_SEARCH_TERM);
  await expect(downloadButton(page)).toHaveText('Download 3 ▾');
  /* No fixture summary holds a line break, so give the first record one. */
  const summary = 'First line\nsecond line';
  await page.evaluate((text) => {
    const w = window as any;
    w.rowData[w.currentIds[0]].summary = text;
  }, summary);

  const { text } = await downloadFile(page, 'CSV');

  expect(text).toContain(',"First line\nsecond line",');
  expect(parseCsv(text)[1][4]).toBe(summary);
});

/* ---- The download of the tree view (gain#2490) ---- */

/**
 * A term of the browse GRR that matches the two resources of
 * `hg38/scores/conservation` and `Zoo/alpha`, and no other resource.
 * At `hg38/scores` it so has matches in a sub-folder, a match outside
 * the folder, and a resource of the folder that it does not match.
 */
const SCATTERED_TERM = 'a';

/** The folder that the tree tests browse, and its sub-folder. */
const SCORES_FOLDER = ['hg38', 'scores'];
const SCORES_PATH = SCORES_FOLDER.join('/');
const CONSERVATION_FOLDER = 'conservation';

/** The two resources of `hg38/scores/conservation`, in ID order. */
const CONSERVATION_RESOURCE_IDS = [
  'hg38/scores/conservation/phastcons', BROWSE_ID_ONLY_RESOURCE_ID,
];

/** The names of the folders that the tree lists. */
function folderNames(page: Page) {
  return page.locator('#hierarchical-list .hv-folder .hv-name');
}

/**
 * Open the browse GRR's index page at `hash` and wait for its search
 * index. `resources` is the count of the search across the whole
 * repository, which the hidden `#status` reports.
 */
async function openBrowseIndex(
  page: Page, hash: string, resources: number,
): Promise<void> {
  await serveGrr(page, FIXTURE_BROWSE_GRR);
  await page.goto(indexPageUrl() + hash);
  await expect(page.locator('#status')).toHaveText(
    `${resources} resources`);
}

test('in the tree, Download -> JSON writes the matches under the folder', async ({
  page,
}) => {
  await openBrowseIndex(
    page, `#/${SCORES_PATH}?q=${SCATTERED_TERM}`, 3);
  await expect(downloadButton(page)).toHaveText('Download 2 ▾');

  const { name, body } = await downloadJson(page);

  expect(body.search).toEqual({
    q: SCATTERED_TERM, type: '', folder: SCORES_PATH, labels: {},
  });
  expect(body.resources.map((r: any) => r.id)).toEqual(
    CONSERVATION_RESOURCE_IDS);
  expect(name).toBe(
    `${SCORES_FOLDER.join('-')}-${SCATTERED_TERM}-`
    + `${body.generated.slice(0, 10)}.json`);
});

test('in the tree with no search, the download holds the whole folder', async ({
  page,
}) => {
  await openBrowseIndex(
    page, `#/${SCORES_PATH}`, BROWSE_RESOURCE_COUNT);
  await expect(downloadButton(page)).toHaveText('Download 3 ▾');

  const whole = await downloadJson(page);
  await folderNames(page)
    .getByText(CONSERVATION_FOLDER, { exact: true }).click();
  await expect(downloadButton(page)).toHaveText('Download 2 ▾');
  const sub = await downloadJson(page);

  expect(whole.body.search).toEqual(
    { q: '', type: '', folder: SCORES_PATH, labels: {} });
  expect(whole.body.resources.map((r: any) => r.id)).toEqual([
    ...CONSERVATION_RESOURCE_IDS, 'hg38/scores/coverage',
  ]);
  expect(sub.body.search.folder).toBe(
    `${SCORES_PATH}/${CONSERVATION_FOLDER}`);
  expect(sub.body.resources.map((r: any) => r.id)).toEqual(
    CONSERVATION_RESOURCE_IDS);
});

test('after a search in the tree, the tree still prunes to the matches', async ({
  page,
}) => {
  await openBrowseIndex(
    page, `#/${SCORES_PATH}`, BROWSE_RESOURCE_COUNT);
  await expect(downloadButton(page)).toHaveText('Download 3 ▾');

  await search(page, SCATTERED_TERM);

  await expect(downloadButton(page)).toHaveText('Download 2 ▾');
  await expect(folderNames(page))
    .toHaveText([CONSERVATION_FOLDER]);
  await expect(page.locator('#hierarchical-list .hv-resource'))
    .toHaveCount(0);
  await expect(page.locator('#hv-status')).toHaveText(
    `2 of 3 resources under ${SCORES_PATH} match`);
});

test('a second onResults listener keeps the tree pruning', async ({
  page,
}) => {
  await openBrowseIndex(
    page, `#/${SCORES_PATH}`, BROWSE_RESOURCE_COUNT);
  /* A later consumer of the search results, next to the tree's own one.
   * A single listener slot would drop the tree's listener here. */
  await page.evaluate(() => {
    const w = window as any;
    w.secondListenerTerms = [];
    w.grrSearch.onResults(({ search }: any) => {
      w.secondListenerTerms.push(search.q);
    });
  });

  await search(page, SCATTERED_TERM);

  await expect(folderNames(page))
    .toHaveText([CONSERVATION_FOLDER]);
  await expect(page.locator('#hv-status')).toHaveText(
    `2 of 3 resources under ${SCORES_PATH} match`);
  expect(await page.evaluate(
    () => (window as any).secondListenerTerms)).toContain(SCATTERED_TERM);
});

test('back in the table, the download holds every match again', async ({
  page,
}) => {
  await openBrowseIndex(
    page, `#/${SCORES_PATH}?q=${SCATTERED_TERM}`, 3);
  await expect(downloadButton(page)).toHaveText('Download 2 ▾');

  await page.locator('#table-view-btn').click();

  await expect(downloadButton(page)).toHaveText('Download 3 ▾');
  const { name, body } = await downloadJson(page);
  expect(body.search).toEqual(
    { q: SCATTERED_TERM, type: '', folder: '', labels: {} });
  expect(body.resources).toHaveLength(3);
  expect(body.resources.map((r: any) => r.id)).toEqual(
    expect.arrayContaining(CONSERVATION_RESOURCE_IDS));
  expect(name).toBe(
    `all-${SCATTERED_TERM}-${body.generated.slice(0, 10)}.json`);
});

/* ---- The ticks of the extended search in the download (gain#2504) ---- */

/** Tick values in the dialog of the extended search, then apply them. */
async function applyTicks(
  page: Page, ticks: [string, string][],
): Promise<void> {
  await page.locator('#extended-search-btn').click();
  const dialog = page.locator('#extended-search-dialog');
  await expect(dialog).toBeVisible();
  for (const [label, value] of ticks) {
    await dialog.locator(`.facet[data-label="${label}"]`)
      .getByRole('checkbox', { name: value }).click();
  }
  await dialog.locator('.es-apply').click();
  await expect(dialog).toBeHidden();
}

/* The ticks, in an order that is not the order of the address: the
 * facet `tissue` comes before `modality`, and `basal ganglia` before
 * `cortex`. */
const UNORDERED_TICKS: [string, string][] = [
  ['modality', 'RNA'], ['tissue', 'cortex'], ['tissue', 'basal ganglia'],
];

test('in the table with ticks, Download -> JSON writes the matches and the ticks', async ({
  page,
}) => {
  await openLabelledIndex(page);

  await applyTicks(page, UNORDERED_TICKS);
  await expect(downloadButton(page)).toHaveText('Download 2 ▾');
  const shownIds = await page
    .locator('#resource-table tbody tr:visible td.id-cell a')
    .allTextContents();
  const { body } = await downloadJson(page);

  expect(body.resources.map((r: any) => r.version === '0'
    ? r.id : `${r.id}(${r.version})`)).toEqual(shownIds);
  expect(shownIds).toHaveLength(2);
  expect(body.search).toEqual({
    q: '', type: '', folder: '',
    labels: { tissue: ['basal ganglia', 'cortex'], modality: ['RNA'] },
  });
  expect(Object.keys(body.search.labels)).toEqual(['tissue', 'modality']);
});

test('with no ticks, the search of Download -> JSON has empty labels', async ({
  page,
}) => {
  await openLabelledIndex(page);
  await search(page, LABELLED_SEARCH_TERM);
  await expect(downloadButton(page)).toHaveText('Download 3 ▾');

  const { body } = await downloadJson(page);

  expect(body.search.labels).toEqual({});
  expect(body.resources).toHaveLength(3);
});

test('at a folder with a tick in the address, Download -> JSON writes the matches under the folder', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_NESTED_GRR);
  await page.goto(`${indexPageUrl()}#/brain?label.modality=RNA`);
  await expect(downloadButton(page)).toHaveText('Download 2 ▾');

  const { body } = await downloadJson(page);

  expect(body.resources.map((r: any) => r.id)).toEqual([
    'brain/cortex/layer1', 'brain/cortex/layer2',
  ]);
  expect(body.search).toEqual({
    q: '', type: '', folder: 'brain', labels: { modality: ['RNA'] },
  });
});

test('with ticks, Download -> CSV writes the records of the JSON download', async ({
  page,
}) => {
  await openLabelledIndex(page);
  await applyTicks(page, UNORDERED_TICKS);
  await expect(downloadButton(page)).toHaveText('Download 2 ▾');
  const { body } = await downloadJson(page);

  const { text } = await downloadFile(page, 'CSV');

  const records = parseCsv(text).slice(1);
  expect(records).toHaveLength(2);
  expect(records).toEqual(body.resources.map((r: any) => [
    r.id, r.type, r.version, String(r.size_bytes), r.summary,
    JSON.stringify(r.labels),
  ]));
});

import { expect, test, type Page } from '@playwright/test';

import {
  BROWSE_ID_ONLY_RESOURCE_ID,
  BROWSE_ID_ONLY_TERM,
  BROWSE_RESOURCE_COUNT,
  BROWSE_SUMMARY_ONLY_RESOURCE_ID,
  BROWSE_SUMMARY_ONLY_TERM,
  BROWSE_TOP_LEVEL_FOLDERS,
  COVERAGE_RESOURCE,
  FIXTURE_BROWSE_GRR,
  FIXTURE_GRR,
} from '../fixtures';
import {
  indexPageUrl,
  indexPageUrlUnder,
  infoPageUrl,
  serveGrr,
  serveGrrsUnderSubPaths,
} from '../serving';

/**
 * The resource ids the visible rows name, in the order they are shown.
 *
 * A search does not remove rows, it hides them and rewrites the ones it
 * keeps, so counting `tbody tr` counts the whole repository however
 * narrow the search was. Only the `:visible` filter reads what is on the
 * screen.
 */
function visibleResourceIds(page: Page) {
  return page.locator('#resource-table tbody tr:visible td.id-cell a');
}

/**
 * Open the browse GRR's index page and wait for its search to be usable.
 *
 * The `#status` wait is a synchronization point, not decoration: it is
 * the only signal that the page has finished deserializing the FTS
 * database, and until it appears a search types into a box whose handler
 * is still awaiting a promise. Every cheaper-looking signal is already
 * true on arrival -- the rows are server-rendered, and the search box is
 * shown synchronously whether or not the database ever loads.
 */
async function openBrowseIndex(page: Page, hash = ''): Promise<void> {
  await serveGrr(page, FIXTURE_BROWSE_GRR);
  await page.goto(indexPageUrl() + hash);
  /* Read as text, never as visibility. The hierarchical view hides this
   * element, so an address naming the tree would make a `toBeVisible`
   * wait hang forever on a page that had loaded perfectly. The status is
   * written with jQuery's `.text()`, which does not care that the
   * element is hidden, so the signal survives being out of sight. */
  await expect(page.locator('#status')).toHaveText(
    `${BROWSE_RESOURCE_COUNT} resources`,
  );
}

/** Type a term into the search box and run the search. */
async function search(page: Page, term: string): Promise<void> {
  await page.locator('#search-field').fill(term);
  await page.locator('#search-field').press('Enter');
}

test('a repo-info published index page loads its search index offline', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_GRR);
  await page.goto(indexPageUrl());

  /* The Coverage GRR, which is the one published by the full `repo-info`
   * pass -- the browse GRR the other tests use is published by
   * `repo-index`. Both render this page through `build_index_info`, and
   * this is the only test that drives the `repo-info` route.
   *
   * `#status` is written by `UpdateStatus`, which is only ever reached
   * after the page has deserialized `.CONTENTS.sqlite3.gz` and queried
   * it -- so this is the whole offline load path in one DOM assertion.
   * Every *cheaper* signal lies: the rows are rendered by the template
   * and are already in the markup, and `#search-container` is shown
   * synchronously, outside the promise that awaits the database. A page
   * whose sqlite-wasm never loaded looks completely normal -- full
   * table, visible search box -- and only this line stays empty.
   *
   * Counted from the rendered rows rather than written as a literal: the
   * number is the Coverage GRR's business, and a resource added there
   * for a sorter test must not redden an index-page one. The two counts
   * reaching the same answer is itself the check -- the rows come from
   * the template, the status line from the search index. */
  const rows = await page.locator('#resource-table tbody tr').count();

  expect(rows).toBeGreaterThan(0);
  await expect(page.locator('#status')).toHaveText(`${rows} resources`);
});

test('a term matching only a summary filters the table to that resource', async ({
  page,
}) => {
  await openBrowseIndex(page);

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
  await openBrowseIndex(page);

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
  await openBrowseIndex(page);

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

  await openBrowseIndex(page);

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

/* ---- The browse view lives in the URL hash (#578) ---- */

/** The fragment the page is currently addressed by, `''` when bare. */
function hashOf(page: Page): string {
  return new URL(page.url()).hash;
}

/**
 * Assert which view the page is showing.
 *
 * Both wrappers, not just the expected one: the views are toggled by
 * `display`, and asserting only that the tree is visible would pass just
 * as well on a page showing the tree *and* the table at once -- which is
 * what a half-applied state looks like.
 */
async function expectView(
  page: Page, view: 'table' | 'hierarchical',
): Promise<void> {
  const hier = view === 'hierarchical';
  await expect(page.locator('#hierarchical-wrapper'))
    .toBeVisible({ visible: hier });
  await expect(page.locator('#table-wrapper')).toBeVisible({ visible: !hier });
}

test('choosing the hierarchical view puts it in the URL hash', async ({
  page,
}) => {
  await openBrowseIndex(page);

  await page.locator('#hierarchical-view-btn').click();

  await expect.poll(() => hashOf(page)).toBe('#/');
  await expectView(page, 'hierarchical');
});

test('choosing the table view again clears the fragment', async ({
  page,
}) => {
  await openBrowseIndex(page, '#/');
  await expectView(page, 'hierarchical');

  await page.locator('#table-view-btn').click();

  /* The way back out, under its own test rather than as a step in one
   * about something else. A `buildHash` that could not express the
   * default view -- returning `#/` whatever it was handed -- leaves this
   * button pushing the address it is trying to leave, and the table
   * never comes back. Nothing else here notices that on its own. */
  await expect.poll(() => hashOf(page)).toBe('');
  await expectView(page, 'table');
});

test('the toggle for the view already showing changes nothing', async ({
  page,
}) => {
  await openBrowseIndex(page, '#section-2');
  await expectView(page, 'table');
  const entriesBefore = await page.evaluate(() => history.length);

  await page.locator('#table-view-btn').click();

  /* Two things this button must not do when it is already the active
   * one. It must not stack a history entry that changes nothing on
   * screen -- Back would then appear not to work, which is the whole
   * reason the push is guarded. And it must not rewrite the address:
   * a fragment this page does not recognise belongs to whatever put it
   * there, and clicking a view is not consent to discard it.
   *
   * Both fail if the guard compares fragments instead of views: `''` is
   * not `'#section-2'`, so a build that asks "is the address already
   * exactly what I would write?" pushes, where one that asks "is this
   * view already showing?" does not. */
  await expect.poll(() => hashOf(page)).toBe('#section-2');
  expect(await page.evaluate(() => history.length)).toBe(entriesBefore);
});

test('the browser\'s Back leaves the hierarchical view again', async ({
  page,
}) => {
  await openBrowseIndex(page);
  await page.locator('#hierarchical-view-btn').click();
  await expectView(page, 'hierarchical');

  await page.goBack();

  /* Back over a fragment-only entry is a same-document navigation: the
   * page is not reloaded, so what restores the table is the `hashchange`
   * listener and nothing else. A build that pushed the entry but left
   * rendering to the click handler passes the test above and fails here. */
  await expect.poll(() => hashOf(page)).toBe('');
  await expectView(page, 'table');
});

test('the page opens in the view its address names', async ({ page }) => {
  await openBrowseIndex(page, '#/');

  await expectView(page, 'hierarchical');

  /* The tree is asserted to have *contents*, not merely to be the
   * visible half. Arriving already at `#/` is the one path that does not
   * pre-render the tree on the way in -- there would be no point, since
   * showing it renders it -- so this load leans entirely on that render
   * happening, and a wrapper assertion alone would be equally happy with
   * an empty one. */
  await expect(page.locator('#hierarchical-list .hv-folder .hv-name'))
    .toHaveText(BROWSE_TOP_LEVEL_FOLDERS);
});

test('the page opens in the table view with no fragment at all', async ({
  page,
}) => {
  await openBrowseIndex(page);

  /* On its own this is also what the markup says -- the tree wrapper
   * ships hidden -- so it would survive the feature being deleted. It
   * earns its place as the other half of a pair: with the test above it
   * pins the mapping in both directions, and a `parseHash` that answered
   * "hierarchical" to everything (or swapped the two) fails here while
   * passing there. Measured, not assumed. */
  await expectView(page, 'table');
});

test('Forward re-enters the hierarchical view', async ({ page }) => {
  await openBrowseIndex(page);
  await page.locator('#hierarchical-view-btn').click();
  await page.goBack();
  await expectView(page, 'table');

  await page.goForward();

  await expect.poll(() => hashOf(page)).toBe('#/');
  await expectView(page, 'hierarchical');
});

test('a fragment that names no browse state opens the table view', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));

  /* Not path-shaped, on purpose. A fragment *is* how things with no
   * interest in this page reach it -- a link into a section, a tracker's
   * leftovers -- so the parser has to treat a stranger as the default
   * rather than as a broken instruction. `#/something` is deliberately
   * not the example: that shape becomes meaningful when the folder path
   * lands (#579), and a test pinning it to the table view would have to
   * be rewritten by the slice it is supposed to protect. */
  await openBrowseIndex(page, '#not-a-browse-state');

  await expectView(page, 'table');

  /* Ignored, not swallowed: the address is left exactly as it was found,
   * so a fragment meant for something else survives the visit. */
  await expect.poll(() => hashOf(page)).toBe('#not-a-browse-state');
  expect(errors).toEqual([]);
});

test('coming back from a resource page returns to the folder it was '
  + 'opened from', async ({
  page,
}) => {
  /* The Coverage GRR, not the browse one. This is the only test here
   * that has to *leave* the index page, and the browse GRR has nowhere
   * to go: it is published by `repo-index`, which writes no page inside
   * any resource directory, so its tree links address files that do not
   * exist and the harness aborts the navigation. The Coverage GRR gets
   * the full `repo-info` pass and does have them. */
  await serveGrr(page, FIXTURE_GRR);
  await page.goto(indexPageUrl() + '#/');
  await expectView(page, 'hierarchical');

  await page.locator('#hierarchical-list .hv-folder')
    .filter({ hasText: 'scores' }).click();
  await page.locator('#hierarchical-list .hv-link').click();

  await expect(page).toHaveURL(infoPageUrl(COVERAGE_RESOURCE));

  await page.goBack();

  /* A real reload, not a fragment move -- the resource page is a
   * different document. So this is the *load* path being asserted, and
   * the history entry having carried the fragment with it: the page has
   * no other memory of where the reader was.
   *
   * The folder, not the root. Before iossifovlab/gain#579 the descent
   * into `scores` wrote nothing to the address, so the entry this
   * returns to was the `#/` the page was opened at and the reader landed
   * a level above the resource they had just been reading. */
  await expect.poll(() => hashOf(page)).toBe('#/scores');
  await expectView(page, 'hierarchical');
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'scores']);
});

test('two GRRs sharing an origin do not share a browse view', async ({
  page,
}) => {
  /* Both repositories on one host, each under its own sub-path -- how
   * they are actually published, and the arrangement that broke this
   * page once before (iossifovlab/gain#129).
   *
   * The check is not ceremony. It is the one assertion that
   * distinguishes state belonging to a *document* from state belonging
   * to an origin, and this issue arrived proposing `sessionStorage` --
   * which is shared by both of these pages and would carry the first
   * one's view to the second. Keeping the state in the address is what
   * makes them independent, and this is where that is demonstrated
   * rather than argued. */
  await serveGrrsUnderSubPaths(page, new Map([
    ['browse', FIXTURE_BROWSE_GRR],
    ['coverage', FIXTURE_GRR],
  ]));

  /* Reached by *clicking*, not by loading `#/` directly. The design this
   * rules out would put its `setItem` in the click handler, so a first
   * document that only ever had the state handed to it in its URL would
   * never run the line that leaks. */
  await page.goto(indexPageUrlUnder('browse'));
  await page.locator('#hierarchical-view-btn').click();
  await expectView(page, 'hierarchical');

  await page.goto(indexPageUrlUnder('coverage'));

  await expectView(page, 'table');
});

test('the sub-path router refuses a segment that names no GRR', async ({
  page,
}) => {
  /* Kept short: the failure this guards against is a request that is
   * never answered *either way*, and the symptom of that is the default
   * timeout rather than a refusal. */
  test.setTimeout(20_000);
  await serveGrrsUnderSubPaths(
    page, new Map([['browse', FIXTURE_BROWSE_GRR]]));

  await expect(page.goto(indexPageUrlUnder('nonesuch'))).rejects.toThrow();

  /* `constructor` names no GRR either. Kept as its own case because it
   * is the one that goes wrong when the lookup is an object rather than
   * a Map: an object answers it with `Object`'s constructor instead of
   * `undefined`, the miss goes unnoticed, and a *function* reaches a
   * path join and throws. An exception inside the route handler leaves
   * the request neither fulfilled nor aborted -- it hangs, which is the
   * one thing this harness promises never to do, and under
   * `--network none` in CI it is indistinguishable from the network
   * being reached for. This passes structurally now; it is here so that
   * swapping the Map back for an object fails loudly. */
  await expect(page.goto(indexPageUrlUnder('constructor'))).rejects.toThrow();
});

/** The inline width every table column currently carries. */
function columnWidths(page: Page): Promise<string[]> {
  return page.locator('#resource-table colgroup col').evaluateAll(
    (cols) => cols.map((col) => (col as HTMLElement).style.width));
}

test('a round trip through the tree leaves the column widths intact', async ({
  page,
}) => {
  await openBrowseIndex(page);

  /* The widths start as inline percentages and are rewritten to pixels
   * by the first `ResizeObserver` callback, so wait for that conversion
   * rather than racing it -- otherwise "before" is a set of percentages
   * and the comparison at the end means nothing. */
  await expect.poll(async () => (await columnWidths(page)).every(
    (width) => width.endsWith('px'))).toBe(true);
  const before = await columnWidths(page);

  await page.locator('#hierarchical-view-btn').click();
  await expectView(page, 'hierarchical');
  await page.locator('#table-view-btn').click();
  await expectView(page, 'table');

  /* Hiding the wrapper fires the observer with a `clientWidth` of 0, and
   * scaling by that would write `0px` into every column -- permanently,
   * because the total would then be 0 and the guard would bail forever
   * after. The `!newWidth` guard added in iossifovlab/gain#560 is what
   * stops it, and routing the toggle through the address must not step
   * around it. Named explicitly so this cannot pass by both sides being
   * equally broken. */
  expect(before).not.toContain('0px');
  expect(await columnWidths(page)).toEqual(before);
});

/* ---- The browsed folder lives in the URL hash (#579) ---- */

/**
 * The folder row with exactly this name.
 *
 * Matched on the name element rather than with `hasText` on the row,
 * because a row's text also carries its resource count and its size --
 * so a substring match is answered by any row whose *size* happens to
 * spell the folder being looked for, and the folder names this suite
 * navigates include one that is mostly punctuation.
 */
function folderRow(page: Page, name: string) {
  return page.locator('#hierarchical-list .hv-folder').filter({
    has: page.locator('.hv-name', { hasText: new RegExp(`^${escapeForRegExp(name)}$`) }),
  });
}

/** `name` with every RegExp metacharacter made literal. */
function escapeForRegExp(name: string): string {
  return name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * The breadcrumb trail, outermost crumb first.
 *
 * `allTextContents` rather than `allInnerTexts`, which collapses runs of
 * whitespace -- a folder whose name carries a space would then be
 * reported under a name it does not have, and one of the names this
 * suite navigates is there precisely to prove spaces survive.
 *
 * The separators are excluded by selecting the crumbs themselves, so the
 * result is the trail and not the trail interleaved with "/".
 */
function breadcrumbTrail(page: Page): Promise<string[]> {
  return page.locator('#breadcrumb .breadcrumb-item').allTextContents();
}

test('drilling into a folder puts it in the URL hash', async ({ page }) => {
  await openBrowseIndex(page, '#/');
  await expectView(page, 'hierarchical');

  await folderRow(page, 'hg38').click();

  await expect.poll(() => hashOf(page)).toBe('#/hg38');
  /* The tree moved too, and not only the address: a folder click that
   * wrote the hash without rendering would pass on the assertion above
   * alone. `scores` is a child of `hg38` and not a top-level folder, so
   * seeing it is what says the descent happened. */
  await expect(folderRow(page, 'scores')).toBeVisible();
});

test('the page opens in the folder its address names', async ({ page }) => {
  await openBrowseIndex(page, '#/hg38/scores');

  await expectView(page, 'hierarchical');
  /* The trail, not just the contents. Restoring the folder while
   * rebuilding the breadcrumb from the root would leave the reader
   * somewhere they cannot climb out of, and the list alone cannot tell
   * the two apart. */
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'hg38', 'scores']);
  await expect(folderRow(page, 'conservation')).toBeVisible();
});

test('a breadcrumb click pushes the folder it climbs to', async ({ page }) => {
  await openBrowseIndex(page, '#/hg38/scores');

  await page.locator('#breadcrumb a.breadcrumb-item')
    .filter({ hasText: 'hg38' }).click();

  await expect.poll(() => hashOf(page)).toBe('#/hg38');
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'hg38']);

  /* Climbing is a push and not a replace, so the way back down is still
   * in the history. A breadcrumb that called the renderer directly --
   * which is what it did before iossifovlab/gain#579 -- leaves the
   * address saying `#/hg38/scores` while the screen shows `hg38`, and
   * this Back then leaves the page entirely. */
  await page.goBack();

  await expect.poll(() => hashOf(page)).toBe('#/hg38/scores');
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'hg38', 'scores']);
});

test('Back walks up the way you came and Forward re-descends', async ({
  page,
}) => {
  await openBrowseIndex(page, '#/');

  await folderRow(page, 'hg38').click();
  await expect.poll(() => hashOf(page)).toBe('#/hg38');
  await folderRow(page, 'scores').click();
  await expect.poll(() => hashOf(page)).toBe('#/hg38/scores');

  /* Two steps up, one per descent, rather than one step out of the tree:
   * each folder move is its own entry, so the trail back is the trail in
   * reverse. */
  await page.goBack();
  await expect.poll(() => hashOf(page)).toBe('#/hg38');
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'hg38']);

  await page.goBack();
  await expect.poll(() => hashOf(page)).toBe('#/');
  await expect(breadcrumbTrail(page)).resolves.toEqual(['All resources']);

  await page.goForward();

  await expect.poll(() => hashOf(page)).toBe('#/hg38');
  await expect(folderRow(page, 'scores')).toBeVisible();
});

test('a hash naming a folder that is gone opens its nearest surviving '
  + 'ancestor', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));

  /* The shape a bookmark takes after the folder it pointed at was
   * renamed away: the repository is regenerated whenever its content
   * changes, so an address outliving a folder is ordinary rather than
   * exceptional. `hg38/scores` is real and `nonesuch` is not. */
  await openBrowseIndex(page, '#/hg38/scores/nonesuch');

  await expectView(page, 'hierarchical');
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'hg38', 'scores']);
  await expect(folderRow(page, 'conservation')).toBeVisible();
  expect(errors).toEqual([]);
});

test('a hash with no surviving ancestor opens the root, still as a tree',
  async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));

  await openBrowseIndex(page, '#/nonesuch/deeper');

  /* The root *in the hierarchical view*, not the table. The address said
   * tree and only the folder on it was wrong, so falling back to the
   * table would answer a question nobody asked. */
  await expectView(page, 'hierarchical');
  await expect(breadcrumbTrail(page)).resolves.toEqual(['All resources']);
  await expect(folderRow(page, BROWSE_TOP_LEVEL_FOLDERS[0])).toBeVisible();
  expect(errors).toEqual([]);
});

test('a hash naming an inherited property name is not a folder', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));

  /* `constructor` names no folder, but it *is* reachable on any plain
   * object through its prototype -- so a lookup written as
   * `children[segment]` answers this one with `Object`'s constructor and
   * the walk-up accepts a folder that does not exist. What follows is not
   * a wrong render but a throw: the accepted "folder" is a function, and
   * reading children off it hands `Object.values` an undefined.
   *
   * Reachable only since the folder path became addressable (#579) --
   * before it, the path was only ever built from folders that existed.
   * The same shape, in the same suite, is why `serving.ts` looks its GRRs
   * up in a `Map`. */
  await openBrowseIndex(page, '#/constructor');

  /* The throw first, because it is the worse half: the breadcrumb below
   * is merely wrong, while this leaves the list empty and every later
   * render broken. */
  expect(errors).toEqual([]);
  await expectView(page, 'hierarchical');
  await expect(breadcrumbTrail(page)).resolves.toEqual(['All resources']);
});

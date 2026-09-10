import { expect, test, type Page } from '@playwright/test';

import {
  BROWSE_CAPITALISED_FOLDER,
  BROWSE_GENOME_RESOURCE_ID,
  BROWSE_GENOME_TYPE,
  BROWSE_ID_ONLY_RESOURCE_ID,
  BROWSE_ID_ONLY_TERM,
  BROWSE_ORDERING_RESOURCE_NAMES,
  BROWSE_RESOURCE_COUNT,
  BROWSE_SCORE_TYPE,
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
 *
 * Read as text, never as visibility. The hierarchical view hides this
 * element, so an address naming the tree would make a `toBeVisible` wait
 * hang forever on a page that had loaded perfectly. The status is written
 * with jQuery's `.text()`, which does not care that the element is
 * hidden, so the signal survives being out of sight.
 *
 * @param resources - the count to wait *for*. An address carrying a
 * search has to override it: such a load passes through the unfiltered
 * count on its way to the filtered one, so waiting for the whole
 * repository would match on the way past and hand the test a page that
 * has not searched yet. Only pass it where the assertion that follows
 * does not retry on its own.
 */
async function openBrowseIndex(
  page: Page, hash = '', resources = BROWSE_RESOURCE_COUNT,
): Promise<void> {
  await serveGrr(page, FIXTURE_BROWSE_GRR);
  await page.goto(indexPageUrl() + hash);
  await expect(page.locator('#status')).toHaveText(`${resources} resources`);
}

/** Type a term into the search box and run the search. */
async function search(page: Page, term: string): Promise<void> {
  await page.locator('#search-field').fill(term);
  await page.locator('#search-field').press('Enter');
}

/** The option the type filter offers for "every type". */
const EVERY_TYPE = 'all';

/** Choose a type in the filter, or `EVERY_TYPE` to stop filtering. */
async function filterByType(page: Page, type: string): Promise<void> {
  await page.locator('#type-filter').selectOption(type);
}

/**
 * Open the Coverage GRR's index page and wait for its search to be usable.
 *
 * The peer of `openBrowseIndex` for the other fixture, which the tests
 * that have to *leave* the index page need: the browse GRR is published
 * by `repo-index` and has no pages inside its resource directories to
 * navigate to, while this one gets the full `repo-info` pass.
 *
 * Its resource count is read off the rendered table rather than written
 * here, because the number is the Coverage GRR's own business -- a
 * resource added there for a sorter test must not redden an index-page
 * one.
 */
async function openCoverageIndex(page: Page, hash = ''): Promise<void> {
  await serveGrr(page, FIXTURE_GRR);
  await page.goto(indexPageUrl() + hash);
  const rows = await page.locator('#resource-table tbody tr').count();
  expect(rows).toBeGreaterThan(0);
  await expect(page.locator('#status')).toHaveText(`${rows} resources`);
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
   * as an empty list rather than as an error.
   *
   * *Which* folders, not in which order: the order is asserted once, by
   * the test that is about the comparator. Pinning it here as well would
   * quietly undo what that test goes to some trouble to buy -- it derives
   * the expected order from the page rather than from this process, so
   * that a collation difference between the two cannot fail it, and a
   * second literal assertion of the order puts that failure right back. */
  expect((await folderNames(page)).sort())
    .toEqual([...BROWSE_TOP_LEVEL_FOLDERS].sort());
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
   * an empty one.
   *
   * Unordered, for the reason given where the folder list is first
   * asserted: the order has one home, and it is not here. */
  expect((await folderNames(page)).sort())
    .toEqual([...BROWSE_TOP_LEVEL_FOLDERS].sort());
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
  const errors = collectPageErrors(page);

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

  await folderRow(page, 'scores').click();
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
 * because a row's text also carries its resource count and its size -- so
 * a substring match is answered by any row whose *size* happens to spell
 * the folder being looked for, and by any folder whose name merely
 * contains it.
 */
function folderRow(page: Page, name: string) {
  return page.locator('#hierarchical-list .hv-folder')
    .filter({ has: page.getByText(name, { exact: true }) });
}

/**
 * The breadcrumb link for `name`.
 *
 * Only the crumbs above the current folder are links -- the last one is a
 * span -- so this locates something clickable by construction. Exact, for
 * the same reason `folderRow` is: a substring match on a trail containing
 * both `hg38` and `hg38_extra` would be answered by either.
 */
function breadcrumbLink(page: Page, name: string) {
  return page.locator('#breadcrumb a.breadcrumb-item')
    .filter({ has: page.getByText(name, { exact: true }) });
}

/** The folder names the tree shows, in the order it shows them. */
function folderNames(page: Page): Promise<string[]> {
  return page.locator('#hierarchical-list .hv-folder .hv-name')
    .allTextContents();
}

/** The resource names the tree shows, in the order it shows them. */
function resourceNames(page: Page): Promise<string[]> {
  return page.locator('#hierarchical-list .hv-resource .hv-name')
    .allTextContents();
}

/**
 * The recursive resource count a folder row reports, as rendered.
 *
 * Read as the text including its brackets rather than parsed to a
 * number, so that a row which had stopped rendering the count at all is
 * a failure here rather than a `NaN` compared against a `NaN`.
 *
 * `innerText` here, where `breadcrumbTrail` below explains at length why
 * it uses `allTextContents` instead. The difference is what each reads
 * from a *hidden* element -- nothing, and the real text, respectively --
 * and the breadcrumb is hidden whenever the table is showing. These two
 * read rows out of `#hierarchical-list`, which is only ever asked about
 * while the tree is on screen, so what is wanted here is the text as
 * rendered.
 */
function folderCount(page: Page, name: string): Promise<string> {
  return folderRow(page, name).locator('.hv-count').innerText();
}

/** The aggregated size a folder row reports, as rendered. */
function folderSize(page: Page, name: string): Promise<string> {
  return folderRow(page, name).locator('.hv-size').innerText();
}

/**
 * The errors the page throws from now on.
 *
 * Attach before the navigation whose errors it is meant to catch; a
 * listener added afterwards sees nothing. Handed back as a live array
 * rather than asserted here, so each test says for itself at which point
 * it expects the page to have stayed quiet.
 */
function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  return errors;
}

/**
 * The breadcrumb trail, outermost crumb first.
 *
 * `allTextContents` rather than `allInnerTexts`: the latter reports text
 * as *rendered*, collapsing whitespace runs and reading nothing at all
 * from a hidden element -- and the breadcrumb is hidden whenever the table
 * view is showing. This reads what the page actually set.
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

  await breadcrumbLink(page, 'hg38').click();

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
  const errors = collectPageErrors(page);

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

test('a walked-up address is rewritten to the folder it resolved to', async ({
  page,
}) => {
  await openBrowseIndex(page, '#/hg38/scores/nonesuch');
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'hg38', 'scores']);

  /* The address is made to agree with the screen, so the URL the reader
   * can copy out of the bar names the folder they are looking at rather
   * than the dead one they arrived by.
   *
   * It also keeps the no-op guard honest. That guard compares *parsed*
   * addresses and knows nothing about the walk-up, so while the address
   * still said `nonesuch` the already-active tree button pushed an entry
   * that changed nothing on screen -- and the Back that followed appeared
   * to do nothing, which is the exact symptom the guard exists to
   * prevent. Asserted on its own below. */
  await expect.poll(() => hashOf(page)).toBe('#/hg38/scores');
});

test('the address a walk-up replaced cannot be gone back to', async ({
  page,
}) => {
  await openBrowseIndex(page, '#/');

  /* Reached by moving the address within the loaded document, so that the
   * entry the walk-up rewrites has a known entry behind it to go back to.
   * Measuring `history.length` instead cannot see this: loading a document
   * adds an entry of its own, so the count grows by one either way. */
  await page.evaluate(() => { location.hash = '/hg38/scores/nonesuch'; });

  await expect.poll(() => hashOf(page)).toBe('#/hg38/scores');

  await page.goBack();

  /* The root, because `replaceState` put the resolved address *in place
   * of* the dead one. Had it been pushed, the dead address would still sit
   * one step back: this Back would land on it, the applier would resolve
   * it again, and the reader would be returned to `#/hg38/scores` -- Back
   * appearing not to work, which is the whole reason for rewriting. */
  await expect.poll(() => hashOf(page)).toBe('#/');
  await expect(breadcrumbTrail(page)).resolves.toEqual(['All resources']);
});

test('the tree button stacks nothing once the address has been rewritten',
  async ({ page }) => {
  await openBrowseIndex(page, '#/hg38/scores/nonesuch');
  await expect.poll(() => hashOf(page)).toBe('#/hg38/scores');
  const before = await page.evaluate(() => history.length);

  await page.locator('#hierarchical-view-btn').click();

  expect(await page.evaluate(() => history.length)).toBe(before);
  await expect.poll(() => hashOf(page)).toBe('#/hg38/scores');
});

test('a hash with no surviving ancestor opens the root, still as a tree',
  async ({ page }) => {
  const errors = collectPageErrors(page);

  await openBrowseIndex(page, '#/nonesuch/deeper');

  /* The root *in the hierarchical view*, not the table. The address said
   * tree and only the folder on it was wrong, so falling back to the
   * table would answer a question nobody asked. */
  await expectView(page, 'hierarchical');
  await expect(breadcrumbTrail(page)).resolves.toEqual(['All resources']);
  await expect(folderRow(page, BROWSE_TOP_LEVEL_FOLDERS[0])).toBeVisible();
  expect(errors).toEqual([]);
});

/* Names no folder has, which a plain object nonetheless answers for out of
 * `Object.prototype` -- so a lookup written as `children[segment]` accepts
 * a folder that does not exist. `__proto__` is the worst of them: *writing*
 * that key on a plain object sets the prototype instead of adding a child,
 * so a repository actually carrying such a folder would corrupt the tree as
 * it was built rather than only when it was addressed.
 *
 * Each gets its own case rather than one test walking the list, so a
 * failure names the segment that broke. */
for (const inherited of ['constructor', '__proto__', 'toString']) {
  test(`a hash naming the inherited property ${inherited} is not a folder`,
    async ({ page }) => {
    const errors = collectPageErrors(page);

    /* Reachable only since the folder path became addressable (#579):
     * before it, the path was built exclusively from folders that
     * existed. What follows acceptance is not a wrong render but a throw
     * -- the accepted "folder" is a function, and reading children off it
     * hands `Object.values` an undefined. The same shape, in this same
     * suite, is why `serving.ts` looks its GRRs up in a `Map`. */
    await openBrowseIndex(page, `#/${inherited}`);

    /* The throw first, because it is the worse half: the breadcrumb below
     * is merely wrong, while this leaves the list empty and every later
     * render broken. */
    expect(errors).toEqual([]);
    await expectView(page, 'hierarchical');
    await expect(breadcrumbTrail(page)).resolves.toEqual(['All resources']);
  });
}

test('a percent-escaped segment is decoded before it is looked up', async ({
  page,
}) => {
  /* `%68` is "h", so this address names `hg38` -- spelled in a way only a
   * page that really decodes can follow. Hand the raw segment to the
   * lookup instead and this reads as a folder literally called
   * "%68g38", which does not exist, so it walks up to the root: this is
   * therefore the test that fails when the decode is dropped, which the
   * malformed-escape test below cannot notice on its own.
   *
   * An escape in a fragment always arrives from outside, never from the
   * page's own links: no *legal* folder name needs escaping, because a
   * resource id is matched against `[a-zA-Z0-9/._-]+` when the repository
   * is enumerated and every one of those characters is unreserved. So
   * what this protects is the hand-edited address and the link mangled by
   * whatever carried it -- and it is the half of the round trip a reader
   * can actually reach. */
  await openBrowseIndex(page, '#/%68g38');

  await expectView(page, 'hierarchical');
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'hg38']);
  await expect(folderRow(page, 'scores')).toBeVisible();
});

test('a hash naming a resource rather than a folder opens its folder',
  async ({ page }) => {
  /* The likeliest stale address there is, because what a reader has to
   * hand is a resource id rather than a folder path. Its last segment
   * names a resource, which is not a folder, so the walk-up stops at the
   * folder holding it. */
  await openBrowseIndex(page, '#/' + BROWSE_ID_ONLY_RESOURCE_ID);

  await expectView(page, 'hierarchical');
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'hg38', 'scores', 'conservation']);
});

test('a hash carrying a malformed escape opens the root without throwing',
  async ({ page }) => {
  const errors = collectPageErrors(page);

  /* `%zz` is not a valid escape, and `decodeURIComponent` answers one by
   * throwing rather than by returning the text unchanged. The address bar
   * is hand-editable and a fragment survives being mangled by whatever
   * copied it, so this arrives without anyone having done anything exotic
   * -- and an uncaught `URIError` here happens while the page is being
   * applied, taking the whole view down with it. */
  await openBrowseIndex(page, '#/%zz');

  expect(errors).toEqual([]);
  await expectView(page, 'hierarchical');
  await expect(breadcrumbTrail(page)).resolves.toEqual(['All resources']);
});

test('sorting the table writes no fragment', async ({ page }) => {
  await openBrowseIndex(page);
  await expectView(page, 'table');

  /* Sorting is the table's other control, and it stayed unaddressable
   * when the search stopped being so (iossifovlab/gain#1331): the column
   * and direction are not part of the browse state, so a sort must leave
   * the address exactly as it found it rather than half-write one.
   *
   * The pair with the search tests is the point. Both controls writing
   * the address and neither writing it are each self-consistent and each
   * wrong; only asserting them apart pins that the page distinguishes
   * them. */
  await page.locator('#id-col-header').click();

  await expect.poll(() => hashOf(page)).toBe('');
  await expectView(page, 'table');
});

test('leaving the tree for the table and back returns to the same folder',
  async ({ page }) => {
  await openBrowseIndex(page, '#/hg38/scores');

  await page.locator('#table-view-btn').click();
  await expect.poll(() => hashOf(page)).toBe('');
  await expectView(page, 'table');

  await page.locator('#hierarchical-view-btn').click();

  /* The folder, not the root. The view buttons carry the folder showing
   * at the time they are clicked, so a round trip through the table is
   * not a way of losing your place. */
  await expect.poll(() => hashOf(page)).toBe('#/hg38/scores');
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'hg38', 'scores']);
});

/**
 * The two orders a mixed-case list of names can be put in.
 *
 * The locale order is computed by the **page**, not by this process.
 * `localeCompare` consults the engine's collation table, and these tests
 * run in Node while the page runs in Chromium -- so deriving the expected
 * order here would compare one ICU build's answer against another's, and
 * an environment where those differ fails the test without anything being
 * wrong with the page. Asking the page keeps the assertion about *which
 * comparator the tree used*, which is what the issue is about.
 */
async function bothOrders(page: Page, names: string[]): Promise<{
  byCodeUnit: string[], byLocale: string[],
}> {
  return {
    /* `sort()` with no comparator compares UTF-16 code units, which is
     * what `<` did. Engine-independent, so it stays here. */
    byCodeUnit: [...names].sort(),
    byLocale: await page.evaluate(
      (unsorted) => [...unsorted].sort((a, b) => a.localeCompare(b)),
      names),
  };
}

test('the tree orders names by locale, as the table does', async ({ page }) => {
  await openBrowseIndex(page, '#/');

  /* Folders first. The guard above the assertion is the point of the
   * capitalised name in the fixture: where the two comparators agree, a
   * tree that sorted by code unit -- or a tree that never sorted at all,
   * and merely happened to receive its folders in order -- passes an
   * assertion like this one, and the divergence between the two views
   * (iossifovlab/gain#564) is invisible. */
  const folders = await bothOrders(page, BROWSE_TOP_LEVEL_FOLDERS);
  expect(folders.byCodeUnit).not.toEqual(folders.byLocale);
  expect(await folderNames(page)).toEqual(folders.byLocale);

  /* Then resources, which the tree sorts with the *same* comparator --
   * so they are asserted here rather than taken on trust from the
   * folders having come out right. */
  await folderRow(page, BROWSE_CAPITALISED_FOLDER).click();

  const resources = await bothOrders(page, BROWSE_ORDERING_RESOURCE_NAMES);
  expect(resources.byCodeUnit).not.toEqual(resources.byLocale);
  expect(await resourceNames(page)).toEqual(resources.byLocale);
});

/* ---- The search state lives in the URL hash (#1331) ---- */

test('Enter runs the search and writes it to the address', async ({
  page,
}) => {
  await openBrowseIndex(page);

  await search(page, BROWSE_SUMMARY_ONLY_TERM);

  await expect.poll(() => hashOf(page))
    .toBe(`#?q=${BROWSE_SUMMARY_ONLY_TERM}`);
});

test('the debounced path writes the address without pushing', async ({
  page,
}) => {
  await openBrowseIndex(page);
  const entriesBefore = await page.evaluate(() => history.length);

  /* Typed a key at a time rather than `fill`ed, so the term reaches the
   * address the way a reader puts it there: through the debounce, which
   * is the path that would stack entries.
   *
   * At Playwright's default typing speed the debounce coalesces the
   * whole term into one settled call, so a build that pushed would add
   * one entry here, not eight. That is still the difference between Back
   * leaving the page and Back stepping through the reader's own typing,
   * and one entry is enough to detect it -- but the count is a property
   * of how fast this test types, not a measurement of the feature. */
  await page.locator('#search-field')
    .pressSequentially(BROWSE_SUMMARY_ONLY_TERM);

  await expect.poll(() => hashOf(page))
    .toBe(`#?q=${BROWSE_SUMMARY_ONLY_TERM}`);
  expect(await page.evaluate(() => history.length)).toBe(entriesBefore);
});

test('the chosen type joins the term in the address', async ({ page }) => {
  await openBrowseIndex(page);

  await search(page, BROWSE_SUMMARY_ONLY_TERM);
  await filterByType(page, BROWSE_SCORE_TYPE);

  /* Both fields, in a fixed order. One search having one spelling is
   * what lets `goTo`'s guard compare addresses as strings at all. */
  await expect.poll(() => hashOf(page))
    .toBe(`#?q=${BROWSE_SUMMARY_ONLY_TERM}&type=${BROWSE_SCORE_TYPE}`);
});

test('a type chosen on its own is the whole query', async ({ page }) => {
  await openBrowseIndex(page);

  await filterByType(page, BROWSE_GENOME_TYPE);

  await expect.poll(() => hashOf(page)).toBe(`#?type=${BROWSE_GENOME_TYPE}`);

  /* That the address says so is half of it; the table has to have
   * actually narrowed. The genome is the fixture's one non-score, so
   * this row set is only reachable by filtering on the type. */
  await expect(visibleResourceIds(page)).toHaveText([
    BROWSE_GENOME_RESOURCE_ID,
  ]);
});

test('clearing both controls takes the query out of the address', async ({
  page,
}) => {
  await openBrowseIndex(page);
  await search(page, BROWSE_SUMMARY_ONLY_TERM);
  await filterByType(page, BROWSE_SCORE_TYPE);
  await expect.poll(() => hashOf(page)).not.toBe('');

  await search(page, '');
  await filterByType(page, EVERY_TYPE);

  /* Not `#?`, and not a bare `#` either: an emptied search leaves the
   * address exactly as an unfiltered table found it, so that arriving
   * with no search and clearing one are the same address rather than two
   * that render alike. */
  await expect.poll(() => hashOf(page)).toBe('');
  await expect(visibleResourceIds(page)).toHaveCount(BROWSE_RESOURCE_COUNT);
});

test('an address carrying a term opens the table filtered by it', async ({
  page,
}) => {
  await openBrowseIndex(page, `#?q=${BROWSE_SUMMARY_ONLY_TERM}`, 1);

  /* The filtered rows and the filled box are both asserted. A page that
   * ran the search without putting the term in the control shows the
   * right rows above a box the reader cannot edit their way out of; one
   * that filled the box without searching shows the term over the whole
   * repository. Either alone passes half of this. */
  await expect(visibleResourceIds(page)).toHaveText([
    BROWSE_SUMMARY_ONLY_RESOURCE_ID,
  ]);
  await expect(page.locator('#search-field'))
    .toHaveValue(BROWSE_SUMMARY_ONLY_TERM);
});

test('an address carrying a type selects it once the options exist', async ({
  page,
}) => {
  await openBrowseIndex(page, `#?type=${BROWSE_GENOME_TYPE}`, 1);

  /* The dropdown's options are not in the markup: they are built from
   * the loaded search index, alongside the id set. Selecting a value
   * before that resolves picks nothing at all -- the option is not there
   * yet -- and does it silently, leaving the control on "all" while the
   * address says otherwise. This is the sharper half of applying a
   * search from the address; the term alone would survive the race. */
  await expect(page.locator('#type-filter')).toHaveValue(BROWSE_GENOME_TYPE);
  await expect(visibleResourceIds(page)).toHaveText([
    BROWSE_GENOME_RESOURCE_ID,
  ]);
});

test('coming back from a resource page restores the filtered table', async ({
  page,
}) => {
  /* The Coverage GRR, for the reason its helper gives: it is the only
   * fixture with pages inside its resource directories, so it is the
   * only one a table row can actually be clicked through to. */
  await openCoverageIndex(page);

  /* Reaches the score through its id and the genome through nothing. */
  await search(page, 'coverage');
  await expect(visibleResourceIds(page)).toHaveText([COVERAGE_RESOURCE]);

  await visibleResourceIds(page).click();
  await expect(page).toHaveURL(infoPageUrl(COVERAGE_RESOURCE));

  await page.goBack();

  /* A real reload -- the resource page is a different document -- so the
   * search survives only because the history entry carried it in the
   * address and the load path put it back. This is the whole point of
   * the feature stated as one navigation: before it, opening a result
   * and coming back handed the reader the unfiltered repository and an
   * empty box, with no way back to what they had been looking at. */
  await expect.poll(() => hashOf(page)).toBe('#?q=coverage');
  await expect(page.locator('#search-field')).toHaveValue('coverage');
  await expect(visibleResourceIds(page)).toHaveText([COVERAGE_RESOURCE]);
});

test('switching to the tree carries the term along', async ({ page }) => {
  await openBrowseIndex(page);
  await search(page, BROWSE_SUMMARY_ONLY_TERM);

  await page.locator('#hierarchical-view-btn').click();

  /* The view moved and the search came with it. A toggle that built its
   * address from the view alone would drop the term here -- and then the
   * applier, finding an address that says "no search", would clear the
   * box to match, so the term is not merely unaddressed but gone. */
  await expect.poll(() => hashOf(page))
    .toBe(`#/?q=${BROWSE_SUMMARY_ONLY_TERM}`);
  await expectView(page, 'hierarchical');
  await expect(page.locator('#search-field'))
    .toHaveValue(BROWSE_SUMMARY_ONLY_TERM);

  /* And the tree arrives already pruned to it (iossifovlab/gain#581).
   *
   * The term carried across the toggle is only half of what makes a
   * shared filtered link work: this is the half where the tree does
   * something with it. `marmoset` reaches one resource, in `hg19`, so
   * that is the only top-level folder left -- a tree that had merely
   * accepted the term and gone on listing the repository would show all
   * four, which is what this asserted while pruning was still unbuilt. */
  await expect.poll(() => folderNames(page)).toEqual(['hg19']);
});

test('a term typed while the index loads survives the address', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_BROWSE_GRR);

  /* Hold the search index back, to open the window between "the box is
   * usable" and "the address's search has been applied" and type into
   * it. The page opens that window itself and on purpose -- the search
   * container is shown synchronously, well before the index has been
   * downloaded and deserialized -- so this is the page's own invitation
   * to type, held open, not a contrived one.
   *
   * Held on a promise rather than a timer. A sleep long enough to be
   * safe here is a flat cost on every run and still only probably long
   * enough on a loaded machine; releasing it once the box demonstrably
   * holds the typed term closes the window on the event that actually
   * matters. */
  let releaseIndex!: () => void;
  const indexHeld = new Promise<void>((resolve) => { releaseIndex = resolve; });
  await page.route(
    (url) => url.pathname.endsWith('.CONTENTS.sqlite3.gz'),
    async (route) => {
      await indexHeld;
      await route.fallback();
    },
  );

  /* The link carries a type as well as a term, and the type is the half
   * that can still land late: the term is written into the box before
   * the wait, but the dropdown cannot be touched until its options
   * exist. So this address is what makes the abandonment check
   * observable -- with a term alone, the ordering does all the work and
   * the check has nothing left to catch. `genome` also disagrees with
   * what the reader is about to ask for, so applying it late changes the
   * rows rather than merely the control. */
  await page.goto(
    `${indexPageUrl()}#?q=${BROWSE_SUMMARY_ONLY_TERM}`
    + `&type=${BROWSE_GENOME_TYPE}`,
  );
  await search(page, BROWSE_ID_ONLY_TERM);
  await expect(page.locator('#search-field')).toHaveValue(BROWSE_ID_ONLY_TERM);
  releaseIndex();

  /* The reader wins. They typed after the link was opened, and the
   * address already agrees with them -- it was rewritten the moment they
   * pressed Enter. An application that finished by writing what the link
   * had said would leave the address, the box and the table each saying
   * something different, with nothing that ever reconciles them.
   *
   * The rows are what settles it: both terms match exactly one resource,
   * so the status line reads "1 resources" either way and would let the
   * wrong one through. */
  await expect(visibleResourceIds(page)).toHaveText([
    BROWSE_ID_ONLY_RESOURCE_ID,
  ]);
  await expect(page.locator('#search-field')).toHaveValue(BROWSE_ID_ONLY_TERM);
  await expect(page.locator('#type-filter')).toHaveValue(EVERY_TYPE);
  expect(hashOf(page)).toBe(`#?q=${BROWSE_ID_ONLY_TERM}`);
});

test('resolving a tree address leaves its query on it', async ({ page }) => {
  await openBrowseIndex(page, `#/hg38?q=${BROWSE_SUMMARY_ONLY_TERM}`, 1);

  /* The tree's render path rewrites the address to the folder it
   * actually resolved to, and that rewrite has to carry the search with
   * it or every tree load that has a query loses it.
   *
   * Asserted here, before anything is clicked, because clicking heals
   * it: the next move rebuilds the address from the controls, which by
   * then hold the term, so an assertion made after one passes whether or
   * not the rewrite dropped anything. */
  expect(hashOf(page)).toBe(`#/hg38?q=${BROWSE_SUMMARY_ONLY_TERM}`);
});

test('moving between folders keeps the term without searching again', async ({
  page,
}) => {
  await openBrowseIndex(page, `#/?q=${BROWSE_SUMMARY_ONLY_TERM}`, 1);

  /* Counted through the page's own handle on the loaded index, because
   * re-running the search is invisible any other way: what the tree
   * lists is rebuilt from the standing hit set on every move, so a build
   * that re-queried for each one would show exactly these rows and
   * merely cost a query each time. The applier runs on every address
   * change and not only on arrival, so this is a real hazard rather than
   * a theoretical one. */
  await page.evaluate(() => {
    const win = window as any;
    const query = win.sqlite3.query;
    win.searchesIssued = 0;
    win.sqlite3.query = (sql: string) => {
      win.searchesIssued += 1;
      return query(sql);
    };
  });

  /* `hg19` rather than any folder, now that the tree is pruned to the
   * term (iossifovlab/gain#581): it is where `marmoset`'s one match
   * lives, and so the only folder there is to move into. */
  await folderRow(page, 'hg19').click();

  await expect.poll(() => hashOf(page))
    .toBe(`#/hg19?q=${BROWSE_SUMMARY_ONLY_TERM}`);
  await expect(page.locator('#search-field'))
    .toHaveValue(BROWSE_SUMMARY_ONLY_TERM);
  expect(await page.evaluate(() => (window as any).searchesIssued)).toBe(0);
});

test('a malformed term in the address degrades, it does not throw', async ({
  page,
}) => {
  const errors = collectPageErrors(page);

  /* A lone double quote: FTS5 reads it as the start of a string that
   * never ends. The page has always been able to receive one -- it is
   * two keystrokes in the search box -- and answers it by logging,
   * showing a message and falling back to the whole repository. Arriving
   * from the address must reach that same path: a link is now a way to
   * hand this page a query, and the one it cannot parse must not be the
   * one that breaks the load. */
  /* The count this waits for is the whole repository, which is also what
   * a failed query falls back to -- so unlike the other search loads,
   * the wait is not what synchronises this test. The error message
   * below is; it cannot appear before the query has been run and
   * rejected. */
  await openBrowseIndex(page, '#?q=%22');

  await expect(page.locator('#status-error'))
    .toHaveText(/Query failed due to syntax error/);
  await expect(visibleResourceIds(page)).toHaveCount(BROWSE_RESOURCE_COUNT);
  await expect(page.locator('#search-field')).toHaveValue('"');
  expect(errors).toEqual([]);
});

test('an address naming a type that is gone searches every type', async ({
  page,
}) => {
  await openBrowseIndex(page, '#?type=nosuchtype');

  /* Salvaged rather than obeyed, which is the choice the folder path
   * already makes when it walks up to an ancestor that still exists:
   * repositories are regenerated, and a link that named a type nobody
   * publishes any more should still open.
   *
   * Obeying it is not an option that leaves the page usable. Selecting
   * an option that is not there selects *nothing*, leaving the dropdown
   * blank and the query asking for the literal string "null" -- an empty
   * table, a blank control, and no account of why. */
  await expect(page.locator('#type-filter')).toHaveValue(EVERY_TYPE);
  await expect(visibleResourceIds(page)).toHaveCount(BROWSE_RESOURCE_COUNT);
});

test('a tree still browses when the search index cannot be loaded', async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await serveGrr(page, FIXTURE_BROWSE_GRR);

  /* Abort sqlite-wasm. It is a *static* import at the top of the module
   * that owns the search, so failing it means that module never
   * evaluates at all and never publishes the seam -- not that the search
   * merely comes up empty.
   *
   * Before the search was addressable, the module that owns the address
   * touched none of it, and a CDN this page could not reach still left a
   * browsable tree and a working Back and Forward over an empty table.
   * Consulting the seam put that at risk: reaching for it directly makes
   * the whole address machinery die with the CDN. */
  await page.route(
    (url) => url.href.includes('sqlite-wasm'),
    (route) => route.abort(),
  );

  await page.goto(`${indexPageUrl()}#/`);

  /* An *empty* tree, and that is the pre-existing bargain rather than a
   * shortfall: the tree is folded up from `window.rowData`, which the
   * same unevaluated module publishes, which is why the fold has always
   * started from `window.rowData || {}`. What is being asserted is that
   * the page still reads its address and renders the view it names,
   * without throwing on the way. */
  await expectView(page, 'hierarchical');
  expect(await folderNames(page)).toEqual([]);
  expect(errors).toEqual([]);

  /* And that the address machinery is alive, not merely that nothing
   * was thrown while it started up. A build that took the seam on trust
   * dies at module evaluation, which leaves the buttons inert and Back
   * unable to restore the table. */
  await page.locator('#table-view-btn').click();
  await expect.poll(() => hashOf(page)).toBe('');
  await expectView(page, 'table');

  await page.goBack();
  await expect.poll(() => hashOf(page)).toBe('#/');
  await expectView(page, 'hierarchical');
});

test('searching replaces a fragment the page does not recognise', async ({
  page,
}) => {
  await openBrowseIndex(page, '#section-2');

  await search(page, BROWSE_SUMMARY_ONLY_TERM);

  /* The one place this page discards a fragment it did not write, and
   * deliberately, so it is pinned rather than left to be discovered.
   *
   * The view buttons do not do this: arriving at a view is not consent
   * to discard someone else's anchor, and their guard says so. Searching
   * is different twice over. It is an act aimed at this page rather than
   * a way of arriving at it, and the table's address is the query and
   * nothing else -- there is no spelling of "filtered, and also
   * #section-2" to keep. */
  await expect.poll(() => hashOf(page))
    .toBe(`#?q=${BROWSE_SUMMARY_ONLY_TERM}`);
});

/*
 * Every character the address has to carry without altering it: the four
 * FTS operators a reader may legitimately type (`"`, `*`, `:`, `-`), a
 * space, and non-ASCII. `encodeURIComponent` leaves `*` and `-` alone and
 * escapes the rest, and `URLSearchParams` -- the obvious-looking way to
 * build a query string -- would spell the space `+` and hand the reader
 * back a different term than they typed.
 */
const AWKWARD_TERM = '"phast*" -naïve:ünïcøde';

test('a term of FTS operators and non-ASCII survives the round trip', async ({
  page,
}) => {
  await openBrowseIndex(page);

  await search(page, AWKWARD_TERM);
  await expect.poll(() => hashOf(page)).not.toBe('');

  await page.reload();

  /* Reloaded rather than merely re-read, so the term comes back out of
   * the *address* and not out of the box it was typed into. Asserting
   * the box without reloading would pass on a page that never encoded
   * anything, since the value was already sitting there.
   *
   * This one assertion carries the test. Comparing the fragment across
   * the reload would look like a second, independent check and is not
   * one: nothing rewrites the table's address on load, so the browser
   * preserves it whatever the parser and builder do with it. */
  await expect(page.locator('#search-field')).toHaveValue(AWKWARD_TERM);
});

test('the toggle for the view already showing stacks nothing, term or not',
  async ({ page }) => {
    await openBrowseIndex(page);
    await search(page, BROWSE_SUMMARY_ONLY_TERM);
    await expect.poll(() => hashOf(page))
      .toBe(`#?q=${BROWSE_SUMMARY_ONLY_TERM}`);
    const entriesBefore = await page.evaluate(() => history.length);

    await page.locator('#table-view-btn').click();

    /* The same guard the view slice put in, asked again now that the
     * state it compares has two more fields in it.
     *
     * What this catches is a guard that has fallen behind the push: the
     * address `goTo` builds includes the search, so a comparison that
     * still looked at only the view and the folder would call these two
     * states equal, decline to push -- or, inverted, push an entry that
     * changes nothing and leave Back appearing not to work.
     *
     * What it cannot catch, despite appearances, is the builder and the
     * parser disagreeing about how to spell a search: both sides of that
     * comparison go through the same builder, so a change of field order
     * cancels out. `the chosen type joins the term in the address` is
     * what pins the spelling. Its old test covers the no-search case. */
    expect(await page.evaluate(() => history.length)).toBe(entriesBefore);
    await expect.poll(() => hashOf(page))
      .toBe(`#?q=${BROWSE_SUMMARY_ONLY_TERM}`);
  });

/* ---- The search prunes the tree (#581) ---- */

test('a term prunes the tree to the folders containing a match', async ({
  page,
}) => {
  await openBrowseIndex(page, '#/');
  await expectView(page, 'hierarchical');

  await search(page, BROWSE_ID_ONLY_TERM);

  /* `phylop` reaches exactly one resource -- `hg38/scores/conservation/
   * phylop`, and through its id -- so `hg38` is the only top-level folder
   * with a match anywhere beneath it.
   *
   * The other three folders are what make this falsifiable in both
   * directions. A tree that ignored the search entirely would still list
   * all four; a tree that emptied itself on any search at all would list
   * none. Only pruning gives exactly this one. */
  await expect.poll(() => folderNames(page)).toEqual(['hg38']);
});

test('a pruned folder reports the matched count and the matched size',
  async ({ page }) => {
    await openBrowseIndex(page, '#/');

    /* What `hg38` reports unfiltered, read off the page rather than
     * written here: how many resources it holds is the browse fixture's
     * business, and a literal would redden this test the day one is
     * added for some other reason. It is captured only to be shown
     * different afterwards. */
    const wholeCount = await folderCount(page, 'hg38');

    await search(page, BROWSE_ID_ONLY_TERM);

    /* One match beneath it, so it reports one -- not the three it holds.
     * This is the assertion that separates a pruned tree from a filtered
     * *listing*: a build that hid the non-matching rows but kept each
     * folder's own totals would show `hg38` here with the count it had
     * before, promising two resources that are not underneath it. */
    await expect.poll(() => folderCount(page, 'hg38')).toBe('(1)');
    expect(wholeCount).not.toBe('(1)');

    const matchedSize = await folderSize(page, 'hg38');

    /* And the size is the size of what matched. Derived rather than
     * asserted as a literal, and derived from the *other* renderer: with
     * `phylop` the only resource left under `hg38`, the folder's
     * aggregate has to come to exactly that one resource's own size --
     * which the page prints from the value the generator put in the row,
     * not from the arithmetic under test.
     *
     * So this fails both if the aggregate still totals the whole folder
     * and if `formatSize` has drifted from the `convert_size` it mirrors
     * -- the two are only ever compared where a folder holds exactly one
     * resource, and pruning is what arranges that. */
    await folderRow(page, 'hg38').click();
    await folderRow(page, 'scores').click();
    await folderRow(page, 'conservation').click();

    await expect(page.locator('#hierarchical-list .hv-resource .hv-size'))
      .toHaveText(matchedSize);
  });

test('a match far below the current folder stays reachable through it',
  async ({ page }) => {
    await openBrowseIndex(page, '#/hg38');
    await search(page, BROWSE_ID_ONLY_TERM);

    /* Two levels down from here, so the folders between have to survive
     * the pruning or the match cannot be got at. That is the whole claim,
     * and it is only decidable below this first step: `hg38` holds
     * nothing but `scores` either way, so seeing `scores` here says
     * nothing yet. */
    await expect.poll(() => folderNames(page)).toEqual(['scores']);

    await folderRow(page, 'scores').click();

    /* Here it starts to discriminate. Unfiltered, `scores` shows the
     * `conservation` folder *and* the `coverage` resource sitting beside
     * it; the term reaches neither `coverage` nor anything under it, so
     * only the folder that leads onward is left. A build that kept the
     * intermediate folders by simply not pruning them would still list
     * `coverage` here. */
    await expect.poll(() => folderNames(page)).toEqual(['conservation']);
    expect(await resourceNames(page)).toEqual([]);

    await folderRow(page, 'conservation').click();

    /* And at the bottom, the match alone -- `phastcons` shares this
     * folder with it and is gone. */
    await expect.poll(() => resourceNames(page)).toEqual(['phylop']);
  });

test('a resource matching only its summary is found in the tree', async ({
  page,
}) => {
  await openBrowseIndex(page, '#/');

  await search(page, BROWSE_SUMMARY_ONLY_TERM);

  /* The term appears in no id anywhere in the fixture -- pinned by
   * `test_info_page_browse_fixture.py` -- so nothing about this resource's
   * *address* can lead the tree to it. Only the full-text index can, and
   * that is the point: the tree does not match ids for itself, it prunes
   * to the hit set the table's own search returned, so both views answer
   * one query with one set of semantics.
   *
   * A tree that had grown its own matcher over the folder names it
   * already has would find nothing here and show an empty repository. */
  const [top, mid, name] = BROWSE_SUMMARY_ONLY_RESOURCE_ID.split('/');

  await expect.poll(() => folderNames(page)).toEqual([top]);

  await folderRow(page, top).click();
  await folderRow(page, mid).click();

  await expect.poll(() => resourceNames(page)).toEqual([name]);
});

test('a type chosen on its own prunes the tree to that type', async ({
  page,
}) => {
  await openBrowseIndex(page, '#/');

  await filterByType(page, BROWSE_GENOME_TYPE);

  /* One resource carries this type, in `genomes`. */
  const [genomeFolder] = BROWSE_GENOME_RESOURCE_ID.split('/');
  await expect.poll(() => folderNames(page)).toEqual([genomeFolder]);

  await filterByType(page, BROWSE_SCORE_TYPE);

  /* Then the other way, which is what makes the first half mean
   * something. A filter that returned everything would pass the
   * assertion above only if `genomes` were the whole repository, and a
   * filter that returned nothing would fail it -- but a filter stuck on
   * one answer is only visible by asking for the complement and getting
   * a different one. Every folder except `genomes` holds scores. */
  await expect.poll(async () => (await folderNames(page)).sort())
    .toEqual(BROWSE_TOP_LEVEL_FOLDERS
      .filter((folder) => folder !== genomeFolder).sort());
});

test('clearing the search restores the folder it was searched from', async ({
  page,
}) => {
  await openBrowseIndex(page, '#/hg38/scores');

  /* The whole folder as it stands, captured to be compared against
   * itself later. Restoring is the claim, so the expected values have to
   * come from before the search rather than from this file -- a literal
   * would let a restore that rebuilt something *similar* pass. */
  const wholeFolders = await folderNames(page);
  const wholeResources = await resourceNames(page);
  const wholeCount = await folderCount(page, 'conservation');

  await search(page, BROWSE_ID_ONLY_TERM);
  await expect.poll(() => resourceNames(page)).toEqual([]);

  await search(page, '');

  /* Everything back, including the counts. The counts matter separately
   * from the rows: a build that dropped the filter but kept rendering
   * from the tree it built for the search would list the whole folder
   * again while still reporting the *matched* totals beside it. */
  await expect.poll(() => folderNames(page)).toEqual(wholeFolders);
  expect(await resourceNames(page)).toEqual(wholeResources);
  expect(await folderCount(page, 'conservation')).toBe(wholeCount);

  /* And in the folder it was searched from, not at the root. */
  expect(hashOf(page)).toBe('#/hg38/scores');
});

test('a term matching nothing here empties the folder without leaving it',
  async ({ page }) => {
    await openBrowseIndex(page, '#/hg38');

    /* `marmoset`'s one match lives under `hg19`, so there is nothing to
     * show in `hg38` -- which is the case that decides whether pruning is
     * a filter or a move.
     *
     * The tree resolves an address by walking up to the nearest folder
     * that still exists, and rewrites the address to wherever it lands
     * (iossifovlab/gain#579). Were the folder resolved against the pruned
     * tree, `hg38` would not be in it, the walk-up would land at the root,
     * and the reader would be moved out of the folder they searched from
     * by a term that merely matched nothing in it. */
    await search(page, BROWSE_SUMMARY_ONLY_TERM);

    await expect(page.locator('#hierarchical-list .hv-empty')).toBeVisible();
    expect(await folderNames(page)).toEqual([]);
    expect(await resourceNames(page)).toEqual([]);

    /* Still here: the address, and the trail that says where here is. */
    expect(hashOf(page)).toBe(`#/hg38?q=${BROWSE_SUMMARY_ONLY_TERM}`);
    expect(await breadcrumbTrail(page)).toContain('hg38');

    /* And clearing it gives the folder back rather than an ancestor,
     * which is what the reader loses if the walk-up ever runs here. */
    await search(page, '');

    await expect.poll(() => folderNames(page)).toEqual(['scores']);
    expect(hashOf(page)).toBe('#/hg38');
  });

test('an address carrying a term opens the tree already pruned', async ({
  page,
}) => {
  /* The count is overridden because this load passes through the whole
   * repository on its way to the filtered answer, and waiting for seven
   * would match on the way past. */
  await openBrowseIndex(page, `#/?q=${BROWSE_ID_ONLY_TERM}`, 1);

  await expectView(page, 'hierarchical');

  /* The other half of the seam, and the half that is easy to leave out.
   * A tree told about searches by the *controls* -- "the reader typed
   * something" -- is never told about this one: nobody typed it, the
   * address applied it. Such a build passes every test above, because
   * every one of them types, and opens this shared link unpruned. */
  await expect.poll(() => folderNames(page)).toEqual(['hg38']);
});

test('a malformed term leaves the tree browsable', async ({ page }) => {
  const errors = collectPageErrors(page);

  /* The same lone double quote the table degrades on, arriving at the
   * tree. FTS5 rejects it, the search falls back to the whole repository,
   * and the tree has to fall back with it -- listing everything is the
   * honest answer to a query that could not be run, and is what the
   * reader sees in the other view.
   *
   * What this rules out is a tree that took the failure for an empty hit
   * set: the fallback reaches it through exactly the same call as a real
   * result, so it is one `catch` away from pruning the repository down to
   * nothing and reporting it as "no resources". */
  await openBrowseIndex(page, '#/?q=%22');

  /* The synchronisation point, and it is not optional. Everything this
   * test asserts afterwards -- all four folders, no error thrown -- is
   * equally true of the page *before* the malformed query has been run,
   * because the load passes through the unfiltered repository on its way
   * to attempting the search. Without something that cannot be true
   * until the query has been rejected, the test could be answered in
   * full by a page that had not searched yet. `#status-error` is that:
   * it is written only in the `catch`. It survives the tree view, unlike
   * `#status` beside it, which `setView` hides. */
  await expect(page.locator('#status-error'))
    .toHaveText(/Query failed due to syntax error/);

  await expect(page.locator('#search-field')).toHaveValue('"');
  expect((await folderNames(page)).sort())
    .toEqual([...BROWSE_TOP_LEVEL_FOLDERS].sort());
  expect(errors).toEqual([]);
});

test('toggling to the table shows matches from outside the folder',
  async ({ page }) => {
    /* Opened in a folder the term does not reach, so the tree is empty
     * -- which is what makes the toggle say something. */
    await openBrowseIndex(page, `#/hg38?q=${BROWSE_SUMMARY_ONLY_TERM}`, 1);
    await expect(page.locator('#hierarchical-list .hv-empty')).toBeVisible();

    await page.locator('#table-view-btn').click();

    /* The table has no current folder and never had one, so leaving the
     * tree for it widens the same search back out to the repository: the
     * match is in `hg19`, nowhere near where the reader was standing.
     *
     * The pairing is the point. A build that had scoped the *search*
     * rather than the listing -- narrowing the ids at the moment they
     * arrived, instead of walking into them at render time -- would show
     * this table as empty too, having thrown away every hit outside
     * `hg38` before either view got to see them. */
    await expect(visibleResourceIds(page))
      .toHaveText([BROWSE_SUMMARY_ONLY_RESOURCE_ID]);
    await expect(page.locator('#search-field'))
      .toHaveValue(BROWSE_SUMMARY_ONLY_TERM);
  });

/* ---- Search scope follows the breadcrumb (#582) ---- */

test('climbing the breadcrumb widens the search rather than dropping it',
  async ({ page }) => {
    await openBrowseIndex(page, '#/hg38');

    /* What `scores` reports with nothing filtered, read off the page
     * rather than written here -- the same reason the pruning tests
     * above capture theirs: how many resources it holds is the browse
     * fixture's business, and a literal would redden this the day one is
     * added for an unrelated reason. */
    const wholeScores = await folderCount(page, 'scores');

    await folderRow(page, 'scores').click();
    await search(page, BROWSE_ID_ONLY_TERM);

    /* Waited for on the row the term actually removes. `scores` keeps its
     * one child folder under this term, so a barrier watching the folders
     * would be true before the search had run and would let the climb
     * below go first -- gating on nothing while looking like a gate. */
    await expect.poll(() => resourceNames(page)).toEqual([]);

    await breadcrumbLink(page, 'hg38').click();

    /* The term came back up with the reader. Climbing is a move like any
     * other, and a move that dropped the query would not merely leave it
     * unaddressed: the applier, reading an address that says "no
     * search", would clear the box to match it. */
    await expect.poll(() => hashOf(page)).toBe(`#/hg38?q=${BROWSE_ID_ONLY_TERM}`);
    await expect(page.locator('#search-field'))
      .toHaveValue(BROWSE_ID_ONLY_TERM);

    /* And the wider scope is *pruned*, which is the half of this that
     * the folder names cannot show. `hg38` holds exactly one child
     * folder, `scores`, whether or not anything is filtered -- so a tree
     * that had kept the term and gone on listing the whole repository
     * would show this same single row, and a `folderNames` assertion
     * here would pass either way.
     *
     * The count is what separates them, and it is the reason climbing is
     * how a reader discovers they were scoped too deep: one match under
     * `scores`, not the three it holds.
     *
     * Which is also why it is the count that retries. Polling the names
     * and then reading the count once would wait on the assertion that
     * cannot fail and hurry the one that can. */
    await expect.poll(() => folderCount(page, 'scores')).toBe('(1)');
    expect(wholeScores).not.toBe('(1)');
  });

test('Back from a folder entered while filtered returns to the wider scope',
  async ({ page }) => {
    await openBrowseIndex(page, '#/hg38');
    await search(page, BROWSE_ID_ONLY_TERM);
    await expect.poll(() => folderCount(page, 'scores')).toBe('(1)');

    await folderRow(page, 'scores').click();

    await expect.poll(() => hashOf(page))
      .toBe(`#/hg38/scores?q=${BROWSE_ID_ONLY_TERM}`);

    await page.goBack();

    /* Back leads out of the *folder*, not out of the page. Moving while
     * filtered pushes, where editing the search replaces (#1331), and the
     * difference is the whole of this test: with a move that replaced
     * too, the reader's arrival, their search and their descent would all
     * be the same single entry, and this Back would leave the page
     * altogether.
     *
     * Which is also why the wider scope has to be reached by going back
     * rather than by clicking the crumb -- the crumb is what the test
     * above climbs, and it would pass just as well against a history
     * that had recorded nothing at all. */
    await expect.poll(() => hashOf(page))
      .toBe(`#/hg38?q=${BROWSE_ID_ONLY_TERM}`);

    /* Still filtered, in the box and in the tree alike. An entry that had
     * been pushed without the query would come back to `#/hg38`, and the
     * applier would then clear the box to agree with it -- so the reader
     * would find themselves where they started with their search
     * silently undone. */
    await expect(page.locator('#search-field'))
      .toHaveValue(BROWSE_ID_ONLY_TERM);
    await expect.poll(() => folderCount(page, 'scores')).toBe('(1)');
  });

test('climbing to the root prunes the whole repository, box intact',
  async ({ page }) => {
    await openBrowseIndex(page, '#/hg38/scores/conservation');
    await search(page, BROWSE_ID_ONLY_TERM);

    /* Waited for, not assumed. Clicking straight after asking for a
     * search would leave it to timing whether the reader climbs *while
     * filtered* -- the scenario -- or climbs before the answer arrives,
     * which is a different one that happens to end in the same place. */
    const [, , , matchedName] = BROWSE_ID_ONLY_RESOURCE_ID.split('/');
    await expect.poll(() => resourceNames(page)).toEqual([matchedName]);

    await breadcrumbLink(page, 'All resources').click();

    await expect.poll(() => hashOf(page)).toBe(`#/?q=${BROWSE_ID_ONLY_TERM}`);

    /* The widest scope there is, and it is still a scope: `phylop`
     * reaches one resource, so of the repository's several top-level
     * folders only the one above it survives.
     *
     * This is where a folder-name assertion *is* the discriminating one,
     * where climbing to `hg38` needed the count -- the fixture's other
     * top-level folders have no match anywhere beneath them, so a tree
     * that had dropped the query on the way up lists them all. */
    await expect.poll(() => folderNames(page)).toEqual(['hg38']);

    /* And the box was not emptied to pay for it. Arriving at the root is
     * not the same act as clearing the search, and the reader who climbs
     * there is widening theirs rather than abandoning it. */
    await expect(page.locator('#search-field'))
      .toHaveValue(BROWSE_ID_ONLY_TERM);
  });

test('a folder entered while filtered reports what matched, and gives the '
  + 'whole of itself back when the search is cleared', async ({ page }) => {
    await openBrowseIndex(page, '#/hg38/scores');

    /* The row this test ends on, read before any search has touched it.
     * Its own earlier reading, not some other row's: a count taken from
     * an ancestor would agree with this one only for as long as the
     * fixture kept a single child folder between them, and would then
     * redden for a reason that has nothing to do with what is being
     * claimed here. */
    const wholeConservation = await folderCount(page, 'conservation');
    expect(wholeConservation).not.toBe('(1)');

    await breadcrumbLink(page, 'hg38').click();
    await search(page, BROWSE_ID_ONLY_TERM);
    await expect.poll(() => folderCount(page, 'scores')).toBe('(1)');

    await folderRow(page, 'scores').click();

    /* Moved into the folder while filtered, and the row that greets the
     * reader there reports what matched rather than what it holds. A
     * build that rendered the move from the listing it already had --
     * rather than walking into the pruned tree afresh -- would show the
     * reader numbers belonging to the folder they just left. */
    await expect.poll(() => folderCount(page, 'conservation')).toBe('(1)');

    await search(page, '');

    /* And clearing gives the folder back where the reader now stands,
     * rather than where they were standing when they typed. The count is
     * the assertion because it is the part that can go stale on its own:
     * the rows can come back correctly while the totals beside them still
     * describe the search that has just been cleared. */
    await expect.poll(() => folderCount(page, 'conservation'))
      .toBe(wholeConservation);
    expect(hashOf(page)).toBe('#/hg38/scores');
  });

test('the toggle applies a standing term to the folder it restores',
  async ({ page }) => {
    await openBrowseIndex(page, '#/hg38/scores');

    /* The folder has a resource to lose. `scores` keeps its one child
     * folder under this term either way, so a listing that ignored the
     * search would give itself away by the resource beside it rather than
     * by the folder -- which makes this the guard that stops the
     * `toEqual([])` below from being satisfied by an empty folder. */
    expect(await resourceNames(page)).not.toEqual([]);

    await page.locator('#table-view-btn').click();
    await expectView(page, 'table');
    await search(page, BROWSE_ID_ONLY_TERM);
    await page.locator('#hierarchical-view-btn').click();

    /* Back to the folder the reader left, not to the root -- the toggle
     * carries the folder showing at the time it is clicked, and the visit
     * to the table does not disturb it. */
    await expect.poll(() => hashOf(page))
      .toBe(`#/hg38/scores?q=${BROWSE_ID_ONLY_TERM}`);
    await expectView(page, 'hierarchical');

    /* And pruned *there*. This is the case the tree can get wrong in a
     * way that shows: the term was typed while the tree was not on
     * screen, so a build that applied searches only to the view that was
     * showing would restore the folder exactly as it was left --
     * listing a resource the box says was filtered out, with the term
     * still in the box to contradict it. */
    await expect.poll(() => resourceNames(page)).toEqual([]);
    expect(await folderNames(page)).toEqual(['conservation']);
    await expect(page.locator('#search-field'))
      .toHaveValue(BROWSE_ID_ONLY_TERM);
  });

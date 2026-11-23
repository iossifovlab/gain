import { expect, test, type Page } from '@playwright/test';

import { breadcrumbTrail } from '../breadcrumb';
import { FIXTURE_BROWSE_GRR } from '../fixtures';
import { indexPageUrl, serveGrr } from '../serving';

/**
 * The index page addressed at a folder, before its modules run
 * (gain#2484).
 *
 * The tree module is a `<script type="module">`, so it runs only after
 * the parser has read the whole page. On a large GRR that is long after
 * the first paint. An address that names a folder must still never
 * paint the resource table: from the first paint the page shows the
 * tree's wrapper and the trail of the folder.
 */

/** The folder these tests address, as its segments. */
const FOLDER = ['hg19', 'legacy'];

/** The address of `FOLDER` on the index page. */
const FOLDER_HASH = '#/' + FOLDER.join('/');

/** What the page showed when the parser inserted one element. */
interface Insertion {
  display: string;
  top: number;
  trail: string[];
}

/** What `recordInsertions` saw, keyed by element id. */
type Insertions = Record<string, Insertion>;

/**
 * Record what the page shows at the moment the parser inserts each of
 * `ids`.
 *
 * A `MutationObserver` registered before the document has any element.
 * Its callback runs at the first microtask checkpoint after the
 * insertion, which is before any module of the page runs, because the
 * modules wait for the end of the parse. Each id is recorded once.
 */
async function recordInsertions(page: Page, ids: string[]): Promise<void> {
  await page.addInitScript((watched: string[]) => {
    const seen: Record<string, unknown> = {};
    (window as unknown as { __insertions: unknown }).__insertions = seen;
    const observer = new MutationObserver(() => {
      for (const id of watched) {
        if (id in seen) continue;
        const el = document.getElementById(id);
        if (!el) continue;
        const crumbs = document.querySelectorAll(
          '#page-header #breadcrumb .breadcrumb-item');
        seen[id] = {
          display: getComputedStyle(el).display,
          top: el.getBoundingClientRect().top,
          trail: [...crumbs].map((c) => c.textContent ?? ''),
        };
      }
      if (watched.every((id) => id in seen)) observer.disconnect();
    });
    observer.observe(document, { childList: true, subtree: true });
  }, ids);
}

/** What the init script of `recordInsertions` recorded. */
function insertions(page: Page): Promise<Insertions> {
  return page.evaluate(() => (
    window as unknown as { __insertions: Insertions }).__insertions);
}

/**
 * Open the browse index at `FOLDER` with its search never loading.
 *
 * The search module shows the search box in place of the loading line,
 * and the two differ in height. Without the search, the only thing that
 * can move the tree after the first paint is the tree module itself.
 *
 * `fonts: false` also blocks the fonts of the repository. The first
 * paint uses the fallback fonts, and the real fonts change the line
 * heights when they arrive. That move is not the tree module's.
 */
async function openFolderWithoutSearch(
  page: Page, { fonts = true, hash = FOLDER_HASH } = {},
): Promise<void> {
  await serveGrr(page, FIXTURE_BROWSE_GRR);
  /* Registered after `serveGrr`: the route registered last answers
   * first. */
  await page.route(
    (url) => url.href.includes('sqlite-wasm')
      || (!fonts && url.pathname.endsWith('.woff2')),
    (route) => route.abort(),
  );
  await page.goto(indexPageUrl() + hash);
}

test('the parser inserts the table hidden and the tree shown', async ({
  page,
}) => {
  await recordInsertions(page, ['table-wrapper', 'hierarchical-wrapper']);

  await openFolderWithoutSearch(page);

  const seen = await insertions(page);
  expect(seen['table-wrapper'].display).toBe('none');
  expect(seen['table-wrapper'].trail).toEqual(['All resources', ...FOLDER]);
  expect(seen['hierarchical-wrapper'].display).not.toBe('none');
});

test('the first-paint trail drops the search and decodes the segments',
  async ({ page }) => {
    await recordInsertions(page, ['table-wrapper']);

    /* `%31%39` decodes to `19`. */
    await openFolderWithoutSearch(page, {
      hash: '#/hg%31%39/legacy?q=phast',
    });

    const seen = await insertions(page);
    expect(seen['table-wrapper'].trail)
      .toEqual(['All resources', ...FOLDER]);
  });

/**
 * Wait until the tree module has run its first `applyHash`.
 *
 * The module removes the class of the first paint right after that
 * call, so the class is the signal. A row of the listing is not: the
 * rows come from the search module, which some of these tests block.
 */
async function waitForTreeModule(page: Page): Promise<void> {
  await expect(page.locator('html')).not.toHaveClass(/tree-address/);
}

test('the tree stays where the first paint put it', async ({ page }) => {
  await recordInsertions(page, ['hierarchical-wrapper']);

  await openFolderWithoutSearch(page, { fonts: false });

  await waitForTreeModule(page);

  const seen = await insertions(page);
  const wrapper = page.locator('#hierarchical-wrapper');
  await expect(wrapper).toBeVisible();
  const box = await wrapper.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.y).toBe(seen['hierarchical-wrapper'].top);
});

test('the module draws the trail with the walked-up folder', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_BROWSE_GRR);
  await page.goto(indexPageUrl() + '#/hg19/no-such-folder');

  await waitForTreeModule(page);

  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'hg19']);
});

test('"Table view" shows the table after a folder address', async ({
  page,
}) => {
  await serveGrr(page, FIXTURE_BROWSE_GRR);
  await page.goto(indexPageUrl() + FOLDER_HASH);
  await expect(page.locator('#hierarchical-list .hv-row').first())
    .toBeVisible();

  await page.locator('#table-view-btn').click();

  await expect(page.locator('#table-wrapper')).toBeVisible();
  await expect(page.locator('#hierarchical-wrapper')).toBeHidden();
});

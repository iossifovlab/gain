import { expect, test, type Page } from '@playwright/test';

import { breadcrumbLink, breadcrumbTrail } from '../breadcrumb';
import { COVERAGE_RESOURCE, FIXTURE_GRR } from '../fixtures';
import { indexPageUrl, infoPageUrl, serveGrr } from '../serving';

/**
 * A resource page's way out, driven in a browser.
 *
 * `core/tests/small/templates/test_page_breadcrumb.py` pins what the
 * page renders: the trail, and the address each crumb carries. What it
 * cannot pin is that the *index page* understands that address -- the
 * hash grammar is a contract between two pages, and a template test sees
 * one of them -- nor that the copy icon reaches the clipboard. That is
 * what this file is for.
 *
 * The Coverage GRR, because it is the one fixture with pages inside its
 * resource directories: the browse GRR is published by `repo-index`,
 * which writes none. `scores/coverage` is two segments deep, which is
 * enough for a middle crumb.
 */

/** The fragment the page is currently addressed by, `''` when bare. */
function hashOf(page: Page): string {
  return new URL(page.url()).hash;
}

test.beforeEach(async ({ page }) => {
  await serveGrr(page, FIXTURE_GRR);
  await page.goto(infoPageUrl(COVERAGE_RESOURCE));
});

test('the header shows the trail down to the resource', async ({ page }) => {
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'scores', 'coverage']);
});

test('a crumb opens its folder in the hierarchical view', async ({
  page,
}) => {
  await breadcrumbLink(page, 'scores').click();

  /* A real navigation to a different document, not a fragment move: the
   * index page loads at the folder the crumb named, and its own trail
   * shows it. That last assertion is the contract being tested -- the
   * address the resource page wrote is one the index page reads. */
  await expect(page).toHaveURL(indexPageUrl() + '#/scores');
  await expect(page.locator('#hierarchical-wrapper')).toBeVisible();
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'scores']);
  expect(hashOf(page)).toBe('#/scores');
});

/**
 * Chromium asks before a page may read or write the clipboard, and a
 * headless run has nobody to answer, so the permission is granted up
 * front.
 */
test.describe('copying the resource id', () => {
  test.use({ permissions: ['clipboard-read', 'clipboard-write'] });

  /** The copy icon beside the id in the resource table. */
  function copyIcon(page: Page) {
    return page.locator('#resource-table .copy-icon');
  }

  test('clicking the icon copies the id and shows a check', async ({
    page,
  }) => {
    await copyIcon(page).click();

    await expect(copyIcon(page)).toHaveText('check');
    expect(await page.evaluate(() => navigator.clipboard.readText()))
      .toBe(COVERAGE_RESOURCE);
  });
});

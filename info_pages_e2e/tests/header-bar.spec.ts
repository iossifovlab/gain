import { expect, test, type Page } from '@playwright/test';

import { breadcrumbTrail } from '../breadcrumb';
import { COVERAGE_RESOURCE, FIXTURE_GRR } from '../fixtures';
import { GRR_ORIGIN, indexPageUrl, infoPageUrl, serveGrr } from '../serving';

/**
 * The one header bar every GRR info page carries (gain#2483).
 *
 * The trail sits on the left and the About link on the right, at the
 * same place on every page, so a click on a crumb does not move the
 * trail down the screen. The about page carries the trail and no About
 * link.
 *
 * The Coverage GRR, because it is the one fixture with every kind of
 * page: `repo-info` writes the index page, the about page, and a
 * resource page and a statistics page inside `scores/coverage`.
 */

/** The top edge of the trail, in CSS pixels from the top of the page. */
async function trailTop(page: Page): Promise<number> {
  const box = await page.locator('#page-header #breadcrumb').boundingBox();
  expect(box).not.toBeNull();
  return box!.y;
}

/** The left edge of the About link, in CSS pixels. */
async function aboutLeft(page: Page): Promise<number> {
  const box = await page.locator('#page-header #nav-about').boundingBox();
  expect(box).not.toBeNull();
  return box!.x;
}

/** A page that shows the About link, and the trail it shows. */
const PAGES_WITH_ABOUT: { name: string, url: string, trail: string[] }[] = [
  {
    name: 'the index page at a folder',
    url: indexPageUrl() + '#/scores',
    trail: ['All resources', 'scores'],
  },
  {
    name: 'the index page in the table view',
    url: indexPageUrl(),
    trail: ['All resources'],
  },
  {
    name: 'the resource page',
    url: infoPageUrl(COVERAGE_RESOURCE),
    trail: ['All resources', 'scores', 'coverage'],
  },
  {
    name: 'the statistics page',
    url: `${GRR_ORIGIN}${COVERAGE_RESOURCE}/statistics/index.html`,
    trail: ['All resources', 'scores', 'coverage', 'statistics'],
  },
];

test.beforeEach(async ({ page }) => {
  await serveGrr(page, FIXTURE_GRR);
});

test('every page puts the trail and About at the same place', async ({
  page,
}) => {
  const tops: number[] = [];
  const lefts: number[] = [];
  for (const { name, url, trail } of PAGES_WITH_ABOUT) {
    await page.goto(url);
    await expect(breadcrumbTrail(page), name).resolves.toEqual(trail);
    await expect(page.locator('#page-header #nav-about'), name)
      .toBeVisible();
    tops.push(await trailTop(page));
    lefts.push(await aboutLeft(page));
  }

  await page.goto(`${GRR_ORIGIN}about.html`);
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'About']);
  await expect(page.locator('#nav-about')).toHaveCount(0);
  tops.push(await trailTop(page));

  expect(new Set(tops).size, `trail tops ${tops}`).toBe(1);
  expect(new Set(lefts).size, `About lefts ${lefts}`).toBe(1);
});

test('the about page climbs to the root of the tree', async ({ page }) => {
  await page.goto(`${GRR_ORIGIN}about.html`);

  await page.locator('#breadcrumb a.breadcrumb-item').click();

  await expect(page).toHaveURL(indexPageUrl() + '#/');
  await expect(page.locator('#hierarchical-wrapper')).toBeVisible();
});

test('the table view redraws the trail as the root alone', async ({
  page,
}) => {
  await page.goto(indexPageUrl() + '#/scores');
  await expect(breadcrumbTrail(page)).resolves.toEqual(
    ['All resources', 'scores']);

  await page.locator('#table-view-btn').click();

  await expect(page.locator('#table-wrapper')).toBeVisible();
  await expect(breadcrumbTrail(page)).resolves.toEqual(['All resources']);
  await expect(page.locator('#breadcrumb a')).toHaveCount(0);
});

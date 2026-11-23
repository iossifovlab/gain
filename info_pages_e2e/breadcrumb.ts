import { type Page } from '@playwright/test';

/**
 * Locators for the breadcrumb trail in the header bar of a page.
 *
 * Every info page renders its trail from a template into its header,
 * and the index page's tree module redraws it from script (gain#1477,
 * gain#2483). One definition of how to read a trail, so the specs agree
 * about what a crumb is.
 */

/**
 * The breadcrumb link for `name`.
 *
 * Only the crumbs above the current one are links -- the last is a span
 * -- so this locates something clickable by construction. Exact, for the
 * same reason a folder row is looked up exactly: a substring match on a
 * trail containing both `hg38` and `hg38_extra` would be answered by
 * either.
 */
export function breadcrumbLink(page: Page, name: string) {
  return page.locator('#breadcrumb a.breadcrumb-item')
    .filter({ has: page.getByText(name, { exact: true }) });
}

/**
 * The breadcrumb trail, outermost crumb first.
 *
 * `allTextContents` rather than `allInnerTexts`: the latter reports text
 * as *rendered*, collapsing whitespace runs and reading nothing at all
 * from a hidden element. This reads what the page actually set.
 *
 * The separators are excluded by selecting the crumbs themselves, so the
 * result is the trail and not the trail interleaved with "/".
 */
export function breadcrumbTrail(page: Page): Promise<string[]> {
  return page.locator('#breadcrumb .breadcrumb-item').allTextContents();
}

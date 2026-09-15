import { type Page } from '@playwright/test';

/**
 * Locators for a breadcrumb trail, wherever one is drawn.
 *
 * The index page's hierarchical view renders its trail from script, and
 * a resource page renders the same markup -- same element id, same
 * classes -- from a template in its header (gain#1477). One definition
 * of how to read a trail, so the two specs agree about what a crumb is.
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
 * from a hidden element -- and the index page's breadcrumb is hidden
 * whenever the table view is showing. This reads what the page actually
 * set.
 *
 * The separators are excluded by selecting the crumbs themselves, so the
 * result is the trail and not the trail interleaved with "/".
 */
export function breadcrumbTrail(page: Page): Promise<string[]> {
  return page.locator('#breadcrumb .breadcrumb-item').allTextContents();
}

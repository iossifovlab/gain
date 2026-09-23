import { type Locator, type Page } from '@playwright/test';

/**
 * Locators for a resource page's statistics tables.
 *
 * One definition of "the table a heading introduces", shared by every
 * spec that drives one, so they agree about how a table is found.
 */

/**
 * The table the heading named `heading` introduces.
 *
 * By heading rather than by position, because the page carries other
 * tables -- Files, for one -- and a positional selector would keep
 * matching after a template moved the sections around, just against the
 * wrong table.
 */
export function tableAfterHeading(page: Page, heading: string): Locator {
  return page
    .getByRole('heading', { name: heading, exact: true })
    .locator('xpath=following::table[1]');
}

/** One of `table`'s column headers, by its visible text. */
export function columnHeader(table: Locator, name: string): Locator {
  return table.getByRole('columnheader', { name });
}

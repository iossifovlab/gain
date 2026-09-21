import { test, expect, Page } from '@playwright/test';
import { GRR_INVENTORY_DRIFT, expectInventoryCountText, expectInventoryOptionCount } from '../utils';

// Contract tests for the live-GRR inventory-pin helpers (gain#1551). They
// render the pinned element themselves, so they need no backend and prove
// exactly one thing: which failures claim "GRR inventory drift" and which
// do not.

const WHAT = 'type filter gene_score';
const PIN = 10;

// A DOM that never changes is settled at the first poll; a DOM that changes
// mid-check does so at 100 ms and needs the budget to outlast it.
const STATIC = { timeout: 250 };
const MID_CHECK = { timeout: 1000 };

async function renderCounter(page: Page, text: string): Promise<void> {
  await page.setContent(`<span id="resource-count">${text}</span>`);
}

async function renderOptions(page: Page, count: number): Promise<void> {
  await page.setContent(`<select id="select-genome">${'<option></option>'.repeat(count)}</select>`);
}

test.describe('expectInventoryCountText', () => {
  const counter = (page: Page): ReturnType<Page['locator']> => page.locator('#resource-count');

  test('a counter that matches its pin passes', async({ page }) => {
    await renderCounter(page, `${PIN} resources`);

    await expectInventoryCountText(counter(page), PIN, WHAT);
  });

  test('the right shape and a different number is drift, quoting both', async({ page }) => {
    await renderCounter(page, '11 resources');

    const failure = expectInventoryCountText(counter(page), PIN, WHAT, STATIC);

    await expect(failure).rejects.toThrow(`${GRR_INVENTORY_DRIFT}: ${WHAT} -- pinned ${PIN}, received 11 resources`);
  });

  for (const text of ['loading…', '0 resources']) {
    test(`a counter reading "${text}" is a UI failure, not drift`, async({ page }) => {
      await renderCounter(page, text);

      const failure = expectInventoryCountText(counter(page), PIN, WHAT, STATIC);

      await expect(failure).rejects.toThrow();
      await expect(failure).rejects.not.toThrow(GRR_INVENTORY_DRIFT);
    });
  }

  test('a counter that vanishes mid-check is a UI failure, not drift', async({ page }) => {
    await renderCounter(page, '11 resources');
    await page.evaluate(() => setTimeout(() => {
      document.querySelector('#resource-count')?.remove();
    }, 100));

    const failure = expectInventoryCountText(counter(page), PIN, WHAT, MID_CHECK);

    // Playwright's own failure, within the helper's budget -- not a later,
    // unrelated timeout from reading the vanished element.
    await expect(failure).rejects.toThrow(/Timed out 1000ms waiting for/);
    await expect(failure).rejects.not.toThrow(GRR_INVENTORY_DRIFT);
  });
});

test.describe('expectInventoryOptionCount', () => {
  const options = (page: Page): ReturnType<Page['locator']> => page.locator('#select-genome option');

  test('an option list that matches its pin passes', async({ page }) => {
    await renderOptions(page, 3);

    await expectInventoryOptionCount(options(page), 3, 'genome selector');
  });

  test('a populated list of a different length is drift, quoting both', async({ page }) => {
    await renderOptions(page, 3);

    const failure = expectInventoryOptionCount(options(page), 7, 'genome selector', STATIC);

    await expect(failure).rejects.toThrow(`${GRR_INVENTORY_DRIFT}: genome selector -- pinned 7, received 3`);
  });

  test('drift quotes the settled count, not the first one rendered', async({ page }) => {
    await renderOptions(page, 1);
    // The rest of the list lands after the first entry, as a component
    // rendering page by page would do.
    await page.evaluate(() => setTimeout(() => {
      for (let i = 0; i < 7; i++) {
        document.querySelector('#select-genome')?.appendChild(document.createElement('option'));
      }
    }, 100));

    const failure = expectInventoryOptionCount(options(page), 7, 'genome selector', MID_CHECK);

    await expect(failure).rejects.toThrow('pinned 7, received 8');
  });

  test('an option list that never populated is a UI failure, not drift', async({ page }) => {
    await renderOptions(page, 0);

    const failure = expectInventoryOptionCount(options(page), 7, 'genome selector', STATIC);

    await expect(failure).rejects.toThrow();
    await expect(failure).rejects.not.toThrow(GRR_INVENTORY_DRIFT);
  });

  test('an option list that empties mid-check is a UI failure, not drift', async({ page }) => {
    await renderOptions(page, 2);
    await page.evaluate(() => setTimeout(() => {
      document.querySelectorAll('#select-genome option').forEach(option => option.remove());
    }, 100));

    const failure = expectInventoryOptionCount(options(page), 7, 'genome selector', MID_CHECK);

    await expect(failure).rejects.toThrow(/Timed out 1000ms waiting for/);
    await expect(failure).rejects.not.toThrow(GRR_INVENTORY_DRIFT);
  });
});

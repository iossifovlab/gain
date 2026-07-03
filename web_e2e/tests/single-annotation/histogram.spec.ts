import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import * as utils from '../../utils';
import { customDefaultPipeline, caddListPipeline, clinvarListPipeline } from './helpers';

test.describe('Histogram visibility and red markers', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
  });

  test('domain-preserving value shows the histogram with one red marker and percentage', async({ page }) => {
    // gnomad_v4_exome_ALL_af uses the max aggregator -> preserves_domain true,
    // so the aggregated value sits on the histogram and the marker is shown.
    await customDefaultPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await page.locator('.switch').click();

    const container = page.locator('.attribute-container').filter({
      has: page.locator('.attribute-header', { hasText: 'gnomad_v4_exome_ALL_af' })
    });
    await expect(container.locator('app-number-histogram')).toBeVisible();
    await expect(container.locator('.single-score-marker')).toHaveCount(1);
    await expect(container.locator('.percentage-text')).toBeVisible();
  });

  test('list-aggregated numeric value hides the histogram but still lists the values', async({ page }) => {
    // The list aggregator does not preserve the score domain
    // (preserves_domain false), so the value cannot be placed on the
    // distribution and the histogram (with its markers) is hidden.
    await caddListPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 11797000');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await page.locator('.switch').click();

    const container = page.locator('.attribute-container').filter({
      has: page.locator('.attribute-header', { hasText: 'cadd_raw_list' })
    });
    await expect(container.locator('.value-grid-cell').first()).toBeVisible();
    await expect(container.locator('app-number-histogram')).not.toBeVisible();
    await expect(container.locator('.single-score-marker')).toHaveCount(0);
    expect(await container.locator('.value-grid-cell').count()).toBeGreaterThan(1);
  });

  test('list-aggregated single categorical value hides the histogram but shows the value', async({ page }) => {
    // CLNSIG is aggregated with the list aggregator (preserves_domain false),
    // so its categorical histogram is hidden while the value is still shown.
    await customDefaultPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await page.locator('.switch').click();

    const container = page.locator('.attribute-container').filter({
      has: page.locator('.attribute-header', { hasText: 'CLNSIG' })
    });
    await expect(container.locator('.value-result')).toBeVisible();
    await expect(container.locator('app-categorical-histogram')).not.toBeVisible();
    await expect(container.locator('.single-score-marker')).toHaveCount(0);
  });

  test('list-aggregated categorical array hides the histogram but still lists the values', async({ page }) => {
    await clinvarListPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796000 11800000');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await page.locator('.switch').click();

    const container = page.locator('.attribute-container').filter({
      has: page.locator('.attribute-header', { hasText: 'clnsig_list' })
    });
    await expect(container.locator('.value-grid-cell').first()).toBeVisible();
    await expect(container.locator('app-categorical-histogram')).not.toBeVisible();
    await expect(container.locator('.single-score-marker')).toHaveCount(0);
    expect(await container.locator('.value-grid-cell').count()).toBeGreaterThan(1);
  });
});

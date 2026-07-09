import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import { SingleAnnotation } from '../../pages/single-annotation.page';
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
    const singleAnnotation = new SingleAnnotation(page);
    // gnomad_v4_exome_ALL_af uses the max aggregator -> preserves_domain true,
    // so the aggregated value sits on the histogram and the marker is shown.
    await customDefaultPipeline(page);
    await singleAnnotation.annotate('chr1 11796321 G A');
    await singleAnnotation.toggleFullReport();

    const container = singleAnnotation.attributeContainer('gnomad_v4_exome_ALL_af');
    await expect(container.locator('app-number-histogram')).toBeVisible();
    await expect(container.locator('.single-score-marker')).toHaveCount(1);
    await expect(container.locator('.percentage-text')).toBeVisible();
  });

  test('list-aggregated numeric value hides the histogram but still lists the values', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    // The list aggregator does not preserve the score domain
    // (preserves_domain false), so the value cannot be placed on the
    // distribution and the histogram (with its markers) is hidden.
    await caddListPipeline(page);
    await singleAnnotation.annotate('chr1 11796321 11797000');
    await singleAnnotation.toggleFullReport();

    const container = singleAnnotation.attributeContainer('cadd_raw_list');
    await expect(container.locator('.value-grid-cell').first()).toBeVisible();
    await expect(container.locator('app-number-histogram')).not.toBeVisible();
    await expect(container.locator('.single-score-marker')).toHaveCount(0);
    expect(await container.locator('.value-grid-cell').count()).toBeGreaterThan(1);
  });

  test('list-aggregated single categorical value hides the histogram but shows the value', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    // CLNSIG is aggregated with the list aggregator (preserves_domain false),
    // so its categorical histogram is hidden while the value is still shown.
    await customDefaultPipeline(page);
    await singleAnnotation.annotate('chr1 11796321 G A');
    await singleAnnotation.toggleFullReport();

    const container = singleAnnotation.attributeContainer('CLNSIG');
    await expect(container.locator('.value-result')).toBeVisible();
    await expect(container.locator('app-categorical-histogram')).not.toBeVisible();
    await expect(container.locator('.single-score-marker')).toHaveCount(0);
  });

  test('list-aggregated categorical array hides the histogram but still lists the values', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await clinvarListPipeline(page);
    await singleAnnotation.annotate('chr1 11796000 11800000');
    await singleAnnotation.toggleFullReport();

    const container = singleAnnotation.attributeContainer('clnsig_list');
    await expect(container.locator('.value-grid-cell').first()).toBeVisible();
    await expect(container.locator('app-categorical-histogram')).not.toBeVisible();
    await expect(container.locator('.single-score-marker')).toHaveCount(0);
    expect(await container.locator('.value-grid-cell').count()).toBeGreaterThan(1);
  });
});

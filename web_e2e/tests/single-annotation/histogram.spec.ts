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

  test('list-aggregated numeric value shows the histogram with one red marker per value', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    // Each element of a list output is an original score value, so the list
    // aggregator preserves the score domain (element-wise) and every value
    // gets its own marker on the histogram.
    await caddListPipeline(page);
    await singleAnnotation.annotate('chr1 11796321 11797000');
    await singleAnnotation.toggleFullReport();

    const container = singleAnnotation.attributeContainer('cadd_raw_list');
    await expect(container.locator('.value-grid-cell').first()).toBeVisible();
    await expect(container.locator('app-number-histogram')).toBeVisible();
    const valueCount = await container.locator('.value-grid-cell').count();
    expect(valueCount).toBeGreaterThan(1);
    await expect(container.locator('.single-score-marker')).toHaveCount(valueCount);
  });

  test('list-aggregated single categorical value shows the histogram with a marker for the shown value', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    // CLNSIG is aggregated with the list aggregator, which preserves the
    // score domain element-wise, so its categorical histogram is shown.
    // The report shows a scalar or an empty list in .value-result: a scalar
    // gets one marker, an empty list gets none.
    await customDefaultPipeline(page);
    await singleAnnotation.annotate('chr1 11796321 G A');
    await singleAnnotation.toggleFullReport();

    const container = singleAnnotation.attributeContainer('CLNSIG');
    const valueResult = container.locator('.value-result');
    await expect(valueResult).toBeVisible();
    await expect(container.locator('app-categorical-histogram')).toBeVisible();
    const shownValue = (await valueResult.innerText()).trim();
    const expectedMarkers = shownValue === '' ? 0 : 1;
    await expect(container.locator('.single-score-marker')).toHaveCount(expectedMarkers);
  });

  test('list-aggregated categorical array shows the histogram with one red marker per value', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await clinvarListPipeline(page);
    await singleAnnotation.annotate('chr1 11796000 11800000');
    await singleAnnotation.toggleFullReport();

    const container = singleAnnotation.attributeContainer('clnsig_list');
    await expect(container.locator('.value-grid-cell').first()).toBeVisible();
    await expect(container.locator('app-categorical-histogram')).toBeVisible();
    const valueCount = await container.locator('.value-grid-cell').count();
    expect(valueCount).toBeGreaterThan(1);
    await expect(container.locator('.single-score-marker')).toHaveCount(valueCount);
  });
});

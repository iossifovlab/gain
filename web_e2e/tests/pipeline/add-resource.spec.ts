import { test, expect } from '@playwright/test';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Add resource to pipeline tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should open New resource dialog with correct header and first step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await expect(page.locator('mat-dialog-container')).toBeVisible();
    await expect(page.locator('#modal-header')).toHaveText('New resource');
    await expect(page.locator('#resource-type')).toBeVisible();
    await expect(page.locator('#resource-search-input')).toBeVisible();
    await expect(page.locator('#resource-count')).toBeVisible();
  });

  test('should display matching resources after typing in search input', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('CADD'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=CADD'), {timeout: 30000}
      )
    ]);

    await expect(page.locator('#resource-count')).toHaveText('2 resources');
    await expect(page.getByTitle('hg38/scores/CADD_v1.7')).toBeVisible();
    await expect(page.getByTitle('hg38/scores/dbNSFP4.9a')).toBeVisible();
  });

  test('should keep searching resources after an invalid search value', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    // An invalid search value returns a 500 and surfaces an error.
    await Promise.all([
      page.locator('#resource-search-input').fill('"unclosed'),
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search') && resp.status() === 500, {timeout: 30000}
      )
    ]);
    await expect(page.locator('#resource-input-form .error-message').nth(0)).toHaveText('Invalid search value');

    // Typing a valid value must still trigger a search. Regression: the search
    // stream used to terminate on the first error, so later searches never fired.
    await Promise.all([
      page.locator('#resource-search-input').fill('CADD'),
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=CADD') && resp.status() === 200, {timeout: 30000}
      )
    ]);

    await expect(page.locator('#resource-input-form .error-message').nth(0)).toHaveText('');
    await expect(page.locator('#resource-count')).toHaveText('2 resources');
    await expect(page.getByTitle('hg38/scores/CADD_v1.7')).toBeVisible();
  });

  test('should load more resources when scrolling to the bottom of the list', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    // The unfiltered list is paginated; the first page renders on open.
    const rows = page.locator('#resource-list .resource-full-id');
    await expect(rows.first()).toBeVisible();
    const initialCount = await rows.count();

    // Scrolling to the bottom brings the load-page indicator into view, which
    // triggers the IntersectionObserver to fetch and append the next page.
    await page.locator('.resource-list-wrapper').evaluate((el: HTMLElement) => el.scrollTo(0, el.scrollHeight));

    await expect.poll(() => rows.count(), { timeout: 30000 }).toBeGreaterThan(initialCount);
  });

  test('should filter resources by resource type', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();
    await expect(page.locator('#resource-count')).toHaveText('259 resources');
    await page.locator('#resource-type mat-select').click();
    await page.locator('mat-option').filter({ hasText: 'gene_score' }).click();
    await expect(page.locator('#resource-count')).toHaveText('10 resources');
  });

  test('should navigate past select annotator step after clicking continue', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('"CADD_v1.7"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22CADD_v1.7%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector('[id="hg38/scores/CADD_v1.7-continue-button"]', { state: 'visible', timeout: 15000 });
    await page.locator('[id$="-continue-button"]').first().click();

    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });
    await expect(
      page.locator('.mat-horizontal-stepper-content-current').locator('.annotator-display-text')
    ).toContainText('allele_score_annotator');
  });

  test('should navigate back to previous step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('"CADD_v1.7"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22CADD_v1.7%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector('[id="hg38/scores/CADD_v1.7-continue-button"]', { state: 'visible', timeout: 15000 });
    await page.locator('[id$="-continue-button"]').first().click();

    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });

    await page.getByRole('button', { name: 'Back' }).click();

    await expect(page.locator('#annotator-input-form')).toBeVisible();
  });

  test('should check selected data in summary panel', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await page.locator('#resource-search-input').fill('"gene_properties/gene_scores/GTEx_V11_RNAexpression"');
    await Promise.all([
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search query
      page.waitForResponse(
        resp => resp.url().includes(
          'api/resources/search?search=%22gene_properties/gene_scores/GTEx_V11_RNAexpression%22'
        ), {timeout: 30000}
      )
    ]);
    await page.waitForSelector(
      '[id="gene_properties/gene_scores/GTEx_V11_RNAexpression-continue-button"]',
      { state: 'visible', timeout: 15000 }
    );
    await page.locator('[id="gene_properties/gene_scores/GTEx_V11_RNAexpression-continue-button"]').click();

    const summary = page.locator('.mat-horizontal-stepper-content-current');
    // configure annotator step
    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });
    await expect(summary.locator('.resource-type-display-text').nth(0))
      .toHaveText('resource type gene_score');
    await expect(summary.locator('.resource-type-display-text').nth(1))
      .toHaveText('resource id gene_properties/gene_scores/GTEx_V11_RNAexpression');
    await expect(summary.locator('.annotator-display-text')).toHaveText('annotatorgene_score_annotator');

    // advance to attribute step
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
    await page.getByRole('button', { name: 'Next' }).click();

    // // attribute step
    await expect(summary.locator('.resource-type-display-text').nth(0))
      .toHaveText('resource type gene_score');
    await expect(summary.locator('.resource-type-display-text').nth(1))
      .toHaveText('resource id gene_properties/gene_scores/GTEx_V11_RNAexpression');
    await expect(summary.locator('.annotator-display-text')).toHaveText('annotatorgene_score_annotator');
    await expect(summary.locator('.resources-display-text')).toHaveText('input_gene_list\ngene_list');
  });

  test('should complete workflow via finish with defaults and append YAML to editor', async({ page }) => {
    await customDefaultPipeline(page);

    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('"hg19/scores/AlphaMissense"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22hg19/scores/AlphaMissense%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector('[id="hg19/scores/AlphaMissense-finish-button"]', { state: 'visible', timeout: 15000 });

    await Promise.all([
      page.locator('[id="hg19/scores/AlphaMissense-finish-button"]').click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/validate')),
    ]);

    /* eslint-disable */
    const value = await page.evaluate(() => {
      return (window as any).monaco.editor.getModels()[0].getValue();
    });
    /* eslint-enable */

    expect(value).toContain('resource_id: hg19/scores/AlphaMissense');
  });

  test('should complete full workflow via continue and append YAML to editor', async({ page }) => {
    await customDefaultPipeline(page);

    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('"hg19/scores/AlphaMissense"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22hg19/scores/AlphaMissense%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector(
      '[id="hg19/scores/AlphaMissense-continue-button"]',
      { state: 'visible', timeout: 15000 }
    );
    await page.locator('[id="hg19/scores/AlphaMissense-continue-button"]').click();

    // resource workflow auto-selects the annotator and navigates to configure step
    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });
    // resource_id is pre-filled from the selected resource, so Next is enabled immediately
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.getByRole('button', { name: 'Finish' })).toBeVisible({ timeout: 10000 });

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/editor/annotator_yaml')),
    ]);

    /* eslint-disable */
    const value = await page.evaluate(() => {
      return (window as any).monaco.editor.getModels()[0].getValue();
    });
    /* eslint-enable */

    expect(value).toContain('resource_id: hg38/scores/CADD_v1.7');
  });

  test('should disable New resource button when pipeline config is invalid', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await utils.typeInPipelineEditor(page, 'preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });

    await expect(page.locator('#pipeline-actions').locator('#add-resource-button')).toBeDisabled();
  });

  test('should open resource details in new tab', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('"CADD_v1.7"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22CADD_v1.7%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector(
      '[id="hg38/scores/CADD_v1.7-resource-details-button"]',
      { state: 'visible', timeout: 15000 }
    );

    const [popup] = await Promise.all([
      page.context().waitForEvent('page'),
      page.locator('[id="hg38/scores/CADD_v1.7-resource-details-button"] a').click(),
    ]);

    await popup.waitForLoadState('domcontentloaded');
    expect(popup.url()).toContain('/hg38/scores/CADD_v1.7/index.html');
  });
});

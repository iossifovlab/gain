import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';
import { ResourceDialog } from '../../pages/annotator.dialog';

test.describe('Add resource to pipeline tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
  });

  test('should open New resource dialog with correct header and first step', async({ page }) => {
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await expect(resourceModal.container).toBeVisible();
    await expect(resourceModal.header).toHaveText('New resource');
    await expect(resourceModal.resourceTable).toBeVisible();
    await expect(resourceModal.resourceSearch).toBeVisible();
    await expect(resourceModal.resourceCount).toBeVisible();
  });

  test('should display matching resources after typing in search input', async({ page }) => {
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await resourceModal.searchResource('CADD');

    await expect(resourceModal.resourceCount).toHaveText('2 resources');
    await expect(page.getByTitle('hg38/scores/CADD_v1.7')).toBeVisible();
    await expect(page.getByTitle('hg38/scores/dbNSFP4.9a')).toBeVisible();
  });

  test('should keep searching resources after an invalid search value', async({ page }) => {
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    // An invalid search value returns a 500 and surfaces an error.
    await resourceModal.searchResource('"unclosed');
    await expect(resourceModal.resourceSearchError).toHaveText('Invalid search value');

    // Typing a valid value must still trigger a search. Regression: the search
    // stream used to terminate on the first error, so later searches never fired.
    await resourceModal.searchResource('CADD');

    await expect(resourceModal.resourceSearchError).toHaveText('');
    await expect(resourceModal.resourceCount).toHaveText('2 resources');
    await expect(page.getByTitle('hg38/scores/CADD_v1.7')).toBeVisible();
  });

  test('should load more resources when scrolling to the bottom of the list', async({ page }) => {
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

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
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();
    await expect(resourceModal.resourceCount).toHaveText('259 resources');
    await resourceModal.selectResourceType('gene_score');
    await expect(resourceModal.resourceCount).toHaveText('10 resources');
  });

  test('should navigate past select annotator step after clicking continue', async({ page }) => {
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await resourceModal.searchResource('"CADD_v1.7"');

    await resourceModal.getResourceContinueButton('hg38/scores/CADD_v1.7').click();

    await expect(resourceModal.resourcesContent).toBeVisible({ timeout: 15000 });
    await expect(
      page.locator('.mat-horizontal-stepper-content-current').locator('.annotator-display-text')
    ).toContainText('allele_score_annotator');
  });

  test('should navigate back to previous step', async({ page }) => {
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await resourceModal.searchResource('"CADD_v1.7"');

    await resourceModal.getResourceContinueButton('hg38/scores/CADD_v1.7').click();

    await expect(resourceModal.resourcesContent).toBeVisible({ timeout: 15000 });

    await resourceModal.backButton.click();

    await expect(page.locator('#annotator-input-form')).toBeVisible();
  });

  test('should check selected data in summary panel', async({ page }) => {
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await resourceModal.searchResource('"gene_properties/gene_scores/GTEx_V11_RNAexpression"');

    await resourceModal.getResourceContinueButton('gene_properties/gene_scores/GTEx_V11_RNAexpression').click();

    const summary = page.locator('.mat-horizontal-stepper-content-current');
    // configure annotator step
    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });
    await expect(summary.locator('.resource-type-display-text').nth(0))
      .toHaveText('resource type gene_score');
    await expect(summary.locator('.resource-type-display-text').nth(1))
      .toHaveText('resource id gene_properties/gene_scores/GTEx_V11_RNAexpression');
    await expect(summary.locator('.annotator-display-text')).toHaveText('annotatorgene_score_annotator');

    // advance to attribute step
    await expect(resourceModal.nextButton).toBeEnabled();
    await resourceModal.next();

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

    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await resourceModal.searchResource('"hg19/scores/AlphaMissense"');

    await Promise.all([
      resourceModal.getResourceFinishButton('hg19/scores/AlphaMissense').click(),
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

    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await resourceModal.searchResource('"hg19/scores/AlphaMissense"');

    await resourceModal.getResourceContinueButton('hg19/scores/AlphaMissense').click();

    // resource workflow auto-selects the annotator and navigates to configure step
    await expect(resourceModal.resourcesContent).toBeVisible({ timeout: 15000 });
    // resource_id is pre-filled from the selected resource, so Next is enabled immediately
    await expect(resourceModal.nextButton).toBeEnabled();
    await resourceModal.next();

    await expect(resourceModal.finishButton).toBeVisible({ timeout: 10000 });

    await Promise.all([
      resourceModal.finish(),
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
    await new PipelineEditor(page).newPipeline();
    await utils.typeInPipelineEditor(page, 'preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });

    const resourceModal = new ResourceDialog(page);
    await expect(resourceModal.addResourceButton).toBeDisabled();
  });

  test('should open resource details in new tab', async({ page }) => {
    const resourceModal = new ResourceDialog(page);
    await resourceModal.open();

    await resourceModal.searchResource('"CADD_v1.7"');

    const [popup] = await Promise.all([
      page.context().waitForEvent('page'),
      resourceModal.getResourceDetailsButton('hg38/scores/CADD_v1.7').locator('a').click(),
    ]);

    await popup.waitForLoadState('domcontentloaded');
    expect(popup.url()).toContain('/hg38/scores/CADD_v1.7/index.html');
  });
});

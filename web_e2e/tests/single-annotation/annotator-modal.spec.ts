import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import { SingleAnnotation } from '../../pages/single-annotation.page';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Single annotation annotator modal', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
    await customDefaultPipeline(page);
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.annotate('chr1 11796321 G A');
  });

  test('should check if modal is available in full report mode', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.toggleFullReport();
    await expect(singleAnnotation.infoIcons).toHaveCount(4);
    await singleAnnotation.infoIcons.first().click();
    await expect(singleAnnotation.modalContent).toBeVisible();
  });

  test('should display attribute details info modal', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.infoIcons.nth(1).click();
    await expect(singleAnnotation.modalContent.locator('.attribute-header')).toHaveText('gnomad_v4_exome_ALL_af');
    await expect(singleAnnotation.modalContent.locator('.attribute-description'))
      .toHaveText('Alternate allele frequency');
    await expect(singleAnnotation.modalContent.locator('.attribute-source')).toHaveText('source: AF');
    await expect(singleAnnotation.modalContent.locator('app-number-histogram')).toBeVisible();
  });

  test('should show annotator details in info modal', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.infoIcons.nth(1).click();
    await expect(singleAnnotation.annotatorHeader).toHaveText('allele_score');
    await expect(singleAnnotation.modalContent.locator('.annotator-description')).toHaveText(
      'Annotator to use with scores that depend on allele like\nvariant frequencies, etc.\n' +
      'Mode (mode parameter, applies to VCFAllele inputs only):\n\n' +
      'allele (default): exact chrom/pos/ref/alt match.\n' +
      'region: aggregates scores for all allele lines overlapping the\n' +
      'annotatable\'s span.\n\n'+
      'Non-VCFAllele annotatables always use region aggregation.\n\n' +
      'More info\n\n' +
      'input_annotatable: normalized_allele\n\n'
    );
    await expect(singleAnnotation.modalContent.locator('.resource')).toHaveCount(1);
    await expect(singleAnnotation.modalContent.locator('.annotator-resource').first()).toHaveText(
      'hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL'
    );
  });

  test('should close info modal on Escape', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.infoIcons.first().click();
    await expect(singleAnnotation.modalContent).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(singleAnnotation.modalContent).not.toBeVisible();
  });

  test('should check annotator link', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.infoIcons.nth(1).click();
    await expect(page.getByRole('link', { name: 'More info' })).toHaveAttribute(
      'href', 'https://iossifovlab.com/gaindocs/annotation_infrastructure.html#allele-score-annotator'
    );
  });

  test('should check resource link', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.infoIcons.nth(1).click();
    await page.getByRole('link', { name: 'hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL' }).click();
    await expect(page.getByRole('link', { name: 'hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL' })).toHaveAttribute(
      'href', 'http://grr.seqpipe.org/hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL/index.html'
    );
  });
});

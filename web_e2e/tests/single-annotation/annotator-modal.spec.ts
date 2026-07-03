import { test, expect } from '@playwright/test';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Single annotation annotator modal', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await customDefaultPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
  });

  test('should check if modal is available in full report mode', async({ page }) => {
    await page.locator('.switch').click();
    await expect(page.locator('.info-icon')).toHaveCount(4);
    await page.locator('.info-icon').first().click();
    await expect(page.locator('#modal-content')).toBeVisible();
  });

  test('should display attribute details info modal', async({ page }) => {
    await page.locator('.info-icon').nth(1).click();
    await expect(page.locator('#modal-content .attribute-header')).toHaveText('gnomad_v4_exome_ALL_af');
    await expect(page.locator('#modal-content .attribute-description')).toHaveText('Alternate allele frequency');
    await expect(page.locator('#modal-content .attribute-source')).toHaveText('source: AF');
    await expect(page.locator('#modal-content app-number-histogram')).toBeVisible();
  });

  test('should show annotator details in info modal', async({ page }) => {
    await page.locator('.info-icon').nth(1).click();
    await expect(page.locator('.annotator-header')).toHaveText('allele_score');
    await expect(page.locator('#modal-content .annotator-description')).toHaveText(
      'Annotator to use with scores that depend on allele like\nvariant frequencies, etc.\n' +
      'Mode (mode parameter, applies to VCFAllele inputs only):\n\n' +
      'allele (default): exact chrom/pos/ref/alt match.\n' +
      'region: aggregates scores for all allele lines overlapping the\n' +
      'annotatable\'s span.\n\n'+
      'Non-VCFAllele annotatables always use region aggregation.\n\n' +
      'More info\n\n' +
      'input_annotatable: normalized_allele\n\n'
    );
    await expect(page.locator('#modal-content .resource')).toHaveCount(1);
    await expect(page.locator('#modal-content .annotator-resource').first()).toHaveText(
      'hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL'
    );
  });

  test('should close info modal on Escape', async({ page }) => {
    await page.locator('.info-icon').first().click();
    await expect(page.locator('#modal-content')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#modal-content')).not.toBeVisible();
  });

  test('should check annotator link', async({ page }) => {
    await page.locator('.info-icon').nth(1).click();
    await expect(page.getByRole('link', { name: 'More info' })).toHaveAttribute(
      'href', 'https://iossifovlab.com/gaindocs/annotation_infrastructure.html#allele-score-annotator'
    );
  });

  test('should check resource link', async({ page }) => {
    await page.locator('.info-icon').nth(1).click();
    await page.getByRole('link', { name: 'hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL' }).click();
    await expect(page.getByRole('link', { name: 'hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL' })).toHaveAttribute(
      'href', 'http://grr.seqpipe.org/hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL/index.html'
    );
  });
});

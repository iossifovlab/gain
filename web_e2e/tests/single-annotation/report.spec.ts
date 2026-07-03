import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import { scanCSV } from 'nodejs-polars';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Single annotation report tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
    await customDefaultPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
  });

  test('should display annotatable data in report', async({ page }) => {
    await expect(page.locator('#annotatable-chromosome')).toHaveText('chr1');
    await expect(page.locator('#annotatable-position')).toHaveText('11796321');
    await expect(page.locator('#annotatable-reference')).toHaveText('G');
    await expect(page.locator('#annotatable-alternate')).toHaveText('A');
    await expect(page.locator('#annotatable-type')).toHaveText('SUBSTITUTION');
  });

  test('should check annotators count and the first attribute', async({ page }) => {
    await expect(page.locator('.annotator')).toHaveCount(4);
    await expect(page.locator('.attribute-container')).toHaveCount(5);
    await expect(page.locator('.attribute-header').first()).toHaveText('dbSNP_RS');
    await expect(page.locator('.attribute-result').first()).toHaveText('1801133');
    await expect(page.locator('#compact-report').first()).toBeVisible();
  });

  test('should hide attribute descriptions when full report is toggled off', async({ page }) => {
    await expect(page.locator('.attribute-container .attribute-description').first()).not.toBeVisible();
    await page.locator('.switch').click();
    await expect(page.locator('.attribute-container .attribute-description').first()).toBeVisible();
    await page.locator('.switch').click();
    await expect(page.locator('.attribute-container .attribute-description').first()).not.toBeVisible();
  });

  test('should download report', async({ page }) => {
    await expect(page.locator('#download-report-button')).toBeVisible();
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#download-report-button').click();
    const download = await downloadPromise;
    const fixtureData = scanCSV(await download.path(), {truncateRaggedLines: true, sep: '\t' });
    const downloadData = scanCSV('./fixtures/chr1_11796321_G_A_report.tsv', {truncateRaggedLines: true, sep: '\t' });
    const fixtureFrame = (await fixtureData.collect()).sort('Attribute name');
    const downloadFrame = (await downloadData.collect()).sort('Attribute name');
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should clear report after selecting other pipeline', async({ page }) => {
    await page.locator('#pipelines-input').click();
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.locator('mat-option').getByText('pipeline/T2T_clinical_annotation').click();
    await PipelineEditor.waitForLoaded(page);

    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await expect(page.locator('#report')).not.toBeVisible();
  });

  test('should clear report after editing the current pipeline', async({ page }) => {
    await utils.typeInPipelineEditor(
      page,
      '- allele_score:\n' +
      '    attributes:\n' +
      '    - internal: false\n' +
      '      name: cadd_raw\n' +
      '      source: cadd_raw\n' +
      '    - internal: false\n' +
      '      name: cadd_phred\n' +
      '      source: cadd_phred\n' +
      '    input_annotatable: normalized_allele\n' +
      '    resource_id: hg19/scores/CADD\n'
    );
    await expect(page.locator('#report')).not.toBeVisible();
  });
});

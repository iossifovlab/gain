import { test, expect, Page } from '@playwright/test';
import * as utils from '../utils';
import { scanCSV } from 'nodejs-polars';

test.describe('Single annotation input tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should disable Go button when no allele is typed', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
    await expect(page.getByRole('button', { name: 'Go' })).toBeDisabled();
  });

  test('should disable Go button when allele format is invalid', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
    await page.getByPlaceholder('Type annotatable...').fill('invalid input');
    await expect(page.getByRole('button', { name: 'Go' })).toBeDisabled();
  });

  test('should enable Go button when valid allele and pipeline are selected', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await expect(page.getByRole('button', { name: 'Go' })).toBeEnabled();
  });

  test('should show validation message for invalid allele format', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('invalid input');
    await expect(page.locator('#validation-message')).toHaveText('Invalid allele format!');
  });

  test('should not show validation message for colon-separated allele', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1:11796321:G:A');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should not show validation message for arrow-separated allele', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G>A');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should not show validation message for region format', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 11800000');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should not show validation message for position-only format', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should not show validation message for dash-range format', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1:11796321-11800000');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should show examples menu when info button is clicked', async({ page }) => {
    await page.locator('#examples-button').click();
    await expect(page.getByRole('menuitem', { name: 'chr1 11796321 G A', exact: true })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'chr1:11796321:G:A', exact: true })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'chr1 11796321 11800000', exact: true })).toBeVisible();
  });

  test('should populate input when example is selected', async({ page }) => {
    await page.locator('#examples-button').click();
    await page.getByRole('menuitem', { name: 'chr1 11796321 G A', exact: true }).click();
    await expect(page.getByPlaceholder('Type annotatable...')).toHaveValue('chr1 11796321 G A');
  });

  test('should clear report when allele input changes', async({ page }) => {
    await customDefaultPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go' }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await expect(page.locator('#report')).not.toBeVisible();
  });
});

test.describe('Single annotation report tests', () => {
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

    const fixtureData = scanCSV(await download.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/chr1_11796321_G_A_report.tsv', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should clear report after selecting other pipeline', async({ page }) => {
    await page.locator('#pipelines-input').click();
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.locator('mat-option').getByText('pipeline/T2T_Clinical_annotation').click();
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
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
      'Annotator to use with scores that depend on allele like variant frequencies, etc.\n' +
      'More info\n' +
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
      'href', 'https://www.iossifovlab.com/gpfuserdocs/administration/annotation.html#allele-score'
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

test.describe('Single annotation history tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await customDefaultPipeline(page);
  });

  test('should show allele in history after annotation', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.allele-name').getByText('chr1:1265232 G>A')).toBeVisible();
  });

  test('should annotate when clicking allele from history', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill('');
    await expect(page.locator('#report')).not.toBeVisible();

    await page.locator('.allele-button').first().click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await expect(page.locator('#report')).toBeVisible();
  });

  test('should delete allele from history', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.allele-name')).toHaveCount(1);
    await page.locator('#delete-allele').click();
    await expect(page.locator('.allele-name')).toHaveCount(0);
  });

  test('should accumulate multiple alleles in history', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.allele-name')).toHaveCount(2);
  });

  test('should not duplicate allele in history when annotating the same allele multiple times', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.allele-name')).toHaveCount(1);
  });
});

test.describe('Single annotation rate limit tests - anonymous user', () => {
  test('should return 429 when rate limit is exceeded', async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');

    /* eslint-disable no-await-in-loop */
    for (let i = 0; i < 10; i++) {
      await Promise.all([
        page.getByRole('button', { name: 'Go', exact: true }).click(),
        page.waitForResponse(
          resp => resp.url().includes('api/single_allele/annotate') && resp.status() === 200, {timeout: 30000}
        )
      ]);
    }
    /* eslint-enable */

    // 11th click should fail
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    const annotateResponse = page.waitForResponse(
      resp => resp.url().includes('api/single_allele/annotate')
    );
    expect((await annotateResponse).status()).toBe(429);
  });
});

test.describe('Single annotation rate limit tests - logged in user', () => {
  test('should return 429 when rate limit is exceeded', async({ page }) => {
    // all tests for single annotation should be done with logged in user
    // as anonymous users have a very low rate limit which makes it hard not to hit the limit
    await page.goto('/', {waitUntil: 'load'});
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);

    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');

    /* eslint-disable no-await-in-loop */
    for (let i = 0; i < 10; i++) {
      await Promise.all([
        page.getByRole('button', { name: 'Go', exact: true }).click(),
        page.waitForResponse(
          resp => resp.url().includes('api/single_allele/annotate') && resp.status() === 200, {timeout: 30000}
        )
      ]);
    }
    /* eslint-enable */

    // 11th click should fail
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    const annotateResponse = page.waitForResponse(
      resp => resp.url().includes('api/single_allele/annotate')
    );
    expect((await annotateResponse).status()).toBe(429);
  });
});

async function customDefaultPipeline(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
  await expect(page.locator('#pipelines-input')).toBeEmpty();
  await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );

  await utils.typeInPipelineEditor(
    page,
    '- normalize_allele_annotator:\n' +
    '    genome: hg38/genomes/GRCh38-hg38\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/scores/dbSNP\n' +
    '    input_annotatable: normalized_allele\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL\n' +
    '    input_annotatable: normalized_allele\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/variant_frequencies/gnomAD_4.1.0/genomes/ALL\n' +
    '    input_annotatable: normalized_allele\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/scores/ClinVar_20240730\n' +
    '    input_annotatable: normalized_allele\n'
  );

  await saveResponse;

  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

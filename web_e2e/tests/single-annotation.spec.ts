import { test, expect, Page } from '@playwright/test';
import * as utils from '../utils';
import { scanCSV } from 'nodejs-polars';

test.describe('Single annotation input tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should disable Go button when no annotatable is typed', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await expect(page.getByRole('button', { name: 'Go' })).toBeDisabled();
  });

  test('should disable Go button when annotatable format is invalid', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await page.getByPlaceholder('Type annotatable...').fill('invalid input');
    await expect(page.getByRole('button', { name: 'Go' })).toBeDisabled();
  });

  test('should enable Go button when valid annotatable and pipeline are selected', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await expect(page.getByRole('button', { name: 'Go' })).toBeEnabled();
  });

  test('should show validation message for invalid annotatable format', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('invalid input');
    await expect(page.locator('#validation-message')).toHaveText('Invalid annotatable format!');
  });

  test('should not show validation message for colon-separated annotatable', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1:11796321:G:A');
    await expect(page.locator('#validation-message')).not.toBeVisible();
  });

  test('should not show validation message for arrow-separated annotatable', async({ page }) => {
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

  test('should clear report when annotatable input changes', async({ page }) => {
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
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

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

  test('should show annotatable in history after annotation', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.annotatable-link').getByText('chr1:1265232 G>A')).toBeVisible();
  });

  test('should annotate when clicking annotatable from history', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill('');
    await expect(page.locator('#report')).not.toBeVisible();

    await page.locator('.annotatable-link').first().click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await expect(page.locator('#report')).toBeVisible();
  });

  test('should delete annotatable from history', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.annotatable-cell')).toHaveCount(1);
    await page.locator('.delete-btn').first().click();
    await expect(page.locator('.annotatable-cell')).toHaveCount(0);
  });

  test('should accumulate multiple annotatable in history', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.annotatable-cell')).toHaveCount(2);
  });

  test('should not duplicate annotatable in history when annotating '+
    'the same annotatable multiple times', async({ page }) => {
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.annotatable-cell')).toHaveCount(1);
  });
});

test.describe('Single annotation annotatable formats and report features', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should display position-start and position-end for region annotatable', async({ page }) => {
    await effectAnnotatorPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 11800000');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('#position-start')).toBeVisible();
    await expect(page.locator('#position-end')).toBeVisible();
    await expect(page.locator('#position-start')).toHaveText('11796321');
    await expect(page.locator('#position-end')).toHaveText('11800000');
    await expect(page.locator('#annotatable-reference')).not.toBeVisible();
    await expect(page.locator('#annotatable-alternate')).not.toBeVisible();
  });

  test('should display position for position-only annotatable', async({ page }) => {
    await effectAnnotatorPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('#annotatable-position')).toBeVisible();
    await expect(page.locator('#annotatable-position')).toHaveText('11796321');
    await expect(page.locator('#position-start')).not.toBeVisible();
    await expect(page.locator('#position-end')).not.toBeVisible();
    await expect(page.locator('#annotatable-reference')).not.toBeVisible();
    await expect(page.locator('#annotatable-alternate')).not.toBeVisible();
  });

  test('should render effect table in full report mode', async({ page }) => {
    await effectAnnotatorPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').nth(0)).toHaveText('MTHFR:missense');
    await expect(page.locator('.compact-value-result').nth(1)).toHaveText(
      'ENST00000376590.9:MTHFR:missense:222/656(Ala->Val)'
    );
    await page.locator('.switch').click();
    await expect(page.locator('.attribute-container').nth(0).locator('app-effect-table')).toBeVisible();
    await expect(page.locator('.attribute-container').nth(1).locator('app-effect-table')).toBeVisible();
  });

  test('should render histogram in report body in full report mode', async({ page }) => {
    await customDefaultPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.attribute-container').nth(1).locator('app-histogram-wrapper')).not.toBeVisible();
    await page.locator('.switch').click();
    await expect(page.locator('.attribute-container').nth(1).locator('app-histogram-wrapper')).toBeVisible();
  });
});

test.describe('Single annotation note tests', () => {
  let email: string;
  let password: string;

  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    email = utils.getRandomString() + '@email.com';
    password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await customDefaultPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
  });

  test('should save and display a note for an annotatable', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('BRCA1 review');

    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.confirm-btn').first().click();
    await noteResponse;

    await expect(page.locator('.note-label').first()).toHaveText('BRCA1 review');
    await expect(page.locator('.note-label').first()).not.toHaveClass(/empty/);
  });

  test('should save a note by pressing Enter', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('entered via keyboard');

    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.note-input').press('Enter');
    await noteResponse;

    await expect(page.locator('.note-label').first()).toHaveText('entered via keyboard');
  });

  test('should discard edit on cancel without saving', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('should not be saved');
    await page.locator('.cancel-btn').first().click();

    await expect(page.locator('.note-input')).not.toBeVisible();
    await expect(page.locator('.note-label').first()).toHaveText('no label');
  });

  test('should clear a note when saved with empty input', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('to be cleared');
    const firstSave = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.confirm-btn').first().click();
    await firstSave;

    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('');
    const clearResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.confirm-btn').first().click();
    await clearResponse;

    await expect(page.locator('.note-label').first()).toHaveText('no label');
    await expect(page.locator('.note-label').first()).toHaveClass(/empty/);
  });

  test('should persist note after logout and login', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('persisted label');
    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.confirm-btn').first().click();
    await noteResponse;

    await Promise.all([
      page.waitForNavigation({ waitUntil: 'load' }),
      page.locator('#logout-button').click(),
    ]);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await expect(page.locator('.note-label').first()).toHaveText('persisted label');
  });

  test('should not retain note when annotatable is deleted and re-queried', async({ page }) => {
    await page.locator('.edit-btn').first().click();
    await page.locator('.note-input').fill('label that will be lost');
    const noteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/note') && resp.status() === 200
    );
    await page.locator('.confirm-btn').first().click();
    await noteResponse;
    await expect(page.locator('.note-label').first()).toHaveText('label that will be lost');

    const deleteResponse = page.waitForResponse(
      resp => resp.url().includes('/api/single_allele/history') && resp.status() === 204
    );
    await page.locator('.delete-btn').first().click();
    await deleteResponse;
    await expect(page.locator('.annotatable-cell')).toHaveCount(0);

    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.note-label').first()).toHaveText('no label');
    await expect(page.locator('.note-label').first()).toHaveClass(/empty/);
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

    // Keep quota well above the 10 requests needed to reach the throttle limit
    // so quota exhaustion never masks the throttle response.
    await utils.setCurrentQuota(page, email, 'daily_variants', 10_000);
    await utils.setCurrentQuota(page, email, 'daily_attributes', 100_000);

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
    let annotateResponse;
    await Promise.all([
      page.getByRole('button', { name: 'Go', exact: true }).click(),
      annotateResponse = page.waitForResponse(
        resp => resp.url().includes('api/single_allele/annotate')
      )
    ]);
    expect((await annotateResponse).status()).toBe(429);
  });
});

test.describe('Single annotation value type rendering', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should render str value as inline scalar in compact and full report', async({ page }) => {
    await strTypePipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').first()).toHaveText('missense');

    await page.locator('.switch').click();
    await expect(page.locator('.value-result').first()).toHaveText('missense');
    await expect(page.locator('.value-grid-container')).not.toBeVisible();
  });

  test('should render float value as formatted scalar in compact and full report', async({ page }) => {
    await floatTypePipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').first()).toHaveText('0.323');

    await page.locator('.switch').click();
    await expect(page.locator('.value-result').first()).toHaveText('0.323');
    await expect(page.locator('.value-grid-container')).not.toBeVisible();
  });

  test('should render annotatable value as its string form in compact and full report', async({ page }) => {
    await annotatableTypePipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').first()).toHaveText('chr1:11796321 G>A');

    await page.locator('.switch').click();
    await expect(page.locator('.value-result').first()).toHaveText('chr1:11796321 G>A');
    await expect(page.locator('.value-grid-container')).not.toBeVisible();
  });

  test('should render gene list object array value as single-column grid in full report', async({ page }) => {
    await effectAnnotatorPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').nth(2)).toHaveText('MTHFR');

    await page.locator('.switch').click();
    const valueGrid = page.locator('.attribute-result').nth(2).locator('.value-grid-container');
    await expect(valueGrid).toBeVisible();
    await expect(valueGrid.locator('.value-grid-header')).toHaveCount(1);
    await expect(valueGrid.locator('.value-grid-header').first()).toContainText('Value');
    await expect(valueGrid.locator('.value-grid-cell').first()).toHaveText('MTHFR');
  });

  test('should render gene effect object map value as two-column grid in full report', async({ page }) => {
    await effectAnnotatorPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').nth(0)).toHaveText('MTHFR:missense');

    await page.locator('.switch').click();
    const valueGrid = page.locator('.attribute-result').nth(0).locator('.grid-container');
    await expect(valueGrid).toBeVisible();
    await expect(valueGrid.locator('.grid-header')).toHaveCount(2);
    await expect(valueGrid.locator('.grid-header').first()).toContainText('Gene');
    await expect(valueGrid.locator('.grid-header').nth(1)).toContainText('Effect');
    await expect(valueGrid.locator('.grid-cell').first()).toHaveText('MTHFR');
    await expect(valueGrid.locator('.grid-cell').nth(1)).toHaveText('missense');
  });

  test('should render effect details object map value as four-column grid in full report', async({ page }) => {
    await effectAnnotatorPipeline(page);

    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').nth(1))
      .toHaveText('ENST00000376590.9:MTHFR:missense:222/656(Ala->Val)');

    await page.locator('.switch').click();
    const valueGrid = page.locator('.attribute-result').nth(1).locator('.grid-container');
    await expect(valueGrid).toBeVisible();
    await expect(valueGrid.locator('.grid-header')).toHaveCount(4);
    await expect(valueGrid.locator('.grid-header').first()).toContainText('Gene');
    await expect(valueGrid.locator('.grid-header').nth(1)).toContainText('Transcript');
    await expect(valueGrid.locator('.grid-header').nth(2)).toContainText('Effect');
    await expect(valueGrid.locator('.grid-header').nth(3)).toContainText('Details');
    await expect(valueGrid.locator('.grid-cell').first()).toHaveText('MTHFR');
    await expect(valueGrid.locator('.grid-cell').nth(1)).toHaveText('ENST00000376590.9');
    await expect(valueGrid.locator('.grid-cell').nth(2)).toHaveText('missense');
    await expect(valueGrid.locator('.grid-cell').nth(3)).toHaveText('222/656(Ala->Val)');
  });

  test('should render object map value as two-column grid in full report', async({ page }) => {
    await objectTypePipeline(page);

    await page.getByPlaceholder('Type annotatable...').fill('chr1 11796321 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    await expect(page.locator('.compact-value-result').nth(1))
      .toHaveText('MTHFR:2');

    await page.locator('.switch').click();
    const valueGrid = page.locator('.attribute-result').nth(1).locator('.value-grid-container');
    await expect(valueGrid).toBeVisible();
    await expect(valueGrid.locator('.value-grid-header')).toHaveCount(2);
    await expect(valueGrid.locator('.value-grid-header').first()).toContainText('Key');
    await expect(valueGrid.locator('.value-grid-header').nth(1)).toContainText('Value');
    await expect(valueGrid.locator('.value-grid-cell').first()).toHaveText('MTHFR');
    await expect(valueGrid.locator('.value-grid-cell').nth(1)).toHaveText('2');
  });
});

async function effectAnnotatorPipeline(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
  await expect(page.locator('#pipelines-input')).toBeEmpty();
  await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), { timeout: 30000 }
  );

  await utils.typeInPipelineEditor(
    page,
    '- effect_annotator:\n' +
    '    gene_models: hg38/gene_models/MANE/1.4\n' +
    '    genome: hg38/genomes/GRCh38.p14\n' +
    '    attributes:\n' +
    '    - name: gene_effects\n' +
    '      source: gene_effects\n' +
    '      internal: false\n' +
    '    - name: effect_details\n' +
    '      source: effect_details\n' +
    '      internal: false\n' +
    '    - name: gene_list\n' +
    '      source: gene_list\n' +
    '      internal: false\n'
  );

  await saveResponse;

  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

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

async function strTypePipeline(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );
  await utils.typeInPipelineEditor(
    page,
    '- effect_annotator:\n' +
    '    gene_models: hg38/gene_models/GENCODE/48/basic/ALL\n' +
    '    genome: hg38/genomes/GRCh38.p13\n' +
    '    attributes:\n' +
    '    - name: worst_effect\n' +
    '      source: worst_effect\n' +
    '      internal: false\n'
  );
  await saveResponse;
  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

async function floatTypePipeline(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );
  await utils.typeInPipelineEditor(
    page,
    '- normalize_allele_annotator:\n' +
    '    genome: hg38/genomes/GRCh38-hg38\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL\n' +
    '    input_annotatable: normalized_allele\n'
  );
  await saveResponse;
  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

async function annotatableTypePipeline(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );
  await utils.typeInPipelineEditor(
    page,
    '- normalize_allele_annotator:\n' +
    '    genome: hg38/genomes/GRCh38-hg38\n' +
    '    attributes:\n' +
    '    - name: normalized_allele\n' +
    '      source: normalized_allele\n' +
    '      internal: false\n'
  );
  await saveResponse;
  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}


async function objectTypePipeline(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );
  await utils.typeInPipelineEditor(
    page,
    '- effect_annotator:\n' +
    '   gene_models: hg38/gene_models/GENCODE/48/basic/PRI\n' +
    '   genome: hg38/genomes/GRCh38.p14\n' +
    '   attributes:\n' +
    '   - worst_effect\n' +
    '   - name: gene_list \n' +
    '     internal: true\n' +
    '- gene_score_annotator:\n' +
    '   resource_id: gene_properties/gene_scores/SFARI_gene_score_2024_Q1\n' +
    '   input_gene_list: gene_list\n' +
    '   attributes:\n' +
    '   - name: SFARI_gene_score\n' +
    '     source: SFARI Gene Score\n'
  );
  await saveResponse;
  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

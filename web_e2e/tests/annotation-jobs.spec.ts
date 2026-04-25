import { test, expect, Page } from '@playwright/test';
import * as utils from '../utils';
import { scanCSV } from 'nodejs-polars';

test.describe('Create job tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
  });

  test('should create job with vcf file', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await page.locator('#create-button').click();

    await expect(page.locator('#result')).toBeVisible();
    await expect(page.locator('#new-job-section')).toBeVisible();
    await expect(page.locator('app-job-creation')).not.toBeVisible();
  });

  test('should check if create button is disabled when no file is uploaded', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Autism_annotation');

    await expect(page.locator('#create-button')).toBeDisabled();
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await expect(page.locator('#create-button')).toBeEnabled();
  });

  test('should check if create button is enabled when no pipeline is selected', async({ page }) => {
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await expect(page.locator('#create-button')).toBeEnabled();

    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await expect(page.locator('#create-button')).toBeEnabled();
  });

  test('should check if create button is disabled when pipeline is invalid', async({ page }) => {
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await expect(page.locator('#create-button')).toBeEnabled();

    await utils.typeInPipelineEditor(page, 'invalid content');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });

    await expect(page.locator('#create-button')).toBeDisabled();
  });

  test('should check if create button is disabled when uploaded file is removed', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Autism_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await expect(page.locator('#create-button')).toBeEnabled();
    await page.locator('#delete-uploaded-file').click();
    await expect(page.locator('#create-button')).toBeDisabled();
  });

  test('should create job and then delete it', async({ page }) => {
    await createJobWithPipeline(page, 'pipeline/Clinical_annotation', 'input-vcf-file.vcf');

    const lastJobId = await page.locator('app-jobs-table').locator('.job-name').evaluate(el => el.textContent);
    await expect(page.getByText(lastJobId)).toBeVisible();

    await page.locator('.delete-icon').nth(0).click();
    await expect(page.getByText(lastJobId)).not.toBeVisible();
  });

  test('should create job with tsv file and columns selected by default', async({ page }) => {
    await customDefaultPipeline(page);

    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-tsv-file.tsv');

    await expect(page.locator('app-column-specifying')).toBeVisible();

    await page.locator('#create-button').click();
    await waitForJobStatus(page, utils.successBackgroundColor);
  });

  test('should create job with csv file and columns selected by default', async({ page }) => {
    await customDefaultPipeline(page);

    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-csv-file.csv');
    await page.waitForSelector('#table');

    await page.locator('#create-button').click();
    await waitForJobStatus(page, utils.successBackgroundColor);
  });
});

test.describe('Job details tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
  });

  test('should check job details of the first job', async({ page }) => {
    await createJobWithPipeline(page, 'pipeline/Clinical_annotation', 'input-vcf-file.vcf');

    await page.locator('.job-name').getByText('info').nth(0).click();
    await expect(page.locator('app-job-details')).toBeVisible();
    await expect(page.locator('app-job-details').locator('.owner')).not.toBeEmpty();
    await expect(page.locator('app-job-details').locator('.name')).not.toBeEmpty();
    await expect(page.locator('app-job-details').locator('.date')).not.toBeEmpty();
    await expect(page.locator('app-job-details').locator('.time')).not.toBeEmpty();
    await expect(page.locator('app-job-details').locator('.started')).not.toBeEmpty();
    await expect(page.locator('app-job-details').locator('.duration')).not.toBeEmpty();
    await expect(page.locator('app-job-details').locator('.status-label')).not.toBeEmpty();
    await expect(page.locator('app-job-details').locator('#download-input')).toBeVisible();
    await expect(page.locator('app-job-details').locator('#download-config')).toBeVisible();
    await expect(page.locator('app-job-details').locator('#download-annotated')).not.toBeVisible();
    await expect(page.locator('app-job-details').locator('#data-size')).toBeVisible();
  });

  test('should download uploaded file from job details modal', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file-reduced.vcf');
    await page.locator('#create-button').click();

    await waitForJobStatus(page, utils.successBackgroundColor);

    await page.locator('.job-name').getByText('info').nth(0).click();

    const downloadPromise = page.waitForEvent('download');
    await page.locator('app-job-details').locator('#download-input').click();
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/input-vcf-file-reduced.vcf', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should download pipeline config file', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await page.locator('#create-button').click();
    await waitForJobStatus(page, utils.successBackgroundColor);

    await page.locator('.job-name').getByText('info').nth(0).click();

    const downloadPromise = page.waitForEvent('download');
    await page.locator('app-job-details').locator('#download-config').click();
    const downloadedFile = await downloadPromise;

    const downloadData = scanCSV(await downloadedFile.path());
    const fixtureData = scanCSV('./fixtures/custom-pipeline.yaml');
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should download annotated file', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await page.locator('#create-button').click();
    await waitForJobStatus(page, utils.successBackgroundColor);

    await page.locator('.job-name').getByText('info').nth(0).click();

    const downloadPromise = page.waitForEvent('download');
    await page.locator('app-job-details').locator('#download-annotated').click();
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/job-result-1.vcf', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should check job details modal of failed job', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-csv-file.csv');

    await expect(page.locator('app-column-specifying')).toBeVisible();
    await page.locator('[id="POS-header"]').locator('mat-select').click();
    await page.getByRole('option', { name: 'vcf_like', exact: true }).click();

    await page.locator('#create-button').click();
    await waitForJobStatus(page, utils.failedBackgroundColor);

    await page.locator('.job-name').getByText('info').nth(0).click();
    await expect(page.locator('app-job-details').locator('.status-label')).toHaveText('failed');
    await expect(page.locator('app-job-details').locator('#download-input')).toBeVisible();
    await expect(page.locator('app-job-details').locator('#download-config')).toBeVisible();
    await expect(page.locator('app-job-details').locator('#download-annotated')).not.toBeVisible();
  });
});

test.describe('Jobs table tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
  });

  test('should create job and check first row', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await page.locator('#create-button').click();

    await waitForJobStatus(page, utils.successBackgroundColor);
    await expect(page.locator('.job-name').nth(0)).not.toBeEmpty();
    await expect(page.locator('.actions').nth(0)).not.toBeEmpty();
  });

  test('should download from table when annotation is success', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await page.locator('#create-button').click();
    await waitForJobStatus(page, utils.successBackgroundColor);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('.download-icon').nth(0).click();
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/job-result-2.vcf', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should check if download button is not available when job is not finished', async({ page }) => {
    await createJobWithPipeline(page, 'pipeline/T2T_Clinical_annotation', 'input-vcf-file.vcf');

    await expect(page.locator('.no-download-icon').nth(0)).toBeVisible();
  });

  test('should upload tsv file and check specify columns component content', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/GPF-SFARI_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-tsv-file.tsv');

    await expect(page.locator('app-column-specifying')).toBeVisible();
    await expect(page.locator('[id="CHROM-header"]').locator('mat-select')).toHaveText('chrom');
    await expect(page.locator('[id="POS-header"]').locator('mat-select')).toHaveText('pos');
    await expect(page.locator('[id="REF-header"]').locator('mat-select')).toHaveText('ref');
    await expect(page.locator('[id="ALT-header"]').locator('mat-select')).toHaveText('alt');

    await expect(page.locator('#instructions')).toBeVisible();

    // row 1 of input file
    await expect(page.locator('.cell').nth(0)).toHaveText('chr1');
    await expect(page.locator('.cell').nth(1)).toHaveText('85827');
    await expect(page.locator('.cell').nth(2)).toHaveText('T');
    await expect(page.locator('.cell').nth(3)).toHaveText('C');

    //row 2 of input file
    await expect(page.locator('.cell').nth(4)).toHaveText('chr1');
    await expect(page.locator('.cell').nth(5)).toHaveText('183733');
    await expect(page.locator('.cell').nth(6)).toHaveText('C');
    await expect(page.locator('.cell').nth(7)).toHaveText('T');
  });

  test('should show error message when specifying invalid combination of columns', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/GPF-SFARI_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-csv-file.csv');

    await page.locator('[id="CHROM-header"]').locator('mat-select').click();
    await page.getByRole('option', { name: 'variant', exact: true }).click();

    await expect(page.getByText('Cannot build annotatable from selected columns!')).toBeVisible();
  });
});

test.describe('Jobs validation tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
  });

  test('should check if create button is disabled when invalid file is uploaded', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Autism_annotation');

    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/invalid-input-file-format.yaml');
    await expect(page.getByText('Unsupported format!')).toBeVisible();
    await expect(page.locator('#create-button')).toBeDisabled();
    await expect(page.locator('app-column-specifying')).not.toBeVisible();
    await expect(page.locator('.separator-list')).not.toBeVisible();
  });

  test('should upload invalid vcf file', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/invalid-vcf-input-file.vcf');

    await page.locator('#create-button').click();
    await expect(page.getByText('does not have valid header')).toBeVisible();
  });

  test('should expect error message file with invalid separator is uploaded', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Autism_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/invalid-separator.csv');
    await page.locator('[id="CHROM+POS+REF+ALT-header"]').locator('mat-select').click();
    await page.getByRole('option', { name: 'chrom', exact: true }).click();
    await expect(page.getByText('Cannot build annotatable from selected columns!')).toBeVisible();
    await expect(page.locator('#create-button')).toBeDisabled();
  });

  test('should expect error message if file content is not separeted correctly', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Autism_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/wrongly-separated-row.csv');
    await page.locator('[id="CHROM,POS,REF,ALT-header"]').locator('mat-select').click();
    await page.getByRole('option', { name: 'chrom', exact: true }).click();
    await expect(page.getByText('Cannot build annotatable from selected columns!')).toBeVisible();
    await expect(page.locator('#create-button')).toBeDisabled();
  });

  test('should expect error message when no columns are specified', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Autism_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/wrongly-separated-row.csv');
    await expect(page.getByText('No columns selected!')).toBeVisible();
    await expect(page.locator('#create-button')).toBeDisabled();
  });

  test.skip('should upload file with more than 1000 variants', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Autism_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/more-than-1000.vcf');
    await page.locator('#create-button').click();
    await expect(page.getByText('Upload limit reached!')).toBeVisible();
  });
});

async function waitForJobStatus(page: Page, color: string): Promise<void> {
  await expect(async() => {
    await expect(page.locator('.grid-cell').nth(0)).toHaveCSS('background-color', color);
    await page.reload();
  }).toPass({intervals: [2000, 3000, 5000], timeout: 120000});
}

async function createJobWithPipeline(page: Page, pipeline: string, inputFileName: string): Promise<void> {
  await utils.selectPipeline(page, pipeline);
  await page.locator('input[id="file-upload"]').setInputFiles(`./fixtures/${inputFileName}`);
  await page.locator('#create-button').click();
}


async function customDefaultPipeline(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
  await expect(page.locator('#pipelines-input')).toBeEmpty();
  await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user') && resp.status() === 200, {timeout: 30000}
  );

  await utils.typeInPipelineEditor(
    page,
    '- effect_annotator:\n' +
    '    gene_models: hg38/gene_models/GENCODE/48/basic/ALL\n' +
    '    genome: hg38/genomes/GRCh38.p13\n' +
    '    attributes:\n' +
    '    - worst_effect\n' +
    '    - gene_effects\n' +
    '    - effect_details\n' +
    '    - name: gene_list \n' +
    '      internal: true\n'
  );

  await saveResponse;

  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}